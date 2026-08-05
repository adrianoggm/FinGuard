-- A transaction may only grow while it is PENDING, and may only leave PENDING once.
--
-- ADR-002 says the ledger is immutable and migration 0001 implemented "no UPDATE, no DELETE".
-- That left the hole this closes: nothing stopped a new debit and a new credit being appended
-- to a transaction that was already BOOKED. The pair keeps it balanced, so the balance trigger
-- passes, and the account's balance changes after the fact. A booked movement that can still
-- gain entries is not a record of what happened.

CREATE OR REPLACE FUNCTION assert_transaction_accepts_entries() RETURNS TRIGGER AS $$
DECLARE
    parent_status VARCHAR(30);
BEGIN
    SELECT status INTO parent_status
    FROM ledger_transactions
    WHERE id = NEW.transaction_id;

    IF parent_status IS DISTINCT FROM 'PENDING' THEN
        RAISE EXCEPTION
            'transaction % is %, and only a PENDING transaction accepts entries',
            NEW.transaction_id, parent_status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
