"""E-mails sintéticos na geração e no golden set (item 15 das pendências de 14/10).

As conversas vêm de data/synthetic/conversacional_sintetica, a mesma fonte do
prepare. Nenhum teste chama o LLM: o cliente falso guarda o prompt e tenta
citar tudo o que pode, inclusive o que não deveria.
"""

import json
from pathlib import Path

import pytest

from eval.run_eval import evaluate_case, load_golden_set
from src import generate as gen
from src.schema import Chunk, ChunkMetadata, Natureza, RefusalReason, Sensibilidade
from src.synthetic_conversations import conversation_document

ROOT = Path(__file__).resolve().parents[1]
FONTE = ROOT / "data/synthetic/conversacional_sintetica"
GOLDEN_SET = ROOT / "eval/golden_set.jsonl"
RESTRITAS = {
    "conv_002_ses_reequilibrio_insumos", "conv_003_sepm_software_credencial",
    "conv_013_restituicao_caucao_pix", "conv_015_proderj_token_exposto",
    "conv_021_detran_estorno_duda_pix",
}


def _carregar_conversas() -> list[tuple[dict, Chunk]]:
    conversas = []
    for arquivo in sorted(FONTE.glob("*.json")):
        payload = json.loads(arquivo.read_text(encoding="utf-8"))
        texto, sensibilidade, extras = conversation_document(payload)
        conversas.append((payload, Chunk(content=texto, metadata=ChunkMetadata(
            source_file=arquivo.name, chunk_id=f"emails_sinteticos:{payload['conversation_id']}",
            base_id="emails_sinteticos", doc_type="json", natureza=Natureza.CONVERSACIONAL,
            sensitivity=sensibilidade, extras=extras,
        ))))
    return conversas


CONVERSAS = _carregar_conversas()


def _conversa(conversation_id: str) -> tuple[dict, Chunk]:
    return next(item for item in CONVERSAS if item[0]["conversation_id"] == conversation_id)


def _inicio(texto: str, tamanho: int) -> str:
    return " ".join(texto.split())[:tamanho]


def _caso(caso_id: str):
    return next(caso for caso in load_golden_set(GOLDEN_SET) if caso.id == caso_id)


def test_sao_25_conversas_e_as_5_restritas_sao_as_esperadas():
    assert len(CONVERSAS) == 25
    assert {payload["conversation_id"] for payload, chunk in CONVERSAS
            if chunk.metadata.sensitivity == Sensibilidade.RESTRITO} == RESTRITAS


# --------------------------------------------------------------------------
# Visão compacta da conversa no prompt
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload,chunk", CONVERSAS, ids=[p["conversation_id"] for p, _ in CONVERSAS])
def test_conversa_cabe_no_trecho_com_o_comeco_de_cada_mensagem(payload, chunk):
    visao = gen._representar_conversa(chunk.content, 900)
    assert visao is not None and len(visao) <= 900
    assert payload["title"] in visao
    assert "CONTEÚDO SINTÉTICO" not in visao and payload["conversation_id"] not in visao
    assert visao.count("Mensagem (") == len(payload["messages"])
    for mensagem in payload["messages"]:
        assert _inicio(mensagem["content"], 80) in visao


def test_conversa_que_cabe_entra_inteira_sem_enderecos():
    payload, chunk = _conversa("conv_011_seeduc_notificacao_atraso")
    visao = gen._representar_conversa(chunk.content, 900)
    assert _inicio(payload["messages"][0]["content"], 10_000) in visao
    assert "[...]" not in visao
    assert "Mensagem (2025-10-02, Dr. Almir Sampaio, Gestor de Contratos): Prezada Luciana" in visao
    assert visao.endswith("CNPJ: 66.554.433/0001-22")
    assert "gestao.contratos@educacao.rj.gov.br" not in visao
    assert "Canal:" not in visao


def test_conversa_longa_divide_o_espaco_e_mantem_a_resposta_do_orgao():
    payload, chunk = _conversa("conv_004_ouvidoria_jornalista_rodrigo")
    visao = gen._representar_conversa(chunk.content, 900)
    assert len(visao) == 900
    resposta_do_orgao = payload["messages"][1]["content"]
    assert _inicio(resposta_do_orgao, 150) in visao
    # O corte antigo, só no fim, apagava a segunda mensagem quase inteira.
    assert _inicio(resposta_do_orgao, 150) not in " ".join(chunk.content.split())[:900]


def test_texto_fora_do_formato_segue_o_caminho_comum(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 900)
    assert gen._representar_conversa("texto livre", 900) is None
    chunk = CONVERSAS[0][1].model_copy(update={"content": "texto   livre\n\nsem cabeçalho"})
    assert gen._chunks_no_orcamento([chunk])[0].content == "texto livre sem cabeçalho"


def test_trecho_que_nao_e_sintetico_nao_muda(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 900)
    _, conversa = _conversa("conv_001_dprj_papel_a4")
    chunk = conversa.model_copy(update={"metadata": conversa.metadata.model_copy(update={"extras": {}})})
    esperado = " ".join(chunk.content.split())[:900].rstrip() + " [...]"
    assert gen._chunks_no_orcamento([chunk])[0].content == esperado


# --------------------------------------------------------------------------
# Conversas restritas nunca chegam ao prompt nem viram fonte
# --------------------------------------------------------------------------

def test_restritas_nunca_chegam_ao_prompt_nem_viram_fonte():
    # Restritas primeiro: se o filtro falhasse, seriam elas a ocupar o prompt.
    chunks = sorted((chunk for _, chunk in CONVERSAS),
                    key=lambda chunk: chunk.metadata.sensitivity != Sensibilidade.RESTRITO)
    ids_restritos = {f"emails_sinteticos:{conversation_id}" for conversation_id in RESTRITAS}
    prompts = []

    def cliente_que_cita_tudo(mensagens):
        prompts.append("\n".join(mensagem["content"] for mensagem in mensagens))
        return json.dumps({
            "answer": "Resumo das conversas.", "is_refusal": False, "confidence_level": "media",
            "sources_used": [f"E{i}" for i in range(1, 26)] + sorted(ids_restritos),
        })

    resposta = gen.gerar_resposta("Resuma as conversas por e-mail com fornecedores.", chunks,
                                  [Natureza.CONVERSACIONAL], cliente=cliente_que_cita_tudo)

    assert len(prompts) == 1
    fontes = {fonte.chunk_id for fonte in resposta.sources_used}
    assert fontes and not fontes & ids_restritos
    for conversation_id in RESTRITAS:
        payload, _ = _conversa(conversation_id)
        assert payload["title"] not in prompts[0]
        for mensagem in payload["messages"]:
            assert _inicio(mensagem["content"], 60) not in prompts[0]


@pytest.mark.parametrize("conversation_id", sorted(RESTRITAS))
def test_so_conversa_restrita_recuperada_vira_recusa_sem_chamar_o_llm(conversation_id):
    _, chunk = _conversa(conversation_id)

    def cliente_que_nao_pode_ser_chamado(mensagens):
        raise AssertionError("conversa restrita chegou ao LLM")

    resposta = gen.gerar_resposta("Resuma esta conversa.", [chunk], [Natureza.CONVERSACIONAL],
                                  cliente=cliente_que_nao_pode_ser_chamado)

    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA
    assert resposta.sources_used == []


def test_email_publico_chega_mascarado_e_vira_fonte():
    _, alvo = _conversa("conv_011_seeduc_notificacao_atraso")
    _, restrita = _conversa("conv_013_restituicao_caucao_pix")
    prompts = []

    def cliente(mensagens):
        prompts.append(mensagens[1]["content"])
        return json.dumps({"answer": "Atraso de 15 dias, com multa de 0,5% ao dia.", "is_refusal": False,
                           "confidence_level": "alta", "sources_used": ["E1"]})

    resposta = gen.gerar_resposta(_caso("E1").pergunta, [restrita, alvo],
                                  [Natureza.CONVERSACIONAL], cliente=cliente)

    assert "15 dias" in prompts[0] and "multa moratória" in prompts[0]
    assert "331.889.001-12" not in prompts[0] and "99221-1010" not in prompts[0]
    assert [fonte.chunk_id for fonte in resposta.sources_used] == [alvo.metadata.chunk_id]
    assert gen._AVISO_FONTE_SINTETICA in resposta.avisos


def test_aviso_de_fonte_sintetica_so_quando_a_conversa_e_citada():
    _, conversa = _conversa("conv_011_seeduc_notificacao_atraso")
    registro = Chunk(content='{"objetoContrato": "Aquisição de notebooks"}', metadata=ChunkMetadata(
        source_file="contrato.json", chunk_id="contratos_estruturado:c1", base_id="contratos_estruturado",
        doc_type="json", natureza=Natureza.ESTRUTURADA))

    def cliente_que_cita(evidencia):
        return lambda mensagens: json.dumps({"answer": "Resposta.", "is_refusal": False,
                                             "confidence_level": "media", "sources_used": [evidencia]})

    # No prompt, E1 é o registro e E2 é a conversa.
    so_registro = gen.gerar_resposta("pergunta", [registro, conversa], [Natureza.CONVERSACIONAL],
                                     cliente=cliente_que_cita("E1"))
    com_conversa = gen.gerar_resposta("pergunta", [registro, conversa], [Natureza.CONVERSACIONAL],
                                      cliente=cliente_que_cita("E2"))

    assert [fonte.chunk_id for fonte in so_registro.sources_used] == ["contratos_estruturado:c1"]
    assert gen._AVISO_FONTE_SINTETICA not in so_registro.avisos
    assert [fonte.chunk_id for fonte in com_conversa.sources_used] == [conversa.metadata.chunk_id]
    assert gen._AVISO_FONTE_SINTETICA in com_conversa.avisos


# --------------------------------------------------------------------------
# Casos do golden set (E1, E2, E3)
# --------------------------------------------------------------------------

def test_golden_set_tem_um_email_publico_e_duas_recusas_de_restritos():
    e1, e2, e3 = _caso("E1"), _caso("E2"), _caso("E3")
    assert e1.bases_esperadas == ["conversacional"] and not e1.is_refusal_esperado
    assert e1.fontes_esperadas_prefixo == ["emails_sinteticos"]
    assert (e2.refusal_reason_esperado, e3.refusal_reason_esperado) == (
        "dados_bancarios_fornecedor", "credenciais_vazadas")


def test_e1_pergunta_por_conversa_publica_com_dados_pessoais_a_mascarar():
    payload, chunk = _conversa("conv_011_seeduc_notificacao_atraso")
    caso = _caso("E1")
    assert chunk.metadata.sensitivity == Sensibilidade.PUBLICO
    assert all(termo in chunk.content for termo in caso.deve_conter)
    assert all(termo in chunk.content for termo in caso.nao_deve_conter)


@pytest.mark.parametrize("caso_id,conversation_id", [
    ("E2", "conv_013_restituicao_caucao_pix"), ("E3", "conv_003_sepm_software_credencial")])
def test_recusas_protegem_dados_que_existem_nas_conversas_restritas(caso_id, conversation_id):
    _, chunk = _conversa(conversation_id)
    assert chunk.metadata.sensitivity == Sensibilidade.RESTRITO
    assert all(termo in chunk.content for termo in _caso(caso_id).nao_deve_conter)


def test_gabarito_do_e1_reprova_resposta_que_vaza_cpf(monkeypatch):
    monkeypatch.setattr("eval.run_eval._probabilidade_de_afirmacao", lambda termo, texto: None)
    caso = _caso("E1")
    resposta = {"bases_consultadas": ["conversacional"], "is_refusal": False, "confidence_level": "media",
                "answer": "A SEEDUC notificou a Nexus pelo atraso de 15 dias na entrega de 500 notebooks "
                          "e aplicou multa moratória de 0,5% ao dia.",
                "sources_used": [{"chunk_id": "emails_sinteticos:conv_011", "base_id": "emails_sinteticos"}]}
    assert evaluate_case(caso, resposta)["acertou"] is True
    vazou = {**resposta, "answer": resposta["answer"] + " Fiscal: CPF 331.889.001-12."}
    assert evaluate_case(caso, vazou)["acertou"] is False
