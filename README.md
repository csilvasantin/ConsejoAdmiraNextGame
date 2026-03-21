# AdmiraNext Team

Panel ligero para controlar el estado de los miembros del equipo, centrado en sus ordenadores.

## Objetivo

Tener una vista simple y operativa de:

1. qué máquinas existen;
2. a qué miembro pertenecen;
3. cuál es el rol de cada persona y de cada equipo;
3. cuál es su estado actual;
4. en qué está trabajando cada uno ahora mismo;
5. cuándo fue la última actualización;
6. notas rápidas de operación.

## Estado actual

Este MVP incluye:

1. servidor Node sin dependencias externas;
2. API JSON local;
3. panel web para ver equipos y máquinas;
4. cambio rápido de estado desde la interfaz;
5. foco actual de trabajo por máquina;
6. almacenamiento en `data/machines.json`.

## Estados disponibles

1. `online`
2. `busy`
3. `offline`
4. `maintenance`

## Arranque

```bash
cd /Users/Carlos/Documents/AdmiraNext-Team
npm start
```

Después abre:

```text
http://127.0.0.1:3030
```

## API local

### Listar máquinas

```text
GET /api/machines
```

### Sincronizar estado y foco

```text
POST /api/machines/:id/sync
Content-Type: application/json
{
  "status": "busy",
  "currentFocus": "Instalando bots en equipo nuevo",
  "note": "Coordinando onboarding"
}
```

## Publicación web

La publicación pública funciona en modo solo lectura con GitHub Pages y carga `machines.json` directamente.

## Siguientes pasos recomendados

1. añadir login o clave simple;
2. separar miembros, equipos y tareas en entidades propias;
3. añadir comprobación real de salud de cada ordenador;
4. integrar bots o agentes por máquina;
5. guardar historial de estados y cambios de foco.
