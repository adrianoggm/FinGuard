-- One role and one database per service: no service reaches another's data.
-- Runs only once, when the postgres-data volume is empty.
-- ponytail: fixed development passwords; a real deployment injects them from a
-- secret store (rule 13).

CREATE ROLE finguard_banking LOGIN PASSWORD 'finguard_banking';
CREATE DATABASE finguard_banking OWNER finguard_banking;
REVOKE ALL ON DATABASE finguard_banking FROM PUBLIC;

CREATE ROLE finguard_assistant LOGIN PASSWORD 'finguard_assistant';
CREATE DATABASE finguard_assistant OWNER finguard_assistant;
REVOKE ALL ON DATABASE finguard_assistant FROM PUBLIC;

-- finguard_audit and finguard_a2a arrive in the 3rd and 4th iterations.
-- The read-only role finguard_query_reader is created in 02-query-reader.sql.
