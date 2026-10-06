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
           -- The same order the read view resolves: the holder's opinion, then this service's,
           -- then the bank's. Written out here rather than read from the view, because the point
           -- of this file is to compute the expectations *separately* from the engine — but
           -- separately means different SQL, not a different rule.
           COALESCE(mc.category, sc.category, t.metadata ->> 'category') AS category,
           t.metadata ->> 'merchant_name' AS merchant,
           e.entry_type,
           e.amount_minor
    FROM ledger_entries e
    JOIN ledger_transactions t ON t.id = e.transaction_id
    JOIN accounts a ON a.id = e.account_id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = t.id AND mc.account_id = e.account_id
    LEFT JOIN movement_classifications sc
           ON sc.transaction_id = t.id AND sc.account_id = e.account_id
    WHERE t.status = 'BOOKED'
      -- Money the holder moved between their own accounts. Excluded here for the same reason the
      -- engine excludes it: "how much did I spend" means money that left, and counting a standing
      -- order to the piggy bank makes every total in this file disagree with every screen.
      AND t.transaction_type <> 'INTERNAL_TRANSFER'
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
    -- Three months back from the 15th, not from the 1st of May. LAST_N_MONTHS used to count the
    -- current month from its 1st, which made `n=1` a fortnight and identical to THIS_MONTH; it
    -- rolls back from the reference day now, like LAST_N_DAYS beside it. This literal is the only
    -- place in the evaluation that spelled the old arithmetic out.
    SELECT 'groceries_last_three_months', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'GROCERIES'
       AND occurred_at >= '2026-04-15 00:00 Europe/Madrid' AND occurred_at < '2026-07-16 00:00 Europe/Madrid'
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
    SELECT 'leisure_since_july_excluding_pending', amount_minor FROM movements
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND category = 'LEISURE'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-10-06 00:00 Europe/Madrid'
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
