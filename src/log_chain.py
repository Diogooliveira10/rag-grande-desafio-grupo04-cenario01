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
from dataclasses import dataclass, field
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


def verificar(arquivos: Iterable[Path], chave: str = "") -> Relatorio:
    """Percorre os arquivos em ordem e aponta cada linha que quebra a cadeia.

    O primeiro arquivo pode começar de um hash que não é a gênese: os dias
    anteriores podem ter saído pela retenção (``LOG_RETENTION_DAYS``).
    """
    relatorio = Relatorio()
    esperado: Optional[str] = None
    for caminho in sorted(arquivos, key=lambda p: p.name):
        relatorio.arquivos += 1
        with caminho.open("rb") as arquivo:
            for numero, linha in enumerate(arquivo, 1):
                if not linha.strip():
                    continue
                relatorio.linhas += 1
                onde = f"{caminho.name}:{numero}"
                try:
                    registro = json.loads(linha.decode("utf-8-sig"))
                except (ValueError, UnicodeError):
                    registro = None
                if not isinstance(registro, dict) or "hash" not in registro:
                    if relatorio.encadeadas:
                        relatorio.problemas.append(f"{onde}: linha sem hash depois do início da cadeia")
                    else:
                        relatorio.antigas += 1
                    esperado = hashlib.sha256(linha.rstrip(b"\r\n")).hexdigest()
                    continue
                relatorio.encadeadas += 1
                if esperado is not None and registro.get("hash_anterior") != esperado:
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
                esperado = registro["hash"]
    relatorio.ultimo_hash = esperado or GENESE
    return relatorio


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
