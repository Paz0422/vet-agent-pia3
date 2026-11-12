# configuracion general del sistema

# rondas maximas de debate del moderador antes de pasar a revision humana
MAX_RONDAS = 3
# --- LLM local (LM Studio) ---
# Los valores se leen del archivo .env; si no existe, se usan los de aca.
import os
from dotenv import load_dotenv

load_dotenv()

# True = respuestas de ejemplo (no necesita LM Studio). False = pregunta al modelo de verdad.
USAR_SIMULACION = os.getenv("USAR_SIMULACION", "true").lower() == "true"
LLM_URL = os.getenv("LLM_URL", "http://localhost:1234/v1")
LLM_MODELO = os.getenv("LLM_MODELO", "qwen/qwen3-4b-2507")
LLM_TEMPERATURA = float(os.getenv("LLM_TEMPERATURA", "0.2"))
LLM_REINTENTOS = int(os.getenv("LLM_REINTENTOS", "2"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "3600"))
# tope de tokens de cada respuesta del modelo (con 3000 el informe del moderador se cortaba;
# el prompt del moderador + esta respuesta tienen que caber en el contexto de LM Studio, 16384)
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# --- RAG: cuanto texto de los documentos va en cada prompt ---
# fragmentos que busca cada agente en su coleccion
K_FRAGMENTOS = int(os.getenv("K_FRAGMENTOS", "3"))
# largo maximo (en caracteres) de cada fragmento dentro del prompt
MAX_CARACTERES_FRAGMENTO = int(os.getenv("MAX_CARACTERES_FRAGMENTO", "1200"))

# --- demo ---
# true = la demo muestra desde el inicio el "Modo equipo" (comparacion B1/B2/B3, JSON y
# salidas de los agentes); igual se puede prender y apagar en la barra lateral
DEMO_MODO_EQUIPO = os.getenv("DEMO_MODO_EQUIPO", "false").lower() == "true"
