"""Controle de qualidade do corpus normalizado (item 7 do documento de correções).

Roda depois de ``prepare_documents`` e antes de ``index``, sobre os JSONL de
``data/normalized``. Nunca muda o texto de um chunk: os vetores do índice são
reaproveitados pelo SHA-256 do texto (``index.py --embeddings``), e um texto
novo exigiria gerar vetores de novo. Muda só status e metadados:

- **Texto ilegível.** PDF sem mapa de caracteres vira ``/12 /3 □`` (glifos)
  ou ``/U0044/U0049`` (códigos no lugar das letras). O chunk recebe
  ``status_extracao = "texto_ilegivel"`` e ``extras.qualidade_texto`` com o
  motivo, e o ``index.py`` o deixa fora do índice. Tabelas de lances, só com
  CNPJ, datas e "R$", continuam: têm pouca letra, mas não são glifos.
- **Vigência das normas.** Os metadados que o manifesto declara por URL
  (``acesso.metadados_por_url``: número da norma, publicação, início da
  vigência) vão para ``extras`` dos chunks com aquela ``origem``. Edital e
  termo de referência do PNCP recebem a data de publicação da compra
  vinculada. ``data_vigencia`` continua vazia: no projeto ela é o fim da
  vigência ("vigente até"), e uma lei em vigor não tem fim.
- **Contrato e compra da mesma contratação** (``numero_controle_compra``):
  o contrato herda a modalidade da compra, e a compra, o fornecedor do
  contrato.
- **Valor simbólico** (abaixo de R$ 1,00, como R$ 0,00 e R$ 0,01 do PNCP):
  ``extras.valor_simbolico = True``. O valor original fica como está.

Uso::

    py -m src.corpus_quality              # aplica nos JSONL e grava o relatório
    py -m src.corpus_quality --verificar  # só conta, sem gravar nada
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import config

NORMALIZED_DIR = config.NORMALIZED_DATA_DIR
MANIFEST_PATH = config.ROOT_DIR / "corpus_manifest.yaml"
# Junto do inventário (versionado no Git, ao contrário de data/normalized).
RELATORIO_PATH = config.ROOT_DIR / "data" / "inventory" / "qualidade_v6.json"

TEXTO_ILEGIVEL = "texto_ilegivel"

# Token que é só glifo sem mapa de caracteres: "/12", "□".
_GLIFO = re.compile(r"^(?:/\d+|□+)$")
# Código de caractere no lugar da letra: "/U0044" = "D".
_CODIGO_UNICODE = re.compile(r"/U[0-9A-Fa-f]{4}")
# Metade ou mais dos tokens (ou dos caracteres) nesse formato = ilegível. Na
# v6 o resultado é o mesmo com qualquer limite entre 0,3 e 0,7.
_LIMITE_ILEGIVEL = 0.5

# "00394502002864-1-000861/2025": identificador de compra no PNCP. Contrato
# tem "-2-" e ata tem um sufixo depois do ano.
_ID_DE_COMPRA = re.compile(r"^\d{14}-1-\d{6}/\d{4}$")

# Campos que o manifesto declara por URL e que vão para extras.
_CAMPOS_DE_NORMA = (
    "tipo_documento", "numero_norma", "data_publicacao", "data_vigencia_inicio",
    "data_vigencia_fim", "ambito", "fonte_oficial",
)

# Texto ilegível

def motivo_de_texto_ilegivel(texto: str) -> Optional[str]:
    """Por que o texto é ilegível, ou ``None`` se ele é legível (ou vazio)."""
    tokens = texto.split()
    if not tokens:
        return None  # chunk sem texto já fica fora do índice (index.py)
    if not any(caractere.isalpha() for caractere in texto):
        return "sem_letras"
    glifos = sum(1 for token in tokens if _GLIFO.match(token) or "□" in token)
    if glifos / len(tokens) >= _LIMITE_ILEGIVEL:
        return "glifos_sem_mapa_de_caracteres"
    caracteres = len("".join(tokens))
    codigos = sum(len(achado.group()) for achado in _CODIGO_UNICODE.finditer(texto))
    if codigos / caracteres >= _LIMITE_ILEGIVEL:
        return "codigos_unicode_no_lugar_das_letras"
    return None


def decodificar_codigos_unicode(texto: str) -> str:
    """"/U0044/U0049Á/U0052/U0049/U004F" -> "DIÁRIO" (usado na extração de PDF)."""
    return _CODIGO_UNICODE.sub(lambda achado: chr(int(achado.group()[2:], 16)), texto)


def _marcar_texto(registro: dict[str, Any], extras: dict[str, Any]) -> Optional[str]:
    motivo = motivo_de_texto_ilegivel(str(registro.get("text") or ""))
    if motivo:
        registro["status_extracao"] = TEXTO_ILEGIVEL
        extras["qualidade_texto"] = motivo
    return motivo


# Metadados declarados no manifesto


def _url_comparavel(url: str) -> str:
    # O leitor de manifesto do projeto (fetch_corpus._parse_manifest_yaml)
    # guarda a chave com as aspas: '"https://..."'.
    return url.strip().strip("\"'").rstrip("/").lower()


def metadados_declarados(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``acesso.metadados_por_url`` de todas as bases, pela URL normalizada."""
    declarados: dict[str, dict[str, Any]] = {}
    for base in manifest.get("bases") or []:
        por_url = (base.get("acesso") or {}).get("metadados_por_url") or {}
        for url, metadados in por_url.items():
            if isinstance(metadados, Mapping):
                declarados[_url_comparavel(str(url))] = dict(metadados)
    return declarados


def _ler_manifesto(caminho: Path) -> dict[str, Any]:
    from src.fetch_corpus import load_manifest

    return load_manifest(caminho)


# Enriquecimento


def _numero(valor: Any) -> Optional[float]:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


class _Contexto:
    """O que um registro precisa saber dos outros: compras e contratos."""

    def __init__(self, registros: Iterable[Mapping[str, Any]], declarados: Mapping[str, dict[str, Any]]):
        self.declarados = declarados
        self.compras: dict[str, Mapping[str, Any]] = {}
        self.fornecedores_por_compra: dict[str, set[str]] = defaultdict(set)
        for registro in registros:
            extras = registro.get("extras") or {}
            identificador = str(extras.get("numero_controle_pncp") or "")
            if registro.get("natureza") == "estruturada" and _ID_DE_COMPRA.match(identificador):
                self.compras[identificador] = extras
            compra = extras.get("numero_controle_compra")
            if registro.get("natureza") == "estruturada" and compra and extras.get("fornecedor"):
                self.fornecedores_por_compra[str(compra)].add(str(extras["fornecedor"]))


def enriquecer(registro: Mapping[str, Any], contexto: _Contexto) -> tuple[dict[str, Any], list[str]]:
    """Cópia do registro com status e metadados de qualidade, e o que mudou.

    Idempotente: aplicar de novo no resultado não muda nada.
    """
    novo = dict(registro)
    extras = dict(registro.get("extras") or {})
    mudancas: list[str] = []

    if _marcar_texto(novo, extras):
        mudancas.append("texto_ilegivel")

    declarado = contexto.declarados.get(_url_comparavel(str(registro.get("origem") or "")))
    if declarado:
        novos = [campo for campo in _CAMPOS_DE_NORMA
                 if declarado.get(campo) not in (None, "") and campo not in extras]
        for campo in novos:
            extras[campo] = declarado[campo]
        if novos:
            mudancas.append("metadados_do_manifesto")
        if declarado.get("titulo") and not extras.get("titulo_documento"):
            extras["titulo_documento"] = declarado["titulo"]
        if "data_vigencia_inicio" in extras:
            extras.setdefault("fonte_vigencia", "manifesto")

    compra_id = str(extras.get("numero_controle_compra") or "")
    compra = contexto.compras.get(compra_id)
    if compra:
        if not extras.get("modalidade") and compra.get("modalidade"):
            extras["modalidade"] = compra["modalidade"]
            extras["modalidade_fonte"] = "compra_vinculada"
            mudancas.append("modalidade_da_compra")
        if registro.get("natureza") == "normativa" and compra.get("data_publicacao"):
            if "data_publicacao" not in extras:
                extras["data_publicacao"] = str(compra["data_publicacao"])[:10]  # sem a hora
                extras.setdefault("tipo_documento", "documento_de_contratacao")
                mudancas.append("publicacao_da_compra")
            if "data_vigencia_inicio" not in extras:
                extras["data_vigencia_inicio"] = extras["data_publicacao"]
                extras.setdefault("fonte_vigencia", "publicacao_da_compra_no_pncp")

    # Contratos e compras da base estruturada. Os registros de compra que
    # estão na base agregada são outro problema (item 6, pca_planos).
    if registro.get("natureza") == "estruturada":
        identificador = str(extras.get("numero_controle_pncp") or "")
        fornecedores = sorted(contexto.fornecedores_por_compra.get(identificador, ()))
        if fornecedores and _ID_DE_COMPRA.match(identificador) and not extras.get("fornecedor"):
            if len(fornecedores) == 1:
                extras["fornecedor"] = fornecedores[0]
            if "fornecedores_contratados" not in extras:
                extras["fornecedores_contratados"] = fornecedores
                mudancas.append("fornecedor_do_contrato")

        valor = _numero(extras.get("valor"))
        if valor is not None and valor < 1 and not extras.get("valor_simbolico"):
            extras["valor_simbolico"] = True
            mudancas.append("valor_simbolico")

    novo["extras"] = extras
    return novo, mudancas


# Arquivos


def arquivos_padrao(diretorio: Path = NORMALIZED_DIR) -> list[Path]:
    """Os JSONL que o sistema lê: o da raiz (texto e SQL na hora da pergunta),
    o combinado da v6 e os da v6 por base (entrada do ``index.py``)."""
    candidatos = [diretorio / "documents.jsonl", diretorio / "v6" / "documents.jsonl"]
    candidatos += sorted((diretorio / "v6").glob("*/documents.jsonl"))
    return [caminho for caminho in candidatos if caminho.is_file()]


def _ler_jsonl(caminho: Path) -> list[dict[str, Any]]:
    with caminho.open(encoding="utf-8") as linhas:
        return [json.loads(linha) for linha in linhas if linha.strip()]


def _gravar_jsonl(caminho: Path, registros: Sequence[Mapping[str, Any]]) -> None:
    # Mesmo formato de prepare_documents.py, para o diff mostrar só o que mudou.
    temporario = caminho.with_name(caminho.name + ".tmp")
    with temporario.open("w", encoding="utf-8", newline="\n") as saida:
        for registro in registros:
            saida.write(json.dumps(registro, ensure_ascii=False, sort_keys=True) + "\n")
    temporario.replace(caminho)


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def aplicar(
    arquivos: Sequence[Path],
    manifest: Mapping[str, Any],
    *,
    gravar: bool = True,
) -> dict[str, Any]:
    """Enriquece cada arquivo e devolve o relatório (contagens por base)."""
    conteudos = {caminho: _ler_jsonl(caminho) for caminho in arquivos}
    unicos: dict[str, dict[str, Any]] = {}
    for registros in conteudos.values():
        for registro in registros:
            unicos.setdefault(str(registro.get("chunk_id")), registro)
    contexto = _Contexto(unicos.values(), metadados_declarados(manifest))

    por_base: dict[str, Counter] = defaultdict(Counter)
    ilegiveis_por_documento: Counter = Counter()
    normas: dict[str, dict[str, Any]] = {}
    for chunk_id, registro in unicos.items():
        novo, mudancas = enriquecer(registro, contexto)
        base = str(novo.get("base_id"))
        por_base[base]["chunks"] += 1
        extras = novo["extras"]
        if novo.get("status_extracao") == TEXTO_ILEGIVEL:
            por_base[base]["texto_ilegivel"] += 1
            ilegiveis_por_documento[str(novo.get("document_id"))] += 1
        if extras.get("data_vigencia_inicio"):
            por_base[base]["com_inicio_de_vigencia"] += 1
            chave = str(extras.get("numero_norma") or extras.get("numero_controle_compra") or novo.get("origem"))
            normas.setdefault(chave, {
                "tipo_documento": extras.get("tipo_documento"),
                "data_publicacao": extras.get("data_publicacao"),
                "data_vigencia_inicio": extras.get("data_vigencia_inicio"),
                "fonte_vigencia": extras.get("fonte_vigencia"),
                "chunks": 0,
            })["chunks"] += 1
        if extras.get("modalidade_fonte") == "compra_vinculada":
            por_base[base]["modalidade_da_compra_vinculada"] += 1
        if extras.get("fornecedores_contratados"):
            por_base[base]["fornecedor_do_contrato_vinculado"] += 1
        if extras.get("valor_simbolico"):
            por_base[base]["valor_simbolico"] += 1
        if novo.get("natureza") == "estruturada" and extras.get("numero_controle_compra") and not extras.get("modalidade"):
            por_base[base]["contrato_sem_modalidade"] += 1
        unicos[chunk_id] = novo

    if gravar:
        for caminho, registros in conteudos.items():
            _gravar_jsonl(caminho, [unicos[str(registro.get("chunk_id"))] for registro in registros])

    return {
        "arquivos": {
            caminho.relative_to(config.ROOT_DIR).as_posix() if caminho.is_relative_to(config.ROOT_DIR) else str(caminho):
                (_sha256(caminho) if gravar else None)
            for caminho in arquivos
        },
        "por_base": {base: dict(sorted(contagem.items())) for base, contagem in sorted(por_base.items())},
        "documentos_com_texto_ilegivel": dict(sorted(ilegiveis_por_documento.items())),
        "vigencias": dict(sorted(normas.items())),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("arquivos", nargs="*", type=Path, help="JSONL normalizados (padrão: os que o sistema lê)")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--relatorio", type=Path, default=RELATORIO_PATH)
    parser.add_argument("--verificar", action="store_true", help="só conta, sem gravar nada")
    args = parser.parse_args(argv)

    arquivos = args.arquivos or arquivos_padrao()
    if not arquivos:
        parser.error("nenhum documents.jsonl encontrado em data/normalized")
    relatorio = aplicar(arquivos, _ler_manifesto(args.manifest), gravar=not args.verificar)
    if not args.verificar:
        args.relatorio.parent.mkdir(parents=True, exist_ok=True)
        args.relatorio.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(relatorio["por_base"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
