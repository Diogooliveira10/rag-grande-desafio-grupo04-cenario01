# Relatórios de validação

Execute a partir da raiz do repositório:

```powershell
py tests/run_corpus_validation.py
```

O comando cria um relatório Markdown com horário no nome. Ele registra o resultado da suíte pytest, a compilação dos módulos, as verificações do manifesto oficial e as evidências dos caminhos arquiteturais conferidos pelo runner.

A validação é offline: não baixa o corpus real nem grava em `data/raw/`. Os relatórios gerados são ignorados pelo Git; este guia permanece versionado.