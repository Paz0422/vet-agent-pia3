"""Piezas de la demo (app_demo.py) que no son widgets de Streamlit: el formulario del caso
(campos, validacion, conversion), el HTML/CSS del informe y la lectura de las comparaciones.

Aca todo son funciones puras: app_demo.py queda con la logica de la pantalla y esto se
prueba sin abrir la demo. Todo texto que viene del caso, del LLM o de los documentos pasa
por esc() antes de ir al HTML: el modelo podria devolver algo como "<script>".
"""
import csv
import html
import json
import pathlib
import re
from collections import Counter

from vet_agent.grafo import despues_del_moderador, despues_de_revision_humana

ACENTO = "#0f766e"
# rojo / ambar / verde se usan SOLO para el nivel de riesgo (tonos oscuros: texto blanco encima)
COLOR_RIESGO = {"bajo": "#15803d", "moderado": "#b45309", "alto": "#b91c1c", "muy_alto": "#7f1d1d"}
NIVELES = ["bajo", "moderado", "alto", "muy_alto"]

NODOS = ["climatico", "diagnostico", "bioseguridad", "moderador", "revision_humana"]
NOMBRE_NODO = {"climatico": "Climático", "diagnostico": "Diagnóstico", "bioseguridad": "Bioseguridad",
               "moderador": "Moderador", "revision_humana": "Revisión humana"}
ICONO_NODO = {"climatico": "🌦", "diagnostico": "🧪", "bioseguridad": "🛡", "moderador": "⚖", "revision_humana": "✍"}

DIMENSIONES = [("animal", "Animal", "🐔"), ("humana", "Humana", "🧑‍⚕️"), ("ambiental", "Ambiental", "🌿")]

VERSIONES = {"B1": "LLM sin RAG", "B2": "Monoagente con RAG", "B3": "Multiagente"}
# las corridas viejas de evaluar.py usaban estos nombres en vez de B1/B2/B3
ALIAS_VERSION = {"llm_sin_rag": "B1", "monoagente_rag": "B2", "multiagente": "B3"}


def esc(valor) -> str:
    return html.escape("" if valor is None else str(valor))


def duracion(segundos) -> str:
    if segundos is None:
        return ""
    segundos = round(float(segundos))
    if segundos < 60:
        return f"{segundos} s"
    return f"{segundos // 60} min {segundos % 60:02d} s"


def nivel_texto(nivel) -> str:
    return esc(str(nivel or "").replace("_", " ").upper())


def como_dict(item, clave: str) -> dict:
    # el LLM real a veces devuelve textos sueltos donde se esperaba un objeto
    return item if isinstance(item, dict) else {clave: item}


# ====================== formulario del caso ======================
# tipo: texto | parrafo | entero | numero | fecha | lineas | laboratorio
# Las claves de sesion son caso_<campo> (y caso_clima_<sub>): las usan las pruebas.

CAMPOS = {
    "especie": {"etiqueta": "Especie", "tipo": "texto", "obligatorio": True, "ejemplo": "gallinas ponedoras"},
    "sistema_productivo": {"etiqueta": "Sistema productivo", "tipo": "texto",
                           "ejemplo": "plantel comercial, galpones abiertos sin malla antiaves"},
    "n_animales": {"etiqueta": "Animales en el plantel", "tipo": "entero", "obligatorio": True, "unidad": "n.º",
                   "ejemplo": "40000"},
    "region": {"etiqueta": "Región", "tipo": "texto", "obligatorio": True, "ejemplo": "Valparaíso"},
    "comuna": {"etiqueta": "Comuna", "tipo": "texto", "obligatorio": True, "ejemplo": "Concón"},
    "fecha_evento": {"etiqueta": "Fecha del evento", "tipo": "fecha", "obligatorio": True, "unidad": "AAAA-MM-DD",
                     "ejemplo": "2026-05-01"},
    "signos_clinicos": {"etiqueta": "Signos clínicos", "tipo": "lineas", "obligatorio": True, "unidad": "uno por línea",
                        "ejemplo": "mortalidad súbita\ncianosis de cresta y barbilla\ncaída brusca de la postura"},
    "mortalidad_pct": {"etiqueta": "Mortalidad", "tipo": "numero", "unidad": "%", "ejemplo": "12", "rango": (0, 100)},
    "morbilidad_pct": {"etiqueta": "Morbilidad", "tipo": "numero", "unidad": "%", "ejemplo": "30", "rango": (0, 100)},
    "indicadores_productivos": {"etiqueta": "Indicadores productivos", "tipo": "parrafo",
                                "ejemplo": "caída de la postura de 20 % en 3 días; menor consumo de agua"},
    "resultados_laboratorio": {"etiqueta": "Resultados de laboratorio", "tipo": "laboratorio",
                               "unidad": "uno por línea: prueba: resultado · fecha",
                               "ejemplo": "RT-PCR: positivo a influenza A (gen M) · 2026-05-03\n"
                                          "Subtipificación: H5N1 · 2026-05-05"},
    "aves_silvestres_cercanas": {"etiqueta": "Aves silvestres cercanas", "tipo": "parrafo",
                                 "ejemplo": "aves migratorias en un humedal a 2 km"},
    "historial_brotes_zona": {"etiqueta": "Historial de brotes en la zona", "tipo": "parrafo",
                              "ejemplo": "detecciones de H5N1 en aves silvestres de la costa el año pasado"},
    "movimientos_animales": {"etiqueta": "Movimientos de animales y personas", "tipo": "parrafo",
                             "ejemplo": "ingreso de pollitas de recría hace 10 días; visitas sin registro"},
}
CLIMA = {"temperatura_media_c": {"etiqueta": "Temperatura media", "unidad": "°C", "ejemplo": "10"},
         "humedad_relativa_pct": {"etiqueta": "Humedad relativa", "unidad": "%", "ejemplo": "85", "rango": (0, 100)},
         "precipitacion_mm": {"etiqueta": "Precipitación", "unidad": "mm", "ejemplo": "15"}}

FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def etiqueta_campo(spec: dict) -> str:
    texto = spec["etiqueta"] + (f" ({spec['unidad']})" if spec.get("unidad") else "")
    return texto + (" *" if spec.get("obligatorio") else "")


def _texto(valor) -> str:
    return "" if valor is None else str(valor)


# un texto que es solo un marcador entre parentesis, como "(comuna)" en la fila de ejemplo
# IAAP-00 de la planilla de Investigacion, es un dato que falta, no un dato
MARCADOR = re.compile(r"^\(.*\)$")


def sin_marcador(texto: str) -> str:
    return "" if MARCADOR.match(texto.strip()) else texto


def lab_a_texto(resultados) -> str:
    """[{"prueba", "resultado", "fecha"}] -> una linea por resultado: "prueba: resultado · fecha"."""
    lineas = []
    for r in resultados or []:
        r = como_dict(r, "prueba")
        linea = _texto(r.get("prueba"))
        if r.get("resultado") not in (None, ""):
            linea += f": {r['resultado']}"
        if r.get("fecha"):
            linea += f" · {r['fecha']}"
        lineas.append(linea)
    return "\n".join(lineas)


def texto_a_lab(texto: str) -> list[dict]:
    """Lo inverso de lab_a_texto. Una fecha al final (" · AAAA-MM-DD") es opcional."""
    resultados = []
    for linea in (texto or "").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        fecha = None
        if " · " in linea and FECHA.match(linea.rsplit(" · ", 1)[1].strip()):
            linea, fecha = (p.strip() for p in linea.rsplit(" · ", 1))
        prueba, _, resultado = linea.partition(":")
        resultados.append({"prueba": prueba.strip(), "resultado": resultado.strip() or None, "fecha": fecha})
    return resultados


def caso_a_formulario(datos: dict, campos: list[str]) -> dict[str, str]:
    """Valores de sesion (caso_<campo>) que muestran `datos` en el formulario.

    Recorre todos los `campos` (los de CASO_H5N1): lo que no venga en `datos` queda vacio,
    para no dejar pegado algo de un caso anterior.
    """
    valores = {}
    for campo in campos:
        valor = datos.get(campo)
        tipo = CAMPOS.get(campo, {}).get("tipo", "texto")
        if campo == "clima":
            for sub in CLIMA:
                valores[f"caso_clima_{sub}"] = _texto((valor or {}).get(sub))
        elif tipo == "laboratorio":
            valores[f"caso_{campo}"] = lab_a_texto(valor)
        elif tipo == "lineas":
            valores[f"caso_{campo}"] = "\n".join(valor or [])
        else:
            valores[f"caso_{campo}"] = sin_marcador(_texto(valor))
    return valores


def _numero(texto: str, spec: dict, entero: bool, errores: list[str]):
    texto = texto.strip().replace(",", ".")
    if not texto:
        return None
    try:
        valor = float(texto.replace(" ", ""))
    except ValueError:
        errores.append(f"{spec['etiqueta']}: escribe un número (ej: {spec['ejemplo']}).")
        return None
    if entero:
        if not valor.is_integer() or valor < 0:
            errores.append(f"{spec['etiqueta']}: escribe un número entero (ej: {spec['ejemplo']}).")
            return None
        return int(valor)
    minimo, maximo = spec.get("rango", (None, None))
    if minimo is not None and not minimo <= valor <= maximo:
        errores.append(f"{spec['etiqueta']}: debe estar entre {minimo} y {maximo} {spec.get('unidad', '')}".rstrip() + ".")
        return None
    return valor


def leer_caso(valores, campos: list[str]) -> tuple[dict, list[str]]:
    """Arma el caso desde los valores del formulario y revisa obligatorios y formatos.

    Devuelve (caso, errores). Un campo vacio queda en None (o lista vacia): para los agentes
    es un dato desconocido, igual que los null de los escenarios de Investigacion.
    """
    caso, errores = {}, []
    for campo in campos:
        if campo == "clima":
            caso["clima"] = {sub: _numero(_texto(valores.get(f"caso_clima_{sub}")), spec, False, errores)
                             for sub, spec in CLIMA.items()}
            continue
        spec = CAMPOS.get(campo, {"etiqueta": campo.replace("_", " ").capitalize(), "tipo": "texto"})
        texto = _texto(valores.get(f"caso_{campo}"))
        tipo = spec["tipo"]
        if tipo == "lineas":
            valor = [linea.strip() for linea in texto.splitlines() if linea.strip()]
        elif tipo == "laboratorio":
            valor = texto_a_lab(texto)
        elif tipo in ("entero", "numero"):
            valor = _numero(texto, spec, tipo == "entero", errores)
        else:
            valor = sin_marcador(texto).strip() or None
            if valor and tipo == "fecha" and not FECHA.match(valor):
                errores.append(f"{spec['etiqueta']}: usa el formato AAAA-MM-DD (ej: {spec['ejemplo']}).")
        if spec.get("obligatorio") and valor in (None, [], "") and not any(spec["etiqueta"] in e for e in errores):
            errores.append(f"Falta: {spec['etiqueta']}.")
        caso[campo] = valor
    return caso, errores


# ====================== estilos ======================

CSS = """
<style>
:root {
  --va-acento: #0f766e; --va-oscuro: #0b3d3a; --va-tinta: #0b1220; --va-texto: #1f2937;
  --va-gris: #4b5563; --va-gris-2: #6b7280; --va-linea: #d5dedc; --va-suave: #eef5f4;
  /* colores de apoyo (familia fria): rojo, ambar y verde quedan solo para el riesgo */
  --va-turquesa: #14b8a6; --va-indigo: #4338ca; --va-cielo: #0369a1; --va-violeta: #7c3aed; --va-cian: #0e7490;
  --va-curva: cubic-bezier(.2, .75, .25, 1);
}

/* ---------- animaciones (se apagan si el sistema pide menos movimiento) ---------- */
@keyframes va-entrar { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes va-pop { 0% { opacity: 0; transform: scale(.7); } 70% { transform: scale(1.06); } 100% { opacity: 1; transform: none; } }
@keyframes va-crecer { from { transform: scaleX(0); } to { transform: scaleX(1); } }
@keyframes va-latido { 0%, 100% { box-shadow: 0 0 0 0 rgba(94, 234, 212, .7); } 50% { box-shadow: 0 0 0 6px rgba(94, 234, 212, 0); } }
@keyframes va-campana { 0%, 100% { transform: rotate(0); } 20% { transform: rotate(-14deg); } 40% { transform: rotate(12deg); } 60% { transform: rotate(-8deg); } 80% { transform: rotate(5deg); } }
@keyframes va-fluir { to { stroke-dashoffset: -24; } }
@keyframes va-brillo { from { background-position: 0% 50%; } to { background-position: 100% 50%; } }
.va-banda, .va-riesgo, .va-aviso, .va-notif, .va-cols, .va-h2, .va-grafo { animation: va-entrar .55s var(--va-curva) both; }
.va-aviso { animation-delay: .12s; }
.va-notif { animation-delay: .08s; }
.va-ahora li { animation: va-entrar .5s var(--va-curva) both; }
.va-ahora li:nth-child(2) { animation-delay: .1s; } .va-ahora li:nth-child(3) { animation-delay: .2s; }
.va-ahora li:nth-child(4) { animation-delay: .3s; } .va-ahora li:nth-child(n+5) { animation-delay: .4s; }
@media (prefers-reduced-motion: reduce) {
  [class*="va-"], [class*="va-"]::before, [class*="va-"]::after, .st-key-firma,
  [data-testid="stBaseButton-primary"], [data-testid="stBaseButton-secondary"] { animation: none !important; transition: none !important; }
}

/* botones: degradado y una leve elevacion al pasar el mouse */
[data-testid="stBaseButton-primary"] { background: linear-gradient(135deg, #0f766e, #0d9488) !important; border: none !important;
  box-shadow: 0 6px 16px rgba(15, 118, 110, .28); transition: transform .15s var(--va-curva), box-shadow .15s; }
[data-testid="stBaseButton-primary"]:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 10px 22px rgba(15, 118, 110, .36); }
[data-testid="stBaseButton-secondary"] { transition: transform .15s var(--va-curva), border-color .15s, color .15s; }
[data-testid="stBaseButton-secondary"]:hover:not(:disabled) { transform: translateY(-1px); border-color: var(--va-acento) !important; color: var(--va-acento) !important; }
/* campos: anillo suave al escribir */
[data-baseweb="input"], [data-baseweb="textarea"] { transition: box-shadow .2s; }
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within { box-shadow: 0 0 0 3px rgba(20, 184, 166, .25); }
/* desplegables (caso analizado, proceso, evidencia) */
[data-testid="stExpander"] summary { transition: background .2s; border-radius: 10px; }
[data-testid="stExpander"] summary:hover { background: var(--va-suave); }
[data-testid="stMainBlockContainer"] { padding-top: 3.6rem !important; padding-bottom: 0 !important; max-width: 1380px; }

/* encabezado */
.va-banda { background: linear-gradient(115deg, #0b3d3a 0%, #0f5f59 45%, #0f766e 75%, #0e7490 100%);
  background-size: 160% 100%; animation: va-entrar .55s var(--va-curva) both, va-brillo 14s ease-in-out infinite alternate;
  color: #fff; border-radius: 16px; padding: 1.05rem 1.4rem; position: relative; overflow: hidden;
  display: flex; align-items: center; gap: 1rem; flex-wrap: wrap; box-shadow: 0 10px 30px rgba(11, 61, 58, .22); }
/* dos circulos decorativos, muy suaves */
.va-banda::before, .va-banda::after { content: ""; position: absolute; border-radius: 50%; pointer-events: none;
  background: radial-gradient(circle, rgba(94, 234, 212, .22), transparent 70%); }
.va-banda::before { width: 320px; height: 320px; right: -80px; top: -160px; }
.va-banda::after { width: 220px; height: 220px; right: 260px; bottom: -150px; }
.va-banda > * { position: relative; z-index: 1; }
.va-banda .logo { width: 46px; height: 46px; border-radius: 11px; background: #fff; color: var(--va-oscuro);
  font-weight: 900; display: grid; place-items: center; font-size: 1.15rem; }
.va-banda .t b { display: block; font-size: 1.4rem; letter-spacing: .08em; line-height: 1.15; }
.va-banda .t span { font-size: .95rem; opacity: .88; }
.va-banda .modo { margin-left: auto; background: rgba(255,255,255,.14); border: 1px solid rgba(255,255,255,.4);
  border-radius: 999px; padding: .3rem .9rem; font-size: .88rem; font-weight: 600;
  display: inline-flex; align-items: center; gap: .5rem; backdrop-filter: blur(4px); }
.va-banda .modo::before { content: ""; width: 8px; height: 8px; border-radius: 50%; background: #5eead4;
  animation: va-latido 2.2s ease-in-out infinite; }
.va-banda .nota { flex-basis: 100%; font-size: .85rem; opacity: .82; margin-top: -.25rem; }

/* titulos */
.va-h2 { font-size: 1.55rem; font-weight: 800; color: var(--va-tinta); margin: 1.3rem 0 .5rem;
  display: flex; align-items: baseline; gap: .7rem; flex-wrap: wrap; }
.va-h2 small { font-size: .92rem; font-weight: 500; color: var(--va-gris); }
.va-h3 { font-size: .82rem; letter-spacing: .13em; text-transform: uppercase; font-weight: 800;
  color: var(--va-oscuro); margin: .9rem 0 .35rem; padding-bottom: .3rem; border-bottom: 2px solid var(--va-oscuro);
  display: flex; align-items: center; gap: .5rem; }
/* cada seccion del formulario y cada dimension One Health con su color */
.va-h3.s1 { color: var(--va-acento); border-color: var(--va-acento); }
.va-h3.s2, .va-h3.oh-animal { color: var(--va-indigo); border-color: var(--va-indigo); }
.va-h3.s3 { color: var(--va-cielo); border-color: var(--va-cielo); }
.va-h3.s4, .va-h3.oh-humana { color: var(--va-violeta); border-color: var(--va-violeta); }
.va-h3.oh-ambiental { color: var(--va-cian); border-color: var(--va-cian); }
.va-h3 .num { display: inline-grid; place-items: center; width: 1.45rem; height: 1.45rem; border-radius: 50%;
  background: currentColor; letter-spacing: 0; font-size: .78rem; }
.va-h3 .num b { color: #fff; }
.va-h2::before { content: ""; width: 6px; align-self: stretch; border-radius: 3px; margin-right: .1rem;
  background: linear-gradient(180deg, var(--va-turquesa), var(--va-acento)); }
.va-vacio { color: var(--va-gris-2); font-style: italic; }

/* 1. nivel de riesgo */
.va-riesgo { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; border-radius: 16px; overflow: hidden; margin-top: .6rem; }
.va-riesgo { box-shadow: 0 14px 34px rgba(11, 18, 32, .12); }
.va-riesgo .nivel { color: #fff; padding: 1.3rem 1.5rem 1.2rem; position: relative; overflow: hidden;
  background-image: linear-gradient(160deg, rgba(255,255,255,.16), rgba(255,255,255,0) 55%); }
.va-riesgo .nivel .v { animation: va-pop .65s .15s var(--va-curva) both; }
.va-escala span.on { transform-origin: left; animation: va-crecer .7s .35s var(--va-curva) both;
  box-shadow: 0 0 10px rgba(255,255,255,.7); }
.va-riesgo .k { font-size: .85rem; letter-spacing: .14em; text-transform: uppercase; font-weight: 800; }
.va-riesgo .nivel .v { font-size: 3.6rem; font-weight: 900; line-height: 1.05; margin: .25rem 0 .8rem; }
.va-escala { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; }
.va-escala span { height: 9px; border-radius: 5px; background: rgba(255,255,255,.3); }
.va-escala span.on { background: #fff; }
.va-escala-et { display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; font-size: .74rem; margin-top: .3rem; opacity: .92; }
.va-riesgo .porque { padding: 1.3rem 1.7rem; background: linear-gradient(135deg, #f0fdfa, var(--va-suave) 60%, #eef2ff); }
.va-riesgo .porque .k { color: var(--va-oscuro); }
.va-riesgo .frase { font-size: 1.35rem; font-weight: 700; color: var(--va-tinta); line-height: 1.38; margin: .35rem 0 .7rem; }
.va-riesgo .res { font-size: 1rem; color: var(--va-texto); line-height: 1.55; margin: 0 0 .8rem; }
.va-meta { display: flex; gap: .5rem; flex-wrap: wrap; align-items: center; font-size: .9rem; color: var(--va-gris); }
.va-conf { background: var(--va-oscuro); color: #fff; font-weight: 700; border-radius: 999px; padding: .2rem .45rem .2rem .8rem;
  cursor: help; display: inline-flex; align-items: center; gap: .45rem; }
.va-ayuda { width: 1.15rem; height: 1.15rem; border-radius: 50%; background: #fff; color: var(--va-oscuro);
  display: inline-grid; place-items: center; font-size: .75rem; font-weight: 900; }
/* texto solo para lectores de pantalla (el title no siempre se lee) */
.va-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.va-aviso { display: flex; gap: .7rem; align-items: center; border-left: 6px solid var(--va-oscuro); background: #f1f3f5;
  padding: .75rem 1.1rem; font-weight: 600; color: var(--va-tinta); margin-top: .8rem; border-radius: 0 10px 10px 0; font-size: 1rem; }

/* 2. que hacer ahora */
.va-notif { display: flex; gap: .9rem; align-items: center; background: linear-gradient(110deg, var(--va-oscuro), #0f5f59 70%, var(--va-cian));
  color: #fff; border-radius: 12px; padding: .9rem 1.2rem; font-size: 1.08rem; margin: .3rem 0 .6rem;
  box-shadow: 0 8px 22px rgba(11, 61, 58, .2); }
.va-notif .ico { font-size: 1.5rem; display: inline-block; transform-origin: 50% 20%;
  animation: va-campana 1.1s .6s ease-in-out 2; }
.va-notif.dudosa { background: #fff; color: var(--va-tinta); border: 2px solid var(--va-oscuro); }
.va-ahora { list-style: none; counter-reset: n; margin: 0; padding: 0; }
.va-ahora li { counter-increment: n; display: grid; grid-template-columns: 2.6rem 1fr; gap: .9rem;
  padding: .8rem .6rem; border-bottom: 1px solid var(--va-linea); border-radius: 10px;
  transition: background .2s, transform .2s var(--va-curva); }
.va-ahora li:hover { background: #f0fdfa; transform: translateX(4px); }
.va-ahora li::before { content: counter(n); width: 2.3rem; height: 2.3rem; border-radius: 50%;
  background: linear-gradient(135deg, var(--va-turquesa), var(--va-acento)); box-shadow: 0 4px 10px rgba(15, 118, 110, .3);
  color: #fff; font-weight: 800; display: grid; place-items: center; font-size: 1.1rem; }
.va-ahora .acc { font-size: 1.2rem; font-weight: 700; color: var(--va-tinta); line-height: 1.35; }
.va-ahora .det { font-size: .9rem; color: var(--va-gris); margin-top: .2rem; }
.va-cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.6rem; }
.va-cols ul { margin: 0; padding-left: 1.15rem; }
.va-cols li { font-size: .98rem; color: var(--va-texto); margin-bottom: .4rem; line-height: 1.45; }
.va-tag { display: inline-block; font-size: .72rem; font-weight: 700; padding: .05rem .45rem; border-radius: 4px;
  background: var(--va-suave); color: var(--va-oscuro); margin-left: .25rem; vertical-align: middle; white-space: nowrap; }
.va-nota { color: var(--va-acento); font-weight: 800; font-size: .72em; margin-left: 1px; }

/* diagrama del grafo (solo mientras corre, o al desplegar el resumen del proceso) */
.va-grafo { background: #fff; border: 1px solid var(--va-linea); border-radius: 12px; padding: .2rem .6rem .1rem; }
.va-grafo svg { width: 100%; height: auto; display: block; max-height: 140px; }
.va-grafo-pie { display: flex; justify-content: space-between; gap: 1rem; font-size: .85rem; color: var(--va-gris); padding: 0 .4rem .35rem; }
.va-grafo-pie b { color: var(--va-oscuro); }
.va-nodo rect { fill: #fff; stroke: #9aa8a6; stroke-width: 1.5; }
.va-nodo .n { font: 700 14.5px system-ui, "Segoe UI", sans-serif; fill: var(--va-tinta); }
.va-nodo .e { font: 12.5px system-ui, "Segoe UI", sans-serif; fill: var(--va-gris); }
.va-nodo.pensando rect { fill: var(--va-suave); stroke: var(--va-acento); stroke-width: 3; animation: va-pulso 1.3s ease-in-out infinite; }
.va-nodo.pensando .e { fill: var(--va-oscuro); font-weight: 700; }
.va-nodo.listo rect, .va-nodo.aprobado rect { fill: var(--va-oscuro); stroke: var(--va-oscuro); }
.va-nodo.listo text, .va-nodo.aprobado text { fill: #fff; }
.va-nodo.omitido rect { fill: #f6f7f7; stroke-dasharray: 5 4; }
.va-nodo.omitido text { fill: #9aa3a1; }
.va-nodo.error rect { fill: #fef2f2; stroke: #b91c1c; stroke-width: 2.5; }
.va-nodo.error .e { fill: #b91c1c; font-weight: 700; }
.va-nodo.pausa rect { fill: #fff; stroke: var(--va-acento); stroke-width: 2.5; stroke-dasharray: 7 4; animation: va-hormigas 1s linear infinite; }
.va-nodo.pausa .e { fill: var(--va-oscuro); font-weight: 700; }
.va-flecha { stroke: #b8c2c0; stroke-width: 2; fill: none; }
.va-punta { fill: #b8c2c0; }
.va-flecha.activa { stroke: var(--va-acento); }
.va-punta.activa { fill: var(--va-acento); }
.va-flecha.vuelta { stroke-dasharray: 5 5; }
/* la flecha que entra al agente que esta pensando "fluye" hacia el */
.va-flecha.fluye { stroke: var(--va-turquesa); stroke-width: 3; stroke-dasharray: 6 6; animation: va-fluir .7s linear infinite; }
.va-punta.fluye { fill: var(--va-turquesa); }
.va-nodo.pensando rect { filter: drop-shadow(0 0 6px rgba(20, 184, 166, .55)); }
.va-rotulo { font: 12px system-ui, "Segoe UI", sans-serif; fill: #8a9593; }
.va-rotulo.activa { fill: var(--va-oscuro); font-weight: 700; }
@keyframes va-pulso { 0%, 100% { stroke-opacity: 1; } 50% { stroke-opacity: .25; } }
@keyframes va-hormigas { to { stroke-dashoffset: -22; } }

/* modo equipo: comparacion */
.va-carta { background: #fff; border: 1px solid var(--va-linea); border-radius: 12px; padding: 1rem 1.15rem; height: 100%; }
.va-ver .cab { display: flex; justify-content: space-between; align-items: baseline; }
.va-ver .id { font-size: 1.5rem; font-weight: 800; color: var(--va-oscuro); }
.va-ver .nom { font-size: .88rem; color: var(--va-gris); }
.va-kpis { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; margin: .7rem 0; }
.va-kpi { background: var(--va-suave); border-radius: 8px; padding: .45rem .6rem; }
.va-kpi .k { font-size: .7rem; color: var(--va-gris); text-transform: uppercase; letter-spacing: .08em; }
.va-kpi .v { font-size: 1.15rem; font-weight: 700; color: var(--va-tinta); }
.va-ver .res { font-size: .88rem; color: var(--va-texto); display: -webkit-box; -webkit-line-clamp: 6; -webkit-box-orient: vertical; overflow: hidden; }
.va-ver.falla { border-top: 4px solid #b91c1c; }
.va-pill { display: inline-block; font-size: .75rem; font-weight: 800; border-radius: 999px; padding: .1rem .6rem; color: #fff; }
.va-fila-pills { display: flex; gap: .35rem; flex-wrap: wrap; margin: .2rem 0 .4rem; }

/* barra de firma fija abajo: Streamlit envuelve el contenedor en un stLayoutWrapper de su
   mismo alto, asi que lo "sticky" tiene que ser el envoltorio */
div[data-testid="stLayoutWrapper"]:has(> .st-key-firma) { position: sticky; bottom: 0; z-index: 60; }
.st-key-firma { background: #fff; border-radius: 14px 14px 0 0; position: relative;
  padding: .8rem 1.1rem .85rem; box-shadow: 0 -10px 28px rgba(11, 18, 32, .14); margin-top: 1.2rem;
  animation: va-entrar .5s .2s var(--va-curva) both; }
.st-key-firma::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 5px; border-radius: 14px 14px 0 0;
  background: linear-gradient(90deg, var(--va-oscuro), var(--va-turquesa), var(--va-cian)); }
.va-firma-t { font-size: .85rem; letter-spacing: .12em; text-transform: uppercase; font-weight: 800; color: var(--va-oscuro); }
@media (max-width: 800px) { .va-riesgo { grid-template-columns: 1fr; } }
</style>
"""


def encabezado(modo: str, nota: str) -> str:
    return (f'<div class="va-banda"><div class="logo">VA</div><div class="t"><b>VET·AGENT</b>'
            f'<span>Apoyo a la decisión epidemiológica · One Health</span></div>'
            f'<span class="modo">{esc(modo)}</span><div class="nota">{esc(nota)}</div></div>')


def h2(titulo: str, sub: str = "") -> str:
    return f'<div class="va-h2">{esc(titulo)}' + (f"<small>{esc(sub)}</small>" if sub else "") + "</div>"


def h3(titulo: str, clase: str = "") -> str:
    """Titulo de seccion. "1 · Plantel..." toma el color de su seccion y el numero va en un circulo."""
    numero, sep, resto = titulo.partition(" · ")
    if sep and numero.isdigit():
        return f'<div class="va-h3 s{numero} {clase}"><span class="num"><b>{numero}</b></span>{esc(resto)}</div>'
    return f'<div class="va-h3 {clase}">{esc(titulo)}</div>'


# ====================== diagrama del grafo ======================

def grafo_inicial() -> dict:
    """Estado del diagrama antes de analizar: todos los nodos en espera."""
    return {"nodos": {n: {"estado": "espera", "segundos": None} for n in NODOS},
            "ronda": 0, "debate": False, "rechazo": False, "debates": 0, "rechazos": 0, "nota": ""}


def siguiente_nodo(nodo: str, cambios: dict | None) -> str | None:
    """Nodo que empieza cuando termina `nodo` (la cadena es fija y corre de a uno).

    Despues del moderador y de la revision humana decide la misma funcion que usa el grafo.
    None = el grafo termino.
    """
    if nodo in ("climatico", "diagnostico", "bioseguridad"):
        return NODOS[NODOS.index(nodo) + 1]
    if nodo == "moderador" and cambios and "evaluacion" in cambios:
        return despues_del_moderador(cambios)
    if nodo == "revision_humana" and cambios and "decision_humana" in cambios:
        destino = despues_de_revision_humana(cambios)
        return destino if destino in NODOS else None
    return None


def estado_nodo(n: dict) -> tuple[str, str]:
    """(clase css, texto bajo el nombre) de un nodo."""
    estado, seg = n.get("estado", "espera"), n.get("segundos")
    textos = {"espera": "en espera", "pensando": "pensando…", "omitido": "no consultado",
              "error": "✕ falló", "pausa": "esperando tu firma", "aprobado": "✓ aprobado"}
    if estado == "listo":
        return "listo", "✓ listo" + (f" · {duracion(seg)}" if seg is not None else "")
    return estado, textos.get(estado, estado)


def _punta(x: float, y: float, direccion: str, clase: str) -> str:
    # triangulos dibujados a mano (sin <marker>, que no siempre sobrevive al filtro de HTML)
    p = {"der": f"{x},{y} {x - 9},{y - 5} {x - 9},{y + 5}",
         "arr": f"{x},{y} {x - 5},{y + 9} {x + 5},{y + 9}",
         "aba": f"{x},{y} {x - 5},{y - 9} {x + 5},{y - 9}"}[direccion]
    return f'<polygon class="va-punta{clase}" points="{p}"/>'


def diagrama_grafo(vista: dict) -> str:
    """SVG del grafo: los 5 nodos, la flecha de debate del moderador y la del rechazo."""
    nodos, ancho, alto, y = vista["nodos"], 168, 58, 46
    xs = [8 + i * 204 for i in range(len(NODOS))]
    centro = {n: x + ancho / 2 for n, x in zip(NODOS, xs)}
    partes = []
    for i in range(len(NODOS) - 1):
        activa = " activa" if nodos[NODOS[i]]["estado"] in ("listo", "omitido") else ""
        if activa and nodos[NODOS[i + 1]]["estado"] == "pensando":
            activa = " fluye"  # el trabajo "fluye" hacia el agente que esta pensando
        x1, x2, ym = xs[i] + ancho, xs[i + 1], y + alto / 2
        partes.append(f'<line class="va-flecha{activa}" x1="{x1}" y1="{ym}" x2="{x2 - 2}" y2="{ym}"/>')
        partes.append(_punta(x2, ym, "der", activa))

    act = " activa" if vista.get("debate") else ""
    xm, xc, yb = centro["moderador"], centro["climatico"], y + alto
    partes.append(f'<path class="va-flecha vuelta{act}" d="M{xm} {yb} V{yb + 26} H{xc} V{yb + 4}"/>')
    partes.append(_punta(xc, yb + 1, "arr", act))
    partes.append(f'<text class="va-rotulo{act}" x="{(xm + xc) / 2}" y="{yb + 22}" text-anchor="middle">'
                  f'ronda de debate: el moderador pregunta a un agente</text>')

    act = " activa" if vista.get("rechazo") else ""
    xh, xm = centro["revision_humana"], centro["moderador"]
    partes.append(f'<path class="va-flecha vuelta{act}" d="M{xh} {y} V{y - 26} H{xm} V{y - 4}"/>')
    partes.append(_punta(xm, y - 1, "aba", act))
    partes.append(f'<text class="va-rotulo{act}" x="{(xh + xm) / 2}" y="{y - 31}" text-anchor="middle">'
                  f'rechazo con comentario</text>')

    for nodo, x in zip(NODOS, xs):
        clase, texto = estado_nodo(nodos[nodo])
        partes.append(
            f'<g class="va-nodo {clase}"><rect x="{x}" y="{y}" width="{ancho}" height="{alto}" rx="11"/>'
            f'<text class="n" x="{x + 13}" y="{y + 24}">{ICONO_NODO[nodo]} {esc(NOMBRE_NODO[nodo])}</text>'
            f'<text class="e" x="{x + 13}" y="{y + 44}">{esc(texto)}</text></g>')

    ronda = vista.get("ronda") or 0
    pie_der = f"Ronda del moderador: <b>{ronda}</b>" if ronda else ""
    return (f'<div class="va-grafo"><svg viewBox="0 0 1000 140" role="img" '
            f'aria-label="Avance de los agentes">{"".join(partes)}</svg>'
            f'<div class="va-grafo-pie"><span>{esc(vista.get("nota", ""))}</span><span>{pie_der}</span></div></div>')


def resumen_proceso(vista: dict, segundos_analisis, segundos_revision) -> str:
    """Una linea para cuando el analisis termino: "4 agentes · 2 min 31 s · 1 ronda de debate"."""
    partes = ["✓ 4 agentes"]
    if segundos_analisis is not None:
        partes.append(duracion(segundos_analisis))
    debates = vista.get("debates", 0)
    partes.append("sin rondas de debate" if not debates else
                  f"{debates} ronda{'s' if debates > 1 else ''} de debate")
    rechazos = vista.get("rechazos", 0)
    if rechazos:
        partes.append(f"{rechazos} revisión{'es' if rechazos > 1 else ''} por rechazo"
                      + (f" (la última, {duracion(segundos_revision)})" if segundos_revision is not None else ""))
    return " · ".join(partes)


# ====================== informe: apoyo a la decision ======================

def numerar_citas(informe: dict) -> dict[str, int]:
    """doc_id -> numero de nota, en el orden en que aparecen en el informe."""
    orden: dict[str, int] = {}

    def agregar(ids):
        for i in ids or []:
            if isinstance(i, str) and i.strip() and i not in orden:
                orden[i] = len(orden) + 1

    for r in informe.get("riesgos_identificados") or []:
        agregar(como_dict(r, "riesgo").get("evidencia"))
    for r in informe.get("recomendaciones") or []:
        agregar(como_dict(r, "accion").get("fuentes"))
    for f in informe.get("fuentes_utilizadas") or []:
        agregar([como_dict(f, "doc_id").get("doc_id")])
    return orden


def sup(ids, numeros: dict[str, int]) -> str:
    ns = sorted({numeros[i] for i in ids or [] if isinstance(i, str) and i in numeros})
    return f'<sup class="va-nota">{",".join(map(str, ns))}</sup>' if ns else ""


def indice_evidencia(resultados: dict) -> dict[str, dict]:
    """doc_id -> {titulo, institucion, anio, fragmentos: [{agente, chunk, texto}]} con lo que recuperaron los agentes."""
    indice: dict[str, dict] = {}
    for agente, salida in (resultados or {}).items():
        for e in (salida or {}).get("evidencia", []):
            doc = indice.setdefault(e["doc_id"], {"titulo": e.get("titulo", ""), "institucion": e.get("institucion", ""),
                                                  "anio": e.get("anio", ""), "fragmentos": []})
            doc["fragmentos"].append({"agente": agente, "chunk": e.get("chunk", ""), "texto": e.get("texto", "")})
    return indice


ORDEN_CONFIANZA = ["baja", "media", "alta"]
# la confianza general no la entrega el moderador: la calcula la demo, y el texto de ayuda lo explica
AYUDA_CONFIANZA = ("Calculada a partir de la confianza de cada riesgo identificado: "
                   "la más frecuente y, si hay empate, la más baja.")
AYUDA_CONFIANZA_AGENTES = ("Calculada a partir de la confianza de cada agente, porque los riesgos del informe "
                           "no la indican: la más frecuente y, si hay empate, la más baja.")


def _confianzas_de_riesgos(informe: dict) -> list[str]:
    valores = [str(como_dict(r, "riesgo").get("confianza", "")).lower() for r in informe.get("riesgos_identificados") or []]
    return [v for v in valores if v in ORDEN_CONFIANZA]


def confianza_global(informe: dict, resultados: dict) -> str | None:
    """La confianza que mas se repite en los riesgos del informe (o, si no hay, en los agentes).

    En un empate gana la mas baja: ante la duda, mejor no sobrestimar.
    """
    valores = _confianzas_de_riesgos(informe)
    if not valores:
        valores = [str((s or {}).get("confianza", "")).lower() for s in (resultados or {}).values()]
        valores = [v for v in valores if v in ORDEN_CONFIANZA]
    if not valores:
        return None
    cuenta = Counter(valores)
    return max(cuenta, key=lambda v: (cuenta[v], -ORDEN_CONFIANZA.index(v)))


def primera_frase(texto) -> str:
    texto = str(texto or "").strip()
    corte = re.search(r"[.!?](\s|$)", texto)
    return texto[:corte.end()].strip() if corte else texto


def bloque_riesgo(informe: dict, confianza: str | None, meta: str, sin_dato: str) -> str:
    """Lo primero que se lee: el nivel de riesgo grande, una frase de por que y la confianza."""
    nivel = str(informe.get("nivel_riesgo_global") or "").lower()
    color = COLOR_RIESGO.get(nivel, "#4b5563")
    escala = "".join(f'<span class="{"on" if n == nivel else ""}"></span>' for n in NIVELES)
    etiquetas = "".join(f"<span>{n.replace('_', ' ')}</span>" for n in NIVELES)
    frase = primera_frase(informe.get("justificacion_riesgo")) or primera_frase(informe.get("resumen_ejecutivo"))
    resumen = informe.get("resumen_ejecutivo") or ""
    # "estimada": la calcula la demo (ver confianza_global), no es un dato del modelo
    ayuda = AYUDA_CONFIANZA if _confianzas_de_riesgos(informe) else AYUDA_CONFIANZA_AGENTES
    meta_html = (f'<span class="va-conf" title="{esc(ayuda)}">Confianza estimada: {esc(confianza)}'
                 f'<span class="va-ayuda" aria-hidden="true">?</span></span>'
                 f'<span class="va-sr">{esc(ayuda)}</span>' if confianza else "") + \
        (f"<span>{esc(meta)}</span>" if meta else "")
    return (f'<div class="va-riesgo"><div class="nivel" style="background:{color}">'
            f'<div class="k">Nivel de riesgo</div><div class="v">{nivel_texto(nivel) or "SIN NIVEL"}</div>'
            f'<div class="va-escala">{escala}</div><div class="va-escala-et">{etiquetas}</div></div>'
            f'<div class="porque"><div class="k">Por qué</div>'
            + (f'<p class="frase">{esc(frase)}</p>' if frase else f'<p class="frase va-vacio">{esc(sin_dato)}</p>')
            + (f'<p class="res">{esc(resumen)}</p>' if resumen and resumen.strip() != frase else "")
            + f'<div class="va-meta">{meta_html}</div></div></div>')


def aviso(texto: str) -> str:
    return f'<div class="va-aviso"><span>⚠</span><span>{esc(texto)}</span></div>'


def notificacion(resultados: dict) -> dict:
    """Si el caso es de notificacion obligatoria y a quien, segun el agente de bioseguridad."""
    bio = (resultados or {}).get("bioseguridad") or {}
    autoridades = bio.get("autoridad_competente") or []
    if isinstance(autoridades, str):
        autoridades = [autoridades]
    return {"estado": str(bio.get("notificacion_obligatoria") or "").lower() or None,
            "autoridades": [str(a) for a in autoridades]}


def bloque_notificacion(noti: dict) -> str:
    quien = ", ".join(noti["autoridades"]) or "la autoridad competente"
    if noti["estado"] == "si":
        return (f'<div class="va-notif"><span class="ico">📣</span><span><b>Notificación obligatoria:</b> '
                f'avisar a {esc(quien)}.</span></div>')
    if noti["estado"] == "no":
        return (f'<div class="va-notif dudosa"><span class="ico">📣</span><span>Según la evidencia, '
                f'<b>no es de notificación obligatoria</b>. Ante la duda, consulta a {esc(quien)}.</span></div>')
    if noti["estado"]:
        return (f'<div class="va-notif dudosa"><span class="ico">📣</span><span><b>Notificación obligatoria: '
                f'no determinable</b> con la evidencia recuperada. Consulta a {esc(quien)}.</span></div>')
    return ""


def _detalle_recomendacion(r: dict) -> str:
    partes = []
    if r.get("responsable_sugerido"):
        partes.append(f"Responsable: {esc(r['responsable_sugerido'])}")
    if r.get("requiere_autoridad"):
        partes.append("requiere autorización o ejecución de la autoridad sanitaria")
    return " · ".join(partes)


def recomendaciones_por_prioridad(recomendaciones: list) -> dict[str, list[dict]]:
    grupos = {"inmediata": [], "corto_plazo": [], "preventiva": [], "otra": []}
    for r in recomendaciones or []:
        r = como_dict(r, "accion")
        grupos.get(str(r.get("prioridad", "")).lower(), grupos["otra"]).append(r)
    return grupos


def acciones_inmediatas(inmediatas: list[dict], numeros: dict[str, int], sin_dato: str) -> str:
    if not inmediatas:
        return f'<p class="va-vacio">{esc(sin_dato)}</p>'
    items = "".join(f'<li><div><div class="acc">{esc(r.get("accion", ""))}{sup(r.get("fuentes"), numeros)}</div>'
                    f'<div class="det">{_detalle_recomendacion(r)}</div></div></li>' for r in inmediatas)
    return f'<ol class="va-ahora">{items}</ol>'


def _lista(items, vacio: str) -> str:
    if not items:
        return f'<p class="va-vacio">{esc(vacio)}</p>'
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def columnas(bloques: list[tuple[str, list[str], str]], clases: list[str] | None = None) -> str:
    """Columnas con titulo y lista; cada item ya viene como HTML escapado. `clases` da color a cada titulo."""
    clases = (clases or []) + [""] * len(bloques)
    return '<div class="va-cols">' + "".join(
        f"<div>{h3(titulo, clase)}{_lista(items, vacio)}</div>"
        for (titulo, items, vacio), clase in zip(bloques, clases)) + "</div>"


def items_recomendacion(recs: list[dict], numeros: dict[str, int]) -> list[str]:
    return [f'{esc(r.get("accion", ""))}{sup(r.get("fuentes"), numeros)}'
            + (' <span class="va-tag">autoridad</span>' if r.get("requiere_autoridad") else "") for r in recs]


def riesgos_one_health(riesgos: list, numeros: dict[str, int]) -> str:
    """Los riesgos del informe en 3 columnas compactas: animal, humana y ambiental."""
    bloques = []
    todos = [como_dict(r, "riesgo") for r in riesgos or []]
    for dim, titulo, icono in DIMENSIONES:
        propios = [r for r in todos if str(r.get("dimension_one_health", "")).lower() == dim]
        items = [f'{esc(r.get("riesgo", ""))}{sup(r.get("evidencia"), numeros)}'
                 + (f' <span class="va-tag">confianza {esc(r["confianza"])}</span>' if r.get("confianza") else "")
                 for r in propios]
        bloques.append((f"{icono} {titulo} · {len(propios)}", items, "Sin riesgos en esta dimensión."))
    sin_dim = [r for r in todos if str(r.get("dimension_one_health", "")).lower() not in {d for d, _, _ in DIMENSIONES}]
    if sin_dim:
        bloques.append((f"Otros · {len(sin_dim)}", [esc(r.get("riesgo", "")) for r in sin_dim], ""))
    return columnas(bloques, [f"oh-{dim}" for dim, _, _ in DIMENSIONES])


def para_decidir_mejor(informe: dict, sin_dato: str) -> str:
    return columnas([
        ("Falta información", [esc(i) for i in informe.get("informacion_faltante") or []], sin_dato),
        ("Diagnósticos a descartar", [esc(i) for i in informe.get("hipotesis_alternativas") or []], sin_dato),
        ("Incertidumbres", [esc(i) for i in informe.get("incertidumbres_y_limitaciones") or []], sin_dato),
    ])


def resumen_agente(agente: str, salida: dict) -> str:
    """Una linea con lo principal que concluyo cada especialista."""
    campos = {"climatico": [("riesgo ambiental", "nivel_riesgo_ambiental"), ("ventana", "ventana_temporal")],
              "diagnostico": [("presencia", "probabilidad_presencia"), ("confirmar", "requiere_confirmacion")],
              "bioseguridad": [("notificación", "notificacion_obligatoria"), ("autoridad", "autoridad_competente")]}
    partes = []
    for nombre, clave in campos.get(agente, []) + [("confianza", "confianza")]:
        valor = salida.get(clave)
        if valor in (None, "", []):
            continue
        if isinstance(valor, bool):
            valor = "sí" if valor else "no"
        elif isinstance(valor, list):
            valor = ", ".join(map(str, valor))
        partes.append(f'<span class="va-tag">{esc(nombre)}: {esc(str(valor).replace("_", " "))}</span>')
    return f'<div class="va-fila-pills">{"".join(partes)}</div>'


# ====================== modo equipo: comparacion B1 / B2 / B3 ======================

def pill_riesgo(nivel) -> str:
    nivel = str(nivel or "").lower()
    color = COLOR_RIESGO.get(nivel, "#6b7280")
    return f'<span class="va-pill" style="background:{color}">{nivel_texto(nivel) or "SIN NIVEL"}</span>'


def archivos_comparacion(carpeta: pathlib.Path) -> dict[str, pathlib.Path]:
    """Los comparacion*.csv de resultados/, con su ruta relativa como nombre (el de la raiz primero)."""
    if not carpeta.is_dir():
        return {}
    rutas = sorted(carpeta.rglob("comparacion*.csv"), key=lambda p: (len(p.relative_to(carpeta).parts), str(p)))
    return {p.relative_to(carpeta).as_posix(): p for p in rutas}


def leer_comparacion(ruta: pathlib.Path) -> list[dict]:
    """Lee un comparacion.csv de evaluar.py, en el formato viejo (llm_sin_rag...) o el nuevo (B1, escenario...)."""
    filas = []
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            texto = fila.get("informe") or ""
            informe, error = None, None
            if texto.startswith("ERROR"):
                error = texto
            else:
                try:
                    informe = json.loads(texto)
                except json.JSONDecodeError:
                    error = "El informe guardado no es un JSON válido."
                if not isinstance(informe, dict):
                    informe, error = None, error or "El informe guardado no tiene el formato esperado."
            try:
                segundos = float(fila.get("segundos") or 0)
            except ValueError:
                segundos = 0.0
            try:
                fuentes = int(fila.get("fuentes_citadas") or 0)
            except ValueError:
                fuentes = 0
            configuracion = fila.get("configuracion", "")
            filas.append({"version": ALIAS_VERSION.get(configuracion, configuracion),
                          "escenario": fila.get("escenario") or "CASO_H5N1",
                          "repeticion": fila.get("repeticion") or "1",
                          "informe": informe, "error": error, "fuentes": fuentes, "segundos": segundos})
    return filas


def tarjeta_version(version: str, fila: dict | None) -> str:
    nombre = VERSIONES.get(version, version)
    cab = f'<div class="cab"><span class="id">{esc(version)}</span><span class="nom">{esc(nombre)}</span></div>'
    if fila is None:
        return f'<div class="va-carta va-ver">{cab}<p class="va-vacio">Sin datos de esta versión en la corrida.</p></div>'
    if fila["error"]:
        return (f'<div class="va-carta va-ver falla">{cab}<p style="color:#b91c1c;font-weight:700">✕ Falló a los '
                f'{esc(duracion(fila["segundos"]))}</p><div class="res">{esc(fila["error"][:220])}</div></div>')
    inf = fila["informe"]
    kpis = [("Tiempo", duracion(fila["segundos"])), ("Fuentes citadas", fila["fuentes"]),
            ("Riesgos", len(inf.get("riesgos_identificados") or [])),
            ("Recomendaciones", len(inf.get("recomendaciones") or []))]
    kpis_html = "".join(f'<div class="va-kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
                        for k, v in kpis)
    return (f'<div class="va-carta va-ver">{cab}<div style="margin-top:.5rem">{pill_riesgo(inf.get("nivel_riesgo_global"))}'
            f'</div><div class="va-kpis">{kpis_html}</div>'
            f'<div class="res">{esc(inf.get("resumen_ejecutivo", ""))}</div></div>')
