"""Extração de unidades por formato: PDF (com OCR das páginas ilegíveis), JSONL, CSV, JSON, conversas, PCA e resultados."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator

from src import ocr
from src.ingest import sha256_file
from src.pca_indicators import eh_csv_pca, indicadores_pca
from src.corpus_quality import decodificar_codigos_unicode, motivo_de_texto_ilegivel
from src.pncp_result_indicators import indicadores_resultados_homologados
from src.preparation.common import PROJECT_ROOT, SourceUnit, _relative_path
from src.preparation.provenance import _declared_record_metadata, _file_provenance, _make_unit, _record_logical_id
from src.preparation.readers import _csv_records, _json_records, _read_text, _visible_html


def _texto_legivel_da_pagina(path: Path, page_index: int, text: str) -> tuple[str, str, str | None]:
    """(texto, status, extração) da página de PDF; extração ``None`` = mantém o texto.

    Página escaneada (sem camada de texto) ou com texto ilegível vai para o
    OCR (src/ocr.py). "Ilegível" é o critério de src/corpus_quality.py:
    glifos sem mapa de caracteres ("/12 /3 □") ou códigos no lugar das letras
    ("/U0044"), que são só decodificados, sem OCR.
    """
    # A mostly readable page can still have an encoded header. Decode before
    # the whole-page quality threshold, otherwise its first chunk stays bad.
    decodificado = decodificar_codigos_unicode(text)
    if decodificado != text and motivo_de_texto_ilegivel(decodificado) is None:
        return decodificado, "ok", "codigos_unicode_decodificados"
    motivo = motivo_de_texto_ilegivel(text) if text else None
    if text and motivo is None:
        return text, "ok", None
    if ocr.disponivel():
        lido = ocr.texto_da_pagina(path, page_index).strip()
        # Página vazia aceita o que o OCR ler; texto ilegível só troca por legível.
        if lido and (not text or motivo_de_texto_ilegivel(lido) is None):
            return lido, "ok_ocr", ocr.motor()
    return text, "ok" if text else "sem_texto", None


def _reextrair_pagina_ilegivel(path: Path, unit: SourceUnit) -> SourceUnit:
    """Página de PDF guardada no cache com texto ilegível: tenta de novo."""
    if unit.formato != "pdf" or unit.status != "ok":
        return unit
    text, status, extracao = _texto_legivel_da_pagina(path, int(unit.extras["pagina"]) - 1, unit.text)
    if not extracao:
        return unit
    return replace(unit, text=text, status=status, extras={**unit.extras, "extracao": extracao})


def _pdf_units(
    path: Path,
    base: dict[str, Any],
    digest: str,
    manifest_hash: str,
    collected_at: str,
    origin: str,
) -> Iterator[SourceUnit]:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError("extração de PDF requer pypdf; execute pip install -r requirements.txt") from error

    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    base_document_id = f"{base['base_id']}:{digest[:16]}"
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        status = "ok" if text else "sem_texto"
        extras: dict[str, Any] = {"pagina": page_number, "total_paginas": total_pages}
        legivel, status_legivel, extracao = _texto_legivel_da_pagina(path, page_number - 1, text)
        if extracao:
            text, status = legivel, status_legivel
            extras["extracao"] = extracao
        yield _make_unit(
            path,
            base,
            digest,
            text,
            f"{base_document_id}:pagina-{page_number}",
            "pdf",
            status,
            manifest_hash,
            collected_at,
            origin,
            extras,
        )


@dataclass(frozen=True)
class _ArquivoDeOrigem:
    """O arquivo bruto e a proveniência que cada unidade extraída dele carrega."""

    path: Path
    base: dict[str, Any]
    digest: str
    manifest_hash: str
    collected_at: str
    origin: str

    @property
    def document_id(self) -> str:
        return f"{self.base['base_id']}:{self.digest[:16]}"

    def unidade(
        self, text: str, document_id: str, formato: str, status: str, extras: dict[str, Any] | None = None
    ) -> SourceUnit:
        return _make_unit(
            self.path, self.base, self.digest, text, document_id, formato, status,
            self.manifest_hash, self.collected_at, self.origin, extras,
        )


# Campos do registro JSONL que viram metadados da unidade quando presentes.
_CAMPOS_DECLARADOS_JSONL = (
    "dataset",
    "tipo_valor",
    "indicador",
    "dimensao",
    "recorte",
    "metadados",
    "source_ref",
    "artifact_id",
    "source_sha256",
    "source_url",
)


def _file_units(
    path: Path,
    base: dict[str, Any],
    manifest_hash: str,
    fallback_collected_at: str,
    inventory_provenance: dict[tuple[str, str], dict[str, str]],
) -> Iterator[SourceUnit]:
    digest = sha256_file(path)
    origin, collected_at = _file_provenance(
        base, path, digest, inventory_provenance, fallback_collected_at
    )
    suffix = path.suffix.lower()
    arquivo = _ArquivoDeOrigem(path, base, digest, manifest_hash, collected_at, origin)

    if base.get("dados_sinteticos"):
        yield from _unidades_de_conversas(arquivo, suffix)
    elif suffix == ".pdf":
        yield from _pdf_units(path, base, digest, manifest_hash, collected_at, origin)
    elif suffix == ".jsonl":
        yield from _unidades_jsonl(arquivo)
    elif suffix == ".csv":
        records = [record for _, record in _csv_records(path)] if str(base.get("natureza")) == "agregada" else None
        if records and eh_csv_pca(records[0]):
            yield from _unidades_de_indicadores_pca(arquivo, records)
        else:
            yield from _unidades_csv(arquivo)
    elif suffix == ".json":
        yield from _unidades_json(arquivo)
    else:
        text = _read_text(path)
        if suffix in {".html", ".htm", ".aspx"}:
            text = _visible_html(text)
        yield arquivo.unidade(
            text,
            arquivo.document_id,
            suffix.removeprefix(".") or "bin",
            "ok" if text.strip() else "sem_texto",
        )


def _unidades_de_conversas(arquivo: _ArquivoDeOrigem, suffix: str) -> Iterator[SourceUnit]:
    """Uma unidade por conversa sintética, com a sensibilidade declarada nela."""
    from src.synthetic_conversations import conversation_document

    if suffix == ".json":
        payloads = [json.loads(_read_text(arquivo.path))]
    elif suffix == ".jsonl":
        payloads = [json.loads(line) for line in _read_text(arquivo.path).splitlines() if line.strip()]
    else:
        raise ValueError("conversas sintéticas exigem JSON/JSONL canônico, sem cópias TXT")
    seen_ids: set[str] = set()
    for payload in payloads:
        text, sensitivity, extras = conversation_document(payload)
        conversation_id = str(payload["conversation_id"])
        if conversation_id in seen_ids:
            raise ValueError(f"conversation_id duplicado: {conversation_id}")
        seen_ids.add(conversation_id)
        logical_id = f"{arquivo.base['base_id']}:{conversation_id}"
        unit = arquivo.unidade(
            text, f"{arquivo.document_id}:{conversation_id}", suffix.lstrip("."), "ok",
            {**extras, "logical_id": logical_id},
        )
        yield replace(unit, sensitivity=sensitivity)


def _unidades_jsonl(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por linha; linha que não é JSON vira unidade com status json_invalido."""
    for line_number, line in enumerate(_read_text(arquivo.path).splitlines(), start=1):
        if not line.strip():
            continue
        payload: Any = None
        try:
            payload = json.loads(line)
            text = (
                str(payload.get("texto"))
                if isinstance(payload, dict) and payload.get("texto")
                else json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            )
            status = "ok"
        except json.JSONDecodeError:
            text = line.strip()
            status = "json_invalido"
        source_file = _relative_path(arquivo.path)
        extras = _declared_record_metadata(payload, arquivo.base)
        if isinstance(payload, dict):
            for key in _CAMPOS_DECLARADOS_JSONL:
                if key in payload:
                    extras[key] = payload[key]
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}:linha-{line_number}",
            "jsonl",
            status,
            {
                "linha": line_number,
                "logical_id": (
                    str(payload.get("logical_id"))
                    if isinstance(payload, dict) and payload.get("logical_id")
                    else _record_logical_id(
                        str(arquivo.base["base_id"]),
                        source_file,
                        payload,
                        f"linha-{line_number}",
                    )
                ),
                **extras,
            },
        )


def _unidades_de_indicadores_pca(arquivo: _ArquivoDeOrigem, records: list[dict[str, Any]]) -> Iterator[SourceUnit]:
    """Base agregada: um chunk por indicador calculado, não por linha do CSV
    (ver src/pca_indicators.py)."""
    base = arquivo.base
    source_file = _relative_path(arquivo.path)
    for indicador in indicadores_pca(
        records,
        orgao=str(base.get("orgao") or base.get("fonte") or "o órgão"),
        ano=base.get("ano"),
    ):
        yield arquivo.unidade(
            indicador.texto,
            f"{arquivo.document_id}:indicador-{indicador.id}",
            "csv",
            "ok",
            {
                "indicador": indicador.id,
                "dimensao": indicador.dimensao,
                "logical_id": f"{base['base_id']}:{source_file}:indicador-{indicador.id}",
                **indicador.metadados,
            },
        )


def _unidades_csv(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por registro do CSV."""
    for record_number, record in _csv_records(arquivo.path):
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        source_file = _relative_path(arquivo.path)
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}:registro-{record_number}",
            "csv",
            "ok",
            {
                "registro": record_number,
                "logical_id": _record_logical_id(
                    str(arquivo.base["base_id"]),
                    source_file,
                    record,
                    f"registro-{record_number}",
                ),
                **_declared_record_metadata(record, arquivo.base),
            },
        )


def _unidades_json(arquivo: _ArquivoDeOrigem) -> Iterator[SourceUnit]:
    """Uma unidade por registro do JSON; JSON ilegível vira unidade com status json_invalido."""
    raw = _read_text(arquivo.path)
    try:
        payload = json.loads(raw)
        status = "ok"
    except json.JSONDecodeError:
        payload = raw
        status = "json_invalido"
    for record_number, record in _json_records(payload, unwrap_resultado=arquivo.base.get("natureza") == "agregada"):
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2)
        suffix_id = f":registro-{record_number}" if record_number else ""
        source_file = _relative_path(arquivo.path)
        yield arquivo.unidade(
            text,
            f"{arquivo.document_id}{suffix_id}",
            "json",
            status,
            {
                **({"registro": record_number} if record_number else {}),
                "logical_id": _record_logical_id(
                    str(arquivo.base["base_id"]),
                    source_file,
                    record,
                    f"registro-{record_number or 1}",
                ),
                **_declared_record_metadata(record, arquivo.base),
            },
        )


def _unidades_de_resultados_homologados(
    inventory_path: Path,
    base: dict[str, Any],
    summary_manifest_hash: str,
    fallback_collected_at: str,
) -> Iterator[SourceUnit]:
    """Um documento por indicador de resultados homologados calculado sobre o inventário."""
    inventory_payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    for indicador in indicadores_resultados_homologados(inventory_payload, PROJECT_ROOT):
        text = str(indicador.get("texto") or "").strip()
        if not text:
            continue
        indicator_id = str(indicador["indicador"])
        document_id = f"pca_agregado:pncp:{indicator_id}"
        logical_id = document_id
        source_file = f"{_relative_path(inventory_path)}#derivado:{indicator_id}"
        content_digest = hashlib.sha256(
            json.dumps(indicador, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        extras = {
            key: value
            for key, value in indicador.items()
            if key != "texto"
        }
        # O indicador é calculado localmente; a origem aponta para uma
        # fonte pública do cálculo, nunca para o caminho do inventário.
        sources = indicador.get("observacoes") or inventory_payload.get("arquivos") or []
        indicator_origin = next(
            (str(source["origem"]) for source in sources
             if isinstance(source, dict) and
             str(source.get("origem") or "").startswith(("https://", "http://"))),
            "PNCP: resultados de itens homologados",
        )
        extras["tipo_origem"] = "calculo_local_sobre_fontes_publicas"
        yield SourceUnit(
            source_file=source_file,
            logical_id=logical_id,
            document_id=document_id,
            base_id="pca_agregado",
            natureza=str(base["natureza"]),
            doc_type="indicador_pncp",
            sensitivity=str(base.get("sensitivity_default") or "publico"),
            origem=indicator_origin,
            manifest_hash=summary_manifest_hash,
            bytes=len(text.encode("utf-8")),
            data_vigencia=None,
            chunking_sugerido=str(base.get("chunking_sugerido") or "chunking_generico"),
            formato="json",
            sha256=content_digest,
            text=text,
            status="ok",
            extras={
                "source_file": source_file,
                "arquivo_bruto": source_file,
                "nome_arquivo": indicator_id,
                "manifest_fonte": base.get("fonte"),
                "manifest_hash": summary_manifest_hash,
                "origem": indicator_origin,
                "acesso_tipo": (base.get("acesso") or {}).get("tipo"),
                "logical_id": logical_id,
                "coletado_em": fallback_collected_at,
                **extras,
            },
        )
