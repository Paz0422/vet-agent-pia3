import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent.grafo import construir_grafo


if __name__ == "__main__":
    # InMemorySaver guarda el estado en memoria mientras corre el programa
    app = construir_grafo(InMemorySaver())

    # thread_id identifica el caso, sirve para retomarlo despues de la pausa
    config = {"configurable": {"thread_id": "caso-prueba-1"}}

    caso = {
        "especie": "porcino",
        "sistema_productivo": "plantel de engorda",
        "ubicacion": "Región de O'Higgins",
        "periodo": "julio 2026",
        "signos_clinicos": ["fiebre", "dificultad respiratoria", "abortos en reproductoras"],
        "mortalidad": "4% semanal, sobre lo normal",
        "resultados_laboratorio": ["PCR positivo a PRRSV"],
        "clima": {"temperatura_promedio": "8°C", "precipitaciones": "altas"},
        "movimientos_animales": "ingreso de 200 lechones hace 3 semanas",
        "manejo_bioseguridad": "sin cuarentena de ingreso",
    }

    estado_inicial = {
        "caso": caso,
        "resultados": {},
        "solicitudes": {},
        "revisiones": 0,
        "decision_humana": {},
    }

    resultado = app.invoke(estado_inicial, config)

    # mientras el grafo este pausado esperando revision, preguntamos por terminal
    while "__interrupt__" in resultado:
        pausa = resultado["__interrupt__"][0].value
        print("\n--- BORRADOR PARA REVISIÓN ---")
        print("Nivel de riesgo:", pausa["nivel_riesgo"])
        print(json.dumps(pausa["borrador"], ensure_ascii=False, indent=2))

        respuesta = input("\n¿Aprobar el informe? (s/n): ").strip().lower()
        if respuesta == "s":
            decision = {"aprobado": True, "comentarios": ""}
        else:
            comentarios = input("Comentarios para el moderador: ")
            decision = {"aprobado": False, "comentarios": comentarios}

        # Command(resume=...) le entrega la decision al grafo y sigue desde la pausa
        resultado = app.invoke(Command(resume=decision), config)

    print("\nInforme aprobado por el revisor.")