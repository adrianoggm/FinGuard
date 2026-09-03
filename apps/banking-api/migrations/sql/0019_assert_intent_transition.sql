-- 0006's guard, over the columns this table has now rather than the ones it had then.
--
-- 0006 compared `account_id`, `contact_id`, `amount_minor` and `currency`: every column a
-- destination had when the only rail was Bizum and a recipient *was* a contact. 0012 added
-- `rail`, `recipient_name` and `recipient_iban`; 0017 added `to_account_id`. Neither redefined
-- this function, so the comment below went on promising something it had stopped checking.
--
-- **Closed by default, and that is why this is a row comparison and not four more `<>` clauses.**
-- The list that has to be kept up to date is now the one this service writes *after* the holder
-- has agreed — `confirm`, `cancel`, the expiry sweep — and not the one the holder agreed to,
-- which grows every time payments learn a new kind of destination. Forgetting a column now
-- fails loudly on the next UPDATE; forgetting one under the old enumeration failed silently,
-- and did, for two migrations.
--
-- **The comparison runs before the early return**, which is the other half of the fix. This
-- function used to return at the first statement whenever the status was unchanged, so an
-- UPDATE that rewrote the IBAN and left the status alone was never inspected — the intent could
-- be redirected while it waited and then confirmed entirely legitimately. The early return stays,
-- one statement lower: it is right about the *transition* rules, which have nothing to say about
-- an UPDATE that does not transition.
--
-- **jsonb rather than `<>`, because `<>` has a three-valued escape.**
-- `NEW.contact_id <> OLD.contact_id` is NULL when either side is NULL, and NULL is not TRUE, so
-- the whole OR chain went NULL and the IF was not taken: nulling a favourite's contact id passed
-- a guard that named the column. An absent jsonb value is JSON null, and JSON null is a value.
--
-- Only OLD and NEW are read, never a table, so none of this is subject to the row-level security
-- trap 0007 documents — the one that made `assert_transaction_is_balanced` sum zero rows.

CREATE OR REPLACE FUNCTION assert_intent_transition() RETURNS TRIGGER AS $$
DECLARE
    -- Everything this service writes to an intent after it exists, read off the three writers
    -- in `payment_repository` rather than guessed: `confirm` sets the first three, `cancel` and
    -- `expire_stale` set `status` alone. The two timestamps are written once at prepare and by
    -- nothing afterwards; they are exempt because ageing a row is how the integration suite
    -- makes an hour pass without waiting one. Every other column is what the holder agreed to.
    mutable CONSTANT text[] := ARRAY[
        'status', 'transaction_id', 'confirmed_at', 'created_at', 'expires_at'
    ];
    agreed  CONSTANT jsonb := to_jsonb(OLD) - mutable;
    offered CONSTANT jsonb := to_jsonb(NEW) - mutable;
    changed text;
BEGIN
    -- The amount, the recipient and the account are what the user agreed to. Confirming has no
    -- business changing any of them, and a bug that did would move a different payment than the
    -- one that was previewed.
    IF offered IS DISTINCT FROM agreed THEN
        -- Which columns, because "cannot change what it is for" on its own sends the reader to
        -- diff two rows by hand at the moment a payment has just been refused.
        SELECT string_agg(was.name, ', ' ORDER BY was.name) INTO changed
        FROM jsonb_each(agreed) AS was(name, value)
        WHERE offered -> was.name IS DISTINCT FROM was.value;

        RAISE EXCEPTION 'payment intent % cannot change what it is for: %', OLD.id, changed
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    IF OLD.status <> 'AWAITING_CONFIRMATION' THEN
        RAISE EXCEPTION
            'payment intent % is already %, and that is terminal', OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status NOT IN ('CONFIRMED', 'CANCELLED', 'EXPIRED') THEN
        RAISE EXCEPTION 'an intent may only become CONFIRMED, CANCELLED or EXPIRED, not %',
            NEW.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
