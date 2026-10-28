"""Guardrails antes da geração: pedidos que viram recusa sem chamar o LLM.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
from typing import Optional

from src.schema import RefusalReason


# --------------------------------------------------------------------------
# Guardrails determinísticos, antes de chamar o LLM
# --------------------------------------------------------------------------

_PADROES_GUARDRAIL: dict[RefusalReason, list[str]] = {
    RefusalReason.DADOS_BANCARIOS: [
        r"n[uú]mero da conta banc[aá]ria",
        r"ag[eê]ncia e conta",
        r"chave pix",
        r"dados banc[aá]rios (do|da) (fornecedor|empresa|contratad[oa])",
    ],
    RefusalReason.CREDENCIAIS_VAZADAS: [
        r"senha (do|de|da)",
        r"credencia(l|is) de acesso",
        r"token de acesso",
        r"chave de api",
    ],
}


def _checar_guardrail_previo(query: str) -> Optional[RefusalReason]:
    q = query.lower()
    for motivo, padroes in _PADROES_GUARDRAIL.items():
        if any(re.search(p, q) for p in padroes):
            return motivo
    return None
