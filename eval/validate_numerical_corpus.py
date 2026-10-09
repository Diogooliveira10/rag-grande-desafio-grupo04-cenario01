"""Independent raw-corpus oracle and live CLI/browser numerical validation.

Run from the repository root. Evidence is stored locally in data/runs/numerical-validation.
No production parsing or SQL is used to calculate expected contract values.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/runs/numerical-validation"


def save(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def money(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN), "f")


def oracle():
    inventory = json.loads((ROOT / "data/inventory/v6.json").read_text(encoding="utf-8"))
    # Use raw files referenced by the active normalized snapshot; never sum chunks.
    sources = {}
    for line in (ROOT / "data/normalized/documents.jsonl").open(encoding="utf-8"):
        c = json.loads(line)
        if c["base_id"] in {"contratos_estruturado", "pca_agregado"} and c.get("doc_type") == "json":
            sources[c["source_file"]] = c["base_id"]
    records = {}
    duplicates = []
    for source, base in sources.items():
        r = json.loads((ROOT / source).read_text(encoding="utf-8"), parse_float=Decimal)
        if not isinstance(r, dict) or not r.get("numeroControlePNCP"):
            continue
        n = r["numeroControlePNCP"]
        if n in records:
            duplicates.append(n)
            continue
        records[n] = dict(record=r, source=source, base=base)
    contracts = [v for n, v in records.items() if "-2-" in n]

    def stats(rows):
        values = [Decimal(str(v["record"]["valorGlobal"])) for v in rows if v["record"].get("valorGlobal") is not None]
        informed = [v for v in values if v >= 1]
        return {"count": len(rows), "values_count": len(informed), "missing_or_symbolic_count": len(rows)-len(informed),
                "literal_sum": money(sum(values, Decimal(0))),
                "sum": money(sum(informed, Decimal(0))),
                "mean": money(sum(informed, Decimal(0))/len(informed)) if informed else None,
                "median": money(statistics.median(informed)) if informed else None,
                "min": money(min(informed)) if informed else None,
                "max": money(max(informed)) if informed else None}

    signed = [v for v in contracts if str(v["record"].get("dataAssinatura", "")).startswith("2025-")]
    published = [v for v in contracts if str(v["record"].get("dataPublicacaoPncp", "")).startswith("2025-")]
    spheres = {"F": "federal", "E": "estadual", "M": "municipal"}
    by_sphere = defaultdict(list)
    by_month = defaultdict(list)
    for v in signed:
        by_sphere[spheres.get(v["record"]["orgaoEntidade"]["esferaId"], "other")].append(v)
        by_month[v["record"]["dataAssinatura"][:7]].append(v)
    top = sorted(signed, key=lambda v: Decimal(str(v["record"].get("valorGlobal") or 0)), reverse=True)[:5]
    result = {"snapshot": inventory["version"], "scope": "corpus sample, not all RJ contracts", "sources_read": len(sources),
              "documents_sha256": hashlib.sha256((ROOT / "data/normalized/documents.jsonl").read_bytes()).hexdigest(),
              "duplicate_ids": duplicates, "all_contracts": stats(contracts), "signed_2025": stats(signed),
              "published_2025": stats(published), "by_signature_year": dict(Counter(str(v["record"].get("dataAssinatura"))[:4] for v in contracts)),
              "signed_2025_by_sphere": {k: stats(v) for k, v in by_sphere.items()},
              "signed_2025_by_month": {k: stats(v) for k, v in sorted(by_month.items())},
              "top5": [{"pncp": v["record"]["numeroControlePNCP"], "value": money(Decimal(str(v["record"]["valorGlobal"]))),
                        "orgao": v["record"]["orgaoEntidade"]["razaoSocial"], "source": v["source"]} for v in top]}
    save("oracle.json", result)
    cases = [
        {"id": "total", "query": "Qual o valor total de todos os contratos de 2025 no corpus?", "expected": result["signed_2025"]},
        {"id": "count", "query": "Quantos contratos foram assinados em 2025 no corpus?", "expected": {"count": len(signed)}},
        {"id": "mean", "query": "Qual o valor médio dos contratos assinados em 2025 no corpus?", "expected": {"mean": result["signed_2025"]["mean"]}},
        {"id": "median", "query": "Qual a mediana do valor dos contratos assinados em 2025 no corpus?", "expected": {"median": result["signed_2025"]["median"]}},
        {"id": "published", "query": "Qual o valor total dos contratos publicados em 2025 no corpus?", "expected": result["published_2025"]},
        {"id": "municipal", "query": "Qual o valor total dos contratos municipais assinados em 2025 no corpus?", "expected": result["signed_2025_by_sphere"]["municipal"]},
        {"id": "top5", "query": "Quais os cinco maiores contratos por valor assinados em 2025 no corpus?", "expected": result["top5"]},
        {"id": "threshold", "query": "Quantos contratos assinados em 2025 têm valor acima de R$ 1 milhão no corpus?", "expected": {"count": sum(Decimal(str(v["record"].get("valorGlobal") or 0)) > 1000000 for v in signed)}},
        {"id": "monthly", "query": "Qual o valor total dos contratos assinados em 2025 por mês no corpus?", "expected": result["signed_2025_by_month"]},
        {"id": "empty_year", "query": "Qual o valor total dos contratos assinados em 2024 no corpus?", "expected": stats([v for v in contracts if str(v["record"].get("dataAssinatura", "")).startswith("2024-")])},
    ]
    save("cases.json", cases)
    print(json.dumps({k: result[k] for k in ("sources_read", "all_contracts", "signed_2025", "published_2025", "signed_2025_by_sphere", "top5")}, ensure_ascii=False, indent=2))


def cli(only=None):
    results = []
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "RERANK_ENABLED": "false", "LLM_PROVIDER_MODE": "openrouter"}
    for c in json.loads((OUT / "cases.json").read_text(encoding="utf-8")):
        if only and c["id"] not in only:
            continue
        start = time.monotonic()
        try:
            proc = subprocess.run([sys.executable, "-m", "app.cli", "--openrouter-only", "perguntar", c["query"]],
                                  cwd=ROOT, env=env, capture_output=True, encoding="utf-8", timeout=300)
            (OUT / f"cli-{c['id']}.txt").write_text(proc.stdout + "\nSTDERR:\n" + proc.stderr, encoding="utf-8")
            payload = json.JSONDecoder().raw_decode(proc.stdout[proc.stdout.index("{"):])[0] if "{" in proc.stdout else {}
            r = {**c, "seconds": round(time.monotonic()-start, 2), "exit_code": proc.returncode, "response": payload}
        except Exception as e:
            r = {**c, "error": str(e)}
        results.append(r)
        save("cli-results.json", results)
        print(c["id"], json.dumps(r.get("response", r), ensure_ascii=False), flush=True)


def check():
    """Compare displayed numbers with the independent raw oracle (not model grading)."""
    import re
    def brl(value):
        return "R$ " + f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    validations = []
    for mode in ("cli", "browser"):
        file = OUT / f"{mode}-results.json"
        if not file.exists():
            continue
        data = json.loads(file.read_text(encoding="utf-8"))
        results = data if isinstance(data, list) else data["results"]
        for row in results:
            answer = row.get("response", {}).get("answer", "")
            expected = row["expected"]
            required = []
            if row["id"] == "top5":
                required = [brl(v["value"]) for v in expected]
            elif row["id"] == "monthly":
                required = [brl(v["literal_sum"]) for v in expected.values()]
            elif "literal_sum" in expected and expected["count"]:
                required = [brl(expected["literal_sum"])]
            elif row["id"] in {"mean", "median"}:
                required = [brl(next(iter(expected.values())))]
            if "count" in expected:
                required.append(f"{expected['count']} registros")
            missing = [v for v in required if v not in answer]
            errors = [row["error"]] if row.get("error") else []
            if row.get("response", {}).get("is_refusal"):
                errors.append("unexpected refusal")
            if mode == "browser" and not row.get("answer_rendered"):
                errors.append("answer not rendered")
            if row["id"] == "top5":
                positions = [answer.find(v) for v in required]
                if positions != sorted(positions):
                    errors.append("ranking order differs")
            if row["id"] in {"mean", "median"}:
                label = "média" if row["id"] == "mean" else "mediana"
                if f"{label} {required[0]}" not in answer:
                    errors.append("value associated with wrong statistic")
            if row["id"] == "monthly":
                for month, values in expected.items():
                    if not re.search(re.escape(month) + r":[^\n]*" + re.escape("total " + brl(values["literal_sum"])), answer):
                        errors.append("wrong month/value association: " + month)
            if row["id"] == "empty_year" and "R$ 0,00" in answer:
                errors.append("empty sample presented as zero value")
            if mode == "browser":
                trace_file = OUT / f"trace-{row['id']}.json"
                if trace_file.exists():
                    trace = json.loads(trace_file.read_text(encoding="utf-8"))
                    stages = trace.get("etapas", [])
                    generated = next((e.get("detalhes", {}) for e in stages if e["stage"] == "geracao"), {})
                    sql_ids = {i for e in stages if e["stage"] == "consulta_sql" for i in e.get("detalhes", {}).get("chunk_ids", [])}
                    cited = {s["chunk_id"] for s in row.get("response", {}).get("sources_used", [])}
                    if generated.get("metodo") != "sql_deterministico" or generated.get("llm_called") is not False:
                        errors.append("numerical result sent to model")
                    if not trace.get("completo") or not cited or not cited <= sql_ids:
                        errors.append("incomplete trace or invalid SQL citations")
                else:
                    errors.append("missing trace")
            validations.append({"mode": mode, "case": row["id"], "pass": not missing and not errors,
                                "missing": missing, "errors": errors})
    summary = {"checks": validations, "passed": sum(v["pass"] for v in validations), "total": len(validations)}
    save("validation.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return all(v["pass"] for v in validations) and len(validations) == 20


def browser(only=None):
    from playwright.sync_api import sync_playwright
    results, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        for c in json.loads((OUT / "cases.json").read_text(encoding="utf-8")):
            if only and c["id"] not in only:
                continue
            page.goto("http://localhost:5173/", wait_until="networkidle")
            new = page.get_by_role("button", name="Iniciar nova conversa")
            if new.count():
                new.first.click()
            textarea = page.locator("textarea").first
            textarea.fill(c["query"])
            start = time.monotonic()
            try:
                with page.expect_response(lambda r: "/api/v1/query" in r.url and r.request.method == "POST", timeout=300000) as response:
                    page.get_by_role("button", name="Enviar consulta", exact=True).click()
                http = response.value
                payload = http.json()
                page.wait_for_timeout(700)
                text = page.locator("body").inner_text()
                (OUT / f"browser-{c['id']}.txt").write_text(text, encoding="utf-8")
                page.screenshot(path=str(OUT / f"browser-{c['id']}.png"), full_page=True)
                r = {**c, "seconds": round(time.monotonic()-start, 2), "status": http.status, "response": payload,
                     "answer_rendered": " ".join(payload.get("answer", "").split()) in " ".join(text.split()),
                     "request": http.request.post_data_json}
                if payload.get("query_id") and payload.get("session_id"):
                    trace = page.request.get(f"http://localhost:8080/api/v1/trace/{payload['query_id']}", headers={"X-Session-Id": payload["session_id"]})
                    save(f"trace-{c['id']}.json", trace.json())
            except Exception as e:
                r = {**c, "error": str(e)}
            results.append(r)
            save("browser-results.json", {"results": results, "browser_errors": errors})
            print(c["id"], json.dumps(r.get("response", r), ensure_ascii=False), flush=True)
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["oracle", "cli", "browser", "check"])
    parser.add_argument("--only", nargs="+")
    args = parser.parse_args()
    if args.mode == "oracle":
        oracle()
    elif args.mode == "check":
        raise SystemExit(0 if check() else 1)
    else:
        globals()[args.mode](args.only)
