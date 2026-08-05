/**
 * The account: its balance, and every movement with a way to narrow them.
 *
 * The search field is the reason this screen exists rather than a longer list on the home screen. It
 * sends a *merchant* — free text, because the ledger's shop names are data and no enum can hold them —
 * and the category chips send an identifier, which banking refuses if it does not know it. Two
 * different kinds of filter, and the difference is not cosmetic: one is a search, the other is a
 * vocabulary.
 */

import { useEffect, useRef, useState } from "react";

import { movements as fetchMovements, type Movements, type Summary } from "./api";
import {
  accountGlyph,
  accountName,
  categoryName,
  categorySource,
  day,
  glyph,
  money,
  signed,
} from "./format";

/** The categories worth a chip. The ledger has more; these are the ones a phone can show. */
const CHIPS = [
  "GROCERIES",
  "RESTAURANTS",
  "TRANSPORT_FUEL",
  "SUBSCRIPTIONS",
  "HOUSING_RENT",
  "LEISURE",
] as const;

const WINDOWS = [
  [30, "30 días"],
  [90, "3 meses"],
  [365, "1 año"],
] as const;

type Props = { summary: Summary | null; searching: boolean };

export function Accounts({ summary, searching }: Props) {
  const [days, setDays] = useState(90);
  const [category, setCategory] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const [internal, setInternal] = useState(false);
  const [found, setFound] = useState<Movements | null>(null);
  const [error, setError] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searching) search.current?.focus();
  }, [searching]);

  useEffect(() => {
    let current = true;
    // Debounced, because every keystroke is a query against the ledger. 300ms is long enough that a
    // typed word is one query and short enough that it does not feel like waiting.
    const timer = setTimeout(async () => {
      try {
        const answer = await fetchMovements({
          days,
          limit: 50,
          category: category ?? undefined,
          merchant: term.trim() || undefined,
          includeInternal: internal,
        });
        if (current) {
          setFound(answer);
          setError(null);
        }
      } catch {
        if (current) setError("No he podido cargar los movimientos.");
      }
    }, 300);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [days, category, term, internal]);

  const shown = found?.movements ?? [];

  return (
    <div className="screen">
      <h1 className="screen-title">Cuentas</h1>

      {/*
        Every account the holder has, not the one the credential names. The header used to be the
        literal string "Cuenta corriente · ES-0001", which was true of the only account there was.

        The investment is listed with the rest and says what its figure is. Hiding it would leave
        somebody with 2.700 € invested unaware of it; adding it into a total would report what they
        paid as what they have. Both are wrong, and only the second is wrong silently.
      */}
      {summary?.holdings?.length ? (
        <section className="holdings" aria-label="Tus cuentas">
          {summary.holdings.map((holding) => (
            <article className="holding" key={holding.account_id}>
              <span className="holding-glyph" aria-hidden="true">
                {accountGlyph(holding.kind)}
              </span>
              <span className="holding-what">
                <strong>{accountName(holding)}</strong>
                {holding.kind === "ASSET" && <small>lo invertido, no lo que vale hoy</small>}
              </span>
              <span className="holding-balance">
                {money(holding.booked_minor, holding.currency)}
              </span>
            </article>
          ))}
        </section>
      ) : (
        summary && (
          <section className="account small">
            <p className="account-name">Cuenta corriente</p>
            <p className="balance">{money(summary.booked_minor, summary.currency)}</p>
          </section>
        )
      )}

      <div className="finder">
        <span aria-hidden="true">🔍</span>
        <input
          ref={search}
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          placeholder="Buscar por comercio"
          aria-label="Buscar por comercio"
        />
        {term && (
          <button className="clear" onClick={() => setTerm("")} aria-label="Limpiar">
            ✕
          </button>
        )}
      </div>

      <div className="chips" role="group" aria-label="Filtros">
        {WINDOWS.map(([value, label]) => (
          <button
            key={value}
            className={days === value ? "chip on" : "chip"}
            onClick={() => setDays(value)}
          >
            {label}
          </button>
        ))}
        {/*
          ADR-009 left open whether internal movements are hidden from the list or merely excluded
          from totals. With a screen in front of it: excluded by default, because the tally is a net
          and a transfer nets to nothing — and available, because a list that hides a 200 € transfer
          does not reconcile with the balance, and the person hunting for it has nowhere to look.
        */}
        <button className={internal ? "chip on" : "chip"} onClick={() => setInternal(!internal)}>
          Traspasos
        </button>
        {CHIPS.map((key) => (
          <button
            key={key}
            className={category === key ? "chip on" : "chip"}
            onClick={() => setCategory(category === key ? null : key)}
          >
            {categoryName(key)}
          </button>
        ))}
      </div>

      {found && (
        <p className="tally">
          {/* "neto", because with ALL directions this figure is income minus expenses. Printed bare
              beside a movement count it reads as "you spent this", which for a window containing a
              salary is wrong by the size of the salary. */}
          {found.movement_count} movimientos · neto {money(found.total_minor, found.currency)}
          {/* Said, not implied. With transfers in, the net counts money that never left the
              household, and a figure whose scope is unstated is one somebody will misread. */}
          {internal && " (traspasos incluidos)"}
        </p>
      )}

      <section className="movements">
        {error && <p className="empty">{error}</p>}
        {!error && shown.length === 0 && <p className="empty">Nada con esos filtros.</p>}
        <ul>
          {shown.map((movement, index) => (
            <li key={`${movement.occurred_at}-${index}`}>
              <span className="glyph" aria-hidden="true">
                {glyph(movement.category)}
              </span>
              <span className="what">
                <strong>{movement.merchant_name ?? movement.description}</strong>
                <small>
                  {day(movement.occurred_at)} · {categoryName(movement.category)}
                  {/* Only where it changes what somebody would do: their own label, or none yet.
                      A caption on every row is a caption nobody reads. */}
                  {categorySource(movement.category_source) && (
                    <em className="source"> · {categorySource(movement.category_source)}</em>
                  )}
                </small>
              </span>
              {/* Money between the holder's own accounts. Banking already keeps it out of the
                  totals; the row still has to say so, or a 200 € line to the piggy bank reads as
                  200 € spent. */}
              {movement.is_internal && <span className="tag-internal">traspaso</span>}
              <span className={movement.amount_minor < 0 ? "out" : "in"}>
                {signed(movement.amount_minor, found?.currency ?? "EUR")}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
