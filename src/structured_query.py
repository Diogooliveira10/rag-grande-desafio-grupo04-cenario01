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
from src.domain import purchase_status
from src.obs import Stage, default_logger
from src.query_analysis import texto_sem_filtros
from src.schema import Chunk, ChunkMetadata, Natureza, QueryFilters, QueryIntent
from src.structured_db import Banco
from src.sql_markdown import tabela_markdown, texto_markdown
from src.sensitivity import mascarar
from src.sql_query.detection import (  # noqa: F401  (reexportados)
    Consulta,
    LIMITE_RANKING,
    _COLUNA_DO_TIPO_DE_VALOR,
    _ESFERAS,
    _MESES,
    _MODALIDADES,
    _NUMEROS_POR_EXTENSO,
    _PALAVRAS_GENERICAS,
    _PALAVRAS_ORGAO_VAZIAS,
    _RADICAIS_GENERICOS,
    _RE_AGENTE,
    _RE_AGRUPAMENTO,
    _RE_CITACAO_DE_NORMA,
    _RE_CNPJ_COM_CONTA,
    _RE_COMPARACAO,
    _RE_CONTAGEM_CONTRATOS,
    _RE_CONTRATOS,
    _RE_ESTATISTICA,
    _RE_EVOLUCAO,
    _RE_LISTAGEM,
    _RE_LISTAGEM_CONTRATACOES,
    _RE_MES,
    _RE_NORMA_NUMERADA,
    _RE_NUMERO_CONTROLE_PNCP,
    _RE_N_RANKING,
    _RE_OBJETO_CONTRATACAO,
    _RE_PAGAMENTO,
    _RE_PCA,
    _RE_PEDIDO_ALEM_DA_LISTA,
    _RE_PEDIDO_DE_CNPJ,
    _RE_QUAIS_MODALIDADES,
    _RE_RANKING,
    _Recorte,
    _TIPO_CODIGO_INVALIDO,
    _agrupamento_da_pergunta,
    _campos_de_periodo,
    _consulta_de_um_registro,
    _esferas_da_pergunta,
    _limite_da_pergunta,
    _mes_da_pergunta,
    _minimo_por_grupo,
    _modalidade_e_objeto,
    _orgao_da_pergunta,
    _recorte_da_pergunta,
    _singular,
    _situacoes_da_pergunta,
    _tabela_da_pergunta,
    _termos_do_objeto,
    _termos_objeto_da_pergunta,
    _tipo_da_pergunta,
    consulta_da_intencao,
    detectar_consulta,
    requer_calculo,
)
from src.sql_query.execution import (  # noqa: F401  (reexportados)
    _Clausulas,
    _EXECUTORES,
    _EXPANSOES_DO_OBJETO,
    _Execucao,
    _MINIMO_VALORES_POR_ESFERA,
    _SEM_DUPLICATA,
    _SEM_DUPLICATA_MODALIDADE,
    _VALOR_MINIMO_INFORMADO,
    _brl,
    _clausula_de_situacao,
    _coluna_valor,
    _comparacao_esferas_insuficiente,
    _estatisticas,
    _executar,
    _executar_agrupamento,
    _executar_cnpj_fornecedor,
    _executar_contagem_tipada,
    _executar_estatistica,
    _executar_evolucao,
    _executar_identificador,
    _executar_lista,
    _filtro_de_cnpj,
    _filtro_de_objeto,
    _filtros_analiticos,
    _filtros_de_lista,
    _filtros_de_periodo,
    _filtros_de_registro,
    _filtros_de_valor_e_campos,
    _formatar_cnpj,
    _metricas_tabela,
    _n_registros,
    _resumir,
    _tabela_metricas,
    _tem_valor,
    _usa_modalidade,
    _where,
)

DOCUMENTS_FILE = structured_db.DOCUMENTS_FILE


# Palavra do nome do banco que muda o órgão quando a pergunta não a diz:
# "Universidade do Estado do RJ" não é a "Universidade FEDERAL do Estado do RJ".
_QUALIFICADORES_DE_ORGAO = frozenset({"federal", "regional"})


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
        "campos": frozenset({"valor_total", "quantidade_itens", "preco_medio_item"}),
        "grupos": frozenset({"orgao_nome", "municipio", "unidade_nome", "categoria", "classe"}),
        "datas": {"pca": "ano_pca", "resultado": "data_resultado"},
    },
}
_METRICAS_SQL = {
    "contagem": "COUNT(*)", "soma": "SUM({campo})", "media": "AVG({campo})",
    "minimo": "MIN({campo})", "maximo": "MAX({campo})",
    "mediana": "percentile_cont(0.5) WITHIN GROUP (ORDER BY {campo})",
    "media_unitaria": "SUM(valor_total) / NULLIF(SUM(quantidade_itens), 0)",
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


# --------------------------------------------------------------------------
# Execução (SQL com "?" como marcador; Banco.consultar adapta ao Postgres)
# --------------------------------------------------------------------------


# Situação da compra: regra de negócio, em src/domain/purchase_status.py.
_SITUACOES_INVALIDAS = purchase_status.SITUACOES_INVALIDAS


def _descrever_termo(termo: str) -> str:
    return f"{termo} (TI: tecnologia da informação, informática, software, TIC)" if termo == "tecnologia" else termo


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
            origem += (" Publicação de compra não é somada aos instrumentos vinculados no mesmo recorte."
                       + (" Na contagem por modalidade prevalece a compra." if _usa_modalidade(consulta)
                          else " Contratos/empenhos distintos são preservados; isto não é uma contagem de processos."))
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
        entidade = "unidade responsável" if consulta.papel_orgao == "unidade" else "órgão"
        filtros.append(
            f"{entidade} em {', '.join(consulta.orgaos[:5])}{'…' if len(consulta.orgaos) > 5 else ''}" if consulta.orgaos
            else f"{entidade} {' '.join(consulta.orgao_termos)} (nenhum {'órgão' if consulta.papel_orgao != 'unidade' else 'unidade responsável'} do corpus casa com esse nome)"
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
        mudancas["modalidades"] = tuple(sorted(
            v for v in banco.distintos("contratacoes", "modalidade")
            if all(t in structured_db.normalizar(v) for t in consulta.modalidade_termos)
        ))
    # Órgão que o query_analysis não reconheceu (minúsculas, "Fundo", "Comando"…)
    # chega como termos do objeto; com duas palavras ou mais que formam o nome de
    # um órgão do banco ("comando aeronautica"), vira filtro de órgão.
    termos_como_orgao = (
        not consulta.orgao_termos and len(consulta.termos_objeto) >= 2 and consulta.tabela == "contratacoes"
        and not dict(consulta.filtros_analiticos).get("fornecedor")
    )
    if consulta.orgao_termos or termos_como_orgao:
        colunas = {
            "orgao": ("orgao_nome",),
            "unidade": ("unidade_nome",),
        }.get(consulta.papel_orgao, ("orgao_nome", "unidade_nome"))
        nomes_por_coluna = {coluna: banco.distintos(consulta.tabela, coluna) for coluna in colunas}
        nomes = set().union(*nomes_por_coluna.values())
        if consulta.orgao_termos:
            mudancas["orgaos"] = tuple(sorted(n for n in nomes if _orgao_casa(consulta.orgao_termos, n)))
        else:
            casados = tuple(sorted(n for n in nomes if _orgao_casa(consulta.termos_objeto, n)))
            if casados:
                mudancas.update(orgaos=casados, orgao_termos=consulta.termos_objeto, termos_objeto=())
    return replace(consulta, **mudancas) if mudancas else consulta


def executar_consulta(consulta: Consulta, banco: Banco, recorte: Optional[dict] = None,
                     *, markdown: Optional[list[str]] = None) -> tuple[str, str, list[str]]:
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

    # A mesma tentativa é contada para decidir e de novo para o recorte: uma
    # consulta a cada vez (~0,08 s numa varredura da tabela).
    contagens: dict[tuple[tuple[str, ...], str], int] = {}

    def contagem(termos: tuple[str, ...], juncao: str = "AND") -> int:
        if (termos, juncao) not in contagens:
            where, parametros = _where(consulta, termos, juncao)
            contagens[(termos, juncao)] = banco.consultar(
                f"SELECT COUNT(*) AS n FROM {consulta.tabela}{where}", parametros)[0]["n"]
        return contagens[(termos, juncao)]

    tentativas = [(consulta.termos_objeto, "AND")]
    if len(consulta.termos_objeto) > 1:
        uteis = tuple(termo for termo in consulta.termos_objeto if contagem((termo,)))
        if uteis and uteis != consulta.termos_objeto:
            tentativas.append((uteis, "AND"))
        if len(uteis) > 1:
            tentativas.append((uteis, "OR"))
    for indice, (termos, juncao) in enumerate(tentativas):
        if not termos or contagem(termos, juncao):
            tabelas: list[str] = []
            sql, linhas = _executar(banco, consulta, termos, juncao, markdown=tabelas if markdown is not None else None)
            # Contagem que também mostra o total (mostrar_total) recebe o alerta,
            # porque o total exibido depende do valor fora do padrão; a comparação
            # entre esferas também, pois a tabela dela traz total, média e máximo.
            if (consulta.tipo in {"ranking", "estatistica", "agrupamento", "evolucao"}
                    and (consulta.metrica != "contagem" or consulta.mostrar_total
                         or consulta.comparacao_esferas)):
                where, params = _where(consulta, termos, juncao)
                coluna = _coluna_valor(consulta)
                extremo = banco.consultar(
                    f"SELECT MAX({coluna}) AS maior FROM {consulta.tabela}{where}", params,
                )[0]["maior"]
                if extremo is not None and float(extremo) >= config.VALUE_REVIEW_THRESHOLD:
                    linhas.append(
                        f"Alerta de qualidade: o recorte contém valor registrado de {_brl(extremo)}, "
                        "igual ou acima do limiar de conferência. Confirme na fonte PNCP antes de interpretar "
                        "totais ou rankings; os valores foram preservados, sem correção ou exclusão."
                    )
            if recorte is not None:
                _preencher_recorte(recorte, consulta, banco, total, contagem(termos, juncao),
                                   termos, juncao, relaxados=_relaxamentos(indice, consulta, termos, juncao))
            if markdown is not None and (recorte is None or recorte.get("registros_correspondentes")):
                tabelas.extend(texto_markdown(l) for l in linhas if l.startswith("Alerta de qualidade:"))
                markdown.extend(m for m in tabelas if m)
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


def _vazio(campo: str) -> str:
    """Condição de campo ausente: NULL, e também string vazia nos de texto."""
    return f"{campo} IS NULL" if campo in _CAMPOS_NUMERICOS else f"({campo} IS NULL OR {campo} = '')"


def _completude(banco: Banco, tabela: str, where: str, parametros: list[Any],
                campos: Sequence[str]) -> list[dict[str, Any]]:
    """Conhecidos/desconhecidos por campo, sobre o recorte já filtrado.

    Uma consulta para todos os campos (era uma por campo, cada uma varrendo o
    mesmo recorte); ``campos`` vem das colunas reais do esquema.
    """
    if not campos:
        return []
    somas = ", ".join(f"SUM(CASE WHEN {_vazio(campo)} THEN 1 ELSE 0 END) AS d{indice}"
                      for indice, campo in enumerate(campos))
    linha = banco.consultar(f"SELECT COUNT(*) AS n, {somas} FROM {tabela}{where}", parametros)[0]
    total_campo = int(linha["n"] or 0)
    saida: list[dict[str, Any]] = []
    for indice, campo in enumerate(campos):
        desconhecidos = int(linha[f"d{indice}"] or 0)
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
            recorte: Optional[dict[str, Any]] = None, *, markdown: str = "") -> list[Chunk]:
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
    sem_registros = bool(recorte and recorte.get("tipo_resultado") == "sem_correspondencia") or not linhas or all(
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
                            # A primeira evidência também contém a apresentação
                            # integral da consulta, não limitada ao prompt.
                            "markdown": markdown if indice == 1 else "",
                            "metrica": consulta.metrica, "tipo": consulta.tipo,
                            "comparacao_esferas": consulta.comparacao_esferas,
                            "politica": _POLITICA_LISTAGEM if consulta.tipo == "listagem" else
                            "Totais somam os valores registrados, inclusive zeros/centavos; "
                            "média, mediana, mínimo e máximo consideram valores >= R$ 1,00. "
                            "Campos ausentes não são zero. Esta amostra não é um censo do RJ.",
                            "recorte": recorte},
                        "calculo_disponivel": (
                            False if recorte and recorte.get("tipo_resultado") == "calculo_indisponivel"
                            else None
                        ),
                        "valor_unitario_indisponivel": bool(
                            recorte and recorte.get("valor_unitario_indisponivel")
                        ),
                        # Listagem vazia não dispensa a busca: o objeto pode
                        # estar com outra palavra ("UTI móvel"), e a busca
                        # semântica ainda acha; o LLM recebe o zero e os trechos.
                        "geracao_deterministica": (consulta.tipo in {"estatistica", "ranking", "agrupamento", "evolucao"}
                                                   or (consulta.tipo == "cnpj_fornecedor" and not sem_registros)
                                                   or (consulta.listagem_direta and not sem_registros)
                                                   or (recorte and recorte.get("valor_unitario_indisponivel")
                                                       and not sem_registros))
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
    return bool(consulta and (consulta.tipo in {"estatistica", "ranking", "agrupamento", "evolucao", "cnpj_fornecedor"}
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
            structured_db.validar_banco(documents_file, banco)
            with structured_db.observar_consultas() as consultas:
                span.extra["execucao_sql"] = consultas
                recorte: dict[str, Any] = {}
                with banco.sessao():
                    tabelas: list[str] = []
                    sql, cabecalho, linhas = executar_consulta(consulta, banco, recorte, markdown=tabelas)
                valor_unitario_indisponivel = filtros.tipo_valor == "unitario" or (
                    intencao is not None and intencao.filtros.tipo_valor == "unitario"
                )
                if valor_unitario_indisponivel:
                    recorte.update({
                        "calculo_disponivel": False,
                        "tipo_resultado": "calculo_indisponivel",
                        "valor_unitario_indisponivel": True,
                        "calculo_motivo": (
                            "o preço por unidade não está disponível nos dados; "
                            "o valor total do contrato não pode substituí-lo"
                        ),
                    })
            markdown = "\n\n".join(tabelas)
            chunks = _chunks(consulta, sql, cabecalho, linhas, banco, recorte, markdown=markdown)
            span.extra.update({
                "sql": sql, "linhas": len(linhas), "chunks": len(chunks),
                # O que a consulta devolveu, para acompanhar o dado até a resposta.
                "cabecalho": cabecalho,
                "resultado": linhas[:_LINHAS_NO_LOG],
                # Markdown completo, além do resumo textual legado. Nenhuma
                # linha da tabela é removida pelo limite _LINHAS_NO_LOG.
                "resultado_markdown": mascarar(markdown)[0],
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
