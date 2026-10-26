"""Situação da compra no PNCP: quais entram nas contas.

Anulada, revogada e suspensa não valem como contratação e ficam fora de toda
conta, salvo quando a pergunta fala delas. O PNCP não tem "cancelada"; na
pergunta, ela vale anulada e revogada.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from src.domain.text import normalizar

SITUACOES_INVALIDAS = ("Anulada", "Revogada", "Suspensa")
_RE_SITUACAO = (
    ("Anulada", re.compile(r"\banulad[ao]s?\b|\bcancelad[ao]s?\b")),
    ("Revogada", re.compile(r"\brevogad[ao]s?\b|\bcancelad[ao]s?\b")),
    ("Suspensa", re.compile(r"\bsuspens[ao]s?\b")),
)
_RE_TAMBEM_VALIDAS = re.compile(r"\b(?:vigentes?|ativas|valid[ao]s|inclusive|incluindo)\b")


def situacoes_da_pergunta(pergunta: str) -> dict[str, object]:
    """Situações inválidas citadas e se a pergunta também quer as válidas.

    Devolve os campos ``situacoes_citadas`` e ``com_situacoes_validas`` da
    consulta. Os dois caminhos da consulta SQL (regras e intenção do LLM) usam
    esta função; antes cada um decidia por conta própria, e "anuladas e
    vigentes" saía diferente em cada.
    """
    texto = normalizar(pergunta)
    citadas = tuple(situacao for situacao, padrao in _RE_SITUACAO if padrao.search(texto))
    return {"situacoes_citadas": citadas,
            "com_situacoes_validas": bool(citadas and _RE_TAMBEM_VALIDAS.search(texto))}


def clausula_de_situacao(citadas: tuple[str, ...], com_validas: bool, todas: bool) -> Optional[str]:
    """Filtro SQL por situação; os valores são constantes deste módulo.

    ``IS NULL`` mantém empenhos e contratos, que não têm situação no PNCP.
    ``todas``: consulta por identificador, em que o registro pedido aparece em
    qualquer situação.
    """
    if todas:
        return None
    if citadas and not com_validas:
        return f"situacao IN ({_lista_sql(citadas)})"
    excluidas = tuple(s for s in SITUACOES_INVALIDAS if s not in citadas)
    if not excluidas:
        return None
    return f"(situacao IS NULL OR situacao NOT IN ({_lista_sql(excluidas)}))"


def _lista_sql(valores: Iterable[str]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)
