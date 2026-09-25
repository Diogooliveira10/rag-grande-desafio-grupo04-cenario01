
from __future__ import annotations

from datetime import date

from src.retrieve import _matches_filters


METADATA_BASE = {
    "chunk_id": "c1",
    "extras": {"orgao": "Secretaria de Estado de Saude do Rio de Janeiro", "valor": "250000.00"},
    "data_vigencia": "2023-06-15",
}


# --------------------------------------------------------------------------
# Regressão: formatos já suportados antes da extensão
# --------------------------------------------------------------------------


def test_valor_direto_e_igualdade():
    assert _matches_filters({"base_id": "x"}, {"base_id": "x"}) is True
    assert _matches_filters({"base_id": "x"}, {"base_id": "y"}) is False


def test_dict_com_operator_unico():
    metadata = {"extras": {"valor": "300000"}}
    assert _matches_filters(metadata, {"valor": {"operator": ">=", "value": 100000}}) is True
    assert _matches_filters(metadata, {"valor": {"operator": "<", "value": 100000}}) is False


def test_tupla_operator_valor_unico():
    metadata = {"extras": {"valor": "300000"}}
    assert _matches_filters(metadata, {"valor": (">=", 100000)}) is True
    assert _matches_filters(metadata, {"valor": ("<", 100000)}) is False


def test_sem_filtros_sempre_passa():
    assert _matches_filters({"qualquer": "coisa"}, None) is True
    assert _matches_filters({"qualquer": "coisa"}, {}) is True


def test_chave_ausente_no_metadado_reprova():
    assert _matches_filters({"extras": {}}, {"orgao": "Secretaria"}) is False


# --------------------------------------------------------------------------
# Novo: lista de dicts = múltiplas condições AND na mesma chave
# --------------------------------------------------------------------------


def test_faixa_de_valor_dentro_do_intervalo():
    filtros = {"valor": [{"operator": ">=", "value": 100000.0}, {"operator": "<=", "value": 500000.0}]}
    assert _matches_filters(METADATA_BASE, filtros) is True


def test_faixa_de_valor_abaixo_do_minimo():
    filtros = {"valor": [{"operator": ">=", "value": 300000.0}, {"operator": "<=", "value": 500000.0}]}
    assert _matches_filters(METADATA_BASE, filtros) is False


def test_faixa_de_valor_acima_do_maximo():
    filtros = {"valor": [{"operator": ">=", "value": 100000.0}, {"operator": "<=", "value": 200000.0}]}
    assert _matches_filters(METADATA_BASE, filtros) is False


def test_faixa_de_data_dentro_do_ano():
    filtros = {
        "data_vigencia": [
            {"operator": ">=", "value": date(2023, 1, 1)},
            {"operator": "<", "value": date(2024, 1, 1)},
        ]
    }
    assert _matches_filters(METADATA_BASE, filtros) is True


def test_faixa_de_data_fora_do_ano():
    filtros = {
        "data_vigencia": [
            {"operator": ">=", "value": date(2024, 1, 1)},
            {"operator": "<", "value": date(2025, 1, 1)},
        ]
    }
    assert _matches_filters(METADATA_BASE, filtros) is False


def test_contains_para_orgao_aproximado():
    filtros = {"orgao": {"operator": "contains", "value": "Secretaria de Estado de Saude"}}
    assert _matches_filters(METADATA_BASE, filtros) is True
    filtros_sem_match = {"orgao": {"operator": "contains", "value": "Secretaria de Educacao"}}
    assert _matches_filters(METADATA_BASE, filtros_sem_match) is False


def test_combina_faixa_de_valor_e_orgao():
    filtros = {
        "orgao": {"operator": "contains", "value": "Saude"},
        "valor": [{"operator": ">=", "value": 100000.0}, {"operator": "<=", "value": 500000.0}],
    }
    assert _matches_filters(METADATA_BASE, filtros) is True