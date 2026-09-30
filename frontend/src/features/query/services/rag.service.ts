import type { RagQueryRequest, RagResponse } from '../../../domain/rag/types';
import { mockQuery } from '../../../mocks/rag.mock';

const API_BASE_URL = String(import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const USE_MOCKS = String(import.meta.env.VITE_USE_MOCKS ?? 'false') === 'true';

class RagService {
  async query(payload: RagQueryRequest): Promise<RagResponse> {
    if (USE_MOCKS) {
      return mockQuery(payload.query);
    }

    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/query`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        if (response.status >= 500) {
          throw new Error('O serviço está temporariamente indisponível.');
        }
        throw new Error('Não foi possível concluir a consulta. Tente novamente.');
      }

      return (await response.json()) as RagResponse;
    } catch (err) {
      if (err instanceof Error && err.message) {
        if (err.message.includes('indisponível') || err.message.includes('concluir')) {
          throw err;
        }
      }
      throw new Error('Não foi possível conectar ao serviço. Tente novamente em alguns instantes.');
    }
  }

  async health(): Promise<boolean> {
    if (USE_MOCKS) return true;

    try {
      const response = await fetch(`${API_BASE_URL}/health`, { method: 'GET' });
      return response.ok;
    } catch {
      return false;
    }
  }
}

export const ragService = new RagService();
