"""O que muda entre SQLite e Postgres na base estruturada: a porta ``Dialeto`` e as duas implementações.

``src/structured_db.py`` escreve as consultas uma vez, com ``?`` como marcador, e
o ``Banco`` delega a um dialeto o resto: como conectar, como traduzir o SQL,
como gravar a base inteira sem que um leitor a veja pela metade, e o que mostrar
no log sem credenciais. Um banco novo é uma implementação nova desta porta.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

import config

if TYPE_CHECKING:
    from src.structured_db import Banco

# Filtro do objeto contratado, escrito uma vez por quem monta o WHERE. Sem
# índice, ``LIKE '%termo%'`` lê as ~107 mil linhas a cada consulta (~140 ms; uma
# pergunta faz umas 10). No SQLite com o índice FTS5 de trigramas, o mesmo LIKE
# vai ao índice e devolve as mesmas linhas: o trigram aceita LIKE com a mesma
# semântica (sem caixa, só ASCII, e o texto já vem normalizado). Postgres e
# SQLite sem o índice seguem com o LIKE.
FILTRO_DE_OBJETO = "objeto_normalizado LIKE ?"
_INDICE_DE_OBJETO = "contratacoes_objeto_fts"
_FILTRO_DE_OBJETO_INDEXADO = (
    f"rowid IN (SELECT rowid FROM {_INDICE_DE_OBJETO} WHERE {_INDICE_DE_OBJETO}.objeto_normalizado LIKE ?)"
)

# Recria as tabelas e insere as linhas numa conexão já aberta (structured_db._gravar).
Gravacao = Callable[["Banco", Any], None]


class Dialeto(Protocol):
    """Porta: o que o ``Banco`` pede a cada tipo de banco."""

    nome: str
    tipo_real: str

    def sql(self, banco: Banco, sql: str) -> str: ...
    def conectar(self, banco: Banco, *, somente_leitura: bool) -> Any: ...
    def depois_de_falhar(self, conexao: Any) -> None: ...
    def existe(self, banco: Banco) -> bool: ...
    def descricao(self, banco: Banco) -> str: ...
    def rotulo_publico(self, banco: Banco) -> str: ...
    def assinatura_do_cache(self, banco: Banco) -> Optional[tuple[str, int, int, int]]: ...
    def depois_de_criar_tabelas(self, cursor: Any) -> None: ...
    def gravar_base(self, banco: Banco, gravar: Gravacao) -> None: ...
    def indexar_objeto(self, banco: Banco) -> bool: ...


def _criar_indice_de_objeto(cursor: Any) -> bool:
    """(Re)cria o índice FTS5 do objeto a partir de ``contratacoes``.

    Só acelera: se o SQLite não tem FTS5/trigram (anterior a 3.34), devolve
    False e as consultas continuam no LIKE.
    """
    try:
        cursor.execute(f"DROP TABLE IF EXISTS {_INDICE_DE_OBJETO}")
        cursor.execute(
            f"CREATE VIRTUAL TABLE {_INDICE_DE_OBJETO} USING fts5(objeto_normalizado, "
            "content='contratacoes', content_rowid='rowid', tokenize='trigram')"
        )
        cursor.execute(f"INSERT INTO {_INDICE_DE_OBJETO}({_INDICE_DE_OBJETO}) VALUES ('rebuild')")
    except sqlite3.OperationalError:
        return False
    return True


def _assinatura_do_arquivo(path: Path) -> Optional[tuple[str, int, int, int]]:
    """Muda quando o arquivo muda (build, ``fts``); None se ele não existe.

    Data e tamanho não bastam: apagar e recriar o índice na mesma fração de
    segundo reaproveita as páginas e deixa os dois iguais. O contador de
    alterações do cabeçalho do SQLite (bytes 24-27) sobe a cada escrita.
    """
    try:
        stat = path.stat()
        with path.open("rb") as arquivo:
            cabecalho = arquivo.read(28)
    except OSError:
        return None
    contador = int.from_bytes(cabecalho[24:28], "big") if len(cabecalho) == 28 else 0
    return str(path.resolve()), stat.st_mtime_ns, stat.st_size, contador


@lru_cache(maxsize=8)
def _tem_indice_de_objeto(assinatura: tuple[str, int, int, int]) -> bool:
    try:
        with closing(sqlite3.connect(f"file:{assinatura[0]}?mode=ro", uri=True)) as conexao:
            return conexao.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [_INDICE_DE_OBJETO]
            ).fetchone() is not None
    except sqlite3.Error:
        return False


class DialetoSqlite:
    """SQLite local em ``data/structured/``: testes, avaliação e quem não tem o Neon."""

    nome = "sqlite"
    tipo_real = "REAL"

    def sql(self, banco: Banco, sql: str) -> str:
        if FILTRO_DE_OBJETO in sql:
            assinatura = _assinatura_do_arquivo(Path(banco.sqlite_path))
            if assinatura and _tem_indice_de_objeto(assinatura):
                return sql.replace(FILTRO_DE_OBJETO, _FILTRO_DE_OBJETO_INDEXADO)
        return sql

    def conectar(self, banco: Banco, *, somente_leitura: bool) -> Any:
        if somente_leitura:
            return sqlite3.connect(f"file:{Path(banco.sqlite_path)}?mode=ro", uri=True)
        return sqlite3.connect(Path(banco.sqlite_path))

    def depois_de_falhar(self, conexao: Any) -> None:
        pass

    def existe(self, banco: Banco) -> bool:
        return Path(banco.sqlite_path).exists()

    def descricao(self, banco: Banco) -> str:
        try:
            return Path(banco.sqlite_path).resolve().relative_to(config.ROOT_DIR).as_posix()
        except ValueError:
            return str(banco.sqlite_path)

    def rotulo_publico(self, banco: Banco) -> str:
        return self.descricao(banco)

    def assinatura_do_cache(self, banco: Banco) -> Optional[tuple[str, int, int, int]]:
        return _assinatura_do_arquivo(Path(banco.sqlite_path))

    def depois_de_criar_tabelas(self, cursor: Any) -> None:
        _criar_indice_de_objeto(cursor)

    def gravar_base(self, banco: Banco, gravar: Gravacao) -> None:
        """Monta o arquivo ao lado e troca no fim: quem lê nunca vê a base pela metade."""
        from src.structured_db import Banco

        destino = Path(banco.sqlite_path)
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporario = destino.with_name(destino.name + ".tmp")
        temporario.unlink(missing_ok=True)
        # closing(): o "with" do sqlite3 só faz commit; no Windows, conexão aberta
        # trava o arquivo e impede o replace.
        with closing(sqlite3.connect(temporario)) as conexao, conexao:
            gravar(Banco(sqlite_path=temporario), conexao)
        temporario.replace(destino)

    def indexar_objeto(self, banco: Banco) -> bool:
        if not self.existe(banco):
            return False
        with closing(sqlite3.connect(Path(banco.sqlite_path))) as conexao, conexao:
            return _criar_indice_de_objeto(conexao.cursor())


class DialetoPostgres:
    """Postgres (ex.: Neon), quando ``DATABASE_URL`` está preenchido."""

    nome = "postgres"
    tipo_real = "DOUBLE PRECISION"

    def sql(self, banco: Banco, sql: str) -> str:
        return sql.replace("?", "%s")

    def conectar(self, banco: Banco, *, somente_leitura: bool) -> Any:
        import psycopg

        conexao = psycopg.connect(banco.url, connect_timeout=15)
        conexao.read_only = somente_leitura
        # Escrita em autocommit + conexao.transaction(): o bloco inteiro
        # vira uma transação só, com commit explícito no fim.
        conexao.autocommit = not somente_leitura
        return conexao

    def depois_de_falhar(self, conexao: Any) -> None:
        conexao.rollback()  # senão a conexão fica inutilizável para a próxima consulta

    def existe(self, banco: Banco) -> bool:
        return True

    def descricao(self, banco: Banco) -> str:
        host = banco.url.split("@", 1)[-1].split("/", 1)[0].split("?", 1)[0]
        return f"postgres://{host}"

    def rotulo_publico(self, banco: Banco) -> str:
        # Sem o host: ele serve a quem opera a carga, não a quem lê uma resposta.
        return "postgres"

    def assinatura_do_cache(self, banco: Banco) -> Optional[tuple[str, int, int, int]]:
        return None  # sem arquivo para versionar: os valores distintos são relidos

    def depois_de_criar_tabelas(self, cursor: Any) -> None:
        pass

    def gravar_base(self, banco: Banco, gravar: Gravacao) -> None:
        """Tudo numa transação só: quem lê nunca vê a base pela metade."""
        with closing(banco.conectar(somente_leitura=False)) as conexao, conexao.transaction():
            gravar(banco, conexao)

    def indexar_objeto(self, banco: Banco) -> bool:
        return False  # o índice FTS5 é só do SQLite


SQLITE = DialetoSqlite()
POSTGRES = DialetoPostgres()
