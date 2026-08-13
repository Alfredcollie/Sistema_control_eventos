# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
import os
import sys  
import json
import subprocess 
import webbrowser
import threading
import customtkinter as ctk
from conexion import conectar_db

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA: Función universal para abrir archivos
# =========================================================
def abrir_documento(ruta):
    try:
        if sys.platform == "win32":
            os.startfile(ruta)
        elif sys.platform == "darwin": # macOS
            subprocess.call(["open", ruta])
        else: # Linux
            subprocess.call(["xdg-open", ruta])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")

# =========================================================
# 🚀 MOTOR DE CONFIGURACIÓN REGIONAL
# =========================================================
def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00"
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

class LibroMayorApp:
    def __init__(self, root):
        self.root = root
        self.pantalla_expandida = False
        
        try:
            self.root.configure(fg_color="#f8f9fa")
        except Exception:
            try: self.root.configure(bg="#f8f9fa")
            except Exception: pass
            
        if hasattr(self.root, 'title'):
            self.root.title("📊 Libro Mayor - Black Cube")
        if hasattr(self.root, 'geometry') and isinstance(self.root, (tk.Tk, tk.Toplevel)):
            self.root.geometry("900x600")
            self.root.update_idletasks()
            ancho = self.root.winfo_width()
            alto = self.root.winfo_height()
            x = (self.root.winfo_screenwidth() // 2) - (ancho // 2)
            y = (self.root.winfo_screenheight() // 2) - (alto // 2)
            self.root.geometry(f"{ancho}x{alto}+{x}+{y}")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=30, font=("Arial", 11))
        style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000", font=("Arial", 11, "bold"), bordercolor="#e0e0e0", borderwidth=1, relief="flat")
        
        # 🚀 HEADER CON BOTÓN DE PANTALLA COMPLETA
        header_frame = ctk.CTkFrame(self.root, fg_color="transparent")
        header_frame.pack(fill="x", padx=15, pady=(15, 5))
        
        ctk.CTkLabel(header_frame, text="LIBRO MAYOR ACUMULADO POR CATEGORÍAS", font=("Arial", 16, "bold"), text_color="#1f538d").pack(side="left")
        self.btn_pantalla = ctk.CTkButton(header_frame, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="right")

        frame_principal = tk.Frame(root, bg="#f8f9fa")
        frame_principal.pack(fill="both", expand=True, padx=15, pady=5)
        
        # 🚀 PANEL LATERAL DE ACCIONES
        frame_acciones = tk.LabelFrame(frame_principal, text=" Panel de Acciones ", font=("Arial", 11, "bold"), fg="#34495e", bg="#f8f9fa", padx=10, pady=10)
        frame_acciones.pack(side="right", fill="y", padx=(15, 0))
        
        btn_refresh = ctk.CTkButton(frame_acciones, text="🔄 Actualizar Tabla", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.cargar_datos_mayor)
        btn_refresh.pack(fill="x", pady=(10, 5))

        btn_export = ctk.CTkButton(frame_acciones, text="📊 Exportar Excel", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=self.exportar_excel)
        btn_export.pack(fill="x", pady=5)

        # 🚀 TABLA PRINCIPAL
        frame_tabla = tk.Frame(frame_principal, bg="#f8f9fa")
        frame_tabla.pack(side="left", fill="both", expand=True)

        columnas = ("categoria", "debe", "haber", "saldo")
        self.tabla = ttk.Treeview(frame_tabla, columns=columnas, show="headings", style="Treeview")
        
        self.tabla.heading("categoria", text="Cuenta / Categoría Suministro")
        self.tabla.heading("debe", text="Cargos Acumulados (Debe)")
        self.tabla.heading("haber", text="Abonos Acumulados (Haber)")
        self.tabla.heading("saldo", text="Saldo Neto Final")
        
        self.tabla.column("categoria", width=300, anchor="w")
        self.tabla.column("debe", width=180, anchor="e")
        self.tabla.column("haber", width=180, anchor="e")
        self.tabla.column("saldo", width=180, anchor="e")
        
        scr_y = ttk.Scrollbar(frame_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scr_y.set)
        
        self.tabla.pack(side="left", fill="both", expand=True)
        scr_y.pack(side="right", fill="y")
        
        # 🚀 TOTALES INFERIORES
        self.frame_totales = tk.Frame(root, bg="#f8f9fa")
        self.frame_totales.pack(fill="x", side="bottom", pady=(10, 15))
        
        self.lbl_total_debe = tk.Label(self.frame_totales, text="Total Debe: 0.00", font=("Arial", 14, "bold"), fg="#27ae60", bg="#f8f9fa")
        self.lbl_total_debe.pack(side="left", padx=30)
        
        self.lbl_saldo_neto = tk.Label(self.frame_totales, text="Saldo Neto General: 0.00", font=("Arial", 16, "bold"), fg="#1f538d", bg="#f8f9fa")
        self.lbl_saldo_neto.pack(side="left", expand=True)
        
        self.lbl_total_haber = tk.Label(self.frame_totales, text="Total Haber: 0.00", font=("Arial", 14, "bold"), fg="#c0392b", bg="#f8f9fa")
        self.lbl_total_haber.pack(side="right", padx=30)

        if hasattr(self.root, 'after'):
            self.root.after(100, self.cargar_datos_mayor)
        else:
            self.cargar_datos_mayor()

    def toggle_pantalla_completa(self):
        sidebar = None
        try:
            if self.root.master:
                for child in self.root.master.winfo_children():
                    if hasattr(child, "cget") and child.cget("width") == 280:
                        sidebar = child
                        break
        except Exception: pass

        if getattr(self, "pantalla_expandida", False):
            if sidebar: sidebar.pack(side="left", fill="y", before=self.root)
            self.btn_pantalla.configure(text="[ + ] Pantalla Completa", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = False
        else:
            if sidebar: sidebar.pack_forget()
            self.btn_pantalla.configure(text="[ - ] Restaurar Vista", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = True

    def exportar_excel(self):
        try:
            import pandas as pd
        except ImportError:
            return messagebox.showerror("Librería Faltante", "Instala pandas: pip install pandas openpyxl")
            
        filas = []
        for item in self.tabla.get_children():
            vals = self.tabla.item(item)["values"]
            filas.append([vals[0], vals[1], vals[2], vals[3]])

        if not filas: return messagebox.showwarning("Aviso", "No hay datos para exportar.")
        
        cols = ["Cuenta / Categoría", "Cargos Acumulados (Debe)", "Abonos Acumulados (Haber)", "Saldo Neto Final"]
        
        ruta = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile="Libro_Mayor_Acumulado.xlsx", filetypes=[("Excel", "*.xlsx")])
        if ruta:
            try:
                pd.DataFrame(filas, columns=cols).to_excel(ruta, index=False)
                messagebox.showinfo("Éxito", f"Reporte exportado exitosamente a:\n{ruta}")
                abrir_documento(ruta)
            except Exception as e:
                messagebox.showerror("Error", f"Fallo al guardar:\n{e}")

    # =======================================================
    # CARGA DEL MAYOR EN SEGUNDO PLANO (HILO + TOKEN)
    # =======================================================
    def cargar_datos_mayor(self):
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
            
        self._mayor_token = getattr(self, "_mayor_token", 0) + 1
        token = self._mayor_token

        def tarea():
            agrupado = {}
            conn = conectar_db(silencioso=True)
            if not conn:
                self.root.after(0, lambda: messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nEl Libro Mayor no se puede cargar."))
                return
                
            try:
                cursor = conn.cursor()
                
                # 1. Ingresos (COBRO CLIENTE)
                cursor.execute("SELECT monto_pagado FROM pagos_clientes")
                for r in cursor.fetchall():
                    monto = float(r[0]) if r[0] else 0.0
                    cat = "COBRO CLIENTE"
                    if cat not in agrupado: agrupado[cat] = {"debe": 0.0, "haber": 0.0}
                    agrupado[cat]["debe"] += monto
                    
                # 2. Egresos (PROVEEDORES por categoría)
                cursor.execute("SELECT categoria_suministro, monto_pagado FROM pagos_comprobantes")
                for r in cursor.fetchall():
                    cat = str(r[0]).strip().upper() if r[0] and str(r[0]).strip() else "GENERAL / NO ASIGNADO"
                    monto = float(r[1]) if r[1] else 0.0
                    if cat not in agrupado: agrupado[cat] = {"debe": 0.0, "haber": 0.0}
                    agrupado[cat]["haber"] += monto

                # 3. Anulaciones y Notas de Crédito (Auditoría visual acumulada)
                cursor.execute("""
                    SELECT total, COALESCE(det_monto, 0)
                    FROM facturas_emitidas
                    WHERE estado_sunat LIKE '%Anulada%'
                """)
                for r in cursor.fetchall():
                    tot_bruto = float(r[0]) if r[0] else 0.0
                    det_monto = float(r[1]) if r[1] else 0.0
                    neto = tot_bruto - det_monto
                    
                    cat_rev = "ANULACIÓN DE INGRESOS (REVERSO)"
                    cat_nc = "NOTAS DE CRÉDITO EMITIDAS"
                    
                    if cat_rev not in agrupado: agrupado[cat_rev] = {"debe": 0.0, "haber": 0.0}
                    if cat_nc not in agrupado: agrupado[cat_nc] = {"debe": 0.0, "haber": 0.0}
                    
                    agrupado[cat_rev]["debe"] += neto   # Reverso visual
                    agrupado[cat_nc]["haber"] += neto   # Salida contable virtual
                    
            except Exception as e:
                self.root.after(0, lambda err=e: messagebox.showerror("Error de Base de Datos", f"No se pudo compilar el mayor:\n{str(err)}"))
            finally:
                conn.close()
                
            lista_ordenada = sorted(agrupado.items(), key=lambda x: x[0])
            self.root.after(0, lambda t=token, l=lista_ordenada: self._pintar_mayor(t, l))

        threading.Thread(target=tarea, daemon=True).start()

    def _pintar_mayor(self, token, lista_ordenada):
        if token != getattr(self, "_mayor_token", 0):
            return
            
        total_gral_debe = 0.0
        total_gral_haber = 0.0
        
        for cat, montos in lista_ordenada:
            debe = montos["debe"]
            haber = montos["haber"]
            
            if debe == 0.0 and haber == 0.0:
                continue
                
            saldo = debe - haber
            
            str_debe = formatear_moneda(debe) if debe > 0 else "-"
            str_haber = formatear_moneda(haber) if haber > 0 else "-"
            str_saldo = formatear_moneda(saldo)
            
            self.tabla.insert("", tk.END, values=(cat, str_debe, str_haber, str_saldo))
            
            total_gral_debe += debe
            total_gral_haber += haber
            
        self.lbl_total_debe.config(text=f"Total Debe: {formatear_moneda(total_gral_debe)}")
        self.lbl_total_haber.config(text=f"Total Haber: {formatear_moneda(total_gral_haber)}")
        
        saldo_neto_gral = total_gral_debe - total_gral_haber
        color_saldo = "#27ae60" if saldo_neto_gral >= 0 else "#c0392b"
        self.lbl_saldo_neto.config(text=f"Saldo Neto General: {formatear_moneda(saldo_neto_gral)}", fg=color_saldo)

if __name__ == "__main__":
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use("clam")
    app = LibroMayorApp(root)
    root.mainloop()