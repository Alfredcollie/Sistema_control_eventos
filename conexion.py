# -*- coding: utf-8 -*-
"""
CONEXION.PY (v3 SEGURA + OPTIMIZADA + BLINDADA MAC)
- Credenciales de Supabase en llavero del sistema (keyring) o variables de entorno.
- Pool de Conexiones Persistente (ThreadedConnectionPool).
- Auditoría Asíncrona (Background Threading).
"""
import logging
import psycopg2
from psycopg2 import pool
import threading
from datetime import datetime
import sys
import os

# keyring es opcional: si no está instalado, se usan las variables de entorno.
try:
    import keyring
except ImportError:
    keyring = None

SERVICE_NAME = "ControlEventos"

# Valores por defecto (no secretos). Usuario y contraseña NUNCA se hardcodean:
# se leen del llavero del sistema (keyring) o de variables de entorno.
DB_HOST_DEFAULT = "aws-1-us-west-2.pooler.supabase.com"
DB_PORT_DEFAULT = "6543"
DB_NAME_DEFAULT = "postgres"

# Config empaquetado (config_local.json) como último recurso de credenciales.
try:
    from app_paths import cargar_config_local
except Exception:
    cargar_config_local = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] conexion_supabase: %(message)s"
)

# Registrar también en un archivo (útil en apps empaquetadas sin consola,
# por ejemplo macOS). El log queda en la carpeta de datos del usuario.
try:
    from app_paths import obtener_directorio_datos_usuario
    _log_dir = obtener_directorio_datos_usuario()
    _fh = logging.FileHandler(os.path.join(_log_dir, "conexion_supabase.log"), encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass

# Variable global para el Pool de conexiones
_connection_pool = None


def leer_credenciales():
    """Lee credenciales con prioridad:
    1) llavero del sistema (keyring) -> 2) variables de entorno -> 3) config empaquetado.
    No se hardcodea usuario ni contraseña.
    """
    # Base: config_local.json empaquetado (defaults del build)
    config = {}
    if cargar_config_local is not None:
        try:
            config = cargar_config_local()
        except Exception:
            config = {}

    host = config.get("supabase_db_host")
    port = config.get("supabase_db_port") or DB_PORT_DEFAULT
    dbname = config.get("supabase_db_name") or DB_NAME_DEFAULT
    user = config.get("supabase_db_user")
    password = config.get("supabase_db_password")

    # Variables de entorno tienen prioridad sobre el config empaquetado
    host = os.environ.get("SUPABASE_DB_HOST") or host
    port = os.environ.get("SUPABASE_DB_PORT") or port
    dbname = os.environ.get("SUPABASE_DB_NAME") or dbname
    user = os.environ.get("SUPABASE_DB_USER") or user
    password = os.environ.get("SUPABASE_DB_PASSWORD") or password

    # El llavero del sistema tiene la máxima prioridad
    if keyring is not None:
        try:
            k_host = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_HOST")
            if k_host:
                host = k_host
                port = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_PORT") or port
                dbname = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_NAME") or dbname
                user = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_USER")
                password = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_PASSWORD")
        except Exception as e:
            logging.error(f"Error al leer keyring: {e}")

    return {
        "host": host or DB_HOST_DEFAULT,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
    }


def inicializar_pool(silencioso=False):
    """Inicializa el pool de conexiones persistentes leyendo del llavero."""
    global _connection_pool
    try:
        if _connection_pool is None:
            cred = leer_credenciales()
            if not cred["host"] or not cred["user"] or not cred["password"]:
                logging.warning(
                    "No hay credenciales válidas. host=%r user=%r pwd_len=%d",
                    cred["host"], cred["user"], len(cred["password"] or ""),
                )
                return
            
            logging.info(
                "Conectando a host=%r port=%r db=%r user=%r pwd_len=%d",
                cred["host"], cred["port"], cred["dbname"], cred["user"], len(cred["password"] or ""),
            )
            _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=15,
                host=cred["host"],
                port=int(cred["port"]),
                database=cred["dbname"],
                user=cred["user"],
                password=cred["password"],
                sslmode="require",
                connect_timeout=10
            )
    except Exception as e:
        logging.error(f"Error inicializando el Pool de Conexiones: {type(e).__name__}: {e}")


def conectar_db(silencioso=False):
    """Obtiene una conexión pre-creada del Pool en lugar de crear una nueva."""
    global _connection_pool
    if _connection_pool is None:
        inicializar_pool(silencioso)
        
    try:
        if _connection_pool:
            return _connection_pool.getconn()
    except Exception as e:
        if not silencioso:
            logging.error(f"Error al obtener conexión del pool: {e}")
    return None


def liberar_conexion(conn):
    """Devuelve la conexión al pool para que sea reutilizada por otro proceso."""
    global _connection_pool
    if _connection_pool and conn:
        try:
            _connection_pool.putconn(conn)
        except Exception:
            pass


def _tarea_auditoria_asincrona(usuario, modulo, accion):
    """Función interna que se ejecuta en un hilo separado (Background)."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return
    try:
        ahora = datetime.now()
        marca_tiempo = ahora.strftime("%d/%m/%Y %H:%M:%S")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bitacora_auditoria (fecha, hora, usuario, modulo, accion) VALUES (%s, %s, %s, %s, %s)",
            (marca_tiempo.split(" ")[0], marca_tiempo.split(" ")[1], usuario, modulo, accion)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Error en auditoría asíncrona: {e}")
    finally:
        liberar_conexion(conn)


def registrar_auditoria(usuario, modulo, accion):
    """Registra una acción en la bitácora sin congelar la interfaz (Asíncrono)."""
    if usuario in ["Desconocido", "Invitado", None]:
        return
    
    hilo = threading.Thread(
        target=_tarea_auditoria_asincrona, 
        args=(usuario, modulo, accion),
        daemon=True
    )
    hilo.start()


if __name__ == "__main__":
    c = conectar_db()
    if c:
        print("✅ Conexión (Pool) correcta a la base de datos.")
        liberar_conexion(c)
    else:
        print("❌ Sin conexión a la base de datos.")