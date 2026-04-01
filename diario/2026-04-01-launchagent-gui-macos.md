# 2026-04-01 · LaunchAgent GUI para control macOS

AdmiraNext Team

## Trabajo realizado

- Se ha preparado un arranque estable para macOS dentro de la sesion grafica real, evitando depender de procesos headless por SSH.
- El repositorio incorpora ahora scripts `npm` para `agent:doctor`, `agent:install`, `agent:uninstall` y `start:gui`.
- `agent:install` genera e instala `~/Library/LaunchAgents/com.admiranext.control.plist` con `RunAtLoad`, `KeepAlive` y sesion `Aqua`.
- `agent:doctor` comprueba sesion GUI, `System Events`, `screencapture` y la API local para detectar rapido si faltan permisos.

## Motivo

- El panel podia seguir vivo por SSH y Funnel, pero sin acceso real a la sesion grafica de macOS.
- En ese escenario aparecian estados de texto como `sin sesion grafica` o `SSH activo`, pero no snapshots reales.
- Con el `LaunchAgent` el nodo Mac puede mantenerse arrancado tras login y con el contexto adecuado para capturar pantalla.

## Siguiente paso recomendado

- Instalar este agente en `macmini`.
- Repetir el mismo patron en cada Mac que deba exponer previews vivos al hub publico.
