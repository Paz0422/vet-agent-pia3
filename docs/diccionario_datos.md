# Diccionario de datos

## Clase Caso (vet_agent/estado.py)

| campo | qué significa | tipo | ejemplo |
|---|---|---|---|
| especie | Especie animal afectada. | texto | aves de corral (gallinas ponedoras) |
| sistema_productivo | Tipo de plantel o sistema productivo. | texto | plantel de ponedoras comerciales, 40.000 aves |
| ubicacion | Ubicación geográfica del caso. | texto | Región de Valparaíso, cerca de un humedal costero |
| periodo | Periodo o fecha relevante del brote. | texto | mayo 2026 |
| signos_clinicos | Lista de signos clínicos observados. | lista | ["mortalidad súbita", "cianosis de cresta y barbilla", "caída brusca de la postura", "signos nerviosos"] |
| mortalidad | Descripción de la mortalidad observada. | texto | 12% en 48 horas en un galpón |
| resultados_laboratorio | Resultados de pruebas de laboratorio. | lista | ["RT-PCR positivo a influenza A (gen M)", "subtipificación H5N1 por RT-PCR"] |
| clima | Condiciones climáticas en la zona. | diccionario | {"temperatura_promedio": "10°C", "humedad_relativa": "85%", "precipitaciones": "moderadas"} |
| historial_brotes | Historial de brotes previos. | texto | detecciones de H5N1 en aves silvestres de la costa en temporadas anteriores |
| movimientos_animales | Movimientos o ingresos de animales. | texto | ingreso de pollitas de recría hace 2 semanas; aves migratorias en el humedal a 2 km |
| manejo_bioseguridad | Medidas de bioseguridad aplicadas o su estado. | texto | galpones abiertos sin malla antiaves, agua de bebida desde un tranque sin tratar |
| ventas_tests | Venta/uso de kits diagnósticos en la zona. | texto | (no informado) |

## Reglas de normalización

- **Temperatura**: siempre en °C (ej. `10°C`).
- **Humedad relativa**: expresar en porcentaje (ej. `85%`).
- **Mortalidad**: expresar como porcentaje cuando sea numérica (ej. `12% en 48 horas`). Si es cualitativa, mantener descripción clara.
- **Fechas/periodos**: usar formato descriptivo consistente (ej. `mayo 2026`).
- **Listas**: signos clínicos y resultados de laboratorio deben ser listas de strings.
- **Valores vacíos**: cuando no hay información, el campo puede omitirse (Caso usa `total=False`).
