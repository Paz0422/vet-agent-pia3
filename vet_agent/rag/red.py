"""Pedidos HTTP responsables, compartidos por prefiltro.py y la descarga.

Reglas que se cumplen siempre:
- User-Agent que identifica al proyecto.
- robots.txt de cada dominio (tambien en cada redireccion).
- Dentro de un mismo dominio, un pedido a la vez y al menos ESPERA segundos
  entre pedidos (o lo que pida el Crawl-delay del robots.txt).
- Nunca se intenta saltar logins, paywalls, CAPTCHAs ni bloqueos: se informan.

Es seguro usarlo desde varios hilos a la vez (cada dominio tiene su candado).
"""
import ipaddress
import re
import socket
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "vet-agent-pia3 (proyecto universitario UTEM; ppereza@utem.cl)"
ESPERA = 2.0                                  # segundos minimos entre pedidos al mismo dominio
ESPERA_POR_DOMINIO = {"api.openalex.org": 1.0}  # APIs que permiten ir mas rapido
MAX_REDIRECCIONES = 5

# URL de login: /login, /signin, /sign-in, /auth (palabra completa: /authors no cuenta)
RE_LOGIN = re.compile(r"/(log-?in|sign-?in|auth)\b", re.IGNORECASE)
# Titulos de paginas anti-bots (CAPTCHA, Cloudflare)
RE_ANTIBOTS = re.compile(
    rb"<title[^>]*>[^<]*(captcha|checking your browser|just a moment|attention required)",
    re.IGNORECASE)

# Estados que se informan (los mismos nombres que usa el prefiltro)
CAIDO = "Caído"
PORTADA_LOGIN = "Portada o login"
BLOQUEADO_ROBOTS = "Bloqueado por robots.txt"
CON_PAGO = "Con pago"


class FalloRed(Exception):
    """Un pedido que no se pudo hacer. Lleva el estado y el motivo para el reporte."""

    def __init__(self, estado: str, motivo: str):
        super().__init__(motivo)
        self.estado = estado
        self.motivo = motivo


_local = threading.local()                       # una sesion por hilo
_candados: dict[str, threading.Lock] = {}        # un candado por dominio
_candado_global = threading.Lock()
_ultimo_pedido: dict[str, float] = {}
_robots: dict[str, RobotFileParser] = {}
_motivo_robots: dict[str, str] = {}              # por que se bloqueo un dominio entero
_destinos_ok: dict[str, bool] = {}               # host -> es una direccion publica


def _destino_publico(url: str) -> None:
    """Solo http/https hacia direcciones publicas (nunca la red local ni este PC).

    Evita que una URL o una redireccion de un sitio externo haga que el
    programa pida cosas a localhost, al router o a otras maquinas internas.
    """
    partes = urlparse(url)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        raise FalloRed(CAIDO, f"URL no permitida ({partes.scheme or 'sin esquema'})")
    host = partes.hostname
    if host not in _destinos_ok:
        try:
            ips = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except socket.gaierror:
            raise FalloRed(CAIDO, f"{host}: el dominio no existe")
        _destinos_ok[host] = all(ipaddress.ip_address(ip.split("%")[0]).is_global for ip in ips)
    if not _destinos_ok[host]:
        raise FalloRed(CAIDO, f"{host}: apunta a una red privada o local (no se pide)")


def _sesion() -> requests.Session:
    if not hasattr(_local, "sesion"):
        _local.sesion = requests.Session()
        _local.sesion.headers["User-Agent"] = USER_AGENT
    return _local.sesion


def _candado(clave: str) -> threading.Lock:
    with _candado_global:
        return _candados.setdefault(clave, threading.Lock())


def pedir(url: str, *, timeout: float = 30, stream: bool = False, reintentos: int = 0,
          params: dict | None = None) -> requests.Response:
    """GET sin seguir redirecciones, esperando el turno del dominio.

    Reintenta (con pausa creciente) solo si no hubo respuesta o fue un 5xx.
    """
    _destino_publico(url)
    dominio = urlparse(url).netloc
    espera = ESPERA_POR_DOMINIO.get(dominio, ESPERA)
    if dominio in _robots:  # si robots.txt pide mas pausa (Crawl-delay), se respeta
        espera = max(espera, float(_robots[dominio].crawl_delay(USER_AGENT) or 0))
    for intento in range(reintentos + 1):
        with _candado(dominio):  # un pedido a la vez por dominio
            falta = _ultimo_pedido.get(dominio, 0) + espera - time.monotonic()
            if falta > 0:
                time.sleep(falta)
            try:
                r = _sesion().get(url, params=params, timeout=timeout, stream=stream, allow_redirects=False)
            except requests.Timeout:
                r, error = None, f"sin respuesta en {timeout:.0f} s"
            except requests.ConnectionError:
                r, error = None, "sitio caído o sin conexión"
            finally:
                _ultimo_pedido[dominio] = time.monotonic()
        if r is not None and r.status_code < 500:
            return r
        if r is not None:
            error = f"error del servidor (HTTP {r.status_code})"
            if intento == reintentos:
                return r  # el que llama decide que hacer con el 5xx
            r.close()
        if intento < reintentos:
            time.sleep(2 * (intento + 1))
    raise FalloRed(CAIDO, error)


def permitido(url: str) -> bool:
    """Lee (una vez por dominio) el robots.txt y dice si se puede pedir la URL."""
    partes = urlparse(url)
    dominio = partes.netloc
    with _candado("robots:" + dominio):
        if dominio not in _robots:
            rp = RobotFileParser()
            robots = f"{partes.scheme}://{dominio}/robots.txt"
            try:
                r = pedir(robots, timeout=15)
                for _ in range(MAX_REDIRECCIONES):  # la norma RFC 9309 pide seguir redirecciones
                    if not r.is_redirect:
                        break
                    r = pedir(urljoin(robots, r.headers["Location"]), timeout=15)
            except FalloRed:
                r = None
            if r is not None and r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif r is not None and r.status_code in (401, 403):
                _motivo_robots[dominio] = f"{dominio} rechaza bots (su robots.txt responde HTTP {r.status_code})"
                rp.disallow_all = True   # criterio prudente: si niega el robots.txt, no se entra
            elif r is not None and 400 <= r.status_code < 500:
                rp.allow_all = True      # no hay robots.txt: se permite todo
            else:
                estado = f"HTTP {r.status_code}" if r is not None else "sin respuesta"
                _motivo_robots[dominio] = f"no se pudo leer el robots.txt de {dominio} ({estado})"
                rp.disallow_all = True   # no se pudo leer (caido, 5xx): mejor no entrar
            _robots[dominio] = rp
    return _robots[dominio].can_fetch(USER_AGENT, url)


def _revisar_robots(url: str) -> None:
    if not permitido(url):
        dominio = urlparse(url).netloc
        motivo = _motivo_robots.get(dominio, f"robots.txt de {dominio} no permite esta ruta")
        raise FalloRed(BLOQUEADO_ROBOTS, motivo)


def seguir(url: str, *, timeout: float = 30, stream: bool = False, reintentos: int = 0,
           params: dict | None = None) -> tuple[str, requests.Response]:
    """Pide una URL siguiendo redirecciones a mano y revisando robots.txt en cada salto.

    Devuelve (url_final, respuesta). Si algo impide seguir, lanza FalloRed.
    """
    original = urlparse(url)
    for _ in range(MAX_REDIRECCIONES + 1):
        _revisar_robots(url)
        r = pedir(url, timeout=timeout, stream=stream, reintentos=reintentos, params=params)
        params = None  # los parametros solo van en el primer pedido
        if r.is_redirect:
            r.close()
            url = urljoin(url, r.headers["Location"])
            continue
        final = urlparse(url)
        dominio = final.netloc
        if RE_LOGIN.search(final.path):
            r.close()
            raise FalloRed(PORTADA_LOGIN, "redirige a una página de login")
        if final.path in ("", "/") and original.path not in ("", "/"):
            r.close()
            raise FalloRed(PORTADA_LOGIN, "redirige a la portada del sitio")
        if r.status_code in (401, 407):
            r.close()
            raise FalloRed(PORTADA_LOGIN, f"{dominio} pide iniciar sesión (HTTP {r.status_code})")
        if r.status_code == 402:
            r.close()
            raise FalloRed(CON_PAGO, f"{dominio} pide pago (HTTP 402)")
        if r.status_code >= 400:
            r.close()
            raise FalloRed(CAIDO, f"{dominio} no disponible (HTTP {r.status_code})")
        return url, r
    raise FalloRed(CAIDO, "demasiadas redirecciones")


def es_antibots(primeros_bytes: bytes) -> bool:
    """La pagina es un CAPTCHA o un control anti-bots (no se intenta saltar)."""
    return bool(RE_ANTIBOTS.search(primeros_bytes))
