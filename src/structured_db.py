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
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from contextvars import ContextVar
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

import config

SQLITE_PATH = config.ROOT_DIR / "data" / "structured" / "base_estruturada.sqlite"
DOCUMENTS_FILE = config.NORMALIZED_DATA_DIR / "documents.jsonl"

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
        "tipo_registro": "contrato_assinado" if contrato_assinado else "publicacao_compra",
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
    if registrado == _sha256(documents_file) and versao and versao[0]["valor"] == SCHEMA_VERSION:
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
