# Diccionario de datos

## Clase Caso (vet_agent/estado.py)

| campo | qué significa | tipo | ejemplo |
|---|---|---|---|
| especie | Especie animal afectada. | texto | porcino |
| sistema_productivo | Tipo de plantel o sistema productivo. | texto | plantel de engorda |
| ubicacion | Ubicación geográfica del caso. | texto | Región de O'Higgins |
| periodo | Periodo o fecha relevante del brote. | texto | julio 2026 |
| signos_clinicos | Lista de signos clínicos observados. | lista | ["fiebre", "dificultad respiratoria", "abortos en reproductoras"] |
| mortalidad | Descripción de la mortalidad observada. | texto | 4% semanal, sobre lo normal |
| resultados_laboratorio | Resultados de pruebas de laboratorio. | lista | ["PCR positivo a PRRSV"] |
| clima | Condiciones climáticas en la zona. | diccionario | {"temperatura_promedio": "8°C", "precipitaciones": "altas"} |
| historial_brotes | Historial de brotes previos. | texto | (no informado) |
| movimientos_animales | Movimientos o ingresos de animales. | texto | ingreso de 200 lechones hace 3 semanas |
| manejo_bioseguridad | Medidas de bioseguridad aplicadas o su estado. | texto | sin cuarentena de ingreso |
| ventas_tests | Venta/uso de kits diagnósticos en la zona (campo en Estado, no en Caso). | texto | (no informado) |

## Reglas de normalización

- **Temperatura**: siempre en °C (ej. `8°C`).
- **Mortalidad**: expresar como porcentaje cuando sea numérica (ej. `4% semanal`). Si es cualitativa, mantener descripción clara.
- **Fechas/periodos**: usar formato descriptivo consistente (ej. `julio 2026`).
- **Listas**: signos clínicos y resultados de laboratorio deben ser listas de strings.
- **Valores vacíos**: cuando no hay información, el campo puede omitirse (Caso usa `total=False`).
