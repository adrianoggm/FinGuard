-- A log the service can edit proves nothing about the service.
--
-- The message deliberately does not offer a way round it: unlike the ledger, where a mistake is
-- corrected with a compensating entry, there is nothing to compensate here. A wrong audit row is
-- history, and the fix is another row saying so.

CREATE FUNCTION reject_audit_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'the audit trail is append-only: % on % is not allowed',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql
;
