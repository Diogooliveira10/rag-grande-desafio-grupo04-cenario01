"""Cadeia de hashes no log JSONL: alterar ou apagar uma linha quebra a corrente.

Cada linha do ``pipeline-AAAA-MM-DD.jsonl`` ganha dois campos:

* ``hash_anterior``: o ``hash`` da linha anterior (no primeiro arquivo, 64 zeros;
  no primeiro registro de um dia, o último hash do dia anterior);
* ``hash``: HMAC-SHA256 do registro inteiro sem o próprio ``hash``, em JSON
  canônico (chaves ordenadas, sem espaços). Sem ``LOG_CHAIN_KEY``, SHA-256 puro.

Com a chave, quem só lê o arquivo não consegue recalcular uma cadeia nova.
Sem ela, a cadeia ainda pega a edição de uma linha, mas quem conhece o método
pode reescrever o arquivo inteiro. Nos dois casos, guardar o último hash fora
do servidor de tempos em tempos (``verificar`` o imprime) impede reescrever o
passado sem ser notado.

Linhas antigas, gravadas antes da cadeia, ficam como estão. A primeira linha
encadeada depois delas aponta para o SHA-256 da linha antiga anterior.

Verificar: ``python -m src.log_chain verificar [diretório]``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import re
import sys
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

GENESE = "0" * 64
_ARQUIVO_DO_DIA = re.compile(r"pipeline-\d{4}-\d{2}-\d{2}\.jsonl")
# Lido do fim do arquivo em blocos até achar a quebra de linha anterior.
_BLOCO = 64 * 1024


def _canonico(registro: dict[str, Any]) -> bytes:
    sem_hash = {chave: valor for chave, valor in registro.items() if chave != "hash"}
    return json.dumps(sem_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def calcular_hash(registro: dict[str, Any], chave: str = "") -> str:
    conteudo = _canonico(registro)
    if chave:
        return hmac.new(chave.encode("utf-8"), conteudo, hashlib.sha256).hexdigest()
    return hashlib.sha256(conteudo).hexdigest()


def encadear(payload: str, hash_anterior: str, chave: str = "") -> tuple[str, str]:
    """Linha JSON com ``hash_anterior`` e ``hash``; devolve a linha e o hash novo."""
    registro = json.loads(payload)
    registro.pop("hash", None)
    registro["hash_anterior"] = hash_anterior
    registro["hash"] = calcular_hash(registro, chave)
    return json.dumps(registro, ensure_ascii=False, default=str), registro["hash"]


def _hash_da_linha(linha: bytes) -> str:
    """Hash que a próxima linha deve apontar: o gravado, ou o da linha antiga."""
    try:
        registro = json.loads(linha.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        registro = None
    if isinstance(registro, dict) and isinstance(registro.get("hash"), str):
        return registro["hash"]
    return hashlib.sha256(linha.rstrip(b"\r\n")).hexdigest()


def _ultima_linha(caminho: Path) -> Optional[bytes]:
    """Última linha do arquivo, sem a quebra final; ``None`` se vazio."""
    with caminho.open("rb") as arquivo:
        fim = arquivo.seek(0, 2)
        if not fim:
            return None
        arquivo.seek(fim - 1)
        if arquivo.read(1) == b"\n":
            fim -= 1
        pedaco, posicao = b"", fim
        while posicao > 0:
            inicio = max(0, posicao - _BLOCO)
            arquivo.seek(inicio)
            pedaco = arquivo.read(posicao - inicio) + pedaco
            posicao = inicio
            quebra = pedaco.rfind(b"\n")
            if quebra >= 0:
                return pedaco[quebra + 1:]
        return pedaco


def ultimo_hash(caminho: Path) -> str:
    """Hash de onde a próxima linha de ``caminho`` continua a cadeia.

    Arquivo novo ou vazio continua do último arquivo do dia anterior na mesma
    pasta; sem nenhum, começa da gênese.
    """
    if caminho.is_file():
        linha = _ultima_linha(caminho)
        if linha is not None and linha.strip():
            return _hash_da_linha(linha)
    anteriores = sorted(p for p in caminho.parent.glob("pipeline-*.jsonl")
                        if _ARQUIVO_DO_DIA.fullmatch(p.name) and p.name < caminho.name and p.is_file())
    for anterior in reversed(anteriores):
        linha = _ultima_linha(anterior)
        if linha is not None and linha.strip():
            return _hash_da_linha(linha)
    return GENESE


@dataclass
class Relatorio:
    linhas: int = 0
    encadeadas: int = 0
    antigas: int = 0
    # Com chave: quantas linhas conferem com ela e quantas são de antes dela (SHA-256).
    com_chave: int = 0
    sem_chave: int = 0
    arquivos: int = 0
    ultimo_hash: str = GENESE
    problemas: list[str] = field(default_factory=list)

    @property
    def integra(self) -> bool:
        return not self.problemas


class _Conferencia:
    """Confere a cadeia linha a linha; ``verificar`` e ``VerificacaoContinua`` usam a mesma."""

    def __init__(self, chave: str) -> None:
        self.chave = chave
        self.relatorio = Relatorio()
        self.esperado: Optional[str] = None

    def linha(self, linha: bytes, onde: str) -> None:
        relatorio, chave = self.relatorio, self.chave
        if not linha.strip():
            return
        relatorio.linhas += 1
        try:
            registro = json.loads(linha.decode("utf-8-sig"))
        except (ValueError, UnicodeError):
            registro = None
        if not isinstance(registro, dict) or "hash" not in registro:
            if relatorio.encadeadas:
                relatorio.problemas.append(f"{onde}: linha sem hash depois do início da cadeia")
            else:
                relatorio.antigas += 1
            self.esperado = hashlib.sha256(linha.rstrip(b"\r\n")).hexdigest()
            return
        relatorio.encadeadas += 1
        if self.esperado is not None and registro.get("hash_anterior") != self.esperado:
            relatorio.problemas.append(f"{onde}: hash_anterior não bate com a linha anterior "
                                       "(linha apagada, inserida ou fora de ordem)")
        if calcular_hash(registro, chave) == registro["hash"]:
            if chave:
                relatorio.com_chave += 1
        elif chave and not relatorio.com_chave and calcular_hash(registro) == registro["hash"]:
            # Linha gravada antes de a chave existir (SHA-256). Só vale antes da
            # primeira linha com chave: essa guarda o hash da última sem chave,
            # então reescrever o trecho antigo quebra a corrente ali.
            relatorio.sem_chave += 1
        else:
            relatorio.problemas.append(f"{onde}: conteúdo alterado (ou chave diferente)")
        self.esperado = registro["hash"]

    def resultado(self) -> Relatorio:
        """Cópia do relatório até aqui, com o último hash conferido."""
        return replace(self.relatorio, ultimo_hash=self.esperado or GENESE,
                       problemas=list(self.relatorio.problemas))


def verificar(arquivos: Iterable[Path], chave: str = "") -> Relatorio:
    """Percorre os arquivos em ordem e aponta cada linha que quebra a cadeia.

    O primeiro arquivo pode começar de um hash que não é a gênese: os dias
    anteriores podem ter saído pela retenção (``LOG_RETENTION_DAYS``).
    """
    conferencia = _Conferencia(chave)
    for caminho in sorted(arquivos, key=lambda p: p.name):
        conferencia.relatorio.arquivos += 1
        with caminho.open("rb") as arquivo:
            for numero, linha in enumerate(arquivo, 1):
                conferencia.linha(linha, f"{caminho.name}:{numero}")
    return conferencia.resultado()


@dataclass(frozen=True)
class _ArquivoConferido:
    nome: str
    tamanho: int          # bytes conferidos (até o fim da última linha completa)
    mtime_ns: int
    linhas: int           # linhas lidas, para apontar ``arquivo:linha`` nas quebras
    inicio_da_ultima: int
    hash_da_ultima: str   # SHA-256 dos bytes da última linha conferida


class VerificacaoContinua:
    """A cadeia do diretório inteiro, conferida uma vez e depois só nas linhas novas.

    Conferir 1,7 GB de log leva ~45 s, caro demais para cada pedido de trace
    (2026-10-30). O log só cresce: o arquivo do dia recebe linhas no fim e os
    dias anteriores não mudam. Por isso a conferência continua de onde parou.
    Ela recomeça do zero quando um arquivo já conferido muda de outro jeito: um
    dia anterior com outro tamanho ou data, a última linha conferida alterada,
    um arquivo que encolheu ou um dia que sumiu. Uma edição no meio do arquivo
    do dia seguida de novas linhas só aparece na conferência completa
    (``python -m src.log_chain verificar``).

    ``estado`` nunca espera: devolve a última conferência concluída e dispara a
    próxima numa thread, uma por vez, como o ``/ready`` (2026-10-27).
    """

    def __init__(self, diretorio: Path, chave: str = "") -> None:
        self.diretorio = Path(diretorio)
        self.chave = chave
        self._conferencia: Optional[_Conferencia] = None
        self._arquivos: list[_ArquivoConferido] = []
        self._rodando = threading.Lock()
        self._ultima: Optional[tuple[Relatorio, str]] = None
        self._erro: Optional[str] = None

    def atualizar(self) -> Relatorio:
        """Confere o que falta, no próprio processo; devolve o relatório do diretório inteiro."""
        with self._rodando:
            return self._atualizar()

    def disparar(self) -> None:
        """Atualiza numa thread, se nenhuma estiver rodando."""
        if not self._rodando.acquire(blocking=False):
            return

        def rodar() -> None:
            try:
                self._atualizar()
            except Exception as erro:  # o trace segue, com a cadeia não verificada
                self._erro = type(erro).__name__
            finally:
                self._rodando.release()

        threading.Thread(target=rodar, name="log-chain-verificacao", daemon=True).start()

    def estado(self) -> dict[str, Any]:
        """Integridade do log para o trace, sem esperar a conferência."""
        self.disparar()
        if self._ultima is None:
            motivo = f"erro_na_verificacao:{self._erro}" if self._erro else "verificacao_em_andamento"
            return {"status": "nao_verificada", "algoritmo": self._algoritmo(), "motivo": motivo}
        relatorio, verificado_em = self._ultima
        return {
            "status": situacao(relatorio),
            "algoritmo": self._algoritmo(),
            "registros_verificados": relatorio.encadeadas,
            "registros_legados": relatorio.antigas,
            "problemas": len(relatorio.problemas),
            "verificado_em": verificado_em,
        }

    def _algoritmo(self) -> str:
        return "HMAC-SHA256" if self.chave else "SHA-256"

    def _atualizar(self) -> Relatorio:
        atuais = arquivos_do_diretorio(self.diretorio)
        if not self._pode_continuar(atuais):
            self._conferencia, self._arquivos = _Conferencia(self.chave), []
        conferidos = len(self._arquivos)
        for indice, caminho in enumerate(atuais):
            if indice < conferidos - 1:
                continue  # dia anterior, já conferido e sem mudança
            anterior = self._arquivos[indice] if indice < conferidos else None
            if anterior is None:
                self._conferencia.relatorio.arquivos += 1
            # Só o último arquivo pode ter uma linha ainda sendo gravada.
            arquivo = self._conferir_arquivo(caminho, anterior, ultimo=indice == len(atuais) - 1)
            if anterior is None:
                self._arquivos.append(arquivo)
            else:
                self._arquivos[indice] = arquivo
        relatorio = self._conferencia.resultado()
        self._ultima = (relatorio, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._erro = None
        return relatorio

    def _pode_continuar(self, atuais: list[Path]) -> bool:
        if self._conferencia is None or not self._arquivos:
            return False
        nomes = [caminho.name for caminho in atuais]
        if nomes[:len(self._arquivos)] != [arquivo.nome for arquivo in self._arquivos]:
            return False
        for conferido, caminho in zip(self._arquivos[:-1], atuais):
            stat = caminho.stat()
            if (stat.st_size, stat.st_mtime_ns) != (conferido.tamanho, conferido.mtime_ns):
                return False
        ultimo = self._arquivos[-1]
        caminho = atuais[len(self._arquivos) - 1]
        if caminho.stat().st_size < ultimo.tamanho:
            return False
        with caminho.open("rb") as arquivo:
            arquivo.seek(ultimo.inicio_da_ultima)
            cauda = arquivo.read(ultimo.tamanho - ultimo.inicio_da_ultima)
        return hashlib.sha256(cauda).hexdigest() == ultimo.hash_da_ultima

    def _conferir_arquivo(self, caminho: Path, anterior: Optional[_ArquivoConferido], *, ultimo: bool) -> _ArquivoConferido:
        posicao = anterior.tamanho if anterior else 0
        numero = anterior.linhas if anterior else 0
        inicio_da_ultima = anterior.inicio_da_ultima if anterior else 0
        hash_da_ultima = anterior.hash_da_ultima if anterior else hashlib.sha256(b"").hexdigest()
        with caminho.open("rb") as arquivo:
            arquivo.seek(posicao)
            for linha in arquivo:  # quebra só em \n, como ``verificar``, sem ler o arquivo inteiro na memória
                if ultimo and not linha.endswith(b"\n"):
                    break  # linha ainda sendo gravada: entra na próxima conferência
                numero += 1
                self._conferencia.linha(linha, f"{caminho.name}:{numero}")
                inicio_da_ultima, posicao = posicao, posicao + len(linha)
                hash_da_ultima = hashlib.sha256(linha).hexdigest()
        return _ArquivoConferido(caminho.name, posicao, caminho.stat().st_mtime_ns, numero,
                                 inicio_da_ultima, hash_da_ultima)


def situacao(relatorio: Relatorio) -> str:
    """``invalida`` (quebra), ``legado`` (nenhuma linha encadeada), ``parcial``
    (linhas de antes da cadeia seguidas das encadeadas) ou ``valida``."""
    if relatorio.problemas:
        return "invalida"
    if not relatorio.encadeadas:
        return "legado"
    return "parcial" if relatorio.antigas else "valida"


def arquivos_do_diretorio(diretorio: Path) -> list[Path]:
    return sorted(p for p in diretorio.glob("pipeline-*.jsonl") if _ARQUIVO_DO_DIA.fullmatch(p.name) and p.is_file())


def main(argv: Optional[list[str]] = None) -> int:
    import config

    parser = argparse.ArgumentParser(description="Cadeia de hashes do log do pipeline.")
    sub = parser.add_subparsers(dest="comando", required=True)
    cmd = sub.add_parser("verificar", help="confere a cadeia e imprime o último hash")
    cmd.add_argument("diretorio", nargs="?", default=str(config.PIPELINE_LOGS_DIR))
    args = parser.parse_args(argv)
    arquivos = arquivos_do_diretorio(Path(args.diretorio))
    relatorio = verificar(arquivos, config.LOG_CHAIN_KEY)
    print(f"{relatorio.arquivos} arquivos, {relatorio.linhas} linhas "
          f"({relatorio.encadeadas} encadeadas, {relatorio.antigas} anteriores à cadeia)")
    if config.LOG_CHAIN_KEY:
        print(f"com chave: {relatorio.com_chave}; de antes da chave (SHA-256): {relatorio.sem_chave}")
    for problema in relatorio.problemas[:50]:
        print(f"QUEBRA {problema}")
    if len(relatorio.problemas) > 50:
        print(f"... e mais {len(relatorio.problemas) - 50}")
    print(f"último hash: {relatorio.ultimo_hash}")
    print("íntegra" if relatorio.integra else "CADEIA QUEBRADA")
    return 0 if relatorio.integra else 1


if __name__ == "__main__":
    sys.exit(main())
