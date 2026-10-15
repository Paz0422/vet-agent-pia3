"""Etapa 2 - Descarga en Markdown de las fuentes que aprobo el prefiltro.

Uso (raiz del repo, despues de correr prefiltro):
    python -m vet_agent.rag.descargar_md

Lee corpus/md/prefiltro.xlsx (filas con descargar = si) y guarda cada fuente
en corpus/md/<coleccion>/<id>.md con una cabecera de metadatos. La ruta ya
la eligio el prefiltro (Europe PMC siempre primero):
  a) Europe PMC   -> XML completo de la API, pasado a Markdown (sin bibliografia)
  b) PDF OpenAlex -> PDF a corpus/md/_tmp_pdf/ + pymupdf4llm
  c) Web          -> trafilatura (solo el contenido principal)
  d) PDF directo  -> PDF a corpus/md/_tmp_pdf/ + pymupdf4llm
Si falla, se anota en corpus/md/errores.csv y se sigue con la siguiente.
No se intenta saltar bloqueos, CAPTCHAs, logins ni paywalls.

Como se reparte el trabajo:
- Descargas: una cola por dominio y hasta 8 dominios a la vez; dentro de un
  dominio, una descarga tras otra (al menos 2 s entre pedidos, 1 s en las APIs
  de Europe PMC y OpenAlex, y una sesion HTTP por dominio; eso lo hace red.py).
  Las colas mas largas (Europe PMC) parten primero, asi no frenan a las demas.
- El XML/HTML ya bajado queda en corpus/md/_cache/descargas/: si hay que volver
  a convertir o limpiar, no se pide de nuevo a internet (--sin-cache lo ignora).
- Conversion de PDF: en un grupo de procesos aparte (usa CPU, no red), asi
  las descargas no esperan a que termine cada conversion.
- Los PDF se bajan por partes al disco y se borran apenas se convierten.
- Archivos de mas de 50 MB no se bajan: quedan en errores.csv como "Muy grande".
- Si el .md ya existe y se bajo desde la misma URL, se salta (para reanudar).
  Si la ruta o la URL cambio en el prefiltro, se vuelve a bajar.
El .md queda "crudo": despues se limpia con limpiar_texto.
"""
import argparse
import csv
import html
import json
import os
import pathlib
import queue
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

import defusedxml.ElementTree as ET  # como xml.etree, pero rechaza XML malicioso
import pandas as pd
import trafilatura
from tqdm import tqdm

from vet_agent.rag import cabecera, red
from vet_agent.rag.prefiltro import RE_META_REFRESH, RUTA_EPMC, RUTA_PDF, RUTA_PDF_OA

RAIZ = pathlib.Path(__file__).resolve().parents[2]
PREFILTRO = RAIZ / "corpus" / "md" / "prefiltro.xlsx"
SALIDA = RAIZ / "corpus" / "md"
TMP_PDF = SALIDA / "_tmp_pdf"     # PDF a medio convertir (se borran solos; limpiar.py borra restos)
LOGS = SALIDA / "_logs"
CACHE_DESCARGAS = SALIDA / "_cache" / "descargas"  # XML/HTML ya bajados (no PDF)
USAR_CACHE = True
HILOS = 8                         # dominios a la vez (cada dominio, una descarga a la vez)
PROCESOS = max(1, min(4, (os.cpu_count() or 2) - 1))  # conversiones de PDF a la vez
TIMEOUT = 30
REINTENTOS = 2
MAX_BYTES = 50 * 1024 * 1024      # mas grande = "Muy grande" (libros o manuales completos)
SIN_TEXTO, MUY_GRANDE = "Sin texto", "Muy grande"
SEGUNDOS_CORRIDA_ANTERIOR = 1293  # 21,5 min: primera descarga completa (271 fuentes)


# ---------------------------------------------------------------------------
# Bajar (siempre a traves de red.py)
# ---------------------------------------------------------------------------

def bajar(url: str, archivo_pdf: pathlib.Path) -> tuple[str, bytes | None]:
    """Baja por partes. Si es PDF lo escribe en archivo_pdf y devuelve (url_final, None);
    si no, devuelve (url_final, contenido)."""
    url_final, r = red.seguir(url, timeout=TIMEOUT, stream=True, reintentos=REINTENTOS)
    try:
        if int(r.headers.get("Content-Length") or 0) > MAX_BYTES:
            raise red.FalloRed(MUY_GRANDE, f"pesa más de {MAX_BYTES // 2**20} MB (revisar a mano)")
        trozos = r.iter_content(256 * 1024)
        primero = next(trozos, b"")
        es_pdf = "pdf" in r.headers.get("Content-Type", "").lower() or primero[:5] == b"%PDF-"
        total = len(primero)
        if es_pdf:
            archivo_pdf.parent.mkdir(parents=True, exist_ok=True)
            with open(archivo_pdf, "wb") as salida:
                salida.write(primero)
                for trozo in trozos:
                    total += len(trozo)
                    if total > MAX_BYTES:
                        raise red.FalloRed(MUY_GRANDE, f"pesa más de {MAX_BYTES // 2**20} MB (revisar a mano)")
                    salida.write(trozo)
            return url_final, None
        partes = [primero]
        for trozo in trozos:
            total += len(trozo)
            if total > MAX_BYTES:
                raise red.FalloRed(MUY_GRANDE, f"pesa más de {MAX_BYTES // 2**20} MB (revisar a mano)")
            partes.append(trozo)
        return url_final, b"".join(partes)
    except red.FalloRed:
        archivo_pdf.unlink(missing_ok=True)
        raise
    finally:
        r.close()


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


def convertir_pdf(ruta: str) -> tuple[str, int]:
    """(markdown, paginas). Corre en un proceso aparte (usa CPU, no red)."""
    import pymupdf  # se importa aca: solo lo necesitan los procesos de conversion
    import pymupdf4llm
    with open(ruta, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError("se esperaba un PDF y llegó otra cosa")
    with pymupdf.open(ruta) as doc:
        paginas = doc.page_count
    return pymupdf4llm.to_markdown(ruta, show_progress=False), paginas


def web_a_md(url_final: str, contenido: bytes, archivo_pdf: pathlib.Path) -> tuple[str | None, str]:
    """(markdown, url_final). Sigue una vez un <meta refresh>. Si al final llega un PDF, md = None."""
    m = RE_META_REFRESH.search(contenido[:50_000])
    if m:
        destino = urljoin(url_final, html.unescape(m.group(1).decode("utf-8", "replace")))
        url_final, contenido = bajar(destino, archivo_pdf)
        if contenido is None:
            return None, url_final
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


def ya_bajada(f: dict) -> bool:
    """El .md existe y se bajo con la misma ruta y URL que pide el prefiltro."""
    if not destino(f).exists():
        return False
    meta, _ = cabecera.leer(destino(f))
    return meta.get("url_plan") == f["url_descarga"] and meta.get("ruta") == f["ruta"]


def _cache_descarga(f: dict) -> tuple[pathlib.Path, pathlib.Path]:
    nombre = f"{f['coleccion']}_{f['id']}"
    return CACHE_DESCARGAS / f"{nombre}.bin", CACHE_DESCARGAS / f"{nombre}.json"


def bajar_con_cache(f: dict, archivo_pdf: pathlib.Path) -> tuple[str, bytes | None]:
    """Como bajar(), pero el XML/HTML ya bajado desde la misma URL se lee del disco.

    Los PDF no se guardan en el cache (pesan mucho y no van en el repo).
    """
    datos, info = _cache_descarga(f)
    if USAR_CACHE and datos.exists() and info.exists():
        guardado = json.loads(info.read_text(encoding="utf-8"))
        if guardado.get("url_plan") == f["url_descarga"]:
            return guardado["url_final"], datos.read_bytes()
    url_final, contenido = bajar(f["url_descarga"], archivo_pdf)
    if contenido is not None:
        CACHE_DESCARGAS.mkdir(parents=True, exist_ok=True)
        datos.write_bytes(contenido)
        info.write_text(json.dumps({"url_plan": f["url_descarga"], "url_final": url_final}), encoding="utf-8")
    return url_final, contenido


def bajar_fuente(f: dict) -> tuple[str, object, str]:
    """Paso de red de una fuente: ("md", markdown, url_final) o ("pdf", ruta_pdf, url_final)."""
    archivo_pdf = TMP_PDF / f"{f['coleccion']}_{f['id']}.pdf"
    url_final, contenido = bajar_con_cache(f, archivo_pdf)
    if contenido is None:
        return "pdf", archivo_pdf, url_final
    if f["ruta"] == RUTA_EPMC:
        return "md", jats_a_md(contenido), url_final
    if f["ruta"] in (RUTA_PDF, RUTA_PDF_OA):
        raise red.FalloRed(SIN_TEXTO, "se esperaba un PDF y llegó una página web")
    md, url_final = web_a_md(url_final, contenido, archivo_pdf)
    return ("pdf", archivo_pdf, url_final) if md is None else ("md", md, url_final)


def escribir(f: dict, md: str, url_final: str, paginas) -> None:
    if not md.strip():
        raise red.FalloRed(SIN_TEXTO, "no se pudo sacar texto")
    meta = {
        "id": f["id"], "coleccion": f["coleccion"], "titulo": f["meta_titulo"], "titulo_excel": f["titulo_excel"],
        "institucion": f["meta_institucion"], "anio": f["meta_anio"], "enfermedad": f["enfermedad"],
        "doi": f["doi"], "url_origen": f["url"], "url_plan": f["url_descarga"], "url_descarga": url_final,
        "ruta": f["ruta"], "paginas": paginas, "fuente_metadatos": f["fuente_metadatos"],
        "fecha_descarga": time.strftime("%Y-%m-%d"),
    }
    salida = destino(f)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(cabecera.escribir(meta, md), encoding="utf-8")


def _error(f: dict, estado: str, motivo: str) -> dict:
    return {"id": f["id"], "coleccion": f["coleccion"], "titulo": f["titulo_excel"], "ruta": f["ruta"],
            "url": f["url_descarga"], "estado": estado, "motivo": motivo}


def _trabajar_cola(fuentes: list[dict], resultados: queue.Queue) -> None:
    """Un trabajador por dominio: baja sus fuentes una tras otra (red.py pone la pausa)."""
    for f in fuentes:
        try:
            resultados.put(("bajada", f, bajar_fuente(f)))
        except red.FalloRed as e:
            resultados.put(("error", f, (e.estado, e.motivo)))
        except Exception as e:  # nunca se detiene: se anota y sigue
            resultados.put(("error", f, ("Error", f"{type(e).__name__}: {e}")))


def main() -> None:
    global SALIDA, TMP_PDF, USAR_CACHE
    parser = argparse.ArgumentParser(description="Descarga en Markdown de las fuentes del prefiltro")
    parser.add_argument("--salida", help="otra carpeta de salida (para pruebas; por defecto corpus/md)")
    parser.add_argument("--sin-cache", action="store_true", help="no leer el cache de descargas")
    args = parser.parse_args()
    if args.salida:
        SALIDA, TMP_PDF = pathlib.Path(args.salida), pathlib.Path(args.salida) / "_tmp_pdf"
    USAR_CACHE = not args.sin_cache

    fuentes = pd.read_excel(PREFILTRO, sheet_name="Fuentes", dtype=str).fillna("")
    fuentes = fuentes[fuentes.descargar == "sí"].to_dict("records")
    pendientes = [f for f in fuentes if not ya_bajada(f)]
    colas = defaultdict(list)  # una cola por dominio
    for f in pendientes:
        colas[urlparse(f["url_descarga"]).netloc].append(f)
    orden = sorted(colas, key=lambda d: -len(colas[d]))  # las colas mas largas parten primero
    print(f"{len(fuentes)} fuentes para descargar | ya bajadas {len(fuentes) - len(pendientes)} | "
          f"pendientes {len(pendientes)} en {len(colas)} dominios | {HILOS} dominios y "
          f"{PROCESOS} conversiones de PDF a la vez")

    inicio = time.monotonic()
    errores, ok, terminadas = [], 0, 0
    resultados: queue.Queue = queue.Queue()
    conversiones = {}
    barra = tqdm(total=len(pendientes), unit="doc", desc="Descargando")

    def terminar(f, error=None):
        nonlocal ok, terminadas
        if error:
            errores.append(_error(f, *error))
        else:
            ok += 1
        terminadas += 1
        barra.set_postfix(ok=ok, fallidos=len(errores))
        barra.update()

    with ThreadPoolExecutor(HILOS) as hilos, ProcessPoolExecutor(PROCESOS) as procesos:
        for d in orden:
            hilos.submit(_trabajar_cola, colas[d], resultados)
        while terminadas < len(pendientes):
            try:
                tipo, f, dato = resultados.get(timeout=0.2)
            except queue.Empty:
                tipo = None
            if tipo == "error":
                terminar(f, dato)
            elif tipo == "bajada":
                clase, contenido, url_final = dato
                if clase == "pdf":  # la conversion va al grupo de procesos; la red sigue
                    conversiones[procesos.submit(convertir_pdf, str(contenido))] = (f, contenido, url_final)
                else:
                    try:
                        escribir(f, contenido, url_final, "")
                        terminar(f)
                    except red.FalloRed as e:
                        terminar(f, (e.estado, e.motivo))
            for c in [c for c in conversiones if c.done()]:
                f, archivo_pdf, url_final = conversiones.pop(c)
                archivo_pdf.unlink(missing_ok=True)  # el PDF se borra apenas se convierte
                try:
                    md, paginas = c.result()
                    escribir(f, md, url_final, paginas)
                    terminar(f)
                except red.FalloRed as e:
                    terminar(f, (e.estado, e.motivo))
                except Exception as e:
                    terminar(f, (SIN_TEXTO, f"{type(e).__name__}: {e}"))
    barra.close()
    segundos = time.monotonic() - inicio

    with open(SALIDA / "errores.csv", "w", encoding="utf-8", newline="") as arch:
        w = csv.DictWriter(arch, fieldnames=["id", "coleccion", "titulo", "ruta", "url", "estado", "motivo"])
        w.writeheader()
        w.writerows(sorted(errores, key=lambda e: (e["coleccion"], e["id"])))
    resumen = (f"{time.strftime('%Y-%m-%d %H:%M')} | {len(pendientes)} pendientes: {ok} bajadas y {len(errores)} "
               f"fallidas en {segundos / 60:.1f} min ({segundos / max(len(pendientes), 1):.1f} s por fuente). "
               f"Corrida anterior: {SEGUNDOS_CORRIDA_ANTERIOR / 60:.1f} min para 271 "
               f"({SEGUNDOS_CORRIDA_ANTERIOR / 271:.1f} s por fuente).")
    LOGS.mkdir(parents=True, exist_ok=True)
    (LOGS / f"descarga_{time.strftime('%Y%m%d_%H%M')}.txt").write_text(
        resumen + "\n\n" + "\n".join(f"{e['coleccion']}/{e['id']}: {e['estado']} - {e['motivo']}" for e in errores),
        encoding="utf-8")
    print("\n" + resumen)
    print(f"Errores en {SALIDA / 'errores.csv'}")


if __name__ == "__main__":
    main()
