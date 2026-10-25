# -*- coding: utf-8 -*-
"""
GUARDAR_CREDENCIALES.PY - Configuración única de credenciales de Supabase.

Guarda en el llavero del sistema (Windows Credential Manager / macOS Keychain)
las credenciales de los dos roles de aplicación, para que 'conexion.py' y
'validacion_licencia.py' NO lleven contraseñas en el código.

Uso (una sola vez por equipo):
    python guardar_credenciales.py

Alternativa sin keyring: definir variables de entorno
    SUPABASE_DB_USER / SUPABASE_DB_PASSWORD   (app principal)
    SUPABASE_LIC_USER / SUPABASE_LIC_PASSWORD (licencias)

IMPORTANTE: el usuario debe llevar el sufijo del proyecto (pooler):
    SUPABASE_DB_USER = app_control_eventos.xqhlkmqiwkeldpcxomhb
    SUPABASE_LIC_USER = app_licencias_lectura.nqjfptmupnrkmgvnbyly
"""
import getpass

try:
    import keyring
except ImportError:
    keyring = None

SERVICE_APP = "ControlEventos"
SERVICE_LIC = "ControlEventosLicencias"

DB_HOST = "aws-1-us-west-2.pooler.supabase.com"
DB_PORT = "6543"
DB_NAME = "postgres"

# El pooler de Supabase enruta por el sufijo <rol>.<project_ref>.
# Estos son los usuarios COMPLETOS (no solo el nombre del rol).
DB_USER_APP = "app_control_eventos.xqhlkmqiwkeldpcxomhb"
DB_USER_LIC = "app_licencias_lectura.nqjfptmupnrkmgvnbyly"


def _pedir_valor(etiqueta, por_defecto=""):
    valor = input(etiqueta + (f" [{por_defecto}]" if por_defecto else "") + ": ").strip()
    return valor or por_defecto


def _pedir_password(etiqueta):
    try:
        return getpass.getpass(etiqueta + ": ").strip()
    except Exception:
        return input(etiqueta + " (se verá en pantalla): ").strip()


def guardar_app():
    print("\n=== Credenciales de la APP PRINCIPAL (rol app_control_eventos) ===")
    host = _pedir_valor("Host", DB_HOST)
    port = _pedir_valor("Puerto", DB_PORT)
    dbname = _pedir_valor("Base de datos", DB_NAME)
    user = _pedir_valor("Usuario", DB_USER_APP)
    password = _pedir_password("Contraseña (no se mostrará)")

    if keyring is None:
        print("\n[!] No se pudo importar 'keyring'. Instálalo con: pip install keyring")
        print("    Alternativa: variables de entorno SUPABASE_DB_USER / SUPABASE_DB_PASSWORD.")
        return

    keyring.set_password(SERVICE_APP, "SUPABASE_DB_HOST", host)
    keyring.set_password(SERVICE_APP, "SUPABASE_DB_PORT", port)
    keyring.set_password(SERVICE_APP, "SUPABASE_DB_NAME", dbname)
    keyring.set_password(SERVICE_APP, "SUPABASE_DB_USER", user)
    keyring.set_password(SERVICE_APP, "SUPABASE_DB_PASSWORD", password)
    print(f"✅ Credenciales de la app principal guardadas para el rol '{user}'.")


def guardar_licencias():
    print("\n=== Credenciales de LICENCIAS (rol app_licencias_lectura) ===")
    user = _pedir_valor("Usuario", DB_USER_LIC)
    password = _pedir_password("Contraseña (no se mostrará)")

    if keyring is None:
        print("\n[!] No se pudo importar 'keyring'. Usa variables de entorno SUPABASE_LIC_USER / SUPABASE_LIC_PASSWORD.")
        return

    keyring.set_password(SERVICE_LIC, "SUPABASE_LIC_USER", user)
    keyring.set_password(SERVICE_LIC, "SUPABASE_LIC_PASSWORD", password)
    print(f"✅ Credenciales de licencias guardadas para el rol '{user}'.")


if __name__ == "__main__":
    print("Configuración de credenciales Supabase (una sola vez por equipo).")
    try:
        guardar_app()
        guardar_licencias()
    except KeyboardInterrupt:
        print("\nCancelado.")
    print("\nListo. Reinicia la app para que tome las nuevas credenciales.")
