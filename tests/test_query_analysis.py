from __future__ import annotations

from datetime import date

import pytest

from src import query_analysis as qa
from src.schema import QueryFilters


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
    assert resultado.filtros.orgao == "Prefeitura de Niteroi"


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


def test_filtros_para_retrieve_orgao_usa_contains():
    filtros = QueryFilters(orgao="Secretaria de Saude")
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["orgao"] == {"operator": "contains", "value": "Secretaria de Saude", "optional": True}


def test_filtros_para_retrieve_valor_minimo_isolado():
    filtros = QueryFilters(valor_min=100_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": ">=", "value": 100_000.0, "optional": True}


def test_filtros_para_retrieve_valor_maximo_isolado():
    filtros = QueryFilters(valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": "<=", "value": 500_000.0, "optional": True}


def test_filtros_para_retrieve_faixa_de_valor_vira_lista_de_condicoes():
    filtros = QueryFilters(valor_min=100_000.0, valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == [
        {"operator": ">=", "value": 100_000.0, "optional": True},
        {"operator": "<=", "value": 500_000.0, "optional": True},
    ]


def test_filtros_para_retrieve_ano_vira_faixa_de_data_vigencia():
    filtros = QueryFilters(ano=2023)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["data_vigencia"] == [
        {"operator": ">=", "value": date(2023, 1, 1), "optional": True},
        {"operator": "<", "value": date(2024, 1, 1), "optional": True},
    ]


def test_filtro_opcional_mantem_chunk_sem_o_campo_e_descarta_o_de_fora():
    """Caso real do PCA: o indicador com a resposta não tem data_vigencia."""
    from src.retrieve import _matches_filters

    filtros = qa.filtros_para_retrieve(QueryFilters(ano=2025))

    assert _matches_filters({"base_id": "pca_agregado"}, filtros)
    assert _matches_filters({"data_vigencia": "2025-06-30"}, filtros)
    assert not _matches_filters({"data_vigencia": "2023-06-30"}, filtros)


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
        {"operator": ">=", "value": date(2024, 1, 1), "optional": True},
        {"operator": "<", "value": date(2026, 1, 1), "optional": True},
    ]


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