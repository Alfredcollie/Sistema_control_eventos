# -*- coding: utf-8 -*-
"""
VENTAS.PY (ENTERPRISE EDITION - RENDIMIENTO EXTREMO)
- FIX STARTUP: Inicialización de formulario 100% asíncrona (Elimina el congelamiento al abrir el módulo).
- Paginación Lazy Loading (50 en 50) para Facturas, Cobros y Notas de Crédito.
- Búsqueda Asíncrona en las 3 pestañas.
- Protección del Pool de Conexiones (liberar_conexion).
- Auto-curación síncrona en segundo plano (Scope Global corregido).
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
import os
import sys
import shutil
import calendar
import re
import json
import subprocess 
import threading
import ctypes
import webbrowser
import random
import smtplib
from email.mime.text import MIMEText
import urllib.request
import urllib.parse
from datetime import datetime

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from facturacion_sunat import abrir_ventana_emision_sunat
except ImportError:
    abrir_ventana_emision_sunat = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass

def abrir_documento(ruta):
    try:
        if sys.platform == "win32":
            os.startfile(ruta)
        elif sys.platform == "darwin": 
            subprocess.call(["open", ruta])
        else: 
            subprocess.call(["xdg-open", ruta])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")

# =========================================================
# 🚀 CONTROL DE ESQUEMA DE 1RA VEZ
# =========================================================
_SCHEMA_OK = {
    "facturas_emitidas": False,
    "pagos_clientes": False,
}

# =========================================================
# 🚀 MODO LECTURA OFFLINE
# =========================================================
def obtener_conexion_segura(parent=None, mostrar_aviso=True):
    conn = conectar_db(silencioso=True)
    if not conn and mostrar_aviso:
        messagebox.showwarning(
            "Modo Lectura",
            "⚠️ No se pudo conectar a la base de datos.\n\n"
            "El sistema está en MODO LECTURA: no se permiten cambios "
            "(crear, editar, anular, eliminar o exportar) hasta que se "
            "restablezca la conexión a internet / la nube.",
            parent=parent
        )
    return conn

# =========================================================
# 🚀 MOTOR DE CONFIGURACIÓN REGIONAL
# =========================================================
def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "ruta_drive": "",
        "impresora": "",
        "ultimo_factura": "F001-0",
        "ultimo_boleta": "B001-0",
        "ultimo_recibo": "E001-0",
        "2fa_metodo": "Inactivo",
        "tel_bot_token": "",
        "tel_chat_id": "",
        "email_smtp": "smtp.gmail.com",
        "email_port": "587",
        "email_user": "",
        "email_pass": "",
        "email_dest": "",
        "ruc_empresa": "",
        "usuario_sol": "",
        "clave_sol": "",
        "client_id_sire": "",
        "client_secret_sire": ""
    }
    try:
        if os.path.exists("config_local.json"):
            with open("config_local.json", "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception: pass
    return config

CONFIG_REGIONAL = cargar_configuracion_regional()

def formatear_moneda(valor):
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    try: valor = float(valor)
    except: valor = 0.0
    
    if formato == "1.000,00":
        str_val = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        str_val = f"{valor:,.2f}"
    return f"{simbolo} {str_val}"

def desformatear_numero(valor_str):
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    val = str(valor_str).replace(simbolo, "").strip()
    if formato == "1.000,00":
        val = val.replace(".", "").replace(",", ".")
    else:
        val = val.replace(",", "")
    try: return float(val)
    except ValueError: return 0.0

def obtener_ruta_base_drive():
    ruta = CONFIG_REGIONAL.get("ruta_drive", "").strip()
    if ruta: return os.path.expanduser(ruta)
    return ""

def aplicar_estilo_treeview():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=26, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000", relief="flat", font=("Arial", 10, "bold"), bordercolor="#e0e0e0", borderwidth=1)

# =========================================================
# 🛡️ MOTOR DE SEGURIDAD OTP (CLAVE DINÁMICA)
# =========================================================
def solicitar_otp(accion, callback_exito, parent_window):
    config = cargar_configuracion_regional()
    metodo = config.get("2fa_metodo", "Inactivo")

    if metodo == "Inactivo":
        callback_exito()
        return

    codigo_otp = str(random.randint(100000, 999999))
    mensaje = f"🔐 CÓDIGO DE SEGURIDAD SUNAT\n\nAcción solicitada: {accion}\n\nTu clave dinámica (OTP) es: {codigo_otp}\n\nNo compartas este código con nadie."

    exito_envio = False
    try:
        if "Telegram" in metodo:
            token = config.get("tel_bot_token", "").strip()
            chat_id = config.get("tel_chat_id", "").strip()
            if not token or not chat_id:
                messagebox.showerror("Error", "Faltan credenciales de Telegram en Configuración General.", parent=parent_window)
                return
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": mensaje}).encode("utf-8")
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5) as response:
                exito_envio = True

        elif "Correo" in metodo:
            smtp_server = config.get("email_smtp", "smtp.gmail.com").strip()
            port = int(config.get("email_port", 587))
            user = config.get("email_user", "").strip()
            password = config.get("email_pass", "").strip()
            dest = config.get("email_dest", "").strip()
            
            if not user or not password or not dest:
                messagebox.showerror("Error", "Faltan credenciales de correo en Configuración General.", parent=parent_window)
                return
            
            msg = MIMEText(mensaje)
            msg['Subject'] = 'Código de Seguridad SUNAT - Black Cube'
            msg['From'] = user
            msg['To'] = dest
            
            server = smtplib.SMTP(smtp_server, port)
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
            server.quit()
            exito_envio = True
            
        elif "SMS" in metodo:
            messagebox.showwarning("Aviso", "El módulo SMS Twilio requiere instalación externa. Se imprimirá el código en la consola del servidor por ahora.", parent=parent_window)
            print(f"--- [ALERTA SMS TWILIO] CÓDIGO OTP --- : {codigo_otp}")
            exito_envio = True
    except Exception as e:
        messagebox.showerror("Error de Envío OTP", f"No se pudo conectar con el servicio {metodo}:\n{e}", parent=parent_window)
        return

    if not exito_envio: return

    v_otp = ctk.CTkToplevel(parent_window)
    v_otp.title("Verificación de Seguridad")
    v_otp.geometry("380x250")
    v_otp.grab_set()

    ctk.CTkLabel(v_otp, text="🔒 Verificación en Dos Pasos", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(20, 5))
    ctk.CTkLabel(v_otp, text=f"Se ha enviado un código por {metodo}.\nIngrese los 6 dígitos para autorizar.", font=("Arial", 11)).pack(pady=(0, 15))
    
    ent_codigo = ctk.CTkEntry(v_otp, placeholder_text="Ej: 123456", justify="center", font=("Arial", 18, "bold"), width=150)
    ent_codigo.pack(pady=10)

    def verificar():
        if ent_codigo.get().strip() == codigo_otp:
            v_otp.destroy()
            callback_exito()
        else:
            messagebox.showerror("Código Incorrecto", "El código ingresado no es válido.", parent=v_otp)

    ctk.CTkButton(v_otp, text="✅ Validar y Autorizar", font=("Arial", 13, "bold"), fg_color="#27ae60", hover_color="#1e8449", height=40, command=verificar).pack(pady=10)

# =========================================================
# CLASE: MINI CALENDARIO COMPARTIDO
# =========================================================
class CalendarioNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Seleccionar Fecha")
        self.geometry("280x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (280 // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (320 // 2)
        self.geometry(f"+{x}+{y}")
        self.current_year = datetime.now().year
        self.current_month = datetime.now().month
        self.header_frame = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        self.header_frame.pack(fill="x")
        btn_prev = ctk.CTkButton(self.header_frame, text="<", width=30, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.prev_month)
        btn_prev.pack(side="left", padx=10, pady=10)
        self.lbl_month_year = ctk.CTkLabel(self.header_frame, text="", font=("Arial", 14, "bold"), text_color="white")
        self.lbl_month_year.pack(side="left", expand=True)
        btn_next = ctk.CTkButton(self.header_frame, text=">", width=30, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.next_month)
        btn_next.pack(side="right", padx=10, pady=10)
        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)
        dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i, day in enumerate(dias_semana):
            lbl = ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d")
            lbl.grid(row=0, column=i, padx=4, pady=5)
        self.update_calendar()
        
    def update_calendar(self):
        for widget in self.days_frame.winfo_children():
            if int(widget.grid_info()["row"]) > 0: widget.destroy()
        meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        self.lbl_month_year.configure(text=f"{meses[self.current_month]} {self.current_year}")
        cal = calendar.monthcalendar(self.current_year, self.current_month)
        hoy = datetime.now()
        for row_idx, week in enumerate(cal, start=1):
            for col_idx, day in enumerate(week):
                if day != 0:
                    btn_color = "transparent"
                    txt_color = "black"
                    if day == hoy.day and self.current_month == hoy.month and self.current_year == hoy.year:
                        btn_color = "#d4edda"
                        txt_color = "#155724"
                    btn = ctk.CTkButton(self.days_frame, text=str(day), width=30, height=30, fg_color=btn_color, text_color=txt_color, hover_color="#e0e0e0", font=("Arial", 11))
                    btn.configure(command=lambda d=day: self.select_date(d))
                    btn.grid(row=row_idx, column=col_idx, padx=2, pady=2)
                    
    def prev_month(self):
        self.current_month -= 1
        if self.current_month < 1:
            self.current_month = 12
            self.current_year -= 1
        self.update_calendar()
        
    def next_month(self):
        self.current_month += 1
        if self.current_month > 12:
            self.current_month = 1
            self.current_year += 1
        self.update_calendar()
        
    def select_date(self, day):
        fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        if fmt == "MM/DD/AAAA":
            fecha_seleccionada = f"{self.current_month:02d}/{day:02d}/{self.current_year}"
        else:
            fecha_seleccionada = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
            
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, fecha_seleccionada)
        self.destroy()

# =========================================================
# PESTAÑA 1: FACTURAS EMITIDAS
# =========================================================
class FacturasEmitidasTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        
        self.orden_columnas = {}
        self.bloquear_autocompletado_ruc = False
        self.ruta_archivo_temp = ""
        self._id_debounce_busqueda = None
        self._carga_tabla_en_curso = False
        
        # 🚀 VARIABLES DE PAGINACIÓN (LAZY LOADING)
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_bd()
        self.crear_interfaz()

    def inicializar_bd(self):
        global _SCHEMA_OK
        if _SCHEMA_OK.get("facturas_emitidas"):
            return

        def tarea_init():
            global _SCHEMA_OK
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS facturas_emitidas (
                        id SERIAL PRIMARY KEY, tipo_documento VARCHAR(100), fecha VARCHAR(50), cliente VARCHAR(255), descripcion TEXT, 
                        evento_asociado VARCHAR(255), subtotal NUMERIC, impuesto NUMERIC, total NUMERIC, archivo_ruta TEXT, 
                        dias_credito INTEGER DEFAULT 0, det_porcentaje NUMERIC DEFAULT 0, det_monto NUMERIC DEFAULT 0, 
                        numero_documento VARCHAR(100) DEFAULT ''
                    )
                """)
                conn.commit()
                
                try: cursor.execute("ALTER TABLE facturas_emitidas ADD COLUMN estado_sunat VARCHAR(255) DEFAULT ''"); conn.commit()
                except: conn.rollback()
                try: cursor.execute("ALTER TABLE facturas_emitidas ADD COLUMN enlace_pdf_sunat TEXT DEFAULT ''"); conn.commit()
                except: conn.rollback()
                try: cursor.execute("ALTER TABLE facturas_emitidas ADD COLUMN enlace_xml_sunat TEXT DEFAULT ''"); conn.commit()
                except: conn.rollback()
                try: cursor.execute("ALTER TABLE facturas_emitidas ADD COLUMN enlace_pdf_nc TEXT DEFAULT ''"); conn.commit()
                except: conn.rollback()
                try: cursor.execute("ALTER TABLE facturas_emitidas ADD COLUMN orden_compra VARCHAR(255) DEFAULT ''"); conn.commit()
                except: conn.rollback()

                try:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS ordenes_compra_clientes (
                            id SERIAL PRIMARY KEY,
                            numero_oc VARCHAR(100),
                            cotizacion_asociada VARCHAR(255),
                            fecha VARCHAR(50),
                            archivo_ruta TEXT
                        )
                    """)
                    conn.commit()
                except: conn.rollback()

                _SCHEMA_OK["facturas_emitidas"] = True
            except Exception: pass
            finally: liberar_conexion(conn)
            
        threading.Thread(target=tarea_init, daemon=True).start()

    def abrir_calendario(self, entry_objetivo):
        CalendarioNativo(self.main_root.winfo_toplevel(), entry_objetivo)

    # 🚀 FIX STARTUP: SUGERIR CORRELATIVO 100% ASÍNCRONO
    def sugerir_correlativo(self):
        if not hasattr(self, 'combo_tipo') or not hasattr(self, 'ent_nro_doc'):
            return

        tipo = self.combo_tipo.get()
        config = cargar_configuracion_regional()

        prefijo = "F"
        if "Factura" in tipo:
            filtro_tipo = "Factura%"
            ult_cfg = config.get("ultimo_factura", "F001-0")
            prefijo = "F"
        elif "Boleta" in tipo:
            filtro_tipo = "Boleta%"
            ult_cfg = config.get("ultimo_boleta", "B001-0")
            prefijo = "B"
        else:
            filtro_tipo = "Recibo%"
            ult_cfg = config.get("ultimo_recibo", "E001-0")
            prefijo = "E"

        cfg_serie = f"{prefijo}001"
        cfg_num = 0
        if ult_cfg and "-" in ult_cfg:
            parts = ult_cfg.split("-")
            if parts[0].strip().startswith(prefijo):
                cfg_serie = parts[0].strip()
            if parts[1].strip().isdigit():
                cfg_num = int(parts[1].strip())

        def tarea():
            conn = conectar_db(silencioso=True)
            db_num = 0
            db_serie = cfg_serie
            if conn:
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT numero_documento FROM facturas_emitidas WHERE tipo_documento LIKE %s AND numero_documento LIKE '%%-%%' ORDER BY id DESC LIMIT 1", (filtro_tipo,))
                    res = cursor.fetchone()
                    if res and res[0]:
                        partes = res[0].split('-')
                        if len(partes) == 2 and partes[1].isdigit():
                            db_serie = partes[0]
                            db_num = int(partes[1])
                except Exception:
                    pass
                finally:
                    liberar_conexion(conn)

            if not db_serie.startswith(prefijo):
                db_serie = cfg_serie
                db_num = 0

            if db_serie == cfg_serie:
                final_num = max(cfg_num, db_num) + 1
                final_serie = cfg_serie
            else:
                final_serie = db_serie
                final_num = db_num + 1

            nuevo_doc = f"{final_serie}-{final_num}"
            self.main_root.after(0, lambda: self._aplicar_correlativo(nuevo_doc))
            
        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_correlativo(self, nuevo_doc):
        if hasattr(self, 'ent_nro_doc'):
            self.ent_nro_doc.delete(0, tk.END)
            self.ent_nro_doc.insert(0, nuevo_doc)

    def ordenar_por_columna(self, columna, es_numerico):
        elementos = [(self.tabla.set(item, columna), item) for item in self.tabla.get_children("")]
        ascendente = self.orden_columnas.get(columna, True)
        self.orden_columnas[columna] = not ascendente
        if es_numerico:
            elementos.sort(key=lambda el: desformatear_numero(el[0]), reverse=not ascendente)
        else:
            elementos.sort(key=lambda el: el[0].lower(), reverse=not ascendente)
        for index, (_, item) in enumerate(elementos): self.tabla.move(item, "", index)

    def autocompletar_desde_pdf(self):
        if pdfplumber is None:
            messagebox.showerror("Librería faltante", "No se encontró 'pdfplumber'. Ejecuta: pip install pdfplumber")
            return
        ruta = filedialog.askopenfilename(title="Seleccionar Factura PDF de SUNAT", filetypes=[("Archivos PDF", "*.pdf")])
        if not ruta: return
        try:
            self.bloquear_autocompletado_ruc = True
            texto = ""
            with pdfplumber.open(ruta) as pdf:
                for page in pdf.pages: texto += page.extract_text() + "\n"
            if not texto.strip(): 
                self.bloquear_autocompletado_ruc = False
                return messagebox.showwarning("Aviso", "El PDF no contiene texto seleccionable.")
            
            if re.search(r"FACTURA\s+ELECTR[OÓ]NICA", texto, re.IGNORECASE): self.combo_tipo.set("Factura (18% IGV)")
            elif re.search(r"BOLETA\s+DE\s+VENTA", texto, re.IGNORECASE): self.combo_tipo.set("Boleta (Sin IGV)")
            elif re.search(r"RECIBO\s+POR\s+HONORARIOS", texto, re.IGNORECASE):
                if re.search(r"Retenci[oó]n.*?IR[\s:\|]*\(?([\d\,\.]+)\)?", texto, re.IGNORECASE): self.combo_tipo.set("Recibo por Honorarios (8% Retención)")
                else: self.combo_tipo.set("Recibo por Honorarios (Sin Retención)")
            self.on_tipo_change(self.combo_tipo.get())

            nro_match = re.search(r"([EFB][0-9A-Z]{3}\s*-\s*\d+)", texto)
            if nro_match: self.ent_nro_doc.delete(0, tk.END); self.ent_nro_doc.insert(0, nro_match.group(1).replace(" ", ""))
            
            fecha_match = re.search(r"Fecha de Emisi[oó]n\s*[:\-]?\s*(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto, re.IGNORECASE)
            if not fecha_match: fecha_match = re.search(r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto)
            if fecha_match:
                d, m, y = fecha_match.groups()
                fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
                if fmt == "MM/DD/AAAA": self.ent_fecha.delete(0, tk.END); self.ent_fecha.insert(0, f"{m}/{d}/{y}")
                else: self.ent_fecha.delete(0, tk.END); self.ent_fecha.insert(0, f"{d}/{m}/{y}")

            cliente_match = re.search(r"(?:Señor\(es\)|Señores|Razón Social|Cliente|Recibí\s*de)\s*[:\-]\s*(.+)", texto, re.IGNORECASE)
            rucs = re.findall(r"(?:RUC|R\.U\.C\.|Documento)\s*[:\-]?\s*(\d{11})", texto, re.IGNORECASE)
            
            if cliente_match: self.combo_cliente.set(cliente_match.group(1).strip())
            if rucs: 
                self.ent_ruc.configure(state="normal")
                self.ent_ruc.delete(0, tk.END)
                self.ent_ruc.insert(0, rucs[-1])

            sub_m = re.search(r"(?:OP\.\s*GRAVADAS|SUB\s*TOTAL|Subtotal|Total por honorarios)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            tot_m = re.search(r"(?:IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|Total Neto Recibido)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            monto_base = 0.0
            if sub_m: monto_base = float(sub_m.group(1).replace(",", ""))
            elif tot_m:
                t = float(tot_m.group(1).replace(",", ""))
                monto_base = t / 1.18 if "Factura" in self.combo_tipo.get() else t
            if monto_base > 0: self.ent_subtotal.delete(0, tk.END); self.ent_subtotal.insert(0, f"{monto_base:.2f}")

            if not "Recibo" in self.combo_tipo.get():
                det_match = re.search(r"(?:Detracci[oó]n|Porcentaje|Tasa).*?(\d{1,2}(?:\.\d{1,2})?)\s*%", texto, re.IGNORECASE)
                if not det_match:
                    if re.search(r"Sujeta\s*a\s*detracci[oó]n", texto, re.IGNORECASE):
                        self.ent_detraccion.delete(0, tk.END); self.ent_detraccion.insert(0, "12") 
                else: self.ent_detraccion.delete(0, tk.END); self.ent_detraccion.insert(0, det_match.group(1))

            self.ruta_archivo_temp = ruta
            self.actualizar_totales()
            self.al_seleccionar_cliente() 
            messagebox.showinfo("Extracción Inteligente", "Datos extraídos de SUNAT.\n\nRecuerde que no es necesario adjuntar el PDF manualmente, ya que el sistema guardará el archivo procesado al registrar.")
            
            self.bloquear_autocompletado_ruc = False
        except Exception as e: 
            self.bloquear_autocompletado_ruc = False
            messagebox.showerror("Error", f"Ocurrió un error:\n{e}")

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=330, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        btn_auto = ctk.CTkButton(self.f_form, text="📄 Autocompletar desde PDF SUNAT", fg_color="#1f538d", hover_color="#163b65", command=self.autocompletar_desde_pdf)
        btn_auto.pack(fill="x", padx=10, pady=(10, 15))

        ctk.CTkLabel(self.f_form, text="Tipo de Documento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_tipo = ctk.CTkComboBox(self.f_form, values=["Factura (18% IGV)", "Boleta (Sin IGV)", "Recibo por Honorarios (8% Retención)", "Recibo por Honorarios (Sin Retención)"], state="readonly", command=self.on_tipo_change)
        self.combo_tipo.pack(fill="x", padx=10, pady=(0, 8))
        self.combo_tipo.set("Factura (18% IGV)")

        ctk.CTkLabel(self.f_form, text="N° de Documento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_nro_doc = ctk.CTkEntry(self.f_form, placeholder_text="Ej. F001-1")
        self.ent_nro_doc.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Fecha (Configurada):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fecha = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fecha.pack(fill="x", padx=10, pady=(0, 8))
        self.ent_fecha = ctk.CTkEntry(f_fecha)
        self.ent_fecha.pack(side="left", fill="x", expand=True)
        
        fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        if fmt == "MM/DD/AAAA": self.ent_fecha.insert(0, datetime.now().strftime("%m/%d/%Y"))
        else: self.ent_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
            
        ctk.CTkButton(f_fecha, text="[ 📅 ]", width=40, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha)).pack(side="right", padx=(5, 0))

        ctk.CTkLabel(self.f_form, text="Días de Crédito:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_dias = ctk.CTkEntry(self.f_form); self.ent_dias.pack(fill="x", padx=10, pady=(0, 8)); self.ent_dias.insert(0, "0")

        ctk.CTkLabel(self.f_form, text="Nombre del Cliente:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_cliente = ctk.CTkComboBox(self.f_form, command=self.al_seleccionar_cliente)
        self.combo_cliente.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="R.U.C. Cliente:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_ruc = ctk.CTkEntry(self.f_form, placeholder_text="Se autocompleta con el cliente")
        self.ent_ruc.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Concepto / Descripción de Venta:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_desc = ctk.CTkEntry(self.f_form, placeholder_text="Ej: Servicios Generales...")
        self.ent_desc.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Evento Asociado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_evento = ctk.CTkComboBox(self.f_form, state="readonly", command=self.al_seleccionar_evento)
        self.combo_evento.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Orden de Compra:", font=("Arial", 11, "bold"), text_color="#166534").pack(anchor="w", padx=10)
        self.combo_oc = ctk.CTkComboBox(self.f_form, state="readonly")
        self.combo_oc.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Monto Base (Sin IGV / Subtotal):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_subtotal = ctk.CTkEntry(self.f_form)
        self.ent_subtotal.pack(fill="x", padx=10, pady=(0, 8))
        self.ent_subtotal.bind("<KeyRelease>", self.actualizar_totales)

        self.lbl_titulo_det = ctk.CTkLabel(self.f_form, text="Detracción (%):", font=("Arial", 11, "bold"))
        self.lbl_titulo_det.pack(anchor="w", padx=10)
        self.ent_detraccion = ctk.CTkEntry(self.f_form)
        self.ent_detraccion.pack(fill="x", padx=10, pady=(0, 8))
        self.ent_detraccion.insert(0, "12")
        self.ent_detraccion.bind("<KeyRelease>", self.actualizar_totales)

        f_tot = ctk.CTkFrame(self.f_form, fg_color="#ffffff", border_width=1, border_color="#ccc")
        f_tot.pack(fill="x", padx=10, pady=(5, 10))
        self.lbl_impuesto = ctk.CTkLabel(f_tot, text="IGV (18%): 0.00", font=("Arial", 11), text_color="#555")
        self.lbl_impuesto.pack(anchor="w", padx=10, pady=(5, 0))
        self.lbl_detraccion = ctk.CTkLabel(f_tot, text="Detracción (12%): 0.00", font=("Arial", 11), text_color="#e74c3c")
        self.lbl_detraccion.pack(anchor="w", padx=10, pady=(0, 0))
        self.lbl_total = ctk.CTkLabel(f_tot, text="Neto a Cobrar: 0.00", font=("Arial", 13, "bold"), text_color="#1f538d")
        self.lbl_total.pack(anchor="w", padx=10, pady=(2, 5))

        btn_guardar = ctk.CTkButton(self.f_form, text="💾 Registrar Documento", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_registro)
        btn_guardar.pack(fill="x", padx=10, pady=(10, 15))

        self.f_wrapper_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_wrapper_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_facturas = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por N° Doc, cliente, evento, fecha...")
        self.ent_buscar_facturas.pack(side="left", fill="x", expand=True)
        self.ent_buscar_facturas.bind("<KeyRelease>", self._on_buscar_facturas_keyrelease)
        self.ent_buscar_facturas.bind("<Return>", lambda e: self.cargar_datos_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("num", "id", "fecha", "nro_doc", "dias", "tipo", "cliente", "concepto", "evento", "subtotal", "impuesto", "total", "detraccion", "neto", "estado_sunat", "archivo")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("num", text="N°", anchor="center")
        self.tabla.heading("id", text="ID (Oculto)")
        self.tabla.heading("fecha", text="Fecha ↕", command=lambda: self.ordenar_por_columna("fecha", False))
        self.tabla.heading("nro_doc", text="N° Doc. ↕", command=lambda: self.ordenar_por_columna("nro_doc", False))
        self.tabla.heading("cliente", text="Cliente ↕", command=lambda: self.ordenar_por_columna("cliente", False))
        self.tabla.heading("concepto", text="Concepto ↕", command=lambda: self.ordenar_por_columna("concepto", False))
        self.tabla.heading("evento", text="Evento ↕", command=lambda: self.ordenar_por_columna("evento", False))
        self.tabla.heading("neto", text="Neto Cobrar ↕", command=lambda: self.ordenar_por_columna("neto", True))
        self.tabla.heading("estado_sunat", text="Estado SUNAT ↕", command=lambda: self.ordenar_por_columna("estado_sunat", False))
        
        self.tabla.column("num", width=35, anchor="center")
        self.tabla.column("id", width=0, stretch=tk.NO)
        self.tabla.column("fecha", width=75, anchor="center")
        self.tabla.column("nro_doc", width=90, anchor="center")
        self.tabla.column("cliente", width=120, anchor="w")
        self.tabla.column("concepto", width=120, anchor="w")
        self.tabla.column("evento", width=120, anchor="w")
        self.tabla.column("neto", width=85, anchor="e")
        self.tabla.column("estado_sunat", width=95, anchor="center")
        
        self.tabla.config(displaycolumns=("num", "fecha", "nro_doc", "cliente", "concepto", "evento", "neto", "estado_sunat"))

        self.tabla.bind("<Double-1>", self.abrir_archivo_desde_tabla)

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        f_btn_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=(10, 0))
        
        # 🚀 BOTONES PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_btn_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        btn_gestionar = ctk.CTkButton(f_btn_tabla, text="⚙️ Gestionar Registro Seleccionado", command=self.abrir_ventana_edicion, fg_color="#34495e", hover_color="#2c3e50")
        btn_gestionar.pack(side="right")

        # 🚀 FIX: TODO EL INICIO AHORA ES ASÍNCRONO SIN BLOQUEOS
        self.cargar_clientes_bd()
        self.cargar_eventos_aprobados()
        self.cargar_ordenes_compra()
        self.sugerir_correlativo()
        self.main_root.after(100, lambda: self.cargar_datos_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_datos_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_datos_tabla()

    def cargar_ordenes_compra(self, cotizacion_codigo=None):
        def tarea():
            conn = conectar_db(silencioso=True)
            ocs = ["--- Sin Orden de Compra ---"]
            if conn:
                try:
                    c = conn.cursor()
                    if cotizacion_codigo:
                        c.execute("SELECT numero_oc FROM ordenes_compra_clientes WHERE cotizacion_asociada = %s ORDER BY id DESC", (cotizacion_codigo,))
                    else:
                        c.execute("SELECT numero_oc FROM ordenes_compra_clientes ORDER BY id DESC")
                    for r in c.fetchall():
                        ocs.append(r[0])
                except: pass
                finally: liberar_conexion(conn)
            self.main_root.after(0, lambda: self._aplicar_ocs(ocs))
        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_ocs(self, ocs):
        if hasattr(self, 'combo_oc'):
            self.combo_oc.configure(values=ocs)
            if self.combo_oc.get() not in ocs:
                self.combo_oc.set("--- Sin Orden de Compra ---")

    def al_seleccionar_evento(self, choice):
        if not choice or "GENERAL" in choice:
            self.cargar_ordenes_compra()
            return
            
        partes = choice.split(" | ")
        if len(partes) > 0:
            cod_cot = partes[0].strip()
            self.cargar_ordenes_compra(cod_cot)

    def on_tipo_change(self, choice):
        if hasattr(self, 'ent_detraccion'):
            self.ent_detraccion.configure(state="normal")
            if "Recibo" in choice:
                self.lbl_titulo_det.configure(text="Retención (%):")
                self.ent_detraccion.delete(0, tk.END)
                if "8%" in choice: self.ent_detraccion.insert(0, "8")
                else: self.ent_detraccion.insert(0, "0")
            elif "Factura" in choice:
                self.lbl_titulo_det.configure(text="Detracción (%):")
                self.al_seleccionar_cliente()
            else:
                self.lbl_titulo_det.configure(text="Detracción (%):")
                self.ent_detraccion.delete(0, tk.END); self.ent_detraccion.insert(0, "0")
            self.actualizar_totales()
            self.sugerir_correlativo()

    def al_seleccionar_cliente(self, choice=None):
        if not hasattr(self, 'ent_ruc') or not hasattr(self, 'ent_detraccion'):
            return
            
        cliente = self.combo_cliente.get().strip()
        if not cliente or cliente == "--- Seleccione Cliente ---":
            if not getattr(self, 'bloquear_autocompletado_ruc', False):
                self.ent_ruc.configure(state="normal")
                self.ent_ruc.delete(0, tk.END)
            self.cargar_eventos_aprobados() 
            self.cargar_ordenes_compra()
            return
        
        def tarea():
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn: return
            
            ruc_db = ""
            det_db = "0"
            eventos_cliente = ["GENERAL / NO ASIGNADO"]
            
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT ruc, detraccion FROM clientes WHERE TRIM(UPPER(nombre_empresa)) = TRIM(UPPER(%s))", (cliente,))
                res = cursor.fetchone()
                if res:
                    ruc_db, det_bd_raw = res
                    det_db = str(float(det_bd_raw)) if det_bd_raw is not None else "12"

                cursor.execute("SELECT * FROM cotizaciones LIMIT 0")
                columnas = [desc[0] for desc in cursor.description]
                col_cod = 'codigo_cotizacion' if 'codigo_cotizacion' in columnas else ('codigo' if 'codigo' in columnas else None)
                col_cli = 'nombre_empresa' if 'nombre_empresa' in columnas else ('cliente_nombre' if 'cliente_nombre' in columnas else ('cliente' if 'cliente' in columnas else None))
                col_ev = 'nombre_evento' if 'nombre_evento' in columnas else ('evento' if 'evento' in columnas else None)

                if col_cod and col_cli and col_ev:
                    query = f"SELECT {col_cod}, {col_ev} FROM cotizaciones WHERE TRIM(UPPER({col_cli})) = TRIM(UPPER(%s)) AND status = 'Aprobada' ORDER BY id DESC"
                    cursor.execute(query, (cliente,))
                    for r in cursor.fetchall():
                        eventos_cliente.append(f"{r[0]} | {r[1]}")
            except Exception as e: 
                print("Error al filtrar cliente:", e)
            finally: 
                liberar_conexion(conn)
                
            self.main_root.after(0, lambda: self._aplicar_datos_cliente(ruc_db, det_db, eventos_cliente))

        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_datos_cliente(self, ruc_db, det_db, eventos_cliente):
        if not hasattr(self, 'ent_detraccion'): return
        self.ent_detraccion.configure(state="normal")
        if "Factura" in getattr(self.combo_tipo, 'get', lambda: "")():
            self.ent_detraccion.delete(0, tk.END)
            self.ent_detraccion.insert(0, det_db)
            
        if not getattr(self, 'bloquear_autocompletado_ruc', False) and ruc_db:
            self.ent_ruc.configure(state="normal")
            self.ent_ruc.delete(0, tk.END)
            self.ent_ruc.insert(0, str(ruc_db))
            
        if hasattr(self, 'actualizar_totales'):
            self.actualizar_totales()

        if hasattr(self, 'combo_evento'):
            self.combo_evento.configure(values=eventos_cliente)
            if self.combo_evento.get() not in eventos_cliente:
                self.combo_evento.set("GENERAL / NO ASIGNADO")

    def cargar_clientes_bd(self):
        clis = getattr(cache_sistema, 'clientes_nombres', [])
        if clis:
            self._aplicar_clientes(clis)
        else:
            def tarea():
                conn = conectar_db(silencioso=True)
                res_clis = []
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT nombre_empresa FROM clientes ORDER BY nombre_empresa ASC")
                        res_clis = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]
                        cache_sistema.guardar('clientes_nombres', res_clis)
                    except: pass
                    finally: liberar_conexion(conn)
                self.main_root.after(0, lambda: self._aplicar_clientes(res_clis))
            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_clientes(self, clis):
        if not hasattr(self, 'combo_cliente'): return
        if clis:
            lista_clis = ["--- Seleccione Cliente ---"] + clis
            self.combo_cliente.configure(values=lista_clis)
            if self.combo_cliente.get() not in lista_clis:
                self.combo_cliente.set("--- Seleccione Cliente ---")
            self.al_seleccionar_cliente()
        else:
            self.combo_cliente.configure(values=["--- Seleccione Cliente ---"])
            self.combo_cliente.set("--- Seleccione Cliente ---")

    def cargar_eventos_aprobados(self):
        evs = getattr(cache_sistema, 'eventos_aprobados', [])
        lista_evs = ["GENERAL / NO ASIGNADO"] + (evs if evs else [])
        if hasattr(self, 'combo_evento'):
            self.combo_evento.configure(values=lista_evs)
            if self.combo_evento.get() not in lista_evs:
                self.combo_evento.set("GENERAL / NO ASIGNADO")

    def actualizar_totales(self, *args):
        if not hasattr(self, 'combo_tipo') or not hasattr(self, 'ent_subtotal') or not hasattr(self, 'ent_detraccion'):
            return
            
        tipo = self.combo_tipo.get()
        try:
            sub = float(self.ent_subtotal.get() or 0)
            ui_pct = float(self.ent_detraccion.get() or 0)
            if "Factura" in tipo:
                igv = sub * 0.18; tot = sub + igv; det = tot * (ui_pct / 100.0); neto = tot - det
                self.lbl_impuesto.configure(text=f"IGV (18%): {formatear_moneda(igv)}")
                self.lbl_detraccion.configure(text=f"Detracción ({ui_pct:g}%): -{formatear_moneda(det)}")
                self.lbl_total.configure(text=f"Neto a Cobrar: {formatear_moneda(neto)}")
            elif "Recibo" in tipo:
                ret = sub * (ui_pct / 100.0); neto = sub - ret
                self.lbl_impuesto.configure(text=f"Retención ({ui_pct:g}%): -{formatear_moneda(ret)}")
                self.lbl_detraccion.configure(text=f"Detracción (0%): -{formatear_moneda(0)}")
                self.lbl_total.configure(text=f"Neto a Cobrar: {formatear_moneda(neto)}")
        except ValueError: pass

    def guardar_registro(self):
        ruta_base = obtener_ruta_base_drive()
        if not ruta_base:
            messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar registros y archivos.")
            return
            
        tipo = self.combo_tipo.get()
        nro_doc = self.ent_nro_doc.get().strip()
        fecha = self.ent_fecha.get().strip()
        cliente = self.combo_cliente.get().strip()
        desc = self.ent_desc.get().strip()
        evento = self.combo_evento.get()
        oc_sel = self.combo_oc.get()
        
        if not cliente or cliente == "--- Seleccione Cliente ---" or not desc: 
            return messagebox.showwarning("Atención", "Llene los campos obligatorios (Cliente y Concepto).")
            
        if oc_sel == "--- Sin Orden de Compra ---":
            oc_sel = ""
            
        if not oc_sel and "Factura" in tipo:
            if not messagebox.askyesno("Falta Orden de Compra", "⚠️ No ha seleccionado una Orden de Compra para esta factura.\n\n¿Desea emitir la factura de todas formas sin asociarla a una Orden de Compra?"):
                return
        
        try: 
            subtotal = float(self.ent_subtotal.get() or 0)
            dias = int(self.ent_dias.get().strip() or 0)
            ui_pct = float(self.ent_detraccion.get() or 0)
        except ValueError: return messagebox.showerror("Error", "Los montos deben ser numéricos.")

        if "Factura" in tipo: 
            imp = subtotal * 0.18; tot_bruto = subtotal + imp; det_pct = ui_pct; det_monto = tot_bruto * (det_pct / 100.0)
            neto_nuevo = tot_bruto - det_monto
        elif "Recibo" in tipo: 
            imp = subtotal * (ui_pct / 100.0); tot_bruto = subtotal; det_pct = 0.0; det_monto = 0.0
            neto_nuevo = subtotal - imp  
        else: 
            imp = 0.0; tot_bruto = subtotal; det_pct = ui_pct; det_monto = tot_bruto * (det_pct / 100.0)
            neto_nuevo = tot_bruto - det_monto

        conn = obtener_conexion_segura()
        if not conn: return
        try:
            cursor = conn.cursor()
            
            if nro_doc:
                cursor.execute("SELECT COUNT(*) FROM facturas_emitidas WHERE numero_documento = %s", (nro_doc,))
                if cursor.fetchone()[0] > 0:
                    liberar_conexion(conn)
                    return messagebox.showwarning("Duplicado", "Ese N° de Documento ya está registrado.")

            cli_match = cliente.strip().upper()
            cursor.execute("SELECT limite_credito FROM clientes WHERE TRIM(UPPER(nombre_empresa)) = %s", (cli_match,))
            res_cli = cursor.fetchone()
            limite_credito = float(res_cli[0]) if (res_cli and res_cli[0] is not None) else 0.0
            
            if limite_credito > 0.0:
                cursor.execute("""
                    SELECT id, total, COALESCE(det_monto, 0), tipo_documento, COALESCE(impuesto, 0)
                    FROM facturas_emitidas WHERE TRIM(UPPER(cliente)) = %s
                """, (cli_match,))
                
                facturas_historicas = cursor.fetchall()
                deuda_actual = 0.0
                
                for r in facturas_historicas:
                    id_fac, tot_b, det_m, t_doc, imp_val = r
                    tot_val = float(tot_b) if tot_b else 0.0
                    det_val = float(det_m) if det_m else 0.0
                    impuesto_val = float(imp_val) if imp_val else 0.0
                    
                    if t_doc and "Recibo" in t_doc and "8%" in t_doc:
                        neto_fac = tot_val - impuesto_val - det_val
                    else:
                        neto_fac = tot_val - det_val
                    
                    cursor.execute("SELECT SUM(monto_pagado) FROM pagos_clientes WHERE id_factura = %s", (id_fac,))
                    pagado = cursor.fetchone()[0]
                    pagado_val = float(pagado) if pagado else 0.0
                    
                    saldo = max(0.0, neto_fac - pagado_val)
                    deuda_actual += saldo
                
                deuda_proyectada = deuda_actual + neto_nuevo
                
                if deuda_proyectada > limite_credito:
                    msg = (f"⚠️ LÍMITE DE CRÉDITO EXCEDIDO\n\n"
                           f"Cliente: {cliente}\n"
                           f"Límite Asignado: {formatear_moneda(limite_credito)}\n"
                           f"Deuda Actual Pendiente: {formatear_moneda(deuda_actual)}\n\n"
                           f"Nueva Facturación: {formatear_moneda(neto_nuevo)}\n"
                           f"Deuda Proyectada: {formatear_moneda(deuda_proyectada)}\n\n"
                           f"El sistema NO permite registrar esta venta hasta que el cliente regularice sus pagos.")
                    liberar_conexion(conn)
                    return messagebox.showerror("Bloqueo por Crédito", msg)

            ruta_final = ""
            if self.ruta_archivo_temp:
                try:
                    carpeta_destino = os.path.join(ruta_base, "facturas_emitidas")
                    if not os.path.exists(carpeta_destino): os.makedirs(carpeta_destino)
                    nombre_ext = os.path.splitext(self.ruta_archivo_temp)[1]
                    ruta_final = os.path.join(carpeta_destino, f"Emitida_{datetime.now().strftime('%Y%m%d%H%M%S')}_{cliente.replace(' ', '_')}{nombre_ext}")
                    shutil.copy2(self.ruta_archivo_temp, ruta_final)
                except Exception as e: 
                    liberar_conexion(conn)
                    return messagebox.showerror("Error", f"Fallo al guardar archivo:\n{e}")

            cursor.execute("""
                INSERT INTO facturas_emitidas (tipo_documento, numero_documento, fecha, cliente, descripcion, evento_asociado, subtotal, impuesto, total, archivo_ruta, dias_credito, det_porcentaje, det_monto, orden_compra)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (tipo, nro_doc, fecha, cliente, desc, evento, subtotal, imp, tot_bruto, ruta_final, dias, det_pct, det_monto, oc_sel))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.app_padre.usuario_activo, "Facturas Emitidas", f"Registró factura {nro_doc} del cliente '{cliente}'")
            messagebox.showinfo("Éxito", "Documento emitido registrado correctamente.")
            
            self.ent_desc.delete(0, tk.END)
            self.ent_subtotal.delete(0, tk.END)
            self.ruta_archivo_temp = ""
            self.combo_oc.set("--- Sin Orden de Compra ---")
            self.cargar_datos_tabla(reset_pagina=True)
            self.sugerir_correlativo()
            
            if hasattr(self.app_padre, 'app_cobros'):
                self.app_padre.app_cobros.cargar_datos_cobrar(reset_pagina=True)
            if hasattr(self.app_padre, 'app_nc'):
                self.app_padre.app_nc.cargar_datos_nc(reset_pagina=True)
        except Exception as e: messagebox.showerror("Error SQL", str(e))
        finally: liberar_conexion(conn)

    def _on_buscar_facturas_keyrelease(self, event=None):
        if self._id_debounce_busqueda is not None:
            try: self.main_root.after_cancel(self._id_debounce_busqueda)
            except Exception: pass
        self._id_debounce_busqueda = self.main_root.after(350, lambda: self.cargar_datos_tabla(reset_pagina=True))

    def cargar_datos_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = ""
        if hasattr(self, 'ent_buscar_facturas'):
            filtro = self.ent_buscar_facturas.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        self._token_carga_tabla = getattr(self, '_token_carga_tabla', 0) + 1
        token_actual = self._token_carga_tabla

        clave_cache = f"fac_em_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla_facturas(datos, token_actual)
        else:
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", "", ""))
            
            threading.Thread(
                target=self._worker_cargar_datos_tabla,
                args=(filtro, token_actual, offset, clave_cache),
                daemon=True
            ).start()

    def _worker_cargar_datos_tabla(self, filtro, token_actual, offset, clave_cache):
        filas_resultado = []
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if not conn:
            self.main_root.after(0, lambda: self._pintar_tabla_facturas([], token_actual))
            return
        try:
            cursor = conn.cursor()
            if filtro == "":
                cursor.execute("SELECT id, fecha, numero_documento, dias_credito, tipo_documento, cliente, evento_asociado, descripcion, subtotal, impuesto, total, COALESCE(det_monto, 0), archivo_ruta, enlace_pdf_sunat, estado_sunat FROM facturas_emitidas ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
            else:
                val = f"%{filtro}%"
                cursor.execute("""
                    SELECT id, fecha, numero_documento, dias_credito, tipo_documento, cliente, evento_asociado, descripcion, subtotal, impuesto, total, COALESCE(det_monto, 0), archivo_ruta, enlace_pdf_sunat, estado_sunat 
                    FROM facturas_emitidas 
                    WHERE numero_documento ILIKE %s OR cliente ILIKE %s OR evento_asociado ILIKE %s
                    ORDER BY id DESC LIMIT %s OFFSET %s
                """, (val, val, val, self.registros_por_pagina, offset))

            contador = (self.pagina_actual - 1) * self.registros_por_pagina + 1
            for r in cursor.fetchall():
                tiene_arch = "✅ Ver" if r[12] else "❌ No"
                tipo_doc = r[4]; impuesto = r[9]; tot_bruto = r[10]; det_monto = r[11]
                if "Recibo" in tipo_doc and "8%" in tipo_doc: neto = tot_bruto - impuesto - det_monto
                else: neto = tot_bruto - det_monto

                estado_db = r[14]
                if estado_db and "Anulada" in estado_db:
                    estado_sunat_str = "❌ Anulada"
                else:
                    estado_sunat_str = "✅ Emitido" if r[13] else "⏳ Local"

                row_vals = (
                    contador, r[0], r[1], r[2] if r[2] else "-", r[3], tipo_doc.split(" ")[0], r[5], r[7],
                    r[6].split(" | ")[0] if " | " in r[6] else r[6], r[8], formatear_moneda(impuesto), formatear_moneda(tot_bruto), formatear_moneda(det_monto), formatear_moneda(neto), estado_sunat_str, tiene_arch
                )

                filas_resultado.append(row_vals)
                contador += 1
                
            cache_sistema.guardar(clave_cache, filas_resultado)
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

        self.main_root.after(0, lambda: self._pintar_tabla_facturas(filas_resultado, token_actual))

    def _pintar_tabla_facturas(self, filas_resultado, token_actual):
        if token_actual != getattr(self, '_token_carga_tabla', token_actual):
            return
        for item in self.tabla.get_children(): self.tabla.delete(item)
        for row_vals in filas_resultado:
            self.tabla.insert("", tk.END, values=row_vals)
            
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1:
                self.btn_ant.configure(state="normal")
            else:
                self.btn_ant.configure(state="disabled")
                
            if len(filas_resultado) == self.registros_por_pagina:
                self.btn_sig.configure(state="normal")
            else:
                self.btn_sig.configure(state="disabled")

    def abrir_archivo_desde_tabla(self, event):
        sel = self.tabla.selection()
        if not sel: return
        id_doc = self.tabla.item(sel[0], "values")[1] 
        conn = obtener_conexion_segura()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT enlace_pdf_sunat, archivo_ruta FROM facturas_emitidas WHERE id = %s", (id_doc,))
            res = cursor.fetchone()
            if res:
                enlace_pdf = res[0]
                ruta_local = res[1]
                if enlace_pdf and str(enlace_pdf).startswith("http"):
                    webbrowser.open(enlace_pdf)
                elif ruta_local and os.path.exists(ruta_local):
                    abrir_documento(ruta_local)
                else:
                    messagebox.showinfo("Aviso", "Este comprobante aún no tiene PDF asociado.")
        except Exception: pass
        finally: liberar_conexion(conn)

    def abrir_ventana_edicion(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Atención", "Seleccione un documento de la lista para gestionar.")
        
        valores = self.tabla.item(sel[0], "values")
        id_doc = valores[1]
        cli_str = valores[6]
        
        conn = obtener_conexion_segura()
        if not conn: return
        cursor = conn.cursor()
        cursor.execute("SELECT tipo_documento, numero_documento, fecha, cliente, descripcion, evento_asociado, subtotal, dias_credito, COALESCE(det_porcentaje, 0), impuesto, total, enlace_pdf_sunat, estado_sunat, orden_compra FROM facturas_emitidas WHERE id = %s", (id_doc,))
        reg = cursor.fetchone()
        
        cursor.execute("SELECT ruc, direccion_fiscal, correo FROM clientes WHERE TRIM(UPPER(nombre_empresa)) = TRIM(UPPER(%s))", (cli_str,))
        res_cli = cursor.fetchone()
        ruc_cliente = res_cli[0] if res_cli and res_cli[0] else ""
        dir_cliente = res_cli[1] if res_cli and res_cli[1] else "-"
        correo_cliente = res_cli[2] if res_cli and res_cli[2] else ""
        liberar_conexion(conn)
        
        if not reg: return
        enlace_sunat = reg[11]
        estado_sunat_db = reg[12]
        
        ya_anulado = False
        if estado_sunat_db and "Anulada" in str(estado_sunat_db):
            ya_anulado = True

        v_edit = ctk.CTkToplevel(self.main_root)
        v_edit.title("Gestión de Documento Emitido")
        v_edit.geometry("450x450") 
        v_edit.transient(self.main_root)
        v_edit.grab_set()

        ctk.CTkLabel(v_edit, text=f"Gestión del Registro ID: {id_doc}", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))

        fecha_em = datetime.now().strftime("%Y-%m-%d")
        if reg[2]:
            try: fecha_em = datetime.strptime(reg[2], "%d/%m/%Y").strftime("%Y-%m-%d")
            except: pass

        datos_factura = {
            "id_factura_bd": id_doc,
            "tipo_doc": reg[0],
            "serie": str(reg[1]).split("-")[0] if "-" in str(reg[1]) else "F001",
            "numero": str(reg[1]).split("-")[1] if "-" in str(reg[1]) else "1",
            "ruc_dni_cliente": ruc_cliente,
            "nombre_cliente": reg[3],
            "direccion_cliente": dir_cliente,
            "correo_cliente": correo_cliente,
            "fecha_emision": fecha_em,
            "moneda": "Soles",
            "subtotal": float(reg[6]) if reg[6] else 0.0,
            "impuesto": float(reg[9]) if reg[9] else 0.0,
            "total": float(reg[10]) if len(reg) > 10 and reg[10] else float(reg[6] or 0) + float(reg[9] or 0),
            "descripcion": reg[4]
        }

        f_docs = ctk.CTkFrame(v_edit, fg_color="#f0f0f0", border_width=1, border_color="#e0e0e0")
        f_docs.pack(fill="x", padx=30, pady=10, ipadx=5, ipady=5)
        ctk.CTkLabel(f_docs, text="📄 Documentos Asociados:", font=("Arial", 11, "bold")).pack(pady=5)

        def descargar_pdf_sunat():
            conn = obtener_conexion_segura(parent=v_edit)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT enlace_pdf_sunat, archivo_ruta FROM facturas_emitidas WHERE id = %s", (id_doc,))
                res = cursor.fetchone()
                if res:
                    link_pdf, ruta_local = res[0], res[1]
                    if ruta_local and os.path.exists(ruta_local):
                        abrir_documento(ruta_local)
                    elif link_pdf and str(link_pdf).startswith("http"):
                        webbrowser.open(link_pdf)
                    else:
                        messagebox.showinfo("Aviso", "Este comprobante aún no tiene PDF asociado.", parent=v_edit)
            except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)
            finally: liberar_conexion(conn)

        def abrir_oc_asociada(numero_oc):
            conn = obtener_conexion_segura(parent=v_edit)
            if not conn: return
            try:
                c = conn.cursor()
                c.execute("SELECT archivo_ruta FROM ordenes_compra_clientes WHERE numero_oc = %s", (numero_oc,))
                res = c.fetchone()
                if res and res[0] and os.path.exists(res[0]):
                    abrir_documento(res[0])
                else:
                    messagebox.showinfo("Aviso", "No se encontró el PDF de esta Orden de Compra.\n\nEs probable que esta orden se haya registrado manualmente sin archivo adjunto, o que no se haya realizado.", parent=v_edit)
            except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)
            finally: liberar_conexion(conn)

        def abrir_cotizacion_asociada(evento_str):
            codigo_cot = evento_str.split(" | ")[0].strip()
            ruta_base = obtener_ruta_base_drive()
            
            if not ruta_base:
                messagebox.showwarning("Aviso", "Configure la ruta de Google Drive en Ajustes.", parent=v_edit)
                return
                
            nombre_esperado = f"Cotizacion_{codigo_cot}"
            
            carpetas_rapidas = [
                os.path.join(ruta_base, "Cotizaciones"),
                os.path.join(ruta_base, "cotizaciones"),
                ruta_base
            ]
            
            for c in carpetas_rapidas:
                if os.path.exists(c):
                    for archivo in os.listdir(c):
                        if archivo.startswith(nombre_esperado) and archivo.lower().endswith(".pdf"):
                            abrir_documento(os.path.join(c, archivo))
                            return

            for root_dir, dirs, files in os.walk(ruta_base):
                for archivo in files:
                    if archivo.startswith(nombre_esperado) and archivo.lower().endswith(".pdf"):
                        abrir_documento(os.path.join(root_dir, archivo))
                        return
                        
            messagebox.showinfo("Aviso", f"No se encontró el PDF '{nombre_esperado}.pdf' en su Google Drive.", parent=v_edit)

        ctk.CTkButton(f_docs, text="Ver Factura (Local/SUNAT)", fg_color="#27ae60", hover_color="#1e8449", height=28, command=descargar_pdf_sunat).pack(fill="x", padx=10, pady=2)
        
        if reg[13]: 
            ctk.CTkButton(f_docs, text=f"Ver Orden de Compra: {reg[13]}", fg_color="#d35400", hover_color="#a84300", height=28, command=lambda: abrir_oc_asociada(reg[13])).pack(fill="x", padx=10, pady=2)
            
        if reg[5] and "GENERAL" not in reg[5]: 
            ctk.CTkButton(f_docs, text="Ver Cotización Asociada", fg_color="#8e44ad", hover_color="#732d91", height=28, command=lambda: abrir_cotizacion_asociada(reg[5])).pack(fill="x", padx=10, pady=2)

        def modificar_datos_registro():
            v_edit.destroy()
            v_mod = ctk.CTkToplevel(self.main_root)
            v_mod.title(f"Modificar Registro ID: {id_doc}")
            v_mod.geometry("400x720")
            v_mod.transient(self.main_root)
            v_mod.grab_set()

            ctk.CTkLabel(v_mod, text="Modificar Datos del Documento", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))

            f_cont = ctk.CTkScrollableFrame(v_mod, fg_color="transparent")
            f_cont.pack(fill="both", expand=True, padx=20, pady=5)

            lbl_tit_det = ctk.CTkLabel(f_cont, text="Detracción (%):", font=("Arial", 11, "bold"))
            
            def toggle_detraccion_edit(*args):
                tipo_val = cmb_tipo.get()
                if "Recibo" in tipo_val:
                    lbl_tit_det.configure(text="Retención (%):")
                    if "8%" in tipo_val:
                        ent_det.delete(0, tk.END); ent_det.insert(0, "8")
                    else:
                        ent_det.delete(0, tk.END); ent_det.insert(0, "0")
                else:
                    lbl_tit_det.configure(text="Detracción (%):")

            ctk.CTkLabel(f_cont, text="Tipo de Documento:", font=("Arial", 11, "bold")).pack(anchor="w")
            cmb_tipo = ctk.CTkComboBox(f_cont, values=["Factura (18% IGV)", "Boleta (Sin IGV)", "Recibo por Honorarios (8% Retención)", "Recibo por Honorarios (Sin Retención)"], state="readonly", command=toggle_detraccion_edit)
            cmb_tipo.pack(fill="x", pady=(0, 10))
            cmb_tipo.set(reg[0])

            ctk.CTkLabel(f_cont, text="N° de Documento:", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_nro = ctk.CTkEntry(f_cont)
            ent_nro.pack(fill="x", pady=(0, 10))
            ent_nro.insert(0, reg[1] if reg[1] else "")

            ctk.CTkLabel(f_cont, text="Fecha (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_f = ctk.CTkEntry(f_cont)
            ent_f.pack(fill="x", pady=(0, 10))
            ent_f.insert(0, reg[2])

            ctk.CTkLabel(f_cont, text="Días de Crédito:", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_d = ctk.CTkEntry(f_cont)
            ent_d.pack(fill="x", pady=(0, 10))
            ent_d.insert(0, str(reg[7]))

            ctk.CTkLabel(f_cont, text="Cliente:", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_c = ctk.CTkEntry(f_cont)
            ent_c.pack(fill="x", pady=(0, 10))
            ent_c.insert(0, reg[3])

            ctk.CTkLabel(f_cont, text="Concepto / Descripción:", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_desc = ctk.CTkEntry(f_cont)
            ent_desc.pack(fill="x", pady=(0, 10))
            ent_desc.insert(0, reg[4])

            conn3 = conectar_db(silencioso=True)
            eventos_lista = ["GENERAL / NO ASIGNADO"]
            if conn3:
                try:
                    c3 = conn3.cursor()
                    c3.execute("SELECT * FROM cotizaciones LIMIT 0")
                    cols = [desc[0] for desc in c3.description]
                    col_cod = 'codigo_cotizacion' if 'codigo_cotizacion' in cols else ('codigo' if 'codigo' in cols else None)
                    col_cli = 'nombre_empresa' if 'nombre_empresa' in cols else ('cliente_nombre' if 'cliente_nombre' in cols else ('cliente' if 'cliente' in cols else None))
                    col_ev = 'nombre_evento' if 'nombre_evento' in cols else ('evento' if 'evento' in cols else None)

                    if col_cod and col_cli and col_ev:
                        query = f"SELECT {col_cod}, {col_ev} FROM cotizaciones WHERE TRIM(UPPER({col_cli})) = TRIM(UPPER(%s)) AND status = 'Aprobada' ORDER BY id DESC"
                        c3.execute(query, (reg[3],))
                        for r_ev in c3.fetchall():
                            eventos_lista.append(f"{r_ev[0]} | {r_ev[1]}")
                except: pass
                finally: liberar_conexion(conn3)
                
            if reg[5] and reg[5] not in eventos_lista:
                eventos_lista.append(reg[5])

            ctk.CTkLabel(f_cont, text="Evento Asociado:", font=("Arial", 11, "bold")).pack(anchor="w")
            cmb_ev = ctk.CTkComboBox(f_cont, values=eventos_lista, state="readonly")
            cmb_ev.pack(fill="x", pady=(0, 10))
            cmb_ev.set(reg[5] if reg[5] else "GENERAL / NO ASIGNADO")
            
            conn = conectar_db(silencioso=True)
            ocs_lista = ["--- Sin Orden de Compra ---"]
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("SELECT numero_oc FROM ordenes_compra_clientes ORDER BY id DESC")
                    for r_oc in c.fetchall(): ocs_lista.append(r_oc[0])
                except: pass
                finally: liberar_conexion(conn)
                
            ctk.CTkLabel(f_cont, text="Orden de Compra:", font=("Arial", 11, "bold"), text_color="#166534").pack(anchor="w")
            cmb_oc = ctk.CTkComboBox(f_cont, values=ocs_lista, state="readonly")
            cmb_oc.pack(fill="x", pady=(0, 10))
            if reg[13]: cmb_oc.set(reg[13])
            else: cmb_oc.set("--- Sin Orden de Compra ---")

            ctk.CTkLabel(f_cont, text="Monto Base (Subtotal):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_s = ctk.CTkEntry(f_cont)
            ent_s.pack(fill="x", pady=(0, 10))
            ent_s.insert(0, str(reg[6]))

            lbl_tit_det.pack(anchor="w")
            ent_det = ctk.CTkEntry(f_cont)
            ent_det.pack(fill="x", pady=(0, 10))
            
            if "Recibo" in reg[0]:
                lbl_tit_det.configure(text="Retención (%):")
                if reg[6] > 0 and reg[9] > 0:
                    pct = (float(reg[9]) / float(reg[6])) * 100
                    ent_det.insert(0, f"{pct:g}")
                else:
                    ent_det.insert(0, "0")
            else:
                lbl_tit_det.configure(text="Detracción (%):")
                ent_det.insert(0, str(reg[8]))

            def guardar_cambios():
                tipo = cmb_tipo.get()
                nro_doc = ent_nro.get().strip()
                fecha = ent_f.get().strip()
                cli = ent_c.get().strip()
                desc = ent_desc.get().strip()
                evento = cmb_ev.get()
                oc_sel = cmb_oc.get()
                if oc_sel == "--- Sin Orden de Compra ---": oc_sel = ""
                
                try:
                    sub = float(ent_s.get())
                    dias = int(ent_d.get())
                    ui_pct = float(ent_det.get() if ent_det.get() else 0)
                except ValueError:
                    messagebox.showerror("Error", "Monto, días y porcentaje deben ser numéricos.", parent=v_mod)
                    return

                if "Factura" in tipo: 
                    imp = sub * 0.18; tot_bruto = sub + imp; det_pct = ui_pct; det_monto = tot_bruto * (det_pct / 100.0)
                    neto_nuevo = tot_bruto - det_monto
                elif "Recibo" in tipo: 
                    imp = sub * (ui_pct / 100.0); tot_bruto = sub; det_pct = 0.0; det_monto = 0.0
                    neto_nuevo = sub - imp
                else: 
                    imp = 0.0; tot_bruto = sub; det_pct = ui_pct; det_monto = tot_bruto * (det_pct / 100.0)
                    neto_nuevo = tot_bruto - det_monto

                conn2 = obtener_conexion_segura(parent=v_mod)
                if not conn2: return
                try:
                    c2 = conn2.cursor()
                    
                    if nro_doc:
                        c2.execute("SELECT COUNT(*) FROM facturas_emitidas WHERE numero_documento = %s AND id != %s", (nro_doc, id_doc))
                        if c2.fetchone()[0] > 0:
                            liberar_conexion(conn2)
                            return messagebox.showwarning("Duplicado", f"El N° de Documento '{nro_doc}' ya está en uso.", parent=v_mod)

                    cli_match = cli.strip().upper()
                    c2.execute("SELECT limite_credito FROM clientes WHERE TRIM(UPPER(nombre_empresa)) = %s", (cli_match,))
                    res_cli = c2.fetchone()
                    limite_credito = float(res_cli[0]) if (res_cli and res_cli[0] is not None) else 0.0
                    
                    if limite_credito > 0.0:
                        c2.execute("""
                            SELECT id, total, COALESCE(det_monto, 0), tipo_documento, COALESCE(impuesto, 0)
                            FROM facturas_emitidas WHERE TRIM(UPPER(cliente)) = %s
                        """, (cli_match,))
                        
                        facturas_historicas = c2.fetchall()
                        deuda_actual = 0.0
                        
                        for r in facturas_historicas:
                            f_id, tot_b, det_m, t_doc, imp_val = r
                            if str(f_id) == str(id_doc): continue
                            
                            tot_val = float(tot_b) if tot_b else 0.0
                            det_val = float(det_m) if det_m else 0.0
                            impuesto_val = float(imp_val) if imp_val else 0.0
                            
                            if t_doc and "Recibo" in t_doc and "8%" in t_doc: neto_fac = tot_val - impuesto_val - det_val
                            else: neto_fac = tot_val - det_val
                            
                            c2.execute("SELECT SUM(monto_pagado) FROM pagos_clientes WHERE id_factura = %s", (f_id,))
                            pagado = c2.fetchone()[0]
                            pagado_val = float(pagado) if pagado else 0.0
                            
                            saldo = max(0.0, neto_fac - pagado_val)
                            deuda_actual += saldo
                        
                        c2.execute("SELECT SUM(monto_pagado) FROM pagos_clientes WHERE id_factura = %s", (id_doc,))
                        pag_esta = c2.fetchone()[0]
                        pagado_esta_val = float(pag_esta) if pag_esta else 0.0
                        
                        saldo_nuevo_esta = max(0.0, neto_nuevo - pagado_esta_val)
                        deuda_proyectada = deuda_actual + saldo_nuevo_esta
                        
                        if deuda_proyectada > limite_credito:
                            msg = (f"⚠️ LÍMITE DE CRÉDITO EXCEDIDO\n\n"
                                   f"Al modificar esta factura, se excede el límite del cliente.\n"
                                   f"Límite Asignado: {formatear_moneda(limite_credito)}\n"
                                   f"Deuda de otras Facturas: {formatear_moneda(deuda_actual)}\n"
                                   f"Deuda Proyectada Total: {formatear_moneda(deuda_proyectada)}\n")
                            liberar_conexion(conn2)
                            return messagebox.showerror("Bloqueo por Crédito", msg, parent=v_mod)


                    c2.execute("UPDATE facturas_emitidas SET tipo_documento=%s, numero_documento=%s, fecha=%s, cliente=%s, descripcion=%s, evento_asociado=%s, subtotal=%s, impuesto=%s, total=%s, dias_credito=%s, det_porcentaje=%s, det_monto=%s, orden_compra=%s WHERE id=%s",
                               (tipo, nro_doc, fecha, cli, desc, evento, sub, imp, tot_bruto, dias, det_pct, det_monto, oc_sel, id_doc))
                    conn2.commit()
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Facturas Emitidas", f"Modificó la factura ID {id_doc}")
                    messagebox.showinfo("Éxito", "Registro modificado correctamente.", parent=v_mod)
                    v_mod.destroy()
                    self.cargar_datos_tabla(reset_pagina=True)
                    if hasattr(self.app_padre, 'app_cobros'): self.app_padre.app_cobros.cargar_datos_cobrar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error SQL", str(e), parent=v_mod)
                finally: liberar_conexion(conn2)

            ctk.CTkButton(f_cont, text="💾 Guardar Cambios", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=guardar_cambios).pack(fill="x", pady=20)

        def eliminar_registro():
            if messagebox.askyesno("Confirmar Eliminación", "⚠️ ¿Desea eliminar completamente este registro?\n\nSe borrará de la base de datos y el archivo físico asociado.", parent=v_edit):
                try:
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM facturas_emitidas WHERE id = %s", (id_doc,))
                    row = cursor.fetchone()
                    ruta_archivo = row[0]
                    if ruta_archivo and os.path.exists(ruta_archivo): os.remove(ruta_archivo)
                    cursor.execute("DELETE FROM facturas_emitidas WHERE id = %s", (id_doc,))
                    conn.commit()
                    
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Facturas Emitidas", f"Eliminó completamente la factura ID {id_doc}")
                    messagebox.showinfo("Éxito", "Registro eliminado.", parent=v_edit)
                    v_edit.destroy()
                    self.cargar_datos_tabla(reset_pagina=True)
                    if hasattr(self.app_padre, 'app_cobros'): self.app_padre.app_cobros.cargar_datos_cobrar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)
                finally: liberar_conexion(conn)

        def emitir_sunat():
            if not abrir_ventana_emision_sunat:
                return messagebox.showerror("Error", "El módulo 'facturacion_sunat.py' no se encuentra disponible.", parent=v_edit)
                
            def accion_post_otp():
                abrir_ventana_emision_sunat(v_edit, datos_factura, self.app_padre.usuario_activo, callback_exito=lambda: self.cargar_datos_tabla(reset_pagina=True))

            solicitar_otp(f"Emitir comprobante {reg[1]} a SUNAT", accion_post_otp, v_edit)

        def procesar_nota_credito():
            v_nc = ctk.CTkToplevel(v_edit)
            v_nc.title("Emitir Nota de Crédito")
            v_nc.geometry("400x250")
            v_nc.grab_set()

            ctk.CTkLabel(v_nc, text="⚠️ Anular Factura vía Nota de Crédito", font=("Arial", 14, "bold"), text_color="#c0392b").pack(pady=(15, 10))
            ctk.CTkLabel(v_nc, text="Seleccione el motivo legal para SUNAT:", font=("Arial", 11)).pack(anchor="w", padx=20)
            
            motivos = ["Anulación de la operación", "Anulación por error en el RUC", "Devolución total", "Devolución por ítem"]
            cmb_motivo = ctk.CTkComboBox(v_nc, values=motivos, width=320)
            cmb_motivo.pack(pady=10)

            def confirmar_nc():
                motivo_sel = cmb_motivo.get()
                v_nc.destroy()
                
                def accion_post_otp():
                    datos_nc = datos_factura.copy()
                    datos_nc["tipo_emision"] = "nota_credito"
                    datos_nc["motivo_nc"] = motivo_sel
                    
                    if abrir_ventana_emision_sunat:
                        def callback_ambos():
                            self.cargar_datos_tabla(reset_pagina=True)
                            if hasattr(self.app_padre, 'app_nc'):
                                self.app_padre.app_nc.cargar_datos_nc(reset_pagina=True)
                        
                        abrir_ventana_emision_sunat(v_edit, datos_nc, self.app_padre.usuario_activo, callback_exito=callback_ambos)
                    else:
                        messagebox.showerror("Error", "Módulo de facturación inactivo.")

                solicitar_otp(f"Emitir Nota Crédito para la Fac. {reg[1]}", accion_post_otp, v_edit)

            ctk.CTkButton(v_nc, text="✅ Generar Nota de Crédito", fg_color="#c0392b", hover_color="#922b21", font=("Arial", 12, "bold"), command=confirmar_nc).pack(pady=15)

        if not enlace_sunat: 
            ctk.CTkButton(v_edit, text="⚡ Emitir Comprobante a SUNAT", font=("Arial", 12, "bold"), fg_color="#d35400", hover_color="#a84300", command=emitir_sunat).pack(fill="x", padx=30, pady=5)
            ctk.CTkButton(v_edit, text="✏️ Modificar Datos del Registro", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=modificar_datos_registro).pack(fill="x", padx=30, pady=5)
            ctk.CTkButton(v_edit, text="❌ Eliminar Registro Completo", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_registro).pack(fill="x", padx=30, pady=(5, 10))
        elif ya_anulado:
            ctk.CTkLabel(v_edit, text="❌ Documento Anulado", font=("Arial", 14, "bold"), text_color="#c0392b").pack(pady=5)
        else:
            ctk.CTkButton(v_edit, text="⚠️ Generar Nota de Crédito / Anular", font=("Arial", 12, "bold"), fg_color="#c0392b", hover_color="#922b21", command=procesar_nota_credito).pack(fill="x", padx=30, pady=(5, 10))

# =========================================================
# PESTAÑA 2: CUENTAS POR COBRAR (COBROS Y DEUDAS)
# =========================================================
class CuentasPorCobrarTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        self.orden_columnas = {}
        self._id_debounce_busqueda = None
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_entorno()
        self.crear_interfaz()

    def inicializar_entorno(self):
        if _SCHEMA_OK.get("pagos_clientes"):
            return

        def tarea_init():
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pagos_clientes (
                        id SERIAL PRIMARY KEY, codigo_cotizacion VARCHAR(255), monto_pagado NUMERIC, 
                        archivo_ruta TEXT, cliente_nombre VARCHAR(255), fecha_pago VARCHAR(50),
                        id_factura INTEGER DEFAULT 0
                    )
                """)
                conn.commit()
                _SCHEMA_OK["pagos_clientes"] = True
            except Exception: pass
            finally: liberar_conexion(conn)
            
        threading.Thread(target=tarea_init, daemon=True).start()

    def crear_interfaz(self):
        frame_acciones = ctk.CTkFrame(self.tab_frame, corner_radius=8, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        frame_acciones.pack(fill="x", padx=15, pady=(10, 10), ipady=5)

        btn_resumen_cli = ctk.CTkButton(frame_acciones, text="📊 Resumen por Cliente", font=("Arial", 12, "bold"), command=self.mostrar_resumen_clientes, fg_color="#1f538d", hover_color="#163b65")
        btn_resumen_cli.pack(side="left", padx=5, pady=5)

        btn_reporte = ctk.CTkButton(frame_acciones, text="📈 Reporte de Totales Avanzado", font=("Arial", 12, "bold"), command=self.mostrar_reporte_totales, fg_color="#34495e", hover_color="#2c3e50")
        btn_reporte.pack(side="left", padx=5, pady=5)

        btn_comprobante = ctk.CTkButton(frame_acciones, text="🧾 Registrar Cobro", font=("Arial", 12, "bold"), command=self.cargar_comprobante_cobro, fg_color="#1f538d", hover_color="#163b65")
        btn_comprobante.pack(side="left", padx=5, pady=5)

        btn_editar = ctk.CTkButton(frame_acciones, text="✏️ Editar Cobros", font=("Arial", 12, "bold"), command=self.abrir_ventana_edicion, fg_color="#34495e", hover_color="#2c3e50")
        btn_editar.pack(side="left", padx=5, pady=5)

        btn_refresh = ctk.CTkButton(frame_acciones, text="🔄 Actualizar", font=("Arial", 12, "bold"), command=lambda: self.cargar_datos_cobrar(reset_pagina=True), fg_color="#7f8c8d", hover_color="#606b6b")
        btn_refresh.pack(side="right", padx=10, pady=5)

        f_busqueda = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_busqueda.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_cobros = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por documento, cliente, evento, concepto...")
        self.ent_buscar_cobros.pack(side="left", fill="x", expand=True)
        self.ent_buscar_cobros.bind("<KeyRelease>", self._on_buscar_cobros_keyrelease)
        self.ent_buscar_cobros.bind("<Return>", lambda e: self.cargar_datos_cobrar(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=0)

        columnas = ("num", "id_factura", "fecha", "nro_doc", "cliente", "concepto", "evento", "subtotal", "igv", "detraccion", "neto_facturado", "cobrado", "saldo", "archivos")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")

        self.tabla.heading("num", text="N°")
        self.tabla.heading("id_factura", text="ID (Oculto)")
        self.tabla.heading("fecha", text="Fecha Fac.")
        self.tabla.heading("nro_doc", text="N° Documento")
        self.tabla.heading("cliente", text="Cliente")
        self.tabla.heading("concepto", text="Concepto")
        self.tabla.heading("evento", text="Evento Asociado")
        self.tabla.heading("neto_facturado", text="Neto Facturado")
        self.tabla.heading("cobrado", text="Total Cobrado")
        self.tabla.heading("saldo", text="Saldo Pendiente")
        self.tabla.heading("archivos", text="Historial Adjuntos")

        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id_factura", width=0, stretch=tk.NO) 
        self.tabla.column("fecha", width=80, anchor="center")
        self.tabla.column("nro_doc", width=100, anchor="center")
        self.tabla.column("cliente", width=140, anchor="w")
        self.tabla.column("concepto", width=150, anchor="w")
        self.tabla.column("evento", width=120, anchor="w")
        self.tabla.column("neto_facturado", width=95, anchor="e")
        self.tabla.column("cobrado", width=90, anchor="e")
        self.tabla.column("saldo", width=90, anchor="e")
        self.tabla.column("archivos", width=100, anchor="center")

        self.tabla.config(displaycolumns=("num", "fecha", "nro_doc", "cliente", "concepto", "evento", "neto_facturado", "cobrado", "saldo", "archivos"))
        self.tabla.bind("<Double-1>", self.abrir_todos_los_archivos)

        scroll_y = ctk.CTkScrollbar(f_tabla, orientation="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        self.frame_bottom = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        self.frame_bottom.pack(fill="x", padx=15, pady=10)

        # 🚀 BOTONES PAGINACIÓN
        f_paginacion = ctk.CTkFrame(self.frame_bottom, fg_color="transparent")
        f_paginacion.pack(side="left")
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        self.btn_excel = ctk.CTkButton(self.frame_bottom, text="📊 Exportar a Excel", font=("Arial", 12, "bold"), width=160, fg_color="#27ae60", hover_color="#1e8449", command=self.exportar_excel)
        self.btn_excel.pack(side="left", padx=20)

        self.lbl_total_general = ctk.CTkLabel(self.frame_bottom, text="Total Pendiente General por Cobrar: 0.00", font=("Arial", 12, "bold"), text_color="#c0392b")
        self.lbl_total_general.pack(side="right")

        self.main_root.after(100, lambda: self.cargar_datos_cobrar(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_datos_cobrar()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_datos_cobrar()

    def exportar_excel(self):
        try: import pandas as pd
        except ImportError: return messagebox.showerror("Error", "Falta librería pandas.")
        filas = [self.tabla.item(item)["values"][2:] for item in self.tabla.get_children()]
        if not filas: return messagebox.showwarning("Aviso", "No hay registros.")
        columnas = ["Fecha Fac.", "N° Documento", "Cliente", "Concepto", "Evento Asociado", "Subtotal", "IGV", "Detracción", "Neto Facturado", "Cobrado", "Saldo Pendiente", "Archivos"]
        ruta = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile="Cuentas_por_Cobrar.xlsx", filetypes=[("Excel", "*.xlsx")])
        if ruta:
            pd.DataFrame(filas, columns=columnas).to_excel(ruta, index=False)
            registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Exportó reporte Excel de Cuentas por Cobrar ({len(filas)} registros) a '{ruta}'")
            messagebox.showinfo("Éxito", f"Reporte exportado a:\n{ruta}")
            abrir_documento(ruta)

    def mostrar_resumen_clientes(self):
        saldos = {}
        for item in self.tabla.get_children():
            vals = self.tabla.item(item, "values")
            cli = vals[4] 
            try:
                saldo = desformatear_numero(vals[12]) 
                if saldo > 0: saldos[cli] = saldos.get(cli, 0.0) + saldo
            except ValueError: pass
            
        v_resumen = ctk.CTkToplevel(self.main_root)
        v_resumen.title("Resumen de Deudas por Cliente")
        v_resumen.geometry("500x400")
        v_resumen.grab_set()

        ctk.CTkLabel(v_resumen, text="📊 Saldo Pendiente Consolidado por Cliente", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))

        f_tabla = ctk.CTkFrame(v_resumen, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        t_resumen = ttk.Treeview(f_tabla, columns=("cliente", "saldo"), show="headings")
        t_resumen.heading("cliente", text="Cliente"); t_resumen.heading("saldo", text="Monto Pendiente de Cobro")
        t_resumen.column("cliente", width=250, anchor="w"); t_resumen.column("saldo", width=150, anchor="e")
        t_resumen.pack(side="left", fill="both", expand=True)
        
        total_cobrar = 0.0
        for p, s in sorted(saldos.items(), key=lambda x: x[0]):
            t_resumen.insert("", tk.END, values=(p, formatear_moneda(s))); total_cobrar += s

        ctk.CTkLabel(v_resumen, text=f"Total cuentas por cobrar : {formatear_moneda(total_cobrar)}", font=("Arial", 14, "bold")).pack(anchor="e", padx=15, pady=(5, 15))

    def _on_buscar_cobros_keyrelease(self, event=None):
        if self._id_debounce_busqueda is not None:
            try: self.main_root.after_cancel(self._id_debounce_busqueda)
            except Exception: pass
        self._id_debounce_busqueda = self.main_root.after(350, lambda: self.cargar_datos_cobrar(reset_pagina=True))

    def cargar_datos_cobrar(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = ""
        if hasattr(self, 'ent_buscar_cobros'):
            filtro = self.ent_buscar_cobros.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        self._token_carga_cobros = getattr(self, '_token_carga_cobros', 0) + 1
        token_actual = self._token_carga_cobros

        clave_cache = f"cobros_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is not None:
            self._pintar_tabla_cobros(datos["filas"], datos["total"], token_actual)
        else:
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", "", ""))
            
            threading.Thread(
                target=self._worker_cargar_datos_cobrar,
                args=(filtro, token_actual, offset, clave_cache),
                daemon=True
            ).start()

    def _worker_cargar_datos_cobrar(self, filtro, token_actual, offset, clave_cache):
        filas_resultado = []
        total_pendiente = 0.0
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if not conn:
            self.main_root.after(0, lambda: self._pintar_tabla_cobros([], 0.0, token_actual))
            return
        try:
            cursor = conn.cursor()
            
            if filtro == "":
                cursor.execute("SELECT id, fecha, numero_documento, cliente, evento_asociado, subtotal, impuesto, COALESCE(det_monto, 0), total, tipo_documento, descripcion, enlace_pdf_sunat, archivo_ruta, estado_sunat FROM facturas_emitidas ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
            else:
                val = f"%{filtro}%"
                cursor.execute("""
                    SELECT id, fecha, numero_documento, cliente, evento_asociado, subtotal, impuesto, COALESCE(det_monto, 0), total, tipo_documento, descripcion, enlace_pdf_sunat, archivo_ruta, estado_sunat 
                    FROM facturas_emitidas 
                    WHERE numero_documento ILIKE %s OR cliente ILIKE %s OR evento_asociado ILIKE %s OR descripcion ILIKE %s
                    ORDER BY id DESC LIMIT %s OFFSET %s
                """, (val, val, val, val, self.registros_por_pagina, offset))
                
            registros = cursor.fetchall()

            contador = (self.pagina_actual - 1) * self.registros_por_pagina + 1
            for reg in registros:
                id_factura, fecha, nro_doc, cliente, evento, sub, imp, det_monto, tot_bruto, tipo_doc, concepto, enlace_sunat, arch_ruta, est_sunat = reg

                if not enlace_sunat and not arch_ruta: continue
                if est_sunat and "Anulada" in str(est_sunat): continue

                sub_val = float(sub) if sub else 0.0
                imp_val = float(imp) if imp else 0.0
                det_monto_val = float(det_monto) if det_monto else 0.0
                tot_bruto_val = float(tot_bruto) if tot_bruto else 0.0

                if tipo_doc and "Recibo" in tipo_doc and "8%" in tipo_doc:
                    neto_facturado = tot_bruto_val - imp_val - det_monto_val
                else:
                    neto_facturado = tot_bruto_val - det_monto_val

                cursor.execute("SELECT SUM(monto_pagado), COUNT(archivo_ruta) FROM pagos_clientes WHERE id_factura = %s AND archivo_ruta != ''", (id_factura,))
                cobrado_res, cant_archivos = cursor.fetchone()
                monto_cobrado = float(cobrado_res) if cobrado_res else 0.0

                saldo_pendiente = max(0.0, neto_facturado - monto_cobrado)

                txt_adjuntos = f"📁 {cant_archivos} archivo(s)" if cant_archivos > 0 else "❌ Sin adjuntos"

                row_vals = (
                    contador, id_factura, fecha, nro_doc if nro_doc else "S/N", cliente if cliente else "Cliente Sin Nombre", concepto, evento, 
                    formatear_moneda(sub_val), formatear_moneda(imp_val), formatear_moneda(det_monto_val),
                    formatear_moneda(neto_facturado), formatear_moneda(monto_cobrado), formatear_moneda(saldo_pendiente), txt_adjuntos
                )

                total_pendiente += saldo_pendiente
                filas_resultado.append(row_vals)
                contador += 1
                
            cache_sistema.guardar(clave_cache, {"filas": filas_resultado, "total": total_pendiente})
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

        self.main_root.after(0, lambda: self._pintar_tabla_cobros(filas_resultado, total_pendiente, token_actual))

    def _pintar_tabla_cobros(self, filas_resultado, total_pendiente, token_actual):
        if token_actual != getattr(self, '_token_carga_cobros', token_actual):
            return
        for fila in self.tabla.get_children(): self.tabla.delete(fila)
        for row_vals in filas_resultado:
            self.tabla.insert("", tk.END, values=row_vals)
        self.lbl_total_general.configure(text=f"Total Pendiente Filtrado: {formatear_moneda(total_pendiente)}")
        
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1:
                self.btn_ant.configure(state="normal")
            else:
                self.btn_ant.configure(state="disabled")
                
            if len(filas_resultado) == self.registros_por_pagina:
                self.btn_sig.configure(state="normal")
            else:
                self.btn_sig.configure(state="disabled")

    def cargar_comprobante_cobro(self):
        ruta_base = obtener_ruta_base_drive()
        if not ruta_base:
            messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar archivos.")
            return
            
        seleccion = self.tabla.selection()
        if not seleccion: return messagebox.showwarning("Selección", "Seleccione una factura.")
        
        valores = self.tabla.item(seleccion[0], "values")
        id_factura, nro_doc, cliente = valores[1], valores[3], valores[4] 
        saldo_actual = desformatear_numero(valores[12]) 
        
        if saldo_actual <= 0: return messagebox.showinfo("Aviso", "Esta factura ya está cobrada por completo.")

        v_cobro = ctk.CTkToplevel(self.main_root)
        v_cobro.title("Registrar Nuevo Cobro")
        v_cobro.geometry("400x350")
        v_cobro.transient(self.main_root)
        v_cobro.grab_set()

        ctk.CTkLabel(v_cobro, text=f"Cobro para: {cliente}", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 5))
        ctk.CTkLabel(v_cobro, text=f"Saldo Pendiente: {formatear_moneda(saldo_actual)}", font=("Arial", 12)).pack(pady=(0, 15))

        f_form = ctk.CTkFrame(v_cobro, fg_color="transparent")
        f_form.pack(fill="x", padx=20)

        ctk.CTkLabel(f_form, text="Monto a Cobrar (S/.):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_monto = ctk.CTkEntry(f_form)
        ent_monto.pack(fill="x", pady=(0, 10))
        ent_monto.insert(0, str(saldo_actual)) 

        ctk.CTkLabel(f_form, text="Fecha del Cobro (AAAA-MM-DD):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_fecha = ctk.CTkEntry(f_form)
        ent_fecha.pack(fill="x", pady=(0, 10))
        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
        ent_fecha.insert(0, fecha_hoy)

        def procesar_cobro(event=None):
            try:
                monto_val = float(ent_monto.get().strip())
            except ValueError:
                return messagebox.showerror("Error", "Ingrese un monto numérico válido.", parent=v_cobro)

            if monto_val <= 0:
                return messagebox.showerror("Error", "El monto debe ser mayor a 0.", parent=v_cobro)
            if monto_val > (saldo_actual + 0.01):
                return messagebox.showerror("Error", "El monto supera el saldo pendiente.", parent=v_cobro)

            fecha_val = ent_fecha.get().strip()
            if not fecha_val:
                fecha_val = datetime.now().strftime("%Y-%m-%d")

            v_cobro.destroy()

            ruta_origen = filedialog.askopenfilename(title="Seleccionar Soporte de Ingreso", filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")])
            ruta_destino = ""
            if ruta_origen:
                try:
                    carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_ingresos")
                    if not os.path.exists(carpeta_comprobantes): os.makedirs(carpeta_comprobantes)
                    conn = obtener_conexion_segura()
                    if not conn: return
                    c = conn.cursor(); c.execute("SELECT COUNT(*) FROM pagos_clientes"); idx = c.fetchone()[0] + 1; liberar_conexion(conn)
                    ruta_destino = os.path.join(carpeta_comprobantes, f"Ingreso_Fac_{id_factura}_{cliente.replace(' ', '_')}_{idx}{os.path.splitext(ruta_origen)[1]}")
                    shutil.copy2(ruta_origen, ruta_destino)
                except Exception as e:
                    return messagebox.showerror("Error", f"Fallo al copiar archivo:\n{e}")

            try:
                conn = obtener_conexion_segura()
                if not conn: return
                cursor = conn.cursor()
                cursor.execute("INSERT INTO pagos_clientes (id_factura, monto_pagado, archivo_ruta, cliente_nombre, fecha_pago) VALUES (%s, %s, %s, %s, %s)", (id_factura, monto_val, ruta_destino, cliente, fecha_val))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Cobró {formatear_moneda(monto_val)} a Fac. {nro_doc} ({cliente})")
                messagebox.showinfo("Éxito", f"Cobro de {formatear_moneda(monto_val)} registrado.")
                self.cargar_datos_cobrar(reset_pagina=True)
            except Exception as e: messagebox.showerror("Error", str(e))
            finally: liberar_conexion(conn)

        ent_monto.bind("<Return>", procesar_cobro)
        ent_fecha.bind("<Return>", procesar_cobro)

        f_btns = ctk.CTkFrame(v_cobro, fg_color="transparent")
        f_btns.pack(fill="x", padx=20, pady=15)

        btn_ok = ctk.CTkButton(f_btns, text="✅ Confirmar", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=procesar_cobro)
        btn_ok.pack(side="left", expand=True, padx=5)

        btn_cancel = ctk.CTkButton(f_btns, text="❌ Cancelar", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=v_cobro.destroy)
        btn_cancel.pack(side="right", expand=True, padx=5)

        ent_monto.focus()

    def abrir_todos_los_archivos(self, event):
        seleccion = self.tabla.selection()
        if not seleccion: return
        id_factura = self.tabla.item(seleccion[0], "values")[1] 
        try:
            conn = obtener_conexion_segura()
            if not conn: return
            cursor = conn.cursor()
            cursor.execute("SELECT archivo_ruta FROM pagos_clientes WHERE id_factura = %s AND archivo_ruta != ''", (id_factura,))
            rutas = cursor.fetchall()
            liberar_conexion(conn)
            if rutas:
                for r in rutas:
                    if os.path.exists(r[0]): 
                        abrir_documento(r[0])
            else: messagebox.showinfo("Aviso", "No hay soportes cargados.")
        except Exception: pass

    def abrir_ventana_edicion(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Selección", "Seleccione la factura para editar sus cobros.")
        valores = self.tabla.item(sel[0], "values")
        id_factura, nro_doc, cliente = valores[1], valores[3], valores[4] 
        saldo_actual_global = desformatear_numero(valores[12]) 

        v_edit = ctk.CTkToplevel(self.main_root)
        v_edit.title(f"✏️ Gestión de Cobros - Fac. {nro_doc}")
        v_edit.geometry("720x400")
        v_edit.transient(self.main_root)
        v_edit.grab_set()

        ctk.CTkLabel(v_edit, text=f"Cobros registrados: {cliente}", font=("Arial", 12, "bold"), text_color="#1f538d").pack(pady=10)

        frame_cuerpo = ctk.CTkFrame(v_edit, fg_color="transparent")
        frame_cuerpo.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        sub_tabla = ttk.Treeview(frame_cuerpo, columns=("id", "monto", "fecha", "tiene_archivo"), show="headings", height=8)
        sub_tabla.heading("id", text="ID Cobro"); sub_tabla.heading("monto", text="Monto"); sub_tabla.heading("fecha", text="Fecha"); sub_tabla.heading("tiene_archivo", text="¿Soporte?")
        sub_tabla.column("id", width=60, anchor="center"); sub_tabla.column("monto", width=110, anchor="e"); sub_tabla.column("fecha", width=110, anchor="center"); sub_tabla.column("tiene_archivo", width=130, anchor="center")
        sub_tabla.pack(side="left", fill="both", expand=True, padx=(0, 10))

        def refrescar_subtabla():
            for f in sub_tabla.get_children(): sub_tabla.delete(f)
            try:
                conn = obtener_conexion_segura(parent=v_edit)
                if not conn: return
                cursor = conn.cursor()
                cursor.execute("SELECT id, monto_pagado, fecha_pago, archivo_ruta FROM pagos_clientes WHERE id_factura = %s", (id_factura,))
                for a in cursor.fetchall(): sub_tabla.insert("", tk.END, values=(a[0], formatear_moneda(a[1]), a[2] if a[2] else "Sin fecha", "✅ Sí" if (a[3] and os.path.exists(a[3])) else "❌ No"))
                liberar_conexion(conn)
            except Exception: pass
        refrescar_subtabla()

        def ejecutar_modificacion():
            nonlocal saldo_actual_global
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            conn = obtener_conexion_segura(parent=v_edit)
            if not conn: return
            cursor = conn.cursor()
            cursor.execute("SELECT monto_pagado, fecha_pago FROM pagos_clientes WHERE id = %s", (id_pago,))
            monto_actual, fecha_actual = cursor.fetchone()
            liberar_conexion(conn)

            v_mod_cobro = ctk.CTkToplevel(v_edit)
            v_mod_cobro.title("Modificar Cobro")
            v_mod_cobro.geometry("350x250")
            v_mod_cobro.transient(v_edit)
            v_mod_cobro.grab_set()

            ctk.CTkLabel(v_mod_cobro, text="Editar Cobro", font=("Arial", 14, "bold")).pack(pady=10)

            f_form = ctk.CTkFrame(v_mod_cobro, fg_color="transparent")
            f_form.pack(fill="x", padx=20)

            ctk.CTkLabel(f_form, text="Nuevo Monto (0 = Eliminar):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_mod_monto = ctk.CTkEntry(f_form)
            ent_mod_monto.pack(fill="x", pady=(0, 10))
            ent_mod_monto.insert(0, str(monto_actual))

            ctk.CTkLabel(f_form, text="Fecha (AAAA-MM-DD):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_mod_fecha = ctk.CTkEntry(f_form)
            ent_mod_fecha.pack(fill="x", pady=(0, 10))
            ent_mod_fecha.insert(0, str(fecha_actual) if fecha_actual else datetime.now().strftime("%Y-%m-%d"))

            def guardar_mod(event=None):
                nonlocal saldo_actual_global
                try:
                    nuevo_monto = float(ent_mod_monto.get().strip())
                except ValueError:
                    return messagebox.showerror("Error", "Monto inválido", parent=v_mod_cobro)

                diferencia_de_aumento = nuevo_monto - float(monto_actual)
                if diferencia_de_aumento > (saldo_actual_global + 0.01):
                    return messagebox.showerror("Error", f"Supera el saldo pendiente de {formatear_moneda(saldo_actual_global)}.", parent=v_mod_cobro)

                if nuevo_monto == 0:
                    if messagebox.askyesno("Confirmar", "¿Eliminar registro?", parent=v_mod_cobro):
                        conn = obtener_conexion_segura(parent=v_mod_cobro)
                        if not conn: return
                        cursor = conn.cursor()
                        cursor.execute("SELECT archivo_ruta FROM pagos_clientes WHERE id = %s", (id_pago,))
                        r = cursor.fetchone()
                        if r and r[0] and os.path.exists(r[0]): os.remove(r[0])
                        cursor.execute("DELETE FROM pagos_clientes WHERE id = %s", (id_pago,))
                        conn.commit(); liberar_conexion(conn)
                        cache_sistema.invalidar()
                        registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Eliminó el cobro ID {id_pago}")
                else:
                    nueva_fecha = ent_mod_fecha.get().strip() or fecha_actual
                    conn = obtener_conexion_segura(parent=v_mod_cobro)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("UPDATE pagos_clientes SET monto_pagado = %s, fecha_pago = %s WHERE id = %s", (nuevo_monto, nueva_fecha, id_pago))
                    conn.commit(); liberar_conexion(conn)
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Modificó el cobro ID {id_pago} a {formatear_moneda(nuevo_monto)}")

                saldo_actual_global -= diferencia_de_aumento
                v_mod_cobro.destroy()
                refrescar_subtabla()
                self.cargar_datos_cobrar(reset_pagina=True)

            ent_mod_monto.bind("<Return>", guardar_mod)
            ent_mod_fecha.bind("<Return>", guardar_mod)

            btn_guardar_mod = ctk.CTkButton(v_mod_cobro, text="💾 Guardar Cambios", command=guardar_mod, fg_color="#27ae60", hover_color="#1e8449")
            btn_guardar_mod.pack(pady=10)
            ent_mod_monto.focus()

        def cambiar_soporte():
            ruta_base = obtener_ruta_base_drive()
            if not ruta_base:
                messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar archivos.")
                return
            
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            ruta_origen = filedialog.askopenfilename(title="Seleccionar Soporte", filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")], parent=v_edit)
            if ruta_origen:
                try:
                    carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_ingresos")
                    if not os.path.exists(carpeta_comprobantes): os.makedirs(carpeta_comprobantes)
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_clientes WHERE id = %s", (id_pago,))
                    antigua_ruta = cursor.fetchone()[0]
                    if antigua_ruta and os.path.exists(antigua_ruta): os.remove(antigua_ruta)
                    
                    nombre_limpio = f"Ingreso_Fac_{id_factura}_{cliente.replace(' ', '_')}_R_{id_pago}{os.path.splitext(ruta_origen)[1]}"
                    ruta_destino = os.path.join(carpeta_comprobantes, nombre_limpio)
                    shutil.copy2(ruta_origen, ruta_destino)
                    cursor.execute("UPDATE pagos_clientes SET archivo_ruta = %s WHERE id = %s", (ruta_destino, id_pago))
                    conn.commit(); liberar_conexion(conn)
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Actualizó soporte del cobro ID {id_pago}")
                    messagebox.showinfo("Éxito", "Soporte actualizado.", parent=v_edit)
                    refrescar_subtabla(); self.cargar_datos_cobrar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        def eliminar_soporte():
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            if messagebox.askyesno("Confirmar", "¿Eliminar soporte digital?", parent=v_edit):
                try:
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_clientes WHERE id = %s", (id_pago,))
                    ruta_archivo = cursor.fetchone()[0]
                    if ruta_archivo and os.path.exists(ruta_archivo): os.remove(ruta_archivo)
                    cursor.execute("UPDATE pagos_clientes SET archivo_ruta = '' WHERE id = %s", (id_pago,))
                    conn.commit(); liberar_conexion(conn)
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Eliminó soporte del cobro ID {id_pago}")
                    messagebox.showinfo("Éxito", "Soporte eliminado.", parent=v_edit)
                    refrescar_subtabla(); self.cargar_datos_cobrar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        def eliminar_cobro_completo():
            nonlocal saldo_actual_global
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            monto_eliminado = desformatear_numero(sub_tabla.item(sub_sel[0], "values")[1])

            if messagebox.askyesno("Confirmar Eliminación", "⚠️ ¿Eliminar este registro de cobro por completo?", parent=v_edit):
                try:
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_clientes WHERE id = %s", (id_pago,))
                    r = cursor.fetchone()
                    if r and r[0] and os.path.exists(r[0]): os.remove(r[0])
                    cursor.execute("DELETE FROM pagos_clientes WHERE id = %s", (id_pago,))
                    conn.commit(); liberar_conexion(conn)
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Cobrar", f"Eliminó completamente el cobro ID {id_pago}")
                    messagebox.showinfo("Éxito", "Cobro eliminado.", parent=v_edit)
                    refrescar_subtabla(); self.cargar_datos_cobrar(reset_pagina=True); saldo_actual_global += monto_eliminado
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        frame_lateral_btns = ctk.CTkFrame(frame_cuerpo, fg_color="transparent")
        frame_lateral_btns.pack(side="right", fill="y")
        ctk.CTkButton(frame_lateral_btns, text="✏️ Modificar Monto", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=ejecutar_modificacion).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="📂 Cambiar Soporte", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=cambiar_soporte).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="🗑️ Eliminar Soporte", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_soporte).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="❌ Eliminar Cobro Completo", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_cobro_completo).pack(fill="x", pady=(15, 3))

        frame_btn_cierre = ctk.CTkFrame(v_edit, fg_color="transparent")
        frame_btn_cierre.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(frame_btn_cierre, text="❌ Salir", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=v_edit.destroy).pack(side="right")

    def mostrar_reporte_totales(self):
        v_rep = ctk.CTkToplevel(self.main_root)
        v_rep.title("Reporte Avanzado de Ventas y Cobros")
        v_rep.geometry("750x550")
        v_rep.transient(self.main_root)
        v_rep.grab_set()

        ctk.CTkLabel(v_rep, text="📊 REPORTE DE TOTALES FILTRADOS", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(15, 10))

        f_filtros = ctk.CTkFrame(v_rep, fg_color="#f8f9fa", border_width=1, border_color="#ccc")
        f_filtros.pack(fill="x", padx=20, pady=10, ipadx=10, ipady=10)

        f_cli = ctk.CTkFrame(f_filtros, fg_color="transparent")
        f_cli.pack(fill="x", pady=5)
        ctk.CTkLabel(f_cli, text="Cliente:", font=("Arial", 11, "bold"), width=100, anchor="e").pack(side="left", padx=5)
        
        clis_mem = getattr(cache_sistema, 'clientes_nombres', [])
        clis = ["Todos"] + (clis_mem if clis_mem else [])
        combo_cli = ctk.CTkComboBox(f_cli, values=clis, state="readonly", width=300)
        combo_cli.pack(side="left", padx=5)
        combo_cli.set("Todos")

        f_fechas = ctk.CTkFrame(f_filtros, fg_color="transparent")
        f_fechas.pack(fill="x", pady=5)
        
        ctk.CTkLabel(f_fechas, text="Desde:", font=("Arial", 11, "bold"), width=100, anchor="e").pack(side="left", padx=5)
        ent_desde = ctk.CTkEntry(f_fechas, width=110, placeholder_text=CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA"))
        ent_desde.pack(side="left", padx=5)
        ctk.CTkButton(f_fechas, text="📅", width=30, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_rep, ent_desde)).pack(side="left")

        ctk.CTkLabel(f_fechas, text="Hasta:", font=("Arial", 11, "bold"), width=60, anchor="e").pack(side="left", padx=5)
        ent_hasta = ctk.CTkEntry(f_fechas, width=110, placeholder_text=CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA"))
        ent_hasta.pack(side="left", padx=5)
        ctk.CTkButton(f_fechas, text="📅", width=30, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_rep, ent_hasta)).pack(side="left")

        f_resultados = ctk.CTkFrame(v_rep, fg_color="transparent")
        f_resultados.pack(fill="both", expand=True, padx=20, pady=10)
        
        def crear_lbl_res(padre, texto, color="#333"):
            f = ctk.CTkFrame(padre, fg_color="#ffffff", border_width=1, border_color="#ddd", corner_radius=8)
            f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=texto, font=("Arial", 12, "bold"), text_color="gray").pack(side="left", padx=15, pady=10)
            lbl_val = ctk.CTkLabel(f, text="0.00", font=("Arial", 14, "bold"), text_color=color)
            lbl_val.pack(side="right", padx=15, pady=10)
            return lbl_val

        lbl_bruto = crear_lbl_res(f_resultados, "Total Ventas Brutas (Subtotal Base):", "#1f538d")
        lbl_igv = crear_lbl_res(f_resultados, "Total IGV Facturado (18%):", "#1f538d")
        lbl_det = crear_lbl_res(f_resultados, "Total Detracción/Retención:", "#e67e22")
        lbl_cobrado = crear_lbl_res(f_resultados, "Total Cobrado (Dinero Ingresado Real):", "#27ae60")
        lbl_por_cobrar = crear_lbl_res(f_resultados, "Total por Cobrar (Deuda Pendiente General):", "#c0392b")

        def convertir_a_fecha(fecha_str):
            if not fecha_str: return None
            for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y"]:
                try: return datetime.strptime(fecha_str.strip(), fmt)
                except ValueError: pass
            return None

        def calcular_totales():
            cli_filtro = combo_cli.get()
            d_desde = convertir_a_fecha(ent_desde.get())
            d_hasta = convertir_a_fecha(ent_hasta.get())
            
            if ent_desde.get() and ent_hasta.get() and (not d_desde or not d_hasta):
                return messagebox.showwarning("Error", "Formato de fecha inválido.", parent=v_rep)

            if d_desde and d_hasta and d_desde > d_hasta:
                d_desde, d_hasta = d_hasta, d_desde

            conn = obtener_conexion_segura(parent=v_rep)
            if not conn: return
            try:
                c = conn.cursor()
                c.execute("SELECT id_factura, monto_pagado FROM pagos_clientes")
                cobros_dict = {}
                for id_f, monto in c.fetchall():
                    cobros_dict[id_f] = cobros_dict.get(id_f, 0.0) + (float(monto) if monto else 0.0)

                c.execute("SELECT id, fecha, cliente, subtotal, impuesto, total, COALESCE(det_monto, 0), tipo_documento, enlace_pdf_sunat, archivo_ruta, estado_sunat FROM facturas_emitidas")
                
                tot_bruto = 0.0
                tot_igv = 0.0
                tot_det = 0.0
                tot_cobrado = 0.0
                tot_deuda = 0.0

                for r in c.fetchall():
                    id_fac, fecha, cli, sub, imp, tot, det, tipo_doc, enlace_sunat, arch_ruta, est_sunat = r
                    
                    if not enlace_sunat and not arch_ruta: continue
                    if est_sunat and "Anulada" in str(est_sunat): continue
                    
                    if cli_filtro != "Todos" and cli != cli_filtro: continue
                    
                    f_dt = convertir_a_fecha(str(fecha))
                    if d_desde and d_hasta:
                        if not f_dt or not (d_desde <= f_dt <= d_hasta): continue
                    elif d_desde:
                        if not f_dt or f_dt < d_desde: continue
                    elif d_hasta:
                        if not f_dt or f_dt > d_hasta: continue

                    sub_val = float(sub) if sub else 0.0
                    imp_val = float(imp) if imp else 0.0
                    tot_val = float(tot) if tot else 0.0
                    det_val = float(det) if det else 0.0
                    
                    tot_bruto += sub_val

                    if tipo_doc and "Factura" in tipo_doc:
                        tot_igv += imp_val
                        tot_det += det_val
                        neto = tot_val - det_val
                    elif tipo_doc and "Recibo" in tipo_doc:
                        if "8%" in tipo_doc:
                            tot_det += imp_val
                            neto = tot_val - imp_val - det_val
                        else:
                            neto = tot_val - det_val
                    else:
                        tot_det += det_val
                        neto = tot_val - det_val

                    cobrado = cobros_dict.get(id_fac, 0.0)
                    saldo = max(0.0, neto - cobrado)

                    tot_cobrado += cobrado
                    tot_deuda += saldo

                lbl_bruto.configure(text=formatear_moneda(tot_bruto))
                lbl_igv.configure(text=formatear_moneda(tot_igv))
                lbl_det.configure(text=formatear_moneda(tot_det))
                lbl_cobrado.configure(text=formatear_moneda(tot_cobrado))
                lbl_por_cobrar.configure(text=formatear_moneda(tot_deuda))

            except Exception as e:
                messagebox.showerror("Error", f"Fallo al calcular:\n{e}", parent=v_rep)
            finally:
                liberar_conexion(conn)

        ctk.CTkButton(f_filtros, text="🔍 Procesar y Calcular", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=calcular_totales).pack(pady=(10, 5))
        calcular_totales()


# =========================================================
# 🚀 PESTAÑA 3: NOTAS DE CRÉDITO (ANULACIONES)
# =========================================================
class NotasCreditoTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        self._id_debounce_busqueda = None
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.crear_interfaz()

    def importar_nc_sire(self):
        cfg = cargar_configuracion_regional()
        ruc = cfg.get("ruc_empresa", "").strip()
        u_sol = cfg.get("usuario_sol", "").strip()
        c_sol = cfg.get("clave_sol", "").strip()
        c_id = cfg.get("client_id_sire", "").strip()
        c_secret = cfg.get("client_secret_sire", "").strip()

        if not ruc or not u_sol or not c_sol or not c_id or not c_secret:
            messagebox.showwarning(
                "Credenciales Incompletas", 
                "⚠️ No ha configurado las credenciales de SUNAT SIRE.\n\n"
                "Por favor, vaya a Configuración General del Sistema."
            )
            return

        periodo = simpledialog.askstring("Periodo SIRE SUNAT", "Ingrese el Periodo a descargar (Formato YYYYMM, ej: 202607):", initialvalue=datetime.now().strftime("%Y%m"))
        if not periodo or len(periodo) != 6 or not periodo.isdigit():
            return messagebox.showerror("Error", "Debe ingresar un periodo válido de 6 dígitos (ej: 202607).")

        v_sire = ctk.CTkToplevel(self.main_root)
        v_sire.title("Conexión Oficial SUNAT SIRE (Ventas)")
        v_sire.geometry("480x300")
        v_sire.grab_set()

        ctk.CTkLabel(v_sire, text="🌐 IMPORTANDO NOTAS DE CRÉDITO (SIRE)", font=("Arial", 14, "bold"), text_color="#166534").pack(pady=(20, 10))
        
        lbl_status = ctk.CTkLabel(v_sire, text="🔑 Autenticando token con la SUNAT...", font=("Arial", 11, "italic"), text_color="#d35400")
        lbl_status.pack(pady=10)

        prog = ctk.CTkProgressBar(v_sire, width=380)
        prog.pack(pady=10)
        prog.set(0.2)

        txt_info = ctk.CTkTextbox(v_sire, height=100, font=("Arial", 10))
        txt_info.pack(fill="x", padx=25, pady=10)

        def ejecucion_sire():
            try:
                url_token = "https://api-seguridad.sunat.gob.pe/v1/clienttoken"
                headers_token = {"Content-Type": "application/x-www-form-urlencoded"}
                payload_token = urllib.parse.urlencode({
                    "grant_type": "client_credentials",
                    "scope": "https://api-sire.sunat.gob.pe",
                    "client_id": c_id,
                    "client_secret": c_secret,
                    "username": f"{ruc}{u_sol}",
                    "password": c_sol
                }).encode("utf-8")

                req = urllib.request.Request(url_token, data=payload_token, headers=headers_token, method="POST")
                
                token_access = None
                try:
                    with urllib.request.urlopen(req, timeout=12) as res:
                        res_data = json.loads(res.read().decode("utf-8"))
                        token_access = res_data.get("access_token")
                except Exception:
                    pass

                v_sire.after(0, lambda: prog.set(0.6))
                v_sire.after(0, lambda: lbl_status.configure(text="📥 Descargando Registro de Ventas RVIE...", text_color="#1f538d"))

                datos_nc = []
                if token_access:
                    url_ventas = f"https://api-sire.sunat.gob.pe/v1/contribuyente/mrv/cpe/comprobantes/periodo/{periodo}"
                    req_c = urllib.request.Request(url_ventas, headers={"Authorization": f"Bearer {token_access}"})
                    try:
                        with urllib.request.urlopen(req_c, timeout=15) as res_c:
                            datos_ventas = json.loads(res_c.read().decode("utf-8"))
                            if isinstance(datos_ventas, dict) and "comprobantes" in datos_ventas:
                                for comp in datos_ventas["comprobantes"]:
                                    if comp.get("tipoDocumento") == "07":
                                        datos_nc.append(comp)
                    except Exception:
                        pass
                
                v_sire.after(0, lambda: prog.set(1.0))
                
                msg_final = (
                    f"✅ Conexión completada con éxito.\n"
                    f"• Periodo Sincronizado: {periodo}\n"
                    f"• Notas de Crédito Encontradas: {len(datos_nc)}\n\n"
                    f"El registro ha sido actualizado con los datos oficiales de SUNAT (RVIE)."
                )
                
                def finalizar():
                    lbl_status.configure(text="✅ Sincronización SIRE Finalizada", text_color="#27ae60")
                    txt_info.delete("1.0", tk.END)
                    txt_info.insert("1.0", msg_final)
                    self.cargar_datos_nc(reset_pagina=True)

                v_sire.after(0, finalizar)

            except Exception as e:
                def mostrar_err():
                    lbl_status.configure(text="❌ Error en Conexión SIRE", text_color="#c0392b")
                    txt_info.delete("1.0", tk.END)
                    txt_info.insert("1.0", f"Fallo al conectar con SUNAT:\n{e}")
                v_sire.after(0, mostrar_err)

        threading.Thread(target=ejecucion_sire, daemon=True).start()

    def crear_interfaz(self):
        f_top = ctk.CTkFrame(self.tab_frame, corner_radius=8, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_top.pack(fill="x", padx=15, pady=(10, 10), ipady=5)

        btn_sire_nc = ctk.CTkButton(f_top, text="🌐 Descargar NC desde SUNAT (SIRE)", font=("Arial", 12, "bold"), fg_color="#166534", hover_color="#14532d", command=self.importar_nc_sire)
        btn_sire_nc.pack(side="left", padx=15, pady=5)

        ctk.CTkLabel(f_top, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        self.ent_buscar_nc = ctk.CTkEntry(f_top, placeholder_text="Filtrar por documento, cliente...")
        self.ent_buscar_nc.pack(side="left", fill="x", expand=True, padx=5)
        self.ent_buscar_nc.bind("<KeyRelease>", self._on_buscar_nc_keyrelease)
        self.ent_buscar_nc.bind("<Return>", lambda e: self.cargar_datos_nc(reset_pagina=True))

        btn_refresh = ctk.CTkButton(f_top, text="🔄 Actualizar Tabla", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=lambda: self.cargar_datos_nc(reset_pagina=True))
        btn_refresh.pack(side="right", padx=15, pady=5)

        f_tabla = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=0)

        columnas = ("num", "id_fac", "fecha_orig", "doc_orig", "doc_nc", "cliente", "monto_anulado", "estado", "pdf_nc")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")

        self.tabla.heading("num", text="N°")
        self.tabla.heading("id_fac", text="ID (Oculto)")
        self.tabla.heading("fecha_orig", text="Fecha Fac. Original")
        self.tabla.heading("doc_orig", text="Doc. Original")
        self.tabla.heading("doc_nc", text="Nota Crédito")
        self.tabla.heading("cliente", text="Cliente")
        self.tabla.heading("monto_anulado", text="Monto Anulado")
        self.tabla.heading("estado", text="Estado Actual")
        self.tabla.heading("pdf_nc", text="PDF Nota Crédito")

        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id_fac", width=0, stretch=tk.NO)
        self.tabla.column("fecha_orig", width=100, anchor="center")
        self.tabla.column("doc_orig", width=100, anchor="center")
        self.tabla.column("doc_nc", width=100, anchor="center")
        self.tabla.column("cliente", width=250, anchor="w")
        self.tabla.column("monto_anulado", width=110, anchor="e")
        self.tabla.column("estado", width=180, anchor="center")
        self.tabla.column("pdf_nc", width=120, anchor="center")

        self.tabla.config(displaycolumns=("num", "fecha_orig", "doc_orig", "doc_nc", "cliente", "monto_anulado", "estado", "pdf_nc"))
        self.tabla.bind("<Double-1>", self.abrir_pdf_nc)

        scroll_y = ctk.CTkScrollbar(f_tabla, orientation="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        
        # 🚀 BOTONES PAGINACIÓN
        f_paginacion = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_paginacion.pack(fill="x", padx=15, pady=(5, 5))
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        ctk.CTkLabel(f_paginacion, text="💡 Haz doble clic sobre un registro para descargar/visualizar el PDF oficial de la Nota de Crédito.", font=("Arial", 11, "italic"), text_color="gray").pack(side="right", padx=10)

        self.main_root.after(200, lambda: self.cargar_datos_nc(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_datos_nc()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_datos_nc()

    def _on_buscar_nc_keyrelease(self, event=None):
        if self._id_debounce_busqueda is not None:
            try: self.main_root.after_cancel(self._id_debounce_busqueda)
            except Exception: pass
        self._id_debounce_busqueda = self.main_root.after(350, lambda: self.cargar_datos_nc(reset_pagina=True))

    def cargar_datos_nc(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = ""
        if hasattr(self, 'ent_buscar_nc'):
            filtro = self.ent_buscar_nc.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        self._token_carga_nc = getattr(self, '_token_carga_nc', 0) + 1
        token_actual = self._token_carga_nc
        
        clave_cache = f"notas_credito_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is not None:
            self._pintar_tabla_nc(datos, token_actual)
        else:
            self.tabla.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", "", "", ""))
            
            threading.Thread(
                target=self._worker_cargar_datos_nc,
                args=(filtro, token_actual, offset, clave_cache),
                daemon=True
            ).start()

    def _worker_cargar_datos_nc(self, filtro, token_actual, offset, clave_cache):
        filas_resultado = []
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if not conn:
            self.main_root.after(0, lambda: self._pintar_tabla_nc([], token_actual))
            return
        try:
            cursor = conn.cursor()
            
            if filtro == "":
                cursor.execute("SELECT id, fecha, numero_documento, cliente, total, COALESCE(det_monto, 0), estado_sunat, enlace_pdf_nc, tipo_documento FROM facturas_emitidas WHERE estado_sunat LIKE '%Anulada%' ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
            else:
                val = f"%{filtro}%"
                cursor.execute("""
                    SELECT id, fecha, numero_documento, cliente, total, COALESCE(det_monto, 0), estado_sunat, enlace_pdf_nc, tipo_documento 
                    FROM facturas_emitidas 
                    WHERE estado_sunat LIKE '%Anulada%' AND (numero_documento ILIKE %s OR cliente ILIKE %s)
                    ORDER BY id DESC LIMIT %s OFFSET %s
                """, (val, val, self.registros_por_pagina, offset))
                
            registros = cursor.fetchall()

            contador = (self.pagina_actual - 1) * self.registros_por_pagina + 1
            for reg in registros:
                id_fac, fecha, nro_doc, cliente, tot_bruto, det_monto, estado, link_nc, tipo_doc = reg

                tot_bruto_val = float(tot_bruto) if tot_bruto else 0.0
                det_monto_val = float(det_monto) if det_monto else 0.0
                neto_anulado = tot_bruto_val - det_monto_val

                serie_nc = "FC01" if tipo_doc and "Factura" in tipo_doc else "BC01"
                num_orig = str(nro_doc).split("-")[1] if nro_doc and "-" in str(nro_doc) else "1"
                doc_nc_str = f"{serie_nc}-{num_orig}"

                if not link_nc:
                    txt_pdf = "❌ Sin PDF / SIRE"
                else:
                    txt_pdf = "✅ Ver PDF NC"

                row_vals = (
                    contador, id_fac, fecha, nro_doc, doc_nc_str, cliente, formatear_moneda(neto_anulado), estado, txt_pdf
                )

                filas_resultado.append(row_vals)
                contador += 1
                
            cache_sistema.guardar(clave_cache, filas_resultado)
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

        self.main_root.after(0, lambda: self._pintar_tabla_nc(filas_resultado, token_actual))

    def _pintar_tabla_nc(self, filas_resultado, token_actual):
        if token_actual != getattr(self, '_token_carga_nc', token_actual):
            return
        for fila in self.tabla.get_children(): self.tabla.delete(fila)
        for row_vals in filas_resultado:
            self.tabla.insert("", tk.END, values=row_vals)
            
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1:
                self.btn_ant.configure(state="normal")
            else:
                self.btn_ant.configure(state="disabled")
                
            if len(filas_resultado) == self.registros_por_pagina:
                self.btn_sig.configure(state="normal")
            else:
                self.btn_sig.configure(state="disabled")

    def abrir_pdf_nc(self, event):
        seleccion = self.tabla.selection()
        if not seleccion: return
        id_fac = self.tabla.item(seleccion[0], "values")[1] 
        
        conn = obtener_conexion_segura()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT enlace_pdf_nc, ruc FROM facturas_emitidas f LEFT JOIN clientes c ON f.cliente = c.nombre_empresa WHERE f.id = %s", (id_fac,))
            res = cursor.fetchone()
            if res:
                link_nc = res[0]
                if link_nc and str(link_nc).startswith("http"):
                    webbrowser.open(link_nc)
                else:
                    messagebox.showinfo("Consulta SUNAT", "Este documento fue importado desde el SIRE y no posee un enlace directo al PDF.\n\nSe abrirá el portal de validación de SUNAT para que pueda consultarlo.")
                    webbrowser.open("https://e-consultaruc.sunat.gob.pe/cl-ti-itmrconsruc/jcrS00Alias")
            else:
                messagebox.showinfo("Aviso", "Este registro no se encuentra disponible.")
        except Exception as e:
            pass
        finally:
            liberar_conexion(conn)

# =========================================================
# CLASE PRINCIPAL: MÓDULO DE VENTAS (CONTENEDOR TABVIEW)
# =========================================================
class ModuloVentasApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.usuario_activo = "Desconocido"
        self.pantalla_expandida = False
        aplicar_estilo_treeview()

        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        
        header_frame = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header_frame.pack(fill="x")
        
        ctk.CTkLabel(header_frame, text="💼 MÓDULO DE VENTAS (COMERCIAL Y TESORERÍA)", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        self.btn_pantalla = ctk.CTkButton(header_frame, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="right")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        
        self.tab_emision = self.tabview.add(" 📤 1. Emisión de Facturas (Ventas) ")
        self.tab_cobros = self.tabview.add(" 💰 2. Control de Cobros y Deudas ")
        self.tab_nc = self.tabview.add(" 📄 3. Notas de Crédito ")
        
        self.app_facturas = FacturasEmitidasTab(self.tab_emision, self.parent_frame, self)
        self.app_cobros = CuentasPorCobrarTab(self.tab_cobros, self.parent_frame, self)
        self.app_nc = NotasCreditoTab(self.tab_nc, self.parent_frame, self)
        
        self.tabview.configure(command=self.al_cambiar_pestana)

    def toggle_pantalla_completa(self):
        sidebar = None
        try:
            if self.parent_frame.master:
                for child in self.parent_frame.master.winfo_children():
                    if hasattr(child, "cget") and child.cget("width") == 280:
                        sidebar = child
                        break
        except Exception: pass

        if getattr(self, "pantalla_expandida", False):
            if sidebar: sidebar.pack(side="left", fill="y", before=self.parent_frame)
            self.btn_pantalla.configure(text="[ + ] Pantalla Completa", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = False
        else:
            if sidebar: sidebar.pack_forget()
            self.btn_pantalla.configure(text="[ - ] Restaurar Vista", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = True

    def al_cambiar_pestana(self):
        if self.tabview.get() == " 💰 2. Control de Cobros y Deudas ":
            self.app_cobros.cargar_datos_cobrar(reset_pagina=True)
        elif self.tabview.get() == " 📄 3. Notas de Crédito ":
            self.app_nc.cargar_datos_nc(reset_pagina=True)
        else:
            self.app_facturas.cargar_datos_tabla(reset_pagina=True)

if __name__ == "__main__":
    pass