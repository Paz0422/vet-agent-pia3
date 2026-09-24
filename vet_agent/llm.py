import json

# mientras no tengamos LLM trabajamos con respuestas de prueba.
# cuando este elegido, se cambia a False y se completa la llamada real
USAR_SIMULACION = True

# respuestas de ejemplo con el mismo formato que piden los prompts
RESPUESTAS_SIMULADAS = {
    "climatico": {
        "agente": "climatico",
        "resumen_condiciones": "temperaturas bajas y lluvias altas (simulado)",
        "factores_de_riesgo": [
            {"factor": "temperatura baja", "efecto": "favorece",
             "mecanismo": "mayor persistencia viral en aerosoles",
             "tipo": "inferencia", "fuentes": []}
        ],
        "one_health": {"animal": "simulado", "humana": "simulado", "ambiental": "simulado"},
        "nivel_riesgo_ambiental": "moderado",
        "ventana_temporal": "julio 2026",
        "vacios_de_datos": ["humedad relativa"],
        "confianza": "media",
    },
    "diagnostico": {
        "agente": "diagnostico",
        "resumen_resultados": {"muestras": 10, "positivos": 4, "proporcion": 0.4, "pruebas": ["RT-PCR"]},
        "interpretacion": "compatible con circulación de PRRSV (simulado)",
        "probabilidad_presencia": "alta",
        "requiere_confirmacion": True,
        "prueba_confirmatoria_sugerida": "ver documento fuente",
        "senal_ventas_tests": {"anomalia_detectada": False, "descripcion": "sin datos", "causas_alternativas": []},
        "riesgo_zoonotico": "bajo o nulo en PRRS",
        "one_health": {"animal": "simulado", "humana": "simulado", "ambiental": "simulado"},
        "calidad_datos": "muestra pequeña",
        "afirmaciones": [],
        "confianza": "media",
    },
    "bioseguridad": {
        "agente": "bioseguridad",
        "autoridad_competente": ["SAG"],
        "notificacion_obligatoria": "no_determinable",
        "medidas": [
            {"nivel": "contencion", "medida": "restricción de movimientos",
             "detalle": "medida que la autoridad competente podría evaluar",
             "prioridad": "inmediata", "dimension_one_health": "animal",
             "requiere_autoridad": True, "fuentes": []}
        ],
        "proteccion_personas": ["uso de EPP"],
        "proteccion_ambiental": ["disposición de cadáveres: ver documento fuente"],
        "vacios_documentales": ["sin documentos recuperados"],
        "confianza": "baja",
    },
    "moderador": {
        "estado": "informe_final",
        "informe": {
            "resumen_ejecutivo": "caso simulado compatible con PRRS",
            "nivel_riesgo_global": "alto",
            "justificacion_riesgo": "simulado",
            "riesgos_identificados": [],
            "analisis_one_health_integrado": "simulado",
            "recomendaciones": [],
            "protocolos_bioseguridad": [],
            "incertidumbres_y_limitaciones": ["sin respaldo documental aún"],
            "afirmaciones_descartadas": [],
            "fuentes_utilizadas": [],
            "aviso": "Informe de apoyo a la decisión. Requiere validación de un médico veterinario y de la autoridad sanitaria competente.",
        },
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