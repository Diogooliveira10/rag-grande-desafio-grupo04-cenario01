"""Filtros de metadados da busca: igualdade, faixa, datas, valores, nomes de órgão e recorte por base.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Iterable, Mapping

from src.retrieval.identifiers import _CNPJ_RE
from src.retrieval.lexical import _tokens
from src.retrieval.records import _NATUREZAS, _metadata_of, _record_base_id, _record_natureza


def _metadata_value(metadata: Mapping[str, Any], key: str) -> Any:
    if key in metadata:
        return metadata[key]
    extras = metadata.get("extras")
    if isinstance(extras, Mapping):
        return extras.get(key)
    return None


def _values_equal(left: Any, right: Any) -> bool:
    left_normalized, right_normalized = _comparable_values(left, right)
    return left_normalized == right_normalized


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    for formatter in (date.fromisoformat, lambda item: datetime.strptime(item, "%Y%m%d").date()):
        try:
            return formatter(candidate)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_number(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    if not isinstance(value, str):
        return None
    candidate = value.strip().replace("R$", "").replace(" ", "")
    if not candidate:
        return None
    if "," in candidate:
        if candidate.count(",") != 1:
            return None
        integer, fraction = candidate.split(",")
        if "." in integer:
            if not re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", integer):
                return None
            integer = integer.replace(".", "")
        candidate = f"{integer}.{fraction}"
    elif candidate.count(".") > 1:
        if not re.fullmatch(r"[+-]?[1-9]\d{0,2}(?:\.\d{3})+", candidate):
            return None
        candidate = candidate.replace(".", "")
    elif candidate.count(".") == 1:
        integer, fraction = candidate.split(".")
        if len(fraction) == 3 and re.fullmatch(r"[+-]?[1-9]\d{0,2}", integer):
            candidate = f"{integer}{fraction}"
    try:
        return Decimal(candidate)
    except InvalidOperation:
        return None


def _comparable_values(left: Any, right: Any) -> tuple[Any, Any]:
    left_date, right_date = _parse_date(left), _parse_date(right)
    if left_date is not None and right_date is not None:
        return left_date, right_date
    left_number, right_number = _parse_number(left), _parse_number(right)
    if left_number is not None and right_number is not None:
        return left_number, right_number
    return left, right


def _condicoes(expected: Any) -> list[tuple[str, Any]]:
    """Normaliza o valor de um filtro numa lista de condições (operator, value).

    Mantém os formatos já suportados (valor direto = igualdade; dict com
    "operator"/"op" e "value"; tupla/lista de 2 elementos (operator, value))
    e acrescenta um formato novo: lista de dicts, para expressar várias
    condições sobre a MESMA chave com semântica E — ex. uma faixa de valor
    (>= mínimo e <= máximo) ou um intervalo de datas (>= início do ano e <
    início do ano seguinte), usado por ``src/query_analysis.py``.
    """
    if isinstance(expected, Mapping):
        operator = str(expected.get("operator", expected.get("op", "==")))
        return [(operator, expected.get("value"))]
    if isinstance(expected, (tuple, list)):
        if expected and all(isinstance(item, Mapping) for item in expected):
            return [
                (str(item.get("operator", item.get("op", "=="))), item.get("value"))
                for item in expected
            ]
        if len(expected) == 2:
            return [(str(expected[0]), expected[1])]
    return [("==", expected)]


def _sem_caixa_e_acento(texto: str) -> str:
    sem_acento = "".join(
        char for char in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(char)
    )
    return " ".join(sem_acento.split())


def _condicao_satisfeita(actual: Any, operator: str, value: Any) -> bool:
    try:
        if operator in {"==", "="}:
            return _values_equal(actual, value)
        if operator == "!=":
            return not _values_equal(actual, value)
        if operator in {">", ">=", "<", "<="}:
            left_date, right_date = _parse_date(actual), _parse_date(value)
            if left_date is not None or right_date is not None:
                if left_date is None or right_date is None:
                    return False
                left, right = left_date, right_date
            else:
                left_number, right_number = _parse_number(actual), _parse_number(value)
                if left_number is not None or right_number is not None:
                    if left_number is None or right_number is None:
                        return False
                    left, right = left_number, right_number
                else:
                    left, right = actual, value
            if type(left) is not type(right):
                return False
            comparisons = {
                ">": left > right,
                ">=": left >= right,
                "<": left < right,
                "<=": left <= right,
            }
            return comparisons[operator]
        if operator == "in":
            candidates = value if isinstance(value, (list, tuple, set)) else [value]
            return any(_values_equal(actual, candidate) for candidate in candidates)
        if operator == "contains":
            if isinstance(actual, str):
                # Sem caixa nem acento: "Secretaria de Saúde" (pergunta) casa
                # com "SECRETARIA DE ESTADO DE SAUDE" (razaoSocial do PNCP).
                return _sem_caixa_e_acento(str(value)) in _sem_caixa_e_acento(actual)
            return value in actual
        return False
    except (TypeError, ValueError, KeyError):
        return False


def filtro_opcional(expected: Any) -> bool:
    """Filtro com ``"optional": True`` não descarta chunk que não tem o campo."""
    if isinstance(expected, Mapping):
        return bool(expected.get("optional"))
    if isinstance(expected, (tuple, list)) and expected:
        return all(isinstance(item, Mapping) and item.get("optional") for item in expected)
    return False


# Nome antigo, usado dentro deste módulo e pelos testes.
_filtro_opcional = filtro_opcional


_COMPARACOES = frozenset({">", ">=", "<", "<="})


def _sem_valor_comparavel(actual: Any, expected: Any) -> bool:
    """Campo presente, mas vazio ou ilegível para uma comparação (``""``,
    ``"Sigiloso"``, ``{}``): num filtro opcional, conta como campo ausente."""
    if actual in ("", [], {}) or (isinstance(actual, str) and not actual.strip()):
        return True
    operadores = {operator for operator, _ in _condicoes(expected)}
    if operadores and operadores <= _COMPARACOES:
        return _parse_date(actual) is None and _parse_number(actual) is None
    return False


def _filtro_aplicavel(metadata: Mapping[str, Any], key: str, expected: Any) -> bool:
    """Recortes do analisador são obrigatórios só na natureza correspondente.

    Filtros diretos da API (sem optional=False explícito) conservam a semântica
    estrita original. Natureza desconhecida também nunca afrouxa um filtro.
    """
    conditions = expected if isinstance(expected, list) else [expected]
    if not conditions or not all(isinstance(c, Mapping) and c.get("optional") is False for c in conditions):
        return True
    natureza = _record_natureza(metadata, metadata)
    if natureza not in _NATUREZAS:
        return True
    if key in {"objeto_auxiliar", "objeto_termo"}:
        return natureza in {"estruturada", "conversacional"}
    if key == "data_vigencia_inicio":
        return natureza == "normativa"
    if key == "ano_pca":
        return natureza == "agregada"
    if key in {"data_assinatura", "data_resultado", "orgao_cnpj", "fornecedor_cnpj"}:
        return natureza == "estruturada"
    if key == "unidade_nome":
        return natureza in {"estruturada", "agregada"}
    if key == "data_publicacao":
        return natureza in {"estruturada", "normativa", "conversacional"}
    if key == "data_vigencia":
        # Vigência normativa usa início; ano de PCA usa ano_pca, não este campo.
        return natureza in {"estruturada", "conversacional"}
    if natureza == "normativa" and key in {
        "valor", "fornecedor", "modalidade", "cnpj", "orgao_cnpj", "fornecedor_cnpj", "data_assinatura", "data_resultado",
    }:
        # Normas têm órgão emissor/UF e data de publicação; estes filtros
        # continuam aplicáveis. Só ignorar critérios próprios de contratação.
        return False
    return True


_CONECTORES_DE_NOME = frozenset({"de", "do", "da", "dos", "das", "e"})


@lru_cache(maxsize=1 << 16)
def _tokens_de_nome(texto: str) -> frozenset[str]:
    """Tokens de um nome de órgão/unidade: ~1.800 nomes se repetem nos 107 mil contratos."""
    return frozenset(_tokens(texto))


def _matches_filters(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None,
                     *, content: str = "") -> bool:
    if not filters:
        return True
    for key, expected in filters.items():
        if not _filtro_aplicavel(metadata, key, expected):
            continue
        actual = _metadata_value(metadata, key)
        if key in {"objeto_auxiliar", "objeto_termo"}:
            from src.retrieval_constraints import objeto_auxiliar, objeto_do_registro, radicais_do_objeto
            objeto = objeto_do_registro(metadata, content)
            actual = None if objeto is None else (
                objeto_auxiliar(objeto) if key == "objeto_auxiliar" else radicais_do_objeto(objeto)
            )
        if key in {"orgao_cnpj", "fornecedor_cnpj"}:
            actual = re.sub(r"\D", "", str(actual)) if actual is not None else None
        if key == "cnpj":
            # União dos identificadores do corpo e dos campos canônicos. Não
            # confundir CNPJ com CPF nem com prefixo do controle PNCP.
            text = content + " " + json.dumps(dict(metadata), ensure_ascii=False, default=str)
            actual = ["".join(match.groups()) for match in _CNPJ_RE.finditer(text)]
        if actual is None or (_filtro_opcional(expected) and _sem_valor_comparavel(actual, expected)):
            if _filtro_opcional(expected):
                continue
            return False
        for operator, value in _condicoes(expected):
            # A análise pede "==" e a API costuma pedir "contains": nos dois, o
            # mesmo CNPJ raiz é a mesma empresa, com qualquer grafia do nome.
            if key == "fornecedor" and operator in {"==", "contains"}:
                from src.analysis.suppliers import mesma_razao_social, raizes_do_fornecedor, raiz_cnpj
                raizes = raizes_do_fornecedor(str(value))
                cnpj = _metadata_value(metadata, "fornecedor_cnpj")
                # O payload vetorial antigo só tem o nome. Resolva sua grafia
                # pelo catálogo; não exija regravar os 143 mil vetores antigos.
                raizes_atuais = (raiz_cnpj(cnpj),) if cnpj is not None else raizes_do_fornecedor(str(actual))
                if raizes and raizes_atuais:
                    if not set(raizes).intersection(raizes_atuais):
                        return False
                elif not (_condicao_satisfeita(actual, operator, value)
                          or mesma_razao_social(str(actual), str(value), exata=operator == "==")):
                    return False
            elif key in {"orgao", "unidade_nome"} and operator == "contains" and isinstance(actual, str):
                # Nomes admitem qualificadores intermediários e abreviações do
                # PNCP: "Secretaria de Estado de Saúde" casa com "SECRET ESTADO
                # SAUDE". Exigir cada termo informativo no campo da entidade,
                # sem procurar no objeto ou fornecedor.
                requeridos = _tokens_de_nome(str(value)) - _CONECTORES_DE_NOME
                disponiveis = _tokens_de_nome(actual)
                casa = lambda termo: any(
                    token == termo or (len(token) >= 4 and termo.startswith(token))
                    or (len(termo) >= 4 and token.startswith(termo))
                    for token in disponiveis
                )
                if not requeridos or not all(casa(termo) for termo in requeridos):
                    return False
            elif not _condicao_satisfeita(actual, operator, value):
                return False
    return True


def _filtrar_por_base(
    records: Iterable[Mapping[str, Any]],
    filters: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, list[str]]]]:
    """Aplica filtros obrigatórios; só os explicitamente opcionais podem relaxar.

    No relaxamento opcional, um filtro só vale se ao menos um chunk tem o campo (ex.:
    ``valor`` não existe na base normativa) e se ao menos um desses chunks
    passa nele: um filtro que reprova todos os chunks que têm o campo está
    lendo errado aquela base (ex.: órgão "Secretaria de Saúde" contra os
    indicadores do PCA, cujo órgão é "Estado do Rio de Janeiro"), e deixaria
    só os chunks sem o campo. Se os filtros que sobram, juntos, excluem todos
    os chunks da base, ela entra sem filtro. Devolve os registros e, por
    ``base_id``, as chaves ignoradas (``sem_campo`` / ``nenhum_chunk_passou``
    / ``zerou_resultados``).
    """
    por_base: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        base_id = _record_base_id(record, _metadata_of(record))
        por_base.setdefault(base_id, []).append(record)
    selecionados: list[dict[str, Any]] = []
    ignorados: dict[str, dict[str, list[str]]] = {}
    for base_id, registros in por_base.items():
        metadados = [_metadata_of(record) for record in registros]
        motivo: dict[str, list[str]] = {}
        obrigatorios = {key: value for key, value in filters.items() if not _filtro_opcional(value)}
        aplicaveis: dict[str, Any] = dict(obrigatorios)
        for key, value in filters.items():
            if key in obrigatorios:
                continue
            com_campo = [m for m in metadados if _metadata_value(m, key) is not None]
            if not com_campo:
                motivo.setdefault("sem_campo", []).append(key)
            elif not any(_matches_filters(m, {key: value}) for m in com_campo):
                motivo.setdefault("nenhum_chunk_passou", []).append(key)
            else:
                aplicaveis[key] = value
        for chave in motivo:
            motivo[chave].sort()
        passam = [
            record
            for record, metadata in zip(registros, metadados)
            if _matches_filters(metadata, aplicaveis, content=str(record.get("text", "")))
        ]
        if aplicaveis and not passam and not obrigatorios:
            passam = registros
            motivo["zerou_resultados"] = sorted(aplicaveis)
        if motivo:
            ignorados[base_id] = motivo
        selecionados.extend(passam)
    return selecionados, ignorados
