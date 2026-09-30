import { useState } from 'react';
import { Icon } from '../../../shared/components/Icon';

interface QueryComposerProps {
  loading: boolean;
  onSubmit: (query: string) => void;
}

export function QueryComposer({ loading, onSubmit }: QueryComposerProps) {
  const [query, setQuery] = useState('');

  function submit() {
    const normalized = query.trim();
    if (!normalized || loading) return;
    onSubmit(normalized);
    setQuery('');
  }

  return (
    <div className="query-container">
      <form
        className="query-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label htmlFor="query-input" className="query-label">
          Pesquisar contratações públicas
        </label>
        <div className="query-input-wrapper">
          <Icon name="search" size={18} className="query-search-icon" />
          <input
            id="query-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Ex: Quais contratos de tecnologia foram celebrados acima de R$ 500 mil em 2025?"
            aria-label="Pergunta para consulta ao acervo público de contratações"
            disabled={loading}
          />
          <button
            type="submit"
            className="primary-action"
            disabled={loading || !query.trim()}
          >
            <span>{loading ? 'Pesquisando...' : 'Pesquisar'}</span>
            <Icon name="search" size={15} />
          </button>
        </div>
        <div className="query-meta">
          <span>As fontes mais relevantes são selecionadas automaticamente para cada consulta.</span>
        </div>
      </form>
    </div>
  );
}
