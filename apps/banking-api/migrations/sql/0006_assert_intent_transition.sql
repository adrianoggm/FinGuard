-- An intent leaves AWAITING_CONFIRMATION exactly once.
--
-- This is what makes confirming twice safe without a lock held across the whole flow: the second
-- attempt finds a status that cannot transition and stops. Racing two confirmations means one of
-- them updates the row and the other sees the new status, because the caller takes the row with
-- SELECT ... FOR UPDATE before it decides.

CREATE OR REPLACE FUNCTION assert_intent_transition() RETURNS TRIGGER AS $$
BEGIN
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

    -- The amount, the recipient and the account are what the user agreed to. Confirming has no
    -- business changing any of them, and a bug that did would move a different payment than the
    -- one that was previewed.
    IF NEW.account_id <> OLD.account_id
        OR NEW.contact_id <> OLD.contact_id
        OR NEW.amount_minor <> OLD.amount_minor
        OR NEW.currency <> OLD.currency THEN
        RAISE EXCEPTION 'payment intent % cannot change what it is for', OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
