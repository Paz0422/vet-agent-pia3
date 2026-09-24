from vet_agent.grafo import construir_grafo


if __name__ == "__main__":
    app = construir_grafo()

    # caso de prueba inventado, despues vendra de la interfaz
    caso = {"especie": "porcino", "enfermedad": "PRRS", "region": "O'Higgins"}

    resultado = app.invoke({"caso": caso, "hallazgos": []})
    print(resultado["informe"])
    