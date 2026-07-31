-- ADR-002: movements are never modified or deleted; a correction is a new
-- compensating transaction.

CREATE FUNCTION reject_ledger_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'the ledger is immutable: % on % is not allowed, '
        'use a compensating transaction',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql
;
