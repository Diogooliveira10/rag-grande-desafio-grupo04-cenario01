# Plano possível — corpus v7 (contratos do RJ de 2025)

> **Status: APROVADO PARA CONSTRUÇÃO EM PARALELO (2026-10-13).** As decisões
> da seção 7 foram tomadas; execução autorizada nos termos da seção 6 (v6
> ativa, v7 como artefato derivado, promoção só após re-auditoria + medição
> de qualidade). A **v6 continua sendo a base** do corpus, do eval e da demo.

**Atualização de execução (15/10/2026):** contratos e empenhos serão preservados
separadamente, e o cadastro será ampliado/reconciliado para o RJ. O modo direto
apresentou divergências de recorte e modalidades, documentadas em
[Coleta e recuperação v7](COLETA_DAS_BASES_E_RECUPERACAO_V7.md) e no
[relatório parcial](RELATORIO_RECUPERACAO_V7_2026-10-15.md). Os números abaixo são
a avaliação histórica, não a certificação da aquisição atual. Promoção só após
relatório final, rollback integrado validado e decisão explícita.

Data da avaliação: 2026-10-13. Medidas coletadas nesta data, descritas na seção 3.

---

## 1. Por que se pensou nisso

A v6 é uma **amostra deliberada**, não um censo. Toda resposta que depende de
contagem precisa declarar a limitação de cobertura:

> *"Escopo: amostra do PNCP/RJ registrada neste snapshot, não o universo de
> contratações do Estado."*

É a vulnerabilidade mais previsível de ser atacada na defesa. Um censo de 2025
enfraqueceria esse ataque — e, por outro lado, invalidaria o gabarito que foi
auditado sobre o snapshot atual (seção 5).

## 2. O que a v6 é (fatos, não impressão)

| Item | Valor | Fonte |
|---|---|---|
| Coleta | **completa** — `volume_alvo: 625`, `volume_obtido: 625`, `divergencia_alvo: 0` | `data/inventory/v6.json` |
| Limitação de projeto | `limite_por_orgao_por_base: 5` | `corpus_manifest.yaml:397` |
| Arquivos | 1.400 (625 de `contratos_estruturado`: 448 contratos + 177 publicações) | inventário v6 |
| Registros estruturados | 810 (455 contratos assinados + 355 publicações) | SQLite |
| Órgãos / municípios | 164 CNPJs / 56 municípios | SQLite |
| Vetores | 5.621, dimensão 1.024 | `data/embeddings/2026-09-30_12-08-51` |
| Esquema | `schema_version 6`, `source_sha256 d8a4f47f…827504` | tabela `meta` |

Ponto que costuma ser mal entendido: **a v6 não está incompleta**. Ela atingiu
exatamente o alvo de projeto. Passar para "todos os contratos" não é *terminar* a
v6 — é trocar de paradigma (amostra → censo).

Cada órgão da amostra tem exatamente 10 registros (5 por recurso × 2 recursos),
o que confirma que o limite foi aplicado como planejado.

## 3. Escala medida (somente 2025)

Medidos na API pública do PNCP
(`https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20251231&cnpjOrgao=…`)
em 2026-10-13, um órgão por chamada:

| Órgão | na amostra | **real 2025** |
|---|---|---|
| Tribunal de Justiça do Estado do RJ | 10 | **739** |
| Município de Maricá | — | **417** |
| Ministério Público do Estado do RJ | 10 | **263** |
| Município de Volta Redonda | 10 | **92** |
| Fundo Municipal de Saúde | 10 | **82** |
| Município de Piraí | 10 | **61** |
| Fundo Municipal de Saúde/SUS | 10 | **53** |
| Serviço Autônomo Hospitalar | 10 | **37** |
| Nova Friburgo Câmara Municipal | 10 | **15** |

9 órgãos → **1.759 contratos** reais contra **80** na amostra (**≈ 22×**).

**Estimativa para os 164 órgãos já amostrados: 25.000–35.000 contratos em 2025**
(média observada de 168–195 por órgão). Limites da estimativa, declarados:

- amostra de **9 órgãos**, com variância alta (15 a 739);
- órgãos do RJ **fora** da nossa amostra não entram nesse número;
- o endpoint nacional (`totalRegistros: 2.023.063`) **não** serve de base: os
  filtros `uf`/data não são aplicados sem `cnpjOrgao`, então ele traz todos os
  anos e estados.

Contra os 455 contratos assinados de hoje, isso é **~60×** — não 300×.

## 4. Custos estimados

Três cenários. O terceiro é o que torna o projeto confortável.

| | v6 (hoje) | v7 — chunks inteiros | v7 — **projeção compacta** |
|---|---|---|---|
| Bruto | 35 MB | ~1,2 GB | ~1,2 GB |
| Registros | 1.425 | ~50 mil | ~50 mil |
| Vetores | 5.621 | ~200 mil | **~50 mil** |
| Embeddings | 278 MB | **~10 GB** | **~2,5 GB** |
| Tempo de embedding | — | ~10 h | **~2,5 h** |
| Chamadas MiMo (classificação P6) | 455 | ~27.500 | ~27.500 |

**Projeção compacta:** embeddar só o campo `objeto`/descrição (1 vetor por
registro) em vez de chunks inteiros. É a mesma filosofia de *input mínimo* que
o plano de demo já aplica à classificação de assuntos. Reduz 4× o volume e 4× o
tempo. Precisa de verificação de qualidade (o índice de hoje foi validado com
sobreposição top-5 de 100% entre backends; a projeção nova exigiria medição
própria).

Sobre **"evitar o registro em disco"** — a intenção está certa, com um limite:

- **Dá para eliminar** a andaime: `raw` + `backups` + `inventory` = **390 MB**
  hoje (35 + 212 + 143). São artefatos intermediários, não o produto.
- **Não dá para eliminar** o índice vetorial (é o retrieval) e o SQLite
  (1,8 MB). O pipeline pode processar **direto** para esses dois artefatos
  finais, sem materializar os intermediários em disco.

## 5. O que isso resolve e o que quebra

**Resolve:** a limitação de cobertura, que hoje precisa ser declarada em toda
resposta baseada em contagem. É o ataque mais previsível da banca.

**Quebra — e é o ponto central:** o golden set está **calibrado no snapshot v6**.
Os critérios auditados em 2026-10-13 dependem de fatos que um censo muda:

| Caso | Conclusão v6 hoje | O que muda num censo |
|---|---|---|
| P1.1 | *"nenhum fornecedor tem ≥3 contratos de tecnologia"* | vira **uma lista** |
| P1.2 | premissa vazia, nada a verificar | passa a ter fornecedores |
| P4.1 | 3 contratos de manutenção de computadores | muda a contagem |
| P6.2 | 17 registros, **0 com modalidade** | muda a contagem |
| P5.1 / P5.2 | CRISTALIA, R$ 1.196.949.440,06, o maior | pode mudar o topo |
| P2.1 | 2 contratos da PROTEGER | pode mudar |

Cada `confianca_maxima`, cada conclusão de insuficiência e cada critério
numérico teriam de ser **re-auditados contra a base nova**. Isso é retrabalho de
avaliação, não de engenharia — e não tem como dimensionar sem começar.

Também se aplica a regra do plano de demo: *"Alterações de corpus/esquema após o
freeze exigem justificativa de correção, autorização, rollback e reavaliação dos
casos afetados"*. O feature freeze **ainda não tem data**.

## 6. Proposta: duas bases em paralelo

Como o custo com projeção compacta é baixo (~2,5 h de embedding), não é preciso
trocar de base de uma vez. Sugestão:

1. **v6 permanece** como base do eval, do golden set e da demo até a
   re-auditoria estar concluída.
2. **v7 é construída ao lado**, como artefato derivado com versão própria
   (mesma regra do plano: classificações e derivações têm versão, não se
   sobrescreve a origem).
3. Só se promove a v7 a base ativa **depois** de: re-auditoria do golden set
   sobre a base nova, medição de qualidade do retrieval com a projeção compacta,
   e decisão explícita da equipe.

Assim a v7 não entra no caminho crítico da demo e não invalida o que já foi
auditado.

## 7. Decisões necessárias antes de qualquer execução

> **Decididas em 2026-10-13.** Execução autorizada como construção em paralelo
> (§6). Registro da decisão em docs/registro_execucao_v6.md.

- [x] **Escopo do censo:** contratos assinados **+ publicações de compra** —
  preserva a semântica medida no P6 (modalidade vive nas publicações,
  fornecedor nos contratos). Volume ~2× (~50 mil registros).
- [x] **Projeção compacta: sim**, com duas salvaguardas: (a) o chunk armazenado
  e o BM25 continuam com o **texto completo do registro** — só o input do
  embedding vira objeto/descrição (contraste exigido pelo cenário: identificador
  no BM25, semântica no vetor — ver `grande-desafio.txt`, persona Rodrigo);
  (b) medição obrigatória da qualidade do retrieval antes de qualquer promoção.
- [x] **Re-auditoria do golden set aceita** como custo, com orçamento de tempo a
  definir pela equipe. Condição de promoção (§8), não bloqueia a construção.
- [x] **v6 permanece ativa** durante toda a construção da v7.
- [x] **Classificação P6 deferida** — a construção da v7 não roda inferência
  paga. A taxonomia entra depois, sobre a base pronta, com budget próprio.
- [x] **Formato dos artefatos:** o modo “direto para artefatos finais” (índice
  + SQLite, sem materializar raw/backups/inventory) será implementado como
  **modo separado**; o comportamento padrão do pipeline não muda.
- [ ] **Prazo e responsáveis.** Pendência administrativa da equipe.

Regra do desafio aplicável (grande-desafio.txt): *“a partir do fim do Sprint 1
o corpus está congelado”* — lido como o **cenário e as fontes** congelados; o
mesmo documento descreve `update` → `v(n+1)` → `DIFF` → **benchmark de
regressão** para atualizar a base em sprint seguinte. A v7 é construída como
**atualização versionada**: nunca sobrescreve a v6, gera DIFF e evidência de
regressão.

## 8. Condições para considerar o projeto pronto

- Cobertura: 100% dos contratos de 2025 dos órgãos alvo, com falhas declaradas.
- Proveniência: `source_sha256` e inventário por origem, como na v6.
- Golden set re-auditado contra a base nova, com os critérios que mudaram
  listados explicitamente.
- Qualidade do retrieval medida (não inferida) com a projeção compacta.
- Rollback possível: a v6 continua utilizável e é o fallback.
- Nenhum artefato da v6 sobrescrito.

## 9. Referências

- Corpus e inventário: `corpus_manifest.yaml` (limite por órgão por base),
  `data/inventory/v6.json`, [../CORPUS_INVENTORY.md](../CORPUS_INVENTORY.md).
- Estado do eval auditado: [registro_execucao_v6.md](registro_execucao_v6.md),
  seção "Auditoria do golden set concluída".
- Limitação de cobertura e a separação confiabilidade/completude/cobertura:
  fase P3 do plano de demo.

## 10. Possibilidade futura: evidência documental das atas na geração

Os registros de ata do censo trazem `numeroControlePNCPAta`, com CNPJ, compra,
ano e sequencial embutidos, e o PNCP expõe os arquivos em
`/orgaos/{cnpj}/compras/{ano}/{seq}/atas/{seqAta}/arquivos`. Uma evolução
possível é usar o PDF da ata como evidência adicional na geração, além do
registro JSON:

- **Fluxo**: registro citado na resposta → cache por ata (sha256; a primeira
  consulta paga o download, as seguintes reutilizam) → extração de texto pelo
  caminho de PDF/OCR já existente → trecho limitado como evidência extra
  dentro do orçamento de contexto.
- **Falhas**: download ou extração que falham respondem só com o registro JSON
  e registram `degradado`; a resposta nunca depende do PDF.
- **Configuração**: chave própria (`EVIDENCIA_PDF_ATAS=auto|off`) e testes
  offline com PDF mockado, seguindo o padrão de testes do projeto.
- **Custos aceitos**: latência por consulta (limitar às atas efetivamente
  citadas), chamadas externas por consulta (cache obrigatório) e **re-medida
  do golden set** com a evidência ativa — os critérios atuais foram calibrados
  só com o registro JSON, então o número do Demo Day não pode misturar os dois
  regimes.
- **Fora do recorte atual**: o censo guarda os registros; os PDFs continuam
  sob demanda (inclusive por comando de operador) enquanto a feature não sair.
