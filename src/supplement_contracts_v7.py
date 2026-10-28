"""Aquisição complementar RJ/assinatura 2025, retomável por página em staging.

Busca superset por CNPJ; só exporta registros elegíveis de janelas completas.
Páginas brutas (JSON canônico, hash derivado) ficam imutáveis para auditoria e
retomada sem refazer HTTP. Sem modelos, banco ativo ou promoção.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Callable
from uuid import uuid4
from urllib.parse import urlencode

from src.direct_build import RegistroDireto, chunk_de_registro
from src.collection_policy import PoliticaColeta, tipo_instrumento
from src.direct_collection import ENDPOINT_CONTRATOS, FalhaDeColeta, _payload_de_resposta, _e_fim_de_paginacao
from src.supplement_corpus_v7 import ConsultasOrcadas
from src.fetch_corpus import load_manifest
from src.index import file_sha256
from src.recover_corpus_v7 import ROOT, agora, destino_novo, gravar_json
from src.pncp_client import PncpClient

CACHE_VERSION = 1
MAX_PAGINAS = 10000


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _url(janela: dict, pagina: int) -> str:
    return janela["endpoint"] + "?" + urlencode(sorted({**janela["params"], "pagina": pagina}.items()))


def _pasta(destino: Path, janela: dict) -> Path:
    return destino / "paginas" / _digest({"endpoint": janela["endpoint"], "params": janela["params"]})[:24]


def _validar_janelas(janelas: list[dict], politica: PoliticaColeta) -> None:
    ids = set(); consultas = set()
    for j in janelas:
        params = j["params"]
        if j["id"] in ids or j["endpoint"] != ENDPOINT_CONTRATOS:
            raise ValueError("janela duplicada ou endpoint divergente")
        ids.add(j["id"])
        query_key = _digest({"endpoint": j["endpoint"], "params": params})
        if query_key in consultas:
            raise ValueError("mesma consulta repetida sob identificadores diferentes")
        consultas.add(query_key)
        if not re.fullmatch(r"\d{14}", str(params.get("cnpjOrgao") or "")) or params.get("tamanhoPagina") != 500:
            raise ValueError("CNPJ/tamanho de página inválidos")
        if set(params) != {"dataInicial", "dataFinal", "cnpjOrgao", "tamanhoPagina"}:
            raise ValueError("parâmetros fora do plano de aquisição por CNPJ")
        inicio, fim = [datetime.strptime(params[k], "%Y%m%d").date() for k in ["dataInicial", "dataFinal"]]
        if not date.fromisoformat(politica.publicacao_inicio) <= inicio <= fim <= date.fromisoformat(politica.publicacao_fim) or (fim-inicio).days + 1 > 365:
            raise ValueError("período de publicação fora da política/limite da API")


def _validar_envelope(dados: dict, janela: dict, pagina: int) -> None:
    if dados.get("url") != _url(janela, pagina) or dados.get("payload_sha256") != _digest(dados.get("payload")):
        raise ValueError("cache de página com origem/hash divergente; preservar para auditoria")
    payload = dados["payload"]
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) or any(not isinstance(r, dict) for r in payload["data"]):
        raise ValueError("página fora do contrato JSON esperado")


def _selecionar(r: dict, j: dict, politica: PoliticaColeta) -> tuple[bool, str]:
    if not re.fullmatch(r"\d{14}-2-\d+/\d{4}", str(r.get("numeroControlePNCP") or "")):
        return False, "identidade_nao_contrato_empenho"
    papeis = {(r.get(k) or {}).get("cnpj") for k in ["orgaoEntidade", "orgaoSubRogado"]}
    if j["params"]["cnpjOrgao"] not in papeis:
        return False, "orgao_divergente_da_consulta"
    try:
        assinatura = date.fromisoformat(str(r.get("dataAssinatura") or "")[:10])
        publicada = date.fromisoformat(str(r.get("dataPublicacaoPncp") or "")[:10])
    except ValueError:
        return False, "data_ausente_ou_invalida"
    a, b = [datetime.strptime(j["params"][k], "%Y%m%d").date() for k in ["dataInicial", "dataFinal"]]
    if not a <= publicada <= b:
        return False, "publicacao_fora_da_janela_consultada"
    return politica.decidir(r)


def executar(plano: Path, destino: Path, *, rede: ConsultasOrcadas, manifest: Path | None = None, retomar: bool = False, max_segundos: float | None = None) -> dict:
    inicio_execucao = time.monotonic()
    if max_segundos is not None and max_segundos <= 0:
        raise ValueError("limite de tempo inválido")
    prazo = inicio_execucao + max_segundos if max_segundos is not None else float("inf")
    politica, janelas, base, fingerprint = _carregar_plano(plano, manifest)
    destino = _preparar_destino(destino.resolve(), plano, politica, fingerprint, retomar)
    # Trava de execução: interrupção abrupta requer revisar/remover só a trava
    # do próprio staging, nunca apagar páginas ou presumir que outro processo saiu.
    lock = destino / ".coleta.lock"
    estado_path = destino / "estado.json"
    estado = json.loads(estado_path.read_text(encoding="utf-8")) if estado_path.exists() else {"janelas": {}}
    if estado_path.exists() and (estado.get("fingerprint") != fingerprint or not set(estado["janelas"]) <= {j["id"] for j in janelas}):
        raise ValueError("estado incompatível com o plano; preservar para auditoria")
    with lock.open("x", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "iniciado_em": agora()}, f)
    estado.update({"fingerprint": fingerprint, "em_execucao": True, "politica": asdict(politica)})
    cache_lido = 0
    run_id = "coleta-" + uuid4().hex
    estado["run_id_ultima_execucao"] = run_id

    def pode_http():
        proximo_inicio = max(time.monotonic(), rede.proxima, (rede.ultima + rede.intervalo) if rede.ultima is not None else 0)
        return rede.consultas < rede.limite and proximo_inicio < prazo

    def salvar():
        estado["atualizado_em"] = agora()
        estado["consultas_http_esta_execucao"] = rede.consultas
        gravar_json(estado_path, estado)

    try:
        salvar()
        # Concluir páginas parciais antes de abrir novas janelas. Falhas HTTP
        # antigas ficam por último: não podem consumir todo lote repetidamente.
        ordem = {"parcial": 0, "completa": 1, "falhou": 3}
        ordenadas = sorted(enumerate(janelas), key=lambda v: (ordem.get(estado["janelas"].get(v[1]["id"], {}).get("status"), 2), v[0]))
        for _, j in ordenadas:
            cache_lido += _adquirir_janela(j, destino, rede, politica, estado, run_id, pode_http, salvar)
        exportados = _exportar_janelas_completas(destino, janelas, estado, politica, base)
        report = _relatorio_da_execucao(destino, janelas, estado, politica, rede, inicio_execucao=inicio_execucao,
                                        max_segundos=max_segundos, run_id=run_id, cache_lido=cache_lido,
                                        exportados=exportados)
        gravar_json(destino / "relatorio.json", report)
        estado.pop("ultimo_erro", None)
        return report
    except Exception as exc:
        estado["ultimo_erro"] = f"{type(exc).__name__}: {exc}"[:500]
        gravar_json(destino / "falha_execucao.json", {"gerado_em": agora(), "erro": estado["ultimo_erro"], "exportacao_anterior_preservada": True, "fingerprint": fingerprint, "promocao_autorizada": False})
        raise
    finally:
        estado["em_execucao"] = False
        salvar()
        lock.unlink()


def _carregar_plano(plano: Path, manifest: Path | None) -> tuple[PoliticaColeta, list[dict], dict, str]:
    """(política, janelas, base, fingerprint) do plano versionado, conferido contra o manifesto."""
    plan = json.loads(plano.read_text(encoding="utf-8"))
    politica = PoliticaColeta(**plan["politica"])
    if politica.ano != 2025 or politica.uf != "RJ":
        raise ValueError("esta aquisição só aceita assinatura 2025 e UF RJ")
    janelas = plan["janelas_a_adquirir"]
    _validar_janelas(janelas, politica)
    if manifest:
        fontes = {str(Path(p["path"]).resolve()): p["sha256"] for p in plan["fontes"]}
        if fontes.get(str(manifest.resolve())) != file_sha256(manifest):
            raise ValueError("manifesto difere do plano versionado")
        base = next(b for b in load_manifest(manifest)["bases"] if b["base_id"] == "contratos_estruturado")
    else:
        base = {"base_id": "contratos_estruturado", "natureza": "estruturada", "fonte": "PNCP", "chunking_sugerido": "1 registro = 1 chunk", "sensitivity_default": "publico"}
    fingerprint = _digest({"plano_sha256": file_sha256(plano), "base": base, "cache_version": CACHE_VERSION})
    return politica, janelas, base, fingerprint


def _preparar_destino(destino: Path, plano: Path, politica: PoliticaColeta, fingerprint: str, retomar: bool) -> Path:
    """Destino novo, ou a retomada de um destino compatível com o mesmo plano."""
    if retomar:
        protegidos = [ROOT / p for p in ["index", "data/normalized", "data/structured", "data/v7_direto", "data/v7_direto_atas"]]
        if any(destino == p.resolve() or destino.is_relative_to(p.resolve()) or p.resolve().is_relative_to(destino) for p in protegidos):
            raise ValueError("destino protegido")
        meta = json.loads((destino / "execucao.json").read_text(encoding="utf-8"))
        if meta.get("fingerprint") != fingerprint:
            raise ValueError("retomada incompatível com plano/política/base")
    else:
        destino = destino_novo(destino, [plano])
        gravar_json(destino / "execucao.json", {"gerado_em": agora(), "fingerprint": fingerprint, "plano": str(plano.resolve()), "politica": asdict(politica), "promocao_autorizada": False})
    return destino


def _adquirir_janela(j: dict, destino: Path, rede: ConsultasOrcadas, politica: PoliticaColeta, estado: dict,
                     run_id: str, pode_http: Callable[[], bool], salvar: Callable[[], None]) -> int:
    """Lê do cache ou baixa as páginas da janela até o fim declarado; devolve quantas vieram do cache."""
    cache_lido = 0
    folder = _pasta(destino, j)
    if not (folder / "000001.json").exists() and not pode_http():
        return cache_lido
    folder.mkdir(parents=True, exist_ok=True)
    vistos = set(); lidos = incluidos = 0; descartes = Counter(); esperadas = None; esperado_total = None
    status = "parcial"; pagina = 1; ultimo_erro = None
    while pagina <= MAX_PAGINAS:
        path = folder / f"{pagina:06d}.json"
        if path.exists():
            envelope = json.loads(path.read_text(encoding="utf-8"))
            cache_lido += 1
        else:
            if not pode_http():
                break
            envelope, ultimo_erro = _baixar_pagina(j, pagina, path, destino, rede, run_id, esperadas)
            if envelope is None:
                status = "falhou"
                break
        _validar_envelope(envelope, j, pagina)
        payload = envelope["payload"]
        esperadas, esperado_total = _conferir_totais(payload, esperadas, esperado_total)
        registros = payload["data"]
        for r in registros:
            id_ = r.get("numeroControlePNCP")
            if id_ in vistos:
                raise ValueError("identidade repetida entre páginas; revisar estabilidade da janela")
            vistos.add(id_)
            lidos += 1
            aceito, motivo = _selecionar(r, j, politica)
            if aceito: incluidos += 1
            else: descartes[motivo] += 1
        fim = pagina >= esperadas if esperadas is not None else len(registros) < j["params"]["tamanhoPagina"]
        if not registros and not fim:
            raise ValueError("página vazia antes do fim declarado")
        if esperadas == 0 and registros:
            raise ValueError("paginação declara zero páginas com registros")
        if fim:
            if esperado_total is not None and esperado_total != lidos:
                raise ValueError("soma das páginas difere do total de registros declarado")
            status = "completa"
        estado["janelas"][j["id"]] = {"status": status, "paginas_em_cache": pagina, "lidos": lidos, "selecionados_RJ_assinatura2025": incluidos, "descartes": dict(descartes), "total_paginas_declarado": esperadas, "total_registros_declarado": esperado_total, "folder": str(folder.relative_to(destino))}
        salvar()
        if status == "completa": break
        pagina += 1
    else:
        status = "falhou"; ultimo_erro = {"erro": "teto de páginas atingido; não certificar completude"}
    salvo = estado["janelas"].setdefault(j["id"], {"folder": str(folder.relative_to(destino))})
    salvo.update({"status": status, "lidos": lidos, "selecionados_RJ_assinatura2025": incluidos, "descartes": dict(descartes)})
    if ultimo_erro: salvo["erro"] = ultimo_erro
    else: salvo.pop("erro", None)
    salvar()
    return cache_lido


def _baixar_pagina(j: dict, pagina: int, path: Path, destino: Path, rede: ConsultasOrcadas, run_id: str,
                   esperadas: int | None) -> tuple[dict | None, dict | None]:
    """(envelope gravado em ``path``, None) ou, se a página falhou, (None, erro)."""
    inicio_http = time.perf_counter()
    resposta = rede.get(_url(j, pagina))
    evento = {"ts": agora(), "run_id": run_id, "query_id": None, "stage": "coleta", "status": "ok" if resposta.get("http_status") in (200, 204) else "erro", "latencia_ms": round((time.perf_counter()-inicio_http)*1000), "input": _url(j, pagina), "output": f"HTTP {resposta.get('http_status')}", "tokens": {"prompt": 0, "completion": 0, "total": 0}, "modelo": None, "extra": {"janela_id": j["id"], "pagina": pagina, "bytes_lidos": resposta.get("bytes_lidos"), "latencia_inclui_rate_limit": True, "timeout_http_segundos": rede.timeout}}
    with (destino / "eventos_http.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(evento, ensure_ascii=False) + "\n")
    try:
        payload = _payload_de_resposta(resposta, _url(j, pagina))
    except FalhaDeColeta as exc:
        if pagina > 1 and (esperadas is None or pagina > esperadas) and _e_fim_de_paginacao(exc):
            payload = {"data": []}
        else:
            return None, {"http_status": resposta.get("http_status"), "erro": str(exc)[:300]}
    envelope = {"url": _url(j, pagina), "coletado_em": agora(), "payload": payload, "payload_sha256": _digest(payload), "hash_derivado": True, "http_status": resposta.get("http_status")}
    _validar_envelope(envelope, j, pagina)
    gravar_json(path, envelope)
    return envelope, None


def _conferir_totais(payload: dict, esperadas: int | None, esperado_total: int | None) -> tuple[int | None, int | None]:
    """Totais declarados de páginas e registros: estáveis entre as páginas e não negativos."""
    for campo, anterior in [("totalPaginas", esperadas), ("totalRegistros", esperado_total)]:
        valor = payload.get(campo)
        if valor is not None and (int(valor) < 0 or (anterior is not None and int(valor) != anterior)):
            raise ValueError(f"{campo} instável/inválido; páginas preservadas para auditoria")
    if payload.get("totalPaginas") is not None: esperadas = int(payload["totalPaginas"])
    if payload.get("totalRegistros") is not None: esperado_total = int(payload["totalRegistros"])
    return esperadas, esperado_total


def _exportar_janelas_completas(destino: Path, janelas: list[dict], estado: dict, politica: PoliticaColeta,
                                base: dict) -> int:
    """documents.jsonl só com as janelas completas; devolve quantos chunks foram exportados."""
    # O artefato principal só recebe páginas de janelas completas. Dados
    # de janela parcial ficam no cache, sem perder chamadas já realizadas.
    provisorio = destino / "documents.jsonl.provisorio"
    exportados = 0
    with provisorio.open("w", encoding="utf-8", newline="\n") as out:
        for j in janelas:
            e = estado["janelas"].get(j["id"], {})
            if e.get("status") != "completa": continue
            for n in range(1, e["paginas_em_cache"] + 1):
                envelope = json.loads((_pasta(destino, j) / f"{n:06d}.json").read_text(encoding="utf-8"))
                _validar_envelope(envelope, j, n)
                for r in envelope["payload"]["data"]:
                    if not _selecionar(r, j, politica)[0]: continue
                    chunks, _ = chunk_de_registro(RegistroDireto(payload=r, origem=envelope["url"], coletado_em=envelope["coletado_em"]), base)
                    for c in chunks:
                        c.setdefault("extras", {}).update({"tipo_instrumento": tipo_instrumento(r), "recorte_policy": asdict(politica)})
                        out.write(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n")
                        exportados += 1
    provisorio.replace(destino / "documents.jsonl")
    return exportados


def _relatorio_da_execucao(destino: Path, janelas: list[dict], estado: dict, politica: PoliticaColeta,
                           rede: ConsultasOrcadas, *, inicio_execucao: float, max_segundos: float | None,
                           run_id: str, cache_lido: int, exportados: int) -> dict:
    totais = Counter(e.get("status") for e in estado["janelas"].values())
    pendencias_qualidade = sum(sum(n for motivo,n in e.get("descartes", {}).items() if motivo in {"identidade_nao_contrato_empenho", "orgao_divergente_da_consulta", "data_ausente_ou_invalida", "publicacao_fora_da_janela_consultada", "territorio_pendente"}) for e in estado["janelas"].values())
    return {"gerado_em": agora(), "duracao_segundos": round(time.monotonic()-inicio_execucao, 2), "limite_tempo_http_segundos": max_segundos, "run_id": run_id, "politica": asdict(politica), "janelas_plano": len(janelas), "janelas_por_status": dict(totais), "janelas_nao_iniciadas": len(janelas)-len(estado["janelas"]),
        "consultas_http_esta_execucao": rede.consultas, "paginas_lidas_do_cache": cache_lido,
        "registros_lidos_acumulados": sum(e.get("lidos",0) for e in estado["janelas"].values()),
        "registros_elegiveis_em_cache": sum(e.get("selecionados_RJ_assinatura2025",0) for e in estado["janelas"].values()),
        "ocorrencias_exportadas_janelas_completas": exportados, "source_sha256": file_sha256(destino / "documents.jsonl"),
        "aquisicao_plano_completa": totais["completa"] == len(janelas), "registros_com_qualidade_pendente": pendencias_qualidade, "cobertura_institucional_comprovada": False, "promocao_autorizada": False}


def congelar_exportacao(origem: Path, destino: Path) -> dict:
    """Congela só o export validado, antes de retomar aquisição no diretório."""
    if (origem / ".coleta.lock").exists():
        raise ValueError("coleta ativa/trava presente: não congelar arquivo em escrita")
    documentos = origem / "documents.jsonl"
    relatorio = origem / "relatorio.json"
    sha_docs, sha_relatorio = file_sha256(documentos), file_sha256(relatorio)
    r = json.loads(relatorio.read_text(encoding="utf-8"))
    if r.get("source_sha256") != sha_docs or r.get("politica", {}).get("ano") != 2025 or r.get("politica", {}).get("uf") != "RJ":
        raise ValueError("export/política não confere com o relatório")
    destino = destino_novo(destino, [origem])
    for p in [documentos, relatorio]:
        shutil.copy2(p, destino / p.name)
    if file_sha256(documentos) != sha_docs or file_sha256(relatorio) != sha_relatorio or file_sha256(destino / documentos.name) != sha_docs or file_sha256(destino / relatorio.name) != sha_relatorio:
        raise ValueError("fonte mudou durante o congelamento")
    meta = {"gerado_em": agora(), "origem": str(origem.resolve()), "documentos_sha256": sha_docs, "relatorio_sha256": sha_relatorio,
            "ocorrencias_exportadas": r["ocorrencias_exportadas_janelas_completas"], "aquisicao_plano_completa": r["aquisicao_plano_completa"], "promocao_autorizada": False}
    gravar_json(destino / "snapshot.json", meta)
    return meta


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plano", type=Path, required=True); p.add_argument("--destino", type=Path, required=True)
    p.add_argument("--manifest", type=Path, default=Path("corpus_manifest.yaml"))
    p.add_argument("--max-consultas", type=int, default=20); p.add_argument("--intervalo", type=float, default=5)
    p.add_argument("--retomar", action="store_true")
    p.add_argument("--max-segundos", type=float, help="não inicia novas consultas após este tempo; a requisição em curso pode terminar depois")
    p.add_argument("--timeout-http", type=float, default=60)
    a = p.parse_args(argv)
    r = executar(a.plano, a.destino, manifest=a.manifest, retomar=a.retomar, max_segundos=a.max_segundos, rede=ConsultasOrcadas(a.max_consultas, a.intervalo, PncpClient(read_limit=8*1024*1024), timeout=a.timeout_http))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
