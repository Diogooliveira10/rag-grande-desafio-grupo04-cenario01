# Nexo RJ — Frontend Adaptive RAG (Contratações Públicas RJ)

Interface web completa e moderna do cenário de contratações públicas com Adaptive RAG para o Estado do Rio de Janeiro.

O projeto foi estruturado seguindo os princípios de **Clean Code**, **Separação de Preocupações (SoC)** e **Arquitetura Orientada a Features (FSD)**:

- **Aplicação React 19 + TypeScript + Vite**: Single Page Application (SPA) reativa com 8 módulos integrados, mock configurável e navegação fluida.

---

## Identidade Visual e Diretrizes de Design (Design System)

- **Paleta Institucional de Alto Contraste:** Fundo limpo neutro em tom de papel perolado (`#f7f6f2`), tipografia grafite profundo (`#111111`, `#33312e`) com taxa de contraste WCAG AAA, e acentos em verde escuro institucional (`#1e4b22`).
- **Tipografia:** Títulos institucionais com peso editorial sólido combinados a sans-serif moderno de alta legibilidade para UI e dados.
- **Padrão Profissional:** Sem artifícios gerados por IA (sem brilhos ambientais, sem cantos exagerados, sem gradientes chamativos). Status e tags apresentados via tipografia limpa e indicadores em ponto (`.dot-indicator`), sem fundos coloridos atrás de textos.
- **Arquitetura Mobile-First:** Experiência pensada primariamente para smartphones e telas menores, com alvos de toque ergonômicos (>= 44px), navegação em drawer lateral com overlay, e aprimoramento progressivo para tablets e desktops.

---

## Arquitetura de Pastas e Arquivos

```text
frontend-rag/
├── src/                               # Aplicação React 19 + TypeScript
│   ├── app/                          # Composição da aplicação e orquestração de visões
│   │   └── App.tsx
│   ├── domain/rag/                   # Contratos de domínio, tipagens e catálogo de dados
│   │   ├── types.ts                  # Interfaces fortes (RagResponse, EvidenceNature, etc.)
│   │   └── catalog.ts                # Definições das bases, perguntas e dados do Cenário 1
│   ├── features/                     # Módulos verticais por funcionalidade
│   │   ├── query/                    # Consulta, composer, loading e serviço HTTP
│   │   ├── results/                  # Apresentação da resposta, badges e fontes
│   │   ├── history/                  # Histórico em tela cheia e sidebar retrátil
│   │   ├── corpus/                   # Inventário, contagem de chunks e documentos
│   │   ├── sources/                  # Catálogo de fontes oficiais e links externos
│   │   ├── curation/                 # Manifesto de curadoria e regras de sensibilidade
│   │   ├── pipeline/                 # Gatilho de atualização, chunk size e embeddings
│   │   ├── evaluation/               # Métricas (Faithfulness, Relevancy) e Golden Set
│   │   ├── observability/            # Traces de execução (run_id/query_id), latência e tokens
│   │   ├── knowledge/                # Modal descritivo das 4 bases de conhecimento
│   │   └── guardrails/               # Modal de limites legais e motivos de recusa
│   ├── shared/                       # Componentes e utilitários reutilizáveis
│   │   ├── components/               # Header, Icon (SVG inline), Modal
│   │   ├── config/                   # Configurações gerais da aplicação
│   │   └── utils/                    # Formatadores de data e texto
│   ├── mocks/                        # Respostas simuladas realistas
│   │   └── rag.mock.ts
│   ├── styles/                       # CSS global e design tokens da SPA
│   │   └── global.css
│   └── main.tsx                      # Ponto de montagem React
│
├── .env                              # Variáveis de ambiente locais
├── .env.example
├── package.json
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
└── vite.config.ts
```

---

## Como Executar

### 1. Aplicação React (SPA Interativa)

Certifique-se de ter o Node.js instalado (v18+).

```bash
# A partir da raiz do repositório
cd frontend
npm ci

# Iniciar o servidor de desenvolvimento
npm run dev
```

Abra [http://localhost:5173](http://localhost:5173) no seu navegador.

#### Variáveis de Ambiente (`.env`)

```env
# No desenvolvimento local, deixe vazio para usar o proxy do Vite
VITE_API_BASE_URL=

# False para consultar o pipeline real
VITE_USE_MOCKS=false
```

Execute o backend em outro terminal, a partir da raiz do repositório:

```bash
uvicorn app.api:app --reload --host 127.0.0.1 --port 8000
```

O Vite encaminha `/health` e `/api/*` para o backend local. Na publicação do
GitHub Pages, configure a variável de repositório `VITE_API_BASE_URL` com a URL
HTTPS pública da API. O GitHub Pages não consegue acessar um backend disponível
somente em `localhost`.

---

## Princípios de Clean Code Aplicados

1. **Responsabilidade Única (SRP):** Cada componente React ou arquivo CSS cuida de uma única responsabilidade visual ou de negócio.
2. **Desacoplamento de Domínio:** As entidades e contratos em `src/domain/rag/types.ts` não dependem de bibliotecas visuais.
3. **Isolamento de Efeitos Colaterais:** A comunicação com o backend FastAPI fica estritamente encapsulada em `rag.service.ts`.
4. **Resiliência e Mocks Integrados:** O sistema funciona perfeitamente sem o backend ativo quando `VITE_USE_MOCKS=true`, facilitando testes de interface e validação de layout.
