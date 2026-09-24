from vet_agent.estado import Estado


def moderador(estado: Estado) -> dict:
    # por ahora solo junta lo que dijeron los demas.
    # mas adelante tiene que revisar contradicciones y armar el informe de verdad
    informe = "\n".join(estado["hallazgos"])
    return {"informe": informe}