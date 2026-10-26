# Guia de execução local — NEXO frontend + backend RAG

Este guia parte de uma máquina Windows nova e coloca os dois projetos em
execução local nesta arquitetura:

```text
React/Vite — http://localhost:5173
              ↓ HTTP + JSON
FastAPI    — http://localhost:8080
              ↓
pipeline RAG + corpus v6 + PostgreSQL ou SQLite + histórico + feedback
```

Execute as etapas na ordem apresentada.

## 1. Pré-requisitos

Instale:

- Git;
- Python 3.12 de 64 bits;
- Node.js LTS com npm;
- PostgreSQL, apenas se quiser usar o banco local em vez do SQLite incluído no
  repositório.

Confirme no PowerShell:

```powershell
git --version
py -3.12 --version
node --version
npm.cmd --version
```

Python 3.12 é recomendado pela compatibilidade com FAISS, PyTorch e
`sentence-transformers`.

## 2. Criar uma pasta para os projetos

```powershell
New-Item -ItemType Directory -Force C:\projetos\nexo
Set-Location C:\projetos\nexo
```

Os repositórios ficarão lado a lado:

```text
C:\projetos\nexo\
├── rag-grande-desafio-grupo04-cenario01\
└── frontendRAG\
```

## 3. Clonar o backend

```powershell
git clone https://github.com/Diogooliveira10/rag-grande-desafio-grupo04-cenario01.git
Set-Location .\rag-grande-desafio-grupo04-cenario01
git switch main
git pull --ff-only origin main
```

Confirme:

```powershell
git status --short --branch
```

O resultado deve indicar `main` sincronizada e sem alterações.

## 4. Criar o ambiente Python do backend

Ainda na pasta do backend:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação, autorize scripts somente para a sessão
atual e tente novamente:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 5. Criar o `.env` do backend

```powershell
Copy-Item .env.example .env
```

Abra `.env` e confira pelo menos estas variáveis:

```env
OLLAMA_URL=http://ecoa-llm.duckdns.org:11434/v1
OLLAMA_API_KEY=ollama
GENERATION_MODEL=qcwind/qwen3-8b-instruct-Q4-K-M
EMBEDDING_MODEL=xagi/qwen3-embedding:0.6b-q4_k_m

RERANK_ENABLED=true
RERANK_MODEL=cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
RERANK_CANDIDATES=10
RERANK_MAX_LENGTH=512
```

O backend já aceita o frontend local (`http://localhost:5173` e
`http://127.0.0.1:5173`) sem configurar nada: `CORS_ALLOW_ORIGINS` só acrescenta
outras origens, como a do site publicado.

Não versione `.env`. Tokens e senhas devem existir somente nesse arquivo local.

## 6. Escolher o banco estruturado

O backend aceita duas opções. Escolha apenas uma.

### Opção A — PostgreSQL local

Crie o banco `grande_desafio`. Pelo `psql`, usando o usuário administrador:

```sql
CREATE DATABASE grande_desafio;
```

Se o usuário for `postgres` e a senha for `123456`, configure:

```env
DATABASE_URL=postgresql://postgres:123456@localhost:5432/grande_desafio
```

A estrutura da URI é:

```text
postgresql://USUARIO:SENHA@HOST:PORTA/BANCO
```

Não use:

```env
DATABASE_URL=postgresql://postgres123456:@localhost:5432/grande_desafio
```

Essa URI significa usuário `postgres123456` e senha vazia. Ela gera erros como
`fe_sendauth: no password supplied`, a menos que esse usuário específico exista
e o servidor permita autenticação sem senha.

Teste a conexão antes de iniciar a API:

```powershell
psql "postgresql://postgres:123456@localhost:5432/grande_desafio" -c "SELECT 1;"
```

Materialize as tabelas com os dados normalizados do projeto:

```powershell
python -m src.structured_db build
```

Confirme a quantidade de registros:

```powershell
python -m src.structured_db sql "SELECT COUNT(*) AS total FROM contratacoes"
```

### Opção B — SQLite local, sem PostgreSQL

Deixe a variável vazia:

```env
DATABASE_URL=
```

O backend usará automaticamente:

```text
data/structured/base_estruturada.sqlite
```

Essa é a opção mais simples para demonstração local. Não é necessário iniciar
um servidor de banco.

Se a base veio pronta do Drive, crie uma vez o índice do objeto (o `build` já
cria; sem ele as contas funcionam, só mais devagar):

```powershell
python -m src.structured_db fts
```

## 7. Iniciar o backend na porta 8080

Abra um PowerShell na pasta do backend:

```powershell
Set-Location C:\projetos\nexo\rag-grande-desafio-grupo04-cenario01
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.api:app --reload --host 127.0.0.1 --port 8080
```

Mantenha esse terminal aberto.

Teste no navegador:

- saúde: <http://localhost:8080/health>;
- documentação Swagger: <http://localhost:8080/docs>.

A saúde deve responder:

```json
{"status":"ok"}
```

Teste também os dados estruturados antes de abrir o frontend:

```powershell
Invoke-RestMethod "http://localhost:8080/api/v1/explore/contratacoes?limit=2"
Invoke-RestMethod "http://localhost:8080/api/v1/explore/orgaos?limit=2"
Invoke-RestMethod "http://localhost:8080/api/v1/project/summary"
```

Se `/health` funcionar, mas contratações retornarem erro 500, confira
`DATABASE_URL`. Esse comportamento normalmente significa que o PostgreSQL está
inacessível, sem a tabela carregada ou com usuário/senha incorretos.

## 8. Clonar o frontend

O frontend é mantido em um repositório separado:

<https://github.com/Etuarda/frontendRAG>

Para testar a integração local, é necessário clonar esse repositório e executar
o Vite no computador. O site publicado no GitHub Pages não substitui essa etapa,
pois este guia conecta o React local ao FastAPI local.

Abra outro PowerShell e execute:

```powershell
Set-Location C:\projetos\nexo
git clone https://github.com/Etuarda/frontendRAG.git
Set-Location .\frontendRAG
npm.cmd install
```

Depois do clone, consulte também o `README.md` e o guia de integração existentes
no próprio repositório do frontend. Eles descrevem a estrutura e os comandos
específicos da interface; em caso de divergência, mantenha neste teste a URL do
backend definida neste documento: `http://localhost:8080`.

## 9. Configurar a conexão do frontend

Crie `.env.development` na raiz do frontend:

```env
VITE_API_BASE_URL=http://localhost:8080
```

Não inclua `/api/v1/query` no final. O frontend acrescenta o caminho de cada
endpoint.

Se existir alguma configuração antiga de mocks, mantenha-a desligada:

```env
VITE_USE_MOCKS=false
```

Reinicie o Vite sempre que alterar um arquivo `.env*`, pois essas variáveis são
lidas na inicialização.

## 10. Iniciar o frontend na porta 5173

Na pasta do frontend:

```powershell
npm.cmd run dev -- --port 5173
```

Mantenha esse segundo terminal aberto e acesse:

<http://localhost:5173>

No rodapé, o estado esperado é `Backend online`.

## 11. Ordem diária para executar novamente

Depois da primeira instalação, use sempre esta ordem:

1. Inicie o PostgreSQL, caso tenha escolhido a opção A.
2. Inicie o backend na porta 8080.
3. Confirme `http://localhost:8080/health`.
4. Inicie o frontend na porta 5173.
5. Abra `http://localhost:5173`.
6. Faça a pergunta somente depois que aparecer `Backend online`.

Backend:

```powershell
Set-Location C:\projetos\nexo\rag-grande-desafio-grupo04-cenario01
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.api:app --reload --host 127.0.0.1 --port 8080
```

Frontend, em outro terminal:

```powershell
Set-Location C:\projetos\nexo\frontendRAG
npm.cmd run dev -- --port 5173
```

## 12. Perguntas para testar

Consulta estruturada:

```text
Quais contratos de software foram assinados em 2025 e quais foram os fornecedores?
```

Consulta normativa:

```text
O que a Lei 14.133/2021 estabelece sobre a fase preparatória da contratação?
```

Teste de recusa segura (dado bancário nunca é fornecido):

```text
Qual é o número da conta bancária e a agência do fornecedor que venceu o maior contrato de ambulâncias?
```

Uma pergunta fora do tema, como "Qual é a capital da França?", hoje é
respondida pelo modo de conversa (`CONVERSATION_MODE=llm`) em vez de recusada;
o defeito está nas pendências do [ARQUITETURA.md](../ARQUITETURA.md).

O modelo de rerank é baixado (na primeira vez) e carregado quando a API sobe, em
segundo plano (`RERANK_PRELOAD=true`, padrão); uma pergunta feita logo depois
de subir pode esperar esse carregamento. O frontend
atual espera até 240 segundos por uma consulta; se a primeira tentativa exceder
esse tempo durante o download do modelo, aguarde o backend terminar a preparação
e envie a pergunta novamente.

## 13. Validar histórico e feedback

Depois de fazer uma pergunta:

```powershell
Invoke-RestMethod "http://localhost:8080/api/v1/history?limit=5"
```

Copie o `query_id` devolvido pela consulta e envie um feedback:

```powershell
$body = @{
  query_id = "q_SUBSTITUA_PELO_ID_REAL"
  avaliacao = "positivo"
  comentario = "Resposta útil e fundamentada."
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://localhost:8080/api/v1/feedback" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))
```

## 14. Problemas comuns

### Backend online, mas Contratações ou Órgãos apresentam erro

Teste diretamente:

```powershell
Invoke-RestMethod "http://localhost:8080/api/v1/explore/contratacoes?limit=2"
```

- Se usar PostgreSQL, confira serviço, usuário, senha, banco e tabelas.
- Para usar o SQLite incluído, deixe `DATABASE_URL=` vazio e reinicie o backend.

### Frontend continua tentando a porta 8000

Confirme `.env.development`:

```env
VITE_API_BASE_URL=http://localhost:8080
```

Encerre o Vite com `Ctrl+C`, execute-o novamente e atualize o navegador com
`Ctrl+F5`.

### `Failed to fetch`, CORS ou backend offline

Teste `/health` antes do frontend. O backend já aceita `http://localhost:5173` e
`http://127.0.0.1:5173`; se o Vite estiver em outra porta, acrescente essa
origem em `CORS_ALLOW_ORIGINS` e reinicie o backend.

### A primeira pergunta demora

O reranker local baixa aproximadamente 470 MB na primeira utilização e carrega
quando a API sobe. Além disso, a geração depende do endpoint configurado em
`OLLAMA_URL`: no ECOA, uma resposta que passa pelo LLM leva de 20 s a alguns
minutos, e uma conta que o SQL responde sem o LLM sai em menos de 1 s.

### Porta ocupada

Descubra o processo:

```powershell
Get-NetTCPConnection -LocalPort 8080 -State Listen
Get-NetTCPConnection -LocalPort 5173 -State Listen
```

Encerre o terminal antigo antes de iniciar uma segunda instância.

## 15. Contrato completo

Consulte [FRONTEND_API_CONTRACT.md](FRONTEND_API_CONTRACT.md) para schemas,
parâmetros, respostas, erros e tipos TypeScript de todos os endpoints.
