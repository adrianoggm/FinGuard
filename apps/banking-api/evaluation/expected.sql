-- Calcula a mano lo que debería devolver cada caso de evaluation/queries.yaml.
--
-- A propósito NO usa las vistas, ni el compilador, ni el resolvedor de periodos: SQL
-- escrito aparte, sobre las tablas, con las fechas puestas de forma explícita. Si las
-- cifras esperadas salieran del motor que queremos medir, la evaluación no mediría
-- nada. Sus resultados son los que están escritos en queries.yaml.
--
--   docker exec -i finguard-postgres-1 psql -U finguard_banking -d finguard_banking \
--     -f - < evaluation/expected.sql

\pset footer off

WITH movimientos AS (
    SELECT a.account_reference,
           t.status,
           t.occurred_at,
           t.metadata ->> 'category' AS categoria,
           t.metadata ->> 'merchant_name' AS comercio,
           e.entry_type,
           e.amount_minor
    FROM ledger_entries e
    JOIN ledger_transactions t ON t.id = e.transaction_id
    JOIN accounts a ON a.id = e.account_id
    WHERE t.status = 'BOOKED'
)
SELECT caso, SUM(importe)::bigint AS total_minor, COUNT(*) AS movement_count
FROM (
    SELECT 'gasolina_julio' AS caso, amount_minor AS importe FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'gasolina_mes_pasado', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'supermercado_tres_meses', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'GROCERIES'
       AND occurred_at >= '2026-05-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-16 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'gasto_total_junio', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'ingresos_junio', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'CREDIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'neto_junio',
           CASE WHEN entry_type = 'CREDIT' THEN amount_minor ELSE -amount_minor END FROM movimientos
     WHERE account_reference = 'ES-0001'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'gasolina_julio_por_semana', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'TRANSPORT_FUEL'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'junio_por_categoria', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'supermercado_junio_por_comercio', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'GROCERIES'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'salud_esta_semana', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'HEALTH'
       AND occurred_at >= '2026-07-13 00:00 Europe/Madrid' AND occurred_at < '2026-07-16 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'ocio_julio_sin_lo_pendiente', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0001' AND entry_type = 'DEBIT' AND categoria = 'LEISURE'
       AND occurred_at >= '2026-07-01 00:00 Europe/Madrid' AND occurred_at < '2026-08-01 00:00 Europe/Madrid'
    UNION ALL
    SELECT 'gasto_junio_tercera_cuenta', amount_minor FROM movimientos
     WHERE account_reference = 'ES-0003' AND entry_type = 'DEBIT'
       AND occurred_at >= '2026-06-01 00:00 Europe/Madrid' AND occurred_at < '2026-07-01 00:00 Europe/Madrid'
) casos
GROUP BY caso
ORDER BY caso;

\echo
\echo -- desglose semanal de gasolina_julio_por_semana (lunes de cada semana)
SELECT to_char(date_trunc('week', t.occurred_at AT TIME ZONE 'Europe/Madrid'), 'YYYY-MM-DD') AS clave,
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
