# Extender FinGuard con servicios e idiomas

Esta propuesta amplía la explicación de la memoria. Distingue mecanismos existentes de ampliaciones pendientes; no implica que haya conectores operativos con Booking, Rastreator u otros agentes.

## Qué existe hoy

El orquestador separa dominio, aplicación, infraestructura y composición de dependencias. Un ejemplo concreto es [LlmProviderPort](../../apps/orchestrator/src/finguard_orchestrator/domain/ports/llm_provider.py): el caso de uso conoce una interfaz y los proveedores la implementan. El [arranque](../../apps/orchestrator/src/finguard_orchestrator/bootstrap/main.py) construye los adaptadores según la configuración.

La [visión de producto, apartado 7](../vision.md#7-a2a-readiness) propone una frontera A2A para interoperar con agentes. En el estado revisado para esta memoria, las aplicaciones implementadas son web, orquestador, Banking API y canal de voz. La interoperabilidad A2A permanece como ampliación de diseño.

## Una interfaz por capacidad, varias implementaciones

Una búsqueda de alojamiento tendría una solicitud con destino, fechas y ocupación, y devolvería ofertas identificadas, precios con moneda y condiciones. Una cotización de seguros tendría sus propios datos, coberturas, vigencia y resultados. Estos son contratos ilustrativos: habría que concretarlos al implementar cada capacidad.

Varios proveedores que ofrecen la misma capacidad pueden implementar una interfaz común. Los contratos de alojamiento y seguros se mantienen separados, porque tienen datos y reglas diferentes. La implementación elegida traduce el protocolo del proveedor al contrato interno.

| Capa | Cambio al añadir una capacidad |
| --- | --- |
| Dominio | Modelos de solicitud y resultado, invariantes e interfaz del servicio necesario. |
| Aplicación | Caso de uso, relación con el contexto, permisos y pasos de revisión o aprobación si hay una acción. |
| Infraestructura | Cliente API o adaptador de agentes, traducción de mensajes y tratamiento de errores y tiempos de espera. |
| Composición y configuración | Registro del adaptador, selección del proveedor y sus parámetros de conexión. |

Booking y Rastreator son ejemplos de posibles proveedores, no afirmaciones sobre la disponibilidad de sus APIs, agentes o acuerdos de acceso. La conexión concreta dependería del servicio y del contrato de integración disponibles.

Cambiar de proveedor dentro de un contrato compatible afecta principalmente al adaptador y a su configuración. Añadir una capacidad nueva requiere también modelar su significado y coordinar su caso de uso. Un canal de entrada nuevo puede reutilizar capacidades existentes mediante un adaptador que convierta sus mensajes en consultas y comandos internos.

Una reserva o contratación tendría un recorrido propio de previsualización y autorización. La respuesta de un agente externo no constituiría por sí misma una autorización financiera. Si la operación necesitara un pago, éste seguiría pasando por la capacidad bancaria correspondiente.

Las pruebas de contrato comprobarían que todos los adaptadores cumplen la misma interfaz. Los episodios conversacionales comprobarían que la persona puede pedir, aclarar, revisar y cancelar la nueva tarea, además de su comportamiento cuando el proveedor falla.

## Ampliación lingüística

### Punto de partida

La implementación está centrada en español. Esto es visible en:

- los [vocabularios de intención](../../apps/orchestrator/src/finguard_orchestrator/domain/services/intent_lexicon.py) y [hechos explícitos](../../apps/orchestrator/src/finguard_orchestrator/domain/services/explicit_lexicon.py);
- la [normalización del español](../../apps/orchestrator/src/finguard_orchestrator/domain/services/spanish.py) y los lectores de importes y periodos;
- los [textos de respuesta](../../apps/orchestrator/src/finguard_orchestrator/domain/services/answer_writer.py) y [formatos de la web](../../apps/web/src/format.ts);
- el idioma de transcripción y la voz seleccionada en la [configuración de audio](../../apps/voice-gateway/.env.defaults).

La posibilidad de cambiar el idioma del transcriptor o usar un modelo multilingüe no demuestra que los lectores, las confirmaciones y la interfaz funcionen en ese idioma.

### Diseño propuesto

Se propone introducir un perfil lingüístico explícito, seleccionado al iniciar la conversación y conservado entre turnos. No existe todavía como mecanismo completo de selección de idiomas.

| Parte | Ampliación prevista |
| --- | --- |
| Entrada | Normalización y lectores por idioma, con reglas propias para números, fechas, nombres y negaciones. |
| Interpretación con modelo | Instrucciones y ejemplos del idioma seleccionado, manteniendo el mismo contrato de salida y las validaciones. |
| Dominio | Planes, importes con moneda y operaciones independientes de la forma lingüística de la petición. |
| Presentación | Catálogos de mensajes y formato regional para web y respuesta conversacional. |
| Voz | Transcripción y síntesis compatibles con el idioma, conservando identidad de turno y reglas de confirmación. |
| Evaluación | Episodios propios por idioma y pares equivalentes que deban producir el mismo plan. |

El idioma, el formato regional, la zona horaria y la moneda son dimensiones diferentes. Hablar español no determina una zona horaria ni convierte los importes a euros. Un cambio de idioma tampoco debe modificar por sí solo la cuenta o una operación pendiente.

La implementación debería comenzar extrayendo los elementos lingüísticos existentes detrás de interfaces y catálogos, conservando primero el comportamiento en español. Después se incorporaría un segundo idioma y se evaluaría con casos nativos, además de pares equivalentes como «el mes anterior» y «last month». Nombres, separadores decimales, fechas ambiguas y confirmaciones necesitan pruebas específicas.

## Capacidad operativa

Los servicios separados permiten dimensionar por separado la inferencia, el audio y las APIs. Ese reparto no convierte automáticamente la aplicación en una solución escalable horizontalmente.

La política de modelos actual se mantiene en memoria de cada proceso; sus cambios tendrían que distribuirse de forma consistente entre réplicas. Las sesiones de voz y sus conexiones WebSocket requieren una estrategia de encaminamiento y recuperación. La base de datos, los límites de conexiones y el servicio de inferencia también deben medirse bajo carga.

Docker Compose describe el entorno local. La elección de una plataforma de despliegue, el balanceo y el autoescalado son trabajo posterior, sujeto a esas mediciones.
