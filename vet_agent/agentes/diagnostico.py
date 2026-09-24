from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_DIAGNOSTICO, rellenar
from vet_agent.agentes.base import preparar_pregunta, a_texto, SIN_CONTEXTO


def diagnostico(estado: Estado) -> dict:
    caso = estado["caso"]
    instrucciones = rellenar(
        PROMPT_DIAGNOSTICO,
        resultados_lab=a_texto(caso.get("resultados_laboratorio")),
        ventas_tests=a_texto(caso.get("ventas_tests")),
        contexto_rag=SIN_CONTEXTO,
    )
    pregunta = preparar_pregunta(estado, "diagnostico")
    texto = consultar_llm(instrucciones, pregunta, "diagnostico")
    return {"resultados": {"diagnostico": leer_json(texto)}}