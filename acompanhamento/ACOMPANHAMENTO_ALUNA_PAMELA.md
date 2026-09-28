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