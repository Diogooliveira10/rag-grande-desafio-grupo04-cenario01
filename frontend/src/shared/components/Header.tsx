import { useState } from 'react';
import type { AppView } from '../../domain/rag/types';
import { Icon } from './Icon';

interface HeaderProps {
  activeView: AppView;
  onSelectView: (view: AppView) => void;
  historyCount?: number;
}

export function Header({
  activeView,
  onSelectView,
  historyCount = 0,
}: HeaderProps) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const handleSelectNav = (view: AppView) => {
    onSelectView(view);
    setMobileMenuOpen(false);
  };

  return (
    <>
      <header className="app-header">
        <div className="header-inner">
          {/* Marca Institucional NEXO RJ */}
          <div
            className="brand-container"
            onClick={() => handleSelectNav('consulta')}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && handleSelectNav('consulta')}
            aria-label="NEXO RJ - Página inicial de consulta"
          >
            <div className="brand-title-row">
              <span className="brand-logo-text">NEXO RJ</span>
            </div>
            <span className="brand-subtext">Consulta inteligente de contratações públicas</span>
          </div>

          {/* Navegação Desktop (Consulta | Histórico | Fontes) */}
          <nav className="header-desktop-nav" aria-label="Navegação principal">
            <button
              type="button"
              className={`nav-link-btn ${activeView === 'consulta' ? 'is-active' : ''}`}
              onClick={() => handleSelectNav('consulta')}
              aria-current={activeView === 'consulta' ? 'page' : undefined}
            >
              <Icon name="search" size={15} />
              <span>Consulta</span>
            </button>

            <button
              type="button"
              className={`nav-link-btn ${activeView === 'historico' ? 'is-active' : ''}`}
              onClick={() => handleSelectNav('historico')}
              aria-current={activeView === 'historico' ? 'page' : undefined}
            >
              <Icon name="clock" size={15} />
              <span>Histórico</span>
              {historyCount > 0 ? (
                <span className="nav-badge-count" aria-label={`${historyCount} consultas no histórico`}>
                  {historyCount}
                </span>
              ) : null}
            </button>

            <button
              type="button"
              className={`nav-link-btn ${activeView === 'fontes' ? 'is-active' : ''}`}
              onClick={() => handleSelectNav('fontes')}
              aria-current={activeView === 'fontes' ? 'page' : undefined}
            >
              <Icon name="file-text" size={15} />
              <span>Fontes</span>
            </button>
          </nav>

          {/* Botão Menu Mobile */}
          <button
            type="button"
            className="mobile-menu-btn"
            onClick={() => setMobileMenuOpen((curr) => !curr)}
            aria-label={mobileMenuOpen ? 'Fechar menu' : 'Abrir menu de navegação'}
            aria-expanded={mobileMenuOpen}
          >
            <Icon name={mobileMenuOpen ? 'x' : 'menu'} size={20} />
            <span className="mobile-menu-text">Menu</span>
          </button>
        </div>
      </header>

      {/* Drawer Móvel de Navegação (Mobile-First) */}
      {mobileMenuOpen ? (
        <div
          className="mobile-drawer-backdrop"
          onClick={() => setMobileMenuOpen(false)}
          aria-hidden="true"
        >
          <nav
            className="mobile-drawer"
            aria-label="Menu principal de navegação"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mobile-drawer-top">
              <div>
                <span className="brand-logo-text">NEXO RJ</span>
                <p className="mobile-drawer-subtext">Contratações públicas</p>
              </div>
              <button
                type="button"
                className="drawer-close-btn"
                onClick={() => setMobileMenuOpen(false)}
                aria-label="Fechar menu"
              >
                <Icon name="x" size={20} />
              </button>
            </div>

            <div className="mobile-drawer-links">
              <button
                type="button"
                className={`drawer-link ${activeView === 'consulta' ? 'is-active' : ''}`}
                onClick={() => handleSelectNav('consulta')}
              >
                <div className="drawer-link-content">
                  <Icon name="search" size={18} />
                  <span>Consulta</span>
                </div>
                {activeView === 'consulta' ? <span className="drawer-active-indicator" /> : null}
              </button>

              <button
                type="button"
                className={`drawer-link ${activeView === 'historico' ? 'is-active' : ''}`}
                onClick={() => handleSelectNav('historico')}
              >
                <div className="drawer-link-content">
                  <Icon name="clock" size={18} />
                  <span>Histórico</span>
                </div>
                <div className="drawer-link-right">
                  {historyCount > 0 ? (
                    <span className="nav-badge-count">{historyCount}</span>
                  ) : null}
                  {activeView === 'historico' ? <span className="drawer-active-indicator" /> : null}
                </div>
              </button>

              <button
                type="button"
                className={`drawer-link ${activeView === 'fontes' ? 'is-active' : ''}`}
                onClick={() => handleSelectNav('fontes')}
              >
                <div className="drawer-link-content">
                  <Icon name="file-text" size={18} />
                  <span>Fontes</span>
                </div>
                {activeView === 'fontes' ? <span className="drawer-active-indicator" /> : null}
              </button>
            </div>

            <div className="mobile-drawer-footer">
              <div className="system-status-indicator">
                <span className="dot-indicator dot-success" />
                <span>Sistema disponível</span>
              </div>
            </div>
          </nav>
        </div>
      ) : null}
    </>
  );
}
