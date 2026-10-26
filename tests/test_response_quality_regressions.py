"""Regressões dos logs de outubro: fixtures isoladas, sem reindexar o acervo."""
from __future__ import annotations

import json

import pytest

from src import generate as gen, query_analysis as qa, structured_db, structured_query as sq
from src.schema import Chunk, ChunkMetadata, ConfidenceLevel, Natureza
from tests.test_structured_db import escrever_documents
from tests.test_structured_query import _contrato

BASES = [Natureza.ESTRUTURADA]


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    path = tmp_path / "catalogo.json"
    path.write_text(json.dumps({
        "nomes": ["CASA DA MOEDA DO BRASIL", "BANCO CENTRAL DO BRASIL",
                  "DEFENSORIA PUBLICA DO ESTADO DO RIO DE JANEIRO"],
        "fornecedores": ["CASA DA MOEDA DO BRASIL CMB", "CASA DOS PARAFUSOS LTDA",
                         "MERENDA ESCOLAR DO SUL LTDA", "UNIODONTO DO BRASIL CENTRAL",
                         "CEAP RIO DE JANEIRO GESTAO PUBLICA LIMITADA"],
        "municipios": [], "siglas": {},
        # Valores do catálogo da v7: "casa" e "moeda" são assunto de contrato
        # (aparecem no objeto e nunca no nome do fornecedor desses contratos).
        "palavras_objetos": {"casa": [567, 0], "moeda": [43, 0], "merenda": [300, 0], "escolar": [900, 0],
                             "brasil": [2000, 10], "central": [400, 2]},
    }), encoding="utf-8")
    # O catálogo é lido em src/analysis/entities.py: a troca é feita lá.
    from src.analysis import entities
    monkeypatch.setattr(entities, "ORGAOS_CORPUS_PATH", path)
    entities._catalogo_de_orgaos.cache_clear()
    entities._palavras_dos_objetos.cache_clear()
    yield
    entities._catalogo_de_orgaos.cache_clear()
    entities._palavras_dos_objetos.cache_clear()


@pytest.mark.parametrize("query", [
    "Quanto vale o maior contrato de 2025?",
    "Quantos contratos foram assinados em 2025 no acervo do Rio de Janeiro?",
    "Qual o total dos contratos de 2025 neste acervo?",
])
def test_linguagem_da_pergunta_nao_vira_objeto(query):
    consulta = sq.detectar_consulta(query, qa._analisar(query), BASES)
    assert consulta.termos_objeto == ()


@pytest.mark.parametrize("query,limite", [
    ("Quanto vale o maior contrato de 2025?", 1),
    ("Qual o menor contrato de 2025?", 1),
    ("Quais os 5 maiores contratos de 2025?", 5),
])
def test_ranking_respeita_singular_e_limite(query, limite):
    assert sq.detectar_consulta(query, qa._analisar(query), BASES).limite == limite


def test_vale_transporte_continua_objeto():
    q = "Quais contratos de vale transporte foram assinados em 2025?"
    assert "vale" in sq.detectar_consulta(q, qa._analisar(q), BASES).termos_objeto


@pytest.mark.parametrize("q", [
    "Quais contratos têm como fornecedor a CASA DA MOEDA?",
    "Contratos da Casa da Moeda em 2025",
    "Quanto a Casa da Moeda recebeu?",
    "Contratos da CASA DA MOEDA",
])
def test_fornecedor_nao_vira_orgao(catalogo, q):
    filtros = qa._analisar(q)
    assert filtros.filtros_analiticos["fornecedor"] == "CASA DA MOEDA DO BRASIL CMB"
    consulta = sq.detectar_consulta(q, filtros, BASES)
    assert consulta.termos_objeto == () and not consulta.orgao_termos


@pytest.mark.parametrize("q", [
    "Contratos do Banco Central do Brasil acima de 100 milhões",
    "Contratações da Defensoria Pública do Estado do Rio de Janeiro",
])
def test_orgao_citado_por_inteiro_nao_vira_fornecedor(catalogo, q):
    """Nome de órgão com maiúscula não vira a empresa que tem as mesmas palavras
    ("UNIODONTO DO BRASIL CENTRAL", "CEAP RIO DE JANEIRO ... PUBLICA")."""
    assert "fornecedor" not in qa._analisar(q).filtros_analiticos


@pytest.mark.parametrize("q", [
    "Contratos de merenda escolar em 2025",
    "Merenda escolar em 2025",
])
def test_assunto_comum_em_minusculas_nao_vira_fornecedor(catalogo, q):
    """Nome de empresa com palavras de assunto ("MERENDA ESCOLAR DO SUL") só vira
    filtro escrito como nome próprio ou com gatilho ("empresa ...")."""
    assert "fornecedor" not in qa._analisar(q).filtros_analiticos


@pytest.mark.parametrize("q", [
    "Quais contratos estaduais foram assinados em 2025?",
    "Quais contratos foram assinados em 2025?",
    "Quais contratos têm como fornecedor a CASA DA MOEDA?",
])
def test_listagem_com_recorte_dispensa_busca_vetorial(q, catalogo):
    filtros = qa.resolver_referencia_temporal(q, qa._analisar(q))
    assert sq.pode_consultar_sql_direto(q, filtros, BASES)


def test_lista_sem_recorte_nao_inventa_filtro():
    q = "Quais contratos existem?"
    assert sq.detectar_consulta(q, qa._analisar(q), BASES) is None


@pytest.fixture
def base_respostas(tmp_path):
    records = [
        _contrato("11111111000191-2-000001/2025", "00000000000000", "CASA DA MOEDA DO BRASIL CMB", 100),
        _contrato("11111111000191-2-000002/2025", "00000000000000", "CASA DA MOEDA DO BRASIL CMB", 200),
        _contrato("11111111000191-2-000003/2025", "00000000000000", "OUTRA LTDA", 300),
    ]
    for i, row in enumerate(records):
        row["objetoContrato"] = "Serviços de informática"
        row["orgaoEntidade"]["razaoSocial"] = "ORGAO A" if i < 2 else "ORGAO B"
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=records)
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    return {"banco": banco, "documents_file": documents}


def responder_sql(query, base):
    filtros = qa.resolver_referencia_temporal(query, qa._analisar(query))
    chunks = sq.consultar_estruturado(query, filtros, BASES, **base)
    return gen.gerar_resposta(query, chunks, BASES, cliente=lambda _: json.dumps({"answer": "{{sql}}"}))


def test_pergunta_composta_traz_orgaos_e_total(base_respostas):
    q = "Quais órgãos contrataram serviços de informática em 2025 e qual o valor total?"
    resposta = responder_sql(q, base_respostas)
    assert "ORGAO A" in resposta.answer and "ORGAO B" in resposta.answer
    assert "R$ 600,00" in resposta.answer
    assert not resposta.is_refusal


def test_contagem_objetiva_nao_despeja_estatisticas(base_respostas):
    resposta = responder_sql("Quantos contratos foram assinados em 2025?", base_respostas)
    assert "3 registros" in resposta.answer
    assert "mediana" not in resposta.answer and "máximo" not in resposta.answer


def test_soma_objetiva_nao_despeja_estatisticas(base_respostas):
    resposta = responder_sql("Qual o valor total dos contratos assinados em 2025?", base_respostas)
    assert "R$ 600,00" in resposta.answer
    assert "mediana" not in resposta.answer


def test_ano_sem_dados_nao_parece_total_zero(base_respostas):
    resposta = responder_sql("Qual o valor total dos contratos de 2024?", base_respostas)
    assert "Não foram encontrados registros" in resposta.answer
    assert "não comprova que não existam" in resposta.answer
    assert "total não informado" not in resposta.answer


def test_ranking_identifica_contrato_e_fornecedor(base_respostas):
    resposta = responder_sql("Quanto vale o maior contrato de 2025?", base_respostas)
    assert "000003/2025" in resposta.answer and "OUTRA LTDA" in resposta.answer
    assert "None" not in resposta.answer
    assert len([line for line in resposta.answer.splitlines() if line.startswith("| 1 |")]) == 1
    assert "| 2 |" not in resposta.answer


def test_fornecedor_sql_nao_confunde_com_comprador(base_respostas, catalogo):
    resposta = responder_sql("Quais contratos têm como fornecedor a CASA DA MOEDA?", base_respostas)
    assert "2 registros" in resposta.answer
    assert "CASA DA MOEDA DO BRASIL CMB" in resposta.answer
    assert "OUTRA LTDA" not in resposta.answer


def test_cabecalho_modalidade_nao_contradiz_compra_vinculada():
    chunk = Chunk(content=json.dumps({"objetoContrato": "Material"}), metadata=ChunkMetadata(
        chunk_id="c1", base_id="contratos_estruturado", source_file="c.json", doc_type="json",
        natureza=Natureza.ESTRUTURADA,
        extras={"modalidade_fonte": "compra_vinculada", "modalidade": "Pregão - Eletrônico"},
    ))
    preparado = gen._chunks_no_orcamento([chunk])[0]
    # O corpo leva a procedência, como o cabeçalho (teste da Pâmela em
    # test_generate.py): a modalidade é da compra, não do próprio contrato.
    assert json.loads(preparado.content)["modalidade_licitacao"].startswith("Pregão - Eletrônico")
    assert "não informada" not in preparado.content


def test_recuperacao_degradada_avisa_usuario_e_limita_confianca():
    chunk = Chunk(content="Texto da norma", metadata=ChunkMetadata(
        chunk_id="c1", base_id="editais_normativo", source_file="lei.pdf", doc_type="pdf",
        natureza=Natureza.NORMATIVA, extras={"recuperacao_degradada": "bm25_sem_embedding"},
    ))
    resposta = gen.gerar_resposta("Qual a regra?", [chunk], [Natureza.NORMATIVA], cliente=lambda _: json.dumps({
        "answer": "A regra consta da norma [E1].", "is_refusal": False,
        "confidence_level": "alta", "sources_used": ["E1"],
    }))
    assert resposta.confidence_level == ConfidenceLevel.MEDIA
    assert any("BM25" in aviso for aviso in resposta.avisos)


def test_total_geral_nao_soma_apenas_os_orgaos_exibidos(tmp_path):
    records = []
    for i in range(12):
        row = _contrato(f"11111111000191-2-{i+1:06d}/2025", "00000000000000", "EMPRESA", 100)
        row["objetoContrato"] = "Serviços de informática"
        row["orgaoEntidade"]["razaoSocial"] = f"ORGAO {i}"
        records.append(row)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=records)
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    resposta = responder_sql("Quais órgãos contrataram informática em 2025 e qual o valor total?",
                             {"banco": banco, "documents_file": documents})
    assert "R$ 1.200,00" in resposta.answer
    assert "12 órgãos" in resposta.answer and "até 10 órgãos" in resposta.answer


def test_valor_extremo_e_sinalizado_sem_alterar_banco(base_respostas, monkeypatch):
    monkeypatch.setattr(sq.config, "VALUE_REVIEW_THRESHOLD", 250)
    banco = base_respostas["banco"]
    antes = banco.sqlite_path.read_bytes()
    resposta = responder_sql("Qual o valor total dos contratos de 2025?", base_respostas)
    assert "R$ 600,00" in resposta.answer and "Alerta de qualidade" in resposta.answer
    assert "sem correção ou exclusão" in resposta.answer
    assert banco.sqlite_path.read_bytes() == antes


def test_confianca_considera_todos_os_recortes():
    from tests.test_generate import _chunk_com_recorte, _recorte, _resposta_llm
    chunks = [_chunk_com_recorte(_recorte()), _chunk_com_recorte(_recorte(
        execucao="parcial", filtros_relaxados=["objeto relaxado"]))]
    resultado = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.ALTA), chunks)
    assert resultado.confidence_level == ConfidenceLevel.MEDIA
    assert any("objeto relaxado" in aviso for aviso in resultado.avisos)


@pytest.mark.parametrize("total,indice,espera_aviso", [(1, 0, False), (2, 0, True)])
def test_cobertura_de_intencoes_avisa_somente_parte_realmente_ausente(total, indice, espera_aviso):
    chunk = Chunk(content="Fato público", metadata=ChunkMetadata(
        chunk_id="c1", base_id="editais_normativo", source_file="lei.pdf", doc_type="pdf",
        natureza=Natureza.NORMATIVA, extras={"intencao": indice, "total_intencoes": total},
    ))
    resposta = gen.gerar_resposta("Qual a regra?", [chunk], [Natureza.NORMATIVA], cliente=lambda _: json.dumps({
        "answer": "A regra está no documento [E1].", "is_refusal": False,
        "confidence_level": "alta", "sources_used": ["E1"],
    }))
    assert any("Nenhuma evidência recuperada" in aviso for aviso in resposta.avisos) == espera_aviso
    assert resposta.confidence_level == (ConfidenceLevel.MEDIA if espera_aviso else ConfidenceLevel.ALTA)


def test_resposta_sem_fontes_nao_pode_alegar_alta_confianca():
    chunk = Chunk(content="Registro", metadata=ChunkMetadata(
        chunk_id="c1", base_id="editais_normativo", source_file="lei.pdf", doc_type="pdf"))
    resposta = gen.gerar_resposta("Qual a regra?", [chunk], [Natureza.NORMATIVA], cliente=lambda _: json.dumps({
        "answer": "Afirmação sem citação.", "is_refusal": False,
        "confidence_level": "alta", "sources_used": [],
    }))
    assert resposta.confidence_level == ConfidenceLevel.BAIXA
    assert any("não contém fontes verificáveis" in aviso for aviso in resposta.avisos)


def test_proveniencia_do_sistema_e_deterministica_e_distingue_sintetico(monkeypatch):
    from src import conversation
    monkeypatch.setattr(conversation, "_interpret_interaction", lambda *a, **k: pytest.fail("Não deve chamar LLM"))
    resposta = conversation.resposta_conversacional("De onde vêm os dados deste sistema?")
    assert "PNCP" in resposta.answer and "sintéticas" in resposta.answer and "snapshot" in resposta.answer
    assert "Conversas dos usuários não são fonte" in resposta.answer


def test_fora_de_escopo_nao_e_falta_de_evidencia():
    from src import router
    decisao = router._rotear("Qual é a capital da França?")
    assert router.inferir_motivo_recusa(decisao.justificativa).value == "fora_de_escopo"


@pytest.mark.parametrize("metrica,esperado", [
    ("contagem", "Pregão - Eletrônico: 10 registros"),
    ("soma", "Pregão - Eletrônico: 10 registros, total R$ 100,00"),
])
def test_agrupamento_exibe_metrica_pedida_sem_perder_evidencia_integral(metrica, esperado):
    linha = "Pregão - Eletrônico: 10 registros, total R$ 100,00, média R$ 10,00, mínimo R$ 1,00, máximo R$ 20,00"
    resultado = {"tipo": "agrupamento", "metrica": metrica, "linhas": [linha]}
    assert gen._linhas_para_exibicao(resultado) == [esperado]
    assert resultado["linhas"] == [linha]


def test_publicacao_explicita_e_distinta_da_data_de_captura():
    chunk = Chunk(content="20/04/2021 cabeçalho de impressão", metadata=ChunkMetadata(
        chunk_id="c1", base_id="editais_normativo", source_file="lei.pdf", doc_type="pdf",
        natureza=Natureza.NORMATIVA, extras={"data_publicacao": "2021-04-01"},
    ))
    cabecalho = gen._cabecalho(chunk.metadata)
    assert "publicacao_informada=2021-04-01" in cabecalho
    assert "20/04/2021" not in cabecalho
    assert "cabeçalhos de impressão" in gen._SYSTEM_PROMPT


def test_comparacao_entre_esferas_tambem_recebe_o_alerta_de_valor_extremo(base_respostas, monkeypatch):
    # A comparação fica com métrica "contagem", mas a tabela dela mostra total,
    # média e máximo: sem o alerta, um valor fora do padrão passava calado.
    monkeypatch.setattr(sq.config, "VALUE_REVIEW_THRESHOLD", 250)
    resposta = responder_sql("Compare os gastos federais e estaduais em 2025.", base_respostas)
    assert "Alerta de qualidade" in resposta.answer


def test_comparacao_entre_esferas_citadas_mostra_so_elas_na_tabela(tmp_path):
    records = []
    for i, (esfera, valor) in enumerate([("F", 100), ("F", 200), ("F", 300), ("E", 400), ("E", 500),
                                         ("E", 600), ("M", 700), ("N", 800)]):
        row = _contrato(f"11111111000191-2-{i+1:06d}/2025", "00000000000000", "EMPRESA", valor)
        row["objetoContrato"] = "Serviços de informática"
        row["orgaoEntidade"]["esferaId"] = esfera
        records.append(row)
    documents = escrever_documents(tmp_path / "documents.jsonl", contratacoes=records)
    banco = structured_db.Banco(sqlite_path=tmp_path / "base.sqlite")
    structured_db.construir_banco(documents, banco)
    resposta = responder_sql("Compare os gastos federais e estaduais em 2025.",
                             {"banco": banco, "documents_file": documents})
    tabela = resposta.answer[resposta.answer.index("| Recorte"):]
    assert "| Todas as esferas | 8 registros |" in tabela
    assert "| federal | 3 registros |" in tabela and "| estadual | 3 registros |" in tabela
    assert "| municipal |" not in tabela and "nao_se_aplica" not in tabela
