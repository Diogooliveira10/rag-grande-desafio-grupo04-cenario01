import { Icon } from '../../../shared/components/Icon';

export function ProcessingStatus() {
  return (
    <section className="processing-card" aria-live="polite">
      <div className="processing-icon spin">
        <Icon name="refresh" size={20} />
      </div>
      <div>
        <h2>Consultando fontes...</h2>
        <p>Buscando evidências verificáveis nas fontes oficiais de contratações públicas.</p>
      </div>
    </section>
  );
}
