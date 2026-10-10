-- Exact receipt data may be added once, in the same transition that confirms and books the intent.
-- It is immutable thereafter, just like the destination and amount the holder approved.
CREATE OR REPLACE FUNCTION assert_intent_transition() RETURNS TRIGGER AS $$
DECLARE
    mutable CONSTANT text[] := ARRAY[
        'status', 'transaction_id', 'confirmed_at', 'created_at', 'expires_at', 'receipt_payload'
    ];
    agreed  CONSTANT jsonb := to_jsonb(OLD) - mutable;
    offered CONSTANT jsonb := to_jsonb(NEW) - mutable;
    changed text;
BEGIN
    IF OLD.receipt_payload IS DISTINCT FROM NEW.receipt_payload AND NOT (
        OLD.receipt_payload IS NULL
        AND NEW.receipt_payload IS NOT NULL
        AND OLD.status = 'AWAITING_CONFIRMATION'
        AND NEW.status = 'CONFIRMED'
    ) THEN
        RAISE EXCEPTION 'payment intent % receipt may only be recorded when confirmed', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF offered IS DISTINCT FROM agreed THEN
        SELECT string_agg(was.name, ', ' ORDER BY was.name) INTO changed
        FROM jsonb_each(agreed) AS was(name, value)
        WHERE offered -> was.name IS DISTINCT FROM was.value;

        RAISE EXCEPTION 'payment intent % cannot change what it is for: %', OLD.id, changed
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF OLD.status = 'AWAITING_CONFIRMATION'
       AND NEW.status = 'CONFIRMED'
       AND NEW.receipt_payload IS NULL THEN
        RAISE EXCEPTION 'payment intent % cannot be confirmed without its exact receipt', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status = OLD.status THEN
        RETURN NEW;
    END IF;

    IF OLD.status <> 'AWAITING_CONFIRMATION' THEN
        RAISE EXCEPTION
            'payment intent % is already %, and that is terminal', OLD.id, OLD.status
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    IF NEW.status NOT IN ('CONFIRMED', 'CANCELLED', 'EXPIRED') THEN
        RAISE EXCEPTION 'an intent may only become CONFIRMED, CANCELLED or EXPIRED, not %',
            NEW.status USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
