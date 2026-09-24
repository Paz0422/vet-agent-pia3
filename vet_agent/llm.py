# Punto unico de conexion con el LLM.
# Todos los agentes pasan por aca, asi cuando tengamos proveedor
# (Claude, GPT o Gemini) solo hay que cambiar esta funcion.


def consultar_llm(instrucciones: str, pregunta: str) -> str:
    # instrucciones = el "rol" del agente (ej: experto en clima)
    # pregunta = lo que se le pide analizar

    # TODO: reemplazar por la llamada real a la API
    return f"[simulado] {pregunta[:60]}..."