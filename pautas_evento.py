# -*- coding: utf-8 -*-
"""
=========================================================
PAUTAS_EVENTO.PY (ENTERPRISE EDITION)
=========================================================
- FIX: Renderizado de Logo Borde a Borde en el PDF de Pautas (Estándar Corporativo).
- FIX: Auto-curación síncrona segura (Scope global corregido).
- FIX: Carga 100% Asíncrona de Eventos y Actividades.
- FIX: El desplegable de 'Nueva Pauta' ahora muestra todas las cotizaciones Aprobadas.
- Caché Inteligente para el filtro de Eventos.
- Uso del Pool de conexiones seguro (liberar_conexion).
- Tabla de pautas completa (Sin paginación) para mantener secuencia de hora.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import os
import sys
import json
import threading
from datetime import datetime
import io
from xml.sax.saxutils import escape

# Importación para el manejo de imágenes
from PIL import Image as PILImage

# Para la generación de PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.utils import ImageReader

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema
from app_paths import CONFIG_FILE

# Variable global definida al más alto nivel
_SCHEMA_PAUTAS_OK = False

class PautasEventoApp:
    def __init__(self, tab_frame, usuario_activo="Desconocido"):
        self.tab_frame = tab_frame
        self.usuario_activo = usuario_activo
        
        self.eventos_dict = {}
        self.item_edicion = None
        
        self.inicializar_db_pautas()
        self.crear_interfaz()

    # 🚀 FIX: AUTO-CURACIÓN EN SEGUNDO PLANO Y SCOPE GLOBAL RESUELTO
    def inicializar_db_pautas(self):
        global _SCHEMA_PAUTAS_OK
        if _SCHEMA_PAUTAS_OK: return
        
        def tarea_init():
            global _SCHEMA_PAUTAS_OK
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pautas_eventos (
                        codigo_cotizacion VARCHAR(150) PRIMARY KEY,
                        fecha_creacion VARCHAR(50)
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pautas_items (
                        id SERIAL PRIMARY KEY,
                        codigo_cotizacion VARCHAR(150) REFERENCES pautas_eventos(codigo_cotizacion) ON DELETE CASCADE,
                        hora VARCHAR(20),
                        actividad TEXT,
                        responsable VARCHAR(150)
                    )
                """)
                conn.commit()
                _SCHEMA_PAUTAS_OK = True
            except Exception as e:
                print("Error inicializando tablas de pautas:", e)
            finally:
                liberar_conexion(conn)

        threading.Thread(target=tarea_init, daemon=True).start()

    def generar_lista_horas(self):
        horas = []
        for h in range(24):
            for m in (0, 15, 30, 45):
                ampm = "AM" if h < 12 else "PM"
                h_12 = h % 12
                if h_12 == 0: h_12 = 12
                horas.append(f"{h_12:02d}:{m:02d} {ampm}")
        return horas

    def crear_interfaz(self):
        f_header = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_header.pack(fill="x", padx=15, pady=(15, 10))
        
        ctk.CTkLabel(f_header, text="🕒 PAUTAS Y MINUTO A MINUTO", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        
        self.modo_var = ctk.StringVar(value="Nueva Pauta")
        self.seg_modo = ctk.CTkSegmentedButton(
            f_header, 
            values=["Nueva Pauta", "Pautas Guardadas"], 
            variable=self.modo_var, 
            command=self.cambiar_modo,
            selected_color="#1f538d",
            selected_hover_color="#163b65"
        )
        self.seg_modo.pack(side="right", padx=10)

        f_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_split.pack(fill="both", expand=True, padx=15, pady=5)

        f_form = ctk.CTkScrollableFrame(f_split, width=320, corner_radius=10, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_form.pack(side="left", fill="y", padx=(0, 10))

        ctk.CTkLabel(f_form, text="1. Seleccionar Evento", font=("Arial", 12, "bold"), text_color="#2c3e50").pack(anchor="w", padx=15, pady=(15, 5))
        
        self.cmb_evento = ctk.CTkComboBox(f_form, state="readonly", width=280, command=self.al_seleccionar_evento)
        self.cmb_evento.pack(padx=15, pady=5)
        self.cmb_evento.set("Cargando eventos...") 

        ctk.CTkLabel(f_form, text="2. Agregar Actividad", font=("Arial", 12, "bold"), text_color="#2c3e50").pack(anchor="w", padx=15, pady=(20, 5))

        ctk.CTkLabel(f_form, text="Hora:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(5, 0))
        lista_horas = self.generar_lista_horas()
        self.cmb_hora = ttk.Combobox(f_form, values=lista_horas, state="readonly", font=("Arial", 11))
        self.cmb_hora.pack(fill="x", padx=15, pady=(0, 10))
        self.cmb_hora.set("08:00 AM")

        ctk.CTkLabel(f_form, text="Actividad / Pauta:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(5, 0))
        self.ent_actividad = ctk.CTkEntry(f_form, width=280, placeholder_text="Describa la actividad...")
        self.ent_actividad.pack(padx=15, pady=(0, 10))

        ctk.CTkLabel(f_form, text="Responsable (Opcional):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15, pady=(5, 0))
        self.ent_responsable = ctk.CTkEntry(f_form, width=280, placeholder_text="Ej: Equipo Técnico")
        self.ent_responsable.pack(padx=15, pady=(0, 15))

        f_botones_form = ctk.CTkFrame(f_form, fg_color="transparent")
        f_botones_form.pack(fill="x", padx=15, pady=5)

        self.btn_agregar = ctk.CTkButton(f_botones_form, text="➕ Agregar", font=("Arial", 11, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=self.agregar_pauta)
        self.btn_agregar.pack(side="left", expand=True, padx=2)
        
        ctk.CTkButton(f_botones_form, text="🧹 Limpiar", font=("Arial", 11, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_campos).pack(side="right", expand=True, padx=2)

        f_der = ctk.CTkFrame(f_split, fg_color="transparent")
        f_der.pack(side="right", fill="both", expand=True)

        f_tabla = ctk.CTkFrame(f_der, fg_color="transparent")
        f_tabla.pack(side="top", fill="both", expand=True)

        self.tree = ttk.Treeview(f_tabla, columns=("hora", "actividad", "responsable"), show="headings")
        self.tree.heading("hora", text="Hora")
        self.tree.heading("actividad", text="Actividad / Pauta")
        self.tree.heading("responsable", text="Responsable")

        self.tree.column("hora", width=120, anchor="center")
        self.tree.column("actividad", width=350, anchor="w")
        self.tree.column("responsable", width=150, anchor="center")

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_y.set)
        
        self.tree.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        f_acciones_rapidas = ctk.CTkFrame(f_der, fg_color="transparent")
        f_acciones_rapidas.pack(fill="x", pady=(10, 0))
        ctk.CTkButton(f_acciones_rapidas, text="✏️ Modificar Fila", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.cargar_pauta_edicion).pack(side="left", padx=5)
        ctk.CTkButton(f_acciones_rapidas, text="❌ Eliminar Fila", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_pauta).pack(side="left", padx=5)
        ctk.CTkButton(f_acciones_rapidas, text="🗑️ Vaciar Tabla", font=("Arial", 12, "bold"), fg_color="#555555", hover_color="#333333", command=self.vaciar_lista).pack(side="left", padx=5)

        self.f_modo_nueva = ctk.CTkFrame(f_der, fg_color="transparent")
        self.f_modo_guardada = ctk.CTkFrame(f_der, fg_color="transparent")

        ctk.CTkButton(self.f_modo_nueva, text="💾 GUARDAR PAUTA Y GENERAR PDF", font=("Arial", 14, "bold"), height=40, fg_color="#8e44ad", hover_color="#732d91", command=self.guardar_y_generar).pack(side="right", padx=5, pady=10)

        ctk.CTkButton(self.f_modo_guardada, text="📄 GENERAR PDF", font=("Arial", 14, "bold"), height=40, fg_color="#8e44ad", hover_color="#732d91", command=self.generar_pdf).pack(side="right", padx=5, pady=10)
        ctk.CTkButton(self.f_modo_guardada, text="🔄 ACTUALIZAR PAUTA EN BD", font=("Arial", 13, "bold"), height=40, fg_color="#27ae60", hover_color="#1e8449", command=self.actualizar_pauta_db).pack(side="right", padx=5, pady=10)
        ctk.CTkButton(self.f_modo_guardada, text="❌ ELIMINAR EVENTO COMPLETO", font=("Arial", 13, "bold"), height=40, fg_color="#c0392b", hover_color="#922b21", command=self.eliminar_pauta_completa).pack(side="left", padx=5, pady=10)

        self.cambiar_modo("Nueva Pauta")

    def cambiar_modo(self, modo):
        self.limpiar_campos()
        self.vaciar_lista_silencioso()
        
        if modo == "Nueva Pauta":
            self.f_modo_guardada.pack_forget()
            self.f_modo_nueva.pack(side="bottom", fill="x", pady=5)
            self.cargar_eventos("nuevos")
        else:
            self.f_modo_nueva.pack_forget()
            self.f_modo_guardada.pack(side="bottom", fill="x", pady=5)
            self.cargar_eventos("guardados")

    # 🚀 FIX: CARGA DE EVENTOS ASÍNCRONA + CACHÉ
    def cargar_eventos(self, filtro):
        clave_cache = f"pautas_filtro_v2_{filtro}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos:
            self._set_eventos_y_actualizar(datos["dict"], datos["lista"])
        else:
            self.cmb_evento.set("Cargando eventos...")
            def tarea():
                conn = conectar_db(silencioso=True)
                lista_nombres = []
                evt_dict = {}
                if conn:
                    try:
                        cursor = conn.cursor()
                        
                        if filtro == "nuevos":
                            query_principal = "SELECT codigo_cotizacion, nombre_evento, fecha_evento, locacion_evento, nombre_empresa FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC"
                            query_secundaria = "SELECT codigo_cotizacion, nombre_evento, fecha_evento, nombre_empresa FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC"
                        else:
                            query_principal = "SELECT c.codigo_cotizacion, c.nombre_evento, c.fecha_evento, c.locacion_evento, c.nombre_empresa FROM cotizaciones c JOIN pautas_eventos p ON c.codigo_cotizacion = p.codigo_cotizacion ORDER BY p.fecha_creacion DESC"
                            query_secundaria = "SELECT c.codigo_cotizacion, c.nombre_evento, c.fecha_evento, c.nombre_empresa FROM cotizaciones c JOIN pautas_eventos p ON c.codigo_cotizacion = p.codigo_cotizacion ORDER BY p.fecha_creacion DESC"
                        
                        try:
                            cursor.execute(query_principal)
                            filas = cursor.fetchall()
                            tiene_locacion = True
                        except Exception:
                            conn.rollback()
                            cursor.execute(query_secundaria)
                            filas = cursor.fetchall()
                            tiene_locacion = False
                        
                        for r in filas:
                            cod = r[0] if r[0] else "S/C"
                            nom = r[1] if r[1] else "Evento sin nombre"
                            fec = r[2] if r[2] else "Sin fecha"
                            
                            if tiene_locacion:
                                loc = r[3] if r[3] else "No especificada"
                                emp = r[4] if r[4] else "No especificado"
                            else:
                                loc = "No especificada"
                                emp = r[3] if r[3] else "No especificado"
                            
                            display_name = f"{nom} | {fec} | {cod}"
                            lista_nombres.append(display_name)
                            evt_dict[display_name] = {
                                "codigo": cod,
                                "evento": nom,
                                "fecha": fec,
                                "locacion": loc,
                                "empresa": emp
                            }
                        cache_sistema.guardar(clave_cache, {"lista": lista_nombres, "dict": evt_dict})
                    except Exception as e:
                        print(f"Error al cargar eventos ({filtro}):", e)
                    finally:
                        liberar_conexion(conn)
                self.tab_frame.after(0, lambda: self._set_eventos_y_actualizar(evt_dict, lista_nombres))
            threading.Thread(target=tarea, daemon=True).start()

    def _set_eventos_y_actualizar(self, evt_dict, lista_nombres):
        self.eventos_dict = evt_dict
        if lista_nombres:
            self.cmb_evento.configure(values=lista_nombres)
            self.cmb_evento.set(lista_nombres[0])
            self.al_seleccionar_evento(lista_nombres[0])
        else:
            self.cmb_evento.configure(values=["No hay eventos para mostrar"])
            self.cmb_evento.set("No hay eventos para mostrar")
            self.vaciar_lista_silencioso()

    # 🚀 FIX: CARGA DE PAUTAS ASÍNCRONA + CACHÉ
    def al_seleccionar_evento(self, choice):
        if choice not in self.eventos_dict:
            self.vaciar_lista_silencioso()
            return
            
        cod = self.eventos_dict[choice]["codigo"]
        clave_cache = f"pautas_items_{cod}"
        items = cache_sistema.obtener(clave_cache)
        
        if items is not None:
            self._llenar_tabla_bd(items)
        else:
            def tarea():
                conn = conectar_db(silencioso=True)
                items_bd = []
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT hora, actividad, responsable FROM pautas_items WHERE codigo_cotizacion = %s ORDER BY id ASC", (cod,))
                        items_bd = cursor.fetchall()
                        cache_sistema.guardar(clave_cache, items_bd)
                    except Exception as e:
                        print("Error cargando pautas del evento:", e)
                    finally:
                        liberar_conexion(conn)
                self.tab_frame.after(0, lambda: self._llenar_tabla_bd(items_bd))
            threading.Thread(target=tarea, daemon=True).start()

    def _llenar_tabla_bd(self, items):
        self.vaciar_lista_silencioso()
        for i in items:
            self.tree.insert("", tk.END, values=(i[0], i[1], i[2]))
        self.ordenar_tabla_por_hora()

    def limpiar_campos(self):
        self.item_edicion = None
        self.cmb_hora.set("08:00 AM")
        self.ent_actividad.delete(0, tk.END)
        self.ent_responsable.delete(0, tk.END)
        self.btn_agregar.configure(text="➕ Agregar", fg_color="#27ae60", hover_color="#1e8449")
        self.ent_actividad.focus()

    def ordenar_tabla_por_hora(self):
        items = [(self.tree.set(k, "hora"), k) for k in self.tree.get_children("")]
        try:
            items.sort(key=lambda t: datetime.strptime(t[0], "%I:%M %p"))
        except Exception:
            pass 
        for index, (val, k) in enumerate(items):
            self.tree.move(k, '', index)

    def agregar_pauta(self):
        hora = self.cmb_hora.get()
        actividad = self.ent_actividad.get().strip()
        responsable = self.ent_responsable.get().strip() or "-"

        if not actividad:
            return messagebox.showwarning("Atención", "La Actividad es obligatoria.", parent=self.tab_frame.winfo_toplevel())

        if self.item_edicion:
            self.tree.item(self.item_edicion, values=(hora, actividad, responsable))
        else:
            self.tree.insert("", tk.END, values=(hora, actividad, responsable))
            
        self.ordenar_tabla_por_hora()
        self.limpiar_campos()

    def cargar_pauta_edicion(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione una pauta de la tabla para editarla.", parent=self.tab_frame.winfo_toplevel())
            
        self.item_edicion = sel[0]
        valores = self.tree.item(self.item_edicion, "values")
        self.cmb_hora.set(valores[0])
        self.ent_actividad.delete(0, tk.END); self.ent_actividad.insert(0, valores[1])
        self.ent_responsable.delete(0, tk.END); self.ent_responsable.insert(0, valores[2] if valores[2] != "-" else "")
        self.btn_agregar.configure(text="✏️ Actualizar Pauta", fg_color="#1f538d", hover_color="#163b65")

    def eliminar_pauta(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showwarning("Aviso", "Seleccione una pauta de la tabla para eliminar.", parent=self.tab_frame.winfo_toplevel())
        for item in sel: self.tree.delete(item)
        self.limpiar_campos()

    def vaciar_lista(self):
        if messagebox.askyesno("Confirmar", "¿Está seguro que desea borrar toda la lista de pautas?", parent=self.tab_frame.winfo_toplevel()):
            self.vaciar_lista_silencioso()

    def vaciar_lista_silencioso(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        self.limpiar_campos()

    # =======================================================
    # OPERACIONES DE BASE DE DATOS Y CACHÉ INVALIDATION
    # =======================================================
    def guardar_y_generar(self):
        seleccion = self.cmb_evento.get()
        if seleccion not in self.eventos_dict:
            return messagebox.showwarning("Aviso", "Seleccione un evento válido.", parent=self.tab_frame.winfo_toplevel())
            
        items_tabla = self.tree.get_children()
        if not items_tabla:
            return messagebox.showwarning("Aviso", "No hay pautas para guardar.", parent=self.tab_frame.winfo_toplevel())

        cod = self.eventos_dict[seleccion]["codigo"]
        conn = conectar_db()
        if not conn: return messagebox.showwarning("Error", "Sin conexión. Imposible guardar en BD.")
        
        try:
            cursor = conn.cursor()
            
            # Buscamos si ya existía una pauta para este código y la borramos antes de insertar
            cursor.execute("DELETE FROM pautas_eventos WHERE codigo_cotizacion = %s", (cod,))
            
            fecha_hoy = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("INSERT INTO pautas_eventos (codigo_cotizacion, fecha_creacion) VALUES (%s, %s)", (cod, fecha_hoy))
            
            for item in items_tabla:
                v = self.tree.item(item, "values")
                cursor.execute("INSERT INTO pautas_items (codigo_cotizacion, hora, actividad, responsable) VALUES (%s, %s, %s, %s)", (cod, v[0], v[1], v[2]))
            conn.commit()
            cache_sistema.invalidar()
            
            exito_pdf = self.generar_pdf(abrir_auto=True)
            if exito_pdf:
                messagebox.showinfo("Éxito", "Pautas guardadas y PDF generado con éxito.", parent=self.tab_frame.winfo_toplevel())
                self.cargar_eventos("nuevos") 
                
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error SQL", str(e), parent=self.tab_frame.winfo_toplevel())
        finally:
            liberar_conexion(conn)

    def actualizar_pauta_db(self):
        seleccion = self.cmb_evento.get()
        if seleccion not in self.eventos_dict: return
        cod = self.eventos_dict[seleccion]["codigo"]
        items_tabla = self.tree.get_children()
        
        conn = conectar_db()
        if not conn: return messagebox.showwarning("Error", "Sin conexión a internet.", parent=self.tab_frame.winfo_toplevel())
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM pautas_items WHERE codigo_cotizacion = %s", (cod,))
            for item in items_tabla:
                v = self.tree.item(item, "values")
                cursor.execute("INSERT INTO pautas_items (codigo_cotizacion, hora, actividad, responsable) VALUES (%s, %s, %s, %s)", (cod, v[0], v[1], v[2]))
            conn.commit()
            cache_sistema.invalidar()
            messagebox.showinfo("Éxito", "Pautas actualizadas en la base de datos.", parent=self.tab_frame.winfo_toplevel())
        except Exception as e:
            messagebox.showerror("Error", str(e), parent=self.tab_frame.winfo_toplevel())
        finally:
            liberar_conexion(conn)

    def eliminar_pauta_completa(self):
        seleccion = self.cmb_evento.get()
        if seleccion not in self.eventos_dict: return
        cod = self.eventos_dict[seleccion]["codigo"]
        
        if messagebox.askyesno("⚠️ CUIDADO", f"¿Estás seguro de eliminar PERMANENTEMENTE toda la pauta del evento:\n{cod}?", parent=self.tab_frame.winfo_toplevel()):
            conn = conectar_db()
            if not conn: return messagebox.showwarning("Error", "Sin conexión a internet.", parent=self.tab_frame.winfo_toplevel())
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM pautas_eventos WHERE codigo_cotizacion = %s", (cod,))
                conn.commit()
                cache_sistema.invalidar()
                messagebox.showinfo("Éxito", "Pauta eliminada correctamente.", parent=self.tab_frame.winfo_toplevel())
                self.cargar_eventos("guardados")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=self.tab_frame.winfo_toplevel())
            finally:
                liberar_conexion(conn)

    # =======================================================
    # GENERADOR DE PDF PROFESIONAL AUTOMATIZADO CON LOGO BORDE A BORDE
    # =======================================================
    def obtener_configuracion_local(self):
        config = {
            "ruta_logo": "", 
            "color_primario": "#1f538d", 
            "razon_social": "Nuestra Empresa",
            "ruta_drive": ""
        }
        try:
            if os.path.exists(str(CONFIG_FILE)):
                with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                    data = json.load(f)
                    config["ruta_logo"] = data.get("ruta_logo_cotizacion", "")
                    config["color_primario"] = data.get("color_franja", "#1f538d")
                    config["razon_social"] = data.get("razon_social_empresa", "Nuestra Empresa")
                    config["ruta_drive"] = data.get("ruta_drive", "")
        except Exception: pass
        return config

    def generar_pdf(self, abrir_auto=False):
        parent_window = self.tab_frame.winfo_toplevel()
        seleccion = self.cmb_evento.get()
        if seleccion not in self.eventos_dict:
            messagebox.showwarning("Aviso", "Debe seleccionar un evento válido para generar el PDF.", parent=parent_window)
            return False
            
        items_tabla = self.tree.get_children()
        if not items_tabla:
            messagebox.showwarning("Aviso", "No hay pautas en la tabla para generar el documento.", parent=parent_window)
            return False

        datos_evento = self.eventos_dict[seleccion]
        cfg = self.obtener_configuracion_local()
        ruta_base = cfg.get("ruta_drive", "").strip()

        cod_limpio = str(datos_evento['codigo']).replace("/", "-").replace("\\", "-")
        nombre_default = f"Pautas_{cod_limpio}.pdf"
        
        try:
            if ruta_base and os.path.exists(ruta_base):
                carpeta_pautas = os.path.join(ruta_base, "Pautas")
                if not os.path.exists(carpeta_pautas):
                    os.makedirs(carpeta_pautas)
                ruta_guardado = os.path.join(carpeta_pautas, nombre_default)
            else:
                ruta_guardado = filedialog.asksaveasfilename(
                    parent=parent_window,
                    defaultextension=".pdf",
                    initialfile=nombre_default,
                    title="Guardar Pauta en PDF",
                    filetypes=[("PDF files", "*.pdf")]
                )
                if not ruta_guardado: return False
        except Exception as e:
            print("Error definiendo la ruta de guardado:", e)
            return False

        try:
            ancho_hoja = letter[0]
            alto_hoja = letter[1]
            margin = 40
            available_width = ancho_hoja - (margin * 2)

            ruta_usar = None
            ruta_conf = str(cfg.get("ruta_logo", "")).strip()
            
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

            top_margin_calc = alto_banner + 15 if img_reader else margin

            doc = SimpleDocTemplate(ruta_guardado, pagesize=letter, rightMargin=margin, leftMargin=margin, topMargin=top_margin_calc, bottomMargin=margin)
            elementos = []
            estilos = getSampleStyleSheet()

            estilo_titulo = ParagraphStyle(
                'TituloPrincipal', parent=estilos['Heading1'], fontSize=16,
                textColor=colors.HexColor(cfg["color_primario"]), alignment=TA_CENTER, spaceAfter=15
            )
            estilo_info = ParagraphStyle(
                'InfoEvento', parent=estilos['Normal'], fontSize=11, spaceAfter=5
            )

            elementos.append(Paragraph(f"<b>MINUTO A MINUTO / PAUTA DE EVENTO</b>", estilo_titulo))
            
            info_texto = f"""
            <b>N° de Pauta:</b> {datos_evento['codigo']}<br/>
            <b>Fecha Programada:</b> {datos_evento['fecha']}<br/>
            <b>Cliente:</b> {datos_evento['empresa']}<br/>
            <b>Evento:</b> {datos_evento['evento']}<br/>
            <b>Locación:</b> {datos_evento['locacion']}
            """
            elementos.append(Paragraph(info_texto, estilo_info))
            elementos.append(Spacer(1, 20))

            datos_tabla = [["HORA", "ACTIVIDAD / PAUTA", "RESPONSABLE"]]
            
            for item in items_tabla:
                valores = self.tree.item(item, "values")
                texto_actividad = escape(str(valores[1])) 
                datos_tabla.append([valores[0], Paragraph(texto_actividad, estilos['Normal']), valores[2]])

            w_hora = available_width * 0.15
            w_act  = available_width * 0.60
            w_resp = available_width * 0.25

            t = Table(datos_tabla, colWidths=[w_hora, w_act, w_resp])
            
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(cfg["color_primario"])),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 11),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
                ('ALIGN', (0, 1), (0, -1), 'CENTER'),
                ('ALIGN', (2, 1), (2, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white])
            ]))
            
            elementos.append(t)

            def dibujar_encabezado(canvas_obj, doc_obj):
                if img_reader and ruta_usar:
                    canvas_obj.saveState()
                    y_logo = alto_hoja - alto_banner
                    canvas_obj.drawImage(ruta_usar, 0, y_logo, width=ancho_hoja, height=alto_banner, mask='auto')
                    canvas_obj.restoreState()

            doc.build(elementos, onFirstPage=dibujar_encabezado, onLaterPages=dibujar_encabezado)
            
            registrar_auditoria(self.usuario_activo, "Pautas", f"Generó PDF de Pautas para cotización {datos_evento['codigo']}")
            
            if sys.platform == "win32": os.startfile(ruta_guardado)
            elif sys.platform == "darwin": subprocess.call(["open", ruta_guardado])
            else: subprocess.call(["xdg-open", ruta_guardado])
            
            return True

        except Exception as e:
            messagebox.showerror("Error al generar PDF", f"Hubo un problema al crear el archivo:\n{e}", parent=parent_window)
            return False

if __name__ == "__main__":
    pass