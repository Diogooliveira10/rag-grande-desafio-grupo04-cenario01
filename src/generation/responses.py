"""Peças comuns das respostas: JSON da saída do LLM, recusa padronizada e fonte de um trecho.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import json
import re

from src.schema import Chunk, ConfidenceLevel, Natureza, RAGResponse, RefusalReason, SourceRef


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
        RefusalReason.ERRO_PROVEDOR: (
            "Não consegui gerar a resposta agora por uma falha no serviço de geração. "
            "Tente novamente em instantes."
        ),
    }
    return RAGResponse(
        query=query,
        answer=respostas_padrao.get(motivo, respostas_padrao[RefusalReason.SEM_EVIDENCIA]),
        bases_consultadas=bases_consultadas,
        sources_used=[],
        confidence_level=ConfidenceLevel.RECUSADO,
        is_refusal=True,
        refusal_reason=motivo,
    )


def _fonte_do_chunk(chunk: Chunk) -> SourceRef:
    """Fonte citável de um chunk; a origem só vai quando é um link público."""
    origem = chunk.metadata.origem
    return SourceRef(
        chunk_id=chunk.metadata.chunk_id,
        base_id=chunk.metadata.base_id,
        source_file=chunk.metadata.source_file,
        origem=origem if (origem or "").startswith(("https://", "http://")) else None,
    )
