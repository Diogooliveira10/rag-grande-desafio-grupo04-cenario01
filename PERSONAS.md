# PERSONAS

> Este documento descreve as personas de referência do sistema e as perguntas utilizadas para orientar e avaliar o comportamento do Adaptive RAG.

## Visão geral

| Persona | Papel principal | Foco de uso |
|---|---|---|
| Persona 1: Helena Azevedo | Analista de Controle Interno de um órgão público do Estado do Rio de Janeiro | Precisa reunir informações confiáveis sobre valores, fornecedores e ocorrências registradas para apoiar a análise do processo |
| Persona 2: Rafael Mota | Gestor de Compras de um hospital público do Rio de Janeiro | Precisa encontrar contratações parecidas, verificar valores praticados, modalidades usadas e documentos que detalham os itens comprados |
| Persona 3: Carolina Nogueira | Assessora Jurídica de Licitações de uma entidade pública do Estado do Rio de Janeiro | Precisa encontrar normas e documentos que ajudem na análise jurídica de cada processo |
| Persona 4: Bruno Martins | Servidor responsável pelo planejamento de contratações em uma secretaria estadual | Precisa ter uma referência de valores, modalidades e órgãos que já contrataram objetos semelhantes |
| Persona 5: Patrícia Gomes | Pesquisadora de políticas públicas que estuda gastos governamentais e compras públicas no Estado do Rio de Janeiro | Precisa encontrar padrões nos dados sem ter que consultar contrato por contrato manualmente |
| Persona 6: Lucas Ferreira | Dono de uma pequena empresa que vende materiais de escritório e quer começar a participar de contratações públicas | Precisa saber se pode participar de determinados processos, quais exigências aparecem nos editais e onde existem oportunidades compatíveis com sua empresa |

## Persona 1: Helena Azevedo

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Analista de Controle Interno de um órgão público do Estado do Rio de Janeiro. Trabalha revisando processos de contratação antes que eles avancem para outras etapas. |
| **Situação de uso** | Usa o sistema quando precisa conferir contratos parecidos, fornecedores recorrentes e registros relacionados a uma contratação. |
| **Necessidade** | Precisa reunir informações confiáveis sobre valores, fornecedores e ocorrências registradas para apoiar a análise do processo. |
| **Urgência** | No mesmo dia, antes de emitir um parecer ou encaminhar o processo. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P1.1** | Fornecedores recorrentes em contratos de tecnologia | Quais fornecedores firmaram três ou mais contratos de serviços ou licenciamento de tecnologia com órgãos estaduais do Rio de Janeiro em 2025, considerando apenas contratos acima de R$ 500 mil? | Estruturada | Filtro |
| **P1.2** | Ocorrências relacionadas aos fornecedores | Entre os fornecedores encontrados nesses contratos, existem atas ou registros que mencionem atraso de entrega, descumprimento de prazo ou problema de fornecimento? | Estruturada + Conversacional | Cross-source |
| **P1.3** | Limite da análise | Esses contratos e registros comprovam que algum dos fornecedores fraudou licitações ou agiu de forma ilegal? | Nenhuma base para concluir fraude. Aplicar recusa. | Recusa |

## Persona 2: Rafael Mota

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Gestor de Compras de um hospital público do Rio de Janeiro. Participa da preparação de processos para compra de equipamentos, veículos e materiais hospitalares. |
| **Situação de uso** | Usa o sistema durante a pesquisa de preços e quando precisa comparar compras feitas por outros órgãos públicos. |
| **Necessidade** | Precisa encontrar contratações parecidas, verificar valores praticados, modalidades usadas e documentos que detalham os itens comprados. |
| **Urgência** | Alta, principalmente durante a elaboração de um novo processo de compra. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P2.1** | Contratações de ambulâncias tipo D | Quais contratações de ambulâncias tipo D foram publicadas por órgãos públicos do Rio de Janeiro em 2025? Informe órgão contratante, fornecedor, modalidade e valor contratado quando esses dados estiverem disponíveis. | Estruturada | Factual |
| **P2.2** | Comparação de valores | Qual foi a mediana do valor por ambulância tipo D nas contratações de municípios do Rio de Janeiro em 2025 e como esse valor se compara ao das contratações feitas por órgãos estaduais? | Estruturada + Agregada | Cross-source |
| **P2.3** | Especificações técnicas | Quais editais estão ligados às contratações de ambulâncias tipo D encontradas e quais especificações técnicas devem ser consideradas para saber se as compras podem ser comparadas? | Estruturada + Normativa | Cross-source |

## Persona 3: Carolina Nogueira

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Assessora Jurídica de Licitações de uma entidade pública do Estado do Rio de Janeiro. Dá apoio jurídico aos processos de contratação. |
| **Situação de uso** | Usa o sistema quando precisa localizar regras, conferir qual norma estava válida em determinada data e relacionar a legislação com um processo de contratação. |
| **Necessidade** | Precisa encontrar normas e documentos que ajudem na análise jurídica de cada processo. |
| **Urgência** | Alta, normalmente antes de emitir parecer ou responder uma diligência. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P3.1** | Norma vigente para pesquisa de preços | Qual regra estava vigente em 2025 para a pesquisa de preços nas contratações públicas e quais documentos do corpus tratam desse procedimento? | Normativa | Vigência |
| **P3.2** | Norma relacionada ao processo | Para uma contratação publicada no Rio de Janeiro em 2025, quais normas do corpus se aplicam à modalidade registrada no processo? | Estruturada + Normativa | Cross-source |
| **P3.3** | Consulta fora do escopo | Quais licitações de tecnologia estarão abertas no Rio de Janeiro no próximo mês e quais empresas provavelmente vencerão esses processos? | Nenhuma base. Consulta fora do escopo do corpus. | Fora de escopo |

## Persona 4: Bruno Martins

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Servidor responsável pelo planejamento de contratações em uma secretaria estadual. Ajuda as áreas internas a organizar demandas antes da abertura dos processos. |
| **Situação de uso** | Usa o sistema para consultar compras anteriores e entender como itens parecidos foram contratados por outros órgãos. |
| **Necessidade** | Precisa ter uma referência de valores, modalidades e órgãos que já contrataram objetos semelhantes. |
| **Urgência** | Antes da reunião de planejamento ou da montagem do termo de referência. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P4.1** | Compras semelhantes | Quais órgãos do Rio de Janeiro contrataram serviços de manutenção de computadores em 2025 e quais foram os valores desses contratos? | Estruturada | Filtro |
| **P4.2** | Modalidades mais usadas | Quais modalidades apareceram com mais frequência nas contratações de manutenção de computadores encontradas em 2025? | Estruturada + Agregada | Cross-source |
| **P4.3** | Regras aplicáveis | Quais regras do corpus tratam da modalidade mais usada nessas contratações? | Estruturada + Normativa | Cross-source |

## Persona 5: Patrícia Gomes

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Pesquisadora de políticas públicas que estuda gastos governamentais e compras públicas no Estado do Rio de Janeiro. |
| **Situação de uso** | Usa o sistema para levantar dados de contratos e comparar comportamentos de compra entre órgãos, municípios e períodos. |
| **Necessidade** | Precisa encontrar padrões nos dados sem ter que consultar contrato por contrato manualmente. |
| **Urgência** | Antes de fechar uma análise, relatório ou apresentação. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P5.1** | Maiores contratações | Quais foram as dez maiores contratações registradas no Rio de Janeiro em 2025 por valor contratado? | Estruturada | Filtro |
| **P5.2** | Concentração por fornecedor | Quais fornecedores receberam o maior valor total em contratos no conjunto de dados de 2025? | Estruturada + Agregada | Cross-source |
| **P5.3** | Mudança ao longo do tempo | Houve aumento no valor médio das contratações de tecnologia ao longo de 2025? | Estruturada + Agregada | Cross-source |

## Persona 6: Lucas Ferreira

### Perfil

| Campo | Descrição |
|---|---|
| **Quem é** | Dono de uma pequena empresa que vende materiais de escritório e quer começar a participar de contratações públicas. |
| **Situação de uso** | Usa o sistema para entender regras básicas e localizar processos que tenham relação com os produtos que sua empresa vende. |
| **Necessidade** | Precisa saber se pode participar de determinados processos, quais exigências aparecem nos editais e onde existem oportunidades compatíveis com sua empresa. |
| **Urgência** | Quando encontra uma oportunidade ou antes do prazo de envio de proposta. |

### Perguntas de referência

| ID | Título | Pergunta | Rota esperada | Tipo |
|---|---|---|---|---|
| **P6.1** | Participação de pequena empresa | Uma microempresa pode participar de pregão eletrônico segundo as regras presentes no corpus? | Normativa | Factual |
| **P6.2** | Oportunidades para material de escritório | Existem contratações de material de escritório no Rio de Janeiro em 2025 com informações de fornecedor, órgão e modalidade? | Estruturada | Filtro |
| **P6.3** | Regra e oportunidade na mesma consulta | Quais regras se aplicam à participação de microempresas e quais contratações de material de escritório do corpus usam modalidades compatíveis com essas regras? | Normativa + Estruturada | Cross-source |

# Pergunta de falha esperada

| Campo | Descrição |
|---|---|
| **ID** | F1 |
| **Pergunta** | Em 2025, órgãos estaduais do Rio de Janeiro pagaram mais por ambulâncias tipo D do que os municípios fluminenses? Compare a mediana do valor por veículo considerando apenas compras tecnicamente equivalentes, informe as modalidades usadas e cite os editais que mostram as especificações técnicas. |
| **Persona relacionada** | Persona 2: Rafael Mota |
| **Rota correta esperada** | Estruturada + Agregada + Normativa |
| **Tipo** | Cross-source |

### Hipótese de falha

O sistema pode encontrar contratos que parecem semelhantes pelo texto, mas que tratam de veículos com características diferentes. Também precisa juntar dados de mais de uma base, separar órgãos estaduais e municipais, calcular a mediana e conferir os editais. Se algum desses passos falhar, a comparação pode ficar errada mesmo que o cálculo esteja certo.

### Comportamento esperado

O sistema deve comparar somente contratações que tenham especificações técnicas compatíveis, separar os contratos por esfera, calcular a mediana em cada grupo e mostrar quais documentos foram usados. Se não houver informação suficiente para fazer uma comparação segura, deve deixar isso claro.

# Resumo das perguntas

| ID | Persona | Tipo | Rota esperada |
|---|---|---|---|
| P1.1 | Persona 1: Helena Azevedo | Filtro | Estruturada |
| P1.2 | Persona 1: Helena Azevedo | Cross-source | Estruturada + Conversacional |
| P1.3 | Persona 1: Helena Azevedo | Recusa | Nenhuma base para concluir fraude. Aplicar recusa. |
| P2.1 | Persona 2: Rafael Mota | Factual | Estruturada |
| P2.2 | Persona 2: Rafael Mota | Cross-source | Estruturada + Agregada |
| P2.3 | Persona 2: Rafael Mota | Cross-source | Estruturada + Normativa |
| P3.1 | Persona 3: Carolina Nogueira | Vigência | Normativa |
| P3.2 | Persona 3: Carolina Nogueira | Cross-source | Estruturada + Normativa |
| P3.3 | Persona 3: Carolina Nogueira | Fora de escopo | Nenhuma base. Consulta fora do escopo do corpus. |
| P4.1 | Persona 4: Bruno Martins | Filtro | Estruturada |
| P4.2 | Persona 4: Bruno Martins | Cross-source | Estruturada + Agregada |
| P4.3 | Persona 4: Bruno Martins | Cross-source | Estruturada + Normativa |
| P5.1 | Persona 5: Patrícia Gomes | Filtro | Estruturada |
| P5.2 | Persona 5: Patrícia Gomes | Cross-source | Estruturada + Agregada |
| P5.3 | Persona 5: Patrícia Gomes | Cross-source | Estruturada + Agregada |
| P6.1 | Persona 6: Lucas Ferreira | Factual | Normativa |
| P6.2 | Persona 6: Lucas Ferreira | Filtro | Estruturada |
| P6.3 | Persona 6: Lucas Ferreira | Cross-source | Normativa + Estruturada |
| F1 | Falha esperada | Cross-source | Estruturada + Agregada + Normativa |

## Cobertura das perguntas

| Dimensão | Cobertura |
|---|---|
| **Tipos presentes** | Cross-source, Factual, Filtro, Fora de escopo, Recusa, Vigência |
| **Rotas presentes** | Normativa, Estruturada, Conversacional, Agregada e combinações entre bases |
| **Total de personas** | 6 |
| **Total de perguntas de referência** | 18 |
| **Pergunta de falha esperada** | 1 |
