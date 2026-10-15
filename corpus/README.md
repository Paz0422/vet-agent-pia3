# Corpus eje D (Diagnóstico)

Fuente: Drive de Investigación, `EXCEL CON FUENTES`, hoja DOCUMENTOS, filas D1 a D36.
Se excluyen D2 y D26 porque son duplicados de B11/E9 y B4.

- `D/`: un PDF por documento, nombrado con su ID (`D8.pdf`).
- `metadatos.csv`: una fila por cada PDF de `D/`, con las columnas de la Tarea 1.

**Estado:** 26 de 34 documentos (consultados el 14/10/2026).

## Todo el corpus está en Markdown (desde el 15/10/2026)

Ya no hay pipeline `.txt` (se retiraron `extraer.py`, `indexar.py`, `corpus/txt/` y la colección `diagnostico`). Los documentos viven en `corpus/md/<colección>/<id>.md` y se indexan en `climatico_md`, `diagnostico_md` y `bioseguridad_md`:

1. `python -m vet_agent.rag.prefiltro`: decide qué fuentes del Excel se bajan y por qué ruta.
2. `python -m vet_agent.rag.descargar_md`: baja desde Europe PMC, PDF o web.
3. `python -m vet_agent.rag.pdf_local`: pasa a Markdown los PDF de `D/` (aplica `arreglos.json`).
4. `python -m vet_agent.rag.limpiar_texto`, luego `validar` e `indexar_md`.

Las notas de abajo sobre la "Tarea 2" siguen valiendo: esos arreglos se aplican ahora desde `pdf_local.py`.

## Columna `enfermedad`

El proyecto es solo de influenza aviar, pero el corpus incluye documentos de cerdos. Esta columna permite filtrarlos en el buscador:

| Valor | Significado | Documentos |
|-------|-------------|------------|
| `aviar` | Trata de influenza aviar o de aves. | 9 |
| `porcino` | Trata de PRRS o de otras enfermedades de cerdos. | 9 |
| `general` | Aplica a cualquier enfermedad: laboratorio, muestras, validación de pruebas, PCR en general. | 8 |

- D11 (kits aprobados por el SAG) es `general`: lista kits de muchas especies, incluida una sección de influenza aviar.
- D25 es `porcino` aunque trata de influenza A, porque es influenza en cerdos.

## Versiones en inglés (D6, D7, D8, D10, D16, D19)

Los capítulos del Manual Terrestre de la OMSA están en su versión en inglés:

1. Son los PDF a los que apuntan los enlaces del Excel (D7, D8, D16 y D19 enlazan a `/eng/`).
2. El modelo de embeddings (`paraphrase-multilingual-MiniLM-L12-v2`) es multilingüe, así que una pregunta en español igual recupera texto en inglés.

Ojo con D7: según las observaciones del Excel, Investigación tenía un PDF en español. Acá se usó la edición en inglés de 2023.

## Notas por documento

| ID | Nota |
|----|------|
| D3 | El Excel enlaza solo a la portada del SAG. Se usó el enlace directo de la fila F1, que es el mismo documento. |
| D5 | Versión en español (WHO/WHE/CPI/2019.20), copia publicada en saludcapital.gov.co. Enlace oficial: `apps.who.int/iris/bitstream/handle/10665/327978/WHO-WHE-CPI-2019.20-spa.pdf`. No vigente (edición 2019–2020). |
| D9 | Descargado de un mirror de IDEXX (`idexx-prod-base.django-aws.idexx.com`), mismo número de documento 06-0013212-05. |
| D10, D13, D15, D20, D24 | Ver "Para las próximas tareas". |
| D32 | Cada página trae el sello "Downloaded from academic.oup.com … by guest", que la Tarea 2 debe limpiar. |

## Para las próximas tareas

### D10: página web de la OMSA guardada como PDF (Tarea 2)

Es la edición 2026 del capítulo 1.1.2, la que pide el Excel. El PDF en inglés de `/tahm/` es de 2018, por eso se guardó la página web. Tiene todo el capítulo, pero viene con basura de la página:

- **Títulos repetidos 4 veces seguidas:** cada línea de título sale 4 veces una tras otra (`INTRODUCTORY CHAPTERS`, `COLLECTION, SUBMISSION AND STORAGE OF DIAGNOSTIC SPECIMENS`, `1.    General considerations`, `2.   Blood`…). Se arregla borrando las líneas idénticas consecutivas. Los números de sección traen espacios no separables (`\xa0`).
- **Inicio:** el texto de introducción de "Codes and Manuals" (`Our Terrestrial and Aquatic Animal Health Codes provide…`, `To consult previous versions, visit the following links:`), también repetido 4 veces.
- **Final:** después de `Most recent updates adopted in 2026.` viene el banner de cookies (`We value your privacy`, `We use cookies…`, `Customize`, `Reject All`, `Accept All`). Se puede cortar todo lo que viene después de esa frase.
- **Pie de página en medio de las referencias:** `Follow us`, `Terms and Conditions`, `Privacy Policy`, `Copyright ©WOAH2026`.

### D13, D15, D20: páginas de producto de IDEXX guardadas como PDF (Tarea 2)

Al guardar la página como PDF, las ligaduras "fi" y "fl" quedaron como otro carácter, y cada PDF usa uno distinto. **No hay que reemplazar el carácter en todo el texto**, porque rompería las palabras normales. Hay que reemplazar solo estas palabras:

| PDF | Cambio | Palabras afectadas |
|-----|--------|--------------------|
| D13 | `<` → `fi` | `per<les`, `signi<cado`, `Simpli<cado`, `certi<cados`, `de<ciente`, `de<nitivos`, `e<caz`, `identi<cación` |
| D15 | `E` → `fi`, `V` → `fl` | `proEles`, `proEts`, `signiEcance`, `SimpliEed`, `deEnitive`, `identiEcation`, `CertiEcates`, `Eles`, `inVuenza`, `Vuid` |
| D20 | `O` → `fl` | `inOuenza`, `InOuenza` |

Además:

- Los tres traen el menú del sitio: `Home > Livestock > …`, `Support Investors Contact Subscribe Careers Suppliers Search Sign in`, `Box sizes and labels will vary.`, `* Not all products are available and/or registered in every country.`
- **D15 parte con un banner de otro producto.** El primer párrafo (`The IDEXX PRRS X3 Ab Test ELISA can be used to develop herd pro…` hasta `…export the data to Microsoft Excel Eles.`, tal cual sale en el texto) habla del PRRS X3 (D13), no del Oral Fluids. Si no se borra, el buscador puede devolver datos del X3 citados como D15.

### D24: revista corregida (Tareas 4 y 5, y el informe)

- El Excel lo registra en Canadian Journal of Veterinary Research (ScienceDirect), pero el PDF es de **Canadian Journal of Microbiology** 72: 1–13 (2026), doi 10.1139/cjm-2025-0195. En `metadatos.csv` ya está corregido, así que las citas del agente salen bien. Falta avisar a Investigación para que corrija el Excel.
- El PDF original traía en el margen de cada página `Downloaded from cdnsciencepub.com by <IP> on <fecha>`, con la IP de quien lo descargó. Ese sello se borró del archivo. Si alguien vuelve a descargar D24, debe borrarlo de nuevo antes de subirlo al repo.

## Pendientes (no disponibles)

| ID | Motivo |
|----|--------|
| D1 | Sin enlace; fue entregado como Anexo 4 por Investigación. |
| D4 | Sin enlace específico (solo la portada de porkcolombia.co). |
| D12 | El enlace a S3 de IDEXX responde "acceso denegado". |
| D28, D29, D30 | Sin enlace específico (solo la portada de idexx.com). |
| D31 | Oxford Academic pide permisos para descargar el PDF. |
| D36 | No se pudo descargar desde MDPI. |
