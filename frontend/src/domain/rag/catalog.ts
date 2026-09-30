import type { ConfidenceLevel, OfficialSource, RefusalReason } from './types';

export interface SuggestedQueryItem {
  category: string;
  query: string;
}

export const SUGGESTED_QUERIES: SuggestedQueryItem[] = [
  {
    category: 'Maiores contratações',
    query: 'Quais foram as maiores contratações do RJ em 2025?',
  },
  {
    category: 'Contratações por órgão',
    query: 'Quais órgãos contrataram serviços de manutenção de computadores?',
  },
  {
    category: 'Regras de contratação',
    query: 'Qual regra estava vigente para pesquisa de preços em 2025?',
  },
  {
    category: 'Participação de empresas',
    query: 'Uma microempresa pode participar deste tipo de contratação?',
  },
  {
    category: 'Pesquisa por fornecedor',
    query: 'Quais contratos estão associados a determinado fornecedor?',
  },
];

export const REFUSAL_LABELS: Record<RefusalReason, string> = {
  dados_bancarios_fornecedor:
    'Esta consulta envolve dados bancários ou informações financeiras protegidas de terceiros.',
  credenciais_vazadas:
    'Esta consulta envolve credenciais de acesso ou informações sob sigilo restrito.',
  juizo_legalidade_contrato:
    'Não é possível emitir juízo definitivo de fraude ou ilegalidade sem decisão administrativa ou judicial prévia.',
  fora_de_escopo:
    'O assunto consultado não se encontra no âmbito das contratações públicas do Estado do Rio de Janeiro.',
  sem_evidencia:
    'As fontes disponíveis não fornecem evidências suficientes para responder esta pergunta com segurança.',
};

export const EVIDENCE_LEVEL_LABELS: Record<ConfidenceLevel, string> = {
  alta: 'Alto',
  media: 'Moderado',
  baixa: 'Baixo',
  recusado: 'Não avaliado',
};

export const CONFIDENCE_LABELS = EVIDENCE_LEVEL_LABELS;

/* Catálogo de Fontes Oficiais de Contratações */
export const OFFICIAL_SOURCES: OfficialSource[] = [
  {
    id: 'pncp',
    nome: 'Portal Nacional de Contratações Públicas',
    sigla: 'PNCP',
    instituicao: 'Governo Federal / Ministério da Gestão e da Inovação',
    tipoInformacao: 'Editais, avisos de contratação direta e contratos administrativos',
    url: 'https://pncp.gov.br',
    natureza: 'estruturada',
    frequenciaColeta: 'Diária',
    descricao:
      'Repositório nacional oficial instituído pela Lei nº 14.133/2021 para divulgação centralizada dos atos de contratação pública de todos os entes federativos.',
  },
  {
    id: 'siga-rj',
    nome: 'Sistema Integrado de Gestão de Aquisições do RJ',
    sigla: 'SIGA-RJ',
    instituicao: 'Governo do Estado do Rio de Janeiro / SEPLAG-RJ',
    tipoInformacao: 'Pregões eletrônicos, atas de registro de preços e catálogo de itens',
    url: 'https://www.compras.rj.gov.br',
    natureza: 'estruturada',
    frequenciaColeta: 'Semanal',
    descricao:
      'Canal oficial estadual de compras públicas do Rio de Janeiro, contendo editais, atas de sessões públicas e cadastro de fornecedores.',
  },
  {
    id: 'doerj',
    nome: 'Diário Oficial do Estado do Rio de Janeiro',
    sigla: 'DOERJ',
    instituicao: 'Imprensa Oficial do Estado do Rio de Janeiro (IOERJ)',
    tipoInformacao: 'Atos normativos, decretos regulamentares e homologações de licitações',
    url: 'https://www.ioerj.com.br',
    natureza: 'normativa',
    frequenciaColeta: 'Diária',
    descricao:
      'Veículo oficial de publicação dos atos governamentais, decretos de regulamentação da Lei nº 14.133/2021 e extratos contratuais no âmbito do Estado do RJ.',
  },
  {
    id: 'dados-abertos-rj',
    nome: 'Portal de Dados Abertos do Estado do RJ',
    sigla: 'DADOS-RJ',
    instituicao: 'Controladoria Geral do Estado (CGE-RJ) e SEPLAG-RJ',
    tipoInformacao: 'Execução orçamentária, pagamentos a fornecedores e planos anuais de contratações',
    url: 'https://dados.rj.gov.br',
    natureza: 'agregada',
    frequenciaColeta: 'Mensal',
    descricao:
      'Conjuntos de dados abertos para transparência pública, permitindo conferência e cruzamento de empenhos, liquidações e contratos vigentes.',
  },
];
