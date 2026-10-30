# -*- coding: utf-8 -*-
"""
MOVER_CARPETA_ARCHIVOS.PY - Traslada TODA la carpeta de archivos a otra ubicación.

Se usa cuando en Configuración General se cambia la "carpeta local de archivos":
antes de guardar el cambio, el programa mueve todas las carpetas y archivos a la
nueva ubicación y solo entonces guarda (y se reinicia).

Reglas de seguridad:
  * Nunca borra el origen por su cuenta: solo lo vacía moviendo su contenido.
  * Si en el destino ya existe un archivo con el mismo nombre:
      - mismo tamaño  -> se considera la misma copia y se descarta la del origen;
      - distinto      -> el que llega se guarda como "nombre (2).ext".
  * La carpeta nueva NO puede estar dentro de la actual.
  * Los archivos basura (desktop.ini, .DS_Store, Thumbs.db, temporales) se omiten.
"""
import os
import shutil

BASURA = {".ds_store", "thumbs.db", "desktop.ini", "icon\r"}


def _es_basura(nombre):
    bajo = nombre.lower()
    return bajo in BASURA or bajo.endswith((".tmp", ".partial")) or nombre.startswith("~$")


def misma_unidad(origen, destino):
    """True si las dos rutas están en el mismo disco (mover es instantáneo)."""
    try:
        a = os.path.splitdrive(os.path.abspath(origen))[0].lower()
        b = os.path.splitdrive(os.path.abspath(destino))[0].lower()
        return a == b
    except Exception:
        return True


def contar(origen):
    """(archivos, carpetas) que hay dentro de una carpeta."""
    archivos = carpetas = 0
    for _raiz, dirs, files in os.walk(origen):
        carpetas += len(dirs)
        archivos += len(files)
    return archivos, carpetas


def _nombre_libre(ruta):
    """Si la ruta existe, devuelve 'base (2).ext', 'base (3).ext'..."""
    if not os.path.exists(ruta):
        return ruta
    base, ext = os.path.splitext(ruta)
    n = 2
    while os.path.exists("%s (%d)%s" % (base, n, ext)):
        n += 1
    return "%s (%d)%s" % (base, n, ext)


def mover_contenido(origen, destino, al_progreso=None, max_errores=15):
    """Mueve el contenido de `origen` a `destino`.

    `al_progreso(hechos, total, archivo_actual)` se llama cada pocos archivos.

    Devuelve un dict:
        {"ok": bool, "archivos": int, "carpetas": int,
         "errores": [str...], "detalle": str}
    """
    resultado = {"ok": False, "archivos": 0, "carpetas": 0, "errores": [], "detalle": ""}
    origen = os.path.abspath(str(origen or "")).rstrip("\\/")
    destino = os.path.abspath(str(destino or "")).rstrip("\\/")

    if not origen or not destino:
        resultado["detalle"] = "Faltan la carpeta actual o la nueva."
        return resultado
    if os.path.normcase(origen) == os.path.normcase(destino):
        resultado.update(ok=True, detalle="La carpeta es la misma: no hay nada que mover.")
        return resultado
    if os.path.normcase(destino).startswith(os.path.normcase(origen) + os.sep):
        resultado["detalle"] = ("La carpeta nueva no puede estar dentro de la carpeta actual.\n"
                                "Elija otra ubicación (por ejemplo, una carpeta hermana).")
        return resultado
    if not os.path.isdir(origen):
        try:
            os.makedirs(destino, exist_ok=True)
            resultado.update(ok=True, detalle="La carpeta anterior ya no existía; se creó la nueva.")
        except Exception as e:
            resultado["detalle"] = "No se pudo crear la carpeta nueva: %s" % e
        return resultado

    try:
        os.makedirs(destino, exist_ok=True)
    except Exception as e:
        resultado["detalle"] = "No se pudo crear la carpeta nueva: %s" % e
        return resultado

    total, _carpetas = contar(origen)
    hechos = 0
    errores = resultado["errores"]

    for raiz, dirs, archivos in os.walk(origen):
        dirs[:] = [d for d in dirs if not _es_basura(d)]
        relativo = os.path.relpath(raiz, origen)
        destino_raiz = destino if relativo == "." else os.path.join(destino, relativo)
        try:
            os.makedirs(destino_raiz, exist_ok=True)
        except Exception as e:
            errores.append("No se pudo crear %s: %s" % (destino_raiz, e))
            continue

        for nombre in archivos:
            if _es_basura(nombre):
                continue
            origen_archivo = os.path.join(raiz, nombre)
            destino_archivo = os.path.join(destino_raiz, nombre)
            try:
                if os.path.exists(destino_archivo):
                    try:
                        igual = os.path.getsize(origen_archivo) == os.path.getsize(destino_archivo)
                    except OSError:
                        igual = False
                    if igual:
                        os.remove(origen_archivo)      # ya está en el destino
                    else:
                        destino_archivo = _nombre_libre(destino_archivo)
                        shutil.move(origen_archivo, destino_archivo)
                else:
                    shutil.move(origen_archivo, destino_archivo)
                resultado["archivos"] += 1
                hechos += 1
                if al_progreso and (total <= 50 or hechos % 25 == 0 or hechos >= total):
                    al_progreso(hechos, total, nombre)
            except Exception as e:
                if len(errores) < max_errores:
                    errores.append("%s: %s" % (nombre, e))

    # Los archivos basura que quedaron (desktop.ini, Thumbs.db…) se descartan y
    # después se borran las carpetas que quedaron vacías en el origen.
    for raiz, _dirs, archivos in os.walk(origen):
        for nombre in archivos:
            if _es_basura(nombre):
                try:
                    os.remove(os.path.join(raiz, nombre))
                except OSError:
                    pass

    for raiz, dirs, archivos in os.walk(origen, topdown=False):
        if os.path.normcase(raiz) == os.path.normcase(origen):
            continue
        try:
            if not os.listdir(raiz):
                os.rmdir(raiz)
                resultado["carpetas"] += 1
        except OSError:
            pass
    try:
        if not os.listdir(origen):
            os.rmdir(origen)
    except OSError:
        pass

    resultado["ok"] = not errores
    resultado["detalle"] = "Se movieron %d archivos a la nueva ubicación." % resultado["archivos"]
    return resultado
