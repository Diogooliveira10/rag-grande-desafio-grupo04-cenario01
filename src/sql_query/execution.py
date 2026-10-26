"""Execução da ``Consulta``: WHERE parametrizado, um executor por tipo e formatação.

``_where`` monta as condições com ``?`` (os valores do usuário nunca entram no
texto do SQL); ``_EXECUTORES`` liga cada tipo de consulta à função que a roda
e escreve as linhas da resposta e a tabela em Markdown.

Movido de src/structured_query.py em 2026-10-25 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Iterable, Optional
from src import structured_db
from src.domain import purchase_status
from src.structured_db import Banco
from src.sql_markdown import tabela_markdown, texto_markdown
from src.sql_query.detection import Consulta


def _clausula_de_situacao(consulta: Consulta) -> Optional[str]:
    return purchase_status.clausula_de_situacao(
        consulta.situacoes_citadas, consulta.com_situacoes_validas, consulta.todas_as_situacoes)


def _coluna_valor(consulta: Consulta) -> str:
    if consulta.tabela == "pca_planos":
        return "valor_total"
    return consulta.campo or ("valor_contratado" if consulta.so_contratos or consulta.referencia_temporal == "assinatura" else "valor")


@dataclass
class _Clausulas:
    """Condições do WHERE e os parâmetros delas, na ordem em que entram (a ordem dos "?")."""
    condicoes: list[str] = field(default_factory=list)
    parametros: list[Any] = field(default_factory=list)

    def add(self, condicao: str, *parametros: Any) -> None:
        self.condicoes.append(condicao)
        self.parametros.extend(parametros)


def _where(consulta: Consulta, termos: Iterable[str], juncao: str = "AND") -> tuple[str, list[Any]]:
    """WHERE parametrizado da consulta; os valores vão só em ``?``."""
    c = _Clausulas()
    _filtros_de_registro(consulta, c)
    _filtros_de_periodo(consulta, c)
    _filtros_de_valor_e_campos(consulta, c)
    _filtros_de_lista(consulta, c)
    _filtros_analiticos(consulta, c)
    termos = list(termos)
    _filtro_de_objeto(termos, juncao, c)
    if consulta.deduplicar and consulta.tabela == "contratacoes":
        # Vínculo fora do recorte não pode apagar uma compra/instrumento válido.
        scope, scope_params = _where(replace(consulta, deduplicar=False), termos, juncao)
        template = _SEM_DUPLICATA_MODALIDADE if _usa_modalidade(consulta) else _SEM_DUPLICATA
        c.add(template.format(recorte=scope, conector=" AND " if scope else " WHERE "), *scope_params)
    return (" WHERE " + " AND ".join(c.condicoes)) if c.condicoes else "", c.parametros


def _filtros_de_registro(consulta: Consulta, c: _Clausulas) -> None:
    """Só contratos assinados, quando a pergunta é de contrato; situação da compra."""
    if consulta.tabela == "contratacoes" and (
        consulta.so_contratos or consulta.referencia_temporal == "assinatura" or consulta.campo == "valor_contratado"
    ):
        c.add("tipo_registro = ?", "contrato_assinado")
    if consulta.tabela == "contratacoes":
        clausula = _clausula_de_situacao(consulta)
        if clausula:
            c.add(clausula)


def _filtros_de_periodo(consulta: Consulta, c: _Clausulas) -> None:
    """Ano (ou faixa) e período aberto, na coluna de data que a pergunta pede."""
    coluna_ano = "ano_pca" if consulta.tabela == "pca_planos" else (
        "ano_assinatura" if consulta.referencia_temporal == "assinatura" else "ano_publicacao")
    if consulta.ano is not None:
        fim = consulta.ano_final or consulta.ano
        if fim == consulta.ano:
            c.add(f"{coluna_ano} = ?", consulta.ano)
        else:
            c.add(f"{coluna_ano} >= ? AND {coluna_ano} <= ?", consulta.ano, fim)
    if consulta.ano_desde is not None:
        c.add(f"{coluna_ano} >= ?", consulta.ano_desde)
    if consulta.ano_ate is not None:
        c.add(f"{coluna_ano} <= ?", consulta.ano_ate)


def _filtros_de_valor_e_campos(consulta: Consulta, c: _Clausulas) -> None:
    """Faixa de valor, esfera, número de controle e mês."""
    if consulta.valor_min is not None:
        c.add(f"{_coluna_valor(consulta)} {'>' if consulta.valor_min_exclusivo else '>='} ?", consulta.valor_min)
    if consulta.valor_max is not None:
        c.add(f"{_coluna_valor(consulta)} {'<' if consulta.valor_max_exclusivo else '<='} ?", consulta.valor_max)
    if consulta.esfera:
        c.add("esfera = ?", consulta.esfera)
    if consulta.numero_controle_pncp:
        c.add("numero_controle_pncp = ?", consulta.numero_controle_pncp)
    if consulta.mes is not None and consulta.ano is not None and consulta.tabela == "contratacoes":
        coluna_mes = "mes_assinatura" if consulta.referencia_temporal == "assinatura" else "mes_publicacao"
        c.add(f"{coluna_mes} = ?", f"{consulta.ano}-{consulta.mes:02d}")


def _filtros_de_lista(consulta: Consulta, c: _Clausulas) -> None:
    """Modalidades e órgãos já resolvidos para os nomes do banco (``IN``)."""
    colunas_orgao = {
        "orgao": ("orgao_nome",),
        "unidade": ("unidade_nome",),
    }.get(consulta.papel_orgao, ("orgao_nome", "unidade_nome"))
    for coluna_valores, valores in (
        (("modalidade",), consulta.modalidades),
        (colunas_orgao, consulta.orgaos),
    ):
        if valores is None:
            continue
        if not valores:  # pedido que não casou com nada do banco: nenhum registro
            c.add("1 = 0")
            continue
        marcadores = ", ".join("?" for _ in valores)
        c.add("(" + " OR ".join(f"{coluna} IN ({marcadores})" for coluna in coluna_valores) + ")",
              *(valor for _coluna in coluna_valores for valor in valores))


def _filtros_analiticos(consulta: Consulta, c: _Clausulas) -> None:
    """Filtros por campo (fornecedor, município, CNPJ...); só colunas do esquema."""
    for campo, valor in consulta.filtros_analiticos:
        if campo == "cnpj":
            _filtro_de_cnpj(consulta, valor, c)
            continue
        if campo not in structured_db._TABELAS[consulta.tabela]:
            raise ValueError("Campo SQL não permitido")
        if campo == "fornecedor":
            from src.analysis.suppliers import raizes_do_fornecedor
            raizes = raizes_do_fornecedor(str(valor))
            if raizes:
                # O nome canônico é só apresentação; matriz e filiais podem ter
                # razões sociais diferentes. CNPJ explícito continua exato.
                c.add("substr(fornecedor_cnpj, 1, 8) IN (" + ", ".join("?" for _ in raizes) + ")", *raizes)
            else:
                c.add("LOWER(fornecedor) LIKE LOWER(?)", f"%{valor}%")
        elif campo in {"orgao_nome", "unidade_nome"}:
            c.add(f"LOWER({campo}) LIKE LOWER(?)", f"%{valor}%")
        else:
            c.add(f"{campo} = ?", valor)


def _filtro_de_cnpj(consulta: Consulta, valor: Any, c: _Clausulas) -> None:
    # Papel explicitamente identificado não pode casar com a outra entidade.
    papel = consulta.papel_cnpj
    if papel is not None:
        coluna = f"{papel}_cnpj"
        if coluna not in structured_db._TABELAS[consulta.tabela]:
            raise ValueError("Papel de CNPJ sem suporte no esquema SQL")
        c.add(f"{coluna} = ?", valor)
        return
    # Sem papel explícito, mantém compatibilidade do plano legado; a
    # nova análise registra papel_cnpj=None em vez de inventá-lo.
    colunas = [col for col in ("orgao_cnpj", "fornecedor_cnpj") if col in structured_db._TABELAS[consulta.tabela]]
    c.add("(" + " OR ".join(f"{coluna} = ?" for coluna in colunas) + ")", *(valor for _ in colunas))


def _filtro_de_objeto(termos: list[str], juncao: str, c: _Clausulas) -> None:
    """Termos do objeto contratado (com as expansões de sinônimo), unidos por ``juncao``.

    O texto do filtro é o de ``structured_db``: lá o SQLite com índice troca o
    LIKE pela busca no FTS5, com o mesmo resultado.
    """
    if not termos:
        return
    partes = []
    parametros: list[Any] = []
    for termo in termos:
        padroes = _EXPANSOES_DO_OBJETO.get(termo, (termo,))
        partes.append("(" + " OR ".join(structured_db.FILTRO_DE_OBJETO for _ in padroes) + ")")
        parametros.extend(f"%{padrao}%" for padrao in padroes)
    c.add("(" + f" {juncao} ".join(partes) + ")", *parametros)


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
    "(SELECT numero_controle_compra FROM contratacoes{recorte}{conector}"
    "numero_controle_compra IS NOT NULL AND tipo_registro IN "
    "('contrato_assinado', 'empenho', 'outro_instrumento')))"
)


_SEM_DUPLICATA_MODALIDADE = (
    "NOT (tipo_registro IN ('contrato_assinado', 'empenho', 'outro_instrumento') AND numero_controle_compra IS NOT NULL AND numero_controle_compra IN "
    "(SELECT numero_controle_pncp FROM contratacoes{recorte}{conector}tipo_registro = 'publicacao_compra'))"
)


def _usa_modalidade(consulta: Consulta) -> bool:
    return bool(consulta.modalidade_termos) or (
        consulta.tipo == "agrupamento" and "modalidade" in (consulta.agrupamentos or (consulta.agrupar_por,)))


def _n_registros(n: Any) -> str:
    """"1 registro", "2 registros": as tabelas mostravam "1 registros"."""
    return f"{n} registro" if n == 1 else f"{n} registros"


def _formatar_cnpj(cnpj: Any) -> str:
    digitos = re.sub(r"\D", "", str(cnpj))
    if len(digitos) != 14:
        return str(cnpj)
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


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


_MINIMO_VALORES_POR_ESFERA = 3  # com menos, mediana e média de uma esfera não sustentam comparação


def _comparacao_esferas_insuficiente(consulta: Consulta, por_esfera: dict[str, list[float]]) -> Optional[str]:
    """Motivo, quando a comparação entre esferas não tem duas esferas com valor informativo.

    Valor simbólico (< R$ 1,00) não entra na estatística, então uma esfera só
    com R$ 0,00 não sustenta comparação, como uma esfera sem registros.
    """
    if not consulta.comparacao_esferas:
        return None
    contagens = {esfera: sum(_tem_valor(v) for v in valores) for esfera, valores in sorted(por_esfera.items())}
    if consulta.esferas_comparadas:
        # Só as esferas que a pergunta compara, cada uma com um mínimo de
        # valores: na v7, a F1 ("estaduais pagaram mais que os municípios?")
        # comparava um único registro estadual, e uma esfera federal que a
        # pergunta não citava completava as "duas esferas com valor".
        pedidas = {esfera: contagens.get(esfera, 0) for esfera in consulta.esferas_comparadas}
        if all(n >= _MINIMO_VALORES_POR_ESFERA for n in pedidas.values()):
            return None
        detalhe = "; ".join(f"{esfera}: {n} valores >= R$ 1,00" for esfera, n in pedidas.items())
        return ("Dados insuficientes: não é possível comparar as esferas neste recorte, porque cada esfera "
                f"comparada precisa de pelo menos {_MINIMO_VALORES_POR_ESFERA} registros com valor >= R$ 1,00 "
                f"({detalhe}).")
    if sum(1 for n in contagens.values() if n) >= 2:
        return None
    detalhe = "; ".join(f"{esfera}: {n} valores >= R$ 1,00" for esfera, n in contagens.items()) or "nenhuma esfera com valor registrado"
    return ("Dados insuficientes: não é possível comparar as esferas neste recorte, porque menos de duas "
            f"têm registros com valor >= R$ 1,00 ({detalhe}).")


def _metricas_tabela(valores: list[float], *, n: Optional[int] = None) -> dict[str, Any]:
    informativos = [v for v in valores if _tem_valor(v)]
    return {"n": len(valores) if n is None else n,
            "total": sum((Decimal(str(v)) for v in valores), Decimal(0)) if valores else None,
            "media": statistics.fmean(informativos) if informativos else None,
            "mediana": statistics.median(informativos) if informativos else None,
            "minimo": min(informativos) if informativos else None,
            "maximo": max(informativos) if informativos else None}


def _tabela_metricas(consulta: Consulta, registros: list[dict], rotulo: str) -> str:
    nomes = {"total": "Total registrado", "media": "Média (valores ≥ R$ 1,00)",
             "mediana": "Mediana (valores ≥ R$ 1,00)", "minimo": "Mínimo (≥ R$ 1,00)",
             "maximo": "Máximo (≥ R$ 1,00)"}
    campos = (["total", "media", "mediana", "minimo", "maximo"] if consulta.comparacao_esferas
              else ["total"] if consulta.metrica == "contagem" and consulta.mostrar_total
              else [] if consulta.metrica == "contagem"
              else [{"soma": "total"}.get(consulta.metrica, consulta.metrica)])
    campos = [c for c in campos if c in nomes]
    return tabela_markdown([rotulo, "Registros", *(nomes[c] for c in campos)],
        [[r["rotulo"], _n_registros(r['n']), *(_brl(r.get(c)) for c in campos)] for r in registros],
        numericas=range(1, len(campos) + 2))


@dataclass
class _Execucao:
    """O que todo executor recebe: a consulta já decidida e o WHERE montado."""
    banco: Banco
    consulta: Consulta
    where: str
    parametros: list[Any]
    coluna_valor: str
    markdown: Optional[list[str]]


def _executar_identificador(e: _Execucao) -> tuple[str, list[str]]:
    """Um registro pelo número de controle do PNCP."""
    banco, consulta, where, parametros, markdown = e.banco, e.consulta, e.where, e.parametros, e.markdown
    sql = (
        "SELECT numero_controle_pncp, data_assinatura, orgao_nome, municipio, uf, "
        f"fornecedor, objeto, valor_contratado FROM {consulta.tabela}{where} LIMIT 1"
    )
    linhas = banco.consultar(sql, parametros)
    if markdown is not None:
        markdown.append(tabela_markdown(["PNCP", "Órgão", "Fornecedor", "Valor contratado", "Assinatura", "Objeto"],
            [[l["numero_controle_pncp"], l["orgao_nome"], l["fornecedor"], _brl(l["valor_contratado"]),
              l["data_assinatura"], _resumir(l["objeto"], 220)] for l in linhas], numericas=[3]))
    return sql, [
        f"PNCP {linha['numero_controle_pncp']} | órgão: {linha['orgao_nome']} | "
        f"fornecedor: {linha['fornecedor'] or 'não informado'} | "
        f"valor contratado: {_brl(linha['valor_contratado'])} | "
        f"assinatura: {linha['data_assinatura'] or 'não informada'} | "
        f"{_resumir(linha['objeto'], 220)}"
        for linha in linhas
    ] or [f"Nenhum contrato encontrado para o controle PNCP {consulta.numero_controle_pncp}."]


def _executar_cnpj_fornecedor(e: _Execucao) -> tuple[str, list[str]]:
    """CNPJs do fornecedor, os mais frequentes primeiro."""
    banco, consulta, where, parametros, markdown = e.banco, e.consulta, e.where, e.parametros, e.markdown
    # Um fornecedor pode ter mais de um CNPJ (matriz e filiais): mostra os
    # mais frequentes, com quantos registros cada um tem.
    sql = (
        f"SELECT fornecedor, fornecedor_cnpj, COUNT(*) AS n FROM {consulta.tabela}{where} "
        f"GROUP BY fornecedor, fornecedor_cnpj ORDER BY n DESC, fornecedor_cnpj LIMIT {int(consulta.limite)}"
    )
    linhas = banco.consultar(sql, parametros)
    if markdown is not None and linhas:
        markdown.append(tabela_markdown(["Fornecedor", "CNPJ", "Registros"],
            [[l["fornecedor"], _formatar_cnpj(l["fornecedor_cnpj"]) if l["fornecedor_cnpj"] else "não informado",
              f"{l['n']} registro{'s' if l['n'] != 1 else ''}"] for l in linhas], numericas=[2]))
    return sql, [
        f"Fornecedor {l['fornecedor']} | CNPJ: "
        f"{_formatar_cnpj(l['fornecedor_cnpj']) if l['fornecedor_cnpj'] else 'não informado'} | "
        f"{l['n']} registro{'s' if l['n'] != 1 else ''}"
        for l in linhas
    ] or ["Nenhum registro do fornecedor encontrado."]


def _executar_lista(e: _Execucao) -> tuple[str, list[str]]:
    """Ranking por valor, ou listagem com o total de registros que casaram."""
    banco, consulta, where, parametros, coluna_valor, markdown = e.banco, e.consulta, e.where, e.parametros, e.coluna_valor, e.markdown
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
            colunas = f"numero_controle_pncp, {coluna_data} AS data_referencia, orgao_nome, esfera, municipio, modalidade, fornecedor, objeto, {coluna_valor} AS valor"
    ordem = (f"CASE WHEN {coluna_valor} IS NULL THEN 1 ELSE 0 END, {coluna_valor} {consulta.ordem}"
             if consulta.tipo == "listagem" else f"{coluna_valor} {consulta.ordem}")
    sql = (
        f"SELECT {colunas} FROM {consulta.tabela}{where_valor} "
        f"ORDER BY {ordem} LIMIT {consulta.limite}"
    )
    linhas = banco.consultar(sql, parametros)
    if markdown is not None:
        if consulta.tabela == "pca_planos":
            markdown.append(tabela_markdown(["Posição", "Ano PCA", "Unidade", "Itens", "Valor registrado"],
                [[i, l["ano_pca"], l["unidade_nome"], l["quantidade_itens"], _brl(l["valor"])]
                 for i, l in enumerate(linhas, 1)], numericas=[0, 3, 4]))
        else:
            data_rotulo = "Assinatura" if consulta.referencia_temporal == "assinatura" else "Publicação"
            markdown.append(tabela_markdown(["Posição", "PNCP", "Órgão", "Fornecedor", "Valor registrado",
                                              data_rotulo, "Esfera", "Município", "Modalidade", "Objeto"],
                [[i, l["numero_controle_pncp"], l["orgao_nome"], l["fornecedor"], _brl(l["valor"]),
                  l["data_referencia"], l["esfera"], l["municipio"], l["modalidade"], _resumir(l["objeto"], 180)]
                 for i, l in enumerate(linhas, 1)], numericas=[0, 4]))
    if consulta.tipo == "listagem" and consulta.tabela == "contratacoes":
        total = banco.consultar(f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}", parametros)[0]["n"]
        if markdown is not None and linhas:
            markdown.insert(0, f"{total} registros atendem aos filtros; os {len(linhas)} de maior valor:")
        return sql, [f"{total} registros atendem aos filtros; os {len(linhas)} de maior valor:"] + [
            f"{i}. PNCP {l['numero_controle_pncp']} | {_brl(l['valor'])} | {l['data_referencia'] or 'não informada'} | {l['orgao_nome'] or 'não informado'} ({l['esfera'] or 'não informada'}, {l['municipio'] or 'não informado'}) | "
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
        f"{i}. {_brl(l['valor'])} | {l['data_referencia'] or 'não informada'} | {l['orgao_nome'] or 'não informado'} ({l['esfera'] or 'não informada'}, {l['municipio'] or 'não informado'}) | "
        f"modalidade: {l['modalidade'] or 'não informada'} | fornecedor: {l['fornecedor'] or 'não informado'} | "
        f"PNCP {l['numero_controle_pncp']} | {_resumir(l['objeto'], 180)}"
        for i, l in enumerate(linhas, 1)
    ]


def _executar_agrupamento(e: _Execucao) -> tuple[str, list[str]]:
    """Contagem e valores por grupo (modalidade, órgão, fornecedor...)."""
    banco, consulta, where, parametros, coluna_valor, markdown = e.banco, e.consulta, e.where, e.parametros, e.coluna_valor, e.markdown
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
    registros_tabela = []
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
        registros_tabela = [{"rotulo": " / ".join(str(k or 'não informado') for k in key),
                             **_metricas_tabela(values, n=contagens[key])}
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
        registros = banco.consultar(sql, parametros)
        registros_tabela = [{**l, "rotulo": " / ".join(str(l[c] or 'não informado') for c in campos)}
                            for l in registros]
        grupos = [
            f"{'Fornecedor ' if coluna == 'fornecedor' else ''}{' / '.join(str(l[c] or 'não informado') for c in campos)}: "
            f"{l['n']} registros, total {_brl(l['total'])}, média {_brl(l['media'])}, "
            f"mínimo {_brl(l['minimo'])}, máximo {_brl(l['maximo'])}"
            for l in registros
        ]
    if markdown is not None and registros_tabela:
        rotulos = {"orgao_nome": "Órgão", "fornecedor": "Fornecedor", "modalidade": "Modalidade",
                   "esfera": "Esfera", "municipio": "Município", "unidade_nome": "Unidade"}
        markdown.append(_tabela_metricas(consulta, registros_tabela, " / ".join(rotulos.get(c, c) for c in campos)))
    if not grupos and consulta.minimo_por_grupo is not None:
        grupos = [f"Nenhum {coluna} tem {consulta.minimo_por_grupo} ou mais registros com esses filtros."]
    if sem_campo and not grupos:
        # Sem nenhum grupo, a resposta é "não dá para saber", não um valor de
        # outro trecho do contexto (P4.2: "pregão" tirado de um item do PCA).
        motivo = " (os contratos assinados no PNCP não trazem modalidade)" if coluna == "modalidade" else ""
        grupos = [f"Nenhum dos {sem_campo} registros que atendem aos filtros informa {coluna}{motivo}: "
                  f"com estes dados não é possível dizer qual {coluna} é a mais frequente."]
    elif sem_campo:
        grupos.append(f"({_n_registros(sem_campo)} sem informação de {coluna} ficaram fora dos grupos.)")
    if consulta.incluir_total_geral:
        # Total de TODO o recorte, não só dos grupos que cabem no LIMIT.
        totais = banco.consultar(
            f"SELECT COUNT(*) AS n, COUNT(DISTINCT orgao_nome) AS orgaos, "
            f"SUM({coluna_valor}) AS total FROM {consulta.tabela}{where}", parametros,
        )[0]
        grupos.insert(0, f"Total geral: {_brl(totais['total'])}; {totais['n']} registros em "
                      f"{totais['orgaos']} órgãos; exibidos até {consulta.limite} órgãos por valor.")
    if markdown is not None:
        # Totais gerais e lacunas não são linhas de dados dos grupos.
        notas = [g for g in grupos if g.startswith(("Total geral:", "(", "Nenhum"))]
        markdown[:0] = [texto_markdown(n) for n in notas if n.startswith("Total geral:")]
        markdown.extend(texto_markdown(n) for n in notas if not n.startswith("Total geral:"))
    return sql, grupos


def _executar_evolucao(e: _Execucao) -> tuple[str, list[str]]:
    """Valores por mês (ou por ano do PCA) e a variação entre o primeiro e o último."""
    banco, consulta, where, parametros, coluna_valor, markdown = e.banco, e.consulta, e.where, e.parametros, e.coluna_valor, e.markdown
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
    if markdown is not None:
        markdown.append(_tabela_metricas(consulta, [
            {"rotulo": p, **_metricas_tabela(v)} for p, v in conhecidos], "Período"))
        markdown.extend(texto_markdown(l) for l in linhas if l.startswith(("Período observado", "Comparação", "Meses sem")))
    return sql, linhas


def _executar_estatistica(e: _Execucao) -> tuple[str, list[str]]:
    """Média, mediana, soma e contagem do recorte, por esfera quando há."""
    banco, consulta, where, parametros, coluna_valor, markdown = e.banco, e.consulta, e.where, e.parametros, e.coluna_valor, e.markdown
    if consulta.plano_tipado and consulta.metrica == "contagem":
        return _executar_contagem_tipada(e)
    coluna_esfera = "esfera" if consulta.tabela == "contratacoes" else "NULL"
    if consulta.tabela == "pca_planos" and consulta.metrica == "media_unitaria":
        sql = f"SELECT SUM(valor_total) AS total, SUM(quantidade_itens) AS qtd FROM {consulta.tabela}{where}"
        linha = banco.consultar(sql, parametros)[0]
        total = linha["total"] or 0
        qtd = linha["qtd"] or 0
        media = total / qtd if qtd else 0
        if markdown is not None:
            markdown.append(tabela_markdown(["Total planejado", "Quantidade de itens", "Média por item"],
                [[_brl(total), qtd, _brl(media)]], numericas=[0, 1, 2]))
            markdown.append("Média ponderada: total planejado dividido pela quantidade total de itens.")
        return sql, [
            f"Geral: Total {_brl(total)}; {qtd} itens; Média por item {_brl(media)}.",
            "Nota: Média ponderada calculada sobre o total planejado dividido pela quantidade total de itens."
        ]
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
    insuficiente = None
    if consulta.tabela == "contratacoes":
        por_esfera: dict[str, list[float]] = {}
        for l in todas:
            if l["valor"] is not None:
                por_esfera.setdefault(l["esfera"] or "não informada", []).append(float(l["valor"]))
        resultado += [f"Esfera {esfera}: {_estatisticas(v)}" for esfera, v in sorted(por_esfera.items())]
        insuficiente = _comparacao_esferas_insuficiente(consulta, por_esfera)
        if insuficiente:
            resultado.append(insuficiente)
    if markdown is not None and todas:
        # Com esferas citadas ("federais e estaduais"), a tabela mostra só elas;
        # a primeira linha continua sendo o recorte inteiro, com todas as esferas.
        rotulo_geral = ("Todas as esferas" if consulta.comparacao_esferas and consulta.esferas_comparadas
                        else "Recorte solicitado")
        registros = [{"rotulo": rotulo_geral, **_metricas_tabela(
            [float(l['valor']) for l in todas if l['valor'] is not None], n=len(todas))}]
        if consulta.comparacao_esferas:
            esferas = (consulta.esferas_comparadas
                       or sorted({l['esfera'] or 'não informada' for l in todas}))
            for esfera in esferas:
                grupo = [l for l in todas if (l['esfera'] or 'não informada') == esfera]
                registros.append({"rotulo": esfera.replace("nao_se_aplica", "não se aplica"), **_metricas_tabela(
                    [float(l['valor']) for l in grupo if l['valor'] is not None], n=len(grupo))})
        markdown.append(_tabela_metricas(consulta, registros, "Recorte"))
        if ausentes or simbolicos:
            markdown.append(f"{_n_registros(ausentes)} sem valor informado; {simbolicos} com valor abaixo de R$ 1,00. Campos ausentes não são zero.")
        if consulta.comparacao_esferas and insuficiente:
            markdown.append(texto_markdown(insuficiente))
    return sql, resultado


def _executar_contagem_tipada(e: _Execucao) -> tuple[str, list[str]]:
    """Contagem do plano tipado (intenção do LLM), incluindo registros sem valor."""
    banco, consulta, where, parametros, markdown = e.banco, e.consulta, e.where, e.parametros, e.markdown
    sql = f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}"
    n = banco.consultar(sql, parametros)[0]['n']
    if markdown is not None:
        markdown.append(tabela_markdown(["Métrica", "Resultado"], [["Contagem no recorte solicitado", _n_registros(n)]], numericas=[1]))
    return sql, [f"Contagem no recorte solicitado: {n} registros (inclui registros sem valor)."]


# Um executor por tipo de consulta. Tipo novo: uma função e uma linha aqui,
# sem mexer nos outros.
_EXECUTORES = {
    "identificador": _executar_identificador,
    "cnpj_fornecedor": _executar_cnpj_fornecedor,
    "ranking": _executar_lista,
    "listagem": _executar_lista,
    "agrupamento": _executar_agrupamento,
    "evolucao": _executar_evolucao,
    "estatistica": _executar_estatistica,
}


def _executar(banco: Banco, consulta: Consulta, termos: tuple[str, ...], juncao: str,
              markdown: Optional[list[str]] = None) -> tuple[str, list[str]]:
    executor = _EXECUTORES.get(consulta.tipo)
    if executor is None:
        raise ValueError(f"Tipo de consulta SQL desconhecido: {consulta.tipo!r}")
    where, parametros = _where(consulta, termos, juncao)
    return executor(_Execucao(banco, consulta, where, parametros, _coluna_valor(consulta), markdown))
