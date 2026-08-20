# Matriz de capacidades conversacionales

Estado vivo del orquestador a 18 de agosto de 2026. Esta es la referencia para evitar que la ayuda,
el `capability_guard`, el planificador y el corpus se contradigan. Una capacidad sólo puede
considerarse **activa** cuando tiene representación de dominio, ejecución determinista y una prueba
conversacional. Los textos de producto no deben prometer una fila que siga en **siguiente**.

| Capacidad | Estado | Representación / control | Evidencia principal |
| --- | --- | --- | --- |
| SUM, COUNT, AVERAGE y PERIOD_AVERAGE | Activa | `FinancialPlan.metric` | `test_averages.py`, corpus 14 |
| Filtros y exclusiones precisas de categoría/comercio | Activa | `FinancialPlan` + verificador | corpus y pruebas del guard |
| Cuenta única, varias cuentas y desglose por cuenta | Activa | alcance de `FinancialPlan` | `test_which_accounts_*`, corpus 18 |
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
| Respuesta con gráfico | Siguiente, fase 4 | `ChartSpec` tipado sin protocolo, derivado del plan y del resultado ya verificados; ninguna cifra dentro del spec | [ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) |
| Reformular el gráfico con palabras | Siguiente, fase 4 | parche de codificación emitido por el modelo, validado contra el esquema y contra los campos del resultado, y fusionado por código | [ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) |
| Guardar una métrica y volver a verla | Siguiente, fase 4 | `saved_metric` con `plan_json` y `encoding_json`, sin filas ni totales; el plan almacenado es entrada no confiable y se reejecuta con el alcance recomprobado | [ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) |

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

Lo que falta ya no es aritmética, es presentación. Comparaciones, ratios, acumulados, frecuencia,
recurrencia y rankings por grupo se calculan y se leen en prosa: **ninguna de esas filas activas
tiene todavía una forma de verse**. Las tres filas marcadas como siguiente son ese hueco, y su
regla es que el vocabulario del gráfico es abierto mientras el origen de sus cifras está cerrado.
