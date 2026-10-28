"""Leitura da resposta do LLM: JSON, campos, nível de confiança e recusa.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

from typing import Any, Optional

from src.domain import confidence
from src.schema import Chunk, ConfidenceLevel, Natureza, RAGResponse, RefusalReason, SourceRef
from src.generation.responses import _extrair_json, _fonte_do_chunk, _recusa
from src.generation.sql_answer import _MARCA_SQL_SEM_REGISTROS, _TEXTO_SQL_SEM_REGISTROS, _sql_sem_registros


# Nomes que o LLM usa no lugar das chaves do contrato (o qwen3-8b devolve
# "sources" e "confidence": 0.95 apesar do prompt). O primeiro é o canônico.
_ALIASES_CHAVES: dict[str, tuple[str, ...]] = {
    "sources_used": ("sources_used", "sources", "fontes"),
    "confidence_level": ("confidence_level", "confidence", "confianca", "confiança"),
}


# Cortes para converter uma confiança numérica (0 a 1) em ConfidenceLevel.
_CONFIANCA_ALTA = 0.8


_CONFIANCA_MEDIA = 0.5


def _campo(dados: dict, chave: str, alternativas_usadas: list[str]) -> Any:
    """Lê ``chave`` ou um de seus aliases, anotando quando veio por alias."""
    for nome in _ALIASES_CHAVES[chave]:
        if nome in dados:
            if nome != chave:
                alternativas_usadas.append(nome)
            return dados[nome]
    return None


def _nivel_de_confianca(valor: Any) -> ConfidenceLevel:
    if isinstance(valor, bool):
        return ConfidenceLevel.BAIXA
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        try:
            return ConfidenceLevel(str(valor).lower())
        except ValueError:
            return ConfidenceLevel.BAIXA
    if numero >= _CONFIANCA_ALTA:
        return ConfidenceLevel.ALTA
    if numero >= _CONFIANCA_MEDIA:
        return ConfidenceLevel.MEDIA
    return ConfidenceLevel.BAIXA


def _parsear_resposta_llm(
    bruto: str,
    query: str,
    chunks: list[Chunk],
    bases_consultadas: list[Natureza],
) -> tuple[RAGResponse, Optional[str], list[str], list[str]]:
    """Devolve a resposta, o motivo se a saída do LLM for inválida, as chaves
    fora do contrato que foram aceitas como alias e os chunk_ids citados pelo
    LLM que não estavam entre os chunks recuperados (descartados)."""
    chunks_por_id = {c.metadata.chunk_id: c for c in chunks}
    chunks_por_evidence_id = {
        f"E{index}": chunk for index, chunk in enumerate(chunks, start=1)
    }
    alternativas_usadas: list[str] = []
    citacoes_descartadas: list[str] = []
    try:
        dados = _extrair_json(bruto)
        answer = str(dados["answer"]).strip()
        is_refusal = bool(dados.get("is_refusal", False))

        sem_registros_sql = _sql_sem_registros(chunks)
        confidence_level = _nivel_de_confianca(_campo(dados, "confidence_level", alternativas_usadas))

        sources_used: list[SourceRef] = []
        fontes_adicionadas: set[str] = set()
        for referencia in _campo(dados, "sources_used", alternativas_usadas) or []:
            referencia = referencia if isinstance(referencia, str) else str(referencia)
            chunk = chunks_por_evidence_id.get(referencia) or chunks_por_id.get(referencia)
            if chunk is None:
                citacoes_descartadas.append(referencia)
                continue
            if chunk.metadata.chunk_id in fontes_adicionadas:
                continue
            fontes_adicionadas.add(chunk.metadata.chunk_id)
            sources_used.append(_fonte_do_chunk(chunk))

        refusal_reason: Optional[RefusalReason] = None
        if is_refusal:
            try:
                refusal_reason = RefusalReason(dados.get("refusal_reason"))
            except ValueError:
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            if refusal_reason is RefusalReason.ERRO_PROVEDOR:
                # Só o código marca falha de infraestrutura; o LLM não decide isso.
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            sources_used = []
            confidence_level = ConfidenceLevel.RECUSADO
            if not answer:
                answer = _recusa(query, bases_consultadas, refusal_reason).answer
        elif confidence_level == ConfidenceLevel.RECUSADO:
            confidence_level = ConfidenceLevel.BAIXA
        # A confiança de um zero vem do recorte (_aplicar_recorte); a ressalva
        # deixa o escopo da amostra no próprio texto, à vista mesmo onde os
        # avisos não são mostrados.
        if not is_refusal and sem_registros_sql and _MARCA_SQL_SEM_REGISTROS not in answer.casefold():
            answer = f"{answer}\n\n{_TEXTO_SQL_SEM_REGISTROS}"
        if not answer:
            raise ValueError("Resposta sem texto")

        resposta = RAGResponse(
            query=query,
            answer=answer,
            bases_consultadas=bases_consultadas,
            sources_used=sources_used,
            confidence_level=confidence_level,
            is_refusal=is_refusal,
            refusal_reason=refusal_reason,
        )
        return resposta, None, alternativas_usadas, citacoes_descartadas
    except Exception as exc:
        # Saída inválida do LLM vira recusa segura; o motivo vai para o log.
        # É falha do serviço, não falta de evidência: o usuário vê "tente de novo".
        return (
            _recusa(query, bases_consultadas, RefusalReason.ERRO_PROVEDOR),
            f"{type(exc).__name__}: {exc}",
            alternativas_usadas,
            citacoes_descartadas,
        )
