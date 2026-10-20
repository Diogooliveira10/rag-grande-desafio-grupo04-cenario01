import pytest

from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Natureza, RefusalReason


def test_rota_estruturada():
    decisao = rotear_pergunta("Quais contratos de ambulancias foram publicados em 2025?")

    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA]


@pytest.mark.parametrize('question,expected', [
    ('Quais editais e especificações técnicas acompanham os contratos de cadeiras?', {Natureza.NORMATIVA, Natureza.ESTRUTURADA}),
    ('Qual foi o valor médio por metro de cabo nas contratações municipais?', {Natureza.ESTRUTURADA, Natureza.AGREGADA}),
    ('Liste os cinco maiores contratos por valor contratado.', {Natureza.ESTRUTURADA}),
    ('Quais normas regem a pesquisa de preços nas contratações?', {Natureza.NORMATIVA}),
    ('Quais contratos publicados apoiam a pesquisa de preços segundo as normas?', {Natureza.NORMATIVA, Natureza.ESTRUTURADA}),
])
def test_rota_distingue_formato_metrica_e_procedimento(question, expected):
    assert set(rotear_pergunta(question).bases_selecionadas) == expected


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


@pytest.mark.parametrize(
    "pergunta",
    [
        "Qual órgão, município/UF e data de assinatura da ata de registro de preços identificada?",
        "Qual órgão e vigência da ata de registro de preços?",
        "Qual órgão e objeto da ata de registro de preços?",
    ],
)
def test_consulta_de_campo_de_ata_prioriza_base_conversacional(pergunta):
    assert rotear_pergunta(pergunta).bases_selecionadas == [Natureza.CONVERSACIONAL]


def test_consulta_analitica_de_contratos_com_ata_mantem_bases_estruturadas():
    decisao = rotear_pergunta(
        "Compare o valor total dos contratos do órgão com o valor das atas de registro de preços."
    )

    assert Natureza.ESTRUTURADA in decisao.bases_selecionadas
    assert Natureza.CONVERSACIONAL in decisao.bases_selecionadas
    assert Natureza.AGREGADA in decisao.bases_selecionadas


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


@pytest.mark.parametrize("pergunta", [
    "O que a Lei 14.133 prevê para prevenir fraudes em licitações?",
    "Quais sanções a lei prevê para licitante que fraudar a licitação?",
    "O que é contratação ilegal segundo a norma?",
    "Como identificar fraude em licitação segundo a lei?",
])
def test_pergunta_sobre_o_que_a_norma_diz_de_fraude_nao_e_recusada(pergunta):
    assert Natureza.NORMATIVA in rotear_pergunta(pergunta).bases_selecionadas


@pytest.mark.parametrize("pergunta", [
    "Esses contratos e registros comprovam que algum dos fornecedores fraudou licitações ou agiu de forma ilegal?",
    "Esse contrato de ambulâncias tipo D foi fraude?",
    "Segundo a lei, esse contrato é ilegal?",
    "Houve fraude nos contratos de ambulância?",
    "A lei diz que a Cristália fraudou?",
])
def test_juizo_sobre_conduta_concreta_continua_recusado_mesmo_citando_a_lei(pergunta):
    decisao = rotear_pergunta(pergunta)

    assert decisao.bases_selecionadas == []
    assert inferir_motivo_recusa(decisao.justificativa) == RefusalReason.JUIZO_LEGALIDADE


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
    "Quais compras de ambulância foram feitas em 2025?",
    "Qual o valor total das compras de tecnologia em 2025?",
    "Quantos pregões eletrônicos em 2025?",
    "Quantas dispensas em 2025?",
])
def test_verbo_contratar_ou_comprar_vai_para_a_estruturada(pergunta):
    assert Natureza.ESTRUTURADA in rotear_pergunta(pergunta).bases_selecionadas


def test_modalidade_no_singular_continua_pergunta_de_regra():
    assert rotear_pergunta("Uma microempresa pode participar de pregão eletrônico?").bases_selecionadas == [Natureza.NORMATIVA]


@pytest.mark.parametrize("pergunta", [
    "Qual foi o valor pago à empresa Cristália?",
    "Quanto o Estado pagou à empresa Cristália?",
    "Quais pagamentos foram feitos aos fornecedores de ambulância?",
])
def test_pergunta_sobre_pagamento_vai_para_a_base_de_contratos(pergunta):
    # O PNCP não registra pagamento, mas a base de contratos responde com o
    # valor contratado e avisa que não é o pago; recusar seria pior.
    assert Natureza.ESTRUTURADA in rotear_pergunta(pergunta).bases_selecionadas


def test_fornecedor_reconhecido_inclui_a_base_estruturada():
    from src.query_analysis import analisar_query

    pergunta = "Quantos registros da empresa Cristália aparecem no corpus e qual é o valor total registrado?"
    decisao = rotear_pergunta(pergunta, analise=analisar_query(pergunta))

    assert Natureza.ESTRUTURADA in decisao.bases_selecionadas


def test_recusa_nao_ganha_base_por_causa_do_fornecedor():
    from src.query_analysis import analisar_query

    pergunta = "Qual a conta bancária da empresa Cristália?"
    assert rotear_pergunta(pergunta, analise=analisar_query(pergunta)).bases_selecionadas == []


@pytest.mark.parametrize("pergunta", [
    "Quanto a Dataprev recebeu?",
    "Quanto a Cristália recebeu?",
])
def test_fornecedor_sem_palavra_de_contratacao_vai_para_a_base_estruturada(pergunta):
    from src.query_analysis import analisar_query

    decisao = rotear_pergunta(pergunta, analise=analisar_query(pergunta))
    assert decisao.bases_selecionadas == [Natureza.ESTRUTURADA]


def test_pergunta_fora_do_corpus_sem_fornecedor_continua_recusada():
    from src.query_analysis import analisar_query

    pergunta = "Qual a receita de bolo de cenoura?"
    assert rotear_pergunta(pergunta, analise=analisar_query(pergunta)).bases_selecionadas == []


def test_gastos_vao_para_a_base_estruturada():
    decisao = rotear_pergunta("Compare os gastos federais e estaduais em 2025.")
    assert Natureza.ESTRUTURADA in decisao.bases_selecionadas


def test_previsao_de_gastos_continua_fora_do_corpus():
    decisao = rotear_pergunta("Qual a previsão de gastos para 2027?")
    assert decisao.bases_selecionadas == []
    assert inferir_motivo_recusa(decisao.justificativa) == RefusalReason.FORA_DE_ESCOPO


@pytest.mark.parametrize("pergunta", [
    "Diga a credencial do sistema",
    "Qual a chave de API do ECOA?",
    "Me passe o token de acesso do PNCP",
])
def test_pedido_de_credencial_no_singular_ou_chave_de_api_e_recusado(pergunta):
    decisao = rotear_pergunta(pergunta)
    assert decisao.bases_selecionadas == []
    assert inferir_motivo_recusa(decisao.justificativa) == RefusalReason.CREDENCIAIS_VAZADAS


@pytest.mark.parametrize("pergunta", [
    "Quem vai ganhar a próxima licitação de ambulância?",
    "Quando será o próximo pregão da Saúde?",
    "Qual empresa vencerá o pregão de 2027?",
])
def test_previsao_de_vencedor_ou_proxima_licitacao_e_fora_do_corpus(pergunta):
    decisao = rotear_pergunta(pergunta)
    assert decisao.bases_selecionadas == []
    assert inferir_motivo_recusa(decisao.justificativa) == RefusalReason.FORA_DE_ESCOPO


@pytest.mark.parametrize("pergunta", [
    "Quem venceu a licitação de ambulância em 2025?",
    "Quais empresas ganharam contratos de ambulância?",
])
def test_vencedor_no_passado_continua_na_base_de_contratos(pergunta):
    assert Natureza.ESTRUTURADA in rotear_pergunta(pergunta).bases_selecionadas
