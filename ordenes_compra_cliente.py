# -*- coding: utf-8 -*-

"""
=========================================================
ORDENES_COMPRA_CLIENTE.PY (ENTERPRISE EDITION)
=========================================================
- FIX: Auto-curación síncrona para evitar Race Conditions y Caché Fantasma.
- FIX: Carga 100% Asíncrona (Cero congelamientos).
- Paginación Lazy Loading (50 en 50) para la lista de órdenes.
- Caché Inteligente para el filtro de Cotizaciones.
- Uso del Pool de conexiones seguro (liberar_conexion).
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from datetime import datetime
import os
import sys
import shutil
import json
import re
import subprocess
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = "config_local.json"

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# Variable global definida al más alto nivel
_SCHEMA_OC_OK = False


# =========================================================
# MULTIPLATAFORMA: Función universal para abrir archivos
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


def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "ruta_drive": ""
    }
    try:
        if os.path.exists(RUTA_CONFIG):
            with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config


CONFIG_REGIONAL = cargar_configuracion_regional()


def formatear_moneda(valor):
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    try:
        valor = float(valor)
    except Exception:
        valor = 0.0
    return f"{simbolo} {valor:,.2f}"


class OrdenesCompraClienteApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo
        self.ruta_archivo_temp = ""
        
        # 🚀 VARIABLES DE PAGINACIÓN
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_bd()
        self.crear_interfaz()

    # =======================================================
    # SCHEMA: CREATE/ALTER SOLO LA PRIMERA VEZ (SÍNCRONO)
    # =======================================================
    def inicializar_bd(self):
        global _SCHEMA_OC_OK
        if _SCHEMA_OC_OK:
            return
            
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ordenes_compra_clientes (
                    id SERIAL PRIMARY KEY,
                    numero_oc VARCHAR(100),
                    cotizacion_asociada VARCHAR(255),
                    fecha VARCHAR(50),
                    cliente VARCHAR(255),
                    monto_total NUMERIC,
                    archivo_ruta TEXT
                )
            """)
            conn.commit()
            for sql in (
                "ALTER TABLE ordenes_compra_clientes ADD COLUMN IF NOT EXISTS cliente VARCHAR(255) DEFAULT ''",
                "ALTER TABLE ordenes_compra_clientes ADD COLUMN IF NOT EXISTS monto_total NUMERIC DEFAULT 0",
                "ALTER TABLE ordenes_compra_clientes ADD COLUMN IF NOT EXISTS descripcion TEXT DEFAULT ''",
                "ALTER TABLE ordenes_compra_clientes ADD COLUMN IF NOT EXISTS subtotal NUMERIC DEFAULT 0",
                "ALTER TABLE ordenes_compra_clientes ADD COLUMN IF NOT EXISTS igv NUMERIC DEFAULT 0",
            ):
                try:
                    cursor.execute(sql)
                    conn.commit()
                except Exception:
                    conn.rollback()
            _SCHEMA_OC_OK = True
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        header_frame = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 15))
        ctk.CTkLabel(header_frame, text="📥 RECEPCIÓN DE ÓRDENES DE COMPRA (CLIENTES)", font=("Arial", 18, "bold"), text_color="#27ae60").pack(side="left")
        frame_split = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        # =========================================
        # PANEL IZQUIERDO: FORMULARIO
        # =========================================
        f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=350, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_form.pack(side="left", fill="y", padx=(0, 15))
        self.btn_arch_cli = ctk.CTkButton(f_form, text="📄 Cargar y Escanear PDF", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.escanear_pdf)
        self.btn_arch_cli.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(f_form, text="1. Nº Orden de Compra:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_oc_cli = ctk.CTkEntry(f_form, placeholder_text="Ej: OC-998877")
        self.ent_oc_cli.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(f_form, text="2. Fecha de Emisión:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_fec_cli = ctk.CTkEntry(f_form)
        self.ent_fec_cli.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_fec_cli.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ctk.CTkLabel(f_form, text="3. Cotización Aprobada:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.cmb_cot_cli = ctk.CTkComboBox(f_form, state="readonly", command=self.al_seleccionar_cotizacion)
        self.cmb_cot_cli.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(f_form, text="4. Cliente (Autocompletado):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_cliente_cli = ctk.CTkEntry(f_form, state="disabled")
        self.ent_cliente_cli.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(f_form, text="5. Descripción / Nombre del Evento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_desc = ctk.CTkEntry(f_form, placeholder_text="Servicios solicitados según OC...")
        self.ent_desc.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(f_form, text="6. Subtotal (Sin IGV):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_subtotal = ctk.CTkEntry(f_form)
        self.ent_subtotal.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_subtotal.bind("<KeyRelease>", self.calcular_totales_math)
        ctk.CTkLabel(f_form, text="7. IGV (18%):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_igv = ctk.CTkEntry(f_form)
        self.ent_igv.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(f_form, text="8. Monto Total OC:", font=("Arial", 12, "bold"), text_color="#c0392b").pack(anchor="w", padx=15)
        self.ent_monto_cli = ctk.CTkEntry(f_form)
        self.ent_monto_cli.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkButton(f_form, text="💾 Archivar Orden Oficial", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=self.guardar_oc).pack(fill="x", padx=15, pady=20)

        # =========================================
        # PANEL DERECHO: TABLA DE ÓRDENES
        # =========================================
        f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        f_derecha.pack(side="right", fill="both", expand=True)
        f_busqueda = ctk.CTkFrame(f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_busc_cli = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por OC, cotización, cliente o descripción...")
        self.ent_busc_cli.pack(side="left", fill="x", expand=True)
        
        self.ent_busc_cli.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_busc_cli.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        style = ttk.Style()
        style.configure("Treeview", rowheight=28, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        columnas = ("id", "oc", "fecha", "cotizacion", "cliente", "desc", "subtotal", "igv", "monto", "arch", "ruta_real")
        self.tbl_cli = ttk.Treeview(f_derecha, columns=columnas, show="headings")
        self.tbl_cli.heading("oc", text="No OC")
        self.tbl_cli.heading("fecha", text="Fecha")
        self.tbl_cli.heading("cotizacion", text="Cotización")
        self.tbl_cli.heading("cliente", text="Cliente")
        self.tbl_cli.heading("desc", text="Descripción")
        self.tbl_cli.heading("subtotal", text="Subtotal")
        self.tbl_cli.heading("igv", text="IGV")
        self.tbl_cli.heading("monto", text="Total")
        self.tbl_cli.heading("arch", text="PDF")
        
        self.tbl_cli.column("id", width=0, stretch=tk.NO)
        self.tbl_cli.column("oc", width=100, anchor="center")
        self.tbl_cli.column("fecha", width=80, anchor="center")
        self.tbl_cli.column("cotizacion", width=100, anchor="center")
        self.tbl_cli.column("cliente", width=180, anchor="w")
        self.tbl_cli.column("desc", width=180, anchor="w")
        self.tbl_cli.column("subtotal", width=85, anchor="e")
        self.tbl_cli.column("igv", width=70, anchor="e")
        self.tbl_cli.column("monto", width=90, anchor="e")
        self.tbl_cli.column("arch", width=60, anchor="center")
        self.tbl_cli.column("ruta_real", width=0, stretch=tk.NO)
        
        self.tbl_cli["displaycolumns"] = ("oc", "fecha", "cotizacion", "cliente", "desc", "subtotal", "igv", "monto", "arch")
        self.tbl_cli.bind("<Double-1>", self.abrir_pdf_oc)
        
        scr_y = ttk.Scrollbar(f_derecha, orient="vertical", command=self.tbl_cli.yview)
        self.tbl_cli.configure(yscrollcommand=scr_y.set)
        self.tbl_cli.pack(side="left", fill="both", expand=True)
        scr_y.pack(side="right", fill="y")
        
        f_btn_del = ctk.CTkFrame(f_derecha, fg_color="transparent")
        f_btn_del.pack(fill="x", pady=(10, 0))
        
        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_btn_del, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        ctk.CTkButton(f_btn_del, text="🗑️ Eliminar Registro Seleccionado", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_oc).pack(side="right")

        self.cargar_cotizaciones_aprobadas()
        self.parent_frame.after(150, lambda: self.cargar_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_tabla()

    def buscar_con_retraso(self):
        if hasattr(self, "_busqueda_job"):
            try:
                self.parent_frame.after_cancel(self._busqueda_job)
            except Exception:
                pass
        self._busqueda_job = self.parent_frame.after(350, lambda: self.cargar_tabla(reset_pagina=True))

    # =======================================================
    # ESCÁNER DE PDF (IA DE EXTRACCIÓN) - SIN CAMBIOS
    # =======================================================
    def escanear_pdf(self):
        if pdfplumber is None:
            return messagebox.showerror("Librería Faltante", "El escáner requiere 'pdfplumber'. Instálalo ejecutando: pip install pdfplumber")
        ruta = filedialog.askopenfilename(title="Seleccionar PDF de la Orden de Compra", filetypes=[("PDF", "*.pdf")])
        if not ruta:
            return
        self.ruta_archivo_temp = ruta
        self.btn_arch_cli.configure(text="✅ PDF Escaneado (Memoria)", fg_color="#27ae60")
        try:
            texto = ""
            with pdfplumber.open(ruta) as pdf:
                for page in pdf.pages:
                    texto += page.extract_text() + "\n"
            if not texto.strip():
                return messagebox.showwarning("Aviso", "El PDF es una imagen o no contiene texto legible por la IA.")
            # 1. Extraer Número de OC
            m_oc = re.search(r"(?:ORDEN DE COMPRA|ORDEN DE SERVICIO|O/C|OC|PO|PURCHASE ORDER)[\sNo:#]+([A-Z0-9\-]+)", texto, re.IGNORECASE)
            if m_oc:
                self.ent_oc_cli.delete(0, tk.END)
                self.ent_oc_cli.insert(0, m_oc.group(1).strip())
            # 2. Extraer Fecha
            m_fec = re.search(r"(\d{2}[/\-.]\d{2}[/\-.]\d{4})", texto)
            if m_fec:
                val_f = m_fec.group(1).replace(".", "/").replace("-", "/")
                self.ent_fec_cli.delete(0, tk.END)
                self.ent_fec_cli.insert(0, val_f)
            # 3. Extraer Totales Financieros
            m_sub = re.search(r"(?:SUBTOTAL|SUB TOTAL|VALOR VENTA)[\s:S/$\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            m_igv = re.search(r"(?:IGV|I\.G\.V\.|IMPUESTO)[\s:S/$\|%]+([\d\,\.]+)", texto, re.IGNORECASE)
            m_tot = re.search(r"(?:TOTAL|MONTO TOTAL|IMPORTE TOTAL)[\s:S/$\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            sub_val = 0.0
            igv_val = 0.0
            tot_val = 0.0
            if m_sub:
                sub_val = float(m_sub.group(1).replace(",", ""))
            if m_igv:
                igv_val = float(m_igv.group(1).replace(",", ""))
            if m_tot:
                tot_val = float(m_tot.group(1).replace(",", ""))
            if tot_val > 0 and sub_val == 0.0:
                sub_val = tot_val / 1.18
                igv_val = tot_val - sub_val
            elif sub_val > 0 and tot_val == 0.0:
                igv_val = sub_val * 0.18
                tot_val = sub_val + igv_val
            if sub_val > 0:
                self.ent_subtotal.delete(0, tk.END)
                self.ent_subtotal.insert(0, f"{sub_val:.2f}")
                self.ent_igv.delete(0, tk.END)
                self.ent_igv.insert(0, f"{igv_val:.2f}")
                self.ent_monto_cli.delete(0, tk.END)
                self.ent_monto_cli.insert(0, f"{tot_val:.2f}")
            messagebox.showinfo("Escaneo Exitoso", "Extracción finalizada.\n\nEl sistema recuperó los datos legibles. Por favor, asigne manualmente la 'Cotización Aprobada' correspondiente para validar los datos financieros.")
        except Exception as e:
            messagebox.showerror("Error de Escaneo", f"Ocurrió un error al leer el PDF:\n{e}")

    def calcular_totales_math(self, event=None):
        try:
            sub = float(self.ent_subtotal.get().replace(',', '').strip() or 0)
            igv = sub * 0.18
            tot = sub + igv
            self.ent_igv.delete(0, tk.END)
            self.ent_igv.insert(0, f"{igv:.2f}")
            self.ent_monto_cli.delete(0, tk.END)
            self.ent_monto_cli.insert(0, f"{tot:.2f}")
        except Exception:
            pass

    # =======================================================
    # COMBO DE COTIZACIONES APROBADAS CON CACHÉ
    # =======================================================
    def cargar_cotizaciones_aprobadas(self):
        clave_cache = "lista_cotizaciones_aprobadas_combo"
        datos_cot = cache_sistema.obtener(clave_cache)
        
        if datos_cot is not None:
            self._aplicar_combo_cotizaciones(datos_cot)
        else:
            self.cmb_cot_cli.set("Cargando...")
            def tarea():
                lista = ["--- Seleccione ---"]
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        c = conn.cursor()
                        c.execute("SELECT cotizacion_asociada FROM ordenes_compra_clientes WHERE cotizacion_asociada IS NOT NULL")
                        ocs_asignadas = [str(r[0]).strip() for r in c.fetchall()]
                        
                        c.execute("SELECT * FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        filas = c.fetchall()
                        if filas:
                            columnas = [desc[0] for desc in c.description]
                            idx_cod = columnas.index('codigo_cotizacion') if 'codigo_cotizacion' in columnas else -1
                            idx_emp = columnas.index('nombre_empresa') if 'nombre_empresa' in columnas else -1
                            idx_tot = columnas.index('total') if 'total' in columnas else (columnas.index('monto_total') if 'monto_total' in columnas else -1)
                            
                            if idx_cod != -1:
                                for r in filas:
                                    cod_val = str(r[idx_cod]).strip()
                                    if cod_val in ocs_asignadas:
                                        continue
                                    cli_val = str(r[idx_emp]).strip() if idx_emp != -1 and r[idx_emp] else "Cliente Genérico"
                                    tot_val = str(r[idx_tot]).strip() if idx_tot != -1 and r[idx_tot] else "0.00"
                                    lista.append(f"{cod_val} | {cli_val} | {tot_val}")
                        
                        cache_sistema.guardar(clave_cache, lista)
                    except Exception as e:
                        print("Error cargando cotizaciones:", e)
                    finally:
                        liberar_conexion(conn)
                        
                self.parent_frame.after(0, lambda: self._aplicar_combo_cotizaciones(lista))

            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_combo_cotizaciones(self, lista):
        try:
            self.cmb_cot_cli.configure(values=lista)
            self.cmb_cot_cli.set(lista[0] if lista else "--- Seleccione ---")
        except Exception:
            pass

    def al_seleccionar_cotizacion(self, choice):
        if choice == "--- Seleccione ---":
            self.ent_cliente_cli.configure(state="normal")
            self.ent_cliente_cli.delete(0, tk.END)
            self.ent_cliente_cli.configure(state="disabled")
            self.ent_monto_cli.delete(0, tk.END)
            self.ent_subtotal.delete(0, tk.END)
            self.ent_igv.delete(0, tk.END)
            self.ent_desc.delete(0, tk.END)
            return
            
        partes = choice.split(" | ")
        if len(partes) >= 2:
            cod_cot = partes[0].strip()
            cli_val = partes[1].strip() if len(partes) > 1 else "Cliente Genérico"
            tot_val = partes[2].strip().replace(",", "") if len(partes) > 2 else "0.00"
            ev_val = f"Aprobación de la cotización {cod_cot}"
            
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("SELECT * FROM cotizaciones WHERE codigo_cotizacion = %s", (cod_cot,))
                    row = c.fetchone()
                    if row:
                        cols = [desc[0] for desc in c.description]
                        idx_ev = cols.index('nombre_evento') if 'nombre_evento' in cols else -1
                        if idx_ev != -1 and row[idx_ev]:
                            ev_val = str(row[idx_ev]).strip()
                except Exception:
                    pass
                finally:
                    liberar_conexion(conn)
                    
            self.ent_cliente_cli.configure(state="normal")
            self.ent_cliente_cli.delete(0, tk.END)
            self.ent_cliente_cli.insert(0, cli_val)
            self.ent_cliente_cli.configure(state="disabled")
            self.ent_desc.delete(0, tk.END)
            self.ent_desc.insert(0, ev_val)
            try:
                tot = float(tot_val)
                sub = tot / 1.18
                igv = tot - sub
                if not self.ent_monto_cli.get().strip():
                    self.ent_monto_cli.insert(0, f"{tot:.2f}")
                    self.ent_subtotal.insert(0, f"{sub:.2f}")
                    self.ent_igv.insert(0, f"{igv:.2f}")
            except Exception:
                pass

    # =======================================================
    # ARCHIVADO DE LA ORDEN (COPIA A DRIVE + BITÁCORA)
    # =======================================================
    def guardar_oc(self):
        oc = self.ent_oc_cli.get().strip()
        cot_str = self.cmb_cot_cli.get()
        fecha = self.ent_fec_cli.get().strip()
        cli = self.ent_cliente_cli.get().strip()
        desc = self.ent_desc.get().strip()
        if not oc:
            return messagebox.showwarning("Atención", "Debe ingresar el No de Orden de Compra.")
        if cot_str == "--- Seleccione ---":
            return messagebox.showwarning("Atención", "Debe seleccionar una cotización de la lista.")
        if not self.ruta_archivo_temp:
            if not messagebox.askyesno("PDF Faltante", "⚠️ No has cargado el PDF de la Orden de Compra.\n\n¿Deseas registrarla en el sistema de todas formas sin el documento físico?"):
                return
        cot_codigo = cot_str.split(" | ")[0].strip()
        try:
            subtotal = float(self.ent_subtotal.get().replace(',', '').strip() or 0)
            igv = float(self.ent_igv.get().replace(',', '').strip() or 0)
            monto = float(self.ent_monto_cli.get().replace(',', '').strip() or 0)
        except Exception:
            return messagebox.showerror("Error", "Los valores financieros deben ser números válidos.")
            
        ruta_final = ""
        if self.ruta_archivo_temp:
            ruta_base = str(CONFIG_REGIONAL.get("ruta_drive", "")).strip()
            if not ruta_base:
                return messagebox.showwarning("Configuración", "Debe configurar la ruta de Google Drive en los ajustes del sistema para guardar PDFs.")
            carpeta_dest = os.path.join(ruta_base, "ordenes_compra_recibidas")
            if not os.path.exists(carpeta_dest):
                os.makedirs(carpeta_dest)
            ext = os.path.splitext(self.ruta_archivo_temp)[1]
            ruta_final = os.path.join(carpeta_dest, f"OC_{oc}_{cot_codigo}{ext}")
            try:
                shutil.copy2(self.ruta_archivo_temp, ruta_final)
            except Exception as e:
                return messagebox.showerror("Error de copia", f"No se pudo guardar el PDF:\n{e}")
                
        conn = conectar_db()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO ordenes_compra_clientes (numero_oc, cotizacion_asociada, fecha, cliente, descripcion, subtotal, igv, monto_total, archivo_ruta)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (oc, cot_codigo, fecha, cli, desc, subtotal, igv, monto, ruta_final))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Ordenes de Compra Clientes", f"Recibió y archivó OC {oc} vinculada a {cot_codigo}")
            messagebox.showinfo("Éxito", "Orden de Compra archivada y lista para facturación.")
            
            self.ent_oc_cli.delete(0, tk.END)
            self.ent_desc.delete(0, tk.END)
            self.ent_subtotal.delete(0, tk.END)
            self.ent_igv.delete(0, tk.END)
            self.ent_monto_cli.delete(0, tk.END)
            self.btn_arch_cli.configure(text="📄 Cargar y Escanear PDF", fg_color="#1f538d")
            self.ruta_archivo_temp = ""
            
            self.cargar_cotizaciones_aprobadas()
            self.cargar_tabla(reset_pagina=True)
        except Exception as e:
            messagebox.showerror("Error BD", str(e))
        finally:
            liberar_conexion(conn)

    # =======================================================
    # TABLA DE ÓRDENES EN SEGUNDO PLANO (LAZY LOADING)
    # =======================================================
    def cargar_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tbl_cli.get_children(): 
            self.tbl_cli.delete(item)
            
        filtro = self.ent_busc_cli.get().strip().lower()
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"oc_clientes_evt_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos)
        else:
            self.tbl_cli.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", "", "", "", "", ""))
            
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        query_base = "SELECT id, numero_oc, fecha, cotizacion_asociada, cliente, descripcion, subtotal, igv, monto_total, archivo_ruta FROM ordenes_compra_clientes"
                        if filtro == "":
                            cursor.execute(f"{query_base} ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            cursor.execute(f"""
                                {query_base} 
                                WHERE numero_oc ILIKE %s OR cliente ILIKE %s OR cotizacion_asociada ILIKE %s OR descripcion ILIKE %s
                                ORDER BY id DESC LIMIT %s OFFSET %s
                            """, (val, val, val, val, self.registros_por_pagina, offset))
                        
                        rows = cursor.fetchall()
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception:
                        pass
                    finally:
                        liberar_conexion(conn)
                        
                self.parent_frame.after(0, lambda: self._pintar_tabla(rows))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_tabla(self, rows):
        for f in self.tbl_cli.get_children():
            self.tbl_cli.delete(f)
            
        for r in rows:
            arch = "✅ Ver PDF" if r[9] else "❌ Sin PDF"
            self.tbl_cli.insert("", tk.END, values=(r[0], r[1], r[2], r[3], r[4], r[5], formatear_moneda(r[6]), formatear_moneda(r[7]), formatear_moneda(r[8]), arch, r[9]))

        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")
            
        if len(rows) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")

    def abrir_pdf_oc(self, event):
        sel = self.tbl_cli.selection()
        if not sel:
            return
        ruta = self.tbl_cli.item(sel[0], "values")[10]
        if ruta and os.path.exists(ruta):
            abrir_documento(ruta)
        else:
            messagebox.showinfo("Aviso", "No hay un archivo PDF asociado a este registro o el archivo fue movido.")

    # =======================================================
    # ELIMINACIÓN SEGURA (BORRA TAMBIÉN EL PDF + BITÁCORA)
    # =======================================================
    def eliminar_oc(self):
        sel = self.tbl_cli.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione un registro para eliminar.")
            
        id_reg = self.tbl_cli.item(sel[0], "values")[0]
        ruta = self.tbl_cli.item(sel[0], "values")[10]
        num_oc = self.tbl_cli.item(sel[0], "values")[1]
        
        if messagebox.askyesno("Confirmar", f"¿Eliminar orden {num_oc} permanentemente?\n\nAl hacer esto, la cotización asociada volverá a aparecer como disponible en el desplegable."):
            if ruta and os.path.exists(ruta):
                try:
                    os.remove(ruta)
                except Exception:
                    pass
            conn = conectar_db()
            if conn:
                try:
                    conn.cursor().execute("DELETE FROM ordenes_compra_clientes WHERE id = %s", (id_reg,))
                    conn.commit()
                    cache_sistema.invalidar()
                    registrar_auditoria(self.usuario_activo, "Ordenes de Compra Clientes", f"Eliminó OC Recibida {num_oc}")
                except Exception:
                    pass
                finally:
                    liberar_conexion(conn)
                    
            self.cargar_tabla(reset_pagina=True)
            self.cargar_cotizaciones_aprobadas()


if __name__ == "__main__":
    pass