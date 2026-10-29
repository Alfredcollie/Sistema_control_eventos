# -*- coding: utf-8 -*-
"""
SUBIR A GITHUB - SOLO LOS ARCHIVOS QUE CAMBIARON
================================================
Hace tres cosas, sin intervención manual:

    1. Muestra qué archivos cambiaron (git status)
    2. Hace el commit SOLO de esos archivos
    3. Los sube a GitHub (push)

Funciona en Windows y en macOS. Se puede ejecutar de dos formas:

    * Doble clic en  SUBIR_A_GITHUB.bat      (Windows)
    * Doble clic en  SUBIR_A_GITHUB.command  (macOS)
    * O desde la consola:  python subir_github.py

Opciones:
    --estado    solo muestra qué se subiría (no hace commit ni push)
    --mensaje T usa ese texto como mensaje del commit

PROGRAMA: EVENTOS
REPOSITORIO: https://github.com/Alfredcollie/Sistema_control_eventos
"""

import os
import subprocess
import sys
import datetime

REPO = os.path.dirname(os.path.abspath(__file__))
RAMA_PREFERIDA = "main"


def ejecutar(args, silencioso=False):
    """Ejecuta un comando de git dentro del repositorio."""
    orden = ["git", "-C", REPO] + args
    resultado = subprocess.run(orden, capture_output=True, text=True)
    if not silencioso:
        salida = (resultado.stdout or "").strip()
        if salida:
            print(salida)
        error = (resultado.stderr or "").strip()
        if error and resultado.returncode != 0:
            print("   " + error)
    return resultado


def rama_actual():
    r = ejecutar(["rev-parse", "--abbrev-ref", "HEAD"], silencioso=True)
    return (r.stdout or "").strip() or RAMA_PREFERIDA


def hay_repositorio():
    return os.path.isdir(os.path.join(REPO, ".git"))


def main():
    argumentos = sys.argv[1:]
    solo_estado = "--estado" in argumentos or "--dry-run" in argumentos
    mensaje = ""
    if "--mensaje" in argumentos:
        try:
            mensaje = argumentos[argumentos.index("--mensaje") + 1]
        except IndexError:
            mensaje = ""

    print("=" * 66)
    print(" SUBIR A GITHUB - SOLO ARCHIVOS MODIFICADOS")
    print("=" * 66)
    print(" Carpeta: " + REPO)

    if not hay_repositorio():
        print("")
        print(" ⚠️  Esta carpeta NO es un repositorio git.")
        print("     Copie aquí los archivos o pida que se prepare el repositorio.")
        return 1

    rama = rama_actual()
    print(" Rama    : " + rama)
    remoto = ejecutar(["remote", "get-url", "origin"], silencioso=True)
    print(" Repositorio: " + ((remoto.stdout or "").strip() or "(sin remoto)"))
    print("")

    print("--- Archivos con cambios ------------------------------------------")
    estado = ejecutar(["status", "--porcelain"], silencioso=True).stdout or ""
    if not estado.strip():
        print("   (ninguno)  ✔ Nada nuevo para subir.")
        return 0
    for linea in estado.strip().splitlines():
        print("   " + linea)
    print("")

    if solo_estado:
        print(" Modo --estado: NO se hizo commit ni push.")
        return 0

    # 1) se agregan SOLO los archivos que cambiaron (respeta .gitignore)
    print("--- Preparando los cambios ---------------------------------------")
    if ejecutar(["add", "-A"]).returncode != 0:
        print(" ⚠️  No se pudieron preparar los cambios.")
        return 1

    # 2) commit
    if not mensaje:
        cuantos = len([l for l in estado.strip().splitlines() if l.strip()])
        mensaje = ("Actualización %s (%d archivos)"
                   % (datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), cuantos))
    print(" Mensaje del commit: " + mensaje)
    if ejecutar(["commit", "-m", mensaje]).returncode != 0:
        print(" ⚠️  No se pudo crear el commit (¿faltan datos de usuario de git?).")
        return 1

    # 3) push
    print("")
    print("--- Subiendo a GitHub --------------------------------------------")
    subida = ejecutar(["push", "origin", rama])
    if subida.returncode != 0:
        print("")
        print(" ⚠️  NO se pudo subir. Revise:")
        print("     · que haya conexión a internet")
        print("     · que haya iniciado sesión en GitHub (usuario/token)")
        print("     · si GitHub tiene cambios nuevos: git pull --rebase origin " + rama)
        return 1

    print("")
    print(" ✅ Listo: los archivos modificados ya están en GitHub.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)
