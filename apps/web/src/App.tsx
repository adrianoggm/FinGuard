/**
 * The phone. A bank app with four screens and a floating button that opens conversation mode.
 *
 * Framed as a device on a wide screen because that is the only way this is meant to be looked at: it
 * is a phone app, and a desktop-shaped version of it would be a different product. On an actual phone
 * the frame gets out of the way.
 *
 * The sheet rises over whichever screen you are on rather than replacing it. The point of a floating
 * button is that you do not leave what you were doing — and the figures staying visible behind the
 * conversation are what make asking about your money part of the app instead of a chat bolted onto it.
 *
 * The tab state lives here and nowhere else. There is no router: four destinations reached by tapping
 * do not need URLs, and a router would be the second dependency in the project for something a
 * `useState` already does.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { configured, summary as fetchSummary, type Summary } from "./api";
import { Accounts } from "./Accounts";
import { Bizum } from "./Bizum";
import { Conversation } from "./Conversation";
import { Finances } from "./Finances";
import { Home } from "./Home";
import { TABS, tabOf, type Tab } from "./tabs";

export function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [talking, setTalking] = useState(false);
  const [where, setWhere] = useState<Tab>("inicio");
  const app = useRef<HTMLElement>(null);
  const opened = useRef(false);

  const load = useCallback(async () => {
    if (!configured) {
      setError("Falta el token");
      return;
    }
    try {
      setSummary(await fetchSummary());
      setError(null);
    } catch {
      // The figure is left absent rather than replaced by a zero: a balance of 0,00 € is a statement
      // about somebody's money, and "we could not ask" is not that statement.
      setError("No disponible");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    // The app is not scrollable until the figures arrive, and once it is, Chrome restores whatever
    // position the container had before a reload. On a bank app that means opening halfway down the
    // movement list instead of on the balance. Once, on the first load only.
    if (summary && !opened.current) {
      opened.current = true;
      app.current?.scrollTo({ top: 0 });
    }
  }, [summary]);

  const go = useCallback((tab: Tab) => {
    setWhere(tab);
    // A new screen starts at its top. Keeping the previous scroll position across a tab change is how
    // a screen opens on its own middle.
    app.current?.scrollTo({ top: 0 });
  }, []);

  return (
    <div className="stage">
      <div className="phone">
        <StatusBar />
        <main ref={app} className={talking ? "app dimmed" : "app"}>
          {where === "inicio" && (
            <Home
              summary={summary}
              error={error}
              holder="Laura"
              onGo={go}
              onAsk={() => setTalking(true)}
            />
          )}
          {tabOf(where) === "cuentas" && (
            <Accounts summary={summary} searching={where === "cuentas-buscar"} />
          )}
          {where === "finanzas" && <Finances />}
          {where === "bizum" && <Bizum onPaid={load} />}
        </main>

        {!talking && (
          <button className="fab" onClick={() => setTalking(true)} aria-label="Hablar con FinGuard">
            <span aria-hidden="true">✨</span>
          </button>
        )}

        {talking && (
          <>
            <div className="scrim" onClick={() => setTalking(false)} />
            <Conversation onClose={() => setTalking(false)} onSettled={load} />
          </>
        )}

        <nav className="tabs" aria-label="Secciones">
          {TABS.map(([tab, label, icon]) => (
            <button
              key={tab}
              className={tabOf(where) === tab ? "tab active" : "tab"}
              onClick={() => go(tab)}
              aria-current={tabOf(where) === tab ? "page" : undefined}
            >
              <span aria-hidden="true">{icon}</span>
              <small>{label}</small>
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

/** Chrome. A phone has one, and without it this reads as a web page in a rounded box. */
function StatusBar() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const tick = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(tick);
  }, []);
  const clock = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit" }).format(now);
  return (
    <div className="status" aria-hidden="true">
      <span>{clock}</span>
      <span className="status-right">
        {/* Drawn rather than typed. Box-drawing characters for a signal meter came out as a black
            blob in a system font, which is the sort of thing that only shows up in a screenshot. */}
        <svg width="17" height="11" viewBox="0 0 17 11" fill="currentColor">
          <rect x="0" y="7" width="3" height="4" rx="1" />
          <rect x="4.7" y="5" width="3" height="6" rx="1" />
          <rect x="9.4" y="2.5" width="3" height="8.5" rx="1" />
          <rect x="14" y="0" width="3" height="11" rx="1" />
        </svg>
        <svg width="24" height="11" viewBox="0 0 24 11" fill="none">
          <rect x="0.5" y="0.5" width="20" height="10" rx="2.5" stroke="currentColor" />
          <rect x="2" y="2" width="15" height="7" rx="1.5" fill="currentColor" />
          <path d="M22 4v3a2 2 0 000-3z" fill="currentColor" />
        </svg>
      </span>
    </div>
  );
}
