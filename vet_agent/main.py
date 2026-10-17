import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent.grafo import construir_grafo

# caso de ejemplo (ficticio) de influenza aviar H5N1; tambien lo usa app_demo.py
CASO_H5N1 = {
    "especie": "aves de corral (gallinas ponedoras)",
    "sistema_productivo": "plantel de ponedoras comerciales, 40.000 aves",
    "ubicacion": "Región de Valparaíso, cerca de un humedal costero",
    "periodo": "mayo 2026",
    "signos_clinicos": ["mortalidad súbita", "cianosis de cresta y barbilla",
                        "caída brusca de la postura", "signos nerviosos"],
    "mortalidad": "12% en 48 horas en un galpón",
    "resultados_laboratorio": ["RT-PCR positivo a influenza A (gen M)", "subtipificación H5N1 por RT-PCR"],
    "clima": {"temperatura_promedio": "10°C", "humedad_relativa": "85%", "precipitaciones": "moderadas"},
    "historial_brotes": "detecciones de H5N1 en aves silvestres de la costa en temporadas anteriores",
    "movimientos_animales": "ingreso de pollitas de recría hace 2 semanas; aves migratorias en el humedal a 2 km",
    "manejo_bioseguridad": "galpones abiertos sin malla antiaves, agua de bebida desde un tranque sin tratar",
}


if __name__ == "__main__":
    # InMemorySaver guarda el estado en memoria mientras corre el programa
    app = construir_grafo(InMemorySaver())

    # thread_id identifica el caso, sirve para retomarlo despues de la pausa
    config = {"configurable": {"thread_id": "caso-prueba-1"}}

    estado_inicial = {
        "caso": CASO_H5N1,
        "resultados": {},
        "solicitudes": {},
        "revisiones": 0,
        "decision_humana": {},
    }

    resultado = app.invoke(estado_inicial, config)

    # mientras el grafo este pausado esperando revision, preguntamos por terminal
    while "__interrupt__" in resultado:
        pausa = resultado["__interrupt__"][0].value
        print("\n--- INFORME PARA REVISIÓN ---")
        print("Nivel de riesgo:", pausa["nivel_riesgo"])
        print(json.dumps(pausa["informe"], ensure_ascii=False, indent=2))

        # si el moderador no alcanzo a cerrar, mostramos lo que quedo pendiente
        if pausa["preguntas_pendientes"]:
            print("\nPreguntas sin resolver:")
            for p in pausa["preguntas_pendientes"]:
                print(f"- {p['agente_destino']}: {p['pregunta']}")

        respuesta = input("\n¿Aprobar el informe? (s/n): ").strip().lower()
        if respuesta == "s":
            decision = {"aprobado": True, "comentarios": ""}
        else:
            comentarios = input("Comentarios para el moderador: ")
            decision = {"aprobado": False, "comentarios": comentarios}

        # Command(resume=...) le entrega la decision al grafo y sigue desde la pausa
        resultado = app.invoke(Command(resume=decision), config)

    print("\nInforme aprobado por el revisor.")