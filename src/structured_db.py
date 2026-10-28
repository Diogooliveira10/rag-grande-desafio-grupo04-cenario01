"""Base estruturada em SQL: contratações e planos do PCA.

A busca semântica encontra *qual* registro fala de ambulância, mas não ordena
por valor nem calcula mediana. Este módulo carrega os registros JSON do PNCP
em ``data/normalized/documents.jsonl`` (ou no snapshot versionado
``data/normalized/v6/documents.jsonl`` quando o arquivo sem versão não existe; bases
``contratos_estruturado`` e ``pca_agregado``) em tabelas SQL, para
``src/structured_query.py`` responder ranking, estatística e agrupamento.

Dois destinos, escolhidos por ``DATABASE_URL`` no ``.env``:

- **Postgres** (ex.: Neon, na nuvem) quando ``DATABASE_URL`` está preenchido;
- **SQLite** local em ``data/structured/`` (fora do Git) quando está vazio —
  é o que os testes e quem não tem acesso ao Neon usam.

O banco é derivado e preparado explicitamente por ``build``. O atendimento
usa ``validar_banco`` e nunca o reconstrói durante uma pergunta.
``garantir_banco`` permanece disponível para ferramentas administrativas legadas.

Uso::

    py -m src.structured_db build
    py -m src.structured_db fts     # índice do objeto numa base já montada (ex.: baixada do Drive)
    py -m src.structured_db sql "SELECT modalidade, COUNT(*) FROM contratacoes GROUP BY 1"
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import config
from src.domain import text as texto_dominio

SQLITE_PATH = config.ROOT_DIR / "data" / "structured" / "base_estruturada.sqlite"
DOCUMENTS_FILE = config.NORMALIZED_DATA_DIR / "documents.jsonl"
_DOCUMENTS_FILE_V6 = config.NORMALIZED_DATA_DIR / "v6" / "documents.jsonl"
if not DOCUMENTS_FILE.is_file() and _DOCUMENTS_FILE_V6.is_file():
    DOCUMENTS_FILE = _DOCUMENTS_FILE_V6

BASE_CONTRATACOES = "contratos_estruturado"
BASE_PCA = "pca_agregado"

# Sobe quando muda o que vai para as tabelas: garantir_banco remonta a base
# (SQLite ou Postgres) na próxima consulta. "4": pca_planos só com PCA.
# "5": compras e contratos da base agregada entram em contratacoes.
# "6": contratos da agregada entram também pelo ano de assinatura.
# "7": fornecedor_cnpj, para filtrar pelo CNPJ do fornecedor.
SCHEMA_VERSION = "7"

# numeroControlePNCP = CNPJ-<tipo>-<sequencial>/<ano>; tipo 0 = PCA, 1 = compra,
# 2 = contrato. A base agregada traz também compras e contratos ligados aos
# planos, que não são planos.
_TIPO_CONTROLE_PNCP = re.compile(r"^\d{14}-(\d)-")

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
    # Contrato assinado: número de controle da compra que o originou. A mesma
    # contratação aparece como compra publicada e como contrato; este campo
    # permite contar uma vez só.
    "numero_controle_compra": "TEXT",
    "fornecedor": "TEXT",
    # Só dígitos, como orgao_cnpj; vazio quando o fornecedor é pessoa física.
    "fornecedor_cnpj": "TEXT",
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


# Valores distintos (nomes de órgão, unidade, modalidade) por versão do arquivo
# SQLite: eram relidos a cada pergunta, ~11% do tempo do SQL.
_DISTINTOS: dict[tuple[tuple[str, int, int, int], str], frozenset] = {}


_sql_observation: ContextVar[dict[str, Any] | None] = ContextVar("sql_observation", default=None)


@contextmanager
def observar_consultas() -> Iterator[dict[str, Any]]:
    """Bounded SQL templates/parameters, including failed/relaxed attempts."""
    observation = {"total": 0, "fora_do_log": 0, "consultas": []}
    token = _sql_observation.set(observation)
    try:
        yield observation
    finally:
        _sql_observation.reset(token)


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

    def rotulo_publico(self) -> str:
        """Identificação que pode sair para o usuário (fonte da resposta, log, trace).

        Sem o host: ``descricao()`` diz qual servidor Postgres foi usado, o que
        serve a quem opera a carga, mas não a quem lê uma resposta.
        """
        return "postgres" if self.dialeto == "postgres" else self.descricao()

    def _sql(self, sql: str) -> str:
        if self.dialeto == "postgres":
            return sql.replace("?", "%s")
        if FILTRO_DE_OBJETO in sql:
            assinatura = _assinatura_do_arquivo(Path(self.sqlite_path))
            if assinatura and _tem_indice_de_objeto(assinatura):
                return sql.replace(FILTRO_DE_OBJETO, _FILTRO_DE_OBJETO_INDEXADO)
        return sql

    def distintos(self, tabela: str, coluna: str) -> frozenset:
        """Valores distintos não nulos de ``coluna``; no SQLite, lidos uma vez por versão do arquivo.

        ``tabela`` e ``coluna`` vêm do código, nunca do usuário.
        """
        sql = f"SELECT DISTINCT {coluna} AS v FROM {tabela} WHERE {coluna} IS NOT NULL"
        assinatura = None if self.dialeto == "postgres" else _assinatura_do_arquivo(Path(self.sqlite_path))
        if assinatura is None:
            return frozenset(linha["v"] for linha in self.consultar(sql))
        chave = (assinatura, sql)
        if chave not in _DISTINTOS:
            _DISTINTOS[chave] = frozenset(linha["v"] for linha in self.consultar(sql))
        return _DISTINTOS[chave]

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
        observation = _sql_observation.get()
        event = {"sql": self._sql(sql), "parametros": list(parametros), "status": "ok"}
        started = time.perf_counter()
        try:
            if self._sessao.conexao is not None:
                result = self._executar(self._sessao.conexao, sql, parametros)
            else:
                with closing(self.conectar()) as conexao:
                    result = self._executar(conexao, sql, parametros)
            event["linhas_retornadas"] = len(result)
            return result
        except Exception as error:
            event.update({"status": "erro", "error_type": type(error).__name__})
            raise
        finally:
            if observation is not None:
                from src.obs import bounded_log_value
                event["latencia_ms"] = max(0, round((time.perf_counter() - started) * 1000))
                observation["total"] += 1
                if len(observation["consultas"]) >= 20:
                    # Keep first ten and latest ten, so the final query is visible.
                    del observation["consultas"][10]
                    observation["fora_do_log"] += 1
                observation["consultas"].append(bounded_log_value(event))

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


# casefold + sem acentos + espaços únicos; regra em src/domain/text.py.
normalizar = texto_dominio.normalizar


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


_PESOS_CNPJ = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def cnpj_valido(digitos: str) -> bool:
    """Confere os dois dígitos verificadores de um CNPJ só com dígitos."""
    if len(digitos) != 14 or not digitos.isdigit() or len(set(digitos)) == 1:
        return False
    for posicao in (12, 13):
        pesos = _PESOS_CNPJ[13 - posicao:]
        resto = sum(int(d) * p for d, p in zip(digitos[:posicao], pesos)) % 11
        if digitos[posicao] != str(0 if resto < 2 else 11 - resto):
            return False
    return True


def _cnpj(valor: Any) -> str | None:
    """CNPJ só com dígitos; CPF (11 dígitos) e vazio viram None."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos if len(digitos) == 14 else None


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
        # Recortes novos carregam classificação oficial explícita em extras.
        # Legado sem essa marca mantém o contrato/schema ativo até promoção.
        "tipo_registro": (chunk.get("extras") or {}).get("tipo_instrumento") or (
            "contrato_assinado" if contrato_assinado else "publicacao_compra"),
        "numero_controle_compra": registro.get("numeroControlePncpCompra") if contrato_assinado else None,
        "fornecedor": registro.get("nomeRazaoSocialFornecedor") or (chunk.get("extras") or {}).get("fornecedor"),
        "fornecedor_cnpj": _cnpj(registro.get("niFornecedor")),
        "modalidade": registro.get("modalidadeNome") or (chunk.get("extras") or {}).get("modalidade"),
        "modo_disputa": registro.get("modoDisputaNome"),
        "situacao": registro.get("situacaoCompraNome"),
        "srp": int(bool(registro.get("srp"))) if registro.get("srp") is not None else None,
        "amparo_legal": (registro.get("amparoLegal") or {}).get("nome"),
        "orgao_cnpj": orgao.get("cnpj"),
        "orgao_nome": orgao.get("razaoSocial"),
        "esfera": ESFERAS.get(str(orgao.get("esferaId") or "")),
        "poder": orgao.get("poderId"),
        "uf": unidade.get("ufSigla") or (chunk.get("extras") or {}).get("uf"),
        "municipio": unidade.get("municipioNome"),
        "codigo_ibge": unidade.get("codigoIbge"),
        "unidade_nome": unidade.get("nomeUnidade"),
        "objeto": objeto,
        "objeto_normalizado": normalizar(objeto),
        "valor_estimado": estimado,
        "valor_homologado": homologado,
        "valor_contratado": contratado,
        # Contratos usam valorGlobal; compras conservam a distinção dos campos.
        "valor": contratado if contrato_assinado else (
            homologado if homologado is not None else estimado),
    }


def _eh_pca(numero_controle: str, registro: Mapping[str, Any]) -> bool:
    """Plano de contratações: identificador de tipo 0, ou ``anoPca`` quando o
    identificador não diz o tipo."""
    tipo = _TIPO_CONTROLE_PNCP.match(numero_controle)
    if tipo:
        return tipo.group(1) == "0"
    return registro.get("anoPca") is not None


def _compra_ou_contrato_da_base_agregada(
    registro: Mapping[str, Any], anos_publicacao: set[int]
) -> bool:
    """Compra (tipo 1) ou contrato (tipo 2) que a base agregada traz ligado a um
    PCA e que cabe no recorte da tabela ``contratacoes``.

    Só entra no formato completo do PNCP, de órgão do RJ, no período que a
    base estruturada cobre. Compras usam publicação; contratos podem também
    usar assinatura. Não descarte um contrato assinado no período só porque
    sua publicação ocorreu em outro ano. As datas originais são preservadas.
    """
    tipo = _TIPO_CONTROLE_PNCP.match(str(registro.get("numeroControlePNCP") or ""))
    if not tipo or tipo.group(1) not in {"1", "2"} or "orgaoEntidade" not in registro:
        return False
    uf = (registro.get("unidadeOrgao") or {}).get("ufSigla")
    publicacao = _data(registro.get("dataPublicacaoPncp"))
    assinatura = _data(registro.get("dataAssinatura")) if tipo.group(1) == "2" else None
    return uf == "RJ" and any(
        data is not None and int(data[:4]) in anos_publicacao
        for data in (publicacao, assinatura)
    )


def _linha_pca(chunk: Mapping[str, Any], registro: dict[str, Any]) -> dict[str, Any] | None:
    numero_controle = registro.get("numeroControlePNCP")
    if not numero_controle or not _eh_pca(str(numero_controle), registro):
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
    from src.index import file_sha256
    return file_sha256(path)


@lru_cache(maxsize=8)
def _hash_snapshot(path: str, tamanho: int, modificado_ns: int) -> str:
    """Hash por assinatura do arquivo imutável, não uma releitura a cada pergunta."""
    return _sha256(Path(path))


def _meta_valida(banco: Banco) -> dict[str, str] | None:
    meta = {row["chave"]: row["valor"] for row in banco.consultar("SELECT chave, valor FROM meta")}
    return meta if meta.get("schema_version") == SCHEMA_VERSION else None


def validar_banco(documents_file: Path = DOCUMENTS_FILE, banco: Optional[Banco] = None, *, fast: bool = False) -> Banco:
    """Abre somente um snapshot já preparado; nunca constrói/migra na consulta."""
    banco = banco or banco_padrao()
    documents_file = Path(documents_file).resolve()
    if not banco.existe():
        raise FileNotFoundError(f"banco não preparado: {banco.descricao()}; execute structured_db build")
    with banco.sessao():
        meta = _meta_valida(banco)
        if meta is None:
            raise ValueError("schema SQL incompatível; prepare um novo snapshot fora da consulta")
        if fast:
            for tabela in _TABELAS:
                banco.consultar(f"SELECT 1 FROM {tabela} LIMIT 1")
        else:
            for tabela, colunas in _TABELAS.items():
                banco.consultar(f"SELECT {', '.join(colunas)} FROM {tabela} LIMIT 0")
    if not fast:
        stat = documents_file.stat()
        expected = _hash_snapshot(str(documents_file), stat.st_size, stat.st_mtime_ns)
        if meta.get("source_sha256") != expected:
            raise ValueError("SQL e documentos pertencem a snapshots diferentes; prepare o banco explicitamente")
    return banco


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
    if banco.dialeto == "sqlite":
        _criar_indice_de_objeto(cursor)


def indexar_objeto(banco: Optional[Banco] = None) -> bool:
    """Cria o índice do objeto num SQLite já montado, sem remontar a base.

    O ``build`` já cria; isto serve à base baixada pronta (Drive). Devolve
    False no Postgres e no SQLite sem FTS5. Não roda durante uma pergunta.
    """
    banco = banco or banco_padrao()
    if banco.dialeto != "sqlite" or not banco.existe():
        return False
    with closing(sqlite3.connect(Path(banco.sqlite_path))) as conexao, conexao:
        return _criar_indice_de_objeto(conexao.cursor())


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
    # A base agregada traz compras e contratos ligados aos PCAs; os que a base
    # de contratos não tem completam a tabela (a versão da base de contratos
    # prevalece quando o registro está nas duas).
    anos = {linha[campo] for linha in contratacoes.values()
            for campo in ("ano_publicacao", "ano_assinatura") if linha[campo]}
    da_agregada = 0
    for chunk, registro in _registros_json(documents_file, BASE_PCA):
        if not _compra_ou_contrato_da_base_agregada(registro, anos):
            continue
        linha = _linha_contratacao(chunk, registro)
        if linha and linha["numero_controle_pncp"] not in contratacoes:
            contratacoes[linha["numero_controle_pncp"]] = linha
            da_agregada += 1
    planos = {
        linha["numero_controle_pncp"]: linha
        for chunk, registro in _registros_json(documents_file, BASE_PCA)
        if (linha := _linha_pca(chunk, registro))
    }
    resumo: dict[str, Any] = {
        "destino": banco.descricao(),
        "documents_file": str(documents_file),
        "source_sha256": _sha256(documents_file),
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "contratacoes": len(contratacoes),
        "contratacoes_da_base_agregada": da_agregada,
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


# Bancos já conferidos contra o documents.jsonl neste processo. A conferência
# abria uma conexão e fazia duas consultas a cada pergunta (~1,3 s no Neon); a
# chave muda quando o arquivo muda, e aí a conferência roda de novo. Só entra
# aqui o banco que estava em dia: o desatualizado continua sendo conferido.
_BANCOS_CONFERIDOS: set[tuple[str, str, str, int, int]] = set()


def _chave_conferencia(banco: Banco, documents_file: Path) -> tuple[str, str, str, int, int]:
    stat = documents_file.stat()
    return (banco.url, str(banco.sqlite_path), str(documents_file), stat.st_mtime_ns, stat.st_size)


def garantir_banco(
    documents_file: Path = DOCUMENTS_FILE,
    banco: Optional[Banco] = None,
    status: Optional[dict[str, Any]] = None,
    *,
    fast: bool = False,
) -> Banco:
    """Devolve o banco pronto, remontando-o se não existe ou se o
    ``documents.jsonl`` mudou desde a última montagem (SHA-256 no ``meta``).

    Com um usuário só de leitura (ex.: ``rag_leitor`` no Neon), a remontagem
    falha por permissão; se a base já existe, ela é usada como está e
    ``status["base_desatualizada"]`` fica ``True`` (o chamador leva ao log).
    Sem base nenhuma, o erro sobe.
    """
    if fast:
        banco = banco or banco_padrao()
        try:
            return validar_banco(documents_file, banco, fast=True)
        except Exception:
            construir_banco(documents_file, banco)
            return banco
    banco = banco or banco_padrao()
    if not banco.existe():
        construir_banco(documents_file, banco)
        _BANCOS_CONFERIDOS.add(_chave_conferencia(banco, documents_file))
        return banco
    chave = _chave_conferencia(banco, documents_file)
    if chave in _BANCOS_CONFERIDOS:
        return banco
    with banco.sessao():
        registrado = _sha_registrado(banco)
        try:
            versao = banco.consultar("SELECT valor FROM meta WHERE chave = ?", ["schema_version"])
        except Exception:
            versao = []
    if registrado == _sha256(documents_file) and versao and versao[0]["valor"] == SCHEMA_VERSION:
        _BANCOS_CONFERIDOS.add(chave)
        return banco
    try:
        construir_banco(documents_file, banco)
    except Exception as erro:
        if registrado is None:
            raise
        if status is not None:
            status["base_desatualizada"] = True
            status["erro_remontagem"] = type(erro).__name__
    else:
        _BANCOS_CONFERIDOS.add(chave)
    return banco


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Base estruturada em SQL (contratações e PCA).")
    parser.add_argument("--documents", type=Path, default=DOCUMENTS_FILE)
    parser.add_argument("--sqlite", type=Path, default=SQLITE_PATH, help="arquivo SQLite (sem DATABASE_URL)")
    comandos = parser.add_subparsers(dest="comando", required=True)
    comandos.add_parser("build", help="recria a base a partir do documents.jsonl")
    comandos.add_parser("fts", help="cria o índice do objeto (FTS5) numa base SQLite já montada")
    consulta = comandos.add_parser("sql", help="roda uma consulta somente leitura")
    consulta.add_argument("consulta")
    args = parser.parse_args(argv)

    banco = Banco(url=config.DATABASE_URL, sqlite_path=args.sqlite)
    if args.comando == "build":
        print(json.dumps(construir_banco(args.documents, banco), ensure_ascii=False, indent=2))
        return 0
    if args.comando == "fts":
        criado = indexar_objeto(banco)
        print("índice do objeto criado" if criado else "índice não criado (Postgres, base ausente ou SQLite sem FTS5)")
        return 0 if criado else 1
    validar_banco(args.documents, banco)
    linhas = banco.consultar(args.consulta)
    if linhas:
        print(" | ".join(linhas[0]))
    for linha in linhas:
        print(" | ".join("" if valor is None else str(valor) for valor in linha.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
