"""Etapa 2 - Validacion de los .md descargados, antes de indexar.

Uso (raiz del repo, despues de descargar_md):
    python -m vet_agent.rag.validar

Revisa corpus/md/<coleccion>/*.md y escribe corpus/md/reporte_validacion.xlsx
con un estado por archivo. Solo los "OK" se indexan.

Estados (se aplica el primero que calce, en este orden):
  Excluido          ya no esta en el plan del prefiltro (por ejemplo, BIO-139).
  Escaneado         PDF con menos de 100 caracteres por pagina.
  Pagina equivocada el titulo no aparece en las primeras 3.000 palabras Y la
                    pagina tiene menos de 150 palabras o es casi puro links.
  Duplicado         mismo texto (normalizado) que otro archivo.
  Con pago          aparece "Purchase access", "Access through your institution",
                    "Log in", "Subscribe" o "Comprar".
  Solo resumen      tiene Abstract pero no Methods/Results/Discussion y menos
                    de 1.500 palabras.
  Revisar titulo    el titulo no aparece y tiene 1.500 palabras o menos
                    (con mas de 1.500 palabras se acepta como OK).
  OK                paso todo.
Los umbrales estan arriba, en mayusculas, para ajustarlos facil.
"""
import hashlib
import pathlib
import random
import re
import time

import pandas as pd
from rapidfuzz import fuzz, utils

from vet_agent.rag import cabecera
from vet_agent.rag.prefiltro import sin_formulas

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CARPETA = RAIZ / "corpus" / "md"
COLECCIONES = ("climatico", "diagnostico", "bioseguridad")
SALIDA = CARPETA / "reporte_validacion.xlsx"

CHARS_POR_PAGINA = 100       # menos = PDF escaneado (sin capa de texto)
UMBRAL_TITULO = 80           # rapidfuzz partial_ratio: el titulo "aparece" desde aqui
PALABRAS_TITULO = 3000       # donde se busca el titulo
PALABRAS_MINIMAS = 150       # menos = pagina casi vacia
PALABRAS_TITULO_OK = 1500    # si el titulo no calza pero hay mas texto que esto, se acepta
LINEAS_CORTAS = 0.6          # proporcion de lineas de <= 5 palabras = casi puro links/menu
PALABRAS_RESUMEN = 1500      # "solo resumen" si tiene menos que esto
FRASES_PAGO = ["Purchase access", "Access through your institution", "Log in", "Subscribe", "Comprar"]

RE_PAGO = re.compile(r"\b(" + "|".join(map(re.escape, FRASES_PAGO)) + r")\b", re.IGNORECASE)
RE_ABSTRACT = re.compile(r"^[#*\s\d.]*(abstract|resumen|summary)\b", re.IGNORECASE | re.MULTILINE)
RE_SECCIONES = re.compile(
    r"^[#*\s\d.]*(methods?|materials? and methods|methodology|results?|discussion|"
    r"m[ée]todos?|materiales y m[ée]todos|metodolog[íi]a|resultados?|discusi[óo]n)\b",
    re.IGNORECASE | re.MULTILINE)

EXCLUIDO, ESCANEADO, DUPLICADO, CON_PAGO = "Excluido", "Escaneado", "Duplicado", "Con pago"
PAGINA_EQUIVOCADA, SOLO_RESUMEN, REVISAR_TITULO, OK = "Página equivocada", "Solo resumen", "Revisar título", "OK"


def huella(texto: str) -> str:
    """Hash del texto normalizado (minusculas, solo letras y numeros)."""
    return hashlib.sha1(" ".join(re.findall(r"\w+", texto.lower())).encode()).hexdigest()


def similitud_titulo(cuerpo: str, *titulos: str) -> int:
    """Que tanto aparece el mejor de los titulos en las primeras 3.000 palabras (0-100)."""
    inicio = utils.default_process(" ".join(cuerpo.split()[:PALABRAS_TITULO]))
    return max((int(fuzz.partial_ratio(utils.default_process(t), inicio)) for t in titulos if t.strip()), default=0)


def revisar(ruta: pathlib.Path) -> dict:
    meta, cuerpo = cabecera.leer(ruta)
    palabras = len(cuerpo.split())
    lineas = [l for l in cuerpo.splitlines() if l.strip()]
    cortas = sum(1 for l in lineas if len(l.split()) <= 5) / len(lineas) if lineas else 1.0
    paginas = int(meta.get("paginas") or 0)
    pago = RE_PAGO.search(cuerpo)
    return {
        "id": meta.get("id", ruta.stem), "coleccion": ruta.parent.name, "ruta": meta.get("ruta", ""),
        "enfermedad": meta.get("enfermedad", ""), "palabras_antes_limpieza": meta.get("palabras_antes", ""),
        "palabras": palabras, "paginas": paginas or "",
        "chars_por_pagina": round(len(cuerpo) / paginas) if paginas else "",
        "similitud_titulo": similitud_titulo(cuerpo, meta.get("titulo_excel", ""), meta.get("titulo", "")),
        "lineas_cortas": round(cortas, 2), "frase_pago": pago.group(0) if pago else "",
        "tiene_abstract": bool(RE_ABSTRACT.search(cuerpo)), "tiene_secciones": bool(RE_SECCIONES.search(cuerpo)),
        "huella": huella(cuerpo), "titulo": meta.get("titulo", ""), "archivo": str(ruta.relative_to(RAIZ)),
        "_inicio": [l[:160] for l in lineas[:3]],
    }


def clasificar(r: dict, primero_con_huella: dict, plan: set[str]) -> tuple[str, str]:
    if f"{r['coleccion']}/{r['id']}" not in plan:
        return EXCLUIDO, "ya no está en el plan del prefiltro (excluido o sin ruta)"
    if r["paginas"] and r["chars_por_pagina"] < CHARS_POR_PAGINA:
        return ESCANEADO, f"{r['chars_por_pagina']} caracteres por página (< {CHARS_POR_PAGINA})"
    titulo_no_esta = r["similitud_titulo"] < UMBRAL_TITULO
    if titulo_no_esta and (r["palabras"] < PALABRAS_MINIMAS or r["lineas_cortas"] >= LINEAS_CORTAS):
        razon = (f"{r['palabras']} palabras" if r["palabras"] < PALABRAS_MINIMAS
                 else f"{r['lineas_cortas']:.0%} de líneas cortas (casi puro links)")
        return PAGINA_EQUIVOCADA, f"título {r['similitud_titulo']}/100 y {razon}"
    if primero_con_huella[r["huella"]] != r["id"]:
        return DUPLICADO, f"mismo texto que {primero_con_huella[r['huella']]}"
    if r["frase_pago"]:
        return CON_PAGO, f"aparece \"{r['frase_pago']}\""
    if r["tiene_abstract"] and not r["tiene_secciones"] and r["palabras"] < PALABRAS_RESUMEN:
        return SOLO_RESUMEN, f"Abstract sin Methods/Results/Discussion y {r['palabras']} palabras"
    if titulo_no_esta and r["palabras"] <= PALABRAS_TITULO_OK:
        return REVISAR_TITULO, f"título {r['similitud_titulo']}/100 (< {UMBRAL_TITULO}) y {r['palabras']} palabras"
    if titulo_no_esta:
        return OK, f"título {r['similitud_titulo']}/100, aceptado por tener {r['palabras']} palabras (> {PALABRAS_TITULO_OK})"
    return OK, ""


def main() -> None:
    plan = pd.read_excel(CARPETA / "prefiltro.xlsx", sheet_name="Fuentes", dtype=str).fillna("")
    plan = {f"{c}/{i}" for c, i, d in zip(plan.coleccion, plan.id, plan.descargar) if d == "sí"}
    archivos = sorted(p for c in COLECCIONES for p in (CARPETA / c).glob("*.md"))
    filas = [revisar(p) for p in archivos]
    primero = {}
    for r in filas:
        if f"{r['coleccion']}/{r['id']}" in plan:
            primero.setdefault(r["huella"], r["id"])
    for r in filas:
        primero.setdefault(r["huella"], r["id"])
        r["estado"], r["motivo"] = clasificar(r, primero, plan)

    df = pd.DataFrame(filas)
    fecha = time.strftime("%Y-%m-%d %H:%M")
    resumen = pd.crosstab(df.estado, df.coleccion, margins=True, margins_name="Total")
    criterios = pd.DataFrame({"criterio": ["CHARS_POR_PAGINA", "UMBRAL_TITULO", "PALABRAS_TITULO", "PALABRAS_MINIMAS",
                                           "PALABRAS_TITULO_OK", "LINEAS_CORTAS", "PALABRAS_RESUMEN", "FRASES_PAGO"],
                              "valor": [CHARS_POR_PAGINA, UMBRAL_TITULO, PALABRAS_TITULO, PALABRAS_MINIMAS,
                                        PALABRAS_TITULO_OK, LINEAS_CORTAS, PALABRAS_RESUMEN, ", ".join(FRASES_PAGO)]})
    with pd.ExcelWriter(SALIDA, engine="openpyxl") as xl:
        resumen.to_excel(xl, sheet_name="Resumen", startrow=1)
        xl.sheets["Resumen"].cell(1, 1, f"Archivos por estado y colección (validación del {fecha})")
        sin_formulas(df.drop(columns=["_inicio", "huella"])).to_excel(xl, sheet_name="Archivos", index=False)
        criterios.to_excel(xl, sheet_name="Criterios", index=False)

    print(f"{len(df)} archivos validados ({fecha}):")
    print(resumen.to_string())
    azar = random.Random(2026)  # semilla fija: los mismos ejemplos si se vuelve a correr
    for estado in (OK, PAGINA_EQUIVOCADA):
        grupo = [r for r in filas if r["estado"] == estado]
        print(f"\n===== 5 '{estado}' al azar (de {len(grupo)}) =====")
        for r in azar.sample(grupo, min(5, len(grupo))):
            print(f"\n[{r['id']} | {r['coleccion']} | {r['ruta']} | {r['palabras']} palabras | título {r['similitud_titulo']}/100]"
                  + (f" {r['motivo']}" if r["motivo"] else ""))
            for linea in r["_inicio"]:
                print("   " + linea)
    cortos = [r for r in filas if r["estado"] == REVISAR_TITULO]
    print(f"\n===== 'Revisar título' para revisar a mano ({len(cortos)}) =====")
    for r in cortos:
        print(f"\n[{r['id']} | {r['coleccion']} | {r['ruta']} | {r['palabras']} palabras | título {r['similitud_titulo']}/100]")
        print(f"   Título esperado: {r['titulo'][:150]}")
        for linea in r["_inicio"]:
            print("   " + linea)
    print(f"\nReporte: {SALIDA}")


if __name__ == "__main__":
    main()
