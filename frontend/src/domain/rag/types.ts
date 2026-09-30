export type EvidenceNature = 'normativa' | 'estruturada' | 'conversacional' | 'agregada';

export type ConfidenceLevel = 'alta' | 'media' | 'baixa' | 'recusado';

export type RefusalReason =
  | 'dados_bancarios_fornecedor'
  | 'credenciais_vazadas'
  | 'juizo_legalidade_contrato'
  | 'fora_de_escopo'
  | 'sem_evidencia';

export interface SourceRef {
  chunk_id: string;
  base_id: string;
  source_file: string;
  trecho?: string;
}

export interface RagResponse {
  query: string;
  answer: string;
  bases_consultadas: EvidenceNature[];
  sources_used: SourceRef[];
  confidence_level: ConfidenceLevel;
  is_refusal: boolean;
  refusal_reason?: RefusalReason | null;
  pipeline_metadata?: {
    run_id?: string;
    query_id?: string;
    latency_ms?: number;
    tokens?: number;
    model?: string;
  };
}

export interface RagQueryRequest {
  query: string;
  top_k?: number;
}

export interface SessionEntry {
  id: string;
  createdAt: Date;
  response: RagResponse;
}

export type ApiHealthStatus = 'checking' | 'online' | 'offline';

export type AppView = 'consulta' | 'historico' | 'fontes';

export interface OfficialSource {
  id: string;
  nome: string;
  sigla: string;
  instituicao: string;
  tipoInformacao: string;
  url: string;
  natureza: EvidenceNature;
  frequenciaColeta?: string;
  descricao: string;
}
