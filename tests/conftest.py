import pytest

from src import obs


@pytest.fixture(autouse=True)
def _logs_em_diretorio_temporario(tmp_path, monkeypatch):
    """Nenhum teste escreve no logs/ real do repositório."""
    monkeypatch.setattr(obs.default_logger, "logs_dir", tmp_path / "logs")
