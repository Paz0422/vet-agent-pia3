"""Borra archivos que ya no se usan.

Uso (raiz del repo):
    python -m vet_agent.rag.limpiar --prueba    # solo lista lo que se borraria (no borra nada)
    python -m vet_agent.rag.limpiar             # borra de verdad

Borra:
- corpus/md/_tmp_pdf/       PDF temporales que quedaron de una descarga cortada
- los .md descartados en la validacion (Pagina equivocada, Duplicado, Excluido)
- los .md sueltos en corpus/md/ (sin carpeta de coleccion): los dejaba el antiguo
  descargar.py; los vigentes estan en corpus/md/<coleccion>/
- las carpetas __pycache__ del repo (fuera de .venv)
- los logs de corridas anteriores (en corpus/md/_logs/ se deja el mas nuevo)
- lo que quedaba del pipeline .txt: corpus/txt/, corpus/escaneados_*.txt y la
  coleccion de Chroma "diagnostico" (todo el corpus ya esta en Markdown)

NUNCA borra (si algo de esto aparece en la lista, se detiene sin borrar nada):
los .md "OK", corpus/md/_cache/, prefiltro.xlsx, reporte_validacion.xlsx,
para_investigacion.xlsx, las colecciones *_md de Chroma (incluida
diagnostico_md_v1) ni corpus/D/ (PDF entregados a mano). Los .md que estan en
git (los 18 del eje D que venian de PDF) son "OK", asi que tambien quedan protegidos.
"""
import argparse
import json
import pathlib
import shutil

import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MD = RAIZ / "corpus" / "md"
REPORTE = MD / "reporte_validacion.xlsx"
SE_BORRAN = {"Página equivocada", "Duplicado", "Excluido"}
COLECCIONES_TXT = ["diagnostico"]     # colecciones de Chroma del pipeline .txt
PROTEGIDOS = [MD / "_cache", MD / "prefiltro.xlsx", REPORTE, MD / "para_investigacion.xlsx",
              RAIZ / "chroma_db", RAIZ / "corpus" / "D", RAIZ / ".venv", RAIZ / ".git"]


def _dentro(ruta: pathlib.Path, base: pathlib.Path) -> bool:
    return ruta.resolve() == base.resolve() or base.resolve() in ruta.resolve().parents


def _peso(ruta: pathlib.Path) -> int:
    if ruta.is_file():
        return ruta.stat().st_size
    return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file())


def _cliente():
    import chromadb  # solo si hay que tocar Chroma
    return chromadb.PersistentClient(path=str(RAIZ / "chroma_db"))


def a_borrar() -> tuple[list[tuple[str, pathlib.Path]], list[tuple[str, str, int]]]:
    """(archivos, colecciones): lo que se borraria, con su motivo."""
    lista = []
    if (MD / "_tmp_pdf").exists():
        lista.append(("PDF temporales", MD / "_tmp_pdf"))
    if REPORTE.exists():
        r = pd.read_excel(REPORTE, sheet_name="Archivos", dtype=str).fillna("")
        for f in r[r.estado.isin(SE_BORRAN)].itertuples():
            ruta = RAIZ / f.archivo
            if ruta.exists():
                lista.append((f".md descartado ({f.estado})", ruta))
    lista += [(".md suelto (descargar.py antiguo)", p) for p in sorted(MD.glob("*.md"))]
    for p in RAIZ.rglob("__pycache__"):
        if not _dentro(p, RAIZ / ".venv"):
            lista.append(("__pycache__", p))
    logs = sorted((MD / "_logs").glob("*.txt")) if (MD / "_logs").exists() else []
    lista += [("log de una corrida anterior", p) for p in logs[:-1]]
    lista += [("log de una corrida anterior", p) for p in RAIZ.rglob("*.log")
              if not _dentro(p, RAIZ / ".venv") and not _dentro(p, RAIZ / ".git")]
    if (RAIZ / "corpus" / "txt").exists():
        lista.append(("pipeline .txt", RAIZ / "corpus" / "txt"))
    lista += [("pipeline .txt", p) for p in (RAIZ / "corpus").glob("escaneados_*.txt")]

    colecciones = []
    if (RAIZ / "chroma_db").exists():
        existentes = {c.name for c in _cliente().list_collections()}
        for nombre in COLECCIONES_TXT:
            if nombre in existentes:
                colecciones.append(("colección Chroma del pipeline .txt", nombre, _cliente().get_collection(nombre).count()))
    return lista, colecciones


def recordar_descartados(rutas: list[pathlib.Path]) -> None:
    """Anota en _cache/descartados.json la URL de cada .md descartado, para que la
    descarga no lo vuelva a bajar (solo lo reintenta si su URL cambia en el prefiltro)."""
    from vet_agent.rag import cabecera
    archivo = MD / "_cache" / "descartados.json"
    datos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}
    for ruta in rutas:
        meta, _ = cabecera.leer(ruta)
        datos[f"{ruta.parent.name}/{ruta.stem}"] = meta.get("url_plan", "")
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def ok_protegidos() -> set[pathlib.Path]:
    """Los .md "OK" de la validacion (tambien protegidos)."""
    if not REPORTE.exists():
        return set()
    r = pd.read_excel(REPORTE, sheet_name="Archivos", dtype=str).fillna("")
    return {(RAIZ / a).resolve() for a in r[r.estado == "OK"].archivo}


def main() -> None:
    parser = argparse.ArgumentParser(description="Borra archivos que ya no se usan")
    parser.add_argument("--prueba", action="store_true", help="solo listar, sin borrar nada")
    args = parser.parse_args()

    lista, colecciones = a_borrar()
    oks = ok_protegidos()
    peligro = [p for _, p in lista if any(_dentro(p, b) for b in PROTEGIDOS) or p.resolve() in oks]
    peligro += [n for _, n, _ in colecciones if n.endswith("_md") or "_md_" in n]
    if peligro:
        raise SystemExit(f"ALTO: la lista incluye cosas protegidas, no se borra nada: {peligro}")

    marca = "[prueba] " if args.prueba else ""
    total = 0
    por_motivo: dict[str, list[int]] = {}
    for motivo, ruta in lista:
        peso = _peso(ruta)
        total += peso
        por_motivo.setdefault(motivo, [0, 0])
        por_motivo[motivo][0] += 1
        por_motivo[motivo][1] += peso
        print(f"{marca}{motivo:32} {peso / 1024:9.1f} KB  {ruta.relative_to(RAIZ)}")
    for motivo, nombre, pedazos in colecciones:
        print(f"{marca}{motivo:32} {pedazos:6} pedazos  chroma_db: {nombre}")
    print("\nResumen:")
    for motivo, (n, peso) in por_motivo.items():
        print(f"   {motivo:32} {n:4} elementos  {peso / 2**20:7.2f} MB")
    if colecciones:
        print(f"   {'colecciones de Chroma':32} {len(colecciones):4} elementos  ({', '.join(n for _, n, _ in colecciones)})")
    print(f"   {'TOTAL':32} {len(lista) + len(colecciones):4} elementos  {total / 2**20:7.2f} MB en disco")

    if args.prueba:
        print("\nModo de prueba: no se borró nada.")
        return
    recordar_descartados([ruta for motivo, ruta in lista if motivo.startswith(".md descartado")])
    for _, ruta in lista:
        shutil.rmtree(ruta) if ruta.is_dir() else ruta.unlink(missing_ok=True)
    for _, nombre, _ in colecciones:
        _cliente().delete_collection(nombre)
    print(f"\nBorrados {len(lista)} archivos/carpetas ({total / 2**20:.2f} MB) y {len(colecciones)} colecciones de Chroma.")


if __name__ == "__main__":
    main()
