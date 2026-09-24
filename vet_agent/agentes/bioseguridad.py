from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_BIOSEGURIDAD
from vet_agent.agentes.base import preparar_pregunta


def bioseguridad(estado: Estado) -> dict:
    pregunta = preparar_pregunta(estado, "bioseguridad")
    # mas adelante aca se agregan los fragmentos del RAG (manuales SAG/OMSA)
    texto = consultar_llm(PROMPT_BIOSEGURIDAD, pregunta, "bioseguridad")
    return {"resultados": {"bioseguridad": leer_json(texto)}}