"""Política de sensibilidade do Cenário 1 — a parte "Mascarar".

Responder (público por lei): valores de contrato, nome de fornecedor, CNPJ de
pessoa jurídica, objeto contratado. Nada aqui toca esses dados.

Mascarar: CPF de pessoa física, telefone e e-mail pessoal que aparecem em atas.
O mascaramento é determinístico (regex) e aplicado em ``src/generate.py`` em dois
pontos: no contexto antes de ir ao LLM (o modelo nunca vê o dado) e na resposta
final (defesa em profundidade, caso o dado venha da própria pergunta).

Recusar: tratado por ``RefusalReason`` em ``src/router.py`` e ``src/generate.py``.

Chunks com ``sensitivity=restrito`` nunca chegam ao LLM
(``filtrar_chunks_permitidos``).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping

from src.schema import Chunk, Sensibilidade


MASCARA_CPF = "[CPF omitido]"
MASCARA_TELEFONE = "[telefone omitido]"
MASCARA_EMAIL = "[e-mail omitido]"

# E-mails de domínio institucional são canais públicos do órgão; o que se
# mascara é o e-mail pessoal (gmail, hotmail, domínio de empresa etc.).
DOMINIOS_INSTITUCIONAIS = (".gov.br", ".jus.br", ".mp.br", ".leg.br", ".def.br", ".mil.br")

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")

# CPF formatado (000.000.000-00). As bordas impedem casar pedaço de CNPJ
# (00.000.000/0000-00) ou de outro número maior.
_CPF_FORMATADO = re.compile(r"(?<![\d./-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\d/-])")
# CPF sem pontuação só é reconhecido quando rotulado ("CPF: 12345678909"),
# para não mascarar ids de processo, códigos PNCP etc.
_CPF_ROTULADO = re.compile(
    r"(\bCPF\b\s*(?:n[º°o.]?\s*)?[:\-]?\s*)(\d{3}\.?\d{3}\.?\d{3}-?\d{2})(?!\d)",
    re.IGNORECASE,
)

_NUMERO_TELEFONE = r"(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s.]?\d{4}"
# Telefone com DDD entre parênteses ou prefixo +55 é reconhecido sozinho.
_TELEFONE_COM_DDD = re.compile(
    r"(?<![\w(])(?:\+55\s?\(?\d{2}\)?|\(\d{2}\))\s?9?\d{4}[-\s.]?\d{4}(?!\d)"
)
# Sem DDD formatado, só quando rotulado — "3333-4444" solto pode ser outra coisa.
_TELEFONE_ROTULADO = re.compile(
    r"(\b(?:tel(?:efone)?s?|fone|celular|cel|whats(?:app)?)\b\.?\s*(?:n[º°o.]?\s*)?[:\-]?\s*)"
    rf"({_NUMERO_TELEFONE})(?!\d)",
    re.IGNORECASE,
)


def _eh_institucional(dominio: str) -> bool:
    dominio = dominio.lower()
    return any(dominio.endswith(sufixo) for sufixo in DOMINIOS_INSTITUCIONAIS)


def mascarar(texto: str) -> tuple[str, dict[str, int]]:
    """Mascara CPF, telefone e e-mail pessoal em ``texto``.

    Devolve o texto mascarado e a contagem de ocorrências por tipo (só os tipos
    com ocorrência), para log.
    """
    contagem: Counter[str] = Counter()

    def _email(m: re.Match) -> str:
        if _eh_institucional(m.group(1)):
            return m.group(0)
        contagem["email"] += 1
        return MASCARA_EMAIL

    def _rotulado(tipo: str, mascara: str):
        def _substituir(m: re.Match) -> str:
            contagem[tipo] += 1
            return m.group(1) + mascara

        return _substituir

    def _inteiro(tipo: str, mascara: str):
        def _substituir(m: re.Match) -> str:
            contagem[tipo] += 1
            return mascara

        return _substituir

    # E-mail primeiro: dígitos dentro de um e-mail não devem virar telefone/CPF.
    texto = _EMAIL.sub(_email, texto)
    texto = _CPF_ROTULADO.sub(_rotulado("cpf", MASCARA_CPF), texto)
    texto = _CPF_FORMATADO.sub(_inteiro("cpf", MASCARA_CPF), texto)
    texto = _TELEFONE_ROTULADO.sub(_rotulado("telefone", MASCARA_TELEFONE), texto)
    texto = _TELEFONE_COM_DDD.sub(_inteiro("telefone", MASCARA_TELEFONE), texto)
    return texto, dict(contagem)


def mascarar_valores(valor):
    """Aplica ``mascarar`` a cada texto de ``valor``, percorrendo dicionários e listas.

    Usado no que fica gravado (log de ``src/obs.py`` e histórico de
    ``src/history_store.py``); números e outros tipos passam intactos.
    """
    if isinstance(valor, str):
        return mascarar(valor)[0]
    if isinstance(valor, Mapping):
        return {chave: mascarar_valores(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [mascarar_valores(item) for item in valor]
    return valor


def filtrar_chunks_permitidos(chunks: list[Chunk]) -> tuple[list[Chunk], int]:
    """Descarta chunks ``restrito``; devolve (permitidos, quantidade descartada)."""
    permitidos = [c for c in chunks if c.metadata.sensitivity != Sensibilidade.RESTRITO]
    return permitidos, len(chunks) - len(permitidos)


def mascarar_chunks(chunks: list[Chunk]) -> tuple[list[Chunk], dict[str, int]]:
    """Aplica ``mascarar`` ao conteúdo de cada chunk, sem alterar os originais."""
    total: Counter[str] = Counter()
    mascarados: list[Chunk] = []
    for chunk in chunks:
        conteudo, contagem = mascarar(chunk.content)
        total.update(contagem)
        mascarados.append(chunk if not contagem else chunk.model_copy(update={"content": conteudo}))
    return mascarados, dict(total)


__all__ = [
    "DOMINIOS_INSTITUCIONAIS",
    "MASCARA_CPF",
    "MASCARA_EMAIL",
    "MASCARA_TELEFONE",
    "filtrar_chunks_permitidos",
    "mascarar",
    "mascarar_chunks",
    "mascarar_valores",
]
