'''Coleta resumível do censo v7 alimentando o modo direto (§7 do plano v7).

Percorre janelas determinísticas da API do PNCP e acumula o ``documents.jsonl``
do modo direto **sem materializar ``data/raw/``** — cada registro vira chunk na
hora (``src.direct_build.chunk_de_registro``) e é só então descartado.

Janelas (as mesmas URLs que a v6 registrou no inventário):

- ``contrato_assinado`` — uma por órgão:
  ``/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20251231&cnpjOrgao=X``
  paginada (§3 do plano v7 mediu a escala exatamente assim);
- ``contratacao`` (publicações de compra) — mês × modalidade:
  ``/api/consulta/v1/contratacoes/publicacao?...&codigoModalidadeContratacao=M&uf=RJ``.

Retomada: ``estado_coleta.json`` guarda janelas e fingerprint da configuração.
Só se faz append depois de ler a janela inteira. Uma queda entre append e
checkpoint pode repetir registros; staging deduplica por identidade PNCP e
audita revisões. Checkpoint legado/incompatível exige destino separado.

As retentativas/paletas são as do ``src/source_check.py``
(``request_url(..., attempts=3)``) e o rate limit declarado no manifesto
(``rate_limit_seg``); nada de regra própria aqui.

Uso (CLI):

    python -m src.direct_collection --manifest corpus_manifest.yaml \
        --base-id contratos_estruturado --destino data/v7_direto \
        --orgaos orgaos_v6.json [--sem-publicacoes] [--so-publicacoes] [--so-atas]
'''

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.direct_build import (
    CHUNK_SIZE_PADRAO,
    OVERLAP_PADRAO,
    RegistroDireto,
    chunk_de_registro,
    finalizar_direto,
)
from src.fetch_corpus import _build_request_url, _payload_records, load_manifest
from src.collection_policy import PoliticaColeta, chave_unidade, periodos, tipo_instrumento

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
    uf_verificada: str = ""

    @property
    def tamanho_pagina(self) -> int:
        return int(self.params.get("tamanhoPagina") or 50)


def janelas_contratos(
    orgaos: list[str], *, inicio: str = "2025-01-01", fim: str = "2025-12-31",
    unidades: list[dict[str, Any]] | None = None,
) -> list[Janela]:
    faixas = periodos(inicio, fim)
    escopos = [(cnpj, "", "") for cnpj in sorted(set(orgaos))]
    if unidades is not None:
        escopos = sorted({(str(u["cnpj"]), str(u["codigoUnidadeAdministrativa"]), str(u["ufSigla"])) for u in unidades})
        if any(uf != "RJ" for _, _, uf in escopos):
            raise ValueError("cadastro contém unidade não RJ")
    resultado = []
    for cnpj, codigo, uf in escopos:
        for a, b in faixas:
            sufixo = (f":unidade={codigo}" if codigo else "")
            if len(faixas) > 1:
                sufixo += f":{a.isoformat()}:{b.isoformat()}"
            params = {"dataInicial": a.strftime("%Y%m%d"), "dataFinal": b.strftime("%Y%m%d"), "cnpjOrgao": cnpj, "tamanhoPagina": TAMANHO_PAGINA_CONTRATOS}
            if codigo:
                params["codigoUnidadeAdministrativa"] = codigo
            resultado.append(Janela(id=f"contrato_assinado:{cnpj}{sufixo}", endpoint=ENDPOINT_CONTRATOS, params=params, uf_verificada=uf))
    return resultado


def janelas_publicacoes(
    modalidades: tuple[int, ...] = MODALIDADES_PADRAO,
    ano: int = 2025,
) -> list[Janela]:
    janelas: list[Janela] = []
    for modalidade in modalidades:
        for mes in range(1, 13):
            inicio = f"{ano}{mes:02d}01"
            fim_dia = calendar.monthrange(ano, mes)[1]
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


def janelas_atas(orgaos: list[str], ano: int = 2025, *, unidades: list[dict[str, Any]] | None = None) -> list[Janela]:
    '''Atas de registro de preço REGISTRADAS em ``ano`` (``anoAta == ano``).

    A API de consulta filtra atas por **período de vigência** (não pelo ano da
    ata) e rejeita períodos maiores que 365 dias, então cada órgão tem uma
    janela por ano de vigência — ``ano`` e ``ano + 1`` como superset — e o
    filtro declarativo fecha o corte. O recorte por órgão é o parâmetro
    ``cnpj`` (a consulta de atas não filtra por UF).
    '''
    if unidades is not None:
        resultado = []
        for unidade in unidades:
            if unidade.get("ufSigla") != "RJ" or not unidade.get("codigoUnidadeAdministrativa"):
                raise ValueError("unidade de ata sem comprovação RJ/código")
            for janela in janelas_atas([str(unidade["cnpj"])], ano):
                codigo = str(unidade["codigoUnidadeAdministrativa"])
                janela.id += f":unidade={codigo}"
                janela.params["codigoUnidadeAdministrativa"] = codigo
                janela.uf_verificada = "RJ"
                resultado.append(janela)
        return resultado
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


def _carregar_estado(estado_path: Path) -> dict[str, Any]:
    """Checkpoint da coleta (retomável por janela), ou um estado novo."""
    if not estado_path.exists():
        return {"gerado_em": _agora(), "janelas": {}}
    estado = json.loads(estado_path.read_text(encoding="utf-8"))
    estado.setdefault("janelas", {})
    return estado


@dataclass(frozen=True)
class _ConfigDaColeta:
    """O que vale para todas as janelas de uma coleta."""

    base: dict[str, Any]
    manifest_hash: str
    request_fn: RequestFn
    taxa_seg: float
    espera_429: float
    chunk_size: int
    overlap: int
    politica: PoliticaColeta | None

    def fingerprint(self, janela: Janela) -> str:
        """Identidade da janela com as regras da coleta: muda se qualquer regra mudar."""
        dados = {"janela": asdict(janela), "politica": asdict(self.politica) if self.politica else None,
                 "manifest_hash": self.manifest_hash, "chunk_size": self.chunk_size, "overlap": self.overlap,
                 "base": self.base, "coletor_version": 2}
        return hashlib.sha256(json.dumps(dados, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class _JanelaColetada:
    """Andamento de uma janela; preenchido página a página, também quando ela falha."""

    registros: int = 0
    lidos: int = 0
    pagina: int = 1
    descartes: Counter[str] = field(default_factory=Counter)
    ultima_chamada: float | None = None


def _coletar_paginas(janela: Janela, config_coleta: _ConfigDaColeta, linhas: Any,
                     coletada: _JanelaColetada, ultima_chamada: float) -> float:
    """Pagina a janela até o fim declarado, gravando os chunks aceitos em ``linhas``.

    Devolve o horário da última chamada (para o intervalo da próxima janela);
    o mesmo horário fica em ``coletada``, para o caso de a janela falhar no meio.
    """
    pagina = 1
    while pagina <= MAX_PAGINAS_POR_JANELA:
        if config_coleta.taxa_seg > 0:
            espera = config_coleta.taxa_seg - (time.monotonic() - ultima_chamada)
            if espera > 0:
                time.sleep(espera)
        url = _build_request_url(janela.endpoint, {**janela.params, "pagina": pagina})
        ultima_chamada = time.monotonic()
        coletada.ultima_chamada = ultima_chamada
        coletada.pagina = pagina
        payload = _buscar_pagina(url, config_coleta.request_fn, espera_429=config_coleta.espera_429)
        registros = _payload_records(payload)
        lidos_pagina = len(registros)
        coletada.lidos += lidos_pagina
        for registro in _filtrar_pela_janela(registros, janela.filtro):
            if not isinstance(registro, dict):
                continue
            if not _aceito_pela_politica(registro, janela, config_coleta.politica, coletada.descartes):
                continue
            _gravar_chunks_do_registro(registro, url, config_coleta, linhas)
            coletada.registros += 1
        if _ultima_pagina(payload, pagina, lidos_pagina, janela.tamanho_pagina):
            return ultima_chamada
        pagina += 1
    raise FalhaDeColeta("teto de páginas atingido; janela incompleta")


def _filtrar_pela_janela(registros: list[Any], filtro: dict[str, Any] | None) -> list[Any]:
    """Com filtro na janela, só os registros (dicts) com os valores pedidos."""
    if not filtro:
        return registros
    return [
        registro
        for registro in registros
        if isinstance(registro, dict)
        and all(str(registro.get(chave)) == str(valor) for chave, valor in filtro.items())
    ]


def _aceito_pela_politica(registro: dict[str, Any], janela: Janela, politica: PoliticaColeta | None,
                          descartes: Counter[str]) -> bool:
    """O registro passa na política de coleta; se não, o motivo entra em ``descartes``."""
    if not politica:
        return True
    if janela.uf_verificada:
        _, codigo_retornado = chave_unidade(registro)
        if codigo_retornado != str(janela.params.get("codigoUnidadeAdministrativa") or ""):
            descartes["unidade_divergente_da_consulta"] += 1
            return False
    aceito, motivo = politica.decidir(registro, uf_resolvida=janela.uf_verificada)
    if not aceito:
        descartes[motivo] += 1
        return False
    return True


def _gravar_chunks_do_registro(registro: dict[str, Any], url: str, config_coleta: _ConfigDaColeta,
                               linhas: Any) -> None:
    chunks, _entrada = chunk_de_registro(
        RegistroDireto(payload=registro, origem=url, coletado_em=_agora()),
        config_coleta.base,
        manifest_hash=config_coleta.manifest_hash,
        chunk_size=config_coleta.chunk_size,
        overlap=config_coleta.overlap,
    )
    for chunk in chunks:
        if config_coleta.politica and "-2-" in str(registro.get("numeroControlePNCP") or ""):
            chunk.setdefault("extras", {})["tipo_instrumento"] = tipo_instrumento(registro)
        linhas.write(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n")


def _ultima_pagina(payload: Any, pagina: int, lidos_pagina: int, tamanho_pagina: int) -> bool:
    """A janela acabou nesta página? Paginação declarada inconsistente é falha."""
    total_paginas = payload.get("totalPaginas") if isinstance(payload, dict) else None
    if total_paginas is not None:
        total_paginas = int(total_paginas)
        if total_paginas < 0 or (total_paginas == 0 and lidos_pagina):
            raise FalhaDeColeta("metadados de paginação inconsistentes")
        if not lidos_pagina and pagina < total_paginas:
            raise FalhaDeColeta("página vazia antes do fim declarado")
        return pagina >= total_paginas
    return lidos_pagina < tamanho_pagina


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
    politica: PoliticaColeta | None = None,
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
    estado = _carregar_estado(estado_path)
    config_coleta = _ConfigDaColeta(base=base, manifest_hash=manifest_hash, request_fn=request_fn,
                                    taxa_seg=taxa_seg, espera_429=espera_429, chunk_size=chunk_size,
                                    overlap=overlap, politica=politica)

    # Validar todas as retomadas antes de qualquer append. Estado legado não
    # certifica a nova regra de seleção; recuperar para destino separado.
    for janela in janelas:
        salvo = estado["janelas"].get(janela.id) or {}
        if salvo.get("status") == "completa" and salvo.get("fingerprint") != config_coleta.fingerprint(janela):
            raise ValueError(f"checkpoint incompatível/legado: {janela.id}; use novo destino")
    completas = puladas = falhadas = 0
    registros_totais = 0
    ultima_chamada = 0.0
    for janela in janelas:
        ja_feita = estado["janelas"].get(janela.id) or {}
        if ja_feita.get("status") == "completa":
            puladas += 1
            registros_totais += int(ja_feita.get("registros") or 0)
            continue

        linhas = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", newline="\n", dir=destino)
        janela_coletada = _JanelaColetada()
        try:
            ultima_chamada = _coletar_paginas(janela, config_coleta, linhas, janela_coletada, ultima_chamada)
        except Exception as exc:  # noqa: BLE001 — falha registrada, não aborta a coleta
            linhas.close()
            # A chamada que falhou também conta para o intervalo da próxima janela.
            if janela_coletada.ultima_chamada is not None:
                ultima_chamada = janela_coletada.ultima_chamada
            estado["janelas"][janela.id] = {
                "status": "falhada",
                "fingerprint": config_coleta.fingerprint(janela),
                "erro": f"{type(exc).__name__}: {exc}"[:300],
            }
            estado["gerado_em"] = _agora()
            _gravar_estado(estado_path, estado)
            falhadas += 1
            continue

        # Append só após a janela inteira. Se cair entre append e checkpoint,
        # a retomada pode repetir identidade; staging deduplica sem perder origem.
        try:
            linhas.seek(0)
            with documents.open("a", encoding="utf-8", newline="\n") as handle:
                shutil.copyfileobj(linhas, handle)
        finally:
            linhas.close()
        estado["janelas"][janela.id] = {
            "status": "completa",
            "fingerprint": config_coleta.fingerprint(janela),
            "descartes": dict(janela_coletada.descartes),
            "paginas": janela_coletada.pagina,
            "registros": janela_coletada.registros,
            "registros_lidos": janela_coletada.lidos,
        }
        estado["gerado_em"] = _agora()
        _gravar_estado(estado_path, estado)
        completas += 1
        registros_totais += janela_coletada.registros

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
    parser.add_argument("--taxa-seg", type=float, default=5.0)
    parser.add_argument("--unidades", type=Path, help="cadastro oficial verificado com verified_unit_scopes")
    parser.add_argument("--catalogo-modalidades", type=Path, help="catálogo oficial versionado (JSON lista ou {modalidades: [...]})")
    parser.add_argument("--planejar", action="store_true", help="mostra janelas e política, sem rede nem escrita")
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

    unidades = None
    if args.unidades:
        cadastro = json.loads(args.unidades.read_text(encoding="utf-8"))
        if cadastro.get("verificacao_completa") is not True:
            parser.error("cadastro de unidades não está verificado")
        unidades = cadastro.get("verified_unit_scopes")
        if not unidades:
            parser.error("cadastro sem unidades RJ verificadas")
    if args.so_atas and unidades is None:
        parser.error("atas exigem --unidades para resolver o território sem inferência por nome")
    populations = (base.get("acesso") or {}).get("populations") or []
    contrato_params = next((p.get("params") or {} for p in populations if p.get("resource_type") == "contrato_assinado"), {})
    politica = PoliticaColeta(ano=int(contrato_params.get("anoAssinatura") or 2025),
        publicacao_inicio=str(contrato_params.get("dataInicial") or "2025-01-01"),
        publicacao_fim=str(contrato_params.get("dataFinal") or "2026-09-25"))
    janelas: list[Janela] = []
    if args.so_atas:
        janelas.extend(janelas_atas(orgaos, unidades=unidades))
    else:
        if not args.sem_publicacoes:
            if not args.catalogo_modalidades:
                parser.error("publicações exigem --catalogo-modalidades; lista fixa não certifica censo")
            catalogo = json.loads(args.catalogo_modalidades.read_text(encoding="utf-8"))
            valores = catalogo if isinstance(catalogo, list) else catalogo["modalidades"]
            modalidades = tuple(sorted({int(v["id"]) for v in valores}))
            janelas.extend(janelas_publicacoes(modalidades, ano=politica.ano))
        if not args.so_publicacoes:
            janelas.extend(janelas_contratos(orgaos, inicio=politica.publicacao_inicio, fim=politica.publicacao_fim, unidades=unidades))
    if args.planejar:
        print(json.dumps({"politica": asdict(politica), "janelas": [asdict(j) for j in janelas], "cobertura_institucional_comprovada": False}, ensure_ascii=False, indent=2))
        return 0
    protegidos = [Path("index"), Path("data/normalized"), Path("data/structured"), Path("data/v7_direto"), Path("data/v7_direto_atas")]
    if any(args.destino.resolve() == p.resolve() or args.destino.resolve().is_relative_to(p.resolve()) for p in protegidos):
        parser.error("destino protegido; use staging separado da v6 e das aquisições originais")
    relatorio = coletar_direto(
        janelas, base, args.destino,
        manifest_hash=manifest_hash, taxa_seg=args.taxa_seg, politica=politica,
    )
    if args.finalizar and relatorio["janelas_falhadas"]:
        parser.error("finalização recusada: há janelas falhadas/incompletas")
    if args.finalizar:
        relatorio["finalizacao"] = finalizar_direto(
            args.destino, base, manifest_hash=manifest_hash
        )
    print(json.dumps(relatorio, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
