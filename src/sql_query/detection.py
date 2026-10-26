"""Que conta a pergunta pede à base estruturada: tipo, tabela e filtros.

``detectar_consulta`` (caminho por regras) e ``consulta_da_intencao`` (plano do
LLM) devolvem a ``Consulta``: ranking, listagem, estatística, agrupamento,
evolução, CNPJ do fornecedor ou um registro pelo número de controle. Não toca o
banco: só lê a pergunta e os filtros já extraídos.

Movido de src/structured_query.py em 2026-10-25 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from src import structured_db
from src.domain import purchase_status
from src.query_analysis import texto_sem_filtros
from src.schema import Natureza, QueryFilters, QueryIntent


LIMITE_RANKING = 10


_NUMEROS_POR_EXTENSO = {
    "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30, "cinquenta": 50,
}


_RE_PCA = re.compile(r"\bpca\b|\bplanos? (?:anua(?:l|is) )?de contratac|\bplanos? anua(?:l|is)\b|\bplanejad")


_RE_CONTRATOS = re.compile(r"\bcontrat(?:o|os|ad[oa]s?|acao|acoes|ou|aram)\b")


_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


# "Rio de Janeiro" não é o mês de janeiro.
_RE_MES = re.compile(r"(?<!\brio de )\b(" + "|".join(_MESES) + r")\b")


# Modalidade é coluna, não objeto: (padrão na pergunta, termos que a modalidade
# gravada precisa conter, sem acento). A mais específica vem antes.
_MODALIDADES = (
    (re.compile(r"\bpreg(?:ao|oes) eletronicos?\b"), ("pregao", "eletronic")),
    (re.compile(r"\bpreg(?:ao|oes) presencia(?:l|is)\b"), ("pregao", "presencial")),
    (re.compile(r"\bpregao\b|\bpregoes\b"), ("pregao",)),
    (re.compile(r"\bdispensas?\b"), ("dispensa",)),
    (re.compile(r"\binexigibilidade|\binexigive"), ("inexigibilidade",)),
    (re.compile(r"\bconcorrencias?\b"), ("concorrencia",)),
    (re.compile(r"\bleil(?:ao|oes)\b"), ("leilao",)),
    (re.compile(r"\bcredenciamentos?\b"), ("credenciamento",)),
    (re.compile(r"\bdialogo competitivo\b"), ("dialogo",)),
)


_ESFERAS = (("estadual", r"\bestadua"), ("municipal", r"\bmunicip"), ("federal", r"\bfedera"))


# Palavras do nome do órgão que não ajudam a distinguir um órgão de outro. "Estado",
# "Rio" e "Janeiro" ficam: sem elas, "Universidade do Estado do Rio de Janeiro"
# vira "universidade" e "Prefeitura do Rio de Janeiro" vira "prefeitura".
_PALAVRAS_ORGAO_VAZIAS = frozenset("de da do das dos e no na nos nas em pca plano planos".split())


# Citação de norma não é esfera nem modalidade: "Lei federal 14.133", "Lei do Pregão".
# Pedido que a lista de registros não responde sozinha: julgamento, explicação,
# suficiência, norma. Com ele, a listagem vai ao LLM com o resultado SQL.
_RE_PEDIDO_ALEM_DA_LISTA = re.compile(
    r"\b(?:suficientes?|insuficien\w*|conclu\w*|predomin\w*|por que|porque|expli\w*|analis\w*|"
    r"avali\w*|interpret\w*|tendenc\w*|padr(?:ao|oes)|riscos?|recomend\w*|deveri\w*|irregular\w*|"
    r"regras?|normas?|leis?|decretos?|justifi\w*|motivos?|razo(?:es)?|razao|significa\w*|"
    r"semelhan\w*|diferen\w*|resum\w*|explicit\w*)\b"
)


_RE_NORMA_NUMERADA = re.compile(
    r"\b(?:lei|decreto|portaria|resolucao|instrucao normativa)(?:\s+complementar)?"
    r"(?:\s+(?:federal|estadual|municipal))?(?:\s+n[o.]?)?\s+\d|\blei do pregao\b"
)


# Pagamento citado na pergunta: o PNCP só traz valor contratado/informado, e o
# resultado avisa que o número não é o que foi pago.
_RE_PAGAMENTO = re.compile(r"\bpag(?:o|os|a|as|ou|aram|amentos?)\b")


_RE_CITACAO_DE_NORMA = re.compile(
    r"\b(?:lei|decreto|portaria|resolucao|instrucao normativa)\s+(?:complementar\s+)?"
    r"(?:federal|estadual|municipal)\b|\blei do pregao\b"
)


_RE_RANKING = re.compile(r"\b(?:maior(?:es)?|menor(?:es)?|top|ranking)\b")


_RE_N_RANKING = re.compile(r"\b(\d{1,2}|" + "|".join(_NUMEROS_POR_EXTENSO) + r")\s+(?:maiores|menores)\b")


_RE_EVOLUCAO = re.compile(r"\bao longo\b|\bevolu|\baument|\bcresc|\bqueda\b|\bpor mes\b|\bmensa")


_RE_AGRUPAMENTO = re.compile(
    r"\bmais frequen|\bmais usad|\bmais comu|\bpor modalidades?\b|\bpor orgaos?\b|\bpor municipios?\b|\bpor esferas?\b"
)


# "Quais modalidades aparecem nos contratos de X?" (P4.2) ia à busca semântica,
# que trazia contratos de outro assunto. Vale só sem norma na pergunta: "que
# modalidades a Lei 14.133 prevê?" é pergunta da base normativa.
_RE_QUAIS_MODALIDADES = re.compile(r"\b(?:quais|que) (?:as )?modalidades\b")


# "Somaram/gastaram/receberam mais do que" também compara (V5 do golden set).
_RE_COMPARACAO = re.compile(r"\bmais car[oa]s?\b|\bmais barat|\bpag(?:ou|aram|am|a) mais\b|\bcompar"
                            r"|\b(?:som(?:ou|aram|am|a)|gast(?:ou|aram|am|a)|receb(?:eu|eram|em|e)|"
                            r"contrat(?:ou|aram|am|a)) mais\b")


# Sem "\bvalor\b": pedir para *informar* um valor ("informe o valor contratado")
# é listar, não calcular. Só conta como cálculo a palavra que declara a conta
# (média, mediana, total, soma, quanto, quantidade). Métrica sem a pergunta
# pedir é extrapolação — ver P4 do plano.
# "Valor de todos os contratos" e "somados" também pedem a soma (sem a palavra "total").
_RE_ESTATISTICA = re.compile(r"\bmedia\b|\bmedio\b|\bmediana\b|\btotal\b|\bsoma\b|\bquant[oa]s?\b|\bquantidade\b|\bmaior(es)?\b|\bmenor(es)?\b"
                             r"|\bvalor(?:es)?\s+(?:de\s+)?tod[oa]s\b|\bsomad[oa]s?\b")


_RE_CONTAGEM_CONTRATOS = re.compile(
    r"\b(?:(?P<numero>\d+|um|dois|tres|quatro|cinco)\s+ou\s+mais|"
    r"(?:pelo\s+menos|no\s+minimo)\s+(?P<minimo>\d+|um|dois|tres|quatro|cinco))"
    r"\s+(?:contratos?|contratac(?:ao|oes))\b"
)


_RE_AGENTE = re.compile(r"\b[a-z]+(?:ad|id|it)[oa]s? por (?:orgaos|municipios|esferas)\b")


# Pergunta que pede os registros em si: "quais órgãos contrataram…", "existem contratações de…".
# Também a frase nominal "Contratos acima de 50 milhões em 2025", sem verbo:
# ia ao LLM, levava 50 s e saía com os valores mal formatados.
_RE_LISTAGEM = re.compile(
    r"^(?:quais|qual|existem?|ha|houve|liste|listar|mostre|mostrar|contratos|contratacoes)\b")


_RE_LISTAGEM_CONTRATACOES = re.compile(
    r"\b(?:quais|qual|liste|listar|mostre|mostrar)\s+contrat(?:a(?:cao|coes)|os?)\b"
)


_RE_OBJETO_CONTRATACAO = re.compile(
    r"\bcontrat(?:a(?:cao|coes)|os?)\s+de\s+(.+?)"
    r"(?=\s+(?:do corpus|da base|que|usam|utilizam|foram|sao|existem|em)\b|[,;?.!]|$)"
)


_RE_PEDIDO_DE_CNPJ = re.compile(r"(?:\b(?:qual|quais|informe|mostre|diga|me de)\b.*|^)\bcnpjs?\b")


# Pedido de CNPJ junto de uma conta ("quantos contratos e qual o CNPJ") segue
# o caminho da conta.
_RE_CNPJ_COM_CONTA = re.compile(r"\b(?:quantos|quantas|quanto|valor|total|soma|media|maior|maiores|ranking)\b")


_RE_NUMERO_CONTROLE_PNCP = re.compile(
    r"(?<!\d)\d{14}-\d-\d{6}/\d{4}(?:-\d{6})?(?!\d)"
)


def requer_calculo(query: str) -> bool:
    """Identifica contas que uma amostra recuperada não pode comprovar."""
    texto = structured_db.normalizar(query)
    return bool(
        _RE_ESTATISTICA.search(texto)
        or _RE_CONTAGEM_CONTRATOS.search(texto)
        or _RE_RANKING.search(texto)
        or re.search(r"\bmais frequen|\bmais usad|\bmais comu", texto)
        or _RE_EVOLUCAO.search(texto)
        or (_RE_COMPARACAO.search(texto) and re.search(r"valor|preco|pag|custo", texto))
    )


# Palavras que não descrevem o objeto contratado; o que sobra vira filtro de objeto.
_PALAVRAS_GENERICAS = frozenset(
    """
    qual quais quanto quantos quantas foram foi sao seja estao tiveram tem teve houve existem existe
    pelos pelas pelo pela para como onde quando entre sobre desde durante com sem mais menos cada
    todos todas algum alguma alguns algumas esse essa esses essas este esta estes estas isso aquele
    maior maiores menor menores media medio mediana total soma valor valores preco precos
    contrato contratos contratacao contratacoes contratado contratada contratados contratadas contratou contrataram
    licitacao licitacoes compra compras registrad registrada registradas registrados publicad publicada publicadas
    orgao orgaos estado estados estadual estaduais municipio municipios municipal municipais esfera
    publico publicos publica publicas governo prefeitura prefeituras secretaria secretarias
    fluminense fluminenses janeiro dados conjunto periodo anos modalidade modalidades frequencia frequente frequentes
    apareceram aparecem encontrad encontrada encontradas encontrados informe informar mostre liste lista
    ranking longo aumento evolucao crescimento queda mensal plano planos anual planejado planejada pca
    tipo tipos corpus base bases numero fornecedor fornecedores empresa empresas receberam recebeu
    usado usada usadas usados comum comuns pagaram pagou pago paga pagos caro cara caros barato
    publicado publicados assinado assinada assinados assinadas assinatura assinaturas vigente vigentes vigencia
    anulada anulado anuladas anulados cancelada cancelado canceladas cancelados
    revogada revogado revogadas revogados suspensa suspenso suspensas suspensos situacao situacoes
    incluindo inclusive ativas validas validos
    compare comparar compara comparado comparados comparacao data datas federal federais uniao nacional
    fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
    semestre trimestre existem quantidade registro registros realizado realizadas realizados
    servico servicos aquisicao aquisicoes fornecimento prestacao especializada especializado
    informacao informacoes detalhe detalhes firmaram firmou celebraram disponiveis disponivel
    comparando publicando assinando somando informando mostrando listando buscando calculando
    continuacao anterior pergunta principal principais
    evidencia evidencias insuficiencia suficiencia reconheca reconhecido reconhecimento
    suficiente suficientes tecnica tecnicas equivalente equivalentes tecnicamente
    disponivel disponiveis apenas comprovem comprovado comprovada
    assinou assinaram contratou contrataram comprou compraram gastou gastaram gasto gastos despesa despesas
    concluir conclusao predominante predominantes predomina predominancia encontrados encontradas
    publicou publicaram custou custaram custam custa custaria
    seus suas dele dela deles delas respectivo respectiva respectivos respectivas
    seguem segue seguindo base baseado baseada baseados baseadas fundamentado fundamentada fundamentados
    fundamentadas fundamento amparo amparado amparada amparados amparadas conforme lei leis decreto decretos
    portaria portarias norma normas
    apenas somente considerando considerada considerado consideradas considerados desse dessa desses dessas
    nesse nessa nesses nessas neste nesta nestes nestas deste desta destes destas aqui estiver estiverem
    feita feito feitas feitos ligado ligada ligados ligadas devem deve devia podem pode poderia saber
    tratam trata tratar cite citar citem mostram mostra mostrar informe tecnicamente tecnica tecnicas
    equivalente equivalentes especificacao especificacoes edital editais regra regras norma normas
    lei leis documento documentos usam usou usaram compativel compativeis participacao participar
    encontrou encontraram sendo tendo havia haviam ainda tambem muito muitos muita muitas outro outra
    outros outras mesmo mesma mesmos mesmas qualquer quaisquer algo nenhum nenhuma cujo cuja cujos cujas
    """.split()
)


@dataclass(frozen=True)
class Consulta:
    """Uma consulta estruturada já decidida: o que calcular, em qual tabela, com quais filtros."""

    tabela: str  # "contratacoes" | "pca_planos"
    tipo: str  # "ranking" | "estatistica" | "evolucao" | "agrupamento"
    ordem: str = "DESC"
    limite: int = LIMITE_RANKING
    agrupar_por: str = "modalidade"
    # Período fechado: ``ano`` até ``ano_final`` (None = só ``ano``). Períodos
    # abertos ("a partir de 2024", "até 2025") vão em ``ano_desde`` / ``ano_ate``.
    ano: Optional[int] = None
    ano_desde: Optional[int] = None
    ano_ate: Optional[int] = None
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    esfera: Optional[str] = None
    termos_objeto: tuple[str, ...] = field(default_factory=tuple)
    dataset: Optional[str] = None
    versao: Optional[str] = None
    metrica: str = "contagem"
    campo: Optional[str] = None
    agrupamentos: tuple[str, ...] = field(default_factory=tuple)
    referencia_temporal: Optional[str] = None
    ano_final: Optional[int] = None
    filtros_analiticos: tuple[tuple[str, Any], ...] = field(default_factory=tuple)
    papel_orgao: Optional[str] = None
    papel_cnpj: Optional[str] = None
    valor_min_exclusivo: bool = False
    minimo_por_grupo: Optional[int] = None
    # Mês (1-12) dentro do ano da consulta: "média de janeiro de 2025".
    mes: Optional[int] = None
    # Termos (sem acento) que a modalidade gravada precisa conter, e os valores
    # da coluna que casaram (resolvidos contra o banco em executar_consulta).
    modalidade_termos: tuple[str, ...] = field(default_factory=tuple)
    modalidades: Optional[tuple[str, ...]] = None
    # Palavras do órgão pedido e os nomes de órgão/unidade do banco que casaram.
    orgao_termos: tuple[str, ...] = field(default_factory=tuple)
    orgaos: Optional[tuple[str, ...]] = None
    # A pergunta fala de contratos: só os contratos assinados, mesmo quando o
    # ano é o da publicação ("contratos publicados em março").
    so_contratos: bool = False
    # Compra publicada e contrato assinado da mesma contratação contam uma vez
    # (resolvido em _resolver_valores: exige a coluna numero_controle_compra).
    deduplicar: bool = False
    # A pergunta cita lei/decreto ("contratos que seguem a Lei 14.133"): a norma
    # não é filtro, e o resultado avisa isso em vez de parecer uma contagem por lei.
    norma_citada: bool = False
    # A pergunta fala em pagamento ("valor pago", "quanto pagou"): o resultado
    # avisa que a base tem o valor contratado/informado, não o pago.
    pagamento_citado: bool = False
    valor_max_exclusivo: bool = False
    plano_tipado: bool = False
    numero_controle_pncp: Optional[str] = None
    # A pergunta cita mais de uma esfera ("estaduais" e "municípios"): a conta sai
    # quebrada por esfera, e só compara de fato se houver valor em duas ou mais.
    comparacao_esferas: bool = False
    # Esferas que a pergunta compara ("estaduais ... do que os municípios").
    # Vazio quando ela não cita duas: aí contam todas as do resultado.
    esferas_comparadas: tuple[str, ...] = field(default_factory=tuple)
    # Contagem que também pede o total ("quantos registros ... e qual o valor
    # total?"): a tabela mostra os dois, e não só a contagem (V1).
    mostrar_total: bool = False
    # Listagem que a pergunta pede por inteiro ("quais órgãos contrataram X em
    # 2025 e quais os valores?"): a resposta sai das linhas do SQL, sem o LLM,
    # que resumia a lista e deixava registros de fora. Com um pedido além da
    # lista ("há dados suficientes para concluir?", "quais regras?"), False.
    listagem_direta: bool = False
    incluir_total_geral: bool = False
    # Situações inválidas que a pergunta cita ("anuladas", "revogadas"); vazio
    # deixa todas as inválidas fora da conta. Ver ``_situacoes_da_pergunta``.
    situacoes_citadas: tuple[str, ...] = ()
    # Junto das citadas, a pergunta quer as válidas ("anuladas e vigentes").
    com_situacoes_validas: bool = False
    # Consulta por identificador: o registro pedido aparece em qualquer situação.
    todas_as_situacoes: bool = False


def _singular(token: str) -> str:
    """Plural simples do português: "ambulancias" casa com "ambulancia".

    "-gem"/"-gens" viram o radical "-ge": o termo vai num ``LIKE``, e
    "passage" casa "passagem" e "passagens" (com "passagen", "passagem" ficava
    de fora; com "passagem", "passagens").
    """
    if token.endswith(("gem", "gens")) and len(token) > 5:
        return token[: token.rindex("ge") + 2]
    for plural, singular in (("oes", "ao"), ("res", "r"), ("zes", "z"), ("ais", "al"), ("eis", "el")):
        if token.endswith(plural) and len(token) > len(plural) + 2:
            return token[: -len(plural)] + singular
    return token[:-1] if token.endswith("s") and len(token) > 4 else token


# Radicais que nunca descrevem o objeto, em qualquer flexão ("comparando", "publicaram").
# "houv"/"haver"/"havia"/"haven"/"haja" são o verbo haver ("se não houver dados suficientes").
_RADICAIS_GENERICOS = (
    "compar", "publica", "assina", "vigen", "contrat", "registr", "realiz", "celebr", "firma",
    "houv", "haver", "havia", "haven", "haja",
)


_TIPO_CODIGO_INVALIDO = frozenset("de do da dos das em no na um uma ou e os as ao aos por com".split())


def _termos_do_objeto(texto: str) -> tuple[str, ...]:
    termos: list[str] = []
    # "tipo D", "tipo B", "tipo 3": a especificação faz parte do objeto.
    # Sem isso, "ambulancias tipo d" perde o "d" (1 caractere) e casa qualquer
    # ambulância. "Qual tipo de contrato" não gera termo: "de" é palavra.
    codigos = re.findall(r"\btipo\s+([a-z0-9]{1,2})\b", texto)
    texto = re.sub(r"\btipo\s+[a-z0-9]{1,2}\b", " ", texto)
    for codigo in codigos:
        if codigo in _TIPO_CODIGO_INVALIDO:
            continue
        termo = f"tipo {codigo}"
        if termo not in termos:
            termos.append(termo)
    for token in re.findall(r"[a-z]{4,}", texto):
        if (token in _PALAVRAS_GENERICAS or token.rstrip("s") in _PALAVRAS_GENERICAS
                or token in _NUMEROS_POR_EXTENSO or token.startswith(_RADICAIS_GENERICOS)):
            continue
        termo = _singular(token)
        if termo not in termos:
            termos.append(termo)
    return tuple(termos)


def detectar_consulta(query: str, filtros: QueryFilters, bases: Iterable[Natureza]) -> Optional[Consulta]:
    """Decide se a pergunta pede uma conta sobre a base estruturada/agregada.

    Devolve ``None`` quando a pergunta não é de ranking/estatística/comparação/
    evolução/agrupamento, ou quando o roteador não escolheu base estruturada
    nem agregada: aí a busca normal responde sozinha. Para valor unitário sem
    campo disponível, devolve uma listagem dos registros, nunca uma conta sobre
    o valor global.
    """
    valor_unitario_indisponivel = filtros.tipo_valor == "unitario"
    if ((filtros.tipo_medida == "unitaria" and not valor_unitario_indisponivel)
            or filtros.referencia_temporal in {"resultado", "vigencia", "propostas"}):
        return None
    bases = set(bases)
    situacoes = _situacoes_da_pergunta(query)
    if Natureza.ESTRUTURADA in bases:
        registro = _consulta_de_um_registro(query, filtros, situacoes)
        if registro is not None:
            return registro
    # Sem as expressões que já viraram filtro: em "média dos contratos com valor
    # maior que 2000", o "maior" não pede ranking, e "acima"/"milhão" não
    # descrevem o objeto contratado.
    texto = structured_db.normalizar(texto_sem_filtros(query))
    tabela = _tabela_da_pergunta(texto, bases)
    if tabela is None:
        return None

    # Particípio + "por" + plural é o agente, não agrupamento: "publicadas por
    # órgãos públicos", "contratados por municípios". "Total por órgão" agrupa.
    texto_agrupamento = _RE_AGENTE.sub(" ", texto)
    orgaos_e_total = bool(re.search(r"\bquais orgaos\b", texto)
                          and re.search(r"\b(?:total|soma)\b", texto))
    tipo = _tipo_da_pergunta(texto, texto_agrupamento, bases, tabela,
                             valor_unitario_indisponivel=valor_unitario_indisponivel,
                             orgaos_e_total=orgaos_e_total)
    if tipo is None:
        return None

    minimo_por_grupo = _minimo_por_grupo(texto) if tabela == "contratacoes" else None
    if minimo_por_grupo is not None:
        tipo = "agrupamento"

    limite = _limite_da_pergunta(texto, tipo)
    agrupar_por = _agrupamento_da_pergunta(texto_agrupamento, tabela, orgaos_e_total)
    metrica = "soma" if orgaos_e_total else filtros.metrica or "contagem"
    if tabela == "contratacoes" and re.search(r"\bfornecedo(?:r|res)\b", texto) and "valor total" in texto:
        tipo, agrupar_por, metrica = "agrupamento", "fornecedor", "soma"
    if minimo_por_grupo is not None:
        agrupar_por = "fornecedor"

    r = _recorte_da_pergunta(query, texto, filtros, tabela, tipo,
                             valor_unitario_indisponivel=valor_unitario_indisponivel,
                             minimo_por_grupo=minimo_por_grupo)
    so_menor = re.search(r"\bmenor(?:es)?\b", texto) and not re.search(r"\bmaior(?:es)?\b", texto)
    primeiro_ano, ultimo_ano = filtros.intervalo_de_anos()
    # O CNPJ citado também delimita o pedido ("contratos do fornecedor de CNPJ X").
    if (tipo == "listagem" and not r.termos_objeto and not r.modalidade_termos and not r.orgao_termos
            and not r.analiticos and primeiro_ano is None and ultimo_ano is None
            and filtros.valor_min is None and filtros.valor_max is None):
        return None  # "Quais contratos existem?" sem assunto: a busca normal responde
    return Consulta(
        tabela=tabela,
        tipo=tipo,
        ordem="ASC" if so_menor else "DESC",
        limite=max(1, min(limite, 50)),
        agrupar_por=agrupar_por,
        metrica=metrica,
        campo=_COLUNA_DO_TIPO_DE_VALOR.get(filtros.tipo_valor),
        filtros_analiticos=tuple((k, v) for k, v in r.analiticos.items() if k not in {"esfera", "modalidade", "orgao_nome"}),
        papel_orgao=filtros.papel_orgao,
        papel_cnpj=filtros.papel_cnpj,
        valor_min_exclusivo=filtros.valor_min_exclusivo,
        valor_max_exclusivo=filtros.valor_max_exclusivo,
        **_campos_de_periodo(primeiro_ano, ultimo_ano),
        valor_min=filtros.valor_min,
        valor_max=filtros.valor_max,
        esfera=r.esfera,
        comparacao_esferas=bool(tabela == "contratacoes" and tipo == "estatistica"
                                and r.esfera is None and len(r.citadas) > 1),
        esferas_comparadas=tuple(r.citadas) if len(r.citadas) > 1 else (),
        mostrar_total=bool(metrica == "contagem" and re.search(r"\b(?:soma|total)\b", texto)),
        termos_objeto=r.termos_objeto,
        referencia_temporal=r.referencia_temporal,
        minimo_por_grupo=minimo_por_grupo,
        mes=r.mes,
        modalidade_termos=r.modalidade_termos,
        orgao_termos=r.orgao_termos,
        so_contratos=r.so_contratos,
        norma_citada=bool(_RE_NORMA_NUMERADA.search(texto)),
        pagamento_citado=bool(_RE_PAGAMENTO.search(texto)),
        listagem_direta=(tipo == "listagem" and not valor_unitario_indisponivel
                         and not _RE_NORMA_NUMERADA.search(texto)
                         and not _RE_PEDIDO_ALEM_DA_LISTA.search(texto)),
        incluir_total_geral=orgaos_e_total,
        **situacoes,
    )


@dataclass(frozen=True)
class _Recorte:
    """Filtros da pergunta além do tipo: esfera, modalidade, órgão, mês e objeto."""
    analiticos: dict[str, Any]
    esfera: Optional[str]
    citadas: list[str]
    modalidade_termos: tuple[str, ...]
    orgao_termos: tuple[str, ...]
    mes: Optional[int]
    so_contratos: bool
    referencia_temporal: Optional[str]
    termos_objeto: tuple[str, ...]


def _recorte_da_pergunta(query: str, texto: str, filtros: QueryFilters, tabela: str, tipo: str, *,
                         valor_unitario_indisponivel: bool, minimo_por_grupo: Optional[int]) -> _Recorte:
    # Esfera e modalidade: vale o que o query_analysis já extraiu; a detecção
    # local só cobre quem chama sem esses filtros, e ignora citação de norma.
    analiticos = filtros.filtros_analiticos or {}
    sem_normas = _RE_CITACAO_DE_NORMA.sub(" ", texto)
    esfera, citadas = _esferas_da_pergunta(sem_normas, analiticos) if tabela == "contratacoes" else (None, [])
    modalidade_termos, texto_objeto = _modalidade_e_objeto(
        texto, sem_normas, analiticos, tabela, tipo,
        valor_unitario_indisponivel=valor_unitario_indisponivel, minimo_por_grupo=minimo_por_grupo)
    mes = _mes_da_pergunta(query, filtros)
    orgao_termos, texto_objeto = _orgao_da_pergunta(filtros, texto_objeto)
    # Contratos assinados não trazem modalidade no PNCP; com filtro de
    # modalidade a consulta usa as compras publicadas, que trazem.
    so_contratos = bool(tabela == "contratacoes" and not modalidade_termos and
                        re.search(r"\bcontratos?\b|\bcontratad|\bcontrat(?:ou|aram)\b", texto))
    return _Recorte(
        analiticos=analiticos, esfera=esfera, citadas=citadas, modalidade_termos=modalidade_termos,
        orgao_termos=orgao_termos, mes=mes, so_contratos=so_contratos,
        # Contrato conta pela assinatura, salvo quando a pergunta pede a publicação.
        referencia_temporal=filtros.referencia_temporal or ("assinatura" if so_contratos else None),
        termos_objeto=(_termos_objeto_da_pergunta(texto_objeto, valor_unitario_indisponivel)
                       if tabela == "contratacoes" else ()),
    )


# Etapa do valor pedida na pergunta -> coluna da tabela contratacoes.
_COLUNA_DO_TIPO_DE_VALOR = {"contratado": "valor_contratado", "estimado": "valor_estimado",
                            "homologado": "valor_homologado"}


def _campos_de_periodo(primeiro_ano: Optional[int], ultimo_ano: Optional[int]) -> dict[str, Optional[int]]:
    """Período fechado em ``ano``/``ano_final``; aberto em ``ano_desde``/``ano_ate``."""
    fechado = primeiro_ano is not None and ultimo_ano is not None
    return {
        "ano": primeiro_ano if fechado else None,
        "ano_final": ultimo_ano if fechado else None,
        "ano_desde": primeiro_ano if ultimo_ano is None else None,
        "ano_ate": ultimo_ano if primeiro_ano is None else None,
    }


def _minimo_por_grupo(texto: str) -> Optional[int]:
    """"fornecedores com três ou mais contratos": agrupa e exige o mínimo."""
    contagem = _RE_CONTAGEM_CONTRATOS.search(texto)
    if not contagem:
        return None
    numero = contagem.group("numero") or contagem.group("minimo")
    return int(numero) if numero.isdigit() else _NUMEROS_POR_EXTENSO.get(numero, 1)


def _termos_objeto_da_pergunta(texto_objeto: str, valor_unitario_indisponivel: bool) -> tuple[str, ...]:
    """Termos do objeto contratado; no pedido de preço unitário, sem as palavras do pedido."""
    termos = _termos_do_objeto(texto_objeto)
    if valor_unitario_indisponivel:
        termos = tuple(t for t in termos if t not in {"veiculo", "grupo", "diga", "explicitamente"})
    return termos


def _consulta_de_um_registro(query: str, filtros: QueryFilters, situacoes: dict[str, Any]) -> Optional[Consulta]:
    """Pedido de um registro: pelo número de controle do PNCP, ou o CNPJ de um fornecedor."""
    identificador = _RE_NUMERO_CONTROLE_PNCP.search(query)
    if identificador:
        return Consulta(
            tabela="contratacoes",
            tipo="identificador",
            limite=1,
            numero_controle_pncp=identificador.group(0),
            todas_as_situacoes=True,
        )
    # "Qual o CNPJ da Cristália?": o CNPJ está no registro do fornecedor.
    # Sem este caminho, a pergunta ia à busca e ao LLM, que levava 90 s e
    # respondia com o CNPJ do órgão contratante.
    fornecedor = (filtros.filtros_analiticos or {}).get("fornecedor")
    pergunta = structured_db.normalizar(query)
    if (fornecedor and not (filtros.filtros_analiticos or {}).get("cnpj")
            and _RE_PEDIDO_DE_CNPJ.search(pergunta) and not _RE_CNPJ_COM_CONTA.search(pergunta)):
        return Consulta(
            tabela="contratacoes",
            tipo="cnpj_fornecedor",
            limite=5,
            filtros_analiticos=(("fornecedor", fornecedor),),
            **situacoes,
        )
    return None


def _tabela_da_pergunta(texto: str, bases: set[Natureza]) -> Optional[str]:
    """``contratacoes`` ou ``pca_planos``; ``None`` sem base estruturada nem agregada.

    "Valor total contratado" é pergunta de contratos mesmo quando o roteador só
    escolheu a agregada (por causa do "total"); PCA só quando a pergunta fala de
    plano/PCA.
    """
    fala_de_contratos = bool(_RE_CONTRATOS.search(texto)) and not _RE_PCA.search(texto)
    if Natureza.AGREGADA in bases and not fala_de_contratos and (
        _RE_PCA.search(texto) or Natureza.ESTRUTURADA not in bases
    ):
        return "pca_planos"
    if Natureza.ESTRUTURADA in bases or (Natureza.AGREGADA in bases and fala_de_contratos):
        return "contratacoes"
    return None


def _tipo_da_pergunta(texto: str, texto_agrupamento: str, bases: set[Natureza], tabela: str, *,
                      valor_unitario_indisponivel: bool, orgaos_e_total: bool) -> Optional[str]:
    """Tipo de consulta pedido; ``None`` quando não é conta (a busca responde).

    A ordem importa: a primeira regra que casa decide.
    """
    if valor_unitario_indisponivel:
        return "listagem"
    if orgaos_e_total:
        return "agrupamento"
    if _RE_EVOLUCAO.search(texto):
        return "evolucao"
    if _RE_AGRUPAMENTO.search(texto_agrupamento):
        return "agrupamento"
    if (_RE_QUAIS_MODALIDADES.search(texto) and Natureza.NORMATIVA not in bases
            and not _RE_NORMA_NUMERADA.search(texto)):
        return "agrupamento"
    if _RE_CONTAGEM_CONTRATOS.search(texto):
        return "agrupamento"
    if _RE_COMPARACAO.search(texto):
        return "estatistica"  # sai quebrada por esfera, que é a comparação pedida
    if _RE_RANKING.search(texto):
        return "ranking"
    if _RE_ESTATISTICA.search(texto) or (_RE_PAGAMENTO.search(texto) and re.search(r"\bvalor(es)?\b", texto)):
        # "Qual foi o valor pago à empresa X?" pede uma soma, como "valor total".
        return "estatistica"
    if (
        tabela == "contratacoes"
        and _RE_LISTAGEM.search(texto)
        and (
            bases <= {Natureza.ESTRUTURADA, Natureza.AGREGADA}
            or (Natureza.ESTRUTURADA in bases and _RE_LISTAGEM_CONTRATACOES.search(texto))
        )
    ):
        # "Quais órgãos contrataram manutenção de computadores em 2025?": filtro
        # por metadado e objeto, que a busca por texto não garante. Em perguntas
        # multi-base, limita a listagem estruturada ao objeto da cláusula pedida.
        return "listagem"
    return None


def _limite_da_pergunta(texto: str, tipo: str) -> int:
    """"5 maiores" pede 5; "o maior contrato" pede 1; senão, o limite padrão."""
    if n := _RE_N_RANKING.search(texto):
        return _NUMEROS_POR_EXTENSO.get(n.group(1)) or int(n.group(1))
    if tipo == "ranking" and re.search(r"\b(?:o|qual)\s+(?:o\s+)?(?:maior|menor)\s+contrato\b", texto):
        return 1
    return LIMITE_RANKING


def _agrupamento_da_pergunta(texto_agrupamento: str, tabela: str, orgaos_e_total: bool) -> str:
    if tabela == "pca_planos":
        return "unidade_nome"
    if orgaos_e_total or "por orgao" in texto_agrupamento:
        return "orgao_nome"
    if "por municipio" in texto_agrupamento:
        return "municipio"
    if "por esfera" in texto_agrupamento:
        return "esfera"
    return "modalidade"


def _esferas_da_pergunta(sem_normas: str, analiticos: dict[str, Any]) -> tuple[Optional[str], list[str]]:
    """Esfera do filtro (uma só citada) e as citadas (várias = comparação)."""
    if analiticos.get("esfera"):
        return analiticos["esfera"], []
    # "por município(s)" é agrupamento, não filtro de esfera.
    sem_agrupamento = re.sub(r"\bpor (?:municipios?|orgaos?|esferas?)\b", " ", sem_normas)
    citadas = [nome for nome, padrao in _ESFERAS if re.search(padrao, sem_agrupamento)]
    # Várias juntas = comparação, que já sai quebrada por esfera.
    return (citadas[0] if len(citadas) == 1 else None), citadas


def _modalidade_e_objeto(texto: str, sem_normas: str, analiticos: dict[str, Any], tabela: str, tipo: str, *,
                         valor_unitario_indisponivel: bool,
                         minimo_por_grupo: Optional[int]) -> tuple[tuple[str, ...], str]:
    """Termos da modalidade pedida e o texto que sobra para descrever o objeto."""
    modalidade_termos: tuple[str, ...] = ()
    texto_objeto = texto
    if tabela != "contratacoes":
        return modalidade_termos, texto_objeto
    if valor_unitario_indisponivel:
        texto_objeto = re.sub(r"\b(?:valor|preco)\s+por\s+(?:veiculos?|ambulancias?|unidades?)\b", " ", texto_objeto)
    pedida = analiticos.get("modalidade")
    for padrao, termos in _MODALIDADES:
        if padrao.search(pedida if pedida else sem_normas):
            modalidade_termos = termos
            break
    for padrao, _ in _MODALIDADES:  # a modalidade não descreve o objeto
        texto_objeto = padrao.sub(" ", texto_objeto)
    if (tipo == "listagem" and _RE_LISTAGEM_CONTRATACOES.search(texto)) or minimo_por_grupo is not None:
        objeto_contratacao = _RE_OBJETO_CONTRATACAO.search(texto)
        if objeto_contratacao:
            texto_objeto = objeto_contratacao.group(1)
    return modalidade_termos, texto_objeto


def _mes_da_pergunta(query: str, filtros: QueryFilters) -> Optional[int]:
    """O mês vem da pergunta original: o query_analysis já tira "janeiro de 2025"
    do texto quando vira filtro de ano. Só vale com um ano só."""
    achado = _RE_MES.search(structured_db.normalizar(query))
    if achado and filtros.ano is not None and filtros.ano_final in (None, filtros.ano):
        return _MESES[achado.group(1)]
    return None


def _orgao_da_pergunta(filtros: QueryFilters, texto_objeto: str) -> tuple[tuple[str, ...], str]:
    """Palavras do órgão pedido, tiradas também do texto do objeto."""
    if not filtros.orgao:
        return (), texto_objeto
    palavras = re.findall(r"[a-z]+", structured_db.normalizar(filtros.orgao))
    orgao_termos = tuple(p for p in palavras if p not in _PALAVRAS_ORGAO_VAZIAS and len(p) > 2)
    for palavra in palavras:  # o órgão não descreve o objeto contratado
        texto_objeto = re.sub(rf"\b{re.escape(palavra)}\b", " ", texto_objeto)
    return orgao_termos, texto_objeto


def consulta_da_intencao(intent: QueryIntent) -> Optional[Consulta]:
    """Compila o plano tipado para operações disponíveis no SQLite existente."""
    f = intent.filtros
    valor_unitario_indisponivel = f.tipo_valor == "unitario"
    pca_media_unitaria = (f.tipo_medida == "unitaria" and f.metrica == "media_unitaria"
                         and f.referencia_temporal == "pca")
    if ((intent.operacao == "buscar" and not valor_unitario_indisponivel)
            or (f.tipo_medida == "unitaria" and not pca_media_unitaria
                and not valor_unitario_indisponivel)
            or f.referencia_temporal in {"resultado", "vigencia", "propostas"}):
        return None
    tabela = "pca_planos" if f.referencia_temporal == "pca" else "contratacoes"
    if valor_unitario_indisponivel and not intent.termos_objeto:
        return None
    if not set(intent.bases) & {Natureza.ESTRUTURADA, Natureza.AGREGADA}:
        return None
    fields = structured_db._TABELAS[tabela]
    groups = f.agrupamentos or (["unidade_nome"] if tabela == "pca_planos" else ["modalidade"])
    if any(g not in fields for g in groups):
        return None
    filters = dict(f.filtros_analiticos)
    campo_entidade = "unidade_nome" if f.papel_orgao == "unidade" else "orgao_nome"
    if f.orgao and f.papel_orgao is None:
        filters[campo_entidade] = f.orgao
    if any(k not in fields for k in filters):
        return None
    # Usa a mesma resolução de nomes/modalidades do caminho por regras.
    modalidade = structured_db.normalizar(filters.pop("modalidade", ""))
    modalidade_termos = next((termos for padrao, termos in _MODALIDADES if padrao.search(modalidade)), ())
    if modalidade and not modalidade_termos:
        return None
    orgao_termos = tuple(
        p for p in re.findall(r"[a-z]+", structured_db.normalizar(f.orgao or ""))
        if p not in _PALAVRAS_ORGAO_VAZIAS and len(p) > 2)
    texto = structured_db.normalizar(intent.pergunta)
    so_contratos = bool(tabela == "contratacoes" and not modalidade_termos and
                        re.search(r"\bcontratos?\b|\bcontratad|\bcontrat(?:ou|aram)\b", texto))
    referencia = f.referencia_temporal or ("assinatura" if so_contratos else None)
    primeiro_ano, ultimo_ano = f.intervalo_de_anos()
    metrica = f.metrica or "contagem"
    return Consulta(tabela=tabela, tipo="listagem" if valor_unitario_indisponivel else intent.operacao,
        limite=intent.limite,
        plano_tipado=True,
        so_contratos=so_contratos, modalidade_termos=modalidade_termos, orgao_termos=orgao_termos,
        ordem="ASC" if f.metrica == "minimo" else "DESC",
        **_campos_de_periodo(primeiro_ano, ultimo_ano),
        valor_min=f.valor_min, valor_max=f.valor_max,
        valor_min_exclusivo=f.valor_min_exclusivo, valor_max_exclusivo=f.valor_max_exclusivo,
        filtros_analiticos=tuple(filters.items()), papel_orgao=f.papel_orgao,
        papel_cnpj=f.papel_cnpj,
        agrupar_por=groups[0], agrupamentos=tuple(groups),
        metrica=f.metrica or "contagem", minimo_por_grupo=f.minimo_por_grupo,
        campo=_COLUNA_DO_TIPO_DE_VALOR.get(f.tipo_valor),
        referencia_temporal=referencia,
        termos_objeto=tuple(structured_db.normalizar(t) for t in intent.termos_objeto) if tabela == "contratacoes" else (),
        **_situacoes_da_pergunta(intent.pergunta))


_situacoes_da_pergunta = purchase_status.situacoes_da_pergunta
