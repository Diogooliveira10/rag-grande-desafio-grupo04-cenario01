"""Trechos de evidência como texto para o prompt: cabeçalho, registro compacto, conversa e orçamento de caracteres.

Separado de src/generate.py em 2026-10-27 sem mudar a lógica; os nomes
continuam importáveis de lá.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import config
from src.schema import Chunk, ChunkMetadata, Natureza


def _natureza_label(metadata: ChunkMetadata) -> str:
    """``natureza`` é a classificação Adaptive RAG; ``doc_type`` é o formato do
    arquivo (json/pdf/html) — só cai para ``doc_type`` se natureza não veio."""
    return metadata.natureza.value if metadata.natureza is not None else metadata.doc_type


def _modalidade_vinculada(metadata: ChunkMetadata) -> Optional[str]:
    """Modalidade que o contrato herda da compra vinculada (src/corpus_quality.py)."""
    if metadata.extras.get("modalidade_fonte") == "compra_vinculada" and metadata.extras.get("modalidade"):
        return f"{metadata.extras['modalidade']} (da compra vinculada)"
    return None


def _cabecalho(metadata: ChunkMetadata, evidence_id: str | None = None) -> str:
    evidence_id = evidence_id or metadata.chunk_id
    campos = [
        f"evidence_id={evidence_id}",
        f"base={metadata.base_id}",
        f"natureza={_natureza_label(metadata)}",
    ]
    if metadata.extras.get("sintetico"):
        campos.append("sintetico=true; cenário fictício, não comprova fatos reais")
    # Distingue a ata em si de publicações anexadas (ex.: Diário Oficial).
    if metadata.extras.get("titulo_documento"):
        campos.append(f"documento={metadata.extras['titulo_documento']}")
    if metadata.data_vigencia is not None:
        campos.append(f"vigencia_ate={metadata.data_vigencia.isoformat()}")
    # Metadados de src/corpus_quality.py: início da vigência das normas,
    # valor simbólico do PNCP e modalidade herdada da compra.
    if metadata.extras.get("data_publicacao"):
        campos.append(f"publicacao_informada={metadata.extras['data_publicacao']}")
    if metadata.extras.get("data_vigencia_inicio"):
        campos.append(f"vigente_desde={metadata.extras['data_vigencia_inicio']}")
    if metadata.extras.get("valor_simbolico"):
        campos.append("valor_simbolico=true; R$ 0,00 ou R$ 0,01 no PNCP não é o custo real")
    modalidade = _modalidade_vinculada(metadata)
    if modalidade:
        campos.append(f"modalidade={modalidade}")
    if metadata.extras.get("subpergunta"):
        campos.append(f"intencao={metadata.extras['intencao']}: {metadata.extras['subpergunta']}")
    if metadata.extras.get("filtros_relaxados"):
        campos.append("recorte_nao_comprovado=" + json.dumps(metadata.extras["filtros_relaxados"], ensure_ascii=False))
    if metadata.extras.get("calculo_disponivel") is False:
        campos.append("calculo_disponivel=false")
    if metadata.extras.get("vigencia_historica_verificada") is False or (
        metadata.natureza == Natureza.NORMATIVA and metadata.data_vigencia is None):
        campos.append("vigencia_historica=não verificada")
    return f"[{' | '.join(campos)}]"


_VAZIOS = (None, "", [], {})


def _sem_vazios(valor: Any) -> Any:
    if isinstance(valor, dict):
        limpo = {chave: _sem_vazios(item) for chave, item in valor.items()}
        return {chave: item for chave, item in limpo.items() if item not in _VAZIOS}
    if isinstance(valor, list):
        return [item for item in (_sem_vazios(i) for i in valor) if item not in _VAZIOS]
    return valor


def _compactar(conteudo: str) -> str:
    """Registro JSON (PNCP) vira JSON compacto, sem campos vazios; texto só
    perde espaços repetidos. O JSON indentado gastava boa parte da janela."""
    try:
        dados = json.loads(conteudo)
    except (TypeError, ValueError):
        return " ".join(conteudo.split())
    dados = _sem_vazios(dados)
    if isinstance(dados, dict):
        # Os campos de evidência devem caber antes de campos administrativos
        # extensos. Apenas reordena o registro, preservando valores e unidades.
        prioridade = (
            "numeroControlePNCP", "valorGlobal", "valorInicial", "valorTotalHomologado",
            "valorTotalEstimado", "valorUnitarioHomologado", "valorTotal", "unidadeMedida",
            "quantidadeHomologada", "nomeRazaoSocialFornecedor", "dataAssinatura", "anoPca",
            "descricao", "descricaoResumida", "objetoContrato", "objetoCompra", "objeto",
            "modalidadeNome", "amparoLegal", "categoriaProcesso", "orgaoEntidade", "unidadeOrgao",
        )
        dados = {**{campo: dados[campo] for campo in prioridade if campo in dados},
                 **{campo: valor for campo, valor in dados.items() if campo not in prioridade}}
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":"))


def _representar_registro(conteudo: str, modalidade_vinculada: Optional[str] = None) -> str:
    """Visão compacta dos campos relevantes, sem alterar o documento original.

    ``modalidade_vinculada`` é a modalidade da compra ligada ao contrato
    (src/corpus_quality.py). Sem ela, o campo dizia "não informada" enquanto o
    cabeçalho do mesmo trecho trazia a modalidade.
    """
    try:
        registro = json.loads(conteudo)
    except (ValueError, TypeError):
        return conteudo
    if not isinstance(registro, dict):
        return conteudo
    campos = ("numeroControlePNCP", "numeroControlePNCPAta", "objetoContrato", "objetoCompra",
        "objetoContratacao", "descricao", "descricaoResumida", "nomeRazaoSocialFornecedor",
        # Registros de ATA do PNCP identificam órgão/unidade e vigência com
        # outros nomes de campo (nomeOrgao/nomeUnidadeOrgao/vigencia*). Sem
        # eles, a evidência do prompt ficava sem órgão nem vigência e a
        # resposta honesta era "não consta" (defeito #18).
        "nomeOrgao", "nomeUnidadeOrgao", "cnpjOrgao", "numeroAtaRegistroPreco",
        "dataAssinatura", "dataPublicacaoPncp", "dataAberturaProposta", "dataEncerramentoProposta",
        "dataVigenciaInicio", "dataVigenciaFim", "vigenciaInicio", "vigenciaFim",
        "anoPca", "valorGlobal", "valorInicial",
        "valorTotalHomologado", "valorTotalEstimado", "valorUnitarioHomologado", "valorUnitarioEstimado",
        "valorTotal", "valor", "quantidade", "quantidadeHomologada", "unidadeMedida", "amparoLegal",
        "orgaoEntidade", "unidadeOrgao", "municipio", "uf", "esfera", "categoriaProcesso", "tipoContrato", "modalidadeNome")
    view = {k: registro[k] for k in campos if k in registro and registro[k] is not None}
    if "categoriaProcesso" in registro or "objetoContrato" in registro or "objetoCompra" in registro:
        view["modalidade_licitacao"] = (registro.get("modalidadeNome") or modalidade_vinculada
                                        or "não informada neste registro")
    return json.dumps(view, ensure_ascii=False, separators=(",", ":")) if view else conteudo


# Conversa sintética (texto de src/synthetic_conversations.py). Inteira, ela
# passa do limite do trecho (941 a 1.803 caracteres contra os 900 de
# GENERATION_CHUNK_CHARS), e o corte comia a mensagem: a resposta do órgão, na
# segunda, nunca chegava ao LLM. Saem o aviso (o cabeçalho já diz
# sintetico=true), o id interno, o canal, a data da conversa (cada mensagem tem a
# sua) e os endereços; os identificadores vão para depois das mensagens. Se
# ainda passar do limite, o espaço é dividido entre as mensagens.
_CONVERSA_DESCARTADOS = frozenset({"Conversa", "Canal", "Data da conversa", "E-mail", "Para"})


_CONVERSA_NO_FIM = ("Processo", "ARP", "CNPJ")


_CONVERSA_ROTULOS_DA_MENSAGEM = frozenset({"Data", "De", "Função", "E-mail", "Para"})


_DATA_E_HORA_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T[\d:.]+(?:[+-]\d{2}:\d{2}|Z)?")


_CORTE = " [...]"


def _dividir_entre_mensagens(mensagens: list[str], espaco: int) -> list[str]:
    """Cada mensagem ganha uma fatia igual do espaço, e a que não usa a fatia
    inteira deixa a sobra para as outras. Quem passa da fatia perde o fim."""
    cortadas = list(mensagens)
    da_menor_para_maior = sorted(range(len(mensagens)), key=lambda i: len(mensagens[i]))
    for posicao, i in enumerate(da_menor_para_maior):
        fatia = espaco // (len(mensagens) - posicao)
        if len(mensagens[i]) > fatia:
            cortadas[i] = mensagens[i][:max(0, fatia - len(_CORTE))].rstrip() + _CORTE
        espaco -= len(cortadas[i])
    return cortadas


def _representar_conversa(conteudo: str, limite: int) -> str | None:
    """Visão compacta da conversa sintética, sem alterar o documento original.
    Fora do formato esperado, devolve None e o trecho segue o caminho comum."""
    blocos = [bloco for bloco in conteudo.split("\n\n") if bloco.strip()]
    if len(blocos) < 2 or not blocos[0].lstrip().startswith("[CONTEÚDO SINTÉTICO"):
        return None
    cabecalho, fim = [], []
    for linha in blocos[0].splitlines()[1:]:
        rotulo, _, valor = linha.partition(": ")
        if rotulo in _CONVERSA_DESCARTADOS or not valor.strip():
            continue
        (fim if rotulo in _CONVERSA_NO_FIM else cabecalho).append(f"{rotulo}: {valor.strip()}")
    mensagens: list[str] = []
    for bloco in blocos[1:]:
        campos: dict[str, str] = {}
        texto: list[str] = []
        for linha in bloco.splitlines():
            rotulo, _, valor = linha.partition(": ")
            if not texto and rotulo in _CONVERSA_ROTULOS_DA_MENSAGEM and valor.strip():
                campos[rotulo] = valor.strip()
            else:
                texto.append(linha)
        corpo = " ".join(" ".join(texto).split())
        if not campos and mensagens:
            mensagens[-1] += " " + corpo  # parágrafo seguinte da mesma mensagem
            continue
        autor = ", ".join(valor for valor in (
            _DATA_E_HORA_RE.sub(r"\1", campos.get("Data", "")), campos.get("De"), campos.get("Função"),
        ) if valor)
        mensagens.append(f"Mensagem ({autor}): {corpo}" if autor else f"Mensagem: {corpo}")
    partes = cabecalho + mensagens + fim
    if len(" | ".join(partes)) > limite:
        fixo = sum(len(parte) for parte in cabecalho + fim) + len(" | ") * (len(partes) - 1)
        mensagens = _dividir_entre_mensagens(mensagens, limite - fixo)
    return " | ".join(cabecalho + mensagens + fim)


def _chunks_no_orcamento(
    chunks: list[Chunk], bases_requeridas: list[Natureza] | None = None,
) -> list[Chunk]:
    """Limita contexto preservando candidatos por intenção, base e natureza."""
    preparados = []
    for chunk in chunks:
        conteudo = (_representar_conversa(chunk.content, config.GENERATION_CHUNK_CHARS)
                    if chunk.metadata.extras.get("sintetico") else None)
        if conteudo is None:
            conteudo = _representar_registro(_compactar(chunk.content), _modalidade_vinculada(chunk.metadata))
        if len(conteudo) > config.GENERATION_CHUNK_CHARS:
            conteudo = conteudo[:config.GENERATION_CHUNK_CHARS].rstrip() + " [...]"
        preparados.append(chunk.model_copy(update={"content": conteudo}))
    primeiros, restantes, grupos = [], [], set()
    for chunk in preparados:
        grupo = (chunk.metadata.extras.get("intencao", 0), chunk.metadata.base_id)
        if grupo not in grupos:
            primeiros.append(chunk)
            grupos.add(grupo)
        else:
            restantes.append(chunk)
    ordem = primeiros + restantes
    reservas = []
    naturezas = list(dict.fromkeys(bases_requeridas or []))
    if len(naturezas) > 1:
        reservas = [next(i for i, chunk in enumerate(ordem) if chunk.metadata.natureza == natureza)
                    for natureza in naturezas if any(chunk.metadata.natureza == natureza for chunk in ordem)]
    resto = [i for i in range(len(ordem)) if i not in reservas]
    if len(naturezas) > 1:
        # Depois da reserva, as naturezas se alternam: o 2º trecho de cada uma
        # entra antes do 3º de qualquer outra. Sem isso, as partes do resultado
        # SQL, que vêm primeiro, ocupavam a janela de 3.500 caracteres e só 1
        # trecho normativo cabia nas perguntas mistas (P6.3).
        filas: dict[Any, list[int]] = {}
        for i in resto:
            filas.setdefault(ordem[i].metadata.natureza, []).append(i)
        resto = []
        while any(filas.values()):
            for fila in filas.values():
                if fila:
                    resto.append(fila.pop(0))
    indices = reservas + resto
    escolhidos, usados = [], 0
    for i in indices:
        chunk = ordem[i]
        tamanho = len(_cabecalho(chunk.metadata, f"E{len(escolhidos)+1}")) + len(chunk.content)
        if tamanho > config.GENERATION_CONTEXT_CHARS - usados:
            continue
        escolhidos.append(i)
        usados += tamanho
    escolhidos.sort()
    return [ordem[i] for i in escolhidos]
