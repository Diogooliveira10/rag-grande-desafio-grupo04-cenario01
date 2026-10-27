"""Identificadores citados na pergunta (número do PNCP, CNPJ, contrato) e os registros que batem com eles.

Separado de src/retrieve.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping, Sequence

from src.retrieval.lexical import _tokens
from src.retrieval.records import RetrievalResult, _metadata_of, _result


_PNCP_CONTROL_RE = re.compile(r"(?<!\d)\d{14}-\d-\d{6}/\d{4}(?:-\d{6})?(?!\d)")


# CNPJ com ou sem pontuação; os 14 dígitos no começo de um número de controle
# ficam de fora pelo "-" que vem depois deles.
_CNPJ_RE = re.compile(r"(?<![\d./-])(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?![\d/-])")


_CONTRACT_NUMBER_RE = re.compile(
    r"\bcontrato\s*(?:n(?:[uú]mero|ro)?\.?\s*[º°o]?\s*)?"
    r"(\d+)\s*/\s*(\d{4})(?!\d)", re.IGNORECASE
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
