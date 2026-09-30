import { useCallback, useEffect, useState } from 'react';
import type { ApiHealthStatus, RagResponse, SessionEntry } from '../../../domain/rag/types';
import { ragService } from '../services/rag.service';

export function useRagWorkspace() {
  const [loading, setLoading] = useState(false);
  const [currentResponse, setCurrentResponse] = useState<RagResponse | null>(null);
  const [history, setHistory] = useState<SessionEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<ApiHealthStatus>('checking');

  useEffect(() => {
    let active = true;
    ragService.health().then((online) => {
      if (active) setApiStatus(online ? 'online' : 'offline');
    });
    return () => { active = false; };
  }, []);

  const submitQuery = useCallback(async (query: string) => {
    const normalized = query.trim();
    if (!normalized || loading) return;

    setLoading(true);
    setCurrentResponse(null);
    setError(null);

    try {
      const response = await ragService.query({ query: normalized });
      const entry: SessionEntry = {
        id: crypto.randomUUID(),
        createdAt: new Date(),
        response,
      };
      setCurrentResponse(response);
      setHistory((current) => [entry, ...current]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : 'Não foi possível concluir a consulta.');
    } finally {
      setLoading(false);
    }
  }, [loading]);

  const clearHistory = useCallback(() => {
    setHistory([]);
    setCurrentResponse(null);
  }, []);

  return {
    apiStatus,
    loading,
    currentResponse,
    history,
    error,
    submitQuery,
    selectHistoryEntry: (entry: SessionEntry) => setCurrentResponse(entry.response),
    clearHistory,
    resetResponse: () => setCurrentResponse(null),
  };
}
