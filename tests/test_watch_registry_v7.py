import json
import pytest
from src.watch_registry_v7 import acompanhar


def test_reconcilia_so_depois_lote_encerrado(tmp_path, monkeypatch):
    cadastro = tmp_path / "cadastro.json"
    cadastro.write_text(json.dumps({"execucao_finalizada": True}))
    monkeypatch.setattr("src.watch_registry_v7.reconciliar", lambda *a, **k: {"verificacao_sementes_completa": False, "orgaos_processados": 2, "orgaos_verificados": 1})
    estado = tmp_path / "estado.json"
    r = acompanhar(cadastro, tmp_path / "obs.json", tmp_path / "out", estado)
    assert r["status"] == "reconciliado_com_pendencias"
    assert r["promocao_autorizada"] is False
    assert json.loads(estado.read_text())["status"] == r["status"]


def test_timeout_nao_vira_sucesso(tmp_path, monkeypatch):
    valores = iter([0.0, 2.0])
    monkeypatch.setattr("src.watch_registry_v7.time.monotonic", lambda: next(valores))
    r = acompanhar(tmp_path / "ausente", tmp_path / "obs", tmp_path / "out", tmp_path / "estado.json", timeout=1)
    assert r["status"] == "timeout"
    assert not (tmp_path / "out").exists()


def test_arquivo_nao_final_nao_e_reconciliado(tmp_path, monkeypatch):
    cadastro = tmp_path / "cadastro.json"; cadastro.write_text('{}')
    estado = tmp_path / "estado.json"
    with pytest.raises(ValueError, match="encerramento"):
        acompanhar(cadastro, tmp_path / "obs", tmp_path / "out", estado)
    assert json.loads(estado.read_text())["status"] == "erro"
    assert not (tmp_path / "out").exists()
