# -*- coding: utf-8 -*-
import psycopg2
import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox, filedialog
import ctypes
import urllib.request
import json
import sys
import threading

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass

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

ZONAS_LIMA = [
    "Lima Centro (Cercado, Breña, Victoria, Rímac, San Luis)",
    "Lima Moderna (Miraflores, San Isidro, San Borja, Surco, Molina)",
    "Lima Moderna 2 (Jesús María, Lince, Magdalena, Pueblo Libre)",
    "Lima Norte (Comas, Los Olivos, SMP, Independencia, Carabayllo)",
    "Lima Sur (Chorrillos, SJM, VES, VMT, Barranco, Lurín)",
    "Lima Este (Ate, SJL, Santa Anita, El Agustino, Chosica)",
    "Callao (Bellavista, La Perla, La Punta, Ventanilla)",
    "Provincias / Extranjero (Fuera de Lima)"
]

BANCOS_PERU = ["BCP", "BBVA", "Interbank", "Scotiabank", "BanBif", "Banco de la Nación", "Pichincha", "Falabella"]

_SCHEMA_PROV_OK = False

# 🚀 FIX 1: AUTO-CURACIÓN EN SEGUNDO PLANO
def inicializar_db_proveedores():
    global _SCHEMA_PROV_OK
    if _SCHEMA_PROV_OK:
        return

    def tarea_curacion():
        global _SCHEMA_PROV_OK
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                c = conn.cursor()
                c.execute("""
                    CREATE TABLE IF NOT EXISTS proveedores (
                        id SERIAL PRIMARY KEY,
                        ruc VARCHAR(50),
                        nombre VARCHAR(255),
                        categoria VARCHAR(150),
                        contacto VARCHAR(150),
                        whatsapp VARCHAR(50),
                        ubicacion VARCHAR(255)
                    )
                """)
                conn.commit()

                try:
                    c.execute("ALTER TABLE proveedores RENAME COLUMN razon_social TO nombre")
                    conn.commit()
                except Exception:
                    conn.rollback() 
                
                columnas_extra = {
                    "direccion_fiscal": "TEXT",
                    "contacto_2": "VARCHAR(150)",
                    "whatsapp_2": "VARCHAR(50)",
                    "correo": "VARCHAR(150)",
                    "web": "VARCHAR(255)",
                    "catalogo": "VARCHAR(255)",
                    "banco_1": "VARCHAR(100)",
                    "cuenta_1": "VARCHAR(100)",
                    "cci_1": "VARCHAR(100)",
                    "banco_2": "VARCHAR(100)",
                    "cuenta_2": "VARCHAR(100)",
                    "cci_2": "VARCHAR(100)",
                    "cuenta_detraccion": "VARCHAR(100)",
                    "porcentaje_detraccion": "VARCHAR(50)",
                    "descripcion": "TEXT",
                    "categoria_2": "VARCHAR(255)",
                    "categoria_3": "VARCHAR(255)",
                    "categoria_4": "VARCHAR(255)",
                    "categoria_5": "VARCHAR(255)"
                }

                for col, tipo in columnas_extra.items():
                    try:
                        c.execute(f"ALTER TABLE proveedores ADD COLUMN IF NOT EXISTS {col} {tipo}")
                        if "categoria_" in col:
                            c.execute(f"ALTER TABLE proveedores ALTER COLUMN {col} SET DEFAULT 'No seleccionada'")
                        conn.commit()
                    except Exception: 
                        conn.rollback()
                        
                c.execute("CREATE TABLE IF NOT EXISTS categorias (id SERIAL PRIMARY KEY, nombre VARCHAR(255) UNIQUE NOT NULL)")
                conn.commit()
                
                _SCHEMA_PROV_OK = True
            except Exception as e:
                print("Error en auto-curación de DB Proveedores:", e)
            finally:
                liberar_conexion(conn)

    # Lanzamos la verificación sin congelar la ventana principal
    threading.Thread(target=tarea_curacion, daemon=True).start()


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tip_window or not self.text: return
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
        if tw: tw.destroy()


class SistemaProveedores:
    def __init__(self, root):
        self.root = root
        self.usuario_activo = "Desconocido"
        
        # 🚀 VARIABLES DE PAGINACIÓN
        self.pagina_actual = 1
        self.registros_por_pagina = 50

        try:
            self.root.title("Gestión de Proveedores - Black Cube")
            if isinstance(self.root, (ctk.CTkToplevel, tk.Tk, ctk.CTk)):
                self.root.geometry("1200x750")
            else:
                self.root.after(0, lambda: maximizar_ventana(self.root))
        except AttributeError:
            pass 
            
        inicializar_db_proveedores()
        
        header_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        header_frame.pack(fill="x", padx=15, pady=(15, 0))
        ctk.CTkLabel(header_frame, text="📦 GESTIÓN DE PROVEEDORES", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")

        self.tabview = ctk.CTkTabview(self.root, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, padx=15, pady=15)
        
        self.tab_buscar = self.tabview.add(" 🔍 Buscar Proveedores ")
        self.tab_incluir = self.tabview.add(" ➕ Incluir Proveedor ")
        
        self.crear_tab_buscar()
        self.crear_tab_incluir()

    def crear_tab_buscar(self):
        frame_busqueda = ctk.CTkFrame(self.tab_buscar, corner_radius=8, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        frame_busqueda.pack(fill="x", padx=10, pady=10, ipady=5)
        
        lbl_buscar = ctk.CTkLabel(frame_busqueda, text="🔍 Buscar:", font=("Arial", 12, "bold"), text_color="#333333")
        lbl_buscar.pack(side="left", padx=(15, 5), pady=10)
        
        self.ent_buscar = ctk.CTkEntry(frame_busqueda, placeholder_text="Escribe RUC, Nombre, Categoría, Ubicación...", border_color="#cccccc")
        self.ent_buscar.pack(side="left", fill="x", expand=True, padx=5, pady=10)
        
        self.ent_buscar.bind("<KeyRelease>", lambda e: self.buscar_con_retraso())
        self.ent_buscar.bind("<Return>", lambda e: self.cargar_proveedores_tabla(reset_pagina=True))
        
        btn_limpiar_b = ctk.CTkButton(frame_busqueda, text="🔄 Limpiar", width=90, font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_busqueda)
        btn_limpiar_b.pack(side="left", padx=5, pady=10)
        ToolTip(btn_limpiar_b, "Limpia la búsqueda y muestra todos.")
        
        frame_acciones = ctk.CTkFrame(frame_busqueda, fg_color="transparent")
        frame_acciones.pack(side="right", padx=10)

        self.btn_editar = ctk.CTkButton(frame_acciones, text="✏️ Editar", width=100, font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=self.abrir_ventana_editar, state="disabled")
        self.btn_editar.pack(side="left", padx=5)

        self.btn_eliminar_p = ctk.CTkButton(frame_acciones, text="❌ Eliminar", width=100, font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=self.ejecutar_eliminacion_proveedor, state="disabled")
        self.btn_eliminar_p.pack(side="left", padx=5)

        frame_tabla = ctk.CTkFrame(self.tab_buscar, corner_radius=10)
        frame_tabla.pack(fill="both", expand=True, padx=10, pady=5)
        
        columnas = ("num", "id", "ruc", "nombre", "categoria", "contacto", "whatsapp", "ubicacion")
        
        style = tk.ttk.Style()
        style.theme_use("clam")
        bg_blanco, fg_negro, bg_seleccion, border_color = "#ffffff", "#000000", "#1f538d", "#e0e0e0"
        
        style.configure("Treeview", background=bg_blanco, foreground=fg_negro, fieldbackground=bg_blanco, rowheight=28, font=("Arial", 10), bordercolor=border_color, borderwidth=1)
        style.map("Treeview", background=[("selected", bg_seleccion)], foreground=[("selected", bg_blanco)])
        style.configure("Treeview.Heading", background="#f0f0f0", foreground=fg_negro, font=("Arial", 10, "bold"), bordercolor=border_color, borderwidth=1, relief="flat")
        
        self.tabla = tk.ttk.Treeview(frame_tabla, columns=columnas, show="headings")
        
        self.tabla.heading("num", text="N°", anchor="center")
        self.tabla.heading("id", text="ID (Oculto)")
        self.tabla.heading("ruc", text="RUC", command=lambda: self.ordenar_columna("ruc", False))
        self.tabla.heading("nombre", text="Razón Social / Nombre", command=lambda: self.ordenar_columna("nombre", False))
        self.tabla.heading("categoria", text="Categorías (Múltiples)", command=lambda: self.ordenar_columna("categoria", False))
        self.tabla.heading("contacto", text="Contacto", command=lambda: self.ordenar_columna("contacto", False))
        self.tabla.heading("whatsapp", text="WhatsApp", command=lambda: self.ordenar_columna("whatsapp", False))
        self.tabla.heading("ubicacion", text="Ubicación", command=lambda: self.ordenar_columna("ubicacion", False))
        
        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id", width=0, stretch=tk.NO)
        self.tabla.column("ruc", width=110, anchor="center")
        self.tabla.column("nombre", width=250, anchor="w")
        self.tabla.column("categoria", width=220, anchor="w")
        self.tabla.column("contacto", width=130, anchor="w")
        self.tabla.column("whatsapp", width=110, anchor="center")
        self.tabla.column("ubicacion", width=150, anchor="w")
        
        self.tabla.config(displaycolumns=("num", "ruc", "nombre", "categoria", "contacto", "whatsapp", "ubicacion"))
        
        scroll_y = ctk.CTkScrollbar(frame_tabla, orientation="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        
        self.tabla.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scroll_y.pack(side="right", fill="y", padx=(0, 10), pady=10)
        
        self.tabla.bind("<<TreeviewSelect>>", self.on_fila_seleccionada)
        self.tabla.bind("<Double-1>", lambda event: self.abrir_ventana_editar())
        
        frame_paginacion = ctk.CTkFrame(self.tab_buscar, fg_color="transparent")
        frame_paginacion.pack(fill="x", padx=10, pady=(0, 10))
        
        self.btn_ant = ctk.CTkButton(frame_paginacion, text="◀ Anterior", width=100, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=10)
        
        self.lbl_pagina = ctk.CTkLabel(frame_paginacion, text=f"Página {self.pagina_actual}", font=("Arial", 12, "bold"))
        self.lbl_pagina.pack(side="left", expand=True)
        
        self.btn_sig = ctk.CTkButton(frame_paginacion, text="Siguiente ▶", width=100, command=self.pagina_siguiente)
        self.btn_sig.pack(side="right", padx=10)

        self.root.after(100, lambda: self.cargar_proveedores_tabla(reset_pagina=True))

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_proveedores_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_proveedores_tabla()

    def buscar_con_retraso(self):
        if hasattr(self, "_busqueda_job"):
            try:
                self.root.after_cancel(self._busqueda_job)
            except Exception:
                pass
        self._busqueda_job = self.root.after(350, lambda: self.cargar_proveedores_tabla(reset_pagina=True))

    def limpiar_busqueda(self):
        self.ent_buscar.delete(0, tk.END)
        self.cargar_proveedores_tabla(reset_pagina=True)

    # 🚀 FIX 2: CARGA ASÍNCRONA + LAZY LOADING (Cero Congelamientos)
    def cargar_proveedores_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Página {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        if hasattr(self, 'btn_editar'):
            self.btn_editar.configure(state="disabled")
            self.btn_eliminar_p.configure(state="disabled")
            
        texto = self.ent_buscar.get().strip().lower()
        offset = (self.pagina_actual - 1) * self.registros_por_pagina
        
        clave_cache = f"proveedores_evt_{texto}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is not None:
            # ⚡ Si está en el caché, se pinta al instante
            self._pintar_datos_en_tabla(datos, offset)
        else:
            # ☁️ Si NO está en el caché, mostramos "Cargando" y lanzamos el hilo
            self.tabla.insert("", tk.END, values=("", "", "", "Cargando datos desde la nube...", "", "", "", ""))
            
            def tarea_descarga():
                conn = conectar_db(silencioso=True)
                if not conn: 
                    self.root.after(0, lambda: messagebox.showwarning("Modo Lectura", "Sin conexión."))
                    return
                    
                try:
                    cursor = conn.cursor()
                    query_base = "SELECT id, ruc, nombre, categoria, contacto, whatsapp, ubicacion, categoria_2, categoria_3, categoria_4, categoria_5 FROM proveedores"
                    
                    if texto == "":
                        cursor.execute(f"{query_base} ORDER BY nombre ASC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                    else:
                        val = f"%{texto}%"
                        cursor.execute(f"""
                            {query_base} 
                            WHERE nombre ILIKE %s OR ruc ILIKE %s OR categoria ILIKE %s OR categoria_2 ILIKE %s 
                               OR categoria_3 ILIKE %s OR categoria_4 ILIKE %s OR categoria_5 ILIKE %s 
                               OR contacto ILIKE %s OR whatsapp ILIKE %s OR ubicacion ILIKE %s
                            ORDER BY nombre ASC LIMIT %s OFFSET %s
                        """, (val, val, val, val, val, val, val, val, val, val, self.registros_por_pagina, offset))
                    
                    datos_bd = cursor.fetchall()
                    cache_sistema.guardar(clave_cache, datos_bd)
                    self.root.after(0, lambda: self._pintar_datos_en_tabla(datos_bd, offset))
                except Exception as e:
                    print(f"Error DB Proveedores: {e}")
                finally:
                    liberar_conexion(conn)

            threading.Thread(target=tarea_descarga, daemon=True).start()

    def _pintar_datos_en_tabla(self, datos, offset):
        for item in self.tabla.get_children():
            self.tabla.delete(item)

        contador_visual = offset + 1
        for row in datos:
            cat_final = str(row[3])
            if row[7] and row[7] != "No seleccionada": cat_final += f" / {row[7]}"
            if row[8] and row[8] != "No seleccionada": cat_final += f" / {row[8]}"
            if row[9] and row[9] != "No seleccionada": cat_final += f" / {row[9]}"
            if row[10] and row[10] != "No seleccionada": cat_final += f" / {row[10]}"
            
            valores = (contador_visual, row[0], row[1], row[2], cat_final, row[4], row[5], row[6])
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
            self.btn_eliminar_p.configure(state="normal")
        else:
            self.btn_editar.configure(state="disabled")
            self.btn_eliminar_p.configure(state="disabled")

    def ejecutar_eliminacion_proveedor(self):
        seleccion = self.tabla.selection()
        if not seleccion: return
        valores = self.tabla.item(seleccion[0], "values")
        id_prov = valores[1]
        nombre_prov = valores[3]
        
        if messagebox.askyesno("Confirmar Eliminación", f"¿Desea eliminar al proveedor:\n\n'{nombre_prov}' (ID: {id_prov})?"):
            conn = conectar_db()
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM proveedores WHERE id = %s", (id_prov,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Proveedores", f"Eliminó al proveedor '{nombre_prov}' (ID: {id_prov})")
                messagebox.showinfo("Éxito", "Proveedor eliminado correctamente.")
                self.cargar_proveedores_tabla(reset_pagina=True)
            except Exception as e:
                conn.rollback()
                messagebox.showerror("Error", f"No se pudo eliminar: {str(e)}")
            finally:
                liberar_conexion(conn)

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
        except tk.TclError: pass

    def crear_botones_cp(self, parent, row, col, widget, nombre_campo):
        f_btn = ctk.CTkFrame(parent, fg_color="transparent")
        f_btn.grid(row=row, column=col, sticky="w", padx=5, pady=5)
        btn_p = ctk.CTkButton(f_btn, text="📋", width=32, height=32, font=("Arial", 12), fg_color="#e0e0e0", hover_color="#c8c8c8", text_color="black", command=lambda: self.portapapeles_pegar(widget))
        btn_p.pack(side="left", padx=2)
        btn_c = ctk.CTkButton(f_btn, text="↗", width=32, height=32, font=("Arial", 12), fg_color="#e0e0e0", hover_color="#c8c8c8", text_color="black", command=lambda: self.portapapeles_copiar(widget, nombre_campo))
        btn_c.pack(side="left", padx=2)

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
        if direccion == "-": direccion = ""
        
        if not direccion:
            direccion = data.get("direccion_completa", data.get("direccionCompleta", "")).strip()
            if direccion == "-": direccion = ""
        
        distrito = data.get("distrito", "").strip()
        provincia = data.get("provincia", "").strip()
        departamento = data.get("departamento", "").strip()
        
        if distrito == "-": distrito = ""
        if provincia == "-": provincia = ""
        if departamento == "-": departamento = ""
        
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

    def ejecutar_importacion_pdf(self):
        try:
            from pypdf import PdfReader
        except ImportError:
            messagebox.showerror("Librería Faltante", "Para activar el lector de Fichas PDF, ejecute en su consola:\npip install pypdf")
            return

        archivo_pdf = filedialog.askopenfilename(title="Seleccionar Ficha PDF de Proveedor", filetypes=[("Archivos PDF de Fichas", "*.pdf")])
        if not archivo_pdf: return

        try:
            reader = PdfReader(archivo_pdf)
            fields = reader.get_fields()
            if not fields:
                messagebox.showwarning("PDF Inválido", "El archivo PDF no contiene un formulario interactivo."); return

            nombre = fields.get("razon_social", {}).get("/V", "").strip()
            ruc = fields.get("ruc", {}).get("/V", "").strip()
            cat = fields.get("categoria", {}).get("/V", "").strip()
            contacto_p = fields.get("contacto_principal", {}).get("/V", "").strip()
            contacto_a = fields.get("contacto_alternativo", {}).get("/V", "").strip()
            correo_p = fields.get("correo", {}).get("/V", "").strip()
            link_w = fields.get("link_web", {}).get("/V", "").strip()
            zona_dist = fields.get("zona_distrito", {}).get("/V", "").strip()
            enlace_c = fields.get("enlace_catalogo", {}).get("/V", "").strip()
            whats_p = fields.get("whatsapp_principal", {}).get("/V", "").strip()
            whats_a = fields.get("whatsapp_alternativo", {}).get("/V", "").strip()
            b1 = fields.get("banco_1", {}).get("/V", "").strip()
            c1 = fields.get("cuenta_1", {}).get("/V", "").strip()
            cc1 = fields.get("cci_1", {}).get("/V", "").strip() 
            b2 = fields.get("banco_2", {}).get("/V", "").strip()
            c2 = fields.get("cuenta_2", {}).get("/V", "").strip()
            cc2 = fields.get("cci_2", {}).get("/V", "").strip() 
            detraccion = fields.get("cuenta_detraccion", {}).get("/V", "").strip()
            porcentaje_det = fields.get("porcentaje_detraccion", {}).get("/V", "").strip()
            desc_prov = fields.get("descripcion_proveedor", {}).get("/V", "").strip()

            if b1 == "Seleccione Banco": b1 = "Ninguno"
            if b2 == "Seleccione Banco": b2 = "Ninguno"

            self.limpiar_formulario_incluir()
            self.ent_ruc.insert(0, ruc)
            self.ent_nombre.insert(0, nombre)
            
            if cat and cat.lower() != "seleccione una opción":
                conn = conectar_db()
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT nombre FROM categorias WHERE nombre ILIKE %s", (cat,))
                        if not cursor.fetchone():
                            cursor.execute("INSERT INTO categorias (nombre) VALUES (%s)", (cat,))
                            conn.commit()
                            cache_sistema.invalidar()
                        self.var_cat.set(cat)
                    except Exception: pass
                    finally: liberar_conexion(conn)

            self.ent_contacto.insert(0, contacto_p)
            self.ent_whatsapp.insert(0, whats_p)
            self.ent_contacto_2.insert(0, contacto_a)
            self.ent_whatsapp_2.insert(0, whats_a)
            self.ent_correo.insert(0, correo_p)
            self.ent_web.insert(0, link_w)
            self.ent_catalogo.insert(0, zona_dist)
            self.ent_catalogo_link.insert(0, enlace_c)
            
            if b1 in BANCOS_PERU: self.cmb_banco_1.set(b1)
            self.ent_cuenta_1.insert(0, c1)
            self.ent_cci_1.insert(0, cc1)
            
            if b2 in BANCOS_PERU: self.cmb_banco_2.set(b2)
            self.ent_cuenta_2.insert(0, c2)
            self.ent_cci_2.insert(0, cc2)
            
            self.ent_detraccion.insert(0, detraccion)
            self.ent_porcentaje_detraccion.insert(0, porcentaje_det)
            
            self.txt_descripcion.insert("1.0", desc_prov)
            self.lbl_contador.configure(text=f"Caracteres restantes: {max(0, 400 - len(desc_prov))}")

            messagebox.showinfo("Ficha Importada", "¡Datos extraídos del PDF!\nRevisa el formulario y dale a guardar.")
        except Exception as e:
            messagebox.showerror("Error de Lectura", f"No se pudo procesar el PDF:\n\n{str(e)}")

    def crear_tab_incluir(self):
        scroll_frame = ctk.CTkScrollableFrame(self.tab_incluir, fg_color="transparent")
        scroll_frame.pack(fill="both", expand=True, padx=5, pady=5)

        f_pdf = ctk.CTkFrame(scroll_frame, corner_radius=12)
        f_pdf.pack(fill="x", padx=10, pady=(0, 10))
        btn_importar_rapido = ctk.CTkButton(f_pdf, text="📄 Importar Datos de Ficha PDF", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=self.ejecutar_importacion_pdf)
        btn_importar_rapido.pack(fill="x", padx=15, pady=12)

        f1 = ctk.CTkFrame(scroll_frame, corner_radius=12)
        f1.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f1.columnconfigure(1, weight=1)
        f1.columnconfigure(4, weight=1)
        
        ctk.CTkLabel(f1, text="Datos Generales del Proveedor", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))

        ctk.CTkLabel(f1, text="RUC:\n(Presiona Enter para buscar)", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_ruc = ctk.CTkEntry(f1, placeholder_text="11 dígitos y presiona ENTER")
        self.ent_ruc.grid(row=1, column=1, sticky="ew", pady=8)
        
        self.ent_ruc.bind("<Return>", lambda e: self.consultar_ruc_api(self.ent_ruc, self.ent_nombre, self.ent_direccion))

        self.crear_botones_cp(f1, 1, 2, self.ent_ruc, "el RUC")
        
        ctk.CTkLabel(f1, text="Nombre/Razón Social:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_nombre = ctk.CTkEntry(f1)
        self.ent_nombre.grid(row=1, column=4, sticky="ew", pady=8)
        self.crear_botones_cp(f1, 1, 5, self.ent_nombre, "la Razón Social")

        ctk.CTkLabel(f1, text="Dirección Fiscal:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_direccion = ctk.CTkEntry(f1)
        self.ent_direccion.grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)
        self.crear_botones_cp(f1, 2, 3, self.ent_direccion, "la Dirección Fiscal")
        
        ctk.CTkLabel(f1, text="Categoría Principal:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        self.var_cat = tk.StringVar(value="No seleccionada")
        self.lbl_cat = ctk.CTkLabel(f1, textvariable=self.var_cat, font=("Arial", 12, "bold"), text_color="#1F85DE")
        self.lbl_cat.grid(row=3, column=1, sticky="w", pady=8)
        btn_sel_cat = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(self.var_cat))
        btn_sel_cat.grid(row=3, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 2:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        self.var_cat_2 = tk.StringVar(value="No seleccionada")
        self.lbl_cat_2 = ctk.CTkLabel(f1, textvariable=self.var_cat_2, font=("Arial", 12, "bold"), text_color="#1F85DE")
        self.lbl_cat_2.grid(row=4, column=1, sticky="w", pady=8)
        btn_sel_cat_2 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(self.var_cat_2))
        btn_sel_cat_2.grid(row=4, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 3:", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        self.var_cat_3 = tk.StringVar(value="No seleccionada")
        self.lbl_cat_3 = ctk.CTkLabel(f1, textvariable=self.var_cat_3, font=("Arial", 12, "bold"), text_color="#1F85DE")
        self.lbl_cat_3.grid(row=5, column=1, sticky="w", pady=8)
        btn_sel_cat_3 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(self.var_cat_3))
        btn_sel_cat_3.grid(row=5, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 4:", font=("Arial", 12, "bold")).grid(row=6, column=0, sticky="w", padx=(20, 5), pady=8)
        self.var_cat_4 = tk.StringVar(value="No seleccionada")
        self.lbl_cat_4 = ctk.CTkLabel(f1, textvariable=self.var_cat_4, font=("Arial", 12, "bold"), text_color="#1F85DE")
        self.lbl_cat_4.grid(row=6, column=1, sticky="w", pady=8)
        btn_sel_cat_4 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(self.var_cat_4))
        btn_sel_cat_4.grid(row=6, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 5:", font=("Arial", 12, "bold")).grid(row=7, column=0, sticky="w", padx=(20, 5), pady=8)
        self.var_cat_5 = tk.StringVar(value="No seleccionada")
        self.lbl_cat_5 = ctk.CTkLabel(f1, textvariable=self.var_cat_5, font=("Arial", 12, "bold"), text_color="#1F85DE")
        self.lbl_cat_5.grid(row=7, column=1, sticky="w", pady=8)
        btn_sel_cat_5 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(self.var_cat_5))
        btn_sel_cat_5.grid(row=7, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Descripción Proveedor:\n(Max 400 carac.)", font=("Arial", 12, "bold")).grid(row=8, column=0, sticky="nw", padx=(20, 5), pady=12)
        self.txt_descripcion = ctk.CTkTextbox(f1, height=110, font=("Arial", 12), border_width=1)
        self.txt_descripcion.grid(row=8, column=1, columnspan=4, sticky="ew", pady=12)
        self.crear_botones_cp(f1, 8, 5, self.txt_descripcion, "la Descripción")
        
        self.lbl_contador = ctk.CTkLabel(f1, text="Caracteres restantes: 400", font=("Arial", 11), text_color="gray")
        self.lbl_contador.grid(row=9, column=1, sticky="w", padx=2)

        def limitar_caracteres_inc(event):
            texto = self.txt_descripcion.get("1.0", "end-1c")
            if len(texto) > 400:
                self.txt_descripcion.delete("1.0", tk.END)
                self.txt_descripcion.insert("1.0", texto[:400])
                texto = texto[:400]
            self.lbl_contador.configure(text=f"Caracteres restantes: {400 - len(texto)}")

        self.txt_descripcion.bind("<KeyRelease>", limitar_caracteres_inc)

        f2 = ctk.CTkFrame(scroll_frame, corner_radius=12)
        f2.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f2.columnconfigure(1, weight=1)
        f2.columnconfigure(4, weight=1)
        
        ctk.CTkLabel(f2, text="Información de Contacto y Enlaces", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        
        ctk.CTkLabel(f2, text="Contacto Principal:", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_contacto = ctk.CTkEntry(f2)
        self.ent_contacto.grid(row=1, column=1, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 1, 2, self.ent_contacto, "el Contacto Principal")
        
        ctk.CTkLabel(f2, text="WhatsApp Principal:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_whatsapp = ctk.CTkEntry(f2)
        self.ent_whatsapp.grid(row=1, column=4, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 1, 5, self.ent_whatsapp, "el WhatsApp Principal")

        ctk.CTkLabel(f2, text="Contacto Alternativo:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_contacto_2 = ctk.CTkEntry(f2)
        self.ent_contacto_2.grid(row=2, column=1, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 2, 2, self.ent_contacto_2, "el Contacto Alternativo")
        
        ctk.CTkLabel(f2, text="WhatsApp Alternativo:", font=("Arial", 12, "bold")).grid(row=2, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_whatsapp_2 = ctk.CTkEntry(f2)
        self.ent_whatsapp_2.grid(row=2, column=4, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 2, 5, self.ent_whatsapp_2, "el WhatsApp Alternativo")
        
        ctk.CTkLabel(f2, text="Correo Electrónico:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_correo = ctk.CTkEntry(f2)
        self.ent_correo.grid(row=3, column=1, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 3, 2, self.ent_correo, "el Correo")
        
        ctk.CTkLabel(f2, text="Ubicación (Zonas):", font=("Arial", 12, "bold")).grid(row=3, column=3, sticky="w", padx=(30, 5), pady=8)
        self.cmb_ubicacion = ctk.CTkOptionMenu(f2, values=ZONAS_LIMA)
        self.cmb_ubicacion.grid(row=3, column=4, sticky="ew", pady=8)
        self.cmb_ubicacion.set(ZONAS_LIMA[0])
        
        ctk.CTkLabel(f2, text="Link Web:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_web = ctk.CTkEntry(f2)
        self.ent_web.grid(row=4, column=1, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 4, 2, self.ent_web, "el Link Web")
        
        ctk.CTkLabel(f2, text="Zona/Distrito Fijo:", font=("Arial", 12, "bold")).grid(row=4, column=3, sticky="w", padx=(30, 5), pady=8)
        self.ent_catalogo = ctk.CTkEntry(f2)
        self.ent_catalogo.grid(row=4, column=4, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 4, 5, self.ent_catalogo, "la Zona Fija")

        ctk.CTkLabel(f2, text="Enlace Catálogo:", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        self.ent_catalogo_link = ctk.CTkEntry(f2)
        self.ent_catalogo_link.grid(row=5, column=1, sticky="ew", pady=8)
        self.crear_botones_cp(f2, 5, 2, self.ent_catalogo_link, "el Catálogo")

        f3 = ctk.CTkFrame(scroll_frame, corner_radius=12)
        f3.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f3.columnconfigure(1, weight=1)
        f3.columnconfigure(4, weight=1)

        ctk.CTkLabel(f3, text="Información Financiera y Detracciones", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        
        ctk.CTkLabel(f3, text="CUENTA PRINCIPAL", font=("Arial", 12, "bold"), text_color="gray").grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(5, 10))
        ctk.CTkLabel(f3, text="Banco:", font=("Arial", 11, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=5)
        self.cmb_banco_1 = ctk.CTkOptionMenu(f3, values=["Ninguno"] + BANCOS_PERU)
        self.cmb_banco_1.grid(row=2, column=1, sticky="w", pady=5)
        self.cmb_banco_1.set("BCP")
        
        ctk.CTkLabel(f3, text="N° Cuenta:", font=("Arial", 11, "bold")).grid(row=2, column=2, sticky="w", padx=(15, 5), pady=5)
        self.ent_cuenta_1 = ctk.CTkEntry(f3)
        self.ent_cuenta_1.grid(row=2, column=3, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 2, 4, self.ent_cuenta_1, "la Cuenta Principal")
        
        ctk.CTkLabel(f3, text="CCI:", font=("Arial", 11, "bold")).grid(row=2, column=5, sticky="w", padx=(15, 5), pady=5)
        self.ent_cci_1 = ctk.CTkEntry(f3)
        self.ent_cci_1.grid(row=2, column=6, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 2, 7, self.ent_cci_1, "el CCI Principal")

        ctk.CTkLabel(f3, text="CUENTA SECUNDARIA (OPCIONAL)", font=("Arial", 12, "bold"), text_color="gray").grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 10))
        ctk.CTkLabel(f3, text="Banco:", font=("Arial", 11, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=5)
        self.cmb_banco_2 = ctk.CTkOptionMenu(f3, values=["Ninguno"] + BANCOS_PERU)
        self.cmb_banco_2.grid(row=4, column=1, sticky="w", pady=5)
        self.cmb_banco_2.set("Ninguno")
        
        ctk.CTkLabel(f3, text="N° Cuenta:", font=("Arial", 11, "bold")).grid(row=4, column=2, sticky="w", padx=(15, 5), pady=5)
        self.ent_cuenta_2 = ctk.CTkEntry(f3)
        self.ent_cuenta_2.grid(row=4, column=3, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 4, 4, self.ent_cuenta_2, "la Cuenta Secundaria")
        
        ctk.CTkLabel(f3, text="CCI:", font=("Arial", 11, "bold")).grid(row=4, column=5, sticky="w", padx=(15, 5), pady=5)
        self.ent_cci_2 = ctk.CTkEntry(f3)
        self.ent_cci_2.grid(row=4, column=6, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 4, 7, self.ent_cci_2, "el CCI Secundario")

        ctk.CTkLabel(f3, text="SISTEMA DE DETRACCIONES", font=("Arial", 12, "bold"), text_color="#1F85DE").grid(row=5, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 10))
        ctk.CTkLabel(f3, text="Cuenta BN:", font=("Arial", 11, "bold")).grid(row=6, column=0, sticky="w", padx=(20, 5), pady=5)
        self.ent_detraccion = ctk.CTkEntry(f3)
        self.ent_detraccion.grid(row=6, column=1, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 6, 2, self.ent_detraccion, "la Detracción")
        
        ctk.CTkLabel(f3, text="Tasa Detracción (%):", font=("Arial", 11, "bold")).grid(row=6, column=3, sticky="w", padx=(30, 5), pady=5)
        self.ent_porcentaje_detraccion = ctk.CTkEntry(f3)
        self.ent_porcentaje_detraccion.grid(row=6, column=4, sticky="ew", pady=5)
        self.crear_botones_cp(f3, 6, 5, self.ent_porcentaje_detraccion, "el % de Detracción")

        btn_guardar = ctk.CTkButton(scroll_frame, text="💾 Guardar Nuevo Proveedor", font=("Arial", 14, "bold"), width=250, height=40, command=self.guardar_proveedor)
        btn_guardar.pack(pady=25)

    def guardar_proveedor(self):
        ruc = self.ent_ruc.get().strip()
        nombre = self.ent_nombre.get().strip()
        direccion_fiscal = self.ent_direccion.get().strip()
        categoria = self.var_cat.get()
        categoria_2 = self.var_cat_2.get()
        categoria_3 = self.var_cat_3.get()
        categoria_4 = self.var_cat_4.get()
        categoria_5 = self.var_cat_5.get()
        contacto = self.ent_contacto.get().strip()
        whatsapp = self.ent_whatsapp.get().strip()
        contacto_2 = self.ent_contacto_2.get().strip()
        whatsapp_2 = self.ent_whatsapp_2.get().strip()
        correo = self.ent_correo.get().strip()
        ubicacion = self.cmb_ubicacion.get()
        web = self.ent_web.get().strip()
        catalogo = self.ent_catalogo.get().strip()
        banco_1 = self.cmb_banco_1.get()
        cuenta_1 = self.ent_cuenta_1.get().strip()
        cci_1 = self.ent_cci_1.get().strip()
        banco_2 = self.cmb_banco_2.get()
        cuenta_2 = self.ent_cuenta_2.get().strip()
        cci_2 = self.ent_cci_2.get().strip()
        cuenta_det = self.ent_detraccion.get().strip()
        porcentaje_det = self.ent_porcentaje_detraccion.get().strip()
        desc = self.txt_descripcion.get("1.0", "end-1c").strip()

        if len(ruc) != 11 or not ruc.isdigit():
            messagebox.showwarning("Campos Incompletos", "El RUC de la empresa debe tener exactamente 11 dígitos numéricos.")
            return
        if not nombre:
            messagebox.showwarning("Campos Incompletos", "Debe ingresar la Razón Social del proveedor.")
            return

        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.")
            return
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO proveedores (ruc, nombre, direccion_fiscal, categoria, contacto, whatsapp, contacto_2, whatsapp_2, correo, ubicacion, web, catalogo, banco_1, cuenta_1, cci_1, banco_2, cuenta_2, cci_2, cuenta_detraccion, porcentaje_detraccion, descripcion, categoria_2, categoria_3, categoria_4, categoria_5)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (ruc, nombre, direccion_fiscal, categoria, contacto, whatsapp, contacto_2, whatsapp_2, correo, ubicacion, web, catalogo, banco_1, cuenta_1, cci_1, banco_2, cuenta_2, cci_2, cuenta_det, porcentaje_det, desc, categoria_2, categoria_3, categoria_4, categoria_5))
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Proveedores", f"Registró al proveedor '{nombre}'")
            messagebox.showinfo("Éxito", f"El proveedor '{nombre}' se registró correctamente.")
            self.limpiar_formulario_incluir()
            self.cargar_proveedores_tabla(reset_pagina=True)
        except psycopg2.IntegrityError:
            conn.rollback()
            messagebox.showerror("Error de Duplicidad", "Este número de RUC ya se encuentra registrado.")
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error SQL", f"No se pudo guardar el proveedor:\n{e}")
        finally:
            liberar_conexion(conn)

    def limpiar_formulario_incluir(self):
        self.ent_ruc.delete(0, tk.END)
        self.ent_nombre.delete(0, tk.END)
        self.ent_direccion.delete(0, tk.END)
        self.var_cat.set("No seleccionada")
        self.var_cat_2.set("No seleccionada")
        self.var_cat_3.set("No seleccionada")
        self.var_cat_4.set("No seleccionada")
        self.var_cat_5.set("No seleccionada")
        self.ent_contacto.delete(0, tk.END)
        self.ent_whatsapp.delete(0, tk.END)
        self.ent_contacto_2.delete(0, tk.END)
        self.ent_whatsapp_2.delete(0, tk.END)
        self.ent_correo.delete(0, tk.END)
        self.cmb_ubicacion.set(ZONAS_LIMA[0])
        self.ent_web.delete(0, tk.END)
        self.ent_catalogo.delete(0, tk.END)
        self.ent_catalogo_link.delete(0, tk.END)
        self.cmb_banco_1.set("BCP")
        self.ent_cuenta_1.delete(0, tk.END)
        self.ent_cci_1.delete(0, tk.END)
        self.cmb_banco_2.set("Ninguno")
        self.ent_cuenta_2.delete(0, tk.END)
        self.ent_cci_2.delete(0, tk.END)
        self.ent_detraccion.delete(0, tk.END)
        self.ent_porcentaje_detraccion.delete(0, tk.END)
        self.txt_descripcion.delete("1.0", tk.END)
        self.lbl_contador.configure(text="Caracteres restantes: 400")

    def abrir_ventana_editar(self):
        seleccion = self.tabla.selection()
        if not seleccion: return
        
        valores = self.tabla.item(seleccion[0], "values")
        id_prov = valores[1] 
        
        conn = conectar_db()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, ruc, nombre, categoria, contacto, whatsapp, contacto_2, whatsapp_2, 
                       correo, ubicacion, web, catalogo, banco_1, cuenta_1, cci_1, 
                       banco_2, cuenta_2, cci_2, cuenta_detraccion, porcentaje_detraccion, descripcion,
                       categoria_2, categoria_3, categoria_4, categoria_5, direccion_fiscal
                FROM proveedores WHERE id = %s
            ''', (id_prov,))
            p = cursor.fetchone()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar el proveedor:\n{e}")
            return
        finally:
            liberar_conexion(conn)
            
        if not p: return
            
        v_edit = ctk.CTkToplevel(self.root)
        v_edit.title(f"Modificar Proveedor Registrado - ID Interno: {id_prov}")
        v_edit.geometry("1100x750")
        
        v_edit.after(0, lambda: maximizar_ventana(v_edit))
        v_edit.grab_set()
        
        scroll_frame_e = ctk.CTkScrollableFrame(v_edit, fg_color="transparent")
        scroll_frame_e.pack(fill="both", expand=True, padx=5, pady=5)

        f1 = ctk.CTkFrame(scroll_frame_e, corner_radius=12)
        f1.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f1.columnconfigure(1, weight=1)
        f1.columnconfigure(4, weight=1)

        ctk.CTkLabel(f1, text="Datos Principales", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        
        ctk.CTkLabel(f1, text="RUC:\n(Presiona Enter para buscar)", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_ruc = ctk.CTkEntry(f1)
        ent_e_ruc.grid(row=1, column=1, sticky="ew", pady=8)
        ent_e_ruc.insert(0, str(p[1]) if p[1] else "")
        
        self.crear_botones_cp(f1, 1, 2, ent_e_ruc, "el RUC")
        
        ctk.CTkLabel(f1, text="Nombre/Razón Social:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_nombre = ctk.CTkEntry(f1)
        ent_e_nombre.grid(row=1, column=4, sticky="ew", pady=8)
        ent_e_nombre.insert(0, str(p[2]) if p[2] else "")
        self.crear_botones_cp(f1, 1, 5, ent_e_nombre, "la Razón Social")

        ctk.CTkLabel(f1, text="Dirección Fiscal:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_direccion = ctk.CTkEntry(f1)
        ent_e_direccion.grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)
        ent_e_direccion.insert(0, str(p[25]) if len(p)>25 and p[25] else "")
        self.crear_botones_cp(f1, 2, 3, ent_e_direccion, "la Dirección Fiscal")

        ent_e_ruc.bind("<Return>", lambda e: self.consultar_ruc_api(ent_e_ruc, ent_e_nombre, ent_e_direccion))
        
        ctk.CTkLabel(f1, text="Categoría Principal:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        var_e_cat = tk.StringVar(value=str(p[3]) if p[3] else "No seleccionada")
        lbl_e_cat = ctk.CTkLabel(f1, textvariable=var_e_cat, font=("Arial", 12, "bold"), text_color="#1F85DE")
        lbl_e_cat.grid(row=3, column=1, sticky="w", pady=8)
        btn_sel_cat = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(var_e_cat))
        btn_sel_cat.grid(row=3, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 2:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        var_e_cat_2 = tk.StringVar(value=str(p[21]) if len(p)>21 and p[21] else "No seleccionada")
        lbl_e_cat_2 = ctk.CTkLabel(f1, textvariable=var_e_cat_2, font=("Arial", 12, "bold"), text_color="#1F85DE")
        lbl_e_cat_2.grid(row=4, column=1, sticky="w", pady=8)
        btn_sel_cat_2 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(var_e_cat_2))
        btn_sel_cat_2.grid(row=4, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 3:", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        var_e_cat_3 = tk.StringVar(value=str(p[22]) if len(p)>22 and p[22] else "No seleccionada")
        lbl_e_cat_3 = ctk.CTkLabel(f1, textvariable=var_e_cat_3, font=("Arial", 12, "bold"), text_color="#1F85DE")
        lbl_e_cat_3.grid(row=5, column=1, sticky="w", pady=8)
        btn_sel_cat_3 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(var_e_cat_3))
        btn_sel_cat_3.grid(row=5, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 4:", font=("Arial", 12, "bold")).grid(row=6, column=0, sticky="w", padx=(20, 5), pady=8)
        var_e_cat_4 = tk.StringVar(value=str(p[23]) if len(p)>23 and p[23] else "No seleccionada")
        lbl_e_cat_4 = ctk.CTkLabel(f1, textvariable=var_e_cat_4, font=("Arial", 12, "bold"), text_color="#1F85DE")
        lbl_e_cat_4.grid(row=6, column=1, sticky="w", pady=8)
        btn_sel_cat_4 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(var_e_cat_4))
        btn_sel_cat_4.grid(row=6, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Categoría Adicional 5:", font=("Arial", 12, "bold")).grid(row=7, column=0, sticky="w", padx=(20, 5), pady=8)
        var_e_cat_5 = tk.StringVar(value=str(p[24]) if len(p)>24 and p[24] else "No seleccionada")
        lbl_e_cat_5 = ctk.CTkLabel(f1, textvariable=var_e_cat_5, font=("Arial", 12, "bold"), text_color="#1F85DE")
        lbl_e_cat_5.grid(row=7, column=1, sticky="w", pady=8)
        btn_sel_cat_5 = ctk.CTkButton(f1, text="Gestionar", width=120, command=lambda: self.ventana_emergente_categorias(var_e_cat_5))
        btn_sel_cat_5.grid(row=7, column=3, sticky="w", padx=15, pady=8)

        ctk.CTkLabel(f1, text="Descripción Proveedor:\n(Max 400 carac.)", font=("Arial", 12, "bold")).grid(row=8, column=0, sticky="nw", padx=(20, 5), pady=12)
        txt_e_descripcion = ctk.CTkTextbox(f1, height=110, font=("Arial", 12), border_width=1)
        txt_e_descripcion.grid(row=8, column=1, columnspan=4, sticky="ew", pady=12)
        txt_e_descripcion.insert("1.0", str(p[20]) if p[20] else "")
        self.crear_botones_cp(f1, 8, 5, txt_e_descripcion, "la Descripción")
        
        lbl_e_contador = ctk.CTkLabel(f1, text=f"Caracteres restantes: {400 - len(txt_e_descripcion.get('1.0', 'end-1c'))}", font=("Arial", 11), text_color="gray")
        lbl_e_contador.grid(row=9, column=1, sticky="w", padx=2)

        def limitar_caracteres_edit(event):
            texto = txt_e_descripcion.get("1.0", "end-1c")
            if len(texto) > 400:
                txt_e_descripcion.delete("1.0", tk.END)
                txt_e_descripcion.insert("1.0", texto[:400])
                texto = texto[:400]
            lbl_e_contador.configure(text=f"Caracteres restantes: {400 - len(texto)}")

        txt_e_descripcion.bind("<KeyRelease>", limitar_caracteres_edit)

        f2 = ctk.CTkFrame(scroll_frame_e, corner_radius=12)
        f2.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f2.columnconfigure(1, weight=1)
        f2.columnconfigure(4, weight=1)
        
        ctk.CTkLabel(f2, text="Información de Contacto y Enlaces", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        
        ctk.CTkLabel(f2, text="Contacto Principal:", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_contacto = ctk.CTkEntry(f2)
        ent_e_contacto.grid(row=1, column=1, sticky="ew", pady=8)
        ent_e_contacto.insert(0, str(p[4]) if p[4] else "")
        self.crear_botones_cp(f2, 1, 2, ent_e_contacto, "el Contacto Principal")
        
        ctk.CTkLabel(f2, text="WhatsApp Principal:", font=("Arial", 12, "bold")).grid(row=1, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_whatsapp = ctk.CTkEntry(f2)
        ent_e_whatsapp.grid(row=1, column=4, sticky="ew", pady=8)
        ent_e_whatsapp.insert(0, str(p[5]) if p[5] else "")
        self.crear_botones_cp(f2, 1, 5, ent_e_whatsapp, "el WhatsApp Principal")

        ctk.CTkLabel(f2, text="Contacto Alternativo:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_contacto_2 = ctk.CTkEntry(f2)
        ent_e_contacto_2.grid(row=2, column=1, sticky="ew", pady=8)
        ent_e_contacto_2.insert(0, str(p[6]) if p[6] else "")
        self.crear_botones_cp(f2, 2, 2, ent_e_contacto_2, "el Contacto Alternativo")
        
        ctk.CTkLabel(f2, text="WhatsApp Alternativo:", font=("Arial", 12, "bold")).grid(row=2, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_whatsapp_2 = ctk.CTkEntry(f2)
        ent_e_whatsapp_2.grid(row=2, column=4, sticky="ew", pady=8)
        ent_e_whatsapp_2.insert(0, str(p[7]) if p[7] else "")
        self.crear_botones_cp(f2, 2, 5, ent_e_whatsapp_2, "el WhatsApp Alternativo")
        
        ctk.CTkLabel(f2, text="Correo Electrónico:", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_correo = ctk.CTkEntry(f2)
        ent_e_correo.grid(row=3, column=1, sticky="ew", pady=8)
        ent_e_correo.insert(0, str(p[8]) if p[8] else "")
        self.crear_botones_cp(f2, 3, 2, ent_e_correo, "el Correo")
        
        ctk.CTkLabel(f2, text="Ubicación (Zonas Lima):", font=("Arial", 12, "bold")).grid(row=3, column=3, sticky="w", padx=(30, 5), pady=8)
        cmb_e_ubicacion = ctk.CTkOptionMenu(f2, values=ZONAS_LIMA)
        cmb_e_ubicacion.grid(row=3, column=4, sticky="ew", pady=8)
        if p[9] in ZONAS_LIMA: cmb_e_ubicacion.set(p[9])
        
        ctk.CTkLabel(f2, text="Link Web:", font=("Arial", 12, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_web = ctk.CTkEntry(f2)
        ent_e_web.grid(row=4, column=1, sticky="ew", pady=8)
        ent_e_web.insert(0, str(p[10]) if p[10] else "")
        self.crear_botones_cp(f2, 4, 2, ent_e_web, "el Link Web")
        
        ctk.CTkLabel(f2, text="Zona:", font=("Arial", 12, "bold")).grid(row=4, column=3, sticky="w", padx=(30, 5), pady=8)
        ent_e_catalogo = ctk.CTkEntry(f2)
        ent_e_catalogo.grid(row=4, column=4, sticky="ew", pady=8)
        ent_e_catalogo.insert(0, str(p[11]) if p[11] else "")
        self.crear_botones_cp(f2, 4, 5, ent_e_catalogo, "la Zona")

        ctk.CTkLabel(f2, text="Enlace Catálogo:", font=("Arial", 12, "bold")).grid(row=5, column=0, sticky="w", padx=(20, 5), pady=8)
        ent_e_catalogo_link = ctk.CTkEntry(f2)
        ent_e_catalogo_link.grid(row=5, column=1, sticky="ew", pady=8)
        ent_e_catalogo_link.insert(0, str(p[11]) if p[11] else "")
        self.crear_botones_cp(f2, 5, 2, ent_e_catalogo_link, "el Catálogo")

        f3 = ctk.CTkFrame(scroll_frame_e, corner_radius=12)
        f3.pack(fill="x", padx=10, pady=10, ipady=15)
        
        f3.columnconfigure(1, weight=1)
        f3.columnconfigure(4, weight=1)

        ctk.CTkLabel(f3, text="Información Financiera y Detracciones", font=("Arial", 15, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=6, sticky="w", padx=20, pady=(15, 15))
        
        ctk.CTkLabel(f3, text="CUENTA PRINCIPAL", font=("Arial", 12, "bold"), text_color="gray").grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=(5, 10))
        ctk.CTkLabel(f3, text="Banco:", font=("Arial", 11, "bold")).grid(row=2, column=0, sticky="w", padx=(20, 5), pady=5)
        cmb_e_banco_1 = ctk.CTkOptionMenu(f3, values=["Ninguno"] + BANCOS_PERU)
        cmb_e_banco_1.grid(row=2, column=1, sticky="w", pady=5)
        if p[12] in (["Ninguno"] + BANCOS_PERU): cmb_e_banco_1.set(p[12])
        
        ctk.CTkLabel(f3, text="N° Cuenta:", font=("Arial", 11, "bold")).grid(row=2, column=2, sticky="w", padx=(15, 5), pady=5)
        ent_e_cuenta_1 = ctk.CTkEntry(f3)
        ent_e_cuenta_1.grid(row=2, column=3, sticky="ew", pady=5)
        ent_e_cuenta_1.insert(0, str(p[13]) if p[13] else "")
        self.crear_botones_cp(f3, 2, 4, ent_e_cuenta_1, "la Cuenta Principal")
        
        ctk.CTkLabel(f3, text="CCI:", font=("Arial", 11, "bold")).grid(row=2, column=5, sticky="w", padx=(15, 5), pady=5)
        ent_e_cci_1 = ctk.CTkEntry(f3)
        ent_e_cci_1.grid(row=2, column=6, sticky="ew", pady=5)
        ent_e_cci_1.insert(0, str(p[14]) if p[14] else "")
        self.crear_botones_cp(f3, 2, 7, ent_e_cci_1, "el CCI Principal")

        ctk.CTkLabel(f3, text="CUENTA SECUNDARIA (OPCIONAL)", font=("Arial", 12, "bold"), text_color="gray").grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 10))
        ctk.CTkLabel(f3, text="Banco:", font=("Arial", 11, "bold")).grid(row=4, column=0, sticky="w", padx=(20, 5), pady=5)
        cmb_e_banco_2 = ctk.CTkOptionMenu(f3, values=["Ninguno"] + BANCOS_PERU)
        cmb_e_banco_2.grid(row=4, column=1, sticky="w", pady=5)
        if p[15] in (["Ninguno"] + BANCOS_PERU): cmb_e_banco_2.set(p[15])
        
        ctk.CTkLabel(f3, text="N° Cuenta:", font=("Arial", 11, "bold")).grid(row=4, column=2, sticky="w", padx=(15, 5), pady=5)
        ent_e_cuenta_2 = ctk.CTkEntry(f3)
        ent_e_cuenta_2.grid(row=4, column=3, sticky="ew", pady=5)
        ent_e_cuenta_2.insert(0, str(p[16]) if p[16] else "")
        self.crear_botones_cp(f3, 4, 4, ent_e_cuenta_2, "la Cuenta Secundaria")
        
        ctk.CTkLabel(f3, text="CCI:", font=("Arial", 11, "bold")).grid(row=4, column=5, sticky="w", padx=(15, 5), pady=5)
        ent_e_cci_2 = ctk.CTkEntry(f3)
        ent_e_cci_2.grid(row=4, column=6, sticky="ew", pady=5)
        ent_e_cci_2.insert(0, str(p[17]) if p[17] else "")
        self.crear_botones_cp(f3, 4, 7, ent_e_cci_2, "el CCI Secundario")

        ctk.CTkLabel(f3, text="SISTEMA DE DETRACCIONES", font=("Arial", 12, "bold"), text_color="#1F85DE").grid(row=5, column=0, columnspan=2, sticky="w", padx=20, pady=(20, 10))
        ctk.CTkLabel(f3, text="Cuenta BN:", font=("Arial", 11, "bold")).grid(row=6, column=0, sticky="w", padx=(20, 5), pady=5)
        ent_e_detraccion = ctk.CTkEntry(f3)
        ent_e_detraccion.grid(row=6, column=1, sticky="ew", pady=5)
        ent_e_detraccion.insert(0, str(p[18]) if p[18] else "")
        self.crear_botones_cp(f3, 6, 2, ent_e_detraccion, "la Detracción")
        
        ctk.CTkLabel(f3, text="Tasa Detracción (%):", font=("Arial", 11, "bold")).grid(row=6, column=3, sticky="w", padx=(30, 5), pady=5)
        ent_e_porcentaje_detraccion = ctk.CTkEntry(f3)
        ent_e_porcentaje_detraccion.grid(row=6, column=4, sticky="ew", pady=5)
        ent_e_porcentaje_detraccion.insert(0, str(p[19]) if p[19] else "")
        self.crear_botones_cp(f3, 6, 5, ent_e_porcentaje_detraccion, "el % de Detracción")

        def ejecutar_update():
            if len(ent_e_ruc.get().strip()) != 11 or not ent_e_ruc.get().strip().isdigit():
                messagebox.showwarning("Error", "El RUC debe tener 11 dígitos numéricos.")
                return
            if not ent_e_nombre.get().strip():
                messagebox.showwarning("Error", "Falta Razón Social.")
                return
            
            conn_u = conectar_db()
            if not conn_u: return
            try:
                cursor_u = conn_u.cursor()
                cursor_u.execute('''
                    UPDATE proveedores 
                    SET ruc=%s, nombre=%s, direccion_fiscal=%s, categoria=%s, contacto=%s, whatsapp=%s, contacto_2=%s, whatsapp_2=%s, correo=%s, ubicacion=%s, web=%s, catalogo=%s, 
                        banco_1=%s, cuenta_1=%s, cci_1=%s, banco_2=%s, cuenta_2=%s, cci_2=%s, cuenta_detraccion=%s, porcentaje_detraccion=%s, descripcion=%s,
                        categoria_2=%s, categoria_3=%s, categoria_4=%s, categoria_5=%s
                    WHERE id=%s
                ''', (ent_e_ruc.get().strip(), ent_e_nombre.get().strip(), ent_e_direccion.get().strip(), var_e_cat.get(), ent_e_contacto.get().strip(),
                      ent_e_whatsapp.get().strip(), ent_e_contacto_2.get().strip(), ent_e_whatsapp_2.get().strip(), ent_e_correo.get().strip(), 
                      cmb_e_ubicacion.get(), ent_e_web.get().strip(), ent_e_catalogo.get().strip(), cmb_e_banco_1.get(), 
                      ent_e_cuenta_1.get().strip(), ent_e_cci_1.get().strip(), cmb_e_banco_2.get(), ent_e_cuenta_2.get().strip(), 
                      ent_e_cci_2.get().strip(), ent_e_detraccion.get().strip(), ent_e_porcentaje_detraccion.get().strip(), 
                      txt_e_descripcion.get("1.0", "end-1c").strip(), var_e_cat_2.get(), var_e_cat_3.get(), var_e_cat_4.get(), var_e_cat_5.get(), id_prov))
                conn_u.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Proveedores", f"Modificó los datos del proveedor ID {id_prov} ({ent_e_nombre.get().strip()})")
                messagebox.showinfo("Éxito", "Cambios guardados.")
                v_edit.destroy()
                self.cargar_proveedores_tabla(reset_pagina=True)
            except psycopg2.IntegrityError:
                conn_u.rollback()
                messagebox.showerror("Error", "Este RUC ya pertenece a otra empresa.")
            except Exception as e:
                conn_u.rollback()
                messagebox.showerror("Error SQL", f"No se pudo guardar la edición:\n{e}")
            finally:
                liberar_conexion(conn_u)

        btn_actualizar = ctk.CTkButton(scroll_frame_e, text="💾 Guardar Cambios", font=("Arial", 14, "bold"), fg_color="#1f538d", hover_color="#163b65", width=250, height=40, command=ejecutar_update)
        btn_actualizar.pack(pady=20)

    def ventana_emergente_categorias(self, variable_destino=None):
        v_cat = ctk.CTkToplevel(self.root)
        v_cat.title("Categorías")
        v_cat.geometry("460x520")
        v_cat.grab_set() 
        v_cat.resizable(False, False)
        
        ctk.CTkLabel(v_cat, text="Gestione las categorías disponibles:", font=("Arial", 12, "bold")).pack(pady=10)
        
        frame_lista = ctk.CTkFrame(v_cat)
        frame_lista.pack(fill="both", expand=True, padx=20, pady=5)
        
        lista_box = tk.Listbox(frame_lista, font=("Arial", 10), bg="#ffffff", fg="black", selectbackground="#1f538d", borderwidth=1)
        scroll_list = tk.ttk.Scrollbar(frame_lista, orient="vertical", command=lista_box.yview)
        lista_box.configure(yscrollcommand=scroll_list.set)
        lista_box.pack(side="left", fill="both", expand=True, padx=(10,0), pady=10)
        scroll_list.pack(side="right", fill="y", pady=10, padx=(0,10))
        
        def cargar_lista():
            lista_box.delete(0, tk.END)
            conn = conectar_db(silencioso=True)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT nombre FROM categorias ORDER BY nombre ASC")
                for row in cursor.fetchall():
                    lista_box.insert(tk.END, str(row[0]).strip())
            except Exception: pass
            finally: liberar_conexion(conn)
                
        cargar_lista()
        
        frame_ctrl = ctk.CTkFrame(v_cat, fg_color="transparent")
        frame_ctrl.pack(fill="x", padx=20, pady=5)
        
        ent_nueva = ctk.CTkEntry(frame_ctrl, width=180, placeholder_text="Nueva categoría...")
        ent_nueva.pack(side="left", padx=5)
        
        def agregar():
            nueva = ent_nueva.get().strip()
            if nueva:
                conn = conectar_db()
                if not conn: return
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM categorias WHERE nombre ILIKE %s", (nueva,))
                    if cursor.fetchone():
                        messagebox.showerror("Error", "La categoría ya existe.")
                        return
                    cursor.execute("INSERT INTO categorias (nombre) VALUES (%s)", (nueva,))
                    conn.commit()
                    cache_sistema.invalidar()
                    registrar_auditoria(self.usuario_activo, "Proveedores", f"Creó nueva categoría '{nueva}'")
                    ent_nueva.delete(0, tk.END)
                    cargar_lista()
                except psycopg2.IntegrityError:
                    conn.rollback()
                    messagebox.showerror("Error", "Ya existe.")
                except Exception:
                    conn.rollback()
                finally:
                    liberar_conexion(conn)

        def eliminar():
            if not lista_box.curselection(): return
            categoria_a_borrar = lista_box.get(lista_box.curselection())
            
            if messagebox.askyesno("Confirmar", f"¿Eliminar la categoría '{categoria_a_borrar}'?"):
                conn = conectar_db()
                if not conn: return
                try:
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM categorias WHERE nombre = %s", (categoria_a_borrar,))
                    cursor.execute("UPDATE proveedores SET categoria = 'No seleccionada' WHERE categoria = %s", (categoria_a_borrar,))
                    cursor.execute("UPDATE proveedores SET categoria_2 = 'No seleccionada' WHERE categoria_2 = %s", (categoria_a_borrar,))
                    cursor.execute("UPDATE proveedores SET categoria_3 = 'No seleccionada' WHERE categoria_3 = %s", (categoria_a_borrar,))
                    cursor.execute("UPDATE proveedores SET categoria_4 = 'No seleccionada' WHERE categoria_4 = %s", (categoria_a_borrar,))
                    cursor.execute("UPDATE proveedores SET categoria_5 = 'No seleccionada' WHERE categoria_5 = %s", (categoria_a_borrar,))
                    conn.commit()
                    cache_sistema.invalidar()
                    registrar_auditoria(self.usuario_activo, "Proveedores", f"Eliminó categoría '{categoria_a_borrar}'")
                    
                    if variable_destino and variable_destino.get() == categoria_a_borrar:
                        variable_destino.set("No seleccionada")
                    elif not variable_destino and self.var_cat.get() == categoria_a_borrar:
                        self.var_cat.set("No seleccionada")
                        
                    cargar_lista()
                except Exception:
                    conn.rollback()
                finally:
                    liberar_conexion(conn)

        def seleccionar():
            if lista_box.curselection():
                item = lista_box.get(lista_box.curselection())
                if variable_destino: variable_destino.set(item)
                else: self.var_cat.set(item)
                v_cat.destroy()

        def limpiar_seleccion():
            if variable_destino: variable_destino.set("No seleccionada")
            else: self.var_cat.set("No seleccionada")
            v_cat.destroy()

        btn_add = ctk.CTkButton(frame_ctrl, text="[ Agregar ]", width=80, command=agregar)
        btn_add.pack(side="left", padx=2)
        
        btn_eli = ctk.CTkButton(frame_ctrl, text="[ Borrar ]", width=80, fg_color="#D32F2F", hover_color="#B71C1C", command=eliminar)
        btn_eli.pack(side="left", padx=2)
        
        btn_sel = ctk.CTkButton(v_cat, text="[ OK ] Seleccionar Categoría", width=200, command=seleccionar)
        btn_sel.pack(pady=(15, 5))
        
        btn_clear = ctk.CTkButton(v_cat, text="[ X ] Quitar / Dejar en Blanco", width=200, fg_color="#7f8c8d", hover_color="#606b6b", command=limpiar_seleccion)
        btn_clear.pack(pady=(0, 15))


if __name__ == "__main__":
    root = ctk.CTk()
    app = SistemaProveedores(root)
    root.mainloop()