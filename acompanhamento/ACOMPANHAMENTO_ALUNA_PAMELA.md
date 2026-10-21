# Acompanhamento - Grande Desafio: IA Generativa & RAG

## Encontro 1 - 2026-09-14

### Relato individual - Pâmela de Freitas

- Por hoje participei na discussão para escolha do cenário, escolhemos o cenário 1
- Resolvemos a arquitetura em grupo
- Fiz as anotações do time 4 no notion
- Dei git clone e preparei o ambiente para iniciar o projeto


## Encontro 2 - 2026-09-16

### Relato individual - Pâmela de Freitas

- Hoje fizemos o arquivo de arquitetura, eu fiz a parte de pipeline, o diagrama e stage. Ainda vamos discutir sobre os nomes se vão mudar ou ficarão esses mesmos do arquivo.


## Encontro 3 - 2026-09-18

### Relato individual - Pâmela de Freitas

- Minha parte individual hoje foi fazer o arquivo config.py

## Encontro 4 - 2026-09-21

### Relato individual - Pâmela de Freitas

- Criado src/router.py.
- Criada a função rotear_pergunta(query).
- A função retorna RouteDecision, conforme definido em src/schema.py.
- O roteador identifica as quatro naturezas:
    - 1 - normativa;
    - 2 - estruturada;
    - 3 - conversacional;
    - 4 - agregada.
- Permite rotas combinadas, como estruturada + agregada.
- Trata perguntas sobre fraude, ilegalidade e dados sensíveis como recusa.
- Trata perguntas fora do escopo, como previsões futuras.
- Normaliza letras maiúsculas, minúsculas e acentos.
- Não acessa os índices nem chama o LLM. Ele apenas decide para quais bases a busca deverá ir.
Também foi criado tests/test_router.py, com 7 testes cobrindo esses comportamentos.


## Encontro 5 - 2026-09-23

### Relato individual - Pâmela de Freitas

- Ela possui os comandos rota e perguntar.
- Implementei uma CLI simples para testar o projeto em terminal.
- rota valida em quais bases a pergunta deve buscar.
- perguntar executa o pipeline completo.
- Também aceita várias perguntas em uma mesma execução.
- A validação foi confirmada no terminal

Exemplos de uso:

- python app/cli.py --help

- python app/cli.py rota 
- e aí abre um input pra fazer a pergunta no terminal

- python app/cli.py perguntar
- e aí abre um input pra fazer a pergunta no terminal

Ou direto com a pergunta:

- python app/cli.py rota "Quais contratos de software foram assinados acima de 500 mil?"
- python app/cli.py perguntar "Quais contratos de software foram assinados acima de 500 mil?"

## Encontro 6 - 2026-09-25

### Relato individual - Pâmela de Freitas

- Concluí a coleta oficial das quatro bases. Tive resolver um erro de certificado SSL usando o bundle certifi do Python, a coleta terminou completa com 222 arquivos, 200 itens e zero falhas.

- Preparei as bases e gerei um índice local com 3.130 chunks, todos com vetores de dimensão 1024.
Fiz um teste real com retrieve, que retornou três resultados.Não rodei a avaliação completa do golden set.

- Os dados brutos, os documentos normalizados e o índice continuam locais e ignorados pelo Git. Portanto, o commit dos inventários não entrega o índice pra vcs, para usarem o retrieve, cada um precisa gerar os artefatos na própria máquina

### Uso de assistentes de IA:

- Usei para ajudar no erro de certificado que estava dando em um dos documentos

## Encontro 7 - 2026-09-28

### Relato individual - Pâmela de Freitas

Fiz um fix para arrumar o query_analyzer
- o sistema voltou a responder certo
- Ele tava com um erro que fazia toda pergunta terminar em recusa (sem_evidencia):
- Os filtros zeravam a busca. "acima de 500 mil" virava filtro de valor, mas nenhum chunk do índice tem esse campo preenchido, então o sistema descartava tudo.
- O LLM não respondia em JSON. O prompt era ~4x maior que o limite do servidor da turma (2048 tokens), e o servidor cortava o começo, onde estava a instrução "responda em JSON".
 
O que mudou:
 
- retrieve.py: o filtro é ignorado na base que não tem o campo, ou quando eliminaria todos os chunks. Isso fica registrado no log (filtros_ignorados).
- generate.py: os chunks vão compactos e com tamanho limitado, para caber no limite; a instrução de formato se repete no fim do prompt; e o servidor é obrigado a devolver JSON (response_format).
- config.py: dois limites novos e ajustáveis, GENERATION_CONTEXT_CHARS e GENERATION_CHUNK_CHARS.
 
- ajustes no query_analysis:
 
- O filtro escondia a resposta certa. Em "valor total do PCA em 2025", o indicador com a resposta (R$ 61,1 bi) sumia, porque não tem data_vigencia. Agora os filtros da pergunta são opcionais: chunk sem o campo continua na busca, e só sai quem tem o campo com valor fora do filtro.
- Ano virava valor. "maior valor total… de 2025" virava valor_min=2025. Agora o número precisa vir logo depois do gatilho ("maior que 100 mil"), e um ano solto só conta como valor com "R$", "reais", "mil" ou "milhão".
 
Aproveitei e corrigi também:
 
- Período: "entre 2024 e 2025" agora vira o intervalo de 2024 a 2025 (antes pegava só 2024).
- Contagem: "mais de 3 contratos" e "acima de 500 mil habitantes" não viram mais filtro de valor.
- Ano de lei: "Lei 14.133/2021" e "edital 90005/2025" não viram mais filtro de ano.
- Bilhões: "acima de 2 bilhões" passou a ser entendido.
- Arquivos: query_analysis.py e schema.py, adicionei um campo ano_final para o período.
- Testes: 70 novos no total (9 na parte 1 e 18 na parte 2; o resto veio da main); agora são 406 passando.
 
Resultado: a busca acha os chunks certos, os filtros não escondem mais a resposta, e a IA responde no formato certo, citando as fontes.
Mais um fix em query_analyzer:
- Um número com cara de ano não conta como ano quando parece dinheiro: tem "R$" antes (R$ 2025); ou tem "reais", "mil", "milhão" ou "bilhão" depois (2000 reais, 2025 mil).
- Nesses casos, o sistema pula esse número e continua procurando outro ano na pergunta.
- Nos testes, acrescentei os 5 casos da tabela em tests/test_query_analysis.py.

### Uso de assistentes de IA:

Usei para ajudar a achar a melhor solução nos erros que estavam aparecendo


## Encontro 8 - 2026-09-30

### Relato individual - Pâmela de Freitas

Hoje trabalhei nos filtros do query_analysis e em como eles são usados na busca e no SQL.

#### Correções pontuais (casos que o Renato reportou)

- Valor com centavos virava ano: "acima de 2000,00 reais" gerava ano=2000, e "acima de 2025,00 em 2024" perdia o 2024.
- "Contratos acima de 2000 em 2025" virava ano=2000 e sem valor. Agora, depois de "acima de", "superior a", "maior que" etc., o número é sempre valor; só "a partir de" e "até" tratam 2024/2025 como ano.
- Passaram a ser reconhecidos "superior a", "inferior a", "maior/menor que" com número com cara de ano, e "entre 1000 e 2000" (antes virava ano=2000).

#### Revisão completa dos filtros

- Revisei o query_analysis e todo o código que usa os filtros (retrieve, structured_query, pipeline). No SQL do Renato, os filtros viram WHERE estrito, então filtro errado dá resposta errada.
- Achei e corrigi problemas no SQL causados pelos filtros:
    - "acima de 500 mil" buscava contratos com "acima" no objeto;
    - "maior que 2000" virava pedido de ranking;
    - "entre 2024 e 2025" filtrava só 2024.
- Como cada correção abria um caso novo, reescrevi a extração com uma regra só: **o filtro só é criado quando a leitura não deixa dúvida**. Na dúvida, nenhum filtro.
- Casos que passaram a funcionar:
    - "antes de 2024" (até 2023), "depois de 2023", "a partir de", "até";
    - datas completas e com mês/semestre, e "2024 x 2025" como período;
    - leis e processos com ano no número ("Lei 14.133/2021", "pregão 05/2023"), que não viram ano;
    - contagens ("mais de 3 contratos", "2000 cestas"), que não viram valor nem ano;
    - "1 milhão e meio", "500k" e "1,2 mi".
- No retrieve: o filtro de órgão ignora maiúsculas e acentos; um filtro que reprova todos os chunks com o campo é ignorado naquela base; valor ilegível conta como campo ausente.

#### Testes

- Fiz três rodadas de revisão adversarial com agentes de IA tentando quebrar o código, com cerca de 20 mil perguntas no total.
- Resultado final:
    - 0 erros nas 3.840 combinações de valor × período;
    - 1.656 de 1.663 perguntas adversariais certas (o que sobra é escolha de propósito).
- Os casos encontrados viraram mais de 130 testes de regressão em `tests/test_query_analysis.py`.

#### Merge com a main

- Juntei a main nova (v6, rerank e o SQL do João) com as minhas mudanças e resolvi os conflitos em query_analysis, structured_query e nos testes:
    - mantive as detecções novas do João, calculadas sobre o texto sem as expressões de filtro;
    - separei os dois `ano_final` (o dele e o meu tinham sentidos diferentes), criando `ano_desde`/`ano_ate`;
    - desfiz uma linha dele no retrieve que trazia de volta o erro do PCA.
- Suíte completa: **753 testes passando**. Branch `pamela-filtros-conservadores` enviada para o GitHub.

#### Pendências e avisos

- Mandei ao Renato uma lista de problemas no SQL dele: palavras comuns virando objeto, modalidade, esfera federal, mês ignorado, contagem sem os valores nulos.
- O `index/` e o `data/normalized/` da v6 foram commitados no Git e substituíram o meu índice local v1. O zip antigo continua guardado.

### Uso de assistentes de IA:

- Usei o Claude Code para diagnosticar os erros, escrever as correções e os testes, rodar as revisões adversariais (agentes tentando quebrar o código) e resolver os conflitos do merge.
- Eu direcionei o que corrigir a partir dos casos do Renato, pedi a revisão completa do código que usa os filtros e decidi manter as funcionalidades do João no merge. Conferi os resultados rodando os testes e os comandos de verificação.

## Encontro 9 - 2026-10-02

### Relato individual - Pâmela de Freitas

Hoje trabalhei na extração de órgão, esfera e modalidade do query_analysis, a partir do que o Renato pediu.

#### Órgão com "no PCA" junto do nome

- O nome do órgão pegava o que vinha depois: "Secretaria de Saúde no PCA", "Prefeitura de Niterói no PNCP".
- Agora "no", "na" e "em" encerram o nome, e siglas do sistema (PCA, PNCP, Lei, Edital…) também.

#### Agora funciona

- Reconhecer órgão sem depender de maiúscula: "prefeitura de niterói", "secretaria municipal de saúde".
- Palavras novas de início de nome: fundo, comando, banco, defensoria, município, procuradoria, controladoria, polícia, companhia, empresa pública.
- Siglas de órgãos: SEEDUC, SES, DPE, DETRAN, UERJ, FIOCRUZ, TCE-RJ…
- Esfera federal quando aparece "federal" ou "federais".
- Modalidade (pregão, dispensa, concorrência, inexigibilidade…), só quando a pergunta é sobre os dados. "Como funciona o pregão?" não vira filtro.

#### Revisão adversarial e reescrita

- Antes de mandar, pus um agente de IA para tentar quebrar a parte nova. Ele achou 24 tipos de erro, como:
    - o nome engolia palavras ("Comando da Marinha e informe");
    - cidades eram cortadas ("prefeitura de são", sem "gonçalo");
    - palavras comuns viravam órgão ("banco de sangue", "PVC", "MAIOR").
- Mudei a abordagem: em vez de adivinhar onde o nome termina, o nome é **conferido contra a lista real de órgãos do corpus** (353 nomes, em `data/config/orgaos_corpus.json`, gerada com `py -m src.query_analysis orgaos`) e contra a lista dos 92 municípios do RJ.
- Também corrigi: "no município do Rio" (lugar) e "legislação federal" não viram esfera; "pouca concorrência", "dispensa médica" e "não foram por dispensa" não viram modalidade.
- No SQL do Renato, "prefeitura de niterói" passou a trazer os 5 contratos da prefeitura, e não mais 12.

#### Testes

- Régua do agente revisor:
    - 216 perguntas escritas à mão: de 130 para **210 certas**;
    - 13.440 frases combinadas: de 67,7% para **93,8%**.
- Suíte completa: **879 testes passando**, sem regressão nas revisões anteriores.
- Branch `pamela-orgao-esfera-modalidade`.

#### Pendências e avisos

- Órgão em minúsculas que não está no corpus, e sem município no nome, fica sem filtro de órgão, de propósito: sem a lista, é mais seguro não chutar onde o nome termina.
- Encontrei três pontos no SQL do Renato, para ele avaliar:
    - "UERJ" e "Prefeitura do Rio de Janeiro" casam com órgãos errados, porque o SQL descarta "estado", "rio" e "janeiro";
    - o SQL calcula esfera e modalidade por conta própria e lê "Lei federal" como esfera;
    - "Quantos pregões eletrônicos?" não gera consulta.
- Quando o corpus mudar, é preciso rodar `py -m src.query_analysis orgaos` de novo.

### Uso de assistentes de IA:

- Usei o Claude Code para eescrever os testes, rodar a revisão adversarial com um agente e conferir os resultados no SQL com a base local.
- Eu trouxe os pedidos e os casos do Renato, decidi não mexer no módulo de SQL dele, e conferi os resultados com os testes e com o caso da prefeitura de Niterói.

## Encontro 10 - 2026-10-05

### Relato individual - Pâmela de Freitas

Hoje analisei a base sintética de conversas e fiz os ajustes do query_analysis pedidos no documento de correções.

#### Base sintética de conversas

- Analisei o `conversacional_sintetica.zip` do tutor: 25 conversas (45 mensagens), em três formatos com o mesmo conteúdo. O campo `guardrail_type` funciona como gabarito: mascarar CPF e telefone, recusar dados bancários, credenciais e juízo de fraude.
- Levantei o que precisava mudar para integrar a base:
    - não indexar os campos de gabarito;
    - sensibilidade por conversa (as 5 restritas entrariam como públicas);
    - escopo oficial no manifesto;
    - roteador reconhecendo perguntas sobre as conversas;
    - perguntas novas no golden set.
- A integração é parte do João, não mexi.
- Encontrei uma data inconsistente na conv_013: a conversa é de 20/10/2025, mas as mensagens são de 20/01/2025.

#### Correções do query_analysis (item 1 do documento de correções)

- A mensagem de cortesia ganhou etapa própria no log, `interacao`. Antes, a decisão "conversa ou consulta" era gravada como `query_analysis` e se confundia com a análise da pergunta.
- O log do query_analysis passou a:
    - mascarar CPF, telefone e e-mail pessoal também no modo IA, nas subperguntas e na mensagem de erro do fallback;
    - não repetir a pergunta original, que já está no input;
    - limitar cada texto a 500 caracteres.
- Mexi o mínimo nos arquivos dos colegas: uma linha no `obs.py` (a etapa nova) e o nome da etapa no `conversation.py`. Atualizei o README e o ARQUITETURA com a etapa nova.
- 6 testes novos, que falham sem a correção. Suíte completa: **1.074 testes passando**.
- Branch `pamela-rastreabilidade-query-analysis`.

#### Comparação com o enunciado

- Comparei o projeto com o que o enunciado do Notion pede e encontrei pontos que não estão no documento de correções:
    - não há teste de integração marcado `@pytest.mark.llm`;
    - faltam os DIFFs `v1_v2` e `v5_v6` do inventário;
    - falta o diagnóstico da pergunta de falha (F1) para o relatório final.

#### Pendências e avisos

- Ficaram de fora o item 8 (rerank no modo IA), que depende de confirmação do Renato, e o item 11 (contexto entre perguntas), que depende de decisão da equipe.
- O modo IA do query_analysis envia a pergunta para a IA sem mascarar.
- O README ainda descreve o query_analysis como "sem LLM".

### Uso de assistentes de IA:

- Usei o Claude Code para analisar a base sintética, rodar a pergunta de ponta a ponta, depois que implementei as correções usei para os testes e comparar o projeto com o enunciado para dizer o que falta para critério de pronto

## Encontro 11 - 2026-10-07

### Relato individual - Pâmela de Freitas

Hoje fiz o item 7 do documento de correções (qualidade do corpus) e configurei a chave de acesso da API.

#### Qualidade do corpus

- Criei o meu `.env` a partir do `.env.example` e gerei a `RAG_API_KEY` que o Renato pediu, para os endpoints de histórico e de trace.
- Testei: com a chave, o histórico responde; sem a chave ou com chave errada, a API bloqueia (HTTP 401).
- Criei o `src/qualidade_corpus.py`, que roda entre a extração e o índice e muda só status e metadados, nunca o texto. Assim o índice foi refeito reaproveitando os vetores, sem gerar nenhum vetor novo.
- Atas: 819 chunks (52 páginas de 3 PDFs) eram glifos sem letras (`/12 /3 □`) e estavam com status "ok". Marquei como `texto_ilegivel` e tirei do índice e da busca por palavras (atas indexadas: de 2.524 para 1.705). Tabelas de lances continuam, porque o critério é o formato de glifo, não a pouca letra.
- Normas: 1.498 dos 1.561 chunks ganharam início de vigência (Lei 14.133, LC 123, Decretos 48.816 e 48.760, e editais pela data de publicação da compra). A vigência faltava porque o leitor do manifesto guardava as URLs com aspas, e a coleta nunca achava os metadados. "O que estava vigente em 2025?" passou a filtrar pela vigência das normas, e o prompt mostra "vigente desde".
- Contratos: só 3 dos 448 têm a compra no corpus e herdaram a modalidade dela. 44 valores abaixo de R$ 1,00 ficaram marcados como simbólicos.
- 39 testes novos, que falham sem as correções. Suíte completa na branch: **1.177 testes passando**.

#### Sobre o OCR das atas q fiz hoje

- Descobri que o `prepare_documents` só chamava o OCR em página vazia. As páginas de glifos tinham "texto" e nunca passavam pelo OCR. Corrigi: página com glifos vai para o OCR, e os códigos `/U0044` dos decretos são decodificados.
- Testei com o Tesseract: as 60 páginas das 3 atas foram lidas pelo OCR, com 323 partes de texto legível.


### Uso de assistentes de IA:

- Usei o Claude Code para escrever os testes e refazer o índice.

## Encontro 12 - 2026-10-09

### Relato individual - Pâmela de Freitas

Hoje fiz correção no query_analysis "Prefeitura de X" não casava com × "MUNICIPIO DE X"

- "Quais contratos da Prefeitura de Maricá existem?" gerava o filtro "Prefeitura de Maricá", mas no PNCP o órgão se chama "MUNICIPIO DE MARICA" ("PREFEITURA MUNICIPAL DE MARICA" é o nome da unidade). O filtro não casava com nada, e a busca era ampliada sem ele.
- Agora "Prefeitura (Municipal) de X", "Prefeitura da Cidade do X", "Município de X" e "Prefeitura do Rio" viram o nome do órgão no PNCP, tirado do catálogo de órgãos do corpus.
- A pergunta de Maricá passou a trazer só os contratos do MUNICIPIO DE MARICA, sem ampliar a busca (antes vinham 625 trechos de todos os órgãos). O SQL continuou com o mesmo resultado.


### Uso de assistentes de IA:

- Usei o Claude Code para implementar a correção

## Encontro 13 - 2026-10-14

### Relato individual - Pâmela de Freitas

Hoje trabalgei em cima das perguntas dos e-mails sintéticos no golden set.

- Coloquei três perguntas novas no golden set, que passou de 26 para 29 casos:
  - E1: o que diz o e-mail público da SEEDUC sobre o atraso dos notebooks da Nexus. Tem que citar a base `emails_sinteticos`, sem mostrar o CPF e o celular do fiscal.
  - E2: pede a chave PIX e a conta da caução, que estão numa conversa restrita. Tem que recusar.
  - E3: pede o usuário e a senha enviados à SEPM, também de conversa restrita. Tem que recusar.
- Criei o `tests/test_emails_sinteticos_geracao.py`. Sem chamar o LLM, ele mostra que as 5 conversas restritas nunca chegam ao prompt nem viram fonte, mesmo quando vêm primeiro no ranking e o LLM tenta citá-las.
- O E1 falhava por dois motivos no `generate.py`:
  - As conversas têm de 941 a 1.803 caracteres, e o limite por trecho é 900. O corte apagava a resposta do órgão. Agora a conversa vai mais enxuta no prompt e, se ainda não couber, o espaço é dividido entre as mensagens.
  - O modelo lia "não comprova fatos reais" como falta de evidência e recusava. Deixei a regra clara: pergunta sobre o que um e-mail diz é respondida com ele. Ele só não serve como prova de fato real do PNCP.
  - Também pus um aviso fixo de cenário fictício na resposta que cita conversa sintética, porque o LLM nem sempre avisava.
- Com o LLM, E1, E2 e E3 passaram (o E1 em 4 de 4 rodadas). Rodei também P1.2, A1, A2 e I2. A1 e I2 falham igual na main, então a falha não veio da minha mudança. Suíte completa: 1.634 testes passando.


### Uso de assistentes de IA:

- Usei o Claude Code para descobrir por que o E1 falhava, fazer os ajustes e os testes e rodar as perguntas com o LLM.


## Encontro 14 - 2026-10-16

### Relato individual - Pâmela de Freitas

Hoje fiz perguntas do golden set que dependem da anterior e casos de CNPJ e a correção dos textos desatualizados, e testei o sistema com frontend e backend juntos.

  - P1.3, P4.2 e P4.3 começam com "esses contratos" ou "os mesmos contratos" e eram avaliadas sozinhas. Agora cada uma diz de quais perguntas depende (`depende_de`), e o avaliador manda essas perguntas como histórico, como o frontend faz numa conversa. Vai só a pergunta anterior, sem a resposta, para o erro de um caso não passar para o seguinte.
  - Criei dois casos de CNPJ, com o gabarito conferido no banco: I3 (contratos da DATAPREV pelo CNPJ do fornecedor) e I4 (compras do Ministério das Relações Exteriores pelo CNPJ do órgão). O golden set passou de 29 para 31 casos.
  - Com o LLM, P1.3, I3 e I4 passaram. A P4.2 deu uma resposta certa com palavras que não estavam no gabarito; com o OK do Renato, acrescentei dois sinônimos, com teste mostrando que respostas erradas continuam reprovadas. A P4.2 e a P4.3 ainda falham por confiança alta, que é o item 22.


  - Guia de execução local: a branch é a `main`, o CORS do frontend local já vem liberado e troquei o exemplo de recusa por um que funciona.
  - Índice da pasta docs: faltavam 9 documentos, e o passo a passo dos e-mails sintéticos não funcionava mais.
  - Também pus o `RERANK_PRELOAD` no `.env.example` e atualizei o número de testes no ARQUITETURA.
- No teste do frontend com o backend, telas, trace, histórico e feedback funcionaram. Achei três defeitos e registrei nas pendências: "Qual é a capital da França?" é respondida em vez de recusada, "publicou" e "seus" viravam filtro no SQL (o Renato já corrigiu) e "Qual o valor de todos os contratos de 2025?" não passa pelo SQL.
- Suíte completa na main: 1.704 testes passando.


### Uso de assistentes de IA:

- Usei o Claude Code para implementar o histórico no avaliador e os testes, conferir os gabaritos no banco, rodar as perguntas com o LLM, testar o frontend com o backend e revisar a documentação.

## Encontro 15 - 2026-10-19

### Relato individual - Pâmela de Freitas

Hoje fiz o item 22 na P4.2: o LLM dizia que a modalidade era "pregão", deduzindo pelo amparo legal.

- Pus uma regra no prompt: a modalidade de um contrato só vem do campo de modalidade do próprio registro. O LLM não pode deduzir pelo amparo legal nem copiar de outro contrato que veio na busca, de outro assunto (foi o que o Diogo viu na P4.2).
- Corrigi também um conflito que ficou grande no v7: muitos contratos ganharam a modalidade pela compra vinculada, e o cabeçalho do trecho mostrava "modalidade = Pregão (da compra vinculada)", mas o registro dizia "modalidade não informada". Agora o registro mostra a mesma modalidade do cabeçalho.
- Testei com o LLM no v7 as perguntas que falam de modalidade e norma: com a mudança, P3.2, P4.2, P4.3 e P6.3 passaram (4 de 4); sem ela, 2 de 4. O "pregão" da P4.2 não se repetiu no v7 (o erro foi no v6), então deixei um teste automático com esse caso.
- O gabarito da P4.2 ficou da v6: no v7 são 12 contratos de manutenção de computadores em 2025, e 10 têm modalidade. Avisei que ele precisa ser revisto.
- Para testar, baixei os dados do v7 do Google Drive, porque o pull tirou os dados do repositório. O arquivo de vetores dos contratos (2,5 GB) não vinha no "Baixar tudo" e foi baixado separado; conferi que ele tem os 105.917 vetores do índice.
- O Renato juntou a minha branch na dele para levar à main. Suíte: 1.911 testes passando; a única falha é um teste do v7 que só quebra no Windows (acentos lidos sem UTF-8).
- Também estudei como funciona o log (etapas, resumo da consulta e session_id) para a apresentação.


### Uso de assistentes de IA:

- Usei o Claude Code para investigar a falha da P4.2, fazer a regra, a correção e os testes, conferir os dados do v7 e comparar as respostas do LLM com e sem a mudança.

## Encontro 16 - 2026-10-21

### Relato individual - Pâmela de Freitas

Hoje me preparei para a minha parte do Demo Day: resultados e melhorias (slides 14 e 15).

- Revisei o que entrou na main: o Renato rodou o golden set na v7 (35 de 36), refez o gabarito da P4.2 para a v7, como eu tinha avisado, e fez ela ir ao SQL. A minha correção do item 22 já está na main, e os três defeitos que eu tinha achado em 16/10 foram corrigidos.
- Li o plano B do Demo Day: as 5 consultas ao vivo saem do SQL, sem o LLM, e as que passam pelo LLM vão com captura prévia.
- Liguei o backend e o frontend com os dados da v7 e testei pelo site: "Quantos contratos de ambulância foram firmados em 2025?" deu 31 registros, pelo SQL, em 1,7 s, igual ao plano B.
- Estudei o log, cada etapa da pergunta grava uma linha em `logs/pipeline/pipeline-AAAA-MM-DD.jsonl`, e o "Ver caminho" do site junta essas linhas pelo `query_id`. Li o JSON (resumo da consulta, etapas puladas e o motivo, tempo de cada etapa) e a ver no navegador (F12, aba Rede) o JSON que o site recebe.
- Estudei os números dos slides 14 e 15 para explicar: golden set, recusas e citações, roteamento, busca, testes e as limitações.


### Uso de assistentes de IA:

- Usei o Claude Code para revisar o que entrou na main
