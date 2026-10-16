"""Consultas SQL fixas sobre a base estruturada, como evidência para a geração.

Perguntas de ranking ("dez maiores contratações por valor"), estatística
("mediana", "valor médio", "quantas"), comparação ("pagaram mais do que"),
evolução ("ao longo de 2025") e agrupamento ("modalidades mais frequentes")
não se resolvem por busca semântica. Aqui a pergunta é classificada por regras
e respondida por uma consulta SQL **pré-escrita e parametrizada** (o LLM nunca
escreve SQL). O resultado vira ``Chunk`` com ``doc_type="sql"``, que
``src/pipeline.py`` põe à frente dos chunks da busca para o LLM citar.

Listagens explícitas de contratações também podem produzir uma evidência SQL
limitada aos termos do objeto, inclusive quando a pergunta exige cruzá-la com
evidência normativa.

Filtros vêm de ``query_analysis`` (ano, valor) e da própria pergunta (esfera
estadual/municipal, termos do objeto). O banco é o de ``src/structured_db.py``
(Postgres/Neon com ``DATABASE_URL``, senão SQLite local).
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import config
from src import structured_db
from src.obs import Stage, default_logger
from src.query_analysis import texto_sem_filtros
from src.schema import Chunk, ChunkMetadata, Natureza, QueryFilters, QueryIntent
from src.structured_db import Banco

DOCUMENTS_FILE = structured_db.DOCUMENTS_FILE

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
# Palavra do nome do banco que muda o órgão quando a pergunta não a diz:
# "Universidade do Estado do RJ" não é a "Universidade FEDERAL do Estado do RJ".
_QUALIFICADORES_DE_ORGAO = frozenset({"federal", "regional"})
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
_RE_COMPARACAO = re.compile(r"\bmais car[oa]s?\b|\bmais barat|\bpag(?:ou|aram|am|a) mais\b|\bcompar")
# Sem "\bvalor\b": pedir para *informar* um valor ("informe o valor contratado")
# é listar, não calcular. Só conta como cálculo a palavra que declara a conta
# (média, mediana, total, soma, quanto, quantidade). Métrica sem a pergunta
# pedir é extrapolação — ver P4 do plano.
_RE_ESTATISTICA = re.compile(r"\bmedia\b|\bmedio\b|\bmediana\b|\btotal\b|\bsoma\b|\bquant[oa]s?\b|\bquantidade\b|\bmaior(es)?\b|\bmenor(es)?\b")
_RE_CONTAGEM_CONTRATOS = re.compile(
    r"\b(?:(?P<numero>\d+|um|dois|tres|quatro|cinco)\s+ou\s+mais|"
    r"(?:pelo\s+menos|no\s+minimo)\s+(?P<minimo>\d+|um|dois|tres|quatro|cinco))"
    r"\s+(?:contratos?|contratac(?:ao|oes))\b"
)
_RE_AGENTE = re.compile(r"\b[a-z]+(?:ad|id|it)[oa]s? por (?:orgaos|municipios|esferas)\b")
# Pergunta que pede os registros em si: "quais órgãos contrataram…", "existem contratações de…".
_RE_LISTAGEM = re.compile(r"^(?:quais|qual|existem?|ha|houve|liste|listar|mostre|mostrar)\b")
_RE_LISTAGEM_CONTRATACOES = re.compile(
    r"\b(?:quais|qual|liste|listar|mostre|mostrar)\s+contrat(?:a(?:cao|coes)|os?)\b"
)
_RE_OBJETO_CONTRATACAO = re.compile(
    r"\bcontrat(?:a(?:cao|coes)|os?)\s+de\s+(.+?)"
    r"(?=\s+(?:do corpus|da base|que|usam|utilizam|foram|sao|existem|em)\b|[,;?.!]|$)"
)
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
    assinou assinaram contratou contrataram comprou compraram gastou gastaram
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
    # Listagem que a pergunta pede por inteiro ("quais órgãos contrataram X em
    # 2025 e quais os valores?"): a resposta sai das linhas do SQL, sem o LLM,
    # que resumia a lista e deixava registros de fora. Com um pedido além da
    # lista ("há dados suficientes para concluir?", "quais regras?"), False.
    listagem_direta: bool = False


# Contrato de compilação apenas: estas tabelas de fatos não são criadas aqui.
# O caminho legado SQLite/SQL permanece independente desta função.
_DATASETS_SQL = {
    "contratos_estruturado": {
        "tabela": "contratos_fatos", "valor": "valor",
        "campos": frozenset({"valor", "valor_contratado", "valor_estimado", "valor_homologado"}),
        "grupos": frozenset({"orgao_nome", "municipio", "modalidade", "esfera", "fornecedor", "numero_controle_pncp", "mes_assinatura", "mes_publicacao"}),
        "expressoes_grupo": {"mes_assinatura": "EXTRACT(MONTH FROM data_assinatura)",
                             "mes_publicacao": "EXTRACT(MONTH FROM data_publicacao)"},
        "datas": {"assinatura": "data_assinatura", "resultado": "data_resultado", "publicacao": "data_publicacao"},
    },
    "pca_agregado": {
        "tabela": "pca_fatos", "valor": "valor_total",
        "campos": frozenset({"valor_total", "quantidade_itens"}),
        "grupos": frozenset({"orgao_nome", "municipio", "unidade_nome", "categoria", "classe"}),
        "datas": {"pca": "ano_pca", "resultado": "data_resultado"},
    },
}
_METRICAS_SQL = {
    "contagem": "COUNT(*)", "soma": "SUM({campo})", "media": "AVG({campo})",
    "minimo": "MIN({campo})", "maximo": "MAX({campo})",
    "mediana": "percentile_cont(0.5) WITHIN GROUP (ORDER BY {campo})",
}


def compilar_consulta_postgres(consulta: Consulta) -> tuple[str, tuple[Any, ...]]:
    """Compila SQL permitido e parâmetros, sem I/O, DDL ou execução.

    Versão é obrigatória em fatos agregados. Datas nunca são intercambiáveis:
    uma seleção de ano exige a referência PCA, assinatura, resultado ou publicação.
    """
    dataset = consulta.dataset
    if dataset not in _DATASETS_SQL:
        raise ValueError("dataset não permitido")
    spec = _DATASETS_SQL[dataset]
    if dataset == "pca_agregado" and not consulta.versao:
        raise ValueError("fatos agregados exigem versão")
    if consulta.metrica not in _METRICAS_SQL:
        raise ValueError("métrica não permitida")
    campo = consulta.campo or spec["valor"]
    if campo not in spec["campos"]:
        raise ValueError("campo não permitido")
    grupos = consulta.agrupamentos
    if any(grupo not in spec["grupos"] for grupo in grupos) or len(set(grupos)) != len(grupos):
        raise ValueError("agrupamento não permitido")
    if consulta.ordem not in {"ASC", "DESC"} or not 1 <= consulta.limite <= 50:
        raise ValueError("ordem ou limite não permitido")
    if consulta.referencia_temporal is not None and consulta.referencia_temporal not in spec["datas"]:
        raise ValueError("referência temporal não permitida")
    condicoes: list[str] = []
    parametros: list[Any] = []
    if consulta.versao:
        condicoes.append("inventory_version = %s")
        parametros.append(consulta.versao)
    if consulta.ano_final is not None and consulta.ano is None:
        raise ValueError("ano_final exige ano inicial")
    if consulta.ano is not None or consulta.ano_desde is not None or consulta.ano_ate is not None:
        if consulta.referencia_temporal is None:
            raise ValueError("ano exige referência temporal explícita")
        coluna = spec["datas"][consulta.referencia_temporal]
        expressao = coluna if coluna == "ano_pca" else f"EXTRACT(YEAR FROM {coluna})"
        if consulta.ano is not None:
            fim = consulta.ano_final or consulta.ano
            if not 1900 <= consulta.ano <= fim <= 2099:
                raise ValueError("período inválido")
            condicoes.append(f"{expressao} BETWEEN %s AND %s")
            parametros.extend((consulta.ano, fim))
        for operador, ano in ((">=", consulta.ano_desde), ("<=", consulta.ano_ate)):
            if ano is not None:
                if not 1900 <= ano <= 2099:
                    raise ValueError("período inválido")
                condicoes.append(f"{expressao} {operador} %s")
                parametros.append(ano)
    for operador, valor in ((">" if consulta.valor_min_exclusivo else ">=", consulta.valor_min), ("<" if consulta.valor_max_exclusivo else "<=", consulta.valor_max)):
        if valor is not None:
            condicoes.append(f"{campo} {operador} %s")
            parametros.append(valor)
    if consulta.valor_min is not None and consulta.valor_max is not None and consulta.valor_min > consulta.valor_max:
        raise ValueError("faixa de valor inválida")
    for chave, valor in consulta.filtros_analiticos:
        if chave not in spec["grupos"]:
            raise ValueError("filtro não permitido")
        expressao_filtro = spec.get("expressoes_grupo", {}).get(chave, chave)
        condicoes.append(f"{expressao_filtro} = %s")
        parametros.append(valor)
    if consulta.esfera:
        if "esfera" not in spec["grupos"]:
            raise ValueError("esfera não permitida neste dataset")
        condicoes.append("esfera = %s")
        parametros.append(consulta.esfera)
    for termo in consulta.termos_objeto:
        condicoes.append("objeto_normalizado LIKE %s")
        parametros.append(f"%{termo}%")
    agregado = _METRICAS_SQL[consulta.metrica].format(campo=campo)
    expressoes_grupo = tuple(spec.get("expressoes_grupo", {}).get(grupo, grupo) for grupo in grupos)
    selecao = ", ".join((*(f"{expressao} AS {grupo}" if expressao != grupo else grupo
                         for grupo, expressao in zip(grupos, expressoes_grupo)), f"{agregado} AS resultado"))
    sql = f"SELECT {selecao} FROM {spec['tabela']}"
    if condicoes:
        sql += " WHERE " + " AND ".join(condicoes)
    if grupos:
        sql += " GROUP BY " + ", ".join(expressoes_grupo)
    if consulta.minimo_por_grupo is not None:
        if not grupos or isinstance(consulta.minimo_por_grupo, bool) or not isinstance(consulta.minimo_por_grupo, int) or consulta.minimo_por_grupo < 1:
            raise ValueError("mínimo por grupo exige agrupamento e inteiro positivo")
        sql += " HAVING COUNT(*) >= %s"
        parametros.append(consulta.minimo_por_grupo)
    sql += f" ORDER BY resultado {consulta.ordem}"
    if grupos:
        sql += ", " + ", ".join(grupos)
    sql += " LIMIT %s"
    parametros.append(consulta.limite)
    return sql, tuple(parametros)


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
    nem agregada: aí a busca normal responde sozinha.
    """
    if filtros.tipo_valor == "unitario" or filtros.referencia_temporal in {"resultado", "vigencia", "propostas"}:
        return None
    bases = set(bases)
    if Natureza.ESTRUTURADA in bases:
        identificador = _RE_NUMERO_CONTROLE_PNCP.search(query)
        if identificador:
            return Consulta(
                tabela="contratacoes",
                tipo="identificador",
                limite=1,
                numero_controle_pncp=identificador.group(0),
            )
    # Sem as expressões que já viraram filtro: em "média dos contratos com valor
    # maior que 2000", o "maior" não pede ranking, e "acima"/"milhão" não
    # descrevem o objeto contratado.
    texto = structured_db.normalizar(texto_sem_filtros(query))
    # "Valor total contratado" é pergunta de contratos mesmo quando o roteador
    # só escolheu a agregada (por causa do "total"); PCA só quando a pergunta
    # fala de plano/PCA.
    fala_de_contratos = bool(_RE_CONTRATOS.search(texto)) and not _RE_PCA.search(texto)
    if Natureza.AGREGADA in bases and not fala_de_contratos and (
        _RE_PCA.search(texto) or Natureza.ESTRUTURADA not in bases
    ):
        tabela = "pca_planos"
    elif Natureza.ESTRUTURADA in bases or (Natureza.AGREGADA in bases and fala_de_contratos):
        tabela = "contratacoes"
    else:
        return None

    # Particípio + "por" + plural é o agente, não agrupamento: "publicadas por
    # órgãos públicos", "contratados por municípios". "Total por órgão" agrupa.
    texto_agrupamento = _RE_AGENTE.sub(" ", texto)
    if _RE_EVOLUCAO.search(texto):
        tipo = "evolucao"
    elif _RE_AGRUPAMENTO.search(texto_agrupamento):
        tipo = "agrupamento"
    elif _RE_CONTAGEM_CONTRATOS.search(texto):
        tipo = "agrupamento"
    elif _RE_COMPARACAO.search(texto):
        tipo = "estatistica"  # sai quebrada por esfera, que é a comparação pedida
    elif _RE_RANKING.search(texto):
        tipo = "ranking"
    elif _RE_ESTATISTICA.search(texto) or (_RE_PAGAMENTO.search(texto) and re.search(r"\bvalor(es)?\b", texto)):
        # "Qual foi o valor pago à empresa X?" pede uma soma, como "valor total".
        tipo = "estatistica"
    elif (
        tabela == "contratacoes"
        and _RE_LISTAGEM.search(texto)
        and (
            bases <= {Natureza.ESTRUTURADA, Natureza.AGREGADA}
            or (
                Natureza.ESTRUTURADA in bases
                and _RE_LISTAGEM_CONTRATACOES.search(texto)
            )
        )
    ):
        # "Quais órgãos contrataram manutenção de computadores em 2025?": filtro
        # por metadado e objeto, que a busca por texto não garante. Em perguntas
        # multi-base, limita a listagem estruturada ao objeto da cláusula pedida.
        tipo = "listagem"
    else:
        return None

    minimo_por_grupo = None
    if tabela == "contratacoes" and (contagem := _RE_CONTAGEM_CONTRATOS.search(texto)):
        # "fornecedores com três ou mais contratos": agrupa e exige o mínimo.
        numero = contagem.group("numero") or contagem.group("minimo")
        minimo_por_grupo = int(numero) if numero.isdigit() else _NUMEROS_POR_EXTENSO.get(numero, 1)
        tipo = "agrupamento"

    limite = LIMITE_RANKING
    if n := _RE_N_RANKING.search(texto):
        limite = _NUMEROS_POR_EXTENSO.get(n.group(1)) or int(n.group(1))
    agrupar_por = "modalidade"
    if tabela == "pca_planos":
        agrupar_por = "unidade_nome"
    elif "por orgao" in texto_agrupamento:
        agrupar_por = "orgao_nome"
    elif "por municipio" in texto_agrupamento:
        agrupar_por = "municipio"
    elif "por esfera" in texto_agrupamento:
        agrupar_por = "esfera"

    metrica = "contagem"
    if tabela == "contratacoes" and re.search(r"\bfornecedo(?:r|res)\b", texto) and "valor total" in texto:
        tipo = "agrupamento"
        agrupar_por = "fornecedor"
        metrica = "soma"
    if minimo_por_grupo is not None:
        agrupar_por = "fornecedor"

    # Esfera e modalidade: vale o que o query_analysis já extraiu; a detecção
    # local só cobre quem chama sem esses filtros, e ignora citação de norma.
    analiticos = filtros.filtros_analiticos or {}
    sem_normas = _RE_CITACAO_DE_NORMA.sub(" ", texto)
    esfera = None
    citadas: list[str] = []
    if tabela == "contratacoes":
        if analiticos.get("esfera"):
            esfera = analiticos["esfera"]
        else:
            # "por município(s)" é agrupamento, não filtro de esfera.
            sem_agrupamento = re.sub(r"\bpor (?:municipios?|orgaos?|esferas?)\b", " ", sem_normas)
            citadas = [nome for nome, padrao in _ESFERAS if re.search(padrao, sem_agrupamento)]
            if len(citadas) == 1:  # várias juntas = comparação, que já sai quebrada por esfera
                esfera = citadas[0]

    modalidade_termos: tuple[str, ...] = ()
    texto_objeto = texto
    if tabela == "contratacoes":
        pedida = analiticos.get("modalidade")
        for padrao, termos in _MODALIDADES:
            if padrao.search(pedida if pedida else sem_normas):
                modalidade_termos = termos
                break
        for padrao, _ in _MODALIDADES:  # a modalidade não descreve o objeto
            texto_objeto = padrao.sub(" ", texto_objeto)
        if (
            (tipo == "listagem" and _RE_LISTAGEM_CONTRATACOES.search(texto))
            or minimo_por_grupo is not None
        ):
            objeto_contratacao = _RE_OBJETO_CONTRATACAO.search(texto)
            if objeto_contratacao:
                texto_objeto = objeto_contratacao.group(1)

    # O mês vem da pergunta original: o query_analysis já tira "janeiro de 2025"
    # do texto quando vira filtro de ano.
    mes = None
    achado = _RE_MES.search(structured_db.normalizar(query))
    if achado and filtros.ano is not None and filtros.ano_final in (None, filtros.ano):
        mes = _MESES[achado.group(1)]

    orgao_termos: tuple[str, ...] = ()
    if filtros.orgao:
        palavras = re.findall(r"[a-z]+", structured_db.normalizar(filtros.orgao))
        orgao_termos = tuple(p for p in palavras if p not in _PALAVRAS_ORGAO_VAZIAS and len(p) > 2)
        for palavra in palavras:  # o órgão não descreve o objeto contratado
            texto_objeto = re.sub(rf"\b{re.escape(palavra)}\b", " ", texto_objeto)

    so_menor = re.search(r"\bmenor(?:es)?\b", texto) and not re.search(r"\bmaior(?:es)?\b", texto)
    primeiro_ano, ultimo_ano = filtros.intervalo_de_anos()
    # Contratos assinados não trazem modalidade no PNCP; com filtro de
    # modalidade a consulta usa as compras publicadas, que trazem.
    so_contratos = bool(tabela == "contratacoes" and not modalidade_termos and
                        re.search(r"\bcontratos?\b|\bcontratad|\bcontrat(?:ou|aram)\b", texto))
    # Contrato conta pela assinatura, salvo quando a pergunta pede a publicação.
    referencia_temporal = filtros.referencia_temporal or ("assinatura" if so_contratos else None)
    termos_objeto = _termos_do_objeto(texto_objeto) if tabela == "contratacoes" else ()
    # O CNPJ citado também delimita o pedido ("contratos do fornecedor de CNPJ X").
    if (tipo == "listagem" and not termos_objeto and not modalidade_termos and not orgao_termos
            and not analiticos.get("cnpj")):
        return None  # "Quais contratos existem?" sem assunto: a busca normal responde
    return Consulta(
        tabela=tabela,
        tipo=tipo,
        ordem="ASC" if so_menor else "DESC",
        limite=max(1, min(limite, 50)),
        agrupar_por=agrupar_por,
        metrica=metrica,
        campo={"contratado": "valor_contratado", "estimado": "valor_estimado",
               "homologado": "valor_homologado"}.get(filtros.tipo_valor),
        filtros_analiticos=tuple((k, v) for k, v in analiticos.items() if k not in {"esfera", "modalidade", "orgao_nome"}),
        valor_min_exclusivo=filtros.valor_min_exclusivo,
        valor_max_exclusivo=filtros.valor_max_exclusivo,
        # Período fechado em ano/ano_final; aberto em ano_desde/ano_ate.
        ano=primeiro_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_final=ultimo_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_desde=primeiro_ano if ultimo_ano is None else None,
        ano_ate=ultimo_ano if primeiro_ano is None else None,
        valor_min=filtros.valor_min,
        valor_max=filtros.valor_max,
        esfera=esfera,
        comparacao_esferas=bool(tabela == "contratacoes" and tipo == "estatistica"
                                and esfera is None and len(citadas) > 1),
        termos_objeto=termos_objeto,
        referencia_temporal=referencia_temporal,
        minimo_por_grupo=minimo_por_grupo,
        mes=mes,
        modalidade_termos=modalidade_termos,
        orgao_termos=orgao_termos,
        so_contratos=so_contratos,
        norma_citada=bool(_RE_NORMA_NUMERADA.search(texto)),
        pagamento_citado=bool(_RE_PAGAMENTO.search(texto)),
        listagem_direta=(tipo == "listagem" and not _RE_NORMA_NUMERADA.search(texto)
                         and not _RE_PEDIDO_ALEM_DA_LISTA.search(texto)),
    )


def consulta_da_intencao(intent: QueryIntent) -> Optional[Consulta]:
    """Compila o plano tipado para operações disponíveis no SQLite existente."""
    f = intent.filtros
    if intent.operacao == "buscar" or f.tipo_valor == "unitario" or f.referencia_temporal in {"resultado", "vigencia", "propostas"}:
        return None
    tabela = "pca_planos" if f.referencia_temporal == "pca" else "contratacoes"
    if not set(intent.bases) & {Natureza.ESTRUTURADA, Natureza.AGREGADA}:
        return None
    fields = structured_db._TABELAS[tabela]
    groups = f.agrupamentos or (["unidade_nome"] if tabela == "pca_planos" else ["modalidade"])
    if any(g not in fields for g in groups):
        return None
    filters = dict(f.filtros_analiticos)
    if f.orgao:
        filters["orgao_nome"] = f.orgao
    if any(k not in fields for k in filters):
        return None
    # Usa a mesma resolução de nomes/modalidades do caminho por regras.
    modalidade = structured_db.normalizar(filters.pop("modalidade", ""))
    modalidade_termos = next((termos for padrao, termos in _MODALIDADES if padrao.search(modalidade)), ())
    if modalidade and not modalidade_termos:
        return None
    orgao = filters.pop("orgao_nome", "")
    orgao_termos = tuple(p for p in re.findall(r"[a-z]+", structured_db.normalizar(orgao))
                        if p not in _PALAVRAS_ORGAO_VAZIAS and len(p) > 2)
    texto = structured_db.normalizar(intent.pergunta)
    so_contratos = bool(tabela == "contratacoes" and not modalidade_termos and
                        re.search(r"\bcontratos?\b|\bcontratad|\bcontrat(?:ou|aram)\b", texto))
    referencia = f.referencia_temporal or ("assinatura" if so_contratos else None)
    primeiro_ano, ultimo_ano = f.intervalo_de_anos()
    return Consulta(tabela=tabela, tipo=intent.operacao, limite=intent.limite,
        plano_tipado=True,
        so_contratos=so_contratos, modalidade_termos=modalidade_termos, orgao_termos=orgao_termos,
        ordem="ASC" if f.metrica == "minimo" else "DESC",
        ano=primeiro_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_final=ultimo_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_desde=primeiro_ano if ultimo_ano is None else None,
        ano_ate=ultimo_ano if primeiro_ano is None else None,
        valor_min=f.valor_min, valor_max=f.valor_max,
        valor_min_exclusivo=f.valor_min_exclusivo, valor_max_exclusivo=f.valor_max_exclusivo,
        filtros_analiticos=tuple(filters.items()), agrupar_por=groups[0], agrupamentos=tuple(groups),
        metrica=f.metrica or "contagem", minimo_por_grupo=f.minimo_por_grupo,
        campo={"contratado": "valor_contratado", "estimado": "valor_estimado", "homologado": "valor_homologado"}.get(f.tipo_valor),
        referencia_temporal=referencia,
        termos_objeto=tuple(structured_db.normalizar(t) for t in intent.termos_objeto) if tabela == "contratacoes" else ())


# --------------------------------------------------------------------------
# Execução (SQL com "?" como marcador; Banco.consultar adapta ao Postgres)
# --------------------------------------------------------------------------


def _coluna_valor(consulta: Consulta) -> str:
    if consulta.tabela == "pca_planos":
        return "valor_total"
    return consulta.campo or ("valor_contratado" if consulta.so_contratos or consulta.referencia_temporal == "assinatura" else "valor")


def _where(consulta: Consulta, termos: Iterable[str], juncao: str = "AND") -> tuple[str, list[Any]]:
    condicoes: list[str] = []
    parametros: list[Any] = []
    coluna_ano = "ano_pca" if consulta.tabela == "pca_planos" else (
        "ano_assinatura" if consulta.referencia_temporal == "assinatura" else "ano_publicacao")
    if consulta.tabela == "contratacoes" and (
        consulta.so_contratos or consulta.referencia_temporal == "assinatura" or consulta.campo == "valor_contratado"
    ):
        condicoes.append("tipo_registro = ?")
        parametros.append("contrato_assinado")
    if consulta.ano is not None:
        fim = consulta.ano_final or consulta.ano
        if fim == consulta.ano:
            condicoes.append(f"{coluna_ano} = ?")
            parametros.append(consulta.ano)
        else:
            condicoes.append(f"{coluna_ano} >= ? AND {coluna_ano} <= ?")
            parametros.extend((consulta.ano, fim))
    if consulta.ano_desde is not None:
        condicoes.append(f"{coluna_ano} >= ?")
        parametros.append(consulta.ano_desde)
    if consulta.ano_ate is not None:
        condicoes.append(f"{coluna_ano} <= ?")
        parametros.append(consulta.ano_ate)

    if consulta.valor_min is not None:
        condicoes.append(f"{_coluna_valor(consulta)} {'>' if consulta.valor_min_exclusivo else '>='} ?")
        parametros.append(consulta.valor_min)
    if consulta.valor_max is not None:
        condicoes.append(f"{_coluna_valor(consulta)} {'<' if consulta.valor_max_exclusivo else '<='} ?")
        parametros.append(consulta.valor_max)
    if consulta.esfera:
        condicoes.append("esfera = ?")
        parametros.append(consulta.esfera)
    if consulta.numero_controle_pncp:
        condicoes.append("numero_controle_pncp = ?")
        parametros.append(consulta.numero_controle_pncp)
    if consulta.mes is not None and consulta.ano is not None and consulta.tabela == "contratacoes":
        coluna_mes = "mes_assinatura" if consulta.referencia_temporal == "assinatura" else "mes_publicacao"
        condicoes.append(f"{coluna_mes} = ?")
        parametros.append(f"{consulta.ano}-{consulta.mes:02d}")
    for coluna_valores, valores in (
        (("modalidade",), consulta.modalidades),
        (("orgao_nome", "unidade_nome"), consulta.orgaos),
    ):
        if valores is None:
            continue
        if not valores:  # pedido que não casou com nada do banco: nenhum registro
            condicoes.append("1 = 0")
            continue
        marcadores = ", ".join("?" for _ in valores)
        condicoes.append("(" + " OR ".join(f"{coluna} IN ({marcadores})" for coluna in coluna_valores) + ")")
        for _coluna in coluna_valores:
            parametros.extend(valores)
    if consulta.deduplicar and consulta.tabela == "contratacoes":
        condicoes.append(_SEM_DUPLICATA_MODALIDADE if _usa_modalidade(consulta) else _SEM_DUPLICATA)
    for campo, valor in consulta.filtros_analiticos:
        if campo == "cnpj":
            # A pergunta cita o CNPJ sem dizer se é do órgão ou do fornecedor.
            colunas = [c for c in ("orgao_cnpj", "fornecedor_cnpj") if c in structured_db._TABELAS[consulta.tabela]]
            condicoes.append("(" + " OR ".join(f"{coluna} = ?" for coluna in colunas) + ")")
            parametros.extend(valor for _ in colunas)
            continue
        if campo not in structured_db._TABELAS[consulta.tabela]:
            raise ValueError("Campo SQL não permitido")
        if campo in {"orgao_nome", "fornecedor"}:
            condicoes.append(f"LOWER({campo}) LIKE LOWER(?)")
            parametros.append(f"%{valor}%")
        else:
            condicoes.append(f"{campo} = ?")
            parametros.append(valor)
    termos = list(termos)
    if termos:
        partes = []
        for termo in termos:
            padroes = _EXPANSOES_DO_OBJETO.get(termo, (termo,))
            partes.append("(" + " OR ".join("objeto_normalizado LIKE ?" for _ in padroes) + ")")
            parametros.extend(f"%{padrao}%" for padrao in padroes)
        condicoes.append("(" + f" {juncao} ".join(partes) + ")")
    return (" WHERE " + " AND ".join(condicoes)) if condicoes else "", parametros


# Termo da pergunta que, solto no objeto, pega assunto errado: "tecnologia"
# casa com "transferência de tecnologia do medicamento", "cateteres de
# tecnologia IVUS" e editais que só citam tecnologia de passagem. Na pergunta,
# quer dizer TI; procura-se pelas expressões de TI.
_EXPANSOES_DO_OBJETO: dict[str, tuple[str, ...]] = {
    "tecnologia": (
        "tecnologia da informacao", "tecnologia de informacao", "informatica", "software",
        " tic ", "(tic)", "em tic", "(ti)", " ti ",
    ),
}


# Quando a consulta mistura compras e contratos, a contratação que tem os dois
# registros conta uma vez: fica o contrato (valor contratado e fornecedor), ou,
# se a pergunta é de modalidade, a compra (o contrato do PNCP não traz modalidade).
_SEM_DUPLICATA = (
    "NOT (tipo_registro = 'publicacao_compra' AND numero_controle_pncp IN "
    "(SELECT numero_controle_compra FROM contratacoes WHERE numero_controle_compra IS NOT NULL))"
)
_SEM_DUPLICATA_MODALIDADE = (
    "NOT (tipo_registro = 'contrato_assinado' AND numero_controle_compra IN "
    "(SELECT numero_controle_pncp FROM contratacoes))"
)


def _usa_modalidade(consulta: Consulta) -> bool:
    return bool(consulta.modalidade_termos) or (
        consulta.tipo == "agrupamento" and "modalidade" in (consulta.agrupamentos or (consulta.agrupar_por,)))


def _descrever_termo(termo: str) -> str:
    return f"{termo} (TI: tecnologia da informação, informática, software, TIC)" if termo == "tecnologia" else termo


def _brl(valor: Optional[float]) -> str:
    if valor is None:
        return "sem valor"
    return "R$ " + f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _resumir(texto: Any, limite: int) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= limite else texto[:limite].rstrip() + "…"


# Abaixo disso o valor é simbólico (R$ 0,00 ou R$ 0,01 de registro de preços),
# não o valor do contrato: fica fora de média, mediana e ranking.
_VALOR_MINIMO_INFORMADO = 1.0


def _tem_valor(valor: Any) -> bool:
    return valor is not None and float(valor) >= _VALOR_MINIMO_INFORMADO


def _estatisticas(valores: list[float]) -> str:
    if not valores:
        return "0 registros com valor; total não informado"
    # Somar campos registrados não é imputar valores: zeros/centavos contam
    # no total literal, mas não na estatística dos valores informativos.
    total = sum((Decimal(str(v)) for v in valores), Decimal(0))
    informativos = [v for v in valores if _tem_valor(v)]
    prefixo = f"{len(valores)} registros; total {_brl(total)}"
    if len(informativos) != len(valores):
        prefixo += f"; estatísticas sobre {len(informativos)} valores >= R$ 1,00"
    if not informativos:
        return prefixo + "; média, mediana, mínimo e máximo não informados"
    return (
        f"{prefixo}; média {_brl(statistics.fmean(informativos))}; "
        f"mediana {_brl(statistics.median(informativos))}; mínimo {_brl(min(informativos))}; máximo {_brl(max(informativos))}"
    )


def _comparacao_esferas_insuficiente(consulta: Consulta, por_esfera: dict[str, list[float]]) -> Optional[str]:
    """Motivo, quando a comparação entre esferas não tem duas esferas com valor informativo.

    Valor simbólico (< R$ 1,00) não entra na estatística, então uma esfera só
    com R$ 0,00 não sustenta comparação, como uma esfera sem registros.
    """
    if not consulta.comparacao_esferas:
        return None
    contagens = {esfera: sum(_tem_valor(v) for v in valores) for esfera, valores in sorted(por_esfera.items())}
    if sum(1 for n in contagens.values() if n) >= 2:
        return None
    detalhe = "; ".join(f"{esfera}: {n} valores >= R$ 1,00" for esfera, n in contagens.items()) or "nenhuma esfera com valor registrado"
    return ("Dados insuficientes: não é possível comparar as esferas neste recorte, porque menos de duas "
            f"têm registros com valor >= R$ 1,00 ({detalhe}).")


def _executar(banco: Banco, consulta: Consulta, termos: tuple[str, ...], juncao: str) -> tuple[str, list[str]]:
    where, parametros = _where(consulta, termos, juncao)
    coluna_valor = _coluna_valor(consulta)

    if consulta.tipo == "identificador":
        sql = (
            "SELECT numero_controle_pncp, data_assinatura, orgao_nome, municipio, uf, "
            f"fornecedor, objeto, valor_contratado FROM {consulta.tabela}{where} LIMIT 1"
        )
        linhas = banco.consultar(sql, parametros)
        return sql, [
            f"PNCP {linha['numero_controle_pncp']} | órgão: {linha['orgao_nome']} | "
            f"fornecedor: {linha['fornecedor'] or 'não informado'} | "
            f"valor contratado: {_brl(linha['valor_contratado'])} | "
            f"assinatura: {linha['data_assinatura'] or 'não informada'} | "
            f"{_resumir(linha['objeto'], 220)}"
            for linha in linhas
        ] or [f"Nenhum contrato encontrado para o controle PNCP {consulta.numero_controle_pncp}."]

    if consulta.tipo == "identificador":
        sql = ("SELECT numero_controle_pncp, data_assinatura, orgao_nome, municipio, uf, "
               f"fornecedor, objeto, valor_contratado FROM {consulta.tabela}{where} LIMIT 1")
        linhas = banco.consultar(sql, parametros)
        return sql, [
            f"PNCP {linha['numero_controle_pncp']} | órgão: {linha['orgao_nome']} | "
            f"fornecedor: {linha['fornecedor'] or 'não informado'} | "
            f"valor contratado: {_brl(linha['valor_contratado'])} | "
            f"assinatura: {linha['data_assinatura'] or 'não informada'} | "
            f"{_resumir(linha['objeto'], 220)}" for linha in linhas
        ] or [f"Nenhum contrato encontrado para o controle PNCP {consulta.numero_controle_pncp}."]

    if consulta.tipo in ("ranking", "listagem"):
        # Listagem mostra também registros sem valor (depois dos com valor) e
        # diz quantos casaram no total.
        where_valor = where if consulta.tipo == "listagem" else (
            where + (" AND " if where else " WHERE ") + f"{coluna_valor} >= {_VALOR_MINIMO_INFORMADO}")
        if consulta.tabela == "pca_planos":
            colunas = "ano_pca, unidade_nome, quantidade_itens, valor_total AS valor"
        else:
            coluna_data = "data_assinatura" if consulta.referencia_temporal == "assinatura" else "data_publicacao"
            if consulta.tipo == "listagem":
                colunas = f"numero_controle_pncp, {coluna_data} AS data_referencia, orgao_nome, esfera, municipio, modalidade, fornecedor, objeto, {coluna_valor} AS valor"
            else:
                colunas = f"{coluna_data} AS data_referencia, orgao_nome, esfera, municipio, modalidade, fornecedor, objeto, {coluna_valor} AS valor"
        ordem = (f"CASE WHEN {coluna_valor} IS NULL THEN 1 ELSE 0 END, {coluna_valor} {consulta.ordem}"
                 if consulta.tipo == "listagem" else f"{coluna_valor} {consulta.ordem}")
        sql = (
            f"SELECT {colunas} FROM {consulta.tabela}{where_valor} "
            f"ORDER BY {ordem} LIMIT {consulta.limite}"
        )
        linhas = banco.consultar(sql, parametros)
        if consulta.tipo == "listagem" and consulta.tabela == "contratacoes":
            total = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}", parametros)[0]["n"]
            return sql, [f"{total} registros atendem aos filtros; os {len(linhas)} de maior valor:"] + [
                f"{i}. PNCP {l['numero_controle_pncp']} | {_brl(l['valor'])} | {l['data_referencia']} | {l['orgao_nome']} ({l['esfera']}, {l['municipio']}) | "
                f"modalidade: {l['modalidade'] or 'não informada'} | fornecedor: {l['fornecedor'] or 'não informado'} | "
                # 180: o objeto é o que identifica o contrato; com 70, "remoção de
                # pacientes em ambulância tipo D" virava "remoção de pacien…".
                f"{_resumir(l['objeto'], 180)}"
                for i, l in enumerate(linhas, 1)
            ]
        if consulta.tabela == "pca_planos":
            return sql, [
                f"{i}. {_brl(l['valor'])} | PCA {l['ano_pca']} | {l['unidade_nome']} | {l['quantidade_itens']} itens"
                for i, l in enumerate(linhas, 1)
            ]
        return sql, [
            f"{i}. {_brl(l['valor'])} | {l['data_referencia']} | {l['orgao_nome']} ({l['esfera']}, {l['municipio']}) | "
            f"{l['modalidade']} | {_resumir(l['objeto'], 60)}"
            for i, l in enumerate(linhas, 1)
        ]

    if consulta.tipo == "agrupamento":
        campos = consulta.agrupamentos or (consulta.agrupar_por,)
        if any(c not in structured_db._TABELAS[consulta.tabela] for c in campos):
            raise ValueError("Agrupamento SQL não permitido")
        coluna = ", ".join(campos)
        # Fornecedor/modalidade ausente não é um grupo; conta-se à parte.
        sem_campo = 0
        obrigatorios = [c for c in campos if c in {"fornecedor", "modalidade"}]
        if obrigatorios:
            nulos = " OR ".join(f"{c} IS NULL" for c in obrigatorios)
            where_nulo = where + (" AND " if where else " WHERE ") + f"({nulos})"
            sem_campo = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where_nulo}", parametros)[0]["n"]
            where += (" AND " if where else " WHERE ") + " AND ".join(f"{c} IS NOT NULL" for c in obrigatorios)
        if consulta.metrica == "mediana":
            sql = f"SELECT {coluna}, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
            valores_por_grupo: dict[tuple, list[float]] = {}
            contagens: dict[tuple, int] = {}
            for row in banco.consultar(sql, parametros):
                key = tuple(row[c] for c in campos)
                contagens[key] = contagens.get(key, 0) + 1
                if row["valor"] is not None:
                    valores_por_grupo.setdefault(key, []).append(float(row["valor"]))
            items = [(k, v) for k, v in valores_por_grupo.items()
                     if contagens[k] >= (consulta.minimo_por_grupo or 1) and any(_tem_valor(n) for n in v)]
            items.sort(key=lambda kv: statistics.median([n for n in kv[1] if _tem_valor(n)]),
                       reverse=consulta.ordem == "DESC")
            grupos = [f"{' / '.join(str(k or 'não informado') for k in key)}: {_estatisticas(values)}"
                      for key, values in items[:consulta.limite]]
        else:
            valor_informado = f"CASE WHEN {coluna_valor} >= {_VALOR_MINIMO_INFORMADO} THEN {coluna_valor} END"
            funcao = {"soma": "SUM", "media": "AVG", "minimo": "MIN", "maximo": "MAX"}.get(consulta.metrica)
            expressao_ordem = coluna_valor if consulta.metrica == "soma" else valor_informado
            ordem = (f"{funcao}({expressao_ordem}) {consulta.ordem}, n DESC"
                     if funcao else f"n DESC, COALESCE(SUM({coluna_valor}), 0) DESC")
            sql = (
                f"SELECT {coluna}, COUNT(*) AS n, SUM({coluna_valor}) AS total, "
                f"AVG({valor_informado}) AS media, MIN({valor_informado}) AS minimo, MAX({valor_informado}) AS maximo "
                f"FROM {consulta.tabela}{where} GROUP BY {coluna}"
            )
            if consulta.minimo_por_grupo is not None:
                sql += " HAVING COUNT(*) >= ?"
                parametros.append(consulta.minimo_por_grupo)
            sql += f" ORDER BY {ordem}"
            if consulta.plano_tipado or consulta.metrica != "contagem":
                sql += f" LIMIT {consulta.limite}"
            grupos = [
                f"{'Fornecedor ' if coluna == 'fornecedor' else ''}{' / '.join(str(l[c] or 'não informado') for c in campos)}: "
                f"{l['n']} registros, total {_brl(l['total'])}, média {_brl(l['media'])}, "
                f"mínimo {_brl(l['minimo'])}, máximo {_brl(l['maximo'])}"
                for l in banco.consultar(sql, parametros)
            ]
        if not grupos and consulta.minimo_por_grupo is not None:
            grupos = [f"Nenhum {coluna} tem {consulta.minimo_por_grupo} ou mais registros com esses filtros."]
        if sem_campo and not grupos:
            # Sem nenhum grupo, a resposta é "não dá para saber", não um valor de
            # outro trecho do contexto (P4.2: "pregão" tirado de um item do PCA).
            motivo = " (os contratos assinados no PNCP não trazem modalidade)" if coluna == "modalidade" else ""
            grupos = [f"Nenhum dos {sem_campo} registros que atendem aos filtros informa {coluna}{motivo}: "
                      f"com estes dados não é possível dizer qual {coluna} é a mais frequente."]
        elif sem_campo:
            grupos.append(f"({sem_campo} registros sem informação de {coluna} ficaram fora dos grupos.)")
        return sql, grupos

    if consulta.tipo == "evolucao":
        coluna_periodo = "ano_pca" if consulta.tabela == "pca_planos" else (
            "mes_assinatura" if consulta.referencia_temporal == "assinatura" else "mes_publicacao")
        sql = f"SELECT {coluna_periodo} AS periodo, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
        por_periodo: dict[Any, list[float]] = {}
        for l in banco.consultar(sql, parametros):
            if l["valor"] is not None:
                por_periodo.setdefault(l["periodo"], []).append(float(l["valor"]))
        conhecidos = sorted((p, v) for p, v in por_periodo.items() if p is not None)
        linhas = [f"{p}: {_estatisticas(v)}" for p, v in conhecidos]
        todos = [v for _, valores in conhecidos for v in valores]
        if todos:
            linhas.insert(0, f"Período observado (média ponderada por registro): {_estatisticas(todos)}")
        periodos_informativos = [(p, [v for v in valores if _tem_valor(v)])
                                 for p, valores in conhecidos]
        periodos_informativos = [(p, v) for p, v in periodos_informativos if v]
        if len(periodos_informativos) >= 2:
            inicio, fim = periodos_informativos[0], periodos_informativos[-1]
            a, b = statistics.fmean(inicio[1]), statistics.fmean(fim[1])
            delta = b - a
            percentual = f"{100 * delta / a:.2f}%" if a else "percentual indefinido (base zero)"
            linhas.insert(1, f"Comparação primeiro/último mês disponível ({inicio[0]} / {fim[0]}): variação {_brl(delta)}; {percentual}. Não prova tendência monotônica.")
        if consulta.tabela == "contratacoes" and consulta.ano is not None:
            expected = {f"{y}-{m:02d}" for y in range(consulta.ano, (consulta.ano_final or consulta.ano) + 1) for m in range(1, 13)}
            missing = sorted(expected - set(p for p, _ in conhecidos))
            if missing:
                linhas.append("Meses sem registros com valor: " + ", ".join(missing) + "; não são valores zero.")
        return sql, linhas

    if consulta.plano_tipado and consulta.metrica == "contagem":
        sql = f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}"
        return sql, [f"Contagem no recorte solicitado: {banco.consultar(sql, parametros)[0]['n']} registros (inclui registros sem valor)."]

    # estatistica: geral e, nas contratações, quebrada por esfera. Mediana em
    # Python porque SQLite não tem função de mediana.
    coluna_esfera = "esfera" if consulta.tabela == "contratacoes" else "NULL"
    sql = f"SELECT {coluna_esfera} AS esfera, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
    todas = banco.consultar(sql, parametros)
    # Contagem inclui ausentes; soma inclui todo valor registrado. Somente as
    # demais estatísticas excluem valores simbólicos, sem chamá-los de ausentes.
    ausentes = sum(l["valor"] is None for l in todas)
    simbolicos = sum(l["valor"] is not None and not _tem_valor(l["valor"]) for l in todas)
    geral = f"Geral: {_estatisticas([float(l['valor']) for l in todas if l['valor'] is not None])}"
    if ausentes or simbolicos:
        qualificacao = f"{len(todas)} registros no total"
        if ausentes:
            qualificacao += f", {ausentes} sem valor informado"
        if simbolicos:
            qualificacao += f", {simbolicos} com valor abaixo de R$ 1,00"
        geral = f"Geral: {qualificacao}; valores registrados: " + geral[7:]
    resultado = [geral]
    if consulta.tabela == "contratacoes":
        por_esfera: dict[str, list[float]] = {}
        for l in todas:
            if l["valor"] is not None:
                por_esfera.setdefault(l["esfera"] or "não informada", []).append(float(l["valor"]))
        resultado += [f"Esfera {esfera}: {_estatisticas(v)}" for esfera, v in sorted(por_esfera.items())]
        insuficiente = _comparacao_esferas_insuficiente(consulta, por_esfera)
        if insuficiente:
            resultado.append(insuficiente)
    return sql, resultado


def _cabecalho(consulta: Consulta, total_tabela: int, termos: tuple[str, ...], juncao: str) -> str:
    if consulta.tabela == "pca_planos":
        origem = (
            f"Consulta SQL na tabela pca_planos: {total_tabela} plano(s) de contratações anual (PCA) de unidades do RJ presentes no corpus. "
            "É a soma só desses planos, NÃO o valor total do PCA do Estado."
        )
    else:
        origem = (
            f"Consulta SQL na tabela contratacoes: {total_tabela} registros da amostra PNCP/RJ. "
            "Contratos: valorGlobal; compras: homologado ou estimado. Valores informados, não pagamentos."
        )
        if consulta.pagamento_citado:
            origem += (" A pergunta fala em pagamento, mas o PNCP não registra pagamentos: o valor"
                       " abaixo é o contratado ou informado, não o que foi efetivamente pago.")
        if consulta.norma_citada:
            origem += (" A norma citada na pergunta NÃO é filtro desta conta: os contratos assinados do PNCP"
                       " não trazem o amparo legal, então o resultado vale para todos os registros com os"
                       " demais filtros, e não só para os que seguem essa norma.")
        if consulta.deduplicar:
            origem += (" Contratação com compra e contrato no corpus conta uma vez"
                       + (" (pela compra, que traz a modalidade)." if _usa_modalidade(consulta) else " (pelo contrato)."))
    filtros = []
    if consulta.ano is not None:
        fim = consulta.ano_final or consulta.ano
        filtros.append(f"ano={consulta.ano}" if fim == consulta.ano else f"ano de {consulta.ano} a {fim}")
    if consulta.ano_desde is not None:
        filtros.append(f"ano>={consulta.ano_desde}")
    if consulta.ano_ate is not None:
        filtros.append(f"ano<={consulta.ano_ate}")

    if consulta.valor_min is not None:
        filtros.append(f"valor{'>' if consulta.valor_min_exclusivo else '>='}{_brl(consulta.valor_min)}")
    if consulta.valor_max is not None:
        filtros.append(f"valor{'<' if consulta.valor_max_exclusivo else '<='}{_brl(consulta.valor_max)}")
    if consulta.esfera:
        filtros.append(f"esfera={consulta.esfera}")
    if consulta.mes is not None and consulta.ano is not None:
        filtros.append(f"mês={consulta.mes:02d}/{consulta.ano}")
    if consulta.minimo_por_grupo is not None:
        filtros.append(f"só {consulta.agrupar_por} com {consulta.minimo_por_grupo} ou mais registros")
    if consulta.modalidades is not None:
        filtros.append(
            f"modalidade em {', '.join(consulta.modalidades)}" if consulta.modalidades
            else f"modalidade {' '.join(consulta.modalidade_termos)} (nenhum registro do corpus tem essa modalidade)"
        )
    if consulta.orgaos is not None:
        filtros.append(
            f"órgão em {', '.join(consulta.orgaos[:5])}{'…' if len(consulta.orgaos) > 5 else ''}" if consulta.orgaos
            else f"órgão {' '.join(consulta.orgao_termos)} (nenhum órgão do corpus casa com esse nome)"
        )
    filtros.extend(
        f"{k}={v} (dígito verificador inválido: confira o número)"
        if k == "cnpj" and not structured_db.cnpj_valido(str(v)) else f"{k}={v}"
        for k, v in consulta.filtros_analiticos
    )
    if consulta.referencia_temporal:
        filtros.append(f"referência={consulta.referencia_temporal}")
    if consulta.campo:
        filtros.append(f"campo_valor={consulta.campo}")
    if consulta.tabela == "contratacoes" and (
        consulta.so_contratos or consulta.referencia_temporal == "assinatura" or consulta.campo == "valor_contratado"
    ):
        filtros.append("tipo_registro=contrato_assinado")
    if termos:
        filtros.append(f"objeto contém {(' e ' if juncao == 'AND' else ' ou ').join(map(_descrever_termo, termos))}")
    return f"{origem} Filtros: {', '.join(filtros) or 'nenhum'}. Cálculo: {consulta.tipo}."


def _palavras_casam(pedidas: Iterable[str], nome: str) -> bool:
    """Toda palavra pedida casa com alguma do nome, por prefixo nos dois sentidos:
    "secretaria saude" casa com "SECRETARIA DE ESTADO DE SAUDE" e com "SECRET ESTADO SAUDE"."""
    palavras = re.findall(r"[a-z]+", structured_db.normalizar(nome))
    return all(
        any(p.startswith(pedida) or (len(p) >= 4 and pedida.startswith(p)) for p in palavras)
        for pedida in pedidas
    )


def _orgao_casa(pedidas: tuple[str, ...], nome: str) -> bool:
    """O nome do banco é do órgão pedido: casa palavra a palavra ("prefeitura"
    vale também como "município") e não tem qualificador que a pergunta não diz."""
    palavras = set(re.findall(r"[a-z]+", structured_db.normalizar(nome)))
    if (palavras & _QUALIFICADORES_DE_ORGAO) - set(pedidas):
        return False
    variantes = [pedidas]
    if "prefeitura" in pedidas:
        variantes.append(tuple("municipio" if p == "prefeitura" else p for p in pedidas))
    return any(_palavras_casam(variante, nome) for variante in variantes)


def _resolver_valores(consulta: Consulta, banco: Banco) -> Consulta:
    """Troca os termos de modalidade e de órgão pelos valores do banco que casam.

    A comparação é feita aqui, sem acento e sem caixa, para valer igual no
    SQLite e no Postgres; o SQL recebe só ``coluna IN (...)``.
    """
    mudancas: dict[str, Any] = {}
    if consulta.tabela == "contratacoes" and not (
        consulta.so_contratos or consulta.referencia_temporal == "assinatura" or consulta.campo == "valor_contratado"
    ):
        try:  # base montada antes da coluna (ex.: Neon só de leitura desatualizado): sem deduplicar
            banco.consultar("SELECT numero_controle_compra FROM contratacoes LIMIT 1")
            mudancas["deduplicar"] = True
        except Exception:
            pass
    if consulta.modalidade_termos and consulta.tabela == "contratacoes":
        valores = banco.consultar(
            "SELECT DISTINCT modalidade AS v FROM contratacoes WHERE modalidade IS NOT NULL"
        )
        mudancas["modalidades"] = tuple(sorted(
            l["v"] for l in valores
            if all(t in structured_db.normalizar(l["v"]) for t in consulta.modalidade_termos)
        ))
    # Órgão que o query_analysis não reconheceu (minúsculas, "Fundo", "Comando"…)
    # chega como termos do objeto; com duas palavras ou mais que formam o nome de
    # um órgão do banco ("comando aeronautica"), vira filtro de órgão.
    termos_como_orgao = (
        not consulta.orgao_termos and len(consulta.termos_objeto) >= 2 and consulta.tabela == "contratacoes"
    )
    if consulta.orgao_termos or termos_como_orgao:
        nomes: set[str] = set()
        for coluna in ("orgao_nome", "unidade_nome"):
            nomes |= {
                l["v"] for l in banco.consultar(
                    f"SELECT DISTINCT {coluna} AS v FROM {consulta.tabela} WHERE {coluna} IS NOT NULL"
                )
            }
        if consulta.orgao_termos:
            mudancas["orgaos"] = tuple(sorted(n for n in nomes if _orgao_casa(consulta.orgao_termos, n)))
        else:
            casados = tuple(sorted(n for n in nomes if _orgao_casa(consulta.termos_objeto, n)))
            if casados:
                mudancas.update(orgaos=casados, orgao_termos=consulta.termos_objeto, termos_objeto=())
    return replace(consulta, **mudancas) if mudancas else consulta


def executar_consulta(consulta: Consulta, banco: Banco, recorte: Optional[dict] = None) -> tuple[str, str, list[str]]:
    """Roda a consulta e devolve (sql, cabeçalho, linhas de resultado).

    Com ``recorte`` (dict vazio), preenche as condições da evidência —
    snapshot, denominador, correspondências, execução completa/parcial,
    filtros relaxados, completude por campo e tipo de resultado — para quem
    montar a resposta não precisar deduzir nada lendo o texto gerado.

    Termos do objeto são tentados todos com E. Se nada casa, ficam só os termos
    que casam com algum registro (com os demais filtros), de novo com E; se
    ainda nada, esses mesmos termos com OU ("ambulâncias e pavimentação" é a
    união dos dois assuntos). Termos que não casam com nada ficam de fora do
    OU, para uma palavra solta da pergunta não trazer registros de outro
    assunto. Se nenhum termo casa, o resultado diz que nenhuma contratação do
    corpus tem esse objeto, em vez de calcular sobre contratações de outro assunto.
    """
    total = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}")[0]["n"]
    consulta = _resolver_valores(consulta, banco)

    def contagem(termos: tuple[str, ...], juncao: str = "AND") -> int:
        where, parametros = _where(consulta, termos, juncao)
        return banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}", parametros)[0]["n"]

    tentativas = [(consulta.termos_objeto, "AND")]
    if len(consulta.termos_objeto) > 1:
        uteis = tuple(termo for termo in consulta.termos_objeto if contagem((termo,)))
        if uteis and uteis != consulta.termos_objeto:
            tentativas.append((uteis, "AND"))
        if len(uteis) > 1:
            tentativas.append((uteis, "OR"))
    for indice, (termos, juncao) in enumerate(tentativas):
        if not termos or contagem(termos, juncao):
            sql, linhas = _executar(banco, consulta, termos, juncao)
            if recorte is not None:
                _preencher_recorte(recorte, consulta, banco, total, contagem(termos, juncao),
                                   termos, juncao, relaxados=_relaxamentos(indice, consulta, termos, juncao))
            return sql, _cabecalho(consulta, total, termos, juncao), linhas
    where, _ = _where(consulta, consulta.termos_objeto, "OR")
    if recorte is not None:
        # Nada casou, mesmo com os termos relaxados: é uma conclusão exaustiva
        # sobre o recorte, não uma falha de execução.
        _preencher_recorte(recorte, consulta, banco, total, 0,
                           consulta.termos_objeto, "OR", relaxados=_relaxamentos(len(tentativas), consulta, consulta.termos_objeto, "OR"))
    return (
        f"SELECT 1 FROM {consulta.tabela}{where}",
        _cabecalho(consulta, total, consulta.termos_objeto, "OR"),
        [f"Nenhum registro encontrado com os filtros aplicados e os termos de objeto: {', '.join(consulta.termos_objeto)}."],
    )


# Campos numéricos: ausente é NULL. Nos de texto, string vazia também é ausente.
_CAMPOS_NUMERICOS = frozenset({
    "valor", "valor_estimado", "valor_homologado", "valor_contratado", "valor_total", "quantidade_itens",
})


def _relaxamentos(tentativa: int, consulta: Consulta, termos: tuple[str, ...], juncao: str) -> list[str]:
    """O que foi afrouxado para chegar a este resultado; vazio = execução completa."""
    if tentativa <= 0:
        return []
    motivos = []
    if termos != consulta.termos_objeto:
        fora = [t for t in consulta.termos_objeto if t not in termos]
        if fora:
            motivos.append("termos de objeto ignorados: " + ", ".join(fora))
    if juncao != "AND" and len(termos) > 1:
        motivos.append("termos de objeto unidos com OU em vez de E")
    return motivos or ["termos de objeto relaxados"]


def _snapshot_do_banco(banco: Banco) -> tuple[Optional[str], Optional[str]]:
    """(versão do esquema, hash da origem) registrados na tabela ``meta``."""
    try:
        linhas = banco.consultar("SELECT chave, valor FROM meta")
    except Exception:
        return None, None
    valores = {str(l["chave"]): l["valor"] for l in linhas}
    return valores.get("schema_version"), valores.get("source_sha256")


def _completude(banco: Banco, tabela: str, where: str, parametros: list[Any],
                campos: Sequence[str]) -> list[dict[str, Any]]:
    """Conhecidos/desconhecidos por campo, sobre o recorte já filtrado."""
    saida: list[dict[str, Any]] = []
    for campo in campos:
        vazio = f"{campo} IS NULL" if campo in _CAMPOS_NUMERICOS else f"({campo} IS NULL OR {campo} = '')"
        linha = banco.consultar(
            f"SELECT COUNT(*) AS n, SUM(CASE WHEN {vazio} THEN 1 ELSE 0 END) AS d FROM {tabela}{where}",
            parametros)[0]
        total_campo = int(linha["n"] or 0)
        desconhecidos = int(linha["d"] or 0)
        saida.append({"campo": campo, "conhecidos": total_campo - desconhecidos, "desconhecidos": desconhecidos})
    return saida


def _preencher_recorte(recorte: dict[str, Any], consulta: Consulta, banco: Banco, total: int,
                       correspondentes: int, termos: tuple[str, ...], juncao: str,
                       relaxados: list[str]) -> None:
    """Preenche o recorte em ``recorte``, validando campo contra as colunas reais."""
    from src.schema import CampoCompletude, RecorteConsulta, TipoResultado

    where, parametros = _where(consulta, termos, juncao)
    colunas = set(structured_db._TABELAS[consulta.tabela])
    campos = [c for c in dict(consulta.filtros_analiticos) if c in colunas]
    # O campo de valor é o que a conta realmente usa (_coluna_valor), não só o
    # pedido explicitamente: senão a completude fica vazia em conta padrão.
    valor_col = _coluna_valor(consulta)
    if valor_col in colunas and valor_col not in campos:
        campos.append(valor_col)
    completude = _completude(banco, consulta.tabela, where, parametros, campos)

    # Tipo de resultado: zero registros, soma zero e cálculo indisponível são
    # coisas diferentes e não podem virar o mesmo texto.
    tipo = TipoResultado.REGISTROS
    motivo: Optional[str] = None
    disponivel = True
    if not correspondentes:
        tipo = TipoResultado.SEM_CORRESPONDENCIA
    elif consulta.tipo in {"estatistica", "agrupamento", "evolucao"} and consulta.metrica in {
        "soma", "media", "mediana", "minimo", "maximo",
    }:
        valor_col = _coluna_valor(consulta) if consulta.tabela == "contratacoes" else "valor_total"
        if valor_col not in colunas:
            disponivel, tipo = False, TipoResultado.CALCULO_INDISPONIVEL
            motivo = f"coluna de valor {valor_col} fora do esquema"
        else:
            linha = banco.consultar(
                f"SELECT COUNT({valor_col}) AS conhecidos FROM {consulta.tabela}{where}", parametros)[0]
            if not int(linha["conhecidos"] or 0):
                disponivel, tipo = False, TipoResultado.CALCULO_INDISPONIVEL
                motivo = "nenhum registro do recorte tem o campo de valor preenchido"
            elif consulta.metrica == "soma":
                soma = banco.consultar(f"SELECT SUM({valor_col}) AS s FROM {consulta.tabela}{where}", parametros)[0]["s"]
                if float(soma or 0) == 0.0:
                    tipo = TipoResultado.SOMA_ZERO

    # Comparação entre esferas sem duas esferas com valor: a conta existe, mas
    # não sustenta a comparação pedida.
    if (consulta.comparacao_esferas and correspondentes
            and tipo in {TipoResultado.REGISTROS, TipoResultado.SOMA_ZERO}):
        por_esfera: dict[str, list[float]] = {}
        for linha in banco.consultar(
                f"SELECT esfera, {_coluna_valor(consulta)} AS valor FROM {consulta.tabela}{where}", parametros):
            if linha["valor"] is not None:
                por_esfera.setdefault(linha["esfera"] or "não informada", []).append(float(linha["valor"]))
        insuficiente = _comparacao_esferas_insuficiente(consulta, por_esfera)
        if insuficiente:
            tipo, motivo = TipoResultado.COMPARACAO_INSUFICIENTE, insuficiente

    versao, hash_origem = _snapshot_do_banco(banco)
    modelo = RecorteConsulta(
        tabela=consulta.tabela,
        snapshot_versao=versao,
        snapshot_hash=hash_origem,
        registros_no_snapshot=int(total),
        registros_correspondentes=int(correspondentes),
        execucao="parcial" if relaxados else "completa",
        filtros_relaxados=list(relaxados),
        calculo=consulta.tipo,
        calculo_disponivel=disponivel,
        calculo_motivo=motivo,
        tipo_resultado=tipo,
        completude=[CampoCompletude(**c) for c in completude],
    )
    recorte.update(modelo.model_dump(mode="json"))


_POLITICA_LISTAGEM = ("Campo sem valor aparece como \"não informado\"; valor de R$ 0,00 é o que "
                      "o registro traz, não um campo ausente. Esta amostra não é um censo do RJ.")


def _chunks(consulta: Consulta, sql: str, cabecalho: str, linhas: list[str], banco: Banco,
            recorte: Optional[dict[str, Any]] = None) -> list[Chunk]:
    """Resultado em um ou mais chunks que cabem em ``GENERATION_CHUNK_CHARS``
    (a geração corta o que passar disso); o cabeçalho se repete em cada parte."""
    limite = max(300, config.GENERATION_CHUNK_CHARS - 40)
    partes: list[list[str]] = [[]]
    for linha in linhas or ["(nenhum registro)"]:
        cabeca = len(cabecalho) if len(partes) == 1 else 70
        tamanho = cabeca + sum(len(l) + 1 for l in partes[-1]) + len(linha) + 1
        if tamanho > limite and (partes[-1] or len(partes) == 1):
            partes.append([])
        partes[-1].append(linha)
    sem_registros = not linhas or all(
        linha.strip().casefold() == "(nenhum registro)"
        or linha.strip().casefold().startswith("nenhum registro encontrado")
        for linha in linhas)
    assinatura = hashlib.sha256(f"{sql}|{consulta}".encode("utf-8")).hexdigest()[:12]
    base_id = structured_db.BASE_PCA if consulta.tabela == "pca_planos" else structured_db.BASE_CONTRATACOES
    natureza = Natureza.AGREGADA if consulta.tabela == "pca_planos" else Natureza.ESTRUTURADA
    return [
        Chunk(
            content=(cabecalho if indice == 1 else f"(continuação da consulta SQL acima, parte {indice}; mesmos filtros)")
            + "\n"
            + "\n".join(parte),
            metadata=ChunkMetadata(
                source_file=banco.rotulo_publico(),
                chunk_id=f"sql:{consulta.tabela}:{consulta.tipo}:{assinatura}:{indice}",
                base_id=base_id,
                doc_type="sql",
                natureza=natureza,
                chunking_strategy="consulta_sql",
                extras={"sql": sql, "tipo": consulta.tipo, "parte": indice, "partes": len(partes),
                        "resultado_sql": {"cabecalho": cabecalho, "linhas": parte,
                            "politica": _POLITICA_LISTAGEM if consulta.tipo == "listagem" else
                            "Totais somam os valores registrados, inclusive zeros/centavos; "
                            "média, mediana, mínimo e máximo consideram valores >= R$ 1,00. "
                            "Campos ausentes não são zero. Esta amostra não é um censo do RJ.",
                            "recorte": recorte},
                        # Listagem vazia não dispensa a busca: o objeto pode
                        # estar com outra palavra ("UTI móvel"), e a busca
                        # semântica ainda acha; o LLM recebe o zero e os trechos.
                        "geracao_deterministica": (consulta.tipo in {"estatistica", "ranking", "agrupamento", "evolucao"}
                                                   or (consulta.listagem_direta and not sem_registros))
                        and not consulta.norma_citada,
                        # Nada atendeu aos filtros: é ausência na amostra, não um número calculado.
                        "sem_registros": sem_registros},
            ),
        )
        for indice, parte in enumerate(partes, 1)
    ]


def pode_consultar_sql_direto(
    query: str, filtros: QueryFilters, bases: Iterable[Natureza],
    *, intencao: Optional[QueryIntent] = None,
) -> bool:
    """Planejamento sem I/O; só contas que podem dispensar a busca vetorial."""
    bases = list(bases)
    if not bases or not set(bases) <= {Natureza.ESTRUTURADA, Natureza.AGREGADA}:
        return False
    consulta = consulta_da_intencao(intencao) if intencao is not None else detectar_consulta(query, filtros, bases)
    return bool(consulta and (consulta.tipo in {"estatistica", "ranking", "agrupamento", "evolucao"}
                              or consulta.listagem_direta)
                and not consulta.norma_citada)


# Linhas do resultado SQL gravadas no log da etapa ``consulta_sql``.
_LINHAS_NO_LOG = 20


def consultar_estruturado(
    query: str,
    filtros: QueryFilters,
    bases: Iterable[Natureza],
    *,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
    banco: Optional[Banco] = None,
    documents_file: Optional[Path] = None,
    intencao: Optional[QueryIntent] = None,
) -> list[Chunk]:
    """Etapa ``consulta_sql``: devolve chunks com o resultado, ou ``[]``.

    Sem pergunta de conta, ou sem ``documents.jsonl`` na máquina, não roda nem
    registra nada. Erro na consulta (ex.: Neon fora do ar) é registrado no log
    e não derruba o pipeline: a resposta segue só com a busca normal.
    """
    consulta = consulta_da_intencao(intencao) if intencao is not None else detectar_consulta(query, filtros, bases)
    documents_file = Path(documents_file or DOCUMENTS_FILE)
    if consulta is None or not documents_file.exists():
        from src.obs import registrar_pulo
        registrar_pulo(run_id=run_id, query_id=query_id, stage=Stage.CONSULTA_SQL, query=query,
                       motivo="nao_aplicavel" if consulta is None else "corpus_ausente")
        return []
    banco = banco or structured_db.banco_padrao()
    try:
        with default_logger.span(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.CONSULTA_SQL,
            input=query,
            extra={"tabela": consulta.tabela, "tipo": consulta.tipo, "banco": banco.rotulo_publico()},
        ) as span:
            structured_db.garantir_banco(documents_file, banco, status=span.extra)
            with structured_db.observar_consultas() as consultas:
                span.extra["execucao_sql"] = consultas
                recorte: dict[str, Any] = {}
                with banco.sessao():
                    sql, cabecalho, linhas = executar_consulta(consulta, banco, recorte)
            chunks = _chunks(consulta, sql, cabecalho, linhas, banco, recorte)
            span.extra.update({
                "sql": sql, "linhas": len(linhas), "chunks": len(chunks),
                # O que a consulta devolveu, para acompanhar o dado até a resposta.
                "cabecalho": cabecalho,
                "resultado": linhas[:_LINHAS_NO_LOG],
                "linhas_fora_do_log": max(0, len(linhas) - _LINHAS_NO_LOG),
                "chunk_ids": [chunk.metadata.chunk_id for chunk in chunks],
                # Confiabilidade no recorte, completude e cobertura separadas:
                # não se deduz isso lendo o texto da resposta.
                "recorte": recorte,
            })
            from src.log_evidence import auditoria_completa
            if auditoria_completa():
                span.extra["auditoria_evidencias"] = {
                    "modo": "completo", "cabecalho": cabecalho,
                    "resultado_completo": linhas, "chunk_ids": [c.metadata.chunk_id for c in chunks],
                }
            span.set_result(f"{len(linhas)} linha(s) em {len(chunks)} chunk(s)")
            return chunks
    except Exception:
        return []


__all__ = ["Consulta", "consultar_estruturado", "detectar_consulta", "executar_consulta"]
