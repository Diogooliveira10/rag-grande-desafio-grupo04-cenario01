from __future__ import annotations

import json
import sqlite3
import zlib
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import config
from src.sensitivity import mascarar, mascarar_valores

DB_PATH = config.ROOT_DIR / "data" / "app_state" / "historico.sqlite"

# A resposta completa é guardada comprimida (zlib nível 6, a partir de 64 bytes),
# a mesma regra do compressor do DUOPEN: só é lida inteira, nunca pesquisada
# por SQL, então a compressão não atrapalha nenhuma consulta. Linhas antigas,
# gravadas como texto, continuam legíveis.
NIVEL_COMPRESSAO = 6
MIN_BYTES_COMPRESSAO = 64


def _comprimir_json(dados: Any) -> str | bytes:
    texto = json.dumps(dados, ensure_ascii=False)
    bruto = texto.encode("utf-8")
    return zlib.compress(bruto, NIVEL_COMPRESSAO) if len(bruto) >= MIN_BYTES_COMPRESSAO else texto


def _ler_json(valor: str | bytes) -> Any:
    if isinstance(valor, (bytes, bytearray, memoryview)):
        valor = zlib.decompress(bytes(valor)).decode("utf-8")
    return json.loads(valor)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    query TEXT NOT NULL,
    top_k INTEGER NOT NULL,
    resposta_json TEXT NOT NULL,
    confidence_level TEXT,
    is_refusal INTEGER NOT NULL,
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT NOT NULL REFERENCES consultas (query_id),
    ts TEXT NOT NULL,
    avaliacao TEXT NOT NULL,
    comentario TEXT
);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConsultaNaoEncontrada(Exception):
    """``registrar_feedback`` recebeu um ``query_id`` sem consulta correspondente."""


@dataclass(frozen=True)
class HistoricoStore:
    """Persistência SQLite do histórico de consultas e do feedback.

    Cada método abre e fecha sua própria conexão — o volume de uma API de
    desafio não justifica reaproveitar conexão como em ``structured_db.Banco``,
    e assim cada chamada já vê o schema criado (``CREATE TABLE IF NOT EXISTS``).

    Proteção do que fica gravado: pergunta, resposta e comentário do feedback
    passam pelo mascaramento de ``src/sensitivity.py`` (CPF, telefone, e-mail
    pessoal) antes do INSERT, e cada nova consulta apaga as que passaram de
    ``retencao_dias`` (``HISTORY_RETENTION_DAYS``; 0 = sem expurgo).
    """

    db_path: Path = field(default=DB_PATH)
    retencao_dias: int = field(default_factory=lambda: config.HISTORY_RETENTION_DAYS)

    def _conectar(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conexao = sqlite3.connect(self.db_path)
        conexao.row_factory = sqlite3.Row
        conexao.executescript(_SCHEMA)
        colunas = {linha["name"] for linha in conexao.execute("PRAGMA table_info(consultas)")}
        if "session_id" not in colunas:
            # Histórico gravado antes da rastreabilidade por sessão.
            conexao.execute("ALTER TABLE consultas ADD COLUMN session_id TEXT")
        conexao.execute(
            "CREATE INDEX IF NOT EXISTS idx_consultas_session ON consultas (session_id)"
        )
        return conexao

    def registrar_consulta(
        self,
        *,
        query_id: str,
        run_id: str,
        query: str,
        top_k: int,
        resposta: dict[str, Any],
        session_id: Optional[str] = None,
    ) -> None:
        """Grava a pergunta e a resposta do pipeline no histórico, mascaradas."""
        with closing(self._conectar()) as conexao, conexao:
            self._expurgar(conexao)
            conexao.execute(
                "INSERT OR REPLACE INTO consultas "
                "(query_id, run_id, ts, query, top_k, resposta_json, confidence_level, is_refusal, "
                "session_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    query_id,
                    run_id,
                    _agora(),
                    mascarar(query)[0],
                    top_k,
                    _comprimir_json(mascarar_valores(resposta)),
                    resposta.get("confidence_level"),
                    int(bool(resposta.get("is_refusal"))),
                    session_id,
                ),
            )

    def obter_consulta(self, query_id: str) -> Optional[dict[str, Any]]:
        """Devolve a consulta registrada, ou ``None`` se ``query_id`` é desconhecido."""
        with closing(self._conectar()) as conexao:
            linha = conexao.execute(
                "SELECT * FROM consultas WHERE query_id = ?", (query_id,)
            ).fetchone()
            return self._linha_para_consulta(conexao, linha) if linha else None

    def registrar_feedback(
        self, *, query_id: str, avaliacao: str, comentario: Optional[str] = None
    ) -> None:
        """Associa uma avaliação a uma consulta já registrada.

        Levanta ``ConsultaNaoEncontrada`` se ``query_id`` não existir no
        histórico — a API traduz isso em HTTP 404 em vez de aceitar feedback
        órfão.
        """
        with closing(self._conectar()) as conexao, conexao:
            existe = conexao.execute(
                "SELECT 1 FROM consultas WHERE query_id = ?", (query_id,)
            ).fetchone()
            if existe is None:
                raise ConsultaNaoEncontrada(query_id)
            conexao.execute(
                "INSERT INTO feedback (query_id, ts, avaliacao, comentario) VALUES (?, ?, ?, ?)",
                (query_id, _agora(), avaliacao,
                 mascarar(comentario)[0] if comentario else comentario),
            )

    def _expurgar(self, conexao: sqlite3.Connection) -> None:
        """Apaga consultas (e o feedback delas) mais antigas que ``retencao_dias``."""
        if self.retencao_dias <= 0:
            return
        limite = (datetime.now(timezone.utc) - timedelta(days=self.retencao_dias)).isoformat(
            timespec="seconds"
        )
        conexao.execute(
            "DELETE FROM feedback WHERE query_id IN (SELECT query_id FROM consultas WHERE ts < ?)",
            (limite,),
        )
        conexao.execute("DELETE FROM consultas WHERE ts < ?", (limite,))

    def obter_historico(
        self, *, limit: int = 20, session_id: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """As ``limit`` consultas mais recentes, com o feedback recebido (se houver).

        Com ``session_id``, só as consultas daquela conversa.
        """
        filtro, parametros = ("WHERE session_id = ? ", (session_id,)) if session_id else ("", ())
        with closing(self._conectar()) as conexao:
            # "ts" tem resolução de segundo: várias consultas no mesmo
            # segundo empatam, então o rowid (ordem de inserção) desempata.
            linhas = conexao.execute(
                f"SELECT * FROM consultas {filtro}ORDER BY ts DESC, rowid DESC LIMIT ?",
                (*parametros, limit),
            ).fetchall()
            return [self._linha_para_consulta(conexao, linha) for linha in linhas]

    def _linha_para_consulta(self, conexao: sqlite3.Connection, linha: sqlite3.Row) -> dict[str, Any]:
        feedbacks = conexao.execute(
            "SELECT avaliacao, comentario, ts FROM feedback WHERE query_id = ? ORDER BY ts",
            (linha["query_id"],),
        ).fetchall()
        return {
            "query_id": linha["query_id"],
            "run_id": linha["run_id"],
            "session_id": linha["session_id"],
            "ts": linha["ts"],
            "query": linha["query"],
            "resposta": _ler_json(linha["resposta_json"]),
            "feedback": [dict(f) for f in feedbacks],
        }


default_store = HistoricoStore()

__all__ = ["HistoricoStore", "ConsultaNaoEncontrada", "default_store", "DB_PATH"]
