"""row-level security was bypassed by its own write policy

0003 and 0005 each paired a SELECT policy restricting rows to the caller's account with a write
policy spelled `FOR ALL USING (true)`. PostgreSQL combines permissive policies with **OR**, and
`FOR ALL` includes SELECT — so every SELECT evaluated `account_id = current_setting(...) OR true`
and saw everything.

Measured on the seeded database before this migration, with Laura's account declared:

    contacts        8 rows visible   (Laura has 4; the other four are Diego's and Nuria's)
    ledger_entries  3248 rows visible   (every entry in the database)

**The exposure was narrower than that reads, and being precise about it matters.** The figures
above are the *owner* — `finguard_query_reader` cannot reach those tables at all, holding SELECT on
the two views and nothing else. So the bypass never affected the query path, which is the threat
this was defending against. What it affected was the owner: banking's own write path, which is
trusted, and which 0003 had only just subjected to policies with FORCE.

The bug is real and the fix is right regardless. A permissive `FOR ALL` policy silently cancelling
a restricting SELECT policy is a mistake that costs nothing to fix now and everything the day a
role gets table access.

The fix is per-command write policies. INSERT, UPDATE and DELETE each get their own permissive
policy, and none of them mentions SELECT, so the only policy governing reads is the one that
restricts them. Writing still works for the owner: an UPDATE's row visibility comes from the
UPDATE policy, not from the SELECT one.

**And FORCE ROW LEVEL SECURITY comes off, which is the second half of the same lesson.** 0003 added
it so the owner would be subject to the policies too. Once the policies actually restricted
anything, that broke the ledger's own invariants — because **row-level security applies inside
trigger functions**, and every integrity check here reads the table it protects:

- `assert_lifecycle_transition` counts a transaction's entries and, seeing none, refused to book
  anything at all. Loud, and it is how this was found.
- `assert_transaction_is_balanced` sums a transaction's entries. Seeing none, the sum is zero, and
  **zero balances.** An unbalanced transaction would have been accepted in silence. That is the
  dangerous direction, and it is the reason this migration is a correction rather than a tweak.

An integrity check has to see the whole transaction regardless of who is asking; it is not
answering a caller's question. So the owner — the service's own write path, which runs those
triggers — is no longer subject to RLS.

**Which leaves row-level security on these tables dormant, and worth saying plainly rather than
implying otherwise.** No role in this deployment is both subject to the policies and able to reach
the tables: the reader lacks the privilege, the owner is exempt. What enforces isolation today is
the privilege barrier plus the views' WHERE clause, and there are tests for both. The policies are
correct, in place, and enforcing nothing — which is the right state for something that becomes
load-bearing the moment a migration grants table access, and the wrong thing to describe as a
second line of defence in the meantime.

Revision ID: 0007_fix_rls_write_policies
Revises: 0006_payment_intents
Create Date: 2026-08-03
"""

from alembic import op

revision = "0007_fix_rls_write_policies"
down_revision = "0006_payment_intents"
branch_labels = None
depends_on = None

# Every table whose rows belong to one account.
TABLES = ("accounts", "ledger_entries", "contacts", "payment_intents")


def upgrade() -> None:
    for table in TABLES:
        # See the module docstring: with the policies working, this made the ledger's own
        # integrity triggers see an empty table, and one of them fails safe while the other
        # fails silent.
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_writes ON {table}")
        # Split by command, and SELECT is deliberately absent: it is governed only by the
        # own-account policy, which is the whole point.
        op.execute(f"CREATE POLICY {table}_inserts ON {table} FOR INSERT WITH CHECK (true)")
        op.execute(
            f"CREATE POLICY {table}_updates ON {table} FOR UPDATE USING (true) WITH CHECK (true)"
        )
        op.execute(f"CREATE POLICY {table}_deletes ON {table} FOR DELETE USING (true)")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_deletes ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_updates ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_inserts ON {table}")
        op.execute(
            f"CREATE POLICY {table}_writes ON {table} FOR ALL USING (true) WITH CHECK (true)"
        )
