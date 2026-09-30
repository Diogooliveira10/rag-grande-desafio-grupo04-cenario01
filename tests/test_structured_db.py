"""Base estruturada em SQL, sempre em SQLite temporário (sem rede, sem Neon)."""

from __future__ import annotations

import json
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


def _plano(numero, *, ano=2025, valor=5_000_000.0, unidade="Secretaria de Saúde"):
    return {
        "numeroControlePNCP": numero,
        "anoPca": ano,
        "cnpj": "42498600000171",
        "razaoSocial": "ESTADO DO RIO DE JANEIRO",
        "codigoUnidade": "1",
        "nomeUnidade": unidade,
        "uf": "RJ",
        "municipio": "Rio de Janeiro",
        "quantidade": 12,
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


def test_contratacao_vira_linha_normalizada(documents, banco):
    structured_db.construir_banco(documents, banco)

    [linha] = banco.consultar("SELECT * FROM contratacoes WHERE numero_controle_pncp = ?", ["A-1"])
    assert linha["esfera"] == "municipal"
    assert linha["uf"] == "RJ"
    assert linha["ano_publicacao"] == 2025
    assert linha["mes_publicacao"] == "2025-03"
    assert linha["objeto_normalizado"] == "aquisicao de ambulancias tipo d"
    assert linha["valor"] == 900.0  # homologado tem prioridade


def test_valor_usa_estimado_quando_homologado_e_zero_ou_ausente(documents, banco):
    structured_db.construir_banco(documents, banco)

    valores = {
        l["numero_controle_pncp"]: l["valor"]
        for l in banco.consultar("SELECT numero_controle_pncp, valor FROM contratacoes")
    }
    assert valores == {"A-1": 900.0, "A-2": 2000.0, "A-3": None}


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
