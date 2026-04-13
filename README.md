# ConsejoAdmiraNextGame

> Panel de control del equipo AdmiraNext reimaginado como aventura gráfica point-and-click al estilo LucasArts 16-bit.

---

## 🎮 Demo en vivo

**[→ Jugar ahora](https://csilvasantin.github.io/ConsejoAdmiraNextGame/council-scumm.html)**

---

## Qué es esto

Una interfaz alternativa para el panel de gestión de equipo AdmiraNext, construida como aventura gráfica SCUMM al estilo de los clásicos de LucasArts (Monkey Island, Day of the Tentacle). En lugar de un dashboard tradicional, navegas por las salas de la oficina, interactúas con los miembros del equipo usando verbos clásicos y consultas el estado de cada máquina como si fuera una aventura gráfica de los 90.

El motor soporta dos modos:
- **Demo (GitHub Pages)** — datos ficticios, funciona en cualquier navegador sin instalación
- **Conectado** — conectado al servidor Node.js de AdmiraNext-Team, datos reales del equipo

---

## Controles

### Sistema SCUMM — 9 verbos

```
┌─────────┬─────────┬──────────┐
│   Dar   │  Coger  │   Usar   │
├─────────┼─────────┼──────────┤
│  Abrir  │  Mirar  │ Empujar  │
├─────────┼─────────┼──────────┤
│ Cerrar  │ Hablar  │  Tirar   │
└─────────┴─────────┴──────────┘
```

| Verbo | Acción |
|---|---|
| **Mirar** | Información del miembro (nombre, rol, área, máquina) |
| **Hablar** | Foco actual y notas del miembro |
| **Abrir** | Estado completo + checklist de onboarding |
| **Usar** | Copiar comando SSH de conexión |
| **Empujar** / **Dar** | Enviar un comando o prompt a la máquina |
| **Coger** | Sincronizar estado (requiere servidor local) |
| **Tirar** / **Cerrar** | Historial de comandos enviados |

**Clic en puerta** → navegar entre salas  
**Clic en verbo** → seleccionar verbo activo  
**Clic en personaje** → ejecutar verbo sobre ese personaje  
**Inventario (derecha)** → clic en nombre para seleccionar personaje

---

## Escenas

### Sala de Control
La sala principal donde trabaja el equipo. Cada miembro aparece como personaje pixel art frente a su mesa. El LED sobre la cabeza y el color de la camiseta reflejan su estado:

| Color | Estado |
|---|---|
| 🟢 Verde | Online |
| 🔵 Azul | Idle |
| 🟠 Naranja | Busy |
| ⚫ Gris | Offline |
| 🟣 Púrpura | Maintenance |

### Sala del Consejo
Los 8 consejeros de AdmiraNext en traje formal con corbata del color de su rol. Accesible por la puerta derecha de la oficina (o botón en el inventario).

---

## Ejecución local

```bash
git clone https://github.com/csilvasantin/ConsejoAdmiraNextGame
cd ConsejoAdmiraNextGame
npm start
# Abre http://localhost:3030/game.html
```

Requiere Node.js 18+. Sin dependencias externas.

---

## Arquitectura

```
ConsejoAdmiraNextGame/
├── index.html          ← Versión estática (GitHub Pages)
├── public/
│   └── game.html       ← Versión conectada al servidor
├── src/
│   ├── server.js       ← API REST Node.js (puerto 3030)
│   ├── store.js        ← Persistencia JSON
│   └── ssh-exec.js     ← Ejecución remota SSH
└── package.json
```

**Stack**: HTML5 Canvas · Pure JS · Node.js ES Modules · Press Start 2P · CSS CRT effect  
**Resolución**: 640×400px (320×200 game pixels × escala 2)

---

## Ecosistema AdmiraNext

| Proyecto | Descripción |
|---|---|
| [AdmiraNext-Team](https://github.com/csilvasantin/AdmiraNext-Team) | Panel de control base |
| [Yarig.Telegram](https://github.com/csilvasantin/Yarig.Telegram) | Bots Telegram del Consejo con IA |
| **ConsejoAdmiraNextGame** | Esta aventura gráfica |

---

*Inspirado en LucasArts SCUMM Engine (1987–1997): Maniac Mansion, Monkey Island, Day of the Tentacle, Full Throttle.*
