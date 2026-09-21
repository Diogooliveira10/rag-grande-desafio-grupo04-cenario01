from __future__ import annotations

import json

import pytest

from src import generate as gen
from src.schema import Chunk, ChunkMetadata, ConfidenceLevel, Natureza, RefusalReason


def _chunk(chunk_id="c1", base_id="contratos_estruturado", conteudo="texto do chunk"):
    return Chunk(
        content=conteudo,
        metadata=ChunkMetadata(
            source_file="arquivo.json",
            chunk_id=chunk_id,
            base_id=base_id,
            doc_type="json",
            natureza=Natureza.ESTRUTURADA,
        ),
    )


# --------------------------------------------------------------------------
# Guardrails determinísticos (não chamam o LLM)
# --------------------------------------------------------------------------

def test_guardrail_previo_bloqueia_pedido_de_dados_bancarios_sem_chamar_llm():
    chamou = {"vezes": 0}

    def cliente_espiao(mensagens):
        chamou["vezes"] += 1
        return "{}"

    resposta = gen.gerar_resposta(
        "Qual o número da conta bancária do fornecedor X?",
        [_chunk()],
        [Natureza.ESTRUTURADA],
        cliente=cliente_espiao,
    )

    assert chamou["vezes"] == 0
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.DADOS_BANCARIOS
    assert resposta.sources_used == []
    assert resposta.confidence_level == ConfidenceLevel.RECUSADO


def test_sem_chunks_recusa_sem_chamar_llm():
    chamou = {"vezes": 0}

    def cliente_espiao(mensagens):
        chamou["vezes"] += 1
        return "{}"

    resposta = gen.gerar_resposta("Qual o valor médio dos contratos?", [], [], cliente=cliente_espiao)

    assert chamou["vezes"] == 0
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


# --------------------------------------------------------------------------
# Parsing da resposta do LLM (feliz e defensivo)
# --------------------------------------------------------------------------

def test_resposta_valida_com_fontes_reais():
    chunk = _chunk(chunk_id="c1", base_id="contratos_estruturado")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "O valor médio foi R$ 4,20.",
            "is_refusal": False,
            "refusal_reason": None,
            "confidence_level": "alta",
            "sources_used": ["c1"],
        })

    resposta = gen.gerar_resposta(
        "Qual o valor médio?", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert resposta.is_refusal is False
    assert resposta.confidence_level == ConfidenceLevel.ALTA
    assert len(resposta.sources_used) == 1
    assert resposta.sources_used[0].base_id == "contratos_estruturado"
    assert resposta.sources_used[0].source_file == "arquivo.json"


def test_json_com_cercas_markdown_e_parseado():
    chunk = _chunk()

    def cliente_fake(mensagens):
        payload = json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "media",
            "sources_used": [],
        })
        return f"```json\n{payload}\n```"

    resposta = gen.gerar_resposta("pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.answer == "resposta"
    assert resposta.confidence_level == ConfidenceLevel.MEDIA


def test_chunk_id_inventado_pelo_llm_e_descartado():
    chunk = _chunk(chunk_id="c1")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "baixa",
            "sources_used": ["c1", "chunk-que-nao-existe"],
        })

    resposta = gen.gerar_resposta("pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert [s.chunk_id for s in resposta.sources_used] == ["c1"]


def test_saida_nao_json_cai_em_recusa_segura_sem_excecao():
    def cliente_fake(mensagens):
        return "desculpa, não sei responder isso em JSON."

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


def test_llm_diz_recusa_mas_inclui_fontes_e_corrigido():
    """O LLM errou o contrato (recusa citando fontes) — generate.py tem que corrigir,
    nunca deixar isso estourar a validação de src/schema.py."""
    chunk = _chunk(chunk_id="c1")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "não posso responder",
            "is_refusal": True,
            "refusal_reason": "juizo_legalidade_contrato",
            "confidence_level": "alta",
            "sources_used": ["c1"],
        })

    resposta = gen.gerar_resposta("Isso foi fraude?", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is True
    assert resposta.sources_used == []
    assert resposta.confidence_level == ConfidenceLevel.RECUSADO
    assert resposta.refusal_reason == RefusalReason.JUIZO_LEGALIDADE


def test_confidence_recusado_sem_is_refusal_e_corrigido():
    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta parcial",
            "is_refusal": False,
            "confidence_level": "recusado",
            "sources_used": [],
        })

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is False
    assert resposta.confidence_level == ConfidenceLevel.BAIXA


def test_erro_do_cliente_llm_nao_propaga_excecao():
    def cliente_com_erro(mensagens):
        raise ConnectionError("endpoint fora do ar")

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_com_erro)
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA