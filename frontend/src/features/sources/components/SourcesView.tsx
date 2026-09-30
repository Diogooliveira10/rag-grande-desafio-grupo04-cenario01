import { OFFICIAL_SOURCES } from '../../../domain/rag/catalog';
import { Icon } from '../../../shared/components/Icon';

export function SourcesView() {
  return (
    <div className="view-container">
      <header className="view-header">
        <span className="section-eyebrow">TRANSPARÊNCIA E PROVENIÊNCIA</span>
        <h1>Fontes <em>Oficiais</em> de Informação</h1>
        <p>
          Repositórios governamentais e portais oficiais do Estado do Rio de Janeiro e da União
          utilizados para fundamentar respostas com dados autênticos e verificáveis.
        </p>
      </header>

      {/* Visão de Conjunto / O que está coberto */}
      <section className="sources-overview-panel">
        <div className="overview-heading">
          <Icon name="compass" size={18} />
          <h2>De onde vêm as respostas</h2>
        </div>
        <p>
          O sistema consulta exclusivamente acervos públicos governamentais. Toda resposta é
          construída a partir de trechos de editais homologados, leis em vigor, atas de pregão e
          registros do Portal Nacional de Contratações Públicas (PNCP) e do compras estaduais (SIGA-RJ).
        </p>
      </section>

      {/* Grid de Fontes Oficiais */}
      <div className="sources-grid">
        {OFFICIAL_SOURCES.map((source) => (
          <article key={source.id} className="source-card">
            <header className="source-card-header">
              <div className="source-meta">
                <strong className="source-sigla">{source.sigla}</strong>
                <span className="source-nature-label">
                  <span className="dot-indicator dot-success" />
                  {source.instituicao}
                </span>
              </div>
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="external-link"
                title={`Acessar portal oficial: ${source.nome}`}
                aria-label={`Acessar portal oficial de ${source.nome} (abre em nova aba)`}
              >
                <span>Portal oficial</span>
                <Icon name="arrow-up-right" size={13} />
              </a>
            </header>

            <h2 className="source-title">{source.nome}</h2>

            <div className="source-type-info">
              <span className="source-info-label">Tipo de informação:</span>
              <p className="source-info-value">{source.tipoInformacao}</p>
            </div>

            <p className="source-desc">{source.descricao}</p>

            <footer className="source-card-footer">
              <div className="footer-meta-item">
                <Icon name="check" size={13} />
                <span>Origem verificada pelo Estado do RJ</span>
              </div>
            </footer>
          </article>
        ))}
      </div>
    </div>
  );
}
