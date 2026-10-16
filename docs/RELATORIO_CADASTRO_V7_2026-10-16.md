# Cadastro oficial e reconciliação territorial — continuação do v7

Início em 15/10/2026 (data local); checkpoints abaixo em UTC de 16/10/2026.
**Relatório parcial, com aquisição cadastral ainda em execução. Não é promoção.**

Contexto: [coleta e estratégias](COLETA_DAS_BASES_E_RECUPERACAO_V7.md) e
[primeira recuperação](RELATORIO_RECUPERACAO_V7_2026-10-15.md).

## 1. Escopo autorizado

Prosseguir com a verificação oficial dos **524 CNPJs-semente**, reaproveitando
cache, reconciliar território e preparar janelas faltantes. Não iniciar embeddings,
reconstruir a v6, promover candidato ou tratar as sementes como todos os órgãos
existentes no RJ. Nenhum commit ou push realizado.

## 2. Lotes e execução em andamento

Raiz local: `data/v7_recuperacao/2026-10-15/` (ignorada pelo Git).

| Aquisição | Situação registrada |
|---|---|
| `piloto_unidades/` | Cinco órgãos previamente consultados |
| `unidades_lote_02/` | 25 novas consultas, 5 caches; 30 órgãos verificados, zero falhas; 1.950 unidades RJ |
| `unidades_lote_03/` | Cache dos 30 anteriores; teto de 494 novas consultas, intervalo mínimo de 5 s; em andamento |

A execução longa foi iniciada com PID **16672**, registrado em
`processo_cadastro_03.json`. O PID é referência do início, não prova permanente
de que o processo está vivo; verificar também processo e atualização dos arquivos.

- Checkpoint atômico após cada órgão: `unidades_lote_03/cadastro_parcial.json`.
- Resposta oficial de cada órgão: `unidades_lote_03/<CNPJ>.json`.
- Resultado final do lote: `unidades_lote_03/cadastro.json` (somente quando o
  comando terminar; ainda pode conter falhas/pendências).
- Log: `.tmp/v7-unidades-lote-03.log`.
- Estimativa inicial: cerca de 41 minutos mínimos para 494 chamadas a 5 s,
  acrescidos de latência, falhas e pausas. Não é garantia de término.

Não iniciar outro lote concorrente contra o mesmo host. Se houver interrupção,
reaproveitar os JSONs já concluídos em **novo destino**, sem apagar o lote anterior.

Um acompanhamento **offline** também foi iniciado (PID inicial **13628**) com
`src/acompanhar_cadastro_v7.py`. Ele aguarda até duas horas pelo encerramento do
lote e então reconcilia em `reconciliacao_pos_lote_03/`. Não faz chamadas HTTP,
não inicia coleta de contratos e não promove nada. Estado atômico em
`acompanhamento_cadastral_03.json`; log `.tmp/v7-acompanhar-cadastro-03.log`.
Estados possíveis: espera, reconciliação, conclusão, conclusão com pendências,
erro ou timeout. Se o coletor falhar/interromper, não há sucesso presumido.

## 3. Melhorias operacionais realizadas antes do lote

- Escrita JSON por temporário + troca atômica.
- Checkpoint cadastral periódico, inclusive para consulta interrompida.
- Preservação de `coletado_em` original ao reutilizar cache; reutilização recebe
  data própria e não se apresenta como nova consulta.
- Cache continua sendo processado mesmo depois de esgotado o orçamento de rede.
- Cadastro-semente vazio não recebe certificação de completude.
- Cooldown do host para HTTP 429/503, respeitando `Retry-After` em segundos ou data
  HTTP, com pausa mínima conservadora de 30 s.
- `PncpClient` preserva `retry_after` da resposta de erro para o chamador.
- Nenhum provedor de modelos é usado nessas operações.

## 4. Reconciliação offline congelada

Ferramenta: `src/reconciliar_unidades_v7.py`.
Saída utilizada neste relatório: `reconciliacao_cadastral_02/`.
Checkpoint congelado: **2026-10-16T00:04:56+00:00**.

| Indicador | Valor no checkpoint |
|---|---:|
| CNPJs-semente | 524 |
| Órgãos processados/verificados | 113 |
| Falhas cadastrais | 0 |
| Unidades oficiais, todas as UFs, desses órgãos | 6.913 |
| Unidades RJ elegíveis, sem conflito observado | 2.225 |
| Unidades RJ não observadas no snapshot anterior | 1.510 |

O job continua avançando: os números desta seção são de um **snapshot fechado**,
não devem ser confundidos com o progresso atual do processo. Na última conferência
do processo, às **2026-10-16T00:13:01+00:00**, já havia **210/524 órgãos verificados,
zero falhas e 2.414 unidades RJ**. A reconciliação desta seção continua congelada
nos 113 órgãos; o acompanhamento produzirá outro relatório após encerrar o lote.

### Unidades já observadas nos documentos

| Situação | Unidades |
|---|---:|
| UF RJ confirmada no cadastro consultado | 715 |
| UF fora do RJ confirmada no cadastro consultado | 1.079 |
| Órgão ainda não verificado nesse checkpoint | 1.425 |

O reconciliador também distingue unidade ausente no cadastro atual, UF cadastral
não informada, conflitos cadastrais e divergência entre snapshot e cadastro.
**Ausência no cadastro atual não prova ausência histórica nem localização fora do
RJ.** Nenhum desses casos pendentes é incluído automaticamente nas janelas RJ.

### Atas: identidades PNCP distintas, após seleção da revisão

| Situação cadastral | Atas |
|---|---:|
| Unidade RJ confirmada no cadastro consultado | 15.120 |
| Unidade fora do RJ confirmada no cadastro consultado | 51.339 |
| Órgão ainda não verificado nesse checkpoint | 43.410 |
| **Total examinado** | **109.869** |

Dentro das 51.339, **3.570 atas antes sem UF resolvida localmente** encontraram
unidade fora do RJ no cadastro oficial atual. Esse valor é um subconjunto, não
mais uma linha para somar ao total.

O cadastro consultado em 2026 não comprova sozinho a localização em cada data de
2025. A evidência territorial é marcada como atual; o histórico e eventuais
sub-rogações precisam ser considerados antes de promoção. A reconciliação não
reescreveu `candidato_03`, nem excluiu atas originais ou documentos de staging.

## 5. Plano conservador de janelas de contratos

`reconciliacao_cadastral_02/plano_janelas_contratos.json` contém **4.450 janelas**
para as unidades RJ já verificadas, com assinatura-alvo 2025 e publicação até
25/09/2026, divididas em intervalos de até 365 dias.

| Ação proposta | Janelas |
|---|---:|
| Auditar reaproveitamento do snapshot de publicação 2025 | 1.295 |
| Aquisição faltante, inclusive publicação em 2026 | 3.155 |

Isso **não significa** 4.450 chamadas, nem que as 1.295 devam ser recoletadas.
Uma janela pode ter várias páginas. A classificação antiga `completa` é somente
um indício para priorizar auditoria: não recebe baixa automática como se fosse
checkpoint compatível com a nova política.

Antes de aquisição ampla, revisar integridade/paginação das janelas antigas e
avaliar agrupamento por órgão versus consulta por unidade. Usar superset por CNPJ
pode reduzir chamadas, mas aumenta tráfego de outras UFs; seleção final precisa
continuar verificando UF, assinatura, tipo e janela de publicação. A estratégia
não pode inferir validade territorial pelo CNPJ.

O plano é **não executável automaticamente** e não autoriza promoção. Neste passo
não foram baixados novos contratos, empenhos ou atas, nem gerados embeddings.

## 6. Artefatos da reconciliação

- `cadastro_snapshot.json`: checkpoint efetivamente usado, independente do job que
  continua atualizando o arquivo de origem.
- `unidades_conferidas.jsonl`: classificação por par CNPJ/código de unidade.
- `atas_territorio.jsonl`: diagnóstico por identidade de ata, sem alterar o corpo.
- `unidades_RJ_elegiveis.json`: escopos sem conflitos; mantém o indicador de
  verificação parcial das sementes.
- `plano_janelas_contratos.json`: janelas e prioridade de auditoria/aquisição.
- `relatorio.json`: contagens, SHA-256 das fontes e das respostas cadastrais.

SHA-256 do checkpoint utilizado:
`d1ef90a81a9f62dfc532b4cb30d63bbea08111c53b398c0bcdfb7f7345354138`.

## 7. Validação e preservação

- **1.714 testes passaram em 75,61 s**, usando `-p tests.offline_guard`:
  DNS e sockets externos bloqueados no processo de testes, loopback permitido.
- Casos novos cobrem cache/data de origem, esgotamento do orçamento, cadastro
  vazio, `Retry-After`, conflito de UF, unidade ausente, ata sem UF, plano que
  não dá baixa automática em checkpoints antigos e acompanhamento que não
  converte timeout/arquivo não finalizado em sucesso.
- Baseline de ativos e aquisições originais novamente verificada:
  **íntegra, sem erros**, em `2026-10-16T00:02:09+00:00`.
- `git diff --check` sem erros (há avisos de conversão LF/CRLF já conhecidos).

## 8. Pendências e próximo checkpoint

1. Aguardar o término do lote ou tratar falhas/interrupções usando o cache.
2. Reexecutar a reconciliação em novo destino com `cadastro.json` final.
3. Auditar unidades ausentes/conflitos, localização histórica e sub-rogações.
4. Continuar descoberta/reconciliação institucional além das sementes: verificar
   524 CNPJs não prova que todos os órgãos do RJ foram descobertos.
5. Revisar custo, reaproveitamento e paginação antes da aquisição faltante.
6. Só depois completar bases/vínculos, índices, regressão, golden e rollback
   integrado. A promoção continua exigindo relatório final e decisão explícita.

Equivalente manual da reconciliação após o lote. **Já está agendado no
acompanhamento; não executar em paralelo no mesmo destino:**

```powershell
python -m src.reconciliar_unidades_v7 --observadas data/v7_recuperacao/2026-10-15/candidato_03/unidades_observadas.json --cadastro data/v7_recuperacao/2026-10-15/unidades_lote_03/cadastro.json --catalogo-aquisicao data/v7_recuperacao/2026-10-15/candidato_03/catalogo_aquisicao.sqlite --estado-contratos data/v7_direto/estado_coleta.json --destino data/v7_recuperacao/2026-10-15/reconciliacao_pos_lote_03
```
