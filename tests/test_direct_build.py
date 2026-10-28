'''Testes do modo separado "direto para artefatos finais" (decisão §7.6).'''

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from src.direct_build import RegistroDireto, construir_direto, finalizar_direto
from src.corpus_validator import validate_inventory_contract

BASE = {
    "base_id": "contratos_estruturado",
    "escopo": "oficial",
    "natureza": "estruturada",
    "fonte": "PNCP - contratos assinados",
    "acesso": {"tipo": "provider", "provider": "pncp"},
    "sensitivity_default": "publico",
    "campo_vigencia": "dataVigenciaFim",
    "chunking_sugerido": "1 contrato = 1 chunk, serializado em linguagem natural",
    "metadados_extras": ["orgao", "modalidade", "valor", "municipio", "ano"],
}


def _registro(numero: str, orgao: str, municipio: str, valor: float) -> dict:
    return {
        "numeroControlePNCP": numero,
        "objetoContrato": "Contratação de manutenção de computadores",
        "dataAssinatura": "2025-03-10",
        "dataVigenciaFim": "2026-03-10",
        "valorGlobal": valor,
        "nomeRazaoSocialFornecedor": "FORNECEDORA EXEMPLO LTDA",
        "orgaoEntidade": {"cnpj": "00000000000000", "razaoSocial": orgao, "esferaId": "M"},
        "unidadeOrgao": {"municipioNome": municipio, "ufSigla": "RJ"},
    }


def _embed_fake(texts: list[str]) -> list[list[float]]:
    return [[float(len(t) % 7), 1.0, 0.25] for t in texts]


def _registros() -> list[RegistroDireto]:
    payloads = [
        _registro("11111111000001-2-000001/2025", "PREFEITURA DE EXEMPLO", "Exemplo", 1000.0),
        _registro("11111111000001-2-000002/2025", "PREFEITURA DE EXEMPLO", "Exemplo", 2000.0),
    ]
    saida = []
    for indice, payload in enumerate(payloads, start=1):
        bruto = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        saida.append(RegistroDireto(
            payload=payload,
            origem=f"https://pncp.gov.br/api/consulta/v1/contratos/{indice}",
            sha256=hashlib.sha256(bruto).hexdigest(),
            bytes=len(bruto),
            coletado_em="2026-10-13T12:00:00+00:00",
        ))
    return saida


def _construir(tmp_path: Path) -> dict:
    return construir_direto(
        _registros(),
        BASE,
        tmp_path / "v7",
        embed_fn=_embed_fake,
        modelo="modelo-de-teste",
        manifest_hash="sha256:teste",
    )


def test_modo_direto_grava_somente_artefatos_finais(tmp_path):
    '''Sem raw/, backups/, checkpoints/ nem snapshot de embeddings/.'''
    relatorio = _construir(tmp_path)
    destino = Path(relatorio["destino"])
    assert (destino / "documents.jsonl").exists()
    assert (destino / "base_estruturada.sqlite").exists()
    assert (destino / "inventario_direto.json").exists()
    assert (destino / "index" / "contratos_estruturado" / "vectors.faiss").exists()
    assert (destino / "index" / "contratos_estruturado" / "vectors.jsonl").exists()
    assert (destino / "index" / "contratos_estruturado" / "manifest.json").exists()
    for intermediario in ("raw", "backups", "embeddings", "checkpoints", "normalized"):
        assert not (destino / intermediario).exists(), intermediario


def test_chunks_preservam_metadados_do_caminho_padrao(tmp_path):
    relatorio = _construir(tmp_path)
    linhas = [
        json.loads(linha)
        for linha in Path(relatorio["documents"]).read_text(encoding="utf-8").splitlines()
        if linha.strip()
    ]
    assert len(linhas) == 2  # 1 contrato = 1 chunk
    chunk = linhas[0]
    assert chunk["chunking_strategy"] == "registro_inteiro"
    assert chunk["document_id"] == f"contratos_estruturado:{chunk['sha256'][:16]}"
    assert chunk["logical_id"].startswith("contratos_estruturado:numeroControlePNCP:")
    # O texto do chunk é o registro serializado como no preparo (paridade de hash).
    assert chunk["text"] == json.dumps(
        _registros()[0].payload, ensure_ascii=False, sort_keys=True, indent=2
    )
    for obrigatorios in ("source_file", "chunk_id", "base_id", "doc_type", "sensitivity"):
        assert chunk[obrigatorios], obrigatorios
    extras = chunk["extras"]
    assert extras["orgao"] == "PREFEITURA DE EXEMPLO"
    assert extras["municipio"] == "Exemplo"
    assert extras["valor"] == 1000.0
    assert extras["objeto"].startswith("Contratação de manutenção")
    assert chunk["data_vigencia"] == "2026-03-10"
    assert chunk["origem"].startswith("https://pncp.gov.br/")


def test_sqlite_fica_consultavel(tmp_path):
    relatorio = _construir(tmp_path)
    conexao = sqlite3.connect(Path(relatorio["destino"]) / "base_estruturada.sqlite")
    try:
        linhas = conexao.execute(
            "SELECT orgao_nome, municipio, valor FROM contratacoes ORDER BY valor"
        ).fetchall()
    finally:
        conexao.close()
    assert linhas == [
        ("PREFEITURA DE EXEMPLO", "Exemplo", 1000.0),
        ("PREFEITURA DE EXEMPLO", "Exemplo", 2000.0),
    ]


def test_inventario_traz_proveniencia_por_origem(tmp_path):
    relatorio = _construir(tmp_path)
    inventario = json.loads(Path(relatorio["inventario"]).read_text(encoding="utf-8"))
    assert inventario["modo"] == "direto"
    assert inventario["por_base"][0]["volume_obtido"] == 2
    assert len(inventario["arquivos"]) == 2
    for arquivo in inventario["arquivos"]:
        assert arquivo["origem"].startswith("https://")
        assert len(arquivo["sha256"]) == 64
        assert arquivo["hash_derivado"] is False
    # source_sha256 do índice confere com o documents.jsonl gravado.
    manifest_indice = json.loads(
        (Path(relatorio["destino"]) / "index" / "contratos_estruturado" / "manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest_indice["source_sha256"] == relatorio["source_sha256"]
    assert relatorio["source_sha256"] == hashlib.sha256(
        Path(relatorio["documents"]).read_bytes()
    ).hexdigest()
    # O inventário é um inventário válido para o contrato do manifesto.
    manifesto = {
        "bases": [{**BASE, "volume_alvo": 2, "volume_regra": "minimo",
                   "selecao_deterministica": True}],
    }
    assert validate_inventory_contract(manifesto, inventario, official=False).ok


def test_dedup_preserva_as_linhas_originais(tmp_path, monkeypatch):
    '''A deduplicação preserva cada linha byte a byte e não sobra temporário.'''
    monkeypatch.setattr("src.direct_build.index_mod.build_index", lambda **_k: {"chunks": 0})
    monkeypatch.setattr("src.direct_build.construir_banco", lambda **_k: {"ok": True})
    destino = tmp_path / "base"
    destino.mkdir()

    def linha_chunk(chunk_id: str, documento: str) -> str:
        # Formatação propositalmente diferente do sort_keys do coletor.
        return json.dumps(
            {"document_id": documento, "texto": "texto ", "chunk_id": chunk_id,
             "base_id": "contratos_estruturado", "extras": {}, "bytes": 1,
             "coletado_em": "2026-10-15T00:00:00", "origem": "u", "sha256": ""},
            ensure_ascii=False,
        )

    primeira = linha_chunk("c1", "d1")
    segunda = linha_chunk("c2", "d1")
    repetida = linha_chunk("c1", "d1")
    arquivo = destino / "documents.jsonl"
    arquivo.write_text("\n".join([primeira, segunda, repetida]) + "\n", encoding="utf-8")

    relatorio = finalizar_direto(destino, BASE, embed_fn=_embed_fake)

    assert arquivo.read_text(encoding="utf-8") == primeira + "\n" + segunda + "\n"
    assert relatorio["chunks"] == 2
    assert relatorio["registros"] == 1  # um único document_id
    assert not (destino / "documents.jsonl.provisorio").exists()


def test_falha_na_reescrita_preserva_o_documents(tmp_path, monkeypatch):
    '''Linha corrompida interrompe a dedup sem tocar no arquivo original.'''
    monkeypatch.setattr("src.direct_build.index_mod.build_index", lambda **_k: {"chunks": 0})
    monkeypatch.setattr("src.direct_build.construir_banco", lambda **_k: {"ok": True})
    destino = tmp_path / "base"
    destino.mkdir()
    arquivo = destino / "documents.jsonl"
    original = (
        '{"chunk_id": "c1", "document_id": "d1", "base_id": "contratos_estruturado"}\n'
        '{"chunk_id": "c2", "document_id": "d1", "base_id": "contratos_estruturad'
    )
    arquivo.write_text(original, encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        finalizar_direto(destino, BASE, embed_fn=_embed_fake)

    assert arquivo.read_text(encoding="utf-8") == original  # nada truncado
    assert not (destino / "documents.jsonl.provisorio").exists()


def test_registro_sem_sha_usa_hash_derivado_marcado(tmp_path):
    registro = RegistroDireto(
        payload=_registro("33333333000001-2-000003/2025", "CAMARA DE EXEMPLO", "Exemplo", 300.0),
        origem="https://pncp.gov.br/api/consulta/v1/contratos/3",
    )
    relatorio = construir_direto(
        [registro], BASE, tmp_path / "v7", embed_fn=_embed_fake, modelo="modelo-de-teste"
    )
    inventario = json.loads(Path(relatorio["inventario"]).read_text(encoding="utf-8"))
    assert inventario["arquivos"][0]["hash_derivado"] is True
    assert len(inventario["arquivos"][0]["sha256"]) == 64
    # Modelo efetivo no inventário e manifests
    assert inventario["meta"]["embedding_model"] == "modelo-de-teste"
    assert json.loads((tmp_path / "v7" / "index" / "manifest.json").read_text())["embedding_model"] == "modelo-de-teste"
