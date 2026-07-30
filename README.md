# FinGuard

Motor financiero conversacional, modular y seguro. Permite consultar información bancaria y preparar operaciones mediante lenguaje natural, por texto o por voz.

> FinGuard interpreta. Los servicios deterministas validan. El usuario autoriza. El sistema ejecuta.

El modelo generativo interpreta intenciones y redacta respuestas, pero no accede a la base de datos ni ejecuta movimientos de dinero por su cuenta. La seguridad es una propiedad estructural, no una capa añadida.

## Estado

En diseño. Todavía no hay código: la arquitectura, los contratos y el modelo de datos están definidos. Fase 0 — diseño y preparación (30 jul – 3 ago).

## Documentación

- [docs/vision.md](docs/vision.md) — visión de producto, arquitectura, modelo de datos, seguridad, A2A y ADRs.
- [docs/roadmap.md](docs/roadmap.md) — fases, fechas y métricas de éxito.
- [docs/architecture/monorepo.md](docs/architecture/monorepo.md) — estructura del repositorio, tooling y dependencias permitidas.
- [docs/architecture/development-rules.md](docs/architecture/development-rules.md) — reglas de desarrollo.

## Servicios previstos

Monorepo con proyectos independientes: cada servicio tiene sus dependencias, lockfile, Dockerfile, pruebas y pipeline.

| `apps/` | Responsabilidad |
| --- | --- |
| `web` | Chat, audio, visualizaciones y confirmaciones |
| `assistant-api` | Contexto conversacional, intención, coordinación de herramientas, LLM |
| `banking-api` | Cuentas, libro mayor, saldos, contactos, Bizum. Único con escritura financiera |
| `voice-gateway` | Audio en streaming, transcripción, síntesis, interrupciones |
| `a2a-gateway` | Agent Card, cliente y servidor A2A |
| `audit-worker` | Auditoría append-only de accesos y operaciones |

## Stack

Python 3.12 · FastAPI · Pydantic · SQLAlchemy 2 · Alembic · PostgreSQL · Redis · RabbitMQ · React/Next.js + TypeScript · Vega-Lite · Docker Compose · Pytest.

## Cómo arrancar

```bash
docker compose --profile core up -d --wait
```

Por ahora el perfil `core` solo levanta PostgreSQL 18 en `127.0.0.1:5442` (5432 y 5433 suelen estar ocupados por otros proyectos), con una base de datos y un rol por servicio. Las credenciales de desarrollo son triviales, así que el puerto **no** se publica fuera de localhost:

| Base de datos | Rol | Contraseña de desarrollo |
| --- | --- | --- |
| `finguard_banking` | `finguard_banking` | `finguard_banking` |
| `finguard_assistant` | `finguard_assistant` | `finguard_assistant` |

Ningún rol puede conectarse a la base de datos de otro servicio. Copia [.env.example](.env.example) a `.env` para cambiar puerto o credenciales.
