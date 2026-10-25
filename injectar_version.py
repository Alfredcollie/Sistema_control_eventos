# -*- coding: utf-8 -*-
"""Inyecta la versión del tag de GitHub en control_general.py (VERSION_ACTUAL).
Se ejecuta en el workflow ANTES de PyInstaller.

Fuente de la versión (en orden de prioridad):
  1. Push de un tag  -> usa el nombre del tag (GITHUB_REF_NAME)
  2. workflow_dispatch con tag_name
  3. En cualquier otro caso, deja VERSION_ACTUAL sin cambios.
"""
import os

ARCHIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control_general.py")


def obtener_version():
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        return os.environ.get("GITHUB_REF_NAME", "").strip()
    tag_input = (os.environ.get("INPUT_TAG_NAME") or "").strip()
    if tag_input:
        return tag_input
    return None


def main():
    ver = obtener_version()
    if not ver:
        print("Sin tag (build de desarrollo): se conserva VERSION_ACTUAL.")
        return

    with open(ARCHIVO, encoding="utf-8") as f:
        lineas = f.read().splitlines()

    cambiadas = 0
    nuevas = []
    for linea in lineas:
        if linea.strip().startswith("VERSION_ACTUAL"):
            linea = 'VERSION_ACTUAL = "{}"'.format(ver)
            cambiadas += 1
        nuevas.append(linea)

    if cambiadas == 0:
        print("ADVERTENCIA: no se encontro 'VERSION_ACTUAL' en control_general.py")
        return

    with open(ARCHIVO, "w", encoding="utf-8") as f:
        f.write("\n".join(nuevas) + "\n")

    print("Version inyectada: {}".format(ver))


if __name__ == "__main__":
    main()
