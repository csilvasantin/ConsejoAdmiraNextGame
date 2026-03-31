# 2026-03-31 · Canal Windows local y separacion Consejo/Equipo

AdmiraNext Team

## Trabajo realizado

- Implementado un canal de automatizacion local para Windows en `AdmiraNext Control`.
- `PC Sitges 3 Monitores` deja de depender de `ssh.enabled` y pasa a operar mediante `automation.channel: windows-local`.
- El canal Windows local resuelve:
  - foco de ventana;
  - envio de prompts por portapapeles + `SendKeys`;
  - aprobacion manual para `Codex`, `Claude` y `Terminal`;
  - captura local del escritorio;
  - deteccion de estado visible de `Codex`.
- Verificado un envio real contra un PowerShell auxiliar local usando el endpoint `/api/teamwork/send`.
- El historial devolvio `ok: true` y genero captura de evidencia para `PC Sitges 3 Monitores`.

## Ajuste visual

- Eliminada la etiqueta visible `Workers` de dashboard y control.
- Separacion visual reforzada entre:
  - `Consejo de Administracion` para todos los Mac;
  - `Equipo` para todos los PC.
- Actualizados textos, contadores, chips y subtitulos para usar lenguaje de negocio en lugar de la etiqueta tecnica `worker`.
- Subida la version visible a `v2.3.0`.
- Subida la version del paquete a `0.3.0`.
- Añadido `cache busting` nuevo para `styles`, `app`, `teamwork` y `machines.json`.

## Estado operativo

- `PC Sitges 3 Monitores` queda identificado como este mismo equipo Windows (`OmenGdG`).
- La ficha mantiene `ssh.enabled: false`, pero ya no queda bloqueada porque el canal activo es local Windows.
- Snapshot local funcionando con captura de escritorio.
- Auto-approve sobre Windows queda soportado de forma manual desde botones; la deteccion automatica tipo watchdog sigue centrada en el flujo macOS.
