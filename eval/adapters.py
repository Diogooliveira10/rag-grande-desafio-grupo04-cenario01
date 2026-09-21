"""Adaptadores que plugam módulos já implementados do pipeline no harness de eval/run_eval.py.

``retrieve.py`` ainda não existe, então não há como rodar o pipeline completo
(roteamento + recuperação + geração) contra o golden set. ``rota_apenas``
avalia só o estágio ``roteamento`` (``src/router.py``), que já está pronto —
é o suficiente para medir se o roteador escolhe as bases certas e recusa nos
casos de recusa/fora de escopo do golden set.
"""

from __future__ import annotations

from typing import Any, Optional

from src.router import rotear_pergunta

# Palavras da justificativa do roteador (src/router.py) usadas para inferir o
# RefusalReason equivalente — router.py hoje só devolve texto livre, não o enum.
_PISTAS_MOTIVO = (
    ("dado bancario", "dados_bancarios_fornecedor"),
    ("dados bancarios", "dados_bancarios_fornecedor"),
    ("credencia", "credenciais_vazadas"),
    ("juizo", "juizo_legalidade_contrato"),
    ("legalidade", "juizo_legalidade_contrato"),
    ("fora do corpus", "fora_de_escopo"),
    ("nao foi identificado", "sem_evidencia"),
)


def _inferir_motivo(justificativa: str) -> Optional[str]:
    texto = justificativa.lower()
    for pista, motivo in _PISTAS_MOTIVO:
        if pista in texto:
            return motivo
    return None


def rota_apenas(pergunta: str) -> dict[str, Any]:
    """Roda só ``rotear_pergunta`` e devolve o formato que ``evaluate_case`` espera."""
    decisao = rotear_pergunta(pergunta)
    bases = [base.value for base in decisao.bases_selecionadas]
    is_refusal = not bases
    return {
        "bases_consultadas": bases,
        "is_refusal": is_refusal,
        "refusal_reason": _inferir_motivo(decisao.justificativa) if is_refusal else None,
    }


__all__ = ["rota_apenas"]
