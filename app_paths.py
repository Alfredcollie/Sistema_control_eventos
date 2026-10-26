# -*- coding: utf-8 -*-
"""
APP_PATHS.PY - Rutas de configuración, recursos y RESOLUCIÓN PORTABLE de archivos.
- BASE_DIR / RESOURCES_DIR: carpeta del ejecutable (recursos de SOLO LECTURA).
- DATA_DIR / CONFIG_FILE: carpeta de datos del usuario (ESCRIBIBLE):
    Windows -> %APPDATA%\\BlackCube
    macOS   -> ~/Library/Application Support/BlackCube
    Linux   -> ~/.config/BlackCube
"""
import os
import sys
import json
import uuid
from pathlib import Path

APP_NAME = "BlackCube"


def _base_dir():
    """Carpeta base: la del .exe/.app si está compilado, o la del script."""
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable))
    return Path(__file__).resolve().parent


def _data_dir():
    """Carpeta de datos del usuario (escribible)."""
    if sys.platform == "win32":
        raiz = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    elif sys.platform == "darwin":
        raiz = Path.home() / "Library" / "Application Support"
    else:
        raiz = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    carpeta = raiz / APP_NAME
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return carpeta


BASE_DIR = _base_dir()
DATA_DIR = _data_dir()
RESOURCES_DIR = BASE_DIR

# Directorio donde vive este archivo (compatibilidad con el código existente).
APP_DIR = str(BASE_DIR)
CONFIG_DIR = str(DATA_DIR)

# Configuración del usuario (se conserva el nombre histórico del programa).
CONFIG_FILE = DATA_DIR / "configuracion.json"

# Identificador único y persistente de este equipo.
DEVICE_ID_FILE = DATA_DIR / "device_id.txt"


def obtener_directorio_datos_usuario():
    """Ruta segura del sistema operativo para los datos del usuario."""
    return str(DATA_DIR)


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


def ruta_recurso(nombre_relativo):
    """Resuelve un recurso (logo, icono, json, etc.) sin depender del cwd."""
    try:
        base = sys._MEIPASS  # Carpeta temporal de PyInstaller
    except Exception:
        base = str(BASE_DIR)
    return os.path.join(base, nombre_relativo)


def cargar_config_local():
    """Configuración local: valores por defecto (junto a la app) + los del usuario."""
    config = {}
    candidatos = [os.path.join(str(BASE_DIR), "config_local.json")]
    try:
        candidatos.append(os.path.join(sys._MEIPASS, "config_local.json"))
    except Exception:
        pass
    candidatos.append(str(CONFIG_FILE))   # el usuario tiene prioridad
    for ruta in candidatos:
        try:
            if not os.path.exists(ruta):
                continue
            with open(ruta, "r", encoding="utf-8") as f:
                datos = json.load(f)
            if not isinstance(datos, dict):
                continue
            for clave, valor in datos.items():
                # 🚫 Un valor VACÍO del usuario NO debe borrar el que trae el build
                if valor is None or (isinstance(valor, str) and not valor.strip()):
                    continue
                config[clave] = valor
        except Exception:
            continue
    return config


# =========================================================
# RESOLUCIÓN PORTABLE DE RUTAS DE ARCHIVOS
# =========================================================
# Subcarpetas que usa el programa para guardar documentos. Sirven para reconocer
# una ruta guardada (aunque venga de otro equipo o de otro sistema operativo) y
# volver a encontrarla en la carpeta local de ESTE equipo.
SUBCARPETAS_CONOCIDAS = (
    "facturas_recibidas", "facturas_emitidas", "comprobantes_egresos", "comprobantes_ingresos",
    "comprobantes_impuestos", "ordenes_generadas", "ordenes_anuladas", "ordenes_compra_recibidas",
    "cobranzas_generadas", "Inspecciones", "Inspecciones_PDF", "archivos_flota",
    "Cotizaciones", "soportes_pagos_terceros", "cartas_eventos", "formularios_proveedores",
    "Pautas_Eventos", "tarjetas_propiedad", "expedientes_choferes",
)


def ruta_drive_configurada():
    """Carpeta local configurada en Configuración del Sistema (ruta_drive)."""
    try:
        datos = cargar_config_local()
        if isinstance(datos, dict):
            return str(datos.get("ruta_drive", "") or "").strip()
    except Exception:
        pass
    return ""


def _normalizar_nombre(nombre):
    """Normaliza un nombre para compararlo (acentos NFC/NFD y mayúsculas)."""
    try:
        import unicodedata
        return unicodedata.normalize("NFC", str(nombre)).casefold()
    except Exception:
        return str(nombre).casefold()


def partir_ruta_guardada(ruta):
    """(subcarpeta, nombre_archivo) de una ruta guardada, sea Windows o macOS."""
    texto = str(ruta or "").replace("\\", "/").strip()
    if not texto:
        return "", ""
    partes = [p for p in texto.split("/") if p not in ("", ".")]
    if not partes:
        return "", ""
    nombre = partes[-1]
    subcarpeta = ""
    for i in range(len(partes) - 1, -1, -1):
        if partes[i] in SUBCARPETAS_CONOCIDAS:
            subcarpeta = "/".join(partes[i:-1])
            break
    return subcarpeta, nombre


def resolver_ruta_archivo(ruta_guardada, ruta_base=""):
    """Ruta REAL del archivo en este equipo, o "" si no se encuentra.

    1) si la ruta existe tal cual, se devuelve;
    2) si es relativa, se resuelve contra la carpeta local (ruta_drive);
    3) se reconstruye subcarpeta + nombre dentro de la carpeta local;
    4) se busca el nombre en las subcarpetas conocidas de la carpeta local.
    """
    if not ruta_guardada:
        return ""
    base = os.path.expanduser(str(ruta_base or "").strip()) or os.path.expanduser(ruta_drive_configurada())
    candidatas = []

    texto = str(ruta_guardada).replace("\\", os.sep).strip()
    if os.path.isabs(texto):
        candidatas.append(texto)
    elif base:
        candidatas.append(os.path.join(base, texto))

    subcarpeta, nombre = partir_ruta_guardada(ruta_guardada)
    if base and nombre:
        if subcarpeta:
            candidatas.append(os.path.join(base, *subcarpeta.split("/"), nombre))
        candidatas.append(os.path.join(base, nombre))
        for conocida in SUBCARPETAS_CONOCIDAS:
            candidatas.append(os.path.join(base, conocida, nombre))
    if nombre:
        candidatas.append(os.path.join(str(BASE_DIR), nombre))

    for candidata in candidatas:
        try:
            if candidata and os.path.exists(candidata):
                return os.path.normpath(candidata)
        except Exception:
            continue

    objetivo = _normalizar_nombre(nombre) if nombre else ""
    if objetivo:
        carpetas = []
        if base:
            carpetas.append(base)
            if subcarpeta:
                carpetas.append(os.path.join(base, *subcarpeta.split("/")))
            carpetas.extend(os.path.join(base, conocida) for conocida in SUBCARPETAS_CONOCIDAS)
        for carpeta in carpetas:
            try:
                if not os.path.isdir(carpeta):
                    continue
                for entrada in os.listdir(carpeta):
                    if _normalizar_nombre(entrada) == objetivo:
                        ruta = os.path.join(carpeta, entrada)
                        if os.path.isfile(ruta):
                            return os.path.normpath(ruta)
            except Exception:
                continue
    return ""


def ruta_para_guardar(ruta, ruta_base=""):
    """Ruta que se debe GUARDAR en la base de datos.

    Si el archivo está dentro de la carpeta local sincronizada (ruta_drive) se
    guarda RELATIVA (por ejemplo 'facturas_emitidas/archivo.pdf'): así la misma
    fila sirve en Windows y en Mac. Fuera de esa carpeta se guarda absoluta.
    """
    if not ruta:
        return ruta
    try:
        base = os.path.expanduser(str(ruta_base or "").strip()) or os.path.expanduser(ruta_drive_configurada())
        if not base:
            return ruta
        ruta_abs = os.path.abspath(os.path.expanduser(str(ruta)))
        base_abs = os.path.abspath(base)
        if os.path.commonpath([ruta_abs, base_abs]) != base_abs:
            return ruta
        relativa = os.path.relpath(ruta_abs, base_abs)
        if relativa.startswith(".."):
            return ruta
        return relativa.replace(os.sep, "/")
    except Exception:
        return ruta


def eliminar_archivo(ruta_guardada, ruta_base=""):
    """Elimina el archivo resolviendo la ruta (aunque se guardó en otro equipo/SO)."""
    ruta = resolver_ruta_archivo(ruta_guardada, ruta_base)
    if not ruta:
        return False
    try:
        os.remove(ruta)
        return True
    except Exception:
        return False
