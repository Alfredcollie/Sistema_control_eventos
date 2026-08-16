# -*- coding: utf-8 -*-
"""
APP_PATHS.PY - Gestor de Rutas Seguras
Garantiza que la configuración y los datos locales sobrevivan a las actualizaciones.
"""
import os
import sys

APP_NAME = "BlackCube"

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