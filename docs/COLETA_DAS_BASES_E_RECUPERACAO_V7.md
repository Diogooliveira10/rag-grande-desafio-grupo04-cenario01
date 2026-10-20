# Coleta das bases, desafios e recuperação do corpus v7

## 1. Status e decisões

Documento iniciado em 15/10/2026. Construção **em staging**, não promoção.
A autorização do usuário é executar o plano de recuperação e ampliar a cobertura,
**preservando contratos e empenhos separados**. A v6 continua ativa.

> **Atualização de 2026-10-19:** a v7 foi promovida e é a base ativa (decisão do
> grupo a pedido do tutor). Os dados da v7 ficam fora do Git; cada integrante
> baixa na própria máquina (README). O texto abaixo é o registro da construção
> em staging.

> **Promoção somente após relatório, rollback validado e decisão explícita.**
> Passar nos testes de código não equivale a provar cobertura ou qualidade do RAG.

Não fazer commits/push, sobrescrever o corpus ativo, apagar snapshots adquiridos,
publicar no Neon ou chamar modelos pagos nesta recuperação. Novas chamadas de
coleta são leituras de APIs públicas, com ritmo controlado e resultados separados.

Referências: [plano v7](PLANO_POSSIVEL_CORPUS_V7.md),
[registro histórico](registro_execucao_v6.md),
[qualidade da v6](QUALIDADE_DOS_DADOS.md),
[arquitetura](../ARQUITETURA.md).

## 2. O que cada base representa

| Base | Fonte e recorte | Estratégia | Cuidados |
|---|---|---|---|
| `contratos_estruturado` | PNCP: contratos/empenhos e publicações de compras | JSON por identidade PNCP; contratos selecionados pela assinatura e unidade; compras pela publicação | Tipo 2 não significa somente termo de contrato; compra e contrato não são dois fatos independentes para qualquer total |
| `atas_conversacional` | Atas PNCP; novas atas com `anoAta=2025` | Busca por vigência e órgão/unidade; deduplicação por número de controle | Ano da ata, assinatura, publicação e vigência são campos distintos; UF exige resolução da unidade |
| `editais_normativo` | Legislação oficial e documentos relacionados | Herdar v6 validada; PDF/HTML, extração e OCR quando necessário | Compilação atual não prova vigência histórica de cada dispositivo; manter URL e hash |
| `pca_agregado` | PCA, itens e indicadores PNCP | Herdar v6 inicialmente, mantendo a distinção plano/compra/contrato | Cobertura de PCA não é a de contratos; não converter registro vinculado em plano |
| `emails_sinteticos` | Conversas fictícias autorizadas pelo desafio | Herdar suplemento v6 com marcação explícita | Não prova fatos reais do PNCP; preservar sensibilidade, mascaramento e identificação sintética |

A nova seleção não apaga documentos históricos necessários ao golden, como atas
assinadas antes de 2025 e vigentes em 2025. A união com as bases herdadas deve
preservar sua identidade e declarar o papel de cada recorte.

## 3. Limitações verificadas da API

Verificação em 15/10/2026:

- `GET /api/consulta/v1/contratos` filtra por **publicação**, não assinatura.
- Contratos e atas aceitam `codigoUnidadeAdministrativa`, mas não filtro `uf`.
- `GET /api/consulta/v1/atas` busca por **período de vigência**.
- Contratos e atas rejeitaram período superior a 365 dias com **HTTP 422**.
- Página: contratos/atas até 500; publicações até 50 (mínimo documentado 10).
- 204 é janela vazia, não falha. 429 é limitação operacional: retentativa limitada,
  backoff e `Retry-After`. **12 requisições/minuto é referência operacional, não
  um limite oficial contratual confirmado.**
- Modalidade é campo da compra; o contrato fornece o vínculo, não a modalidade.
- Catálogo, unidades e registros podem mudar entre consultas. Guardar instante
  de aquisição, hash e versão. Uma consulta hoje não reconstrói automaticamente
  o estado histórico de uma data anterior.

Fontes oficiais:
- [OpenAPI Consulta](https://pncp.gov.br/api/consulta/v3/api-docs)
- [Swagger](https://pncp.gov.br/api/consulta/swagger-ui/index.html)
- [Catálogo de modalidades](https://pncp.gov.br/api/pncp/v1/modalidades)
- [Contrato/Empenho](https://pncp.gov.br/manual/pt-br/latest/contrato_empenho/consultar_contrato_ou_empenho.html)

## 4. Diagnóstico do snapshot adquirido

### 4.1 Contratos/empenhos

`data/v7_direto/documents.jsonl`: 168.051 identidades, sendo 110.378 registros
na família contratos/empenhos e 57.673 publicações. Comparação integral com o
SQLite não encontrou divergência de UF, assinatura, publicação, CNPJ principal
ou referência da compra. O SQL não inventou os valores: o recorte já estava
incorreto antes de carregá-lo.

| Família tipo 2 | Quantidade |
|---|---:|
| Total adquirido | 110.378 |
| Unidade fora do RJ | 53.328 |
| Assinatura fora de 2025 | 16.821 |
| RJ e assinatura em 2025 | 48.244 |
| Empenhos nesse recorte RJ/2025 | 31.814 |
| Não empenhos nesse recorte | 16.430 |
| Tipo oficial `Contrato (termo inicial)` nesse recorte | 15.524 |

Os conjuntos fora de UF/ano se sobrepõem. Não somá-los. Os 16.430 não empenhos
incluem outros instrumentos: não são todos termos iniciais de contrato.

Causas locais:
1. Coletor direto fixou publicação 01/01–31/12/2025 e não aplicou assinatura/UF.
2. Manifesto declarava assinatura 2025 e publicação até 25/09/2026, mas o modo
   direto não consumia esses parâmetros.
3. Descoberta do modo direto contornou as proteções do provider padrão.
4. SQL classificou qualquer identificador `-2-` como `contrato_assinado`.
5. Janelas de contratos abrangiam 164 CNPJs herdados. Publicações têm 511;
   347 CNPJs dessas publicações não tiveram janela de contratos.

A diferença entre CNPJ consultado e órgão principal em 105 registros corresponde
ao órgão sub-rogado. Não eliminar esses registros por igualdade simplista de CNPJ.

### 4.2 Atas

109.871 atas únicas com `anoAta=2025`, 1.044 janelas completas de aquisição.
Isso não comprova recorte territorial nem universo institucional completo.
Cruzamento por códigos de unidade com UFs observadas no snapshot:

| Evidência local | Quantidade |
|---|---:|
| Unidade observada no RJ | 32.920 |
| Unidade observada fora do RJ | 56.832 |
| UF ainda não resolvida | 20.119 |

O cadastro oficial confirmou unidade 179087 do Banco Central em Brasília/DF.
As 20.119 não resolvidas **não** devem ser tratadas automaticamente como RJ ou
como comprovadamente fora do RJ. O cruzamento local não substitui cadastro
oficial/histórico. Os 10.365 vetores de atas existentes são parciais, não índice
finalizado, e não devem ser sobrescritos para tentar retomar às cegas.

### 4.3 Modalidades omitidas

A lista fixa usada era `[1,3,4,5,6,7,8,9,11]`. O catálogo oficial tem outros
códigos. Consultas pontuais RJ/publicação 2025 retornaram totais de 522 para
credenciamento (12), 2 para leilão presencial (13), 48 para inaplicabilidade (14).
São 572 resultados informados pela API, **não 572 novos únicos já reconciliados**.
Catálogo atual inclui códigos criados posteriormente: registrar o domínio e
verificar cada janela, sem presumir aplicabilidade histórica pela lista atual.

## 5. Campos que não se confundem

- **Modalidade da aquisição:** `modalidadeId`/`modalidadeNome` da compra.
  Derivação por vínculo exato `numeroControlePncpCompra` (contratos) ou
  `numeroControlePNCPCompra` (atas), com proveniência. Ausência = não derivável
  com a evidência disponível, não "sem modalidade".
- **Categoria do processo:** `categoriaProcesso` do contrato, oficial.
- **Amparo legal:** objeto `amparoLegal` da compra. Não converter a categoria ou
  o artigo legal em modalidade por inferência; não atribuir ao contrato como
  campo original da sua fonte.
- **Tipo de instrumento:** `tipoContrato.id/nome`; contratos, empenhos, demais
  instrumentos e tipo não informado permanecem distinguíveis.

O join local era possível para 28.664 registros do snapshot amplo, mas ainda não
estava aplicado. No recorte RJ/assinatura 2025, 28.291 encontram compra local;
19.953 não encontram, correspondendo a 4.223 compras distintas ausentes
(incluindo empenhos). Os números mudarão após completar a aquisição.

## 6. Estratégia de modalidade e evidência auxiliar

1. Join local antes de chamadas de rede.
2. Completar publicações omitidas; refazer a fila deduplicada.
3. Para compras ausentes, consulta pontual por identidade:
   `GET /api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}`.
4. Guardar URL, hash, coleta e identificador retornado. Resposta de outra compra
   não vale. Campos faltantes não são preenchidos por adivinhação.
5. Compras fora de 2025 entram como **evidência auxiliar vinculada**, não como
   publicações adicionais do universo principal.
6. Falhas ficam pendentes com motivo, sem se converterem em ausência factual.

Exemplos testados, ambos HTTP 200:
- `00038166000105-1-000283/2024`: Pregão eletrônico, publicação 13/11/2024.
- `00394445000101-1-000041/2025`: Dispensa, publicação 25/12/2024.

Portanto, ano do identificador não é substituto da data de publicação.
Compras.gov.br é contingência já prevista pelo provider; cobertura desse fallback
precisa ser verificada. PDF/OCR é último recurso, não primeira escolha para um
campo estruturado que o vínculo oficial fornece.

## 7. Execução por fases e critérios

| Fase | Entrega | Critério |
|---|---|---|
| P0 | Baseline, cópia de rollback e hashes | Ativos e cópias íntegros; nenhum destino ativo escrito |
| P1 | Regras compartilhadas e configuração do coletor | UF, datas, tipos, paginação e checkpoint cobertos por testes |
| P2 | Reclassificação offline e SQL de staging | Texto/identidade preservados; inclusão/quarentena com motivos; categorias separadas |
| P3 | Complementação da aquisição | Cadastro reconciliado, catálogo versionado, janelas faltantes e falhas declaradas |
| P4 | Modalidades e referências auxiliares | Evidência por vínculo, fila única, cache e orçamento de requisições |
| P5 | Artefatos utilizáveis em staging | SQL/JSONL/FAISS coerentes, memória e retomada seguras, origem dos vetores auditada |
| P6 | Relatório e pacote de promoção | Regressão, golden re-auditado, rollback ensaiado e decisão explícita |

Não marcar P3 concluída só porque um piloto terminou. Não marcar P5 concluída
só porque existe SQL. Não marcar P6 concluída pelos testes unitários.

### Segurança operacional

- Destinos de recuperação devem ser novos, separados das fontes e ativos.
- Política/parametrização entram no fingerprint do checkpoint. Checkpoints antigos
  sem política não certificam a nova coleta.
- Janelas não podem ser marcadas completas se o teto de páginas foi alcançado.
- Preservar arquivo original em falha de escrita; temporário + troca atômica.
- Streaming no hash/reescrita e cautela com listas de vetores em RAM. Há histórico
  de `MemoryError` na deduplicação e montagem do FAISS.
- Mesmo nome de modelo base não prova identidade entre quantizações Q4/Q8. Não
  relabelar vetores retomados como se a origem tivesse sido medida.
- Separar completude técnica de janelas e cobertura institucional. Unidade sem UF
  ou cadastro faltante fica pendente; não inferir inexistência de contratos.

## 8. Ferramentas de execução

`src/recuperar_corpus_v7.py` é opt-in. Não tem comando de promoção.

```powershell
python -m src.recuperar_corpus_v7 baseline --destino data/v7_recuperacao/EXECUCAO/baseline
python -m src.recuperar_corpus_v7 staging --fontes data/v7_direto/documents.jsonl data/v7_direto_atas/documents.jsonl --destino data/v7_recuperacao/EXECUCAO/candidato_01
python -m src.recuperar_corpus_v7 verificar --baseline data/v7_recuperacao/EXECUCAO/baseline
```

`baseline` copia os ativos (sem credenciais ou histórico de usuários), verifica
cada cópia e registra hashes das aquisições. A cópia verificada ainda não é um
ensaio de restauração funcional.

`staging` cria catálogo em SQLite para trabalhar em streaming, seleciona por
identidade PNCP e revisão declarada, registra drift, enriquece somente extras
(não o texto original), produz `documents.jsonl`, `quarentena.jsonl`, SQL de
staging, unidades observadas e fila de compras ausentes. Seu SQL tem identificação
`v7-staging-1`, não é banco pronto para substituir o schema da aplicação.

**Cobertura de todas as bases e índice final não são produzidos por esse comando.**
A classificação territorial local é observacional e deve ser reconciliada na P3.
O comando `ensaio-rollback` restaura a cópia em sandbox e abre SQL/FAISS, mas não
sobe a aplicação. `alvos` une órgãos observados e cadastros-semente, sem perder
os órgãos que não tiveram resultados no snapshot.

`src/complementar_corpus_v7.py` executa lotes públicos e orçados (`publicacoes`,
`unidades`, `compras`), com `--max-consultas`, intervalo e destinos novos. Compras
e unidades aceitam `--cache` de execução anterior. As ferramentas não têm
comando de promoção e não chamam modelos de embedding/geração.

## 9. Promoção: bloqueios obrigatórios

- [ ] Cadastro de órgãos/unidades reconciliado e lacunas declaradas.
- [ ] Complementação de períodos/modalidades e vínculos avaliada.
- [ ] Bases herdadas incorporadas sem perda de evidências históricas.
- [ ] SQL e índices finais compatíveis com o contrato da aplicação.
- [ ] Proveniência e compatibilidade dos vetores comprovadas.
- [ ] Regressão de retrieval e golden atual (29 casos) re-auditados.
- [ ] Relatório final factual, com limites e resultados por dimensão.
- [ ] Rollback funcional ensaiado em destino isolado.
- [ ] Decisão explícita autorizando a promoção.

## 10. Registro desta execução

Resultados em [RELATORIO_RECUPERACAO_V7_2026-10-15.md](RELATORIO_RECUPERACAO_V7_2026-10-15.md).
Artefatos locais em `data/v7_recuperacao/2026-10-15/`, protegidos pelo `.gitignore`.

- Baseline com 33 arquivos ativos e cópias verificadas; integridade final mantida.
- Rollback de artefatos ensaiado em sandbox: 810 linhas SQL e 5.115 vetores.
  Ensaio integrado da aplicação ainda pendente.
- Reclassificação real em três candidatos independentes, sem embeddings.
- Dez códigos omitidos consultados: 572 novas publicações em três códigos e
  zero registros nos demais sete; não houve sincronização nova dos nove códigos
  originais. Ampliar cobertura não elimina automaticamente drift temporal.
- Cinco órgãos verificados oficialmente no piloto e dez compras vinculadas
  resolvidas. Cadastro e backfill completos ainda pendentes.
- Candidato atual: 15.561 contratos, 31.814 empenhos, 869 outros instrumentos,
  58.245 publicações e 32.919 atas com vínculo local RJ; 20.119 atas com UF pendente.
- Catálogo deduplica por identidade PNCP e audita duas revisões de atas; referências
  auxiliares não aumentam o universo principal.
- 1.703 testes aprovados na execução final com rede externa bloqueada. O relatório
  registra o incidente de um teste inicialmente desviado para OpenRouter pelo
  `.env`, posteriormente isolado.

**Continuação cadastral:** ver [cadastro e reconciliação territorial](RELATORIO_CADASTRO_V7_2026-10-16.md).
Um lote dos CNPJs-semente continua em segundo plano, com checkpoint por órgão;
um acompanhamento offline aguarda o término para gerar nova reconciliação e
plano de janelas, sem iniciar aquisição de contratos ou promoção. Na reconciliação
congelada de 113 órgãos, 3.570 atas sem UF local encontraram unidade fora do RJ no
cadastro atual; a validade histórica continua sendo uma ressalva. A suíte mais
recente tem 1.714 testes aprovados.

P0 e reclassificação inicial executados; P1 tem núcleo comum implementado, mas a
integração final de schema/filtros/API permanece pendente. Ver também
[consolidação atualizada](RELATORIO_EXECUCAO_V7_2026-10-16.md). P3/P4 tiveram pilotos;
P5/P6 ainda não estão concluídos. **Não é um pacote de promoção.**
