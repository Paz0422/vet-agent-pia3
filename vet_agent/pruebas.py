import copy
import json
import pathlib

import chromadb
import httpx
import openai
from chromadb.api.types import EmbeddingFunction
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.types import Command

from vet_agent import llm
from vet_agent.agentes import base, climatico, diagnostico, bioseguridad, moderador
from vet_agent.config import MAX_RONDAS, K_FRAGMENTOS
from vet_agent.grafo import construir_grafo, despues_del_moderador, despues_de_revision_humana
from vet_agent.main import pedir_decision
from vet_agent.rag import buscar

# las pruebas siempre usan respuestas de ejemplo, aunque el .env diga otra cosa
llm.USAR_SIMULACION = True

APROBAR = {"aprobado": True, "comentarios": ""}
RECHAZAR = {"aprobado": False, "comentarios": "falta informacion de humedad"}

CASO = {
    "especie": "porcino",
    "ubicacion": "Región de O'Higgins",
    "resultados_laboratorio": ["PCR positivo a PRRSV"],
}


def estado_inicial():
    return {"caso": CASO, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}


def pregunta(destino, texto="aclarar"):
    # arma una pregunta del moderador con el formato del prompt
    return {"agente_destino": destino, "pregunta": texto, "motivo": "prueba"}


def ejecutar(decisiones):
    # corre el grafo entero y va respondiendo cada pausa humana en orden
    app = construir_grafo(InMemorySaver())
    config = {"configurable": {"thread_id": "prueba"}}
    resultado = app.invoke(estado_inicial(), config)

    for decision in decisiones:
        assert "__interrupt__" in resultado, "se esperaba una pausa para revision humana"
        resultado = app.invoke(Command(resume=decision), config)

    assert "__interrupt__" not in resultado, "el grafo no termino"
    return resultado


# ---------- pruebas de las funciones que deciden el camino ----------

def prueba_moderador_informe_final():
    estado = {"evaluacion": {"estado": "informe_final", "informe": {}}, "revisiones": 1}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_moderador_pide_debate():
    # aunque pregunte a varios agentes, la cadena siempre se vuelve a recorrer desde climatico
    evaluacion = {"estado": "requiere_debate", "preguntas": [
        pregunta("climatico"), pregunta("diagnostico"), pregunta("climatico"),
    ]}
    estado = {"evaluacion": evaluacion, "revisiones": 1}
    assert despues_del_moderador(estado) == "climatico"


def prueba_moderador_llega_al_maximo():
    evaluacion = {"estado": "requiere_debate", "preguntas": [pregunta("climatico")]}
    estado = {"evaluacion": evaluacion, "revisiones": MAX_RONDAS}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_moderador_agente_invalido():
    # si el modelo inventa un nombre de agente, no debe romper el grafo
    evaluacion = {"estado": "requiere_debate", "preguntas": [pregunta("agente climático")]}
    estado = {"evaluacion": evaluacion, "revisiones": 1}
    assert despues_del_moderador(estado) == "revision_humana"


def prueba_humano_aprueba():
    assert despues_de_revision_humana({"decision_humana": APROBAR}) == END


def prueba_humano_rechaza():
    assert despues_de_revision_humana({"decision_humana": RECHAZAR}) == "moderador"


# ---------- pruebas del grafo completo ----------

def prueba_flujo_normal():
    resultado = ejecutar([APROBAR])
    assert set(resultado["resultados"]) == {"climatico", "diagnostico", "bioseguridad"}
    assert resultado["revisiones"] == 1
    assert resultado["evaluacion"]["estado"] == "informe_final"


def prueba_rechazo_y_aprobacion():
    resultado = ejecutar([RECHAZAR, APROBAR])
    assert resultado["revisiones"] == 2
    assert resultado["decision_humana"]["aprobado"] is True


def prueba_debate_termina():
    # forzamos que el moderador simulado siempre pida debatir,
    # para comprobar que el grafo corta en MAX_RONDAS y no queda pegado
    original = copy.deepcopy(llm.RESPUESTAS_SIMULADAS["moderador"])
    llm.RESPUESTAS_SIMULADAS["moderador"] = {
        "estado": "requiere_debate",
        "preguntas": [pregunta("climatico", "precisar humedad")],
    }
    try:
        resultado = ejecutar([APROBAR])
        assert resultado["revisiones"] == MAX_RONDAS
        assert resultado["solicitudes"] == {"climatico": "precisar humedad"}
    finally:
        # dejamos la simulacion como estaba
        llm.RESPUESTAS_SIMULADAS["moderador"] = original


def prueba_debate_solo_llama_al_consultado():
    # espia sobre consultar_llm: anota que agente llama al LLM y en que orden.
    # Cada agente importa consultar_llm por su cuenta, asi que se reemplaza en cada modulo.
    llamadas = []
    original = llm.consultar_llm
    debate = {"estado": "requiere_debate", "preguntas": [pregunta("diagnostico", "precisar muestras")]}

    def espia(instrucciones, pregunta_llm, agente):
        llamadas.append(agente)
        # el moderador pide debatir solo la primera vez, despues entrega el informe
        if agente == "moderador" and llamadas.count("moderador") == 1:
            return json.dumps(debate, ensure_ascii=False)
        return original(instrucciones, pregunta_llm, agente)

    modulos = [climatico, diagnostico, bioseguridad, moderador]
    for modulo in modulos:
        modulo.consultar_llm = espia
    try:
        resultado = ejecutar([APROBAR])
    finally:
        for modulo in modulos:
            modulo.consultar_llm = original

    assert llamadas == [
        "climatico", "diagnostico", "bioseguridad", "moderador",  # ronda 1: todos
        "diagnostico", "moderador",                                 # debate: solo el consultado
    ], llamadas
    # los que no fueron consultados conservan su resultado de la ronda 1
    assert set(resultado["resultados"]) == {"climatico", "diagnostico", "bioseguridad"}
    assert resultado["revisiones"] == 2
# ---------- pruebas del buscador (RAG) ----------
# No usan chroma_db/ ni el modelo de embeddings: arman una coleccion en memoria
# con pocos pedazos y un embedding falso, asi corren en cualquier PC.

class EmbeddingFalso(EmbeddingFunction):
    """Vectores fijos y baratos: alcanza para probar filtros y cantidades."""

    def __init__(self):
        pass

    def __call__(self, input):
        return [[float(len(t) % 7), 1.0, float(t.count("a"))] for t in input]

    @staticmethod
    def name():
        return "embedding_falso"

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return EmbeddingFalso()


PEDAZOS = [
    ("PRRS en cerdos: ELISA en fluidos orales", "porcino"),
    ("Síndrome reproductivo y respiratorio porcino", "porcino"),
    ("Vigilancia de PRRS en granjas de cerdos", "porcino"),
    ("Influenza aviar H5N1 en aves de corral", "aviar"),
    ("RT-PCR para influenza aviar", "aviar"),
    ("Toma y envío de muestras al laboratorio", "general"),
]


def con_buscador_de_prueba(prueba):
    """Corre `prueba` con buscar.py apuntando a una coleccion en memoria y la deja como estaba."""
    cliente_original, ef_original = buscar._cliente, buscar._ef
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        col = buscar._cliente.get_or_create_collection("prueba_md", embedding_function=buscar._ef)
        col.add(ids=[f"P{i}" for i in range(len(PEDAZOS))], documents=[t for t, _ in PEDAZOS],
                metadatas=[{"doc_id": f"P{i}", "enfermedad": e, "titulo": t} for i, (t, e) in enumerate(PEDAZOS)])
        prueba()
    finally:
        buscar._cliente.delete_collection("prueba_md")
        buscar._cliente, buscar._ef = cliente_original, ef_original


def prueba_filtro_excluir_porcino():
    def revisar():
        sin = buscar.buscar_crudo("prueba_md", "PRRS en cerdos", k=6)
        assert sin and all(p["enfermedad"] != "porcino" for p in sin), "devolvió pedazos porcinos"
        con = buscar.buscar_crudo("prueba_md", "PRRS en cerdos", k=6, excluir_porcino=False)
        assert any(p["enfermedad"] == "porcino" for p in con), "sin el filtro deberían aparecer porcinos"
    con_buscador_de_prueba(revisar)


def prueba_buscar_maximo_k():
    def revisar():
        for k in (1, 2, 3, 50):  # 50 es mas que los pedazos que hay
            texto = buscar.buscar("prueba_md", "influenza aviar", k=k, excluir_porcino=False)
            assert len(texto.split("\n\n---\n\n")) <= k, f"buscar() devolvió más de {k}"
            assert len(buscar.buscar_crudo("prueba_md", "influenza aviar", k=k)) <= k
    con_buscador_de_prueba(revisar)


# ---------- pruebas de la evidencia que reciben los agentes ----------

CASO_AVIAR = {"especie": "aves de corral", "signos_clinicos": ["mortalidad súbita"],
              "resultados_laboratorio": ["RT-PCR positivo a H5N1"]}


def prueba_recortar_fragmento():
    assert base.recortar("texto corto", 50) == "texto corto"
    # corta en el ultimo espacio: no deja "influ" a medias
    assert base.recortar("influenza aviar altamente patogena", 20) == "influenza aviar […]"
    # una sola palabra mas larga que el limite se corta igual
    assert base.recortar("a" * 30, 10) == "a" * 10 + " […]"
    largo = [{"doc_id": "X", "texto": "palabra " * 500}]
    assert len(base.texto_para_prompt(largo)) < len(buscar.como_texto(largo))


def prueba_contexto_rag_sin_coleccion():
    # sin chroma_db (o sin la coleccion) el agente sigue, con SIN_CONTEXTO y sin evidencia
    cliente_original = buscar._cliente
    buscar._cliente = chromadb.EphemeralClient()  # vacio: no existe climatico_md
    try:
        assert base.contexto_rag("climatico", CASO_AVIAR) == (base.SIN_CONTEXTO, [])
    finally:
        buscar._cliente = cliente_original


def prueba_evidencia_y_fuentes_reales():
    # los tres agentes buscan en la coleccion de prueba con un caso aviar
    def revisar():
        colecciones_originales = dict(base.COLECCIONES)
        base.COLECCIONES.update({a: "prueba_md" for a in base.COLECCIONES})
        try:
            app = construir_grafo(InMemorySaver())
            config = {"configurable": {"thread_id": "evidencia"}}
            estado = estado_inicial() | {"caso": CASO_AVIAR}
            resultado = app.invoke(estado, config)
        finally:
            base.COLECCIONES.clear()
            base.COLECCIONES.update(colecciones_originales)

        no_porcinos = {f"P{i}" for i, (_, e) in enumerate(PEDAZOS) if e != "porcino"}
        for agente, salida in resultado["resultados"].items():
            ids = [e["doc_id"] for e in salida["evidencia"]]
            assert ids and len(ids) <= K_FRAGMENTOS, f"{agente}: {ids}"
            assert set(ids) <= no_porcinos, f"{agente} recibió documentos porcinos: {ids}"
        # los "fuentes" simulados se reemplazan por los doc_id recuperados
        clima = resultado["resultados"]["climatico"]
        assert clima["factores_de_riesgo"][0]["fuentes"] == [e["doc_id"] for e in clima["evidencia"]]
        # el informe cita la union de la evidencia de los tres agentes, sin repetir
        citados = [f["doc_id"] for f in resultado["evaluacion"]["informe"]["fuentes_utilizadas"]]
        todos = {e["doc_id"] for s in resultado["resultados"].values() for e in s["evidencia"]}
        assert sorted(citados) == sorted(todos)
    con_buscador_de_prueba(revisar)


# ---------- pruebas de la revision humana por terminal (main.py) ----------

def respuestas(*textos):
    # reemplaza a input(): devuelve las respuestas en orden
    pendientes = list(textos)
    return lambda _pregunta: pendientes.pop(0)


def prueba_main_rechazo_pide_comentario():
    assert pedir_decision(respuestas("s")) == APROBAR
    # vacio y solo espacios no sirven: vuelve a pedir hasta que haya texto
    decision = pedir_decision(respuestas("n", "", "   ", " falta humedad "))
    assert decision == {"aprobado": False, "comentarios": "falta humedad"}


# ---------- pruebas de la demo con el modo de pruebas de Streamlit (sin navegador) ----------

APP_DEMO = pathlib.Path(__file__).resolve().parents[1] / "app_demo.py"


def con_demo(prueba):
    """Corre `prueba(at)` con la demo cargada y Chroma vacio (los agentes siguen sin contexto RAG)."""
    from streamlit.testing.v1 import AppTest
    cliente_original = buscar._cliente
    buscar._cliente = chromadb.EphemeralClient()
    try:
        at = AppTest.from_file(str(APP_DEMO), default_timeout=120)
        at.run()
        prueba(at)
    finally:
        buscar._cliente = cliente_original


def apretar(at, etiqueta):
    boton = next(b for b in at.button if b.label == etiqueta)
    boton.click().run()
    assert not at.exception, at.exception


def hay_boton(at, etiqueta):
    return any(b.label == etiqueta for b in at.button)


def avisos(at):
    return [w.value for w in at.warning]


def errores(at):
    return [e.value for e in at.error]


def con_llm_que_falla(modulo, error, accion):
    # el agente usa el consultar_llm que importo; se reemplaza solo mientras corre accion()
    original = modulo.consultar_llm

    def falla(*_):
        raise error

    modulo.consultar_llm = falla
    try:
        accion()
    finally:
        modulo.consultar_llm = original


def prueba_demo_rechazo_sin_comentario():
    def revisar(at):
        apretar(at, "Analizar")
        pausa = at.session_state["resultado"]["__interrupt__"]
        assert hay_boton(at, "Aprobar") and hay_boton(at, "Rechazar")
        # vacio y solo espacios: aviso, y el grafo no se reanuda
        for texto in ("", "   "):
            at.text_area(key="comentarios").input(texto)
            apretar(at, "Rechazar")
            assert "Escribe un comentario para que el moderador sepa qué corregir." in avisos(at)
            assert at.session_state["resultado"]["__interrupt__"] == pausa, "no debía reanudar"
        # con comentario: el moderador lo recibe y vuelve a pedir revision
        at.text_area(key="comentarios").input("falta informacion de humedad")
        apretar(at, "Rechazar")
        resultado = at.session_state["resultado"]
        assert resultado["decision_humana"] == {"aprobado": False, "comentarios": "falta informacion de humedad"}
        assert "__interrupt__" in resultado and resultado["revisiones"] == 2
        # aprobar no necesita comentario
        apretar(at, "Aprobar")
        assert "__interrupt__" not in at.session_state["resultado"]
        assert at.session_state["resultado"]["decision_humana"]["aprobado"] is True
    con_demo(revisar)


def prueba_demo_error_de_conexion():
    pedido = httpx.Request("POST", "http://localhost:1234/v1/chat/completions")

    def revisar(at):
        con_llm_que_falla(climatico, openai.APIConnectionError(request=pedido), lambda: apretar(at, "Analizar"))
        assert any("servidor de LM Studio" in e for e in errores(at)), errores(at)
        assert not hay_boton(at, "Aprobar") and hay_boton(at, "Reintentar")
        # con el LLM de vuelta, Reintentar sigue desde el agente que fallo
        apretar(at, "Reintentar")
        assert not errores(at) and "__interrupt__" in at.session_state["resultado"]
        assert hay_boton(at, "Aprobar")
    con_demo(revisar)


def prueba_demo_error_de_contexto_al_reanudar():
    respuesta = httpx.Response(400, request=httpx.Request("POST", "http://localhost:1234/v1/chat/completions"))
    error = openai.BadRequestError("Context size has been exceeded.", response=respuesta, body=None)

    def revisar(at):
        apretar(at, "Analizar")
        at.text_area(key="comentarios").input("falta humedad")
        con_llm_que_falla(moderador, error, lambda: apretar(at, "Rechazar"))
        assert any("Context Length" in e and "Max Concurrent Predictions" in e for e in errores(at)), errores(at)
        # el grafo quedo en el moderador: aprobar ahora se perderia, solo se ofrece reintentar
        assert not hay_boton(at, "Aprobar") and hay_boton(at, "Reintentar")
        apretar(at, "Reintentar")
        resultado = at.session_state["resultado"]
        assert not errores(at) and "__interrupt__" in resultado
        assert resultado["decision_humana"]["comentarios"] == "falta humedad"
    con_demo(revisar)


if __name__ == "__main__":
    pruebas = [
        prueba_moderador_informe_final,
        prueba_moderador_pide_debate,
        prueba_moderador_llega_al_maximo,
        prueba_moderador_agente_invalido,
        prueba_humano_aprueba,
        prueba_humano_rechaza,
        prueba_flujo_normal,
        prueba_rechazo_y_aprobacion,
        prueba_debate_termina,
        prueba_debate_solo_llama_al_consultado,
        prueba_filtro_excluir_porcino,
        prueba_buscar_maximo_k,
        prueba_recortar_fragmento,
        prueba_contexto_rag_sin_coleccion,
        prueba_evidencia_y_fuentes_reales,
        prueba_main_rechazo_pide_comentario,
        prueba_demo_rechazo_sin_comentario,
        prueba_demo_error_de_conexion,
        prueba_demo_error_de_contexto_al_reanudar,
    ]
    for prueba in pruebas:
        prueba()
        print(f"OK  {prueba.__name__}")

    print(f"\n{len(pruebas)} pruebas pasaron")