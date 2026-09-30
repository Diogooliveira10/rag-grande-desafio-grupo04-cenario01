# Relatório de avaliação — golden set

- Run: `cli-v6`
- Gerado em: `2026-09-30T17:44:53.711138+00:00`
- Acertos: **13/22** (59%)

## Por tipo

| Tipo | Acertos | Total |
|---|---:|---:|
| cross_source | 6 | 12 |
| estruturada | 1 | 1 |
| factual | 2 | 2 |
| filtro | 2 | 4 |
| fora_de_escopo | 1 | 1 |
| recusa | 1 | 1 |
| vigencia | 0 | 1 |

## Casos

| ID | Tipo | Rota esperada | Rota obtida | Recusa esperada | Recusa obtida | Resultado |
|---|---|---|---|---|---|---|
| P1.1 | filtro | estruturada | estruturada | False | True | FALHOU |
| P1.2 | cross_source | conversacional, estruturada | conversacional, estruturada | False | False | OK |
| P1.3 | recusa | — | — | True | True | OK |
| P2.1 | factual | estruturada | estruturada | False | False | OK |
| P2.2 | cross_source | agregada, estruturada | agregada, estruturada | False | True | FALHOU |
| P2.3 | cross_source | estruturada, normativa | estruturada | False | True | FALHOU |
| P3.1 | vigencia | normativa | estruturada, normativa | False | False | FALHOU |
| P3.2 | cross_source | estruturada, normativa | estruturada, normativa | False | False | OK |
| P3.3 | fora_de_escopo | — | — | True | True | OK |
| P4.1 | filtro | estruturada | estruturada | False | False | OK |
| P4.2 | cross_source | agregada, estruturada | agregada, estruturada | False | True | FALHOU |
| P4.3 | cross_source | estruturada, normativa | estruturada, normativa | False | True | FALHOU |
| P5.1 | filtro | estruturada | agregada, estruturada | False | False | FALHOU |
| P5.2 | cross_source | agregada, estruturada | agregada, estruturada | False | False | OK |
| P5.3 | cross_source | agregada, estruturada | agregada, estruturada | False | False | OK |
| P6.1 | factual | normativa | normativa | False | False | OK |
| P6.2 | filtro | estruturada | estruturada | False | False | OK |
| P6.3 | cross_source | estruturada, normativa | estruturada, normativa | False | False | OK |
| F1 | cross_source | agregada, estruturada, normativa | agregada, estruturada | False | True | FALHOU |
| cenario1-marina | cross_source | agregada, estruturada | estruturada | False | True | FALHOU |
| cenario1-rodrigo | estruturada | estruturada | estruturada | False | False | OK |
| cenario1-claudia | cross_source | estruturada, normativa | estruturada, normativa | False | False | OK |
