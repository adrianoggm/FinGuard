type Props = {
  kind: "bizum" | "transfer";
  onContinue: (draft: string) => void;
};

const DRAFTS = {
  bizum: "Quiero hacer un Bizum",
  transfer: "Quiero hacer un traspaso entre mis cuentas",
} as const;

/** Money actions start in the canonical conversation so their drafts and confirmations share its graph state. */
export function MoneyAction({ kind, onContinue }: Props) {
  const title = kind === "bizum" ? "Bizum" : "Traspaso entre cuentas";

  return (
    <section className="screen">
      <h1 className="screen-title">{title}</h1>
      <section className="card">
        <p>
          Prepararemos la operación en tu conversación. Allí podrás revisar el destinatario,
          el importe y la cuenta antes de confirmar.
        </p>
        <button className="primary" onClick={() => onContinue(DRAFTS[kind])}>
          Preparar {kind === "bizum" ? "Bizum" : "traspaso"}
        </button>
      </section>
    </section>
  );
}
