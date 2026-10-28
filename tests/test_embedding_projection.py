'''Testes da projeção compacta dos embeddings (§4/§7 do plano v7).'''

from __future__ import annotations

import json
from pathlib import Path

from src.direct_build import RegistroDireto, construir_direto
from src.embedding_projection import embed_fn_concorrente, embed_fn_projetado, texto_projecao

REGISTRO = {
    "numeroControlePNCP": "11111111000001-2-000001/2025",
    "objetoContrato": "Contratação de   manutenção de computadores",
    "descricaoResumida": "Manutenção preventiva",
    "titulo": "Chamada 01",
    "dataAssinatura": "2025-03-10",
    "valorGlobal": 1000.0,
    "informacaoComplementar": "processo administrativo diligencia parecer",
}


def test_texto_projecao_extrai_so_descricao():
    texto = json.dumps(REGISTRO, ensure_ascii=False)
    projetado = texto_projecao(texto, "compacta")
    assert "manutenção de computadores" in projetado
    assert "Manutenção preventiva" in projetado
    for fora in ("numeroControlePNCP", "11111111000001", "valorGlobal", "1000",
                 "diligencia", "2025-03-10"):
        assert fora not in projetado, fora


def test_texto_projecao_e_deterministico_e_normaliza_espacos():
    texto = json.dumps(REGISTRO, ensure_ascii=False)
    projetado = texto_projecao(texto, "compacta")
    assert projetado == (
        "Contratação de manutenção de computadores | Manutenção preventiva | Chamada 01"
    )
    assert texto_projecao(texto, "compacta") == projetado


def test_texto_projecao_sem_descricao_cai_para_o_texto_completo():
    texto = json.dumps({"numeroControlePNCP": "x", "valorGlobal": 1.0}, ensure_ascii=False)
    assert texto_projecao(texto, "compacta") == texto


def test_texto_projecao_nao_json_passa_inteiro():
    assert texto_projecao("texto corrido qualquer", "compacta") == "texto corrido qualquer"


def test_modo_completa_e_o_padrao():
    texto = json.dumps(REGISTRO, ensure_ascii=False)
    assert texto_projecao(texto) == texto


def test_embed_fn_projetado_envia_textos_projetados():
    vistos: list[list[str]] = []

    def embed(texts):
        vistos.append(list(texts))
        return [[1.0] for _ in texts]

    embed.encoding_config = {"tipo": "teste"}
    compacto = embed_fn_projetado(embed, "compacta")
    compacto([json.dumps(REGISTRO, ensure_ascii=False)])
    assert vistos[-1] == [
        "Contratação de manutenção de computadores | Manutenção preventiva | Chamada 01"]
    assert compacto.encoding_config == {"tipo": "teste"}

    completo = embed_fn_projetado(embed, "completa")
    assert completo is embed
    completo(["bruto"])
    assert vistos[-1] == ["bruto"]


def test_modo_desconhecido_rejeitado():
    try:
        embed_fn_projetado(lambda texts: [], "outra")
    except ValueError:
        pass
    else:
        raise AssertionError("modo desconhecido deveria falhar")


def test_construir_direto_com_projecao_compacta_marca_artefatos(tmp_path):
    vistos: list[list[str]] = []

    def embed(texts):
        vistos.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    base = {
        "base_id": "contratos_estruturado", "natureza": "estruturada",
        "acesso": {"tipo": "provider"}, "sensitivity_default": "publico",
        "chunking_sugerido": "1 contrato = 1 chunk",
        "metadados_extras": ["orgao", "modalidade", "valor", "municipio", "ano"],
    }
    relatorio = construir_direto(
        [RegistroDireto(payload=REGISTRO, origem="https://exemplo.teste/1")],
        base, tmp_path / "v7", embed_fn=embed, modelo="modelo-de-teste",
        projecao="compacta",
    )
    # O encoder viu o texto projetado, nunca o JSON inteiro.
    assert vistos == [["Contratação de manutenção de computadores | Manutenção preventiva | Chamada 01"]]
    # A projeção fica registrada no inventário e no manifest do índice.
    inventario = json.loads(Path(relatorio["inventario"]).read_text(encoding="utf-8"))
    assert inventario["meta"]["projecao_embedding"] == "compacta"
    manifest_indice = json.loads(
        (Path(relatorio["destino"]) / "index" / "contratos_estruturado" / "manifest.json")
        .read_text(encoding="utf-8")
    )
    assert manifest_indice["projecao_embedding"] == "compacta"
    # O chunk armazenado continua COMPLETO (BM25/evidência não perdem nada).
    linha = json.loads(Path(relatorio["documents"]).read_text(encoding="utf-8").splitlines()[0])
    assert "informacaoComplementar" in linha["text"]


def test_texto_projecao_enriquecida_traz_contexto_sem_identificador_nem_valor():
    registro = dict(REGISTRO)
    registro["orgaoEntidade"] = {"cnpj": "11111111000001", "razaoSocial": "PREFEITURA DE EXEMPLO", "esferaId": "M"}
    registro["unidadeOrgao"] = {"municipioNome": "Exemplo", "ufSigla": "RJ"}
    registro["modalidadeNome"] = "Pregão - Eletrônico"
    registro["nomeRazaoSocialFornecedor"] = "FORNECEDORA EXEMPLO LTDA"
    registro["categoriaProcesso"] = "Serviços"
    texto = json.dumps(registro, ensure_ascii=False)
    projetado = texto_projecao(texto, "enriquecida")
    for dentro in ("manutenção de computadores", "PREFEITURA DE EXEMPLO", "Exemplo",
                   "RJ", "Pregão - Eletrônico", "FORNECEDORA EXEMPLO LTDA", "Serviços"):
        assert dentro in projetado, dentro
    for fora in ("11111111000001", "numeroControlePNCP", "valorGlobal", "1000",
                 "2025-03-10", "diligencia"):
        assert fora not in projetado, fora


def test_texto_projecao_enriquecida_deduplica_valores_repetidos():
    registro = {
        "objetoContrato": "Manutenção de computadores",
        "nomeOrgao": "ORGAO X",
        "orgaoEntidade": {"razaoSocial": "ORGAO X"},
        "unidadeOrgao": {"nomeUnidade": "ORGAO X"},
    }
    projetado = texto_projecao(json.dumps(registro, ensure_ascii=False), "enriquecida")
    assert projetado.count("ORGAO X") == 1


def test_embed_fn_concorrente_preserva_a_ordem():
    import time

    def embed(texts):
        for texto in texts:  # força intercalação real entre as threads
            if len(texto) % 2 == 0:
                time.sleep(0.002)
        return [[float(len(t))] for t in texts]

    concorrente = embed_fn_concorrente(embed, 4)
    textos = [f"{'x' * (i % 7 + 1)}-{i}" for i in range(23)]
    saida = concorrente(textos)
    assert saida == [[float(len(t))] for t in textos]  # ordem idêntica à entrada


def test_embed_fn_concorrente_sem_paralelismo_e_o_mesmo_fn():
    def embed(texts):
        return [[1.0] for _ in texts]

    assert embed_fn_concorrente(embed, 1) is embed
