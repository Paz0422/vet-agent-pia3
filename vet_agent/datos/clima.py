"""Trae temperatura, humedad y precipitacion desde Open-Meteo (gratis, sin clave)."""
import requests

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def _get(url: str, params: dict) -> dict:
    # funcion aparte para que las pruebas la reemplacen sin llamar a internet
    respuesta = requests.get(url, params=params, timeout=10)
    respuesta.raise_for_status()
    return respuesta.json()


def traer_clima(lugar: str, fecha: str) -> dict | None:
    """Busca el clima de `lugar` (nombre de ciudad/comuna) en `fecha` (AAAA-MM-DD).

    Devuelve {"temperatura_media_c", "humedad_relativa_pct", "precipitacion_mm"},
    o None si no hay internet o no se encontro el lugar (nunca lanza una excepcion).
    """
    try:
        geo = _get(GEOCODING_URL, {"name": lugar, "country": "CL"})
        resultados = geo.get("results")
        if not resultados:
            return None
        lat, lon = resultados[0]["latitude"], resultados[0]["longitude"]

        clima = _get(ARCHIVE_URL, {
            "latitude": lat,
            "longitude": lon,
            "start_date": fecha,
            "end_date": fecha,
            "daily": "temperature_2m_mean,relative_humidity_2m_mean,precipitation_sum",
            "timezone": "America/Santiago",
        })
        diario = clima.get("daily") or {}
        if not diario.get("time"):
            return None

        return {
            "temperatura_media_c": diario["temperature_2m_mean"][0],
            "humedad_relativa_pct": diario["relative_humidity_2m_mean"][0],
            "precipitacion_mm": diario["precipitation_sum"][0],
        }
    except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
        return None
