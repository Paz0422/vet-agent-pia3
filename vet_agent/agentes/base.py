import json

from vet_agent import llm
from vet_agent.config import K_FRAGMENTOS, MAX_CARACTERES_FRAGMENTO
from vet_agent.estado import Estado
from vet_agent.rag import buscar

# si no se pudo buscar (no hay chroma_db, falta la coleccion, etc.) el agente recibe esto
SIN_CONTEXTO = "(Aún no hay documentos recuperados. Indícalo como vacío de respaldo documental.)"

# cada agente busca en su propia coleccion de Chroma
COLECCIONES = {
    "climatico": "climatico_md",
    "diagnostico": "diagnostico_md",
    "bioseguridad": "bioseguridad_md",
}

# que partes del caso van en la consulta de cada agente (ademas de especie y signos)
CAMPOS_CONSULTA = {
    "climatico": ["clima", "region", "comuna", "fecha_evento"],
    "diagnostico": ["resultados_laboratorio", "mortalidad_pct"],
    "bioseguridad": ["sistema_productivo", "movimientos_animales", "aves_silvestres_cercanas"],
}


def a_texto(datos) -> str:
    # pasa listas o diccionarios del caso a texto legible para el prompt
    if datos in (None, "", [], {}):
        return "no informado"
    if isinstance(datos, str):
        return datos
    return json.dumps(datos, ensure_ascii=False, indent=2)


def preparar_pregunta(estado: Estado, agente: str) -> str:
    # el caso completo va siempre, asi el agente sabe especie, enfermedad, etc
    pregunta = "Caso a analizar:\n" + a_texto(estado["caso"])

    # si el moderador le pidio algo a este agente, se lo agregamos
    aclaracion = estado.get("solicitudes", {}).get(agente)
    if aclaracion:
        pregunta += f"\n\nEl moderador solicita aclarar lo siguiente: {aclaracion}"

    return pregunta


def _en_una_linea(datos) -> str:
    # la consulta va al buscador, no al LLM: mejor texto corrido que JSON
    if isinstance(datos, dict):
        return ", ".join(f"{k} {v}" for k, v in datos.items())
    if isinstance(datos, list):
        return ", ".join(str(d) for d in datos)
    return str(datos)


def consulta_del_caso(agente: str, caso: dict) -> str:
    campos = ["especie", "signos_clinicos"] + CAMPOS_CONSULTA.get(agente, [])
    return ". ".join(_en_una_linea(caso[c]) for c in campos if caso.get(c))


def recortar(texto: str, limite: int = MAX_CARACTERES_FRAGMENTO) -> str:
    # corta en el ultimo espacio antes del limite para no dejar una palabra a medias
    if len(texto) <= limite:
        return texto
    corte = texto.rfind(" ", 0, limite + 1)
    return texto[:corte if corte > 0 else limite].rstrip() + " […]"


def texto_para_prompt(fragmentos: list[dict]) -> str:
    # menos texto al modelo = respuesta mas rapida; la evidencia guardada queda completa
    return buscar.como_texto([f | {"texto": recortar(f["texto"])} for f in fragmentos])


def contexto_rag(agente: str, caso: dict) -> tuple[str, list[dict]]:
    """Busca en la coleccion del agente. Devuelve (texto para el prompt, fragmentos crudos).

    Si Chroma falla o la coleccion no existe devuelve (SIN_CONTEXTO, []) para no cortar el grafo.
    """
    coleccion = COLECCIONES[agente]
    consulta = consulta_del_caso(agente, caso)
    # en un caso que no es de cerdos solo sirven los documentos "aviar" y "general"
    excluir_porcino = "porcino" not in str(caso.get("especie", "")).lower()
    try:
        fragmentos = buscar.buscar_crudo(coleccion, consulta, K_FRAGMENTOS, excluir_porcino)
    except Exception as e:
        print(f"[{agente}] sin contexto RAG: no pude buscar en '{coleccion}' ({e})")
        return SIN_CONTEXTO, []
    return (texto_para_prompt(fragmentos) or SIN_CONTEXTO), fragmentos


def _rellenar_fuentes(datos, doc_ids: list[str]):
    # recorre la respuesta y pone los doc_id reales en cada campo "fuentes"
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            if clave == "fuentes" and isinstance(valor, list):
                datos[clave] = list(doc_ids)
            else:
                _rellenar_fuentes(valor, doc_ids)
    elif isinstance(datos, list):
        for d in datos:
            _rellenar_fuentes(d, doc_ids)


def con_evidencia(salida: dict, fragmentos: list[dict]) -> dict:
    """Agrega a la salida del agente los fragmentos recuperados en "evidencia".

    Con la redaccion simulada, ademas reemplaza los "fuentes" de ejemplo por los doc_id reales.
    """
    salida["evidencia"] = [
        {campo: f.get(campo, "") for campo in ("doc_id", "chunk", "institucion", "anio", "titulo", "texto")}
        for f in fragmentos
    ]
    if llm.USAR_SIMULACION:
        _rellenar_fuentes(salida, list(dict.fromkeys(f["doc_id"] for f in fragmentos)))
    return salida