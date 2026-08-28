"""a confirmation cannot redirect a payment, over the columns the table has now

0006 put a trigger on `payment_intents` to stop a confirmation changing the payment underneath the
person who approved it, and said so in its own comment:

    The amount, the recipient and the account are what the user agreed to. Confirming has no
    business changing any of them, and a bug that did would move a different payment than the
    one that was previewed.

It then compared `account_id`, `contact_id`, `amount_minor` and `currency`. Those were every column
a destination had in 0006, when the only rail was Bizum and a recipient *was* a contact. 0012 added
`rail`, `recipient_name` and `recipient_iban` — the actual destination of a transfer — and 0017
added `to_account_id`. Neither redefined the function. "The recipient" quietly stopped being any of
the columns it checks, and the comment stayed.

Measured on the seeded database before this migration. An intent for 30,00 € to
`ES9121000418450200051332` in the name of Elena Marin, confirmed with one statement:

    UPDATE payment_intents
       SET status = 'CONFIRMED', transaction_id = ..., confirmed_at = NOW(),
           recipient_iban = 'ES6000491500051234567892', recipient_name = 'Somebody Else'
     WHERE id = ...

    UPDATE 1

    status    | recipient_name | recipient_iban           | amount_minor
    CONFIRMED | Somebody Else  | ES6000491500051234567892 |         3000

Every CHECK still holds, which is what makes this invisible: `payment_intents_destination` requires
a TRANSFER to *have* an IBAN and `payment_intents_iban_shape` requires it to look like one, and
neither pins its value. So the money goes to an account the holder never saw, in the single
statement that represents their approval, and nothing in the database objects.

This is not about a hostile caller — 0003 says plainly that banking does not authenticate its
callers. It is the failure 0006 was written to stop, and the trigger is the control that is
supposed to hold when the code above it is wrong.

**A whole-row comparison rather than four more columns in the OR chain.** Enumerating the missing
four is explicit, obvious, and exactly what already failed: the list was right when it was written
and silently wrong two migrations later, and the next migration to add a destination column would
escape it the same way. So the comparison inverts. It is now the whole row minus the columns a
confirmation legitimately writes — closed by default, so a column added and forgotten is guarded
rather than exposed, and the way anyone finds out is a refused UPDATE instead of a payment that
went somewhere else.

The exempt list is short, does not grow with the destination, and is read off the three writers in
`payment_repository` rather than guessed at:

- `confirm` writes `status`, `transaction_id` and `confirmed_at`.
- `cancel` and `expire_stale` write `status` alone.
- `created_at` and `expires_at` are written by nothing after prepare. They are exempt because three
  integration tests age a row to make it stale — `expires_at > created_at` applies to updates too,
  so both move together — and that is how the suite makes an hour pass without waiting one.

Everything else is immutable: `id`, `account_id`, `contact_id`, `amount_minor`, `currency`,
`concept`, `idempotency_key`, `rail`, `recipient_name`, `recipient_iban`, `to_account_id`.

**The check moves above the `NEW.status = OLD.status` early return, and without that the rest of
this migration fixes half the hole.** The function's first statement returned when the status was
unchanged, so an UPDATE that rewrote the IBAN and left the status alone was never inspected at all.
That shape is worse than the headline: the intent is redirected while it waits, and the
confirmation that follows is entirely legitimate, so there is no bad statement to find afterwards.
The early return stays where it is, one statement lower — it is right about the *transition* rules,
which have nothing to say about an UPDATE that does not transition, and keeping it is what lets the
sweep tests age an already-CONFIRMED row without tripping "already CONFIRMED, and that is
terminal".

**`IS DISTINCT FROM` rather than `<>`, which repairs a column that was already on the list.**
`NEW.contact_id <> OLD.contact_id` is a three-valued comparison: against NULL it evaluates to NULL,
not TRUE, so the entire OR chain went NULL and the IF was not taken. Setting a favourite's contact
id to NULL therefore passed a guard that names the column. It became reachable in 0012, which made
`contact_id` nullable because a transfer to a typed account has no contact — the same migration
that introduced the other three gaps, for the same reason. Comparing jsonb has no such escape: an
absent value is JSON null, and JSON null is a value.

**What the exemption still admits, stated rather than implied.** It is the whole remaining surface
and two entries on it are wider than they look:

- `expires_at` can move forward, reviving an intent the holder let lapse — `confirm` re-checks the
  expiry against NOW() and would believe the new figure. Exempt anyway, because the suite treats an
  intent's clock as writable and three tests depend on it.
- `transaction_id` can be repointed on an already-CONFIRMED row, since that UPDATE does not change
  the status and so reaches the early return. The CHECK constraints keep it non-NULL and the ledger
  is immutable under 0001, so this breaks the audit link rather than moving money.

Neither is the failure this migration is about, and folding either one in would make it about three
things. They are named here so the exempt list is not mistaken for a claim of completeness.

**A future backfill of a guarded column now has to say so out loud.** `UPDATE payment_intents SET
rail = ...` across the table would be refused, where 0012's `ADD COLUMN ... DEFAULT 'BIZUM'` was
not — an added column's default does not fire row triggers. A migration that genuinely needs to
rewrite one of these drops the trigger, writes, and puts it back. That is three lines, and it is
the right price for a column the schema claims the holder agreed to.

The body is replaced rather than amended and 0006's file is untouched, so the downgrade is
literally "the definition 0006 shipped": `CREATE OR REPLACE` re-running that same file. The trigger
itself is not recreated in either direction — `payment_intents_lifecycle` already points at this
function by name.

Revision ID: 0019_intent_immutability
Revises: 0018_card_charge_layers
Create Date: 2026-08-28
"""

from pathlib import Path

from alembic import op

revision = "0019_intent_immutability"
down_revision = "0018_card_charge_layers"
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute(_sql("0019_assert_intent_transition.sql"))


def downgrade() -> None:
    # 0006's file, exactly as it shipped. Left alone on purpose: the migrations here are a
    # history, and a file edited in place stops being a record of what was run.
    op.execute(_sql("0006_assert_intent_transition.sql"))
