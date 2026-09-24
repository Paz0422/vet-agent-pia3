from langgraph.types import interrupt

from vet_agent.estado import Estado


def revision_humana(estado: Estado) -> dict:
    evaluacion = estado["evaluacion"]

    # si se acabaron las rondas sin informe final, igual llega al humano
    # con lo que haya y con las preguntas que quedaron sin resolver
    informe = evaluacion.get("informe", {})

    decision = interrupt({
        "nivel_riesgo": informe.get("nivel_riesgo_global", "no determinado"),
        "informe": informe,
        "preguntas_pendientes": evaluacion.get("preguntas", []),
    })
    return {"decision_humana": decision}