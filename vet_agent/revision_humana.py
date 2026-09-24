from langgraph.types import interrupt

from vet_agent.estado import Estado


def revision_humana(estado: Estado) -> dict:
    # interrupt pausa el grafo aca y le muestra el borrador a quien revisa.
    # el grafo sigue recien cuando alguien responde (aprobado o no)
    decision = interrupt({
        "nivel_riesgo": estado["evaluacion"]["nivel_riesgo_global"],
        "borrador": estado["evaluacion"]["borrador_informe"],
    })
    return {"decision_humana": decision}