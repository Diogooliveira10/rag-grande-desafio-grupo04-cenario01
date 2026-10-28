"""Indicadores derivados dos resultados homologados de itens do PNCP."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any


_CONTROL = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")
_ITEM_IN_ARTIFACT = re.compile(r":item:(\d+):resultados(?:$|:)")


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [value for value in payload if isinstance(value, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "resultado", "registros", "content", "lista"):
        nested = payload.get(key)
        if isinstance(nested, list):
            return [value for value in nested if isinstance(value, dict)]
        if isinstance(nested, dict):
            records = _records(nested)
            if records:
                return records
    return [payload]


def _walk_values(value: Any, names: set[str]):
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).casefold() in names and nested not in (None, ""):
                yield nested
            yield from _walk_values(nested, names)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_values(nested, names)


def _first(value: Any, *names: str) -> Any:
    normalized = {name.casefold() for name in names}
    return next(_walk_values(value, normalized), None)


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _source_group(source_ref: Any) -> tuple[str, str, str] | None:
    if not isinstance(source_ref, dict) or not isinstance(source_ref.get("key"), dict):
        return None
    key = source_ref["key"]
    control = str(key.get("numeroControlePNCPCompra") or key.get("numeroControlePNCP") or "")
    match = _CONTROL.match(control)
    if match:
        return match.group(1), match.group(3), str(int(match.group(2)))
    cnpj = str(key.get("cnpj") or "")
    year = str(key.get("ano") or key.get("anoCompra") or "")
    sequence = str(key.get("sequencial") or key.get("sequencialCompra") or "")
    if cnpj and year and sequence:
        return cnpj, year, str(int(sequence))
    return None


def _read_payload(record: dict[str, Any], project_root: Path) -> Any:
    path = project_root / str(record.get("path") or record.get("source_file") or "")
    if path.suffix.lower() != ".json" or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def _key(record: dict[str, Any]) -> tuple[str, str, str, int] | None:
    group = _source_group(record.get("source_ref"))
    if group is None:
        return None
    artifact = str(record.get("artifact_id") or "")
    match = _ITEM_IN_ARTIFACT.search(artifact)
    item_number = match.group(1) if match else ""
    if not item_number:
        return None
    return (*group, int(item_number))


def _quartile(values: list[Decimal], fraction: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    position = Decimal(len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    remainder = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * remainder


def _price_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prices = sorted(Decimal(row["valorUnitarioHomologado"]) for row in rows)
    quantities = [Decimal(row["quantidadeHomologada"]) for row in rows]
    quantity_total = sum(quantities, Decimal(0))
    weighted_total = sum(
        Decimal(row["valorUnitarioHomologado"]) * Decimal(row["quantidadeHomologada"])
        for row in rows
    )
    reported_totals = [
        _decimal(row.get("valorTotalHomologado"))
        for row in rows
    ]
    reported_totals = [value for value in reported_totals if value is not None and value >= 0]
    explicit_total = sum(reported_totals, Decimal(0)) if reported_totals else None
    median_price = Decimal(str(median(prices)))
    contract_ids = set()
    for row in rows:
        source = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
        key = source.get("key") if isinstance(source.get("key"), dict) else {}
        contract_id = key.get("numeroControlePNCP") or key.get("numeroControlePncp")
        if contract_id:
            contract_ids.add(str(contract_id))
    return {
        "numero_observacoes": len(rows),
        "numero_compras": len({row["compra"] for row in rows}),
        "numero_itens": len({(row["compra"], row["numeroItem"]) for row in rows}),
        "numero_contratos_vinculados": len(contract_ids),
        "numero_fornecedores": len({row["niFornecedor"] for row in rows if row.get("niFornecedor")}),
        "quantidade_homologada_total": str(quantity_total),
        "media_simples_valor_unitario_homologado": str(sum(prices, Decimal(0)) / Decimal(len(prices))),
        "media_ponderada_quantidade_homologada": str(weighted_total / quantity_total) if quantity_total else None,
        "mediana_valor_unitario_homologado": str(median_price),
        "q1_valor_unitario_homologado": str(_quartile(prices, Decimal("0.25"))),
        "q3_valor_unitario_homologado": str(_quartile(prices, Decimal("0.75"))),
        "min_valor_unitario_homologado": str(prices[0]),
        "max_valor_unitario_homologado": str(prices[-1]),
        "valor_total_homologado_informado": str(explicit_total) if explicit_total is not None else None,
        "numero_totais_homologados_informados": len(reported_totals),
        "valor_total_calculado_unitario_vezes_quantidade": str(weighted_total),
    }


def _coverage_indicator(inventory: dict[str, Any]) -> dict[str, Any] | None:
    records = [
        record
        for record in inventory.get("arquivos", [])
        if isinstance(record, dict) and record.get("base_id") == "pca_agregado"
    ]
    if not records:
        return None

    contract_refs: set[str] = set()
    purchases_from_contracts: set[tuple[str, str, str]] = set()
    for record in records:
        source = record.get("source_ref")
        if not isinstance(source, dict):
            continue
        resource_type = str(source.get("resource_type") or "").casefold()
        if resource_type not in {"contrato_assinado", "contrato_pncp", "contratacao", "contrato", "contratação"}:
            continue
        key = source.get("key") if isinstance(source.get("key"), dict) else {}
        contract_id = key.get("numeroControlePNCP") or key.get("numeroControlePncp")
        if contract_id:
            contract_refs.add(str(contract_id))
        purchase_group = _source_group(source)
        if purchase_group:
            purchases_from_contracts.add(purchase_group)

    resource_stats: dict[str, dict[str, Any]] = {}
    for kind in ("contrato", "contratacao_detalhe", "contratacao_vinculada", "itens", "resultado_item"):
        rows = [record for record in records if record.get("resource_kind") == kind]
        if not rows:
            continue
        resource_stats[kind] = {
            "artefatos": len(rows),
            "sucesso": sum(record.get("status") == "ok" for record in rows),
            "falhas": sum(record.get("status") != "ok" for record in rows),
            "registros_informados": sum(int(record.get("registros", 0) or 0) for record in rows),
            "artefatos_vazios_ok": sum(record.get("status") == "ok" and int(record.get("registros", 0) or 0) == 0 for record in rows),
        }

    linked_purchase_groups: dict[str, set[tuple[str, str, str]]] = {}
    for kind in ("contratacao_detalhe", "contratacao_vinculada", "itens", "resultado_item"):
        linked_purchase_groups[kind] = {
            group
            for record in records
            if record.get("resource_kind") == kind and record.get("status") == "ok"
            if (group := _source_group(record.get("source_ref"))) is not None
        }

    summary = {
        "contratos_estruturados_usados": len(contract_refs),
        "compras_distintas_vinculadas": len(purchases_from_contracts),
        "compras_com_detalhe_disponivel": len(linked_purchase_groups.get("contratacao_vinculada", set()) | linked_purchase_groups.get("contratacao_detalhe", set())),
        "compras_com_itens_disponiveis": len(linked_purchase_groups.get("itens", set())),
        "compras_com_resultados_disponiveis": len(linked_purchase_groups.get("resultado_item", set())),
        "falhas_totais_na_base_agregada": sum(record.get("status") != "ok" for record in records),
        "falhas_sem_resource_kind": sum(record.get("status") != "ok" and not record.get("resource_kind") for record in records),
        "recursos": resource_stats,
    }
    text = (
        "Cobertura da coleta agregada vinculada à Estruturada: "
        f"{summary['contratos_estruturados_usados']} contratos de origem, "
        f"{summary['compras_distintas_vinculadas']} compras distintas; detalhes disponíveis para "
        f"{summary['compras_com_detalhe_disponivel']} compras, itens para "
        f"{summary['compras_com_itens_disponiveis']} e resultados homologados para "
        f"{summary['compras_com_resultados_disponiveis']}; falhas totais: "
        f"{summary['falhas_totais_na_base_agregada']}. "
        "Artefatos vazios indicam resposta sem registros; falhas permanecem separadas de ausência de resultado."
    )
    return {
        "indicador": "cobertura-coleta-resultados-vinculados",
        "tipo_valor": "cobertura_de_coleta",
        **summary,
        "texto": text,
    }


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
            stream.write("\n")
    os.replace(temporary, path)


def indicadores_resultados_homologados(
    inventory: dict[str, Any],
    project_root: Path,
    *,
    fatos_saida: Path | None = None,
) -> list[dict[str, Any]]:
    """Resume resultados PNCP por descrição/unidade/localidade/modalidade.

    A descrição e a unidade são mantidas literalmente: sem análise do edital,
    isso não afirma equivalência técnica entre itens parecidos.
    """
    arquivos = sorted(
        (
            record
            for record in inventory.get("arquivos", [])
            if isinstance(record, dict) and record.get("status") == "ok"
        ),
        key=lambda record: str(record.get("artifact_id") or record.get("logical_id") or ""),
    )
    item_rows, metadata, result_rows = _read_artifacts(arquivos, project_root)
    observations = _observations(result_rows, item_rows, metadata)

    if fatos_saida is not None:
        _write_jsonl(fatos_saida, observations)

    groups: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        group = (
            observation["descricao"],
            observation["unidadeMedida"],
            str(observation.get("municipio") or "não informado"),
            str(observation.get("esfera") or "não informada"),
            str(observation.get("modalidade") or "não informada"),
            str(observation.get("anoAssinatura") or "não informado"),
        )
        groups[group].append(observation)

    indicators = [_group_indicator(key, rows) for key, rows in sorted(groups.items())]
    coverage = _coverage_indicator(inventory)
    if coverage is not None:
        indicators.append(coverage)
    return indicators


def _read_artifacts(
    arquivos: list[dict[str, Any]], project_root: Path
) -> tuple[
    dict[tuple[str, str, str, int], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    list[tuple[dict[str, Any], dict[str, Any]]],
]:
    """(itens por compra e número, metadados da compra, pares artefato/resultado) dos artefatos lidos."""
    item_rows: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    metadata: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(dict)
    result_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for record in arquivos:
        kind = str(record.get("resource_kind") or "")
        if kind not in {"contratacao_detalhe", "contratacao_vinculada", "contratacao_publicada", "contrato", "itens", "resultado_item"}:
            continue
        source_group = _source_group(record.get("source_ref"))
        if source_group is None:
            continue
        payload = _read_payload(record, project_root)
        if payload is None:
            continue
        if kind in {"contratacao_detalhe", "contratacao_vinculada", "contratacao_publicada", "contrato"}:
            for row in _records(payload):
                metadata[source_group].update(row)
        elif kind == "itens":
            for row in _records(payload):
                number = _first(row, "numeroItem", "numeroItemPncp", "numeroItemCompra")
                try:
                    parsed_number = int(number)
                except (TypeError, ValueError):
                    continue
                item_rows[(*source_group, parsed_number)] = row
        elif kind == "resultado_item":
            for row in _records(payload):
                result_rows.append((record, row))
    return item_rows, metadata, result_rows


def _result_item_key(record: dict[str, Any], result: dict[str, Any]) -> tuple[str, str, str, int] | None:
    """Item do resultado: pelo artifact_id ou, sem ele, pelo número informado no próprio resultado."""
    item_key = _key(record)
    if item_key is None:
        source_group = _source_group(record.get("source_ref"))
        item_number = _first(
            result,
            "numeroItemPncp",
            "numeroItem",
            "numeroItemCompra",
        )
        try:
            parsed_number = int(item_number)
        except (TypeError, ValueError):
            parsed_number = None
        if source_group is not None and parsed_number is not None:
            item_key = (*source_group, parsed_number)
    return item_key


def _observations(
    result_rows: list[tuple[dict[str, Any], dict[str, Any]]],
    item_rows: dict[tuple[str, str, str, int], dict[str, Any]],
    metadata: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Um fato por resultado homologado válido, sem repetir o mesmo resultado."""
    observations: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for record, result in result_rows:
        item_key = _result_item_key(record, result)
        if item_key is None:
            continue
        value = _decimal(result.get("valorUnitarioHomologado"))
        quantity = _decimal(result.get("quantidadeHomologada"))
        if value is None or value <= 0 or quantity is None or quantity <= 0:
            continue
        status = result.get("situacaoCompraItemResultadoId")
        if str(status) == "2":
            continue
        purchase_identity = ":".join(item_key[:3])
        result_identity = (
            purchase_identity,
            item_key[3],
            result.get("sequencialResultado"),
            result.get("niFornecedor"),
            str(value),
            str(quantity),
        )
        if result_identity in seen:
            continue
        seen.add(result_identity)
        observations.append(_observation(
            record, result, item_key, item_rows.get(item_key, {}), metadata.get(item_key[:3], {}),
            value=value, quantity=quantity, status=status, result_identity=result_identity,
        ))
    return observations


def _observation(
    record: dict[str, Any],
    result: dict[str, Any],
    item_key: tuple[str, str, str, int],
    item: dict[str, Any],
    context: dict[str, Any],
    *,
    value: Decimal,
    quantity: Decimal,
    status: Any,
    result_identity: tuple[Any, ...],
) -> dict[str, Any]:
    description = str(
        _first(item, "descricao", "descricaoResumida", "descricaodetalhada")
        or _first(result, "descricao", "descricaoResumida", "descricaodetalhada")
        or ""
    ).strip()
    unit = str(item.get("unidadeMedida") or "").strip()
    municipality = _first(
        context,
        "municipioNome",
        "nomeMunicipio",
        "municipio",
        "unidadeOrgaoMunicipioNome",
    )
    sphere = _first(
        context,
        "esferaId",
        "esferaNome",
        "codigoEsfera",
        "orgaoEntidadeEsferaId",
    )
    modality = _first(
        context,
        "modalidadeNome",
        "codigoModalidadeContratacao",
        "modalidadeIdPncp",
    )
    signature_date = str(_first(context, "dataAssinatura") or "")[:10]
    signature_year = signature_date[:4] if re.match(r"^\d{4}-\d{2}-\d{2}$", signature_date) else "não informado"
    result_date = str(result.get("dataResultado") or result.get("dataResultadoPncp") or "")[:10]
    result_month = result_date[:7] if re.match(r"^\d{4}-\d{2}-\d{2}$", result_date) else None
    fact_identity = hashlib.sha256(
        json.dumps(result_identity, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return {
        "base_id": "pca_agregado",
        "dataset": "homologated_items",
        "fact_id": f"homologated-item:{fact_identity}",
        "compra": result_identity[0],
        "numeroItem": item_key[3],
        "descricao": description or "descrição não informada",
        "unidadeMedida": unit or "unidade não informada",
        "quantidadeHomologada": str(quantity),
        "valorUnitarioHomologado": str(value),
        "valorTotalHomologado": result.get("valorTotalHomologado"),
        "fornecedor": result.get("nomeRazaoSocialFornecedor"),
        "niFornecedor": result.get("niFornecedor"),
        "sequencialResultado": result.get("sequencialResultado"),
        "situacaoCompraItemResultadoId": status,
        "dataResultado": result_date or None,
        "municipio": municipality,
        "esfera": sphere,
        "modalidade": modality,
        "dataAssinatura": signature_date or None,
        "anoAssinatura": signature_year,
        "mesResultado": result_month,
        "origem": record.get("url_final") or record.get("origem"),
        "origem_primaria": record.get("origem"),
        "acquisition_backend": record.get("acquisition_backend") or "pncp",
        "source_ref": record.get("source_ref"),
        "artifact_id": record.get("artifact_id"),
    }


def _group_indicator(key: tuple[str, str, str, str, str, str], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Estatísticas e série mensal do valor homologado de um grupo descrição/unidade/local/modalidade/ano."""
    identity = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    municipality, sphere, modality, signature_year = key[2:]
    description = rows[0]["descricao"]
    unit = rows[0]["unidadeMedida"]
    metrics = _price_metrics(rows)
    monthly_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("mesResultado"):
            monthly_groups[str(row["mesResultado"])].append(row)
    monthly_series = [
        {"mes_resultado": month, **_price_metrics(month_rows)}
        for month, month_rows in sorted(monthly_groups.items())
    ]
    series_text = "; ".join(
        f"{month['mes_resultado']}: média simples R$ {month['media_simples_valor_unitario_homologado']}, "
        f"média ponderada R$ {month['media_ponderada_quantidade_homologada']}, "
        f"{month['numero_observacoes']} observações"
        for month in monthly_series
    ) or "sem data de resultado informada"
    return {
        "indicador": f"mediana-homologada-{identity}",
        "tipo_valor": "valor_unitario_homologado",
        "descricao_item": description,
        "unidade_medida": unit,
        "municipio": municipality,
        "esfera": sphere,
        "modalidade": modality,
        "ano_assinatura_contrato": signature_year,
        **metrics,
        "serie_mensal_resultado": monthly_series,
        "observacoes": [
            {
                "compra": row["compra"],
                "numeroItem": row["numeroItem"],
                "sequencialResultado": row.get("sequencialResultado"),
                "dataAssinatura": row.get("dataAssinatura"),
                "dataResultado": row.get("dataResultado"),
                "niFornecedor": row.get("niFornecedor"),
                "source_ref": row.get("source_ref"),
                "artifact_id": row.get("artifact_id"),
                "origem": row.get("origem"),
                "origem_primaria": row.get("origem_primaria"),
                "acquisition_backend": row.get("acquisition_backend"),
            }
            for row in rows
        ],
        "texto": (
            f"Estatísticas do valor unitário homologado para o item publicado como {description} ({unit}), "
            f"município {municipality}, esfera {sphere}, modalidade {modality}, "
            f"contratos assinados em {signature_year}: média simples R$ {metrics['media_simples_valor_unitario_homologado']}; "
            f"média ponderada pela quantidade R$ {metrics['media_ponderada_quantidade_homologada']}; "
            f"mediana R$ {metrics['mediana_valor_unitario_homologado']}; "
            f"Q1 R$ {metrics['q1_valor_unitario_homologado']}; Q3 R$ {metrics['q3_valor_unitario_homologado']}; "
            f"mínimo R$ {metrics['min_valor_unitario_homologado']}; máximo R$ {metrics['max_valor_unitario_homologado']}; "
            f"quantidade homologada {metrics['quantidade_homologada_total']} {unit}; "
            f"{metrics['numero_observacoes']} resultados em {metrics['numero_compras']} compras. "
            f"Série mensal por data do resultado: {series_text}. "
            "Valores homologados não comprovam preço pago nem, sem vínculo documental suficiente, valor efetivamente contratado. "
            "A equivalência técnica entre itens não foi avaliada; descrição e unidade foram mantidas como publicadas."
        ),
    }
