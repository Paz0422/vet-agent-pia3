"""Recall@3 y Recall@5 del buscador con los 12 escenarios, y huella de la base indexada.

Solo busca en Chroma: no llama al LLM ni repite corridas. Tiene que correr con la MISMA
chroma_db de las corridas (sin reindexar entre medio).

Uso (raiz del repo):
    python -m vet_agent.rag.recall_escenarios --registro resultados/<entrega>/registro_corridas.csv

Escribe junto al registro, en la carpeta v2/ (o en --salida):

1. recall_por_escenario.csv: una fila por escenario y agente/coleccion, mas una fila
   "todas (B2/B3)" por escenario con lo que reciben B2 y B3 (los top-k de las 3 colecciones).
   - consulta: la misma que arma consulta_del_caso() para ese agente.
   - documentos_esperados: los doc_id de parte_b_referencia.documentos_esperados. La Parte B
     solo se usa aca, para evaluar; nunca se le paso al sistema.
   - esperados_sin_doc_id: los esperados escritos como texto (ej. "Manual AFC ...") que no
     son un doc_id del corpus y no se pueden medir.
   - esperados_fuera_de_la_base: doc_id esperados que no estan indexados en ninguna coleccion.
   - esperados_en_coleccion: los esperados que si estan indexados en ESA coleccion. Es el
     denominador del recall de la fila (en la fila "todas", los indexados en cualquiera).
   - recuperados_top5: los 5 pedazos en orden (doc_id_chunk).
   - recall@3 y recall@5: esperados de la coleccion que aparecen en una busqueda de 3 / de 5
     pedazos, dividido por esperados_en_coleccion. Vacio si la coleccion no tiene esperados.
     Cada k es una busqueda aparte (no se recorta el top-5), porque Chroma busca de forma
     aproximada y con pedazos casi empatados el orden puede cambiar segun cuantos se pidan.
   - recuperados_como_en_corridas: busqueda con K_FRAGMENTOS, igual que contexto_rag().
   - coincide_con_corridas: compara recuperados_como_en_corridas de hoy con la columna fragmentos de las
     corridas validas de B2 de ese escenario, por el texto de cada pedazo (sha256). "sí" = mismos
     pedazos; "sí (mismo texto)" = Chroma eligio otro pedazo con texto identico (empate exacto,
     anotado en empates_texto_identico); "no" = algun texto distinto.
   Las corridas usaron K_FRAGMENTOS pedazos por agente (3 por defecto): Recall@3 mide lo que el
   sistema recibio de verdad; Recall@5 es de referencia.

2. manifiesto_corpus.csv: por coleccion, cantidad de pedazos y documentos, una huella sha256
   (doc_id, chunk y texto de cada pedazo, ordenados) y, para los 15 documentos a los que d04d21a les quito la
   bibliografia, cuantos pedazos todavia tienen un titulo de bibliografia (tiene que ser 0).
   La huella sirve como identificador de la version de la base. No se usa la fecha de
   chroma.sqlite3: Chroma escribe en ese archivo al abrir la base, asi que no dice cuando se indexo.
"""
import argparse
import csv
import hashlib
import json
import pathlib
import re
import subprocess

from vet_agent.agentes.base import COLECCIONES, consulta_del_caso
from vet_agent.config import K_FRAGMENTOS
from vet_agent.rag import buscar
from vet_agent.rag.limpiar_texto import RE_BIBLIOGRAFIA

RAIZ = pathlib.Path(__file__).resolve().parents[2]
K = 5
# los 15 documentos a los que el commit d04d21a les quito la bibliografia
SIN_BIBLIOGRAFIA = ["A15", "C31", "CLI-002", "CLI-106", "D24", "CLI-024", "CLI-074", "D-PM350", "D21",
                    "D25", "D33", "BIO-060", "C30", "C8", "F2"]
# un doc_id del corpus: D3, C34, CLI-020, BIO-063, D-PM350, A15...
RE_DOC_ID = re.compile(r"^[A-Z]{1,4}(-[A-Z]{1,4})?-?\d+$")


# ---------- datos ----------

def leer_registro(ruta: pathlib.Path) -> list[dict]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def escenario_en_commit(escenario: str, commit: str) -> dict:
    texto = subprocess.run(["git", "show", f"{commit}:corpus/escenarios/{escenario}.json"], cwd=RAIZ,
                           capture_output=True, check=True).stdout.decode("utf-8")
    return json.loads(texto)


def separar_esperados(lista: list) -> tuple[list[str], list[str]]:
    """(doc_id, textos que no son doc_id), sin repetir y en orden."""
    ids, textos = [], []
    for e in lista or []:
        e = str(e).strip()
        destino = ids if RE_DOC_ID.match(e) else textos
        if e and e not in destino:
            destino.append(e)
    return ids, textos


def contenido(coleccion: str) -> dict:
    """{'docs': set de doc_id, 'pedazos': [(doc_id, chunk, texto)]} de una coleccion."""
    datos = buscar._cliente.get_collection(coleccion).get(include=["metadatas", "documents"])
    pedazos = [(m["doc_id"], str(m.get("chunk", "")), t) for m, t in zip(datos["metadatas"], datos["documents"])]
    return {"docs": {p[0] for p in pedazos}, "pedazos": pedazos}


# ---------- recall ----------

def recall(esperados: list[str], docs: list[str], k: int) -> str:
    if not esperados:
        return ""
    aciertos = len(set(esperados) & set(docs[:k]))
    return f"{aciertos / len(esperados):.3f}"


def docs_de(pedazos: list[str]) -> list[str]:
    # "D3_2" -> "D3", sin repetir y en orden
    return list(dict.fromkeys(p.rsplit("_", 1)[0] for p in pedazos))


def fragmentos_corridas(registro: list[dict], escenario: str) -> set[frozenset]:
    """Los conjuntos de pedazos que recibio B2 en sus corridas validas de ese escenario."""
    return {frozenset(f["fragmentos"].split(";")) for f in registro
            if f["baseline"] == "B2" and f["escenario"] == escenario and f["estado"] == "ok" and f["fragmentos"]}


def comparar(corridas: set[frozenset], hoy: list[str], textos_de: dict) -> tuple[str, str]:
    """Compara los pedazos de hoy con los de las corridas B2. Devuelve (coincide, empates).

    Algunos pedazos tienen exactamente el mismo texto (ej. frases cortas que se repiten en los
    informes EFSA D-PM213, D-PM258 y D-PM278): quedan a la misma distancia de la consulta y
    Chroma elige cualquiera, sin un orden estable. Por eso se compara el texto (sha256), no el
    doc_id_chunk:
    - "sí": mismos pedazos.
    - "sí (mismo texto)": distinto doc_id_chunk pero mismo texto; el modelo recibio lo mismo.
      En empates se anota cada par distinto como "corrida = hoy".
    - "no": algun texto distinto.
    """
    if not corridas:
        return "sin corridas B2 válidas", ""
    if corridas == {frozenset(hoy)}:
        return "sí", ""
    texto_hoy = {textos_de.get(p) for p in hoy}
    empates, iguales = [], True
    for corrida in corridas:
        if None in texto_hoy or {textos_de.get(p) for p in corrida} != texto_hoy:
            iguales = False
            continue
        solo_corrida, solo_hoy = sorted(corrida - set(hoy)), sorted(set(hoy) - corrida)
        for c in solo_corrida:
            pareja = next((h for h in solo_hoy if textos_de.get(h) == textos_de.get(c)), "?")
            empates.append(f"{c} = {pareja}")
    if not iguales:
        return "no", ""
    return "sí (mismo texto)", "; ".join(dict.fromkeys(empates))


def pedazos_de(coleccion: str, consulta: str, k: int, excluir_porcino: bool) -> list[str]:
    return [f"{p['doc_id']}_{p['chunk']}" for p in buscar.buscar_crudo(coleccion, consulta, k, excluir_porcino)]


def filas_escenario(escenario: str, datos: dict, indexados: dict, todos: set, registro: list[dict],
                    textos_de: dict) -> list[dict]:
    """Cada top-k sale de una busqueda con ESE k (no se recorta el top-5): Chroma busca de forma
    aproximada y, con pedazos casi empatados, pedir 3 o 5 puede cambiar el orden."""
    caso = datos["parte_a_entrada"]
    ids, textos = separar_esperados(datos.get("parte_b_referencia", {}).get("documentos_esperados"))
    fuera = [d for d in ids if d not in todos]
    excluir_porcino = "porcino" not in str(caso.get("especie", "")).lower()  # igual que contexto_rag()
    filas, top_k_todas, top3_todas, top5_todas = [], [], [], []
    base = {"escenario": escenario, "documentos_esperados": ";".join(ids),
            "esperados_sin_doc_id": " | ".join(textos), "esperados_fuera_de_la_base": ";".join(fuera)}
    for agente, coleccion in COLECCIONES.items():
        consulta = consulta_del_caso(agente, caso)
        como_corridas = pedazos_de(coleccion, consulta, K_FRAGMENTOS, excluir_porcino)  # igual que contexto_rag()
        top3 = como_corridas if K_FRAGMENTOS == 3 else pedazos_de(coleccion, consulta, 3, excluir_porcino)
        top5 = pedazos_de(coleccion, consulta, K, excluir_porcino)
        top_k_todas += como_corridas
        top3_todas += top3
        top5_todas += top5
        en_col = [d for d in ids if d in indexados[coleccion]]
        docs3 = [p.rsplit("_", 1)[0] for p in top3]
        docs5 = [p.rsplit("_", 1)[0] for p in top5]
        filas.append(base | {
            "agente": agente, "coleccion": coleccion, "consulta": consulta,
            "esperados_en_coleccion": ";".join(en_col),
            "recuperados_como_en_corridas": ";".join(como_corridas), "recuperados_top5": ";".join(top5),
            "docs_top3": ";".join(docs_de(top3)), "docs_top5": ";".join(docs_de(top5)),
            "recall@3": recall(en_col, docs3, 3), "recall@5": recall(en_col, docs5, 5),
            "coincide_con_corridas": "", "empates_texto_identico": "",
        })
    # fila "todas": lo que reciben B2 y B3 = top-k de cada coleccion juntos
    en_base = [d for d in ids if d in todos]
    coincide, empates = comparar(fragmentos_corridas(registro, escenario), list(dict.fromkeys(top_k_todas)), textos_de)
    docs3 = docs_de(top3_todas)
    docs5 = docs_de(top5_todas)
    filas.append(base | {
        "agente": "todas (B2/B3)", "coleccion": ";".join(COLECCIONES.values()), "consulta": "",
        "esperados_en_coleccion": ";".join(en_base),
        "recuperados_como_en_corridas": ";".join(dict.fromkeys(top_k_todas)), "recuperados_top5": ";".join(top5_todas),
        "docs_top3": ";".join(docs3), "docs_top5": ";".join(docs5),
        "recall@3": recall(en_base, docs3, len(docs3)), "recall@5": recall(en_base, docs5, len(docs5)),
        "coincide_con_corridas": coincide, "empates_texto_identico": empates,
    })
    return filas


# ---------- manifiesto ----------

def manifiesto(contenidos: dict) -> list[dict]:
    filas = []
    for coleccion, c in contenidos.items():
        h = hashlib.sha256()
        for doc_id, chunk, texto in sorted(c["pedazos"]):
            h.update(f"{doc_id}\t{chunk}\t{hashlib.sha256(texto.encode('utf-8')).hexdigest()}\n".encode())
        limpiados = [p for p in c["pedazos"] if p[0] in SIN_BIBLIOGRAFIA]
        con_biblio = sorted({p[0] for p in limpiados
                             if any(RE_BIBLIOGRAFIA.match(l.strip()) for l in p[2].splitlines())})
        filas.append({
            "coleccion": coleccion, "pedazos": len(c["pedazos"]), "documentos": len(c["docs"]),
            "huella_sha256": h.hexdigest(),
            "docs_limpiados_en_coleccion": ";".join(sorted({p[0] for p in limpiados})),
            "docs_limpiados_con_titulo_bibliografia": ";".join(con_biblio),
        })
    total = hashlib.sha256("".join(f["huella_sha256"] for f in filas).encode()).hexdigest()
    for f in filas:
        f["huella_base_completa"] = total
    return filas


def escribir(ruta: pathlib.Path, filas: list[dict]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(filas)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recall@3/@5 por escenario y huella de chroma_db")
    parser.add_argument("--registro", required=True, help="registro_corridas.csv de la entrega")
    parser.add_argument("--salida", help="carpeta de salida (por defecto v2/ junto al registro)")
    args = parser.parse_args()

    ruta_registro = pathlib.Path(args.registro)
    salida = pathlib.Path(args.salida) if args.salida else ruta_registro.parent / "v2"
    registro = leer_registro(ruta_registro)
    commits = {f["version_sistema"].removesuffix("-dirty") for f in registro}
    if len(commits) != 1:
        raise SystemExit(f"El registro tiene varias versiones del sistema: {commits}")
    commit = commits.pop()

    contenidos = {c: contenido(c) for c in COLECCIONES.values()}
    indexados = {c: v["docs"] for c, v in contenidos.items()}
    todos = set().union(*indexados.values())

    textos_de = {f"{d}_{c}": hashlib.sha256(t.encode("utf-8")).hexdigest()
                 for v in contenidos.values() for d, c, t in v["pedazos"]}
    filas = []
    for escenario in sorted({f["escenario"] for f in registro}):
        filas += filas_escenario(escenario, escenario_en_commit(escenario, commit), indexados, todos, registro,
                                 textos_de)
    escribir(salida / "recall_por_escenario.csv", filas)
    man = manifiesto(contenidos)
    escribir(salida / "manifiesto_corpus.csv", man)

    print(f"Listo en {salida}: {len(filas)} filas de recall (K_FRAGMENTOS={K_FRAGMENTOS})")
    for m in man:
        print(f"  {m['coleccion']}: {m['pedazos']} pedazos, {m['documentos']} documentos, huella {m['huella_sha256'][:12]}")
    print(f"  huella de la base completa: {man[0]['huella_base_completa'][:16]}")
    con_biblio = [m["docs_limpiados_con_titulo_bibliografia"] for m in man if m["docs_limpiados_con_titulo_bibliografia"]]
    if con_biblio:
        print("OJO: documentos limpiados que todavia tienen titulo de bibliografia:", con_biblio)
    distintos = [f["escenario"] for f in filas if f["coincide_con_corridas"] == "no"]
    if distintos:
        print("OJO: la base de hoy no devuelve lo mismo que en las corridas para:", distintos)
    else:
        print("La base de hoy devuelve los mismos pedazos (mismo texto) que recibio B2 en las corridas.")
    for f in filas:
        if f["empates_texto_identico"]:
            print(f"  {f['escenario']}: empate exacto de texto, Chroma eligio otro pedazo igual: {f['empates_texto_identico']}")


if __name__ == "__main__":
    main()
