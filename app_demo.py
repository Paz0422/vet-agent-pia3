"""Demo del sistema: la busqueda de evidencia (RAG) es real; la redaccion es simulada
o la hace el modelo de LM Studio, segun USAR_SIMULACION en el .env.

Uso (raiz del repo):
    streamlit run app_demo.py
"""
import time
import uuid

import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent.config import USAR_SIMULACION, LLM_MODELO
from vet_agent.grafo import construir_grafo, ESPECIALISTAS
from vet_agent.main import CASO_H5N1

if USAR_SIMULACION:
    AVISO = "Redacción simulada (USAR_SIMULACION=true): no se usa el LLM. La recuperación de evidencia es real."
else:
    AVISO = (f"Modelo en uso: {LLM_MODELO} (LM Studio). Con el modelo real el análisis "
             "puede tardar varios minutos. La recuperación de evidencia es real.")

# nombre que se muestra para cada nodo del grafo mientras corre
NOMBRE_NODO = {"climatico": "Climático", "diagnostico": "Diagnóstico", "bioseguridad": "Bioseguridad",
               "moderador": "Moderador", "revision_humana": "Revisión humana"}


@st.cache_resource
def grafo():
    # un solo grafo (y checkpointer) para toda la app; cada analisis usa su propio thread_id
    return construir_grafo(InMemorySaver())


def config():
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def correr(entrada, titulo: str) -> float:
    """Corre el grafo mostrando que nodo va terminando, guarda el resultado en la sesion
    y devuelve cuantos segundos tardo.

    El resultado queda igual que el de grafo().invoke(): el estado completo y, si el grafo
    se detuvo a esperar la revision humana, la pausa en "__interrupt__".
    """
    inicio = time.perf_counter()
    with st.status(titulo, expanded=True) as estado:
        for paso in grafo().stream(entrada, config(), stream_mode="updates"):
            for nodo, cambios in paso.items():
                if nodo == "__interrupt__":
                    continue
                # en las rondas de debate, los agentes no consultados no llaman al LLM ni devuelven cambios
                detalle = "" if cambios else " (no consultado en esta ronda)"
                estado.write(f"✅ {NOMBRE_NODO.get(nodo, nodo)} terminó en "
                             f"{time.perf_counter() - inicio:.0f} s{detalle}")
        segundos = time.perf_counter() - inicio
        estado.update(label=f"{titulo} Listo en {segundos:.0f} s", state="complete", expanded=False)

    foto = grafo().get_state(config())
    resultado = dict(foto.values)
    if foto.interrupts:
        resultado["__interrupt__"] = foto.interrupts
    st.session_state.resultado = resultado
    return segundos


def etiqueta(campo: str) -> str:
    return campo.replace("_", " ").capitalize()


def formulario_caso() -> dict:
    """Muestra el caso precargado y devuelve lo que quedo escrito."""
    caso = {}
    for campo, valor in CASO_H5N1.items():
        if isinstance(valor, list):
            texto = st.text_area(etiqueta(campo) + " (uno por línea)", "\n".join(valor))
            caso[campo] = [linea.strip() for linea in texto.splitlines() if linea.strip()]
        elif isinstance(valor, dict):
            st.markdown(f"**{etiqueta(campo)}**")
            columnas = st.columns(len(valor))
            caso[campo] = {k: col.text_input(etiqueta(k), v) for col, (k, v) in zip(columnas, valor.items())}
        else:
            caso[campo] = st.text_input(etiqueta(campo), valor)
    return caso


def cita(e: dict) -> str:
    return f"[{e['doc_id']}] {e['institucion']} {e['anio']} — {e['titulo']}"


def mostrar_evidencia(resultados: dict):
    st.subheader("Evidencia recuperada por cada agente")
    for agente in ESPECIALISTAS:
        salida = resultados.get(agente, {})
        evidencia = salida.get("evidencia", [])
        with st.expander(f"{etiqueta(agente)}: {len(evidencia)} fragmentos"):
            if not evidencia:
                st.info("Sin evidencia: no se pudo buscar en la colección de este agente.")
            for i, e in enumerate(evidencia, 1):
                st.markdown(f"**{i}. {cita(e)}**")
                with st.container(border=True):
                    st.markdown(e["texto"])
            st.caption("Salida del agente (redacción simulada, con los doc_id reales en «fuentes»):"
                       if USAR_SIMULACION else f"Salida del agente (redactada por {LLM_MODELO}):")
            st.json({k: v for k, v in salida.items() if k != "evidencia"}, expanded=False)


PENDIENTE = "Se completa al conectar el LLM" if USAR_SIMULACION else "El modelo no completó esta sección"
COLOR_RIESGO = {"bajo": "green", "moderado": "orange", "alto": "red", "muy_alto": "red"}


def seccion(titulo: str, contenido) -> bool:
    """Titulo de la seccion; si no hay contenido, el aviso de pendiente. Devuelve si hay algo que mostrar."""
    st.markdown(f"#### {titulo}")
    if contenido in (None, "", [], {}):
        st.caption(PENDIENTE)
        return False
    return True


def citas(ids) -> str:
    return " ".join(f"`{i}`" for i in ids or [])


def lista(items, formato):
    # el LLM real podria devolver textos sueltos en vez de diccionarios: se muestran tal cual
    for item in items:
        st.markdown("- " + (formato(item) if isinstance(item, dict) else str(item)))


def mostrar_informe(resultado: dict, pausa: dict | None):
    st.subheader("Borrador del informe")
    tiempos = [f"el análisis tardó {st.session_state.segundos_analisis:.0f} s"]
    if st.session_state.get("segundos_revision") is not None:
        tiempos.append(f"la última revisión del moderador, {st.session_state.segundos_revision:.0f} s")
    st.caption("⏱️ " + "; ".join(tiempos).capitalize())
    informe = (pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {})

    if seccion("Resumen", informe.get("resumen_ejecutivo")):
        st.write(informe["resumen_ejecutivo"])

    nivel = informe.get("nivel_riesgo_global")
    if seccion("Nivel de riesgo", nivel):
        color = COLOR_RIESGO.get(nivel, "gray")
        st.markdown(f":{color}-badge[{nivel.replace('_', ' ').upper()}]")
        if informe.get("justificacion_riesgo"):
            st.write(informe["justificacion_riesgo"])
    st.caption(f"Revisiones del moderador: {resultado.get('revisiones', 0)}")

    if seccion("Riesgos", informe.get("riesgos_identificados")):
        lista(informe["riesgos_identificados"], lambda r: (
            f"**{r.get('riesgo', '')}** · {r.get('dimension_one_health', '')} · "
            f"confianza {r.get('confianza', '')} {citas(r.get('evidencia'))}"))

    if seccion("Recomendaciones", informe.get("recomendaciones")):
        lista(informe["recomendaciones"], lambda r: (
            f"**{r.get('accion', '')}** · {r.get('prioridad', '').replace('_', ' ')}"
            + (f" · responsable: {r['responsable_sugerido']}" if r.get("responsable_sugerido") else "")
            + (" · requiere autoridad sanitaria" if r.get("requiere_autoridad") else "")
            + f" {citas(r.get('fuentes'))}"))

    if seccion("Protocolos", informe.get("protocolos_bioseguridad")):
        lista(informe["protocolos_bioseguridad"], str)

    if seccion("Incertidumbres", informe.get("incertidumbres_y_limitaciones")):
        lista(informe["incertidumbres_y_limitaciones"], str)

    if seccion("Aviso", informe.get("aviso")):
        st.info(informe["aviso"])

    with st.expander("Ver JSON"):
        st.json(informe)

    if pausa and pausa.get("preguntas_pendientes"):
        st.warning("Preguntas que el moderador dejó sin resolver:")
        for p in pausa["preguntas_pendientes"]:
            st.markdown(f"- **{p.get('agente_destino')}**: {p.get('pregunta')}")

    st.subheader("Fuentes utilizadas")
    titulos = {e["doc_id"]: e["titulo"] for s in resultado.get("resultados", {}).values()
               for e in s.get("evidencia", [])}
    fuentes = informe.get("fuentes_utilizadas", [])
    if fuentes:
        st.dataframe([f | {"titulo": titulos.get(f["doc_id"], "")} for f in fuentes],
                     hide_index=True, width="stretch")
    else:
        st.info("El informe no cita fuentes.")


def revision(pausa: dict):
    st.subheader("Revisión humana")
    comentarios = st.text_area("Comentarios para el moderador (si rechazas)")
    aprobar, rechazar = st.columns(2)
    decision = None
    if aprobar.button("Aprobar", type="primary", width="stretch"):
        decision = {"aprobado": True, "comentarios": ""}
    if rechazar.button("Rechazar", width="stretch"):
        decision = {"aprobado": False, "comentarios": comentarios}
    if decision:
        # Command(resume=...) le entrega la decision al grafo y sigue desde la pausa
        titulo = "Cerrando el caso..." if decision["aprobado"] else "El moderador revisa el informe con tus comentarios..."
        segundos = correr(Command(resume=decision), titulo)
        # al aprobar solo se cierra el caso: el tiempo que importa es el de un rechazo
        if not decision["aprobado"]:
            st.session_state.segundos_revision = segundos
        st.rerun()


st.set_page_config(page_title="Demo vet-agent", page_icon="🐔", layout="wide")
st.title("Análisis epidemiológico: influenza aviar H5N1")
st.warning(AVISO, icon="⚠️")

with st.form("caso"):
    st.subheader("Caso")
    caso = formulario_caso()
    analizar = st.form_submit_button("Analizar", type="primary")

if analizar:
    st.session_state.thread_id = str(uuid.uuid4())
    estado = {"caso": caso, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}
    st.session_state.segundos_analisis = correr(
        estado, "Buscando evidencia y corriendo los agentes (la primera vez carga el modelo de embeddings)...")
    st.session_state.segundos_revision = None

resultado = st.session_state.get("resultado")
if resultado:
    pausa = resultado["__interrupt__"][0].value if "__interrupt__" in resultado else None
    mostrar_evidencia(resultado.get("resultados", {}))
    mostrar_informe(resultado, pausa)
    if pausa:
        revision(pausa)
    else:
        st.success("Informe aprobado por el revisor.")
