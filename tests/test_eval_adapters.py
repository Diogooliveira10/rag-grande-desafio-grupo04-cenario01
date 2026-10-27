import json
from pathlib import Path

from eval.adapters import metricas_do_log, rota_apenas
from eval.run_eval import load_golden_set, run_eval

GOLDEN_SET = Path(__file__).resolve().parents[1] / "eval" / "golden_set.jsonl"


def test_rota_apenas_roda_todo_o_golden_set_sem_excecao():
    """src/router.py é determinístico e offline — não precisa de @pytest.mark.llm."""
    cases = load_golden_set(GOLDEN_SET)

    report = run_eval(cases, rota_apenas)

    assert report["total"] == len(cases)
    assert 0.0 <= report["taxa_acerto"] <= 1.0


def test_rota_apenas_acerta_os_casos_de_recusa_e_fora_de_escopo():
    """Guardrails determinísticos de router.py — não deveriam regredir silenciosamente."""
    cases = load_golden_set(GOLDEN_SET)
    por_id = {case.id: case for case in cases}

    resultado_recusa = rota_apenas(por_id["P1.3"].pergunta)
    resultado_fora_de_escopo = rota_apenas(por_id["P3.3"].pergunta)

    assert resultado_recusa["is_refusal"] is True
    assert resultado_recusa["refusal_reason"] == "juizo_legalidade_contrato"
    assert resultado_fora_de_escopo["is_refusal"] is True
    assert resultado_fora_de_escopo["refusal_reason"] == "fora_de_escopo"


def test_rota_apenas_nao_regride_abaixo_da_taxa_observada():
    """Trava de regressão: o roteador acerta 22/22 desde 30/09 — uma queda aqui é sinal real."""
    cases = load_golden_set(GOLDEN_SET)

    report = run_eval(cases, rota_apenas)

    assert report["acertos"] >= 22, (
        f"roteador regrediu: {report['acertos']}/{report['total']} "
        f"(esperado >= 22) — veja eval/runs/ para o relatório completo"
    )


def test_rota_apenas_da_o_motivo_certo_nos_guardrails():
    cases = {case.id: case for case in load_golden_set(GOLDEN_SET)}

    for case_id in ("G1", "G2", "G3"):
        resultado = rota_apenas(cases[case_id].pergunta)
        assert resultado["is_refusal"] is True
        assert resultado["refusal_reason"] == cases[case_id].refusal_reason_esperado


def test_metricas_do_log_soma_so_o_run_id_pedido(tmp_path):
    log = tmp_path / "pipeline-2026-09-30.jsonl"
    registros = [
        {"run_id": "r1", "stage": "retrieval_denso", "latencia_ms": 20, "tokens": {"total": 9}},
        {"run_id": "r1", "stage": "geracao", "latencia_ms": 1000, "tokens": {"total": 500}},
        {"run_id": "r1", "stage": "geracao", "latencia_ms": 5, "tokens": {"total": 1}},
        {"run_id": "outro", "stage": "geracao", "latencia_ms": 999, "tokens": {"total": 999}},
    ]
    linhas = [json.dumps(r) for r in registros] + ["linha quebrada"]
    log.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    metricas = metricas_do_log("r1", [log, tmp_path / "inexistente.jsonl"])

    assert metricas["tokens_total"] == 510
    assert metricas["etapas"]["geracao"] == {"tokens": 501, "latencia_ms": 1005}
    assert metricas["etapas"]["retrieval_denso"] == {"tokens": 9, "latencia_ms": 20}


def test_golden_faz_a_pre_carga_da_api_fora_da_latencia_dos_casos(monkeypatch):
    """Sem a pré-carga, a primeira pergunta de cada rodada pagava o carregamento do
    cross-encoder (16 a 36 s) e outra pagava a montagem do índice da BM25."""
    import config
    from eval import adapters, run_eval
    from src import rerank, retrieve

    chamadas = []
    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(config, "RERANK_PRELOAD", True)
    monkeypatch.setattr(config, "INDEX_PRELOAD", True)
    monkeypatch.setattr(rerank, "pre_carregar", lambda: chamadas.append("rerank"))
    monkeypatch.setattr(retrieve, "pre_carregar_indices", lambda: chamadas.append("indices"))

    assert run_eval._preparar(adapters.pipeline_completo) is not None
    assert chamadas == ["rerank", "indices"]
    # Adaptador sem pré-carga segue igual.
    assert run_eval._preparar(adapters.rota_apenas) is None

