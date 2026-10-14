'''Testes do DIFF de corpus (v6 → v7).'''

from __future__ import annotations

import json
from pathlib import Path

from src.diff_corpus import diff_corpus, render_markdown


def _linha(logical_id: str, base_id: str, conteudo: str) -> str:
    registro = {
        "chunk_id": f"{logical_id}:chunk-1-abc",
        "logical_id": logical_id,
        "document_id": logical_id,
        "base_id": base_id,
        "text": conteudo,
        "document_content_sha256": f"sha-{conteudo}",
        "sha256": "hash-bruto-que-nao-compara",
    }
    return json.dumps(registro, ensure_ascii=False)


def _corpus(caminho: Path, linhas: list[str]) -> Path:
    caminho.write_text("".join(linha + "\n" for linha in linhas), encoding="utf-8")
    return caminho


def test_diff_classifica_entrou_saiu_alterou_identico(tmp_path):
    antigo = _corpus(tmp_path / "antigo.jsonl", [
        _linha("c:numeroControlePNCP:A", "contratos_estruturado", "registro A"),
        _linha("c:numeroControlePNCP:B", "contratos_estruturado", "registro B v1"),
        _linha("c:numeroControlePNCP:C", "contratos_estruturado", "registro C"),
    ])
    novo = _corpus(tmp_path / "novo.jsonl", [
        _linha("c:numeroControlePNCP:B", "contratos_estruturado", "registro B v2"),
        _linha("c:numeroControlePNCP:C", "contratos_estruturado", "registro C"),
        _linha("c:numeroControlePNCP:D", "contratos_estruturado", "registro D"),
    ])
    diff = diff_corpus(antigo, novo, base_id="contratos_estruturado")
    assert diff["contagens"] == {
        "entraram": 1, "sairam": 1, "alterados": 1, "identicos": 1,
        "total_antigo": 3, "total_novo": 3,
    }
    assert diff["entraram"] == ["c:numeroControlePNCP:D"]
    assert diff["sairam"] == ["c:numeroControlePNCP:A"]
    assert diff["alterados"] == ["c:numeroControlePNCP:B"]
    assert diff["identicos"] == ["c:numeroControlePNCP:C"]


def test_diff_ignora_outras_bases(tmp_path):
    antigo = _corpus(tmp_path / "antigo.jsonl", [
        _linha("c:x", "contratos_estruturado", "1"),
        _linha("e:y", "editais_normativo", "edital"),
    ])
    novo = _corpus(tmp_path / "novo.jsonl", [
        _linha("c:x", "contratos_estruturado", "1"),
    ])
    diff = diff_corpus(antigo, novo, base_id="contratos_estruturado")
    # O edital herdado não é "removido": a comparação é da base censitária.
    assert diff["contagens"]["sairam"] == 0
    assert diff["contagens"]["identicos"] == 1


def test_render_markdown_lista_amostra_e_contagens(tmp_path):
    antigo = _corpus(tmp_path / "a.jsonl", [_linha("c:x", "contratos_estruturado", "1")])
    novo = _corpus(tmp_path / "n.jsonl", [_linha("c:x", "contratos_estruturado", "2")])
    diff = diff_corpus(antigo, novo, base_id="contratos_estruturado")
    texto = render_markdown(diff)
    assert "Alterados (mesmo `logical_id`, conteúdo diferente): **1**" in texto
    assert "`c:x`" in texto
