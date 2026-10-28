'''Fallback de embeddings (ECOA primário, encoder local reserva) e pausas.'''

from src.embedding_projection import embed_fn_com_fallback, embed_fn_com_pausa


def _resposta_fixa(valor):
    def embed(textos):
        return [[valor]] * len(textos)

    return embed


def test_fallback_usa_primario_quando_funciona():
    chamadas = {"primario": 0, "reserva": 0}

    def primario(textos):
        chamadas["primario"] += 1
        return [[1.0]] * len(textos)

    def reserva(textos):
        chamadas["reserva"] += 1
        return [[2.0]] * len(textos)

    embed = embed_fn_com_fallback(primario, reserva)

    assert embed(["a", "b"]) == [[1.0], [1.0]]
    assert chamadas == {"primario": 1, "reserva": 0}


def test_fallback_cai_para_reserva_e_registra_o_evento():
    eventos = []
    estatisticas = {}

    def primario(textos):
        raise TimeoutError("ECOA fora do ar")

    embed = embed_fn_com_fallback(
        primario,
        _resposta_fixa(2.0),
        estatisticas=estatisticas,
        ao_fallback=lambda erro, tamanho: eventos.append((type(erro).__name__, tamanho)),
    )

    assert embed(["a", "b", "c"]) == [[2.0], [2.0], [2.0]]
    assert eventos == [("TimeoutError", 3)]
    assert estatisticas == {"reserva": 3}


def test_fallback_conta_os_vetores_de_cada_origem():
    estatisticas = {}

    def primario(textos):
        if textos and textos[0] == "falha":
            raise RuntimeError("sem resposta")
        return [[1.0]] * len(textos)

    embed = embed_fn_com_fallback(primario, _resposta_fixa(2.0), estatisticas=estatisticas)

    assert embed(["a", "b"]) == [[1.0], [1.0]]
    assert embed(["falha", "c"]) == [[2.0], [2.0]]
    assert estatisticas == {"primario": 2, "reserva": 2}


def test_fallback_sem_lote_nao_chama_ninguem():
    def primario(textos):
        raise AssertionError("nao devia ser chamado")

    embed = embed_fn_com_fallback(primario, _resposta_fixa(2.0))

    assert embed([]) == []


def test_pausa_espera_depois_de_cada_lote(monkeypatch):
    dormiu = []
    monkeypatch.setattr("time.sleep", lambda segundos: dormiu.append(segundos))

    embed = embed_fn_com_pausa(_resposta_fixa(1.0), 0.5)

    embed(["a"])
    embed(["b", "c"])

    assert dormiu == [0.5, 0.5]


def test_pausa_zero_nao_espera(monkeypatch):
    dormiu = []
    monkeypatch.setattr("time.sleep", lambda segundos: dormiu.append(segundos))

    embed = embed_fn_com_pausa(_resposta_fixa(1.0), 0.0)

    embed(["a"])

    assert dormiu == []
