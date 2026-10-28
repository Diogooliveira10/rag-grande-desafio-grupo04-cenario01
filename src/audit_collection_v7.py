"""Audita aquisição legada e planeja complementação RJ/2025 sem rede.

Checkpoint legado não é prova isolada de completude. Conferir hashes, parâmetros,
identidades, contagens e duplicação entre órgão principal/sub-rogado. Planejamento
por CNPJ busca um superset; UF/assinatura continuam sendo filtros por registro.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.collection_policy import PoliticaColeta, periodos, uf_unidade
from src.direct_collection import ENDPOINT_CONTRATOS
from src.index import file_sha256
from src.fetch_corpus import load_manifest
from src.recover_corpus_v7 import agora, destino_novo, gravar_json


def auditar(documents: Path, estado: Path, destino: Path, *, baseline: Path | None = None) -> dict:
    fontes = [documents, estado]
    hashes = {str(p.resolve()): file_sha256(p) for p in fontes}
    if baseline:
        b = json.loads((baseline / "baseline.json").read_text(encoding="utf-8"))
        esperados = {str((Path(b["root"]) / v["path"]).resolve()): v["sha256"] for v in b["adquiridos"]}
        if any(esperados.get(k) != v for k, v in hashes.items()):
            raise ValueError("aquisição divergiu da baseline; não certificar reaproveitamento")
    estados = json.loads(estado.read_text(encoding="utf-8"))["janelas"]
    janelas = {k.split(":")[1]: v for k, v in estados.items() if k.startswith("contrato_assinado:")}
    if not janelas:
        raise ValueError("estado sem janelas de contratos")
    contagens = Counter(); paginas = defaultdict(Counter); alternativas = defaultdict(set)
    erros = defaultdict(list); ids = {}; fontes_ids = defaultdict(set); perfil_rj = Counter()
    timestamps = []
    with documents.open(encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            if not linha.strip():
                continue
            c = json.loads(linha)
            r = json.loads(c["text"])
            id_ = str(r.get("numeroControlePNCP") or "")
            if "-2-" not in id_:
                continue
            origem = urlparse(c.get("origem") or c.get("source_file") or "")
            q = parse_qs(origem.query)
            cnpj = q.get("cnpjOrgao", [""])[0]
            if cnpj not in janelas:
                raise ValueError(f"origem sem janela declarada: linha {n}")
            def erro(motivo):
                if len(erros[cnpj]) < 20:
                    erros[cnpj].append({"linha": n, "id": id_, "motivo": motivo})
            if origem.scheme != "https" or origem.netloc != "pncp.gov.br" or origem.path != "/api/consulta/v1/contratos":
                erro("endpoint divergente")
            if q.get("dataInicial") != ["20250101"] or q.get("dataFinal") != ["20251231"] or q.get("tamanhoPagina") != ["500"]:
                erro("parâmetros fora da aquisição 2025 esperada")
            page = int(q.get("pagina", ["0"])[0])
            if page < 1:
                erro("página inválida")
            digest = hashlib.sha256(c["text"].encode()).hexdigest()
            if any(c.get(k) != digest for k in ["chunk_content_sha256", "document_content_sha256", "sha256"]):
                erro("hash do texto divergente/ausente")
            if c.get("chunking_strategy") != "registro_inteiro":
                erro("registro não está inteiro")
            papeis = {str((r.get(k) or {}).get("cnpj") or "") for k in ["orgaoEntidade", "orgaoSubRogado"]} - {""}
            if cnpj not in papeis:
                erro("CNPJ da consulta não é principal nem sub-rogado")
            if str(r.get("dataPublicacaoPncp") or "")[:4] != "2025":
                erro("publicação fora de 2025")
            if id_ in ids:
                erro("identidade repetida no snapshot de contratos")
            ids[id_] = digest
            contagens[cnpj] += 1; paginas[cnpj][page] += 1
            fontes_ids[cnpj].add(id_)
            if uf_unidade(r) == "RJ" and str(r.get("dataAssinatura") or "").startswith("2025"):
                perfil_rj[cnpj] += 1
            if c.get("coletado_em"):
                timestamps.append(c["coletado_em"])
            for outro in papeis - {cnpj}:
                if outro in janelas:
                    alternativas[outro].add(id_)
    resultados = []
    for cnpj, e in sorted(janelas.items()):
        esperado = int(e.get("registros") or 0); retidos = contagens[cnpj]
        deficit = esperado - retidos
        duplicados = alternativas[cnpj] - fontes_ids[cnpj]
        estrutura = e.get("status") == "completa" and e.get("paginas") == esperado // 500 + 1
        if any(p < 1 or p > e.get("paginas", 0) or n > 500 for p, n in paginas[cnpj].items()):
            estrutura = False
        # Nas janelas sem perda por deduplicação, todas as páginas com registros
        # devem estar presentes e ter o tamanho esperado. Terminal 204 é só estado.
        if deficit == 0 and esperado:
            esperadas = {p: min(500, esperado - (p - 1) * 500) for p in range(1, (esperado + 499) // 500 + 1)}
            estrutura = estrutura and dict(paginas[cnpj]) == esperadas
        if not estrutura or erros[cnpj] or deficit < 0:
            status = "revalidacao_necessaria"
        elif deficit == 0:
            status = "reaproveitavel" if esperado else "vazia_declarada_pelo_checkpoint"
        elif deficit == len(duplicados):
            status = "reaproveitavel_com_dedupe_crossrole"
        else:
            status = "revalidacao_necessaria"
        resultados.append({"cnpj": cnpj, "status": status, "registros_estado": esperado, "registros_retidos_por_origem": retidos,
            "paginas_estado": e.get("paginas"), "paginas_observadas": dict(sorted(paginas[cnpj].items())),
            "diferenca_multiplicidade": deficit, "ids_cobertos_por_outro_papel": sorted(duplicados) if deficit else [],
            "rj_assinatura_2025_retidos_por_origem": perfil_rj[cnpj], "erros": erros[cnpj]})
    if any(file_sha256(p) != hashes[str(p.resolve())] for p in fontes):
        raise RuntimeError("fonte mudou durante a auditoria")
    destino = destino_novo(destino, fontes)
    report = {"gerado_em": agora(), "baseline_conferida": baseline is not None, "fontes": [{"path": k, "sha256": v} for k,v in hashes.items()],
        "escopo": {"assinatura_alvo": 2025, "uf_alvo": "RJ", "aquisicao_publicacao": "2025-01-01/2025-12-31"},
        "janelas_por_status": dict(Counter(v["status"] for v in resultados)), "janelas": resultados,
        "registros_estado": sum(v["registros_estado"] for v in resultados), "contratos_empenhos_distintos_retidos": len(ids),
        "primeira_coleta_observada": min(timestamps) if timestamps else None, "ultima_coleta_observada": max(timestamps) if timestamps else None,
        "ressalvas": ["O estado vazio não contém a resposta HTTP original.", "A deduplicação perdeu URLs secundárias; papéis principal/sub-rogado explicam multiplicidade, não a posição das linhas removidas.", "Reaproveitar snapshot não prova ausência de atualizações posteriores do PNCP."],
        "cobertura_institucional_comprovada": False, "promocao_autorizada": False}
    gravar_json(destino / "auditoria.json", report)
    return report


def planejar(cadastro: Path, auditoria: Path, destino: Path, *, politica: PoliticaColeta | None = None, manifest: Path | None = None) -> dict:
    if manifest:
        base = next(b for b in load_manifest(manifest)["bases"] if b["base_id"] == "contratos_estruturado")
        populacoes = base["acesso"]["populations"]
        params = next(p["params"] for p in populacoes if p["resource_type"] == "contrato_assinado")
        compras = next(p["params"] for p in populacoes if p["resource_type"] == "contratacao")
        politica = PoliticaColeta(ano=int(params["anoAssinatura"]), uf=compras["uf"], publicacao_inicio=params["dataInicial"], publicacao_fim=params["dataFinal"])
    politica = politica or PoliticaColeta()
    cad = json.loads(cadastro.read_text(encoding="utf-8"))
    audit = json.loads(auditoria.read_text(encoding="utf-8"))
    if cad.get("verificacao_completa") is not True or audit.get("baseline_conferida") is not True:
        raise ValueError("cadastro-semente e baseline precisam estar verificados")
    if audit.get("escopo", {}).get("assinatura_alvo") != politica.ano or politica.uf != "RJ" or politica.ano != 2025:
        raise ValueError("este plano é exclusivamente RJ/2025")
    orgaos = Counter(str(u["cnpj"]) for u in cad["verified_unit_scopes"] if u["ufSigla"] == politica.uf)
    if not orgaos:
        raise ValueError("cadastro sem unidades RJ")
    reaproveitaveis = {j["cnpj"]: j for j in audit["janelas"] if j["status"] in {"reaproveitavel", "reaproveitavel_com_dedupe_crossrole", "vazia_declarada_pelo_checkpoint"}}
    janelas = []; reusadas = []
    for cnpj, unidades in sorted(orgaos.items()):
        for inicio, fim in periodos(politica.publicacao_inicio, politica.publicacao_fim):
            chave = f"contratos:{cnpj}:{inicio.isoformat()}:{fim.isoformat()}"
            reuso = cnpj in reaproveitaveis and inicio.isoformat() == "2025-01-01" and fim.isoformat() == "2025-12-31"
            if reuso:
                reusadas.append({"id": chave, "cnpj": cnpj, "unidades_RJ": unidades, "evidencia": reaproveitaveis[cnpj]["status"]})
                continue
            janelas.append({"id": chave, "endpoint": ENDPOINT_CONTRATOS,
                "params": {"dataInicial": inicio.strftime("%Y%m%d"), "dataFinal": fim.strftime("%Y%m%d"), "cnpjOrgao": cnpj, "tamanhoPagina": 500},
                "unidades_RJ_no_cadastro": unidades,
                "prioridade": 0 if inicio.year > politica.ano and cnpj in reaproveitaveis else 1 if inicio.year == politica.ano else 2})
    janelas.sort(key=lambda j: (j["prioridade"], j["params"]["cnpjOrgao"]))
    result = {"gerado_em": agora(), "politica": asdict(politica), "estrategia": "superset_por_cnpj_com_selecao_local_obrigatoria",
        "fontes": [{"path": str(p.resolve()), "sha256": file_sha256(p)} for p in [cadastro, auditoria, *([manifest] if manifest else [])]],
        "orgaos_RJ": len(orgaos), "unidades_RJ": sum(orgaos.values()), "janelas_reaproveitadas": reusadas, "janelas_a_adquirir": janelas,
        "minimo_paginas_http_novas": len(janelas), "janelas_por_prioridade": dict(Counter(str(j["prioridade"]) for j in janelas)),
        "sem_busca_textual_remota": True, "refresh_snapshot_2025_executado": False,
        "ressalva": "Cadastro atual e sementes verificadas não provam censo institucional completo. As páginas podem conter outras UFs/assinaturas: não entram no artefato principal.",
        "promocao_autorizada": False}
    destino = destino_novo(destino, [cadastro, auditoria])
    gravar_json(destino / "plano.json", result)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="acao", required=True)
    a = sub.add_parser("auditar"); a.add_argument("--documents", type=Path, required=True); a.add_argument("--estado", type=Path, required=True); a.add_argument("--baseline", type=Path, required=True); a.add_argument("--destino", type=Path, required=True)
    a = sub.add_parser("planejar"); a.add_argument("--cadastro", type=Path, required=True); a.add_argument("--auditoria", type=Path, required=True); a.add_argument("--destino", type=Path, required=True); a.add_argument("--manifest", type=Path, default=Path("corpus_manifest.yaml"))
    a = p.parse_args(argv)
    if a.acao == "auditar":
        r = auditar(a.documents, a.estado, a.destino, baseline=a.baseline)
        print(json.dumps({k:v for k,v in r.items() if k != "janelas"}, ensure_ascii=False, indent=2))
    else:
        r = planejar(a.cadastro, a.auditoria, a.destino, manifest=a.manifest)
        print(json.dumps({k:v for k,v in r.items() if k not in ["janelas_a_adquirir", "janelas_reaproveitadas"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
