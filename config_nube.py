# -*- coding: utf-8 -*-
"""
CONFIG_NUBE.PY - Registro de sincronizacion en la nube (Supabase).
Guarda en la tabla 'config_general' la configuracion de Rclone compartida
entre todos los equipos, y el token (cifrado) del rclone.conf.
"""
import json
from conexion import conectar_db, liberar_conexion
from cifrado_rclone import cifrar_texto, descifrar_texto

CLAVE_RCLONE = "rclone_sync"
CLAVE_RCLONE_TOKEN = "rclone_token"

_TABLA_ASEGURADA = False


def _asegurar_tabla(cursor):
    """Crea la tabla si no existe (una sola vez por sesion)."""
    global _TABLA_ASEGURADA
    if _TABLA_ASEGURADA:
        return
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS config_general "
        "(clave VARCHAR(255) PRIMARY KEY, valor TEXT)"
    )
    _TABLA_ASEGURADA = True


def _cargar_clave(clave, default=""):
    conn = conectar_db(silencioso=True)
    if not conn:
        return default
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            conn.commit()          # persiste la tabla si se acaba de crear
            cursor.execute("SELECT valor FROM config_general WHERE clave = %s", (clave,))
            fila = cursor.fetchone()
            if fila and fila[0] is not None:
                return fila[0]
    except Exception:
        pass
    finally:
        liberar_conexion(conn)
    return default


def _guardar_clave(clave, valor):
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute(
                "INSERT INTO config_general (clave, valor) VALUES (%s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                (clave, valor),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)


def clave_existe_en_nube(clave):
    """True si la clave ya existe en la tabla config_general de Supabase."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            conn.commit()          # persiste la tabla si se acaba de crear
            cursor.execute("SELECT 1 FROM config_general WHERE clave = %s", (clave,))
            return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        liberar_conexion(conn)


def cargar_rclone_sync():
    """Devuelve el dict de sincronizacion en la nube registrado en Supabase,
    o None si todavia ningun equipo lo registro."""
    raw = _cargar_clave(CLAVE_RCLONE, "")
    if not raw:
        return None
    try:
        datos = json.loads(raw)
        if isinstance(datos, dict):
            return datos
    except Exception:
        pass
    return None


def registrar_rclone_sync(datos):
    """Registra la configuracion SOLO si aun no existe (first-write-wins)."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute(
                "INSERT INTO config_general (clave, valor) VALUES (%s, %s) "
                "ON CONFLICT (clave) DO NOTHING",
                (CLAVE_RCLONE, json.dumps(datos or {}, ensure_ascii=False)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        liberar_conexion(conn)
    return cargar_rclone_sync()


def sobrescribir_rclone_sync(datos):
    """Reemplaza el registro de sincronización a propósito (sin 'first-write-wins').

    Lo usa el equipo que TOMA EL CONTROL como PRINCIPAL (por ejemplo, al pasar
    el mando del Windows a un Mac).
    """
    conn = conectar_db(silencioso=True)
    if not conn:
        return None
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute(
                "INSERT INTO config_general (clave, valor) VALUES (%s, %s) "
                "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                (CLAVE_RCLONE, json.dumps(datos or {}, ensure_ascii=False)),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        liberar_conexion(conn)
    return cargar_rclone_sync()


def actualizar_cuenta_remotos(cuenta_remotos, device_id=""):
    """Guarda la IDENTIDAD de las cuentas Rclone (remote -> huella estable).
    Solo puede cambiarla el equipo PROPIETARIO del registro."""
    if not isinstance(cuenta_remotos, dict) or not cuenta_remotos:
        return False
    registro = cargar_rclone_sync()
    if not registro:
        return False
    propietario = registro.get("linked_by_device") or ""
    if propietario and device_id and propietario != device_id:
        return False

    registro["cuenta_remotos"] = cuenta_remotos
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute(
                "UPDATE config_general SET valor = %s WHERE clave = %s",
                (json.dumps(registro, ensure_ascii=False), CLAVE_RCLONE),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)


def borrar_rclone_sync():
    """Borra el registro de sincronizacion en la nube."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute("DELETE FROM config_general WHERE clave = %s", (CLAVE_RCLONE,))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)


def guardar_rclone_token(token_texto):
    """Cifra el contenido de rclone.conf y lo guarda en Supabase."""
    if not token_texto or not token_texto.strip():
        return False
    cifrado = cifrar_texto(token_texto)
    if not cifrado:
        return False
    return _guardar_clave(CLAVE_RCLONE_TOKEN, cifrado)


def obtener_rclone_token():
    """Descarga y descifra el token de Rclone compartido (o '' si no hay/falla)."""
    cifrado = _cargar_clave(CLAVE_RCLONE_TOKEN, "")
    if not cifrado:
        return ""
    return descifrar_texto(cifrado) or ""


def borrar_rclone_token():
    """Elimina el token de Rclone compartido de Supabase."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            _asegurar_tabla(cursor)
            cursor.execute("DELETE FROM config_general WHERE clave = %s", (CLAVE_RCLONE_TOKEN,))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)
