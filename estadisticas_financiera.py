# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
import calendar
import os
import sys 
import json
import subprocess 
from datetime import datetime
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

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
        if os.path.exists("config_local.json"):
            with open("config_local.json", "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception: pass
    return config

# 🚀 FUNCIÓN PARA LEER EL PORCENTAJE DE RENTA ANUAL DESDE CONFIG
def obtener_porcentaje_renta_anual():
    try:
        if os.path.exists("config_local.json"):
            with open("config_local.json", "r", encoding="utf-8") as f:
                config = json.load(f)
                return float(config.get("renta_anual_porcentaje", "0.0"))
    except Exception: pass
    return 0.0

# 🚀 FUNCIÓN PARA LEER EL PORCENTAJE ISR MENSUAL DESDE LA CONFIGURACIÓN GENERAL (control_general.py -> Configuración General)
def obtener_porcentaje_renta_mensual():
    try:
        if os.path.exists("config_local.json"):
            with open("config_local.json", "r", encoding="utf-8") as f:
                config = json.load(f)
                return float(config.get("renta_mensual_porcentaje", "0.0"))
    except Exception: pass
    return 0.0

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

class EstadisticasFinancieraApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.usuario_activo = "Desconocido"
        self.tasa_renta_anual = obtener_porcentaje_renta_anual()
        self.tasa_renta_mensual = obtener_porcentaje_renta_mensual()
        self.crear_interfaz()
        self.cargar_bancos_y_listas()
        
        self.parent_frame.after(200, self.cargar_kpis)

    def abrir_calendario(self, entry_objetivo):
        CalendarioNativo(self.parent_frame.winfo_toplevel(), entry_objetivo)

    def crear_interfaz(self):
        self.frame_main = ctk.CTkScrollableFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)

        ctk.CTkLabel(
            self.frame_main, 
            text="📈 DASHBOARD GERENCIAL DE RENTABILIDAD", 
            font=("Arial", 18, "bold"), 
            text_color="#1f538d"
        ).pack(anchor="w", pady=(0, 15))

        f_filtro = ctk.CTkFrame(self.frame_main, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0", corner_radius=10)
        f_filtro.pack(fill="x", pady=(0, 20), ipadx=10, ipady=10)

        f_entidades = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_entidades.pack(fill="x", pady=(5, 10), padx=10)

        f_banco = ctk.CTkFrame(f_entidades, fg_color="transparent")
        f_banco.pack(fill="x", pady=2)
        ctk.CTkLabel(f_banco, text="Filtrar por Cuenta / Banco:", font=("Arial", 12, "bold"), width=160, anchor="e").pack(side="left", padx=(0, 10))
        self.combo_banco = ctk.CTkComboBox(f_banco, values=["Todas las Cuentas"], state="readonly", width=400)
        self.combo_banco.pack(side="left")
        self.combo_banco.set("Todas las Cuentas")

        f_cp = ctk.CTkFrame(f_entidades, fg_color="transparent")
        f_cp.pack(fill="x", pady=(8,0))
        
        # 🚀 FIX: ETIQUETA ADAPTADA A VEHÍCULO / CLIENTE
        ctk.CTkLabel(f_cp, text="Filtrar por Vehículo / Cliente:", font=("Arial", 12, "bold"), width=180, anchor="e").pack(side="left", padx=(0, 10))
        self.combo_cliente = ctk.CTkComboBox(f_cp, values=["Todos los Vehículos / Clientes"], state="readonly", width=250)
        self.combo_cliente.pack(side="left", padx=(0, 20))
        self.combo_cliente.set("Todos los Vehículos / Clientes")

        ctk.CTkLabel(f_cp, text="Filtrar por Proveedor:", font=("Arial", 12, "bold"), width=140, anchor="e").pack(side="left", padx=(0, 10))
        self.combo_proveedor = ctk.CTkComboBox(f_cp, values=["Todos los Proveedores"], state="readonly", width=250)
        self.combo_proveedor.pack(side="left")
        self.combo_proveedor.set("Todos los Proveedores")

        f_periodo = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_periodo.pack(fill="x", pady=5, padx=10)

        ctk.CTkLabel(f_periodo, text="Tipo de Fecha:", font=("Arial", 12, "bold"), width=160, anchor="e").pack(side="left", padx=(0, 10))
        
        self.tipo_fecha_var = ctk.StringVar(value="Mensual/Anual")
        self.opcion_mensual = ctk.CTkRadioButton(f_periodo, text="Mensual / Anual", variable=self.tipo_fecha_var, value="Mensual/Anual", command=self.toggle_fecha_modo)
        self.opcion_mensual.pack(side="left", padx=10)
        
        self.opcion_rango = ctk.CTkRadioButton(f_periodo, text="Rango de Fechas Exacto", variable=self.tipo_fecha_var, value="Rango", command=self.toggle_fecha_modo)
        self.opcion_rango.pack(side="left", padx=10)

        self.f_controles_fecha = ctk.CTkFrame(f_filtro, fg_color="transparent")
        self.f_controles_fecha.pack(fill="x", pady=5, padx=10)

        self.f_mensual = ctk.CTkFrame(self.f_controles_fecha, fg_color="transparent")
        ctk.CTkLabel(self.f_mensual, text="Mes y Año:", font=("Arial", 12, "bold"), width=160, anchor="e").pack(side="left", padx=(0, 10))
        
        meses = ["Todos los meses", "01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        self.combo_mes = ctk.CTkComboBox(self.f_mensual, values=meses, state="readonly", width=140)
        self.combo_mes.pack(side="left", padx=5)
        self.combo_mes.set("Todos los meses")

        anios = [str(y) for y in range(2023, 2031)]
        self.combo_anio = ctk.CTkComboBox(self.f_mensual, values=anios, state="readonly", width=100)
        self.combo_anio.pack(side="left", padx=5)
        self.combo_anio.set(str(datetime.now().year))

        self.f_rango = ctk.CTkFrame(self.f_controles_fecha, fg_color="transparent")
        ctk.CTkLabel(self.f_rango, text="Desde:", font=("Arial", 12, "bold"), width=160, anchor="e").pack(side="left", padx=(0, 10))
        
        fmt_fecha = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        self.ent_fecha_ini = ctk.CTkEntry(self.f_rango, width=100, placeholder_text=fmt_fecha)
        self.ent_fecha_ini.pack(side="left")
        btn_cal_ini = ctk.CTkButton(self.f_rango, text="📅", width=30, height=28, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha_ini))
        btn_cal_ini.pack(side="left", padx=5)

        ctk.CTkLabel(self.f_rango, text="Hasta:", font=("Arial", 12, "bold")).pack(side="left", padx=10)
        self.ent_fecha_fin = ctk.CTkEntry(self.f_rango, width=100, placeholder_text=fmt_fecha)
        self.ent_fecha_fin.pack(side="left")
        btn_cal_fin = ctk.CTkButton(self.f_rango, text="📅", width=30, height=28, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha_fin))
        btn_cal_fin.pack(side="left", padx=5)

        self.f_mensual.pack(side="left", fill="x")

        f_btn_buscar = ctk.CTkFrame(f_filtro, fg_color="transparent")
        f_btn_buscar.pack(fill="x", pady=10)
        
        btn_calcular = ctk.CTkButton(f_btn_buscar, text="🔍 Procesar y Calcular Estadísticas", font=("Arial", 11, "bold"), fg_color="#1f538d", hover_color="#163b65", height=30, command=self.cargar_kpis)
        btn_calcular.pack(side="left", expand=True, anchor="e", padx=(0, 10))

        btn_excel = ctk.CTkButton(f_btn_buscar, text="📊 Exportar Dashboard a Excel", font=("Arial", 11, "bold"), fg_color="#27ae60", hover_color="#1e8449", height=30, command=self.exportar_excel)
        btn_excel.pack(side="left", expand=True, anchor="w", padx=(10, 0))

        f_tarjetas = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        f_tarjetas.pack(fill="x", pady=(0, 20))

        self.card_ventas = self.crear_tarjeta(f_tarjetas, "VENTAS DEL PERIODO (Facturado Neto)", formatear_moneda(0), "#1f538d", 0, 0)
        self.card_cobrado = self.crear_tarjeta(f_tarjetas, "COBRADO EN EL PERIODO (Dinero Real)", formatear_moneda(0), "#27ae60", 0, 1)
        self.card_por_cobrar = self.crear_tarjeta(f_tarjetas, "DEUDA TOTAL POR COBRAR (Al día de hoy)", formatear_moneda(0), "#e74c3c", 0, 2)
        
        self.card_compras = self.crear_tarjeta(f_tarjetas, "COMPRAS DEL PERIODO (Facturado Neto)", formatear_moneda(0), "#34495e", 1, 0)
        self.card_pagado = self.crear_tarjeta(f_tarjetas, "PAGADO EN EL PERIODO (Dinero Real)", formatear_moneda(0), "#7f8c8d", 1, 1)
        self.card_por_pagar = self.crear_tarjeta(f_tarjetas, "DEUDA TOTAL A PROVEEDORES (Al día de hoy)", formatear_moneda(0), "#e74c3c", 1, 2)

        f_rentabilidad = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        f_rentabilidad.pack(fill="x", pady=10)

        self.card_rentabilidad = self.crear_tarjeta_larga(f_rentabilidad, "RENTABILIDAD DEL NEGOCIO (Ventas Netas - Compras Netas)", formatear_moneda(0), "#1f538d")
        self.card_caja = self.crear_tarjeta_larga(f_rentabilidad, "FLUJO DE CAJA DE LA CUENTA (Dinero Efectivo Real: Cobrado - Pagado)", formatear_moneda(0), "#27ae60")
        
        self.card_provision_renta = self.crear_tarjeta_larga(f_rentabilidad, f"PROVISIÓN IMPUESTO A LA RENTA ANUAL ({self.tasa_renta_anual}%)", formatear_moneda(0), "#e67e22")
        self.card_provision_renta_mensual = self.crear_tarjeta_larga(f_rentabilidad, f"PROVISIÓN ISR MENSUAL ({self.tasa_renta_mensual}%)", formatear_moneda(0), "#d35400")

    def toggle_fecha_modo(self):
        modo = self.tipo_fecha_var.get()
        if modo == "Mensual/Anual":
            self.f_rango.pack_forget()
            self.f_mensual.pack(side="left", fill="x")
        else:
            self.f_mensual.pack_forget()
            self.f_rango.pack(side="left", fill="x")

    def crear_tarjeta(self, parent, titulo, valor, color_borde, row, col):
        frame = ctk.CTkFrame(parent, corner_radius=10, border_width=2, border_color=color_borde, fg_color="#ffffff")
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)

        ctk.CTkLabel(frame, text=titulo, font=("Arial", 11, "bold"), text_color="gray").pack(pady=(15, 5))
        lbl_valor = ctk.CTkLabel(frame, text=valor, font=("Arial", 22, "bold"), text_color=color_borde)
        lbl_valor.pack(pady=(0, 15))
        return lbl_valor

    def crear_tarjeta_larga(self, parent, titulo, valor, color_borde):
        frame = ctk.CTkFrame(parent, corner_radius=10, border_width=2, border_color=color_borde, fg_color="#ffffff")
        frame.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(frame, text=titulo, font=("Arial", 12, "bold"), text_color="gray").pack(pady=(15, 2))
        lbl_valor = ctk.CTkLabel(frame, text=valor, font=("Arial", 24, "bold"), text_color=color_borde)
        lbl_valor.pack(pady=(0, 15))
        return lbl_valor

    def cargar_bancos_y_listas(self):
        bancos_guardados = CONFIG_REGIONAL.get("cuentas_bancarias", [])
        lista_cuentas = ["Todas las Cuentas"]
        for b in bancos_guardados:
            banco_nom = b.get("banco", "").strip()
            cuenta_num = b.get("cuenta", "").strip()
            if banco_nom or cuenta_num:
                lista_cuentas.append(f"{banco_nom} - {cuenta_num}".strip(" - "))
        lista_cuentas.extend(["Efectivo / Caja Chica", "Tarjeta de Crédito", "Tarjeta de Débito", "Cheque", "Otro"])
        
        self.combo_banco.configure(values=lista_cuentas)
        self.combo_banco.set("Todas las Cuentas")

        # 🚀 FIX: CARGAMOS LA FLOTA DE VEHÍCULOS ADEMÁS DE CLIENTES
        cli_mem = cache_sistema.obtener('lista_clientes_flota_combobox')
        prov_mem = cache_sistema.obtener('lista_proveedores_combobox')
        
        if cli_mem is not None and prov_mem is not None:
            self._aplicar_listas(cli_mem, prov_mem)
        else:
            def tarea_listas():
                conn = conectar_db(silencioso=True)
                clis = []
                provs = []
                if conn:
                    try:
                        cursor = conn.cursor()
                        # Extraer placas de vehículos
                        try:
                            cursor.execute("SELECT placa, marca FROM flota_vehiculos ORDER BY placa ASC")
                            clis = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                        except Exception as e:
                            print("No se encontró tabla flota_vehiculos:", e)
                            conn.rollback()
                        
                        # Extraer también clientes (para mantener compatibilidad si facturas a nombre de personas)
                        cursor.execute("SELECT DISTINCT cliente FROM facturas_emitidas WHERE cliente IS NOT NULL AND cliente != '' ORDER BY cliente")
                        for r in cursor.fetchall():
                            nom = str(r[0]).strip()
                            if nom not in clis:
                                clis.append(nom)
                                
                        cache_sistema.guardar('lista_clientes_flota_combobox', clis)
                        
                        # Extraer proveedores
                        cursor.execute("SELECT DISTINCT proveedor FROM facturas_recibidas WHERE proveedor IS NOT NULL AND proveedor != '' ORDER BY proveedor")
                        provs = [str(r[0]).strip() for r in cursor.fetchall()]
                        cache_sistema.guardar('lista_proveedores_combobox', provs)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._aplicar_listas(clis, provs))
                
            threading.Thread(target=tarea_listas, daemon=True).start()
            
    def _aplicar_listas(self, clientes, proveedores):
        clientes.insert(0, "Todos los Vehículos / Clientes")
        self.combo_cliente.configure(values=clientes)
        self.combo_cliente.set("Todos los Vehículos / Clientes")

        proveedores.insert(0, "Todos los Proveedores")
        self.combo_proveedor.configure(values=proveedores)
        self.combo_proveedor.set("Todos los Proveedores")

    def convertir_a_fecha(self, fecha_str):
        if not fecha_str: return None
        formatos = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y"]
        for fmt in formatos:
            try:
                return datetime.strptime(fecha_str.strip(), fmt)
            except ValueError:
                pass
        return None

    def exportar_excel(self):
        try:
            import pandas as pd
        except ImportError:
            messagebox.showerror("Error", "Falta la librería pandas. Ejecuta: pip install pandas openpyxl")
            return

        banco_analizado = self.combo_banco.get()
        cliente_analizado = self.combo_cliente.get()
        proveedor_analizado = self.combo_proveedor.get()
        modo_fecha = self.tipo_fecha_var.get()
        
        if modo_fecha == "Rango":
            periodo_analizado = f"Desde el {self.ent_fecha_ini.get()} hasta el {self.ent_fecha_fin.get()}"
        else:
            periodo_analizado = f"Mes: {self.combo_mes.get()} - Año: {self.combo_anio.get()}"

        datos_reporte = {
            "Indicador Financiero": [
                "Ventas del Periodo (Neto)",
                "Cobrado en el Periodo",
                "Deuda Total Pendiente por Cobrar",
                "Compras del Periodo (Neto)",
                "Pagado en el Periodo",
                "Deuda Total a Proveedores",
                "Rentabilidad Global (Ventas - Compras)",
                "Flujo de Caja Real (Cobrado - Pagado)",
                f"Provisión Impuesto Renta Anual ({self.tasa_renta_anual}%)",
                f"Provisión ISR Mensual ({self.tasa_renta_mensual}%)"
            ],
            "Valor Registrado": [
                self.card_ventas.cget("text"),
                self.card_cobrado.cget("text"),
                self.card_por_cobrar.cget("text"),
                self.card_compras.cget("text"),
                self.card_pagado.cget("text"),
                self.card_por_pagar.cget("text"),
                self.card_rentabilidad.cget("text"),
                self.card_caja.cget("text"),
                self.card_provision_renta.cget("text"),
                self.card_provision_renta_mensual.cget("text")
            ]
        }

        ruta = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialfile=f"Reporte_Rentabilidad_{datetime.now().strftime('%Y%m%d')}.xlsx",
            title="Exportar Dashboard a Excel",
            filetypes=[("Archivos Excel", "*.xlsx")]
        )

        if ruta:
            try:
                df_filtros = pd.DataFrame({
                    "Indicador Financiero": ["Filtro de Cuenta/Banco:", "Filtro Vehículo/Cliente:", "Filtro de Proveedor:", "Periodo Analizado:", ""],
                    "Valor Registrado": [banco_analizado, cliente_analizado, proveedor_analizado, periodo_analizado, ""]
                })
                
                df_datos = pd.DataFrame(datos_reporte)
                df_final = pd.concat([df_filtros, df_datos], ignore_index=True)

                df_final.to_excel(ruta, index=False, engine='openpyxl')
                registrar_auditoria(self.usuario_activo, "Estadísticas Financieras", "Exportó el Dashboard Gerencial a Excel")
                messagebox.showinfo("Éxito", f"Reporte Gerencial exportado a:\n{ruta}")
                
                abrir_documento(ruta)
                
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo crear el archivo:\n{e}")

    def cargar_kpis(self):
        banco_seleccionado = self.combo_banco.get()
        cliente_seleccionado = self.combo_cliente.get()
        proveedor_seleccionado = self.combo_proveedor.get()
        
        modo_fecha = self.tipo_fecha_var.get()
        mes = self.combo_mes.get()
        anio = self.combo_anio.get()
        
        dt_ini = None
        dt_fin = None
        if modo_fecha == "Rango":
            dt_ini = self.convertir_a_fecha(self.ent_fecha_ini.get())
            dt_fin = self.convertir_a_fecha(self.ent_fecha_fin.get())
            if not dt_ini or not dt_fin:
                messagebox.showwarning("Fechas Inválidas", "Por favor, ingrese un rango de fechas válido.")
                return
            if dt_ini > dt_fin:
                dt_ini, dt_fin = dt_fin, dt_ini
                
        self.card_ventas.configure(text="Calculando...")
        self.card_compras.configure(text="Calculando...")
        self.card_rentabilidad.configure(text="Calculando...", text_color="gray")
        
        def tarea_kpis():
            conn = conectar_db(silencioso=True)
            if not conn: return
            
            try:
                cursor = conn.cursor()

                pagos_clientes_dict = {}
                try:
                    cursor.execute("SELECT id_factura, monto_pagado, fecha_pago, cuenta_destino FROM pagos_clientes")
                    for fk, m, f, cuenta_dest in cursor.fetchall():
                        if banco_seleccionado != "Todas las Cuentas" and str(cuenta_dest) != banco_seleccionado:
                            continue
                        pagos_clientes_dict.setdefault(fk, []).append((m, f))
                except Exception: conn.rollback()

                pagos_proveedores_dict = {}
                try:
                    cursor.execute("SELECT id_factura, monto_pagado, fecha_pago, cuenta_origen FROM pagos_comprobantes")
                    for fk, m, f, cuenta_ori in cursor.fetchall():
                        if banco_seleccionado != "Todas las Cuentas" and str(cuenta_ori) != banco_seleccionado:
                            continue
                        pagos_proveedores_dict.setdefault(fk, []).append((m, f))
                except Exception: conn.rollback()

                def fecha_en_periodo(f_str):
                    dt_f = self.convertir_a_fecha(f_str)
                    if not dt_f: return False 
                    
                    if modo_fecha == "Rango":
                        return dt_ini <= dt_f <= dt_fin
                    else: 
                        if str(dt_f.year) == anio:
                            if mes == "Todos los meses": return True
                            if f"{dt_f.month:02d}" == mes: return True
                        return False

                # --- ANÁLISIS DE VENTAS ---
                cursor.execute("SELECT id, tipo_documento, total, det_monto, fecha, cliente, estado_sunat FROM facturas_emitidas")
                facturas_ventas = cursor.fetchall()
                
                ventas_periodo = 0.0
                cobrado_periodo = 0.0
                por_cobrar_global = 0.0
                
                for v_id, v_tipo, v_tot, v_det, v_f, v_cliente, v_estado in facturas_ventas:
                    if v_estado and "Anulada" in str(v_estado):
                        continue
                        
                    if cliente_seleccionado != "Todos los Vehículos / Clientes" and str(v_cliente) != cliente_seleccionado:
                        continue

                    t = float(v_tot) if v_tot else 0.0
                    d = float(v_det) if v_det else 0.0
                    neto_venta = t - d
                    
                    if fecha_en_periodo(str(v_f)):
                        ventas_periodo += neto_venta

                    pagado_factura_global = 0.0
                    if v_id in pagos_clientes_dict:
                        for monto_pago, fecha_pago in pagos_clientes_dict[v_id]:
                            m = float(monto_pago) if monto_pago else 0.0
                            pagado_factura_global += m
                            
                            if fecha_en_periodo(str(fecha_pago)):
                                cobrado_periodo += m

                    saldo_pendiente = neto_venta - pagado_factura_global
                    if saldo_pendiente > 0.01:
                        por_cobrar_global += saldo_pendiente

                # --- ANÁLISIS DE COMPRAS ---
                cursor.execute("SELECT id, tipo_documento, total, impuesto, det_monto, fecha, proveedor FROM facturas_recibidas")
                facturas_compras = cursor.fetchall()
                
                compras_periodo = 0.0
                pagado_periodo = 0.0
                por_pagar_global = 0.0
                
                for c_id, c_tipo, c_tot, c_imp, c_det, c_f, c_proveedor in facturas_compras:
                    if proveedor_seleccionado != "Todos los Proveedores" and str(c_proveedor) != proveedor_seleccionado:
                        continue

                    tipo = str(c_tipo) if c_tipo else ""
                    t = float(c_tot) if c_tot else 0.0
                    i = float(c_imp) if c_imp else 0.0
                    d = float(c_det) if c_det else 0.0
                    
                    if "Recibo" in tipo and "8%" in tipo:
                        neto_compra = (t - i - d)
                    else:
                        neto_compra = (t - d)

                    if fecha_en_periodo(str(c_f)):
                        compras_periodo += neto_compra

                    pagado_factura_global = 0.0
                    if c_id in pagos_proveedores_dict:
                        for monto_pago, fecha_pago in pagos_proveedores_dict[c_id]:
                            m = float(monto_pago) if monto_pago else 0.0
                            pagado_factura_global += m
                            
                            if fecha_en_periodo(str(fecha_pago)):
                                pagado_periodo += m

                    saldo_pendiente = neto_compra - pagado_factura_global
                    if saldo_pendiente > 0.01:
                        por_pagar_global += saldo_pendiente

                rentabilidad = ventas_periodo - compras_periodo
                caja = cobrado_periodo - pagado_periodo
                
                provision_renta = 0.0
                if rentabilidad > 0:
                    provision_renta = rentabilidad * (self.tasa_renta_anual / 100.0)

                provision_renta_mensual = 0.0
                if rentabilidad > 0:
                    provision_renta_mensual = rentabilidad * (self.tasa_renta_mensual / 100.0)

                # Actualizar interfaz en el main thread
                def update_ui():
                    self.card_ventas.configure(text=formatear_moneda(ventas_periodo))
                    self.card_cobrado.configure(text=formatear_moneda(cobrado_periodo))
                    self.card_por_cobrar.configure(text=formatear_moneda(por_cobrar_global))

                    self.card_compras.configure(text=formatear_moneda(compras_periodo))
                    self.card_pagado.configure(text=formatear_moneda(pagado_periodo))
                    self.card_por_pagar.configure(text=formatear_moneda(por_pagar_global))
                    
                    self.card_rentabilidad.configure(text=formatear_moneda(rentabilidad))
                    self.card_caja.configure(text=formatear_moneda(caja))
                    self.card_provision_renta.configure(text=formatear_moneda(provision_renta))
                    self.card_provision_renta_mensual.configure(text=formatear_moneda(provision_renta_mensual))

                    if rentabilidad < 0:
                        self.card_rentabilidad.configure(text_color="#e74c3c")
                    else:
                        self.card_rentabilidad.configure(text_color="#1f538d")

                    if caja < 0:
                        self.card_caja.configure(text_color="#e74c3c")
                    else:
                        self.card_caja.configure(text_color="#27ae60")
                        
                self.parent_frame.after(0, update_ui)

            except Exception as e:
                self.parent_frame.after(0, lambda: messagebox.showerror("Error de Cálculo", f"No se pudo calcular las estadísticas:\n{e}"))
            finally:
                liberar_conexion(conn)

        threading.Thread(target=tarea_kpis, daemon=True).start()

if __name__ == "__main__":
    pass