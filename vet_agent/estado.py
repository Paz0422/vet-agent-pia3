from typing import Annotated, TypedDict
import operator


# Datos de entrada de un caso epidemiologico.
# total=False porque no siempre vamos a tener todos los datos
class Caso(TypedDict, total=False):
    especie: str
    sistema_productivo: str       # ej: plantel de engorda, reproductoras
    ubicacion: str
    periodo: str
    signos_clinicos: list[str]
    mortalidad: str
    resultados_laboratorio: list[str]   # PCR, ELISA, etc
    clima: dict                   # temperatura, humedad, lluvias...
    historial_brotes: str
    movimientos_animales: str
    manejo_bioseguridad: str      # desinfeccion, cuarentena, visitas...


# Ficha compartida que recorre el grafo
class Estado(TypedDict):
    caso: Caso

    # el JSON que devuelve cada agente, ej: {"climatico": {...}}
    # operator.or_ junta los diccionarios porque los agentes escriben
    # al mismo tiempo y si no se pisarian
    resultados: Annotated[dict, operator.or_]

    # lo que el moderador le pide aclarar a cada agente (si pide algo)
    solicitudes: dict

    # el ultimo JSON completo del moderador, incluye el borrador del informe
    evaluacion: dict

      # cuantas veces ha evaluado el moderador, para no quedar en un loop infinito
    revisiones: int

    # lo que decidio el revisor humano: {"aprobado": True/False, "comentarios": "..."}
    decision_humana: dict