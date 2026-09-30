import type { RagResponse } from '../domain/rag/types';

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export async function mockQuery(query: string): Promise<RagResponse> {
  await wait(750);
  const normalized = query.toLocaleLowerCase('pt-BR');

  if (normalized.includes('fraud') || normalized.includes('ilegal')) {
    return {
      query,
      answer:
        'As fontes disponíveis não fornecem evidências suficientes para responder esta pergunta com segurança. A emissão de juízo definitivo sobre conduta ilícita ou fraude contratual exige decisão administrativa conclusiva ou manifestação dos órgãos de controle e do Poder Judiciário.',
      bases_consultadas: ['normativa'],
      sources_used: [
        {
          chunk_id: 'src_norm_01',
          base_id: 'normativa',
          source_file: 'Lei Federal nº 14.133/2021 — Art. 155 (Infrações e Sanções Administrativas)',
        },
      ],
      confidence_level: 'recusado',
      is_refusal: true,
      refusal_reason: 'juizo_legalidade_contrato',
    };
  }

  if (normalized.includes('bancári') || normalized.includes('conta') || normalized.includes('senha')) {
    return {
      query,
      answer:
        'Não foi possível responder esta consulta com as fontes disponíveis. Informações bancárias ou credenciais protegidas de agentes e fornecedores não integram o acervo de transparência pública.',
      bases_consultadas: [],
      sources_used: [],
      confidence_level: 'recusado',
      is_refusal: true,
      refusal_reason: 'dados_bancarios_fornecedor',
    };
  }

  if (normalized.includes('maiores contratações') || normalized.includes('maiores')) {
    return {
      query,
      answer:
        'Com base nos registros oficiais do Portal Nacional de Contratações Públicas (PNCP) e do SIGA-RJ referentes ao exercício de 2025, destacam-se entre as maiores contratações do Estado do Rio de Janeiro:\n\n1. Secretaria de Estado de Saúde (SES-RJ): Gestão integrada de unidades de pronto atendimento hospitalar (Contrato nº 084/2025) — R$ 142.800.000,00.\n2. Secretaria de Estado de Transporte e Mobilidade Urbana (SECTRAN): Manutenção corretiva e preventiva de infraestrutura viária (Contrato nº 019/2025) — R$ 98.450.000,00.\n3. Secretaria de Estado de Educação (SEEDUC): Fornecimento continuado de gêneros alimentícios para merenda escolar regional (Contrato nº 102/2025) — R$ 67.200.000,00.\n4. Fundação Saúde do Estado do RJ: Fornecimento de órteses, próteses e insumos cirúrgicos especializados (Ata de Registro de Preços nº 031/2025) — R$ 54.120.000,00.',
      bases_consultadas: ['estruturada', 'agregada'],
      sources_used: [
        {
          chunk_id: 'src_pncp_01',
          base_id: 'estruturada',
          source_file: 'PNCP — Contratos Executivos Estado do Rio de Janeiro (Exercício 2025)',
          trecho: 'Relação consolidada de instrumentos contratuais vigentes com valor global homologado acima de R$ 50 milhões.',
        },
        {
          chunk_id: 'src_siga_02',
          base_id: 'estruturada',
          source_file: 'SIGA-RJ — Sistema Integrado de Gestão de Aquisições do RJ',
          trecho: 'Extratos de empenho e execução orçamentária por unidade administrativa da administração direta e indireta.',
        },
        {
          chunk_id: 'src_dados_03',
          base_id: 'agregada',
          source_file: 'Portal de Dados Abertos do Estado do RJ — Execução Orçamentária 2025',
          trecho: 'Planilhas agregadas de despesas liquidadas por grupo de natureza da despesa (GND 3 e 4).',
        },
      ],
      confidence_level: 'alta',
      is_refusal: false,
      refusal_reason: null,
    };
  }

  if (normalized.includes('manutenção de computadores') || normalized.includes('computador')) {
    return {
      query,
      answer:
        'No exercício de 2025, os seguintes órgãos estaduais registraram contratações para serviços de suporte e manutenção de microcomputadores, estações de trabalho e equipamentos de TI:\n\n• Secretaria de Estado de Fazenda (SEFAZ-RJ): Pregão Eletrônico nº 008/2025 — Valor homologado de R$ 2.450.000,00 para manutenção preventiva e corretiva com reposição de peças.\n• Universidade do Estado do Rio de Janeiro (UERJ): Contrato nº 045/2025 — Valor global de R$ 1.180.000,00 para suporte técnico a laboratórios acadêmicos e campi regionais.\n• Controladoria Geral do Estado (CGE-RJ): Termo de Adesão a Registro de Preços SEPLAG nº 012/2025 — Valor de R$ 380.000,00.',
      bases_consultadas: ['estruturada'],
      sources_used: [
        {
          chunk_id: 'src_siga_comp_01',
          base_id: 'estruturada',
          source_file: 'SIGA-RJ — Pregão Eletrônico nº 008/2025 (SEFAZ-RJ)',
          trecho: 'Termo de Referência: Serviços de sustentação e suporte técnico a parque de microcomputadores.',
        },
        {
          chunk_id: 'src_pncp_comp_02',
          base_id: 'estruturada',
          source_file: 'PNCP — Contrato Administrativo nº 045/2025 (UERJ)',
          trecho: 'Instrumento contratual de prestação de serviços continuados de tecnologia da informação.',
        },
      ],
      confidence_level: 'alta',
      is_refusal: false,
      refusal_reason: null,
    };
  }

  if (normalized.includes('pesquisa de preços') || normalized.includes('regra')) {
    return {
      query,
      answer:
        'Em 2025, a pesquisa de preços nas contratações públicas do Estado do Rio de Janeiro é regulada pelo art. 23 da Lei Federal nº 14.133/2021 c/c a regulamentação estadual vigente. A formação do preço estimado exige a utilização de parâmetros preferenciais:\n\n1. Dados do Portal Nacional de Contratações Públicas (PNCP) e do Painel de Preços oficial;\n2. Contratações similares de outros órgãos públicos da administração estadual e municipal concluídas no período de até 1 ano anterior à pesquisa;\n3. Dados de notas fiscais eletrônicas em bancos de preços públicos governamentais;\n4. Pesquisa direta com ao menos 3 fornecedores idôneos, admitida em caráter complementar quando inviáveis os parâmetros anteriores.\n\nDeve-se priorizar o cálculo da mediana ou média saneada dos valores obtidos, descartando preços manifestamente inexequíveis ou excessivos.',
      bases_consultadas: ['normativa'],
      sources_used: [
        {
          chunk_id: 'src_lei_14133',
          base_id: 'normativa',
          source_file: 'Lei Federal nº 14.133/2021 — Art. 23 (Parâmetros de Pesquisa de Preços)',
          trecho: 'O valor previamente estimado da contratação deverá ser compatível com os valores praticados pelo mercado.',
        },
        {
          chunk_id: 'src_doerj_dec',
          base_id: 'normativa',
          source_file: 'Diário Oficial do Estado do Rio de Janeiro (DOERJ) — Decretos Regulamentares de Compras',
          trecho: 'Normativa estadual que estabelece metodologia para apuração de preços de referência nos órgãos fluminenses.',
        },
      ],
      confidence_level: 'alta',
      is_refusal: false,
      refusal_reason: null,
    };
  }

  if (normalized.includes('microempresa') || normalized.includes('me/') || normalized.includes('epp')) {
    return {
      query,
      answer:
        'Sim. Microempresas (ME) e Empresas de Pequeno Porte (EPP) possuem garantia de tratamento diferenciado e favorecido nas licitações e contratações do Estado do Rio de Janeiro, com respaldo nos arts. 42 a 49 da Lei Complementar nº 123/2006 e no art. 4º da Lei nº 14.133/2021:\n\n• Licitações exclusivas para itens ou lotes de contratação cujo valor seja de até R$ 80.000,00;\n• Cota reservada de até 25% para ME/EPP na aquisição de bens de natureza divisível;\n• Preferência de desempate ficto em caso de propostas com margem de até 5% (no pregão) ou 10% (outras modalidades) em relação à menor oferta;\n• Prazo de 5 dias úteis para regularização de eventuais pendências fiscais e trabalhistas após a habilitação.',
      bases_consultadas: ['normativa', 'estruturada'],
      sources_used: [
        {
          chunk_id: 'src_lc_123',
          base_id: 'normativa',
          source_file: 'Lei Complementar Federal nº 123/2006 — Estatuto Nacional da Microempresa',
          trecho: 'Do Acesso aos Mercados: tratamento diferenciado e simplificado nas licitações públicas.',
        },
        {
          chunk_id: 'src_edital_padrao',
          base_id: 'normativa',
          source_file: 'Edital Padrão de Pregão Eletrônico SEPLAG-RJ (Cláusula de Habilitação ME/EPP)',
          trecho: 'Critérios de desempate ficto e comprovação de regularidade fiscal postergada.',
        },
      ],
      confidence_level: 'alta',
      is_refusal: false,
      refusal_reason: null,
    };
  }

  if (normalized.includes('fornecedor') || normalized.includes('contrato')) {
    return {
      query,
      answer:
        'A pesquisa por fornecedor nas fontes oficiais do Estado do Rio de Janeiro consolida todos os instrumentos contratuais, atas de registro de preços vigentes e termos aditivos cadastrados no PNCP e no SIGA-RJ.\n\nPara o CNPJ consultado, constam registros de contratos ativos nas pastas de Educação, Infraestrutura e Saúde, com dados auditados de valor inicial homologado, saldo executado, vigência contratual e eventuais sanções vigentes registradas no Cadastro de Fornecedores Impedidos.',
      bases_consultadas: ['estruturada'],
      sources_used: [
        {
          chunk_id: 'src_siga_fornec',
          base_id: 'estruturada',
          source_file: 'SIGA-RJ — Cadastro Geral de Fornecedores e Contratos Homologados',
          trecho: 'Consulta unificada por Razão Social / CNPJ de fornecedores habilitados no Estado do Rio de Janeiro.',
        },
      ],
      confidence_level: 'media',
      is_refusal: false,
      refusal_reason: null,
    };
  }

  return {
    query,
    answer:
      'Com base nas fontes oficiais de contratações públicas do Estado do Rio de Janeiro (PNCP, SIGA-RJ e Diário Oficial), foram identificados registros correspondentes à sua consulta. As informações foram consolidadas a partir de editais, atas e extratos contratuais vigentes com fundamentação na Lei nº 14.133/2021.',
    bases_consultadas: ['estruturada', 'normativa'],
    sources_used: [
      {
        chunk_id: 'src_pncp_gen',
        base_id: 'estruturada',
        source_file: 'Portal Nacional de Contratações Públicas (PNCP)',
        trecho: 'Base oficial de editais e instrumentos contratuais do Estado do Rio de Janeiro.',
      },
      {
        chunk_id: 'src_doerj_gen',
        base_id: 'normativa',
        source_file: 'Diário Oficial do Estado do Rio de Janeiro (DOERJ)',
        trecho: 'Publicações de atos administrativos e extratos oficiais.',
      },
    ],
    confidence_level: 'alta',
    is_refusal: false,
    refusal_reason: null,
  };
}
