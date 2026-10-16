# Execução v7 — consolidação atualizada (16/10/2026)

**Status: candidato_05 em staging (candidato_04 + bases herdadas). V6 ativa. Sem promoção.**

Promoção só após relatório final, rollback integrado validado e decisão explícita.

## 1. Snapshot do candidato_04

Produzido em 16/10/2026 01:32:56 UTC, a partir de cinco fontes verificadas e
do laudo oficial de território para atas. Recorte: **RJ, ano-base 2025**.

| Tipo | Registros | Com modalidade |
|---|---:|---:|
| Contratos (termo inicial/carta) | 15.643 | 11.137 |
| Empenhos | 31.908 | 16.918 |
| Outros instrumentos | 870 | 560 |
| Publicações de compra | 58.245 | 58.245 |
| Atas com laudo territorial oficial | 32.956 | 26.553 |
| **Total de chunks principais** | **139.622** | **113.613** |

- **0 atas com UF pendente** (anteriormente 20.119).
- 3.570 atas antes sem UF local agora atribuídas a fora do RJ pelo cadastro;
  37 atribuídas a RJ. Evidência é cadastro consultado, não prova histórica.
- 177 contratos/empenhos novos do piloto de publicação em 2026 (assinatura 2025).
- 10 compras auxiliares não contabilizadas como publicações principais.
- 4.863 compras distintas pendentes para 26.209 vínculos.
- SQL staging: integridade `ok`, `v7-staging-1`.
- 1731 testes offline aprovados com rede externa bloqueada.
- Baseline de artefatos: íntegra, sem erros.

SHA-256 de `candidato_04/documents.jsonl`:
`4461b26a90ef645af29177f69d3c3ef9f9f5e70c347ef866a80bdbbbb53b6c3f`.

## 2. O que foi feito

| Etapa | Resultado |
|---|---|
| Preservação (P0) | Baseline verificada, rollback ensaiado, copias de rollback preservadas |
| Recorte comum (P1) | Funções puras, recorte configurável, classes de instrumento, fingerprint |
| Reclassificação (P2) | catálogo SQLite, quarentena, vínculos, SQL staging, território por laudo oficial |
| Cadastro (P3 concluída) | 524/524 CNPJs, 5.049 unidades RJ, zero falhas, 494 consultas HTTP |
| Publicações omitidas | 572 registros (3 códigos), 166 ganhos de modalidade por join |
| Modalidade (P4 parcial) | 10 compras auxiliares resolvidas; fila de 4.863 pendente |
| Complementação de contratos | Piloto de 177 instrumentos, com priorização de páginas parciais e cache |
| Atas com território | 32.956 com laudo cadastral oficial, 0 pendentes |
| Validação | 1.731 testes, baseline íntegra, SQL íntegro |

## 3. Complementação de contratos: andamento

O primeiro lote de 20 chamadas HTTP, com timeout de 90 s:
- 7 janelas completas, 2 falhas (timeout de rede), 1 parcial (500/2.805 registros).
- 177 registros exportados após seleção RJ/assinatura 2025.
- Todas as páginas consultadas ficaram em cache, mesmo de janelas não completas.
- Falhas HTTP antigas não impedem processar janelas novas.

**Complementação de contratos em andamento** (PID 28752, teto de 200 consultas,
3 h, retomada do cache). Último checkpoint: **38 janelas completas**,
**1.069 elegíveis**, **838 janelas restantes**, 82 consultas HTTP. Previsão:
~14 h para completar as 838 restantes.

Janelas restantes do plano: **838** (dos 878 iniciais).
Páginas já em cache: **11**. Cada janela pode ter múltiplas páginas.

## 4. O que não foi concluído

- Complementação completa de contratos/empenhos até 25/09/2026.
- Fila completa de compras pendentes.
- Incorporação das bases herdadas. ✅ (candidato_05, 2.468 chunks adicionados)
- FAISS candidato em escala.
- Proveniência Q4/ECOA × Q8/local e homogeneidade de vetores.
- Regressão de retrieval e re-auditoria do golden de 29 casos.
- Rollback integrado com aplicação funcionando.
- Relatório final e autorização explícita.

## 5. Ressalvas

- O cadastro é o snapshot PNCP consultado agora, não o cadastro histórico de 2025.
- A localização de atas resolve UF em 109.869/109.871 pelo cadastro oficial.
  A posição restante difere em código de unidade entre o laudo e o registro
  (um par, provavelmente sub-rogação).
- Os 2.670 contratos com snapshot legado precisam ser auditados antes de
  reaproveitar como se o checkpoint novo fosse.
- Snapshot legado aceita apenas quando as 164 janelas antigas foram
  verificadas contra hashes, parâmetros, identidades, paginação e
  contagens. Não bastava o `status: completa`.
- Cada chamada de aquisição é monitorada: URL, status, latência e
  timestamp. Interações com timeout/cache em lote podem ocorrer; a trava
  de execução registra PID e momento, e falhas de processo precisam ser
  identificadas com arquivos de auditoria, não presumidas.
- O plano de janelas usa superset por CNPJ. A seleção por registro
  continua verificando UF, assinatura, tipo, janela de publicação e
  identidade. A infra do PNCP não tem busca textual remota: os filtros
  de recorte são locais.

## 6. Próximos passos

1. Completar os 854 CNPJs faltantes do plano de aquisição (cache +
   retomada). Pode incluir várias páginas por CNPJ.
2. Refinar a fila de compras pendentes e resolver por lote controlado.
3. Incorporar bases herdadas.
4. Construir e auditar FAISS.
5. Executar regressão e re-auditar os 29 casos do golden.
6. Ensaio integrado de rollback.
7. Relatório final e decisão explícita de promoção.

## 7. Status e operações em andamento

**candidato_05** incorporou as bases herdadas (1.557 normativas + 911 PCA
agregado), preservando texto, hashes e identidades. 141.090 chunks no
documents.jsonl; SQL staging-2 com 106.666 linhas de contratações.

**Complementação de contratos** retomada em segundo plano (PID 28752,
teto de 200 consultas, 3 h). Último checkpoint: **22 janelas completas,
1.020 elegíveis, 854 janelas restantes**, 66 consultas HTTP. O tempo
médio por janela é de ~60 s. Com 854 restantes, a previsão é ~14 h,
sujeita a interrupções e retomada. Os cache pages do primeiro piloto
estão sendo reaproveitados.

A **construção do FAISS** para staging é a peça que falta para regressão
e golden: depende de embeddings, que não foram iniciados nesta sessão.
O servidor llama.cpp está disponível (PID 31252, desde 14/10). O
heartbeat da sessão continuou vivo; a execução pode ser reiniciada
a qualquer momento.

A suíte de testes (**1.731 testes, rede externa bloqueada**) cobre as
novas ferramentas de auditoria, aquisição, congelamento de snapshot,
desvio de UF/assinatura, cache, retomada, trava e falhas.

**candidato_05** incorporou as bases herdadas (1.557 normativas + 911 PCA
agregado), preservando texto, hashes e identidades. 141.090 chunks no
documents.jsonl; SQL staging-2 com 106.666 linhas de contratações.

**Complementação de contratos** retomada em segundo plano (PID 28752,
teto de 200 consultas, 3 h). Até a última conferência: 15 janelas
completas, 886 elegíveis, 860 restantes. As páginas do primeiro piloto
foram reaproveitadas do cache. Falhas de timeout permanecem registradas
para nova tentativa em lote seguinte.

A **construção do FAISS** para staging é a peça que falta para regressão
e golden: depende de embeddings, que não foram iniciados nesta sessão.
O servidor llama.cpp está disponível (PID 31252, desde 14/10). O
heartbeat da sessão continuou vivo; a execução pode ser reiniciada
a qualquer momento.

A suíte de testes (**1.731 testes, rede externa bloqueada**) cobre as
novas ferramentas de auditoria, aquisição, congelamento de snapshot,
desvio de UF/assinatura, cache, retomada, trava e falhas.