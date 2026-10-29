import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from vet_agent.grafo import construir_grafo
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Vet Agent API")

RAIZ = Path(__file__).resolve().parents[1]
CARPETA_ESCENARIOS = RAIZ / "corpus" / "escenarios"


@app.get("/escenarios")
def listar_escenarios() -> list[str]:
    if not CARPETA_ESCENARIOS.is_dir():
        return []

    return sorted(
        archivo.stem
        for archivo in CARPETA_ESCENARIOS.glob("*.json")
        if archivo.is_file()
    )


@app.get("/escenarios/{id}")
def obtener_escenario(id: str):
    # Evita que el ID pueda apuntar fuera de la carpeta de escenarios.
    if Path(id).name != id:
        raise HTTPException(status_code=404, detail="Escenario no encontrado")

    ruta = CARPETA_ESCENARIOS / f"{id}.json"
    if not ruta.is_file():
        raise HTTPException(status_code=404, detail="Escenario no encontrado")

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    if "parte_a_entrada" not in datos:
        raise HTTPException(
            status_code=500,
            detail="El escenario no contiene parte_a_entrada",
        )

    return datos["parte_a_entrada"]


@app.post("/analizar")
def analizar(caso: dict[str, Any]):
    grafo = construir_grafo(InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid4())}}

    estado_inicial = {
        "caso": caso,
        "resultados": {},
        "solicitudes": {},
        "revisiones": 0,
        "decision_humana": {},
    }

    resultado = grafo.invoke(estado_inicial, config)

    # La pauta pide aprobar automáticamente la revisión humana en la API.
    while "__interrupt__" in resultado:
        resultado = grafo.invoke(
            Command(resume={"aprobado": True, "comentarios": ""}),
            config,
        )

    informe = resultado.get("evaluacion", {}).get("informe")
    if not isinstance(informe, dict):
        raise HTTPException(status_code=500, detail="El grafo no generó un informe")

    return {
        "informe": informe,
        "fuentes": informe.get("fuentes_utilizadas", []),
    }