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
    return hashlib.sha256(json.dumps([query, config.GENERATION_MODEL, PROMPT_VERSION, _PROMPT, version,
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
    from src.generate import _cliente_padrao, _texto_e_tokens
    key = _analysis_key(query)
    span.extra["prompt_version"] = PROMPT_VERSION
    # Clientes injetados não consultam cache de produção.
    cacheable = cliente is None
    if cacheable and ANALYSIS_CACHE.exists():
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
    data = json.loads(raw)
    intents = []
    normalized = []
    for original in data["intencoes"]:
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
        original = _normalize(query)
        for campo in ("modalidade", "categoria", "classe", "fornecedor", "orgao_nome", "unidade_nome"):
            valor = intent.filtros.filtros_analiticos.get(campo)
            if valor and _normalize(str(valor)) not in original:
                raise ValueError(f"Filtro {campo} não explícito na pergunta")
        cnpj_modelo = intent.filtros.filtros_analiticos.get("cnpj")
        cnpj_explicito = explicit.filtros_analiticos.get("cnpj")
        if cnpj_modelo and cnpj_modelo != cnpj_explicito:
            raise ValueError("Filtro CNPJ não explícito na pergunta")
        if intent.filtros.papel_cnpj and intent.filtros.papel_cnpj != explicit.papel_cnpj:
            raise ValueError("Papel do CNPJ não explícito na pergunta")
        if intent.filtros.ano is not None and str(intent.filtros.ano) not in query:
            raise ValueError("Ano não explícito na pergunta")
        intents.append(intent)
    span.extra["campos_normalizados"] = normalized
    if not 1 <= len(intents) <= 4:
        raise ValueError("Análise deve produzir entre uma e quatro intenções")
    result = QueryAnalysisResult(query_original=query, filtros=intents[0].filtros, intencoes=intents, metodo="llm")
    if cacheable:
        _gravar_cache(query, result, span.extra["llm"])
    return result


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


def _normalize(texto: str) -> str:
    dobrado = texto.casefold()
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", dobrado)
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


Span = tuple[int, int]


def _sobrepoe(inicio: int, fim: int, trechos: Sequence[Span]) -> bool:
    return any(inicio < t_fim and t_inicio < fim for t_inicio, t_fim in trechos)


_ANO = r"(?:19|20)\d{2}"
_MES = r"(?:janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)"
_MESES = {
    nome: numero
    for numero, nome in enumerate(
        "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro".split(), 1
    )
}
_TEM_CARA_DE_ANO_RE = re.compile(_ANO)
_PALAVRA_DE_DINHEIRO = r"(?:reais|real|mil|milh(?:ao|oes)|bilh(?:ao|oes)|bi|mi|k)"
_MOEDA_DEPOIS_RE = re.compile(rf"\s*{_PALAVRA_DE_DINHEIRO}\b")

# Palavras que podem vir logo depois de um valor sem mudar o sentido dele:
# conectivos, verbos, particípios e advérbios. Qualquer outra palavra logo
# depois do número indica contagem ("3 contratos", "500 mil habitantes").
_PALAVRAS_NEUTRAS = frozenset(
    """
    a o as os ao aos e ou em no na nos nas para pra ate entre que com sem cada
    neste nesse nesta nessa deste desse desta dessa este esse esta essa
    durante desde apos antes depois sobre segundo conforme mas porem tambem so apenas somente
    qual quais quanto quantos quantas onde como hoje atualmente anual anuais mensal mensais
    houve ha foram foi sao era eram tiveram teve tem existem existe estao esta
    ficaram ficou somam somaram custaram custou valem valiam
    contratados contratadas contratado contratada assinados assinadas assinado assinada
    firmados firmadas publicados publicadas homologados homologadas pagos pagas
    empenhados empenhadas empenhado empenhada celebrados celebradas realizados realizadas
    vigentes vigente previstos previstas previsto prevista ordenados ordenadas listados listadas
    pode podem deve devem precisa precisam
    """.split()
)

# Substantivos de contagem: um número logo antes deles é quantidade, não valor
# nem ano ("2000 cestas básicas", "até 2000 alunos"). Só itens e unidades
# físicas: palavras de contexto ("2025 contratos de TI") ficam de fora.
_SUBSTANTIVOS_DE_CONTAGEM = frozenset(
    """
    cesta cestas item itens unidade unidades habitante habitantes pessoa pessoas dose doses
    veiculo veiculos ambulancia ambulancias computador computadores kit kits litro litros
    quilo quilos kg tonelada toneladas metro metros m km refeicao refeicoes aluno alunos
    servidor servidores leito leitos vaga vagas lampada lampadas cadeira cadeiras mesa mesas
    notebook notebooks tablet tablets medicamento medicamentos frasco frascos caixa caixas
    pacote pacotes resma resmas caneta canetas uniforme uniformes exemplar exemplares
    vacina vacinas teste testes exame exames livro livros carteira carteiras muda mudas
    cartucho cartuchos quentinha quentinhas marmita marmitas lote lotes
    familia familias morador moradores eleitor eleitores usuario usuarios paciente pacientes
    atendimento atendimentos visita visitas acesso acessos
    """.split()
)
# Contagem mais adiante: "mais de 2000 e menos de 5000 itens", "2000 ou mais
# unidades", "2000 a 3000 cestas".
_CONTAGEM_ADIANTE_RE = re.compile(
    r"\s+(?:(?:e|ou|a|ate)\s+(?:(?:mais|menos)\s+(?:de\s+)?)?\d[\d.,]*|ou\s+mais)\s+(?P<palavra>[a-z]+)"
)


def _contagem_adiante(texto: str, fim: int) -> bool:
    match = _CONTAGEM_ADIANTE_RE.match(texto, fim)
    return bool(match and match.group("palavra") in _SUBSTANTIVOS_DE_CONTAGEM)


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

_DINHEIRO_RE = re.compile(
    r"(?P<rs>r\$\s*)?"
    r"(?P<num>\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+\.\d+|\d+(?:,\d+)?)"
    r"(?:\s*(?P<mult>bilh(?:ao|oes)|milh(?:ao|oes)|mil|bi|mi|k)(?![a-z0-9]))?"
    r"(?P<reais>\s+(?:de\s+)?reais\b)?"
)
# "1 milhão e meio", "1 milhão e 500 mil", "1 milhão 200 mil", "2 mil e 500 reais"
_COMPLEMENTO_RE = re.compile(
    r"\s+(?:e\s+)?(?:(?P<meio>meio|meia)\b|(?P<num>\d+(?:,\d+)?)(?:\s*(?P<mult>mil|k)(?![a-z0-9]))?)"
    r"(?P<reais>\s+(?:de\s+)?reais\b)?"
)
_MULTIPLICADORES = {
    "mil": 1e3, "k": 1e3, "milhao": 1e6, "milhoes": 1e6, "mi": 1e6,
    "bilhao": 1e9, "bilhoes": 1e9, "bi": 1e9,
}
_GATILHOS_MINIMO = (
    r"acima d(?:e|os|as)|(?:igual ou )?superior(?:es)?(?: ou igua(?:l|is))? a"
    r"|(?:igual ou )?maior(?:es)?(?: ou igua(?:l|is) a| (?:do )?que)"
    r"|mais (?:de|(?:do )?que)|no minimo|minimo de|pelo menos|ao menos"
    r"|ultrapass(?:a|am|ou|aram|em)|exced(?:e|em|eu|eram)|super(?:a|am|ou|aram)|passa(?:m|ram)? de"
    r"|a partir do valor de|a partir de"
)
_GATILHOS_MAXIMO = (
    r"abaixo d(?:e|os|as)|(?:igual ou )?inferior(?:es)?(?: ou igua(?:l|is))? a"
    r"|(?:igual ou )?menor(?:es)?(?: ou igua(?:l|is) a| (?:do )?que)"
    r"|menos (?:de|(?:do )?que)|no maximo|maximo de|ate o valor de|ate"
)
_GATILHO_MINIMO_RE = re.compile(rf"\b(?P<gatilho>{_GATILHOS_MINIMO})\s+(?=r\$|\d)")
_GATILHO_MAXIMO_RE = re.compile(rf"\b(?P<gatilho>{_GATILHOS_MAXIMO})\s+(?=r\$|\d)")
# Os de tempo também servem a valor, mas neles um número com cara de ano é ano
# ("até 2025"), salvo depois de "valor" ("com valor até 2000").
_GATILHOS_DE_TEMPO = frozenset({"a partir de", "ate"})
# Gatilho de grandeza logo antes do número: ele é valor ou contagem, nunca ano.
_GATILHO_DE_GRANDEZA_ANTES_RE = re.compile(
    r"(?:\b(?:acima|abaixo)\s+d(?:e|os|as)|\b(?:superior|inferior)(?:es)?(?:\s+ou\s+igua(?:l|is))?\s+a"
    r"|\b(?:maior|menor)(?:es)?(?:\s+ou\s+igua(?:l|is)\s+a|\s+(?:do\s+)?que)"
    r"|\b(?:mais|menos)\s+(?:de|(?:do\s+)?que)|\bno\s+(?:minimo|maximo)|\b(?:minimo|maximo)\s+de"
    r"|\b(?:pelo|ao)\s+menos|\bultrapass\w*|\bexced\w*|\bsuper(?:a|am|ou|aram)|\bpassa(?:m|ram)?\s+de"
    r"|\bvalor"
    # aproximação: "uns 2000 contratos", "cerca de 2000"
    r"|\b(?:uns|umas|cerca\s+de|aproximadamente|quase|mais\s+ou\s+menos))\s*$"
)
# Antes do gatilho: o que se compara é tempo ("ano maior que 2023") ou algo que
# não é dinheiro ("quantidade superior a 1500", "prazo de até 90 dias").
# ("últimos 3 anos acima de 1 milhão" não compara o ano: o "anos" é contagem)
_ATRIBUTO_DE_TEMPO_ANTES_RE = re.compile(r"(?<!\d )\b(?:ano|anos|exercicio|periodo|data(?:\s+de\s+[a-z]+)?)\s*$")
_ATRIBUTO_NAO_MONETARIO_ANTES_RE = re.compile(
    r"\b(?:quantidade|qtd|numero|prazo|populacao|area|capacidade|idade|duracao|tempo|peso|volume"
    r"|extensao|distancia|potencia|consumo|tamanho|lotacao|vigencia|validade)"
    r"(?P<de_algo>\s+(?:de|do|da|dos|das)\s+[a-z]+)?(?:\s+(?:de|do|da|com))?\s*$"
)
_VALOR_ANTES_RE = re.compile(
    r"\bvalor(?:es)?(?:\s+(?:total|global|estimado|homologado|contratado|medio))?(?:\s+(?:de|do))?\s*$"
)
_FAIXA_ENTRE_RE = re.compile(r"\bentre\s+(?=r\$|\d)")
_FAIXA_DE_A_RE = re.compile(r"\bde\s+(?=r\$|\d)")
# "de" que faz parte de expressão de tempo: "a partir de", "antes de", "maio de".
_DE_DE_TEMPO_RE = re.compile(rf"\b(?:a partir|antes|depois|ano|exercicio|inicio|fim|final|{_MES})\s*$")


@dataclass(frozen=True)
class _Valor:
    valor: float
    numero: Span  # onde está o número (não pode virar ano)
    fim: int  # fim da expressão, incluindo multiplicador, complemento e "reais"
    tem_marca: bool  # R$, reais, multiplicador ou centavos
    mult: Optional[str]
    rs: bool = False


def _parse_numero(bruto: str) -> Optional[float]:
    texto = bruto
    if "," in texto:
        # Formato BR: ponto = milhar, vírgula = decimal.
        texto = texto.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
        # "100.000" / "1.200.000": pontos são separador de milhar.
        texto = texto.replace(".", "")
    # "1.5" (antes de "milhão") ou "2025.50": o ponto é o decimal.
    try:
        return float(texto)
    except ValueError:
        return None


def _sem_percentual_data_ou_letra_colada(resto: str) -> bool:
    if re.match(r"\s*(?:%|/|\(|°|-\s*\d|por\s+cento\b)", resto):
        return False
    return not re.match(r"[a-z0-9]", resto)  # colado no número: "100kg", "1o"


def _final_limpo(texto: str, fim: int, *, tem_marca: bool = False) -> bool:
    """Depois do valor vem fim de frase, pontuação ou palavra neutra?

    "3 contratos", "25%", "31/12", "90 (noventa)", "1 milhão de habitantes",
    "3 por modalidade" não têm final limpo. Com sinal de dinheiro, "por",
    "do/da <órgão>", "(cem mil reais)" e " - 2025" também são limpos.
    """
    resto = texto[fim:]
    if not resto.strip():
        return True
    if tem_marca and re.match(rf"\s*(?:\(|-\s*{_ANO}\b)", resto):
        return True
    if not _sem_percentual_data_ou_letra_colada(resto) or re.match(r"\s*x\b", resto):
        return False
    if re.match(r"\s*[?!.;:,)]", resto):
        return True
    palavra = re.match(r"\s+([a-z]+)", resto)
    if not palavra:
        return True
    seguinte = palavra.group(1)
    if tem_marca:
        # Com sinal de dinheiro o número é valor, a não ser que seja contagem:
        # "1 milhão de habitantes", "500 mil pessoas". "1 milhão da Secretaria",
        # "500 mil saúde", "1 milhão de obras" continuam valor.
        substantivo = re.match(r"\s+(?:(?:de|do|da|dos|das)\s+)?([a-z]+)", resto).group(1)
        return substantivo not in _SUBSTANTIVOS_DE_CONTAGEM
    if seguinte in ("de", "do", "da", "dos", "das"):
        # "2000 de 2025" e "2000 de março" seguem limpos; "2000 de itens", não.
        depois = re.match(rf"\s+\w+\s+(\w+)", resto)
        return bool(depois and (_TEM_CARA_DE_ANO_RE.fullmatch(depois.group(1)) or depois.group(1) in _MESES))
    if seguinte not in _PALAVRAS_NEUTRAS:
        return False
    return not _contagem_adiante(texto, fim)


def _ler_dinheiro(texto: str, inicio: int, *, aceita_ano: bool) -> Optional[_Valor]:
    """Lê um valor que começa exatamente em ``inicio``."""
    match = _DINHEIRO_RE.match(texto, inicio)
    if not match:
        return None
    numero = match.group("num")
    mult = match.group("mult")
    valor = _parse_numero(numero)
    if valor is None:
        return None
    if mult:
        valor *= _MULTIPLICADORES[mult]
    fim = match.end()
    reais = bool(match.group("reais"))
    complemento = _COMPLEMENTO_RE.match(texto, fim) if mult and not reais else None
    if complemento:
        escala = _MULTIPLICADORES[mult]
        if complemento.group("meio"):
            valor += 0.5 * escala
            fim = complemento.end()
        else:
            parte = _parse_numero(complemento.group("num"))
            escala_parte = _MULTIPLICADORES[complemento.group("mult")] if complemento.group("mult") else 1.0
            sem_e = not re.match(r"\s+e\s", texto[fim:])
            aceita = parte is not None and (
                (complemento.group("mult") and escala_parte < escala)  # "1 milhão (e) 200 mil"
                or (not sem_e and not complemento.group("mult") and mult == "mil" and parte < 1000)  # "2 mil e 500"
            )
            if aceita:
                valor += parte * escala_parte
                fim = complemento.end()
                reais = reais or bool(complemento.group("reais"))
    tem_marca = bool(match.group("rs") or mult or reais or "," in numero)
    if not _final_limpo(texto, fim, tem_marca=tem_marca):
        return None
    if not tem_marca:
        if not aceita_ano and _TEM_CARA_DE_ANO_RE.fullmatch(numero):
            return None  # "até 2025": ano, não valor
        if valor < 1000:
            return None  # "mais de 3", "no máximo 2": contagem, não dinheiro
    return _Valor(valor, match.span("num"), fim, tem_marca, mult, bool(match.group("rs")))


def _limite_por_gatilho(texto: str, gatilho_re: re.Pattern, bloqueados: Sequence[Span]) -> tuple[Optional[_Valor], Optional[Span]]:
    """Primeiro gatilho da pergunta que tem um valor válido logo depois."""
    for match in gatilho_re.finditer(texto):
        if _sobrepoe(match.start(), match.end(), bloqueados):
            continue
        antes = texto[: match.start()]
        if _ATRIBUTO_DE_TEMPO_ANTES_RE.search(antes):
            continue
        de_tempo = match.group("gatilho") in _GATILHOS_DE_TEMPO
        aceita_ano = not de_tempo or bool(_VALOR_ANTES_RE.search(antes))
        valor = _ler_dinheiro(texto, match.end(), aceita_ano=aceita_ano)
        if not valor:
            continue
        atributo = _ATRIBUTO_NAO_MONETARIO_ANTES_RE.search(antes)
        # "população acima de 100 mil", "quantidade de itens acima de 2000":
        # não é dinheiro. Mas em "quantidade de contratos acima de 1 milhão" o
        # valor é dos contratos.
        if atributo and (not atributo.group("de_algo") or not valor.tem_marca):
            continue
        return valor, (match.start(), valor.fim)
    return None, None


def _faixa(texto: str, bloqueados: Sequence[Span]) -> Optional[tuple[_Valor, _Valor, Span]]:
    """"entre X e/a Y" ou "de X a/até Y" com dois valores válidos."""
    for regex, separador in ((_FAIXA_ENTRE_RE, r"\s+(?P<sep>e|a)\s+"), (_FAIXA_DE_A_RE, r"\s+(?P<sep>a|ate)\s+")):
        for match in regex.finditer(texto):
            if _sobrepoe(match.start(), match.end(), bloqueados):
                continue
            antes = texto[: match.start()]
            if _ATRIBUTO_NAO_MONETARIO_ANTES_RE.search(antes):
                continue
            primeiro = _DINHEIRO_RE.match(texto, match.end())
            if not primeiro:
                continue
            sep = re.compile(separador).match(texto, primeiro.end())
            if not sep:
                continue
            numero_min = primeiro.group("num")
            marca_min = bool(primeiro.group("rs") or primeiro.group("mult") or primeiro.group("reais") or "," in numero_min)
            ano_no_min = not marca_min and _TEM_CARA_DE_ANO_RE.fullmatch(numero_min)
            if regex is _FAIXA_DE_A_RE and ano_no_min and (
                sep.group("sep") == "ate" or _DE_DE_TEMPO_RE.search(antes)
            ):
                # "dispensas de 2025 até R$ 50 mil", "a partir de 2024 até R$ 80
                # mil": o primeiro número é ano, e o "até" é um limite à parte.
                continue
            # Segundo lado com cara de ano e sem sinal de dinheiro só é valor se
            # o primeiro também é um número sem sinal ("entre 1000 e 2000").
            maximo = _ler_dinheiro(texto, sep.end(), aceita_ano=not marca_min)
            if not maximo:
                continue
            if not maximo.tem_marca and ano_no_min and _TEM_CARA_DE_ANO_RE.fullmatch(
                texto[maximo.numero[0]:maximo.numero[1]]
            ):
                continue  # "entre 2024 e 2025" é período
            valor_min = _parse_numero(numero_min)
            if valor_min is None:
                continue
            mult_min = primeiro.group("mult")
            if mult_min:
                valor_min *= _MULTIPLICADORES[mult_min]
            elif maximo.mult and valor_min < 1000:
                # "entre 500 e 800 mil": o multiplicador vale para os dois. Em
                # "entre 500 e 1 milhão" isso daria 500 milhões: aí é 500 mil.
                for escala in (_MULTIPLICADORES[maximo.mult], 1e3):
                    if valor_min * escala <= maximo.valor:
                        valor_min *= escala
                        marca_min = True
                        break
            if regex is _FAIXA_DE_A_RE and not (marca_min or maximo.tem_marca):
                continue  # "de 2 a 5" sem sinal de dinheiro: contagem
            if regex is _FAIXA_DE_A_RE and ano_no_min and (maximo.rs or maximo.valor > 20 * valor_min):
                continue  # "contratações de 2025 a R$ 1 milhão": o 2025 é ano
            if not marca_min and valor_min < 1000:
                continue
            minimo = _Valor(valor_min, primeiro.span("num"), primeiro.end(), marca_min, mult_min)
            if minimo.valor > maximo.valor:
                minimo, maximo = maximo, minimo
            return minimo, maximo, (match.start(), max(minimo.fim, maximo.fim))
    return None


@dataclass
class _Valores:
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    numeros: list[Span] = field(default_factory=list)  # não podem virar ano
    expressoes: list[Span] = field(default_factory=list)  # saem do texto_sem_filtros
    valor_min_exclusivo: bool = False
    valor_max_exclusivo: bool = False


_PERIODO_DE_DOIS_ANOS_RE = re.compile(rf"\bentre\s+{_ANO}\s+(?:e|a)\s+{_ANO}\b")
_FAIXA_AMBIGUA_RE = re.compile(
    rf"\bentre\s+(?:r\$\s*)?\d[\d.,]*\s*{_PALAVRA_DE_DINHEIRO}?\s+(?:e|a)\s+(?P<ano>{_ANO})\b(?![.,]\d)"
)


def _extrair_valores(texto: str, bloqueados: Sequence[Span]) -> _Valores:
    faixa = _faixa(texto, bloqueados)
    if faixa:
        minimo, maximo, expressao = faixa
        return _Valores(minimo.valor, maximo.valor, [minimo.numero, maximo.numero], [expressao])

    minimo, expr_min = _limite_por_gatilho(texto, _GATILHO_MINIMO_RE, bloqueados)
    maximo, expr_max = _limite_por_gatilho(texto, _GATILHO_MAXIMO_RE, bloqueados)
    numeros = [v.numero for v in (minimo, maximo) if v]
    # "entre 100 mil e 2025": faixa que não fecha; o 2025 também não é ano.
    numeros += [m.span("ano") for m in _FAIXA_AMBIGUA_RE.finditer(texto)
                if not _PERIODO_DE_DOIS_ANOS_RE.match(texto, m.start())]
    if minimo and maximo and minimo.valor > maximo.valor:
        # "acima de 1 milhão ou abaixo de 10 mil" é um OU, que o filtro não
        # expressa; aplicar os dois juntos não deixaria nada passar. As
        # expressões saem do texto do mesmo jeito: falam de valor, não do objeto.
        return _Valores(numeros=numeros, expressoes=[expr_min, expr_max])
    return _Valores(
        minimo.valor if minimo else None,
        maximo.valor if maximo else None,
        numeros,
        [e for e in (expr_min, expr_max) if e],
        valor_min_exclusivo=bool(expr_min and not re.search(
            r"igual|minimo|pelo menos|ao menos|a partir", texto[expr_min[0]:expr_min[1]])),
        valor_max_exclusivo=bool(expr_max and not re.search(
            r"igual|maximo|ate", texto[expr_max[0]:expr_max[1]])),
    )


# --------------------------------------------------------------------------
# Ano e período
# --------------------------------------------------------------------------

# Data: "31/12/2025", "03/2025", "2025-03-01", "1º de janeiro de 2025", "2º
# semestre de 2024", "março de 2025", "2025".
_MES_NUM = r"(?:0?[1-9]|1[0-2])"
_DATA = (
    rf"(?:(?P<d1>\d{{1,2}})/(?P<m1>{_MES_NUM})/(?P<a1>{_ANO})"
    rf"|(?P<m2>{_MES_NUM})/(?P<a2>{_ANO})"
    rf"|(?P<d3>\d{{1,2}})[o°]?\s+de\s+(?P<m3>{_MES})\s+de\s+(?P<a3>{_ANO})"
    rf"|(?P<s4>[12][o°]?|primeiro|segundo)\s+semestre\s+de\s+(?P<a4>{_ANO})"
    rf"|(?P<m5>{_MES})\s+de\s+(?P<a5>{_ANO})"
    rf"|(?P<a7>{_ANO})-(?P<m7>{_MES_NUM})(?:-(?P<d7>\d{{1,2}}))?"
    rf"|(?P<a6>{_ANO}))(?![.,]\d|\d|-\d)"
)
_GRUPOS_DE_ANO = ("a1", "a2", "a3", "a4", "a5", "a7", "a6")
_ABERTO_RE = re.compile(
    r"\b(?P<gatilho>a partir|a contar|desde|depois|apos|posterior(?:es)?|antes|anterior(?:es)?|ate)"
    r"(?:\s+(?:de|do|da|a|ao|dos|das))?(?:\s+(?:o|a))?"
    r"(?:\s+(?P<parte>inicio|comeco|fim|final|meados|dia|mes|exercicio|ano))?(?:\s+(?:de|do))?"
    rf"(?:\s+(?:ano|exercicio)(?:\s+de)?)?\s+{_DATA}"
)
_EM_DIANTE_RE = re.compile(
    rf"\b(?:(?:de|desde|a partir de|a contar de)\s+)?(?:\d{{1,2}}/{_MES_NUM}/|{_MES_NUM}/|{_MES}\s+de\s+)?"
    rf"(?P<ano>{_ANO})\s+(?:em\s+diante|pra\s+frente|para\s+frente|pra\s+ca|para\s+ca|ou\s+depois|ou\s+mais\s+tarde|ou\s+posteriores?|ou\s+mais\s+recentes"
    r"|ate\s+(?:hoje(?:\s+em\s+dia)?|agora|o\s+momento|os\s+dias\s+(?:atuais|de\s+hoje)|a\s+presente\s+data|o\s+presente))\b(?!\s+(?:de|do|da)\s)"
)
_OU_ANTES_RE = re.compile(rf"\b(?P<ano>{_ANO})\s+ou\s+(?:antes|anteriores?)\b(?!\s+(?:de|do|da|a|ao)\s)")
_COMPARACAO_DE_ANO_RE = re.compile(
    r"\b(?:ano|anos|exercicio|data(?:\s+de\s+[a-z]+)?)\s+(?P<igual1>igual\s+ou\s+)?"
    r"(?:(?P<maior>maior|superior|posterior)|(?P<menor>menor|inferior|anterior))(?:es)?"
    r"(?P<igual2>\s+ou\s+igua(?:l|is))?(?:\s+(?:do\s+)?que|\s+a|\s+ao)?"
    rf"\s+(?P<ano>{_ANO})\b"
)
_DESDE = frozenset({"a partir", "a contar", "desde"})
_DEPOIS = frozenset({"depois", "apos", "posterior", "posteriores"})
_ATE = frozenset({"ate"})
_ANTES = frozenset({"antes", "anterior", "anteriores"})
# "com vigência após 2025": o período aberto qualifica outra data.
_QUALIFICADOR_DE_VIGENCIA_RE = re.compile(r"\b(?:vigencia|vigente|vigentes|validade|prazo|vencem|vence|vencimento|terminam|termina|encerram|encerra)(?:\s+(?:de|do))?\s*$")

# Período fechado explícito: "de 2023 até 2025", "entre 2024 e 2025", "de
# março de 2023 a maio de 2024", "de 01/03/2024 até 28/02/2025".
_LADO_DE_PERIODO = rf"(?:\d{{1,2}}/{_MES_NUM}/|{_MES_NUM}/|(?:\d{{1,2}}[o°]?\s+de\s+)?{_MES}\s+de\s+)?"
_PERIODO_RE = re.compile(
    rf"\b(?:entre|de|desde|a partir de|a contar de)\s+{_LADO_DE_PERIODO}(?P<inicio>{_ANO})(?:-\d{{1,2}}(?:-\d{{1,2}})?)?"
    rf"\s+(?:e|a|ate)\s+{_LADO_DE_PERIODO}(?P<fim>{_ANO})(?:-\d{{1,2}}(?:-\d{{1,2}})?)?\b(?![.,]\d)"
)
# Ano solto e pares "2024/2025", "2024-2025", datas "30/09/2025", "03/2025", "2025-03-01".
_ANO_SOLTO_RE = re.compile(rf"(?<![\d.,/\-])\b(?P<ano>{_ANO})\b(?![.,]\d|/\d|-\d|\d)")
_PAR_DE_ANOS_RE = re.compile(rf"\b(?P<a>{_ANO})\s*[/\-–—]\s*(?P<b>{_ANO})\b")
_DATA_SOLTA_RE = re.compile(
    rf"(?<![\d.])\b(?:(?:\d{{1,2}}/)?{_MES_NUM}/(?P<ano>{_ANO})\b|\d{{1,2}}\.{_MES_NUM}\.(?P<ponto>{_ANO})\b|(?P<iso>{_ANO})-{_MES_NUM}(?:-\d{{1,2}})?\b|{_MES}/(?P<mes_barra>{_ANO})\b)"
)
# Antes do ano: palavras que confirmam que é ano mesmo com substantivo depois
# ("PCA 2025 itens de TI", "exercício de 2025"). "Em" também confirma ("em
# 2025 milhões de reais foram gastos"), mas perde para contagem ("entregues
# em 2000 unidades").
_CONTEXTO_MUITO_FORTE_DE_ANO_RE = re.compile(r"(?:\bano(?:\s+de)?|\bexercicio(?:\s+de)?|\bpca(?:\s+de)?)\s*$")
_CONTEXTO_FORTE_DE_ANO_RE = re.compile(r"(?:\bem|\bano(?:\s+de)?|\bexercicio(?:\s+de)?|\bpca(?:\s+de)?)\s*$")
# Número no nome de empresa: "Rio 2000 Engenharia", "empresa 2000 Serviços Ltda".
_NOME_DE_EMPRESA_ANTES_RE = re.compile(r"\b(?:empresa|firma|construtora|grupo|consorcio)(?:\s+[a-z]+)?\s*$")
_NOME_DE_EMPRESA_DEPOIS = frozenset(
    """
    engenharia servicos comercio construcoes construtora ltda eireli epp sa distribuidora industria
    tecnologia solucoes consultoria informatica transportes empreendimentos participacoes
    """.split()
)
# Antes do número: é dinheiro, não ano ("no valor de 2000", "R$ 2025"). "O valor
# total de 2025" é ano: sem preposição antes de "valor".
_CONTEXTO_DE_VALOR_RE = re.compile(
    r"(?:r\$|\b(?:no|num|com|pelo|por|de|em)\s+(?:um\s+)?valor(?:\s+(?:total|global|estimado|homologado|contratado))?\s+de)\s*$"
)
# Palavras de tempo antes do ano, que saem junto no texto_sem_filtros.
_EXPRESSAO_DE_TEMPO_ANTES_RE = re.compile(
    r"(?:\b(?:no|do|em|o|a|ao|na|nos)\s+)?"
    rf"\b(?:exercicio|ano|(?:mes\s+de\s+)?{_MES}(?:\s+(?:e|a|ate)\s+{_MES})?"
    r"|(?:(?:[12][o°]?|primeiro|segundo)\s+)?semestre"
    r"|(?:(?:[1-4][o°]?|primeiro|segundo|terceiro|quarto)\s+)?trimestre|inicio|comeco|fim|final|meados)"
    r"(?:\s+(?:de|do))?\s*$"
)
_EXPRESSAO_DE_TEMPO_DEPOIS_RE = re.compile(r"\s+(?:inteiro|todo|completo)\b")


@dataclass(frozen=True)
class _Periodo:
    ano: Optional[int] = None
    ano_final: Optional[int] = None
    ano_desde: Optional[int] = None
    ano_ate: Optional[int] = None


def _unidade_de_tempo(match: re.Match) -> tuple[date, date]:
    """(primeiro dia, último dia) da data lida por ``_DATA``."""
    g = match.groupdict()
    if g["a1"]:
        ano, mes, dia = int(g["a1"]), int(g["m1"]), int(g["d1"])
        try:
            primeiro = ultimo = date(ano, mes, dia)
        except ValueError:
            primeiro, ultimo = date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])
    elif g["a3"]:
        ano, mes = int(g["a3"]), _MESES[g["m3"]]
        primeiro = ultimo = date(ano, mes, min(int(g["d3"]), calendar.monthrange(ano, mes)[1]))
    elif g["a7"] and g["d7"]:
        ano, mes = int(g["a7"]), int(g["m7"])
        primeiro = ultimo = date(ano, mes, min(int(g["d7"]), calendar.monthrange(ano, mes)[1]))
    elif g["a2"] or g["a5"] or g["a7"]:
        ano = int(g["a2"] or g["a5"] or g["a7"])
        mes = int(g["m2"] or g["m7"]) if (g["a2"] or g["a7"]) else _MESES[g["m5"]]
        primeiro, ultimo = date(ano, mes, 1), date(ano, mes, calendar.monthrange(ano, mes)[1])
    elif g["a4"]:
        ano = int(g["a4"])
        primeiro_semestre = g["s4"] in ("1", "1o", "1°", "primeiro")
        primeiro, ultimo = (date(ano, 1, 1), date(ano, 6, 30)) if primeiro_semestre else (date(ano, 7, 1), date(ano, 12, 31))
    else:
        ano = int(g["a6"])
        primeiro, ultimo = date(ano, 1, 1), date(ano, 12, 31)
    parte = g.get("parte")
    if parte in ("inicio", "comeco"):
        return primeiro, primeiro
    if parte in ("fim", "final"):
        return ultimo, ultimo
    if parte == "meados":
        meio = primeiro + (ultimo - primeiro) / 2
        return meio, meio
    return primeiro, ultimo


def _ano_simples_depois_de_gatilho_e_contagem(texto: str, match: re.Match) -> bool:
    """"até 2000 alunos", "a partir de 2000 unidades", "até 2000m", "até 2000
    milhões": o número depois do gatilho de tempo não é ano."""
    if not match.group("a6"):
        return False
    fim = match.end("a6")
    if _MOEDA_DEPOIS_RE.match(texto, fim) or re.match(r"[a-z]", texto[fim:]):
        return True
    seguinte = re.match(r"\s+([a-z]+)", texto[fim:])
    return bool(seguinte and seguinte.group(1) in _SUBSTANTIVOS_DE_CONTAGEM) or _contagem_adiante(texto, fim)


@dataclass
class _Aberto:
    desde: Optional[int] = None
    ate: Optional[int] = None
    trechos: list[Span] = field(default_factory=list)
    so_qualificadores: bool = True  # todos qualificam outra data ("vigência após")


def _periodo_aberto(texto: str, bloqueados: Sequence[Span]) -> _Aberto:
    aberto = _Aberto()

    def registrar(desde: Optional[int], ate: Optional[int], trecho: Span) -> None:
        if desde is not None and aberto.desde is None:
            aberto.desde = desde
        elif ate is not None and aberto.ate is None:
            aberto.ate = ate
        else:
            return
        aberto.trechos.append(trecho)
        if not _QUALIFICADOR_DE_VIGENCIA_RE.search(texto[: trecho[0]]):
            aberto.so_qualificadores = False

    for match in _COMPARACAO_DE_ANO_RE.finditer(texto):
        if _sobrepoe(*match.span("ano"), bloqueados):
            continue
        ano = int(match.group("ano"))
        igual = bool(match.group("igual1") or match.group("igual2"))
        if match.group("maior"):
            registrar(ano if igual else ano + 1, None, match.span())
        else:
            registrar(None, ano if igual else ano - 1, match.span())
    for match in _EM_DIANTE_RE.finditer(texto):
        if not _sobrepoe(*match.span("ano"), bloqueados):
            registrar(int(match.group("ano")), None, match.span())
    for match in _OU_ANTES_RE.finditer(texto):
        if not _sobrepoe(*match.span("ano"), bloqueados):
            registrar(None, int(match.group("ano")), match.span())
    for match in _ABERTO_RE.finditer(texto):
        grupo = next(g for g in _GRUPOS_DE_ANO if match.group(g))
        if _sobrepoe(*match.span(grupo), bloqueados) or _sobrepoe(*match.span(), aberto.trechos):
            continue
        gatilho = match.group("gatilho")
        # "com valor até 2000": "até" e "a partir de" também servem a valor.
        if gatilho in ("ate", "a partir") and _VALOR_ANTES_RE.search(texto[: match.start()]):
            continue
        if _ano_simples_depois_de_gatilho_e_contagem(texto, match):
            continue
        primeiro_dia, ultimo_dia = _unidade_de_tempo(match)
        if gatilho in _DESDE:
            registrar(primeiro_dia.year, None, match.span())
        elif gatilho in _DEPOIS:
            registrar((ultimo_dia + timedelta(days=1)).year, None, match.span())
        elif gatilho in _ATE:
            registrar(None, ultimo_dia.year, match.span())
        elif gatilho in _ANTES:
            registrar(None, (primeiro_dia - timedelta(days=1)).year, match.span())
    return aberto


def _eh_ano(texto: str, inicio: int, fim: int, bloqueados: Sequence[Span]) -> tuple[bool, bool]:
    """(é o ano da pergunta?, com contexto forte?) para o 19xx/20xx em ``texto[inicio:fim]``."""
    if _sobrepoe(inicio, fim, bloqueados):
        return False, False
    antes = texto[:inicio]
    if _CONTEXTO_DE_VALOR_RE.search(antes) or _GATILHO_DE_GRANDEZA_ANTES_RE.search(antes):
        return False, False
    resto = texto[fim:]
    seguinte = re.match(r"\s+([a-z]+)", resto)
    if _NOME_DE_EMPRESA_ANTES_RE.search(antes) or (seguinte and seguinte.group(1) in _NOME_DE_EMPRESA_DEPOIS):
        return False, False
    if _CONTEXTO_MUITO_FORTE_DE_ANO_RE.search(antes):
        return True, True
    if seguinte and seguinte.group(1) in _SUBSTANTIVOS_DE_CONTAGEM:
        return False, False  # "entregues em 2000 unidades": contagem, mesmo depois de "em"
    if _CONTEXTO_FORTE_DE_ANO_RE.search(antes):
        return True, True
    if _MOEDA_DEPOIS_RE.match(texto, fim):
        return False, False
    if not _sem_percentual_data_ou_letra_colada(resto):
        return False, False
    return not _contagem_adiante(texto, fim), False


_NAO_SAO_QUANTIDADE = frozenset(
    "mais menos maiores menores superiores inferiores dos das pelos pelas versus vs".split()
)


def _anos_soltos(texto: str, bloqueados: Sequence[Span]) -> tuple[list[int], list[Span]]:
    anos: list[tuple[int, bool, bool, Span]] = []  # (ano, forte, seguido de palavra de conteúdo, trecho)
    trechos: list[Span] = []
    for match in _PAR_DE_ANOS_RE.finditer(texto):
        if not _sobrepoe(*match.span(), bloqueados):
            for grupo in ("a", "b"):
                anos.append((int(match.group(grupo)), True, False, match.span()))
            trechos.append(match.span())
    for match in _DATA_SOLTA_RE.finditer(texto):
        if not _sobrepoe(*match.span(), bloqueados) and not _sobrepoe(*match.span(), trechos):
            anos.append((int(match.group("ano") or match.group("ponto") or match.group("iso") or match.group("mes_barra")), True, False, match.span()))
            trechos.append(match.span())
    for match in _ANO_SOLTO_RE.finditer(texto):
        if _sobrepoe(*match.span(), trechos):
            continue
        eh_ano, forte = _eh_ano(texto, *match.span(), bloqueados)
        if not eh_ano:
            continue
        seguinte = re.match(r"\s+([a-z]+)", texto[match.end():])
        # Substantivo no plural logo depois ("2000 vacinas"): candidato a quantidade.
        conteudo = bool(
            seguinte
            and seguinte.group(1).endswith("s")
            and seguinte.group(1) not in _PALAVRAS_NEUTRAS
            and seguinte.group(1) not in _NAO_SAO_QUANTIDADE
        )
        inicio, fim = match.span()
        tempo_antes = _EXPRESSAO_DE_TEMPO_ANTES_RE.search(texto[:inicio])
        if tempo_antes:
            inicio = tempo_antes.start()
        tempo_depois = _EXPRESSAO_DE_TEMPO_DEPOIS_RE.match(texto, fim)
        if tempo_depois:
            fim = tempo_depois.end()
        anos.append((int(match.group("ano")), forte, conteudo, (inicio, fim)))
        trechos.append((inicio, fim))
    if len({ano for ano, *_ in anos}) > 1 and any(forte for _, forte, _, _ in anos):
        # Com um ano de contexto forte ("em 2021"), um número de contexto fraco
        # seguido de substantivo é quantidade: "compra de 2000 vacinas em 2021".
        anos = [a for a in anos if a[1] or not a[2]]
    return [ano for ano, *_ in anos], [trecho for *_, trecho in anos]


def _extrair_periodo(texto: str, bloqueados: Sequence[Span]) -> tuple[_Periodo, list[Span]]:
    """Período da pergunta e os trechos usados para chegar nele."""
    for match in _PERIODO_RE.finditer(texto):
        if not any(_sobrepoe(*match.span(g), bloqueados) for g in ("inicio", "fim")):
            inicio, fim = sorted((int(match.group("inicio")), int(match.group("fim"))))
            return _Periodo(ano=inicio, ano_final=fim if fim != inicio else None), [match.span()]

    aberto = _periodo_aberto(texto, bloqueados)
    anos, trechos_anos = _anos_soltos(texto, list(bloqueados) + aberto.trechos)
    trechos = aberto.trechos + trechos_anos

    if aberto.desde is not None or aberto.ate is not None:
        desde, ate = aberto.desde, aberto.ate
        if desde is not None and ate is not None and desde > ate:
            return _Periodo(), trechos  # "antes de 2024 ou depois de 2025" é um OU
        if anos:
            dentro = all((desde is None or ano >= desde) and (ate is None or ano <= ate) for ano in anos)
            if aberto.so_qualificadores or dentro:
                # "assinados em 2024 com vigência após 2025": vale o ano da pergunta.
                primeiro, ultimo = min(anos), max(anos)
                return _Periodo(ano=primeiro, ano_final=ultimo if ultimo != primeiro else None), trechos
            return _Periodo(), trechos  # ano e período aberto se contradizem
        if desde is not None and ate is not None:
            return _Periodo(ano=desde, ano_final=ate if ate != desde else None), trechos
        return _Periodo(ano_desde=desde, ano_ate=ate), trechos

    if not anos:
        return _Periodo(), []
    primeiro, ultimo = min(anos), max(anos)
    return _Periodo(ano=primeiro, ano_final=ultimo if ultimo != primeiro else None), trechos


# --------------------------------------------------------------------------
# Órgão — heurística de token, sem lista canônica (ainda não existe uma)
# --------------------------------------------------------------------------

_GATILHOS_ORGAO = frozenset(
    """
    secretaria prefeitura ministerio tribunal instituto fundacao autarquia universidade
    departamento camara assembleia orgao agencia fundo comando banco defensoria municipio
    procuradoria controladoria policia companhia empresa conselho
    """.split()
)
# Gatilho que só vale com a palavra seguinte: "empresa pública", não "a empresa X".
_GATILHO_EXIGE = {"empresa": frozenset({"publica", "municipal", "estadual"})}
# Gatilho + palavra que não é órgão, mesmo com maiúscula ("Banco de Preços" é
# ferramenta de pesquisa de preços).
_NAO_E_ORGAO = frozenset(
    {
        ("banco", "precos"), ("banco", "preco"), ("banco", "dados"), ("banco", "horas"),
        ("banco", "sangue"), ("camara", "ar"), ("fundo", "reserva"),
    }
)
# Adjetivos logo depois do gatilho: "Secretaria Municipal de Saúde", "Polícia
# Militar", "Defensoria Pública", "Procuradoria Geral".
_QUALIFICADORES_DE_ORGAO = frozenset(
    """
    municipal estadual federal nacional regional geral publica publico especial executiva
    executivo militar civil legislativa metropolitana superior extraordinaria central
    """.split()
)
# Dentro do nome só entram estas conectoras ("Secretaria de Ciência e
# Tecnologia", "Tribunal de Contas do Estado"). "No", "na", "em" encerram o
# nome: "Secretaria de Saúde no PCA" é a Secretaria de Saúde.
_PALAVRA_CONECTORA = frozenset({"de", "do", "da", "dos", "das", "e"})
# Palavras que encerram o nome mesmo com maiúscula ("Prefeitura De Maricá Em
# 2025"): preposições de lugar e siglas/termos do sistema que vêm depois dele.
_FIM_DO_NOME_DO_ORGAO = frozenset(
    "em no na nos nas pelo pela pelos pelas para "
    "pca pncp sei siga lei decreto portaria edital pregao ata atas contrato contratos".split()
)
# Palavras que não fazem parte de nome de órgão, mesmo depois de "de".
_NAO_SAO_NOME = frozenset(
    """
    qual quais quanto quantos quantas que como onde quando cujo cuja contratacao contratacoes
    compra compras valor valores acima abaixo maior menor maiores menores total media mediana
    ano anos mes meses ate entre mais menos todos todas algum alguma cada outro outros
    o a os as um uma
    janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
    """.split()
) | _FIM_DO_NOME_DO_ORGAO
# Como o PNCP grava: "Prefeitura de Niterói" aparece como "MUNICIPIO DE NITEROI".
_SINONIMOS_DE_ORGAO = {"prefeitura": "municipio"}
# Siglas de órgãos do RJ, pelo nome que aparece no PNCP.
_SIGLAS_DE_ORGAO = {
    "seeduc": "Secretaria de Estado de Educação",
    "ses": "Secretaria de Estado de Saúde",
    "sefaz": "Secretaria de Estado de Fazenda",
    "seplag": "Secretaria de Estado de Planejamento e Gestão",
    "seap": "Secretaria de Estado de Administração Penitenciária",
    "dpe": "Defensoria Pública do Estado",
    "dperj": "Defensoria Pública do Estado",
    "pge": "Procuradoria Geral do Estado",
    "tce": "Tribunal de Contas do Estado",
    "tcerj": "Tribunal de Contas do Estado",
    "tjrj": "Tribunal de Justiça do Estado do Rio de Janeiro",
    "mprj": "Ministério Público do Estado do Rio de Janeiro",
    "alerj": "Assembleia Legislativa do Estado do Rio de Janeiro",
    "detran": "Departamento de Trânsito",
    "cedae": "Companhia Estadual de Águas e Esgotos",
    "uerj": "Universidade do Estado do Rio de Janeiro",
    "uenf": "Universidade Estadual do Norte Fluminense",
    "faetec": "Fundação de Apoio à Escola Técnica",
    "pmerj": "Polícia Militar do Estado do Rio de Janeiro",
    "pcerj": "Polícia Civil do Estado do Rio de Janeiro",
    "cbmerj": "Corpo de Bombeiros Militar do Estado do Rio de Janeiro",
    "inea": "Instituto Estadual do Ambiente",
    "degase": "Departamento Geral de Ações Socioeducativas",
    # Órgãos federais com sede no RJ
    "fiocruz": "Fundação Oswaldo Cruz",
    "ufrj": "Universidade Federal do Rio de Janeiro",
    "uff": "Universidade Federal Fluminense",
    "unirio": "Universidade Federal do Estado do Rio de Janeiro",
    "ufrrj": "Universidade Federal Rural do Rio de Janeiro",
    "inca": "Instituto Nacional de Câncer",
}
_PONTUACAO = ".,;:!?()\"'“”‘’"
_PALAVRAS_FUNCIONAIS = frozenset("de da do das dos em no na e a o os as que para com por pela pelo".split())

# --------------------------------------------------------------------------
# Catálogo de órgãos do corpus: os nomes de órgão e de unidade que existem na
# base (data/config/orgaos_corpus.json, gerado por
# ``py -m src.query_analysis orgaos`` a partir da base SQL). Com ele, o nome só
# cresce enquanto ainda corresponde a um órgão de verdade: "prefeitura de são
# gonçalo" vai até o fim, "Comando da Marinha e informe" para em "Marinha", e
# "banco de sangue" não vira órgão.
# --------------------------------------------------------------------------

ORGAOS_CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "config" / "orgaos_corpus.json"
_TOKEN_DE_NOME_RE = re.compile(r"[a-z0-9]+")
_VAZIAS_DE_NOME = frozenset("de do da dos das e em a o rj".split())
_SIGLA_NO_NOME_RE = re.compile(r"(?:^|\s-\s)([A-Z]{2,12})(?:/[A-Z]{2})?(?=\s-\s|\s*$)|^([A-Z]{3,12})\s-\s")


@dataclass(frozen=True)
class _Catalogo:
    nomes: tuple[str, ...]
    tokens: tuple[tuple[str, ...], ...]
    siglas: dict[str, str]  # sigla normalizada -> nome do órgão
    # Listas canônicas do snapshot, para o mesmo valor escrito de outro jeito
    # virar o mesmo filtro ("Volta Redonda", "empresa Cristália").
    municipios: tuple[str, ...] = ()
    fornecedores: tuple[str, ...] = ()


def _tokens_de_nome(texto: str) -> list[str]:
    return [t for t in _TOKEN_DE_NOME_RE.findall(_normalize(texto)) if t not in _VAZIAS_DE_NOME]


@lru_cache(maxsize=1)
def _catalogo_de_orgaos() -> Optional[_Catalogo]:
    if not ORGAOS_CORPUS_PATH.exists():
        return None
    dados = json.loads(ORGAOS_CORPUS_PATH.read_text(encoding="utf-8"))
    nomes = tuple(dados.get("nomes", []))
    return _Catalogo(
        nomes, tuple(tuple(_tokens_de_nome(n)) for n in nomes), dict(dados.get("siglas", {})),
        tuple(dados.get("municipios", ())), tuple(dados.get("fornecedores", ())),
    )


def _token_casa(da_pergunta: str, do_nome: str) -> bool:
    """Igual, ou abreviado no nome ("SECRET ESTADO SAÚDE")."""
    if da_pergunta == do_nome:
        return True
    return len(do_nome) >= 4 and len(da_pergunta) >= 4 and (
        da_pergunta.startswith(do_nome) or do_nome.startswith(da_pergunta)
    )


def _orgao_do_catalogo(chaves: Sequence[str], exigir_algum: Sequence[str] = ()) -> Optional[str]:
    """Primeiro órgão do catálogo que contém todas as palavras-chave (e, se
    pedido, ao menos uma de ``exigir_algum``)."""
    catalogo = _catalogo_de_orgaos()
    if not catalogo or not chaves:
        return None
    for nome, tokens in zip(catalogo.nomes, catalogo.tokens):
        if exigir_algum and not any(token in exigir_algum for token in tokens):
            continue
        if all(any(_token_casa(chave, token) for token in tokens) for chave in chaves):
            return nome
    return None


def _orgao_do_municipio(cidade: Sequence[str]) -> str:
    """Nome com que o PNCP grava a prefeitura: "MUNICIPIO DE MARICA" é a razão
    social do órgão ("PREFEITURA MUNICIPAL DE MARICA" é o nome da unidade). Vem
    do catálogo quando a cidade está nele; senão, "Município de <cidade>"."""
    chaves = _tokens_de_nome(" ".join(cidade))
    catalogo = _catalogo_de_orgaos()
    for nome, tokens in (zip(catalogo.nomes, catalogo.tokens) if catalogo else ()):
        if tokens[:1] == ("municipio",) and list(tokens[1:]) == chaves:
            return nome
    return "Município de " + " ".join(cidade)
# Palavras que acompanham o nome do fornecedor mas não o identificam: fora
# delas, "empresa Cristália" acha "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS
# LTDA" sem precisar citar o nome inteiro.
_GENERICAS_DE_FORNECEDOR = frozenset(
    "ltda epp eireli sa me comercio industria produtos servicos assessoria "
    "representacoes distribuicao importacao exportacao consultoria engenharia "
    "construcoes equipamentos sistemas solucoes participacoes empreendimentos "
    "negocios grupo empresa fornecedor "
    # Palavras de órgão público: sozinhas não nomeiam um fornecedor. Na v7,
    # "Qual órgão, município/UF..." virava fornecedor "MUNICIPIO DE CASIMIRO
    # DE ABREU", o único com "municipio" no nome (A1 e A2 do golden set).
    "municipio municipios prefeitura prefeituras estado estadual governo secretaria "
    "camara fundo uf".split()
)

# Palavra que anuncia que o que vem depois é o nome de um fornecedor. Sem uma
# delas (ou sem nome de duas palavras), não vira filtro: senão "com mais
# frequência" acharia "MED MAIS SOLUCOES..." por causa do "mais".
_GATILHOS_DE_FORNECEDOR = frozenset(
    "empresa fornecedor fornecedora firma comercial companhia sociedade "
    "industria laboratorio clinica hospital cooperativa associacao fundacao "
    "construtora transportadora distribuidora representante orgao".split()
)


def _valor_do_catalogo(opcoes: Sequence[str], mencionados: Sequence[str]) -> Optional[str]:
    """Único valor do catálogo que contém todos os tokens citados.

    Token compara por igualdade depois de normalizar ("Cristália" ==
    "CRISTALIA"), não por prefixo: "contratos" não pode casar com "CONTRA".
    Empate entre valores diferentes ("Rio" casa "Rio de Janeiro" e "Rio das
    Ostras") devolve ``None``: não se escolhe o primeiro arbitrariamente.
    """
    if not mencionados:
        return None
    pedidos = [_normalize(m) for m in mencionados]
    casados = [valor for valor in opcoes
               if all(pedido in _tokens_de_nome(valor) for pedido in pedidos)]
    return casados[0] if len(casados) == 1 else None


# Palavras que só introduzem o nome do município/órgão: vão junto do trecho
# para não sobrarem soltas e virarem outro filtro ("do Município de Maricá"
# não pode achar um fornecedor que também é município).
_INTRODUTORES_DE_NOME = frozenset(
    "de do da dos das em no na nos nas municipio cidade prefeitura estado".split()
)


def _estender_para_tras(texto: str, span: Span) -> Span:
    """Puxa o início do trecho para trás enquanto a palavra anterior introduz nome."""
    inicio, fim = span
    for achado in reversed(list(re.finditer(r"\w+", texto[:inicio]))):
        palavra = _normalize(achado.group())
        if palavra not in _INTRODUTORES_DE_NOME:
            break
        inicio = achado.start()
    return (inicio, fim)


# Palavras que indicam que a menção é ao ESTADO, não ao município: "órgãos
# estaduais do Rio de Janeiro" é recorte estadual, e "Rio de Janeiro" é o nome
# dos dois. Sem isso, o filtro municipal apaga contratos de outros municípios
# fluminenses (os de ambulância, por exemplo, são de Paraty).
_ESCOPO_ESTADUAL = frozenset(
    "estado estadual estaduais estadualmente fluminense fluminenses "
    "poder governos governo".split()
)

# "Rio de Janeiro" é o nome do Estado e da capital. Sem um marcador de cidade,
# a menção é ao Estado ("órgãos públicos do Rio de Janeiro"); tratar como
# município recortaria o Estado inteiro para uma cidade e apagaria contratos de
# outros municípios fluminenses. Só o marcador explícito fecha o recorte.
_MARCADOR_DE_CIDADE = frozenset(
    "municipio municipio cidade prefeitura camara capital bairro municipais".split()
)
_NOME_DUPLICADO_ESTADO_CIDADE = frozenset({"rio de janeiro"})


def _em_escopo_estadual(texto: str, span: Span) -> bool:
    """True quando a menção ao município está num contexto de Estado."""
    palavras_antes = texto[:span[0]].split()[-4:]
    palavras_depois = texto[span[1]:].split()[:3]
    return any(_normalize(p) in _ESCOPO_ESTADUAL for p in palavras_antes + palavras_depois)


def _sem_marcador_de_cidade(texto: str, span: Span, valor: str) -> bool:
    """True quando "Rio de Janeiro" é citado sem sinal de que é a cidade."""
    if _normalize(valor) not in _NOME_DUPLICADO_ESTADO_CIDADE:
        return False
    janela = texto[max(0, span[0] - 40):span[1] + 4]
    return not any(_normalize(p) in _MARCADOR_DE_CIDADE for p in janela.split())


def _extrair_municipio(texto: str) -> tuple[Optional[str], Optional[Span]]:
    """Município do snapshot citado na pergunta, e o trecho que o citou.

    ``texto`` vem normalizado, então os nomes do catálogo também são
    normalizados antes de virar regex ("Maricá" casa com "marica"). Casa a
    sequência de palavras inteiras e fica com o mais longo quando um nome é
    parte do outro. Empate entre municípios diferentes não vira filtro.
    """
    catalogo = _catalogo_de_orgaos()
    if not catalogo or not texto:
        return None, None
    achados: list[tuple[str, Span]] = []
    for municipio in catalogo.municipios:
        palavras = [_normalize(p) for p in municipio.split()]
        padrao = r"(?<!\w)" + r"\W+".join(re.escape(p) for p in palavras) + r"(?!\w)"
        encontrado = re.search(padrao, texto)
        if encontrado:
            achados.append((municipio, encontrado.span()))
    if not achados:
        return None, None
    mais_longo = max(len(_tokens_de_nome(m)) for m, _ in achados)
    finalistas = [(m, s) for m, s in achados if len(_tokens_de_nome(m)) == mais_longo]
    if len({m for m, _ in finalistas}) != 1:
        return None, None  # empate: não escolhe o primeiro
    valor, span = finalistas[0]
    if _em_escopo_estadual(texto, span) or _sem_marcador_de_cidade(texto, span, valor):
        # Menção ao Estado ("órgãos estaduais do Rio de Janeiro"), ou nome
        # duplicado Estado/cidade sem marcador: não se recorta pela cidade.
        return None, None
    return valor, _estender_para_tras(texto, span)


def _extrair_fornecedor(texto: str, bloqueados: Sequence[Span] = ()) -> tuple[Optional[str], Optional[Span]]:
    """Fornecedor do snapshot citado na pergunta, e o trecho que o citou.

    Aceita nome parcial sem as palavras genéricas ("empresa Cristália"). Se
    vários fornecedores casam, não vira filtro. ``bloqueados`` são trechos já
    reconhecidos como outra coisa (o município, por exemplo): sem isso, "do
    Município de Maricá" acharia um fornecedor que também é município.
    """
    catalogo = _catalogo_de_orgaos()
    if not catalogo or not texto:
        return None, None

    def _livre(inicio: int, fim: int) -> bool:
        return not any(comeca < fim and inicio < termina for comeca, termina in bloqueados)

    todas = [(palavra, achado.start(), achado.end()) for achado in re.finditer(r"\w+", texto)
             if (palavra := _normalize(achado.group()))]
    palavras = [(p, i, f) for p, i, f in todas if _livre(i, f)]
    # Só vira filtro com gatilho antes do nome ("empresa Cristália") ou com
    # nome de duas palavras ou mais ("Cristalia Produtos Quimicos").
    for inicio_palavra, (primeira, comeca, termina) in enumerate(palavras):
        if primeira in _GENERICAS_DE_FORNECEDOR or len(primeira) < 4:
            continue
        anteriores = [p for p, i, f in todas if f <= comeca]
        tem_gatilho = bool(anteriores) and anteriores[-1] in _GATILHOS_DE_FORNECEDOR
        citados = [primeira]
        span: Span = (comeca, termina)
        for palavra, pos_inicio, pos_fim in palavras[inicio_palavra + 1: inicio_palavra + 6]:
            if palavra in _GENERICAS_DE_FORNECEDOR:
                continue
            if _valor_do_catalogo(catalogo.fornecedores, citados + [palavra]) is None:
                break
            citados.append(palavra)
            span = (span[0], pos_fim)
        if len(citados) < 2 and not tem_gatilho:
            continue
        valor = _valor_do_catalogo(catalogo.fornecedores, citados)
        if valor is not None:
            return valor, span
    return None, None


def gerar_catalogo_de_orgaos(destino: Path = ORGAOS_CORPUS_PATH) -> dict[str, Any]:
    """Grava o catálogo de órgãos, municípios e fornecedores da base SQL.

    As listas antigas (``nomes``/``siglas``) continuam com o mesmo formato; as
    novas (``municipios``/``fornecedores``) são derivadas do snapshot e trazem
    ``versao``/``fonte_sha256`` para o cache de análise ser invalidado quando a
    base mudar. Só valores não vazios entram.
    """
    from datetime import datetime, timezone

    from src import structured_db

    banco = structured_db.garantir_banco()
    nomes: set[str] = set()
    for tabela in ("contratacoes", "pca_planos"):
        for coluna in ("orgao_nome", "unidade_nome"):
            for linha in banco.consultar(f"SELECT DISTINCT {coluna} AS nome FROM {tabela} WHERE {coluna} IS NOT NULL"):
                nome = " ".join(str(linha["nome"]).split())
                if nome:
                    nomes.add(nome)
    siglas: dict[str, str] = {}
    for nome in sorted(nomes):
        if " " not in nome:
            continue  # nome de uma palavra só ("GERAL") não traz sigla
        for achado in _SIGLA_NO_NOME_RE.finditer(nome):
            sigla = _normalize(achado.group(1) or achado.group(2))
            if sigla not in ("rj", "pr") and sigla not in siglas:
                siglas[sigla] = nome
    # Municípios e fornecedores vêm da mesma base, já normalizados de espaço,
    # preservando a caixa original do valor (o SQL compara por igualdade).
    def _distintos(coluna: str) -> list[str]:
        valores = {" ".join(str(linha["v"]).split())
                   for linha in banco.consultar(
                       f"SELECT DISTINCT {coluna} AS v FROM contratacoes WHERE {coluna} IS NOT NULL")}
        return sorted(v for v in valores if v)

    municipios = _distintos("municipio")
    fornecedores = _distintos("fornecedor")
    # Hash do conteúdo que define os valores: muda o snapshot, muda o hash, e
    # quem guarda cache de análise consegue descartar a versão antiga.
    resumo = hashlib.sha256(
        json.dumps({"nomes": sorted(nomes), "municipios": municipios, "fornecedores": fornecedores},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    dados = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "nomes de órgão e unidade, municípios e fornecedores da base SQL (src/structured_db.py) do corpus",
        "versao": 2,
        "fonte_sha256": resumo,
        "nomes": sorted(nomes),
        "siglas": dict(sorted(siglas.items())),
        "municipios": municipios,
        "fornecedores": fornecedores,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _catalogo_de_orgaos.cache_clear()
    return dados


def _sem_pista_de_maiuscula(tokens: Sequence[str]) -> bool:
    """Pergunta TODA EM MAIÚSCULAS ou Toda Com Inicial Maiúscula: a maiúscula
    não indica nome próprio. Numa frase normal, "de", "da", "em" vêm em
    minúscula, mesmo com muitos nomes próprios."""
    palavras = [t for t in (tok.strip(_PONTUACAO) for tok in tokens) if t[:1].isalpha()]
    if not palavras:
        return True
    if not any(c.islower() for p in palavras for c in p):
        return True
    funcionais = [p for p in palavras if p.lower() in _PALAVRAS_FUNCIONAIS]
    if funcionais:
        return sum(p[0].isupper() for p in funcionais) / len(funcionais) >= 0.5
    return sum(p[0].isupper() for p in palavras) / len(palavras) >= 0.8


def _nome_a_partir_do_gatilho(tokens: Sequence[str], indice: int, sem_caixa: bool) -> Optional[str]:
    def limpa(i: int) -> str:
        return tokens[i].strip(_PONTUACAO) if i < len(tokens) else ""

    def nome_proprio(palavra: str) -> bool:
        # Inicial maiúscula numa frase normal; SIGLA toda em maiúscula não conta.
        return not sem_caixa and palavra[:1].isupper() and not (len(palavra) > 1 and palavra.isupper())

    gatilho = _normalize(limpa(indice))
    palavras = [limpa(indice)]
    chaves = [_SINONIMOS_DE_ORGAO.get(gatilho, gatilho)]
    cursor = indice + 1
    while cursor < len(tokens) and _normalize(limpa(cursor)) in _QUALIFICADORES_DE_ORGAO:
        palavras.append(limpa(cursor))
        chaves.append(_normalize(limpa(cursor)))
        cursor += 1
    inicio_do_nome = len(chaves)

    def confere(nome: Sequence[str]) -> Optional[str]:
        """O nome existe no corpus? Para prefeitura, bastam as palavras da
        cidade num órgão municipal ("Câmara Municipal de São Gonçalo"); para
        cabeças fortes, as palavras do nome; para as demais ("câmara",
        "fundo", "comando"), a cabeça também tem de estar no nome."""
        if gatilho in ("prefeitura", "municipio"):
            return _orgao_do_catalogo(nome, exigir_algum=("municipio", "municipal", "prefeitura"))
        if gatilho in _GATILHOS_FORTES:
            return _orgao_do_catalogo(nome)
        return _orgao_do_catalogo(list(chaves[:inicio_do_nome]) + list(nome))

    com_maiuscula: list[bool] = []
    parou_em_palavra_colada = False
    antes_do_municipio: Optional[list[str]] = None
    cidade: Optional[list[str]] = None
    while cursor < len(tokens):
        if tokens[cursor - 1][-1:] in _PONTUACAO or tokens[cursor][:1] in "(\"'“‘":
            break  # vírgula, ponto, aspas ou "(SES)" encerram o nome
        palavra = limpa(cursor)
        normal = _normalize(palavra)
        if not palavra or normal in _FIM_DO_NOME_DO_ORGAO:
            break
        if normal in ("de", "do", "da") and (tamanho := _municipio_em(tokens, cursor + 1)):
            # Município do RJ: entra inteiro e confirmado ("de são gonçalo", "de maricá").
            if antes_do_municipio is None:
                antes_do_municipio = list(chaves[inicio_do_nome:])
                cidade = [limpa(i) for i in range(cursor + 1, cursor + 1 + tamanho)]
            palavras += [palavra] + [limpa(i) for i in range(cursor + 1, cursor + 1 + tamanho)]
            chaves += [_normalize(limpa(i)) for i in range(cursor + 1, cursor + 1 + tamanho)]
            com_maiuscula.append(True)
            cursor += 1 + tamanho
            continue
        if normal in _PALAVRA_CONECTORA:
            seguinte = limpa(cursor + 1)
            normal_seguinte = _normalize(seguinte)
            if (
                not seguinte
                or not seguinte[0].isalpha()
                or normal_seguinte in _NAO_SAO_NOME
                or normal_seguinte in _PALAVRA_CONECTORA
                or (normal == "e" and normal_seguinte in _GATILHOS_ORGAO)
            ):
                break  # "de 2025", "de qual", "e da Prefeitura", "e o valor"
            novas_palavras, novas_chaves, passo = [palavra, seguinte], [normal_seguinte], 2
        elif (
            palavra[:1].isalpha()
            and normal not in _GATILHOS_ORGAO
            and normal not in _NAO_SAO_NOME
            and len(chaves) > inicio_do_nome or nome_proprio(palavra)
        ) and normal not in _GATILHOS_ORGAO:
            # Palavra colada, sem conectora: "Prefeitura Niterói", "são gonçalo".
            novas_palavras, novas_chaves, passo = [palavra], [normal], 1
        else:
            break
        fim = cursor + passo
        if novas_chaves[-1] == "rio" and [_normalize(limpa(fim)), _normalize(limpa(fim + 1))] == ["de", "janeiro"]:
            novas_palavras += [limpa(fim), limpa(fim + 1)]  # "Rio de Janeiro" é um nome só
            novas_chaves.append("janeiro")
            fim += 2
        # Cresce se ainda corresponde a um órgão do corpus, ou se é nome próprio.
        if not (confere(chaves[inicio_do_nome:] + novas_chaves) or nome_proprio(novas_palavras[-1])):
            parou_em_palavra_colada = passo == 1
            break
        palavras += novas_palavras
        chaves += novas_chaves
        com_maiuscula.append(nome_proprio(novas_palavras[-1]))
        cursor = fim
    if gatilho in ("prefeitura", "municipio") and antes_do_municipio is None and chaves[inicio_do_nome:] == ["rio"]:
        return _orgao_do_municipio(["Rio", "de", "Janeiro"])  # "Prefeitura do Rio"
    if antes_do_municipio is not None:
        # "Prefeitura (Municipal) de X", "Prefeitura da Cidade do X", "Município
        # de X": o órgão é gravado como "MUNICIPIO DE X", e o filtro "contém"
        # não casaria "Prefeitura de Maricá" com ele.
        if gatilho in ("prefeitura", "municipio") and antes_do_municipio in ([], ["cidade"]) and cidade:
            return _orgao_do_municipio(cidade)
        # Com município: vale para prefeitura/câmara/cabeça forte, ou se o que
        # vem antes da cidade existe no corpus ("fundo municipal de saúde").
        # "banco de sangue de niterói" não.
        if (
            gatilho in ("prefeitura", "municipio", "camara")
            or gatilho in _GATILHOS_FORTES
            or not antes_do_municipio
            or confere(antes_do_municipio)
            or all(com_maiuscula)
        ):
            return " ".join(palavras)
        return None
    if parou_em_palavra_colada and chaves[-1] in _COMECOS_DE_NOME_COMPOSTO:
        return None  # "prefeitura de são" + palavra que não deu para confirmar: nome pela metade
    if chaves[-1] in _CONTINUA_SEM_CATALOGO and _normalize(limpa(cursor)) in ("de", "do", "da"):
        # "secretaria de estado" + "de educação" que não está no corpus: nome incompleto.
        return _nome_sem_catalogo(tokens, indice) if gatilho in _GATILHOS_FORTES else None
    if len(chaves) > inicio_do_nome and (confere(chaves[inicio_do_nome:]) or all(com_maiuscula)):
        return " ".join(palavras)
    if len(chaves) == inicio_do_nome > 1 and gatilho in _GATILHOS_FORTES and all(nome_proprio(p) for p in palavras[1:]):
        return " ".join(palavras)  # "Defensoria Pública", "Procuradoria Geral"
    if gatilho in _GATILHOS_FORTES:
        return _nome_sem_catalogo(tokens, indice)
    return None  # "órgão federal", ou nome em minúsculas que não existe no corpus: "banco de sangue"


# Os 92 municípios do RJ: depois de "de/do/da", entram inteiros no nome do
# órgão, com ou sem maiúscula ("fundo municipal de saúde de maricá",
# "prefeitura de são gonçalo"), mesmo que o órgão não esteja no corpus.
_MUNICIPIOS_RJ = tuple(
    sorted(
        (
            tuple(_normalize(nome).split())
            for nome in """
            Angra dos Reis|Aperibé|Araruama|Areal|Armação dos Búzios|Arraial do Cabo|Barra do Piraí|Barra Mansa
            |Belford Roxo|Bom Jardim|Bom Jesus do Itabapoana|Cabo Frio|Cachoeiras de Macacu|Cambuci
            |Campos dos Goytacazes|Cantagalo|Carapebus|Cardoso Moreira|Carmo|Casimiro de Abreu
            |Comendador Levy Gasparian|Conceição de Macabu|Cordeiro|Duas Barras|Duque de Caxias
            |Engenheiro Paulo de Frontin|Guapimirim|Iguaba Grande|Itaboraí|Itaguaí|Italva|Itaocara|Itaperuna
            |Itatiaia|Japeri|Laje do Muriaé|Macaé|Macuco|Magé|Mangaratiba|Maricá|Mendes|Mesquita|Miguel Pereira
            |Miracema|Natividade|Nilópolis|Niterói|Nova Friburgo|Nova Iguaçu|Paracambi|Paraíba do Sul|Paraty
            |Paty do Alferes|Petrópolis|Pinheiral|Piraí|Porciúncula|Porto Real|Quatis|Queimados|Quissamã|Resende
            |Rio Bonito|Rio Claro|Rio das Flores|Rio das Ostras|Rio de Janeiro|Santa Maria Madalena
            |Santo Antônio de Pádua|São Fidélis|São Francisco de Itabapoana|São Gonçalo|São João da Barra
            |São João de Meriti|São José de Ubá|São José do Vale do Rio Preto|São Pedro da Aldeia
            |São Sebastião do Alto|Sapucaia|Saquarema|Seropédica|Silva Jardim|Sumidouro|Tanguá|Teresópolis
            |Trajano de Moraes|Três Rios|Valença|Varre-Sai|Vassouras|Volta Redonda
            """.replace("\n", "").split("|")
            if nome.strip()
        ),
        key=len,
        reverse=True,
    )
)


def _municipio_em(tokens: Sequence[str], inicio: int) -> int:
    """Quantos tokens a partir de ``inicio`` formam um município do RJ (0 se nenhum)."""
    for municipio in _MUNICIPIOS_RJ:
        trecho = [_normalize(t.strip(_PONTUACAO)) for t in tokens[inicio: inicio + len(municipio)]]
        if tuple(trecho) == municipio:
            return len(municipio)
    return 0


# Primeira palavra de nome composto de cidade: parar nela corta o nome ao meio.
_COMECOS_DE_NOME_COMPOSTO = frozenset(
    "sao santa santo nova novo barra volta tres duque angra campos cabo bom porto paty "
    "engenheiro casimiro armacao silva sapucaia miguel conceicao".split()
)
# Cabeças de nome que quase nunca são substantivo comum: sem catálogo, o nome
# ainda é lido, mas só um nível ("procuradoria geral do estado").
_GATILHOS_FORTES = frozenset(
    "secretaria ministerio tribunal defensoria procuradoria controladoria policia assembleia companhia autarquia".split()
)
_CONTINUA_SEM_CATALOGO = frozenset({"estado", "governo"})


def _nome_sem_catalogo(tokens: Sequence[str], indice: int) -> Optional[str]:
    def limpa(i: int) -> str:
        return tokens[i].strip(_PONTUACAO) if i < len(tokens) else ""

    palavras = [limpa(indice)]
    cursor = indice + 1
    while cursor < len(tokens) and _normalize(limpa(cursor)) in _QUALIFICADORES_DE_ORGAO:
        palavras.append(limpa(cursor))
        cursor += 1
    niveis = 0
    while cursor + 1 < len(tokens) and niveis < 2:
        if tokens[cursor - 1][-1:] in _PONTUACAO or tokens[cursor][:1] in "(\"'“‘":
            break
        conectora, palavra = _normalize(limpa(cursor)), limpa(cursor + 1)
        normal = _normalize(palavra)
        if (
            conectora not in ("de", "do", "da", "dos", "das")
            or not palavra[:1].isalpha()
            or normal in _NAO_SAO_NOME
            or normal in _PALAVRA_CONECTORA
            or normal in _GATILHOS_ORGAO
        ):
            break
        palavras += [limpa(cursor), palavra]
        cursor += 2
        if normal == "rio" and [_normalize(limpa(cursor)), _normalize(limpa(cursor + 1))] == ["de", "janeiro"]:
            palavras += [limpa(cursor), limpa(cursor + 1)]
            cursor += 2
        niveis += 1
        if _normalize(limpa(cursor)) in ("de", "do", "da") and (tamanho := _municipio_em(tokens, cursor + 1)):
            # "secretaria municipal de saúde de niterói": o município entra.
            palavras += [limpa(i) for i in range(cursor, cursor + 1 + tamanho)]
            break
        if normal not in _CONTINUA_SEM_CATALOGO and normal != "rio":
            # "secretaria de ciência e tecnologia": um "e" + palavra ainda entra.
            seguinte = _normalize(limpa(cursor + 1))
            if (
                _normalize(limpa(cursor)) == "e"
                and limpa(cursor + 1)[:1].isalpha()
                and tokens[cursor - 1][-1:] not in _PONTUACAO
                and seguinte not in _NAO_SAO_NOME
                and seguinte not in _PALAVRA_CONECTORA
                and seguinte not in _GATILHOS_ORGAO
                and seguinte not in _VERBOS_COMUNS_DEPOIS_DO_NOME
            ):
                palavras += [limpa(cursor), limpa(cursor + 1)]
            break  # "secretaria de saúde", não "secretaria de saúde de ambulâncias"
    return " ".join(palavras) if len(palavras) > 1 and niveis else None


# Depois de "nome e", palavras que começam outra oração: "secretaria de saúde e
# informe", "e compare", "e liste".
_VERBOS_COMUNS_DEPOIS_DO_NOME = frozenset(
    "informe informar compare comparar liste listar mostre mostrar diga traga some calcule qual quais quanto".split()
)


def _sigla_de_orgao(tokens: Sequence[str]) -> tuple[Optional[str], Optional[str]]:
    """(nome do órgão, sigla como está no texto normalizado) para SEEDUC, TCE-RJ, FESAUDE..."""
    catalogo = _catalogo_de_orgaos()
    for bruto in tokens:
        token = bruto.strip(_PONTUACAO)
        for parte in re.split(r"[-/]", token):
            normal = _normalize(parte)
            if normal in _SIGLAS_DE_ORGAO:
                return _SIGLAS_DE_ORGAO[normal], _normalize(token)
            if catalogo and parte.isupper() and len(parte) >= 2 and normal in catalogo.siglas:
                return catalogo.siglas[normal], _normalize(token)
    return None, None


def _extrair_orgao(query_original: str) -> tuple[Optional[str], Optional[str]]:
    """Nome do órgão citado na pergunta, e a sigla usada (ou None).

    Começa numa palavra de início de nome ("secretaria", "prefeitura",
    "fundo"...), com ou sem maiúscula, e segue por adjetivos ("municipal") e
    por "de/do/da/dos/das/e" + nome, enquanto o nome corresponde a um órgão do
    corpus ou é nome próprio. Sem gatilho, aceita sigla conhecida (SEEDUC,
    SES, DPE...) ou que apareça no nome de um órgão do corpus (FESAUDE).
    """
    tokens = query_original.split()
    sem_caixa = _sem_pista_de_maiuscula(tokens)
    for indice, bruto in enumerate(tokens):
        gatilho = _normalize(bruto.strip(_PONTUACAO))
        if gatilho not in _GATILHOS_ORGAO:
            continue
        anterior = _normalize(tokens[indice - 1].strip(_PONTUACAO)) if indice else ""
        if gatilho in {"municipio", "cidade"} and anterior in {"no", "na", "em", "nos", "nas"}:
            # "no município do Rio" é dimensão territorial; não o órgão
            # MUNICIPIO DE RIO DE JANEIRO.
            continue
        depois = [_normalize(t.strip(_PONTUACAO)) for t in tokens[indice + 1: indice + 3]]
        if gatilho in _GATILHO_EXIGE and (not depois or depois[0] not in _GATILHO_EXIGE[gatilho]):
            continue
        if len(depois) == 2 and depois[0] in _PALAVRA_CONECTORA and (gatilho, depois[1]) in _NAO_E_ORGAO:
            continue
        nome = _nome_a_partir_do_gatilho(tokens, indice, sem_caixa)
        if nome:
            return nome, None
    nome, sigla = _sigla_de_orgao(tokens)
    if nome:
        return nome, sigla
    # Fallback: palavras de gatilho não formaram nome completo, mas o corpus
    # pode ter o órgão. Ex.: "BANCO CENTRAL DO BRASIL" — "central" não era
    # qualificador, então o nome parou em "banco". Tenta com os tokens a
    # partir do gatilho.
    for indice, bruto in enumerate(tokens):
        gatilho = _normalize(bruto.strip(_PONTUACAO))
        if gatilho not in _GATILHOS_ORGAO:
            continue
        palavras = [_normalize(t.strip(_PONTUACAO)) for t in tokens[indice:] if _normalize(t.strip(_PONTUACAO)) not in _NAO_SAO_NOME]
        nome = _orgao_do_catalogo(palavras)
        if nome:
            return nome, None
    return None, None


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


# CNPJ com ou sem pontuação. Os 14 dígitos no começo de um número de controle
# do PNCP ("00394429000100-2-001108/2024") não são CNPJ citado: ficam de fora
# pelo "-" depois deles.
_CNPJ_RE = re.compile(
    r"(?:\bcnpjs?\s*(?:n[o.]?\s*|:\s*)?)?"
    r"(?<![\d./-])(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?![\d/-])"
)


def _extrair_cnpjs(texto: str) -> tuple[list[str], list[Span]]:
    """CNPJs citados (só dígitos, sem repetição) e os trechos onde aparecem."""
    cnpjs, trechos = [], []
    for match in _CNPJ_RE.finditer(texto):
        digitos = "".join(match.groups())
        if digitos not in cnpjs:
            cnpjs.append(digitos)
        trechos.append(match.span())
    return cnpjs, trechos


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
    # Métrica, agrupamento e referência saem do texto sem as expressões que já
    # viraram filtro: em "no máximo 50 mil" o "máximo" é filtro, não métrica.
    resto = _sem_trechos(texto, trechos)
    media_unitaria = bool(re.search(r"\b(?:media|medio)\b.*\b(?:por|unidade|item)\b", resto))
    if media_unitaria:
        metrica = "media_unitaria"
    else:
        metrica = next((valor for padrao, valor in (
            (r"\bmediana\b", "mediana"), (r"\b(?:media|medio)\b", "media"),
            (r"\b(?:soma|total)\b", "soma"), (r"\bvalor\b", "soma"), (r"\b(?:quantos|quantas|quantidade)\b", "contagem"),
            (r"\b(?:maior|maiores|maximo)\b", "maximo"), (r"\b(?:menor|menores|minimo)\b", "minimo"),
        ) if re.search(padrao, resto)), None)
    agrupamentos = [campo for termo, campo in (
        ("modalidade", "modalidade"), ("municipio", "municipio"), ("orgao", "orgao_nome"),
        ("unidade", "unidade_nome"), ("categoria", "categoria"), ("classe", "classe"),
        ("esfera", "esfera"), ("fornecedor", "fornecedor"),
    ) if re.search(r"\bpor " + termo, resto)]
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
        referencia = "vigencia"
    analiticos: dict[str, str] = {}
    # Esfera e modalidade saem do texto sem filtros e sem citações de norma
    # ("Lei federal 14.133" não é esfera federal; "Lei do Pregão" não é
    # modalidade). "Por município" pede agrupamento, não filtro de esfera.
    sem_normas = _NORMA_COM_ESFERA_RE.sub(" ", _sem_trechos(texto, trechos + bloqueados))
    sem_agrupamento = re.sub(r"\bpor\s+(?:municipios?|orgaos?|esferas?)\b", " ", sem_normas)
    esferas = [nome for nome, padrao in _ESFERAS if re.search(padrao, sem_agrupamento)]
    if len(esferas) == 1:  # várias juntas = comparação entre esferas
        analiticos["esfera"] = esferas[0]
    sem_sentido_comum = _SENTIDO_COMUM_DE_MODALIDADE_RE.sub(" ", sem_normas)
    achadas = [(m, nome) for padrao, nome in _MODALIDADES if (m := re.search(padrao, sem_sentido_comum))]
    familias = {nome.split()[0] for _, nome in achadas}
    orgao, sigla = _extrair_orgao(query)
    # Município e fornecedor viram o valor canônico do snapshot ("Volta
    # Redonda", "empresa Cristália"), e o trecho que os citou sai dos termos de
    # objeto. O fornecedor não olha dentro do que já é município/órgão.
    municipio, trecho_municipio = _extrair_municipio(texto)
    fornecedor, trecho_fornecedor = _extrair_fornecedor(
        texto, [s for s in (trecho_municipio,) if s])
    if municipio and not (orgao and _normalize(municipio) in _normalize(orgao)):
        # "Prefeitura de Maricá" já prende no órgão: não repetir o município,
        # que sozinho traria os 23 órgãos da cidade em vez da prefeitura.
        analiticos["municipio"] = municipio
    if fornecedor:
        analiticos["fornecedor"] = fornecedor
    trechos = trechos + [s for s in (trecho_municipio, trecho_fornecedor) if s]
    if len(familias) == 1:  # "pregões e dispensas" juntos é comparação: sem filtro
        achada, nome = achadas[0]
        plural = achada.group().split()[0].endswith("s")
        negada = re.search(r"\bnao\b(?:\s+\w+){0,3}\s*$", sem_sentido_comum[: achada.start()])
        if not negada and (
            _PERGUNTA_SOBRE_CONTRATOS_RE.search(sem_sentido_comum)
            or (plural and (_PERGUNTA_COM_CONTA_RE.search(sem_sentido_comum) or orgao))
        ):
            analiticos["modalidade"] = nome
    if len(cnpjs) == 1:  # vários juntos é comparação: sem filtro
        analiticos["cnpj"] = cnpjs[0]
    if sigla:
        # "contratos da SEEDUC": o órgão vira o nome por extenso, e a sigla sai
        # do texto para não ser lida como objeto do contrato.
        trechos = trechos + [m.span() for m in re.finditer(rf"\b{re.escape(sigla)}\b", texto)]
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
    filtros = QueryFilters(
        ano=periodo.ano,
        ano_final=periodo.ano_final,
        ano_desde=periodo.ano_desde,
        ano_ate=periodo.ano_ate,
        valor_min=valores.valor_min,
        valor_max=valores.valor_max,
        orgao=orgao,
        metrica=metrica,
        agrupamentos=agrupamentos,
        referencia_temporal=referencia,
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
        # início da vigência vem de src/qualidade_corpus.py (só a base
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
