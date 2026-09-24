from typing import Annotated, TypedDict
import operator


# Ficha compartida que pasa por todos los agentes
class Estado(TypedDict):
    caso: dict  # datos de entrada: especie, enfermedad, ubicacion, etc.

    # lista de hallazgos de cada agente.
    # el operator.add hace que se vayan sumando en vez de pisarse,
    # importante porque los agentes corren al mismo tiempo
    hallazgos: Annotated[list[str], operator.add]

    informe: str  # texto final que arma el moderador