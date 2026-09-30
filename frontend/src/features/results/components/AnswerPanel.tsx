import { EVIDENCE_LEVEL_LABELS, REFUSAL_LABELS } from '../../../domain/rag/catalog';
import type { ConfidenceLevel, EvidenceNature, RagResponse, SourceRef } from '../../../domain/rag/types';
import { Icon } from '../../../shared/components/Icon';

interface AnswerPanelProps {
  response: RagResponse;
  onReset: () => void;
}

function getFriendlyBaseName(baseId: string): string {
  switch (baseId) {
    case 'normativa':
    case 'editais_normativo':
    case 'normas_contratacoes':
      return 'Normas e Legislação Oficial';
    case 'estruturada':
    case 'contratos_estruturado':
      return 'Editais e Contratos (PNCP / SIGA-RJ)';
    case 'conversacional':
    case 'atas_conversacional':
      return 'Atas de Sessão e Registros';
    case 'agregada':
    case 'pca_agregado':
      return 'Dados Abertos e Indicadores';
    default:
      return 'Fontes Oficiais';
  }
}

function formatFriendlySource(source: SourceRef): { title: string; context: string } {
  const file = source.source_file;
  if (
    file.includes('—') ||
    file.includes('Lei') ||
    file.includes('PNCP') ||
    file.includes('SIGA') ||
    file.includes('DOERJ') ||
    file.includes('Portal')
  ) {
    return {
      title: file,
      context: getFriendlyBaseName(source.base_id),
    };
  }

  const cleanName = file.replace(/\.(pdf|json|csv|html)$/i, '').replace(/_/g, ' ');
  return {
    title: cleanName,
    context: getFriendlyBaseName(source.base_id),
  };
}

function getEvidenceDotClass(level: ConfidenceLevel): string {
  switch (level) {
    case 'alta':
      return 'dot-success';
    case 'media':
      return 'dot-warning';
    case 'baixa':
    case 'recusado':
      return 'dot-danger';
  }
}

export function AnswerPanel({ response, onReset }: AnswerPanelProps) {
  // Separação limpa de parágrafos da resposta
  const paragraphs = response.answer
    ? response.answer.split('\n\n').filter((p) => p.trim().length > 0)
    : [];

  return (
    <div className="answer-flow">
      {/* 1. Pergunta Realizada (Sem Caixa Fechada) */}
      <section className="query-recap-unboxed">
        <div className="recap-header-row">
          <span className="section-eyebrow">CONSULTA REALIZADA</span>
          <button
            type="button"
            className="btn-text-action"
            onClick={onReset}
            aria-label="Fazer nova pesquisa"
          >
            <Icon name="refresh" size={13} />
            <span>Nova pesquisa</span>
          </button>
        </div>
        <h2 className="recap-query-headline">“{response.query}”</h2>
      </section>

      {response.is_refusal ? (
        /* Estado de Recusa / Evidência Insuficiente (Unboxed) */
        <article className="refusal-unboxed" role="alert">
          <div className="refusal-header">
            <Icon name="info" size={20} />
            <div>
              <h2 className="refusal-title">Não foi possível responder esta consulta</h2>
              <p className="refusal-desc">
                As fontes disponíveis não fornecem evidências suficientes para responder esta
                pergunta com segurança.
              </p>
            </div>
          </div>

          {response.refusal_reason ? (
            <div className="refusal-reason-clean">
              <span className="refusal-reason-label">Motivo da restrição:</span>
              <p className="refusal-reason-text">{REFUSAL_LABELS[response.refusal_reason]}</p>
            </div>
          ) : null}

          {paragraphs.length > 0 ? (
            <div className="refusal-body">
              {paragraphs.map((para, i) => (
                <p key={i}>{para}</p>
              ))}
            </div>
          ) : null}
        </article>
      ) : (
        <>
          {/* 2. Resposta Principal Fundamentada (Unboxed, Espaçamento Editorial) */}
          <article className="answer-prose-section" aria-labelledby="answer-title">
            <header className="answer-meta-header">
              <div className="answer-status-tag">
                <span className="dot-indicator dot-success" />
                <h2 id="answer-title" className="answer-title-text">
                  Resposta Fundamentada
                </h2>
              </div>
              <span className="answer-count-tag">
                {response.sources_used.length}{' '}
                {response.sources_used.length === 1 ? 'referência verificada' : 'referências verificadas'}
              </span>
            </header>

            <div className="answer-body-paragraphs">
              {paragraphs.map((para, index) => (
                <p key={index}>{para}</p>
              ))}
            </div>

            {/* 3. Fontes Utilizadas (Separadas com elegância, sem caixas fechadas) */}
            {response.sources_used.length > 0 ? (
              <section className="sources-unboxed-section" aria-labelledby="sources-heading">
                <h3 id="sources-heading" className="sources-unboxed-title">
                  Fontes oficiais utilizadas ({response.sources_used.length})
                </h3>

                <div className="sources-unboxed-list">
                  {response.sources_used.map((source, index) => {
                    const friendly = formatFriendlySource(source);
                    return (
                      <article
                        className="source-unboxed-item"
                        key={`${source.source_file}-${index}`}
                      >
                        <div className="source-item-top">
                          <Icon name="file-text" size={15} />
                          <h4 className="source-title-text">{friendly.title}</h4>
                        </div>
                        <span className="source-context-text">{friendly.context}</span>
                        {source.trecho ? (
                          <blockquote className="source-quote-excerpt">
                            “{source.trecho}”
                          </blockquote>
                        ) : null}
                      </article>
                    );
                  })}
                </div>
              </section>
            ) : null}

            {/* 4. Informações Complementares */}
            <footer className="answer-footer-clean" aria-label="Informações complementares">
              <div className="answer-footer-item">
                <span className="footer-label">Nível de evidência</span>
                <span className="footer-value">
                  <span
                    className={`dot-indicator ${getEvidenceDotClass(response.confidence_level)}`}
                  />
                  <strong>{EVIDENCE_LEVEL_LABELS[response.confidence_level]}</strong>
                </span>
              </div>

              {response.bases_consultadas.length > 0 ? (
                <div className="answer-footer-item">
                  <span className="footer-label">Acervos consultados</span>
                  <div className="footer-chips-list">
                    {response.bases_consultadas.map((base: EvidenceNature) => (
                      <span key={base} className="footer-chip">
                        {getFriendlyBaseName(base)}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
            </footer>
          </article>
        </>
      )}
    </div>
  );
}
