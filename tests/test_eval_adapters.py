from pathlib import Path

from eval.adapters import rota_apenas
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
    """Trava de regressão: hoje o roteador acerta 15/19 — uma queda aqui é sinal real."""
    cases = load_golden_set(GOLDEN_SET)

    report = run_eval(cases, rota_apenas)

    assert report["acertos"] >= 15, (
        f"roteador regrediu: {report['acertos']}/{report['total']} "
        f"(esperado >= 15) — veja eval/runs/ para o relatório completo"
    )
