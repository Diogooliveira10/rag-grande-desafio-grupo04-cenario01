"""Integridade verificável de um bundle RAG imutável, sem promoção implícita.

O bundle é descrito por um JSON de preparação versionado. Este módulo valida
artefatos existentes; nunca constrói, migra, ativa ou reescreve snapshots.
"""
from __future__ import annotations

import hashlib
import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import config

BUNDLE_FORMAT = "rag-bundle-v1"
_FILE_ROLES = {"documents", "database", "index_manifest", "catalog"}


class SnapshotValidationError(ValueError):
    """Bundle ausente ou incoerente; ``code`` não contém caminhos privados."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@lru_cache(maxsize=1024)
def _sha256_cached(path: str, size: int, modified_ns: int) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    stat = path.stat()
    # Bundles são imutáveis durante a vida do processo. Cache por assinatura do
    # arquivo evita reler documentos/FAISS a cada probe de readiness.
    return _sha256_cached(str(path), stat.st_size, stat.st_mtime_ns)


def validate_bundle(manifest_path: Path | str) -> dict[str, Any]:
    """Confere todos os arquivos declarados e SQL pronto, em modo somente leitura.

    Caminhos de artefato são relativos ao manifesto. O chamador pode usá-lo na
    preparação ou no readiness; não muda os caminhos já carregados pelo runtime.
    """
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise SnapshotValidationError("manifesto_ausente")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SnapshotValidationError("manifesto_invalido") from None
    if not isinstance(manifest, Mapping) or manifest.get("format") != BUNDLE_FORMAT:
        raise SnapshotValidationError("formato_incompativel")
    snapshot_id = manifest.get("snapshot_id")
    files = manifest.get("files")
    if not isinstance(snapshot_id, str) or not snapshot_id.strip() or not isinstance(files, Mapping):
        raise SnapshotValidationError("manifesto_incompleto")
    if not _FILE_ROLES.issubset(files):
        raise SnapshotValidationError("artefatos_obrigatorios_ausentes")

    checked: dict[str, Path] = {}
    for role in sorted(_FILE_ROLES):
        item = files.get(role)
        if not isinstance(item, Mapping):
            raise SnapshotValidationError("manifesto_incompleto")
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str) or len(expected) != 64:
            raise SnapshotValidationError("manifesto_incompleto")
        artifact = (path.parent / relative).resolve()
        try:
            artifact.relative_to(path.parent)
        except ValueError:
            raise SnapshotValidationError("caminho_fora_do_bundle") from None
        if not artifact.is_file():
            raise SnapshotValidationError("artefato_ausente")
        if _sha256(artifact) != expected.lower():
            raise SnapshotValidationError("hash_divergente")
        checked[role] = artifact

    schema_version = manifest.get("sql_schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise SnapshotValidationError("schema_declarado_invalido")
    from src.structured_db import Banco, SCHEMA_VERSION, validar_banco
    if schema_version != SCHEMA_VERSION:
        raise SnapshotValidationError("schema_incompativel")
    try:
        validar_banco(checked["documents"], Banco(sqlite_path=checked["database"]))
    except FileNotFoundError:
        raise SnapshotValidationError("sql_ausente") from None
    except Exception as error:
        message = str(error).casefold()
        code = "schema_incompativel" if "schema" in message else (
            "snapshot_divergente" if "snapshot" in message else "sql_invalido"
        )
        raise SnapshotValidationError(code) from None

    try:
        index_manifest = json.loads(checked["index_manifest"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SnapshotValidationError("manifesto_indice_invalido") from None
    if not isinstance(index_manifest, Mapping):
        raise SnapshotValidationError("manifesto_indice_invalido")
    documents_file_entry = files.get("documents")
    if not isinstance(documents_file_entry, Mapping) or not isinstance(documents_file_entry.get("sha256"), str):
        raise SnapshotValidationError("manifesto_incompleto")
    documents_hash = documents_file_entry["sha256"].lower()
    operator_acceptance = _aceite_proveniencia(manifest, index_manifest, checked["index_manifest"])
    _validar_indice(index_manifest, checked["index_manifest"], documents_hash,
                    manifest.get("index_artifacts"), operator_acceptance=operator_acceptance)
    declared_documents_hash = manifest.get("documents_sha256", documents_hash)
    if not isinstance(declared_documents_hash, str) or declared_documents_hash.lower() != documents_hash:
        raise SnapshotValidationError("snapshot_divergente")
    _validar_catalogo(checked["catalog"], checked["database"])

    capabilities = manifest.get("capabilities", [])
    if not isinstance(capabilities, list) or any(not isinstance(value, str) for value in capabilities):
        raise SnapshotValidationError("capacidades_invalidas")
    return {
        "ready": True,
        "snapshot_id": snapshot_id,
        "schema_version": schema_version,
        "capabilities": sorted(set(capabilities)),
    }


def _jsonl_ids(path: Path) -> list[str]:
    identifiers: list[str] = []
    try:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                identifier = row.get("chunk_id") if isinstance(row, Mapping) else None
                if not isinstance(identifier, str) or not identifier:
                    raise SnapshotValidationError("indice_ids_invalidos")
                identifiers.append(identifier)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SnapshotValidationError("indice_jsonl_invalido") from None
    return identifiers


def _aceite_proveniencia(manifest: Mapping[str, Any], index: Mapping[str, Any],
                         index_path: Path) -> bool:
    """Aceite operacional vinculado ao índice exato; não atesta o encoding histórico.

    Sem aceite, os três gates continuam obrigatórios. O aceite só substitui a
    exigência de comprovação histórica, nunca hashes, cobertura ou integridade.
    """
    acceptance = manifest.get("operator_acceptance")
    if acceptance is None:
        return False
    if not isinstance(acceptance, Mapping):
        raise SnapshotValidationError("aceite_operador_invalido")
    model = acceptance.get("embedding_model")
    if (acceptance.get("scope") != "historical_embedding_provenance"
            or acceptance.get("approved") is not True
            or not isinstance(acceptance.get("approved_by"), str) or not acceptance["approved_by"].strip()
            or not isinstance(acceptance.get("approved_at"), str) or not acceptance["approved_at"].strip()
            or acceptance.get("snapshot_id") != manifest.get("snapshot_id")
            or index.get("snapshot_id") != manifest.get("snapshot_id")
            or acceptance.get("index_manifest_sha256") != _sha256(index_path)
            or not isinstance(model, str) or not model.strip()
            or model != index.get("embedding_model")
            or index.get("promocao_autorizada") is not True):
        raise SnapshotValidationError("aceite_operador_invalido")
    return True


def _validar_indice(index_manifest: Mapping[str, Any], global_path: Path,
                    documents_hash: str, artifacts: Any, *, operator_acceptance: bool = False) -> None:
    global_source_hash = index_manifest.get("source_sha256")
    if not isinstance(global_source_hash, str) or global_source_hash.lower() != documents_hash:
        raise SnapshotValidationError("snapshot_divergente")
    total_chunks, total_vectors = index_manifest.get("total_chunks"), index_manifest.get("total_vectors")
    if not isinstance(total_chunks, int) or not isinstance(total_vectors, int):
        raise SnapshotValidationError("cobertura_indice_desconhecida")
    if total_chunks != total_vectors:
        raise SnapshotValidationError("vetores_ausentes")
    if not operator_acceptance and any(index_manifest.get(field) is not True for field in (
        "homogeneidade_modelo_comprovada", "homogeneidade_configuracao_comprovada",
        "proveniencia_encoding_completa",
    )):
        raise SnapshotValidationError("proveniencia_incompleta")
    bases = index_manifest.get("bases")
    if not isinstance(bases, list) or not bases:
        raise SnapshotValidationError("bases_indice_ausentes")
    if not isinstance(artifacts, list):
        raise SnapshotValidationError("artefatos_indice_nao_declarados")
    declared: dict[str, str] = {}
    for item in artifacts:
        if not isinstance(item, Mapping) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
            raise SnapshotValidationError("manifesto_incompleto")
        declared[item["path"]] = item["sha256"].lower()
    index_root = global_path.parent.resolve()
    seen_bases: set[str] = set()
    expected_files: set[str] = set()
    for entry in bases:
        base_id = entry.get("base_id") if isinstance(entry, Mapping) else entry
        if not isinstance(base_id, str) or not base_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in base_id):
            raise SnapshotValidationError("base_indice_invalida")
        if base_id in seen_bases:
            raise SnapshotValidationError("base_indice_duplicada")
        seen_bases.add(base_id)
        directory = (index_root / base_id).resolve()
        try:
            directory.relative_to(index_root)
        except ValueError:
            raise SnapshotValidationError("caminho_fora_do_bundle") from None
        paths = {name: directory / name for name in (
            "manifest.json", "documents.jsonl", "vectors.jsonl", "vectors.faiss")}
        bundle_root = global_path.parent.parent.resolve()
        for name, artifact in paths.items():
            relative = artifact.relative_to(bundle_root).as_posix()
            expected_files.add(relative)
            if relative not in declared:
                raise SnapshotValidationError("artefatos_indice_nao_declarados")
            resolved = artifact.resolve()
            if not resolved.is_file() or _sha256(resolved) != declared[relative]:
                raise SnapshotValidationError("hash_divergente")
        try:
            submanifest = json.loads(paths["manifest.json"].read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise SnapshotValidationError("manifesto_indice_invalido") from None
        if not isinstance(submanifest, Mapping) or submanifest.get("base_id") != base_id:
            raise SnapshotValidationError("manifesto_indice_invalido")
        base_documents_hash = _sha256(paths["documents.jsonl"])
        if submanifest.get("source_sha256") != base_documents_hash:
            raise SnapshotValidationError("snapshot_divergente")
        count = submanifest.get("total_chunks")
        vector_count = submanifest.get("total_vectors")
        if not isinstance(count, int) or not isinstance(vector_count, int) or count != vector_count:
            raise SnapshotValidationError("vetores_ausentes")
        if operator_acceptance and submanifest.get("embedding_model") != index_manifest.get("embedding_model"):
            raise SnapshotValidationError("modelo_indice_divergente")
        if not operator_acceptance and any(submanifest.get(field) is not True for field in (
            "homogeneidade_modelo_comprovada", "homogeneidade_configuracao_comprovada",
            "proveniencia_encoding_completa",
        )):
            raise SnapshotValidationError("proveniencia_incompleta")
        document_ids = _jsonl_ids(paths["documents.jsonl"])
        vector_ids = _jsonl_ids(paths["vectors.jsonl"])
        if len(document_ids) != count or len(set(document_ids)) != count:
            raise SnapshotValidationError("documentos_indice_incoerentes")
        if len(vector_ids) != vector_count or len(set(vector_ids)) != vector_count or set(document_ids) != set(vector_ids):
            raise SnapshotValidationError("vetores_ausentes")
        try:
            import faiss
            faiss_index = faiss.read_index(str(paths["vectors.faiss"]))
        except Exception:
            raise SnapshotValidationError("indice_faiss_invalido") from None
        if int(faiss_index.ntotal) != vector_count:
            raise SnapshotValidationError("vetores_ausentes")
    if set(declared) != expected_files:
        raise SnapshotValidationError("artefatos_indice_nao_declarados")


def _validar_catalogo(catalog_path: Path, database_path: Path) -> None:
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SnapshotValidationError("catalogo_invalido") from None
    if not isinstance(catalog, Mapping):
        raise SnapshotValidationError("catalogo_invalido")
    from src.structured_db import Banco
    banco = Banco(sqlite_path=database_path)
    names: set[str] = set()
    for table in ("contratacoes", "pca_planos"):
        for column in ("orgao_nome", "unidade_nome"):
            for row in banco.consultar(f"SELECT DISTINCT {column} AS nome FROM {table} WHERE {column} IS NOT NULL"):
                name = " ".join(str(row["nome"]).split())
                if name:
                    names.add(name)
    def values(column: str) -> list[str]:
        found = {" ".join(str(row["v"]).split()) for row in banco.consultar(
            f"SELECT DISTINCT {column} AS v FROM contratacoes WHERE {column} IS NOT NULL")}
        return sorted(value for value in found if value)
    municipalities, suppliers = values("municipio"), values("fornecedor")
    expected = hashlib.sha256(json.dumps(
        {"nomes": sorted(names), "municipios": municipalities, "fornecedores": suppliers},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    if (catalog.get("fonte_sha256") != expected or catalog.get("nomes") != sorted(names)
            or catalog.get("municipios") != municipalities or catalog.get("fornecedores") != suppliers):
        raise SnapshotValidationError("catalogo_divergente")
    if catalog.get("versao", 0) >= 3:
        from src.analysis.entities import (
            _GENERICAS_DE_FORNECEDOR, _NAO_SAO_NOME, _VERBOS_COMUNS_DEPOIS_DO_NOME,
        )
        from src.analysis.suppliers import compor_fornecedores
        identities = compor_fornecedores(
            banco, _GENERICAS_DE_FORNECEDOR | _NAO_SAO_NOME | _VERBOS_COMUNS_DEPOIS_DO_NOME,
            sorted(names), municipalities,
        )
        signature = hashlib.sha256(json.dumps(
            identities, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        if (catalog.get("fornecedores_sha256") != signature
                or any(catalog.get(key) != value for key, value in identities.items())):
            raise SnapshotValidationError("catalogo_divergente")


# Validar o bundle relê ~290 mil linhas do índice e recompõe o catálogo
# (~1 min de CPU). Chamadas simultâneas ao /ready repetiam tudo em paralelo
# e nenhuma terminava (2026-10-27). O resultado válido fica guardado enquanto
# nenhum arquivo lido mudar de tamanho ou data, o critério do cache de hashes.
_VALIDACAO_EM_ANDAMENTO = threading.Lock()
_BUNDLES_VALIDOS: dict[tuple[tuple[str, int, int], ...], dict[str, Any]] = {}


def _assinatura_do_bundle(manifest_path: Path | str) -> tuple[tuple[str, int, int], ...] | None:
    """Caminho, tamanho e data do manifesto e de cada arquivo que a validação lê; None se não der para listar."""
    try:
        path = Path(manifest_path).resolve()
        manifest = json.loads(path.read_text(encoding="utf-8"))
        files = [(path.parent / item["path"]).resolve() for item in manifest["files"].values()]
        bundle_root = (path.parent / manifest["files"]["index_manifest"]["path"]).resolve().parent.parent
        artifacts = [(bundle_root / item["path"]).resolve() for item in manifest.get("index_artifacts") or []]
        assinatura = []
        for arquivo in sorted({path, *files, *artifacts}):
            stat = arquivo.stat()
            assinatura.append((str(arquivo), stat.st_size, stat.st_mtime_ns))
        return tuple(assinatura)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError):
        return None


def readiness(manifest_path: Path | str | None = None) -> dict[str, Any]:
    """Retorna estado sem paths, hashes internos ou mensagens de exceção.

    Só uma validação por vez: quem chega enquanto ela roda recebe na hora
    ``validacao_em_andamento``, em vez de abrir outra leitura do bundle.
    """
    configured = manifest_path if manifest_path is not None else config.SNAPSHOT_MANIFEST
    if not configured:
        return {"ready": False, "reason": "manifesto_nao_configurado"}
    if not _VALIDACAO_EM_ANDAMENTO.acquire(blocking=False):
        return {"ready": False, "reason": "validacao_em_andamento"}
    try:
        assinatura = _assinatura_do_bundle(configured)
        if assinatura is not None and assinatura in _BUNDLES_VALIDOS:
            resultado = _BUNDLES_VALIDOS[assinatura]
        else:
            try:
                resultado = validate_bundle(configured)
            except SnapshotValidationError as error:
                return {"ready": False, "reason": error.code}
            if assinatura is not None:
                if len(_BUNDLES_VALIDOS) >= 8:
                    _BUNDLES_VALIDOS.clear()
                _BUNDLES_VALIDOS[assinatura] = resultado
        return {**resultado, "capabilities": list(resultado["capabilities"])}
    finally:
        _VALIDACAO_EM_ANDAMENTO.release()
