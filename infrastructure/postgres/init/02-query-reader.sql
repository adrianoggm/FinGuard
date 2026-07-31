-- Role the query engine uses to read the ledger (vision.md §8.6).
-- It is not the owner: even if the SQL compiler had a hole, these credentials cannot
-- write anything nor read a table directly.
-- ponytail: fixed development password; a real deployment injects it from a secret
-- store (rule 13).

CREATE ROLE finguard_query_reader LOGIN PASSWORD 'finguard_query_reader';

-- 01-databases.sql revoked CONNECT from PUBLIC, so it has to be granted explicitly.
GRANT CONNECT ON DATABASE finguard_banking TO finguard_query_reader;

-- Every session of this role is read-only, aborts if it takes too long, and cannot
-- leave a transaction open holding resources.
ALTER ROLE finguard_query_reader SET default_transaction_read_only = on;
ALTER ROLE finguard_query_reader SET statement_timeout = '3s';
ALTER ROLE finguard_query_reader SET idle_in_transaction_session_timeout = '10s';

-- Privileges on the views are granted in migration 0002, once they exist.
