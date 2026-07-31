-- ADR-003: every transaction balances, and balances per currency.
-- 25 EUR debited against 25 USD credited adds up to zero and is not valid.
--
-- Loaded by migrations/versions/0001_ledger.py. Kept as SQL because it is the most
-- delicate logic in the repository, and inside a Python string it gets no syntax
-- highlighting and no linter.

CREATE FUNCTION assert_transaction_is_balanced() RETURNS TRIGGER AS $$
DECLARE
    unbalanced INTEGER;
    foreign_currencies INTEGER;
BEGIN
    SELECT COUNT(*) INTO unbalanced FROM (
        SELECT currency,
               SUM(CASE WHEN entry_type = 'DEBIT' THEN amount_minor
                        ELSE -amount_minor END) AS delta
        FROM ledger_entries
        WHERE transaction_id = NEW.transaction_id
        GROUP BY currency
    ) per_currency
    WHERE delta <> 0;

    IF unbalanced > 0 THEN
        RAISE EXCEPTION
            'transaction % is unbalanced: debits and credits do not match per currency',
            NEW.transaction_id;
    END IF;

    SELECT COUNT(*) INTO foreign_currencies
    FROM ledger_entries e
    JOIN accounts a ON a.id = e.account_id
    WHERE e.transaction_id = NEW.transaction_id
      AND e.currency <> a.currency;

    IF foreign_currencies > 0 THEN
        RAISE EXCEPTION
            'transaction %: entries in a currency other than their account',
            NEW.transaction_id;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql
;
