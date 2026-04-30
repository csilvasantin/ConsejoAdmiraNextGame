# HANDOFF — Consejo AdmiraNext

Actualizado: 2026-04-30  
Proyecto: `32.-ConsejoAdmiraNextGame`

## Punto de entrada

- URL pública: [https://csilvasantin.github.io/32.-ConsejoAdmiraNextGame/council-scumm.html](https://csilvasantin.github.io/32.-ConsejoAdmiraNextGame/council-scumm.html)
- Versión visible: `AdmiraNext Consejo v26.30.04.2`
- Rama: `main`
- Commit actual: `3883d45`

## Qué comprobar al retomar

1. Abrir la URL pública.
2. Verificar arriba que pone `AdmiraNext Consejo v26.30.04.2`.
3. Si se va a desarrollar, clonar y actualizar:

```bash
git clone https://github.com/csilvasantin/32.-ConsejoAdmiraNextGame.git
cd 32.-ConsejoAdmiraNextGame
git checkout main
git pull
git rev-parse --short HEAD
```

El hash debe coincidir con `3883d45` o ser posterior.

## Estado operativo actual

### Consejo

- La build pública y la copia local del HTML suelen mantenerse sincronizadas.
- La referencia principal para probar el producto es siempre GitHub Pages.
- Tras cada update significativo se envía Telegram con:
  - URL exacta
  - versión visible

### Yarig.AI

- CLI disponible con alias equivalentes:
  - `/yarig on` y `/yarig.ai on`
  - `/yarig off` y `/yarig.ai off`
  - `/yarig login` y `/yarig.ai login`
  - `/yarig logout` y `/yarig.ai logout`
  - `/yarig sincro` y `/yarig.ai sincro`
- `/help` es toggle y usa las 3 ventanas superiores.
- La mesa de Yarig reparte:
  - izquierda: `Finalizadas`
  - centro: `En proceso`
  - derecha: `Pendientes`
- La ventana central ya incluye controles para la tarea en curso:
  - `Finalizar`
  - `Pausar`
  - `Cancelar`

## Últimos cambios relevantes

### `v26.30.04.1`

- `Yarig.AI` gana `login` y `logout` desde CLI.
- `/help` pasa a ser toggle y se pinta en las 3 ventanas superiores.

### `v26.30.04.2`

- Ventanas laterales superiores más altas.
- Ventana central más ancha y algo más alta.
- La cota superior de las tres se mantiene.

## Riesgos y notas abiertas

- La sincronización de Yarig puede no corresponderse exactamente con una pestaña manual de Chrome si la sesión automatizada no está alineada.
- La lectura directa de una pestaña normal de Chrome puede verse limitada si no está activado:
  - `Ver > Opciones para desarrolladores > Permitir JavaScript desde Eventos de Apple`
- Cuando haya watcher persistente de Yarig, las acciones de control temporalmente pueden necesitar liberar/reusar el perfil de automatización.

## Siguiente foco recomendado

1. Afinar la bidireccionalidad real con Yarig para que `Pendientes`, `En proceso` y `Finalizadas` reflejen exactamente la sesión visible del usuario.
2. Validar a fondo los controles de tarea de la ventana central sobre casos reales de Yarig.
3. Seguir puliendo geometría/legibilidad de overlays solo después de asegurar la sincronización.

## Archivos clave

- [`/tmp/council-publish/public/council-scumm.html`](/tmp/council-publish/public/council-scumm.html)
- [`/tmp/council-publish/council-api.py`](/tmp/council-publish/council-api.py)
- [`/tmp/council-publish/tools/yarig-tasks-sync.mjs`](/tmp/council-publish/tools/yarig-tasks-sync.mjs)
- [`/tmp/council-publish/CLAUDE.md`](/tmp/council-publish/CLAUDE.md)

## Convención para futuros handoff

Cuando el usuario escriba `handoff`, actualizar este archivo con:

- fecha
- URL pública
- versión visible
- URL directa de este `HANDOFF.md`
- commit
- últimos cambios
- estado real de Yarig
- riesgos abiertos
- siguiente paso recomendado

Y además enviar Telegram con:

- URL pública del Consejo
- versión visible
- URL directa del `HANDOFF.md`
- commit publicado
