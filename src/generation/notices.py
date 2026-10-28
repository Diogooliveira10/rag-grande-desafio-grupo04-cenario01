"""Avisos e confiança da resposta: recorte da evidência, ressalva de insuficiência e fonte sintética.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations


from src.domain import confidence
from src.domain.text import sem_acento
from src.schema import Chunk, ConfidenceLevel, RAGResponse


# Política de confiança: regra de negócio, em src/domain/confidence.py. Os
# nomes antigos continuam valendo para quem os importava daqui.
_confianca_do_recorte = confidence.confianca_do_recorte


_confianca_dos_recortes = confidence.confianca_dos_recortes


_recorte_dos_chunks = confidence.recorte_dos_chunks


_limitar_confianca_pela_ressalva = confidence.limitar_confianca_pela_ressalva


_aplicar_recorte = confidence.aplicar_recorte


_NIVEIS = confidence.NIVEIS


_RESSALVA_DE_INSUFICIENCIA = confidence.RESSALVA_DE_INSUFICIENCIA


_AVISO_RESSALVA = confidence.AVISO_RESSALVA


_sem_acento = sem_acento


_AVISO_FONTE_SINTETICA = ("Fonte sintética: a resposta usa conversa fictícia criada para testes, "
                          "que não comprova fatos reais.")


def _avisar_fonte_sintetica(resposta: RAGResponse, chunks: list[Chunk]) -> RAGResponse:
    """Aviso fixo quando uma conversa sintética é citada: o prompt pede que o
    LLM diga que o cenário é fictício, mas ele nem sempre diz."""
    sinteticos = {chunk.metadata.chunk_id for chunk in chunks if chunk.metadata.extras.get("sintetico")}
    if not any(fonte.chunk_id in sinteticos for fonte in resposta.sources_used):
        return resposta
    return resposta.model_copy(update={
        "avisos": list(dict.fromkeys([*resposta.avisos, _AVISO_FONTE_SINTETICA])),
    })


def _avisar_limites_da_evidencia(resposta: RAGResponse, chunks: list[Chunk]) -> RAGResponse:
    """Limites observáveis, sem fingir que fontes válidas provam a afirmação."""
    if resposta.is_refusal:
        return resposta
    avisos = list(resposta.avisos)
    nivel = resposta.confidence_level
    if any(c.metadata.extras.get("recuperacao_degradada") for c in chunks):
        avisos.append("Busca semântica indisponível: resposta recuperada por BM25; pode haver evidências relevantes não encontradas.")
        if nivel == ConfidenceLevel.ALTA:
            nivel = ConfidenceLevel.MEDIA
    if not resposta.sources_used:
        avisos.append("A resposta não contém fontes verificáveis; não trate suas afirmações como fatos comprovados do acervo.")
        if nivel == ConfidenceLevel.ALTA:
            nivel = ConfidenceLevel.BAIXA
    citados = {f.chunk_id for f in resposta.sources_used}
    grupos: dict[int, list[Chunk]] = {}
    for chunk in chunks:
        if chunk.metadata.extras.get("total_intencoes", 0) > 1:
            grupos.setdefault(chunk.metadata.extras.get("intencao", 0), []).append(chunk)
    total_intencoes = max((c.metadata.extras.get("total_intencoes", 0) for c in chunks), default=0)
    for numero in range(total_intencoes if total_intencoes > 1 else 0):
        if numero not in grupos:
            avisos.append(f"Nenhuma evidência recuperada para a parte {numero + 1} de {total_intencoes} da pergunta.")
            if nivel == ConfidenceLevel.ALTA:
                nivel = ConfidenceLevel.MEDIA
    for grupo in grupos.values():
        if not any(c.metadata.chunk_id in citados for c in grupo):
            pergunta = grupo[0].metadata.extras.get("subpergunta", "parte da pergunta")
            avisos.append(f"Parte sem fonte citada na resposta: {pergunta}")
            if nivel == ConfidenceLevel.ALTA:
                nivel = ConfidenceLevel.MEDIA
    return resposta.model_copy(update={"confidence_level": nivel, "avisos": list(dict.fromkeys(avisos))})
