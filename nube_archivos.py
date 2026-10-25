# -*- coding: utf-8 -*-
r"""
NUBE_ARCHIVOS.PY — Borrado de archivos TAMBIÉN en la carpeta sincronizada
=========================================================================
PROBLEMA QUE RESUELVE
---------------------
La sincronización del programa es bilateral y usa 'rclone copy', que NUNCA
borra nada en el destino:

    rclone copy  <nube>  <local>  --update     # nube  -> local  (RESTAURA)
    rclone copy  <local> <nube>   --update     # local -> nube

Por eso, cuando un módulo borra una carpeta o un archivo SOLO del disco, en el
siguiente ciclo de sincronización (cada 10 minutos, y al abrir el programa) la
copia de la nube lo vuelve a traer. Resultado: el registro desaparece de la base
de datos, pero el archivo "reaparece" como por arte de magia.

Con 'borrar_en_nube(ruta_local)' el borrado es real en TODO el sistema (local,
nube y base de datos). Los archivos NO se destruyen: van a la PAPELERA de
Google Drive, así que se pueden recuperar si fue un error.

USO
---
    from nube_archivos import borrar_en_nube
    ok, mensaje = borrar_en_nube(r"C:\...\BlackRider_Archivos\Inspecciones\ABC-123_5")
"""
import os
import shutil
import subprocess
import sys

from app_paths import CONFIG_FILE, DATA_DIR

__all__ = ["borrar_en_nube", "nube_configurada"]


def _leer_config():
    """Lee la configuración general (carpeta local, remote y carpeta de la nube)."""
    config = {}
    try:
        import json
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config = json.load(f) or {}
    except Exception:
        config = {}
    return config


def obtener_comando_rclone():
    """Detecta rclone en Windows, macOS (Intel/Apple Silicon) y Linux."""
    nombre = "rclone.exe" if sys.platform == "win32" else "rclone"
    try:
        if hasattr(sys, "_MEIPASS"):
            ruta_bundle = os.path.join(sys._MEIPASS, nombre)
            if os.path.exists(ruta_bundle):
                return ruta_bundle
    except Exception:
        pass
    ruta_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre)
    if os.path.exists(ruta_local):
        return ruta_local
    en_path = shutil.which("rclone")
    if en_path:
        return en_path
    if sys.platform != "win32":
        for ruta in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone",
                     "/opt/local/bin/rclone", os.path.expanduser("~/.local/bin/rclone"),
                     "/usr/bin/rclone"):
            if os.path.exists(ruta):
                return ruta
    return "rclone"


def _argumentos_config_rclone():
    """Flags para que rclone use el mismo rclone.conf que el resto del programa."""
    try:
        ruta = DATA_DIR / "rclone.conf"
        if ruta.exists():
            return ["--config", str(ruta)]
    except Exception:
        pass
    return []


def _datos_nube():
    """(raiz_local, ruta_remota) de la carpeta sincronizada, o (None, None)."""
    config = _leer_config()
    local = str(config.get("ruta_drive", "") or "").strip()
    if local:
        local = os.path.normpath(os.path.expanduser(local))
    remote = str(config.get("rclone_remote", "") or "").strip()
    nube = str(config.get("rclone_ruta_nube", "") or "").strip()
    if not local or not remote or not nube:
        return None, None
    ruta_remota = f"{remote}{nube}" if remote.endswith(":") else f"{remote}:{nube}"
    return local, ruta_remota


def nube_configurada():
    local, remota = _datos_nube()
    return bool(local and remota)


def borrar_en_nube(ruta_local, raiz_local=None, timeout=300):
    """Borra en la carpeta de la nube la copia de 'ruta_local'.

    Devuelve (ok, mensaje). Si la carpeta de la nube no está configurada o la
    copia no existe, devuelve (True, ...) porque no hay nada que evitar que
    reaparezca.
    """
    ruta_local = os.path.normpath(str(ruta_local or ""))
    if not ruta_local:
        return True, "sin ruta"

    raiz, ruta_remota = _datos_nube()
    raiz = os.path.normpath(raiz_local) if raiz_local else raiz
    if not raiz or not ruta_remota:
        return True, "la nube no está configurada (no hay nada que borrar allí)"

    try:
        relativo = os.path.relpath(ruta_local, raiz)
    except Exception:
        return False, "la ruta no pertenece a la carpeta sincronizada"
    if relativo.startswith("..") or relativo.strip("\\/.") == "":
        return False, "la ruta no pertenece a la carpeta sincronizada"

    destino = ruta_remota.rstrip("/") + "/" + relativo.replace(os.sep, "/").strip("/")

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000          # sin ventana de consola

    cmd = obtener_comando_rclone()
    # Si el equipo tiene filtros globales por variable de entorno (RCLONE_EXCLUDE,
    # RCLONE_FILTER, ...) hay que ignorarlos: rclone no permite 'purge' con filtros.
    entorno = {k: v for k, v in os.environ.items()
               if k.upper() not in ("RCLONE_EXCLUDE", "RCLONE_EXCLUDE_FROM", "RCLONE_FILTER",
                                    "RCLONE_FILTER_FROM", "RCLONE_INCLUDE", "RCLONE_INCLUDE_FROM")}
    try:
        # 'purge' borra la carpeta indicada y su contenido. Sin --drive-use-trash=false
        # los archivos van a la PAPELERA de Google Drive (se pueden recuperar).
        res = subprocess.run([cmd, *_argumentos_config_rclone(), "purge", destino, "--quiet"],
                             capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout, env=entorno, **kwargs)
    except FileNotFoundError:
        return False, "no se encontró rclone en este equipo"
    except subprocess.TimeoutExpired:
        return False, "la nube tardó demasiado en responder"
    except Exception as e:
        return False, str(e)

    if res.returncode == 0:
        return True, "borrado en la nube"
    detalle = (res.stderr or res.stdout or "").strip().splitlines()
    detalle = detalle[-1] if detalle else ""
    # Si ya no existía allí, no hay nada que hacer
    if "not found" in detalle.lower() or "directory not found" in detalle.lower():
        return True, "la copia ya no estaba en la nube"
    return False, detalle or "rclone respondió con error"
