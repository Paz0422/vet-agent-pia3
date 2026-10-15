"""Borra archivos que ya no se usan.

Uso (raiz del repo):
    python -m vet_agent.rag.limpiar --prueba    # solo lista lo que se borraria (no borra nada)
    python -m vet_agent.rag.limpiar             # borra de verdad

Borra:
- corpus/md/_tmp_pdf/       PDF temporales que quedaron de una descarga cortada
- los .md descartados en la validacion (Pagina equivocada, Duplicado, Excluido)
- las carpetas __pycache__ del repo (fuera de .venv)
- los logs de corridas anteriores (en corpus/md/_logs/ se deja el mas nuevo)

NUNCA borra (si algo de esto aparece en la lista, se detiene sin borrar nada):
los .md "OK", corpus/md/_cache/, prefiltro.xlsx, reporte_validacion.xlsx,
para_investigacion.xlsx, chroma_db/ (todas las colecciones, incluida
diagnostico_md_v1), corpus/D/ ni corpus/txt/ (los usa la coleccion "diagnostico").
"""
import argparse
import pathlib
import shutil

import pandas as pd

RAIZ = pathlib.Path(__file__).resolve().parents[2]
MD = RAIZ / "corpus" / "md"
REPORTE = MD / "reporte_validacion.xlsx"
SE_BORRAN = {"Página equivocada", "Duplicado", "Excluido"}
PROTEGIDOS = [MD / "_cache", MD / "prefiltro.xlsx", REPORTE, MD / "para_investigacion.xlsx",
              RAIZ / "chroma_db", RAIZ / "corpus" / "D", RAIZ / "corpus" / "txt", RAIZ / ".venv", RAIZ / ".git"]


def _dentro(ruta: pathlib.Path, base: pathlib.Path) -> bool:
    return ruta.resolve() == base.resolve() or base.resolve() in ruta.resolve().parents


def _peso(ruta: pathlib.Path) -> int:
    if ruta.is_file():
        return ruta.stat().st_size
    return sum(p.stat().st_size for p in ruta.rglob("*") if p.is_file())


def a_borrar() -> list[tuple[str, pathlib.Path]]:
    """(motivo, ruta) de todo lo que se borraria."""
    lista = []
    if (MD / "_tmp_pdf").exists():
        lista.append(("PDF temporales", MD / "_tmp_pdf"))
    if REPORTE.exists():
        r = pd.read_excel(REPORTE, sheet_name="Archivos", dtype=str).fillna("")
        for f in r[r.estado.isin(SE_BORRAN)].itertuples():
            ruta = RAIZ / f.archivo
            if ruta.exists():
                lista.append((f".md descartado ({f.estado})", ruta))
    for p in RAIZ.rglob("__pycache__"):
        if not _dentro(p, RAIZ / ".venv"):
            lista.append(("__pycache__", p))
    logs = sorted((MD / "_logs").glob("*.txt")) if (MD / "_logs").exists() else []
    lista += [("log de una corrida anterior", p) for p in logs[:-1]]
    lista += [("log de una corrida anterior", p) for p in RAIZ.rglob("*.log")
              if not _dentro(p, RAIZ / ".venv") and not _dentro(p, RAIZ / ".git")]
    return lista


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

    lista = a_borrar()
    oks = ok_protegidos()
    peligro = [p for _, p in lista if any(_dentro(p, b) for b in PROTEGIDOS) or p.resolve() in oks]
    if peligro:
        raise SystemExit(f"ALTO: la lista incluye archivos protegidos, no se borra nada: {peligro}")

    total = 0
    por_motivo: dict[str, list[int]] = {}
    for motivo, ruta in lista:
        peso = _peso(ruta)
        total += peso
        por_motivo.setdefault(motivo, [0, 0])
        por_motivo[motivo][0] += 1
        por_motivo[motivo][1] += peso
        print(f"{'[prueba] ' if args.prueba else ''}{motivo:32} {peso / 1024:9.1f} KB  {ruta.relative_to(RAIZ)}")
    print("\nResumen:")
    for motivo, (n, peso) in por_motivo.items():
        print(f"   {motivo:32} {n:4} elementos  {peso / 2**20:7.2f} MB")
    print(f"   {'TOTAL':32} {len(lista):4} elementos  {total / 2**20:7.2f} MB")

    if args.prueba:
        print("\nModo de prueba: no se borró nada.")
        return
    for _, ruta in lista:
        shutil.rmtree(ruta) if ruta.is_dir() else ruta.unlink(missing_ok=True)
    print(f"\nBorrados {len(lista)} elementos ({total / 2**20:.2f} MB).")


if __name__ == "__main__":
    main()
