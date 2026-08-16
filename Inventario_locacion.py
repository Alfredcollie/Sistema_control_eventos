# -*- coding: utf-8 -*-
"""
INVENTARIO_LOCACION.PY (ENTERPRISE EDITION)
- Paginación Lazy Loading (50 en 50) para el Catálogo y Reservas.
- Búsqueda Asíncrona en las pestañas.
- Caché Inteligente en Combobox de Eventos.
- Protección del Pool de Conexiones (liberar_conexion).
- Auto-curación síncrona en segundo plano.
"""

import os
import shutil
import sys 
import subprocess 
import time
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFont, ImageTk
import calendar
from datetime import datetime

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

_SCHEMA_LOC_OK = False

# =========================================================
# 🚀 CALENDARIO COMPARTIDO
# =========================================================
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
            if int(w.grid_info()["row"]) > 0: w.destroy()
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
        if self.current_month < 1: self.current_month = 12; self.current_year -= 1
        self.update_calendar()
        
    def next_month(self):
        self.current_month += 1
        if self.current_month > 12: self.current_month = 1; self.current_year += 1
        self.update_calendar()
        
    def select_date(self, day):
        fecha_sel = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, fecha_sel)
        self.destroy()

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA
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
        messagebox.showerror("Error", f"No se pudo abrir la imagen:\n{e}")

def maximizar_ventana(ventana):
    if sys.platform == "win32":
        try:
            ventana.state("zoomed")
        except Exception:
            pass
    else:
        try:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

def aplicar_estilo_treeview():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=28, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000", font=("Arial", 10, "bold"), bordercolor="#e0e0e0", borderwidth=1, relief="flat")


# =========================================================
# PESTAÑA 1: CATÁLOGO Y GALERÍA DE LOCACIONES
# =========================================================
class CatalogoLocacionesTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        
        self.id_locacion_seleccionada = None
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.carpeta_originales = os.path.join("locaciones_img", "fotos_originales")
        self.carpeta_medidas = os.path.join("locaciones_img", "fotos_medidas")
        os.makedirs(self.carpeta_originales, exist_ok=True)
        os.makedirs(self.carpeta_medidas, exist_ok=True)
            
        self.crear_interfaz()

    def abrir_calendario_locacion(self, entry_objetivo):
        CalendarioNativo(self.main_root, entry_objetivo)

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=340, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        ctk.CTkLabel(self.f_form, text="Datos de la Locación", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(10, 15))

        ctk.CTkLabel(self.f_form, text="Nombre del Lugar:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_nombre = ctk.CTkEntry(self.f_form, placeholder_text="Ej: Hacienda Los Ficus")
        self.ent_nombre.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Persona de Contacto:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_contacto = ctk.CTkEntry(self.f_form, placeholder_text="Ej: María Pérez")
        self.ent_contacto.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Número de Contacto:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_telefono = ctk.CTkEntry(self.f_form, placeholder_text="Ej: 987654321")
        self.ent_telefono.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Dirección Exacta:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_direccion = ctk.CTkEntry(self.f_form)
        self.ent_direccion.pack(fill="x", padx=10, pady=(0, 10))

        f_nums = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_nums.pack(fill="x", padx=10, pady=(0, 10))
        
        ctk.CTkLabel(f_nums, text="Aforo (Pers):", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky="w", padx=(0,5))
        self.ent_aforo = ctk.CTkEntry(f_nums, width=80); self.ent_aforo.grid(row=1, column=0, sticky="w", padx=(0,5))
        self.ent_aforo.insert(0, "0")

        ctk.CTkLabel(f_nums, text="Área (m²):", font=("Arial", 11, "bold")).grid(row=0, column=1, sticky="w", padx=5)
        self.ent_area = ctk.CTkEntry(f_nums, width=80); self.ent_area.grid(row=1, column=1, sticky="w", padx=5)
        self.ent_area.insert(0, "0.0")

        ctk.CTkLabel(f_nums, text="Último Precio:", font=("Arial", 11, "bold")).grid(row=0, column=2, sticky="w", padx=(5,0))
        self.ent_precio = ctk.CTkEntry(f_nums, width=100, placeholder_text="S/."); self.ent_precio.grid(row=1, column=2, sticky="w", padx=(5,0))
        self.ent_precio.insert(0, "0.0")

        ctk.CTkLabel(self.f_form, text="Fecha del Último Precio:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=(5,0))
        f_fec = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fec.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_fecha_precio = ctk.CTkEntry(f_fec, placeholder_text="DD/MM/AAAA")
        self.ent_fecha_precio.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fec, text="[ 📅 ]", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario_locacion(self.ent_fecha_precio)).pack(side="right", padx=(5,0))

        ctk.CTkLabel(self.f_form, text="¿Requiere Permisos Municipales/Especiales?", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=(10,0))
        self.var_permisos = ctk.StringVar(value="No")
        self.cmb_permisos = ctk.CTkComboBox(self.f_form, values=["No", "Sí"], variable=self.var_permisos, command=self.toggle_permisos)
        self.cmb_permisos.pack(fill="x", padx=10, pady=(0, 5))
        
        self.f_txt_permisos = ctk.CTkFrame(self.f_form, fg_color="transparent")
        ctk.CTkLabel(self.f_txt_permisos, text="Detalle los permisos requeridos:", font=("Arial", 10)).pack(anchor="w")
        self.txt_permisos = ctk.CTkTextbox(self.f_txt_permisos, height=60, border_width=1)
        self.txt_permisos.pack(fill="x", pady=(0,5))

        ctk.CTkLabel(self.f_form, text="¿Cuenta con Estacionamiento?", font=("Arial", 11, "bold")).pack(anchor="w", padx=10, pady=(10,0))
        self.var_est = ctk.StringVar(value="No")
        self.cmb_estacionamiento = ctk.CTkComboBox(self.f_form, values=["No", "Sí"], variable=self.var_est, command=self.toggle_estacionamiento)
        self.cmb_estacionamiento.pack(fill="x", padx=10, pady=(0, 5))

        self.f_cant_est = ctk.CTkFrame(self.f_form, fg_color="transparent")
        ctk.CTkLabel(self.f_cant_est, text="Cantidad de puestos:", font=("Arial", 10)).pack(side="left", padx=(0,5))
        self.ent_cant_est = ctk.CTkEntry(self.f_cant_est, width=80)
        self.ent_cant_est.pack(side="left")
        self.ent_cant_est.insert(0, "0")

        self.btn_guardar = ctk.CTkButton(self.f_form, text="💾 Guardar Locación", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_locacion)
        self.btn_guardar.pack(fill="x", padx=10, pady=(20, 5))

        self.btn_limpiar = ctk.CTkButton(self.f_form, text="🧹 Limpiar Formulario", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_formulario)
        self.btn_limpiar.pack(fill="x", padx=10, pady=5)

        self.f_wrapper_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_wrapper_derecha.pack(side="right", fill="both", expand=True)

        ctk.CTkLabel(self.f_wrapper_derecha, text="Directorio de Locaciones Guardadas", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 5))

        f_busqueda = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_catalogo = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por nombre, contacto, dirección, aforo...")
        self.ent_buscar_catalogo.pack(side="left", fill="x", expand=True)
        
        # 🚀 BÚSQUEDA ASÍNCRONA
        self.ent_buscar_catalogo.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_buscar_catalogo.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "nombre", "contacto", "telefono", "aforo", "m2", "precio", "fecha_precio", "permisos", "estacionamiento", "direccion")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", style="Treeview")
        
        self.tabla.heading("id", text="ID")
        self.tabla.heading("nombre", text="Nombre de la Locación")
        self.tabla.heading("contacto", text="Contacto")
        self.tabla.heading("telefono", text="Teléfono")
        self.tabla.heading("aforo", text="Aforo")
        self.tabla.heading("m2", text="Área m²")
        self.tabla.heading("precio", text="Último Precio")
        self.tabla.heading("fecha_precio", text="Fecha Precio")
        self.tabla.heading("permisos", text="Permisos")
        self.tabla.heading("estacionamiento", text="Estacionamiento")
        
        self.tabla.column("id", width=40, anchor="center")
        self.tabla.column("nombre", width=180, anchor="w")
        self.tabla.column("contacto", width=120, anchor="w")
        self.tabla.column("telefono", width=90, anchor="center")
        self.tabla.column("aforo", width=60, anchor="center")
        self.tabla.column("m2", width=70, anchor="center")
        self.tabla.column("precio", width=90, anchor="e")
        self.tabla.column("fecha_precio", width=90, anchor="center")
        self.tabla.column("permisos", width=70, anchor="center")
        self.tabla.column("estacionamiento", width=110, anchor="center")
        
        self.tabla.config(displaycolumns=("id", "nombre", "contacto", "telefono", "aforo", "m2", "precio", "fecha_precio", "permisos", "estacionamiento"))

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        self.tabla.bind("<<TreeviewSelect>>", self.al_seleccionar_tabla)

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
        
        btn_fotos = ctk.CTkButton(f_btn_tabla, text="📸 Ver / Agregar Fotos y Medidas", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.abrir_galeria)
        btn_fotos.pack(side="left", padx=(10, 10))

        btn_eliminar = ctk.CTkButton(f_btn_tabla, text="❌ Eliminar Locación", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_locacion)
        btn_eliminar.pack(side="right")

        self.main_root.after(150, lambda: self.cargar_tabla(reset_pagina=True))

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
                self.main_root.after_cancel(self._busqueda_job)
            except Exception:
                pass
        self._busqueda_job = self.main_root.after(350, lambda: self.cargar_tabla(reset_pagina=True))

    def toggle_permisos(self, valor):
        if valor == "Sí": self.f_txt_permisos.pack(fill="x", padx=10, pady=(0, 10), after=self.cmb_permisos)
        else: self.f_txt_permisos.pack_forget()

    def toggle_estacionamiento(self, valor):
        if valor == "Sí": self.f_cant_est.pack(fill="x", padx=10, pady=(0, 10), after=self.cmb_estacionamiento)
        else: self.f_cant_est.pack_forget()

    def limpiar_formulario(self):
        self.id_locacion_seleccionada = None
        self.ent_nombre.delete(0, tk.END)
        self.ent_contacto.delete(0, tk.END)
        self.ent_telefono.delete(0, tk.END)
        self.ent_direccion.delete(0, tk.END)
        self.ent_aforo.delete(0, tk.END); self.ent_aforo.insert(0, "0")
        self.ent_area.delete(0, tk.END); self.ent_area.insert(0, "0.0")
        self.ent_precio.delete(0, tk.END); self.ent_precio.insert(0, "0.0")
        self.ent_fecha_precio.delete(0, tk.END)
        
        self.var_permisos.set("No")
        self.toggle_permisos("No")
        self.txt_permisos.delete("1.0", tk.END)
        
        self.var_est.set("No")
        self.toggle_estacionamiento("No")
        self.ent_cant_est.delete(0, tk.END); self.ent_cant_est.insert(0, "0")
        
        self.btn_guardar.configure(text="💾 Guardar Locación", fg_color="#1f538d", hover_color="#163b65")

    def al_seleccionar_tabla(self, event):
        sel = self.tabla.selection()
        if not sel: return
        
        valores = self.tabla.item(sel[0], "values")
        self.id_locacion_seleccionada = valores[0]
        
        self.ent_nombre.delete(0, tk.END); self.ent_nombre.insert(0, valores[1])
        self.ent_contacto.delete(0, tk.END); self.ent_contacto.insert(0, valores[2])
        self.ent_telefono.delete(0, tk.END); self.ent_telefono.insert(0, valores[3])
        self.ent_aforo.delete(0, tk.END); self.ent_aforo.insert(0, valores[4])
        self.ent_area.delete(0, tk.END); self.ent_area.insert(0, str(valores[5]).replace(" m²", ""))
        self.ent_precio.delete(0, tk.END); self.ent_precio.insert(0, str(valores[6]).replace("S/. ", "").replace(",", ""))
        
        self.ent_fecha_precio.delete(0, tk.END)
        if len(valores) > 7 and valores[7] != "None":
            self.ent_fecha_precio.insert(0, str(valores[7]))
            
        self.ent_direccion.delete(0, tk.END); self.ent_direccion.insert(0, valores[10])

        conn = conectar_db(silencioso=True)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT requiere_permisos, detalle_permisos, tiene_estacionamiento, cantidad_estacionamiento FROM locaciones WHERE id = %s", (self.id_locacion_seleccionada,))
                res = cursor.fetchone()
                if res:
                    req_perm, det_perm, tiene_est, cant_est = res
                    self.var_permisos.set(req_perm)
                    self.toggle_permisos(req_perm)
                    self.txt_permisos.delete("1.0", tk.END)
                    if det_perm: self.txt_permisos.insert("1.0", det_perm)
                    
                    self.var_est.set(tiene_est)
                    self.toggle_estacionamiento(tiene_est)
                    self.ent_cant_est.delete(0, tk.END); self.ent_cant_est.insert(0, str(cant_est))
            except Exception: pass
            finally: liberar_conexion(conn)

        self.btn_guardar.configure(text="✏️ Actualizar Locación", fg_color="#34495e", hover_color="#2c3e50")

    def guardar_locacion(self):
        nom = self.ent_nombre.get().strip()
        cont = self.ent_contacto.get().strip()
        tel = self.ent_telefono.get().strip()
        dir_val = self.ent_direccion.get().strip()
        fecha_pr = self.ent_fecha_precio.get().strip()
        
        try:
            aforo = int(self.ent_aforo.get().strip() or 0)
            area = float(self.ent_area.get().strip() or 0.0)
            precio = float(self.ent_precio.get().strip() or 0.0)
            cant_est = int(self.ent_cant_est.get().strip() or 0)
        except ValueError:
            return messagebox.showerror("Error", "Aforo, Área, Precio y Cantidad de Estacionamiento deben ser numéricos.")

        req_perm = self.var_permisos.get()
        det_perm = self.txt_permisos.get("1.0", "end-1c").strip() if req_perm == "Sí" else ""
        tiene_est = self.var_est.get()
        if tiene_est == "No": cant_est = 0

        if not nom: return messagebox.showwarning("Atención", "El nombre de la locación es obligatorio.")

        conn = conectar_db()
        if not conn:
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden guardar locaciones en Modo Lectura.")
        try:
            cursor = conn.cursor()
            if self.id_locacion_seleccionada:
                cursor.execute("""
                    UPDATE locaciones 
                    SET nombre=%s, contacto=%s, telefono=%s, direccion=%s, aforo=%s, precio_ultimo=%s, area_m2=%s, 
                        requiere_permisos=%s, detalle_permisos=%s, tiene_estacionamiento=%s, cantidad_estacionamiento=%s, fecha_precio=%s
                    WHERE id=%s
                """, (nom, cont, tel, dir_val, aforo, precio, area, req_perm, det_perm, tiene_est, cant_est, fecha_pr, self.id_locacion_seleccionada))
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Modificó la locación: {nom}")
                messagebox.showinfo("Éxito", "Locación actualizada correctamente.")
            else:
                cursor.execute("""
                    INSERT INTO locaciones (nombre, contacto, telefono, direccion, aforo, precio_ultimo, area_m2, requiere_permisos, detalle_permisos, tiene_estacionamiento, cantidad_estacionamiento, fecha_precio)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (nom, cont, tel, dir_val, aforo, precio, area, req_perm, det_perm, tiene_est, cant_est, fecha_pr))
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Registró nueva locación: {nom}")
                messagebox.showinfo("Éxito", "Locación registrada correctamente.")
                
            conn.commit()
            cache_sistema.invalidar()
            self.limpiar_formulario()
            self.cargar_tabla(reset_pagina=True)
            
            if hasattr(self.app_padre, 'app_reservas'):
                self.app_padre.app_reservas.cargar_combos()
        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    # 🚀 FIX: LAZY LOADING Y CACHÉ
    def cargar_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        filtro = self.ent_buscar_catalogo.get().strip().lower() if hasattr(self, 'ent_buscar_catalogo') else ""
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"locaciones_cat_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos)
        else:
            self.tabla.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", "", "", "", ""))
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            cursor.execute("SELECT id, nombre, contacto, telefono, aforo, area_m2, precio_ultimo, requiere_permisos, tiene_estacionamiento, cantidad_estacionamiento, direccion, fecha_precio FROM locaciones ORDER BY nombre ASC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            cursor.execute("SELECT id, nombre, contacto, telefono, aforo, area_m2, precio_ultimo, requiere_permisos, tiene_estacionamiento, cantidad_estacionamiento, direccion, fecha_precio FROM locaciones WHERE nombre ILIKE %s OR contacto ILIKE %s OR direccion ILIKE %s ORDER BY nombre ASC LIMIT %s OFFSET %s", (val, val, val, self.registros_por_pagina, offset))
                            
                        for r in cursor.fetchall():
                            est_txt = "No" if r[8] == "No" else f"Sí ({r[9]} puestos)"
                            precio_fmt = f"S/. {float(r[6]):,.2f}"
                            fec_pr = r[11] if r[11] else ""
                            rows.append((r[0], r[1], r[2], r[3], r[4], f"{r[5]} m²", precio_fmt, fec_pr, r[7], est_txt, r[10]))
                            
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                self.main_root.after(0, lambda: self._pintar_tabla(rows))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_tabla(self, rows):
        for row in self.tabla.get_children(): self.tabla.delete(row)
        for r in rows:
            self.tabla.insert("", tk.END, values=r)
            
        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")
            
        if len(rows) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")

    def eliminar_locacion(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Atención", "Seleccione una locación de la tabla.")
        id_loc = self.tabla.item(sel[0], "values")[0]
        nom = self.tabla.item(sel[0], "values")[1]

        if messagebox.askyesno("Confirmar", f"¿Eliminar la locación '{nom}' y TODAS sus fotos permanentemente?"):
            conn = conectar_db()
            if not conn:
                return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar locaciones en Modo Lectura.")
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT ruta_foto, ruta_foto_medidas FROM locaciones_fotos WHERE locacion_id = %s", (id_loc,))
                for (r_orig, r_med) in cursor.fetchall():
                    if r_orig and os.path.exists(r_orig):
                        try: os.remove(r_orig)
                        except: pass
                    if r_med and os.path.exists(r_med):
                        try: os.remove(r_med)
                        except: pass
                
                cursor.execute("DELETE FROM locaciones WHERE id = %s", (id_loc,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Eliminó la locación: {nom}")
                self.limpiar_formulario()
                self.cargar_tabla(reset_pagina=True)
                
                if hasattr(self.app_padre, 'app_reservas'):
                    self.app_padre.app_reservas.cargar_tabla(reset_pagina=True)
                    
                messagebox.showinfo("Éxito", "Locación eliminada.")
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)

    def abrir_visor_carrusel(self, parent_window, lista_rutas, indice_inicial):
        if not lista_rutas: return
        
        rutas_validas = [r for r in lista_rutas if os.path.exists(r)]
        if not rutas_validas:
            return messagebox.showwarning("Error", "Las imágenes ya no se encuentran en el disco duro.", parent=parent_window)
            
        idx_actual = 0
        if lista_rutas[indice_inicial] in rutas_validas:
            idx_actual = rutas_validas.index(lista_rutas[indice_inicial])

        visor = ctk.CTkToplevel(parent_window)
        visor.title("Visor de Imágenes")
        visor.attributes("-fullscreen", True)
        visor.configure(fg_color="black")
        visor.transient(parent_window)
        visor.grab_set()

        screen_w = visor.winfo_screenwidth()
        screen_h = visor.winfo_screenheight()

        f_top = ctk.CTkFrame(visor, fg_color="transparent", height=40)
        f_top.pack(fill="x", side="top")
        
        lbl_contador = ctk.CTkLabel(f_top, text="", font=("Arial", 14, "bold"), text_color="white")
        lbl_contador.pack(side="left", padx=20, pady=10)
        
        btn_cerrar = ctk.CTkButton(f_top, text="❌ Cerrar Visor (Esc)", font=("Arial", 12, "bold"), width=120, fg_color="#e74c3c", hover_color="#c0392b", command=visor.destroy)
        btn_cerrar.pack(side="right", padx=20, pady=10)

        f_center = ctk.CTkFrame(visor, fg_color="transparent")
        f_center.pack(expand=True, fill="both")

        lbl_img = ctk.CTkLabel(f_center, text="")
        lbl_img.pack(expand=True)

        estado_visor = {"index": idx_actual}

        def renderizar_imagen():
            ruta = rutas_validas[estado_visor["index"]]
            try:
                img = Image.open(ruta)
                img.thumbnail((int(screen_w * 0.9), int(screen_h * 0.9)), Image.Resampling.LANCZOS)
                ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
                lbl_img.configure(image=ctk_img)
                lbl_contador.configure(text=f"Foto {estado_visor['index'] + 1} de {len(rutas_validas)}")
            except Exception:
                lbl_img.configure(image=None, text="Error al cargar imagen", text_color="white")

        def img_siguiente(e=None):
            if estado_visor["index"] < len(rutas_validas) - 1:
                estado_visor["index"] += 1
                renderizar_imagen()

        def img_anterior(e=None):
            if estado_visor["index"] > 0:
                estado_visor["index"] -= 1
                renderizar_imagen()

        btn_izq = ctk.CTkButton(f_center, text="<", font=("Arial", 24, "bold"), width=50, height=screen_h, fg_color="transparent", hover_color="#333333", command=img_anterior)
        btn_izq.place(relx=0.0, rely=0.5, anchor="w")
        
        btn_der = ctk.CTkButton(f_center, text=">", font=("Arial", 24, "bold"), width=50, height=screen_h, fg_color="transparent", hover_color="#333333", command=img_siguiente)
        btn_der.place(relx=1.0, rely=0.5, anchor="e")

        visor.bind("<Escape>", lambda e: visor.destroy())
        visor.bind("<Right>", img_siguiente)
        visor.bind("<Left>", img_anterior)
        lbl_img.bind("<Double-1>", lambda e: visor.destroy())

        renderizar_imagen()

    def abrir_editor_medidas(self, parent_window, fid, ruta_orig, ruta_med, callback_actualizar):
        base_ruta = ruta_med if (ruta_med and os.path.exists(ruta_med)) else ruta_orig
        if not base_ruta or not os.path.exists(base_ruta):
            return messagebox.showwarning("Error", "La imagen no existe o fue borrada.", parent=parent_window)

        editor = ctk.CTkToplevel(parent_window)
        editor.title("Editor Interactivo de Medidas")
        editor.geometry("900x700")
        editor.transient(parent_window)
        editor.grab_set()

        try:
            imagen_original = Image.open(base_ruta).convert("RGB")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir la imagen:\n{e}", parent=parent_window)
            editor.destroy()
            return

        f_top = ctk.CTkFrame(editor, fg_color="transparent")
        f_top.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(f_top, text="🖌️ Instrucciones: Haz clic y arrastra el mouse sobre la foto para dibujar la medida.", font=("Arial", 13, "bold"), text_color="#1f538d").pack(side="left")
        
        def guardar_cambios():
            try:
                nombre_base = f"Medidas_{fid}_{int(time.time())}.jpg"
                nueva_ruta_medidas = os.path.join(self.carpeta_medidas, nombre_base)
                
                imagen_original.save(nueva_ruta_medidas, quality=100)
                
                conn = conectar_db()
                if not conn:
                    return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden guardar medidas en Modo Lectura.", parent=editor)
                
                c = conn.cursor()
                c.execute("UPDATE locaciones_fotos SET ruta_foto_medidas = %s WHERE id = %s", (nueva_ruta_medidas, fid))
                conn.commit()
                liberar_conexion(conn)
                
                if ruta_med and os.path.exists(ruta_med) and ruta_med != nueva_ruta_medidas:
                    try: os.remove(ruta_med)
                    except: pass

                messagebox.showinfo("Éxito", "Medidas guardadas correctamente. El archivo original no fue modificado.", parent=editor)
                callback_actualizar()
                editor.destroy()
            except Exception as e:
                messagebox.showerror("Error", f"Fallo al guardar:\n{e}", parent=editor)

        ctk.CTkButton(f_top, text="💾 Guardar Copia con Medidas", fg_color="#27ae60", hover_color="#1e8449", command=guardar_cambios).pack(side="right")

        f_canvas = ctk.CTkFrame(editor)
        f_canvas.pack(fill="both", expand=True, padx=10, pady=10)
        
        canvas = tk.Canvas(f_canvas, cursor="crosshair", bg="#2c3e50", highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        estado = {
            "escala": 1.0, "offset_x": 0, "offset_y": 0, "tk_img": None,
            "start_x": None, "start_y": None, "linea_tmp": None
        }

        def redimensionar(event=None):
            w = canvas.winfo_width()
            h = canvas.winfo_height()
            if w <= 1 or h <= 1: return
            
            img_w, img_h = imagen_original.size
            escala = min(w / img_w, h / img_h)
            estado["escala"] = escala
            
            new_w = int(img_w * escala)
            new_h = int(img_h * escala)
            
            img_res = imagen_original.resize((new_w, new_h), Image.Resampling.LANCZOS)
            estado["tk_img"] = ImageTk.PhotoImage(img_res)
            
            estado["offset_x"] = (w - new_w) // 2
            estado["offset_y"] = (h - new_h) // 2
            
            canvas.delete("img")
            canvas.create_image(estado["offset_x"], estado["offset_y"], anchor="nw", image=estado["tk_img"], tags="img")
            canvas.tag_lower("img")

        canvas.bind("<Configure>", redimensionar)

        def click(event):
            estado["start_x"] = event.x
            estado["start_y"] = event.y

        def drag(event):
            if estado["linea_tmp"]: canvas.delete(estado["linea_tmp"])
            estado["linea_tmp"] = canvas.create_line(estado["start_x"], estado["start_y"], event.x, event.y, fill="#e74c3c", width=3)

        def release(event):
            if estado["linea_tmp"]:
                canvas.delete(estado["linea_tmp"])
                estado["linea_tmp"] = None
            
            end_x = event.x
            end_y = event.y
            
            if abs(end_x - estado["start_x"]) < 5 and abs(end_y - estado["start_y"]) < 5: return 
                
            medida = simpledialog.askstring("Ingresar Medida", "Escribe el valor de esta medida (Ej: 15 metros):", parent=editor)
            if not medida: return
            
            draw = ImageDraw.Draw(imagen_original)
            
            ox1 = (estado["start_x"] - estado["offset_x"]) / estado["escala"]
            oy1 = (estado["start_y"] - estado["offset_y"]) / estado["escala"]
            ox2 = (end_x - estado["offset_x"]) / estado["escala"]
            oy2 = (end_y - estado["offset_y"]) / estado["escala"]
            
            grosor = max(3, int(4 / estado["escala"]))
            draw.line([(ox1, oy1), (ox2, oy2)], fill="#e74c3c", width=grosor)
            
            mid_x = (ox1 + ox2) / 2
            mid_y = (oy1 + oy2) / 2
            
            tam_fuente = max(16, int(20 / estado["escala"]))
            try: font = ImageFont.truetype("arialbd.ttf", tam_fuente)
            except:
                try: font = ImageFont.truetype("arial.ttf", tam_fuente)
                except: font = ImageFont.load_default()
                
            try:
                bbox = draw.textbbox((0, 0), medida, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                tx = mid_x - tw/2
                ty = mid_y - th/2
            except AttributeError:
                tw, th = draw.textsize(medida, font=font)
                tx = mid_x - tw/2
                ty = mid_y - th/2

            margen = int(5 / estado["escala"])
            draw.rectangle([tx-margen, ty-margen, tx+tw+margen, ty+th+margen], fill="white", outline="#1f538d", width=max(1, int(2/estado["escala"])))
            draw.text((tx, ty), medida, fill="#1f538d", font=font)
            
            redimensionar() 
            
        canvas.bind("<ButtonPress-1>", click)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", release)

    def abrir_galeria(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Atención", "Seleccione una locación para gestionar sus fotos.")
        
        id_loc = self.tabla.item(sel[0], "values")[0]
        nom_loc = self.tabla.item(sel[0], "values")[1]

        v_gal = ctk.CTkToplevel(self.main_root)
        v_gal.title(f"Galería de Fotos - {nom_loc}")
        v_gal.geometry("1000x700")
        v_gal.after(100, lambda: maximizar_ventana(v_gal))
        v_gal.transient(self.main_root)
        v_gal.grab_set()

        f_header = ctk.CTkFrame(v_gal, fg_color="transparent")
        f_header.pack(fill="x", padx=20, pady=(15, 5))

        ctk.CTkLabel(f_header, text=f"📸 Fotos y Planos: {nom_loc}", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")

        btn_cerrar_galeria = ctk.CTkButton(f_header, text="❌ Cerrar Galería", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=v_gal.destroy)
        btn_cerrar_galeria.pack(side="right")

        f_split = ctk.CTkFrame(v_gal, fg_color="transparent")
        f_split.pack(fill="both", expand=True, padx=20, pady=10)

        f_lista = ctk.CTkScrollableFrame(f_split, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_lista.pack(side="left", fill="both", expand=True, padx=(0, 15))

        f_add = ctk.CTkFrame(f_split, width=350, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        f_add.pack(side="right", fill="y")
        
        ctk.CTkLabel(f_add, text="Agregar Nueva Foto/Plano", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(20, 15))
        
        ruta_temp = {"path": ""}
        btn_seleccionar = ctk.CTkButton(f_add, text="📂 Buscar Imagen (JPG/PNG)", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b")
        btn_seleccionar.pack(fill="x", padx=20, pady=(0, 15))
        
        lbl_img_preview = ctk.CTkLabel(f_add, text="Sin imagen", width=250, height=200, fg_color="#e0e0e0", corner_radius=8)
        lbl_img_preview.pack(pady=(0, 15))

        def seleccionar_img():
            ruta = filedialog.askopenfilename(title="Seleccionar Imagen", filetypes=[("Imágenes", "*.png;*.jpg;*.jpeg")])
            if ruta:
                ruta_temp["path"] = ruta
                btn_seleccionar.configure(text="✅ Imagen Lista", fg_color="#28a745", hover_color="#218838")
                try:
                    img = Image.open(ruta)
                    img.thumbnail((250, 200))
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
                    lbl_img_preview.configure(image=ctk_img, text="")
                except Exception: pass
        btn_seleccionar.configure(command=seleccionar_img)

        ctk.CTkLabel(f_add, text="Leyenda / Descripción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20)
        ent_leyenda = ctk.CTkEntry(f_add, placeholder_text="Ej: Vista puerta principal")
        ent_leyenda.pack(fill="x", padx=20, pady=(0, 10))

        ctk.CTkLabel(f_add, text="Medidas (Largo x Ancho):", font=("Arial", 11, "bold")).pack(anchor="w", padx=20)
        ent_medidas = ctk.CTkEntry(f_add, placeholder_text="Ej: 10m x 15m")
        ent_medidas.pack(fill="x", padx=20, pady=(0, 20))

        def editar_datos_foto(fid, ley_act, med_act):
            v_edit_foto = ctk.CTkToplevel(v_gal)
            v_edit_foto.title("Editar Información")
            v_edit_foto.geometry("350x270")
            v_edit_foto.transient(v_gal)
            v_edit_foto.grab_set()

            ctk.CTkLabel(v_edit_foto, text="Modificar Datos", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
            
            ctk.CTkLabel(v_edit_foto, text="Leyenda / Descripción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20)
            ent_l = ctk.CTkEntry(v_edit_foto)
            ent_l.pack(fill="x", padx=20, pady=(0, 10))
            ent_l.insert(0, ley_act if ley_act else "")

            ctk.CTkLabel(v_edit_foto, text="Medidas referenciales:", font=("Arial", 11, "bold")).pack(anchor="w", padx=20)
            ent_m = ctk.CTkEntry(v_edit_foto)
            ent_m.pack(fill="x", padx=20, pady=(0, 15))
            ent_m.insert(0, med_act if med_act else "")

            def guardar_cambios_foto():
                n_ley = ent_l.get().strip()
                n_med = ent_m.get().strip()
                conn = conectar_db()
                if not conn:
                    return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden guardar cambios en Modo Lectura.", parent=v_edit_foto)
                try:
                    c = conn.cursor()
                    c.execute("UPDATE locaciones_fotos SET leyenda = %s, medidas = %s WHERE id = %s", (n_ley, n_med, fid))
                    conn.commit()
                    refrescar_galeria()
                    v_edit_foto.destroy()
                except Exception as e:
                    messagebox.showerror("Error", str(e), parent=v_edit_foto)
                finally:
                    liberar_conexion(conn)

            ctk.CTkButton(v_edit_foto, text="💾 Guardar Cambios", font=("Arial", 12, "bold"), fg_color="#17a2b8", hover_color="#138496", command=guardar_cambios_foto).pack(fill="x", padx=20, pady=5)

        def descargar_img(ruta_archivo):
            if not os.path.exists(ruta_archivo): 
                return messagebox.showerror("Error", "El archivo ya no se encuentra en el disco.", parent=v_gal)
            
            ext = os.path.splitext(ruta_archivo)[1]
            dest = filedialog.asksaveasfilename(defaultextension=ext, initialfile=os.path.basename(ruta_archivo), title="Guardar Imagen", parent=v_gal)
            if dest:
                try:
                    shutil.copy2(ruta_archivo, dest)
                    messagebox.showinfo("Éxito", "Imagen descargada y guardada correctamente.", parent=v_gal)
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo descargar:\n{e}", parent=v_gal)

        def refrescar_galeria():
            for widget in f_lista.winfo_children(): widget.destroy()
            
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, ruta_foto, leyenda, medidas, ruta_foto_medidas FROM locaciones_fotos WHERE locacion_id = %s ORDER BY id DESC", (id_loc,))
                fotos_bd = cursor.fetchall()
                
                rutas_orig = [r[1] for r in fotos_bd if r[1] and os.path.exists(r[1])]
                rutas_med = [r[4] for r in fotos_bd if len(r)>4 and r[4] and os.path.exists(r[4])]
                
                if not fotos_bd:
                    ctk.CTkLabel(f_lista, text="No hay fotos guardadas para esta locación.", text_color="gray").pack(pady=30)
                else:
                    f_carrusel_btns = ctk.CTkFrame(f_lista, fg_color="transparent")
                    f_carrusel_btns.pack(fill="x", padx=10, pady=(10, 15))
                    
                    if rutas_orig:
                        ctk.CTkButton(f_carrusel_btns, text="🎬 Carrusel (Originales)", font=("Arial", 12, "bold"), fg_color="#8e44ad", hover_color="#732d91", height=35, command=lambda: self.abrir_visor_carrusel(v_gal, rutas_orig, 0)).pack(side="left", expand=True, fill="x", padx=5)
                    if rutas_med:
                        ctk.CTkButton(f_carrusel_btns, text="🎬 Carrusel (Con Medidas)", font=("Arial", 12, "bold"), fg_color="#d35400", hover_color="#a84300", height=35, command=lambda: self.abrir_visor_carrusel(v_gal, rutas_med, 0)).pack(side="left", expand=True, fill="x", padx=5)

                    for fid, ruta_orig, ley, med, ruta_med in fotos_bd:
                        f_item = ctk.CTkFrame(f_lista, fg_color="#ffffff", corner_radius=8, border_width=1, border_color="#ccc")
                        f_item.pack(fill="x", pady=5, padx=10, ipadx=5, ipady=5)
                        
                        f_thumbs = ctk.CTkFrame(f_item, fg_color="transparent")
                        f_thumbs.pack(side="left", padx=10)
                        
                        if ruta_orig and os.path.exists(ruta_orig):
                            try:
                                img_o = Image.open(ruta_orig)
                                img_o.thumbnail((120, 90))
                                ctk_img_o = ctk.CTkImage(light_image=img_o, dark_image=img_o, size=(img_o.width, img_o.height))
                                
                                f_col_o = ctk.CTkFrame(f_thumbs, fg_color="transparent")
                                f_col_o.pack(side="left", padx=5)
                                ctk.CTkLabel(f_col_o, text="ORIGINAL", font=("Arial", 10, "bold"), text_color="gray").pack()
                                lbl_img_o = ctk.CTkLabel(f_col_o, image=ctk_img_o, text="", cursor="hand2")
                                lbl_img_o.pack()
                                
                                idx_orig = rutas_orig.index(ruta_orig) if ruta_orig in rutas_orig else 0
                                lbl_img_o.bind("<Double-1>", lambda e, r_list=rutas_orig, start_idx=idx_orig: self.abrir_visor_carrusel(v_gal, r_list, start_idx))
                            except Exception: pass
                            
                        if ruta_med and os.path.exists(ruta_med):
                            try:
                                img_m = Image.open(ruta_med)
                                img_m.thumbnail((120, 90))
                                ctk_img_m = ctk.CTkImage(light_image=img_m, dark_image=img_m, size=(img_m.width, img_m.height))
                                
                                f_col_m = ctk.CTkFrame(f_thumbs, fg_color="transparent")
                                f_col_m.pack(side="left", padx=5)
                                ctk.CTkLabel(f_col_m, text="MEDIDAS", font=("Arial", 10, "bold"), text_color="#d35400").pack()
                                lbl_img_m = ctk.CTkLabel(f_col_m, image=ctk_img_m, text="", cursor="hand2")
                                lbl_img_m.pack()
                                
                                idx_med = rutas_med.index(ruta_med) if ruta_med in rutas_med else 0
                                lbl_img_m.bind("<Double-1>", lambda e, r_list=rutas_med, start_idx=idx_med: self.abrir_visor_carrusel(v_gal, r_list, start_idx))
                            except Exception: pass

                        f_info = ctk.CTkFrame(f_item, fg_color="transparent")
                        f_info.pack(side="left", fill="both", expand=True, padx=10)
                        
                        ctk.CTkLabel(f_info, text=ley if ley else "Sin descripción", font=("Arial", 14, "bold"), text_color="#333", anchor="w").pack(fill="x", pady=(5, 5))
                        ctk.CTkLabel(f_info, text=f"📐 Ref: {med}" if med else "📐 Ref: N/A", font=("Arial", 11), text_color="#555", anchor="w").pack(fill="x", pady=(0, 10))
                        
                        f_actions_orig = ctk.CTkFrame(f_info, fg_color="transparent")
                        f_actions_orig.pack(fill="x", pady=2)
                        ctk.CTkButton(f_actions_orig, text="👁️ Ver Orig.", width=100, height=24, font=("Arial", 11), fg_color="#34495e", hover_color="#2c3e50", command=lambda r=ruta_orig: abrir_documento(r)).pack(side="left", padx=2)
                        ctk.CTkButton(f_actions_orig, text="📥 Bajar Orig.", width=100, height=24, font=("Arial", 11), fg_color="#27ae60", hover_color="#1e8449", command=lambda r=ruta_orig: descargar_img(r)).pack(side="left", padx=2)

                        if ruta_med and os.path.exists(ruta_med):
                            f_actions_med = ctk.CTkFrame(f_info, fg_color="transparent")
                            f_actions_med.pack(fill="x", pady=2)
                            ctk.CTkButton(f_actions_med, text="👁️ Ver Medidas", width=100, height=24, font=("Arial", 11), fg_color="#d35400", hover_color="#a84300", command=lambda r=ruta_med: abrir_documento(r)).pack(side="left", padx=2)
                            ctk.CTkButton(f_actions_med, text="📥 Bajar Medidas", width=100, height=24, font=("Arial", 11), fg_color="#27ae60", hover_color="#1e8449", command=lambda r=ruta_med: descargar_img(r)).pack(side="left", padx=2)

                        f_btns = ctk.CTkFrame(f_item, fg_color="transparent")
                        f_btns.pack(side="right", padx=15)
                        
                        btn_editar = ctk.CTkButton(f_btns, text="✏️ Dibujar Medidas", font=("Arial", 11, "bold"), width=130, fg_color="#f39c12", hover_color="#d35400", command=lambda f=fid, ro=ruta_orig, rm=ruta_med: self.abrir_editor_medidas(v_gal, f, ro, rm, refrescar_galeria))
                        btn_editar.pack(pady=4)
                        
                        btn_editar_datos = ctk.CTkButton(f_btns, text="📝 Editar Datos", font=("Arial", 11, "bold"), width=130, fg_color="#17a2b8", hover_color="#138496", command=lambda f_id=fid, l=ley, m=med: editar_datos_foto(f_id, l, m))
                        btn_editar_datos.pack(pady=4)
                        
                        btn_borrar = ctk.CTkButton(f_btns, text="🗑️ Eliminar Todo", font=("Arial", 11, "bold"), width=130, fg_color="#e74c3c", hover_color="#c0392b", command=lambda f_id=fid, ro=ruta_orig, rm=ruta_med: borrar_foto(f_id, ro, rm))
                        btn_borrar.pack(pady=4)

            except Exception as e: print(e)
            finally: liberar_conexion(conn)

        def borrar_foto(fid, ro, rm):
            if messagebox.askyesno("Confirmar", "¿Eliminar esta foto (y sus medidas) permanentemente?", parent=v_gal):
                conn = conectar_db()
                if not conn:
                    return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar fotos en Modo Lectura.", parent=v_gal)
                try:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM locaciones_fotos WHERE id = %s", (fid,))
                    conn.commit()
                    
                    if ro and os.path.exists(ro):
                        try: os.remove(ro)
                        except: pass
                    if rm and os.path.exists(rm):
                        try: os.remove(rm)
                        except: pass
                        
                    refrescar_galeria()
                except Exception as e:
                    messagebox.showerror("Error", str(e), parent=v_gal)
                finally:
                    liberar_conexion(conn)

        def subir_foto():
            if not ruta_temp["path"]: return messagebox.showwarning("Atención", "Seleccione una imagen primero.", parent=v_gal)
            
            ley = ent_leyenda.get().strip()
            med = ent_medidas.get().strip()
            
            nombre_archivo = f"Orig_Loc_{id_loc}_{int(time.time())}{os.path.splitext(ruta_temp['path'])[1]}"
            ruta_final = os.path.join(self.carpeta_originales, nombre_archivo)
            
            conn = conectar_db()
            if not conn:
                return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden subir fotos en Modo Lectura.", parent=v_gal)
            try:
                shutil.copy2(ruta_temp["path"], ruta_final)
                cursor = conn.cursor()
                cursor.execute("INSERT INTO locaciones_fotos (locacion_id, ruta_foto, leyenda, medidas, ruta_foto_medidas) VALUES (%s, %s, %s, %s, '')", (id_loc, ruta_final, ley, med))
                conn.commit()
                
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Agregó foto a locación ID {id_loc}")
                
                ruta_temp["path"] = ""
                btn_seleccionar.configure(text="📂 Buscar Imagen (JPG/PNG)", fg_color="#7f8c8d", hover_color="#606b6b")
                lbl_img_preview.configure(image=None, text="Sin imagen")
                ent_leyenda.delete(0, tk.END)
                ent_medidas.delete(0, tk.END)
                refrescar_galeria()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar la imagen:\n{e}", parent=v_gal)
            finally:
                liberar_conexion(conn)

        ctk.CTkButton(f_add, text="⬆️ Subir y Guardar", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=subir_foto).pack(fill="x", padx=20, pady=20)

        refrescar_galeria()

# =========================================================
# 🚀 PESTAÑA 2: RESERVAS DE LOCACIONES
# =========================================================
class ReservasLocacionesTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.crear_interfaz()

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=330, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        ctk.CTkLabel(self.f_form, text="📅 Reservar Locación", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 15))

        ctk.CTkLabel(self.f_form, text="🔍 Buscar Locación:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_buscar = ctk.CTkEntry(self.f_form, placeholder_text="Escriba para filtrar...")
        self.ent_buscar.pack(fill="x", padx=10, pady=(0, 5))
        self.ent_buscar.bind("<KeyRelease>", self.filtrar_locaciones)

        self.combo_locacion = ctk.CTkComboBox(self.f_form, state="readonly")
        self.combo_locacion.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Evento Asociado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_evento = ctk.CTkComboBox(self.f_form, state="readonly", command=self.al_seleccionar_evento)
        self.combo_evento.pack(fill="x", padx=10, pady=(0, 10))

        self.f_nota = ctk.CTkFrame(self.f_form, fg_color="transparent")
        self.f_nota.pack(fill="x", padx=10)
        
        self.lbl_nota = ctk.CTkLabel(self.f_nota, text="Motivo / Detalles de la reserva:", font=("Arial", 11, "bold"), text_color="#d35400")
        self.txt_nota = ctk.CTkTextbox(self.f_nota, height=50, border_width=1, border_color="#aab7c4", fg_color="#ffffff", corner_radius=6)

        ctk.CTkLabel(self.f_form, text="Fecha de Inicio:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_inicio = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_inicio.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_inicio = ctk.CTkEntry(f_inicio, placeholder_text="DD/MM/AAAA")
        self.ent_inicio.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_inicio, text="[ 📅 ]", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(self.main_root, self.ent_inicio)).pack(side="right", padx=(5,0))

        ctk.CTkLabel(self.f_form, text="Fecha Final:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fin = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fin.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_fin = ctk.CTkEntry(f_fin, placeholder_text="DD/MM/AAAA")
        self.ent_fin.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fin, text="[ 📅 ]", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(self.main_root, self.ent_fin)).pack(side="right", padx=(5,0))

        btn_guardar = ctk.CTkButton(self.f_form, text="🔒 Confirmar Reserva", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_reserva)
        btn_guardar.pack(fill="x", padx=10, pady=(20, 5))

        self.f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_reservas = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por locación, evento, motivos...")
        self.ent_buscar_reservas.pack(side="left", fill="x", expand=True)
        
        # 🚀 BÚSQUEDA ASÍNCRONA
        self.ent_buscar_reservas.bind("<KeyRelease>", lambda e: self.buscar_con_retraso_reservas())
        self.ent_buscar_reservas.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "locacion", "evento", "inicio", "fin", "notas")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("id", text="ID Res.")
        self.tabla.heading("locacion", text="Locación")
        self.tabla.heading("evento", text="Evento / Motivo")
        self.tabla.heading("inicio", text="Inicio")
        self.tabla.heading("fin", text="Fin")
        self.tabla.heading("notas", text="Notas")

        self.tabla.column("id", width=60, anchor="center")
        self.tabla.column("locacion", width=220, anchor="w")
        self.tabla.column("evento", width=200, anchor="w")
        self.tabla.column("inicio", width=90, anchor="center")
        self.tabla.column("fin", width=90, anchor="center")
        self.tabla.column("notas", width=200, anchor="w")

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        f_btn_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=10)
        
        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_btn_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        ctk.CTkButton(f_btn_tabla, text="❌ Cancelar/Eliminar Reserva", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_reserva).pack(side="right")

        self.cargar_combos()
        self.main_root.after(100, lambda: self.cargar_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_tabla()

    def buscar_con_retraso_reservas(self):
        if hasattr(self, "_busqueda_res_job"):
            try:
                self.main_root.after_cancel(self._busqueda_res_job)
            except Exception:
                pass
        self._busqueda_res_job = self.main_root.after(350, lambda: self.cargar_tabla(reset_pagina=True))

    def al_seleccionar_evento(self, valor):
        if "No asociado" in valor:
            self.lbl_nota.pack(anchor="w", pady=(0, 2))
            self.txt_nota.pack(fill="x", pady=(0, 10))
        else:
            self.lbl_nota.pack_forget()
            self.txt_nota.pack_forget()

    def cargar_combos(self):
        conn = conectar_db(silencioso=True)
        self.locaciones_completas = []
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id, nombre FROM locaciones ORDER BY nombre")
                for r in cursor.fetchall():
                    self.locaciones_completas.append(f"[{r[0]}] {r[1]}")
            except Exception: pass
            finally: liberar_conexion(conn)
            
        if self.locaciones_completas:
            self.combo_locacion.configure(values=self.locaciones_completas)
            self.combo_locacion.set(self.locaciones_completas[0])
        else:
            self.combo_locacion.configure(values=["No hay locaciones registradas"])
            self.combo_locacion.set("No hay locaciones registradas")

        eventos = getattr(cache_sistema, 'eventos_aprobados', [])
        lista_eventos = ["No asociado"]
        if eventos:
            lista_eventos.extend(eventos)
            
        self.combo_evento.configure(values=lista_eventos)
        
        if len(lista_eventos) > 1:
            self.combo_evento.set(lista_eventos[1])
        else:
            self.combo_evento.set(lista_eventos[0])
            
        self.al_seleccionar_evento(self.combo_evento.get())

    def filtrar_locaciones(self, event):
        texto = self.ent_buscar.get().lower()
        if not hasattr(self, 'locaciones_completas') or not self.locaciones_completas: return
        filtrados = [loc for loc in self.locaciones_completas if texto in loc.lower()]
        
        if filtrados:
            self.combo_locacion.configure(values=filtrados)
            self.combo_locacion.set(filtrados[0])
        else:
            self.combo_locacion.configure(values=["Sin coincidencias"])
            self.combo_locacion.set("Sin coincidencias")

    def str_to_date(self, date_str):
        try: return datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            try: return datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError: return None

    def guardar_reserva(self):
        loc_str = self.combo_locacion.get()
        evento = self.combo_evento.get()
        inicio_str = self.ent_inicio.get().strip()
        fin_str = self.ent_fin.get().strip()
        
        notas = ""
        if "No asociado" in evento:
            notas = self.txt_nota.get("1.0", tk.END).strip()
            if not notas:
                return messagebox.showwarning("Atención", "Ha seleccionado 'No asociado'.\nPor favor, escriba obligatoriamente el motivo o detalle de la reserva.")

        if "No hay" in loc_str or not inicio_str or not fin_str:
            return messagebox.showwarning("Atención", "Complete todos los campos requeridos.")

        inicio_dt = self.str_to_date(inicio_str)
        fin_dt = self.str_to_date(fin_str)

        if not inicio_dt or not fin_dt:
            return messagebox.showerror("Error", "Formato de fecha inválido.")
        if inicio_dt > fin_dt:
            return messagebox.showerror("Error", "La fecha de inicio no puede ser mayor a la fecha final.")

        loc_id = int(loc_str.split("]")[0].replace("[", ""))

        conn = conectar_db()
        if not conn:
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden registrar reservas en Modo Lectura.")
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT e.nombre, r.fecha_inicio, r.fecha_fin, r.evento_asociado
                FROM locaciones_reservas r
                JOIN locaciones e ON r.locacion_id = e.id
                WHERE r.locacion_id = %s
                AND r.fecha_inicio <= %s AND r.fecha_fin >= %s
            """, (loc_id, fin_dt, inicio_dt))
            
            cruce = cursor.fetchone()
            if cruce:
                fmt = "%d/%m/%Y"
                msg = (f"⚠️ ALERTA DE CRUCE DE FECHAS\n\n"
                       f"La locación '{cruce[0]}' ya se encuentra reservada para el evento:\n"
                       f"👉 '{cruce[3]}'\n\n"
                       f"Ocupada desde: {cruce[1].strftime(fmt)}\n"
                       f"Ocupada hasta: {cruce[2].strftime(fmt)}\n\n"
                       f"No puedes reservar esta locación en las fechas seleccionadas.")
                liberar_conexion(conn)
                return messagebox.showerror("Locación No Disponible", msg)

            cursor.execute("""
                INSERT INTO locaciones_reservas (locacion_id, evento_asociado, fecha_inicio, fecha_fin, notas)
                VALUES (%s, %s, %s, %s, %s)
            """, (loc_id, evento, inicio_dt, fin_dt, notas))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Reservó la locación ID {loc_id} para {evento}")
            messagebox.showinfo("Éxito", "Reserva guardada correctamente. Locación asegurada.")
            
            self.txt_nota.delete("1.0", tk.END)
            self.cargar_tabla(reset_pagina=True)

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    # 🚀 FIX: LAZY LOADING Y CACHÉ
    def cargar_tabla(self, reset_pagina=False):
        self._carga_res_token = getattr(self, "_carga_res_token", 0) + 1
        token = self._carga_res_token

        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        filtro = self.ent_buscar_reservas.get().strip().lower() if hasattr(self, 'ent_buscar_reservas') else ""
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"loc_res_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(token, datos)
        else:
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", ""))
            
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            query = """
                                SELECT r.id, e.nombre, r.evento_asociado, r.fecha_inicio, r.fecha_fin, r.notas 
                                FROM locaciones_reservas r
                                JOIN locaciones e ON r.locacion_id = e.id
                                ORDER BY r.fecha_inicio ASC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            query = """
                                SELECT r.id, e.nombre, r.evento_asociado, r.fecha_inicio, r.fecha_fin, r.notas 
                                FROM locaciones_reservas r
                                JOIN locaciones e ON r.locacion_id = e.id
                                WHERE e.nombre ILIKE %s OR r.evento_asociado ILIKE %s OR r.notas ILIKE %s
                                ORDER BY r.fecha_inicio ASC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (val, val, val, self.registros_por_pagina, offset))
                        
                        for r in cursor.fetchall():
                            f_ini = r[3].strftime("%d/%m/%Y") if r[3] else ""
                            f_fin = r[4].strftime("%d/%m/%Y") if r[4] else ""
                            
                            evento_mostrar = r[2]
                            if "No asociado" in r[2] and r[5]:
                                nota_limpia = r[5].replace('\n', ' | ')
                                evento_mostrar = f"No asociado ({nota_limpia[:30]}...)"
                            
                            rows.append((r[0], r[1], evento_mostrar, f_ini, f_fin, r[5]))
                            
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                    
                self.main_root.after(0, lambda t=token, r=rows: self._pintar_tabla(t, r))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_tabla(self, token, rows):
        if token != getattr(self, "_carga_res_token", 0):
            return
        for row in self.tabla.get_children(): self.tabla.delete(row)
        for r in rows:
            self.tabla.insert("", tk.END, values=r)
            
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1:
                self.btn_ant.configure(state="normal")
            else:
                self.btn_ant.configure(state="disabled")
                
            if len(rows) == self.registros_por_pagina:
                self.btn_sig.configure(state="normal")
            else:
                self.btn_sig.configure(state="disabled")

    def eliminar_reserva(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Aviso", "Seleccione una reserva para cancelar/eliminar.")
        id_res = self.tabla.item(sel[0], "values")[0]

        if messagebox.askyesno("Confirmar", "¿Desea cancelar esta reserva y liberar la locación en esas fechas?"):
            conn = conectar_db()
            if not conn:
                return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar reservas en Modo Lectura.")
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM locaciones_reservas WHERE id = %s", (id_res,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario Locaciones", f"Canceló la reserva ID {id_res}")
                self.cargar_tabla(reset_pagina=True)
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)


# =========================================================
# CLASE PRINCIPAL: INVENTARIO DE LOCACIONES
# =========================================================
class InventarioLocacionesApp:
    def __init__(self, parent_frame, usuario_activo="Desconocido"):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo
        self.pantalla_expandida = False
        aplicar_estilo_treeview()

        self.inicializar_bd()

        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        
        header_frame = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header_frame.pack(fill="x")
        
        ctk.CTkLabel(header_frame, text="📍 MÓDULO DE INVENTARIO Y RESERVAS DE LOCACIONES", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        self.btn_pantalla = ctk.CTkButton(header_frame, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="right")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        
        self.tab_catalogo = self.tabview.add(" 📍 1. Catálogo de Locaciones ")
        self.tab_reservas = self.tabview.add(" 📅 2. Reservar Locación ")
        
        self.app_catalogo = CatalogoLocacionesTab(self.tab_catalogo, self.parent_frame.winfo_toplevel(), self)
        self.app_reservas = ReservasLocacionesTab(self.tab_reservas, self.parent_frame.winfo_toplevel(), self)

    def inicializar_bd(self):
        global _SCHEMA_LOC_OK
        if _SCHEMA_LOC_OK:
            return
            
        def tarea_init():
            global _SCHEMA_LOC_OK
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS locaciones (
                        id SERIAL PRIMARY KEY,
                        nombre VARCHAR(255) NOT NULL,
                        contacto VARCHAR(255),
                        direccion TEXT,
                        aforo INTEGER DEFAULT 0,
                        precio_ultimo NUMERIC DEFAULT 0.0,
                        area_m2 NUMERIC DEFAULT 0.0,
                        requiere_permisos VARCHAR(2) DEFAULT 'No',
                        detalle_permisos TEXT,
                        tiene_estacionamiento VARCHAR(2) DEFAULT 'No',
                        cantidad_estacionamiento INTEGER DEFAULT 0
                    )
                """)
                
                for sql in (
                    "ALTER TABLE locaciones ADD COLUMN IF NOT EXISTS telefono VARCHAR(50) DEFAULT ''",
                    "ALTER TABLE locaciones ADD COLUMN IF NOT EXISTS fecha_precio VARCHAR(50) DEFAULT ''"
                ):
                    try: cursor.execute(sql); conn.commit()
                    except Exception: conn.rollback()
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS locaciones_fotos (
                        id SERIAL PRIMARY KEY,
                        locacion_id INTEGER REFERENCES locaciones(id) ON DELETE CASCADE,
                        ruta_foto TEXT,
                        leyenda TEXT,
                        medidas VARCHAR(255)
                    )
                """)
                
                for sql in (
                    "ALTER TABLE locaciones_fotos ADD COLUMN IF NOT EXISTS ruta_foto_medidas TEXT DEFAULT ''",
                ):
                    try: cursor.execute(sql); conn.commit()
                    except Exception: conn.rollback()
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS locaciones_reservas (
                        id SERIAL PRIMARY KEY,
                        locacion_id INTEGER REFERENCES locaciones(id) ON DELETE CASCADE,
                        evento_asociado VARCHAR(255),
                        fecha_inicio DATE,
                        fecha_fin DATE,
                        notas TEXT DEFAULT ''
                    )
                """)
                conn.commit()
                _SCHEMA_LOC_OK = True
            except Exception as e:
                print("Error creando tablas de locaciones:", e)
            finally:
                liberar_conexion(conn)
                
        threading.Thread(target=tarea_init, daemon=True).start()

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

if __name__ == "__main__":
    pass