"""Consultas fixas sobre a base estruturada (SQLite temporário) e a ligação com o pipeline."""

from __future__ import annotations

import json

import pytest

from src import obs, pipeline, structured_db, structured_query as sq
from src.schema import ChunkMetadata, Natureza, QueryFilters, RAGResponse, ConfidenceLevel, RouteDecision
from src.structured_db import Banco
from tests.test_structured_db import _contratacao, _plano, escrever_documents

ESTRUTURADA = [Natureza.ESTRUTURADA]
_SEM_ANULADAS = "(situacao IS NULL OR situacao NOT IN ('Anulada', 'Revogada', 'Suspensa'))"
AGREGADA = [Natureza.ESTRUTURADA, Natureza.AGREGADA]


def test_valor_contratado_publicado_nao_mistura_estimativas_nem_ano_assinatura(tmp_path):
    from src.query_analysis import analisar_query

    compra = _contratacao("111-1-000001/2025", valor_estimado=1_000_000)
    contrato = _contratacao("111-2-000001/2025")
    contrato.update(valorGlobal=100, dataAssinatura="2024-12-20")
    zero = _contratacao("111-2-000002/2025")
    zero.update(valorGlobal=0, dataAssinatura="2024-12-20")
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[compra, contrato, zero])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    query = "Maiores contratações por valor contratado publicadas em 2025"
    consulta = sq.detectar_consulta(query, analisar_query(query, modo="regras").filtros, ESTRUTURADA)
    sql, cabecalho, linhas = sq.executar_consulta(consulta, banco)
    assert "tipo_registro = ?" in sql
    assert "ano_publicacao = ?" in sql
    assert "tipo_registro=contrato_assinado" in cabecalho
    assert len(linhas) == 1  # zero permanece no banco, mas não entra no ranking monetário
    assert "1.000.000" not in " ".join(linhas)


def test_contagem_tipificada_de_valor_contratado_exclui_publicacoes():
    from src.schema import QueryIntent

    intent = QueryIntent(
        pergunta="Quantos contratos por valor contratado foram publicados em 2025?",
        bases=ESTRUTURADA, operacao="estatistica",
        filtros=QueryFilters(tipo_valor="contratado", metrica="contagem", referencia_temporal="publicacao", ano=2025),
    )
    consulta = sq.consulta_da_intencao(intent)
    sql, parametros = sq._where(consulta, ())
    assert "tipo_registro = ?" in sql
    assert parametros == ["contrato_assinado", 2025]


def test_compilador_offline_valor_contratado_por_mes_publicacao():
    sql, parametros = sq.compilar_consulta_postgres(sq.Consulta(
        tabela="contratos_fatos", tipo="estatistica", dataset="contratos_estruturado",
        campo="valor_contratado", metrica="soma", referencia_temporal="publicacao",
        ano=2025, agrupamentos=("mes_publicacao",),
    ))
    assert "SUM(valor_contratado)" in sql
    assert "EXTRACT(MONTH FROM data_publicacao)" in sql
    assert parametros[:2] == (2025, 2025)


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


def _texto_sql(chunks):
    """Confere o pacote completo, sem pressupor um único chunk de evidência."""
    assert chunks
    assert len({c.metadata.chunk_id for c in chunks}) == len(chunks)
    recorte = chunks[0].metadata.extras["resultado_sql"]["recorte"]
    for indice, chunk in enumerate(chunks, 1):
        extras = chunk.metadata.extras
        assert extras["parte"] == indice and extras["partes"] == len(chunks)
        assert extras["resultado_sql"]["recorte"] == recorte
        assert chunk.metadata.source_file == chunks[0].metadata.source_file
        assert chunk.content.splitlines()[1:] == extras["resultado_sql"]["linhas"]
        assert len(chunk.content) <= sq.config.GENERATION_CHUNK_CHARS
    return "\n".join(c.content for c in chunks)


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


@pytest.mark.parametrize("pergunta", [
    "Qual o valor de todos os contratos de 2025?",
    "Quanto deram os contratos de ambulância somados em 2025?",
])
def test_valor_de_todos_e_somados_pedem_a_soma(pergunta):
    from src.query_analysis import _analisar

    assert sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA).tipo == "estatistica"


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
    # Compras anuladas ficam fora de toda conta (PR #67, João, 21/10).
    assert sql == f" WHERE {_SEM_ANULADAS} AND {where}"
    assert valores == parametros


def test_periodo_aberto_de_contratos_usa_ano_da_assinatura():
    consulta = sq.detectar_consulta("Qual a mediana dos contratos?", QueryFilters(ano_desde=2024), ESTRUTURADA)
    sql, valores = sq._where(consulta, ())
    assert sql == f" WHERE tipo_registro = ? AND {_SEM_ANULADAS} AND ano_assinatura >= ?"
    assert valores == ["contrato_assinado", 2024]


def test_periodo_filtra_as_linhas_do_sql(base):
    chunks = _consultar("Qual a mediana das contratações?", base, ano=2024, ano_final=2025)
    texto = _texto_sql(chunks)
    assert "ano de 2024 a 2025" in texto
    assert texto.count("Geral: 4 registros") == 1
    assert chunks[0].metadata.extras["resultado_sql"]["recorte"]["registros_correspondentes"] == 4


def test_consulta_exata_por_identificador_pncp_e_parametrizada(tmp_path):
    identificador = "00394429000100-2-000183/2025"
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao(
                identificador,
                esfera="F",
                objeto="Transferência de tecnologia do medicamento Darunavir",
                municipio="Rio de Janeiro",
            ),
            _contratacao(
                "00394429000100-2-000184/2025",
                esfera="F",
                objeto="Outro contrato",
                municipio="Rio de Janeiro",
            ),
        ],
    )
    banco = Banco(sqlite_path=tmp_path / "base-exata.sqlite")
    structured_db.construir_banco(documents, banco)
    pergunta = f"Qual órgão, fornecedor e valor do contrato com número de controle PNCP {identificador}?"

    [chunk] = sq.consultar_estruturado(
        pergunta,
        QueryFilters(),
        ESTRUTURADA,
        banco=banco,
        documents_file=documents,
    )

    assert identificador in chunk.content
    assert "ORGAO " + identificador in chunk.content
    assert chunk.content.count("PNCP ") == 1
    sql = chunk.metadata.extras["sql"]
    assert "numero_controle_pncp = ?" in sql
    assert identificador not in sql


def test_consulta_exata_pncp_retorna_sem_resultado_quando_nao_existe(base):
    identificador = "00394429000100-2-000183/2025"
    [chunk] = sq.consultar_estruturado(
        f"Qual órgão do contrato PNCP {identificador}?",
        QueryFilters(),
        ESTRUTURADA,
        **base,
    )

    assert "Nenhum contrato encontrado" in chunk.content


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
    assert sq.detectar_consulta("Quais contratos existem?", QueryFilters(), ESTRUTURADA) is None
    assert sq.detectar_consulta("Qual o valor médio?", QueryFilters(), [Natureza.NORMATIVA]) is None


def test_detecta_identificador_pncp_completo_para_consulta_estruturada():
    identificador = "00394429000100-2-000183/2025"
    consulta = sq.detectar_consulta(
        f"Qual órgão, fornecedor e valor do contrato com número de controle PNCP {identificador}?",
        QueryFilters(),
        ESTRUTURADA,
    )

    assert consulta is not None
    assert consulta.tipo == "identificador"
    assert consulta.numero_controle_pncp == identificador
    sql, parametros = sq._where(consulta, ())
    assert "numero_controle_pncp = ?" in sql
    assert parametros == [identificador]


def test_identificador_pncp_parcial_nao_vira_busca_exata():
    consulta = sq.detectar_consulta(
        "Qual órgão do contrato PNCP 00394429000100-2-000183?",
        QueryFilters(),
        ESTRUTURADA,
    )

    assert consulta is None or consulta.tipo != "identificador"


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


def _base_pca_com_colisao_de_orgao_unidade(tmp_path):
    secretaria = _plano("P-UNIT", valor=3008.0, unidade="SECRET ESTADO SAUDE")
    outro_setor = _plano("P-OTHER", valor=7000.0, unidade="SECRET ESTADO EDUCACAO")
    orgao_secretaria = _plano("P-ORG", valor=9000.0, unidade="UNIDADE ADMINISTRATIVA")
    orgao_secretaria["razaoSocial"] = "SECRETARIA DE ESTADO DE SAUDE"
    documents = escrever_documents(tmp_path / "documents-pca-roles.jsonl",
                                   planos=[secretaria, outro_setor, orgao_secretaria])
    banco = Banco(sqlite_path=tmp_path / "pca-roles.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


@pytest.mark.parametrize("question,role,column,expected", [
    ("Qual o total planejado pela Secretaria de Estado de Saúde no plano de contratações de 2025?",
     "unidade", "unidade_nome", "R$ 3.008,00"),
    ("Qual o total planejado pelo órgão Secretaria de Estado de Saúde no PCA de 2025?",
     "orgao", "orgao_nome", "R$ 9.000,00"),
])
def test_sql_pca_nao_confunde_orgao_com_unidade(question, role, column, expected, tmp_path):
    from src.query_analysis import analisar_query
    base = _base_pca_com_colisao_de_orgao_unidade(tmp_path)
    filters = analisar_query(question, modo="regras").filtros
    consulta = sq.detectar_consulta(question, filters, AGREGADA)
    recorte = {}

    sql, _header, rows = sq.executar_consulta(consulta, base["banco"], recorte)

    assert filters.papel_orgao == role
    assert f"{column} IN" in sql
    assert ("unidade_nome IN" in sql) == (role == "unidade")
    assert "1 registros" in rows[0] and expected in rows[0]
    assert recorte["registros_correspondentes"] == 1


@pytest.mark.parametrize("question,role,column,expected", [
    ("Qual o total planejado pela Secretaria de Estado de Saúde no plano de contratações de 2025?",
     "unidade", "unidade_nome", "R$ 3.008,00"),
    ("Qual o total planejado pelo órgão Secretaria de Estado de Saúde no PCA de 2025?",
     "orgao", "orgao_nome", "R$ 9.000,00"),
])
def test_sql_tipado_pca_filtra_somente_o_papel_resolvido(question, role, column, expected, tmp_path):
    from src.query_analysis import analisar_query
    from src.schema import QueryIntent
    filters = analisar_query(question, modo="regras").filtros
    intent = QueryIntent(pergunta=question, bases=AGREGADA, operacao="estatistica", filtros=filters)
    consulta = sq.consulta_da_intencao(intent)
    base = _base_pca_com_colisao_de_orgao_unidade(tmp_path)
    recorte = {}

    sql, _header, rows = sq.executar_consulta(consulta, base["banco"], recorte)

    assert filters.papel_orgao == role
    assert f"{column} IN" in sql
    assert ("unidade_nome IN" in sql) == (role == "unidade")
    assert ("orgao_nome IN" in sql) == (role == "orgao")
    assert "1 registros" in rows[0] and expected in rows[0]
    assert recorte["registros_correspondentes"] == 1

def test_sql_pca_media_unitaria_calcula_corretamente(tmp_path):
    from src.query_analysis import analisar_query
    from src.schema import QueryIntent
    # Cenário: 2 planos, um com 10 itens (1000 reais) e outro com 20 itens (2000 reais)
    # Média esperada: (1000 + 2000) / (10 + 20) = 100 reais por item
    plano1 = _plano("P-UNIT-1", valor=1000.0, quantidade=10)
    plano2 = _plano("P-UNIT-2", valor=2000.0, quantidade=20)

    documents = escrever_documents(tmp_path / "documents-pca-unit.jsonl",
                                   planos=[plano1, plano2])
    banco = Banco(sqlite_path=tmp_path / "pca-unit.sqlite")
    structured_db.construir_banco(documents, banco)
    base = {"banco": banco, "documents_file": documents}

    question = "Qual o valor médio por item nos planos de contratações de 2025?"
    # Força a métrica para testar o caminho SQL tipado
    filters = analisar_query(question, modo="regras").filtros
    filters.metrica = "media_unitaria"
    filters.tipo_medida = "unitaria"
    filters.granularidade = "item"
    filters.referencia_temporal = "pca"

    intent = QueryIntent(pergunta=question, bases=AGREGADA, operacao="estatistica", filtros=filters)
    consulta = sq.consulta_da_intencao(intent)
    recorte = {}

    sql, _header, rows = sq.executar_consulta(consulta, base["banco"], recorte)

    assert "SUM(valor_total)" in sql and "quantidade_itens" in sql
    assert "R$ 100,00" in rows[0]
    assert "Média por item" in rows[0]


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

    assert "Nenhum registro encontrado com os filtros aplicados" in chunk.content
    assert "média" not in chunk.content


def test_termos_sem_combinacao_exata_caem_para_ou_entre_os_que_existem(base):
    chunks = _consultar("Qual o valor total de ambulâncias e pavimentação?", base)
    texto = _texto_sql(chunks)
    assert "objeto contém ambulancia ou pavimentacao" in texto
    assert texto.count("Geral: 4 registros") == 1
    assert chunks[0].metadata.extras["resultado_sql"]["recorte"]["execucao"] == "parcial"


def test_agrupamento_conta_por_modalidade(base):
    [chunk] = _consultar("Quais modalidades apareceram com mais frequência?", base, ano=2025)

    assert "Pregão - Eletrônico: 2 registros, total R$ 400,00" in chunk.content
    assert "Concorrência - Eletrônica: 1 registros, total R$ 500,00" in chunk.content


def test_evolucao_agrupa_por_mes(base):
    chunks = _consultar("Houve aumento no valor ao longo de 2025?", base, ano=2025)
    texto = "\n".join(chunk.content for chunk in chunks)

    assert texto.index("2025-01:") < texto.index("2025-02:")
    assert "média ponderada por registro" in texto
    assert "Meses sem registros com valor" in texto


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
    structured_db.construir_banco(documents, banco)

    chunks = sq.consultar_estruturado("Quais as quinze maiores contratações?", QueryFilters(), ESTRUTURADA,
                                      banco=banco, documents_file=documents)

    assert len(chunks) > 1
    assert all(len(c.content) <= 400 for c in chunks)
    assert len({c.metadata.chunk_id for c in chunks}) == len(chunks)
    assert all(c.metadata.doc_type == "sql" and c.metadata.chunk_id.startswith("sql:") for c in chunks)
    assert chunks[1].content.startswith("(continuação da consulta SQL")
    _texto_sql(chunks)
    linhas = [linha for c in chunks for linha in c.metadata.extras["resultado_sql"]["linhas"]]
    assert len(linhas) == 15
    assert [linha.split(". ", 1)[0] for linha in linhas] == [str(i) for i in range(1, 16)]
    assert "R$ 15,00" in linhas[0] and "R$ 1,00" in linhas[-1]


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
    # O que a consulta devolveu também vai para o log (rastreabilidade).
    assert len(registro["extra"]["resultado"]) == 3
    assert registro["extra"]["linhas_fora_do_log"] == 0
    assert registro["extra"]["cabecalho"]
    assert all(chunk_id.startswith("sql:") for chunk_id in registro["extra"]["chunk_ids"])


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


def test_pipeline_misto_poe_resultado_sql_antes_dos_chunks_da_busca(base, monkeypatch):
    vistos = {}
    bases_mistas = ESTRUTURADA + [Natureza.NORMATIVA]
    monkeypatch.setattr(sq, "DOCUMENTS_FILE", base["documents_file"])
    monkeypatch.setattr(sq.structured_db, "banco_padrao", lambda: base["banco"])
    monkeypatch.setattr(
        pipeline, "rotear_pergunta",
        lambda q, **kw: RouteDecision(bases_selecionadas=bases_mistas, justificativa="t", confidence=0.9),
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


# --------------------------------------------------------------------------
# Regressões apontadas na revisão da Pâmela (30/09): palavra comum, modalidade,
# esfera, agrupamento, tabela, PCA no plural, mês, contagem e órgão.
# --------------------------------------------------------------------------


def _detectar(pergunta, bases=ESTRUTURADA, **filtros):
    return sq.detectar_consulta(pergunta, QueryFilters(**filtros), bases)


def test_palavras_comuns_nao_viram_termo_do_objeto():
    consulta = _detectar("Quantas contratações foram publicadas e assinadas em 2025, comparando a data?", ano=2025)

    assert consulta.termos_objeto == ()


def test_modalidade_vira_filtro_de_coluna_e_nao_termo_do_objeto(base):
    consulta = _detectar("Quantas contratações por pregão eletrônico em 2025?", ano=2025)
    assert consulta.modalidade_termos == ("pregao", "eletronic")
    assert consulta.termos_objeto == ()

    [chunk] = _consultar("Quantas contratações por pregão eletrônico em 2025?", base, ano=2025)
    assert "modalidade em Pregão - Eletrônico" in chunk.content
    assert "Geral: 2 registros" in chunk.content  # M-1 e M-2; E-1 é concorrência e F-1 é de 2024


def test_modalidade_que_nao_existe_no_corpus_diz_isso(base):
    [chunk] = _consultar("Quantas contratações por leilão em 2025?", base, ano=2025)

    assert "nenhum registro do corpus tem essa modalidade" in chunk.content
    assert "0 registros" in chunk.content


def test_federais_vira_filtro_de_esfera(base):
    consulta = _detectar("Qual o total das contratações federais?")
    assert consulta.esfera == "federal"
    assert consulta.termos_objeto == ()

    [chunk] = _consultar("Qual o total das contratações federais?", base)
    assert "esfera=federal" in chunk.content
    assert "Geral: 1 registros" in chunk.content


def test_agrupar_por_municipios_nao_filtra_so_os_municipais():
    consulta = _detectar("Quantas contratações por municípios?")

    assert consulta.tipo == "agrupamento"
    assert consulta.agrupar_por == "municipio"
    assert consulta.esfera is None


def test_valor_contratado_vai_para_contratacoes_mesmo_so_com_a_agregada():
    consulta = _detectar("Qual o valor total contratado em 2025?", bases=[Natureza.AGREGADA], ano=2025)

    assert consulta.tabela == "contratacoes"


@pytest.mark.parametrize("pergunta", [
    "Qual o valor total dos planos anuais de contratação de 2025?",
    "Qual o valor total dos planos anuais de 2025?",
])
def test_planos_anuais_no_plural_sao_pca(pergunta):
    assert _detectar(pergunta, bases=AGREGADA, ano=2025).tabela == "pca_planos"


def test_mes_da_pergunta_filtra_o_mes_e_rio_de_janeiro_nao_e_mes(base):
    assert _detectar("Qual a média das contratações de janeiro de 2025?", ano=2025).mes == 1
    assert _detectar("Qual a média das contratações do Rio de Janeiro em 2025?", ano=2025).mes is None

    [chunk] = _consultar("Qual a média das contratações de janeiro de 2025?", base, ano=2025)
    assert "mês=01/2025" in chunk.content
    assert "Geral: 1 registros" in chunk.content  # só M-1 é de janeiro


def test_contagem_inclui_registros_sem_valor(tmp_path):
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao("A-1", valor_estimado=100.0),
            _contratacao("A-2", valor_estimado=None),
        ],
    )
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)

    [chunk] = sq.consultar_estruturado(
        "Quantas contratações em 2025?", QueryFilters(ano=2025), ESTRUTURADA,
        banco=banco, documents_file=documents,
    )

    assert "2 registros no total, 1 sem valor informado" in chunk.content
    assert "1 registros; total R$ 100,00" in chunk.content


def test_filtro_de_orgao_chega_ao_sql_sem_virar_objeto(base):
    consulta = _detectar("Qual o total planejado pela Educação no PCA 2025?", bases=AGREGADA, ano=2025,
                         orgao="Educação no PCA")
    assert consulta.orgao_termos == ("educacao",)

    [chunk] = _consultar("Qual o total planejado pela Educação no PCA 2025?", base, bases=AGREGADA, ano=2025,
                         orgao="Educação no PCA")
    assert "órgão em Educação" in chunk.content
    assert "total R$ 3.000,00" in chunk.content


def test_orgao_que_nao_existe_no_corpus_diz_isso(base):
    [chunk] = _consultar("Qual o total planejado pela Defesa Civil no PCA 2025?", base, bases=AGREGADA, ano=2025,
                         orgao="Defesa Civil")

    assert "nenhum órgão do corpus casa com esse nome" in chunk.content


@pytest.mark.parametrize("nome", ["SECRETARIA DE ESTADO DE SAUDE", "SECRET ESTADO SAÚDE"])
def test_nome_de_orgao_casa_sem_acento_caixa_e_com_abreviacao(nome):
    assert sq._palavras_casam(("secretaria", "saude"), nome)
    assert not sq._palavras_casam(("secretaria", "educacao"), nome)


def test_listagem_devolve_registros_com_orgao_fornecedor_e_modalidade(base):
    consulta = _detectar("Quais contratos de ambulâncias foram publicados?")
    assert consulta.tipo == "listagem"

    chunks = _consultar("Quais contratações de ambulâncias existem em 2025?", base, ano=2025)
    texto = _texto_sql(chunks)
    assert "3 registros atendem aos filtros" in texto  # M-1, M-2 e E-1
    assert "fornecedor:" in texto and "modalidade: Concorrência - Eletrônica" in texto
    linhas = [linha for c in chunks for linha in c.metadata.extras["resultado_sql"]["linhas"]]
    assert len(linhas) == 4  # resumo + três registros, sem perda na divisão
    assert sum("PNCP " in linha for linha in linhas) == 3


def test_listagem_multi_base_isola_objeto_da_clausula_de_contratacoes():
    pergunta = (
        "Quais regras se aplicam à participação de microempresas e quais contratações "
        "de material de escritório do corpus usam modalidades compatíveis com essas regras?"
    )

    consulta = _detectar(pergunta, bases=[Natureza.NORMATIVA, Natureza.ESTRUTURADA])

    assert consulta.tipo == "listagem"
    assert consulta.termos_objeto == ("material", "escritorio")


def test_listagem_multi_base_retira_so_os_registros_do_objeto_relevante(tmp_path):
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao("OFF-1", objeto="Material de escritório", modalidade="Pregão - Eletrônico"),
            _contratacao("OFF-2", objeto="Material de escritório e papelaria", modalidade="Dispensa"),
            _contratacao("OTHER", objeto="Material de construção", modalidade="Concorrência"),
        ],
    )
    banco = Banco(sqlite_path=tmp_path / "base-listagem-multibase.sqlite")
    structured_db.construir_banco(documents, banco)
    pergunta = (
        "Quais regras se aplicam à participação de microempresas e quais contratações "
        "de material de escritório do corpus usam modalidades compatíveis com essas regras?"
    )

    [chunk] = sq.consultar_estruturado(
        pergunta,
        QueryFilters(),
        [Natureza.NORMATIVA, Natureza.ESTRUTURADA],
        banco=banco,
        documents_file=documents,
    )

    assert chunk.metadata.base_id == "contratos_estruturado"
    assert chunk.metadata.natureza == Natureza.ESTRUTURADA
    assert "2 registros atendem aos filtros" in chunk.content
    assert "Pregão - Eletrônico" in chunk.content
    assert "Dispensa" in chunk.content
    assert "Material de construção" not in chunk.content


def test_listagem_so_roda_com_base_estruturada_e_assunto():
    pergunta = "Quais contratações de ambulâncias existem?"
    assert _detectar(pergunta, bases=[Natureza.NORMATIVA, Natureza.ESTRUTURADA]).tipo == "listagem"
    assert _detectar(pergunta, bases=[Natureza.NORMATIVA]) is None
    assert _detectar(
        "Quais regras se aplicam às microempresas?",
        bases=[Natureza.NORMATIVA, Natureza.ESTRUTURADA],
    ) is None
    # Período explícito já delimita uma listagem SQL verificável; não precisa
    # inventar assunto nem pagar busca vetorial para trazer exemplos aleatórios.
    assert _detectar("Quais contratos existem em 2025?", ano=2025).tipo == "listagem"
    assert _detectar("Quais contratos existem?") is None


def test_contagem_pelo_menos_n_contratos_cria_agregacao_por_fornecedor():
    from src.query_analysis import analisar_query

    pergunta = (
        "Considerando os fornecedores que apareceram em pelo menos três contratos de "
        "tecnologia de órgãos estaduais do Rio de Janeiro em 2025, com valor acima de "
        "R$ 500 mil, há atas ou registros no corpus que mencionem atraso de entrega?"
    )
    filtros = analisar_query(pergunta).filtros

    consulta = sq.detectar_consulta(
        pergunta,
        filtros,
        [Natureza.ESTRUTURADA, Natureza.CONVERSACIONAL],
    )

    assert consulta.tipo == "agrupamento"
    assert consulta.agrupar_por == "fornecedor"
    assert consulta.minimo_por_grupo == 3
    assert consulta.termos_objeto == ("tecnologia",)
    assert consulta.ano == 2025
    assert consulta.esfera == "estadual"
    assert consulta.valor_min == 500000
    assert consulta.valor_min_exclusivo is True
    where, params = sq._where(consulta, consulta.termos_objeto)
    assert "valor_contratado > ?" in where
    assert 500000 in params


def test_palavra_solta_da_pergunta_nao_entra_no_ou(base):
    texto = _texto_sql(_consultar("Qual o total de ambulâncias e pavimentação desse período?", base))
    assert "objeto contém ambulancia ou pavimentacao" in texto
    assert "objeto contém periodo" not in texto


def test_agente_da_passiva_nao_vira_agrupamento():
    consulta = _detectar("Qual o total das contratações publicadas por órgãos públicos em 2025?", ano=2025)
    assert consulta.tipo == "estatistica"
    assert _detectar("Qual o total por órgão em 2025?", ano=2025).agrupar_por == "orgao_nome"


def test_minimo_de_contratos_por_fornecedor(base):
    consulta = _detectar("Quais fornecedores têm três ou mais contratações de ambulância?")
    assert (consulta.tipo, consulta.agrupar_por, consulta.minimo_por_grupo) == ("agrupamento", "fornecedor", 3)

    [chunk] = _consultar("Quais fornecedores têm três ou mais contratações de ambulância?", base)
    assert "Nenhum fornecedor tem 3 ou mais registros" in chunk.content


def test_agrupamento_por_modalidade_deixa_sem_modalidade_fora_dos_grupos(tmp_path):
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao("A-1", modalidade="Pregão - Eletrônico"),
            _contratacao("A-2", modalidade=None),
            _contratacao("A-3", modalidade=None),
        ],
    )
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)

    [chunk] = sq.consultar_estruturado(
        "Quais modalidades são as mais usadas?", QueryFilters(), ESTRUTURADA, banco=banco, documents_file=documents
    )

    assert "Pregão - Eletrônico: 1 registros" in chunk.content
    assert "não informado" not in chunk.content
    assert "(2 registros sem informação de modalidade ficaram fora dos grupos.)" in chunk.content


def _base_de_contratos(tmp_path, contratos):
    registros = []
    for numero, (assinatura, publicacao, valor) in enumerate(contratos, 1):
        registro = _contratacao(f"111-2-{numero:06d}/2025", data=publicacao)
        registro.update(dataAssinatura=assinatura, valorGlobal=valor, nomeRazaoSocialFornecedor="F")
        registros.append(registro)
    # Uma compra publicada em março, que não é contrato.
    registros.append(_contratacao("C-1", data="2025-03-15T00:00:00", valor_estimado=50.0))
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def test_contratos_publicados_contam_pela_data_de_publicacao(tmp_path):
    base = _base_de_contratos(tmp_path, [
        ("2025-02-20", "2025-03-02T00:00:00", 100.0),  # assinado em fevereiro, publicado em março
        ("2025-03-10", "2025-03-12T00:00:00", 200.0),
    ])
    pergunta = "Quantos contratos foram publicados em março de 2025?"
    consulta = sq.detectar_consulta(pergunta, QueryFilters(ano=2025, referencia_temporal="publicacao"), ESTRUTURADA)
    assert (consulta.referencia_temporal, consulta.so_contratos) == ("publicacao", True)

    [chunk] = _consultar(pergunta, base, ano=2025, referencia_temporal="publicacao")
    assert "Geral: 2 registros" in chunk.content  # os dois contratos; a compra fica fora

    [chunk] = _consultar("Quantos contratos foram assinados em março de 2025?", base,
                         ano=2025, referencia_temporal="assinatura")
    assert "Geral: 1 registros" in chunk.content


def test_valor_simbolico_nao_entra_na_mediana_nem_no_ranking(tmp_path):
    base = _base_de_contratos(tmp_path, [
        ("2025-03-10", "2025-03-12T00:00:00", 0.0),
        ("2025-03-09", "2025-03-12T00:00:00", 0.01),
        ("2025-03-11", "2025-03-12T00:00:00", 100.0),
        ("2025-03-12", "2025-03-12T00:00:00", 300.0),
    ])
    [chunk] = _consultar("Qual a mediana dos contratos de ambulância em 2025?", base, ano=2025)
    assert "4 registros no total, 2 com valor abaixo de R$ 1,00" in chunk.content
    assert "total R$ 400,01" in chunk.content
    assert "estatísticas sobre 2 valores >= R$ 1,00" in chunk.content
    assert "mediana R$ 200,00" in chunk.content

    [chunk] = _consultar("Quais os menores contratos de ambulância em 2025?", base, ano=2025)
    assert "R$ 0,00" not in chunk.content and "R$ 0,01" not in chunk.content


def test_orgao_nao_reconhecido_vira_filtro_de_orgao_pelos_termos(tmp_path):
    registros = []
    for numero, (orgao, objeto, valor) in enumerate([
        ("COMANDO DA AERONAUTICA", "Aquisição de medicamentos", 1000.0),
        ("COMANDO DA AERONAUTICA", "Serviço de limpeza", 500.0),
        ("MUNICIPIO X", "Manutenção de aeronave do comando de bombeiros", 70.0),
    ], 1):
        registro = _contratacao(f"111-2-{numero:06d}/2025")
        registro.update(dataAssinatura="2025-03-01", valorGlobal=valor, nomeRazaoSocialFornecedor="F")
        registro["orgaoEntidade"]["razaoSocial"] = orgao
        registro["objetoCompra"] = objeto
        registros.append(registro)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)

    # O query_analysis não extraiu órgão (QueryFilters sem orgao).
    [chunk] = _consultar("Quanto o comando da aeronáutica contratou em 2025?",
                         {"banco": banco, "documents_file": documents}, ano=2025)
    assert "órgão em COMANDO DA AERONAUTICA" in chunk.content
    assert "Geral: 2 registros; total R$ 1.500,00" in chunk.content


def _base_com_objetos(tmp_path, objetos):
    registros = []
    for numero, (objeto, valor) in enumerate(objetos, 1):
        registro = _contratacao(f"T-{numero}", objeto=objeto, valor_estimado=valor, modalidade=None)
        registros.append(registro)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def test_tecnologia_quer_dizer_ti_e_nao_transferencia_de_tecnologia(tmp_path):
    base = _base_com_objetos(tmp_path, [
        ("Transferência de tecnologia do medicamento Darunavir", 1_000_000.0),
        ("Cateteres de tecnologia IVUS para hemodinâmica", 500.0),
        ("Solução de Tecnologia da Informação e Comunicação (TIC)", 300.0),
        ("Licença de uso de software de gestão", 100.0),
    ])
    [chunk] = _consultar("Qual o total das compras de tecnologia em 2025?", base, ano=2025)

    assert "Geral: 2 registros; total R$ 400,00" in chunk.content
    assert "tecnologia (TI:" in chunk.content


def test_modalidade_mais_frequente_sem_nenhuma_modalidade_informada(tmp_path):
    base = _base_com_objetos(tmp_path, [("Manutenção de computadores", 100.0), ("Manutenção de computadores", 200.0)])
    [chunk] = _consultar("Quais modalidades apareceram com mais frequência nas contratações de manutenção de computadores?", base)

    assert "Nenhum dos 2 registros que atendem aos filtros informa modalidade" in chunk.content
    assert "não é possível dizer" in chunk.content


def _base_compra_e_contrato(tmp_path):
    compra = _contratacao("111-1-000001/2025", objeto="Locação de ambulâncias", valor_estimado=1000.0,
                          modalidade="Pregão - Eletrônico")
    contrato = _contratacao("111-2-000001/2025", objeto="Locação de ambulâncias", modalidade=None)
    contrato.update(dataAssinatura="2025-03-01", valorGlobal=900.0, nomeRazaoSocialFornecedor="F",
                    numeroControlePncpCompra="111-1-000001/2025")
    outra = _contratacao("111-1-000002/2025", objeto="Ambulância de resgate", valor_estimado=50.0,
                         modalidade="Dispensa")
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[compra, contrato, outra])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def test_compra_e_contrato_da_mesma_contratacao_contam_uma_vez(tmp_path):
    base = _base_compra_e_contrato(tmp_path)
    [chunk] = _consultar("Qual o total das compras de ambulância em 2025?", base, ano=2025)

    # Sem deduplicar seriam 3 registros (1.000 + 900 + 50); fica o contrato.
    assert "Geral: 2 registros; total R$ 950,00" in chunk.content
    assert "Contratos/empenhos distintos são preservados" in chunk.content


def test_pergunta_de_modalidade_mantem_a_compra_e_tira_o_contrato(tmp_path):
    base = _base_compra_e_contrato(tmp_path)
    [chunk] = _consultar("Qual a modalidade mais usada nas compras de ambulância em 2025?", base, ano=2025)

    assert "Pregão - Eletrônico: 1 registros" in chunk.content
    assert "Dispensa: 1 registros" in chunk.content
    assert "sem informação de modalidade" not in chunk.content  # o contrato duplicado saiu


def test_base_sem_a_coluna_nova_roda_sem_deduplicar(tmp_path):
    base = _base_compra_e_contrato(tmp_path)
    import sqlite3
    with sqlite3.connect(base["banco"].sqlite_path) as conexao:
        conexao.execute("ALTER TABLE contratacoes DROP COLUMN numero_controle_compra")
    consulta = sq.detectar_consulta("Qual o total das compras de ambulância em 2025?", QueryFilters(ano=2025), ESTRUTURADA)

    assert sq._resolver_valores(consulta, base["banco"]).deduplicar is False


def test_vinculo_fora_do_periodo_nao_apaga_compra_do_recorte(tmp_path):
    from contextlib import closing
    base = _base_compra_e_contrato(tmp_path)
    with closing(base["banco"].conectar(somente_leitura=False)) as connection, connection:
        connection.execute("UPDATE contratacoes SET ano_publicacao=2024 WHERE tipo_registro='contrato_assinado'")
    recorte = {}
    sq.executar_consulta(sq.Consulta(tabela="contratacoes", tipo="estatistica", ano=2025,
                                   metrica="contagem"), base["banco"], recorte)
    assert recorte["registros_correspondentes"] == 2


def test_contagem_um_para_muitos_preserva_contratos_e_distingue_empenho(tmp_path):
    compra = _contratacao("111-1-000001/2025")
    instrumentos = []
    for numero in range(1, 4):
        contrato = _contratacao(f"111-2-{numero:06d}/2025", modalidade=None)
        contrato.update(dataAssinatura="2025-03-01", valorGlobal=100 * numero,
                        numeroControlePncpCompra=compra["numeroControlePNCP"])
        instrumentos.append(contrato)
    docs = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[compra, *instrumentos])
    rows = [json.loads(line) for line in docs.read_text(encoding="utf-8").splitlines()]
    rows[-1]["extras"] = {"tipo_instrumento": "empenho"}
    docs.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(docs, banco)
    for opcoes, esperado in [({"so_contratos": True}, 2),
                             ({"filtros_analiticos": (("tipo_registro", "empenho"),)}, 1)]:
        recorte = {}
        sq.executar_consulta(sq.Consulta(tabela="contratacoes", tipo="estatistica", metrica="contagem",
                                       **opcoes), banco, recorte)
        assert recorte["registros_correspondentes"] == esperado


def test_orgao_com_estado_e_rio_nao_casa_com_orgao_federal_nem_com_outra_prefeitura(tmp_path):
    registros = []
    for numero, (orgao, unidade) in enumerate([
        ("UNIVERSIDADE FEDERAL DO ESTADO DO RIO DE JANEIRO", "MEC-UNIRIO"),
        ("MUNICIPIO DE RIO DE JANEIRO", "MRJ-PREFEITURA MUNICIPAL DO RIO DE JANEIRO"),
        ("MUNICIPIO DE NITEROI", "MRJ- PREFEITURA MUNICIPAL DE NITEROI"),
    ], 1):
        registro = _contratacao(f"111-2-{numero:06d}/2025")
        registro.update(dataAssinatura="2025-03-01", valorGlobal=100.0 * numero, nomeRazaoSocialFornecedor="F")
        registro["orgaoEntidade"]["razaoSocial"] = orgao
        registro["unidadeOrgao"]["nomeUnidade"] = unidade
        registros.append(registro)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    base = {"banco": banco, "documents_file": documents}

    [chunk] = _consultar("Quanto a UERJ contratou em 2025?", base, ano=2025,
                         orgao="Universidade do Estado do Rio de Janeiro")
    assert "nenhum órgão do corpus casa" in chunk.content

    [chunk] = _consultar("Quanto a Prefeitura do Rio de Janeiro contratou em 2025?", base, ano=2025,
                         orgao="Prefeitura do Rio de Janeiro")
    assert "Geral: 1 registros; total R$ 200,00" in chunk.content


def test_citacao_de_lei_nao_vira_esfera_e_vale_o_filtro_do_query_analysis():
    pergunta = "Quantas compras com base na Lei federal 14.133 foram publicadas em 2025?"
    assert _detectar(pergunta, ano=2025).esfera is None

    consulta = _detectar("Quantos contratos estaduais em 2025?", ano=2025,
                         filtros_analiticos={"esfera": "municipal"})
    assert consulta.esfera == "municipal"  # o query_analysis decide


def test_modalidade_no_plural_vem_do_query_analysis_e_sai_do_objeto():
    consulta = _detectar("Quantos pregões eletrônicos foram publicados em 2025?", ano=2025,
                         filtros_analiticos={"modalidade": "pregao eletronico"})
    assert consulta.modalidade_termos == ("pregao", "eletronic")
    assert consulta.termos_objeto == ()


@pytest.mark.parametrize("pergunta", [
    "Quantos contratos seguem a Lei federal 14.133 em 2025?",
    "Quantos contratos fundamentados na Lei 14.133 foram assinados em 2025?",
    "Quantos contratos baseados no Decreto 12.343/2024 em 2025?",
])
def test_citacao_de_norma_nao_vira_objeto_e_o_resultado_avisa(pergunta):
    from src.query_analysis import _analisar

    consulta = sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA)
    assert consulta.termos_objeto == ()
    assert consulta.esfera is None
    assert consulta.norma_citada
    assert "NÃO é filtro" in sq._cabecalho(consulta, 10, (), "AND")


def test_plano_typed_aplica_orgao_periodo_e_limite_exclusivo(tmp_path):
    from src.schema import QueryIntent
    registros = []
    for numero, ano, valor in [(1, 2024, 500), (2, 2025, 600), (3, 2026, 900)]:
        r = _contratacao(f'111-2-{numero:06d}/{ano}')
        r.update(dataAssinatura=f'{ano}-02-01', valorGlobal=valor, nomeRazaoSocialFornecedor='Fornecedor X')
        r['orgaoEntidade']['razaoSocial'] = 'SECRETARIA ALFA'
        registros.append(r)
    docs = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / 'base.sqlite')
    structured_db.construir_banco(docs, banco)
    intent = QueryIntent(pergunta='liste contratos', bases=ESTRUTURADA, operacao='ranking', filtros=QueryFilters(
        ano=2024, ano_final=2025, orgao='ALFA', valor_min=500, valor_min_exclusivo=True, referencia_temporal='assinatura'))
    resultado = sq.consultar_estruturado(intent.pergunta, intent.filtros, intent.bases, intencao=intent, banco=banco, documents_file=docs)
    texto = ' '.join(c.content for c in resultado)
    assert 'R$ 600,00' in texto
    assert '1. R$ 500,00' not in texto and 'R$ 900,00' not in texto


def test_media_periodo_ponderada_exclui_valor_simbolico(tmp_path):
    registros = []
    for n, mes, valor in [(1, '01', 0), (2, '02', 100), (3, '02', 200)]:
        r = _contratacao(f'111-2-{n:06d}/2025')
        r.update(dataAssinatura=f'2025-{mes}-01', valorGlobal=valor)
        registros.append(r)
    docs = escrever_documents(tmp_path / 'documents.jsonl', contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / 'base.sqlite')
    structured_db.construir_banco(docs, banco)
    _, _, linhas = sq.executar_consulta(sq.Consulta(tabela='contratacoes', tipo='evolucao', referencia_temporal='assinatura', ano=2025), banco)
    texto = ' '.join(linhas)
    assert 'média R$ 150,00' in linhas[0]
    assert 'percentual indefinido (base zero)' not in texto
    assert '2025-01' in texto
    assert '2025-03' in texto and 'não são valores zero' in texto


def test_plano_unitario_nao_calcula_com_valores_globais():
    from src.schema import QueryIntent
    intent = QueryIntent(pergunta='média por ambulância', bases=AGREGADA, operacao='estatistica', filtros=QueryFilters(tipo_valor='unitario', metrica='media'))
    assert sq.consulta_da_intencao(intent) is None


def test_contagem_tipado_inclui_registro_sem_valor(base):
    from src.schema import QueryIntent
    intent = QueryIntent(pergunta='quantas publicações em 2025?', bases=ESTRUTURADA, operacao='estatistica', filtros=QueryFilters(ano=2025, referencia_temporal='publicacao', metrica='contagem'))
    result = sq.consultar_estruturado(intent.pergunta, intent.filtros, intent.bases, intencao=intent, **base)
    assert 'Contagem no recorte solicitado: 3 registros' in ' '.join(c.content for c in result)


def test_mediana_agrupada_por_esfera(base):
    consulta = sq.Consulta(tabela='contratacoes', tipo='agrupamento', agrupar_por='esfera', metrica='mediana', ano=2025)
    _, _, rows = sq.executar_consulta(consulta, base['banco'])
    text = ' '.join(rows)
    assert 'mediana R$ 200,00' in text and 'mediana R$ 500,00' in text


def test_lista_publicada_por_orgaos_nao_dispara_agrupamento():
    from src.query_analysis import analisar_query
    question = 'Quais contratações de ambulâncias tipo D foram publicadas por órgãos públicos do Rio de Janeiro em 2025?'
    filters = analisar_query(question, modo='regras').filtros
    consulta = sq.detectar_consulta(question, filters, ESTRUTURADA)
    assert consulta.tipo == 'listagem'
    assert consulta.agrupar_por == 'modalidade'


def test_mediana_por_veiculo_nao_compila_calculo_sobre_global():
    from src.query_analysis import analisar_query
    question = 'Compare a mediana do valor por veículo em ambulâncias tipo D em 2025.'
    filters = analisar_query(question, modo='regras').filtros
    assert filters.tipo_valor == 'unitario'
    assert filters.tipo_medida == 'unitaria' and filters.granularidade == 'item'
    consulta = sq.detectar_consulta(question, filters, ESTRUTURADA + AGREGADA)
    assert consulta.tipo == 'listagem'
    assert "tipo d" in consulta.termos_objeto
    assert "ambulancia" in consulta.termos_objeto
    assert "veiculo" not in consulta.termos_objeto
    assert not sq.pode_consultar_sql_direto(question, filters, ESTRUTURADA + AGREGADA)


def test_consulta_unitaria_p2_2_lista_o_contrato_tipo_d_com_valor_total_zero(tmp_path):
    from src.query_analysis import analisar_query

    question = (
        'Considerando os registros de ambulância tipo D disponíveis no corpus para municípios '
        'e órgãos estaduais do Rio de Janeiro em 2025, qual é a mediana do valor por veículo '
        'em cada grupo e o que os dados mostram sobre a comparação? Se houver insuficiência '
        'de dados, diga isso explicitamente.'
    )
    contrato = {
        "numeroControlePNCP": "3303302-2-000001/2025",
        "dataPublicacaoPncp": "2025-03-10T10:00:00",
        "dataAssinatura": "2025-03-10",
        "objetoContrato": "REMOÇÃO DE PACIENTES EM AMBULÂNCIA TIPO D",
        "valorGlobal": 0.0,
        "nomeRazaoSocialFornecedor": "PROTEGER SERVIÇOS LTDA",
        "orgaoEntidade": {
            "cnpj": "111", "razaoSocial": "FUNDO MUNICIPAL DE SAUDE",
            "esferaId": "M", "poderId": "E",
        },
        "unidadeOrgao": {
            "ufSigla": "RJ", "municipioNome": "Niterói", "codigoIbge": "3303302",
            "nomeUnidade": "FUNDO MUNICIPAL DE SAUDE",
        },
    }
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[contrato])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    filtros = analisar_query(question, modo="regras").filtros
    consulta = sq.detectar_consulta(question, filtros, AGREGADA)

    assert consulta.tipo == "listagem"
    assert "tipo d" in consulta.termos_objeto
    assert "ambulancia" in consulta.termos_objeto
    assert "veiculo" not in consulta.termos_objeto
    chunks = sq.consultar_estruturado(
        question, filtros, AGREGADA, banco=banco, documents_file=documents
    )

    assert len(chunks) == 1
    texto = chunks[0].content
    assert "3303302-2-000001/2025" in texto
    assert "FUNDO MUNICIPAL DE SAUDE" in texto
    assert "PROTEGER SERVIÇOS LTDA" in texto
    assert "R$ 0,00" in texto
    assert chunks[0].metadata.extras["calculo_disponivel"] is False
    recorte = chunks[0].metadata.extras["resultado_sql"]["recorte"]
    assert recorte["tipo_resultado"] == "calculo_indisponivel"
    assert "preço por unidade" in recorte["calculo_motivo"]
    assert "valor total do contrato não pode substituí-lo" in recorte["calculo_motivo"]
    from src.generate import _confianca_do_recorte
    from src.schema import ConfidenceLevel
    confianca, _ = _confianca_do_recorte(recorte)
    assert confianca == ConfidenceLevel.BAIXA

    from src import generate
    chamadas_llm = []

    def cliente_teste(mensagens):
        chamadas_llm.append(mensagens)
        raise AssertionError("A resposta unitária indisponível deve ser determinística.")

    resposta = generate.gerar_resposta(
        question, chunks, AGREGADA, cliente=cliente_teste,
    )
    assert chamadas_llm == []
    assert "A consulta encontrou um registro correspondente" in resposta.answer
    assert "R$ 0,00" in resposta.answer
    assert "valor total/global registrado, não o preço por veículo" in resposta.answer
    assert "preço por veículo não está informado" in resposta.answer
    assert "não é possível calcular a mediana solicitada" in resposta.answer
    assert resposta.confidence_level == ConfidenceLevel.BAIXA


def test_mediana_unitaria_tipificada_recupera_registros_sem_agregar_valor_global():
    from src.schema import QueryIntent

    intent = QueryIntent(
        pergunta="Qual a mediana do valor por ambulância tipo D?",
        bases=AGREGADA,
        operacao="estatistica",
        termos_objeto=["ambulancia", "tipo d"],
        filtros=QueryFilters(
            tipo_valor="unitario", tipo_medida="unitaria", granularidade="item",
            metrica="mediana",
        ),
    )
    consulta = sq.consulta_da_intencao(intent)

    assert consulta.tipo == "listagem"


def test_preco_unitario_homologado_nao_compila_soma_global():
    from src.query_analysis import analisar_query
    from src.schema import QueryIntent
    question = 'Qual a mediana do preço unitário homologado de serviço de engenharia?'
    filters = analisar_query(question, modo='regras').filtros
    intent = QueryIntent(pergunta=question, bases=AGREGADA, operacao='estatistica', filtros=filters)

    assert filters.tipo_valor == 'homologado'
    assert filters.tipo_medida == 'unitaria' and filters.granularidade == 'item'
    assert sq.consulta_da_intencao(intent) is None
    assert sq.detectar_consulta(question, filters, ESTRUTURADA + AGREGADA) is None


def test_plano_llm_rejeita_preco_unitario_sem_grao_de_item():
    from src.schema import QueryIntent
    with pytest.raises(ValueError, match='grão de item'):
        QueryIntent(pergunta='preço unitário', bases=AGREGADA, operacao='estatistica',
                    filtros=QueryFilters(tipo_medida='unitaria', metrica='mediana'))


# --------------------------------------------------------------------------
# Filtro por CNPJ (do órgão ou do fornecedor)
# --------------------------------------------------------------------------

def _contrato(numero, ni_fornecedor, fornecedor, valor, cnpj_orgao="29131075000193"):
    return {
        "numeroControlePNCP": numero, "anoContrato": 2025, "dataAssinatura": "2025-05-10",
        "dataPublicacaoPncp": "2025-05-12T00:00:00", "objetoContrato": "Serviços de consultoria",
        "valorGlobal": valor, "niFornecedor": ni_fornecedor, "nomeRazaoSocialFornecedor": fornecedor,
        "orgaoEntidade": {"cnpj": cnpj_orgao, "razaoSocial": "MUNICIPIO DE MARICA", "esferaId": "M"},
        "unidadeOrgao": {"ufSigla": "RJ", "municipioNome": "Maricá", "nomeUnidade": "U"},
    }


@pytest.fixture
def base_cnpj(tmp_path):
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=[
        _contrato("29131075000193-2-000001/2025", "05340639000130", "PRIME CONSULTORIA", 1000.0),
        _contrato("29131075000193-2-000002/2025", "05340639000130", "PRIME CONSULTORIA", 3000.0),
        _contrato("30381552000158-2-000003/2025", "15064270000133", "OUTRA LTDA", 500.0,
                  cnpj_orgao="30381552000158"),
        _contrato("30381552000158-2-000004/2025", "12345678901", "PESSOA FISICA", 100.0,
                  cnpj_orgao="30381552000158"),
    ])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def test_fornecedor_cnpj_vem_do_ni_fornecedor_e_cpf_fica_vazio(base_cnpj):
    linhas = base_cnpj["banco"].consultar(
        "SELECT numero_controle_pncp, fornecedor_cnpj FROM contratacoes ORDER BY numero_controle_pncp")

    assert [linha["fornecedor_cnpj"] for linha in linhas] == [
        "05340639000130", "05340639000130", "15064270000133", None]


@pytest.mark.parametrize("pergunta", [
    "Quais contratos tem o fornecedor de CNPJ 05.340.639/0001-30?",
    "Quais contratos tem o fornecedor de CNPJ 05340639000130?",
])
def test_listagem_pelo_cnpj_do_fornecedor(pergunta, base_cnpj):
    from src.query_analysis import analisar_query
    consulta = sq.detectar_consulta(pergunta, analisar_query(pergunta).filtros, ESTRUTURADA)

    _, _, linhas = sq.executar_consulta(consulta, base_cnpj["banco"])

    assert consulta.tipo == "listagem"
    assert linhas[0].startswith("2 registros")
    assert all("PRIME CONSULTORIA" in linha for linha in linhas[1:])


def test_papel_cnpj_explicito_nao_colide_com_a_outra_entidade(base_cnpj):
    from src.query_analysis import analisar_query
    pergunta = "Quais contratos tem fornecedor de CNPJ 30.381.552/0001-58?"
    filtros = analisar_query(pergunta).filtros
    consulta = sq.detectar_consulta(pergunta, filtros, ESTRUTURADA)

    assert filtros.papel_cnpj == "fornecedor"
    assert consulta.papel_cnpj == "fornecedor"
    where, params = sq._where(consulta, ())
    assert "fornecedor_cnpj = ?" in where and "orgao_cnpj = ?" not in where
    assert "30381552000158" in params
    _sql, _header, rows = sq.executar_consulta(consulta, base_cnpj["banco"])
    assert rows[0].startswith("0 registros atendem aos filtros")


def test_soma_pelo_cnpj_do_orgao(base_cnpj):
    from src.query_analysis import analisar_query
    pergunta = "Qual o valor total dos contratos do órgão CNPJ 29.131.075/0001-93 em 2025?"
    consulta = sq.detectar_consulta(pergunta, analisar_query(pergunta).filtros, ESTRUTURADA)

    _, _, linhas = sq.executar_consulta(consulta, base_cnpj["banco"])

    assert "2 registros" in linhas[0] and "R$ 4.000,00" in linhas[0]


@pytest.mark.parametrize("cnpj,valido", [
    ("05340639000130", True), ("29131075000193", True), ("11222333000181", True),
    ("11222333000180", False), ("05340639000131", False), ("11111111111111", False), ("1234567800019", False),
])
def test_cnpj_valido_confere_os_digitos_verificadores(cnpj, valido):
    assert structured_db.cnpj_valido(cnpj) is valido


def test_cabecalho_avisa_cnpj_com_digito_verificador_invalido(base_cnpj):
    from src.query_analysis import analisar_query
    pergunta = "Quais contratos tem o fornecedor de CNPJ 05.340.639/0001-31?"
    consulta = sq.detectar_consulta(pergunta, analisar_query(pergunta).filtros, ESTRUTURADA)

    _, cabecalho, _ = sq.executar_consulta(consulta, base_cnpj["banco"])

    assert "cnpj=05340639000131 (dígito verificador inválido" in cabecalho


def test_cabecalho_nao_avisa_cnpj_valido(base_cnpj):
    from src.query_analysis import analisar_query
    pergunta = "Quais contratos tem o fornecedor de CNPJ 05.340.639/0001-30?"
    consulta = sq.detectar_consulta(pergunta, analisar_query(pergunta).filtros, ESTRUTURADA)

    _, cabecalho, _ = sq.executar_consulta(consulta, base_cnpj["banco"])

    assert "cnpj=05340639000130" in cabecalho and "inválido" not in cabecalho


@pytest.mark.parametrize("verbo", ["foram firmados", "firmou", "firmaram"])
def test_firmar_nao_vira_termo_do_objeto(verbo):
    assert sq._termos_do_objeto(f"quantos contratos de ambulancia {verbo}") == ("ambulancia",)


@pytest.mark.parametrize("verbo", ["se houver", "se houve", "havendo", "havia", "haja", "se houvesse"])
def test_haver_nao_vira_termo_do_objeto(verbo):
    assert sq._termos_do_objeto(f"contratos de ambulancia {verbo}") == ("ambulancia",)


@pytest.mark.parametrize("complemento", [
    "e seus fornecedores", "e suas modalidades", "e os valores deles", "e seus respectivos valores",
    "que o orgao publicou", "que publicaram", "e quanto custaram",
])
def test_pronome_e_verbo_da_pergunta_nao_viram_termo_do_objeto(complemento):
    assert sq._termos_do_objeto(f"contratos de ambulancia {complemento}") == ("ambulancia",)


@pytest.mark.parametrize("pergunta,termos", [
    ("compare os gastos federais e estaduais em 2025", ()),
    ("quais os gastos com merenda escolar", ("merenda", "escolar")),
    ("despesas com ambulancia", ("ambulancia",)),
])
def test_gasto_e_despesa_nao_viram_termo_do_objeto(pergunta, termos):
    assert sq._termos_do_objeto(pergunta) == termos


@pytest.mark.parametrize("palavra", ["passagem", "passagens"])
def test_gem_e_gens_viram_o_mesmo_radical(palavra):
    assert sq._termos_do_objeto(f"contratos de {palavra} aerea") == ("passage", "aerea")


def test_termo_no_singular_acha_objeto_no_plural(tmp_path):
    documents = tmp_path / "documents.jsonl"
    escrever_documents(documents, contratacoes=[
        _contratacao("P-1", objeto="Fornecimento de passagens aéreas"),
        _contratacao("P-2", objeto="Agenciamento de passagem aérea nacional"),
    ])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    consulta = sq.Consulta(tabela="contratacoes", tipo="listagem",
                           termos_objeto=sq._termos_do_objeto("contratos de passagem"))

    _, _, linhas = sq.executar_consulta(consulta, banco)

    assert linhas[0].startswith("2 registros atendem aos filtros")


def test_listagem_direta_so_dispensa_o_llm_quando_ha_registros(tmp_path):
    documents = tmp_path / "documents.jsonl"
    escrever_documents(documents, contratacoes=[_contratacao("L-1", objeto="Locação de ambulância")])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)

    def chunks(termos):
        consulta = sq.Consulta(tabela="contratacoes", tipo="listagem", termos_objeto=termos, listagem_direta=True)
        sql, cabecalho, linhas = sq.executar_consulta(consulta, banco)
        return sq._chunks(consulta, sql, cabecalho, linhas, banco)

    assert all(c.metadata.extras["geracao_deterministica"] for c in chunks(("ambulancia",)))
    # Zero pode ser sinônimo ("UTI móvel"): a busca semântica ainda tem chance.
    assert not any(c.metadata.extras["geracao_deterministica"] for c in chunks(("helicoptero",)))


def test_comparacao_conta_so_as_esferas_citadas_e_exige_minimo_em_cada():
    consulta = sq.Consulta(tabela="contratacoes", tipo="estatistica", comparacao_esferas=True,
                           esferas_comparadas=("estadual", "municipal"))
    # F1 na v7: um único estadual, e a federal (não citada) completava duas esferas.
    motivo = sq._comparacao_esferas_insuficiente(
        consulta, {"estadual": [58500.0], "municipal": [1.0e5, 2.0e5, 3.0e5, 4.0e5, 5.0e5], "federal": [1.0e6] * 6})
    assert motivo and "estadual: 1 valores" in motivo

    assert sq._comparacao_esferas_insuficiente(
        consulta, {"estadual": [1.0e5] * 3, "municipal": [2.0e5] * 3}) is None


def test_comparacao_sem_esferas_citadas_mantem_a_regra_de_duas_esferas():
    consulta = sq.Consulta(tabela="contratacoes", tipo="estatistica", comparacao_esferas=True)

    assert sq._comparacao_esferas_insuficiente(consulta, {"estadual": [1.0e5], "municipal": [2.0e5]}) is None
    assert sq._comparacao_esferas_insuficiente(consulta, {"estadual": [1.0e5], "municipal": [0.0]})


def test_pergunta_que_compara_esferas_guarda_as_esferas_citadas():
    from src.query_analysis import _analisar

    pergunta = "Em 2025, órgãos estaduais pagaram mais por ambulâncias do que os municípios?"
    consulta = sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA)

    assert set(consulta.esferas_comparadas) == {"estadual", "municipal"}


def test_ranking_sem_modalidade_mostra_nao_informada_e_nao_none(tmp_path):
    documents = tmp_path / "documents.jsonl"
    escrever_documents(documents, contratacoes=[_contratacao("R-1", modalidade=None, objeto="Aquisição de ambulância")])
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    consulta = sq.Consulta(tabela="contratacoes", tipo="ranking", termos_objeto=("ambulancia",))

    _, _, linhas = sq.executar_consulta(consulta, banco)

    assert "None" not in linhas[0] and "modalidade: não informada" in linhas[0]


def test_pergunta_da_f1_nao_traz_houver_como_termo_do_objeto():
    pergunta = (
        "Em 2025, órgãos estaduais do Rio de Janeiro pagaram mais por ambulâncias tipo D do que os "
        "municípios fluminenses? Compare apenas os registros tecnicamente equivalentes disponíveis "
        "no corpus; se não houver dados suficientes, reconheça a insuficiência de evidência."
    )

    termos = sq._termos_do_objeto(structured_db.normalizar(pergunta))

    assert "houver" not in termos
    assert "ambulancia" in termos


def test_resultado_sql_vazio_e_marcado_sem_registros(base):
    chunks = _consultar("Quantos contratos de helicóptero existem?", base, metrica="contagem")

    assert chunks and all(chunk.metadata.extras["sem_registros"] for chunk in chunks)
# P3: recorte da consulta — confiabilidade, completude e cobertura separadas
# --------------------------------------------------------------------------


def _banco_com(tmp_path, contratacoes):
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=contratacoes)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return banco


def _consulta_soma(**mudancas):
    from dataclasses import replace
    base = sq.Consulta(tabela="contratacoes", tipo="estatistica", limite=10,
                       metrica="soma", campo="valor_contratado", so_contratos=True,
                       referencia_temporal="assinatura")
    return replace(base, **mudancas)


def test_zero_confiavel_no_snapshot_e_conclusao_nao_falha(tmp_path):
    """Execução completa e zero correspondências: é uma conclusão, não erro."""
    banco = _banco_com(tmp_path, [_contratacao("111-2-000001/2025")])
    recorte = {}
    _sql, _cab, _linhas = sq.executar_consulta(
        _consulta_soma(orgaos=("ORGAO QUE NAO EXISTE",)), banco, recorte)
    assert recorte["tipo_resultado"] == "sem_correspondencia"
    assert recorte["execucao"] == "completa" and recorte["filtros_relaxados"] == []
    assert recorte["registros_correspondentes"] == 0
    assert recorte["registros_no_snapshot"] == 1  # denominador continua visível


def test_recorte_traz_snapshot_versao_e_hash(tmp_path):
    banco = _banco_com(tmp_path, [_contratacao("111-2-000001/2025")])
    recorte = {}
    sq.executar_consulta(_consulta_soma(), banco, recorte)
    assert recorte["snapshot_versao"] == structured_db.SCHEMA_VERSION
    assert recorte["snapshot_hash"]  # proveniência da base, sem ler o texto


def test_campo_desconhecido_entra_na_completude_nao_como_nao_correspondente(tmp_path):
    com_valor = _contratacao("111-2-000001/2025")
    com_valor.update(valorGlobal=100, dataAssinatura="2024-12-20")
    sem_valor = _contratacao("111-2-000002/2025")
    sem_valor.update(valorGlobal=None, dataAssinatura="2024-12-20")
    banco = _banco_com(tmp_path, [com_valor, sem_valor])
    recorte = {}
    sq.executar_consulta(_consulta_soma(), banco, recorte)
    completude = {c["campo"]: c for c in recorte["completude"]}
    assert completude["valor_contratado"]["conhecidos"] == 1
    assert completude["valor_contratado"]["desconhecidos"] == 1
    assert recorte["registros_correspondentes"] == 2  # os dois continuam no recorte


def test_calculo_indisponivel_quando_nenhum_valor_e_conhecido(tmp_path):
    vazio = _contratacao("111-2-000001/2025")
    vazio.update(valorGlobal=None, dataAssinatura="2024-12-20")
    banco = _banco_com(tmp_path, [vazio])
    recorte = {}
    sq.executar_consulta(_consulta_soma(), banco, recorte)
    assert recorte["tipo_resultado"] == "calculo_indisponivel"
    assert recorte["calculo_disponivel"] is False
    assert "campo de valor" in recorte["calculo_motivo"]


def test_soma_zero_e_distinta_de_sem_correspondencia(tmp_path):
    zero = _contratacao("111-2-000001/2025")
    zero.update(valorGlobal=0, dataAssinatura="2024-12-20")
    banco = _banco_com(tmp_path, [zero])
    recorte = {}
    sq.executar_consulta(_consulta_soma(), banco, recorte)
    assert recorte["tipo_resultado"] == "soma_zero"
    assert recorte["registros_correspondentes"] == 1  # há registro; a soma é que é zero


def test_execucao_parcial_registra_o_que_foi_relaxado(tmp_path):
    """Termo de objeto que não casa força relaxamento: não é execução completa."""
    ambulancia = _contratacao("111-2-000001/2025")
    ambulancia.update(objeto="aquisicao de ambulancia", valorGlobal=100, dataAssinatura="2024-12-20")
    banco = _banco_com(tmp_path, [ambulancia])
    recorte = {}
    sq.executar_consulta(_consulta_soma(termos_objeto=("ambulancia", "pavimentacao")), banco, recorte)
    assert recorte["execucao"] == "parcial"
    assert recorte["filtros_relaxados"]
    assert recorte["registros_correspondentes"] == 1


def test_recorte_nao_extrapola_para_o_estado(tmp_path):
    banco = _banco_com(tmp_path, [_contratacao("111-2-000001/2025")])
    recorte = {}
    sq.executar_consulta(_consulta_soma(), banco, recorte)
    # Cobertura declarada como amostra, com o denominador do snapshot.
    assert recorte["cobertura"] == "amostra"
    assert recorte["registros_no_snapshot"] == 1


def test_instrucao_da_pergunta_nao_vira_termo_de_objeto():
    """"Reconheça a insuficiência de evidência" é instrução, não objeto."""
    assert sq._termos_do_objeto("ambulancias tipo d reconheca a insuficiencia de evidencia") == (
        "tipo d", "ambulancia")


# --------------------------------------------------------------------------
# Comparação entre esferas sem dados suficientes (F1 do golden set)
# --------------------------------------------------------------------------

_PERGUNTA_F1 = (
    "Em 2025, órgãos estaduais do Rio de Janeiro pagaram mais por ambulâncias tipo D do que os "
    "municípios fluminenses? Compare apenas os registros tecnicamente equivalentes disponíveis "
    "no corpus; se não houver dados suficientes, reconheça a insuficiência de evidência."
)


def _consulta_comparacao(**mudancas):
    argumentos = dict(metrica="media", campo="valor_estimado", so_contratos=False,
                      referencia_temporal=None, comparacao_esferas=True)
    argumentos.update(mudancas)
    return _consulta_soma(**argumentos)


def test_pergunta_que_cita_duas_esferas_e_comparacao_entre_esferas():
    from src.query_analysis import analisar_query
    consulta = sq.detectar_consulta(_PERGUNTA_F1, analisar_query(_PERGUNTA_F1).filtros, ESTRUTURADA)

    assert consulta is not None and consulta.comparacao_esferas


@pytest.mark.parametrize("pergunta", [
    "Qual a média dos contratos municipais de ambulâncias em 2025?",
    "Qual a média dos contratos de ambulâncias em 2025?",
])
def test_pergunta_sem_duas_esferas_nao_e_comparacao_entre_esferas(pergunta):
    from src.query_analysis import analisar_query
    consulta = sq.detectar_consulta(pergunta, analisar_query(pergunta).filtros, ESTRUTURADA)

    assert consulta is not None and not consulta.comparacao_esferas


def test_comparacao_sem_esfera_com_valor_e_marcada_como_insuficiente(tmp_path):
    """F1: só há um registro municipal e ele vale R$ 0,00; não há o que comparar."""
    municipal = _contratacao("111-2-000001/2025", esfera="M", valor_estimado=0.0)
    banco = _banco_com(tmp_path, [municipal])
    recorte = {}

    _sql, _cabecalho, linhas = sq.executar_consulta(_consulta_comparacao(), banco, recorte)

    assert recorte["tipo_resultado"] == "comparacao_insuficiente"
    assert "não é possível comparar" in recorte["calculo_motivo"]
    assert recorte["calculo_disponivel"] is True  # a conta existe; é a comparação que não se sustenta
    assert any(linha.startswith("Dados insuficientes") and "municipal: 0 valores" in linha for linha in linhas)


def test_comparacao_com_uma_so_esfera_com_valor_e_insuficiente(tmp_path):
    estadual = _contratacao("111-2-000001/2025", esfera="E", valor_estimado=500_000.0)
    municipal = _contratacao("111-2-000002/2025", esfera="M", valor_estimado=0.0)
    banco = _banco_com(tmp_path, [estadual, municipal])
    recorte = {}

    _sql, _cabecalho, linhas = sq.executar_consulta(_consulta_comparacao(), banco, recorte)

    assert recorte["tipo_resultado"] == "comparacao_insuficiente"
    assert any("estadual: 1 valores" in linha and "municipal: 0 valores" in linha for linha in linhas)


def test_comparacao_com_duas_esferas_com_valor_nao_e_marcada(tmp_path):
    estadual = _contratacao("111-2-000001/2025", esfera="E", valor_estimado=500_000.0)
    municipal = _contratacao("111-2-000002/2025", esfera="M", valor_estimado=120_000.0)
    banco = _banco_com(tmp_path, [estadual, municipal])
    recorte = {}

    _sql, _cabecalho, linhas = sq.executar_consulta(_consulta_comparacao(), banco, recorte)

    assert recorte["tipo_resultado"] == "registros"
    assert not any(linha.startswith("Dados insuficientes") for linha in linhas)


def test_consulta_que_nao_e_comparacao_entre_esferas_nao_e_marcada(tmp_path):
    municipal = _contratacao("111-2-000001/2025", esfera="M", valor_estimado=0.0)
    banco = _banco_com(tmp_path, [municipal])
    recorte = {}

    _sql, _cabecalho, linhas = sq.executar_consulta(
        _consulta_comparacao(comparacao_esferas=False), banco, recorte)

    assert recorte["tipo_resultado"] != "comparacao_insuficiente"
    assert not any(linha.startswith("Dados insuficientes") for linha in linhas)


def test_especificacao_de_tipo_e_preservada():
    assert sq._termos_do_objeto("contratacoes de ambulancias tipo d") == ("tipo d", "ambulancia")


def test_qual_tipo_de_contrato_nao_gera_termo():
    """"tipo" sozinho é palavra genérica; só a especificação conta."""
    assert sq._termos_do_objeto("qual tipo de contrato") == ()


@pytest.mark.parametrize("pergunta", [
    "Qual foi o valor pago pelas ambulâncias?",
    "Quanto foi pago pelas ambulâncias?",
])
def test_pagamento_vira_soma_com_aviso_de_que_nao_e_o_pago(pergunta, base):
    consulta = sq.detectar_consulta(pergunta, QueryFilters(), ESTRUTURADA)

    _, cabecalho, _ = sq.executar_consulta(consulta, base["banco"])

    assert consulta.tipo == "estatistica" and consulta.pagamento_citado
    assert "o PNCP não registra pagamentos" in cabecalho


def test_valor_total_sem_pagamento_nao_ganha_o_aviso(base):
    consulta = sq.detectar_consulta("Qual o valor total das ambulâncias?", QueryFilters(), ESTRUTURADA)

    _, cabecalho, _ = sq.executar_consulta(consulta, base["banco"])

    assert not consulta.pagamento_citado
    assert "não registra pagamentos" not in cabecalho


@pytest.mark.parametrize("verbo", ["somaram", "gastaram", "receberam", "contrataram"])
def test_verbo_de_quantidade_com_mais_e_comparacao_entre_esferas(verbo):
    from src.query_analysis import _analisar

    pergunta = f"Em 2025, os órgãos federais {verbo} mais em ambulâncias do que os municipais?"
    consulta = sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA)

    assert consulta.tipo == "estatistica" and consulta.comparacao_esferas
    assert set(consulta.esferas_comparadas) == {"federal", "municipal"}


@pytest.mark.parametrize("pergunta,mostra", [
    ("Quantos contratos de ambulância existem e qual o valor total?", True),
    ("Quantos contratos de ambulância foram assinados em 2025?", False),
])
def test_contagem_mostra_o_total_so_quando_a_pergunta_pede(pergunta, mostra):
    from src.query_analysis import _analisar

    consulta = sq.detectar_consulta(pergunta, _analisar(pergunta), ESTRUTURADA)

    assert consulta.metrica == "contagem" and consulta.mostrar_total is mostra
    tabela = sq._tabela_metricas(consulta, [{"rotulo": "Recorte", "n": 2, "total": 300.0}], "Recorte")
    assert ("Total registrado" in tabela) is mostra


def test_cnpj_do_fornecedor_sai_do_sql_formatado(base_cnpj):
    filtros = QueryFilters(filtros_analiticos={"fornecedor": "PRIME CONSULTORIA"})
    consulta = sq.detectar_consulta("Qual o CNPJ da Prime Consultoria?", filtros, ESTRUTURADA)
    assert consulta.tipo == "cnpj_fornecedor"
    assert sq.pode_consultar_sql_direto("Qual o CNPJ da Prime Consultoria?", filtros, ESTRUTURADA)

    chunks = sq.consultar_estruturado("Qual o CNPJ da Prime Consultoria?", filtros, ESTRUTURADA, **base_cnpj)
    assert "05.340.639/0001-30" in chunks[0].content and "2 registros" in chunks[0].content
    assert all(c.metadata.extras["geracao_deterministica"] for c in chunks)


@pytest.mark.parametrize("pergunta", [
    "Quantos contratos a Prime Consultoria tem e qual o CNPJ?",
    "Qual o valor total da Prime Consultoria e o CNPJ?",
])
def test_cnpj_junto_de_uma_conta_segue_o_caminho_da_conta(pergunta):
    filtros = QueryFilters(filtros_analiticos={"fornecedor": "PRIME CONSULTORIA"})
    assert sq.detectar_consulta(pergunta, filtros, ESTRUTURADA).tipo != "cnpj_fornecedor"


@pytest.mark.parametrize("pergunta", [
    "Contratos acima de 50 milhões em 2025",
    "Contratações de merenda em Niterói",
])
def test_frase_nominal_de_contratos_e_listagem_direta(pergunta):
    consulta = sq.detectar_consulta(pergunta, QueryFilters(ano=2025), ESTRUTURADA)
    assert consulta.tipo == "listagem" and consulta.listagem_direta


def test_cnpj_sem_verbo_tambem_e_pedido_de_cnpj():
    filtros = QueryFilters(filtros_analiticos={"fornecedor": "PRIME CONSULTORIA"})
    assert sq.detectar_consulta("CNPJ da Prime Consultoria?", filtros, ESTRUTURADA).tipo == "cnpj_fornecedor"


def test_quais_modalidades_dos_contratos_e_agrupamento_por_modalidade():
    pergunta = ("Considerando os contratos de manutenção de computadores encontrados em 2025, quais modalidades "
                "aparecem nos registros e há dados suficientes para concluir uma modalidade predominante?")
    consulta = sq.detectar_consulta(pergunta, QueryFilters(ano=2025), ESTRUTURADA)
    assert consulta.tipo == "agrupamento" and consulta.agrupar_por == "modalidade"
    assert consulta.termos_objeto == ("manutencao", "computador")


@pytest.mark.parametrize("pergunta", [
    "Que modalidades de licitação a Lei 14.133 prevê?",
    "Quais as modalidades de licitação previstas nas normas?",
])
def test_quais_modalidades_da_norma_nao_vira_consulta_sql(pergunta):
    bases = [Natureza.NORMATIVA, Natureza.ESTRUTURADA]
    assert sq.detectar_consulta(pergunta, QueryFilters(), bases) is None


# --------------------------------------------------------------------------
# Situação da compra: anuladas, revogadas e suspensas fora das contas, salvo
# quando a pergunta fala delas (os dois caminhos usam _situacoes_da_pergunta).
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pergunta, clausula", [
    ("Qual a mediana das contratações?", _SEM_ANULADAS),
    ("Qual a mediana das contratações anuladas?", "situacao IN ('Anulada')"),
    ("Qual a mediana das contratações canceladas?", "situacao IN ('Anulada', 'Revogada')"),
    ("Qual a mediana das contratações revogadas ou suspensas?", "situacao IN ('Revogada', 'Suspensa')"),
    ("Qual a mediana das contratações, incluindo as anuladas?",
     "(situacao IS NULL OR situacao NOT IN ('Revogada', 'Suspensa'))"),
    ("Qual a mediana das contratações anuladas e vigentes?",
     "(situacao IS NULL OR situacao NOT IN ('Revogada', 'Suspensa'))"),
])
def test_situacao_citada_define_o_filtro(pergunta, clausula):
    consulta = sq.detectar_consulta(pergunta, QueryFilters(), ESTRUTURADA)
    sql, _ = sq._where(consulta, ())
    assert sql == f" WHERE {clausula}"


def test_situacao_decidida_igual_nos_dois_caminhos():
    # Antes, o caminho da intenção tratava "anuladas e vigentes" como só anuladas.
    for pergunta in ("Quantas contratações anuladas e vigentes?", "Total das compras canceladas"):
        assert sq._situacoes_da_pergunta(pergunta) == sq._situacoes_da_pergunta(pergunta.upper())
    assert sq._situacoes_da_pergunta("Quantas contratações anuladas e vigentes?") == {
        "situacoes_citadas": ("Anulada",), "com_situacoes_validas": True}


@pytest.fixture
def base_situacoes(tmp_path):
    registros = []
    for numero, situacao in [("S-1", "Divulgada no PNCP"), ("S-2", "Anulada"), ("S-3", "Revogada"), ("S-4", "Suspensa")]:
        registro = _contratacao(numero, valor_estimado=100.0)
        registro["situacaoCompraNome"] = situacao
        registros.append(registro)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=registros)
    banco = Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def _registros(pergunta, base):
    chunks = _consultar(pergunta, base)
    return chunks[0].metadata.extras["resultado_sql"]["recorte"]["registros_correspondentes"]


@pytest.mark.parametrize("pergunta, esperado", [
    ("Qual a mediana das contratações?", 1),
    ("Qual a mediana das contratações anuladas?", 1),
    ("Qual a mediana das contratações canceladas?", 2),
    ("Qual a mediana das contratações suspensas?", 1),
    ("Qual a mediana das contratações anuladas e vigentes?", 2),
    ("Qual a mediana das contratações, incluindo as anuladas?", 2),
    ("Qual a mediana das contratações revogadas ou suspensas?", 2),
])
def test_situacao_filtra_as_linhas_do_sql(base_situacoes, pergunta, esperado):
    assert _registros(pergunta, base_situacoes) == esperado


def test_identificador_encontra_compra_anulada():
    # Quem pede um registro pelo número do PNCP precisa vê-lo, mesmo anulado.
    consulta = sq.detectar_consulta("Detalhes da compra 00394429000100-1-000183/2025",
                                    QueryFilters(), ESTRUTURADA)
    assert consulta.tipo == "identificador"
    sql, _ = sq._where(consulta, ())
    assert "situacao" not in sql


# --------------------------------------------------------------------------
# Registro de executores: um por tipo de consulta (aberto para tipo novo sem
# mexer nos outros).
# --------------------------------------------------------------------------

def test_todo_tipo_de_consulta_tem_executor():
    from typing import get_args
    from src.schema import QueryIntent

    operacoes = set(get_args(QueryIntent.model_fields["operacao"].annotation)) - {"buscar"}
    gerados_por_regra = {"identificador", "cnpj_fornecedor", "listagem"}
    assert operacoes | gerados_por_regra <= set(sq._EXECUTORES)


def test_tipo_desconhecido_da_erro_claro(base):
    consulta = sq.Consulta(tabela="contratacoes", tipo="inexistente")
    with pytest.raises(ValueError, match="Tipo de consulta SQL desconhecido"):
        sq._executar(base["banco"], consulta, (), "AND")
