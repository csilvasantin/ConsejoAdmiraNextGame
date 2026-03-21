# AdmiraNext Team

Panel ligero para controlar el estado de los miembros del equipo, centrado en sus ordenadores.

## Objetivo

Tener una vista simple y operativa de:

1. qué máquinas existen;
2. a qué miembro pertenecen;
3. cuál es su estado actual;
4. cuándo fue la última actualización;
5. notas rápidas de operación.

## Estado actual

Este MVP incluye:

1. servidor Node sin dependencias externas;
2. API JSON local;
3. panel web para ver equipos y máquinas;
4. cambio rápido de estado desde la interfaz;
5. almacenamiento en `data/machines.json`.

## Estados disponibles

1. `online`
2. `busy`
3. `offline`
4. `maintenance`

## Arranque

```bash
cd /Users/csilvasantin/Documents/Codex/AdmiraNext-Team
npm start
```

Después abre:

```text
http://127.0.0.1:3030
```

## API

### Listar máquinas

```text
GET /api/machines
```

### Actualizar estado

```text
POST /api/machines/:id/status
Content-Type: application/json
{
  "status": "busy",
  "note": "Instalando bots en equipo nuevo"
}
```

## Siguientes pasos recomendados

1. añadir login o clave simple;
2. distinguir miembros, equipos y ubicaciones;
3. añadir comprobación real de salud de cada ordenador;
4. integrar bots o agentes por máquina;
5. guardar historial de estados.
