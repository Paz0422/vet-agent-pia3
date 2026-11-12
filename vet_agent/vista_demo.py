"""Piezas visuales de la demo (app_demo.py): CSS, HTML y los datos que se dibujan.

Aca no hay widgets de Streamlit (botones, campos): solo funciones que devuelven HTML o
datos, para que app_demo.py quede con la logica de la pantalla y esto se pueda probar
sin abrir la demo. Todo texto que viene del caso, del LLM o de los documentos pasa por
esc() antes de ir al HTML: el modelo podria devolver algo como "<script>".
"""
import csv
import html
import json
import math
import pathlib

from vet_agent.grafo import despues_del_moderador, despues_de_revision_humana

ACENTO = "#0f766e"
# rojo / ambar / verde se usan SOLO para el nivel de riesgo
COLOR_RIESGO = {"bajo": "#15803d", "moderado": "#d97706", "alto": "#dc2626", "muy_alto": "#991b1b"}
NIVELES = ["bajo", "moderado", "alto", "muy_alto"]

# nodos del grafo, en el orden en que se dibujan
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


# ---------- estilos ----------

CSS = """
<style>
:root {
  --va-acento: #0f766e; --va-acento-2: #115e59; --va-acento-suave: #f0fdfa; --va-acento-borde: #99f6e4;
  --va-tinta: #0f172a; --va-gris: #64748b; --va-gris-claro: #94a3b8; --va-borde: #e2e8f0;
  --va-fondo: #f8fafc; --va-carta: #ffffff;
}
[data-testid="stMainBlockContainer"] { padding-top: 4.2rem !important; padding-bottom: 0 !important; max-width: 1500px; }
section[data-testid="stSidebar"] { min-width: 360px; }
section[data-testid="stSidebar"] .va-lado-titulo {
  font-size: .72rem; letter-spacing: .12em; text-transform: uppercase; color: var(--va-gris);
  font-weight: 700; margin: .4rem 0 .2rem;
}

/* encabezado */
.va-enc { display: flex; align-items: center; gap: 1.2rem; flex-wrap: wrap;
  padding: .2rem 0 .9rem; border-bottom: 1px solid var(--va-borde); margin-bottom: .6rem; }
.va-marca { display: flex; align-items: center; gap: .65rem; }
.va-logo { width: 38px; height: 38px; border-radius: 10px; background: var(--va-acento);
  display: grid; place-items: center; color: #fff; font-weight: 800; font-size: 1.05rem; }
.va-marca b { display: block; font-size: 1.15rem; letter-spacing: .06em; color: var(--va-tinta); }
.va-marca span { font-size: .8rem; color: var(--va-gris); }
.va-chips { display: flex; gap: .4rem; flex-wrap: wrap; flex: 1; }
.va-chip { border: 1px solid var(--va-borde); background: var(--va-carta); border-radius: 999px;
  padding: .18rem .7rem; font-size: .82rem; color: var(--va-tinta); white-space: nowrap; }
.va-chip i { font-style: normal; color: var(--va-gris); margin-right: .3rem; }
.va-modo { border-radius: 999px; padding: .22rem .75rem; font-size: .8rem; font-weight: 600;
  background: var(--va-acento-suave); color: var(--va-acento-2); border: 1px solid var(--va-acento-borde); }
.va-modo::before { content: "●"; margin-right: .35rem; }
.va-aviso { font-size: .78rem; color: var(--va-gris); margin: -.2rem 0 .4rem; }

/* diagrama del grafo */
.va-grafo { background: var(--va-carta); border: 1px solid var(--va-borde); border-radius: 14px;
  padding: .3rem .6rem .2rem; }
.va-grafo svg { width: 100%; height: auto; display: block; max-height: 165px; }
.va-grafo-pie { display: flex; justify-content: space-between; gap: 1rem; font-size: .8rem;
  color: var(--va-gris); padding: 0 .4rem .35rem; }
.va-grafo-pie b { color: var(--va-acento-2); }
.va-nodo rect { fill: #fff; stroke: #cbd5e1; stroke-width: 1.5; }
.va-nodo .n { font: 600 14.5px system-ui, "Segoe UI", sans-serif; fill: var(--va-tinta); }
.va-nodo .e { font: 12.5px system-ui, "Segoe UI", sans-serif; fill: var(--va-gris); }
.va-nodo.pensando rect { fill: var(--va-acento-suave); stroke: var(--va-acento); stroke-width: 2.5;
  animation: va-pulso 1.3s ease-in-out infinite; }
.va-nodo.pensando .e { fill: var(--va-acento-2); font-weight: 600; }
.va-nodo.listo rect, .va-nodo.aprobado rect { fill: var(--va-acento); stroke: var(--va-acento); }
.va-nodo.listo text, .va-nodo.aprobado text { fill: #fff; }
.va-nodo.omitido rect { fill: var(--va-fondo); stroke-dasharray: 5 4; }
.va-nodo.omitido text { fill: var(--va-gris-claro); }
.va-nodo.error rect { fill: #fef2f2; stroke: #b91c1c; stroke-width: 2; }
.va-nodo.error .e { fill: #b91c1c; font-weight: 600; }
.va-nodo.pausa rect { fill: #fff; stroke: var(--va-acento); stroke-width: 2; stroke-dasharray: 7 4;
  animation: va-hormigas 1s linear infinite; }
.va-nodo.pausa .e { fill: var(--va-acento-2); font-weight: 600; }
.va-flecha { stroke: #cbd5e1; stroke-width: 2; fill: none; }
.va-punta { fill: #cbd5e1; }
.va-flecha.activa { stroke: var(--va-acento); }
.va-punta.activa { fill: var(--va-acento); }
.va-flecha.vuelta { stroke-dasharray: 5 5; }
.va-rotulo { font: 12px system-ui, "Segoe UI", sans-serif; fill: var(--va-gris-claro); }
.va-rotulo.activa { fill: var(--va-acento-2); font-weight: 600; }
@keyframes va-pulso { 0%, 100% { stroke-opacity: 1; } 50% { stroke-opacity: .25; } }
@keyframes va-hormigas { to { stroke-dashoffset: -22; } }

/* tarjetas */
.va-carta { background: var(--va-carta); border: 1px solid var(--va-borde); border-radius: 14px;
  padding: 1rem 1.15rem; height: 100%; }
.va-rot { font-size: .72rem; letter-spacing: .12em; text-transform: uppercase; color: var(--va-gris);
  font-weight: 700; margin-bottom: .45rem; }
.va-resumen p { font-size: 1.02rem; line-height: 1.55; color: var(--va-tinta); margin: 0 0 .6rem; }
.va-resumen .just { font-size: .92rem; color: #334155; }
.va-meta { font-size: .78rem; color: var(--va-gris); margin-top: .4rem; }
.va-nota { color: var(--va-acento); font-weight: 700; font-size: .72em; margin-left: 1px; }
.va-pend { color: var(--va-gris-claro); font-style: italic; font-size: .88rem; }

/* medidor */
.va-medidor { text-align: center; }
.va-medidor svg { width: 100%; max-width: 270px; height: auto; }
.va-medidor .nivel { font-size: 1.9rem; font-weight: 800; letter-spacing: .05em; line-height: 1; }
.va-medidor .sub { font-size: .78rem; color: var(--va-gris); margin-top: .3rem; }

/* One Health */
.va-oh { border-top: 4px solid var(--va-acento); }
.va-oh .cab { display: flex; align-items: center; justify-content: space-between; }
.va-oh .dim { font-weight: 700; font-size: 1.02rem; color: var(--va-tinta); }
.va-oh .ico { font-size: 1.35rem; margin-right: .35rem; }
.va-oh .num { font-size: 1.7rem; font-weight: 800; color: var(--va-acento-2); line-height: 1; }
.va-oh .num small { font-size: .72rem; font-weight: 600; color: var(--va-gris); margin-left: .25rem; }
.va-oh ul { margin: .55rem 0 0; padding-left: 1.05rem; }
.va-oh li { font-size: .86rem; color: #334155; margin-bottom: .25rem; }
.va-oh .nota-ag { font-size: .82rem; color: var(--va-gris); margin-top: .5rem; }

/* listas del informe */
.va-item { border: 1px solid var(--va-borde); border-left: 4px solid var(--va-acento-borde);
  border-radius: 10px; padding: .6rem .8rem; margin-bottom: .5rem; background: var(--va-carta); }
.va-item b { color: var(--va-tinta); font-weight: 600; }
.va-item .pie { margin-top: .3rem; display: flex; gap: .35rem; flex-wrap: wrap; }
.va-pill { font-size: .72rem; border-radius: 999px; padding: .08rem .55rem; border: 1px solid var(--va-borde);
  color: #334155; background: var(--va-fondo); white-space: nowrap; }
.va-pill.acento { background: var(--va-acento-suave); border-color: var(--va-acento-borde); color: var(--va-acento-2); }
.va-pill.fuerte { background: var(--va-acento); border-color: var(--va-acento); color: #fff; }
.va-riesgo { color: #fff; border: none; font-weight: 700; }
.va-sec { font-weight: 700; color: var(--va-tinta); margin: .9rem 0 .45rem; font-size: 1rem; }
.va-sec span { color: var(--va-gris); font-weight: 500; font-size: .85rem; margin-left: .3rem; }
.va-lista { margin: 0; padding-left: 1.1rem; }
.va-lista li { margin-bottom: .3rem; color: #334155; font-size: .92rem; }

/* inicio */
.va-hero { background: linear-gradient(135deg, var(--va-acento-suave), #fff 70%);
  border: 1px solid var(--va-acento-borde); border-radius: 16px; padding: 1.3rem 1.4rem; }
.va-hero h3 { margin: 0 0 .3rem; color: var(--va-tinta); font-size: 1.3rem; }
.va-hero p { color: #334155; margin: 0 0 .9rem; }
.va-pasos { display: flex; gap: .8rem; flex-wrap: wrap; }
.va-paso { flex: 1 1 180px; background: #fff; border: 1px solid var(--va-borde); border-radius: 12px; padding: .7rem .8rem; font-size: .86rem; color: #334155; }
.va-paso b { display: inline-grid; place-items: center; width: 22px; height: 22px; border-radius: 50%;
  background: var(--va-acento); color: #fff; font-size: .75rem; margin-right: .4rem; }
.va-ficha { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: .7rem; }
.va-dato .k { font-size: .7rem; letter-spacing: .1em; text-transform: uppercase; color: var(--va-gris); font-weight: 700; }
.va-dato .v { font-size: 1.05rem; font-weight: 600; color: var(--va-tinta); }
.va-fila-pills { display: flex; gap: .35rem; flex-wrap: wrap; margin-top: .35rem; }

/* comparacion */
.va-ver .cab { display: flex; justify-content: space-between; align-items: baseline; }
.va-ver .id { font-size: 1.5rem; font-weight: 800; color: var(--va-acento-2); }
.va-ver .nom { font-size: .85rem; color: var(--va-gris); }
.va-kpis { display: grid; grid-template-columns: 1fr 1fr; gap: .5rem; margin: .7rem 0; }
.va-kpi { background: var(--va-fondo); border-radius: 10px; padding: .45rem .6rem; }
.va-kpi .k { font-size: .7rem; color: var(--va-gris); text-transform: uppercase; letter-spacing: .08em; }
.va-kpi .v { font-size: 1.15rem; font-weight: 700; color: var(--va-tinta); }
.va-ver .res { font-size: .86rem; color: #334155; display: -webkit-box; -webkit-line-clamp: 6;
  -webkit-box-orient: vertical; overflow: hidden; }
.va-ver.falla { border-top: 4px solid #b91c1c; }

/* barra de firma fija abajo: Streamlit envuelve el contenedor en un stLayoutWrapper de su
   mismo alto, asi que lo "sticky" tiene que ser el envoltorio */
div[data-testid="stLayoutWrapper"]:has(> .st-key-firma) { position: sticky; bottom: 0; z-index: 60; }
.st-key-firma { background: #fff;
  border: 1px solid var(--va-borde); border-top: 3px solid var(--va-acento); border-radius: 14px 14px 0 0;
  padding: .7rem 1rem .8rem; box-shadow: 0 -8px 24px rgba(15, 23, 42, .08); margin-top: 1rem; }
.st-key-firma .va-rot { margin-bottom: .1rem; }
</style>
"""


# ---------- encabezado ----------

def encabezado(caso: dict, escenario: str, modo: str, aviso: str) -> str:
    lugar = ", ".join(x for x in (caso.get("comuna"), caso.get("region")) if x)
    chips = [("Escenario", escenario), ("Lugar", lugar), ("Fecha", caso.get("fecha_evento")),
             ("Especie", caso.get("especie"))]
    chips_html = "".join(f'<span class="va-chip"><i>{esc(k)}</i>{esc(v)}</span>' for k, v in chips if v)
    return (f'<div class="va-enc"><div class="va-marca"><div class="va-logo">VA</div>'
            f'<div><b>VET·AGENT</b><span>Vigilancia epidemiológica · One Health</span></div></div>'
            f'<div class="va-chips">{chips_html}</div><span class="va-modo">{esc(modo)}</span></div>'
            f'<div class="va-aviso">{esc(aviso)}</div>')


# ---------- diagrama del grafo ----------

def grafo_inicial() -> dict:
    """Estado del diagrama antes de analizar: todos los nodos en espera."""
    return {"nodos": {n: {"estado": "espera", "segundos": None} for n in NODOS},
            "ronda": 0, "debate": False, "rechazo": False, "nota": "Esperando un caso para analizar"}


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
    # triangulos dibujados a mano (los <marker> de SVG no siempre pasan el filtro de st.html)
    p = {"der": f"{x},{y} {x - 9},{y - 5} {x - 9},{y + 5}",
         "arr": f"{x},{y} {x - 5},{y + 9} {x + 5},{y + 9}",
         "aba": f"{x},{y} {x - 5},{y - 9} {x + 5},{y - 9}"}[direccion]
    return f'<polygon class="va-punta{clase}" points="{p}"/>'


def diagrama_grafo(vista: dict) -> str:
    """SVG del grafo: los 5 nodos, la flecha de debate del moderador y la del rechazo."""
    nodos, ancho, alto, y = vista["nodos"], 168, 62, 52
    xs = [8 + i * 204 for i in range(len(NODOS))]
    centro = {n: x + ancho / 2 for n, x in zip(NODOS, xs)}
    partes = []

    # flechas de la cadena
    for i in range(len(NODOS) - 1):
        a, b = NODOS[i], NODOS[i + 1]
        activa = " activa" if nodos[a]["estado"] in ("listo", "omitido") else ""
        x1, x2, ym = xs[i] + ancho, xs[i + 1], y + alto / 2
        partes.append(f'<line class="va-flecha{activa}" x1="{x1}" y1="{ym}" x2="{x2 - 2}" y2="{ym}"/>')
        partes.append(_punta(x2, ym, "der", activa))

    # ronda de debate: moderador -> climatico, por abajo
    act = " activa" if vista.get("debate") else ""
    xm, xc, yb = centro["moderador"], centro["climatico"], y + alto
    partes.append(f'<path class="va-flecha vuelta{act}" d="M{xm} {yb} V{yb + 30} H{xc} V{yb + 4}"/>')
    partes.append(_punta(xc, yb + 1, "arr", act))
    partes.append(f'<text class="va-rotulo{act}" x="{(xm + xc) / 2}" y="{yb + 26}" text-anchor="middle">'
                  f'ronda de debate: el moderador pregunta a un agente</text>')

    # rechazo: revision humana -> moderador, por arriba
    act = " activa" if vista.get("rechazo") else ""
    xh, xm = centro["revision_humana"], centro["moderador"]
    partes.append(f'<path class="va-flecha vuelta{act}" d="M{xh} {y} V{y - 30} H{xm} V{y - 4}"/>')
    partes.append(_punta(xm, y - 1, "aba", act))
    partes.append(f'<text class="va-rotulo{act}" x="{(xh + xm) / 2}" y="{y - 36}" text-anchor="middle">'
                  f'rechazo con comentario</text>')

    for nodo, x in zip(NODOS, xs):
        clase, texto = estado_nodo(nodos[nodo])
        partes.append(
            f'<g class="va-nodo {clase}"><rect x="{x}" y="{y}" width="{ancho}" height="{alto}" rx="12"/>'
            f'<text class="n" x="{x + 14}" y="{y + 26}">{ICONO_NODO[nodo]} {esc(NOMBRE_NODO[nodo])}</text>'
            f'<text class="e" x="{x + 14}" y="{y + 47}">{esc(texto)}</text></g>')

    ronda = vista.get("ronda") or 0
    pie_der = f"Ronda del moderador: <b>{ronda}</b>" if ronda else ""
    return (f'<div class="va-grafo"><svg viewBox="0 0 1000 158" role="img" '
            f'aria-label="Avance de los agentes">{"".join(partes)}</svg>'
            f'<div class="va-grafo-pie"><span>{esc(vista.get("nota", ""))}</span><span>{pie_der}</span></div></div>')


# ---------- medidor de riesgo ----------

def _punto(angulo: float, r: float, cx: float = 110, cy: float = 108) -> tuple[float, float]:
    # angulo en grados medido desde la izquierda (0) hasta la derecha (180), por arriba
    a = math.radians(angulo)
    return round(cx - r * math.cos(a), 2), round(cy - r * math.sin(a), 2)


def medidor(nivel) -> str:
    """Medidor semicircular con los 4 niveles; el del informe en color pleno y la aguja apuntandolo."""
    nivel = str(nivel or "").lower()
    arcos = []
    for i, n in enumerate(NIVELES):
        (x1, y1), (x2, y2) = _punto(i * 45 + 1.5, 82), _punto((i + 1) * 45 - 1.5, 82)
        opacidad = 1 if n == nivel else .18
        arcos.append(f'<path d="M{x1} {y1} A82 82 0 0 1 {x2} {y2}" fill="none" stroke="{COLOR_RIESGO[n]}" '
                     f'stroke-width="20" stroke-opacity="{opacidad}"/>')
    if nivel in NIVELES:
        xa, ya = _punto(NIVELES.index(nivel) * 45 + 22.5, 62)
        aguja = (f'<line x1="110" y1="108" x2="{xa}" y2="{ya}" stroke="#0f172a" stroke-width="4" '
                 f'stroke-linecap="round"/><circle cx="110" cy="108" r="7" fill="#0f172a"/>')
        color, texto, sub = COLOR_RIESGO[nivel], nivel_texto(nivel), "Nivel de riesgo global"
    else:
        aguja, color, texto, sub = '<circle cx="110" cy="108" r="7" fill="#cbd5e1"/>', "#94a3b8", "PENDIENTE", \
            "El informe aún no asigna un nivel"
    marcas = "".join(
        f'<text x="{_punto(i * 45 + 22.5, 104)[0]}" y="{_punto(i * 45 + 22.5, 104)[1] + 4}" text-anchor="middle" '
        f'style="font: 10px system-ui; fill: #94a3b8">{n.replace("_", " ")}</text>' for i, n in enumerate(NIVELES))
    return (f'<div class="va-carta va-medidor"><div class="va-rot">Riesgo</div>'
            f'<svg viewBox="-18 0 256 124" role="img" aria-label="Nivel de riesgo {esc(texto)}">{"".join(arcos)}'
            f'{marcas}{aguja}</svg><div class="nivel" style="color:{color}">{texto}</div>'
            f'<div class="sub">{esc(sub)}</div></div>')


# ---------- notas al pie (citas) ----------

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


# ---------- bloques del informe ----------

def pendiente(texto: str) -> str:
    return f'<div class="va-pend">{esc(texto)}</div>'


def resumen(informe: dict, meta: str, sin_dato: str) -> str:
    cuerpo = ""
    if informe.get("resumen_ejecutivo"):
        cuerpo += f'<p>{esc(informe["resumen_ejecutivo"])}</p>'
    if informe.get("justificacion_riesgo"):
        cuerpo += f'<p class="just"><b>Por qué este nivel:</b> {esc(informe["justificacion_riesgo"])}</p>'
    return (f'<div class="va-carta va-resumen"><div class="va-rot">Resumen ejecutivo</div>'
            f'{cuerpo or pendiente(sin_dato)}<div class="va-meta">{esc(meta)}</div></div>')


def tarjeta_one_health(dimension: str, titulo: str, icono: str, riesgos: list, notas_agentes: list[tuple[str, str]],
                       numeros: dict[str, int]) -> str:
    """Tarjeta de una dimension: cuantos riesgos del informe caen en ella y cuales son.

    Si el informe no trae riesgos de esa dimension, muestra lo que dijeron los agentes de ella.
    """
    propios = [como_dict(r, "riesgo") for r in riesgos]
    propios = [r for r in propios if str(r.get("dimension_one_health", "")).lower() == dimension]
    if propios:
        cuerpo = "<ul>" + "".join(
            f'<li>{esc(r.get("riesgo", ""))}{sup(r.get("evidencia"), numeros)}'
            + (f' <span class="va-pill">confianza {esc(r["confianza"])}</span>' if r.get("confianza") else "")
            + "</li>" for r in propios[:3]) + "</ul>"
        if len(propios) > 3:
            cuerpo += f'<div class="nota-ag">y {len(propios) - 3} más en la pestaña Informe.</div>'
    elif notas_agentes:
        cuerpo = "".join(f'<div class="nota-ag"><b>{esc(a)}:</b> {esc(t)}</div>' for a, t in notas_agentes)
    else:
        cuerpo = '<div class="nota-ag">Sin riesgos identificados en esta dimensión.</div>'
    plural = "riesgo" if len(propios) == 1 else "riesgos"
    return (f'<div class="va-carta va-oh"><div class="cab"><span class="dim"><span class="ico">{icono}</span>'
            f'{esc(titulo)}</span><span class="num">{len(propios)}<small>{plural}</small></span></div>{cuerpo}</div>')


ORDEN_PRIORIDAD = {"inmediata": 0, "corto_plazo": 1, "preventiva": 2}


def lista_riesgos(riesgos: list, numeros: dict[str, int]) -> str:
    items = []
    for r in riesgos:
        r = como_dict(r, "riesgo")
        pills = "".join(f'<span class="va-pill{c}">{esc(t)}</span>' for t, c in (
            (r.get("dimension_one_health"), " acento"), (f'confianza {r["confianza"]}' if r.get("confianza") else "", ""))
            if t)
        items.append(f'<div class="va-item"><b>{esc(r.get("riesgo", ""))}</b>{sup(r.get("evidencia"), numeros)}'
                     f'<div class="pie">{pills}</div></div>')
    return "".join(items)


def lista_recomendaciones(recomendaciones: list, numeros: dict[str, int]) -> str:
    recs = sorted((como_dict(r, "accion") for r in recomendaciones),
                  key=lambda r: ORDEN_PRIORIDAD.get(str(r.get("prioridad", "")), 9))
    items = []
    for r in recs:
        prioridad = str(r.get("prioridad", "")).replace("_", " ")
        pills = []
        if prioridad:
            pills.append(f'<span class="va-pill{" fuerte" if r.get("prioridad") == "inmediata" else " acento"}">'
                         f'{esc(prioridad)}</span>')
        if r.get("responsable_sugerido"):
            pills.append(f'<span class="va-pill">responsable: {esc(r["responsable_sugerido"])}</span>')
        if r.get("requiere_autoridad"):
            pills.append('<span class="va-pill">requiere autoridad sanitaria</span>')
        items.append(f'<div class="va-item"><b>{esc(r.get("accion", ""))}</b>{sup(r.get("fuentes"), numeros)}'
                     f'<div class="pie">{"".join(pills)}</div></div>')
    return "".join(items)


def lista_simple(items: list) -> str:
    return '<ul class="va-lista">' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"


def seccion(titulo: str, contenido_html: str, cantidad: int | None, sin_dato: str) -> str:
    extra = f"<span>{cantidad}</span>" if cantidad else ""
    return f'<div class="va-sec">{esc(titulo)}{extra}</div>{contenido_html or pendiente(sin_dato)}'


def pill_riesgo(nivel) -> str:
    nivel = str(nivel or "").lower()
    color = COLOR_RIESGO.get(nivel, "#94a3b8")
    return f'<span class="va-pill va-riesgo" style="background:{color}">{nivel_texto(nivel) or "SIN NIVEL"}</span>'


def resumen_agente(agente: str, salida: dict) -> str:
    """Una linea con lo principal que concluyo cada especialista."""
    campos = {"climatico": [("riesgo ambiental", "nivel_riesgo_ambiental"), ("ventana", "ventana_temporal")],
              "diagnostico": [("presencia", "probabilidad_presencia"), ("confirmar", "requiere_confirmacion")],
              "bioseguridad": [("notificación", "notificacion_obligatoria"), ("autoridad", "autoridad_competente")]}
    pills = []
    for nombre, clave in campos.get(agente, []) + [("confianza", "confianza")]:
        valor = salida.get(clave)
        if valor in (None, "", []):
            continue
        if isinstance(valor, bool):
            valor = "sí" if valor else "no"
        elif isinstance(valor, list):
            valor = ", ".join(map(str, valor))
        pills.append(f'<span class="va-pill acento">{esc(nombre)}: {esc(str(valor).replace("_", " "))}</span>')
    return f'<div class="va-fila-pills">{"".join(pills)}</div>'


# ---------- pantalla de inicio ----------

HERO = """<div class="va-hero"><h3>Listo para analizar</h3>
<p>Cuatro agentes especializados revisan el caso con evidencia documental y un especialista humano firma el informe.</p>
<div class="va-pasos">
<div class="va-paso"><b>1</b>Elige un escenario o edita el caso en el panel izquierdo.</div>
<div class="va-paso"><b>2</b>Presiona <i>Analizar</i> y mira trabajar a los agentes en el diagrama.</div>
<div class="va-paso"><b>3</b>Revisa el informe, apruébalo o recházalo con un comentario y descárgalo.</div>
</div></div>"""


def ficha_caso(caso: dict) -> str:
    def num(v, sufijo=""):
        if v in (None, ""):
            return "—"
        try:
            n = float(v)
            # 40000 -> "40 000"; los decimales se dejan como vienen (12.0 %)
            v = f"{int(n):,}".replace(",", " ") if n.is_integer() and abs(n) >= 1000 else v
        except (TypeError, ValueError):
            pass
        return f"{v}{sufijo}"

    datos = [("Animales", num(caso.get("n_animales"))), ("Mortalidad", num(caso.get("mortalidad_pct"), " %")),
             ("Morbilidad", num(caso.get("morbilidad_pct"), " %"))]
    clima = caso.get("clima") or {}
    datos += [("Temperatura", num(clima.get("temperatura_media_c"), " °C")),
              ("Humedad", num(clima.get("humedad_relativa_pct"), " %")),
              ("Precipitación", num(clima.get("precipitacion_mm"), " mm"))]
    grid = "".join(f'<div class="va-dato"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
                   for k, v in datos)
    signos = "".join(f'<span class="va-pill">{esc(s)}</span>' for s in caso.get("signos_clinicos") or [])
    lab = "".join(
        f'<span class="va-pill acento">{esc(r.get("prueba", ""))}: {esc(r.get("resultado", ""))}</span>'
        for r in (caso.get("resultados_laboratorio") or []) if isinstance(r, dict))
    extra = ""
    if signos:
        extra += f'<div class="va-rot" style="margin-top:.9rem">Signos clínicos</div><div class="va-fila-pills">{signos}</div>'
    if lab:
        extra += f'<div class="va-rot" style="margin-top:.9rem">Laboratorio</div><div class="va-fila-pills">{lab}</div>'
    sistema = caso.get("sistema_productivo")
    titulo = esc(caso.get("especie") or "Caso") + (f' <span style="color:#64748b;font-weight:400">· {esc(sistema)}</span>'
                                                    if sistema else "")
    return (f'<div class="va-carta"><div class="va-rot">Ficha del caso</div>'
            f'<div style="font-weight:600;margin-bottom:.8rem">{titulo}</div>'
            f'<div class="va-ficha">{grid}</div>{extra}</div>')


# ---------- comparacion B1 / B2 / B3 ----------

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
    cab = (f'<div class="cab"><span class="id">{esc(version)}</span>'
           f'<span class="nom">{esc(nombre)}</span></div>')
    if fila is None:
        return f'<div class="va-carta va-ver">{cab}{pendiente("Sin datos de esta versión en la corrida.")}</div>'
    if fila["error"]:
        return (f'<div class="va-carta va-ver falla">{cab}<div class="va-sec" style="color:#b91c1c">✕ Falló a los '
                f'{esc(duracion(fila["segundos"]))}</div><div class="res">{esc(fila["error"][:220])}</div></div>')
    inf = fila["informe"]
    kpis = [("Tiempo", duracion(fila["segundos"])), ("Fuentes citadas", fila["fuentes"]),
            ("Riesgos", len(inf.get("riesgos_identificados") or [])),
            ("Recomendaciones", len(inf.get("recomendaciones") or []))]
    kpis_html = "".join(f'<div class="va-kpi"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div></div>'
                        for k, v in kpis)
    return (f'<div class="va-carta va-ver">{cab}<div style="margin-top:.5rem">{pill_riesgo(inf.get("nivel_riesgo_global"))}'
            f'</div><div class="va-kpis">{kpis_html}</div>'
            f'<div class="res">{esc(inf.get("resumen_ejecutivo", ""))}</div></div>')
