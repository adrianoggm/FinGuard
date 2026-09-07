# Matriz de capacidades conversacionales

Estado vivo del orquestador a 28 de agosto de 2026. Esta es la referencia para evitar que la ayuda,
el `capability_guard`, el planificador y el corpus se contradigan. Una capacidad sólo puede
considerarse **activa** cuando tiene representación de dominio, ejecución determinista y una prueba
conversacional. Los textos de producto no deben prometer una fila que siga en **siguiente**.

Las tres filas de gráfico ya no están en **siguiente**: están construidas y corriendo. Tampoco están
en **activa**, porque les falta la tercera condición y esa condición es de esta matriz, no un
detalle. **Construida** es la palabra para una capacidad con dominio y ejecución y sin prueba
conversacional, y un texto de producto puede prometerla nombrando lo que no está medido.

| Capacidad | Estado | Representación / control | Evidencia principal |
| --- | --- | --- | --- |
| SUM, COUNT, AVERAGE y PERIOD_AVERAGE | Activa | `FinancialPlan.metric` | `test_averages.py`, corpus 14 |
| Filtros y exclusiones precisas de categoría/comercio | Activa | `FinancialPlan` + verificador; una exclusión que no puede haber movido la cifra lo dice en vez de presentarse como aplicada, y un complemento vago ya no se lee como nombre de comercio | corpus, `test_answer_headline.py`, `test_explicit_facts.py` y pruebas del guard |
| Cuenta única, varias cuentas y desglose por cuenta | Activa | alcance de `FinancialPlan` | `test_which_accounts_*`, corpus 18, y desde el 28 de agosto el corpus entero por el camino con directorio de cuentas |
| Rankings de movimientos | Activa, individual o top 2-10 | `TopMovements` con lectura bancaria por posición | `test_ranking_pipeline.py`, corpus 24 |
| Comparación de semanas, meses y años | Activa | `ComparisonPlan` con dos operandos compatibles | `test_comparison_plan.py`, corpus 15 y 18 |
| Diferencia absoluta y porcentual | Activa | `ComparisonPlan` + `RatioPlan.PERCENT_CHANGE` | `test_percentages.py` |
| Proporciones y tasa de ahorro | Activa | `RatioPlan` con dos lecturas | corpus 16 |
| Flujo neto con ingresos y gastos visibles | Activa | `RatioPlan.NET_CASH_FLOW` | `test_percentages.py`, corpus 19 |
| Traspasos propios y aportaciones a inversión | Activa | `internal_only` | corpus 17 |
| Memoria de cuenta, categoría, comercio, grupo y movimientos mostrados | Activa | referencias tipadas + UUID de evidencia en `Conversation` | corpus 15, 18 y 23 |
| Rangos desde/hasta | Activa | `AbsoluteRange` determinista y acotado | `test_window_words.py` |
| Comparar dos rangos parciales arbitrarios | Activa para dos rangos explícitos pasados | `ComparisonPlan` + máximo 366 días | corpus 22 |
| Gastos recurrentes, recibos y suscripciones | Activa | `Metric.RECURRENCE` + `TransactionType` + tipo de contraparte/recibo | `test_recurrence.py`, `test_movement_semantics.py`, corpus 21 |
| Frecuencia real y día habitual | Activa | `Metric.FREQUENCY` + evidencia completa | `test_frequency.py`, corpus 20 |
| Acumulados por periodo | Activa | `Metric.CUMULATIVE` + grupos calendario completos | `test_cumulative_and_group_rankings.py`, corpus 24 |
| Rankings configurables por ticket medio/operaciones | Activa | `FinancialPlan.group_limit` + SUM/COUNT/AVERAGE agrupados | `test_cumulative_and_group_rankings.py`, corpus 24 |
| Contrapartes separadas de comercios | Activa | filtros y `GroupBy.COUNTERPARTY` conservan nombre y tipo de contraparte | `test_query_compiler.py`, `test_explicit_facts.py` |
| Trimestre, semestre, quincena y fin de semana | Activa | periodos relativos tipados + rangos calendario numerados | `test_natural_periods.py`, corpus 25 |
| Alias aprendidos de entidades | Activa para cuentas, comercios y contrapartes | cuenta alias→UUID; contraparte alias→(nombre, tipo), comprobados contra banca | `test_account_aliases.py`, `test_counterparty_aliases.py`, corpus 26 y 27 |
| Consultas compuestas generales | Activa para 2-3 lecturas seguras, incluidas relaciones tipadas | cada parte conserva `FinancialPlan`, `RatioPlan` o `ComparisonPlan`, evidencia y contexto; las operaciones o mezclas ambiguas piden elección | `test_a_conversation_that_wanders.py`, corpus 28 |
| Respuesta con gráfico | Construida: dominio sí, ejecución determinista sí, prueba conversacional no | `ChartSpec` tipado sin protocolo, derivado del plan y del resultado ya verificados; ninguna cifra dentro del spec, y no por una comprobación sino porque una codificación sólo admite un identificador. Seis de las siete capacidades tienen gráfico propio; de los ratios sólo la proporción, y el resto cae en barras | `test_visualization_stage1.py`; el corpus afirma su primer gráfico en el episodio 32 (`chart:`, `banking_calls:`, `derived_from:`) |
| Reformular el gráfico con palabras | Construida: dominio sí, ejecución determinista sí, prueba conversacional no; desde 4.2 el parche fusionado pasa por el verificador (`verify(draft_of(...))`) y un rechazo mantiene el gráfico anterior nombrando la regla; un seguimiento ambiguo pregunta (`VISUAL_AMBIGUOUS`) | `EncodingPatch` validado contra los campos del resultado y fusionado por código, y el parche **no lo emite el modelo**: es un regex sobre una lista blanca que corre antes de cualquier llamada, al contrario de lo que dice [ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) §2. Un parche que nombra un campo ausente se rechaza entero | `test_visualization_stage2_core.py`, con cadenas de dos y tres reformulaciones, y `test_visual_follow_up.py`; episodio 32 del corpus |
| Guardar una métrica y volver a verla | Construida: dominio sí, ejecución determinista sí, prueba conversacional no | `saved_metric` con `plan_json` y `encoding_json`, sin filas ni totales; el plan almacenado es entrada no confiable y se reejecuta con el alcance recomprobado. Esa recomprobación mira una sola cuenta: rechaza una cuenta propia del titular y no mira `across_accounts` | `test_saved_metrics.py`, `test_saved_metric_codec.py`; ninguna prueba llega a `/v1/metrics` por HTTP y la que ejecuta la política RLS no corre en ningún sitio |
| Proponer el gráfico con un modelo | Construida y apagada por defecto (deterministic); en shadow el modelo propone, el dominio autoriza y el usuario ve el suelo; medida por scripts/evaluate_visual.py (idoneidad por marca **y** estructura anotada, sensibilidad por el efecto propio de la instrucción); promoción pendiente de tres corridas | `VisualPlannerPort.plan(question, profile, presentation, budget_seconds)` asíncrono; `VisualPlanningPolicy` decide entre el modelo y el suelo determinista (`DeterministicVisualPlanner`) según `ORCHESTRATOR_VISUAL_PLANNER` (`deterministic \| shadow \| hybrid \| promoted`), con presupuesto `visual_planner_budget_seconds` (6,0 s por defecto) | `test_visual_planning_policy.py`, `test_visual_plan_verifier.py`, `test_llm_visual_planner.py`, `test_visual_decision_seam.py`, `test_visual_profile.py`; medido además por `scripts/evaluate_visual.py` sobre `evaluation/visual/cases.yaml` (16 casos, 14 entran a la política, 4 pares de parafraseo equivalente, 2 instruidos, 10 con estructura requerida), con `RESULTS.md` y `last-run.json` comprometidos tras cada corrida y el modelo visual configurable aparte del planificador (`ORCHESTRATOR_VISUAL_PLANNER_MODEL`); ningún episodio del corpus llega a esta capacidad |

## Evaluación obligatoria por capacidad

Una prueba de ruta no basta. Cada caso nuevo debe fijar, cuando corresponda:

- ventana temporal exacta;
- métrica y unidad;
- dirección de dinero;
- cuentas incluidas y excluidas;
- categorías, comercios y contrapartes;
- resultado numérico;
- referentes preservados durante varios turnos;
- número de llamadas al modelo y latencia.

Las pruebas metamórficas deben verificar que un cambio local sólo modifica su dimensión: `importe`
por `cantidad` cambia la métrica; `sin alquiler` añade una exclusión; `julio` por `junio` cambia sólo
la ventana. Las relaciones de dos lecturas deben aplicar el mismo cambio compatible a ambos
operandos.

Esta comprobación ya está automatizada en `evaluation/planning/metamorphic.yaml`. Cada variante
declara `metamorph_of` y `changes`; el evaluador compara los planes completos y falla tanto si no
cambia la dimensión declarada como si se modifica otra adicional. `just evaluate-metamorphic`
ejecuta el corpus contra el modelo local, mientras que la suite valida siempre la coherencia del
propio corpus sin necesitar un modelo.

## Estado del bloque

El bloque funcional previsto está completo. Las consultas compuestas admiten relaciones derivadas
cuando sus operandos y ventanas tienen representación tipada. Las operaciones siguen fuera de la
ejecución múltiple automática: mezclar una lectura con un Bizum o una transferencia siempre exige
elegir primero.

Lo que faltaba ya no es aritmética ni presentación: ese hueco está construido. Comparaciones,
ratios, acumulados, frecuencia, recurrencia, rankings por grupo y desgloses derivan su gráfico sin
llamada al modelo, el presentador emite Vega-Lite desde infraestructura y el cliente lo dibuja
dentro del chat; una métrica se guarda como estructura y se reejecuta al abrirla. La regla se
mantuvo, y se mantuvo mejor de lo prometido: el vocabulario del gráfico es abierto y el origen de
sus cifras está cerrado por construcción.

Lo que falta ahora es la medida. Ninguna de las tres filas aparece en el corpus y nada valida un spec
contra un esquema. De los tres defectos que esta sección dejaba abiertos, dos ya están cerrados:
~~el presentador declara Vega-Lite v5 contra un cliente que resuelve 6.4.3~~ (`dfc8940`, "declare the
Vega-Lite version the client actually has") y ~~el orden por defecto de un ranking ordena las
etiquetas y no la medida~~ (`e4e5376`, "a ranked dimension sorts by magnitude, not by label"); ambos
en `develop` en `d1b0a40`. Sigue abierto que reformular una métrica ya guardada no se construyó
nunca — `PATCH /v1/metrics/{id}` acepta un título y reutiliza la definición almacenada. El estado
criterio por criterio está en [ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md).

## Lo que la evidencia de esta matriz no alcanza

Anotado el 28 de agosto de 2026. Una columna de evidencia que no dice hasta dónde llega su prueba es
una columna que tranquiliza, que es lo contrario de para lo que existe esta matriz.

- **El corpus no corría por el camino que corre una implantación.** Hasta hoy el arnés no cableaba
  directorio de cuentas alguno: los 1143 turnos que informaban 0,0 % de contexto perdido tomaban
  tres ramas de `_scoped` escritas para las llamadas heredadas del manejador, y ningún turno había
  visto nunca la aclaración de cuenta. Un defecto de alcance de cuenta sobrevivió a esa cifra. Hoy
  van 1147 de 1167 turnos por el camino con directorio y quedan tres episodios en el heredado, cada
  uno con su motivo escrito encima de su `scope: legacy`. Las filas de alcance de cuenta tienen
  evidencia real desde hoy y no desde su fecha.
- **Una familia entera del guard puede apagarse sin que nada falle.** La auditoría por mutación del
  28 de agosto desactivó `CATEGORY_EXCLUSION` y la suite completa pasó. La fila de filtros y
  exclusiones cita «pruebas del guard»; esas pruebas no discriminan, y eso está medido en
  [la auditoría](audits/2026-08-28-do-the-tests-prove-anything.md).
