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
LLM_MODELO = os.getenv("LLM_MODELO", "qwen3-4b")
LLM_TEMPERATURA = float(os.getenv("LLM_TEMPERATURA", "0.2"))
LLM_REINTENTOS = int(os.getenv("LLM_REINTENTOS", "2"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "600"))