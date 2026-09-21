# Fixtures reais de formatos

Estas amostras foram extraídas de dados públicos já coletados nas tentativas do
corpus e mantêm o conteúdo de registros reais, sem valores inventados.

| Arquivo | Base de origem | Formato |
| --- | --- | --- |
| `editais_normativo_real.html` | `editais_normativo` | HTML |
| `contratos_estruturado_real.json` | `contratos_estruturado` | JSON |
| `respostas_api_real.jsonl` | `respostas_api_jsonl` | JSONL |
| `licitacoes_siga_real.csv` | `licitacoes_siga_csv` | CSV |

A base está declarada em `corpus_manifest.yaml` com a URL oficial, tipo de
acesso `http_csv`, licença PDDL e os nomes das colunas publicados. O coletor
usa essa declaração para reconstruir o arquivo completo quando necessário.

Os PDFs reais permanecem em `tests/fixtures/pdf/`. O CSV é uma amostra literal
das primeiras 50 linhas do arquivo público da SEPLAG-RJ, preservando o cabeçalho
e os valores publicados; ele é parcial apenas para manter o teste rápido.
Markdown e TXT continuam cobertos por testes unitários do parser.
