import pytest

from src.router import rotear_pergunta
from src.schema import Natureza


def test_rota_estruturada():
    decisao = rotear_pergunta("Quais contratos de ambulancias foram publicados em 2025?")

    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA]


def test_rota_agregada_e_estruturada():
    decisao = rotear_pergunta(
        "Qual foi a mediana do valor por ambulancia nas contratacoes do RJ?"
    )

    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA, Natureza.AGREGADA]


def test_rota_normativa_e_estruturada():
    decisao = rotear_pergunta(
        "Sou MEI, posso participar de pregao eletronico? Tem algum aberto para material de escritorio?"
    )

    assert decisao.bases_selecionadas == [Natureza.NORMATIVA, Natureza.ESTRUTURADA]


def test_rota_estruturada_e_conversacional():
    decisao = rotear_pergunta(
        "Entre os fornecedores, existem atas que mencionem atraso de entrega?"
    )

    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA, Natureza.CONVERSACIONAL]


def test_recusa_nao_seleciona_base():
    decisao = rotear_pergunta("Esses contratos comprovam que algum fornecedor fraudou licitacoes?")

    assert decisao.bases_selecionadas == []
    assert decisao.confidence > 0.9


def test_pergunta_fora_de_escopo_nao_seleciona_base():
    decisao = rotear_pergunta(
        "Quais licitacoes estarao abertas no Rio de Janeiro no proximo mes?"
    )

    assert decisao.bases_selecionadas == []


def test_pergunta_vazia():
    decisao = rotear_pergunta("  ")

    assert decisao.bases_selecionadas == []
    assert decisao.confidence == 0.0

@pytest.mark.parametrize(
    "pergunta",
    [
        "Quanto a Polícia Militar planeja gastar em 2025?",
        "Qual o valor total do PCA 2025?",
        "Quais classes têm o maior valor no plano de contratações anual?",
        "Qual o maior valor planejado para material médico?",
    ],
)
def test_perguntas_sobre_o_pca_vao_para_a_base_agregada(pergunta):
    assert Natureza.AGREGADA in rotear_pergunta(pergunta).bases_selecionadas
