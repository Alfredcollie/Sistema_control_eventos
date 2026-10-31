# -*- coding: utf-8 -*-
"""Apertura de documentos (PDF e imágenes) con el programa EXTERNO del sistema.

El visor interno se retiró porque daba problemas en Windows y macOS. Ahora
todos los módulos abren el archivo con la aplicación del sistema (Adobe,
Vista Previa, Fotos…) mediante ver_documento_ventana(), que conserva el mismo
nombre y los mismos parámetros para no tocar los módulos.

Windows: os.startfile · macOS: open · Linux: xdg-open
"""
import os
import sys
import subprocess
import datetime

NL = chr(10)
_REGISTRO = {"ruta": ""}


def _log(mensaje):
    """Anota la apertura en visor.log (sirve para diagnosticar)."""
    try:
        if not _REGISTRO["ruta"]:
            try:
                from app_paths import DATA_DIR
                carpeta = str(DATA_DIR)
            except Exception:
                carpeta = os.path.dirname(os.path.abspath(__file__))
            _REGISTRO["ruta"] = os.path.join(carpeta, "visor.log")
        with open(_REGISTRO["ruta"], "a", encoding="utf-8") as archivo:
            archivo.write("%s | %s%s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                         str(mensaje)[:400], NL))
    except Exception:
        pass


class _DocumentoAbierto:
    """Objeto inofensivo que se devuelve a quien use el resultado.

    El documento ya se abrió con el programa externo; esto solo evita errores
    en los módulos que hacían ventana.bind(...), ventana.destroy(), etc.
    """

    def __init__(self, ruta=""):
        self.ruta = ruta

    def winfo_exists(self):
        return True

    def title(self, *_a, **_k):
        return os.path.basename(str(self.ruta or ""))

    def bind(self, *_a, **_k):
        return None

    def unbind(self, *_a, **_k):
        return None

    def after(self, *_a, **_k):
        return None

    def destroy(self):
        return None

    def lift(self):
        return None

    def deiconify(self):
        return None

    def focus_force(self):
        return None

    def grab_set(self):
        return None

    def grab_release(self):
        return None

    def transient(self, *_a, **_k):
        return None

    def geometry(self, *_a, **_k):
        return None

    def attributes(self, *_a, **_k):
        return None

    def update_idletasks(self):
        return None

    def update(self):
        return None

    def wait_visibility(self):
        return None

    def mainloop(self):
        return None

    def configure(self, *_a, **_k):
        return None

    def pack(self, *_a, **_k):
        return None

    def grid(self, *_a, **_k):
        return None


def abrir_aparte(ruta):
    """Abre el archivo con el programa externo del sistema."""
    try:
        if sys.platform == "win32":
            os.startfile(str(ruta))          # noqa: S606 (solo Windows)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print("Aviso - abriendo aparte:", e)
        _log("ERROR abriendo aparte: %s" % e)
        return False


def resolver(ruta):
    """Devuelve la ruta real del archivo (relativa, de otro equipo, de la nube…)."""
    try:
        from app_paths import resolver_ruta_archivo
        hallada = resolver_ruta_archivo(ruta)
        if hallada:
            return hallada
    except Exception:
        pass
    return str(ruta or "")


def ver_documento_ventana(ruta, titulo="Documento", parent=None, avisar=None):
    """Abre el PDF o la imagen con el programa EXTERNO del sistema."""
    real = resolver(ruta)
    if not real or not os.path.exists(real):
        mensaje = ("El archivo no está en este equipo:" + NL + NL + str(ruta) + NL + NL +
                   "Vuelva a cargarlo desde el módulo.")
        if avisar is not None:
            try:
                avisar("Documento no encontrado", mensaje)
                return None
            except Exception:
                pass
        try:
            from tkinter import messagebox
            messagebox.showerror("Documento no encontrado", mensaje, parent=parent)
        except Exception:
            print(mensaje)
        _log("ERROR archivo inexistente: %s" % real)
        return None

    _log("abrir externo: %s" % real)
    abrir_aparte(real)
    return _DocumentoAbierto(real)
