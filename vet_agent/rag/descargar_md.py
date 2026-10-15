"""Etapa 2 - Descarga en Markdown de las fuentes que aprobo el prefiltro.

Uso (raiz del repo, despues de correr prefiltro):
    python -m vet_agent.rag.descargar_md

Lee corpus/md/prefiltro.xlsx (filas con descargar = si) y guarda cada fuente
en corpus/md/<coleccion>/<id>.md con una cabecera de metadatos. La ruta ya
la eligio el prefiltro:
  a) Europe PMC   -> XML completo de la API, pasado a Markdown (sin bibliografia)
  b) PDF OpenAlex -> PDF a carpeta temporal + pymupdf4llm
  c) Web          -> trafilatura (solo el contenido principal)
  d) PDF directo  -> PDF a carpeta temporal + pymupdf4llm
Si falla, se anota en corpus/md/errores.csv y se sigue con la siguiente.
No se intenta saltar bloqueos, CAPTCHAs, logins ni paywalls.

Hasta 8 descargas a la vez, pero un solo pedido a la vez por dominio
(con al menos 2 s entre pedidos; eso lo hace red.py). Si el .md ya existe,
se salta: asi se puede cortar y reanudar.
"""
import csv
import html
import pathlib
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import defusedxml.ElementTree as ET  # como xml.etree, pero rechaza XML malicioso
import pandas as pd
import pymupdf
import pymupdf4llm
import trafilatura
from tqdm import tqdm

from vet_agent.rag import cabecera, red
from vet_agent.rag.prefiltro import RE_META_REFRESH, RUTA_EPMC, RUTA_PDF, RUTA_PDF_OA, RUTA_WEB

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PREFILTRO = RAIZ / "corpus" / "md" / "prefiltro.xlsx"
SALIDA = RAIZ / "corpus" / "md"
HILOS = 8
TIMEOUT = 30
REINTENTOS = 2
MAX_BYTES = 80 * 1024 * 1024   # PDF mas grandes que esto no se bajan
SIN_TEXTO = "Sin texto"

_candado_pdf = threading.Lock()  # PyMuPDF no es seguro con varios hilos a la vez


# ---------------------------------------------------------------------------
# Bajar bytes (siempre a traves de red.py)
# ---------------------------------------------------------------------------

def bajar(url: str) -> tuple[str, bytes, str]:
    """(url_final, contenido, content_type), con tope de tamano."""
    url_final, r = red.seguir(url, timeout=TIMEOUT, stream=True, reintentos=REINTENTOS)
    try:
        if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
            raise red.FalloRed(red.CAIDO, f"archivo de más de {MAX_BYTES // 2**20} MB")
        partes, total = [], 0
        for trozo in r.iter_content(64 * 1024):
            total += len(trozo)
            if total > MAX_BYTES:
                raise red.FalloRed(red.CAIDO, f"archivo de más de {MAX_BYTES // 2**20} MB")
            partes.append(trozo)
    finally:
        r.close()
    return url_final, b"".join(partes), r.headers.get("Content-Type", "").lower()


# ---------------------------------------------------------------------------
# Conversores a Markdown
# ---------------------------------------------------------------------------

def _texto(e) -> str:
    return " ".join("".join(e.itertext()).split()) if e is not None else ""


def _tabla(tw) -> str:
    """<table-wrap> de JATS -> tabla Markdown con su titulo."""
    titulo = " ".join(t for t in (_texto(tw.find("label")), _texto(tw.find("caption"))) if t)
    filas = [[_texto(c).replace("|", "/") for c in tr if c.tag in ("th", "td")] for tr in tw.iter("tr")]
    filas = [f for f in filas if any(f)]
    if not filas:
        return f"**{titulo}**" if titulo else ""
    ancho = max(len(f) for f in filas)
    filas = [f + [""] * (ancho - len(f)) for f in filas]
    md = ["| " + " | ".join(filas[0]) + " |", "|" + " --- |" * ancho]
    md += ["| " + " | ".join(f) + " |" for f in filas[1:]]
    return (f"**{titulo}**\n\n" if titulo else "") + "\n".join(md)


def _bloques(elem, nivel: int) -> list[str]:
    """Secciones, parrafos, listas y tablas de un elemento JATS, en orden."""
    salida = []
    for hijo in elem:
        if hijo.tag == "sec":
            titulo = _texto(hijo.find("title"))
            if titulo:
                salida.append("#" * min(nivel, 6) + " " + titulo)
            salida += _bloques(hijo, nivel + 1)
        elif hijo.tag == "p":
            salida.append(_texto(hijo))
        elif hijo.tag == "table-wrap":
            salida.append(_tabla(hijo))
        elif hijo.tag == "list":
            salida += ["- " + _texto(item) for item in hijo.iter("list-item")]
        elif hijo.tag == "fig":
            leyenda = " ".join(t for t in (_texto(hijo.find("label")), _texto(hijo.find("caption"))) if t)
            if leyenda:
                salida.append(f"*{leyenda}*")
        elif hijo.tag in ("boxed-text", "disp-quote", "statement", "def-list"):
            salida += _bloques(hijo, nivel)
    return salida


def jats_a_md(xml: bytes) -> str:
    """XML de Europe PMC -> Markdown: titulo, resumen, cuerpo y tablas (sin bibliografia)."""
    raiz = ET.fromstring(xml)
    partes = ["# " + _texto(raiz.find(".//article-meta//article-title"))]
    for resumen in raiz.findall(".//article-meta/abstract"):
        partes.append("## Abstract")
        partes += _bloques(resumen, 3) or [_texto(resumen)]
    cuerpo = raiz.find("body")
    if cuerpo is not None:
        partes += _bloques(cuerpo, 2)
    flotantes = raiz.find("floats-group")  # en Europe PMC las tablas suelen venir aca, al final
    if flotantes is not None:
        tablas = [_tabla(tw) for tw in flotantes.iter("table-wrap")]
        if tablas:
            partes += ["## Tablas"] + tablas
    return "\n\n".join(p for p in partes if p.strip() and p.strip() != "#")


def pdf_a_md(contenido: bytes) -> tuple[str, int]:
    """(markdown, paginas). El PDF se guarda en una carpeta temporal que se borra sola."""
    if contenido[:5] != b"%PDF-":
        raise red.FalloRed(SIN_TEXTO, "se esperaba un PDF y llegó otra cosa")
    with tempfile.TemporaryDirectory() as carpeta, _candado_pdf:
        ruta = pathlib.Path(carpeta) / "documento.pdf"
        ruta.write_bytes(contenido)
        with pymupdf.open(ruta) as doc:
            paginas = doc.page_count
        md = pymupdf4llm.to_markdown(str(ruta), show_progress=False)
    return md, paginas


def web_a_md(url_final: str, contenido: bytes) -> tuple[str, str]:
    """(markdown, url_final). Sigue una vez un <meta refresh> de pagina intermedia."""
    m = RE_META_REFRESH.search(contenido[:50_000])
    if m:
        destino = urljoin(url_final, html.unescape(m.group(1).decode("utf-8", "replace")))
        url_final, contenido, _ = bajar(destino)
    if red.es_antibots(contenido[:50_000]):
        raise red.FalloRed("Bloqueo anti-bots", "la página es un CAPTCHA o control anti-bots")
    md = trafilatura.extract(contenido, url=url_final, output_format="markdown", include_tables=True,
                             include_links=False, include_images=False, include_comments=False)
    return md or "", url_final


# ---------------------------------------------------------------------------
# Una fuente
# ---------------------------------------------------------------------------

def destino(f: dict) -> pathlib.Path:
    return SALIDA / f["coleccion"] / f"{f['id']}.md"


def descargar(f: dict) -> int:
    """Baja una fuente y escribe su .md. Devuelve los caracteres del cuerpo."""
    url_final, contenido, tipo = bajar(f["url_descarga"])
    paginas = ""
    if f["ruta"] == RUTA_EPMC:
        md = jats_a_md(contenido)
    elif f["ruta"] in (RUTA_PDF, RUTA_PDF_OA) or "pdf" in tipo or contenido[:5] == b"%PDF-":
        md, paginas = pdf_a_md(contenido)
    else:
        md, url_final = web_a_md(url_final, contenido)
    if not md.strip():
        raise red.FalloRed(SIN_TEXTO, "no se pudo sacar texto")
    meta = {
        "id": f["id"], "coleccion": f["coleccion"], "titulo": f["meta_titulo"], "titulo_excel": f["titulo_excel"],
        "institucion": f["meta_institucion"], "anio": f["meta_anio"], "enfermedad": f["enfermedad"],
        "doi": f["doi"], "url_origen": f["url"], "url_descarga": url_final, "ruta": f["ruta"],
        "paginas": paginas, "fuente_metadatos": f["fuente_metadatos"], "fecha_descarga": time.strftime("%Y-%m-%d"),
    }
    salida = destino(f)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(cabecera.escribir(meta, md), encoding="utf-8")
    return len(md)


def main() -> None:
    fuentes = pd.read_excel(PREFILTRO, sheet_name="Fuentes", dtype=str).fillna("")
    fuentes = fuentes[fuentes.descargar == "sí"].to_dict("records")
    pendientes = [f for f in fuentes if not destino(f).exists()]
    print(f"{len(fuentes)} fuentes para descargar | ya bajadas {len(fuentes) - len(pendientes)} | pendientes {len(pendientes)}")

    inicio = time.monotonic()
    errores, ok = [], 0
    barra = tqdm(total=len(pendientes), unit="doc", desc="Descargando")
    with ThreadPoolExecutor(HILOS) as pool:
        tareas = {pool.submit(descargar, f): f for f in pendientes}
        for t in as_completed(tareas):
            f = tareas[t]
            try:
                t.result()
                ok += 1
            except red.FalloRed as e:
                errores.append({"id": f["id"], "coleccion": f["coleccion"], "ruta": f["ruta"],
                                "url": f["url_descarga"], "estado": e.estado, "motivo": e.motivo})
            except Exception as e:  # nunca se detiene: se anota y sigue
                errores.append({"id": f["id"], "coleccion": f["coleccion"], "ruta": f["ruta"],
                                "url": f["url_descarga"], "estado": "Error", "motivo": f"{type(e).__name__}: {e}"})
            barra.set_postfix(ok=ok, fallidos=len(errores))
            barra.update()
    barra.close()

    with open(SALIDA / "errores.csv", "w", encoding="utf-8", newline="") as arch:
        w = csv.DictWriter(arch, fieldnames=["id", "coleccion", "ruta", "url", "estado", "motivo"])
        w.writeheader()
        w.writerows(sorted(errores, key=lambda e: (e["coleccion"], e["id"])))
    print(f"\n{ok} bajadas y {len(errores)} fallidas en {time.monotonic() - inicio:.0f} s "
          f"(errores en {SALIDA / 'errores.csv'})")


if __name__ == "__main__":
    main()
