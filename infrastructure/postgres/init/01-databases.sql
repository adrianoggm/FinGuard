-- One role and one database per service: no service reaches another's data.
-- Runs only once, when the postgres-data volume is empty.
-- ponytail: fixed development passwords; a real deployment injects them from a
-- secret store (rule 13).

CREATE ROLE finguard_banking LOGIN PASSWORD 'finguard_banking';
CREATE DATABASE finguard_banking OWNER finguard_banking;
REVOKE ALL ON DATABASE finguard_banking FROM PUBLIC;

-- Provisioned and unused: the orchestrator holds no state of its own today, and the
-- conversation is carried by whoever is having it. This exists so §8.2 of the vision —
-- one database per service — is true from the first volume rather than retrofitted.
--
-- Renamed with the service. A volume created before the rename still has the old role
-- and database, which is inert precisely because nothing connects to them; it reconciles
-- the next time the volume is recreated.
CREATE ROLE finguard_orchestrator LOGIN PASSWORD 'finguard_orchestrator';
CREATE DATABASE finguard_orchestrator OWNER finguard_orchestrator;
REVOKE ALL ON DATABASE finguard_orchestrator FROM PUBLIC;

-- finguard_audit and finguard_a2a arrive in the 3rd and 4th iterations.
-- The read-only role finguard_query_reader is created in 02-query-reader.sql.
