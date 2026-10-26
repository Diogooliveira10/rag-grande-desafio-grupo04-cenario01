"""Cadeia de hashes no log JSONL (src/log_chain.py, gravada por src/log_store.py)."""

import json

import pytest

import config
from src import log_chain
from src.log_store import EventStore


@pytest.fixture(autouse=True)
def _cadeia_ligada(monkeypatch):
    monkeypatch.setattr(config, "LOG_CHAIN_ENABLED", True)
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "chave-de-teste")


def _gravar(diretorio, nome, quantidade, inicio=0):
    caminho = diretorio / nome
    EventStore(diretorio).append_batch(
        (caminho, json.dumps({"query_id": f"q{i}", "stage": "teste", "output": f"linha {i}"}, ensure_ascii=False))
        for i in range(inicio, inicio + quantidade))
    return caminho


def _verificar(diretorio, chave="chave-de-teste"):
    return log_chain.verificar(log_chain.arquivos_do_diretorio(diretorio), chave)


def test_cem_linhas_formam_cadeia_integra(tmp_path):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 100)
    linhas = [json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines()]
    assert linhas[0]["hash_anterior"] == log_chain.GENESE
    assert all(b["hash_anterior"] == a["hash"] for a, b in zip(linhas, linhas[1:]))
    relatorio = _verificar(tmp_path)
    assert relatorio.integra and relatorio.encadeadas == 100
    assert relatorio.ultimo_hash == linhas[-1]["hash"]


def test_alterar_um_caractere_aponta_a_linha(tmp_path):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 100)
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    linhas[49] = linhas[49].replace("linha 49", "linha 4X")
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    relatorio = _verificar(tmp_path)
    assert not relatorio.integra
    assert relatorio.problemas == ["pipeline-2026-10-23.jsonl:50: conteúdo alterado (ou chave diferente)"]


def test_apagar_uma_linha_quebra_a_corrente(tmp_path):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 100)
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    del linhas[49]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    relatorio = _verificar(tmp_path)
    # A antiga linha 51 agora é a 50 e aponta para a que sumiu.
    assert len(relatorio.problemas) == 1
    assert relatorio.problemas[0].startswith("pipeline-2026-10-23.jsonl:50: hash_anterior não bate")


def test_trocar_a_ordem_quebra_a_corrente(tmp_path):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 10)
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    linhas[3], linhas[4] = linhas[4], linhas[3]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    assert not _verificar(tmp_path).integra


def test_reiniciar_continua_da_ultima_linha(tmp_path):
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 5)
    # Outro EventStore faz o papel do serviço reiniciado.
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 5, inicio=5)
    relatorio = _verificar(tmp_path)
    assert relatorio.integra and relatorio.encadeadas == 10


def test_novo_dia_continua_do_ultimo_hash_do_anterior(tmp_path):
    ontem = _gravar(tmp_path, "pipeline-2026-10-22.jsonl", 3)
    hoje = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3, inicio=3)
    ultima_de_ontem = json.loads(ontem.read_text(encoding="utf-8").splitlines()[-1])
    primeira_de_hoje = json.loads(hoje.read_text(encoding="utf-8").splitlines()[0])
    assert primeira_de_hoje["hash_anterior"] == ultima_de_ontem["hash"]
    relatorio = _verificar(tmp_path)
    assert relatorio.integra and relatorio.arquivos == 2


def test_apagar_um_dia_do_meio_e_detectado(tmp_path):
    _gravar(tmp_path, "pipeline-2026-10-21.jsonl", 2)
    meio = _gravar(tmp_path, "pipeline-2026-10-22.jsonl", 2, inicio=2)
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 2, inicio=4)
    meio.unlink()
    assert not _verificar(tmp_path).integra


def test_retencao_que_apaga_o_primeiro_dia_nao_e_quebra(tmp_path):
    primeiro = _gravar(tmp_path, "pipeline-2026-10-22.jsonl", 2)
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 2, inicio=2)
    primeiro.unlink()
    assert _verificar(tmp_path).integra


def test_linhas_anteriores_a_cadeia_ficam_como_estao(tmp_path):
    caminho = tmp_path / "pipeline-2026-10-23.jsonl"
    antigas = [json.dumps({"query_id": "velha", "n": i}) for i in range(3)]
    caminho.write_text("\n".join(antigas) + "\n", encoding="utf-8")
    _gravar(tmp_path, caminho.name, 2)
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    assert linhas[:3] == antigas
    relatorio = _verificar(tmp_path)
    assert relatorio.integra and relatorio.antigas == 3 and relatorio.encadeadas == 2
    # Alterar uma linha antiga também quebra: a primeira encadeada aponta para ela.
    linhas[2] = linhas[2].replace('"n": 2', '"n": 9')
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    assert not _verificar(tmp_path).integra


def test_linha_sem_hash_no_meio_da_cadeia_e_apontada(tmp_path):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3)
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps({"query_id": "inserida"}) + "\n")
    relatorio = _verificar(tmp_path)
    assert any("linha sem hash" in problema for problema in relatorio.problemas)


def test_chave_errada_nao_confere(tmp_path):
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3)
    assert not _verificar(tmp_path, chave="outra").integra
    assert not _verificar(tmp_path, chave="").integra


def test_sem_chave_usa_sha256(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "")
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3)
    assert _verificar(tmp_path, chave="").integra


def test_hash_nao_depende_da_ordem_das_chaves():
    a = {"b": 1, "a": "ç", "hash_anterior": log_chain.GENESE}
    b = {"hash_anterior": log_chain.GENESE, "a": "ç", "b": 1}
    assert log_chain.calcular_hash(a, "k") == log_chain.calcular_hash(b, "k")


def test_desligada_grava_como_antes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_CHAIN_ENABLED", False)
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 2)
    assert all("hash" not in json.loads(l) for l in caminho.read_text(encoding="utf-8").splitlines())


def test_indice_do_trace_continua_lendo_as_linhas(tmp_path):
    _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3)
    registros = EventStore(tmp_path).records("query_id", "q1")
    assert len(registros) == 1 and registros[0]["output"] == "linha 1"


def test_linha_maior_que_o_bloco_de_leitura(tmp_path):
    caminho = tmp_path / "pipeline-2026-10-23.jsonl"
    EventStore(tmp_path).append_batch([(caminho, json.dumps({"output": "x" * 200_000}))])
    _gravar(tmp_path, caminho.name, 2)
    assert _verificar(tmp_path).integra


def test_comando_verificar(tmp_path, capsys):
    caminho = _gravar(tmp_path, "pipeline-2026-10-23.jsonl", 3)
    assert log_chain.main(["verificar", str(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert "íntegra" in saida and "último hash:" in saida
    caminho.write_text(caminho.read_text(encoding="utf-8").replace("linha 1", "linha 7"), encoding="utf-8")
    assert log_chain.main(["verificar", str(tmp_path)]) == 1
    assert "CADEIA QUEBRADA" in capsys.readouterr().out


def test_linhas_de_antes_da_chave_continuam_validas(tmp_path, monkeypatch):
    # Os logs de 2026-10-24 e 25 foram encadeados sem chave; a chave veio depois.
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "")
    _gravar(tmp_path, "pipeline-2026-10-24.jsonl", 3)
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "chave-de-teste")
    _gravar(tmp_path, "pipeline-2026-10-26.jsonl", 3, inicio=3)
    relatorio = _verificar(tmp_path)
    assert relatorio.integra
    assert (relatorio.sem_chave, relatorio.com_chave) == (3, 3)


def test_linha_sem_chave_depois_da_primeira_com_chave_e_quebra(tmp_path, monkeypatch):
    # Quem não tem a chave não pode acrescentar linhas "sem chave" depois que ela existe.
    _gravar(tmp_path, "pipeline-2026-10-26.jsonl", 2)
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "")
    _gravar(tmp_path, "pipeline-2026-10-26.jsonl", 1, inicio=2)
    relatorio = _verificar(tmp_path)
    assert relatorio.problemas == ["pipeline-2026-10-26.jsonl:3: conteúdo alterado (ou chave diferente)"]


def test_reescrever_o_trecho_sem_chave_quebra_na_primeira_linha_com_chave(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "")
    antigo = _gravar(tmp_path, "pipeline-2026-10-24.jsonl", 3)
    monkeypatch.setattr(config, "LOG_CHAIN_KEY", "chave-de-teste")
    _gravar(tmp_path, "pipeline-2026-10-26.jsonl", 2, inicio=3)
    # Reescreve o arquivo antigo inteiro, com hashes SHA-256 válidos entre si.
    linhas = [json.loads(l) for l in antigo.read_text(encoding="utf-8").splitlines()]
    linhas[1]["output"] = "adulterado"
    anterior = log_chain.GENESE
    refeitas = []
    for registro in linhas:
        linha, anterior = log_chain.encadear(json.dumps(registro), anterior, "")
        refeitas.append(linha)
    antigo.write_text("\n".join(refeitas) + "\n", encoding="utf-8")
    relatorio = _verificar(tmp_path)
    assert relatorio.problemas[0].startswith("pipeline-2026-10-26.jsonl:1: hash_anterior não bate")
