from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_DIAGNOSTICO
from vet_agent.agentes.base import preparar_pregunta


def diagnostico(estado: Estado) -> dict:
    pregunta = preparar_pregunta(estado, "diagnostico")
    texto = consultar_llm(PROMPT_DIAGNOSTICO, pregunta, "diagnostico")
    return {"resultados": {"diagnostico": leer_json(texto)}}