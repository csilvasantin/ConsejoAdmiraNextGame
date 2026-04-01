# 2026-04-01 · Panel de control operativo

AdmiraNext Team

## Trabajo realizado

- La portada del panel se ha rehecho para priorizar operativa real: ahora resume `SSH listo`, altas completas y equipos que piden ayuda.
- Las tarjetas del dashboard se ordenan por prioridad operativa para que los bloqueos y onboarding incompletos aparezcan antes.
- Cada ficha muestra ya el area del equipo, el estado de acceso remoto, el comando SSH guardado y el checklist de alta cuando existe.
- Se ha añadido un bloque visible de `Siguiente bloqueo` para no perder las ayudas pedidas por el nuevo fichaje.
- La portada incorpora acceso directo a `Alta CEO` junto a la entrada general y al panel de control remoto.
- Se ha corregido el fallback estatico del panel `teamwork` y la etiqueta de pantallas del preview multi-monitor para que no cambie Claude y Studio al refrescar snapshots.
- El despliegue de GitHub Pages publica ahora tambien `ceo.html`, `alta-ceo.html` y `matrix-pills.jpg`, evitando diferencias entre local y publico.
- El servidor local ya sirve imagenes estaticas con su MIME correcto (`jpg`, `png`, `svg`), evitando que recursos del panel se entreguen como texto plano.

## Estado actual

- El dashboard principal sirve ya como vista de situacion, no solo como inventario.
- El panel de control remoto queda versionado como `v2.4.1`.
- El alias `control.html` vuelve a quedar sincronizado con la version real y con el cache-buster publicado para evitar redirecciones a builds antiguas.
- El panel deja de usar `offline` como cajon de sastre: ahora diferencia `estado del equipo`, `preview` y `canal remoto`, para no confundir equipos activos sin snapshot o sin canal con maquinas realmente caidas.
- El cliente vuelve a refrescar maquinas, snapshots y watchdog en bucle, de forma que los previos online no se quedan congelados tras la carga inicial.
- La publicacion publica y la local vuelven a estar alineadas en rutas y recursos.
