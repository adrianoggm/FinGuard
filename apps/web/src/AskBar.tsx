/**
 * Where the assistant lives: a bar above the tabs, on every screen.
 *
 * It replaces a floating sparkle button. The conversation is what this app is about, and a round
 * button in a corner made it look like an add-on — while covering whatever row was under it.
 *
 * Two suggestions per screen, about what that screen shows. Tapping one opens the conversation with
 * the sentence written and not sent, as the Bizum and transfer screens already do: nothing is asked
 * of the bank or the model until the person sends it.
 */

import { Icon } from "./Icon";
import { tabOf, type Tab } from "./tabs";

const SUGGESTIONS: Record<string, readonly [string, string]> = {
  inicio: ["¿En qué gasté más este mes?", "¿Cuánto tengo en la Hucha?"],
  cuentas: ["Enséñame los últimos gastos", "¿Cuánto he ingresado este mes?"],
  tarjetas: ["¿Cuánto gasté con tarjeta este mes?", "Mi mayor compra del mes"],
  finanzas: ["Compara este mes con el anterior", "Ponlo en un gráfico"],
  bizum: ["Hazle un bizum a Marta Solis", "¿Cuánto le mandé a Marta?"],
};

type Props = {
  where: Tab;
  onAsk: (draft: string) => void;
};

export function AskBar({ where, onAsk }: Props) {
  const suggestions = SUGGESTIONS[tabOf(where)] ?? SUGGESTIONS.inicio;
  return (
    <div className="askbar">
      <button className="askbar-field" onClick={() => onAsk("")}>
        <span className="askbar-mark">
          <Icon name="spark" size={16} />
        </span>
        Pregúntale a FinGuard
      </button>
      <div className="askbar-chips" aria-label="Sugerencias">
        {suggestions?.map((sentence) => (
          <button key={sentence} className="askbar-chip" onClick={() => onAsk(sentence)}>
            {sentence}
          </button>
        ))}
      </div>
    </div>
  );
}
