"""Compara el sistema multiagente con dos versiones mas simples y deja un registro de cada corrida.

Uso (raiz del repo, con chroma_db armada y LM Studio encendido o USAR_SIMULACION=true):
    python -m vet_agent.evaluar                                   # CASO_H5N1, 1 repeticion
    python -m vet_agent.evaluar --escenarios corpus/escenarios --repeticiones 3

Las 3 versiones (con los nombres que usa Investigacion):
- B1 llm_sin_rag:    un solo prompt al LLM, sin buscar documentos.
- B2 monoagente_rag: un solo agente que busca en las 3 colecciones y redacta el informe solo.
- B3 multiagente:    el grafo actual, aprobando la revision humana automaticamente.

Cada intento de cada corrida agrega una fila a resultados/registro_corridas.csv (UTF-8, todos
los campos entre comillas), con el formato acordado con Investigacion (documento E1 v6):
- id_corrida = baseline-escenario-repeticion-intento, ej. B3-IAAP-01-R2-I1.
- Si un intento termina en error (tiempo agotado, respuesta vacia, JSON invalido o texto
  cortado por max_tokens), se repite la misma combinacion hasta MAX_INTENTOS en total.
  Cada intento es una fila; apenas uno sale "ok" no se hacen mas.
- Mientras corre, consultar_llm no reintenta por su cuenta (LLM_REINTENTOS = 0), asi cada
  falla queda registrada como intento. La demo y main.py siguen usando LLM_REINTENTOS del .env.
Ademas, resultados/comparacion.csv resume la ultima ejecucion (el ultimo intento de cada corrida).
"""
import argparse
import csv
import json
import pathlib
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent import llm
from vet_agent.agentes.base import COLECCIONES, SIN_CONTEXTO, a_texto, contexto_rag, texto_para_prompt
from vet_agent.agentes.moderador import evidencia_unida, fuentes_de
from vet_agent.config import LLM_MAX_TOKENS, LLM_MODELO, LLM_TEMPERATURA, LLM_URL
from vet_agent.grafo import construir_grafo
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.main import CASO_H5N1
from vet_agent.prompts import PROMPT_UNICO, VERSION_PROMPTS

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SALIDA = RAIZ / "resultados" / "comparacion.csv"
REGISTRO = RAIZ / "resultados" / "registro_corridas.csv"

# columnas del registro, en el orden acordado con Investigacion (E1 v6)
COLUMNAS_REGISTRO = [
    "id_corrida", "fecha", "version_sistema", "baseline", "escenario", "repeticion", "intento",
    "modelo_y_version", "temperatura", "max_tokens", "version_prompt_base",
    "entrada", "salida", "tiempo_s", "fragmentos", "estado",
]

# intentos en total por combinacion (baseline, escenario, repeticion)
MAX_INTENTOS = 4

APROBAR = {"aprobado": True, "comentarios": ""}

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


def id_corrida(baseline: str, escenario: str, repeticion: int, intento: int) -> str:
    return f"{baseline}-{escenario}-R{repeticion}-I{intento}"


def lista_fragmentos(fragmentos: list[dict]) -> str:
    # "D3_2;D21_0": doc_id y numero de pedazo que guarda Chroma. Sin repetir: B2 puede traer
    # el mismo pedazo de dos colecciones (D3 esta en diagnostico_md y bioseguridad_md)
    return ";".join(dict.fromkeys(f"{f['doc_id']}_{f['chunk']}" for f in fragmentos))


def correr(baseline: str, escenario: str, caso: dict, repeticion: int, intento: int, fijos: dict) -> dict:
    """Corre un intento de una version con un escenario y devuelve la fila del registro."""
    _, configuracion = BASELINES[baseline]
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    inicio = time.perf_counter()
    try:
        informe, fragmentos = configuracion(caso)
        if not informe:
            raise ValueError("el sistema no entregó informe (respuesta vacía)")
        salida, estado = json.dumps(informe, ensure_ascii=False), "ok"
        fragmentos = lista_fragmentos(fragmentos)
    except Exception as e:
        # tiempo agotado, respuesta vacia, JSON invalido o texto cortado: se anota y se reintenta
        salida, estado, fragmentos = f"{type(e).__name__}: {e}", "error", ""
    return {
        "id_corrida": id_corrida(baseline, escenario, repeticion, intento), "fecha": fecha,
        "baseline": baseline, "escenario": escenario, "repeticion": repeticion, "intento": intento,
        **fijos, "entrada": escenario, "salida": salida,
        "tiempo_s": f"{time.perf_counter() - inicio:.1f}", "fragmentos": fragmentos, "estado": estado,
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


@contextmanager
def sin_reintentos_llm():
    # cada falla del LLM tiene que quedar como un intento en el registro, asi que mientras
    # se evalua consultar_llm no vuelve a preguntar por su cuenta; despues queda como estaba
    original = llm.LLM_REINTENTOS
    llm.LLM_REINTENTOS = 0
    try:
        yield
    finally:
        llm.LLM_REINTENTOS = original


def evaluar(escenarios: list[tuple[str, dict]], repeticiones: int = 1, registro: pathlib.Path = REGISTRO) -> list[dict]:
    fijos = {"version_sistema": version_sistema(), "modelo_y_version": modelo_y_version(),
             "temperatura": LLM_TEMPERATURA, "max_tokens": LLM_MAX_TOKENS,
             "version_prompt_base": VERSION_PROMPTS}
    filas, total, n = [], len(escenarios) * len(BASELINES) * repeticiones, 0
    with sin_reintentos_llm():
        for escenario, caso in escenarios:
            for repeticion in range(1, repeticiones + 1):
                for baseline, (nombre, _) in BASELINES.items():
                    n += 1
                    for intento in range(1, MAX_INTENTOS + 1):
                        print(f"\n=== [{n}/{total}] {escenario} · {baseline} ({nombre}) · "
                              f"repetición {repeticion}/{repeticiones} · intento {intento}/{MAX_INTENTOS} ===")
                        fila = correr(baseline, escenario, caso, repeticion, intento, fijos)
                        registrar(fila, registro)
                        filas.append(fila)
                        detalle = f"{len(fila['fragmentos'].split(';')) if fila['fragmentos'] else 0} fragmentos recuperados"
                        print(f"{fila['id_corrida']}: {fila['estado']}, {fila['tiempo_s']} s, {detalle}")
                        if fila["estado"] == "ok":
                            break
    return filas


def ultimos_intentos(filas: list[dict]) -> list[dict]:
    # una fila por corrida: la que salio ok o, si todas fallaron, el ultimo intento
    return [f for f in filas if f["estado"] == "ok" or f["intento"] == MAX_INTENTOS]


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
    finales = ultimos_intentos(filas)
    guardar(finales)
    print(f"\nListo: {len(filas)} intentos de {len(finales)} corridas agregados a {REGISTRO.relative_to(RAIZ)}; "
          f"resumen en {SALIDA.relative_to(RAIZ)}")
    fallidas = sum(f["estado"] == "error" for f in finales)
    if fallidas:
        print(f"OJO: {fallidas} corridas fallaron en los {MAX_INTENTOS} intentos (ver la columna salida)")


if __name__ == "__main__":
    main()
