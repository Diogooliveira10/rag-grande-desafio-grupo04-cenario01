'''Testes do harness de medição da projeção (eval/medir_projecao.py).

O caso sintético monta exatamente o risco da projeção: um documento relevante
cujo OBJETO responde a pergunta e um ruído cujo JSON cita a mesma frase em
campo não-descritivo. A projeção compacta precisa acertar o primeiro; a
completa é dominada pelo ruído — e é isso que a medição deve mostrar.
'''

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from eval.ablacao_rerank import CasoRecuperacao
from eval.medir_projecao import medir_projecao
from src.direct_build import RegistroDireto, chunk_de_registro

BASE = {
    "base_id": "contratos_estruturado", "natureza": "estruturada",
    "acesso": {"tipo": "provider"}, "sensitivity_default": "publico",
    "chunking_sugerido": "1 contrato = 1 chunk",
    "metadados_extras": ["orgao", "modalidade", "valor", "municipio", "ano"],
}

A_RELEVANTE = {
    "numeroControlePNCP": "11111111000001-2-000001/2025",
    "objetoContrato": "Manutencao de computadores",
    "dataAssinatura": "2025-01-01",
    "valorGlobal": 100.0,
    "informacaoComplementar": "processo administrativo diligencia parecer encaminhamento",
    "orgaoEntidade": {"cnpj": "11111111000001", "razaoSocial": "ORGAO ALFA", "esferaId": "M"},
    "unidadeOrgao": {"municipioNome": "Alfa", "ufSigla": "RJ"},
}
B_RUIDO = {
    "numeroControlePNCP": "11111111000001-2-000002/2025",
    "objetoContrato": "Fornecimento de refeicoes",
    "informacaoComplementar": "manutencao de computadores " * 4,
}

_DIM = 512


def _embed_bow(texts: list[str]) -> list[list[float]]:
    '''Bag-of-words em balde fixo: semântica de sobreposição de tokens.'''
    vetores = []
    for texto in texts:
        vetor = [0.0] * _DIM
        for token in re.findall(r"\w+", texto.casefold()):
            balde = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % _DIM
            vetor[balde] += 1.0
        norma = math.sqrt(sum(v * v for v in vetor)) or 1.0
        vetores.append([v / norma for v in vetor])
    return vetores


def _documents(tmp_path: Path) -> Path:
    linhas = []
    for payload, url in (
        (A_RELEVANTE, "https://exemplo.teste/registro_a.json"),
        (B_RUIDO, "https://exemplo.teste/registro_b.json"),
    ):
        chunks, _ = chunk_de_registro(
            RegistroDireto(payload=payload, origem=url), BASE, manifest_hash="teste"
        )
        linhas.extend(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in chunks)
    documents = tmp_path / "documents.jsonl"
    documents.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")
    return documents


def test_projecao_compacta_acerta_o_objeto_e_completa_cai_no_ruido(tmp_path):
    caso = CasoRecuperacao(
        id="S1",
        pergunta="manutencao de computadores",
        bases=["estruturada"],
        relevante_contem=["manutencao de computadores"],
        relevante_documento="registro_a.json",
    )
    relatorio = medir_projecao(
        [caso],
        documents_file=_documents(tmp_path),
        embed_fn=_embed_bow,
        modelo="bow-de-teste",
        destino=tmp_path / "runs",
    )
    assert relatorio["resumos"]["compacta"]["hit@1"] == 1.0
    assert relatorio["resumos"]["completa"]["hit@1"] == 0.0
    # A enriquecida também isola o objeto: o ruído vive num campo que ela não
    # inclui (informacaoComplementar).
    assert relatorio["resumos"]["enriquecida"]["hit@1"] == 1.0
    assert relatorio["por_caso"][0]["posicao_compacta"] == 1
    assert relatorio["deltas_hit@3"]["compacta"] == 0.0  # ambos em top-3; o efeito é no topo


def test_relatorio_compara_os_modos_pedidos(tmp_path):
    caso = CasoRecuperacao(
        id="S2", pergunta="manutencao de computadores", bases=["estruturada"],
        relevante_contem=["manutencao de computadores"], relevante_documento="registro_a.json",
    )
    relatorio = medir_projecao(
        [caso], documents_file=_documents(tmp_path), embed_fn=_embed_bow,
        modelo="bow-de-teste", destino=tmp_path / "runs",
        modos=("completa", "compacta"),
    )
    assert set(relatorio["resumos"]) == {"completa", "compacta"}
    for chave in ("hit@1", "hit@3", "hit@5", "hit@10", "mrr@10"):
        assert chave in relatorio["resumos"]["completa"]
    assert "compacta" in relatorio["deltas_hit@3"]
    assert relatorio["total"] == 1
