# Estrategia de repositorio

Un único repositorio Git llamado `finguard`, compuesto por proyectos autónomos y desplegables de manera independiente.

> **Monorepo no significa aplicación monolítica.**

Cada servicio tendrá sus propias dependencias, lockfile, Dockerfile, pruebas, configuración, migraciones y pipeline. El repositorio raíz solamente coordinará el conjunto.

No se usarán submódulos Git ni repositorios anidados: complicarían demasiado el desarrollo. Cada directorio debe comportarse como un pequeño repositorio independiente y poder extraerse en el futuro sin rehacer el código.

## Tabla de contenidos

1. [Herramientas del monorepo](#1-herramientas-del-monorepo)
2. [Estructura general](#2-estructura-general)
3. [Componentes desplegables](#3-componentes-desplegables)
4. [Diagrama de dependencias](#4-diagrama-de-dependencias)
5. [Contratos como fuente de verdad](#5-contratos-como-fuente-de-verdad)
6. [Qué compartiría y qué no](#6-qué-compartiría-y-qué-no)
7. [Configuración de pnpm](#7-configuración-de-pnpm)
8. [Configuración raíz de Nx](#8-configuración-raíz-de-nx)
9. [Configuración Nx de un servicio Python](#9-configuración-nx-de-un-servicio-python)
10. [Independencia Python](#10-independencia-python)
11. [Docker Compose por perfiles](#11-docker-compose-por-perfiles)
12. [Construcción de imágenes](#12-construcción-de-imágenes)
13. [Pruebas](#13-pruebas)
14. [CI/CD](#14-cicd)
15. [Orden de implementación](#15-orden-de-implementación)
16. [Reglas que dejaría por escrito](#16-reglas-que-dejaría-por-escrito)

---

## 1. Herramientas del monorepo

| Necesidad | Herramienta |
| --- | --- |
| Orquestación global | Nx |
| Frontend y paquetes TypeScript | pnpm workspace |
| Dependencias de cada servicio Python | uv |
| Desarrollo local | Docker Compose |
| Construcción de imágenes | Docker Buildx Bake |
| Contratos | OpenAPI, JSON Schema y AsyncAPI |
| CI/CD | GitHub Actions |
| Observabilidad | OpenTelemetry |

**Nx** actuaría como coordinador políglota. Puede ejecutar comandos arbitrarios por proyecto, aplicar caché y ejecutar únicamente las tareas afectadas por un cambio mediante `nx affected`, por lo que no obliga a que todos los proyectos sean TypeScript.

Para TypeScript, **pnpm workspace** administraría el frontend y los paquetes compartidos. pnpm permite agrupar varios proyectos dentro de un repositorio y referenciar paquetes locales explícitamente mediante `workspace:`.

Para Python, **no** se utilizaría un único workspace global de uv. Cada microservicio tendría su propio `pyproject.toml` y su propio `uv.lock`. Los workspaces de uv comparten un único lockfile y entorno, y su documentación desaconseja utilizarlos cuando los miembros necesitan dependencias conflictivas o entornos separados. Eso podría ocurrir fácilmente entre voz, A2A, banca y orquestación.

---

## 2. Estructura general

```text
finguard/
├── apps/
│   ├── web/
│   ├── assistant-api/
│   ├── banking-api/
│   ├── voice-gateway/
│   ├── a2a-gateway/
│   └── audit-worker/
│
├── packages/
│   ├── contracts/
│   │   ├── openapi/
│   │   ├── asyncapi/
│   │   ├── json-schema/
│   │   ├── a2a/
│   │   └── generated/
│   │       ├── python/
│   │       └── typescript/
│   │
│   ├── python/
│   │   ├── finguard-observability/
│   │   ├── finguard-security/
│   │   └── finguard-testing/
│   │
│   └── typescript/
│       ├── ui/
│       ├── chart-renderer/
│       ├── api-client/
│       └── eslint-config/
│
├── infrastructure/
│   ├── compose/
│   ├── docker/
│   ├── postgres/
│   ├── rabbitmq/
│   ├── redis/
│   ├── telemetry/
│   └── monitoring/
│
├── tests/
│   ├── e2e/
│   ├── contract/
│   ├── security/
│   ├── performance/
│   └── fixtures/
│
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── api/
│   ├── threat-model/
│   └── roadmap/
│
├── tools/
│   ├── codegen/
│   ├── generators/
│   └── scripts/
│
├── .github/
│   └── workflows/
│
├── compose.yaml
├── docker-bake.hcl
├── nx.json
├── package.json
├── pnpm-workspace.yaml
├── CODEOWNERS
├── README.md
└── .env.example
```

---

## 3. Componentes desplegables

### `apps/web`

Aplicación del cliente.

```text
apps/web/
├── src/
│   ├── app/
│   ├── features/
│   │   ├── chat/
│   │   ├── voice/
│   │   ├── transactions/
│   │   ├── charts/
│   │   └── payment-confirmation/
│   ├── components/
│   ├── hooks/
│   └── infrastructure/
├── tests/
├── package.json
├── project.json
├── Dockerfile
├── next.config.ts
└── README.md
```

Responsabilidades:

- Chat y streaming.
- Captura y reproducción de audio.
- Renderizado de Vega-Lite.
- Confirmaciones de Bizum.
- Presentación de movimientos consultados.
- Gestión de la sesión del usuario.

No contiene reglas financieras ni llama directamente a `banking-api`.

### `apps/assistant-api`

Es el orquestador de FinGuard.

```text
apps/assistant-api/
├── src/
│   └── finguard_assistant/
│       ├── domain/
│       │   ├── conversation.py
│       │   ├── message.py
│       │   ├── query_plan.py
│       │   └── pending_action.py
│       │
│       ├── application/
│       │   ├── process_message.py
│       │   ├── plan_financial_query.py
│       │   ├── prepare_payment.py
│       │   └── generate_visualization.py
│       │
│       ├── ports/
│       │   ├── llm_provider.py
│       │   ├── banking_client.py
│       │   ├── session_repository.py
│       │   ├── visualization_provider.py
│       │   └── audit_publisher.py
│       │
│       ├── adapters/
│       │   ├── inbound/
│       │   │   ├── http/
│       │   │   └── websocket/
│       │   └── outbound/
│       │       ├── banking_http/
│       │       ├── gemini/
│       │       ├── openai/
│       │       ├── redis/
│       │       └── vega_lite/
│       │
│       └── bootstrap/
│           ├── config.py
│           ├── dependencies.py
│           └── main.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
├── .env.example
└── README.md
```

Responsabilidades:

- Mantener contexto conversacional.
- Interpretar la intención.
- Crear un `FinancialQuery`.
- Seleccionar herramientas.
- Coordinar consultas y operaciones.
- Generar explicaciones.
- Crear el plan visual.
- Integrarse con proveedores LLM.

No conoce las tablas bancarias ni ejecuta SQL directamente.

### `apps/banking-api`

Es el núcleo financiero determinista.

```text
apps/banking-api/
├── src/
│   └── finguard_banking/
│       ├── domain/
│       │   ├── accounts/
│       │   ├── ledger/
│       │   ├── payments/
│       │   ├── contacts/
│       │   └── policies/
│       │
│       ├── application/
│       │   ├── get_balance.py
│       │   ├── query_transactions.py
│       │   ├── prepare_bizum.py
│       │   ├── confirm_bizum.py
│       │   └── expire_payment_intent.py
│       │
│       ├── ports/
│       │   ├── account_repository.py
│       │   ├── ledger_repository.py
│       │   ├── payment_repository.py
│       │   └── event_publisher.py
│       │
│       ├── adapters/
│       │   ├── inbound/http/
│       │   └── outbound/
│       │       ├── postgres/
│       │       └── rabbitmq/
│       │
│       └── bootstrap/
│
├── migrations/
├── scripts/
│   └── generate_financial_data.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── concurrency/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsabilidades:

- Cuentas.
- Libro mayor de doble entrada.
- Saldos.
- Movimientos.
- Contactos.
- Intenciones de pago.
- Bizum simulado.
- Idempotencia.
- Políticas financieras.
- Eventos de dominio.

Este será el único servicio con acceso de escritura a la base financiera.

### `apps/voice-gateway`

```text
apps/voice-gateway/
├── src/
│   └── finguard_voice/
│       ├── domain/
│       ├── application/
│       ├── ports/
│       │   ├── speech_to_text.py
│       │   ├── text_to_speech.py
│       │   └── assistant_client.py
│       ├── adapters/
│       │   ├── inbound/websocket/
│       │   └── outbound/
│       │       ├── realtime_provider/
│       │       └── assistant_http/
│       └── bootstrap/
├── tests/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsabilidades:

- Streaming de audio.
- Detección de turnos.
- Transcripción.
- Síntesis.
- Interrupciones.
- Gestión de sesiones de voz.

No interpreta movimientos ni ejecuta operaciones. Convierte:

```text
Audio → texto → assistant-api → texto → audio
```

### `apps/a2a-gateway`

```text
apps/a2a-gateway/
├── src/
│   └── finguard_a2a/
│       ├── application/
│       │   ├── handle_task.py
│       │   ├── discover_agent.py
│       │   └── invoke_remote_agent.py
│       ├── mapping/
│       │   ├── messages.py
│       │   ├── tasks.py
│       │   └── artifacts.py
│       ├── security/
│       │   ├── agent_registry.py
│       │   ├── scopes.py
│       │   └── credential_validation.py
│       ├── adapters/
│       │   ├── inbound/a2a_server/
│       │   └── outbound/
│       │       ├── a2a_client/
│       │       └── assistant_client/
│       └── bootstrap/
├── agent-cards/
│   ├── public.json
│   └── development.json
├── tests/
│   ├── unit/
│   ├── interoperability/
│   └── security/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Este servicio será un adaptador, no el núcleo de FinGuard.

```text
A2A Task
   ↓
a2a-gateway
   ↓
Comando interno
   ↓
assistant-api
   ↓
Resultado interno
   ↓
A2A Artifact
```

El protocolo A2A utiliza el Agent Card para declarar identidad, endpoint, capacidades, skills y requisitos de autenticación. El gateway debe publicar la tarjeta y traducir los conceptos A2A sin introducirlos en el dominio financiero.

### `apps/audit-worker`

```text
apps/audit-worker/
├── src/
│   └── finguard_audit/
│       ├── consumers/
│       ├── integrity/
│       ├── persistence/
│       └── bootstrap/
├── tests/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsabilidades:

- Consumir eventos.
- Registrar accesos y operaciones.
- Crear trazabilidad.
- Construir el historial de una conversación.
- Detectar eventos duplicados.
- Comprobar la integridad de la cadena de auditoría.

---

## 4. Diagrama de dependencias

### Dependencias permitidas

```text
web             → assistant-api
voice-gateway   → assistant-api
a2a-gateway     → assistant-api
assistant-api   → banking-api
banking-api     → PostgreSQL
banking-api     → RabbitMQ
audit-worker    → RabbitMQ
audit-worker    → base de auditoría
```

### Dependencias prohibidas

```text
web             ✗ banking-api directamente
voice-gateway   ✗ banking-api
a2a-gateway     ✗ PostgreSQL
assistant-api   ✗ tablas de banking-api
audit-worker    ✗ modificar el libro mayor
banking-api     ✗ proveedor LLM
```

---

## 5. Contratos como fuente de verdad

La pieza central del monorepo debería ser `packages/contracts/`.

No se compartirían modelos ORM ni clases internas entre servicios. Se compartirían contratos públicos.

```text
packages/contracts/
├── openapi/
│   ├── assistant-api.yaml
│   ├── banking-api.yaml
│   └── voice-gateway.yaml
│
├── asyncapi/
│   └── domain-events.yaml
│
├── json-schema/
│   ├── financial-query.schema.json
│   ├── visualization-result.schema.json
│   ├── payment-preview.schema.json
│   └── audit-event.schema.json
│
├── a2a/
│   ├── agent-card.schema.json
│   ├── query-movements.input.json
│   └── query-movements.output.json
│
└── generated/
    ├── python/
    └── typescript/
```

El proceso sería:

```text
Contrato OpenAPI o JSON Schema
          ↓
Validación
          ↓
Generación de clientes
          ├── Cliente Python
          └── Cliente TypeScript
```

Así:

- `assistant-api` utiliza un cliente generado para llamar a `banking-api`.
- `web` utiliza un cliente generado para llamar a `assistant-api`.
- Los eventos se validan contra AsyncAPI o JSON Schema.
- A2A traduce sus mensajes a contratos internos conocidos.

Esto evita que un cambio en una clase de Python rompa silenciosamente otro servicio.

---

## 6. Qué compartiría y qué no

### Compartiría

- `packages/python/finguard-observability`
- `packages/python/finguard-security`
- `packages/python/finguard-testing`
- `packages/typescript/ui`
- `packages/typescript/chart-renderer`
- `packages/contracts`

Ejemplos:

- Inicialización de OpenTelemetry.
- Formato común de logs.
- Propagación de `correlation_id`.
- Utilidades de autenticación.
- Fixtures de test.
- Componentes visuales.
- Clientes generados.

### No compartiría

- Entidades financieras.
- Repositorios de base de datos.
- Modelos SQLAlchemy.
- Casos de uso.
- Políticas de Bizum.
- Contexto conversacional.
- Código específico de Gemini u OpenAI.
- Acceso directo a tablas.

Se evitaría crear `packages/common/`. Los paquetes denominados `common`, `shared` o `utils` suelen convertirse en un contenedor sin límites y terminan acoplando todos los servicios.

---

## 7. Configuración de pnpm

El workspace TypeScript incluiría solamente el frontend y los paquetes TypeScript:

```yaml
# pnpm-workspace.yaml

packages:
  - "apps/web"
  - "packages/typescript/*"
  - "packages/contracts/generated/typescript"
```

En `apps/web/package.json`:

```json
{
  "dependencies": {
    "@finguard/ui": "workspace:*",
    "@finguard/chart-renderer": "workspace:*",
    "@finguard/api-client": "workspace:*"
  }
}
```

El protocolo `workspace:` obliga a resolver la dependencia desde el propio monorepo y evita que pnpm utilice accidentalmente una versión publicada diferente.

---

## 8. Configuración raíz de Nx

```json
{
  "$schema": "./node_modules/nx/schemas/nx-schema.json",
  "defaultBase": "main",
  "targetDefaults": {
    "lint": {
      "cache": true
    },
    "typecheck": {
      "cache": true
    },
    "test": {
      "cache": true,
      "dependsOn": ["^build"]
    },
    "build": {
      "cache": true,
      "dependsOn": ["^build"]
    }
  }
}
```

El `package.json` raíz podría tener:

```json
{
  "name": "finguard",
  "private": true,
  "scripts": {
    "dev": "nx run-many -t serve --parallel=6",
    "check": "nx run-many -t lint,typecheck,test",
    "build": "nx run-many -t build",
    "affected:check": "nx affected -t lint,typecheck,test,build",
    "graph": "nx graph",
    "compose:up": "docker compose up --build",
    "compose:down": "docker compose down"
  },
  "devDependencies": {
    "nx": "<versión-fijada>"
  }
}
```

No se pondrían las dependencias Python en el root.

---

## 9. Configuración Nx de un servicio Python

Cada aplicación incluiría un `project.json`. Ejemplo de `apps/banking-api/project.json`:

```json
{
  "name": "finguard-banking-api",
  "root": "apps/banking-api",
  "projectType": "application",
  "tags": [
    "scope:banking",
    "type:service",
    "language:python"
  ],
  "targets": {
    "serve": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run uvicorn finguard_banking.bootstrap.main:app --reload --port 8001"
      }
    },
    "lint": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run ruff check ."
      }
    },
    "format-check": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run ruff format --check ."
      }
    },
    "typecheck": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run mypy src"
      }
    },
    "test": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run pytest"
      }
    },
    "build": {
      "executor": "nx:run-commands",
      "options": {
        "command": "docker build -f apps/banking-api/Dockerfile -t finguard/banking-api:local ."
      }
    }
  }
}
```

Nx permite asignar cualquier comando a un target y ejecutarlo para un proyecto, para todos los proyectos o solo para los afectados.

---

## 10. Independencia Python

Cada servicio tendría su propio lockfile:

```text
apps/assistant-api/uv.lock
apps/banking-api/uv.lock
apps/voice-gateway/uv.lock
apps/a2a-gateway/uv.lock
apps/audit-worker/uv.lock
```

Y su propio `pyproject.toml`:

```toml
# apps/banking-api/pyproject.toml

[project]
name = "finguard-banking-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "asyncpg",
    "pydantic"
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy"
]
```

Esto permite que:

- `voice-gateway` utilice librerías de audio sin instalarlas en banca.
- `a2a-gateway` actualice el SDK A2A sin afectar el libro mayor.
- `assistant-api` tenga SDK de LLM sin introducirlos en `banking-api`.
- Cada imagen Docker contenga solo lo que necesita.

---

## 11. Docker Compose por perfiles

No se levantarían siempre todos los servicios.

```yaml
services:
  postgres:
    image: postgres
    profiles: ["core"]

  redis:
    image: redis
    profiles: ["core"]

  banking-api:
    build:
      context: .
      dockerfile: apps/banking-api/Dockerfile
    profiles: ["core"]

  assistant-api:
    build:
      context: .
      dockerfile: apps/assistant-api/Dockerfile
    profiles: ["core"]

  web:
    build:
      context: .
      dockerfile: apps/web/Dockerfile
    profiles: ["core"]

  voice-gateway:
    build:
      context: .
      dockerfile: apps/voice-gateway/Dockerfile
    profiles: ["voice"]

  a2a-gateway:
    build:
      context: .
      dockerfile: apps/a2a-gateway/Dockerfile
    profiles: ["a2a"]

  rabbitmq:
    image: rabbitmq
    profiles: ["events"]

  audit-worker:
    build:
      context: .
      dockerfile: apps/audit-worker/Dockerfile
    profiles: ["events"]
```

Comandos:

```bash
# Núcleo funcional
docker compose --profile core up

# Núcleo y voz
docker compose --profile core --profile voice up

# Sistema completo
docker compose \
  --profile core \
  --profile voice \
  --profile a2a \
  --profile events \
  up
```

Docker Compose permite habilitar servicios opcionales mediante perfiles, lo que encaja bien para mantener voz, A2A y observabilidad fuera del arranque mínimo.

---

## 12. Construcción de imágenes

Se utilizaría `docker-bake.hcl` para centralizar la construcción:

```hcl
group "core" {
  targets = [
    "web",
    "assistant-api",
    "banking-api"
  ]
}

group "all" {
  targets = [
    "web",
    "assistant-api",
    "banking-api",
    "voice-gateway",
    "a2a-gateway",
    "audit-worker"
  ]
}

target "banking-api" {
  context    = "."
  dockerfile = "apps/banking-api/Dockerfile"
  tags       = ["finguard/banking-api:local"]
}

target "assistant-api" {
  context    = "."
  dockerfile = "apps/assistant-api/Dockerfile"
  tags       = ["finguard/assistant-api:local"]
}
```

```bash
docker buildx bake core
docker buildx bake all
```

Bake permite definir de forma declarativa varios targets de construcción y resulta apropiado para construir múltiples imágenes desde un monorepo.

---

## 13. Pruebas

Las pruebas se separan en dos niveles.

### Dentro de cada servicio

```text
apps/banking-api/tests/
├── unit/
├── integration/
├── contract/
└── concurrency/
```

### En la raíz

```text
tests/
├── e2e/
│   ├── text_conversation/
│   ├── financial_queries/
│   ├── bizum/
│   ├── voice/
│   └── a2a/
│
├── contract/
│   ├── assistant_banking/
│   ├── web_assistant/
│   └── a2a_assistant/
│
├── security/
│   ├── prompt_injection/
│   ├── unauthorized_access/
│   ├── payment_confirmation/
│   └── duplicate_operations/
│
└── performance/
    ├── query_latency/
    ├── websocket/
    └── voice_realtime/
```

---

## 14. CI/CD

```text
.github/workflows/
├── pull-request.yml
├── contracts.yml
├── build-images.yml
├── security.yml
└── release.yml
```

### Pull request

1. Detectar proyectos afectados.
2. Ejecutar lint.
3. Ejecutar typecheck.
4. Ejecutar tests.
5. Construir proyectos afectados.
6. Validar contratos.

```bash
pnpm nx affected -t lint,typecheck,test,build
```

### Contratos

Se ejecutaría cuando cambie `packages/contracts/**`. Comprobaría:

- Esquemas válidos.
- Clientes regenerados.
- Cambios incompatibles.
- Tests de consumidores.

### Imágenes

Solo construiría las imágenes afectadas. GitHub Actions permite filtrar workflows según las rutas modificadas, aunque también puede centralizarse esta lógica mediante `nx affected`.

---

## 15. Orden de implementación

No se crearían todos los directorios con código vacío desde el primer momento.

### Primera iteración

```text
apps/
├── web/
├── assistant-api/
└── banking-api/

packages/
└── contracts/

infrastructure/
└── postgres/
```

Objetivo:

```text
Usuario → web → assistant-api → banking-api → PostgreSQL
```

### Segunda iteración

- `apps/voice-gateway`
- `packages/typescript/chart-renderer`

### Tercera iteración

- `apps/audit-worker`
- RabbitMQ

### Cuarta iteración

- `apps/a2a-gateway`
- `packages/contracts/a2a`

Así se evita construir una arquitectura enorme antes de disponer del flujo principal.

---

## 16. Reglas que dejaría por escrito

Las reglas de desarrollo viven en [development-rules.md](development-rules.md).

### Estructura mínima que empezaría a crear

```text
finguard/
├── apps/
│   ├── web/
│   ├── assistant-api/
│   └── banking-api/
├── packages/
│   └── contracts/
├── infrastructure/
│   └── postgres/
├── tests/
│   └── e2e/
├── docs/
│   ├── architecture/
│   └── adr/
├── compose.yaml
├── nx.json
├── package.json
├── pnpm-workspace.yaml
└── README.md
```

Esta estructura ya permite desarrollar el núcleo sin cerrar la puerta a voz, auditoría o A2A. La clave será que FinGuard tenga proyectos independientes dentro de un único repositorio, no múltiples servicios artificiales que compartan código y base de datos como si fueran un monolito distribuido.
