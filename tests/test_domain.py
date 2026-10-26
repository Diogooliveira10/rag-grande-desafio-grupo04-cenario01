"""Regras do domínio testadas sozinhas, sem banco, índice nem LLM."""

import pytest

from src.domain import fusion, purchase_status, text


def test_rrf_soma_o_inverso_da_posicao_em_cada_lista():
    pontos = fusion.pontuacoes_rrf([["a", "b"], ["b", "c"]], k=60)
    assert pontos["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert pontos["a"] == pytest.approx(1 / 61)
    assert pontos["c"] == pytest.approx(1 / 62)
    # Quem aparece nas duas listas fica à frente.
    assert max(pontos, key=pontos.get) == "b"


def test_rrf_mantem_a_ordem_da_primeira_aparicao():
    assert list(fusion.pontuacoes_rrf([["x", "y"], ["z", "x"]])) == ["x", "y", "z"]


def test_rrf_rejeita_k_negativo():
    with pytest.raises(ValueError):
        fusion.pontuacoes_rrf([["a"]], k=-1)


@pytest.mark.parametrize("citadas, com_validas, todas, esperado", [
    ((), False, False, "(situacao IS NULL OR situacao NOT IN ('Anulada', 'Revogada', 'Suspensa'))"),
    (("Anulada",), False, False, "situacao IN ('Anulada')"),
    (("Anulada",), True, False, "(situacao IS NULL OR situacao NOT IN ('Revogada', 'Suspensa'))"),
    (("Anulada", "Revogada", "Suspensa"), True, False, None),
    ((), False, True, None),
])
def test_clausula_de_situacao(citadas, com_validas, todas, esperado):
    assert purchase_status.clausula_de_situacao(citadas, com_validas, todas) == esperado


def test_canceladas_valem_anuladas_e_revogadas():
    assert purchase_status.situacoes_da_pergunta("Quantas compras CANCELADAS em 2025?") == {
        "situacoes_citadas": ("Anulada", "Revogada"), "com_situacoes_validas": False}


def test_normalizar_tira_acento_maiuscula_e_espacos_extras():
    assert text.normalizar("  Município   de MARICÁ ") == "municipio de marica"
    assert text.sem_acento("Ação  X") == "acao  x"
    assert text.normalizar(None) == ""


@pytest.mark.parametrize("pergunta, valor_min, valor_max, periodo", [
    ("contratos acima de r$ 500 mil em 2025", 500_000.0, None, (2025, None, None, None)),
    ("contratos entre 1 e 2 milhoes", 1_000_000.0, 2_000_000.0, (None, None, None, None)),
    ("contratos a partir de 2024", None, None, (None, None, 2024, None)),
    ("contratos ate 2023", None, None, (None, None, None, 2023)),
])
def test_leitura_de_valores_e_periodos(pergunta, valor_min, valor_max, periodo):
    from src.domain import values_and_periods as vp

    normalizada = text.normalizar(pergunta)
    valores = vp._extrair_valores(normalizada, [])
    lido, _ = vp._extrair_periodo(normalizada, valores.numeros)
    assert (valores.valor_min, valores.valor_max) == (valor_min, valor_max)
    assert (lido.ano, lido.ano_final, lido.ano_desde, lido.ano_ate) == periodo


def test_limitar_repeticoes_rebaixa_o_excedente_sem_descartar():
    itens = ["a1", "a2", "a3", "b1", "a4", "c1"]
    lista, rebaixados = fusion.limitar_repeticoes(itens, lambda i: i[0], 2)
    assert lista == ["a1", "a2", "b1", "c1", "a3", "a4"]
    assert rebaixados == 2
    assert sorted(lista) == sorted(itens)


def test_limitar_repeticoes_sem_chave_ou_desligada_nao_mexe():
    itens = ["a1", "a2", "a3", "x"]
    # chave None = sem limite
    lista, rebaixados = fusion.limitar_repeticoes(itens, lambda i: None if i == "a3" else i[0], 1)
    assert lista == ["a1", "a3", "x", "a2"] and rebaixados == 1
    for maximo in (0, -1):
        assert fusion.limitar_repeticoes(itens, lambda i: i[0], maximo) == (itens, 0)


def test_limitar_repeticoes_mantem_a_ordem_relativa_dos_excedentes():
    lista, _ = fusion.limitar_repeticoes(["a1", "a2", "a3", "a4", "a5"], lambda i: "a", 1)
    assert lista == ["a1", "a2", "a3", "a4", "a5"]
