# -*- coding: utf-8 -*-
"""
CONFIGURAR EL TOKEN DE GITHUB (Flota + Eventos)
===============================================
El token NO se puede crear desde aquí: GitHub exige iniciar sesión en tu cuenta.
Se crea acá (2 minutos):

  1. https://github.com/settings/tokens?type=beta
  2. «Generate new token»:
       · Token name:  BlackCube y BlackRaiders Compilar
       · Expiration:  90 días (o «No expiration»)
       · Repository access → Only select repositories:
            Sistema_control_flota_automotriz
            Sistema_control_eventos
       · Permissions → Repository permissions:
            Actions  → Read and write     (obligatorio)
            Contents → Read-only
            Metadata → Read-only          (se marca solo)
  3. «Generate token» y copiá el valor (github_pat_...).

Después ejecutá este programa UNA sola vez y pegá el token: se guarda en los
DOS programas (Flota y Eventos) en su archivo github_token.txt, que está en
.gitignore y por lo tanto NUNCA se sube al repositorio.
"""

import json
import os
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))
API = "https://api.github.com"
NOMBRE_ARCHIVO = "github_token.txt"


def _carpetas_programas():
    """Carpetas de los programas (Flota y Eventos) donde guardar el token."""
    carpetas = [REPO]
    padre = os.path.dirname(REPO)
    try:
        for nombre in sorted(os.listdir(padre)):
            ruta = os.path.join(padre, nombre)
            if not os.path.isdir(ruta) or os.path.abspath(ruta) == os.path.abspath(REPO):
                continue
            if os.path.isfile(os.path.join(ruta, "subir_github.py")):
                carpetas.append(ruta)
    except Exception:
        pass
    return carpetas


def _llamar(ruta, token):
    peticion = urllib.request.Request(API + ruta)
    peticion.add_header("Accept", "application/vnd.github+json")
    peticion.add_header("User-Agent", "configurar-token")
    peticion.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(peticion, timeout=25) as respuesta:
            return json.loads(respuesta.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as e:
        return {"__error__": "%s %s" % (e.code, e.reason)}
    except Exception as e:
        return {"__error__": str(e)}


def _remoto(carpeta):
    """usuario/repositorio del remoto origin de esa carpeta."""
    try:
        import subprocess
        salida = subprocess.run(["git", "-C", carpeta, "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
    except Exception:
        salida = ""
    if not salida:
        return "", ""
    parte = salida.split(":")[-1] if salida.startswith("git@") else salida.split("github.com/")[-1]
    parte = parte.strip("/")
    if parte.endswith(".git"):
        parte = parte[:-4]
    trozos = parte.split("/")
    return (trozos[0], trozos[1]) if len(trozos) >= 2 else ("", "")


def _pedir_token():
    """Pide el token sin mostrarlo (y sin colgarse si no hay consola)."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            import getpass
            return getpass.getpass(" Pegá el token (no se muestra al escribir): ").strip()
    except Exception:
        pass
    try:
        return input(" Pegá el token: ").strip()
    except Exception:
        return ""


def main():
    print("=" * 70)
    print(" CONFIGURAR EL TOKEN DE GITHUB  (Flota + Eventos)")
    print("=" * 70)
    carpetas = _carpetas_programas()
    print(" Programas detectados:")
    for carpeta in carpetas:
        usuario, repositorio = _remoto(carpeta)
        print("   · %-46s  %s" % (os.path.basename(carpeta),
                                   ("%s/%s" % (usuario, repositorio)) if usuario else "(sin remoto)"))
    print("")
    print(" Creá el token en:  https://github.com/settings/tokens?type=beta")
    print(" Nombre sugerido:   BlackCube y BlackRaiders Compilar")
    print(" Permisos: Actions → Read and write   ·   Contents → Read-only")
    print("")
    existentes = [c for c in carpetas if os.path.isfile(os.path.join(c, NOMBRE_ARCHIVO))]
    if existentes:
        print(" Ya hay un token guardado en %d programa(s); si pegás otro, lo reemplaza." % len(existentes))
        print("")

    token = _pedir_token()
    if not token:
        print("")
        print(" No se escribió nada: no se guardó ningún token.")
        return 1

    print("")
    print(" Comprobando el token…")
    identidad = _llamar("/user", token)
    if identidad.get("__error__"):
        print(" ⚠️  El token no funcionó: " + str(identidad["__error__"]))
        print("     Revise que esté bien copiado y que no haya vencido.")
        return 1
    print("   ✔ Usuario de GitHub: " + str(identidad.get("login") or "(desconocido)"))

    # se comprueba el permiso de Actions en cada repositorio
    for carpeta in carpetas:
        usuario, repositorio = _remoto(carpeta)
        if not usuario or not repositorio:
            continue
        flujos = _llamar("/repos/%s/%s/actions/workflows" % (usuario, repositorio), token)
        if flujos.get("__error__"):
            print(" ⚠️  Sin acceso a %s/%s: %s" % (usuario, repositorio, flujos["__error__"]))
            print("     Agregue ese repositorio al token o dé permiso Actions (Read and write).")
        else:
            nombres = [w.get("name") for w in (flujos.get("workflows") or [])]
            print("   ✔ %s → %s" % (repositorio, ", ".join(nombres) if nombres else "(sin flujos)"))

    print("")
    guardados = 0
    for carpeta in carpetas:
        destino = os.path.join(carpeta, NOMBRE_ARCHIVO)
        try:
            with open(destino, "w", encoding="utf-8") as archivo:
                archivo.write(token)
            print(" ✅ Guardado en: " + destino)
            guardados += 1
        except Exception as e:
            print(" ⚠️  No se pudo guardar en %s: %s" % (destino, e))
    if not guardados:
        return 1
    print("")
    print(" Listo: SUBIR_A_GITHUB ya puede lanzar y vigilar la compilación")
    print(" en los dos programas. (El archivo está en .gitignore: no se sube.)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nCancelado.")
        sys.exit(1)
