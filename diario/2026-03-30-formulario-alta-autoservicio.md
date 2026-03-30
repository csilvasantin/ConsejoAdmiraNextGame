# Diario - 2026-03-30

## Proyecto

AdmiraNext Team

## Trabajo realizado

- Se ha corregido la inconsistencia del estado `idle` para que datos, UI y API acepten el mismo conjunto de estados.
- Se ha creado una nueva pagina de alta autoservicio en `public/new-member.html`.
- El formulario recoge persona, maquina, acceso tecnico y checklist de onboarding en una sola ficha.
- La pagina guarda borrador local, genera vista previa en tiempo real y permite copiar un resumen o descargar la ficha en JSON.
- En modo servidor local, la ficha se registra directamente en `data/machines.json` mediante `POST /api/machines`.
- En GitHub Pages, el formulario funciona en modo exportacion para que el nuevo fichaje pueda rellenarlo sin depender del panel editable.
- La portada y el panel de control enlazan ya a la nueva entrada de alta.
- Se ha actualizado el despliegue de GitHub Pages para publicar la nueva pagina y su script.

## Estado actual

- Alta local prevista en `http://127.0.0.1:3030/new-member.html`.
- Alta publica prevista en `https://csilvasantin.github.io/AdmiraNext-Team/new-member.html`.
- El sistema ya permite incorporar una nueva maquina con menos friccion y sin carga manual inicial.
- Se ha añadido una URL guiada especifica para el lado creativo del consejo cuando el nuevo MacBook Air llega completamente limpio, sin Tailscale, GitHub ni bots instalados.
- La URL guiada incluye ya un bloque de 10 pasos con foco en permisos de macOS, Tailscale, GitHub CLI, onboarding y validacion final.
- La misma URL genera ahora un script `.command` de arranque express para automatizar Homebrew, `gh`, Python, Tailscale, clon de `onboarding` y lanzamiento del setup de bots con las pausas manuales justas.
- El arranque express instala tambien Google Chrome y lo fija como navegador por defecto para unificar el flujo operativo desde el primer uso.
- Claude y Codex pasan a ser obligatorios en el alta: aparecen como checklist propio y el arranque express abre sus paginas oficiales y bloquea la continuidad hasta que ambas apps esten instaladas y abiertas.
- El flujo ya contempla modo delegado: si el nuevo Mac activa `Inicio de sesion remoto` y comparte `usuario + IP`, la IA puede asumir la mayor parte del alta por SSH y limitar la intervencion humana a logins y permisos de macOS.
- Para futuros equipos, el paso minimo de Terminal queda explicitado con dos comandos: `sudo systemsetup -setremotelogin on` y `ipconfig getifaddr en0`.
- Se han añadido rutas cortas de acceso (`/alta`, `/creativa`, `/alta-creativa`) para evitar errores al teclear la URL manualmente desde equipos nuevos.
- Se ha dado de alta una entrada provisional para `MacBook Air creativo`, en estado `maintenance`, con checklist vacia y foco en primer arranque para poder seguir el onboarding desde el panel.
- Se ha ejecutado una incorporacion casi completa por SSH sobre `192.168.0.120`: Command Line Tools, Homebrew, Python, `gh`, Chrome, Codex, onboarding copiado y bots levantados en segundo plano.
- El estado real del equipo ya queda reflejado en `machines.json` con Tailscale y SSH operativos, `Claude` y `Codex` instalados y `ClaudeBot`/`CodexBot` activos. El unico punto que seguia pendiente al cierre era completar `gh auth login`.
- El cierre final ya ha quedado completado: `gh auth status` valido en el Mac nuevo, Tailscale conectado, bots vivos y ficha actualizada a estado `idle`, lista para arrancar el onboarding diario.
- Se fija una semantica nueva para activacion: `onboarding` significa solo onboarding local de la sesion actual, mientras que `onboarding all` significa refresco global de todos los equipos alcanzables de `Admira Next`.
- `AdmiraNext-Team` ya expone `POST /api/teamwork/onboarding-all`, el panel de `teamwork` muestra un atajo visible y el backend evita duplicar `Claude` y `Codex` en una misma maquina prefiriendo `Codex`, luego `Claude` y despues `Terminal`.
