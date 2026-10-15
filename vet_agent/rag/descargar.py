"""Tarea 8 - Scraping: URL -> Markdown (corpus/md/<id>.md).

Uso (raiz del repo):
    python -m vet_agent.rag.descargar --fuentes corpus/fuentes_D.csv

El CSV de fuentes necesita las columnas id y url (es_portada es opcional).
Solo baja las URL de la lista: NO rastrea sitios ni sigue enlaces.

- Pagina web -> trafilatura saca solo el contenido principal (sin menus,
  cookies ni pies de pagina) en Markdown.
- PDF -> se baja a una carpeta temporal y pymupdf4llm lo pasa a Markdown,
  conservando titulos y tablas.

Reglas de buena conducta:
- Respeta robots.txt de cada sitio (tambien en cada redireccion).
- Espera al menos 2 segundos entre pedidos al mismo dominio.
- Se identifica con un User-Agent del proyecto.
- No intenta saltar logins, paywalls ni bloqueos: si aparecen, se anota y sigue.

Lo que falla queda en corpus/md/errores.csv (id, url, motivo).
"""
import argparse
import csv
import pathlib
import re
import tempfile
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pymupdf4llm
import requests
import trafilatura

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SALIDA = RAIZ / "corpus" / "md"
USER_AGENT = "vet-agent-pia3 (proyecto universitario UTEM)"
ESPERA = 2.0             # segundos minimos entre pedidos al mismo dominio
MINIMO_CARACTERES = 200  # menos que esto = escaneado, vacio o bloqueado
TIMEOUT = 30             # segundos maximos esperando a un sitio
MAX_REDIRECCIONES = 5
# URL de login: /login, /signin, /sign-in, /auth (palabra completa: /authors no cuenta)
RE_LOGIN = re.compile(r"/(log-?in|sign-?in|auth)\b", re.IGNORECASE)

_sesion = requests.Session()
_sesion.headers["User-Agent"] = USER_AGENT
_robots: dict[str, RobotFileParser] = {}   # un robots.txt por dominio
_ultimo_pedido: dict[str, float] = {}      # cuando se le pidio algo a cada dominio
_rechaza_bots: set[str] = set()            # dominios cuyo robots.txt responde 401/403
# Titulos de paginas anti-bots (CAPTCHA, Cloudflare). No se intenta saltarlas.
RE_ANTIBOTS = re.compile(
    rb"<title[^>]*>[^<]*(captcha|checking your browser|just a moment|attention required)",
    re.IGNORECASE)


class FalloDescarga(Exception):
    """Un documento que no se pudo bajar. El mensaje es el motivo para errores.csv."""


def _pedir(url: str) -> requests.Response:
    """GET sin seguir redirecciones, esperando el turno del dominio."""
    dominio = urlparse(url).netloc
    espera = ESPERA
    if dominio in _robots:  # si robots.txt pide mas pausa (Crawl-delay), se respeta
        espera = max(espera, float(_robots[dominio].crawl_delay(USER_AGENT) or 0))
    falta = _ultimo_pedido.get(dominio, 0) + espera - time.monotonic()
    if falta > 0:
        time.sleep(falta)
    try:
        return _sesion.get(url, timeout=TIMEOUT, allow_redirects=False)
    except requests.Timeout:
        raise FalloDescarga(f"el sitio no respondio en {TIMEOUT} s")
    except requests.ConnectionError:
        raise FalloDescarga("pagina caida o sin conexion")
    finally:
        _ultimo_pedido[dominio] = time.monotonic()


def _leer_robots(dominio_url: str) -> requests.Response:
    """Pide robots.txt siguiendo hasta 5 redirecciones (lo pide la norma RFC 9309)."""
    url = f"{dominio_url}/robots.txt"
    for _ in range(MAX_REDIRECCIONES + 1):
        r = _pedir(url)
        if not r.is_redirect:
            return r
        url = urljoin(url, r.headers["Location"])
    raise FalloDescarga("robots.txt con demasiadas redirecciones")


def _permitido(url: str) -> bool:
    """Lee (una vez por dominio) el robots.txt y dice si se puede bajar la URL."""
    partes = urlparse(url)
    if partes.netloc not in _robots:
        rp = RobotFileParser()
        r = _leer_robots(f"{partes.scheme}://{partes.netloc}")
        if r.status_code == 200:
            rp.parse(r.text.splitlines())
        elif r.status_code in (401, 403):
            _rechaza_bots.add(partes.netloc)
            rp.disallow_all = True   # criterio prudente: si niega el robots.txt, no se entra
        elif 400 <= r.status_code < 500:
            rp.allow_all = True      # no hay robots.txt: se permite todo
        else:
            raise FalloDescarga(f"no se pudo leer robots.txt (HTTP {r.status_code})")
        _robots[partes.netloc] = rp
    return _robots[partes.netloc].can_fetch(USER_AGENT, url)


def bajar(url: str) -> tuple[str, requests.Response]:
    """Baja una URL siguiendo redirecciones a mano, revisando robots.txt en cada salto."""
    for _ in range(MAX_REDIRECCIONES + 1):
        if not _permitido(url):
            dominio = urlparse(url).netloc
            if dominio in _rechaza_bots:
                raise FalloDescarga(f"{dominio} rechaza bots (su robots.txt responde HTTP 403)")
            raise FalloDescarga(f"robots.txt de {dominio} no permite bajarlo")
        r = _pedir(url)
        if r.is_redirect:
            url = urljoin(url, r.headers["Location"])
            continue
        if RE_LOGIN.search(url):
            raise FalloDescarga("redirige a una pagina de login")
        if r.status_code in (401, 402, 403, 407, 451):
            raise FalloDescarga(f"bloqueo, login o pago (HTTP {r.status_code})")
        if r.status_code == 429:
            raise FalloDescarga("el sitio pidio bajar la velocidad (HTTP 429)")
        if r.status_code >= 400:
            raise FalloDescarga(f"pagina no disponible (HTTP {r.status_code})")
        return url, r
    raise FalloDescarga("demasiadas redirecciones")


def pdf_a_md(contenido: bytes) -> str:
    """PDF (bytes) -> Markdown, usando una carpeta temporal que se borra sola."""
    with tempfile.TemporaryDirectory() as carpeta:
        ruta = pathlib.Path(carpeta) / "documento.pdf"
        ruta.write_bytes(contenido)
        md = pymupdf4llm.to_markdown(str(ruta), show_progress=False)
    if len(md.strip()) < MINIMO_CARACTERES:
        raise FalloDescarga("PDF escaneado o casi sin texto (necesita OCR)")
    return md


def web_a_md(contenido: bytes, url: str) -> str:
    """Pagina web -> Markdown con solo el contenido principal."""
    if RE_ANTIBOTS.search(contenido[:50000]):  # el <title> puede venir despues de scripts largos
        raise FalloDescarga("bloqueo anti-bots (CAPTCHA); no se intenta saltar")
    md = trafilatura.extract(
        contenido, url=url, output_format="markdown",
        include_tables=True, include_links=False, include_images=False, include_comments=False,
    )
    if not md or len(md.strip()) < MINIMO_CARACTERES:
        raise FalloDescarga("menos de 200 caracteres de contenido (vacia, armada con JavaScript o bloqueada)")
    return md


def url_a_md(url: str) -> tuple[str, str]:
    """Devuelve (markdown, tipo) para una URL. tipo es 'pdf' o 'web'."""
    url_final, r = bajar(url)
    es_pdf = "pdf" in r.headers.get("Content-Type", "").lower() or r.content[:5] == b"%PDF-"
    if es_pdf:
        return pdf_a_md(r.content), "pdf"
    return web_a_md(r.content, url_final), "web"


def es_portada(fila: dict) -> bool:
    """La URL apunta a la portada de un sitio y no al documento."""
    if fila.get("es_portada", "").strip().lower() == "si":
        return True
    return urlparse(fila["url"]).path in ("", "/")


def main() -> None:
    parser = argparse.ArgumentParser(description="URL -> Markdown (corpus/md/<id>.md)")
    parser.add_argument("--fuentes", required=True, help="CSV con columnas id y url")
    parser.add_argument("--salida", default=str(SALIDA), help="carpeta de los .md")
    args = parser.parse_args()

    salida = pathlib.Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    with open(args.fuentes, encoding="utf-8-sig", newline="") as f:
        fuentes = list(csv.DictReader(f))

    inicio = time.monotonic()
    errores = []
    for fila in fuentes:
        id_, url = fila["id"].strip(), fila["url"].strip()
        t0 = time.monotonic()
        try:
            if not url.startswith(("http://", "https://")):
                raise FalloDescarga("no hay URL (solo texto en el Excel)")
            if es_portada(fila):
                raise FalloDescarga("la URL es una portada o portal, no el documento (no se rastrea el sitio)")
            md, tipo = url_a_md(url)
        except FalloDescarga as e:
            motivo = str(e)
        except Exception as e:  # cualquier otra cosa: se anota y se sigue
            motivo = f"error inesperado: {type(e).__name__}: {e}"
        else:
            (salida / f"{id_}.md").write_text(md, encoding="utf-8")
            print(f"OK    {id_:4} {tipo:3} {len(md):7} caracteres  {time.monotonic() - t0:5.1f} s")
            continue
        (salida / f"{id_}.md").unlink(missing_ok=True)  # que no quede uno viejo
        errores.append({"id": id_, "url": url, "motivo": motivo})
        print(f"FALLO {id_:4} {motivo}")

    with open(salida / "errores.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "url", "motivo"])
        w.writeheader()
        w.writerows(errores)

    total = time.monotonic() - inicio
    print(f"\n{len(fuentes) - len(errores)} de {len(fuentes)} documentos bajados en {total:.0f} s.")
    print(f"Errores anotados en {salida / 'errores.csv'}")


if __name__ == "__main__":
    main()
