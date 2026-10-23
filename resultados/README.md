# Resultados de la Tarea 2: comparación de las 3 configuraciones

Esta carpeta guarda las corridas de `python -m vet_agent.evaluar` con el LLM real (LM Studio). Sirven para dos cosas:

1. Comparar el sistema multiagente con dos configuraciones más simples.
2. Registrar un problema de memoria que aparece con MLX en un Mac de 16 GB, y cómo se resolvió con GGUF.

Todas las corridas se hicieron el 23/10/2026.

## Qué compara `evaluar.py`

Se corre el mismo caso (`CASO_H5N1` de `vet_agent/main.py`) de 3 formas:

| Configuración | Qué hace |
|---|---|
| `llm_sin_rag` | Un solo prompt al LLM, sin buscar documentos. |
| `monoagente_rag` | Un solo agente que busca en las 3 colecciones y redacta el informe solo. Usa las mismas consultas que los agentes (`contexto_rag()`), así recibe los mismos documentos que el sistema multiagente. |
| `multiagente` | El grafo actual (climático → diagnóstico → bioseguridad → moderador), aprobando la revisión humana automáticamente. |

Las dos configuraciones simples piden el informe con el mismo formato JSON que el moderador (`informe_final` de `PROMPT_MODERADOR`), para que los tres informes se puedan comparar campo por campo.

Cada corrida escribe en `comparacion.csv` una fila por configuración, con estas columnas:

- `configuracion`
- `informe`: el JSON completo del informe.
- `fuentes_citadas`: los `doc_id` distintos que aparecen en `fuentes_utilizadas`.
- `segundos`: lo que tardó.

Si una configuración falla, su fila queda con `informe = "ERROR: ..."` y 0 fuentes, y el script sigue con las demás.

**Cómo se cuentan las fuentes:** por ahora se cuentan **todas** las fuentes citadas, aunque el modelo las haya inventado. Ver "Fuentes citadas" más abajo.

## Entorno de las corridas

| | |
|---|---|
| Equipo | MacBook con Apple M4 y 16 GB de memoria unificada |
| LM Studio | Modelo `qwen/qwen3-4b-2507` (Instruct). Context Length 16384, Max Concurrent Predictions 1 |
| Formatos probados | **MLX 4-bit** y **GGUF Q4_K_M** (Flash Attention activado, GPU Offload al máximo) |
| `.env` | Valores de `.env.ejemplo`: `LLM_MAX_TOKENS=3000`, `K_FRAGMENTOS=3`, `MAX_CARACTERES_FRAGMENTO=1200`, temperatura 0.2 |
| Corpus | 239 documentos bajados y 18 rechazados por las editoriales (`corpus/md/errores.csv`). Indexados: 243 documentos en 13.468 pedazos (climático 76, diagnóstico 97, bioseguridad 70) |

## Resultados

### GGUF Q4_K_M (`gguf/comparacion_corrida1.csv`, igual a `comparacion.csv`)

| Configuración | Segundos | Fuentes citadas | doc_id citados |
|---|---|---|---|
| `llm_sin_rag` | 170,6 | 3 | `doc_id_01`, `doc_id_02`, `doc_id_03` (inventados) |
| `monoagente_rag` | 122,3 | 4 | D3, D24, BIO-063, D8 |
| `multiagente` | 553,1 | 5 | CLI-017, D24, D8, `SAG-P-PP-VE-006`\*, BIO-063 |

### MLX 4-bit, corrida 3 (`mlx/comparacion_corrida3.csv`)

| Configuración | Segundos | Fuentes citadas | doc_id citados |
|---|---|---|---|
| `llm_sin_rag` | 63,8 | 6 | `doc_id_01` … `doc_id_06` (inventados) |
| `monoagente_rag` | 95,7 | 5 | CLI-017, D24, D8, `SAG 2007`†, `SAG 2023`† |
| `multiagente` | 459,1 | 5 | CLI-017, D8, D3, BIO-063, `SAG-P-PP-VE-006`\* |

### MLX 4-bit, corridas 1 y 2: el multiagente falla

- **Corrida 1** (`mlx/comparacion_corrida1_fallo_multiagente.csv`):
  - `llm_sin_rag`: 58,8 s, 4 fuentes inventadas.
  - `monoagente_rag`: 87,8 s, 4 fuentes (D3, D8, CLI-017, `SAG 2007`†).
  - `multiagente`: **falló** a los 341,3 s.
- **Corrida 2:**
  - `llm_sin_rag`: 92,9 s.
  - `monoagente_rag`: 115,2 s.
  - `multiagente`: **falló** a los 438,1 s.
  - Su CSV se sobrescribió con la corrida 3, así que no quedó guardado.

En ambas corridas falló la llamada del moderador, con el mismo error de LM Studio:

```
RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory
(00000008:kIOGPUCommandBufferCallbackErrorOutOfMemory)
```

### En las tres corridas completas

- Las tres configuraciones dieron nivel de riesgo global **alto**.
- `llm_sin_rag` siempre **inventa** sus fuentes (`doc_id_01`, `doc_id_001`…), porque no recibe documentos.
- Las dos configuraciones con RAG citan mayormente documentos reales del corpus.

## Fuentes citadas

`fuentes_citadas` cuenta todo lo que el modelo puso en `fuentes_utilizadas`. Eso mezcla tres casos:

- **doc_id reales del corpus:** D3, D8, D24, CLI-017 y BIO-063 existen en `corpus/md/`.
- **Inventados:** todos los de `llm_sin_rag` (`doc_id_01`…) y los marcados con †. `SAG 2007` y `SAG 2023` no son doc_id del corpus.
- **Mal citados (\*):** `SAG-P-PP-VE-006` no es un doc_id, pero corresponde a un documento real: **E7** ("Procedimiento P-PP-VE-006 – Contingencia de Influenza Aviar"). El modelo citó el código del procedimiento en vez de su ID.

Por eso, con el conteo actual, `llm_sin_rag` aparece con tantas o más fuentes que las configuraciones con RAG, aunque todas sean inventadas. Para comparar fundamentación, conviene contar solo los doc_id que existen en el corpus. Se dejó así a propósito por ahora.

## El problema de memoria con MLX

Durante la corrida 3 de MLX y la corrida de GGUF se midió la memoria cada 5 segundos (`*/memoria_corrida*.csv`). Los CSV tienen estas columnas:

- `hora`
- `libre_pct`: memoria libre del sistema, según `memory_pressure`.
- `gpu_en_uso_gb`: memoria que usa la GPU, según `ioreg` (`AGXAccelerator`, "In use system memory").
- `swap_usado_mb`
- `lmstudio_motor_gb`: memoria del proceso de LM Studio. Solo tiene valores útiles con MLX; con GGUF el motor corre en otro proceso.
- `python_gb`: memoria del proceso de `evaluar`.

### Comparación de la memoria

| | MLX 4-bit (corrida 3) | GGUF Q4_K_M |
|---|---|---|
| Máximo de memoria GPU que permite macOS en este Mac | 11,84 GB | 11,84 GB |
| **Pico de memoria GPU** | **10,33 GB** | **5,52 GB** |
| Comportamiento | Sube con el largo de cada prompt | Constante (~5,3 GB) toda la corrida |
| Memoria libre mínima del sistema | 10 % | 19 % |
| Swap durante la corrida | 2,6 → 5,2 GB | 3,3 → 5,4 GB |
| Tiempo total de las 3 configuraciones | 623 s | 850 s (~37 % más lento) |

El máximo de 11,84 GB es el `recommendedMaxWorkingSetSize` de Metal, leído con `torch.mps.recommended_max_memory()`.

### Qué pasa con MLX

MLX pide memoria en cada llamada según el largo del prompt. En la corrida 3 la memoria GPU fue aproximadamente:

| Prompt | Memoria GPU |
|---|---|
| ~1.000 tokens | ~3,3 GB |
| 2.000 a 4.000 tokens | ~4,4 a 5,3 GB |
| ~5.900 tokens | ~6,9 GB |
| ~9.900 tokens (moderador) | 9 a 10,3 GB |

Entre una llamada y otra la memoria vuelve a bajar.

El prompt del moderador es el más largo, porque lleva las salidas de los 3 agentes más los fragmentos recuperados: entre 9.500 y 9.900 tokens según la corrida. Cabe en los 16.384 tokens de contexto, así que no es un problema de contexto. El problema es que necesita ~10,3 de los 11,84 GB que macOS deja usar a la GPU. Con un poco más de memoria ocupada por otras cosas no alcanza, y la llamada falla. Por eso la corrida 3 pasó y las corridas 1 y 2 no.

### Qué pasa con GGUF

GGUF (el motor llama.cpp de LM Studio) reserva la memoria del contexto completo (16.384 tokens) al cargar el modelo y no pide más durante la corrida. Si el modelo carga, no se queda sin memoria a mitad de camino. A cambio, en este Mac es más lento que MLX.

## Conclusión y recomendación

- El fallo **no es un error del código**. Es un límite de MLX en un Mac de 16 GB con prompts largos.
- **En Mac:** usar la versión **GGUF Q4_K_M** de `qwen/qwen3-4b-2507`, con Flash Attention y contexto 16384. El nombre del modelo en LM Studio es el mismo, así que `.env` no cambia.
- **En Windows**, que es lo que usa el resto del equipo, LM Studio corre GGUF, que reserva la memoria al cargar. No debería aparecer este problema. No se probó en Windows; la velocidad dependerá de la tarjeta gráfica de cada PC.
- **Ojo al recargar el modelo en LM Studio:** el Context Length puede volver a 8192. Hay que revisar que quede en 16384; si no, el moderador no cabe.

## Archivos

```
resultados/
├── README.md
├── comparacion.csv                                 ← última corrida (GGUF); es lo que escribe evaluar.py
├── gguf/
│   ├── comparacion_corrida1.csv                    ← corrida completa con GGUF Q4_K_M
│   └── memoria_corrida1.csv                        ← memoria cada 5 s durante esa corrida
└── mlx/
    ├── comparacion_corrida1_fallo_multiagente.csv  ← multiagente con error de memoria
    ├── comparacion_corrida3.csv                    ← corrida completa con MLX 4-bit
    └── memoria_corrida3.csv                        ← memoria cada 5 s durante la corrida 3
```

## Limitaciones

- Es **un solo caso** (`CASO_H5N1`) y una corrida completa por formato. Los tiempos varían entre corridas: por ejemplo, `llm_sin_rag` tardó entre 59 y 93 s con MLX.
- El LLM no es determinista (temperatura 0.2): el contenido de los informes y las fuentes citadas cambian de una corrida a otra.
- `probar_rag` dio en este equipo diagnóstico 58 %, climático 100 % y bioseguridad 50 %, distinto a lo que esperaba la guía (100 %, 58 % y 50 %). Los documentos de las preguntas que fallan están indexados y bien descargados. Corresponde a la Tarea 3.
