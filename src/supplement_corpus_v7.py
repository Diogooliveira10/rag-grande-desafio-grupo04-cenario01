"""Complementação opt-in e orçada; sem embeddings, SQL ativo ou promoção.

Catálogo oficial, publicações omitidas, unidades oficiais e compras vinculadas.
Respostas e falhas ficam em staging. --max-consultas é um teto por execução.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from src.direct_build import RegistroDireto, chunk_de_registro
from src.collection_policy import PoliticaColeta, uf_unidade
from src.direct_collection import ENDPOINT_PUBLICACOES, Janela, _payload_de_resposta, coletar_direto
from src.fetch_corpus import load_manifest
from src.pncp_client import PncpClient
from src.recover_corpus_v7 import agora, destino_novo, gravar_json, url_compra


class ConsultasOrcadas:
    def __init__(self, limite: int, intervalo: float = 5.0, client: Any = None, timeout: float = 30.0):
        if limite < 1 or intervalo < 0 or timeout <= 0:
            raise ValueError("orçamento/intervalo/timeout inválido")
        self.limite, self.intervalo, self.timeout = limite, intervalo, timeout
        self.client = client or PncpClient()
        self.consultas = 0
        self.ultima: float | None = None
        self.proxima = 0.0

    def get(self, url: str) -> dict[str, Any]:
        if self.consultas >= self.limite:
            raise RuntimeError("orçamento de consultas atingido; retomar em outra execução")
        espera = max(self.proxima, (self.ultima + self.intervalo) if self.ultima is not None else 0.0)
        time.sleep(max(0.0, espera - time.monotonic()))
        self.consultas += 1
        self.ultima = time.monotonic()
        resposta = self.client.request_url(url, "json", attempts=1, timeout=self.timeout)
        if resposta.get("http_status") in (429, 503):
            # Não insistir em outro órgão imediatamente após limitação do host.
            depois = resposta.get("retry_after")
            try:
                segundos = float(depois) if depois is not None else 0.0
            except (TypeError, ValueError):
                try:
                    segundos = parsedate_to_datetime(str(depois)).timestamp() - datetime.now(timezone.utc).timestamp()
                except (TypeError, ValueError, OverflowError):
                    segundos = 0.0
            self.proxima = time.monotonic() + max(30.0, self.intervalo, segundos)
        return resposta


def coletar_catalogo(destino: Path, rede: ConsultasOrcadas) -> dict[str, Any]:
    url = "https://pncp.gov.br/api/pncp/v1/modalidades"
    r = rede.get(url)
    valores = _payload_de_resposta(r, url)
    if not isinstance(valores, list) or not valores or any(not isinstance(v, dict) or not v.get("id") for v in valores):
        raise ValueError("catálogo oficial inválido")
    result = {"origem": url, "coletado_em": agora(), "modalidades": valores,
              "hash_derivado": True, "sha256": hashlib.sha256(json.dumps(valores, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    gravar_json(destino, result)
    return result


def publicacoes_omitidas(destino: Path, modalidades: list[int], *, manifest: Path, rede: ConsultasOrcadas, ano: int = 2025) -> dict[str, Any]:
    destino = destino_novo(destino)
    catalogo = coletar_catalogo(destino / "catalogo_modalidades.json", rede)
    ids = {int(m["id"]) for m in catalogo["modalidades"]}
    if not modalidades or not set(modalidades) <= ids:
        raise ValueError("modalidade solicitada fora do catálogo oficial")
    base = next(b for b in load_manifest(manifest)["bases"] if b["base_id"] == "contratos_estruturado")
    janelas = [Janela(f"publicacao:{ano}:modalidade={m}", ENDPOINT_PUBLICACOES,
               {"dataInicial": f"{ano}0101", "dataFinal": f"{ano}1231", "uf": "RJ", "codigoModalidadeContratacao": m, "tamanhoPagina": 50}) for m in sorted(set(modalidades))]
    result = coletar_direto(janelas, base, destino / "aquisicao", request_fn=lambda u: _payload_de_resposta(rede.get(u), u), taxa_seg=0,
              politica=PoliticaColeta(ano=ano), manifest_hash=hashlib.sha256(manifest.read_bytes()).hexdigest())
    result.update({"consultas_http": rede.consultas, "modalidades": modalidades, "piloto": True,
                   "catalogo_ids_nao_consultados": sorted(ids - set(modalidades)), "promocao_autorizada": False})
    gravar_json(destino / "relatorio.json", result)
    return result


def compras_pendentes(fila: Path, destino: Path, *, rede: ConsultasOrcadas, cache: Path | None = None) -> dict[str, Any]:
    destino = destino_novo(destino, [fila])
    base = {"base_id": "referencias_auxiliares", "natureza": "estruturada", "fonte": "PNCP - compra vinculada", "sensitivity_default": "publico", "chunking_sugerido": "1 registro = 1 chunk"}
    entradas = [json.loads(l) for l in fila.read_text(encoding="utf-8").splitlines() if l.strip()]
    unicas = {v["compra"]: v for v in entradas}
    resolvidas = falhas = 0
    eventos = []
    with (destino / "documents.jsonl").open("w", encoding="utf-8", newline="\n") as out:
        for compra in sorted(unicas):
            url = url_compra(compra)
            cache_file = cache / (compra.replace("/", "_") + ".json") if cache else None
            resposta_file = destino / (compra.replace("/", "_") + ".json")
            if cache_file and cache_file.exists():
                registro = json.loads(cache_file.read_text(encoding="utf-8"))
            else:
                if rede.consultas >= rede.limite:
                    continue
                r = rede.get(url)
                try:
                    payload = _payload_de_resposta(r, url)
                    if not isinstance(payload, dict) or payload.get("numeroControlePNCP") != compra:
                        raise ValueError("resposta não corresponde à compra solicitada")
                    if not payload.get("modalidadeId") or not payload.get("modalidadeNome"):
                        raise ValueError("compra sem modalidade estruturada")
                    registro = {"payload": payload, "origem": url, "coletado_em": agora()}
                except (ValueError, RuntimeError) as exc:
                    falhas += 1
                    eventos.append({"compra": compra, "status": r.get("status"), "http_status": r.get("http_status"), "erro": str(exc)[:300]})
                    continue
            if registro.get("payload", {}).get("numeroControlePNCP") != compra or not registro.get("payload", {}).get("modalidadeNome"):
                raise ValueError("cache inválido/não correspondente")
            gravar_json(resposta_file, registro)
            chunks, _ = chunk_de_registro(RegistroDireto.de_dict(registro), base)
            for chunk in chunks:
                chunk.setdefault("extras", {})["papel"] = "referencia_auxiliar"
                out.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
            resolvidas += 1
    result = {"gerado_em": agora(), "fila_unica": len(unicas), "compras_resolvidas": resolvidas, "falhas": falhas, "nao_processadas": len(unicas) - resolvidas - falhas, "eventos": eventos, "consultas_http": rede.consultas, "promocao_autorizada": False}
    gravar_json(destino / "relatorio.json", result)
    return result


def unidades_oficiais(observadas: Path, destino: Path, *, rede: ConsultasOrcadas, cache: Path | None = None) -> dict[str, Any]:
    destino = destino_novo(destino, [observadas])
    d = json.loads(observadas.read_text(encoding="utf-8"))
    cnpjs = sorted(set(d.get("orgaos") or [v["cnpj"] for v in d["unidades"]]))
    if not cnpjs:
        raise ValueError("cadastro-semente vazio: não certifica cobertura")
    verificacoes, unidades = {}, []

    def checkpoint(final: bool = False) -> dict[str, Any]:
        completo = len(verificacoes) == len(cnpjs) and all(v["status"] == "verificado" for v in verificacoes.values())
        result = {"gerado_em": agora(), "orgaos_alvo_observados": len(cnpjs), "verificacoes_orgaos": verificacoes, "verified_unit_scopes": unidades, "verificacao_completa": completo,
                  "execucao_finalizada": final, "orgaos_nao_processados": len(cnpjs) - len(verificacoes), "cobertura_institucional_comprovada": False, "consultas_http": rede.consultas, "promocao_autorizada": False}
        gravar_json(destino / ("cadastro.json" if final else "cadastro_parcial.json"), result)
        return result

    for cnpj in cnpjs:
        url = f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/unidades"
        cache_file = cache / f"{cnpj}.json" if cache else None
        try:
            salvo = json.loads(cache_file.read_text(encoding="utf-8")) if cache_file and cache_file.exists() else None
            if not salvo and rede.consultas >= rede.limite:
                continue
            r = None if salvo else rede.get(url)
            if salvo and salvo.get("origem") != url:
                raise ValueError("cache de unidades de outro órgão")
            payload = salvo["unidades"] if salvo else _payload_de_resposta(r, url)
            # Não aceitar envelope desconhecido como lista vazia/completa.
            if not isinstance(payload, list) or any(not isinstance(u, dict) for u in payload):
                raise ValueError("listagem de unidades não é lista de objetos; verificar paginação/contrato")
            registros = []
            for u in payload:
                codigo = u.get("codigoUnidade")
                if codigo and uf_unidade({"unidadeOrgao": u}) == "RJ":
                    registros.append({"cnpj": cnpj, "codigoUnidadeAdministrativa": str(codigo), "ufSigla": "RJ", "origem": url})
            unidades.extend(registros)
            verificado_em = salvo.get("coletado_em") if salvo else agora()
            verificacoes[cnpj] = {"status": "verificado", "unidades_rj": len(registros), "unidades_total": len(payload),
                                 "unidades_sem_uf": sum(not uf_unidade({"unidadeOrgao": u}) for u in payload), "origem": url,
                                 "coletado_em": verificado_em, "cache": bool(salvo)}
            gravar_json(destino / f"{cnpj}.json", {"origem": url, "coletado_em": verificado_em, "reutilizado_em": agora() if salvo else None, "unidades": payload})
        except (ValueError, RuntimeError) as exc:
            verificacoes[cnpj] = {"status": "falhou", "erro": str(exc)[:300], "origem": url}
        checkpoint()
    return checkpoint(final=True)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("acao", choices=["publicacoes", "compras", "unidades"])
    p.add_argument("--destino", type=Path, required=True)
    p.add_argument("--max-consultas", type=int, default=10)
    p.add_argument("--intervalo", type=float, default=5.0)
    p.add_argument("--manifest", type=Path, default=Path("corpus_manifest.yaml"))
    p.add_argument("--modalidades", type=int, nargs="+", default=[12, 13, 14])
    p.add_argument("--fila", type=Path)
    p.add_argument("--cache", type=Path)
    p.add_argument("--observadas", type=Path)
    a = p.parse_args(argv)
    rede = ConsultasOrcadas(a.max_consultas, a.intervalo)
    if a.acao == "publicacoes":
        r = publicacoes_omitidas(a.destino, a.modalidades, manifest=a.manifest, rede=rede)
    elif a.acao == "compras":
        if not a.fila: p.error("compras requer --fila")
        r = compras_pendentes(a.fila, a.destino, rede=rede, cache=a.cache)
    else:
        if not a.observadas: p.error("unidades requer --observadas")
        r = unidades_oficiais(a.observadas, a.destino, rede=rede, cache=a.cache)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
