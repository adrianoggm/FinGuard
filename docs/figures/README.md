# Capturas para la memoria

La memoria reserva tres espacios. Al colocar los PNG con estos nombres, el documento los incorpora automáticamente mediante `\captura`; mientras no existan, muestra un recuadro rotulado.

| Archivo en este directorio | Contenido esperado | Altura máxima |
| --- | --- | --- |
| `web-conversation.png` | Vista de la web con conversación, gráfico y acceso a la evidencia, usando el entorno sintético. | 6,5 cm |
| `grafana-dashboard.png` | Panel de Grafana con resultados, decisiones y tiempos, con sus títulos y leyenda visibles. | 3,5 cm |
| `conversation-trace.png` | Traza expandida de un turno, mostrando lectores, validaciones y tiempos. | 4 cm |

Las imágenes conservan su proporción y no se recortan. Conviene capturar sólo la zona relevante, sin el escritorio ni paneles ajenos, para mantener legible el contenido al tamaño de la memoria.

Después de añadir cada captura, retirar «Captura pendiente de incorporar» de su pie y comprobar visualmente el PDF. Las alturas se especifican en las llamadas a `\captura` de [memoria-finguard.tex](../memoria-finguard.tex); cambiarlas puede alterar la paginación.

Compilar desde `docs` dos veces para actualizar índice y referencias:

```powershell
pdflatex -interaction=nonstopmode -halt-on-error memoria-finguard.tex
pdflatex -interaction=nonstopmode -halt-on-error memoria-finguard.tex
```

Los diagramas de arquitectura se conservan como TikZ. `orchestrator-trace.tex` es la figura del recorrido interno y no debe sustituirse por estas capturas.
