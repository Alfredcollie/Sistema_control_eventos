# -*- coding: utf-8 -*-

"""
=========================================================
ORDENES_COMPRA.PY (ENTERPRISE EDITION)
=========================================================
- FIX: Auto-curación síncrona para evitar Race Conditions y Caché Fantasma.
- FIX: Eliminación de conn.close() en favor de liberar_conexion(conn).
- FIX: Renderizado de Banner PDF idéntico al de Cotizaciones.
- FIX: Ajuste de márgenes y truncado en Coordenadas Logísticas para evitar solapamiento.
- Paginación Lazy Loading (50 en 50) para el Historial.
- Carga 100% Asíncrona (Cero congelamientos).
- Caché Inteligente en consultas cruzadas.
"""

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import customtkinter as ctk
from datetime import datetime
from app_paths import CONFIG_FILE
import sys
import os
import subprocess
import webbrowser
import urllib.parse
import json
import calendar
import re
import shutil
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.utils import ImageReader
    REPORTLAB_DISPONIBLE = True
except ImportError:
    REPORTLAB_DISPONIBLE = False

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# MULTIPLATAFORMA Y EXTRACCIÓN DE DATOS
# =========================================================
def abrir_documento(ruta):
    try:
        ruta_abs = os.path.abspath(ruta)
        if sys.platform == "win32":
            os.startfile(ruta_abs)
        elif sys.platform == "darwin":
            subprocess.call(["open", ruta_abs])
        else:
            subprocess.call(["xdg-open", ruta_abs])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo o carpeta:\n{e}")

def copiar_archivo_portapapeles(ruta):
    try:
        ruta_absoluta = os.path.abspath(ruta)
        if sys.platform == "darwin":
            os.system(f'osascript -e \'set the clipboard to POSIX file "{ruta_absoluta}"\'')
        elif sys.platform == "win32":
            os.system(f'powershell -command "Set-Clipboard -Path \'{ruta_absoluta}\'"')
    except Exception as e:
        print("Error copiando al portapapeles:", e)

def maximizar_ventana(ventana):
    try:
        if sys.platform == "win32":
            ventana.state("zoomed")
        elif sys.platform == "darwin":
            ventana.attributes("-zoomed", True)
        else:
            ventana.state("zoomed")
    except Exception:
        try:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

def obtener_ruta_logo():
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                return json.load(f).get("ruta_logo_cotizacion", "")
    except Exception:
        pass
    return ""

def buscar_fila_proveedor(prov):
    conn = conectar_db(silencioso=True)
    if not conn:
        return None
    fila_encontrada = None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM proveedores")
        filas = cursor.fetchall()
        target_limpio = re.sub(r'[^a-zA-Z0-9]', '', prov).upper()
        target_limpio = target_limpio.replace("SAC", "").replace("SA", "").replace("PERU", "")
        for fila in filas:
            fila_text = " ".join([str(v) for v in fila if v]).upper()
            fila_limpia = re.sub(r'[^a-zA-Z0-9]', '', fila_text)
            if target_limpio and target_limpio in fila_limpia:
                fila_encontrada = fila
                break
    except Exception:
        pass
    finally:
        liberar_conexion(conn)
    return fila_encontrada

def obtener_telefono_proveedor(prov):
    telefono_proveedor = ""
    fila = buscar_fila_proveedor(prov)
    if fila:
        for campo in fila:
            if not campo:
                continue
            val_raw = str(campo).strip()
            digits = re.sub(r'\D', '', val_raw)
            if len(digits) == 9 and digits.startswith("9"):
                telefono_proveedor = f"51{digits}"
                break
            elif len(digits) >= 11 and (digits.startswith("519") or digits.startswith("589")):
                telefono_proveedor = digits
                break
            elif val_raw.startswith("+") and len(digits) >= 10:
                telefono_proveedor = digits
                break
    return telefono_proveedor

def obtener_email_proveedor(prov):
    email_proveedor = ""
    fila = buscar_fila_proveedor(prov)
    if fila:
        for campo in fila:
            if not campo:
                continue
            val_str = str(campo).strip()
            match = re.search(r'[\w.-]+@[\w.-]+\.\w+', val_str)
            if match:
                email_proveedor = match.group(0)
                break
    return email_proveedor

# =========================================================
# CLASE: SELECTOR DE HORA Y CALENDARIO
# =========================================================
class SelectorHoraNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry, fecha_str):
        super().__init__(parent)
        self.target_entry = target_entry
        self.fecha_str = fecha_str
        self.title("Hora")
        self.geometry("260x160")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (260 // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (160 // 2)
        self.geometry(f"+{x}+{y}")
        ctk.CTkLabel(self, text="Seleccione la Hora:", font=("Arial", 13, "bold"), text_color="#1f538d").pack(pady=(15, 5))
        f_cont = ctk.CTkFrame(self, fg_color="transparent")
        f_cont.pack(pady=5)
        self.cmb_hora = ctk.CTkComboBox(f_cont, values=[f"{i:02d}" for i in range(1, 13)], width=65, state="readonly")
        self.cmb_hora.pack(side="left", padx=2)
        self.cmb_hora.set("08")
        ctk.CTkLabel(f_cont, text=":", font=("Arial", 14, "bold")).pack(side="left")
        self.cmb_min = ctk.CTkComboBox(f_cont, values=[f"{i:02d}" for i in range(0, 60, 5)], width=65, state="readonly")
        self.cmb_min.pack(side="left", padx=2)
        self.cmb_min.set("00")
        self.cmb_ampm = ctk.CTkComboBox(f_cont, values=["AM", "PM"], width=65, state="readonly")
        self.cmb_ampm.pack(side="left", padx=2)
        self.cmb_ampm.set("AM")
        ctk.CTkButton(self, text="Confirmar Logística", fg_color="#27ae60", hover_color="#1e8449", command=self.confirmar).pack(pady=(10, 15))

    def confirmar(self):
        hora_final = f"{self.fecha_str} - {self.cmb_hora.get()}:{self.cmb_min.get()} {self.cmb_ampm.get()}"
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, hora_final)
        self.destroy()


class CalendarioNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Fecha")
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
        ctk.CTkButton(self.header_frame, text="<", width=30, fg_color="transparent", command=self.prev_month).pack(side="left", padx=10, pady=10)
        self.lbl_month_year = ctk.CTkLabel(self.header_frame, text="", font=("Arial", 14, "bold"), text_color="white")
        self.lbl_month_year.pack(side="left", expand=True)
        ctk.CTkButton(self.header_frame, text=">", width=30, fg_color="transparent", command=self.next_month).pack(side="right", padx=10, pady=10)
        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)
        for i, day in enumerate(["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]):
            ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d").grid(row=0, column=i, padx=4, pady=5)
        self.update_calendar()

    def update_calendar(self):
        for w in self.days_frame.winfo_children():
            if int(w.grid_info()["row"]) > 0:
                w.destroy()
        meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        self.lbl_month_year.configure(text=f"{meses[self.current_month]} {self.current_year}")
        hoy = datetime.now()
        for r_idx, week in enumerate(calendar.monthcalendar(self.current_year, self.current_month), start=1):
            for c_idx, day in enumerate(week):
                if day != 0:
                    b_col, t_col = ("#d4edda", "#155724") if day == hoy.day and self.current_month == hoy.month and self.current_year == hoy.year else ("transparent", "black")
                    btn = ctk.CTkButton(self.days_frame, text=str(day), width=30, height=30, fg_color=b_col, text_color=t_col, font=("Arial", 11))
                    btn.configure(command=lambda d=day: self.select_date(d))
                    btn.grid(row=r_idx, column=c_idx, padx=2, pady=2)

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
        fecha_sel = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
        v_padre, e_dest = self.master, self.target_entry
        self.destroy()
        SelectorHoraNativo(v_padre, e_dest, fecha_sel)

# =========================================================
# CLASE PRINCIPAL: ÓRDENES DE COMPRA (OPTIMIZADA)
# =========================================================
_SCHEMA_ORD_OK = False

class OrdenesCompraApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo
        self.ultima_ruta_pdf = ""
        self.orden_columnas_hist = {}
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_bd()
        self.crear_interfaz()

    def inicializar_bd(self):
        global _SCHEMA_ORD_OK
        if _SCHEMA_ORD_OK:
            return
            
        def tarea_init():
            global _SCHEMA_ORD_OK
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS ordenes_compra (
                        id SERIAL PRIMARY KEY, codigo_cotizacion VARCHAR(100), proveedor VARCHAR(255), fecha_emision VARCHAR(50), 
                        locacion VARCHAR(255), fh_instalacion VARCHAR(100), fh_desmontaje VARCHAR(100), detalles_tecnicos TEXT, 
                        total_orden NUMERIC, pdf_ruta TEXT
                    )
                """)
                conn.commit()
                for sql in (
                    "ALTER TABLE ordenes_compra ADD COLUMN IF NOT EXISTS locacion VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE ordenes_compra ADD COLUMN IF NOT EXISTS numero_orden VARCHAR(100) DEFAULT ''",
                    "ALTER TABLE ordenes_compra ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 0",
                    "ALTER TABLE ordenes_compra ADD COLUMN IF NOT EXISTS estado VARCHAR(50) DEFAULT 'Activa'",
                ):
                    try:
                        cursor.execute(sql)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                _SCHEMA_ORD_OK = True
            except Exception as e:
                print("Error DB:", e)
            finally:
                liberar_conexion(conn)

        threading.Thread(target=tarea_init, daemon=True).start()

    def abrir_calendario(self, entry_objetivo, parent=None):
        CalendarioNativo(parent if parent else self.parent_frame.winfo_toplevel(), entry_objetivo)

    def abrir_carpeta_anuladas(self):
        carpeta = "ordenes_anuladas"
        if not os.path.exists(carpeta):
            os.makedirs(carpeta)
        abrir_documento(carpeta)

    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(self.frame_main, text="📝 GESTIÓN DE ÓRDENES DE COMPRA / SERVICIO", font=("Arial", 18, "bold"), text_color="#1f538d").pack(anchor="w", pady=(0, 10))
        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True)
        self.tab_generar = self.tabview.add(" ➕ 1. Generar Nuevas Órdenes ")
        self.tab_historial = self.tabview.add(" 🗂️ 2. Historial y Modificaciones ")
        self.crear_pestaña_generar()
        self.crear_pestaña_historial()
        self.tabview.configure(command=self.al_cambiar_pestana)

    def crear_pestaña_generar(self):
        f_top = ctk.CTkFrame(self.tab_generar, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_top.pack(fill="x", pady=(0, 10), ipadx=10, ipady=10)
        ctk.CTkLabel(f_top, text="1. Cotización Aprobada:", font=("Arial", 12, "bold")).grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.cmb_cotizacion = ctk.CTkComboBox(f_top, width=350, state="readonly", command=self.al_seleccionar_cotizacion)
        self.cmb_cotizacion.grid(row=0, column=1, padx=10, pady=10)
        ctk.CTkLabel(f_top, text="2. Proveedor Pendiente:", font=("Arial", 12, "bold")).grid(row=0, column=2, padx=10, pady=10, sticky="w")
        self.cmb_proveedor = ctk.CTkComboBox(f_top, width=300, state="readonly", command=self.al_seleccionar_proveedor)
        self.cmb_proveedor.grid(row=0, column=3, padx=10, pady=10)
        f_centro = ctk.CTkFrame(self.tab_generar, fg_color="transparent")
        f_centro.pack(fill="both", expand=True)
        f_izq = ctk.CTkFrame(f_centro, corner_radius=10)
        f_izq.pack(side="left", fill="both", expand=True, padx=(0, 10))
        ctk.CTkLabel(f_izq, text="Servicios Contratados a este Proveedor", font=("Arial", 12, "bold"), text_color="#1f538d").pack(anchor="w", padx=10, pady=10)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"), background="#f0f0f0")
        self.tabla_servicios = ttk.Treeview(f_izq, columns=("categoria", "cant", "p_lista", "p_dscto", "p_costo_real"), show="headings", height=6)
        self.tabla_servicios.heading("categoria", text="Categoría / Detalle")
        self.tabla_servicios.heading("cant", text="Cant.")
        self.tabla_servicios.heading("p_lista", text="P. Lista")
        self.tabla_servicios.heading("p_dscto", text="P. Acordado")
        self.tabla_servicios.heading("p_costo_real", text="Costo Total")
        self.tabla_servicios.column("categoria", width=200, anchor="w")
        self.tabla_servicios.column("cant", width=50, anchor="center")
        self.tabla_servicios.column("p_lista", width=80, anchor="e")
        self.tabla_servicios.column("p_dscto", width=80, anchor="e")
        self.tabla_servicios.column("p_costo_real", width=90, anchor="e")
        self.tabla_servicios.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.lbl_total_orden = ctk.CTkLabel(f_izq, text="Monto Total de la Orden: S/ 0.00", font=("Arial", 14, "bold"), text_color="#c0392b")
        self.lbl_total_orden.pack(anchor="e", padx=15, pady=10)
        f_der = ctk.CTkFrame(f_centro, width=360, corner_radius=10)
        f_der.pack(side="right", fill="y")
        ctk.CTkLabel(f_der, text="Coordenadas Logísticas", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=10)
        ctk.CTkLabel(f_der, text="Locación del Evento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(2, 0))
        self.ent_locacion = ctk.CTkEntry(f_der, placeholder_text="Ej: Hacienda Los Ficus")
        self.ent_locacion.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(f_der, text="Fecha y Hora de Instalación:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(10, 0))
        f_inst = ctk.CTkFrame(f_der, fg_color="transparent")
        f_inst.pack(fill="x", padx=15, pady=2)
        self.ent_instalacion = ctk.CTkEntry(f_inst, placeholder_text="Ej: 15/10/2026 - 08:00 AM")
        self.ent_instalacion.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_inst, text="[ 📅 ]", width=40, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_instalacion)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_der, text="Fecha y Hora de Desmontaje:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(10, 0))
        f_desm = ctk.CTkFrame(f_der, fg_color="transparent")
        f_desm.pack(fill="x", padx=15, pady=2)
        self.ent_desmontaje = ctk.CTkEntry(f_desm, placeholder_text="Ej: 16/10/2026 - 02:00 PM")
        self.ent_desmontaje.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_desm, text="[ 📅 ]", width=40, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_desmontaje)).pack(side="right", padx=(5, 0))
        f_bottom = ctk.CTkFrame(self.tab_generar, fg_color="transparent")
        f_bottom.pack(fill="x", pady=10)
        ctk.CTkLabel(f_bottom, text="Detalles Adicionales y Especificaciones Técnicas (Extraídas de Cotización):", font=("Arial", 12, "bold")).pack(anchor="w")
        self.txt_detalles = ctk.CTkTextbox(f_bottom, height=80, border_width=1)
        self.txt_detalles.pack(fill="x", pady=5)
        f_botones = ctk.CTkFrame(self.tab_generar, fg_color="transparent")
        f_botones.pack(fill="x", pady=5)
        ctk.CTkButton(f_botones, text="📄 Solo Generar", font=("Arial", 13, "bold"), fg_color="#1f538d", hover_color="#163b65", height=40, command=lambda: self.generar_orden("solo")).pack(side="left", expand=True, fill="x", padx=3)
        ctk.CTkButton(f_botones, text="💬 Generar y Enviar WA", font=("Arial", 13, "bold"), fg_color="#27ae60", hover_color="#1e8449", height=40, command=lambda: self.generar_orden("whatsapp")).pack(side="left", expand=True, fill="x", padx=3)
        ctk.CTkButton(f_botones, text="📧 Generar y Enviar Mail", font=("Arial", 13, "bold"), fg_color="#e67e22", hover_color="#d35400", height=40, command=lambda: self.generar_orden("email")).pack(side="left", expand=True, fill="x", padx=3)
        self.cargar_cotizaciones_aprobadas()

    def crear_pestaña_historial(self):
        f_busqueda = ctk.CTkFrame(self.tab_historial, fg_color="transparent")
        f_busqueda.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_historial = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por N° Orden, Cotización, Evento, Proveedor o Monto...")
        self.ent_buscar_historial.pack(side="left", fill="x", expand=True)
        self.ent_buscar_historial.bind("<KeyRelease>", lambda e: self.buscar_historial_con_retraso())
        self.ent_buscar_historial.bind("<Return>", lambda e: self.cargar_historial_ordenes(reset_pagina=True))
        
        f_tabla = ctk.CTkFrame(self.tab_historial, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, pady=10)
        self.tree_historial = ttk.Treeview(f_tabla, columns=("id", "num_orden", "codigo", "evento", "proveedor", "fecha", "total"), show="headings")
        self.tree_historial.heading("id", text="ID ↕", command=lambda: self.ordenar_por_columna("id", True))
        self.tree_historial.heading("num_orden", text="N° Orden ↕", command=lambda: self.ordenar_por_columna("num_orden", False))
        self.tree_historial.heading("codigo", text="Cot. Ref. ↕", command=lambda: self.ordenar_por_columna("codigo", False))
        self.tree_historial.heading("evento", text="Nombre del Evento ↕", command=lambda: self.ordenar_por_columna("evento", False))
        self.tree_historial.heading("proveedor", text="Proveedor ↕", command=lambda: self.ordenar_por_columna("proveedor", False))
        self.tree_historial.heading("fecha", text="Emisión ↕", command=lambda: self.ordenar_por_columna("fecha", False))
        self.tree_historial.heading("total", text="Total S/ ↕", command=lambda: self.ordenar_por_columna("total", True))
        self.tree_historial.column("id", width=40, anchor="center")
        self.tree_historial.column("num_orden", width=120, anchor="center")
        self.tree_historial.column("codigo", width=100, anchor="center")
        self.tree_historial.column("evento", width=160, anchor="w")
        self.tree_historial.column("proveedor", width=160, anchor="w")
        self.tree_historial.column("fecha", width=110, anchor="center")
        self.tree_historial.column("total", width=90, anchor="e")
        scroll_y = ctk.CTkScrollbar(f_tabla, command=self.tree_historial.yview)
        self.tree_historial.configure(yscrollcommand=scroll_y.set)
        self.tree_historial.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y", padx=5)
        self.tree_historial.bind("<Double-1>", lambda e: self.abrir_ventana_modificacion())
        
        f_botones_hist = ctk.CTkFrame(self.tab_historial, fg_color="transparent")
        f_botones_hist.pack(fill="x", pady=10)
        
        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_botones_hist, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        ctk.CTkButton(f_botones_hist, text="📂 Ver Anuladas", font=("Arial", 12, "bold"), fg_color="#8e44ad", hover_color="#732d91", command=self.abrir_carpeta_anuladas).pack(side="left", padx=(15, 5))
        ctk.CTkButton(f_botones_hist, text="🔄 Actualizar", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=lambda: self.cargar_historial_ordenes(reset_pagina=True)).pack(side="left", padx=5)
        
        ctk.CTkButton(f_botones_hist, text="🗑️ Anular Orden", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_orden_historial).pack(side="right", padx=5)
        ctk.CTkButton(f_botones_hist, text="✏️ Modificar y Reenviar", font=("Arial", 12, "bold"), fg_color="#d35400", hover_color="#a84300", command=self.abrir_ventana_modificacion).pack(side="right", padx=5)
        ctk.CTkButton(f_botones_hist, text="👁️ Ver PDF", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.ver_pdf_historial).pack(side="right", padx=5)

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_historial_ordenes()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_historial_ordenes()

    def buscar_historial_con_retraso(self):
        if hasattr(self, "_busc_hist_job"):
            try:
                self.parent_frame.after_cancel(self._busc_hist_job)
            except Exception:
                pass
        self._busc_hist_job = self.parent_frame.after(350, lambda: self.cargar_historial_ordenes(reset_pagina=True))

    def ordenar_por_columna(self, columna, es_numerico):
        elementos = [(self.tree_historial.set(item, columna), item) for item in self.tree_historial.get_children("")]
        ascendente = self.orden_columnas_hist.get(columna, True)
        self.orden_columnas_hist[columna] = not ascendente
        if es_numerico:
            def parsear_numero(val):
                try:
                    return float(val.replace(",", "").replace("S/", "").strip())
                except ValueError:
                    return 0.0
            elementos.sort(key=lambda el: parsear_numero(el[0]), reverse=not ascendente)
        else:
            elementos.sort(key=lambda el: el[0].lower() if el[0] else "", reverse=not ascendente)
        for index, (_, item) in enumerate(elementos):
            self.tree_historial.move(item, "", index)

    def al_cambiar_pestana(self):
        if self.tabview.get() == " 🗂️ 2. Historial y Modificaciones ":
            self.cargar_historial_ordenes(reset_pagina=True)
        else:
            self.cargar_cotizaciones_aprobadas()

    def cargar_historial_ordenes(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tree_historial.get_children():
            self.tree_historial.delete(item)
            
        filtro = ""
        if hasattr(self, 'ent_buscar_historial'):
            filtro = self.ent_buscar_historial.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"ordenes_generadas_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_historial(datos)
        else:
            self.tree_historial.insert("", tk.END, values=("", "Cargando datos...", "", "", "", "", ""))
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            query = """
                                SELECT o.id, o.numero_orden, o.version, o.codigo_cotizacion, c.nombre_evento, o.proveedor, o.fecha_emision, o.total_orden 
                                FROM ordenes_compra o 
                                LEFT JOIN cotizaciones c ON o.codigo_cotizacion = c.codigo_cotizacion 
                                WHERE o.estado != 'Anulada' OR o.estado IS NULL
                                ORDER BY o.id DESC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            query = """
                                SELECT o.id, o.numero_orden, o.version, o.codigo_cotizacion, c.nombre_evento, o.proveedor, o.fecha_emision, o.total_orden 
                                FROM ordenes_compra o 
                                LEFT JOIN cotizaciones c ON o.codigo_cotizacion = c.codigo_cotizacion 
                                WHERE (o.estado != 'Anulada' OR o.estado IS NULL)
                                AND (o.numero_orden ILIKE %s OR o.codigo_cotizacion ILIKE %s OR c.nombre_evento ILIKE %s OR o.proveedor ILIKE %s)
                                ORDER BY o.id DESC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (val, val, val, val, self.registros_por_pagina, offset))
                            
                        for r in cursor.fetchall():
                            num_base = r[1] if r[1] else f"OC-{r[3]}-P{r[0]}"
                            ver = r[2] or 0
                            n_imprimir = num_base if ver == 0 else f"{num_base}-{ver}"
                            ev_nombre = r[4] if r[4] else "Evento no registrado"
                            rows.append((r[0], n_imprimir, r[3], ev_nombre, r[5], r[6], f"{r[7]:,.2f}"))
                            
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception as e:
                        print("Error al cargar historial OC:", e)
                    finally:
                        liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._pintar_historial(rows))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_historial(self, rows):
        for item in self.tree_historial.get_children():
            self.tree_historial.delete(item)
            
        for row_vals in rows:
            self.tree_historial.insert("", tk.END, values=row_vals)
            
        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")
            
        if len(rows) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")

    def ver_pdf_historial(self):
        sel = self.tree_historial.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione una orden del historial.")
        id_orden = self.tree_historial.item(sel[0], "values")[0]
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT pdf_ruta FROM ordenes_compra WHERE id = %s", (id_orden,))
                ruta = cursor.fetchone()
                if ruta and ruta[0] and os.path.exists(ruta[0]):
                    abrir_documento(ruta[0])
                else:
                    messagebox.showerror("Error", "El archivo PDF no se encuentra en la ruta especificada.")
            except Exception:
                pass
            finally:
                liberar_conexion(conn)

    def eliminar_orden_historial(self):
        sel = self.tree_historial.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione una orden del historial para anular.")
        id_orden = self.tree_historial.item(sel[0], "values")[0]
        cod_cot = self.tree_historial.item(sel[0], "values")[2]
        prov = self.tree_historial.item(sel[0], "values")[4]
        msg = f"¿Estás seguro de que deseas anular y archivar la orden de {prov} (Cot: {cod_cot})?\n\nLa orden pasará a la carpeta de 'Anuladas' y el proveedor volverá a aparecer como pendiente."
        if messagebox.askyesno("Confirmar Anulación", msg):
            conn = conectar_db()
            if not conn:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden anular órdenes en Modo Lectura.")
                return
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE ordenes_compra SET estado = 'Anulada' WHERE id=%s", (id_orden,))
                conn.commit()
                
                cache_sistema.invalidar()
                
                carpeta = "ordenes_generadas"
                carpeta_anuladas = "ordenes_anuladas"
                if not os.path.exists(carpeta_anuladas):
                    os.makedirs(carpeta_anuladas)
                if os.path.exists(carpeta):
                    prov_limpio = prov.replace(' ', '_')
                    for archivo in os.listdir(carpeta):
                        if cod_cot in archivo and prov_limpio in archivo:
                            ruta_archivo = os.path.join(carpeta, archivo)
                            nueva_ruta = os.path.join(carpeta_anuladas, archivo)
                            try:
                                shutil.move(ruta_archivo, nueva_ruta)
                            except Exception as e:
                                print(f"No se pudo archivar {ruta_archivo}: {e}")
                                
                registrar_auditoria(self.usuario_activo, "Órdenes de Compra", f"Anuló la O/C de {prov} (Cot: {cod_cot})")
                messagebox.showinfo("Éxito", "Orden anulada y archivada correctamente.")
                self.cargar_historial_ordenes(reset_pagina=True)
                if self.cmb_cotizacion.get().startswith(cod_cot):
                    self.al_seleccionar_cotizacion(self.cmb_cotizacion.get())
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)

    def cargar_cotizaciones_aprobadas(self):
        clave_cache = "lista_eventos_aprobados"
        cotizaciones = cache_sistema.obtener(clave_cache)

        if cotizaciones is not None:
            self._aplicar_cotizaciones_combo(cotizaciones)
        else:
            self.cmb_cotizacion.set("Cargando cotizaciones...")
            def tarea():
                cots = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT codigo_cotizacion, nombre_evento, fecha_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        hoy = datetime.now().date()
                        for r in cursor.fetchall():
                            cod_cot, nom_ev, fec_str = r[0], r[1], r[2]
                            incluir = True
                            if fec_str:
                                try:
                                    f_dt = datetime.strptime(fec_str, "%Y-%m-%d").date() if "-" in fec_str else datetime.strptime(fec_str, "%d/%m/%Y").date()
                                    if f_dt < hoy:
                                        incluir = False
                                except Exception:
                                    pass
                            if incluir:
                                cots.append(f"{cod_cot} | {nom_ev}")
                        cache_sistema.guardar(clave_cache, cots)
                    except Exception:
                        pass
                    finally:
                        liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._aplicar_cotizaciones_combo(cots))

            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_cotizaciones_combo(self, cotizaciones):
        if cotizaciones:
            self.cmb_cotizacion.configure(values=cotizaciones)
            self.cmb_cotizacion.set("Seleccione una cotización...")
        else:
            self.cmb_cotizacion.configure(values=["No hay cotizaciones vigentes"])
            self.cmb_cotizacion.set("No hay cotizaciones vigentes")
        self.cmb_proveedor.configure(values=["-"])
        self.cmb_proveedor.set("-")

    def al_seleccionar_cotizacion(self, choice):
        if "Seleccione" in choice or "No hay" in choice or "Cargando" in choice:
            return
        codigo_cot = choice.split(" | ")[0].strip()
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT locacion_evento FROM cotizaciones WHERE codigo_cotizacion = %s", (codigo_cot,))
            loc = cursor.fetchone()
            self.ent_locacion.delete(0, tk.END)
            self.ent_locacion.insert(0, loc[0] if loc and loc[0] else "Por definir")
            cursor.execute("SELECT DISTINCT proveedor_nombre FROM cotizacion_proveedores WHERE codigo_cotizacion = %s AND proveedor_nombre IS NOT NULL AND proveedor_nombre != ''", (codigo_cot,))
            provs_totales = [str(r[0]) for r in cursor.fetchall()]
            cursor.execute("SELECT proveedor FROM ordenes_compra WHERE codigo_cotizacion = %s AND (estado != 'Anulada' OR estado IS NULL)", (codigo_cot,))
            provs_listos = [str(r[0]) for r in cursor.fetchall()]
            provs_pendientes = [p for p in provs_totales if p not in provs_listos]
            if provs_pendientes:
                self.cmb_proveedor.configure(values=provs_pendientes)
                self.cmb_proveedor.set("Seleccione proveedor...")
            else:
                self.cmb_proveedor.configure(values=["✅ Todas las órdenes generadas"])
                self.cmb_proveedor.set("✅ Todas las órdenes generadas")
            for item in self.tabla_servicios.get_children():
                self.tabla_servicios.delete(item)
            self.lbl_total_orden.configure(text="Monto Total de la Orden: S/ 0.00")
            self.txt_detalles.delete("1.0", tk.END)
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

    def al_seleccionar_proveedor(self, choice):
        if "Seleccione" in choice or "Todas las" in choice or "Sin proveedores" in choice:
            return
        codigo_cot = self.cmb_cotizacion.get().split(" | ")[0].strip()
        proveedor = choice.strip()
        for item in self.tabla_servicios.get_children():
            self.tabla_servicios.delete(item)
        self.txt_detalles.delete("1.0", tk.END)
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        total_orden = 0.0
        notas_proveedor = []
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT categoria_suministro, cantidad, precio_lista, precio_descuento, notes_negociacion 
                FROM cotizacion_proveedores WHERE codigo_cotizacion = %s AND proveedor_nombre = %s
            """, (codigo_cot, proveedor))
            for r in cursor.fetchall():
                cat = r[0].replace("('", "").replace("',)", "").replace("',", "").strip("() '\", ")
                cant = r[1]
                p_lista = float(r[2] or 0)
                p_dscto = float(r[3] or 0)
                notas = r[4]
                costo_real = p_dscto if p_dscto > 0 else p_lista
                total_orden += costo_real
                self.tabla_servicios.insert("", tk.END, values=(cat, cant, f"{p_lista:.2f}", f"{p_dscto:.2f}", f"{costo_real:.2f}"))
                if notas and str(notas).strip():
                    n_limpia = str(notas).replace("[B]", "").replace("[/B]", "").replace("[M]", "").replace("[/M]", "").strip()
                    notas_proveedor.append(f"• {cat}:\n  {n_limpia}\n")
            self.lbl_total_orden.configure(text=f"Monto Total de la Orden: S/ {total_orden:,.2f}")
            self.total_actual = total_orden
            if notas_proveedor:
                self.txt_detalles.insert("1.0", "\n".join(notas_proveedor))
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

    # 🚀 FIX: DIBUJADO DE LOGO Y DIMENSIONES PROPORCIONALES
    def fabricar_pdf(self, cod_cot, evento, prov, locacion, inst, desm, detalles, fecha, items_servicios, total_orden, num_orden_imprimir):
        total_orden = float(total_orden)
        carpeta_destino = "ordenes_generadas"
        if not os.path.exists(carpeta_destino):
            os.makedirs(carpeta_destino)
        marca_tiempo = datetime.now().strftime("%H%M%S")
        nombre_archivo = os.path.join(carpeta_destino, f"Orden_{num_orden_imprimir}_{prov.replace(' ', '_')}_{marca_tiempo}.pdf")
        
        c = canvas.Canvas(nombre_archivo, pagesize=letter)
        ancho_hoja = 612.0 # 8.5 x 72
        
        config = {}
        try:
            if os.path.exists(RUTA_CONFIG):
                with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                    config = json.load(f)
        except Exception: pass
        
        ruta_usar = None
        ruta_conf = config.get("ruta_logo_cotizacion", "")
        if ruta_conf and os.path.exists(ruta_conf):
            ruta_usar = ruta_conf
        if not ruta_usar:
            fallbacks = [
                "LogoCotizacion.png",
                "LogoCotizacion.jpg",
                "Logo_Collie_Software.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.jpg"
            ]
            for fallback in fallbacks:
                if os.path.exists(fallback):
                    ruta_usar = fallback
                    break
                    
        offset_y = 0
        if ruta_usar:
            try:
                img = ImageReader(ruta_usar)
                img_w, img_h = img.getSize()
                alto_proporcional = ancho_hoja * (float(img_h) / float(img_w))
                y_logo = 792.0 - alto_proporcional
                c.drawImage(ruta_usar, 0, y_logo, width=ancho_hoja, height=alto_proporcional, mask='auto')
                offset_y = (y_logo - 30) - 740.0
                y_cursor = 740.0 + offset_y
            except Exception:
                y_cursor = 740.0
        else:
            y_cursor = 740.0
            
        c.setFont("Helvetica-Bold", 16)
        c.setFillColorRGB(0.12, 0.32, 0.55)
        c.drawString(40, y_cursor, "ORDEN DE SERVICIO / COMPRA")
        
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(0, 0, 0)
        c.drawRightString(572, y_cursor + 2, "RUC. 20613989146")
        
        c.setFont("Helvetica", 10)
        c.drawString(410, y_cursor - 15, f"Fecha: {fecha}")
        c.drawString(410, y_cursor - 30, f"Cotización Ref: {cod_cot}")
        c.drawString(410, y_cursor - 45, f"N° Orden: {num_orden_imprimir}")
        y_cursor -= 60.0
        c.setLineWidth(1)
        c.line(40, y_cursor, 572, y_cursor)
        y_cursor -= 20.0
        
        c.setFont("Helvetica-Bold", 10)
        c.drawString(40, y_cursor, "DATOS DEL PROVEEDOR:")
        c.drawString(340, y_cursor, "COORDENADAS LOGÍSTICAS:")
        
        c.setFont("Helvetica", 10)
        prov_display = prov[:40] + "..." if len(prov) > 43 else prov
        evento_display = evento[:40] + "..." if len(evento) > 43 else evento
        c.drawString(40, y_cursor - 15, f"Empresa / Nombre: {prov_display}")
        c.drawString(40, y_cursor - 30, f"Evento: {evento_display}")
        
        loc_display = locacion[:35] + "..." if locacion and len(locacion) > 38 else (locacion if locacion else 'A coordinar')
        c.drawString(340, y_cursor - 15, f"Locación: {loc_display}")
        c.drawString(340, y_cursor - 30, f"Instalación: {inst if inst else 'A coordinar'}")
        c.drawString(340, y_cursor - 45, f"Desmontaje: {desm if desm else 'A coordinar'}")
        
        y_cursor -= 55.0
        c.line(40, y_cursor, 572, y_cursor)
        y_cursor -= 20.0
        
        c.setFont("Helvetica-Bold", 10)
        c.drawString(40, y_cursor, "DETALLE DE SERVICIOS SOLICITADOS:")
        y_pos = y_cursor - 20.0
        c.setFont("Helvetica-Bold", 9)
        c.drawString(40, y_pos, "CANT.")
        c.drawString(90, y_pos, "DESCRIPCIÓN / CATEGORÍA")
        c.drawString(400, y_pos, "P. UNIT ACORDADO")
        c.drawString(500, y_pos, "TOTAL")
        y_pos -= 8.0
        c.line(40, y_pos, 572, y_pos)
        y_pos -= 15.0
        c.setFont("Helvetica", 9)
        for valores in items_servicios:
            cat = valores[0][:45] + "..." if len(valores[0]) > 45 else valores[0]
            c.drawString(45, y_pos, str(valores[1]))
            c.drawString(90, y_pos, cat)
            costo_linea = float(valores[4])
            try:
                p_unit = costo_linea / float(valores[1])
            except Exception:
                p_unit = 0.0
            c.drawString(400, y_pos, f"S/ {p_unit:,.2f}")
            c.drawString(500, y_pos, f"S/ {costo_linea:,.2f}")
            y_pos -= 20.0
            if y_pos < 250:
                c.showPage()
                y_pos = 730.0
        subtotal = total_orden / 1.18
        igv = total_orden - subtotal
        c.line(40, y_pos + 10, 572, y_pos + 10)
        c.setFont("Helvetica", 10)
        c.drawString(380, y_pos - 10, "SUBTOTAL:")
        c.drawString(480, y_pos - 10, f"S/ {subtotal:,.2f}")
        c.drawString(380, y_pos - 25, "IGV (18%):")
        c.drawString(480, y_pos - 25, f"S/ {igv:,.2f}")
        c.setFont("Helvetica-Bold", 12)
        c.drawString(380, y_pos - 45, "MONTO TOTAL:")
        c.setFillColorRGB(0.75, 0.22, 0.16)
        c.drawString(480, y_pos - 45, f"S/ {total_orden:,.2f}")
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 10)
        y_pos -= 80.0
        c.drawString(40, y_pos, "ESPECIFICACIONES TÉCNICAS / NOTAS ADICIONALES:")
        y_pos -= 20.0
        c.setFont("Helvetica", 9)
        for linea in detalles.split("\n"):
            while len(linea) > 0:
                if y_pos < 50:
                    c.showPage()
                    y_pos = 730.0
                    c.setFont("Helvetica", 9)
                c.drawString(40, y_pos, linea[:100])
                linea = linea[100:]
                y_pos -= 12.0
        c.save()
        return nombre_archivo

    def generar_orden(self, accion="solo"):
        cot_str = self.cmb_cotizacion.get()
        prov = self.cmb_proveedor.get()
        loc = self.ent_locacion.get().strip()
        inst = self.ent_instalacion.get().strip()
        desm = self.ent_desmontaje.get().strip()
        detalles = self.txt_detalles.get("1.0", "end-1c").strip()
        if "Seleccione" in cot_str or "Seleccione" in prov or "Todas" in prov:
            return messagebox.showwarning("Incompleto", "Seleccione una cotización y un proveedor válido.")
        if not REPORTLAB_DISPONIBLE:
            return messagebox.showerror("Librería", "Falta ReportLab.")
        codigo_cot = cot_str.split(" | ")[0].strip()
        evento_nombre = cot_str.split(" | ")[1].strip() if " | " in cot_str else ""
        fecha_emision = datetime.now().strftime("%d/%m/%Y")
        servicios_lista = [self.tabla_servicios.item(item, "values") for item in self.tabla_servicios.get_children()]
        total_orden = getattr(self, 'total_actual', 0.0)
        
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden generar órdenes en Modo Lectura.")
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(id) FROM ordenes_compra WHERE codigo_cotizacion = %s", (codigo_cot,))
            count = cursor.fetchone()[0]
            numero_orden_base = f"OC-{codigo_cot}-P{count + 1}"
            version_inicial = 0
            ruta_pdf = self.fabricar_pdf(codigo_cot, evento_nombre, prov, loc, inst, desm, detalles, fecha_emision, servicios_lista, total_orden, numero_orden_base)
            self.ultima_ruta_pdf = ruta_pdf
            cursor.execute("""
                INSERT INTO ordenes_compra (codigo_cotizacion, proveedor, fecha_emision, locacion, fh_instalacion, fh_desmontaje, detalles_tecnicos, total_orden, pdf_ruta, numero_orden, version, estado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (codigo_cot, prov, fecha_emision, loc, inst, desm, detalles, total_orden, ruta_pdf, numero_orden_base, version_inicial, 'Activa'))

            def _extraer_fecha(texto):
                if not texto:
                    return ""
                m = re.search(r"(\d{2}/\d{2}/\d{4})", str(texto))
                return m.group(1) if m else str(texto).strip()[:10]

            evento_asociado = f"{codigo_cot} | {evento_nombre}" if evento_nombre else codigo_cot
            tareas_auto = []
            fecha_inst = _extraer_fecha(inst)
            fecha_desm = _extraer_fecha(desm)
            if fecha_inst:
                tareas_auto.append(("Instalación", fecha_inst, inst))
            if fecha_desm:
                tareas_auto.append(("Desmontaje", fecha_desm, desm))

            for accion_t, fecha_lim, detalle in tareas_auto:
                nombre_t = f"{accion_t}: {prov}"
                cursor.execute(
                    "SELECT id FROM tareas_evento WHERE evento_asociado = %s AND nombre_tarea = %s",
                    (evento_asociado, nombre_t)
                )
                if not cursor.fetchone():
                    cursor.execute(
                        "SELECT COALESCE(MAX(orden), 0) FROM tareas_evento WHERE evento_asociado = %s",
                        (evento_asociado,)
                    )
                    nuevo_orden = cursor.fetchone()[0] + 1
                    notas_t = (
                        f"Generada automáticamente desde la O/C {numero_orden_base}.\n"
                        f"Locación: {loc if loc else 'A coordinar'}.\n"
                        f"Horario programado: {detalle if detalle else 'Por coordinar'}."
                    )
                    cursor.execute("""
                        INSERT INTO tareas_evento (evento_asociado, nombre_tarea, responsable, fecha_limite, estado, notas, orden, tipo_pago)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """, (evento_asociado, nombre_t, prov, fecha_lim, "Pendiente", notas_t, nuevo_orden, "Crédito"))

            conn.commit()
            cache_sistema.invalidar()
            
            if tareas_auto:
                registrar_auditoria(self.usuario_activo, "Órdenes de Compra", f"Generó O/C {numero_orden_base} para {prov} y agendó {len(tareas_auto)} tarea(s) en el cronograma")
            else:
                registrar_auditoria(self.usuario_activo, "Órdenes de Compra", f"Generó O/C {numero_orden_base} para {prov}")            
            if accion == "solo":
                messagebox.showinfo("Éxito", f"Orden de Compra generada exitosamente.\nN° Orden: {numero_orden_base}")
                abrir_documento(ruta_pdf)
            else:
                datos_mod = {"prov": prov, "loc": loc, "inst": inst, "desm": desm, "evento": evento_nombre, "ruta_pdf": ruta_pdf}
                if accion == "whatsapp":
                    self.ejecutar_envio_whatsapp(datos_mod, es_modificacion=False)
                elif accion == "email":
                    self.ejecutar_envio_email(datos_mod, es_modificacion=False)
            self.al_seleccionar_cotizacion(cot_str)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo generar la orden:\n{e}")
        finally:
            liberar_conexion(conn)

    def ejecutar_envio_whatsapp(self, datos_mod, es_modificacion=False):
        prov = datos_mod["prov"]
        loc = datos_mod["loc"]
        inst = datos_mod["inst"]
        desm = datos_mod["desm"]
        evento_nombre = datos_mod["evento"]
        ruta_pdf = datos_mod["ruta_pdf"]
        telefono_proveedor = obtener_telefono_proveedor(prov)
        if not telefono_proveedor:
            tel_manual = simpledialog.askstring("Celular no detectado", f"No se detectó automáticamente el celular de:\n{prov}\n\nIngresa su número para enviar el mensaje (ej: 987654321):", parent=self.parent_frame.winfo_toplevel())
            if tel_manual:
                digits = re.sub(r'\D', '', tel_manual)
                telefono_proveedor = f"51{digits}" if len(digits) == 9 and digits.startswith("9") else digits
        if es_modificacion:
            mensaje = (
                f"Hola {prov},\n\n"
                f"Te compartimos la *Orden de Servicio ACTUALIZADA* para el evento '{evento_nombre}'.\n\n"
                f"⚠️ *NOTA LOGÍSTICA: Hubo cambios en la orden original.*\n\n"
                f"📍 *Nueva Locación:* {loc if loc else 'A coordinar'}\n"
                f"🛠️ *Nueva Instalación:* {inst if inst else 'A coordinar'}\n"
                f"🚚 *Nuevo Desmontaje:* {desm if desm else 'A coordinar'}\n\n"
                f"Por favor, revisa el archivo PDF adjunto para validar las actualizaciones.\n"
                f"¡Quedamos atentos a tu confirmación!"
            )
        else:
            mensaje = (
                f"Hola {prov},\n\n"
                f"Te compartimos la Orden de Servicio Oficial para el evento '{evento_nombre}'.\n\n"
                f"📍 *Locación:* {loc if loc else 'A coordinar'}\n"
                f"🛠️ *Instalación:* {inst if inst else 'A coordinar'}\n"
                f"🚚 *Desmontaje:* {desm if desm else 'A coordinar'}\n\n"
                f"Por favor, revisa el archivo PDF adjunto con las especificaciones técnicas completas.\n"
                f"¡Quedamos atentos a tu confirmación!"
            )
        mensaje_codificado = urllib.parse.quote(mensaje)
        respuesta = messagebox.askyesnocancel("WhatsApp", "¿Abrir WhatsApp de Escritorio?\n\n[Sí] = App de Escritorio\n[No] = WhatsApp Web\n[Cancelar] = Cancelar")
        if respuesta is None:
            return
        if telefono_proveedor:
            url_whatsapp = f"{'whatsapp://send' if respuesta else 'https://api.whatsapp.com/send'}?phone={telefono_proveedor}&text={mensaje_codificado}"
        else:
            url_whatsapp = f"{'whatsapp://send' if respuesta else 'https://web.whatsapp.com/send'}?text={mensaje_codificado}"
        try:
            copiar_archivo_portapapeles(ruta_pdf)
            messagebox.showinfo("¡Listo!", f"PDF copiado al portapapeles.\n\nAbriendo el chat de {prov}...\nHaz clic en la caja de mensaje y presiona Pegar (Ctrl+V / Cmd+V).")
            webbrowser.open(url_whatsapp)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def ejecutar_envio_email(self, datos_mod, es_modificacion=False):
        prov = datos_mod["prov"]
        loc = datos_mod["loc"]
        inst = datos_mod["inst"]
        desm = datos_mod["desm"]
        evento_nombre = datos_mod["evento"]
        ruta_pdf = datos_mod["ruta_pdf"]
        email_prov = obtener_email_proveedor(prov)
        if not email_prov:
            email_prov = simpledialog.askstring("Correo no detectado", f"No se detectó automáticamente el correo de:\n{prov}\n\nIngresa su correo electrónico:", parent=self.parent_frame.winfo_toplevel())
            if not email_prov:
                return
        if es_modificacion:
            asunto = f"ACTUALIZACIÓN: Orden de Servicio - {evento_nombre}"
            cuerpo = (
                f"Hola {prov},\n\n"
                f"Te compartimos la Orden de Servicio ACTUALIZADA para el evento '{evento_nombre}'.\n\n"
                f"NOTA LOGÍSTICA: Hubo cambios en la orden original.\n"
                f"- Nueva Locación: {loc if loc else 'A coordinar'}\n"
                f"- Nueva Instalación: {inst if inst else 'A coordinar'}\n"
                f"- Nuevo Desmontaje: {desm if desm else 'A coordinar'}\n\n"
                f"Por favor, revisa el archivo PDF que adjuntamos para validar las actualizaciones.\n"
                f"Quedamos atentos a tu confirmación."
            )
        else:
            asunto = f"Orden de Servicio Oficial - {evento_nombre}"
            cuerpo = (
                f"Hola {prov},\n\n"
                f"Te compartimos la Orden de Servicio Oficial para el evento '{evento_nombre}'.\n\n"
                f"- Locación: {loc if loc else 'A coordinar'}\n"
                f"- Instalación: {inst if inst else 'A coordinar'}\n"
                f"- Desmontaje: {desm if desm else 'A coordinar'}\n\n"
                f"Por favor, revisa el archivo PDF que adjuntamos con las especificaciones técnicas completas.\n"
                f"Quedamos atentos a tu confirmación."
            )
        asunto_enc = urllib.parse.quote(asunto)
        cuerpo_enc = urllib.parse.quote(cuerpo)
        mailto_url = f"mailto:{email_prov}?subject={asunto_enc}&body={cuerpo_enc}"
        try:
            copiar_archivo_portapapeles(ruta_pdf)
            messagebox.showinfo("¡Listo para enviar!", f"El PDF de la Orden fue copiado al portapapeles.\n\nSe abrirá tu gestor de correos ({email_prov}).\n\n1. Haz clic derecho y selecciona 'Pegar' (o presiona Ctrl+V) para adjuntar el PDF.\n2. Haz clic en Enviar.")
            webbrowser.open(mailto_url)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def abrir_ventana_modificacion(self):
        sel = self.tree_historial.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione una orden del historial para modificar.")
        id_orden = self.tree_historial.item(sel[0], "values")[0]
        cod_cot = self.tree_historial.item(sel[0], "values")[2]
        prov = self.tree_historial.item(sel[0], "values")[4]
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden modificar órdenes en Modo Lectura.")
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT locacion, fh_instalacion, fh_desmontaje, detalles_tecnicos, total_orden, numero_orden, version, pdf_ruta FROM ordenes_compra WHERE id=%s", (id_orden,))
            ord_data = cursor.fetchone()
            if not ord_data:
                return
            loc_db, inst_db, desm_db, det_db, total_db, num_orden_db, version_db, ruta_pdf_antigua = ord_data
            cursor.execute("SELECT nombre_evento FROM cotizaciones WHERE codigo_cotizacion=%s", (cod_cot,))
            ev_data = cursor.fetchone()
            evento_nombre = ev_data[0] if ev_data else ""
            cursor.execute("SELECT categoria_suministro, cantidad, precio_lista, precio_descuento FROM cotizacion_proveedores WHERE codigo_cotizacion = %s AND proveedor_nombre = %s", (cod_cot, prov))
            servicios_lista = []
            for r in cursor.fetchall():
                cat = r[0].replace("('", "").replace("',)", "").replace("',", "").strip("() '\", ")
                c_real = float(r[3]) if float(r[3]) > 0 else float(r[2])
                servicios_lista.append((cat, r[1], r[2], r[3], c_real))
        except Exception as e:
            return messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)
            
        v_mod = ctk.CTkToplevel(self.parent_frame.winfo_toplevel())
        v_mod.title(f"Modificar Orden de {prov}")
        v_mod.geometry("500x550")
        v_mod.grab_set()
        ctk.CTkLabel(v_mod, text="✏️ Modificar Parámetros de la Orden", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=15)
        f_form = ctk.CTkFrame(v_mod, fg_color="transparent")
        f_form.pack(fill="both", expand=True, padx=20)
        ctk.CTkLabel(f_form, text="Locación del Evento:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(5, 2))
        ent_loc_mod = ctk.CTkEntry(f_form, width=400)
        ent_loc_mod.pack(fill="x", pady=2)
        ent_loc_mod.insert(0, loc_db)
        ctk.CTkLabel(f_form, text="F/H Instalación:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 2))
        f_i_mod = ctk.CTkFrame(f_form, fg_color="transparent")
        f_i_mod.pack(fill="x", pady=2)
        ent_inst_mod = ctk.CTkEntry(f_i_mod)
        ent_inst_mod.pack(side="left", fill="x", expand=True)
        ent_inst_mod.insert(0, inst_db)
        ctk.CTkButton(f_i_mod, text="📅", width=30, command=lambda: self.abrir_calendario(ent_inst_mod, parent=v_mod)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_form, text="F/H Desmontaje:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 2))
        f_d_mod = ctk.CTkFrame(f_form, fg_color="transparent")
        f_d_mod.pack(fill="x", pady=2)
        ent_desm_mod = ctk.CTkEntry(f_d_mod)
        ent_desm_mod.pack(side="left", fill="x", expand=True)
        ent_desm_mod.insert(0, desm_db)
        ctk.CTkButton(f_d_mod, text="📅", width=30, command=lambda: self.abrir_calendario(ent_desm_mod, parent=v_mod)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_form, text="Detalles Adicionales:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 2))
        txt_det_mod = ctk.CTkTextbox(f_form, height=100)
        txt_det_mod.pack(fill="x", pady=2)
        txt_det_mod.insert("1.0", det_db)

        def ejecutar_modificacion(accion="solo"):
            n_loc = ent_loc_mod.get().strip()
            n_inst = ent_inst_mod.get().strip()
            n_desm = ent_desm_mod.get().strip()
            n_det = txt_det_mod.get("1.0", "end-1c").strip()
            n_fecha_emision = datetime.now().strftime("%d/%m/%Y (Modif.)")
            num_ord_base = num_orden_db if num_orden_db else f"OC-{cod_cot}-L{id_orden}"
            n_version = (version_db or 0) + 1
            num_orden_imprimir = f"{num_ord_base}-{n_version}"
            
            c2 = conectar_db()
            if not c2:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede guardar la modificación en Modo Lectura.", parent=v_mod)
                return

            if ruta_pdf_antigua and os.path.exists(ruta_pdf_antigua):
                carpeta_anuladas = "ordenes_anuladas"
                if not os.path.exists(carpeta_anuladas):
                    os.makedirs(carpeta_anuladas)
                try:
                    shutil.move(ruta_pdf_antigua, os.path.join(carpeta_anuladas, os.path.basename(ruta_pdf_antigua)))
                except Exception:
                    pass
            try:
                n_ruta_pdf = self.fabricar_pdf(cod_cot, evento_nombre, prov, n_loc, n_inst, n_desm, n_det, n_fecha_emision, servicios_lista, total_db, num_orden_imprimir)
                cursor = c2.cursor()
                cursor.execute("""
                    UPDATE ordenes_compra SET locacion=%s, fh_instalacion=%s, fh_desmontaje=%s, detalles_tecnicos=%s, fecha_emision=%s, pdf_ruta=%s, version=%s WHERE id=%s
                """, (n_loc, n_inst, n_desm, n_det, n_fecha_emision, n_ruta_pdf, n_version, id_orden))
                c2.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Órdenes", f"Modificó O/C a versión {num_orden_imprimir} para {prov}")
                v_mod.destroy()
                self.cargar_historial_ordenes(reset_pagina=True)
                
                if accion == "solo":
                    messagebox.showinfo("Éxito", f"Orden modificada a la versión {n_version}.\nEl PDF anterior se movió a 'Anuladas'.")
                    abrir_documento(n_ruta_pdf)
                else:
                    datos_mod = {"prov": prov, "loc": n_loc, "inst": n_inst, "desm": n_desm, "evento": evento_nombre, "ruta_pdf": n_ruta_pdf}
                    if accion == "whatsapp":
                        self.ejecutar_envio_whatsapp(datos_mod, es_modificacion=True)
                    elif accion == "email":
                        self.ejecutar_envio_email(datos_mod, es_modificacion=True)
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(c2)

        f_btn_mod = ctk.CTkFrame(v_mod, fg_color="transparent")
        f_btn_mod.pack(fill="x", padx=20, pady=20)
        ctk.CTkButton(f_btn_mod, text="💾 Guardar", fg_color="#1f538d", hover_color="#163b65", command=lambda: ejecutar_modificacion("solo")).pack(side="left", fill="x", expand=True, padx=2)
        ctk.CTkButton(f_btn_mod, text="💬 Guardar + WA", fg_color="#27ae60", hover_color="#1e8449", command=lambda: ejecutar_modificacion("whatsapp")).pack(side="left", fill="x", expand=True, padx=2)
        ctk.CTkButton(f_btn_mod, text="📧 Guardar + Mail", fg_color="#e67e22", hover_color="#d35400", command=lambda: ejecutar_modificacion("email")).pack(side="left", fill="x", expand=True, padx=2)


if __name__ == "__main__":
    pass