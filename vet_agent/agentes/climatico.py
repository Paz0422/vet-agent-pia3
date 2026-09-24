from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_CLIMATICO
from vet_agent.agentes.base import preparar_pregunta


def climatico(estado: Estado) -> dict:
    pregunta = preparar_pregunta(estado, "climatico")
    texto = consultar_llm(PROMPT_CLIMATICO, pregunta, "climatico")
    # guardamos el JSON del agente bajo su nombre
    return {"resultados": {"climatico": leer_json(texto)}}