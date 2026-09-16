# Fontes do Cenário 1 — Contratações Públicas do RJ

| Base do desafio    | O que entra                                | Fonte externa concreta                    | API / URL específica                                                                                                                                    | Tipo de acesso                   | O que fica local depois                     | O que ainda precisamos definir                                                                       |
| ------------------ | ------------------------------------------ | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| **Estruturada**    | Contratações/licitações do RJ              | **PNCP – API pública de consulta**        | `GET /api/consulta/v1/contratacoes/publicacao?dataInicial=20250101&dataFinal=20251231&codigoModalidadeContratacao={id}&uf=RJ&pagina=1&tamanhoPagina=50` | REST → JSON                      | JSON/JSONL com contratações                 | período final, modalidades e quantidade                                                              |
| **Estruturada**    | Contratos/empenhos celebrados              | PNCP                                      | `GET /api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20251231&pagina=1`                                                                       | REST → JSON                      | contratos normalizados                      | como restringir ao universo RJ; o endpoint de contratos não usa `uf` da mesma forma que contratações |
| **Estruturada**    | Detalhe de uma contratação encontrada      | PNCP                                      | `GET /api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}`                                                                                         | REST → JSON                      | registro enriquecido                        | quais campos serão preservados                                                                       |
| **Normativa**      | Lei geral de licitações                    | **Planalto**                              | [Lei 14.133/2021 — Planalto](https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm?utm_source=chatgpt.com)                            | HTML                             | HTML/Markdown/texto processado              | nenhuma quanto à Lei 14.133; já é uma boa fonte fixa                                                 |
| **Normativa**      | Regulamentação estadual do RJ              | **REDELOG / Governo do RJ**               | [Legislação de Licitações — REDELOG RJ](https://redelog.rj.gov.br/redelog/legislacao-licitacoes/?utm_source=chatgpt.com)                                | HTML / documentos                | textos normativos locais                    | quais decretos/resoluções entram                                                                     |
| **Normativa**      | Decretos estaduais relacionados            | Governo do RJ / CGE                       | [Decretos — CGE-RJ](https://cge.rj.gov.br/legislacao/decretos/?utm_source=chatgpt.com)                                                                  | HTML/PDF                         | documentos locais                           | selecionar os relevantes ao benchmark                                                                |
| **Normativa**      | Editais reais de contratações              | PNCP                                      | primeiro encontra a compra pela Consulta API; depois documentos em `GET /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/arquivos`                 | API → metadados + URI de arquivo | PDFs dos editais                            | lista determinística de editais                                                                      |
| **Conversacional** | Atas de registro de preços                 | PNCP                                      | `GET /api/consulta/v1/atas?dataInicial={AAAAMMDD}&dataFinal={AAAAMMDD}&pagina=1`                                                                        | REST → JSON                      | atas/metadados locais                       | período e CNPJs/órgãos do RJ                                                                         |
| **Conversacional** | Documentos/texto das atas                  | PNCP                                      | `GET /api/pncp/v1/orgaos/{cnpj}/compras/{anoCompra}/{seqCompra}/atas/{seqAta}/arquivos`                                                                 | API + arquivo                    | PDF/texto extraído                          | quais atas entram                                                                                    |
| **Conversacional** | E-mails de compras                         | **base sintética fornecida pelo desafio** | **não há URL pública indicada**                                                                                                                         | arquivo local / sintético        | TXT/MD/JSONL                                | localizar/receber a base fornecida                                                                   |
| **Agregada**       | PCA — Plano de Contratações Anual          | PNCP                                      | `GET /api/consulta/v1/pca/?anoPca={ano}&codigoClassificacaoSuperior={id}&pagina=1`                                                                      | REST → JSON                      | PCA local                                   | ano e classificações que serão coletadas                                                             |
| **Agregada**       | PCA de determinado órgão                   | PNCP                                      | `GET /api/pncp/v1/orgaos/{cnpj}/pca/{ano}/consolidado`                                                                                                  | REST → JSON                      | totais por órgão                            | órgãos/CNPJs selecionados                                                                            |
| **Agregada**       | Itens do PCA                               | PNCP                                      | `GET /api/pncp/v1/orgaos/{cnpj}/pca/{ano}/{sequencial}/itens/contratacao`                                                                               | REST → JSON                      | itens para agregação                        | recorte                                                                                              |
| **Agregada**       | Valores e fornecedores vencedores por item | PNCP                                      | `GET /api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{sequencial}/itens/{numeroItem}/resultados`                                                               | REST → JSON                      | valores homologados; fornecedor; quantidade | quais dimensões vamos agregar                                                                        |
| **Agregada**       | Totais, médias, séries                     | **calculado pelo nosso pipeline**         | sem API própria                                                                                                                                         | processamento local              | CSV/Parquet/SQLite etc.                     | definir agregações necessárias às personas                                                           |

## Critério de disponibilidade

Os endpoints parametrizados serão testados com os valores-padrão definidos em `testes/testar_fontes_cenario01.py`. A verificação registra data/hora UTC, status HTTP, tipo de conteúdo, tamanho da resposta e uma prévia limitada do corpo. A disponibilidade é separada da qualidade ou completude dos dados.

As linhas sem URL pública (base sintética e processamento local) são marcadas como `não aplicável` pelo script, e não como falha de rede.

## Organização dos resultados

- Resumos JSON: `data/runs/<timestamp>/`.
- Corpus bruto compartilhado: `data/raw/v1/arquivos/html/`, `data/raw/v1/arquivos/pdf/`, `data/raw/v1/arquivos/json/`, `data/raw/v1/arquivos/jsonl/` e `data/raw/v1/arquivos/pncp/`.
- O script compara SHA-256 antes de salvar; conteúdo repetido é registrado como `reutilizado` e não cria cópia.
- HTML, HTM, ASPX, PDF, ZIP, JSON e JSONL são identificados por extensão, Content-Type e assinatura do conteúdo quando necessário.
- As respostas JSON das APIs são preservadas em `json/`; quando possuem uma lista de registros, também são materializadas em uma linha por registro em `jsonl/`.

## Resultados da última verificação

Última execução: `2026-09-16T19:19:17+00:00`.

| Fonte/teste | Disponibilidade | HTTP | Tentativas | Content-Type | URL testada | Observação |
| --- | --- | ---: | ---: | --- | --- | --- |
| PNCP — contratações/publicação | disponivel | 200 | 1 | application/json | https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao?dataInicial=20250101&dataFinal=20250131&codigoModalidadeContratacao=1&uf=RJ&pagina=1&tamanhoPagina=10 | {"data": [{"srp": false, "orgaoEntidade": {"cnpj": "00394460005887", "razaoSocial": "MINISTERIO DA ECONOMIA", "poderId": "E", "esferaId": "F"}, "anoCompra": 2025, "sequencialCompra": 1, "dataInclusao": "2025-01-28T15:29:47", "dataPublicacao |
| PNCP — contratos | disponivel | 200 | 1 | application/json | https://pncp.gov.br/api/consulta/v1/contratos?dataInicial=20250101&dataFinal=20250131&pagina=1&tamanhoPagina=10 | {"data": [{"numeroControlePncpCompra": "00394460005887-1-000067/2024", "numeroControlePncpAta": null, "codigoPaisFornecedor": null, "anoContrato": 2024, "tipoContrato": {"id": 1, "nome": "Contrato (termo inicial)"}, "numeroContratoEmpenho": |
| PNCP — detalhe da contratação encontrada | erro_http | 301 | 1 | application/json | https://pncp.gov.br/api/pncp/v1/orgaos/00394460005887/compras/2025/1 | Moved Permanently |
| PNCP — arquivos/editais da contratação | disponivel | 200 | 1 | application/json | https://pncp.gov.br/api/pncp/v1/orgaos/00394460005887/compras/2025/1/arquivos | [{"uri": "https://pncp.gov.br/pncp-api/v1/orgaos/00394460005887/compras/2025/1/arquivos/1", "url": "https://pncp.gov.br/pncp-api/v1/orgaos/00394460005887/compras/2025/1/arquivos/1", "tipoDocumentoNome": "Edital", "tipoDocumentoDescricao": " |
| Planalto — Lei 14.133/2021 | disponivel | 200 | 1 | text/html | https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm | <html xmlns="http://www.w3.org/TR/REC-html40" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:v="urn:schemas-microsoft-com:vml">  <head>   <meta content="Microsoft FrontPage 6.0" name="GENERATOR"/>   <title>    L14133   </title>   < |
| REDELOG RJ — legislação de licitações | disponivel | 200 | 1 | text/html; charset=UTF-8 | https://redelog.rj.gov.br/redelog/legislacao-licitacoes/ | <!DOCTYPE html> <html lang="pt-BR"> <head> <meta charset="UTF-8"> <link rel="profile" href="http://gmpg.org/xfn/11"> <link rel="pingback" href="https://redelog.rj.gov.br/redelog/xmlrpc.php"> <title>Rede Logística - Redelog</title> <meta nam |
| CGE-RJ — decretos estaduais RJ | disponivel | 200 | 1 | text/html; charset=UTF-8 | https://cge.rj.gov.br/legislacao/decretos/ | <!DOCTYPE html> <html lang="pt-br">  <head>     <meta charset="UTF-8" />   <meta name="viewport" content="width=device-width, initial-scale=1.0" />        <!-- coisas normais, favicon, fontes, css etc -->   <link rel="shortcut icon" href="h |
| PNCP — documentação OpenAPI v3 | disponivel | 200 | 1 | application/json | https://pncp.gov.br/api/consulta/v3/api-docs | {"openapi": "3.0.1", "info": {"title": "API PNCP CONSULTA", "description": "API REST de serviços do Portal Nacional de Contratações Públicas (PNCP)", "contact": {"name": "Serviço Federal de Processamento de Dados - Serpro", "url": "https:// |
| PNCP — Swagger UI | disponivel | 200 | 1 | text/html | https://pncp.gov.br/api/consulta/swagger-ui/index.html#/ | <!-- HTML for static distribution bundle build --> <!DOCTYPE html> <html lang="en">   <head>     <meta charset="UTF-8">     <title>Swagger UI</title>     <link rel="stylesheet" type="text/css" href="./swagger-ui.css" />     <link rel="style |
| CATMAT | disponivel | 200 | 1 | text/html; charset=utf-8 | https://catmat.com.br/ | <!doctype html> <html lang="pt-BR" data-theme=""> <head>   <meta charset="UTF-8"/> <link rel="icon" type="image/svg+xml" href="/favicon.svg"/> <link rel="icon" type="image/x-icon" href="/images/favicon.ico"/> <link rel="icon" type="image/pn |
| Gist — MCP Compras | disponivel | 200 | 1 | text/html; charset=utf-8 | https://gist.github.com/Micael106/04a3e5515057ab11ea8797603682f0bd | <!DOCTYPE html> <html   lang="en"      data-color-mode="auto" data-light-theme="light" data-dark-theme="dark"   data-a11y-animated-images="system" data-a11y-link-underlines="true"      >       <head>     <meta charset="utf-8">   <link rel=" |
| Glama — MCP Compras PNCP PCA | disponivel | 200 | 1 | text/html; charset=utf-8 | https://glama.ai/mcp/servers/opedrosoares/MCP_Compras/tools/compras_pncp_pca_por_classificacao_superior | <!DOCTYPE html><html lang="en-US"><head><meta charSet="utf-8"/><meta content="width=device-width, initial-scale=1" name="viewport"/><link rel="preload" as="image" href="/avatars/opedrosoares.avif?size=200"/><link as="font" crossorigin="anon |
| Diário Oficial da União — publicação da Lei 14.133 | disponivel | 200 | 1 | text/html;charset=ISO-8859-1 | http://pesquisa.in.gov.br/imprensa/jsp/visualiza/index.jsp?jornal=515&pagina=1&data=01/04/2021 | <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
 
 
 
 
 <html>
 <head>
   <meta http-equiv="Content-Type" content="text/html; charset=ISO-8859-1">
   <meta name="description" content="Sum�rio dispon�vel no SGPUB">
 	  
 			 |
| MPPI — PDF da Lei 14.133/2021 | disponivel | 200 | 1 | application/pdf | https://www.mppi.mp.br/internet/wp-content/uploads/2021/04/L14133.pdf | %PDF-1.4 %���� 1 0 obj <</Creator (Mozilla/5.0 \(Windows NT 10.0; Win64; x64\) AppleWebKit/537.36 \(KHTML, like Gecko\) Chrome/89.0.4389.128 Safari/537.36) /Producer (Skia/PDF m89) /CreationDate (D:20210420174412+00'00') /ModDate (D:2021042 |
| PNCP — atas | indisponivel | — | 1 | — | https://pncp.gov.br/api/consulta/v1/atas?dataInicial=20250101&dataFinal=20250131&pagina=1&tamanhoPagina=10 | The read operation timed out |
| PNCP — arquivos das atas | bloqueado_por_fonte | — |  | — | parametrizada | não testado porque a consulta de atas está temporariamente indisponível |
| Base sintética — e-mails de compras | nao_aplicavel | — |  | — | não há URL pública indicada | fonte local/sintética; localizar a base fornecida pelo desafio |
| PNCP — consulta de PCA | erro_requisicao | 404 | 1 | application/json | https://pncp.gov.br/api/consulta/v1/pca?anoPca=2025&pagina=1&tamanhoPagina=10&codigoClassificacaoSuperior=979 | {"timestamp": "2026-09-16T19:19:42.355+00:00", "status": 404, "error": "Not Found", "message": "No message available", "path": "/pncp-consulta/v1/pca"} |
| PNCP — PCA consolidado por órgão | bloqueado_por_fonte | — |  | — | parametrizada | não testado porque a consulta de PCA não forneceu os identificadores necessários |
| PNCP — itens do PCA | bloqueado_por_fonte | — |  | — | parametrizada | não testado porque a consulta de PCA não forneceu os identificadores necessários |
| PNCP — resultados do item 1 | disponivel | 204 | 1 | application/json | https://pncp.gov.br/api/pncp/v1/orgaos/00394460005887/compras/2025/1/itens/1/resultados | resposta sem conteúdo |
| Pipeline — totais, médias e séries | nao_aplicavel | — |  | — | sem API própria | processamento local; disponibilidade depende da implementação do pipeline |

Estados principais: `disponivel` = resposta válida; `indisponivel_temporariamente` = 429/5xx após retries; `erro_requisicao` = 4xx não transitório; `bloqueado_por_fonte` = endpoint encadeado não pôde ser testado; `nao_aplicavel` = fonte local; `indisponivel` = falha de rede/timeout.

Para repetir sem alterar este arquivo, execute `py .\testes\test-fontes\testar_fontes.py --atualizar-md --salvar-json --baixar-arquivos`.


## Arquivos baixados

| Arquivo | Status | HTTP | Tentativas | Content-Type | Caminho local | Observação |
| --- | --- | ---: | ---: | --- | --- | --- |
| Download — Planalto — Lei 14.133/2021 | erro_download | — | 1 | — | — | [WinError 10054] Foi forçado o cancelamento de uma conexão existente pelo host remoto |
| Download — REDELOG RJ — legislação de licitações | baixado | 200 | 1 | text/html; charset=UTF-8 | data/raw/v1/arquivos/html/redelog_2aab9265a1fb.html |  |
| Download — CGE-RJ — decretos estaduais RJ | reutilizado | 200 | 1 | text/html; charset=UTF-8 | data/raw/v1/arquivos/html/cge_decretos_92dacd382dcc.html | conteúdo já existente; arquivo não duplicado |
| Download — PNCP — documentação OpenAPI v3 | baixado | 200 | 1 | application/json | data/raw/v1/arquivos/json/pncp_api_docs_849945438c47.json |  |
| Download — PNCP — Swagger UI | baixado | 200 | 1 | text/html | data/raw/v1/arquivos/html/pncp_swagger_bb9928afd0ea.html |  |
| Download — CATMAT | reutilizado | 200 | 1 | text/html; charset=utf-8 | data/raw/v1/arquivos/html/catmat_2f2443b697e7.html | conteúdo já existente; arquivo não duplicado |
| Download — Gist — MCP Compras | baixado | 200 | 1 | text/html; charset=utf-8 | data/raw/v1/arquivos/html/gist_mcp_compras_a313e21d2259.html |  |
| Download — Glama — MCP Compras PNCP PCA | baixado | 200 | 1 | text/html; charset=utf-8 | data/raw/v1/arquivos/html/glama_mcp_compras_72267236b0af.html |  |
| Download — Diário Oficial da União — publicação da Lei 14.133 | reutilizado | 200 | 1 | text/html;charset=ISO-8859-1 | data/raw/v1/arquivos/html/in_gov_lei_14133_a7d7a3be2b63.html | conteúdo já existente; arquivo não duplicado |
| Download — MPPI — PDF da Lei 14.133/2021 | reutilizado | 200 | 1 | application/pdf | data/raw/v1/arquivos/pdf/mppi_lei_14133_pdf_a5b21177bdc7.pdf | conteúdo já existente; arquivo não duplicado |
| Download — PNCP — primeiro arquivo de edital | baixado | 200 | 1 | application/octet-stream;charset=UTF-8 | data/raw/v1/arquivos/pncp/edital_60fecce8c451.pdf |  |
| Download — PNCP — primeiro arquivo de ata | bloqueado_por_fonte | — |  | — | — | download não tentado porque PNCP — primeiro arquivo de ata não forneceu uma URL de arquivo |
| JSON bruto — PNCP — contratações/publicação | baixado | 200 | 1 | application/json | data/raw/v1/arquivos/json/raw_contratacoes_publicacao_3d4fff66dd0a.json |  |
| JSONL — registros de PNCP — contratações/publicação | baixado | 200 | 1 | application/x-ndjson | data/raw/v1/arquivos/jsonl/records_contratacoes_publicacao_6e4988a4f6ff.jsonl |  |
| JSON bruto — PNCP — contratos | baixado | 200 | 1 | application/json | data/raw/v1/arquivos/json/raw_contratos_3580276b9d1f.json |  |
| JSONL — registros de PNCP — contratos | baixado | 200 | 1 | application/x-ndjson | data/raw/v1/arquivos/jsonl/records_contratos_19d8cdafe76a.jsonl |  |
| JSON bruto — PNCP — arquivos/editais da contratação | baixado | 200 | 1 | application/json | data/raw/v1/arquivos/json/raw_arquivos_edital_144134c5b2bc.json |  |
| JSONL — registros de PNCP — arquivos/editais da contratação | baixado | 200 | 1 | application/x-ndjson | data/raw/v1/arquivos/jsonl/records_arquivos_edital_df0fcc442ad5.jsonl |  |
| JSON bruto — PNCP — documentação OpenAPI v3 | baixado | 200 | 1 | application/json | data/raw/v1/arquivos/json/raw_pncp_api_docs_a0496c34cd5e.json |  |
