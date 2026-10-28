"""Controle de qualidade do corpus (src/corpus_quality.py) e quem usa o resultado.

Item 7 do documento de correções: atas ilegíveis fora do índice, vigência das
normas, contrato ligado à compra e valor simbólico. Nada aqui muda o texto de
um chunk (os vetores do índice são reaproveitados pelo hash do texto).
"""

from __future__ import annotations

import json

import pytest

from src import corpus_quality as qc
from src.index import build_index
from src.retrieve import _matches_filters
from src.schema import ChunkMetadata, Natureza
from src.generate import _cabecalho
from src.structured_db import _linha_contratacao

LEI_URL = "https://www.mppi.mp.br/internet/wp-content/uploads/2021/04/L14133.pdf"
COMPRA_ID = "00394502002864-1-000861/2025"
# Como o leitor de manifesto do projeto devolve: chave com as aspas.
MANIFESTO = {"bases": [{"base_id": "editais_normativo", "acesso": {"metadados_por_url": {
    f'"{LEI_URL}"': {
        "titulo": "Lei nº 14.133/2021", "tipo_documento": "lei_federal",
        "numero_norma": "Lei 14.133/2021", "data_publicacao": "2021-04-01",
        "data_vigencia_inicio": "2021-04-01",
    },
}}}]}


def _registro(chunk_id, base_id, natureza, texto="texto legível", **extras):
    return {
        "chunk_id": chunk_id, "document_id": chunk_id.rsplit(":", 1)[0], "base_id": base_id,
        "natureza": natureza, "text": texto, "status_extracao": "ok", "source_file": f"{chunk_id}.json",
        "doc_type": "json", "sensitivity": "publico", "data_vigencia": None,
        "origem": extras.pop("origem", ""), "extras": extras,
    }


def _compra(**extras):
    return _registro("contratos_estruturado:compra:1", "contratos_estruturado", "estruturada",
                     numero_controle_pncp=COMPRA_ID, modalidade="Dispensa",
                     data_publicacao="2025-06-10T10:23:33", valor=12000.0, **extras)


def _contrato(numero="1", fornecedor="EMPRESA A LTDA", valor=5000.0):
    return _registro(f"contratos_estruturado:contrato:{numero}", "contratos_estruturado", "estruturada",
                     numero_controle_pncp=f"00394502002864-2-00000{numero}/2025",
                     numero_controle_compra=COMPRA_ID, fornecedor=fornecedor, valor=valor)


def _enriquecer(registro, *outros):
    contexto = qc._Contexto([registro, *outros], qc.metadados_declarados(MANIFESTO))
    return qc.enriquecer(registro, contexto)[0]


# --------------------------------------------------------------------------
# Texto ilegível
# --------------------------------------------------------------------------


@pytest.mark.parametrize("texto,motivo", [
    ("/0 /1 /2 /1 /3 /4 □ /9 /10 □ /11 /12 /3", "sem_letras"),
    ("8 /129 /130 /131 /132 /133 □ /132 /134 □ /135 Ata", "glifos_sem_mapa_de_caracteres"),
    ("/U0044/U0049Á/U0052/U0049/U004F /U0050/U004F/U0044/U0045/U0052", "codigos_unicode_no_lugar_das_letras"),
])
def test_glifos_sem_mapa_de_caracteres_sao_ilegiveis(texto, motivo):
    assert qc.motivo_de_texto_ilegivel(texto) == motivo


@pytest.mark.parametrize("texto", [
    # Tabela de lances: pouca letra, mas é dado útil (CNPJ, valor, data).
    "58,0000 01/11/2023 14:20:08 06.222.208/0001-31 R$ 70,7100 01/11/2023 14:20:16",
    "Art. 1º Esta Lei estabelece normas gerais de licitação e contratação.",
    "",
])
def test_tabela_de_lances_e_texto_normal_continuam_legiveis(texto):
    assert qc.motivo_de_texto_ilegivel(texto) is None


def test_codigos_unicode_sao_decodificados():
    assert qc.decodificar_codigos_unicode("/U0044/U0049Á/U0052/U0049/U004F /U0050/U004F/U0044/U0045/U0052") == "DIÁRIO PODER"


# --------------------------------------------------------------------------
# Extração (src/prepare_documents.py): página ilegível vai para o OCR
# --------------------------------------------------------------------------


@pytest.fixture
def ocr_falso(monkeypatch):
    from src import prepare_documents as prep
    chamadas = []

    def ligar(texto_lido):
        monkeypatch.setattr(prep.ocr, "disponivel", lambda: True)
        monkeypatch.setattr(prep.ocr, "motor", lambda: "tesseract:por:300")
        monkeypatch.setattr(prep.ocr, "texto_da_pagina",
                            lambda caminho, pagina: chamadas.append(pagina) or texto_lido)
    return chamadas, ligar


def test_pagina_com_glifos_vai_para_o_ocr(ocr_falso):
    from src.prepare_documents import _texto_legivel_da_pagina
    chamadas, ligar = ocr_falso
    ligar("ATA DE REGISTRO DE PREÇOS Nº 79/2023")
    assert _texto_legivel_da_pagina(None, 2, "/0 /1 /2 □ /3") == (
        "ATA DE REGISTRO DE PREÇOS Nº 79/2023", "ok_ocr", "tesseract:por:300")
    assert chamadas == [2]


def test_ocr_ilegivel_nao_troca_o_texto(ocr_falso):
    from src.prepare_documents import _texto_legivel_da_pagina
    _, ligar = ocr_falso
    ligar("□ □ /4")
    assert _texto_legivel_da_pagina(None, 0, "/0 /1 □") == ("/0 /1 □", "ok", None)


def test_codigos_unicode_sao_decodificados_sem_ocr(ocr_falso):
    from src.prepare_documents import _texto_legivel_da_pagina
    chamadas, ligar = ocr_falso
    ligar("não deveria ser chamado")
    texto, status, extracao = _texto_legivel_da_pagina(None, 0, "/U0044/U0049Á/U0052/U0049/U004F /U004F/U0046")
    assert (texto, status, extracao) == ("DIÁRIO OF", "ok", "codigos_unicode_decodificados")
    assert chamadas == []


def test_cabecalho_unicode_em_pagina_legivel_e_decodificado_sem_ocr(ocr_falso):
    from src.prepare_documents import _texto_legivel_da_pagina
    chamadas, ligar = ocr_falso
    ligar("não deveria ser chamado")
    corpo = "Art. 1º Esta Lei estabelece normas gerais. " * 100
    texto = "/U0044/U0049Á/U0052/U0049/U004F\n" + corpo
    assert qc.motivo_de_texto_ilegivel(texto) is None
    assert _texto_legivel_da_pagina(None, 0, texto) == (
        "DIÁRIO\n" + corpo, "ok", "codigos_unicode_decodificados",
    )
    assert chamadas == []


def test_numero_de_pagina_fica_junto_da_primeira_secao():
    from src.prepare_documents import _section_chunks
    texto = "08\nCLÁUSULA PRIMEIRA - OBJETO\nAquisição de material.\nCLÁUSULA SEGUNDA - VALOR\nR$ 100,00."
    chunks = _section_chunks(texto, 1200, 150)
    assert len(chunks) == 2
    assert chunks[0].startswith("08\nCLÁUSULA PRIMEIRA")
    assert all(qc.motivo_de_texto_ilegivel(chunk) is None for chunk in chunks)


def test_numero_de_pagina_nao_vira_chunk_antes_de_paragrafo_longo():
    from types import SimpleNamespace
    from src.prepare_documents import _chunks_for_unit, _section_chunks
    texto = "08\n\n" + "Características do material contratado. " * 100
    unit = SimpleNamespace(text=texto, chunking_sugerido="Texto por seção, com overlap",
                           formato="pdf", natureza="conversacional")
    genericos, _ = _chunks_for_unit(unit, 1200, 150)
    for chunks in (genericos, _section_chunks(texto, 1200, 150)):
        assert chunks[0].startswith("08\nCaracterísticas")
        assert all(qc.motivo_de_texto_ilegivel(chunk) is None for chunk in chunks)


def test_pagina_legivel_nao_passa_pelo_ocr(ocr_falso):
    from src.prepare_documents import _texto_legivel_da_pagina
    chamadas, ligar = ocr_falso
    ligar("não deveria ser chamado")
    assert _texto_legivel_da_pagina(None, 0, "Art. 1º Esta Lei") == ("Art. 1º Esta Lei", "ok", None)
    assert chamadas == []


@pytest.mark.parametrize("texto_cache,texto_esperado,paginas_ocr", [
    ("/0 /1 /2 □ /3", "Texto recuperado por OCR", True),
    ("/U0044/U0049Á/U0052/U0049/U004F\n" + "Art. 1º Texto legível. " * 100,
     "DIÁRIO\n" + "Art. 1º Texto legível. " * 100, False),
])
def test_pagina_ilegivel_guardada_no_cache_e_refeita_com_ocr(
    tmp_path, monkeypatch, ocr_falso, texto_cache, texto_esperado, paginas_ocr,
):
    # Caso da máquina com a extração antiga: o cache guardou os glifos como texto.
    import shutil
    from src import prepare_documents as prep
    raiz = qc.config.ROOT_DIR
    fonte = tmp_path / "fonte"
    fonte.mkdir()
    shutil.copy2(next((raiz / "tests" / "fixtures" / "pdf").glob("*.pdf")), fonte / "documento.pdf")
    saida, relatorio = tmp_path / "documents.jsonl", tmp_path / "report.json"
    manifesto = raiz / "corpus_manifest.yaml"
    monkeypatch.setattr(prep.ocr, "disponivel", lambda: False)
    monkeypatch.setattr(prep.ocr, "motor", lambda: "tesseract:por:300")
    prep.prepare_documents(fonte, saida, relatorio, manifesto, base_id="exemplos_teste")
    estado = prep._extraction_state_path(saida)
    unidades = [json.loads(linha) for linha in estado.read_text(encoding="utf-8").splitlines()]
    unidades[0].update(text=texto_cache, status="ok")
    estado.write_text("".join(json.dumps(u) + "\n" for u in unidades), encoding="utf-8")

    chamadas, ligar = ocr_falso
    ligar("Texto recuperado por OCR")
    monkeypatch.setattr(prep, "_file_units", lambda *a: pytest.fail("o PDF do cache não deve ser extraído inteiro"))
    prep.prepare_documents(fonte, saida, relatorio, manifesto, base_id="exemplos_teste")

    refeitas = [json.loads(linha) for linha in estado.read_text(encoding="utf-8").splitlines()]
    assert chamadas == ([int(unidades[0]["extras"]["pagina"]) - 1] if paginas_ocr else [])
    assert (refeitas[0]["status"], refeitas[0]["text"]) == (
        "ok_ocr" if paginas_ocr else "ok", texto_esperado,
    )
    assert [u["text"] for u in refeitas[1:]] == [u["text"] for u in unidades[1:]]


def test_chunk_ilegivel_recebe_status_e_motivo_sem_mudar_o_texto():
    texto = "/0 /1 /2 □ /3"
    novo = _enriquecer(_registro("atas:a:1", "atas_conversacional", "conversacional", texto=texto))
    assert novo["status_extracao"] == qc.TEXTO_ILEGIVEL
    assert novo["extras"]["qualidade_texto"] == "sem_letras"
    assert novo["text"] == texto


# --------------------------------------------------------------------------
# Vigência das normas
# --------------------------------------------------------------------------


def test_norma_recebe_a_vigencia_declarada_no_manifesto_pela_origem():
    novo = _enriquecer(_registro("editais:lei:1", "editais_normativo", "normativa", origem=LEI_URL))
    extras = novo["extras"]
    assert extras["numero_norma"] == "Lei 14.133/2021"
    assert extras["data_vigencia_inicio"] == "2021-04-01"
    assert extras["titulo_documento"] == "Lei nº 14.133/2021"
    assert extras["fonte_vigencia"] == "manifesto"
    # data_vigencia é o fim da vigência ("vigente até"): lei em vigor não tem.
    assert novo["data_vigencia"] is None


def test_edital_do_pncp_recebe_a_publicacao_da_compra_vinculada():
    edital = _registro("editais:edital:1", "editais_normativo", "normativa",
                       numero_controle_compra=COMPRA_ID, modalidade="Dispensa")
    extras = _enriquecer(edital, _compra())["extras"]
    assert extras["data_publicacao"] == "2025-06-10"
    assert extras["data_vigencia_inicio"] == "2025-06-10"
    assert extras["tipo_documento"] == "documento_de_contratacao"
    assert extras["fonte_vigencia"] == "publicacao_da_compra_no_pncp"


# --------------------------------------------------------------------------
# Contrato e compra, valor simbólico
# --------------------------------------------------------------------------


def test_contrato_herda_a_modalidade_da_compra_vinculada():
    extras = _enriquecer(_contrato(), _compra())["extras"]
    assert extras["modalidade"] == "Dispensa"
    assert extras["modalidade_fonte"] == "compra_vinculada"


def test_contrato_sem_a_compra_no_corpus_continua_sem_modalidade():
    assert "modalidade" not in _enriquecer(_contrato())["extras"]


def test_compra_herda_o_fornecedor_do_contrato():
    extras = _enriquecer(_compra(), _contrato())["extras"]
    assert extras["fornecedor"] == "EMPRESA A LTDA"
    assert extras["fornecedores_contratados"] == ["EMPRESA A LTDA"]


def test_compra_com_dois_contratados_lista_os_dois_sem_escolher_um():
    extras = _enriquecer(_compra(), _contrato("1", "EMPRESA A LTDA"), _contrato("2", "EMPRESA B LTDA"))["extras"]
    assert "fornecedor" not in extras
    assert extras["fornecedores_contratados"] == ["EMPRESA A LTDA", "EMPRESA B LTDA"]


@pytest.mark.parametrize("valor,simbolico", [(0.0, True), (0.01, True), (1.0, False), (5000.0, False)])
def test_valor_abaixo_de_um_real_e_simbolico(valor, simbolico):
    extras = _enriquecer(_contrato(valor=valor))["extras"]
    assert extras.get("valor_simbolico", False) is simbolico
    assert extras["valor"] == valor  # o valor original fica como está


def test_valor_simbolico_so_na_base_estruturada():
    pca = _registro("pca:1", "pca_agregado", "agregada", valor=0.0)
    assert "valor_simbolico" not in _enriquecer(pca)["extras"]


def test_enriquecer_de_novo_nao_muda_nada():
    outros = [_compra(), _contrato("1", "EMPRESA A LTDA"), _contrato("2", "EMPRESA B LTDA")]
    contexto = qc._Contexto(outros, qc.metadados_declarados(MANIFESTO))
    for registro in outros:
        uma_vez, _ = qc.enriquecer(registro, contexto)
        duas_vezes, mudancas = qc.enriquecer(uma_vez, contexto)
        assert duas_vezes == uma_vez and mudancas == []


# --------------------------------------------------------------------------
# Arquivos: todos os JSONL recebem o mesmo resultado
# --------------------------------------------------------------------------


def test_aplicar_grava_os_arquivos_na_mesma_ordem_e_e_idempotente(tmp_path):
    registros = [_compra(), _contrato(valor=0.0),
                 _registro("atas:a:1", "atas_conversacional", "conversacional", texto="/1 /2 □")]
    combinado, por_base = tmp_path / "documents.jsonl", tmp_path / "contratos.jsonl"
    combinado.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros), encoding="utf-8")
    por_base.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros[:2]), encoding="utf-8")

    relatorio = qc.aplicar([combinado, por_base], MANIFESTO)

    gravados = [json.loads(linha) for linha in combinado.read_text(encoding="utf-8").splitlines()]
    assert [r["chunk_id"] for r in gravados] == [r["chunk_id"] for r in registros]
    assert [r["text"] for r in gravados] == [r["text"] for r in registros]
    assert gravados[1]["extras"]["valor_simbolico"] is True
    assert gravados[2]["status_extracao"] == qc.TEXTO_ILEGIVEL
    assert json.loads(por_base.read_text(encoding="utf-8").splitlines()[1]) == gravados[1]
    assert relatorio["por_base"]["atas_conversacional"]["texto_ilegivel"] == 1
    assert relatorio["por_base"]["contratos_estruturado"]["modalidade_da_compra_vinculada"] == 1

    antes = combinado.read_bytes()
    qc.aplicar([combinado, por_base], MANIFESTO)
    assert combinado.read_bytes() == antes


def test_verificar_nao_grava_nada(tmp_path):
    arquivo = tmp_path / "documents.jsonl"
    arquivo.write_text(json.dumps(_contrato(valor=0.0)) + "\n", encoding="utf-8")
    antes = arquivo.read_bytes()
    qc.aplicar([arquivo], MANIFESTO, gravar=False)
    assert arquivo.read_bytes() == antes


# --------------------------------------------------------------------------
# Quem usa: índice, busca, prompt e SQL
# --------------------------------------------------------------------------


def test_indice_deixa_de_fora_o_texto_ilegivel(tmp_path):
    legivel = _registro("atas:a:1", "atas_conversacional", "conversacional", texto="Ata de registro de preços")
    ilegivel = qc.enriquecer(_registro("atas:a:2", "atas_conversacional", "conversacional", texto="/1 /2 □"),
                             qc._Contexto([], {}))[0]
    entrada = tmp_path / "documents.jsonl"
    entrada.write_text("".join(json.dumps(r) + "\n" for r in (legivel, ilegivel)), encoding="utf-8")

    manifesto = build_index(entrada, tmp_path / "index", lambda textos: [[1.0, 0.0] for _ in textos], model="teste")

    [base] = manifesto["bases"]
    assert base["total_chunks"] == 1
    assert base["chunks_ilegiveis_ignorados"] == 1
    assert manifesto["chunks_ilegiveis_ignorados"] == 1
    indexados = (tmp_path / "index" / "atas_conversacional" / "vectors.jsonl").read_text(encoding="utf-8")
    assert "atas:a:1" in indexados and "atas:a:2" not in indexados


def test_bm25_tambem_deixa_de_fora_o_texto_ilegivel(tmp_path):
    from src.retrieve import bm25_retrieve
    legivel = _registro("atas:a:1", "atas_conversacional", "conversacional", texto="Ata do pregão 12 de 2025")
    ilegivel = dict(_registro("atas:a:2", "atas_conversacional", "conversacional", texto="/12 /12 □ pregão"),
                    status_extracao=qc.TEXTO_ILEGIVEL)
    resultados = bm25_retrieve("pregão 12", records=[legivel, ilegivel], index_dir=tmp_path)
    assert [r.chunk_id for r in resultados] == ["atas:a:1"]


def test_filtro_de_vigencia_compara_a_data_de_inicio_da_norma():
    from datetime import date
    filtro = {"data_vigencia_inicio": {"operator": "<", "value": date(2026, 1, 1), "optional": True}}
    assert _matches_filters({"extras": {"data_vigencia_inicio": "2021-04-01"}}, filtro)
    assert not _matches_filters({"extras": {"data_vigencia_inicio": "2026-03-01"}}, filtro)
    assert _matches_filters({"extras": {}}, filtro)  # sem o campo: continua (filtro opcional)


def test_prompt_mostra_vigencia_valor_simbolico_e_modalidade_da_compra():
    metadata = ChunkMetadata(
        source_file="x", chunk_id="c1", base_id="contratos_estruturado", doc_type="json",
        natureza=Natureza.ESTRUTURADA,
        extras={"valor_simbolico": True, "modalidade": "Dispensa", "modalidade_fonte": "compra_vinculada"},
    )
    cabecalho = _cabecalho(metadata)
    assert "valor_simbolico=true" in cabecalho
    assert "modalidade=Dispensa (da compra vinculada)" in cabecalho

    norma = ChunkMetadata(source_file="x", chunk_id="c2", base_id="editais_normativo", doc_type="pdf",
                          natureza=Natureza.NORMATIVA, extras={"data_vigencia_inicio": "2021-04-01"})
    assert "vigente_desde=2021-04-01" in _cabecalho(norma)
    assert "vigencia_ate" not in _cabecalho(norma)


def test_sql_usa_o_fornecedor_herdado_pela_compra():
    registro = {"numeroControlePNCP": COMPRA_ID, "objetoCompra": "material", "modalidadeNome": "Dispensa"}
    linha = _linha_contratacao({"chunk_id": "c1", "extras": {"fornecedor": "EMPRESA A LTDA"}}, registro)
    assert linha["fornecedor"] == "EMPRESA A LTDA"
