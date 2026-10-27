"""Norma citada na pergunta (lei, decreto) e a ordem dos trechos dela no resultado.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from src.retrieval.filters import _sem_caixa_e_acento
from src.retrieval.lexical import _tokens
from src.retrieval.records import RetrievalResult


_NORMA_RE = re.compile(
    r"\b(lei(?: complementar)?|decreto|portaria|resolucao)\s*"
    r"(?:(?:federal|estadual|municipal)\s+)?(?:n(?:o|umero)?[.°º]?\s*)?(\d[\d.]*)"
)


def _normas_mencionadas(texto: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2).replace(".", ""))
            for m in _NORMA_RE.finditer(_sem_caixa_e_acento(texto))}


def _norma_do_documento(metadata: Mapping[str, Any]) -> set[tuple[str, str]]:
    # Só identidade declarada, nunca uma menção incidental no corpo de um
    # decreto, edital ou catálogo de links para legislação.
    extras = metadata.get("extras") or {}
    return _normas_mencionadas(str(extras.get("numero_norma") or extras.get("titulo_documento") or ""))


_VISAO_GERAL_NORMA = "âmbito aplicação princípios objetivos normas gerais"


_ARTIGO_INICIAL = re.compile(r"^\s*art\.?\s*(\d+)(?!\d|\.\d)", re.I)


def _ordenar_candidatos_norma(query: str, candidatos: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    topico = _consulta_topica_norma(query)
    visao_geral = topico == _VISAO_GERAL_NORMA
    pede_fases = bool(re.search(r"\b(?:fases|etapas)\b", topico))
    artigo_pedido = re.search(r"\bart(?:igo)?\.?\s*(\d+)\b", _sem_caixa_e_acento(query))

    def prioridade(item: RetrievalResult):
        texto = _sem_caixa_e_acento(item.content)
        artigo = _ARTIGO_INICIAL.match(texto)
        numero = int(artigo.group(1)) if artigo else None
        if artigo_pedido:
            return (numero != int(artigo_pedido.group(1)),)
        if visao_geral:
            # Visão geral: disposições iniciais antes de alterações de outras
            # leis no fim do texto; a pontuação original desempata.
            return (numero is None, numero if numero is not None else 100000)
        definicao_fases = pede_fases and bool(re.search(r"\bseguintes (?:fases|etapas)\b", texto))
        return (not definicao_fases, not bool(re.search(r"\bart\.?\s*\d|§", texto)))

    ordenados = sorted(candidatos, key=prioridade)
    if visao_geral:
        diversos, repetidos, vistos = [], [], set()
        for item in ordenados:
            artigo = _ARTIGO_INICIAL.match(_sem_caixa_e_acento(item.content))
            chave = (tuple(sorted(_norma_do_documento(item.metadata))), artigo.group(1)) if artigo else None
            if chave is not None and chave in vistos:
                repetidos.append(item)
            else:
                diversos.append(item)
                vistos.add(chave)
        ordenados = diversos + repetidos
    return ordenados


def _consulta_topica_norma(query: str) -> str:
    texto = _sem_caixa_e_acento(query)
    texto = _NORMA_RE.sub(" ", texto)
    texto = re.sub(r"/\s*\d{4}\b", " ", texto)
    vazias = {"o", "a", "os", "as", "que", "qual", "quais", "estabelece", "diz", "sobre", "segundo",
              "conforme", "de", "do", "da", "dos", "das", "em", "no", "na", "e", "cite", "fontes",
              "corpus", "acervo", "resuma", "resumo", "explique", "ano"}
    termos = [t for t in _tokens(texto) if t not in vazias and not re.fullmatch(r"\d{4}", t)]
    # A identidade já delimitou os documentos: repetir número/ano no BM25
    # favorecia capas, cabeçalhos de impressão e catálogos, não os artigos.
    return " ".join(termos) or _VISAO_GERAL_NORMA


def _priorizar_norma_solicitada(
    query: str, resultados: Sequence[RetrievalResult],
    candidatos_norma: Sequence[RetrievalResult], limite: int,
) -> list[RetrievalResult]:
    pedidas = _normas_mencionadas(query)
    if not pedidas or not candidatos_norma:
        return list(resultados[:limite])
    por_id = {r.chunk_id: r for r in candidatos_norma}
    # Mantém o score do reranker quando ele recebeu o candidato.
    por_id.update({r.chunk_id: r for r in resultados if r.chunk_id in por_id})
    primarios = [por_id[r.chunk_id] for r in resultados if r.chunk_id in por_id]
    vistos = {r.chunk_id for r in primarios}
    primarios += [r for r in candidatos_norma if r.chunk_id not in vistos]
    # Uma fonte de cada norma explicitamente citada antes de repetir a mesma.
    primeiros, demais, cobertas = [], [], set()
    for item in primarios:
        identidades = _norma_do_documento(item.metadata) & pedidas
        if identidades - cobertas:
            primeiros.append(item)
            cobertas.update(identidades)
        else:
            demais.append(item)
    primarios = _ordenar_candidatos_norma(query, primeiros + demais)
    reserva = min(len(primarios), max(1, limite - 1), limite)
    escolhidos = primarios[:reserva]
    ids = {r.chunk_id for r in escolhidos}
    escolhidos += [r for r in resultados if r.chunk_id not in ids]
    return escolhidos[:limite]
