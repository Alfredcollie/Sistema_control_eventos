# -*- coding: utf-8 -*-
"""
CONEXION.PY (v3 SEGURA + OPTIMIZADA)
- Credenciales de Supabase en llavero del sistema (keyring).
- Pool de Conexiones Persistente (ThreadedConnectionPool).
- Auditoría Asíncrona (Background Threading).
"""
import logging
import psycopg2
from psycopg2 import pool
import keyring
import threading
from datetime import datetime

SERVICE_NAME = "ControlEventos"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] conexion_supabase: %(message)s"
)

# Variable global para el Pool de conexiones
_connection_pool = None


def leer_credenciales():
    """Lee las credenciales del llavero del sistema."""
    return {
        "host": keyring.get_password(SERVICE_NAME, "SUPABASE_DB_HOST"),
        "port": keyring.get_password(SERVICE_NAME, "SUPABASE_DB_PORT") or "5432",
        "dbname": keyring.get_password(SERVICE_NAME, "SUPABASE_DB_NAME") or "postgres",
        "user": keyring.get_password(SERVICE_NAME, "SUPABASE_DB_USER"),
        "password": keyring.get_password(SERVICE_NAME, "SUPABASE_DB_PASSWORD"),
    }


def inicializar_pool(silencioso=False):
    """Inicializa el pool de conexiones persistentes leyendo del llavero."""
    global _connection_pool
    try:
        if _connection_pool is None:
            cred = leer_credenciales()
            if not cred["host"] or not cred["user"] or not cred["password"]:
                if not silencioso:
                    logging.warning("No hay credenciales en el llavero. Ejecuta: python configurar_credenciales.py")
                return
            
            _connection_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=15,
                host=cred["host"],
                port=int(cred["port"]),
                database=cred["dbname"],
                user=cred["user"],
                password=cred["password"],
                connect_timeout=10
            )
    except Exception as e:
        if not silencioso:
            logging.error(f"Error inicializando el Pool de Conexiones: {e}")


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
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bitacora_auditoria (fecha, hora, usuario, modulo, accion) VALUES (%s, %s, %s, %s, %s)",
            (ahora.strftime("%d/%m/%Y"), ahora.strftime("%H:%M:%S"), usuario, modulo, accion)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Error en auditoría asíncrona: {e}")
    finally:
        # IMPORTANTE: Liberamos la conexión en lugar de cerrarla
        liberar_conexion(conn)


def registrar_auditoria(usuario, modulo, accion):
    """Registra una acción en la bitácora sin congelar la interfaz (Asíncrono)."""
    if usuario in ["Desconocido", "Invitado", None]:
        return
    
    # Lanzamos la escritura a la base de datos en un "hilo" paralelo (Daemon)
    hilo = threading.Thread(
        target=_tarea_auditoria_asincrona, 
        args=(usuario, modulo, accion),
        daemon=True
    )
    hilo.start()


if __name__ == "__main__":
    c = conectar_db()
    if c:
        print("✅ Conexión (Pool) correcta leyendo desde el llavero del sistema.")
        liberar_conexion(c)
    else:
        print("❌ Sin conexión. Ejecuta primero: python configurar_credenciales.py")