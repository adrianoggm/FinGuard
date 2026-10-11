/**
 * The cards, drawn as cards, each one openable.
 *
 * Four digits and never a number: that is what the service sends, because a PAN belongs in a vault and
 * a person only needs enough to recognise which card is theirs.
 *
 * **Tapping a card shows its charges**, and that is not a nicety. A screen that states "437,92 € en 10
 * cargos" and cannot then show the ten is asking to be trusted, which is the objection this whole
 * product is built against. The same reason every figure in the chat carries its plan.
 *
 * Every figure says which window it is from, in words, next to the number. The service answers a
 * rolling window with its start date rather than claiming a month, and a total labelled "este mes" that
 * is really the last thirty days is wrong on the 1st by everything that happened on the 31st.
 *
 * A credit card gets a bar against its limit; a debit card does not, because its limit is the balance
 * and drawing a full bar for that would be inventing a ceiling.
 */

import { useEffect, useState } from "react";

import {
  cards as fetchCards,
  chargesOn,
  type Card,
  type Charge,
  type Wallet,
} from "./api";
import { categoryName, day, money } from "./format";
import { displayText } from "./displayText";
import { categoryIcon, Icon } from "./Icon";

const WINDOWS = [
  [30, "30 días"],
  [90, "3 meses"],
  [365, "1 año"],
] as const;

const KINDS: Record<string, string> = { DEBIT: "Débito", CREDIT: "Crédito" };
const STATUSES: Record<string, string> = {
  ACTIVE: "Activa",
  BLOCKED: "Bloqueada",
  EXPIRED: "Caducada",
};

export function Cards() {
  const [days, setDays] = useState(30);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    (async () => {
      try {
        const answer = await fetchCards(days);
        if (current) {
          setWallet(answer);
          setError(null);
        }
      } catch {
        // Not an empty wallet: an account with no cards and an account we could not ask about look
        // the same otherwise, and only one of them is worth showing as "no tienes tarjetas".
        if (current) setError("No he podido cargar tus tarjetas.");
      }
    })();
    return () => {
      current = false;
    };
  }, [days]);

  const label = WINDOWS.find(([value]) => value === days)?.[1] ?? `${days} días`;

  return (
    <div className="screen">
      <h1 className="screen-title">Tarjetas</h1>

      <div className="chips" role="group" aria-label="Periodo">
        {WINDOWS.map(([value, text]) => (
          <button
            key={value}
            className={days === value ? "chip on" : "chip"}
            onClick={() => setDays(value)}
          >
            {text}
          </button>
        ))}
      </div>

      {error && <p className="empty">{error}</p>}
      {wallet?.cards.length === 0 && <p className="empty">No tienes tarjetas.</p>}

      <ul className="wallet">
        {wallet?.cards.map((card) => (
          <li key={card.card_id}>
            <Plastic
              card={card}
              window={label}
              open={open === card.card_id}
              onToggle={() => setOpen(open === card.card_id ? null : card.card_id)}
            />
            {open === card.card_id && <ChargeList cardId={card.card_id} days={days} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

type PlasticProps = {
  card: Card;
  window: string;
  open: boolean;
  onToggle: () => void;
};

function Plastic({ card, window: label, open, onToggle }: PlasticProps) {
  const blocked = card.status !== "ACTIVE";
  const share =
    card.spending_limit_minor && card.spending_limit_minor > 0
      ? Math.min(100, Math.round((card.spent_minor / card.spending_limit_minor) * 100))
      : null;

  return (
    <article className={blocked ? "plastic blocked" : "plastic"}>
      <header>
        <span className="plastic-brand">{card.brand}</span>
        <span className="plastic-kind">{KINDS[card.kind] ?? card.kind}</span>
      </header>

      {/* Four groups, three of them masked. It reads as a card number without being one — there is
          no number here to leak, because the service never sent one. */}
      <p className="plastic-number">
        <span aria-hidden="true">•••• •••• ••••</span> {card.last_four}
      </p>

      <footer>
        <span>
          <small>Titular</small>
          {card.display_name}
        </span>
        <span>
          <small>Caduca</small>
          {card.expires}
        </span>
      </footer>

      <div className="plastic-spend">
        <p>
          <strong>{money(card.spent_minor)}</strong> en {card.charge_count}{" "}
          {card.charge_count === 1 ? "cargo" : "cargos"}
          {/* The window, beside the figure and not in a heading somewhere above it. */}
          <small> · {label}</small>
        </p>
        {share !== null && (
          <>
            <span className="track">
              <span className="fill" style={{ width: `${share}%` }} />
            </span>
            <small className="limit">
              {share}% de {money(card.spending_limit_minor ?? 0)}
            </small>
          </>
        )}
        {blocked && <p className="plastic-status">{STATUSES[card.status] ?? card.status}</p>}
        <button className="plastic-more" onClick={onToggle} aria-expanded={open}>
          {open ? "Ocultar los movimientos" : `Ver los ${card.charge_count} movimientos`}
        </button>
      </div>
    </article>
  );
}

function ChargeList({ cardId, days }: { cardId: string; days: number }) {
  const [charges, setCharges] = useState<Charge[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    (async () => {
      try {
        const answer = await chargesOn(cardId, days);
        if (current) {
          setCharges(answer.charges);
          setError(null);
        }
      } catch {
        if (current) setError("No he podido cargar los movimientos.");
      }
    })();
    return () => {
      current = false;
    };
  }, [cardId, days]);

  return (
    <section className="movements charges" aria-label="Movimientos de la tarjeta">
      {error && <p className="empty">{error}</p>}
      {!error && charges === null && <p className="empty">Cargando…</p>}
      {charges?.length === 0 && <p className="empty">Ningún cargo en este periodo.</p>}
      {charges && charges.length > 0 && (
        <ul>
          {charges.map((charge) => (
            <li key={charge.transaction_id}>
              <span className="glyph" aria-hidden="true">
                <Icon name={categoryIcon(charge.category)} />
              </span>
              <span className="what">
                <strong>{displayText(charge.merchant_name ?? charge.description)}</strong>
                <small>
                  {day(charge.occurred_at)} · {categoryName(charge.category)}
                </small>
              </span>
              {/* The minus is written here: the service sends unsigned amounts because on this list
                  nothing can be positive, and a sign in the data would be decoration. */}
              <span className="out">-{money(charge.amount_minor)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
