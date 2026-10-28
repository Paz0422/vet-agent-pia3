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
from vet_agent.datos.planteles import buscar_planteles
from vet_agent.grafo import construir_grafo, despues_del_moderador, despues_de_revision_humana
from vet_agent.main import pedir_decision
from vet_agent.rag import buscar, cabecera, chunking, limpiar_texto

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
                metadatas=[{"doc_id": f"P{i}", "enfermedad": e, "titulo": t} for i, (t, e) in enumerate(PEDAZOS)])
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


def con_demo(prueba):
    """Corre `prueba(at)` con la demo cargada y Chroma vacio (los agentes siguen sin contexto RAG)."""
    from streamlit.testing.v1 import AppTest
    cliente_original, ef_original = buscar._cliente, buscar._ef
    buscar._cliente, buscar._ef = chromadb.EphemeralClient(), EmbeddingFalso()
    try:
        at = AppTest.from_file(str(APP_DEMO), default_timeout=120)
        at.run()
        prueba(at)
    finally:
        buscar._cliente, buscar._ef = cliente_original, ef_original


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

# ---------- pruebas de la tabla de planteles simulados ----------

def prueba_buscar_planteles_por_region():
    df = buscar_planteles(region="Valparaíso")
    assert len(df) == 5
    assert set(df["region"]) == {"Valparaíso"}
    assert set(df["id_plantel"]) == {"PL-001", "PL-002", "PL-003", "PL-004", "PL-005"}



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
        prueba_buscar_planteles_por_region,
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