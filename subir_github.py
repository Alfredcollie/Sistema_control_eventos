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
    --version X.Y.Z   publica esa versión sin preguntar
    --no-release      sube sin crear etiqueta ni release

PROGRAMA: EVENTOS
REPOSITORIO: https://github.com/Alfredcollie/Sistema_control_eventos
"""

import os
import subprocess
import shutil
import time
import sys
import datetime
import io
import re

REPO = os.path.dirname(os.path.abspath(__file__))
RAMA_PREFERIDA = "main"
PROGRAMA = "EVENTOS"


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


def _flujo_compila_con_rama(flujo):
    """¿El flujo compila cuando se sube la RAMA (no la etiqueta)?"""
    if not flujo:
        return False
    ruta = os.path.join(REPO, ".github", "workflows", flujo)
    if not os.path.isfile(ruta):
        return False
    try:
        texto = io.open(ruta, encoding="utf-8").read()
    except Exception:
        return False
    # se busca el bloque 'push:' y si dentro declara 'branches:'
    marcador = texto.find("push:")
    if marcador == -1:
        return False
    trozo = texto[marcador:marcador + 400]
    return "branches:" in trozo


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
        print("   ℹ️  La compilación la dispara la etiqueta de versión.")
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


# =========================================================
# 🏷️ VERSIÓN DEL RELEASE
# =========================================================
def _leer_version_codigo(programa):
    """Versión que dice el código (si el programa la tiene)."""
    candidatos = []
    if programa == "EVENTOS":
        candidatos = ["control_general.py"]
    elif programa == "FLOTA":
        candidatos = ["control_general.py", "version.py", "versiones.py"]
    for nombre in candidatos:
        ruta = os.path.join(REPO, nombre)
        if not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8", errors="ignore") as archivo:
                for linea in archivo:
                    if "VERSION_ACTUAL" in linea and "=" in linea:
                        texto = linea.split("=", 1)[1].strip().strip('"').strip("'")
                        if texto:
                            return texto
        except Exception:
            pass
    return ""


def ultima_etiqueta():
    """Última etiqueta (tag) de versión del repositorio."""
    r = ejecutar(["tag", "--sort=-v:refname"], silencioso=True)
    for linea in (r.stdout or "").splitlines():
        etiqueta = linea.strip()
        if re.match(r"^[vV]\d", etiqueta):
            return etiqueta
    return ""


def _numeros(texto):
    limpio = re.sub(r"[^0-9.]", "", str(texto or ""))
    partes = [int(p) for p in limpio.split(".") if p.isdigit()]
    while len(partes) < 3:
        partes.append(0)
    return tuple(partes[:4])


def _version_limpia(texto):
    """'v1.5.7' / 'V1.5.7' / '1.5.7' -> '1.5.7' (o '' si no sirve)."""
    limpio = str(texto or "").strip().lstrip("vV").strip()
    if not re.match(r"^\d+(\.\d+){1,3}$", limpio):
        return ""
    return limpio


def preguntar_version(actual_tag):
    """Pregunta la versión: Enter = la misma · escribir otro número = cambiarla."""
    actual = _version_limpia(actual_tag) or "1.0.0"
    print("")
    print("--- Versión del release ------------------------------------------")
    print("   Última versión publicada : V%s" % actual)
    codigo = _leer_version_codigo(PROGRAMA)
    if codigo:
        print("   Versión en el código     : %s" % codigo)
    print("")
    print("   ENTER  = mantener la versión V%s" % actual)
    print("   Escribir otro número (ej. %s) + ENTER = versión nueva"
          % _siguiente_version(actual))
    try:
        respuesta = input("   Versión [V%s]: " % actual).strip()
    except (EOFError, KeyboardInterrupt):
        respuesta = ""
    if not respuesta:
        return actual
    nueva = _version_limpia(respuesta)
    if not nueva:
        print("   ⚠️  «%s» no parece un número de versión. Se usará V%s."
              % (respuesta, actual))
        return actual
    if _numeros(nueva) < _numeros(actual):
        print("   ⚠️  V%s es MENOR que la actual (V%s)." % (nueva, actual))
        try:
            if input("       ¿Continuar igual? (s/N): ").strip().lower() not in ("s", "si", "sí", "y"):
                return actual
        except (EOFError, KeyboardInterrupt):
            return actual
    return nueva


def _siguiente_version(actual):
    partes = list(_numeros(actual))
    partes[2] = partes[2] + 1
    return ".".join(str(p) for p in partes[:3])


def actualizar_version_en_codigo(nueva):
    """Escribe la versión nueva en el código (solo si el programa la tiene)."""
    nombre = "control_general.py" if PROGRAMA == "EVENTOS" else ""
    if not nombre:
        return False
    ruta = os.path.join(REPO, nombre)
    if not os.path.isfile(ruta):
        return False
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            lineas = archivo.readlines()
        cambiado = False
        for i, linea in enumerate(lineas):
            if "VERSION_ACTUAL" in linea and "=" in linea:
                antes = linea.split("=", 1)[0]
                lineas[i] = antes + '= "v%s"\n' % nueva
                cambiado = True
        if cambiado:
            with open(ruta, "w", encoding="utf-8") as archivo:
                archivo.writelines(lineas)
        return cambiado
    except Exception as e:
        print("   ⚠️  No se pudo escribir la versión en el código: %s" % e)
        return False


def publicar_release(version, rama, forzar=False):
    """Crea (o mueve) la etiqueta Vx.y.z y la sube: eso publica el RELEASE."""
    if not version:
        return False
    etiqueta = "V" + version
    usuario, repositorio = _remoto_github()
    token = _token_github()
    print("")
    print("--- Liberación (release) -----------------------------------------")
    print("   Etiqueta: %s" % etiqueta)

    existentes = ejecutar(["tag"], silencioso=True).stdout or ""
    ya_existe = etiqueta in [l.strip() for l in existentes.splitlines()]

    if ya_existe and not forzar:
        print("   ℹ️  La etiqueta %s ya existe en este equipo." % etiqueta)
        try:
            if input("       ¿Volver a publicar V%s? (s/N): " % version).strip().lower() not in ("s", "si", "sí", "y"):
                print("   (no se publicó nada nuevo)")
                return False
        except (EOFError, KeyboardInterrupt):
            return False
        forzar = True

    argumentos_tag = ["tag", "-f", etiqueta] if ya_existe else ["tag", etiqueta]
    if ejecutar(argumentos_tag).returncode != 0:
        print("   ⚠️  No se pudo crear la etiqueta %s." % etiqueta)
        return False
    print("   ✔ Etiqueta creada: %s" % etiqueta)

    orden_push = ["push", "origin", etiqueta]
    if ya_existe:                      # sólo se fuerza si la etiqueta ya estaba
        orden_push.insert(1, "--force")
    if ejecutar(orden_push).returncode != 0:
        print("   ⚠️  No se pudo subir la etiqueta.")
        print("       Pruebe:  git push %sorigin %s" % ("--force " if forzar else "", etiqueta))
        return False
    print("   ✔ Etiqueta subida: la compilación publicará el release.")

    if usuario and repositorio:
        url = "https://github.com/%s/%s/releases/tag/%s" % (usuario, repositorio, etiqueta)
        print("   🔗 Release: " + url)
        if token:
            print("   ⏳ El release aparecerá cuando termine la compilación (2-6 min).")
    return True


def main():
    argumentos = sys.argv[1:]
    solo_estado = "--estado" in argumentos or "--dry-run" in argumentos
    sin_release = "--no-release" in argumentos or "--sin-release" in argumentos
    version_fijada = ""
    if "--version" in argumentos:
        try:
            version_fijada = _version_limpia(argumentos[argumentos.index("--version") + 1])
        except IndexError:
            version_fijada = ""
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

    # 🏷️ Versión del release (Enter = la misma · número nuevo = cambiarla)
    version_tag = ""
    version_nueva = False
    if not sin_release:
        actual = ultima_etiqueta() or _leer_version_codigo(PROGRAMA)
        if version_fijada:
            version_tag = version_fijada
            print("")
            print("--- Versión del release ------------------------------------------")
            print("   Versión indicada: V%s" % version_tag)
        else:
            version_tag = preguntar_version(actual)
        version_nueva = (_numeros(version_tag) != _numeros(actual)) or (not actual)
        if version_nueva and actualizar_version_en_codigo(version_tag):
            print("   ✔ Versión escrita en el código: v%s" % version_tag)
        print("   → Se publicará la versión V%s" % version_tag)

    print("--- Archivos con cambios ------------------------------------------")
    estado = ejecutar(["status", "--porcelain"], silencioso=True).stdout or ""
    if not estado.strip():
        print("   (ninguno)  ✔ Nada nuevo para subir.")
        if sin_release or not version_tag:
            return 0
        print("")
        print("   No hay archivos nuevos, pero se puede publicar el release igual.")
        try:
            if input("   ¿Publicar la versión V%s ahora? (s/N): " % version_tag).strip().lower() not in ("s", "si", "sí", "y"):
                print("   (no se publicó nada)")
                return 0
        except (EOFError, KeyboardInterrupt):
            return 0
        if publicar_release(version_tag, rama):
            print("")
            print(" ✅ Release V%s publicado." % version_tag)
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
        if version_tag:
            mensaje = "v%s — %s" % (version_tag, mensaje)
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

    # 🏷️ Liberación: se crea y sube la etiqueta Vx.y.z
    release_ok = False
    if version_tag and "--no-compilar" not in argumentos:
        release_ok = publicar_release(version_tag, rama, forzar=version_nueva)
    elif version_tag:
        print(" (--no-compilar: no se publicó el release)")

    # 🚀 Compilación: ahora la dispara la ETIQUETA de versión (una sola vez)
    if "--no-compilar" in argumentos:
        print(" (--no-compilar: no se compiló nada)")
    elif release_ok:
        print("")
        print(" ℹ️  La etiqueta V%s ya lanzó la compilación y el release." % version_tag)
        print("     (una sola compilación por versión)")
    elif "--compilar" in argumentos:
        try:
            compilar_y_publicar(rama, forzar=True)
        except Exception as e_comp:
            print("   ⚠️  No se pudo lanzar la compilación: %s" % e_comp)
    else:
        print("")
        print(" ℹ️  No se compiló: las compilaciones salen con una versión nueva.")
        print("     Para forzar una compilación de prueba use:  --compilar")

    if release_ok:
        usuario, repositorio = _remoto_github()
        print("")
        print(" ✅ RELEASE V%s en camino." % version_tag)
        print("    " + "https://github.com/%s/%s/releases/tag/V%s"
              % (usuario, repositorio, version_tag))
        print("    (los archivos .exe / .dmg aparecen cuando termine la compilación)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)
