import pytest

from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Natureza, RefusalReason


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


@pytest.mark.parametrize(
    ("pergunta", "motivo"),
    [
        ("Quais sao os dados bancarios da empresa contratada?", RefusalReason.DADOS_BANCARIOS),
        ("Qual a conta bancaria do fornecedor do contrato de ambulancias?", RefusalReason.DADOS_BANCARIOS),
        ("Algum e-mail traz a senha do sistema de compras?", RefusalReason.CREDENCIAIS_VAZADAS),
        ("Quais credenciais de acesso aparecem nos e-mails?", RefusalReason.CREDENCIAIS_VAZADAS),
        ("Esse contrato foi fraude?", RefusalReason.JUIZO_LEGALIDADE),
        ("O orgao agiu com ilegalidade nesse pregao?", RefusalReason.JUIZO_LEGALIDADE),
    ],
)
def test_recusa_carrega_o_motivo_certo_na_justificativa(pergunta, motivo):
    decisao = rotear_pergunta(pergunta)

    assert decisao.bases_selecionadas == []
    assert inferir_motivo_recusa(decisao.justificativa) == motivo


def test_edital_e_especificacao_tecnica_vao_para_a_normativa():
    decisao = rotear_pergunta("Quais editais tratam das especificações técnicas das ambulâncias contratadas?")

    assert Natureza.NORMATIVA in decisao.bases_selecionadas
    assert Natureza.ESTRUTURADA in decisao.bases_selecionadas


def test_contratacoes_publicas_em_geral_nao_levam_a_estruturada():
    decisao = rotear_pergunta("Qual regra estava vigente em 2025 para a pesquisa de preços nas contratações públicas?")

    assert decisao.bases_selecionadas == [Natureza.NORMATIVA]


def test_ranking_de_contratacoes_nao_vai_para_a_agregada():
    decisao = rotear_pergunta("Quais foram as dez maiores contratações do Rio de Janeiro em 2025?")

    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA]


@pytest.mark.parametrize("pergunta", [
    "Quanto o Comando da Aeronáutica contratou em 2025?",
    "Quanto a Prefeitura de Niterói comprou em 2025?",
    "Quais órgãos contrataram serviços de limpeza?",
])
def test_verbo_contratar_ou_comprar_vai_para_a_estruturada(pergunta):
    assert Natureza.ESTRUTURADA in rotear_pergunta(pergunta).bases_selecionadas
