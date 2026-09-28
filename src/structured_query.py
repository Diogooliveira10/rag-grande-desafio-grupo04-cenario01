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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import config
from src import structured_db
from src.obs import Stage, default_logger
from src.schema import Chunk, ChunkMetadata, Natureza, QueryFilters
from src.structured_db import Banco

DOCUMENTS_FILE = structured_db.DOCUMENTS_FILE

LIMITE_RANKING = 10
_NUMEROS_POR_EXTENSO = {
    "dois": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9, "dez": 10,
    "doze": 12, "quinze": 15, "vinte": 20, "trinta": 30, "cinquenta": 50,
}

_RE_PCA = re.compile(r"\bpca\b|\bplanos? (?:anual )?de contratac|\bplanejad")
_RE_RANKING = re.compile(r"\b(?:maior(?:es)?|menor(?:es)?|top|ranking)\b")
_RE_N_RANKING = re.compile(r"\b(\d{1,2}|" + "|".join(_NUMEROS_POR_EXTENSO) + r")\s+(?:maiores|menores)\b")
_RE_EVOLUCAO = re.compile(r"\bao longo\b|\bevolu|\baument|\bcresc|\bqueda\b|\bpor mes\b|\bmensa")
_RE_AGRUPAMENTO = re.compile(
    r"\bmais frequen|\bmais usad|\bmais comu|\bpor modalidade|\bpor orgao|\bpor municipio|\bpor esfera"
)
_RE_COMPARACAO = re.compile(r"\bmais car[oa]s?\b|\bmais barat|\bpag(?:ou|aram|am|a) mais\b|\bcompar")
_RE_ESTATISTICA = re.compile(r"\bmedia\b|\bmedio\b|\bmediana\b|\btotal\b|\bsoma\b|\bquant[oa]s\b|\bquantidade\b")

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
    ano: Optional[int] = None
    valor_min: Optional[float] = None
    valor_max: Optional[float] = None
    esfera: Optional[str] = None
    termos_objeto: tuple[str, ...] = field(default_factory=tuple)


def _singular(token: str) -> str:
    """Plural simples do português: "ambulancias" casa com "ambulancia"."""
    for plural, singular in (("oes", "ao"), ("res", "r"), ("zes", "z"), ("ais", "al"), ("eis", "el")):
        if token.endswith(plural) and len(token) > len(plural) + 2:
            return token[: -len(plural)] + singular
    return token[:-1] if token.endswith("s") and len(token) > 4 else token


def _termos_do_objeto(texto: str) -> tuple[str, ...]:
    termos: list[str] = []
    for token in re.findall(r"[a-z]{4,}", texto):
        if token in _PALAVRAS_GENERICAS or token.rstrip("s") in _PALAVRAS_GENERICAS or token in _NUMEROS_POR_EXTENSO:
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
    texto = structured_db.normalizar(query)
    if Natureza.AGREGADA in bases and (_RE_PCA.search(texto) or Natureza.ESTRUTURADA not in bases):
        tabela = "pca_planos"
    elif Natureza.ESTRUTURADA in bases:
        tabela = "contratacoes"
    else:
        return None

    if _RE_EVOLUCAO.search(texto):
        tipo = "evolucao"
    elif _RE_AGRUPAMENTO.search(texto):
        tipo = "agrupamento"
    elif _RE_COMPARACAO.search(texto):
        tipo = "estatistica"  # sai quebrada por esfera, que é a comparação pedida
    elif _RE_RANKING.search(texto):
        tipo = "ranking"
    elif _RE_ESTATISTICA.search(texto):
        tipo = "estatistica"
    else:
        return None

    limite = LIMITE_RANKING
    if n := _RE_N_RANKING.search(texto):
        limite = _NUMEROS_POR_EXTENSO.get(n.group(1)) or int(n.group(1))
    agrupar_por = "modalidade"
    if tabela == "pca_planos":
        agrupar_por = "unidade_nome"
    elif "por orgao" in texto:
        agrupar_por = "orgao_nome"
    elif "por municipio" in texto:
        agrupar_por = "municipio"
    elif "por esfera" in texto:
        agrupar_por = "esfera"

    esfera = None
    if tabela == "contratacoes":
        estadual, municipal = "estadua" in texto, "municip" in texto
        if estadual != municipal:  # as duas juntas = comparação, que já sai quebrada por esfera
            esfera = "estadual" if estadual else "municipal"

    so_menor = re.search(r"\bmenor(?:es)?\b", texto) and not re.search(r"\bmaior(?:es)?\b", texto)
    return Consulta(
        tabela=tabela,
        tipo=tipo,
        ordem="ASC" if so_menor else "DESC",
        limite=max(1, min(limite, 50)),
        agrupar_por=agrupar_por,
        ano=filtros.ano,
        valor_min=filtros.valor_min,
        valor_max=filtros.valor_max,
        esfera=esfera,
        termos_objeto=_termos_do_objeto(texto) if tabela == "contratacoes" else (),
    )


# --------------------------------------------------------------------------
# Execução (SQL com "?" como marcador; Banco.consultar adapta ao Postgres)
# --------------------------------------------------------------------------


def _coluna_valor(consulta: Consulta) -> str:
    return "valor_total" if consulta.tabela == "pca_planos" else "valor"


def _where(consulta: Consulta, termos: Iterable[str], juncao: str = "AND") -> tuple[str, list[Any]]:
    condicoes: list[str] = []
    parametros: list[Any] = []
    coluna_ano = "ano_pca" if consulta.tabela == "pca_planos" else "ano_publicacao"
    if consulta.ano is not None:
        condicoes.append(f"{coluna_ano} = ?")
        parametros.append(consulta.ano)
    if consulta.valor_min is not None:
        condicoes.append(f"{_coluna_valor(consulta)} >= ?")
        parametros.append(consulta.valor_min)
    if consulta.valor_max is not None:
        condicoes.append(f"{_coluna_valor(consulta)} <= ?")
        parametros.append(consulta.valor_max)
    if consulta.esfera:
        condicoes.append("esfera = ?")
        parametros.append(consulta.esfera)
    termos = list(termos)
    if termos:
        condicoes.append("(" + f" {juncao} ".join("objeto_normalizado LIKE ?" for _ in termos) + ")")
        parametros.extend(f"%{termo}%" for termo in termos)
    return (" WHERE " + " AND ".join(condicoes)) if condicoes else "", parametros


def _brl(valor: Optional[float]) -> str:
    if valor is None:
        return "sem valor"
    return "R$ " + f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _resumir(texto: Any, limite: int) -> str:
    texto = " ".join(str(texto or "").split())
    return texto if len(texto) <= limite else texto[:limite].rstrip() + "…"


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

    if consulta.tipo == "ranking":
        where_valor = where + (" AND " if where else " WHERE ") + f"{coluna_valor} IS NOT NULL"
        if consulta.tabela == "pca_planos":
            colunas = "ano_pca, unidade_nome, quantidade_itens, valor_total AS valor"
        else:
            colunas = "data_publicacao, orgao_nome, esfera, municipio, modalidade, objeto, valor"
        sql = (
            f"SELECT {colunas} FROM {consulta.tabela}{where_valor} "
            f"ORDER BY {coluna_valor} {consulta.ordem} LIMIT {consulta.limite}"
        )
        linhas = banco.consultar(sql, parametros)
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
        sql = (
            f"SELECT {coluna} AS grupo, COUNT(*) AS n, SUM({coluna_valor}) AS total "
            f"FROM {consulta.tabela}{where} GROUP BY {coluna} ORDER BY n DESC, COALESCE(SUM({coluna_valor}), 0) DESC"
        )
        return sql, [
            f"{l['grupo'] or 'não informado'}: {l['n']} registros, total {_brl(l['total'])}"
            for l in banco.consultar(sql, parametros)
        ]

    if consulta.tipo == "evolucao":
        coluna_periodo = "ano_pca" if consulta.tabela == "pca_planos" else "mes_publicacao"
        sql = f"SELECT {coluna_periodo} AS periodo, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
        por_periodo: dict[Any, list[float]] = {}
        for l in banco.consultar(sql, parametros):
            if l["valor"] is not None:
                por_periodo.setdefault(l["periodo"], []).append(float(l["valor"]))
        return sql, [f"{periodo}: {_estatisticas(valores)}" for periodo, valores in sorted(por_periodo.items())]

    # estatistica: geral e, nas contratações, quebrada por esfera. Mediana em
    # Python porque SQLite não tem função de mediana.
    coluna_esfera = "esfera" if consulta.tabela == "contratacoes" else "NULL"
    sql = f"SELECT {coluna_esfera} AS esfera, {coluna_valor} AS valor FROM {consulta.tabela}{where}"
    linhas = [l for l in banco.consultar(sql, parametros) if l["valor"] is not None]
    resultado = [f"Geral: {_estatisticas([float(l['valor']) for l in linhas])}"]
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
            f"Consulta SQL na tabela contratacoes: {total_tabela} contratações do PNCP (UF=RJ) presentes no corpus. "
            "valor = homologado quando existe, senão estimado. A tabela não tem dados de fornecedor."
        )
    filtros = []
    if consulta.ano is not None:
        filtros.append(f"ano={consulta.ano}")
    if consulta.valor_min is not None:
        filtros.append(f"valor>={_brl(consulta.valor_min)}")
    if consulta.valor_max is not None:
        filtros.append(f"valor<={_brl(consulta.valor_max)}")
    if consulta.esfera:
        filtros.append(f"esfera={consulta.esfera}")
    if termos:
        filtros.append(f"objeto contém {(' e ' if juncao == 'AND' else ' ou ').join(termos)}")
    return f"{origem} Filtros: {', '.join(filtros) or 'nenhum'}. Cálculo: {consulta.tipo}."


def executar_consulta(consulta: Consulta, banco: Banco) -> tuple[str, str, list[str]]:
    """Roda a consulta e devolve (sql, cabeçalho, linhas de resultado).

    Termos do objeto são tentados com E; se nada casa, com OU; se ainda nada,
    o resultado diz que nenhuma contratação do corpus tem esse objeto (em vez
    de calcular sobre contratações de outro assunto).
    """
    total = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}")[0]["n"]
    tentativas = [(consulta.termos_objeto, "AND")]
    if len(consulta.termos_objeto) > 1:
        tentativas.append((consulta.termos_objeto, "OR"))
    for termos, juncao in tentativas:
        where, parametros = _where(consulta, termos, juncao)
        if not termos or banco.consultar(f"SELECT 1 AS ok FROM {consulta.tabela}{where} LIMIT 1", parametros):
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
