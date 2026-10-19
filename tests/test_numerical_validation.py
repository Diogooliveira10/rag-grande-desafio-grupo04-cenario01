"""Regression coverage for discrepancies found in the live corpus audit."""
import json

import pytest

from src import generate, structured_db, structured_query
from src.schema import Chunk, ChunkMetadata, Natureza, QueryFilters
from tests.test_structured_db import _contratacao, escrever_documents


def contract(n, value, signature="2025-10-24", publication="2025-10-30"):
    row = _contratacao(f"11111111000111-2-{n:06d}/2025", data=publication)
    row.update(dataAssinatura=signature, valorGlobal=value, objetoContrato="Aquisição de computadores")
    return row


def test_aggregate_contract_uses_signature_year_without_rewriting_publication(tmp_path):
    documents = escrever_documents(tmp_path / "documents.jsonl",
        contratacoes=[contract(1, 100)],
        planos=[contract(2, 200, publication="2024-12-10"),
                contract(3, 300, publication="2026-01-02"),
                contract(4, 400, signature="2024-03-01", publication="2024-03-10")])
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    rows = banco.consultar("SELECT ano_assinatura, ano_publicacao, valor_contratado FROM contratacoes ORDER BY valor_contratado")
    assert rows == [
        {"ano_assinatura": 2025, "ano_publicacao": 2025, "valor_contratado": 100},
        {"ano_assinatura": 2025, "ano_publicacao": 2024, "valor_contratado": 200},
        {"ano_assinatura": 2025, "ano_publicacao": 2026, "valor_contratado": 300}]
    _, _, signed = structured_query.executar_consulta(structured_query.Consulta(
        tabela="contratacoes", tipo="estatistica", ano=2025, referencia_temporal="assinatura", so_contratos=True), banco)
    _, _, published = structured_query.executar_consulta(structured_query.Consulta(
        tabela="contratacoes", tipo="estatistica", ano=2025, referencia_temporal="publicacao", so_contratos=True), banco)
    assert "total R$ 600,00" in signed[0]
    assert "total R$ 100,00" in published[0]


@pytest.mark.parametrize("tipo,metrica", [
    ("estatistica", "soma"), ("agrupamento", "soma"),
    ("agrupamento", "mediana"), ("evolucao", "soma")])
def test_literal_totals_preserve_cents_and_zero_and_qualify_statistics(tmp_path, tipo, metrica):
    documents = escrever_documents(tmp_path / "documents.jsonl",
        contratacoes=[contract(1, 0), contract(2, 0.01), contract(3, 100), contract(4, 300), contract(5, None)])
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    _, _, lines = structured_query.executar_consulta(structured_query.Consulta(
        tabela="contratacoes", tipo=tipo, ano=2025, referencia_temporal="assinatura", so_contratos=True,
        agrupar_por="esfera", metrica=metrica), banco)
    text = "\n".join(lines)
    assert "total R$ 400,01" in text
    assert "média R$ 200,00" in text
    if tipo == "estatistica":
        assert "5 registros no total, 1 sem valor informado, 2 com valor abaixo de R$ 1,00" in text
    if tipo != "agrupamento":
        assert "mediana R$ 200,00" in text


def sql_chunk(part, lines, *, kind="estatistica"):
    # Recorte de uma execução completa, como structured_query grava em toda consulta.
    vazio = all(linha == "(nenhum registro)" for linha in lines)
    recorte = {"execucao": "completa", "filtros_relaxados": [], "calculo_disponivel": True,
               "tipo_resultado": "sem_correspondencia" if vazio else "registros"}
    return Chunk(content="not parsed for numerical rendering", metadata=ChunkMetadata(
        source_file="sqlite", chunk_id=f"sql:contratacoes:{kind}:hash:{part}",
        base_id="contratos_estruturado", doc_type="sql", natureza=Natureza.ESTRUTURADA,
        extras={"parte": part, "geracao_deterministica": True,
                "resultado_sql": {"cabecalho": "Consulta SQL na tabela contratacoes: amostra do corpus.",
                                  "linhas": lines, "recorte": recorte}}))


def modelo_template_sql(_):
    return json.dumps({"answer": "{{sql}}"})


def test_sql_template_keeps_all_sql_parts_outside_llm_budget(monkeypatch):
    monkeypatch.setattr(generate.config, "GENERATION_CONTEXT_CHARS", 50)
    chunks = [sql_chunk(1, ["Geral: 455 registros; total R$ 2.546.786.958,21"]),
              sql_chunk(2, ["Esfera municipal: total R$ 534.883.349,11"])]
    response = generate.gerar_resposta("Qual o total de contratos de 2025?", chunks,
        [Natureza.ESTRUTURADA, Natureza.AGREGADA],
        cliente=modelo_template_sql)
    assert "R$ 2.546.786.958,21 [1]" in response.answer
    assert "R$ 534.883.349,11 [2]" in response.answer
    assert "sql:" not in response.answer and "contratacoes" not in response.answer
    assert len(response.sources_used) == 2


@pytest.mark.parametrize("scenario", ["normativa", "missing_intent", "buscar", "unsupported_sql", "no_mark"])
def test_mixed_or_incomplete_evidence_does_not_bypass_generation(scenario):
    chunks = [sql_chunk(1, ["total R$ 100,00"])]
    bases = [Natureza.ESTRUTURADA]
    if scenario == "normativa":
        bases.append(Natureza.NORMATIVA)
    elif scenario == "missing_intent":
        chunks[0].metadata.extras.update(intencao=0, total_intencoes=2)
    elif scenario == "buscar":
        chunks[0].metadata.extras["operacao"] = "buscar"
    elif scenario == "unsupported_sql":
        chunks.append(sql_chunk(2, ["context only"]))
        chunks[-1].metadata.extras["geracao_deterministica"] = False
    else:
        chunks[0].metadata.extras.pop("geracao_deterministica")
    calls = []
    def model(messages):
        calls.append(messages)
        return json.dumps({"answer": "Partial evidence", "is_refusal": False,
                           "confidence_level": "baixa", "sources_used": ["E1"]})
    response = generate.gerar_resposta("Qual o valor total e a regra aplicável?", chunks, bases, cliente=model)
    assert calls and response.answer == "Partial evidence"


def test_symbolic_only_is_zero_total_not_missing_statistics():
    text = structured_query._estatisticas([0.0])
    assert "total R$ 0,00" in text
    assert "média, mediana, mínimo e máximo não informados" in text
    assert "total não informado" in structured_query._estatisticas([])


def test_sql_sem_registros_e_conclusao_no_recorte_com_ressalva_de_escopo():
    # Zero de uma consulta completa sustenta "não há registros" na amostra
    # (confiança do recorte); o escopo fica no texto e nos avisos.
    vazio = sql_chunk(1, ["(nenhum registro)"])
    response = generate.gerar_resposta("Quantos contratos de ambulância em 2024?", [vazio],
        [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert response.confidence_level.value == "alta"
    assert any(aviso.startswith("Escopo") for aviso in response.avisos)
    assert "amostra do corpus" in response.answer
    assert "não comprova" in response.answer
    assert "universo completo do Estado" in response.answer
    assert "(nenhum registro)" not in response.answer
    assert response.sources_used[0].chunk_id == vazio.metadata.chunk_id


def test_sql_com_registros_continua_com_confianca_alta():
    response = generate.gerar_resposta("Quantos contratos de ambulância em 2025?",
        [sql_chunk(1, ["Geral: 13 registros; total R$ 8.266.940,37"])],
        [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert response.confidence_level.value == "alta"
    assert "13 registros" in response.answer
    assert "R$ 8.266.940,37" in response.answer
    assert response.sources_used[0].chunk_id.endswith(":1")


def test_parte_sql_vazia_nao_rebaixa_outras_partes_com_resultados():
    response = generate.gerar_resposta(
        "Quantos contratos de 2025?",
        [
            sql_chunk(1, []),
            sql_chunk(2, ["Geral: 13 registros; total R$ 8.266.940,37"]),
        ],
        [Natureza.ESTRUTURADA],
        cliente=modelo_template_sql,
    )
    assert response.confidence_level.value == "alta"
    assert "R$ 8.266.940,37" in response.answer
    assert "(nenhum registro)" not in response.answer


@pytest.mark.parametrize(
    "valor,esperado",
    [("alta", "alta"), (0.95, "alta"), (0.6, "media"), ("media", "media"), ("baixa", "baixa"),
     ("invalida", "baixa"), (True, "baixa")],
)
def test_nivel_de_confianca_converte_valores_do_llm(valor, esperado):
    assert generate._nivel_de_confianca(valor).value == esperado


@pytest.mark.parametrize(
    "pergunta",
    [
        "Quais contratações de ambulâncias tipo D foram publicadas?",
        "Quantos contratos de ambulância em 2024?",
    ],
)
def test_perguntas_p21_sem_resultados_trazem_a_ressalva_da_amostra(pergunta):
    response = generate.gerar_resposta(
        pergunta,
        [sql_chunk(1, ["(nenhum registro)"])],
        [Natureza.ESTRUTURADA],
        cliente=modelo_template_sql,
    )
    assert "não comprova" in response.answer


def test_resposta_llm_com_sql_vazio_ganha_a_ressalva_da_amostra():
    chunk = sql_chunk(1, ["(nenhum registro)"])
    chunk.metadata.extras["geracao_deterministica"] = False
    response = generate.gerar_resposta(
        "Quantos contratos de ambulância em 2024?",
        [chunk],
        [Natureza.ESTRUTURADA],
        cliente=lambda _: json.dumps({
            "answer": "Nenhum registro encontrado.",
            "is_refusal": False,
            "confidence_level": "alta",
            "sources_used": ["E1"],
        }),
    )
    assert response.confidence_level.value == "alta"  # recorte completo sustenta o zero
    assert "amostra do corpus" in response.answer
    assert "não comprova" in response.answer
    assert "(nenhum registro)" not in response.answer


def _intencao(chunk, intencao, *, sem_registros):
    chunk.metadata.extras.update({"intencao": intencao, "total_intencoes": 2, "sem_registros": sem_registros})
    return chunk


def test_intencao_vazia_nao_rebaixa_a_outra_com_resultado():
    chunks = [
        _intencao(sql_chunk(1, ["Geral: 13 registros; total R$ 8.266.940,37"]), 1, sem_registros=False),
        _intencao(sql_chunk(1, ["(nenhum registro)"], kind="ranking"), 2, sem_registros=True),
    ]
    response = generate.gerar_resposta("Quantos contratos de ambulância em 2025 e quem mais vendeu?", chunks,
        [Natureza.ESTRUTURADA], cliente=modelo_template_sql)
    assert response.confidence_level.value == "alta"
    assert "R$ 8.266.940,37" in response.answer
    # A ressalva fica só na intenção que não achou nada.
    assert response.answer.count("não comprova") == 1


def test_resposta_llm_com_uma_intencao_vazia_nao_ganha_ressalva_de_ausencia():
    chunks = [
        _intencao(sql_chunk(1, ["Geral: 13 registros; total R$ 8.266.940,37"]), 1, sem_registros=False),
        _intencao(sql_chunk(1, ["(nenhum registro)"], kind="ranking"), 2, sem_registros=True),
    ]
    for chunk in chunks:
        chunk.metadata.extras["geracao_deterministica"] = False
    response = generate.gerar_resposta(
        "Quantos contratos de ambulância em 2025 e quem mais vendeu?", chunks, [Natureza.ESTRUTURADA],
        cliente=lambda _: json.dumps({"answer": "Foram 13 contratos, R$ 8.266.940,37 [E1].",
                                      "is_refusal": False, "confidence_level": "alta", "sources_used": ["E1"]}),
    )
    # A conta da primeira parte continua válida, mas a pergunta composta não
    # pode ter confiança alta quando a segunda parte não tem fonte citada.
    assert response.confidence_level.value == "media"
    assert any("Parte sem fonte citada" in aviso for aviso in response.avisos)
    assert "não comprova" not in response.answer
