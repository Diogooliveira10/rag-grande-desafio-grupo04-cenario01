"""Órgão, município, fornecedor e CNPJ citados na pergunta.

Reconhece as entidades pelo catálogo do corpus (data/config/orgaos_corpus.json,
gerado por ``gerar_catalogo_de_orgaos``) e pelo texto: "Prefeitura de Maricá",
"SEEDUC", "empresa Cristália", "CNPJ 33.663.683/0001-16". Devolve o nome
canônico e o trecho que o citou, para sair dos termos do objeto.

Movido de src/query_analysis.py em 2026-10-24 sem mudar a lógica; os nomes
continuam importáveis de lá. Testes que trocam o catálogo ou a estatística de
palavras (``ORGAOS_CORPUS_PATH``, ``_palavras_dos_objetos``) fazem a troca aqui.
"""

from __future__ import annotations

import json
import re
import hashlib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence
from src.domain.values_and_periods import Span
from src.analysis.suppliers import APELIDOS_CONHECIDOS, IndiceFornecedores, compor_fornecedores


def _normalize(texto: str) -> str:
    dobrado = texto.casefold()
    sem_acentos = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", dobrado)
        if not unicodedata.combining(caractere)
    )
    return " ".join(sem_acentos.split())


_GATILHOS_ORGAO = frozenset(
    """
    secretaria prefeitura ministerio tribunal instituto fundacao autarquia universidade
    departamento camara assembleia orgao agencia fundo comando banco defensoria municipio
    procuradoria controladoria policia companhia empresa conselho
    """.split()
)


# Gatilho que só vale com a palavra seguinte: "empresa pública", não "a empresa X".
_GATILHO_EXIGE = {"empresa": frozenset({"publica", "municipal", "estadual"})}


# Gatilho + palavra que não é órgão, mesmo com maiúscula ("Banco de Preços" é
# ferramenta de pesquisa de preços).
_NAO_E_ORGAO = frozenset(
    {
        ("banco", "precos"), ("banco", "preco"), ("banco", "dados"), ("banco", "horas"),
        ("banco", "sangue"), ("camara", "ar"), ("fundo", "reserva"),
    }
)


# Adjetivos logo depois do gatilho: "Secretaria Municipal de Saúde", "Polícia
# Militar", "Defensoria Pública", "Procuradoria Geral".
_QUALIFICADORES_DE_ORGAO = frozenset(
    """
    municipal estadual federal nacional regional geral publica publico especial executiva
    executivo militar civil legislativa metropolitana superior extraordinaria central
    """.split()
)


# Dentro do nome só entram estas conectoras ("Secretaria de Ciência e
# Tecnologia", "Tribunal de Contas do Estado"). "No", "na", "em" encerram o
# nome: "Secretaria de Saúde no PCA" é a Secretaria de Saúde.
_PALAVRA_CONECTORA = frozenset({"de", "do", "da", "dos", "das", "e"})


# Palavras que encerram o nome mesmo com maiúscula ("Prefeitura De Maricá Em
# 2025"): preposições de lugar e siglas/termos do sistema que vêm depois dele.
_FIM_DO_NOME_DO_ORGAO = frozenset(
    "em no na nos nas pelo pela pelos pelas para "
    "pca pncp sei siga lei decreto portaria edital pregao ata atas contrato contratos".split()
)


# Palavras que não fazem parte de nome de órgão, mesmo depois de "de".
_NAO_SAO_NOME = frozenset(
    """
    qual quais quanto quantos quantas que como onde quando cujo cuja contratacao contratacoes
    compra compras valor valores acima abaixo maior menor maiores menores total media mediana
    ano anos mes meses ate entre mais menos todos todas algum alguma cada outro outros
    o a os as um uma
    janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro
    """.split()
) | _FIM_DO_NOME_DO_ORGAO


# Como o PNCP grava: "Prefeitura de Niterói" aparece como "MUNICIPIO DE NITEROI".
_SINONIMOS_DE_ORGAO = {"prefeitura": "municipio"}


# Siglas de órgãos do RJ, pelo nome que aparece no PNCP.
_SIGLAS_DE_ORGAO = {
    "seeduc": "Secretaria de Estado de Educação",
    "ses": "Secretaria de Estado de Saúde",
    "sefaz": "Secretaria de Estado de Fazenda",
    "seplag": "Secretaria de Estado de Planejamento e Gestão",
    "seap": "Secretaria de Estado de Administração Penitenciária",
    "dpe": "Defensoria Pública do Estado",
    "dperj": "Defensoria Pública do Estado",
    "pge": "Procuradoria Geral do Estado",
    "tce": "Tribunal de Contas do Estado",
    "tcerj": "Tribunal de Contas do Estado",
    "tjrj": "Tribunal de Justiça do Estado do Rio de Janeiro",
    "mprj": "Ministério Público do Estado do Rio de Janeiro",
    "alerj": "Assembleia Legislativa do Estado do Rio de Janeiro",
    "detran": "Departamento de Trânsito",
    "cedae": "Companhia Estadual de Águas e Esgotos",
    "uerj": "Universidade do Estado do Rio de Janeiro",
    "uenf": "Universidade Estadual do Norte Fluminense",
    "faetec": "Fundação de Apoio à Escola Técnica",
    "pmerj": "Polícia Militar do Estado do Rio de Janeiro",
    "pcerj": "Polícia Civil do Estado do Rio de Janeiro",
    "cbmerj": "Corpo de Bombeiros Militar do Estado do Rio de Janeiro",
    "inea": "Instituto Estadual do Ambiente",
    "degase": "Departamento Geral de Ações Socioeducativas",
    # Órgãos federais com sede no RJ
    "fiocruz": "Fundação Oswaldo Cruz",
    "ufrj": "Universidade Federal do Rio de Janeiro",
    "uff": "Universidade Federal Fluminense",
    "unirio": "Universidade Federal do Estado do Rio de Janeiro",
    "ufrrj": "Universidade Federal Rural do Rio de Janeiro",
    "inca": "Instituto Nacional de Câncer",
}


_PONTUACAO = ".,;:!?()\"'“”‘’"


_PALAVRAS_FUNCIONAIS = frozenset("de da do das dos em no na e a o os as que para com por pela pelo".split())


# src/analysis/entities.py -> raiz do projeto é parents[2].
ORGAOS_CORPUS_PATH = Path(__file__).resolve().parents[2] / "data" / "config" / "orgaos_corpus.json"


_TOKEN_DE_NOME_RE = re.compile(r"[a-z0-9]+")


_VAZIAS_DE_NOME = frozenset("de do da dos das e em a o rj".split())


_SIGLA_NO_NOME_RE = re.compile(r"(?:^|\s-\s)([A-Z]{2,12})(?:/[A-Z]{2})?(?=\s-\s|\s*$)|^([A-Z]{3,12})\s-\s")


@dataclass(frozen=True)
class _Catalogo:
    nomes: tuple[str, ...]
    tokens: tuple[tuple[str, ...], ...]
    siglas: dict[str, str]  # sigla normalizada -> nome do órgão
    # Listas canônicas do snapshot, para o mesmo valor escrito de outro jeito
    # virar o mesmo filtro ("Volta Redonda", "empresa Cristália").
    municipios: tuple[str, ...] = ()
    fornecedores: tuple[str, ...] = ()
    indice_fornecedores: Optional[IndiceFornecedores] = None
    apelidos: Optional[dict[str, str]] = None
    palavras_objetos: Optional[dict[str, tuple[int, int]]] = None
    assinatura: str = ""


def _tokens_de_nome(texto: str) -> list[str]:
    return [t for t in _TOKEN_DE_NOME_RE.findall(_normalize(texto)) if t not in _VAZIAS_DE_NOME]


@lru_cache(maxsize=1)
def _catalogo_de_orgaos() -> Optional[_Catalogo]:
    if not ORGAOS_CORPUS_PATH.exists():
        return None
    dados = json.loads(ORGAOS_CORPUS_PATH.read_text(encoding="utf-8"))
    nomes = tuple(dados.get("nomes", []))
    return _Catalogo(
        nomes, tuple(tuple(_tokens_de_nome(n)) for n in nomes), dict(dados.get("siglas", {})),
        tuple(dados.get("municipios", ())), tuple(dados.get("fornecedores", ())),
        IndiceFornecedores(dados) if "fornecedores_identidades" in dados else None,
        dict(dados.get("apelidos_fornecedor", {})), dados.get("palavras_objetos"),
        str(dados.get("fonte_sha256", "")) + str(dados.get("fornecedores_sha256", "")),
    )


def _token_casa(da_pergunta: str, do_nome: str) -> bool:
    """Igual, ou abreviado no nome ("SECRET ESTADO SAÚDE")."""
    if da_pergunta == do_nome:
        return True
    return len(do_nome) >= 4 and len(da_pergunta) >= 4 and (
        da_pergunta.startswith(do_nome) or do_nome.startswith(da_pergunta)
    )


def _orgao_do_catalogo(chaves: Sequence[str], exigir_algum: Sequence[str] = ()) -> Optional[str]:
    """Primeiro órgão do catálogo que contém todas as palavras-chave (e, se
    pedido, ao menos uma de ``exigir_algum``)."""
    catalogo = _catalogo_de_orgaos()
    if not catalogo or not chaves:
        return None
    for nome, tokens in zip(catalogo.nomes, catalogo.tokens):
        if exigir_algum and not any(token in exigir_algum for token in tokens):
            continue
        if all(any(_token_casa(chave, token) for token in tokens) for chave in chaves):
            return nome
    return None


def _orgao_do_municipio(cidade: Sequence[str]) -> str:
    """Nome com que o PNCP grava a prefeitura: "MUNICIPIO DE MARICA" é a razão
    social do órgão ("PREFEITURA MUNICIPAL DE MARICA" é o nome da unidade). Vem
    do catálogo quando a cidade está nele; senão, "Município de <cidade>"."""
    chaves = _tokens_de_nome(" ".join(cidade))
    catalogo = _catalogo_de_orgaos()
    for nome, tokens in (zip(catalogo.nomes, catalogo.tokens) if catalogo else ()):
        if tokens[:1] == ("municipio",) and list(tokens[1:]) == chaves:
            return nome
    return "Município de " + " ".join(cidade)


# Palavras que acompanham o nome do fornecedor mas não o identificam: fora
# delas, "empresa Cristália" acha "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS
# LTDA" sem precisar citar o nome inteiro.
_GENERICAS_DE_FORNECEDOR = frozenset(
    "ltda epp eireli sa me comercio industria produtos servicos assessoria "
    "representacoes distribuicao importacao exportacao consultoria engenharia "
    "construcoes equipamentos sistemas solucoes participacoes empreendimentos "
    "negocios grupo empresa fornecedor "
    # Palavras de órgão público: sozinhas não nomeiam um fornecedor. Na v7,
    # "Qual órgão, município/UF..." virava fornecedor "MUNICIPIO DE CASIMIRO
    # DE ABREU", o único com "municipio" no nome (A1 e A2 do golden set).
    "municipio municipios prefeitura prefeituras estado estadual governo secretaria "
    "camara fundo uf tecnologia limpeza urbana saude educacao informatica "
    "conservacao manutencao transporte alimentacao escolar material materiais "
    # "Defensoria Pública do Estado do Rio de Janeiro" virava o fornecedor
    # "CEAP RIO DE JANEIRO ... GESTÃO PÚBLICA" (pública + rio + janeiro).
    "publica publico publicas publicos".split()
)


# Palavra que anuncia que o que vem depois é o nome de um fornecedor. Sem uma
# delas (ou sem nome de duas palavras), não vira filtro: senão "com mais
# frequência" acharia "MED MAIS SOLUCOES..." por causa do "mais".
_GATILHOS_DE_FORNECEDOR = frozenset(
    "empresa fornecedor fornecedora firma comercial companhia sociedade "
    "industria laboratorio clinica hospital cooperativa associacao fundacao "
    "construtora transportadora distribuidora representante".split()
    # "órgão" não anuncia fornecedor: em "o órgão X", X é o contratante. Como
    # gatilho, "Qual é o órgão, a data de assinatura..." virava fornecedor
    # "LINK DATA INFORMÁTICA E SERVIÇOS LTDA" na v7 (I2 do golden set).
)


def _valor_do_catalogo(opcoes: Sequence[str], mencionados: Sequence[str]) -> Optional[str]:
    """Único valor do catálogo que contém todos os tokens citados.

    Token compara por igualdade depois de normalizar ("Cristália" ==
    "CRISTALIA"), não por prefixo: "contratos" não pode casar com "CONTRA".
    Empate entre valores diferentes ("Rio" casa "Rio de Janeiro" e "Rio das
    Ostras") devolve ``None``: não se escolhe o primeiro arbitrariamente.
    """
    if not mencionados:
        return None
    pedidos = [_normalize(m) for m in mencionados]
    casados = [valor for valor in opcoes
               if all(pedido in _tokens_de_nome(valor) for pedido in pedidos)]
    return casados[0] if len(casados) == 1 else None


# Palavras que só introduzem o nome do município/órgão: vão junto do trecho
# para não sobrarem soltas e virarem outro filtro ("do Município de Maricá"
# não pode achar um fornecedor que também é município).
_INTRODUTORES_DE_NOME = frozenset(
    "de do da dos das em no na nos nas municipio cidade prefeitura estado".split()
)


def _estender_para_tras(texto: str, span: Span) -> Span:
    """Puxa o início do trecho para trás enquanto a palavra anterior introduz nome."""
    inicio, fim = span
    for achado in reversed(list(re.finditer(r"\w+", texto[:inicio]))):
        palavra = _normalize(achado.group())
        if palavra not in _INTRODUTORES_DE_NOME:
            break
        inicio = achado.start()
    return (inicio, fim)


# Palavras que indicam que a menção é ao ESTADO, não ao município: "órgãos
# estaduais do Rio de Janeiro" é recorte estadual, e "Rio de Janeiro" é o nome
# dos dois. Sem isso, o filtro municipal apaga contratos de outros municípios
# fluminenses (os de ambulância, por exemplo, são de Paraty).
_ESCOPO_ESTADUAL = frozenset(
    "estado estadual estaduais estadualmente fluminense fluminenses "
    "poder governos governo".split()
)


# "Rio de Janeiro" é o nome do Estado e da capital. Sem um marcador de cidade,
# a menção é ao Estado ("órgãos públicos do Rio de Janeiro"); tratar como
# município recortaria o Estado inteiro para uma cidade e apagaria contratos de
# outros municípios fluminenses. Só o marcador explícito fecha o recorte.
_MARCADOR_DE_CIDADE = frozenset(
    "municipio municipio cidade prefeitura camara capital bairro municipais".split()
)


_NOME_DUPLICADO_ESTADO_CIDADE = frozenset({"rio de janeiro"})


def _em_escopo_estadual(texto: str, span: Span) -> bool:
    """True quando a menção ao município está num contexto de Estado."""
    palavras_antes = texto[:span[0]].split()[-4:]
    palavras_depois = texto[span[1]:].split()[:3]
    return any(_normalize(p) in _ESCOPO_ESTADUAL for p in palavras_antes + palavras_depois)


def _sem_marcador_de_cidade(texto: str, span: Span, valor: str) -> bool:
    """True quando "Rio de Janeiro" é citado sem sinal de que é a cidade."""
    if _normalize(valor) not in _NOME_DUPLICADO_ESTADO_CIDADE:
        return False
    janela = texto[max(0, span[0] - 40):span[1] + 4]
    return not any(_normalize(p) in _MARCADOR_DE_CIDADE for p in janela.split())


def _extrair_municipio(texto: str) -> tuple[Optional[str], Optional[Span]]:
    """Município do snapshot citado na pergunta, e o trecho que o citou.

    ``texto`` vem normalizado, então os nomes do catálogo também são
    normalizados antes de virar regex ("Maricá" casa com "marica"). Casa a
    sequência de palavras inteiras e fica com o mais longo quando um nome é
    parte do outro. Empate entre municípios diferentes não vira filtro.
    """
    catalogo = _catalogo_de_orgaos()
    if not catalogo or not texto:
        return None, None
    achados: list[tuple[str, Span]] = []
    for municipio in catalogo.municipios:
        palavras = [_normalize(p) for p in municipio.split()]
        padrao = r"(?<!\w)" + r"\W+".join(re.escape(p) for p in palavras) + r"(?!\w)"
        encontrado = re.search(padrao, texto)
        if encontrado:
            achados.append((municipio, encontrado.span()))
    if not achados:
        return None, None
    mais_longo = max(len(_tokens_de_nome(m)) for m, _ in achados)
    finalistas = [(m, s) for m, s in achados if len(_tokens_de_nome(m)) == mais_longo]
    if len({m for m, _ in finalistas}) != 1:
        return None, None  # empate: não escolhe o primeiro
    valor, span = finalistas[0]
    if _em_escopo_estadual(texto, span) or _sem_marcador_de_cidade(texto, span, valor):
        # Menção ao Estado ("órgãos estaduais do Rio de Janeiro"), ou nome
        # duplicado Estado/cidade sem marcador: não se recorta pela cidade.
        return None, None
    return valor, _estender_para_tras(texto, span)


# Mantém os nomes públicos dos apelidos conhecidos. Os demais vêm do catálogo
# e exigem contexto de nome; nunca disparam só porque a palavra está no texto.
_APELIDOS_DE_FORNECEDOR = APELIDOS_CONHECIDOS


# Apelido que também é palavra comum: só vale com inicial maiúscula na pergunta
# ("a Light recebeu"), não em "lâmpadas light".
_APELIDOS_SO_COM_MAIUSCULA = {"light": re.compile(r"\b(?:Light|LIGHT)\b")}


# Artigo ou contração logo antes do nome: "da Medshore", "a Cristália recebeu".
_ARTIGOS_ANTES_DE_NOME = frozenset("da do das dos a o as os pela pelo pelas pelos".split())


# Parcela dos objetos que citam a palavra cujo fornecedor também a tem no nome,
# para ela valer como nome de empresa: Cristália 92%; Light 24%, limpeza 4%,
# merenda e ambulância 0% (medido na v7 em 2026-10-19).
_PARCELA_MINIMA_DO_PROPRIO_FORNECEDOR = 0.8


@lru_cache(maxsize=1)
def _palavras_dos_objetos() -> Optional[dict[str, tuple[int, int]]]:
    """Palavra -> (objetos que a citam, quantos deles têm fornecedor com ela no nome).

    ``None`` sem a base. Uma palavra que é assunto de contrato ("merenda",
    "limpeza") não pode virar fornecedor só por coincidir com o nome de uma
    empresa; a marca citada no objeto dos próprios contratos ("medicamentos
    Cristália") pode.
    """
    catalogo = _catalogo_de_orgaos()
    if catalogo and catalogo.palavras_objetos is not None:
        return catalogo.palavras_objetos
    try:
        from src import structured_db

        linhas = structured_db.banco_padrao().consultar(
            "SELECT objeto_normalizado AS o, fornecedor AS f FROM contratacoes")
    except Exception:
        return None
    contagem: dict[str, list[int]] = {}
    for linha in linhas:
        fornecedor = _normalize(linha.get("f") or "")
        for palavra in set(re.findall(r"[a-z]{4,}", linha.get("o") or "")):
            par = contagem.setdefault(palavra, [0, 0])
            par[0] += 1
            par[1] += palavra in fornecedor
    return {palavra: (total, do_fornecedor) for palavra, (total, do_fornecedor) in contagem.items()}


def _nome_curto_de_fornecedor(palavra: str, todas: Sequence[tuple[str, int, int]], comeca: int) -> bool:
    """Uma palavra só, sem "empresa"/"fornecedor" antes, pode nomear o fornecedor?

    Só depois de artigo ("da Medshore"), com 5 letras ou mais, e se nos objetos
    dos contratos ela não aparece ou aparece quase sempre em contratos do próprio
    fornecedor. O fornecedor ainda precisa ser único no catálogo
    (``_valor_do_catalogo``). Sem a base para conferir, não aceita.
    """
    anterior = next((p for p, _, fim in reversed(todas) if fim <= comeca), None)
    if len(palavra) < 5 or anterior not in _ARTIGOS_ANTES_DE_NOME:
        return False
    palavras = _palavras_dos_objetos()
    if palavras is None:
        return False
    total, do_fornecedor = palavras.get(palavra, (0, 0))
    return total == 0 or do_fornecedor / total >= _PARCELA_MINIMA_DO_PROPRIO_FORNECEDOR


def _escrito_como_nome_proprio(citados: Sequence[str], original: Optional[str]) -> bool:
    """Toda palavra informativa do nome começa com maiúscula na pergunta original."""
    if not original:
        return False
    maiusculas = {_normalize(p) for p in re.findall(r"\w+", original) if p[:1].isupper()}
    informativas = [p for p in citados if p not in _PALAVRAS_FUNCIONAIS]
    return bool(informativas) and all(p in maiusculas for p in informativas)


def _parte_de_orgao_citado(citados: Sequence[str], catalogo: "_Catalogo",
                           todas: Sequence[tuple[str, int, int]]) -> bool:
    """As palavras do nome pertencem a um órgão do catálogo citado por inteiro na
    pergunta: "Banco Central do Brasil" é o órgão, não "UNIODONTO DO BRASIL CENTRAL"."""
    na_pergunta = {palavra for palavra, _, _ in todas}
    informativas = {p for p in citados if p not in _PALAVRAS_FUNCIONAIS}
    return any(tokens and set(tokens) <= na_pergunta and informativas <= set(tokens)
               for tokens in catalogo.tokens)


def _extrair_fornecedor(texto: str, bloqueados: Sequence[Span] = (), *,
                        original: Optional[str] = None) -> tuple[Optional[str], Optional[Span]]:
    """Fornecedor do snapshot citado na pergunta, e o trecho que o citou.

    Aceita nome parcial sem as palavras genéricas ("empresa Cristália"). Se
    vários fornecedores casam, não vira filtro. ``bloqueados`` são trechos já
    reconhecidos como outra coisa (o município, por exemplo): sem isso, "do
    Município de Maricá" acharia um fornecedor que também é município.
    """
    catalogo = _catalogo_de_orgaos()
    if texto:
        for achado in re.finditer(r"\w+", texto):
            chave = _normalize(achado.group())
            apelido = _APELIDOS_DE_FORNECEDOR.get(chave)
            exige_maiuscula = _APELIDOS_SO_COM_MAIUSCULA.get(chave)
            if apelido and exige_maiuscula and original is not None and not exige_maiuscula.search(original):
                continue
            if apelido and not any(comeca < achado.end() and achado.start() < termina
                                   for comeca, termina in bloqueados):
                return apelido, achado.span()
    if not catalogo or not texto:
        return None, None

    def _valor(citados: list[str]) -> Optional[str]:
        if catalogo.indice_fornecedores is not None:
            return catalogo.indice_fornecedores.valor(citados)
        return _valor_do_catalogo(catalogo.fornecedores, citados)

    def _livre(inicio: int, fim: int) -> bool:
        return not any(comeca < fim and inicio < termina for comeca, termina in bloqueados)

    todas = [(palavra, achado.start(), achado.end()) for achado in re.finditer(r"\w+", texto)
             if (palavra := _normalize(achado.group()))]
    palavras = [(p, i, f) for p, i, f in todas if _livre(i, f)]
    # Só vira filtro com gatilho antes do nome ("empresa Cristália") ou com
    # nome de duas palavras ou mais ("Cristalia Produtos Quimicos").
    for inicio_palavra, (primeira, comeca, termina) in enumerate(palavras):
        if (primeira in _GENERICAS_DE_FORNECEDOR or primeira in _NAO_SAO_NOME
                or primeira in _VERBOS_COMUNS_DEPOIS_DO_NOME or len(primeira) < 4
                or not primeira[0].isalpha() or primeira in _SIGLAS_DE_ORGAO):
            continue
        anteriores = [p for p, i, f in todas if f <= comeca and p not in _VAZIAS_DE_NOME]
        tem_gatilho = bool(anteriores) and anteriores[-1] in _GATILHOS_DE_FORNECEDOR
        citados = [primeira]
        span: Span = (comeca, termina)
        for palavra, pos_inicio, pos_fim in palavras[inicio_palavra + 1: inicio_palavra + 6]:
            if palavra in _GENERICAS_DE_FORNECEDOR or palavra in _VAZIAS_DE_NOME:
                continue
            if _valor(citados + [palavra]) is None:
                break
            citados.append(palavra)
            span = (span[0], pos_fim)
        if not tem_gatilho:
            palavras_objetos = _palavras_dos_objetos() or {}
            frequencias = [palavras_objetos.get(p, (0, 0)) for p in citados
                           if p not in _PALAVRAS_FUNCIONAIS]
            # Uma primeira palavra comum não basta para descartar um nome composto,
            # e um nome escrito como nome próprio também não: "casa" e "moeda" são
            # assunto de contrato, mas "Contratos da Casa da Moeda" cita a empresa;
            # "contratos de merenda escolar" continua sendo assunto.
            if (frequencias and all(total and proprio / total < _PARCELA_MINIMA_DO_PROPRIO_FORNECEDOR
                                    for total, proprio in frequencias)
                    and not (len(citados) >= 2 and _escrito_como_nome_proprio(citados, original)
                             and not _parte_de_orgao_citado(citados, catalogo, todas))):
                continue
        if len(citados) < 2 and not tem_gatilho and not _nome_curto_de_fornecedor(primeira, todas, comeca):
            continue
        valor = _valor(citados)
        if valor is None and catalogo.apelidos:
            valor = catalogo.apelidos.get(primeira) if len(citados) == 1 else None
        if valor is not None:
            return valor, span
    return None, None


def gerar_catalogo_de_orgaos(destino: Path = ORGAOS_CORPUS_PATH, *, banco: Any = None) -> dict[str, Any]:
    """Grava o catálogo de órgãos, municípios e fornecedores da base SQL.

    As listas antigas (``nomes``/``siglas``) continuam com o mesmo formato; as
    novas (``municipios``/``fornecedores``) são derivadas do snapshot e trazem
    ``versao``/``fonte_sha256`` para o cache de análise ser invalidado quando a
    base mudar. Só valores não vazios entram.
    """
    from datetime import datetime, timezone

    from src import structured_db

    banco = banco if banco is not None else structured_db.garantir_banco()
    nomes: set[str] = set()
    for tabela in ("contratacoes", "pca_planos"):
        for coluna in ("orgao_nome", "unidade_nome"):
            for linha in banco.consultar(f"SELECT DISTINCT {coluna} AS nome FROM {tabela} WHERE {coluna} IS NOT NULL"):
                nome = " ".join(str(linha["nome"]).split())
                if nome:
                    nomes.add(nome)
    siglas: dict[str, str] = {}
    for nome in sorted(nomes):
        if " " not in nome:
            continue  # nome de uma palavra só ("GERAL") não traz sigla
        for achado in _SIGLA_NO_NOME_RE.finditer(nome):
            sigla = _normalize(achado.group(1) or achado.group(2))
            if sigla not in ("rj", "pr") and sigla not in siglas:
                siglas[sigla] = nome
    # Municípios e fornecedores vêm da mesma base, já normalizados de espaço,
    # preservando a caixa original do valor (o SQL compara por igualdade).
    def _distintos(coluna: str) -> list[str]:
        valores = {" ".join(str(linha["v"]).split())
                   for linha in banco.consultar(
                       f"SELECT DISTINCT {coluna} AS v FROM contratacoes WHERE {coluna} IS NOT NULL")}
        return sorted(v for v in valores if v)

    municipios = _distintos("municipio")
    fornecedores = _distintos("fornecedor")
    # Hash do conteúdo que define os valores: muda o snapshot, muda o hash, e
    # quem guarda cache de análise consegue descartar a versão antiga.
    resumo = hashlib.sha256(
        json.dumps({"nomes": sorted(nomes), "municipios": municipios, "fornecedores": fornecedores},
                   ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    identidades = compor_fornecedores(
        banco, _GENERICAS_DE_FORNECEDOR | _NAO_SAO_NOME | _VERBOS_COMUNS_DEPOIS_DO_NOME,
        sorted(nomes), municipios,
    )
    dados = {
        **identidades,
        "fornecedores_sha256": hashlib.sha256(json.dumps(
            identidades, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "nomes de órgão e unidade, municípios e fornecedores da base SQL (src/structured_db.py) do corpus",
        "versao": 3,
        "fonte_sha256": resumo,
        "nomes": sorted(nomes),
        "siglas": dict(sorted(siglas.items())),
        "municipios": municipios,
        "fornecedores": fornecedores,
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _catalogo_de_orgaos.cache_clear()
    _palavras_dos_objetos.cache_clear()
    return dados


def _sem_pista_de_maiuscula(tokens: Sequence[str]) -> bool:
    """Pergunta TODA EM MAIÚSCULAS ou Toda Com Inicial Maiúscula: a maiúscula
    não indica nome próprio. Numa frase normal, "de", "da", "em" vêm em
    minúscula, mesmo com muitos nomes próprios."""
    palavras = [t for t in (tok.strip(_PONTUACAO) for tok in tokens) if t[:1].isalpha()]
    if not palavras:
        return True
    if not any(c.islower() for p in palavras for c in p):
        return True
    funcionais = [p for p in palavras if p.lower() in _PALAVRAS_FUNCIONAIS]
    if funcionais:
        return sum(p[0].isupper() for p in funcionais) / len(funcionais) >= 0.5
    return sum(p[0].isupper() for p in palavras) / len(palavras) >= 0.8


def _nome_a_partir_do_gatilho(tokens: Sequence[str], indice: int, sem_caixa: bool) -> Optional[str]:
    """Nome do órgão que começa no gatilho ("Prefeitura", "Secretaria"...), ou ``None``."""
    return _LeitorDeOrgao(tokens, indice, sem_caixa).ler()


class _LeitorDeOrgao:
    """Lê o nome do órgão palavra por palavra a partir do gatilho.

    O nome cresce enquanto continua correspondendo a um órgão do corpus ou é
    nome próprio; município do RJ entra inteiro; pontuação, palavra de fim ou
    conectora sem nome depois encerram. ``ler`` decide no fim se o que foi lido
    é um órgão.
    """

    def __init__(self, tokens: Sequence[str], indice: int, sem_caixa: bool) -> None:
        self.tokens, self.indice, self.sem_caixa = tokens, indice, sem_caixa
        self.gatilho = _normalize(self.limpa(indice))
        self.palavras = [self.limpa(indice)]
        self.chaves = [_SINONIMOS_DE_ORGAO.get(self.gatilho, self.gatilho)]
        self.cursor = indice + 1
        self._ler_qualificadores()
        self.inicio_do_nome = len(self.chaves)
        self.com_maiuscula: list[bool] = []
        self.parou_em_palavra_colada = False
        self.antes_do_municipio: Optional[list[str]] = None
        self.cidade: Optional[list[str]] = None

    def limpa(self, i: int) -> str:
        return self.tokens[i].strip(_PONTUACAO) if i < len(self.tokens) else ""

    def nome_proprio(self, palavra: str) -> bool:
        # Inicial maiúscula numa frase normal; SIGLA toda em maiúscula não conta.
        return not self.sem_caixa and palavra[:1].isupper() and not (len(palavra) > 1 and palavra.isupper())

    def confere(self, nome: Sequence[str]) -> Optional[str]:
        """O nome existe no corpus? Para prefeitura, bastam as palavras da
        cidade num órgão municipal ("Câmara Municipal de São Gonçalo"); para
        cabeças fortes, as palavras do nome; para as demais ("câmara",
        "fundo", "comando"), a cabeça também tem de estar no nome."""
        if self.gatilho in ("prefeitura", "municipio"):
            return _orgao_do_catalogo(nome, exigir_algum=("municipio", "municipal", "prefeitura"))
        if self.gatilho in _GATILHOS_FORTES:
            return _orgao_do_catalogo(nome)
        return _orgao_do_catalogo(list(self.chaves[:self.inicio_do_nome]) + list(nome))

    def _ler_qualificadores(self) -> None:
        while self.cursor < len(self.tokens) and _normalize(self.limpa(self.cursor)) in _QUALIFICADORES_DE_ORGAO:
            self.palavras.append(self.limpa(self.cursor))
            self.chaves.append(_normalize(self.limpa(self.cursor)))
            self.cursor += 1

    def ler(self) -> Optional[str]:
        while self.cursor < len(self.tokens):
            if not self._avancar():
                break
        return self._veredito()

    def _avancar(self) -> bool:
        """Lê a próxima palavra (ou grupo) do nome; ``False`` quando o nome acabou."""
        tokens, cursor = self.tokens, self.cursor
        if tokens[cursor - 1][-1:] in _PONTUACAO or tokens[cursor][:1] in "(\"'“‘":
            return False  # vírgula, ponto, aspas ou "(SES)" encerram o nome
        palavra = self.limpa(cursor)
        normal = _normalize(palavra)
        if not palavra or normal in _FIM_DO_NOME_DO_ORGAO:
            return False
        if normal in ("de", "do", "da") and (tamanho := _municipio_em(tokens, cursor + 1)):
            self._ler_municipio(palavra, tamanho)
            return True
        passo_lido = self._proximo_passo(palavra, normal)
        if passo_lido is None:
            return False
        novas_palavras, novas_chaves, passo = passo_lido
        fim = cursor + passo
        if novas_chaves[-1] == "rio" and [_normalize(self.limpa(fim)), _normalize(self.limpa(fim + 1))] == ["de", "janeiro"]:
            novas_palavras += [self.limpa(fim), self.limpa(fim + 1)]  # "Rio de Janeiro" é um nome só
            novas_chaves.append("janeiro")
            fim += 2
        # Cresce se ainda corresponde a um órgão do corpus, ou se é nome próprio.
        if not (self.confere(self.chaves[self.inicio_do_nome:] + novas_chaves)
                or self.nome_proprio(novas_palavras[-1])):
            self.parou_em_palavra_colada = passo == 1
            return False
        self.palavras += novas_palavras
        self.chaves += novas_chaves
        self.com_maiuscula.append(self.nome_proprio(novas_palavras[-1]))
        self.cursor = fim
        return True

    def _ler_municipio(self, palavra: str, tamanho: int) -> None:
        """Município do RJ: entra inteiro e confirmado ("de são gonçalo", "de maricá")."""
        cursor = self.cursor
        nome_da_cidade = [self.limpa(i) for i in range(cursor + 1, cursor + 1 + tamanho)]
        if self.antes_do_municipio is None:
            self.antes_do_municipio = list(self.chaves[self.inicio_do_nome:])
            self.cidade = nome_da_cidade
        self.palavras += [palavra] + nome_da_cidade
        self.chaves += [_normalize(p) for p in nome_da_cidade]
        self.com_maiuscula.append(True)
        self.cursor += 1 + tamanho

    def _proximo_passo(self, palavra: str, normal: str) -> Optional[tuple[list[str], list[str], int]]:
        """Palavras e chaves candidatas e quantos tokens elas ocupam; ``None`` encerra."""
        if normal in _PALAVRA_CONECTORA:
            seguinte = self.limpa(self.cursor + 1)
            normal_seguinte = _normalize(seguinte)
            if (
                not seguinte
                or not seguinte[0].isalpha()
                or normal_seguinte in _NAO_SAO_NOME
                or normal_seguinte in _PALAVRA_CONECTORA
                or (normal == "e" and normal_seguinte in _GATILHOS_ORGAO)
            ):
                return None  # "de 2025", "de qual", "e da Prefeitura", "e o valor"
            return [palavra, seguinte], [normal_seguinte], 2
        if (
            palavra[:1].isalpha()
            and normal not in _GATILHOS_ORGAO
            and normal not in _NAO_SAO_NOME
            and len(self.chaves) > self.inicio_do_nome or self.nome_proprio(palavra)
        ) and normal not in _GATILHOS_ORGAO:
            # Palavra colada, sem conectora: "Prefeitura Niterói", "são gonçalo".
            return [palavra], [normal], 1
        return None

    def _veredito(self) -> Optional[str]:
        """O que foi lido é um órgão? Devolve o nome, ou ``None``."""
        gatilho, chaves, inicio_do_nome = self.gatilho, self.chaves, self.inicio_do_nome
        if gatilho in ("prefeitura", "municipio") and self.antes_do_municipio is None and chaves[inicio_do_nome:] == ["rio"]:
            return _orgao_do_municipio(["Rio", "de", "Janeiro"])  # "Prefeitura do Rio"
        if self.antes_do_municipio is not None:
            return self._veredito_com_municipio()
        if self.parou_em_palavra_colada and chaves[-1] in _COMECOS_DE_NOME_COMPOSTO:
            return None  # "prefeitura de são" + palavra que não deu para confirmar: nome pela metade
        if chaves[-1] in _CONTINUA_SEM_CATALOGO and _normalize(self.limpa(self.cursor)) in ("de", "do", "da"):
            # "secretaria de estado" + "de educação" que não está no corpus: nome incompleto.
            return _nome_sem_catalogo(self.tokens, self.indice) if gatilho in _GATILHOS_FORTES else None
        if len(chaves) > inicio_do_nome and (self.confere(chaves[inicio_do_nome:]) or all(self.com_maiuscula)):
            return " ".join(self.palavras)
        if (len(chaves) == inicio_do_nome > 1 and gatilho in _GATILHOS_FORTES
                and all(self.nome_proprio(p) for p in self.palavras[1:])):
            return " ".join(self.palavras)  # "Defensoria Pública", "Procuradoria Geral"
        if gatilho in _GATILHOS_FORTES:
            return _nome_sem_catalogo(self.tokens, self.indice)
        return None  # "órgão federal", ou nome em minúsculas que não existe no corpus: "banco de sangue"

    def _veredito_com_municipio(self) -> Optional[str]:
        gatilho, antes = self.gatilho, self.antes_do_municipio
        # "Prefeitura (Municipal) de X", "Prefeitura da Cidade do X", "Município
        # de X": o órgão é gravado como "MUNICIPIO DE X", e o filtro "contém"
        # não casaria "Prefeitura de Maricá" com ele.
        if gatilho in ("prefeitura", "municipio") and antes in ([], ["cidade"]) and self.cidade:
            return _orgao_do_municipio(self.cidade)
        # Com município: vale para prefeitura/câmara/cabeça forte, ou se o que
        # vem antes da cidade existe no corpus ("fundo municipal de saúde").
        # "banco de sangue de niterói" não.
        if (
            gatilho in ("prefeitura", "municipio", "camara")
            or gatilho in _GATILHOS_FORTES
            or not antes
            or self.confere(antes)
            or all(self.com_maiuscula)
        ):
            return " ".join(self.palavras)
        return None


# Os 92 municípios do RJ: depois de "de/do/da", entram inteiros no nome do
# órgão, com ou sem maiúscula ("fundo municipal de saúde de maricá",
# "prefeitura de são gonçalo"), mesmo que o órgão não esteja no corpus.
_MUNICIPIOS_RJ = tuple(
    sorted(
        (
            tuple(_normalize(nome).split())
            for nome in """
            Angra dos Reis|Aperibé|Araruama|Areal|Armação dos Búzios|Arraial do Cabo|Barra do Piraí|Barra Mansa
            |Belford Roxo|Bom Jardim|Bom Jesus do Itabapoana|Cabo Frio|Cachoeiras de Macacu|Cambuci
            |Campos dos Goytacazes|Cantagalo|Carapebus|Cardoso Moreira|Carmo|Casimiro de Abreu
            |Comendador Levy Gasparian|Conceição de Macabu|Cordeiro|Duas Barras|Duque de Caxias
            |Engenheiro Paulo de Frontin|Guapimirim|Iguaba Grande|Itaboraí|Itaguaí|Italva|Itaocara|Itaperuna
            |Itatiaia|Japeri|Laje do Muriaé|Macaé|Macuco|Magé|Mangaratiba|Maricá|Mendes|Mesquita|Miguel Pereira
            |Miracema|Natividade|Nilópolis|Niterói|Nova Friburgo|Nova Iguaçu|Paracambi|Paraíba do Sul|Paraty
            |Paty do Alferes|Petrópolis|Pinheiral|Piraí|Porciúncula|Porto Real|Quatis|Queimados|Quissamã|Resende
            |Rio Bonito|Rio Claro|Rio das Flores|Rio das Ostras|Rio de Janeiro|Santa Maria Madalena
            |Santo Antônio de Pádua|São Fidélis|São Francisco de Itabapoana|São Gonçalo|São João da Barra
            |São João de Meriti|São José de Ubá|São José do Vale do Rio Preto|São Pedro da Aldeia
            |São Sebastião do Alto|Sapucaia|Saquarema|Seropédica|Silva Jardim|Sumidouro|Tanguá|Teresópolis
            |Trajano de Moraes|Três Rios|Valença|Varre-Sai|Vassouras|Volta Redonda
            """.replace("\n", "").split("|")
            if nome.strip()
        ),
        key=len,
        reverse=True,
    )
)


def _municipio_em(tokens: Sequence[str], inicio: int) -> int:
    """Quantos tokens a partir de ``inicio`` formam um município do RJ (0 se nenhum)."""
    for municipio in _MUNICIPIOS_RJ:
        trecho = [_normalize(t.strip(_PONTUACAO)) for t in tokens[inicio: inicio + len(municipio)]]
        if tuple(trecho) == municipio:
            return len(municipio)
    return 0


# Primeira palavra de nome composto de cidade: parar nela corta o nome ao meio.
_COMECOS_DE_NOME_COMPOSTO = frozenset(
    "sao santa santo nova novo barra volta tres duque angra campos cabo bom porto paty "
    "engenheiro casimiro armacao silva sapucaia miguel conceicao".split()
)


# Cabeças de nome que quase nunca são substantivo comum: sem catálogo, o nome
# ainda é lido, mas só um nível ("procuradoria geral do estado").
_GATILHOS_FORTES = frozenset(
    "secretaria ministerio tribunal defensoria procuradoria controladoria policia assembleia companhia autarquia".split()
)


_CONTINUA_SEM_CATALOGO = frozenset({"estado", "governo"})


def _nome_sem_catalogo(tokens: Sequence[str], indice: int) -> Optional[str]:
    def limpa(i: int) -> str:
        return tokens[i].strip(_PONTUACAO) if i < len(tokens) else ""

    palavras = [limpa(indice)]
    cursor = indice + 1
    while cursor < len(tokens) and _normalize(limpa(cursor)) in _QUALIFICADORES_DE_ORGAO:
        palavras.append(limpa(cursor))
        cursor += 1
    niveis = 0
    while cursor + 1 < len(tokens) and niveis < 2:
        if tokens[cursor - 1][-1:] in _PONTUACAO or tokens[cursor][:1] in "(\"'“‘":
            break
        conectora, palavra = _normalize(limpa(cursor)), limpa(cursor + 1)
        normal = _normalize(palavra)
        if (
            conectora not in ("de", "do", "da", "dos", "das")
            or not palavra[:1].isalpha()
            or normal in _NAO_SAO_NOME
            or normal in _PALAVRA_CONECTORA
            or normal in _GATILHOS_ORGAO
        ):
            break
        palavras += [limpa(cursor), palavra]
        cursor += 2
        if normal == "rio" and [_normalize(limpa(cursor)), _normalize(limpa(cursor + 1))] == ["de", "janeiro"]:
            palavras += [limpa(cursor), limpa(cursor + 1)]
            cursor += 2
        niveis += 1
        if _normalize(limpa(cursor)) in ("de", "do", "da") and (tamanho := _municipio_em(tokens, cursor + 1)):
            # "secretaria municipal de saúde de niterói": o município entra.
            palavras += [limpa(i) for i in range(cursor, cursor + 1 + tamanho)]
            break
        if normal not in _CONTINUA_SEM_CATALOGO and normal != "rio":
            # "secretaria de ciência e tecnologia": um "e" + palavra ainda entra.
            seguinte = _normalize(limpa(cursor + 1))
            if (
                _normalize(limpa(cursor)) == "e"
                and limpa(cursor + 1)[:1].isalpha()
                and tokens[cursor - 1][-1:] not in _PONTUACAO
                and seguinte not in _NAO_SAO_NOME
                and seguinte not in _PALAVRA_CONECTORA
                and seguinte not in _GATILHOS_ORGAO
                and seguinte not in _VERBOS_COMUNS_DEPOIS_DO_NOME
            ):
                palavras += [limpa(cursor), limpa(cursor + 1)]
            break  # "secretaria de saúde", não "secretaria de saúde de ambulâncias"
    return " ".join(palavras) if len(palavras) > 1 and niveis else None


# Depois de "nome e", palavras que começam outra oração: "secretaria de saúde e
# informe", "e compare", "e liste".
_VERBOS_COMUNS_DEPOIS_DO_NOME = frozenset(
    "informe informar compare comparar liste listar mostre mostrar diga traga some calcule qual quais quanto".split()
)


def _sigla_de_orgao(tokens: Sequence[str]) -> tuple[Optional[str], Optional[str]]:
    """(nome do órgão, sigla como está no texto normalizado) para SEEDUC, TCE-RJ, FESAUDE..."""
    catalogo = _catalogo_de_orgaos()
    for bruto in tokens:
        token = bruto.strip(_PONTUACAO)
        for parte in re.split(r"[-/]", token):
            normal = _normalize(parte)
            if normal in _SIGLAS_DE_ORGAO:
                return _SIGLAS_DE_ORGAO[normal], _normalize(token)
            if catalogo and parte.isupper() and len(parte) >= 2 and normal in catalogo.siglas:
                return catalogo.siglas[normal], _normalize(token)
    return None, None


def _extrair_orgao(query_original: str) -> tuple[Optional[str], Optional[str]]:
    """Nome do órgão citado na pergunta, e a sigla usada (ou None).

    Começa numa palavra de início de nome ("secretaria", "prefeitura",
    "fundo"...), com ou sem maiúscula, e segue por adjetivos ("municipal") e
    por "de/do/da/dos/das/e" + nome, enquanto o nome corresponde a um órgão do
    corpus ou é nome próprio. Sem gatilho, aceita sigla conhecida (SEEDUC,
    SES, DPE...) ou que apareça no nome de um órgão do corpus (FESAUDE).
    """
    tokens = query_original.split()
    sem_caixa = _sem_pista_de_maiuscula(tokens)
    for indice, bruto in enumerate(tokens):
        gatilho = _normalize(bruto.strip(_PONTUACAO))
        if gatilho not in _GATILHOS_ORGAO:
            continue
        anterior = _normalize(tokens[indice - 1].strip(_PONTUACAO)) if indice else ""
        if gatilho in {"municipio", "cidade"} and anterior in {"no", "na", "em", "nos", "nas"}:
            # "no município do Rio" é dimensão territorial; não o órgão
            # MUNICIPIO DE RIO DE JANEIRO.
            continue
        depois = [_normalize(t.strip(_PONTUACAO)) for t in tokens[indice + 1: indice + 3]]
        if gatilho in _GATILHO_EXIGE and (not depois or depois[0] not in _GATILHO_EXIGE[gatilho]):
            continue
        if len(depois) == 2 and depois[0] in _PALAVRA_CONECTORA and (gatilho, depois[1]) in _NAO_E_ORGAO:
            continue
        nome = _nome_a_partir_do_gatilho(tokens, indice, sem_caixa)
        if nome:
            return nome, None
    nome, sigla = _sigla_de_orgao(tokens)
    if nome:
        return nome, sigla
    # Fallback: palavras de gatilho não formaram nome completo, mas o corpus
    # pode ter o órgão. Ex.: "BANCO CENTRAL DO BRASIL" — "central" não era
    # qualificador, então o nome parou em "banco". Tenta com os tokens a
    # partir do gatilho.
    for indice, bruto in enumerate(tokens):
        gatilho = _normalize(bruto.strip(_PONTUACAO))
        if gatilho not in _GATILHOS_ORGAO:
            continue
        palavras = [_normalize(t.strip(_PONTUACAO)) for t in tokens[indice:] if _normalize(t.strip(_PONTUACAO)) not in _NAO_SAO_NOME]
        nome = _orgao_do_catalogo(palavras)
        if nome:
            return nome, None
    return None, None


# CNPJ com ou sem pontuação. Os 14 dígitos no começo de um número de controle
# do PNCP ("00394429000100-2-001108/2024") não são CNPJ citado: ficam de fora
# pelo "-" depois deles.
_CNPJ_RE = re.compile(
    r"(?:\bcnpjs?\s*(?:n[o.]?\s*|:\s*)?)?"
    r"(?<![\d./-])(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?![\d/-])"
)


def _extrair_cnpjs(texto: str) -> tuple[list[str], list[Span]]:
    """CNPJs citados (só dígitos, sem repetição) e os trechos onde aparecem."""
    cnpjs, trechos = [], []
    for match in _CNPJ_RE.finditer(texto):
        digitos = "".join(match.groups())
        if digitos not in cnpjs:
            cnpjs.append(digitos)
        trechos.append(match.span())
    return cnpjs, trechos
