# FinGuard — Roadmap y métricas

## Roadmap

### Fase 0 — Diseño y preparación

**30 de julio – 3 de agosto**

- Crear repositorio.
- Registrar decisiones arquitectónicas.
- Definir contratos.
- Preparar Docker Compose.
- Crear PostgreSQL.
- Configurar CI.
- Definir escenarios de demostración.

*Resultado:* estructura ejecutable y arquitectura documentada.

### Fase 1 — Núcleo bancario

**4 – 10 de agosto**

- Implementar cuentas.
- Implementar libro mayor.
- Crear saldo proyectado.
- Generar movimientos sintéticos.
- Implementar contactos.
- Implementar API de consulta.
- Añadir pruebas de integridad.

*Resultado:* API bancaria determinista sin IA.

### Fase 2 — Consultas financieras

**11 – 17 de agosto**

- Definir `FinancialQuery`.
- Resolver fechas relativas.
- Implementar compilador SQL.
- Crear vistas de lectura.
- Aplicar rol read-only.
- Preparar dataset de evaluación.
- Medir exactitud de ejecución.

*Resultado:* preguntas financieras resueltas de manera verificable.

### Fase 3 — Asistente conversacional

**18 – 24 de agosto**

- Integrar proveedor LLM.
- Implementar selección de herramientas.
- Mantener contexto.
- Añadir respuestas explicables.
- Mostrar movimientos utilizados.
- Implementar streaming textual.

*Resultado:* asistente funcional por texto.

### Fase 4 — Visualización dinámica

**25 – 31 de agosto**

- Implementar planificador visual.
- Generar Vega-Lite.
- Validar especificaciones.
- Incorporar gráficos al chat.
- Permitir reformulación:
  - «Muéstralo por semanas».
  - «Mejor en barras».
  - «Compara con junio».

*Resultado:* gráficos generados dinámicamente.

### Fase 5 — Bizum seguro

**1 – 7 de septiembre**

- Implementar `payment_intents`.
- Añadir confirmación en dos fases.
- Añadir idempotencia.
- Bloquear saldo concurrentemente.
- Implementar doble entrada.
- Generar recibo.
- Registrar auditoría.

*Resultado:* operación Bizum simulada y segura.

### Fase 6 — Voz en tiempo real

**8 – 14 de septiembre**

- Capturar audio.
- Integrar transcripción.
- Añadir síntesis.
- Implementar interrupciones.
- Sincronizar voz, texto y gráficos.
- Medir latencia.

*Resultado:* conversación multimodal.

### Fase 7 — Preparación A2A

**15 – 19 de septiembre**

- Crear adaptador A2A.
- Publicar Agent Card.
- Exponer una skill de consulta.
- Exponer una skill de preparación de Bizum.
- Crear un agente externo ficticio.
- Validar autenticación y tareas.

*Resultado:* demostración de interoperabilidad sin comprometer el núcleo.

### Fase 8 — Evaluación y seguridad

**20 – 23 de septiembre**

- Ejecutar benchmark.
- Probar consultas ambiguas.
- Probar prompt injection.
- Probar operaciones duplicadas.
- Probar concurrencia.
- Medir latencias.
- Revisar logs y datos sensibles.

*Resultado:* métricas verificables.

### Fase 9 — Entregables

**24 – 27 de septiembre**

- Preparar guion del vídeo.
- Grabar demo.
- Redactar memoria.
- Preparar diagramas.
- Resumir métricas.
- Documentar limitaciones.

### Margen final

**28 – 30 de septiembre**

- Correcciones.
- Regrabación si fuera necesaria.
- Verificación de entregables.
- Entrega definitiva.

---

## Métricas de éxito

**Comprensión**

- Exactitud de intención.
- Exactitud de extracción de fechas.
- Exactitud de categorías.
- Resolución correcta de referencias contextuales.

**Consultas**

- SQL válido.
- Execution accuracy.
- Exactitud del importe.
- Exactitud de agrupaciones.
- Ausencia de acceso no autorizado.

**Operaciones**

- Cero ejecuciones sin confirmación.
- Cero operaciones duplicadas.
- Libro mayor siempre equilibrado.
- Respuesta correcta ante saldo insuficiente.
- Comportamiento correcto ante confirmaciones caducadas.

**Visualización**

- Porcentaje de especificaciones válidas.
- Adecuación del gráfico.
- Ausencia de campos inventados.
- Tiempo de generación.
- Capacidad de reformulación.

**Experiencia de usuario**

- Latencia hasta el primer contenido.
- Latencia total.
- Calidad de la respuesta.
- Interrupción de voz.
- Claridad de las confirmaciones.

### Objetivos iniciales

| Métrica | Objetivo |
| --- | --- |
| Exactitud de consultas | ≥ 95 % |
| Consultas SQL seguras | 100 % |
| Gráficos válidos | ≥ 98 % |
| Operaciones sin confirmación | 0 |
| Operaciones duplicadas | 0 |
| Latencia textual P50 | < 2 s |
| Latencia de consulta P95 | < 4 s |
| Libro mayor desequilibrado | 0 |

Los resultados finales deberán ser medidos y no estimados.
