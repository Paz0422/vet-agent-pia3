"""Demo del sistema para un medico veterinario epidemiologo: ingresa su caso y recibe un
informe de apoyo a la decision, que revisa y firma. La busqueda de evidencia (RAG) es real;
la redaccion es simulada o la hace el modelo de LM Studio, segun USAR_SIMULACION en el .env.

Uso (raiz del repo):
    streamlit run app_demo.py

Pantalla:
- arriba, el formulario del caso (se pliega cuando hay un analisis);
- mientras se analiza, el diagrama del grafo con los agentes trabajando; al terminar queda
  como una linea ("4 agentes · 31 s · sin rondas de debate") que se puede desplegar;
- el informe, ordenado por lo que hay que decidir: nivel de riesgo, que hacer ahora,
  riesgos One Health, lo que falta para decidir mejor y la evidencia;
- abajo, fija, la barra de firma (aprobar / rechazar con comentario / descargar).
El "Modo equipo" (barra lateral o DEMO_MODO_EQUIPO=true) agrega la comparacion B1·B2·B3,
el JSON y las salidas de los agentes. Lo que se dibuja (HTML/CSS) esta en vet_agent/vista_demo.py.
"""
import json
import pathlib
import time
import uuid

import openai
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent import vista_demo as vista
from vet_agent.api import listar_escenarios, obtener_escenario
from vet_agent.config import USAR_SIMULACION, LLM_MODELO, LLM_URL, LLM_TIMEOUT, DEMO_MODO_EQUIPO
from vet_agent.datos import clima
from vet_agent.grafo import construir_grafo, ESPECIALISTAS
from vet_agent.main import CASO_H5N1

st.set_page_config(page_title="VET·AGENT · Apoyo a la decisión", page_icon="🐔", layout="wide",
                   initial_sidebar_state="collapsed")

if USAR_SIMULACION:
    MODO = "Redacción simulada"
    AVISO = "Redacción simulada (USAR_SIMULACION=true): no se usa el LLM. La recuperación de evidencia es real."
else:
    MODO = f"{LLM_MODELO} · LM Studio"
    AVISO = (f"Modelo en uso: {LLM_MODELO} (LM Studio). Con el modelo real el análisis "
             "puede tardar varios minutos. La recuperación de evidencia es real.")

# se muestra siempre, aunque el modelo no lo escriba en el informe
AVISO_INFORME = ("Informe de apoyo a la decisión. Requiere validación de un médico veterinario "
                 "y de la autoridad sanitaria competente.")
PENDIENTE = "Se completa al conectar el LLM" if USAR_SIMULACION else "El modelo no completó esta sección"

RESULTADOS = pathlib.Path(__file__).resolve().parent / "resultados"
CAMPOS_CASO = list(CASO_H5N1)  # campos (y orden) del caso que reciben los agentes


@st.cache_resource
def grafo():
    # un solo grafo (y checkpointer) para toda la app; cada analisis usa su propio thread_id
    return construir_grafo(InMemorySaver())


def config():
    return {"configurable": {"thread_id": st.session_state.thread_id}}


# errores del LLM que se muestran con un mensaje claro en vez del traceback
# (ValueError = el modelo no entrego JSON valido ni con reintentos, ver llm.py)
ERRORES_LLM = (openai.APIError, ValueError)


def explicar_error(e: Exception) -> str:
    if isinstance(e, openai.APITimeoutError):
        return (f"El modelo no respondió en {LLM_TIMEOUT} s. Sube LLM_TIMEOUT en el .env "
                "o corre el análisis en un PC con GPU.")
    if isinstance(e, openai.APIConnectionError):
        return (f"No se pudo conectar con LM Studio en {LLM_URL}. Revisa que el servidor de LM Studio "
                "esté encendido (pestaña Developer → Start Server, o `lms server start`) y que el modelo esté cargado.")
    if "context" in str(e).lower():
        return ("El texto enviado no cabe en el contexto del modelo. En LM Studio, vuelve a cargar el modelo "
                "con un Context Length mayor (16384 o más) y deja Max Concurrent Predictions en 1 "
                "(con más predicciones a la vez, el contexto se puede repartir entre ellas).")
    if isinstance(e, ValueError):
        return ("El modelo no entregó un JSON válido ni con reintentos. Si la respuesta se cortó, "
                "sube LLM_MAX_TOKENS en el .env.")
    return "LM Studio devolvió un error."


def desplazar(destino_js: str):
    """Mueve la vista de la pagina (el contenedor que hace scroll en Streamlit).

    components.html corre en un iframe del mismo origen, asi que puede tocar la pagina de arriba.
    Se dibuja al final de la pagina para no correr de lugar a los demas elementos.
    """
    components.html("<script>const d = window.parent.document;"
                    "const m = d.querySelector('[data-testid=\"stMain\"]');"
                    f"let n = 0; const t = setInterval(() => {{ n++; if (m) {{ {destino_js} }} if (n > 15) clearInterval(t); }}, 200);"
                    "</script>", height=0)


def svg(destino, contenido: str):
    # st.html borra los <svg>; st.markdown con HTML los deja. El texto ya viene escapado (vista.esc)
    destino.markdown(contenido, unsafe_allow_html=True)


# ---------- diagrama del grafo en vivo ----------

def vista_grafo() -> dict:
    if "grafo_vista" not in st.session_state:
        st.session_state.grafo_vista = vista.grafo_inicial()
    return st.session_state.grafo_vista


def correr(entrada, titulo: str, lienzo) -> float | None:
    """Corre el grafo iluminando en `lienzo` el nodo que trabaja, guarda el resultado en la
    sesion y devuelve cuantos segundos tardo (None si fallo el LLM).

    El resultado queda igual que el de grafo().invoke(): el estado completo y, si el grafo
    se detuvo a esperar la revision humana, la pausa en "__interrupt__".
    Si el LLM falla, el resultado anterior no se toca y el error queda en st.session_state.error:
    el grafo queda detenido en el paso que fallo y se puede reintentar con entrada None.
    """
    v = vista_grafo()
    nodos = v["nodos"]
    inicio = ultimo = time.perf_counter()
    lineas = []

    # quien empieza: en un analisis nuevo, el climatico; al reanudar, el paso pendiente del grafo
    if isinstance(entrada, dict):
        primero = "climatico"
    else:
        pendientes = grafo().get_state(config()).next
        primero = pendientes[0] if pendientes else None
    if primero in nodos:
        nodos[primero] = {"estado": "pensando", "segundos": None}
    trabajando = primero

    def dibujar(nota: str):
        v["nota"] = nota
        svg(lienzo, vista.diagrama_grafo(v))

    dibujar(titulo)
    try:
        for paso in grafo().stream(entrada, config(), stream_mode="updates"):
            for nodo, cambios in paso.items():
                ahora = time.perf_counter()
                if nodo == "__interrupt__":
                    nodos["revision_humana"] = {"estado": "pausa", "segundos": None}
                    trabajando = None
                    continue
                # en las rondas de debate, los agentes no consultados no llaman al LLM ni devuelven cambios
                if nodo in ESPECIALISTAS and not cambios:
                    nodos[nodo] = {"estado": "omitido", "segundos": None}
                    detalle = " (no consultado en esta ronda)"
                elif nodo == "revision_humana":
                    aprobado = ((cambios or {}).get("decision_humana") or {}).get("aprobado")
                    nodos[nodo] = {"estado": "aprobado" if aprobado else "listo", "segundos": None}
                    v["rechazo"] = not aprobado
                    if not aprobado:
                        v["rechazos"] = v.get("rechazos", 0) + 1
                    detalle = "" if aprobado else " (rechazado: el moderador revisa los comentarios)"
                else:
                    nodos[nodo] = {"estado": "listo", "segundos": ahora - ultimo}
                    detalle = ""
                if nodo == "moderador" and cambios:
                    v["ronda"] = cambios.get("revisiones", v["ronda"])
                ultimo = ahora
                lineas.append(f"✅ {vista.NOMBRE_NODO.get(nodo, nodo)} terminó en {ahora - inicio:.0f} s{detalle}")

                siguiente = vista.siguiente_nodo(nodo, cambios)
                if nodo == "moderador" and siguiente == "climatico":
                    # ronda de debate: la cadena se vuelve a recorrer
                    v["debate"] = True
                    v["debates"] = v.get("debates", 0) + 1
                    for n in ESPECIALISTAS:
                        nodos[n] = {"estado": "espera", "segundos": None}
                if siguiente in nodos:
                    nodos[siguiente] = {"estado": "pensando", "segundos": None}
                trabajando = siguiente
                quien = vista.NOMBRE_NODO.get(siguiente, "")
                dibujar(f"{quien} está trabajando · {ahora - inicio:.0f} s transcurridos" if quien else titulo)
    except ERRORES_LLM as e:
        segundos = time.perf_counter() - inicio
        if trabajando in nodos:
            nodos[trabajando] = {"estado": "error", "segundos": None}
        dibujar(f"Falló a los {segundos:.0f} s. Revisa el mensaje y usa Reintentar.")
        st.session_state.error = {"mensaje": explicar_error(e), "detalle": f"{type(e).__name__}: {e}"}
        st.session_state.avance = lineas
        return None
    segundos = time.perf_counter() - inicio

    foto = grafo().get_state(config())
    resultado = dict(foto.values)
    if foto.interrupts:
        resultado["__interrupt__"] = foto.interrupts
        dibujar(f"Listo en {vista.duracion(segundos)}. El informe espera tu firma.")
    else:
        dibujar(f"Caso cerrado en {vista.duracion(segundos)}.")
    st.session_state.resultado = resultado
    st.session_state.error = None
    st.session_state.avance = lineas
    return segundos


# ---------- formulario del caso ----------

ESCENARIO_EJEMPLO = "Caso de ejemplo (H5N1)"


def opciones_escenarios() -> list[str]:
    return [ESCENARIO_EJEMPLO] + listar_escenarios()


def campo(nombre: str, corriendo: bool):
    """Un campo del caso, con su etiqueta (unidad, * si es obligatorio) y un ejemplo de guia."""
    spec = vista.CAMPOS.get(nombre, {"etiqueta": nombre.replace("_", " ").capitalize(), "tipo": "texto", "ejemplo": ""})
    clave, etiqueta = f"caso_{nombre}", vista.etiqueta_campo(spec)
    ejemplo = f"ej: {spec['ejemplo']}" if spec.get("ejemplo") else ""
    if spec["tipo"] in ("lineas", "laboratorio"):
        st.text_area(etiqueta, key=clave, placeholder=ejemplo, height=118, disabled=corriendo)
    elif spec["tipo"] == "parrafo":
        st.text_area(etiqueta, key=clave, placeholder=ejemplo, height=76, disabled=corriendo)
    else:
        st.text_input(etiqueta, key=clave, placeholder=ejemplo, disabled=corriendo)


def formulario_caso(corriendo: bool):
    """El caso que ingresa el veterinario. Los valores quedan en session_state (caso_<campo>)
    y vista.leer_caso() los convierte en el caso que reciben los agentes."""
    titulo_col, ejemplo_col = st.columns([3, 1], vertical_alignment="bottom")
    titulo_col.html(vista.h2("Caso a analizar", "* obligatorio · si un dato no se conoce, déjalo vacío: "
                                                "el informe lo marcará como información faltante"))
    with ejemplo_col.popover("Cargar caso de ejemplo", icon=":material/folder_open:", width="stretch",
                             disabled=corriendo):
        st.selectbox("Caso de ejemplo", opciones_escenarios(), index=None, placeholder="Elige un caso…",
                     key="escenario_elegido", on_change=pedir_escenario)
        st.button("Vaciar formulario", icon=":material/delete_sweep:", on_click=pedir_formulario_vacio, width="stretch")

    st.html(vista.h3("1 · Plantel y ubicación"))
    izquierda, centro, mapa_col = st.columns([1, 1, 0.8])
    with izquierda:
        for nombre in ("especie", "sistema_productivo", "n_animales"):
            campo(nombre, corriendo)
    with centro:
        for nombre in ("region", "comuna", "fecha_evento"):
            campo(nombre, corriendo)
    with mapa_col:
        mapa(st.session_state.get("caso_comuna") or st.session_state.get("caso_region") or "")

    st.html(vista.h3("2 · Cuadro clínico"))
    izquierda, derecha = st.columns(2)
    with izquierda:
        campo("signos_clinicos", corriendo)
    with derecha:
        mort, morb = st.columns(2)
        with mort:
            campo("mortalidad_pct", corriendo)
        with morb:
            campo("morbilidad_pct", corriendo)
        campo("indicadores_productivos", corriendo)

    st.html(vista.h3("3 · Laboratorio y clima"))
    izquierda, derecha = st.columns(2)
    with izquierda:
        campo("resultados_laboratorio", corriendo)
    with derecha:
        for col, (sub, spec) in zip(st.columns(3), vista.CLIMA.items()):
            col.text_input(vista.etiqueta_campo(spec), key=f"caso_clima_{sub}", placeholder=f"ej: {spec['ejemplo']}",
                           disabled=corriendo)
        lugar_col, fecha_col, boton_col = st.columns([1.1, 1, 0.9], vertical_alignment="bottom")
        lugar_col.text_input("Lugar del clima", key="lugar_clima", placeholder="la comuna del caso",
                             disabled=corriendo)
        fecha_col.text_input("Fecha del clima (AAAA-MM-DD)", key="fecha_clima", placeholder="la fecha del evento",
                             disabled=corriendo)
        boton_col.button("Traer clima", icon=":material/cloud_download:", on_click=pedir_clima, disabled=corriendo,
                         width="stretch", help="Completa temperatura, humedad y precipitación desde Open-Meteo")
        if st.session_state.get("clima_error"):
            st.warning("No se pudo obtener el clima de Open-Meteo: revisa el lugar, la fecha o la conexión a internet.")

    st.html(vista.h3("4 · Entorno"))
    for col, nombre in zip(st.columns(3), ("aves_silvestres_cercanas", "historial_brotes_zona", "movimientos_animales")):
        with col:
            campo(nombre, corriendo)
    otros = [c for c in CAMPOS_CASO if c not in vista.CAMPOS and c != "clima"]
    for nombre in otros:
        campo(nombre, corriendo)

    st.button("Analizar", type="primary", icon=":material/play_arrow:", disabled=corriendo, on_click=pedir_analisis)
    errores_caso = st.session_state.get("errores_caso")
    if errores_caso:
        st.warning("Revisa el caso antes de analizar:\n\n" + "\n".join(f"- {e}" for e in errores_caso))


def resumen_caso(caso: dict) -> str:
    animales = caso.get("n_animales")
    partes = [caso.get("especie"), caso.get("comuna") or caso.get("region"), caso.get("fecha_evento"),
              f"{animales:,} animales".replace(",", " ") if isinstance(animales, int) else None]
    return " · ".join(str(p) for p in partes if p)


# ---------- mapa de la comuna ----------

@st.cache_data(ttl=600, show_spinner=False)
def coordenadas(lugar: str):
    # sin internet devuelve None y no hay mapa; se vuelve a intentar a los 10 minutos
    return clima.ubicar(lugar)


def mapa(lugar: str):
    """Mapa chico de la comuna. Sin internet (o sin comuna) no se dibuja nada."""
    punto = coordenadas(lugar.strip()) if lugar and lugar.strip() else None
    if punto is None:
        st.caption("📍 Con la comuna escrita y conexión a internet, aquí aparece el mapa de la zona.")
        return
    st.map(pd.DataFrame({"lat": [punto[0]], "lon": [punto[1]]}), zoom=8, size=3000,
           color="#0f766e99", height=205)
    st.caption(f"📍 {lugar}")


# ---------- informe ----------

def notas_al_pie(numeros: dict[str, int], indice: dict[str, dict]):
    """Cada cita del informe como nota numerada que se abre y muestra el fragmento y la fuente."""
    st.html(vista.h3("Notas al pie · abre una para ver el fragmento citado"))
    if not numeros:
        st.caption("El informe no cita fuentes.")
        return
    with st.container(horizontal=True):
        for doc_id, n in numeros.items():
            doc = indice.get(doc_id)
            with st.popover(f"{n} · {doc_id}"):
                if doc is None:
                    st.markdown(f"**{doc_id}**")
                    st.caption("⚠ Esta fuente no aparece en la evidencia que recuperaron los agentes: "
                               "puede ser una cita inventada por el modelo.")
                    continue
                st.markdown(f"**{doc['titulo'] or doc_id}**")
                st.caption(f"{doc_id} · {doc['institucion']} {doc['anio']}".strip())
                for f in doc["fragmentos"]:
                    st.caption(f"Recuperado por {vista.NOMBRE_NODO.get(f['agente'], f['agente'])}"
                               + (f" · fragmento {f['chunk']}" if f["chunk"] != "" else ""))
                    with st.container(border=True, height=220):
                        st.markdown(f["texto"])


def evidencia_por_agente(resultados: dict):
    for agente in ESPECIALISTAS:
        salida = resultados.get(agente, {})
        documentos = vista.indice_evidencia({agente: salida})
        st.html(vista.h3(f"{vista.ICONO_NODO[agente]} {vista.NOMBRE_NODO[agente]} · "
                         f"{len(salida.get('evidencia', []))} fragmentos") + vista.resumen_agente(agente, salida))
        if not documentos:
            st.caption("Sin evidencia: no se pudo buscar en la colección de este agente.")
            continue
        # un chip por documento: si el agente trajo varios fragmentos del mismo, van juntos adentro
        with st.container(horizontal=True):
            for doc_id, doc in documentos.items():
                n = len(doc["fragmentos"])
                with st.popover(f"{doc_id} · {doc['institucion']} {doc['anio']}".strip()
                                + (f" · {n} fragmentos" if n > 1 else "")):
                    st.markdown(f"**{doc['titulo'] or doc_id}**")
                    st.caption(f"{doc_id} · {doc['institucion']} {doc['anio']}")
                    for f in doc["fragmentos"]:
                        if f["chunk"] != "":
                            st.caption(f"Fragmento {f['chunk']}")
                        with st.container(border=True, height=220):
                            st.markdown(f["texto"])


def mostrar_informe(resultado: dict, pausa: dict | None):
    informe = (pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {})
    resultados = resultado.get("resultados", {})
    numeros = vista.numerar_citas(informe)
    indice = vista.indice_evidencia(resultados)

    # 1. nivel de riesgo, por que y confianza
    base = f"basado en {len(indice)} documentos recuperados" if indice else "sin documentos recuperados"
    st.html(vista.bloque_riesgo(informe, vista.confianza_global(informe, resultados), base, PENDIENTE)
            + vista.aviso(informe.get("aviso") or AVISO_INFORME))

    # 2. que hacer ahora
    recomendaciones = informe.get("recomendaciones") or []
    grupos = vista.recomendaciones_por_prioridad(recomendaciones)
    sin_inmediatas = "El informe no marca acciones de prioridad inmediata." if recomendaciones else PENDIENTE
    despues = [("Corto plazo", vista.items_recomendacion(grupos["corto_plazo"], numeros), "Sin acciones de corto plazo."),
               ("Preventivas", vista.items_recomendacion(grupos["preventiva"], numeros), "Sin acciones preventivas."),
               ("Protocolos de bioseguridad", [vista.esc(p) for p in informe.get("protocolos_bioseguridad") or []],
                PENDIENTE)]
    if grupos["otra"]:
        despues.append(("Sin prioridad indicada", vista.items_recomendacion(grupos["otra"], numeros), ""))
    st.html(vista.h2("Qué hacer ahora", "acciones de prioridad inmediata")
            + vista.bloque_notificacion(vista.notificacion(resultados))
            + vista.acciones_inmediatas(grupos["inmediata"], numeros, sin_inmediatas)
            + '<div style="height:.6rem"></div>' + vista.columnas(despues))

    # 3. riesgos por dimension
    st.html(vista.h2("Riesgos por dimensión One Health")
            + vista.riesgos_one_health(informe.get("riesgos_identificados") or [], numeros))

    # 4. lo que falta para decidir mejor
    st.html(vista.h2("Para decidir mejor", "lo que falta o no es seguro") + vista.para_decidir_mejor(informe, PENDIENTE))
    if pausa and pausa.get("preguntas_pendientes"):
        st.warning("Preguntas que el moderador dejó sin resolver:\n\n" + "\n".join(
            f"- **{p.get('agente_destino')}**: {p.get('pregunta')}" for p in pausa["preguntas_pendientes"]))

    # 5. evidencia y citas
    with st.expander(f"Evidencia y citas · {len(indice)} documentos · {len(numeros)} citados en el informe",
                     icon=":material/menu_book:"):
        notas_al_pie(numeros, indice)
        evidencia_por_agente(resultados)


# ---------- modo equipo ----------

def mostrar_comparacion(corriendo: bool):
    archivos = vista.archivos_comparacion(RESULTADOS)
    if not archivos:
        st.info("No hay corridas en resultados/. Genera una con `python -m vet_agent.evaluar`.")
        return
    st.caption("Corridas guardadas por `python -m vet_agent.evaluar`: el mismo caso con un solo LLM (B1), "
               "un agente con RAG (B2) y el sistema multiagente (B3).")
    elegir_col, caso_col = st.columns(2)
    nombre = elegir_col.selectbox("Corrida", list(archivos), key="corrida_comparar", disabled=corriendo)
    filas = vista.leer_comparacion(archivos[nombre])
    casos = list(dict.fromkeys((f["escenario"], f["repeticion"]) for f in filas))
    if not casos:
        st.info("El archivo no tiene filas.")
        return
    caso = caso_col.selectbox("Caso", casos, key="caso_comparar", disabled=corriendo or len(casos) == 1,
                              format_func=lambda c: f"{c[0]} · repetición {c[1]}")
    elegidas = {f["version"]: f for f in filas if (f["escenario"], f["repeticion"]) == caso}
    for col, version in zip(st.columns(3), vista.VERSIONES):
        col.html(vista.tarjeta_version(version, elegidas.get(version)))
    tiempos = pd.DataFrame({"versión": [f"{v} · {vista.VERSIONES[v]}" for v in vista.VERSIONES if v in elegidas],
                            "minutos": [round(elegidas[v]["segundos"] / 60, 1) for v in vista.VERSIONES if v in elegidas]})
    st.html(vista.h3("Tiempo de cada versión (minutos)"))
    st.bar_chart(tiempos, x="versión", y="minutos", color=vista.ACENTO, horizontal=True, height=170)


def modo_equipo(resultado: dict | None, pausa: dict | None, corriendo: bool):
    st.html(vista.h2("Modo equipo", "para el equipo del proyecto: comparación, JSON y salidas de los agentes"))
    comparar, informe_json, agentes = st.tabs(["Comparar B1·B2·B3", "JSON del informe", "Salidas de los agentes"])
    with comparar:
        mostrar_comparacion(corriendo)
    with informe_json:
        if resultado:
            st.json((pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {}))
        else:
            st.caption("El JSON del informe aparece después del análisis.")
    with agentes:
        resultados = (resultado or {}).get("resultados", {})
        if not resultados:
            st.caption("Las salidas aparecen después del análisis.")
        for agente in ESPECIALISTAS:
            if agente in resultados:
                st.caption(f"{vista.NOMBRE_NODO[agente]} · " + ("redacción simulada, con los doc_id reales en «fuentes»"
                                                                 if USAR_SIMULACION else f"redactada por {LLM_MODELO}"))
                st.json({k: v for k, v in resultados[agente].items() if k != "evidencia"}, expanded=False)


def informe_a_markdown(informe: dict, resultado: dict) -> str:
    """Arma en Markdown el informe, en el mismo orden que la pantalla, para el boton de descarga."""
    lineas = ["# Informe epidemiológico", ""]
    nivel = informe.get("nivel_riesgo_global")
    if nivel:
        lineas += [f"## Nivel de riesgo: {nivel.replace('_', ' ').upper()}", ""]
        if informe.get("justificacion_riesgo"):
            lineas += [informe["justificacion_riesgo"], ""]
    if informe.get("resumen_ejecutivo"):
        lineas += ["## Resumen", "", informe["resumen_ejecutivo"], ""]
    lineas += [f"> {informe.get('aviso') or AVISO_INFORME}", ""]

    noti = vista.notificacion(resultado.get("resultados", {}))
    if noti["estado"]:
        quien = ", ".join(noti["autoridades"]) or "la autoridad competente"
        lineas += [f"**Notificación obligatoria:** {noti['estado'].replace('_', ' ')} ({quien})", ""]

    grupos = vista.recomendaciones_por_prioridad(informe.get("recomendaciones"))
    for titulo, clave in (("Qué hacer ahora", "inmediata"), ("Corto plazo", "corto_plazo"),
                          ("Preventivas", "preventiva"), ("Sin prioridad indicada", "otra")):
        if grupos[clave]:
            lineas.append(f"## {titulo}")
            for r in grupos[clave]:
                lineas.append(
                    f"- **{r.get('accion', '')}**"
                    + (f" · responsable: {r['responsable_sugerido']}" if r.get("responsable_sugerido") else "")
                    + (" · requiere autoridad sanitaria" if r.get("requiere_autoridad") else "")
                    + f" {citas(r.get('fuentes'))}")
            lineas.append("")

    if informe.get("protocolos_bioseguridad"):
        lineas += ["## Protocolos de bioseguridad"] + [f"- {p}" for p in informe["protocolos_bioseguridad"]] + [""]

    if informe.get("riesgos_identificados"):
        lineas.append("## Riesgos")
        for r in informe["riesgos_identificados"]:
            r = vista.como_dict(r, "riesgo")
            lineas.append(f"- **{r.get('riesgo', '')}** · {r.get('dimension_one_health', '')} · "
                          f"confianza {r.get('confianza', '')} {citas(r.get('evidencia'))}")
        lineas.append("")

    for titulo, clave in (("Falta información", "informacion_faltante"),
                          ("Diagnósticos a descartar", "hipotesis_alternativas"),
                          ("Incertidumbres", "incertidumbres_y_limitaciones")):
        if informe.get(clave):
            lineas += [f"## {titulo}"] + [f"- {i}" for i in informe[clave]] + [""]

    fuentes = informe.get("fuentes_utilizadas", [])
    if fuentes:
        titulos = {e["doc_id"]: e["titulo"] for s in resultado.get("resultados", {}).values()
                   for e in s.get("evidencia", [])}
        lineas.append("## Fuentes utilizadas")
        for f in fuentes:
            f = vista.como_dict(f, "doc_id")
            titulo = titulos.get(f.get("doc_id"), "")
            lineas.append(f"- `{f.get('doc_id')}`" + (f" — {titulo}" if titulo else ""))
    return "\n".join(lineas)


def citas(ids) -> str:
    return " ".join(f"`{i}`" for i in ids or [])


# ---------- botones ----------
# Los botones solo dejan anotado el pedido (on_click corre antes de redibujar la pagina):
# asi, mientras el grafo corre, todos los botones ya se dibujan desactivados y no se
# pueden lanzar dos peticiones al LLM a la vez. El pedido se ejecuta al final del script.

def pedir_analisis():
    caso, errores = vista.leer_caso(st.session_state, CAMPOS_CASO)
    if errores:
        # sin los datos minimos no se corre el grafo: se muestra que falta
        st.session_state.errores_caso = errores
        return
    st.session_state.errores_caso = None
    st.session_state.caso_a_analizar = caso
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.resultado = None
    st.session_state.error = None
    st.session_state.grafo_vista = vista.grafo_inicial()
    st.session_state.pedido = "analizar"


def pedir_aprobacion():
    st.session_state.pedido = {"aprobado": True, "comentarios": ""}


def pedir_rechazo():
    comentarios = st.session_state.get("comentarios", "").strip()
    if not comentarios:
        # sin comentario el moderador no sabe que corregir: no se reanuda el grafo
        st.session_state.falta_comentario = True
        return
    st.session_state.comentarios = ""  # el informe nuevo parte con el campo vacio
    st.session_state.pedido = {"aprobado": False, "comentarios": comentarios}


def pedir_reintento():
    st.session_state.pedido = "reintentar"


def pedir_clima():
    # si no se escribio otro lugar/fecha para el clima, se usan los del caso
    lugar = (st.session_state.get("lugar_clima") or st.session_state.get("caso_comuna")
             or st.session_state.get("caso_region") or "")
    fecha = st.session_state.get("fecha_clima") or st.session_state.get("caso_fecha_evento") or ""
    resultado = clima.traer_clima(lugar.strip(), fecha.strip())
    st.session_state.clima_error = resultado is None
    if resultado:
        # traer_clima() devuelve las mismas claves que CASO_H5N1["clima"] (caso_clima_<clave>)
        for k, v in resultado.items():
            st.session_state[f"caso_clima_{k}"] = str(v)


def cargar_en_formulario(datos: dict):
    st.session_state.update(vista.caso_a_formulario(datos, CAMPOS_CASO))
    # el lugar/fecha del boton "Traer clima" tambien se actualizan: si no, quedarian
    # apuntando al caso anterior y "Traer clima" traeria el clima de otro lugar/fecha
    st.session_state["lugar_clima"] = datos.get("comuna") or datos.get("region") or ""
    st.session_state["fecha_clima"] = datos.get("fecha_evento") or ""
    st.session_state.errores_caso = None
    st.session_state.clima_error = False


def pedir_escenario():
    id_escenario = st.session_state.get("escenario_elegido")
    if not id_escenario:
        return
    # el caso de ejemplo tambien se carga: si no, quedarian los datos del escenario anterior
    cargar_en_formulario(CASO_H5N1 if id_escenario == ESCENARIO_EJEMPLO else obtener_escenario(id_escenario))


def pedir_formulario_vacio():
    cargar_en_formulario({})
    st.session_state.escenario_elegido = None


def barra_firma(resultado: dict, pausa: dict | None, corriendo: bool):
    """Barra fija abajo: firmar (aprobar/rechazar) el borrador o descargar el informe aprobado."""
    with st.container(key="firma"):
        if pausa:
            texto_col, aprobar_col, rechazar_col = st.columns([4, 1, 1], vertical_alignment="bottom")
            with texto_col:
                st.html('<div class="va-firma-t">✍ Revisión del médico veterinario</div>')
                st.text_area("Comentarios para el moderador (obligatorio si rechazas)", key="comentarios",
                             disabled=corriendo, height=68)
            aprobar_col.button("Aprobar", type="primary", icon=":material/check:", width="stretch",
                               disabled=corriendo, on_click=pedir_aprobacion)
            rechazar_col.button("Rechazar", icon=":material/undo:", width="stretch", disabled=corriendo,
                                on_click=pedir_rechazo)
            if st.session_state.pop("falta_comentario", False):
                st.warning("Escribe un comentario para que el moderador sepa qué corregir.")
        else:
            aviso_col, descarga_col = st.columns([3, 1], vertical_alignment="center")
            aviso_col.success("Informe aprobado por el revisor.", icon=":material/verified:")
            informe = resultado.get("evaluacion", {}).get("informe", {})
            id_escenario = st.session_state.get("escenario_elegido")
            nombre = f"informe_{id_escenario}.md" if id_escenario and id_escenario != ESCENARIO_EJEMPLO else "informe.md"
            descarga_col.download_button("Descargar informe (Markdown)", informe_a_markdown(informe, resultado),
                                         file_name=nombre, mime="text/markdown", icon=":material/download:",
                                         type="primary", width="stretch")


def ejecutar(pedido, lienzo):
    if pedido == "analizar":
        estado = {"caso": st.session_state.caso_a_analizar, "resultados": {}, "solicitudes": {}, "revisiones": 0,
                  "decision_humana": {}}
        segundos = correr(estado, "Buscando evidencia y corriendo los agentes "
                                  "(la primera vez carga el modelo de embeddings)…", lienzo)
        st.session_state.segundos_analisis = segundos
        st.session_state.segundos_revision = None
        # desde ahora el formulario va plegado arriba del informe, y la pagina sube al inicio
        # para que lo primero que se vea sea el nivel de riesgo
        st.session_state.plegar_formulario = True
        st.session_state.subir = True
    elif pedido == "reintentar":
        # entrada None = seguir desde el ultimo paso guardado, el que fallo
        analizando = st.session_state.get("resultado") is None
        segundos = correr(None, "Reintentando desde el paso que falló…", lienzo)
        if segundos is not None:
            clave = "segundos_analisis" if analizando else "segundos_revision"
            st.session_state[clave] = segundos
    else:
        # Command(resume=...) le entrega la decision al grafo y sigue desde la pausa
        titulo = "Cerrando el caso…" if pedido["aprobado"] else "El moderador revisa el informe con tus comentarios…"
        segundos = correr(Command(resume=pedido), titulo, lienzo)
        # al aprobar solo se cierra el caso: el tiempo que importa es el de un rechazo
        if segundos is not None and not pedido["aprobado"]:
            st.session_state.segundos_revision = segundos


# ---------- pagina ----------

st.html(vista.CSS)

pedido = st.session_state.get("pedido")
corriendo = pedido is not None
resultado = st.session_state.get("resultado")
error = st.session_state.get("error")
pausa = resultado["__interrupt__"][0].value if resultado and "__interrupt__" in resultado else None
vista_grafo()  # el diagrama parte con todos los agentes en espera

with st.sidebar:
    st.toggle("Modo equipo", key="modo_equipo", value=DEMO_MODO_EQUIPO,
              help="Agrega la comparación B1·B2·B3, el JSON del informe y las salidas de cada agente. "
                   "Es para el equipo del proyecto, no para el veterinario.")
    st.caption(AVISO)

st.html(vista.encabezado(MODO, AVISO))

# el formulario va abierto hasta que termina el primer analisis; despues queda plegado arriba.
# Durante ese primer analisis sigue abierto (y desactivado): si se plegara al apretar Analizar,
# Streamlit dejaria visibles, atenuados, los campos de la pasada anterior mientras corre el grafo.
if st.session_state.get("plegar_formulario"):
    analizado = st.session_state.get("caso_a_analizar") or {}
    with st.expander(f"Caso analizado · {resumen_caso(analizado)}  (abrir para editar y volver a analizar)",
                     icon=":material/edit_note:"):
        formulario_caso(corriendo)
else:
    formulario_caso(corriendo)

# proceso: el diagrama mientras corre o si fallo; si termino bien, una linea que se despliega
lienzo = st.empty()
if not corriendo:
    if error:
        svg(lienzo, vista.diagrama_grafo(vista_grafo()))
    elif resultado:
        with lienzo.container():
            linea = vista.resumen_proceso(vista_grafo(), st.session_state.get("segundos_analisis"),
                                          st.session_state.get("segundos_revision"))
            with st.expander(linea, icon=":material/hub:"):
                svg(st, vista.diagrama_grafo(vista_grafo()))
                if st.session_state.get("avance"):
                    st.markdown("\n\n".join(st.session_state.avance))

if error:
    with st.container(border=True):
        st.error(error["mensaje"])
        st.caption(f"Detalle técnico: {error['detalle'][:500]}")
        if not resultado and st.session_state.get("avance"):
            st.markdown("Alcanzaron a terminar:\n\n" + "\n\n".join(st.session_state.avance))
        st.button("Reintentar", icon=":material/refresh:", disabled=corriendo, on_click=pedir_reintento)

if resultado:
    mostrar_informe(resultado, pausa)

if st.session_state.get("modo_equipo"):
    modo_equipo(resultado, pausa, corriendo)

# con un error pendiente el grafo no esta en la pausa de revision: primero hay que reintentar
if resultado and not (pausa and error):
    barra_firma(resultado, pausa, corriendo)

if corriendo:
    # mientras corre, la vista va al diagrama (queda bajo el boton Analizar o arriba del informe)
    desplazar("const g = d.querySelector('.va-grafo'); if (g) { g.scrollIntoView({block: 'center'}); clearInterval(t); }")
elif st.session_state.pop("subir", False):
    # al terminar el analisis, arriba: lo primero que se lee es el nivel de riesgo
    desplazar("m.scrollTo({top: 0}); clearInterval(t);")

# el trabajo pesado va al final, con toda la pagina ya dibujada (y los botones desactivados)
if corriendo:
    try:
        ejecutar(pedido, lienzo)
    finally:
        st.session_state.pedido = None  # aunque algo falle, los botones no quedan bloqueados
    st.rerun()
