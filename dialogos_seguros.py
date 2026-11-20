# -*- coding: utf-8 -*-
"""
DIALOGOS_SEGUROS.PY - Selectores de archivo a prueba de fallos (Windows / Mac / Linux).

⚠️ macOS: el diálogo nativo de Tkinter (tk_getOpenFile / tk_getSaveFile) puede CERRAR
la aplicación al abrirse (fallo conocido de Tk en macOS, sobre todo en apps
empaquetadas). Por eso en Mac se usa el selector nativo del sistema mediante
'osascript', que corre en un proceso independiente y NO puede tumbar la aplicación.
En Windows/Linux se mantiene el diálogo normal de Tkinter.

Uso:
    from dialogos_seguros import seleccionar_archivo_dialogo, guardar_archivo_dialogo

    ruta = seleccionar_archivo_dialogo("Seleccionar PDF", [("Archivos PDF", "*.pdf")])
    if ruta:
        ...

    destino = guardar_archivo_dialogo("Guardar reporte", ".xlsx", "Reporte.xlsx", [("Excel", "*.xlsx")])
    if destino:
        ...
"""
import os
import sys
import subprocess
from tkinter import filedialog, messagebox


def _extensiones_de(tipos):
    """Convierte [("desc", "*.pdf;*.png"), ...] en ['pdf', 'png'] (minúsculas, sin duplicados)."""
    extensiones = []
    for elemento in (tipos or []):
        try:
            _desc, patrones = elemento
        except Exception:
            continue
        if isinstance(patrones, str):
            patrones = patrones.replace(";", " ").split()
        for patron in patrones:
            # macOS espera extensiones en minúscula y sin duplicados (mayúsculas como
            # "PDF" las interpreta como identificador de tipo y el selector falla).
            ext = str(patron).replace("*", "").strip().lstrip(".").lower()
            if ext and ext not in extensiones:
                extensiones.append(ext)
    return extensiones


def _applescript_elegir_archivo(titulo, extensiones):
    """AppleScript del selector de archivos nativo de macOS."""
    titulo_limpio = str(titulo or "Seleccionar Documento").replace('"', "'")
    cmd = f'POSIX path of (choose file with prompt "{titulo_limpio}"'
    if extensiones:
        lista = "{" + ", ".join(f'"{e}"' for e in extensiones) + "}"
        cmd += f" of type {lista}"
    return cmd + ")"


def _applescript_guardar_archivo(titulo, nombre_sugerido):
    """AppleScript del selector de destino nativo de macOS."""
    titulo_limpio = str(titulo or "Guardar archivo").replace('"', "'")
    nombre_limpio = str(nombre_sugerido or "").replace('"', "'")
    cmd = f'POSIX path of (choose file name with prompt "{titulo_limpio}"'
    if nombre_limpio:
        cmd += f' default name "{nombre_limpio}"'
    return cmd + ")"


def _applescript_elegir_carpeta(titulo):
    """AppleScript del selector de carpetas nativo de macOS."""
    titulo_limpio = str(titulo or "Seleccionar Carpeta").replace('"', "'")
    return f'POSIX path of (choose folder with prompt "{titulo_limpio}")'


def _osascript(cmd):
    """Ejecuta el AppleScript. Devuelve (osascript_disponible, ruta_elegida)."""
    try:
        res = subprocess.run(["osascript", "-e", cmd], capture_output=True, text=True)
    except FileNotFoundError:
        return (False, "")          # Sin osascript: se usará el diálogo de Tkinter
    except Exception:
        return (True, "")           # Falló el selector: se trata como cancelado
    if res.returncode == 0:
        return (True, res.stdout.strip())
    return (True, "")               # El usuario canceló (-128)


def seleccionar_archivo_dialogo(titulo="Seleccionar Documento", tipos=None):
    """Pide elegir un archivo EXISTENTE. Devuelve la ruta, o "" si el usuario canceló."""
    tipos = tipos or [("Todos los archivos", "*.*")]
    if sys.platform == "darwin":
        disponible, ruta = _osascript(_applescript_elegir_archivo(titulo, _extensiones_de(tipos)))
        if disponible:
            return ruta
    return filedialog.askopenfilename(title=titulo, filetypes=tipos)


def seleccionar_archivos_dialogo(titulo="Seleccionar Documentos", tipos=None):
    """Pide elegir uno o VARIOS archivos existentes. Devuelve una lista (vacía si canceló)."""
    tipos = tipos or [("Todos los archivos", "*.*")]
    rutas = filedialog.askopenfilenames(title=titulo, filetypes=tipos)
    return [r for r in rutas if r] if rutas else []


def seleccionar_carpeta_dialogo(titulo="Seleccionar Carpeta"):
    """Pide elegir una CARPETA. Devuelve la ruta (sin barra final), o "" si canceló."""
    if sys.platform == "darwin":
        disponible, ruta = _osascript(_applescript_elegir_carpeta(titulo))
        if disponible:
            # macOS devuelve la carpeta con "/" final; se normaliza como Tkinter.
            return (ruta.rstrip("/") or "/") if ruta else ""
    return filedialog.askdirectory(title=titulo)


def guardar_archivo_dialogo(titulo="Guardar como", defaultextension="", initialfile="", tipos=None):
    """Pide una ruta de DESTINO. Devuelve la ruta, o "" si el usuario canceló.

    🔒 Respeta la política de almacenamiento: si este equipo no está autorizado
    (equipo secundario sin la cuenta Rclone del principal) no se permite guardar
    ningún archivo, en ningún módulo.
    """
    try:
        from politica_almacenamiento import exigir_permiso
        if not exigir_permiso():
            return ""
    except ImportError:
        pass
    tipos = tipos or [("Todos los archivos", "*.*")]
    if sys.platform == "darwin":
        nombre = initialfile or (f"archivo{defaultextension}" if defaultextension else "")
        disponible, ruta = _osascript(_applescript_guardar_archivo(titulo, nombre))
        if disponible:
            if not ruta:
                return ""
            if defaultextension and not os.path.splitext(ruta)[1]:
                ruta += defaultextension
            if os.path.exists(ruta):
                if not messagebox.askyesno("Confirmar", f"El archivo ya existe:\n{ruta}\n\n¿Deseas reemplazarlo?"):
                    return ""
            return ruta
    return filedialog.asksaveasfilename(
        title=titulo, defaultextension=defaultextension, initialfile=initialfile, filetypes=tipos
    )
