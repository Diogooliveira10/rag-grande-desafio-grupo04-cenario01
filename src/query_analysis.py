"""Etapa query_analysis: extrai da pergunta filtros de valor, período e órgão.

Determinística (regex e regras, sem LLM) e **conservadora**: um filtro só é
criado quando a leitura não deixa dúvida. Na dúvida, nenhum filtro: filtro
faltando deixa a resposta mais ampla; filtro errado dá resposta errada, porque
``structured_query`` aplica ano e valor como WHERE estrito.

Regras principais:

- **Valor** só depois de um gatilho de comparação ("acima de", "superior a",
  "maior que", "no máximo"...), com o número logo em seguida e um final limpo
  (fim da frase, pontuação, "em 2025", "e", verbo). Número seguido de
  substantivo, "%", "/", "(" ou "de <palavra>" é contagem, data ou
  percentual. Número pequeno (< 1.000) sem sinal de dinheiro (R$, reais, mil,
  milhão, mi, bi, k, centavos) também não vira valor: "mais de 3 por
  modalidade" é contagem. Gatilho depois de atributo que não é dinheiro
  ("quantidade superior a", "prazo de até") ou de tempo ("ano maior que")
  não gera valor.
- **Ano** só com contexto de ano. Número usado como valor, número de norma ou
  processo ("Lei 14.133/2021", "Lei nº 14.133, de 1º de abril de 2021",
  "pregão 05/2023") e número de contagem ("2000 cestas", "mais de 2000
  itens") não são ano. Dois ou mais anos diferentes viram período do menor ao
  maior ("2024 x 2025", "entre 2024 e 2025", "2023, 2024 e 2025"). "A partir
  de", "desde", "depois de", "até", "antes de" dão períodos abertos, com dia,
  mês, semestre, início ou fim se houver. Período aberto que contradiz o ano
  da pergunta vira nenhum filtro de ano.
"""

from __future__ import annotations

import calendar
import json
import re
import hashlib
import os
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

import config
from src.obs import Stage, default_logger
from src.schema import QueryAnalysisResult, QueryFilters, QueryIntent
from src.analysis.entities import (  # noqa: F401  (reexportados)
    ORGAOS_CORPUS_PATH,
    _APELIDOS_DE_FORNECEDOR,
    _APELIDOS_SO_COM_MAIUSCULA,
    _ARTIGOS_ANTES_DE_NOME,
    _CNPJ_RE,
    _COMECOS_DE_NOME_COMPOSTO,
    _CONTINUA_SEM_CATALOGO,
    _Catalogo,
    _ESCOPO_ESTADUAL,
    _FIM_DO_NOME_DO_ORGAO,
    _GATILHOS_DE_FORNECEDOR,
    _GATILHOS_FORTES,
    _GATILHOS_ORGAO,
    _GATILHO_EXIGE,
    _GENERICAS_DE_FORNECEDOR,
    _INTRODUTORES_DE_NOME,
    _LeitorDeOrgao,
    _MARCADOR_DE_CIDADE,
    _MUNICIPIOS_RJ,
    _NAO_E_ORGAO,
    _NAO_SAO_NOME,
    _NOME_DUPLICADO_ESTADO_CIDADE,
    _PALAVRAS_FUNCIONAIS,
    _PALAVRA_CONECTORA,
    _PARCELA_MINIMA_DO_PROPRIO_FORNECEDOR,
    _PONTUACAO,
    _QUALIFICADORES_DE_ORGAO,
    _SIGLAS_DE_ORGAO,
    _SIGLA_NO_NOME_RE,
    _SINONIMOS_DE_ORGAO,
    _TOKEN_DE_NOME_RE,
    _VAZIAS_DE_NOME,
    _VERBOS_COMUNS_DEPOIS_DO_NOME,
    _catalogo_de_orgaos,
    _em_escopo_estadual,
    _estender_para_tras,
    _extrair_cnpjs,
    _extrair_fornecedor,
    _extrair_municipio,
    _extrair_orgao,
    _municipio_em,
    _nome_a_partir_do_gatilho,
    _nome_curto_de_fornecedor,
    _nome_sem_catalogo,
    _normalize,
    _orgao_do_catalogo,
    _orgao_do_municipio,
    _palavras_dos_objetos,
    _sem_marcador_de_cidade,
    _sem_pista_de_maiuscula,
    _sigla_de_orgao,
    _token_casa,
    _tokens_de_nome,
    _valor_do_catalogo,
    gerar_catalogo_de_orgaos,
)
# Leitura de valores e períodos: regras puras, em src/domain/values_and_periods.py.
from src.domain.values_and_periods import (  # noqa: F401  (reexportados)
    Span,
    _ABERTO_RE,
    _ANO,
    _ANO_SOLTO_RE,
    _ANTES,
    _ATE,
    _ATRIBUTO_DE_TEMPO_ANTES_RE,
    _ATRIBUTO_NAO_MONETARIO_ANTES_RE,
    _Aberto,
    _COMPARACAO_DE_ANO_RE,
    _COMPLEMENTO_RE,
    _CONTAGEM_ADIANTE_RE,
    _CONTEXTO_DE_VALOR_RE,
    _CONTEXTO_FORTE_DE_ANO_RE,
    _CONTEXTO_MUITO_FORTE_DE_ANO_RE,
    _DATA,
    _DATA_SOLTA_RE,
    _DEPOIS,
    _DESDE,
    _DE_DE_TEMPO_RE,
    _DINHEIRO_RE,
    _EM_DIANTE_RE,
    _EXPRESSAO_DE_TEMPO_ANTES_RE,
    _EXPRESSAO_DE_TEMPO_DEPOIS_RE,
    _FAIXA_AMBIGUA_RE,
    _FAIXA_DE_A_RE,
    _FAIXA_ENTRE_RE,
    _GATILHOS_DE_TEMPO,
    _GATILHOS_MAXIMO,
    _GATILHOS_MINIMO,
    _GATILHO_DE_GRANDEZA_ANTES_RE,
    _GATILHO_MAXIMO_RE,
    _GATILHO_MINIMO_RE,
    _GRUPOS_DE_ANO,
    _LADO_DE_PERIODO,
    _MES,
    _MESES,
    _MES_NUM,
    _MOEDA_DEPOIS_RE,
    _MULTIPLICADORES,
    _NAO_SAO_QUANTIDADE,
    _NOME_DE_EMPRESA_ANTES_RE,
    _NOME_DE_EMPRESA_DEPOIS,
    _OU_ANTES_RE,
    _PALAVRAS_NEUTRAS,
    _PALAVRA_DE_DINHEIRO,
    _PAR_DE_ANOS_RE,
    _PERIODO_DE_DOIS_ANOS_RE,
    _PERIODO_RE,
    _Periodo,
    _QUALIFICADOR_DE_VIGENCIA_RE,
    _SUBSTANTIVOS_DE_CONTAGEM,
    _TEM_CARA_DE_ANO_RE,
    _VALOR_ANTES_RE,
    _Valor,
    _Valores,
    _ano_simples_depois_de_gatilho_e_contagem,
    _anos_soltos,
    _contagem_adiante,
    _eh_ano,
    _extrair_periodo,
    _extrair_valores,
    _faixa,
    _final_limpo,
    _ler_dinheiro,
    _limite_por_gatilho,
    _parse_numero,
    _periodo_aberto,
    _sem_percentual_data_ou_letra_colada,
    _sobrepoe,
    _unidade_de_tempo,
)
from src.sensitivity import mascarar

ANALYSIS_MODE = os.getenv("QUERY_ANALYSIS_MODE", "regras")
ANALYSIS_CACHE = config.ROOT_DIR / "data" / "runs" / "query-analysis-cache.jsonl"
PROMPT_VERSION = "intencoes-v7-semantic-entity-roles"
_PROMPT = """Analise necessidades de evidência de uma pergunta sobre contratações públicas do RJ.
Não responda à pergunta e não escreva SQL. Retorne JSON {\"intencoes\":[...]} com até 4 intenções.
Cada intenção: pergunta (subpergunta fiel), bases, filtros, operacao, termos_objeto, limite.
bases permitidas: normativa, estruturada, agregada, conversacional. Escolha evidência necessária,
não todas as bases sugeridas pelas palavras: norma/requisito -> normativa; registro/lista/ranking
de contratos -> estruturada; preço por item/estatística por modalidade -> agregada e estruturada;
relato/ata -> conversacional. Comparação técnica exige também normativa. Pergunta composta exige
intenções separadas, por exemplo regras de participação e oportunidades abertas.
operacao: buscar, ranking, estatistica, agrupamento, evolucao. termos_objeto: palavras do objeto,
sem órgão, ano ou valor. Não invente sinônimos técnicos nem equivalência entre produtos.
filtros opcionais: ano, ano_final, valor_min, valor_max, valor_min_exclusivo, valor_max_exclusivo,
orgao (somente um nome específico, não 'órgãos estaduais'), papel_orgao (orgao,unidade), filtros_analiticos,
metrica (contagem,soma,media,mediana,minimo,maximo), agrupamentos, referencia_temporal,
tipo_valor (contratado,estimado,homologado), tipo_medida (global,unitaria), granularidade (contrato,item,pca), papel_cnpj (orgao,fornecedor), minimo_por_grupo.
Campos analíticos: esfera,municipio,uf,modalidade,fornecedor,orgao_nome,unidade_nome,categoria,
classe,mes_assinatura,mes_publicacao. esfera use estadual,municipal,federal; uf use RJ.
referencia_temporal: assinatura para contrato firmado/assinado ou valor contratado;
publicacao para compra publicada; resultado para homologação; pca para plano;
vigencia para normas; propostas para oportunidades abertas. Ano de número de lei não é filtro.
Use limites numéricos explícitos: acima de é exclusivo, a partir de é inclusivo.
Maior total por fornecedor é agrupamento com soma, não ranking de contratos individuais.
Uma média/mediana por unidade exige tipo_medida=unitaria e granularidade=item; mantenha tipo_valor=homologado quando pedido. Não use valor global como unitário.
Exemplo de formato: {"intencoes":[{"pergunta":"Liste contratos", "bases":["estruturada"],
"filtros":{"ano":2025,"filtros_analiticos":{"uf":"RJ","esfera":"estadual"}},
"operacao":"buscar","termos_objeto":[],"limite":10}]}.
uf, esfera, município e modalidade ficam SOMENTE em filtros.filtros_analiticos.
termos_objeto fica na intenção, FORA de filtros. limite é entre 1 e 50, nunca 100.
Não inclua agrupamento quando a pergunta apenas pede um campo junto de uma lista.
Estar no RJ não implica esfera estadual: não filtre esfera se a pergunta não a especificou.
Toda operação de total/média/mediana/contagem exige operacao analítica, não buscar.
Frequência/mais usada exige agrupamento e contagem, com recorte comprovável.
Referências como 'nessas contratações' sem contexto anterior não identificam um conjunto:
não invente objeto, órgão ou ano para completar a referência.
Em perguntas sobre PCA, Secretaria citada sem papel explícito é a unidade responsável;
se a pergunta disser explicitamente órgão, use o órgão. Não invente nomes de órgão/unidade.
Não filtre esfera ao comparar estadual com municipal. Não transforme contagem mínima de contratos
em valor monetário. Campos ausentes ficam ausentes. Não acrescente campos fora deste contrato."""


def _analysis_key(query: str) -> str:
    inventory = config.ROOT_DIR / "data" / "inventory" / "v6.json"
    version = hashlib.sha256(inventory.read_bytes()).hexdigest() if inventory.exists() else "sem-inventario"
    catalogo = _catalogo_de_orgaos()
    assinatura_catalogo = catalogo.assinatura if catalogo else "sem-catalogo"
    return hashlib.sha256(json.dumps([query, config.GENERATION_MODEL, PROMPT_VERSION, _PROMPT, version,
        assinatura_catalogo,
        date.today().isoformat(), config.OPENROUTER_MODEL_FALLBACK, bool(config.OPENROUTER_API_KEY),
        config.LLM_PROVIDER_MODE, config.OLLAMA_URL, config.OPENROUTER_BASE_URL,
        config.OPENROUTER_MAX_TOKENS], ensure_ascii=False).encode()).hexdigest()


def _gravar_cache(query: str, result: QueryAnalysisResult, llm: dict, fallback: Optional[str] = None) -> None:
    from src.sensitivity import mascarar
    entry = {"key": _analysis_key(query), "analysis": result.model_dump(mode="json"), "llm": llm}
    if fallback is not None:
        entry.update({"fallback": fallback, "expires_at": time.time() + 300})
    masked, _ = mascarar(json.dumps(entry, ensure_ascii=False))
    ANALYSIS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with ANALYSIS_CACHE.open("a", encoding="utf8") as handle:
        handle.write(masked + "\n")


def _analisar_llm(query: str, cliente: Any, span: Any) -> QueryAnalysisResult:
    span.extra["prompt_version"] = PROMPT_VERSION
    # Clientes injetados não consultam cache de produção.
    cacheable = cliente is None
    if cacheable:
        do_cache = _analise_do_cache(query, span)
        if do_cache is not None:
            return do_cache
    data = _chamar_llm_de_analise(query, cliente, span)
    normalized: list[str] = []
    intents = [_intencao_validada(original, query, normalized) for original in data["intencoes"]]
    span.extra["campos_normalizados"] = normalized
    if not 1 <= len(intents) <= 4:
        raise ValueError("Análise deve produzir entre uma e quatro intenções")
    result = QueryAnalysisResult(query_original=query, filtros=intents[0].filtros, intencoes=intents, metodo="llm")
    if cacheable:
        _gravar_cache(query, result, span.extra["llm"])
    return result


def _analise_do_cache(query: str, span: Any) -> Optional[QueryAnalysisResult]:
    """Última análise guardada para a pergunta, se ainda vale; anota o acerto no span."""
    if not ANALYSIS_CACHE.exists():
        return None
    key = _analysis_key(query)
    for line in reversed(ANALYSIS_CACHE.read_text(encoding="utf8").splitlines()):
        try:
            cached = json.loads(line)
            if cached.get("key") == key:
                if cached.get("expires_at", float("inf")) <= time.time():
                    continue
                result = QueryAnalysisResult.model_validate(cached["analysis"])
                span.extra["cache_hit"] = True
                span.extra["llm"] = {**cached.get("llm", {}), "llm_called": False,
                                     "chamadas_modelo": [], "uso_tokens": {"status": "cache"}}
                span.extra["llm_called"] = False
                span.extra["uso_tokens"] = {"status": "cache"}
                span.modelo = cached.get("llm", {}).get("model", config.GENERATION_MODEL)
                if cached.get("fallback"):
                    span.status = "degradado"
                    span.extra["fallback"] = cached["fallback"]
                return result.model_copy(update={"query_original": query})
        except (ValueError, KeyError):
            continue
    return None


def _chamar_llm_de_analise(query: str, cliente: Any, span: Any) -> dict[str, Any]:
    """Chama o LLM com o prompt de análise e devolve o JSON dele (ainda sem validar)."""
    from src.generate import _cliente_padrao, _texto_e_tokens
    span.modelo = config.GENERATION_MODEL
    span.extra["cache_hit"] = False
    chamada = cliente or _cliente_padrao(_schema_analise())
    span.extra["llm_called"] = True
    span.extra["uso_tokens"] = {"status": "indisponivel", "fonte": "response.usage"}
    try:
        raw, usage = _texto_e_tokens(chamada([
            {"role": "system", "content": _PROMPT},
            {"role": "user", "content": f"Data da consulta: {date.today().isoformat()}. Corpus: v6, amostra principalmente de 2025.\nPergunta: {query}"},
        ]), extra=span.extra)
    finally:
        span.extra["llm"] = dict(getattr(chamada, "diagnosticos", {}))
        span.modelo = span.extra["llm"].get("model", config.GENERATION_MODEL)
    span.tokens = usage
    return json.loads(raw)


def _intencao_validada(original: dict[str, Any], query: str, normalized: list[str]) -> QueryIntent:
    """Uma intenção do LLM, normalizada e conferida contra a pergunta.

    O LLM não pode inventar filtro: órgão, modalidade, fornecedor, CNPJ e ano
    precisam estar na pergunta, e conta só quando a pergunta pede conta. Senão,
    ``ValueError`` (a análise cai para as regras). ``normalized`` recebe os
    campos que foram movidos para o lugar certo do contrato.
    """
    row = dict(original)
    filtros = dict(row.get("filtros") or {})
    analiticos = dict(filtros.get("filtros_analiticos") or {})
    for campo in ("uf", "esfera", "municipio", "modalidade", "fornecedor", "orgao_nome", "unidade_nome", "categoria", "classe"):
        if campo in filtros:
            value = filtros.pop(campo)
            if campo in analiticos and analiticos[campo] != value:
                raise ValueError("Filtros contraditórios")
            analiticos[campo] = value
            normalized.append("filtros." + campo)
    if "termos_objeto" in filtros:
        value = filtros.pop("termos_objeto")
        if "termos_objeto" in row and row["termos_objeto"] != value:
            raise ValueError("Termos contraditórios")
        row["termos_objeto"] = value
        normalized.append("filtros.termos_objeto")
    filtros["filtros_analiticos"] = analiticos
    explicit = _analisar(row.get("pergunta", query))
    if explicit.orgao:
        filtros["orgao"] = explicit.orgao
        filtros["papel_orgao"] = explicit.papel_orgao
        normalized.append("orgao_papel_explicito")
    elif filtros.get("orgao"):
        raise ValueError("Filtro de órgão/unidade não explícito na pergunta")
    if ("estruturada" in row.get("bases", []) and filtros.get("referencia_temporal") is None
            and explicit.referencia_temporal is not None):
        filtros["referencia_temporal"] = explicit.referencia_temporal
        normalized.append("referencia_temporal_explicita")
    row["filtros"] = filtros
    intent = QueryIntent.model_validate(row)
    from src.structured_query import requer_calculo
    if intent.operacao != "buscar" and not requer_calculo(query):
        raise ValueError("Operação analítica não solicitada na pergunta")
    pergunta = _normalize(query)
    for campo in ("modalidade", "categoria", "classe", "fornecedor", "orgao_nome", "unidade_nome"):
        valor = intent.filtros.filtros_analiticos.get(campo)
        if valor and _normalize(str(valor)) not in pergunta:
            raise ValueError(f"Filtro {campo} não explícito na pergunta")
    cnpj_modelo = intent.filtros.filtros_analiticos.get("cnpj")
    cnpj_explicito = explicit.filtros_analiticos.get("cnpj")
    if cnpj_modelo and cnpj_modelo != cnpj_explicito:
        raise ValueError("Filtro CNPJ não explícito na pergunta")
    if intent.filtros.papel_cnpj and intent.filtros.papel_cnpj != explicit.papel_cnpj:
        raise ValueError("Papel do CNPJ não explícito na pergunta")
    if intent.filtros.ano is not None and str(intent.filtros.ano) not in query:
        raise ValueError("Ano não explícito na pergunta")
    return intent


def _schema_analise() -> dict:
    intent = QueryIntent.model_json_schema()
    defs = intent.pop("$defs", {})
    groups = ["esfera", "municipio", "uf", "modalidade", "fornecedor", "orgao_nome", "unidade_nome", "categoria", "classe", "mes_assinatura", "mes_publicacao"]
    filter_fields = [*groups, "cnpj"]
    filters = defs["QueryFilters"]["properties"]
    filters["filtros_analiticos"] = {"type": "object", "additionalProperties": False,
        "properties": {field: {"type": "string"} for field in filter_fields}}
    filters["metrica"] = {"anyOf": [{"enum": ["contagem", "soma", "media", "mediana", "minimo", "maximo"]}, {"type": "null"}]}
    filters["referencia_temporal"] = {"anyOf": [{"enum": ["assinatura", "publicacao", "resultado", "pca", "vigencia", "propostas"]}, {"type": "null"}]}
    filters["agrupamentos"] = {"type": "array", "items": {"enum": groups}}
    return {"type": "object", "additionalProperties": False, "required": ["intencoes"],
        "properties": {"intencoes": {"type": "array", "minItems": 1, "maxItems": 4, "items": {"$ref": "#/$defs/QueryIntent"}}},
        "$defs": {**defs, "QueryIntent": intent}}

# --------------------------------------------------------------------------
# Normalização (mesma lógica de router.py — casefold + remoção de acentos)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Citações de norma e números de processo: o ano delas não é o da pergunta
# --------------------------------------------------------------------------

_TIPO_NORMA = (
    r"(?:lei(?:\s+complementar)?|lc|decreto(?:-lei)?|portaria|resolucao|instrucao\s+normativa|in"
    r"|medida\s+provisoria|emenda\s+constitucional|deliberacao)"
)
_NAO_SAO_ORGAO = r"(?!(?:de|do|da|em|no|na|que|e|ou|a|o|para|sobre)\b)"
_NORMA_RE = re.compile(
    rf"\b{_TIPO_NORMA}\b"
    # órgão emissor ou esfera entre o tipo e o número: "IN SEGES/ME nº 65"
    rf"(?:\s+{_NAO_SAO_ORGAO}(?:federal|estadual|municipal|conjunta|[a-z]{{2,12}}(?:/[a-z]{{2,12}})?)){{0,2}}"
    r"\s*,?\s*(?:n[o°.]*|numero)?\s*\d[\d.]*"
    # artigo, inciso, parágrafo: "Lei 14.133, art. 75, inciso II, de 2021"
    r"(?:\s*,?\s*(?:art\.?|artigo|inciso|inc\.?|paragrafo|§)\s*[\w°]+)*"
    rf"(?:/\d{{2,4}}"
    rf"|\s*\(\s*de\s+{_ANO}\s*\)"
    # ", de 1º de abril de 2021", "de abril de 2021": a data é da norma.
    rf"|\s*,?\s*de\s+(?:\d{{1,2}}[o°]?\s+de\s+)?{_MES}\s+de\s+{_ANO}\b"
    # "de 2021" sozinho também, mas não o que abre um período ("de 2021 a 2023")
    rf"|\s*,?\s*de\s+{_ANO}\b(?!\s+(?:a|ate|e)\s+{_ANO})"
    rf"|\s*\({_ANO}\)"
    rf"|\s*,?\s*de\s+\d{{1,2}}/\d{{1,2}}/{_ANO}\b)?"
)
_NORMA_SEM_NUMERO_RE = re.compile(rf"\b{_TIPO_NORMA}\s+(?:de|do)\s+{_ANO}\b(?!\s+(?:a|ate|e)\s+{_ANO})")
_LEI_POR_NOME_RE = re.compile(
    r"\b(?:(?:nova\s+|antiga\s+)?lei\s+(?:geral\s+)?(?:de|do|da|das)\s+(?:licitacoes(?:\s+e\s+contratos(?:\s+administrativos)?)?"
    r"|responsabilidade\s+fiscal|acesso\s+a\s+informacao|improbidade(?:\s+administrativa)?"
    r"|diretrizes\s+orcamentarias|pregao|estatais|anticorrupcao)|lgpd|lrf|lai|ldo|loa)"
    r"(?:\s*,?\s*(?:n[o°.]*\s*)?\d[\d.]*(?:/\d{2,4})?)?"
    rf"\s*,?\s*(?:de\s+|\(\s*(?:de\s+)?|do\s+ano\s+de\s+)?{_ANO}\)?"
)
_PLANO_COM_ANO_RE = re.compile(rf"\b(?:ppa|plano\s+plurianual|agenda)\s+{_ANO}(?:\s*[-/–]\s*{_ANO})?\b")
_PROCESSO_RE = re.compile(
    r"\b(?:pregao|edital|processo|licitacao|dispensa|inexigibilidade|ata|contrato|concorrencia"
    r"|tomada\s+de\s+precos|convite|chamamento|leilao|srp)"
    r"(?:\s+(?:eletronico|presencial|publico|publica|de\s+registro\s+de\s+precos|de\s+precos|sei))*"
    r"\s*(?:n[o°.]*\s*)?\d[\d.\-]*/\d{2,4}\b"
    r"|\bn[o°]\.?\s*\d[\d.\-]*/\d{2,4}\b"
)


def _trechos_bloqueados(texto: str) -> list[Span]:
    """Normas, planos com ano no nome e números de processo."""
    regexes = (_NORMA_RE, _NORMA_SEM_NUMERO_RE, _LEI_POR_NOME_RE, _PLANO_COM_ANO_RE, _PROCESSO_RE)
    return [m.span() for regex in regexes for m in regex.finditer(texto)]


# --------------------------------------------------------------------------
# Valor (aceita "R$ 100.000,00", "100 mil", "1,5 milhão", "500k", "2 bi",
# "1 milhão e meio", faixas)
#
# Roda antes do ano: um número usado como valor ("acima de 2000 em 2025") não
# pode virar ano também.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Ano e período
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Órgão — heurística de token, sem lista canônica (ainda não existe uma)
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Catálogo de órgãos do corpus: os nomes de órgão e de unidade que existem na
# base (data/config/orgaos_corpus.json, gerado por
# ``py -m src.query_analysis orgaos`` a partir da base SQL). Com ele, o nome só
# cresce enquanto ainda corresponde a um órgão de verdade: "prefeitura de são
# gonçalo" vai até o fim, "Comando da Marinha e informe" para em "Marinha", e
# "banco de sangue" não vira órgão.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------


# Só o adjetivo (e "municípios" no plural, como esfera) indica esfera: "no
# município do Rio de Janeiro" é lugar, não filtro.
_ESFERAS = (
    ("estadual", r"\bestadua(?:l|is)\b"),
    ("municipal", r"\bmunicipa(?:l|is)\b|\bmunicipios\b"),
    ("federal", r"\bfedera(?:l|is)\b"),
)
# Norma sem número também não é esfera: "legislação federal", "lei estadual".
_NORMA_COM_ESFERA_RE = re.compile(
    r"\b(?:leis?|legislac(?:ao|oes)|decretos?|normas?|constituicao|regulamentos?|resoluc(?:ao|oes)|portarias?)"
    r"\s+(?:federa(?:l|is)|estadua(?:l|is)|municipa(?:l|is))\b"
)
# "Concorrência" e "dispensa" no sentido comum, não de modalidade.
_SENTIDO_COMUM_DE_MODALIDADE_RE = re.compile(
    r"\b(?:pouca|muita|baixa|alta|ampla|livre|sem|maior|menor|forte|fraca)\s+concorrencia\b"
    r"|\bconcorrencia\s+(?:entre|de\s+mercado|desleal|livre)\b"
    r"|\bdispensas?\s+(?:de\s+)?(?:garantia|servidor\w*|ponto|medica|licenca|pagamento|multa)\b"
)
# Modalidade da Lei 14.133, com o termo que o campo "modalidade" do PNCP
# contém (sem acento). A mais específica vem antes.
_MODALIDADES = (
    (r"\bpreg(?:ao|oes)\s+eletronicos?\b", "pregao eletronico"),
    (r"\bpreg(?:ao|oes)\s+presenciais\b|\bpreg(?:ao|oes)\s+presencial\b", "pregao presencial"),
    (r"\bpregao\b|\bpregoes\b", "pregao"),
    (r"\bdispensas?\b", "dispensa"),
    (r"\binexigibilidades?\b|\binexigive\w*", "inexigibilidade"),
    (r"\bconcorrencias?\b", "concorrencia"),
    (r"\bleil(?:ao|oes)\b", "leilao"),
    (r"\bcredenciamentos?\b", "credenciamento"),
    (r"\bdialogo\s+competitivo\b", "dialogo competitivo"),
)
# Modalidade só vira filtro em pergunta sobre os dados: com palavra de contrato
# ou compra, ou no plural com conta ("quantos pregões", "total das dispensas").
# "Quantas fases tem o pregão?" e "limites de valor para dispensa" são regra.
_PERGUNTA_SOBRE_CONTRATOS_RE = re.compile(
    r"\b(?:contratos|contratac(?:ao|oes)|compras?|gast\w*|contrat(?:ou|aram|ados|adas)|adquir\w*"
    r"|publicad[oa]s|homologad[oa]s|realizad[oa]s)\b"
)
_PERGUNTA_COM_CONTA_RE = re.compile(
    r"\b(?:quant[oa]s|quantidade|total|soma|media|medio|mediana|maiores|menores|ranking|valor(?:es)?)\b"
)
_VIGENCIA_RE = re.compile(r"\bvigent[es]*\b|\bvigencia\b|\bem vigor\b")
_REGISTRO_COM_VIGENCIA_RE = re.compile(r"\b(?:contratos?|atas?)\b")


def _analisar_com_trechos(query: str) -> tuple[QueryFilters, str, list[Span]]:
    """Filtros, texto normalizado e os trechos dele que viraram filtro."""
    if not isinstance(query, str) or not query.strip():
        return QueryFilters(), "", []
    texto = _normalize(query)
    bloqueados = _trechos_bloqueados(texto)
    valores = _extrair_valores(texto, bloqueados)
    periodo, expr_periodo = _extrair_periodo(texto, bloqueados + valores.numeros)
    cnpjs, expr_cnpj = _extrair_cnpjs(texto)
    # O CNPJ sai do resto da pergunta: senão vira termo do objeto no SQL.
    trechos = valores.expressoes + expr_periodo + expr_cnpj
    # "Quanto vale" é uma pergunta sobre valor, não objeto "vale" (mas
    # "vale transporte" continua sendo objeto). "No acervo" declara escopo.
    trechos += [m.span() for m in re.finditer(
        r"\bquanto\s+(?:vale|valem)\b|\b(?:n[oa]|d[oa]|neste|nesta|desse|deste)\s+acervo(?:\s+d[oe]\s+rio de janeiro)?\b",
        texto,
    )]
    # Métrica, agrupamento e referência saem do texto sem as expressões que já
    # viraram filtro: em "no máximo 50 mil" o "máximo" é filtro, não métrica.
    resto = _sem_trechos(texto, trechos)
    # Esfera e modalidade saem do texto sem filtros e sem citações de norma
    # ("Lei federal 14.133" não é esfera federal; "Lei do Pregão" não é
    # modalidade).
    sem_normas = _NORMA_COM_ESFERA_RE.sub(" ", _sem_trechos(texto, trechos + bloqueados))
    orgao, sigla, municipio, fornecedor, trechos_de_entidade = _entidades_da_pergunta(query, texto)
    trechos = trechos + trechos_de_entidade

    # A ordem de entrada vira a ordem dos filtros no SQL: esfera, município,
    # fornecedor, modalidade, CNPJ.
    analiticos: dict[str, str] = {}
    if esfera := _esfera_da_pergunta(sem_normas):
        analiticos["esfera"] = esfera
    if municipio and not (orgao and _normalize(municipio) in _normalize(orgao)):
        # "Prefeitura de Maricá" já prende no órgão: não repetir o município,
        # que sozinho traria os 23 órgãos da cidade em vez da prefeitura.
        analiticos["municipio"] = municipio
    if fornecedor:
        analiticos["fornecedor"] = fornecedor
    if modalidade := _modalidade_da_pergunta(sem_normas, orgao):
        analiticos["modalidade"] = modalidade
    if len(cnpjs) == 1:  # vários juntos é comparação: sem filtro
        analiticos["cnpj"] = cnpjs[0]
    if sigla:
        # "contratos da SEEDUC": o órgão vira o nome por extenso, e a sigla sai
        # do texto para não ser lida como objeto do contrato.
        trechos = trechos + [m.span() for m in re.finditer(rf"\b{re.escape(sigla)}\b", texto)]
    etapa_valor, medida_unitaria = _tipo_de_valor_da_pergunta(texto)
    filtros = QueryFilters(
        ano=periodo.ano,
        ano_final=periodo.ano_final,
        ano_desde=periodo.ano_desde,
        ano_ate=periodo.ano_ate,
        valor_min=valores.valor_min,
        valor_max=valores.valor_max,
        orgao=orgao,
        metrica=_metrica_da_pergunta(resto),
        agrupamentos=_agrupamentos_da_pergunta(resto),
        referencia_temporal=_referencia_da_pergunta(resto),
        filtros_analiticos=analiticos,
        papel_orgao=_papel_orgao(query, orgao),
        valor_min_exclusivo=valores.valor_min_exclusivo,
        valor_max_exclusivo=valores.valor_max_exclusivo,
        tipo_valor=etapa_valor or ("unitario" if medida_unitaria else None),
        tipo_medida="unitaria" if medida_unitaria else ("global" if etapa_valor else None),
        granularidade="item" if medida_unitaria else None,
        papel_cnpj=(_papel_cnpj(texto, expr_cnpj[0]) if len(cnpjs) == 1 else None),
    )
    return filtros, texto, trechos


def _metrica_da_pergunta(resto: str) -> Optional[str]:
    """Conta pedida (mediana, média, contagem, soma, máximo, mínimo); a primeira que casa vale."""
    if re.search(r"\b(?:media|medio)\b.*\b(?:por|unidade|item)\b", resto):
        return "media_unitaria"
    return next((valor for padrao, valor in (
        (r"\bmediana\b", "mediana"), (r"\b(?:media|medio)\b", "media"),
        (r"\b(?:quantos|quantas|quantidade)\b", "contagem"),
        # "Quanto a Fiotec recebeu/gastou?" pede valor, não contagem.
        (r"\b(?:soma|total|quanto)\b", "soma"),
        (r"\b(?:maior|maiores|maximo)\b", "maximo"), (r"\b(?:menor|menores|minimo)\b", "minimo"),
        (r"\bvalor\b", "soma"),
    ) if re.search(padrao, resto)), None)


def _agrupamentos_da_pergunta(resto: str) -> list[str]:
    """Campos pedidos com "por" ("por modalidade", "por órgão")."""
    return [campo for termo, campo in (
        ("modalidade", "modalidade"), ("municipio", "municipio"), ("orgao", "orgao_nome"),
        ("unidade", "unidade_nome"), ("categoria", "categoria"), ("classe", "classe"),
        ("esfera", "esfera"), ("fornecedor", "fornecedor"),
    ) if re.search(r"\bpor " + termo, resto)]


def _referencia_da_pergunta(resto: str) -> Optional[str]:
    """Qual data conta: assinatura, PCA, publicação, resultado ou vigência."""
    referencia = next((nome for termo, nome in (
        ("assinatura", "assinatura"), ("assinad", "assinatura"),
        ("pca", "pca"), ("publicac", "publicacao"), ("publicad", "publicacao"),
        ("resultado", "resultado"), ("homolog", "resultado"),
    ) if termo in resto), None)
    # "O que estava vigente em 2025?" pede a vigência das normas. Em pergunta
    # sobre contratos ou atas, "vigente" é a vigência deles e segue pelo
    # campo de sempre (data_vigencia). "Contratações" sozinha é o assunto
    # ("regras das contratações"), não o registro.
    if referencia is None and _VIGENCIA_RE.search(resto) and not _REGISTRO_COM_VIGENCIA_RE.search(resto):
        return "vigencia"
    return referencia


def _esfera_da_pergunta(sem_normas: str) -> Optional[str]:
    """Uma esfera citada vira filtro; várias juntas são comparação, sem filtro.

    "Por município" pede agrupamento, não filtro de esfera.
    """
    sem_agrupamento = re.sub(r"\bpor\s+(?:municipios?|orgaos?|esferas?)\b", " ", sem_normas)
    esferas = [nome for nome, padrao in _ESFERAS if re.search(padrao, sem_agrupamento)]
    return esferas[0] if len(esferas) == 1 else None


def _entidades_da_pergunta(query: str, texto: str) -> tuple[Optional[str], Optional[str], Optional[str],
                                                              Optional[str], list[Span]]:
    """Órgão (e a sigla), município e fornecedor citados, com os trechos que os citaram.

    Município e fornecedor viram o valor canônico do snapshot ("Volta Redonda",
    "empresa Cristália"), e o trecho que os citou sai dos termos de objeto. O
    fornecedor não olha dentro do que já é município/órgão.
    """
    orgao, sigla = _extrair_orgao(query)
    municipio, trecho_municipio = _extrair_municipio(texto)
    fornecedor, trecho_fornecedor = _extrair_fornecedor(
        texto, [s for s in (trecho_municipio,) if s], original=query)
    if fornecedor and orgao and trecho_fornecedor and re.search(
        r"\b(?:fornecedor(?:a)?|empresa|contratada)\s+(?:(?:a|o|da|do|de|e)\s+)*$",
        texto[:trecho_fornecedor[0]],
    ) and set(_tokens_de_nome(orgao)) & set(_tokens_de_nome(fornecedor)):
        # Uma entidade pode constar nos dois catálogos; o papel explicitamente
        # pedido prevalece, sem criar um segundo filtro por órgão.
        orgao, sigla = None, None
    trechos = [s for s in (trecho_municipio, trecho_fornecedor) if s]
    return orgao, sigla, municipio, fornecedor, trechos


def _modalidade_da_pergunta(sem_normas: str, orgao: Optional[str]) -> Optional[str]:
    """Modalidade citada que vira filtro.

    Só uma família ("pregões e dispensas" juntos é comparação), não negada, e
    numa pergunta sobre contratos ou com conta ("quantos pregões?").
    """
    sem_sentido_comum = _SENTIDO_COMUM_DE_MODALIDADE_RE.sub(" ", sem_normas)
    achadas = [(m, nome) for padrao, nome in _MODALIDADES if (m := re.search(padrao, sem_sentido_comum))]
    if len({nome.split()[0] for _, nome in achadas}) != 1:
        return None
    achada, nome = achadas[0]
    plural = achada.group().split()[0].endswith("s")
    negada = re.search(r"\bnao\b(?:\s+\w+){0,3}\s*$", sem_sentido_comum[: achada.start()])
    if not negada and (
        _PERGUNTA_SOBRE_CONTRATOS_RE.search(sem_sentido_comum)
        or (plural and (_PERGUNTA_COM_CONTA_RE.search(sem_sentido_comum) or orgao))
    ):
        return nome
    return None


def _tipo_de_valor_da_pergunta(texto: str) -> tuple[Optional[str], bool]:
    """Etapa do valor pedida (contratado, estimado, homologado) e se é preço unitário."""
    medida_unitaria = bool(re.search(
        r"\b(?:preco|valor)\s+unitari[oa]s?\b|\b(?:media|mediana|valor|preco)\b.*\bpor\s+(?:veiculo|ambulancia|quilo|quilograma|kg|unidade)s?\b",
        texto,
    ))
    etapa_valor = next((tipo for padrao, tipo in (
        (r"\bvalor(?:es)?\s+contratad[oa]s?\b", "contratado"),
        (r"\bvalor(?:es)?\s+estimad[oa]s?\b", "estimado"),
        (r"\b(?:valor|preco)(?:\s+unitari[oa]s?)?\s+homologad[oa]s?\b", "homologado"),
        (r"\bvalor(?:es)?\s+homologad[oa]s?\b", "homologado"),
    ) if re.search(padrao, texto)), None)
    return etapa_valor, medida_unitaria


def _papel_orgao(query: str, orgao: Optional[str]) -> Optional[str]:
    """Determina o papel da entidade em PCA quando a pergunta dá evidência."""
    if not orgao:
        return None
    texto = _normalize(query)
    if re.search(r"\borgaos?\b", texto):
        return "orgao"
    if re.search(r"\b(?:unidade(?:s)?(?:\s+responsave(?:l|is))?)\b", texto):
        return "unidade"
    pca = bool(re.search(r"\b(?:pca|plano\s+(?:anual\s+)?de\s+contratacoes)\b", texto))
    if pca and re.search(r"\bsecretarias?\b", texto):
        return "unidade"
    return None


def _papel_cnpj(texto: str, trecho: Span) -> Optional[str]:
    """Retorna o papel só quando a pergunta o associa expressamente ao CNPJ."""
    inicio, fim = trecho
    contexto = texto[max(0, inicio - 60):min(len(texto), fim + 40)]
    fornecedor = bool(re.search(r"\b(?:fornecedor(?:a)?|empresa|contratada)\b", contexto))
    orgao = bool(re.search(r"\b(?:orgao|contratante|unidade responsavel)\b", contexto))
    if fornecedor == orgao:  # nenhum papel ou menção contraditória
        return None
    return "fornecedor" if fornecedor else "orgao"


def _sem_trechos(texto: str, trechos: Sequence[Span]) -> str:
    caracteres = list(texto)
    for inicio, fim in trechos:
        caracteres[inicio:fim] = " " * (fim - inicio)
    return " ".join("".join(caracteres).split())


def _analisar(query: str) -> QueryFilters:
    return _analisar_com_trechos(query)[0]


def resolver_referencia_temporal(query: str, filtros: QueryFilters) -> QueryFilters:
    """Resolve uma única vez o default temporal de domínio antes dos executores.

    Referências explícitas sempre prevalecem. "Vigente" sem referência
    contratual explícita permanece separado; PCA usa seu ano próprio.
    """
    if not any(value is not None for value in (filtros.ano, filtros.ano_desde, filtros.ano_ate)):
        return filtros
    texto = _normalize(query)
    explicita = _analisar(query).referencia_temporal
    if explicita is not None:
        referencia = explicita
    elif re.search(r"\b(?:pca|plano\s+(?:anual\s+)?de\s+contratacoes)\b", texto):
        referencia = "pca"
    elif re.search(r"\b(?:vigentes?|vigencias?|em vigor)\b", texto):
        referencia = None  # Vigência de contratos/atas, distinta da assinatura.
    elif re.search(r"\b(?:contratos?|contratados?|assinados?)\b", texto):
        referencia = "assinatura"
    elif re.search(r"\b(?:contratacoes?|compras?|publicacoes?)\b", texto):
        referencia = "publicacao"
    else:
        # Não atribuir silenciosamente um ano sem contexto de domínio nem
        # conservar uma referência inventada pelo analisador LLM.
        referencia = None
    return filtros.model_copy(update={"referencia_temporal": referencia})


def _resolver_referencias_analise(resultado: QueryAnalysisResult) -> QueryAnalysisResult:
    """Materializa a referência resolvida no filtro legado e em cada intenção."""
    intencoes = [item.model_copy(update={
        "filtros": _resolver_papeis_entidade(
            item.pergunta, resolver_referencia_temporal(item.pergunta, item.filtros)),
    }) for item in resultado.intencoes]
    filtros = _resolver_papeis_entidade(
        resultado.query_original,
        resolver_referencia_temporal(resultado.query_original, resultado.filtros))
    if intencoes:
        filtros = intencoes[0].filtros
    return resultado.model_copy(update={"filtros": filtros, "intencoes": intencoes})


def _resolver_papeis_entidade(query: str, filtros: QueryFilters) -> QueryFilters:
    """Alinha papéis de órgão/unidade e CNPJ à menção explícita da pergunta."""
    atualizados: dict[str, Any] = {}
    if filtros.filtros_analiticos.get("cnpj"):
        atualizados["papel_cnpj"] = _analisar(query).papel_cnpj
    if filtros.orgao:
        atualizados["papel_orgao"] = _analisar(query).papel_orgao
    if not atualizados:
        return filtros
    return filtros.model_copy(update=atualizados)


def texto_sem_filtros(query: str) -> str:
    """Pergunta normalizada sem as expressões que viraram filtro de valor/ano.

    Para quem interpreta o resto da pergunta (ex.: ``structured_query``): em
    "média dos contratos com valor maior que 2000", "maior que 2000" já é o
    filtro, não um pedido de ranking, e "acima"/"milhão" não descrevem o objeto.
    """
    _, texto, trechos = _analisar_com_trechos(query)
    return _sem_trechos(texto, trechos)


# Tamanho máximo de cada texto da saída gravada no log: no modo llm, as
# subperguntas repetem trechos da pergunta.
_LIMITE_DE_TEXTO_NO_LOG = 500


def _para_o_log(valor: Any) -> Any:
    """Saída da etapa como vai para o log: sem ``query_original`` (já é o input),
    com CPF, telefone e e-mail pessoal mascarados e textos longos cortados.

    ``obs.py`` só mascara input/output que são texto, e a saída desta etapa é
    um dicionário (no modo llm, com a pergunta repetida nas subperguntas).
    """
    if isinstance(valor, dict):
        return {chave: _para_o_log(item) for chave, item in valor.items() if chave != "query_original"}
    if isinstance(valor, list):
        return [_para_o_log(item) for item in valor]
    if isinstance(valor, str):
        texto = mascarar(valor)[0]
        return texto if len(texto) <= _LIMITE_DE_TEXTO_NO_LOG else texto[:_LIMITE_DE_TEXTO_NO_LOG] + "…"
    return valor


def analisar_query(
    query: str,
    *,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
    cliente: Any = None,
    modo: Optional[str] = None,
) -> QueryAnalysisResult:
    """Etapa query_analysis: extrai ano/valor/órgão mencionados na pergunta.

    Não filtra nada sozinha — devolve QueryAnalysisResult para quem orquestra
    (pipeline.py) decidir o que fazer com os filtros (ver `filtros_para_retrieve`).
    """
    with default_logger.span(
        run_id=run_id, query_id=query_id, stage=Stage.QUERY_ANALYSIS, input=query
    ) as span:
        if (modo or ANALYSIS_MODE) == "llm" and isinstance(query, str) and query.strip():
            try:
                result = _resolver_referencias_analise(_analisar_llm(query, cliente, span))
                span.set_result(_para_o_log(result.model_dump(mode="json")))
                return result
            except Exception as error:
                # A mensagem de erro de validação repete o texto que o LLM devolveu.
                span.status = "degradado"
                span.extra["error_type"] = type(error).__name__
                span.extra["fallback"] = _para_o_log(f"{type(error).__name__}: {error}")
                if cliente is None and isinstance(error, (ValueError, KeyError)):
                    result = _resolver_referencias_analise(
                        QueryAnalysisResult(query_original=query, filtros=_analisar(query)))
                    _gravar_cache(query, result, span.extra.get("llm", {}), span.extra["fallback"])
        result = _resolver_referencias_analise(
            QueryAnalysisResult(query_original=query, filtros=_analisar(query)))
        span.set_result(_para_o_log(result.filtros.model_dump(exclude_none=True)))
        return result


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
        chave_orgao = "unidade_nome" if filtros.papel_orgao == "unidade" else "orgao"
        resultado[chave_orgao] = {"operator": "contains", "value": filtros.orgao}
    if filtros.valor_min is not None and filtros.valor_max is not None:
        resultado["valor"] = [
            {"operator": ">" if filtros.valor_min_exclusivo else ">=", "value": filtros.valor_min},
            {"operator": "<" if filtros.valor_max_exclusivo else "<=", "value": filtros.valor_max},
        ]
    elif filtros.valor_min is not None:
        resultado["valor"] = {"operator": ">" if filtros.valor_min_exclusivo else ">=", "value": filtros.valor_min}
    elif filtros.valor_max is not None:
        resultado["valor"] = {"operator": "<" if filtros.valor_max_exclusivo else "<=", "value": filtros.valor_max}
    # O ano filtra a referência temporal já resolvida pelo plano semântico.
    # ``None`` aqui mantém compatibilidade com QueryFilters construído à mão;
    # perguntas analisadas resolvem contrato/compra/PCA antes desta tradução.
    primeiro_ano, ultimo_ano = filtros.intervalo_de_anos()
    if filtros.referencia_temporal == "pca":
        anos = []
        if primeiro_ano is not None:
            anos.append({"operator": ">=", "value": primeiro_ano})
        if ultimo_ano is not None:
            anos.append({"operator": "<=", "value": ultimo_ano})
        if anos:
            resultado["ano_pca"] = anos
    elif filtros.referencia_temporal == "vigencia":
        # Norma vigente no período = começou a valer antes do fim dele. O
        # início da vigência vem de src/corpus_quality.py (só a base
        # normativa tem o campo). Nenhuma norma do corpus foi revogada; quando
        # houver, entra também data_vigencia_fim >= início do período.
        if ultimo_ano is not None:
            resultado["data_vigencia_inicio"] = {"operator": "<", "value": date(ultimo_ano + 1, 1, 1)}
    else:
        campo = {
            "assinatura": "data_assinatura", "resultado": "data_resultado",
            "publicacao": "data_publicacao",
        }.get(filtros.referencia_temporal, "data_vigencia")
        datas = []
        if primeiro_ano is not None:
            datas.append({"operator": ">=", "value": date(primeiro_ano, 1, 1)})
        if ultimo_ano is not None:
            datas.append({"operator": "<", "value": date(ultimo_ano + 1, 1, 1)})
        if datas:
            resultado[campo] = datas
    for campo in ("esfera", "municipio", "uf", "fornecedor"):

        if filtros.filtros_analiticos.get(campo) is not None:
            resultado[campo] = {"operator": "==", "value": filtros.filtros_analiticos[campo]}
    if filtros.filtros_analiticos.get("cnpj"):
        chave_cnpj = f"{filtros.papel_cnpj}_cnpj" if filtros.papel_cnpj else "cnpj"
        resultado[chave_cnpj] = {"operator": "contains", "value": filtros.filtros_analiticos["cnpj"]}
    if filtros.filtros_analiticos.get("modalidade"):
        # No PNCP a modalidade vem como "Pregão - Eletrônico": contém cada palavra.
        resultado["modalidade"] = [
            {"operator": "contains", "value": palavra}
            for palavra in filtros.filtros_analiticos["modalidade"].split()
        ]
    # O recorte explícito é obrigatório na natureza à qual se aplica. A busca
    # distingue inaplicabilidade (ex.: valor contratual em norma) de falta do
    # campo em um registro estruturado; esta última não autoriza relaxamento.
    return {chave: _marcar_obrigatorio(condicao) for chave, condicao in resultado.items()}


def _marcar_obrigatorio(condicao: Any) -> Any:
    if isinstance(condicao, list):
        return [{**item, "optional": False} for item in condicao]
    return {**condicao, "optional": False}


__all__ = ["analisar_query", "filtros_para_retrieve", "texto_sem_filtros"]


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["orgaos"]:
        gerado = gerar_catalogo_de_orgaos()
        print(f"{len(gerado['nomes'])} nomes e {len(gerado['siglas'])} siglas em {ORGAOS_CORPUS_PATH}")
    else:
        print("uso: py -m src.query_analysis orgaos   (regenera data/config/orgaos_corpus.json)")
