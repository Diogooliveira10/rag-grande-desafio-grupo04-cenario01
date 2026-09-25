"""Adaptadores que plugam módulos já implementados do pipeline no harness de eval/run_eval.py.

``rota_apenas`` avalia só o estágio ``roteamento`` (``src/router.py``): é
determinístico, offline e não chama o LLM — o suficiente para medir se o
roteador escolhe as bases certas e recusa nos casos de recusa/fora de escopo
do golden set, sem o custo (tempo, tokens) de rodar a geração.

Agora que ``src/retrieve.py`` existe, o pipeline completo (roteamento +
recuperação híbrida + geração) roda direto via ``src/pipeline.py:responder``
— já compatível com o contrato de ``eval/run_eval.py`` sem precisar de
adaptador:

    py .\\eval\\run_eval.py run --pipeline src.pipeline:responder
"""

from __future__ import annotations

from typing import Any

from src.router import inferir_motivo_recusa, rotear_pergunta


def rota_apenas(pergunta: str) -> dict[str, Any]:
    """Roda só ``rotear_pergunta`` e devolve o formato que ``evaluate_case`` espera."""
    decisao = rotear_pergunta(pergunta)
    bases = [base.value for base in decisao.bases_selecionadas]
    is_refusal = not bases
    motivo = inferir_motivo_recusa(decisao.justificativa) if is_refusal else None
    return {
        "bases_consultadas": bases,
        "is_refusal": is_refusal,
        "refusal_reason": motivo.value if motivo else None,
    }


__all__ = ["rota_apenas"]
