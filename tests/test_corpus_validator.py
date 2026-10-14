from __future__ import annotations

import copy
import json
from pathlib import Path

from src.corpus_validator import (
    OFFICIAL_BASE_TARGETS,
    validate_inventory_contract,
    validate_manifest_contract,
)
from src.fetch_corpus import load_manifest

ROOT = Path(__file__).resolve().parents[1]


def _manifesto_oficial() -> dict:
    return {
        "bases": [
            {
                "base_id": base_id,
                "escopo": "oficial",
                "natureza": "estruturada",
                "volume_alvo": alvo,
                "selecao_deterministica": True,
            }
            for base_id, alvo in OFFICIAL_BASE_TARGETS.items()
        ]
        + [{"base_id": "exemplos_teste", "escopo": "experimental", "natureza": "normativa"}]
    }


def _inventario_completo(manifesto: dict) -> dict:
    return {
        "completo": True,
        "falhas": [],
        "por_base": [
            {"base_id": base["base_id"], "status": "complete", "volume_obtido": base["volume_alvo"]}
            for base in manifesto["bases"]
            if base.get("escopo") == "oficial"
        ],
    }


def test_curadoria_rejeita_soma_diferente_do_orcamento_de_arquivos():
    manifesto = _manifesto_oficial()
    manifesto["curadoria_v6"] = {"alvo_arquivos_total": 1400, "limite_por_orgao_por_base": 5}
    result = validate_manifest_contract(manifesto)
    assert not result.ok
    assert any("soma dos volumes" in error for error in result.errors)


def test_manifesto_oficial_valido_passa_e_ignora_bases_experimentais():
    assert validate_manifest_contract(_manifesto_oficial()).ok


def test_suplemento_sintetico_nao_altera_orcamento_v6_mas_exige_inventario():
    manifesto = load_manifest(ROOT / "corpus_manifest.yaml")
    assert validate_manifest_contract(manifesto).ok
    inventario = _inventario_completo(manifesto)
    assert validate_inventory_contract(manifesto, inventario).ok
    inventario["por_base"] = [base for base in inventario["por_base"] if base["base_id"] != "emails_sinteticos"]
    assert "inventário sem base(s): emails_sinteticos" in validate_inventory_contract(manifesto, inventario).errors


def test_suplemento_oficial_exige_rotulo_sintetico_e_fonte_local():
    manifesto = load_manifest(ROOT / "corpus_manifest.yaml")
    suplemento = next(base for base in manifesto["bases"] if base["base_id"] == "emails_sinteticos")
    suplemento["dados_sinteticos"] = False
    assert "emails_sinteticos: suplemento sintético local inválido" in validate_manifest_contract(manifesto).errors


def test_manifesto_sem_bases_e_rejeitado():
    resultado = validate_manifest_contract({"bases": []})

    assert resultado.errors == ("manifesto sem bases declaradas",)


def test_manifesto_oficial_aponta_base_faltando_e_volume_divergente():
    manifesto = _manifesto_oficial()
    manifesto["bases"] = [base for base in manifesto["bases"] if base["base_id"] != "pca_agregado"]
    manifesto["bases"][0]["volume_alvo"] = 5

    erros = validate_manifest_contract(manifesto).errors

    assert any("bases oficiais divergentes" in erro for erro in erros)
    assert any(f"contratos_estruturado: volume_alvo esperado={OFFICIAL_BASE_TARGETS['contratos_estruturado']} obtido=5" in erro for erro in erros)


def test_manifesto_exige_selecao_deterministica_natureza_e_volume_valido():
    manifesto = {"bases": [{"base_id": "b", "volume_alvo": "muitos"}]}

    erros = validate_manifest_contract(manifesto, official=False).errors

    assert "b: selecao_deterministica não está habilitada" in erros
    assert "b: natureza ausente" in erros
    assert "b: volume_alvo inválido" in erros


def test_inventario_completo_passa():
    manifesto = _manifesto_oficial()

    assert validate_inventory_contract(manifesto, _inventario_completo(manifesto)).ok


def test_inventario_incompleto_com_falha_base_ausente_e_volume_errado():
    manifesto = _manifesto_oficial()
    inventario = _inventario_completo(manifesto)
    inventario["completo"] = False
    inventario["falhas"] = [{"base_id": "atas_conversacional"}]
    inventario["por_base"] = [b for b in inventario["por_base"] if b["base_id"] != "pca_agregado"]
    inventario["por_base"][0].update(status="partial", volume_obtido=150)

    erros = validate_inventory_contract(manifesto, inventario).errors

    assert "inventário marcado como incompleto" in erros
    assert "inventário contém 1 falha(s)" in erros
    assert "inventário sem base(s): pca_agregado" in erros
    assert "contratos_estruturado: status='partial'" in erros
    assert f"contratos_estruturado: volume esperado={OFFICIAL_BASE_TARGETS['contratos_estruturado']} obtido=150" in erros


def test_inventario_aceita_itens_quando_nao_ha_volume_obtido():
    manifesto = _manifesto_oficial()
    inventario = _inventario_completo(manifesto)
    for base in inventario["por_base"]:
        base["itens"] = base.pop("volume_obtido")

    assert validate_inventory_contract(manifesto, copy.deepcopy(inventario)).ok


def test_manifesto_e_inventario_v5_do_repositorio_continuam_validos():
    manifesto = load_manifest(ROOT / "corpus_manifest.yaml")
    # A v5 conserva seu contrato de volumes, sem suplementos posteriores.
    manifesto["bases"] = [base for base in manifesto["bases"] if not base.get("dados_sinteticos")]
    manifesto.pop("curadoria_v6", None)
    for base in manifesto["bases"]:
        if base["base_id"] in OFFICIAL_BASE_TARGETS:
            base["volume_alvo"] = OFFICIAL_BASE_TARGETS[base["base_id"]]
    inventario = json.loads((ROOT / "data" / "inventory" / "v5.json").read_text(encoding="utf-8"))

    assert validate_manifest_contract(manifesto).ok
    assert validate_inventory_contract(manifesto, inventario).ok


def test_curadoria_v7_censo_valida_sem_volumes_fixos():
    '''Censo v7: volumes podem ser ausentes e o limite por órgão é nulo.'''
    manifesto = _manifesto_oficial()
    for base in manifesto["bases"]:
        if base["base_id"] == "contratos_estruturado":
            base["volume_alvo"] = None
    manifesto["curadoria_v7"] = {
        "selecao": "censo_contratos_e_publicacoes_2025_rj",
        "limite_por_orgao_por_base": None,
        "alvo_arquivos_total": None,
    }
    result = validate_manifest_contract(manifesto)
    assert result.ok


def test_curadoria_v7_rejeita_limite_invalido():
    manifesto = _manifesto_oficial()
    manifesto["curadoria_v7"] = {"selecao": "censo", "limite_por_orgao_por_base": 0}
    result = validate_manifest_contract(manifesto)
    assert not result.ok
    assert any("curadoria_v7: limite" in error for error in result.errors)


def test_curadoria_v7_rejeita_selecao_ausente():
    manifesto = _manifesto_oficial()
    manifesto["curadoria_v7"] = {}
    result = validate_manifest_contract(manifesto)
    assert not result.ok
    assert any("curadoria_v7: selecao ausente" in error for error in result.errors)


def test_manifesto_v6_arquivado_continua_valido():
    '''Regressão: o contrato v6 segue íntegro no manifesto arquivado.'''
    manifesto = load_manifest(ROOT / "archive" / "corpus_manifest_v6.yaml")
    assert validate_manifest_contract(manifesto).ok


def test_manifesto_atual_e_valido_com_curadoria_v7():
    manifesto = load_manifest(ROOT / "corpus_manifest.yaml")
    assert validate_manifest_contract(manifesto).ok
