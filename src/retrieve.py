from __future__ import annotations

import heapq
import json
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import config
from src.domain import fusion
from src.obs import Stage, TokenUsage, default_logger, registrar, token_usage_from_response
from src.schema import Natureza


DEFAULT_RRF_K = 60
DEFAULT_TOP_K = 10
DEFAULT_FETCH_K = 50
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_PNCP_CONTROL_RE = re.compile(r"(?<!\d)\d{14}-\d-\d{6}/\d{4}(?:-\d{6})?(?!\d)")
# CNPJ com ou sem pontuação; os 14 dígitos no começo de um número de controle
# ficam de fora pelo "-" que vem depois deles.
_CNPJ_RE = re.compile(r"(?<![\d./-])(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?![\d/-])")
_CONTRACT_NUMBER_RE = re.compile(
    r"\bcontrato\s*(?:n(?:[uú]mero|ro)?\.?\s*[º°o]?\s*)?"
    r"(\d+)\s*/\s*(\d{4})(?!\d)", re.IGNORECASE
)
MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"
_NATUREZAS = frozenset(item.value for item in Natureza)

EmbedFn = Callable[[list[str]], list[list[float]]]
RerankFn = Callable[[str, list["RetrievalResult"]], Sequence[Any]]
QUERY_INSTRUCTION = "Given a public procurement question, retrieve relevant passages that answer the query"
QUERY_CACHE_PATH = config.ROOT_DIR / "data" / "embeddings" / "query_cache.jsonl"
_EMBEDDING_FAILURES: dict[tuple[str, str], float] = {}


class EmbeddingUnavailable(ConnectionError):
    """Endpoint em cooldown ou busca densa desabilitada explicitamente."""


def embedding_query_text(query: str) -> str:
    """Instrução Qwen exclusiva da busca densa; BM25/roteamento recebem o original."""
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query}"


@dataclass
class RetrievalResult:
    """Resultado de uma estratégia ou da fusão, com proveniência preservada."""

    chunk_id: str
    content: str
    metadata: dict[str, Any]
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    document_id: str | None = None
    source_path: str | None = None
    _record: dict[str, Any] = field(default_factory=dict, repr=False)

    def __getitem__(self, key: str) -> Any:
        """Permite consumo como mapeamento sem perder o acesso tipado."""
        return self.to_dict()[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "content": self.content,
            "metadata": self.metadata,
            "score": self.score,
            "dense_score": self.dense_score,
            "sparse_score": self.sparse_score,
            "rrf_score": self.rrf_score,
            "rerank_score": self.rerank_score,
            "document_id": self.document_id,
            "source_path": self.source_path,
        }


# Arquivo lido -> (mtime, tamanho, registros). Cada pergunta lia de novo os
# vectors.jsonl e o documents.jsonl (~150 MB de JSON), uns 3 s por consulta;
# a assinatura do arquivo invalida a entrada quando ele é regravado.
_JSONL_CACHE: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}


_RE_EMBEDDING = re.compile(r'"embedding":\s*\[[^\]]*\]\s*,?\s*')


def _read_jsonl(path: Path, *, sem_vetor: bool = False) -> list[dict[str, Any]]:
    """Registros do arquivo, lidos uma vez enquanto ele não muda.

    Os dicts são compartilhados entre as chamadas: quem precisar alterar um
    registro copia antes (``dict(record)``), como ``_records_for`` já faz.

    ``sem_vetor`` descarta o campo ``embedding`` antes de decodificar a linha:
    com o FAISS ao lado, o vetor não é usado na consulta. Na v7, os vetores dos
    contratos passam de ~4 GB e 3 min para ~700 MB e 12 s.
    """
    try:
        stat = path.stat()
    except FileNotFoundError:
        return []
    chave = f"{path}|sem_vetor" if sem_vetor else str(path)
    guardado = _JSONL_CACHE.get(chave)
    if guardado and guardado[0] == stat.st_mtime_ns and guardado[1] == stat.st_size:
        return list(guardado[2])
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(_RE_EMBEDDING.sub("", line, count=1) if sem_vetor else line))
    _JSONL_CACHE[chave] = (stat.st_mtime_ns, stat.st_size, records)
    return list(records)


def _registros_do_indice(vectors_path: Path) -> list[dict[str, Any]]:
    """Registros de uma base do índice, na ordem do FAISS.

    Com ``vectors.faiss`` ao lado, os vetores ficam no FAISS e o JSON é lido
    sem eles; sem o FAISS, o vetor é necessário para montar o índice em memória.
    """
    return _read_jsonl(vectors_path, sem_vetor=(vectors_path.parent / "vectors.faiss").exists())


@lru_cache(maxsize=4)
def _manifest_bases(manifest_path: Path = MANIFEST_PATH) -> dict[str, dict[str, Any]]:
    """``base_id`` -> declaração da base no manifesto (natureza, escopo...)."""
    if not manifest_path.exists():
        return {}
    from src.fetch_corpus import load_manifest

    return {str(base["base_id"]): base for base in load_manifest(manifest_path)["bases"]}


def _split_selection(bases: Iterable[Any] | None) -> tuple[set[str], set[str]] | None:
    """Separa a seleção do roteador em (naturezas, base_ids explícitos).

    Um item que é nome de natureza (``Natureza`` ou "estruturada") seleciona
    todas as bases daquela natureza; qualquer outro valor é tratado como
    ``base_id``.
    """
    if bases is None:
        return None
    if isinstance(bases, str):
        bases = [bases]
    naturezas: set[str] = set()
    base_ids: set[str] = set()
    for base in bases:
        value = str(getattr(base, "value", base))
        (naturezas if value in _NATUREZAS else base_ids).add(value)
    return naturezas, base_ids


def _record_base_id(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    return str(record.get("base_id") or metadata.get("base_id") or "")


def _record_natureza(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    """Natureza do registro; sem o campo, vem do manifesto pelo ``base_id``."""
    natureza = record.get("natureza") or metadata.get("natureza")
    if natureza:
        return str(getattr(natureza, "value", natureza))
    declarada = _manifest_bases().get(_record_base_id(record, metadata), {}).get("natureza")
    return str(declarada) if declarada else None


def _record_escopo(record: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    escopo = _manifest_bases().get(_record_base_id(record, metadata), {}).get("escopo")
    return str(escopo) if escopo else None


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
            if key == "fornecedor" and operator == "contains":
                from src.analysis.suppliers import raizes_do_fornecedor, raiz_cnpj
                raizes = raizes_do_fornecedor(str(value))
                cnpj = _metadata_value(metadata, "fornecedor_cnpj")
                # O payload vetorial antigo só tem o nome. Resolva sua grafia
                # pelo catálogo; não exija regravar os 143 mil vetores antigos.
                raizes_atuais = (raiz_cnpj(cnpj),) if cnpj is not None else raizes_do_fornecedor(str(actual))
                if raizes and raizes_atuais:
                    if not set(raizes).intersection(raizes_atuais):
                        return False
                elif not _condicao_satisfeita(actual, operator, value):
                    return False
            elif key in {"orgao", "unidade_nome"} and operator == "contains" and isinstance(actual, str):
                # Nomes admitem qualificadores intermediários e abreviações do
                # PNCP: "Secretaria de Estado de Saúde" casa com "SECRET ESTADO
                # SAUDE". Exigir cada termo informativo no campo da entidade,
                # sem procurar no objeto ou fornecedor.
                conectores = {"de", "do", "da", "dos", "das", "e"}
                requeridos = set(_tokens(str(value))) - conectores
                disponiveis = set(_tokens(actual))
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


def _records_for(
    index_dir: Path,
    bases: Iterable[Any] | None,
    filters: Mapping[str, Any] | None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    selection = _split_selection(bases)
    allowed_scopes = set(escopos) if escopos is not None else None
    if records is None:
        paths = sorted(index_dir.glob("*/vectors.jsonl"))
        declarations = _manifest_bases()
        paths = [path for path in paths if not declarations.get(path.parent.name) or (
            (selection is None or path.parent.name in selection[1]
             or declarations[path.parent.name].get("natureza") in selection[0])
            and (allowed_scopes is None
                 or declarations[path.parent.name].get("escopo", "oficial") in allowed_scopes)
        )]
        raw = [record for path in paths for record in _registros_do_indice(path)]
    else:
        raw = [dict(record) for record in records]
    filtered: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for record in raw:
        metadata = _metadata_of(record)
        chunk_id = str(record.get("chunk_id") or metadata.get("chunk_id") or "")
        if not chunk_id or chunk_id in seen_ids or not _matches_filters(metadata, filters, content=str(record.get("text", ""))):
            continue
        # Texto ilegível (src/qualidade_corpus.py) não é evidência: já fica fora
        # do índice denso, e o BM25 lê o documents.jsonl, onde ele continua.
        if _metadata_value(metadata, "status_extracao") == "texto_ilegivel":
            continue
        if selection is not None:
            naturezas, base_ids = selection
            if (
                _record_natureza(record, metadata) not in naturezas
                and _record_base_id(record, metadata) not in base_ids
            ):
                continue
        if allowed_scopes is not None and _record_escopo(record, metadata) not in allowed_scopes:
            continue
        seen_ids.add(chunk_id)
        filtered.append(record)
    return filtered


def _metadata_of(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Metadados do registro: o índice guarda em ``metadata``; o
    ``documents.jsonl`` de ``prepare_documents.py`` guarda no próprio registro."""
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, Mapping) else record


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
        por_base.setdefault(base_id, []).append(dict(record))
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


def _result(record: Mapping[str, Any], score: float, content: str = "") -> RetrievalResult:
    metadata = dict(_metadata_of(record))
    metadata.pop("text", None)
    metadata.pop("embedding", None)
    return RetrievalResult(
        chunk_id=str(record.get("chunk_id") or metadata.get("chunk_id", "")),
        content=content or str(record.get("text", "")),
        metadata=metadata,
        score=score,
        document_id=record.get("document_id"),
        source_path=record.get("source_path"),
        _record=dict(record),
    )


def _identificadores_pncp(query: str) -> list[str]:
    """Números de controle do PNCP e CNPJs (só dígitos) citados na pergunta.

    O CNPJ entra pelo mesmo caminho: busca exata no texto e nos metadados, e
    o trecho que o contém sobe na fusão (marcado ``identificador_pncp_exato``).
    """
    cnpjs = ["".join(match.groups()) for match in _CNPJ_RE.finditer(query)]
    return list(dict.fromkeys(_PNCP_CONTROL_RE.findall(query) + cnpjs))


def _variantes(identificador: str) -> tuple[str, ...]:
    """Formas em que o identificador aparece no corpus.

    O JSON dos contratos traz o CNPJ só com dígitos ("niFornecedor"); o PDF das
    atas, com pontuação ("44.292.391/0001-30").
    """
    if len(identificador) == 14 and identificador.isdigit():
        d = identificador
        return d, f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return (identificador,)


def _identificador_em_resultado(identificador: str, item: RetrievalResult) -> bool:
    metadata = item.metadata
    extras = metadata.get("extras")
    extras_texto = json.dumps(extras, ensure_ascii=False, default=str) if isinstance(extras, Mapping) else ""
    return any(forma in item.content or forma in extras_texto for forma in _variantes(identificador))


def _diagnostico_identificadores(
    query: str, resultados: Sequence[RetrievalResult]
) -> list[dict[str, Any]]:
    diagnosticos = []
    for identificador in _identificadores_pncp(query):
        matches = [
            {
                "rank": rank,
                "chunk_id": item.chunk_id,
                "score": item.score,
                "dense_score": item.dense_score,
                "sparse_score": item.sparse_score,
                "rrf_score": item.rrf_score,
                "exact_lookup": bool((item.metadata.get("extras") or {}).get("identificador_pncp_exato")),
            }
            for rank, item in enumerate(resultados, start=1)
            if _identificador_em_resultado(identificador, item)
        ]
        diagnosticos.append({"identificador": identificador, "matches": matches})
    return diagnosticos


def _referencias_contrato(query: str) -> set[tuple[int, int]]:
    """Só número/ano explicitamente associados a 'contrato', nunca uma data solta."""
    return {(int(numero), int(ano)) for numero, ano in _CONTRACT_NUMBER_RE.findall(query)}


def _contrato_corresponde(record: Mapping[str, Any], referencias: set[tuple[int, int]]) -> bool:
    """Confere campos do contrato, não números de compra/ata citados em seu objeto."""
    if not referencias:
        return False
    metadata = _metadata_of(record)
    extras = metadata.get("extras") or {}
    texto = str(record.get("text", ""))
    try:
        payload = json.loads(texto) if texto.lstrip().startswith("{") else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    numero = payload.get("numeroContratoEmpenho") or extras.get("numero_contrato")
    if numero is None:
        return False
    match = re.fullmatch(r"\s*(\d+)\s*(?:/\s*(\d{4}))?\s*", str(numero))
    if not match:
        return False
    ano = match[2] or payload.get("anoContrato") or extras.get("ano_contrato")
    try:
        return (int(match[1]), int(ano)) in referencias
    except (ValueError, TypeError):
        return False


def _resultados_identificador_exato(
    query: str,
    records: Iterable[Mapping[str, Any]],
) -> list[RetrievalResult]:
    identificadores = [forma for identificador in _identificadores_pncp(query)
                       for forma in _variantes(identificador)]
    referencias_contrato = _referencias_contrato(query)
    if not identificadores and not referencias_contrato:
        return []
    cnpjs_consulta = {identificador for identificador in _identificadores_pncp(query)
                      if identificador.isdigit() and len(identificador) == 14}
    consulta_fornecedor = bool(cnpjs_consulta and re.search(r"\bfornecedor(?:a)?\b", query, re.IGNORECASE))
    termos_consulta = set(_tokens(query))
    resultados: list[RetrievalResult] = []
    vistos: set[str] = set()
    for record in records:
        metadata = _metadata_of(record)
        extras = metadata.get("extras")
        texto_busca = str(record.get("text", ""))
        if isinstance(extras, Mapping):
            texto_busca += json.dumps(extras, ensure_ascii=False, default=str)
        pncp_exato = any(identificador in texto_busca for identificador in identificadores)
        payload: Mapping[str, Any] = {}
        if consulta_fornecedor or referencias_contrato:
            try:
                parsed = json.loads(str(record.get("text", "")))
                payload = parsed if isinstance(parsed, Mapping) else {}
            except ValueError:
                pass
        if consulta_fornecedor and "niFornecedor" in payload:
            # O mesmo CNPJ no órgão/compra não torna o fornecedor correto.
            pncp_exato = re.sub(r"\D", "", str(payload["niFornecedor"])) in cnpjs_consulta
        contrato_exato = _contrato_corresponde(record, referencias_contrato)
        # Quando ambos foram explicitados, não promover um contrato de outro
        # número só porque compartilha o CNPJ (nem o inverso).
        if (identificadores and not pncp_exato) or (referencias_contrato and not contrato_exato):
            continue
        item = _result(record, 0.0, str(record.get("text", "")))
        if item.chunk_id in vistos:
            continue
        vistos.add(item.chunk_id)
        # Número/ano não é globalmente único: um nome de fornecedor citado
        # também na pergunta deve desambiguar ANTES do corte do rerank.
        nome_fornecedor = payload.get("nomeRazaoSocialFornecedor") or (
            extras.get("fornecedor") if isinstance(extras, Mapping) else ""
        )
        termos_fornecedor = set(_tokens(str(nome_fornecedor or ""))) - {
            "s", "a", "sa", "ltda", "me", "epp", "eireli", "de", "da", "do", "e",
        }
        coincidentes = sorted(termos_fornecedor & termos_consulta) if contrato_exato else []
        item.metadata = {
            **item.metadata,
            "extras": {
                **(item.metadata.get("extras") or {}),
                "identificador_pncp_exato": pncp_exato,
                "identificador_contrato_exato": contrato_exato,
                "fornecedor_termos_coincidentes": coincidentes,
            },
        }
        resultados.append(item)
    return sorted(resultados, key=lambda item: (
        -len(item.metadata["extras"]["fornecedor_termos_coincidentes"]), item.chunk_id
    ))


def _promover_identificadores_exatos(
    resultados: Sequence[RetrievalResult],
    identificadores_exatos: Sequence[RetrievalResult],
    limite: int,
) -> list[RetrievalResult]:
    por_id = {item.chunk_id: item for item in resultados}
    exatos = []
    for item in identificadores_exatos:
        existente = por_id.get(item.chunk_id)
        if existente is not None:
            existente.metadata = item.metadata
            exatos.append(existente)
        else:
            exatos.append(item)
    ids_exatos = {item.chunk_id for item in exatos}
    # Identidade tem precedência, mas a relevância entre os matches continua
    # sendo a da fusão/reranker, e não a ordem lexicográfica do hash do chunk.
    exatos_por_id = {item.chunk_id: item for item in exatos}
    ordenados = [exatos_por_id[item.chunk_id] for item in resultados if item.chunk_id in ids_exatos]
    presentes = {item.chunk_id for item in ordenados}
    ordenados += [item for item in exatos if item.chunk_id not in presentes]
    # Mais identificadores explícitos concordantes precedem matches só de
    # número/ano; empates mantêm a ordem de relevância já calculada.
    ordenados.sort(key=lambda item: -len(
        (item.metadata.get("extras") or {}).get("fornecedor_termos_coincidentes", [])
    ))
    ordenados += [item for item in resultados if item.chunk_id not in ids_exatos]
    return ordenados[:limite]


_NORMA_RE = re.compile(
    r"\b(lei(?: complementar)?|decreto|portaria|resolucao)\s*"
    r"(?:(?:federal|estadual|municipal)\s+)?(?:n(?:o|umero)?[.°º]?\s*)?(\d[\d.]*)"
)


def _normas_mencionadas(texto: str) -> set[tuple[str, str]]:
    return {(m.group(1), m.group(2).replace(".", ""))
            for m in _NORMA_RE.finditer(_sem_caixa_e_acento(texto))}


def _norma_do_documento(metadata: Mapping[str, Any]) -> set[tuple[str, str]]:
    # Só identidade declarada, nunca uma menção incidental no corpo de um
    # decreto, edital ou catálogo de links para legislação.
    extras = metadata.get("extras") or {}
    return _normas_mencionadas(str(extras.get("numero_norma") or extras.get("titulo_documento") or ""))


_VISAO_GERAL_NORMA = "âmbito aplicação princípios objetivos normas gerais"
_ARTIGO_INICIAL = re.compile(r"^\s*art\.?\s*(\d+)(?!\d|\.\d)", re.I)


def _ordenar_candidatos_norma(query: str, candidatos: Sequence[RetrievalResult]) -> list[RetrievalResult]:
    topico = _consulta_topica_norma(query)
    visao_geral = topico == _VISAO_GERAL_NORMA
    pede_fases = bool(re.search(r"\b(?:fases|etapas)\b", topico))
    artigo_pedido = re.search(r"\bart(?:igo)?\.?\s*(\d+)\b", _sem_caixa_e_acento(query))

    def prioridade(item: RetrievalResult):
        texto = _sem_caixa_e_acento(item.content)
        artigo = _ARTIGO_INICIAL.match(texto)
        numero = int(artigo.group(1)) if artigo else None
        if artigo_pedido:
            return (numero != int(artigo_pedido.group(1)),)
        if visao_geral:
            # Visão geral: disposições iniciais antes de alterações de outras
            # leis no fim do texto; a pontuação original desempata.
            return (numero is None, numero if numero is not None else 100000)
        definicao_fases = pede_fases and bool(re.search(r"\bseguintes (?:fases|etapas)\b", texto))
        return (not definicao_fases, not bool(re.search(r"\bart\.?\s*\d|§", texto)))

    ordenados = sorted(candidatos, key=prioridade)
    if visao_geral:
        diversos, repetidos, vistos = [], [], set()
        for item in ordenados:
            artigo = _ARTIGO_INICIAL.match(_sem_caixa_e_acento(item.content))
            chave = (tuple(sorted(_norma_do_documento(item.metadata))), artigo.group(1)) if artigo else None
            if chave is not None and chave in vistos:
                repetidos.append(item)
            else:
                diversos.append(item)
                vistos.add(chave)
        ordenados = diversos + repetidos
    return ordenados


def _consulta_topica_norma(query: str) -> str:
    texto = _sem_caixa_e_acento(query)
    texto = _NORMA_RE.sub(" ", texto)
    texto = re.sub(r"/\s*\d{4}\b", " ", texto)
    vazias = {"o", "a", "os", "as", "que", "qual", "quais", "estabelece", "diz", "sobre", "segundo",
              "conforme", "de", "do", "da", "dos", "das", "em", "no", "na", "e", "cite", "fontes",
              "corpus", "acervo", "resuma", "resumo", "explique", "ano"}
    termos = [t for t in _tokens(texto) if t not in vazias and not re.fullmatch(r"\d{4}", t)]
    # A identidade já delimitou os documentos: repetir número/ano no BM25
    # favorecia capas, cabeçalhos de impressão e catálogos, não os artigos.
    return " ".join(termos) or _VISAO_GERAL_NORMA


def _priorizar_norma_solicitada(
    query: str, resultados: Sequence[RetrievalResult],
    candidatos_norma: Sequence[RetrievalResult], limite: int,
) -> list[RetrievalResult]:
    pedidas = _normas_mencionadas(query)
    if not pedidas or not candidatos_norma:
        return list(resultados[:limite])
    por_id = {r.chunk_id: r for r in candidatos_norma}
    # Mantém o score do reranker quando ele recebeu o candidato.
    por_id.update({r.chunk_id: r for r in resultados if r.chunk_id in por_id})
    primarios = [por_id[r.chunk_id] for r in resultados if r.chunk_id in por_id]
    vistos = {r.chunk_id for r in primarios}
    primarios += [r for r in candidatos_norma if r.chunk_id not in vistos]
    # Uma fonte de cada norma explicitamente citada antes de repetir a mesma.
    primeiros, demais, cobertas = [], [], set()
    for item in primarios:
        identidades = _norma_do_documento(item.metadata) & pedidas
        if identidades - cobertas:
            primeiros.append(item)
            cobertas.update(identidades)
        else:
            demais.append(item)
    primarios = _ordenar_candidatos_norma(query, primeiros + demais)
    reserva = min(len(primarios), max(1, limite - 1), limite)
    escolhidos = primarios[:reserva]
    ids = {r.chunk_id for r in escolhidos}
    escolhidos += [r for r in resultados if r.chunk_id not in ids]
    return escolhidos[:limite]


def _registrar_evidencias(
    span: Any, resultados: Sequence[RetrievalResult], *,
    antes_do_top_k: Sequence[RetrievalResult] | None = None,
) -> None:
    from src.log_evidence import auditoria_completa, auditar_candidatos
    span.extra["evidencias"] = _evidencias(resultados)
    span.extra["evidencias_fora_do_log"] = max(0, len(resultados) - 10)
    if auditoria_completa():
        audit = auditar_candidatos(antes_do_top_k if antes_do_top_k is not None else resultados)
        if antes_do_top_k is not None:
            selecionados = {item.chunk_id for item in resultados}
            for item in audit["candidatos"]:
                item["selecionado_apos_top_k"] = item["chunk_id"] in selecionados
        span.extra["auditoria_evidencias"] = audit


def _evidencias(resultados: Sequence[RetrievalResult], limite: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "rank": rank,
            "chunk_id": item.chunk_id,
            "base_id": item.metadata.get("base_id"),
            "source_file": item.metadata.get("source_file"),
            "score": item.score,
            "dense_score": item.dense_score,
            "sparse_score": item.sparse_score,
            "rrf_score": item.rrf_score,
            "rerank_score": item.rerank_score,
            "identificador_contrato_exato": bool(
                (item.metadata.get("extras") or {}).get("identificador_contrato_exato")
            ),
            "identificador_pncp_exato": bool(
                (item.metadata.get("extras") or {}).get("identificador_pncp_exato")
            ),
        }
        for rank, item in enumerate(resultados[:limite], start=1)
    ]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left or not right:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _default_embed(query: str) -> list[float]:
    return _embed_fn_com_uso([])([query])[0]


def _embed_fn_com_uso(uso: list[TokenUsage], estatisticas: dict[str, int] | None = None,
                       observacoes: list[dict] | None = None) -> EmbedFn:
    """Embedding padrão (servidor da turma) que anota o ``usage`` de cada chamada.

    Usado por ``retrieve`` para preencher ``tokens`` do stage
    ``retrieval_denso``; quem passa ``embed_fn`` próprio fica sem contagem.
    """
    def embed(texts: list[str]) -> list[list[float]]:
        encoded = [embedding_query_text(text) for text in texts]
        endpoint, chave_api, modelo = config.embedding_endpoint()
        chave = {"endpoint": endpoint, "model": modelo, "texts": encoded}
        for record in _read_jsonl(QUERY_CACHE_PATH):
            if record.get("key") == chave:
                vectors = record["vectors"]
                if len(vectors) == len(texts) and all(
                    vector and all(math.isfinite(v) for v in vector) and any(vector)
                    for vector in vectors
                ):
                    uso.append(TokenUsage())
                    if estatisticas is not None:
                        estatisticas["cache_hits"] += 1
                    return vectors
        # O cache continua disponível durante a indisponibilidade. Nunca usar
        # outro encoder automaticamente: mesma dimensão não garante compatibilidade.
        endpoint_key = (endpoint, modelo)
        if time.monotonic() < _EMBEDDING_FAILURES.get(endpoint_key, 0):
            raise EmbeddingUnavailable("Embedding em cooldown após falha de disponibilidade")
        # Cliente criado só na chamada: sem candidatos, dense_retrieve nem embeda.
        from openai import OpenAI
        import httpx
        from src.obs import aguardar_modelo

        aguardar_modelo()
        client = OpenAI(base_url=endpoint, api_key=chave_api,
                        timeout=httpx.Timeout(config.QUERY_EMBEDDING_TIMEOUT,
                                              connect=min(3.0, config.QUERY_EMBEDDING_TIMEOUT)),
                        max_retries=0)
        from src.model_observation import observar_chamada
        diagnostics: dict = {}
        if estatisticas is not None:
            estatisticas["calls"] += 1
        try:
            response = observar_chamada(lambda: client.embeddings.create(model=modelo, input=encoded),
                provider=config.EMBEDDING_PROVIDER, model=modelo, sdk_max_retries=0, diagnosticos=diagnostics)
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if erro_de_disponibilidade(error):
                _EMBEDDING_FAILURES[endpoint_key] = time.monotonic() + config.QUERY_EMBEDDING_COOLDOWN
            raise
        else:
            _EMBEDDING_FAILURES.pop(endpoint_key, None)
        finally:
            if observacoes is not None:
                observacoes.extend(diagnostics.get("chamadas_modelo", []))
        uso.append(token_usage_from_response(response))
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: getattr(item, "index", 0))]
        if len(vectors) != len(texts) or not all(
            vector and all(math.isfinite(v) for v in vector) and any(vector) for vector in vectors
        ):
            raise ValueError(f"{config.EMBEDDING_PROVIDER} retornou embeddings de query inválidos")
        QUERY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with QUERY_CACHE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": chave, "vectors": vectors}, ensure_ascii=False) + "\n")
        return vectors

    return embed


def pre_carregar_indices(
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
) -> int:
    """Lê os índices antes da primeira pergunta; devolve quantos arquivos leu.

    Falha só vira aviso: a primeira pergunta lê pelo caminho normal.
    """
    lidos = 0
    try:
        for vectors_path in sorted(index_dir.glob("*/vectors.jsonl")):
            _registros_do_indice(vectors_path)
            lidos += 1
            faiss_path = vectors_path.parent / "vectors.faiss"
            if faiss_path.exists():
                _ler_indice_faiss(faiss_path)
                lidos += 1
        for path in (documents_file, QUERY_CACHE_PATH):
            if path.exists():
                _read_jsonl(path)
                lidos += 1
        # Tokeniza os chunks para o BM25 agora, e não na primeira pergunta.
        for record in _read_jsonl(documents_file):
            _termos(str(record.get("text", "")))
    except Exception as erro:
        import logging

        logging.getLogger(__name__).warning(
            "Pré-carregamento dos índices falhou (%s); a primeira pergunta lê do disco", type(erro).__name__)
    return lidos


_FAISS_CACHE: dict[str, tuple[int, int, Any]] = {}


def _ler_indice_faiss(index_path: Path) -> Any:
    """Índice persistido, lido do disco só quando o arquivo muda."""
    import faiss

    stat = index_path.stat()
    chave = str(index_path)
    guardado = _FAISS_CACHE.get(chave)
    if guardado and guardado[0] == stat.st_mtime_ns and guardado[1] == stat.st_size:
        return guardado[2]
    index = faiss.read_index(chave)
    _FAISS_CACHE[chave] = (stat.st_mtime_ns, stat.st_size, index)
    return index


def _faiss_scores(
    query_vector: Sequence[float],
    candidates: list[dict[str, Any]],
    indexed_records: list[dict[str, Any]],
    *,
    index_path: Path | None,
    top_k: int,
) -> dict[str, float]:
    """Busca candidatos no índice persistido ou monta um índice FAISS em memória."""
    import faiss
    import numpy as np

    dimension = len(query_vector)
    if not dimension:
        return {str(record.get("chunk_id", "")): 0.0 for record in candidates}

    candidate_ids = {str(record.get("chunk_id", "")) for record in candidates}
    if index_path is not None and index_path.exists():
        index = _ler_indice_faiss(index_path)
        if index.ntotal != len(indexed_records):
            raise RuntimeError(
                f"índice FAISS desatualizado em {index_path}; regenere com src.index"
            )
        if index.d != dimension:
            return {chunk_id: 0.0 for chunk_id in candidate_ids}
    else:
        vectors = np.zeros((len(indexed_records), dimension), dtype=np.float32)
        for row, record in enumerate(indexed_records):
            embedding = record.get("embedding", [])
            if isinstance(embedding, list) and len(embedding) == dimension:
                vectors[row] = embedding
        vectors = np.ascontiguousarray(vectors)
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(dimension)
        index.add(vectors)

    query = np.ascontiguousarray([query_vector], dtype=np.float32)
    faiss.normalize_L2(query)
    target = min(top_k, len(candidate_ids))
    search_k = min(index.ntotal, max(1, target))
    scores_by_id: dict[str, float] = {}
    while index.ntotal:
        scores, positions = index.search(query, search_k)
        scores_by_id = {}
        for score, position in zip(scores[0], positions[0]):
            if position < 0:
                continue
            chunk_id = str(indexed_records[int(position)].get("chunk_id", ""))
            if chunk_id in candidate_ids:
                scores_by_id[chunk_id] = float(score)
        if len(scores_by_id) >= target or search_k >= index.ntotal:
            break
        search_k = min(index.ntotal, max(search_k + 1, search_k * 2))
    return scores_by_id


def dense_retrieve(
    query: str,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    embed_fn: EmbedFn | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Recupera chunks próximos pelo FAISS e aplica filtros de metadados."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = _records_for(index_dir, bases, filters, records, escopos)
    if not candidates:
        return []

    query_vector = (embed_fn or (lambda texts: [_default_embed(texts[0])]))([query])[0]
    indexed_by_base, index_paths = _indices_das_bases(index_dir, _por_base(candidates), records)
    bases_com_faiss = {base_id for base_id, caminho in index_paths.items() if caminho.exists()}
    normalized_text = {
        str(item.get("chunk_id")): str(item.get("text", ""))
        for item in _read_jsonl(documents_file)
    }
    compatible_candidates_by_base, incompatible_embeddings, missing_embeddings = _candidatos_com_vetor(
        candidates, bases_com_faiss, len(query_vector))
    scored: list[RetrievalResult] = []
    for base_id, group in compatible_candidates_by_base.items():
        faiss_path = index_paths.get(base_id)
        scores = _faiss_scores(
            query_vector,
            group,
            indexed_by_base.get(base_id, group),
            index_path=faiss_path if faiss_path and faiss_path.exists() else None,
            top_k=top_k,
        )
        for record in group:
            chunk_id = str(record.get("chunk_id", ""))
            if chunk_id not in scores:
                continue
            content = str(record.get("text", "")) or normalized_text.get(chunk_id, "")
            scored.append(_result(record, scores[chunk_id], content))
    _avisar_vetores_descartados(query, incompatible_embeddings, missing_embeddings,
                                run_id=run_id, query_id=query_id)
    return sorted(scored, key=lambda item: (-item.score, item.chunk_id))[:top_k]


def _base_do(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    return _record_base_id(record, metadata if isinstance(metadata, Mapping) else {})


def _por_base(candidates: Iterable[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    por_base: dict[str, list[Mapping[str, Any]]] = {}
    for record in candidates:
        por_base.setdefault(_base_do(record), []).append(record)
    return por_base


def _indices_das_bases(index_dir: Path, candidates_by_base: dict[str, list[Mapping[str, Any]]],
                       records: Iterable[Mapping[str, Any]] | None) -> tuple[dict[str, list], dict[str, Path]]:
    """Registros do índice e caminho do FAISS de cada base com candidatos.

    Com ``records`` dados por quem chama, o próprio grupo de candidatos é o índice.
    """
    if records is not None:
        return dict(candidates_by_base), {}
    indexed_by_base: dict[str, list[dict[str, Any]]] = {}
    index_paths: dict[str, Path] = {}
    # Só as bases que têm candidatos: com a v7, ler todas custava 3,4 GB
    # de JSON (2,5 GB só dos contratos) para uma pergunta sobre normas.
    for vectors_path in sorted(index_dir.glob("*/vectors.jsonl")):
        base_id = vectors_path.parent.name
        if base_id not in candidates_by_base:
            continue
        indexed_by_base[base_id] = _registros_do_indice(vectors_path)
        index_paths[base_id] = vectors_path.parent / "vectors.faiss"
    return indexed_by_base, index_paths


def _candidatos_com_vetor(candidates: Iterable[Mapping[str, Any]], bases_com_faiss: set[str],
                          dimensao: int) -> tuple[dict[str, list], list[dict[str, Any]], list[dict[str, str]]]:
    """Candidatos que podem ser comparados com o vetor da pergunta, por base.

    Devolve também os descartados: vetor com outra dimensão e vetor ausente.
    """
    compativeis: dict[str, list[Mapping[str, Any]]] = {}
    incompativeis: list[dict[str, Any]] = []
    ausentes: list[dict[str, str]] = []
    for record in candidates:
        embedding = record.get("embedding", [])
        metadata = record.get("metadata")
        chunk_id = str(record.get("chunk_id") or (
            metadata.get("chunk_id", "") if isinstance(metadata, Mapping) else ""
        ))
        base_do_registro = _base_do(record)
        if "embedding" not in record and base_do_registro in bases_com_faiss:
            # Lido sem o vetor (_registros_do_indice): o FAISS tem o vetor e
            # confere a dimensão em _faiss_scores.
            compativeis.setdefault(base_do_registro, []).append(record)
            continue
        if embedding is None:
            ausentes.append({"chunk_id": chunk_id})
            continue
        try:
            embedding_dimension = len(embedding)
        except TypeError:
            embedding_dimension = None
        if embedding_dimension != dimensao:
            incompativeis.append({
                "chunk_id": chunk_id,
                "expected_dimension": dimensao,
                "actual_dimension": embedding_dimension,
            })
            continue
        compativeis.setdefault(base_do_registro, []).append(record)
    return compativeis, incompativeis, ausentes


def _avisar_vetores_descartados(query: str, incompativeis: list[dict[str, Any]], ausentes: list[dict[str, str]],
                                *, run_id: str, query_id: str) -> None:
    """Aviso no log (stage ``retrieval_denso``) para cada tipo de vetor descartado."""
    if incompativeis:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(incompativeis)} embedding(s) incompatível(is) excluído(s)",
            extra={"embedding_dimension_mismatches": incompativeis},
        )
    if ausentes:
        registrar(
            run_id=run_id,
            query_id=query_id,
            stage=Stage.RETRIEVAL_DENSO,
            status="aviso",
            latencia_ms=0,
            input=query,
            output=f"{len(ausentes)} embedding(s) ausente(s)/nulo(s) ignorado(s)",
            extra={"missing_embeddings": ausentes},
        )


def _tokens(text: str) -> list[str]:
    """Tokens em minúsculas e sem acento: "médico" casa com "MEDICO" do catálogo."""
    sem_acento = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    return [token.lower() for token in _TOKEN_RE.findall(sem_acento)]


# Texto do chunk -> (contagem dos termos, número de termos). O BM25 tokenizava
# todos os candidatos a cada pergunta: com os 106.667 contratos da v7, ~2 min.
# A chave é o próprio texto, que vem dos registros já guardados em cache.
_TERMOS_CACHE: dict[str, tuple[Counter[str], int]] = {}


def _termos(text: str) -> tuple[Counter[str], int]:
    guardado = _TERMOS_CACHE.get(text)
    if guardado is None:
        tokens = _tokens(text)
        guardado = (Counter(tokens), len(tokens))
        _TERMOS_CACHE[text] = guardado
    return guardado


def bm25_retrieve(
    query: str,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    bases: Iterable[Any] | None = None,
    filters: Mapping[str, Any] | None = None,
    records: Iterable[Mapping[str, Any]] | None = None,
    escopos: Iterable[str] | None = None,
) -> list[RetrievalResult]:
    """Recupera chunks lexicalmente com BM25, sem dependências adicionais."""
    if top_k <= 0 or not query.strip():
        return []
    candidates = (
        _records_for(index_dir, bases, filters, records, escopos)
        if records is not None
        else _records_for(index_dir, bases, filters, _read_jsonl(documents_file), escopos)
    )
    documents = [(record, str(record.get("text", ""))) for record in candidates]
    query_terms = _tokens(query)
    if not query_terms or not documents:
        return []
    tokenized = [(record, text, *_termos(text)) for record, text in documents]
    average_length = sum(length for _, _, _, length in tokenized) / len(tokenized)
    # Só os termos da pergunta entram na conta: a frequência é a mesma que a
    # contagem sobre todos os termos daria para eles.
    document_frequency = {term: sum(1 for _, _, counts, _ in tokenized if term in counts)
                          for term in set(query_terms)}
    scored: list[tuple[float, str, Mapping[str, Any], str]] = []
    for record, text, counts, length in tokenized:
        score = 0.0
        for term in query_terms:
            if term not in counts:
                continue
            idf = math.log(1 + (len(tokenized) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = counts[term] + 1.5 * (1 - 0.75 + 0.75 * length / (average_length or 1))
            score += idf * counts[term] * 2.5 / denominator
        if score > 0:
            chunk_id = str(record.get("chunk_id") or _metadata_of(record).get("chunk_id", ""))
            scored.append((score, chunk_id, record, text))
    # Só os top_k viram RetrievalResult: com a v7, quase todos os 106 mil
    # contratos pontuam e montar o resultado de cada um custava ~3 s.
    melhores = heapq.nsmallest(top_k, scored, key=lambda item: (-item[0], item[1]))
    return [_result(record, score, text) for score, _, record, text in melhores]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[RetrievalResult]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """Combina rankings por identificador estável usando Reciprocal Rank Fusion.

    A conta é do domínio (src/domain/fusion.py); aqui só se monta o resultado.
    """
    scores = fusion.pontuacoes_rrf([[item.chunk_id for item in ranked] for ranked in ranked_lists], k=k)
    merged: dict[str, RetrievalResult] = {}
    dense_scores: dict[str, float] = {}
    sparse_scores: dict[str, float] = {}
    for list_number, ranked in enumerate(ranked_lists):
        for item in ranked:
            key = item.chunk_id
            if list_number == 0:
                dense_scores[key] = item.score
            else:
                sparse_scores[key] = item.score
            if key not in merged:
                merged[key] = item
    result: list[RetrievalResult] = []
    for key, item in merged.items():
        value = scores[key]
        result.append(
            RetrievalResult(
                **{field: getattr(item, field) for field in (
                    "chunk_id", "content", "metadata",
                    "document_id", "source_path", "_record"
                )},
                score=value,
                rrf_score=value,
                dense_score=dense_scores.get(key),
                sparse_score=sparse_scores.get(key),
            )
        )
    result.sort(key=lambda item: (-item.score, item.chunk_id))
    return result[:top_k] if top_k is not None else result


def retrieve(
    query: str,
    route: Any = None,
    *,
    index_dir: Path = config.INDEX_DIR,
    documents_file: Path = config.NORMALIZED_DATA_DIR / "documents.jsonl",
    top_k: int = DEFAULT_TOP_K,
    filters: Mapping[str, Any] | None = None,
    embed_fn: EmbedFn | None = None,
    reranker: RerankFn | None = None,
    escopos: Iterable[str] | None = None,
    fetch_k: int | None = None,
    rerank_k: int | None = None,
    run_id: str = "sem-run-id",
    query_id: str = "sem-query-id",
) -> list[RetrievalResult]:
    """Executa dense + BM25 + RRF e aplica reranker somente se fornecido.

    Com ``reranker``, o RRF entrega ``rerank_k`` candidatos (padrão
    ``max(top_k, config.RERANK_CANDIDATES)``) e o reranker escolhe os ``top_k``
    finais entre eles — assim ele pode trazer para cima um chunk que o RRF
    deixou abaixo do corte. Se o reranker falhar (ex.: modelo indisponível), o
    stage ``rerank`` é registrado com ``status: "erro"`` e o resultado volta a
    ser o do RRF.

    ``escopos`` restringe a busca às bases com esse ``escopo`` no manifesto
    (ex.: ``("oficial",)``); ``None`` não filtra por escopo.

    ``fetch_k`` é quantos candidatos a busca densa e a BM25 devolvem, cada uma,
    antes da fusão; o RRF escolhe os ``top_k`` finais entre eles. Com
    ``fetch_k == top_k``, um chunk que as duas buscas põem logo abaixo do corte
    nunca chega à fusão. Padrão: ``max(5 * top_k, DEFAULT_FETCH_K)``.

    Os filtros são aplicados base a base (``_filtrar_por_base``). Somente os
    explicitamente opcionais podem ser ignorados quando não há campo ou
    correspondência; os obrigatórios permanecem. O log registra relaxamentos em
    ``extra.filtros_ignorados``. ``dense_retrieve`` e ``bm25_retrieve``, chamados
    diretamente, continuam aplicando os filtros de forma estrita.

    Cada etapa registra seu stage (``retrieval_denso``, ``retrieval_bm25``,
    ``fusao_rrf``, ``rerank``) em ``logs/pipeline/pipeline-AAAA-MM-DD.jsonl``.
    """
    bases, route_filters = _bases_e_filtros_da_rota(query, route, filters)
    fetch_k = fetch_k if fetch_k is not None else max(5 * top_k, DEFAULT_FETCH_K)
    dense_records, sparse_records, filtros_ignorados = _registros_elegiveis(
        index_dir, documents_file, bases, escopos, route_filters)
    extra = {
        "bases": sorted(str(getattr(b, "value", b)) for b in ([bases] if isinstance(bases, str) else bases or [])),
        "escopos": sorted(escopos) if escopos is not None else None,
        "filtros": dict(route_filters or {}),
        "k": top_k,
        "fetch_k": fetch_k,
    }
    if filtros_ignorados:
        extra["filtros_ignorados"] = filtros_ignorados
    r = _Recuperacao(query=query, index_dir=index_dir, documents_file=documents_file, bases=bases,
                     escopos=escopos, route_filters=route_filters, top_k=top_k, fetch_k=fetch_k,
                     run_id=run_id, query_id=query_id, extra=extra,
                     dense_records=dense_records, sparse_records=sparse_records)

    dense, recuperacao_degradada = _busca_densa(r, embed_fn)
    sparse, candidatos_norma = _busca_bm25(r)
    identificadores_exatos = _identificadores_exatos(r)
    if reranker is not None:
        rerank_k = max(top_k, rerank_k if rerank_k is not None else config.RERANK_CANDIDATES)
    corte = rerank_k if reranker is not None else top_k
    fused = _fundir(r, dense, sparse, candidatos_norma, identificadores_exatos, corte,
                    recuperacao_degradada=recuperacao_degradada, filtros_ignorados=filtros_ignorados)
    if reranker is None or not fused:
        from src.obs import registrar_pulo
        registrar_pulo(run_id=run_id, query_id=query_id, stage=Stage.RERANK, query=query,
                       motivo="desabilitado" if reranker is None else "sem_candidatos")
        return fused
    return _reordenar(r, reranker, fused, candidatos_norma, identificadores_exatos, rerank_k)


@dataclass
class _Recuperacao:
    """O que as etapas de uma busca compartilham: a pergunta, o recorte e os ids do log."""
    query: str
    index_dir: Path
    documents_file: Path
    bases: Any
    escopos: Iterable[str] | None
    route_filters: Mapping[str, Any] | None
    top_k: int
    fetch_k: int
    run_id: str
    query_id: str
    extra: dict[str, Any]
    dense_records: list[dict[str, Any]] | None
    sparse_records: list[dict[str, Any]] | None

    def span(self, stage: Stage, **mais: Any):
        return default_logger.span(run_id=self.run_id, query_id=self.query_id, stage=stage,
                                   input=self.query, extra={**self.extra, **mais})

    def registros_bm25(self) -> list[dict[str, Any]]:
        """Registros que a BM25 pode ver; sem filtro, todos os das bases escolhidas."""
        if self.sparse_records is not None:
            return self.sparse_records
        return _records_for(self.index_dir, self.bases, None, _read_jsonl(self.documents_file), self.escopos)


def _bases_e_filtros_da_rota(query: str, route: Any, filters: Mapping[str, Any] | None) -> tuple[Any, Any]:
    """Bases e filtros da rota (dict ou objeto), mais os critérios de "bem completo"."""
    bases = None
    route_filters = filters
    if route is not None:
        if isinstance(route, Mapping):
            bases = route.get("bases_selecionadas", route.get("bases"))
            route_filters = route.get("filters", route.get("filtros", route_filters))
        else:
            bases = getattr(route, "bases_selecionadas", getattr(route, "bases", None))
            route_filters = getattr(route, "filters", getattr(route, "filtros", route_filters))
    from src.retrieval_constraints import solicita_bem_completo, tema_bem_completo
    if solicita_bem_completo(query):
        # Critério de admissibilidade antes de dense/BM25, lookup e rerank.
        # Não pode ser relaxado para devolver peças/serviços como aparelho.
        route_filters = {**(route_filters or {}), "objeto_auxiliar": {
            "operator": "==", "value": False, "optional": False,
        }}
        tema = tema_bem_completo(query)
        if tema:
            route_filters["objeto_termo"] = {"operator": "contains", "value": tema, "optional": False}
    return bases, route_filters


def _registros_elegiveis(index_dir: Path, documents_file: Path, bases: Any, escopos: Iterable[str] | None,
                         route_filters: Mapping[str, Any] | None) -> tuple[Any, Any, dict[str, dict[str, list[str]]]]:
    """Registros que passam nos filtros, para a busca densa e para a BM25.

    Sem filtros, ``None``: cada busca lê as bases por conta própria.
    """
    if not route_filters:
        return None, None, {}
    # Seleção de bases e escopos antes, para decidir o filtro só entre as
    # bases que o roteador escolheu.
    sparse_records, ignorados_bm25 = _filtrar_por_base(
        _records_for(index_dir, bases, None, _read_jsonl(documents_file), escopos), route_filters
    )
    # O payload vetorial não repete o texto JSON com o CNPJ do fornecedor.
    # Os IDs dos documentos filtrados delimitam também os candidatos densos.
    dense_filters = {key: value for key, value in route_filters.items() if key != "cnpj"}
    dense_records, ignorados_denso = _filtrar_por_base(
        _records_for(index_dir, bases, None, None, escopos), dense_filters
    )
    if "cnpj" in route_filters:
        eligible = {str(record.get("chunk_id")) for record in sparse_records}
        dense_records = [record for record in dense_records if str(record.get("chunk_id")) in eligible]
    return dense_records, sparse_records, {**ignorados_bm25, **ignorados_denso}


def _busca_densa(r: _Recuperacao, embed_fn: EmbedFn | None) -> tuple[list[RetrievalResult], bool]:
    """Stage ``retrieval_denso``; sem embedding disponível, vazia e degradada (segue a BM25)."""
    uso_embedding: list[TokenUsage] = []
    estatisticas_embedding = {"calls": 0, "cache_hits": 0}
    observacoes_embedding: list[dict] = []
    embed_original = embed_fn or _embed_fn_com_uso(uso_embedding, estatisticas_embedding, observacoes_embedding)

    def embed_da_consulta(texts):
        if embed_fn is not None:
            estatisticas_embedding["calls"] += 1
        return embed_original(texts)

    recuperacao_degradada = False
    with r.span(Stage.RETRIEVAL_DENSO) as span:
        try:
            if not config.DENSE_RETRIEVAL_ENABLED:
                raise EmbeddingUnavailable("Busca densa desabilitada por configuração; usando BM25")
            dense = dense_retrieve(
                r.query,
                index_dir=r.index_dir,
                documents_file=r.documents_file,
                top_k=r.fetch_k,
                bases=r.bases,
                filters=None if r.dense_records is not None else r.route_filters,
                embed_fn=embed_da_consulta,
                records=r.dense_records,
                escopos=r.escopos,
                run_id=r.run_id,
                query_id=r.query_id,
            )
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if not erro_de_disponibilidade(error):
                raise
            dense = []
            recuperacao_degradada = True
            span.status = "degradado"
            span.extra.update({"degraded": True, "fallback": "bm25", "error_type": type(error).__name__,
                               "status_code": getattr(error, "status_code", None),
                               "embedding_cooldown": isinstance(error, EmbeddingUnavailable),
                               "dense_enabled": config.DENSE_RETRIEVAL_ENABLED})
        span.set_result(_resumo(dense))
        _registrar_evidencias(span, dense)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, dense)
        span.extra.update({"query_embedding": estatisticas_embedding})
        if observacoes_embedding:
            from src.model_observation import resumir_uso
            span.extra["chamadas_modelo"] = observacoes_embedding
            span.extra["uso_tokens"] = resumir_uso(observacoes_embedding)[1]
            span.modelo = observacoes_embedding[-1]["model"]
        elif estatisticas_embedding["cache_hits"]:
            span.extra["uso_tokens"] = {"status": "cache"}
        elif embed_fn is not None and estatisticas_embedding["calls"]:
            span.extra["uso_tokens"] = {"status": "indisponivel", "fonte": "funcao_injetada_sem_usage"}
        if uso_embedding:
            span.modelo = span.modelo or config.EMBEDDING_MODEL
            span.tokens = TokenUsage(
                prompt=sum(u.prompt for u in uso_embedding),
                completion=sum(u.completion for u in uso_embedding),
                total=sum(u.total for u in uso_embedding),
            )
    return dense, recuperacao_degradada


def _busca_bm25(r: _Recuperacao) -> tuple[list[RetrievalResult], list[RetrievalResult]]:
    """Stage ``retrieval_bm25``, com a norma citada na pergunta priorizada.

    Devolve o ranking e os trechos da norma pedida, que a fusão e o rerank
    também priorizam.
    """
    with r.span(Stage.RETRIEVAL_BM25) as span:
        sparse = bm25_retrieve(
            r.query,
            index_dir=r.index_dir,
            documents_file=r.documents_file,
            top_k=r.fetch_k,
            bases=r.bases,
            filters=None if r.sparse_records is not None else r.route_filters,
            records=r.sparse_records,
            escopos=r.escopos,
        )
        candidatos_norma: list[RetrievalResult] = []
        pedidas = _normas_mencionadas(r.query)
        if pedidas:
            elegiveis = r.sparse_records if r.sparse_records is not None else _records_for(
                r.index_dir, r.bases, r.route_filters, _read_jsonl(r.documents_file), r.escopos,
            )
            primarios = [registro for registro in elegiveis
                         if _record_natureza(registro, _metadata_of(registro)) == Natureza.NORMATIVA.value
                         and _norma_do_documento(_metadata_of(registro)) & pedidas]
            if primarios:
                candidatos_norma = _trechos_da_norma_pedida(r, primarios)
                sparse = _priorizar_norma_solicitada(r.query, sparse, candidatos_norma, r.fetch_k)
                span.extra["normas_primarias_priorizadas"] = [c.chunk_id for c in candidatos_norma[:r.top_k]]
        span.set_result(_resumo(sparse))
        _registrar_evidencias(span, sparse)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, sparse)
    return sparse, candidatos_norma


def _trechos_da_norma_pedida(r: _Recuperacao, primarios: list[dict[str, Any]]) -> list[RetrievalResult]:
    """Trechos da norma citada, conteúdo articulado antes de capas, sem texto repetido."""
    candidatos = bm25_retrieve(
        _consulta_topica_norma(r.query), records=primarios, top_k=r.fetch_k,
        index_dir=r.index_dir, documents_file=r.documents_file,
    )
    # Se há conteúdo articulado, capas/cabeçalhos ficam depois.
    candidatos = _ordenar_candidatos_norma(r.query, candidatos)
    vistos_textos: set[str] = set()
    distintos = []
    for item in candidatos:
        assinatura = " ".join(item.content.split()).casefold()
        if assinatura not in vistos_textos:
            distintos.append(item)
            vistos_textos.add(assinatura)
    return distintos


def _identificadores_exatos(r: _Recuperacao) -> list[RetrievalResult]:
    """Registros com o número do PNCP ou do contrato citado na pergunta."""
    if not (_identificadores_pncp(r.query) or _referencias_contrato(r.query)):
        return []
    return _resultados_identificador_exato(r.query, r.registros_bm25())


def _fundir(r: _Recuperacao, dense: list[RetrievalResult], sparse: list[RetrievalResult],
            candidatos_norma: list[RetrievalResult], identificadores_exatos: list[RetrievalResult],
            corte: int, *, recuperacao_degradada: bool,
            filtros_ignorados: dict[str, dict[str, list[str]]]) -> list[RetrievalResult]:
    """Stage ``fusao_rrf``: RRF, norma pedida e identificadores exatos à frente."""
    with r.span(Stage.FUSAO_RRF, rrf_k=DEFAULT_RRF_K) as span:
        fused = reciprocal_rank_fusion([dense, sparse], top_k=corte)
        fused = _priorizar_norma_solicitada(r.query, fused, candidatos_norma, corte)
        fused = _promover_identificadores_exatos(fused, identificadores_exatos, corte)
        for item in fused:
            if recuperacao_degradada:
                item.metadata = {**item.metadata, "extras": {
                    **item.metadata.get("extras", {}), "recuperacao_degradada": "bm25_sem_embedding",
                }}
            ignored = filtros_ignorados.get(str(item.metadata.get("base_id")), {})
            if ignored:
                item.metadata = {**item.metadata, "extras": {
                    **item.metadata.get("extras", {}), "filtros_relaxados": ignored,
                }}
        span.set_result(_resumo(fused))
        _registrar_evidencias(span, fused)
        span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, fused)
        span.extra["identificadores_exactos_adicionados"] = [
            item.chunk_id for item in identificadores_exatos
        ]
    return fused


def _reordenar(r: _Recuperacao, reranker: RerankFn, fused: list[RetrievalResult],
               candidatos_norma: list[RetrievalResult], identificadores_exatos: list[RetrievalResult],
               rerank_k: int) -> list[RetrievalResult]:
    """Stage ``rerank``; se o reranker falhar, o span registra o erro e valem os do RRF."""
    try:
        with r.span(Stage.RERANK, rerank_k=rerank_k) as span:
            span.modelo = getattr(reranker, "modelo", None)
            span.extra["candidatos"] = len(fused)
            candidates = {item.chunk_id: item for item in fused}
            span.extra["fallback_on_error"] = "fusao_rrf"
            ranked = list(reranker(r.query, fused))
            if getattr(reranker, "backend_usado", None):
                span.extra["rerank_backend"] = reranker.backend_usado
                if "ecoa falhou" in reranker.backend_usado:
                    span.status = "degradado"
                    span.extra["fallback"] = "reranker_local"
            if {item.chunk_id for item in ranked} - candidates.keys():
                raise ValueError("Reranker devolveu evidência fora dos candidatos")
            reranked = []
            seen = set()
            for item in ranked:
                if item.chunk_id not in seen:
                    original = candidates[item.chunk_id]
                    reranked.append(replace(original,
                        score=getattr(item, "score", original.score),
                        rerank_score=getattr(item, "rerank_score", original.rerank_score)))
                    seen.add(item.chunk_id)
            if not reranked:
                raise ValueError("Reranker vazio")
            antes_do_top_k = reranked
            reranked = _priorizar_norma_solicitada(r.query, reranked, candidatos_norma, r.top_k)
            reranked = _promover_identificadores_exatos(reranked, identificadores_exatos, r.top_k)
            span.set_result(_resumo(reranked))
            _registrar_evidencias(span, reranked, antes_do_top_k=antes_do_top_k)
            span.extra["diagnostico_identificadores"] = _diagnostico_identificadores(r.query, reranked)
        return reranked
    except Exception:  # o span registra o erro; segue com os candidatos originais
        return fused[:r.top_k]


def _resumo(resultados: Sequence[Any]) -> str:
    """Resumo curto para o campo ``output`` do log (ex.: "12 chunks; top base: x")."""
    if not resultados:
        return "0 chunks"
    primeiro = resultados[0]
    metadata = getattr(primeiro, "metadata", None) or {}
    base = metadata.get("base_id") or getattr(primeiro, "_record", {}).get("base_id")
    return f"{len(resultados)} chunks; top base: {base}" if base else f"{len(resultados)} chunks"


__all__ = [
    "RetrievalResult",
    "bm25_retrieve",
    "dense_retrieve",
    "reciprocal_rank_fusion",
    "retrieve",
]
