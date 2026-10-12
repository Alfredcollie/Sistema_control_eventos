# -*- coding: utf-8 -*-
"""
ESTADISTICAS_FINANCIERA.PY - DASHBOARD GERENCIAL DE RENTABILIDAD (v2)

Novedades v2
------------
* FILTRO POR CATEGORIA: reune TODAS las categorias del sistema y las usa para
  filtrar compras, pagos, proveedores, ordenes de servicio y conciliacion:
    - categorias de gasto definidas en Banco (Ajustes -> Categorias de Gastos)
    - categoria de cada comprobante recibido (facturas_recibidas.categoria)
    - categoria de suministro de cada pago (pagos_comprobantes.categoria_suministro)
    - categorias de proveedores (proveedores.categoria .. categoria_5)
    - categoria de flota (flota_vehiculos.categoria) y servicios de taller
    - categoria detectada en la conciliacion bancaria ("Principal - Sub - ...")
* FILTRO POR PLACA / VEHICULO: flota, compras por 'evento_asociado', ordenes de
  servicio, inspecciones y unidades de cliente. Compara placas sin guiones
  (CHD-817 == CHD817).
* FILTRO POR CUENTA / BANCO mejorado: coincide por etiqueta, nombre de banco y
  por los digitos de la cuenta (igual que el modulo de Banco).
* PESTANAS con todo el detalle del sistema y EXPORTACION A EXCEL con una hoja
  por seccion (22 hojas: compras, pagos, combustible, cobranza, flota, etc.).
"""
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import calendar
import os
import sys
import json
import re
import subprocess
from datetime import datetime
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema
from dialogos_seguros import guardar_archivo_dialogo
from tareas_seguras import ejecutar_en_hilo
from app_paths import CONFIG_FILE
from config_nube import cargar_bancos

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA: Función universal para abrir archivos (Excel)
# =========================================================
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
# 🚀 MOTOR DE CONFIGURACIÓN REGIONAL
# =========================================================
def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "ruta_drive": "",
        "impresora": "",
        "cuentas_bancarias": []
    }
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception: pass
    return config

# 🚀 FUNCIÓN PARA LEER EL PORCENTAJE DE RENTA ANUAL DESDE CONFIG
def obtener_porcentaje_renta_anual():
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config = json.load(f)
                return float(config.get("renta_anual_porcentaje", "0.0"))
    except Exception: pass
    return 0.0

# 🚀 FUNCIÓN PARA LEER EL PORCENTAJE ISR MENSUAL DESDE LA CONFIGURACIÓN GENERAL
def obtener_porcentaje_renta_mensual():
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config = json.load(f)
                return float(config.get("renta_mensual_porcentaje", "0.0"))
    except Exception: pass
    return 0.0

# 🚀 CATEGORÍAS DE GASTO (las mismas que usa el módulo de Banco)
CATEGORIAS_GASTOS_DEFAULT = {
    "Gastos Fijos": ["Alquiler", "Planilla / Sueldos", "Servicios (Luz, Agua, Internet)", "Seguros"],
    "Gastos Operativos": ["Combustible", "Mantenimiento", "Repuestos", "Peajes"],
    "Otros": ["Varios"],
}

def cargar_categorias_banco():
    """Categorías de gasto configuradas en el módulo de Banco (config local)."""
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cats = cfg.get("categorias_gastos")
            if isinstance(cats, dict):
                return {k: (v if isinstance(v, list) else []) for k, v in cats.items()}
    except Exception:
        pass
    return dict(CATEGORIAS_GASTOS_DEFAULT)

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

def formatear_numero(valor, decimales=2):
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    try: valor = float(valor)
    except: valor = 0.0
    if formato == "1.000,00":
        return f"{valor:,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{valor:,.{decimales}f}"

# =========================================================
# 🚀 NORMALIZADORES Y PARSERS COMPARTIDOS
# =========================================================
def norm_txt(valor):
    """Texto comparable: sin espacios dobles y en mayúsculas."""
    return re.sub(r"\s+", " ", str(valor if valor is not None else "")).strip().upper()

def norm_placa(valor):
    """Placa comparable: solo letras y números (CHD-817 == CHD817)."""
    return re.sub(r"[^A-Z0-9]", "", norm_txt(valor))

def parse_num(valor):
    """Convierte cualquier texto de monto/cantidad a float (1,234.56 y 1.234,56)."""
    if isinstance(valor, (int, float)):
        return float(valor)
    s = str(valor if valor is not None else "").strip()
    if not s:
        return 0.0
    s = re.sub(r"[^\d.,+-]", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        try:
            return float(re.sub(r"[^\d.]", "", s))
        except ValueError:
            return 0.0

def categoria_de_conciliacion(descripcion):
    """'Gastos Fijos - Planilla / Sueldos - AFP HABITAD' -> 'Gastos Fijos - Planilla / Sueldos'."""
    partes = [p.strip() for p in str(descripcion or "").split(" - ") if p.strip()]
    if len(partes) >= 2:
        return f"{partes[0]} - {partes[1]}"
    return partes[0] if partes else "(Sin categoría)"

# --- CLASE DEL CALENDARIO NATIVO PARA EL RANGO DE FECHAS ---
class CalendarioNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Seleccionar Fecha")
        self.geometry("310x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (310 // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (320 // 2)
        self.geometry(f"+{x}+{y}")

        self.current_year = datetime.now().year
        self.current_month = datetime.now().month
        self.meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

        self.header_frame = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        self.header_frame.pack(fill="x")

        ctk.CTkButton(self.header_frame, text="<", width=25, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.prev_month).pack(side="left", padx=5, pady=10)

        self.cmb_mes = ctk.CTkComboBox(self.header_frame, values=self.meses_nombres, width=100, command=self.cambiar_mes_combo)
        self.cmb_mes.pack(side="left", padx=2, pady=10)

        anios = [str(y) for y in range(datetime.now().year - 80, datetime.now().year + 20)]
        self.cmb_anio = ctk.CTkComboBox(self.header_frame, values=anios, width=75, command=self.cambiar_anio_combo)
        self.cmb_anio.pack(side="left", padx=2, pady=10)

        ctk.CTkButton(self.header_frame, text=">", width=25, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.next_month).pack(side="right", padx=5, pady=10)

        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)

        dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i, day in enumerate(dias_semana):
            ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d").grid(row=0, column=i, padx=5, pady=5)

        self.update_calendar()

    def cambiar_mes_combo(self, choice):
        self.current_month = self.meses_nombres.index(choice) + 1
        self.update_calendar()

    def cambiar_anio_combo(self, choice):
        try:
            self.current_year = int(choice)
            self.update_calendar()
        except ValueError:
            pass

    def update_calendar(self):
        self.cmb_mes.set(self.meses_nombres[self.current_month - 1])
        self.cmb_anio.set(str(self.current_year))

        for widget in self.days_frame.winfo_children():
            if int(widget.grid_info()["row"]) > 0: widget.destroy()

        cal = calendar.monthcalendar(self.current_year, self.current_month)
        hoy = datetime.now()

        for row_idx, week in enumerate(cal, start=1):
            for col_idx, day in enumerate(week):
                if day != 0:
                    btn_color = "#d4edda" if day == hoy.day and self.current_month == hoy.month and self.current_year == hoy.year else "transparent"
                    txt_color = "#155724" if btn_color == "#d4edda" else "black"
                    btn = ctk.CTkButton(self.days_frame, text=str(day), width=30, height=30, fg_color=btn_color, text_color=txt_color, hover_color="#e0e0e0", font=("Arial", 11))
                    btn.configure(command=lambda d=day: self.select_date(d))
                    btn.grid(row=row_idx, column=col_idx, padx=3, pady=2)

    def prev_month(self):
        self.current_month -= 1
        if self.current_month < 1: self.current_month = 12; self.current_year -= 1
        self.update_calendar()

    def next_month(self):
        self.current_month += 1
        if self.current_month > 12: self.current_month = 1; self.current_year += 1
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
# 🚀 DEFINICIÓN DE SECCIONES (una hoja de Excel por sección)
# =========================================================
TXT, MON, NUM, ENT = "txt", "mon", "num", "ent"

SECCIONES_UI = [
    ("resumen", " 📊 Resumen "),
    ("categorias", " 🗂 Categorías "),
    ("placas", " 🚚 Placas "),
    ("proveedores_res", " 🏭 Proveedores "),
    ("cuentas", " 🏦 Cuentas "),
    ("compras", " 🧾 Compras "),
    ("pagos", " 💸 Pagos "),
    ("cobranza", " 💰 Cobranza "),
    ("flota", " 🚗 Flota "),
    ("bitacora", " 🗓 Actividad "),
]

ORDEN_EXPORT = [
    "resumen", "categorias", "placas", "proveedores_res", "cuentas",
    "compras", "pagos", "combustible", "cobranza", "cobranza_unidades",
    "cobranza_viajes", "flota", "ordenes_servicio", "conciliacion",
    "transferencias", "impuestos", "bitacora", "actividad_modulo",
    "clientes", "proveedores_cat", "choferes", "inspecciones",
]

NOMBRES_HOJA = {
    "resumen": "Resumen Gerencial",
    "categorias": "Por Categoria",
    "placas": "Por Placa",
    "proveedores_res": "Por Proveedor",
    "cuentas": "Cuentas y Bancos",
    "compras": "Compras Detalle",
    "pagos": "Pagos Detalle",
    "combustible": "Combustible Detalle",
    "cobranza": "Cobranza Quincenas",
    "cobranza_unidades": "Cobranza Unidades",
    "cobranza_viajes": "Cobranza Viajes",
    "flota": "Flota Vehiculos",
    "ordenes_servicio": "Ordenes de Servicio",
    "conciliacion": "Conciliacion Bancaria",
    "transferencias": "Transferencias",
    "impuestos": "Impuestos",
    "bitacora": "Bitacora",
    "actividad_modulo": "Actividad por Modulo",
    "clientes": "Clientes",
    "proveedores_cat": "Proveedores Catalogo",
    "choferes": "Choferes",
    "inspecciones": "Inspecciones",
}


class EstadisticasFinancieraApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo or "Desconocido"
        self.tasa_renta_anual = obtener_porcentaje_renta_anual()
        self.tasa_renta_mensual = obtener_porcentaje_renta_mensual()

        # Catálogos que llenan los filtros
        self.bancos_config = []
        self.lista_categorias = ["Todas las Categorías"]
        self.lista_placas = ["Todas las Placas / Vehículos"]
        self.lista_clientes = ["Todos los Clientes"]
        self.lista_proveedores = ["Todos los Proveedores"]

        # Resultado del último cálculo
        self.datos = None
        self.trees = {}
        self._listas_aplicadas = False
        self._placa_objetivo = ""

        self.crear_interfaz()
        self.cargar_bancos_y_listas()

        self.parent_frame.after(200, self.cargar_kpis)

    def abrir_calendario(self, entry_objetivo):
        CalendarioNativo(self.parent_frame.winfo_toplevel(), entry_objetivo)

    # =====================================================
    # INTERFAZ
    # =====================================================
    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=10)

        ctk.CTkLabel(
            self.frame_main,
            text="📈 DASHBOARD GERENCIAL DE RENTABILIDAD Y ESTADÍSTICAS DEL SISTEMA",
            font=("Arial", 16, "bold"),
            text_color="#1f538d"
        ).pack(anchor="w", pady=(0, 8))

        self._construir_filtros()

        # -------- PESTAÑAS CON EL DETALLE COMPLETO --------
        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(8, 0))

        for clave, titulo in SECCIONES_UI:
            tab = self.tabview.add(titulo)
            if clave == "resumen":
                self._construir_tab_resumen(tab)
            else:
                self.trees[clave] = self._crear_tree(tab)

    def _construir_filtros(self):
        f_filtro = ctk.CTkFrame(self.frame_main, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0", corner_radius=10)
        f_filtro.pack(fill="x", pady=(0, 8), ipadx=10, ipady=8)

        # ---- Fila 1: Cuenta / Banco  +  Categoría ----
        f_banco = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_banco.pack(fill="x", pady=2, padx=10)
        ctk.CTkLabel(f_banco, text="Filtrar por Cuenta / Banco:", font=("Arial", 12, "bold"), width=185, anchor="e").pack(side="left", padx=(0, 10))
        self.combo_banco = ctk.CTkComboBox(f_banco, values=["Todas las Cuentas"], state="readonly", width=300)
        self.combo_banco.pack(side="left")
        self.combo_banco.set("Todas las Cuentas")
        ctk.CTkLabel(f_banco, text="Filtrar por Categoría:", font=("Arial", 12, "bold"), width=145, anchor="e").pack(side="left", padx=(20, 10))
        self.combo_categoria = ctk.CTkComboBox(f_banco, values=["Todas las Categorías"], state="readonly", width=360)
        self.combo_categoria.pack(side="left")
        self.combo_categoria.set("Todas las Categorías")

        # ---- Fila 2: Placa / Cliente / Proveedor ----
        f_cp = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_cp.pack(fill="x", pady=(6, 0), padx=10)

        ctk.CTkLabel(f_cp, text="Filtrar por Placa / Vehículo:", font=("Arial", 12, "bold"), width=185, anchor="e").pack(side="left", padx=(0, 10))
        self.combo_placa = ctk.CTkComboBox(f_cp, values=["Todas las Placas / Vehículos"], state="readonly", width=195)
        self.combo_placa.pack(side="left")
        self.combo_placa.set("Todas las Placas / Vehículos")

        ctk.CTkLabel(f_cp, text="Cliente:", font=("Arial", 12, "bold"), width=65, anchor="e").pack(side="left", padx=(15, 10))
        self.combo_cliente = ctk.CTkComboBox(f_cp, values=["Todos los Clientes"], state="readonly", width=230)
        self.combo_cliente.pack(side="left")
        self.combo_cliente.set("Todos los Clientes")

        ctk.CTkLabel(f_cp, text="Proveedor:", font=("Arial", 12, "bold"), width=90, anchor="e").pack(side="left", padx=(15, 10))
        self.combo_proveedor = ctk.CTkComboBox(f_cp, values=["Todos los Proveedores"], state="readonly", width=230)
        self.combo_proveedor.pack(side="left")
        self.combo_proveedor.set("Todos los Proveedores")

        # ---- Fila 3: Tipo de fecha ----
        f_periodo = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_periodo.pack(fill="x", pady=(6, 0), padx=10)

        ctk.CTkLabel(f_periodo, text="Tipo de Fecha:", font=("Arial", 12, "bold"), width=185, anchor="e").pack(side="left", padx=(0, 10))

        self.tipo_fecha_var = ctk.StringVar(value="Mensual/Anual")
        self.opcion_mensual = ctk.CTkRadioButton(f_periodo, text="Mensual / Anual", variable=self.tipo_fecha_var, value="Mensual/Anual", command=self.toggle_fecha_modo)
        self.opcion_mensual.pack(side="left", padx=10)

        self.opcion_rango = ctk.CTkRadioButton(f_periodo, text="Rango de Fechas Exacto", variable=self.tipo_fecha_var, value="Rango", command=self.toggle_fecha_modo)
        self.opcion_rango.pack(side="left", padx=10)

        self.f_controles_fecha = ctk.CTkFrame(f_filtro, fg_color="transparent")
        self.f_controles_fecha.pack(fill="x", pady=4, padx=10)

        self.f_mensual = ctk.CTkFrame(self.f_controles_fecha, fg_color="transparent")
        ctk.CTkLabel(self.f_mensual, text="Mes y Año:", font=("Arial", 12, "bold"), width=185, anchor="e").pack(side="left", padx=(0, 10))

        meses = ["Todos los meses", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        self.combo_mes = ctk.CTkComboBox(self.f_mensual, values=meses, state="readonly", width=140)
        self.combo_mes.pack(side="left", padx=5)
        self.combo_mes.set("Todos los meses")

        anios = [str(y) for y in range(2020, 2036)]
        self.combo_anio = ctk.CTkComboBox(self.f_mensual, values=anios, state="readonly", width=100)
        self.combo_anio.pack(side="left", padx=5)
        self.combo_anio.set(str(datetime.now().year))

        self.f_rango = ctk.CTkFrame(self.f_controles_fecha, fg_color="transparent")
        ctk.CTkLabel(self.f_rango, text="Desde:", font=("Arial", 12, "bold"), width=185, anchor="e").pack(side="left", padx=(0, 10))

        fmt_fecha = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        self.ent_fecha_ini = ctk.CTkEntry(self.f_rango, width=100, placeholder_text=fmt_fecha)
        self.ent_fecha_ini.pack(side="left")
        ctk.CTkButton(self.f_rango, text="📅", width=30, height=28, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha_ini)).pack(side="left", padx=5)

        ctk.CTkLabel(self.f_rango, text="Hasta:", font=("Arial", 12, "bold")).pack(side="left", padx=10)
        self.ent_fecha_fin = ctk.CTkEntry(self.f_rango, width=100, placeholder_text=fmt_fecha)
        self.ent_fecha_fin.pack(side="left")
        ctk.CTkButton(self.f_rango, text="📅", width=30, height=28, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha_fin)).pack(side="left", padx=5)

        self.f_mensual.pack(side="left", fill="x")

        # ---- Fila 4: Botones ----
        f_btn_buscar = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_btn_buscar.pack(fill="x", pady=(8, 2), padx=10)

        ctk.CTkButton(f_btn_buscar, text="🔍 Procesar y Calcular Estadísticas", font=("Arial", 11, "bold"), fg_color="#1f538d", hover_color="#163b65", height=32, command=self.cargar_kpis).pack(side="left", expand=True, fill="x", padx=(0, 8))
        ctk.CTkButton(f_btn_buscar, text="📊 Exportar TODO el Sistema a Excel", font=("Arial", 11, "bold"), fg_color="#27ae60", hover_color="#1e8449", height=32, command=self.exportar_excel).pack(side="left", expand=True, fill="x", padx=(8, 0))

        self.lbl_estado = ctk.CTkLabel(f_filtro, text="Listo. Elige tus filtros y pulsa «Procesar y Calcular Estadísticas».",
                                       font=("Arial", 10, "italic"), text_color="#7f8c8d", anchor="w")
        self.lbl_estado.pack(fill="x", padx=12, pady=(2, 2))

    def _construir_tab_resumen(self, tab):
        scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        f_tarjetas = ctk.CTkFrame(scroll, fg_color="transparent")
        f_tarjetas.pack(fill="x", pady=(0, 10))

        self.card_ventas = self.crear_tarjeta(f_tarjetas, "VENTAS DEL PERIODO (Facturado Neto)", formatear_moneda(0), "#1f538d", 0, 0)
        self.card_cobrado = self.crear_tarjeta(f_tarjetas, "COBRADO EN EL PERIODO (Dinero Real)", formatear_moneda(0), "#27ae60", 0, 1)
        self.card_por_cobrar = self.crear_tarjeta(f_tarjetas, "DEUDA TOTAL POR COBRAR (Al día de hoy)", formatear_moneda(0), "#e74c3c", 0, 2)

        self.card_compras = self.crear_tarjeta(f_tarjetas, "COMPRAS DEL PERIODO (Facturado Neto)", formatear_moneda(0), "#34495e", 1, 0)
        self.card_pagado = self.crear_tarjeta(f_tarjetas, "PAGADO EN EL PERIODO (Dinero Real)", formatear_moneda(0), "#7f8c8d", 1, 1)
        self.card_por_pagar = self.crear_tarjeta(f_tarjetas, "DEUDA TOTAL A PROVEEDORES (Al día de hoy)", formatear_moneda(0), "#e74c3c", 1, 2)

        f_rentabilidad = ctk.CTkFrame(scroll, fg_color="transparent")
        f_rentabilidad.pack(fill="x", pady=6)

        self.card_rentabilidad = self.crear_tarjeta_larga(f_rentabilidad, "RENTABILIDAD DEL NEGOCIO (Ventas Netas - Compras Netas)", formatear_moneda(0), "#1f538d")
        self.card_caja = self.crear_tarjeta_larga(f_rentabilidad, "FLUJO DE CAJA DEL PERIODO (Cobrado - Pagado)", formatear_moneda(0), "#27ae60")
        self.card_saldo_banco = self.crear_tarjeta_larga(f_rentabilidad, "SALDO TOTAL DISPONIBLE EN BANCOS (Saldo inicial + Cobros - Pagos)", formatear_moneda(0), "#16a085")
        self.card_combustible = self.crear_tarjeta_larga(f_rentabilidad, "COMBUSTIBLE DEL PERIODO (Importe / Galones o m³)", formatear_moneda(0), "#8e44ad")

        self.card_provision_renta = self.crear_tarjeta_larga(f_rentabilidad, f"PROVISIÓN IMPUESTO A LA RENTA ANUAL ({self.tasa_renta_anual}%)", formatear_moneda(0), "#e67e22")
        self.card_provision_renta_mensual = self.crear_tarjeta_larga(f_rentabilidad, f"PROVISIÓN ISR MENSUAL ({self.tasa_renta_mensual}%)", formatear_moneda(0), "#d35400")

        ctk.CTkLabel(scroll, text="Detalle completo de indicadores", font=("Arial", 13, "bold"), text_color="#1f538d").pack(anchor="w", pady=(12, 4))
        self.trees["resumen"] = self._crear_tree(scroll, altura=16)

    def _crear_tree(self, parent, altura=17):
        cont = ctk.CTkFrame(parent, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=2, pady=2)

        try:
            estilo = ttk.Style()
            estilo.theme_use("clam")
            estilo.configure("Treeview", background="#ffffff", foreground="#000000",
                             rowheight=24, fieldbackground="#ffffff", font=("Arial", 11))
            estilo.configure("Treeview.Heading", font=("Arial", 11, "bold"),
                             background="#1f538d", foreground="white")
            estilo.map("Treeview", background=[("selected", "#1f538d")])
        except Exception:
            pass

        tree = ttk.Treeview(cont, columns=(), show="headings", height=altura)
        vs = ttk.Scrollbar(cont, orient="vertical", command=tree.yview)
        hs = ttk.Scrollbar(cont, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        cont.grid_rowconfigure(0, weight=1)
        cont.grid_columnconfigure(0, weight=1)
        return tree

    def toggle_fecha_modo(self):
        if self.tipo_fecha_var.get() == "Mensual/Anual":
            self.f_rango.pack_forget()
            self.f_mensual.pack(side="left", fill="x")
        else:
            self.f_mensual.pack_forget()
            self.f_rango.pack(side="left", fill="x")

    def crear_tarjeta(self, parent, titulo, valor, color_borde, row, col):
        frame = ctk.CTkFrame(parent, corner_radius=10, border_width=2, border_color=color_borde, fg_color="#ffffff")
        frame.grid(row=row, column=col, padx=10, pady=8, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        ctk.CTkLabel(frame, text=titulo, font=("Arial", 11, "bold"), text_color="gray").pack(pady=(15, 5))
        lbl_valor = ctk.CTkLabel(frame, text=valor, font=("Arial", 22, "bold"), text_color=color_borde)
        lbl_valor.pack(pady=(0, 15))
        return lbl_valor

    def crear_tarjeta_larga(self, parent, titulo, valor, color_borde):
        frame = ctk.CTkFrame(parent, corner_radius=10, border_width=2, border_color=color_borde, fg_color="#ffffff")
        frame.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(frame, text=titulo, font=("Arial", 12, "bold"), text_color="gray").pack(pady=(12, 2))
        lbl_valor = ctk.CTkLabel(frame, text=valor, font=("Arial", 22, "bold"), text_color=color_borde)
        lbl_valor.pack(pady=(0, 12))
        return lbl_valor

    # =====================================================
    # CARGA DE CATÁLOGOS (BANCOS, CATEGORÍAS, PLACAS, ...)
    # =====================================================
    def cargar_bancos_y_listas(self):
        try:
            self.bancos_config = cargar_bancos() or []
        except Exception:
            self.bancos_config = []

        lista_cuentas = ["Todas las Cuentas"]
        for b in self.bancos_config:
            etiqueta = f"{str(b.get('banco', '')).strip()} - {str(b.get('cuenta', '')).strip()}".strip(" -")
            if etiqueta:
                lista_cuentas.append(etiqueta)
        lista_cuentas.extend(["Efectivo / Caja Chica", "Tarjeta de Crédito", "Tarjeta de Débito", "Cheque", "Otro"])

        self.combo_banco.configure(values=lista_cuentas)
        self.combo_banco.set("Todas las Cuentas")

        cli_mem = cache_sistema.obtener('lista_clientes_flota_combobox')
        prov_mem = cache_sistema.obtener('lista_proveedores_combobox')
        cat_mem = cache_sistema.obtener('estadisticas_lista_categorias')
        plac_mem = cache_sistema.obtener('estadisticas_lista_placas')

        if all(v is not None for v in (cli_mem, prov_mem, cat_mem, plac_mem)):
            self._aplicar_listas(cat_mem, plac_mem, cli_mem, prov_mem)
        else:
            ejecutar_en_hilo(self.parent_frame, self._tarea_listas,
                             al_terminar=self._pintar_listas)

    def _tarea_listas(self, estado):
        conn = conectar_db(silencioso=True)
        categorias, placas, clientes, proveedores = [], [], [], []

        # --- Catálogo de CATEGORÍAS (todas las fuentes del sistema) ---
        cats = set()
        for principal, subs in cargar_categorias_banco().items():
            principal = str(principal).strip()
            if principal:
                cats.add(principal)                      # permite filtrar todo el grupo
            for sub in (subs or []):
                sub = str(sub).strip()
                if sub:
                    cats.add(f"{principal} - {sub}" if principal else sub)
        categorias = sorted(cats, key=lambda s: norm_txt(s))

        if conn:
            try:
                cur = conn.cursor()
                consultas_cat = [
                    "SELECT DISTINCT categoria FROM facturas_recibidas WHERE categoria IS NOT NULL AND TRIM(categoria) <> ''",
                    "SELECT DISTINCT categoria_suministro FROM pagos_comprobantes WHERE categoria_suministro IS NOT NULL AND TRIM(categoria_suministro) <> ''",
                    "SELECT DISTINCT categoria FROM proveedores WHERE categoria IS NOT NULL AND TRIM(categoria) <> ''",
                    "SELECT DISTINCT categoria_2 FROM proveedores WHERE categoria_2 IS NOT NULL AND TRIM(categoria_2) <> ''",
                    "SELECT DISTINCT categoria_3 FROM proveedores WHERE categoria_3 IS NOT NULL AND TRIM(categoria_3) <> ''",
                    "SELECT DISTINCT categoria_4 FROM proveedores WHERE categoria_4 IS NOT NULL AND TRIM(categoria_4) <> ''",
                    "SELECT DISTINCT categoria_5 FROM proveedores WHERE categoria_5 IS NOT NULL AND TRIM(categoria_5) <> ''",
                    "SELECT DISTINCT categoria FROM flota_vehiculos WHERE categoria IS NOT NULL AND TRIM(categoria) <> ''",
                    "SELECT DISTINCT servicio FROM ordenes_servicio_flota WHERE servicio IS NOT NULL AND TRIM(servicio) <> ''",
                ]
                for sql in consultas_cat:
                    try:
                        cur.execute(sql)
                        for (v,) in cur.fetchall():
                            v = str(v or "").strip()
                            if v and norm_txt(v) != "NO SELECCIONADA":
                                cats.add(v)
                    except Exception:
                        conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT descripcion FROM conciliacion_bancaria WHERE descripcion IS NOT NULL AND TRIM(descripcion) <> ''")
                    for (v,) in cur.fetchall():
                        c = categoria_de_conciliacion(v)
                        if c:
                            cats.add(c)
                except Exception:
                    conn.rollback()
                categorias = sorted(cats, key=lambda s: norm_txt(s))

                # --- Catálogo de PLACAS (flota + compras + órdenes + unidades) ---
                placas_set = {}

                def agregar_placa(placa, etiqueta_extra=""):
                    p = norm_placa(placa)
                    if not p:
                        return
                    etiqueta = str(placa or "").strip().upper()
                    if etiqueta_extra:
                        etiqueta = f"{etiqueta} | {etiqueta_extra}"
                    if p not in placas_set or (etiqueta_extra and etiqueta_extra == "Vehículo"):
                        placas_set[p] = etiqueta

                try:
                    cur.execute("SELECT placa, COALESCE(marca,''), COALESCE(modelo,''), COALESCE(anio,'') FROM flota_vehiculos")
                    for placa, marca, modelo, anio in cur.fetchall():
                        detalle = " ".join([x for x in [marca, modelo, anio] if x]).strip()
                        agregar_placa(placa, detalle or "Vehículo")
                except Exception:
                    conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT evento_asociado FROM facturas_recibidas WHERE evento_asociado IS NOT NULL AND TRIM(evento_asociado) <> ''")
                    for (v,) in cur.fetchall():
                        agregar_placa(v, "Compra")
                except Exception:
                    conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT placa FROM ordenes_servicio_flota WHERE placa IS NOT NULL AND TRIM(placa) <> ''")
                    for (v,) in cur.fetchall():
                        agregar_placa(v, "Servicio")
                except Exception:
                    conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT placa FROM inspecciones WHERE placa IS NOT NULL AND TRIM(placa) <> ''")
                    for (v,) in cur.fetchall():
                        agregar_placa(v, "Inspección")
                except Exception:
                    conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT COALESCE(unidad,'') FROM clientes_unidades WHERE unidad IS NOT NULL AND TRIM(unidad) <> ''")
                    for (v,) in cur.fetchall():
                        agregar_placa(str(v).split("—")[0], "Cliente")
                except Exception:
                    conn.rollback()
                try:
                    cur.execute("SELECT DISTINCT movil_asignado FROM choferes WHERE movil_asignado IS NOT NULL AND TRIM(movil_asignado) <> ''")
                    for (v,) in cur.fetchall():
                        agregar_placa(v, "Chofer")
                except Exception:
                    conn.rollback()

                placas = [placas_set[k] for k in sorted(placas_set.keys())]

                # --- Catálogo de CLIENTES ---
                clis = set()
                for sql in (
                    "SELECT DISTINCT nombre_empresa FROM clientes WHERE nombre_empresa IS NOT NULL AND TRIM(nombre_empresa) <> ''",
                    "SELECT DISTINCT cliente FROM facturas_emitidas WHERE cliente IS NOT NULL AND TRIM(cliente) <> ''",
                    "SELECT DISTINCT cliente_nombre FROM cobranza_quincenas WHERE cliente_nombre IS NOT NULL AND TRIM(cliente_nombre) <> ''",
                ):
                    try:
                        cur.execute(sql)
                        for (v,) in cur.fetchall():
                            clis.add(str(v).strip())
                    except Exception:
                        conn.rollback()
                clientes = sorted(clis, key=lambda s: norm_txt(s))

                # --- Catálogo de PROVEEDORES (registrados + los de compras) ---
                provs = set()
                for sql in (
                    "SELECT DISTINCT nombre FROM proveedores WHERE nombre IS NOT NULL AND TRIM(nombre) <> ''",
                    "SELECT DISTINCT proveedor FROM facturas_recibidas WHERE proveedor IS NOT NULL AND TRIM(proveedor) <> ''",
                ):
                    try:
                        cur.execute(sql)
                        for (v,) in cur.fetchall():
                            provs.add(str(v).strip())
                    except Exception:
                        conn.rollback()
                proveedores = sorted(provs, key=lambda s: norm_txt(s))
            except Exception:
                pass
            finally:
                liberar_conexion(conn)

        cache_sistema.guardar('estadisticas_lista_categorias', categorias)
        cache_sistema.guardar('estadisticas_lista_placas', placas)
        cache_sistema.guardar('lista_clientes_flota_combobox', clientes)
        cache_sistema.guardar('lista_proveedores_combobox', proveedores)

        estado["categorias"] = categorias
        estado["placas"] = placas
        estado["clientes"] = clientes
        estado["proveedores"] = proveedores

    def _pintar_listas(self, estado):
        # Hilo principal: si la consulta falló se dejan los combos como estaban
        if "categorias" not in estado:
            return
        self._aplicar_listas(estado["categorias"], estado["placas"],
                             estado["clientes"], estado["proveedores"])

    def _aplicar_listas(self, categorias, placas, clientes, proveedores):
        if self._listas_aplicadas:
            return
        self._listas_aplicadas = True

        self.lista_categorias = ["Todas las Categorías"] + list(categorias or [])
        self.combo_categoria.configure(values=self.lista_categorias)
        self.combo_categoria.set("Todas las Categorías")

        self.lista_placas = ["Todas las Placas / Vehículos"] + list(placas or [])
        self.combo_placa.configure(values=self.lista_placas)
        self.combo_placa.set("Todas las Placas / Vehículos")

        self.lista_clientes = ["Todos los Clientes"] + list(clientes or [])
        self.combo_cliente.configure(values=self.lista_clientes)
        self.combo_cliente.set("Todos los Clientes")

        self.lista_proveedores = ["Todos los Proveedores"] + list(proveedores or [])
        self.combo_proveedor.configure(values=self.lista_proveedores)
        self.combo_proveedor.set("Todos los Proveedores")

    # =====================================================
    # UTILIDADES DE FILTRADO
    # =====================================================
    def convertir_a_fecha(self, fecha_str):
        if not fecha_str: return None
        if isinstance(fecha_str, datetime):
            return fecha_str
        formatos = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y", "%Y%m%d"]
        for fmt in formatos:
            try:
                return datetime.strptime(str(fecha_str).strip(), fmt)
            except ValueError:
                pass
        return None

    def _normalizar_txt(self, valor):
        return norm_txt(valor)

    def _extraer_placa(self, seleccion):
        """'PLACA | MARCA' -> 'PLACA'; si no trae '|' devuelve el texto tal cual."""
        if not seleccion:
            return None
        if "|" in seleccion:
            return seleccion.split("|", 1)[0].strip()
        return str(seleccion).strip()

    def _mismo_banco(self, valor_db, filtro):
        """Compara la cuenta guardada con el filtro (por etiqueta, banco o dígitos)."""
        if not filtro or filtro == "Todas las Cuentas":
            return True
        t = norm_txt(valor_db)
        f = norm_txt(filtro)
        if not t:
            return False
        if t == f or f in t or t in f:
            return True
        nombre_banco = norm_txt(filtro.split(" - ")[0])
        if nombre_banco and nombre_banco in t:
            return True
        digitos = re.sub(r"\D", "", filtro)
        if len(digitos) >= 4 and digitos in re.sub(r"\D", "", str(valor_db or "")):
            return True
        return False

    def _coincide_categoria(self, texto, filtro):
        """Exacta, por grupo ('Gastos Fijos' -> 'Gastos Fijos - X') o parcial."""
        if not filtro or filtro == "Todas las Categorías":
            return True
        t = norm_txt(texto)
        f = norm_txt(filtro)
        if not t:
            return False
        if t == f:
            return True
        if t.startswith(f + " -") or f.startswith(t + " -"):
            return True
        if f in t or t in f:
            return True
        ultimo_t = norm_txt(t.split(" - ")[-1])
        ultimo_f = norm_txt(f.split(" - ")[-1])
        return bool(ultimo_t and ultimo_t == ultimo_f)

    def _coincide_texto(self, texto, filtro, comodin):
        if not filtro or filtro == comodin:
            return True
        return norm_txt(texto) == norm_txt(filtro)

    def _coincide_placa(self, *campos):
        """True si la placa filtrada aparece en alguno de los campos dados."""
        objetivo = self._placa_objetivo
        if not objetivo:
            return True
        for c in campos:
            valor = norm_placa(c)
            if valor and (valor == objetivo or objetivo in valor):
                return True
        return False

    def _clientes_de_placa(self, cur, conn, placa):
        """Nombres de clientes asociados a una placa (por flota o por unidad de cliente)."""
        nombres = set()
        pl = norm_placa(placa)
        if not pl:
            return nombres
        veh_ids = set()
        try:
            cur.execute("SELECT id, placa FROM flota_vehiculos")
            for vid, p in cur.fetchall():
                if norm_placa(p) == pl:
                    veh_ids.add(vid)
        except Exception:
            conn.rollback()
        cli_ids = set()
        try:
            cur.execute("SELECT id_cliente, id_vehiculo, COALESCE(unidad,'') FROM clientes_unidades")
            for idc, idv, unidad in cur.fetchall():
                unidad_pl = norm_placa(str(unidad or "").split("—")[0])
                if (idv in veh_ids) or (unidad_pl and unidad_pl == pl):
                    if idc:
                        cli_ids.add(idc)
        except Exception:
            conn.rollback()
        if cli_ids:
            try:
                marcadores = ",".join(["%s"] * len(cli_ids))
                cur.execute(f"SELECT nombre_empresa FROM clientes WHERE id IN ({marcadores})", tuple(cli_ids))
                for (nombre,) in cur.fetchall():
                    if nombre:
                        nombres.add(norm_txt(nombre))
            except Exception:
                conn.rollback()
        return nombres

    # =====================================================
    # CÁLCULO PRINCIPAL
    # =====================================================
    def cargar_kpis(self):
        filtros = {
            "banco": self.combo_banco.get(),
            "categoria": self.combo_categoria.get(),
            "placa": self.combo_placa.get(),
            "cliente": self.combo_cliente.get(),
            "proveedor": self.combo_proveedor.get(),
            "modo_fecha": self.tipo_fecha_var.get(),
            "mes": self.combo_mes.get(),
            "anio": self.combo_anio.get(),
            "dt_ini": None,
            "dt_fin": None,
        }

        if filtros["modo_fecha"] == "Rango":
            filtros["dt_ini"] = self.convertir_a_fecha(self.ent_fecha_ini.get())
            filtros["dt_fin"] = self.convertir_a_fecha(self.ent_fecha_fin.get())
            if not filtros["dt_ini"] or not filtros["dt_fin"]:
                messagebox.showwarning("Fechas Inválidas", "Por favor, ingrese un rango de fechas válido.")
                return
            if filtros["dt_ini"] > filtros["dt_fin"]:
                filtros["dt_ini"], filtros["dt_fin"] = filtros["dt_fin"], filtros["dt_ini"]

        filtros["periodo_txt"] = self._periodo_txt(filtros)

        self.lbl_estado.configure(text="⏳ Procesando la información del sistema...", text_color="#e67e22")
        self.card_ventas.configure(text="Calculando...")
        self.card_compras.configure(text="Calculando...")
        self.card_rentabilidad.configure(text="Calculando...", text_color="gray")

        def tarea_kpis(estado):
            # Hilo secundario: solo la consulta y el cálculo de los datos
            try:
                datos = self._recolectar_datos(filtros)
            except Exception as e:
                estado["error_calculo"] = e
                return
            if datos is None:
                estado["error_calculo"] = "No hay conexión con la base de datos."
                return
            estado["datos"] = datos

        def pintar_kpis(estado):
            # Hilo principal: mensajes de error y pintado de tarjetas/gráficos
            if estado.get("error_calculo") is not None:
                return self._error_calculo(estado["error_calculo"])
            self.datos = estado.get("datos")
            self._renderizar()

        ejecutar_en_hilo(self.parent_frame, tarea_kpis, al_terminar=pintar_kpis)

    def _error_calculo(self, error):
        self.lbl_estado.configure(text=f"❌ Error: {error}", text_color="#e74c3c")
        messagebox.showerror("Error de Cálculo", f"No se pudo calcular las estadísticas:\n{error}")

    def _periodo_txt(self, f):
        if f["modo_fecha"] == "Rango":
            return f"Desde {self.ent_fecha_ini.get()} hasta {self.ent_fecha_fin.get()}"
        if f["mes"] == "Todos los meses":
            return f"Año {f['anio']} (todos los meses)"
        return f"Mes: {f['mes']} - Año: {f['anio']}"

    def _cargar_placas_flota(self):
        """Diccionario {placa_normalizada: fila_completa} de la flota."""
        info = {}
        conn = conectar_db(silencioso=True)
        if not conn:
            return info
        try:
            cur = conn.cursor()
            cur.execute("""SELECT placa, COALESCE(marca,''), COALESCE(modelo,''), COALESCE(anio,'')
                           FROM flota_vehiculos""")
            for placa, marca, modelo, anio in cur.fetchall():
                info[norm_placa(placa)] = (placa, marca, modelo, anio)
        except Exception:
            try: conn.rollback()
            except Exception: pass
        finally:
            liberar_conexion(conn)
        return info

    def _recolectar_datos(self, f):
        conn = conectar_db(silencioso=True)
        if not conn:
            return None

        def q(sql, params=None):
            try:
                cur.execute(sql, params or ())
                return cur.fetchall()
            except Exception:
                try: conn.rollback()
                except Exception: pass
                return []

        try:
            cur = conn.cursor()

            # ---------------- FILTROS ACTIVOS ----------------
            if f["placa"] == "Todas las Placas / Vehículos":
                self._placa_objetivo = ""
            else:
                self._placa_objetivo = norm_placa(self._extraer_placa(f["placa"]))

            def fecha_en_periodo(f_str):
                dt_f = self.convertir_a_fecha(f_str)
                if not dt_f:
                    return False
                if f["modo_fecha"] == "Rango":
                    return f["dt_ini"] <= dt_f <= f["dt_fin"]
                if str(dt_f.year) == str(f["anio"]):
                    if f["mes"] == "Todos los meses":
                        return True
                    return f"{dt_f.month:02d}" == f["mes"]
                return False

            # ---------------- PAGOS A PROVEEDORES ----------------
            pagos_prov_todos = q("""SELECT id_factura, COALESCE(monto_pagado,0), COALESCE(fecha_pago,''),
                                           COALESCE(cuenta_origen,''), COALESCE(proveedor_nombre,''),
                                           COALESCE(categoria_suministro,''), COALESCE(archivo_ruta,''),
                                           COALESCE(codigo_cotizacion,'')
                                    FROM pagos_comprobantes""")

            pagos_por_factura = {}
            pagos_prov_filtrados = []
            for idf, monto, fecha_pago, cuenta, prov, cat, archivo, ref in pagos_prov_todos:
                if not self._mismo_banco(cuenta, f["banco"]):
                    continue
                if not self._coincide_categoria(cat, f["categoria"]):
                    continue
                if not self._coincide_texto(prov, f["proveedor"], "Todos los Proveedores"):
                    continue
                m = parse_num(monto)
                pagos_por_factura.setdefault(idf, []).append((m, fecha_pago, cuenta, cat, prov))
                pagos_prov_filtrados.append((idf, m, fecha_pago, cuenta, prov, cat, archivo, ref))

            # ---------------- PAGOS DE CLIENTES ----------------
            pagos_cli_todos = q("""SELECT id_factura, COALESCE(monto_pagado,0), COALESCE(fecha_pago,''),
                                          COALESCE(cuenta_destino,''), COALESCE(cliente_nombre,''),
                                          COALESCE(archivo_ruta,''), COALESCE(codigo_cotizacion,'')
                                   FROM pagos_clientes""")
            pagos_cli_por_factura = {}
            for idf, monto, fecha_pago, cuenta, cliente, archivo, ref in pagos_cli_todos:
                if not self._mismo_banco(cuenta, f["banco"]):
                    continue
                m = parse_num(monto)
                pagos_cli_por_factura.setdefault(idf, []).append((m, fecha_pago, cuenta, cliente, archivo, ref))

            # ---------------- DOCUMENTOS (para cruces) ----------------
            doc_compra = {i: nd for (i, nd) in q("SELECT id, COALESCE(numero_documento,'') FROM facturas_recibidas")}
            doc_venta = {i: nd for (i, nd) in q("SELECT id, COALESCE(numero_documento,'') FROM facturas_emitidas")}

            # ---------------- CLIENTES DE LA PLACA SELECCIONADA ----------------
            clientes_de_placa = set()
            if self._placa_objetivo:
                clientes_de_placa = self._clientes_de_placa(cur, conn, self._placa_objetivo)

            # ---------------- FACTURAS EMITIDAS (VENTAS) ----------------
            ventas_periodo = 0.0
            cobrado_periodo = 0.0
            por_cobrar_global = 0.0

            for (v_id, v_tipo, v_tot, v_det, v_f, v_cliente, v_estado, v_desc, v_evento, v_num, v_sub, v_imp) in q(
                """SELECT id, COALESCE(tipo_documento,''), COALESCE(total,0), COALESCE(det_monto,0),
                          COALESCE(fecha,''), COALESCE(cliente,''), COALESCE(estado_sunat,''),
                          COALESCE(descripcion,''), COALESCE(evento_asociado,''),
                          COALESCE(numero_documento,''), COALESCE(subtotal,0), COALESCE(impuesto,0)
                   FROM facturas_emitidas"""):
                if v_estado and "Anulada" in str(v_estado):
                    continue
                if self._placa_objetivo:
                    if not self._coincide_placa(v_cliente, v_desc, v_evento) and norm_txt(v_cliente) not in clientes_de_placa:
                        continue
                elif f["cliente"] != "Todos los Clientes":
                    if norm_txt(v_cliente) != norm_txt(f["cliente"]):
                        continue

                t = parse_num(v_tot); d = parse_num(v_det)
                neto_venta = t - d

                pagado_factura = 0.0
                for (m, fp, cuenta, cli, archivo, ref) in pagos_cli_por_factura.get(v_id, []):
                    pagado_factura += m
                    if fecha_en_periodo(fp):
                        cobrado_periodo += m

                if fecha_en_periodo(v_f):
                    ventas_periodo += neto_venta

                saldo = neto_venta - pagado_factura
                if saldo > 0.01:
                    por_cobrar_global += saldo

            # ---------------- FACTURAS RECIBIDAS (COMPRAS) ----------------
            compras_periodo = 0.0
            compras_brutas_periodo = 0.0
            igv_compras_periodo = 0.0
            pagado_periodo = 0.0
            por_pagar_global = 0.0
            combustible_importe = 0.0
            combustible_galones = 0.0

            compras_filtradas = []
            for fila in q("""SELECT id, COALESCE(tipo_documento,''), COALESCE(total,0), COALESCE(impuesto,0),
                                    COALESCE(det_monto,0), COALESCE(fecha,''), COALESCE(proveedor,''),
                                    COALESCE(evento_asociado,''), COALESCE(descripcion,''),
                                    COALESCE(numero_documento,''), COALESCE(categoria,''),
                                    COALESCE(kilometraje,''), COALESCE(cantidad_combustible,''),
                                    COALESCE(ruc,''), COALESCE(pagado_por_tercero,''),
                                    COALESCE(es_compra_cruzada, FALSE), COALESCE(subtotal,0)
                             FROM facturas_recibidas"""):
                (c_id, c_tipo, c_tot, c_imp, c_det, c_f, c_prov, c_evento, c_desc, c_num,
                 c_cat, c_km, c_comb, c_ruc, c_tercero, c_cruzada, c_sub) = fila

                if not self._coincide_texto(c_prov, f["proveedor"], "Todos los Proveedores"):
                    continue
                if not self._coincide_categoria(c_cat, f["categoria"]):
                    continue
                if self._placa_objetivo:
                    if not self._coincide_placa(c_evento, c_desc):
                        continue
                elif f["cliente"] != "Todos los Clientes":
                    if self._extraer_placa(f["cliente"]):
                        pass
                    if norm_txt(f["cliente"]) not in (norm_txt(c_evento) + " " + norm_txt(c_desc) + " " + norm_txt(c_prov)):
                        continue
                if f["banco"] != "Todas las Cuentas" and c_id not in pagos_por_factura:
                    continue

                tipo = str(c_tipo or "")
                t = parse_num(c_tot); i = parse_num(c_imp); d = parse_num(c_det)
                if "Recibo" in tipo and "8%" in tipo:
                    neto_compra = t - i - d
                else:
                    neto_compra = t - d

                pagado_factura = 0.0
                cuentas_pago = set()
                for (m, fp, cuenta, cat, prov) in pagos_por_factura.get(c_id, []):
                    pagado_factura += m
                    if cuenta:
                        cuentas_pago.add(str(cuenta))
                    if fecha_en_periodo(fp):
                        pagado_periodo += m

                if fecha_en_periodo(c_f):
                    compras_periodo += neto_compra
                    compras_brutas_periodo += t
                    igv_compras_periodo += i
                    combustible_importe += t
                    combustible_galones += parse_num(c_comb)

                saldo = neto_compra - pagado_factura
                if saldo > 0.01:
                    por_pagar_global += saldo

                compras_filtradas.append({
                    "id": c_id, "fecha": c_f, "numero": c_num, "tipo": tipo, "proveedor": c_prov,
                    "ruc": c_ruc, "categoria": c_cat or "(Sin categoría)", "placa": c_evento,
                    "descripcion": c_desc, "km": parse_num(c_km), "km_txt": c_km,
                    "galones": parse_num(c_comb), "galones_txt": c_comb,
                    "subtotal": parse_num(c_sub), "igv": i, "detraccion": d, "total": t,
                    "neto": neto_compra, "pagado": pagado_factura, "saldo": saldo,
                    "cuentas": ", ".join(sorted(cuentas_pago)),
                })

            n_compras = len(compras_filtradas)
            n_pagos = len(pagos_prov_filtrados)

            # ---------------- COBRANZA (QUINCENAS) ----------------
            cobranza_filas = []
            cobranza_total = 0.0
            cobranza_facturada = 0.0
            cobranza_pendiente = 0.0
            for (cid, cli, ruc, anio_c, mes_c, quinc, plan, total_c, facturado, ref, freg, notas, idcli) in q(
                """SELECT id, COALESCE(cliente_nombre,''), COALESCE(cliente_ruc,''),
                          COALESCE(anio,0), COALESCE(mes,0), COALESCE(quincena,0),
                          COALESCE(plan_cobro,''), COALESCE(total,0), COALESCE(facturado,FALSE),
                          COALESCE(factura_referencia,''), COALESCE(fecha_registro,''),
                          COALESCE(notas,''), COALESCE(id_cliente,0)
                   FROM cobranza_quincenas"""):
                if f["cliente"] != "Todos los Clientes" and norm_txt(cli) != norm_txt(f["cliente"]):
                    continue
                try:
                    fecha_ref = datetime(int(anio_c or 0), int(mes_c or 1), 16 if int(quinc or 1) == 2 else 1)
                except Exception:
                    fecha_ref = None
                if fecha_ref is None or not fecha_en_periodo(fecha_ref):
                    continue
                t = parse_num(total_c)
                cobranza_total += t
                if facturado:
                    cobranza_facturada += t
                else:
                    cobranza_pendiente += t
                cobranza_filas.append([cli, ruc, anio_c, mes_c, quinc, plan, t,
                                       "Sí" if facturado else "No", ref, freg, notas, cid])

            # ---------------- CONCILIACIÓN BANCARIA ----------------
            conc_filas = []
            for (banco, cuenta, fecha, desc, monto, tipo, origen, estado, fconc) in q(
                """SELECT COALESCE(banco,''), COALESCE(cuenta,''), COALESCE(fecha,''), COALESCE(descripcion,''),
                          COALESCE(monto,0), COALESCE(tipo,''), COALESCE(origen,''), COALESCE(estado,''),
                          COALESCE(fecha_conciliacion,'')
                   FROM conciliacion_bancaria"""):
                if not self._mismo_banco(f"{banco} - {cuenta}".strip(" -"), f["banco"]):
                    continue
                cat_conc = categoria_de_conciliacion(desc)
                if not self._coincide_categoria(cat_conc, f["categoria"]):
                    continue
                if not fecha_en_periodo(fecha):
                    continue
                conc_filas.append([banco, cuenta, fecha, desc, cat_conc, parse_num(monto), tipo, origen, estado, fconc])

            # ---------------- TRANSFERENCIAS ----------------
            transf_filas = []
            for (bo, co, bd, cd, fecha, desc, monto) in q(
                """SELECT COALESCE(banco_origen,''), COALESCE(cuenta_origen,''), COALESCE(banco_destino,''),
                          COALESCE(cuenta_destino,''), COALESCE(fecha,''), COALESCE(descripcion,''),
                          COALESCE(monto,0)
                   FROM transferencias_bancarias"""):
                et_origen = f"{bo} - {co}".strip(" -")
                et_destino = f"{bd} - {cd}".strip(" -")
                if f["banco"] != "Todas las Cuentas" and not (
                        self._mismo_banco(et_origen, f["banco"]) or self._mismo_banco(et_destino, f["banco"])):
                    continue
                if not fecha_en_periodo(fecha):
                    continue
                transf_filas.append([fecha, bo, co, bd, cd, parse_num(monto), desc])

            # ---------------- CUENTAS / BANCOS ----------------
            fuentes_cuentas = {}
            for b in (self.bancos_config or []):
                etiqueta = f"{str(b.get('banco','')).strip()} - {str(b.get('cuenta','')).strip()}".strip(" -")
                if etiqueta:
                    fuentes_cuentas[etiqueta] = {"saldo_inicial": parse_num(b.get("saldo_inicial", 0)),
                                                 "ingresos": 0.0, "egresos": 0.0, "n": 0}

            def etiqueta_de(texto):
                for etiqueta in fuentes_cuentas:
                    if self._mismo_banco(texto, etiqueta):
                        return etiqueta
                if texto:
                    fuentes_cuentas.setdefault(texto, {"saldo_inicial": 0.0, "ingresos": 0.0, "egresos": 0.0, "n": 0})
                    return texto
                return None

            for (idf, monto, fecha_pago, cuenta, cliente, archivo, ref) in pagos_cli_todos:
                et = etiqueta_de(cuenta)
                if et:
                    fuentes_cuentas[et]["ingresos"] += parse_num(monto)
                    fuentes_cuentas[et]["n"] += 1

            for (idf, monto, fecha_pago, cuenta, prov, cat, archivo, ref) in pagos_prov_todos:
                et = etiqueta_de(cuenta)
                if et:
                    fuentes_cuentas[et]["egresos"] += parse_num(monto)
                    fuentes_cuentas[et]["n"] += 1

            cuentas_filas = []
            saldo_total_bancos = 0.0
            for etiqueta, data in sorted(fuentes_cuentas.items(), key=lambda kv: norm_txt(kv[0])):
                if f["banco"] != "Todas las Cuentas" and not self._mismo_banco(etiqueta, f["banco"]):
                    continue
                saldo = data["saldo_inicial"] + data["ingresos"] - data["egresos"]
                saldo_total_bancos += saldo
                cuentas_filas.append([etiqueta, data["saldo_inicial"], data["ingresos"],
                                      data["egresos"], saldo, data["n"]])

            # ---------------- ÓRDENES DE SERVICIO ----------------
            os_por_placa = {}
            os_filas = []
            for (num, placa, vinfo, prov, servicio, desc, costo, femision, estado) in q(
                """SELECT COALESCE(numero_orden,''), COALESCE(placa,''), COALESCE(vehiculo_info,''),
                          COALESCE(proveedor,''), COALESCE(servicio,''), COALESCE(descripcion,''),
                          COALESCE(costo_total,0), COALESCE(fecha_emision,''), COALESCE(estado,'')
                   FROM ordenes_servicio_flota"""):
                if self._placa_objetivo and norm_placa(placa) != self._placa_objetivo:
                    continue
                if not self._coincide_categoria(servicio, f["categoria"]):
                    continue
                if not self._coincide_texto(prov, f["proveedor"], "Todos los Proveedores"):
                    continue
                if not fecha_en_periodo(str(femision)[:10]):
                    continue
                registro = os_por_placa.setdefault(norm_placa(placa), {"n": 0, "costo": 0.0})
                registro["n"] += 1
                if "anulada" not in norm_txt(estado).lower():
                    registro["costo"] += parse_num(costo)
                os_filas.append([num, placa, vinfo, prov, servicio, desc, parse_num(costo), femision, estado])

            # ---------------- AGRUPACIÓN POR PLACA ----------------
            info_flota = self._cargar_placas_flota()
            placas_agg = {}

            def registro_placa(placa_txt):
                clave = norm_placa(placa_txt) or "(SIN PLACA)"
                if clave not in placas_agg:
                    info = info_flota.get(clave)
                    vehiculo = ""
                    if info:
                        vehiculo = " ".join([str(x) for x in [info[1], info[2], info[3]] if x]).strip()
                    placas_agg[clave] = {
                        "placa": (str(placa_txt).strip().upper() if clave != "(SIN PLACA)" else "(Sin placa)"),
                        "vehiculo": vehiculo, "n": 0, "galones": 0.0, "km_max": 0.0,
                        "bruto": 0.0, "igv": 0.0, "neto": 0.0, "pagado": 0.0, "saldo": 0.0,
                        "os_n": 0, "os_costo": 0.0,
                    }
                return placas_agg[clave]

            for c in compras_filtradas:
                r = registro_placa(c["placa"])
                r["n"] += 1
                r["galones"] += c["galones"]
                r["km_max"] = max(r["km_max"], c["km"])
                r["bruto"] += c["total"]
                r["igv"] += c["igv"]
                r["neto"] += c["neto"]
                r["pagado"] += c["pagado"]
                if c["saldo"] > 0:
                    r["saldo"] += c["saldo"]

            for clave, datos_os in os_por_placa.items():
                r = placas_agg.get(clave)
                if r is None:
                    if self._placa_objetivo and clave != self._placa_objetivo:
                        continue
                    r = registro_placa(clave)
                r["os_n"] = datos_os["n"]
                r["os_costo"] = datos_os["costo"]

            placas_filas = []
            for clave, r in sorted(placas_agg.items(), key=lambda kv: -kv[1]["neto"]):
                if self._placa_objetivo and clave != self._placa_objetivo:
                    continue
                placas_filas.append([r["placa"], r["vehiculo"], r["n"], r["galones"], r["km_max"],
                                     r["bruto"], r["igv"], r["neto"], r["pagado"], r["saldo"],
                                     r["os_n"], r["os_costo"]])

            # ---------------- AGRUPACIÓN POR CATEGORÍA ----------------
            cats_agg = {}
            for c in compras_filtradas:
                clave = c["categoria"] or "(Sin categoría)"
                r = cats_agg.setdefault(clave, {"n": 0, "bruto": 0.0, "igv": 0.0, "neto": 0.0,
                                                "pagado": 0.0, "saldo": 0.0})
                r["n"] += 1
                r["bruto"] += c["total"]
                r["igv"] += c["igv"]
                r["neto"] += c["neto"]
                r["pagado"] += c["pagado"]
                if c["saldo"] > 0:
                    r["saldo"] += c["saldo"]

            total_neto_categorias = sum(r["neto"] for r in cats_agg.values()) or 1.0
            categorias_filas = []
            for clave, r in sorted(cats_agg.items(), key=lambda kv: -kv[1]["neto"]):
                categorias_filas.append([clave, r["n"], r["bruto"], r["igv"], r["neto"],
                                         r["pagado"], r["saldo"],
                                         (r["neto"] / total_neto_categorias) * 100.0])

            # ---------------- AGRUPACIÓN POR PROVEEDOR ----------------
            prov_agg = {}
            for c in compras_filtradas:
                clave = c["proveedor"] or "(Sin proveedor)"
                r = prov_agg.setdefault(clave, {"n": 0, "bruto": 0.0, "igv": 0.0, "neto": 0.0,
                                                "pagado": 0.0, "saldo": 0.0, "cats": set(), "ruc": ""})
                r["n"] += 1
                r["bruto"] += c["total"]
                r["igv"] += c["igv"]
                r["neto"] += c["neto"]
                r["pagado"] += c["pagado"]
                if c["saldo"] > 0:
                    r["saldo"] += c["saldo"]
                r["ruc"] = r["ruc"] or c["ruc"]
                if c["categoria"]:
                    r["cats"].add(c["categoria"])

            proveedores_filas = []
            for clave, r in sorted(prov_agg.items(), key=lambda kv: -kv[1]["neto"]):
                proveedores_filas.append([clave, r["ruc"], ", ".join(sorted(r["cats"])) or "-",
                                          r["n"], r["bruto"], r["igv"], r["neto"], r["pagado"], r["saldo"]])

            # ---------------- COMBUSTIBLE (DETALLE) ----------------
            combustible_filas = []
            for c in sorted(compras_filtradas,
                            key=lambda x: self.convertir_a_fecha(x["fecha"]) or datetime(1900, 1, 1)):
                if not c["galones_txt"] and not c["km_txt"]:
                    continue
                combustible_filas.append([c["fecha"], c["placa"], c["numero"], c["proveedor"],
                                          c["categoria"], c["galones"], c["galones_txt"],
                                          c["km"], c["total"], c["descripcion"]])

            # ---------------- BITÁCORA / ACTIVIDAD ----------------
            bitacora_filas = []
            for (fecha, hora, usuario, modulo, accion) in q(
                """SELECT COALESCE(fecha,''), COALESCE(hora,''), COALESCE(usuario,''),
                          COALESCE(modulo,''), COALESCE(accion,'')
                   FROM bitacora_auditoria ORDER BY id DESC LIMIT 8000"""):
                if not fecha_en_periodo(fecha):
                    continue
                bitacora_filas.append([fecha, hora, usuario, modulo, accion])

            actividad_modulo = {}
            actividad_usuario = {}
            for (fecha, hora, usuario, modulo, accion) in bitacora_filas:
                actividad_modulo[modulo] = actividad_modulo.get(modulo, 0) + 1
                actividad_usuario[usuario] = actividad_usuario.get(usuario, 0) + 1

            actividad_filas = [[m, n] for m, n in sorted(actividad_modulo.items(), key=lambda kv: -kv[1])]
            usuarios_filas = [[u, n] for u, n in sorted(actividad_usuario.items(), key=lambda kv: -kv[1])]

            # ---------------- COBRANZA: UNIDADES Y VIAJES ----------------
            ids_cobranza = {fila[11]: fila[0] for fila in cobranza_filas}
            cobranza_unidades_filas = []
            for (idc, unidad, pn, pd, pf, hd, cn, cd, cf, dn, dd, df, mn, md, mf, mded, sub) in q(
                """SELECT COALESCE(id_cobranza,0), COALESCE(unidad,''), COALESCE(precio_normal,0),
                          COALESCE(precio_domingo,0), COALESCE(precio_feriado,0), COALESCE(horas_dia,0),
                          COALESCE(cant_normal,0), COALESCE(cant_domingo,0), COALESCE(cant_feriado,0),
                          COALESCE(ded_normal_h,0), COALESCE(ded_domingo_h,0), COALESCE(ded_feriado_h,0),
                          COALESCE(monto_normal,0), COALESCE(monto_domingo,0), COALESCE(monto_feriado,0),
                          COALESCE(monto_deducciones,0), COALESCE(subtotal,0)
                   FROM cobranza_quincena_unidades"""):
                if ids_cobranza and idc not in ids_cobranza:
                    continue
                if self._placa_objetivo and norm_placa(str(unidad).split("—")[0]) != self._placa_objetivo:
                    continue
                cobranza_unidades_filas.append([ids_cobranza.get(idc, idc), unidad, parse_num(pn), parse_num(pd),
                                                parse_num(pf), parse_num(hd), cn, cd, cf,
                                                parse_num(mn), parse_num(md), parse_num(mf),
                                                parse_num(mded), parse_num(sub)])

            cobranza_viajes_filas = []
            for (idc, ddesde, dhasta, pn, pd, pf, vn, vd, vf, dn, dd, df, mbase, mded, sub) in q(
                """SELECT COALESCE(id_cobranza,0), COALESCE(distancia_desde,0), COALESCE(distancia_hasta,0),
                          COALESCE(precio_normal,0), COALESCE(precio_domingo,0), COALESCE(precio_feriado,0),
                          COALESCE(viajes_normal,0), COALESCE(viajes_domingo,0), COALESCE(viajes_feriado,0),
                          COALESCE(ded_normal,0), COALESCE(ded_domingo,0), COALESCE(ded_feriado,0),
                          COALESCE(monto_base,0), COALESCE(monto_deducciones,0), COALESCE(subtotal,0)
                   FROM cobranza_quincena_viajes"""):
                if ids_cobranza and idc not in ids_cobranza:
                    continue
                cobranza_viajes_filas.append([ids_cobranza.get(idc, idc), parse_num(ddesde), parse_num(dhasta),
                                              parse_num(pn), parse_num(pd), parse_num(pf), vn, vd, vf,
                                              parse_num(mbase), parse_num(mded), parse_num(sub)])

            # ---------------- IMPUESTOS ----------------
            impuestos_filas = []
            for (periodo, vn, igvv, cn, igvc, cfa, igvp, cfs, ir, total_sunat, sna, ddm, nu, sns, pb, dc, arch) in q(
                """SELECT COALESCE(periodo,''), COALESCE(ventas_netas,0), COALESCE(igv_ventas,0),
                          COALESCE(compras_netas,0), COALESCE(igv_compras,0), COALESCE(credito_fiscal_anterior,0),
                          COALESCE(igv_a_pagar,0), COALESCE(credito_fiscal_siguiente,0), COALESCE(impuesto_renta,0),
                          COALESCE(total_sunat,0), COALESCE(saldo_nacion_anterior,0), COALESCE(detracciones_del_mes,0),
                          COALESCE(nacion_utilizado,0), COALESCE(saldo_nacion_siguiente,0), COALESCE(pago_bolsillo,0),
                          COALESCE(detracciones_compras,0), COALESCE(archivo_pago,'')
                   FROM registro_impuestos ORDER BY periodo"""):
                impuestos_filas.append([periodo, parse_num(vn), parse_num(igvv), parse_num(cn), parse_num(igvc),
                                        parse_num(cfa), parse_num(igvp), parse_num(cfs), parse_num(ir),
                                        parse_num(total_sunat), parse_num(sna), parse_num(ddm), parse_num(nu),
                                        parse_num(sns), parse_num(pb), parse_num(dc), arch])

            # ---------------- CATÁLOGOS ----------------
            clientes_filas = [[str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
                               str(r[4] or ""), parse_num(r[5]), str(r[6] or "")]
                              for r in q("""SELECT COALESCE(ruc,''), COALESCE(nombre_empresa,''), COALESCE(telefono,''),
                                                   COALESCE(correo,''), COALESCE(plan_cobro,''), COALESCE(limite_credito,0),
                                                   COALESCE(persona_contacto,'')
                                            FROM clientes ORDER BY nombre_empresa""")]

            proveedores_cat_filas = [[str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
                                      str(r[4] or ""), str(r[5] or "")]
                                     for r in q("""SELECT COALESCE(ruc,''), COALESCE(nombre,''), COALESCE(categoria,''),
                                                          COALESCE(contacto,''), COALESCE(whatsapp,''), COALESCE(correo,'')
                                                   FROM proveedores ORDER BY nombre""")]

            choferes_filas = [[str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or ""),
                               str(r[4] or ""), str(r[5] or "")]
                              for r in q("""SELECT COALESCE(dni,''), COALESCE(nombres,''), COALESCE(licencia,''),
                                                   COALESCE(categoria_licencia,''), COALESCE(estado,''),
                                                   COALESCE(movil_asignado,'')
                                            FROM choferes ORDER BY nombres""")]

            inspecciones_filas = [[str(r[0] or ""), str(r[1] or ""), str(r[2] or ""), str(r[3] or "")]
                                  for r in q("""SELECT COALESCE(placa,''), COALESCE(chofer,''), COALESCE(inspector,''),
                                                       COALESCE(fecha_hora,'')
                                                FROM inspecciones ORDER BY creado_en DESC LIMIT 2000""")]

            # ---------------- KPIs DERIVADOS ----------------
            rentabilidad = ventas_periodo - compras_periodo
            caja = cobrado_periodo - pagado_periodo
            provision_renta = rentabilidad * (self.tasa_renta_anual / 100.0) if rentabilidad > 0 else 0.0
            provision_renta_mensual = rentabilidad * (self.tasa_renta_mensual / 100.0) if rentabilidad > 0 else 0.0

            # ================= SECCIONES =================
            S = {}

            resumen_filas = [
                ["— FILTROS APLICADOS —", "", 0],
                ["Cuenta / Banco", f["banco"], 0],
                ["Categoría", f["categoria"], 0],
                ["Placa / Vehículo", f["placa"], 0],
                ["Cliente", f["cliente"], 0],
                ["Proveedor", f["proveedor"], 0],
                ["Periodo analizado", f.get("periodo_txt") or "—", 0],
                ["Fecha de cálculo", datetime.now().strftime("%d/%m/%Y %H:%M:%S"), 0],
                ["— RESULTADOS DEL PERIODO —", "", 0],
                ["Ventas del Periodo (Facturado Neto)", formatear_moneda(ventas_periodo), ventas_periodo],
                ["Cobrado en el Periodo (Dinero Real)", formatear_moneda(cobrado_periodo), cobrado_periodo],
                ["Deuda Total por Cobrar (al día de hoy)", formatear_moneda(por_cobrar_global), por_cobrar_global],
                ["Compras del Periodo (Neto)", formatear_moneda(compras_periodo), compras_periodo],
                ["Compras Brutas del Periodo", formatear_moneda(compras_brutas_periodo), compras_brutas_periodo],
                ["IGV de las Compras del Periodo", formatear_moneda(igv_compras_periodo), igv_compras_periodo],
                ["Pagado en el Periodo (Dinero Real)", formatear_moneda(pagado_periodo), pagado_periodo],
                ["Deuda Total a Proveedores (al día de hoy)", formatear_moneda(por_pagar_global), por_pagar_global],
                ["RENTABILIDAD DEL NEGOCIO", formatear_moneda(rentabilidad), rentabilidad],
                ["FLUJO DE CAJA DEL PERIODO", formatear_moneda(caja), caja],
                [f"Provisión Impuesto a la Renta Anual ({self.tasa_renta_anual}%)", formatear_moneda(provision_renta), provision_renta],
                [f"Provisión ISR Mensual ({self.tasa_renta_mensual}%)", formatear_moneda(provision_renta_mensual), provision_renta_mensual],
                ["— BANCOS —", "", 0],
                ["Saldo Total Disponible en Bancos", formatear_moneda(saldo_total_bancos), saldo_total_bancos],
                ["Cuentas / Bancos analizados", formatear_numero(len(cuentas_filas), 0), len(cuentas_filas)],
                ["— COBRANZA (QUINCENAS DEL PERIODO) —", "", 0],
                ["Cobranza Registrada (Total)", formatear_moneda(cobranza_total), cobranza_total],
                ["Cobranza Facturada", formatear_moneda(cobranza_facturada), cobranza_facturada],
                ["Cobranza Pendiente de Facturar", formatear_moneda(cobranza_pendiente), cobranza_pendiente],
                ["— COMBUSTIBLE (COMPRAS DEL PERIODO) —", "", 0],
                ["Importe de Combustible", formatear_moneda(combustible_importe), combustible_importe],
                ["Galones / m³ de Combustible", formatear_numero(combustible_galones, 3), combustible_galones],
                ["— VOLÚMENES DEL SISTEMA —", "", 0],
                ["Nº Comprobantes de Compra (filtrados)", formatear_numero(n_compras, 0), n_compras],
                ["Nº Pagos a Proveedores (filtrados)", formatear_numero(n_pagos, 0), n_pagos],
                ["Nº Placas / Vehículos con movimiento", formatear_numero(len(placas_filas), 0), len(placas_filas)],
                ["Nº Categorías con movimiento", formatear_numero(len(categorias_filas), 0), len(categorias_filas)],
                ["Nº Proveedores con movimiento", formatear_numero(len(proveedores_filas), 0), len(proveedores_filas)],
                ["Nº Órdenes de Servicio", formatear_numero(len(os_filas), 0), len(os_filas)],
                ["Nº Movimientos en Conciliación", formatear_numero(len(conc_filas), 0), len(conc_filas)],
                ["Nº Transferencias", formatear_numero(len(transf_filas), 0), len(transf_filas)],
                ["Nº Registros en Bitácora (periodo)", formatear_numero(len(bitacora_filas), 0), len(bitacora_filas)],
            ]
            S["resumen"] = {"cols": [("Indicador", TXT), ("Valor", TXT), ("Valor Numérico", NUM)],
                            "filas": resumen_filas, "ocultar": ["Valor Numérico"]}

            S["categorias"] = {"cols": [("Categoría", TXT), ("Comprobantes", ENT), ("Bruto", MON), ("IGV", MON),
                                        ("Neto", MON), ("Pagado", MON), ("Saldo", MON), ("% del Total", NUM)],
                               "filas": categorias_filas}

            S["placas"] = {"cols": [("Placa", TXT), ("Vehículo", TXT), ("Comprobantes", ENT), ("Combustible (gal/m³)", NUM),
                                    ("Odómetro Máx.", NUM), ("Bruto", MON), ("IGV", MON), ("Neto Compras", MON),
                                    ("Pagado", MON), ("Saldo", MON), ("Órdenes Servicio", ENT), ("Costo Servicios", MON)],
                           "filas": placas_filas}

            S["proveedores_res"] = {"cols": [("Proveedor", TXT), ("RUC", TXT), ("Categorías", TXT), ("Comprobantes", ENT),
                                             ("Bruto", MON), ("IGV", MON), ("Neto", MON), ("Pagado", MON), ("Saldo", MON)],
                                    "filas": proveedores_filas}

            S["cuentas"] = {"cols": [("Cuenta / Banco", TXT), ("Saldo Inicial", MON), ("Ingresos", MON),
                                     ("Egresos", MON), ("Saldo Actual", MON), ("Nº Movimientos", ENT)],
                            "filas": cuentas_filas}

            compras_filas_salida = [[c["fecha"], c["numero"], c["tipo"], c["proveedor"], c["ruc"], c["categoria"],
                                     c["placa"], c["descripcion"], c["galones"], c["km"], c["subtotal"], c["igv"],
                                     c["detraccion"], c["total"], c["neto"], c["pagado"], c["saldo"], c["cuentas"]]
                                    for c in sorted(compras_filtradas,
                                                    key=lambda x: self.convertir_a_fecha(x["fecha"]) or datetime(1900, 1, 1))]
            S["compras"] = {"cols": [("Fecha", TXT), ("Nº Documento", TXT), ("Tipo", TXT), ("Proveedor", TXT),
                                     ("RUC", TXT), ("Categoría", TXT), ("Placa", TXT), ("Descripción", TXT),
                                     ("Galones / m³", NUM), ("Odómetro", NUM), ("Subtotal", MON), ("IGV", MON),
                                     ("Detracción", MON), ("Total", MON), ("Neto", MON), ("Pagado", MON),
                                     ("Saldo", MON), ("Cuenta(s) de Pago", TXT)],
                            "filas": compras_filas_salida}

            # Con filtro de Placa/Cliente solo se listan los pagos de las facturas visibles
            filtra_por_unidad = bool(self._placa_objetivo) or f["cliente"] != "Todos los Clientes"
            ids_visibles = {c["id"] for c in compras_filtradas} if filtra_por_unidad else None
            pagos_filas_salida = [[fp, prov, doc_compra.get(idf, ""), cat, m, cuenta, archivo]
                                  for (idf, m, fp, cuenta, prov, cat, archivo, ref) in
                                  sorted(pagos_prov_filtrados,
                                         key=lambda x: self.convertir_a_fecha(x[2]) or datetime(1900, 1, 1))
                                  if ids_visibles is None or idf in ids_visibles]
            S["pagos"] = {"cols": [("Fecha de Pago", TXT), ("Proveedor", TXT), ("Factura", TXT), ("Categoría", TXT),
                                   ("Monto Pagado", MON), ("Cuenta Origen", TXT), ("Archivo", TXT)],
                          "filas": pagos_filas_salida}

            S["combustible"] = {"cols": [("Fecha", TXT), ("Placa", TXT), ("Nº Documento", TXT), ("Proveedor", TXT),
                                         ("Categoría", TXT), ("Galones / m³", NUM), ("Cantidad (texto)", TXT),
                                         ("Odómetro", NUM), ("Importe", MON), ("Descripción", TXT)],
                                "filas": combustible_filas}

            S["cobranza"] = {"cols": [("Cliente", TXT), ("RUC", TXT), ("Año", ENT), ("Mes", ENT), ("Quincena", ENT),
                                      ("Plan de Cobro", TXT), ("Total", MON), ("Facturado", TXT),
                                      ("Factura Referencia", TXT), ("Fecha Registro", TXT), ("Notas", TXT)],
                             "filas": [fila[:11] for fila in cobranza_filas]}

            S["cobranza_unidades"] = {"cols": [("Cobranza", ENT), ("Unidad / Vehículo", TXT), ("P. Normal", MON),
                                               ("P. Domingo", MON), ("P. Feriado", MON), ("Horas/Día", NUM),
                                               ("Cant. Normal", ENT), ("Cant. Domingo", ENT), ("Cant. Feriado", ENT),
                                               ("Monto Normal", MON), ("Monto Domingo", MON), ("Monto Feriado", MON),
                                               ("Deducciones", MON), ("Subtotal", MON)],
                                      "filas": cobranza_unidades_filas}

            S["cobranza_viajes"] = {"cols": [("Cobranza", ENT), ("Dist. Desde", NUM), ("Dist. Hasta", NUM),
                                             ("P. Normal", MON), ("P. Domingo", MON), ("P. Feriado", MON),
                                             ("Viajes Normal", ENT), ("Viajes Domingo", ENT), ("Viajes Feriado", ENT),
                                             ("Monto Base", MON), ("Deducciones", MON), ("Subtotal", MON)],
                                    "filas": cobranza_viajes_filas}

            flota_filas_salida = []
            for fila in q("""SELECT placa, COALESCE(marca,''), COALESCE(modelo,''), COALESCE(anio,''),
                                    COALESCE(categoria,''), COALESCE(estado,''), COALESCE(tipo_combustible,''),
                                    COALESCE(kilometraje,''), COALESCE(vencimiento_soat,''), COALESCE(vencimiento_rt,''),
                                    COALESCE(vencimiento_seguro,''), COALESCE(fecha_ultimo_aceite,''),
                                    COALESCE(km_ultimo_aceite,0), COALESCE(km_prox_correa,''),
                                    COALESCE(fec_venc_extintor,''), COALESCE(serial_motor,'')
                             FROM flota_vehiculos ORDER BY placa"""):
                if self._placa_objetivo and norm_placa(fila[0]) != self._placa_objetivo:
                    continue
                flota_filas_salida.append([str(fila[0] or ""), str(fila[1] or ""), str(fila[2] or ""),
                                           str(fila[3] or ""), str(fila[4] or ""), str(fila[5] or ""),
                                           str(fila[6] or ""), parse_num(fila[7]), str(fila[8] or ""),
                                           str(fila[9] or ""), str(fila[10] or ""), str(fila[11] or ""),
                                           parse_num(fila[12]), parse_num(fila[13]), str(fila[14] or ""),
                                           str(fila[15] or "")])
            S["flota"] = {"cols": [("Placa", TXT), ("Marca", TXT), ("Modelo", TXT), ("Año", TXT), ("Categoría", TXT),
                                   ("Estado", TXT), ("Tipo Combustible", TXT), ("Kilometraje", NUM),
                                   ("Venc. SOAT", TXT), ("Venc. Rev. Técnica", TXT), ("Venc. Seguro", TXT),
                                   ("Últ. Cambio Aceite", TXT), ("Km Últ. Aceite", NUM), ("Km Próx. Correa", NUM),
                                   ("Venc. Extintor", TXT), ("Serial Motor", TXT)],
                          "filas": flota_filas_salida}

            S["ordenes_servicio"] = {"cols": [("Nº Orden", TXT), ("Placa", TXT), ("Vehículo", TXT), ("Proveedor", TXT),
                                              ("Servicio", TXT), ("Descripción", TXT), ("Costo Total", MON),
                                              ("Fecha Emisión", TXT), ("Estado", TXT)],
                                     "filas": os_filas}

            S["conciliacion"] = {"cols": [("Banco", TXT), ("Cuenta", TXT), ("Fecha", TXT), ("Descripción", TXT),
                                          ("Categoría", TXT), ("Monto", MON), ("Tipo", TXT), ("Origen", TXT),
                                          ("Estado", TXT), ("Fecha Conciliación", TXT)],
                                 "filas": conc_filas}

            S["transferencias"] = {"cols": [("Fecha", TXT), ("Banco Origen", TXT), ("Cuenta Origen", TXT),
                                            ("Banco Destino", TXT), ("Cuenta Destino", TXT), ("Monto", MON),
                                            ("Descripción", TXT)],
                                   "filas": transf_filas}

            S["impuestos"] = {"cols": [("Periodo", TXT), ("Ventas Netas", MON), ("IGV Ventas", MON),
                                       ("Compras Netas", MON), ("IGV Compras", MON), ("Crédito Fiscal Anterior", MON),
                                       ("IGV a Pagar", MON), ("Crédito Fiscal Siguiente", MON), ("Impuesto Renta", MON),
                                       ("Total SUNAT", MON), ("Saldo Nación Anterior", MON),
                                       ("Detracciones del Mes", MON), ("Nación Utilizado", MON),
                                       ("Saldo Nación Siguiente", MON), ("Pago de Bolsillo", MON),
                                       ("Detracciones Compras", MON), ("Archivo de Pago", TXT)],
                              "filas": impuestos_filas}

            S["bitacora"] = {"cols": [("Fecha", TXT), ("Hora", TXT), ("Usuario", TXT), ("Módulo", TXT), ("Acción", TXT)],
                             "filas": bitacora_filas[:2000]}

            S["actividad_modulo"] = {"cols": [("Módulo", TXT), ("Nº de Acciones", ENT)], "filas": actividad_filas}
            S["actividad_usuario"] = {"cols": [("Usuario", TXT), ("Nº de Acciones", ENT)], "filas": usuarios_filas}

            S["clientes"] = {"cols": [("RUC", TXT), ("Razón Social", TXT), ("Teléfono", TXT), ("Correo", TXT),
                                      ("Plan de Cobro", TXT), ("Límite de Crédito", MON), ("Contacto", TXT)],
                             "filas": clientes_filas}

            S["proveedores_cat"] = {"cols": [("RUC", TXT), ("Proveedor", TXT), ("Categoría", TXT), ("Contacto", TXT),
                                             ("WhatsApp", TXT), ("Correo", TXT)],
                                    "filas": proveedores_cat_filas}

            S["choferes"] = {"cols": [("DNI", TXT), ("Nombres", TXT), ("Licencia", TXT), ("Categoría Licencia", TXT),
                                      ("Estado", TXT), ("Móvil Asignado", TXT)],
                             "filas": choferes_filas}

            S["inspecciones"] = {"cols": [("Placa", TXT), ("Chofer", TXT), ("Inspector", TXT), ("Fecha y Hora", TXT)],
                                 "filas": inspecciones_filas}

            kpis = {
                "ventas": ventas_periodo, "cobrado": cobrado_periodo, "por_cobrar": por_cobrar_global,
                "compras": compras_periodo, "pagado": pagado_periodo, "por_pagar": por_pagar_global,
                "rentabilidad": rentabilidad, "caja": caja, "saldo_bancos": saldo_total_bancos,
                "combustible_importe": combustible_importe, "combustible_galones": combustible_galones,
                "provision_renta": provision_renta, "provision_renta_mensual": provision_renta_mensual,
            }

            return {"secciones": S, "kpis": kpis, "filtros": f,
                    "periodo_txt": f.get("periodo_txt") or "—"}

        finally:
            liberar_conexion(conn)

    # =====================================================
    # PINTADO EN PANTALLA
    # =====================================================
    def _formatear_celda(self, valor, tipo):
        if tipo == MON:
            return formatear_moneda(valor)
        if tipo == NUM:
            return formatear_numero(valor, 3)
        if tipo == ENT:
            try:
                return formatear_numero(int(round(parse_num(valor))), 0)
            except Exception:
                return str(valor)
        return "" if valor is None else str(valor)

    def _pintar_seccion(self, clave, seccion):
        tree = self.trees.get(clave)
        if tree is None or seccion is None:
            return
        cols = seccion["cols"]
        tipos = dict(cols)
        ocultar = set(seccion.get("ocultar") or [])
        visibles = [c for c, _t in cols if c not in ocultar]
        indices = [i for i, (c, _t) in enumerate(cols) if c not in ocultar]

        tree.delete(*tree.get_children())
        tree.configure(columns=visibles, displaycolumns=visibles)
        for nombre in visibles:
            tipo = tipos.get(nombre, TXT)
            tree.heading(nombre, text=nombre, anchor="w")
            ancho = 130 if tipo in (MON, NUM, ENT) else (200 if len(nombre) > 18 else 115)
            tree.column(nombre, width=ancho, minwidth=80,
                        anchor="e" if tipo in (MON, NUM, ENT) else "w", stretch=False)

        for fila in seccion["filas"]:
            valores = []
            for i in indices:
                valor = fila[i] if i < len(fila) else ""
                valores.append(self._formatear_celda(valor, cols[i][1]))
            tree.insert("", "end", values=valores)

    def _renderizar(self):
        datos = self.datos
        if not datos:
            return
        k = datos["kpis"]

        self.card_ventas.configure(text=formatear_moneda(k["ventas"]))
        self.card_cobrado.configure(text=formatear_moneda(k["cobrado"]))
        self.card_por_cobrar.configure(text=formatear_moneda(k["por_cobrar"]))
        self.card_compras.configure(text=formatear_moneda(k["compras"]))
        self.card_pagado.configure(text=formatear_moneda(k["pagado"]))
        self.card_por_pagar.configure(text=formatear_moneda(k["por_pagar"]))

        self.card_rentabilidad.configure(text=formatear_moneda(k["rentabilidad"]),
                                         text_color="#e74c3c" if k["rentabilidad"] < 0 else "#1f538d")
        self.card_caja.configure(text=formatear_moneda(k["caja"]),
                                 text_color="#e74c3c" if k["caja"] < 0 else "#27ae60")
        self.card_saldo_banco.configure(text=formatear_moneda(k["saldo_bancos"]),
                                        text_color="#e74c3c" if k["saldo_bancos"] < 0 else "#16a085")
        self.card_combustible.configure(
            text=f"{formatear_moneda(k['combustible_importe'])}  |  {formatear_numero(k['combustible_galones'], 3)} gal / m³")

        self.card_provision_renta.configure(text=formatear_moneda(k["provision_renta"]))
        self.card_provision_renta_mensual.configure(text=formatear_moneda(k["provision_renta_mensual"]))

        for clave, seccion in datos["secciones"].items():
            if clave in self.trees:
                self._pintar_seccion(clave, seccion)

        total_filas = sum(len(s["filas"]) for s in datos["secciones"].values())
        n_hojas = len([c for c in ORDEN_EXPORT if datos["secciones"].get(c)]) + 2
        self.lbl_estado.configure(
            text=f"✅ Cálculo completado ({datos['periodo_txt']}). Registros analizados: {total_filas}. "
                 f"Al exportar se generan {n_hojas} hojas de Excel.",
            text_color="#27ae60")

    # =====================================================
    # EXPORTACIÓN A EXCEL (TODAS LAS HOJAS)
    # =====================================================
    def exportar_excel(self):
        try:
            import pandas as pd
        except ImportError:
            messagebox.showerror("Error", "Falta la librería pandas. Ejecuta: pip install pandas openpyxl")
            return

        if not self.datos:
            messagebox.showwarning("Sin datos", "Primero pulsa «Procesar y Calcular Estadísticas».")
            return

        ruta = guardar_archivo_dialogo(
            titulo="Exportar TODA la información del sistema a Excel",
            defaultextension=".xlsx",
            initialfile=f"Estadisticas_Sistema_Completo_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            tipos=[("Archivos Excel", "*.xlsx")]
        )
        if not ruta:
            return

        secciones = self.datos["secciones"]
        f = self.datos["filtros"]

        try:
            hojas = 0
            with pd.ExcelWriter(ruta, engine="openpyxl") as writer:
                portada = pd.DataFrame({
                    "Parámetro": ["Fecha de generación", "Usuario", "Cuenta / Banco", "Categoría",
                                  "Placa / Vehículo", "Cliente", "Proveedor", "Periodo analizado"],
                    "Valor": [datetime.now().strftime("%d/%m/%Y %H:%M:%S"), self.usuario_activo,
                              f["banco"], f["categoria"], f["placa"], f["cliente"], f["proveedor"],
                              self.datos["periodo_txt"]],
                })
                portada.to_excel(writer, sheet_name="Filtros Aplicados", index=False)
                hojas += 1

                for clave in ORDEN_EXPORT:
                    seccion = secciones.get(clave)
                    if not seccion:
                        continue
                    nombres = [c for c, _t in seccion["cols"]]
                    filas = seccion["filas"]
                    ancho = len(nombres)
                    if not filas:
                        df = pd.DataFrame(columns=nombres)
                    else:
                        normalizadas = [(list(fila) + [""] * ancho)[:ancho] for fila in filas]
                        df = pd.DataFrame(normalizadas, columns=nombres)
                    df.to_excel(writer, sheet_name=NOMBRES_HOJA.get(clave, clave)[:31], index=False)
                    hojas += 1

                usuarios = secciones.get("actividad_usuario")
                if usuarios and usuarios["filas"]:
                    pd.DataFrame(usuarios["filas"], columns=["Usuario", "Nº de Acciones"]).to_excel(
                        writer, sheet_name="Actividad por Usuario", index=False)
                    hojas += 1

            registrar_auditoria(self.usuario_activo, "Estadísticas Financieras",
                                "Exportó el reporte completo del sistema a Excel")
            self.lbl_estado.configure(text=f"✅ Reporte exportado con {hojas} hojas: {ruta}", text_color="#27ae60")
            if messagebox.askyesno("Éxito", f"Reporte exportado con {hojas} hojas en:\n{ruta}\n\n¿Deseas abrirlo ahora?"):
                abrir_documento(ruta)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo crear el archivo:\n{e}")


if __name__ == "__main__":
    pass
