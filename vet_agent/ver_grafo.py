from vet_agent.grafo import construir_grafo


# script aparte solo para ver como quedo el grafo, no es parte del sistema
if __name__ == "__main__":
    app = construir_grafo()

    # genera la imagen del grafo y la guarda en la carpeta PIA
    imagen = app.get_graph().draw_mermaid_png()
    with open("grafo.png", "wb") as archivo:
        archivo.write(imagen)

    print("Listo, revisa grafo.png")