'''Testes do benchmark de regressão de recuperação (eval/medir_regressao.py).'''

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from eval.ablacao_rerank import CasoRecuperacao
from eval.medir_regressao import Alvo, medir_regressao
from src.direct_build import RegistroDireto, chunk_de_registro
from src.index import build_index

BASE = {
    "base_id": "contratos_estruturado", "natureza": "estruturada",
    "acesso": {"tipo": "provider"}, "sensitivity_default": "publico",
    "chunking_sugerido": "1 contrato = 1 chunk",
    "metadados_extras": ["orgao", "modalidade", "valor", "municipio", "ano"],
}

A = {
    "numeroControlePNCP": "11111111000001-2-000001/2025",
    "objetoContrato": "Manutencao de computadores",
    "informacaoComplementar": "processo administrativo diligencia parecer",
}
B = {
    "numeroControlePNCP": "11111111000001-2-000002/2025",
    "objetoContrato": "Fornecimento de refeicoes",
    "informacaoComplementar": "manutencao de computadores " * 3,
}

_DIM = 512


def _embed_bow(texts: list[str]) -> list[list[float]]:
    vetores = []
    for texto in texts:
        vetor = [0.0] * _DIM
        for token in re.findall(r"\w+", texto.casefold()):
            vetor[int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % _DIM] += 1.0
        norma = math.sqrt(sum(v * v for v in vetor)) or 1.0
        vetores.append([v / norma for v in vetor])
    return vetores


def _corpus(tmp_path: Path, nome: str, payloads: list[tuple[dict, str]]) -> Alvo:
    linhas = []
    for payload, url in payloads:
        chunks, _ = chunk_de_registro(
            RegistroDireto(payload=payload, origem=url), BASE, manifest_hash="teste"
        )
        linhas.extend(json.dumps(c, ensure_ascii=False, sort_keys=True) for c in chunks)
    documents = tmp_path / f"{nome}.jsonl"
    documents.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")
    index_dir = tmp_path / f"index_{nome}"
    build_index(input_file=documents, output_dir=index_dir,
                embed_fn=_embed_bow, model="bow-de-teste")
    return Alvo(nome, documents, index_dir)


def test_regressao_e_melhoria_sao_classificadas(tmp_path):
    '''Referência tem só A (caso S1 acerta 1º). O novo corpus traz B com a
    frase repetida em campo não-descritivo, que empurra A para 2º — regressão.
    S2 (B) não existia na referência e passa a ser encontrado — melhoria.'''
    referencia = _corpus(tmp_path, "ref", [(A, "https://exemplo.teste/registro_a.json")])
    novo = _corpus(tmp_path, "novo", [
        (A, "https://exemplo.teste/registro_a.json"),
        (B, "https://exemplo.teste/registro_b.json"),
    ])
    casos = [
        CasoRecuperacao(id="S1", pergunta="manutencao de computadores",
                        bases=["estruturada"], relevante_contem=["manutencao de computadores"],
                        relevante_documento="registro_a.json"),
        CasoRecuperacao(id="S2", pergunta="manutencao de computadores",
                        bases=["estruturada"], relevante_contem=["manutencao de computadores"],
                        relevante_documento="registro_b.json"),
    ]
    relatorio = medir_regressao(casos, referencia=referencia, novo=novo,
                               embed_fn=_embed_bow, top_k=10)
    assert [r["id"] for r in relatorio["regressoes"]] == ["S1"]
    assert [r["id"] for r in relatorio["melhorias"]] == ["S2"]
    assert relatorio["empates"] == 0
    assert relatorio["resumos"]["ref"]["hit@1"] == 0.5  # S1 sim, S2 não
    assert relatorio["resumos"]["novo"]["hit@1"] == 0.5  # S2 sim, S1 não


def test_empate_quando_nada_muda(tmp_path):
    corpus = _corpus(tmp_path, "igual", [(A, "https://exemplo.teste/registro_a.json")])
    casos = [CasoRecuperacao(id="S1", pergunta="manutencao de computadores",
                             bases=["estruturada"],
                             relevante_contem=["manutencao de computadores"],
                             relevante_documento="registro_a.json")]
    relatorio = medir_regressao(casos, referencia=corpus, novo=corpus,
                               embed_fn=_embed_bow)
    assert relatorio["regressoes"] == []
    assert relatorio["melhorias"] == []
    assert relatorio["empates"] == 1
