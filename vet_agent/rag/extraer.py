"""Tarea 2 - Extraer texto limpio de PDF. Modulo GLOBAL para todos los agentes.

Uso desde la terminal (raiz del repo):
    python -m vet_agent.rag.extraer            # procesa corpus/D/
    python -m vet_agent.rag.extraer --eje B    # procesa corpus/B/

Uso desde otro archivo de Python (cualquier agente):
    from vet_agent.rag.extraer import extraer_pdf
    texto = extraer_pdf("corpus/B/B4.pdf")

Los arreglos (palabras rotas, menus, cookies...) NO estan escritos aca: estan
en corpus/arreglos.json. La clave "*" se aplica a TODOS los PDF; las claves
"D10", "D13"... solo a ese documento. Para arreglar un documento nuevo se
edita ese archivo, no este codigo.
"""
import argparse
import json
import pathlib
import re
from collections import Counter

try:
    import pymupdf as fitz  # nombre nuevo de pymupdf
except ImportError:  # versiones antiguas
    import fitz

RAIZ = pathlib.Path(__file__).resolve().parents[2]  # raiz del repo
CORPUS = RAIZ / "corpus"
SALIDA = CORPUS / "txt"
ARCHIVO_ARREGLOS = CORPUS / "arreglos.json"
MINIMO_CARACTERES = 200  # menos que esto = probablemente escaneado

# Frases que se repiten en cada hoja y no aportan. Se borran en todos los PDF.
# (Los encabezados/pies repetidos ya se detectan solos; esto es para casos raros.)
BASURA = []

# Linea que es un numero de pagina CON texto: "Pág. 3 de 20", "Page 3 of 14", "- 12 -"
RE_PAG_CON_TEXTO = re.compile(
    r"^\s*[-–—]?\s*(?:p[áa]g(?:ina)?\.?|page)\s*\d{1,4}"
    r"(?:\s*(?:de|of|/)\s*\d{1,4})?\s*[-–—]?\s*$",
    re.IGNORECASE,
)
# Linea que es SOLO un numero: "12", "- 12 -"
RE_SOLO_NUM = re.compile(r"^\s*[-–—]?\s*(\d{1,4})\s*[-–—]?\s*$")
# Linea de puntos que se usa como separador de hoja
RE_PUNTOS = re.compile(r"^\s*\.{6,}\s*$")

BORDE_ARRIBA = 8   # cuantas lineas del inicio de la hoja se miran
BORDE_ABAJO = 4    # cuantas lineas del final de la hoja se miran


def _normalizar(linea: str) -> str:
    """Clave para comparar lineas entre paginas (los digitos varian: 'Pág. 3')."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", linea.strip().lower()))


def quitar_encabezados_y_numeros(paginas: list[str]) -> list[str]:
    """Borra encabezados/pies repetidos y numeros de pagina.

    Solo se toca el BORDE de cada hoja (primeras y ultimas lineas), nunca el
    medio. Asi no se borran numeros que son datos (tablas: "hasta 11 torulas").
    """
    lineas_por_pag = [p.splitlines() for p in paginas]

    def indices_borde(lineas):
        """Posiciones (en `lineas`) de las lineas no vacias del borde."""
        pos = [i for i, l in enumerate(lineas) if l.strip()]
        return set(pos[:BORDE_ARRIBA] + pos[-BORDE_ABAJO:])

    # En cuantas hojas aparece cada linea (solo mirando los bordes)
    conteo = Counter()
    for lineas in lineas_por_pag:
        if sum(1 for l in lineas if l.strip()) < 8:  # hoja muy corta: no arriesgar
            continue
        claves = {_normalizar(lineas[i]) for i in indices_borde(lineas)}
        conteo.update(c for c in claves if 0 < len(c) <= 150 and c.strip("#. ") != "")

    # Solo con 3+ hojas; "repetida" = aparece en >=40% de ellas
    umbral = max(3, int(len(paginas) * 0.4))
    repetidas = {c for c, n in conteo.items() if n >= umbral} if len(paginas) >= 3 else set()

    limpias = []
    for num, lineas in enumerate(lineas_por_pag, start=1):
        borde = indices_borde(lineas)
        nuevas = []
        for i, l in enumerate(lineas):
            if i in borde:
                m = RE_SOLO_NUM.match(l)
                # numero suelto: solo si esta en el borde Y coincide con el nº de hoja
                if m and int(m.group(1)) in (num, num - 1, num + 1):
                    continue
                if RE_PAG_CON_TEXTO.match(l) or RE_PUNTOS.match(l):
                    continue
                if _normalizar(l) in repetidas:
                    continue
            nuevas.append(l)
        limpias.append("\n".join(nuevas))
    return limpias


def limpiar(texto: str) -> str:
    for frase in BASURA:
        texto = texto.replace(frase, "")
    texto = texto.replace("\r\n", "\n")
    texto = texto.replace("\u00ad", "")                          # guion blando invisible
    texto = texto.replace("\xa0", " ")                           # espacio duro
    texto = texto.replace("\ufb01", "fi").replace("\ufb02", "fl")  # ligaduras fi y fl bien codificadas
    texto = texto.replace("\uf0b7", "•").replace("\uf02d", "-")  # vinetas de Word (PDF del SAG)
    texto = re.sub(r"[\uf000-\uf8ff]", "", texto)                # otros simbolos privados
    texto = re.sub(r"\.{4,}", " ", texto)                        # puntos de relleno (indice, separadores)
    texto = re.sub(r"(\w)-\n(?=[a-záéíóúüñ])", r"\1", texto)     # une palabras cortadas: "vigi-\nlancia"
    texto = re.sub(r"[ \t]+", " ", texto)                        # espacios repetidos
    texto = re.sub(r" ?\n ?", "\n", texto)                       # espacios pegados a saltos de linea
    texto = re.sub(r"\n{3,}", "\n\n", texto)                     # maximo una linea en blanco
    return texto.strip()


def extraer_texto(pdf: pathlib.Path) -> str:
    with fitz.open(pdf) as doc:
        paginas = [pag.get_text() for pag in doc]
    paginas = quitar_encabezados_y_numeros(paginas)
    return "\n\n".join(paginas)


# ---------------------------------------------------------------------------
# Arreglos por documento (las reglas estan en corpus/arreglos.json)
# ---------------------------------------------------------------------------

def cargar_arreglos(ruta: pathlib.Path = ARCHIVO_ARREGLOS) -> dict:
    """Lee corpus/arreglos.json. Si no existe, no hay arreglos."""
    if not ruta.exists():
        return {}
    return json.loads(ruta.read_text(encoding="utf-8"))


def _posicion(lineas: list[str], frase: str, desde: int = 0) -> int:
    """Numero de la primera linea (desde `desde`) que contiene `frase`. -1 si no esta."""
    for i in range(desde, len(lineas)):
        if frase in lineas[i]:
            return i
    return -1


def aplicar_arreglos(texto: str, reglas: dict) -> str:
    """Aplica las reglas de UN documento. Cada regla es opcional."""
    # 1. Palabras rotas: "inVuenza" -> "influenza"
    # (las mas largas primero: "Vuids" antes que "Vuid", sin depender del orden del JSON)
    for roto, bien in sorted(reglas.get("reemplazar", {}).items(), key=lambda par: -len(par[0])):
        texto = texto.replace(roto, bien)

    lineas = texto.split("\n")

    # 2. Lineas identicas seguidas: se deja solo una
    if reglas.get("quitar_repetidas"):
        sin_repetir = []
        for linea in lineas:
            if sin_repetir and linea.strip() and linea == sin_repetir[-1]:
                continue
            sin_repetir.append(linea)
        lineas = sin_repetir

    # 3. Bloques de basura en medio del texto: desde la linea A hasta la B (incluidas)
    for desde, hasta in reglas.get("borrar_entre", []):
        i = _posicion(lineas, desde)
        j = _posicion(lineas, hasta, i + 1) if i >= 0 else -1
        if i >= 0 and j >= 0:
            lineas = lineas[:i] + lineas[j + 1:]
        else:
            print(f"   AVISO: no encontre el bloque '{desde}' ... '{hasta}'")

    # 4. Lineas sueltas de basura (la linea completa tiene que ser igual)
    basura = set(reglas.get("borrar_lineas", []))
    lineas = [l for l in lineas if l.strip() not in basura]

    # 5. Donde empieza el contenido real: se borra todo lo de antes
    if "empieza_en" in reglas:
        i = _posicion(lineas, reglas["empieza_en"])
        if i >= 0:
            lineas = lineas[i:]
        else:
            print(f"   AVISO: no encontre el inicio '{reglas['empieza_en']}'")

    # 6. Donde termina el contenido real: se borra todo lo de despues
    if "termina_en" in reglas:
        i = _posicion(lineas, reglas["termina_en"])
        if i >= 0:
            lineas = lineas[:i + 1]
        else:
            print(f"   AVISO: no encontre el final '{reglas['termina_en']}'")

    return "\n".join(lineas).strip()


# ---------------------------------------------------------------------------
# Funciones que usan los demas (agentes, indexar.py, etc.)
# ---------------------------------------------------------------------------

def extraer_pdf(ruta_pdf, arreglos=None) -> str:
    """PDF -> texto limpio. La funcion principal del modulo.

    Sirve para cualquier PDF de cualquier eje. Si el documento tiene reglas
    en corpus/arreglos.json (se buscan por nombre de archivo: D15.pdf -> "D15"),
    tambien se aplican.
    """
    ruta_pdf = pathlib.Path(ruta_pdf)
    if arreglos is None:
        arreglos = cargar_arreglos()
    texto = limpiar(extraer_texto(ruta_pdf))
    # Primero las reglas "*" (valen para TODOS los PDF) y luego las del documento
    for clave in ("*", ruta_pdf.stem):
        reglas = arreglos.get(clave)
        if reglas:
            texto = aplicar_arreglos(texto, reglas)
    return texto


def procesar_carpeta(carpeta: pathlib.Path, salida: pathlib.Path = SALIDA) -> list[str]:
    """Pasa todos los PDF de `carpeta` a .txt en `salida`. Devuelve los escaneados."""
    salida.mkdir(parents=True, exist_ok=True)
    arreglos = cargar_arreglos()
    escaneados, fallidos = [], []
    for pdf in sorted(carpeta.glob("*.pdf")):
        try:
            texto = extraer_pdf(pdf, arreglos)
        except Exception as e:  # PDF danado o con clave
            fallidos.append(pdf.name)
            print("ERROR", pdf.name, e)
            continue
        if len(texto) < MINIMO_CARACTERES:
            escaneados.append(pdf.name)
            continue
        (salida / f"{pdf.stem}.txt").write_text(texto, encoding="utf-8")
        marca = " (con arreglos)" if pdf.stem in arreglos else ""
        print("OK", pdf.name, len(texto), "caracteres" + marca)
    if fallidos:
        print("Fallidos (no se pudieron abrir):", fallidos)
    return escaneados


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF -> texto limpio")
    parser.add_argument("--eje", default="D", help="carpeta dentro de corpus/ (D, B, E...)")
    args = parser.parse_args()

    carpeta = CORPUS / args.eje
    if not any(carpeta.glob("*.pdf")):
        print(f"No hay PDF en {carpeta}")
        return
    escaneados = procesar_carpeta(carpeta)
    print("Escaneados (necesitan OCR):", escaneados)
    (CORPUS / f"escaneados_{args.eje}.txt").write_text("\n".join(escaneados), encoding="utf-8")


if __name__ == "__main__":
    main()