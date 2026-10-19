"""Restrições semânticas gerais: papéis, núcleo do objeto e não relaxamento."""
import json

import pytest

from src import retrieve as retrieval
from src.query_analysis import _analisar, filtros_para_retrieve
from src.retrieval_constraints import solicita_bem_completo, objeto_auxiliar, tema_bem_completo, radicais_do_objeto
from src.obs import default_logger


@pytest.mark.parametrize("query", [
    "Qual órgão comprou uma máquina de impressão?",
    "Há aquisição de aparelhos para o laboratório?",
    "Alguma ata fornece equipamentos permanentes?",
])
def test_identifica_aquisicao_do_bem_inteiro(query):
    assert solicita_bem_completo(query)


@pytest.mark.parametrize("query", [
    "Quem contratou manutenção de máquinas?", "Contratos de peças para aparelhos",
    "Qual contrato de locação de máquina?", "Compras de filmes para aparelhos",
    "Há compras de equipamentos de proteção?", "Norma sobre uso de aparelhos",
    "Compras que não incluem aparelhos", "Qual órgão utiliza máquina?",
])
def test_nao_converte_servico_peca_negacao_ou_objeto_ambiguo_em_bem_inteiro(query):
    assert not solicita_bem_completo(query)


@pytest.mark.parametrize("objeto", [
    "Aquisição de cartuchos para impressora", "Compra de peças para máquina industrial",
    "[PORTAL] Registro de preços para futura e eventual aquisição de filme para aparelho",
    "Contratação de empresa especializada na prestação de serviços de manutenção de máquinas",
    "Aquisição de material de consumo para equipamento", "Fornecimento de cabos para aparelhos",
])
def test_rejeita_nucleo_auxiliar_independente_da_tecnologia(objeto):
    assert objeto_auxiliar(objeto)


@pytest.mark.parametrize("objeto", [
    "Aquisição de máquina industrial com peças de reposição",
    "Compra de impressora com cartuchos e cabos", "Aquisição e instalação de equipamento",
    "Fornecimento de peças e máquinas", "Aquisição de material permanente",
    "Equipamento com manutenção e garantia incluídas",
])
def test_nao_descarta_bem_completo_por_mencionar_pecas_ou_garantia(objeto):
    assert not objeto_auxiliar(objeto)


def test_tema_explicito_do_aparelho_e_lexical_nao_um_sinonimo_inventado():
    tema = tema_bem_completo("Compra de aparelho de impressão")
    assert tema in radicais_do_objeto("Aquisição de impressora")
    assert tema not in radicais_do_objeto("Máquina de costura")
    assert tema_bem_completo("Aparelhos para o laboratório") is None
    assert tema_bem_completo("Compra de aparelhos para laboratório") is None
    assert "computad" not in radicais_do_objeto("notebook")
    # O campo do fornecedor/órgão não pode fingir compatibilidade do objeto.
    filters = {"objeto_termo": {"operator": "contains", "value": tema, "optional": False}}
    assert not retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {
        "objeto": "Máquina de costura", "fornecedor": "Impressora Industrial SA"}}, filters)


def test_orgao_aceita_qualificador_intermediario_sem_aceitar_outro_papel():
    filters = {"orgao": {"operator": "contains", "value": "Secretaria de Educação", "optional": False}}
    assert retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {
        "orgao": "SECRETARIA DE ESTADO DE EDUCAÇÃO"}}, filters)
    assert not retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {
        "orgao": "Secretaria de Saúde", "objeto": "Atendimento à Secretaria de Educação",
        "fornecedor": "Secretaria de Educação"}}, filters)
    municipal = {"orgao": {"operator": "contains", "value": "Secretaria Municipal de Educação", "optional": False}}
    assert not retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {
        "orgao": "SECRETARIA DE ESTADO DE EDUCAÇÃO"}}, municipal)


def test_nome_generico_do_fundo_nao_escolhe_um_unico_municipio():
    filters = filtros_para_retrieve(_analisar("Algum fundo de assistência social contratou transporte?"))
    for name in ["FUNDO MUNICIPAL DE ASSISTENCIA SOCIAL", "FUNDO DE ASSISTÊNCIA SOCIAL DE CIDADE AZUL"]:
        assert retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {"orgao": name}}, filters)
    assert not retrieval._matches_filters({"base_id": "contratos_estruturado", "extras": {
        "orgao": "Fundo de Previdência Social", "objeto": "Assistência social para usuários"}}, filters)


def test_analise_llm_valida_cnpj_sem_cair_em_fallback_por_atributo_inexistente():
    from src.query_analysis import analisar_query
    query = "Contratos do fornecedor CNPJ 11.222.333/0001-81"
    response = {"intencoes": [{"pergunta": query, "bases": ["estruturada"],
        "filtros": {"filtros_analiticos": {"cnpj": "11222333000181"}}}]}
    result = analisar_query(query, modo="llm", cliente=lambda _: json.dumps(response))
    assert result.metodo == "llm"
    assert result.filtros.filtros_analiticos["cnpj"] == "11222333000181"


def make_record(id, objeto):
    return {"chunk_id": id, "base_id": "contratos_estruturado", "embedding": [1.0, 0.1],
            "text": json.dumps({"objetoContrato": objeto}),
            "metadata": {"base_id": "contratos_estruturado", "source_file": f"{id}.json",
                         "extras": {"objeto": objeto}}}


def test_restricao_remove_auxiliar_de_dense_bm25_lookup_e_pool_do_reranker(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    rows = [make_record("part", "Aquisição de cartuchos para impressora"),
            make_record("device", "Compra de impressora com cartuchos")]
    base = tmp_path / "contratos_estruturado"
    base.mkdir()
    (base / "vectors.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf8")
    docs = tmp_path / "documents.jsonl"
    docs.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf8")
    seen = []
    def rerank(query, items):
        seen.extend(i.chunk_id for i in items)
        return items
    result = retrieval.retrieve("Qual compra de aparelho para impressão?", index_dir=tmp_path,
        documents_file=docs, embed_fn=lambda _: [[1.0, 0.0]], reranker=rerank)
    assert seen == ["device"]
    assert [r.chunk_id for r in result] == ["device"]


def test_restricao_que_zera_nao_e_relaxada(tmp_path, monkeypatch):
    monkeypatch.setattr(default_logger, "logs_dir", tmp_path / "logs")
    row = make_record("only-part", "Aquisição de cartuchos para impressora")
    base = tmp_path / "contratos_estruturado"
    base.mkdir()
    (base / "vectors.jsonl").write_text(json.dumps(row) + "\n", encoding="utf8")
    docs = tmp_path / "documents.jsonl"
    docs.write_text(json.dumps(row) + "\n", encoding="utf8")
    def never_embed(_):
        pytest.fail("Não embutir pergunta sem candidatos admissíveis")
    assert retrieval.retrieve("Há compra de aparelho de impressão?", index_dir=tmp_path,
        documents_file=docs, embed_fn=never_embed) == []


def test_restricao_de_objeto_nao_e_aplicada_a_norma():
    assert retrieval._matches_filters({"base_id": "editais_normativo"}, {
        "objeto_auxiliar": {"operator": "==", "value": False, "optional": False}})
