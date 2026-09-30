"""Métricas e rótulos da ablação do rerank (eval/ablacao_rerank.py), sem índice real."""

import json
from pathlib import Path

from eval.ablacao_rerank import (
    CasoRecuperacao,
    carregar_casos,
    e_relevante,
    posicao_do_primeiro_relevante,
    resumir,
)
from src.retrieve import RetrievalResult

CONJUNTO = Path(__file__).resolve().parents[1] / "eval" / "retrieval_set.jsonl"


def _resultado(texto, source_file="x.json"):
    return RetrievalResult(chunk_id=texto[:8], content=texto, metadata={"source_file": source_file}, score=1.0)


def test_relevancia_ignora_acento_caixa_e_espacos_e_exige_todos_os_trechos():
    caso = CasoRecuperacao("R", "p", ["normativa"], ["condição indispensável", "eficácia do CONTRATO"])

    assert e_relevante(caso, _resultado("A  CONDICAO indispensavel para a eficacia do contrato"))
    assert not e_relevante(caso, _resultado("condição indispensável, sem o resto"))


def test_relevante_documento_restringe_ao_arquivo_de_origem():
    caso = CasoRecuperacao("R", "p", ["normativa"], ["decreto"], relevante_documento="normativo_003")

    assert e_relevante(caso, _resultado("Decreto nº 1", source_file="data/editais_normativo_003_x.html"))
    assert not e_relevante(caso, _resultado("Decreto nº 1", source_file="data/editais_normativo_004_x.pdf"))


def test_posicao_e_metricas_contam_so_o_top_10():
    caso = CasoRecuperacao("R", "p", ["normativa"], ["alvo"])
    ranking = [_resultado(f"outro {i}") for i in range(2)] + [_resultado("o alvo")]

    assert posicao_do_primeiro_relevante(caso, ranking) == 3
    assert posicao_do_primeiro_relevante(caso, [_resultado(f"outro {i}") for i in range(10)] + [_resultado("alvo")]) is None

    metricas = resumir([1, 3, None, 10])
    assert metricas == {"hit@1": 0.25, "hit@3": 0.5, "hit@5": 0.5, "hit@10": 0.75, "mrr@10": round((1 + 1 / 3 + 0.1) / 4, 4)}


def test_conjunto_do_repositorio_tem_ids_unicos_e_bases_validas():
    casos = carregar_casos(CONJUNTO)
    ids = [caso.id for caso in casos]

    assert len(ids) == len(set(ids)) >= 30
    assert {base for caso in casos for base in caso.bases} <= {"normativa", "estruturada", "conversacional", "agregada"}
    assert all(caso.relevante_contem for caso in casos)
    assert all(json.dumps(caso.pergunta) for caso in casos)


def test_conjunto_separa_perguntas_de_identificador():
    casos = carregar_casos(CONJUNTO)
    tipos = {caso.tipo for caso in casos}

    assert tipos == {"parafrase", "identificador"}
    assert sum(caso.tipo == "identificador" for caso in casos) >= 10
