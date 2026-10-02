"""Consultas SQL fixas sobre a base estruturada, como evidência para a geração.

Perguntas de ranking ("dez maiores contratações por valor"), estatística
("mediana", "valor médio", "quantas"), comparação ("pagaram mais do que"),
evolução ("ao longo de 2025") e agrupamento ("modalidades mais frequentes")
não se resolvem por busca semântica. Aqui a pergunta é classificada por regras
e respondida por uma consulta SQL **pré-escrita e parametrizada** (o LLM nunca
escreve SQL). O resultado vira ``Chunk`` com ``doc_type="sql"``, que
``src/pipeline.py`` põe à frente dos chunks da busca para o LLM citar.

Filtros vêm de ``query_analysis`` (ano, valor) e da própria pergunta (esfera
estadual/municipal, termos do objeto). O banco é o de ``src/structured_db.py``
(Postgres/Neon com ``DATABASE_URL``, senão SQLite local).
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Optional

import config
from src import structured_db
from src.obs import Stage, default_logger
from src.query_analysis import texto_sem_filtros
from src.schema import Chunk, ChunkMetadata, Natureza, QueryFilters
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
    (re.compile(r"\bpregao eletronico\b"), ("pregao", "eletronic")),
    (re.compile(r"\bpregao presencial\b"), ("pregao", "presencial")),
    (re.compile(r"\bpregao\b|\bpregoes\b"), ("pregao",)),
    (re.compile(r"\bdispensas?\b"), ("dispensa",)),
    (re.compile(r"\binexigibilidade|\binexigive"), ("inexigibilidade",)),
    (re.compile(r"\bconcorrencias?\b"), ("concorrencia",)),
    (re.compile(r"\bleil(?:ao|oes)\b"), ("leilao",)),
    (re.compile(r"\bcredenciamentos?\b"), ("credenciamento",)),
    (re.compile(r"\bdialogo competitivo\b"), ("dialogo",)),
)
_ESFERAS = (("estadual", r"\bestadua"), ("municipal", r"\bmunicip"), ("federal", r"\bfedera"))
# Palavras do nome do órgão que não ajudam a distinguir um órgão de outro.
_PALAVRAS_ORGAO_VAZIAS = frozenset(
    "de da do das dos e no na nos nas em estado estadual municipal rio janeiro rj pca plano planos".split()
)
_RE_RANKING = re.compile(r"\b(?:maior(?:es)?|menor(?:es)?|top|ranking)\b")
_RE_N_RANKING = re.compile(r"\b(\d{1,2}|" + "|".join(_NUMEROS_POR_EXTENSO) + r")\s+(?:maiores|menores)\b")
_RE_EVOLUCAO = re.compile(r"\bao longo\b|\bevolu|\baument|\bcresc|\bqueda\b|\bpor mes\b|\bmensa")
_RE_AGRUPAMENTO = re.compile(
    r"\bmais frequen|\bmais usad|\bmais comu|\bpor modalidade|\bpor orgao|\bpor municipio|\bpor esfera"
)
_RE_COMPARACAO = re.compile(r"\bmais car[oa]s?\b|\bmais barat|\bpag(?:ou|aram|am|a) mais\b|\bcompar")
_RE_ESTATISTICA = re.compile(r"\bmedia\b|\bmedio\b|\bmediana\b|\btotal\b|\bsoma\b|\bquant[oa]s?\b|\bquantidade\b")
_RE_CONTAGEM_CONTRATOS = re.compile(
    r"\b(?:\d+|um|dois|tres|quatro|cinco)\s+ou\s+mais\s+(?:contratos?|contratac(?:ao|oes))\b"
)
_RE_AGENTE = re.compile(r"\b[a-z]+(?:ad|id|it)[oa]s? por (?:orgaos|municipios|esferas)\b")
# Pergunta que pede os registros em si: "quais órgãos contrataram…", "existem contratações de…".
_RE_LISTAGEM = re.compile(r"^(?:quais|qual|existem?|ha|houve|liste|listar|mostre|mostrar)\b")


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
    assinou assinaram contratou contrataram comprou compraram gastou gastaram
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


# Contrato de compilação apenas: estas tabelas de fatos não são criadas aqui.
# O caminho legado SQLite/SQL permanece independente desta função.
_DATASETS_SQL = {
    "contratos_estruturado": {
        "tabela": "contratos_fatos", "valor": "valor",
        "campos": frozenset({"valor", "valor_estimado", "valor_homologado"}),
        "grupos": frozenset({"orgao_nome", "municipio", "modalidade", "esfera", "fornecedor", "numero_controle_pncp", "mes_assinatura"}),
        "expressoes_grupo": {"mes_assinatura": "EXTRACT(MONTH FROM data_assinatura)"},
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
    for operador, valor in ((">" if consulta.valor_min_exclusivo else ">=", consulta.valor_min), ("<=", consulta.valor_max)):
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
    """Plural simples do português: "ambulancias" casa com "ambulancia"."""
    for plural, singular in (("oes", "ao"), ("res", "r"), ("zes", "z"), ("ais", "al"), ("eis", "el")):
        if token.endswith(plural) and len(token) > len(plural) + 2:
            return token[: -len(plural)] + singular
    return token[:-1] if token.endswith("s") and len(token) > 4 else token


# Radicais que nunca descrevem o objeto, em qualquer flexão ("comparando", "publicaram").
_RADICAIS_GENERICOS = ("compar", "publica", "assina", "vigen", "contrat", "registr", "realiz")


def _termos_do_objeto(texto: str) -> tuple[str, ...]:
    termos: list[str] = []
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
    bases = set(bases)
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
    elif _RE_COMPARACAO.search(texto):
        tipo = "estatistica"  # sai quebrada por esfera, que é a comparação pedida
    elif _RE_RANKING.search(texto):
        tipo = "ranking"
    elif _RE_ESTATISTICA.search(texto):
        tipo = "estatistica"
    elif (tabela == "contratacoes" and bases <= {Natureza.ESTRUTURADA, Natureza.AGREGADA}
          and _RE_LISTAGEM.search(texto)):
        # "Quais órgãos contrataram manutenção de computadores em 2025?": filtro
        # por metadado e objeto, que a busca por texto não garante. Só vira
        # consulta se sobrar termo de objeto (verificado abaixo).
        tipo = "listagem"
    else:
        return None

    minimo_por_grupo = None
    if tabela == "contratacoes" and (contagem := _RE_CONTAGEM_CONTRATOS.search(texto)):
        # "fornecedores com três ou mais contratos": agrupa e exige o mínimo.
        numero = contagem.group(0).split()[0]
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

    esfera = None
    if tabela == "contratacoes":
        # "por município(s)" é agrupamento, não filtro de esfera.
        sem_agrupamento = re.sub(r"\bpor (?:municipios?|orgaos?|esferas?)\b", " ", texto)
        citadas = [nome for nome, padrao in _ESFERAS if re.search(padrao, sem_agrupamento)]
        if len(citadas) == 1:  # várias juntas = comparação, que já sai quebrada por esfera
            esfera = citadas[0]

    modalidade_termos: tuple[str, ...] = ()
    texto_objeto = texto
    if tabela == "contratacoes":
        for padrao, termos in _MODALIDADES:
            if padrao.search(texto):
                modalidade_termos = termos
                texto_objeto = padrao.sub(" ", texto_objeto)
                break

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
    referencia_temporal = (
        ("publicacao" if filtros.referencia_temporal == "publicacao" else "assinatura")
        if so_contratos else None
    )
    termos_objeto = _termos_do_objeto(texto_objeto) if tabela == "contratacoes" else ()
    if tipo == "listagem" and not termos_objeto and not modalidade_termos:
        return None  # "Quais contratos existem?" sem assunto: a busca normal responde
    return Consulta(
        tabela=tabela,
        tipo=tipo,
        ordem="ASC" if so_menor else "DESC",
        limite=max(1, min(limite, 50)),
        agrupar_por=agrupar_por,
        metrica=metrica,
        # Período fechado em ano/ano_final; aberto em ano_desde/ano_ate.
        ano=primeiro_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_final=ultimo_ano if primeiro_ano is not None and ultimo_ano is not None else None,
        ano_desde=primeiro_ano if ultimo_ano is None else None,
        ano_ate=ultimo_ano if primeiro_ano is None else None,
        valor_min=filtros.valor_min,
        valor_max=filtros.valor_max,
        esfera=esfera,
        termos_objeto=termos_objeto,
        referencia_temporal=referencia_temporal,
        minimo_por_grupo=minimo_por_grupo,
        mes=mes,
        modalidade_termos=modalidade_termos,
        orgao_termos=orgao_termos,
        so_contratos=so_contratos,
    )


# --------------------------------------------------------------------------
# Execução (SQL com "?" como marcador; Banco.consultar adapta ao Postgres)
# --------------------------------------------------------------------------


def _coluna_valor(consulta: Consulta) -> str:
    if consulta.tabela == "pca_planos":
        return "valor_total"
    return "valor_contratado" if consulta.so_contratos or consulta.referencia_temporal == "assinatura" else "valor"


def _where(consulta: Consulta, termos: Iterable[str], juncao: str = "AND") -> tuple[str, list[Any]]:
    condicoes: list[str] = []
    parametros: list[Any] = []
    coluna_ano = "ano_pca" if consulta.tabela == "pca_planos" else (
        "ano_assinatura" if consulta.referencia_temporal == "assinatura" else "ano_publicacao")
    if consulta.tabela == "contratacoes" and (consulta.so_contratos or consulta.referencia_temporal == "assinatura"):
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
        condicoes.append(f"{_coluna_valor(consulta)} <= ?")
        parametros.append(consulta.valor_max)
    if consulta.esfera:
        condicoes.append("esfera = ?")
        parametros.append(consulta.esfera)
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
        consulta.tipo == "agrupamento" and consulta.agrupar_por == "modalidade")


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
        return "0 registros com valor"
    return (
        f"{len(valores)} registros; total {_brl(sum(valores))}; média {_brl(statistics.fmean(valores))}; "
        f"mediana {_brl(statistics.median(valores))}; mínimo {_brl(min(valores))}; máximo {_brl(max(valores))}"
    )


def _executar(banco: Banco, consulta: Consulta, termos: tuple[str, ...], juncao: str) -> tuple[str, list[str]]:
    where, parametros = _where(consulta, termos, juncao)
    coluna_valor = _coluna_valor(consulta)

    if consulta.tipo in ("ranking", "listagem"):
        # Listagem mostra também registros sem valor (depois dos com valor) e
        # diz quantos casaram no total.
        where_valor = where if consulta.tipo == "listagem" else (
            where + (" AND " if where else " WHERE ") + f"{coluna_valor} >= {_VALOR_MINIMO_INFORMADO}")
        if consulta.tabela == "pca_planos":
            colunas = "ano_pca, unidade_nome, quantidade_itens, valor_total AS valor"
        else:
            colunas = "data_publicacao, orgao_nome, esfera, municipio, modalidade, fornecedor, objeto, valor"
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
                f"{i}. {_brl(l['valor'])} | {l['data_publicacao']} | {l['orgao_nome']} ({l['esfera']}, {l['municipio']}) | "
                f"modalidade: {l['modalidade'] or 'não informada'} | fornecedor: {l['fornecedor'] or 'não informado'} | "
                f"{_resumir(l['objeto'], 70)}"
                for i, l in enumerate(linhas, 1)
            ]
        if consulta.tabela == "pca_planos":
            return sql, [
                f"{i}. {_brl(l['valor'])} | PCA {l['ano_pca']} | {l['unidade_nome']} | {l['quantidade_itens']} itens"
                for i, l in enumerate(linhas, 1)
            ]
        return sql, [
            f"{i}. {_brl(l['valor'])} | {l['data_publicacao']} | {l['orgao_nome']} ({l['esfera']}, {l['municipio']}) | "
            f"{l['modalidade']} | {_resumir(l['objeto'], 60)}"
            for i, l in enumerate(linhas, 1)
        ]

    if consulta.tipo == "agrupamento":
        coluna = consulta.agrupar_por
        # O grupo "não informado" vai para o fim: em "modalidade mais usada", os
        # contratos sem modalidade não são uma modalidade.
        ordem = f"CASE WHEN {coluna} IS NULL THEN 1 ELSE 0 END, " + (
            f"COALESCE(SUM({coluna_valor}), 0) {consulta.ordem}, n DESC"
            if consulta.metrica == "soma" else f"n DESC, COALESCE(SUM({coluna_valor}), 0) DESC")
        # Registro sem fornecedor não é um fornecedor, e sem modalidade não é uma
        # modalidade (os contratos assinados do PNCP não trazem modalidade): ficam
        # fora dos grupos e são contados à parte, para "a mais usada" não ser "nenhuma".
        sem_campo = 0
        if coluna in ("fornecedor", "modalidade"):
            where_nulo = where + (" AND " if where else " WHERE ") + f"{coluna} IS NULL"
            sem_campo = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where_nulo}", parametros)[0]["n"]
            where = where + (" AND " if where else " WHERE ") + f"{coluna} IS NOT NULL"
        sql = (
            f"SELECT {coluna} AS grupo, COUNT(*) AS n, SUM({coluna_valor}) AS total "
            f"FROM {consulta.tabela}{where} GROUP BY {coluna}"
        )
        if consulta.minimo_por_grupo is not None:
            sql += f" HAVING COUNT(*) >= {int(consulta.minimo_por_grupo)}"
        sql += f" ORDER BY {ordem}"
        if consulta.metrica == "soma":
            sql += f" LIMIT {consulta.limite}"
        grupos = [
            f"{'Fornecedor ' if coluna == 'fornecedor' else ''}{l['grupo'] or 'não informado'}: {l['n']} registros, total {_brl(l['total'])}"
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
            if _tem_valor(l["valor"]):
                por_periodo.setdefault(l["periodo"], []).append(float(l["valor"]))
        return sql, [f"{periodo}: {_estatisticas(valores)}" for periodo, valores in sorted(por_periodo.items())]

    # estatistica: geral e, nas contratações, quebrada por esfera. Mediana em
    # Python porque SQLite não tem função de mediana.
    coluna_esfera = "esfera" if consulta.tabela == "contratacoes" else "NULL"
    sql = f"SELECT {coluna_esfera} AS esfera, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
    todas = banco.consultar(sql, parametros)
    linhas = [l for l in todas if _tem_valor(l["valor"])]
    # A contagem vale para todos os registros; as contas de valor, só para os que
    # têm valor (R$ 0,00 e R$ 0,01 no PNCP não são o valor e puxariam média e mediana).
    sem_valor = len(todas) - len(linhas)
    geral = f"Geral: {_estatisticas([float(l['valor']) for l in linhas])}"
    if sem_valor:
        geral = f"Geral: {len(todas)} registros no total, {sem_valor} sem valor informado (vazio ou abaixo de R$ 1,00); valores: " + geral[7:]
    resultado = [geral]
    if consulta.tabela == "contratacoes":
        por_esfera: dict[str, list[float]] = {}
        for l in linhas:
            por_esfera.setdefault(l["esfera"] or "não informada", []).append(float(l["valor"]))
        resultado += [f"Esfera {esfera}: {_estatisticas(v)}" for esfera, v in sorted(por_esfera.items())]
    return sql, resultado


def _cabecalho(consulta: Consulta, total_tabela: int, termos: tuple[str, ...], juncao: str) -> str:
    if consulta.tabela == "pca_planos":
        origem = (
            f"Consulta SQL na tabela pca_planos: {total_tabela} planos (PCA) de unidades do RJ presentes no corpus. "
            "É a soma só desses planos, NÃO o valor total do PCA do Estado."
        )
    else:
        origem = (
            f"Consulta SQL na tabela contratacoes: {total_tabela} registros da amostra PNCP/RJ. "
            "Contratos: valorGlobal; compras: homologado ou estimado. Valores informados, não pagamentos."
        )
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
        filtros.append(f"valor>={_brl(consulta.valor_min)}")
    if consulta.valor_max is not None:
        filtros.append(f"valor<={_brl(consulta.valor_max)}")
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


def _resolver_valores(consulta: Consulta, banco: Banco) -> Consulta:
    """Troca os termos de modalidade e de órgão pelos valores do banco que casam.

    A comparação é feita aqui, sem acento e sem caixa, para valer igual no
    SQLite e no Postgres; o SQL recebe só ``coluna IN (...)``.
    """
    mudancas: dict[str, Any] = {}
    if consulta.tabela == "contratacoes" and not (consulta.so_contratos or consulta.referencia_temporal == "assinatura"):
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
            mudancas["orgaos"] = tuple(sorted(n for n in nomes if _palavras_casam(consulta.orgao_termos, n)))
        else:
            casados = tuple(sorted(n for n in nomes if _palavras_casam(consulta.termos_objeto, n)))
            if casados:
                mudancas.update(orgaos=casados, orgao_termos=consulta.termos_objeto, termos_objeto=())
    return replace(consulta, **mudancas) if mudancas else consulta


def executar_consulta(consulta: Consulta, banco: Banco) -> tuple[str, str, list[str]]:
    """Roda a consulta e devolve (sql, cabeçalho, linhas de resultado).

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
    for termos, juncao in tentativas:
        if not termos or contagem(termos, juncao):
            sql, linhas = _executar(banco, consulta, termos, juncao)
            return sql, _cabecalho(consulta, total, termos, juncao), linhas
    where, _ = _where(consulta, consulta.termos_objeto, "OR")
    return (
        f"SELECT 1 FROM {consulta.tabela}{where}",
        _cabecalho(consulta, total, consulta.termos_objeto, "OR"),
        [f"Nenhuma contratação do corpus tem no objeto: {', '.join(consulta.termos_objeto)}."],
    )


def _chunks(consulta: Consulta, sql: str, cabecalho: str, linhas: list[str], banco: Banco) -> list[Chunk]:
    """Resultado em um ou mais chunks que cabem em ``GENERATION_CHUNK_CHARS``
    (a geração corta o que passar disso); o cabeçalho se repete em cada parte."""
    limite = max(300, config.GENERATION_CHUNK_CHARS - 40)
    partes: list[list[str]] = [[]]
    for linha in linhas or ["(nenhum registro)"]:
        cabeca = len(cabecalho) if len(partes) == 1 else 70
        tamanho = cabeca + sum(len(l) + 1 for l in partes[-1]) + len(linha) + 1
        if partes[-1] and tamanho > limite:
            partes.append([])
        partes[-1].append(linha[: max(80, limite - len(cabecalho) - 2)])
    assinatura = hashlib.sha256(f"{sql}|{consulta}".encode("utf-8")).hexdigest()[:12]
    base_id = structured_db.BASE_PCA if consulta.tabela == "pca_planos" else structured_db.BASE_CONTRATACOES
    natureza = Natureza.AGREGADA if consulta.tabela == "pca_planos" else Natureza.ESTRUTURADA
    return [
        Chunk(
            content=(cabecalho if indice == 1 else f"(continuação da consulta SQL acima, parte {indice}; mesmos filtros)")
            + "\n"
            + "\n".join(parte),
            metadata=ChunkMetadata(
                source_file=banco.descricao(),
                chunk_id=f"sql:{consulta.tabela}:{consulta.tipo}:{assinatura}:{indice}",
                base_id=base_id,
                doc_type="sql",
                natureza=natureza,
                chunking_strategy="consulta_sql",
                extras={"sql": sql, "tipo": consulta.tipo, "parte": indice, "partes": len(partes)},
            ),
        )
        for indice, parte in enumerate(partes, 1)
    ]


def consultar_estruturado(
    query: str,
    filtros: QueryFilters,
    bases: Iterable[Natureza],
    *,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
    banco: Optional[Banco] = None,
    documents_file: Optional[Path] = None,
) -> list[Chunk]:
    """Etapa ``consulta_sql``: devolve chunks com o resultado, ou ``[]``.

    Sem pergunta de conta, ou sem ``documents.jsonl`` na máquina, não roda nem
    registra nada. Erro na consulta (ex.: Neon fora do ar) é registrado no log
    e não derruba o pipeline: a resposta segue só com a busca normal.
    """
    consulta = detectar_consulta(query, filtros, bases)
    documents_file = Path(documents_file or DOCUMENTS_FILE)
    if consulta is None or not documents_file.exists():
        return []
    banco = banco or structured_db.banco_padrao()
    try:
        with default_logger.span(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.CONSULTA_SQL,
            input=query,
            extra={"tabela": consulta.tabela, "tipo": consulta.tipo, "banco": banco.descricao()},
        ) as span:
            structured_db.garantir_banco(documents_file, banco, status=span.extra)
            with banco.sessao():
                sql, cabecalho, linhas = executar_consulta(consulta, banco)
            chunks = _chunks(consulta, sql, cabecalho, linhas, banco)
            span.extra.update({"sql": sql, "linhas": len(linhas), "chunks": len(chunks)})
            span.set_result(f"{len(linhas)} linha(s) em {len(chunks)} chunk(s)")
            return chunks
    except Exception:
        return []


__all__ = ["Consulta", "consultar_estruturado", "detectar_consulta", "executar_consulta"]
