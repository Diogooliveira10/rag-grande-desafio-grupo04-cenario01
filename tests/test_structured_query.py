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

    assert "Nenhum registro encontrado com os filtros aplicados" in chunk.content
    assert "média" not in chunk.content


def test_termos_sem_combinacao_exata_caem_para_ou_entre_os_que_existem(base):
    [chunk] = _consultar("Qual o valor total de ambulâncias e pavimentação?", base)

    assert "objeto contém ambulancia ou pavimentacao" in chunk.content
    assert "Geral: 4 registros" in chunk.content


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

    [chunk] = _consultar("Quais contratações de ambulâncias existem em 2025?", base, ano=2025)
    assert "3 registros atendem aos filtros" in chunk.content  # M-1, M-2 e E-1
    assert "fornecedor:" in chunk.content and "modalidade: Concorrência - Eletrônica" in chunk.content


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
    assert _detectar("Quais contratos existem em 2025?", ano=2025) is None


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
    [chunk] = _consultar("Qual o total de ambulâncias e pavimentação desse período?", base)

    assert "objeto contém ambulancia ou pavimentacao" in chunk.content


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
    assert "conta uma vez (pelo contrato)" in chunk.content


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
    question = 'Compare a mediana do valor por veículo em 2025.'
    filters = analisar_query(question, modo='regras').filtros
    assert filters.tipo_valor == 'unitario'
    assert sq.detectar_consulta(question, filters, ESTRUTURADA + AGREGADA) is None
