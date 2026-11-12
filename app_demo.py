"""Demo del sistema: la busqueda de evidencia (RAG) es real; la redaccion es simulada
o la hace el modelo de LM Studio, segun USAR_SIMULACION en el .env.

Uso (raiz del repo):
    streamlit run app_demo.py

Pantalla: a la izquierda el caso (escenario, clima y formulario); al centro el diagrama
del grafo, que se ilumina mientras corren los agentes, y las pestanas Informe, Evidencia,
Comparar B1·B2·B3 y JSON; abajo, fija, la barra de firma de la revision humana.
Lo que se dibuja (HTML/CSS) esta en vet_agent/vista_demo.py; aca va la logica.
"""
import json
import pathlib
import time
import uuid

import openai
import pandas as pd
import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent import vista_demo as vista
from vet_agent.api import listar_escenarios, obtener_escenario
from vet_agent.config import USAR_SIMULACION, LLM_MODELO, LLM_URL, LLM_TIMEOUT
from vet_agent.datos import clima
from vet_agent.grafo import construir_grafo, ESPECIALISTAS
from vet_agent.main import CASO_H5N1

st.set_page_config(page_title="VET·AGENT · Vigilancia One Health", page_icon="🐔", layout="wide",
                   initial_sidebar_state="expanded")

if USAR_SIMULACION:
    MODO = "Redacción simulada"
    AVISO = "Redacción simulada (USAR_SIMULACION=true): no se usa el LLM. La recuperación de evidencia es real."
else:
    MODO = f"{LLM_MODELO} · LM Studio"
    AVISO = (f"Modelo en uso: {LLM_MODELO} (LM Studio). Con el modelo real el análisis "
             "puede tardar varios minutos. La recuperación de evidencia es real.")

RESULTADOS = pathlib.Path(__file__).resolve().parent / "resultados"


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


# ---------- diagrama del grafo en vivo ----------

def svg(destino, contenido: str):
    # st.html borra los <svg>; st.markdown con HTML los deja. El texto ya viene escapado (vista.esc)
    destino.markdown(contenido, unsafe_allow_html=True)


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


# ---------- formulario del caso (panel izquierdo) ----------

ETIQUETAS = {"especie": "Especie", "sistema_productivo": "Sistema productivo", "region": "Región", "comuna": "Comuna",
             "fecha_evento": "Fecha del evento", "n_animales": "N.º de animales", "signos_clinicos": "Signos clínicos",
             "mortalidad_pct": "Mortalidad (%)", "morbilidad_pct": "Morbilidad (%)",
             "resultados_laboratorio": "Resultados de laboratorio", "temperatura_media_c": "Temperatura (°C)",
             "humedad_relativa_pct": "Humedad (%)", "precipitacion_mm": "Precipitación (mm)",
             "aves_silvestres_cercanas": "Aves silvestres cercanas", "historial_brotes_zona": "Historial de brotes en la zona",
             "movimientos_animales": "Movimientos de animales", "indicadores_productivos": "Indicadores productivos"}

# secciones del formulario; un campo que no este aca cae en "Otros datos"
GRUPOS = [("🏠 Plantel y ubicación", ["especie", "sistema_productivo", "region", "comuna", "fecha_evento", "n_animales"]),
          ("🩺 Cuadro clínico", ["signos_clinicos", "mortalidad_pct", "morbilidad_pct", "indicadores_productivos"]),
          ("🧪 Laboratorio", ["resultados_laboratorio"]),
          ("🌦 Clima", ["clima"]),
          ("🌿 Entorno", ["aves_silvestres_cercanas", "historial_brotes_zona", "movimientos_animales"])]


def etiqueta(campo: str) -> str:
    return ETIQUETAS.get(campo, campo.replace("_", " ").capitalize())


def _como_texto(valor) -> str:
    # los campos numéricos se muestran como texto para poder editarlos
    return "" if valor is None else str(valor)


def campo_del_caso(campo: str, valor, caso: dict):
    """Dibuja un campo de CASO_H5N1 y deja en `caso` lo que quedo escrito."""
    # con la clave en session_state, el selectbox de escenarios y el boton "Traer clima"
    # pueden pre-llenar estos campos antes de que se vuelvan a dibujar
    clave = f"caso_{campo}"
    if isinstance(valor, list) and valor and isinstance(valor[0], dict):
        # lista de objetos (ej: resultados_laboratorio): se edita como JSON
        etiqueta_campo = etiqueta(campo) + " (JSON)"
        if clave in st.session_state:
            texto = st.text_area(etiqueta_campo, key=clave, height=150)
        else:
            texto = st.text_area(etiqueta_campo, json.dumps(valor, ensure_ascii=False, indent=2), key=clave, height=150)
        try:
            caso[campo] = json.loads(texto)
        except json.JSONDecodeError:
            caso[campo] = valor  # si lo rompen, se conserva el valor precargado
    elif isinstance(valor, list):
        etiqueta_campo = etiqueta(campo) + " (uno por línea)"
        if clave in st.session_state:
            texto = st.text_area(etiqueta_campo, key=clave)
        else:
            texto = st.text_area(etiqueta_campo, "\n".join(valor), key=clave)
        caso[campo] = [linea.strip() for linea in texto.splitlines() if linea.strip()]
    elif isinstance(valor, dict):
        caso[campo] = {}
        for k, v in valor.items():
            subclave = f"{clave}_{k}"
            if subclave in st.session_state:
                caso[campo][k] = st.text_input(etiqueta(k), key=subclave)
            else:
                caso[campo][k] = st.text_input(etiqueta(k), _como_texto(v), key=subclave)
    else:
        if clave in st.session_state:
            caso[campo] = st.text_input(etiqueta(campo), key=clave)
        else:
            caso[campo] = st.text_input(etiqueta(campo), _como_texto(valor), key=clave)


def formulario_caso() -> dict:
    """Muestra el caso precargado (o el escenario elegido, ver cargar_escenario_en_formulario)
    agrupado en secciones plegables, y devuelve lo que quedo escrito (en el orden de CASO_H5N1)."""
    caso = {}
    agrupados = {c for _, campos in GRUPOS for c in campos}
    grupos = GRUPOS + [("📎 Otros datos", [c for c in CASO_H5N1 if c not in agrupados])]
    for titulo, campos in grupos:
        campos = [c for c in campos if c in CASO_H5N1]
        if not campos:
            continue
        with st.expander(titulo):
            for campo in campos:
                campo_del_caso(campo, CASO_H5N1[campo], caso)
    return {c: caso[c] for c in CASO_H5N1}


ESCENARIO_EJEMPLO = "Caso de ejemplo (H5N1)"


def opciones_escenarios() -> list[str]:
    return [ESCENARIO_EJEMPLO] + listar_escenarios()


def cargar_escenario_en_formulario(datos: dict):
    """Pone la Parte A de un escenario en session_state para que formulario_caso() la muestre.

    Usa CASO_H5N1 como plantilla de los campos que existen: lo que falte en el escenario
    elegido queda vacio, para no dejar pegado algo del escenario anterior.
    """
    for campo, plantilla in CASO_H5N1.items():
        valor = datos.get(campo)
        clave = f"caso_{campo}"
        if isinstance(plantilla, list) and plantilla and isinstance(plantilla[0], dict):
            st.session_state[clave] = json.dumps(valor or [], ensure_ascii=False, indent=2)
        elif isinstance(plantilla, list):
            st.session_state[clave] = "\n".join(valor or [])
        elif isinstance(plantilla, dict):
            for k in plantilla:
                st.session_state[f"{clave}_{k}"] = _como_texto((valor or {}).get(k))
        else:
            st.session_state[clave] = _como_texto(valor)


def panel_caso(corriendo: bool) -> dict:
    st.html('<div class="va-lado-titulo">Escenario</div>')
    st.selectbox("Escenario", opciones_escenarios(), key="escenario_elegido",
                 on_change=pedir_escenario, disabled=corriendo, label_visibility="collapsed")

    st.html('<div class="va-lado-titulo">Clima desde Open-Meteo</div>')
    lugar_col, fecha_col = st.columns(2)
    # primer dibujo: se precarga con la comuna/region y la fecha del caso; despues manda lo que escriba la persona
    lugar_defecto = CASO_H5N1.get("comuna") or CASO_H5N1.get("region", "")
    fecha_defecto = CASO_H5N1.get("fecha_evento", "")
    if "lugar_clima" in st.session_state:
        lugar_col.text_input("Lugar", key="lugar_clima")
    else:
        lugar_col.text_input("Lugar", lugar_defecto, key="lugar_clima")
    if "fecha_clima" in st.session_state:
        fecha_col.text_input("Fecha (AAAA-MM-DD)", key="fecha_clima")
    else:
        fecha_col.text_input("Fecha (AAAA-MM-DD)", fecha_defecto, key="fecha_clima")
    st.button("Traer clima", icon=":material/cloud_download:", on_click=pedir_clima, disabled=corriendo,
              width="stretch")
    if st.session_state.get("clima_error"):
        st.warning("No se pudo obtener el clima de Open-Meteo: revisa el lugar, la fecha o la conexión a internet.")

    st.html('<div class="va-lado-titulo">Datos del caso</div>')
    with st.form("caso", border=False):
        caso = formulario_caso()
        st.form_submit_button("Analizar", type="primary", icon=":material/play_arrow:", disabled=corriendo,
                              on_click=pedir_analisis, width="stretch")
    return caso


# ---------- mapa de la comuna ----------

@st.cache_data(ttl=600, show_spinner=False)
def coordenadas(lugar: str):
    # sin internet devuelve None y no hay mapa; se vuelve a intentar a los 10 minutos
    return clima.ubicar(lugar)


def mapa(caso: dict) -> bool:
    """Dibuja el mapa de la comuna del caso. Devuelve False (sin dibujar nada) si no se pudo ubicar."""
    lugar = caso.get("comuna") or caso.get("region") or ""
    punto = coordenadas(lugar)
    if punto is None:
        return False
    with st.container(border=True):
        st.html(f'<div class="va-rot">Zona del caso · {vista.esc(lugar)}</div>')
        st.map(pd.DataFrame({"lat": [punto[0]], "lon": [punto[1]]}), zoom=9, size=2500,
               color="#0f766e88", height=210)
    return True


# ---------- informe ----------

PENDIENTE = "Se completa al conectar el LLM" if USAR_SIMULACION else "El modelo no completó esta sección"


def citas(ids) -> str:
    return " ".join(f"`{i}`" for i in ids or [])


def meta_tiempos(resultado: dict) -> str:
    partes = []
    if st.session_state.get("segundos_analisis") is not None:
        partes.append(f"Análisis: {vista.duracion(st.session_state.segundos_analisis)}")
    if st.session_state.get("segundos_revision") is not None:
        partes.append(f"última revisión del moderador: {vista.duracion(st.session_state.segundos_revision)}")
    partes.append(f"revisiones del moderador: {resultado.get('revisiones', 0)}")
    return " · ".join(partes)


def notas_al_pie(numeros: dict[str, int], indice: dict[str, dict]):
    """Cada cita del informe como nota numerada que se abre y muestra el fragmento y la fuente."""
    st.html('<div class="va-sec">Notas al pie <span>abre una nota para ver el fragmento citado</span></div>')
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


def mostrar_informe(resultado: dict, pausa: dict | None, caso: dict):
    informe = (pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {})
    resultados = resultado.get("resultados", {})
    numeros = vista.numerar_citas(informe)
    indice = vista.indice_evidencia(resultados)

    medidor_col, resumen_col = st.columns([1, 2.4])
    svg(medidor_col, vista.medidor(informe.get("nivel_riesgo_global")))
    resumen_col.html(vista.resumen(informe, meta_tiempos(resultado), PENDIENTE))

    riesgos = informe.get("riesgos_identificados") or []
    columnas = st.columns([1, 1, 1, 1.25])
    for col, (dim, titulo, icono) in zip(columnas, vista.DIMENSIONES):
        notas = [(vista.NOMBRE_NODO[a], (resultados.get(a) or {}).get("one_health", {}).get(dim))
                 for a in ESPECIALISTAS]
        col.html(vista.tarjeta_one_health(dim, titulo, icono, riesgos, [(a, t) for a, t in notas if t], numeros))
    with columnas[3]:
        if not mapa(caso):
            st.html(vista.ficha_caso(caso))

    izquierda, derecha = st.columns([1.35, 1], gap="large")
    recomendaciones = informe.get("recomendaciones") or []
    izquierda.html(
        vista.seccion("Recomendaciones", vista.lista_recomendaciones(recomendaciones, numeros),
                      len(recomendaciones), PENDIENTE)
        + vista.seccion("Riesgos identificados", vista.lista_riesgos(riesgos, numeros), len(riesgos), PENDIENTE))
    bloques = [("Protocolos de bioseguridad", "protocolos_bioseguridad"),
               ("Hipótesis alternativas", "hipotesis_alternativas"),
               ("Información faltante", "informacion_faltante"),
               ("Incertidumbres y limitaciones", "incertidumbres_y_limitaciones")]
    derecha.html("".join(vista.seccion(t, vista.lista_simple(informe.get(c) or []) if informe.get(c) else "",
                                       len(informe.get(c) or []), PENDIENTE) for t, c in bloques))

    if pausa and pausa.get("preguntas_pendientes"):
        st.warning("Preguntas que el moderador dejó sin resolver:\n\n" + "\n".join(
            f"- **{p.get('agente_destino')}**: {p.get('pregunta')}" for p in pausa["preguntas_pendientes"]))

    notas_al_pie(numeros, indice)
    if informe.get("aviso"):
        st.info(informe["aviso"], icon=":material/verified_user:")
    else:
        st.caption(f"Aviso: {PENDIENTE}")


def mostrar_inicio(caso: dict):
    st.html(vista.HERO)
    ficha_col, mapa_col = st.columns([1.6, 1])
    ficha_col.html(vista.ficha_caso(caso))
    with mapa_col:
        mapa(caso)


def mostrar_evidencia(resultados: dict):
    if not resultados:
        st.html(vista.pendiente("La evidencia aparece cuando los agentes terminan el análisis."))
        return
    for agente in ESPECIALISTAS:
        salida = resultados.get(agente, {})
        evidencia = salida.get("evidencia", [])
        with st.container(border=True):
            st.html(f'<div class="va-sec" style="margin-top:0">{vista.ICONO_NODO[agente]} '
                    f'{vista.NOMBRE_NODO[agente]}<span>{len(evidencia)} fragmentos recuperados</span></div>'
                    + vista.resumen_agente(agente, salida))
            if not evidencia:
                st.info("Sin evidencia: no se pudo buscar en la colección de este agente.")
            # un chip por documento: si el agente trajo varios fragmentos del mismo, van juntos adentro
            documentos = vista.indice_evidencia({agente: salida})
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
            with st.expander("Salida del agente (JSON)"):
                st.caption("Redacción simulada, con los doc_id reales en «fuentes»."
                           if USAR_SIMULACION else f"Redactada por {LLM_MODELO}.")
                st.json({k: v for k, v in salida.items() if k != "evidencia"}, expanded=False)


def mostrar_comparacion(corriendo: bool):
    archivos = vista.archivos_comparacion(RESULTADOS)
    if not archivos:
        st.info("No hay corridas en resultados/. Genera una con `python -m vet_agent.evaluar`.")
        return
    st.html('<div class="va-pend" style="font-style:normal;margin-bottom:.4rem">Corridas guardadas por '
            '<code>python -m vet_agent.evaluar</code>: el mismo caso con un solo LLM (B1), un agente con RAG (B2) '
            'y el sistema multiagente (B3).</div>')
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
    st.html('<div class="va-sec">Tiempo de cada versión <span>minutos</span></div>')
    st.bar_chart(tiempos, x="versión", y="minutos", color=vista.ACENTO, horizontal=True, height=170)


def informe_a_markdown(informe: dict, resultado: dict) -> str:
    """Arma en Markdown el mismo informe que se ve en pantalla, para el boton de descarga."""
    lineas = ["# Informe epidemiológico", ""]
    if informe.get("resumen_ejecutivo"):
        lineas += ["## Resumen", "", informe["resumen_ejecutivo"], ""]

    nivel = informe.get("nivel_riesgo_global")
    if nivel:
        lineas.append(f"## Nivel de riesgo: {nivel.replace('_', ' ').upper()}")
        lineas.append("")
        if informe.get("justificacion_riesgo"):
            lineas += [informe["justificacion_riesgo"], ""]

    if informe.get("riesgos_identificados"):
        lineas.append("## Riesgos")
        for r in informe["riesgos_identificados"]:
            r = vista.como_dict(r, "riesgo")
            lineas.append(f"- **{r.get('riesgo', '')}** · {r.get('dimension_one_health', '')} · "
                          f"confianza {r.get('confianza', '')} {citas(r.get('evidencia'))}")
        lineas.append("")

    if informe.get("recomendaciones"):
        lineas.append("## Recomendaciones")
        for r in informe["recomendaciones"]:
            r = vista.como_dict(r, "accion")
            lineas.append(
                f"- **{r.get('accion', '')}** · {r.get('prioridad', '').replace('_', ' ')}"
                + (f" · responsable: {r['responsable_sugerido']}" if r.get("responsable_sugerido") else "")
                + (" · requiere autoridad sanitaria" if r.get("requiere_autoridad") else "")
                + f" {citas(r.get('fuentes'))}")
        lineas.append("")

    if informe.get("protocolos_bioseguridad"):
        lineas.append("## Protocolos")
        lineas += [f"- {p}" for p in informe["protocolos_bioseguridad"]]
        lineas.append("")

    if informe.get("incertidumbres_y_limitaciones"):
        lineas.append("## Incertidumbres")
        lineas += [f"- {i}" for i in informe["incertidumbres_y_limitaciones"]]
        lineas.append("")

    if informe.get("aviso"):
        lineas += ["## Aviso", "", informe["aviso"], ""]

    fuentes = informe.get("fuentes_utilizadas", [])
    if fuentes:
        titulos = {e["doc_id"]: e["titulo"] for s in resultado.get("resultados", {}).values()
                   for e in s.get("evidencia", [])}
        lineas.append("## Fuentes utilizadas")
        for f in fuentes:
            titulo = titulos.get(f["doc_id"], "")
            lineas.append(f"- `{f['doc_id']}`" + (f" — {titulo}" if titulo else ""))

    return "\n".join(lineas)


# ---------- botones ----------
# Los botones solo dejan anotado el pedido (on_click corre antes de redibujar la pagina):
# asi, mientras el grafo corre, todos los botones ya se dibujan desactivados y no se
# pueden lanzar dos peticiones al LLM a la vez. El pedido se ejecuta al final del script.

def pedir_analisis():
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
    resultado = clima.traer_clima(st.session_state.get("lugar_clima", ""), st.session_state.get("fecha_clima", ""))
    st.session_state.clima_error = resultado is None
    if resultado:
        # traer_clima() devuelve las mismas claves que CASO_H5N1["clima"], asi que
        # calzan con las que usa formulario_caso() para precargar el campo (caso_clima_<clave>)
        for k, v in resultado.items():
            st.session_state[f"caso_clima_{k}"] = str(v)


def pedir_escenario():
    id_escenario = st.session_state.get("escenario_elegido")
    if not id_escenario:
        return
    # el caso de ejemplo tambien se carga: si no, quedarian los datos del escenario anterior
    datos = CASO_H5N1 if id_escenario == ESCENARIO_EJEMPLO else obtener_escenario(id_escenario)
    cargar_escenario_en_formulario(datos)
    # el lugar/fecha del boton "Traer clima" tambien se actualizan: si no, quedarian
    # apuntando al caso anterior y "Traer clima" traeria el clima de otro lugar/fecha
    st.session_state["lugar_clima"] = datos.get("comuna") or datos.get("region") or ""
    st.session_state["fecha_clima"] = datos.get("fecha_evento") or ""


def barra_firma(resultado: dict, pausa: dict | None, corriendo: bool):
    """Barra fija abajo: firmar (aprobar/rechazar) el borrador o descargar el informe aprobado."""
    with st.container(key="firma"):
        if pausa:
            texto_col, aprobar_col, rechazar_col = st.columns([4, 1, 1], vertical_alignment="bottom")
            with texto_col:
                st.html('<div class="va-rot">✍ Revisión humana · firma del especialista</div>')
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


def ejecutar(pedido, caso: dict, lienzo):
    if pedido == "analizar":
        estado = {"caso": caso, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}
        segundos = correr(estado, "Buscando evidencia y corriendo los agentes "
                                  "(la primera vez carga el modelo de embeddings)…", lienzo)
        st.session_state.segundos_analisis = segundos
        st.session_state.segundos_revision = None
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

with st.sidebar:
    caso = panel_caso(corriendo)

escenario = st.session_state.get("escenario_elegido") or ESCENARIO_EJEMPLO
st.html(vista.encabezado(caso, escenario, MODO, AVISO))
lienzo = st.empty()
svg(lienzo, vista.diagrama_grafo(vista_grafo()))

resultado = st.session_state.get("resultado")
error = st.session_state.get("error")
pausa = resultado["__interrupt__"][0].value if resultado and "__interrupt__" in resultado else None

if error:
    with st.container(border=True):
        st.error(error["mensaje"])
        st.caption(f"Detalle técnico: {error['detalle'][:500]}")
        if not resultado and st.session_state.get("avance"):
            st.markdown("Alcanzaron a terminar:\n\n" + "\n\n".join(st.session_state.avance))
        st.button("Reintentar", icon=":material/refresh:", disabled=corriendo, on_click=pedir_reintento)

pestana_informe, pestana_evidencia, pestana_comparar, pestana_json = st.tabs(
    ["📋 Informe", "📚 Evidencia", "⚖ Comparar B1·B2·B3", "{ } JSON"])
with pestana_informe:
    if resultado:
        mostrar_informe(resultado, pausa, caso)
        if st.session_state.get("avance"):
            with st.expander("Registro de la última corrida"):
                st.markdown("\n\n".join(st.session_state.avance))
    else:
        mostrar_inicio(caso)
with pestana_evidencia:
    mostrar_evidencia((resultado or {}).get("resultados", {}))
with pestana_comparar:
    mostrar_comparacion(corriendo)
with pestana_json:
    if resultado:
        st.caption("Borrador del informe tal como lo entregó el moderador.")
        st.json((pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {}))
    else:
        st.html(vista.pendiente("El JSON del informe aparece después del análisis."))

# con un error pendiente el grafo no esta en la pausa de revision: primero hay que reintentar
if resultado and not (pausa and error):
    barra_firma(resultado, pausa, corriendo)

# el trabajo pesado va al final, con toda la pagina ya dibujada (y los botones desactivados)
if corriendo:
    try:
        ejecutar(pedido, caso, lienzo)
    finally:
        st.session_state.pedido = None  # aunque algo falle, los botones no quedan bloqueados
    st.rerun()
