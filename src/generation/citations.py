"""Citações da resposta: fontes numeradas, trechos mencionados no texto e recusa indevida convertida.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
from typing import Optional

from src.schema import Chunk, ConfidenceLevel, Natureza, RAGResponse, RefusalReason, SourceRef
from src.generation.responses import _fonte_do_chunk, _recusa


_PEDIDO_DE_CITACAO = (
    "Sua resposta não citou nenhuma evidência em sources_used, mas o contexto tem trechos "
    "relacionados, então ela não é uma recusa. Devolva o mesmo JSON com is_refusal=false e "
    "refusal_reason=null, sem mudar a conclusão, citando em sources_used e no texto ([E1]) os "
    "evidence_id em que a resposta se apoia, inclusive os trechos que você examinou para "
    "concluir que algo falta."
)


def _falta_citar(resposta: RAGResponse, chunks: list[Chunk], erro_parsing: Optional[str]) -> bool:
    """Resposta (não recusa) sem nenhuma fonte, nem citada nem mencionada no texto."""
    return (not erro_parsing and not resposta.is_refusal and bool(chunks)
            and not _incluir_trechos_mencionados(resposta, chunks).sources_used)


# Identificadores internos que o LLM escreve no texto: evidence_id do prompt
# (E1), chunk_id completo (base:hash:chunk-...), resultado SQL (sql:tabela:...)
# e pedaços de chunk_id (chunk-1-7303119b4364, 10-f8902f79c194).
_ID_INTERNO = (
    r"(?:E\d{1,3}\b|sql:[a-z_]+(?::[\w.-]+)+|[a-z_]+:[0-9a-f]{8,}(?::[\w-]+)+"
    r"|chunk-\d+-[0-9a-f]{6,}|\b\d+-[0-9a-f]{12}\b)"
)


_ROTULO_ID = r"(?:evidence_id|chunk_id|evid[eê]ncias?|trechos?|chunks?|fontes?)"


_SEP_ID = r"\s*(?:,|;|\be\b)\s*"


# Grupo entre parênteses ou colchetes: "(evidence_id=E1)", "[E1, E2]", "(chunks 10-… e 23-…)".
_RE_GRUPO_IDS = re.compile(
    rf"\s*[\(\[]\s*(?:{_ROTULO_ID}\s*[=:]?\s*)?({_ID_INTERNO}(?:{_SEP_ID}(?:{_ROTULO_ID}\s*[=:]?\s*)?{_ID_INTERNO})*)\s*[\)\]]",
    re.IGNORECASE,
)


# Fora de grupo, só o que não tem como ser texto comum: rótulo explícito ou id completo.
# "O trecho E2 prevê", sem "=" nem ":": os modelos de reserva escrevem assim,
# apesar do prompt (o ECOA não). Só com o rótulo: "a classe E1" é texto comum.
_RE_E_SEM_SEPARADOR = re.compile(
    r"(?P<rotulo>(?:\b(?:trechos?|evid[eê]ncias?|fontes?|registros?|documentos?|contrata(?:ç|c)(?:ão|ao|ões|oes)"
    # "o E2 trata", "e E2 é uma norma", "identificada em E3", "como E1", "regra de
    # E2", "são E2", "apenas E3": artigo, "e", preposição ou verbo logo antes
    # também marca o id. "A classe E1" continua texto comum.
    r"|o|a|e|de|do|da|no|na|em|como|conforme|segundo|pelo|pela|sao|são|apenas|somente)\s+"
    # Lista depois de uma citação já numerada: "os registros [3], E3 e E4".
    r"|(?<=\]),\s*))"
    r"(?P<id>E\d{1,3})\b(?![\w-])",
    re.IGNORECASE,
)


_RE_ID_SOLTO = re.compile(
    rf"(?:{_ROTULO_ID}\s*[=:]\s*{_ID_INTERNO}|sql:[a-z_]+(?::[\w.-]+)+|[a-z_]+:[0-9a-f]{{8,}}(?::[\w-]+)+)",
    re.IGNORECASE,
)


def _incluir_trechos_mencionados(resposta: RAGResponse, chunks: list[Chunk]) -> RAGResponse:
    """Trecho que o texto menciona ("o registro E1 não informa") entra nas fontes.

    O LLM usou o trecho para afirmar algo, então ele é fonte; sem isso, o texto
    mostrava "E1", que não diz nada ao usuário. Vale só para E<n> com rótulo ou
    artigo antes (``_RE_E_SEM_SEPARADOR``) e que exista no prompt. Recusa
    continua sem fontes.
    """
    if resposta.is_refusal:
        return resposta
    citados = {fonte.chunk_id for fonte in resposta.sources_used}
    novas: list[SourceRef] = []
    for achado in _RE_E_SEM_SEPARADOR.finditer(resposta.answer):
        indice = int(achado.group("id")[1:])
        if not 1 <= indice <= len(chunks):
            continue
        chunk = chunks[indice - 1]
        if chunk.metadata.chunk_id not in citados:
            citados.add(chunk.metadata.chunk_id)
            novas.append(_fonte_do_chunk(chunk))
    if not novas:
        return resposta
    return resposta.model_copy(update={"sources_used": [*resposta.sources_used, *novas]})


def _numerar_citacoes(resposta: RAGResponse, chunks: list[Chunk]) -> tuple[RAGResponse, int]:
    """Troca identificadores internos do texto por ``[n]``, o número da fonte em
    ``sources_used``; id que não é fonte aceita sai do texto.

    O prompt pede o evidence_id junto da afirmação (liga cada parte da resposta
    à sua fonte), mas ``E1`` ou ``contratos_estruturado:…`` não dizem nada ao
    usuário. Devolve a resposta e quantos identificadores foram trocados.
    """
    resposta = _incluir_trechos_mencionados(resposta, chunks)
    numero_da_fonte = {fonte.chunk_id: indice for indice, fonte in enumerate(resposta.sources_used, start=1)}
    por_evidence_id = {f"E{indice}": chunk.metadata.chunk_id for indice, chunk in enumerate(chunks, start=1)}

    def numero(token: str) -> Optional[int]:
        token = re.sub(rf"^{_ROTULO_ID}\s*[=:]?\s*", "", token.strip(), flags=re.IGNORECASE)
        chunk_id = por_evidence_id.get(token, token)
        if chunk_id in numero_da_fonte:
            return numero_da_fonte[chunk_id]
        for completo, indice in numero_da_fonte.items():
            if completo.endswith(token) or completo.endswith(f"chunk-{token}"):
                return indice
        return None

    trocas = 0

    def grupo(match: re.Match) -> str:
        nonlocal trocas
        tokens = re.findall(_ID_INTERNO, match.group(1))
        trocas += len(tokens)
        numeros = sorted({n for n in (numero(t) for t in tokens) if n is not None})
        return f" [{', '.join(map(str, numeros))}]" if numeros else ""

    def solto(match: re.Match) -> str:
        nonlocal trocas
        trocas += 1
        n = numero(match.group(0))
        return f"[{n}]" if n is not None else ""

    def e_solto(match: re.Match) -> str:
        # "O trecho E2 prevê", "o E3 trata": só troca se E<n> for uma fonte
        # citada; senão o texto fica como veio.
        nonlocal trocas
        n = numero(match.group("id"))
        if n is None:
            return match.group(0)
        trocas += 1
        return f"{match.group('rotulo') or ''}[{n}]"

    texto = _RE_GRUPO_IDS.sub(grupo, resposta.answer)
    texto = _RE_ID_SOLTO.sub(solto, texto)
    texto = _RE_E_SEM_SEPARADOR.sub(e_solto, texto)
    if not trocas:
        return resposta, 0
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"\s+([.,;:])", r"\1", texto).strip()
    return resposta.model_copy(update={"answer": texto}), trocas


# Recusas que o LLM pode marcar por engano; as de guardrail (dados bancários,
# credenciais, juízo de legalidade) são decididas antes do LLM e nunca mudam.
_RECUSAS_CONVERTIVEIS = (RefusalReason.FORA_DE_ESCOPO, RefusalReason.SEM_EVIDENCIA)


def _converter_recusa_indevida(
    resposta: RAGResponse, bases_consultadas: list[Natureza], erro_parsing: Optional[str]
) -> tuple[RAGResponse, Optional[str]]:
    """Recusa com texto, numa pergunta que o roteador pôs no escopo, vira resposta.

    O qwen3-8b escreve uma resposta útil ("o contexto só traz dados municipais,
    com mediana de R$ 519.500") e ainda marca ``is_refusal`` como fora de escopo
    ou sem evidência. Se o roteador escolheu bases, a pergunta está no escopo, e
    dizer o que os dados mostram e o que falta é a resposta esperada; ela sai com
    confiança baixa, e o log registra a conversão em ``recusa_convertida``.
    """
    if (
        erro_parsing
        or not resposta.is_refusal
        or not bases_consultadas
        or resposta.refusal_reason not in _RECUSAS_CONVERTIVEIS
        or not resposta.answer.strip()
        or resposta.answer == _recusa(resposta.query, bases_consultadas, resposta.refusal_reason).answer
    ):
        return resposta, None
    motivo = resposta.refusal_reason.value
    return resposta.model_copy(update={
        "is_refusal": False, "refusal_reason": None, "confidence_level": ConfidenceLevel.BAIXA,
    }), motivo
