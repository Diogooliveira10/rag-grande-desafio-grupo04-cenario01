"""Leitura de valores e períodos na pergunta: regras puras, sem banco nem LLM.

"acima de R$ 500 mil", "entre 1 e 2 milhões", "a partir de 2024", "em março de
2025", "nos últimos dois anos": cada expressão vira um filtro (valor mínimo,
valor máximo, ano, faixa de anos) e o trecho que a citou, para sair dos termos
do objeto. Não lê dado nenhum: só o texto da pergunta, já normalizado.

Movido de src/query_analysis.py em 2026-10-24 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence


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
