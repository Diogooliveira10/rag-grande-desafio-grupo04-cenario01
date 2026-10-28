"""Reconciliação offline entre snapshot PNCP e cadastro oficial consultado.

Não altera candidato, fontes, SQL ou índice ativo. Produz diagnóstico territorial
por unidade/ata e plano de janelas, nunca uma promoção ou prova de censo estadual.
O cadastro atual não comprova, sozinho, a localização histórica em 2025.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable

from src.collection_policy import PoliticaColeta, chave_unidade, uf_unidade
from src.direct_collection import janelas_contratos
from src.index import file_sha256
from src.recover_corpus_v7 import agora, destino_novo, gravar_json


def reconciliar(observadas: Path, cadastro: Path, destino: Path, *, catalogo_aquisicao: Path | None = None,
                estado_contratos: Path | None = None, politica: PoliticaColeta | None = None) -> dict[str, Any]:
    politica = politica or PoliticaColeta()
    arquivos = [observadas, cadastro, *([catalogo_aquisicao] if catalogo_aquisicao else []), *([estado_contratos] if estado_contratos else [])]
    for p in arquivos:
        if not p.is_file():
            raise FileNotFoundError(p)
    checkpoint_bytes = cadastro.read_bytes()
    checkpoint_hash = hashlib.sha256(checkpoint_bytes).hexdigest()
    dados = json.loads(checkpoint_bytes)
    local = json.loads(observadas.read_text(encoding="utf-8"))
    verificacoes = dados.get("verificacoes_orgaos") or {}
    oficiais, evidencias = _unidades_oficiais(verificacoes, cadastro)
    observacao = {(str(u["cnpj"]), str(u["codigoUnidadeAdministrativa"])): set(u.get("ufs_observadas") or []) for u in local["unidades"]}

    def situacao(key: tuple[str, str]) -> tuple[str, str]:
        return _situacao_da_unidade(key, verificacoes, oficiais, observacao, politica)

    destino = destino_novo(destino, arquivos)
    gravar_json(destino / "cadastro_snapshot.json", dados)
    resumo_unidades = _conferir_unidades(destino, observacao, situacao)
    resumo_atas = _conferir_atas(destino, catalogo_aquisicao, politica, observacao, situacao) if catalogo_aquisicao else Counter()
    escopos, janelas, tipos_janelas = _planejar_janelas(oficiais, situacao, politica, estado_contratos)
    gravar_json(destino / "plano_janelas_contratos.json", {"gerado_em": agora(), "politica": asdict(politica), "manifesto_de_planejamento_nao_checkpoint": True, "janelas": janelas, "contagens": dict(tipos_janelas), "promocao_autorizada": False})
    gravar_json(destino / "unidades_RJ_elegiveis.json", {"verificacao_completa": dados.get("verificacao_completa") is True,
        "cadastro_origem": str(cadastro), "verified_unit_scopes": escopos, "cobertura_institucional_comprovada": False})
    report = {"gerado_em": agora(), "cadastro": str(cadastro.resolve()), "checkpoint_gerado_em": dados.get("gerado_em"),
              "orgaos_alvo": dados.get("orgaos_alvo_observados"), "orgaos_processados": len(verificacoes),
              "orgaos_verificados": sum(v.get("status") == "verificado" for v in verificacoes.values()),
              "verificacao_sementes_completa": dados.get("verificacao_completa") is True,
              "unidades_oficiais_todas_UFs": len(oficiais), "unidades_RJ_elegiveis": len(escopos),
              "unidades_RJ_nao_observadas_no_snapshot": sum((u["cnpj"], u["codigoUnidadeAdministrativa"]) not in observacao for u in escopos),
              "unidades_RJ_sem_UF_no_snapshot": sum(not observacao.get((u["cnpj"], u["codigoUnidadeAdministrativa"])) for u in escopos),
              "checkpoint_sha256": checkpoint_hash,
              "fontes_locais": [{"path": str(p.resolve()), "sha256": file_sha256(p)} for p in arquivos if p != cadastro],
              "unidades_observadas_por_situacao": dict(resumo_unidades), "atas_por_situacao": dict(resumo_atas),
              "janelas_contratos": dict(tipos_janelas), "evidencias_cadastrais": evidencias,
              "cobertura_institucional_comprovada": False, "promocao_autorizada": False}
    gravar_json(destino / "relatorio.json", report)
    return report


def _unidades_oficiais(verificacoes: dict[str, Any], cadastro: Path) -> tuple[dict[tuple[str, str], set[str]], list[dict[str, Any]]]:
    """Unidades (cnpj, código) -> UFs do cadastro oficial dos órgãos verificados, e as evidências lidas."""
    # Snapshot do checkpoint atômico: não incorporar arquivos que apareçam
    # depois enquanto o coletor ainda estiver rodando no mesmo diretório.
    oficiais: dict[tuple[str, str], set[str]] = {}
    evidencias = []
    for cnpj, estado in sorted(verificacoes.items()):
        if estado.get("status") != "verificado":
            continue
        path = cadastro.parent / f"{cnpj}.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        esperado = f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/unidades"
        if d.get("origem") != esperado:
            raise ValueError(f"origem cadastral divergente: {cnpj}")
        payload = d.get("unidades")
        if not isinstance(payload, list):
            raise ValueError(f"cadastro não é lista: {cnpj}")
        evidencias.append({"path": str(path.resolve()), "sha256": file_sha256(path), "coletado_em": d.get("coletado_em")})
        for u in payload:
            codigo = str(u.get("codigoUnidade") or "")
            dono = (u.get("orgao") or {}).get("cnpj")
            if dono and str(dono) != cnpj:
                raise ValueError(f"unidade retornada por outro órgão: {cnpj}/{codigo}")
            if codigo:
                key = (cnpj, codigo)
                oficiais.setdefault(key, set())
                uf = uf_unidade({"unidadeOrgao": u})
                if uf:
                    oficiais[key].add(uf)
    return oficiais, evidencias


def _situacao_da_unidade(key: tuple[str, str], verificacoes: dict[str, Any], oficiais: dict[tuple[str, str], set[str]],
                         observacao: dict[tuple[str, str], set[str]], politica: PoliticaColeta) -> tuple[str, str]:
    """(situação, UF cadastral) da unidade, comparando o cadastro oficial com o snapshot."""
    if verificacoes.get(key[0], {}).get("status") != "verificado":
        return "orgao_nao_verificado", ""
    if key not in oficiais:
        return "unidade_ausente_no_cadastro_atual", ""
    ufs = oficiais[key]
    if not ufs:
        return "uf_cadastral_pendente", ""
    if len(ufs) != 1:
        return "conflito_cadastral", ""
    uf = next(iter(ufs))
    vistas = observacao.get(key, set())
    if vistas and vistas != {uf}:
        return "conflito_snapshot_cadastro", uf
    return ("confirmada_RJ" if uf == politica.uf else "confirmada_fora_RJ"), uf


def _conferir_unidades(destino: Path, observacao: dict[tuple[str, str], set[str]],
                       situacao: Callable[[tuple[str, str]], tuple[str, str]]) -> Counter:
    resumo_unidades = Counter()
    with (destino / "unidades_conferidas.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for key in sorted(observacao):
            status, uf = situacao(key)
            resumo_unidades[status] += 1
            out.write(json.dumps({"cnpj": key[0], "codigoUnidadeAdministrativa": key[1], "situacao": status, "uf_cadastral": uf, "ufs_snapshot": sorted(observacao[key])}, ensure_ascii=False) + "\n")
    return resumo_unidades


def _conferir_atas(destino: Path, catalogo_aquisicao: Path, politica: PoliticaColeta,
                   observacao: dict[tuple[str, str], set[str]],
                   situacao: Callable[[tuple[str, str]], tuple[str, str]]) -> Counter:
    """Território de cada ata do ano no catálogo de aquisição, em atas_territorio.jsonl."""
    resumo_atas = Counter()
    uri = catalogo_aquisicao.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db, (destino / "atas_territorio.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        query = "SELECT id,payload FROM registros WHERE json_extract(payload,'$.numeroControlePNCPAta') IS NOT NULL"
        for id_, payload in db.execute(query):
            ata = json.loads(payload)
            if str(ata.get("anoAta")) != str(politica.ano):
                continue
            key = chave_unidade(ata)
            status, uf = situacao(key)
            resumo_atas[status] += 1
            if not observacao.get(key) and status == "confirmada_RJ":
                resumo_atas["RJ_resolvida_sem_UF_no_snapshot"] += 1
            if not observacao.get(key) and status == "confirmada_fora_RJ":
                resumo_atas["fora_RJ_resolvida_sem_UF_no_snapshot"] += 1
            out.write(json.dumps({"ata": id_, "cnpj": key[0], "codigoUnidadeAdministrativa": key[1], "situacao": status, "uf_cadastral": uf, "evidencia_temporal": "cadastro_consultado_agora_nao_vigencia_historica"}, ensure_ascii=False) + "\n")
    return resumo_atas


def _planejar_janelas(oficiais: dict[tuple[str, str], set[str]], situacao: Callable[[tuple[str, str]], tuple[str, str]],
                      politica: PoliticaColeta, estado_contratos: Path | None) -> tuple[list[dict], list[dict], Counter]:
    """(unidades RJ elegíveis, janelas de contratos propostas, contagem por ação)."""
    # Conflitos cadastrais/de snapshot não viram janelas elegíveis automaticamente.
    escopos = [{"cnpj": cnpj, "codigoUnidadeAdministrativa": codigo, "ufSigla": politica.uf}
               for (cnpj, codigo), ufs in sorted(oficiais.items())
               if ufs == {politica.uf} and situacao((cnpj, codigo))[0] == "confirmada_RJ"]
    antigas = json.loads(estado_contratos.read_text(encoding="utf-8")).get("janelas", {}) if estado_contratos else {}
    tipos_janelas = Counter()
    janelas = []
    for janela in janelas_contratos([], inicio=politica.publicacao_inicio, fim=politica.publicacao_fim, unidades=escopos):
        cnpj = janela.params["cnpjOrgao"]
        antiga = antigas.get(f"contrato_assinado:{cnpj}", {})
        # Não dar baixa automática: completude do checkpoint legado não prova
        # integridade/paginação nem equivalência de escopo. Apenas priorizar auditoria.
        reaproveitavel = antiga.get("status") == "completa" and janela.params["dataInicial"] == "20250101" and janela.params["dataFinal"] == "20251231"
        acao = "auditar_reaproveitamento_snapshot_2025" if reaproveitavel else "aquisicao_faltante"
        tipos_janelas[acao] += 1
        janelas.append({**asdict(janela), "acao_proposta": acao, "execucao_autorizada_por_este_arquivo": False})
    return escopos, janelas, tipos_janelas


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observadas", type=Path, required=True)
    p.add_argument("--cadastro", type=Path, required=True)
    p.add_argument("--destino", type=Path, required=True)
    p.add_argument("--catalogo-aquisicao", type=Path)
    p.add_argument("--estado-contratos", type=Path)
    a = p.parse_args(argv)
    result = reconciliar(a.observadas, a.cadastro, a.destino, catalogo_aquisicao=a.catalogo_aquisicao, estado_contratos=a.estado_contratos)
    print(json.dumps({k:v for k,v in result.items() if k != "evidencias_cadastrais"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
