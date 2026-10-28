from __future__ import annotations

from datetime import date

import pytest

from src import query_analysis as qa
from src.schema import QueryFilters


@pytest.mark.parametrize("qualificador,tipo", [
    ("contratado", "contratado"), ("estimado", "estimado"),
    ("homologado", "homologado"),
])
def test_regras_distinguem_campo_de_valor_explicitamente_pedido(qualificador, tipo):
    filtros = qa.analisar_query(
        f"Maiores contratações por valor {qualificador} publicadas em 2025",
        modo="regras",
    ).filtros
    assert filtros.tipo_valor == tipo
    assert filtros.referencia_temporal == "publicacao"


# --------------------------------------------------------------------------
# Ano
# --------------------------------------------------------------------------


def test_extrai_ano_simples():
    resultado = qa.analisar_query("quais contratos foram assinados em 2023?")
    assert resultado.filtros.ano == 2023


def test_sem_ano_quando_nao_mencionado():
    resultado = qa.analisar_query("quais contratos foram assinados pela secretaria?")
    assert resultado.filtros.ano is None


def test_ignora_ano_fora_do_intervalo_plausivel():
    # "12345" não deve ser confundido com ano (regex exige exatamente 4 dígitos 19xx/20xx)
    resultado = qa.analisar_query("processo numero 12345 foi publicado?")
    assert resultado.filtros.ano is None


# --------------------------------------------------------------------------
# Valor
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("contratos acima de R$ 500.000,00", 500_000.0),
        ("contratos com valor maior que 100.000", 100_000.0),
        ("contratos a partir de 1,5 milhão", 1_500_000.0),
        ("contratos com mais de 200 mil reais", 200_000.0),
    ],
)
def test_extrai_valor_minimo(pergunta, esperado):
    resultado = qa.analisar_query(pergunta)
    assert resultado.filtros.valor_min == pytest.approx(esperado)
    assert resultado.filtros.valor_max is None


@pytest.mark.parametrize(
    "pergunta,esperado",
    [
        ("contratos abaixo de R$ 50.000,00", 50_000.0),
        ("contratos com valor menor que 10 mil", 10_000.0),
        ("contratos de ate 300.000", 300_000.0),
    ],
)
def test_extrai_valor_maximo(pergunta, esperado):
    resultado = qa.analisar_query(pergunta)
    assert resultado.filtros.valor_max == pytest.approx(esperado)
    assert resultado.filtros.valor_min is None


def test_extrai_faixa_de_valor():
    resultado = qa.analisar_query("contratos entre R$ 100.000,00 e R$ 500.000,00")
    assert resultado.filtros.valor_min == pytest.approx(100_000.0)
    assert resultado.filtros.valor_max == pytest.approx(500_000.0)


def test_sem_valor_quando_nao_mencionado():
    resultado = qa.analisar_query("quais contratos foram assinados pela secretaria?")
    assert resultado.filtros.valor_min is None
    assert resultado.filtros.valor_max is None


# --------------------------------------------------------------------------
# Órgão
# --------------------------------------------------------------------------


def test_extrai_orgao_simples():
    resultado = qa.analisar_query("quais contratos a Secretaria de Saude assinou em 2024?")
    assert resultado.filtros.orgao == "Secretaria de Saude"


def test_extrai_orgao_com_pontuacao_no_fim():
    resultado = qa.analisar_query("contratos da Prefeitura de Niteroi, acima de 100 mil.")
    assert resultado.filtros.orgao == "MUNICIPIO DE NITEROI"


def test_sem_orgao_quando_nao_mencionado():
    resultado = qa.analisar_query("quais contratos foram assinados em 2024?")
    assert resultado.filtros.orgao is None


def test_nao_confunde_palavra_solta_com_orgao():
    # "instituto" sem nome próprio atrás não deve virar um órgão vazio/estranho
    resultado = qa.analisar_query("existe algum instituto mencionado nos contratos?")
    assert resultado.filtros.orgao is None


# --------------------------------------------------------------------------
# Combinação e casos vazios
# --------------------------------------------------------------------------


def test_combina_todos_os_filtros():
    resultado = qa.analisar_query(
        "contratos da Secretaria de Educacao em 2022 acima de R$ 1.000.000,00"
    )
    assert resultado.filtros.orgao == "Secretaria de Educacao"
    assert resultado.filtros.ano == 2022
    assert resultado.filtros.valor_min == pytest.approx(1_000_000.0)


def test_query_vazia_nao_gera_filtro():
    resultado = qa.analisar_query("")
    assert resultado.filtros == QueryFilters()


def test_query_sem_filtro_nenhum():
    resultado = qa.analisar_query("como funciona o pregao eletronico?")
    assert resultado.filtros.esta_vazio()


def test_query_original_preservada():
    pergunta = "contratos da Secretaria de Saude em 2024"
    resultado = qa.analisar_query(pergunta)
    assert resultado.query_original == pergunta


# --------------------------------------------------------------------------
# filtros_para_retrieve
# --------------------------------------------------------------------------


def test_filtros_para_retrieve_vazio_quando_sem_filtro():
    assert qa.filtros_para_retrieve(QueryFilters()) == {}


def test_schema_llm_aceita_cnpj_analitico():
    from src.schema import QueryIntent

    schema = qa._schema_analise()
    campos = schema["$defs"]["QueryFilters"]["properties"]["filtros_analiticos"]["properties"]
    assert "cnpj" in campos
    agrupamentos = schema["$defs"]["QueryFilters"]["properties"]["agrupamentos"]["items"]["enum"]
    assert "cnpj" not in agrupamentos
    intent = QueryIntent.model_validate({
        "pergunta": "Contratos da empresa",
        "bases": ["estruturada"],
        "filtros": {"filtros_analiticos": {"cnpj": "44292391000130"}},
    })
    assert intent.filtros.filtros_analiticos["cnpj"] == "44292391000130"
    with pytest.raises(ValueError, match="Campo analítico"):
        QueryIntent.model_validate({
            "pergunta": "Agrupar por CNPJ",
            "bases": ["estruturada"],
            "operacao": "agrupamento",
            "termos_objeto": [],
            "filtros": {
                "metrica": "contagem",
                "agrupamentos": ["cnpj"],
                "filtros_analiticos": {},
            },
        })


def test_filtros_para_retrieve_orgao_usa_contains():
    filtros = QueryFilters(orgao="Secretaria de Saude")
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["orgao"] == {"operator": "contains", "value": "Secretaria de Saude", "optional": False}


def test_filtros_para_retrieve_valor_minimo_isolado():
    filtros = QueryFilters(valor_min=100_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": ">=", "value": 100_000.0, "optional": False}


def test_filtros_para_retrieve_valor_maximo_isolado():
    filtros = QueryFilters(valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": "<=", "value": 500_000.0, "optional": False}


def test_filtros_para_retrieve_faixa_de_valor_vira_lista_de_condicoes():
    filtros = QueryFilters(valor_min=100_000.0, valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == [
        {"operator": ">=", "value": 100_000.0, "optional": False},
        {"operator": "<=", "value": 500_000.0, "optional": False},
    ]


def test_filtros_para_retrieve_ano_vira_faixa_de_data_vigencia():
    filtros = QueryFilters(ano=2023)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["data_vigencia"] == [
        {"operator": ">=", "value": date(2023, 1, 1), "optional": False},
        {"operator": "<", "value": date(2024, 1, 1), "optional": False},
    ]


def test_filtro_obrigatorio_aplica_ano_e_ignora_natureza_incompativel():
    """Caso real do PCA: o indicador com a resposta não tem data_vigencia."""
    from src.retrieve import _matches_filters

    filtros = qa.filtros_para_retrieve(QueryFilters(ano=2025))

    assert _matches_filters({"base_id": "pca_agregado"}, filtros)
    assert _matches_filters({"base_id": "contratos_estruturado", "data_vigencia": "2025-06-30"}, filtros)
    assert not _matches_filters({"base_id": "contratos_estruturado", "data_vigencia": "2023-06-30"}, filtros)


# --------------------------------------------------------------------------
# Ano não vira valor: o número precisa vir logo depois do gatilho, e um ano
# solto ("2025") só conta como valor com "R$", "reais" ou "mil/milhão".
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "quais fornecedores receberam o maior valor total em contratos de 2025?",
    "contratos vigentes entre 2024 e 2025",
    "contratos publicados ate 2025",
    "pregoes com criterio de menor preco em 2025",
    "contratos de limpeza a partir de 2024",
])
def test_ano_nao_vira_valor(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.valor_min is None and filtros.valor_max is None


def test_numero_com_reais_continua_sendo_valor():
    assert qa.analisar_query("contratos acima de 2000 reais").filtros.valor_min == pytest.approx(2000.0)


def test_numero_com_cifrao_continua_sendo_valor():
    assert qa.analisar_query("contratos ate R$ 2025").filtros.valor_max == pytest.approx(2025.0)


def test_maior_que_com_numero_continua_sendo_valor():
    assert qa.analisar_query("contratos maior que 100 mil").filtros.valor_min == pytest.approx(100_000.0)


# --------------------------------------------------------------------------
# Comparação de grandeza ("acima de", "superior a"...): o número é valor mesmo
# com cara de ano; só "a partir de" e "até" tratam 2024/2025 como ano.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "Contratos acima de 2000 em 2025",
    "Contratos acima de 3000 em 2025",
])
def test_numero_depois_de_acima_de_e_valor_mesmo_com_cara_de_ano(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.ano == 2025
    assert filtros.valor_min is not None and filtros.valor_min >= 2000


@pytest.mark.parametrize("pergunta, campo, esperado", [
    ("valor superior a 2000", "valor_min", 2000.0),
    ("valor superior ou igual a 500 mil", "valor_min", 500_000.0),
    ("valor inferior a 2000", "valor_max", 2000.0),
    ("valor inferior ou igual a R$ 1 milhão", "valor_max", 1_000_000.0),
    ("contratos maior que 2000", "valor_min", 2000.0),
    ("contratos menor que 2000", "valor_max", 2000.0),
    ("contratos com menos de 1500 reais", "valor_max", 1500.0),
])
def test_comparacoes_de_valor_reconhecidas(pergunta, campo, esperado):
    filtros = qa.analisar_query(pergunta).filtros
    assert getattr(filtros, campo) == pytest.approx(esperado)
    assert filtros.ano is None


def test_menos_de_com_substantivo_e_contagem():
    filtros = qa.analisar_query("municípios com menos de 50 mil habitantes").filtros
    assert filtros.valor_min is None and filtros.valor_max is None


def test_faixa_com_numero_com_cara_de_ano_e_faixa_de_valor():
    filtros = qa.analisar_query("contratos entre 1000 e 2000").filtros
    assert (filtros.valor_min, filtros.valor_max) == (pytest.approx(1000.0), pytest.approx(2000.0))
    assert filtros.ano is None


def test_faixa_so_usa_o_numero_logo_depois_do_e():
    """Faixa incompleta é ambígua: nenhum filtro de valor, e o 2025 continua ano."""
    filtros = qa.analisar_query("contratos entre 1000 e os contratos de 2025").filtros
    assert filtros.valor_max is None
    assert filtros.ano == 2025


# --------------------------------------------------------------------------
# Contagem não vira valor: número seguido de substantivo ("3 contratos").
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "quais empresas têm mais de 3 contratos com o Estado?",
    "órgãos com mais de 10 licitações em 2025",
    "municípios acima de 500 mil habitantes",
])
def test_contagem_nao_vira_valor(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.valor_min is None and filtros.valor_max is None


@pytest.mark.parametrize("pergunta, esperado", [
    ("contratos acima de 500 mil em 2025", 500_000.0),
    ("contratos acima de 1 milhão de reais", 1_000_000.0),
    ("contratos acima de 3 reais", 3.0),
    ("contratos acima de R$ 3 por unidade", 3.0),
    ("contratos acima de 500 mil?", 500_000.0),
    ("planos acima de 2 bilhões", 2_000_000_000.0),
])
def test_valor_seguido_de_conectora_ou_moeda_continua_sendo_valor(pergunta, esperado):
    assert qa.analisar_query(pergunta).filtros.valor_min == pytest.approx(esperado)


# --------------------------------------------------------------------------
# Período e ano de norma
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "contratos vigentes entre 2024 e 2025",
    "contratos de 2024 a 2025",
    "contratos de 2024 até 2025",
    "contratos entre 2025 e 2024",
])
def test_periodo_entre_dois_anos(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert (filtros.ano, filtros.ano_final) == (2024, 2025)
    assert filtros.valor_min is None and filtros.valor_max is None


def test_periodo_vira_intervalo_de_data_vigencia_do_inicio_ao_fim():
    convertido = qa.filtros_para_retrieve(QueryFilters(ano=2024, ano_final=2025))
    assert convertido["data_vigencia"] == [
        {"operator": ">=", "value": date(2024, 1, 1), "optional": False},
        {"operator": "<", "value": date(2026, 1, 1), "optional": False},
    ]


@pytest.mark.parametrize("referencia,campo", [
    ("assinatura", "data_assinatura"), ("resultado", "data_resultado"),
    ("publicacao", "data_publicacao"),
])
def test_explicit_temporal_reference_does_not_filter_validity(referencia, campo):
    convertido = qa.filtros_para_retrieve(QueryFilters(ano=2025, referencia_temporal=referencia))
    assert campo in convertido and "data_vigencia" not in convertido


def test_pca_year_and_analytical_scope_are_preserved():
    convertido = qa.filtros_para_retrieve(QueryFilters(
        ano=2025, referencia_temporal="pca", filtros_analiticos={"esfera": "estadual"},
    ))
    assert convertido["ano_pca"][0]["value"] == 2025
    assert convertido["esfera"]["value"] == "estadual"
    assert "data_vigencia" not in convertido


@pytest.mark.parametrize("question,reference", [
    ("Contratos assinados em 2025", "assinatura"),
    ("Contratações publicadas em 2025", "publicacao"),
    ("Publicações de contratos em 2025", "publicacao"),
])
def test_temporal_reference_recognizes_natural_word_forms(question, reference):
    filtros = qa.analisar_query(question).filtros
    assert filtros.referencia_temporal == reference
    assert filtros.ano == 2025
    assert "data_vigencia" not in qa.filtros_para_retrieve(filtros)


@pytest.mark.parametrize("question,reference,field", [
    ("Quais contratos de 2025?", "assinatura", "data_assinatura"),
    ("Quais contratações de 2025?", "publicacao", "data_publicacao"),
    ("Quais compras em 2025?", "publicacao", "data_publicacao"),
    ("Quanto foi planejado no plano de contratações de 2025?", "pca", "ano_pca"),
])
def test_default_temporal_reference_is_resolved_once_for_domain(question, reference, field):
    filtros = qa.analisar_query(question).filtros

    assert filtros.referencia_temporal == reference
    assert field in qa.filtros_para_retrieve(filtros)
    assert "data_vigencia" not in qa.filtros_para_retrieve(filtros)


def test_explicit_publication_reference_overrides_contract_default():
    filtros = qa.analisar_query("Contratos publicados em 2025").filtros

    assert filtros.referencia_temporal == "publicacao"
    assert "data_publicacao" in qa.filtros_para_retrieve(filtros)
    assert "data_assinatura" not in qa.filtros_para_retrieve(filtros)


@pytest.mark.parametrize("question,role", [
    ("Quanto a Secretaria de Estado de Saúde planejou no plano de contratações de 2025?", "unidade"),
    ("Qual o total planejado pelo órgão Secretaria de Estado de Saúde no PCA 2025?", "orgao"),
    ("Qual o total da unidade responsável Secretaria de Estado de Saúde no PCA 2025?", "unidade"),
])
def test_pca_secretaria_resolve_o_papel_da_entidade(question, role):
    filtros = qa.analisar_query(question).filtros
    retrieve = qa.filtros_para_retrieve(filtros)
    assert filtros.papel_orgao == role
    assert ("unidade_nome" if role == "unidade" else "orgao") in retrieve
    assert ("orgao" if role == "unidade" else "unidade_nome") not in retrieve


def test_temporal_reference_explicitly_marks_yearless_contract_query_as_unchanged():
    filtros = qa.analisar_query("Quais contratos existem?").filtros
    assert filtros.referencia_temporal is None


@pytest.mark.parametrize("question,role", [
    ("CNPJ do fornecedor 05.340.639/0001-30", "fornecedor"),
    ("CNPJ do órgão 29.131.075/0001-93", "orgao"),
    ("CNPJ 29.131.075/0001-93", None),
])
def test_cnpj_only_gets_a_role_when_the_question_names_it(question, role):
    assert qa.analisar_query(question).filtros.papel_cnpj == role


def test_ano_isolado_nao_tem_ano_final():
    filtros = qa.analisar_query("contratos de 2025").filtros
    assert (filtros.ano, filtros.ano_final) == (2025, None)


@pytest.mark.parametrize("pergunta", [
    "contratos acima de 2000 reais",
    "contratos ate R$ 2025",
    "contratos acima de 2025 mil",
    "planos acima de 2020 milhões",
])
def test_valor_monetario_nao_vira_ano(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.ano is None
    assert filtros.valor_min is not None or filtros.valor_max is not None


@pytest.mark.parametrize("pergunta, valor", [
    ("contratos acima de 2000,00 reais", 2000.0),
    ("contratos acima de 2000.00 reais", 2000.0),
    ("contratos acima de 2000,50", 2000.5),
])
def test_valor_com_centavos_nao_vira_ano(pergunta, valor):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.ano is None
    assert filtros.valor_min == pytest.approx(valor)


def test_valor_com_centavos_nao_esconde_o_ano_da_pergunta():
    filtros = qa.analisar_query("contratos acima de 2025,00 em 2024").filtros
    assert filtros.ano == 2024
    assert filtros.valor_min == pytest.approx(2025.0)


def test_ano_no_fim_da_frase_continua_sendo_ano():
    assert qa.analisar_query("Quais contratos foram assinados em 2025.").filtros.ano == 2025


def test_ano_da_pergunta_nao_e_confundido_com_valor_monetario():
    filtros = qa.analisar_query("contratos acima de 2000 reais em 2025").filtros
    assert filtros.ano == 2025
    assert filtros.valor_min == pytest.approx(2000.0)


@pytest.mark.parametrize("pergunta", [
    "O que diz a Lei 14.133/2021 sobre pregão?",
    "Qual o objeto do edital 90005/2025?",
])
def test_ano_de_numero_de_norma_ou_processo_nao_vira_ano(pergunta):
    assert qa.analisar_query(pergunta).filtros.ano is None


def test_ano_de_norma_nao_esconde_o_ano_da_pergunta():
    filtros = qa.analisar_query("contratos de 2025 com base na Lei 14.133/2021").filtros
    assert filtros.ano == 2025


@pytest.mark.parametrize("pergunta", [
    "O que diz a Lei nº 14.133, de 1º de abril de 2021, sobre pregão?",
    "Lei 14.133 de 2021 sobre dispensa",
    "Decreto 42.063/2009",
])
def test_ano_de_citacao_de_norma_nao_vira_ano(pergunta):
    assert qa.analisar_query(pergunta).filtros.intervalo_de_anos() == (None, None)


# --------------------------------------------------------------------------
# Períodos abertos e outras formas de citar anos
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta, intervalo", [
    ("contratos a partir de 2024", (2024, None)),
    ("contratos desde 2023", (2023, None)),
    ("contratos depois de 2023", (2024, None)),
    ("contratos após 2023", (2024, None)),
    ("contratos publicados até 2025", (None, 2025)),
    ("contratos até o ano de 2025", (None, 2025)),
    ("contratos antes de 2024", (None, 2023)),
    ("contratos a partir de 2024 até 2025", (2024, 2025)),
    ("contratos desde 2023 até 2025", (2023, 2025)),
])
def test_periodo_aberto(pergunta, intervalo):
    assert qa.analisar_query(pergunta).filtros.intervalo_de_anos() == intervalo


def test_periodo_aberto_vira_so_um_limite_de_data_vigencia():
    convertido = qa.filtros_para_retrieve(QueryFilters(ano_ate=2023))
    assert convertido["data_vigencia"] == [{"operator": "<", "value": date(2024, 1, 1), "optional": False}]


@pytest.mark.parametrize("pergunta", [
    "compare os contratos em 2024 e 2025",
    "contratos de 2024 ou 2025",
    "contratos de 2024, 2025",
    "contratos de setembro de 2024 a março de 2025",
])
def test_dois_anos_citados_juntos_viram_periodo(pergunta):
    assert qa.analisar_query(pergunta).filtros.intervalo_de_anos() == (2024, 2025)


def test_data_completa_da_o_ano():
    assert qa.analisar_query("contratos assinados em 30/09/2025").filtros.ano == 2025


# --------------------------------------------------------------------------
# Valor seguido de verbo, faixa impossível e plurais
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta, valor", [
    ("Quantos contratos acima de 1 milhão houve por modalidade?", 1_000_000.0),
    ("contratos acima de 5000 foram assinados em 2025?", 5000.0),
    ("contratos acima de 500 mil contratados em 2025", 500_000.0),
    ("valores maiores que 100 mil", 100_000.0),
    ("contratos superiores a 2 milhões", 2_000_000.0),
    ("contratos com valor maior do que 50 mil", 50_000.0),
])
def test_valor_seguido_de_verbo_ou_com_gatilho_no_plural(pergunta, valor):
    assert qa.analisar_query(pergunta).filtros.valor_min == pytest.approx(valor)


def test_minimo_maior_que_maximo_e_um_ou_e_nao_vira_filtro():
    filtros = qa.analisar_query("contratos acima de 1 milhão ou abaixo de 10 mil").filtros
    assert filtros.valor_min is None and filtros.valor_max is None


# --------------------------------------------------------------------------
# texto_sem_filtros: o que sobra da pergunta para o structured_query
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta, esperado", [
    ("Qual a média dos contratos com valor maior que 2000?", "qual a media dos contratos com valor ?"),
    ("Total de ambulâncias acima de R$ 300 mil em 2025", "total de ambulancias em"),
    ("Quantos contratos acima de 1 milhão de reais houve?", "quantos contratos houve?"),
    ("Mediana entre 2024 e 2025", "mediana"),
    ("Contratos a partir de 2024", "contratos"),
])
def test_texto_sem_filtros_tira_so_as_expressoes_que_viraram_filtro(pergunta, esperado):
    assert qa.texto_sem_filtros(pergunta) == esperado


# --------------------------------------------------------------------------
# Vigência das normas (item 7): o início da vigência vem de
# src/corpus_quality.py, só na base normativa.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "O que estava vigente em 2025 sobre pesquisa de preços?",
    "O que estava vigente em 2025 sobre pesquisa de preços nas contratações do Estado do RJ?",
    "Quais regras de dispensa estavam em vigor em 2024?",
    "Qual decreto estadual tinha vigência em 2023?",
])
def test_pergunta_sobre_vigencia_de_norma_filtra_o_inicio_da_vigencia(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.referencia_temporal == "vigencia"
    retrieve = qa.filtros_para_retrieve(filtros)
    fim_do_ano = date(filtros.intervalo_de_anos()[1] + 1, 1, 1)
    assert retrieve["data_vigencia_inicio"] == {"operator": "<", "value": fim_do_ano, "optional": False}
    assert "data_vigencia" not in retrieve


@pytest.mark.parametrize("pergunta", [
    "Quais contratos estavam vigentes em 2025?",
    "Quais atas de registro de preço estavam vigentes em 2025?",
])
def test_contrato_ou_ata_vigente_continua_pela_vigencia_do_registro(pergunta):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.referencia_temporal is None
    retrieve = qa.filtros_para_retrieve(filtros)
    assert "data_vigencia" in retrieve and "data_vigencia_inicio" not in retrieve


def test_vigencia_sem_ano_nao_cria_filtro():
    filtros = qa.analisar_query("Qual lei de licitações está vigente?").filtros
    assert filtros.referencia_temporal == "vigencia"
    assert qa.filtros_para_retrieve(filtros) == {}


# --------------------------------------------------------------------------
# Logging (stage query_analysis em logs/pipeline-AAAA-MM-DD.jsonl)
# --------------------------------------------------------------------------


def test_analisar_query_loga_stage_query_analysis(tmp_path, monkeypatch):
    from src import obs

    logger_de_teste = obs.PipelineLogger(tmp_path)
    monkeypatch.setattr(qa, "default_logger", logger_de_teste)

    qa.analisar_query("contratos acima de 100 mil", run_id="r1", query_id="q1")

    caminho = logger_de_teste.log_path()
    linhas = caminho.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 1
    assert '"stage": "query_analysis"' in linhas[0]
    assert '"run_id": "r1"' in linhas[0]


def _registro_do_log(tmp_path, monkeypatch, pergunta, **kwargs):
    import json
    from src import obs

    logger_de_teste = obs.PipelineLogger(tmp_path)
    monkeypatch.setattr(qa, "default_logger", logger_de_teste)
    qa.analisar_query(pergunta, run_id="r1", query_id="q1", **kwargs)
    [linha] = logger_de_teste.log_path().read_text(encoding="utf-8").strip().splitlines()
    return linha, json.loads(linha)


def test_log_do_modo_regras_grava_os_filtros(tmp_path, monkeypatch):
    _, registro = _registro_do_log(tmp_path, monkeypatch, "contratos acima de 100 mil em 2025")
    assert registro["output"]["valor_min"] == 100000
    assert registro["output"]["ano"] == 2025


def test_log_do_modo_llm_mascara_dado_pessoal_e_nao_repete_a_pergunta(tmp_path, monkeypatch):
    import json
    pergunta = "Contratos do fornecedor de CPF 123.456.789-09"
    raw = {"intencoes": [{"pergunta": pergunta, "bases": ["estruturada"]}]}
    linha, registro = _registro_do_log(
        tmp_path, monkeypatch, pergunta, modo="llm", cliente=lambda _: json.dumps(raw)
    )
    assert registro["output"]["metodo"] == "llm"
    assert "query_original" not in registro["output"]
    assert registro["output"]["intencoes"][0]["pergunta"] == "Contratos do fornecedor de CPF [CPF omitido]"
    assert "123.456.789-09" not in linha


def test_log_do_fallback_nao_grava_dado_pessoal_devolvido_pelo_llm(tmp_path, monkeypatch):
    import json
    # Operação inválida: a mensagem do erro de validação repete o valor recebido.
    raw = {"intencoes": [{"pergunta": "contratos", "bases": ["estruturada"],
                          "operacao": "listar CPF 123.456.789-09"}]}
    linha, registro = _registro_do_log(
        tmp_path, monkeypatch, "contratos de 2025", modo="llm", cliente=lambda _: json.dumps(raw)
    )
    assert registro["extra"]["fallback"].startswith("ValidationError")
    assert "123.456.789-09" not in linha


def test_log_corta_texto_longo_da_saida(tmp_path, monkeypatch):
    import json
    raw = {"intencoes": [{"pergunta": "contratos de limpeza " * 60, "bases": ["estruturada"]}]}
    _, registro = _registro_do_log(
        tmp_path, monkeypatch, "contratos de limpeza", modo="llm", cliente=lambda _: json.dumps(raw)
    )
    subpergunta = registro["output"]["intencoes"][0]["pergunta"]
    assert len(subpergunta) == qa._LIMITE_DE_TEXTO_NO_LOG + 1
    assert subpergunta.endswith("…")

# --------------------------------------------------------------------------
# Regressões achadas em revisão adversarial (30/09). Cada linha é um caso que
# já saiu errado: (pergunta, (primeiro ano, último ano), valor_min, valor_max).
# --------------------------------------------------------------------------

_SEM = (None, None)


@pytest.mark.parametrize("pergunta, anos, vmin, vmax", [
    # multiplicadores abreviados e colados
    ("Quais contratos da Secretaria de Saúde acima de 500k em 2025?", (2025, 2025), 500_000, None),
    ("contratos acima de 1,2mi em 2025", (2025, 2025), 1_200_000, None),
    ("contratos acima de R$ 1 mi", _SEM, 1_000_000, None),
    ("contratos acima de R$ 2 bi", _SEM, 2_000_000_000, None),
    ("contratos acima de 1.500 mil", _SEM, 1_500_000, None),
    ("compras de 100kg em 2025", (2025, 2025), None, None),
    # número seguido de vírgula, percentual, data, parêntese
    ("Liste os contratos acima de 2000, ordenados por valor", _SEM, 2000, None),
    ("contratos com aditivos acima de 25%", _SEM, None, None),
    ("reajuste de até 10% em 2025", (2025, 2025), None, None),
    ("contratos acima de 25 por cento", _SEM, None, None),
    ("contratos até 31/12/2025", (None, 2025), None, None),
    ("contratos a partir de 01/01/2024", (2024, None), None, None),
    ("prazo de até 90 (noventa) dias", _SEM, None, None),
    ("contratos vigentes até 30 de junho de 2025", (None, 2025), None, None),
    # contagem
    ("municípios com mais de 1 milhão de habitantes", _SEM, None, None),
    ("fornecedores que ganharam mais de 3 dos pregões", _SEM, None, None),
    ("mais de 3 por modalidade", _SEM, None, None),
    ("no máximo 2 por empresa", _SEM, None, None),
    ("prefeituras que compraram entre 2 e 5 ambulâncias em 2025", (2025, 2025), None, None),
    ("entre 100 e 200 mil habitantes", _SEM, None, None),
    ("Em 2025, 2000 cestas básicas foram compradas pela Secretaria?", (2025, 2025), None, None),
    ("compra de 2000 cestas básicas em 2025", (2025, 2025), None, None),
    ("contratos com mais de 2000 itens em 2025", (2025, 2025), None, None),
    ("contratos no valor de 2000 em 2025", (2025, 2025), None, None),
    # faixas
    ("contratos entre 500 e 800 mil reais", _SEM, 500_000, 800_000),
    ("contratos entre 1 milhão e 500 mil", _SEM, 500_000, 1_000_000),
    ("contratos de 2000 a 5000 reais em 2025", (2025, 2025), 2000, 5000),
    ("contratos de R$ 100 mil até R$ 500 mil", _SEM, 100_000, 500_000),
    ("Entre 2024 e 2025, quais contratos ficaram entre 100 mil e 500 mil?", (2024, 2025), 100_000, 500_000),
    ("contratos a partir de 2024 até R$ 80 mil", (2024, None), None, 80_000),
    ("contratos de março de 2023 a maio de 2024 até R$ 80 mil", (2023, 2024), None, 80_000),
    ("contratos a partir de 100 mil reais até 2023", (None, 2023), 100_000, None),
    # gatilho repetido: o primeiro não tem valor válido
    ("a partir de 2024, contratos acima de 1 milhão", (2024, None), 1_000_000, None),
    ("fornecedores com mais de 3 contratos acima de R$ 500 mil em 2025", (2025, 2025), 500_000, None),
    # períodos abertos com contração, data, mês e semestre
    ("contrato posterior a 2023", (2024, None), None, None),
    ("período anterior à 2024", (None, 2023), None, None),
    ("depois do ano de 2023", (2024, None), None, None),
    ("anteriores ao ano de 2024", (None, 2023), None, None),
    ("a partir do 2º semestre de 2024", (2024, None), None, None),
    ("antes de 01/01/2024", (None, 2023), None, None),
    ("após 01/06/2024", (2024, None), None, None),
    ("até o dia 30/06/2025", (None, 2025), None, None),
    ("até 1º de janeiro de 2025", (None, 2025), None, None),
    ("antes de março de 2024", (None, 2024), None, None),
    ("após junho de 2024", (2024, None), None, None),
    ("contratos de 2024 em diante", (2024, None), None, None),
    ("desde o início de 2024", (2024, None), None, None),
    ("até o fim de 2025", (None, 2025), None, None),
    ("Quais os maiores contratos antes de 2024 ou depois de 2025?", _SEM, None, None),
    # vários anos
    ("contratos de 2023, 2024 e 2025", (2023, 2025), None, None),
    ("exercício 2024/2025", (2024, 2025), None, None),
    ("contratos 2024 x 2025", (2024, 2025), None, None),
    ("compare 2024 com 2025", (2024, 2025), None, None),
    ("de 2024 para 2025", (2024, 2025), None, None),
    ("contratos de 2023 até 2025 com valor até 100 mil", (2023, 2025), None, 100_000),
    ("contratos de 2025-03-01", (2025, 2025), None, None),
    ("contratos assinados em 03/2025", (2025, 2025), None, None),
    ("Em 2025 milhões de reais foram gastos com ambulâncias?", (2025, 2025), None, None),
    # citações de norma
    ("O que diz a Lei n° 14.133, de 1° de abril de 2021?", _SEM, None, None),
    ("IN SEGES/ME nº 65, de 7 de julho de 2021, sobre pesquisa de preços", _SEM, None, None),
    ("o que diz a nova lei de licitações de 2021 sobre pregão?", _SEM, None, None),
    ("Lei 14.133 (2021) e contratos de 2025", (2025, 2025), None, None),
    ("contratos firmados sob a Lei 14.133/2021 de 2023 a 2025", (2023, 2025), None, None),
])
def test_regressoes_da_revisao_adversarial(pergunta, anos, vmin, vmax):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.intervalo_de_anos() == anos
    assert filtros.valor_min == (pytest.approx(vmin) if vmin is not None else None)
    assert filtros.valor_max == (pytest.approx(vmax) if vmax is not None else None)


def test_pergunta_toda_em_maiusculas_nao_estende_o_orgao():
    # Sem pista de maiúscula, o nome é lido como em minúsculas: para no "ACIMA".
    assert qa.analisar_query("QUAIS CONTRATOS DA SECRETARIA DE SAUDE ACIMA DE 1 MILHAO EM 2025").filtros.orgao == (
        "SECRETARIA DE SAUDE"
    )


def test_texto_sem_filtros_tira_expressoes_de_valor_mesmo_quando_o_ou_descarta_o_filtro():
    assert qa.texto_sem_filtros("contratos acima de 1 milhão ou abaixo de 10 mil") == "contratos ou"

# Terceira rodada adversarial (30/09): (pergunta, (primeiro ano, último ano), valor_min, valor_max).
@pytest.mark.parametrize("pergunta, anos, vmin, vmax", [
    ("dispensas de 2025 ate R$ 50 mil", (2025, 2025), None, 50_000),
    ("compras de 2024 ate 100 mil reais", (2024, 2024), None, 100_000),
    ("quantos contratos pela Lei 8.666 de 2021 a 2023?", (2021, 2023), None, None),
    ("contratos de 2025 sob a nova lei de licitacoes (2021)", (2025, 2025), None, None),
    ("contratos de 2025 conforme a Resolução SEPLAG nº 5 de 2024", (2025, 2025), None, None),
    ("contratos de 2025 conforme Portaria SEFAZ nº 123, de 15 de março de 2024", (2025, 2025), None, None),
    ("empresas enquadradas na LC 123 de 2006 com contratos em 2025", (2025, 2025), None, None),
    ("contratos em 2025 segundo o Decreto Rio nº 51.078 de 2022", (2025, 2025), None, None),
    ("Lei de Acesso à Informação (2011) e contratos de 2025", (2025, 2025), None, None),
    ("contratos de 2025 e a lei do pregao de 2002", (2025, 2025), None, None),
    ("Lei 14.133 (de 2021) e contratos de 2025", (2025, 2025), None, None),
    ("lei 14.133 art 75 de 2021", (None, None), None, None),
    ("a lei geral de licitacoes 8.666 de 1993 e os contratos de 2025", (2025, 2025), None, None),
    ("contratos de 2024 oriundos do pregao eletronico 05/2023", (2024, 2024), None, None),
    ("ata de registro de precos nº 12/2024", (None, None), None, None),
    ("IN SEGES 65/2021 e pesquisa de precos em 2025", (2025, 2025), None, None),
    ("contratos assinados em 2024 com vigencia apos 2025", (2024, 2024), None, None),
    ("contratos de 2025 com vigencia ate 2027", (2025, 2025), None, None),
    ("contratos de 2024 que vencem depois de 2025", (2024, 2024), None, None),
    ("contratos assinados antes do fim de 2024", (None, 2024), None, None),
    ("contratos assinados depois do inicio de 2024", (2024, None), None, None),
    ("contratos antes de 2025-03-01", (None, 2025), None, None),
    ("contratos entre 500 e 1 milhão", (None, None), 500_000, 1_000_000),
    ("contratos entre 800 e 1,2 milhão em 2025", (2025, 2025), 800_000, 1_200_000),
    ("contratos com ano maior que 2023", (2024, None), None, None),
    ("contratos com ano menor ou igual a 2025", (None, 2025), None, None),
    ("contratos com data de publicacao superior a 2023", (2024, None), None, None),
    ("itens do pca com quantidade superior a 1500", (None, None), None, None),
    ("municipios com populacao acima de 100 mil", (None, None), None, None),
    ("quantidade de contratos acima de 1 milhão", (None, None), 1_000_000, None),
    ("escolas com ate 2000 alunos", (None, None), None, None),
    ("kits para ate 2000 pessoas em 2025", (2025, 2025), None, None),
    ("dispensas com valor ate 2000", (None, None), None, 2000),
    ("ate 2000 milhoes destinados a saude", (None, None), None, 2_000_000_000),
    ("contratos acima de 1 milhao e meio", (None, None), 1_500_000, None),
    ("contratos acima de R$ 1 milhão e 500 mil", (None, None), 1_500_000, None),
    ("contratos acima de 2 mil e 500 reais", (None, None), 2500, None),
    ("contratos abaixo de 1 milhão e 200 mil em 2025", (2025, 2025), None, 1_200_000),
    ("compare o valor total de 2024 com o de 2025", (2024, 2025), None, None),
    ("qual o valor total de 2025?", (2025, 2025), None, None),
    ("qual o contrato de maior valor de 2025?", (2025, 2025), None, None),
    ("compra de 2000 a 3000 cestas basicas em 2025", (2025, 2025), None, None),
    ("kits para 2000 ou 3000 pessoas", (None, None), None, None),
    ("2000 ou mais unidades em 2025", (2025, 2025), None, None),
    ("pregoes com mais de 2000 e menos de 5000 itens", (None, None), None, None),
    ("compra de 2000 vacinas em 2021", (2021, 2021), None, None),
    ("entregues em 2000 unidades de saude", (None, None), None, None),
    ("uns 2000 contratos", (None, None), None, None),
    ("contratos com a empresa Rio 2000 Engenharia", (None, None), None, None),
    ("contratos de 2024 pra frente", (2024, None), None, None),
    ("contratos a contar de 2024", (2024, None), None, None),
    ("contratos anteriores ao exercicio de 2024", (None, 2023), None, None),
    ("contratos desde meados de 2023", (2023, None), None, None),
    ("contratos de 2023 ate a presente data", (2023, None), None, None),
    ("contratos de 2023 ou anteriores", (None, 2023), None, None),
    ("contratos de 01/03/2024 ate 28/02/2025", (2024, 2025), None, None),
    ("Qual a média dos contratos com valor igual ou superior a 100 mil?", (None, None), 100_000, None),
    ("acima de 1 milhão da Secretaria de Saúde", (None, None), 1_000_000, None),
    ("acima de R$ 100.000,00 (cem mil reais)", (None, None), 100_000, None),
    ("contratos entre R$ 100 mil a R$ 500 mil em 2025", (2025, 2025), 100_000, 500_000),
    ("os maiores 2025", (2025, 2025), None, None),
    ("contratos do MP 2024", (2024, 2024), None, None),
    ("pca 2025 itens de ti", (2025, 2025), None, None),
    ("contratos dos ultimos 3 anos acima de 1 milhao", (None, None), 1_000_000, None),
    ("contratos que ultrapassam R$ 1 milhão", (None, None), 1_000_000, None),
    ("contratos acima dos 100 mil reais", (None, None), 100_000, None),
    ("contratos em 15.03.2025", (2025, 2025), None, None),
])
def test_regressoes_da_terceira_rodada(pergunta, anos, vmin, vmax):
    filtros = qa.analisar_query(pergunta).filtros
    assert filtros.intervalo_de_anos() == anos
    assert filtros.valor_min == (pytest.approx(vmin) if vmin is not None else None)
    assert filtros.valor_max == (pytest.approx(vmax) if vmax is not None else None)


@pytest.mark.parametrize("pergunta, esperado", [
    ("Qual o total de contratos no exercício de 2025?", "qual o total de contratos ?"),
    ("Quantos contratos no 1º semestre de 2025?", "quantos contratos ?"),
    ("Qual a média dos contratos com valor igual ou superior a 100 mil?", "qual a media dos contratos com valor ?"),
    ("total de contratos em outubro/2025", "total de contratos em"),
])
def test_texto_sem_filtros_tira_expressoes_de_tempo(pergunta, esperado):
    assert qa.texto_sem_filtros(pergunta) == esperado


@pytest.mark.parametrize("pergunta, orgao", [
    ("Qual o total da Secretaria de Saúde no PCA?", "Secretaria de Saúde"),
    ("Qual o total da Secretaria de Saúde do PCA de 2025?", "Secretaria de Saúde"),
    # Prefeitura sai com o nome do órgão no PNCP ("MUNICIPIO DE X"), item 9.
    ("Contratos da Prefeitura de Niterói na Saúde", "MUNICIPIO DE NITEROI"),
    ("Contratos da Prefeitura de Niterói no PNCP em 2025", "MUNICIPIO DE NITEROI"),
    ("Contratos da Prefeitura do Rio no RJ", "MUNICIPIO DE RIO DE JANEIRO"),
    ("Contratos da Prefeitura de Niterói Edital 5/2024", "MUNICIPIO DE NITEROI"),
    ("Contratos Da Prefeitura De Maricá Em 2025", "MUNICIPIO DE MARICA"),
    ("Prefeitura Do Rio No PNCP", "MUNICIPIO DE RIO DE JANEIRO"),
    ("Contratos da Secretaria de Ciência e Tecnologia", "Secretaria de Ciência e Tecnologia"),
    ("Tribunal de Contas do Estado do Rio de Janeiro", "Tribunal de Contas do Estado do Rio de Janeiro"),
])
def test_orgao_termina_antes_de_no_na_em_e_de_siglas_do_sistema(pergunta, orgao):
    assert qa.analisar_query(pergunta).filtros.orgao == orgao

# Órgão sem depender de maiúscula, gatilhos novos, siglas, esfera e modalidade (02/10).
@pytest.mark.parametrize("pergunta, orgao", [
    ("quantos contratos da prefeitura de niterói em 2025?", "MUNICIPIO DE NITEROI"),
    ("contratos da secretaria municipal de saúde de niterói", "secretaria municipal de saúde de niterói"),
    ("fundo municipal de saude de marica", "fundo municipal de saude de marica"),
    ("Comando da Aeronáutica contratou o quê?", "Comando da Aeronáutica"),
    ("defensoria publica do estado contratos acima de 1 milhao", "defensoria publica do estado"),
    ("contratos do municipio de niteroi", "MUNICIPIO DE NITEROI"),
    ("procuradoria geral do estado", "procuradoria geral do estado"),
    ("controladoria geral do estado em 2025", "controladoria geral do estado"),
    ("policia militar do estado do rio de janeiro", "policia militar do estado do rio de janeiro"),
    ("companhia estadual de aguas e esgotos", "companhia estadual de aguas e esgotos"),
    ("secretaria de estado de saude contratos", "secretaria de estado de saude"),
    ("secretaria de ciencia e tecnologia", "secretaria de ciencia e tecnologia"),
    ("secretaria de saude e prefeitura de niteroi", "secretaria de saude"),
    ("prefeitura de niteroi de ambulancias", "MUNICIPIO DE NITEROI"),
    ("Contratos Da Secretaria De Saúde Acima De 1 Milhão", "Secretaria De Saúde"),
    ("contratos da SEEDUC em 2025", "Secretaria de Estado de Educação"),
    ("contratos da ses acima de 1 milhão", "Secretaria de Estado de Saúde"),
    ("DPE contratou o que em 2025?", "Defensoria Pública do Estado"),
    ("contratos da SEOBRAS", None),  # sigla desconhecida e fora do corpus
    ("pesquisa de preços no banco de preços", None),
    ("a empresa ganhou contratos", None),
    ("contratos de TI acima de 1 milhão", None),
    ("Lei 14.133, art. 75, inciso II", None),
    ("contratos da prefeitura de qual cidade?", None),
])
def test_orgao_com_e_sem_maiuscula_e_siglas(pergunta, orgao):
    assert qa.analisar_query(pergunta).filtros.orgao == orgao


def test_sigla_de_orgao_sai_do_texto_sem_filtros():
    assert "seeduc" not in qa.texto_sem_filtros("Qual o total dos contratos da SEEDUC?")


@pytest.mark.parametrize("pergunta, esfera", [
    ("contratos federais em 2025", "federal"),
    ("contratos dos órgãos federais", "federal"),
    ("contratos da secretaria municipal de saúde", "municipal"),
    ("média dos contratos federais e estaduais", None),
    ("o que diz a Lei federal 14.133 sobre pregão?", None),
    ("quantos contratos por município em 2025?", None),
])
def test_esfera_inclui_federal(pergunta, esfera):
    assert qa.analisar_query(pergunta).filtros.filtros_analiticos.get("esfera") == esfera


@pytest.mark.parametrize("pergunta, modalidade", [
    ("quantos pregões eletrônicos em 2025?", "pregao eletronico"),
    ("contratos por dispensa acima de 50 mil", "dispensa"),
    ("inexigibilidades da prefeitura de niteroi", "inexigibilidade"),
    ("quantas concorrências em 2025?", "concorrencia"),
    ("como funciona o pregao eletronico?", None),
    ("MEI pode participar de pregão?", None),
    ("o que diz a lei do pregão de 2002?", None),
])
def test_modalidade_so_em_pergunta_sobre_dados(pergunta, modalidade):
    assert qa.analisar_query(pergunta).filtros.filtros_analiticos.get("modalidade") == modalidade


def test_modalidade_vira_contains_de_cada_palavra_na_busca():
    filtros = qa.analisar_query("quantos pregões eletrônicos em 2025?").filtros
    assert qa.filtros_para_retrieve(filtros)["modalidade"] == [
        {"operator": "contains", "value": "pregao", "optional": False},
        {"operator": "contains", "value": "eletronico", "optional": False},
    ]

# Catálogo de órgãos do corpus e municípios do RJ (02/10, revisão adversarial).
@pytest.mark.parametrize("pergunta, orgao", [
    ("Quantos contratos do Comando da Marinha e informe o valor total", "Comando da Marinha"),
    ("liste os contratos da secretaria de saúde e informe o total", "secretaria de saúde"),
    ("Qual o total da Secretaria de Saúde e da Prefeitura de Niterói?", "Secretaria de Saúde"),
    ("Quantas compras da Prefeitura de Niterói de material de limpeza?", "MUNICIPIO DE NITEROI"),
    ("Quantos contratos da Secretaria Municipal de Saúde de ambulâncias em 2025?", "Secretaria Municipal de Saúde"),
    ("quantos contratos do município de são gonçalo em 2025?", "MUNICIPIO DE SAO GONCALO"),
    ("gastos do municipio de volta redonda em 2024", "MUNICIPIO DE VOLTA REDONDA"),
    ("contratos da prefeitura de campos dos goytacazes", "MUNICIPIO DE CAMPOS DOS GOYTACAZES"),
    ("fundo municipal de saude de marica", "fundo municipal de saude de marica"),
    ("câmara municipal de três rios contratou o quê?", "câmara municipal de três rios"),
    ("prefeitura de petropolis contratos", "MUNICIPIO DE PETROPOLIS"),
    ("Total Gasto pela Prefeitura de São Gonçalo em 2025", "MUNICIPIO DE SAO GONCALO"),
    ("Quanto a Secretaria de Estado de Saúde (SES) planejou gastar no PCA de 2025?", "Secretaria de Estado de Saúde"),
    ("Quantos contratos da Prefeitura do Rio de Janeiro de janeiro a março de 2025", "MUNICIPIO DE RIO DE JANEIRO"),
    ("procuradoria geral do estado", "procuradoria geral do estado"),
    ("Contratos da FIOCRUZ em 2025", "Fundação Oswaldo Cruz"),
    ("contratos do TCE-RJ", "Tribunal de Contas do Estado"),
    ('Quantos contratos da "Prefeitura de Niterói" em 2025?', "MUNICIPIO DE NITEROI"),
    # não é órgão
    ("Qual o total gasto por órgão federal em 2025?", None),
    ("Quantos contratos de empresa pública em 2025?", None),
    ("qual órgão de saúde mais contratou?", None),
    ("aquisição de câmara de conservação de vacinas", None),
    ("contratos com banco de sangue", None),
    ("Qual é a conta bancária e a agência do fornecedor do maior contrato?", None),
    ("banco de sangue de niteroi", None),
    ("Por quanto foi homologado o forro de PVC no pregão 62/2023?", None),
    ("Quantos contratos de EPI em 2025?", None),
    ("Qual o MAIOR contrato de 2025?", None),
    ("Quais contratos NÃO foram por dispensa?", None),
])
def test_orgao_confere_com_catalogo_e_municipios(pergunta, orgao):
    assert qa.analisar_query(pergunta).filtros.orgao == orgao


# Item 9 das pendências até o Demo Day: "Prefeitura de X" vira o nome do órgão
# no PNCP, "MUNICIPIO DE X" ("PREFEITURA MUNICIPAL DE X" é o nome da unidade).
@pytest.mark.parametrize("pergunta, orgao", [
    ("Quais contratos da Prefeitura de Maricá existem?", "MUNICIPIO DE MARICA"),
    ("Quais contratos da Prefeitura Municipal de Duque de Caxias?", "MUNICIPIO DE DUQUE DE CAXIAS"),
    ("Contratos da Prefeitura da Cidade do Rio de Janeiro", "MUNICIPIO DE RIO DE JANEIRO"),
    ("contratos da prefeitura do rio em 2025", "MUNICIPIO DE RIO DE JANEIRO"),
    ("contratos da prefeitura de volta redonda acima de 100 mil", "MUNICIPIO DE VOLTA REDONDA"),
    ("Contratos da Prefeitura de Sumidouro", "MUNICIPIO DE SUMIDOURO"),  # presente no catálogo v7
    ("Contratos da Câmara Municipal de Maricá", "Câmara Municipal de Maricá"),
    ("Contratos da Secretaria Municipal de Saúde de Niterói", "Secretaria Municipal de Saúde de Niterói"),
])
def test_prefeitura_vira_o_nome_do_orgao_no_pncp(pergunta, orgao):
    assert qa.analisar_query(pergunta).filtros.orgao == orgao


def test_filtro_da_prefeitura_casa_com_o_orgao_gravado_no_pncp():
    from src.retrieve import _matches_filters
    filtros = qa.filtros_para_retrieve(qa.analisar_query("Quais contratos da Prefeitura de Maricá existem?").filtros)
    assert _matches_filters({"extras": {"orgao": "MUNICIPIO DE MARICA"}}, filtros)
    assert not _matches_filters({"extras": {"orgao": "MARICA CAMARA MUNICIPAL"}}, filtros)
    assert not _matches_filters({"extras": {"orgao": "MUNICIPIO DE NITEROI"}}, filtros)


@pytest.mark.parametrize("pergunta, esfera", [
    ("quantos contratos no município do Rio de Janeiro em 2025?", None),
    ("contratos de municípios em 2025", "municipal"),
    ("o que diz a legislação federal sobre dispensa?", None),
    ("lei estadual sobre pesquisa de preços", None),
])
def test_esfera_nao_vem_de_lugar_nem_de_norma(pergunta, esfera):
    assert qa.analisar_query(pergunta).filtros.filtros_analiticos.get("esfera") == esfera


def test_municipio_locativo_nao_vira_orgao_prefeitura():
    filters = qa.analisar_query(
        "Qual a mediana do preço unitário homologado no município do Rio de Janeiro?"
    ).filtros
    assert filters.orgao is None
    assert filters.filtros_analiticos.get("municipio") == "Rio de Janeiro"


def test_municipio_administrativo_explicito_continua_sendo_orgao():
    filters = qa.analisar_query("Quanto planejou o Município de Maricá?").filtros
    assert filters.orgao == "MUNICIPIO DE MARICA"


@pytest.mark.parametrize("pergunta", [
    "Compare o valor médio de pregões e dispensas em 2025",
    "Como funciona a dispensa de licitação?",
    "Quais os limites de valor para dispensa?",
    "Quantas fases tem o pregão?",
    "MEI pode participar de pregões eletrônicos?",
    "Quais contratos tiveram pouca concorrência?",
    "quantos dias de dispensa medica o servidor pode ter?",
    "quantos contratos não foram por dispensa em 2025?",
])
def test_modalidade_nao_vira_filtro_em_comparacao_regra_ou_sentido_comum(pergunta):
    assert qa.analisar_query(pergunta).filtros.filtros_analiticos.get("modalidade") is None


def test_plano_llm_duas_intencoes_sem_confundir_contagem_com_valor():
    import json
    from src.router import rotear_pergunta
    raw = {'intencoes': [
        {'pergunta': 'Quais requisitos para microempresa no pregão?', 'bases': ['normativa']},
        {'pergunta': 'Há oportunidades abertas de material de escritório?', 'bases': ['estruturada'],
         'filtros': {'referencia_temporal': 'propostas'}, 'termos_objeto': ['material', 'escritório']},
    ]}
    analise = qa.analisar_query('Sou MEI, posso participar e tem pregão aberto?', modo='llm', cliente=lambda _: json.dumps(raw))
    assert len(analise.intencoes) == 2
    route = rotear_pergunta(analise.query_original, analise=analise)
    assert {b.value for b in route.bases_selecionadas} == {'normativa', 'estruturada'}
    assert analise.intencoes[1].filtros.valor_min is None


def test_publicacao_explicita_completa_referencia_omitida():
    import json
    raw = {'intencoes': [{'pergunta': 'contratos publicados em 2025', 'bases': ['estruturada'],
                         'filtros': {'ano': 2025}}]}
    result = qa.analisar_query('contratos publicados em 2025', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'llm'
    assert result.intencoes[0].filtros.referencia_temporal == 'publicacao'


def test_llm_default_periodo_temporal_nao_pode_conflitar_com_regra_de_dominio():
    import json
    raw = {'intencoes': [{'pergunta': 'mediana dos contratos de 2025', 'bases': ['estruturada'],
                          'operacao': 'estatistica',
                          'filtros': {'ano': 2025, 'referencia_temporal': 'vigencia', 'metrica': 'mediana'}}]}
    result = qa.analisar_query(
        'Qual a mediana dos contratos de 2025?', modo='llm', cliente=lambda _: json.dumps(raw)
    )
    assert result.metodo == 'llm'
    assert result.intencoes[0].filtros.referencia_temporal == 'assinatura'


def test_llm_usa_papel_cnpj_explicitamente_mencionado_se_omitir_o_campo():
    import json
    question = 'Quais contratos do fornecedor de CNPJ 05.340.639/0001-30?'
    raw = {'intencoes': [{'pergunta': question, 'bases': ['estruturada'],
                          'filtros': {'filtros_analiticos': {'cnpj': '05340639000130'}}}]}
    result = qa.analisar_query(question, modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'llm'
    assert result.intencoes[0].filtros.papel_cnpj == 'fornecedor'


def test_llm_usa_unidade_da_secretaria_no_pca_mesmo_se_omitir_o_papel():
    import json
    question = 'Quanto a Secretaria de Estado de Saúde planejou no plano de contratações de 2025?'
    raw = {'intencoes': [{'pergunta': question, 'bases': ['agregada'],
                          'operacao': 'estatistica',
                          'filtros': {'ano': 2025, 'metrica': 'soma', 'referencia_temporal': 'pca'}}]}
    result = qa.analisar_query(question, modo='llm', cliente=lambda _: json.dumps(raw))
    intent = result.intencoes[0]
    assert result.metodo == 'llm'
    assert intent.filtros.orgao == 'Secretaria de Estado de Saúde'
    assert intent.filtros.papel_orgao == 'unidade'


def test_llm_preserva_orgao_quando_pca_o_especifica():
    import json
    question = 'Qual o total planejado pelo órgão Secretaria de Estado de Saúde no PCA de 2025?'
    raw = {'intencoes': [{'pergunta': question, 'bases': ['agregada'],
                          'operacao': 'estatistica',
                          'filtros': {'ano': 2025, 'metrica': 'soma', 'referencia_temporal': 'pca'}}]}
    result = qa.analisar_query(question, modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'llm'
    assert result.intencoes[0].filtros.papel_orgao == 'orgao'


def test_llm_nao_aceita_orgao_inventado():
    import json
    raw = {'intencoes': [{'pergunta': 'Quais contratos existem?', 'bases': ['estruturada'],
                          'filtros': {'orgao': 'SECRETARIA INVENTADA'}}]}
    result = qa.analisar_query(
        'Quais contratos existem?', modo='llm', cliente=lambda _: json.dumps(raw)
    )
    assert result.metodo == 'regras'
    assert result.filtros.orgao is None


@pytest.mark.parametrize('question', [
    'Compare a mediana do valor por veículo entre esferas.',
    'Qual o valor médio por quilo de feijão?',
    'Qual o preço por unidade do item?',
])
def test_preco_por_unidade_nao_e_valor_global(question):
    filters = qa.analisar_query(question, modo='regras').filtros
    assert filters.tipo_valor == 'unitario'
    assert filters.tipo_medida == 'unitaria' and filters.granularidade == 'item'


def test_preco_unitario_homologado_preserva_etapa_do_valor_e_grao():
    filters = qa.analisar_query(
        'Qual a mediana do preço unitário homologado de serviço de engenharia?', modo='regras'
    ).filtros
    assert filters.tipo_valor == 'homologado'
    assert filters.tipo_medida == 'unitaria'
    assert filters.granularidade == 'item'
    assert filters.metrica == 'mediana'
    assert filters.referencia_temporal == 'resultado'


def test_limites_monetarios_exclusivos_tambem_no_modo_regras():
    lower = qa.analisar_query('contratos acima de R$ 500 mil', modo='regras').filtros
    upper = qa.analisar_query('contratos abaixo de R$ 500 mil', modo='regras').filtros
    assert lower.valor_min == 500000 and lower.valor_min_exclusivo
    assert upper.valor_max == 500000 and upper.valor_max_exclusivo


def test_operacao_analitica_inventada_usa_fallback():
    import json
    raw = {'intencoes': [{'pergunta': 'liste contratos', 'bases': ['estruturada'],
                         'operacao': 'agrupamento', 'filtros': {'metrica': 'contagem', 'agrupamentos': ['orgao_nome']}}]}
    result = qa.analisar_query('Liste contratos publicados por órgãos públicos em 2025', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'regras'


def test_cache_fallback_invalido_e_temporario(monkeypatch):
    from src import generate
    calls = []
    def fake(messages):
        calls.append(messages)
        return '{"intencoes": [{"pergunta": "contratos", "bases": ["inexistente"]}]}'
    monkeypatch.setattr(generate, '_cliente_padrao', lambda schema=None: fake)
    now = qa.time.time()
    monkeypatch.setattr(qa.time, 'time', lambda: now)
    first = qa.analisar_query('contratos de 2025', modo='llm')
    second = qa.analisar_query('contratos de 2025', modo='llm')
    assert first.metodo == second.metodo == 'regras' and len(calls) == 1
    monkeypatch.setattr(qa.time, 'time', lambda: now + 301)
    qa.analisar_query('contratos de 2025', modo='llm')
    assert len(calls) == 2


def test_plano_analitico_sem_metrica_usa_fallback():
    import json
    raw = {'intencoes': [{'pergunta': 'maior total por fornecedor', 'bases': ['estruturada'],
                         'operacao': 'agrupamento', 'filtros': {'agrupamentos': ['fornecedor']}}]}
    result = qa.analisar_query('maior valor total por fornecedor', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'regras'
    assert result.filtros.metrica == 'soma'


def test_filtro_modalidade_inventado_usa_fallback():
    import json
    raw = {'intencoes': [{'pergunta': 'contratos de tecnologia', 'bases': ['estruturada'],
                         'filtros': {'filtros_analiticos': {'modalidade': 'concorrência'}}}]}
    result = qa.analisar_query('contratos de tecnologia', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'regras'
    assert 'modalidade' not in result.filtros.filtros_analiticos


def test_plano_llm_invalido_retorna_regras():
    analise = qa.analisar_query('contratos acima de 500 mil em 2025', modo='llm', cliente=lambda _: '{"intencoes":[{"pergunta":"x","bases":["emails"],"operacao":"DROP"}]}')
    assert analise.metodo == 'regras'
    assert analise.filtros.valor_min == 500000


def test_cache_analise_e_invalidacao_prompt(monkeypatch):
    import json
    from src import query_analysis, generate
    chamadas = []
    def fake(mensagens):
        chamadas.append(mensagens)
        return json.dumps({'intencoes': [{'pergunta': 'maiores contratos', 'bases': ['estruturada'], 'operacao': 'ranking'}]})
    monkeypatch.setattr(generate, '_cliente_padrao', lambda schema=None: fake)
    qa.analisar_query('maiores contratos', modo='llm')
    qa.analisar_query('maiores contratos', modo='llm')
    assert len(chamadas) == 1
    monkeypatch.setattr(query_analysis, 'PROMPT_VERSION', 'teste-novo')
    qa.analisar_query('maiores contratos', modo='llm')
    assert len(chamadas) == 2



def test_normaliza_campos_conhecidos_sem_aceitar_filtro_desconhecido():
    import json
    raw = {'intencoes': [{'pergunta': 'contratos', 'bases': ['estruturada'], 'filtros': {'uf': 'RJ', 'termos_objeto': ['software']}}]}
    result = qa.analisar_query('contratos', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'llm'
    assert result.intencoes[0].filtros.filtros_analiticos == {'uf': 'RJ'}
    assert result.intencoes[0].termos_objeto == ['software']
    raw['intencoes'][0]['filtros']['senha'] = 'não permitido'
    result = qa.analisar_query('contratos', modo='llm', cliente=lambda _: json.dumps(raw))
    assert result.metodo == 'regras'


# --------------------------------------------------------------------------
# CNPJ citado na pergunta
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pergunta", [
    "Quais contratos tem o fornecedor de CNPJ 05.340.639/0001-30?",
    "Quais contratos tem o fornecedor de CNPJ 05340639000130?",
    "Contratos do cnpj nº 05.340.639/0001-30",
])
def test_cnpj_com_ou_sem_pontuacao_vira_o_mesmo_filtro(pergunta):
    filtros = qa.analisar_query(pergunta).filtros

    assert filtros.filtros_analiticos == {"cnpj": "05340639000130"}


def test_cnpj_sai_do_texto_que_descreve_o_objeto():
    resto = qa.texto_sem_filtros("Quais contratos tem o fornecedor de CNPJ 05.340.639/0001-30?")

    assert "cnpj" not in resto and "05" not in resto


def test_numero_de_controle_pncp_nao_e_lido_como_cnpj():
    filtros = qa.analisar_query("Qual o órgão do contrato 00394429000100-2-001108/2024?").filtros

    assert "cnpj" not in filtros.filtros_analiticos


def test_cnpj_convive_com_ano_e_valor():
    filtros = qa.analisar_query("Contratos do CNPJ 29131075000193 em 2025 acima de 100 mil").filtros

    assert filtros.filtros_analiticos == {"cnpj": "29131075000193"}
    assert filtros.ano == 2025 and filtros.valor_min == 100_000


def test_dois_cnpjs_sao_comparacao_e_nao_filtram():
    filtros = qa.analisar_query("Compare os CNPJs 05.340.639/0001-30 e 44.292.391/0001-30").filtros

    assert "cnpj" not in filtros.filtros_analiticos
# Município e fornecedor canônicos (catálogo do snapshot)
# --------------------------------------------------------------------------


def test_municipio_vira_filtro_canonico():
    filtros = qa.analisar_query("Contratos de Volta Redonda", modo="regras").filtros
    assert filtros.filtros_analiticos.get("municipio") == "Volta Redonda"


def test_fornecedor_por_nome_parcial_com_gatilho():
    filtros = qa.analisar_query("Contratos com a empresa Cristália", modo="regras").filtros
    assert filtros.filtros_analiticos.get("fornecedor") == (
        "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS LTDA"
    )


def test_fornecedor_por_nome_de_duas_palavras_sem_gatilho():
    filtros = qa.analisar_query(
        "Contratos com a Cristalia Produtos Quimicos", modo="regras"
    ).filtros
    assert filtros.filtros_analiticos.get("fornecedor") == (
        "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS LTDA"
    )


def test_prefeitura_fica_no_orgao_e_nao_repete_o_municipio():
    """8 da prefeitura não são os 23 de todos os órgãos de Maricá."""
    filtros = qa.analisar_query("Quantos contratos da Prefeitura de Maricá?", modo="regras").filtros
    # O órgão sai com o nome do PNCP (correção da prefeitura → MUNICIPIO DE X).
    assert filtros.orgao == "MUNICIPIO DE MARICA"
    assert "municipio" not in filtros.filtros_analiticos


def test_municipio_com_gatilho_tambem_nao_vira_fornecedor():
    filtros = qa.analisar_query("Contratos do Município de Maricá", modo="regras").filtros
    assert filtros.filtros_analiticos == {}


def test_nome_ambiguo_nao_escolhe_o_primeiro():
    """"Rio" casa mais de um município: nenhum filtro, em vez do primeiro."""
    filtros = qa.analisar_query("Contratos do Rio", modo="regras").filtros
    assert "municipio" not in filtros.filtros_analiticos


@pytest.mark.parametrize("pergunta", [
    "Quantas contratações por municípios?",
    "Quais modalidades apareceram com mais frequência?",
    "O que estabelece a Lei 14.133/2021?",
])
def test_palavra_comum_nao_vira_fornecedor(pergunta):
    filtros = qa.analisar_query(pergunta, modo="regras").filtros
    assert "fornecedor" not in filtros.filtros_analiticos


def test_valor_do_catalogo_exige_todos_os_tokens():
    opcoes = ["Rio de Janeiro", "Rio das Ostras", "Cabo Frio"]
    assert qa._valor_do_catalogo(opcoes, ["cabo", "frio"]) == "Cabo Frio"
    assert qa._valor_do_catalogo(opcoes, ["rio"]) is None        # ambíguo
    assert qa._valor_do_catalogo(opcoes, ["rio", "ostras"]) == "Rio das Ostras"
    assert qa._valor_do_catalogo(opcoes, ["das"]) is None       # palavra vazia não é token
    assert qa._valor_do_catalogo(opcoes, []) is None


def test_trecho_do_municipio_sai_dos_termos_de_objeto():
    """O nome reconhecido não pode reaproveitar como objeto do contrato."""
    _filtros, _texto, trechos = qa._analisar_com_trechos("Contratos de Volta Redonda")
    assert trechos, "o município reconhecido precisa registrar o trecho"
    inicio, fim = trechos[-1]
    assert "volta redonda" in qa._normalize("Contratos de Volta Redonda")[inicio:fim]


# --------------------------------------------------------------------------
# Estado x cidade: "Rio de Janeiro" é o nome dos dois
# --------------------------------------------------------------------------


def test_orgaos_publicos_do_rio_de_janeiro_nao_filtra_pela_cidade():
    """"Órgãos públicos do Rio de Janeiro" é recorte estadual, não a capital."""
    filtros = qa.analisar_query(
        "Quais contratações de ambulâncias tipo D foram publicadas por órgãos públicos do Rio de Janeiro em 2025?",
        modo="regras").filtros
    assert "municipio" not in filtros.filtros_analiticos


def test_orgaos_estaduais_do_rio_de_janeiro_e_escopo_estadual():
    filtros = qa.analisar_query(
        "órgãos estaduais do Rio de Janeiro pagaram mais por ambulâncias do que os municípios fluminenses?",
        modo="regras").filtros
    assert "municipio" not in filtros.filtros_analiticos


def test_marcador_de_cidade_fecha_o_recorte_na_capital():
    filtros = qa.analisar_query("Contratos da cidade do Rio de Janeiro", modo="regras").filtros
    assert filtros.filtros_analiticos.get("municipio") == "Rio de Janeiro"


def test_nome_sem_marcador_nao_estreita_o_recorte():
    """"Contratos do Rio de Janeiro" é ambíguo: não se escolhe a cidade em silêncio."""
    filtros = qa.analisar_query("Contratos do Rio de Janeiro", modo="regras").filtros
    assert "municipio" not in filtros.filtros_analiticos


def test_outros_municipios_continuam_sendo_municipio():
    filtros = qa.analisar_query("Contratos de Volta Redonda", modo="regras").filtros
    assert filtros.filtros_analiticos.get("municipio") == "Volta Redonda"


def test_municipio_uf_na_pergunta_nao_vira_fornecedor():
    from src.query_analysis import _analisar

    pergunta = ("Qual órgão, município/UF, data de assinatura, início e fim de vigência e objeto da ata "
                "de registro de preços com identificador 28741080000155-1-000001/2023-000001?")

    assert "fornecedor" not in _analisar(pergunta).filtros_analiticos


@pytest.fixture
def palavras_dos_objetos(monkeypatch):
    """Estatística fixa: (objetos que citam a palavra, quantos são do próprio fornecedor)."""
    tabela = {"cristalia": (79, 73), "merenda": (153, 0), "limpeza": (2031, 91)}
    from src.analysis import entities  # quem lê a estatística é o módulo de entidades
    monkeypatch.setattr(entities, "_palavras_dos_objetos", lambda: tabela)


def _todas(texto):
    import re
    return [(qa._normalize(m.group()), m.start(), m.end()) for m in re.finditer(r"\w+", texto)]


@pytest.mark.parametrize("texto,palavra,aceita", [
    ("quais contratos da medshore", "medshore", True),      # não aparece em objeto
    ("quanto a cristalia recebeu", "cristalia", True),      # 92% dos objetos são dela
    ("quais contratos da merenda escolar", "merenda", False),
    ("quais contratos da limpeza urbana", "limpeza", False),
    ("contratos de medshore", "medshore", False),           # sem artigo antes
])
def test_nome_curto_de_fornecedor(palavras_dos_objetos, texto, palavra, aceita):
    todas = _todas(texto)
    comeca = next(i for p, i, _ in todas if p == palavra)

    assert qa._nome_curto_de_fornecedor(palavra, todas, comeca) is aceita


def test_nome_curto_sem_a_base_nao_vira_fornecedor(monkeypatch):
    from src.analysis import entities
    monkeypatch.setattr(entities, "_palavras_dos_objetos", lambda: None)
    todas = _todas("quais contratos da medshore")

    assert qa._nome_curto_de_fornecedor("medshore", todas, todas[-1][1]) is False


@pytest.mark.parametrize("pergunta,fornecedor", [
    ("Quais contratos a Removip assinou?", "REMOVIP SERVICO MEDICO LTDA"),
    ("Quanto a Fiotec recebeu?", "FIOTEC - FUNDACAO PARA O DESENVOLVIMENTO CIENTIFICO E TECNOLOGICO EM SAUDE"),
    ("Quais contratos da Dataprev?", "EMPRESA DE TECNOLOGIA E INFORMACOES DA PREVIDENCIA S.A. - DATAPREV"),
    # Trecho comum às grafias da v7 ("S A"/"SA", "TELEGRAFOS"/"TELÉGRAFOS").
    ("Quanto a Light recebeu em 2025?", "LIGHT SERVICOS DE ELETRICIDADE S"),
    ("Quantos contratos os Correios têm?", "EMPRESA BRASILEIRA DE CORREIOS E TEL"),
])
def test_apelido_de_fornecedor_vira_a_razao_social(pergunta, fornecedor):
    assert qa._analisar(pergunta).filtros_analiticos.get("fornecedor") == fornecedor


def test_light_minusculo_e_palavra_comum_nao_fornecedor():
    assert "fornecedor" not in qa._analisar("Contratos de iluminação com lâmpadas light").filtros_analiticos


def test_cedae_continua_como_orgao_e_nao_fornecedor():
    # Também é órgão do catálogo: com apelido, os dois filtros juntos zeravam a resposta.
    filtros = qa._analisar("Qual o valor total dos contratos da CEDAE?")
    assert "fornecedor" not in filtros.filtros_analiticos and filtros.orgao


@pytest.mark.parametrize("pergunta,metrica", [
    ("Quanto a Fiotec recebeu em 2025?", "soma"),
    ("Quantos contratos de ambulância foram assinados?", "contagem"),
])
def test_quanto_pede_valor_e_quantos_pede_contagem(pergunta, metrica):
    assert qa._analisar(pergunta).metrica == metrica

