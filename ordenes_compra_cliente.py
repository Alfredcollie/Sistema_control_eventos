# -*- coding: utf-8 -*-

"""
=========================================================
ORDENES_COMPRA_CLIENTE.PY (ENTERPRISE TURBO EDITION + SMART OCR)
=========================================================
Optimizaciones y Capacidades:
1. 🧠 Lector híbrido: PDF digital (pdfplumber) + OCR del sistema para escaneados.
   Entiende el formato de NGR ("Pedido N°...", "SUB-TOTAL PEN", "IGV 18.00 %") y
   formatos genéricos (Folios, Tablas, Fechas en texto, IGV/IVA).
2. ⚡ Renderizado Instantáneo de UI (<50ms): Cero bloqueos visuales en __init__.
3. 🚀 Inicialización Asíncrona: Esquema BD e índices en hilo daemon.
4. 📂 I/O Asíncrono de Archivos: Verificación de PDF en hilo worker.
5. 💾 Caché Inteligente en Memoria: Acceso instantáneo a páginas.
6. ✏️ CRUD Completo: Edición y Eliminación con auditoría de base de datos.
7. 🌐 100% Cross-Platform (macOS Cocoa seguro con Queue, Windows y Linux).
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
from datetime import datetime
import os
from formato_numeros import formatear_numero
import sys
import shutil
import json
import re
import subprocess
import threading
import queue
import unicodedata

from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_local.json")

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

_SCHEMA_OC_OK = False
_CONFIG_REGIONAL_CACHE = None


def normalizar_ruta(ruta):
    """Resuelve tildes (~), variables de entorno y barras según el SO."""
    if not ruta:
        return ""
    ruta_limpia = str(ruta).strip().strip('"').strip("'")
    return os.path.abspath(os.path.expanduser(os.path.normpath(ruta_limpia)))


def abrir_documento(ruta, titulo="Documento"):
    """🖼️ Muestra el documento en el VISOR INTERNO del programa.

    Antes se abría con el programa externo (Acrobat, Fotos…) y la ventana
    quedaba muchas veces detrás del sistema. Ahora se ve dentro del programa.
    """
    try:
        from visor_documentos import ver_documento_ventana
        return ver_documento_ventana(ruta, titulo)
    except Exception as e_visor:
        print("Aviso - visor interno:", e_visor)
    return abrir_documento_externo(ruta)


def abrir_documento_externo(ruta):
    """Abre documentos de forma no bloqueante en macOS, Windows y Linux."""
    if not ruta:
        messagebox.showwarning("Aviso", "No se especificó la ruta del archivo.")
        return

    ruta_res = normalizar_ruta(ruta)
    if not os.path.exists(ruta_res):
        messagebox.showerror("Error", f"El archivo no existe en la ruta:\n{ruta_res}")
        return

    try:
        if sys.platform == "win32":
            os.startfile(ruta_res)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", ruta_res])
        else:
            subprocess.Popen(["xdg-open", ruta_res])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")


def detectar_ruta_google_drive_sugerida():
    """Detecta de forma no bloqueante la ruta de Google Drive."""
    try:
        if sys.platform == "darwin":
            cloud_storage = os.path.expanduser("~/Library/CloudStorage")
            if os.path.exists(cloud_storage):
                for carpeta in os.listdir(cloud_storage):
                    if "GoogleDrive" in carpeta:
                        cand = os.path.join(cloud_storage, carpeta, "Mi unidad")
                        if os.path.exists(cand):
                            return cand
                        return os.path.join(cloud_storage, carpeta)
            clasico = os.path.expanduser("~/Google Drive")
            if os.path.exists(clasico):
                return clasico
        elif sys.platform == "win32":
            for letra in ["G:", "H:", "D:", "E:"]:
                cand = os.path.join(letra + os.sep, "Mi unidad")
                if os.path.exists(cand):
                    return cand
            clasico = os.path.expanduser("~/Google Drive")
            if os.path.exists(clasico):
                return clasico
    except Exception:
        pass
    return ""


def cargar_configuracion_regional():
    global _CONFIG_REGIONAL_CACHE
    if _CONFIG_REGIONAL_CACHE is not None:
        return _CONFIG_REGIONAL_CACHE
        
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "ruta_drive": ""
    }
    try:
        ruta_cfg = normalizar_ruta(RUTA_CONFIG)
        if os.path.exists(ruta_cfg):
            with open(ruta_cfg, "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
        
    if not config.get("ruta_drive"):
        config["ruta_drive"] = detectar_ruta_google_drive_sugerida()
        
    _CONFIG_REGIONAL_CACHE = config
    return _CONFIG_REGIONAL_CACHE


def parsear_numero_seguro(valor_str):
    """Parsea importes numéricos soportando formatos con comas y puntos."""
    if valor_str is None:
        return 0.0
    if isinstance(valor_str, (int, float)):
        return float(valor_str)
    limpio = str(valor_str).replace('S/.', '').replace('$', '').replace(' ', '').strip()
    if not limpio:
        return 0.0
    if ',' in limpio and '.' in limpio:
        limpio = limpio.replace(',', '')
    elif ',' in limpio and '.' not in limpio:
        limpio = limpio.replace(',', '.')
    try:
        return float(limpio)
    except Exception:
        return 0.0


def formatear_moneda(valor):
    simbolo = cargar_configuracion_regional().get("simbolo_moneda", "S/.")
    valor_float = parsear_numero_seguro(valor)
    return f"{simbolo} {formatear_numero(valor_float)}"


def _texto_busqueda(texto):
    """Minúsculas y sin tildes, para comparar nombres de clientes/eventos."""
    normal = unicodedata.normalize("NFD", str(texto or ""))
    limpio = "".join(c for c in normal if unicodedata.category(c) != "Mn")
    return limpio.lower().strip()

FEE_PRODUCCION_PCT = 15.0


def obtener_porcentaje_igv():
    """Devuelve el % de IGV configurado en Ajustes (Configuración), 18% por defecto."""
    try:
        valor = cargar_configuracion_regional().get("igv_porcentaje", 18)
        if valor in (None, ""):
            return 18.0
        return float(valor)
    except (TypeError, ValueError):
        return 18.0
    except Exception:
        return 18.0


def calcular_totales_cotizacion(venta, sin_fee=False, fin_pct=0.0, igv_pct=None):
    """
    Replica exactamente la fórmula de Cotizaciones:
        Fee 15%        = Venta x 15%   (0 si la cotización está exonerada del Fee)
        Total sin IGV  = Venta + Fee
        IGV            = Total sin IGV x %IGV
        Total con IGV  = Total sin IGV + IGV
        Financiamiento = Total con IGV x %Financiamiento
        Total final    = Total con IGV + Financiamiento   (lo que paga el cliente)
    """
    try:
        venta = float(venta or 0.0)
    except (TypeError, ValueError):
        venta = 0.0
    try:
        fin_pct = max(0.0, float(fin_pct or 0.0))
    except (TypeError, ValueError):
        fin_pct = 0.0

    igv_pct = obtener_porcentaje_igv() if igv_pct is None else float(igv_pct)

    fee = 0.0 if sin_fee else venta * (FEE_PRODUCCION_PCT / 100.0)
    sin_igv = venta + fee
    igv = sin_igv * igv_pct / 100.0
    con_igv = sin_igv + igv
    financiamiento = con_igv * fin_pct / 100.0 if fin_pct > 0 else 0.0

    return {
        "venta": venta,
        "fee": fee,
        "sin_fee": bool(sin_fee),
        "sin_igv": sin_igv,
        "igv_pct": igv_pct,
        "igv": igv,
        "con_igv": con_igv,
        "fin_pct": fin_pct,
        "financiamiento": financiamiento,
        "total": con_igv + financiamiento,
    }



class OrdenesCompraClienteApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo
        self.ruta_archivo_temp = ""
        self._busqueda_job = None
        self._esta_destruido = False
        self._escaneando = False

        # Estado del escaneo con la multifuncional (páginas y PDF temporal)
        self._escaneo_paginas = []
        self._escaneo_pdf = ""
        self._escaneo_config = {}
        self._escaneo_datos = {}
        self._ventana_preview = None

        # Estado de Edición
        self.id_oc_en_edicion = None
        self.ruta_archivo_actual_en_edicion = ""

        # Datos de las cotizaciones aprobadas: venta, Fee 15%, IGV y financiamiento
        self._datos_cotizaciones = {}
        
        # Cola de eventos UI Thread-safe
        self.ui_queue = queue.Queue()
        
        # Paginación
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        # 1. Dibujar UI de inmediato
        self.crear_interfaz()
        self._iniciar_procesador_cola_ui()
        
        # 2. Inicialización en segundo plano
        threading.Thread(target=self._inicializar_bd_async, daemon=True).start()
        
        try:
            self.parent_frame.bind("<Destroy>", self._al_destruir, add="+")
        except Exception:
            pass

    def _al_destruir(self, event=None):
        self._esta_destruido = True
        if self._busqueda_job:
            try:
                self.parent_frame.after_cancel(self._busqueda_job)
            except Exception:
                pass

    def _iniciar_procesador_cola_ui(self):
        if self._esta_destruido:
            return
        try:
            while not self.ui_queue.empty():
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as ex:
                    print(f"[UI Queue Error] {ex}")
        except Exception:
            pass
        finally:
            if not self._esta_destruido and self.parent_frame.winfo_exists():
                self.parent_frame.after(40, self._iniciar_procesador_cola_ui)

    def ejecutar_en_ui(self, fn, *args):
        self.ui_queue.put((fn, args))

    def _inicializar_bd_async(self):
        global _SCHEMA_OC_OK
        if _SCHEMA_OC_OK:
            self.cargar_cotizaciones_aprobadas()
            self.cargar_tabla(reset_pagina=True)
            return
            
        conn = conectar_db(silencioso=True)
        if not conn:
            self.cargar_cotizaciones_aprobadas()
            self.cargar_tabla(reset_pagina=True)
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
                    descripcion TEXT DEFAULT '',
                    subtotal NUMERIC DEFAULT 0,
                    igv NUMERIC DEFAULT 0,
                    monto_total NUMERIC DEFAULT 0,
                    archivo_ruta TEXT
                )
            """)
            conn.commit()
            
            for idx_sql in (
                "CREATE INDEX IF NOT EXISTS idx_oc_clientes_cot ON ordenes_compra_clientes(cotizacion_asociada)",
                "CREATE INDEX IF NOT EXISTS idx_oc_clientes_id_desc ON ordenes_compra_clientes(id DESC)",
                "CREATE INDEX IF NOT EXISTS idx_oc_clientes_num ON ordenes_compra_clientes(numero_oc)",
            ):
                try:
                    cursor.execute(idx_sql)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    
            _SCHEMA_OC_OK = True
        except Exception as e:
            print(f"[Schema Init Warning] {e}")
        finally:
            liberar_conexion(conn)
            
        self.cargar_cotizaciones_aprobadas()
        self.cargar_tabla(reset_pagina=True)

    def crear_interfaz(self):
        familia_fuente = "Helvetica" if sys.platform == "darwin" else "Segoe UI"
        
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        
        header_frame = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(
            header_frame, 
            text="📥 GESTIÓN DE ÓRDENES DE COMPRA (CLIENTES)", 
            font=ctk.CTkFont(family=familia_fuente, size=17, weight="bold"), 
            text_color="#27ae60"
        ).pack(side="left")
        
        frame_split = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        # PANEL IZQUIERDO: FORMULARIO
        f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=360, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_form.pack(side="left", fill="y", padx=(0, 15))
        
        self.lbl_modo = ctk.CTkLabel(
            f_form, 
            text="📝 NUEVO REGISTRO DE ORDEN", 
            font=ctk.CTkFont(family=familia_fuente, size=12, weight="bold"),
            text_color="#1f538d",
            fg_color="#e8f0fe",
            corner_radius=6,
            height=28
        )
        self.lbl_modo.pack(fill="x", padx=15, pady=(15, 10))

        self.btn_arch_cli = ctk.CTkButton(
            f_form, 
            text="📄 Escanear OC (PDF o imagen)",
            font=ctk.CTkFont(family=familia_fuente, size=12, weight="bold"), 
            fg_color="#1f538d", 
            hover_color="#163b65", 
            command=self.escanear_pdf
        )
        self.btn_arch_cli.pack(fill="x", padx=15, pady=(0, 6))

        self.btn_escaner_multi = ctk.CTkButton(
            f_form,
            text="🖨️ Escanear con la Multifuncional",
            font=ctk.CTkFont(family=familia_fuente, size=12, weight="bold"),
            fg_color="#8e44ad",
            hover_color="#6c3483",
            command=self.escanear_con_multifuncional
        )
        self.btn_escaner_multi.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(f_form, text="1. Nº Orden de Compra / Folio:", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_oc_cli = ctk.CTkEntry(f_form, placeholder_text="Ej: 012898 u OC-998877")
        self.ent_oc_cli.pack(fill="x", padx=15, pady=(0, 10))
        
        ctk.CTkLabel(f_form, text="2. Fecha de Emisión:", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_fec_cli = ctk.CTkEntry(f_form)
        self.ent_fec_cli.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_fec_cli.insert(0, datetime.now().strftime("%d/%m/%Y"))
        
        ctk.CTkLabel(f_form, text="3. Cotización Aprobada:", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.cmb_cot_cli = ctk.CTkComboBox(f_form, state="readonly", command=self.al_seleccionar_cotizacion)
        self.cmb_cot_cli.pack(fill="x", padx=15, pady=(0, 10))
        self.cmb_cot_cli.set("Cargando cotizaciones...")
        
        ctk.CTkLabel(f_form, text="4. Cliente (Autocompletado):", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_cliente_cli = ctk.CTkEntry(f_form, state="disabled")
        self.ent_cliente_cli.pack(fill="x", padx=15, pady=(0, 10))
        
        ctk.CTkLabel(f_form, text="5. Descripción / Nombre del Evento:", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_desc = ctk.CTkEntry(f_form, placeholder_text="Servicios solicitados según OC...")
        self.ent_desc.pack(fill="x", padx=15, pady=(0, 10))
        
        ctk.CTkLabel(f_form, text="6. Subtotal sin IGV (Venta + Fee 15%):", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_subtotal = ctk.CTkEntry(f_form)
        self.ent_subtotal.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_subtotal.bind("<KeyRelease>", self.calcular_totales_math)
        
        ctk.CTkLabel(f_form, text="7. IGV (18%):", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(anchor="w", padx=15)
        self.ent_igv = ctk.CTkEntry(f_form)
        self.ent_igv.pack(fill="x", padx=15, pady=(0, 10))
        
        ctk.CTkLabel(f_form, text="8. Monto Total OC:", font=ctk.CTkFont(family=familia_fuente, size=12, weight="bold"), text_color="#c0392b").pack(anchor="w", padx=15)
        self.ent_monto_cli = ctk.CTkEntry(f_form)
        self.ent_monto_cli.pack(fill="x", padx=15, pady=(0, 10))

        self.lbl_desglose = ctk.CTkLabel(
            f_form,
            text="",
            font=ctk.CTkFont(family=familia_fuente, size=10),
            text_color="#1f538d",
            justify="left",
            anchor="w",
            wraplength=310
        )
        self.lbl_desglose.pack(fill="x", padx=15, pady=(0, 8))
        
        self.btn_guardar_oc = ctk.CTkButton(
            f_form, 
            text="💾 Archivar Orden Oficial", 
            font=ctk.CTkFont(family=familia_fuente, size=12, weight="bold"), 
            fg_color="#27ae60", 
            hover_color="#1e8449", 
            command=self.procesar_guardar_o_actualizar
        )
        self.btn_guardar_oc.pack(fill="x", padx=15, pady=(15, 5))
        
        self.btn_cancelar_edicion = ctk.CTkButton(
            f_form,
            text="❌ Cancelar Edición",
            font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold"),
            fg_color="#7f8c8d",
            hover_color="#636e72",
            command=self.cancelar_edicion
        )

        # PANEL DERECHO: TABLA Y BOTONES CRUD
        f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        f_derecha.pack(side="right", fill="both", expand=True)
        
        f_busqueda = ctk.CTkFrame(f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold")).pack(side="left", padx=(0, 5))
        self.ent_busc_cli = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por OC, cotización, cliente o descripción...")
        self.ent_busc_cli.pack(side="left", fill="x", expand=True)
        
        self.ent_busc_cli.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_busc_cli.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))
        self.ent_busc_cli.bind("<KP_Enter>", lambda e: self.cargar_tabla(reset_pagina=True))

        style = ttk.Style()
        if sys.platform == "darwin":
            style.theme_use("clam")
            
        style.configure("Treeview", rowheight=28, font=(familia_fuente, 10))
        style.configure("Treeview.Heading", font=(familia_fuente, 10, "bold"))
        
        columnas = ("id", "oc", "fecha", "cotizacion", "cliente", "desc", "subtotal", "igv", "monto", "arch", "ruta_real")
        self.tbl_cli = ttk.Treeview(f_derecha, columns=columnas, show="headings", selectmode="browse")
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
        self.tbl_cli.column("arch", width=65, anchor="center")
        self.tbl_cli.column("ruta_real", width=0, stretch=tk.NO)
        
        self.tbl_cli["displaycolumns"] = ("oc", "fecha", "cotizacion", "cliente", "desc", "subtotal", "igv", "monto", "arch")
        
        self.tbl_cli.bind("<Double-Button-1>", self.abrir_pdf_oc)
        self.tbl_cli.bind("<Double-1>", self.abrir_pdf_oc)
        
        self.menu_contextual = tk.Menu(self.parent_frame, tearoff=0)
        self.menu_contextual.add_command(label="📄 Abrir Documento PDF", command=self.abrir_pdf_oc)
        self.menu_contextual.add_command(label="✏️ Editar Orden Seleccionada", command=self.cargar_oc_para_editar)
        self.menu_contextual.add_separator()
        self.menu_contextual.add_command(label="🗑️ Eliminar Registro", command=self.eliminar_oc)
        
        self.tbl_cli.bind("<Button-3>", self._mostrar_menu_contextual)
        if sys.platform == "darwin":
            self.tbl_cli.bind("<Button-2>", self._mostrar_menu_contextual)
            self.tbl_cli.bind("<Control-Button-1>", self._mostrar_menu_contextual)
        
        scr_y = ttk.Scrollbar(f_derecha, orient="vertical", command=self.tbl_cli.yview)
        self.tbl_cli.configure(yscrollcommand=scr_y.set)
        self.tbl_cli.pack(side="left", fill="both", expand=True)
        scr_y.pack(side="right", fill="y")
        
        self.tbl_cli.insert("", tk.END, values=("", "Cargando...", "", "", "Conectando con base de datos...", "", "", "", "", "", ""))
        
        # BARRA INFERIOR
        f_acciones_inferiores = ctk.CTkFrame(f_derecha, fg_color="transparent")
        f_acciones_inferiores.pack(fill="x", pady=(10, 0))
        
        f_paginacion = ctk.CTkFrame(f_acciones_inferiores, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        f_botones_crud = ctk.CTkFrame(f_acciones_inferiores, fg_color="transparent")
        f_botones_crud.pack(side="right")
        
        self.btn_ver_pdf = ctk.CTkButton(
            f_botones_crud, 
            text="📄 Ver PDF", 
            font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold"), 
            fg_color="#1f538d", 
            hover_color="#163b65", 
            width=90,
            command=self.abrir_pdf_oc
        )
        self.btn_ver_pdf.pack(side="left", padx=3)
        
        self.btn_editar = ctk.CTkButton(
            f_botones_crud, 
            text="✏️ Editar Orden", 
            font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold"), 
            fg_color="#d35400", 
            hover_color="#a04000", 
            width=110,
            command=self.cargar_oc_para_editar
        )
        self.btn_editar.pack(side="left", padx=3)
        
        self.btn_eliminar = ctk.CTkButton(
            f_botones_crud, 
            text="🗑️ Eliminar Registro", 
            font=ctk.CTkFont(family=familia_fuente, size=11, weight="bold"), 
            fg_color="#e74c3c", 
            hover_color="#c0392b", 
            width=130,
            command=self.eliminar_oc
        )
        self.btn_eliminar.pack(side="left", padx=3)

    def _mostrar_menu_contextual(self, event):
        item = self.tbl_cli.identify_row(event.y)
        if item:
            self.tbl_cli.selection_set(item)
            try:
                self.menu_contextual.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu_contextual.grab_release()

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_tabla()

    def buscar_con_retraso(self):
        if self._busqueda_job:
            try:
                self.parent_frame.after_cancel(self._busqueda_job)
            except Exception:
                pass
        self._busqueda_job = self.parent_frame.after(250, lambda: self.cargar_tabla(reset_pagina=True))

    # =======================================================
    # 📷 ESCANEO DE ÓRDENES DE COMPRA (PDF DIGITAL + OCR)
    # =======================================================
    def escanear_pdf(self):
        """Elige un PDF/imagen y lo lee en segundo plano, sin congelar la UI."""
        if getattr(self, "_escaneando", False):
            return

        try:
            ventana_top = self.parent_frame.winfo_toplevel()
        except Exception:
            ventana_top = None

        ruta = filedialog.askopenfilename(
            parent=ventana_top,
            title="Seleccionar la Orden de Compra (PDF o imagen)",
            filetypes=[
                ("Órdenes de compra", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                ("Archivos PDF", "*.pdf"),
                ("Imágenes", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                ("Todos los archivos", "*.*"),
            ],
        )
        if not ruta:
            return

        self.ruta_archivo_temp = normalizar_ruta(ruta)
        # Un archivo elegido a mano no tiene páginas de escáner para agregar.
        self._escaneo_paginas = []
        self._escaneo_pdf = ""
        self._escaneando = True
        try:
            self.btn_arch_cli.configure(
                text="⏳ Leyendo documento...", state="disabled", fg_color="#7f8c8d"
            )
            self.btn_escaner_multi.configure(state="disabled")
        except Exception:
            pass

        ruta_para_leer = self.ruta_archivo_temp

        def tarea():
            try:
                from lector_orden_compra import leer_orden_compra
                resultado = leer_orden_compra(
                    ruta_para_leer,
                    idioma="Automático",
                    igv_pct=obtener_porcentaje_igv(),
                )
            except Exception as e:
                resultado = {
                    "ok": False, "error": str(e), "datos": {},
                    "texto": "", "motor": "", "paginas": 0,
                }
            self.ejecutar_en_ui(self._finalizar_escaneo, resultado)

        threading.Thread(target=tarea, daemon=True).start()

    def _finalizar_escaneo(self, resultado):
        """Habilita el botón otra vez y muestra el resultado (siempre en el hilo UI)."""
        if self._esta_destruido:
            return
        self._habilitar_botones_escaneo()

        if not resultado or not resultado.get("ok"):
            motivo = (resultado or {}).get("error") or "No se pudo leer el documento."
            try:
                self.btn_arch_cli.configure(
                    text="⚠️ Documento adjuntado (sin lectura)", fg_color="#d35400"
                )
            except Exception:
                pass
            tiene_pdf = bool(
                resultado
                and resultado.get("ruta_archivo")
                and isinstance(resultado.get("paginas"), (list, tuple))
                and resultado.get("paginas")
            )
            if tiene_pdf:
                messagebox.showwarning(
                    "Lectura incompleta",
                    "El documento quedó adjuntado, pero no se pudieron reconocer todos sus datos.\n\n"
                    f"Detalle: {motivo}\n\n"
                    "Puede agregar otra página o completar los datos a mano."
                )
                self._mostrar_resultado_escaneo(resultado)
                return
            messagebox.showwarning(
                "No se pudo leer la OC",
                "El documento quedó adjuntado, pero no se pudieron reconocer sus datos.\n\n"
                f"Detalle: {motivo}\n\n"
                "Puede completar los datos a mano o probar con un PDF de mejor calidad."
            )
            return

        try:
            self.btn_arch_cli.configure(
                text="✅ Documento leído (en memoria)", fg_color="#27ae60"
            )
        except Exception:
            pass
        self._mostrar_resultado_escaneo(resultado)

    def _habilitar_botones_escaneo(self):
        """Vuelve a dejar listos los dos botones de escaneo."""
        self._escaneando = False
        try:
            self.btn_arch_cli.configure(state="normal")
        except Exception:
            pass
        try:
            self.btn_escaner_multi.configure(
                state="normal", text="🖨️ Escanear con la Multifuncional"
            )
        except Exception:
            pass

    def _traer_al_frente(self):
        """Devuelve el foco a la ventana del programa después del escaneo."""
        try:
            ventana = self.parent_frame.winfo_toplevel()
            ventana.deiconify()
            ventana.lift()
            ventana.focus_force()
        except Exception:
            pass

    def _cerrar_progreso_escaneo(self, v_prog):
        try:
            if v_prog is not None and v_prog.winfo_exists():
                v_prog.grab_release()
        except Exception:
            pass
        try:
            if v_prog is not None and v_prog.winfo_exists():
                v_prog.destroy()
        except Exception:
            pass

    def _mostrar_progreso_escaneo(self, titulo, instruccion):
        """Ventanita modal con barra mientras la multifuncional trabaja."""
        familia = "Helvetica" if sys.platform == "darwin" else "Segoe UI"
        try:
            v_prog = ctk.CTkToplevel(self.parent_frame)
            v_prog.title(titulo)
            v_prog.geometry("480x200")
            v_prog.minsize(430, 180)
            try:
                v_prog.transient(self.parent_frame.winfo_toplevel())
            except Exception:
                pass
            try:
                v_prog.grab_set()
            except Exception:
                pass

            def _soltar(evento=None):
                try:
                    if evento is None or getattr(evento, "widget", None) is v_prog:
                        v_prog.grab_release()
                except Exception:
                    pass

            try:
                v_prog.bind("<Destroy>", _soltar, add="+")
                v_prog.bind("<Unmap>", _soltar, add="+")
            except Exception:
                pass

            ctk.CTkLabel(
                v_prog, text="🖨️ Escaneando la Orden de Compra...",
                font=ctk.CTkFont(family=familia, size=14, weight="bold"),
                text_color="#1f538d",
            ).pack(pady=(18, 4))
            ctk.CTkLabel(
                v_prog, text=instruccion,
                font=ctk.CTkFont(family=familia, size=10),
                text_color="#7f8c8d", justify="center", wraplength=420,
            ).pack(pady=(0, 10))
            barra = ctk.CTkProgressBar(v_prog, width=380, mode="indeterminate")
            barra.pack(pady=4)
            barra.start()
            ctk.CTkLabel(
                v_prog, text="Esto puede tardar unos segundos.",
                font=ctk.CTkFont(family=familia, size=9),
                text_color="#7f8c8d",
            ).pack(pady=(4, 0))
            self._centrar_ventana(v_prog, 480, 200)
            try:
                v_prog.lift()
                v_prog.focus_force()
            except Exception:
                pass
            return v_prog
        except Exception as e:
            print("[Escáner OC] No se pudo mostrar el progreso:", e)
            return None

    def _carpeta_escaneos_oc(self, config):
        """Carpeta donde se guardan los escaneos de las órdenes de compra."""
        base = str((config or {}).get("escaner_carpeta") or "").strip()
        if not base:
            base = str((config or {}).get("ruta_drive") or "").strip()
        if not base:
            try:
                from app_paths import CONFIG_DIR
                base = str(CONFIG_DIR)
            except Exception:
                base = os.path.join(os.path.expanduser("~"), "BlackCube_Seguro")
        destino = os.path.join(base, "ordenes_compra_recibidas", "escaneos")
        try:
            os.makedirs(destino, exist_ok=True)
        except Exception:
            import tempfile
            destino = tempfile.mkdtemp(prefix="oc_escaneos_")
        return destino

    def _resultado_escaneo_oc(self, paginas, pdf, texto, motor, aviso=""):
        """Arma el resultado que entiende la vista previa del escaneo."""
        from lector_orden_compra import parsear_orden_compra
        datos_oc = parsear_orden_compra(texto or "", [], obtener_porcentaje_igv()) if texto else {}
        relevante = any(
            datos_oc.get(k) for k in ("numero_oc", "fecha", "cliente", "descripcion", "total")
        )
        return {
            "ok": bool(relevante),
            "error": "" if relevante else (
                "El OCR no reconoció los datos de la OC."
                if texto else "El OCR no reconoció texto en el escaneo."
            ),
            "datos": datos_oc,
            "texto": texto or "",
            "motor": motor or "Escáner",
            "paginas": list(paginas or []),
            "ruta_archivo": pdf,
            "aviso": aviso or "",
        }

    def escanear_con_multifuncional(self):
        """Escanea la OC con la multifuncional y la lee con OCR."""
        if getattr(self, "_escaneando", False):
            return
        try:
            import escaner
        except Exception as e:
            return messagebox.showerror(
                "Escáner", f"No se pudo cargar el módulo de escaneo:\n{e}"
            )
        try:
            if not escaner.sistema_soportado():
                return messagebox.showinfo(
                    "Escáner",
                    "El escaneo con la multifuncional está disponible en Windows y macOS (M1/M2).",
                )
        except Exception:
            pass

        try:
            try:
                from app_paths import cargar_config_local
                config = cargar_config_local()
            except Exception:
                config = cargar_configuracion_regional()
            datos = escaner.config_escaner(config)
        except Exception as e:
            return messagebox.showerror(
                "Escáner", f"No se pudo leer la configuración del escáner:\n{e}"
            )

        modo = str(datos.get("escaner_modo", "Automático (sin ventanas)"))
        if modo.lower().startswith("con la ventana"):
            instruccion = "Se abrirá el cuadro de escaneo del sistema."
        else:
            instruccion = (
                f"Coloque la OC en el escáner (cristal) — "
                f"{datos.get('escaner_dpi', '300')} ppp, "
                f"{datos.get('escaner_color', 'Escala de grises')}.\n"
                f"Páginas a escanear: {datos.get('escaner_paginas', '1')}."
            )
            if not datos.get("escaner_dispositivo"):
                instruccion += (
                    "\n(Si hay varios escáneres, elíjalo en Ajustes → "
                    "Configuración General → Escáner.)"
                )

        self._escaneando = True
        self._escaneo_config = config
        self._escaneo_paginas = []
        self._escaneo_pdf = ""
        self._escaneo_datos = {}
        try:
            self.btn_arch_cli.configure(state="disabled")
            self.btn_escaner_multi.configure(state="disabled", text="⏳ Escaneando...")
        except Exception:
            pass

        v_prog = self._mostrar_progreso_escaneo("Escaneando Orden de Compra", instruccion)
        threading.Thread(
            target=self._worker_escanear_multifuncional,
            args=(config, v_prog, datos),
            daemon=True,
        ).start()

    def _worker_escanear_multifuncional(self, config, v_prog, datos):
        """Escanea, arma el PDF, hace el OCR y devuelve los datos leídos."""
        try:
            import escaner
            carpeta = self._carpeta_escaneos_oc(config)
            try:
                paginas_pedidas = max(1, int(str(datos.get("escaner_paginas") or 1)))
            except Exception:
                paginas_pedidas = 1
            escaneo = escaner.escanear(
                carpeta,
                dispositivo=datos.get("escaner_dispositivo", ""),
                dpi=datos.get("escaner_dpi", "300"),
                color=datos.get("escaner_color", "Escala de grises"),
                origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                paginas=paginas_pedidas,
                optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                modo=datos.get("escaner_modo", "Automático (sin ventanas)"),
            )
            if not escaneo.get("ok"):
                self.ejecutar_en_ui(
                    self._finalizar_escaneo_multifuncional,
                    {"ok": False, "error": escaneo.get("error") or "No se pudo escanear."},
                    v_prog,
                )
                return

            paginas = [str(p) for p in (escaneo.get("paginas") or []) if os.path.exists(str(p))]
            if not paginas:
                self.ejecutar_en_ui(
                    self._finalizar_escaneo_multifuncional,
                    {"ok": False, "error": "El escáner no devolvió ninguna imagen."},
                    v_prog,
                )
                return

            marca = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf = os.path.join(carpeta, f"OC_Escaneada_{marca}.pdf")
            try:
                escaner.imagenes_a_pdf(paginas, pdf)
            except Exception as e_pdf:
                print("[Escáner OC] Aviso - no se pudo armar el PDF:", e_pdf)
                pdf = paginas[0]

            texto, motor = "", "Escáner"
            if str(datos.get("escaner_ocr", "Sí")).lower().startswith("s"):
                try:
                    ocr = escaner.ocr_paginas(paginas, datos.get("escaner_idioma_ocr", "Automático"))
                    texto = escaner.texto_de_resultados(ocr) or ""
                    motor = str(ocr.get("motor") or "OCR")
                except Exception as e_ocr:
                    print("[Escáner OC] Aviso - OCR:", e_ocr)

            resultado = self._resultado_escaneo_oc(
                paginas, pdf, texto, motor, escaneo.get("aviso", "")
            )
            self.ejecutar_en_ui(self._finalizar_escaneo_multifuncional, resultado, v_prog)
        except Exception as e:
            self.ejecutar_en_ui(
                self._finalizar_escaneo_multifuncional,
                {"ok": False, "error": str(e)},
                v_prog,
            )

    def _finalizar_escaneo_multifuncional(self, resultado, v_prog):
        """Cierra el progreso y aplica/muestra lo escaneado (hilo UI)."""
        self._cerrar_progreso_escaneo(v_prog)
        if self._esta_destruido:
            return
        self._traer_al_frente()
        resultado = resultado or {}
        paginas = []
        if isinstance(resultado.get("paginas"), (list, tuple)):
            paginas = [str(p) for p in resultado.get("paginas") if os.path.exists(str(p))]
        ruta = str(resultado.get("ruta_archivo") or "")
        if not paginas or not ruta or not os.path.exists(ruta):
            self._habilitar_botones_escaneo()
            try:
                self.btn_arch_cli.configure(
                    text="📄 Escanear OC (PDF o imagen)", fg_color="#1f538d"
                )
            except Exception:
                pass
            messagebox.showerror(
                "Escáner",
                "No se pudo escanear la Orden de Compra.\n\n"
                + str(resultado.get("error") or
                      "Revise que la multifuncional esté encendida y conectada."),
            )
            return
        self._escaneo_paginas = paginas
        self._escaneo_pdf = ruta
        self._escaneo_datos = resultado.get("datos") or {}
        self.ruta_archivo_temp = ruta
        self._finalizar_escaneo(resultado)

    def _agregar_pagina_escaneo(self):
        """Escanea una página más y la agrega al PDF de la OC actual."""
        if getattr(self, "_escaneando", False):
            return
        if not self._escaneo_pdf or not os.path.exists(self._escaneo_pdf):
            return messagebox.showinfo(
                "Escáner", "Primero escanee la OC con la multifuncional."
            )
        try:
            import escaner
        except Exception as e:
            return messagebox.showerror(
                "Escáner", f"No se pudo cargar el módulo de escaneo:\n{e}"
            )

        config = self._escaneo_config or {}
        try:
            datos = escaner.config_escaner(config)
        except Exception:
            datos = {}
        modo = str(datos.get("escaner_modo", "Automático (sin ventanas)"))
        if modo.lower().startswith("con la ventana"):
            instruccion = "Coloque la página siguiente y confirme en el cuadro del sistema."
        else:
            instruccion = (
                "Coloque la página siguiente en el escáner (cristal).\n"
                "Se agregará al PDF que ya tiene la OC."
            )

        self._escaneando = True
        try:
            self.btn_arch_cli.configure(state="disabled")
            self.btn_escaner_multi.configure(state="disabled", text="⏳ Escaneando...")
        except Exception:
            pass

        v_prog = self._mostrar_progreso_escaneo("Agregando página a la OC", instruccion)
        threading.Thread(
            target=self._worker_agregar_pagina,
            args=(config, list(self._escaneo_paginas), self._escaneo_pdf, v_prog),
            daemon=True,
        ).start()

    def _worker_agregar_pagina(self, config, paginas_previas, pdf_actual, v_prog):
        """Escanea una página, rehace el PDF y vuelve a leer todo con OCR."""
        try:
            import escaner
            datos = escaner.config_escaner(config)
            carpeta = self._carpeta_escaneos_oc(config)
            escaneo = escaner.escanear(
                carpeta,
                dispositivo=datos.get("escaner_dispositivo", ""),
                dpi=datos.get("escaner_dpi", "300"),
                color=datos.get("escaner_color", "Escala de grises"),
                origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                paginas=1,
                optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                modo=datos.get("escaner_modo", "Automático (sin ventanas)"),
            )
            if not escaneo.get("ok"):
                self.ejecutar_en_ui(
                    self._finalizar_agregar_pagina,
                    {"ok": False, "error": escaneo.get("error") or "No se pudo escanear la página."},
                    v_prog,
                )
                return

            nuevas = [str(p) for p in (escaneo.get("paginas") or []) if os.path.exists(str(p))]
            if not nuevas:
                self.ejecutar_en_ui(
                    self._finalizar_agregar_pagina,
                    {"ok": False, "error": "El escáner no devolvió la página."},
                    v_prog,
                )
                return

            paginas = list(paginas_previas or []) + nuevas
            pdf = str(pdf_actual or "")
            if not pdf.lower().endswith(".pdf"):
                pdf = os.path.join(
                    carpeta,
                    "OC_Escaneada_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".pdf",
                )
            try:
                escaner.imagenes_a_pdf(paginas, pdf)
            except Exception as e_pdf:
                self.ejecutar_en_ui(
                    self._finalizar_agregar_pagina,
                    {"ok": False, "error": f"No se pudo actualizar el PDF:\n{e_pdf}"},
                    v_prog,
                )
                return

            texto, motor = "", "Escáner"
            if str(datos.get("escaner_ocr", "Sí")).lower().startswith("s"):
                try:
                    ocr = escaner.ocr_paginas(paginas, datos.get("escaner_idioma_ocr", "Automático"))
                    texto = escaner.texto_de_resultados(ocr) or ""
                    motor = str(ocr.get("motor") or "OCR")
                except Exception as e_ocr:
                    print("[Escáner OC] Aviso - OCR al agregar página:", e_ocr)

            resultado = self._resultado_escaneo_oc(
                paginas, pdf, texto, motor, escaneo.get("aviso", "")
            )
            self.ejecutar_en_ui(self._finalizar_agregar_pagina, resultado, v_prog)
        except Exception as e:
            self.ejecutar_en_ui(
                self._finalizar_agregar_pagina,
                {"ok": False, "error": str(e)},
                v_prog,
            )

    def _finalizar_agregar_pagina(self, resultado, v_prog):
        """Cierra el progreso y vuelve a mostrar la OC con la página nueva."""
        self._cerrar_progreso_escaneo(v_prog)
        if self._esta_destruido:
            return
        self._traer_al_frente()
        resultado = resultado or {}
        paginas = []
        if isinstance(resultado.get("paginas"), (list, tuple)):
            paginas = [str(p) for p in resultado.get("paginas") if os.path.exists(str(p))]
        ruta = str(resultado.get("ruta_archivo") or "")
        if not paginas or not ruta or not os.path.exists(ruta):
            self._habilitar_botones_escaneo()
            messagebox.showerror(
                "Escáner",
                "No se pudo agregar la página.\n\n" + str(resultado.get("error") or ""),
            )
            # Se vuelve a mostrar la OC que ya estaba escaneada.
            if self._escaneo_pdf and os.path.exists(self._escaneo_pdf):
                previo = {
                    "ok": True, "motor": "Escáner",
                    "paginas": list(self._escaneo_paginas),
                    "texto": "", "datos": self._escaneo_datos,
                    "ruta_archivo": self._escaneo_pdf, "error": "",
                }
                self.ruta_archivo_temp = self._escaneo_pdf
                self._finalizar_escaneo(previo)
            return
        self._escaneo_paginas = paginas
        self._escaneo_pdf = ruta
        self._escaneo_datos = resultado.get("datos") or {}
        self.ruta_archivo_temp = ruta
        self._finalizar_escaneo(resultado)

    def _mostrar_resultado_escaneo(self, resultado):
        """Vista previa de los datos leídos antes de pasarlos al formulario."""
        if self._esta_destruido:
            return
        datos = (resultado or {}).get("datos") or {}
        familia = "Helvetica" if sys.platform == "darwin" else "Segoe UI"

        # Si había otra vista previa abierta (p. ej. al agregar una página),
        # se cierra antes de mostrar la nueva.
        try:
            if self._ventana_preview is not None and self._ventana_preview.winfo_exists():
                self._ventana_preview.destroy()
        except Exception:
            pass

        ventana = ctk.CTkToplevel(self.parent_frame)
        self._ventana_preview = ventana
        ventana.title("Resultado del escaneo de la OC")
        ventana.geometry("680x620")
        ventana.minsize(600, 520)
        try:
            ventana.transient(self.parent_frame.winfo_toplevel())
        except Exception:
            pass

        ctk.CTkLabel(
            ventana,
            text="📷 DATOS LEÍDOS DE LA ORDEN DE COMPRA",
            font=ctk.CTkFont(family=familia, size=15, weight="bold"),
            text_color="#1f538d",
        ).pack(pady=(14, 2))
        paginas_resultado = resultado.get("paginas")
        if isinstance(paginas_resultado, (list, tuple)):
            total_paginas = len(paginas_resultado)
        else:
            try:
                total_paginas = int(paginas_resultado or 1)
            except Exception:
                total_paginas = 1
        partes_motor = [
            f"Motor: {resultado.get('motor') or 'PDF'}",
            f"Páginas: {total_paginas}",
        ]
        if datos.get("items_count"):
            partes_motor.append(f"Artículos detectados: {datos['items_count']}")
        ctk.CTkLabel(
            ventana,
            text="  ·  ".join(partes_motor),
            font=ctk.CTkFont(family=familia, size=10),
            text_color="#7f8c8d",
        ).pack(pady=(0, 8))

        # El pie se reserva PRIMERO: así los botones nunca quedan fuera de la
        # ventana cuando el contenido crece (p. ej. al agregar páginas).
        f_pie = ctk.CTkFrame(ventana, fg_color="transparent")
        f_pie.pack(side="bottom", fill="x", padx=16, pady=(0, 14))

        cuerpo = ctk.CTkScrollableFrame(ventana, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        campos = [
            ("N° Orden / Pedido", datos.get("numero_oc")),
            ("Fecha de emisión", datos.get("fecha")),
            ("Cliente", datos.get("cliente")),
            ("Descripción / Evento", datos.get("descripcion")),
            ("Subtotal sin IGV", formatear_moneda(datos.get("subtotal")) if datos.get("subtotal") else ""),
            ("IGV", formatear_moneda(datos.get("igv")) if datos.get("igv") else ""),
            ("Total OC", formatear_moneda(datos.get("total")) if datos.get("total") else ""),
        ]
        for etiqueta, valor in campos:
            fila = ctk.CTkFrame(cuerpo, fg_color="#f8f9fa", corner_radius=6)
            fila.pack(fill="x", pady=3)
            ctk.CTkLabel(
                fila, text=etiqueta, width=190, anchor="w",
                font=ctk.CTkFont(family=familia, size=11, weight="bold"),
            ).pack(side="left", padx=(10, 4), pady=6)
            texto = str(valor or "").strip()
            ctk.CTkLabel(
                fila, text=texto or "— no detectado —", anchor="w",
                font=ctk.CTkFont(family=familia, size=11),
                text_color="#1e8449" if texto else "#c0392b",
            ).pack(side="left", fill="x", expand=True, padx=(0, 10))

        detalle = []
        if datos.get("ruc_cliente"):
            detalle.append(f"RUC cliente: {datos['ruc_cliente']}")
        if datos.get("proveedor"):
            detalle.append(f"Proveedor: {datos['proveedor']}")
        if detalle:
            ctk.CTkLabel(
                cuerpo, text="  ·  ".join(detalle),
                font=ctk.CTkFont(family=familia, size=10),
                text_color="#7f8c8d",
            ).pack(anchor="w", padx=4, pady=(6, 0))

        if self._escaneo_paginas and self._escaneo_pdf:
            ctk.CTkButton(
                f_pie, text="➕ Escanear otra página de la OC",
                fg_color="#8e44ad", hover_color="#6c3483",
                font=ctk.CTkFont(family=familia, size=11, weight="bold"),
                command=self._agregar_pagina_escaneo,
            ).pack(fill="x", pady=(0, 6))

        botones = ctk.CTkFrame(f_pie, fg_color="transparent")
        botones.pack(fill="x")

        def aplicar():
            self._aplicar_lectura_oc(datos)
            ventana.destroy()

        def ver_texto():
            self._ver_texto_escaneado((resultado or {}).get("texto") or "")

        ctk.CTkButton(
            botones, text="✅ Aplicar al formulario", fg_color="#27ae60",
            hover_color="#1e8449",
            font=ctk.CTkFont(family=familia, size=12, weight="bold"),
            command=aplicar,
        ).pack(side="left")
        ctk.CTkButton(
            botones, text="📝 Ver texto leído", fg_color="#1f538d",
            hover_color="#163b65",
            font=ctk.CTkFont(family=familia, size=11, weight="bold"),
            width=150, command=ver_texto,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            botones, text="Cancelar", fg_color="#7f8c8d", hover_color="#636e72",
            font=ctk.CTkFont(family=familia, size=11, weight="bold"),
            width=90, command=ventana.destroy,
        ).pack(side="right")

        self._centrar_ventana(ventana, 680, 620)
        try:
            ventana.lift()
            ventana.focus_force()
        except Exception:
            pass

    def _ver_texto_escaneado(self, texto):
        """Muestra el texto reconocido para revisarlo o copiarlo."""
        familia = "Helvetica" if sys.platform == "darwin" else "Segoe UI"
        ventana = ctk.CTkToplevel(self.parent_frame)
        ventana.title("Texto leído del documento")
        ventana.geometry("780x620")
        try:
            ventana.transient(self.parent_frame.winfo_toplevel())
        except Exception:
            pass
        ctk.CTkLabel(
            ventana, text="Texto reconocido (para revisar o copiar)",
            font=ctk.CTkFont(family=familia, size=13, weight="bold"),
        ).pack(pady=(12, 6))
        caja = ctk.CTkTextbox(
            ventana, wrap="word", font=ctk.CTkFont(family=familia, size=10)
        )
        caja.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        caja.insert("1.0", str(texto or "(sin texto)"))
        caja.configure(state="disabled")
        ctk.CTkButton(ventana, text="Cerrar", command=ventana.destroy).pack(pady=(0, 12))
        self._centrar_ventana(ventana, 780, 620)
        try:
            ventana.lift()
        except Exception:
            pass

    def _aplicar_lectura_oc(self, datos):
        """Pasa los datos leídos al formulario (la OC manda sobre la cotización)."""
        datos = datos or {}

        # Primero se intenta vincular la cotización con el total/evento leídos;
        # después se escriben los valores de la OC para que el documento manda.
        try:
            self._intentar_auto_asignar_cotizacion(
                parsear_numero_seguro(datos.get("total")),
                str(datos.get("cliente") or ""),
                str(datos.get("numero_oc") or ""),
                str(datos.get("descripcion") or ""),
            )
        except Exception:
            pass

        def poner(entrada, valor):
            valor = str(valor or "").strip()
            if not valor:
                return
            entrada.delete(0, tk.END)
            entrada.insert(0, valor)

        if datos.get("numero_oc"):
            poner(self.ent_oc_cli, datos["numero_oc"])
        if datos.get("fecha"):
            poner(self.ent_fec_cli, datos["fecha"])
        if datos.get("cliente"):
            self.ent_cliente_cli.configure(state="normal")
            poner(self.ent_cliente_cli, datos["cliente"])
            self.ent_cliente_cli.configure(state="disabled")
        if datos.get("descripcion"):
            poner(self.ent_desc, datos["descripcion"])
        if datos.get("subtotal", 0) > 0:
            poner(self.ent_subtotal, f"{datos['subtotal']:.2f}")
        if datos.get("igv", 0) > 0:
            poner(self.ent_igv, f"{datos['igv']:.2f}")
        if datos.get("total", 0) > 0:
            poner(self.ent_monto_cli, f"{datos['total']:.2f}")
        self._pintar_desglose_cotizacion(None)

    def _centrar_ventana(self, ventana, ancho=None, alto=None):
        """Centra la ventana y la achica si no cabe en la pantalla."""
        try:
            ventana.update_idletasks()
            pantalla_ancho = ventana.winfo_screenwidth()
            pantalla_alto = ventana.winfo_screenheight()
            if not ancho:
                ancho = ventana.winfo_width()
            if not alto:
                alto = ventana.winfo_height()
            ancho = min(max(320, int(ancho)), max(320, pantalla_ancho - 40))
            alto = min(max(240, int(alto)), max(240, pantalla_alto - 80))
            x = max(10, (pantalla_ancho - ancho) // 2)
            y = max(10, (pantalla_alto - alto) // 2 - 30)
            ventana.geometry(f"{ancho}x{alto}+{x}+{y}")
        except Exception:
            pass

    def _intentar_auto_asignar_cotizacion(self, total_pdf, cliente_pdf, num_oc, descripcion_pdf=""):
        """Vincula la cotización aprobada que mejor coincide con la OC leída."""
        try:
            valores_combo = self.cmb_cot_cli.cget("values")
            if not valores_combo or len(valores_combo) <= 1:
                return

            def seleccionar(item):
                self.cmb_cot_cli.set(item)
                self.al_seleccionar_cotizacion(item)

            # 1. Por importe total (lo más directo). Se compara contra la
            #    columna del importe para no confundir 1,000.00 con 11,000.00.
            if total_pdf > 0:
                total_str_fmt = f"{formatear_numero(total_pdf)}"
                for item in valores_combo:
                    partes = str(item).split(" | ")
                    if len(partes) >= 3 and partes[2].strip() == total_str_fmt:
                        seleccionar(item)
                        return
                for item in valores_combo:
                    if total_str_fmt in item:
                        seleccionar(item)
                        return

            # 2. Por nombre del evento/descripción. Sirve cuando el total de la
            #    OC incluye o excluye el Fee y no coincide exactamente.
            item_evento = self._cotizacion_por_texto(descripcion_pdf)
            if item_evento:
                seleccionar(item_evento)
                return

            # 3. Por razón social del cliente.
            if cliente_pdf:
                genericas = {"SAC", "EIRL", "SRL", "DEL", "LAS", "LOS", "POR",
                             "CON", "PARA", "SOCIEDAD", "ANONIMA", "CERRADA"}
                palabras = [p for p in re.findall(r"[a-z0-9]{4,}", _texto_busqueda(cliente_pdf))
                            if p.upper() not in genericas]
                for palabra in palabras[:2]:
                    for item in valores_combo:
                        if palabra in item.lower():
                            seleccionar(item)
                            return
        except Exception:
            pass

    def _cotizacion_por_texto(self, texto):
        """Devuelve la etiqueta de la cotización cuyo evento/cliente coincide."""
        try:
            datos = self._datos_cotizaciones or {}
            stop = {"para", "con", "del", "los", "las", "por", "una", "uno",
                    "servicio", "servicios", "evento", "eventos", "cliente",
                    "cotizacion", "aprobacion", "aprobo"}
            palabras = {p for p in re.findall(r"[a-z0-9]{4,}", _texto_busqueda(texto))
                        if p not in stop}
            if len(palabras) < 2:
                return ""

            puntajes = []
            for codigo, registro in datos.items():
                registro = registro or {}
                base = _texto_busqueda(f"{registro.get('evento', '')} {registro.get('cliente', '')}")
                puntos = sum(1 for p in palabras if p in base)
                if puntos >= 2:
                    puntajes.append((puntos, str(codigo)))
            if not puntajes:
                return ""
            puntajes.sort(reverse=True)
            if len(puntajes) > 1 and puntajes[0][0] == puntajes[1][0]:
                return ""   # empate: es mejor que el usuario elija
            codigo = puntajes[0][1]
            for item in self.cmb_cot_cli.cget("values") or []:
                if str(item).split(" | ")[0].strip() == codigo:
                    return item
        except Exception:
            pass
        return ""

    def calcular_totales_math(self, event=None):
        try:
            sub = parsear_numero_seguro(self.ent_subtotal.get())
            igv_pct = obtener_porcentaje_igv()
            igv = sub * igv_pct / 100.0
            tot = sub + igv
            self.ent_igv.delete(0, tk.END)
            self.ent_igv.insert(0, f"{igv:.2f}")
            self.ent_monto_cli.delete(0, tk.END)
            self.ent_monto_cli.insert(0, f"{tot:.2f}")
            # El importe ya no corresponde al desglose de la cotización seleccionada.
            self._pintar_desglose_cotizacion(None)
        except Exception:
            pass

    def cargar_cotizaciones_aprobadas(self, cotizacion_a_incluir=None):
        clave_cache = "lista_cotizaciones_aprobadas_combo_v2"
        datos_cot = cache_sistema.obtener(clave_cache)
        
        if datos_cot is not None and not cotizacion_a_incluir:
            self._aplicar_combo_cotizaciones(datos_cot)
            return

        def tarea():
            lista = []
            aviso_error = None
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    # 🚀 FIX: la tabla 'cotizaciones' NO tiene columna 'total'.
                    # El total de venta de la cotización se calcula sumando precio_final_venta de sus ítems.
                    total_sql = ("(SELECT COALESCE(SUM(p.precio_final_venta), 0) "
                                 "FROM cotizacion_proveedores p "
                                 "WHERE p.codigo_cotizacion = cot.codigo_cotizacion)")
                    sin_oc = ("cot.codigo_cotizacion NOT IN ("
                              "SELECT cotizacion_asociada FROM ordenes_compra_clientes "
                              "WHERE cotizacion_asociada IS NOT NULL)")

                    # 🚀 Se muestran TODAS las aprobadas; si alguna ya tiene orden de
                    #    compra, se indica en la lista (antes esas se ocultaban).
                    ocs_sql = ("LEFT JOIN (SELECT TRIM(cotizacion_asociada) AS cod, "
                               "STRING_AGG(numero_oc, ', ') AS ocs "
                               "FROM ordenes_compra_clientes "
                               "WHERE cotizacion_asociada IS NOT NULL "
                               "GROUP BY TRIM(cotizacion_asociada)) oc "
                               "ON oc.cod = TRIM(cot.codigo_cotizacion)")

                    def construir_sql(con_reglas_fee):
                        # sin_fee y porcentaje_financiamiento son los mismos parámetros que usa
                        # Cotizaciones para exonerar el Fee 15% y calcular el financiamiento.
                        cols_extra = (", COALESCE(cot.sin_fee, FALSE), COALESCE(cot.porcentaje_financiamiento, 0)"
                                      if con_reglas_fee else ", FALSE, 0")
                        sql = f"""
                            SELECT cot.codigo_cotizacion,
                                   COALESCE(cot.nombre_empresa, 'Cliente'),
                                   {total_sql},
                                   COALESCE(cot.nombre_evento, ''){cols_extra},
                                   COALESCE(oc.ocs, '')
                            FROM cotizaciones cot
                            {ocs_sql}
                            WHERE cot.status = 'Aprobada'
                        """
                        # 🚀 Ya NO se filtran las que tienen OC: se listan todas.
                        return (sql + " ORDER BY cot.id DESC LIMIT 500", None)

                    filas, error_reglas = None, None
                    for con_reglas_fee in (True, False):
                        sql, parametros = construir_sql(con_reglas_fee)
                        try:
                            c.execute(sql, parametros) if parametros else c.execute(sql)
                            filas = c.fetchall()
                            break
                        except Exception as e_consulta:
                            conn.rollback()
                            print("[Cotizaciones Load Error]", e_consulta)
                            error_reglas = str(e_consulta).strip()

                    if filas is None:
                        # 🚀 Red de seguridad: si la consulta enriquecida falla, se listan las aprobadas igual.
                        aviso_error = error_reglas
                        c.execute("""
                            SELECT cot.codigo_cotizacion, COALESCE(cot.nombre_empresa, 'Cliente'), 0, COALESCE(cot.nombre_evento, '')
                            FROM cotizaciones cot
                            WHERE cot.status = 'Aprobada'
                            ORDER BY cot.id DESC LIMIT 80
                        """)
                        filas = c.fetchall()

                    for r in filas:
                        cod_val = str(r[0]).strip()
                        if not cod_val or cod_val == "None":
                            continue
                        cli_val = str(r[1]).strip()
                        # False/True = Fee 15% exonerado en la cotización; % de financiamiento.
                        sin_fee_val = bool(r[4]) if len(r) > 4 and r[4] is not None else False
                        try:
                            fin_pct_val = float(r[5]) if len(r) > 5 and r[5] is not None else 0.0
                        except (TypeError, ValueError):
                            fin_pct_val = 0.0
                        oc_val = ""
                        try:
                            if len(r) > 6 and r[6] is not None:
                                oc_val = str(r[6]).strip()
                        except Exception:
                            oc_val = ""
                        lista.append({
                            "codigo": cod_val,
                            "cliente": cli_val,
                            "oc": oc_val,
                            "evento": str(r[3]).strip() if len(r) > 3 and r[3] is not None else "",
                            "venta": parsear_numero_seguro(r[2]),
                            "sin_fee": sin_fee_val,
                            "fin_pct": max(0.0, fin_pct_val),
                        })

                    # Solo se cachea cuando la consulta principal funcionó (sin exclusiones).
                    if not cotizacion_a_incluir and not aviso_error:
                        cache_sistema.guardar(clave_cache, lista)
                except Exception as e:
                    print("[Cotizaciones Load Error]", e)
                    aviso_error = str(e).strip()
                finally:
                    liberar_conexion(conn)

            self.ejecutar_en_ui(self._aplicar_combo_cotizaciones, lista)
            if aviso_error:
                self.ejecutar_en_ui(
                    messagebox.showwarning,
                    "Cotizaciones Aprobadas",
                    "No se pudo cargar la lista completa de cotizaciones aprobadas.\n\n"
                    f"Detalle técnico: {aviso_error}\n\n"
                    "Se muestran todas las cotizaciones aprobadas sin filtro de órdenes de compra."
                )

        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_combo_cotizaciones(self, datos):
        if self._esta_destruido:
            return
        try:
            etiquetas = ["--- Seleccione ---"]
            mapa = {}
            for d in (datos or []):
                if isinstance(d, str):
                    # Compatibilidad con listas guardadas en caché por versiones previas.
                    if d and d not in etiquetas:
                        etiquetas.append(d)
                    continue
                try:
                    cod_val = str(d.get("codigo", "")).strip()
                except Exception:
                    continue
                if not cod_val:
                    continue
                # El importe mostrado en la lista es el total que paga el cliente:
                # Venta + 15% Fee + IGV (+ Financiamiento), igual que la cotización.
                totales = calcular_totales_cotizacion(d.get("venta"), d.get("sin_fee"), d.get("fin_pct"))
                registro = dict(d)
                registro.update(totales)
                mapa[cod_val] = registro
                etiqueta = (f"{cod_val} | {registro.get('cliente') or 'Cliente'} | "
                            f"{formatear_numero(totales['total'])}")
                if str(registro.get("oc") or "").strip():
                    etiqueta += f" | ⚠️ ya tiene OC: {registro['oc']}"
                etiquetas.append(etiqueta)

            self._datos_cotizaciones = mapa
            self.cmb_cot_cli.configure(values=etiquetas)
            val_actual = self.cmb_cot_cli.get()
            if val_actual in ("Cargando...", "Cargando cotizaciones...", ""):
                self.cmb_cot_cli.set(etiquetas[0] if etiquetas else "--- Seleccione ---")
        except Exception:
            pass

    def _pintar_desglose_cotizacion(self, registro):
        """Muestra el desglose Venta + 15% Fee + IGV del importe cargado desde la cotización."""
        try:
            if not registro:
                self.lbl_desglose.configure(text="")
                return
            moneda = cargar_configuracion_regional().get("simbolo_moneda", "S/.")
            partes = []
            if registro.get("venta") is not None:
                partes.append(f"Venta: {moneda} {formatear_numero(registro['venta'])}")
            if "fee" in registro:
                if registro.get("sin_fee"):
                    partes.append("Fee 15%: exonerado")
                else:
                    partes.append(f"+ Fee 15%: {moneda} {formatear_numero(registro['fee'])}")
            partes.append(f"= Sin IGV: {moneda} {formatear_numero(registro.get('sin_igv', 0.0))}")
            partes.append(f"+ IGV ({registro.get('igv_pct', 18):g}%): {moneda} {formatear_numero(registro.get('igv', 0.0))}")
            if registro.get("financiamiento", 0.0):
                partes.append(f"+ Financ. ({registro.get('fin_pct', 0):g}%): {moneda} {formatear_numero(registro['financiamiento'])}")
            partes.append(f"= Total OC: {moneda} {formatear_numero(registro.get('total', 0.0))}")
            if str(registro.get("oc") or "").strip():
                partes.append(f"⚠️ Esta cotización YA tiene OC: {registro['oc']}")
            self.lbl_desglose.configure(text="  ".join(partes))
        except Exception:
            pass

    def al_seleccionar_cotizacion(self, choice):
        if choice in ("--- Seleccione ---", "Cargando cotizaciones..."):
            self.ent_cliente_cli.configure(state="normal")
            self.ent_cliente_cli.delete(0, tk.END)
            self.ent_cliente_cli.configure(state="disabled")
            self.ent_monto_cli.delete(0, tk.END)
            self.ent_subtotal.delete(0, tk.END)
            self.ent_igv.delete(0, tk.END)
            self.ent_desc.delete(0, tk.END)
            self._pintar_desglose_cotizacion(None)
            return

        partes = choice.split(" | ")
        if len(partes) >= 2:
            cod_cot = partes[0].strip()
            cli_val = partes[1].strip() if len(partes) > 1 else "Cliente Genérico"
            ev_val = f"Aprobación de la cotización {cod_cot}"

            # 🚀 Igual que en Cotizaciones: Venta + 15% Fee + IGV (+ Financiamiento).
            registro = self._datos_cotizaciones.get(cod_cot)
            if registro is None:
                # Sin datos de la cotización (lista antigua en caché): el importe mostrado
                # es el total con IGV, se retrocede para obtener el valor sin IGV.
                igv_pct = obtener_porcentaje_igv()
                total_visible = parsear_numero_seguro(partes[2]) if len(partes) > 2 else 0.0
                sin_igv = total_visible / (1 + igv_pct / 100.0) if total_visible > 0 else 0.0
                registro = {
                    "sin_igv": sin_igv, "igv_pct": igv_pct,
                    "igv": total_visible - sin_igv,
                    "fin_pct": 0.0, "financiamiento": 0.0,
                    "total": total_visible,
                }

            self.ent_cliente_cli.configure(state="normal")
            self.ent_cliente_cli.delete(0, tk.END)
            self.ent_cliente_cli.insert(0, cli_val)
            self.ent_cliente_cli.configure(state="disabled")

            self.ent_desc.delete(0, tk.END)
            self.ent_desc.insert(0, ev_val)

            try:
                if not self.ent_monto_cli.get().strip() or self.id_oc_en_edicion is None:
                    self.ent_monto_cli.delete(0, tk.END)
                    self.ent_monto_cli.insert(0, f"{registro.get('total', 0.0):.2f}")
                    self.ent_subtotal.delete(0, tk.END)
                    self.ent_subtotal.insert(0, f"{registro.get('sin_igv', 0.0):.2f}")
                    self.ent_igv.delete(0, tk.END)
                    self.ent_igv.insert(0, f"{registro.get('igv', 0.0):.2f}")
                    self._pintar_desglose_cotizacion(registro)
                elif abs(parsear_numero_seguro(self.ent_monto_cli.get()) - registro.get("total", 0.0)) <= 0.01:
                    # Registro en edición cuyo importe sí proviene de la cotización elegida.
                    self._pintar_desglose_cotizacion(registro)
                else:
                    # El importe guardado no corresponde a esta cotización: no se muestra desglose.
                    self._pintar_desglose_cotizacion(None)
            except Exception:
                pass

    def procesar_guardar_o_actualizar(self):
        if self.id_oc_en_edicion is not None:
            self.actualizar_oc()
        else:
            self.guardar_oc()

    def guardar_oc(self):
        oc = self.ent_oc_cli.get().strip()
        cot_str = self.cmb_cot_cli.get()
        fecha = self.ent_fec_cli.get().strip()
        cli = self.ent_cliente_cli.get().strip()
        desc = self.ent_desc.get().strip()
        
        if not oc:
            return messagebox.showwarning("Atención", "Debe ingresar el No de Orden de Compra.")
        if cot_str in ("--- Seleccione ---", "Cargando cotizaciones..."):
            return messagebox.showwarning("Atención", "Debe seleccionar una cotización de la lista.")
            
        if not self.ruta_archivo_temp:
            if not messagebox.askyesno(
                "PDF Faltante", 
                "⚠️ No has cargado el PDF de la Orden de Compra.\n\n¿Deseas registrarla en el sistema sin el documento digital?"
            ):
                return
                
        cot_codigo = cot_str.split(" | ")[0].strip()
        subtotal = parsear_numero_seguro(self.ent_subtotal.get())
        igv = parsear_numero_seguro(self.ent_igv.get())
        monto = parsear_numero_seguro(self.ent_monto_cli.get())
            
        ruta_final = ""
        if self.ruta_archivo_temp and os.path.exists(self.ruta_archivo_temp):
            ruta_base = normalizar_ruta(cargar_configuracion_regional().get("ruta_drive", ""))
            if not ruta_base:
                return messagebox.showwarning(
                    "Configuración Requerida", 
                    "Debe configurar la ruta de Google Drive en los ajustes del sistema para archivar PDFs."
                )
                
            carpeta_dest = os.path.join(ruta_base, "ordenes_compra_recibidas")
            try:
                os.makedirs(carpeta_dest, exist_ok=True)
            except Exception as e:
                return messagebox.showerror("Error de Carpeta", f"No se pudo crear el directorio de destino:\n{e}")
                
            ext = os.path.splitext(self.ruta_archivo_temp)[1]
            oc_limpio = re.sub(r'[\/*?:"<>|]', '_', oc)
            cot_limpio = re.sub(r'[\/*?:"<>|]', '_', cot_codigo)
            nombre_archivo = f"OC_{oc_limpio}_{cot_limpio}{ext}"
            ruta_final = os.path.join(carpeta_dest, nombre_archivo)
            
            try:
                shutil.copy2(self.ruta_archivo_temp, ruta_final)
            except Exception as e:
                return messagebox.showerror("Error de Copia", f"No se pudo guardar el archivo en la unidad:\n{e}")
                
        conn = conectar_db()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                INSERT INTO ordenes_compra_clientes (
                    numero_oc, cotizacion_asociada, fecha, cliente, descripcion, subtotal, igv, monto_total, archivo_ruta
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (oc, cot_codigo, fecha, cli, desc, subtotal, igv, monto, ruta_final))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Ordenes de Compra Clientes", f"Recibió y archivó OC {oc} vinculada a {cot_codigo}")
            messagebox.showinfo("Éxito", "Orden de Compra archivada y lista para facturación.")
            
            self.limpiar_formulario()
            self.cargar_cotizaciones_aprobadas()
            self.cargar_tabla(reset_pagina=True)
        except Exception as e:
            messagebox.showerror("Error BD", str(e))
        finally:
            liberar_conexion(conn)

    def cargar_oc_para_editar(self):
        sel = self.tbl_cli.selection()
        if not sel:
            return messagebox.showwarning("Atención", "Por favor, seleccione una orden de la lista para editar.")
            
        valores = self.tbl_cli.item(sel[0], "values")
        id_reg = valores[0]
        num_oc = valores[1]
        fecha = valores[2]
        cot_cod = valores[3]
        cli = valores[4]
        desc = valores[5]
        subtotal_str = valores[6]
        igv_str = valores[7]
        monto_str = valores[8]
        ruta_real = valores[10] if len(valores) > 10 else ""
        
        self.id_oc_en_edicion = id_reg
        self.ruta_archivo_actual_en_edicion = ruta_real
        self.ruta_archivo_temp = ""
        
        self.lbl_modo.configure(
            text=f"✏️ EDITANDO ORDEN: {num_oc}",
            text_color="#d35400",
            fg_color="#fdebd0"
        )
        self.btn_guardar_oc.configure(
            text="🔄 Guardar Cambios de OC",
            fg_color="#d35400",
            hover_color="#a04000"
        )
        self.btn_cancelar_edicion.pack(fill="x", padx=15, pady=(0, 15))
        
        self.ent_oc_cli.delete(0, tk.END)
        self.ent_oc_cli.insert(0, str(num_oc))
        
        self.ent_fec_cli.delete(0, tk.END)
        self.ent_fec_cli.insert(0, str(fecha))
        
        self.ent_cliente_cli.configure(state="normal")
        self.ent_cliente_cli.delete(0, tk.END)
        self.ent_cliente_cli.insert(0, str(cli))
        self.ent_cliente_cli.configure(state="disabled")
        
        self.ent_desc.delete(0, tk.END)
        self.ent_desc.insert(0, str(desc))
        
        self.ent_subtotal.delete(0, tk.END)
        self.ent_subtotal.insert(0, f"{parsear_numero_seguro(subtotal_str):.2f}")
        
        self.ent_igv.delete(0, tk.END)
        self.ent_igv.insert(0, f"{parsear_numero_seguro(igv_str):.2f}")
        
        self.ent_monto_cli.delete(0, tk.END)
        self.ent_monto_cli.insert(0, f"{parsear_numero_seguro(monto_str):.2f}")
        
        if ruta_real:
            self.btn_arch_cli.configure(text="📄 Reemplazar PDF (Opcional)", fg_color="#7f8c8d")
        else:
            self.btn_arch_cli.configure(text="📄 Adjuntar PDF", fg_color="#1f538d")
            
        self.cargar_cotizaciones_aprobadas(cotizacion_a_incluir=cot_cod)
        
        def seleccionar_en_combo():
            valores_combo = self.cmb_cot_cli.cget("values")
            for item in valores_combo:
                if item.startswith(cot_cod):
                    self.cmb_cot_cli.set(item)
                    break
            else:
                self.cmb_cot_cli.set(cot_cod)
                
        self.parent_frame.after(100, seleccionar_en_combo)

    def actualizar_oc(self):
        if self.id_oc_en_edicion is None:
            return
            
        oc = self.ent_oc_cli.get().strip()
        cot_str = self.cmb_cot_cli.get()
        fecha = self.ent_fec_cli.get().strip()
        cli = self.ent_cliente_cli.get().strip()
        desc = self.ent_desc.get().strip()
        
        if not oc:
            return messagebox.showwarning("Atención", "Debe ingresar el No de Orden de Compra.")
        if cot_str in ("--- Seleccione ---", "Cargando cotizaciones..."):
            return messagebox.showwarning("Atención", "Debe seleccionar una cotización de la lista.")
            
        cot_codigo = cot_str.split(" | ")[0].strip()
        subtotal = parsear_numero_seguro(self.ent_subtotal.get())
        igv = parsear_numero_seguro(self.ent_igv.get())
        monto = parsear_numero_seguro(self.ent_monto_cli.get())
        
        ruta_final = self.ruta_archivo_actual_en_edicion
        
        if self.ruta_archivo_temp and os.path.exists(self.ruta_archivo_temp):
            ruta_base = normalizar_ruta(cargar_configuracion_regional().get("ruta_drive", ""))
            if ruta_base:
                carpeta_dest = os.path.join(ruta_base, "ordenes_compra_recibidas")
                os.makedirs(carpeta_dest, exist_ok=True)
                ext = os.path.splitext(self.ruta_archivo_temp)[1]
                oc_limpio = re.sub(r'[\/*?:"<>|]', '_', oc)
                cot_limpio = re.sub(r'[\/*?:"<>|]', '_', cot_codigo)
                nombre_archivo = f"OC_{oc_limpio}_{cot_limpio}{ext}"
                ruta_final = os.path.join(carpeta_dest, nombre_archivo)
                try:
                    shutil.copy2(self.ruta_archivo_temp, ruta_final)
                except Exception as e:
                    print(f"Error al reemplazar PDF: {e}")

        conn = conectar_db()
        if not conn:
            return
        try:
            c = conn.cursor()
            c.execute("""
                UPDATE ordenes_compra_clientes 
                SET numero_oc = %s,
                    cotizacion_asociada = %s,
                    fecha = %s,
                    cliente = %s,
                    descripcion = %s,
                    subtotal = %s,
                    igv = %s,
                    monto_total = %s,
                    archivo_ruta = %s
                WHERE id = %s
            """, (oc, cot_codigo, fecha, cli, desc, subtotal, igv, monto, ruta_final, self.id_oc_en_edicion))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Ordenes de Compra Clientes", f"Actualizó y modificó OC {oc} (ID #{self.id_oc_en_edicion})")
            messagebox.showinfo("Actualización Exitosa", f"La Orden de Compra {oc} fue actualizada correctamente.")
            
            self.cancelar_edicion()
            self.cargar_tabla(reset_pagina=False)
            self.cargar_cotizaciones_aprobadas()
        except Exception as e:
            messagebox.showerror("Error de Actualización", str(e))
        finally:
            liberar_conexion(conn)

    def cancelar_edicion(self):
        self.id_oc_en_edicion = None
        self.ruta_archivo_actual_en_edicion = ""
        self.ruta_archivo_temp = ""
        
        self.lbl_modo.configure(
            text="📝 NUEVO REGISTRO DE ORDEN",
            text_color="#1f538d",
            fg_color="#e8f0fe"
        )
        self.btn_guardar_oc.configure(
            text="💾 Archivar Orden Oficial",
            fg_color="#27ae60",
            hover_color="#1e8449"
        )
        self.btn_cancelar_edicion.pack_forget()
        self.btn_arch_cli.configure(text="📄 Escanear OC (PDF o imagen)", fg_color="#1f538d")
        
        self.limpiar_formulario()
        self.cargar_cotizaciones_aprobadas()

    def limpiar_formulario(self):
        self.ent_oc_cli.delete(0, tk.END)
        self.ent_desc.delete(0, tk.END)
        self.ent_subtotal.delete(0, tk.END)
        self.ent_igv.delete(0, tk.END)
        self.ent_monto_cli.delete(0, tk.END)
        self.ent_cliente_cli.configure(state="normal")
        self.ent_cliente_cli.delete(0, tk.END)
        self.ent_cliente_cli.configure(state="disabled")
        self.ent_fec_cli.delete(0, tk.END)
        self.ent_fec_cli.insert(0, datetime.now().strftime("%d/%m/%Y"))
        self.cmb_cot_cli.set("--- Seleccione ---")
        self._pintar_desglose_cotizacion(None)

        # Al terminar una OC, el siguiente registro empieza sin PDF adjunto.
        self.ruta_archivo_temp = ""
        self._escaneo_paginas = []
        self._escaneo_pdf = ""
        self._escaneo_datos = {}
        try:
            self.btn_arch_cli.configure(
                text="📄 Escanear OC (PDF o imagen)", fg_color="#1f538d"
            )
        except Exception:
            pass

    def cargar_tabla(self, reset_pagina=False):
        if self._esta_destruido:
            return
            
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = self.ent_busc_cli.get().strip().lower()
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"oc_clientes_turbo_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos)
            return

        def tarea():
            rows_processed = []
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    cursor = conn.cursor()
                    query_base = """
                        SELECT id, numero_oc, fecha, cotizacion_asociada, cliente, descripcion, subtotal, igv, monto_total, archivo_ruta 
                        FROM ordenes_compra_clientes
                    """
                    if filtro == "":
                        cursor.execute(f"{query_base} ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                    else:
                        val = f"%{filtro}%"
                        cursor.execute(f"""
                            {query_base} 
                            WHERE numero_oc ILIKE %s OR cliente ILIKE %s OR cotizacion_asociada ILIKE %s OR descripcion ILIKE %s
                            ORDER BY id DESC LIMIT %s OFFSET %s
                        """, (val, val, val, val, self.registros_por_pagina, offset))
                    
                    raw_rows = cursor.fetchall()
                    
                    for r in raw_rows:
                        ruta_pdf = r[9]
                        tiene_pdf = False
                        if ruta_pdf:
                            try:
                                tiene_pdf = os.path.exists(normalizar_ruta(ruta_pdf))
                            except Exception:
                                tiene_pdf = False
                        rows_processed.append((*r, tiene_pdf))
                        
                    cache_sistema.guardar(clave_cache, rows_processed)
                except Exception as ex:
                    print("[Table Query Error]", ex)
                finally:
                    liberar_conexion(conn)
                    
            self.ejecutar_en_ui(self._pintar_tabla, rows_processed)

        threading.Thread(target=tarea, daemon=True).start()

    def _pintar_tabla(self, rows):
        if self._esta_destruido:
            return
            
        self.tbl_cli.delete(*self.tbl_cli.get_children())
            
        if not rows:
            self.tbl_cli.insert("", tk.END, values=("", "Sin registros", "", "", "No se encontraron órdenes registradas.", "", "", "", "", "", ""))
            self.btn_ant.configure(state="disabled")
            self.btn_sig.configure(state="disabled")
            return
            
        for r in rows:
            tiene_pdf = r[10] if len(r) > 10 else bool(r[9])
            arch = "✅ Ver PDF" if tiene_pdf else ("⚠️ Archivo no encontrado" if r[9] else "❌ Sin PDF")
            self.tbl_cli.insert("", tk.END, values=(
                r[0], r[1], r[2], r[3], r[4], r[5], 
                formatear_moneda(r[6]), formatear_moneda(r[7]), formatear_moneda(r[8]), 
                arch, r[9]
            ))

        self.btn_ant.configure(state="normal" if self.pagina_actual > 1 else "disabled")
        self.btn_sig.configure(state="normal" if len(rows) == self.registros_por_pagina else "disabled")

    def abrir_pdf_oc(self, event=None):
        sel = self.tbl_cli.selection()
        if not sel:
            return messagebox.showwarning("Atención", "Seleccione un registro de la tabla para visualizar su PDF.")
            
        ruta = self.tbl_cli.item(sel[0], "values")[10]
        if ruta:
            abrir_documento(ruta)
        else:
            messagebox.showinfo("Aviso", "No hay un archivo PDF asociado a este registro.")

    def eliminar_oc(self):
        sel = self.tbl_cli.selection()
        if not sel:
            return messagebox.showwarning("Atención", "Por favor, seleccione una orden de la lista para eliminar.")
            
        id_reg = self.tbl_cli.item(sel[0], "values")[0]
        num_oc = self.tbl_cli.item(sel[0], "values")[1]
        cot_cod = self.tbl_cli.item(sel[0], "values")[3]
        ruta = self.tbl_cli.item(sel[0], "values")[10]
        
        confirmacion = messagebox.askyesno(
            "Confirmar Eliminación", 
            f"¿Está seguro de eliminar permanentemente la Orden {num_oc} vinculada a la cotización {cot_cod}?\n\n"
            "Al eliminarla:\n"
            "- El registro se borrará de la base de datos.\n"
            "- La cotización volverá a estar disponible para asignación.\n"
            "- El evento quedará registrado en la bitácora de auditoría."
        )
        
        if not confirmacion:
            return
            
        if ruta:
            ruta_res = normalizar_ruta(ruta)
            if os.path.exists(ruta_res):
                try:
                    os.remove(ruta_res)
                except Exception as e:
                    print(f"No se pudo eliminar el archivo físico: {e}")
                    
        conn = conectar_db()
        if conn:
            try:
                conn.cursor().execute("DELETE FROM ordenes_compra_clientes WHERE id = %s", (id_reg,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Ordenes de Compra Clientes", f"Eliminó OC Recibida {num_oc} (ID #{id_reg})")
                messagebox.showinfo("Eliminación Completada", f"La orden {num_oc} fue eliminada exitosamente.")
            except Exception as e:
                messagebox.showerror("Error BD", f"No se pudo eliminar: {e}")
            finally:
                liberar_conexion(conn)
                
        if self.id_oc_en_edicion == id_reg:
            self.cancelar_edicion()
            
        self.cargar_tabla(reset_pagina=True)
        self.cargar_cotizaciones_aprobadas()


if __name__ == "__main__":
    root = ctk.CTk()
    root.geometry("1150x680")
    root.title("Gestión Cross-Platform Órdenes de Compra (Cliente) - Turbo Edition")
    app = OrdenesCompraClienteApp(root, "Tester")
    root.mainloop()
