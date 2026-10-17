"""Demo del sistema: la busqueda de evidencia (RAG) es real, la redaccion sigue simulada.

Uso (raiz del repo):
    streamlit run app_demo.py
"""
import uuid

import streamlit as st
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent.grafo import construir_grafo, ESPECIALISTAS
from vet_agent.main import CASO_H5N1

AVISO = "Redacción simulada: el LLM se conectará en LM Studio. La recuperación de evidencia es real."


@st.cache_resource
def grafo():
    # un solo grafo (y checkpointer) para toda la app; cada analisis usa su propio thread_id
    return construir_grafo(InMemorySaver())


def config():
    return {"configurable": {"thread_id": st.session_state.thread_id}}


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
            st.caption("Salida del agente (redacción simulada, con los doc_id reales en «fuentes»):")
            st.json({k: v for k, v in salida.items() if k != "evidencia"}, expanded=False)


def mostrar_informe(resultado: dict, pausa: dict | None):
    st.subheader("Borrador del informe")
    informe = (pausa or {}).get("informe") or resultado.get("evaluacion", {}).get("informe", {})
    col1, col2 = st.columns(2)
    col1.metric("Nivel de riesgo", informe.get("nivel_riesgo_global", "no determinado"))
    col2.metric("Revisiones del moderador", resultado.get("revisiones", 0))
    st.write(informe.get("resumen_ejecutivo", ""))
    st.json({k: v for k, v in informe.items() if k != "fuentes_utilizadas"}, expanded=False)

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
        with st.spinner("Reanudando el grafo..."):
            st.session_state.resultado = grafo().invoke(Command(resume=decision), config())
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
    with st.spinner("Buscando evidencia y corriendo los agentes (la primera vez carga el modelo de embeddings)..."):
        st.session_state.resultado = grafo().invoke(estado, config())

resultado = st.session_state.get("resultado")
if resultado:
    pausa = resultado["__interrupt__"][0].value if "__interrupt__" in resultado else None
    mostrar_evidencia(resultado.get("resultados", {}))
    mostrar_informe(resultado, pausa)
    if pausa:
        revision(pausa)
    else:
        st.success("Informe aprobado por el revisor.")
