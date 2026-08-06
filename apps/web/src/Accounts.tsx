/**
 * The account: its balance, and every movement with a way to narrow them.
 *
 * The search field is the reason this screen exists rather than a longer list on the home screen. It
 * sends a *merchant* — free text, because the ledger's shop names are data and no enum can hold them —
 * and the category chips send an identifier, which banking refuses if it does not know it. Two
 * different kinds of filter, and the difference is not cosmetic: one is a search, the other is a
 * vocabulary.
 *
 * The screen has one scope and everything on it obeys that scope: the balances at the top, the
 * transfers in the list, the caption under the tally. Whoever opened it decides what that scope is
 * — the tab bar means every account, tapping an account on the home screen means that one — and
 * "Todas" is always one tap away.
 */

import { useEffect, useRef, useState } from "react";

import { movements as fetchMovements, type Movements, type Summary } from "./api";
import type { Tab } from "./tabs";
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

type Props = {
  summary: Summary | null;
  searching: boolean;
  /**
   * The account the screen was opened about, when whoever opened it named one. Tapping an account
   * on the home screen names it; the tab bar does not, and that is every account.
   */
  account: string | null;
  onGo: (tab: Tab) => void;
};

export function Accounts({ summary, searching, account, onGo }: Props) {
  const [days, setDays] = useState(90);
  const [category, setCategory] = useState<string | null>(null);
  const [term, setTerm] = useState("");
  const [internal, setInternal] = useState(false);
  // Which account the whole screen is about. `null` is every one of them, which is the honest
  // default when nobody named one: a list silently about the current account, on a screen showing
  // three balances, is a figure whose scope the reader has to guess.
  //
  // Seeded from the destination and then owned here. Only seeded, deliberately: the screen is not
  // mounted while the home screen is up, so arriving from a tap is always a fresh mount — and the
  // destination changes under it when the search field opens, which must not undo a chip the
  // person has chosen since.
  const [only, setOnly] = useState<string | null>(account);
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
          account: only ?? undefined,
          acrossAccounts: only === null,
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
  }, [days, category, term, internal, only]);

  const shown = found?.movements ?? [];
  const holdings = summary?.holdings ?? [];
  // The balances follow the selection, like everything else on the screen. Somebody who tapped
  // "Hucha" is reading Hucha: three figures at the top with one account's movements under them is
  // the same screen saying two different things about what it is showing, and the figure is the
  // half people read first. "Todas" brings the rest back in one tap, which is why narrowing here
  // hides nothing anybody cannot recover.
  const about =
    only === null ? holdings : holdings.filter((holding) => holding.account_id === only);

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
      {holdings.length ? (
        <section className="holdings" aria-label="Tus cuentas">
          {about.map((holding) => (
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
          {/* Only with somewhere to move money to. A button that always refuses is worse than no
              button: it teaches somebody the app is broken, and they are right. Counted over every
              account the holder has rather than the ones on screen: narrowing the view does not
              take the other accounts away, and this is an action rather than a figure. */}
          {holdings.length > 1 && (
            <button className="move-money" onClick={() => onGo("traspaso")}>
              ⇄ Mover dinero entre cuentas
            </button>
          )}
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

      {/* Which account the screen is about, and the way back to all of them. Only with more than
          one — a row of one chip is a control that decides nothing. "Todas" first and lit whenever
          that is what the balances above are showing, which is what keeps the two halves of the
          screen saying the same thing. */}
      {holdings.length > 1 && (
        <div className="chips" role="group" aria-label="Cuenta">
          <button className={only === null ? "chip on" : "chip"} onClick={() => setOnly(null)}>
            Todas
          </button>
          {holdings.map((holding) => (
            <button
              key={holding.account_id}
              className={only === holding.account_id ? "chip on" : "chip"}
              onClick={() => setOnly(holding.account_id)}
            >
              {accountName(holding)}
            </button>
          ))}
        </div>
      )}

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
