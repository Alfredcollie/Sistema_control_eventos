# -*- coding: utf-8 -*-
"""
APP_PATHS.PY - Gestor de Rutas Seguras
Garantiza que la configuración y los datos locales sobrevivan a las actualizaciones.
"""
import os
import sys
import json
import uuid
from pathlib import Path

APP_NAME = "BlackCube"

# Directorio donde vive este archivo (la carpeta de la app), independiente del cwd.
APP_DIR = os.path.dirname(os.path.abspath(__file__))

def obtener_directorio_datos_usuario():
    """
    Obtiene la ruta segura del sistema operativo para guardar datos de usuario.
    Esta carpeta NO se borra cuando la aplicación se actualiza.
    """
    if sys.platform == "win32":
        # Windows: C:\Users\Usuario\AppData\Roaming\BlackCube
        base_dir = os.environ.get("APPDATA", os.path.expanduser("~"))
    elif sys.platform == "darwin":
        # Mac: /Users/Usuario/Library/Application Support/BlackCube
        base_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        # Linux / Otros: ~/.config/BlackCube
        base_dir = os.path.join(os.path.expanduser("~"), ".config")
    
    app_dir = os.path.join(base_dir, APP_NAME)
    
    # Crear la carpeta de forma silenciosa si no existe
    os.makedirs(app_dir, exist_ok=True)
    return app_dir

# --- RUTAS GLOBALES SEGURAS ---
CONFIG_DIR = obtener_directorio_datos_usuario()

# El archivo de configuración ahora vivirá seguro fuera de la app
CONFIG_FILE = os.path.join(CONFIG_DIR, "configuracion.json")

# --- Sincronización en la nube (Rclone) -----------------------------------
# DATA_DIR como Path: lo usan nube_archivos.py y politica_almacenamiento.py
DATA_DIR = Path(CONFIG_DIR)

# Identificador único y persistente de este equipo (para saber qué máquina
# realizó el enlace con Rclone). Vive en DATA_DIR, fuera de la configuración.
DEVICE_ID_FILE = DATA_DIR / "device_id.txt"


def obtener_device_id():
    """Devuelve (y crea si hace falta) un UUID único y persistente por equipo."""
    try:
        if DEVICE_ID_FILE.exists():
            valor = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
            if valor:
                return valor
    except Exception:
        pass
    nuevo = str(uuid.uuid4())
    try:
        DEVICE_ID_FILE.write_text(nuevo, encoding="utf-8")
    except Exception:
        pass
    return nuevo


def ruta_drive_configurada():
    """Carpeta local configurada en Configuración del Sistema (ruta_drive)."""
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                datos = json.load(f)
            if isinstance(datos, dict):
                return str(datos.get("ruta_drive", "") or "").strip()
    except Exception:
        pass
    return ""


def ruta_recurso(nombre_relativo):
    """Resuelve un recurso (logo, icono, json, etc.) sin depender del directorio de trabajo.
    Funciona tanto en desarrollo como empaquetado con PyInstaller (.exe / .app)."""
    try:
        base = sys._MEIPASS  # Carpeta temporal de PyInstaller
    except Exception:
        base = APP_DIR
    return os.path.join(base, nombre_relativo)


def cargar_config_local():
    """Lee la configuración local de forma segura (independiente del cwd).
    Prioriza CONFIG_FILE (datos del usuario) sobre 'config_local.json' (valores por defecto)."""
    config = {}
    # Valores por defecto (junto a la app / empaquetados) con menor prioridad.
    candidatos = [os.path.join(APP_DIR, "config_local.json")]
    try:
        candidatos.append(os.path.join(sys._MEIPASS, "config_local.json"))
    except Exception:
        pass
    # La configuración del usuario se aplica al final para que tenga prioridad.
    candidatos.append(CONFIG_FILE)
    for ruta in candidatos:
        try:
            if os.path.exists(ruta):
                with open(ruta, "r", encoding="utf-8") as f:
                    config.update(json.load(f))
        except Exception:
            continue
    return config