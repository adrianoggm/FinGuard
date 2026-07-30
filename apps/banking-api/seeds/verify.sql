-- Comprueba que el dataset cargado es utilizable. Falla en lugar de avisar: si algo
-- no cuadra, el benchmark de la Fase 2 mediría sobre datos mentirosos.

DO $$
DECLARE
    descuadres INTEGER;
    monedas_ajenas INTEGER;
    huerfanas INTEGER;
BEGIN
    SELECT COUNT(*) INTO descuadres FROM (
        SELECT t.id,
               SUM(CASE WHEN e.entry_type = 'DEBIT' THEN e.amount_minor
                        ELSE -e.amount_minor END) AS delta
        FROM ledger_transactions t
        JOIN ledger_entries e ON e.transaction_id = t.id
        GROUP BY t.id
    ) por_transaccion WHERE delta <> 0;

    IF descuadres > 0 THEN
        RAISE EXCEPTION 'hay % transacciones descuadradas', descuadres;
    END IF;

    SELECT COUNT(*) INTO monedas_ajenas
    FROM ledger_entries e JOIN accounts a ON a.id = e.account_id
    WHERE e.currency <> a.currency;

    IF monedas_ajenas > 0 THEN
        RAISE EXCEPTION 'hay % asientos en una moneda distinta a la de su cuenta', monedas_ajenas;
    END IF;

    SELECT COUNT(*) INTO huerfanas
    FROM ledger_transactions t
    WHERE NOT EXISTS (SELECT 1 FROM ledger_entries e WHERE e.transaction_id = t.id);

    IF huerfanas > 0 THEN
        RAISE EXCEPTION 'hay % transacciones sin asientos', huerfanas;
    END IF;

    RAISE NOTICE 'dataset correcto: cuadra por transaccion, moneda coherente, sin huerfanas';
END $$;

\echo
\echo == volumen
SELECT
    (SELECT COUNT(*) FROM customers)           AS clientes,
    (SELECT COUNT(*) FROM accounts)            AS cuentas,
    (SELECT COUNT(*) FROM ledger_transactions) AS transacciones,
    (SELECT COUNT(*) FROM ledger_entries)      AS asientos;

\echo
\echo == por estado y periodo
SELECT status,
       COUNT(*) AS transacciones,
       MIN(occurred_at)::date AS desde,
       MAX(occurred_at)::date AS hasta
FROM ledger_transactions GROUP BY status ORDER BY status;

\echo
\echo == saldo por cuenta, derivado de los asientos
SELECT a.account_reference,
       c.full_name,
       SUM(CASE WHEN e.entry_type = 'CREDIT' THEN e.amount_minor
                ELSE -e.amount_minor END) / 100.0 AS saldo
FROM accounts a
JOIN customers c ON c.id = a.customer_id
LEFT JOIN ledger_entries e ON e.account_id = a.id
LEFT JOIN ledger_transactions t ON t.id = e.transaction_id AND t.status = 'BOOKED'
WHERE t.id IS NOT NULL OR e.id IS NULL
GROUP BY a.account_reference, c.full_name
ORDER BY a.account_reference;

\echo
\echo == gasto por categoria (solo salidas de cuentas de clientes)
SELECT t.metadata ->> 'category' AS categoria,
       COUNT(*) AS movimientos,
       SUM(e.amount_minor) / 100.0 AS total
FROM ledger_entries e
JOIN ledger_transactions t ON t.id = e.transaction_id
JOIN accounts a ON a.id = e.account_id
WHERE e.entry_type = 'DEBIT'
  AND t.status = 'BOOKED'
  AND a.account_reference <> 'ES-COMPENSACION'
GROUP BY 1 ORDER BY 3 DESC;
