# Corpus v6 e PERSONAS.md — análise de cobertura

Análise local de 30/09/2026, baseada no inventário oficial v6, nos textos
normalizados e na avaliação CLI já executada. Não houve coleta, chamadas a LLM,
alteração de dados, reembedding ou mudança de índices nesta análise.

**O v6 sustenta consultas sobre contratos e algumas normas, mas ainda não sustenta
integralmente as perguntas das seis personas.** Os principais problemas são os
vínculos escassos entre bases, a ausência de preços unitários comparáveis e a
diferença entre cláusula contratual e registro de ocorrência. As 22 rotas corretas
da avaliação anterior não medem cobertura nem correção factual.

## Composição verificada

| Base | Arquivos físicos | Formatos | Municipal / estadual / federal | Chunks |
|---|---:|---|---|---:|
| contratos_estruturado | 625 | 625 JSON | 434 / 32 / 159 | 625 |
| editais_normativo | 13 | 9 PDF, 4 HTML | 2 / 6 / 5 | 1.561 |
| atas_conversacional | 376 | 336 JSON, 40 PDF | 165 / 21 / 190 | 2.524 |
| pca_agregado | 386 | 386 JSON | 203 / 24 / 159 | 911 |
| Total | 1.400 | 1.347 JSON, 49 PDF, 4 HTML | 804 / 83 / 513 | 5.621 |

Arquivos, unidades extraídas e chunks são contagens diferentes. Um arquivo pode
gerar várias unidades, inclusive páginas ou registros. Dos 5.621 chunks, 5.619
referenciam arquivos do inventário; os outros dois são indicadores derivados.
Três arquivos são respostas JSON vazias e não geram chunks: dois nas atas e um
na agregada. Status `ok` da coleta não significa conteúdo útil para responder.

O limite de cinco arquivos por CNPJ/base foi respeitado. A distribuição é
57,4% municipal, 5,9% estadual e 36,6% federal; o recorte estadual é pequeno para
as perguntas de Helena e para a comparação de Rafael. Esse limite também torna
rankings e recorrência estatísticas da amostra curada, sem representatividade
demonstrada sobre todo o Rio de Janeiro.

Há 184 pares com hash bruto idêntico entre Estruturada e Agregada: 1.216 hashes
distintos nos 1.400 arquivos. Não foram encontrados pares idênticos dentro da
mesma base. O reuso entre bases pode servir a representações distintas, mas não
deve virar duas observações em uma soma ou contagem conjunta. Não se recomenda
excluir esses arquivos automaticamente.

## Conteúdo efetivo e metadados

Todos os 1.400 itens têm origem HTTP(S). Os sete itens normativos sem CNPJ não
devem receber órgão inventado: incluem fontes gerais de legislação. Essas
verificações confirmam presença do link, não disponibilidade atual do servidor.

Na Estruturada há **448 contratos e 177 publicações de contratação**. Os 448
contratos têm fornecedor e data de assinatura nos metadados; as 177 publicações
têm modalidade. Nenhum dos 625 registros normalizados reúne fornecedor e
modalidade simultaneamente. Todos têm órgão, valor, publicação, UF e município.
Não se deve tratar a publicação como contrato assinado, nem valor contratado
como pagamento realizado.

A chave de compra dos contratos intersecta a chave PNCP das publicações em
apenas **duas compras**. Pela mesma chave explícita nos metadados normalizados,
a Estruturada intersecta sete compras das atas, uma dos editais e sete da
Agregada. Esses números são um mínimo diretamente identificável por chave;
relações aninhadas não foram exauridas. Uma pergunta cruzada precisa comprovar
o vínculo específico antes de combinar fontes.

`atas_conversacional` contém sobretudo atas de registro de preços e recursos
PNCP associados. Isso não comprova que existam conversas, reuniões ou ocorrências
de execução contratual. A busca textual encontrou 27 arquivos dessa base com
termos de atraso/descumprimento; uma previsão de penalidade não é prova de atraso.

`pca_agregado` contém 355 publicações, sete contratações vinculadas, 14 contratos,
um recurso de PCA planejado, sete recursos de itens e dois de resultado de item.
O nome da base não deve ser interpretado como 386 planos anuais. Dados de
planejamento, contratação e resultado precisam permanecer separados nos cálculos.

## Cobertura das perguntas

Varredura exploratória no **objeto** dos 625 registros estruturados: 164
candidatos de tecnologia/informática/software, 15 de ambulância, um explícito de
ambulância tipo D, nove de manutenção próxima de termos de informática e seis
de material de escritório. São candidatos lexicais, sujeitos a falsos positivos;
não foram promovidos a exemplos equivalentes nem a respostas corretas.

Na varredura de todos os textos normativos não houve candidato de ambulância.
Houve cinco arquivos normativos com microempresa/EPP/MEI e seis com atraso.
Ausência de correspondência textual não prova inexistência do tema, sobretudo
quando depende de OCR, sinônimo ou referência indireta.

| Persona / pergunta | Situação no v6 | Limitação ou próxima verificação |
|---|---|---|
| Helena — P1.1, fornecedores recorrentes | Parcial | Poucos contratos estaduais; aplicar ano, valor e objeto antes de contar contratos distintos por fornecedor. Resultado limitado à amostra. |
| Helena — P1.2, problemas dos fornecedores encontrados | Insuficiente para concluir ocorrência | Vincular as sete compras em comum e examinar evidência de evento; cláusula de atraso não basta. Resolver referência à pergunta anterior. |
| Helena — P1.3, fraude/ilegalidade | Recusa prevista | Contratos e cláusulas não comprovam fraude. Preservar a recusa existente. |
| Rafael — P2.1, ambulâncias D publicadas | Parcial | Um candidato explícito municipal, de serviço de remoção; não equiparar a compra de veículo. Modalidade depende de vínculo. |
| Rafael — P2.2, mediana por veículo municipal/estadual | Insuficiente | Faltam quantidades, preço unitário, compras estaduais comparáveis e natureza de aquisição. Valor global não é valor por ambulância. |
| Rafael — P2.3, editais e especificações | Insuficiente | Nenhum candidato textual normativo de ambulância; falta pacote contrato–compra–edital–item. |
| Carolina — P3.1, pesquisa de preços vigente em 2025 | Parcial | Há normas e procedimento recuperável; validar texto histórico, alterações e vigência por dispositivo. Compilação de 2026 não comprova sozinha 2025. |
| Carolina — P3.2, norma da modalidade de um processo | Parcial | Publicações possuem modalidade; explicitar processo escolhido, competência e período da norma. |
| Carolina — P3.3, futuro e vencedores prováveis | Recusa prevista | Snapshot não informa o futuro nem sustenta previsão de vencedores. |
| Bruno — P4.1, manutenção de computadores | Parcial | Nove candidatos lexicais incluem locação com manutenção; separar serviço solicitado de manutenção de sistemas. |
| Bruno — P4.2, frequência de modalidades | Insuficiente sem enriquecimento | Modalidade ausente nos contratos; ligar publicações e usar apenas conjunto validado de P4.1. Falha 429 da avaliação anterior é de API, não diagnóstico do corpus. |
| Bruno — P4.3, regras da modalidade mais usada | Dependente de P4.2 | Não escolher modalidade antes de calcular frequência com dados suficientes. |
| Patrícia — P5.1, dez maiores contratos | Viável na amostra | Usar contratos, ano e campo de valor explícitos; separar publicações e evitar reuso entre bases. |
| Patrícia — P5.2, fornecedores com maior total | Viável com ressalva | Somar contratos com fornecedor; dizer valor contratado, não recebido/pago, pois não há comprovação de pagamento. |
| Patrícia — P5.3, evolução de tecnologia | Parcial | Classificação lexical inclui transferência farmacêutica; publicar recorte, períodos observados e tamanho da amostra. Comparar extremos não prova crescimento contínuo. |
| Lucas — P6.1, participação de microempresa | Melhor cobertura normativa | LC 123 e normas relacionadas recuperáveis; citar dispositivo, limites e recorte temporal, sem prometer habilitação de empresa específica. |
| Lucas — P6.2, material de escritório | Parcial | Seis candidatos no objeto; recuperação anterior trouxe objetos alheios. Validar candidatos reais e completar modalidade por vínculo. |
| Lucas — P6.3, regras e contratações compatíveis | Parcial | Há candidatos nas duas bases; ainda falta demonstrar modalidade e aplicabilidade por processo. |

F1 exige aquisição, equivalência técnica, preço por veículo, separação de esferas
e editais vinculados. O v6 não sustenta esse cálculo. A recusa do reteste é
adequada; não ajustar o gabarito original apenas para melhorar a pontuação.
Os três exemplos do desafio também permanecem no relatório de qualidade, mas
não substituem esta análise das 18 perguntas de PERSONAS.md.

## Prioridades para aproveitar o que já existe

1. **Separar recursos nos cálculos.** Auditar rankings, totais e séries para
   distinguir contrato, publicação, PCA e resultado. Deduplicar observações por
   identificador de negócio, com hash como verificação adicional. Valor zero
   continua zero; assinatura, publicação e resultado são referências distintas.
2. **Aproveitar vínculos disponíveis.** Inspecionar as duas compras com contrato
   e publicação e as interseções com editais/atas. Completar modalidade apenas
   com fonte vinculada; medir quantos pedidos passam a ser respondíveis.
3. **Validar os candidatos das personas.** Conferir os nove candidatos de
   manutenção, seis de escritório e único tipo D contra texto e registro bruto.
   Criar julgamentos de recuperação por pergunta; separar erro de busca de
   ausência de dados. As 22 rotas corretas não resolvem essa etapa.
4. **Auditar temporalidade e evidências de evento.** Para normas, registrar
   vigência e alteração; para atas, distinguir condição prevista de ocorrência.
   Resolver perguntas que dependem do conjunto encontrado anteriormente.
5. **Só depois considerar curadoria adicional.** Caso seja autorizada nova coleta,
   priorizar pacotes vinculados estaduais/municipais nos temas faltantes,
   respeitando cinco itens por órgão/base, em vez de aumentar volume isolado.
   Preços unitários requerem quantidade, unidade, aquisição/serviço e especificação
   técnica; pagamentos requerem fonte própria. Nenhuma coleta foi feita aqui.

Para cada pergunta, a próxima avaliação deve julgar separadamente cobertura,
recuperação, cálculo e fidelidade da resposta, com candidatos relevantes
verificados. Uma recusa por falta de evidência pode ser correta mesmo quando o
gabarito de recusa diz o contrário. Cross-encoder, histórico/feedback persistidos,
execução PostgreSQL e trilha de e-mails continuam pendências próprias.

## Evidências e limites

- [PERSONAS.md](../PERSONAS.md): fonte das perguntas e dos perfis.
- `eval/runs/quality-v6/corpus-personas-analysis.json` (arquivo local, fora do Git):
  contagens por arquivo, padrões lexicais, metadados, hashes e interseções.
- [Qualidade de consultas](qualidade_consultas_v6.md): avaliação CLI, revisão
  factual e retestes; rodada completa e reparos são resultados separados.
- Inventário: `data/inventory/v6.json`; textos:
  `data/normalized/v6/documents.jsonl` e cópia ativa `data/normalized/documents.jsonl`.

Esta análise não constitui verificação jurídica externa, prova de inexistência
no universo PNCP ou benchmark de precisão/recall. Os candidatos foram contados
com regex normalizada e sem julgamento semântico automatizado. Os vetores,
índices, ZIPs e arquivos brutos foram preservados.

## Ajustes e verificação do estado atual

Após a análise, foram feitas correções pontuais no caminho existente:

- Valor homologado zero permanece zero no SQLite; valor estimado só é usado
  quando o homologado está ausente. O corpus atual não apresentou registros com
  essa substituição incorreta; o caso foi reproduzido em teste de regressão.
- A análise por regras distingue pedidos de valor contratado, estimado e
  homologado. Uma referência explícita à publicação é preservada mesmo quando
  o valor pedido é homologado. Preço por unidade continua um pedido distinto.
- Consultas de valor contratado restringem o resultado a contratos assinados,
  mantendo a data de publicação quando ela foi pedida. O cabeçalho da evidência
  SQL registra esse filtro, evitando combinar estimativas de compras no ranking.
- O compilador PostgreSQL offline aceita valor contratado e agrupamento por
  mês de publicação. Isso é um contrato de compilação; a execução continua
  pendente e não houve conexão ou DDL PostgreSQL.
- Uma busca SQL vazia informa ausência com os filtros aplicados, evitando
  afirmar inexistência do assunto no corpus inteiro.

A revisão interna de cache do SQLite passou a `3`, sem mudança nas tabelas.
A base local foi remontada pelo mecanismo existente e a versão anterior foi
preservada em backup, cujo caminho consta na auditoria. Vetores, índices,
normalizados, inventário e ZIPs foram mantidos.

**Validação: 566 testes offline passaram; as 22 rotas continuam corretas na
auditoria local por regras.** Esse resultado não é uma nova rodada de geração
OpenRouter. O teste de recuperação isolou BM25 para P4.1 e P6.2: ainda retornou
objetos alheios nos primeiros resultados, apesar dos candidatos identificados
no corpus. A consulta de modalidades em P4.2 retornou três registros com
modalidade não informada. Essas pendências permanecem abertas, sem transformar
falta de campo em inferência de modalidade.

Detalhes em `eval/runs/quality-v6/current-state-audit.json` (arquivo local, fora do Git)
e `eval/runs/quality-v6/tests-current-adjustments.txt`. As compilações SQL nessa
auditoria são sondagens locais; não representam integração do novo caminho
PostgreSQL ao pipeline. As rodadas anteriores de respostas continuam separadas.

### Preparação para uma futura v7

Antes de ampliar volume, estabelecer relevância julgada para os candidatos de
manutenção e escritório e medir recuperação por pergunta. Inspecionar os vínculos
existentes de contrato/publicação, preservando ambas as fontes ao completar
modalidade. Para a coleta futura, priorizar pacotes ligados de ambulâncias tipo D
nas duas esferas e documentos que comprovem execução/ocorrência, sem promover
serviço de remoção a compra de veículo. Manter cinco itens por órgão/base e
explicitar quais recursos cada arquivo contém. O alvo de volume da v7 ainda
não foi definido; esta rodada não iniciou coleta.
