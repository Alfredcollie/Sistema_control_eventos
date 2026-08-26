# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import psycopg2
import keyring
import subprocess
import sys
import os
import uuid
import re
from datetime import datetime

# Bundle de certificados raíz para SSL a Supabase en macOS/PyInstaller
try:
    import certifi
    _CA_BUNDLE = certifi.where()
except Exception:
    _CA_BUNDLE = None

# =========================================================
# ⚙️ CREDENCIALES DE LA BASE DE DATOS DE LICENCIAS
# El host / puerto / base / usuario NO son secretos.
# La CONTRASEÑA es el secreto: se lee del llavero del sistema
# (servicio "ControlFlotaLicencias") o de la variable de entorno
# SUPABASE_LIC_DB_PASSWORD. Ya NO va en el código.
# =========================================================
SUPABASE_HOST = "aws-1-us-west-2.pooler.supabase.com"
SUPABASE_DB_NAME = "postgres"
SUPABASE_USER = "postgres.nqjfptmupnrkmgvnbyly"
SUPABASE_PORT = "6543"
SERVICE_NAME = "ControlFlotaLicencias"


def _password_licencia():
    """Contraseña de la base de licencias (llavero o variable de entorno)."""
    try:
        pw = keyring.get_password(SERVICE_NAME, "SUPABASE_DB_PASSWORD")
        if pw:
            return pw
    except Exception:
        pass
    return os.environ.get("SUPABASE_LIC_DB_PASSWORD", "")

# =========================================================
# 🚀 IDENTIFICADOR DEL SOFTWARE
# =========================================================
# Cambia este texto por el nombre exacto del software.
SOFTWARE_ASIGNADO = "Control de Flota Automotriz" 

def _ejecutar_comando(comando):
    """Ejecuta un comando sin shell y devuelve su salida como texto (o vacío si falla)."""
    try:
        return subprocess.check_output(
            comando, stderr=subprocess.DEVNULL, text=True, timeout=10
        ).strip()
    except Exception:
        return ""


def obtener_hwid():
    """Genera o extrae el ID de Hardware único del equipo (HWID).
    Compatible con Windows, macOS (Intel y Apple Silicon M1/M2/M3/M4) y Linux."""
    hwid = ""
    try:
        if sys.platform == "win32":
            # WMIC fue eliminado en Windows 11 24H2+; usamos fuentes alternativas en orden.
            salida = _ejecutar_comando(["wmic", "csproduct", "get", "uuid"])
            if not salida:
                salida = _ejecutar_comando([
                    "powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"
                ])
            for linea in salida.splitlines():
                linea = linea.strip()
                if linea and linea.lower() != "uuid":
                    hwid = linea
                    break
        elif sys.platform == "darwin":
            # ioreg sin shell: extraemos el UUID con expresiones regulares.
            salida = _ejecutar_comando(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"])
            for linea in salida.splitlines():
                if "IOPlatformUUID" in linea:
                    m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', linea)
                    if m:
                        hwid = m.group(1)
                        break
            if not hwid:
                salida = _ejecutar_comando(["system_profiler", "SPHardwareDataType"])
                m = re.search(r"Hardware\s+UUID:\s*([A-Fa-f0-9-]+)", salida, re.IGNORECASE)
                if m:
                    hwid = m.group(1)
        else:
            hwid = _ejecutar_comando(["cat", "/etc/machine-id"])
            if not hwid:
                hwid = _ejecutar_comando(["cat", "/var/lib/dbus/machine-id"])
    except Exception:
        hwid = ""
    if not hwid:
        # Último recurso multiplataforma: identificador derivado del hardware base de Python
        hwid = str(uuid.UUID(int=uuid.getnode())).upper()
    return hwid

def consultar_licencia_supabase(hwid):
    """Se conecta a Supabase y verifica el estado de TODAS las licencias asociadas a este HWID y Software"""
    try:
        password = _password_licencia()
        if not password:
            return False, "Error: contraseña de licencias no configurada. Defina SUPABASE_LIC_DB_PASSWORD o guarde la credencial en el llavero."
        base = dict(
            dbname=SUPABASE_DB_NAME,
            user=SUPABASE_USER,
            password=password,
            host=SUPABASE_HOST,
            port=SUPABASE_PORT,
            connect_timeout=5,
            sslmode="require"
        )
        conn = None
        if _CA_BUNDLE and os.path.exists(_CA_BUNDLE):
            try:
                conn = psycopg2.connect(**{**base, "sslrootcert": _CA_BUNDLE})
            except Exception:
                conn = None
        if conn is None:
            conn = psycopg2.connect(**base)
        cursor = conn.cursor()
        
        query = """
            SELECT e.estado, e.fecha_vencimiento, l.estado, l.fecha_vencimiento
            FROM lic_equipos e
            JOIN lic_asignaciones l ON e.licencia_id = l.id
            WHERE e.hwid = %s AND l.software = %s
        """
        cursor.execute(query, (hwid, SOFTWARE_ASIGNADO))
        resultados = cursor.fetchall()
        conn.close()

        if not resultados:
            return False, f"Este equipo no cuenta con una licencia para el software '{SOFTWARE_ASIGNADO}'.\nCopie el HWID a continuación y envíelo a soporte."

        hoy = datetime.now()
        fmt = "%d/%m/%Y"
        errores = []

        # Revisar si al menos una de las licencias asignadas a este equipo es válida
        for res in resultados:
            estado_eq, venc_eq, estado_lic, venc_lic = res
            valido = True
            
            if estado_eq != 'Activa':
                errores.append(f"Acceso del equipo: {estado_eq.upper()}")
                valido = False
            if estado_lic != 'Activa':
                errores.append(f"Licencia general: {estado_lic.upper()}")
                valido = False

            try:
                if venc_eq:
                    if hoy > datetime.strptime(venc_eq, fmt):
                        errores.append("Licencia de equipo vencida")
                        valido = False
            except Exception: pass

            try:
                if venc_lic:
                    if hoy > datetime.strptime(venc_lic, fmt):
                        errores.append("Licencia general vencida")
                        valido = False
            except Exception: pass

            if valido:
                return True, "Licencia Válida"

        motivos = "\n".join(list(set(errores)))
        return False, f"Acceso denegado. Razones:\n{motivos}"

    except psycopg2.OperationalError:
        return False, "Error: No hay conexión a Internet o el servidor de licencias no responde."
    except Exception as e:
        return False, f"Error al verificar la licencia:\n{str(e)}"

def mostrar_pantalla_bloqueo(hwid, mensaje_error):
    app = ctk.CTk()
    app.title(f"Verificación de Licencia - {SOFTWARE_ASIGNADO}")
    app.geometry("600x400")
    app.resizable(False, False)
    ctk.set_appearance_mode("Light")

    app.update_idletasks()
    x = (app.winfo_screenwidth() // 2) - (600 // 2)
    y = (app.winfo_screenheight() // 2) - (400 // 2)
    app.geometry(f"+{x}+{y}")

    ctk.CTkLabel(app, text="🚫 ACCESO RESTRINGIDO", font=("Arial", 20, "bold"), text_color="#c0392b").pack(pady=(30, 10))
    ctk.CTkLabel(app, text=mensaje_error, font=("Arial", 12), text_color="#333333", justify="center").pack(pady=10)
    
    f_hwid = ctk.CTkFrame(app, fg_color="#f0f0f0", border_width=1, border_color="#cccccc")
    f_hwid.pack(fill="x", padx=40, pady=15)
    
    ctk.CTkLabel(f_hwid, text="CÓDIGO DE HARDWARE DE ESTE EQUIPO (HWID):", font=("Arial", 10, "bold"), text_color="#1f538d").pack(pady=(10, 5))
    
    ent_hwid = ctk.CTkEntry(f_hwid, font=("Courier", 12, "bold"), justify="center")
    ent_hwid.pack(fill="x", padx=20, pady=(0, 15))
    ent_hwid.insert(0, hwid)
    ent_hwid.configure(state="readonly")

    def copiar_hwid():
        app.clipboard_clear()
        app.clipboard_append(hwid)
        app.update()
        messagebox.showinfo("Copiado", "HWID copiado al portapapeles.")

    ctk.CTkButton(app, text="📋 Copiar Código HWID", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=copiar_hwid).pack(pady=5)
    ctk.CTkButton(app, text="❌ Cerrar Aplicación", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=app.destroy).pack(pady=5)

    app.mainloop()

def comprobar_acceso():
    hwid = obtener_hwid()
    valido, mensaje = consultar_licencia_supabase(hwid)
    if valido: return True
    else:
        mostrar_pantalla_bloqueo(hwid, mensaje)
        return False

if __name__ == "__main__":
    comprobar_acceso()