import json
import re

from vet_agent.config import (USAR_SIMULACION, LLM_URL, LLM_MODELO,
                              LLM_TEMPERATURA, LLM_REINTENTOS, LLM_TIMEOUT)

_modelo = None  # se crea la primera vez que se usa

# respuestas de ejemplo con el mismo formato que piden los prompts
RESPUESTAS_SIMULADAS = {
    "climatico": {
        "agente": "climatico",
        "resumen_condiciones": "temperaturas bajas y lluvias altas (simulado)",
        "factores_de_riesgo": [
            {"factor": "temperatura baja", "efecto": "favorece",
             "mecanismo": "mayor persistencia viral en el ambiente y en el agua",
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
        "interpretacion": "compatible con circulación de influenza aviar H5N1 (simulado)",
        "probabilidad_presencia": "alta",
        "requiere_confirmacion": True,
        "prueba_confirmatoria_sugerida": "ver documento fuente",
        "senal_ventas_tests": {"anomalia_detectada": False, "descripcion": "sin datos", "causas_alternativas": []},
        "riesgo_zoonotico": "presente en H5N1: ver documento fuente",
        "one_health": {"animal": "simulado", "humana": "simulado", "ambiental": "simulado"},
        "calidad_datos": "muestra pequeña",
        "afirmaciones": [
            {"texto": "la RT-PCR positiva debe confirmarse en el laboratorio oficial (simulado)",
             "tipo": "inferencia", "fuentes": []}
        ],
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
            "resumen_ejecutivo": "caso simulado compatible con influenza aviar H5N1",
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

    mensajes = [("system", instrucciones), ("human", pregunta)]
    for intento in range(1, LLM_REINTENTOS + 2):
        texto = _cliente().invoke(mensajes).content
        try:
            leer_json(texto)  # solo revisa que sea JSON valido
            return texto
        except json.JSONDecodeError:
            print(f"[{agente}] la respuesta no es JSON valido (intento {intento})")
    raise ValueError(f"[{agente}] el modelo no entrego JSON valido en {LLM_REINTENTOS + 1} intentos")


def _cliente():
    # LM Studio habla igual que la API de OpenAI, por eso sirve ChatOpenAI
    global _modelo
    if _modelo is None:
        from langchain_openai import ChatOpenAI
        _modelo = ChatOpenAI(base_url=LLM_URL, api_key="lm-studio", model=LLM_MODELO,
                             temperature=LLM_TEMPERATURA, timeout=LLM_TIMEOUT,max_retries=0)
    return _modelo


def leer_json(texto: str) -> dict:
    # algunos modelos "piensan" en voz alta dentro de <think>...</think>: se borra
    limpio = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL)
    # y otros envuelven el JSON en ```json ... ``` o agregan texto: nos quedamos del primer { al ultimo }
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio == -1 or fin == -1:
        raise json.JSONDecodeError("no hay JSON en la respuesta", limpio, 0)
    return json.loads(limpio[inicio:fin + 1])