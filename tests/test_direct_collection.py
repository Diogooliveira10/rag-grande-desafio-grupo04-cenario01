'''Testes do coletor resumível do censo v7 (modo direto).'''

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.direct_build import finalizar_direto
from src.direct_collection import (
    FalhaDeColeta,
    Janela,
    coletar_direto,
    janelas_atas,
    janelas_contratos,
    janelas_publicacoes,
    main,
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
    from src.direct_collection import _payload_de_resposta

    resposta = {"status": "disponivel", "http_status": 204, "json": None,
                "observacao": "resposta sem conteúdo"}
    assert _payload_de_resposta(resposta, "https://exemplo.teste") == {"data": []}


def test_resposta_invalida_continua_levantando():
    from src.direct_collection import _payload_de_resposta

    try:
        _payload_de_resposta({"status": "erro_requisicao", "erro": "400"},
                             "https://exemplo.teste")
    except RuntimeError:
        pass
    else:
        raise AssertionError("resposta inválida deveria levantar")


def test_janela_falha_registra_e_continua_sem_abortar(tmp_path):
    '''Regra do desafio: registra falhas sem abortar — e a retomada re-tenta.'''
    from src.direct_collection import FalhaDeColeta

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
    from src.direct_collection import FalhaDeColeta

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


BASE_ATA = {
    "base_id": "atas_conversacional",
    "escopo": "oficial",
    "natureza": "conversacional",
    "fonte": "PNCP - atas de registro de preço",
    "acesso": {"tipo": "provider", "provider": "pncp"},
    "sensitivity_default": "publico",
    "chunking_sugerido": "chunking_generico",
    "metadados_extras": ["anoAta", "numeroAtaRegistroPreco", "vigenciaInicio", "vigenciaFim"],
}


def _registro_ata(numero: str, ano: int) -> dict:
    return {
        "numeroControlePNCPAta": numero,
        "anoAta": ano,
        "objetoContratacao": "Registro de preço de teste",
        "cnpjOrgao": "33333333000001",
        "vigenciaInicio": "2025-02-01",
        "vigenciaFim": "2026-01-31",
    }


def test_janelas_atas_sao_deterministicas_e_por_orgao():
    janelas = janelas_atas(["33333333000001", "22222222000001", "33333333000001"])
    assert [j.id for j in janelas] == [
        "ata_registro_preco:22222222000001:2025", "ata_registro_preco:22222222000001:2026",
        "ata_registro_preco:33333333000001:2025", "ata_registro_preco:33333333000001:2026"]
    url = _build_request_url(janelas[0].endpoint, {**janelas[0].params, "pagina": 1})
    consulta = parse_qs(urlparse(url).query)
    assert consulta["dataInicial"] == ["20250101"]
    assert consulta["dataFinal"] == ["20251231"]  # ano de vigência (a API rejeita > 365 dias)
    assert consulta["cnpj"] == ["22222222000001"]  # filtro de órgão da consulta
    assert consulta["tamanhoPagina"] == ["50"]
    assert janelas[0].filtro == {"anoAta": 2025}


def test_coleta_de_atas_filtra_pelo_ano_da_ata(tmp_path):
    '''O endpoint filtra por vigência; o corte por ano da ata é local e declarativo.'''
    paginas = {
        1: [
            _registro_ata("33333333000001-1-000001/2024-1", 2024),
            _registro_ata("33333333000001-1-000002/2025-1", 2025),
        ],
    }

    def request_fn(url):
        consulta = parse_qs(urlparse(url).query)
        return {"data": paginas.get(int(consulta["pagina"][0]), [])}

    janela = janelas_atas(["33333333000001"])[0]
    relatorio = coletar_direto([janela], BASE_ATA, tmp_path, request_fn=request_fn)

    assert relatorio["registros"] == 1
    documentos = (tmp_path / "documents.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(documentos) == 1
    assert "000002/2025" in documentos[0]
    estado = json.loads((tmp_path / "estado_coleta.json").read_text(encoding="utf-8"))
    janela_estado = estado["janelas"]["ata_registro_preco:33333333000001:2025"]
    assert janela_estado["registros"] == 1  # entrou no corpus
    assert janela_estado["registros_lidos"] == 2  # lidos da API


def test_pagina_inexistente_encerra_janela_que_termina_em_pagina_cheia(tmp_path):
    '''Janela cujo total é múltiplo do tamanho da página: a API responde 400
    "Página inexistente" na página seguinte, e isso é fim de paginação, não falha.'''

    def request_fn(url):
        consulta = parse_qs(urlparse(url).query)
        pagina = int(consulta["pagina"][0])
        if pagina == 1:
            return {"data": [
                _registro_ata(f"33333333000001-1-{i:06d}/2025-1", 2025)
                for i in range(1, 51)
            ]}
        raise FalhaDeColeta(
            f"{url}: erro_requisicao",
            {"http_status": 400, "previa": '{"message": "Página 2 inexistente"}', "erro": "Bad Request"},
        )

    janela = janelas_atas(["33333333000001"])[0]
    relatorio = coletar_direto([janela], BASE_ATA, tmp_path, request_fn=request_fn)

    assert relatorio["janelas_falhadas"] == 0
    assert relatorio["registros"] == 50  # página cheia lida inteira
    estado = json.loads((tmp_path / "estado_coleta.json").read_text(encoding="utf-8"))
    assert estado["janelas"][janela.id]["status"] == "completa"


def test_so_atas_exige_base_de_ata(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        main([
            "--manifest", str(tmp_path / "qualquer.yaml"),
            "--base-id", "contratos_estruturado",
            "--destino", str(tmp_path / "out"),
            "--so-atas",
        ])


def test_politica_filtra_rj_assinatura_e_preserva_empenho(tmp_path):
    from src.collection_policy import PoliticaColeta
    registros = [_registro(f"11111111000001-2-00000{i}/2025") for i in range(1, 4)]
    for r in registros:
        r["dataPublicacaoPncp"] = "2025-05-06"
    registros[0]["tipoContrato"] = {"nome": "Empenho"}
    registros[1]["unidadeOrgao"]["ufSigla"] = "DF"
    registros[2]["dataAssinatura"] = "2024-01-01"
    janela = janelas_contratos(["11111111000001"])[0]
    r = coletar_direto([janela], BASE, tmp_path, request_fn=lambda _: {"data": registros}, taxa_seg=0, politica=PoliticaColeta())
    assert r["registros"] == 1
    c = json.loads((tmp_path / "documents.jsonl").read_text())
    assert c["extras"]["tipo_instrumento"] == "empenho"


def test_teto_paginas_nao_certifica_janela(tmp_path, monkeypatch):
    monkeypatch.setattr("src.direct_collection.MAX_PAGINAS_POR_JANELA", 1)
    janela = Janela("cheia", "https://teste", {"tamanhoPagina": 1})
    r = coletar_direto([janela], BASE, tmp_path, request_fn=lambda _: {"data": [_registro("a")]}, taxa_seg=0)
    assert r["janelas_falhadas"] == 1
    assert not (tmp_path / "documents.jsonl").exists()


def test_mudar_politica_recusa_checkpoint_sem_tocar_documents(tmp_path):
    import pytest
    from src.collection_policy import PoliticaColeta
    janela = Janela("a", "https://teste", {"tamanhoPagina": 50})
    coletar_direto([janela], BASE, tmp_path, request_fn=lambda _: {"data": [_registro("a")]}, taxa_seg=0)
    antes = (tmp_path / "documents.jsonl").read_bytes()
    with pytest.raises(ValueError, match="checkpoint"):
        coletar_direto([janela], BASE, tmp_path, politica=PoliticaColeta())
    assert (tmp_path / "documents.jsonl").read_bytes() == antes


def test_janelas_assinatura_obedecem_intervalo_publicacao_e_unidade():
    j = janelas_contratos([], inicio="2025-01-01", fim="2026-09-25", unidades=[{"cnpj": "1", "codigoUnidadeAdministrativa": "2", "ufSigla": "RJ"}])
    assert len(j) == 2
    assert j[1].params["dataFinal"] == "20260925"
    assert all(x.params["codigoUnidadeAdministrativa"] == "2" for x in j)
    assert len({x.id for x in j}) == 2


def test_pagina_curta_nao_encerra_antes_total_declarado(tmp_path):
    janela = Janela("a", "https://teste", {"tamanhoPagina": 50})
    chamadas = []
    def request(url):
        pagina = int(parse_qs(urlparse(url).query)["pagina"][0])
        chamadas.append(pagina)
        return {"totalPaginas": 2, "data": [_registro(str(pagina))]}
    r = coletar_direto([janela], BASE, tmp_path, request_fn=request, taxa_seg=0)
    assert chamadas == [1, 2]
    assert r["registros"] == 2


def test_fevereiro_bissexto():
    j = janelas_publicacoes((8,), 2024)
    assert j[1].params["dataFinal"] == "20240229"


def test_ata_unidade_verificada_inclui_territorio_sem_campo_uf(tmp_path):
    from src.collection_policy import PoliticaColeta
    j = janelas_atas([], unidades=[{"cnpj": "33333333000001", "codigoUnidadeAdministrativa": "1", "ufSigla": "RJ"}])[0]
    r = coletar_direto([j], BASE_ATA, tmp_path, politica=PoliticaColeta(), taxa_seg=0, request_fn=lambda _: {"data": [{**_registro_ata("ata", 2025), "codigoUnidadeOrgao": "1"}]})
    assert r["registros"] == 1
    assert j.params["codigoUnidadeAdministrativa"] == "1"


def test_janela_que_falha_conta_para_o_intervalo_da_proxima(tmp_path, monkeypatch):
    """O intervalo entre chamadas (taxa_seg) vale também depois de uma janela que
    falhou: a chamada dela aconteceu e conta para a espera da janela seguinte."""
    from src import direct_collection as modulo
    from src.direct_collection import FalhaDeColeta

    relogio = {"agora": 100.0}
    esperas: list[float] = []

    class _Tempo:
        @staticmethod
        def monotonic() -> float:
            return relogio["agora"]

        @staticmethod
        def sleep(segundos: float) -> None:
            esperas.append(segundos)
            relogio["agora"] += segundos

    monkeypatch.setattr(modulo, "time", _Tempo)
    janelas = [
        Janela(id="ruim", endpoint="https://exemplo.teste/y", params={"tamanhoPagina": 50}),
        Janela(id="ok", endpoint="https://exemplo.teste/x", params={"tamanhoPagina": 50}),
    ]
    url_y = _build_request_url("https://exemplo.teste/y", {"tamanhoPagina": 50, "pagina": 1})

    def request_fn(url: str) -> dict:
        if url == url_y:
            raise FalhaDeColeta("500 interno", {"http_status": 500})
        return _payload_com([_registro("11111111000001-2-000001/2025")])

    relatorio = coletar_direto(janelas, BASE, tmp_path, taxa_seg=10.0, request_fn=request_fn)

    assert (relatorio["janelas_falhadas"], relatorio["janelas_completas_agora"]) == (1, 1)
    assert esperas[-1] == 10.0  # a janela "ok" esperou o intervalo inteiro depois da chamada que falhou

