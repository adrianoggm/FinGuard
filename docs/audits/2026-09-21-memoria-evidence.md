# Evidencia utilizada en la revisión de la memoria

Revisión documental del 21 de septiembre de 2026. No se han vuelto a ejecutar las suites ni las campañas de inferencia. Esta nota separa los resultados consignados en la memoria de los respaldados por informes localizables; no sustituye los registros de ejecución.

## Fuentes y alcance

| Evidencia | Fuente | Cómo se utiliza |
| --- | --- | --- |
| Capacidades y pruebas asociadas | [Mapa de capacidades](../conversational-capabilities.md) | Ejemplos de comportamiento, no una tasa global de éxito. |
| Defectos que sobreviven a las pruebas | [Auditoría por mutaciones](2026-08-28-do-the-tests-prove-anything.md) | Justifica comprobar la eficacia de las aserciones además del recuento de pruebas. |
| Clasificación histórica, 818 turnos | [Resultados de conversación](../../apps/orchestrator/evaluation/conversations/RESULTS.md) | Acuerdo con el encaminador determinista; no exactitud de las cifras financieras. |
| Planificación, defensas y fallos históricos | [Resultados de planificación](../../apps/orchestrator/evaluation/planning/RESULTS.md) | Diferencia entre decisión, plan exacto y ejecución incorrecta; efecto de verificadores y filtros explícitos. |
| Gráficos, 16 casos | [Informe visual](../../apps/orchestrator/evaluation/visual/RESULTS.md), [registro disponible](../../apps/orchestrator/evaluation/visual/last-run.json) | 3/14 propuestas válidas, 2/14 válidas, adecuadas y diferentes; latencia añadida P50 5137 ms y P95 6031 ms. |
| Propiedades de las respuestas por HTTP | [Consultas E2E](../../apps/orchestrator/evaluation/planning/RESULTS-E2E.md), [conversaciones E2E](../../apps/orchestrator/evaluation/conversations/RESULTS-E2E.md) | Evidencia histórica con versiones, fallos y revisiones del evaluador; no validación de la versión actual. |
| Recorrido de voz | [Resultados de voz](../../apps/voice-gateway/evaluation/RESULTS.md) | Se cita el ensayo del 17 de agosto con sus condiciones; no se mezclan mediciones de otros equipos, modelos o peticiones. |

## Resultados recientes cuya procedencia debe completarse

La memoria previa, versionada en `cb32d5b`, consignaba:

- 4.645 pruebas superadas, 4 omitidas y un fallo atribuido a la ausencia local de `qwen2.5-coder:7b`.
- Una campaña de planificación del 18 de septiembre sobre `2cd2990`, con `ministral-3:14b`, contexto 4096 y semilla 0. Validación: 76–77 decisiones correctas de 79 y 61–62 planes exactos de 66, en tres repeticiones; `unsafe` 5,1 %. También recoge desarrollo, adversarios, variación lingüística y cambios controlados.
- Tres ejecuciones visuales con los mismos resultados.

El informe de planificación enlazado no contiene esa campaña de septiembre. El informe visual disponible presenta una ejecución y pide repetir tres veces antes de interpretar sus cifras como criterio de publicación. No se han localizado tres registros independientes que permitan verificar la repetición. Estas discrepancias no demuestran que las ejecuciones no se realizaran: impiden acreditarlas con las referencias actuales.

Se conservan los números de planificación y de pruebas como resultados comunicados, señalando su procedencia. Para el gráfico se interpreta exclusivamente la ejecución conservada. No se afirma que toda la suite esté superada ni que el modelo tenga un 97 % de exactitud financiera.

Para cerrar la trazabilidad hay que adjuntar comando, fecha, revisión del código y del evaluador, salida completa y configuración no sensible de cada ejecución. En las suites, delimitar aplicaciones y dependencias reales o simuladas; en inferencia, modelo, proveedor, hardware, calentamiento y concurrencia. Las tres repeticiones deben conservarse por separado. No incluir secretos, tokens ni conversaciones reales.

## Criterios de lectura

- `outcome` clasifica la salida como plan, aclaración o petición no admitida; puede acertar aunque el plan tenga campos incorrectos.
- El porcentaje de plan exacto usa los casos que esperaban un plan, no necesariamente todos los ejemplos del conjunto.
- `unsafe` es una medida del banco de pruebas de consultas, no de pagos no autorizados ni una tasa general de incidentes de seguridad. Parte de la evaluación incluye defensas deterministas.
- Un esquema válido no garantiza que el significado de la pregunta se conserve.
- Los tests con dobles prueban reglas y protocolos, no la calidad de un proveedor real.
- Un verificador de respuestas necesita informar también de cuántos casos llegaron a responder: rechazar todo puede producir un cero engañoso de inconsistencias.
- En la evaluación visual, `shadow` preserva el gráfico de referencia, pero espera al planificador o a su presupuesto y añade latencia.
- Una sola pareja evaluable no permite afirmar estabilidad general; 3 propuestas adecuadas de 3 válidas no equivalen a 14 casos resueltos.
- No se suman ni comparan como equivalentes latencias de campañas con recorridos o condiciones diferentes.
