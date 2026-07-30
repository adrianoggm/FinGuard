-- Rol con el que el motor de consultas lee el libro mayor (vision.md §8.6).
-- No es el propietario: aunque el compilador de SQL tuviera un fallo, con estas
-- credenciales no se puede escribir nada ni leer una tabla directamente.
-- ponytail: contraseña fija de desarrollo; en despliegue real viene de secretos (regla 13).

CREATE ROLE finguard_query_reader LOGIN PASSWORD 'finguard_query_reader';

-- 01-databases.sql revocó el CONNECT de PUBLIC, así que hay que darlo explícitamente.
GRANT CONNECT ON DATABASE finguard_banking TO finguard_query_reader;

-- Toda sesión de este rol es de solo lectura, aborta si tarda demasiado y no puede
-- dejar una transacción abierta ocupando recursos.
ALTER ROLE finguard_query_reader SET default_transaction_read_only = on;
ALTER ROLE finguard_query_reader SET statement_timeout = '3s';
ALTER ROLE finguard_query_reader SET idle_in_transaction_session_timeout = '10s';

-- Los permisos sobre las vistas se conceden en la migración 0002, cuando existen.
