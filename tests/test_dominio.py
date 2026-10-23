"""Regras do domínio testadas sozinhas, sem banco, índice nem LLM."""

import pytest

from src.dominio import fusao, situacao, texto


def test_rrf_soma_o_inverso_da_posicao_em_cada_lista():
    pontos = fusao.pontuacoes_rrf([["a", "b"], ["b", "c"]], k=60)
    assert pontos["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert pontos["a"] == pytest.approx(1 / 61)
    assert pontos["c"] == pytest.approx(1 / 62)
    # Quem aparece nas duas listas fica à frente.
    assert max(pontos, key=pontos.get) == "b"


def test_rrf_mantem_a_ordem_da_primeira_aparicao():
    assert list(fusao.pontuacoes_rrf([["x", "y"], ["z", "x"]])) == ["x", "y", "z"]


def test_rrf_rejeita_k_negativo():
    with pytest.raises(ValueError):
        fusao.pontuacoes_rrf([["a"]], k=-1)


@pytest.mark.parametrize("citadas, com_validas, todas, esperado", [
    ((), False, False, "(situacao IS NULL OR situacao NOT IN ('Anulada', 'Revogada', 'Suspensa'))"),
    (("Anulada",), False, False, "situacao IN ('Anulada')"),
    (("Anulada",), True, False, "(situacao IS NULL OR situacao NOT IN ('Revogada', 'Suspensa'))"),
    (("Anulada", "Revogada", "Suspensa"), True, False, None),
    ((), False, True, None),
])
def test_clausula_de_situacao(citadas, com_validas, todas, esperado):
    assert situacao.clausula_de_situacao(citadas, com_validas, todas) == esperado


def test_canceladas_valem_anuladas_e_revogadas():
    assert situacao.situacoes_da_pergunta("Quantas compras CANCELADAS em 2025?") == {
        "situacoes_citadas": ("Anulada", "Revogada"), "com_situacoes_validas": False}


def test_normalizar_tira_acento_maiuscula_e_espacos_extras():
    assert texto.normalizar("  Município   de MARICÁ ") == "municipio de marica"
    assert texto.sem_acento("Ação  X") == "acao  x"
    assert texto.normalizar(None) == ""
