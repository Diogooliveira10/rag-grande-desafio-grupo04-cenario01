"""Eventos da geração no log: prompt e resposta mascarados e limitados, e a validação.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import time
from typing import Optional

from src.obs import Stage, TokenUsage, registrar
from src.schema import RAGResponse
from src.sensitivity import mascarar


def _logar_evento(
    run_id: str,
    query_id: str,
    status: str,
    inicio: float,
    resposta: RAGResponse,
    *,
    tokens: Optional[TokenUsage] = None,
    modelo: Optional[str] = None,
    **extra,
) -> None:
    """Registra o stage ``geracao`` no contrato de ``src/obs.py``."""
    if resposta.is_refusal:
        output = f"recusa: {resposta.refusal_reason.value if resposta.refusal_reason else '-'}"
    else:
        output = f"resposta {resposta.confidence_level.value}; {len(resposta.sources_used)} fontes"
    if status == "ok" and (extra.get("llm") or {}).get("fallback_used"):
        status = "degradado"
    registrar(
        run_id=run_id,
        query_id=query_id,
        stage=Stage.GERACAO,
        status=status,
        latencia_ms=(time.monotonic() - inicio) * 1000,
        input=resposta.query,
        output=output,
        tokens=tokens,
        modelo=modelo,
        extra={
            "confidence_level": resposta.confidence_level.value,
            "is_refusal": resposta.is_refusal,
            "bases_consultadas": [b.value for b in resposta.bases_consultadas],
            "num_sources": len(resposta.sources_used),
            "answer_truncated": len(mascarar(resposta.answer or "")[0]) > _LIMITE_DE_TEXTO_NO_LOG,
            "log_text_limit": _LIMITE_DE_TEXTO_NO_LOG,
            # A resposta como saiu da etapa, para acompanhar o dado até o usuário.
            "resposta": {
                "answer": _texto_para_log(resposta.answer),
                "sources_used": [fonte.chunk_id for fonte in resposta.sources_used],
                "refusal_reason": resposta.refusal_reason.value if resposta.refusal_reason else None,
            },
            **extra,
        },
    )


# Tamanho máximo de cada texto do prompt e da resposta gravado no log. O
# prompt fica em torno de GENERATION_CONTEXT_CHARS; o limite só corta exceções.
_LIMITE_DE_TEXTO_NO_LOG = 8000


def _texto_para_log(texto: str) -> str:
    """Texto mascarado (CPF, telefone, e-mail pessoal) e cortado no limite do log."""
    mascarado = mascarar(texto or "")[0]
    if len(mascarado) <= _LIMITE_DE_TEXTO_NO_LOG:
        return mascarado
    return mascarado[:_LIMITE_DE_TEXTO_NO_LOG] + "…"


def _prompt_para_log(mensagens: list[dict]) -> list[dict[str, str]]:
    """Mensagens enviadas ao LLM como vão para o log. Os chunks restritos já
    ficaram de fora antes do prompt (``filtrar_chunks_permitidos``)."""
    return [
        {"role": str(mensagem.get("role", "")), "content": _texto_para_log(str(mensagem.get("content", "")))}
        for mensagem in mensagens
    ]


def _logar_validacao(
    run_id: str,
    query_id: str,
    inicio: float,
    resposta: RAGResponse,
    *,
    erro_parsing: Optional[str],
    citacoes_descartadas: list[str],
    chaves_alias: list[str],
    mascaramentos_resposta: dict[str, int],
    recusa_convertida: Optional[str] = None,
) -> None:
    """Registra o stage ``validacao``: o que foi checado/corrigido na saída do LLM.

    Separado de ``geracao`` (chamada ao LLM, tokens) para o log mostrar quanto
    da resposta final veio de correção: JSON inválido virou recusa, citação de
    chunk não recuperado foi descartada, dado pessoal foi mascarado.
    """
    registrar(
        run_id=run_id,
        query_id=query_id,
        stage=Stage.VALIDACAO,
        status="saida_invalida" if erro_parsing else "ok",
        latencia_ms=(time.monotonic() - inicio) * 1000,
        input=resposta.query,
        output=f"{len(resposta.sources_used)} fontes válidas; {len(citacoes_descartadas)} descartadas",
        extra={
            "json_valido": erro_parsing is None,
            "citacoes_aceitas": [source.chunk_id for source in resposta.sources_used],
            "citacoes_descartadas": citacoes_descartadas,
            "chaves_fora_do_contrato": chaves_alias,
            "mascaramentos_resposta": mascaramentos_resposta,
            "recusa_convertida": recusa_convertida,
            "confidence_level": resposta.confidence_level.value,
            "is_refusal": resposta.is_refusal,
        },
    )
