import copy
import json
import pathlib

import chromadb
import httpx
import openai
import requests
from chromadb.api.types import EmbeddingFunction
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END
from langgraph.types import Command

from vet_agent import llm, prompts
from vet_agent.agentes import base, climatico, diagnostico, bioseguridad, moderador
from vet_agent.config import MAX_RONDAS, K_FRAGMENTOS
from vet_agent.datos import clima
from vet_agent.datos.planteles import buscar_planteles
from vet_agent.grafo import construir_grafo, despues_del_moderador, despues_de_revision_humana
from vet_agent.main import pedir_decision
from vet_agent.rag import buscar, cabecera, chunking, limpiar_texto

# las pruebas siempre usan respuestas de ejemplo, aunque el .env diga otra cosa
llm.USAR_SIMULACION = True

APROBAR = {"aprobado": True, "comentarios": ""}
RECHAZAR = {"aprobado": False, "comentarios": "falta informacion de humedad"}

# Caso minimo para probar el flujo del grafo (las respuestas del LLM son simuladas). Es de
# cerdos a proposito: con especie "porcino" los agentes buscan SIN el filtro de documentos
# porcinos, y el camino CON filtro lo cubren las pruebas que usan un caso aviar.
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


def ejecutar(decisiones, con_chroma_real=False, caso=None):
    # corre el grafo entero y va respondiendo cada pausa humana en orden.
    # Por defecto busca la evidencia en una coleccion en memoria: sin chroma_db
    # ni modelo de embeddings, asi las pruebas corren igual en cualquier PC.
    def correr():
        app = construir_grafo(InMemorySaver())
        config = {"configurable": {"thread_id": "prueba"}}
        inicial = estado_inicial() if caso is None else estado_inicial() | {"caso": caso}
        resultado = app.invoke(inicial, config)

        for decision in decisiones:
            assert "__interrupt__" in resultado, "se esperaba una pausa para revision humana"
            resultado = app.invoke(Command(resume=decision), config)

        assert "__interrupt__" not in resultado, "el grafo no termino"
        return resultado

    if con_chroma_real:
        return correr()
    return con_buscador_de_prueba(correr, usar_colecciones=True)


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


def prueba_plantillas_de_salida_son_json_valido():
    # un modelo chico copia la plantilla de SALIDA tal cual: si trae texto fuera de
    # comillas (ej. "(máx. 5 ítems)") la respuesta sale como JSON invalido
    def objetos_json(texto):
        salida = texto.split("SALIDA (JSON)", 1)[1]
        objetos, nivel, inicio = [], 0, 0
        for i, c in enumerate(salida):
            if c == "{":
                if nivel == 0:
                    inicio = i
                nivel += 1
            elif c == "}":
                nivel -= 1
                if nivel == 0:
                    objetos.append(salida[inicio:i + 1])
        return objetos

    for plantilla in (prompts.PROMPT_CLIMATICO, prompts.PROMPT_DIAGNOSTICO, prompts.PROMPT_BIOSEGURIDAD,
                      prompts.PROMPT_MODERADOR, prompts.PROMPT_UNICO):
        objetos = objetos_json(plantilla)
        assert objetos
        for objeto in objetos:
            json.loads(objeto)
    # el informe de B1/B2 sale de partir el prompt del moderador
    assert json.loads(prompts.FORMATO_INFORME)["estado"] == "informe_final"


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


def con_buscador_de_prueba(prueba, usar_colecciones=False):
    """Corre `prueba` con buscar.py apuntando a una coleccion en memoria y la deja como estaba.

    Con usar_colecciones=True tambien redirige base.COLECCIONES a "prueba_md",
    que es lo que necesitan las pruebas que corren el grafo completo.
    """
    cliente_original, ef_original = buscar._cliente, buscar._ef
    colecciones_originales = dict(base.COLECCIONES)
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        col = buscar._cliente.get_or_create_collection("prueba_md", embedding_function=buscar._ef)
        col.add(ids=[f"P{i}" for i in range(len(PEDAZOS))], documents=[t for t, _ in PEDAZOS],
                metadatas=[{"doc_id": f"P{i}", "chunk": 0, "enfermedad": e, "titulo": t,
                            "institucion": "prueba", "anio": "2026"} for i, (t, e) in enumerate(PEDAZOS)])
        if usar_colecciones:
            base.COLECCIONES.update({a: "prueba_md" for a in base.COLECCIONES})
        return prueba()
    finally:
        buscar._cliente.delete_collection("prueba_md")
        buscar._cliente, buscar._ef = cliente_original, ef_original
        base.COLECCIONES.clear()
        base.COLECCIONES.update(colecciones_originales)


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


def prueba_como_texto_arma_la_cabecera():
    pedazos = [
        {"doc_id": "D1", "institucion": "SAG", "anio": "2024", "titulo": "Influenza aviar", "texto": "texto uno"},
        {"doc_id": "D2", "texto": "texto dos"},
    ]
    partes = buscar.como_texto(pedazos).split("\n\n---\n\n")
    assert len(partes) == 2
    assert partes[0] == "[D1] SAG 2024 — Influenza aviar\ntexto uno"
    assert partes[1].startswith("[D2]") and "texto dos" in partes[1]
    assert buscar.como_texto([]) == ""


# ---------- pruebas de los modulos de vet_agent/rag/ ----------

def prueba_chunking_no_deja_pedazos_vacios_ni_pasados():
    assert chunking.cortar("") == []
    assert chunking.cortar("   \n\n  ") == []
    palabras = [f"palabra{i}" for i in range(2000)]
    texto = "\n\n".join(" ".join(palabras[i:i + 40]) for i in range(0, len(palabras), 40))
    pedazos = chunking.cortar(texto)
    assert len(pedazos) > 1
    assert all(p.strip() for p in pedazos)
    assert all(len(p) <= chunking.MAX_CHARS + chunking.SOLAPE + 2 for p in pedazos)


def prueba_chunking_no_corta_palabras():
    palabras = [f"w{i:04d}" for i in range(500)]
    pedazos = chunking.cortar(" ".join(palabras), max_chars=200, solape=40)
    vocabulario = set(palabras)
    for pedazo in pedazos:
        assert set(pedazo.split()) <= vocabulario, pedazo
    # el solape repite texto, pero no se pierde ninguna palabra
    assert vocabulario <= set(" ".join(pedazos).split())


def prueba_chunking_parte_palabra_muy_larga():
    palabra = "x" * 250
    pedazos = chunking.cortar(palabra, max_chars=100, solape=20)
    assert all(len(p) <= 100 for p in pedazos)
    assert "".join(pedazos) == palabra


def prueba_chunking_md_repite_el_titulo_en_las_continuaciones():
    seccion = "# Titulo\n\n" + " ".join(f"palabra{i}" for i in range(80))
    pedazos = chunking.cortar_md("# Chico\n\nhola\n\n" + seccion, max_chars=150, solape=30)
    assert len(pedazos) > 1
    assert pedazos[0].startswith("# Chico")
    assert any(p.startswith("# Titulo") for p in pedazos[1:]), pedazos
    assert all(len(p) <= 200 for p in pedazos)


def prueba_cabecera_ida_y_vuelta():
    meta = {"id": "D23", "titulo": 'Comparación: "aves" y cerdos', "anio": "2025"}
    texto = cabecera.escribir(meta, "cuerpo\ncon dos lineas")
    assert cabecera.separar(texto) == (meta, "cuerpo\ncon dos lineas\n")
    assert cabecera.separar("sin cabecera") == ({}, "sin cabecera")


def prueba_limpiar_texto_quita_ruido_y_conserva_titulos():
    cuerpo = "\n".join([
        "# Seccion importante",
        "Texto cientifico que se mantiene.",
        "1",
        "Downloaded from https://ejemplo.cl",
        "Aceptar todas las cookies",
        "| columna | dato |",
        "",
        "# Resultados",
        "Resultado principal.",
        "",
        "References",
        "Autor, A. 2020. Un articulo.",
    ])
    limpio = limpiar_texto.limpiar(cuerpo)
    assert "# Seccion importante" in limpio
    assert "# Resultados" in limpio
    assert "Resultado principal." in limpio
    assert "| columna | dato |" in limpio
    assert "References" not in limpio and "Autor, A." not in limpio
    assert "Downloaded from" not in limpio and "cookies" not in limpio
    assert "\n1\n" not in limpio


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
    cliente_original, ef_original = buscar._cliente, buscar._ef
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        assert base.contexto_rag("climatico", CASO_AVIAR) == (base.SIN_CONTEXTO, [])
    finally:
        buscar._cliente, buscar._ef = cliente_original, ef_original


def prueba_evidencia_y_fuentes_reales():
    # los tres agentes buscan en la coleccion de prueba con un caso aviar
    def revisar():
        app = construir_grafo(InMemorySaver())
        config = {"configurable": {"thread_id": "evidencia"}}
        estado = estado_inicial() | {"caso": CASO_AVIAR}
        resultado = app.invoke(estado, config)

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
    con_buscador_de_prueba(revisar, usar_colecciones=True)


COLECCIONES_REALES = ("climatico_md", "diagnostico_md", "bioseguridad_md")


def prueba_integracion_chroma_real():
    """Unica prueba que usa el chroma_db y los embeddings de verdad.

    Si no esta indexada alguna de las tres colecciones, avisa que se omitio
    en vez de pasar en silencio (los resultados dependerian de cada PC).
    """
    faltantes = []
    for nombre in COLECCIONES_REALES:
        try:
            col = buscar._cliente.get_collection(nombre, embedding_function=buscar._embedding())
        except Exception as e:
            faltantes.append(f"{nombre} ({e})")
            continue
        if col.count() == 0:
            faltantes.append(f"{nombre} (vacia)")
    if faltantes:
        return "falta indexar " + ", ".join(faltantes) + " en chroma_db/"

    resultado = ejecutar([APROBAR], con_chroma_real=True, caso=CASO_AVIAR)
    for agente, salida in resultado["resultados"].items():
        assert salida["evidencia"], f"{agente} no recuperó evidencia del chroma_db real"
    # el informe cita documentos que existen en la base
    citados = [f["doc_id"] for f in resultado["evaluacion"]["informe"]["fuentes_utilizadas"]]
    assert citados, "el informe quedaría sin fuentes"
    assert set(citados) == {e["doc_id"] for s in resultado["resultados"].values() for e in s["evidencia"]}


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


def sin_internet(url, params):
    raise requests.ConnectionError("sin internet (prueba)")


def con_demo(prueba, internet=sin_internet):
    """Corre `prueba(at)` con la demo cargada y Chroma vacio (los agentes siguen sin contexto RAG).

    Por defecto Open-Meteo no responde (el mapa de la comuna no aparece): asi las pruebas no
    dependen de internet. `internet` reemplaza a clima._get para simular otra respuesta.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    cliente_original, ef_original, get_original = buscar._cliente, buscar._ef, clima._get
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    clima._get = internet
    st.cache_data.clear()  # el mapa guarda las coordenadas: que no queden de otra prueba
    try:
        at = AppTest.from_file(str(APP_DEMO), default_timeout=120)
        at.run()
        prueba(at)
    finally:
        buscar._cliente, buscar._ef, clima._get = cliente_original, ef_original, get_original
        st.cache_data.clear()


def mapas(at):
    # st.map se dibuja como un grafico deck.gl
    return at.get("deck_gl_json_chart")


def apretar(at, etiqueta):
    boton = next(b for b in at.button if b.label == etiqueta)
    boton.click().run()
    assert not at.exception, at.exception


def hay_boton(at, etiqueta):
    return any(b.label == etiqueta for b in at.button)


def hay_descarga(at, etiqueta):
    # st.download_button es un tipo de elemento aparte de st.button en AppTest
    return any(b.label == etiqueta for b in at.download_button)


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
        # en el diagrama queda marcado el agente que fallo
        assert at.session_state["grafo_vista"]["nodos"]["climatico"]["estado"] == "error"
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


def prueba_api_endpoints():
    import tempfile

    from fastapi.testclient import TestClient
    from vet_agent import api
    from vet_agent.main import CASO_H5N1

    parte_a = {"especie": "aves"}
    escenario = {
        "parte_a_entrada": parte_a,
        "parte_b_esperada": {"diagnostico": "dato de prueba"},
    }

    carpeta_original = api.CARPETA_ESCENARIOS

    with tempfile.TemporaryDirectory() as temporal:
        api.CARPETA_ESCENARIOS = pathlib.Path(temporal)

        try:
            (api.CARPETA_ESCENARIOS / "IAAP-00.json").write_text(
                json.dumps(escenario, ensure_ascii=False),
                encoding="utf-8",
            )

            with TestClient(api.app) as cliente:
                lista = cliente.get("/escenarios")
                assert lista.status_code == 200
                assert lista.json() == ["IAAP-00"]

                detalle = cliente.get("/escenarios/IAAP-00")
                assert detalle.status_code == 200
                assert detalle.json() == parte_a  # No expone la Parte B.

                analisis = cliente.post("/analizar", json=CASO_H5N1)
                assert analisis.status_code == 200, analisis.text
                assert isinstance(analisis.json().get("informe"), dict)
                assert isinstance(analisis.json().get("fuentes"), list)
        finally:
            api.CARPETA_ESCENARIOS = carpeta_original

# ---------- prueba de la carga de escenarios (T2) ----------

def prueba_cargar_escenarios_ejemplo():
    # reproduce la fila de ejemplo (IAAP-00) de la plantilla de Investigación
    # y revisa que se convierta al JSON con la Parte A y la Parte B separadas
    import openpyxl
    from vet_agent.datos.cargar_escenarios import escenarios_de_hoja

    cabecera = [
        "id_escenario", "titulo", "version", "autor", "tipo",
        "parte_a_entrada.especie", "parte_a_entrada.sistema_productivo",
        "parte_a_entrada.region", "parte_a_entrada.comuna", "parte_a_entrada.fecha_evento",
        "parte_a_entrada.n_animales", "parte_a_entrada.signos_clinicos",
        "parte_a_entrada.mortalidad_pct", "parte_a_entrada.morbilidad_pct",
        "parte_a_entrada.resultados_laboratorio_1.prueba",
        "parte_a_entrada.resultados_laboratorio_1.resultado",
        "parte_a_entrada.resultados_laboratorio_1.fecha",
        "parte_a_entrada.resultados_laboratorio_2.prueba",
        "parte_a_entrada.resultados_laboratorio_2.resultado",
        "parte_a_entrada.resultados_laboratorio_2.fecha",
        "parte_a_entrada.clima.temperatura_media_c",
        "parte_a_entrada.clima.humedad_relativa_pct",
        "parte_a_entrada.clima.precipitacion_mm",
        "parte_a_entrada.aves_silvestres_cercanas",
        "parte_a_entrada.historial_brotes_zona",
        "parte_a_entrada.movimientos_animales",
        "parte_a_entrada.indicadores_productivos",
        "parte_b_referencia.nivel_riesgo",
        "parte_b_referencia.documentos_esperados",
        "tipo_caso",
    ]
    fila = [
        "EJEMPLO IAAP-00", "Brote sospechoso IAAP en plantel de ponedoras", "borrador_1", "Nombre del autor", "simulado",
        "aves", "ponedoras comerciales",
        "Valparaíso", "(comuna)", "2026-10-20",
        50000, "mortalidad súbita; edema facial",
        2.5, 8,
        "PCR", "positivo", "2026-10-22",
        None, None, None,
        14.2, 78, 3.2,
        "humedal a 3 km con aves migratorias", None, None, None,
        "alto", "C21; C45",
        "concordante",
    ]

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.title = "Escenarios"
    hoja.append(cabecera)
    hoja.append(fila)

    escenarios = escenarios_de_hoja(hoja)
    ej = next(e for e in escenarios if e["id_escenario"] == "IAAP-00")

    # id normalizado y metadatos
    assert ej["id_escenario"] == "IAAP-00"
    assert ej["titulo"] == "Brote sospechoso IAAP en plantel de ponedoras"

    # Parte A: anidación, números y listas
    a = ej["parte_a_entrada"]
    assert a["especie"] == "aves"
    assert a["n_animales"] == 50000
    assert a["mortalidad_pct"] == 2.5
    assert a["signos_clinicos"] == ["mortalidad súbita", "edema facial"]
    assert a["clima"]["temperatura_media_c"] == 14.2
    assert a["resultados_laboratorio"] == [
        {"prueba": "PCR", "resultado": "positivo", "fecha": "2026-10-22"}
    ]

    # Parte B: listas separadas por ";"
    b = ej["parte_b_referencia"]
    assert b["nivel_riesgo"] == "alto"
    assert b["documentos_esperados"] == ["C21", "C45"]

    # celdas vacías -> None y la columna tipo_caso se ignora
    assert a["movimientos_animales"] is None
    assert "tipo_caso" not in ej

# ---------- pruebas del registro de corridas (evaluar.py, formato E1 v6) ----------

CASO_EVALUAR = {"especie": "aves", "signos_clinicos": ["mortalidad súbita"],
                "resultados_laboratorio": [{"prueba": "RT-PCR", "resultado": "positivo", "fecha": "2026-10-22"}]}


def correr_evaluar(escenarios):
    # corre evaluar() con el buscador en memoria y el registro en un archivo temporal;
    # devuelve (texto del CSV, filas leidas del CSV)
    import csv
    import io
    import tempfile
    from vet_agent import evaluar

    with tempfile.TemporaryDirectory() as temporal:
        registro = pathlib.Path(temporal) / "registro.csv"
        con_buscador_de_prueba(lambda: evaluar.evaluar(escenarios, 1, registro), usar_colecciones=True)
        texto = registro.read_text(encoding="utf-8")
    return texto, list(csv.DictReader(io.StringIO(texto)))


def prueba_evaluar_registro_formato():
    import re
    from vet_agent import evaluar
    from vet_agent.prompts import VERSION_PROMPTS

    reintentos = llm.LLM_REINTENTOS
    texto, filas = correr_evaluar([("IAAP-T1", CASO_EVALUAR)])
    assert texto.splitlines()[0] == ",".join(f'"{c}"' for c in evaluar.COLUMNAS_REGISTRO)  # orden y comillas
    assert [f["baseline"] for f in filas] == ["B1", "B2", "B3"]
    for f in filas:
        assert re.fullmatch(r"B[123]-IAAP-T1-R1-I1", f["id_corrida"]), f["id_corrida"]
        assert f["estado"] == "ok" and f["intento"] == "1"
        assert f["entrada"] == "IAAP-T1"
        assert f["version_prompt_base"] == VERSION_PROMPTS
    # salida = solo el informe, con las mismas claves en las 3 versiones
    salidas = [json.loads(f["salida"]) for f in filas]
    assert "estado" not in salidas[0] and "hipotesis_alternativas" in salidas[0]
    assert all(s.keys() == salidas[0].keys() for s in salidas)
    # fragmentos: vacio en B1 y doc_id_numero separados por ";" en B2 y B3
    assert filas[0]["fragmentos"] == ""
    for f in filas[1:]:
        assert re.fullmatch(r"[\w-]+_\d+(;[\w-]+_\d+)*", f["fragmentos"]), f["fragmentos"]
    assert llm.LLM_REINTENTOS == reintentos, "evaluar debe dejar LLM_REINTENTOS como estaba"


def prueba_evaluar_reintenta_si_falla():
    from vet_agent import evaluar

    reintentos_vistos = []

    def falla_la_primera_vez(caso):
        reintentos_vistos.append(llm.LLM_REINTENTOS)
        if len(reintentos_vistos) == 1:
            raise ValueError("[moderador] la respuesta no es JSON valido en 1 intento(s)")
        return evaluar.llm_sin_rag(caso)

    originales = dict(evaluar.BASELINES)
    evaluar.BASELINES.clear()
    evaluar.BASELINES["B1"] = ("llm_sin_rag", falla_la_primera_vez)
    try:
        _, filas = correr_evaluar([("IAAP-T1", CASO_EVALUAR)])
    finally:
        evaluar.BASELINES.clear()
        evaluar.BASELINES.update(originales)

    assert [(f["id_corrida"], f["estado"]) for f in filas] == [("B1-IAAP-T1-R1-I1", "error"), ("B1-IAAP-T1-R1-I2", "ok")]
    assert "JSON valido" in filas[0]["salida"]
    assert reintentos_vistos == [0, 0], "durante la evaluacion consultar_llm no debe reintentar por su cuenta"


def prueba_evaluar_cache_reintento_b3_no_repite_agentes():
    """Si el moderador falla en el intento 1 de B3, en el intento 2 los otros 3
    agentes no vuelven a llamar al LLM (reutilizan la respuesta cacheada)."""
    import sys
    from vet_agent import evaluar
    from vet_agent.main import CASO_H5N1

    llamadas = {"climatico": 0, "diagnostico": 0, "bioseguridad": 0, "moderador": 0}
    original = llm.consultar_llm
    moderador_fallos = [0]

    def mock_consultar(instrucciones, pregunta, agente):
        llamadas[agente] = llamadas.get(agente, 0) + 1
        if agente == "moderador":
            moderador_fallos[0] += 1
            if moderador_fallos[0] == 1:
                raise ValueError("[moderador] falla simulada en intento 1")
        return json.dumps(llm.RESPUESTAS_SIMULADAS[agente], ensure_ascii=False)

    def parchear(fn):
        llm.consultar_llm = fn
        # vars() para no disparar la carga perezosa de librerias como transformers
        for m in list(sys.modules.values()):
            if m is not None and "consultar_llm" in vars(m):
                m.consultar_llm = fn

    parchear(mock_consultar)
    # usar el buscador falso y el cache
    from vet_agent.rag import buscar as buscar_mod
    import chromadb
    cli_o, ef_o = buscar_mod._cliente, buscar_mod._ef
    col_o = dict(base.COLECCIONES)
    buscar_mod._cliente, buscar_mod._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        col = buscar_mod._cliente.get_or_create_collection("prueba_md", embedding_function=buscar_mod._ef)
        col.add(ids=[f"P{i}" for i in range(len(PEDAZOS))], documents=[t for t,_ in PEDAZOS],
                metadatas=[{"doc_id":f"P{i}","enfermedad":e,"titulo":t} for i,(t,e) in enumerate(PEDAZOS)])
        base.COLECCIONES.update({a:"prueba_md" for a in base.COLECCIONES})

        with evaluar.con_cache_respuestas():
            # intento 1: falla el moderador
            try:
                evaluar.multiagente(CASO_H5N1)
            except Exception:
                pass
            llamadas_intento1 = dict(llamadas)
            # intento 2: debe reutilizar 3 agentes, moderador se llama de nuevo
            try:
                evaluar.multiagente(CASO_H5N1)
            except Exception:
                pass
            llamadas_intento2 = dict(llamadas)
    finally:
        buscar_mod._cliente.delete_collection("prueba_md")
        buscar_mod._cliente, buscar_mod._ef = cli_o, ef_o
        base.COLECCIONES.clear(); base.COLECCIONES.update(col_o)
        parchear(original)

    # intento 1: los 3 agentes + moderador (falla)
    assert llamadas_intento1["climatico"] == 1
    assert llamadas_intento1["diagnostico"] == 1
    assert llamadas_intento1["bioseguridad"] == 1
    assert llamadas_intento1["moderador"] == 1
    # intento 2: solo el moderador vuelve a llamar
    assert llamadas_intento2["climatico"] == 1, llamadas_intento2
    assert llamadas_intento2["diagnostico"] == 1, llamadas_intento2
    assert llamadas_intento2["bioseguridad"] == 1, llamadas_intento2
    assert llamadas_intento2["moderador"] == 2, llamadas_intento2


def prueba_evaluar_cache_repeticion_nueva_vuelve_a_llamar():
    """Una repeticion nueva usa un cache nuevo: vuelve a llamar a todos los agentes."""
    import sys
    from vet_agent import evaluar
    from vet_agent.main import CASO_H5N1

    llamadas = {"climatico": 0, "diagnostico": 0, "bioseguridad": 0, "moderador": 0}
    original = llm.consultar_llm

    def mock_consultar(instrucciones, pregunta, agente):
        llamadas[agente] = llamadas.get(agente, 0) + 1
        return json.dumps(llm.RESPUESTAS_SIMULADAS[agente], ensure_ascii=False)

    def parchear(fn):
        llm.consultar_llm = fn
        # vars() para no disparar la carga perezosa de librerias como transformers
        for m in list(sys.modules.values()):
            if m is not None and "consultar_llm" in vars(m):
                m.consultar_llm = fn

    from vet_agent.rag import buscar as buscar_mod
    import chromadb
    cli_o, ef_o = buscar_mod._cliente, buscar_mod._ef
    col_o = dict(base.COLECCIONES)
    buscar_mod._cliente, buscar_mod._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    parchear(mock_consultar)
    try:
        col = buscar_mod._cliente.get_or_create_collection("prueba_md", embedding_function=buscar_mod._ef)
        col.add(ids=[f"P{i}" for i in range(len(PEDAZOS))], documents=[t for t,_ in PEDAZOS],
                metadatas=[{"doc_id":f"P{i}","enfermedad":e,"titulo":t} for i,(t,e) in enumerate(PEDAZOS)])
        base.COLECCIONES.update({a:"prueba_md" for a in base.COLECCIONES})

        # combinacion 1 (cache propio)
        with evaluar.con_cache_respuestas():
            evaluar.multiagente(CASO_H5N1)
        llamadas_r1 = dict(llamadas)
        # combinacion 2 = repeticion nueva (cache nuevo)
        with evaluar.con_cache_respuestas():
            evaluar.multiagente(CASO_H5N1)
        llamadas_r2 = dict(llamadas)
    finally:
        buscar_mod._cliente.delete_collection("prueba_md")
        buscar_mod._cliente, buscar_mod._ef = cli_o, ef_o
        base.COLECCIONES.clear(); base.COLECCIONES.update(col_o)
        parchear(original)

    assert llamadas_r1["climatico"] == 1 and llamadas_r1["moderador"] == 1
    # todos vuelven a llamar en la repeticion nueva
    assert llamadas_r2["climatico"] == 2, llamadas_r2
    assert llamadas_r2["diagnostico"] == 2, llamadas_r2
    assert llamadas_r2["bioseguridad"] == 2, llamadas_r2
    assert llamadas_r2["moderador"] == 2, llamadas_r2


def prueba_evaluar_tiempos_por_caso():
    # B3 falla la primera vez: su tiempo del caso suma los 2 intentos y el archivo
    # de tiempos queda junto al registro (aca, en la carpeta temporal)
    import csv
    import tempfile
    from vet_agent import evaluar

    llamadas_b3 = []

    def b3_falla_la_primera_vez(caso):
        llamadas_b3.append(1)
        if len(llamadas_b3) == 1:
            raise ValueError("[moderador] la respuesta se cortó al llegar a max_tokens (3000)")
        return evaluar.multiagente(caso)

    originales = dict(evaluar.BASELINES)
    evaluar.BASELINES["B3"] = ("multiagente", b3_falla_la_primera_vez)
    try:
        with tempfile.TemporaryDirectory() as temporal:
            registro = pathlib.Path(temporal) / "registro.csv"
            con_buscador_de_prueba(lambda: evaluar.evaluar([("IAAP-T1", CASO_EVALUAR)], 1, registro),
                                   usar_colecciones=True)
            with open(registro, encoding="utf-8") as f:
                intentos = list(csv.DictReader(f))
            with open(registro.with_name(evaluar.ARCHIVO_TIEMPOS), encoding="utf-8") as f:
                texto = f.read()
                f.seek(0)
                tiempos = list(csv.DictReader(f))
    finally:
        evaluar.BASELINES.clear()
        evaluar.BASELINES.update(originales)

    assert texto.splitlines()[0] == ",".join(f'"{c}"' for c in evaluar.COLUMNAS_TIEMPOS)  # orden y comillas
    assert len(tiempos) == 1, "una fila por caso (escenario x repeticion)"
    t = tiempos[0]
    assert (t["escenario"], t["repeticion"]) == ("IAAP-T1", "1")
    assert (t["intentos_B1"], t["intentos_B2"], t["intentos_B3"]) == ("1", "1", "2")
    b3 = sum(float(i["tiempo_s"]) for i in intentos if i["baseline"] == "B3")
    assert abs(float(t["B3_s"]) - b3) < 0.11
    assert abs(float(t["total_s"]) - sum(float(t[f"B{n}_s"]) for n in (1, 2, 3))) < 0.11
    assert t["fallidas"] == ""

    # si una version falla en todos sus intentos, queda en "fallidas"
    def intento(baseline, estado):
        return {"escenario": "X", "repeticion": 1, "baseline": baseline, "tiempo_s": "1.0", "estado": estado}
    fijos = {"modelo_y_version": "simulacion", "version_sistema": "abc1234"}
    fila = evaluar.tiempo_del_caso([intento("B1", "ok"), intento("B2", "ok"),
                                    intento("B3", "error"), intento("B3", "error")], "2026-11-07 10:00:00", fijos)
    assert (fila["B3_s"], fila["intentos_B3"], fila["total_s"], fila["fallidas"]) == ("2.0", 2, "4.0", "B3")


def prueba_llm_respuesta_cortada_por_max_tokens():
    # el modelo llego a max_tokens: error claro, sin volver a preguntar
    class Mensaje:
        content = '{"agente": "climatico", "resumen'
        response_metadata = {"finish_reason": "length"}

    class Cliente:
        llamadas = 0

        def invoke(self, _mensajes):
            Cliente.llamadas += 1
            return Mensaje()

    cliente_original, simulacion = llm._modelo, llm.USAR_SIMULACION
    llm._modelo, llm.USAR_SIMULACION = Cliente(), False
    try:
        llm.consultar_llm("instrucciones", "pregunta", "climatico")
        assert False, "debía lanzar un error"
    except ValueError as e:
        assert "max_tokens" in str(e)
    finally:
        llm._modelo, llm.USAR_SIMULACION = cliente_original, simulacion
    assert Cliente.llamadas == 1


# ---------- pruebas de la tabla de planteles simulados ----------

def prueba_buscar_planteles_por_region():
    df = buscar_planteles(region="Valparaíso")
    assert len(df) == 5
    assert set(df["region"]) == {"Valparaíso"}
    assert set(df["id_plantel"]) == {"PL-001", "PL-002", "PL-003", "PL-004", "PL-005"}


# ---------- pruebas de vet_agent/datos/clima.py (Open-Meteo) ----------

def con_clima_falso(geocodificacion, archivo, prueba):
    # clima._get hace las dos llamadas (geocodificacion y archivo historico) en orden;
    # se reemplaza por una funcion falsa para no depender de internet
    respuestas = iter([geocodificacion, archivo])
    original = clima._get
    clima._get = lambda url, params: next(respuestas)
    try:
        return prueba()
    finally:
        clima._get = original


def prueba_traer_clima():
    geocodificacion = {"results": [{"latitude": -33.05, "longitude": -71.62}]}
    archivo = {"daily": {"time": ["2026-10-20"], "temperature_2m_mean": [15.2],
                          "relative_humidity_2m_mean": [78.0], "precipitation_sum": [0.4]}}
    resultado = con_clima_falso(geocodificacion, archivo,
                                 lambda: clima.traer_clima("Valparaíso", "2026-10-20"))
    assert resultado == {"temperatura_media_c": 15.2, "humedad_relativa_pct": 78.0, "precipitacion_mm": 0.4}


def prueba_traer_clima_lugar_no_encontrado():
    resultado = con_clima_falso({"results": []}, {}, lambda: clima.traer_clima("Lugar Inexistente", "2026-10-20"))
    assert resultado is None


def prueba_traer_clima_datos_incompletos():
    # Open-Meteo no tiene datos de los ultimos ~5 dias: los deja en null en vez de omitirlos
    geocodificacion = {"results": [{"latitude": -33.05, "longitude": -71.62}]}
    archivo = {"daily": {"time": ["2026-10-26"], "temperature_2m_mean": [15.2],
                          "relative_humidity_2m_mean": [None], "precipitation_sum": [0.4]}}
    resultado = con_clima_falso(geocodificacion, archivo,
                                 lambda: clima.traer_clima("Valparaíso", "2026-10-26"))
    assert resultado is None


def prueba_demo_traer_clima():
    geocodificacion = {"results": [{"latitude": -33.05, "longitude": -71.62}]}
    archivo = {"daily": {"time": ["2026-10-20"], "temperature_2m_mean": [15.2],
                          "relative_humidity_2m_mean": [78.0], "precipitation_sum": [0.4]}}

    def revisar(at):
        at.text_input(key="lugar_clima").input("Valparaíso")
        at.text_input(key="fecha_clima").input("2026-10-20")
        con_clima_falso(geocodificacion, archivo, lambda: apretar(at, "Traer clima"))
        assert at.text_input(key="caso_clima_temperatura_media_c").value == "15.2"
        assert at.text_input(key="caso_clima_humedad_relativa_pct").value == "78.0"
        assert at.text_input(key="caso_clima_precipitacion_mm").value == "0.4"
        assert not any("Open-Meteo" in a for a in avisos(at))
    con_demo(revisar)


def prueba_demo_traer_clima_sin_resultado():
    def revisar(at):
        at.text_input(key="lugar_clima").input("Lugar Inexistente")
        at.text_input(key="fecha_clima").input("2026-10-20")
        con_clima_falso({"results": []}, {}, lambda: apretar(at, "Traer clima"))
        assert any("Open-Meteo" in a for a in avisos(at)), avisos(at)
    con_demo(revisar)


# ---------- pruebas de cargar un escenario y descargar el informe (demo) ----------

def prueba_demo_cargar_escenario():
    # IAAP-12 (corpus/escenarios/IAAP-12.json) tiene valores bien distintos al caso
    # de ejemplo, asi que si estos calzan es porque realmente se cargo el escenario
    def revisar(at):
        at.selectbox(key="escenario_elegido").select("IAAP-12").run()
        assert not at.exception, at.exception
        assert at.text_input(key="caso_region").value == "La Araucanía"
        assert at.text_input(key="caso_comuna").value == "Puerto Saavedra"
        assert at.text_input(key="caso_n_animales").value == "47"
        assert at.text_input(key="caso_morbilidad_pct").value == ""  # viene null en el escenario
        assert at.text_input(key="caso_clima_temperatura_media_c").value == "14.0"
        assert "incoordinación en patos" in at.text_area(key="caso_signos_clinicos").value
        assert "RT-PCR influenza A" in at.text_area(key="caso_resultados_laboratorio").value
        # el lugar/fecha del boton "Traer clima" tambien se actualizan al cambiar de escenario
        # (si no, "Traer clima" traeria el clima de otro lugar/fecha sin que se note)
        assert at.text_input(key="lugar_clima").value == "Puerto Saavedra"
        assert at.text_input(key="fecha_clima").value == "2026-03-09"
    con_demo(revisar)


def prueba_demo_volver_al_caso_de_ejemplo():
    # al volver al caso de ejemplo el formulario no debe quedar con los datos de IAAP-12
    from vet_agent.main import CASO_H5N1

    def revisar(at):
        at.selectbox(key="escenario_elegido").select("IAAP-12").run()
        at.selectbox(key="escenario_elegido").select("Caso de ejemplo (H5N1)").run()
        assert not at.exception, at.exception
        assert at.text_input(key="caso_region").value == CASO_H5N1["region"]
        assert at.text_input(key="caso_comuna").value == CASO_H5N1["comuna"]
        assert at.text_input(key="lugar_clima").value == (CASO_H5N1.get("comuna") or CASO_H5N1["region"])
    con_demo(revisar)


def prueba_demo_boton_descarga_solo_tras_aprobar():
    def revisar(at):
        assert not hay_descarga(at, "Descargar informe (Markdown)")
        apretar(at, "Analizar")
        assert not hay_descarga(at, "Descargar informe (Markdown)")  # aun en revision humana
        apretar(at, "Aprobar")
        assert hay_descarga(at, "Descargar informe (Markdown)")
    con_demo(revisar)


# ---------- pruebas del rediseno de la demo (diagrama, mapa, comparacion) ----------

def prueba_demo_diagrama_del_grafo_en_vivo():
    # el diagrama sigue al grafo: agentes listos, la firma en pausa, el rechazo y la aprobacion
    def nodos(at):
        return {n: d["estado"] for n, d in at.session_state["grafo_vista"]["nodos"].items()}

    def revisar(at):
        assert set(nodos(at).values()) == {"espera"}
        apretar(at, "Analizar")
        assert nodos(at) == {"climatico": "listo", "diagnostico": "listo", "bioseguridad": "listo",
                             "moderador": "listo", "revision_humana": "pausa"}
        assert at.session_state["grafo_vista"]["ronda"] == 1
        at.text_area(key="comentarios").input("falta humedad")
        apretar(at, "Rechazar")
        vista = at.session_state["grafo_vista"]
        assert vista["rechazo"] and vista["ronda"] == 2 and nodos(at)["revision_humana"] == "pausa"
        apretar(at, "Aprobar")
        assert nodos(at)["revision_humana"] == "aprobado"
        assert not at.session_state["grafo_vista"]["rechazo"]
    con_demo(revisar)


def prueba_demo_sin_internet_no_hay_mapa():
    # sin internet la demo funciona igual: sin mapa y sin errores ni avisos
    def revisar(at):
        assert not mapas(at)
        apretar(at, "Analizar")
        assert not mapas(at) and not errores(at) and not avisos(at)
    con_demo(revisar)


def prueba_demo_mapa_de_la_comuna():
    lugares = []

    def geocodificacion(url, params):
        lugares.append(params["name"])
        return {"results": [{"latitude": -32.95, "longitude": -71.53}]}

    def revisar(at):
        from vet_agent.main import CASO_H5N1
        assert len(mapas(at)) == 1  # pantalla de inicio
        assert lugares == [CASO_H5N1["comuna"]], "busca la comuna del caso (y una sola vez: queda guardada)"
        apretar(at, "Analizar")
        assert len(mapas(at)) == 1  # junto a las tarjetas One Health
    con_demo(revisar, internet=geocodificacion)


def prueba_demo_comparar_versiones():
    def revisar(at):
        corridas = at.selectbox(key="corrida_comparar")
        assert "comparacion.csv" in corridas.options
        corridas.select("prueba_2casos/comparacion.csv").run()
        assert not at.exception, at.exception
        # formato nuevo de evaluar.py: se elige el caso (escenario x repeticion)
        assert len(at.selectbox(key="caso_comparar").options) == 2
    con_demo(revisar)


def prueba_vista_demo_escapa_html_y_numera_citas():
    from vet_agent import vista_demo as vista
    informe = {"riesgos_identificados": [{"riesgo": "<script>x</script>", "dimension_one_health": "humana",
                                          "evidencia": ["D8", "D3"]}, "un texto suelto"],
               "recomendaciones": [{"accion": "aislar", "fuentes": ["D3", "SAG-1"]}],
               "fuentes_utilizadas": [{"doc_id": "D24"}]}
    numeros = vista.numerar_citas(informe)
    assert numeros == {"D8": 1, "D3": 2, "SAG-1": 3, "D24": 4}, "en el orden en que aparecen"
    tarjeta = vista.tarjeta_one_health("humana", "Humana", "", informe["riesgos_identificados"], [], numeros)
    assert "<script>" not in tarjeta and "&lt;script&gt;" in tarjeta
    assert '<sup class="va-nota">1,2</sup>' in tarjeta
    assert "<script>" not in vista.lista_riesgos(informe["riesgos_identificados"], numeros)
    assert vista.COLOR_RIESGO["alto"] in vista.medidor("alto") and "PENDIENTE" in vista.medidor(None)


def prueba_vista_demo_siguiente_nodo():
    from vet_agent import vista_demo as vista
    assert vista.siguiente_nodo("climatico", {}) == "diagnostico"
    assert vista.siguiente_nodo("bioseguridad", {}) == "moderador"
    debate = {"evaluacion": {"estado": "requiere_debate", "preguntas": [pregunta("diagnostico")]}, "revisiones": 1}
    assert vista.siguiente_nodo("moderador", debate) == "climatico"
    final = {"evaluacion": {"estado": "informe_final"}, "revisiones": 1}
    assert vista.siguiente_nodo("moderador", final) == "revision_humana"
    assert vista.siguiente_nodo("revision_humana", {"decision_humana": {"aprobado": False}}) == "moderador"
    assert vista.siguiente_nodo("revision_humana", {"decision_humana": {"aprobado": True}}) is None


def prueba_vista_demo_lee_comparaciones():
    from vet_agent import vista_demo as vista
    carpeta = pathlib.Path(__file__).resolve().parents[1] / "resultados"
    archivos = vista.archivos_comparacion(carpeta)
    assert next(iter(archivos)) == "comparacion.csv", "la de la raiz va primero"
    # formato viejo: llm_sin_rag/monoagente_rag/multiagente pasan a B1/B2/B3
    viejo = vista.leer_comparacion(archivos["comparacion.csv"])
    assert [f["version"] for f in viejo] == ["B1", "B2", "B3"]
    assert viejo[0]["informe"]["nivel_riesgo_global"] and viejo[0]["segundos"] > 0
    # una corrida que fallo queda con su error y sin informe
    fallo = vista.leer_comparacion(archivos["mlx/comparacion_corrida1_fallo_multiagente.csv"])
    b3 = next(f for f in fallo if f["version"] == "B3")
    assert b3["informe"] is None and b3["error"].startswith("ERROR")
    assert "Falló" in vista.tarjeta_version("B3", b3)
    # formato nuevo: trae escenario y repeticion
    nuevo = vista.leer_comparacion(archivos["prueba_2casos/comparacion.csv"])
    assert {f["escenario"] for f in nuevo} == {"IAAP-01", "IAAP-12"}


# ---------- pruebas del script que revisa escenarios (Tarea 3) ----------

def prueba_revisar_escenarios_tmp():
    import json
    import pathlib
    import tempfile
    from vet_agent.datos import revisar_escenarios

    tmpdir = pathlib.Path(tempfile.mkdtemp())
    escenario = {
        'id_escenario': 'TST-01',
        'titulo': 'Prueba',
        'parte_a_entrada': {
            'especie': 'aves',
            'region': 'Valparaíso',
            'n_animales': 1000,
            'mortalidad_pct': 1.5,
            'morbilidad_pct': 2.0,
            'clima': {
                'temperatura_media_c': 12.0,
                'humedad_relativa_pct': 70.0,
                'precipitacion_mm': 1.0
            }
        },
        'parte_b_referencia': {
            'documentos_esperados': ['C21']
        }
    }
    ruta = tmpdir / 'TST-01.json'
    ruta.write_text(json.dumps(escenario, ensure_ascii=False), encoding='utf-8')
    equiv = revisar_escenarios._cargar_equivalencias()
    ids_ok = revisar_escenarios._cargar_ids_ok()
    res = revisar_escenarios.revisar_archivo(ruta, equiv, ids_ok)
    assert res['id_escenario'] == 'TST-01'
    assert 'documentos_esperados_traducidos' in res
    assert isinstance(res['avisos'], list)
    assert isinstance(res['errores'], list)
    assert res['errores'] == []
    # C21 esta en el corpus: solo se puede comprobar si hay reporte de validacion local
    if ids_ok:
        assert 'C21' not in res['faltantes']
    assert escenario['parte_a_entrada']['region'] == 'Valparaíso'


if __name__ == "__main__":
    pruebas = [
        prueba_moderador_informe_final,
        prueba_moderador_pide_debate,
        prueba_moderador_llega_al_maximo,
        prueba_moderador_agente_invalido,
        prueba_plantillas_de_salida_son_json_valido,
        prueba_humano_aprueba,
        prueba_humano_rechaza,
        prueba_flujo_normal,
        prueba_rechazo_y_aprobacion,
        prueba_debate_termina,
        prueba_debate_solo_llama_al_consultado,
        prueba_filtro_excluir_porcino,
        prueba_buscar_maximo_k,
        prueba_como_texto_arma_la_cabecera,
        prueba_chunking_no_deja_pedazos_vacios_ni_pasados,
        prueba_chunking_no_corta_palabras,
        prueba_chunking_parte_palabra_muy_larga,
        prueba_chunking_md_repite_el_titulo_en_las_continuaciones,
        prueba_cabecera_ida_y_vuelta,
        prueba_limpiar_texto_quita_ruido_y_conserva_titulos,
        prueba_recortar_fragmento,
        prueba_contexto_rag_sin_coleccion,
        prueba_evidencia_y_fuentes_reales,
        prueba_integracion_chroma_real,
        prueba_cargar_escenarios_ejemplo,
        prueba_main_rechazo_pide_comentario,
        prueba_demo_rechazo_sin_comentario,
        prueba_demo_error_de_conexion,
        prueba_demo_error_de_contexto_al_reanudar,

        prueba_api_endpoints,

        prueba_buscar_planteles_por_region,

        prueba_evaluar_registro_formato,
        prueba_evaluar_reintenta_si_falla,
        prueba_evaluar_tiempos_por_caso,
        prueba_evaluar_cache_reintento_b3_no_repite_agentes,
        prueba_evaluar_cache_repeticion_nueva_vuelve_a_llamar,
        prueba_llm_respuesta_cortada_por_max_tokens,

        prueba_traer_clima,
        prueba_traer_clima_lugar_no_encontrado,
        prueba_traer_clima_datos_incompletos,
        prueba_demo_traer_clima,
        prueba_demo_traer_clima_sin_resultado,
        prueba_demo_cargar_escenario,
        prueba_demo_volver_al_caso_de_ejemplo,
        prueba_demo_boton_descarga_solo_tras_aprobar,
        prueba_demo_diagrama_del_grafo_en_vivo,
        prueba_demo_sin_internet_no_hay_mapa,
        prueba_demo_mapa_de_la_comuna,
        prueba_demo_comparar_versiones,
        prueba_vista_demo_escapa_html_y_numera_citas,
        prueba_vista_demo_siguiente_nodo,
        prueba_vista_demo_lee_comparaciones,

        prueba_revisar_escenarios_tmp,
    ]
    pasaron = omitidas = 0
    for prueba in pruebas:
        motivo = prueba()
        if isinstance(motivo, str):
            omitidas += 1
            print(f"OMITIDA {prueba.__name__}: {motivo}")
        else:
            pasaron += 1
            print(f"OK  {prueba.__name__}")

    resumen = f"{pasaron} pruebas pasaron"
    if omitidas:
        resumen += f", {omitidas} omitida(s)"
    print("\n" + resumen)