-- Which status changes are legal. PENDING may become BOOKED or CANCELLED; both are
-- terminal, so a correction is a compensating transaction rather than an edit — which
-- leaves the mistake and the fix both in the record.

CREATE OR REPLACE FUNCTION assert_lifecycle_transition() RETURNS TRIGGER AS $$
BEGIN
    -- Everything except the status is already immutable: 0001's reject_ledger_mutation covers
    -- the other columns. This is only about which status changes are legal.
    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    IF OLD.status <> 'PENDING' THEN
        -- BOOKED and CANCELLED are terminal. A correction is a compensating transaction, which
        -- leaves both the mistake and the fix in the record — the whole point of a ledger.
        RAISE EXCEPTION
            'transaction % is %, which is terminal; correct it with a compensating transaction',
            OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status NOT IN ('BOOKED', 'CANCELLED') THEN
        RAISE EXCEPTION 'PENDING may only become BOOKED or CANCELLED, not %', NEW.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- Booking asserts the balance again rather than trusting that the entries were checked
    -- when they landed. They were, by the deferred constraint on insert — but that check is
    -- per statement and this one is per transition, and the transition is what makes the
    -- figures visible to the read views.
    IF NEW.status = 'BOOKED' AND EXISTS (
        SELECT 1
        FROM ledger_entries
        WHERE transaction_id = NEW.id
        GROUP BY currency
        HAVING SUM(CASE WHEN entry_type = 'CREDIT' THEN amount_minor ELSE -amount_minor END) <> 0
    ) THEN
        RAISE EXCEPTION 'transaction % does not balance and cannot be booked', NEW.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status = 'BOOKED' AND NOT EXISTS (
        SELECT 1 FROM ledger_entries WHERE transaction_id = NEW.id
    ) THEN
        -- Zero entries balance trivially. A booked transaction with none is a movement that
        -- moved nothing, and it would appear in the ledger as a real event.
        RAISE EXCEPTION 'transaction % has no entries and cannot be booked', NEW.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
