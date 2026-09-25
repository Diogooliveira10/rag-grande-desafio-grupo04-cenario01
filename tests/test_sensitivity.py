import json

import pytest

from src import generate as gen
from src.schema import Chunk, ChunkMetadata, Natureza, RefusalReason, Sensibilidade
from src.sensitivity import (
    MASCARA_CPF,
    MASCARA_EMAIL,
    MASCARA_TELEFONE,
    filtrar_chunks_permitidos,
    mascarar,
    mascarar_chunks,
)


def _chunk(chunk_id="c1", conteudo="texto", sensitivity=Sensibilidade.PUBLICO):
    return Chunk(
        content=conteudo,
        metadata=ChunkMetadata(
            source_file="ata.pdf",
            chunk_id=chunk_id,
            base_id="documentos_pncp",
            doc_type="pdf",
            natureza=Natureza.CONVERSACIONAL,
            sensitivity=sensitivity,
        ),
    )


# --------------------------------------------------------------------------
# Mascarar: CPF
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Contratado: João da Silva, CPF 123.456.789-09.",
        "CPF: 12345678909",
        "cpf nº 123.456.789-09",
        "portador do CPF-123456789-09",
    ],
)
def test_mascara_cpf(texto):
    mascarado, contagem = mascarar(texto)
    assert MASCARA_CPF in mascarado
    assert "789" not in mascarado
    assert contagem == {"cpf": 1}


def test_nao_mascara_cnpj_de_pessoa_juridica():
    texto = "Fornecedora ACME LTDA, CNPJ 12.345.678/0001-90, valor R$ 150.000,00."
    assert mascarar(texto) == (texto, {})


def test_nao_mascara_numero_de_11_digitos_sem_rotulo():
    texto = "Controle PNCP 12345678909 do processo SEI-120001/000123/2025."
    assert mascarar(texto) == (texto, {})


# --------------------------------------------------------------------------
# Mascarar: telefone
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Contato do fiscal: (21) 99876-5432.",
        "Ligar para +55 21 3333-4444 amanhã.",
        "Tel.: 3333-4444",
        "celular 21 98765 4321",
        "WhatsApp: (21)987654321",
    ],
)
def test_mascara_telefone(texto):
    mascarado, contagem = mascarar(texto)
    assert MASCARA_TELEFONE in mascarado
    assert "4444" not in mascarado and "5432" not in mascarado and "4321" not in mascarado
    assert contagem == {"telefone": 1}


def test_nao_mascara_valores_datas_e_numeros_de_contrato():
    texto = "Contrato 2025-0012, vigência 01/02/2025 a 31/01/2026, valor R$ 1.234.567,89, ano 2024-2025."
    assert mascarar(texto) == (texto, {})


# --------------------------------------------------------------------------
# Mascarar: e-mail pessoal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email",
    ["maria.souza@gmail.com", "fiscal_2024@hotmail.com.br", "joao+compras@empresa.com.br"],
)
def test_mascara_email_pessoal(email):
    mascarado, contagem = mascarar(f"Enviado por {email} em 10/03.")
    assert email not in mascarado
    assert MASCARA_EMAIL in mascarado
    assert contagem == {"email": 1}


@pytest.mark.parametrize("email", ["licitacao@saude.rj.gov.br", "protocolo@tjrj.jus.br", "ouvidoria@mprj.mp.br"])
def test_preserva_email_institucional(email):
    texto = f"Dúvidas: {email}"
    assert mascarar(texto) == (texto, {})


def test_digitos_de_email_nao_viram_telefone():
    mascarado, contagem = mascarar("contato: 21999998888@gmail.com")
    assert mascarado == f"contato: {MASCARA_EMAIL}"
    assert contagem == {"email": 1}


def test_ata_com_varios_dados_conta_por_tipo():
    ata = (
        "Presentes: Ana Lima (CPF 111.222.333-44), servidora, tel. (21) 2222-3333, "
        "e-mail ana.lima@yahoo.com.br; pregoeiro Carlos, e-mail carlos@fazenda.rj.gov.br. "
        "Vencedora: Beta Serviços LTDA, CNPJ 98.765.432/0001-10, R$ 48.900,00."
    )
    mascarado, contagem = mascarar(ata)
    assert contagem == {"cpf": 1, "telefone": 1, "email": 1}
    assert "carlos@fazenda.rj.gov.br" in mascarado
    assert "98.765.432/0001-10" in mascarado
    assert "R$ 48.900,00" in mascarado


def test_mascarar_e_idempotente():
    uma_vez, _ = mascarar("CPF 123.456.789-09, (21) 99876-5432, x@gmail.com")
    assert mascarar(uma_vez) == (uma_vez, {})


# --------------------------------------------------------------------------
# Chunks
# --------------------------------------------------------------------------


def test_filtra_chunks_restritos():
    chunks = [
        _chunk("pub", sensitivity=Sensibilidade.PUBLICO),
        _chunk("int", sensitivity=Sensibilidade.INTERNO),
        _chunk("res", sensitivity=Sensibilidade.RESTRITO),
    ]
    permitidos, descartados = filtrar_chunks_permitidos(chunks)
    assert [c.metadata.chunk_id for c in permitidos] == ["pub", "int"]
    assert descartados == 1


def test_mascarar_chunks_nao_altera_original_nem_metadados():
    original = _chunk(conteudo="CPF 123.456.789-09")
    mascarados, contagem = mascarar_chunks([original])
    assert original.content == "CPF 123.456.789-09"
    assert mascarados[0].content == f"CPF {MASCARA_CPF}"
    assert mascarados[0].metadata == original.metadata
    assert contagem == {"cpf": 1}


# --------------------------------------------------------------------------
# Integração com generate.py (LLM falso)
# --------------------------------------------------------------------------


def _resposta_llm(answer, sources):
    return json.dumps(
        {"answer": answer, "is_refusal": False, "refusal_reason": None,
         "confidence_level": "alta", "sources_used": sources}
    )


def test_llm_nunca_recebe_dado_pessoal():
    recebido = {}

    def cliente(mensagens):
        recebido["prompt"] = json.dumps(mensagens, ensure_ascii=False)
        return _resposta_llm("A ata registra a presença da fiscal.", ["c1"])

    chunk = _chunk(conteudo="Fiscal Ana, CPF 111.222.333-44, (21) 99876-5432, ana@gmail.com")
    resposta = gen.gerar_resposta("Quem estava na ata?", [chunk], [Natureza.CONVERSACIONAL], cliente=cliente)

    assert "111.222.333-44" not in recebido["prompt"]
    assert "99876-5432" not in recebido["prompt"]
    assert "ana@gmail.com" not in recebido["prompt"]
    assert MASCARA_CPF in recebido["prompt"]
    assert [s.chunk_id for s in resposta.sources_used] == ["c1"]


def test_resposta_final_e_mascarada():
    def cliente(mensagens):
        return _resposta_llm("O CPF informado é 123.456.789-09.", ["c1"])

    resposta = gen.gerar_resposta(
        "O CPF 123.456.789-09 aparece em algum contrato?",
        [_chunk()],
        [Natureza.CONVERSACIONAL],
        cliente=cliente,
    )

    assert "123.456.789-09" not in resposta.answer
    assert MASCARA_CPF in resposta.answer


def test_chunk_restrito_nao_chega_ao_llm():
    recebido = {}

    def cliente(mensagens):
        recebido["prompt"] = json.dumps(mensagens, ensure_ascii=False)
        return _resposta_llm("ok", ["pub"])

    chunks = [
        _chunk("pub", conteudo="conteúdo público"),
        _chunk("res", conteudo="conteúdo sigiloso", sensitivity=Sensibilidade.RESTRITO),
    ]
    gen.gerar_resposta("pergunta", chunks, [Natureza.CONVERSACIONAL], cliente=cliente)

    assert "conteúdo público" in recebido["prompt"]
    assert "conteúdo sigiloso" not in recebido["prompt"]


def test_so_chunks_restritos_vira_recusa_sem_chamar_llm():
    chamou = {"vezes": 0}

    def cliente(mensagens):
        chamou["vezes"] += 1
        return "{}"

    resposta = gen.gerar_resposta(
        "pergunta",
        [_chunk(sensitivity=Sensibilidade.RESTRITO)],
        [Natureza.CONVERSACIONAL],
        cliente=cliente,
    )

    assert chamou["vezes"] == 0
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA
    assert resposta.sources_used == []
