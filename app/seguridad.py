"""Contraseñas, sesiones y freno a los intentos de login fallidos."""
import hashlib
import secrets
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

ph = PasswordHasher()
# Hash de relleno para que un usuario inexistente tarde lo mismo que uno real.
_HASH_FALSO = ph.hash(secrets.token_hex(16))

DURACION_SESION = 30 * 24 * 3600


def hashear(clave: str) -> str:
    return ph.hash(clave)


def verificar(clave_hash: str | None, clave: str) -> bool:
    try:
        return ph.verify(clave_hash or _HASH_FALSO, clave) and clave_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def nuevo_token() -> tuple[str, bytes]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


class Freno:
    """Después de "maximo" intentos fallidos en 10 minutos, bloquea esa clave
    (usuario o IP) hasta que pasen los 10 minutos."""

    def __init__(self, maximo=5, ventana=600):
        self.maximo, self.ventana = maximo, ventana
        self.fallos = defaultdict(deque)

    def _limpiar(self, clave):
        q, limite = self.fallos[clave], time.monotonic() - self.ventana
        while q and q[0] < limite:
            q.popleft()
        return q

    def bloqueado(self, *claves) -> bool:
        return any(len(self._limpiar(c)) >= self.maximo for c in claves)

    def fallo(self, *claves):
        for c in claves:
            self._limpiar(c).append(time.monotonic())

    def limpiar(self, *claves):
        for c in claves:
            self.fallos.pop(c, None)
