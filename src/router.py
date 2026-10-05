"""Roteamento por intenções analisadas, com regras existentes como fallback."""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from src.obs import Stage, default_logger
from src.schema import Natureza, RefusalReason, RouteDecision


# Cada grupo de recusa tem justificativa própria, com a pista que
# ``inferir_motivo_recusa`` usa para devolver o RefusalReason certo.
_REFUSAL_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        (
            r"\bdados? bancarios?\b",
            r"\bconta(s)? (bancaria|corrente)s?\b",
            r"\bagencia e conta\b",
            r"\bchave pix\b",
        ),
        "A pergunta pede dados bancarios de fornecedor; nenhuma base deve ser consultada.",
    ),
    (
        (
            r"\bcredenciais?\b",
            r"\bsenhas?\b",
        ),
        "A pergunta pede credenciais de acesso; nenhuma base deve ser consultada.",
    ),
    (
        (
            r"\bfraud\w*\b",
            r"\bilegalidade\b",
            r"\bilegal(mente)?\b",
            r"\bilicit[oa]s?\b",
        ),
        "A pergunta pede juizo de legalidade; nenhuma base deve ser consultada para concluir isso.",
    ),
)

_OUT_OF_SCOPE_PATTERNS = (
    r"\bproximo mes\b",
    r"\bvao vencer\b",
    r"\bprovavelmente vencerao\b",
    r"\bprever\b",
    r"\bprevisao\b",
)

_NORMATIVE_PATTERNS = (
    r"\bedita(?:l|is)\b",
    r"\bespecificac(?:ao|oes)\s+tecnic(?:a|as)\b",
    r"\blei\b",
    r"\bnorma[s]?\b",
    r"\bregra[s]?\b",
    r"\bdecreto[s]?\b",
    r"\bprotocolo[s]?\b",
    r"\b(qual|quais) .*\bse aplica(m)?\b",
    r"\b(posso|pode|podem) participar\b",
    r"\btratamento diferenciado\b",
    r"\bvigente[s]?\b",
    # Edital e especificação técnica são documentos da base normativa.
    r"\bedita(l|is)\b",
    r"\bespecificac(ao|oes) tecnicas?\b",
    r"\btermos? de referencia\b",
)

# O domínio em geral ("as contratações públicas") não pede registro de
# contratação: sozinho, não leva à base estruturada.
_DOMINIO_GENERICO = re.compile(r"\b(contratacoes|compras|licitacoes) publicas\b")

_STRUCTURED_PATTERNS = (
    r"\bcontrato[s]?\b",
    r"\bcontrata(c|ç)(ao|ões|oes)\b",
    r"\bcontratad[oa]s?\b",
    # "Quanto o órgão X contratou/comprou em 2025?" pede registro de contratação.
    r"\bcontrat(ou|aram)\b",
    r"\bcompr(ou|aram)\b",
    r"\bcompras?\b",
    # Modalidade no plural conta registros ("quantos pregões em 2025?"); no
    # singular costuma ser pergunta de regra ("posso participar de pregão?").
    r"\b(pregoes|dispensas|inexigibilidades|concorrencias)\b",
    r"\bfornecedor(es)?\b",
    r"\borgaos?\b",
    r"\bcnpj\b",
    r"\bmodalidade[s]?\b",
    r"\blicita(c|ç)(ao|ões|oes)\b",
    r"\bprocesso[s]?\b",
    r"\bpublicad[ao]s?\b",
    r"\babert[ao]s?\b",
)

_CONVERSATIONAL_PATTERNS = (
    r"\bata[s]?\b",
    r"\be-?mails?\b",
    r"\barp\b",
    r"\bconversas?\b",
    r"\b(?:chamados?|ouvidoria|parecer(?:es)?|transcri(?:cao|coes))\b",
    r"\b(registro|registros)\b.*\b(menciona|mencionam)\b",
    r"\batraso[s]?\b",
    r"\bdescumprimento\b",
    r"\bproblema[s]? de fornecimento\b",
    r"\bocorrencia[s]?\b",
)

_AGGREGATED_PATTERNS = (
    r"\bmedia\b",
    r"\bmediana\b",
    r"\bmedia[s]?\b",
    r"\btotal\b",
    r"\bmedio[s]?\b",
    r"\bmaiores?\b.*\b(?:orgaos|municipios|fornecedores)\b",
    r"\bfrequencia\b",
    r"\bconcentra(c|ç)(ao|oes|ões)\b",
    r"\baumento\b",
    r"\bcompar(e|ar)\b.*\bvalor(es)?\b",
    # Plano de Contratações Anual (base pca_agregado)
    r"\bpca\b",
    r"\bplano (anual )?de contrata",
    r"\bplanej\w*",
    r"\bquanto\b.*\bgast\w*",
)


def _contains_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


# Palavras da `justificativa` (abaixo) usadas para inferir o RefusalReason
# equivalente quando bases_selecionadas vem vazia — rotear_pergunta só devolve
# texto livre, não o enum, então quem consome a recusa (eval/adapters.py,
# src/pipeline.py) infere o motivo estruturado a partir daqui.
_PISTAS_MOTIVO_RECUSA: tuple[tuple[str, RefusalReason], ...] = (
    ("dado bancario", RefusalReason.DADOS_BANCARIOS),
    ("dados bancarios", RefusalReason.DADOS_BANCARIOS),
    ("credencia", RefusalReason.CREDENCIAIS_VAZADAS),
    ("juizo", RefusalReason.JUIZO_LEGALIDADE),
    ("legalidade", RefusalReason.JUIZO_LEGALIDADE),
    ("fora do corpus", RefusalReason.FORA_DE_ESCOPO),
    ("nao foi identificado", RefusalReason.SEM_EVIDENCIA),
)


def inferir_motivo_recusa(justificativa: str) -> Optional[RefusalReason]:
    """Mapeia a `justificativa` de uma RouteDecision sem bases para um RefusalReason.

    Devolve ``None`` quando a justificativa não bate com nenhuma pista
    conhecida — quem chama decide o padrão (normalmente ``SEM_EVIDENCIA``).
    """
    texto = justificativa.lower()
    for pista, motivo in _PISTAS_MOTIVO_RECUSA:
        if pista in texto:
            return motivo
    return None


def _normalize(query: str) -> str:
    folded = query.casefold()
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", folded)
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.split())


def rotear_pergunta(
    query: str,
    *,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
    analise=None,
) -> RouteDecision:
    """Seleciona as naturezas de evidencia necessarias para ``query``.

    O roteador nao consulta os indices nem chama o LLM. A recuperacao usa as
    naturezas retornadas para localizar os respectivos ``base_id``s.
    Registra o stage ``roteamento`` em ``logs/pipeline-AAAA-MM-DD.jsonl``.
    """
    with default_logger.span(
        run_id=run_id, query_id=query_id, stage=Stage.ROTEAMENTO, input=query
    ) as span:
        decisao = _rotear(query)
        # Mantém as recusas originais antes de considerar o plano do LLM.
        if analise is not None and analise.intencoes and not inferir_motivo_recusa(decisao.justificativa) in {
            RefusalReason.DADOS_BANCARIOS, RefusalReason.CREDENCIAIS_VAZADAS,
            RefusalReason.JUIZO_LEGALIDADE, RefusalReason.FORA_DE_ESCOPO,
        }:
            bases = list(dict.fromkeys(base for intent in analise.intencoes for base in intent.bases))
            decisao = RouteDecision(bases_selecionadas=bases,
                justificativa="Necessidades de evidência identificadas na análise compartilhada da pergunta.",
                confidence=0.8)
        span.set_result(
            {
                "bases": [base.value for base in decisao.bases_selecionadas],
                "justificativa": decisao.justificativa,
            }
        )
        span.extra["confidence"] = decisao.confidence
        return decisao


def _rotear(query: str) -> RouteDecision:
    if not isinstance(query, str) or not query.strip():
        return RouteDecision(
            bases_selecionadas=[],
            justificativa="A pergunta esta vazia ou nao e valida.",
            confidence=0.0,
        )

    text = _normalize(query)

    for patterns, justificativa in _REFUSAL_RULES:
        if _contains_pattern(text, patterns):
            return RouteDecision(
                bases_selecionadas=[],
                justificativa=justificativa,
                confidence=0.98,
            )

    if _contains_pattern(text, _OUT_OF_SCOPE_PATTERNS):
        return RouteDecision(
            bases_selecionadas=[],
            justificativa="A pergunta pede previsao ou informacao fora do corpus congelado.",
            confidence=0.95,
        )

    bases: list[Natureza] = []
    if _contains_pattern(text, _NORMATIVE_PATTERNS):
        bases.append(Natureza.NORMATIVA)
    if _contains_pattern(_DOMINIO_GENERICO.sub(" ", text), _STRUCTURED_PATTERNS):
        bases.append(Natureza.ESTRUTURADA)
    if _contains_pattern(text, _CONVERSATIONAL_PATTERNS):
        bases.append(Natureza.CONVERSACIONAL)
    if _contains_pattern(text, _AGGREGATED_PATTERNS):
        bases.append(Natureza.AGREGADA)

    # Procedimento de pesquisa de preços pede normas, não uma lista de contratos.
    if (Natureza.NORMATIVA in bases and re.search(r"\bpesquisa de precos\b", text)
            and not re.search(r"\b(?:publicad|assinad|registrad|fornecedor|cnpj|medio|media|mediana|total|compar)\w*\b", text)):
        bases = [base for base in bases if base != Natureza.ESTRUTURADA]

    if not bases:
        return RouteDecision(
            bases_selecionadas=[],
            justificativa="Nao foi identificado um tipo de evidencia do corpus.",
            confidence=0.3,
        )

    nomes = ", ".join(base.value for base in bases)
    return RouteDecision(
        bases_selecionadas=bases,
        justificativa=f"A pergunta requer evidencia das bases: {nomes}.",
        confidence=0.8 if len(bases) == 1 else 0.85,
    )


__all__ = ["rotear_pergunta", "inferir_motivo_recusa"]
