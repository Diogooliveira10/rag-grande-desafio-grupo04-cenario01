"""Índice invertido da BM25: o resultado tem de ser idêntico ao da varredura."""

from __future__ import annotations

import json
import random

import pytest

import src.retrieve as retrieve_module
from src.lexical_index import IndiceLexico
from src.retrieve import _bm25_pelo_indice, _bm25_por_varredura, _tokens, bm25_retrieve

_PALAVRAS = ("ambulancia contrato manutencao computadores pregao eletronico dispensa lei "
             "servico saude educacao niteroi marica tomografo pecas aquisicao de da do e "
             "valor registro ata fornecedor").split()


def _corpus(semente: int, tamanho: int = 120) -> list[dict]:
    sorteio = random.Random(semente)
    registros = []
    for i in range(tamanho):
        texto = " ".join(sorteio.choice(_PALAVRAS) for _ in range(sorteio.randint(0, 25)))
        if i % 17 == 0 and registros:  # texto repetido em outro registro
            texto = registros[-1]["text"]
        registros.append({"chunk_id": f"c{sorteio.randint(0, 10**6):07d}-{i}", "text": texto,
                          "base_id": "contratos_estruturado"})
    return registros


def _perguntas(semente: int) -> list[str]:
    sorteio = random.Random(semente)
    perguntas = [" ".join(sorteio.choice(_PALAVRAS) for _ in range(sorteio.randint(1, 6))) for _ in range(25)]
    return perguntas + ["ambulancia ambulancia de de", "palavra inexistente", "de da do e"]


@pytest.mark.parametrize("semente", range(8))
@pytest.mark.parametrize("top_k", [1, 3, 10, 500])
def test_indice_da_o_mesmo_ranking_e_as_mesmas_notas_da_varredura(semente, top_k):
    registros = _corpus(semente)
    indice = IndiceLexico((r["text"] for r in registros), _tokens)
    sorteio = random.Random(semente + 100)
    for pergunta in _perguntas(semente):
        # Recorte aleatório, como depois de um filtro: frequência e média são do recorte.
        recorte = [r for r in registros if sorteio.random() < 0.7] or registros
        documentos = [(r, r["text"]) for r in recorte]
        termos = _tokens(pergunta)
        esperado = _bm25_por_varredura(termos, documentos, top_k)
        obtido = _bm25_pelo_indice(indice, termos, documentos, top_k)
        assert [(nota, chunk) for nota, chunk, _, _ in obtido] == [(nota, chunk) for nota, chunk, _, _ in esperado]


def test_empate_no_corte_do_top_k_desempata_por_chunk_id():
    registros = [{"chunk_id": f"c{i}", "text": "ambulancia"} for i in (5, 3, 9, 1, 7)]
    indice = IndiceLexico((r["text"] for r in registros), _tokens)
    documentos = [(r, r["text"]) for r in registros]
    obtido = _bm25_pelo_indice(indice, ["ambulancia"], documentos, 2)
    assert [chunk for _, chunk, _, _ in obtido] == ["c1", "c3"]
    assert obtido == _bm25_por_varredura(["ambulancia"], documentos, 2)


def test_texto_fora_do_indice_volta_para_a_varredura():
    indice = IndiceLexico(["ambulancia tipo d"], _tokens)
    documentos = [({"chunk_id": "a"}, "ambulancia tipo d"), ({"chunk_id": "b"}, "ambulancia nova")]
    assert _bm25_pelo_indice(indice, ["ambulancia"], documentos, 10) is None


def _escrever(path, registros):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in registros) + "\n", encoding="utf-8")


def test_bm25_retrieve_usa_o_indice_e_refaz_quando_o_arquivo_muda(tmp_path):
    documentos = tmp_path / "documents.jsonl"
    registros = _corpus(42, tamanho=60)
    _escrever(documentos, registros)
    retrieve_module._INDICE_LEXICO_CACHE.pop(str(documentos), None)

    pelo_indice = bm25_retrieve("ambulancia manutencao", documents_file=documentos, top_k=10)
    indice = retrieve_module._INDICE_LEXICO_CACHE[str(documentos)][2]
    esperado = _bm25_por_varredura(_tokens("ambulancia manutencao"), [(r, r["text"]) for r in registros], 10)
    assert [(x.chunk_id, x.score) for x in pelo_indice] == [(chunk, nota) for nota, chunk, _, _ in esperado]

    _escrever(documentos, registros + [{"chunk_id": "novo", "text": "ambulancia " * 5}])
    novo = bm25_retrieve("ambulancia", documents_file=documentos, top_k=1)
    assert novo[0].chunk_id == "novo"
    assert retrieve_module._INDICE_LEXICO_CACHE[str(documentos)][2] is not indice


def test_registros_recebidos_nao_montam_o_indice_mas_usam_o_que_existe(tmp_path):
    """O recorte filtrado não paga a montagem (~40 s na v7); só a pré-carga ou a
    leitura direta do documents.jsonl a fazem."""
    documentos = tmp_path / "documents.jsonl"
    registros = _corpus(7, tamanho=40)
    _escrever(documentos, registros)
    retrieve_module._INDICE_LEXICO_CACHE.pop(str(documentos), None)

    sem_indice = bm25_retrieve("ambulancia", documents_file=documentos, records=registros[:20], top_k=5)
    assert str(documentos) not in retrieve_module._INDICE_LEXICO_CACHE

    retrieve_module._indice_lexico(documentos, construir=True)
    com_indice = bm25_retrieve("ambulancia", documents_file=documentos, records=registros[:20], top_k=5)
    assert [(x.chunk_id, x.score) for x in com_indice] == [(x.chunk_id, x.score) for x in sem_indice]
