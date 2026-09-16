# PERSONAS

Este documento descreve as personas de referência do sistema e as perguntas utilizadas para orientar e avaliar o comportamento do Adaptive RAG.


## Persona 1 — [Nome]

### Perfil

**Quem é:**
[Descrever profissão, função ou papel.]

**Situação de uso:**
[Explicar em que contexto essa pessoa utilizaria o sistema.]

**Necessidade:**
[O que ela precisa descobrir ou resolver.]

**Urgência:**
[Ex.: imediata, no mesmo dia, antes de uma reunião, durante elaboração de processo etc.]

### Perguntas de referência

#### P1.1 — [Título curto opcional]

**Pergunta:**

> [Pergunta feita pela persona]

**Rota esperada:**
[Normativa / Estruturada / Conversacional / Agregada / combinação de bases]

**Tipo:**
[Factual / Filtro / Cross-source / Vigência / Recusa / Fora de escopo]


#### P1.2 — [Título curto opcional]

**Pergunta:**

> [Pergunta feita pela persona]

**Rota esperada:**
[...]

**Tipo:**
[...]


#### P1.3 — [Título curto opcional]

**Pergunta:**

> [Pergunta feita pela persona]

**Rota esperada:**
[...]

**Tipo:**
[...]


## Persona 2 — [Nome]

### Perfil

**Quem é:**
[...]

**Situação de uso:**
[...]

**Necessidade:**
[...]

**Urgência:**
[...]

### Perguntas de referência

#### P2.1 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


#### P2.2 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


#### P2.3 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


## Persona 3 — [Nome]

### Perfil

**Quem é:**
[...]

**Situação de uso:**
[...]

**Necessidade:**
[...]

**Urgência:**
[...]

### Perguntas de referência

#### P3.1 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


#### P3.2 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


#### P3.3 — [Título curto opcional]

**Pergunta:**

> [...]

**Rota esperada:**
[...]

**Tipo:**
[...]


# Pergunta de falha esperada

**Pergunta:**

> [Pergunta que acreditamos que o sistema terá dificuldade para responder corretamente.]

**Persona relacionada:**
[Persona 1 / Persona 2 / Persona 3 / nenhuma específica]

**Rota correta esperada:**
[Base ou combinação de bases]

**Tipo:**
[Factual / Filtro / Cross-source / Vigência / Recusa / Fora de escopo]

### Hipótese de falha

[Explicar por que acreditamos que o sistema pode errar.]

Possíveis causas:

* confusão entre duas bases;
* necessidade de combinar múltiplas fontes;
* filtro numérico ou temporal;
* documentos semanticamente muito parecidos;
* informação contraditória;
* dificuldade de identificar a vigência correta;
* necessidade de recusa;
* ambiguidade na consulta.

### Comportamento esperado

[Descrever resumidamente o que seria considerado uma resposta correta.]


# Resumo das perguntas

| ID   | Persona        | Tipo  | Rota esperada |
| ---- | -------------- | ----- | ------------- |
| P1.1 | Persona 1      | [...] | [...]         |
| P1.2 | Persona 1      | [...] | [...]         |
| P1.3 | Persona 1      | [...] | [...]         |
| P2.1 | Persona 2      | [...] | [...]         |
| P2.2 | Persona 2      | [...] | [...]         |
| P2.3 | Persona 2      | [...] | [...]         |
| P3.1 | Persona 3      | [...] | [...]         |
| P3.2 | Persona 3      | [...] | [...]         |
| P3.3 | Persona 3      | [...] | [...]         |
| F1   | Falha esperada | [...] | [...]         |
