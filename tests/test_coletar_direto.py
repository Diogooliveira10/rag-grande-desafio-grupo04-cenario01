'''Testes do coletor resumível do censo v7 (modo direto).'''

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.build_direto import finalizar_direto
from src.coletar_direto import (
    Janela,
    coletar_direto,
    janelas_contratos,
    janelas_publicacoes,
)
from src.fetch_corpus import _build_request_url

BASE = {
    "base_id": "contratos_estruturado",
    "escopo": "oficial",
    "natureza": "estruturada",
    "fonte": "PNCP - contratos assinados",
    "acesso": {"tipo": "provider", "provider": "pncp"},
    "sensitivity_default": "publico",
    "campo_vigencia": "dataVigenciaFim",
    "chunking_sugerido": "1 contrato = 1 chunk, serializado em linguagem natural",
    "metadados_extras": ["orgao", "modalidade", "valor", "municipio", "ano"],
}


def _payload_com(registros: list[dict]) -> dict:
    return {"data": registros}


def _registro(numero: str, valor: float = 100.0) -> dict:
    return {
        "numeroControlePNCP": numero,
        "objetoContrato": "Compra de teste",
        "dataAssinatura": "2025-05-05",
        "valorGlobal": valor,
        "orgaoEntidade": {"cnpj": "11111111000001", "razaoSocial": "ORGÃO DE TESTE", "esferaId": "M"},
        "unidadeOrgao": {"municipioNome": "Exemplo", "ufSigla": "RJ"},
    }


def test_janelas_contratos_sao_deterministicas_e_deduplicadas():
    janelas = janelas_contratos(["22222222000001", "11111111000001", "11111111000001"])
    assert [j.id for j in janelas] == [
        "contrato_assinado:11111111000001", "contrato_assinado:22222222000001"]
    url = _build_request_url(janelas[0].endpoint, {**janelas[0].params, "pagina": 1})
    consulta = parse_qs(urlparse(url).query)
    assert consulta["dataInicial"] == ["20250101"]
    assert consulta["dataFinal"] == ["20251231"]
    assert consulta["cnpjOrgao"] == ["11111111000001"]
    assert consulta["tamanhoPagina"] == ["500"]  # mesmo padrão das URLs da v6


def test_janelas_publicacoes_cobrem_meses_por_modalidade():
    janelas = janelas_publicacoes(modalidades=(8,))
    assert len(janelas) == 12
    julho = next(j for j in janelas if j.id == "contratacao:2025-07:mod8")
    assert julho.params == {
        "dataInicial": "20250701", "dataFinal": "20250731",
        "codigoModalidadeContratacao": 8, "uf": "RJ", "tamanhoPagina": 50,
    }
    fevereiro = next(j for j in janelas if j.id == "contratacao:2025-02:mod8")
    assert fevereiro.params["dataFinal"] == "20250228"


def _fake(paginas: dict[str, list[dict]], chamadas: list[str]):
    def request_fn(url: str) -> dict:
        chamadas.append(url)
        return _payload_com(paginas.get(url, []))
    return request_fn


def test_coleta_paginada_grava_documents_sem_raw(tmp_path):
    janela = Janela(
        id="teste:paginada", endpoint="https://exemplo.teste/contratos",
        params={"tamanhoPagina": 2, "cnpjOrgao": "11111111000001"},
    )
    url1 = _build_request_url(janela.endpoint, {**janela.params, "pagina": 1})
    url2 = _build_request_url(janela.endpoint, {**janela.params, "pagina": 2})
    chamadas: list[str] = []
    request_fn = _fake({
        url1: [_registro("11111111000001-2-000001/2025"), _registro("11111111000001-2-000002/2025")],
        url2: [_registro("11111111000001-2-000003/2025")],
    }, chamadas)

    relatorio = coletar_direto([janela], BASE, tmp_path, taxa_seg=0.0, request_fn=request_fn)
    assert relatorio["registros"] == 3
    assert len(chamadas) == 2  # página 2 veio curta e encerrou
    linhas = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 3  # 1 contrato = 1 chunk
    assert all(json.loads(l)["chunking_strategy"] == "registro_inteiro" for l in linhas)
    estado = json.loads((tmp_path / "estado_coleta.json").read_text(encoding="utf-8"))
    assert estado["janelas"][janela.id]["status"] == "completa"
    assert estado["janelas"][janela.id]["paginas"] == 2
    for intermediario in ("raw", "backups", "checkpoints", "embeddings"):
        assert not (tmp_path / intermediario).exists(), intermediario


def test_retomada_pula_janelas_completas_sem_novas_chamadas(tmp_path):
    janela = Janela(
        id="teste:retomada", endpoint="https://exemplo.teste/contratos",
        params={"tamanhoPagina": 50},
    )
    url = _build_request_url(janela.endpoint, {**janela.params, "pagina": 1})
    chamadas: list[str] = []
    coletar_direto(
        [janela], BASE, tmp_path, taxa_seg=0.0,
        request_fn=_fake({url: [_registro("11111111000001-2-000001/2025")]}, chamadas),
    )
    linhas_depois = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()

    def sem_rede(url: str) -> dict:
        raise AssertionError(f"não deveria chamar a rede: {url}")

    relatorio = coletar_direto(
        [janela], BASE, tmp_path, taxa_seg=0.0, request_fn=sem_rede
    )
    assert relatorio["janelas_puladas"] == 1
    assert relatorio["janelas_completas_agora"] == 0
    linhas_agora = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert linhas_agora == linhas_depois  # nada duplicado


def test_finalizar_deduplica_registros_repetidos_e_gera_artefatos(tmp_path):
    registro = _registro("11111111000001-2-000001/2025", valor=777.0)
    janelas = [
        Janela(id="a", endpoint="https://exemplo.teste/x", params={"tamanhoPagina": 50}),
        Janela(id="b", endpoint="https://exemplo.teste/y", params={"tamanhoPagina": 50}),
    ]
    chamadas: list[str] = []
    request_fn = _fake({
        _build_request_url("https://exemplo.teste/x", {"tamanhoPagina": 50, "pagina": 1}): [registro],
        _build_request_url("https://exemplo.teste/y", {"tamanhoPagina": 50, "pagina": 1}): [registro],
    }, chamadas)
    coletar_direto(janelas, BASE, tmp_path, taxa_seg=0.0, request_fn=request_fn)

    def embed_fake(texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 5), 1.0] for t in texts]

    relatorio = finalizar_direto(
        tmp_path, BASE, embed_fn=embed_fake, modelo="modelo-de-teste",
        manifest_hash="sha256:teste",
    )
    assert relatorio["registros"] == 1  # mesmo registro em duas janelas: 1 documento
    assert relatorio["chunks"] == 1
    assert (tmp_path / "base_estruturada.sqlite").exists()
    assert (tmp_path / "index" / "contratos_estruturado" / "vectors.faiss").exists()
    inventario = json.loads((tmp_path / "inventario_direto.json").read_text(encoding="utf-8"))
    assert inventario["por_base"][0]["volume_obtido"] == 1
    assert inventario["arquivos"][0]["hash_derivado"] is True  # sem bytes na origem: marcado


def test_resposta_204_vira_janela_vazia_nao_erro():
    '''A API do PNCP devolve 204 "resposta sem conteúdo" para janela sem
    registros — resultado vazio, não falha (achado da primeira coleta).'''
    from src.coletar_direto import _payload_de_resposta

    resposta = {"status": "disponivel", "http_status": 204, "json": None,
                "observacao": "resposta sem conteúdo"}
    assert _payload_de_resposta(resposta, "https://exemplo.teste") == {"data": []}


def test_resposta_invalida_continua_levantando():
    from src.coletar_direto import _payload_de_resposta

    try:
        _payload_de_resposta({"status": "erro_requisicao", "erro": "400"},
                             "https://exemplo.teste")
    except RuntimeError:
        pass
    else:
        raise AssertionError("resposta inválida deveria levantar")


def test_janela_falha_registra_e_continua_sem_abortar(tmp_path):
    '''Regra do desafio: registra falhas sem abortar — e a retomada re-tenta.'''
    from src.coletar_direto import FalhaDeColeta

    janelas = [
        Janela(id="ok", endpoint="https://exemplo.teste/x", params={"tamanhoPagina": 50}),
        Janela(id="ruim", endpoint="https://exemplo.teste/y", params={"tamanhoPagina": 50}),
    ]
    url_x = _build_request_url("https://exemplo.teste/x", {"tamanhoPagina": 50, "pagina": 1})
    url_y = _build_request_url("https://exemplo.teste/y", {"tamanhoPagina": 50, "pagina": 1})
    chamadas: list[str] = []

    def request_fn(url: str) -> dict:
        chamadas.append(url)
        if url == url_y:
            raise FalhaDeColeta("500 interno", {"http_status": 500})
        return _payload_com([_registro("11111111000001-2-000001/2025")])

    relatorio = coletar_direto(janelas, BASE, tmp_path, taxa_seg=0.0, request_fn=request_fn)
    assert relatorio["janelas_completas_agora"] == 1
    assert relatorio["janelas_falhadas"] == 1
    estado = json.loads((tmp_path / "estado_coleta.json").read_text(encoding="utf-8"))
    assert estado["janelas"]["ok"]["status"] == "completa"
    assert estado["janelas"]["ruim"]["status"] == "falhada"
    assert "500" in estado["janelas"]["ruim"]["erro"]
    # Nada parcial foi escrito para a janela falhada.
    linhas = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(linhas) == 1

    relatorio2 = coletar_direto(
        janelas, BASE, tmp_path, taxa_seg=0.0, request_fn=_fake({url_y: [_registro(
            "11111111000001-2-000002/2025")]}, chamadas)
    )
    assert relatorio2["janelas_puladas"] == 1  # a "ok" não reprocessa
    assert relatorio2["janelas_completas_agora"] == 1  # a "ruim" se recupera
    estado2 = json.loads((tmp_path / "estado_coleta.json").read_text(encoding="utf-8"))
    assert estado2["janelas"]["ruim"]["status"] == "completa"


def test_429_faz_backoff_e_tenta_de_novo(tmp_path):
    from src.coletar_direto import FalhaDeColeta

    janela = Janela(id="a", endpoint="https://exemplo.teste/x", params={"tamanhoPagina": 50})
    url = _build_request_url("https://exemplo.teste/x", {"tamanhoPagina": 50, "pagina": 1})
    chamadas: list[str] = []

    def request_fn(u: str) -> dict:
        chamadas.append(u)
        if len(chamadas) == 1:
            raise FalhaDeColeta("Too Many Requests", {"http_status": 429, "erro": "Too Many Requests"})
        return _payload_com([_registro("11111111000001-2-000001/2025")])

    relatorio = coletar_direto(
        [janela], BASE, tmp_path, taxa_seg=0.0, request_fn=request_fn, espera_429=0.0
    )
    assert relatorio["janelas_completas_agora"] == 1
    assert relatorio["janelas_falhadas"] == 0
    assert len(chamadas) == 2  # 429 e depois sucesso
