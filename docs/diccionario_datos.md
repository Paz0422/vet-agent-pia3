# Diccionario de datos

## Clase Caso (vet_agent/estado.py)

Los nombres de campo son los mismos de la Parte A de los escenarios de Investigación. Los ejemplos vienen de `CASO_H5N1` en `vet_agent/main.py`.

| campo | qué significa | tipo | ejemplo |
|---|---|---|---|
| especie | Especie animal afectada. | texto | aves de corral (gallinas ponedoras) |
| sistema_productivo | Tipo de plantel o sistema productivo. | texto | plantel de ponedoras comerciales (galpones abiertos sin malla antiaves) |
| region | Región donde ocurre el caso. | texto | Valparaíso |
| comuna | Comuna donde ocurre el caso. | texto | Concón |
| fecha_evento | Fecha del evento, en formato AAAA-MM-DD. | texto | 2026-05-01 |
| n_animales | Número de animales del plantel. | entero | 40000 |
| signos_clinicos | Signos clínicos observados. | lista de textos | ["mortalidad súbita", "cianosis de cresta y barbilla", "caída brusca de la postura", "signos nerviosos"] |
| mortalidad_pct | Mortalidad, en porcentaje. | decimal | 12.0 |
| morbilidad_pct | Morbilidad, en porcentaje. | decimal | 30.0 |
| resultados_laboratorio | Pruebas de laboratorio, cada una con `prueba`, `resultado` y `fecha`. | lista de diccionarios | [{"prueba": "RT-PCR", "resultado": "positivo a influenza A (gen M)", "fecha": "2026-05-03"}] |
| clima | Clima en la zona: `temperatura_media_c` (°C), `humedad_relativa_pct` (%) y `precipitacion_mm` (mm). | diccionario de decimales | {"temperatura_media_c": 10.0, "humedad_relativa_pct": 85.0, "precipitacion_mm": 15.0} |
| aves_silvestres_cercanas | Presencia de aves silvestres cerca del plantel. | texto | aves migratorias en el humedal a 2 km |
| historial_brotes_zona | Brotes anteriores en la zona. | texto | detecciones de H5N1 en aves silvestres de la costa en temporadas anteriores |
| movimientos_animales | Ingresos, salidas o contactos de animales. | texto | ingreso de pollitas de recría hace 2 semanas |
| indicadores_productivos | Cambios en la producción. | texto | caída brusca de la postura |

## Reglas de normalización

- **Números**: `n_animales` es entero; los porcentajes y el clima son decimales, sin el signo `%` ni la unidad (ej. `12.0`, no `"12%"`).
- **Fechas**: formato AAAA-MM-DD (ej. `2026-05-01`).
- **Listas**: `signos_clinicos` es una lista de textos; `resultados_laboratorio` es una lista de diccionarios.
- **Valores vacíos**: si no hay información, el campo se omite (Caso usa `total=False`). En los JSON de escenario, el campo queda en `null`.

## Escenarios de Investigación (corpus/escenarios/IAAP-XX.json)

Cada escenario es un JSON que `python -m vet_agent.datos.cargar_escenarios <excel>` genera a partir del Excel de Investigación:

| campo | qué significa | tipo | ejemplo |
|---|---|---|---|
| id_escenario | Identificador del escenario. | texto | IAAP-12 |
| titulo | Descripción corta del escenario. | texto | Muertes en aves de traspatio junto al lago Budi… |
| version | Versión del escenario. | texto | v1 |
| autor | Quién lo escribió en Investigación. | texto | Alexander Riveros |
| tipo | `"simulado"` (inventado por Investigación) o `"real"` (caso ocurrido). | texto | simulado |
| parte_a_entrada | Datos del caso, con los mismos campos de la clase Caso. | diccionario | {"especie": "aves", "region": "La Araucanía", …} |
| parte_b_referencia | Respuesta esperada, para evaluar al sistema: `nivel_riesgo`, `factores_riesgo`, `hipotesis_alternativas`, `informacion_faltante`, `medidas_inmediatas`, `protocolos_aplicables`, `documentos_esperados` y `acciones_que_no_debe_recomendar` (todos listas de textos, menos `nivel_riesgo`). | diccionario | {"nivel_riesgo": "alto", "documentos_esperados": ["C34", "E6"], …} |

**El sistema solo recibe la parte A.** La parte B es la respuesta de referencia: sirve para evaluar el informe y los agentes nunca deben verla.

Los casos reales (IAAP-13 a IAAP-15) pueden venir sin parte B, porque no siempre hay una respuesta de referencia.
