"""Perfil do corpus estruturado (src/analyze_structured.py), que não tinha teste.

Fixa o que o relatório conta antes da divisão de ``analyze`` em funções
(2026-10-27): identidade pelo controle PNCP, erros de leitura, datas, valores
e o vínculo com a base agregada.
"""

from __future__ import annotations

import json

from src.analyze_structured import analyze


def _contrato(controle, **campos):
    return {
        "numeroControlePNCP": controle,
        "numeroControlePncpCompra": "11111111000111-1-000001/2025",
        "dataAssinatura": "2025-03-04",
        "dataPublicacaoPncp": "2024-12-20",
        "anoContrato": 2025,
        "objetoContrato": "Aquisição de arroz tipo 1",
        "orgaoEntidade": {"cnpj": "11111111000111", "razaoSocial": "Prefeitura de Maricá", "esferaId": "M"},
        "unidadeOrgao": {"codigoUnidade": "1", "municipioNome": "Maricá", "ufSigla": "RJ"},
        "niFornecedor": "99999999000199",
        "modalidadeNome": "Pregão",
        "tipoContrato": "Contrato",
        "valorGlobal": "1.234,56",
        **campos,
    }


def _registro(caminho, **campos):
    return {"base_id": "contratos_estruturado", "resource_kind": "contrato", "status": "ok",
            "logical_id": caminho, "source_file": caminho, **campos}


def test_relatorio_conta_contratos_unicos_erros_datas_valores_e_vinculo(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps([
        _contrato("11111111000111-2-000001/2025"),
        _contrato("11111111000111-2-000001/2025"),  # repetido: fica fora das métricas
        _contrato("11111111000111-2-000002/2025", dataAssinatura="2024-06-01", anoContrato=2025,
                  valorGlobal=None, objetoContrato="Locação de ambulância", tipoContrato="Nota de Empenho"),
    ]), encoding="utf-8")
    (tmp_path / "quebrado.json").write_text("{", encoding="utf-8")
    inventario = {
        "version": "v3",
        "por_base": [{"base_id": "contratos_estruturado", "status": "ok", "arquivos": 3}],
        "arquivos": [
            _registro("a.json"),
            _registro("quebrado.json"),
            _registro("ausente.json"),
            _registro("falhou.json", status="erro"),
            {"base_id": "pca_agregado", "resource_kind": "itens", "status": "ok", "artifact_id": "itens-1",
             "source_ref": {"key": {"numeroControlePNCP": "11111111000111-2-000001/2025",
                                    "numeroControlePNCPCompra": "11111111000111-1-000001/2025"}}},
        ],
    }
    (tmp_path / "inv.json").write_text(json.dumps(inventario), encoding="utf-8")

    relatorio = analyze(tmp_path / "inv.json", tmp_path, top=5)

    assert relatorio["population"] == {
        "contract_records_unique": 2, "signed_in_2025": 1, "distinct_contract_ids": 2, "distinct_purchase_ids": 1,
        "contract_ids_missing": 0, "duplicate_rows_omitted": 1, "empenho_like_records": 1,
    }
    assert relatorio["source_read_errors"] == {
        "arquivo_ausente": 1, "duplicatas_descartadas_para_metricas": 1, "json_ilegivel_ou_invalido": 1, "registro_nao_ok": 1,
    }
    datas = relatorio["date_coverage"]
    assert datas["signature_years"] == {"2024": 1, "2025": 1}
    assert datas["contract_year_differs_from_signature_year"] == 1
    assert datas["signed_2025_but_published_before_2025"] == 1
    assert relatorio["field_coverage"]["valorGlobal"] == {"preenchidos": 1, "total": 2}
    assert relatorio["contract_values"]["valorGlobal"]["soma"] == "1234.56"
    assert relatorio["contract_values"]["valorGlobal"]["ausentes"] == 1
    assert relatorio["object_keyword_hits_non_exclusive"] == {"arroz": 1, "ambulancia": 1, "tecnologia": 0, "manutencao": 0}
    assert relatorio["contract_value_by"]["signature_month"] == [
        {"mes_assinatura": "2024-06", "contratos": 1, "valor_global": {
            "n": 0, "ausentes": 1, "zeros": 0, "negativos": 0, "soma": None, "media": None, "mediana": None, "min": None, "max": None}},
        {"mes_assinatura": "2025-03", "contratos": 1, "valor_global": {
            "n": 1, "ausentes": 0, "zeros": 0, "negativos": 0, "soma": "1234.56", "media": "1234.56",
            "mediana": "1234.56", "min": "1234.56", "max": "1234.56"}},
    ]
    assert relatorio["geographic_and_administrative_coverage"]["contracts_located_in_rj"] == 2
    vinculo = relatorio["aggregate_link_coverage_in_current_inventory"]
    assert vinculo["compras_vinculadas_com_itens_na_v3"] == 1
    assert vinculo["artefatos_agregados_por_tipo"] == {"itens": {"artefatos": 1, "ok": 1}}
