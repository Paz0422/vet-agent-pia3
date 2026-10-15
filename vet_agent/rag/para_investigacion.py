"""Lista de lo que Investigacion debe resolver a mano -> corpus/md/para_investigacion.xlsx

Uso (raiz del repo, despues de prefiltro, descargar_md y validar):
    python -m vet_agent.rag.para_investigacion

Junta en un solo Excel, con id, coleccion, titulo, url y motivo:
- del prefiltro: con pago, rechazados por la editorial, caidos, bloqueados por
  robots.txt, sin ruta, portadas o login, y los temas que no parecen base
  veterinaria (LLM, RAG, chatbots, salud mental) mas los excluidos a mano;
- de la descarga (errores.csv): rechazados al bajar, muy grandes, sin texto;
- de la validacion: pagina equivocada, escaneados, solo resumen, con pago y
  "Revisar titulo" (los cortos, que hay que mirar a mano).
"""
import pathlib
import time

import pandas as pd

from vet_agent.rag.prefiltro import sin_formulas

RAIZ = pathlib.Path(__file__).resolve().parents[2]
CARPETA = RAIZ / "corpus" / "md"
SALIDA = CARPETA / "para_investigacion.xlsx"

ESTADOS_PREFILTRO = ["Con pago", "Rechazado por la editorial", "Caído", "Bloqueado por robots.txt",
                     "Sin ruta", "Portada o login", "No encontrado en OpenAlex"]
ESTADOS_VALIDACION = ["Página equivocada", "Revisar título", "Escaneado", "Solo resumen", "Con pago"]
QUE_HACER = {
    "Con pago": "Buscar versión libre (repositorio del autor) o conseguir el PDF.",
    "Rechazado por la editorial": "El sitio bloquea bots: bajar el PDF a mano.",
    "Caído": "Revisar si la URL sigue viva o buscar otra.",
    "Bloqueado por robots.txt": "El sitio no permite bots: bajar el PDF a mano.",
    "Sin ruta": "Buscar el link directo al documento o el PDF.",
    "Portada o login": "La URL no lleva al documento: poner el link directo.",
    "No encontrado en OpenAlex": "Revisar el DOI o poner el link directo.",
    "Muy grande": "Pesa más de 50 MB: subir a mano solo los capítulos que sirven.",
    "Sin texto": "No se pudo sacar texto: revisar el archivo.",
    "Página equivocada": "La URL no lleva al documento: poner el link directo o el PDF.",
    "Revisar título": "Abrir el .md y confirmar que es el documento correcto.",
    "Escaneado": "PDF sin texto: necesita OCR o una versión digital.",
    "Solo resumen": "Solo llegó el resumen: conseguir el texto completo.",
    "Tema a revisar": "¿Es base veterinaria? Si no, sacarlo del Excel.",
}
LEYCHILE = "LeyChile necesita JavaScript y reCAPTCHA: bajar a mano el PDF de la norma."


def _fila(id_, coleccion, titulo, url, categoria, motivo, origen) -> dict:
    hacer = LEYCHILE if "bcn.cl" in url else QUE_HACER.get(categoria, "")
    return {"id": id_, "coleccion": coleccion, "categoria": categoria, "titulo": titulo, "url": url,
            "motivo": motivo, "que_hacer": hacer, "origen": origen}


def main() -> None:
    filas = []
    pre = pd.read_excel(CARPETA / "prefiltro.xlsx", sheet_name="Fuentes", dtype=str).fillna("")
    for f in pre[pre.estado.isin(ESTADOS_PREFILTRO)].itertuples():
        filas.append(_fila(f.id, f.coleccion, f.titulo_excel, f.url, f.estado, f.motivo, "prefiltro"))
    for f in pre[pre.revisar_investigacion != ""].itertuples():
        filas.append(_fila(f.id, f.coleccion, f.titulo_excel, f.url, "Tema a revisar", f.revisar_investigacion, "prefiltro"))
    excl = pd.read_excel(CARPETA / "prefiltro.xlsx", sheet_name="Excluidas", dtype=str).fillna("")
    for f in excl[excl.excluida.str.startswith("Excluido a mano")].itertuples():
        filas.append(_fila(f.id, "", f.titulo_excel, f.url, "Tema a revisar", f.excluida, "prefiltro"))

    if (CARPETA / "errores.csv").exists():
        err = pd.read_csv(CARPETA / "errores.csv", dtype=str).fillna("")
        for f in err.itertuples():
            filas.append(_fila(f.id, f.coleccion, f.titulo, f.url, f.estado, f.motivo, "descarga"))

    if (CARPETA / "reporte_validacion.xlsx").exists():
        val = pd.read_excel(CARPETA / "reporte_validacion.xlsx", sheet_name="Archivos", dtype=str).fillna("")
        urls = dict(zip(pre.id, pre.url))
        for f in val[val.estado.isin(ESTADOS_VALIDACION)].itertuples():
            filas.append(_fila(f.id, f.coleccion, f.titulo, urls.get(f.id, ""), f.estado, f.motivo, "validación"))

    df = pd.DataFrame(filas).sort_values(["categoria", "coleccion", "id"])
    resumen = pd.crosstab(df.categoria, df.coleccion.replace("", "(excluido)"), margins=True, margins_name="Total")
    with pd.ExcelWriter(SALIDA, engine="openpyxl") as xl:
        resumen.to_excel(xl, sheet_name="Resumen", startrow=1)
        xl.sheets["Resumen"].cell(1, 1, f"Para revisar por Investigación (generado el {time.strftime('%Y-%m-%d %H:%M')})")
        sin_formulas(df).to_excel(xl, sheet_name="Para revisar", index=False)
    print(f"{len(df)} filas para Investigación -> {SALIDA}")
    print(resumen.to_string())


if __name__ == "__main__":
    main()
