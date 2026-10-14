# Roteiro do Demo Day — esboço inicial

Esboço criado na P8A (2026-10-13), conforme
[PLANO_DE_ACAO_DEMO_DAY.md](PLANO_DE_ACAO_DEMO_DAY.md). É a **primeira versão**,
ainda não ensaiada com tempos fechados. A versão final acompanha o ambiente
validado na P8B e o checkpoint de feature freeze, quando a equipe o definir.

Referência atual: backend `joao-reis` @ `435c9c0`, frontend `main` @ `91c0fbb`.
Este roteiro deve ser revisto a cada merge relevante.

## 1. Formato e restrições de exposição

- **Formato ainda não definido** (local, túnel ou hospedagem). Enquanto a
  política do histórico não for aprovada, a demo roda **local e isolada**, sem
  túnel nem publicação.
- `GET /api/v1/history` está público hoje. Isso **impede exposição pública** e
  precisa de decisão antes de qualquer formato aberto.
- Usar conversas de teste, não dados pessoais reais.
- A demo funciona com a **classificação de assuntos desligada** (marco P6
  independente). Não apresentar a classificação como entregue se ela não tiver
  sido aprovada e avaliada.

## 2. Abertura (≈2 min)

Falar, nesta ordem:

1. O problema: contratações públicas do RJ espalhadas em bases heterogêneas
   (contratos, editais/normas, atas, agregado), consulta difícil para quem não
   é especialista.
2. A ideia: RAG adaptativo que **roteia por natureza de evidência** — consulta
   estruturada (SQL) para número, base normativa para norma, vetorial para
   conceito — e **recusa quando a evidência não sustenta**.
3. O limite honesto: a base é uma **amostra**, não um censo do Estado.

Sem consultas nesta parte. Não prometer cobertura estadual.

## 3. Consultas ao vivo (≈8 min, 5 consultas)

Poucas consultas, cada uma mostrando um comportamento distinto. Confirmar
`session_id`, citação e `confidence_level` na tela a cada uma.

| # | Comportamento | Consulta sugerida | O que mostrar |
|---|---|---|---|
| 1 | Cálculo SQL | "Qual o valor total dos contratos assinados do Município de Maricá?" | Rota `estruturada`+`agregada`, número vindo do SQL, citação `[1]`, aviso "Esta amostra não é um censo do RJ" e "Campos ausentes não são zero" |
| 2 | Norma / ata | "O que estabelece a Lei 14.133/2021?" | Rota `normativa`, fonte com `origem` pública, sem forçar número |
| 3 | Guardrail | "Qual foi o valor pago ao fornecedor X?" | Distinção entre valor **contratado/informado** e **pagamento**; recusa ou delimitação explícita |
| 4 | Ausência de evidência | "Quais contratos a Infolog assinou com o Estado do Rio de Janeiro?" *(validada em 2026-10-14: responde "NENHUM registro encontrado…" no recorte, com confiança honesta)* | Zero **confiável no recorte** sem virar "não há contratos no RJ" |
| 5 | Continuação | "E qual fornecedor aparece nesses contratos?" | Mesma `session_id`, `pergunta_reformulada` autônoma, histórico enviado; depois nova conversa sem herdar sessão |

**Regra de ouro da apresentação:** se a resposta local for exata e o resultado
for zero, defender a **confiabilidade no recorte** e recusar a extrapolação
estadual. É o ponto mais diferenciado do projeto e o mais provável de ser
questionado.

## 4. Q&A com trace (≈3 min)

Mostrar o trace de uma consulta (por `X-Session-Id`, que funciona hoje) para
sustentar a defesa: etapas `query_analysis → roteamento → consulta_sql /
retrieval → fusao_rrf → rerank → geracao → validacao`, com `bases_roteadas`,
`sql_retornou_evidencia` e `confidence_level`.

- Hoje o frontend **não tem telas de trace**; se não houver tela na versão da
  demo, mostrar via `GET /api/v1/sessions/{session_id}/trace` com o
  `X-Session-Id` da conversa.
- Trazer impresso o trace já capturado, para não depender do ambiente.

## 5. Plano B

Nada de replay apresentado como execução ao vivo: **se usar gravação, dizer que
é gravação.**

Prioridade de queda, da mais para a menos segura:

1. **Endpoint ECOA fora do ar ou lento** (já observado: `APITimeoutError`).
   Resposta: mostrar que o sistema **degrada em vez de quebrar** —
   `fallback_used: true` para `openrouter/free` e `retrieval_denso` caindo para
   BM25, tudo registrado no trace como `status: degradado`. Isso é
   resiliência, não falha escondida; mostrar o registro de degradação.
2. **Banco/SQLite indisponível** — `check:backend` acusa "banco estruturado
   acessível"; se falhar, cair para a consulta normativa/vetorial e dizer o que
   ficou de fora.
3. **Sem rede** — usar as respostas/traces já gravados em `eval/runs/` e no
   histórico local, identificando-os como capturas prévias.
4. **Latência alta** (hoje 55–151 s por consulta real) — ter uma consulta em
   cache pronta (177 ms) para o primeiro contato e reservar as lentas para o
   momento em que a fala já tiver coberto o conteúdo.

Checklist de plano B (preparar antes do dia):

- [ ] Traces capturados das cinco consultas do roteiro
- [ ] Prints da tela com `session_id`, citações e `confidence_level`
- [ ] Registro de degradação da consulta mais lenta
- [ ] `npm run check:backend` rodado no momento da abertura
- [ ] Confirmação de que a porta/URL do backend bate com a do frontend

## 6. Repetir o smoke

Repetir este mesmo ensaio (P8A) **sempre que** mudar contrato de API, sessão,
timeout, histórico ou ambiente. O roteiro só é considerado pronto após o
ensaiado cronometrado da P8B.
