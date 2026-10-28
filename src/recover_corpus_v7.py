"""Recuperação opt-in do v7. Só cria destinos novos; nunca promove artefatos.

baseline: cópia verificável da v6 ativa e hashes dos snapshots adquiridos.
staging: reclassificação offline, SQL separado, proveniência e fila de vínculos.
A base SQL de staging NÃO é o banco ativo, nem prova de censo completo.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
from typing import Any, Iterable

from src.collection_policy import PoliticaColeta, chave_unidade, compra_vinculada, nome_instrumento, tipo_instrumento, uf_unidade
from src.index import file_sha256
from src.structured_db import _COLUNAS_CONTRATACOES, _linha_contratacao

ROOT = Path(__file__).resolve().parents[1]


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gravar_json(path: Path, value: Any) -> None:
    temporario = path.with_name(path.name + ".provisorio")
    temporario.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporario.replace(path)


def destino_novo(destino: Path, fontes: Iterable[Path] = ()) -> Path:
    destino = destino.resolve()
    protegidos = [ROOT / "index", ROOT / "data/normalized", ROOT / "data/structured", ROOT / "data/v7_direto", ROOT / "data/v7_direto_atas"]
    for fonte in [*protegidos, *fontes]:
        fonte = fonte.resolve()
        if destino == fonte or destino.is_relative_to(fonte) or fonte.is_relative_to(destino):
            raise ValueError(f"destino se sobrepõe a uma fonte/protegido: {fonte}")
    if destino.exists():
        raise FileExistsError(f"destino já existe: {destino}; escolha uma nova execução")
    destino.mkdir(parents=True)
    return destino


def capturar_baseline(destino: Path, *, root: Path = ROOT) -> dict[str, Any]:
    destino = destino_novo(destino)
    ativos = [root / "index", root / "data/normalized/v6", root / "data/normalized/documents.jsonl", root / "data/structured/base_estruturada.sqlite", root / "data/config/orgaos_corpus.json", root / "corpus_manifest.yaml"]
    paths = sorted({f for p in ativos for f in (p.rglob("*") if p.is_dir() else [p]) if f.is_file()})
    registros = []
    for p in paths:
        rel = p.relative_to(root)
        antes = file_sha256(p)
        copia = destino / "rollback" / rel
        copia.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, copia)
        depois = file_sha256(p)
        if antes != depois or file_sha256(copia) != antes:
            raise RuntimeError(f"fonte mudou durante backup: {p}")
        registros.append({"path": rel.as_posix(), "sha256": antes, "bytes": p.stat().st_size})
    adquiridos = []
    for pasta in ["data/v7_direto", "data/v7_direto_atas"]:
        for sub in ["documents.jsonl", "estado_coleta.json", "base_estruturada.sqlite", "index/manifest.json"]:
            p = root / pasta / sub
            if p.exists():
                adquiridos.append({"path": p.relative_to(root).as_posix(), "sha256": file_sha256(p), "bytes": p.stat().st_size})
    status = subprocess.run(["git", "status", "--short", "--branch"], cwd=root, capture_output=True, text=True, check=True).stdout
    patch = subprocess.run(["git", "diff", "--binary"], cwd=root, capture_output=True, check=True).stdout
    (destino / "alteracoes_preexistentes.patch").write_bytes(patch)
    result = {"gerado_em": agora(), "root": str(root.resolve()), "ativos": registros, "adquiridos": adquiridos, "git_status": status, "rollback_copias_verificadas": True, "rollback_restauracao_ensaiada": False, "promocao_autorizada": False}
    gravar_json(destino / "baseline.json", result)
    return result


def verificar_baseline(baseline: Path) -> dict[str, Any]:
    d = json.loads((baseline / "baseline.json").read_text(encoding="utf-8"))
    root = Path(d["root"])
    erros = []
    for item in d["ativos"]:
        for label, path in [("ativo", root / item["path"]), ("rollback", baseline / "rollback" / item["path"])]:
            if not path.exists() or file_sha256(path) != item["sha256"]:
                erros.append({"tipo": label, "path": str(path)})
    for item in d["adquiridos"]:
        p = root / item["path"]
        if not p.exists() or file_sha256(p) != item["sha256"]:
            erros.append({"tipo": "adquirido", "path": str(p)})
    return {"verificado_em": agora(), "integro": not erros, "erros": erros, "promocao_autorizada": False}


def ensaiar_rollback(baseline: Path, destino: Path) -> dict[str, Any]:
    """Restaura cópias só em sandbox e abre SQLite/FAISS. Não sobe a API."""
    integridade = verificar_baseline(baseline)
    if not integridade["integro"]:
        raise ValueError("baseline/ativos divergiram; não ensaiar como se estivesse íntegro")
    destino = destino_novo(destino, [baseline])
    dados = json.loads((baseline / "baseline.json").read_text(encoding="utf-8"))
    shutil.copytree(baseline / "rollback", destino, dirs_exist_ok=True)
    for item in dados["ativos"]:
        if file_sha256(destino / item["path"]) != item["sha256"]:
            raise ValueError("restauração não confere com a baseline")
    banco = destino / "data/structured/base_estruturada.sqlite"
    with sqlite3.connect(banco.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite restaurado inválido")
        sql_hash = db.execute("SELECT valor FROM meta WHERE chave='source_sha256'").fetchone()[0]
        if sql_hash != file_sha256(destino / "data/normalized/documents.jsonl"):
            raise ValueError("SQL restaurado não corresponde aos documentos ativos")
        linhas_sql = db.execute("SELECT count(*) FROM contratacoes").fetchone()[0]
    import faiss
    manifest = json.loads((destino / "index/manifest.json").read_text(encoding="utf-8"))
    total = 0
    for base in manifest["bases"]:
        folder = destino / "index" / base["base_id"]
        indice = faiss.read_index(str(folder / base["faiss_index"]))
        linhas = sum(1 for _ in _linhas(folder / "vectors.jsonl"))
        if indice.ntotal != base["total_chunks"] or indice.d != base["dimension"] or linhas != indice.ntotal:
            raise ValueError(f"FAISS restaurado inconsistente: {base['base_id']}")
        total += linhas
    result = {"gerado_em": agora(), "copia_restaurada_verificada": True, "sqlite_integrity_check": "ok", "linhas_sql": linhas_sql, "vetores_faiss": total, "aplicacao_ensaiada": False, "promocao_autorizada": False}
    gravar_json(destino / "ensaio_rollback.json", result)
    return result


def _linhas(path: Path):
    with path.open(encoding="utf-8") as f:
        for n, linha in enumerate(f, 1):
            if linha.strip():
                yield n, json.loads(linha)


def construir_staging(fontes: list[Path], destino: Path, politica: PoliticaColeta | None = None, *, territorio_atas: Path | None = None) -> dict[str, Any]:
    politica = politica or PoliticaColeta()
    # Validar antes de criar o destino; nunca produzir um sucesso vazio por fonte ausente.
    entradas = [*fontes, *([territorio_atas] if territorio_atas else [])]
    for fonte in entradas:
        if not fonte.is_file():
            raise FileNotFoundError(fonte)
    destino = destino_novo(destino, entradas)
    fontes_meta = [{"path": str(p.resolve()), "sha256": file_sha256(p)} for p in entradas]
    laudo_hash = file_sha256(territorio_atas) if territorio_atas else None
    contagens = Counter()
    unidades: dict[tuple[str, str], set[str]] = {}
    orgaos_observados: set[str] = set()
    # Catálogo em disco: deduplicação por identidade, sem carregar ~1 GB em RAM.
    with sqlite3.connect(destino / "catalogo_aquisicao.sqlite") as catalogo:
        _catalogar_fontes(catalogo, fontes, territorio_atas, destino, contagens, unidades, orgaos_observados)
        source_hash = _gravar_staging(catalogo, destino, politica, territorio_atas, laudo_hash, unidades, contagens)
        observadas = [{"cnpj": k[0], "codigoUnidadeAdministrativa": k[1], "ufs_observadas": sorted(v), "origem": "snapshot_local_nao_cadastro_oficial"} for k, v in sorted(unidades.items())]
        gravar_json(destino / "unidades_observadas.json", {"completo": False, "orgaos": sorted(orgaos_observados), "unidades": observadas})
    for fonte in fontes_meta:
        if file_sha256(Path(fonte["path"])) != fonte["sha256"]:
            raise RuntimeError("fonte mudou durante a reclassificação; staging inválido")
    report = {"gerado_em": agora(), "fontes": fontes_meta, "policy": asdict(politica), "source_sha256": source_hash, "contagens": dict(contagens), "cobertura_institucional_comprovada": False, "indice_construido": False, "bases_herdadas_incorporadas": False, "golden_executado": False, "territorio_atas_fonte": str(territorio_atas) if territorio_atas else None, "territorio_atas_validade_historica_comprovada": False, "promocao_autorizada": False}
    gravar_json(destino / "relatorio.json", report)
    return report


def _catalogar_fontes(catalogo: sqlite3.Connection, fontes: list[Path], territorio_atas: Path | None, destino: Path,
                      contagens: Counter, unidades: dict[tuple[str, str], set[str]], orgaos_observados: set[str]) -> None:
    """Catálogo das fontes por identidade PNCP: fica a revisão escolhida, e o drift vai para drift.jsonl."""
    catalogo.execute("CREATE TABLE territorio_atas (id TEXT PRIMARY KEY, evidencia TEXT NOT NULL)")
    if territorio_atas:
        for _, e in _linhas(territorio_atas):
            catalogo.execute("INSERT INTO territorio_atas VALUES (?,?)", [e["ata"], json.dumps(e, ensure_ascii=False)])
    catalogo.execute("CREATE TABLE registros (id TEXT PRIMARY KEY, chunk TEXT NOT NULL, payload TEXT NOT NULL, revisao TEXT NOT NULL, fonte TEXT NOT NULL, linha INTEGER NOT NULL)")
    for fonte in fontes:
        for n, chunk in _linhas(fonte):
            _catalogar_registro(catalogo, fonte, n, chunk, destino, contagens, unidades, orgaos_observados)
    catalogo.commit()
    # Só compras tipo 1: evidência de vínculo, inclusive se fora do recorte principal.
    catalogo.execute("CREATE VIEW compras AS SELECT * FROM registros WHERE id GLOB '*-1-*' AND json_extract(payload, '$.numeroControlePNCPAta') IS NULL")


def _catalogar_registro(catalogo: sqlite3.Connection, fonte: Path, n: int, chunk: dict[str, Any], destino: Path,
                        contagens: Counter, unidades: dict[tuple[str, str], set[str]], orgaos_observados: set[str]) -> None:
    payload = json.loads(chunk["text"])
    if not isinstance(payload, dict):
        raise ValueError(f"registro PNCP não é objeto: {fonte}:{n}")
    identificador = payload.get("numeroControlePNCPAta") or payload.get("numeroControlePNCP")
    if not identificador:
        raise ValueError(f"registro sem identidade PNCP: {fonte}:{n}")
    key, uf = chave_unidade(payload), uf_unidade(payload)
    if key[0]:
        orgaos_observados.add(key[0])
    if all(key):
        unidades.setdefault(key, set())
        if uf:
            unidades[key].add(uf)
    revisao = str(payload.get("dataAtualizacaoGlobal") or payload.get("dataAtualizacao") or chunk.get("coletado_em") or "")
    antigo = catalogo.execute("SELECT revisao, chunk FROM registros WHERE id=?", [identificador]).fetchone()
    if antigo:
        contagens["identidades_repetidas"] += 1
        chunk_antigo = json.loads(antigo[1])
        if chunk_antigo["chunk_id"] != chunk["chunk_id"]:
            contagens["identidades_com_drift"] += 1
            with (destino / "drift.jsonl").open("a", encoding="utf-8") as audit:
                audit.write(json.dumps({"id": identificador, "anterior": chunk_antigo["chunk_id"], "candidato": chunk["chunk_id"], "revisao_anterior": antigo[0], "revisao_candidata": revisao, "fonte_candidata": str(fonte)}, ensure_ascii=False) + "\n")
        auxiliar = (chunk.get("extras") or {}).get("papel") == "referencia_auxiliar"
        antigo_auxiliar = (chunk_antigo.get("extras") or {}).get("papel") == "referencia_auxiliar"
        if (auxiliar and not antigo_auxiliar) or (auxiliar == antigo_auxiliar and revisao < antigo[0]):
            return
    catalogo.execute("INSERT OR REPLACE INTO registros VALUES (?,?,?,?,?,?)", [identificador, json.dumps(chunk, ensure_ascii=False), json.dumps(payload, ensure_ascii=False), revisao, str(fonte), n])
    contagens["linhas_catalogadas"] += 1


def _gravar_staging(catalogo: sqlite3.Connection, destino: Path, politica: PoliticaColeta, territorio_atas: Path | None,
                    laudo_hash: str | None, unidades: dict[tuple[str, str], set[str]], contagens: Counter) -> str:
    """documents.jsonl, quarentena e base SQL do staging, na ordem do catálogo; devolve o hash dos documentos."""
    colunas = dict(_COLUNAS_CONTRATACOES)
    colunas.update({"instrumento_id": "INTEGER", "instrumento_nome": "TEXT", "modalidade_id": "INTEGER", "modalidade_origem": "TEXT", "modalidade_compra_pncp": "TEXT", "categoria_processo_id": "INTEGER", "categoria_processo_nome": "TEXT", "codigo_unidade": "TEXT"})
    with sqlite3.connect(destino / "base_estruturada.sqlite") as banco, (destino / "documents.jsonl").open("w", encoding="utf-8", newline="\n") as docs, (destino / "quarentena.jsonl").open("w", encoding="utf-8", newline="\n") as quarentena:
        banco.execute("CREATE TABLE contratacoes (" + ",".join(k + " " + v.replace("{REAL}", "REAL") for k, v in colunas.items()) + ")")
        banco.execute("CREATE TABLE meta (chave TEXT PRIMARY KEY, valor TEXT)")
        banco.execute("CREATE TABLE vinculos_pendentes (compra TEXT NOT NULL, registro TEXT PRIMARY KEY)")
        names = list(colunas)
        insert = "INSERT INTO contratacoes (" + ",".join(names) + ") VALUES (" + ",".join("?" for _ in names) + ")"
        for identificador, raw_chunk, raw_payload, fonte, n in catalogo.execute("SELECT id,chunk,payload,fonte,linha FROM registros ORDER BY id"):
            chunk, payload = json.loads(raw_chunk), json.loads(raw_payload)
            incluir, motivo, ata, evidencia_territorio = _decidir_inclusao(
                identificador, chunk, payload, politica, unidades, catalogo, territorio_atas)
            if not incluir:
                contagens[motivo] += 1
                quarentena.write(json.dumps({"id": identificador, "chunk_id": chunk["chunk_id"], "motivo": motivo, "fonte": fonte, "linha": n}, ensure_ascii=False) + "\n")
                continue
            _gravar_registro_incluido(identificador, chunk, payload, ata, evidencia_territorio, politica, territorio_atas,
                                      laudo_hash, catalogo, banco, docs, insert, names, contagens)
        docs.flush()
        source_hash = file_sha256(destino / "documents.jsonl")
        banco.executemany("INSERT INTO meta VALUES (?,?)", [("schema_version", "v7-staging-1"), ("source_sha256", source_hash), ("policy", politica.fingerprint()), ("promocao_autorizada", "false")])
        fila = [{"compra": compra, "registros": count} for compra, count in banco.execute("SELECT compra,COUNT(*) FROM vinculos_pendentes GROUP BY compra ORDER BY compra")]
        with (destino / "compras_pendentes.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for item in fila:
                f.write(json.dumps(item) + "\n")
        contagens["compras_pendentes_unicas"] = len(fila)
        contagens["registros_com_vinculo_pendente"] = sum(v["registros"] for v in fila)
    return source_hash


def _decidir_inclusao(identificador: str, chunk: dict[str, Any], payload: dict[str, Any], politica: PoliticaColeta,
                      unidades: dict[tuple[str, str], set[str]], catalogo: sqlite3.Connection,
                      territorio_atas: Path | None) -> tuple[bool, str, bool, dict[str, Any] | None]:
    """(incluir, motivo, é ata, evidência territorial): a política da coleta e, nas atas do ano, o laudo cadastral."""
    ufs = unidades.get(chave_unidade(payload), set())
    resolvida = next(iter(ufs)) if len(ufs) == 1 else ""
    incluir, motivo = politica.decidir(payload, uf_resolvida=resolvida)
    ata = bool(payload.get("numeroControlePNCPAta"))
    evidencia_territorio = None
    if ata and territorio_atas and str(payload.get("anoAta")) == str(politica.ano):
        e = catalogo.execute("SELECT evidencia FROM territorio_atas WHERE id=?", [identificador]).fetchone()
        evidencia_territorio = json.loads(e[0]) if e else None
        if not evidencia_territorio or (str(evidencia_territorio.get("cnpj")), str(evidencia_territorio.get("codigoUnidadeAdministrativa"))) != chave_unidade(payload):
            incluir, motivo = False, "territorio_pendente_ou_laudo_divergente"
        else:
            status, uf = evidencia_territorio.get("situacao"), evidencia_territorio.get("uf_cadastral")
            if status in {"confirmada_RJ", "confirmada_fora_RJ"} and uf:
                if (status == "confirmada_RJ") != (uf == politica.uf):
                    raise ValueError("laudo cadastral contradiz sua própria UF")
                if uf_unidade(payload) and uf_unidade(payload) != uf:
                    incluir, motivo = False, "conflito_territorial_laudo_registro"
                else:
                    incluir, motivo = politica.decidir(payload, uf_resolvida=uf)
            else:
                incluir, motivo = False, "territorio_pendente_laudo"
    if (chunk.get("extras") or {}).get("papel") == "referencia_auxiliar":
        incluir, motivo = False, "referencia_auxiliar_nao_contabilizada"
    return incluir, motivo, ata, evidencia_territorio


def _gravar_registro_incluido(identificador: str, chunk: dict[str, Any], payload: dict[str, Any], ata: bool,
                              evidencia_territorio: dict[str, Any] | None, politica: PoliticaColeta,
                              territorio_atas: Path | None, laudo_hash: str | None, catalogo: sqlite3.Connection,
                              banco: sqlite3.Connection, docs: Any, insert: str, names: list[str],
                              contagens: Counter) -> None:
    """Enriquece só os extras, grava o chunk e, fora as atas, a linha SQL."""
    extras = dict(chunk.get("extras") or {})
    extras.update({"recorte_policy": asdict(politica), "uf": politica.uf,
                   "territorio_origem": "registro_pncp" if uf_unidade(payload) else "unidade_observada_no_snapshot",
                   "codigo_unidade": chave_unidade(payload)[1]})
    if ata and territorio_atas:
        extras["territorio_origem"] = "cadastro_oficial_atual"
        extras["territorio_evidencia"] = {"origem": f"https://pncp.gov.br/api/pncp/v1/orgaos/{chave_unidade(payload)[0]}/unidades", "laudo_sha256": laudo_hash, "codigo_unidade": chave_unidade(payload)[1], "evidencia_temporal": evidencia_territorio.get("evidencia_temporal")}
    contrato = "-2-" in identificador
    tipo = tipo_instrumento(payload) if contrato else ("ata" if ata else "publicacao_compra")
    extras["tipo_instrumento"] = tipo
    referencia = compra_vinculada(payload)
    modalidade, modalidade_id, origem_modalidade = _resolver_modalidade(identificador, payload, referencia, catalogo, banco, extras)
    if modalidade:
        extras.update({"modalidade": modalidade, "modalidade_id": modalidade_id})
        contagens[tipo + ":modalidade:" + str(origem_modalidade)] += 1
    categoria = payload.get("categoriaProcesso") or {}
    if categoria:
        extras["categoria_processo"] = categoria
    # Texto, identidade e hashes originais inalterados: enriquecimento só em extras.
    chunk["extras"] = extras
    docs.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")
    contagens[tipo] += 1
    if not ata:
        row = _linha_contratacao(chunk, payload)
        if row is None:
            raise ValueError(f"registro não carregável no SQL: {identificador}")
        instrumento = payload.get("tipoContrato") or {}
        row.update({"tipo_registro": tipo, "instrumento_id": instrumento.get("id") if isinstance(instrumento, dict) else None, "instrumento_nome": nome_instrumento(payload), "modalidade_id": modalidade_id, "modalidade_origem": origem_modalidade, "modalidade_compra_pncp": referencia if origem_modalidade == "compra_vinculada" else None, "categoria_processo_id": categoria.get("id"), "categoria_processo_nome": categoria.get("nome"), "codigo_unidade": chave_unidade(payload)[1]})
        banco.execute(insert, [row.get(k) for k in names])


def _resolver_modalidade(identificador: str, payload: dict[str, Any], referencia: Any, catalogo: sqlite3.Connection,
                         banco: sqlite3.Connection, extras: dict[str, Any]) -> tuple[Any, Any, str | None]:
    """Modalidade do registro ou, sem ela, a da compra vinculada; sem nenhuma, o vínculo fica pendente."""
    modalidade = payload.get("modalidadeNome")
    modalidade_id = payload.get("modalidadeId")
    origem_modalidade = "fonte_direta" if modalidade else None
    if referencia and not modalidade:
        compra = catalogo.execute("SELECT payload,chunk FROM compras WHERE id=?", [referencia]).fetchone()
        if compra:
            compra_payload, compra_chunk = json.loads(compra[0]), json.loads(compra[1])
            modalidade = compra_payload.get("modalidadeNome")
            modalidade_id = compra_payload.get("modalidadeId")
            if modalidade:
                origem_modalidade = "compra_vinculada"
                extras.update({"modalidade_fonte": origem_modalidade, "modalidade_compra_pncp": referencia, "modalidade_evidencia": {"origem": compra_chunk.get("origem"), "sha256": compra_chunk.get("sha256"), "coletado_em": compra_chunk.get("coletado_em")}})
        if not modalidade:
            banco.execute("INSERT INTO vinculos_pendentes VALUES (?,?)", [referencia, identificador])
    return modalidade, modalidade_id, origem_modalidade


def preparar_alvos(observadas: Path, cadastros: list[Path], destino: Path) -> dict[str, Any]:
    """União de sementes: não perder órgão sem unidade/resultado no snapshot."""
    fontes = [observadas, *cadastros]
    valores = set(json.loads(observadas.read_text(encoding="utf-8")).get("orgaos") or [])
    for path in cadastros:
        dados = json.loads(path.read_text(encoding="utf-8"))
        lista = dados if isinstance(dados, list) else dados.get("cnpjs") or dados.get("orgaos") or []
        valores.update(str(v) for v in lista)
    if not valores or any(not re.fullmatch(r"\d{14}", str(v)) for v in valores):
        raise ValueError("sementes ausentes ou CNPJ em formato inválido")
    destino = destino_novo(destino, fontes)
    result = {"gerado_em": agora(), "orgaos": sorted(valores), "unidades": [],
              "fontes": [{"path": str(p), "sha256": file_sha256(p)} for p in fontes],
              "completo": False, "cobertura_institucional_comprovada": False}
    gravar_json(destino / "alvos.json", result)
    return result


def url_compra(identificador: str) -> str:
    match = re.fullmatch(r"(\d{14})-1-(\d+)/(\d{4})", identificador)
    if not match:
        raise ValueError(f"identificador de compra inválido: {identificador}")
    cnpj, sequencial, ano = match.groups()
    return f"https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{int(sequencial)}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="acao", required=True)
    a = sub.add_parser("baseline"); a.add_argument("--destino", type=Path, required=True)
    a = sub.add_parser("verificar"); a.add_argument("--baseline", type=Path, required=True)
    a = sub.add_parser("alvos"); a.add_argument("--observadas", type=Path, required=True); a.add_argument("--cadastros", nargs="*", type=Path, default=[]); a.add_argument("--destino", type=Path, required=True)
    a = sub.add_parser("ensaio-rollback"); a.add_argument("--baseline", type=Path, required=True); a.add_argument("--destino", type=Path, required=True)
    a = sub.add_parser("staging"); a.add_argument("--fontes", nargs="+", type=Path, required=True); a.add_argument("--destino", type=Path, required=True); a.add_argument("--territorio-atas", type=Path)
    args = p.parse_args(argv)
    if args.acao == "baseline":
        r = capturar_baseline(args.destino)
    elif args.acao == "verificar":
        r = verificar_baseline(args.baseline)
    elif args.acao == "ensaio-rollback":
        r = ensaiar_rollback(args.baseline, args.destino)
    elif args.acao == "alvos":
        r = preparar_alvos(args.observadas, args.cadastros, args.destino)
    else:
        r = construir_staging(args.fontes, args.destino, territorio_atas=args.territorio_atas)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 1 if r.get("integro") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
