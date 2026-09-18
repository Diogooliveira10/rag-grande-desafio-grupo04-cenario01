from __future__ import annotations

import json

import pytest

from src import fetch_corpus as fc


# --------------------------------------------------------------------------
# Filtro local
# --------------------------------------------------------------------------

def test_filtro_local_sem_filtro_aceita_tudo():
    assert fc.apply_filtro_local({"a": 1}, None) is True


def test_filtro_local_igualdade_campo_aninhado():
    registro = {"unidadeOrgao": {"ufSigla": "RJ"}}
    filtro = {"campo": "unidadeOrgao.ufSigla", "operador": "==", "valor": "RJ"}
    assert fc.apply_filtro_local(registro, filtro) is True

    registro_sp = {"unidadeOrgao": {"ufSigla": "SP"}}
    assert fc.apply_filtro_local(registro_sp, filtro) is False


def test_filtro_local_campo_ausente_nao_quebra():
    registro = {"outraCoisa": 1}
    filtro = {"campo": "unidadeOrgao.ufSigla", "operador": "==", "valor": "RJ"}
    assert fc.apply_filtro_local(registro, filtro) is False


def test_filtro_local_valor_ref_lista_vazia_retem_zero(tmp_path, monkeypatch):
    # simula data/config/cnpjs_rj.json ainda vazio (situação atual real do projeto)
    ref = tmp_path / "cnpjs_rj.json"
    ref.write_text(json.dumps({"cnpjs": []}), encoding="utf-8")
    monkeypatch.setattr(fc, "ROOT", tmp_path.parent)
    filtro = {"campo": "orgaoEntidadeCnpj", "valor_ref": str(ref.relative_to(tmp_path.parent))}
    assert fc.apply_filtro_local({"orgaoEntidadeCnpj": "123"}, filtro) is False


def test_filtro_local_valor_ref_com_cnpj_presente(tmp_path, monkeypatch):
    ref = tmp_path / "cnpjs_rj.json"
    ref.write_text(json.dumps({"cnpjs": ["11111111000199"]}), encoding="utf-8")
    monkeypatch.setattr(fc, "ROOT", tmp_path.parent)
    filtro = {"campo": "orgaoEntidadeCnpj", "valor_ref": str(ref.relative_to(tmp_path.parent))}
    assert fc.apply_filtro_local({"orgaoEntidadeCnpj": "11111111000199"}, filtro) is True


def test_filtro_local_operador_invalido_levanta_erro():
    with pytest.raises(ValueError):
        fc.apply_filtro_local({"a": 1}, {"campo": "a", "operador": "!=", "valor": 1})


# --------------------------------------------------------------------------
# Versionamento imutável / resumibilidade
# --------------------------------------------------------------------------

def test_primeira_versao_e_v1(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "RAW_DIR", tmp_path / "data" / "raw")
    versao, base_dir, retomando = fc.resolve_target_version("contratos_estruturado")
    assert versao == 1
    assert retomando is False
    assert base_dir.exists()


def test_versao_incompleta_e_retomada_nao_duplicada(tmp_path, monkeypatch):
    raw_dir = tmp_path / "data" / "raw"
    monkeypatch.setattr(fc, "RAW_DIR", raw_dir)

    versao1, base_dir1, _ = fc.resolve_target_version("contratos_estruturado")
    (base_dir1 / "pagina_00001.json").write_text("[]", encoding="utf-8")
    # não marcamos como completa -> deve retomar na mesma versão

    versao2, base_dir2, retomando2 = fc.resolve_target_version("contratos_estruturado")
    assert versao2 == versao1
    assert base_dir2 == base_dir1
    assert retomando2 is True


def test_versao_completa_nunca_e_sobrescrita(tmp_path, monkeypatch):
    raw_dir = tmp_path / "data" / "raw"
    monkeypatch.setattr(fc, "RAW_DIR", raw_dir)

    versao1, base_dir1, _ = fc.resolve_target_version("contratos_estruturado")
    fc._mark_complete(base_dir1, {"total_registros_api": 10})

    versao2, base_dir2, retomando2 = fc.resolve_target_version("contratos_estruturado")
    assert versao2 == versao1 + 1
    assert base_dir2 != base_dir1
    assert retomando2 is False
    # a versão 1 continua intacta
    assert (base_dir1 / fc.DONE_MARKER).exists()


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------

def test_checkpoint_round_trip(tmp_path):
    estado = {"pagina_atual": 3, "total_registros": 100, "total_filtrados": 40, "falhas": 1}
    fc._save_checkpoint(tmp_path, estado)
    lido = fc._load_checkpoint(tmp_path)
    assert lido == estado


def test_checkpoint_ausente_comeca_do_zero(tmp_path):
    estado = fc._load_checkpoint(tmp_path)
    assert estado["pagina_atual"] == 1


# --------------------------------------------------------------------------
# Coleta REST paginada — rede mockada (sem chamar o PNCP de verdade)
# --------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_coletar_rest_api_pagina_e_filtra_e_para_no_fim(tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "LOGS_DIR", tmp_path / "logs")
    fc._setup_logging()

    paginas = {
        1: {
            "data": [
                {"unidadeOrgao": {"ufSigla": "RJ"}, "id": 1},
                {"unidadeOrgao": {"ufSigla": "SP"}, "id": 2},
            ],
            "totalPaginas": 2,
        },
        2: {
            "data": [{"unidadeOrgao": {"ufSigla": "RJ"}, "id": 3}],
            "totalPaginas": 2,
        },
    }

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def get(self, endpoint, params, timeout):
            return _FakeResponse(paginas[params["pagina"]])

    monkeypatch.setattr(fc.requests, "Session", FakeSession)
    monkeypatch.setattr(fc.time, "sleep", lambda s: None)

    cfg = fc.BaseConfig(
        base_id="contratos_estruturado",
        natureza="estruturada",
        fonte="teste",
        acesso={
            "tipo": "rest_api",
            "endpoint": "http://fake",
            "params": {},
            "paginacao": {"campo_pagina": "pagina", "campo_tamanho": "tamanhoPagina"},
            "rate_limit_seg": 0,
            "filtro_local": {"campo": "unidadeOrgao.ufSigla", "operador": "==", "valor": "RJ"},
        },
    )

    resumo = fc.coletar_rest_api(cfg, tmp_path)

    assert resumo["total_registros_api"] == 3
    assert resumo["total_registros_retidos"] == 2  # só os 2 registros com UF == RJ
    assert (tmp_path / "pagina_00001.json").exists()
    assert (tmp_path / "pagina_00002.json").exists()

    pagina1 = json.loads((tmp_path / "pagina_00001.json").read_text(encoding="utf-8"))
    assert all(r["unidadeOrgao"]["ufSigla"] == "RJ" for r in pagina1)