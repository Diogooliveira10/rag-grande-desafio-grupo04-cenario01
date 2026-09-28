from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any, Optional

from src.obs import Stage, default_logger
from src.schema import QueryAnalysisResult, QueryFilters

# --------------------------------------------------------------------------
# Normalização (mesma lógica de router.py — casefold + remoção de acentos)
# --------------------------------------------------------------------------


def _normalize(texto: str) -> str:
    dobrado = texto.casefold()
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", dobrado)
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


# --------------------------------------------------------------------------
# Ano
# --------------------------------------------------------------------------

# Ano depois de "/" é parte de um número de norma ou processo ("Lei
# 14.133/2021", "edital 90005/2025"), não o ano de que a pergunta trata.
_ANO_RE = re.compile(r"(?<![/\d.])\b(19|20)\d{2}\b")
_PERIODO_RE = re.compile(
    r"\b(?:entre|de)\s+(?P<inicio>(?:19|20)\d{2})\s+(?:e|a|ate)\s+(?P<fim>(?:19|20)\d{2})\b"
)


def _extrair_periodo(texto_normalizado: str) -> tuple[Optional[int], Optional[int]]:
    """(ano, ano_final): "entre 2024 e 2025" -> (2024, 2025); "em 2025" -> (2025, None)."""
    periodo = _PERIODO_RE.search(texto_normalizado)
    if periodo:
        inicio, fim = sorted((int(periodo.group("inicio")), int(periodo.group("fim"))))
        return inicio, (fim if fim != inicio else None)
    match = _ANO_RE.search(texto_normalizado)
    return (int(match.group()) if match else None), None


# --------------------------------------------------------------------------
# Valor (aceita "R$ 100.000,00", "100 mil", "1,5 milhão", faixas "entre X e Y")
# --------------------------------------------------------------------------

_NUMERO_RE = re.compile(
    r"(?:r\$\s*)?(?P<numero>\d[\d.,]*)\s*(?P<mult>bilh(?:ao|oes)|milh(?:ao|oes)|mil)?\b"
)
# Palavras que podem vir logo depois de um valor ("500 mil em 2025", "1 milhão
# de reais"). Qualquer outra indica contagem: "mais de 3 contratos".
_PALAVRAS_APOS_VALOR = frozenset(
    {
        "a", "o", "e", "ou", "em", "no", "na", "nos", "nas", "de", "do", "da",
        "dos", "das", "para", "por", "com", "sem", "que", "ate", "entre",
        "reais", "real",
    }
)

_GATILHO_FAIXA_RE = re.compile(r"\bentre\b\s+(?P<min>.+?)\s+\be\b\s+(?P<max>.+)")
# O número precisa vir logo depois do gatilho: "maior que 100 mil" é valor,
# "maior valor total ... 2025" e "menor preço" não são.
_GATILHO_MINIMO_RE = re.compile(
    r"\b(?:acima de|maior(?: ou igual a| que)?|a partir de|no minimo|minimo de|mais de)\b"
    r"\s+(?P<valor>(?:r\$\s*)?\d.*)"
)
_GATILHO_MAXIMO_RE = re.compile(
    r"\b(?:abaixo de|menor(?: ou igual a| que)?|no maximo|maximo de|ate)\b"
    r"\s+(?P<valor>(?:r\$\s*)?\d.*)"
)


def _parse_numero(bruto: str, tem_multiplicador: bool) -> Optional[float]:
    texto = bruto.strip()
    if not texto:
        return None
    if "," in texto:
        # Formato BR com decimal explícito: ponto = milhar, vírgula = decimal.
        texto = texto.replace(".", "").replace(",", ".")
    elif not tem_multiplicador and re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        # "100.000" / "1.200.000" sem vírgula: pontos são separador de milhar.
        texto = texto.replace(".", "")
    # Sem esses dois casos (ex.: "1.5" antes de "milhão"), o ponto já é o
    # separador decimal que float() espera — não mexe.
    try:
        return float(texto)
    except ValueError:
        return None


def _extrair_valor(trecho: str) -> Optional[float]:
    match = _NUMERO_RE.search(trecho)
    if not match:
        return None
    mult = match.group("mult")
    numero = match.group("numero")
    resto = trecho[match.end():]
    tem_moeda = match.group().startswith("r$") or bool(re.match(r"\s*reais\b", resto))
    if not mult and not tem_moeda and _ANO_RE.fullmatch(numero):
        return None  # "2025" sozinho é ano, não valor
    seguinte = re.match(r"\s*([a-z]+)", resto)
    if not tem_moeda and seguinte and seguinte.group(1) not in _PALAVRAS_APOS_VALOR:
        return None  # "mais de 3 contratos" é contagem, não valor
    valor = _parse_numero(numero, tem_multiplicador=bool(mult))
    if valor is None:
        return None
    if mult == "mil":
        valor *= 1_000
    elif mult in ("milhao", "milhoes"):
        valor *= 1_000_000
    elif mult in ("bilhao", "bilhoes"):
        valor *= 1_000_000_000
    return valor


def _extrair_faixa_valor(texto_normalizado: str) -> tuple[Optional[float], Optional[float]]:
    faixa = _GATILHO_FAIXA_RE.search(texto_normalizado)
    if faixa:
        minimo = _extrair_valor(faixa.group("min"))
        maximo = _extrair_valor(faixa.group("max"))
        if minimo is not None or maximo is not None:
            return minimo, maximo

    minimo_match = _GATILHO_MINIMO_RE.search(texto_normalizado)
    minimo = _extrair_valor(minimo_match.group("valor")) if minimo_match else None

    maximo_match = _GATILHO_MAXIMO_RE.search(texto_normalizado)
    maximo = _extrair_valor(maximo_match.group("valor")) if maximo_match else None

    return minimo, maximo


# --------------------------------------------------------------------------
# Órgão — heurística de token, sem lista canônica (ainda não existe uma)
# --------------------------------------------------------------------------

_GATILHOS_ORGAO = frozenset(
    {
        "secretaria", "prefeitura", "ministerio", "tribunal", "instituto",
        "fundacao", "autarquia", "universidade", "departamento", "camara",
        "assembleia", "orgao", "agencia",
    }
)
_PALAVRA_CONECTORA = frozenset({"de", "do", "da", "dos", "das", "e", "em", "no", "na"})
_PONTUACAO = ".,;:!?"


def _extrair_orgao(query_original: str) -> Optional[str]:
    tokens = query_original.split()
    for indice, bruto in enumerate(tokens):
        if _normalize(bruto.strip(_PONTUACAO)) not in _GATILHOS_ORGAO:
            continue
        palavras = [bruto.strip(_PONTUACAO)]
        cursor = indice + 1
        while cursor < len(tokens):
            bruta = tokens[cursor]
            limpa = bruta.strip(_PONTUACAO)
            if not limpa:
                break
            eh_conectora = _normalize(limpa) in _PALAVRA_CONECTORA
            if limpa[0].isupper() or eh_conectora:
                palavras.append(limpa)
                cursor += 1
                if bruta and bruta[-1] in _PONTUACAO:
                    break
                continue
            break
        # Remove conectoras penduradas no fim (ex.: "Secretaria de" sem continuação).
        while len(palavras) > 1 and _normalize(palavras[-1]) in _PALAVRA_CONECTORA:
            palavras.pop()
        if len(palavras) > 1:
            return " ".join(palavras)
    return None


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------


def _analisar(query: str) -> QueryFilters:
    if not isinstance(query, str) or not query.strip():
        return QueryFilters()
    texto = _normalize(query)
    ano, ano_final = _extrair_periodo(texto)
    valor_min, valor_max = _extrair_faixa_valor(texto)
    orgao = _extrair_orgao(query)
    return QueryFilters(
        ano=ano, ano_final=ano_final, valor_min=valor_min, valor_max=valor_max, orgao=orgao
    )


def analisar_query(
    query: str,
    *,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> QueryAnalysisResult:
    """Etapa query_analysis: extrai ano/valor/órgão mencionados na pergunta.

    Não filtra nada sozinha — devolve QueryAnalysisResult para quem orquestra
    (pipeline.py) decidir o que fazer com os filtros (ver `filtros_para_retrieve`).
    """
    with default_logger.span(
        run_id=run_id, query_id=query_id, stage=Stage.QUERY_ANALYSIS, input=query
    ) as span:
        filtros = _analisar(query)
        span.set_result(filtros.model_dump(exclude_none=True))
        return QueryAnalysisResult(query_original=query, filtros=filtros)


def filtros_para_retrieve(filtros: QueryFilters) -> dict[str, Any]:
    """Converte QueryFilters no dict de filtros que src.retrieve._matches_filters lê.

    "orgao" e "valor" vivem em ChunkMetadata.extras; "data_vigencia" é campo
    de primeira classe. Faixas (valor entre X e Y; ano => intervalo de datas)
    usam o formato de lista de condições — depende da extensão de
    _matches_filters que aceita múltiplas condições AND por chave (ver
    retrieve.py). Sem essa extensão, apenas o limite inferior seria aplicado.
    """
    resultado: dict[str, Any] = {}
    if filtros.orgao:
        resultado["orgao"] = {"operator": "contains", "value": filtros.orgao}
    if filtros.valor_min is not None and filtros.valor_max is not None:
        resultado["valor"] = [
            {"operator": ">=", "value": filtros.valor_min},
            {"operator": "<=", "value": filtros.valor_max},
        ]
    elif filtros.valor_min is not None:
        resultado["valor"] = {"operator": ">=", "value": filtros.valor_min}
    elif filtros.valor_max is not None:
        resultado["valor"] = {"operator": "<=", "value": filtros.valor_max}
    if filtros.ano is not None:
        ultimo_ano = filtros.ano_final or filtros.ano
        resultado["data_vigencia"] = [
            {"operator": ">=", "value": date(filtros.ano, 1, 1)},
            {"operator": "<", "value": date(ultimo_ano + 1, 1, 1)},
        ]
    # Filtros vindos da pergunta são opcionais: chunk sem o campo continua na
    # busca (ex.: indicador do PCA sem data_vigencia); só sai quem tem o campo
    # com valor fora do filtro.
    return {chave: _marcar_opcional(condicao) for chave, condicao in resultado.items()}


def _marcar_opcional(condicao: Any) -> Any:
    if isinstance(condicao, list):
        return [{**item, "optional": True} for item in condicao]
    return {**condicao, "optional": True}


__all__ = ["analisar_query", "filtros_para_retrieve"]