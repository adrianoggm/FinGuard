-- Un rol y una base de datos por servicio: ningún servicio accede a los datos de otro.
-- Se ejecuta una sola vez, cuando el volumen postgres-data está vacío.
-- ponytail: contraseñas fijas de desarrollo; en despliegue real vienen de secretos.

CREATE ROLE finguard_banking LOGIN PASSWORD 'finguard_banking';
CREATE DATABASE finguard_banking OWNER finguard_banking;
REVOKE ALL ON DATABASE finguard_banking FROM PUBLIC;

CREATE ROLE finguard_assistant LOGIN PASSWORD 'finguard_assistant';
CREATE DATABASE finguard_assistant OWNER finguard_assistant;
REVOKE ALL ON DATABASE finguard_assistant FROM PUBLIC;

-- finguard_audit y finguard_a2a se añaden en la 3ª y 4ª iteración.
-- El rol de solo lectura finguard_query_reader llega en la Fase 2, con las vistas que debe leer.
