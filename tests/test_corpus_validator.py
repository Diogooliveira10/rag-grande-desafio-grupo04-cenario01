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


def test_manifesto_oficial_valido_passa_e_ignora_bases_experimentais():
    assert validate_manifest_contract(_manifesto_oficial()).ok


def test_manifesto_sem_bases_e_rejeitado():
    resultado = validate_manifest_contract({"bases": []})

    assert resultado.errors == ("manifesto sem bases declaradas",)


def test_manifesto_oficial_aponta_base_faltando_e_volume_divergente():
    manifesto = _manifesto_oficial()
    manifesto["bases"] = [base for base in manifesto["bases"] if base["base_id"] != "pca_agregado"]
    manifesto["bases"][0]["volume_alvo"] = 5

    erros = validate_manifest_contract(manifesto).errors

    assert any("bases oficiais divergentes" in erro for erro in erros)
    assert any("contratos_estruturado: volume_alvo esperado=1200 obtido=5" in erro for erro in erros)


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
    assert "contratos_estruturado: volume esperado=1200 obtido=150" in erros


def test_inventario_aceita_itens_quando_nao_ha_volume_obtido():
    manifesto = _manifesto_oficial()
    inventario = _inventario_completo(manifesto)
    for base in inventario["por_base"]:
        base["itens"] = base.pop("volume_obtido")

    assert validate_inventory_contract(manifesto, copy.deepcopy(inventario)).ok


def test_manifesto_e_inventario_v3_do_repositorio_continuam_validos():
    manifesto = load_manifest(ROOT / "corpus_manifest.yaml")
    inventario = json.loads((ROOT / "data" / "inventory" / "v3.json").read_text(encoding="utf-8"))

    assert validate_manifest_contract(manifesto).ok
    assert validate_inventory_contract(manifesto, inventario).ok
