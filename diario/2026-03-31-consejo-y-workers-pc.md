# 2026-03-31 · Consejo y workers PC

AdmiraNext Team

## Trabajo realizado

- Separado el modelo del equipo en dos capas visibles: `council` para el consejo de direccion y `worker` para el equipo de trabajo.
- Marcados los Macs actuales como `unitType: council`.
- Añadidos tres PCs de ejemplo como workers:
  - `PC Runner 01`
  - `PC OCR 01`
  - `PC Bot 01`
- Añadidos `agentProfile` y `capabilities` para empezar a describir los PCs como agentes de ejecucion.
- Actualizado el dashboard para renderizar dos bloques distintos: `Consejo de direccion` y `Equipo de trabajo`.
- Actualizado `AdmiraNext Control` para ordenar y mostrar por grupos, incluyendo capacidades visibles en los workers.
- Los workers sin canal remoto ya no desaparecen: se muestran como `Pendiente` o `Sin canal`, dejando claro que existen pero aun no estan conectados al hub.
- Subida la version visible del dashboard a `v2.2.0`.
- Subida la version del paquete a `0.2.0`.

## Objetivo de esta fase

Pasar de un panel centrado solo en personas+Macs a un sistema con dos niveles:

- consejo que decide;
- workers que ejecutan.

## Siguiente paso natural

- conectar al menos un PC worker real por Tailscale/SSH o canal equivalente;
- permitir asignacion automatica por capacidad;
- introducir cola de tareas por worker.
