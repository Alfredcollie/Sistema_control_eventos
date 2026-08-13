# -*- coding: utf-8 -*-
"""
CALCULO_IMPUESTOS.PY (OPTIMIZADO + CONFORME SUNAT)
- Bitácora en guardar/eliminar/exportar.
- Config: lectura REAL de config_local.json (app_paths) + claves limpias.
- ALTER TABLE solo la primera vez por sesión (_SCHEMA_IMP_OK).
- Historial y cálculo del mes en segundo plano (hilos + token).
- RENTA MENSUAL SEGÚN RÉGIMEN (LIR / SUNAT):
    * NRUS: cuota fija, sin pago a cuenta.
    * RER: sin pagos a cuenta mensuales (provisión anual 10%).
    * MYPE Tributario: 10% de la renta neta (1er tramo).
    * Régimen General: opción 1.5% ingresos netos (Art. 85 LIR) o 29.5% renta.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from datetime import datetime
from conexion import conectar_db, registrar_auditoria
from app_paths import CONFIG_FILE
import calendar
import os
import sys
import subprocess
import shutil
import json
import threading


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


# =========================================================
# CONFIGURACIÓN (LECTURA REAL + CLAVES LIMPIAS)
# =========================================================
def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "ruta_drive": "",
        "impresora": ""
    }
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config


def cargar_configuracion_impuestos():
    tasas = {"igv": 18.0, "renta_m": 1.5, "renta_a": 29.5, "retencion": 8.0,
             "regimen": "MYPE Tributario"}
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            tasas["igv"] = float(cfg.get("igv_porcentaje", 18.0))
            tasas["renta_m"] = float(cfg.get("renta_mensual_porcentaje", 1.5))
            tasas["renta_a"] = float(cfg.get("renta_anual_porcentaje", 29.5))
            tasas["retencion"] = float(cfg.get("retencion_porcentaje", 8.0))
            tasas["regimen"] = str(cfg.get("regimen_empresa", "MYPE Tributario"))
    except Exception:
        pass
    return tasas


CONFIG_REGIONAL = cargar_configuracion_regional()


def formatear_moneda(valor):
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    try:
        valor = float(valor)
    except Exception:
        valor = 0.0
    if formato == "1.000,00":
        str_val = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        str_val = f"{valor:,.2f}"
    return f"{simbolo} {str_val}"


def desformatear_numero(valor_str):
    if not valor_str:
        return 0.0
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    val = str(valor_str).replace(simbolo, "").strip()
    if formato == "1.000,00":
        val = val.replace(".", "").replace(",", ".")
    else:
        val = val.replace(",", "")
    try:
        return float(val)
    except ValueError:
        return 0.0


def obtener_ruta_base_drive():
    ruta = str(CONFIG_REGIONAL.get("ruta_drive", "")).strip()
    if ruta:
        return os.path.expanduser(ruta)
    return ""


def aplicar_estilo_treeview():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=26, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000", relief="flat", font=("Arial", 10, "bold"), bordercolor="#e0e0e0", borderwidth=1)


_SCHEMA_IMP_OK = False


class CalculoImpuestosApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.pantalla_expandida = False
        self.usuario_activo = "Desconocido"
        self.carpeta_comprobantes = ""
        aplicar_estilo_treeview()
        self.inicializar_bd()
        self.crear_interfaz()

    # =======================================================
    # SCHEMA: CREATE/ALTER SOLO LA PRIMERA VEZ POR SESIÓN
    # =======================================================
    def inicializar_bd(self):
        global _SCHEMA_IMP_OK
        if _SCHEMA_IMP_OK:
            return
        ruta_base = obtener_ruta_base_drive()
        if ruta_base:
            self.carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_impuestos")
            if not os.path.exists(self.carpeta_comprobantes):
                try:
                    os.makedirs(self.carpeta_comprobantes)
                except Exception:
                    pass
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS registro_impuestos (
                    id SERIAL PRIMARY KEY,
                    periodo VARCHAR(20) UNIQUE,
                    ventas_netas NUMERIC,
                    igv_ventas NUMERIC,
                    compras_netas NUMERIC,
                    igv_compras NUMERIC,
                    credito_fiscal_anterior NUMERIC,
                    igv_a_pagar NUMERIC,
                    credito_fiscal_siguiente NUMERIC,
                    impuesto_renta NUMERIC,
                    total_sunat NUMERIC,
                    saldo_nacion_anterior NUMERIC,
                    detracciones_del_mes NUMERIC,
                    nacion_utilizado NUMERIC,
                    saldo_nacion_siguiente NUMERIC,
                    pago_bolsillo NUMERIC
                )
            """)
            conn.commit()
            for sql in (
                "ALTER TABLE registro_impuestos ADD COLUMN IF NOT EXISTS detracciones_compras NUMERIC DEFAULT 0",
                "ALTER TABLE registro_impuestos ADD COLUMN IF NOT EXISTS archivo_pago TEXT DEFAULT ''",
                "ALTER TABLE registro_impuestos ADD COLUMN IF NOT EXISTS provision_anual NUMERIC DEFAULT 0",
            ):
                try:
                    cursor.execute(sql)
                    conn.commit()
                except Exception:
                    conn.rollback()
            _SCHEMA_IMP_OK = True
        except Exception as e:
            print("Error BD Impuestos:", e)
        finally:
            conn.close()

    def toggle_pantalla_completa(self):
        sidebar = None
        try:
            if self.parent_frame.master:
                for child in self.parent_frame.master.winfo_children():
                    if hasattr(child, "cget") and child.cget("width") == 280:
                        sidebar = child
                        break
        except Exception:
            pass
        if getattr(self, "pantalla_expandida", False):
            if sidebar:
                sidebar.pack(side="left", fill="y", before=self.parent_frame)
            self.f_form.pack(side="left", fill="y", padx=(0, 15), before=self.f_wrapper_derecha)
            self.btn_pantalla.configure(text="[ + ] Pantalla Completa", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = False
        else:
            if sidebar:
                sidebar.pack_forget()
            self.f_form.pack_forget()
            self.btn_pantalla.configure(text="[ - ] Restaurar Vista", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = True

    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(self.frame_main, text="🏛️ CÁLCULO DE IMPUESTOS MENSUALES (SUNAT)", font=("Arial", 18, "bold"), text_color="#1f538d").pack(anchor="w", pady=(0, 10))
        frame_split = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)
        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=380, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))
        ctk.CTkLabel(self.f_form, text="Generar Declaración Mensual", font=("Arial", 13, "bold"), text_color="#1f538d").pack(pady=(5, 15))
        f_periodo = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_periodo.pack(fill="x", padx=10, pady=5)
        meses = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        self.combo_mes = ctk.CTkComboBox(f_periodo, values=meses, state="readonly", width=80)
        self.combo_mes.pack(side="left", padx=(0, 5))
        self.combo_mes.set(f"{datetime.now().month:02d}")
        anios = [str(y) for y in range(2023, 2031)]
        self.combo_anio = ctk.CTkComboBox(f_periodo, values=anios, state="readonly", width=90)
        self.combo_anio.pack(side="left")
        self.combo_anio.set(str(datetime.now().year))
        btn_calcular = ctk.CTkButton(f_periodo, text="⚙️ Calcular", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.calcular_mes)
        btn_calcular.pack(side="right", fill="x", expand=True, padx=(10, 0))
        tasas = cargar_configuracion_impuestos()
        f_igv = ctk.CTkFrame(self.f_form, border_width=1, border_color="#ccc", fg_color="#ffffff")
        f_igv.pack(fill="x", padx=10, pady=10)
        self.lbl_titulo_igv = ctk.CTkLabel(f_igv, text=f"Cálculo de IGV ({tasas['igv']}%)", font=("Arial", 12, "bold"), text_color="#1f538d")
        self.lbl_titulo_igv.pack(pady=(5, 0))
        self.lbl_igv_ventas = ctk.CTkLabel(f_igv, text=f"IGV Ventas: {formatear_moneda(0)}", text_color="#333333")
        self.lbl_igv_ventas.pack(anchor="w", padx=10)
        self.lbl_compras_netas = ctk.CTkLabel(f_igv, text=f"Compras Netas (Base): {formatear_moneda(0)}", text_color="#555")
        self.lbl_compras_netas.pack(anchor="w", padx=10)
        self.lbl_igv_compras = ctk.CTkLabel(f_igv, text=f"Crédito Fiscal (IGV Compras sin Recibos): -{formatear_moneda(0)}", text_color="#333333")
        self.lbl_igv_compras.pack(anchor="w", padx=10)
        self.lbl_credito_ant = ctk.CTkLabel(f_igv, text=f"Crédito Fiscal Mes Anterior: -{formatear_moneda(0)}", text_color="#d35400")
        self.lbl_credito_ant.pack(anchor="w", padx=10)
        self.lbl_igv_pagar = ctk.CTkLabel(f_igv, text=f"IGV A PAGAR: {formatear_moneda(0)}", font=("Arial", 12, "bold"), text_color="#c0392b")
        self.lbl_igv_pagar.pack(anchor="e", padx=10, pady=(5, 10))
        f_renta = ctk.CTkFrame(self.f_form, border_width=1, border_color="#ccc", fg_color="#ffffff")
        f_renta.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(f_renta, text="Cálculos de Renta", font=("Arial", 12, "bold"), text_color="#1f538d").pack(pady=(5, 0))
        self.lbl_ventas_netas = ctk.CTkLabel(f_renta, text=f"Base Imponible Ventas (Subtotal): {formatear_moneda(0)}", text_color="#333333")
        self.lbl_ventas_netas.pack(anchor="w", padx=10)
        self.lbl_renta_pagar = ctk.CTkLabel(f_renta, text=f"PAGO A CUENTA RENTA ({tasas['regimen']}): {formatear_moneda(0)}", font=("Arial", 12, "bold"), text_color="#c0392b")
        self.lbl_renta_pagar.pack(anchor="e", padx=10, pady=(5, 0))
        self.lbl_utilidad_mes = ctk.CTkLabel(f_renta, text=f"Utilidad Bruta (Ventas - Compras): {formatear_moneda(0)}", text_color="#555555")
        self.lbl_utilidad_mes.pack(anchor="w", padx=10, pady=(5, 0))
        self.lbl_provision_anual = ctk.CTkLabel(f_renta, text=f"PROV. RENTA ANUAL ({tasas['renta_a']}%): {formatear_moneda(0)}", font=("Arial", 11, "bold"), text_color="#e67e22")
        self.lbl_provision_anual.pack(anchor="e", padx=10, pady=(0, 10))
        f_banco = ctk.CTkFrame(self.f_form, fg_color="#e8f8f5", border_width=1, border_color="#1abc9c")
        f_banco.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(f_banco, text="Cuentas Banco de la Nación (Detracciones)", font=("Arial", 10, "bold"), text_color="#16a085").pack(pady=(5, 0))
        self.lbl_bn_ant = ctk.CTkLabel(f_banco, text=f"Saldo B.N. Anterior (A tu favor): {formatear_moneda(0)}", text_color="#333333")
        self.lbl_bn_ant.pack(anchor="w", padx=10)
        self.lbl_bn_nuevo = ctk.CTkLabel(f_banco, text=f"Nuevas Detracciones Ventas: {formatear_moneda(0)}", text_color="#333333")
        self.lbl_bn_nuevo.pack(anchor="w", padx=10)
        self.lbl_det_compras = ctk.CTkLabel(f_banco, text=f"Detracciones a Prov. (Egresos): {formatear_moneda(0)}", text_color="#c0392b")
        self.lbl_det_compras.pack(anchor="w", padx=10, pady=(2, 0))
        ctk.CTkLabel(f_banco, text="Ajuste manual de saldo B.N. real (Opcional):", font=("Arial", 10), text_color="#333333").pack(anchor="w", padx=10, pady=(5, 0))
        self.ent_bn_real = ctk.CTkEntry(f_banco, height=25)
        self.ent_bn_real.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_bn_real.bind("<KeyRelease>", self.recalcular_totales_finales)
        f_tot = ctk.CTkFrame(self.f_form, fg_color="#2c3e50")
        f_tot.pack(fill="x", padx=10, pady=10)
        self.lbl_total_sunat = ctk.CTkLabel(f_tot, text=f"TOTAL DEUDA SUNAT: {formatear_moneda(0)}", font=("Arial", 12, "bold"), text_color="white")
        self.lbl_total_sunat.pack(anchor="w", padx=10, pady=(10, 2))
        self.lbl_bn_usar = ctk.CTkLabel(f_tot, text=f"Pago con B. Nación: -{formatear_moneda(0)}", font=("Arial", 11), text_color="#f1c40f")
        self.lbl_bn_usar.pack(anchor="w", padx=10)
        self.lbl_pago_bolsillo = ctk.CTkLabel(f_tot, text=f"PAGO EFECTIVO (Bolsillo): {formatear_moneda(0)}", font=("Arial", 15, "bold"), text_color="#e74c3c")
        self.lbl_pago_bolsillo.pack(anchor="e", padx=10, pady=(5, 10))
        self.lbl_bn_restante = ctk.CTkLabel(self.f_form, text=f"Nuevo Saldo Banco Nación a favor: {formatear_moneda(0)}", font=("Arial", 11, "bold"), text_color="#16a085")
        self.lbl_bn_restante.pack(pady=5)
        self.lbl_credito_restante = ctk.CTkLabel(self.f_form, text=f"Nuevo Crédito Fiscal a favor: {formatear_moneda(0)}", font=("Arial", 11, "bold"), text_color="#d35400")
        self.lbl_credito_restante.pack(pady=(0, 15))
        btn_guardar = ctk.CTkButton(self.f_form, text="💾 Guardar Declaración del Mes", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_registro)
        btn_guardar.pack(fill="x", padx=10, pady=(0, 15))
        self.f_wrapper_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_wrapper_derecha.pack(side="right", fill="both", expand=True)
        ctk.CTkLabel(self.f_wrapper_derecha, text="Historial de Declaraciones", font=("Arial", 13, "bold")).pack(anchor="w", pady=(5, 10))
        f_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)
        columnas = ("periodo", "ventas", "igv_v", "compras", "igv_c", "igv_pagar", "renta", "renta_anual", "pago_bolsillo", "archivo")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", style="Treeview")
        self.tabla.heading("periodo", text="Periodo", anchor="center")
        self.tabla.heading("ventas", text="Ventas Netas", anchor="center")
        self.tabla.heading("igv_v", text="IGV Ventas", anchor="center")
        self.tabla.heading("compras", text="Compras Netas", anchor="center")
        self.tabla.heading("igv_c", text="IGV Compras", anchor="center")
        self.tabla.heading("igv_pagar", text="IGV SUNAT", anchor="center")
        self.tabla.heading("renta", text="Renta Mensual", anchor="center")
        self.tabla.heading("renta_anual", text="Prov. Anual", anchor="center")
        self.tabla.heading("pago_bolsillo", text="Efectivo", anchor="center")
        self.tabla.heading("archivo", text="Comprobante", anchor="center")
        self.tabla.column("periodo", width=60, anchor="center")
        self.tabla.column("ventas", width=95, anchor="e")
        self.tabla.column("igv_v", width=95, anchor="e")
        self.tabla.column("compras", width=95, anchor="e")
        self.tabla.column("igv_c", width=95, anchor="e")
        self.tabla.column("igv_pagar", width=95, anchor="e")
        self.tabla.column("renta", width=95, anchor="e")
        self.tabla.column("renta_anual", width=95, anchor="e")
        self.tabla.column("pago_bolsillo", width=95, anchor="e")
        self.tabla.column("archivo", width=95, anchor="center")
        self.tabla.bind("<Double-1>", self.abrir_comprobante)
        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        scroll_x = ttk.Scrollbar(f_tabla, orient="horizontal", command=self.tabla.xview)
        self.tabla.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        scroll_x.pack(side="bottom", fill="x")
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        f_btn_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=(10, 0))
        self.btn_pantalla = ctk.CTkButton(f_btn_tabla, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="left")
        btn_excel = ctk.CTkButton(f_btn_tabla, text="📊 Exportar a Excel", font=("Arial", 12, "bold"), fg_color="#28a745", hover_color="#218838", command=self.exportar_excel_impuestos)
        btn_excel.pack(side="left", padx=15)
        btn_eliminar = ctk.CTkButton(f_btn_tabla, text="❌ Eliminar Declaración", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_registro)
        btn_eliminar.pack(side="right")
        self.datos_actuales = {}
        self.cargar_historial()

    def exportar_excel_impuestos(self):
        try:
            import pandas as pd
        except ImportError:
            messagebox.showerror("Error", "Falta la librería pandas. Ejecuta: pip install pandas openpyxl")
            return
        filas = []
        for item in self.tabla.get_children():
            valores = self.tabla.item(item)["values"]
            filas.append(valores[:-1])
        if not filas:
            messagebox.showwarning("Aviso", "No hay declaraciones guardadas para exportar.")
            return
        columnas = ["Periodo", "Ventas Netas", "IGV Ventas", "Compras Netas", "IGV Compras", "IGV SUNAT", "Renta Mensual", "Provisión Renta Anual", "Pago Efectivo"]
        ruta = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"Declaraciones_Impuestos_{datetime.now().year}.xlsx",
            title="Exportar Declaraciones a Excel",
            filetypes=[("Archivos Excel", "*.xlsx")]
        )
        if ruta:
            try:
                df = pd.DataFrame(filas, columns=columnas)
                df.to_excel(ruta, index=False, engine='openpyxl')
                registrar_auditoria(self.usuario_activo, "Cálculo de Impuestos", "Exportó a Excel el historial de declaraciones de impuestos")
                messagebox.showinfo("Éxito", f"Reporte de Impuestos exportado a:\n{ruta}")
                abrir_documento(ruta)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo crear el archivo:\n{e}")

    def convertir_a_fecha(self, fecha_str):
        if not fecha_str:
            return None
        formatos = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y"]
        for fmt in formatos:
            try:
                return datetime.strptime(fecha_str.strip(), fmt)
            except ValueError:
                pass
        return None

    def _get_mes_anterior(self, mes, anio):
        m = int(mes)
        a = int(anio)
        if m == 1:
            return "12", str(a - 1)
        else:
            return f"{(m - 1):02d}", str(a)

    # =======================================================
    # CÁLCULO DEL MES EN SEGUNDO PLANO (HILO) + RÉGIMEN SUNAT
    # =======================================================
    def calcular_mes(self):
        mes = self.combo_mes.get()
        anio = self.combo_anio.get()

        def tarea():
            resultado = None
            error = None
            tasas = cargar_configuracion_impuestos()
            conn = conectar_db(silencioso=True)
            if not conn:
                error = "Sin conexión a la base de datos."
            else:
                try:
                    cursor = conn.cursor()
                    credito_fiscal_ant = 0.0
                    saldo_bn_ant = 0.0
                    mes_ant, anio_ant = self._get_mes_anterior(mes, anio)
                    periodo_anterior = f"{mes_ant}/{anio_ant}"
                    cursor.execute("SELECT credito_fiscal_siguiente, saldo_nacion_siguiente FROM registro_impuestos WHERE periodo = %s", (periodo_anterior,))
                    res_ant = cursor.fetchone()
                    if res_ant:
                        credito_fiscal_ant = float(res_ant[0])
                        saldo_bn_ant = float(res_ant[1])
                    cursor.execute("SELECT tipo_documento, subtotal, impuesto, det_monto, fecha FROM facturas_emitidas")
                    ventas_netas = 0.0
                    igv_ventas = 0.0
                    detracciones_del_mes = 0.0
                    for tipo, sub, imp, det, fecha in cursor.fetchall():
                        f_dt = self.convertir_a_fecha(str(fecha))
                        if f_dt and f"{f_dt.month:02d}" == mes and str(f_dt.year) == anio:
                            tipo_str = str(tipo).lower()
                            if "factura" in tipo_str or "boleta" in tipo_str:
                                ventas_netas += float(sub or 0)
                                igv_ventas += float(imp or 0)
                            detracciones_del_mes += float(det or 0)
                    cursor.execute("SELECT tipo_documento, subtotal, impuesto, det_monto, fecha FROM facturas_recibidas")
                    compras_netas = 0.0
                    igv_compras = 0.0
                    detracciones_compras = 0.0
                    for tipo, sub, imp, det, fecha in cursor.fetchall():
                        f_dt = self.convertir_a_fecha(str(fecha))
                        if f_dt and f"{f_dt.month:02d}" == mes and str(f_dt.year) == anio:
                            if "factura" in str(tipo).lower():
                                compras_netas += float(sub or 0)
                                igv_compras += float(imp or 0)
                            detracciones_compras += float(det or 0)
                    # ---- RENTA MENSUAL SEGÚN RÉGIMEN (conforme LIR / SUNAT) ----
                    regimen = str(tasas.get("regimen", "MYPE Tributario"))
                    utilidad_bruta = ventas_netas - compras_netas
                    if "NRUS" in regimen:
                        renta_pagar = 0.0
                        provision_anual = 0.0
                        renta_detalle = "NRUS: cuota fija, sin pago a cuenta"
                        provision_detalle = "NRUS"
                    elif "RER" in regimen:
                        renta_pagar = 0.0
                        provision_anual = max(0.0, utilidad_bruta) * 0.10
                        renta_detalle = "RER: sin pago a cuenta mensual"
                        provision_detalle = "10% anual RER"
                    elif "MYPE" in regimen:
                        renta_pagar = max(0.0, utilidad_bruta) * 0.10
                        provision_anual = max(0.0, utilidad_bruta) * 0.10
                        renta_detalle = "10% renta neta (RMT)"
                        provision_detalle = "10% RMT"
                    else:
                        renta_pagar = ventas_netas * (tasas["renta_m"] / 100.0)
                        provision_anual = max(0.0, utilidad_bruta) * (tasas["renta_a"] / 100.0)
                        renta_detalle = f"{tasas['renta_m']}% ingresos (Art. 85 LIR)"
                        provision_detalle = f"{tasas['renta_a']}% renta"
                    igv_neto = igv_ventas - igv_compras - credito_fiscal_ant
                    igv_pagar = 0.0
                    nuevo_credito_fiscal = 0.0
                    if igv_neto > 0:
                        igv_pagar = igv_neto
                    else:
                        nuevo_credito_fiscal = abs(igv_neto)
                    total_deuda_sunat = igv_pagar + renta_pagar
                    resultado = {
                        "periodo": f"{mes}/{anio}",
                        "ventas_netas": ventas_netas,
                        "igv_ventas": igv_ventas,
                        "compras_netas": compras_netas,
                        "igv_compras": igv_compras,
                        "credito_fiscal_ant": credito_fiscal_ant,
                        "igv_pagar": igv_pagar,
                        "nuevo_credito_fiscal": nuevo_credito_fiscal,
                        "renta_pagar": renta_pagar,
                        "provision_anual": provision_anual,
                        "total_deuda_sunat": total_deuda_sunat,
                        "saldo_bn_ant": saldo_bn_ant,
                        "detracciones_del_mes": detracciones_del_mes,
                        "detracciones_compras": detracciones_compras,
                        "regimen": regimen,
                        "renta_detalle": renta_detalle,
                        "provision_detalle": provision_detalle,
                        "tasas": tasas
                    }
                except Exception as e:
                    error = str(e)
                finally:
                    conn.close()
            self.parent_frame.after(0, lambda r=resultado, e=error: self._aplicar_calculo(r, e))

        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_calculo(self, d, error):
        if error:
            messagebox.showerror("Error", f"Fallo al calcular mes:\n{error}")
            return
        if not d:
            return
        tasas = d["tasas"]
        self.datos_actuales = d
        self.lbl_titulo_igv.configure(text=f"Cálculo de IGV ({tasas['igv']}%)")
        self.lbl_igv_ventas.configure(text=f"IGV Ventas: {formatear_moneda(d['igv_ventas'])}")
        self.lbl_compras_netas.configure(text=f"Compras Netas (Base Imponible): {formatear_moneda(d['compras_netas'])}")
        self.lbl_igv_compras.configure(text=f"Crédito Fiscal (IGV Compras): -{formatear_moneda(d['igv_compras'])}")
        self.lbl_credito_ant.configure(text=f"Crédito Fiscal Mes Anterior: -{formatear_moneda(d['credito_fiscal_ant'])}")
        self.lbl_igv_pagar.configure(text=f"IGV A PAGAR: {formatear_moneda(d['igv_pagar'])}")
        self.lbl_ventas_netas.configure(text=f"Base Imponible Ventas (Subtotal): {formatear_moneda(d['ventas_netas'])}")
        det_renta = d.get("renta_detalle", f"{tasas['renta_m']}%")
        self.lbl_renta_pagar.configure(text=f"PAGO A CUENTA RENTA ({det_renta}): {formatear_moneda(d['renta_pagar'])}")
        self.lbl_utilidad_mes.configure(text=f"Utilidad Bruta (Ventas - Compras): {formatear_moneda(d['ventas_netas'] - d['compras_netas'])}")
        det_prov = d.get("provision_detalle", f"{tasas['renta_a']}%")
        self.lbl_provision_anual.configure(text=f"PROV. RENTA ANUAL ({det_prov}): {formatear_moneda(d['provision_anual'])}")
        self.lbl_bn_ant.configure(text=f"Saldo B.N. Anterior (A tu favor): {formatear_moneda(d['saldo_bn_ant'])}")
        self.lbl_bn_nuevo.configure(text=f"Nuevas Detracciones Ventas: {formatear_moneda(d['detracciones_del_mes'])}")
        self.lbl_det_compras.configure(text=f"Detracciones a Prov. (Egresos): {formatear_moneda(d['detracciones_compras'])}")
        fondo_sugerido = d['saldo_bn_ant'] + d['detracciones_del_mes']
        self.ent_bn_real.delete(0, tk.END)
        if CONFIG_REGIONAL.get("formato_numero", "1,000.00") == "1.000,00":
            self.ent_bn_real.insert(0, f"{fondo_sugerido:.2f}".replace(".", ","))
        else:
            self.ent_bn_real.insert(0, f"{fondo_sugerido:.2f}")
        self.recalcular_totales_finales()

    def recalcular_totales_finales(self, *args):
        if not self.datos_actuales:
            return
        try:
            fondo_bn = desformatear_numero(self.ent_bn_real.get())
        except ValueError:
            fondo_bn = 0.0
        total_sunat = self.datos_actuales["total_deuda_sunat"]
        if fondo_bn >= total_sunat:
            pago_bolsillo = 0.0
            bn_utilizado = total_sunat
            bn_restante = fondo_bn - total_sunat
        else:
            pago_bolsillo = total_sunat - fondo_bn
            bn_utilizado = fondo_bn
            bn_restante = 0.0
        self.datos_actuales["fondo_bn_real"] = fondo_bn
        self.datos_actuales["bn_utilizado"] = bn_utilizado
        self.datos_actuales["bn_restante"] = bn_restante
        self.datos_actuales["pago_bolsillo"] = pago_bolsillo
        self.lbl_total_sunat.configure(text=f"TOTAL DEUDA SUNAT: {formatear_moneda(total_sunat)}")
        self.lbl_bn_usar.configure(text=f"Pago con B. Nación: -{formatear_moneda(bn_utilizado)}")
        self.lbl_pago_bolsillo.configure(text=f"PAGO EFECTIVO (Bolsillo): {formatear_moneda(pago_bolsillo)}")
        self.lbl_bn_restante.configure(text=f"Nuevo Saldo Banco Nación a favor: {formatear_moneda(bn_restante)}")
        self.lbl_credito_restante.configure(text=f"Nuevo Crédito Fiscal a favor: {formatear_moneda(self.datos_actuales['nuevo_credito_fiscal'])}")

    def guardar_registro(self):
        if not self.datos_actuales:
            messagebox.showwarning("Aviso", "Primero debe hacer clic en 'Calcular'.")
            return
        ruta_base = obtener_ruta_base_drive()
        if not ruta_base:
            messagebox.showwarning("Configuración Requerida",
                                   "No ha configurado la ruta de Google Drive.\n\n"
                                   "Vaya a: ⚙️ Configuración General → 'Carpeta de Google Drive'\ny guárdela para poder adjuntar comprobantes de pago.")
            return
        self.carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_impuestos")
        d = self.datos_actuales
        messagebox.showinfo("Comprobante de Pago", "A continuación, seleccione el comprobante de pago de SUNAT (PDF o Imagen).\n\nPuede cancelar si no desea adjuntar uno ahora.")
        ruta_origen = filedialog.askopenfilename(
            title="Seleccionar Comprobante de Pago SUNAT",
            filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")]
        )
        ruta_destino_final = ""
        if ruta_origen:
            try:
                if not os.path.exists(self.carpeta_comprobantes):
                    os.makedirs(self.carpeta_comprobantes)
                _, ext = os.path.splitext(ruta_origen)
                periodo_limpio = d["periodo"].replace("/", "_")
                nombre_limpio = f"Pago_Impuesto_{periodo_limpio}{ext}"
                ruta_destino_final = os.path.join(self.carpeta_comprobantes, nombre_limpio)
                shutil.copy2(ruta_origen, ruta_destino_final)
            except Exception as e:
                messagebox.showerror("Error de Archivo", f"No se pudo guardar el archivo físico:\n{e}")
                ruta_destino_final = ""
        conn = conectar_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT archivo_pago FROM registro_impuestos WHERE periodo = %s", (d["periodo"],))
            existe = cursor.fetchone()
            if existe:
                if not messagebox.askyesno("Sobreescribir", f"El periodo {d['periodo']} ya está declarado.\n¿Desea recalcular y sobreescribir los datos?"):
                    return
                old_file = existe[0]
                if old_file and os.path.exists(old_file) and ruta_destino_final:
                    try:
                        os.remove(old_file)
                    except Exception:
                        pass
                if not ruta_destino_final and old_file:
                    ruta_destino_final = old_file
                cursor.execute("DELETE FROM registro_impuestos WHERE periodo = %s", (d["periodo"],))
                conn.commit()
            cursor.execute("""
                INSERT INTO registro_impuestos (
                    periodo, ventas_netas, igv_ventas, compras_netas, igv_compras,
                    credito_fiscal_anterior, igv_a_pagar, credito_fiscal_siguiente,
                    impuesto_renta, total_sunat, saldo_nacion_anterior,
                    detracciones_del_mes, nacion_utilizado, saldo_nacion_siguiente,
                    pago_bolsillo, detracciones_compras, archivo_pago, provision_anual
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                d["periodo"], d["ventas_netas"], d["igv_ventas"], d["compras_netas"], d["igv_compras"],
                d["credito_fiscal_ant"], d["igv_pagar"], d["nuevo_credito_fiscal"],
                d["renta_pagar"], d["total_deuda_sunat"], d["saldo_bn_ant"],
                d["detracciones_del_mes"], d["bn_utilizado"], d["bn_restante"],
                d["pago_bolsillo"], d["detracciones_compras"], ruta_destino_final, d["provision_anual"]
            ))
            conn.commit()
            registrar_auditoria(self.usuario_activo, "Cálculo de Impuestos", f"Registró/Actualizó la declaración del periodo {d['periodo']}")
            messagebox.showinfo("Éxito", f"Declaración del periodo {d['periodo']} guardada correctamente.\nEl saldo del Banco de la Nación ha pasado al siguiente mes.")
            self.cargar_historial()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar:\n{e}")
        finally:
            conn.close()

    # =======================================================
    # HISTORIAL EN SEGUNDO PLANO (HILO + TOKEN)
    # =======================================================
    def cargar_historial(self):
        self._hist_token = getattr(self, "_hist_token", 0) + 1
        token = self._hist_token

        def tarea():
            rows = []
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT periodo, ventas_netas, igv_ventas, compras_netas, igv_compras,
                               igv_a_pagar, impuesto_renta, provision_anual, pago_bolsillo, archivo_pago
                        FROM registro_impuestos
                        ORDER BY substring(periodo from 4 for 4) DESC, substring(periodo from 1 for 2) DESC
                    """)
                    rows = cursor.fetchall()
                except Exception:
                    rows = []
                finally:
                    conn.close()
            self.parent_frame.after(0, lambda t=token, r=rows: self._pintar_historial(t, r))

        threading.Thread(target=tarea, daemon=True).start()

    def _pintar_historial(self, token, rows):
        if token != getattr(self, "_hist_token", 0):
            return
        for item in self.tabla.get_children():
            self.tabla.delete(item)
        for r in rows:
            tiene_arch = "✅ Ver" if r[9] else "❌ No"
            self.tabla.insert("", tk.END, values=(
                r[0], formatear_moneda(r[1]), formatear_moneda(r[2]), formatear_moneda(r[3]), formatear_moneda(r[4]),
                formatear_moneda(r[5]), formatear_moneda(r[6]), formatear_moneda(r[7]), formatear_moneda(r[8]), tiene_arch
            ))

    def abrir_comprobante(self, event):
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        periodo = self.tabla.item(seleccion[0], "values")[0]
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT archivo_pago FROM registro_impuestos WHERE periodo = %s", (periodo,))
            res = cursor.fetchone()
            if res and res[0] and os.path.exists(res[0]):
                abrir_documento(res[0])
            else:
                messagebox.showinfo("Aviso", "No hay comprobante adjunto para este periodo.")
        except Exception:
            pass
        finally:
            conn.close()

    def eliminar_registro(self):
        sel = self.tabla.selection()
        if not sel:
            return
        periodo = self.tabla.item(sel[0], "values")[0]
        if messagebox.askyesno("Confirmar", f"¿Eliminar la declaración de impuestos del periodo {periodo}?"):
            conn = conectar_db()
            if not conn:
                return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT archivo_pago FROM registro_impuestos WHERE periodo = %s", (periodo,))
                res = cursor.fetchone()
                if res and res[0] and os.path.exists(res[0]):
                    try:
                        os.remove(res[0])
                    except Exception:
                        pass
                cursor.execute("DELETE FROM registro_impuestos WHERE periodo = %s", (periodo,))
                conn.commit()
                registrar_auditoria(self.usuario_activo, "Cálculo de Impuestos", f"Eliminó la declaración del periodo {periodo}")
                self.cargar_historial()
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                conn.close()


if __name__ == "__main__":
    root = ctk.CTk()
    app = CalculoImpuestosApp(root)
    root.mainloop()