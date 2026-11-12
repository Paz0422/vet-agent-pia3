import json
import re

from vet_agent.config import (USAR_SIMULACION, LLM_URL, LLM_MODELO, LLM_TEMPERATURA,
                              LLM_REINTENTOS, LLM_TIMEOUT, LLM_MAX_TOKENS)

_modelo = None  # se crea la primera vez que se usa

# respuestas de ejemplo con el mismo formato (y los mismos limites de largo) que piden los
# prompts v2. Son un caso tipico de influenza aviar en aves de corral: sirven para ver la demo
# completa sin LM Studio. En modo simulado, los "fuentes" de los agentes se reemplazan por los
# doc_id que de verdad se recuperaron (ver con_evidencia); los doc_id del informe quedan vacios.
RESPUESTAS_SIMULADAS = {
    "climatico": {
        "agente": "climatico",
        "resumen_condiciones": "Temperaturas bajas y humedad alta, con lluvias recientes; condiciones estables en la última semana.",
        "factores_de_riesgo": [
            {"factor": "temperatura baja y humedad alta", "efecto": "favorece",
             "mecanismo": "El virus persiste más tiempo en el agua, las heces y las superficies.",
             "tipo": "inferencia", "fuentes": []},
            {"factor": "humedal cercano con aves migratorias", "efecto": "favorece",
             "mecanismo": "Las aves silvestres pueden introducir el virus al plantel.",
             "tipo": "inferencia", "fuentes": []},
        ],
        "one_health": {"animal": "Mayor presión de infección desde aves silvestres.",
                       "humana": "Trabajadores expuestos al ambiente contaminado.",
                       "ambiental": "El humedal puede actuar como reservorio del virus."},
        "nivel_riesgo_ambiental": "alto",
        "ventana_temporal": "Próximas 2 a 4 semanas, mientras sigan las condiciones frías y húmedas.",
        "vacios_de_datos": ["Sin censo reciente de aves silvestres en el humedal.",
                            "Sin datos de la estación meteorológica más cercana al plantel."],
        "confianza": "media",
    },
    "diagnostico": {
        "agente": "diagnostico",
        "resumen_resultados": {"muestras": 10, "positivos": 4, "proporcion": 0.4, "pruebas": ["RT-PCR"]},
        "interpretacion": "Los resultados son compatibles con influenza aviar H5N1. La RT-PCR detecta ácido nucleico viral, lo que sugiere infección activa.",
        "probabilidad_presencia": "alta",
        "requiere_confirmacion": True,
        "prueba_confirmatoria_sugerida": "Confirmación y patotipificación en el laboratorio oficial del SAG.",
        "riesgo_zoonotico": "H5N1 tiene potencial zoonótico: los trabajadores en contacto con aves enfermas están expuestos.",
        "one_health": {"animal": "Alta probabilidad de infección activa en el plantel.",
                       "humana": "Riesgo zoonótico para trabajadores sin protección.",
                       "ambiental": "Eliminación de virus por heces y secreciones."},
        "calidad_datos": "Muestra pequeña y sin registro de qué galpones se muestrearon.",
        "afirmaciones": [
            {"texto": "La RT-PCR positiva debe confirmarse en el laboratorio oficial.",
             "tipo": "inferencia", "fuentes": []},
            {"texto": "La mortalidad súbita con signos nerviosos es compatible con influenza aviar altamente patógena.",
             "tipo": "inferencia", "fuentes": []},
        ],
        "confianza": "media",
    },
    "bioseguridad": {
        "agente": "bioseguridad",
        "autoridad_competente": ["SAG", "SEREMI de Salud"],
        "notificacion_obligatoria": "si",
        "medidas": [
            {"nivel": "contencion", "medida": "restricción de movimientos de aves y vehículos",
             "detalle": "Medida que la autoridad competente podría evaluar.",
             "prioridad": "inmediata", "dimension_one_health": "animal",
             "requiere_autoridad": True, "fuentes": []},
            {"nivel": "prevencion", "medida": "control de acceso de personas al plantel",
             "detalle": "Solo personal esencial, con registro de entradas y salidas.",
             "prioridad": "inmediata", "dimension_one_health": "humana",
             "requiere_autoridad": False, "fuentes": []},
            {"nivel": "limpieza_desinfeccion", "medida": "limpieza y desinfección de galpones y equipos",
             "detalle": "Producto, concentración y tiempo de contacto: ver documento fuente.",
             "prioridad": "corto_plazo", "dimension_one_health": "ambiental",
             "requiere_autoridad": False, "fuentes": []},
        ],
        "proteccion_personas": ["EPP completo para quien maneje aves enfermas o muertas.",
                                "Vigilancia de síntomas respiratorios en trabajadores."],
        "proteccion_ambiental": ["Disposición de cadáveres según indique la autoridad: ver documento fuente."],
        "vacios_documentales": ["Sin protocolo específico para galpones abiertos."],
        "confianza": "media",
    },
    "moderador": {
        "estado": "informe_final",
        "informe": {
            "resumen_ejecutivo": "Caso compatible con influenza aviar H5N1 en aves de corral, con RT-PCR positiva, "
                                 "mortalidad súbita y signos nerviosos. El clima frío y húmedo y un humedal cercano "
                                 "con aves migratorias aumentan el riesgo de diseminación.",
            "nivel_riesgo_global": "alto",
            "justificacion_riesgo": "Hay evidencia de laboratorio de infección activa por un virus con potencial "
                                    "zoonótico, en un entorno que favorece su persistencia y su llegada desde aves "
                                    "silvestres. Se aplica el principio de precaución.",
            "riesgos_identificados": [
                {"riesgo": "Diseminación del virus a otros galpones del plantel.", "dimension_one_health": "animal",
                 "evidencia": [], "confianza": "alta"},
                {"riesgo": "Contagio a planteles vecinos por vehículos, personas o aves silvestres.",
                 "dimension_one_health": "animal", "evidencia": [], "confianza": "media"},
                {"riesgo": "Exposición zoonótica de trabajadores sin EPP.", "dimension_one_health": "humana",
                 "evidencia": [], "confianza": "media"},
                {"riesgo": "Contaminación del humedal cercano y de cursos de agua.", "dimension_one_health": "ambiental",
                 "evidencia": [], "confianza": "media"},
            ],
            "analisis_one_health_integrado": "Las condiciones ambientales favorecen la persistencia del virus y su "
                                             "llegada desde aves silvestres; la infección en el plantel expone a los "
                                             "trabajadores y puede volver al ambiente por efluentes y cadáveres.",
            "hipotesis_alternativas": ["Enfermedad de Newcastle.", "Cólera aviar (Pasteurella multocida).",
                                       "Intoxicación (por ejemplo, micotoxinas en el alimento)."],
            "informacion_faltante": ["Patotipificación del virus (alta o baja patogenicidad).",
                                     "Origen y estado sanitario de las aves que ingresaron en el último mes.",
                                     "Mortalidad diaria por galpón de la última semana.",
                                     "Censo reciente de aves silvestres enfermas o muertas en el humedal."],
            "recomendaciones": [
                {"accion": "Notificar de inmediato la sospecha al SAG.", "prioridad": "inmediata",
                 "responsable_sugerido": "médico veterinario del plantel", "requiere_autoridad": True, "fuentes": []},
                {"accion": "Restringir el movimiento de aves, huevos, vehículos y personas: medida que la autoridad "
                           "competente podría evaluar.", "prioridad": "inmediata",
                 "responsable_sugerido": "SAG", "requiere_autoridad": True, "fuentes": []},
                {"accion": "Usar EPP completo al manejar aves enfermas o muertas y registrar a los trabajadores expuestos.",
                 "prioridad": "inmediata", "responsable_sugerido": "encargado del plantel",
                 "requiere_autoridad": False, "fuentes": []},
                {"accion": "Enviar muestras para confirmación y patotipificación al laboratorio oficial.",
                 "prioridad": "corto_plazo", "responsable_sugerido": "médico veterinario del plantel",
                 "requiere_autoridad": True, "fuentes": []},
                {"accion": "Avisar a la SEREMI de Salud para vigilar síntomas en trabajadores expuestos.",
                 "prioridad": "corto_plazo", "responsable_sugerido": "médico veterinario del plantel",
                 "requiere_autoridad": True, "fuentes": []},
                {"accion": "Instalar malla antiaves y tratar el agua de bebida.", "prioridad": "preventiva",
                 "responsable_sugerido": "encargado del plantel", "requiere_autoridad": False, "fuentes": []},
            ],
            "protocolos_bioseguridad": ["Control de acceso con registro de entradas y salidas.",
                                        "Limpieza y desinfección de galpones y equipos: ver documento fuente.",
                                        "Pediluvios y cambio de ropa al entrar y salir de cada galpón."],
            "incertidumbres_y_limitaciones": ["La muestra de laboratorio es pequeña.",
                                              "No se sabe si el virus es de alta o baja patogenicidad."],
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
    # LLM_REINTENTOS se lee aca (no al importar) para que evaluar.py lo pueda dejar en 0
    for intento in range(1, LLM_REINTENTOS + 2):
        mensaje = _cliente().invoke(mensajes)
        # si el modelo llego a max_tokens el JSON quedo cortado: volver a preguntar no lo arregla
        if (mensaje.response_metadata or {}).get("finish_reason") == "length":
            raise ValueError(f"[{agente}] la respuesta se cortó al llegar a max_tokens ({LLM_MAX_TOKENS}); "
                             "sube LLM_MAX_TOKENS en el .env")
        texto = mensaje.content
        if not texto.strip():
            motivo = "la respuesta vino vacía"
        else:
            try:
                leer_json(texto)  # solo revisa que sea JSON valido
                return texto
            except json.JSONDecodeError:
                motivo = "la respuesta no es JSON valido"
        print(f"[{agente}] {motivo} (intento {intento})")
    raise ValueError(f"[{agente}] {motivo} en {LLM_REINTENTOS + 1} intento(s)")


def _cliente():
    # LM Studio habla igual que la API de OpenAI, por eso sirve ChatOpenAI
    global _modelo
    if _modelo is None:
        from langchain_openai import ChatOpenAI
        _modelo = ChatOpenAI(base_url=LLM_URL, api_key="lm-studio", model=LLM_MODELO,
                             temperature=LLM_TEMPERATURA, timeout=LLM_TIMEOUT, max_retries=0,
                             max_tokens=LLM_MAX_TOKENS)
    return _modelo


def leer_json(texto: str) -> dict:
    # algunos modelos "piensan" en voz alta dentro de <think>...</think>: se borra
    limpio = re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL)
    # y otros envuelven el JSON en ```json ... ``` o agregan texto: nos quedamos del primer { al ultimo }
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio == -1 or fin == -1:
        raise json.JSONDecodeError("no hay JSON en la respuesta", limpio, 0)
    return json.loads(limpio[inicio:fin + 1])