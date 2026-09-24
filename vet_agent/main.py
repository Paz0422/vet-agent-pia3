import json

from vet_agent.grafo import construir_grafo


if __name__ == "__main__":
    app = construir_grafo()

    # caso inventado para probar, despues vendra de la interfaz
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

    resultado = app.invoke({"caso": caso, "resultados": {}, "solicitudes": {}})

    # mostramos el borrador del informe que armo el moderador
    borrador = resultado["evaluacion"]["borrador_informe"]
    print(json.dumps(borrador, ensure_ascii=False, indent=2))