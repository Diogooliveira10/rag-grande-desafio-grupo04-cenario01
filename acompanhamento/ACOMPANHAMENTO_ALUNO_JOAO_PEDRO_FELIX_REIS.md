# Acompanhamento — João Pedro Félix Reis

**Grupo:** 4
**Data:** 14/09/2026

## Atividades realizadas — 14/09/2026

Durante o encontro, participei da discussão sobre a escolha do cenário do Grande Desafio. O grupo optou pelo **Desafio 1 — Contratações Públicas do Estado do Rio de Janeiro**.

Também realizei o setup inicial do projeto com o clone do repositório do grupo para o ambiente local.

Criei o grupo de WhatsApp da equipe para facilitar a comunicação e a organização das atividades ao longo do desafio.

Participei da discussão sobre o conteúdo inicial do `ARQUITETURA.md`, especialmente sobre a justificativa para a escolha do cenário. 

Também discutimos algumas experiências individuais dos integrantes do grupo, buscando identificar conhecimentos prévios e experiências que possam contribuir para a divisão futura das responsabilidades no projeto.

## Resultado do encontro

Ao final do encontro, o grupo havia definido o cenário de Contratações Públicas do RJ, iniciado a organização do repositório e avançado na discussão da arquitetura e da justificativa do projeto.

## Próximos passos

Os próximos passos são continuar a definição da arquitetura, dividir as responsabilidades entre os integrantes e avançar na construção dos arquivos iniciais do projeto, como `ARQUITETURA.md`, `PERSONAS.md` e `corpus_manifest.yaml`.

## Atualização — 16/09/2026

### Atividades realizadas

Nesta sessão, continuei a organização técnica do repositório e mantive as branches `main` e `joao-reis` sincronizadas com o remoto.

Organizei os artefatos coletados na pasta `data/`, separando arquivos brutos por tipo em `data/raw/v1/arquivos/` e relatórios de execução em `data/runs/`. Também garanti que os arquivos fossem identificados por hash SHA-256, evitando duplicações.

Implementei os scripts `src/fetch_corpus.py` e `src/ingest.py`. O coletor permite selecionar um tipo específico e uma quantidade exata de arquivos novos, salva cada arquivo imediatamente após o download e utiliza as fontes compatíveis com o tipo solicitado. A ingestão prepara os documentos para o corpus, gera chunks determinísticos e marca PDFs que ainda aguardam extração.

Também criei e estruturei `testes/test-fontes/artifact_downloader.py`, `testes/test-fontes/pncp_client.py`, `testes/test-fontes/testar_fontes.py`, `testes/test-fontes/testar_fontes_cenario01.py`, `testes/test-fontes/fontes_cenario01.md` e `data/CORPUS_INVENTORY.md`.

Utilizei o Codex como ferramenta de apoio para criar os esqueletos iniciais dos scripts e da estrutura de ingestão, gerar arquivos auxiliares e estruturar as métricas de disponibilidade, quantidade por tipo, arquivos novos e hashes únicos.

Validei as APIs e URLs das fontes, incluindo os endpoints do PNCP e as fontes normativas. O coletor foi ampliado para percorrer todos os arquivos retornados pelos endpoints de editais e atas, em vez de considerar somente o primeiro arquivo.

Ao final, foram coletados 20 PDFs novos, além de 2 arquivos HTML, 2 JSON e 2 JSONL. O diretório passou a conter 47 arquivos com hashes únicos. Os resultados das coletas foram salvos em relatórios versionados.

### Resultado da sessão

O fluxo de coleta ficou modularizado, seletivo por tipo e resiliente a falhas posteriores, preservando os arquivos que já foram baixados. Consultei o histórico de commits da sessão e registrei os principais commits realizados:

- `ebfe727` — `chore: organiza as saídas do corpus em data`
- `2335b94` — `refactor: modulariza os testes de fontes e o cliente PNCP`
- `672d35f` — `docs: documenta o inventário do corpus e as fontes iniciais`
- `2affbcb` — `chore: inclui dados coletados no repositório`
- `2563ea4` — `feat: amplia a coleta seletiva do corpus`
- `acfac9e` — `data: adiciona artefatos coletados por tipo`
- `e038a91` — `docs: atualiza o manifesto do corpus`
- `77e9668` — `docs: atualiza acompanhamento do João`

## Atualização — 18/09/2026

### Atividades realizadas

Reestruturei o `src/fetch_corpus.py` para realizar a coleta de arquivos HTML, PDF, JSON e JSONL, com seleção de uma quantidade exata de documentos novos e únicos. O fluxo passou a incluir deduplicação, tentativas configuráveis, timeout, checkpoints e retomada de execuções interrompidas.

Também implementei o armazenamento versionado e imutável dos resultados, com promoção da versão somente após a conclusão da coleta. Cada arquivo passou a ser registrado em um inventário com informações de origem, caminho, tamanho, status, data de coleta e hash SHA-256.

O coletor passou a oferecer os subcomandos `fetch`, `update` e `inventory`. Além da coleta, o inventário permite comparar versões do corpus e identificar arquivos adicionados, removidos, alterados ou mantidos iguais. Também foi incluída a opção de salvar relatórios de execução em JSON.

Criei o teste offline `testes/teste_extração.py` e o respectivo log de execução. A validação verificou o carregamento do manifesto, a disponibilidade dos subcomandos, o contrato do inventário e as regras de armazenamento dos dados brutos, sem realizar novos downloads durante o teste.

### Resultado da sessão

O fluxo de extração ficou mais controlado, reexecutável e rastreável, com suporte a versionamento e auditoria das alterações no corpus. A validação registrou um inventário com 47 arquivos distribuídos em 5 bases, sem falhas.

O principal commit realizado foi:

- `2a31f9a` — `feat: implementar extração de corpus orientada por manifesto`

Esse commit alterou o coletor, adicionou o teste offline e registrou o log de validação, totalizando 994 linhas adicionadas em três arquivos.
