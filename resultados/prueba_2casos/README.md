# Prueba con 2 casos y tiempo de cada caso

Corrida del 07/11/2026 para revisar que el código de las 3 configuraciones corre bien antes de las corridas finales.

- **Escenarios:** IAAP-12, la versión final del repo, e IAAP-01, del borrador de Investigación (`E2_escenarios-cargables.xlsx`, `borrador_1`).
- **Equipo y modelo:** un Mac M4 con Qwen3-4B Q4_K_M (GGUF) en LM Studio, contexto 16.384.
- **Configuración:** `.env` igual a `.env.ejemplo` (`LLM_MAX_TOKENS=3000`).
- **Commit:** `a257f71`.

Los tiempos de este equipo **no son los oficiales**: las corridas finales se miden en otro PC.

## `tiempos_por_caso.csv` (nuevo)

`evaluar.py` agrega una fila apenas terminan las 3 versiones de un caso (escenario × repetición). El archivo queda junto a `registro_corridas.csv` y, como el registro, agrega filas sin borrar las anteriores.

| Columna | Qué guarda |
|---|---|
| `fecha` | Cuándo empezó el caso |
| `escenario`, `repeticion` | Qué caso es |
| `B1_s`, `B2_s`, `B3_s` | Segundos de cada versión, **sumando todos sus intentos** |
| `intentos_B1`, `intentos_B2`, `intentos_B3` | Cuántos intentos necesitó cada versión |
| `total_s` | `B1_s + B2_s + B3_s` |
| `fallidas` | Versiones cuyo último intento terminó en error (vacío si todas terminaron `ok`) |
| `modelo_y_version`, `version_sistema` | Modelo cargado y commit |

Las columnas de `registro_corridas.csv` no cambian: el formato está acordado con Investigación.

## Resultado

Las 3 configuraciones terminaron en `ok` en los 2 casos. Hubo 2 reintentos, por los mismos errores de la prueba en seco:

| Caso | B1 | B2 | B3 | Total |
|---|---|---|---|---|
| IAAP-01 | 95,0 s | 224,2 s | **1.797,0 s** (2 intentos) | 2.116,2 s |
| IAAP-12 | **185,8 s** (2 intentos) | 171,1 s | 801,2 s | 1.158,1 s |

Los errores de los intentos fallidos:

- **IAAP-01, B3, intento 1:** la respuesta del moderador se cortó en `max_tokens` (3000).
- **IAAP-12, B1, intento 1:** el modelo respondió algo que no es JSON válido.

Las dos causas son las que ataca la Tarea 1 de Tareas IV (`LLM_MAX_TOKENS` a 4096 y forzar respuestas en JSON).

Los tiempos de `tiempos_por_caso.csv` coinciden con la suma de los intentos de `registro_corridas.csv`.

## Ojo al cargar el borrador de escenarios

No cargues el borrador con el comando normal del cargador:

```
python -m vet_agent.datos.cargar_escenarios <ruta_del_excel>
```

Ese comando escribe en `corpus/escenarios/` y **sobrescribe los JSON que ya están ahí**. Con el borrador, el `IAAP-12.json` del repo (versión `v1`) se reemplaza por el `IAAP-12` del borrador (`borrador_3`), que tiene campos distintos. Por ejemplo:

- `especie` dice "Gallinas y patos domésticos" en vez de "aves".
- `indicadores_productivos` trae todo el relato del caso.

Para usar el borrador sin tocar el repo, guárdalo en otra carpeta llamando a la función directamente. Así se hizo para esta prueba:

```
python -c "from vet_agent.datos.cargar_escenarios import guardar_escenarios; guardar_escenarios('<ruta_del_excel>', '<otra_carpeta>')"
python -m vet_agent.evaluar --escenarios <otra_carpeta>
```

Si se sobrescribió por error, `git checkout -- corpus/escenarios/` recupera los archivos del repo.

## Archivos

- `registro_corridas.csv`: los 8 intentos (6 corridas).
- `tiempos_por_caso.csv`: una fila por caso.
- `comparacion.csv`: el último intento de cada corrida.
