-- Checks that the loaded dataset is usable. It fails instead of warning: if something
-- does not add up, the phase 2 benchmark would be measuring over lying data.

DO $$
DECLARE
    unbalanced INTEGER;
    foreign_currencies INTEGER;
    orphans INTEGER;
BEGIN
    SELECT COUNT(*) INTO unbalanced FROM (
        SELECT t.id,
               SUM(CASE WHEN e.entry_type = 'DEBIT' THEN e.amount_minor
                        ELSE -e.amount_minor END) AS delta
        FROM ledger_transactions t
        JOIN ledger_entries e ON e.transaction_id = t.id
        GROUP BY t.id
    ) per_transaction WHERE delta <> 0;

    IF unbalanced > 0 THEN
        RAISE EXCEPTION '% transactions do not balance', unbalanced;
    END IF;

    SELECT COUNT(*) INTO foreign_currencies
    FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
    WHERE e.currency <> a.currency;

    IF foreign_currencies > 0 THEN
        RAISE EXCEPTION '% entries are in a currency other than their account', foreign_currencies;
    END IF;

    SELECT COUNT(*) INTO orphans
    FROM ledger_transactions t
    WHERE NOT EXISTS (SELECT 1 FROM ledger_entries e WHERE e.transaction_id = t.id);

    IF orphans > 0 THEN
        RAISE EXCEPTION '% transactions have no entries', orphans;
    END IF;

    RAISE NOTICE 'dataset is sound: balances per transaction, coherent currency, no orphans';
END $$;

\echo
\echo == volume
SELECT
    (SELECT COUNT(*) FROM customers)           AS customers,
    (SELECT COUNT(*) FROM accounts)            AS accounts,
    (SELECT COUNT(*) FROM ledger_transactions) AS transactions,
    (SELECT COUNT(*) FROM ledger_entries)      AS entries;

\echo
\echo == by status and period
SELECT status,
       COUNT(*) AS transactions,
       MIN(occurred_at)::date AS first,
       MAX(occurred_at)::date AS last
FROM ledger_transactions GROUP BY status ORDER BY status;

\echo
\echo == balance per account, derived from the entries
SELECT a.account_reference,
       c.full_name,
       SUM(CASE WHEN e.entry_type = 'CREDIT' THEN e.amount_minor
                ELSE -e.amount_minor END) / 100.0 AS balance
FROM accounts a
JOIN customers c ON c.id = a.customer_id
LEFT JOIN ledger_entries e ON e.account_id = a.id
LEFT JOIN ledger_transactions t ON t.id = e.transaction_id AND t.status = 'BOOKED'
WHERE t.id IS NOT NULL OR e.id IS NULL
GROUP BY a.account_reference, c.full_name
ORDER BY a.account_reference;

\echo
\echo == spending by category (only outflows from customer accounts)
SELECT t.metadata ->> 'category' AS category,
       COUNT(*) AS movements,
       SUM(e.amount_minor) / 100.0 AS total
FROM ledger_entries e
JOIN ledger_transactions t ON t.id = e.transaction_id
JOIN accounts a ON a.id = e.account_id
WHERE e.entry_type = 'DEBIT'
  AND t.status = 'BOOKED'
  AND a.account_reference <> 'ES-COMPENSACION'
GROUP BY 1 ORDER BY 3 DESC;
