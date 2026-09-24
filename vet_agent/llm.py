import json

# mientras no tengamos LLM trabajamos con respuestas de prueba.
# cuando este elegido, se cambia a False y se completa la llamada real
USAR_SIMULACION = True

# respuestas de ejemplo con el mismo formato que piden los prompts
RESPUESTAS_SIMULADAS = {
    "climatico": {
        "agente": "climatico",
        "nivel_riesgo_climatico": "medio",
        "factores_identificados": ["temperaturas bajas favorecen persistencia viral"],
        "justificacion": "respuesta simulada",
        "hipotesis": [],
        "informacion_faltante": ["humedad relativa"],
        "fuentes": [],
        "confianza": "media",
    },
    "diagnostico": {
        "agente": "diagnostico",
        "enfermedad_compatible": "compatible con PRRS",
        "diagnosticos_diferenciales": ["influenza porcina", "circovirus porcino"],
        "hallazgos_clave": ["PCR positivo"],
        "justificacion": "respuesta simulada",
        "hipotesis": [],
        "informacion_faltante": [],
        "fuentes": [],
        "confianza": "media",
    },
    "bioseguridad": {
        "agente": "bioseguridad",
        "nivel_riesgo_contencion": "alto",
        "brechas_bioseguridad": ["ingreso de animales sin cuarentena"],
        "riesgo_zoonotico_identificado": "nulo",
        "justificacion": "respuesta simulada",
        "medidas_mitigacion_urgentes": ["[REQUIERE AUTORIDAD] restringir movimientos"],
        "informacion_faltante": [],
        "fuentes": [],
        "confianza": "media",
    },
    "moderador": {
        "agente": "moderador",
        "coincidencias": ["riesgo medio-alto"],
        "contradicciones": [],
        "requiere_nueva_revision": False,
        "agente_a_consultar": [],
        "solicitud_de_aclaracion": "",
        "nivel_riesgo_global": "alto",
        "borrador_informe": {
            "sintesis_evento": "caso simulado compatible con PRRS",
            "factores_de_riesgo": [],
            "hipotesis_alternativas": [],
            "informacion_faltante": [],
            "medidas_sugeridas": [],
            "fuentes": [],
            "advertencias_y_limitaciones": ["informe generado con apoyo de IA, requiere revision profesional"],
        },
        "confianza": "media",
    },
}


def consultar_llm(instrucciones: str, pregunta: str, agente: str) -> str:
    # instrucciones = el prompt del agente, pregunta = el caso a analizar
    # agente solo se usa para elegir la respuesta simulada
    if USAR_SIMULACION:
        return json.dumps(RESPUESTAS_SIMULADAS[agente], ensure_ascii=False)

    # TODO: aca va la llamada real al LLM (la otra semana)
    raise NotImplementedError("Falta conectar el LLM")


def leer_json(texto: str) -> dict:
    # a veces los modelos envuelven el JSON en ```json ... ```, se lo sacamos
    limpio = texto.replace("```json", "").replace("```", "").strip()
    return json.loads(limpio)