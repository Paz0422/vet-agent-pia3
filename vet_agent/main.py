import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vet_agent.grafo import construir_grafo

# caso de ejemplo (ficticio) de influenza aviar H5N1; tambien lo usa app_demo.py
# usa los mismos nombres de campo que la Parte A de los escenarios de Investigacion
CASO_H5N1 = {
    "especie": "aves de corral (gallinas ponedoras)",
    "sistema_productivo": "plantel de ponedoras comerciales (galpones abiertos sin malla antiaves)",
    "region": "Valparaíso",
    "comuna": "Concón",
    "fecha_evento": "2026-05-01",
    "n_animales": 40000,
    "signos_clinicos": ["mortalidad súbita", "cianosis de cresta y barbilla",
                        "caída brusca de la postura", "signos nerviosos"],
    "mortalidad_pct": 12.0,
    "morbilidad_pct": 30.0,
    "resultados_laboratorio": [
        {"prueba": "RT-PCR", "resultado": "positivo a influenza A (gen M)", "fecha": "2026-05-03"},
        {"prueba": "Subtipificación", "resultado": "H5N1", "fecha": "2026-05-05"},
    ],
    "clima": {"temperatura_media_c": 10.0, "humedad_relativa_pct": 85.0, "precipitacion_mm": 15.0},
    "aves_silvestres_cercanas": "aves migratorias en el humedal a 2 km",
    "historial_brotes_zona": "detecciones de H5N1 en aves silvestres de la costa en temporadas anteriores",
    "movimientos_animales": "ingreso de pollitas de recría hace 2 semanas",
    "indicadores_productivos": "caída brusca de la postura",
}


def pedir_decision(leer=input) -> dict:
    # leer se puede cambiar en las pruebas para no esperar al teclado
    respuesta = leer("\n¿Aprobar el informe? (s/n): ").strip().lower()
    if respuesta == "s":
        return {"aprobado": True, "comentarios": ""}
    # sin comentario el moderador no sabe que corregir: se pide hasta que escriba algo
    comentarios = leer("Comentarios para el moderador: ").strip()
    while not comentarios:
        print("Escribe un comentario para que el moderador sepa qué corregir.")
        comentarios = leer("Comentarios para el moderador: ").strip()
    return {"aprobado": False, "comentarios": comentarios}


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

        decision = pedir_decision()

        # Command(resume=...) le entrega la decision al grafo y sigue desde la pausa
        resultado = app.invoke(Command(resume=decision), config)

    print("\nInforme aprobado por el revisor.")