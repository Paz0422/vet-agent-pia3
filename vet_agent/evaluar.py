"""Tarea 2: compara el sistema multiagente con dos configuraciones mas simples.

Uso (raiz del repo, con chroma_db armada y LM Studio encendido):
    python -m vet_agent.evaluar

Corre el mismo caso (CASO_H5N1 de main.py) de 3 formas:
- llm_sin_rag:    un solo prompt al LLM, sin buscar documentos.
- monoagente_rag: un solo agente que busca en las 3 colecciones y redacta el informe solo.
- multiagente:    el grafo actual, aprobando la revision humana automaticamente.

Guarda en resultados/comparacion.csv una fila por configuracion: nombre, informe (JSON),
numero de fuentes citadas y segundos que tardo. Con USAR_SIMULACION=true tambien corre
(respuestas de ejemplo), pero los numeros solo sirven con el LLM real.
"""
import csv
import json
import pathlib
import time

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent import llm
from vet_agent.agentes.base import COLECCIONES, SIN_CONTEXTO, a_texto, contexto_rag, texto_para_prompt
from vet_agent.agentes.moderador import fuentes_de
from vet_agent.grafo import construir_grafo
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.main import CASO_H5N1
from vet_agent.prompts import PROMPT_MODERADOR

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "resultados" / "comparacion.csv"

APROBAR = {"aprobado": True, "comentarios": ""}

# el informe tiene que salir con el mismo formato en las 3 configuraciones para poder
# compararlas, asi que se reutiliza el JSON de "informe_final" del prompt del moderador
FORMATO_INFORME = PROMPT_MODERADOR.split("Si el informe está listo:", 1)[1].strip()

# un solo prompt que hace el trabajo de los 4 agentes (lo usan las dos configuraciones simples)
PROMPT_UNICO = """
ROL
Eres un asistente de vigilancia epidemiológica veterinaria con enfoque One Health. Analizas tú solo el caso completo (condiciones climáticas, resultados de laboratorio y bioseguridad del plantel) y redactas el informe epidemiológico prescriptivo final.

ENTRADAS
- Contexto recuperado: {contexto_rag}

TAREAS
1. Evalúa cómo el clima y el ambiente influyen en el riesgo de introducción y transmisión del patógeno.
2. Interpreta los resultados de laboratorio. No emitas un diagnóstico definitivo: usa "compatible con", "sugiere", "no descarta".
3. Propón medidas de prevención, contención y limpieza/desinfección, y de protección para personas y ambiente.
4. Integra las tres dimensiones One Health y asigna un nivel de riesgo global justificado.

REGLAS
- Cita solo doc_id que aparezcan en el contexto recuperado. Si no hay documentos, deja las listas de fuentes vacías y decláralo como limitación.
- No inventes productos, dosis ni tiempos de contacto. No ordenes sacrificios, cuarentenas ni restricciones: descríbelas como "medida que la autoridad competente podría evaluar".
- El informe debe declarar que es apoyo a la decisión y requiere validación de un médico veterinario y de la autoridad competente.

SALIDA (JSON)
""" + FORMATO_INFORME + "\n"

SIN_DOCUMENTOS = "(Sin documentos: esta configuración no busca en la base documental.)"


def _informe_unico(caso: dict, contexto: str, fragmentos: list[dict]) -> dict:
    instrucciones = PROMPT_UNICO.replace("{contexto_rag}", contexto)
    pregunta = "Caso a analizar:\n" + a_texto(caso)
    # "moderador": en modo simulado devuelve el informe de ejemplo, que tiene el mismo formato
    respuesta = leer_json(consultar_llm(instrucciones, pregunta, "moderador"))
    informe = respuesta.get("informe", respuesta)
    # igual que el moderador: en modo simulado las fuentes son los documentos recuperados
    if llm.USAR_SIMULACION:
        informe["fuentes_utilizadas"] = fuentes_de(fragmentos)
    return informe


def llm_sin_rag(caso: dict) -> dict:
    return _informe_unico(caso, SIN_DOCUMENTOS, [])


def monoagente_rag(caso: dict) -> dict:
    # busca en las 3 colecciones con las mismas consultas que usan los agentes,
    # asi recibe los mismos documentos que el sistema multiagente
    fragmentos = []
    for agente in COLECCIONES:
        fragmentos += contexto_rag(agente, caso)[1]
    contexto = texto_para_prompt(fragmentos) or SIN_CONTEXTO
    return _informe_unico(caso, contexto, fragmentos)


def multiagente(caso: dict) -> dict:
    app = construir_grafo(InMemorySaver())
    config = {"configurable": {"thread_id": "evaluacion"}}
    estado_inicial = {"caso": caso, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}
    resultado = app.invoke(estado_inicial, config)
    # cada pausa de revision humana se aprueba sola
    while "__interrupt__" in resultado:
        resultado = app.invoke(Command(resume=APROBAR), config)
    return resultado["evaluacion"].get("informe", {})


def fuentes_citadas(informe: dict) -> int:
    # documentos distintos en fuentes_utilizadas
    return len({f.get("doc_id") for f in informe.get("fuentes_utilizadas", []) if isinstance(f, dict) and f.get("doc_id")})


CONFIGURACIONES = {
    "llm_sin_rag": llm_sin_rag,
    "monoagente_rag": monoagente_rag,
    "multiagente": multiagente,
}


def evaluar(caso: dict) -> list[dict]:
    filas = []
    for nombre, configuracion in CONFIGURACIONES.items():
        print(f"\n=== {nombre} ===")
        inicio = time.perf_counter()
        try:
            informe = configuracion(caso)
            texto, fuentes = json.dumps(informe, ensure_ascii=False), fuentes_citadas(informe)
        except Exception as e:
            # si una configuracion falla se anota y se sigue con las demas
            print(f"[{nombre}] fallo: {e}")
            texto, fuentes = f"ERROR: {e}", 0
        segundos = round(time.perf_counter() - inicio, 1)
        print(f"{nombre}: {fuentes} fuentes citadas, {segundos} s")
        filas.append({"configuracion": nombre, "informe": texto, "fuentes_citadas": fuentes, "segundos": segundos})
    return filas


def guardar(filas: list[dict], ruta: pathlib.Path = SALIDA) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig para que Excel muestre bien las tildes
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["configuracion", "informe", "fuentes_citadas", "segundos"])
        w.writeheader()
        w.writerows(filas)


if __name__ == "__main__":
    print("Modo:", "SIMULADO (respuestas de ejemplo)" if llm.USAR_SIMULACION else f"LLM real ({llm.LLM_MODELO})")
    filas = evaluar(CASO_H5N1)
    guardar(filas)
    print(f"\nListo: {SALIDA.relative_to(RAIZ)}")
    for f in filas:
        print(f"  {f['configuracion']:15} {f['fuentes_citadas']:3} fuentes  {f['segundos']:8} s")
