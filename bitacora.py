# -*- coding: utf-8 -*-
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from conexion import conectar_db

class BitacoraApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.crear_interfaz()
        self.cargar_registros()

    def crear_interfaz(self):
        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)

        ctk.CTkLabel(self.frame_main, text="📜 BITÁCORA DE AUDITORÍA (Historial de Movimientos)", font=("Arial", 18, "bold"), text_color="#a93226").pack(anchor="w", pady=(0, 15))

        f_tabla = ctk.CTkFrame(self.frame_main, corner_radius=10)
        f_tabla.pack(fill="both", expand=True)

        columnas = ("id", "fecha", "hora", "usuario", "modulo", "accion")
        style = ttk.Style()
        style.configure("Treeview", rowheight=28, font=("Arial", 10))
        style.configure("Treeview.Heading", font=("Arial", 11, "bold"))
        
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", style="Treeview")
        self.tabla.heading("id", text="ID")
        self.tabla.heading("fecha", text="Fecha")
        self.tabla.heading("hora", text="Hora")
        self.tabla.heading("usuario", text="Usuario")
        self.tabla.heading("modulo", text="Módulo")
        self.tabla.heading("accion", text="Acción Realizada")

        self.tabla.column("id", width=50, anchor="center")
        self.tabla.column("fecha", width=100, anchor="center")
        self.tabla.column("hora", width=100, anchor="center")
        self.tabla.column("usuario", width=120, anchor="center")
        self.tabla.column("modulo", width=180, anchor="w")
        self.tabla.column("accion", width=450, anchor="w")

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        
        self.tabla.pack(side="left", fill="both", expand=True, padx=(10,0), pady=10)
        scroll_y.pack(side="right", fill="y", padx=(0,10), pady=10)

        f_btn = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        f_btn.pack(fill="x", pady=(15, 0))
        ctk.CTkButton(f_btn, text="🔄 Actualizar Historial", font=("Arial", 12, "bold"), command=self.cargar_registros).pack(side="right")

    def cargar_registros(self):
        for item in self.tabla.get_children(): self.tabla.delete(item)
        conn = conectar_db()
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, fecha, hora, usuario, modulo, accion FROM bitacora_auditoria ORDER BY id DESC LIMIT 500")
            for r in cursor.fetchall():
                self.tabla.insert("", tk.END, values=r)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cargar la bitácora:\n{e}")
        finally:
            conn.close()

if __name__ == "__main__":
    pass