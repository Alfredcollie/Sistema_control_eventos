# -*- coding: utf-8 -*-
"""
INVENTARIO.PY (ENTERPRISE EDITION)
- Paginación Lazy Loading (50 en 50) para el Catálogo de Equipos.
- Búsqueda Asíncrona en las 3 pestañas.
- Caché Inteligente en Combobox de Eventos.
- Protección del Pool de Conexiones (liberar_conexion).
- Auto-curación síncrona en segundo plano (Corregido Scope Global).
"""

import os
import json
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import customtkinter as ctk
import calendar
import sys
import threading
from datetime import datetime
import shutil
import subprocess

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

# Importamos PIL para manejar las fotos de los productos
try:
    from PIL import Image, ImageTk
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# Variable global definida al más alto nivel
_SCHEMA_INV_OK = False

# =========================================================
# 🚀 MOTOR DE CONFIGURACIÓN REGIONAL Y MULTIPLATAFORMA
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
        "formato_fecha": "DD/MM/AAAA"
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
    except Exception: valor = 0.0
    
    if formato == "1.000,00":
        str_val = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        str_val = f"{valor:,.2f}"
    return f"{simbolo} {str_val}"

def desformatear_numero(valor_str):
    if not valor_str: return 0.0
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    val = str(valor_str).replace(simbolo, "").replace("-", "").strip()
    if formato == "1.000,00":
        val = val.replace(".", "").replace(",", ".")
    else:
        val = val.replace(",", "")
    try: return float(val)
    except ValueError: return 0.0

def aplicar_estilo_treeview():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=26, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", font=("Arial", 10, "bold"), bordercolor="#e0e0e0", borderwidth=1)


# =========================================================
# CALENDARIO COMPARTIDO
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
            ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d").grid(row=0, column=i, padx=4, pady=5)
            
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
# PESTAÑA 1: CATÁLOGO DE EQUIPOS Y BITÁCORA
# =========================================================
class CatalogoEquiposTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        self.ruta_imagen_actual = ""
        self.imagen_tk = None
        
        # 🚀 VARIABLES DE PAGINACIÓN (LAZY LOADING)
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.opciones_depreciacion = [
            "10% - Otros equipos y muebles de oficina",
            "25% - Equipos de proc. de datos (Computadoras)",
            "20% - Vehículos de transporte terrestre",
            "20% - Maquinaria y equipo (Construcción/Pesca)",
            "20% - Maquinaria y equipo (Minera/Petrolera)",
            "5% - Edificios y construcciones",
            "0% - Sin depreciación / No aplica"
        ]
        
        self.crear_interfaz()

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=330, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        ctk.CTkLabel(self.f_form, text="📦 Registrar Equipo", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 15))

        self.lbl_imagen = ctk.CTkLabel(self.f_form, text="Sin Imagen", width=150, height=150, fg_color="#e0e0e0", corner_radius=10)
        self.lbl_imagen.pack(pady=(5, 5))
        
        ctk.CTkButton(self.f_form, text="📷 Cargar Foto", fg_color="#34495e", hover_color="#2c3e50", command=self.seleccionar_imagen).pack(pady=(0, 15))

        ctk.CTkLabel(self.f_form, text="Código Interno:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_codigo = ctk.CTkEntry(self.f_form, placeholder_text="Ej. ILUM-001")
        self.ent_codigo.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Cantidad Total en Almacén:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_cantidad = ctk.CTkEntry(self.f_form)
        self.ent_cantidad.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_cantidad.insert(0, "1")

        ctk.CTkLabel(self.f_form, text="Número(s) Serial(es) (Separe con comas):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.txt_serial = ctk.CTkTextbox(self.f_form, height=50, border_width=1, border_color="#aab7c4", fg_color="#ffffff", corner_radius=6)
        self.txt_serial.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Nombre del Equipo/Activo:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_nombre = ctk.CTkEntry(self.f_form, placeholder_text="Ej. Tacho Par LED 64")
        self.ent_nombre.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Marca / Modelo:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_marca = ctk.CTkEntry(self.f_form, placeholder_text="Ej. JBL PRX815")
        self.ent_marca.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Categoría:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        f_cat = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_cat.pack(fill="x", padx=15, pady=(0, 10))
        self.cmb_categoria = ctk.CTkComboBox(f_cat, values=["Cargando..."])
        self.cmb_categoria.pack(side="left", fill="x", expand=True)
        btn_nueva_cat = ctk.CTkButton(f_cat, text="+", font=("Arial", 12, "bold"), width=30, fg_color="#1f538d", hover_color="#163b65", command=self.crear_nueva_categoria)
        btn_nueva_cat.pack(side="right", padx=(5, 0))

        ctk.CTkLabel(self.f_form, text="Estado Físico:", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.cmb_estado = ctk.CTkComboBox(self.f_form, values=["Operativo", "En Mantenimiento", "Dado de Baja"], state="readonly")
        self.cmb_estado.pack(fill="x", padx=15, pady=(0, 10))
        self.cmb_estado.set("Operativo")

        simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
        ctk.CTkLabel(self.f_form, text=f"Precio Costo Unitario ({simbolo}):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.ent_costo = ctk.CTkEntry(self.f_form, placeholder_text="Ej. 1500.00")
        self.ent_costo.pack(fill="x", padx=15, pady=(0, 10))
        self.ent_costo.insert(0, "0.0")

        ctk.CTkLabel(self.f_form, text="Depreciación SUNAT (%):", font=("Arial", 11, "bold")).pack(anchor="w", padx=15)
        self.cmb_depreciacion = ctk.CTkComboBox(self.f_form, values=self.opciones_depreciacion)
        self.cmb_depreciacion.pack(fill="x", padx=15, pady=(0, 15))
        self.cmb_depreciacion.set(self.opciones_depreciacion[0])

        self.btn_guardar = ctk.CTkButton(self.f_form, text="💾 Guardar Equipo", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_equipo)
        self.btn_guardar.pack(fill="x", padx=15, pady=5)

        self.btn_limpiar = ctk.CTkButton(self.f_form, text="🧹 Limpiar Campos", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_formulario)
        self.btn_limpiar.pack(fill="x", padx=15, pady=5)

        self.f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_catalogo = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por código, nombre, marca, serial...")
        self.ent_buscar_catalogo.pack(side="left", fill="x", expand=True)
        
        # 🚀 BÚSQUEDA ASÍNCRONA
        self.ent_buscar_catalogo.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_buscar_catalogo.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "codigo", "serial", "nombre", "marca", "categoria", "cantidad", "estado", "costo", "depreciacion", "ruta_img")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("id", text="ID")
        self.tabla.heading("codigo", text="Código")
        self.tabla.heading("serial", text="Serial(es)")
        self.tabla.heading("nombre", text="Nombre del Equipo")
        self.tabla.heading("marca", text="Marca/Modelo")
        self.tabla.heading("categoria", text="Categoría")
        self.tabla.heading("cantidad", text="Stock")
        self.tabla.heading("estado", text="Estado")
        self.tabla.heading("costo", text="Costo Unit.")
        self.tabla.heading("depreciacion", text="Depr. %")

        self.tabla.column("id", width=35, anchor="center")
        self.tabla.column("codigo", width=80, anchor="center")
        self.tabla.column("serial", width=120, anchor="center")
        self.tabla.column("nombre", width=180, anchor="w")
        self.tabla.column("marca", width=120, anchor="w")
        self.tabla.column("categoria", width=120, anchor="center")
        self.tabla.column("cantidad", width=50, anchor="center")
        self.tabla.column("estado", width=100, anchor="center")
        self.tabla.column("costo", width=90, anchor="e")
        self.tabla.column("depreciacion", width=60, anchor="center")
        self.tabla.column("ruta_img", width=0, stretch=tk.NO)

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        scroll_x = ttk.Scrollbar(f_tabla, orient="horizontal", command=self.tabla.xview)
        self.tabla.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        
        scroll_x.pack(side="bottom", fill="x")
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        self.tabla.bind("<Double-1>", lambda e: self.cargar_para_editar())

        f_botones_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_botones_tabla.pack(fill="x", pady=10)

        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_botones_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        ctk.CTkButton(f_botones_tabla, text="📜 Ver Bitácora", font=("Arial", 12, "bold"), fg_color="#8e44ad", hover_color="#732d91", command=self.ver_bitacora).pack(side="left", padx=5)
        
        ctk.CTkButton(f_botones_tabla, text="✏️ Modificar", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.cargar_para_editar).pack(side="right", padx=5)
        ctk.CTkButton(f_botones_tabla, text="❌ Eliminar", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_equipo).pack(side="right", padx=5)

        self.id_edicion = None
        
        self.cargar_categorias()
        self.main_root.after(100, lambda: self.cargar_tabla(reset_pagina=True))

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

    def seleccionar_imagen(self):
        if not PIL_DISPONIBLE:
            return messagebox.showerror("Librería faltante", "Por favor instala Pillow ejecutando: pip install Pillow")

        ruta_origen = filedialog.askopenfilename(
            title="Seleccionar foto del producto",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp")]
        )
        
        if ruta_origen:
            carpeta_destino = "imagenes_inventario"
            if not os.path.exists(carpeta_destino):
                os.makedirs(carpeta_destino)
            
            ext = os.path.splitext(ruta_origen)[1]
            nombre_archivo = f"prod_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            ruta_final = os.path.join(carpeta_destino, nombre_archivo)
            
            try:
                shutil.copy(ruta_origen, ruta_final)
                self.ruta_imagen_actual = ruta_final
                self.mostrar_previsualizacion(ruta_final)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo copiar la imagen:\n{e}")

    def mostrar_previsualizacion(self, ruta):
        if not PIL_DISPONIBLE: return
        try:
            if os.path.exists(ruta):
                img = Image.open(ruta)
                img.thumbnail((150, 150), Image.Resampling.LANCZOS)
                self.imagen_tk = ImageTk.PhotoImage(img)
                self.lbl_imagen.configure(image=self.imagen_tk, text="")
            else:
                self.lbl_imagen.configure(image="", text="Imagen no encontrada")
        except Exception as e:
            print("Error cargando imagen:", e)
            self.lbl_imagen.configure(image="", text="Error en imagen")

    def crear_nueva_categoria(self):
        nueva = simpledialog.askstring("Nueva Categoría", "Ingrese el nombre de la nueva categoría:", parent=self.main_root)
        if nueva:
            nueva = nueva.strip()
            if nueva:
                valores_actuales = list(self.cmb_categoria.cget("values"))
                if nueva not in valores_actuales:
                    valores_actuales.append(nueva)
                    valores_actuales.sort()
                    self.cmb_categoria.configure(values=valores_actuales)
                self.cmb_categoria.set(nueva)
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Creó categoría de equipo '{nueva}'")

    def cargar_categorias(self):
        cats = getattr(cache_sistema, 'categorias_generales', [])
        if not cats:
            cats = ["Equipos Audiovisuales", "Mobiliario y Estructuras", "Decoración", "Otros"]
            
        self.cmb_categoria.configure(values=cats)
        
        current = self.cmb_categoria.get()
        if current not in cats:
            self.cmb_categoria.set(cats[0])

    def limpiar_formulario(self):
        self.ent_codigo.delete(0, tk.END)
        self.txt_serial.delete("1.0", tk.END) 
        self.ent_nombre.delete(0, tk.END)
        self.ent_marca.delete(0, tk.END)
        if self.cmb_categoria.cget("values"):
            self.cmb_categoria.set(self.cmb_categoria.cget("values")[0])
        self.cmb_estado.set("Operativo")
        self.ent_cantidad.delete(0, tk.END); self.ent_cantidad.insert(0, "1")
        self.ent_costo.delete(0, tk.END); self.ent_costo.insert(0, "0.0")
        self.cmb_depreciacion.set(self.opciones_depreciacion[0])
        
        self.ruta_imagen_actual = ""
        self.lbl_imagen.configure(image="", text="Sin Imagen")
        
        self.id_edicion = None
        self.btn_guardar.configure(text="💾 Guardar Equipo", fg_color="#1f538d", hover_color="#163b65")

    def guardar_equipo(self):
        codigo = self.ent_codigo.get().strip()
        nombre = self.ent_nombre.get().strip()
        marca = self.ent_marca.get().strip()
        categoria = self.cmb_categoria.get().strip()
        estado = self.cmb_estado.get()
        depr_str = self.cmb_depreciacion.get()
        ruta_img = self.ruta_imagen_actual
        
        try:
            cantidad = int(self.ent_cantidad.get().strip())
            costo = float(self.ent_costo.get().replace(",", ".").strip())
            depr = float(depr_str.split('%')[0].strip())
        except ValueError:
            return messagebox.showerror("Error", "La cantidad debe ser entera, y el costo numérico.")

        if not codigo or not nombre:
            return messagebox.showwarning("Aviso", "El Código y el Nombre son obligatorios.")

        serial_raw = self.txt_serial.get("1.0", tk.END).strip()
        serials_list = [s.strip() for s in serial_raw.replace('\n', ',').split(',') if s.strip()]
        
        if len(serials_list) > 0 and len(serials_list) != cantidad:
            return messagebox.showwarning("Atención de Seriales", 
                f"Ha indicado que hay {cantidad} equipo(s) en almacén, pero ha ingresado {len(serials_list)} número(s) de serie.\n\n"
                f"Para mantener el control exacto, debe ingresar un serial por cada equipo (separados por coma) o dejar el campo completamente vacío.")
                
        serial_final = ", ".join(serials_list)

        conn = conectar_db()
        if not conn:
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden guardar cambios en Modo Lectura.")
        try:
            cursor = conn.cursor()
            if self.id_edicion:
                cursor.execute("SELECT COUNT(*) FROM inventario_equipos WHERE codigo = %s AND id != %s", (codigo, self.id_edicion))
                if cursor.fetchone()[0] > 0:
                    liberar_conexion(conn)
                    return messagebox.showwarning("Duplicado", "Ese código ya está en uso.")
                
                cursor.execute("""
                    UPDATE inventario_equipos 
                    SET codigo=%s, numero_serial=%s, nombre=%s, marca_modelo=%s, categoria=%s, estado=%s, 
                        cantidad_total=%s, precio_costo=%s, depreciacion=%s, ruta_imagen=%s
                    WHERE id=%s
                """, (codigo, serial_final, nombre, marca, categoria, estado, cantidad, costo, depr, ruta_img, self.id_edicion))
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Modificó el equipo {codigo}")
                messagebox.showinfo("Éxito", "Equipo modificado correctamente.")
            else:
                cursor.execute("SELECT COUNT(*) FROM inventario_equipos WHERE codigo = %s", (codigo,))
                if cursor.fetchone()[0] > 0:
                    liberar_conexion(conn)
                    return messagebox.showwarning("Duplicado", "Ese código ya está registrado.")
                
                cursor.execute("""
                    INSERT INTO inventario_equipos 
                    (codigo, numero_serial, nombre, marca_modelo, categoria, estado, cantidad_total, precio_costo, depreciacion, ruta_imagen) 
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (codigo, serial_final, nombre, marca, categoria, estado, cantidad, costo, depr, ruta_img))
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Registró el equipo {codigo} ({estado})")
                messagebox.showinfo("Éxito", "Equipo registrado correctamente.")

            conn.commit()
            
            if hasattr(cache_sistema, 'invalidar'):
                cache_sistema.invalidar()
            self.cargar_categorias()
            self.cmb_categoria.set(categoria) 
            
            self.limpiar_formulario()
            self.cargar_tabla(reset_pagina=True)
            
            if hasattr(self.app_padre, 'app_reservas'):
                self.app_padre.app_reservas.cargar_combos()
            if hasattr(self.app_padre, 'app_recepcion'):
                self.app_padre.app_recepcion.cargar_combos()

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    def cargar_tabla(self, reset_pagina=False):
        self._carga_cat_token = getattr(self, "_carga_cat_token", 0) + 1
        token = self._carga_cat_token
        
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = self.ent_buscar_catalogo.get().strip().lower() if hasattr(self, 'ent_buscar_catalogo') else ""
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"inventario_eq_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(token, datos)
        else:
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro:
                            cursor.execute("""
                                SELECT id, codigo, numero_serial, nombre, marca_modelo, categoria, cantidad_total, estado, precio_costo, depreciacion, ruta_imagen 
                                FROM inventario_equipos 
                                WHERE codigo ILIKE %s OR nombre ILIKE %s OR marca_modelo ILIKE %s OR numero_serial ILIKE %s
                                ORDER BY categoria, nombre LIMIT %s OFFSET %s
                            """, (f"%{filtro}%", f"%{filtro}%", f"%{filtro}%", f"%{filtro}%", self.registros_por_pagina, offset))
                        else:
                            cursor.execute("SELECT id, codigo, numero_serial, nombre, marca_modelo, categoria, cantidad_total, estado, precio_costo, depreciacion, ruta_imagen FROM inventario_equipos ORDER BY categoria, nombre LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                            
                        for r in cursor.fetchall():
                            row_list = list(r)
                            row_list[8] = formatear_moneda(r[8]) if r[8] else formatear_moneda(0)
                            row_list[9] = f"{r[9]}%" if r[9] else "0%"
                            if len(row_list) == 10:
                                row_list.append("")
                            rows.append(row_list)
                            
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                self.main_root.after(0, lambda t=token, r=rows: self._pintar_tabla(t, r))

            threading.Thread(target=tarea, daemon=True).start()

    def _pintar_tabla(self, token, rows):
        if token != getattr(self, "_carga_cat_token", 0):
            return
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

    def cargar_para_editar(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Aviso", "Seleccione un equipo de la tabla.")
        valores = self.tabla.item(sel[0], "values")
        
        self.id_edicion = valores[0]
        self.ent_codigo.delete(0, tk.END); self.ent_codigo.insert(0, valores[1])
        
        self.txt_serial.delete("1.0", tk.END); self.txt_serial.insert("1.0", valores[2] if valores[2] != "None" else "")
        
        self.ent_nombre.delete(0, tk.END); self.ent_nombre.insert(0, valores[3])
        self.ent_marca.delete(0, tk.END); self.ent_marca.insert(0, valores[4] if valores[4] else "")
        
        cat_bd = valores[5]
        if cat_bd not in self.cmb_categoria.cget("values"):
            vals = list(self.cmb_categoria.cget("values"))
            vals.append(cat_bd)
            self.cmb_categoria.configure(values=vals)
        self.cmb_categoria.set(cat_bd)
        
        self.ent_cantidad.delete(0, tk.END); self.ent_cantidad.insert(0, valores[6])
        self.cmb_estado.set(valores[7])
        
        costo_limpio = desformatear_numero(valores[8])
        self.ent_costo.delete(0, tk.END); self.ent_costo.insert(0, f"{costo_limpio:.2f}")
        
        depr_bd = str(valores[9]).replace("%", "").strip()
        try:
            depr_float = float(depr_bd)
            depr_fmt = f"{int(depr_float)}" if depr_float.is_integer() else f"{depr_float}"
            match = False
            for op in self.opciones_depreciacion:
                if op.startswith(f"{depr_fmt}%"):
                    self.cmb_depreciacion.set(op)
                    match = True
                    break
            if not match:
                self.cmb_depreciacion.set(f"{depr_fmt}% - Valor Personalizado")
        except Exception:
            self.cmb_depreciacion.set("0% - Sin depreciación / No aplica")
            
        self.ruta_imagen_actual = valores[10] if len(valores) > 10 else ""
        if self.ruta_imagen_actual and self.ruta_imagen_actual != "None":
            self.mostrar_previsualizacion(self.ruta_imagen_actual)
        else:
            self.lbl_imagen.configure(image="", text="Sin Imagen")
        
        self.btn_guardar.configure(text="💾 Actualizar Equipo", fg_color="#34495e", hover_color="#2c3e50")

    def eliminar_equipo(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Aviso", "Seleccione un equipo para eliminar.")
        id_eq = self.tabla.item(sel[0], "values")[0]
        codigo = self.tabla.item(sel[0], "values")[1]

        if messagebox.askyesno("Confirmar", f"¿Eliminar el equipo {codigo}?\n\nSe eliminarán también las reservas asociadas a este equipo."):
            conn = conectar_db()
            if not conn:
                return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar equipos en Modo Lectura.")
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM inventario_reservas WHERE equipo_id = %s", (id_eq,))
                cursor.execute("DELETE FROM inventario_equipos WHERE id = %s", (id_eq,))
                conn.commit()
                
                if hasattr(cache_sistema, 'invalidar'):
                    cache_sistema.invalidar() 
                
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Eliminó el equipo {codigo}")
                self.cargar_tabla(reset_pagina=True)
                if hasattr(self.app_padre, 'app_reservas'):
                    self.app_padre.app_reservas.cargar_combos()
                if hasattr(self.app_padre, 'app_recepcion'):
                    self.app_padre.app_recepcion.cargar_combos()
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)

    def ver_bitacora(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Aviso", "Seleccione un equipo para ver su bitácora.")
        
        id_eq = self.tabla.item(sel[0], "values")[0]
        nombre_eq = self.tabla.item(sel[0], "values")[3]
        serial_eq = self.tabla.item(sel[0], "values")[2]
        
        sn_texto = f" (SN: {serial_eq})" if serial_eq and serial_eq != "None" else ""
        
        top = ctk.CTkToplevel(self.main_root)
        top.title(f"Bitácora del Equipo")
        top.geometry("850x550")
        top.transient(self.main_root)
        top.grab_set()
        
        ctk.CTkLabel(top, text=f"📜 Historial de Movimientos: {nombre_eq}{sn_texto}", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=10)
        
        f_tabla_bit = ctk.CTkFrame(top, fg_color="transparent")
        f_tabla_bit.pack(fill="both", expand=True, padx=15, pady=(0, 10))
        
        cols = ("fecha", "tipo", "detalle", "cantidad", "notas", "full_notas")
        tv = ttk.Treeview(f_tabla_bit, columns=cols, show="headings")
        tv.heading("fecha", text="Fecha")
        tv.heading("tipo", text="Movimiento")
        tv.heading("detalle", text="Evento / Persona")
        tv.heading("cantidad", text="Cant.")
        tv.heading("notas", text="Notas / Condición (Resumen)")
        
        tv.column("fecha", width=90, anchor="center")
        tv.column("tipo", width=140, anchor="center")
        tv.column("detalle", width=220, anchor="w")
        tv.column("cantidad", width=60, anchor="center")
        tv.column("notas", width=280, anchor="w")
        tv.column("full_notas", width=0, stretch=tk.NO)
        
        scroll_y = ttk.Scrollbar(f_tabla_bit, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=scroll_y.set)
        tv.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        
        f_detalle = ctk.CTkFrame(top, fg_color="transparent")
        f_detalle.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(f_detalle, text="Detalle completo de la nota seleccionada:", font=("Arial", 11, "bold")).pack(anchor="w")
        txt_detalle = ctk.CTkTextbox(f_detalle, height=80, border_width=1, border_color="#aab7c4")
        txt_detalle.pack(fill="x", pady=(5, 0))
        txt_detalle.configure(state="disabled")

        def al_seleccionar_fila(event):
            seleccion = tv.selection()
            if seleccion:
                valores = tv.item(seleccion[0], "values")
                nota_completa = valores[5] if len(valores) > 5 else ""
                txt_detalle.configure(state="normal")
                txt_detalle.delete("1.0", tk.END)
                txt_detalle.insert("1.0", nota_completa)
                txt_detalle.configure(state="disabled")

        tv.bind("<<TreeviewSelect>>", al_seleccionar_fila)
        
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                cursor = conn.cursor()
                query = """
                    SELECT fecha_inicio AS fecha_movimiento, 'Salida (Reserva)' AS tipo_mov, evento_asociado AS detalle, cantidad, 
                           COALESCE(notas, '') || CASE WHEN COALESCE(autorizado_por, '') != '' THEN ' [Aut: ' || autorizado_por || ' - ' || COALESCE(cargo_autoriza, '') || ']' ELSE '' END AS notas 
                    FROM inventario_reservas WHERE equipo_id = %s
                    UNION ALL
                    SELECT fecha_recepcion AS fecha_movimiento, 'Entrada (Retorno)' AS tipo_mov, persona_entrega AS detalle, cantidad, condicion || ' - ' || detalles AS notas 
                    FROM inventario_recepciones WHERE equipo_id = %s
                    ORDER BY fecha_movimiento DESC
                """
                cursor.execute(query, (id_eq, id_eq))
                
                fmt_salida = "%d/%m/%Y" if CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA") == "DD/MM/AAAA" else "%m/%d/%Y"
                
                for r in cursor.fetchall():
                    fecha_str = r[0].strftime(fmt_salida) if r[0] else ""
                    nota_completa = r[4] if r[4] else ""
                    nota_resumen = nota_completa.replace('\n', ' | ').replace('\r', '')
                    
                    tv.insert("", tk.END, values=(fecha_str, r[1], r[2], r[3], nota_resumen, nota_completa))
            except Exception as e:
                print("Error en bitácora:", e)
            finally:
                liberar_conexion(conn)

# =========================================================
# PESTAÑA 2: RESERVAS Y DESPACHOS
# =========================================================
class ReservasTab:
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

        ctk.CTkLabel(self.f_form, text="📅 Reservar para Evento", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 15))

        ctk.CTkLabel(self.f_form, text="🔍 Buscar Equipo:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_buscar_equipo = ctk.CTkEntry(self.f_form, placeholder_text="Escriba para filtrar...")
        self.ent_buscar_equipo.pack(fill="x", padx=10, pady=(0, 5))
        self.ent_buscar_equipo.bind("<KeyRelease>", self.filtrar_equipos)

        ctk.CTkLabel(self.f_form, text="Equipo/Activo Seleccionado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_equipo = ctk.CTkComboBox(self.f_form, state="readonly", command=self.al_seleccionar_equipo)
        self.combo_equipo.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Evento Asociado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_evento = ctk.CTkComboBox(self.f_form, state="readonly", command=self.al_seleccionar_evento)
        self.combo_evento.pack(fill="x", padx=10, pady=(0, 10))

        self.f_nota = ctk.CTkFrame(self.f_form, fg_color="transparent")
        self.f_nota.pack(fill="x", padx=10)
        
        self.lbl_nota = ctk.CTkLabel(self.f_nota, text="Motivo / Detalles de la salida:", font=("Arial", 11, "bold"), text_color="#d35400")
        self.txt_nota = ctk.CTkTextbox(self.f_nota, height=50, border_width=1, border_color="#aab7c4", fg_color="#ffffff", corner_radius=6)

        fmt_fecha = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")

        ctk.CTkLabel(self.f_form, text="Fecha Salida/Inicio:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_inicio = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_inicio.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_inicio = ctk.CTkEntry(f_inicio, placeholder_text=fmt_fecha)
        self.ent_inicio.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_inicio, text="📅", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(self.main_root.winfo_toplevel(), self.ent_inicio)).pack(side="right", padx=(5,0))

        ctk.CTkLabel(self.f_form, text="Fecha Retorno/Fin:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fin = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fin.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_fin = ctk.CTkEntry(f_fin, placeholder_text=fmt_fecha)
        self.ent_fin.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fin, text="📅", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(self.main_root.winfo_toplevel(), self.ent_fin)).pack(side="right", padx=(5,0))

        ctk.CTkLabel(self.f_form, text="Cantidad a Reservar:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_cantidad = ctk.CTkEntry(self.f_form)
        self.ent_cantidad.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Autorizado por (Nombre):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_autoriza_nombre = ctk.CTkEntry(self.f_form, placeholder_text="Ej. Carlos Torres")
        self.ent_autoriza_nombre.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Cargo del Autorizador:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_autoriza_cargo = ctk.CTkEntry(self.f_form, placeholder_text="Ej. Gerente de Logística")
        self.ent_autoriza_cargo.pack(fill="x", padx=10, pady=(0, 2))

        self.lbl_stock = ctk.CTkLabel(self.f_form, text="Existencia en Almacén: --", font=("Arial", 11, "bold"), text_color="#c0392b")
        self.lbl_stock.pack(anchor="w", padx=15, pady=(0, 15))

        btn_guardar = ctk.CTkButton(self.f_form, text="🔒 Confirmar Reserva", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_reserva)
        btn_guardar.pack(fill="x", padx=10, pady=5)

        self.f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_reservas = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por equipo, evento, persona...")
        self.ent_buscar_reservas.pack(side="left", fill="x", expand=True)
        self.ent_buscar_reservas.bind("<KeyRelease>", lambda e: self.buscar_con_retraso_reservas())
        self.ent_buscar_reservas.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "equipo", "evento", "inicio", "fin", "cantidad", "autoriza")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("id", text="ID Res.")
        self.tabla.heading("equipo", text="Equipo")
        self.tabla.heading("evento", text="Evento / Motivo")
        self.tabla.heading("inicio", text="Salida")
        self.tabla.heading("fin", text="Retorno")
        self.tabla.heading("cantidad", text="Cant.")
        self.tabla.heading("autoriza", text="Autorizado Por")

        self.tabla.column("id", width=50, anchor="center")
        self.tabla.column("equipo", width=180, anchor="w")
        self.tabla.column("evento", width=180, anchor="w")
        self.tabla.column("inicio", width=80, anchor="center")
        self.tabla.column("fin", width=80, anchor="center")
        self.tabla.column("cantidad", width=50, anchor="center")
        self.tabla.column("autoriza", width=120, anchor="w")

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        f_btn_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=10)
        
        # 🚀 BOTONES PAGINACIÓN
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

    # 🚀 FIX: COMBO DE EQUIPOS Y EVENTOS CON CACHÉ
    def cargar_combos(self):
        eqs_cache = cache_sistema.obtener('lista_equipos_stock')
        evs_cache = cache_sistema.obtener('lista_eventos_aprobados')
        
        if eqs_cache is not None and evs_cache is not None:
            self._aplicar_combos(eqs_cache, evs_cache)
        else:
            self.combo_equipo.set("Cargando equipos...")
            self.combo_evento.set("Cargando eventos...")
            def tarea_combos():
                equipos = []
                eventos = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT e.id, e.codigo, e.nombre, e.numero_serial 
                            FROM inventario_equipos e
                            WHERE e.estado = 'Operativo' 
                            AND (e.cantidad_total - COALESCE((SELECT SUM(cantidad) FROM inventario_reservas r WHERE r.equipo_id = e.id), 0)) > 0
                            ORDER BY e.nombre
                        """)
                        for r in cursor.fetchall():
                            sn_text = f" (SN: {r[3]})" if r[3] else ""
                            equipos.append(f"[{r[0]}] {r[1]} - {r[2]}{sn_text}")
                            
                        cache_sistema.guardar('lista_equipos_stock', equipos)
                        
                        cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        eventos = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                        cache_sistema.guardar('lista_eventos_aprobados', eventos)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                
                self.main_root.after(0, lambda: self._aplicar_combos(equipos, eventos))
                
            threading.Thread(target=tarea_combos, daemon=True).start()

    def _aplicar_combos(self, equipos, eventos):
        self.equipos_completos = equipos
        if equipos:
            self.combo_equipo.configure(values=equipos)
            self.combo_equipo.set(equipos[0])
            self.al_seleccionar_equipo(equipos[0])
        else:
            self.combo_equipo.configure(values=["No hay equipos con stock disponible"])
            self.combo_equipo.set("No hay equipos con stock disponible")
            self.lbl_stock.configure(text="Existencia en Almacén: 0")

        lista_eventos = ["No asociado"]
        if eventos: lista_eventos.extend(eventos)
            
        self.combo_evento.configure(values=lista_eventos)
        if len(lista_eventos) > 1: self.combo_evento.set(lista_eventos[1])
        else: self.combo_evento.set(lista_eventos[0])
        self.al_seleccionar_evento(self.combo_evento.get())

    def filtrar_equipos(self, event):
        texto = self.ent_buscar_equipo.get().lower()
        if not hasattr(self, 'equipos_completos') or not self.equipos_completos: return
        filtrados = [eq for eq in self.equipos_completos if texto in eq.lower()]
        
        if filtrados:
            self.combo_equipo.configure(values=filtrados)
            self.combo_equipo.set(filtrados[0])
            self.al_seleccionar_equipo(filtrados[0])
        else:
            self.combo_equipo.configure(values=["Sin coincidencias"])
            self.combo_equipo.set("Sin coincidencias")
            self.lbl_stock.configure(text="Existencia en Almacén: 0 unidades")

    def al_seleccionar_equipo(self, valor):
        if not valor or "No hay" in valor or "coincidencias" in valor:
            self.lbl_stock.configure(text="Existencia en Almacén: 0 unidades")
            return
            
        try:
            equipo_id = int(valor.split("]")[0].replace("[", ""))
            conn = conectar_db(silencioso=True)
            if not conn: return
            cursor = conn.cursor()
            query = """
                SELECT e.cantidad_total, 
                       (e.cantidad_total - COALESCE((SELECT SUM(cantidad) FROM inventario_reservas r WHERE r.equipo_id = e.id), 0))
                FROM inventario_equipos e WHERE e.id = %s
            """
            cursor.execute(query, (equipo_id,))
            res = cursor.fetchone()
            if res:
                self.lbl_stock.configure(text=f"Físico: {res[0]} ud(s) | Disp. Ahora: {res[1]} ud(s)")
            liberar_conexion(conn)
        except Exception as e:
            self.lbl_stock.configure(text="Existencia en Almacén: --")

    def str_to_date(self, date_str):
        try: return datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            try: return datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError: return None

    def guardar_reserva(self):
        equipo_str = self.combo_equipo.get()
        evento = self.combo_evento.get()
        inicio_str = self.ent_inicio.get().strip()
        fin_str = self.ent_fin.get().strip()
        autoriza_nombre = self.ent_autoriza_nombre.get().strip()
        autoriza_cargo = self.ent_autoriza_cargo.get().strip()
        
        notas = ""
        if "No asociado" in evento:
            notas = self.txt_nota.get("1.0", tk.END).strip()
            if not notas:
                return messagebox.showwarning("Atención", "Ha seleccionado 'No asociado'.\nPor favor, escriba obligatoriamente el motivo o detalle de la salida.")

        if "No hay equipos" in equipo_str or "Sin coincidencias" in equipo_str or not inicio_str or not fin_str:
            return messagebox.showwarning("Atención", "Seleccione un equipo válido y complete todos los campos requeridos.")

        try:
            cantidad_req = int(self.ent_cantidad.get().strip())
            if cantidad_req <= 0: raise ValueError
        except ValueError:
            return messagebox.showerror("Error", "La cantidad debe ser un número entero mayor a 0.")

        inicio_dt = self.str_to_date(inicio_str)
        fin_dt = self.str_to_date(fin_str)

        if not inicio_dt or not fin_dt:
            return messagebox.showerror("Error", "Formato de fecha inválido.")
        if inicio_dt > fin_dt:
            return messagebox.showerror("Error", "La fecha de salida no puede ser mayor a la de retorno.")

        try:
            equipo_id = int(equipo_str.split("]")[0].replace("[", ""))
        except Exception:
            return messagebox.showerror("Error", "El formato del equipo seleccionado no es válido.")

        conn = conectar_db()
        if not conn:
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden registrar reservas en Modo Lectura.")
        try:
            cursor = conn.cursor()
            
            cursor.execute("SELECT cantidad_total, nombre FROM inventario_equipos WHERE id = %s", (equipo_id,))
            res_equipo = cursor.fetchone()
            if not res_equipo: 
                liberar_conexion(conn)
                return
            cantidad_total = res_equipo[0]
            nombre_equipo = res_equipo[1]

            cursor.execute("""
                SELECT COALESCE(SUM(cantidad), 0) FROM inventario_reservas 
                WHERE equipo_id = %s 
                AND fecha_inicio <= %s AND fecha_fin >= %s
            """, (equipo_id, fin_dt, inicio_dt))
            
            cantidad_reservada = cursor.fetchone()[0]
            cantidad_disponible = cantidad_total - cantidad_reservada

            if cantidad_req > cantidad_disponible:
                msg = (f"⚠️ ALERTA DE CRUCE DE INVENTARIO\n\n"
                       f"Equipo: {nombre_equipo}\n"
                       f"Stock Total Físico: {cantidad_total}\n"
                       f"Stock ya comprometido en esas fechas: {cantidad_reservada}\n\n"
                       f"Disponibilidad real para esa fecha: ¡Solo quedan {cantidad_disponible} disponibles!\n\n"
                       f"No puedes reservar {cantidad_req} units.")
                liberar_conexion(conn)
                messagebox.showerror("Stock Insuficiente", msg)
                return

            cursor.execute("""
                INSERT INTO inventario_reservas (equipo_id, evento_asociado, fecha_inicio, fecha_fin, cantidad, notas, autorizado_por, cargo_autoriza)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (equipo_id, evento, inicio_dt, fin_dt, cantidad_req, notas, autoriza_nombre, autoriza_cargo))
            conn.commit()
            
            cache_sistema.invalidar() 

            registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Reservó {cantidad_req} un. del equipo ID {equipo_id} para {evento}")
            messagebox.showinfo("Éxito", "Reserva guardada correctamente. Equipo asegurado.")
            
            self.ent_cantidad.delete(0, tk.END)
            self.ent_autoriza_nombre.delete(0, tk.END)
            self.ent_autoriza_cargo.delete(0, tk.END)
            self.txt_nota.delete("1.0", tk.END)
            self.cargar_tabla(reset_pagina=True)
            self.cargar_combos() 
            if hasattr(self.app_padre, 'app_recepcion'):
                self.app_padre.app_recepcion.cargar_combos()

        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    # 🚀 FIX: CARGA LAZY LOADING + CACHÉ
    def cargar_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        filtro = self.ent_buscar_reservas.get().strip().lower() if hasattr(self, 'ent_buscar_reservas') else ""
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"inventario_res_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos)
        else:
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", "", ""))
            
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            query = """
                                SELECT r.id, e.codigo, e.nombre, e.numero_serial, r.evento_asociado, r.fecha_inicio, r.fecha_fin, r.cantidad, r.notas, r.autorizado_por, r.cargo_autoriza 
                                FROM inventario_reservas r
                                JOIN inventario_equipos e ON r.equipo_id = e.id
                                ORDER BY r.fecha_inicio ASC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            query = """
                                SELECT r.id, e.codigo, e.nombre, e.numero_serial, r.evento_asociado, r.fecha_inicio, r.fecha_fin, r.cantidad, r.notas, r.autorizado_por, r.cargo_autoriza 
                                FROM inventario_reservas r
                                JOIN inventario_equipos e ON r.equipo_id = e.id
                                WHERE e.nombre ILIKE %s OR r.evento_asociado ILIKE %s OR r.autorizado_por ILIKE %s OR r.notas ILIKE %s
                                ORDER BY r.fecha_inicio ASC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (val, val, val, val, self.registros_por_pagina, offset))
                        
                        fmt_salida = "%d/%m/%Y" if CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA") == "DD/MM/AAAA" else "%m/%d/%Y"
                        
                        for r in cursor.fetchall():
                            sn_text = f" (SN: {r[3]})" if r[3] else ""
                            eq_display = f"{r[1]} - {r[2]}{sn_text}"
                            f_ini = r[5].strftime(fmt_salida) if r[5] else ""
                            f_fin = r[6].strftime(fmt_salida) if r[6] else ""
                            evento_mostrar = r[4]
                            if "No asociado" in r[4] and r[8]:
                                nota_limpia = r[8].replace('\n', ' | ')
                                evento_mostrar = f"No asociado ({nota_limpia[:30]}...)"
                            autoriza_disp = r[9] if r[9] else ""
                            if autoriza_disp and r[10]: autoriza_disp += f" ({r[10]})"
                            
                            rows.append((r[0], eq_display, evento_mostrar, f_ini, f_fin, r[7], autoriza_disp))
                            
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception as e: print("Error reservas:", e)
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

    def eliminar_reserva(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Aviso", "Seleccione una reserva para eliminar.")
        id_res = self.tabla.item(sel[0], "values")[0]

        if messagebox.askyesno("Confirmar", "¿Desea cancelar y eliminar esta reserva liberando el stock?"):
            conn = conectar_db()
            if not conn:
                return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar reservas en Modo Lectura.")
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM inventario_reservas WHERE id = %s", (id_res,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Canceló la reserva ID {id_res}")
                self.cargar_tabla(reset_pagina=True)
                self.cargar_combos() 
                if hasattr(self.app_padre, 'app_recepcion'):
                    self.app_padre.app_recepcion.cargar_combos()
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)

# =========================================================
# 🚀 PESTAÑA 3: RECEPCIÓN Y RETORNOS
# =========================================================
class RecepcionEquiposTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        self.ruta_evidencia_actual = ""
        self.evidencia_tk = None
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.crear_interfaz()

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=330, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        ctk.CTkLabel(self.f_form, text="📥 Registrar Retorno", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 15))

        ctk.CTkLabel(self.f_form, text="🔍 Buscar Equipo con Reserva/Salida:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_buscar_equipo = ctk.CTkEntry(self.f_form, placeholder_text="Escriba para filtrar...")
        self.ent_buscar_equipo.pack(fill="x", padx=10, pady=(0, 5))
        self.ent_buscar_equipo.bind("<KeyRelease>", self.filtrar_equipos)

        ctk.CTkLabel(self.f_form, text="Equipo con Reserva / Salida Pendiente:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_equipo = ctk.CTkComboBox(self.f_form, state="readonly")
        self.combo_equipo.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Cantidad Devuelta:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_cantidad = ctk.CTkEntry(self.f_form)
        self.ent_cantidad.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_cantidad.insert(0, "1")

        ctk.CTkLabel(self.f_form, text="Entregado por (Nombre/Empresa):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_persona = ctk.CTkEntry(self.f_form, placeholder_text="Ej. Juan Pérez")
        self.ent_persona.pack(fill="x", padx=10, pady=(0, 10))

        fmt_fecha = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        ctk.CTkLabel(self.f_form, text="Fecha de Recepción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fecha = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fecha.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_fecha = ctk.CTkEntry(f_fecha, placeholder_text=fmt_fecha)
        self.ent_fecha.pack(side="left", fill="x", expand=True)
        self.ent_fecha.insert(0, datetime.now().strftime("%d/%m/%Y" if fmt_fecha == "DD/MM/AAAA" else "%m/%d/%Y"))
        ctk.CTkButton(f_fecha, text="📅", width=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(self.main_root.winfo_toplevel(), self.ent_fecha)).pack(side="right", padx=(5,0))

        ctk.CTkLabel(self.f_form, text="Condición Física de Recepción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.cmb_condicion = ctk.CTkComboBox(self.f_form, values=[
            "Óptimas condiciones",
            "Desgaste normal / Uso",
            "Faltan piezas / Incompleto",
            "Dañado / Roto",
            "Requiere Mantenimiento urgente"
        ], state="readonly", command=self.al_seleccionar_condicion)
        self.cmb_condicion.pack(fill="x", padx=10, pady=(0, 10))
        
        self.f_nota = ctk.CTkFrame(self.f_form, fg_color="transparent")
        self.f_nota.pack(fill="x", padx=10)
        
        self.lbl_nota = ctk.CTkLabel(self.f_nota, text="Detalles del daño o faltante:", font=("Arial", 11, "bold"), text_color="#c0392b")
        self.txt_nota = ctk.CTkTextbox(self.f_nota, height=60, border_width=1, border_color="#aab7c4", fg_color="#ffffff", corner_radius=6)
        
        self.lbl_evidencia = ctk.CTkLabel(self.f_nota, text="Sin evidencia", width=120, height=120, fg_color="#e0e0e0", corner_radius=6)
        self.btn_evidencia = ctk.CTkButton(self.f_nota, text="📷 Cargar Foto de Evidencia", fg_color="#34495e", hover_color="#2c3e50", command=self.seleccionar_evidencia)

        self.al_seleccionar_condicion(self.cmb_condicion.get())

        btn_guardar = ctk.CTkButton(self.f_form, text="📥 Registrar Recepción y Liberar Reserva", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=self.guardar_recepcion)
        btn_guardar.pack(fill="x", padx=10, pady=20)

        self.f_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_recepcion = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por equipo, persona, condición, notas...")
        self.ent_buscar_recepcion.pack(side="left", fill="x", expand=True)
        self.ent_buscar_recepcion.bind("<KeyRelease>", lambda e: self.buscar_con_retraso_recepcion())
        self.ent_buscar_recepcion.bind("<Return>", lambda e: self.cargar_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "fecha", "equipo", "cantidad", "persona", "condicion", "notas")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        self.tabla.heading("id", text="ID Rec.")
        self.tabla.heading("fecha", text="Fecha")
        self.tabla.heading("equipo", text="Equipo Devuelto")
        self.tabla.heading("cantidad", text="Cant.")
        self.tabla.heading("persona", text="Entregado Por")
        self.tabla.heading("condicion", text="Condición")
        self.tabla.heading("notas", text="Observaciones")

        self.tabla.column("id", width=50, anchor="center")
        self.tabla.column("fecha", width=80, anchor="center")
        self.tabla.column("equipo", width=220, anchor="w")
        self.tabla.column("cantidad", width=50, anchor="center")
        self.tabla.column("persona", width=120, anchor="w")
        self.tabla.column("condicion", width=130, anchor="center")
        self.tabla.column("notas", width=200, anchor="w")

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        scroll_x = ttk.Scrollbar(f_tabla, orient="horizontal", command=self.tabla.xview)
        self.tabla.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        
        scroll_x.pack(side="bottom", fill="x")
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        
        # 🚀 BOTONES PAGINACIÓN
        f_botones_tabla = ctk.CTkFrame(self.f_derecha, fg_color="transparent")
        f_botones_tabla.pack(fill="x", pady=10)
        f_paginacion = ctk.CTkFrame(f_botones_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        self.cargar_combos()
        self.main_root.after(100, lambda: self.cargar_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_tabla()

    def buscar_con_retraso_recepcion(self):
        if hasattr(self, "_busqueda_rec_job"):
            try:
                self.main_root.after_cancel(self._busqueda_rec_job)
            except Exception:
                pass
        self._busqueda_rec_job = self.main_root.after(350, lambda: self.cargar_tabla(reset_pagina=True))

    def seleccionar_evidencia(self):
        if not PIL_DISPONIBLE:
            return messagebox.showerror("Librería faltante", "Por favor instala Pillow ejecutando: pip install Pillow")

        ruta_origen = filedialog.askopenfilename(
            title="Seleccionar foto de evidencia enviada al PC",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp")]
        )
        
        if ruta_origen:
            carpeta_destino = "evidencias_recepcion"
            if not os.path.exists(carpeta_destino):
                os.makedirs(carpeta_destino)
            
            ext = os.path.splitext(ruta_origen)[1]
            nombre_archivo = f"evidencia_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            ruta_final = os.path.join(carpeta_destino, nombre_archivo)
            
            try:
                shutil.copy(ruta_origen, ruta_final)
                self.ruta_evidencia_actual = ruta_final
                self.mostrar_evidencia(ruta_final)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar la evidencia:\n{e}")

    def mostrar_evidencia(self, ruta):
        if not PIL_DISPONIBLE: return
        try:
            if os.path.exists(ruta):
                img = Image.open(ruta)
                img.thumbnail((120, 120), Image.Resampling.LANCZOS)
                self.evidencia_tk = ImageTk.PhotoImage(img)
                self.lbl_evidencia.configure(image=self.evidencia_tk, text="")
            else:
                self.lbl_evidencia.configure(image="", text="Imagen no encontrada")
        except Exception:
            self.lbl_evidencia.configure(image="", text="Error en imagen")

    def al_seleccionar_condicion(self, valor):
        if valor != "Óptimas condiciones":
            self.lbl_nota.pack(anchor="w", pady=(0, 2))
            self.txt_nota.pack(fill="x", pady=(0, 10))
            self.lbl_evidencia.pack(pady=(0, 5))
            self.btn_evidencia.pack(fill="x", pady=(0, 10))
        else:
            self.lbl_nota.pack_forget()
            self.txt_nota.pack_forget()
            self.lbl_evidencia.pack_forget()
            self.btn_evidencia.pack_forget()
            self.ruta_evidencia_actual = ""
            self.lbl_evidencia.configure(image="", text="Sin evidencia")

    # 🚀 FIX: COMBO DE RETORNOS CON CACHÉ Y ASÍNCRONO
    def cargar_combos(self):
        clave_cache = 'lista_equipos_reservados'
        eqs_cache = cache_sistema.obtener(clave_cache)
        
        if eqs_cache is not None:
            self._aplicar_combos(eqs_cache)
        else:
            self.combo_equipo.set("Cargando equipos...")
            def tarea():
                equipos = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        query = """
                            SELECT DISTINCT r.equipo_id, e.codigo, e.nombre, e.numero_serial, r.evento_asociado, r.cantidad, r.id
                            FROM inventario_reservas r
                            JOIN inventario_equipos e ON r.equipo_id = e.id
                            ORDER BY e.nombre
                        """
                        cursor.execute(query)
                        for r in cursor.fetchall():
                            sn_text = f" (SN: {r[3]})" if r[3] else ""
                            equipos.append(f"[Reserva #{r[6]} - Eq: #{r[0]}] {r[1]} - {r[2]}{sn_text} | Evento: {r[4]} ({r[5]} un.)")
                        cache_sistema.guardar(clave_cache, equipos)
                    except Exception as e:
                        print("Error consultando equipos reservados:", e)
                    finally: liberar_conexion(conn)
                self.main_root.after(0, lambda: self._aplicar_combos(equipos))
            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_combos(self, equipos):
        self.equipos_completos = equipos
        if equipos:
            self.combo_equipo.configure(values=equipos)
            self.combo_equipo.set(equipos[0])
        else:
            self.combo_equipo.configure(values=["No hay equipos reservados pendientes de retorno"])
            self.combo_equipo.set("No hay equipos reservados pendientes de retorno")

    def filtrar_equipos(self, event):
        texto = self.ent_buscar_equipo.get().lower()
        if not hasattr(self, 'equipos_completos') or not self.equipos_completos: return
        filtrados = [eq for eq in self.equipos_completos if texto in eq.lower()]
        
        if filtrados:
            self.combo_equipo.configure(values=filtrados)
            self.combo_equipo.set(filtrados[0])
        else:
            self.combo_equipo.configure(values=["Sin coincidencias"])
            self.combo_equipo.set("Sin coincidencias")

    def str_to_date(self, date_str):
        try: return datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            try: return datetime.strptime(date_str, "%m/%d/%Y").date()
            except ValueError: return None

    def guardar_recepcion(self):
        equipo_str = self.combo_equipo.get()
        persona = self.ent_persona.get().strip()
        fecha_str = self.ent_fecha.get().strip()
        condicion = self.cmb_condicion.get()
        
        try:
            cantidad_devuelta = int(self.ent_cantidad.get().strip())
            if cantidad_devuelta <= 0: raise ValueError
        except ValueError:
            return messagebox.showerror("Error", "La cantidad debe ser un número entero mayor a 0.")

        notas = ""
        if condicion != "Óptimas condiciones":
            notas = self.txt_nota.get("1.0", tk.END).strip()
            if not notas:
                return messagebox.showwarning("Atención", f"Ha reportado el equipo como '{condicion}'.\nDebe escribir los detalles obligatoriamente.")
                
        if "No hay equipos" in equipo_str or "Sin coincidencias" in equipo_str or not persona or not fecha_str:
            return messagebox.showwarning("Atención", "Seleccione una reserva/equipo válido y complete quién entrega y la fecha.")
            
        fecha_dt = self.str_to_date(fecha_str)
        if not fecha_dt:
            return messagebox.showerror("Error", "Formato de fecha inválido.")
            
        try:
            reserva_id = int(equipo_str.split("Reserva #")[1].split(" -")[0])
            equipo_id = int(equipo_str.split("Eq: #")[1].split("]")[0])
        except Exception:
            return messagebox.showerror("Error", "No se pudo interpretar el código de reserva del equipo.")

        conn = conectar_db()
        if not conn:
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden registrar retornos en Modo Lectura.")
        try:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO inventario_recepciones (equipo_id, persona_entrega, condicion, detalles, fecha_recepcion, cantidad, ruta_evidencia)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (equipo_id, persona, condicion, notas, fecha_dt, cantidad_devuelta, self.ruta_evidencia_actual))
            
            cursor.execute("SELECT cantidad FROM inventario_reservas WHERE id = %s", (reserva_id,))
            res_cant = cursor.fetchone()
            if res_cant:
                cant_reserva_actual = res_cant[0]
                if cantidad_devuelta >= cant_reserva_actual:
                    cursor.execute("DELETE FROM inventario_reservas WHERE id = %s", (reserva_id,))
                else:
                    cursor.execute("UPDATE inventario_reservas SET cantidad = cantidad - %s WHERE id = %s", (cantidad_devuelta, reserva_id))

            if condicion in ["Dañado / Roto", "Requiere Mantenimiento urgente", "Faltan piezas / Incompleto"]:
                if messagebox.askyesno("Alerta de Condición", f"El equipo fue reportado con problemas.\n\n¿Desea cambiar el estado del equipo en el catálogo principal a 'En Mantenimiento' de forma automática?"):
                    cursor.execute("UPDATE inventario_equipos SET estado = 'En Mantenimiento' WHERE id = %s", (equipo_id,))
            
            conn.commit()
            cache_sistema.invalidar()
            
            if hasattr(self.app_padre, 'app_catalogo'):
                self.app_padre.app_catalogo.cargar_tabla(reset_pagina=True)
            if hasattr(self.app_padre, 'app_reservas'):
                self.app_padre.app_reservas.cargar_tabla(reset_pagina=True)
                self.app_padre.app_reservas.cargar_combos() 

            registrar_auditoria(self.app_padre.usuario_activo, "Inventario", f"Recibió {cantidad_devuelta}x equipo ID {equipo_id} ({condicion}) entregado por {persona}")
            messagebox.showinfo("Éxito", "Recepción registrada y reserva liberada/actualizada correctamente.")
            
            self.ent_cantidad.delete(0, tk.END)
            self.ent_cantidad.insert(0, "1")
            self.ent_persona.delete(0, tk.END)
            self.txt_nota.delete("1.0", tk.END)
            self.cmb_condicion.set("Óptimas condiciones")
            self.al_seleccionar_condicion("Óptimas condiciones")
            self.cargar_combos()
            self.cargar_tabla(reset_pagina=True)
            
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", str(e))
        finally:
            liberar_conexion(conn)

    # 🚀 FIX: CARGA LAZY LOADING + CACHÉ
    def cargar_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        filtro = self.ent_buscar_recepcion.get().strip().lower() if hasattr(self, 'ent_buscar_recepcion') else ""
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"inventario_retornos_{filtro}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos)
        else:
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", "", ""))
            
            def tarea():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if filtro == "":
                            query = """
                                SELECT r.id, r.fecha_recepcion, e.codigo, e.nombre, e.numero_serial, r.cantidad, r.persona_entrega, r.condicion, r.detalles 
                                FROM inventario_recepciones r
                                JOIN inventario_equipos e ON r.equipo_id = e.id
                                ORDER BY r.id DESC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (self.registros_por_pagina, offset))
                        else:
                            val = f"%{filtro}%"
                            query = """
                                SELECT r.id, r.fecha_recepcion, e.codigo, e.nombre, e.numero_serial, r.cantidad, r.persona_entrega, r.condicion, r.detalles 
                                FROM inventario_recepciones r
                                JOIN inventario_equipos e ON r.equipo_id = e.id
                                WHERE e.nombre ILIKE %s OR r.persona_entrega ILIKE %s OR r.condicion ILIKE %s OR r.detalles ILIKE %s
                                ORDER BY r.id DESC LIMIT %s OFFSET %s
                            """
                            cursor.execute(query, (val, val, val, val, self.registros_por_pagina, offset))
                            
                        fmt_salida = "%d/%m/%Y" if CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA") == "DD/MM/AAAA" else "%m/%d/%Y"
                        
                        for r in cursor.fetchall():
                            f_rec = r[1].strftime(fmt_salida) if r[1] else ""
                            sn_text = f" (SN: {r[4]})" if r[4] else ""
                            eq_display = f"{r[2]} - {r[3]}{sn_text}"
                            nota_completa = r[8] if r[8] else ""
                            nota_limpia = nota_completa.replace('\n', ' | ').replace('\r', '')
                            
                            rows.append((r[0], f_rec, eq_display, r[5], r[6], r[7], nota_limpia))
                            
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


# =========================================================
# CLASE PRINCIPAL: MÓDULO DE INVENTARIO
# =========================================================
class InventarioApp:
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
        
        ctk.CTkLabel(header_frame, text="📦 MÓDULO DE INVENTARIO Y ALMACÉN", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        self.btn_pantalla = ctk.CTkButton(header_frame, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="right")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d", command=self.al_cambiar_tab)
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        
        self.tab_catalogo = self.tabview.add(" 📋 1. Catálogo de Equipos ")
        self.tab_reservas = self.tabview.add(" 📅 2. Control de Reservas ")
        self.tab_recepcion = self.tabview.add(" 📥 3. Recepción y Retornos ")
        
        self.app_catalogo = CatalogoEquiposTab(self.tab_catalogo, self.parent_frame, self)
        self.app_reservas = ReservasTab(self.tab_reservas, self.parent_frame, self)
        self.app_recepcion = RecepcionEquiposTab(self.tab_recepcion, self.parent_frame, self)

    def al_cambiar_tab(self):
        pestana = self.tabview.get().strip()
        if "2. Control de Reservas" in pestana:
            self.app_reservas.cargar_combos()
            self.app_reservas.cargar_tabla(reset_pagina=True)
        elif "3. Recepción y Retornos" in pestana:
            self.app_recepcion.cargar_combos()
            self.app_recepcion.cargar_tabla(reset_pagina=True)

    def inicializar_bd(self):
        global _SCHEMA_INV_OK
        if _SCHEMA_INV_OK: return
        
        def tarea_init():
            global _SCHEMA_INV_OK
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS inventario_equipos (
                        id SERIAL PRIMARY KEY,
                        codigo VARCHAR(50) UNIQUE,
                        nombre VARCHAR(255),
                        categoria VARCHAR(100),
                        cantidad_total INTEGER DEFAULT 0,
                        marca_modelo VARCHAR(255) DEFAULT '',
                        estado VARCHAR(50) DEFAULT 'Operativo',
                        precio_costo NUMERIC DEFAULT 0,
                        depreciacion NUMERIC DEFAULT 0,
                        ruta_imagen TEXT DEFAULT '',
                        numero_serial TEXT DEFAULT ''
                    )
                """)
                
                for sql in (
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS marca_modelo VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS estado VARCHAR(50) DEFAULT 'Operativo'",
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS precio_costo NUMERIC DEFAULT 0",
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS depreciacion NUMERIC DEFAULT 0",
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS ruta_imagen TEXT DEFAULT ''",
                    "ALTER TABLE inventario_equipos ADD COLUMN IF NOT EXISTS numero_serial TEXT DEFAULT ''"
                ):
                    try: cursor.execute(sql); conn.commit()
                    except Exception: conn.rollback()

                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS inventario_reservas (
                        id SERIAL PRIMARY KEY,
                        equipo_id INTEGER REFERENCES inventario_equipos(id) ON DELETE CASCADE,
                        evento_asociado VARCHAR(255),
                        fecha_inicio DATE,
                        fecha_fin DATE,
                        cantidad INTEGER DEFAULT 0,
                        notas TEXT DEFAULT '',
                        autorizado_por VARCHAR(255) DEFAULT '',
                        cargo_autoriza VARCHAR(255) DEFAULT ''
                    )
                """)
                
                for sql in (
                    "ALTER TABLE inventario_reservas ADD COLUMN IF NOT EXISTS notas TEXT DEFAULT ''",
                    "ALTER TABLE inventario_reservas ADD COLUMN IF NOT EXISTS autorizado_por VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE inventario_reservas ADD COLUMN IF NOT EXISTS cargo_autoriza VARCHAR(255) DEFAULT ''"
                ):
                    try: cursor.execute(sql); conn.commit()
                    except Exception: conn.rollback()
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS inventario_recepciones (
                        id SERIAL PRIMARY KEY,
                        equipo_id INTEGER REFERENCES inventario_equipos(id) ON DELETE CASCADE,
                        persona_entrega VARCHAR(255),
                        condicion VARCHAR(100),
                        detalles TEXT,
                        fecha_recepcion DATE,
                        cantidad INTEGER DEFAULT 1,
                        ruta_evidencia TEXT DEFAULT ''
                    )
                """)
                
                for sql in (
                    "ALTER TABLE inventario_recepciones ADD COLUMN IF NOT EXISTS cantidad INTEGER DEFAULT 1",
                    "ALTER TABLE inventario_recepciones ADD COLUMN IF NOT EXISTS ruta_evidencia TEXT DEFAULT ''"
                ):
                    try: cursor.execute(sql); conn.commit()
                    except Exception: conn.rollback()
                
                _SCHEMA_INV_OK = True
            except Exception as e:
                print("Error creando tablas de inventario:", e)
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