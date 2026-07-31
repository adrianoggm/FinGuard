-- Works out by hand what each case in evaluation/queries.yaml should return.
--
-- It deliberately does NOT use the views, the compiler or the period resolver: SQL
-- written separately, over the tables, with the dates spelled out. If the expected
-- figures came from the engine we want to measure, the evaluation would measure
-- nothing. Its results are the ones written into queries.yaml.
--
--   docker exec -i finguard-postgres-1 psql -U finguard_banking -d finguard_banking \
--     -f - < evaluation/expected.sql

\pset footer off

WITH movements AS (
    SELECT a.account_reference,
           t.status,
           t.occurred_at,
           t.metadata ->> 'category' AS category,
           t.metadata ->> 'merchant_name' AS merchant,
           e.entry_type,
           e.amount_minor
    FROM ledger_entries e
    JOIN ledger_transactions t ON t.id = e.transaction_id
    JOIN accounts a ON a.id = e.account_id
    WHERE t.status = 'BOOKED'
)
SELECT case_id, SUM(amount)::bigint AS total_minor, COUNT(*) AS movement_count
FROM (
    SELECT 'fuel_july' AS case_id, amount_minor AS amount FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'fuel_last_month', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'groceries_last_three_months', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'GROCERIES'
       AND occurred_at >= '2026-05-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-16 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'total_spending_june', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'income_june', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'CREDIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'net_june',
           CASE WHEN entry_type = 'CREDIT' THEN amount_minor ELSE -amount_minor END FROM movements
     WHERE account_reference = 'ES-0001'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'fuel_july_by_week', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'june_by_category', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'groceries_june_by_merchant', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'GROCERIES'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'health_this_week', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'HEALTH'
       AND occurred_at >= '2026-07-13 00:00 Europe/Madrid' AND occurred_at < '2026-07-16 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'leisure_july_excluding_pending', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'LEISURE'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'spending_june_third_account', amount_minor FROM movements
     WHERE account_reference = 'ES-0003' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
) cases
GROUP BY case_id
ORDER BY case_id;

\echo
\echo -- weekly breakdown for fuel_july_by_week (Monday of each week)
SELECT to_char(date_trunc('week', t.occurred_at AT TIME ZONE 'Europe/Madrid'), 'YYYY-MM-DD') AS key,
       SUM(e.amount_minor)::bigint AS total_minor,
       COUNT(*) AS movement_count
FROM ledger_entries e
JOIN ledger_transactions t ON t.id = e.transaction_id
JOIN accounts a ON a.id = e.account_id
WHERE a.account_reference = 'ES-0001' AND t.status = 'BOOKED' AND e.entry_type = 'DEBIT'
  AND t.metadata ->> 'category' = 'TRANSPORT_FUEL'
  AND t.occurred_at >= '2026-07-01 00:00 Europe/Madrid'
  AND t.occurred_at < '2026-08-01 00:00 Europe/Madrid'
GROUP BY 1 ORDER BY 1;
