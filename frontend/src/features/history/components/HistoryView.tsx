import { useState } from 'react';
import { EVIDENCE_LEVEL_LABELS } from '../../../domain/rag/catalog';
import type { SessionEntry } from '../../../domain/rag/types';
import { Icon } from '../../../shared/components/Icon';
import { formatTime } from '../../../shared/utils/formatters';

interface HistoryViewProps {
  entries: SessionEntry[];
  onSelectQuery: (entry: SessionEntry) => void;
  onRepeatQuery: (query: string) => void;
  onClearHistory: () => void;
}

export function HistoryView({
  entries,
  onSelectQuery,
  onRepeatQuery,
  onClearHistory,
}: HistoryViewProps) {
  const [searchTerm, setSearchTerm] = useState('');

  const filteredEntries = entries.filter((entry) =>
    entry.response.query.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  return (
    <div className="view-container">
      {/* Cabeçalho Editorial Sem Caixas */}
      <header className="view-header">
        <span className="section-eyebrow">REGISTRO DA SESSÃO</span>
        <h1 className="view-title">Histórico de <em>Consultas</em></h1>
        <p className="view-description">
          Consulte, abra ou repita pesquisas realizadas nesta sessão de trabalho.
        </p>
        <p className="session-notice-clean">
          <Icon name="info" size={14} />
          <span>Armazenado temporariamente apenas nesta sessão do navegador.</span>
        </p>
      </header>

      {/* Barra de Filtro e Ações */}
      <section className="history-section" aria-labelledby="history-heading">
        <div className="history-toolbar">
          <div className="history-count">
            <h2 id="history-heading" className="history-count-title">
              Consultas registradas ({filteredEntries.length})
            </h2>
          </div>

          <div className="history-controls">
            <div className="search-input-wrapper">
              <Icon name="search" size={15} />
              <input
                type="search"
                placeholder="Filtrar por palavra-chave..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                aria-label="Filtrar histórico de consultas"
              />
            </div>
            {entries.length > 0 ? (
              <button
                type="button"
                className="btn-text-action"
                onClick={onClearHistory}
                aria-label="Limpar todo o histórico da sessão"
              >
                <Icon name="trash" size={14} />
                <span>Limpar histórico</span>
              </button>
            ) : null}
          </div>
        </div>

        {/* Lista de Consultas (Mobile-First, Sem Caixas com Bordas) */}
        {filteredEntries.length === 0 ? (
          <div className="empty-state-unboxed">
            <Icon name="search" size={24} />
            <h3>Nenhuma consulta encontrada</h3>
            <p>
              {entries.length === 0
                ? 'As perguntas pesquisadas durante a sua navegação aparecerão listadas aqui.'
                : 'Nenhuma pergunta coincide com o termo pesquisado.'}
            </p>
          </div>
        ) : (
          <div className="history-feed" role="feed" aria-label="Lista de consultas anteriores">
            {filteredEntries.map((entry) => {
              const isRefusal = entry.response.is_refusal;
              const level = entry.response.confidence_level;
              return (
                <article key={entry.id} className="history-entry">
                  {/* Linha Superior: Horário e Grau de Evidência */}
                  <div className="entry-meta-row">
                    <time className="entry-timestamp">
                      <Icon name="clock" size={13} />
                      <span>{formatTime(entry.createdAt)}</span>
                    </time>

                    <div className="entry-status">
                      <span
                        className={`dot-indicator ${
                          isRefusal
                            ? 'dot-danger'
                            : level === 'alta'
                            ? 'dot-success'
                            : 'dot-warning'
                        }`}
                      />
                      <span className="entry-status-text">
                        {isRefusal
                          ? 'Evidência insuficiente'
                          : `Evidência: ${EVIDENCE_LEVEL_LABELS[level]}`}
                      </span>
                    </div>
                  </div>

                  {/* Corpo: Pergunta em destaque sem caixa */}
                  <div className="entry-body">
                    <h3 className="entry-query-title">{entry.response.query}</h3>
                  </div>

                  {/* Ações: Alvos ergonômicos */}
                  <div className="entry-actions-row">
                    <button
                      type="button"
                      className="btn-action-primary"
                      onClick={() => onSelectQuery(entry)}
                      aria-label={`Abrir resposta da pergunta: ${entry.response.query}`}
                    >
                      <span>Abrir resposta</span>
                      <Icon name="arrow-up-right" size={13} />
                    </button>
                    <button
                      type="button"
                      className="btn-action-secondary"
                      onClick={() => onRepeatQuery(entry.response.query)}
                      aria-label={`Repetir consulta: ${entry.response.query}`}
                    >
                      <Icon name="refresh" size={13} />
                      <span>Repetir pesquisa</span>
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
