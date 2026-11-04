# -*- coding: utf-8 -*-
"""Fase 2 · selector.py: pantalla del módulo (eventos + planos)."""
import os
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
import customtkinter as ctk

from . import almacen, bd, pdf_pagina
from .modelo import Plano, Pagina, capas_por_defecto
from .editor import VentanaEditor

FAMILIA = "Helvetica" if os.sys.platform == "darwin" else "Arial"
COLORES_ESTADO = {"Aprobada": ("#d4edda", "#155724"),
                  "En desarrollo": ("#cce5ff", "#004085"),
                  "En evaluación": ("#fff3cd", "#856404"),
                  "No aprobada": ("#f8d7da", "#721c24")}


class ModuloPlanosEvento:
    """Pantalla principal de CROQUIS / PLANO DE EVENTO."""

    def __init__(self, contenedor, usuario=""):
        self.contenedor = contenedor
        self.usuario = usuario
        self.evento = None
        self.planos = []
        self._construir()
        self.cargar_eventos()

    # ---------------------------------------------------------------- interfaz
    def _construir(self):
        cab = ctk.CTkFrame(self.contenedor, fg_color="#1f538d", corner_radius=0)
        cab.pack(fill="x")
        ctk.CTkLabel(cab, text="📐 Croquis / Plano de Evento", font=(FAMILIA, 17, "bold"),
                     text_color="white").pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(cab, text="Planos de los eventos en Aprobada · En desarrollo · En evaluación",
                     font=(FAMILIA, 11), text_color="#d6eaf8").pack(side="left", padx=6)

        cuerpo = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=10, pady=8)

        # ------------- izquierda: eventos -------------
        izq = ctk.CTkFrame(cuerpo, corner_radius=10, border_width=1, border_color="#e0e0e0")
        izq.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ctk.CTkLabel(izq, text="🎪 Eventos del módulo de Cotización",
                     font=(FAMILIA, 12, "bold"), text_color="#1f538d").pack(anchor="w", padx=10, pady=(8, 4))

        f_filtros = ctk.CTkFrame(izq, fg_color="transparent")
        f_filtros.pack(fill="x", padx=10)
        self.ent_buscar = ctk.CTkEntry(f_filtros, placeholder_text="Buscar por evento, código o cliente…")
        self.ent_buscar.pack(side="left", fill="x", expand=True)
        self.ent_buscar.bind("<KeyRelease>", lambda e: self.cargar_eventos())
        self.cmb_estado = ctk.CTkOptionMenu(f_filtros, values=["Todos", "Aprobada", "En desarrollo",
                                                              "En evaluación"],
                                            width=140, command=lambda v: self.cargar_eventos())
        self.cmb_estado.pack(side="left", padx=(6, 0))
        self.cmb_estado.set("Todos")

        f_tabla = ctk.CTkFrame(izq, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=10, pady=8)
        columnas = ("codigo", "evento", "cliente", "fecha", "estado")
        self.tabla_eventos = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="browse")
        for col, titulo, ancho in (("codigo", "Código", 110), ("evento", "Evento", 240),
                                   ("cliente", "Cliente", 170), ("fecha", "Fecha", 90),
                                   ("estado", "Estado", 110)):
            self.tabla_eventos.heading(col, text=titulo)
            self.tabla_eventos.column(col, width=ancho, anchor="w" if col in ("evento", "cliente") else "center")
        for estado, (fondo, color) in COLORES_ESTADO.items():
            self.tabla_eventos.tag_configure(estado, background=fondo, foreground=color)
        self.tabla_eventos.pack(side="left", fill="both", expand=True)
        barra = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_eventos.yview)
        self.tabla_eventos.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        self.tabla_eventos.bind("<<TreeviewSelect>>", lambda e: self.cargar_planos())

        # ------------- derecha: planos -------------
        der = ctk.CTkFrame(cuerpo, corner_radius=10, border_width=1, border_color="#e0e0e0")
        der.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ctk.CTkLabel(der, text="🗂️ Planos del evento", font=(FAMILIA, 12, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=10, pady=(8, 2))
        self.lbl_evento = ctk.CTkLabel(der, text="(elija un evento)", font=(FAMILIA, 11),
                                       text_color="#555555", anchor="w", justify="left")
        self.lbl_evento.pack(fill="x", padx=10)

        columnas_p = ("nombre", "escala", "version", "modificado")
        self.tabla_planos = ttk.Treeview(der, columns=columnas_p, show="headings",
                                         selectmode="browse", height=12)
        for col, titulo, ancho in (("nombre", "Plano", 220), ("escala", "Escala", 110),
                                   ("version", "Versión", 70), ("modificado", "Modificado", 130)):
            self.tabla_planos.heading(col, text=titulo)
            self.tabla_planos.column(col, width=ancho,
                                     anchor="w" if col == "nombre" else "center")
        self.tabla_planos.pack(fill="both", expand=True, padx=10, pady=6)
        self.tabla_planos.bind("<Double-1>", lambda e: self.abrir_plano())
        # 🖱️ Clic derecho: menú con las acciones del plano
        for secuencia in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            try:
                self.tabla_planos.bind(secuencia, self._menu_contextual_planos)
            except Exception:
                pass

        botones = ctk.CTkFrame(der, fg_color="transparent")
        botones.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(botones, text="➕ Nuevo plano (PDF)", width=170, height=32,
                      font=(FAMILIA, 11, "bold"), fg_color="#27ae60", hover_color="#1e8449",
                      command=self.nuevo_plano).pack(side="left")
        ctk.CTkButton(botones, text="⬜ Lienzo en blanco", width=160, height=32,
                      font=(FAMILIA, 11, "bold"), fg_color="#16a085", hover_color="#117a65",
                      command=self.lienzo_en_blanco).pack(side="left", padx=6)
        ctk.CTkButton(botones, text="🖊️ Abrir", width=100, height=32, font=(FAMILIA, 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.abrir_plano).pack(side="left", padx=6)
        ctk.CTkButton(botones, text="📄 Ver PDF", width=110, height=32, font=(FAMILIA, 11, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=self.ver_pdf).pack(side="left", padx=6)
        ctk.CTkButton(botones, text="🗑️ Eliminar", width=110, height=32, font=(FAMILIA, 11, "bold"),
                      fg_color="#c0392b", hover_color="#922b21",
                      command=self.eliminar_plano).pack(side="left", padx=6)

        self.lbl_pie = ctk.CTkLabel(self.contenedor, text="", font=(FAMILIA, 10),
                                    text_color="#7f8c8d")
        self.lbl_pie.pack(anchor="w", padx=14, pady=(0, 6))

    # ---------------------------------------------------------------- datos
    def cargar_eventos(self):
        from .bd import cotizaciones_disponibles
        estado = self.cmb_estado.get()
        estados = ("Aprobada", "En desarrollo", "En evaluación") if estado == "Todos" else (estado,)
        eventos = cotizaciones_disponibles(estados)
        texto = (self.ent_buscar.get() or "").strip().lower()
        if texto:
            eventos = [e for e in eventos
                       if texto in str(e["evento"]).lower() or texto in str(e["cliente"]).lower()
                       or texto in str(e["codigo"]).lower()]
        self.tabla_eventos.delete(*self.tabla_eventos.get_children())
        for e in eventos:
            self.tabla_eventos.insert("", "end", iid=str(e["id"]),
                                      values=(e["codigo"], e["evento"], e["cliente"],
                                              e["fecha"], e["estado"]),
                                      tags=(e["estado"],))
        self.lbl_pie.configure(text="%d evento(s) disponibles para trabajar con planos." % len(eventos))

    def evento_seleccionado(self):
        seleccion = self.tabla_eventos.selection()
        if not seleccion:
            return None
        return bd.cotizacion_por_id(int(seleccion[0]))

    def cargar_planos(self):
        self.evento = self.evento_seleccionado()
        self.tabla_planos.delete(*self.tabla_planos.get_children())
        if not self.evento:
            self.lbl_evento.configure(text="(elija un evento)")
            return
        self.lbl_evento.configure(
            text="%s  ·  %s  ·  %s  ·  Estado: %s"
                 % (self.evento["codigo"], self.evento["evento"], self.evento["cliente"],
                    self.evento["estado"]))
        self.planos = bd.listar_planos(self.evento["id"])
        for p in self.planos:
            plano = almacen.cargar(p["id"])
            escala = "sin calibrar"
            if plano and plano.paginas and plano.paginas[0].px_por_metro:
                escala = "1 px = %.4f m" % (1.0 / plano.paginas[0].px_por_metro)
            nombre = p["nombre"]
            if not p["pdf_ruta"] or p["pdf_ruta"].strip() == "":
                nombre = "⬜ " + nombre
            self.tabla_planos.insert("", "end", iid=p["id"],
                                     values=(nombre, escala, "v%d" % int(p["version"] or 1),
                                             p["modificacion"] or p["creacion"]))

    # ---------------------------------------------------------------- acciones
    def nuevo_plano(self):
        """➕ Crea un plano. Cualquier fallo se muestra en pantalla (nunca «no pasa nada»)."""
        try:
            return self._nuevo_plano()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return messagebox.showerror(
                "Nuevo plano",
                "No se pudo crear el plano:\n\n%s: %s" % (type(e).__name__, e),
                parent=self.contenedor)

    def lienzo_en_blanco(self):
        """⬜ Crea un plano vacío (sin PDF) para dibujar desde cero."""
        try:
            return self._lienzo_en_blanco()
        except Exception as e:
            import traceback
            traceback.print_exc()
            return messagebox.showerror("Lienzo en blanco",
                                        "No se pudo crear el lienzo:\n\n%s: %s"
                                        % (type(e).__name__, e), parent=self.contenedor)

    def _lienzo_en_blanco(self):
        if not self.evento:
            return messagebox.showinfo("Planos", "Primero elija un evento de la lista.",
                                       parent=self.contenedor)
        if not bd.puede_editar(self.evento["estado"]):
            return messagebox.showwarning("Planos",
                                          "El evento está en «%s»: solo se pueden ver los planos."
                                          % self.evento["estado"], parent=self.contenedor)
        padre = self.contenedor.winfo_toplevel() if hasattr(self.contenedor, "winfo_toplevel") else self.contenedor
        datos = DialogoLienzoBlanco(padre)
        try:
            datos.wait_window()
        except Exception:
            pass
        if not getattr(datos, "resultado", None):
            return
        nombre, ancho_m, alto_m = datos.resultado
        plano = Plano(id="plano_%s" % datetime.now().strftime("%Y%m%d%H%M%S%f")[:18],
                      cotizacion_id=int(self.evento["id"]),
                      nombre=nombre, descripcion="Lienzo en blanco",
                      unidad="m", pagina=0, limpio=True,
                      creacion=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                      modificacion=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                      usuario=self.usuario)
        plano.capas = capas_por_defecto()
        plano.paginas = [Pagina(numero=1, ancho_pt=ancho_m * 28.3465,
                                alto_pt=alto_m * 28.3465,
                                px_por_metro=40.0, calibrada=True,
                                distancia_real=1.0, ancho_m=ancho_m, alto_m=alto_m)]
        try:
            almacen.guardar(plano)
            almacen.crear_version(plano)
            bd.insertar_plano(plano)
            bd.registrar_version(plano.id, plano.version,
                                 almacen.ruta_proyecto(plano.id), self.usuario,
                                 "Creación del lienzo en blanco")
        except Exception as e:
            return messagebox.showerror("Lienzo en blanco",
                                        "No se pudo guardar el lienzo:\n\n%s" % e,
                                        parent=self.contenedor)
        self.cargar_planos()
        self.abrir_plano(id_plano=plano.id)

    def _nuevo_plano(self):
        if not self.evento:
            return messagebox.showinfo("Planos", "Primero elija un evento de la lista.",
                                       parent=self.contenedor)
        if not bd.puede_editar(self.evento["estado"]):
            return messagebox.showwarning("Planos",
                                          "El evento está en «%s»: solo se pueden ver los planos."
                                          % self.evento["estado"], parent=self.contenedor)
        ruta = filedialog.askopenfilename(
            title="Elegir el plano en PDF", parent=self.contenedor,
            filetypes=[("PDF", "*.pdf"), ("Todos los archivos", "*.*")])
        if not ruta:
            return
        if not pdf_pagina.disponible():
            return messagebox.showerror("Planos", "Falta PyMuPDF para leer el PDF.",
                                        parent=self.contenedor)
        try:
            paginas = pdf_pagina.info_paginas(ruta)
        except Exception as e:
            return messagebox.showerror("Planos", "No se pudo abrir el PDF:\n%s" % e,
                                        parent=self.contenedor)
        if not paginas:
            return messagebox.showerror("Planos", "El PDF no tiene páginas.", parent=self.contenedor)

        padre_dialogo = self.contenedor.winfo_toplevel() if hasattr(self.contenedor, "winfo_toplevel") else self.contenedor
        datos = DialogoNuevoPlano(padre_dialogo, paginas)
        # ⏳ Se ESPERA la respuesta del diálogo (antes no se esperaba y «Crear»
        #    no hacía nada). El módulo no es un widget: se usa el propio diálogo.
        try:
            datos.wait_window()
        except Exception as e:
            print("[Planos] no se pudo esperar el diálogo:", e)
        if not getattr(datos, "resultado", None):
            return
        nombre, numero_pagina = datos.resultado

        plano = Plano(id="plano_%s" % datetime.now().strftime("%Y%m%d%H%M%S%f")[:18],
                      cotizacion_id=int(self.evento["id"]),
                      nombre=nombre,
                      descripcion="",
                      unidad="m",
                      pagina=numero_pagina - 1,
                      creacion=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                      modificacion=datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                      usuario=self.usuario)
        plano.capas = capas_por_defecto()
        info = paginas[numero_pagina - 1]
        plano.paginas = [Pagina(numero=numero_pagina, ancho_pt=info["ancho"],
                                alto_pt=info["alto"])]
        try:
            plano.pdf_ruta = almacen.copiar_pdf_original(ruta, plano.id)
            almacen.guardar(plano)                    # guardado inicial (v1)
            almacen.crear_version(plano)              # copia de seguridad de la v1
        except Exception as e:
            return messagebox.showerror("Planos", "No se pudo guardar el proyecto:\n%s" % e,
                                        parent=self.contenedor)
        if not bd.insertar_plano(plano):
            return messagebox.showerror("Planos", "No se pudo registrar el plano en la base.",
                                        parent=self.contenedor)
        bd.registrar_version(plano.id, plano.version, almacen.ruta_proyecto(plano.id),
                             self.usuario, "Creación del plano")
        self.cargar_planos()
        messagebox.showinfo("Planos",
                            "Plano creado: %s\n\nPágina %d · %d x %d pt\n\n%s"
                            % (nombre, numero_pagina, round(info["ancho"]), round(info["alto"]),
                               "Contiene vectores (se conservará la calidad)." if info["vectorial"]
                               else "Es un escaneo (imagen): se podrá dibujar encima."),
                            parent=self.contenedor)
        self.abrir_plano(id_plano=plano.id)

    def _menu_contextual_planos(self, evento):
        """🖱️ Menú del botón derecho sobre la lista de planos."""
        fila = self.tabla_planos.identify_row(evento.y)
        if not fila:
            return
        self.tabla_planos.selection_set(fila)
        tiene_pdf = os.path.isfile(almacen.ruta_pdf_original(fila))
        menu = tk.Menu(self.contenedor, tearoff=0, font=(FAMILIA, 11))
        menu.add_command(label="🖊️  Abrir el plano", command=lambda: self.abrir_plano(id_plano=fila))
        menu.add_command(label="✏️  Renombrar…", command=lambda: self.renombrar_plano(fila))
        menu.add_separator()
        if tiene_pdf:
            menu.add_command(label="📄  Ver PDF original", command=lambda: self.ver_pdf(fila))
        else:
            menu.add_command(label="📄  Ver PDF original (no tiene)", state="disabled")
        menu.add_command(label="📊  Exportar a PDF…", command=lambda: self._exportar_plano(fila, "pdf"))
        menu.add_command(label="🖼️  Exportar a PNG…", command=lambda: self._exportar_plano(fila, "png"))
        menu.add_separator()
        menu.add_command(label="🗑️  Eliminar plano…", command=lambda: self.eliminar_plano(fila))
        try:
            menu.tk_popup(evento.x_root, evento.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _exportar_plano(self, id_plano, formato):
        """Exporta el plano elegido desde la lista (PDF o PNG)."""
        plano = almacen.cargar(id_plano)
        if not plano:
            return messagebox.showerror("Exportar", "No se encontró el proyecto del plano.",
                                        parent=self.contenedor)
        from tkinter import filedialog
        from . import exportar
        if formato == "pdf":
            ruta = filedialog.asksaveasfilename(
                parent=self.contenedor, title="Exportar plano a PDF", defaultextension=".pdf",
                initialfile="%s.pdf" % plano.nombre.replace(" ", "_"),
                filetypes=[("PDF", "*.pdf")])
        else:
            ruta = filedialog.asksaveasfilename(
                parent=self.contenedor, title="Exportar plano a PNG", defaultextension=".png",
                initialfile="%s.png" % plano.nombre.replace(" ", "_"),
                filetypes=[("Imagen PNG", "*.png")])
        if not ruta:
            return
        try:
            if formato == "pdf":
                exportar.exportar_pdf(plano, ruta)
            else:
                exportar.exportar_png(plano, ruta, dpi=200)
        except Exception as e:
            return messagebox.showerror("Exportar", "No se pudo exportar:\n\n%s" % e,
                                        parent=self.contenedor)
        messagebox.showinfo("Exportar", "Archivo generado:\n\n%s" % ruta, parent=self.contenedor)

    def renombrar_plano(self, id_plano):
        """✏️ Cambia el nombre del plano (proyecto + base de datos)."""
        plano = almacen.cargar(id_plano)
        if not plano:
            return messagebox.showerror("Renombrar", "No se encontró el proyecto del plano.",
                                        parent=self.contenedor)
        nuevo = DialogoRenombrar(self.contenedor, plano.nombre).pedir()
        if not nuevo or nuevo == plano.nombre:
            return
        plano.nombre = nuevo
        try:
            almacen.guardar(plano)
            bd.actualizar_plano(plano)
        except Exception as e:
            return messagebox.showerror("Renombrar", "No se pudo guardar:\n\n%s" % e,
                                        parent=self.contenedor)
        self.cargar_planos()

    def plano_seleccionado(self):
        seleccion = self.tabla_planos.selection()
        return seleccion[0] if seleccion else None

    def abrir_plano(self, id_plano=None):
        try:
            return self._abrir_plano(id_plano)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return messagebox.showerror("Abrir plano",
                                        "No se pudo abrir el plano:\n\n%s: %s"
                                        % (type(e).__name__, e), parent=self.contenedor)

    def _abrir_plano(self, id_plano=None):
        id_plano = id_plano or self.plano_seleccionado()
        if not id_plano:
            return messagebox.showinfo("Planos", "Elija un plano de la lista.",
                                       parent=self.contenedor)
        plano = almacen.cargar(id_plano)
        if not plano:
            return messagebox.showerror("Planos", "No se encontró el proyecto del plano.",
                                        parent=self.contenedor)
        # ⬜ Los lienzos en blanco no tienen PDF: se abren igual.
        if not plano.limpio:
            if not os.path.isfile(almacen.ruta_pdf_original(id_plano)) \
                    and not os.path.isfile(plano.pdf_ruta):
                return messagebox.showerror("Planos", "Falta el PDF original del plano.",
                                            parent=self.contenedor)
        padre = self.contenedor.winfo_toplevel() if hasattr(self.contenedor, "winfo_toplevel") else self.contenedor
        ventana = VentanaEditor(padre, plano, self.evento or {}, self.usuario)
        try:
            ventana.lift()
            ventana.focus_force()
            ventana.attributes("-topmost", True)
            ventana.after(400, lambda: ventana.attributes("-topmost", False))
        except Exception:
            pass
        return ventana

    def ver_pdf(self, id_plano=None):
        id_plano = id_plano or self.plano_seleccionado()
        if not id_plano:
            return messagebox.showinfo("Planos", "Elija un plano de la lista.",
                                       parent=self.contenedor)
        from visor_documentos import ver_documento_ventana
        ruta = almacen.ruta_pdf_original(id_plano)
        if not os.path.isfile(ruta):
            plano = almacen.cargar(id_plano)
            ruta = plano.pdf_ruta if plano else ""
        ver_documento_ventana(ruta, "PDF original del plano", self.contenedor)

    def eliminar_plano(self, id_plano=None):
        id_plano = id_plano or self.plano_seleccionado()
        if not id_plano:
            return messagebox.showinfo("Planos", "Elija un plano de la lista.",
                                       parent=self.contenedor)
        if not messagebox.askyesno("Eliminar plano",
                                   "¿Eliminar este plano y su proyecto editable?\n\n"
                                   "El PDF original guardado también se borrará.",
                                   parent=self.contenedor):
            return
        bd.borrar_plano(id_plano)
        try:
            import shutil
            shutil.rmtree(almacen.carpeta_plano(id_plano), ignore_errors=True)
        except Exception:
            pass
        self.cargar_planos()


class DialogoRenombrar(ctk.CTkToplevel):
    """✏️ Pide un nombre nuevo para el plano."""

    def __init__(self, parent, nombre_actual):
        super().__init__(parent)
        self.valor = None
        self.title("✏️ Renombrar plano")
        try:
            self.geometry("440x190")
            self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
            self.update_idletasks()
            x = (self.winfo_screenwidth() - 440) // 2
            y = (self.winfo_screenheight() - 190) // 3
            self.geometry("440x190+%d+%d" % (max(0, x), max(0, y)))
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(self, text="✏️ Nuevo nombre del plano", font=(FAMILIA, 13, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=16, pady=(16, 6))
        self.entrada = ctk.CTkEntry(self, font=(FAMILIA, 12))
        self.entrada.pack(fill="x", padx=16)
        self.entrada.insert(0, nombre_actual or "")
        self.entrada.select_range(0, "end")
        self.entrada.focus_set()
        self.entrada.bind("<Return>", lambda e: self._aceptar())
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(botones, text="✔ Guardar", width=140, height=30,
                      font=(FAMILIA, 11, "bold"), fg_color="#27ae60", hover_color="#1e8449",
                      command=self._aceptar).pack(side="left")
        ctk.CTkButton(botones, text="✖ Cancelar", width=140, height=30,
                      font=(FAMILIA, 11, "bold"), fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="right")

    def _aceptar(self):
        texto = (self.entrada.get() or "").strip()
        if texto:
            self.valor = texto
        self.destroy()

    def pedir(self):
        self.wait_window()
        return self.valor


class DialogoLienzoBlanco(ctk.CTkToplevel):
    """⬜ Pide nombre y medidas de la hoja (en metros) para el lienzo vacío."""

    def __init__(self, parent):
        super().__init__(parent)
        self.resultado = None
        self.title("⬜ Lienzo en blanco")
        try:
            self.geometry("480x330")
            self.transient(parent)
            self.update_idletasks()
            x = (self.winfo_screenwidth() - 480) // 2
            y = (self.winfo_screenheight() - 330) // 3
            self.geometry("480x330+%d+%d" % (max(0, x), max(0, y)))
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(self, text="⬜ Nuevo plano en blanco", font=(FAMILIA, 14, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(self, text="Sin PDF: se dibuja desde cero con muros, puertas y mobiliario.",
                     font=(FAMILIA, 10), text_color="#7f8c8d").pack(anchor="w", padx=16, pady=(0, 10))
        ctk.CTkLabel(self, text="Nombre del plano:", font=(FAMILIA, 11, "bold")).pack(anchor="w", padx=16)
        self.ent_nombre = ctk.CTkEntry(self, placeholder_text="Ej.: Plano general")
        self.ent_nombre.pack(fill="x", padx=16, pady=(2, 10))
        self.ent_nombre.insert(0, "Plano general")
        fila = ctk.CTkFrame(self, fg_color="transparent")
        fila.pack(fill="x", padx=16)
        ctk.CTkLabel(fila, text="Ancho (m)", font=(FAMILIA, 11, "bold")).pack(side="left")
        self.ent_ancho = ctk.CTkEntry(fila, width=80)
        self.ent_ancho.pack(side="left", padx=(6, 16))
        self.ent_ancho.insert(0, "20")
        ctk.CTkLabel(fila, text="Alto (m)", font=(FAMILIA, 11, "bold")).pack(side="left")
        self.ent_alto = ctk.CTkEntry(fila, width=80)
        self.ent_alto.pack(side="left", padx=(6, 0))
        self.ent_alto.insert(0, "15")
        ctk.CTkLabel(self, text="medidas de la hoja de trabajo (se puede cambiar luego)",
                     font=(FAMILIA, 9), text_color="#7f8c8d").pack(anchor="w", padx=16, pady=(2, 0))
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.pack(fill="x", padx=16, pady=(16, 12))
        ctk.CTkButton(botones, text="✔ Crear", height=32, width=150,
                      font=(FAMILIA, 12, "bold"), fg_color="#16a085", hover_color="#117a65",
                      command=self._crear).pack(side="left")
        ctk.CTkButton(botones, text="✖ Cancelar", height=32, width=150,
                      font=(FAMILIA, 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="right")

    def _crear(self):
        nombre = (self.ent_nombre.get() or "").strip() or "Plano general"
        try:
            ancho = max(2.0, min(200.0, float(str(self.ent_ancho.get()).replace(",", "."))))
        except Exception:
            ancho = 20.0
        try:
            alto = max(2.0, min(200.0, float(str(self.ent_alto.get()).replace(",", "."))))
        except Exception:
            alto = 15.0
        self.resultado = (nombre, ancho, alto)
        self.destroy()


class DialogoNuevoPlano(ctk.CTkToplevel):
    """Pide nombre del plano y qué página del PDF se va a editar."""

    def __init__(self, parent, paginas):
        super().__init__(parent)
        self.resultado = None
        self.title("➕ Nuevo plano")
        try:
            self.geometry("480x310")
            self.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
            self.update_idletasks()
            x = (self.winfo_screenwidth() - 480) // 2
            y = (self.winfo_screenheight() - 310) // 3
            self.geometry("480x310+%d+%d" % (max(0, x), max(0, y)))
            self.grab_set()
            self.lift()
            self.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(self, text="➕ Nuevo plano a partir del PDF", font=(FAMILIA, 14, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=16, pady=(14, 6))
        ctk.CTkLabel(self, text="Nombre del plano:", font=(FAMILIA, 11, "bold")).pack(anchor="w", padx=16)
        self.ent_nombre = ctk.CTkEntry(self, placeholder_text="Ej.: Plano general")
        self.ent_nombre.pack(fill="x", padx=16, pady=(2, 10))
        self.ent_nombre.insert(0, "Plano general")
        ctk.CTkLabel(self, text="Página a editar:", font=(FAMILIA, 11, "bold")).pack(anchor="w", padx=16)
        valores = ["%d · %.0f x %.0f pt%s" % (p["numero"], p["ancho"], p["alto"],
                                             " · vectorial" if p["vectorial"] else " · escaneado")
                   for p in paginas]
        self.cmb_pagina = ctk.CTkOptionMenu(self, values=valores, width=420)
        self.cmb_pagina.pack(fill="x", padx=16, pady=(2, 14))
        self.cmb_pagina.set(valores[0])
        fila = ctk.CTkFrame(self, fg_color="transparent")
        fila.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkButton(fila, text="✔ Crear", width=140, height=34, font=(FAMILIA, 12, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self._crear).pack(side="left")
        ctk.CTkButton(fila, text="✖ Cancelar", width=140, height=34, font=(FAMILIA, 12, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="right")

    def _crear(self):
        nombre = (self.ent_nombre.get() or "").strip() or "Plano"
        try:
            numero = int(self.cmb_pagina.get().split("·")[0].strip())
        except Exception:
            numero = 1
        self.resultado = (nombre, numero)
        self.destroy()
