# -*- coding: utf-8 -*-
"""
Rutas absolutas de la aplicación.
Funciona igual en desarrollo, en macOS y compilado con PyInstaller.
"""

import sys
from pathlib import Path


def carpeta_aplicacion():
    """
    Devuelve la carpeta donde está el programa:
    - Si está compilado (.exe / .app): la carpeta del ejecutable.
    - Si está en desarrollo: la carpeta del proyecto.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


# Ruta absoluta del archivo de configuración local
CONFIG_FILE = carpeta_aplicacion() / "config_local.json"