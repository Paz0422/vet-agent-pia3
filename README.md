# vet-agent-pia3

## Qué es

Sistema multiagente para la vigilancia de influenza aviar H5N1. Cuatro agentes (climático, diagnóstico, bioseguridad y moderador) analizan un caso, buscan evidencia en un corpus de documentos (RAG) y redactan un informe de apoyo a la decisión. Ningún informe se entrega sin la **revisión humana obligatoria**: una persona debe aprobarlo (o devolverlo con comentarios al moderador).

## Cómo instalar

Todos los comandos se corren desde la carpeta principal del repo.

1. **Python 3.10 o superior** (los comandos de este README se probaron con Python 3.13).
2. Crea y activa un entorno virtual:
   - macOS / Linux: `python3 -m venv .venv` y luego `source .venv/bin/activate`
   - Windows (PowerShell): `python -m venv .venv` y luego `.venv\Scripts\Activate.ps1`
3. Instala las dependencias (tarda varios minutos: incluye `torch` y `sentence-transformers`):
   ```
   pip install -r requirements.txt
   ```
4. Copia el archivo de configuración:
   - macOS / Linux: `cp .env.ejemplo .env`
   - Windows: `copy .env.ejemplo .env`
5. Carga el modelo en **LM Studio**:
   - Modelo: `qwen/qwen3-4b-2507` (Qwen3-4B Q4_K_M, GGUF).
   - Al cargarlo, sube **Context Length** a `16384` y deja **Max Concurrent Predictions** en `1`. Con menos contexto el moderador falla con `exceed_context_size_error`.
   - Enciende el servidor (pestaña *Developer* → *Start Server*, o `lms server start`). Debe quedar en `http://localhost:1234/v1`, que es el `LLM_URL` del `.env`.
6. **Sin LM Studio** (solo para probar): en el `.env` cambia `USAR_SIMULACION=false` por `USAR_SIMULACION=true`. Los agentes responden con textos de ejemplo.

### Documentos para la búsqueda (RAG)

La base de búsqueda `chroma_db/` **no está en git**: cada persona la arma en su computador. Sin ella el sistema corre igual, pero cada agente avisa `sin contexto RAG` y el informe sale sin fuentes citadas. Para armarla (necesita internet y tarda; el detalle está en [corpus/README.md](corpus/README.md)):

```
python -m vet_agent.rag.prefiltro
python -m vet_agent.rag.descargar_md
python -m vet_agent.rag.limpiar_texto
python -m vet_agent.rag.validar
python -m vet_agent.rag.indexar_md
```

Los 18 documentos del eje D que vienen de PDF ya están en `corpus/md/diagnostico/`. La primera vez que se busca, se descarga el modelo de embeddings `paraphrase-multilingual-MiniLM-L12-v2`.

## Cómo usarlo

**Demo web (Streamlit)** — tablero de vigilancia: se carga el caso, se ve a los agentes trabajar en vivo y un especialista firma el informe:

```
streamlit run app_demo.py
```

Se abre en <http://localhost:8501>. Con `USAR_SIMULACION=true` en el `.env` no usa el LLM (la búsqueda de evidencia sí es real); con `false` usa el modelo de LM Studio y el análisis puede tardar varios minutos.

Qué hay en la pantalla:

- **Panel izquierdo:** elegir un escenario de `corpus/escenarios/`, traer el clima de Open-Meteo y editar el caso por secciones. El botón **Analizar** lanza el grafo.
- **Diagrama del grafo:** los nodos Climático → Diagnóstico → Bioseguridad → Moderador → Revisión humana se iluminan mientras trabajan (en espera / pensando / listo, con su tiempo). También se marcan la ronda de debate del moderador, el rechazo con comentario y el agente que falló.
- **Pestaña Informe:** medidor del nivel de riesgo, resumen, tarjetas One Health (animal, humana, ambiental), mapa de la comuna (solo si hay internet), recomendaciones por prioridad y las citas como **notas al pie** que se abren y muestran el fragmento y la fuente. Una cita que no está en la evidencia recuperada queda marcada como posible invención del modelo.
- **Pestaña Evidencia:** los fragmentos que recuperó cada agente, agrupados por documento, y la salida JSON de cada uno.
- **Pestaña Comparar B1·B2·B3:** lee los `comparacion*.csv` de `resultados/` (formato viejo y nuevo de `evaluar.py`) y compara nivel de riesgo, tiempo, fuentes citadas, riesgos y recomendaciones.
- **Pestaña JSON:** el borrador del informe tal como lo entregó el moderador.
- **Barra de firma (fija abajo):** comentario + **Aprobar** / **Rechazar** (rechazar exige comentario). Después de aprobar, ahí aparece **Descargar informe (Markdown)**.

Si el LLM falla, la demo explica el error y ofrece **Reintentar** desde el paso que falló. Mientras corre, todos los botones quedan desactivados. El tema (modo claro, acento verde azulado) está en `.streamlit/config.toml`; el HTML/CSS de la pantalla, en `vet_agent/vista_demo.py`.

**Terminal** — corre el caso de ejemplo `CASO_H5N1` y pregunta por terminal si apruebas el informe (`s` o `n`):

```
python -m vet_agent.main
```

**API (FastAPI)**:

```
uvicorn vet_agent.api:app --reload
```

| Ruta | Qué hace |
|---|---|
| `GET /escenarios` | Lista los escenarios de `corpus/escenarios` (por ejemplo `IAAP-00`, `IAAP-12`). |
| `GET /escenarios/{id}` | Devuelve solo la Parte A (la entrada) de ese escenario. |
| `POST /analizar` | Recibe un caso en JSON y devuelve `informe` y `fuentes`. Aprueba la revisión humana automáticamente. |

Documentación interactiva en <http://localhost:8000/docs>.

## Pruebas y evaluación

**Pruebas automáticas** (no necesitan LM Studio ni internet):

```
python -m vet_agent.pruebas
```

Deben pasar todas. La prueba de integración con Chroma real se omite (y el programa lo avisa) si no están indexadas las tres colecciones.

**Evaluación** — compara tres configuraciones: B1 (LLM sin RAG), B2 (un solo agente con RAG) y B3 (multiagente):

```
python -m vet_agent.evaluar
python -m vet_agent.evaluar --escenarios corpus/escenarios --repeticiones 3
```

Necesita `chroma_db` armada y LM Studio encendido (o `USAR_SIMULACION=true`). **Una corrida completa con el modelo real puede tomar muchas horas.** Cada intento queda como una fila en `resultados/registro_corridas.csv`, y el resumen de la última ejecución en `resultados/comparacion.csv`.

## Estructura del repositorio

```
vet-agent-pia3/
├── app_demo.py            demo web (Streamlit): tablero del caso, agentes en vivo y revisión humana
├── vet_agent/             el sistema
│   ├── grafo.py           flujo entre agentes (LangGraph) y pausa para la revisión humana
│   ├── prompts.py         prompts de los agentes (versión v1, congelada)
│   ├── main.py · api.py   uso por terminal y por API (FastAPI)
│   ├── vista_demo.py      HTML/CSS de la demo: diagrama del grafo, medidor, tarjetas, comparación
│   ├── evaluar.py         compara B1 (LLM sin RAG), B2 (un agente con RAG) y B3 (multiagente)
│   ├── pruebas.py         pruebas automáticas
│   ├── agentes/           climático, diagnóstico, bioseguridad, moderador y su base común
│   ├── rag/               arma el corpus (descargar → limpiar → validar → indexar) y busca en Chroma
│   └── datos/             clima (Open-Meteo), planteles simulados y escenarios
├── corpus/                fuentes, metadatos, escenarios IAAP y preguntas de prueba
├── resultados/            salidas de evaluar.py (comparaciones y registro de corridas)
└── docs/                  diccionario de datos e imagen del grafo
```

| Carpeta | Para la presentación |
|---|---|
| `vet_agent/agentes/` | Los cuatro especialistas: cada uno busca evidencia en su colección y responde en JSON; el moderador integra y puede pedir aclaraciones. |
| `vet_agent/rag/` | El pipeline del corpus: `prefiltro` → `descargar_md` → `pdf_local` → `limpiar_texto` → `validar` → `indexar_md`, y la búsqueda (`buscar`). Utilidades: `probar_rag` (Recall@5), `para_investigacion` (reporte para Investigación) y `borrar_sobrantes` (borra archivos que ya no se usan). |
| `vet_agent/datos/` | Datos de entrada del caso: clima real, planteles simulados, carga de escenarios desde Excel (`cargar_escenarios`) y su revisión (`revisar_escenarios`). |
| `corpus/` | Lo que entrega Investigación (`fuentes/`, `escenarios/`) y lo que genera el pipeline (`md/`, que no está en git salvo los 18 documentos del eje D y los Excel de reporte). |
| `resultados/` | Comparación de configuraciones (`gguf/`, `mlx/`, `prueba_seco/`); ver [resultados/README.md](resultados/README.md). |
| `docs/` | [Diccionario de datos](docs/diccionario_datos.md) y [grafo.png](docs/grafo.png) (se regenera con `python -m vet_agent.ver_grafo`). |

## Más información

- [corpus/README.md](corpus/README.md): cómo se arma el corpus y estado de los documentos.
- [corpus/datos/README.md](corpus/datos/README.md): aviso sobre los planteles simulados.
- [resultados/README.md](resultados/README.md): resultados de la comparación de configuraciones y problemas de memoria con MLX.
- [docs/diccionario_datos.md](docs/diccionario_datos.md): campos del caso, reglas de normalización y formato de los escenarios.
