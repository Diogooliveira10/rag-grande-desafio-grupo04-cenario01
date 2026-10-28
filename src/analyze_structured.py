"""Read-only analysis of the structured corpus for planning aggregate coverage.

Run with ``py -m src.analyze_structured``. The report is derived from an
existing corpus inventory and its raw JSON artifacts; this module does not
download data, modify the corpus, or require non-standard dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PROJECT_ROOT / "data" / "inventory" / "v3.json"
STRUCTURED_BASE_ID = "contratos_estruturado"
AGGREGATE_BASE_ID = "pca_agregado"


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    return next((value for value in values if value not in (None, "")), None)


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = _first(value.get("nome"), value.get("descricao"), value.get("id"))
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _contract_view(payload: dict[str, Any], inventory_record: dict[str, Any]) -> dict[str, Any]:
    org = _mapping(payload.get("orgaoEntidade"))
    unit = _mapping(_first(payload.get("unidadeOrgao"), payload.get("unidadeAdministrativa")))
    supplier_name = _first(payload.get("nomeRazaoSocialFornecedor"), payload.get("nomeFornecedor"))
    signature = _date(payload.get("dataAssinatura"))
    publication = _date(payload.get("dataPublicacaoPncp"))
    unit_code = _first(unit.get("codigoUnidade"), unit.get("codigoUnidadeAdministrativa"))
    purchase_id = _first(payload.get("numeroControlePncpCompra"), payload.get("numeroControlePNCPCompra"))
    contract_id = _first(payload.get("numeroControlePNCP"), payload.get("numeroControlePncp"))
    modality = _first(
        payload.get("modalidadeNome"),
        payload.get("modalidadeId"),
        payload.get("modalidadeIdPncp"),
    )
    kind = _text(payload.get("tipoContrato"))
    category = _text(payload.get("categoriaProcesso"))
    return {
        "contract_id": str(contract_id) if contract_id else None,
        "purchase_id": str(purchase_id) if purchase_id else None,
        "signature_date": signature,
        "publication_date": publication,
        "contract_year": payload.get("anoContrato"),
        "contract_number": payload.get("numeroContratoEmpenho"),
        "process_number": payload.get("processo"),
        "object": _text(payload.get("objetoContrato")),
        "org_cnpj": _first(org.get("cnpj"), payload.get("cnpjOrgao")),
        "org_name": _first(org.get("razaoSocial"), org.get("nome")),
        "sphere": _first(org.get("esferaId"), org.get("esferaNome")),
        "power": _first(org.get("poderId"), org.get("poderNome")),
        "unit_code": str(unit_code) if unit_code else None,
        "unit_name": _first(unit.get("nomeUnidade"), unit.get("nome")),
        "municipality": _first(unit.get("municipioNome"), unit.get("nomeMunicipio")),
        "ibge_code": _first(unit.get("codigoIbge"), unit.get("codigoMunicipioIbge")),
        "uf": _first(unit.get("ufSigla"), unit.get("uf")),
        "supplier_cnpj": _first(payload.get("niFornecedor"), payload.get("cnpjFornecedor")),
        "supplier_name": supplier_name,
        "supplier_type": _first(payload.get("tipoPessoa"), payload.get("tipoPessoaFornecedor")),
        "modality": _text(modality),
        "contract_type": kind,
        "process_category": category,
        "value_global": _decimal(payload.get("valorGlobal")),
        "value_initial": _decimal(payload.get("valorInicial")),
        "value_installment": _decimal(payload.get("valorParcela")),
        "value_accumulated": _decimal(payload.get("valorAcumulado")),
        "valid_from": _date(payload.get("dataVigenciaInicio")),
        "valid_until": _date(payload.get("dataVigenciaFim")),
        "amendment_number": payload.get("numeroRetificacao"),
        "is_empenho": "empenho" in str(_first(payload.get("tipoContrato"), "")).casefold()
        or "empenho" in str(payload.get("numeroContratoEmpenho") or "").casefold(),
        "inventory_record": inventory_record,
    }


def _load_contracts(inventory: dict[str, Any], project_root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    parsed: list[dict[str, Any]] = []
    errors: Counter[str] = Counter()
    candidates = [
        record
        for record in inventory.get("arquivos", [])
        if isinstance(record, dict)
        and record.get("base_id") == STRUCTURED_BASE_ID
        and record.get("resource_kind") == "contrato"
    ]
    for inventory_record in sorted(candidates, key=lambda item: str(item.get("logical_id") or "")):
        if inventory_record.get("status") != "ok":
            errors["registro_nao_ok"] += 1
            continue
        relative_path = inventory_record.get("source_file") or inventory_record.get("path")
        if not relative_path:
            errors["sem_caminho"] += 1
            continue
        source_path = Path(str(relative_path))
        if not source_path.is_absolute():
            source_path = project_root / source_path
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            errors["arquivo_ausente"] += 1
            continue
        except (OSError, json.JSONDecodeError):
            errors["json_ilegivel_ou_invalido"] += 1
            continue
        records = payload if isinstance(payload, list) else [payload]
        for record in records:
            if isinstance(record, dict):
                parsed.append(_contract_view(record, inventory_record))
            else:
                errors["registro_json_nao_objeto"] += 1

    # The PNCP contract control number is the identity. Keep a deterministic
    # first occurrence for metrics while reporting duplicates separately.
    unique: dict[str, dict[str, Any]] = {}
    missing_id = 0
    duplicates = 0
    for row in parsed:
        identity = row.get("contract_id")
        if not identity:
            missing_id += 1
            identity = str(row["inventory_record"].get("logical_id") or row["inventory_record"].get("artifact_id"))
        if identity in unique:
            duplicates += 1
            continue
        unique[identity] = row
    errors["contratos_sem_id_pncp"] = missing_id
    errors["duplicatas_descartadas_para_metricas"] = duplicates
    return list(unique.values()), dict(errors)


def _count(values: Iterable[Any], limit: int) -> list[dict[str, Any]]:
    counter = Counter(str(value).strip() for value in values if value not in (None, ""))
    return [{"valor": value, "contratos": count} for value, count in counter.most_common(limit)]


def _number(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _numeric_summary(values: Iterable[Decimal | None]) -> dict[str, Any]:
    parsed = sorted(value for value in values if value is not None)
    if not parsed:
        return {
            "n": 0,
            "ausentes": 0,
            "zeros": 0,
            "negativos": 0,
            "soma": None,
            "media": None,
            "mediana": None,
            "min": None,
            "max": None,
        }
    return {
        "n": len(parsed),
        "ausentes": 0,
        "zeros": sum(value == 0 for value in parsed),
        "negativos": sum(value < 0 for value in parsed),
        "soma": _number(sum(parsed, Decimal(0))),
        "media": _number(sum(parsed, Decimal(0)) / Decimal(len(parsed))),
        "mediana": _number(Decimal(str(statistics.median(parsed)))),
        "min": _number(parsed[0]),
        "max": _number(parsed[-1]),
    }


def _with_missing_count(summary: dict[str, Any], total: int) -> dict[str, Any]:
    summary["ausentes"] = max(0, total - summary["n"])
    return summary


def _grouped_amounts(rows: list[dict[str, Any]], dimension: str, limit: int) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = row.get(dimension)
        if key not in (None, ""):
            groups[str(key)].append(row)
    ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:limit]
    result = []
    for key, group in ordered:
        summary = _numeric_summary(row.get("value_global") for row in group)
        _with_missing_count(summary, len(group))
        result.append({"dimensao": key, "contratos": len(group), "valor_global": summary})
    return result


def _aggregate_link_coverage(inventory: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    purchase_ids = {row["purchase_id"] for row in rows if row.get("purchase_id")}
    contract_ids = {row["contract_id"] for row in rows if row.get("contract_id")}
    linked_artifacts: dict[str, set[str]] = defaultdict(set)
    linked_ok: dict[str, set[str]] = defaultdict(set)
    linked_purchases: dict[str, set[str]] = defaultdict(set)
    for artifact in inventory.get("arquivos", []):
        if not isinstance(artifact, dict) or artifact.get("base_id") != AGGREGATE_BASE_ID:
            continue
        kind = str(artifact.get("resource_kind") or "desconhecido")
        source = _mapping(artifact.get("source_ref"))
        key = _mapping(source.get("key"))
        contract_id = _first(key.get("numeroControlePNCP"), key.get("numeroControlePncp"))
        purchase_id = _first(key.get("numeroControlePNCPCompra"), key.get("numeroControlePncpCompra"))
        artifact_id = str(artifact.get("artifact_id") or artifact.get("logical_id") or "")
        if contract_id and str(contract_id) in contract_ids:
            linked_artifacts[kind].add(artifact_id)
            if artifact.get("status") == "ok":
                linked_ok[kind].add(artifact_id)
            if purchase_id:
                linked_purchases[kind].add(str(purchase_id))
    return {
        "contratos_com_compra_vinculada": sum(bool(row.get("purchase_id")) for row in rows),
        "contratos_sem_compra_vinculada": sum(not row.get("purchase_id") for row in rows),
        "compras_distintas_vinculadas_a_contratos": len(purchase_ids),
        "compras_vinculadas_com_itens_na_v3": len(linked_purchases.get("itens", set())),
        "compras_vinculadas_com_resultados_na_v3": len(linked_purchases.get("resultado_item", set())),
        "artefatos_agregados_por_tipo": {
            kind: {"artefatos": len(ids), "ok": len(linked_ok.get(kind, set()))}
            for kind, ids in sorted(linked_artifacts.items())
        },
    }


def analyze(inventory_path: Path, project_root: Path, top: int = 20) -> dict[str, Any]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    structured_base = next(
        (base for base in inventory.get("por_base", []) if base.get("base_id") == STRUCTURED_BASE_ID),
        {},
    )
    rows, read_errors = _load_contracts(inventory, project_root)
    total = len(rows)
    date_coverage = _date_coverage(rows)

    return {
        "analysis": "structured_corpus_profile",
        "inventory": {
            "path": str(inventory_path),
            "version": inventory.get("version"),
            "coletado_em": inventory.get("coletado_em"),
            "completo": inventory.get("completo"),
            "manifest_hash": inventory.get("manifest_hash"),
            "base_status": structured_base.get("status"),
            "volume_alvo": structured_base.get("volume_alvo"),
            "volume_obtido": structured_base.get("volume_obtido"),
            "artifacts": structured_base.get("arquivos"),
            "inventory_failures": structured_base.get("failure_count"),
        },
        "population": {
            "contract_records_unique": total,
            "signed_in_2025": date_coverage["signature_years"].get("2025", 0),
            "distinct_contract_ids": len({row["contract_id"] for row in rows if row.get("contract_id")}),
            "distinct_purchase_ids": len({row["purchase_id"] for row in rows if row.get("purchase_id")}),
            "contract_ids_missing": read_errors.get("contratos_sem_id_pncp", 0),
            "duplicate_rows_omitted": read_errors.get("duplicatas_descartadas_para_metricas", 0),
            "empenho_like_records": sum(bool(row.get("is_empenho")) for row in rows),
        },
        "source_read_errors": {key: value for key, value in sorted(read_errors.items()) if value},
        "field_coverage": _field_coverage(rows),
        "date_coverage": date_coverage,
        "geographic_and_administrative_coverage": {
            "ufs": _count((row.get("uf") for row in rows), top),
            "municipalities": _count((row.get("municipality") for row in rows), top),
            "spheres": _count((row.get("sphere") for row in rows), top),
            "organs": _count((row.get("org_name") or row.get("org_cnpj") for row in rows), top),
            "distinct_org_cnpjs": len({row.get("org_cnpj") for row in rows if row.get("org_cnpj")}),
            "distinct_units": len({(row.get("org_cnpj"), row.get("unit_code")) for row in rows if row.get("unit_code")}),
            "distinct_municipalities": len({row.get("municipality") for row in rows if row.get("municipality")}),
            "distinct_spheres": len({row.get("sphere") for row in rows if row.get("sphere")}),
            "contracts_located_in_rj": sum(str(row.get("uf") or "").upper() == "RJ" for row in rows),
        },
        "distributions": {
            "modalities": _count((row.get("modality") for row in rows), top),
            "process_categories": _count((row.get("process_category") for row in rows), top),
            "contract_types": _count((row.get("contract_type") for row in rows), top),
            "suppliers": _count((row.get("supplier_name") or row.get("supplier_cnpj") for row in rows), top),
            "distinct_supplier_ids": len({row.get("supplier_cnpj") for row in rows if row.get("supplier_cnpj")}),
        },
        "contract_values": _contract_values(rows),
        "object_keyword_hits_non_exclusive": _object_keyword_hits(rows),
        "contract_value_by": {
            "sphere": _grouped_amounts(rows, "sphere", top),
            "municipality": _grouped_amounts(rows, "municipality", top),
            "modality": _grouped_amounts(rows, "modality", top),
            "contract_type": _grouped_amounts(rows, "contract_type", top),
            "process_category": _grouped_amounts(rows, "process_category", top),
            "signature_month": _signature_month_values(rows),
        },
        "aggregate_link_coverage_in_current_inventory": _aggregate_link_coverage(inventory, rows),
        "interpretation_limits": [
            "Os registros analisados são contratos; a contratação vinculada é outra unidade lógica.",
            "As distribuições refletem somente o conteúdo desta versão e não provam cobertura censitária do RJ.",
            "ValorGlobal do contrato não é valor unitário homologado nem pagamento efetivo.",
            "Menções no objeto são triagem textual; itens e editais precisam confirmar produto e unidade comparáveis.",
        ],
    }


def _date_coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Contratos por ano e mês de assinatura e de publicação, e as datas que não batem."""
    signature_years = Counter(str(row["signature_date"].year) for row in rows if row.get("signature_date"))
    publication_years = Counter(str(row["publication_date"].year) for row in rows if row.get("publication_date"))
    signature_months = Counter(row["signature_date"].strftime("%Y-%m") for row in rows if row.get("signature_date"))
    publication_months = Counter(row["publication_date"].strftime("%Y-%m") for row in rows if row.get("publication_date"))
    mismatch_year = sum(
        row.get("signature_date") is not None
        and row.get("contract_year") not in (None, "")
        and str(row.get("signature_date").year) != str(row.get("contract_year"))
        for row in rows
    )
    return {
        "signature_years": dict(sorted(signature_years.items())),
        "publication_years": dict(sorted(publication_years.items())),
        "signature_months": dict(sorted(signature_months.items())),
        "publication_months": dict(sorted(publication_months.items())),
        "contract_year_differs_from_signature_year": mismatch_year,
        "signed_2025_but_published_before_2025": sum(
            row.get("signature_date") is not None
            and row["signature_date"].year == 2025
            and row.get("publication_date") is not None
            and row["publication_date"].year < 2025
            for row in rows
        ),
    }


def _field_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Quantos contratos trazem cada campo do PNCP preenchido."""
    field_values = {
        "dataAssinatura": [row.get("signature_date") for row in rows],
        "dataPublicacaoPncp": [row.get("publication_date") for row in rows],
        "dataVigenciaInicio": [row.get("valid_from") for row in rows],
        "dataVigenciaFim": [row.get("valid_until") for row in rows],
        "numeroControlePNCP": [row.get("contract_id") for row in rows],
        "numeroControlePncpCompra": [row.get("purchase_id") for row in rows],
        "orgaoEntidade.cnpj": [row.get("org_cnpj") for row in rows],
        "unidadeOrgao.codigoUnidade": [row.get("unit_code") for row in rows],
        "unidadeOrgao.codigoIbge": [row.get("ibge_code") for row in rows],
        "unidadeOrgao.municipioNome": [row.get("municipality") for row in rows],
        "unidadeOrgao.ufSigla": [row.get("uf") for row in rows],
        "orgaoEntidade.esferaId": [row.get("sphere") for row in rows],
        "niFornecedor": [row.get("supplier_cnpj") for row in rows],
        "nomeRazaoSocialFornecedor": [row.get("supplier_name") for row in rows],
        "objetoContrato": [row.get("object") for row in rows],
        "modalidade": [row.get("modality") for row in rows],
        "valorGlobal": [row.get("value_global") for row in rows],
    }
    return {
        name: {"preenchidos": sum(value not in (None, "") for value in values), "total": len(rows)}
        for name, values in field_values.items()
    }


def _object_keyword_hits(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Contratos cujo objeto menciona cada termo de interesse (um contrato pode contar em vários)."""
    focus_terms = {
        "arroz": re.compile(r"\barroz\b", re.I),
        "ambulancia": re.compile(r"ambul[aâ]ncia", re.I),
        "tecnologia": re.compile(r"tecnologia|computador|notebook|software|inform[aá]tica", re.I),
        "manutencao": re.compile(r"manuten[cç][aã]o", re.I),
    }
    return {
        term: sum(bool(pattern.search(row.get("object") or "")) for row in rows)
        for term, pattern in focus_terms.items()
    }


def _contract_values(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Resumo numérico de cada campo de valor, com os contratos sem o valor."""
    value_fields = {
        "valorGlobal": [row.get("value_global") for row in rows],
        "valorInicial": [row.get("value_initial") for row in rows],
        "valorParcela": [row.get("value_installment") for row in rows],
        "valorAcumulado": [row.get("value_accumulated") for row in rows],
    }
    return {
        field: _with_missing_count(_numeric_summary(field_data), len(rows))
        for field, field_data in value_fields.items()
    }


def _signature_month_values(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Valor global dos contratos por mês de assinatura."""
    by_signature_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("signature_date"):
            by_signature_month[row["signature_date"].strftime("%Y-%m")].append(row)
    signature_month_value = []
    for month, group in sorted(by_signature_month.items()):
        summary = _numeric_summary(row.get("value_global") for row in group)
        _with_missing_count(summary, len(group))
        signature_month_value.append({"mes_assinatura": month, "contratos": len(group), "valor_global": summary})
    return signature_month_value


def _markdown(report: dict[str, Any]) -> str:
    population = report["population"]
    inventory = report["inventory"]
    lines = [
        f"# Perfil da base Estruturada — {inventory.get('version')}",
        "",
        f"- Inventário completo: **{inventory.get('completo')}**; status da base: **{inventory.get('base_status')}**",
        f"- Contratos únicos lidos: **{population['contract_records_unique']}**; assinados em 2025: **{population['signed_in_2025']}**",
        f"- IDs PNCP distintos: **{population['distinct_contract_ids']}**; compras vinculadas distintas: **{population['distinct_purchase_ids']}**",
        f"- Órgãos/CNPJs: **{report['geographic_and_administrative_coverage']['distinct_org_cnpjs']}**; unidades: **{report['geographic_and_administrative_coverage']['distinct_units']}**",
        "",
        "## Datas",
        "",
        f"- Assinatura por ano: `{json.dumps(report['date_coverage']['signature_years'], ensure_ascii=False)}`",
        f"- Publicação por ano: `{json.dumps(report['date_coverage']['publication_years'], ensure_ascii=False)}`",
        f"- Assinados em 2025 e publicados antes de 2025: **{report['date_coverage']['signed_2025_but_published_before_2025']}**",
        f"- Ano do contrato difere do ano de assinatura: **{report['date_coverage']['contract_year_differs_from_signature_year']}**",
        "",
        "## Cobertura para cruzamentos",
        "",
        f"- Contratos com ID da compra vinculada: **{report['aggregate_link_coverage_in_current_inventory']['contratos_com_compra_vinculada']}**",
        f"- Compras com itens na v3: **{report['aggregate_link_coverage_in_current_inventory']['compras_vinculadas_com_itens_na_v3']}**",
        f"- Compras com resultados na v3: **{report['aggregate_link_coverage_in_current_inventory']['compras_vinculadas_com_resultados_na_v3']}**",
        "",
        "## Cobertura de campos",
        "",
        "| Campo | Preenchidos | Total |",
        "|---|---:|---:|",
    ]
    for field, value in report["field_coverage"].items():
        lines.append(f"| `{field}` | {value['preenchidos']} | {value['total']} |")
    lines.extend(["", "## Valores globais dos contratos", "", "| Campo | n | Ausentes | Soma | Média | Mediana | Mín. | Máx. |", "|---|---:|---:|---:|---:|---:|---:|---:|"])
    for field, value in report["contract_values"].items():
        lines.append(
            f"| `{field}` | {value['n']} | {value['ausentes']} | {value['soma']} | {value['media']} | {value['mediana']} | {value['min']} | {value['max']} |"
        )
    lines.extend(["", "## Distribuição por modalidade", "", "| Modalidade | Contratos | Soma do valor global | Média | Mediana |", "|---|---:|---:|---:|---:|"])
    for item in report["contract_value_by"]["modality"]:
        amount = item["valor_global"]
        lines.append(f"| {item['dimensao']} | {item['contratos']} | {amount['soma']} | {amount['media']} | {amount['mediana']} |")
    lines.extend(["", "## Pistas de objeto para perguntas do cenário", "", "As contagens são não exclusivas e servem apenas para localizar candidatos; não substituem a análise de itens e editais.", ""])
    lines.extend(f"- {term}: {count} contratos com menção no objeto" for term, count in report["object_keyword_hits_non_exclusive"].items())
    lines.extend(["", "## Limites de interpretação", ""])
    lines.extend(f"- {item}" for item in report["interpretation_limits"])
    if report["source_read_errors"]:
        lines.extend(["", "## Erros ao ler registros", ""])
        lines.extend(f"- {key}: {count}" for key, count in report["source_read_errors"].items())
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analisa a cobertura da base contratos_estruturado de um inventário existente.")
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY, help="Inventário JSON (padrão: data/inventory/v3.json).")
    parser.add_argument("--output", type=Path, help="Grava o relatório neste arquivo; sem a opção, imprime no terminal.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown", help="Formato do relatório (padrão: markdown).")
    parser.add_argument("--top", type=int, default=20, help="Máximo de categorias por distribuição (padrão: 20).")
    args = parser.parse_args(argv)
    if args.top < 1:
        parser.error("--top precisa ser maior que zero")
    inventory_path = args.inventory if args.inventory.is_absolute() else PROJECT_ROOT / args.inventory
    try:
        report = analyze(inventory_path, PROJECT_ROOT, args.top)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n" if args.format == "json" else _markdown(report)
        if args.output:
            output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"erro: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
