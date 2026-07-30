# Reglas de desarrollo

Reglas que aplican a todo el monorepo. Ver [monorepo.md](monorepo.md) para la estructura que las soporta.

1. Cada aplicación se puede construir y probar por separado.
2. Cada servicio Python tiene su propio `uv.lock`.
3. Cada servicio es propietario de sus datos.
4. Ningún servicio consulta directamente la base de datos de otro.
5. La comunicación se realiza mediante contratos públicos.
6. Los contratos se definen antes de generar los clientes.
7. A2A es un adaptador externo, no parte del dominio financiero.
8. El LLM nunca accede directamente al libro mayor.
9. Las librerías compartidas no contienen lógica de negocio.
10. Todo evento y petición incorpora un `correlation_id`.
11. Todo servicio dispone de `/health`, `/ready` y métricas.
12. Todo cambio en una operación financiera requiere pruebas de concurrencia e idempotencia.
