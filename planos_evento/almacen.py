# -*- coding: utf-8 -*-
"""Guarda el proyecto editable en JSON (nube) con escritura atómica y versiones."""
import json
import os
import shutil
import tempfile
from datetime import datetime

try:
    from app_paths import ruta_drive_configurada
except Exception:                                     # pragma: no cover
    def ruta_drive_configurada():
        return ""

SUBCARPETA = "Planos"
CARPETA_VERSIONES = "versiones"
MAX_VERSIONES = 30


_AVISO_CARPETA = [False]


def carpeta_local_configurada():
    """📁 La «carpeta local» de Configuración General (la que sincroniza rclone).

    Se lee SIEMPRE del archivo de configuración, así si se cambia la carpeta
    en Configuración General los planos pasan a guardarse en la nueva sin
    reiniciar el programa.
    """
    base = (ruta_drive_configurada() or "").strip()
    if not base:
        try:
            from app_paths import cargar_config_local
            datos = cargar_config_local() or {}
            base = str(datos.get("ruta_drive") or "").strip()
        except Exception:
            base = ""
    if not base:
        return ""
    return os.path.abspath(os.path.expanduser(base))


def carpeta_base():
    """Carpeta donde viven los planos: <carpeta local>/Planos (sincronizada)."""
    base = carpeta_local_configurada()
    if not base:
        # Igual que el resto del programa: se usa la carpeta de datos del usuario,
        # pero se avisa porque NO se sincroniza con Google Drive.
        if not _AVISO_CARPETA[0]:
            _AVISO_CARPETA[0] = True
            try:
                from app_paths import DATA_DIR
                base = str(DATA_DIR)
            except Exception:
                base = os.path.join(os.path.expanduser("~"), "BlackCube")
            print("[Planos] ⚠️  No hay «carpeta local» configurada en Configuración "
                  "General: los planos se guardan en %s (sin sincronizar)." % base)
    destino = os.path.join(base, SUBCARPETA)
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception as e:
        print("[Planos] ⚠️  No se pudo crear la carpeta %s: %s" % (destino, e))
    return destino


def carpeta_plano(id_plano):
    destino = os.path.join(carpeta_base(), str(id_plano))
    os.makedirs(destino, exist_ok=True)
    os.makedirs(os.path.join(destino, CARPETA_VERSIONES), exist_ok=True)
    return destino


def ruta_proyecto(id_plano):
    return os.path.join(carpeta_plano(id_plano), "proyecto.json")


def ruta_pdf_original(id_plano):
    return os.path.join(carpeta_plano(id_plano), "original.pdf")


def guardar(plano, guardar_version=False):
    """Escribe proyecto.json de forma atómica. Devuelve la ruta."""
    destino = ruta_proyecto(plano.id)
    plano.modificacion = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    datos = plano.a_dict()
    temporal = destino + ".tmp"
    with open(temporal, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
    os.replace(temporal, destino)          # atómico: nunca queda a medias
    if guardar_version:
        crear_version(plano)
        plano.version = int(plano.version or 1) + 1
        datos["version"] = plano.version
        with open(temporal, "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
        os.replace(temporal, destino)
    return destino


def cargar(id_plano):
    ruta = ruta_proyecto(id_plano)
    if not os.path.isfile(ruta):
        return None
    with open(ruta, "r", encoding="utf-8") as archivo:
        datos = json.load(archivo)
    from .modelo import Plano
    return Plano.desde_dict(datos)


def crear_version(plano):
    """Copia el proyecto como versión (protección ante cambios)."""
    try:
        origen = ruta_proyecto(plano.id)
        if not os.path.isfile(origen):
            return ""
        carpeta = os.path.join(carpeta_plano(plano.id), CARPETA_VERSIONES)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        # número de versión = el mayor existente + 1 (nunca se pisa una anterior)
        existentes = [n for n in os.listdir(carpeta) if n.startswith("v")]
        numeros = []
        for nombre in existentes:
            try:
                numeros.append(int(nombre.split("_")[0][1:]))
            except Exception:
                pass
        siguiente = (max(numeros) + 1) if numeros else 1
        destino = os.path.join(carpeta, "v%03d_%s.json" % (siguiente, marca))
        shutil.copy2(origen, destino)
        versiones = sorted(os.listdir(carpeta))
        for viejo in versiones[:-MAX_VERSIONES]:
            try:
                os.remove(os.path.join(carpeta, viejo))
            except Exception:
                pass
        return destino
    except Exception as e:
        print("[Planos] no se pudo crear la versión:", e)
        return ""


def listar_versiones(id_plano):
    carpeta = os.path.join(carpeta_plano(id_plano), CARPETA_VERSIONES)
    if not os.path.isdir(carpeta):
        return []
    return sorted(os.listdir(carpeta), reverse=True)


def restaurar_version(id_plano, nombre_version):
    """Devuelve el Plano guardado en esa versión (sin sobreescribir el actual)."""
    carpeta = os.path.join(carpeta_plano(id_plano), CARPETA_VERSIONES)
    ruta = os.path.join(carpeta, nombre_version)
    if not os.path.isfile(ruta):
        return None
    with open(ruta, "r", encoding="utf-8") as archivo:
        datos = json.load(archivo)
    from .modelo import Plano
    return Plano.desde_dict(datos)


def copiar_pdf_original(origen, id_plano):
    """Copia el PDF original a la carpeta del proyecto (nunca se altera)."""
    destino = ruta_pdf_original(id_plano)
    shutil.copy2(origen, destino)
    return destino
