# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import psycopg2
import subprocess
import sys
import uuid
from datetime import datetime

# =========================================================
# ⚙️ CREDENCIALES DE LA BASE DE DATOS DE LICENCIAS
# =========================================================
SUPABASE_HOST = "aws-1-us-west-2.pooler.supabase.com"
SUPABASE_DB_NAME = "postgres"
SUPABASE_USER = "postgres.nqjfptmupnrkmgvnbyly"
SUPABASE_PASSWORD = "Ve-10339092"
SUPABASE_PORT = "6543"

# =========================================================
# 🚀 IDENTIFICADOR DEL SOFTWARE
# =========================================================
# Cambia este texto por el nombre exacto del software.
SOFTWARE_ASIGNADO = "Control de Eventos" 

def obtener_hwid():
    """Genera o extrae el ID de Hardware único de la PC (HWID)"""
    hwid = ""
    try:
        if sys.platform == "win32":
            hwid = subprocess.check_output('wmic csproduct get uuid').decode().split('\n')[1].strip()
        elif sys.platform == "darwin":
            hwid = subprocess.check_output('ioreg -rd1 -c IOPlatformExpertDevice | grep -E "IOPlatformUUID"', shell=True).decode().split('"')[-2]
        else:
            hwid = subprocess.check_output('cat /etc/machine-id', shell=True).decode().strip()
    except Exception:
        hwid = str(uuid.UUID(int=uuid.getnode())).upper()
    return hwid

def consultar_licencia_supabase(hwid):
    """Se conecta a Supabase y verifica el estado de TODAS las licencias asociadas a este HWID y Software"""
    try:
        conn = psycopg2.connect(
            dbname=SUPABASE_DB_NAME,
            user=SUPABASE_USER,
            password=SUPABASE_PASSWORD,
            host=SUPABASE_HOST,
            port=SUPABASE_PORT,
            connect_timeout=5
        )
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