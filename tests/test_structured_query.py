"""Consultas fixas sobre a base estruturada (SQLite temporário) e a ligação com o pipeline."""

from __future__ import annotations

import json

import pytest

from src import obs, pipeline, structured_db, structured_query as sq
from src.schema import ChunkMetadata, Natureza, QueryFilters, RAGResponse, ConfidenceLevel, RouteDecision
from src.structured_db import Banco
from tests.test_structured_db import _contratacao, _plano, escrever_documents

ESTRUTURADA = [Natureza.ESTRUTURADA]
AGREGADA = [Natureza.ESTRUTURADA, Natureza.AGREGADA]


@pytest.fixture
def base(tmp_path):
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao("M-1", esfera="M", valor_estimado=100.0, objeto="Aquisição de ambulâncias tipo D", data="2025-01-05T00:00:00"),
            _contratacao("M-2", esfera="M", valor_estimado=300.0, objeto="Ambulância de resgate", data="2025-02-05T00:00:00"),
            _contratacao("E-1", esfera="E", valor_estimado=500.0, objeto="Ambulâncias UTI", modalidade="Concorrência - Eletrônica"),
            _contratacao("F-1", esfera="F", valor_estimado=10_000.0, objeto="Obra de pavimentação", data="2024-06-01T00:00:00"),
        ],
        planos=[_plano("P-1", valor=7_000.0, unidade="Saúde"), _plano("P-2", valor=3_000.0, unidade="Educação"),
                _plano("P-0", ano=2024, valor=1.0)],
    )
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def _consultar(pergunta, base, bases=ESTRUTURADA, **filtros):
    return sq.consultar_estruturado(pergunta, QueryFilters(**filtros), bases, **base)


# --------------------------------------------------------------------------
# Detecção
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pergunta,tipo",
    [
        ("Quais as dez maiores contratações de 2025?", "ranking"),
        ("Qual a mediana do valor das ambulâncias?", "estatistica"),
        ("Estados pagaram mais por ambulâncias do que municípios?", "estatistica"),
        ("Houve aumento no valor médio ao longo de 2025?", "evolucao"),
        ("Quais modalidades apareceram com mais frequência?", "agrupamento"),
    ],
)
def test_detecta_tipo_de_conta(pergunta, tipo):
    assert sq.detectar_consulta(pergunta, QueryFilters(), ESTRUTURADA).tipo == tipo


def test_pergunta_sem_conta_ou_sem_base_estruturada_nao_vira_sql():
    assert sq.detectar_consulta("Quais contratos de ambulâncias foram publicados?", QueryFilters(), ESTRUTURADA) is None
    assert sq.detectar_consulta("Qual o valor médio?", QueryFilters(), [Natureza.NORMATIVA]) is None


def test_extrai_termos_do_objeto_esfera_e_limite():
    consulta = sq.detectar_consulta(
        "Quais as cinco maiores contratações de manutenção de computadores de municípios?", QueryFilters(ano=2025), ESTRUTURADA
    )

    assert consulta.termos_objeto == ("manutencao", "computador")
    assert consulta.esfera == "municipal"
    assert consulta.limite == 5
    assert consulta.ano == 2025


def test_pergunta_sobre_pca_vai_para_a_tabela_de_planos():
    consulta = sq.detectar_consulta("Qual o valor total planejado no PCA de 2025?", QueryFilters(ano=2025), AGREGADA)

    assert consulta.tabela == "pca_planos"


# --------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------


def test_ranking_ordena_por_valor_e_respeita_filtro_de_ano(base):
    [chunk] = _consultar("Quais as maiores contratações?", base, ano=2025)

    linhas = chunk.content.splitlines()[1:]
    assert [linha.split(" | ")[0] for linha in linhas] == ["1. R$ 500,00", "2. R$ 300,00", "3. R$ 100,00"]
    assert "ano=2025" in chunk.content


def test_estatistica_calcula_mediana_e_quebra_por_esfera(base):
    [chunk] = _consultar("Qual a mediana do valor das ambulâncias?", base)

    assert "objeto contém ambulancia" in chunk.content
    assert "Geral: 3 registros; total R$ 900,00; média R$ 300,00; mediana R$ 300,00" in chunk.content
    assert "Esfera estadual: 1 registros" in chunk.content
    assert "Esfera municipal: 2 registros; total R$ 400,00; média R$ 200,00; mediana R$ 200,00" in chunk.content


def test_objeto_inexistente_nao_calcula_sobre_outro_assunto(base):
    [chunk] = _consultar("Qual o valor médio de helicópteros blindados?", base)

    assert "Nenhuma contratação do corpus tem no objeto" in chunk.content
    assert "média" not in chunk.content


def test_termos_sem_combinacao_exata_caem_para_ou(base):
    [chunk] = _consultar("Qual o valor total de ambulâncias e pavimentação?", base)

    assert "objeto contém ambulancia ou pavimentacao" in chunk.content
    assert "Geral: 4 registros" in chunk.content


def test_agrupamento_conta_por_modalidade(base):
    [chunk] = _consultar("Quais modalidades apareceram com mais frequência?", base, ano=2025)

    assert "Pregão - Eletrônico: 2 registros, total R$ 400,00" in chunk.content
    assert "Concorrência - Eletrônica: 1 registros, total R$ 500,00" in chunk.content


def test_evolucao_agrupa_por_mes(base):
    [chunk] = _consultar("Houve aumento no valor ao longo de 2025?", base, ano=2025)

    assert chunk.content.index("2025-01:") < chunk.content.index("2025-02:")


def test_pca_avisa_que_nao_e_o_total_do_estado(base):
    [chunk] = _consultar("Qual o valor total planejado no PCA de 2025?", base, AGREGADA, ano=2025)

    assert "NÃO o valor total do PCA do Estado" in chunk.content
    assert "Geral: 2 registros; total R$ 10.000,00" in chunk.content
    assert chunk.metadata.base_id == "pca_agregado"


def test_resultado_grande_e_dividido_em_chunks_que_cabem_no_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(sq.config, "GENERATION_CHUNK_CHARS", 400)
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[_contratacao(f"C-{i}", valor_estimado=float(i), objeto="x" * 80) for i in range(1, 16)],
    )
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")

    chunks = sq.consultar_estruturado("Quais as quinze maiores contratações?", QueryFilters(), ESTRUTURADA,
                                      banco=banco, documents_file=documents)

    assert len(chunks) > 1
    assert all(len(c.content) <= 400 for c in chunks)
    assert len({c.metadata.chunk_id for c in chunks}) == len(chunks)
    assert all(c.metadata.doc_type == "sql" and c.metadata.chunk_id.startswith("sql:") for c in chunks)
    assert chunks[1].content.startswith("(continuação da consulta SQL")


def test_consulta_registra_stage_consulta_sql_com_o_sql(base):
    _consultar("Quais as maiores contratações?", base, ano=2025)

    [registro] = [
        json.loads(l)
        for arquivo in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
        for l in arquivo.read_text(encoding="utf-8").splitlines()
    ]
    assert registro["stage"] == "consulta_sql"
    assert registro["status"] == "ok"
    assert "ORDER BY valor DESC" in registro["extra"]["sql"]
    assert registro["extra"]["linhas"] == 3


def test_falha_no_banco_nao_derruba_e_fica_no_log(base, monkeypatch):
    def quebrado(*args, **kwargs):
        raise ConnectionError("Neon fora do ar")

    monkeypatch.setattr(sq, "executar_consulta", quebrado)

    assert _consultar("Quais as maiores contratações?", base) == []
    [registro] = [
        json.loads(l)
        for arquivo in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
        for l in arquivo.read_text(encoding="utf-8").splitlines()
    ]
    assert registro["status"] == "erro"
    assert registro["extra"]["error_type"] == "ConnectionError"


def test_sem_documents_nao_consulta_nem_loga(tmp_path):
    assert sq.consultar_estruturado(
        "Quais as maiores contratações?", QueryFilters(), ESTRUTURADA,
        banco=Banco(sqlite_path=tmp_path / "b.sqlite"), documents_file=tmp_path / "nao-existe.jsonl",
    ) == []
    assert not list(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def test_pipeline_poe_resultado_sql_antes_dos_chunks_da_busca(base, monkeypatch):
    vistos = {}
    monkeypatch.setattr(sq, "DOCUMENTS_FILE", base["documents_file"])
    monkeypatch.setattr(sq.structured_db, "banco_padrao", lambda: base["banco"])
    monkeypatch.setattr(
        pipeline, "rotear_pergunta",
        lambda q, **kw: RouteDecision(bases_selecionadas=ESTRUTURADA, justificativa="t", confidence=0.9),
    )
    metadata = ChunkMetadata(source_file="f.json", chunk_id="busca-1", base_id="contratos_estruturado", doc_type="json")
    monkeypatch.setattr(
        pipeline, "retrieve",
        lambda *a, **kw: [type("R", (), {"content": "texto", "metadata": metadata.model_dump()})()],
    )

    def fake_gerar(query, chunks, bases, **kw):
        vistos["ids"] = [c.metadata.chunk_id for c in chunks]
        return RAGResponse(query=query, answer="ok", bases_consultadas=bases, confidence_level=ConfidenceLevel.ALTA)

    monkeypatch.setattr(pipeline, "gerar_resposta", fake_gerar)

    pipeline.responder("Quais as maiores contratações de 2025?")

    assert vistos["ids"][0].startswith("sql:contratacoes:ranking:")
    assert vistos["ids"][-1] == "busca-1"
