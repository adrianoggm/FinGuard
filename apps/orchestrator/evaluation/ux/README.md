# Conversaciones de UX — 7 de octubre de 2026

Diez conversaciones de seis turnos con cambios de tema, correcciones, elipsis,
abreviaturas y erratas. Las frases y sus resultados esperados están en
[`conversations.yaml`](conversations.yaml). La respuesta completa, los planes
ejecutados y las discrepancias de cada turno quedan en [`latest.json`](latest.json).

Desde `apps/orchestrator`:

```powershell
uv run python scripts/evaluate_conversation_ux.py
uv run python scripts/evaluate_conversation_ux.py --case suscripciones --output evaluation/ux/subscriptions.json
```

El proceso termina con código 1 si hay discrepancias. `--stub` sirve exclusivamente
para revisar el enrutamiento sin modelo; no acredita comprensión semántica.

## Qué se está midiendo

El planificador consulta **Ollama local, qwen2.5-coder:7b**, con contexto y parámetros
del servicio. Los turnos pasan por el controlador HTTP y conservan la conversación
devuelta. Se comprueba la ruta y, según el caso, periodo, categoría, comercio,
dirección, agrupación o ausencia de llamadas bancarias.

**La banca y el extractor de pagos son simulados.** No se envía dinero ni se valida
la exactitud de importes reales. El ensayo de Bizum verifica el flujo de conversación,
pero no acredita la comprensión del extractor de pagos desplegado. La fecha bancaria
es la fija del corpus, indicada en el informe, no la fecha de ejecución. Los tiempos
son observaciones de esta máquina y no un benchmark de latencia.

La prueba de comparación comprueba ambos operandos en el orden interno de ejecución
(julio, junio). El desglose por comercio usa la dimensión `COUNTERPARTY` que utiliza
actualmente ese lector del servicio.

## Conversaciones

| Caso | Secuencia abreviada | Qué debe conservar o cambiar |
|---|---|---|
| Suscripciones | «q llevo gastado… 9 meses» → «solo Netflix» → «y este mes?» → «no, Spotify» | Cambiar comercio o periodo conservando los demás filtros. |
| Cambio de tema | Supermercado en julio → saldo de hucha → contactos → volver a junio | Recuperar la consulta tras las preguntas intercaladas. |
| Correcciones | Julio → junio → gasolina → restaurantes → todos los gastos → contar | Aplicar cada corrección y eliminar el filtro cuando se solicita. |
| Aclaraciones | Supermercado → pregunta por cuentas → «en todas» → cambiar agrupación | Resolver la aclaración sin un bucle y mantener periodo y categoría. |
| Erratas | «super… mes pasao» → «restauantes» → «esta semna» → gasolina | Reconocer variantes sin inventar comercios ni perder el periodo. |
| Bizum | «bixum» → importe → «mejor 25» → «no» → saldo | Corregir la propuesta y cancelarla sin confirmar un pago. |
| Consulta u operación | Lo enviado a Marta → lo recibido de «ella» → nueva orden | Resolver el referente, cambiar dirección y distinguir lectura de operación. |
| Comparaciones | Junio frente a julio → cambiar categoría → solo julio → comercio | Mantener ambos operandos hasta que se pide una sola ventana. |
| Gráficos | Categorías → meses → líneas → explicar julio → restaurantes | Mantener coherencia entre gráfico, evidencia y consulta. |
| Recuperación | Supermercado → «no era eso» → gasolina → «olvidalo» → ingresos | Gestionar el rechazo y evitar que una nueva consulta herede filtros ajenos. |

## Correcciones implementadas

- Los marcadores de continuación ya no se incluyen en el nombre de comercio:
  «Y Spotify», «solo Netflix» y «no, Spotify» modifican el filtro conservando el periodo.
  Las expresiones de alcance, exclusión y control siguen sus rutas propias.
- «En todas», «todas» y «en todas ellas» resuelven una pregunta pendiente sobre
  cuentas. Fuera de esa aclaración no amplían el alcance bancario.
- Una continuación formada solo por «y» y una categoría reconocida, incluida una
  errata que el vocabulario ya reconoce, conserva el periodo sin depender del modelo.
- La descripción de la consulta utiliza etiquetas en español. El formulario para
  guardar la métrica queda dentro de un desplegable y la muestra indica cuántos
  movimientos se muestran del total.

Las regresiones de comercio y categoría están en `test_subscription_conversation.py`;
las de selección de cuentas, en `test_account_scope_workflow.py`.

## Límites abiertos

Última ejecución completa: **52 de 60 turnos cumplen las aserciones; 5 de 10
conversaciones las cumplen enteras**. Pasan completas suscripciones, cambio de tema,
correcciones, Bizum con extractor simulado y gráficos.

| Conversación | Turnos fallidos | Observación |
|---|---|---|
| Aclaraciones | 5 | «Mejor por categoría» vuelve a pedir el periodo. |
| Erratas | 3, 4 | «Esta semna» no cambia correctamente la ventana; el siguiente turno hereda el fallo. |
| Consulta u operación | 2, 3, 4 | Pierde el referente «ella», no enruta «quiero mandarle» a pago y falla la cancelación posterior. |
| Comparaciones | 2 | Cambiar a gasolina pierde un operando de la comparación. |
| Recuperación | 4 | «Olvidalo» produce lecturas bancarias en vez de gestionar el abandono. |

Esta batería es deliberadamente exigente y conserva expectativas incumplidas:
las discrepancias de `latest.json` son trabajo pendiente, no resultados aceptados.
Una ejecución correcta de una frase no garantiza sus paráfrasis ni otro modelo.
Antes de dar por resuelta la UX hay que cerrar los fallos restantes y repetir estas
conversaciones contra la aplicación desplegada con su banca de prueba y su extractor
de pagos reales, además de revisar la presentación en navegador.
