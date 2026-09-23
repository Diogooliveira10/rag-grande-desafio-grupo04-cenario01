# Relatórios de validação

Execute:

```powershell
py tests/run_corpus_validation.py
```

O comando gera `corpus-validation-AAAAMMDD-HHMMSS±HHMM.md` nesta pasta, com:

- data e hora de início e fim;
- resultado da suíte `pytest`;
- compilação dos módulos;
- smoke check das quatro bases do manifesto;
- evidências dos caminhos arquiteturais ainda em migração.

O runner é offline: ele não baixa o corpus real nem escreve em `data/raw`.
