"""Etapa 2 - Pre-filtro de fuentes SIN descargar los documentos.

Uso (raiz del repo):
    python -m vet_agent.rag.prefiltro

Lee corpus/fuentes/Revision_fuentes_scraping_PIA3.xlsx (no lo modifica) y
escribe corpus/md/prefiltro.xlsx con una fila por fuente y:
- si es duplicada (DOI o URL normalizados),
- si tiene acceso abierto (OpenAlex) y texto completo en Europe PMC
  (Europe PMC va siempre primero: si tiene el texto, esa es la ruta),
- si la URL responde, si es portada/login o si robots.txt la bloquea,
- si el <title> de la pagina se parece al titulo esperado,
- la ruta de descarga elegida (a, b, c o d) y la URL que se va a bajar.

Solo hace pedidos livianos: consultas a las APIs por lotes, cabeceras de
las URL y, en paginas web, los primeros 20 KB para leer el <title>.
Las respuestas de OpenAlex y Europe PMC quedan en corpus/md/_cache/ (JSON),
asi una corrida nueva no las vuelve a pedir.
"""
import html
import json
import pathlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import pandas as pd
from rapidfuzz import fuzz, utils

from vet_agent.rag import red

RAIZ = pathlib.Path(__file__).resolve().parents[2]
EXCEL = RAIZ / "corpus" / "fuentes" / "Revision_fuentes_scraping_PIA3.xlsx"
METADATOS = RAIZ / "corpus" / "metadatos.csv"
SALIDA = RAIZ / "corpus" / "md" / "prefiltro.xlsx"
CACHE = RAIZ / "corpus" / "md" / "_cache"   # respuestas de OpenAlex y Europe PMC (JSON)
CORREO = "ppereza@utem.cl"

PRIORIDADES = {"Alta", "Media"}
AGENTE_EXCLUIDO = "Moderador-verificador"   # papers de LLM, no son base veterinaria
# Excluidos a mano (y van a la lista para Investigacion)
EXCLUIDOS_A_MANO = {"BIO-139": "Excluido a mano: paper de LLM/salud mental en bioseguridad (para revisar por Investigación)"}
# Temas que no parecen base veterinaria: se marcan para Investigacion, sin excluir
RE_TEMA_SIGLAS = re.compile(r"\b(LLMs?|RAG)\b")   # en mayusculas, para no confundir "rag" o "drag"
RE_TEMA_PALABRAS = re.compile(r"\b(large )?language models?\b|\bchat ?bots?\b|\bsalud mental\b|\bmental health\b",
                              re.IGNORECASE)
COLECCION_EJE = {"A": "climatico", "D": "diagnostico", "C": "bioseguridad", "E": "bioseguridad", "F": "bioseguridad"}
COLECCION_AGENTE = {"Climático-ambiental": "climatico", "Diagnóstico": "diagnostico", "Bioseguridad": "bioseguridad"}

UMBRAL_TITULO = 60        # rapidfuzz: menos que esto = "Revisar título"
BYTES_TITULO = 20_000     # cuanto se baja de una pagina web para leer el <title>
TIMEOUT = 10              # segundos por pedido en el prefiltro
HILOS = 8                 # pedidos en paralelo (a dominios distintos)
LOTE_OPENALEX = 50
LOTE_EUROPEPMC = 50
LOTE_EUROPEPMC_DOI = 25   # cada DOI ocupa ~60 caracteres en la URL

RUTA_EPMC = "a) Europe PMC"
RUTA_PDF_OA = "b) PDF de OpenAlex"
RUTA_WEB = "c) Web"
RUTA_PDF = "d) PDF directo"
URL_EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/{}/fullTextXML"

# Estados (de mas grave a menos grave). "OK" y "Revisar título" se descargan.
DUPLICADO, SIN_OPENALEX, SIN_RUTA, ANTIBOTS = "Duplicado", "No encontrado en OpenAlex", "Sin ruta", "Bloqueo anti-bots"
REVISAR_TITULO, OK = "Revisar título", "OK"
SE_DESCARGAN = {OK, REVISAR_TITULO}

RE_DOI = re.compile(r"10\.\d{4,9}/[^\s?#&\"'<>]+", re.IGNORECASE)
RE_PMCID = re.compile(r"\bPMC\d{4,9}\b", re.IGNORECASE)
RE_TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|dclid|mc_cid|mc_eid|_ga|_gl|_hsenc|_hsmi)$", re.IGNORECASE)
RE_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
# <meta http-equiv="refresh" content="0; url='...'"> (paginas intermedias como linkinghub.elsevier.com)
RE_META_REFRESH = re.compile(
    rb"""<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["']?\s*\d+\s*;\s*url=['"]?([^"'>\s]+)""",
    re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1. Leer el Excel y decidir que fuentes entran
# ---------------------------------------------------------------------------

def _texto(v) -> str:
    return "" if pd.isna(v) else str(v).strip()


def leer_fuentes(excel: pathlib.Path = EXCEL) -> pd.DataFrame:
    """Una fila por fuente de las hojas Corpus RAG, Bibliometría y Cribado."""
    filas = []
    for f in pd.read_excel(excel, sheet_name="Corpus RAG", dtype=str).to_dict("records"):
        eje = _texto(f["Eje"])
        filas.append({
            "id": _texto(f["ID"]), "hoja": "Corpus RAG", "grupo": f"Eje {eje}",
            "coleccion": COLECCION_EJE.get(eje, ""), "titulo_excel": _texto(f["Título"]),
            # si Investigacion completo la URL directa, manda esa
            "url": _texto(f["URL directa (completar)"]) or _texto(f["URL actual"]),
            "tipo_url": _texto(f["Tipo de URL"]), "enfermedad": _texto(f["Enfermedad"]),
            "prioridad": _texto(f["Prioridad"]),
        })
    for hoja in ("Bibliometría", "Cribado"):
        for f in pd.read_excel(excel, sheet_name=hoja, dtype=str).to_dict("records"):
            agente = _texto(f["Agente"])
            filas.append({
                "id": _texto(f["Código"]), "hoja": hoja, "grupo": agente,
                "coleccion": COLECCION_AGENTE.get(agente, ""), "titulo_excel": _texto(f["Título"]),
                "url": _texto(f["Versión libre (completar)"]) or _texto(f["URL (DOI)"]),
                "tipo_url": _texto(f["Tipo de URL"]), "enfermedad": _texto(f["Enfermedad"]),
                "prioridad": _texto(f["Prioridad"]),
            })
    df = pd.DataFrame(filas)
    return df[df.id != ""].reset_index(drop=True)


def motivo_exclusion(f: dict) -> str:
    """Por que una fila NO entra (vacio = entra)."""
    if f["prioridad"] not in PRIORIDADES:
        return f"Prioridad: {f['prioridad'] or 'vacía'}"
    if f["id"] in EXCLUIDOS_A_MANO:
        return EXCLUIDOS_A_MANO[f["id"]]
    if f["grupo"] == AGENTE_EXCLUIDO:
        return "Agente Moderador-verificador (papers de LLM)"
    if not f["coleccion"]:
        return f"Sin colección para {f['grupo']}"
    return ""


def tema_a_revisar(titulo: str) -> str:
    """Si el titulo habla de LLM, RAG, chatbots o salud mental (no se excluye: solo se avisa)."""
    m = RE_TEMA_SIGLAS.search(titulo) or RE_TEMA_PALABRAS.search(titulo)
    return f"el título menciona \"{m.group(0)}\" (¿es base veterinaria?)" if m else ""


def url_leychile(url: str) -> str:
    """bcn.cl/leychile/navegar?idNorma=X -> vista para imprimir (la norma completa en una pagina)."""
    p = urlparse(url)
    if "bcn.cl" not in p.netloc or not p.path.lower().rstrip("/").endswith("/leychile/navegar"):
        return url
    q = dict(parse_qsl(p.query))
    if "idNorma" not in q:
        return url
    extra = f"&idVersion={q['idVersion']}" if q.get("idVersion") else ""
    return f"https://www.bcn.cl/leychile/Navegar/imprimir?idNorma={q['idNorma']}{extra}"


# ---------------------------------------------------------------------------
# 2. Duplicados: DOI y URL normalizados
# ---------------------------------------------------------------------------

def extraer_doi(url: str) -> str:
    m = RE_DOI.search(unquote(url))
    return m.group(0).rstrip("/.,;)").lower() if m else ""


def extraer_pmcid(texto: str) -> str:
    m = RE_PMCID.search(texto or "")
    return m.group(0).upper() if m else ""


def normalizar_url(url: str) -> str:
    """minusculas, sin esquema, sin www, sin / final y sin parametros de tracking."""
    p = urlparse(url.strip().lower())
    dominio = p.netloc.removeprefix("www.")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not RE_TRACKING.match(k)))
    return dominio + p.path.rstrip("/") + (f"?{query}" if query else "")


def normalizar_titulo(titulo: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", titulo.lower()).strip()


def marcar_duplicados(df: pd.DataFrame) -> None:
    """Dentro de una misma coleccion, se queda la primera aparicion de cada fuente.

    Una fuente se repite si coincide el DOI, la URL normalizada o el titulo
    normalizado (este ultimo pilla la misma fuente con URL web en una hoja y
    DOI en otra). La fila que se queda hereda el DOI de su duplicado si no
    tenia, para poder consultarla en OpenAlex.
    """
    df["duplicado_de"] = ""
    df["tambien_en"] = ""
    entran = df[df.excluida == ""]
    titulos = entran.titulo_excel.map(normalizar_titulo)
    for (_, t), grupo in entran.groupby([entran.coleccion, titulos], sort=False):
        if not t or len(grupo) < 2:
            continue
        queda = grupo.index[0]
        for i in grupo.index[1:]:
            df.loc[i, "duplicado_de"] = df.loc[queda, "id"]
            if not df.loc[queda, "doi"] and df.loc[i, "doi"]:
                df.loc[queda, "doi"] = df.loc[i, "doi"]
    df["clave"] = [f"doi:{d}" if d else f"url:{normalizar_url(u)}" for d, u in zip(df.doi, df.url)]
    entran = df[(df.excluida == "") & (df.duplicado_de == "")]
    for (_, _), grupo in entran.groupby(["coleccion", "clave"], sort=False):
        for i in grupo.index[1:]:
            df.loc[i, "duplicado_de"] = grupo.id.iloc[0]
    entran = df[df.excluida == ""]
    for _, grupo in entran.groupby("clave", sort=False):  # misma fuente en otra coleccion: se informa
        colecciones = set(grupo.coleccion)
        if len(colecciones) > 1:
            for i, c in zip(grupo.index, grupo.coleccion):
                df.loc[i, "tambien_en"] = ", ".join(sorted(colecciones - {c}))


# ---------------------------------------------------------------------------
# 3. APIs por lotes: OpenAlex (acceso abierto) y Europe PMC (texto completo)
# ---------------------------------------------------------------------------

def _cache_leer(nombre: str) -> dict:
    ruta = CACHE / f"{nombre}.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def _cache_guardar(nombre: str, datos: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{nombre}.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def consultar_openalex(dois: list[str]) -> dict[str, dict]:
    """DOI -> datos de OpenAlex. Lotes de 50 DOI, 1 s entre consultas.

    Las respuestas (tambien "no encontrado" = null) quedan en corpus/md/_cache/openalex.json,
    asi una corrida nueva solo consulta los DOI que no conoce.
    """
    cache = _cache_leer("openalex")
    normales = [d for d in dois if "|" not in d and "," not in d]  # | y , rompen el filtro
    faltan = [d for d in normales if d not in cache]
    print(f"   OpenAlex: {len(normales) - len(faltan)} en caché, {len(faltan)} por consultar")
    for i in range(0, len(faltan), LOTE_OPENALEX):
        lote = faltan[i:i + LOTE_OPENALEX]
        try:
            _, r = red.seguir("https://api.openalex.org/works", timeout=30, reintentos=2, params={
                "filter": "doi:" + "|".join(lote), "per_page": LOTE_OPENALEX, "mailto": CORREO,
                "select": "doi,display_name,publication_year,open_access,best_oa_location,ids,primary_location"})
            resultados = r.json()["results"]
        except (red.FalloRed, ValueError, KeyError) as e:
            print(f"   AVISO: falló un lote de OpenAlex ({e}); no se guarda en caché")
            continue
        for d in lote:
            cache[d] = None  # si no viene en la respuesta, OpenAlex no lo conoce
        for w in resultados:
            doi = (w.get("doi") or "").lower().removeprefix("https://doi.org/")
            fuente = ((w.get("primary_location") or {}).get("source") or {})
            cache[doi] = {
                "is_oa": bool((w.get("open_access") or {}).get("is_oa")),
                "oa_url": (w.get("open_access") or {}).get("oa_url") or "",
                "pdf_url": (w.get("best_oa_location") or {}).get("pdf_url") or "",
                "pmcid": extraer_pmcid((w.get("ids") or {}).get("pmcid") or ""),
                "titulo": w.get("display_name") or "",
                "anio": str(w.get("publication_year") or ""),
                "revista": fuente.get("display_name") or fuente.get("host_organization_name") or "",
            }
        _cache_guardar("openalex", cache)
    raros = sorted(set(dois) - set(normales))
    if raros:
        print("   AVISO: DOI con | o , sin consultar:", raros)
    return {d: cache[d] for d in normales if cache.get(d)}


def _buscar_europepmc(consultas: list[str], tipo: str) -> list[dict] | None:
    """Una busqueda en Europe PMC (varias condiciones unidas por OR). None si fallo."""
    try:
        _, r = red.seguir("https://www.ebi.ac.uk/europepmc/webservices/rest/search", timeout=30, reintentos=2,
                          params={"query": " OR ".join(consultas), "resultType": tipo,
                                  "format": "json", "pageSize": 100})
        return r.json()["resultList"]["result"]
    except (red.FalloRed, ValueError, KeyError) as e:
        print(f"   AVISO: falló un lote de Europe PMC ({e}); no se guarda en caché")
        return None


def consultar_europepmc(pmcids: list[str]) -> set[str]:
    """PMCID que Europe PMC entrega completos (isOpenAccess = Y). Con caché."""
    cache = _cache_leer("europepmc_pmcid")  # pmcid -> True/False
    faltan = [p for p in pmcids if p not in cache]
    for i in range(0, len(faltan), LOTE_EUROPEPMC):
        lote = faltan[i:i + LOTE_EUROPEPMC]
        resultados = _buscar_europepmc([f"PMCID:{p}" for p in lote], "lite")
        if resultados is None:
            continue
        for p in lote:
            cache[p] = False
        for x in resultados:
            if x.get("pmcid") in lote:
                cache[x["pmcid"]] = x.get("isOpenAccess") == "Y"
        _cache_guardar("europepmc_pmcid", cache)
    return {p for p in pmcids if cache.get(p)}


def europepmc_por_doi(dois: list[str]) -> dict[str, str]:
    """DOI -> PMCID, solo los que Europe PMC entrega completos (isOpenAccess = Y). Con caché.

    Asi el texto llega por la API de Europe PMC, sin tocar la editorial ni la web de PMC.
    """
    cache = _cache_leer("europepmc_doi")  # doi -> pmcid ("" si no esta completo en Europe PMC)
    faltan = [d for d in dois if '"' not in d and d not in cache]
    print(f"   Europe PMC por DOI: {len(dois) - len(faltan)} en caché, {len(faltan)} por consultar")
    for i in range(0, len(faltan), LOTE_EUROPEPMC_DOI):
        lote = faltan[i:i + LOTE_EUROPEPMC_DOI]
        resultados = _buscar_europepmc([f'DOI:"{d}"' for d in lote], "core")
        if resultados is None:
            continue
        for d in lote:
            cache.setdefault(d, "")
        for x in resultados:
            doi = (x.get("doi") or "").lower()
            if doi in lote and x.get("pmcid") and x.get("isOpenAccess") == "Y":
                cache[doi] = x["pmcid"]
        _cache_guardar("europepmc_doi", cache)
    return {d: cache[d] for d in dois if cache.get(d)}


# ---------------------------------------------------------------------------
# 4. Revisar cada fuente y decidir la ruta de descarga
# ---------------------------------------------------------------------------

def _similitud(titulo_pagina: str, *esperados: str) -> int:
    """El mejor parecido (0-100) entre el <title> y cualquiera de los titulos esperados."""
    return max((int(fuzz.token_set_ratio(titulo_pagina, t, processor=utils.default_process))
                for t in esperados if t), default=0)


def _leer_inicio(url: str) -> tuple[str, bool, bytes]:
    """(url_final, es_pdf, primeros 20 KB si es HTML)."""
    url_final, r = red.seguir(url, timeout=TIMEOUT, stream=True)
    try:
        es_pdf = "pdf" in r.headers.get("Content-Type", "").lower()
        inicio = b"" if es_pdf else next(r.iter_content(BYTES_TITULO), b"")[:BYTES_TITULO]
    finally:
        r.close()
    return url_final, es_pdf or inicio[:5] == b"%PDF-", inicio


def revisar_url(url: str) -> dict:
    """Cabeceras (y en HTML los primeros 20 KB) de una URL. Lanza red.FalloRed si no sirve."""
    if urlparse(url).path in ("", "/"):
        raise red.FalloRed(red.PORTADA_LOGIN, "la URL es la portada del sitio")
    url_final, es_pdf, inicio = _leer_inicio(url)
    m = None if es_pdf else RE_META_REFRESH.search(inicio)
    if m:  # pagina intermedia que redirige con <meta refresh>: se sigue una vez (con robots.txt)
        destino = urljoin(url_final, html.unescape(m.group(1).decode("utf-8", "replace")))
        url_final, es_pdf, inicio = _leer_inicio(destino)
    if not es_pdf and red.es_antibots(inicio):
        raise red.FalloRed(ANTIBOTS, "la página es un CAPTCHA o control anti-bots")
    m = RE_TITLE.search(inicio)
    titulo = html.unescape(m.group(1).decode("utf-8", "replace")).strip() if m else ""
    return {"url_final": url_final, "es_pdf": es_pdf, "titulo_pagina": re.sub(r"\s+", " ", titulo)}


def revisar(f: dict, oa: dict | None, epmc_abiertos: set[str]) -> dict:
    """Prueba las rutas a, b, c, d en orden y se queda con la primera que sirve."""
    if not f["url"]:
        return {"estado": SIN_RUTA, "motivo": "no hay URL en el Excel"}

    candidatos = []                                   # (ruta, url) en orden de preferencia
    # Europe PMC va siempre primero: es mas rapido, mas limpio y no lo bloquean.
    # f["pmcid"] ya trae el PMCID que Europe PMC confirmo por DOI (o el de la URL).
    pmcid = f["pmcid"] or (oa or {}).get("pmcid", "")
    if pmcid and pmcid in epmc_abiertos:
        candidatos.append((RUTA_EPMC, URL_EPMC.format(pmcid)))
    elif f["doi"] and oa is None and urlparse(f["url"]).netloc.endswith("doi.org"):
        return {"estado": SIN_OPENALEX, "motivo": f"OpenAlex no conoce el DOI {f['doi']}"}
    fallos = []
    if pmcid and pmcid not in epmc_abiertos:
        fallos.append((SIN_RUTA, f"Europe PMC no da el texto completo de {pmcid} (sin licencia abierta)"))
    if oa is not None and not oa["is_oa"] and not candidatos:
        return {"estado": red.CON_PAGO, "motivo": "OpenAlex: sin acceso abierto (is_oa = false)", "pmcid": pmcid}
    if oa and oa["pdf_url"]:
        candidatos.append((RUTA_PDF_OA, oa["pdf_url"]))
    if oa and oa["oa_url"] and oa["oa_url"] != oa["pdf_url"]:
        candidatos.append((RUTA_WEB, oa["oa_url"]))
    if not urlparse(f["url"]).netloc.endswith("doi.org"):
        candidatos.append((RUTA_PDF if f["tipo_url"] == "PDF directo" else RUTA_WEB, url_leychile(f["url"])))
    # la web de PMC tiene CAPTCHA: nunca se usa (su texto llega por Europe PMC, ruta a)
    pmc_web = [u for _, u in candidatos if "ncbi.nlm.nih.gov" in u]
    candidatos = [(r, u) for r, u in candidatos if "ncbi.nlm.nih.gov" not in u]
    if pmc_web and not candidatos:
        fallos.append((SIN_RUTA, "solo hay la web de PMC, que no se usa (CAPTCHA); falta texto en Europe PMC"))
    if not candidatos and not fallos:
        fallos.append((SIN_RUTA, "OpenAlex dice acceso abierto pero no da ninguna URL"))

    for ruta, url in candidatos:
        try:
            if ruta in (RUTA_EPMC, RUTA_PDF_OA):      # APIs y PDF de OpenAlex: solo robots.txt
                if not red.permitido(url):
                    raise red.FalloRed(red.BLOQUEADO_ROBOTS, f"robots.txt de {urlparse(url).netloc} no permite esta ruta")
                return {"estado": OK, "motivo": "", "ruta": ruta, "url_descarga": url, "pmcid": pmcid}
            info = revisar_url(url)
        except red.FalloRed as e:
            fallos.append((e.estado, f"{ruta}: {e.motivo}"))
            continue
        # la ruta la decide lo que el sitio entrega de verdad, no lo que dice el Excel
        ruta_real = ruta
        if info["es_pdf"] and ruta == RUTA_WEB:
            ruta_real = RUTA_PDF if url == f["url"] else RUTA_PDF_OA   # resulto ser un PDF
        elif not info["es_pdf"] and ruta == RUTA_PDF:
            ruta_real = RUTA_WEB                                       # "PDF directo" que es una pagina
        resultado = {"estado": OK, "motivo": "", "ruta": ruta_real, "url_descarga": info["url_final"],
                     "titulo_pagina": info["titulo_pagina"], "pmcid": pmcid}
        if not info["es_pdf"]:
            sim = _similitud(info["titulo_pagina"], f["titulo_excel"], (oa or {}).get("titulo", ""))
            resultado["similitud_titulo"] = sim
            if sim < UMBRAL_TITULO:
                resultado["estado"] = REVISAR_TITULO
                resultado["motivo"] = (f"el <title> se parece {sim}/100 al título esperado"
                                       if info["titulo_pagina"] else "la página no tiene <title>")
        return resultado

    estado, _ = fallos[0]
    return {"estado": estado, "motivo": " | ".join(m for _, m in fallos), "pmcid": pmcid}


# ---------------------------------------------------------------------------
# 5. Metadatos para la cabecera de cada .md
# ---------------------------------------------------------------------------

def metadatos(f: dict, oa: dict | None, meta_csv: pd.DataFrame) -> dict:
    """Titulo, institucion/revista y anio. Primero metadatos.csv, luego OpenAlex, luego el Excel."""
    if f["id"] in meta_csv.index:
        m = meta_csv.loc[f["id"]]
        return {"meta_titulo": m.titulo, "meta_institucion": m.institucion, "meta_anio": m.anio, "fuente_metadatos": "excel"}
    if oa:
        return {"meta_titulo": oa["titulo"] or f["titulo_excel"], "meta_institucion": oa["revista"],
                "meta_anio": oa["anio"], "fuente_metadatos": "openalex"}
    return {"meta_titulo": f["titulo_excel"], "meta_institucion": "", "meta_anio": "", "fuente_metadatos": "excel"}


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------

def sin_formulas(df: pd.DataFrame) -> pd.DataFrame:
    """Textos que Excel tomaria como formula (=, +, -, @) quedan como texto con '.

    Los titulos de pagina y las URL vienen de sitios externos: asi nadie del
    equipo ejecuta una formula al abrir el Excel.
    """
    def limpiar(v):
        return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    return df.map(limpiar)


COLUMNAS = ["id", "hoja", "grupo", "coleccion", "titulo_excel", "enfermedad", "prioridad", "tipo_url", "url",
            "doi", "pmcid", "clave", "duplicado_de", "tambien_en", "en_openalex", "is_oa", "oa_url", "pdf_url",
            "en_europepmc", "estado", "motivo", "revisar_investigacion", "descargar", "ruta", "url_descarga", "titulo_pagina", "similitud_titulo",
            "meta_titulo", "meta_institucion", "meta_anio", "fuente_metadatos"]


def main() -> None:
    inicio = time.monotonic()
    fecha = time.strftime("%Y-%m-%d %H:%M")
    df = leer_fuentes()
    df["excluida"] = [motivo_exclusion(f) for f in df.to_dict("records")]
    df["doi"] = df.url.map(extraer_doi)
    df["pmcid"] = df.url.map(extraer_pmcid)
    marcar_duplicados(df)
    entran = df[(df.excluida == "") & (df.duplicado_de == "")]
    print(f"{len(df)} filas en el Excel | entran {len(df[df.excluida == ''])} | "
          f"duplicadas {int((df.duplicado_de != '').sum())} | a revisar {len(entran)}")

    print("Consultando OpenAlex...")
    oa = consultar_openalex(sorted({d for d in entran.doi if d}))
    print("Consultando Europe PMC por DOI (va primero: si tiene el texto completo, esa es la ruta)...")
    epmc_doi = europepmc_por_doi(sorted({d for d in entran.doi if d}))
    for i in entran.index:  # el PMCID confirmado por Europe PMC manda sobre el de la URL
        if epmc_doi.get(df.loc[i, "doi"]):
            df.loc[i, "pmcid"] = epmc_doi[df.loc[i, "doi"]]
    entran = df.loc[entran.index]
    pmcids = sorted({p for p in list(entran.pmcid) + [v["pmcid"] for v in oa.values()] if p} - set(epmc_doi.values()))
    print(f"Consultando Europe PMC ({len(pmcids)} PMCID más)...")
    epmc = consultar_europepmc(pmcids) | set(epmc_doi.values())

    print(f"Revisando {len(entran)} URL ({HILOS} a la vez, un pedido a la vez por dominio)...")
    resultados = {}
    with ThreadPoolExecutor(HILOS) as pool:
        tareas = {pool.submit(revisar, f, oa.get(f["doi"]) if f["doi"] else None, epmc): i
                  for i, f in zip(entran.index, entran.to_dict("records"))}
        for n, t in enumerate(as_completed(tareas), 1):
            i = tareas[t]
            try:
                resultados[i] = t.result()
            except Exception as e:  # nunca se detiene: se anota
                resultados[i] = {"estado": SIN_RUTA, "motivo": f"error inesperado: {type(e).__name__}: {e}"}
            if n % 25 == 0 or n == len(tareas):
                print(f"   {n}/{len(tareas)}  ({time.monotonic() - inicio:.0f} s)")

    meta_csv = pd.read_csv(METADATOS, dtype=str, encoding="utf-8-sig").fillna("").set_index("id")
    for col in COLUMNAS:
        if col not in df:
            df[col] = ""
    df = df.astype({c: object for c in COLUMNAS})
    for i, r in resultados.items():
        for k, v in r.items():
            df.loc[i, k] = v

    for i in df.index:
        f = df.loc[i].to_dict()
        df.loc[i, "en_europepmc"] = "sí" if f["pmcid"] in epmc else ("no" if f["doi"] or f["pmcid"] else "")
        df.loc[i, "revisar_investigacion"] = f["excluida"] if f["id"] in EXCLUIDOS_A_MANO else tema_a_revisar(f["titulo_excel"])
        datos_oa = oa.get(f["doi"]) if f["doi"] else None
        df.loc[i, "en_openalex"] = "sí" if datos_oa else ("no" if f["doi"] else "")
        if datos_oa:
            df.loc[i, ["is_oa", "oa_url", "pdf_url"]] = ["sí" if datos_oa["is_oa"] else "no",
                                                          datos_oa["oa_url"], datos_oa["pdf_url"]]
        if f["duplicado_de"] and not f["excluida"]:
            df.loc[i, ["estado", "motivo"]] = [DUPLICADO, f"mismo DOI/URL que {f['duplicado_de']}"]
        for k, v in metadatos(f, datos_oa, meta_csv).items():
            df.loc[i, k] = v
    df["descargar"] = ["sí" if e in SE_DESCARGAN else "no" for e in df.estado]

    fuentes = df[df.excluida == ""].reindex(columns=COLUMNAS).fillna("")
    excluidas = df[df.excluida != ""][["id", "hoja", "grupo", "titulo_excel", "url", "prioridad", "excluida"]]
    por_estado = pd.crosstab(fuentes.estado, fuentes.coleccion, margins=True, margins_name="Total")
    rutas = pd.crosstab(fuentes[fuentes.descargar == "sí"].ruta, fuentes[fuentes.descargar == "sí"].coleccion,
                        margins=True, margins_name="Total")
    exclusiones = excluidas.groupby(["hoja", "excluida"]).size().rename("filas").reset_index()

    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(SALIDA, engine="openpyxl") as xl:
        por_estado.to_excel(xl, sheet_name="Resumen", startrow=1)
        rutas.to_excel(xl, sheet_name="Resumen", startrow=len(por_estado) + 5)
        exclusiones.to_excel(xl, sheet_name="Resumen", startrow=len(por_estado) + len(rutas) + 9, index=False)
        hoja = xl.sheets["Resumen"]
        hoja.cell(1, 1, "Fuentes por estado y colección")
        hoja.cell(1, 6, f"Corrida: {fecha} (los resultados varían entre corridas: algunos sitios responden distinto)")
        hoja.cell(len(por_estado) + 5, 1, "Ruta de descarga (solo las que se descargan)")
        hoja.cell(len(por_estado) + len(rutas) + 9, 1, "Filas que no entran")
        sin_formulas(fuentes).to_excel(xl, sheet_name="Fuentes", index=False)
        sin_formulas(excluidas).to_excel(xl, sheet_name="Excluidas", index=False)

    print("\nFuentes por estado y colección:")
    print(por_estado.to_string())
    print("\nRuta de descarga (solo las que se descargan):")
    print(rutas.to_string())
    print("\nFilas que no entran:")
    print(exclusiones.to_string(index=False))
    print(f"\nCorrida del {fecha}, lista en {time.monotonic() - inicio:.0f} s -> {SALIDA}")


if __name__ == "__main__":
    main()
