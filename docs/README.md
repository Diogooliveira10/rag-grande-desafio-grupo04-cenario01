# Documentação

Este diretório reúne guias de uso e referências do projeto.

- [Guia de comandos da CLI](CLI.md): instalação, coleta, inventário, curadoria,
  preparação, busca local e embeddings.
- [Emails sintéticos em Markdown](#emails-sintéticos-em-markdown): fixture de
  exemplo e fluxo para preparar uma base local.
- [Arquitetura atual e alvo](../ARQUITETURA.md): o que já está implementado e o
  que ainda é parte do desenho do Adaptive RAG.
- [Inventário do corpus](../CORPUS_INVENTORY.md): contagens da v6 oficial.
- [Registro da v6](registro_execucao_v6.md): distribuição, embeddings, avaliação
  e limitações das respostas.
- [Personas e perguntas de referência](../PERSONAS.md).
- [Enunciado completo do Grande Desafio](../GRANDE_DESAFIO_CENARIOS.md).

## Emails sintéticos em Markdown

O arquivo [email_sintetico_exemplo.md](email_sintetico_exemplo.md) demonstra um
email fictício com cabeçalho, corpo e mensagem citada. Use endereços reservados
como `example.com` e deixe claro que o conteúdo é sintético.

### Preparar uma pasta sem criar versão do corpus

Coloque um arquivo `.md` por mensagem ou por conversa em uma pasta. O
`prepare_documents` percorre a pasta recursivamente; se houver outros formatos
suportados nela, eles também serão preparados. Para conferir sem gravar JSONL,
relatório ou estado incremental, use `--dry-run`:

```powershell
py -m src.prepare_documents `
  --input "docs" `
  --manifest corpus_manifest.yaml `
  --base-id emails_sinteticos `
  --output "data/normalized/emails-preview.jsonl" `
  --report "data/normalized/emails-preview-report.json" `
  --dry-run
```

Para preparar só os emails, aponte `--input` para uma pasta que contenha apenas
os arquivos daquela base. Remova `--dry-run` quando quiser gravar o JSONL, o
relatório e o estado incremental. Essa etapa não cria uma versão de coleta.

### Registrar e coletar uma base local versionada

A base `emails_sinteticos` já está declarada em `corpus_manifest.yaml` e aponta
para o arquivo de exemplo deste diretório. Para trocar por uma coleção, altere
`acesso.origem` para o caminho da pasta que contém os `.md`. Então execute a
coleta experimental:

```powershell
py -m src.fetch_corpus fetch `
  --manifest corpus_manifest.yaml `
  --data-root data/emails-sinteticos `
  --base-id emails_sinteticos
```

Essa coleta cria uma versão e um inventário sob `data/emails-sinteticos/`.
Depois, prepare os documentos a partir do inventário mais recente dessa raiz:

```powershell
py -m src.prepare_documents `
  --latest `
  --data-root "data/emails-sinteticos" `
  --manifest corpus_manifest.yaml `
  --base-id emails_sinteticos
```

A saída fica em `data/emails-sinteticos/normalized/vN/emails_sinteticos/`,
com a versão e o inventário identificados no relatório.

`fetch` é a etapa que versiona e inventaria os arquivos. Use-a quando quiser
incorporar a coleção ao histórico do corpus; para uma prévia local, basta o
primeiro comando com `--dry-run`.
