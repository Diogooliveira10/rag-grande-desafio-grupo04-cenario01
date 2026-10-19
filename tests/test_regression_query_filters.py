from pathlib import Path

import pytest

from eval.ablacao_rerank import CasoRecuperacao
from eval import medir_regressao as regression
from src import query_analysis
from src.retrieve import RetrievalResult
from src.schema import QueryAnalysisResult, QueryFilters


def test_regras_extraem_mesmo_recorte_uma_vez_e_passam_aos_dois_snapshots(monkeypatch):
    calls = []
    analyzed = []
    def analyze(query, *, modo):
        analyzed.append((query, modo))
        return QueryAnalysisResult(query_original=query, filtros=QueryFilters(orgao="Secretaria de Cultura"))
    def retrieve(query, route, **kw):
        calls.append(kw["filters"])
        return [RetrievalResult(chunk_id="c", content="compra", metadata={}, score=1)]
    monkeypatch.setattr(query_analysis, "analisar_query", analyze)
    monkeypatch.setattr(regression, "retrieve", retrieve)
    case = CasoRecuperacao(id="synthetic", pergunta="Uma compra?", bases=["estruturada"], relevante_contem=["compra"])
    a = regression.Alvo("a", Path("a.jsonl"), Path("a"))
    b = regression.Alvo("b", Path("b.jsonl"), Path("b"))
    report = regression.medir_regressao([case], referencia=a, novo=b, strategy="hybrid", query_filters=True)
    assert analyzed == [("Uma compra?", "regras")]
    assert calls == [{"orgao": {"operator": "contains", "value": "Secretaria de Cultura", "optional": False}}] * 2
    assert report["configuracao"]["query_analysis"] == "regras"
    assert report["configuracao"]["filtros_por_caso"]["synthetic"] == calls[0]


def test_ablation_densa_nao_ganha_filtros_silenciosamente():
    a = regression.Alvo("a", Path("a.jsonl"), Path("a"))
    with pytest.raises(ValueError, match="ablação densa"):
        regression.medir_regressao([], referencia=a, novo=a, query_filters=True)
