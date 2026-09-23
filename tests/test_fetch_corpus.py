from __future__ import annotations

import json

import pytest

from src import fetch_corpus as fc


# --------------------------------------------------------------------------
# Fixture: isola cada teste numa árvore de dados própria em tmp_path
# --------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path, monkeypatch):
    """Redireciona PROJECT_ROOT/DATA_ROOT/etc. para uma árvore descartável.

    _relative_path() exige que tudo fique sob PROJECT_ROOT, então também
    trocamos PROJECT_ROOT (não só DATA_ROOT) para o mesmo tmp_path.
    """
    monkeypatch.setattr(fc, "PROJECT_ROOT", tmp_path)
    fc._configure_data_root(tmp_path / "data")
    return tmp_path


def _write_manifest(root, text: str):
    path = root / "corpus_manifest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


MANIFEST_MINIMO = """\
scenario_id: cenario01
version: 1
bases:
  - base_id: base_a
    natureza: estruturada
    acesso:
      tipo: rest_api
      endpoint: "https://fake/v1/contratos"
      params: {}
      rate_limit_seg: 0
"""


# --------------------------------------------------------------------------
# Parsing / validação do manifesto (YAML próprio, sem pyyaml)
# --------------------------------------------------------------------------

def test_parse_manifest_yaml_le_mapa_lista_e_lista_inline():
    texto = """\
scenario_id: cenario01
version: 1
bases:
  - base_id: base_a
    natureza: estruturada
    metadados_extras: [a, b, c]
    acesso:
      tipo: rest_api
      endpoint: "https://fake"
"""
    manifest = fc._parse_manifest_yaml(texto)
    assert manifest["scenario_id"] == "cenario01"
    assert manifest["bases"][0]["base_id"] == "base_a"
    assert manifest["bases"][0]["metadados_extras"] == ["a", "b", "c"]
    assert manifest["bases"][0]["acesso"]["tipo"] == "rest_api"


def test_load_manifest_ok(project):
    path = _write_manifest(project, MANIFEST_MINIMO)
    manifest = fc.load_manifest(path)
    assert manifest["scenario_id"] == "cenario01"
    assert len(manifest["bases"]) == 1


def test_load_manifest_falha_sem_campo_obrigatorio_do_topo(project):
    texto = "version: 1\nbases:\n  - base_id: x\n    natureza: y\n    acesso: {tipo: rest_api}\n"
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="scenario_id"):
        fc.load_manifest(path)


def test_load_manifest_falha_com_base_id_duplicado(project):
    texto = """\
scenario_id: c
version: 1
bases:
  - base_id: dup
    natureza: estruturada
    acesso: {tipo: rest_api, endpoint: "https://fake/1"}
  - base_id: dup
    natureza: normativa
    acesso: {tipo: http_pdf, urls: ["https://fake/2"]}
"""
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="duplicado"):
        fc.load_manifest(path)


def test_load_manifest_falha_sem_campo_obrigatorio_da_base(project):
    texto = "scenario_id: c\nversion: 1\nbases:\n  - base_id: x\n    acesso: {tipo: rest_api}\n"
    path = _write_manifest(project, texto)
    with pytest.raises(ValueError, match="natureza"):
        fc.load_manifest(path)


# --------------------------------------------------------------------------
# Armazenamento por conteúdo (dedup por SHA-256)
# --------------------------------------------------------------------------

def _novo_contexto(project, manifest=None):
    manifest = manifest or {"scenario_id": "c", "version": 1, "bases": []}
    return fc._load_version_context(manifest, "sha256:fake", 1)


def test_save_version_bytes_deduplica_conteudo_identico(project):
    context = _novo_contexto(project)
    corpo = b'{"a": 1}'
    primeiro = fc._save_version_bytes(context, corpo, "base_a", "arq1", ".json")
    segundo = fc._save_version_bytes(context, corpo, "base_a", "arq2", ".json")

    assert primeiro["sha256"] == segundo["sha256"]
    # só um objeto físico no armazenamento de conteúdo, mesmo com dois nomes de arquivo
    objetos = list((project / "data" / "raw" / ".objects" / "sha256").rglob("*"))
    objetos_arquivo = [o for o in objetos if o.is_file() and not o.name.startswith(".")]
    assert len(objetos_arquivo) == 1


def test_save_version_bytes_conteudos_diferentes_geram_objetos_diferentes(project):
    context = _novo_contexto(project)
    a = fc._save_version_bytes(context, b"conteudo A", "base_a", "arq1", ".json")
    b = fc._save_version_bytes(context, b"conteudo B", "base_a", "arq2", ".json")
    assert a["sha256"] != b["sha256"]


# --------------------------------------------------------------------------
# Versionamento imutável
# --------------------------------------------------------------------------

def test_next_version_comeca_em_1(project):
    assert fc._next_version() == 1


def test_promote_version_nunca_sobrescreve_versao_existente(project):
    context = _novo_contexto(project)
    fc._save_version_bytes(context, b"dado", "base_a", "arq", ".json")
    fc._promote_version(context)
    assert fc._version_path(1).exists()

    # uma segunda tentativa de promover a MESMA versão precisa falhar, nunca
    # sobrescrever silenciosamente uma versão já imutável.
    outro_contexto = fc._VersionContext(
        manifest=context.manifest,
        version=1,
        manifest_hash="sha256:fake",
        staging_path=fc.RAW_ROOT / ".staging" / "v1_outro",
        checkpoint_path=fc.INVENTORY_ROOT / "checkpoints" / "v1.json",
        collected_at="2026-01-01T00:00:00+00:00",
        items={},
        hash_index={},
    )
    outro_contexto.staging_path.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        fc._promote_version(outro_contexto)


def test_promote_version_marca_checkpoint_como_promovido(project):
    context = _novo_contexto(project)
    fc._save_version_bytes(context, b"dado", "base_a", "arq", ".json")
    fc._promote_version(context)
    checkpoint = json.loads(context.checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["state"] == "promoted"


# --------------------------------------------------------------------------
# Checkpoint / resumibilidade / isolamento por manifest_hash
# --------------------------------------------------------------------------

def test_load_version_context_resume_com_mesmo_manifest_hash(project):
    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    context1 = fc._load_version_context(manifest, "sha256:abc", 1)
    saved = fc._save_version_bytes(context1, b"dado", "base_a", "arq", ".json")
    fc._base_record(context1, {"base_id": "base_a"}, "logical_1", "origin", saved, tipo="json")

    context2 = fc._load_version_context(manifest, "sha256:abc", 1)
    assert context2.items  # itens do checkpoint anterior foram carregados
    assert "logical_1" in context2.items
    assert context2.staging_path == context1.staging_path


def test_load_version_context_rejeita_manifest_diferente_no_mesmo_checkpoint(project):
    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    fc._load_version_context(manifest, "sha256:abc", 1)

    with pytest.raises(RuntimeError, match="outro manifesto"):
        fc._load_version_context(manifest, "sha256:DIFERENTE", 1)


def test_load_version_context_recusa_staging_sem_checkpoint(project):
    staging = fc.RAW_ROOT / ".staging" / "v1"
    staging.mkdir(parents=True)
    (staging / "arquivo_orfao.json").write_text("{}", encoding="utf-8")

    manifest = {"scenario_id": "c", "version": 1, "bases": []}
    with pytest.raises(RuntimeError, match="recuse sobrescrever"):
        fc._load_version_context(manifest, "sha256:abc", 1)


# --------------------------------------------------------------------------
# Coleta local (arquivo_local) — sem rede
# --------------------------------------------------------------------------

def test_collect_base_arquivo_local_e_idempotente(project):
    origem = project / "fonte_local.json"
    origem.write_text('{"x": 1}', encoding="utf-8")
    base = {
        "base_id": "base_local",
        "natureza": "estruturada",
        "acesso": {"tipo": "arquivo_local", "origem": "fonte_local.json"},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    primeira = fc._collect_base(context, base)
    assert primeira[0]["status"] == "ok"

    # segunda chamada: item já completo, não deve reprocessar (mesmo objeto de registro)
    segunda = fc._collect_base(context, base)
    assert segunda[0] is primeira[0]


def test_collect_base_arquivo_local_ausente_gera_falha_sem_lancar(project):
    base = {
        "base_id": "base_local",
        "natureza": "estruturada",
        "acesso": {"tipo": "arquivo_local", "origem": "nao_existe.json"},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})
    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "falhou"


# --------------------------------------------------------------------------
# Coleta HTTP (http_pdf) — source_catalog.download_url mockado
# --------------------------------------------------------------------------

def test_collect_http_item_sucesso_e_idempotente(project, monkeypatch):
    arquivo_baixado = project / "download_tmp.pdf"
    arquivo_baixado.write_bytes(b"%PDF-conteudo")

    chamadas = {"n": 0}

    def fake_download_url(url, destination_dir, filename_prefix, **kwargs):
        chamadas["n"] += 1
        return {"status": "baixado", "arquivo": str(arquivo_baixado), "content_type": "application/pdf"}

    monkeypatch.setattr(fc.source_check, "download_url", fake_download_url)

    base = {"base_id": "base_pdf", "natureza": "normativa", "acesso": {"tipo": "http_pdf", "urls": ["https://fake/lei.pdf"]}}
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "ok"
    assert chamadas["n"] == 1

    # roda de novo: item já concluído, não deve baixar outra vez
    fc._collect_base(context, base)
    assert chamadas["n"] == 1


def test_collect_http_item_falha_de_download_nao_lanca_excecao(project, monkeypatch):
    def fake_download_url(url, destination_dir, filename_prefix, **kwargs):
        return {"status": "indisponivel", "erro": "timeout simulado"}

    monkeypatch.setattr(fc.source_check, "download_url", fake_download_url)

    base = {"base_id": "base_pdf", "natureza": "normativa", "acesso": {"tipo": "http_pdf", "urls": ["https://fake/lei.pdf"]}}
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_base(context, base)
    assert registros[0]["status"] == "falhou"


# --------------------------------------------------------------------------
# Coleta REST paginada — source_catalog.request_url mockado
# --------------------------------------------------------------------------

def test_collect_rest_endpoint_pagina_ate_pagina_incompleta(project, monkeypatch):
    paginas = {
        1: {"json": {"data": [{"id": 1}, {"id": 2}], "totalPaginas": None}},
        2: {"json": {"data": [{"id": 3}]}},  # menor que o tamanho de página -> última
    }
    chamadas = {"pagina_vista": []}

    def fake_request_url(url, kind, **kwargs):
        pagina = int(url.rsplit("pagina=", 1)[1].split("&")[0])
        chamadas["pagina_vista"].append(pagina)
        return paginas[pagina]

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {
            "tipo": "rest_api",
            "endpoint": "https://fake/v1/contratos",
            # "pagina" precisa estar em params para o fetch_corpus.py detectar
            # paginação automaticamente (ver _collect_rest_endpoint).
            "params": {"tamanhoPagina": 2, "pagina": 1},
            "rate_limit_seg": 0,
        },
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])

    assert chamadas["pagina_vista"] == [1, 2]
    assert [r["registros"] for r in registros] == [2, 1]
    assert all(r["status"] == "ok" for r in registros)


def test_collect_rest_endpoint_erro_de_rede_gera_falha_sem_lancar(project, monkeypatch):
    def fake_request_url(url, kind, **kwargs):
        raise ConnectionError("PNCP fora do ar")

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {"tipo": "rest_api", "endpoint": "https://fake/v1/contratos", "params": {}, "rate_limit_seg": 0},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])
    assert registros[0]["status"] == "falhou"
    assert "ConnectionError" in registros[0]["erro"]


def test_collect_rest_endpoint_com_placeholder_falha_sem_chamar_rede(project, monkeypatch):
    chamou = {"n": 0}

    def fake_request_url(url, kind, **kwargs):
        chamou["n"] += 1
        return {"json": {"data": []}}

    monkeypatch.setattr(fc.source_check, "request_url", fake_request_url)

    base = {
        "base_id": "base_rest",
        "natureza": "estruturada",
        "acesso": {"tipo": "rest_api", "endpoint": "https://fake/v1/orgaos/{cnpj}/pca", "params": {}},
    }
    context = _novo_contexto(project, {"scenario_id": "c", "version": 1, "bases": [base]})

    registros = fc._collect_rest_endpoint(context, base, "principal", base["acesso"]["endpoint"])
    assert registros[0]["status"] == "falhou"
    assert "placeholders" in registros[0]["erro"]
    assert chamou["n"] == 0