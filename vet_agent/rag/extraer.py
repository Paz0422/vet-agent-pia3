"""Tarea 2 - Pasar los PDF de corpus/D/ a texto limpio en corpus/txt/.

Uso (desde la raiz del repo):
    python -m vet_agent.rag.extraer

D8.pdf -> corpus/txt/D8.txt
Los PDF escaneados (casi sin texto) se omiten y se listan al final
(tambien quedan en corpus/escaneados.txt).
"""
import pathlib
import re
from collections import Counter

try:
    import pymupdf as fitz  # nombre nuevo de pymupdf
except ImportError:  # versiones antiguas
    import fitz

RAIZ = pathlib.Path(__file__).resolve().parents[2]  # raiz del repo
ENTRADA = RAIZ / "corpus" / "D"
SALIDA = RAIZ / "corpus" / "txt"
SALIDA.mkdir(parents=True, exist_ok=True)

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


def main() -> None:
    pdfs = sorted(ENTRADA.glob("*.pdf"))
    if not pdfs:
        print(f"No hay PDF en {ENTRADA}")
        return

    escaneados, fallidos = [], []
    for pdf in pdfs:
        try:
            crudo = extraer_texto(pdf)
        except Exception as e:  # PDF danado o con clave
            fallidos.append(pdf.name)
            print("ERROR", pdf.name, e)
            continue
        if len(crudo.strip()) < 200:  # casi sin texto = probablemente escaneado
            escaneados.append(pdf.name)
            continue
        limpio = limpiar(crudo)
        (SALIDA / f"{pdf.stem}.txt").write_text(limpio, encoding="utf-8")
        print("OK", pdf.name, len(crudo), "->", len(limpio), "caracteres")

    print("Escaneados (necesitan OCR):", escaneados)
    if fallidos:
        print("Fallidos (no se pudieron abrir):", fallidos)
    (RAIZ / "corpus" / "escaneados.txt").write_text("\n".join(escaneados), encoding="utf-8")


if __name__ == "__main__":
    main()
