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
    assert convertido["orgao"] == {"operator": "contains", "value": "Secretaria de Saude"}


def test_filtros_para_retrieve_valor_minimo_isolado():
    filtros = QueryFilters(valor_min=100_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": ">=", "value": 100_000.0}


def test_filtros_para_retrieve_valor_maximo_isolado():
    filtros = QueryFilters(valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == {"operator": "<=", "value": 500_000.0}


def test_filtros_para_retrieve_faixa_de_valor_vira_lista_de_condicoes():
    filtros = QueryFilters(valor_min=100_000.0, valor_max=500_000.0)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["valor"] == [
        {"operator": ">=", "value": 100_000.0},
        {"operator": "<=", "value": 500_000.0},
    ]


def test_filtros_para_retrieve_ano_vira_faixa_de_data_vigencia():
    filtros = QueryFilters(ano=2023)
    convertido = qa.filtros_para_retrieve(filtros)
    assert convertido["data_vigencia"] == [
        {"operator": ">=", "value": date(2023, 1, 1)},
        {"operator": "<", "value": date(2024, 1, 1)},
    ]


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