"""Base estruturada em SQL: contratações e planos do PCA.

A busca semântica encontra *qual* registro fala de ambulância, mas não ordena
por valor nem calcula mediana. Este módulo carrega os registros JSON do PNCP
que já estão em ``data/normalized/documents.jsonl`` (bases
``contratos_estruturado`` e ``pca_agregado``) em tabelas SQL, para
``src/structured_query.py`` responder ranking, estatística e agrupamento.

Dois destinos, escolhidos por ``DATABASE_URL`` no ``.env``:

- **Postgres** (ex.: Neon, na nuvem) quando ``DATABASE_URL`` está preenchido;
- **SQLite** local em ``data/structured/`` (fora do Git) quando está vazio —
  é o que os testes e quem não tem acesso ao Neon usam.

O banco é derivado: pode ser apagado e remontado a qualquer momento, e se
remonta sozinho quando o ``documents.jsonl`` muda (``garantir_banco``).

Uso::

    py -m src.structured_db build
    py -m src.structured_db sql "SELECT modalidade, COUNT(*) FROM contratacoes GROUP BY 1"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import config

SQLITE_PATH = config.ROOT_DIR / "data" / "structured" / "base_estruturada.sqlite"
DOCUMENTS_FILE = config.NORMALIZED_DATA_DIR / "documents.jsonl"

BASE_CONTRATACOES = "contratos_estruturado"
BASE_PCA = "pca_agregado"

# orgaoEntidade.esferaId do PNCP.
ESFERAS = {"F": "federal", "E": "estadual", "M": "municipal", "D": "distrital", "N": "nao_se_aplica"}

_COLUNAS_CONTRATACOES = {
    "numero_controle_pncp": "TEXT PRIMARY KEY",
    "chunk_id": "TEXT NOT NULL",
    "source_file": "TEXT",
    "ano_compra": "INTEGER",
    "data_publicacao": "TEXT",
    "ano_publicacao": "INTEGER",
    "mes_publicacao": "TEXT",
    "data_assinatura": "TEXT",
    "ano_assinatura": "INTEGER",
    "mes_assinatura": "TEXT",
    "tipo_registro": "TEXT",
    "fornecedor": "TEXT",
    "modalidade": "TEXT",
    "modo_disputa": "TEXT",
    "situacao": "TEXT",
    "srp": "INTEGER",
    "amparo_legal": "TEXT",
    "orgao_cnpj": "TEXT",
    "orgao_nome": "TEXT",
    "esfera": "TEXT",
    "poder": "TEXT",
    "uf": "TEXT",
    "municipio": "TEXT",
    "codigo_ibge": "TEXT",
    "unidade_nome": "TEXT",
    "objeto": "TEXT",
    "objeto_normalizado": "TEXT",
    "valor_estimado": "{REAL}",
    "valor_homologado": "{REAL}",
    "valor_contratado": "{REAL}",
    "valor": "{REAL}",
}
_COLUNAS_PCA = {
    "numero_controle_pncp": "TEXT PRIMARY KEY",
    "chunk_id": "TEXT NOT NULL",
    "source_file": "TEXT",
    "ano_pca": "INTEGER",
    "orgao_cnpj": "TEXT",
    "orgao_nome": "TEXT",
    "unidade_codigo": "TEXT",
    "unidade_nome": "TEXT",
    "uf": "TEXT",
    "municipio": "TEXT",
    "quantidade_itens": "INTEGER",
    "valor_total": "{REAL}",
    "data_publicacao": "TEXT",
}
_TABELAS = {
    "contratacoes": _COLUNAS_CONTRATACOES,
    "pca_planos": _COLUNAS_PCA,
    "meta": {"chave": "TEXT PRIMARY KEY", "valor": "TEXT"},
}
_INDICES = (
    "CREATE INDEX idx_contratacoes_ano ON contratacoes (ano_publicacao)",
    "CREATE INDEX idx_pca_ano ON pca_planos (ano_pca)",
)


@dataclass
class _Sessao:
    conexao: Any = None


@dataclass(frozen=True)
class Banco:
    """Onde a base estruturada mora: Postgres (``url``) ou SQLite (``sqlite_path``).

    As consultas são escritas uma vez, com ``?`` como marcador de parâmetro;
    ``consultar`` troca por ``%s`` no Postgres.
    """

    url: str = ""
    sqlite_path: Path = SQLITE_PATH
    _sessao: "_Sessao" = field(default_factory=lambda: _Sessao(), compare=False, repr=False)

    @property
    def dialeto(self) -> str:
        return "postgres" if self.url else "sqlite"

    def descricao(self) -> str:
        """Identificação sem credenciais, para log e mensagens."""
        if self.dialeto == "sqlite":
            try:
                return Path(self.sqlite_path).resolve().relative_to(config.ROOT_DIR).as_posix()
            except ValueError:
                return str(self.sqlite_path)
        host = self.url.split("@", 1)[-1].split("/", 1)[0].split("?", 1)[0]
        return f"postgres://{host}"

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.dialeto == "postgres" else sql

    def conectar(self, *, somente_leitura: bool = True) -> Any:
        if self.dialeto == "postgres":
            import psycopg

            conexao = psycopg.connect(self.url, connect_timeout=15)
            conexao.read_only = somente_leitura
            # Escrita em autocommit + conexao.transaction(): o bloco inteiro
            # vira uma transação só, com commit explícito no fim.
            conexao.autocommit = not somente_leitura
            return conexao
        if somente_leitura:
            return sqlite3.connect(f"file:{Path(self.sqlite_path)}?mode=ro", uri=True)
        return sqlite3.connect(Path(self.sqlite_path))

    def consultar(self, sql: str, parametros: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Roda uma consulta somente leitura e devolve as linhas como dicts.

        Dentro de ``with banco.sessao():`` reaproveita a mesma conexão (no Neon,
        abrir conexão custa quase 1 s); fora, abre e fecha uma por consulta.
        """
        if self._sessao.conexao is not None:
            return self._executar(self._sessao.conexao, sql, parametros)
        with closing(self.conectar()) as conexao:
            return self._executar(conexao, sql, parametros)

    def _executar(self, conexao: Any, sql: str, parametros: Sequence[Any]) -> list[dict[str, Any]]:
        cursor = conexao.cursor()
        try:
            cursor.execute(self._sql(sql), list(parametros))
        except Exception:
            if self.dialeto == "postgres":
                conexao.rollback()  # senão a conexão fica inutilizável para a próxima consulta
            raise
        colunas = [coluna[0] for coluna in cursor.description or []]
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]

    @contextmanager
    def sessao(self) -> Iterator["Banco"]:
        """Uma conexão só para várias consultas seguidas."""
        if self._sessao.conexao is not None:
            yield self
            return
        with closing(self.conectar()) as conexao:
            self._sessao.conexao = conexao
            try:
                yield self
            finally:
                self._sessao.conexao = None

    def existe(self) -> bool:
        return self.dialeto == "postgres" or Path(self.sqlite_path).exists()


def banco_padrao() -> Banco:
    """Postgres se ``DATABASE_URL`` está no ``.env``; senão, SQLite local."""
    return Banco(url=config.DATABASE_URL)


def normalizar(texto: str) -> str:
    """casefold + sem acentos + espaços únicos (mesma regra do router e do query_analysis)."""
    dobrado = str(texto or "").casefold()
    sem_acentos = "".join(
        caractere for caractere in unicodedata.normalize("NFKD", dobrado) if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


def _numero(valor: Any) -> float | None:
    if isinstance(valor, bool) or valor in (None, ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor: Any) -> int | None:
    numero = _numero(valor)
    return int(numero) if numero is not None else None


def _data(valor: Any) -> str | None:
    texto = str(valor or "")[:10]
    try:
        return datetime.strptime(texto, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _registros_json(documents_file: Path, base_id: str) -> Iterator[tuple[Mapping[str, Any], dict[str, Any]]]:
    """(chunk, registro) de cada chunk da base cujo texto é um registro JSON inteiro."""
    with documents_file.open(encoding="utf-8") as arquivo:
        for linha in arquivo:
            if not linha.strip():
                continue
            chunk = json.loads(linha)
            if chunk.get("base_id") != base_id:
                continue
            try:
                registro = json.loads(chunk.get("text") or "")
            except (TypeError, ValueError):
                continue  # indicadores em texto (pca_indicators.py) não são registros
            if isinstance(registro, dict):
                yield chunk, registro


def _linha_contratacao(chunk: Mapping[str, Any], registro: dict[str, Any]) -> dict[str, Any] | None:
    numero_controle = registro.get("numeroControlePNCP")
    if not numero_controle:
        return None
    orgao = registro.get("orgaoEntidade") or {}
    unidade = registro.get("unidadeOrgao") or {}
    data_publicacao = _data(registro.get("dataPublicacaoPncp"))
    data_assinatura = _data(registro.get("dataAssinatura"))
    contrato_assinado = "-2-" in str(numero_controle)
    contratado = _numero(registro.get("valorGlobal")) if contrato_assinado else None
    estimado = _numero(registro.get("valorTotalEstimado"))
    homologado = _numero(registro.get("valorTotalHomologado"))
    objeto = " ".join(str(registro.get("objetoContrato") or registro.get("objetoCompra") or "").split())
    return {
        "numero_controle_pncp": numero_controle,
        "chunk_id": chunk.get("chunk_id"),
        "source_file": chunk.get("source_file"),
        "ano_compra": _inteiro(registro.get("anoCompra")),
        "data_publicacao": data_publicacao,
        "ano_publicacao": int(data_publicacao[:4]) if data_publicacao else None,
        "mes_publicacao": data_publicacao[:7] if data_publicacao else None,
        "data_assinatura": data_assinatura,
        "ano_assinatura": int(data_assinatura[:4]) if data_assinatura else None,
        "mes_assinatura": data_assinatura[:7] if data_assinatura else None,
        "tipo_registro": "contrato_assinado" if contrato_assinado else "publicacao_compra",
        "fornecedor": registro.get("nomeRazaoSocialFornecedor"),
        "modalidade": registro.get("modalidadeNome") or (chunk.get("extras") or {}).get("modalidade"),
        "modo_disputa": registro.get("modoDisputaNome"),
        "situacao": registro.get("situacaoCompraNome"),
        "srp": int(bool(registro.get("srp"))) if registro.get("srp") is not None else None,
        "amparo_legal": (registro.get("amparoLegal") or {}).get("nome"),
        "orgao_cnpj": orgao.get("cnpj"),
        "orgao_nome": orgao.get("razaoSocial"),
        "esfera": ESFERAS.get(str(orgao.get("esferaId") or "")),
        "poder": orgao.get("poderId"),
        "uf": unidade.get("ufSigla"),
        "municipio": unidade.get("municipioNome"),
        "codigo_ibge": unidade.get("codigoIbge"),
        "unidade_nome": unidade.get("nomeUnidade"),
        "objeto": objeto,
        "objeto_normalizado": normalizar(objeto),
        "valor_estimado": estimado,
        "valor_homologado": homologado,
        "valor_contratado": contratado,
        # Contratos usam valorGlobal; compras conservam a distinção dos campos.
        "valor": contratado if contrato_assinado else homologado or estimado or None,
    }


def _linha_pca(chunk: Mapping[str, Any], registro: dict[str, Any]) -> dict[str, Any] | None:
    numero_controle = registro.get("numeroControlePNCP")
    if not numero_controle:
        return None
    return {
        "numero_controle_pncp": numero_controle,
        "chunk_id": chunk.get("chunk_id"),
        "source_file": chunk.get("source_file"),
        "ano_pca": _inteiro(registro.get("anoPca")),
        "orgao_cnpj": registro.get("cnpj"),
        "orgao_nome": registro.get("razaoSocial"),
        "unidade_codigo": registro.get("codigoUnidade"),
        "unidade_nome": registro.get("nomeUnidade"),
        "uf": registro.get("uf"),
        "municipio": registro.get("municipio"),
        "quantidade_itens": _inteiro(registro.get("quantidade")),
        "valor_total": _numero(registro.get("valorTotal")),
        "data_publicacao": _data(registro.get("dataPublicacaoPncp")),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gravar(banco: Banco, conexao: Any, tabelas: dict[str, list[dict[str, Any]]]) -> None:
    """Recria as tabelas e insere as linhas, na conexão (e transação) recebida."""
    tipo_real = "DOUBLE PRECISION" if banco.dialeto == "postgres" else "REAL"
    cursor = conexao.cursor()
    for tabela in _TABELAS:
        cursor.execute(f"DROP TABLE IF EXISTS {tabela}")
    for tabela, colunas in _TABELAS.items():
        definicao = ", ".join(f"{nome} {tipo.format(REAL=tipo_real)}" for nome, tipo in colunas.items())
        cursor.execute(f"CREATE TABLE {tabela} ({definicao})")
    for indice in _INDICES:
        cursor.execute(indice)
    for tabela, linhas in tabelas.items():
        if not linhas:
            continue
        colunas = list(_TABELAS[tabela])
        cursor.executemany(
            banco._sql(f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join('?' for _ in colunas)})"),
            [[linha.get(coluna) for coluna in colunas] for linha in linhas],
        )


def construir_banco(documents_file: Path = DOCUMENTS_FILE, banco: Optional[Banco] = None) -> dict[str, Any]:
    """Recria a base a partir do ``documents.jsonl`` e devolve as contagens.

    Quem estiver lendo nunca vê a base pela metade: no Postgres tudo roda numa
    transação só; no SQLite o arquivo é montado ao lado e trocado no fim.
    """
    banco = banco or banco_padrao()
    documents_file = Path(documents_file)
    # Deduplica pelo número de controle (a chave primária): a mesma
    # contratação pode aparecer em mais de uma versão do corpus.
    contratacoes = {
        linha["numero_controle_pncp"]: linha
        for chunk, registro in _registros_json(documents_file, BASE_CONTRATACOES)
        if (linha := _linha_contratacao(chunk, registro))
    }
    planos = {
        linha["numero_controle_pncp"]: linha
        for chunk, registro in _registros_json(documents_file, BASE_PCA)
        if (linha := _linha_pca(chunk, registro))
    }
    resumo: dict[str, Any] = {
        "destino": banco.descricao(),
        "documents_file": str(documents_file),
        "source_sha256": _sha256(documents_file),
        "schema_version": "2",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contratacoes": len(contratacoes),
        "pca_planos": len(planos),
    }
    tabelas = {
        "contratacoes": list(contratacoes.values()),
        "pca_planos": list(planos.values()),
        "meta": [{"chave": chave, "valor": str(valor)} for chave, valor in resumo.items()],
    }

    if banco.dialeto == "postgres":
        with closing(banco.conectar(somente_leitura=False)) as conexao, conexao.transaction():
            _gravar(banco, conexao, tabelas)
        return resumo

    destino = Path(banco.sqlite_path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_name(destino.name + ".tmp")
    temporario.unlink(missing_ok=True)
    # closing(): o "with" do sqlite3 só faz commit; no Windows, conexão aberta
    # trava o arquivo e impede o replace.
    with closing(sqlite3.connect(temporario)) as conexao, conexao:
        _gravar(Banco(sqlite_path=temporario), conexao, tabelas)
    temporario.replace(destino)
    return resumo


def _sha_registrado(banco: Banco) -> Optional[str]:
    try:
        linhas = banco.consultar("SELECT valor FROM meta WHERE chave = ?", ["source_sha256"])
    except Exception:  # tabela ainda não existe
        return None
    return linhas[0]["valor"] if linhas else None


def garantir_banco(
    documents_file: Path = DOCUMENTS_FILE,
    banco: Optional[Banco] = None,
    status: Optional[dict[str, Any]] = None,
) -> Banco:
    """Devolve o banco pronto, remontando-o se não existe ou se o
    ``documents.jsonl`` mudou desde a última montagem (SHA-256 no ``meta``).

    Com um usuário só de leitura (ex.: ``rag_leitor`` no Neon), a remontagem
    falha por permissão; se a base já existe, ela é usada como está e
    ``status["base_desatualizada"]`` fica ``True`` (o chamador leva ao log).
    Sem base nenhuma, o erro sobe.
    """
    banco = banco or banco_padrao()
    documents_file = Path(documents_file)
    if not documents_file.exists():
        raise FileNotFoundError(f"documents.jsonl não encontrado: {documents_file}")
    if not banco.existe():
        construir_banco(documents_file, banco)
        return banco
    with banco.sessao():
        registrado = _sha_registrado(banco)
        try:
            versao = banco.consultar("SELECT valor FROM meta WHERE chave = ?", ["schema_version"])
        except Exception:
            versao = []
    if registrado == _sha256(documents_file) and versao and versao[0]["valor"] == "2":
        return banco
    try:
        construir_banco(documents_file, banco)
    except Exception as erro:
        if registrado is None:
            raise
        if status is not None:
            status["base_desatualizada"] = True
            status["erro_remontagem"] = type(erro).__name__
    return banco


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Base estruturada em SQL (contratações e PCA).")
    parser.add_argument("--documents", type=Path, default=DOCUMENTS_FILE)
    parser.add_argument("--sqlite", type=Path, default=SQLITE_PATH, help="arquivo SQLite (sem DATABASE_URL)")
    comandos = parser.add_subparsers(dest="comando", required=True)
    comandos.add_parser("build", help="recria a base a partir do documents.jsonl")
    consulta = comandos.add_parser("sql", help="roda uma consulta somente leitura")
    consulta.add_argument("consulta")
    args = parser.parse_args(argv)

    banco = Banco(url=config.DATABASE_URL, sqlite_path=args.sqlite)
    if args.comando == "build":
        print(json.dumps(construir_banco(args.documents, banco), ensure_ascii=False, indent=2))
        return 0
    garantir_banco(args.documents, banco)
    linhas = banco.consultar(args.consulta)
    if linhas:
        print(" | ".join(linhas[0]))
    for linha in linhas:
        print(" | ".join("" if valor is None else str(valor) for valor in linha.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
