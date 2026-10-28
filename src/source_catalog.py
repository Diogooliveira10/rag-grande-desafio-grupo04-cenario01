r"""Curadoria e consultas auxiliares das fontes do corpus.

A interface pública usa subcomandos: ``discover``, ``lookup``, ``propose``,
``review``, ``manifest-diff`` e ``apply``. A coleta oficial pertence ao
``fetch_corpus``; este módulo não deve gravar arquivos em ``data/raw``.
"""

from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from .artifact_downloader import ArtifactDownloader
    from .pncp_client import PncpClient
except ImportError:  # permite executar o módulo diretamente
    from artifact_downloader import ArtifactDownloader
    from pncp_client import PncpClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG_OUTPUT_DIR = PROJECT_ROOT / "data" / "source_catalog"
MARKDOWN_PATH = CATALOG_OUTPUT_DIR / "sources.md"
JSON_PREFIX = "resultados_fontes_cenario01"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = CATALOG_OUTPUT_DIR / "raw"
OUTPUT_DIR = CATALOG_OUTPUT_DIR / "runs"
DOWNLOAD_LIMIT = 30 * 1024 * 1024

PNCP_HOST = "https://pncp.gov.br"
PNCP_CONSULTA_BASE = PNCP_HOST + "/api/consulta"
PNCP_API_BASE = PNCP_HOST + "/api/pncp"

USER_AGENT = "Mozilla/5.0 (compatible; rag-grande-desafio-cenario01-source-check/2.0)"
READ_LIMIT = 2 * 1024 * 1024
PREVIEW_LIMIT = 240
DEFAULT_TIMEOUT = 30.0
DEFAULT_ATTEMPTS = 3
RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}

PNCP_CLIENT = PncpClient(
    host=PNCP_HOST,
    user_agent=USER_AGENT,
    read_limit=READ_LIMIT,
    retryable_http=frozenset(RETRYABLE_HTTP),
)
ARTIFACT_DOWNLOADER = ArtifactDownloader(
    root=PROJECT_ROOT,
    output_dir=RAW_DIR,
    download_limit=DOWNLOAD_LIMIT,
    retryable_http=frozenset(RETRYABLE_HTTP),
)

WEB_SOURCES = (
    (
        "lei_14133",
        "Planalto — Lei 14.133/2021",
        "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
        "html",
    ),
    (
        "lc_123",
        "Planalto — Lei Complementar 123/2006",
        "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp123.htm",
        "html",
    ),
    (
        "decreto_rj_48816",
        "CGE-RJ — Decreto Estadual 48.816/2023",
        "https://www.cge.rj.gov.br/wp-content/uploads/2024/01/Decreto-no-48.816.pdf",
        "pdf",
    ),
    (
        "decreto_rj_48820",
        "CGE-RJ — Decreto Estadual 48.820/2023",
        "https://www.cge.rj.gov.br/wp-content/uploads/2024/01/Decreto-no-48.820.pdf",
        "pdf",
    ),
    (
        "redelog",
        "REDELOG RJ — legislação de licitações",
        "https://redelog.rj.gov.br/redelog/legislacao-licitacoes/",
        "html",
    ),
    (
        "cge_decretos",
        "CGE-RJ — decretos estaduais RJ",
        "https://cge.rj.gov.br/legislacao/decretos/",
        "html",
    ),
    (
        "pncp_api_docs",
        "PNCP — documentação OpenAPI v3",
        "https://pncp.gov.br/api/consulta/v3/api-docs",
        "json",
    ),
    (
        "pncp_swagger",
        "PNCP — Swagger UI",
        "https://pncp.gov.br/api/consulta/swagger-ui/index.html#/",
        "html",
    ),
    ("catmat", "CATMAT", "https://catmat.com.br/", "html"),
    (
        "gist_mcp_compras",
        "Gist — MCP Compras",
        "https://gist.github.com/Micael106/04a3e5515057ab11ea8797603682f0bd",
        "html",
    ),
    (
        "glama_mcp_compras",
        "Glama — MCP Compras PNCP PCA",
        "https://glama.ai/mcp/servers/opedrosoares/MCP_Compras/tools/compras_pncp_pca_por_classificacao_superior",
        "html",
    ),
    (
        "in_gov_lei_14133",
        "Diário Oficial da União — publicação da Lei 14.133",
        "http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?jornal=515&pagina=1&data=01/04/2021",
        "html",
    ),
    (
        "mppi_lei_14133_pdf",
        "MPPI — PDF da Lei 14.133/2021",
        "https://www.mppi.mp.br/internet/wp-content/uploads/2021/04/L14133.pdf",
        "pdf",
    ),
)

NORMATIVE_SOURCES = (
    {
        "url": "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
        "metadata": {
            "titulo": "Lei nº 14.133/2021",
            "tipo_documento": "lei_federal",
            "numero_norma": "Lei 14.133/2021",
            "data_publicacao": "2021-04-01",
            "data_vigencia_inicio": "2021-04-01",
            "ambito": "federal",
            "fonte_oficial": "Presidência da República — Planalto",
        },
    },
    {
        "url": "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp123.htm",
        "metadata": {
            "titulo": "Lei Complementar nº 123/2006",
            "tipo_documento": "lei_federal",
            "numero_norma": "LC 123/2006",
            "data_publicacao": "2006-12-15",
            "data_vigencia_inicio": "2006-12-15",
            "ambito": "federal",
            "fonte_oficial": "Presidência da República — Planalto",
        },
    },
    {
        "url": "https://www.cge.rj.gov.br/wp-content/uploads/2024/01/Decreto-no-48.816.pdf",
        "metadata": {
            "titulo": "Decreto Estadual nº 48.816/2023 — fase preparatória e pesquisa de preços",
            "tipo_documento": "decreto_estadual",
            "numero_norma": "Decreto 48.816/2023",
            "data_publicacao": "2023-11-25",
            "data_vigencia_inicio": "2023-11-25",
            "ambito": "Poder Executivo estadual direto, autárquico e fundacional do RJ",
            "fonte_oficial": "Controladoria Geral do Estado do Rio de Janeiro — Diário Oficial",
        },
    },
    {
        "url": "https://www.cge.rj.gov.br/wp-content/uploads/2024/01/Decreto-no-48.820.pdf",
        "metadata": {
            "titulo": "Decreto Estadual nº 48.820/2023 — contratação direta",
            "tipo_documento": "decreto_estadual",
            "numero_norma": "Decreto 48.820/2023",
            "data_publicacao": "2023-11-28",
            "data_vigencia_inicio": "2023-11-28",
            "ambito": "Poder Executivo estadual direto, autárquico e fundacional do RJ",
            "fonte_oficial": "Controladoria Geral do Estado do Rio de Janeiro — Diário Oficial",
        },
    },
)

# Falhas que devem tornar a execução não bem-sucedida.
HARD_FAILURE_STATUSES = {
    "indisponivel",
    "indisponivel_temporariamente",
    "erro_requisicao",
    "erro_http",
    "resposta_invalida",
    "resposta_truncada",
    "erro_download",
}
PARTIAL_STATUSES = {"sem_amostra", "bloqueado_por_fonte"}


@dataclass(frozen=True)
class CheckConfig:
    ano: int = 2025
    uf: str = "RJ"
    modalidade: int | None = 1
    tamanho_pagina: int = 10
    pagina: int = 1
    timeout: float = DEFAULT_TIMEOUT
    attempts: int = DEFAULT_ATTEMPTS
    classificacao_superior: str = "979"
    data_inicial: str | None = None
    data_final: str | None = None


def _normalize_api_date(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value.replace("-", "")
    return value


def make_consulta_url(path: str, params: dict[str, Any]) -> str:
    """Monta URL da API pública de consultas (/api/consulta)."""
    date_fields = {"dataInicial", "dataFinal", "dataInicio", "dataFim"}
    normalized = {
        key: _normalize_api_date(value) if key in date_fields else value
        for key, value in params.items()
    }
    return PNCP_CLIENT.consulta_url(path, normalized)


def make_api_url(path: str) -> str:
    """Monta URL da API PNCP de recurso identificado (/api/pncp)."""
    return PNCP_CLIENT.api_url(path)


def bounded_preview(body: bytes, content_type: str) -> str:
    text = body[:PREVIEW_LIMIT].decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text).strip()
    if len(body) > PREVIEW_LIMIT:
        text += "…"
    if "html" not in content_type.lower() and "json" not in content_type.lower():
        return f"{len(body)} bytes binários"
    return text


def run_timestamp(checked_at: str) -> str:
    return datetime.fromisoformat(checked_at).strftime("%Y-%m-%d_%H-%M-%SZ")


def run_output_dir(checked_at: str) -> Path:
    output_dir = OUTPUT_DIR / run_timestamp(checked_at)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def download_url(
    url: str,
    destination_dir: Path,
    filename_prefix: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    deduplicate: bool = True,
) -> dict[str, Any]:
    """Compatibilidade para o CLI; a implementação fica em ArtifactDownloader."""
    return ARTIFACT_DOWNLOADER.download_url(
        url,
        destination_dir,
        filename_prefix,
        attempts=attempts,
        timeout=timeout,
        headers=headers,
        deduplicate=deduplicate,
    )


def request_url(
    url: str,
    kind: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compatibilidade para o CLI; a implementação fica em PncpClient."""
    return PNCP_CLIENT.request_url(url, kind, attempts=attempts, timeout=timeout, headers=headers)


def empty_result(status: str, observacao: str) -> dict[str, Any]:
    return {
        "status": status,
        "http_status": None,
        "content_type": "",
        "bytes_lidos": 0,
        "url_final": "",
        "previa": "",
        "observacao": observacao,
        "tentativas": 0,
    }


def records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "content", "resultado", "resultados", "results"):
            records = records_from_payload(payload.get(key))
            if records:
                return records
    return []


def first_value(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) not in (None, ""):
            return record[key]
    return None


def nested_first_value(record: dict[str, Any], *keys: str) -> Any:
    value = first_value(record, *keys)
    if value is not None:
        return value
    for nested_key in ("orgaoEntidade", "unidadeOrgao", "compra", "contratacao", "ata", "pca"):
        nested = record.get(nested_key)
        if isinstance(nested, dict):
            value = first_value(nested, *keys)
            if value is not None:
                return value
    return None


# O primeiro bloco aceita CNPJ numérico e também o formato alfanumérico.
_PURCHASE_CONTROL_RE = re.compile(r"^([A-Za-z0-9]{14})-1-(\d+)/(\d{4})$")
_ATA_CONTROL_RE = re.compile(r"^([A-Za-z0-9]{14})-1-(\d+)/(\d{4})-(\d+)$")
_PCA_CONTROL_RE = re.compile(r"^([A-Za-z0-9]{14})-0-(\d+)/(\d{4})$")


def _purchase_from_control_number(value: Any) -> dict[str, str] | None:
    if value in (None, ""):
        return None
    match = _PURCHASE_CONTROL_RE.match(str(value).strip())
    if not match:
        return None
    return {"cnpj": match.group(1), "ano": match.group(3), "sequencial": match.group(2)}


def sample_purchase(publication_payload: Any) -> dict[str, str] | None:
    for record in records_from_payload(publication_payload):
        cnpj = nested_first_value(record, "cnpj", "cnpjOrgao", "orgaoEntidadeCnpj")
        year = nested_first_value(record, "anoCompra", "ano")
        sequence = nested_first_value(record, "sequencialCompra", "sequencial")

        control_number = first_value(
            record,
            "numeroControlePNCP",
            "numeroControlePncp",
            "numeroControlePNCPCompra",
            "numeroControlePncpCompra",
        )
        parsed = _purchase_from_control_number(control_number)
        if parsed:
            cnpj = cnpj or parsed["cnpj"]
            year = year or parsed["ano"]
            sequence = sequence or parsed["sequencial"]

        if cnpj and year and sequence:
            return {"cnpj": str(cnpj), "ano": str(year), "sequencial": str(sequence)}
    return None


def sample_ata(ata_payload: Any) -> dict[str, str] | None:
    for record in records_from_payload(ata_payload):
        cnpj = nested_first_value(record, "cnpj", "cnpjOrgao", "orgaoEntidadeCnpj")
        year = nested_first_value(record, "anoCompra", "ano")
        purchase = nested_first_value(record, "sequencialCompra", "sequencial")
        ata_sequence = nested_first_value(record, "sequencialAta", "seqAta")
        control_number = first_value(
            record,
            "numeroControlePNCPAta",
            "numeroControlePncpAta",
            "numeroControlePNCP",
            "numeroControlePncp",
        )
        if control_number:
            match = _ATA_CONTROL_RE.match(str(control_number).strip())
            if match:
                cnpj = cnpj or match.group(1)
                purchase = purchase or match.group(2)
                year = year or match.group(3)
                ata_sequence = ata_sequence or match.group(4)
        if cnpj and year and purchase and ata_sequence:
            return {
                "cnpj": str(cnpj),
                "ano": str(year),
                "sequencial": str(purchase),
                "seq_ata": str(ata_sequence),
            }
    return None


def sample_pca(pca_payload: Any) -> dict[str, str] | None:
    for record in records_from_payload(pca_payload):
        cnpj = nested_first_value(record, "orgaoEntidadeCnpj", "cnpj", "cnpjOrgao")
        year = nested_first_value(record, "anoPca", "ano")
        sequence = nested_first_value(record, "sequencialPca", "sequencial")
        control_number = first_value(
            record,
            "idPcaPncp",
            "numeroControlePNCPPca",
            "numeroControlePncpPca",
            "numeroControlePNCP",
            "numeroControlePncp",
        )
        if control_number:
            match = _PCA_CONTROL_RE.match(str(control_number).strip())
            if match:
                cnpj = cnpj or match.group(1)
                sequence = sequence or match.group(2)
                year = year or match.group(3)
        if cnpj and year and sequence:
            return {"cnpj": str(cnpj), "ano": str(year), "sequencial": str(sequence)}
    return None


def _month_window(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}{month:02d}01", f"{year:04d}{month:02d}{last_day:02d}"


def sample_windows(config: CheckConfig) -> list[tuple[str, str]]:
    if config.data_inicial or config.data_final:
        if not (config.data_inicial and config.data_final):
            raise ValueError("--data-inicial e --data-final devem ser usados juntos")
        return [(config.data_inicial, config.data_final)]
    # Em vez de pedir um ano inteiro só para obter uma amostra, tenta quatro meses.
    return [_month_window(config.ano, month) for month in (1, 4, 7, 10)]


def _is_available(result: dict[str, Any]) -> bool:
    return result.get("status") == "disponivel"


def _is_transient_failure(result: dict[str, Any]) -> bool:
    return result.get("status") in {"indisponivel", "indisponivel_temporariamente"}


def query_for_sample(
    path: str,
    params_factory: Callable[[str, str], dict[str, Any]],
    extractor: Callable[[Any], dict[str, str] | None],
    config: CheckConfig,
    before_request: Callable[[], None] | None = None,
) -> tuple[str, dict[str, Any], dict[str, str] | None]:
    """Tenta recortes pequenos até encontrar amostra; não insiste quando o servidor falha."""
    tested_periods: list[str] = []
    last_url = ""
    last_result: dict[str, Any] = empty_result("sem_amostra", "nenhum período foi testado")

    for start, end in sample_windows(config):
        params = params_factory(start, end)
        url = make_consulta_url(path, params)
        if before_request is not None:
            before_request()
        result = request_url(url, "json", attempts=config.attempts, timeout=config.timeout)
        tested_periods.append(f"{start}-{end}")
        result["periodos_testados"] = tested_periods.copy()
        last_url, last_result = url, result

        if not _is_available(result):
            # Em 429/5xx/timeouts, mudar datas só aumenta a carga sem testar outra coisa útil.
            return url, result, None

        sample = extractor(result.get("json"))
        if sample:
            return url, result, sample

    if _is_available(last_result):
        last_result = dict(last_result)
        last_result["observacao"] = (
            "consulta respondeu normalmente, mas não foi encontrada amostra com os "
            f"identificadores necessários nos períodos testados: {', '.join(tested_periods)}"
        )
    return last_url, last_result, None


def blocked_result(upstream_name: str, upstream: dict[str, Any]) -> dict[str, Any]:
    if _is_transient_failure(upstream):
        reason = f"não testado porque {upstream_name} está temporariamente indisponível"
    else:
        reason = f"não testado porque {upstream_name} não forneceu os identificadores necessários"
    return empty_result("bloqueado_por_fonte", reason)


def run_checks(config: CheckConfig) -> tuple[str, list[dict[str, Any]]]:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[dict[str, Any]] = []

    publication, contracts, purchase = _check_purchases(config, results)
    for source_id, name, url, kind in WEB_SOURCES:
        results.append(_requested(source_id, name, url, config, kind))
    _check_atas(config, results)
    results.append(
        {
            "id": "emails_sinteticos",
            "nome": "Base sintética — e-mails de compras",
            "url": "não há URL pública indicada",
            **empty_result("nao_aplicavel", "fonte local/sintética; localizar a base fornecida pelo desafio"),
        }
    )
    _check_pca(config, results)

    if purchase:
        results.append(_requested(
            "resultados_itens",
            "PNCP — resultados do item 1",
            make_api_url(
                f"/v1/orgaos/{purchase['cnpj']}/compras/{purchase['ano']}/{purchase['sequencial']}/itens/1/resultados"
            ),
            config,
        ))
    else:
        dependency = publication if not _is_available(publication) else contracts
        results.append(_blocked("resultados_itens", "PNCP — resultados por item", "as consultas de contratação", dependency))

    results.append(
        {
            "id": "pipeline_agregacoes",
            "nome": "Pipeline — totais, médias e séries",
            "url": "sem API própria",
            **empty_result(
                "nao_aplicavel",
                "processamento local; disponibilidade depende da implementação do pipeline",
            ),
        }
    )
    return checked_at, results


def _requested(result_id: str, name: str, url: str, config: CheckConfig, kind: str = "json") -> dict[str, Any]:
    """Resultado de uma consulta feita agora, com o id, o nome e a URL da fonte."""
    return {
        "id": result_id,
        "nome": name,
        "url": url,
        **request_url(url, kind, attempts=config.attempts, timeout=config.timeout),
    }


def _blocked(result_id: str, name: str, upstream_name: str, upstream: dict[str, Any]) -> dict[str, Any]:
    """Fonte parametrizada que não foi consultada porque a consulta de que depende não trouxe amostra."""
    return {"id": result_id, "nome": name, "url": "parametrizada", **blocked_result(upstream_name, upstream)}


def _check_purchases(
    config: CheckConfig, results: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str] | None]:
    """Publicação, contratos e, com uma compra de amostra, o detalhe e os arquivos dela.

    Devolve (publicação, contratos, compra de amostra) para os resultados por item, consultados depois.
    """
    publication_url, publication, purchase = query_for_sample(
        "/v1/contratacoes/publicacao",
        lambda start, end: {
            "dataInicial": start,
            "dataFinal": end,
            **(
                {"codigoModalidadeContratacao": str(config.modalidade)}
                if config.modalidade is not None
                else {}
            ),
            "uf": config.uf,
            "pagina": str(config.pagina),
            "tamanhoPagina": str(config.tamanho_pagina),
        },
        sample_purchase,
        config,
    )
    results.append(
        {
            "id": "contratacoes_publicacao",
            "nome": "PNCP — contratações/publicação",
            "url": publication_url,
            **publication,
        }
    )

    # Contratos é uma fonte independente. Consulta só um mês para checar disponibilidade.
    first_start, first_end = sample_windows(config)[0]
    contracts_url = make_consulta_url(
        "/v1/contratos",
        {
            "dataInicial": first_start,
            "dataFinal": first_end,
            "pagina": str(config.pagina),
            "tamanhoPagina": str(config.tamanho_pagina),
        },
    )
    contracts = request_url(contracts_url, "json", attempts=config.attempts, timeout=config.timeout)
    results.append({"id": "contratos", "nome": "PNCP — contratos", "url": contracts_url, **contracts})

    # Se publicação não encontrou compra, alguns payloads de contrato trazem o controle da compra.
    if purchase is None and _is_available(contracts):
        purchase = sample_purchase(contracts.get("json"))

    if purchase:
        purchase_path = f"/v1/orgaos/{purchase['cnpj']}/compras/{purchase['ano']}/{purchase['sequencial']}"
        results.append(_requested(
            "detalhe_contratacao", "PNCP — detalhe da contratação encontrada", make_api_url(purchase_path), config
        ))
        results.append(_requested(
            "arquivos_edital", "PNCP — arquivos/editais da contratação", make_api_url(purchase_path + "/arquivos"), config
        ))
    else:
        dependency = publication if not _is_available(publication) else contracts
        results.extend(
            [
                _blocked("detalhe_contratacao", "PNCP — detalhe da contratação encontrada",
                         "as consultas de contratação", dependency),
                _blocked("arquivos_edital", "PNCP — arquivos/editais da contratação",
                         "as consultas de contratação", dependency),
            ]
        )
    return publication, contracts, purchase


def _check_atas(config: CheckConfig, results: list[dict[str, Any]]) -> None:
    """Atas e, com uma ata de amostra, os arquivos dela."""
    ata_url, atas, ata = query_for_sample(
        "/v1/atas",
        lambda start, end: {
            "dataInicial": start,
            "dataFinal": end,
            "pagina": str(config.pagina),
            "tamanhoPagina": str(config.tamanho_pagina),
        },
        sample_ata,
        config,
    )
    results.append({"id": "atas", "nome": "PNCP — atas", "url": ata_url, **atas})

    if ata:
        results.append(_requested(
            "arquivos_atas",
            "PNCP — arquivos das atas",
            make_api_url(
                f"/v1/orgaos/{ata['cnpj']}/compras/{ata['ano']}/{ata['sequencial']}/atas/{ata['seq_ata']}/arquivos"
            ),
            config,
        ))
    else:
        results.append(_blocked("arquivos_atas", "PNCP — arquivos das atas", "a consulta de atas", atas))


def _check_pca(config: CheckConfig, results: list[dict[str, Any]]) -> None:
    """Consulta de PCA e, com um plano de amostra, o consolidado e os itens dele."""
    pca_params: dict[str, Any] = {
        "anoPca": str(config.ano),
        "pagina": str(config.pagina),
        "tamanhoPagina": str(config.tamanho_pagina),
    }
    pca_params["codigoClassificacaoSuperior"] = config.classificacao_superior

    pca_url = make_consulta_url("/v1/pca", pca_params)
    pca = request_url(pca_url, "json", attempts=config.attempts, timeout=config.timeout)
    results.append({"id": "pca_consulta", "nome": "PNCP — consulta de PCA", "url": pca_url, **pca})
    pca_sample = sample_pca(pca.get("json")) if _is_available(pca) else None

    if pca_sample:
        pca_base = make_api_url(f"/v1/orgaos/{pca_sample['cnpj']}/pca/{pca_sample['ano']}")
        results.append(_requested("pca_consolidado", "PNCP — PCA consolidado por órgão", pca_base + "/consolidado", config))
        results.append(_requested(
            "pca_itens", "PNCP — itens do PCA", pca_base + f"/{pca_sample['sequencial']}/itens/contratacao", config
        ))
    else:
        results.extend(
            [
                _blocked("pca_consolidado", "PNCP — PCA consolidado por órgão", "a consulta de PCA", pca),
                _blocked("pca_itens", "PNCP — itens do PCA", "a consulta de PCA", pca),
            ]
        )


def find_result(results: list[dict[str, Any]], result_id: str) -> dict[str, Any] | None:
    return next((result for result in results if result.get("id") == result_id), None)


def first_file_url(result: dict[str, Any] | None) -> str | None:
    if not result or not _is_available(result):
        return None
    for record in records_from_payload(result.get("json")):
        for key in ("url", "uri", "location"):
            value = record.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
    return None



def save_json_artifacts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persiste respostas JSON e listas JSONL no repositório deduplicado."""
    artifacts: list[dict[str, Any]] = []
    for result in results:
        payload = result.get("json")
        if payload is None:
            continue
        source_id = str(result.get("id") or "fonte")
        common = {
            "http_status": result.get("http_status"),
            "tentativas": result.get("tentativas", 0),
            "content_type": "application/json",
            "url": result.get("url"),
        }

        json_body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        saved = ARTIFACT_DOWNLOADER.save_bytes(
            json_body,
            RAW_DIR / "arquivos" / "json",
            f"raw_{source_id}",
            ".json",
        )
        artifacts.append(
            {
                "id": f"arquivo_json_{source_id}",
                "nome": f"JSON bruto — {result.get('nome', source_id)}",
                **common,
                **saved,
            }
        )

        records = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(records, list) or not records:
            continue
        jsonl_body = b"".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
            for record in records
        )
        saved_jsonl = ARTIFACT_DOWNLOADER.save_bytes(
            jsonl_body,
            RAW_DIR / "arquivos" / "jsonl",
            f"records_{source_id}",
            ".jsonl",
        )
        artifacts.append(
            {
                "id": f"arquivo_jsonl_{source_id}",
                "nome": f"JSONL — registros de {result.get('nome', source_id)}",
                **common,
                "content_type": "application/x-ndjson",
                **saved_jsonl,
            }
        )
    return artifacts


def download_artifacts(
    checked_at: str,
    results: list[dict[str, Any]],
    config: CheckConfig,
) -> list[dict[str, Any]]:
    """Baixa fontes e persiste respostas JSON/JSONL sem duplicação."""
    downloads: list[dict[str, Any]] = []
    for source_id, name, url, kind in WEB_SOURCES:
        source = find_result(results, source_id)
        if source and _is_available(source):
            folder = (
                "pdf"
                if kind == "pdf"
                else "jsonl"
                if kind == "jsonl"
                else "json"
                if kind == "json"
                else "html"
            )
            downloaded = download_url(
                url,
                RAW_DIR / "arquivos" / folder,
                source_id,
                attempts=config.attempts,
                timeout=config.timeout,
            )
        else:
            downloaded = {
                "status": "bloqueado_por_fonte",
                "http_status": None,
                "tentativas": 0,
                "url": url,
                "observacao": f"download não tentado porque {name} não ficou disponível",
            }
        downloads.append({"id": f"download_{source_id}", "nome": f"Download — {name}", **downloaded})

    for result_id, name, filename_prefix in (
        ("arquivos_edital", "PNCP — primeiro arquivo de edital", "edital"),
        ("arquivos_atas", "PNCP — primeiro arquivo de ata", "ata"),
    ):
        source = find_result(results, result_id)
        file_url = first_file_url(source)
        if file_url:
            downloaded = download_url(
                file_url,
                RAW_DIR / "arquivos" / "pncp",
                filename_prefix,
                attempts=config.attempts,
                timeout=config.timeout,
            )
        else:
            downloaded = {
                "status": "bloqueado_por_fonte",
                "http_status": None,
                "tentativas": 0,
                "url": "parametrizada",
                "observacao": f"download não tentado porque {name} não forneceu uma URL de arquivo",
            }
        downloads.append({"id": f"download_{result_id}", "nome": f"Download — {name}", **downloaded})
    downloads.extend(save_json_artifacts(results))
    return downloads


def format_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def update_markdown(
    checked_at: str,
    results: list[dict[str, Any]],
    downloads: list[dict[str, Any]] | None = None,
) -> None:
    current = MARKDOWN_PATH.read_text(encoding="utf-8") if MARKDOWN_PATH.exists() else ""
    marker = "## Resultados da última verificação"
    prefix = current.split(marker, 1)[0].rstrip()
    rows = [
        "## Resultados da última verificação",
        "",
        f"Última execução: `{checked_at}`.",
        "",
        "| Fonte/teste | Disponibilidade | HTTP | Tentativas | Content-Type | URL testada | Observação |",
        "| --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for result in results:
        observation = result.get("observacao") or result.get("erro") or result.get("previa", "")
        rows.append(
            "| "
            + " | ".join(
                [
                    format_cell(result["nome"]),
                    format_cell(result["status"]),
                    format_cell(result.get("http_status") or "—"),
                    format_cell(result.get("tentativas", "—")),
                    format_cell(result.get("content_type") or "—"),
                    format_cell(result["url"]),
                    format_cell(observation),
                ]
            )
            + " |"
        )
    rows.extend(
        [
            "",
            "Estados principais: `disponivel` = resposta válida; `indisponivel_temporariamente` = 429/5xx após retries; `erro_requisicao` = 4xx não transitório; `bloqueado_por_fonte` = endpoint encadeado não pôde ser testado; `nao_aplicavel` = fonte local; `indisponivel` = falha de rede/timeout.",
            "",
            "Esta tabela é histórica; use `py -m src.source_catalog lookup pncp` para novas consultas.",
            "",
        ]
    )
    if downloads is not None:
        rows.extend(
            [
                "",
                "## Arquivos baixados",
                "",
                "| Arquivo | Status | HTTP | Tentativas | Content-Type | Caminho local | Observação |",
                "| --- | --- | ---: | ---: | --- | --- | --- |",
            ]
        )
        for download in downloads:
            observation = download.get("observacao") or download.get("erro") or ""
            rows.append(
                "| "
                + " | ".join(
                    [
                        format_cell(download["nome"]),
                        format_cell(download.get("status")),
                        format_cell(download.get("http_status") or "—"),
                        format_cell(download.get("tentativas", "—")),
                        format_cell(download.get("content_type") or "—"),
                        format_cell(download.get("arquivo") or "—"),
                        format_cell(observation),
                    ]
                )
                + " |"
            )
    content = (prefix + "\n\n" if prefix else "") + "\n".join(rows)
    MARKDOWN_PATH.write_text(content, encoding="utf-8")


def save_json(
    checked_at: str,
    results: list[dict[str, Any]],
    downloads: list[dict[str, Any]] | None = None,
) -> Path:
    summary = [{key: value for key, value in result.items() if key != "json"} for result in results]
    timestamp = run_timestamp(checked_at)
    json_path = run_output_dir(checked_at) / f"{JSON_PREFIX}_{timestamp}.json"
    summary_downloads = [
        {key: value for key, value in download.items() if key != "json"}
        for download in (downloads or [])
    ]
    json_path.write_text(
        json.dumps(
            {"verificado_em": checked_at, "resultados": summary, "downloads": summary_downloads},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return json_path


def _validate_yyyymmdd(value: str) -> str:
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise argparse.ArgumentTypeError("use o formato YYYYMMDD, por exemplo 20250131") from error
    return value


def _exit_code(results: list[dict[str, Any]]) -> int:
    statuses = {str(result.get("status")) for result in results}
    if statuses & HARD_FAILURE_STATUSES:
        return 1
    if statuses & PARTIAL_STATUSES:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada compatível; a CLI pública é a interface de curadoria."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        from .manifest_curator import main as curator_main
    except ImportError:
        from manifest_curator import main as curator_main
    if not arguments or arguments[0] in {
        "-h",
        "--help",
        "discover",
        "lookup",
        "propose",
        "normative-proposal",
        "review",
        "manifest-diff",
        "apply",
    }:
        return curator_main(arguments)
    print(
        "A interface antiga foi removida. Use um subcomando: discover, lookup, "
        "propose, normative-proposal, review, manifest-diff ou apply.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
