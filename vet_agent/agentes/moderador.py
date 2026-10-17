import json

from vet_agent import llm
from vet_agent.config import MAX_RONDAS
from vet_agent.estado import Estado
from vet_agent.llm import consultar_llm, leer_json
from vet_agent.prompts import PROMPT_MODERADOR, rellenar
from vet_agent.agentes.base import SIN_CONTEXTO


def evidencia_unida(resultados: dict) -> list[dict]:
    # los fragmentos de los tres agentes, sin repetir el mismo pedazo dos veces
    vistos, unida = set(), []
    for salida in resultados.values():
        for e in salida.get("evidencia", []):
            clave = (e["doc_id"], e["texto"])
            if clave not in vistos:
                vistos.add(clave)
                unida.append(e)
    return unida


def fuentes_de(evidencia: list[dict]) -> list[dict]:
    # una entrada por documento, con el formato de fuentes_utilizadas del prompt
    fuentes = {}
    for e in evidencia:
        fuentes.setdefault(e["doc_id"], {"doc_id": e["doc_id"], "organismo": e["institucion"], "anio": str(e["anio"])})
    return list(fuentes.values())


def moderador(estado: Estado) -> dict:
    ronda = estado.get("revisiones", 0) + 1

    # la evidencia va aparte como contexto para verificar; en las salidas solo quedan los hallazgos
    salidas = {
        agente: {k: v for k, v in salida.items() if k != "evidencia"}
        for agente, salida in estado["resultados"].items()
    }
    evidencia = evidencia_unida(estado["resultados"])
    contexto = "\n\n---\n\n".join(
        f"[{e['doc_id']}] {e['institucion']} {e['anio']} — {e['titulo']}\n{e['texto']}" for e in evidencia
    ) or SIN_CONTEXTO

    instrucciones = rellenar(
        PROMPT_MODERADOR,
        salidas_agentes=json.dumps(salidas, ensure_ascii=False, indent=2),
        contexto_rag=contexto,
        ronda=ronda,
        max_rondas=MAX_RONDAS,
    )

    pregunta = "Evalúa las salidas de los agentes y responde con el JSON indicado."

    # si el revisor humano rechazo el informe anterior, le pasamos sus comentarios
    decision = estado.get("decision_humana") or {}
    if decision and not decision.get("aprobado") and decision.get("comentarios"):
        pregunta += (
            "\n\nEl revisor humano rechazó el informe anterior con estos comentarios: "
            + decision["comentarios"]
        )

    texto = consultar_llm(instrucciones, pregunta, "moderador")
    evaluacion = leer_json(texto)

    # con la redaccion simulada, las fuentes del informe son los documentos realmente recuperados
    if llm.USAR_SIMULACION and "informe" in evaluacion:
        evaluacion["informe"]["fuentes_utilizadas"] = fuentes_de(evidencia)

    # juntamos las preguntas por agente (puede haber mas de una para el mismo)
    solicitudes = {}
    if evaluacion.get("estado") == "requiere_debate":
        for p in evaluacion.get("preguntas", []):
            destino = p.get("agente_destino")
            if destino in solicitudes:
                solicitudes[destino] += " / " + p["pregunta"]
            else:
                solicitudes[destino] = p["pregunta"]

    return {"evaluacion": evaluacion, "solicitudes": solicitudes, "revisiones": ronda}