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
    --compilar  fuerza el lanzamiento de «Compilar y publicar»
    --no-compilar  sube sin lanzar la compilación

PROGRAMA: EVENTOS
REPOSITORIO: https://github.com/Alfredcollie/Sistema_control_eventos
"""

import os
import subprocess
import shutil
import time
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



# =========================================================
# 🚀 COMPILAR Y PUBLICAR (GitHub Actions) DESPUÉS DEL PUSH
# =========================================================
NOMBRE_WORKFLOW = "Compilar y publicar (Windows + macOS)"


def _remoto_github():
    """Devuelve (usuario, repositorio) leídos del remoto 'origin'."""
    r = ejecutar(["remote", "get-url", "origin"], silencioso=True)
    url = (r.stdout or "").strip()
    if not url:
        return "", ""
    url = url.replace("\n", "")
    if url.startswith("git@"):
        parte = url.split(":", 1)[-1]
    else:
        parte = url.split("github.com/", 1)[-1]
    parte = parte.strip("/")
    if parte.endswith(".git"):
        parte = parte[:-4]
    trozos = parte.split("/")
    if len(trozos) >= 2:
        return trozos[0], trozos[1]
    return "", ""


def _workflow_compilar():
    """Archivo del flujo «Compilar y publicar» dentro de .github/workflows."""
    carpeta = os.path.join(REPO, ".github", "workflows")
    if not os.path.isdir(carpeta):
        return ""
    candidatos = []
    for nombre in sorted(os.listdir(carpeta)):
        if not nombre.lower().endswith((".yml", ".yaml")):
            continue
        ruta = os.path.join(carpeta, nombre)
        try:
            with open(ruta, "r", encoding="utf-8", errors="ignore") as archivo:
                texto = archivo.read()
        except Exception:
            continue
        if NOMBRE_WORKFLOW.lower() in texto.lower():
            return nombre
        if "compilar" in nombre.lower():
            candidatos.append(nombre)
    return candidatos[0] if candidatos else ""


def _token_github():
    """Token de GitHub: variable de entorno o archivo github_token.txt junto al script.

    El archivo NO se sube al repositorio (está en .gitignore). Sirve para lanzar y
    vigilar «Compilar y publicar» desde el script sin instalar nada más.
    """
    for clave in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        valor = str(os.environ.get(clave) or "").strip()
        if valor:
            return valor
    for nombre in ("github_token.txt", ".github_token"):
        ruta = os.path.join(REPO, nombre)
        try:
            if os.path.isfile(ruta):
                with open(ruta, "r", encoding="utf-8") as archivo:
                    valor = archivo.read().strip()
                if valor:
                    return valor
        except Exception:
            pass
    return ""


def _flujo_dispara_con_push(flujo):
    """¿El flujo se ejecuta solo con el push? (para no lanzarlo dos veces)."""
    if not flujo:
        return True
    ruta = os.path.join(REPO, ".github", "workflows", flujo)
    try:
        with open(ruta, "r", encoding="utf-8", errors="ignore") as archivo:
            texto = archivo.read()
    except Exception:
        return True
    cabecera = texto.split("jobs:")[0]
    return "push:" in cabecera


def _api(usuario, repositorio, ruta, token, metodo="GET", cuerpo=None):
    """Llama a la API de GitHub y devuelve el JSON (o None)."""
    import json
    import urllib.request
    import urllib.error

    url = "https://api.github.com/repos/%s/%s%s" % (usuario, repositorio, ruta)
    datos = None
    if cuerpo is not None:
        datos = json.dumps(cuerpo).encode("utf-8")
    peticion = urllib.request.Request(url, data=datos, method=metodo)
    peticion.add_header("Accept", "application/vnd.github+json")
    peticion.add_header("User-Agent", "subir-github")
    if token:
        peticion.add_header("Authorization", "Bearer " + token)
    if datos:
        peticion.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(peticion, timeout=25) as respuesta:
            texto = respuesta.read().decode("utf-8", "ignore")
            return json.loads(texto) if texto.strip() else {}
    except urllib.error.HTTPError as e:
        detalle = ""
        try:
            detalle = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        print("   (API GitHub %s: %s %s)" % (e.code, e.reason, detalle))
        return None
    except Exception as e:
        print("   (API GitHub: %s)" % e)
        return None


def compilar_y_publicar(rama, forzar=False):
    """Lanza «Compilar y publicar (Windows + macOS)» en GitHub y muestra el enlace."""
    usuario, repositorio = _remoto_github()
    flujo = _workflow_compilar()
    token = _token_github()
    gh = shutil.which("gh")

    print("")
    print("--- Compilar y publicar (Windows + macOS) ------------------------")
    if not usuario or not repositorio:
        print("   ⚠️  No se pudo leer el repositorio del remoto 'origin'.")
        return
    print("   Repositorio: %s/%s" % (usuario, repositorio))
    print("   Flujo      : %s" % (flujo or NOMBRE_WORKFLOW))
    print("   Rama       : %s" % rama)

    url_acciones = "https://github.com/%s/%s/actions" % (usuario, repositorio)
    if flujo:
        url_acciones = ("https://github.com/%s/%s/actions/workflows/%s"
                        % (usuario, repositorio, flujo))

    # 1) ¿Hace falta lanzarlo? El push ya lo dispara en los dos programas
    ya_lo_dispara = _flujo_dispara_con_push(flujo)
    if ya_lo_dispara and not forzar:
        print("   ℹ️  El push ya dispara la compilación (flujo con 'on: push').")
        print("       (use --compilar si quiere lanzar una ejecución extra)")

    lanzado = False
    lanzar = forzar or not ya_lo_dispara
    if lanzar and gh and True:
        try:
            orden = [gh, "workflow", "run", NOMBRE_WORKFLOW, "--ref", rama]
            resultado = subprocess.run(orden, capture_output=True, text=True, cwd=REPO)
            if resultado.returncode == 0:
                print("   ✅ Flujo lanzado con GitHub CLI (gh).")
                lanzado = True
            else:
                print("   (gh no pudo lanzarlo: %s)" % (resultado.stderr or "").strip()[:120])
        except Exception as e:
            print("   (gh: %s)" % e)

    if lanzar and not lanzado and token and flujo:
        if _api(usuario, repositorio,
                "/actions/workflows/%s/dispatches" % flujo, token,
                metodo="POST", cuerpo={"ref": rama}) is not None:
            print("   ✅ Flujo lanzado con la API de GitHub.")
            lanzado = True

    if not lanzado:
        print("   ℹ️  No se lanzó uno nuevo: el PUSH ya dispara la compilación")
        print("       (el flujo se ejecuta en cada push a la rama).")

    # 2) Se muestra el estado de la última ejecución
    if token:
        espera = 0
        while espera < 40:
            datos = _api(usuario, repositorio, "/actions/runs?per_page=5", token)
            if datos and datos.get("workflow_runs"):
                for corrida in datos["workflow_runs"][:3]:
                    nombre_flujo = str(corrida.get("name") or "")
                    if NOMBRE_WORKFLOW.lower() not in nombre_flujo.lower():
                        continue
                    estado = corrida.get("status") or ""
                    conclusion = corrida.get("conclusion") or ""
                    print("   ▶️  Ejecución #%s  ·  %s %s"
                          % (corrida.get("run_number"), estado,
                             ("(" + conclusion + ")") if conclusion else ""))
                    print("      " + str(corrida.get("html_url") or url_acciones))
                    espera = 999
                    break
            if espera == 999:
                break
            time.sleep(5)
            espera += 5
    print("   Seguir el avance: " + url_acciones)


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

    # 🚀 Después de subir: compilar y publicar (Windows + macOS)
    if "--no-compilar" not in argumentos:
        try:
            compilar_y_publicar(rama, forzar="--compilar" in argumentos)
        except Exception as e_comp:
            print("   ⚠️  No se pudo lanzar la compilación: %s" % e_comp)
    else:
        print(" (--no-compilar: no se lanzó la compilación)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)
