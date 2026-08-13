# -*- coding: utf-8 -*-

"""
=========================================================
CRONOGRAMA_TAREAS.PY (ENTERPRISE EDITION)
=========================================================
- FIX: Restauración de funciones de agendado manual perdidas (soluciona pantalla en blanco).
- FIX: Inyección del "Día del Evento Principal" directamente en el calendario.
- FIX: Cierre de conexiones seguro en consultas del Calendario.
- FIX: Auto-curación síncrona para evitar Race Conditions y Caché Fantasma.
- FIX: Blindaje contra 'NoneType' al leer eventos vacíos.
- Paginación Lazy Loading (50 en 50) para la lista de tareas.
- Caché Inteligente para el filtro de Eventos.
- Uso estricto del Pool de conexiones (liberar_conexion).
"""

import os
import sys
import tempfile
import subprocess
import webbrowser
import urllib.parse
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import customtkinter as ctk
import calendar
import ctypes
import threading
import json
from datetime import datetime, timedelta

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = "config_local.json"

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# Variable global declarada al más alto nivel
_SCHEMA_CRON_OK = False


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

def cargar_configuracion_regional():
    config = {"ruta_drive": ""}
    try:
        if os.path.exists(RUTA_CONFIG):
            with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config

CONFIG_REGIONAL = cargar_configuracion_regional()

def obtener_ruta_base_drive():
    ruta = str(CONFIG_REGIONAL.get("ruta_drive", "")).strip()
    if ruta:
        return os.path.expanduser(ruta)
    return ""


# =========================================================
# CLASE: SELECCIONADOR DE FECHAS (MINI CALENDARIO)
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
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#1f538d")
        self.header_frame.pack(fill="x")
        btn_prev = ctk.CTkButton(self.header_frame, text="<", width=30, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.prev_month)
        btn_prev.pack(side="left", padx=10, pady=10)
        self.lbl_month_year = ctk.CTkLabel(self.header_frame, text="", font=("Arial", 16, "bold"), text_color="white", width=150)
        self.lbl_month_year.pack(side="left", padx=5)
        btn_next = ctk.CTkButton(self.header_frame, text=">", width=40, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.next_month)
        btn_next.pack(side="left", padx=5)
        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)
        dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i, d in enumerate(dias):
            ctk.CTkLabel(self.days_frame, text=d, font=("Arial", 11, "bold"), text_color="#1f538d").grid(row=0, column=i, padx=4, pady=5)
        self.update_calendar()

    def update_calendar(self):
        for widget in self.days_frame.winfo_children():
            if int(widget.grid_info()["row"]) > 0:
                widget.destroy()
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
        fecha_seleccionada = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, fecha_seleccionada)
        self.destroy()

# =========================================================
# CLASE: DASHBOARD DE CALENDARIO GENERAL A PANTALLA COMPLETA
# =========================================================
class CalendarioDashboard(ctk.CTkToplevel):
    def __init__(self, parent, usuario_activo):
        super().__init__(parent)
        self.usuario_activo = usuario_activo
        self.title("Calendario General de Tareas y Eventos")
        self.geometry("1200x700")
        self.after(100, lambda: maximizar_ventana(self))
        self.transient(parent)
        self.grab_set()
        
        self.mes_actual = datetime.now().month
        self.anio_actual = datetime.now().year
        self.tareas_db = {}
        self.pop_detalle = None
        
        self.crear_interfaz()
        self.cargar_datos_db()

    def crear_interfaz(self):
        self.f_header = ctk.CTkFrame(self, corner_radius=0, fg_color="#1a252c")
        self.f_header.pack(fill="x")
        f_controles = ctk.CTkFrame(self.f_header, fg_color="transparent")
        f_controles.pack(side="left", padx=20, pady=8)
        ctk.CTkButton(f_controles, text="<", width=40, fg_color="#34495e", hover_color="#2c3e50", command=self.mes_anterior).pack(side="left", padx=5)
        self.lbl_mes_anio = ctk.CTkLabel(f_controles, text="", font=("Arial", 16, "bold"), text_color="white", width=150)
        self.lbl_mes_anio.pack(side="left", padx=5)
        ctk.CTkButton(f_controles, text=">", width=40, fg_color="#34495e", hover_color="#2c3e50", command=self.mes_siguiente).pack(side="left", padx=5)
        f_filtros = ctk.CTkFrame(self.f_header, fg_color="transparent")
        f_filtros.pack(side="left", padx=20, pady=8)
        ctk.CTkLabel(f_filtros, text="Filtrar por:", text_color="white", font=("Arial", 12, "bold")).pack(side="left", padx=5)
        self.combo_filtro_principal = ctk.CTkComboBox(f_filtros, values=["Todo", "Por Evento", "Trabajo Interno", "Por Proveedor"], command=self.actualizar_opciones_filtro, width=150)
        self.combo_filtro_principal.pack(side="left", padx=5)
        self.combo_filtro_principal.set("Todo")
        self.combo_filtro_secundario = ctk.CTkComboBox(f_filtros, values=["-"], state="disabled", command=self.aplicar_filtro, width=250)
        self.combo_filtro_secundario.pack(side="left", padx=5)
        f_btns = ctk.CTkFrame(self.f_header, fg_color="transparent")
        f_btns.pack(side="right", padx=20, pady=8)
        ctk.CTkButton(f_btns, text="📥 Exportar Todo", font=("Arial", 12, "bold"), fg_color="#28a745", hover_color="#218838", height=32, command=self.exportar_calendario_completo_ics).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="[ + ] Tarea Prov / Evento", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", height=32, command=self.abrir_agendar_proveedor).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="[ + ] Trabajo (Oficina)", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", height=32, command=self.abrir_agendar_interno).pack(side="left", padx=5)
        self.f_grid = ctk.CTkFrame(self, fg_color="#ecf0f1", corner_radius=0)
        self.f_grid.pack(fill="both", expand=True, padx=5, pady=5)
        for i in range(7):
            self.f_grid.grid_columnconfigure(i, weight=1, uniform="col")
        dias = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
        for i, d in enumerate(dias):
            ctk.CTkLabel(self.f_grid, text=d, font=("Arial", 12, "bold"), fg_color="#2980b9", text_color="white", corner_radius=5).grid(row=0, column=i, sticky="ew", padx=1, pady=1, ipadx=5, ipady=3)

    def actualizar_opciones_filtro(self, choice):
        if choice in ["Todo", "Trabajo Interno"]:
            self.combo_filtro_secundario.configure(values=["-"], state="disabled")
            self.combo_filtro_secundario.set("-")
            self.cargar_datos_db()
        else:
            self.combo_filtro_secundario.configure(state="normal")
            self.combo_filtro_secundario.set("Cargando...")
            
            def tarea_filtro():
                opciones = ["Sin registros"]
                if choice == "Por Evento":
                    evs = cache_sistema.obtener("lista_eventos_aprobados")
                    if evs: 
                        opciones = [ev for ev in evs if "OFICINA" not in ev]
                    else:
                        conn = conectar_db(silencioso=True)
                        if conn:
                            try:
                                cursor = conn.cursor()
                                cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                                opciones = [f"{cod} | {nom}" for cod, nom in cursor.fetchall()]
                            except Exception: pass
                            finally: liberar_conexion(conn)
                elif choice == "Por Proveedor":
                    provs = cache_sistema.obtener("lista_proveedores_combobox")
                    if provs:
                        opciones = provs
                    else:
                        conn = conectar_db(silencioso=True)
                        if conn:
                            try:
                                cursor = conn.cursor()
                                cursor.execute("SELECT DISTINCT responsable FROM tareas_evento WHERE responsable IS NOT NULL AND responsable != ''")
                                opciones = [str(r[0]) for r in cursor.fetchall() if r[0]]
                            except Exception: pass
                            finally: liberar_conexion(conn)
                            
                self.after(0, lambda: self._aplicar_opciones_filtro(opciones))

            threading.Thread(target=tarea_filtro, daemon=True).start()

    def _aplicar_opciones_filtro(self, opciones):
        self.combo_filtro_secundario.configure(values=opciones, state="readonly")
        if opciones: self.combo_filtro_secundario.set(opciones[0])
        self.cargar_datos_db()

    def aplicar_filtro(self, choice=None):
        self.cargar_datos_db()

    def cargar_datos_db(self):
        filtro_p = self.combo_filtro_principal.get() if hasattr(self, 'combo_filtro_principal') else "Todo"
        filtro_s = self.combo_filtro_secundario.get() if hasattr(self, 'combo_filtro_secundario') else "-"

        clave_cache = f"calendario_dash_evt_{filtro_p}_{filtro_s}"
        datos_calendario = cache_sistema.obtener(clave_cache)

        if datos_calendario is not None:
            self._procesar_y_dibujar(datos_calendario)
        else:
            def tarea_dash():
                datos_db = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        
                        # 🚀 1. OBTENER TAREAS NORMALES DEL CRONOGRAMA
                        query = "SELECT id, fecha_limite, nombre_tarea, estado, evento_asociado, responsable, notas FROM tareas_evento"
                        params = []
                        condiciones = []
                        if filtro_p == "Trabajo Interno":
                            condiciones.append("evento_asociado = 'OFICINA | Trabajos Internos'")
                        elif filtro_p == "Por Evento" and filtro_s not in ("-", "Sin registros", "Cargando..."):
                            condiciones.append("evento_asociado = %s")
                            params.append(filtro_s)
                        elif filtro_p == "Por Proveedor" and filtro_s not in ("-", "Sin registros", "Cargando..."):
                            condiciones.append("responsable = %s")
                            params.append(filtro_s)
                            
                        if condiciones:
                            query += " WHERE " + " AND ".join(condiciones)
                            
                        cursor.execute(query, tuple(params))
                        datos_db.extend(cursor.fetchall())
                        
                        # 🚀 2. INYECTAR LA FECHA PRINCIPAL DE LOS EVENTOS (SI APLICA EL FILTRO)
                        if filtro_p in ["Todo", "Por Evento"]:
                            query_ev = "SELECT id, fecha_evento, nombre_evento, status, codigo_cotizacion, nombre_empresa, locacion_evento FROM cotizaciones WHERE status = 'Aprobada' AND fecha_evento IS NOT NULL AND TRIM(fecha_evento) != ''"
                            params_ev = []
                            conds_ev = []
                            
                            if filtro_p == "Por Evento" and filtro_s not in ("-", "Sin registros", "Cargando..."):
                                cod_cot = filtro_s.split(" | ")[0].strip()
                                conds_ev.append("codigo_cotizacion = %s")
                                params_ev.append(cod_cot)
                                
                            if conds_ev:
                                query_ev += " AND " + " AND ".join(conds_ev)
                                
                            cursor.execute(query_ev, tuple(params_ev))
                            for r_ev in cursor.fetchall():
                                evt_id = f"EVT_{r_ev[0]}"
                                f_limite = r_ev[1]
                                t_nombre = f"⭐ DÍA DEL EVENTO: {r_ev[2]}"
                                estado = "Evento Principal"
                                evento_asoc = f"{r_ev[4]} | {r_ev[2]}"
                                responsable = f"Cliente: {r_ev[5]}"
                                notas = f"Locación: {r_ev[6]}\nStatus: {r_ev[3]}"
                                
                                datos_db.append((evt_id, f_limite, t_nombre, estado, evento_asoc, responsable, notas))
                        
                        cache_sistema.guardar(clave_cache, datos_db)
                    except Exception as e:
                        print("Error SQL Calendario:", e)
                    finally:
                        liberar_conexion(conn)
                        
                self.after(0, lambda: self._procesar_y_dibujar(datos_db))

            threading.Thread(target=tarea_dash, daemon=True).start()

    # 🚀 BLINDAJE EXTREMO CONTRA 'NoneType' EN DATOS DE LA BD
    def _procesar_y_dibujar(self, datos_db):
        try:
            self.tareas_db.clear()
            if datos_db:
                for t_id, f_limite, t_nombre, estado, evento, resp, notas in datos_db:
                    if f_limite:
                        if f_limite not in self.tareas_db:
                            self.tareas_db[f_limite] = []
                            
                        evento_str = str(evento) if evento else "Sin Evento"
                        evento_nombre_limpio = evento_str.split(" | ")[1] if " | " in evento_str else evento_str
                        
                        self.tareas_db[f_limite].append({
                            "id": t_id,
                            "tarea": str(t_nombre) if t_nombre else "Sin nombre",
                            "estado": str(estado) if estado else "Pendiente",
                            "evento_completo": evento_str,
                            "evento_nombre": evento_nombre_limpio,
                            "responsable": str(resp) if resp else "Sin responsable",
                            "notas": str(notas) if notas else "",
                            "fecha_limite": str(f_limite)
                        })
            self.dibujar_calendario()
        except Exception as e:
            print(f"Error procesando datos del calendario: {e}")
            self.dibujar_calendario() 

    # 🚀 DIBUJADO SEGURO DEL CALENDARIO
    def dibujar_calendario(self):
        try:
            for widget in self.f_grid.winfo_children():
                try:
                    info = widget.grid_info()
                    if info and str(info.get("row", "0")) != "0":
                        widget.destroy()
                except Exception:
                    pass

            meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
            self.lbl_mes_anio.configure(text=f"{meses[self.mes_actual].upper()} {self.anio_actual}")
            
            cal = calendar.monthcalendar(self.anio_actual, self.mes_actual)
            num_weeks = len(cal)
            
            for i in range(1, 7):
                self.f_grid.grid_rowconfigure(i, weight=0, uniform="")
            for i in range(1, num_weeks + 1):
                self.f_grid.grid_rowconfigure(i, weight=1, uniform="row")
                
            hoy = datetime.now()
            
            for row_idx, week in enumerate(cal, start=1):
                for col_idx, day in enumerate(week):
                    if day != 0:
                        fecha_str = f"{day:02d}/{self.mes_actual:02d}/{self.anio_actual}"
                        bg_color = "#ffffff"
                        if day == hoy.day and self.mes_actual == hoy.month and self.anio_actual == hoy.year:
                            bg_color = "#eafaf1"
                            
                        cell_frame = ctk.CTkFrame(self.f_grid, fg_color=bg_color, corner_radius=5, border_width=1, border_color="#bdc3c7")
                        cell_frame.grid(row=row_idx, column=col_idx, sticky="nsew", padx=1, pady=1)
                        
                        lbl_day = ctk.CTkLabel(cell_frame, text=str(day), font=("Arial", 11, "bold"), text_color="#2c3e50", height=12)
                        lbl_day.pack(side="top", anchor="ne", padx=5, pady=(0, 0))
                        
                        if fecha_str in self.tareas_db:
                            tareas_del_dia = self.tareas_db[fecha_str]
                            if len(tareas_del_dia) <= 5:
                                f_contenedor = ctk.CTkFrame(cell_frame, fg_color="transparent")
                                f_contenedor.pack(fill="both", expand=True, padx=1, pady=0)
                            else:
                                f_contenedor = ctk.CTkScrollableFrame(cell_frame, fg_color="transparent", height=90)
                                f_contenedor.pack(fill="both", expand=True, padx=0, pady=0)
                                
                            for tarea_data in tareas_del_dia:
                                t_estado = tarea_data["estado"]
                                
                                if t_estado == "En Progreso":
                                    color_estado = "#fff3cd"
                                    text_color = "#856404"
                                elif t_estado == "Completada":
                                    color_estado = "#d4edda"
                                    text_color = "#155724"
                                elif t_estado == "Evento Principal":
                                    color_estado = "#1f538d" 
                                    text_color = "#ffffff"
                                else:
                                    color_estado = "#f8d7da"
                                    text_color = "#721c24"
                                    
                                titulo_corto = tarea_data["tarea"][:34] + ".." if len(tarea_data["tarea"]) > 34 else tarea_data["tarea"]
                                lbl_tarea = ctk.CTkLabel(
                                    f_contenedor,
                                    text=f"• {titulo_corto}",
                                    font=("Arial", 9, "bold"),
                                    fg_color=color_estado,
                                    text_color=text_color,
                                    corner_radius=3,
                                    anchor="w",
                                    padx=4,
                                    cursor="hand2",
                                    height=16
                                )
                                lbl_tarea.pack(side="top", fill="x", pady=(0, 1))
                                lbl_tarea.bind("<Button-1>", lambda e, td=tarea_data: self.mostrar_detalle_tarea(td))
        except Exception as e:
            print(f"Error fatal dibujando el calendario: {e}")

    def exportar_calendario_completo_ics(self):
        if not self.tareas_db:
            messagebox.showinfo("Información", "No hay tareas cargadas en el calendario actual para exportar.")
            return
        ics_content = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//BlackCube//CronogramaApp//ES",
            "CALSCALE:GREGORIAN"
        ]
        contador_tareas = 0
        for f_limite, tareas_dia in self.tareas_db.items():
            for td in tareas_dia:
                try:
                    dt_start = datetime.strptime(f_limite, "%d/%m/%Y")
                    dt_end = dt_start + timedelta(days=1)
                    str_start = dt_start.strftime("%Y%m%d")
                    str_end = dt_end.strftime("%Y%m%d")
                    dtstamp = datetime.now().strftime('%Y%m%dT%H%M%SZ')
                    desc = f"📌 Evento: {td['evento_completo']}\\n👤 Responsable: {td['responsable']}\\n📊 Estado: {td['estado']}\\n📋 Notas: {td['notas']}".replace("\n", "\\n")
                    ics_content.extend([
                        "BEGIN:VEVENT",
                        f"UID:tarea_global_{contador_tareas}_{dtstamp}@blackcube",
                        f"DTSTAMP:{dtstamp}",
                        f"DTSTART;VALUE=DATE:{str_start}",
                        f"DTEND;VALUE=DATE:{str_end}",
                        f"SUMMARY:{td['tarea']} - {td['evento_nombre']}",
                        f"DESCRIPTION:{desc}",
                        "END:VEVENT"
                    ])
                    contador_tareas += 1
                except Exception as e:
                    print(f"Error procesando fecha {f_limite}: {e}")
        ics_content.append("END:VCALENDAR")
        try:
            fd, path = tempfile.mkstemp(suffix=".ics", prefix="Calendario_Completo_BlackCube_")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("\n".join(ics_content))
            abrir_documento(path)
            messagebox.showinfo("Exportación Exitosa",
                f"✅ El calendario se ha generado correctamente.\n\n"
                f"Ruta: {path}\n\n"
                "• Windows/Mac: Se abrirá tu aplicación predeterminada (Ej: Outlook / Apple Calendar).\n"
                "• Google Calendar: Ve a 'Configuración' -> 'Importar y exportar' y sube este archivo.")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar el calendario:\n{e}")

    def exportar_tarea_google(self, td):
        try:
            f_limite = td["fecha_limite"]
            dt_start = datetime.strptime(f_limite, "%d/%m/%Y")
            dt_end = dt_start + timedelta(days=1)
            str_start = dt_start.strftime("%Y%m%d")
            str_end = dt_end.strftime("%Y%m%d")
            titulo = f"{td['tarea']} - {td['evento_nombre']}"
            desc = f"📌 Evento: {td['evento_completo']}\n👤 Responsable: {td['responsable']}\n📊 Estado: {td['estado']}\n📋 Notas: {td['notas']}"
            base_url = "https://calendar.google.com/calendar/render?action=TEMPLATE"
            text_param = urllib.parse.quote(titulo)
            dates_param = f"{str_start}/{str_end}"
            details_param = urllib.parse.quote(desc)
            final_url = f"{base_url}&text={text_param}&dates={dates_param}&details={details_param}"
            webbrowser.open(final_url)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo abrir Google Calendar:\n{e}")

    def exportar_tarea_ics(self, td):
        try:
            f_limite = td["fecha_limite"]
            dt_start = datetime.strptime(f_limite, "%d/%m/%Y")
            dt_end = dt_start + timedelta(days=1)
            str_start = dt_start.strftime("%Y%m%d")
            str_end = dt_end.strftime("%Y%m%d")
            dtstamp = datetime.now().strftime('%Y%m%dT%H%M%SZ')
            desc = f"📌 Evento: {td['evento_completo']}\\n👤 Responsable: {td['responsable']}\\n📊 Estado: {td['estado']}\\n📋 Notas: {td['notas']}".replace("\n", "\\n")
            ics_content = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//BlackCube//CronogramaApp//ES",
                "CALSCALE:GREGORIAN",
                "BEGIN:VEVENT",
                f"UID:tarea_{datetime.now().strftime('%Y%m%d%H%M%S')}@blackcube",
                f"DTSTAMP:{dtstamp}",
                f"DTSTART;VALUE=DATE:{str_start}",
                f"DTEND;VALUE=DATE:{str_end}",
                f"SUMMARY:{td['tarea']} - {td['evento_nombre']}",
                f"DESCRIPTION:{desc}",
                "END:VEVENT",
                "END:VCALENDAR"
            ]
            fd, path = tempfile.mkstemp(suffix=".ics", prefix="Tarea_BlackCube_")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write("\n".join(ics_content))
            abrir_documento(path)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar la tarea al calendario:\n{e}")

    def mostrar_detalle_tarea(self, td):
        det_id = td.get('id')
        det_evento = td['evento_completo']
        det_tarea = td['tarea']
        det_resp = td['responsable']
        det_est = td['estado']
        det_notas = td['notas']
        if self.pop_detalle is not None and self.pop_detalle.winfo_exists():
            for widget in self.pop_detalle.winfo_children():
                widget.destroy()
            pop = self.pop_detalle
        else:
            pop = ctk.CTkToplevel(self)
            self.pop_detalle = pop
            pop.title("Detalles de la Tarea")
            pop.geometry("480x550")
            pop.resizable(False, False)
            pop.transient(self)
            pop.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() // 2) - (480 // 2)
            y = self.winfo_rooty() + (self.winfo_height() // 2) - (550 // 2)
            pop.geometry(f"+{x}+{y}")
        f_top = ctk.CTkFrame(pop, fg_color="transparent")
        f_top.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(f_top, text="🔍 DETALLES DE LA TAREA", font=("Arial", 16, "bold"), text_color="#1f538d").pack(side="left", expand=True, anchor="w", padx=(5, 0))
        f_botones_export = ctk.CTkFrame(f_top, fg_color="transparent")
        f_botones_export.pack(side="right")
        btn_export_ics = ctk.CTkButton(f_botones_export, text="📥 .ics (Mac/Outlook)", font=("Arial", 10, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", width=130, height=26, command=lambda: self.exportar_tarea_ics(td))
        btn_export_ics.pack(side="top", pady=(0, 4))
        btn_google = ctk.CTkButton(f_botones_export, text="🌐 Google Calendar", font=("Arial", 10, "bold"), fg_color="#2980b9", hover_color="#1c5982", width=130, height=26, command=lambda: self.exportar_tarea_google(td))
        btn_google.pack(side="top")
        f_info = ctk.CTkFrame(pop, fg_color="transparent")
        f_info.pack(fill="both", expand=True, padx=20, pady=5)
        ctk.CTkLabel(f_info, text="📌 EVENTO / PROYECTO:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(5, 0))
        ctk.CTkLabel(f_info, text=det_evento, font=("Arial", 13), justify="left").pack(anchor="w", padx=5)
        ctk.CTkLabel(f_info, text="📝 TAREA A REALIZAR:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 0))
        ent_tarea = ctk.CTkEntry(f_info, font=("Arial", 12))
        ent_tarea.pack(fill="x", padx=5, pady=(2, 0))
        ent_tarea.insert(0, det_tarea)
        
        if str(det_id).startswith("EVT_"):
            ent_tarea.configure(state="disabled")
            
        ctk.CTkLabel(f_info, text="👤 RESPONSABLE:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 0))
        ent_resp = ctk.CTkEntry(f_info, font=("Arial", 12))
        ent_resp.pack(fill="x", padx=5, pady=(2, 0))
        ent_resp.insert(0, det_resp)
        if str(det_id).startswith("EVT_"):
            ent_resp.configure(state="disabled")
            
        ctk.CTkLabel(f_info, text="📊 ESTADO ACTUAL:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 0))
        cmb_estado = ctk.CTkComboBox(f_info, values=["Pendiente", "En Progreso", "Completada"], font=("Arial", 12), state="readonly")
        cmb_estado.pack(fill="x", padx=5, pady=(2, 0))
        cmb_estado.set(det_est)
        if str(det_id).startswith("EVT_"):
            cmb_estado.set("Evento Principal")
            cmb_estado.configure(state="disabled")
            
        ctk.CTkLabel(f_info, text="📋 NOTAS ADICIONALES:", font=("Arial", 11, "bold")).pack(anchor="w", pady=(10, 0))
        txt_notas = ctk.CTkTextbox(f_info, height=90, wrap="word", border_width=1, fg_color="#ffffff", text_color="black")
        txt_notas.pack(fill="x", padx=5, pady=(2, 0))
        txt_notas.insert("1.0", det_notas)
        if str(det_id).startswith("EVT_"):
            txt_notas.configure(state="disabled")
            
        f_botones = ctk.CTkFrame(pop, fg_color="transparent")
        f_botones.pack(fill="x", pady=(5, 15), padx=20)
        
        if str(det_id).startswith("EVT_"):
            btn_guardar = ctk.CTkButton(f_botones, text="🔒 Evento Principal (Solo Lectura)", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", height=32, state="disabled")
        else:
            btn_guardar = ctk.CTkButton(f_botones, text="💾 Guardar Cambios", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", height=32,
                                        command=lambda: self.guardar_edicion_rapida(det_id, ent_tarea.get(), ent_resp.get(), cmb_estado.get(), txt_notas.get("1.0", "end-1c"), pop))
        btn_guardar.pack(side="left", expand=True, fill="x", padx=(0, 5))
        btn_cerrar = ctk.CTkButton(f_botones, text="Cerrar", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", height=32, command=pop.destroy)
        btn_cerrar.pack(side="right", expand=True, fill="x", padx=(5, 0))

    def guardar_edicion_rapida(self, t_id, nueva_tarea, nuevo_resp, nuevo_estado, nuevas_notas, pop_window):
        if not nueva_tarea.strip():
            messagebox.showwarning("Atención", "La descripción de la tarea es obligatoria.", parent=pop_window)
            return
        conn = conectar_db()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tareas_evento 
                SET nombre_tarea=%s, responsable=%s, estado=%s, notas=%s
                WHERE id=%s
            """, (nueva_tarea.strip(), nuevo_resp.strip(), nuevo_estado, nuevas_notas.strip(), t_id))
            conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Cronograma", f"Edición rápida de tarea ID {t_id}")
            pop_window.destroy()
            self.cargar_datos_db()
            messagebox.showinfo("Éxito", "Cambios guardados correctamente.")
        except Exception as ex:
            messagebox.showerror("Error", f"No se pudo guardar los cambios:\n{ex}", parent=pop_window)
        finally:
            liberar_conexion(conn)

    def mes_anterior(self):
        self.mes_actual -= 1
        if self.mes_actual < 1:
            self.mes_actual = 12
            self.anio_actual -= 1
        self.dibujar_calendario()

    def mes_siguiente(self):
        self.mes_actual += 1
        if self.mes_actual > 12:
            self.mes_actual = 1
            self.anio_actual += 1
        self.dibujar_calendario()

    # 🚀 FIX: FUNCIONES DE AGENDADO RESTAURADAS Y OPERATIVAS
    def abrir_agendar_proveedor(self):
        v_prov = ctk.CTkToplevel(self)
        v_prov.title("Agendar Tarea a Proveedor / Evento")
        v_prov.geometry("420x580")
        v_prov.transient(self)
        v_prov.grab_set()
        ctk.CTkLabel(v_prov, text="📋 TAREA PARA PROVEEDOR", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        f_cont = ctk.CTkFrame(v_prov, fg_color="transparent")
        f_cont.pack(fill="both", expand=True, padx=20, pady=5)
        ctk.CTkLabel(f_cont, text="Evento Aprobado:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_ev = ctk.CTkComboBox(f_cont, state="readonly")
        cmb_ev.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f_cont, text="Proveedor del Evento:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_prov = ctk.CTkComboBox(f_cont, state="readonly", values=["Seleccione un evento primero"])
        cmb_prov.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f_cont, text="Tipo de Acción:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_acc = ctk.CTkComboBox(f_cont, values=["Instalación", "Desmontaje", "Provisión", "Operación", "Coordinación", "Otro"], state="readonly")
        cmb_acc.pack(fill="x", pady=(0, 10))
        cmb_acc.set("Instalación")
        ctk.CTkLabel(f_cont, text="Categoría / Detalle de la Tarea:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_t = ctk.CTkEntry(f_cont, placeholder_text="Ej: Toldo 8x4 / Catering")
        ent_t.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f_cont, text="Fecha Límite (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(f_cont, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 10))
        ent_f = ctk.CTkEntry(f_fecha, placeholder_text="Seleccione fecha...")
        ent_f.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fecha, text="[ 📅 ]", width=40, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_prov, ent_f)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_cont, text="Estado Actual:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_e = ctk.CTkComboBox(f_cont, values=["Pendiente", "En Progreso", "Completada"], state="readonly")
        cmb_e.pack(fill="x", pady=(0, 15))
        cmb_e.set("Pendiente")

        def cargar_eventos():
            eventos = []
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                    eventos = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                except Exception:
                    eventos = []
                finally:
                    liberar_conexion(conn)
            v_prov.after(0, lambda e=eventos: aplicar_eventos(e))

        def aplicar_eventos(eventos):
            try:
                if not v_prov.winfo_exists():
                    return
            except Exception:
                return
            if eventos:
                cmb_ev.configure(values=eventos)
                cmb_ev.set(eventos[0])
            else:
                cmb_ev.configure(values=["Sin eventos"])
                cmb_ev.set("Sin eventos")

        threading.Thread(target=cargar_eventos, daemon=True).start()

        def cargar_proveedores(choice):
            if "Sin eventos" in choice:
                return
            cod = choice.split(" | ")[0].strip()
            conn2 = conectar_db(silencioso=True)
            if conn2:
                try:
                    c2 = conn2.cursor()
                    c2.execute("SELECT proveedor_nombre FROM cotizacion_proveedores WHERE codigo_cotizacion = %s", (cod,))
                    provs = []
                    for r in c2.fetchall():
                        p = str(r[0]).strip()
                        if p and p not in provs:
                            provs.append(p)
                    if provs:
                        cmb_prov.configure(values=provs)
                        cmb_prov.set(provs[0])
                    else:
                        cmb_prov.configure(values=["Sin proveedores asignados"])
                        cmb_prov.set("Sin proveedores asignados")
                except Exception:
                    pass
                finally:
                    liberar_conexion(conn2)

        cmb_ev.configure(command=cargar_proveedores)

        def guardar_prov():
            ev = cmb_ev.get()
            pr = cmb_prov.get()
            t_base = ent_t.get().strip()
            f = ent_f.get().strip()
            e = cmb_e.get()
            accion = cmb_acc.get()
            if ev == "Sin eventos" or not ev:
                messagebox.showwarning("Atención", "Debe seleccionar un evento.", parent=v_prov)
                return
            if not t_base:
                messagebox.showwarning("Atención", "La descripción de la tarea es obligatoria.", parent=v_prov)
                return
            tarea_completa = f"{accion}: {t_base}"
            conn3 = conectar_db()
            if not conn3:
                return
            try:
                c3 = conn3.cursor()
                c3.execute("SELECT id FROM tareas_evento WHERE evento_asociado = %s AND nombre_tarea = %s", (ev, tarea_completa))
                if c3.fetchone():
                    if not messagebox.askyesno("Tarea Existente", f"Ya registraste:\n'{tarea_completa}'\n\n¿Seguro que deseas agregarla otra vez para este mismo evento?", parent=v_prov):
                        liberar_conexion(conn3)
                        return
                c3.execute("SELECT COALESCE(MAX(orden), 0) FROM tareas_evento WHERE evento_asociado = %s", (ev,))
                nuevo_orden = c3.fetchone()[0] + 1
                c3.execute("""
                    INSERT INTO tareas_evento (evento_asociado, nombre_tarea, responsable, fecha_limite, estado, notas, orden, tipo_pago)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (ev, tarea_completa, pr, f, e, "Tarea registrada directamente desde el calendario.", nuevo_orden, "Crédito"))
                conn3.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Agendó tarea: '{tarea_completa}' para proveedor {pr}")
                self.cargar_datos_db()
                ent_t.delete(0, tk.END)
                messagebox.showinfo("Éxito", "Tarea guardada correctamente.\n\nPuede cambiar el Tipo de Acción (ej. Desmontaje) y agregar otra tarea para este mismo evento/proveedor sin cerrar la ventana.", parent=v_prov)
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=v_prov)
            finally:
                liberar_conexion(conn3)

        ctk.CTkButton(f_cont, text="💾 Guardar y Agregar Otra Tarea", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=guardar_prov).pack(fill="x", pady=10)

    def abrir_agendar_interno(self):
        v_int = ctk.CTkToplevel(self)
        v_int.title("Agendar Trabajo de Oficina")
        v_int.geometry("380x480")
        v_int.transient(self)
        v_int.grab_set()
        ctk.CTkLabel(v_int, text="🏢 NUEVA TAREA INTERNA", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        f_cont = ctk.CTkFrame(v_int, fg_color="transparent")
        f_cont.pack(fill="both", expand=True, padx=20, pady=5)
        ctk.CTkLabel(f_cont, text="Tipo de Acción:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_acc = ctk.CTkComboBox(f_cont, values=["Reunión", "Llamada", "Gestión", "Compra", "Otro"], state="readonly")
        cmb_acc.pack(fill="x", pady=(0, 10))
        cmb_acc.set("Gestión")
        ctk.CTkLabel(f_cont, text="Descripción de la Tarea:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_t = ctk.CTkEntry(f_cont)
        ent_t.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f_cont, text="Responsable / Área:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_r = ctk.CTkEntry(f_cont)
        ent_r.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(f_cont, text="Fecha Límite (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(f_cont, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 10))
        ent_f = ctk.CTkEntry(f_fecha, placeholder_text="Seleccione fecha...")
        ent_f.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fecha, text="[ 📅 ]", width=40, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_int, ent_f)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_cont, text="Estado Actual:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_e = ctk.CTkComboBox(f_cont, values=["Pendiente", "En Progreso", "Completada"], state="readonly")
        cmb_e.pack(fill="x", pady=(0, 15))
        cmb_e.set("Pendiente")

        def guardar_interno():
            t_base = ent_t.get().strip()
            r = ent_r.get().strip()
            f = ent_f.get().strip()
            e = cmb_e.get()
            accion = cmb_acc.get()
            if not t_base:
                messagebox.showwarning("Atención", "La descripción de la tarea es obligatoria.", parent=v_int)
                return
            tarea_completa = f"{accion}: {t_base}"
            conn = conectar_db()
            if not conn:
                return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM tareas_evento WHERE evento_asociado = %s AND nombre_tarea = %s", ("OFICINA | Trabajos Internos", tarea_completa))
                if cursor.fetchone():
                    if not messagebox.askyesno("Entrada Existente", f"Ya registraste:\n'{tarea_completa}'\n\n¿Seguro que deseas agregarla otra vez?", parent=v_int):
                        liberar_conexion(conn)
                        return
                cursor.execute("SELECT COALESCE(MAX(orden), 0) FROM tareas_evento WHERE evento_asociado = 'OFICINA | Trabajos Internos'")
                nuevo_orden = cursor.fetchone()[0] + 1
                cursor.execute("""
                    INSERT INTO tareas_evento (evento_asociado, nombre_tarea, responsable, fecha_limite, estado, notas, orden, tipo_pago)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, ("OFICINA | Trabajos Internos", tarea_completa, r, f, e, "Asignación interna de oficina.", nuevo_orden, "No aplica"))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Agendó trabajo interno: '{tarea_completa}'")
                v_int.destroy()
                self.cargar_datos_db()
                messagebox.showinfo("Éxito", "Trabajo interno agendado y visible en el calendario.")
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=v_int)
            finally:
                liberar_conexion(conn)

        ctk.CTkButton(f_cont, text="💾 Agendar Tarea", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=guardar_interno).pack(fill="x", pady=10)


# =========================================================
# CLASE PRINCIPAL: GANTT Y CRONOGRAMA (OPTIMIZADA)
# =========================================================
class CronogramaApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.usuario_activo = "Desconocido"
        self.pantalla_expandida = False
        self.dict_responsables = {}
        self.dict_notas = {}
        self.ruta_temp_pago = ""
        self.archivo_ya_cargado_db = ""
        
        # 🚀 VARIABLES DE PAGINACIÓN (LAZY LOADING)
        self.pagina_actual = 1
        self.registros_por_pagina = 50
        
        self.inicializar_bd()
        self.crear_interfaz()

    # 🚀 FIX: AUTO-CURACIÓN SÍNCRONA
    def inicializar_bd(self):
        global _SCHEMA_CRON_OK
        if _SCHEMA_CRON_OK: return
        
        conn = conectar_db(silencioso=True)
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tareas_evento (
                    id SERIAL PRIMARY KEY,
                    evento_asociado VARCHAR(255),
                    nombre_tarea VARCHAR(255),
                    responsable VARCHAR(100),
                    fecha_limite VARCHAR(20),
                    estado VARCHAR(50),
                    notas TEXT
                )
            """)
            conn.commit()
            for sql in (
                "ALTER TABLE tareas_evento ADD COLUMN IF NOT EXISTS orden INTEGER DEFAULT 0",
                "ALTER TABLE tareas_evento ADD COLUMN IF NOT EXISTS tipo_pago VARCHAR(50) DEFAULT 'Crédito'",
                "ALTER TABLE tareas_evento ADD COLUMN IF NOT EXISTS archivo_pago TEXT",
            ):
                try:
                    cursor.execute(sql)
                    conn.commit()
                except Exception:
                    conn.rollback()
            try:
                cursor.execute("UPDATE tareas_evento SET orden = id WHERE orden = 0")
                conn.commit()
            except Exception:
                conn.rollback()
            _SCHEMA_CRON_OK = True
        except Exception as e:
            print("Error BD Tareas:", e)
        finally:
            liberar_conexion(conn)

    def abrir_calendario(self, entry_objetivo):
        CalendarioNativo(self.parent_frame.winfo_toplevel(), entry_objetivo)

    def abrir_calendario_dashboard(self):
        CalendarioDashboard(self.parent_frame.winfo_toplevel(), self.usuario_activo)

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
        ctk.CTkLabel(self.frame_main, text="📅 CRONOGRAMA DE EJECUCIÓN DE EVENTOS", font=("Arial", 18, "bold"), text_color="#1f538d").pack(anchor="w", pady=(0, 10))
        
        f_top = ctk.CTkFrame(self.frame_main, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_top.pack(fill="x", pady=(0, 15), ipadx=10, ipady=5)
        ctk.CTkLabel(f_top, text="Seleccione el Evento / Proyecto:", font=("Arial", 12, "bold"), text_color="#333333").pack(side="left", padx=(10, 10), pady=10)
        self.combo_evento_global = ctk.CTkComboBox(f_top, width=400, state="readonly", command=lambda e: self.cargar_tareas_tabla(reset_pagina=True))
        self.combo_evento_global.pack(side="left", padx=10, pady=10)
        
        frame_split = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)
        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=320)
        self.f_form.pack(side="left", fill="y", padx=(0, 15))
        
        ctk.CTkLabel(self.f_form, text="Gestión de Tarea", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(10, 15))
        ctk.CTkLabel(self.f_form, text="Tipo de Acción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_accion = ctk.CTkComboBox(self.f_form, values=["Instalación", "Desmontaje", "Provisión", "Operación", "Coordinación", "Otro"], state="readonly", command=self.actualizar_sugerencias_tarea)
        self.combo_accion.pack(fill="x", padx=10, pady=(0, 10))
        self.combo_accion.set("Instalación")
        
        ctk.CTkLabel(self.f_form, text="Categoría / Detalle de Tarea:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_tarea = ctk.CTkComboBox(self.f_form, command=self.autocompletar_responsable, values=["Escriba o seleccione..."])
        self.ent_tarea.pack(fill="x", padx=10, pady=(0, 10))
        
        ctk.CTkLabel(self.f_form, text="Responsable Asignado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_responsable = ctk.CTkEntry(self.f_form, placeholder_text="Ej. Juan Pérez / Logística")
        self.ent_responsable.pack(fill="x", padx=10, pady=(0, 10))
        
        ctk.CTkLabel(self.f_form, text="Fecha Límite (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fecha = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fecha.pack(fill="x", padx=10, pady=(0, 10))
        self.ent_fecha = ctk.CTkEntry(f_fecha, placeholder_text="Seleccione fecha...")
        self.ent_fecha.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_fecha, text="[ 📅 ]", width=40, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha)).pack(side="right", padx=(5, 0))
        
        ctk.CTkLabel(self.f_form, text="Estado Actual:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_estado = ctk.CTkComboBox(self.f_form, values=["Pendiente", "En Progreso", "Completada"], state="readonly")
        self.combo_estado.pack(fill="x", padx=10, pady=(0, 10))
        self.combo_estado.set("Pendiente")
        
        ctk.CTkLabel(self.f_form, text="Exigencia de Pago:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_tipo_pago = ctk.CTkComboBox(self.f_form, values=["Crédito", "50% para ejecución", "100% para ejecución", "No aplica"], state="readonly")
        self.combo_tipo_pago.pack(fill="x", padx=10, pady=(0, 10))
        self.combo_tipo_pago.set("Crédito")
        
        self.btn_adjuntar_pago = ctk.CTkButton(self.f_form, text="📎 Cargar Pago/Factura (PDF)", fg_color="#7f8c8d", hover_color="#606b6b", command=self.adjuntar_pago)
        self.btn_adjuntar_pago.pack(fill="x", padx=10, pady=(0, 15))
        
        ctk.CTkLabel(self.f_form, text="Notas Adicionales:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.txt_notas = ctk.CTkTextbox(self.f_form, height=80, border_width=1)
        self.txt_notas.pack(fill="x", padx=10, pady=(0, 15))
        
        self.btn_guardar = ctk.CTkButton(self.f_form, text="💾 Agregar Tarea", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_tarea)
        self.btn_guardar.pack(fill="x", padx=10, pady=(10, 5))
        
        self.btn_limpiar = ctk.CTkButton(self.f_form, text="🧹 Limpiar Formulario", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.limpiar_formulario)
        self.btn_limpiar.pack(fill="x", padx=10, pady=5)
        
        self.id_tarea_seleccionada = None
        
        self.f_wrapper_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_wrapper_derecha.pack(side="right", fill="both", expand=True)
        ctk.CTkLabel(self.f_wrapper_derecha, text="Lista de Tareas (Seleccione para editar/eliminar/mover)", font=("Arial", 13, "bold")).pack(anchor="w", pady=(0, 5))
        
        f_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)
        columnas = ("num", "id", "estado", "tarea", "responsable", "fecha_limite", "tipo_pago", "notas", "archivo_pago")
        style = ttk.Style()
        style.configure("Treeview", rowheight=30, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", style="Treeview")
        self.tabla.heading("num", text="N°")
        self.tabla.heading("id", text="ID (Oculto)")
        self.tabla.heading("estado", text="Estado")
        self.tabla.heading("tarea", text="Descripción de la Tarea")
        self.tabla.heading("responsable", text="Responsable")
        self.tabla.heading("fecha_limite", text="Fecha Límite")
        self.tabla.heading("tipo_pago", text="Pago/Adelanto")
        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id", width=0, stretch=tk.NO)
        self.tabla.column("estado", width=100, anchor="center")
        self.tabla.column("tarea", width=220, anchor="w")
        self.tabla.column("responsable", width=120, anchor="w")
        self.tabla.column("fecha_limite", width=100, anchor="center")
        self.tabla.column("tipo_pago", width=130, anchor="center")
        self.tabla.config(displaycolumns=("num", "estado", "tarea", "responsable", "fecha_limite", "tipo_pago"))
        self.tabla.tag_configure("Pendiente", background="#f8d7da", foreground="#721c24")
        self.tabla.tag_configure("En Progreso", background="#fff3cd", foreground="#856404")
        self.tabla.tag_configure("Completada", background="#d4edda", foreground="#155724")
        self.tabla.bind("<<TreeviewSelect>>", self.seleccionar_tarea_tabla)
        
        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")
        
        f_btn_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=(15, 0))
        
        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(f_btn_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        self.btn_pantalla = ctk.CTkButton(f_btn_tabla, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="left")
        btn_ver_calendario = ctk.CTkButton(f_btn_tabla, text="🗓️ Ver Calendario General", font=("Arial", 12, "bold"), width=180, fg_color="#1f538d", hover_color="#163b65", command=self.abrir_calendario_dashboard)
        btn_ver_calendario.pack(side="left", padx=(15, 5))
        
        btn_subir = ctk.CTkButton(f_btn_tabla, text="▲", width=40, fg_color="#e0e0e0", text_color="black", hover_color="#c8c8c8", command=lambda: self.mover_tarea("ARRIBA"))
        btn_subir.pack(side="left", padx=5)
        btn_bajar = ctk.CTkButton(f_btn_tabla, text="▼", width=40, fg_color="#e0e0e0", text_color="black", hover_color="#c8c8c8", command=lambda: self.mover_tarea("ABAJO"))
        btn_bajar.pack(side="left", padx=5)
        
        btn_editar = ctk.CTkButton(f_btn_tabla, text="✏️ Editar", font=("Arial", 12, "bold"), width=90, fg_color="#34495e", hover_color="#2c3e50", command=self.abrir_ventana_edicion)
        btn_editar.pack(side="left", padx=5)
        btn_eliminar = ctk.CTkButton(f_btn_tabla, text="❌ Eliminar", font=("Arial", 12, "bold"), width=90, fg_color="#e74c3c", hover_color="#c0392b", command=self.eliminar_tarea)
        btn_eliminar.pack(side="right")
        
        self.parent_frame.after(100, self.cargar_eventos_aprobados)

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_tareas_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_tareas_tabla()

    def actualizar_sugerencias_tarea(self, *_):
        evento_seleccionado = getattr(self, "combo_evento_global", None)
        if not evento_seleccionado:
            return
        evento_seleccionado = evento_seleccionado.get()
        if "Sin eventos aprobados" in evento_seleccionado or not evento_seleccionado.strip() or "Cargando" in evento_seleccionado:
            return
        codigo_cot = evento_seleccionado.split(" | ")[0].strip()
        
        conn = conectar_db(silencioso=True)
        if not conn: return
        try:
            cursor = conn.cursor()
            self.dict_responsables = {}
            self.dict_notas = {}
            opciones_tarea = []
            if codigo_cot != "OFICINA":
                cursor.execute("SELECT categoria_suministro, proveedor_nombre, notes_negociacion FROM cotizacion_proveedores WHERE codigo_cotizacion = %s", (codigo_cot,))
                proveedores = cursor.fetchall()
                for r in proveedores:
                    cat = str(r[0]).strip().replace("('", "").replace("',)", "").replace("',", "").strip("() '\", ")
                    prov = str(r[1]).strip()
                    nota = str(r[2]).strip()
                    if cat:
                        if cat not in opciones_tarea:
                            self.dict_responsables[cat] = prov
                            self.dict_notas[cat] = nota
                            opciones_tarea.append(cat)
                        elif self.dict_responsables.get(cat) != prov:
                            cat_alt = f"{cat} ({prov})"
                            if cat_alt not in opciones_tarea:
                                self.dict_responsables[cat_alt] = prov
                                self.dict_notas[cat_alt] = nota
                                opciones_tarea.append(cat_alt)
            if opciones_tarea:
                self.ent_tarea.configure(values=opciones_tarea)
            else:
                self.ent_tarea.configure(values=["Escribir tarea manual..."])
        except Exception: pass
        finally: liberar_conexion(conn)

    # =======================================================
    # ESCÁNER DE FACTURA + COPIA A DRIVE (RUTA CONFIGURABLE)
    # =======================================================
    def procesar_e_insertar_factura(self, ruta_origen, proveedor_tarea, evento_tarea, desc_tarea):
        tipo_doc = "Factura (18% IGV)"
        nro_doc = "S/N"
        fecha_doc = datetime.now().strftime("%d/%m/%Y")
        subtotal = 0.0
        total = 0.0
        impuesto = 0.0
        try:
            import pdfplumber
        except ImportError:
            pdfplumber = None
        if ruta_origen.lower().endswith('.pdf') and pdfplumber:
            try:
                import re
                texto = ""
                with pdfplumber.open(ruta_origen) as pdf:
                    for page in pdf.pages:
                        texto += page.extract_text() + "\n"
                if re.search(r"BOLETA", texto, re.IGNORECASE):
                    tipo_doc = "Boleta (Sin IGV)"
                elif re.search(r"RECIBO", texto, re.IGNORECASE):
                    tipo_doc = "Recibo por Honorarios (Sin Retención)"
                nro_match = re.search(r"([EFB][0-9A-Z]{3}\s*-\s*\d+)", texto)
                if nro_match:
                    nro_doc = nro_match.group(1).replace(" ", "")
                fecha_match = re.search(r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto)
                if fecha_match:
                    fecha_doc = f"{fecha_match.group(1)}/{fecha_match.group(2)}/{fecha_match.group(3)}"
                sub_match = re.search(r"(?:OP\.\s*GRAVADAS|SUB\s*TOTAL|Subtotal|Total por honorarios)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
                tot_match = re.search(r"(?:IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|Total Neto Recibido)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
                if sub_match:
                    subtotal = float(sub_match.group(1).replace(",", ""))
                if tot_match:
                    total = float(tot_match.group(1).replace(",", ""))
                if total > 0 and subtotal == 0:
                    if "Factura" in tipo_doc:
                        subtotal = total / 1.18
                    else:
                        subtotal = total
                elif subtotal > 0 and total == 0:
                    if "Factura" in tipo_doc:
                        total = subtotal * 1.18
                    else:
                        total = subtotal
                if "Factura" in tipo_doc:
                    impuesto = total - subtotal
            except Exception:
                pass
        import shutil
        ruta_base = obtener_ruta_base_drive()
        if ruta_base:
            carpeta_destino = os.path.join(ruta_base, "facturas_recibidas")
        else:
            carpeta_destino = r"G:\Mi unidad\Programa de control black Cube\facturas_recibidas"
        if not os.path.exists(carpeta_destino):
            try: os.makedirs(carpeta_destino)
            except Exception: pass
            
        nombre_ext = os.path.splitext(ruta_origen)[1]
        nombre_limpio = f"Gantt_Recibida_{datetime.now().strftime('%Y%m%d%H%M%S')}_{proveedor_tarea.replace(' ', '_')}{nombre_ext}"
        ruta_final = os.path.join(carpeta_destino, nombre_limpio)
        try: shutil.copy2(ruta_origen, ruta_final)
        except Exception: ruta_final = ""
        
        if ruta_final and evento_tarea != "OFICINA | Trabajos Internos":
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("""
                        INSERT INTO facturas_recibidas (tipo_documento, numero_documento, fecha, proveedor, descripcion, evento_asociado, subtotal, impuesto, total, archivo_ruta, categoria)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (tipo_doc, nro_doc, fecha_doc, proveedor_tarea, desc_tarea, evento_tarea, subtotal, impuesto, total, ruta_final, "GENERAL / NO ASIGNADO"))
                    conn.commit()
                except Exception: pass
                finally: liberar_conexion(conn)
        return ruta_final, total

    def adjuntar_pago(self):
        ruta = filedialog.askopenfilename(title="Seleccionar Factura / Pago", filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")])
        if ruta:
            self.ruta_temp_pago = ruta
            self.btn_adjuntar_pago.configure(text="✅ Archivo Listo para Guardar", fg_color="#28a745")

    def abrir_ventana_edicion(self):
        sel = self.tabla.selection()
        if not sel:
            messagebox.showwarning("Atención", "Seleccione una tarea de la lista para editar.")
            return
        valores = self.tabla.item(sel[0], "values")
        id_tarea = valores[1]
        
        if str(id_tarea).startswith("EVT_"):
            return messagebox.showwarning("Bloqueado", "No se puede editar el Evento Principal desde aquí.\nDebes ir al módulo de Cotizaciones para modificar eventos.")
            
        v_edit = ctk.CTkToplevel(self.parent_frame.winfo_toplevel())
        v_edit.title(f"Editar Tarea ID: {id_tarea}")
        v_edit.geometry("400x600")
        v_edit.transient(self.parent_frame.winfo_toplevel())
        v_edit.grab_set()
        ctk.CTkLabel(v_edit, text="Modificar Estado o Detalles", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        f_cont = ctk.CTkFrame(v_edit, fg_color="transparent")
        f_cont.pack(fill="both", expand=True, padx=20, pady=5)
        nombre_db = valores[3]
        accion_edit = "Provisión"
        tarea_edit = nombre_db
        for acc in ["Instalación", "Desmontaje", "Provisión", "Operación", "Coordinación", "Otro", "Proveer", "Reunión", "Llamada", "Gestión", "Compra"]:
            if nombre_db.startswith(f"{acc}: "):
                accion_edit = acc
                tarea_edit = nombre_db.replace(f"{acc}: ", "", 1)
                break
        ctk.CTkLabel(f_cont, text="Tipo de Acción:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_acc = ctk.CTkComboBox(f_cont, values=["Instalación", "Desmontaje", "Provisión", "Operación", "Coordinación", "Otro", "Reunión", "Llamada", "Gestión", "Compra"], state="readonly")
        cmb_acc.pack(fill="x", pady=(0, 10))
        cmb_acc.set(accion_edit if accion_edit != "Proveer" else "Provisión")
        ctk.CTkLabel(f_cont, text="Categoría / Detalle de la Tarea:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_t = ctk.CTkEntry(f_cont)
        ent_t.pack(fill="x", pady=(0, 10))
        ent_t.insert(0, tarea_edit)
        ctk.CTkLabel(f_cont, text="Responsable Asignado:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_r = ctk.CTkEntry(f_cont)
        ent_r.pack(fill="x", pady=(0, 10))
        ent_r.insert(0, valores[4])
        ctk.CTkLabel(f_cont, text="Fecha Límite (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
        f_fecha_edit = ctk.CTkFrame(f_cont, fg_color="transparent")
        f_fecha_edit.pack(fill="x", pady=(0, 10))
        ent_f = ctk.CTkEntry(f_fecha_edit)
        ent_f.pack(side="left", fill="x", expand=True)
        ent_f.insert(0, valores[5])
        ctk.CTkButton(f_fecha_edit, text="[ 📅 ]", width=40, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(ent_f)).pack(side="right", padx=(5, 0))
        ctk.CTkLabel(f_cont, text="Estado Actual:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_e = ctk.CTkComboBox(f_cont, values=["Pendiente", "En Progreso", "Completada"], state="readonly")
        cmb_e.pack(fill="x", pady=(0, 10))
        cmb_e.set(valores[2])
        ctk.CTkLabel(f_cont, text="Tipo de Pago:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_tp = ctk.CTkComboBox(f_cont, values=["Crédito", "50% para ejecución", "100% para ejecución", "No aplica"], state="readonly")
        cmb_tp.pack(fill="x", pady=(0, 10))
        cmb_tp.set(valores[6] if len(valores) > 6 and valores[6] else "Crédito")
        btn_archivo_edit = ctk.CTkButton(f_cont, text="📎 Cargar Pago/Factura (PDF)", fg_color="#7f8c8d", hover_color="#606b6b")
        btn_archivo_edit.pack(fill="x", pady=(0, 10))
        ruta_temp_edit = {"path": ""}
        archivo_ya_cargado_db = valores[8] if len(valores) > 8 else ""
        if archivo_ya_cargado_db:
            btn_archivo_edit.configure(text="✅ Archivo Ya Cargado", fg_color="#28a745")

        def adjuntar_edit():
            ruta = filedialog.askopenfilename(title="Seleccionar Comprobante / Factura", filetypes=[("Archivos", "*.pdf;*.png;*.jpg;*.jpeg")], parent=v_edit)
            if ruta:
                ruta_temp_edit["path"] = ruta
                btn_archivo_edit.configure(text="✅ Archivo Listo para Guardar", fg_color="#28a745")

        btn_archivo_edit.configure(command=adjuntar_edit)
        ctk.CTkLabel(f_cont, text="Notas Adicionales:", font=("Arial", 11, "bold")).pack(anchor="w")
        txt_n = ctk.CTkTextbox(f_cont, height=80, border_width=1)
        txt_n.pack(fill="x", pady=(0, 10))
        txt_n.insert("1.0", valores[7] if len(valores) > 7 else "")

        def guardar_edicion():
            t_base = ent_t.get().strip()
            r = ent_r.get().strip()
            f = ent_f.get().strip()
            e = cmb_e.get()
            tp = cmb_tp.get()
            accion_select = cmb_acc.get()
            n = txt_n.get("1.0", "end-1c").strip()
            if not t_base:
                messagebox.showwarning("Atención", "La descripción de la tarea es obligatoria.", parent=v_edit)
                return
            tarea_completa = f"{accion_select}: {t_base}"
            if tp in ["50% para ejecución", "100% para ejecución"] and not ruta_temp_edit["path"] and not archivo_ya_cargado_db:
                e = "Pendiente"
                messagebox.showinfo("Aviso Bloqueo", f"El tipo de pago es '{tp}' pero no se ha adjuntado archivo. La tarea quedará forzada a 'Pendiente'.", parent=v_edit)
            ruta_final = archivo_ya_cargado_db
            if ruta_temp_edit["path"]:
                ruta_final, monto_d = self.procesar_e_insertar_factura(ruta_temp_edit["path"], r, self.combo_evento_global.get().split(" | ")[0], tarea_completa)
                if ruta_final and self.combo_evento_global.get().split(" | ")[0] != "OFICINA":
                    messagebox.showinfo("Factura Cargada", f"Se procesó y registró automáticamente en Facturas Recibidas (Compras).\nMonto detectado: S/. {monto_d:,.2f}", parent=v_edit)
            
            conn = conectar_db()
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE tareas_evento 
                    SET nombre_tarea=%s, responsable=%s, fecha_limite=%s, estado=%s, notas=%s, tipo_pago=%s, archivo_pago=%s
                    WHERE id=%s
                """, (tarea_completa, r, f, e, n, tp, ruta_final, id_tarea))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Actualizó tarea ID {id_tarea} a estado '{e}'")
                v_edit.destroy()
                self.limpiar_formulario()
                self.cargar_tareas_tabla(reset_pagina=True)
            except Exception as ex:
                messagebox.showerror("Error", str(ex), parent=v_edit)
            finally:
                liberar_conexion(conn)

        ctk.CTkButton(f_cont, text="💾 Guardar Cambios", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=guardar_edicion).pack(fill="x", pady=15)

    # =======================================================
    # 🚀 FIX: CARGA DE EVENTOS CON CACHÉ
    # =======================================================
    def cargar_eventos_aprobados(self):
        clave_cache = "lista_eventos_aprobados"
        eventos = cache_sistema.obtener(clave_cache)

        if eventos is not None:
            self._aplicar_eventos(eventos)
        else:
            self.combo_evento_global.set("Cargando eventos...")
            def tarea():
                evs = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        evs = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                        evs.insert(0, "OFICINA | Trabajos Internos")
                        cache_sistema.guardar(clave_cache, evs)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                self.parent_frame.after(0, lambda: self._aplicar_eventos(evs))

            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_eventos(self, eventos):
        if eventos:
            self.combo_evento_global.configure(values=eventos)
            self.combo_evento_global.set(eventos[0])
        else:
            self.combo_evento_global.configure(values=["Sin eventos aprobados"])
            self.combo_evento_global.set("Sin eventos aprobados")
            
        self.cargar_tareas_tabla(reset_pagina=True)

    def autocompletar_responsable(self, choice):
        if choice in self.dict_responsables:
            self.ent_responsable.delete(0, tk.END)
            self.ent_responsable.insert(0, self.dict_responsables[choice])
            if choice in self.dict_notas and self.dict_notas[choice]:
                texto_limpio = self.dict_notas[choice].replace("[B]", "").replace("[/B]", "").replace("[M]", "").replace("[/M]", "")
                self.txt_notas.delete(0, tk.END)
                self.txt_notas.insert(0, f"{texto_limpio}")

    def limpiar_formulario(self):
        self.id_tarea_seleccionada = None
        self.ent_tarea.set("")
        self.ent_responsable.delete(0, tk.END)
        self.ent_fecha.delete(0, tk.END)
        self.combo_estado.set("Pendiente")
        self.combo_tipo_pago.set("Crédito")
        self.combo_accion.set("Instalación")
        self.txt_notas.delete("1.0", tk.END)
        self.ruta_temp_pago = ""
        self.archivo_ya_cargado_db = ""
        self.btn_adjuntar_pago.configure(text="📎 Cargar Pago/Factura (PDF)", fg_color="#7f8c8d")
        self.btn_guardar.configure(text="💾 Agregar Tarea", fg_color="#1f538d", hover_color="#163b65")
        self.actualizar_sugerencias_tarea()

    def seleccionar_tarea_tabla(self, event):
        sel = self.tabla.selection()
        if not sel: return
        valores = self.tabla.item(sel[0], "values")
        self.id_tarea_seleccionada = valores[1]
        
        # Si es el Evento Principal, bloqueamos la edición
        if str(self.id_tarea_seleccionada).startswith("EVT_"):
            self.limpiar_formulario()
            self.btn_guardar.configure(text="🔒 Evento Principal (Solo Lectura)", fg_color="#7f8c8d", hover_color="#606b6b", state="disabled")
            return
            
        self.btn_guardar.configure(state="normal")
        
        self.combo_estado.set(valores[2])
        nombre_db = valores[3]
        accion_edit = "Provisión"
        tarea_edit = nombre_db
        for acc in ["Instalación", "Desmontaje", "Provisión", "Operación", "Coordinación", "Otro", "Proveer", "Reunión", "Llamada", "Gestión", "Compra"]:
            if nombre_db.startswith(f"{acc}: "):
                accion_edit = acc
                tarea_edit = nombre_db.replace(f"{acc}: ", "", 1)
                break
        self.combo_accion.set(accion_edit if accion_edit != "Proveer" else "Provisión")
        self.ent_tarea.set(tarea_edit)
        self.ent_responsable.delete(0, tk.END)
        self.ent_responsable.insert(0, valores[4])
        self.ent_fecha.delete(0, tk.END)
        self.ent_fecha.insert(0, valores[5])
        self.combo_tipo_pago.set(valores[6] if valores[6] else "Crédito")
        self.txt_notas.delete("1.0", tk.END)
        
        # Recuperar notas ocultas si las hay
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                c = conn.cursor()
                c.execute("SELECT notas FROM tareas_evento WHERE id = %s", (self.id_tarea_seleccionada,))
                n = c.fetchone()
                if n and n[0]:
                    self.txt_notas.insert("1.0", n[0])
            except: pass
            finally: liberar_conexion(conn)
            
        self.ruta_temp_pago = ""
        
        # Chequear si tiene archivo en BD
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                c = conn.cursor()
                c.execute("SELECT archivo_pago FROM tareas_evento WHERE id = %s", (self.id_tarea_seleccionada,))
                a = c.fetchone()
                self.archivo_ya_cargado_db = a[0] if a and a[0] else ""
            except: pass
            finally: liberar_conexion(conn)
            
        if self.archivo_ya_cargado_db:
            self.btn_adjuntar_pago.configure(text="✅ Archivo Ya Cargado", fg_color="#28a745")
        else:
            self.btn_adjuntar_pago.configure(text="📎 Cargar Pago/Factura (PDF)", fg_color="#7f8c8d")
        self.btn_guardar.configure(text="✏️ Guardar Cambios", fg_color="#34495e", hover_color="#2c3e50")

    def mover_tarea(self, direccion):
        sel = self.tabla.selection()
        if not sel: return
        items = self.tabla.get_children()
        idx_act = items.index(sel[0])
        if direccion == "ARRIBA" and idx_act > 0: idx_dest = idx_act - 1
        elif direccion == "ABAJO" and idx_act < len(items) - 1: idx_dest = idx_act + 1
        else: return
        
        id_act = self.tabla.item(items[idx_act], "values")[1]
        id_dest = self.tabla.item(items[idx_dest], "values")[1]
        conn = conectar_db()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT orden FROM tareas_evento WHERE id = %s", (id_act,))
            ord_act = cursor.fetchone()[0]
            cursor.execute("SELECT orden FROM tareas_evento WHERE id = %s", (id_dest,))
            ord_dest = cursor.fetchone()[0]
            cursor.execute("UPDATE tareas_evento SET orden = %s WHERE id = %s", (ord_dest, id_act))
            cursor.execute("UPDATE tareas_evento SET orden = %s WHERE id = %s", (ord_act, id_dest))
            conn.commit()
            cache_sistema.invalidar()
            self.cargar_tareas_tabla(id_a_seleccionar=id_act)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo mover la tarea:\n{e}")
        finally:
            liberar_conexion(conn)

    def guardar_tarea(self):
        evento = self.combo_evento_global.get()
        if "Sin eventos aprobados" in evento or not evento.strip() or "Cargando" in evento:
            messagebox.showwarning("Atención", "Debe seleccionar un evento válido.")
            return
            
        accion = self.combo_accion.get()
        t_base = self.ent_tarea.get().strip()
        responsable = self.ent_responsable.get().strip()
        fecha_limite = self.ent_fecha.get().strip()
        estado = self.combo_estado.get()
        tipo_pago = self.combo_tipo_pago.get()
        notas = self.txt_notas.get("1.0", "end-1c").strip()
        if not t_base:
            messagebox.showwarning("Atención", "La descripción de la tarea es obligatoria.")
            return
        if t_base.startswith(f"{accion}:"): tarea = t_base
        else: tarea = f"{accion}: {t_base}"
            
        if tipo_pago in ["50% para ejecución", "100% para ejecución"] and not self.ruta_temp_pago and not self.archivo_ya_cargado_db:
            estado = "Pendiente"
            messagebox.showinfo("Aviso Bloqueo", f"El tipo de pago es '{tipo_pago}' pero no adjuntó archivo. El estado se forzará a 'Pendiente'.")
            
        ruta_final = self.archivo_ya_cargado_db
        if self.ruta_temp_pago:
            ruta_final, monto_d = self.procesar_e_insertar_factura(self.ruta_temp_pago, responsable, evento.split(" | ")[0], tarea)
            if ruta_final and evento.split(" | ")[0] != "OFICINA":
                messagebox.showinfo("Factura Cargada", f"Archivo procesado e insertado en Facturas Recibidas automáticamente.\nMonto detectado: S/. {monto_d:,.2f}")
                
        conn = conectar_db()
        if not conn: return
        try:
            cursor = conn.cursor()
            if not self.id_tarea_seleccionada:
                cursor.execute("SELECT id FROM tareas_evento WHERE evento_asociado = %s AND nombre_tarea = %s", (evento, tarea))
                if cursor.fetchone():
                    if not messagebox.askyesno("Tarea Existente", f"Ya registraste la tarea:\n'{tarea}'\n\n¿Seguro que deseas agregarla otra vez?", parent=self.parent_frame.winfo_toplevel()):
                        liberar_conexion(conn)
                        return
            if self.id_tarea_seleccionada:
                cursor.execute("""
                    UPDATE tareas_evento 
                    SET nombre_tarea=%s, responsable=%s, fecha_limite=%s, estado=%s, notas=%s, tipo_pago=%s, archivo_pago=%s
                    WHERE id=%s
                """, (tarea, responsable, fecha_limite, estado, notas, tipo_pago, ruta_final, self.id_tarea_seleccionada))
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Actualizó la tarea '{tarea}' del evento {evento.split(' | ')[0]}")
            else:
                cursor.execute("SELECT COALESCE(MAX(orden), 0) FROM tareas_evento WHERE evento_asociado = %s", (evento,))
                nuevo_orden = cursor.fetchone()[0] + 1
                cursor.execute("""
                    INSERT INTO tareas_evento (evento_asociado, nombre_tarea, responsable, fecha_limite, estado, notas, orden, tipo_pago, archivo_pago)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (evento, tarea, responsable, fecha_limite, estado, notas, nuevo_orden, tipo_pago, ruta_final))
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Creó nueva tarea '{tarea}' para el evento {evento.split(' | ')[0]}")
            conn.commit()
            cache_sistema.invalidar()
            self.limpiar_formulario()
            self.cargar_tareas_tabla(reset_pagina=True)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar la tarea:\n{e}")
        finally:
            liberar_conexion(conn)

    # =======================================================
    # 🚀 FIX: LAZY LOADING Y CACHÉ
    # =======================================================
    def cargar_tareas_tabla(self, reset_pagina=False, id_a_seleccionar=None):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tabla.get_children(): 
            self.tabla.delete(item)
            
        evento_seleccionado = self.combo_evento_global.get()
        if "Sin eventos" in evento_seleccionado or "Cargando" in evento_seleccionado or not evento_seleccionado.strip(): return

        offset = (self.pagina_actual - 1) * self.registros_por_pagina
        clave_cache = f"tareas_evt_{evento_seleccionado}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)
        
        if datos is not None:
            self._pintar_tareas(datos, id_a_seleccionar)
        else:
            self.tabla.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", ""))
            def tarea_descarga():
                rows = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        
                        # --- 1. CARGAR TAREAS NORMALES ---
                        cursor.execute("""
                            SELECT id, estado, nombre_tarea, responsable, fecha_limite, tipo_pago, notas, archivo_pago 
                            FROM tareas_evento 
                            WHERE evento_asociado = %s 
                            ORDER BY orden ASC, id ASC
                            LIMIT %s OFFSET %s
                        """, (evento_seleccionado, self.registros_por_pagina, offset))
                        rows.extend(cursor.fetchall())
                        
                        # --- 2. CARGAR FECHA DEL EVENTO PRINCIPAL SI ES LA PRIMERA PÁGINA ---
                        if self.pagina_actual == 1 and "OFICINA" not in evento_seleccionado:
                            cod_cot = evento_seleccionado.split(" | ")[0].strip()
                            cursor.execute("SELECT id, fecha_evento, nombre_evento, status, codigo_cotizacion, nombre_empresa, locacion_evento FROM cotizaciones WHERE codigo_cotizacion = %s", (cod_cot,))
                            re_ev = cursor.fetchone()
                            if re_ev and re_ev[1]:  # Si hay fecha
                                evt_id = f"EVT_{re_ev[0]}"
                                estado = "Evento Principal"
                                nombre_tarea = f"⭐ EVENTO: {re_ev[2]}"
                                responsable = f"Cliente: {re_ev[5]}"
                                fecha_limite = re_ev[1]
                                tipo_pago = "-"
                                notas = f"Locación: {re_ev[6]}\nStatus: {re_ev[3]}"
                                archivo_pago = ""
                                
                                # Insertamos al principio de la lista
                                rows.insert(0, (evt_id, estado, nombre_tarea, responsable, fecha_limite, tipo_pago, notas, archivo_pago))
                                
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception: pass
                    finally: liberar_conexion(conn)
                    
                self.parent_frame.after(0, lambda: self._pintar_tareas(rows, id_a_seleccionar))

            threading.Thread(target=tarea_descarga, daemon=True).start()

    def _pintar_tareas(self, rows, id_a_seleccionar=None):
        for item in self.tabla.get_children(): self.tabla.delete(item)
            
        contador_visual = ((self.pagina_actual - 1) * self.registros_por_pagina) + 1
        for r in rows:
            estado_actual = r[1]
            if estado_actual == "Evento Principal":
                valores = ("★", r[0], r[1], r[2], r[3], r[4], r[5])
            else:
                valores = (contador_visual, r[0], r[1], r[2], r[3], r[4], r[5])
                contador_visual += 1
                
            self.tabla.insert("", tk.END, values=valores, tags=(estado_actual,))
            
        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")
            
        if len(rows) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")
            
        if id_a_seleccionar:
            for child in self.tabla.get_children():
                if str(self.tabla.item(child, "values")[1]) == str(id_a_seleccionar):
                    self.tabla.selection_set(child)
                    self.tabla.focus(child)
                    break
        self.actualizar_sugerencias_tarea()

    def eliminar_tarea(self):
        if not self.id_tarea_seleccionada:
            messagebox.showwarning("Atención", "Debe seleccionar una tarea de la lista primero.")
            return
            
        if str(self.id_tarea_seleccionada).startswith("EVT_"):
            messagebox.showwarning("Bloqueado", "No puedes eliminar el Evento Principal desde aquí.\nDebes ir al módulo de Cotizaciones para gestionar eventos.")
            return
            
        if messagebox.askyesno("Confirmar", "¿Desea eliminar permanentemente esta tarea del cronograma?"):
            conn = conectar_db()
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM tareas_evento WHERE id = %s", (self.id_tarea_seleccionada,))
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cronograma", f"Eliminó la tarea ID {self.id_tarea_seleccionada}")
                self.limpiar_formulario()
                self.cargar_tareas_tabla(reset_pagina=True)
            except Exception as e:
                messagebox.showerror("Error", str(e))
            finally:
                liberar_conexion(conn)


if __name__ == "__main__":
    pass