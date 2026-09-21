from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from src.schema import (
    Chunk,
    ChunkMetadata,
    ConfidenceLevel,
    Natureza,
    RAGResponse,
    RefusalReason,
    SourceRef,
)

# --------------------------------------------------------------------------
# Logging estruturado — logs/pipeline-AAAA-MM-DD.jsonl (contrato da pipeline)
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"

logger = logging.getLogger("pipeline.geracao")


def _setup_logging() -> None:
    if logger.handlers:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"pipeline-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"

    class JsonlHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            extra = getattr(record, "extra_fields", None)
            if extra:
                entry.update(extra)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.setLevel(logging.INFO)
    logger.addHandler(JsonlHandler())


def log_event(level: str, msg: str, **fields) -> None:
    _setup_logging()
    getattr(logger, level)(msg, extra={"extra_fields": fields})


# --------------------------------------------------------------------------
# Cliente LLM — único ponto de integração real com llm_ecoa.py
# --------------------------------------------------------------------------

GENERATION_MODEL = "qcwind/qwen3-8b-instruct-Q4-K-M"


class ClienteLLM(Protocol):
    def __call__(self, mensagens: list[dict]) -> str: ...


def _cliente_padrao() -> ClienteLLM:
    """
    Wrapper fino sobre o cliente compartilhado llm_ecoa.py.

    TODO (confirmar com o repositório real): ajustar a chamada abaixo para a assinatura
    exata exportada por llm_ecoa.py — o resto de generate.py não depende disso, só desta
    função. Enquanto isso não é confirmado, chamar generate.py sem passar `cliente=` vai
    levantar ImportError/AttributeError explicitamente, em vez de silenciosamente usar o
    endpoint errado.
    """
    import llm_ecoa  

    def _chamar(mensagens: list[dict]) -> str:
        return llm_ecoa.chat_completion(mensagens, model=GENERATION_MODEL)  # type: ignore[attr-defined]

    return _chamar


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


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = """Você é o assistente de um sistema RAG sobre contratações públicas do \
Estado do Rio de Janeiro (dados do PNCP). Responda SOMENTE com base nos trechos de contexto \
fornecidos — nunca invente números, valores ou nomes de órgãos que não estejam no contexto.

Recuse a pergunta (is_refusal=true) quando ela pedir: dados bancários de fornecedor \
(dados_bancarios_fornecedor), credenciais de acesso vazadas (credenciais_vazadas), um juízo \
sobre a legalidade de um contrato específico (juizo_legalidade_contrato), algo fora do escopo \
de contratações públicas do RJ (fora_de_escopo), ou quando o contexto fornecido não tiver \
evidência suficiente para responder (sem_evidencia).

Responda SEMPRE e SOMENTE com um objeto JSON, sem nenhum texto antes ou depois, no formato:
{"answer": "...", "is_refusal": false, "refusal_reason": null, \
"confidence_level": "alta|media|baixa|recusado", "sources_used": ["chunk_id", ...]}

sources_used deve conter apenas os chunk_id que você realmente usou, exatamente como \
aparecem no contexto. Se is_refusal for true, sources_used deve ser uma lista vazia."""


def _natureza_label(metadata: ChunkMetadata) -> str:
    """``natureza`` é a classificação Adaptive RAG; ``doc_type`` é o formato do
    arquivo (json/pdf/html) — só cai para ``doc_type`` se natureza não veio."""
    return metadata.natureza.value if metadata.natureza is not None else metadata.doc_type


def _montar_prompt(query: str, chunks: list[Chunk]) -> list[dict]:
    contexto = "\n\n".join(
        f"[chunk_id={c.metadata.chunk_id} | base={c.metadata.base_id} | "
        f"natureza={_natureza_label(c.metadata)}]\n{c.content}"
        for c in chunks
    )
    mensagem_usuario = f"Contexto:\n{contexto}\n\nPergunta: {query}"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": mensagem_usuario},
    ]


# --------------------------------------------------------------------------
# Parsing defensivo da saída do LLM
# --------------------------------------------------------------------------

def _extrair_json(bruto: str) -> dict:
    texto = bruto.strip()
    texto = re.sub(r"^```(?:json)?", "", texto).strip()
    texto = re.sub(r"```$", "", texto).strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim == -1 or fim < inicio:
        raise ValueError("Nenhum objeto JSON encontrado na resposta do LLM")
    return json.loads(texto[inicio : fim + 1])


def _recusa(
    query: str,
    bases_consultadas: list[Natureza],
    motivo: RefusalReason,
    *,
    motivo_erro: Optional[str] = None,
) -> RAGResponse:
    respostas_padrao = {
        RefusalReason.SEM_EVIDENCIA: (
            "Não encontrei evidência suficiente nas bases consultadas para responder com segurança."
        ),
        RefusalReason.DADOS_BANCARIOS: "Não posso fornecer dados bancários de fornecedores.",
        RefusalReason.CREDENCIAIS_VAZADAS: "Não posso fornecer credenciais de acesso.",
        RefusalReason.JUIZO_LEGALIDADE: (
            "Não posso emitir juízo sobre a legalidade de um contrato específico."
        ),
        RefusalReason.FORA_DE_ESCOPO: (
            "Essa pergunta está fora do escopo de contratações públicas do RJ."
        ),
    }
    if motivo_erro:
        log_event("error", "recusa por erro técnico na geração", motivo_erro=motivo_erro)
    return RAGResponse(
        query=query,
        answer=respostas_padrao.get(motivo, respostas_padrao[RefusalReason.SEM_EVIDENCIA]),
        bases_consultadas=bases_consultadas,
        sources_used=[],
        confidence_level=ConfidenceLevel.RECUSADO,
        is_refusal=True,
        refusal_reason=motivo,
    )


def _parsear_resposta_llm(
    bruto: str,
    query: str,
    chunks: list[Chunk],
    bases_consultadas: list[Natureza],
) -> RAGResponse:
    chunks_por_id = {c.metadata.chunk_id: c for c in chunks}
    try:
        dados = _extrair_json(bruto)
        answer = str(dados["answer"]).strip()
        is_refusal = bool(dados.get("is_refusal", False))

        confidence_bruta = str(dados.get("confidence_level", "baixa")).lower()
        try:
            confidence_level = ConfidenceLevel(confidence_bruta)
        except ValueError:
            confidence_level = ConfidenceLevel.BAIXA

        
        sources_used: list[SourceRef] = []
        for chunk_id in dados.get("sources_used") or []:
            chunk = chunks_por_id.get(chunk_id)
            if chunk is None:
                continue
            sources_used.append(
                SourceRef(
                    chunk_id=chunk.metadata.chunk_id,
                    base_id=chunk.metadata.base_id,
                    source_file=chunk.metadata.source_file,
                )
            )

        refusal_reason: Optional[RefusalReason] = None
        if is_refusal:
            try:
                refusal_reason = RefusalReason(dados.get("refusal_reason"))
            except ValueError:
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            sources_used = []
            confidence_level = ConfidenceLevel.RECUSADO
        elif confidence_level == ConfidenceLevel.RECUSADO:
            confidence_level = ConfidenceLevel.BAIXA

        return RAGResponse(
            query=query,
            answer=answer,
            bases_consultadas=bases_consultadas,
            sources_used=sources_used,
            confidence_level=confidence_level,
            is_refusal=is_refusal,
            refusal_reason=refusal_reason,
        )
    except Exception as exc:
        log_event(
            "warning",
            "saída do LLM inválida — aplicando recusa segura",
            erro=str(exc),
            bruto=bruto[:500],
        )
        return _recusa(query, bases_consultadas, RefusalReason.SEM_EVIDENCIA)


def _logar_evento(run_id: str, query_id: str, status: str, inicio: float, resposta: RAGResponse) -> None:
    log_event(
        "info",
        "geracao concluída",
        run_id=run_id,
        query_id=query_id,
        stage="geracao",
        status=status,
        latencia_ms=round((time.monotonic() - inicio) * 1000, 1),
        confidence_level=resposta.confidence_level.value,
        is_refusal=resposta.is_refusal,
        bases_consultadas=[b.value for b in resposta.bases_consultadas],
        num_sources=len(resposta.sources_used),
    )


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

def gerar_resposta(
    query: str,
    chunks: list[Chunk],
    bases_consultadas: list[Natureza],
    *,
    cliente: Optional[ClienteLLM] = None,
    run_id: str = "sem-run-id",
    query_id: Optional[str] = None,
) -> RAGResponse:
    """
    Estágio "geracao": recebe a pergunta e os chunks já recuperados/rerankeados,
    devolve sempre uma RAGResponse válida (nunca uma exception do LLM/parsing).
    """
    query_id = query_id or str(uuid.uuid4())
    inicio = time.monotonic()

    motivo_previo = _checar_guardrail_previo(query)
    if motivo_previo is not None:
        resposta = _recusa(query, bases_consultadas, motivo_previo)
        _logar_evento(run_id, query_id, "bloqueado_guardrail_previo", inicio, resposta)
        return resposta

    if not chunks:
        resposta = _recusa(query, bases_consultadas, RefusalReason.SEM_EVIDENCIA)
        _logar_evento(run_id, query_id, "sem_chunks", inicio, resposta)
        return resposta

    mensagens = _montar_prompt(query, chunks)
    cliente = cliente or _cliente_padrao()

    try:
        bruto = cliente(mensagens)
    except Exception as exc:
        resposta = _recusa(query, bases_consultadas, RefusalReason.SEM_EVIDENCIA, motivo_erro=str(exc))
        _logar_evento(run_id, query_id, "erro_llm", inicio, resposta)
        return resposta

    resposta = _parsear_resposta_llm(bruto, query, chunks, bases_consultadas)
    _logar_evento(run_id, query_id, "ok", inicio, resposta)
    return resposta