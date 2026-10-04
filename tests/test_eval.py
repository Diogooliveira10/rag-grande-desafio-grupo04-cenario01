from pathlib import Path

import pytest

from eval.run_eval import evaluate_case, load_golden_set, render_report_markdown, run_eval, save_run

GOLDEN_SET = Path(__file__).resolve().parents[1] / "eval" / "golden_set.jsonl"


def test_load_golden_set_tem_18_referencias_a_de_falha_e_3_guardrails():
    cases = load_golden_set(GOLDEN_SET)
    assert len(cases) == 26
    assert len({case.id for case in cases}) == 26  # sem IDs duplicados
    assert {case.id for case in cases[-4:]} == {"A1", "A2", "I1", "I2"}
    assert sum(case.e_pergunta_de_falha_esperada for case in cases) == 1
    guardrails = [case for case in cases if case.id.startswith("G")]
    assert {case.refusal_reason_esperado for case in guardrails} == {
        "dados_bancarios_fornecedor",
        "credenciais_vazadas",
        "juizo_legalidade_contrato",
    }
    assert all(case.is_refusal_esperado and not case.bases_esperadas for case in guardrails)


def test_load_golden_set_cobre_os_seis_tipos_do_documento():
    cases = load_golden_set(GOLDEN_SET)
    tipos = {case.tipo for case in cases}
    assert tipos == {"factual", "filtro", "cross_source", "vigencia", "recusa", "fora_de_escopo"}


def test_load_golden_set_falha_com_ids_duplicados(tmp_path):
    arquivo = tmp_path / "golden_set_invalido.jsonl"
    linha = (
        '{"id": "X1", "persona": "p", "titulo": "t", "pergunta": "q", "tipo": "factual", '
        '"bases_esperadas": ["estruturada"], "is_refusal_esperado": false, '
        '"refusal_reason_esperado": null, "e_pergunta_de_falha_esperada": false}\n'
    )
    arquivo.write_text(linha + linha, encoding="utf-8")

    with pytest.raises(ValueError, match="IDs duplicados"):
        load_golden_set(arquivo)


def test_evaluate_case_acerta_quando_rota_e_recusa_batem():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is True
    assert resultado["rota_ok"] is True
    assert resultado["refusal_ok"] is True
    assert resultado["escopo_avaliacao"] == "roteamento"
    assert resultado["content_ok"] is None


def test_resposta_factual_com_rota_correta_mas_conteudo_e_citacao_errados_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "I1")
    resposta = {
        "bases_consultadas": caso.bases_esperadas,
        "is_refusal": False,
        "answer": "Nenhum registro encontrado.",
        "sources_used": [],
        "confidence_level": "baixa",
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["rota_ok"] is True
    assert resultado["content_ok"] is False
    assert resultado["citacao_ok"] is False
    assert resultado["expected_source_ok"] is False
    assert resultado["acertou"] is False
    assert resultado["escopo_avaliacao"] == "resposta_completa"


def test_evaluate_case_falha_quando_rota_diverge():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")
    resposta = {"bases_consultadas": ["normativa"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["rota_ok"] is False


def test_evaluate_case_recusa_correta_nao_cobra_rota():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.3")
    resposta = {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "juizo_legalidade_contrato"}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is True


def test_evaluate_case_recusa_com_motivo_errado_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P3.3")
    resposta = {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "sem_evidencia"}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["refusal_ok"] is False


def test_evaluate_case_nao_recusar_quando_deveria_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.3")
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["refusal_ok"] is False


def test_run_eval_agrega_taxa_de_acerto_e_totais_por_tipo():
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": caso.is_refusal_esperado,
            "refusal_reason": caso.refusal_reason_esperado,
        }

    report = run_eval(cases, pipeline_fn)

    assert report["total"] == len(cases)
    assert report["acertos"] == len(cases)
    assert report["taxa_acerto"] == 1.0
    assert sum(stats["total"] for stats in report["por_tipo"].values()) == len(cases)


def test_run_eval_marca_divergencias_quando_pipeline_erra_tudo():
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        return {"bases_consultadas": ["base_inexistente"], "is_refusal": False}

    report = run_eval(cases, pipeline_fn)

    assert report["acertos"] == 0
    assert report["taxa_acerto"] == 0.0
    assert all(not resultado["acertou"] for resultado in report["resultados"])


def test_save_run_grava_json_e_markdown_legivel(tmp_path):
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": caso.is_refusal_esperado,
            "refusal_reason": caso.refusal_reason_esperado,
        }

    report = run_eval(cases, pipeline_fn)
    json_path, md_path = save_run(report, tmp_path / "run-teste", "run-teste", "2026-09-21T00:00:00+00:00")

    assert json_path.exists()
    assert md_path.exists()
    markdown = render_report_markdown(report, "run-teste", "2026-09-21T00:00:00+00:00")
    assert "Acertos" in markdown
    assert "P1.1" in markdown


def test_evaluate_case_mede_citacao_so_quando_ha_sources_used():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")

    sem_campo = evaluate_case(caso, {"bases_consultadas": ["estruturada"]})
    sem_fonte = evaluate_case(caso, {"bases_consultadas": ["estruturada"], "sources_used": []})
    com_fonte = evaluate_case(
        caso, {"bases_consultadas": ["estruturada"], "sources_used": [{"chunk_id": "c1"}]}
    )

    assert sem_campo["citacao_ok"] is None
    assert sem_fonte["citacao_ok"] is False
    assert com_fonte["citacao_ok"] is True
    assert com_fonte["n_fontes"] == 1


def test_run_eval_registra_excecao_sem_derrubar_a_rodada():
    cases = [case for case in load_golden_set(GOLDEN_SET) if case.id in {"P1.1", "P1.3"}]

    def pipeline_fn(pergunta: str) -> dict:
        if pergunta == cases[0].pergunta:
            raise TimeoutError("servidor lento")
        return {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "juizo_legalidade_contrato"}

    report = run_eval(cases, pipeline_fn)

    assert report["total"] == 2
    assert report["acertos"] == 1
    assert report["metricas"]["erros"] == 1
    assert report["resultados"][0]["erro"] == "TimeoutError: servidor lento"
    assert "ERRO" in render_report_markdown(report, "r", "t")


def test_run_eval_resume_latencia_confianca_e_tokens():
    cases = [case for case in load_golden_set(GOLDEN_SET) if case.id in {"P1.1", "P6.1"}]

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": False,
            "sources_used": [{"chunk_id": "c1"}],
            "confidence_level": "alta",
            "answer": "resposta",
            "_metricas": {"tokens_total": 100, "etapas": {"geracao": {"tokens": 90}, "retrieval_denso": {"tokens": 10}}},
        }

    report = run_eval(cases, pipeline_fn)
    metricas = report["metricas"]

    assert metricas["citacao_ok"] == 2
    assert metricas["confianca"] == {"alta": 2}
    assert metricas["tokens_total"] == 200
    assert metricas["tokens_por_etapa"] == {"geracao": 180, "retrieval_denso": 20}
    assert metricas["latencia_ms"]["max"] >= 0
    markdown = render_report_markdown(report, "r", "t")
    assert "## Métricas" in markdown
    assert "## Respostas" in markdown
