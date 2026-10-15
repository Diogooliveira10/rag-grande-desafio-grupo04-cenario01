'''Coleta resumível do censo v7 alimentando o modo direto (§7 do plano v7).

Percorre janelas determinísticas da API do PNCP e acumula o ``documents.jsonl``
do modo direto **sem materializar ``data/raw/``** — cada registro vira chunk na
hora (``src.build_direto.chunk_de_registro``) e é só então descartado.

Janelas (as mesmas URLs que a v6 registrou no inventário):

- ``contrato_assinado`` — uma por órgão:
  ``/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20251231&cnpjOrgao=X``
  paginada (§3 do plano v7 mediu a escala exatamente assim);
- ``contratacao`` (publicações de compra) — mês × modalidade:
  ``/api/consulta/v1/contratacoes/publicacao?...&codigoModalidadeContratacao=M&uf=RJ``.

Retomada: ``estado_coleta.json`` guarda as janelas completas; uma janela só é
gravada no ``documents.jsonl`` quando termina inteira (append atômico por
janela), então a retomada nunca duplica registros — e o ``finalizar_direto``
deduplica por ``chunk_id`` de qualquer forma.

As retentativas/paletas são as do ``src/source_check.py``
(``request_url(..., attempts=3)``) e o rate limit declarado no manifesto
(``rate_limit_seg``); nada de regra própria aqui.

Uso (CLI):

    python -m src.coletar_direto --manifest corpus_manifest.yaml \
        --base-id contratos_estruturado --destino data/v7_direto \
        --orgaos orgaos_v6.json [--sem-publicacoes] [--so-publicacoes] [--so-atas]
'''

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.build_direto import (
    CHUNK_SIZE_PADRAO,
    OVERLAP_PADRAO,
    RegistroDireto,
    chunk_de_registro,
    finalizar_direto,
)
from src.fetch_corpus import _build_request_url, _payload_records, load_manifest

RequestFn = Callable[[str], Any]

# Modalidades do population `contratacao` do manifesto (v6).
MODALIDADES_PADRAO = (1, 3, 4, 5, 6, 7, 8, 9, 11)
ENDPOINT_CONTRATOS = "https://pncp.gov.br/api/consulta/v1/contratos"
ENDPOINT_PUBLICACOES = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
ENDPOINT_ATAS = "https://pncp.gov.br/api/consulta/v1/atas"
TAMANHO_PAGINA_CONTRATOS = 500
TAMANHO_PAGINA_PUBLICACOES = 50
TAMANHO_PAGINA_ATAS = 50
MAX_PAGINAS_POR_JANELA = 10000


@dataclass
class Janela:
    '''Um recorte determinístico da API: mesmo recorte = mesmos registros.

    ``filtro`` fecha o corte no lado do cliente (chave/valor, comparação por
    texto): a API de atas filtra por vigência, não pelo ano da ata.
    '''

    id: str
    endpoint: str
    params: dict[str, Any] = field(default_factory=dict)
    filtro: dict[str, Any] = field(default_factory=dict)

    @property
    def tamanho_pagina(self) -> int:
        return int(self.params.get("tamanhoPagina") or 50)


def janelas_contratos(orgaos: list[str]) -> list[Janela]:
    return [
        Janela(
            id=f"contrato_assinado:{cnpj}",
            endpoint=ENDPOINT_CONTRATOS,
            params={
                "dataInicial": "20250101",
                "dataFinal": "20251231",
                "cnpjOrgao": cnpj,
                "tamanhoPagina": TAMANHO_PAGINA_CONTRATOS,
            },
        )
        for cnpj in sorted({str(cnpj).strip() for cnpj in orgaos if str(cnpj).strip()})
    ]


def janelas_publicacoes(
    modalidades: tuple[int, ...] = MODALIDADES_PADRAO,
    ano: int = 2025,
) -> list[Janela]:
    janelas: list[Janela] = []
    for modalidade in modalidades:
        for mes in range(1, 13):
            inicio = f"{ano}{mes:02d}01"
            fim_dia = 31 if mes in (1, 3, 5, 7, 8, 10, 12) else 30 if mes != 2 else 28
            fim = f"{ano}{mes:02d}{fim_dia:02d}"
            janelas.append(Janela(
                id=f"contratacao:{ano}-{mes:02d}:mod{modalidade}",
                endpoint=ENDPOINT_PUBLICACOES,
                params={
                    "dataInicial": inicio,
                    "dataFinal": fim,
                    "codigoModalidadeContratacao": modalidade,
                    "uf": "RJ",
                    "tamanhoPagina": TAMANHO_PAGINA_PUBLICACOES,
                },
            ))
    return janelas


def janelas_atas(orgaos: list[str], ano: int = 2025) -> list[Janela]:
    '''Atas de registro de preço REGISTRADAS em ``ano`` (``anoAta == ano``).

    A API de consulta filtra atas por **período de vigência** (não pelo ano da
    ata) e rejeita períodos maiores que 365 dias, então cada órgão tem uma
    janela por ano de vigência — ``ano`` e ``ano + 1`` como superset — e o
    filtro declarativo fecha o corte. O recorte por órgão é o parâmetro
    ``cnpj`` (a consulta de atas não filtra por UF).
    '''
    cnpjs = sorted({str(cnpj).strip() for cnpj in orgaos if str(cnpj).strip()})
    return [
        Janela(
            id=f"ata_registro_preco:{cnpj}:{ano_base}",
            endpoint=ENDPOINT_ATAS,
            params={
                "dataInicial": f"{ano_base}0101",
                "dataFinal": f"{ano_base}1231",
                "cnpj": cnpj,
                "tamanhoPagina": TAMANHO_PAGINA_ATAS,
            },
            filtro={"anoAta": ano},
        )
        for cnpj in cnpjs
        for ano_base in (ano, ano + 1)
    ]


class FalhaDeColeta(RuntimeError):
    '''Falha de uma página/janela, com a resposta original para diagnóstico.'''

    def __init__(self, mensagem: str, resposta: dict[str, Any] | None = None) -> None:
        super().__init__(mensagem)
        self.resposta = resposta or {}


def _payload_de_resposta(resposta: dict[str, Any], url: str) -> Any:
    '''Converte a resposta do source_catalog em payload.

    A API do PNCP devolve **204 "resposta sem conteúdo"** para janela sem
    registros: é resultado vazio (fim da paginação), não falha.
    '''
    if resposta.get("json") is not None:
        return resposta["json"]
    if resposta.get("http_status") == 204 or resposta.get("observacao") == "resposta sem conteúdo":
        return {"data": []}
    raise FalhaDeColeta(
        f"{url}: {resposta.get('erro') or resposta.get('status') or 'resposta JSON ausente'}",
        resposta,
    )


def _e_429(exc: FalhaDeColeta) -> bool:
    return str(exc.resposta.get("http_status")) == "429" or (
        "too many requests" in str(exc.resposta.get("erro") or "").casefold()
    )


def _e_fim_de_paginacao(exc: FalhaDeColeta) -> bool:
    '''400 "Página N inexistente" é fim da paginação, não falha.

    Janela cujo total é múltiplo do tamanho da página termina numa página
    cheia; a página seguinte não existe e a API responde 400 com
    "Página ... inexistente". É o mesmo caso do 204 vazio: não há mais
    registros a ler.
    '''
    resposta = exc.resposta or {}
    if str(resposta.get("http_status")) != "400":
        return False
    previa = str(resposta.get("previa") or "").casefold()
    return "página" in previa and "inexistente" in previa


def _buscar_pagina(
    url: str,
    request_fn: RequestFn,
    *,
    espera_429: float = 30.0,
    tentativas_429: int = 3,
) -> Any:
    '''429 é limite de taxa, não falha: espera e tenta de novo (backoff dobro).'''
    espera = espera_429
    for tentativa in range(1, tentativas_429 + 1):
        try:
            return request_fn(url)
        except FalhaDeColeta as exc:
            if _e_fim_de_paginacao(exc):
                return {"data": []}
            if not _e_429(exc) or tentativa == tentativas_429:
                raise
            if espera > 0:
                time.sleep(espera)
            espera *= 2
    raise AssertionError("inalcançável")


def _request_fn_padrao(url: str) -> Any:
    '''Retentativa/timeout iguais aos do caminho padrão (src/source_catalog.py).

    O nome ``source_check`` no fetch_corpus é um alias de
    ``src.source_catalog``; aqui importamos a fonte direta.
    '''
    from src.source_catalog import request_url

    return _payload_de_resposta(request_url(url, "json", attempts=3, timeout=30.0), url)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _gravar_estado(caminho: Path, estado: dict[str, Any]) -> None:
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(estado, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(caminho)


def coletar_direto(
    janelas: list[Janela],
    base: dict[str, Any],
    destino: Path,
    *,
    manifest_hash: str = "",
    request_fn: RequestFn | None = None,
    taxa_seg: float = 1.0,
    espera_429: float = 30.0,
    chunk_size: int = CHUNK_SIZE_PADRAO,
    overlap: int = OVERLAP_PADRAO,
) -> dict[str, Any]:
    '''Coleta as janelas em ``destino/documents.jsonl``, retomável por janela.

    Falha de uma janela é **registrada** (``status: falhada``) e a execução
    segue — regra do desafio: "registra falhas sem abortar". A retomada
    re-tenta as janelas não completas; nada parcial é escrito.
    '''
    request_fn = request_fn or _request_fn_padrao
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    documents = destino / "documents.jsonl"
    estado_path = destino / "estado_coleta.json"
    estado: dict[str, Any] = {"gerado_em": _agora(), "janelas": {}}
    if estado_path.exists():
        estado = json.loads(estado_path.read_text(encoding="utf-8"))
        estado.setdefault("janelas", {})

    completas = puladas = falhadas = 0
    registros_totais = 0
    ultima_chamada = 0.0
    for janela in janelas:
        ja_feita = estado["janelas"].get(janela.id) or {}
        if ja_feita.get("status") == "completa":
            puladas += 1
            registros_totais += int(ja_feita.get("registros") or 0)
            continue

        linhas: list[str] = []
        registros_janela = 0
        lidos_janela = 0
        pagina = 1
        try:
            while pagina <= MAX_PAGINAS_POR_JANELA:
                if taxa_seg > 0:
                    espera = taxa_seg - (time.monotonic() - ultima_chamada)
                    if espera > 0:
                        time.sleep(espera)
                url = _build_request_url(janela.endpoint, {**janela.params, "pagina": pagina})
                ultima_chamada = time.monotonic()
                payload = _buscar_pagina(url, request_fn, espera_429=espera_429)
                registros = _payload_records(payload)
                lidos_pagina = len(registros)
                lidos_janela += lidos_pagina
                if janela.filtro:
                    registros = [
                        registro
                        for registro in registros
                        if isinstance(registro, dict)
                        and all(
                            str(registro.get(chave)) == str(valor)
                            for chave, valor in janela.filtro.items()
                        )
                    ]
                for registro in registros:
                    if not isinstance(registro, dict):
                        continue
                    chunks, _entrada = chunk_de_registro(
                        RegistroDireto(payload=registro, origem=url, coletado_em=_agora()),
                        base,
                        manifest_hash=manifest_hash,
                        chunk_size=chunk_size,
                        overlap=overlap,
                    )
                    linhas.extend(
                        json.dumps(chunk, ensure_ascii=False, sort_keys=True) for chunk in chunks
                    )
                registros_janela += len(registros)
                if lidos_pagina < janela.tamanho_pagina:
                    break
                pagina += 1
        except Exception as exc:  # noqa: BLE001 — falha registrada, não aborta a coleta
            estado["janelas"][janela.id] = {
                "status": "falhada",
                "erro": f"{type(exc).__name__}: {exc}"[:300],
            }
            estado["gerado_em"] = _agora()
            _gravar_estado(estado_path, estado)
            falhadas += 1
            continue

        # Janela completa: append e estado juntos — a retomada não duplica.
        with documents.open("a", encoding="utf-8") as handle:
            handle.writelines(linha + "\n" for linha in linhas)
        estado["janelas"][janela.id] = {
            "status": "completa",
            "paginas": pagina,
            "registros": registros_janela,
            "registros_lidos": lidos_janela,
        }
        estado["gerado_em"] = _agora()
        _gravar_estado(estado_path, estado)
        completas += 1
        registros_totais += registros_janela

    return {
        "destino": str(destino),
        "documents": str(documents),
        "janelas": len(janelas),
        "janelas_completas_agora": completas,
        "janelas_puladas": puladas,
        "janelas_falhadas": falhadas,
        "registros": registros_totais,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-id", default="contratos_estruturado")
    parser.add_argument("--destino", type=Path, required=True)
    parser.add_argument("--orgaos", type=Path, default=None,
                        help="JSON com a lista de CNPJs (padrão: da base estruturada da v6)")
    parser.add_argument("--sem-publicacoes", action="store_true")
    parser.add_argument("--so-publicacoes", action="store_true")
    parser.add_argument("--so-atas", action="store_true",
                        help="só as janelas de atas (use com --base-id atas_conversacional)")
    parser.add_argument("--taxa-seg", type=float, default=1.0)
    parser.add_argument("--finalizar", action="store_true",
                        help="roda o finalizar_direto (embeddings/SQLite) ao terminar")
    args = parser.parse_args(argv)

    if args.so_atas and args.base_id != "atas_conversacional":
        parser.error("--so-atas exige --base-id atas_conversacional")
    if not args.so_atas and args.base_id == "atas_conversacional":
        parser.error("--base-id atas_conversacional exige --so-atas")

    manifest = load_manifest(args.manifest)
    manifest_hash = f"sha256:{hashlib.sha256(args.manifest.read_bytes()).hexdigest()}"
    base = next(
        (b for b in manifest.get("bases", []) if b.get("base_id") == args.base_id), None
    )
    if base is None:
        parser.error(f"base {args.base_id!r} não está no manifesto")

    if args.orgaos:
        orgaos = json.loads(args.orgaos.read_text(encoding="utf-8"))
    else:
        from src.structured_db import Banco

        banco = Banco()
        orgaos = [
            linha["orgao_cnpj"]
            for linha in banco.consultar(
                "SELECT DISTINCT orgao_cnpj FROM contratacoes WHERE orgao_cnpj IS NOT NULL ORDER BY orgao_cnpj"
            )
        ]

    janelas: list[Janela] = []
    if args.so_atas:
        janelas.extend(janelas_atas(orgaos))
    else:
        if not args.sem_publicacoes:
            janelas.extend(janelas_publicacoes())
        if not args.so_publicacoes:
            janelas.extend(janelas_contratos(orgaos))
    relatorio = coletar_direto(
        janelas, base, args.destino,
        manifest_hash=manifest_hash, taxa_seg=args.taxa_seg,
    )
    if args.finalizar:
        relatorio["finalizacao"] = finalizar_direto(
            args.destino, base, manifest_hash=manifest_hash
        )
    print(json.dumps(relatorio, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
