"""Completa la trazabilidad de una entrega de corridas (pedido de Investigacion, control E1).

No vuelve a correr nada ni toca los CSV originales: lee la carpeta entregada y escribe una
version nueva en <carpeta>/v2/.

Uso (raiz del repo; no necesita LM Studio ni chroma_db):
    python -m vet_agent.trazabilidad --carpeta resultados/<carpeta_de_la_entrega>

Escribe en <carpeta>/v2/:
- registro_corridas_v2.csv y registro_corridas_B1/B2/B3_v2.csv: las mismas filas y columnas
  que el original, mas 4 columnas despues de "entrada":
    entrada_json    el JSON de la Parte A tal como se le paso al sistema (mismo texto que
                    a_texto(): json.dumps con indent=2), sacado del commit de version_sistema.
    entrada_fuente  de donde se saco: git <commit>:corpus/escenarios/<ID>.json (blob ...).
    entrada_sha256  sha256 de entrada_json (UTF-8).
    entrada_tipo    "recuperada": version_sistema no tiene "-dirty", asi que los archivos
                    versionados eran exactamente los del commit.
                    "reconstruida": version_sistema termina en "-dirty" (habia cambios sin
                    commit al correr): el JSON sale del commit pero no se puede asegurar
                    que fuera el usado. Hay que declararlo como limitacion.
  La Parte B (parte_b_referencia) nunca se incluye.
- tiempos_por_caso_v2.csv: las columnas originales (B1_s, B2_s y B3_s siguen sumando todos los
  intentos) con "fallidas" corregida y columnas nuevas. Ver tiempos_v2().
- versiones_corridas.csv: commit del sistema, version de prompts y en que commits el archivo de
  prompts es identico, modelo, temperatura y max_tokens.
- LEEME_v2.md: explica los archivos y lo que hay que declarar.

Antes de escribir revisa que el registro v2 tenga las mismas filas e id_corrida que el original
y que B1_s/B2_s/B3_s coincidan con la suma de los intentos del registro.
"""
import argparse
import csv
import hashlib
import json
import pathlib
import subprocess
import sys
from collections import defaultdict

RAIZ = pathlib.Path(__file__).resolve().parents[1]
CARPETA_ESCENARIOS = "corpus/escenarios"
ARCHIVO_PROMPTS = "vet_agent/prompts.py"
# commits donde se revisa si prompts.py es el mismo archivo que el usado en las corridas
# (016220c: merge de prompts v2, PR #43; 781c212: el commit que cita Investigacion)
COMMITS_PROMPTS = ["016220c", "781c212"]

COLUMNAS_ENTRADA = ["entrada_json", "entrada_fuente", "entrada_sha256", "entrada_tipo"]
BASELINES = ("B1", "B2", "B3")


# ---------- utilidades ----------

def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True, check=True).stdout


def git_bytes(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, check=True).stdout


def leer_csv(ruta: pathlib.Path) -> tuple[list[str], list[dict]]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        lector = csv.DictReader(f)
        return list(lector.fieldnames or []), list(lector)


def escribir_csv(ruta: pathlib.Path, columnas: list[str], filas: list[dict]) -> None:
    # mismo formato que evaluar.py: UTF-8 con BOM, comas, todos los campos entre comillas
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(filas)


def commit_de(version_sistema: str) -> tuple[str, bool]:
    """'5f345b9-dirty' -> ('5f345b9', True)."""
    sucio = version_sistema.endswith("-dirty")
    return version_sistema.removesuffix("-dirty"), sucio


# ---------- entrada_json ----------

_cache_entradas: dict = {}


def entrada_de(escenario: str, version_sistema: str) -> dict:
    """Las 4 columnas nuevas para un escenario y una version del sistema."""
    clave = (escenario, version_sistema)
    if clave in _cache_entradas:
        return _cache_entradas[clave]
    commit, sucio = commit_de(version_sistema)
    ruta = f"{CARPETA_ESCENARIOS}/{escenario}.json"
    try:
        crudo = git_bytes("show", f"{commit}:{ruta}")
        blob = git("rev-parse", f"{commit}:{ruta}").strip()
    except subprocess.CalledProcessError:
        raise SystemExit(f"No encuentro {ruta} en el commit {commit}. ¿Hiciste git fetch?")
    datos = json.loads(crudo.decode("utf-8"))
    if "parte_a_entrada" not in datos:
        raise SystemExit(f"{ruta} en {commit} no tiene parte_a_entrada")
    # mismo texto que a_texto() en agentes/base.py (lo que va despues de "Caso a analizar:")
    texto = json.dumps(datos["parte_a_entrada"], ensure_ascii=False, indent=2)
    if "parte_b" in texto:
        raise SystemExit(f"{ruta}: la Parte A menciona parte_b; revisar antes de entregar")
    cols = {
        "entrada_json": texto,
        "entrada_fuente": f"git {commit}:{ruta} (blob {blob[:12]})",
        "entrada_sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
        "entrada_tipo": "reconstruida" if sucio else "recuperada",
    }
    _cache_entradas[clave] = cols
    return cols


def con_entrada(columnas: list[str], filas: list[dict]) -> tuple[list[str], list[dict]]:
    """Agrega las columnas de entrada justo despues de 'entrada'. No cambia nada mas."""
    if "entrada" not in columnas:
        raise SystemExit("El registro no tiene la columna 'entrada'")
    if any(c in columnas for c in COLUMNAS_ENTRADA):
        raise SystemExit("El registro ya tiene columnas entrada_*; usa el original, no un v2")
    i = columnas.index("entrada") + 1
    nuevas = columnas[:i] + COLUMNAS_ENTRADA + columnas[i:]
    return nuevas, [f | entrada_de(f["entrada"], f["version_sistema"]) for f in filas]


# ---------- tiempos_por_caso ----------

def tiempos_v2(columnas: list[str], tiempos: list[dict], registro: list[dict]) -> tuple[list[str], list[dict], list[str]]:
    """Corrige tiempos_por_caso.csv con los intentos del registro.

    - fallidas: cantidad de intentos fallidos del caso (0 o 1 en esta entrega). Antes solo
      anotaba las versiones cuyo ULTIMO intento fallo, por eso quedaba vacia.
    - fallidas_B1/B2/B3: intentos fallidos de cada version.
    - B1_s, B2_s, B3_s y total_s no cambian: suman todos los intentos (tiempo real del caso).
    - B1_valido_s, B2_valido_s, B3_valido_s: tiempo_s del intento valido, aparte.
    - valido_con_cache: versiones cuyo intento valido fue un reintento. Ese reintento reutilizo
      las respuestas del LLM que ya habian salido bien, asi que su tiempo NO es un caso completo
      y no se compara con los demas: para comparar se usa B?_s.
    Devuelve (columnas, filas, problemas).
    """
    intentos = defaultdict(list)
    for f in registro:
        intentos[(f["escenario"], str(f["repeticion"]))].append(f)
    extra = ["fallidas_B1", "fallidas_B2", "fallidas_B3", "B1_valido_s", "B2_valido_s", "B3_valido_s",
             "valido_con_cache"]
    nuevas = columnas + [c for c in extra if c not in columnas]
    filas, problemas = [], []
    for t in tiempos:
        suyos = intentos.get((t["escenario"], str(t["repeticion"])), [])
        if not suyos:
            problemas.append(f"{t['escenario']} R{t['repeticion']}: no tiene intentos en el registro")
        fila, total_fallidas, con_cache = dict(t), 0, []
        for b in BASELINES:
            de_b = sorted((f for f in suyos if f["baseline"] == b), key=lambda f: int(f["intento"]))
            fallidas = sum(f["estado"] != "ok" for f in de_b)
            validos = [f for f in de_b if f["estado"] == "ok"]
            total_fallidas += fallidas
            fila[f"fallidas_{b}"] = fallidas
            fila[f"{b}_valido_s"] = validos[-1]["tiempo_s"] if validos else ""
            if validos and int(validos[-1]["intento"]) > 1:
                con_cache.append(b)
            # control: B?_s tiene que ser la suma de todos sus intentos
            suma = sum(float(f["tiempo_s"]) for f in de_b)
            if de_b and t.get(f"{b}_s") and abs(float(t[f"{b}_s"]) - suma) > 0.15:
                problemas.append(f"{t['escenario']} R{t['repeticion']} {b}: {b}_s={t[f'{b}_s']} "
                                 f"pero los intentos suman {suma:.1f}")
        fila["fallidas"] = total_fallidas
        fila["valido_con_cache"] = ";".join(con_cache)
        filas.append(fila)
    return nuevas, filas, problemas


# ---------- versiones ----------

def versiones(registro: list[dict]) -> list[dict]:
    """Una fila por combinacion distinta de version_sistema / prompts / modelo del registro."""
    filas, vistas = [], set()
    for f in registro:
        clave = (f["version_sistema"], f["version_prompt_base"], f["modelo_y_version"], f["temperatura"], f["max_tokens"])
        if clave in vistas:
            continue
        vistas.add(clave)
        commit, sucio = commit_de(f["version_sistema"])
        blob = git("rev-parse", f"{commit}:{ARCHIVO_PROMPTS}").strip()
        iguales = []
        for c in COMMITS_PROMPTS:
            try:
                if git("rev-parse", f"{c}:{ARCHIVO_PROMPTS}").strip() == blob:
                    iguales.append(c)
            except subprocess.CalledProcessError:
                pass
        n = sum(1 for g in registro if (g["version_sistema"], g["version_prompt_base"], g["modelo_y_version"],
                                         g["temperatura"], g["max_tokens"]) == clave)
        filas.append({
            "version_sistema": f["version_sistema"],
            "commit_sistema": commit,
            "cambios_sin_commit": "sí" if sucio else "no",
            "version_prompt_base": f["version_prompt_base"],
            "archivo_prompts": ARCHIVO_PROMPTS,
            "blob_prompts": blob[:12],
            "prompts_identicos_en": ";".join([commit] + iguales),
            "modelo_y_version": f["modelo_y_version"],
            "temperatura": f["temperatura"],
            "max_tokens": f["max_tokens"],
            "intentos_en_registro": n,
        })
    return filas


# ---------- LEEME ----------

def leeme(registro: list[dict], tiempos: list[dict], vers: list[dict]) -> str:
    tipos = defaultdict(int)
    for f in registro:
        tipos[entrada_de(f["entrada"], f["version_sistema"])["entrada_tipo"]] += 1
    fallidos = [f for f in registro if f["estado"] != "ok"]
    lineas = [
        "# Entrega v2: trazabilidad completada",
        "",
        "Generado con `python -m vet_agent.trazabilidad`. No se repitio ninguna corrida y los CSV",
        "originales de la carpeta de arriba no se modificaron.",
        "",
        "## Archivos",
        "",
        f"- `registro_corridas_v2.csv` (+ `_B1/_B2/_B3`): los mismos {len(registro)} intentos y `id_corrida`,",
        "  con `entrada_json`, `entrada_fuente`, `entrada_sha256` y `entrada_tipo` despues de `entrada`.",
        "  Solo Parte A; la Parte B no se incluye.",
        f"- `tiempos_por_caso_v2.csv`: {len(tiempos)} casos, `fallidas` corregida, tiempo del intento valido aparte.",
        "- `versiones_corridas.csv`: commit del sistema y de los prompts.",
        "",
        "## Entrada",
        "",
        "`evaluar.py` registra en `entrada` solo el ID del escenario: el JSON no se guardo por corrida.",
        "`cargar_escenarios()` lee `corpus/escenarios/<ID>.json` y entrega solo `parte_a_entrada`;",
        "el agente lo recibe como \"Caso a analizar:\" + `json.dumps(..., indent=2)`.",
        "`entrada_json` se saco con `git show <commit>:corpus/escenarios/<ID>.json`, con el commit de",
        "`version_sistema`.",
        "",
    ]
    for tipo, n in sorted(tipos.items()):
        lineas.append(f"- {n} intentos con entrada **{tipo}**.")
    if tipos.get("reconstruida"):
        lineas += ["", "Hay intentos con `version_sistema` terminada en `-dirty`: declarar como limitacion de trazabilidad."]
    else:
        lineas += ["", "Ningun intento tiene `-dirty`: los archivos versionados eran los del commit. Declarar como",
                   "\"recuperada desde archivo versionado en el commit registrado\" (no almacenada por corrida)."]
    lineas += ["", "## Prompts y sistema", ""]
    for v in vers:
        lineas.append(f"- `{v['version_prompt_base']}` = `{v['archivo_prompts']}` (blob `{v['blob_prompts']}`), "
                      f"identico en los commits {v['prompts_identicos_en']}. Sistema: `{v['version_sistema']}`. "
                      f"{v['intentos_en_registro']} intentos.")
    lineas += ["", "Prompts v2 entraron en `016220c` (PR #43). `781c212` y el sistema usado tienen el mismo archivo.",
               "", "## Tiempos", "",
               "`B1_s`, `B2_s`, `B3_s` y `total_s` suman todos los intentos (tiempo real). `fallidas` = intentos",
               "fallidos del caso. `B?_valido_s` = tiempo del intento valido. Si la version aparece en",
               "`valido_con_cache`, ese intento reutilizo respuestas del intento fallido: su tiempo no es un caso",
               "completo y no se compara con los demas.", ""]
    for f in fallidos:
        lineas.append(f"- Intento fallido: `{f['id_corrida']}`, {f['tiempo_s']} s: {f.get('mensaje_error', '')}")
    return "\n".join(lineas) + "\n"


# ---------- main ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="Version v2 de una entrega de corridas con la trazabilidad completa")
    parser.add_argument("--carpeta", required=True, help="carpeta con registro_corridas.csv y tiempos_por_caso.csv")
    parser.add_argument("--salida", help="carpeta de salida (por defecto <carpeta>/v2)")
    args = parser.parse_args()

    carpeta = pathlib.Path(args.carpeta)
    salida = pathlib.Path(args.salida) if args.salida else carpeta / "v2"
    if salida.exists() and any(salida.iterdir()):
        raise SystemExit(f"{salida} ya tiene archivos; bórralos o usa --salida")

    columnas, registro = leer_csv(carpeta / "registro_corridas.csv")
    cols_v2, registro_v2 = con_entrada(columnas, registro)
    assert [f["id_corrida"] for f in registro_v2] == [f["id_corrida"] for f in registro]
    escribir_csv(salida / "registro_corridas_v2.csv", cols_v2, registro_v2)
    for b in BASELINES:
        ruta = carpeta / f"registro_corridas_{b}.csv"
        if ruta.exists():
            c, filas = leer_csv(ruta)
            c2, filas2 = con_entrada(c, filas)
            escribir_csv(salida / f"registro_corridas_{b}_v2.csv", c2, filas2)

    cols_t, tiempos = leer_csv(carpeta / "tiempos_por_caso.csv")
    cols_t2, tiempos2, problemas = tiempos_v2(cols_t, tiempos, registro)
    escribir_csv(salida / "tiempos_por_caso_v2.csv", cols_t2, tiempos2)

    vers = versiones(registro)
    escribir_csv(salida / "versiones_corridas.csv", list(vers[0]), vers)
    (salida / "LEEME_v2.md").write_text(leeme(registro, tiempos, vers), encoding="utf-8")

    print(f"Listo en {salida}: {len(registro_v2)} intentos, {len(tiempos2)} casos")
    for v in vers:
        print(f"  sistema {v['version_sistema']} · prompts {v['version_prompt_base']} (blob {v['blob_prompts']}, "
              f"igual en {v['prompts_identicos_en']}) · {v['intentos_en_registro']} intentos")
    if problemas:
        print("OJO, revisar:")
        for p in problemas:
            print("  -", p)
        sys.exit(1)


if __name__ == "__main__":
    main()
