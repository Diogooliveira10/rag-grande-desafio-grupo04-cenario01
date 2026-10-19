# Documentação

Este diretório reúne guias de uso e referências do projeto.

- [Plano de correção SQL/pipeline — alvo V7](PLANO_CORRECAO_SQL_PIPELINE_V7.md):
  etapas, dependências, critérios de aceite, status de aplicação e gates de promoção.
- [Reparo incremental V7](REPARO_V7.md): candidato separado, vetores recuperados,
  herança, SQLite compatível, testes curtos e lacunas ainda abertas; sem promoção.
- [Plano do corpus v7](PLANO_POSSIVEL_CORPUS_V7.md): censo dos contratos e
  publicações de 2025 em construção paralela (modo direto, projeção
  `completa`), com DIFF, regressão e re-auditoria do golden set como etapas
  de promoção; a v6 permanece a base ativa.
- [Coleta das bases e recuperação do v7](COLETA_DAS_BASES_E_RECUPERACAO_V7.md):
  fontes, limitações das APIs, recortes, instrumentos, modalidade vinculada,
  staging e bloqueios de promoção.
- [Relatório parcial da recuperação v7](RELATORIO_RECUPERACAO_V7_2026-10-15.md):
  baseline, reclassificação, pilotos, testes e pendências; sem promoção.
- [Cadastro oficial e reconciliação territorial](RELATORIO_CADASTRO_V7_2026-10-16.md):
  continuação em lotes, acompanhamento em segundo plano e janelas propostas.
- [Execução v7 — consolidação atualizada](RELATORIO_EXECUCAO_V7_2026-10-16.md):
  candidato_04, complementação de contratos, atas com território oficial,
  1.731 testes, pendências e bloqueios de promoção.
- [Guia de comandos da CLI](CLI.md): instalação, coleta, inventário, curadoria,
  preparação, busca local e embeddings.
- [Emails sintéticos em Markdown](#emails-sintéticos-em-markdown): fixture de
  exemplo e fluxo para preparar uma base local.
- [Arquitetura atual e alvo](../ARQUITETURA.md): o que já está implementado e o
  que ainda é parte do desenho do Adaptive RAG.
- [Observabilidade das consultas](OBSERVABILIDADE.md): funcionamento do SQL
  direto (C), auditoria completa opcional (D), fila e índice de traces (E),
  configuração, exemplos e limites.
- [Inventário do corpus](../CORPUS_INVENTORY.md): contagens da v6, a base
  ativa; o censo v7 tem inventário próprio em
  `data/v7_direto/inventario_direto.json`.
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
