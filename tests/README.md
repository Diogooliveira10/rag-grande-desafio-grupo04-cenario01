# Testes

A pasta reúne a suíte ativa, os dados de apoio e os relatórios de validação do projeto. Todo o material está centralizado em `tests/`.

## Organização

- `test_*.py`: testes ativos descobertos pelo pytest.
- `run_corpus_validation.py`: executa a suíte, verifica o manifesto e registra evidências da arquitetura.
- `fixtures/`: documentos locais usados nos testes, incluindo amostras JSON, PDF e formatos tabulares.
- `reports/`: relatórios gerados pelo runner; os relatórios com data são ignorados pelo Git.
- `LEGADO_TESTES.md`: fontes antigas preservadas para consulta. O conteúdo não é executado pelo pytest.

## Executar

Na raiz do repositório:

```powershell
py -m pytest tests -m "not llm"
```

Para gerar o relatório da validação:

```powershell
py tests/run_corpus_validation.py
```

Os testes de contrato usam dados locais e não coletam o corpus real. A coleta continua sendo uma operação explícita da CLI de `src.fetch_corpus`.