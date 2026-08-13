# -*- coding: utf-8 -*-
"""
SOLICITUD_PROVEEDOR.PY (ENTERPRISE EDITION)
- FIX: Integración con formulario_sctr.py para generar PDF de SCTR.
- FIX: Copiado automático del PDF al portapapeles al enviar WhatsApp/Email.
- FIX: Renderizado de Logo Borde a Borde en la Carta de Locación/Cliente con sistema de fallbacks.
- FIX: Márgenes dinámicos en la carta para evitar solapamientos.
- Paginación (Lazy Loading) y Buscador Asíncrono en Historial.
- Uso de Caché Inteligente para el combo de Eventos.
- Protección del Pool de Conexiones (liberar_conexion).
- Distribución 50/50 exacta mediante Grid weights.
"""
import os
import sys
import json
import re
import shutil
import subprocess
import threading
import urllib.parse
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
from datetime import datetime

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

# 🚀 IMPORTAMOS EL GENERADOR DE FORMULARIOS SCTR
try:
    from formulario_sctr import generar_formulario_sctr
except ImportError:
    generar_formulario_sctr = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


def cargar_config():
    config = {}
    try:
        if os.path.exists(RUTA_CONFIG):
            with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config


def copiar_archivo_portapapeles(ruta):
    try:
        ruta_absoluta = os.path.abspath(ruta)
        if sys.platform == "darwin":
            os.system(f'osascript -e \'set the clipboard to POSIX file "{ruta_absoluta}"\'')
        elif sys.platform == "win32":
            os.system(f'powershell -command "Set-Clipboard -Path \'{ruta_absoluta}\'"')
    except Exception as e:
        print("Error copiando al portapapeles:", e)


def limpiar_nombre(nombre):
    return str(nombre).strip().strip("() '\" ,")


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
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")


# =======================================================
# EXTRACCIÓN DE DATOS DEL FORMULARIO PDF RELLENADO
# =======================================================
def extraer_datos_formulario_pdf(ruta_pdf):
    try:
        from pypdf import PdfReader
    except ImportError:
        return None, "Falta instalar pypdf. Ejecuta: pip install pypdf"
    try:
        reader = PdfReader(ruta_pdf)
        campos = reader.get_fields() or {}
    except Exception as e:
        return None, f"No se pudieron leer los campos del PDF: {e}"
    if not campos:
        return None, ("El PDF no tiene campos rellenados.\n"
                       "El proveedor debió completarlo con Adobe Acrobat / Xodo (campos interactivos).")

    def val(nombre):
        f = campos.get(nombre)
        if not f:
            return ""
        v = f.get("/V")
        return str(v).strip() if v is not None else ""

    datos = {"personal": [], "sctr": {}, "rep": {}}
    for i in range(1, 41):
        nom = val(f"p{i}_nombre")
        dni = val(f"p{i}_dni")
        car = val(f"p{i}_cargo")
        if nom or dni:
            datos["personal"].append((nom, dni, car))
    datos["sctr"] = {
        "compania": val("sctr_compania"),
        "poliza": val("sctr_poliza"),
        "desde": val("sctr_desde"),
        "hasta": val("sctr_hasta"),
    }
    datos["rep"] = {"nombre": val("rep_nombre"), "dni": val("rep_dni")}
    return datos, None


# Variable global definida al más alto nivel
_SCHEMA_SOL_OK = False

class SolicitudProveedorApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo
        self.evento_data = None
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_bd()
        self.crear_interfaz()
        
        # Retraso ligero para que la interfaz se dibuje antes de consultar
        self.parent_frame.after(150, self.cargar_eventos)

    # =======================================================
    # SCHEMA + COLUMNAS EXTRA (EN SEGUNDO PLANO)
    # =======================================================
    def inicializar_bd(self):
        global _SCHEMA_SOL_OK
        if _SCHEMA_SOL_OK:
            return
            
        def tarea_init():
            global _SCHEMA_SOL_OK
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS solicitudes_proveedor (
                        id SERIAL PRIMARY KEY,
                        codigo_cotizacion VARCHAR(100),
                        proveedor VARCHAR(255),
                        tipo_respuesta VARCHAR(50) DEFAULT '',
                        estado VARCHAR(20) DEFAULT 'Pendiente',
                        fecha_solicitud VARCHAR(20) DEFAULT '',
                        fecha_recepcion VARCHAR(20) DEFAULT '',
                        archivo_sctr TEXT DEFAULT '',
                        notas TEXT DEFAULT ''
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS personal_proveedor (
                        id SERIAL PRIMARY KEY,
                        solicitud_id INTEGER,
                        nombre_completo VARCHAR(255),
                        dni VARCHAR(20),
                        cargo VARCHAR(100) DEFAULT ''
                    )
                """)
                for sql in (
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS sctr_compania VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS sctr_poliza VARCHAR(100) DEFAULT ''",
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS sctr_desde VARCHAR(20) DEFAULT ''",
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS sctr_hasta VARCHAR(20) DEFAULT ''",
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS rep_nombre VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE solicitudes_proveedor ADD COLUMN IF NOT EXISTS rep_dni VARCHAR(20) DEFAULT ''",
                ):
                    try: cursor.execute(sql)
                    except Exception: conn.rollback()
                conn.commit()
                _SCHEMA_SOL_OK = True
            except Exception as e:
                print("Error BD Solicitudes:", e)
            finally:
                liberar_conexion(conn)

        threading.Thread(target=tarea_init, daemon=True).start()

    # =======================================================
    # INTERFAZ CON 2 PESTAÑAS
    # =======================================================
    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(self.frame_main, text="📨 SOLICITUD DE SCTR / PERSONAL A PROVEEDORES", font=("Arial", 18, "bold"), text_color="#1f538d").pack(anchor="w", pady=(0, 10))
        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True)
        self.tab_nueva = self.tabview.add(" ➕ Solicitud / Registro ")
        self.tab_hist = self.tabview.add(" 📋 Solicitudes Elaboradas ")
        self.crear_tab_nueva()
        self.crear_tab_hist()
        self.tabview.configure(command=self.al_cambiar_tab)

    def al_cambiar_tab(self):
        if self.tabview.get() == " 📋 Solicitudes Elaboradas ":
            self.cargar_solicitudes_tab(reset_pagina=True)

    # -------------------------------------------------------
    # PESTAÑA 1: SOLICITUD / REGISTRO
    # -------------------------------------------------------
    def crear_tab_nueva(self):
        f_top = ctk.CTkFrame(self.tab_nueva, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_top.pack(fill="x", pady=(0, 10), ipadx=10, ipady=8)
        ctk.CTkLabel(f_top, text="Evento Aprobado:", font=("Arial", 12, "bold")).pack(side="left", padx=(10, 10))
        self.cmb_evento = ctk.CTkComboBox(f_top, width=380, state="readonly", command=self.al_seleccionar_evento)
        self.cmb_evento.pack(side="left", padx=(0, 15))
        self.lbl_info_evento = ctk.CTkLabel(f_top, text="Seleccione un evento para ver sus proveedores.", font=("Arial", 11), text_color="#555")
        self.lbl_info_evento.pack(side="left", fill="x", expand=True, padx=(0, 10))
        f_btns = ctk.CTkFrame(self.tab_nueva, fg_color="transparent")
        f_btns.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(f_btns, text="📤 Solicitar por WhatsApp", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=lambda: self.enviar_solicitud("whatsapp")).pack(side="left", padx=(0, 5))
        ctk.CTkButton(f_btns, text="📧 Solicitar por Email", font=("Arial", 12, "bold"), fg_color="#e67e22", hover_color="#d35400", command=lambda: self.enviar_solicitud("email")).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="📥 Registrar Información Recibida", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.registrar_recepcion).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="🔄", width=40, fg_color="#7f8c8d", hover_color="#606b6b", command=self.cargar_proveedores_evento).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="📄 Generar Carta (Locación / Cliente)", font=("Arial", 12, "bold"), fg_color="#8e44ad", hover_color="#732d91", command=self.generar_carta).pack(side="right", padx=5)
        f_tabla = ctk.CTkFrame(self.tab_nueva, corner_radius=10)
        f_tabla.pack(fill="both", expand=True)
        columnas = ("proveedor", "estado", "tipo", "f_solicitud", "f_recepcion", "personal")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", rowheight=28, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("proveedor", text="Proveedor")
        self.tabla.heading("estado", text="Estado", anchor="center")
        self.tabla.heading("tipo", text="Tipo de Respuesta", anchor="center")
        self.tabla.heading("f_solicitud", text="Solicitado el", anchor="center")
        self.tabla.heading("f_recepcion", text="Recibido el", anchor="center")
        self.tabla.heading("personal", text="Personal", anchor="center")
        self.tabla.column("proveedor", width=260, anchor="w")
        self.tabla.column("estado", width=100, anchor="center")
        self.tabla.column("tipo", width=130, anchor="center")
        self.tabla.column("f_solicitud", width=100, anchor="center")
        self.tabla.column("f_recepcion", width=100, anchor="center")
        self.tabla.column("personal", width=80, anchor="center")
        self.tabla.tag_configure("Pendiente", background="#f8d7da", foreground="#721c24")
        self.tabla.tag_configure("Solicitado", background="#fff3cd", foreground="#856404")
        self.tabla.tag_configure("Recibido", background="#d4edda", foreground="#155724")
        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll_y.pack(side="right", fill="y", padx=(0, 10), pady=10)

    # -------------------------------------------------------
    # PESTAÑA 2: SOLICITUDES ELABORADAS (CON PAGINACIÓN)
    # -------------------------------------------------------
    def crear_tab_hist(self):
        # 🚀 BUSCADOR ASÍNCRONO
        f_busqueda = ctk.CTkFrame(self.tab_hist, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(5, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_hist = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por evento o proveedor...")
        self.ent_buscar_hist.pack(side="left", fill="x", expand=True)
        self.ent_buscar_hist.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_buscar_hist.bind("<Return>", lambda e: self.cargar_solicitudes_tab(reset_pagina=True))
        
        f_btns = ctk.CTkFrame(self.tab_hist, fg_color="transparent")
        f_btns.pack(fill="x", pady=(5, 5))
        ctk.CTkButton(f_btns, text="🔄 Actualizar", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=lambda: self.cargar_solicitudes_tab(reset_pagina=True)).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="👁 Ver Archivo", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.ver_archivo_solicitud).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="🗑️ Eliminar Seleccionada", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_solicitud_seleccionada).pack(side="right", padx=5)
        
        f_tabla = ctk.CTkFrame(self.tab_hist, corner_radius=10)
        f_tabla.pack(fill="both", expand=True, pady=10)
        columnas = ("id", "evento", "proveedor", "estado", "tipo", "f_solicitud", "f_recepcion", "personal", "arch_raw", "archivos")
        self.tabla_hist = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla_hist.heading("id", text="ID", anchor="center")
        self.tabla_hist.heading("evento", text="Evento")
        self.tabla_hist.heading("proveedor", text="Proveedor")
        self.tabla_hist.heading("estado", text="Estado", anchor="center")
        self.tabla_hist.heading("tipo", text="Tipo", anchor="center")
        self.tabla_hist.heading("f_solicitud", text="Solicitado el", anchor="center")
        self.tabla_hist.heading("f_recepcion", text="Recibido el", anchor="center")
        self.tabla_hist.heading("personal", text="Personal", anchor="center")
        self.tabla_hist.heading("archivos", text="Adjuntos", anchor="center")
        self.tabla_hist.column("id", width=40, anchor="center")
        self.tabla_hist.column("evento", width=110, anchor="center")
        self.tabla_hist.column("proveedor", width=220, anchor="w")
        self.tabla_hist.column("estado", width=90, anchor="center")
        self.tabla_hist.column("tipo", width=110, anchor="center")
        self.tabla_hist.column("f_solicitud", width=90, anchor="center")
        self.tabla_hist.column("f_recepcion", width=90, anchor="center")
        self.tabla_hist.column("personal", width=70, anchor="center")
        self.tabla_hist.column("arch_raw", width=0, stretch=tk.NO)
        self.tabla_hist.column("archivos", width=70, anchor="center")
        self.tabla_hist.config(displaycolumns=("id", "evento", "proveedor", "estado", "tipo", "f_solicitud", "f_recepcion", "personal", "archivos"))
        self.tabla_hist.tag_configure("Pendiente", background="#f8d7da", foreground="#721c24")
        self.tabla_hist.tag_configure("Solicitado", background="#fff3cd", foreground="#856404")
        self.tabla_hist.tag_configure("Recibido", background="#d4edda", foreground="#155724")
        scroll_h = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_hist.yview)
        self.tabla_hist.configure(yscrollcommand=scroll_h.set)
        self.tabla_hist.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll_h.pack(side="right", fill="y", padx=(0, 10), pady=10)
        
        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(self.tab_hist, fg_color="transparent")
        f_paginacion.pack(fill="x", padx=15, pady=(5, 5))
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)
        
        ctk.CTkLabel(f_paginacion, text="💡 Haz doble clic sobre un registro para descargar/visualizar el PDF oficial de la Nota de Crédito.", font=("Arial", 11, "italic"), text_color="gray").pack(side="right", padx=10)

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_solicitudes_tab()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_solicitudes_tab()

    def buscar_con_retraso(self):
        if hasattr(self, "_busqueda_job"):
            try: self.parent_frame.after_cancel(self._busqueda_job)
            except: pass
        self._busqueda_job = self.parent_frame.after(350, lambda: self.cargar_solicitudes_tab(reset_pagina=True))

    # =======================================================
    # CARGA DE EVENTOS CON CACHÉ (ASÍNCRONO)
    # =======================================================
    def cargar_eventos(self):
        clave_cache = "lista_eventos_aprobados"
        eventos = cache_sistema.obtener(clave_cache)
        
        if eventos is not None:
            self._aplicar_eventos(eventos)
        else:
            self.cmb_evento.set("Cargando eventos...")
            def tarea():
                evs = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        evs = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                        cache_sistema.guardar(clave_cache, evs)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._aplicar_eventos(evs))

            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_eventos(self, eventos):
        if eventos:
            self.cmb_evento.configure(values=eventos)
            self.cmb_evento.set(eventos[0])
            self.al_seleccionar_evento(eventos[0])
        else:
            self.cmb_evento.configure(values=["Sin eventos aprobados"])
            self.cmb_evento.set("Sin eventos aprobados")

    def al_seleccionar_evento(self, choice):
        if "Sin eventos" in choice or not choice or "Cargando" in choice:
            return
        codigo = choice.split(" | ")[0].strip()
        nombre_ev = choice.split(" | ")[1].strip() if " | " in choice else ""
        conn = conectar_db(silencioso=True)
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT nombre_evento, fecha_evento, locacion_evento, nombre_empresa FROM cotizaciones WHERE codigo_cotizacion = %s", (codigo,))
            r = cursor.fetchone()
            self.evento_data = {
                "codigo": codigo,
                "nombre": r[0] if r else nombre_ev,
                "fecha": r[1] if r else "",
                "locacion": r[2] if r else "",
                "cliente": r[3] if r else ""
            }
            self.lbl_info_evento.configure(
                text=f"📍 Locación: {self.evento_data['locacion'] or 'Por definir'}   |   📅 Fecha: {self.evento_data['fecha'] or 'Por definir'}   |   👤 Cliente: {self.evento_data['cliente'] or '-'}"
            )
        except Exception: pass
        finally: liberar_conexion(conn)
        self.cargar_proveedores_evento()

    def cargar_proveedores_evento(self):
        if not self.evento_data:
            return
        self._prov_token = getattr(self, "_prov_token", 0) + 1
        token = self._prov_token
        codigo = self.evento_data["codigo"]
        nombre_ev = self.evento_data["nombre"]

        def tarea():
            filas = []
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    provs = []
                    try:
                        c.execute("""
                            SELECT DISTINCT TRIM(cp.proveedor_nombre)
                            FROM cotizacion_proveedores cp
                            JOIN cotizaciones cz ON cp.codigo_cotizacion = cz.codigo_cotizacion
                            WHERE cz.nombre_evento = %s
                              AND cp.proveedor_nombre IS NOT NULL AND TRIM(cp.proveedor_nombre) != ''
                            ORDER BY 1
                        """, (nombre_ev,))
                        provs = [limpiar_nombre(r[0]) for r in c.fetchall()]
                    except Exception:
                        c.rollback()
                        provs = []
                    if not provs:
                        c.execute("""
                            SELECT DISTINCT TRIM(proveedor_nombre) FROM cotizacion_proveedores
                            WHERE codigo_cotizacion = %s AND proveedor_nombre IS NOT NULL AND TRIM(proveedor_nombre) != ''
                            ORDER BY 1
                        """, (codigo,))
                        provs = [limpiar_nombre(r[0]) for r in c.fetchall()]
                    vistos = []
                    for p in provs:
                        if p in vistos:
                            continue
                        vistos.append(p)
                        c.execute("SELECT id, estado, tipo_respuesta, fecha_solicitud, fecha_recepcion FROM solicitudes_proveedor WHERE codigo_cotizacion = %s AND proveedor = %s", (codigo, p))
                        sol = c.fetchone()
                        estado = sol[1] if sol else "Pendiente"
                        tipo = sol[2] if sol and sol[2] else "-"
                        f_sol = sol[3] if sol and sol[3] else "-"
                        f_rec = sol[4] if sol and sol[4] else "-"
                        n_per = 0
                        if sol:
                            c.execute("SELECT COUNT(*) FROM personal_proveedor WHERE solicitud_id = %s", (sol[0],))
                            n_per = c.fetchone()[0]
                        filas.append((p, estado, tipo, f_sol, f_rec, n_per))
                except Exception as e:
                    print("Error cargando proveedores:", e)
                finally:
                    liberar_conexion(conn)
            self.parent_frame.after(0, lambda t=token, fl=filas: self._pintar_proveedores(t, fl))

        threading.Thread(target=tarea, daemon=True).start()

    def _pintar_proveedores(self, token, filas):
        if token != getattr(self, "_prov_token", 0):
            return
        for item in self.tabla.get_children():
            self.tabla.delete(item)
        for f in filas:
            self.tabla.insert("", tk.END, values=f, tags=(f[1],))

    def _proveedor_seleccionado(self):
        sel = self.tabla.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Primero haz clic en la fila del proveedor.")
            return None
        return self.tabla.item(sel[0], "values")[0]

    # =======================================================
    # PESTAÑA 2: CARGA LAZY LOADING Y CACHÉ
    # =======================================================
    def cargar_solicitudes_tab(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla_hist.get_children(): 
            self.tabla_hist.delete(item)
            
        filtro = ""
        if hasattr(self, 'ent_buscar_hist'):
            filtro = self.ent_buscar_hist.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina
        clave_cache = f"sol_prov_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is not None:
            self._pintar_solicitudes(datos)
        else:
            self.tabla_hist.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", "", "", "", ""))
            
            def tarea():
                filas = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            cursor.execute("""
                                SELECT s.id, s.codigo_cotizacion, s.proveedor, s.estado, s.tipo_respuesta,
                                       s.fecha_solicitud, s.fecha_recepcion, s.archivo_sctr,
                                       (SELECT COUNT(*) FROM personal_proveedor p WHERE p.solicitud_id = s.id)
                                FROM solicitudes_proveedor s
                                ORDER BY s.id DESC LIMIT %s OFFSET %s
                            """, (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            cursor.execute("""
                                SELECT s.id, s.codigo_cotizacion, s.proveedor, s.estado, s.tipo_respuesta,
                                       s.fecha_solicitud, s.fecha_recepcion, s.archivo_sctr,
                                       (SELECT COUNT(*) FROM personal_proveedor p WHERE p.solicitud_id = s.id)
                                FROM solicitudes_proveedor s
                                WHERE s.proveedor ILIKE %s OR s.codigo_cotizacion ILIKE %s OR s.estado ILIKE %s
                                ORDER BY s.id DESC LIMIT %s OFFSET %s
                            """, (val, val, val, self.registros_por_pagina, offset))
                            
                        filas = cursor.fetchall()
                        cache_sistema.guardar(clave_cache, filas)
                    except Exception as e:
                        print("Error cargando solicitudes:", e)
                    finally:
                        liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._pintar_solicitudes(filas))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_solicitudes(self, filas):
        for item in self.tabla_hist.get_children():
            self.tabla_hist.delete(item)
            
        for r in filas:
            arch_raw = str(r[7] or "")
            n_arch = len([x for x in arch_raw.split("|") if x.strip()])
            valores = (r[0], r[1], r[2], r[3], r[4] or "-", r[5] or "-", r[6] or "-", r[8], arch_raw, n_arch)
            self.tabla_hist.insert("", tk.END, values=valores, tags=(r[3],))
            
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1:
                self.btn_ant.configure(state="normal")
            else:
                self.btn_ant.configure(state="disabled")
                
            if len(filas) == self.registros_por_pagina:
                self.btn_sig.configure(state="normal")
            else:
                self.btn_sig.configure(state="disabled")

    def ver_archivo_solicitud(self):
        sel = self.tabla_hist.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Seleccione una solicitud de la lista.")
            return
        vals = self.tabla_hist.item(sel[0], "values")
        arch_raw = str(vals[8] or "") if len(vals) > 8 else ""
        rutas = [x.strip() for x in arch_raw.split("|") if x.strip() and os.path.exists(x.strip())]
        if not rutas:
            messagebox.showinfo("Aviso", "Esta solicitud no tiene archivos adjuntos (o no existen en disco).")
            return
        for r in rutas:
            abrir_documento(r)

    def eliminar_solicitud_seleccionada(self):
        sel = self.tabla_hist.selection()
        if not sel:
            messagebox.showwarning("Aviso", "Seleccione una solicitud de la lista.")
            return
        vals = self.tabla_hist.item(sel[0], "values")
        sid, cod, prov = vals[0], vals[1], vals[2]
        if not messagebox.askyesno("Confirmar Eliminación",
                                   f"¿Eliminar la solicitud de {prov} (Evento {cod})?\n\n"
                                   "Se eliminará también su personal registrado.\n"
                                   "El proveedor volverá a aparecer como Pendiente."):
            return
        conn = conectar_db()
        if not conn: return
        try:
            c = conn.cursor()
            c.execute("DELETE FROM personal_proveedor WHERE solicitud_id = %s", (sid,))
            c.execute("DELETE FROM solicitudes_proveedor WHERE id = %s", (sid,))
            conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Solicitud Proveedores", f"Eliminó la solicitud de {prov} (Evento {cod})")
            self.cargar_solicitudes_tab(reset_pagina=True)
            self.cargar_proveedores_evento()
            messagebox.showinfo("Éxito", "Solicitud eliminada correctamente.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    # =======================================================
    # ENVIAR SOLICITUDES Y GENERAR FORMULARIO PDF
    # =======================================================
    def obtener_contacto_proveedor(self, proveedor, tipo_contacto="whatsapp"):
        conn = conectar_db(silencioso=True)
        if not conn: return ""
        try:
            cursor = conn.cursor()
            if tipo_contacto == "whatsapp":
                cursor.execute("SELECT whatsapp FROM proveedores WHERE nombre = %s", (proveedor,))
            else:
                cursor.execute("SELECT correo FROM proveedores WHERE nombre = %s", (proveedor,))
            
            res = cursor.fetchone()
            if res and res[0]:
                dato = str(res[0]).strip()
                if tipo_contacto == "whatsapp":
                    digits = re.sub(r'\D', '', dato)
                    if len(digits) == 9 and digits.startswith("9"):
                        return f"51{digits}"
                    elif len(digits) >= 11:
                        return digits
                return dato
        except Exception as e:
            print("Error buscando contacto:", e)
        finally:
            liberar_conexion(conn)
        return ""

    def guardar_registro_envio(self, proveedor):
        ev = self.evento_data
        if not ev: return
        conn = conectar_db()
        if not conn: return
        
        try:
            cursor = conn.cursor()
            hoy = datetime.now().strftime("%d/%m/%Y")
            
            cursor.execute("SELECT id FROM solicitudes_proveedor WHERE codigo_cotizacion = %s AND proveedor = %s", (ev["codigo"], proveedor))
            existe = cursor.fetchone()
            
            if existe:
                cursor.execute("UPDATE solicitudes_proveedor SET estado='Solicitado', fecha_solicitud=%s WHERE id=%s", (hoy, existe[0]))
            else:
                cursor.execute("INSERT INTO solicitudes_proveedor (codigo_cotizacion, proveedor, estado, fecha_solicitud) VALUES (%s, %s, 'Solicitado', %s)", (ev["codigo"], proveedor, hoy))
            
            conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Solicitud Proveedores", f"Envió solicitud de SCTR a {proveedor} para el evento {ev['codigo']}")
            self.cargar_proveedores_evento()
        except Exception as e:
            print("Error guardando el estado de envío:", e)
        finally:
            liberar_conexion(conn)

    def enviar_solicitud(self, canal="whatsapp"):
        prov = self._proveedor_seleccionado()
        if not prov: return
        ev = self.evento_data
        if not ev: return messagebox.showwarning("Aviso", "Seleccione primero un evento aprobado.")

        if not generar_formulario_sctr:
            return messagebox.showerror("Librería Faltante", "No se encontró el módulo 'formulario_sctr.py' para generar el PDF.")

        config = cargar_config()
        empresa = config.get("razon_social_empresa", "Nuestra Empresa")

        contacto = self.obtener_contacto_proveedor(prov, canal)
        if not contacto:
            contacto = simpledialog.askstring("Contacto faltante", f"No se encontró el {'número' if canal=='whatsapp' else 'correo'} de {prov}.\n\nPor favor, ingrésalo manualmente:", parent=self.parent_frame.winfo_toplevel())
            if not contacto: return
            if canal == "whatsapp":
                digits = re.sub(r'\D', '', contacto)
                contacto = f"51{digits}" if len(digits) == 9 and digits.startswith("9") else digits

        # 🚀 GENERAR EL PDF DEL FORMULARIO SCTR INTERACTIVO
        try:
            ruta_pdf = generar_formulario_sctr(ev["codigo"], ev["nombre"], ev["fecha"], ev["locacion"], prov)
        except Exception as e:
            return messagebox.showerror("Error", f"Fallo al generar el formulario PDF:\n{e}")

        msj = (
            f"Hola equipo de *{prov}*,\n\n"
            f"Nos contactamos de parte de *{empresa}* para solicitarles la relación de personal y "
            f"pólizas SCTR correspondientes al evento aprobado:\n\n"
            f"🎉 *Evento:* {ev['nombre']}\n"
            f"📅 *Fecha:* {ev['fecha'] or 'Por definir'}\n"
            f"📍 *Locación:* {ev['locacion'] or 'Por definir'}\n\n"
            f"Por favor, completar el *formulario PDF adjunto* con la información requerida (Nombres, DNI, Cargos) y devolverlo junto con los Certificados de SCTR (Salud/Pensión) a la brevedad posible para agilizar los pases de ingreso.\n\n"
            f"Quedamos atentos a su respuesta. ¡Gracias!"
        )

        if canal == "whatsapp":
            msj_enc = urllib.parse.quote(msj)
            respuesta = messagebox.askyesnocancel("WhatsApp", "¿Abrir WhatsApp de Escritorio?\n\n[Sí] = App de Escritorio\n[No] = WhatsApp Web\n[Cancelar] = Cancelar")
            if respuesta is None: return
            url = f"{'whatsapp://send' if respuesta else 'https://api.whatsapp.com/send'}?phone={contacto}&text={msj_enc}"
        else: 
            asunto = urllib.parse.quote(f"Solicitud SCTR y Personal - Evento {ev['nombre']}")
            msj_enc = urllib.parse.quote(msj.replace("*", ""))
            url = f"mailto:{contacto}?subject={asunto}&body={msj_enc}"

        try:
            copiar_archivo_portapapeles(ruta_pdf)
            self.guardar_registro_envio(prov)
            messagebox.showinfo("¡Listo!", f"El Formulario SCTR interactivo ha sido generado y COPIADO AL PORTAPAPELES.\n\nAbriendo {'el chat de WhatsApp' if canal=='whatsapp' else 'el correo'}...\n\nHaz clic en la caja de mensaje y presiona Pegar (Ctrl+V o Cmd+V) para adjuntar el PDF.")
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la aplicación:\n{e}")

    # =======================================================
    # REGISTRAR INFORMACIÓN RECIBIDA
    # =======================================================
    def registrar_recepcion(self):
        prov = self._proveedor_seleccionado()
        if not prov:
            return
        ev = self.evento_data
        if not ev:
            return messagebox.showwarning("Aviso", "Seleccione primero un evento aprobado.")
            
        v = ctk.CTkToplevel(self.parent_frame.winfo_toplevel())
        v.title(f"Información Recibida - {prov}")
        
        ancho_v, alto_v = 700, 650
        v.update_idletasks()
        screen_w = v.winfo_screenwidth()
        screen_h = v.winfo_screenheight()
        x_pos = max(0, (screen_w // 2) - (ancho_v // 2))
        y_pos = max(0, (screen_h // 2) - (alto_v // 2) - 30)
        v.geometry(f"{ancho_v}x{alto_v}+{x_pos}+{y_pos}")
        
        v.grab_set()
        v.transient(self.parent_frame.winfo_toplevel())

        f_botones_accion = ctk.CTkFrame(v, fg_color="transparent")
        f_botones_accion.pack(side="bottom", fill="x", pady=(2, 8))
        
        ctk.CTkButton(f_botones_accion, text="💾 Guardar Información", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: guardar_recepcion()).pack(side="bottom", pady=2)
        ctk.CTkButton(f_botones_accion, text="➕ Agregar Fila de Personal", font=("Arial", 11, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=lambda: agregar_fila()).pack(side="bottom", pady=2)

        f_top_info = ctk.CTkFrame(v, fg_color="transparent")
        f_top_info.pack(side="top", fill="x", padx=15, pady=(5, 0))

        ctk.CTkLabel(f_top_info, text=f"📥 Registrar SCTR / Personal de:\n{prov}", font=("Arial", 13, "bold"), text_color="#1f538d").pack(pady=(2, 1))
        ctk.CTkLabel(f_top_info, text=f"Evento: {ev['nombre']}   |   Locación: {ev['locacion'] or 'Por definir'}", font=("Arial", 10), text_color="#555").pack(pady=(0, 4))
        
        f_tipo = ctk.CTkFrame(f_top_info, fg_color="transparent")
        f_tipo.pack(fill="x", pady=2)
        ctk.CTkLabel(f_tipo, text="Tipo de documentación recibida:", font=("Arial", 10, "bold")).pack(side="left")
        cmb_tipo = ctk.CTkComboBox(f_tipo, values=["SCTR", "Lista de Personal", "SCTR + Lista"], state="readonly", width=170, height=24)
        cmb_tipo.pack(side="left", padx=10)
        cmb_tipo.set("SCTR + Lista")
        
        f_sctr = ctk.CTkFrame(f_top_info, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_sctr.pack(fill="x", pady=2)
        ctk.CTkLabel(f_sctr, text="Datos del SCTR:", font=("Arial", 10, "bold"), text_color="#166534").grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(2, 1))
        ctk.CTkLabel(f_sctr, text="Compañía:", font=("Arial", 9, "bold")).grid(row=1, column=0, sticky="w", padx=(8, 2), pady=1)
        ent_sctr_comp = ctk.CTkEntry(f_sctr, width=170, height=24)
        ent_sctr_comp.grid(row=1, column=1, padx=2, pady=1)
        ctk.CTkLabel(f_sctr, text="N° Póliza:", font=("Arial", 9, "bold")).grid(row=1, column=2, sticky="w", padx=(8, 2), pady=1)
        ent_sctr_pol = ctk.CTkEntry(f_sctr, width=150, height=24)
        ent_sctr_pol.grid(row=1, column=3, padx=2, pady=1)
        ctk.CTkLabel(f_sctr, text="Vigencia desde:", font=("Arial", 9, "bold")).grid(row=2, column=0, sticky="w", padx=(8, 2), pady=1)
        ent_sctr_desde = ctk.CTkEntry(f_sctr, width=170, height=24)
        ent_sctr_desde.grid(row=2, column=1, padx=2, pady=1)
        ctk.CTkLabel(f_sctr, text="hasta:", font=("Arial", 9, "bold")).grid(row=2, column=2, sticky="w", padx=(8, 2), pady=1)
        ent_sctr_hasta = ctk.CTkEntry(f_sctr, width=150, height=24)
        ent_sctr_hasta.grid(row=2, column=3, padx=2, pady=(1, 4))
        
        f_rep = ctk.CTkFrame(f_top_info, fg_color="transparent")
        f_rep.pack(fill="x", pady=2)
        ctk.CTkLabel(f_rep, text="Representante:", font=("Arial", 9, "bold")).pack(side="left")
        ent_rep_nombre = ctk.CTkEntry(f_rep, width=250, height=24)
        ent_rep_nombre.pack(side="left", padx=5)
        ctk.CTkLabel(f_rep, text="DNI:", font=("Arial", 9, "bold")).pack(side="left", padx=(8, 0))
        ent_rep_dni = ctk.CTkEntry(f_rep, width=110, height=24)
        ent_rep_dni.pack(side="left", padx=5)

        f_split = ctk.CTkFrame(v, fg_color="transparent")
        f_split.pack(side="top", fill="both", expand=True, padx=15, pady=4)
        
        f_split.grid_rowconfigure(0, weight=1)
        f_split.grid_rowconfigure(1, weight=1)
        f_split.grid_columnconfigure(0, weight=1)

        f_sec_adj = ctk.CTkFrame(f_split, fg_color="transparent")
        f_sec_adj.grid(row=0, column=0, sticky="nsew", pady=(0, 2))

        f_arch_hdr = ctk.CTkFrame(f_sec_adj, fg_color="transparent")
        f_arch_hdr.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(f_arch_hdr, text="📎 Archivos adjuntos:", font=("Arial", 10, "bold")).pack(side="left")
        ctk.CTkButton(f_arch_hdr, text="📂 Adjuntar", width=80, height=24, command=lambda: adjuntar_manual()).pack(side="right", padx=(4, 0))
        ctk.CTkButton(f_arch_hdr, text="📥 Importar PDF rellenado", width=150, height=24, fg_color="#1f538d", hover_color="#163b65", command=lambda: importar_pdf_relleno()).pack(side="right", padx=(4, 0))

        f_lista_arch = ctk.CTkScrollableFrame(f_sec_adj, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_lista_arch.pack(fill="both", expand=True)
        adjuntos = []

        def repintar_adjuntos():
            for w in f_lista_arch.winfo_children():
                w.destroy()
            if not adjuntos:
                ctk.CTkLabel(f_lista_arch, text="Sin archivos adjuntos.", font=("Arial", 10), text_color="#777").pack(anchor="w", padx=8, pady=4)
                return
            for a in list(adjuntos):
                fr = ctk.CTkFrame(f_lista_arch, fg_color="transparent")
                fr.pack(fill="x", padx=5, pady=2)
                ctk.CTkLabel(fr, text=f"📄 {a['nombre']}", font=("Arial", 10), anchor="w").pack(side="left", fill="x", expand=True)
                ctk.CTkButton(fr, text="👁", width=30, height=22, fg_color="#34495e", hover_color="#2c3e50", command=lambda r=a["ruta"]: abrir_documento(r)).pack(side="left", padx=2)
                ctk.CTkButton(fr, text="✕", width=30, height=22, fg_color="#e74c3c", hover_color="#c0392b", command=lambda item=a: quitar_adjunto(item)).pack(side="left", padx=2)

        def quitar_adjunto(item):
            if item in adjuntos:
                adjuntos.remove(item)
            repintar_adjuntos()

        def agregar_adjunto(ruta):
            if ruta and not any(x["ruta"] == ruta for x in adjuntos):
                adjuntos.append({"ruta": ruta, "nombre": os.path.basename(ruta)})
                repintar_adjuntos()

        def adjuntar_manual():
            rutas = filedialog.askopenfilenames(title="Seleccionar SCTR / anexos (PDF/Imagen)", filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")], parent=v)
            for r in rutas:
                agregar_adjunto(r)

        def importar_pdf_relleno():
            ruta = filedialog.askopenfilename(title="Seleccionar formulario PDF rellenado", filetypes=[("PDF", "*.pdf")], parent=v)
            if not ruta:
                return
            datos, err = extraer_datos_formulario_pdf(ruta)
            if err:
                messagebox.showwarning("Importación", err, parent=v)
                return
            agregar_adjunto(ruta)
            ent_sctr_comp.delete(0, tk.END)
            ent_sctr_comp.insert(0, datos["sctr"].get("compania", ""))
            ent_sctr_pol.delete(0, tk.END)
            ent_sctr_pol.insert(0, datos["sctr"].get("poliza", ""))
            ent_sctr_desde.delete(0, tk.END)
            ent_sctr_desde.insert(0, datos["sctr"].get("desde", ""))
            ent_sctr_hasta.delete(0, tk.END)
            ent_sctr_hasta.insert(0, datos["sctr"].get("hasta", ""))
            ent_rep_nombre.delete(0, tk.END)
            ent_rep_nombre.insert(0, datos["rep"].get("nombre", ""))
            ent_rep_dni.delete(0, tk.END)
            ent_rep_dni.insert(0, datos["rep"].get("dni", ""))
            for item in list(filas):
                quitar_fila(item[0])
            for nom, dni, car in datos["personal"]:
                agregar_fila(nom, dni, car)
            messagebox.showinfo("Importación Exitosa", f"Se extrajeron {len(datos['personal'])} persona(s) del formulario.\nEl PDF quedó en la lista de adjuntos.\nRevise y presione Guardar.", parent=v)

        f_sec_per = ctk.CTkFrame(f_split, fg_color="transparent")
        f_sec_per.grid(row=1, column=0, sticky="nsew", pady=(2, 0))

        ctk.CTkLabel(f_sec_per, text="👷 Personal que realizará el trabajo (Nombre y DNI):", font=("Arial", 10, "bold"), text_color="#1f538d").pack(anchor="w", pady=(0, 2))

        f_scroll = ctk.CTkScrollableFrame(f_sec_per, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_scroll.pack(fill="both", expand=True)
        filas = []

        def quitar_fila(fr):
            for item in list(filas):
                if item[0] == fr:
                    filas.remove(item)
            fr.destroy()

        def agregar_fila(nombre="", dni="", cargo=""):
            fr = ctk.CTkFrame(f_scroll, fg_color="transparent")
            fr.pack(fill="x", padx=5, pady=2)
            e_nom = ctk.CTkEntry(fr, placeholder_text="Nombre completo", height=26)
            e_nom.pack(side="left", fill="x", expand=True, padx=(0, 5))
            e_dni = ctk.CTkEntry(fr, width=100, placeholder_text="DNI", height=26)
            e_dni.pack(side="left", padx=(0, 5))
            e_car = ctk.CTkEntry(fr, width=110, placeholder_text="Cargo", height=26)
            e_car.pack(side="left", padx=(0, 5))
            b_q = ctk.CTkButton(fr, text="✕", width=30, height=26, fg_color="#e74c3c", hover_color="#c0392b", command=lambda: quitar_fila(fr))
            b_q.pack(side="left")
            if nombre:
                e_nom.insert(0, nombre)
            if dni:
                e_dni.insert(0, dni)
            if cargo:
                e_car.insert(0, cargo)
            filas.append((fr, e_nom, e_dni, e_car))

        conn0 = conectar_db(silencioso=True)
        if conn0:
            try:
                c0 = conn0.cursor()
                c0.execute("SELECT id, tipo_respuesta, sctr_compania, sctr_poliza, sctr_desde, sctr_hasta, rep_nombre, rep_dni, archivo_sctr FROM solicitudes_proveedor WHERE codigo_cotizacion = %s AND proveedor = %s", (ev["codigo"], prov))
                r0 = c0.fetchone()
                if r0:
                    if r0[1]:
                        cmb_tipo.set(r0[1])
                    ent_sctr_comp.insert(0, r0[2] or "")
                    ent_sctr_pol.insert(0, r0[3] or "")
                    ent_sctr_desde.insert(0, r0[4] or "")
                    ent_sctr_hasta.insert(0, r0[5] or "")
                    ent_rep_nombre.insert(0, r0[6] or "")
                    ent_rep_dni.insert(0, r0[7] or "")
                    if r0[8]:
                        for una in str(r0[8]).split("|"):
                            una = una.strip()
                            if una:
                                adjuntos.append({"ruta": una, "nombre": os.path.basename(una)})
                    c0.execute("SELECT nombre_completo, dni, cargo FROM personal_proveedor WHERE solicitud_id = %s ORDER BY id", (r0[0],))
                    for nr, dr, cr in c0.fetchall():
                        agregar_fila(nr or "", dr or "", cr or "")
            except Exception:
                pass
            finally:
                liberar_conexion(conn0)
        repintar_adjuntos()
        if not filas:
            agregar_fila()

        def guardar_recepcion():
            tipo = cmb_tipo.get()
            personal = []
            for fr, e_nom, e_dni, e_car in filas:
                nom = e_nom.get().strip()
                dni = e_dni.get().strip()
                car = e_car.get().strip()
                if nom and dni:
                    personal.append((nom, dni, car))
            if "Lista" in tipo and not personal:
                messagebox.showwarning("Atención", "Agregue al menos una persona con Nombre y DNI.", parent=v)
                return
            
            cfg = cargar_config()
            ruta_base_cfg = str(cfg.get("ruta_drive", "")).strip()
            carpeta_sol = os.path.join(ruta_base_cfg, "solicitudes_sctr") if ruta_base_cfg and os.path.exists(ruta_base_cfg) else "solicitudes_sctr"
            if not os.path.exists(carpeta_sol):
                try:
                    os.makedirs(carpeta_sol)
                except Exception:
                    pass
            rutas_finales = []
            for idx, a in enumerate(adjuntos):
                origen = a["ruta"]
                if os.path.abspath(os.path.dirname(origen)) == os.path.abspath(carpeta_sol):
                    if origen not in rutas_finales:
                        rutas_finales.append(origen)
                else:
                    ext = os.path.splitext(origen)[1]
                    destino = os.path.join(carpeta_sol, f"SCTR_{prov.replace(' ', '_')}_{ev['codigo']}_{idx + 1}{ext}")
                    try:
                        shutil.copy2(origen, destino)
                        rutas_finales.append(destino)
                    except Exception:
                        rutas_finales.append(origen)
            ruta_archivo_db = "|".join(rutas_finales)
            comp = ent_sctr_comp.get().strip()
            pol = ent_sctr_pol.get().strip()
            desde = ent_sctr_desde.get().strip()
            hasta = ent_sctr_hasta.get().strip()
            repn = ent_rep_nombre.get().strip()
            repd = ent_rep_dni.get().strip()
            conn = conectar_db()
            if not conn:
                return
            try:
                c = conn.cursor()
                hoy = datetime.now().strftime("%d/%m/%Y")
                c.execute("SELECT id FROM solicitudes_proveedor WHERE codigo_cotizacion = %s AND proveedor = %s", (ev["codigo"], prov))
                row = c.fetchone()
                if row:
                    sid = row[0]
                    c.execute("""
                        UPDATE solicitudes_proveedor
                        SET estado='Recibido', tipo_respuesta=%s, fecha_recepcion=%s, archivo_sctr=%s,
                            sctr_compania=%s, sctr_poliza=%s, sctr_desde=%s, sctr_hasta=%s, rep_nombre=%s, rep_dni=%s
                        WHERE id=%s
                    """, (tipo, hoy, ruta_archivo_db, comp, pol, desde, hasta, repn, repd, sid))
                else:
                    c.execute("""
                        INSERT INTO solicitudes_proveedor
                        (codigo_cotizacion, proveedor, tipo_respuesta, estado, fecha_solicitud, fecha_recepcion, archivo_sctr,
                         sctr_compania, sctr_poliza, sctr_desde, sctr_hasta, rep_nombre, rep_dni)
                        VALUES (%s, %s, %s, 'Recibido', %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                    """, (ev["codigo"], prov, tipo, hoy, hoy, ruta_archivo_db, comp, pol, desde, hasta, repn, repd))
                    sid = c.fetchone()[0]
                c.execute("DELETE FROM personal_proveedor WHERE solicitud_id = %s", (sid,))
                for nom, dni, car in personal:
                    c.execute("INSERT INTO personal_proveedor (solicitud_id, nombre_completo, dni, cargo) VALUES (%s, %s, %s, %s)", (sid, nom, dni, car))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Solicitud Proveedores", f"Registró información recibida de {prov} ({tipo}, {len(rutas_finales)} adjunto(s)) para el evento {ev['codigo']}")
                v.destroy()
                self.cargar_proveedores_evento()
                messagebox.showinfo("Éxito", f"Información de {prov} guardada correctamente.\nAdjuntos guardados: {len(rutas_finales)}")
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar:\n{e}", parent=v)
            finally:
                liberar_conexion(conn)

    # =======================================================
    # CARTA EN PDF (LOCACIÓN O CLIENTE) + ANEXOS
    # =======================================================
    def generar_carta(self):
        ev = self.evento_data
        if not ev:
            return messagebox.showwarning("Aviso", "Seleccione primero un evento aprobado.")
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        bloques = []
        archivos_sctr = []
        try:
            c = conn.cursor()
            c.execute("""
                SELECT id, proveedor, tipo_respuesta, estado, sctr_compania, sctr_poliza, sctr_desde, sctr_hasta,
                       rep_nombre, rep_dni, archivo_sctr
                FROM solicitudes_proveedor WHERE codigo_cotizacion = %s ORDER BY proveedor
            """, (ev["codigo"],))
            sols = c.fetchall()
            if not sols:
                messagebox.showwarning("Aviso", "Aún no hay proveedores con solicitudes para este evento.\nEnvíe primero las solicitudes.")
                return
            for sid, prov, tipo, estado, comp, pol, desde, hasta, repn, repd, arch in sols:
                c.execute("SELECT nombre_completo, dni, cargo FROM personal_proveedor WHERE solicitud_id = %s ORDER BY id", (sid,))
                personal = c.fetchall()
                ruc_prov = ""
                try:
                    c.execute("SELECT ruc FROM proveedores WHERE nombre ILIKE %s LIMIT 1", (prov,))
                    rp = c.fetchone()
                    if rp:
                        ruc_prov = rp[0] or ""
                except Exception:
                    pass
                
                # Formatear el título del proveedor truncando si excede los 40 caracteres para evitar solapamiento
                titulo_prov_raw = f"{prov}" + (f" (RUC: {ruc_prov})" if ruc_prov else "")
                titulo_prov_trunc = titulo_prov_raw[:60] + "..." if len(titulo_prov_raw) > 63 else titulo_prov_raw
                
                bloques.append({
                    "prov": titulo_prov_trunc, "ruc": ruc_prov, "tipo": tipo, "estado": estado,
                    "comp": comp, "pol": pol, "desde": desde, "hasta": hasta,
                    "rep_nombre": repn, "rep_dni": repd, "personal": personal
                })
                if arch:
                    for una in str(arch).split("|"):
                        una = una.strip()
                        if una and os.path.exists(una) and una not in archivos_sctr:
                            archivos_sctr.append(una)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo leer la información:\n{e}")
            return
        finally:
            liberar_conexion(conn)
            
        cliente = ev.get("cliente", "") or ""
        loc = ev.get("locacion", "") or ""
        a_locacion = messagebox.askyesno(
            "Destino de la Carta",
            f"¿Dirigir la carta a la LOCACIÓN del evento?\n\n📍 {loc or 'Por definir'}\n\n[Sí] = Locación del evento\n[No] = Cliente ({cliente or 'por definir'})"
        )
        destinatario = (loc or "Administración de la locación del evento") if a_locacion else (cliente or "Cliente del evento")
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
            from reportlab.lib.utils import ImageReader
        except ImportError:
            return messagebox.showerror("Librería Faltante", "Falta ReportLab. Ejecuta: pip install reportlab")
            
        config = cargar_config()
        razon = str(config.get("razon_social_empresa", "")).strip()
        ruc_emp = str(config.get("ruc_empresa", "")).strip()

        def esc(t):
            return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        ruta_base = str(config.get("ruta_drive", "")).strip()
        if ruta_base and os.path.exists(ruta_base):
            carpeta = os.path.join(ruta_base, "cartas_eventos")
        else:
            carpeta = "cartas_eventos"
        if not os.path.exists(carpeta):
            try:
                os.makedirs(carpeta)
            except Exception:
                pass
                
        nombre_pdf = os.path.join(carpeta, f"Carta_{ev['codigo']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")
        ruta_base_pdf = nombre_pdf.replace(".pdf", "_base.pdf")
        
        # 🚀 CÁLCULO PROPORCIONAL DEL LOGO BORDE A BORDE CON FALLBACK
        ancho_hoja = letter[0]
        alto_hoja = letter[1]
        
        ruta_usar = None
        ruta_conf = str(config.get("ruta_logo_cotizacion", "")).strip()
        
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

        alto_banner = 0
        img_reader = None
        if ruta_usar:
            try:
                img_reader = ImageReader(ruta_usar)
                w_orig, h_orig = img_reader.getSize()
                alto_banner = ancho_hoja * (float(h_orig) / float(w_orig))
            except Exception:
                img_reader = None

        margen_base = 2 * cm
        top_margin_calc = alto_banner + 0.5 * cm if img_reader else margen_base

        styles = getSampleStyleSheet()
        st_titulo = ParagraphStyle("tit", parent=styles["Title"], fontSize=13, textColor=colors.HexColor("#1f538d"))
        st_norm = ParagraphStyle("norm", parent=styles["Normal"], fontSize=10, leading=14)
        st_chico = ParagraphStyle("chico", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#444444"))
        story = []
        
        if ruc_emp:
            story.append(Paragraph(f"RUC: {esc(ruc_emp)}", st_norm))
            story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(f"Lima, {datetime.now().strftime('%d/%m/%Y')}", st_norm))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph(f"Señores<br/><b>{esc(destinatario)}</b><br/>Presente.-", st_norm))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph(f"<b>Asunto:</b> Relación de proveedores, personal y seguros (SCTR) para el evento \"{esc(ev['nombre'])}\" del {esc(ev['fecha'] or 'fecha por definir')}.", st_norm))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph("Por medio de la presente, hacemos llegar a ustedes la relación completa de los proveedores que brindarán servicios durante el evento de la referencia, junto con la documentación de su personal y seguros correspondientes:", st_norm))
        story.append(Spacer(1, 0.5 * cm))
        
        for b in bloques:
            story.append(Paragraph(f"<b>• {b['prov']}</b>", st_norm))
            if b["estado"] == "Recibido":
                if "SCTR" in (b["tipo"] or ""):
                    partes = []
                    if b["comp"]:
                        partes.append(f"Compañía: {esc(b['comp'])}")
                    if b["pol"]:
                        partes.append(f"Póliza: {esc(b['pol'])}")
                    if b["desde"] or b["hasta"]:
                        partes.append(f"Vigencia: {esc(b['desde'])} al {esc(b['hasta'])}")
                    linea_sctr = "SCTR: " + (" | ".join(partes) if partes else "documentación recibida ✔")
                    story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;- {linea_sctr}", st_chico))
                if b["rep_nombre"]:
                    story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;- Representante: {esc(b['rep_nombre'])}" + (f" (DNI {esc(b['rep_dni'])})" if b["rep_dni"] else ""), st_chico))
                if "Lista" in (b["tipo"] or "") and b["personal"]:
                    data = [["N°", "Nombre completo", "DNI", "Cargo"]]
                    for i, (nom, dni, car) in enumerate(b["personal"], start=1):
                        data.append([str(i), esc(nom), esc(dni), esc(car or "-")])
                    t = Table(data, colWidths=[1 * cm, 7.5 * cm, 3.5 * cm, 4 * cm])
                    t.setStyle(TableStyle([
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f538d")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                    ]))
                    story.append(Spacer(1, 0.2 * cm))
                    story.append(t)
                elif "Lista" in (b["tipo"] or ""):
                    story.append(Paragraph("&nbsp;&nbsp;&nbsp;- Lista de personal: recibida (sin registros cargados).", st_chico))
            else:
                story.append(Paragraph("&nbsp;&nbsp;&nbsp;- Estado: pendiente de presentación de documentation.", st_chico))
            story.append(Spacer(1, 0.35 * cm))
            
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph("Agradecemos de antemano las atenciones brindadas para el correcto desarrollo de las actividades en sus instalaciones.", st_norm))
        if archivos_sctr:
            story.append(Spacer(1, 0.4 * cm))
            story.append(Paragraph("<b>Anexos:</b> se adjuntan en las páginas siguientes los formularios / certificados SCTR presentados por los proveedores.", st_chico))
        story.append(Spacer(1, 1 * cm))
        story.append(Paragraph("Atentamente,", st_norm))
        story.append(Spacer(1, 1.2 * cm))
        if razon:
            story.append(Paragraph(f"_____________________________<br/><b>{esc(razon)}</b>", st_norm))
        else:
            story.append(Paragraph("_____________________________", st_norm))
            
        def dibujar_encabezado(canvas_obj, doc_obj):
            if img_reader and ruta_usar:
                canvas_obj.saveState()
                y_logo = alto_hoja - alto_banner
                canvas_obj.drawImage(ruta_usar, 0, y_logo, width=ancho_hoja, height=alto_banner, mask='auto')
                canvas_obj.restoreState()

        try:
            doc = SimpleDocTemplate(ruta_base_pdf, pagesize=letter, leftMargin=margen_base, rightMargin=margen_base, topMargin=top_margin_calc, bottomMargin=margen_base, title=f"Carta Evento {ev['codigo']}")
            doc.build(story, onFirstPage=dibujar_encabezado, onLaterPages=dibujar_encabezado)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo generar la carta:\n{e}")
            return
            
        ruta_final = ruta_base_pdf
        if archivos_sctr:
            try:
                from pypdf import PdfWriter, PdfReader
                writer = PdfWriter()
                for p in PdfReader(ruta_base_pdf).pages:
                    writer.add_page(p)
                for ruta_s in archivos_sctr:
                    try:
                        for p in PdfReader(ruta_s).pages:
                            writer.add_page(p)
                    except Exception:
                        pass
                with open(nombre_pdf, "wb") as f:
                    writer.write(f)
                try:
                    os.remove(ruta_base_pdf)
                except Exception:
                    pass
                ruta_final = nombre_pdf
            except ImportError:
                messagebox.showwarning("Aviso", "Falta pypdf para adjuntar los SCTR.\nEjecuta: pip install pypdf\n\nLa carta se generó sin anexos.")
                try:
                    os.rename(ruta_base_pdf, nombre_pdf)
                    ruta_final = nombre_pdf
                except Exception:
                    ruta_final = ruta_base_pdf
        else:
            try:
                os.rename(ruta_base_pdf, nombre_pdf)
                ruta_final = nombre_pdf
            except Exception:
                ruta_final = ruta_base_pdf
                
        cache_sistema.invalidar()
        registrar_auditoria(self.usuario_activo, "Solicitud Proveedores", f"Generó carta de proveedores para el evento {ev['codigo']} dirigida a {destinatario} con {len(archivos_sctr)} anexo(s)")
        messagebox.showinfo("Éxito", f"Carta generada correctamente:\n{os.path.basename(ruta_final)}\n\nAnexos SCTR adjuntos: {len(archivos_sctr)}")
        abrir_documento(ruta_final)


if __name__ == "__main__":
    pass