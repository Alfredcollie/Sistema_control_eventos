# -*- coding: utf-8 -*-
"""
CIFRADO_RCLONE.PY - Cifrado del token de Rclone compartido en Supabase.

El token OAuth de Google Drive se comparte entre equipos a traves de la tabla
config_general de Supabase. Para NO guardarlo en texto plano, se cifra con
Fernet (AES-128-CBC + HMAC-SHA256, cifrado autenticado) usando una clave
derivada con PBKDF2-HMAC-SHA256 a partir de la contrasena de la base de datos
de Supabase.

Esa contrasena ya la posee cada equipo en su llavero (keyring) / variables de
entorno / config_db.json; por eso todos los equipos pueden derivar la MISMA
clave, pero esa clave NO se guarda en la base de datos.
"""
import base64

# Importación tolerante: si 'cryptography' no está instalada (p. ej. en un Mac
# que aún no corrió el instalador de dependencias), la app igual abre y el
# cifrado queda desactivado hasta que se instale la librería.
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO_DISPONIBLE = True
except Exception:
    _CRYPTO_DISPONIBLE = False
    Fernet = None
    hashes = None
    PBKDF2HMAC = None

_SAL = b"ControlFlota::rclone-token::v1"
_ITERACIONES = 600_000
_PREFIJO = "cf1:"

_clave_cache = None
_clave_cache_resuelta = False


def _clave_fernet():
    global _clave_cache, _clave_cache_resuelta
    if _clave_cache_resuelta:
        return _clave_cache
    if not _CRYPTO_DISPONIBLE:
        _clave_cache = None
        _clave_cache_resuelta = True
        return None

    try:
        from conexion import leer_credenciales
        cred = leer_credenciales()
        password = (cred.get("password") or "").encode("utf-8")
    except Exception:
        password = b""

    if password:
        try:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=_SAL,
                iterations=_ITERACIONES,
            )
            _clave_cache = base64.urlsafe_b64encode(kdf.derive(password))
        except Exception:
            _clave_cache = None
    else:
        _clave_cache = None

    _clave_cache_resuelta = True
    return _clave_cache


def cifrar_texto(texto):
    """Cifra un texto y devuelve su representacion ASCII (prefijo + base64)."""
    if not texto:
        return None
    clave = _clave_fernet()
    if not clave:
        return None
    try:
        token = Fernet(clave).encrypt(texto.encode("utf-8"))
        return _PREFIJO + token.decode("ascii")
    except Exception:
        return None


def descifrar_texto(cifrado):
    """Descifra un texto producido por cifrar_texto(). Devuelve None ante
    cualquier fallo (clave distinta, dato corrupto o manipulado)."""
    if not cifrado or not isinstance(cifrado, str) or not cifrado.startswith(_PREFIJO):
        return None
    clave = _clave_fernet()
    if not clave:
        return None
    try:
        return Fernet(clave).decrypt(cifrado[len(_PREFIJO):].encode("ascii")).decode("utf-8")
    except Exception:
        return None
