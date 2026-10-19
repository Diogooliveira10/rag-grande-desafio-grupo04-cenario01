"""Falhas generalizáveis do corpus ampliado, sem usar perguntas/IDs do golden."""
import json

import pytest

from src import retrieve as retrieval
from src.obs import default_logger
from src.rerank import texto_para_rerank


def record(chunk_id, numero, *, ano=2024, fornecedor="Empresa Azul", cnpj="11222333000181", **extra):
    return {"chunk_id": chunk_id, "base_id": "contratos_estruturado",
            "text": json.dumps({"numeroContratoEmpenho": numero, "anoContrato": ano,
                                "nomeRazaoSocialFornecedor": fornecedor,
                                "niFornecedor": cnpj, **extra}),
            "metadata": {"base_id": "contratos_estruturado", "source_file": f"{chunk_id}.json"}}


@pytest.mark.parametrize("query", ["Contrato nº 18/2024", "contrato n. 0018/2024 da Empresa Azul",
                                   "CONTRATO NÚMERO 18/2024", "Contrato 18/2024"])
def test_lookup_numero_ano_do_contrato_nao_confunde_datas_ou_compras(query):
    rows = [record("expected", "00018"), record("other-year", "18", ano=2023),
            record("other-number", "180/2024"),
            record("purchase-reference", "19/2024", objetoContrato="Compra 18/2024"),
            record("complex-number", "18/2024-001")]
    result = retrieval._resultados_identificador_exato(query, rows)
    assert [r.chunk_id for r in result] == ["expected"]
    assert result[0].metadata["extras"]["identificador_contrato_exato"] is True
    assert result[0].metadata["extras"]["identificador_pncp_exato"] is False
    assert retrieval._referencias_contrato("Publicado em 18/2024; compra nº 18/2024") == set()


def test_lookup_contrato_exige_cnpj_tambem_quando_ambos_foram_informados():
    rows = [record("right", "18/2024"), record("same-number-other-cnpj", "18/2024", cnpj="99888777000166"),
            record("same-cnpj-other-number", "19/2024")]
    result = retrieval._resultados_identificador_exato("Contrato 18/2024 CNPJ 11.222.333/0001-81", rows)
    assert [r.chunk_id for r in result] == ["right"]


def test_lookup_contrato_usa_metadados_canonicos_e_ignora_json_incompleto():
    row = {"chunk_id": "metadata", "text": "Contrato anexado", "metadata": {
        "base_id": "contratos_estruturado", "extras": {"numero_contrato": "18", "ano_contrato": 2024}}}
    truncated = {"chunk_id": "truncated", "text": '{"numeroContratoEmpenho": "18/2024",'}
    assert [r.chunk_id for r in retrieval._resultados_identificador_exato("Contrato 18/2024", [truncated, row])] == ["metadata"]


def test_cnpj_do_fornecedor_nao_promove_cnpj_do_orgao():
    right = record("supplier", "18/2024")
    wrong = record("agency-only", "19/2024", cnpj="99888777000166",
                   orgaoEntidade={"cnpj": "11222333000181"})
    result = retrieval._resultados_identificador_exato(
        "Contratos do fornecedor CNPJ 11.222.333/0001-81", [wrong, right])
    assert [r.chunk_id for r in result] == ["supplier"]


def test_promocao_exata_preserva_relevancia_em_vez_da_ordem_do_hash():
    first = retrieval._result(record("z-best", "18/2024"), 5.0)
    second = retrieval._result(record("a-other", "18/2024"), 1.0)
    outsider = retrieval._result(record("outsider", "19/2024"), 9.0)
    exact = retrieval._resultados_identificador_exato("Contrato 18/2024", [second._record, first._record])
    result = retrieval._promover_identificadores_exatos([outsider, first, second], exact, 3)
    assert [r.chunk_id for r in result] == ["z-best", "a-other", "outsider"]
    assert result[0].score == 5.0


def test_lookup_resgata_contrato_fora_do_pool_e_respeita_filtros(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    documents = tmp_path / "documents.jsonl"
    target = record("target", "18/2024")
    target["metadata"]["municipio"] = "Cidade Azul"
    documents.write_text(json.dumps(target) + "\n", encoding="utf8")
    distractor = retrieval._result(record("distractor", "19/2024"), 1.0)
    monkeypatch.setattr(retrieval, "dense_retrieve", lambda *a, **kw: [distractor])
    monkeypatch.setattr(retrieval, "bm25_retrieve", lambda *a, **kw: [distractor])
    kwargs = dict(index_dir=tmp_path / "index", documents_file=documents, top_k=1)
    result = retrieval.retrieve("Contrato nº 18/2024", **kwargs)
    assert result[0].chunk_id == "target"
    excluded = retrieval.retrieve("Contrato nº 18/2024", filters={"municipio": "Outra cidade"}, **kwargs)
    assert all(r.chunk_id != "target" for r in excluded)


def test_numero_nao_unico_usa_nome_do_fornecedor_antes_do_corte(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    rows = [record(f"a{i:02}", "18/2024", fornecedor="Editora Azul") for i in range(25)]
    target = record("z-target", "18/2024", fornecedor="Editora Violeta S.A.")
    rows.append(target)
    monkeypatch.setattr(retrieval, "_records_for", lambda *a, **kw: rows)
    distractors = [retrieval._result(row, 1.0) for row in rows[:-1]]
    monkeypatch.setattr(retrieval, "dense_retrieve", lambda *a, **kw: distractors)
    monkeypatch.setattr(retrieval, "bm25_retrieve", lambda *a, **kw: distractors)
    monkeypatch.setattr(retrieval.config, "RERANK_CANDIDATES", 10)
    seen = []
    def rerank(query, items):
        seen.extend(r.chunk_id for r in items)
        return list(reversed(items))  # promoção final também deve respeitar identidade
    result = retrieval.retrieve("Contrato 18/2024 da Editora Violeta", index_dir=tmp_path,
        documents_file=tmp_path / "documents.jsonl", top_k=1, reranker=rerank)
    assert len(seen) == 10 and "z-target" in seen
    assert result[0].chunk_id == "z-target"
    assert result[0].metadata["extras"]["fornecedor_termos_coincidentes"] == ["editora", "violeta"]


def test_cnpj_do_orgao_nao_substitui_fornecedor_nulo():
    row = record("missing-supplier", "18/2024", cnpj=None, orgaoEntidade={"cnpj": "11222333000181"})
    assert retrieval._resultados_identificador_exato("Contratos do fornecedor 11222333000181", [row]) == []


def test_pool_configurado_permite_rerank_abaixo_dos_dez_primeiros(tmp_path, monkeypatch):
    monkeypatch.setattr(retrieval.config, "RERANK_CANDIDATES", 50)
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    candidates = [retrieval._result(record(f"c{i:02}", str(i)), 1.0 / (i + 1)) for i in range(25)]
    monkeypatch.setattr(retrieval, "dense_retrieve", lambda *a, **kw: candidates)
    monkeypatch.setattr(retrieval, "bm25_retrieve", lambda *a, **kw: [])
    seen = []
    def rerank(query, items):
        seen.append(len(items))
        return list(reversed(items))
    result = retrieval.retrieve("Uma pergunta genérica", index_dir=tmp_path, documents_file=tmp_path / "missing",
                                top_k=10, reranker=rerank)
    assert seen == [25]
    assert result[0].chunk_id == "c24"


def test_projecao_pncp_preserva_orgao_antes_do_objeto_longo_e_amparo():
    objeto = "Objeto longo " * 120
    payload = {"objetoCompra": objeto, "orgaoEntidade": {"razaoSocial": "Fundo Municipal de Educação"},
               "unidadeOrgao": {"nomeUnidade": "Secretaria de Educação"},
               "amparoLegal": {"descricao": "Base legal extensa " * 200},
               "valorTotalEstimado": 12345, "dataPublicacaoPncp": "2024-05-20"}
    text = texto_para_rerank(json.dumps(payload))
    assert text.startswith("Órgão contratante: Fundo Municipal de Educação; Objeto:")
    assert text.index("Unidade: Secretaria de Educação") < text.index("descricao:")
    assert "valorTotalEstimado: 12345" in text
    assert "dataPublicacaoPncp: 2024-05-20" in text
    assert "Complemento do objeto: " + objeto[600:] in text
    assert payload["objetoCompra"] == objeto


def test_projecao_pncp_nao_confunde_orgao_com_fornecedor():
    text = texto_para_rerank(json.dumps({"objetoContrato": "Fornecimento de livros", "numeroContratoEmpenho": "18/2024",
        "orgaoEntidade": {"razaoSocial": "Município Azul"}, "nomeRazaoSocialFornecedor": "Editora Verde"}))
    assert "Órgão contratante: Município Azul" in text
    assert "Fornecedor: Editora Verde" in text
    assert "Número do contrato: 18/2024" in text
