"""Indicadores agregados do PCA (Plano de Contratações Anual) para a base agregada.

O CSV do PCA publicado no PNCP traz um item planejado por linha (o do Estado do
RJ em 2025 tem ~39 mil). Indexar linha a linha gera dezenas de milhares de
chunks e deixa para o LLM somar valores, o que ele faz mal. Aqui a agregação é
feita na ingestão, de forma determinística: cada indicador (visão geral, por
categoria, por unidade, por classe, por mês e rankings) vira um único chunk em
linguagem natural, com os números já calculados.

Uso (dentro de ``src/prepare_documents.py``, para bases de natureza agregada)::

    if eh_csv_pca(colunas):
        for indicador in indicadores_pca(linhas, orgao="ESTADO DO RIO DE JANEIRO"):
            ...
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


COL_UNIDADE = "Unidade Responsável"
COL_CATEGORIA = "Categoria do Item"
COL_CODIGO_CLASSE = "Código da Classificação Superior (Classe/Grupo)"
COL_CLASSE = "Nome da Classificação Superior (Classe/Grupo)"
COL_DESCRICAO = "Descrição do Item"
COL_VALOR_TOTAL = "Valor Total Estimado (R$)"
COL_DATA = "Data Desejada"

COLUNAS_OBRIGATORIAS = frozenset({COL_UNIDADE, COL_CATEGORIA, COL_CLASSE, COL_VALOR_TOTAL})

TOP_N = 5
# Equivalências precisam de revisão explícita; nomes e unidades originais são preservados.
ALIASES_REVISADOS: dict[str, str] = {}
TOP_RANKING = 30
_MAX_DESCRICAO = 120
_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


@dataclass
class Indicador:
    """Um indicador agregado, pronto para virar um chunk."""

    id: str
    dimensao: str
    texto: str
    metadados: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Item:
    unidade: str
    categoria: str
    classe: str
    descricao: str
    valor: Decimal
    mes: str | None


def eh_csv_pca(colunas: Iterable[str]) -> bool:
    """O CSV tem as colunas do PCA do PNCP?"""
    return COLUNAS_OBRIGATORIAS <= set(colunas)


def _decimal(texto: str | None) -> Decimal:
    """Converte "37117,8000" ou "1.234,56" (formato brasileiro) em Decimal."""
    valor = (texto or "").strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(valor) if valor else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _mes(texto: str | None) -> str | None:
    try:
        return datetime.strptime((texto or "").strip(), "%d/%m/%Y").strftime("%Y-%m")
    except ValueError:
        return None


def brl(valor: Decimal) -> str:
    """Formata em reais: ``R$ 1.234.567,89``."""
    return "R$ " + f"{valor.quantize(Decimal('0.01')):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(parte: Decimal, todo: Decimal) -> str:
    return f"{(parte / todo * 100) if todo else 0:.1f}%".replace(".", ",")


def _slug(texto: str) -> str:
    """Id legível e estável; o hash do nome completo evita colisão entre nomes
    que só diferem por acento, caixa ou depois do corte de 60 caracteres."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    legivel = re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")[:60] or "sem-nome"
    return f"{legivel}-{hashlib.sha1(texto.encode('utf-8')).hexdigest()[:6]}"


def _nome(texto: str, vazio: str = "(não informado)") -> str:
    return " ".join((texto or "").split()) or vazio


def _itens(linhas: Iterable[Mapping[str, str]]) -> list[_Item]:
    return [
        _Item(
            unidade=_nome(linha.get(COL_UNIDADE, "")),
            categoria=_nome(linha.get(COL_CATEGORIA, "")),
            classe=_nome(linha.get(COL_CLASSE, "")),
            descricao=_nome(linha.get(COL_DESCRICAO, ""), "(sem descrição)"),
            valor=_decimal(linha.get(COL_VALOR_TOTAL)),
            mes=_mes(linha.get(COL_DATA)),
        )
        for linha in linhas
    ]


class _Grupo:
    """Soma valores e conta itens de um recorte (ex.: uma unidade)."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.itens = 0
        self.por: dict[str, defaultdict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        self.maiores: list[_Item] = []

    def add(self, item: _Item, *dimensoes: str) -> None:
        self.total += item.valor
        self.itens += 1
        for dimensao in dimensoes:
            self.por[dimensao][getattr(item, dimensao)] += item.valor
        self.maiores.append(item)

    def top(self, dimensao: str, n: int = TOP_N) -> list[tuple[str, Decimal]]:
        return sorted(self.por[dimensao].items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def maiores_itens(self, n: int = 3) -> list[_Item]:
        return sorted(self.maiores, key=lambda item: (-item.valor, item.descricao))[:n]


def _lista(pares: list[tuple[str, Decimal]], total: Decimal) -> str:
    return "; ".join(f"{nome}: {brl(valor)} ({_pct(valor, total)})" for nome, valor in pares)


def _lista_itens(itens: list[_Item]) -> str:
    return "; ".join(f"{item.descricao[:_MAX_DESCRICAO]} ({brl(item.valor)})" for item in itens)


def _ranking(grupos: Mapping[str, _Grupo]) -> list[tuple[str, _Grupo]]:
    return sorted(grupos.items(), key=lambda kv: (-kv[1].total, kv[0]))


@dataclass
class _Agrupamento:
    """Os itens do plano somados no todo e por categoria, unidade, classe e mês."""

    geral: _Grupo = field(default_factory=_Grupo)
    por_categoria: dict[str, _Grupo] = field(default_factory=lambda: defaultdict(_Grupo))
    por_unidade: dict[str, _Grupo] = field(default_factory=lambda: defaultdict(_Grupo))
    por_classe: dict[str, _Grupo] = field(default_factory=lambda: defaultdict(_Grupo))
    por_mes: dict[str, _Grupo] = field(default_factory=lambda: defaultdict(_Grupo))


def _agrupar(itens: list[_Item]) -> _Agrupamento:
    grupos = _Agrupamento()
    for item in itens:
        grupos.geral.add(item, "categoria", "unidade", "classe")
        grupos.por_categoria[item.categoria].add(item, "classe", "unidade")
        grupos.por_unidade[item.unidade].add(item, "categoria", "classe")
        grupos.por_classe[item.classe].add(item, "unidade", "categoria")
        if item.mes:
            grupos.por_mes[item.mes].add(item, "classe", "categoria")
    return grupos


def indicadores_pca(
    linhas: Iterable[Mapping[str, str]],
    *,
    orgao: str = "o órgão",
    ano: int | str | None = None,
) -> list[Indicador]:
    """Calcula os indicadores do PCA. Cada indicador vira um chunk.

    ``orgao`` e ``ano`` só entram no texto (ex.: "PCA 2025 do ESTADO DO RIO DE
    JANEIRO"); se ``ano`` não vier, usa o ano mais frequente da data desejada.
    """
    itens = _itens(linhas)
    if not itens:
        return []
    if ano is None:
        anos = Counter(item.mes[:4] for item in itens if item.mes)
        ano = anos.most_common(1)[0][0] if anos else "(ano não informado)"
    rotulo = f"PCA {ano} de {orgao}"

    grupos = _agrupar(itens)
    geral = grupos.geral
    total = geral.total
    comum = {"orgao": orgao, "ano": str(ano), "fonte": "PCA/PNCP"}
    resultado: list[Indicador] = [
        Indicador(
            id="visao-geral",
            dimensao="visao_geral",
            texto=(
                f"Visão geral e valor total do Plano de Contratações Anual ({rotulo}). "
                f"Valor total planejado (estimado) do plano: {brl(total)}, em {geral.itens} itens, "
                f"por {len(grupos.por_unidade)} unidades responsáveis e {len(grupos.por_classe)} classes de catálogo. "
                f"Por categoria: {_lista(geral.top('categoria', 10), total)}. "
                f"Unidades com maior valor: {_lista(geral.top('unidade'), total)}. "
                f"Classes com maior valor: {_lista(geral.top('classe'), total)}."
            ),
            metadados={**comum, "valor_total": float(total), "itens": geral.itens},
        )
    ]
    resultado += _indicadores_por_recorte(grupos, total, ano, comum)
    resultado += _resumos_e_rankings(grupos, total, ano, orgao, rotulo, comum)
    return resultado


def _indicadores_por_recorte(
    grupos: _Agrupamento, total: Decimal, ano: int | str, comum: dict[str, Any]
) -> list[Indicador]:
    """Um indicador por categoria, unidade e classe (na ordem do valor) e por mês."""
    resultado: list[Indicador] = []
    for posicao, (categoria, grupo) in enumerate(_ranking(grupos.por_categoria), start=1):
        resultado.append(
            Indicador(
                id=f"categoria-{_slug(categoria)}",
                dimensao="categoria",
                texto=(
                    f"Categoria \"{categoria}\" no PCA {ano}: o total planejado para {categoria} é "
                    f"{brl(grupo.total)} ({_pct(grupo.total, total)} do plano), em {grupo.itens} itens; "
                    f"{posicao}ª categoria em valor. "
                    f"Classes com maior valor: {_lista(grupo.top('classe'), grupo.total)}. "
                    f"Unidades com maior valor: {_lista(grupo.top('unidade'), grupo.total)}."
                ),
                metadados={**comum, "categoria": categoria, "valor_total": float(grupo.total),
                           "itens": grupo.itens, "posicao": posicao},
            )
        )

    for posicao, (unidade, grupo) in enumerate(_ranking(grupos.por_unidade), start=1):
        resultado.append(
            Indicador(
                id=f"unidade-{_slug(unidade)}",
                dimensao="unidade",
                texto=(
                    f"Unidade \"{unidade}\" no PCA {ano}: a unidade {unidade} planeja "
                    f"{brl(grupo.total)} ({_pct(grupo.total, total)} do plano), em {grupo.itens} itens; "
                    f"{posicao}ª de {len(grupos.por_unidade)} unidades em valor. "
                    f"Por categoria: {_lista(grupo.top('categoria', 10), grupo.total)}. "
                    f"Classes com maior valor: {_lista(grupo.top('classe'), grupo.total)}. "
                    f"Maiores itens: {_lista_itens(grupo.maiores_itens())}."
                ),
                metadados={**comum, "unidade": unidade, "valor_total": float(grupo.total),
                           "itens": grupo.itens, "posicao": posicao},
            )
        )

    for posicao, (classe, grupo) in enumerate(_ranking(grupos.por_classe), start=1):
        resultado.append(
            Indicador(
                id=f"classe-{_slug(classe)}",
                dimensao="classe",
                texto=(
                    f"Classe \"{classe}\" no PCA {ano}: o total planejado para {classe} é "
                    f"{brl(grupo.total)} ({_pct(grupo.total, total)} do plano), em {grupo.itens} itens; "
                    f"{posicao}ª de {len(grupos.por_classe)} classes em valor. "
                    f"Unidades com maior valor: {_lista(grupo.top('unidade'), grupo.total)}. "
                    f"Maiores itens: {_lista_itens(grupo.maiores_itens())}."
                ),
                metadados={**comum, "classe": classe, "valor_total": float(grupo.total),
                           "itens": grupo.itens, "posicao": posicao},
            )
        )

    for mes, grupo in sorted(grupos.por_mes.items()):
        resultado.append(
            Indicador(
                id=f"mes-{mes}",
                dimensao="mes",
                texto=(
                    f"Mês de {_MESES[int(mes[5:]) - 1]} de {mes[:4]} ({mes[5:]}/{mes[:4]}) "
                    f"no PCA {ano}: as contratações previstas para {_MESES[int(mes[5:]) - 1]} somam "
                    f"{brl(grupo.total)} ({_pct(grupo.total, total)} do plano), "
                    f"em {grupo.itens} itens com data desejada nesse mês. "
                    f"Por categoria: {_lista(grupo.top('categoria', 10), grupo.total)}. "
                    f"Classes com maior valor: {_lista(grupo.top('classe'), grupo.total)}."
                ),
                metadados={**comum, "mes": mes, "valor_total": float(grupo.total), "itens": grupo.itens},
            )
        )
    return resultado


def _resumos_e_rankings(
    grupos: _Agrupamento, total: Decimal, ano: int | str, orgao: str, rotulo: str, comum: dict[str, Any]
) -> list[Indicador]:
    """Valor total, as 10 maiores classes e unidades e os rankings completos."""
    geral = grupos.geral
    # Resumos curtos para as perguntas mais comuns ("qual o valor total?",
    # "quais as classes de maior valor?"): listas longas diluem o vetor da busca
    # densa e são penalizadas pelo BM25, então a resposta direta vira um chunk
    # próprio.
    resultado = [
        Indicador(
            id="valor-total",
            dimensao="resumo",
            texto=(
                f"Valor total do Plano de Contratações Anual {ano} de {orgao}: "
                f"{brl(total)} planejados em {geral.itens} itens."
            ),
            metadados={**comum, "valor_total": float(total), "itens": geral.itens},
        )
    ]
    for dimensao, por_chave, nome in (
        ("classe", grupos.por_classe, "classes de catálogo"),
        ("unidade", grupos.por_unidade, "unidades responsáveis"),
    ):
        maiores = "; ".join(
            f"{i}. {chave} ({brl(g.total)})" for i, (chave, g) in enumerate(_ranking(por_chave)[:10], start=1)
        )
        resultado.append(
            Indicador(
                id=f"maiores-{dimensao}",
                dimensao="resumo",
                texto=(
                    f"Quais as {nome} com maior valor no Plano de Contratações Anual (PCA) {ano}? "
                    f"As 10 {nome} com maior valor planejado são: {maiores}."
                ),
                metadados={**comum, "valor_total": float(total), "itens": geral.itens},
            )
        )

    for dimensao, por_chave, nome in (
        ("unidade", grupos.por_unidade, "unidades responsáveis"),
        ("classe", grupos.por_classe, "classes de catálogo"),
    ):
        ranking = _ranking(por_chave)[:TOP_RANKING]
        linhas_ranking = "; ".join(
            f"{i}. {chave}: {brl(g.total)} ({_pct(g.total, total)})" for i, (chave, g) in enumerate(ranking, start=1)
        )
        resultado.append(
            Indicador(
                id=f"ranking-{dimensao}",
                dimensao=f"ranking_{dimensao}",
                texto=f"Ranking das {len(ranking)} {nome} com maior valor planejado no {rotulo}: {linhas_ranking}.",
                metadados={**comum, "valor_total": float(total), "itens": geral.itens},
            )
        )
    return resultado


__all__ = ["Indicador", "brl", "eh_csv_pca", "indicadores_pca"]
