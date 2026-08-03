/**
 * The bank app behind the conversation: a balance and what happened lately.
 *
 * Every figure here comes from `/v1/account/summary`, which reaches the ledger through a plan built
 * in code. That matters more than it looks: if this screen showed mock data, the balance beside the
 * chat's balance would be two numbers on one phone, and the first thing anybody would notice is that
 * they disagree.
 *
 * The tab bar and the four shortcuts are chrome. They are inert and marked as such rather than being
 * buttons that do nothing when pressed.
 */

import type { Summary } from "./api";
import { categoryName, day, glyph, money, signed } from "./format";

const SHORTCUTS = [
  ["📲", "Bizum"],
  ["↗", "Enviar"],
  ["💳", "Tarjetas"],
  ["📄", "Recibos"],
] as const;

const TABS = [
  ["◧", "Inicio"],
  ["≡", "Cuentas"],
  ["📲", "Bizum"],
  ["⋯", "Más"],
] as const;

type Props = {
  summary: Summary | null;
  error: string | null;
  holder: string;
};

export function Home({ summary, error, holder }: Props) {
  return (
    <div className="home">
      <header className="greeting">
        <div>
          <p className="hello">Hola,</p>
          <h1>{holder}</h1>
        </div>
        <div className="avatar" aria-hidden="true">
          {holder.slice(0, 1)}
        </div>
      </header>

      <section className="account" aria-label="Cuenta">
        <p className="account-name">Cuenta corriente</p>
        {summary ? (
          <>
            <p className="balance">{money(summary.booked_minor, summary.currency)}</p>
            <p className="available">
              Disponible {money(summary.available_minor, summary.currency)}
            </p>
          </>
        ) : (
          // Never a zero. A balance of 0,00 € is a figure, and showing one while we are still asking
          // — or because we could not ask — states something false about somebody's money.
          <p className={error ? "balance-error" : "balance-loading"}>
            {error ?? "···"}
          </p>
        )}
      </section>

      <nav className="shortcuts" aria-label="Accesos" aria-disabled="true">
        {SHORTCUTS.map(([icon, label]) => (
          <div className="shortcut" key={label}>
            <span className="shortcut-icon" aria-hidden="true">
              {icon}
            </span>
            <span>{label}</span>
          </div>
        ))}
      </nav>

      <section className="movements" aria-label="Movimientos">
        <h2>Movimientos</h2>
        {summary?.movements.length === 0 && <p className="empty">Nada en los últimos días.</p>}
        <ul>
          {summary?.movements.map((movement, index) => (
            <li key={`${movement.occurred_at}-${index}`}>
              <span className="glyph" aria-hidden="true">
                {glyph(movement.category)}
              </span>
              <span className="what">
                <strong>{movement.merchant_name ?? movement.description}</strong>
                <small>
                  {day(movement.occurred_at)} · {categoryName(movement.category)}
                </small>
              </span>
              <span className={movement.amount_minor < 0 ? "out" : "in"}>
                {signed(movement.amount_minor, summary.currency)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <nav className="tabs" aria-label="Secciones" aria-disabled="true">
        {TABS.map(([icon, label], index) => (
          <div className={index === 0 ? "tab active" : "tab"} key={label}>
            <span aria-hidden="true">{icon}</span>
            <small>{label}</small>
          </div>
        ))}
      </nav>
    </div>
  );
}
