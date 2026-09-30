import { SUGGESTED_QUERIES } from '../../../domain/rag/catalog';
import { Icon } from '../../../shared/components/Icon';

interface SuggestedQueriesProps {
  disabled: boolean;
  onSelect: (query: string) => void;
}

export function SuggestedQueries({ disabled, onSelect }: SuggestedQueriesProps) {
  return (
    <section className="suggestions-unboxed-section" aria-labelledby="suggested-queries-title">
      <div className="suggestions-header">
        <span className="section-eyebrow">CONSULTAS FREQUENTES</span>
        <h2 id="suggested-queries-title" className="suggestions-title">
          Atalhos de pesquisa orientados a tarefas
        </h2>
        <p className="suggestions-desc">
          Selecione uma pergunta frequente para consultar atos, regras vigentes e fornecedores:
        </p>
      </div>

      <div className="suggestions-list" role="list">
        {SUGGESTED_QUERIES.map((item) => (
          <button
            key={item.query}
            type="button"
            className="suggestion-row"
            disabled={disabled}
            onClick={() => onSelect(item.query)}
            aria-label={`Pesquisar: ${item.query}`}
          >
            <div className="suggestion-info">
              <span className="suggestion-category-tag">
                <span className="dot-indicator" />
                {item.category}
              </span>
              <p className="suggestion-question-text">{item.query}</p>
            </div>
            <div className="suggestion-action-icon">
              <Icon name="arrow-up-right" size={16} />
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}
