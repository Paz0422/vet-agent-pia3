from typing import Annotated, TypedDict
import operator


# Datos de entrada de un caso epidemiologico.
# total=False porque no siempre vamos a tener todos los datos.
# Los nombres de campo calzan con la Parte A de los escenarios de Investigacion.
class Caso(TypedDict, total=False):
    especie: str
    sistema_productivo: str       # ej: ponedoras comerciales, broiler
    region: str
    comuna: str
    fecha_evento: str             # AAAA-MM-DD
    n_animales: int
    signos_clinicos: list[str]
    mortalidad_pct: float         # porcentaje de mortalidad
    morbilidad_pct: float         # porcentaje de morbilidad
    resultados_laboratorio: list[dict]   # [{"prueba", "resultado", "fecha"}]
    clima: dict                   # temperatura_media_c, humedad_relativa_pct, precipitacion_mm
    aves_silvestres_cercanas: str
    historial_brotes_zona: str
    movimientos_animales: str
    indicadores_productivos: str


# Ficha compartida que recorre el grafo
class Estado(TypedDict):
    caso: Caso

    # el JSON que devuelve cada agente, ej: {"climatico": {...}}
    # operator.or_ junta los resultados que cada agente va entregando;
    # sin eso, cada agente reemplazaria el diccionario entero
    resultados: Annotated[dict, operator.or_]

    # lo que el moderador le pide aclarar a cada agente (si pide algo)
    solicitudes: dict

    # el ultimo JSON completo del moderador, incluye el borrador del informe
    evaluacion: dict

    # cuantas veces ha evaluado el moderador, para no quedar en un loop infinito
    revisiones: int

    # lo que decidio el revisor humano: {"aprobado": True/False, "comentarios": "..."}
    decision_humana: dict