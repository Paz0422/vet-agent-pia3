"""Compara el sistema multiagente con dos versiones mas simples y deja un registro de cada corrida.

Uso (raiz del repo, con chroma_db armada y LM Studio encendido o USAR_SIMULACION=true):
    python -m vet_agent.evaluar                                   # CASO_H5N1, 1 repeticion
    python -m vet_agent.evaluar --escenarios corpus/escenarios --repeticiones 3

Las 3 versiones (con los nombres que usa Investigacion):
- B1 llm_sin_rag:    un solo prompt al LLM, sin buscar documentos.
- B2 monoagente_rag: un solo agente que busca en las 3 colecciones y redacta el informe solo.
- B3 multiagente:    el grafo actual, aprobando la revision humana automaticamente.

Cada corrida (un escenario x una version x una repeticion) agrega una fila a
resultados/registro_corridas.csv (UTF-8, todos los campos entre comillas), con el formato
acordado con Investigacion. Si una corrida falla queda con estado "error" y se sigue con la
siguiente. Ademas, resultados/comparacion.csv resume la ultima ejecucion.
"""
import argparse
import csv
import hashlib
import json
import pathlib
import subprocess
import time
import urllib.request
from datetime import datetime

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent import llm, prompts
from vet_agent.agentes.base import COLECCIONES, SIN_CONTEXTO, a_texto, contexto_rag, texto_para_prompt
from vet_agent.agentes.moderador import evidencia_unida, fuentes_de
from vet_agent.config import LLM_MAX_TOKENS, LLM_MODELO, LLM_TEMPERATURA, LLM_URL
from vet_agent.grafo import construir_grafo
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.main import CASO_H5N1
from vet_agent.prompts import PROMPT_MODERADOR

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "resultados" / "comparacion.csv"
REGISTRO = RAIZ / "resultados" / "registro_corridas.csv"

# columnas del registro, en el orden acordado con Investigacion
COLUMNAS_REGISTRO = [
    "id_corrida", "fecha", "version_sistema", "baseline", "escenario", "repeticion",
    "modelo_y_version", "temperatura", "max_tokens", "version_prompt_base",
    "entrada", "salida", "tiempo_s", "fragmentos", "estado",
]

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


# ---------- las 3 versiones: cada una devuelve (informe, fragmentos recuperados) ----------

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


def llm_sin_rag(caso: dict) -> tuple[dict, list[dict]]:
    return _informe_unico(caso, SIN_DOCUMENTOS, []), []


def monoagente_rag(caso: dict) -> tuple[dict, list[dict]]:
    # busca en las 3 colecciones con las mismas consultas que usan los agentes,
    # asi recibe los mismos documentos que el sistema multiagente
    fragmentos = []
    for agente in COLECCIONES:
        fragmentos += contexto_rag(agente, caso)[1]
    contexto = texto_para_prompt(fragmentos) or SIN_CONTEXTO
    return _informe_unico(caso, contexto, fragmentos), fragmentos


def multiagente(caso: dict) -> tuple[dict, list[dict]]:
    app = construir_grafo(InMemorySaver())
    config = {"configurable": {"thread_id": "evaluacion"}}
    estado_inicial = {"caso": caso, "resultados": {}, "solicitudes": {}, "revisiones": 0, "decision_humana": {}}
    resultado = app.invoke(estado_inicial, config)
    # cada pausa de revision humana se aprueba sola
    while "__interrupt__" in resultado:
        resultado = app.invoke(Command(resume=APROBAR), config)
    # los fragmentos son la evidencia que recuperaron los 3 agentes
    return resultado["evaluacion"].get("informe", {}), evidencia_unida(resultado["resultados"])


# baseline -> (nombre de la configuracion, funcion)
BASELINES = {
    "B1": ("llm_sin_rag", llm_sin_rag),
    "B2": ("monoagente_rag", monoagente_rag),
    "B3": ("multiagente", multiagente),
}


# ---------- datos fijos de la ejecucion (iguales en todas sus corridas) ----------

def version_sistema() -> str:
    # hash corto del commit; "-dirty" si hay cambios sin commitear en archivos del repo
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ,
                                capture_output=True, text=True, check=True).stdout.strip()
        cambios = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=RAIZ,
                                 capture_output=True, text=True, check=True).stdout.strip()
        return commit + ("-dirty" if cambios else "")
    except (OSError, subprocess.CalledProcessError):
        return "desconocida"


def modelo_y_version() -> str:
    # nombre del .env y, si LM Studio responde, el formato y la cuantizacion del modelo cargado
    # (ej. "qwen/qwen3-4b-2507 (gguf Q4_K_M)"): MLX y GGUF no se comportan igual
    if llm.USAR_SIMULACION:
        return "simulacion"
    try:
        url = LLM_URL.rstrip("/").removesuffix("/v1") + "/api/v0/models"
        with urllib.request.urlopen(url, timeout=5) as r:
            modelos = json.load(r)["data"]
        m = next(m for m in modelos if m["id"] == LLM_MODELO and m.get("state") == "loaded")
        return f"{LLM_MODELO} ({m.get('compatibility_type', '?')} {m.get('quantization', '?')})"
    except Exception:
        return LLM_MODELO


def version_prompt_base() -> str:
    # hash corto del texto de todos los prompts: cambia solo si alguien edita un prompt
    textos = [prompts.PROMPT_CLIMATICO, prompts.PROMPT_DIAGNOSTICO, prompts.PROMPT_BIOSEGURIDAD,
              prompts.PROMPT_MODERADOR, PROMPT_UNICO]
    return hashlib.sha256("\n".join(textos).encode("utf-8")).hexdigest()[:8]


# ---------- escenarios ----------

def cargar_escenarios(carpeta: str | None) -> list[tuple[str, dict]]:
    """Devuelve [(id_escenario, parte_a_entrada), ...]. Sin carpeta, usa CASO_H5N1.

    La Parte B (respuesta esperada) nunca se le pasa al sistema.
    """
    if not carpeta:
        return [("CASO_H5N1", CASO_H5N1)]
    escenarios = []
    for archivo in sorted(pathlib.Path(carpeta).glob("*.json")):
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        if "parte_a_entrada" not in datos:
            print(f"Se salta {archivo.name}: no tiene parte_a_entrada")
            continue
        escenarios.append((datos.get("id_escenario") or archivo.stem, datos["parte_a_entrada"]))
    return escenarios


# ---------- una corrida y el registro ----------

def fuentes_citadas(informe: dict) -> int:
    # documentos distintos en fuentes_utilizadas
    return len({f.get("doc_id") for f in informe.get("fuentes_utilizadas", []) if isinstance(f, dict) and f.get("doc_id")})


def correr(baseline: str, escenario: str, caso: dict, repeticion: int, id_corrida: str, fijos: dict) -> dict:
    """Corre una version con un escenario y devuelve la fila del registro."""
    _, configuracion = BASELINES[baseline]
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inicio = time.perf_counter()
    try:
        informe, fragmentos = configuracion(caso)
        salida, estado = json.dumps(informe, ensure_ascii=False), "ok"
        doc_ids = ";".join(dict.fromkeys(f["doc_id"] for f in fragmentos))
    except Exception as e:
        # si una corrida falla se anota y se sigue con la siguiente
        salida, estado, doc_ids = f"{type(e).__name__}: {e}", "error", ""
    return {
        "id_corrida": id_corrida, "fecha": fecha, "baseline": baseline,
        "escenario": escenario, "repeticion": repeticion, **fijos,
        "entrada": json.dumps(caso, ensure_ascii=False), "salida": salida,
        "tiempo_s": f"{time.perf_counter() - inicio:.1f}", "fragmentos": doc_ids, "estado": estado,
    }


def registrar(fila: dict, ruta: pathlib.Path = REGISTRO) -> None:
    # se agrega una fila apenas termina cada corrida: si el proceso se corta, no se pierde lo anterior
    ruta.parent.mkdir(parents=True, exist_ok=True)
    nuevo = not ruta.exists() or ruta.stat().st_size == 0
    with open(ruta, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_REGISTRO, quoting=csv.QUOTE_ALL)
        if nuevo:
            w.writeheader()
        w.writerow(fila)


def guardar(filas: list[dict], ruta: pathlib.Path = SALIDA) -> None:
    # resumen de la ultima ejecucion (el formato de la Tarea 2, con escenario y repeticion)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    columnas = ["configuracion", "escenario", "repeticion", "informe", "fuentes_citadas", "segundos"]
    # utf-8-sig para que Excel muestre bien las tildes
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas)
        w.writeheader()
        for fila in filas:
            informe = json.loads(fila["salida"]) if fila["estado"] == "ok" else {}
            w.writerow({"configuracion": fila["baseline"], "escenario": fila["escenario"],
                        "repeticion": fila["repeticion"],
                        "informe": fila["salida"] if fila["estado"] == "ok" else f"ERROR: {fila['salida']}",
                        "fuentes_citadas": fuentes_citadas(informe), "segundos": fila["tiempo_s"]})


def evaluar(escenarios: list[tuple[str, dict]], repeticiones: int = 1, registro: pathlib.Path = REGISTRO) -> list[dict]:
    fijos = {"version_sistema": version_sistema(), "modelo_y_version": modelo_y_version(),
             "temperatura": LLM_TEMPERATURA, "max_tokens": LLM_MAX_TOKENS,
             "version_prompt_base": version_prompt_base()}
    # id_corrida = momento en que parte la ejecucion + numero de corrida: unico aunque se agreguen filas
    sesion = datetime.now().strftime("%Y%m%d-%H%M%S")
    filas, total, n = [], len(escenarios) * len(BASELINES) * repeticiones, 0
    for escenario, caso in escenarios:
        for repeticion in range(1, repeticiones + 1):
            for baseline, (nombre, _) in BASELINES.items():
                n += 1
                print(f"\n=== [{n}/{total}] {escenario} · {baseline} ({nombre}) · repetición {repeticion}/{repeticiones} ===")
                fila = correr(baseline, escenario, caso, repeticion, f"{sesion}-{n:03d}", fijos)
                registrar(fila, registro)
                filas.append(fila)
                detalle = f"{len(fila['fragmentos'].split(';')) if fila['fragmentos'] else 0} documentos recuperados"
                print(f"{baseline}: {fila['estado']}, {fila['tiempo_s']} s, {detalle}")
    return filas


def main() -> None:
    parser = argparse.ArgumentParser(description="Compara B1, B2 y B3 y registra cada corrida")
    parser.add_argument("--escenarios", help="carpeta con los escenarios en JSON (sin esto: CASO_H5N1)")
    parser.add_argument("--repeticiones", type=int, default=1, help="veces que se corre cada version por escenario")
    args = parser.parse_args()

    escenarios = cargar_escenarios(args.escenarios)
    if not escenarios:
        print(f"No hay escenarios en {args.escenarios}")
        return
    print("Modo:", "SIMULADO (respuestas de ejemplo)" if llm.USAR_SIMULACION else f"LLM real ({LLM_MODELO})")
    filas = evaluar(escenarios, args.repeticiones)
    guardar(filas)
    print(f"\nListo: {len(filas)} corridas agregadas a {REGISTRO.relative_to(RAIZ)}; resumen en {SALIDA.relative_to(RAIZ)}")
    errores = sum(f["estado"] == "error" for f in filas)
    if errores:
        print(f"OJO: {errores} corridas con error (ver la columna salida)")


if __name__ == "__main__":
    main()
