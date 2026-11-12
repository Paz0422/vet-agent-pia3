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

**Demo web (Streamlit)** — apoyo a la decisión para un médico veterinario epidemiólogo: ingresa su caso, mira a los agentes trabajar y firma el informe:

```
streamlit run app_demo.py
```

Se abre en <http://localhost:8501>. Con `USAR_SIMULACION=true` en el `.env` no usa el LLM: la redacción es un informe de ejemplo completo y la búsqueda de evidencia sí es real. Con `false` usa el modelo de LM Studio y el análisis puede tardar varios minutos.

**Entrada: el caso.** Un formulario en 4 secciones: plantel y ubicación, cuadro clínico, laboratorio y clima, y entorno. Cada campo tiene su unidad y un ejemplo de guía, y los obligatorios van marcados con `*`: especie, región, comuna, fecha, n.º de animales y signos clínicos. Si falta un obligatorio o un número viene mal escrito, la demo lo avisa y no analiza. Un dato que no se conoce se deja vacío y el informe lo marca como información faltante. El laboratorio se escribe uno por línea (`RT-PCR: positivo · 2026-05-03`). **Traer clima** completa temperatura, humedad y precipitación desde Open-Meteo (por defecto, con la comuna y la fecha del caso). Si hay internet, junto a los datos del plantel aparece un mapa chico de la comuna. **Cargar caso de ejemplo** trae un escenario de `corpus/escenarios/`. Al terminar el análisis, el formulario se pliega arriba del informe y se puede abrir para editar y volver a analizar.

**Proceso.** Mientras se analiza se ve el diagrama del grafo: Climático → Diagnóstico → Bioseguridad → Moderador → Revisión humana se iluminan mientras trabajan, con la ronda de debate, el rechazo con comentario y el agente que falló. Al terminar queda una sola línea (por ejemplo, "4 agentes · 2 min 31 s · 1 ronda de debate") que se puede desplegar.

**Salida: apoyo a la decisión**, en el orden en que hay que decidir:

1. **Nivel de riesgo**, grande y en su color (verde, ámbar o rojo, que se usan solo para esto), con una frase de por qué y la confianza. Debajo va siempre el aviso: es apoyo a la decisión y requiere validación de un médico veterinario y de la autoridad competente.
2. **Qué hacer ahora:** si el caso es de notificación obligatoria y a quién (SAG, SEREMI de Salud), y las acciones de prioridad inmediata destacadas. Después, las de corto plazo, las preventivas y los protocolos de bioseguridad.
3. **Riesgos por dimensión One Health** (animal, humana, ambiental), compactos.
4. **Para decidir mejor:** la información que falta, los diagnósticos a descartar y las incertidumbres.
5. **Evidencia y citas** (plegable): las citas como notas al pie que muestran el fragmento y la fuente (una cita que no está en la evidencia recuperada se marca como posible invención del modelo), y los documentos que recuperó cada agente.

Abajo, fija, la **barra de firma**: comentario + **Aprobar** / **Rechazar** (rechazar exige comentario). Después de aprobar aparece **Descargar informe (Markdown)**. Si el LLM falla, la demo explica el error y ofrece **Reintentar** desde el paso que falló. Mientras corre, el formulario y los botones quedan desactivados.

**Modo equipo.** La comparación B1·B2·B3 (lee los `comparacion*.csv` de `resultados/`), el JSON del informe y las salidas de cada agente son para el equipo, no para el veterinario. Se activan con el interruptor **Modo equipo** de la barra lateral o con `DEMO_MODO_EQUIPO=true` en el `.env` (apagado por defecto).

El tema (modo claro de alto contraste, acento verde azulado) está en `.streamlit/config.toml`. Los campos del formulario y el HTML/CSS de la pantalla están en `vet_agent/vista_demo.py`.

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
├── app_demo.py            demo web (Streamlit): caso del veterinario, agentes en vivo, informe y firma
├── vet_agent/             el sistema
│   ├── grafo.py           flujo entre agentes (LangGraph) y pausa para la revisión humana
│   ├── prompts.py         prompts de los agentes (versión v2, con límites de largo; VERSION_PROMPTS)
│   ├── main.py · api.py   uso por terminal y por API (FastAPI)
│   ├── vista_demo.py      campos del formulario y HTML/CSS de la demo (riesgo, acciones, diagrama, comparación)
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
