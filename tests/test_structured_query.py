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


def test_compilador_postgres_parametriza_filtros_e_mediana():
    consulta = sq.Consulta(
        tabela="pca_fatos", tipo="estatistica", dataset="pca_agregado", versao="v5",
        metrica="mediana", campo="valor_total", agrupamentos=("unidade_nome",),
        referencia_temporal="pca", ano=2025,
        filtros_analiticos=(("unidade_nome", "Saúde'; DROP TABLE pca_fatos; --"),),
    )
    sql, parametros = sq.compilar_consulta_postgres(consulta)
    assert "percentile_cont(0.5)" in sql
    assert "ano_pca BETWEEN %s AND %s" in sql
    assert "GROUP BY unidade_nome" in sql
    assert "DROP" not in sql
    assert parametros == ("v5", 2025, 2025, consulta.filtros_analiticos[0][1], 10)


@pytest.mark.parametrize("opcoes", [
    {"dataset": "emails_sinteticos"}, {"dataset": "pca_agregado"},
    {"campo": "valor; DROP TABLE x"}, {"metrica": "exec"},
    {"agrupamentos": ("senha",)}, {"ano": 2025},
    {"referencia_temporal": "pca"}, {"ordem": "DESC; DROP TABLE x"},
])
def test_compilador_postgres_rejeita_contratos_invalidos(opcoes):
    with pytest.raises(ValueError):
        sq.compilar_consulta_postgres(sq.Consulta(
            tabela="contratos_fatos", tipo="estatistica",
            **({"dataset": "contratos_estruturado"} | opcoes),
        ))


@pytest.mark.parametrize("referencia", ["assinatura", "resultado", "publicacao"])
def test_compilador_preserva_semantica_da_data(referencia):
    sql, parametros = sq.compilar_consulta_postgres(sq.Consulta(
        tabela="contratos_fatos", tipo="estatistica", dataset="contratos_estruturado",
        ano=2024, ano_final=2025, referencia_temporal=referencia,
    ))
    assert f"EXTRACT(YEAR FROM data_{referencia})" in sql
    assert parametros == (2024, 2025, 10)


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
# Filtros vindos do query_analysis
# --------------------------------------------------------------------------


@pytest.mark.parametrize("pergunta", [
    "Qual o valor total dos contratos acima de 500 mil em 2025?",
    "Qual a média dos contratos com valor superior a 2000 em 2025?",
    "Quais os maiores contratos no máximo de 50 mil reais?",
    "Quantos contratos acima de 1 milhão houve por modalidade?",
])
def test_expressao_de_filtro_nao_vira_termo_do_objeto(pergunta):
    from src.query_analysis import _analisar

    consulta = sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA)
    assert consulta.termos_objeto == ()


def test_maior_que_como_filtro_nao_vira_ranking():
    from src.query_analysis import _analisar

    pergunta = "Qual a média dos contratos com valor maior que 2000?"
    assert sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA).tipo == "estatistica"


@pytest.mark.parametrize("filtros, where, parametros", [
    ({"ano": 2025}, "ano_publicacao = ?", [2025]),
    ({"ano": 2024, "ano_final": 2025}, "ano_publicacao >= ? AND ano_publicacao <= ?", [2024, 2025]),
    ({"ano_desde": 2024}, "ano_publicacao >= ?", [2024]),
    ({"ano_ate": 2024}, "ano_publicacao <= ?", [2024]),
])
def test_periodo_vira_where_do_sql(filtros, where, parametros):
    # "contratações": o ano é o da publicação (com "contratos", o da assinatura).
    consulta = sq.detectar_consulta("Qual a mediana das contratações?", QueryFilters(**filtros), ESTRUTURADA)
    sql, valores = sq._where(consulta, ())
    assert sql == f" WHERE {where}"
    assert valores == parametros


def test_periodo_aberto_de_contratos_usa_ano_da_assinatura():
    consulta = sq.detectar_consulta("Qual a mediana dos contratos?", QueryFilters(ano_desde=2024), ESTRUTURADA)
    sql, valores = sq._where(consulta, ())
    assert sql == " WHERE tipo_registro = ? AND ano_assinatura >= ?"
    assert valores == ["contrato_assinado", 2024]


def test_periodo_filtra_as_linhas_do_sql(base):
    [chunk] = _consultar("Qual a mediana das contratações?", base, ano=2024, ano_final=2025)
    assert "ano de 2024 a 2025" in chunk.content
    assert "Geral: 4 registros" in chunk.content


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


def test_postgres_compiles_recurring_supplier_count_and_exclusive_amount():
    from src.structured_query import Consulta, compilar_consulta_postgres
    consulta = Consulta(tabela="contratacoes", tipo="agrupamento", dataset="contratos_estruturado",
                        versao="v6", metrica="contagem", agrupamentos=("fornecedor",),
                        referencia_temporal="assinatura", ano=2025, esfera="estadual",
                        valor_min=500000, valor_min_exclusivo=True, minimo_por_grupo=3, limite=5)
    sql, params = compilar_consulta_postgres(consulta)
    assert "valor > %s" in sql
    assert "GROUP BY fornecedor HAVING COUNT(*) >= %s" in sql
    assert params == ("v6", 2025, 2025, 500000, "estadual", 3, 5)


def test_postgres_groups_months_by_signature_date_and_contracts_by_control_id():
    from src.structured_query import Consulta, compilar_consulta_postgres
    query = Consulta(tabela="contratacoes", tipo="evolucao", dataset="contratos_estruturado",
                     versao="v6", metrica="media", agrupamentos=("mes_assinatura",),
                     referencia_temporal="assinatura", ano=2025)
    sql, params = compilar_consulta_postgres(query)
    assert "EXTRACT(MONTH FROM data_assinatura) AS mes_assinatura" in sql
    assert "GROUP BY EXTRACT(MONTH FROM data_assinatura)" in sql
    assert params[:3] == ("v6", 2025, 2025)
    ranking = Consulta(tabela="contratacoes", tipo="ranking", dataset="contratos_estruturado",
                       metrica="maximo", agrupamentos=("numero_controle_pncp",), limite=10)
    sql, params = compilar_consulta_postgres(ranking)
    assert "GROUP BY numero_controle_pncp" in sql
    assert params == (10,)


def test_legacy_sql_signature_scope_does_not_use_publication_year_or_estimates():
    from src.structured_query import Consulta, _where, _coluna_valor
    consulta = Consulta(tabela="contratacoes", tipo="estatistica", ano=2025,
                        referencia_temporal="assinatura", valor_min=500000, valor_min_exclusivo=True)
    sql, parameters = _where(consulta, ())
    assert "tipo_registro = ?" in sql
    assert "ano_assinatura = ?" in sql and "ano_publicacao" not in sql
    assert "valor_contratado > ?" in sql
    assert parameters == ["contrato_assinado", 2025, 500000]
    assert _coluna_valor(consulta) == "valor_contratado"

def test_fornecedores_ordenados_pela_soma_dos_contratos_assinados(tmp_path):
    registros = []
    for numero, fornecedor, valor in [(1, 'Fornecedor A', 100), (2, 'Fornecedor A', 100), (3, 'Fornecedor B', 900)]:
        registro = _contratacao(f'111-2-{numero:06d}/2025')
        registro.update(dataAssinatura='2025-03-01', valorGlobal=valor, nomeRazaoSocialFornecedor=fornecedor)
        registros.append(registro)
    documents = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / 'base.sqlite')
    structured_db.construir_banco(documents, banco)
    pergunta = 'Quais fornecedores receberam o maior valor total em contratos de 2025?'
    consulta = sq.detectar_consulta(pergunta, QueryFilters(ano=2025), ESTRUTURADA)
    assert consulta.tipo == 'agrupamento'
    assert consulta.agrupar_por == 'fornecedor'
    assert consulta.metrica == 'soma'
    resultado = _consultar(pergunta, {'banco': banco, 'documents_file': documents}, ano=2025)
    assert resultado is not None
    texto = str(resultado)
    assert texto.index('Fornecedor B') < texto.index('Fornecedor A')
    assert '900' in texto and '200' in texto
