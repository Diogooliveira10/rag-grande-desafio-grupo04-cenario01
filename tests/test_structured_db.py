"""Base estruturada em SQL, sempre em SQLite temporário (sem rede, sem Neon)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src import structured_db
from src.structured_db import Banco


def _contratacao(numero, *, esfera="M", valor_homologado=None, valor_estimado=1000.0, data="2025-03-10T10:00:00",
                 objeto="Aquisição de ambulâncias tipo D", modalidade="Pregão - Eletrônico", municipio="Niterói"):
    return {
        "numeroControlePNCP": numero,
        "anoCompra": 2025,
        "dataPublicacaoPncp": data,
        "modalidadeNome": modalidade,
        "situacaoCompraNome": "Divulgada no PNCP",
        "srp": False,
        "objetoCompra": objeto,
        "orgaoEntidade": {"cnpj": "111", "razaoSocial": f"ORGAO {numero}", "esferaId": esfera, "poderId": "E"},
        "unidadeOrgao": {"ufSigla": "RJ", "municipioNome": municipio, "codigoIbge": "3303302", "nomeUnidade": "U"},
        "amparoLegal": {"nome": "Lei 14.133/2021"},
        "valorTotalEstimado": valor_estimado,
        "valorTotalHomologado": valor_homologado,
    }


def _plano(numero, *, ano=2025, valor=5_000_000.0, unidade="Secretaria de Saúde", quantidade=None):
    return {
        "numeroControlePNCP": numero,
        "anoPca": ano,
        "cnpj": "42498600000171",
        "razaoSocial": "ESTADO DO RIO DE JANEIRO",
        "codigoUnidade": "1",
        "nomeUnidade": unidade,
        "uf": "RJ",
        "municipio": "Rio de Janeiro",
        "quantidade": quantidade if quantidade is not None else 12,
        "valorTotal": valor,
        "dataPublicacaoPncp": "2024-12-01T00:00:00",
    }


def _chunk(base_id, registro, chunk_id):
    texto = registro if isinstance(registro, str) else json.dumps(registro, ensure_ascii=False, indent=2)
    return {"chunk_id": chunk_id, "base_id": base_id, "source_file": f"data/raw/v1/{chunk_id}.json", "text": texto}


def escrever_documents(path: Path, contratacoes=(), planos=(), extras=()) -> Path:
    linhas = [_chunk("contratos_estruturado", r, f"c{i}") for i, r in enumerate(contratacoes)]
    linhas += [_chunk("pca_agregado", r, f"p{i}") for i, r in enumerate(planos)]
    linhas += list(extras)
    path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in linhas) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def documents(tmp_path):
    return escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[
            _contratacao("A-1", esfera="M", valor_homologado=900.0, valor_estimado=1000.0),
            _contratacao("A-2", esfera="E", valor_homologado=0.0, valor_estimado=2000.0),
            _contratacao("A-3", esfera="F", valor_homologado=None, valor_estimado=None, objeto="Manutenção de COMPUTADORES"),
        ],
        planos=[_plano("P-1")],
        extras=[
            _chunk("pca_agregado", "Valor total do Plano de Contratações Anual 2025: R$ 61 bi.", "indicador"),
            _chunk("editais_normativo", {"numeroControlePNCP": "N-1"}, "edital"),
        ],
    )


@pytest.fixture
def banco(tmp_path):
    return Banco(sqlite_path=tmp_path / "base.sqlite")


def test_construir_banco_carrega_so_registros_json_das_duas_bases(documents, banco):
    resumo = structured_db.construir_banco(documents, banco)

    assert (resumo["contratacoes"], resumo["pca_planos"]) == (3, 1)
    # Indicador em texto e registro de outra base não entram.
    assert banco.consultar("SELECT COUNT(*) AS n FROM pca_planos")[0]["n"] == 1


def test_pca_planos_so_recebe_planos_e_nao_compras_ou_contratos_da_base_agregada(tmp_path, banco):
    # A base agregada traz, além dos planos (tipo 0), compras (1) e contratos (2)
    # ligados a eles, sem anoPca nem valor. Só os planos entram em pca_planos.
    plano = _plano("42498600000171-0-000004/2025", valor=3_008_979_309.91)
    compra = {**_plano("42498600000171-1-000123/2025"), "anoPca": None, "valorTotal": None}
    contrato = {**_plano("42498600000171-2-000045/2025"), "anoPca": None, "valorTotal": None}
    compra_com_ano = _plano("42498600000171-1-000124/2025")  # o tipo vale mais que o campo
    sem_tipo_sem_ano = {**_plano("P-sem-ano"), "anoPca": None}
    documents = escrever_documents(tmp_path / "documents.jsonl",
                                   planos=[plano, compra, contrato, compra_com_ano, sem_tipo_sem_ano])

    resumo = structured_db.construir_banco(documents, banco)

    assert resumo["pca_planos"] == 1
    [linha] = banco.consultar("SELECT numero_controle_pncp, ano_pca, valor_total FROM pca_planos")
    assert linha == {"numero_controle_pncp": "42498600000171-0-000004/2025", "ano_pca": 2025,
                     "valor_total": 3_008_979_309.91}


def test_garantir_banco_remonta_base_de_schema_antigo_com_compras_em_pca_planos(tmp_path, banco):
    plano = _plano("42498600000171-0-000004/2025")
    compra = {**_plano("42498600000171-1-000123/2025"), "anoPca": None}
    documents = escrever_documents(tmp_path / "documents.jsonl", planos=[plano, compra])
    structured_db.construir_banco(documents, banco)
    with banco.conectar(somente_leitura=False) as conexao:
        conexao.execute("INSERT INTO pca_planos (numero_controle_pncp, chunk_id) "
                        "VALUES ('42498600000171-1-000123/2025', 'p1')")
        conexao.execute("UPDATE meta SET valor = '3' WHERE chave = 'schema_version'")
    conexao.close()

    structured_db.garantir_banco(documents, banco)

    assert banco.consultar("SELECT COUNT(*) AS n FROM pca_planos")[0]["n"] == 1


def test_compras_da_base_agregada_completam_contratacoes_no_mesmo_recorte(tmp_path, banco):
    ja_na_base = "11111111000111-1-000001/2025"
    documents = escrever_documents(
        tmp_path / "documents.jsonl",
        contratacoes=[_contratacao(ja_na_base, objeto="Versão da base de contratos")],
        planos=[
            _plano("42498600000171-0-000004/2025"),
            _contratacao(ja_na_base, objeto="Versão da base agregada"),
            _contratacao("11111111000111-1-000002/2025", objeto="Compra nova do RJ em 2025"),
            _contratacao("11111111000111-1-000003/2024", data="2024-05-10T10:00:00"),  # ano fora da base
            {**_contratacao("11111111000111-1-000004/2025"),
             "unidadeOrgao": {"ufSigla": "DF", "municipioNome": "Brasília"}},  # outro estado
            {k: v for k, v in _contratacao("11111111000111-1-000005/2025").items()
             if k != "orgaoEntidade"},  # formato reduzido, sem o órgão
        ],
    )

    resumo = structured_db.construir_banco(documents, banco)

    assert (resumo["contratacoes"], resumo["contratacoes_da_base_agregada"], resumo["pca_planos"]) == (2, 1, 1)
    linhas = {l["numero_controle_pncp"]: l["objeto"]
              for l in banco.consultar("SELECT numero_controle_pncp, objeto FROM contratacoes")}
    assert linhas == {ja_na_base: "Versão da base de contratos",
                      "11111111000111-1-000002/2025": "Compra nova do RJ em 2025"}


def test_contratacao_vira_linha_normalizada(documents, banco):
    structured_db.construir_banco(documents, banco)

    [linha] = banco.consultar("SELECT * FROM contratacoes WHERE numero_controle_pncp = ?", ["A-1"])
    assert linha["esfera"] == "municipal"
    assert linha["uf"] == "RJ"
    assert linha["ano_publicacao"] == 2025
    assert linha["mes_publicacao"] == "2025-03"
    assert linha["objeto_normalizado"] == "aquisicao de ambulancias tipo d"
    assert linha["valor"] == 900.0  # homologado tem prioridade


def test_valor_preserva_homologado_zero_e_nao_usa_estimado(documents, banco):
    structured_db.construir_banco(documents, banco)

    valores = {
        l["numero_controle_pncp"]: l["valor"]
        for l in banco.consultar("SELECT numero_controle_pncp, valor FROM contratacoes")
    }
    assert valores == {"A-1": 900.0, "A-2": 0.0, "A-3": None}


def test_valor_estimado_so_e_usado_quando_homologado_ausente():
    row = structured_db._linha_contratacao({}, _contratacao("111-1-000001/2025", valor_estimado=2000))
    assert row["valor"] == 2000


def test_cache_sqlite_anterior_e_remontado_para_preservar_zero(documents, banco):
    structured_db.construir_banco(documents, banco)
    with banco.conectar(somente_leitura=False) as conexao:
        conexao.execute("UPDATE meta SET valor = '2' WHERE chave = 'schema_version'")
        conexao.execute("UPDATE contratacoes SET valor = 2000 WHERE numero_controle_pncp = 'A-2'")
    conexao.close()
    structured_db.garantir_banco(documents, banco)
    assert banco.consultar("SELECT valor FROM contratacoes WHERE numero_controle_pncp = 'A-2'")[0]["valor"] == 0
    assert banco.consultar("SELECT valor FROM meta WHERE chave = 'schema_version'")[0]["valor"] == structured_db.SCHEMA_VERSION


def test_registro_repetido_e_deduplicado_pelo_numero_de_controle(tmp_path, banco):
    documents = escrever_documents(
        tmp_path / "documents.jsonl", contratacoes=[_contratacao("X"), _contratacao("X", valor_estimado=5.0)]
    )

    assert structured_db.construir_banco(documents, banco)["contratacoes"] == 1


def test_garantir_banco_remonta_so_quando_o_documents_muda(documents, banco, monkeypatch):
    structured_db.garantir_banco(documents, banco)
    montagens = []
    original = structured_db.construir_banco
    monkeypatch.setattr(structured_db, "construir_banco", lambda *a, **k: montagens.append(1) or original(*a, **k))

    structured_db.garantir_banco(documents, banco)
    assert montagens == []

    escrever_documents(documents, contratacoes=[_contratacao("B-1")])
    structured_db.garantir_banco(documents, banco)
    assert montagens == [1]
    assert banco.consultar("SELECT numero_controle_pncp AS n FROM contratacoes") == [{"n": "B-1"}]


def test_garantir_banco_confere_uma_vez_enquanto_o_documents_nao_muda(documents, banco, monkeypatch):
    structured_db.garantir_banco(documents, banco)
    aberturas = []
    original = Banco.conectar
    monkeypatch.setattr(Banco, "conectar", lambda self, **k: aberturas.append(1) or original(self, **k))

    structured_db.garantir_banco(documents, banco)
    assert aberturas == []

    escrever_documents(documents, contratacoes=[_contratacao("C-1")])
    structured_db.garantir_banco(documents, banco)
    assert aberturas
    assert banco.consultar("SELECT numero_controle_pncp AS n FROM contratacoes") == [{"n": "C-1"}]


def test_garantir_banco_sem_documents_falha_claramente(tmp_path, banco):
    with pytest.raises(FileNotFoundError):
        structured_db.garantir_banco(tmp_path / "nao-existe.jsonl", banco)


def test_sessao_reaproveita_a_mesma_conexao(documents, banco, monkeypatch):
    structured_db.construir_banco(documents, banco)
    aberturas = []
    original = Banco.conectar
    monkeypatch.setattr(Banco, "conectar", lambda self, **k: aberturas.append(1) or original(self, **k))

    with banco.sessao():
        banco.consultar("SELECT 1 AS um")
        banco.consultar("SELECT 2 AS dois")

    assert aberturas == [1]


def test_banco_padrao_escolhe_postgres_so_com_database_url(monkeypatch):
    monkeypatch.setattr(structured_db.config, "DATABASE_URL", "")
    assert structured_db.banco_padrao().dialeto == "sqlite"

    monkeypatch.setattr(structured_db.config, "DATABASE_URL", "postgresql://u:segredo@host.neon.tech/db?sslmode=require")
    banco = structured_db.banco_padrao()
    assert banco.dialeto == "postgres"
    assert banco.descricao() == "postgres://host.neon.tech"  # sem usuário nem senha
    assert banco.rotulo_publico() == "postgres"  # sem o host: vai para resposta e trace
    assert banco._sql("SELECT * FROM t WHERE a = ? AND b = ?") == "SELECT * FROM t WHERE a = %s AND b = %s"


def test_sem_permissao_para_remontar_usa_a_base_existente(documents, banco, monkeypatch):
    # Usuário só de leitura (rag_leitor no Neon): a base existe, mas não pode ser recriada.
    structured_db.construir_banco(documents, banco)
    escrever_documents(documents, contratacoes=[_contratacao("NOVA")])

    def sem_permissao(*args, **kwargs):
        raise PermissionError("permission denied for schema public")

    monkeypatch.setattr(structured_db, "construir_banco", sem_permissao)
    status = {}

    structured_db.garantir_banco(documents, banco, status=status)

    assert status == {"base_desatualizada": True, "erro_remontagem": "PermissionError"}
    assert banco.consultar("SELECT COUNT(*) AS n FROM contratacoes")[0]["n"] == 3  # dados antigos continuam


def test_sem_permissao_e_sem_base_nenhuma_o_erro_sobe(documents, tmp_path, monkeypatch):
    banco = Banco(url="postgresql://leitor@host/db")
    monkeypatch.setattr(structured_db, "_sha_registrado", lambda banco: None)
    monkeypatch.setattr(Banco, "sessao", lambda self: __import__("contextlib").nullcontext(self))
    monkeypatch.setattr(structured_db, "construir_banco", lambda *a, **k: (_ for _ in ()).throw(PermissionError("negado")))

    with pytest.raises(PermissionError):
        structured_db.garantir_banco(documents, banco)


def test_signed_contract_preserves_contract_value_and_distinct_signature_date():
    from src.structured_db import _linha_contratacao
    row = _linha_contratacao({"chunk_id": "c1", "extras": {"modalidade": "Pregão eletrônico"}}, {
        "numeroControlePNCP": "12345678000100-2-000001/2025",
        "dataAssinatura": "2025-12-20", "dataPublicacaoPncp": "2026-01-02",
        "objetoContrato": "Manutenção de computadores", "valorGlobal": 700000,
        "valorTotalEstimado": 900000, "nomeRazaoSocialFornecedor": "Fornecedor público",
    })
    assert row["valor"] == row["valor_contratado"] == 700000
    assert row["valor_estimado"] == 900000
    assert row["ano_assinatura"] == 2025 and row["ano_publicacao"] == 2026
    assert row["mes_assinatura"] == "2025-12" and row["mes_publicacao"] == "2026-01"
    assert row["objeto"] == "Manutenção de computadores"
    assert row["fornecedor"] == "Fornecedor público"
    assert row["modalidade"] == "Pregão eletrônico"
    assert row["tipo_registro"] == "contrato_assinado"


def test_validar_snapshot_nunca_constroi_e_cacheia_hash(documents, banco, monkeypatch):
    structured_db.construir_banco(documents, banco)
    monkeypatch.setattr(structured_db, "construir_banco", lambda *a, **k: pytest.fail("consulta não pode construir banco"))
    structured_db._hash_snapshot.cache_clear()
    hashes = []
    original = structured_db._sha256
    monkeypatch.setattr(structured_db, "_sha256", lambda p: hashes.append(p) or original(p))
    assert structured_db.validar_banco(documents, banco) is banco
    assert structured_db.validar_banco(documents, banco) is banco
    assert len(hashes) == 1
    escrever_documents(documents, contratacoes=[_contratacao("outro")])
    with pytest.raises(ValueError, match="snapshots diferentes"):
        structured_db.validar_banco(documents, banco)


def test_validar_snapshot_rejeita_schema_antigo_sem_alterar_arquivo(documents, banco):
    structured_db.construir_banco(documents, banco)
    from contextlib import closing
    with closing(banco.conectar(somente_leitura=False)) as connection, connection:
        connection.execute("UPDATE meta SET valor='v7-staging-2' WHERE chave='schema_version'")
    before = banco.sqlite_path.read_bytes()
    with pytest.raises(ValueError, match="schema SQL incompatível"):
        structured_db.validar_banco(documents, banco)
    assert banco.sqlite_path.read_bytes() == before


# Índice do objeto (FTS5 de trigramas): o mesmo resultado do LIKE, sem ler a
# tabela inteira a cada consulta.
_CONTAGEM_POR_OBJETO = f"SELECT COUNT(*) AS n FROM contratacoes WHERE {structured_db.FILTRO_DE_OBJETO}"


def _sem_indice_de_objeto(banco):
    with closing(sqlite3.connect(banco.sqlite_path)) as conexao, conexao:
        conexao.execute(f"DROP TABLE {structured_db._INDICE_DE_OBJETO}")


@pytest.mark.parametrize("termo,esperado", [
    ("%ambulancia%", 2), ("%computador%", 1), ("%tipo d%", 2),
    ("%de%", 3), ("%x%", 0),  # menos de 3 letras: o trigram não indexa, mas responde igual
])
def test_indice_do_objeto_devolve_o_mesmo_que_o_like(documents, tmp_path, termo, esperado):
    com_indice = Banco(sqlite_path=tmp_path / "com.sqlite")
    sem_indice = Banco(sqlite_path=tmp_path / "sem.sqlite")
    structured_db.construir_banco(documents, com_indice)
    structured_db.construir_banco(documents, sem_indice)
    _sem_indice_de_objeto(sem_indice)

    assert "rowid IN" in com_indice._sql(_CONTAGEM_POR_OBJETO)
    assert sem_indice._sql(_CONTAGEM_POR_OBJETO) == _CONTAGEM_POR_OBJETO
    assert (com_indice.consultar(_CONTAGEM_POR_OBJETO, [termo])
            == sem_indice.consultar(_CONTAGEM_POR_OBJETO, [termo]) == [{"n": esperado}])


def test_comando_fts_cria_o_indice_numa_base_ja_montada(documents, banco, monkeypatch):
    """A base baixada pronta (Drive) ganha o índice sem ser remontada."""
    monkeypatch.setattr(structured_db.config, "DATABASE_URL", "")
    structured_db.construir_banco(documents, banco)
    _sem_indice_de_objeto(banco)
    assert "rowid IN" not in banco._sql(_CONTAGEM_POR_OBJETO)

    assert structured_db.main(["--sqlite", str(banco.sqlite_path), "fts"]) == 0

    assert "rowid IN" in banco._sql(_CONTAGEM_POR_OBJETO)
    assert banco.consultar(_CONTAGEM_POR_OBJETO, ["%ambulancia%"]) == [{"n": 2}]


def test_postgres_continua_com_like_e_sem_indice():
    banco = Banco(url="postgresql://u@host/db")
    assert banco._sql(_CONTAGEM_POR_OBJETO) == _CONTAGEM_POR_OBJETO.replace("?", "%s")
    assert structured_db.indexar_objeto(banco) is False


def test_valores_distintos_sao_lidos_uma_vez_por_versao_do_arquivo(documents, banco, monkeypatch):
    structured_db.construir_banco(documents, banco)
    consultas = []
    original = Banco.consultar
    monkeypatch.setattr(Banco, "consultar", lambda self, sql, p=(): consultas.append(sql) or original(self, sql, p))

    assert banco.distintos("contratacoes", "municipio") == {"Niterói"}
    assert banco.distintos("contratacoes", "municipio") == {"Niterói"}
    assert len(consultas) == 1

    escrever_documents(documents, contratacoes=[_contratacao("B-1", municipio="Maricá")])
    structured_db.construir_banco(documents, banco)
    assert banco.distintos("contratacoes", "municipio") == {"Maricá"}
