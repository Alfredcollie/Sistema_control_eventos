# -*- coding: utf-8 -*-
import psycopg2
import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox
import sys
import ctypes
import urllib.request
import json
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# MULTIPLATAFORMA
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass

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

_SCHEMA_CLIENTES_OK = False

def inicializar_db_clientes():
    global _SCHEMA_CLIENTES_OK
    if _SCHEMA_CLIENTES_OK:
        return

    conn = conectar_db(silencioso=True)
    if not conn:
        return

    try:
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            ruc VARCHAR(11) UNIQUE NOT NULL,
            nombre_empresa VARCHAR(255) NOT NULL,
            direccion_fiscal TEXT,
            persona_contacto VARCHAR(255),
            telefono VARCHAR(50),
            correo VARCHAR(255),
            pagina_web VARCHAR(255),
            notas TEXT
        )
        ''')
        try:
            cursor.execute("ALTER TABLE clientes ADD COLUMN limite_credito NUMERIC DEFAULT 0.0")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cursor.execute("ALTER TABLE clientes ADD COLUMN razon_comercial VARCHAR(255) DEFAULT ''")
            conn.commit()
        except Exception:
            conn.rollback()
        conn.commit()
        cursor.close()
        _SCHEMA_CLIENTES_OK = True
    except Exception as e:
        print(f"Error SQL al crear tabla clientes: {e}")
    finally:
        # 🚀 LIBERAMOS LA CONEXIÓN PARA EL POOL
        liberar_conexion(conn)

class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 25
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(1)
        tw.wm_geometry(f"+{x}+{y}")
        is_dark = ctk.get_appearance_mode() == "Dark"
        bg_color = "#2b2b2b" if is_dark else "#ffffff"
        fg_color = "#ffffff" if is_dark else "#000000"
        label = tk.Label(tw, text=self.text, justify='left', background=bg_color, foreground=fg_color, relief='solid', borderwidth=1, font=("Arial", "9", "normal"))
        label.pack(ipadx=4, ipady=2)

    def hide_tip(self, event=None):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


class SistemaClientes:
    def __init__(self, root):
        self.root = root
        self.usuario_activo = "Desconocido"
        self.root.title("Gestión de Clientes - Control General")
        
        # 🚀 VARIABLES DE PAGINACIÓN (LAZY LOADING)
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        inicializar_db_clientes()
        
        # ENCABEZADO SUPERIOR DE EVENTOS
        header_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        header_frame.pack(fill="x", padx=15, pady=(15, 0))
        ctk.CTkLabel(header_frame, text="👥 GESTIÓN DE CLIENTES", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        
        self.tabview = ctk.CTkTabview(self.root, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, padx=15, pady=15)
        self.tab_buscar = self.tabview.add(" 🔍 Buscar Clientes ")
        self.tab_incluir = self.tabview.add(" ➕ Incluir Cliente ")
        
        self.crear_tab_buscar()
        self.crear_tab_incluir()

    def crear_tab_buscar(self):
        frame_busqueda = ctk.CTkFrame(self.tab_buscar, corner_radius=8, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        frame_busqueda.pack(fill="x", padx=10, pady=10, ipady=5)
        
        lbl_buscar = ctk.CTkLabel(frame_busqueda, text="🔍 Buscar:", font=("Arial", 12, "bold"), text_color="#333333")
        lbl_buscar.pack(side="left", padx=(15, 5), pady=10)
        
        self.ent_buscar = ctk.CTkEntry(frame_busqueda, placeholder_text="Escribe RUC, Empresa, Comercial, Contacto...", border_color="#cccccc")
        self.ent_buscar.pack(side="left", fill="x", expand=True, padx=5, pady=10)
        
        self.ent_buscar.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_buscar.bind("<Return>", lambda e: self.cargar_clientes_tabla(reset_pagina=True))
        
        btn_limpiar_b = ctk.CTkButton(frame_busqueda, text="🔄 Limpiar", width=90, font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_busqueda)
        btn_limpiar_b.pack(side="left", padx=5, pady=10)
        ToolTip(btn_limpiar_b, "Limpia la búsqueda y muestra todos.")
        
        frame_acciones = ctk.CTkFrame(frame_busqueda, fg_color="transparent")
        frame_acciones.pack(side="right", padx=10)
        
        self.btn_editar = ctk.CTkButton(frame_acciones, text="✏️ Editar", width=100, font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.abrir_ventana_editar, state="disabled")
        self.btn_editar.pack(side="left", padx=5)
        
        self.btn_eliminar_c = ctk.CTkButton(frame_acciones, text="❌ Eliminar", width=100, font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.ejecutar_eliminacion_cliente, state="disabled")
        self.btn_eliminar_c.pack(side="left", padx=5)
        
        frame_tabla = ctk.CTkFrame(self.tab_buscar, corner_radius=10)
        frame_tabla.pack(fill="both", expand=True, padx=10, pady=5)
        
        columnas = ("num", "id", "ruc", "nombre", "comercial", "contacto", "telefono", "correo")
        style = tk.ttk.Style()
        style.theme_use("clam")
        bg_blanco, fg_negro, bg_seleccion, border_color = "#ffffff", "#000000", "#1f538d", "#e0e0e0"
        style.configure("Treeview", background=bg_blanco, foreground=fg_negro, fieldbackground=bg_blanco, rowheight=28, font=("Arial", 10), bordercolor=border_color, borderwidth=1)
        style.map("Treeview", background=[("selected", bg_seleccion)], foreground=[("selected", bg_blanco)])
        style.configure("Treeview.Heading", background="#f0f0f0", foreground=fg_negro, font=("Arial", 10, "bold"), bordercolor=border_color, borderwidth=1, relief="flat")
        
        self.tabla = tk.ttk.Treeview(frame_tabla, columns=columnas, show="headings")
        self.tabla.heading("num", text="N°", anchor="center")
        self.tabla.heading("id", text="ID (Oculto)")
        self.tabla.heading("ruc", text="RUC", anchor="center", command=lambda: self.ordenar_columna("ruc", False))
        self.tabla.heading("nombre", text="Razón Social", anchor="center", command=lambda: self.ordenar_columna("nombre", False))
        self.tabla.heading("comercial", text="Razón Comercial", anchor="center", command=lambda: self.ordenar_columna("comercial", False))
        self.tabla.heading("contacto", text="Persona Contacto", anchor="center", command=lambda: self.ordenar_columna("contacto", False))
        self.tabla.heading("telefono", text="Teléfono", anchor="center", command=lambda: self.ordenar_columna("telefono", False))
        self.tabla.heading("correo", text="Correo Electrónico", anchor="center", command=lambda: self.ordenar_columna("correo", False))
        
        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id", width=0, stretch=tk.NO)
        self.tabla.column("ruc", width=120, anchor="center")
        self.tabla.column("nombre", width=220, anchor="w")
        self.tabla.column("comercial", width=200, anchor="w")
        self.tabla.column("contacto", width=180, anchor="w")
        self.tabla.column("telefono", width=120, anchor="center")
        self.tabla.column("correo", width=220, anchor="w")
        self.tabla.config(displaycolumns=("num", "ruc", "nombre", "comercial", "contacto", "telefono", "correo"))
        
        scroll_y = ctk.CTkScrollbar(frame_tabla, orientation="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        
        self.tabla.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll_y.pack(side="right", fill="y", padx=(0, 10), pady=10)
        
        self.tabla.bind("<<TreeviewSelect>>", self.on_fila_seleccionada)
        self.tabla.bind("<Double-1>", lambda event: self.abrir_ventana_editar())
        
        # 🚀 BOTONES DE PAGINACIÓN
        frame_paginacion = ctk.CTkFrame(self.tab_buscar, fg_color="transparent")
        frame_paginacion.pack(fill="x", padx=10, pady=(0, 10))
        
        self.btn_ant = ctk.CTkButton(frame_paginacion, text="◀ Anterior", width=100, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=10)
        
        self.lbl_pagina = ctk.CTkLabel(frame_paginacion, text=f"Página {self.pagina_actual}", font=("Arial", 12, "bold"))
        self.lbl_pagina.pack(side="left", expand=True)
        
        self.btn_sig = ctk.CTkButton(frame_paginacion, text="Siguiente ▶", width=100, command=self.pagina_siguiente)
        self.btn_sig.pack(side="right", padx=10)

        self.root.after(100, lambda: self.cargar_clientes_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_clientes_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_clientes_tabla()

    def buscar_con_retraso(self):
        if hasattr(self, "_busqueda_job"):
            try:
                self.root.after_cancel(self._busqueda_job)
            except Exception:
                pass
        self._busqueda_job = self.root.after(350, lambda: self.cargar_clientes_tabla(reset_pagina=True))

    def limpiar_busqueda(self):
        self.ent_buscar.delete(0, tk.END)
        self.cargar_clientes_tabla(reset_pagina=True)

    def cargar_clientes_tabla(self, reset_pagina=False):
        """🚀 Motor Lazy Loading con Caché Inteligente (Reemplaza los hilos)"""
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Página {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
        
        if hasattr(self, 'btn_editar'):
            self.btn_editar.configure(state="disabled")
            self.btn_eliminar_c.configure(state="disabled")
            
        texto = self.ent_buscar.get().strip().lower()
        offset = (self.pagina_actual - 1) * self.registros_por_pagina
        
        clave_cache = f"clientes_eventos_{texto}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is None:
            conn = conectar_db(silencioso=True)
            if not conn: return
                
            try:
                cursor = conn.cursor()
                if texto == "":
                    cursor.execute("""
                        SELECT id, ruc, nombre_empresa, COALESCE(razon_comercial, ''), persona_contacto, telefono, correo 
                        FROM clientes 
                        ORDER BY nombre_empresa ASC 
                        LIMIT %s OFFSET %s
                    """, (self.registros_por_pagina, offset))
                else:
                    query = '''SELECT id, ruc, nombre_empresa, COALESCE(razon_comercial, ''), persona_contacto, telefono, correo FROM clientes 
                               WHERE ruc ILIKE %s OR nombre_empresa ILIKE %s OR razon_comercial ILIKE %s OR direccion_fiscal ILIKE %s 
                               OR persona_contacto ILIKE %s OR telefono ILIKE %s OR correo ILIKE %s OR pagina_web ILIKE %s 
                               ORDER BY nombre_empresa ASC LIMIT %s OFFSET %s'''
                    val = f"%{texto}%"
                    cursor.execute(query, (val, val, val, val, val, val, val, val, self.registros_por_pagina, offset))
                
                datos = cursor.fetchall()
                cache_sistema.guardar(clave_cache, datos)
            except Exception as e:
                messagebox.showerror("Error", f"Error al consultar clientes: {e}")
                datos = []
            finally:
                # 🚀 FIX: Liberar conexión
                liberar_conexion(conn)

        contador_visual = offset + 1
        for row in datos: 
            valores = (contador_visual, row[0], row[1], row[2], row[3], row[4], row[5], row[6])
            self.tabla.insert("", tk.END, values=valores)
            contador_visual += 1
            
        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")
            
        if len(datos) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")

    def ordenar_columna(self, col, reverse):
        l = [(self.tabla.set(k, col), k) for k in self.tabla.get_children('')]
        try:
            l.sort(key=lambda t: float(t[0].replace(",", "") if t[0] else 0), reverse=reverse)
        except ValueError:
            l.sort(key=lambda t: str(t[0]).lower(), reverse=reverse)
        for index, (val, k) in enumerate(l):
            self.tabla.move(k, '', index)
            valores_actuales = list(self.tabla.item(k, "values"))
            valores_actuales[0] = index + 1
            self.tabla.item(k, values=valores_actuales)
        self.tabla.heading(col, command=lambda: self.ordenar_columna(col, not reverse))

    def on_fila_seleccionada(self, event):
        if self.tabla.selection():
            self.btn_editar.configure(state="normal")
            self.btn_eliminar_c.configure(state="normal")
        else:
            self.btn_editar.configure(state="disabled")
            self.btn_eliminar_c.configure(state="disabled")

    def ejecutar_eliminacion_cliente(self):
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden eliminar clientes en Modo Lectura.")
            return
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        valores = self.tabla.item(seleccion[0], "values")
        id_cli = valores[1]
        nombre_cli = valores[3]
        if messagebox.askyesno("Confirmar Eliminación", f"¿Desea eliminar permanentemente al cliente:\n\n'{nombre_cli}' (Ref. Interna: {id_cli})?"):
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM clientes WHERE id = %s", (id_cli,))
                conn.commit()
                # 🚀 Borramos el caché
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Clientes", f"Eliminó permanentemente al cliente '{nombre_cli}' (ID: {id_cli})")
                messagebox.showinfo("Éxito", "Cliente eliminado correctamente.")
                self.cargar_clientes_tabla(reset_pagina=True)
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo eliminar: {str(e)}")
            finally:
                liberar_conexion(conn)

    # =======================================================
    # UTILIDADES DE PORTAPAPELES
    # =======================================================
    def portapapeles_copiar(self, widget, nombre_campo):
        self.root.clipboard_clear()
        if hasattr(widget, 'get') and not isinstance(widget, ctk.CTkTextbox):
            self.root.clipboard_append(widget.get())
        else:
            self.root.clipboard_append(widget.get("1.0", tk.END).strip())
        messagebox.showinfo("Copiado", f"Texto de {nombre_campo} copiado al portapapeles.")

    def portapapeles_pegar(self, widget):
        try:
            texto = self.root.clipboard_get()
            if hasattr(widget, 'delete') and not isinstance(widget, ctk.CTkTextbox):
                widget.delete(0, tk.END)
                widget.insert(0, texto)
            else:
                widget.delete("1.0", tk.END)
                widget.insert("1.0", texto)
        except tk.TclError:
            pass

    def crear_boton_cp(self, parent, row, col, widget, nombre_campo):
        f_btn = ctk.CTkFrame(parent, fg_color="transparent")
        f_btn.grid(row=row, column=col, sticky="w", padx=5, pady=5)
        btn_p = ctk.CTkButton(f_btn, text="📋", width=32, height=32, font=("Arial", 12), fg_color="#e0e0e0", hover_color="#c8c8c8", text_color="black", command=lambda: self.portapapeles_pegar(widget))
        btn_p.pack(side="left", padx=2)
        ToolTip(btn_p, f"Pega el contenido en {nombre_campo}.")
        btn_c = ctk.CTkButton(f_btn, text="↗", width=32, height=32, font=("Arial", 12), fg_color="#e0e0e0", hover_color="#c8c8c8", text_color="black", command=lambda: self.portapapeles_copiar(widget, nombre_campo))
        btn_c.pack(side="left", padx=2)
        ToolTip(btn_c, f"Copia el contenido de {nombre_campo}.")

    # =======================================================
    # CONSULTA DE RUC (SUNAT) EN SEGUNDO PLANO - MANTENIDO
    # =======================================================
    def consultar_ruc_api(self, ruc_entry, nombre_entry, dir_entry):
        ruc = ruc_entry.get().strip()
        if len(ruc) != 11 or not ruc.isdigit():
            return messagebox.showwarning("RUC Inválido", "Por favor, ingrese un RUC válido de 11 dígitos antes de presionar Enter.")

        def tarea():
            try:
                url = f"https://api.apis.net.pe/v1/ruc?numero={ruc}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=5) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        self.root.after(0, lambda: self._aplicar_datos_ruc(data, nombre_entry, dir_entry))
                    else:
                        self.root.after(0, lambda: messagebox.showwarning("Sin Resultados", "No se encontró información para este RUC o el servicio está inactivo."))
            except urllib.error.URLError:
                self.root.after(0, lambda: messagebox.showerror("Error de Conexión", "No se pudo conectar al servicio de consulta de RUC. Verifique su internet."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("Error", f"Ocurrió un problema al consultar el RUC: {e}"))

        threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_datos_ruc(self, data, nombre_entry, dir_entry):
        nombre_entry.delete(0, tk.END)
        nombre_entry.insert(0, data.get("nombre", ""))
        dir_entry.delete(0, tk.END)
        direccion = data.get("direccion", "").strip()
        if direccion == "-":
            direccion = ""
        if not direccion:
            direccion = data.get("direccion_completa", data.get("direccionCompleta", "")).strip()
            if direccion == "-":
                direccion = ""
        distrito = data.get("distrito", "").strip()
        provincia = data.get("provincia", "").strip()
        departamento = data.get("departamento", "").strip()
        if distrito == "-":
            distrito = ""
        if provincia == "-":
            provincia = ""
        if departamento == "-":
            departamento = ""
        ubicacion_parts = [p for p in [distrito, provincia, departamento] if p]
        if ubicacion_parts:
            str_ubicacion = ", ".join(ubicacion_parts)
            if direccion and distrito not in direccion:
                direccion = f"{direccion} - {str_ubicacion}"
            elif not direccion:
                direccion = str_ubicacion
        if not direccion:
            direccion = "Dirección no pública o no registrada en SUNAT"
        dir_entry.insert(0, direccion)
        messagebox.showinfo("Consulta Exitosa", f"Datos recuperados para:\n{data.get('nombre', '')}")
        
    def crear_tab_incluir(self):
        scroll_frame = ctk.CTkScrollableFrame(self.tab_incluir, fg_color="transparent")
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)
        f1 = ctk.CTkFrame(scroll_frame, corner_radius=12)
        f1.pack(fill="x", padx=10, pady=10, ipady=15)
        f1.columnconfigure(1, weight=1)
        f1.columnconfigure(4, weight=1)
        ctk.CTkLabel(f1, text="Datos Generales del Cliente", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        ctk.CTkLabel(f1, text="RUC:\n(Presiona Enter para buscar)", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_ruc = ctk.CTkEntry(f1, placeholder_text="11 dígitos y presiona ENTER")
        self.ent_ruc.grid(row=1, column=1, sticky="ew", pady=8)
        self.ent_ruc.bind("<Return>", lambda e: self.consultar_ruc_api(self.ent_ruc, self.ent_empresa, self.ent_direccion))
        self.crear_boton_cp(f1, 1, 2, self.ent_ruc, "el RUC")
        ctk.CTkLabel(f1, text="Razón Social:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_empresa = ctk.CTkEntry(f1)
        self.ent_empresa.grid(row=1, column=4, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 1, 5, self.ent_empresa, "la Razón Social")
        ctk.CTkLabel(f1, text="Razón Comercial:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_comercial = ctk.CTkEntry(f1, placeholder_text="Nombre de Marca / Comercial")
        self.ent_comercial.grid(row=2, column=1, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 2, 2, self.ent_comercial, "la Razón Comercial")
        ctk.CTkLabel(f1, text="Dirección Fiscal:", font=("Arial", 12, "bold")).grid(row=2, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_direccion = ctk.CTkEntry(f1)
        self.ent_direccion.grid(row=2, column=4, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 2, 5, self.ent_direccion, "la Dirección Fiscal")
        ctk.CTkLabel(f1, text="Persona Contacto:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_contacto = ctk.CTkEntry(f1)
        self.ent_contacto.grid(row=3, column=1, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 3, 2, self.ent_contacto, "la Persona de Contacto")
        ctk.CTkLabel(f1, text="Teléfono / Celular:", font=("Arial", 12, "bold")).grid(row=3, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_telefono = ctk.CTkEntry(f1)
        self.ent_telefono.grid(row=3, column=4, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 3, 5, self.ent_telefono, "el Teléfono")
        ctk.CTkLabel(f1, text="Correo Electrónico:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_correo = ctk.CTkEntry(f1)
        self.ent_correo.grid(row=4, column=1, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 4, 2, self.ent_correo, "el Correo")
        ctk.CTkLabel(f1, text="Página Web:", font=("Arial", 12, "bold")).grid(row=4, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_web = ctk.CTkEntry(f1, placeholder_text="www.ejemplo.com")
        self.ent_web.grid(row=4, column=4, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 4, 5, self.ent_web, "la Página Web")
        ctk.CTkLabel(f1, text="Límite de Crédito (S/.):", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_limite = ctk.CTkEntry(f1, placeholder_text="Dejar vacío = Sin límite")
        self.ent_limite.grid(row=5, column=1, sticky="ew", pady=8)
        self.crear_boton_cp(f1, 5, 2, self.ent_limite, "el Límite de Crédito")
        ctk.CTkLabel(f1, text="Notas del Cliente:\n(Max 500 carac.)", font=("Arial", 12, "bold")).grid(row=6, column=0, sticky="nw", padx=(20, 5), pady=12)
        self.txt_notas = ctk.CTkTextbox(f1, height=110, font=("Arial", 12), border_width=1)
        self.txt_notas.grid(row=6, column=1, columnspan=4, sticky="ew", pady=12)
        self.crear_boton_cp(f1, 6, 5, self.txt_notas, "las Notas")
        self.lbl_contador = ctk.CTkLabel(f1, text="Caracteres restantes: 500", font=("Arial", 11), text_color="gray")
        self.lbl_contador.grid(row=7, column=1, sticky="w", padx=2)

        def limitar_caracteres_inc(event):
            texto = self.txt_notas.get("1.0", "end-1c")
            if len(texto) > 500:
                self.txt_notas.delete("1.0", tk.END)
                self.txt_notas.insert("1.0", texto[:500])
                texto = texto[:500]
            restantes = 500 - len(texto)
            self.lbl_contador.configure(text=f"Caracteres restantes: {restantes}")

        self.txt_notas.bind("<KeyRelease>", limitar_caracteres_inc)
        btn_guardar = ctk.CTkButton(scroll_frame, text="💾 Guardar Nuevo Cliente", font=("Arial", 14, "bold"), width=250, height=40, command=self.guardar_cliente)
        btn_guardar.pack(pady=25)
        ToolTip(btn_guardar, "Inserta al nuevo cliente en la base de datos.")

    def guardar_cliente(self):
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nEl sistema no permite guardar nuevos registros.")
            return
        ruc = self.ent_ruc.get().strip()
        empresa = self.ent_empresa.get().strip()
        comercial = self.ent_comercial.get().strip()
        direccion = self.ent_direccion.get().strip()
        contacto = self.ent_contacto.get().strip()
        telefono = self.ent_telefono.get().strip()
        correo = self.ent_correo.get().strip()
        web = self.ent_web.get().strip()
        notas = self.txt_notas.get("1.0", "end-1c").strip()
        try:
            limite_val = self.ent_limite.get().strip().replace(',', '')
            limite_credito = float(limite_val) if limite_val else 0.0
        except ValueError:
            return messagebox.showerror("Error", "El límite de crédito debe ser un número válido.")
        if len(ruc) != 11 or not ruc.isdigit():
            messagebox.showwarning("Campos Incompletos", "El RUC debe contener exactamente 11 números.")
            return
        if not empresa:
            messagebox.showwarning("Campos Incompletos", "Debe ingresar la Razón Social de la Empresa.")
            return
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO clientes (ruc, nombre_empresa, razon_comercial, direccion_fiscal, persona_contacto, telefono, correo, pagina_web, limite_credito, notas)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (ruc, empresa, comercial, direccion, contacto, telefono, correo, web, limite_credito, notas))
            conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Clientes", f"Registró al nuevo cliente '{empresa}' (RUC: {ruc})")
            messagebox.showinfo("Éxito", f"El cliente '{empresa}' se registró correctamente.")
            self.limpiar_formulario_incluir()
            self.cargar_clientes_tabla(reset_pagina=True)
        except psycopg2.IntegrityError:
            conn.rollback()
            messagebox.showerror("Error de Duplicidad", "Este RUC ya se encuentra registrado.")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error SQL", f"No se pudo guardar el cliente:\n{e}")
        finally:
            liberar_conexion(conn)

    def limpiar_formulario_incluir(self):
        self.ent_ruc.delete(0, tk.END)
        self.ent_empresa.delete(0, tk.END)
        self.ent_comercial.delete(0, tk.END)
        self.ent_direccion.delete(0, tk.END)
        self.ent_contacto.delete(0, tk.END)
        self.ent_telefono.delete(0, tk.END)
        self.ent_correo.delete(0, tk.END)
        self.ent_web.delete(0, tk.END)
        self.ent_limite.delete(0, tk.END)
        self.txt_notas.delete("1.0", tk.END)
        self.lbl_contador.configure(text="Caracteres restantes: 500")

    def abrir_ventana_editar(self):
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nEl sistema se encuentra en Modo Lectura y no permite editar información.")
            return
        seleccion = self.tabla.selection()
        if not seleccion:
            return
        items = self.tabla.item(seleccion[0], "values")
        id_cli = items[1]
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, ruc, nombre_empresa, COALESCE(razon_comercial, ''), direccion_fiscal, persona_contacto, telefono, correo, pagina_web, limite_credito, notas
                FROM clientes WHERE id = %s
            ''', (id_cli,))
            p = cursor.fetchone()
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al cargar datos:\n{e}")
            return
        finally:
            liberar_conexion(conn)
        if not p:
            return

        v_edit = ctk.CTkToplevel(self.root)
        v_edit.title(f"Modificar Cliente Registrado - Ref. Interna: {id_cli}")
        v_edit.after(100, lambda: maximizar_ventana(v_edit))
        v_edit.grab_set()
        scroll_frame_e = ctk.CTkScrollableFrame(v_edit, fg_color="transparent")
        scroll_frame_e.pack(fill="both", expand=True, padx=5, pady=5)
        f1 = ctk.CTkFrame(scroll_frame_e, corner_radius=12)
        f1.pack(fill="x", padx=10, pady=10, ipady=15)
        f1.columnconfigure(1, weight=1)
        f1.columnconfigure(4, weight=1)
        ctk.CTkLabel(f1, text="Datos del Cliente a Modificar", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        ctk.CTkLabel(f1, text="RUC:\n(Presiona Enter para buscar)", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_ruc = ctk.CTkEntry(f1)
        ent_e_ruc.grid(row=1, column=1, sticky="ew", pady=8)
        ent_e_ruc.insert(0, str(p[1]) if p[1] else "")
        self.crear_boton_cp(f1, 1, 2, ent_e_ruc, "el RUC")
        ctk.CTkLabel(f1, text="Razón Social:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_empresa = ctk.CTkEntry(f1)
        ent_e_empresa.grid(row=1, column=4, sticky="ew", pady=8)
        ent_e_empresa.insert(0, str(p[2]) if p[2] else "")
        self.crear_boton_cp(f1, 1, 5, ent_e_empresa, "la Razón Social")
        ctk.CTkLabel(f1, text="Razón Comercial:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_comercial = ctk.CTkEntry(f1)
        ent_e_comercial.grid(row=2, column=1, sticky="ew", pady=8)
        ent_e_comercial.insert(0, str(p[3]) if p[3] else "")
        self.crear_boton_cp(f1, 2, 2, ent_e_comercial, "la Razón Comercial")
        ctk.CTkLabel(f1, text="Dirección Fiscal:", font=("Arial", 12, "bold")).grid(row=2, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_direccion = ctk.CTkEntry(f1)
        ent_e_direccion.grid(row=2, column=4, sticky="ew", pady=8)
        ent_e_direccion.insert(0, str(p[4]) if p[4] else "")
        self.crear_boton_cp(f1, 2, 5, ent_e_direccion, "la Dirección Fiscal")
        ent_e_ruc.bind("<Return>", lambda e: self.consultar_ruc_api(ent_e_ruc, ent_e_empresa, ent_e_direccion))
        ctk.CTkLabel(f1, text="Persona Contacto:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_contacto = ctk.CTkEntry(f1)
        ent_e_contacto.grid(row=3, column=1, sticky="ew", pady=8)
        ent_e_contacto.insert(0, str(p[5]) if p[5] else "")
        self.crear_boton_cp(f1, 3, 2, ent_e_contacto, "la Persona de Contacto")
        ctk.CTkLabel(f1, text="Teléfono / Celular:", font=("Arial", 12, "bold")).grid(row=3, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_telefono = ctk.CTkEntry(f1)
        ent_e_telefono.grid(row=3, column=4, sticky="ew", pady=8)
        ent_e_telefono.insert(0, str(p[6]) if p[6] else "")
        self.crear_boton_cp(f1, 3, 5, ent_e_telefono, "el Teléfono")
        ctk.CTkLabel(f1, text="Correo Electrónico:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_correo = ctk.CTkEntry(f1)
        ent_e_correo.grid(row=4, column=1, sticky="ew", pady=8)
        ent_e_correo.insert(0, str(p[7]) if p[7] else "")
        self.crear_boton_cp(f1, 4, 2, ent_e_correo, "el Correo")
        ctk.CTkLabel(f1, text="Página Web:", font=("Arial", 12, "bold")).grid(row=4, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_web = ctk.CTkEntry(f1)
        ent_e_web.grid(row=4, column=4, sticky="ew", pady=8)
        ent_e_web.insert(0, str(p[8]) if p[8] else "")
        self.crear_boton_cp(f1, 4, 5, ent_e_web, "la Página Web")
        ctk.CTkLabel(f1, text="Límite de Crédito (S/.):", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_limite = ctk.CTkEntry(f1)
        ent_e_limite.grid(row=5, column=1, sticky="ew", pady=8)
        val_limite = p[9] if p[9] is not None else 0.0
        if val_limite > 0:
            ent_e_limite.insert(0, str(val_limite))
        self.crear_boton_cp(f1, 5, 2, ent_e_limite, "el Límite de Crédito")
        ctk.CTkLabel(f1, text="Notas del Cliente:\n(Max 500 carac.)", font=("Arial", 12, "bold")).grid(row=6, column=0, sticky="nw", padx=(20, 5), pady=12)
        txt_e_notes = ctk.CTkTextbox(f1, height=110, font=("Arial", 12), border_width=1)
        txt_e_notes.grid(row=6, column=1, columnspan=4, sticky="ew", pady=12)
        txt_e_notes.insert("1.0", str(p[10]) if p and p[10] else "")
        self.crear_boton_cp(f1, 6, 5, txt_e_notes, "las Notas")
        desc_inicial = txt_e_notes.get("1.0", "end-1c")
        restantes_inicial = 500 - len(desc_inicial)
        lbl_e_contador = ctk.CTkLabel(f1, text=f"Caracteres restantes: {restantes_inicial}", font=("Arial", 11), text_color="gray")
        lbl_e_contador.grid(row=7, column=1, sticky="w", padx=2)

        def limitar_caracteres_edit(event):
            texto = txt_e_notes.get("1.0", "end-1c")
            if len(texto) > 500:
                txt_e_notes.delete("1.0", tk.END)
                txt_e_notes.insert("1.0", texto[:500])
                texto = texto[:500]
            restantes = 500 - len(texto)
            lbl_e_contador.configure(text=f"Caracteres restantes: {restantes}")

        txt_e_notes.bind("<KeyRelease>", limitar_caracteres_edit)

        def ejecutar_update():
            if len(ent_e_ruc.get().strip()) != 11 or not ent_e_ruc.get().strip().isdigit():
                messagebox.showwarning("Error", "El RUC de la empresa debe tener exactamente 11 números.")
                return
            if not ent_e_empresa.get().strip():
                messagebox.showwarning("Error", "Debe ingresar el Nombre de la Empresa.")
                return
            try:
                limite_val = ent_e_limite.get().strip().replace(',', '')
                limite_credito = float(limite_val) if limite_val else 0.0
            except ValueError:
                return messagebox.showerror("Error", "El límite de crédito debe ser un número válido.")
            conn_u = conectar_db()
            if not conn_u:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede guardar.")
                return
            try:
                cursor_u = conn_u.cursor()
                cursor_u.execute('''
                    UPDATE clientes 
                    SET ruc=%s, nombre_empresa=%s, razon_comercial=%s, direccion_fiscal=%s, persona_contacto=%s, telefono=%s, correo=%s, pagina_web=%s, limite_credito=%s, notas=%s
                    WHERE id=%s
                ''', (ent_e_ruc.get().strip(), ent_e_empresa.get().strip(), ent_e_comercial.get().strip(), ent_e_direccion.get().strip(),
                      ent_e_contacto.get().strip(), ent_e_telefono.get().strip(), ent_e_correo.get().strip(),
                      ent_e_web.get().strip(), limite_credito, txt_e_notes.get("1.0", "end-1c").strip(), id_cli))
                conn_u.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Clientes", f"Modificó los datos del cliente ID {id_cli} ({ent_e_empresa.get().strip()})")
                messagebox.showinfo("Éxito", "Los cambios se guardaron correctamente.")
                v_edit.destroy()
                self.cargar_clientes_tabla(reset_pagina=True)
            except psycopg2.IntegrityError:
                conn_u.rollback()
                messagebox.showerror("Error", "Este número de RUC ya pertenece a otra empresa registrada.")
            except Exception as e:
                conn_u.rollback()
                messagebox.showerror("Error SQL", f"No se pudo guardar la edición:\n{e}")
            finally:
                liberar_conexion(conn_u)

        btn_actualizar = ctk.CTkButton(scroll_frame_e, text="💾 Guardar Cambios", font=("Arial", 14, "bold"), fg_color="#1f538d", hover_color="#163b65", width=250, height=40, command=ejecutar_update)
        btn_actualizar.pack(pady=25)
        ToolTip(btn_actualizar, "Sobreescribe los datos en la base de datos.")


if __name__ == "__main__":
    root = ctk.CTk()
    app = SistemaClientes(root)
    root.after(100, lambda: maximizar_ventana(root))
    root.mainloop()