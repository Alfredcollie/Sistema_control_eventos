# -*- coding: utf-8 -*-
"""Fase 3 · editor completo: biblioteca + arrastrar/soltar + propiedades + calibración."""
import os
import math
import tkinter as tk
from tkinter import messagebox, colorchooser
import customtkinter as ctk

try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_OK = True
except Exception:
    PIL_OK = False

try:
    import fitz                       # PyMuPDF (multiplataforma: Windows / macOS / Linux)
except Exception:                     # si falta, el editor lo avisa y no se cae
    fitz = None

from . import almacen, bd, biblioteca, exportar, geometria, simbolos
from .ayuda import ayudar
from .modelo import Elemento, Pagina

import sys

ES_MAC = sys.platform == "darwin"


def maximizar_ventana(ventana):
    """Maximiza una ventana en Windows, macOS y Linux (con alternativa segura)."""
    try:
        ventana.update_idletasks()
    except Exception:
        pass
    # macOS
    if ES_MAC:
        try:
            ventana.state("zoomed")
            return
        except Exception:
            pass
        try:
            ventana.attributes("-zoomed", True)
            return
        except Exception:
            pass
    # Windows / Linux
    try:
        ventana.state("zoomed")
        return
    except Exception:
        pass
    try:
        ventana.wm_state("zoomed")
        return
    except Exception:
        pass
    # último recurso: ocupar la pantalla
    try:
        ancho = ventana.winfo_screenwidth()
        alto = ventana.winfo_screenheight()
        ventana.geometry("%dx%d+0+0" % (ancho, alto))
    except Exception:
        pass
FAMILIA = "Helvetica" if ES_MAC else "Arial"
COLOR_SEL = "#e67e22"

# escala por defecto si el plano no está calibrado: 1 metro = 40 px de imagen
ESCALA_POR_DEFECTO = 40.0


class VentanaEditor(ctk.CTkToplevel):
    """🖊️ Editor visual del plano: biblioteca, objetos editables y propiedades."""

    def __init__(self, parent, plano, cotizacion=None, usuario=""):
        super().__init__(parent)
        self.plano = plano
        self.cotizacion = cotizacion or {}
        self.usuario = usuario
        self.zoom = 1.0
        self.cuadricula = True
        self._imagen_tk = None
        self._cache_imagen = {}
        self._arrastre = None
        self._pendiente = None          # símbolo elegido en la biblioteca
        self._seleccion = []
        self._ids_dibujo = {}
        self._ids_seleccion = []
        self._modo = "seleccion"
        self._punto_calibracion = []
        self._historico = []
        self._futuro = []
        self._sin_guardar = False
        self._ruta_pdf = ""
        self._pagina = plano.pagina or 0
        self.escala = ESCALA_POR_DEFECTO

        if plano.paginas and plano.paginas[0].px_por_metro:
            self.escala = float(plano.paginas[0].px_por_metro)

        self.title("🖊️ %s — %s" % (plano.nombre, self.cotizacion.get("evento", "")))
        # 🪟 NO se usa 'transient': en Windows eso quita el botón de MINIMIZAR.
        #    El editor es una ventana independiente, como las de los demás módulos.
        try:
            self.resizable(True, True)
        except Exception:
            pass
        maximizar_ventana(self)          # 🖥️ el editor abre MAXIMIZADO
        self.protocol("WM_DELETE_WINDOW", self.cerrar)
        self._construir()
        self.cargar_pagina(self._pagina)
        self.redibujar()
        self._actualizar_estado()
        # 🔍 Al abrir, el plano completo entra en pantalla
        self.after(250, self._ajustar_al_abrir)
        try:                                  # que aparezca al frente al abrirse
            self.lift()
            self.attributes("-topmost", True)
            self.after(400, lambda: self.attributes("-topmost", False))
            self.focus_force()
        except Exception:
            pass

    # ================================================================ LAYOUT
    def _construir(self):
        cab = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        cab.pack(fill="x")
        ctk.CTkLabel(cab, text="🖊️ %s" % self.plano.nombre, font=(FAMILIA, 14, "bold"),
                     text_color="white").pack(side="left", padx=14, pady=8)
        # 🪟 Botones de la ventana (minimizar · maximizar/restaurar · cerrar)
        ctk.CTkButton(cab, text="✕", width=34, height=26, font=(FAMILIA, 13, "bold"),
                      fg_color="#c0392b", hover_color="#922b21",
                      command=self.cerrar).pack(side="right", padx=(2, 10), pady=6)
        self.btn_maximizar = ctk.CTkButton(cab, text="🗗", width=34, height=26,
                                           font=(FAMILIA, 12, "bold"),
                                           fg_color="#1f538d", hover_color="#163b65",
                                           command=self.alternar_maximizado)
        self.btn_maximizar.pack(side="right", padx=2, pady=6)
        ctk.CTkButton(cab, text="🗕", width=34, height=26, font=(FAMILIA, 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.minimizar).pack(side="right", padx=2, pady=6)
        self.lbl_guardado = ctk.CTkLabel(cab, text="✔ Guardado", font=(FAMILIA, 11, "bold"),
                                         text_color="#a9dfbf")
        self.lbl_guardado.pack(side="right", padx=14)

        barra = ctk.CTkFrame(self, fg_color="#eaf2f8", corner_radius=0)
        barra.pack(fill="x")
        botones = (
            ("💾 Guardar", self.guardar, "#27ae60", "#1e8449"),
            ("↶ Deshacer", self.deshacer, "#5d6d7e", "#4a5967"),
            ("↷ Rehacer", self.rehacer, "#5d6d7e", "#4a5967"),
            ("📐 Calibrar escala", self.iniciar_calibracion, "#8e44ad", "#703688"),
            ("⊞ Cuadrícula", self.alternar_cuadricula, "#34495e", "#2c3e50"),
            ("⤢ Ajustar", self.ajustar_pantalla, "#1f538d", "#163b65"),
            ("1:1", self.zoom_cien, "#1f538d", "#163b65"),
            ("➖", lambda: self.cambiar_zoom(1 / 1.25), "#1f538d", "#163b65"),
            ("➕", lambda: self.cambiar_zoom(1.25), "#1f538d", "#163b65"),
            ("📏 Medir", self.iniciar_medicion, "#b9770e", "#9c640c"),
            ("👥 Agrupar", self.agrupar, "#6c3483", "#512e5f"),
            ("👤 Desagrupar", self.desagrupar, "#7d3c98", "#5b2c6f"),
            ("📄 PDF", self.exportar_pdf, "#c0392b", "#922b21"),
            ("🖼️ PNG", self.exportar_png, "#c0392b", "#922b21"),
        )
        for texto, comando, color, hover in botones:
            ctk.CTkButton(barra, text=texto, width=118 if len(texto) > 4 else 48, height=28,
                          font=(FAMILIA, 11, "bold"), fg_color=color, hover_color=hover,
                          command=comando).pack(side="left", padx=3, pady=4)
        self.lbl_modo = ctk.CTkLabel(barra, text="", font=(FAMILIA, 10, "bold"),
                                     text_color="#c0392b")
        self.lbl_modo.pack(side="right", padx=10)

        barra2 = ctk.CTkFrame(self, fg_color="#f4f6f7", corner_radius=0)
        barra2.pack(fill="x")
        # ---------- Alinear (respeta el borde elegido) ----------
        ctk.CTkLabel(barra2, text="Alinear:", font=(FAMILIA, 10, "bold"),
                     text_color="#555555").pack(side="left", padx=(8, 4))
        alineaciones = (
            ("⇤ Izq", "izquierda", "Alinear a la IZQUIERDA: todos los objetos pegados al borde izquierdo del que está más a la izquierda."),
            ("⇔ Centro", "centro_h", "Alinear al CENTRO horizontal: todos centrados en la misma línea vertical (promedio)."),
            ("⇥ Der", "derecha", "Alinear a la DERECHA: todos pegados al borde derecho del que está más a la derecha."),
            ("⇧ Arriba", "arriba", "Alinear ARRIBA: todos pegados al borde superior del que está más arriba."),
            ("⇕ Medio", "centro_v", "Alinear al MEDIO vertical: todos centrados en la misma línea horizontal (promedio)."),
            ("⇩ Abajo", "abajo", "Alinear ABAJO: todos pegados al borde inferior del que está más abajo."),
        )
        for texto, modo, explicacion in alineaciones:
            boton = ctk.CTkButton(barra2, text=texto, width=76, height=24, font=(FAMILIA, 10),
                                  fg_color="#5d6d7e", hover_color="#4a5967",
                                  command=lambda m=modo: self.alinear(m))
            boton.pack(side="left", padx=2, pady=3)
            ayudar(boton, explicacion)

        # ---------- Centrar en el plano ----------
        ctk.CTkLabel(barra2, text=" Centrar en el plano:", font=(FAMILIA, 10, "bold"),
                     text_color="#555555").pack(side="left", padx=(8, 4))
        for texto, horizontal, vertical, explicacion in (
                ("↔", True, False, "Centra los objetos seleccionados en el MEDIO de la hoja (horizontal)."),
                ("↕", False, True, "Centra los objetos seleccionados en el MEDIO de la hoja (vertical)."),
                ("⦿ Ambos", True, True, "Centra los objetos en el CENTRO de la hoja (horizontal y vertical a la vez).")):
            boton = ctk.CTkButton(barra2, text=texto, width=58 if len(texto) < 3 else 74, height=24,
                                  font=(FAMILIA, 10), fg_color="#1a5276", hover_color="#154360",
                                  command=lambda h=horizontal, v=vertical: self.centrar_en_plano(h, v))
            boton.pack(side="left", padx=2, pady=3)
            ayudar(boton, explicacion)

        # ---------- Igualar tamaños ----------
        ctk.CTkLabel(barra2, text=" Igualar tamaño:", font=(FAMILIA, 10, "bold"),
                     text_color="#555555").pack(side="left", padx=(8, 4))
        for texto, horizontal, explicacion in (
                ("↔ Ancho", True, "Iguala el ANCHO de todos los objetos al del primero seleccionado."),
                ("↕ Alto", False, "Iguala el ALTO (profundidad) de todos los objetos al del primero seleccionado.")):
            boton = ctk.CTkButton(barra2, text=texto, width=74, height=24, font=(FAMILIA, 10),
                                  fg_color="#4d5656", hover_color="#3b4444",
                                  command=lambda h=horizontal: self.igualar_tamano(h))
            boton.pack(side="left", padx=2, pady=3)
            ayudar(boton, explicacion)

        # ---------- Repartir (igual separación) ----------
        ctk.CTkLabel(barra2, text=" Repartir:", font=(FAMILIA, 10, "bold"),
                     text_color="#555555").pack(side="left", padx=(8, 4))
        for texto, horizontal, explicacion in (
                ("↔ H", True, "REPARTIR HORIZONTAL (3+ objetos): el primero y el último no se mueven; "
                              "los del medio se colocan dejando la MISMA separación entre ellos."),
                ("↕ V", False, "REPARTIR VERTICAL (3+ objetos): el de arriba y el de abajo no se mueven; "
                               "los del medio quedan con la MISMA separación entre ellos.")):
            boton = ctk.CTkButton(barra2, text=texto, width=52, height=24, font=(FAMILIA, 10),
                                  fg_color="#6e2c00", hover_color="#5a2400",
                                  command=lambda h=horizontal: self.distribuir(h))
            boton.pack(side="left", padx=2, pady=3)
            ayudar(boton, explicacion)
        ctk.CTkButton(barra2, text="🗂️ Versiones", width=106, height=24, font=(FAMILIA, 10, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=self.ver_versiones).pack(side="right", padx=8, pady=3)
        self.lbl_aviso = ctk.CTkLabel(barra2, text="", font=(FAMILIA, 10, "bold"),
                                      text_color="#b9770e")
        self.lbl_aviso.pack(side="right", padx=8)

        cuerpo = ctk.CTkFrame(self, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True)

        # ---------------- izquierda: biblioteca ----------------
        izq = ctk.CTkFrame(cuerpo, width=250, corner_radius=0)
        izq.pack(side="left", fill="y")
        izq.pack_propagate(False)
        ctk.CTkLabel(izq, text="📚 Biblioteca", font=(FAMILIA, 12, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=10, pady=(8, 2))
        self.ent_buscar = ctk.CTkEntry(izq, placeholder_text="Buscar objeto…", height=26)
        self.ent_buscar.pack(fill="x", padx=8, pady=(0, 4))
        self.ent_buscar.bind("<KeyRelease>", lambda e: self._llenar_biblioteca())
        self.lista_biblioteca = ctk.CTkScrollableFrame(izq, fg_color="transparent")
        self.lista_biblioteca.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        ctk.CTkLabel(izq, text="Clic en un objeto y luego\nclic en el plano para colocarlo.",
                     font=(FAMILIA, 9), text_color="#7f8c8d", justify="left").pack(
            anchor="w", padx=10, pady=(0, 8))
        self._llenar_biblioteca()

        # ---------------- centro: lienzo ----------------
        centro = ctk.CTkFrame(cuerpo, fg_color="transparent")
        centro.pack(side="left", fill="both", expand=True)
        self.lienzo = tk.Canvas(centro, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#d5dbdb", cursor="crosshair")
        self.lienzo.pack(fill="both", expand=True, padx=4, pady=4)
        self.lienzo.bind("<Button-1>", self._click)
        self.lienzo.bind("<B1-Motion>", self._arrastrar)
        self.lienzo.bind("<ButtonRelease-1>", self._soltar)
        # 🖱️ Desplazar el plano: botón central (Windows/Linux) o derecho (MacBook)
        self.lienzo.bind("<ButtonPress-2>", self._iniciar_pan)
        self.lienzo.bind("<B2-Motion>", self._pan)
        self.lienzo.bind("<ButtonRelease-2>", self._soltar)
        self.lienzo.bind("<ButtonPress-3>", self._iniciar_pan)
        self.lienzo.bind("<B3-Motion>", self._pan)
        self.lienzo.bind("<ButtonRelease-3>", self._soltar)
        self.lienzo.bind("<MouseWheel>", self._rueda)
        self.lienzo.bind("<Button-4>", lambda e: self.cambiar_zoom(1.1))
        self.lienzo.bind("<Button-5>", lambda e: self.cambiar_zoom(1 / 1.1))
        self.lienzo.bind("<Motion>", self._mover_cursor)
        atajos = [("<Delete>", self.eliminar), ("<BackSpace>", self.eliminar),
                  ("<Control-s>", lambda e: self.guardar()),
                  ("<Control-z>", lambda e: self.deshacer()),
                  ("<Control-y>", lambda e: self.rehacer()),
                  ("<Control-d>", lambda e: self.duplicar()),
                  ("<Control-c>", lambda e: self.copiar()),
                  ("<Control-v>", lambda e: self.pegar()),
                  ("<Control-a>", lambda e: self.seleccionar_todo()),
                  # 🍎 macOS: los mismos atajos con la tecla Command
                  ("<Command-s>", lambda e: self.guardar()),
                  ("<Command-z>", lambda e: self.deshacer()),
                  ("<Command-Shift-Z>", lambda e: self.rehacer()),
                  ("<Command-d>", lambda e: self.duplicar()),
                  ("<Command-c>", lambda e: self.copiar()),
                  ("<Command-v>", lambda e: self.pegar()),
                  ("<Command-a>", lambda e: self.seleccionar_todo())]
        for tecla, accion in atajos + [
                              ("<Escape>", lambda e: self.cancelar()),
                              ("<Left>", lambda e: self._mover_con_flechas(-1, 0)),
                              ("<Right>", lambda e: self._mover_con_flechas(1, 0)),
                              ("<Up>", lambda e: self._mover_con_flechas(0, -1)),
                  ("<Down>", lambda e: self._mover_con_flechas(0, 1))]:
            self.bind(tecla, accion)

        # ---------------- derecha: propiedades ----------------
        der = ctk.CTkFrame(cuerpo, width=268, corner_radius=0)
        der.pack(side="right", fill="y")
        der.pack_propagate(False)
        # 🗂️ Pestañas: Propiedades y Capas (así entra todo en pantalla)
        self.pestanas = ctk.CTkTabview(der, width=246, height=700,
                                       fg_color="#f4f6f7",
                                       segmented_button_selected_color="#1f538d",
                                       segmented_button_selected_hover_color="#163b65")
        self.pestanas.pack(fill="both", expand=True, padx=4, pady=(6, 4))
        tab_props = self.pestanas.add("⚙️ Propiedades")
        tab_capas = self.pestanas.add("🗂️ Capas")
        self.lienzo_props = ctk.CTkScrollableFrame(tab_props, fg_color="transparent")
        self.lienzo_props.pack(fill="both", expand=True)
        contenedor_props = self.lienzo_props
        self.props = {}
        for clave, etiqueta in (("nombre", "Nombre"), ("ancho", "Ancho (m)"),
                                ("alto", "Profundidad (m)"), ("x", "Posición X (m)"),
                                ("y", "Posición Y (m)"), ("rotacion", "Rotación (°)")):
            fila = ctk.CTkFrame(contenedor_props, fg_color="transparent")
            fila.pack(fill="x", padx=6, pady=1)
            ctk.CTkLabel(fila, text=etiqueta, width=110, anchor="w",
                         font=(FAMILIA, 10, "bold")).pack(side="left")
            entrada = ctk.CTkEntry(fila, height=24, font=(FAMILIA, 10))
            entrada.pack(side="left", fill="x", expand=True)
            entrada.bind("<Return>", lambda e: self.aplicar_propiedades())
            entrada.bind("<FocusOut>", lambda e: self.aplicar_propiedades())
            self.props[clave] = entrada

        # ----- texto del nombre: posición, tamaño y color -----
        fila_pos = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_pos.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_pos, text="Nombre en", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.cmb_texto_pos = ctk.CTkOptionMenu(
            fila_pos, values=["Abajo", "Arriba", "Dentro", "Izquierda", "Derecha"],
            width=120, height=24, font=(FAMILIA, 10),
            command=lambda v: self.aplicar_propiedades())
        self.cmb_texto_pos.pack(side="left", fill="x", expand=True)

        fila_tam = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_tam.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_tam, text="Tamaño texto", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.sld_texto_tam = ctk.CTkSlider(fila_tam, from_=6, to=48, width=90,
                                           command=lambda v: self.aplicar_propiedades())
        self.sld_texto_tam.set(9)
        self.sld_texto_tam.pack(side="left")
        self.lbl_texto_tam = ctk.CTkLabel(fila_tam, text="9", width=24,
                                          font=(FAMILIA, 10, "bold"))
        self.lbl_texto_tam.pack(side="left")

        fila_tcol = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_tcol.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_tcol, text="Color texto", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.btn_color_texto = ctk.CTkButton(fila_tcol, text="   ", width=60, height=24,
                                             fg_color="#555555",
                                             command=self.elegir_color_texto)
        self.btn_color_texto.pack(side="left")

        fila_capa = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_capa.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_capa, text="Capa", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.cmb_capa = ctk.CTkOptionMenu(fila_capa, values=[c.nombre for c in self.plano.capas],
                                          width=120, height=24, font=(FAMILIA, 10),
                                          command=lambda v: self.aplicar_propiedades())
        self.cmb_capa.pack(side="left", fill="x", expand=True)

        fila_color = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_color.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_color, text="Color", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.btn_color = ctk.CTkButton(fila_color, text="   ", width=60, height=24,
                                       fg_color="#1f538d", command=self.elegir_color)
        self.btn_color.pack(side="left")

        fila_op = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        fila_op.pack(fill="x", padx=6, pady=2)
        ctk.CTkLabel(fila_op, text="Opacidad", width=110, anchor="w",
                     font=(FAMILIA, 10, "bold")).pack(side="left")
        self.sld_opacidad = ctk.CTkSlider(fila_op, from_=10, to=100, width=110,
                                          command=lambda v: self.aplicar_propiedades())
        self.sld_opacidad.set(100)
        self.sld_opacidad.pack(side="left")

        self.var_bloqueado = tk.BooleanVar(value=False)
        self.var_visible = tk.BooleanVar(value=True)
        for texto, variable, comando in (("🔒 Bloqueado", self.var_bloqueado, self.aplicar_propiedades),
                                         ("👁 Visible", self.var_visible, self.aplicar_propiedades)):
            ctk.CTkCheckBox(contenedor_props, text=texto, variable=variable, font=(FAMILIA, 10),
                            checkbox_width=16, checkbox_height=16,
                            command=comando).pack(anchor="w", padx=12, pady=2)

        f_acc = ctk.CTkFrame(contenedor_props, fg_color="transparent")
        f_acc.pack(fill="x", padx=6, pady=(8, 4))
        for texto, comando, color in (("⧉ Duplicar", self.duplicar, "#1f538d"),
                                      ("🗑️ Eliminar", self.eliminar, "#c0392b")):
            ctk.CTkButton(f_acc, text=texto, width=105, height=26, font=(FAMILIA, 10, "bold"),
                          fg_color=color, hover_color="#2c3e50",
                          command=comando).pack(side="left", padx=2)
        self.lbl_escala = ctk.CTkLabel(contenedor_props, text="", font=(FAMILIA, 9), text_color="#7f8c8d",
                                       justify="left")
        self.lbl_escala.pack(anchor="w", padx=10, pady=(6, 4))

        # ---------------- capas (en su propia pestaña) ----------------
        ctk.CTkLabel(tab_capas, text="Mostrar · Bloquear · Orden", font=(FAMILIA, 10),
                     text_color="#7f8c8d").pack(anchor="w", padx=6, pady=(4, 2))
        self.lista_capas = ctk.CTkScrollableFrame(tab_capas, fg_color="transparent",
                                                  height=420)
        self.lista_capas.pack(fill="both", expand=True, padx=2, pady=(0, 4))
        self._llenar_capas()

        # 📐 La lista de capas crece con la ventana (no queda recortada)
        def _ajustar_alto_capas(_evento=None):
            try:
                alto = max(160, tab_capas.winfo_height() - 46)
                self.lista_capas.configure(height=alto)
            except Exception:
                pass
        tab_capas.bind("<Configure>", _ajustar_alto_capas)
        self.after(300, _ajustar_alto_capas)
        ctk.CTkLabel(tab_capas, text="👁 ver/ocultar   🔒 bloquear   ▲▼ mover",
                     font=(FAMILIA, 9), text_color="#7f8c8d",
                     justify="left").pack(anchor="w", padx=6, pady=(0, 6))

        # ---------------- estado ----------------
        estado = ctk.CTkFrame(self, fg_color="#f2f3f4", corner_radius=0)
        estado.pack(fill="x", side="bottom")
        self.lbl_estado = ctk.CTkLabel(estado, text="", font=(FAMILIA, 10, "bold"),
                                       text_color="#2c3e50")
        self.lbl_estado.pack(side="left", padx=10, pady=3)
        self.lbl_coords = ctk.CTkLabel(estado, text="X: 0.00 m   Y: 0.00 m",
                                       font=(FAMILIA, 10), text_color="#555555")
        self.lbl_coords.pack(side="right", padx=10)

    # ================================================================ VENTANA
    def minimizar(self):
        """🗕 Minimiza el editor."""
        try:
            self.iconify()
        except Exception:
            try:
                self.state("iconic")
            except Exception:
                pass

    def alternar_maximizado(self):
        """🗗 Maximiza o restaura el editor."""
        try:
            actual = self.state()
        except Exception:
            actual = "normal"
        if actual == "zoomed":
            try:
                self.state("normal")
            except Exception:
                pass
            try:
                self.geometry("1400x840")
            except Exception:
                pass
            try:
                self.btn_maximizar.configure(text="🗖")
            except Exception:
                pass
        else:
            maximizar_ventana(self)
            try:
                self.btn_maximizar.configure(text="🗗")
            except Exception:
                pass

    # ================================================================ BIBLIOTECA
    def _llenar_biblioteca(self):
        for hijo in self.lista_biblioteca.winfo_children():
            hijo.destroy()
        objetos = biblioteca.buscar(self.ent_buscar.get())
        categoria_actual = None
        for objeto in objetos:
            if objeto["categoria"] != categoria_actual:
                categoria_actual = objeto["categoria"]
                ctk.CTkLabel(self.lista_biblioteca, text=categoria_actual,
                             font=(FAMILIA, 10, "bold"), text_color="#1f538d").pack(
                    anchor="w", pady=(6, 1))
            ctk.CTkButton(self.lista_biblioteca, text="▭  " + objeto["nombre"], height=22,
                          font=(FAMILIA, 10), anchor="w", fg_color="#f2f3f4",
                          hover_color="#d6eaf8", text_color="#2c3e50",
                          command=lambda o=objeto: self.elegir_objeto(o)).pack(fill="x", pady=1)

    def elegir_objeto(self, objeto):
        self._pendiente = objeto
        self._modo = "colocar"
        self.lbl_modo.configure(text="Colocando: %s — clic en el plano" % objeto["nombre"])

    def _colocar_objeto(self, x_px, y_px):
        objeto = self._pendiente
        self._pendiente = None
        self._modo = "seleccion"
        self.lbl_modo.configure(text="")
        if not objeto:
            return
        capa = self._capa_para_categoria(objeto["categoria"])
        elemento = Elemento(tipo=objeto["tipo"], nombre=objeto["nombre"], capa=capa.id,
                            x=self._px_a_metros(x_px), y=self._px_a_metros(y_px),
                            ancho=objeto["ancho"], alto=objeto["alto"],
                            color=objeto["color"], orden=len(self.plano.elementos))
        if self.cuadricula:
            elemento.x = geometria.snap(elemento.x, 0.05)
            elemento.y = geometria.snap(elemento.y, 0.05)
        self._snapshot()
        self.plano.elementos.append(elemento)
        self._seleccion = [elemento.id]
        self.redibujar()
        self._mostrar_propiedades()
        self._avisar_superposiciones()
        self.marcar_sin_guardar()

    def _capa_para_categoria(self, categoria):
        equivalencias = {"Mobiliario": "Mobiliario", "Escenarios y estructuras": "Escenario y estructuras",
                         "Audiovisuales": "Audiovisuales", "Decoración": "Decoración",
                         "Logística y circulación": "Circulación",
                         "Formas básicas": "Textos y cotas"}
        buscada = equivalencias.get(categoria, "Mobiliario")
        for capa in self.plano.capas:
            if capa.nombre == buscada:
                return capa
        return self.plano.capas[0] if self.plano.capas else None

    # ================================================================ PDF / ZOOM
    def cargar_pagina(self, numero):
        self._ruta_pdf = almacen.ruta_pdf_original(self.plano.id)
        if not os.path.isfile(self._ruta_pdf):
            self._ruta_pdf = self.plano.pdf_ruta
        self._pagina = max(0, int(numero or 0))
        # ⬜ Lienzo en blanco: no hay PDF, se dibuja una hoja vacía con cuadrícula
        self._limpio = bool(self.plano.limpio) or not (
            self._ruta_pdf and os.path.isfile(self._ruta_pdf))
        self._renderizar()

    def _hoja_en_blanco(self):
        """Genera la imagen de la hoja vacía (metros x escala) para poder editarla."""
        pagina = self.plano.paginas[0] if self.plano.paginas else None
        ancho_m = getattr(pagina, "ancho_m", 0) or 20.0
        alto_m = getattr(pagina, "alto_m", 0) or 15.0
        ancho_px = max(60, int(ancho_m * self.escala * self.zoom))
        alto_px = max(60, int(alto_m * self.escala * self.zoom))
        if not PIL_OK:
            return None
        imagen = Image.new("RGB", (ancho_px, alto_px), "#ffffff")
        dibujo = ImageDraw.Draw(imagen)
        # borde de la hoja
        dibujo.rectangle([0, 0, ancho_px - 1, alto_px - 1], outline="#2c3e50", width=2)
        # cuadrícula de 1 metro y de 5 metros
        paso = self.escala * self.zoom
        for i in range(1, int(ancho_m) + 1):
            x = i * paso
            color = "#d5dbdb" if i % 5 == 0 else "#eef2f4"
            dibujo.line([(x, 0), (x, alto_px)], fill=color, width=1)
        for j in range(1, int(alto_m) + 1):
            y = j * paso
            color = "#d5dbdb" if j % 5 == 0 else "#eef2f4"
            dibujo.line([(0, y), (ancho_px, y)], fill=color, width=1)
        return ImageTk.PhotoImage(imagen)

    def _renderizar(self):
        if getattr(self, "_limpio", False):
            imagen = self._hoja_en_blanco()
            self.lienzo.delete("fondo")
            self.lienzo.delete("cuadricula")
            if imagen is None:
                self.lienzo.create_text(20, 20, anchor="nw", fill="#c0392b", tags="fondo",
                                        text="⚠️ Falta Pillow para dibujar la hoja.")
                return
            self._imagen_tk = imagen
            self.lienzo.create_image(0, 0, anchor="nw", image=self._imagen_tk, tags="fondo")
            self.lienzo.configure(scrollregion=(0, 0, imagen.width(), imagen.height()))
            self.lienzo.tag_lower("fondo")
            self.redibujar()
            return
        if fitz is None:
            self.lienzo.delete("fondo")
            self.lienzo.create_text(20, 20, anchor="nw", fill="#c0392b", tags="fondo",
                                    text="⚠️ Falta PyMuPDF (pip install PyMuPDF) para mostrar el plano.")
            return
        if not PIL_OK or not self._ruta_pdf or not os.path.isfile(self._ruta_pdf):
            self.lienzo.create_text(20, 20, anchor="nw", fill="#c0392b", tags="fondo",
                                    text="⚠️ No se encontró el PDF original del plano.")
            return
        clave = round(self.zoom, 2)
        if clave not in self._cache_imagen:
            with fitz.open(self._ruta_pdf) as documento:
                pagina = documento[min(self._pagina, documento.page_count - 1)]
                base = 150 / 72.0
                pix = pagina.get_pixmap(matrix=fitz.Matrix(base, base), alpha=False)
                imagen = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if abs(self.zoom - 1.0) > 0.001:
                imagen = imagen.resize((max(1, int(imagen.width * self.zoom)),
                                        max(1, int(imagen.height * self.zoom))), Image.LANCZOS)
            self._cache_imagen[clave] = ImageTk.PhotoImage(imagen)
        self._imagen_tk = self._cache_imagen[clave]
        self.lienzo.delete("fondo")
        self.lienzo.delete("cuadricula")
        self.lienzo.create_image(0, 0, anchor="nw", image=self._imagen_tk, tags="fondo")
        self.lienzo.configure(scrollregion=(0, 0, self._imagen_tk.width(), self._imagen_tk.height()))
        if self.cuadricula and not getattr(self, "_limpio", False):
            self._dibujar_cuadricula()
        self.lienzo.tag_lower("fondo")
        self.redibujar()

    def _dibujar_cuadricula(self):
        if not self._imagen_tk:
            return
        ancho, alto = self._imagen_tk.width(), self._imagen_tk.height()
        paso = max(10.0, self.escala * 0.5 * self.zoom)
        x = 0.0
        while x < ancho:
            self.lienzo.create_line(x, 0, x, alto, fill="#e8eef3", tags="cuadricula")
            x += paso
        y = 0.0
        while y < alto:
            self.lienzo.create_line(0, y, ancho, y, fill="#e8eef3", tags="cuadricula")
            y += paso
        self.lienzo.tag_lower("cuadricula")
        self.lienzo.tag_lower("fondo")

    def cambiar_zoom(self, factor):
        nuevo = max(0.15, min(6.0, self.zoom * factor))
        if abs(nuevo - self.zoom) < 0.001:
            return
        self.zoom = nuevo
        self._renderizar()

    def _ajustar_al_abrir(self):
        """Comprueba que el plano quepa en la ventana (una sola vez al abrir)."""
        try:
            self.update_idletasks()
            if not self._imagen_tk:
                return
            ancho_vista = max(200, self.lienzo.winfo_width())
            alto_vista = max(200, self.lienzo.winfo_height())
            if self._imagen_tk.width() > ancho_vista or self._imagen_tk.height() > alto_vista:
                self.ajustar_pantalla()
            else:
                self.lienzo.xview_moveto(0.0)
                self.lienzo.yview_moveto(0.0)
        except Exception:
            pass

    def zoom_cien(self):
        self.zoom = 1.0
        self._renderizar()

    def ajustar_pantalla(self):
        self.update_idletasks()
        if not self._imagen_tk:
            return
        factor = min(self.lienzo.winfo_width() / self._imagen_tk.width(),
                     self.lienzo.winfo_height() / self._imagen_tk.height())
        self.zoom = max(0.15, min(6.0, self.zoom * factor * 0.97))
        self._renderizar()

    def alternar_cuadricula(self):
        self.cuadricula = not self.cuadricula
        self._renderizar()

    def _rueda(self, evento):
        self.cambiar_zoom(1.1 if evento.delta > 0 else 1 / 1.1)

    def _iniciar_pan(self, evento):
        self._arrastre = ("pan", evento.x, evento.y,
                          self.lienzo.canvasx(0), self.lienzo.canvasy(0))
        self.lienzo.configure(cursor="fleur")

    def _pan(self, evento):
        if not self._arrastre or self._arrastre[0] != "pan":
            return
        _m, x0, y0, cx0, cy0 = self._arrastre
        bbox = self.lienzo.bbox("all") or (0, 0, 1, 1)
        ancho, alto = max(1, bbox[2]), max(1, bbox[3])
        self.lienzo.xview_moveto(max(0.0, (cx0 - (evento.x - x0)) / ancho))
        self.lienzo.yview_moveto(max(0.0, (cy0 - (evento.y - y0)) / alto))

    # ================================================================ CONVERSIÓN
    def _metros_a_px(self, metros):
        return float(metros) * self.escala * self.zoom

    def _px_a_metros(self, px):
        if not self.escala:
            return 0.0
        return float(px) / (self.escala * self.zoom)

    def _esquinas_px(self, elemento):
        """Las 4 esquinas del objeto YA rotadas (para marco y tiradores)."""
        cx = self._metros_a_px(elemento.x)
        cy = self._metros_a_px(elemento.y)
        ancho = max(2.0, self._metros_a_px(elemento.ancho))
        alto = max(2.0, self._metros_a_px(elemento.alto))
        hw, hh = ancho / 2.0, alto / 2.0
        esquinas = [(cx - hw, cy - hh), (cx + hw, cy - hh),
                    (cx + hw, cy + hh), (cx - hw, cy + hh)]
        if elemento.rotacion:
            esquinas = [geometria.rotar_punto(x, y, cx, cy, elemento.rotacion)
                        for x, y in esquinas]
        return esquinas, (cx, cy)

    def _caja_px(self, elemento):
        cx = self._metros_a_px(elemento.x)
        cy = self._metros_a_px(elemento.y)
        ancho = max(2.0, self._metros_a_px(elemento.ancho))
        alto = max(2.0, self._metros_a_px(elemento.alto))
        return cx - ancho / 2.0, cy - alto / 2.0, cx + ancho / 2.0, cy + alto / 2.0

    # ================================================================ DIBUJO
    def redibujar(self):
        for ids in self._ids_dibujo.values():
            for i in ids:
                self.lienzo.delete(i)
        self._ids_dibujo = {}
        for elemento in self.plano.elementos:
            capa = self.plano.capa(elemento.capa)
            if capa and not capa.visible:
                continue
            if not elemento.visible:
                continue
            caja = self._caja_px(elemento)
            tags = ("elem", "el_" + elemento.id)
            ids = simbolos.dibujar(self.lienzo, elemento, caja, tags, zoom=self.zoom)
            if elemento.rotacion:
                self._rotar_items(ids, caja, elemento.rotacion)
                self._reubicar_etiqueta(elemento, caja, ids)
            self._ids_dibujo[elemento.id] = ids
        self._dibujar_seleccion()
        self.lienzo.tag_raise("seleccion")

    def _reubicar_etiqueta(self, elemento, caja, ids):
        """Coloca el nombre según la posición elegida, respetando la rotación."""
        if not elemento.nombre:
            return
        x0, y0, x1, y1 = caja
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        ancho, alto = x1 - x0, y1 - y0
        margen = 6.0
        posicion = (elemento.texto_pos or "abajo").lower()
        if posicion == "arriba":
            desfase = (0.0, -(alto / 2.0 + margen))
        elif posicion == "dentro":
            desfase = (0.0, 0.0)
        elif posicion == "izquierda":
            desfase = (-(ancho / 2.0 + margen), 0.0)
        elif posicion == "derecha":
            desfase = ((ancho / 2.0 + margen), 0.0)
        else:
            desfase = (0.0, alto / 2.0 + margen)
        if elemento.rotacion:
            desfase = geometria.rotar_punto(desfase[0], desfase[1], 0, 0, elemento.rotacion)
        nueva_x, nueva_y = cx + desfase[0], cy + desfase[1]
        for item in ids:
            try:
                if self.lienzo.type(item) == "text" and \
                        self.lienzo.itemcget(item, "text") == elemento.nombre:
                    self.lienzo.coords(item, nueva_x, nueva_y)
            except Exception:
                pass

    def _rotar_items(self, ids, caja, grados):
        cx, cy = (caja[0] + caja[2]) / 2.0, (caja[1] + caja[3]) / 2.0
        for i in ids:
            try:
                tipo = self.lienzo.type(i)
            except Exception:
                continue
            coords = list(self.lienzo.coords(i))
            if tipo == "rectangle":
                x0, y0, x1, y1 = coords
                puntos = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                puntos = [geometria.rotar_punto(x, y, cx, cy, grados) for x, y in puntos]
                plano = []
                for x, y in puntos:
                    plano.extend([x, y])
                self.lienzo.delete(i)
                nuevo = self.lienzo.create_polygon(*plano, outline=self._color_de(i),
                                                   fill=self.lienzo.itemcget(i, "fill") or "",
                                                   width=1.5)
                ids[ids.index(i)] = nuevo
            elif tipo == "oval":
                x0, y0, x1, y1 = coords
                cx_o, cy_o = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                rx, ry = (x1 - x0) / 2.0, (y1 - y0) / 2.0
                puntos = []
                for paso in range(24):
                    ang = 2 * math.pi * paso / 24.0
                    px, py = cx_o + rx * math.cos(ang), cy_o + ry * math.sin(ang)
                    px, py = geometria.rotar_punto(px, py, cx, cy, grados)
                    puntos.extend([px, py])
                color = self.lienzo.itemcget(i, "outline")
                self.lienzo.delete(i)
                nuevo = self.lienzo.create_polygon(*puntos, outline=color, width=1.5)
                ids[ids.index(i)] = nuevo
            elif tipo in ("line", "polygon"):
                nuevo = []
                for j in range(0, len(coords) - 1, 2):
                    x, y = geometria.rotar_punto(coords[j], coords[j + 1], cx, cy, grados)
                    nuevo.extend([x, y])
                self.lienzo.coords(i, *nuevo)
            elif tipo == "text":
                x, y = coords
                self.lienzo.coords(i, *geometria.rotar_punto(x, y, cx, cy, grados))

    def _color_de(self, item):
        try:
            return self.lienzo.itemcget(item, "outline")
        except Exception:
            return "#1f538d"

    def _dibujar_seleccion(self):
        for i in self._ids_seleccion:
            self.lienzo.delete(i)
        self._ids_seleccion = []
        for elemento in self._elementos_seleccionados():
            esquinas, (cx, cy) = self._esquinas_px(elemento)
            # marco que sigue la rotación del objeto
            puntos = []
            for px, py in esquinas:
                puntos.extend([px, py])
            self._ids_seleccion.append(self.lienzo.create_polygon(
                *puntos, outline=COLOR_SEL, width=1.5, dash=(4, 2), fill="",
                tags="seleccion"))
            # tiradores en las esquinas giradas
            for hx, hy in esquinas:
                self._ids_seleccion.append(self.lienzo.create_rectangle(
                    hx - 4, hy - 4, hx + 4, hy + 4, fill="#ffffff", outline=COLOR_SEL,
                    tags=("seleccion", "handle")))
            # tirador de rotación sobre el lado superior (también girado)
            medio_x = (esquinas[0][0] + esquinas[1][0]) / 2.0
            medio_y = (esquinas[0][1] + esquinas[1][1]) / 2.0
            dx, dy = medio_x - cx, medio_y - cy
            largo = max(1.0, (dx * dx + dy * dy) ** 0.5)
            fuera_x = medio_x + dx / largo * 20.0
            fuera_y = medio_y + dy / largo * 20.0
            self._ids_seleccion.append(self.lienzo.create_line(
                medio_x, medio_y, fuera_x, fuera_y, fill=COLOR_SEL, tags="seleccion"))
            self._ids_seleccion.append(self.lienzo.create_oval(
                fuera_x - 5, fuera_y - 5, fuera_x + 5, fuera_y + 5,
                fill=COLOR_SEL, outline=COLOR_SEL, tags=("seleccion", "rotate")))

    def _elementos_seleccionados(self):
        return [e for e in self.plano.elementos if e.id in self._seleccion]

    # ================================================================ INTERACCIÓN
    def _click(self, evento):
        x = self.lienzo.canvasx(evento.x)
        y = self.lienzo.canvasy(evento.y)
        if self._modo == "calibrar":
            return self._click_calibracion(x, y)
        if self._modo == "medir":
            return self._click_medicion(x, y)
        if self._pendiente:
            return self._colocar_objeto(x, y)
        elemento = self._elemento_en(x, y)
        if elemento is None:
            self._seleccion = []
            self._arrastre = None
            self._dibujar_seleccion()
            self._mostrar_propiedades()
            return
        if elemento.bloqueado:
            self._seleccion = [elemento.id]
            self._dibujar_seleccion()
            self._mostrar_propiedades()
            return
        # 👥 si el objeto está en un grupo, se selecciona el grupo completo
        integrantes = [e.id for e in self._expandir_grupo(elemento)]
        if getattr(evento, "state", 0) & 0x0001 and self._seleccion:      # Shift + clic
            self._seleccion = list(dict.fromkeys(self._seleccion + integrantes))
            self._dibujar_seleccion()
            self._mostrar_propiedades()
            return
        self._seleccion = integrantes
        self._dibujar_seleccion()
        self._mostrar_propiedades()
        accion = self._handle_en(x, y, elemento)
        self._arrastre = (accion, elemento, x, y, elemento.x, elemento.y,
                          elemento.ancho, elemento.alto, elemento.rotacion)
        self._snapshot()
        self.lienzo.configure(cursor="fleur" if accion == "mover" else "crosshair")

    def _elemento_en(self, x, y):
        for elemento in reversed(self.plano.elementos):
            capa = self.plano.capa(elemento.capa)
            if capa and (not capa.visible or capa.bloqueada):
                continue
            if not elemento.visible or elemento.bloqueado:
                continue
            x0, y0, x1, y1 = self._caja_px(elemento)
            if geometria.punto_en_rect(x, y, ((x0 + x1) / 2.0, (y0 + y1) / 2.0,
                                              x1 - x0, y1 - y0, elemento.rotacion)):
                return elemento
        return None

    def _handle_en(self, x, y, elemento):
        esquinas, (cx, cy) = self._esquinas_px(elemento)
        medio_x = (esquinas[0][0] + esquinas[1][0]) / 2.0
        medio_y = (esquinas[0][1] + esquinas[1][1]) / 2.0
        dx, dy = medio_x - cx, medio_y - cy
        largo = max(1.0, (dx * dx + dy * dy) ** 0.5)
        if math.hypot(x - (medio_x + dx / largo * 20.0),
                      y - (medio_y + dy / largo * 20.0)) <= 12:
            return "rotar"
        nombres = ("escala_izq_arr", "escala_der_arr", "escala_der_aba", "escala_izq_aba")
        for nombre, (hx, hy) in zip(nombres, esquinas):
            if abs(x - hx) <= 8 and abs(y - hy) <= 8:
                return nombre
        return "mover"

    def _arrastrar(self, evento):
        if not self._arrastre:
            return
        if self._arrastre[0] == "pan":
            return self._pan(evento)
        accion, elemento, x0, y0, ex, ey, eancho, ealto, erot = self._arrastre
        x = self.lienzo.canvasx(evento.x)
        y = self.lienzo.canvasy(evento.y)
        if accion == "mover":
            elemento.x = ex + self._px_a_metros(x - x0)
            elemento.y = ey + self._px_a_metros(y - y0)
            # 👥 si es un grupo, los demás integrantes acompañan (offsets guardados)
            for otro in self._expandir_grupo(elemento):
                if otro.id == elemento.id:
                    continue
                otro.x = round(elemento.x + float(otro.extra.get("dx_grupo") or 0.0), 4)
                otro.y = round(elemento.y + float(otro.extra.get("dy_grupo") or 0.0), 4)
            if self.cuadricula:
                elemento.x = geometria.snap(elemento.x, 0.05)
                elemento.y = geometria.snap(elemento.y, 0.05)
        elif accion == "rotar":
            cx = self._metros_a_px(elemento.x)
            cy = self._metros_a_px(elemento.y)
            angulo = math.degrees(math.atan2(y - cy, x - cx)) + 90
            elemento.rotacion = round(angulo % 360, 1)
        else:
            # 📐 Si el objeto está girado, se mide el arrastre EN SU PROPIO EJE
            dx_px, dy_px = x - x0, y - y0
            if elemento.rotacion:
                dx_px, dy_px = geometria.rotar_punto(dx_px, dy_px, 0, 0, -elemento.rotacion)
            dx = self._px_a_metros(dx_px)
            dy = self._px_a_metros(dy_px)
            desplazamiento_x = 0.0
            desplazamiento_y = 0.0
            if "der" in accion:
                elemento.ancho = max(0.05, eancho + dx)
                desplazamiento_x = dx / 2.0
            if "izq" in accion:
                elemento.ancho = max(0.05, eancho - dx)
                desplazamiento_x = dx / 2.0
            if "aba" in accion:
                elemento.alto = max(0.05, ealto + dy)
                desplazamiento_y = dy / 2.0
            if "arr" in accion:
                elemento.alto = max(0.05, ealto - dy)
                desplazamiento_y = dy / 2.0
            if elemento.rotacion:
                desplazamiento_x, desplazamiento_y = geometria.rotar_punto(
                    desplazamiento_x, desplazamiento_y, 0, 0, elemento.rotacion)
            elemento.x = ex + desplazamiento_x
            elemento.y = ey + desplazamiento_y
            if self.cuadricula:
                elemento.ancho = round(elemento.ancho, 2)
                elemento.alto = round(elemento.alto, 2)
        self.redibujar()
        self._mostrar_propiedades()
        self._avisar_superposiciones()
        self.marcar_sin_guardar()

    def _soltar(self, _evento=None):
        self._arrastre = None
        self.lienzo.configure(cursor="crosshair")

    def _mover_cursor(self, evento):
        if not getattr(self, "_imagen_tk", None):
            return
        x = self._px_a_metros(self.lienzo.canvasx(evento.x))
        y = self._px_a_metros(self.lienzo.canvasy(evento.y))
        self.lbl_coords.configure(text="X: %.2f m   Y: %.2f m" % (x, y))

    # ================================================================ CAPAS
    def _llenar_capas(self):
        for hijo in self.lista_capas.winfo_children():
            hijo.destroy()
        for capa in sorted(self.plano.capas, key=lambda c: c.orden):
            fila = ctk.CTkFrame(self.lista_capas, fg_color="#f7f9fa", corner_radius=4)
            fila.pack(fill="x", pady=1)
            ctk.CTkButton(fila, text=("👁" if capa.visible else "🚫"), width=26, height=20,
                          font=(FAMILIA, 10), fg_color=("#d4edda" if capa.visible else "#f8d7da"),
                          text_color=("#155724" if capa.visible else "#721c24"),
                          command=lambda c=capa: self._alternar_capa(c)).pack(side="left", padx=2, pady=2)
            ctk.CTkButton(fila, text=("🔒" if capa.bloqueada else "🔓"), width=26, height=20,
                          font=(FAMILIA, 10), fg_color=("#fdebd0" if capa.bloqueada else "#eaf2f8"),
                          text_color="#7d3c98",
                          command=lambda c=capa: self._bloquear_capa(c)).pack(side="left", padx=2)
            etiqueta_capa = ctk.CTkLabel(fila, text=(capa.nombre[:16] + "…"
                                                      if len(capa.nombre) > 17 else capa.nombre),
                                          font=(FAMILIA, 9), anchor="w")
            etiqueta_capa.pack(side="left", fill="x", expand=True)
            ayudar(etiqueta_capa, "%s\n(VISIBLE con el ojo · BLOQUEADA con el candado)" % capa.nombre)
            ctk.CTkButton(fila, text="▲", width=20, height=20, font=(FAMILIA, 8),
                          fg_color="#e5e8e8", text_color="#2c3e50",
                          command=lambda c=capa: self._mover_capa(c, -1)).pack(side="left")
            ctk.CTkButton(fila, text="▼", width=20, height=20, font=(FAMILIA, 8),
                          fg_color="#e5e8e8", text_color="#2c3e50",
                          command=lambda c=capa: self._mover_capa(c, 1)).pack(side="left", padx=(0, 2))

    def _alternar_capa(self, capa):
        capa.visible = not capa.visible
        self._llenar_capas()
        self.redibujar()
        self.marcar_sin_guardar()

    def _bloquear_capa(self, capa):
        capa.bloqueada = not capa.bloqueada
        self._llenar_capas()
        self.marcar_sin_guardar()

    def _mover_capa(self, capa, sentido):
        ordenadas = sorted(self.plano.capas, key=lambda c: c.orden)
        indice = ordenadas.index(capa)
        nuevo = indice + sentido
        if 0 <= nuevo < len(ordenadas):
            ordenadas[indice], ordenadas[nuevo] = ordenadas[nuevo], ordenadas[indice]
            for posicion, cada in enumerate(ordenadas):
                cada.orden = posicion
            self._llenar_capas()
            self.redibujar()
            self.marcar_sin_guardar()

    # ================================================================ ALINEAR
    def alinear(self, modo):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) < 2:
            return messagebox.showinfo("Alinear", "Seleccioná dos o más objetos.", parent=self)
        rects = [(e.x, e.y, e.ancho, e.alto, e.rotacion) for e in elegidos]
        nuevos = geometria.alinear(rects, modo)
        self._snapshot()
        for elemento, (x, y) in zip(elegidos, nuevos):
            elemento.x, elemento.y = round(x, 4), round(y, 4)
        self.redibujar()
        self.marcar_sin_guardar()

    def distribuir(self, horizontal=True):
        """↔/↕ Reparte los objetos seleccionados con la MISMA separación.

        Ejemplo: tres mesas separadas 2 m, 0,5 m y 1 m → quedan a 1,16 m cada una.
        El primero y el último no se mueven (son los topes).
        """
        elegidos = self._elementos_seleccionados()
        if len(elegidos) < 3:
            return messagebox.showinfo(
                "Repartir",
                "Para repartir hacen falta TRES o más objetos seleccionados.\n\n"
                "Seleccioná los objetos con clic (y Shift + clic para sumar más)\n"
                "y volvé a pulsar el botón.", parent=self)
        rects = [(e.x, e.y, e.ancho, e.alto, e.rotacion) for e in elegidos]
        nuevos, hueco = geometria.distribuir(rects, horizontal)
        self._snapshot()
        for elemento, (x, y) in zip(elegidos, nuevos):
            elemento.x, elemento.y = round(x, 4), round(y, 4)
        self.redibujar()
        self._avisar_superposiciones()
        self.marcar_sin_guardar()
        self.lbl_aviso.configure(
            text="%s Repartidos %d objetos · %.2f m entre cada uno"
                 % ("↔" if horizontal else "↕", len(elegidos), hueco))

    def centrar_en_plano(self, horizontal=True, vertical=True):
        """⦿ Coloca la selección en el centro de la hoja del plano."""
        elegidos = self._elementos_seleccionados()
        if not elegidos:
            return messagebox.showinfo("Centrar", "Seleccioná al menos un objeto.", parent=self)
        ancho_hoja = self._px_a_metros(getattr(self, "_imagen_tk", None).width()) if self._imagen_tk else 0
        alto_hoja = self._px_a_metros(self._imagen_tk.height()) if self._imagen_tk else 0
        if not ancho_hoja or not alto_hoja:
            return messagebox.showinfo("Centrar", "Todavía no se cargó el plano.", parent=self)
        centro_x = sum(e.x for e in elegidos) / float(len(elegidos))
        centro_y = sum(e.y for e in elegidos) / float(len(elegidos))
        self._snapshot()
        desplazamiento_x = (ancho_hoja / 2.0 - centro_x) if horizontal else 0.0
        desplazamiento_y = (alto_hoja / 2.0 - centro_y) if vertical else 0.0
        for elemento in elegidos:
            elemento.x = round(elemento.x + desplazamiento_x, 4)
            elemento.y = round(elemento.y + desplazamiento_y, 4)
        self.redibujar()
        self._mostrar_propiedades()
        self.marcar_sin_guardar()
        self.lbl_aviso.configure(text="⦿ Centrado en el plano (%d objeto/s)" % len(elegidos))

    def igualar_tamano(self, horizontal=True):
        """↔/↕ Iguala el ancho o el alto de la selección al del primer objeto."""
        elegidos = self._elementos_seleccionados()
        if len(elegidos) < 2:
            return messagebox.showinfo(
                "Igualar tamaño",
                "Seleccioná DOS o más objetos.\n\nSe toma como referencia el PRIMERO "
                "que elegiste y los demás copian su medida.", parent=self)
        referencia = elegidos[0]
        self._snapshot()
        for elemento in elegidos[1:]:
            if horizontal:
                elemento.ancho = referencia.ancho
            else:
                elemento.alto = referencia.alto
        self.redibujar()
        self._mostrar_propiedades()
        self.marcar_sin_guardar()
        self.lbl_aviso.configure(
            text="%s Igualados %d objetos a %.2f m"
                 % ("↔" if horizontal else "↕", len(elegidos),
                    referencia.ancho if horizontal else referencia.alto))

    # ================================================================ GRUPOS
    def agrupar(self):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) < 2:
            return messagebox.showinfo("Agrupar", "Seleccioná dos o más objetos.", parent=self)
        grupo = "g_%d" % int(__import__("time").time() * 1000 % 100000000)
        self._snapshot()
        centro_x = sum(e.x for e in elegidos) / float(len(elegidos))
        centro_y = sum(e.y for e in elegidos) / float(len(elegidos))
        for elemento in elegidos:
            elemento.extra["grupo"] = grupo
            elemento.extra["dx_grupo"] = round(elemento.x - centro_x, 4)
            elemento.extra["dy_grupo"] = round(elemento.y - centro_y, 4)
        self.redibujar()
        self.marcar_sin_guardar()
        self.lbl_aviso.configure(text="Grupo creado (%d objetos)" % len(elegidos))

    def desagrupar(self):
        elegidos = self._elementos_seleccionados()
        if not elegidos:
            return
        self._snapshot()
        for elemento in elegidos:
            elemento.extra.pop("grupo", None)
            elemento.extra.pop("dx_grupo", None)
            elemento.extra.pop("dy_grupo", None)
        self.redibujar()
        self.marcar_sin_guardar()
        self.lbl_aviso.configure(text="Grupo deshecho")

    def _expandir_grupo(self, elemento):
        """Si el objeto pertenece a un grupo, devuelve todos los del grupo."""
        grupo = (elemento.extra or {}).get("grupo")
        if not grupo:
            return [elemento]
        return [e for e in self.plano.elementos if (e.extra or {}).get("grupo") == grupo]

    # ================================================================ MEDIR
    def iniciar_medicion(self):
        self._modo = "medir"
        self._punto_calibracion = []
        self.lbl_modo.configure(text="MEDIR: clic en el primer punto")
        self.lienzo.configure(cursor="tcross")

    def _click_medicion(self, x, y):
        self._punto_calibracion.append((x, y))
        if len(self._punto_calibracion) == 1:
            self.lbl_modo.configure(text="MEDIR: clic en el segundo punto")
            return
        (x1, y1), (x2, y2) = self._punto_calibracion
        metros = geometria.distancia((self._px_a_metros(x1), self._px_a_metros(y1)),
                                     (self._px_a_metros(x2), self._px_a_metros(y2)))
        self.cancelar()
        self.lienzo.configure(cursor="crosshair")
        messagebox.showinfo("Medición",
                            "Distancia medida:\n\n%.2f metros" % metros, parent=self)

    # ================================================================ SUPERPOSICIONES
    def _avisar_superposiciones(self):
        elegidos = self._elementos_seleccionados()
        candidatos = elegidos if elegidos else self.plano.elementos
        rects = [(e.x, e.y, e.ancho, e.alto, e.rotacion) for e in candidatos]
        choques = 0
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if geometria.rects_se_tocan(rects[i], rects[j]):
                    choques += 1
        if choques:
            self.lbl_aviso.configure(text="⚠️ %d superposición(es)" % choques)
        else:
            self.lbl_aviso.configure(text="")

    # ================================================================ VERSIONES
    def ver_versiones(self):
        nombres = almacen.listar_versiones(self.plano.id)
        if not nombres:
            return messagebox.showinfo("Versiones", "Todavía no hay versiones guardadas.",
                                       parent=self)
        ventana = ctk.CTkToplevel(self)
        ventana.title("🗂️ Versiones del plano")
        ventana.geometry("520x420")
        ventana.transient(self)
        try:
            ventana.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(ventana, text="🗂️ Versiones guardadas", font=(FAMILIA, 13, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=14, pady=(12, 6))
        lista = tk.Listbox(ventana, font=(FAMILIA, 10))
        lista.pack(fill="both", expand=True, padx=14, pady=4)
        for nombre in nombres:
            lista.insert("end", nombre)
        ctk.CTkLabel(ventana, text="Restaurar reemplaza los objetos actuales (podés deshacer).",
                     font=(FAMILIA, 9), text_color="#7f8c8d").pack(anchor="w", padx=14)

        def restaurar():
            seleccion = lista.curselection()
            if not seleccion:
                return
            nombre = lista.get(seleccion[0])
            anterior = almacen.restaurar_version(self.plano.id, nombre)
            if not anterior:
                return messagebox.showerror("Versiones", "No se pudo leer esa versión.",
                                            parent=ventana)
            self._snapshot()
            self.plano.elementos = anterior.elementos
            self.plano.capas = anterior.capas or self.plano.capas
            if anterior.paginas:
                self.plano.paginas = anterior.paginas
                self.escala = anterior.paginas[0].px_por_metro or self.escala
            self._seleccion = []
            self._llenar_capas()
            self.redibujar()
            self._actualizar_estado()
            self.marcar_sin_guardar()
            ventana.destroy()
            messagebox.showinfo("Versiones", "Versión restaurada: %s" % nombre, parent=self)

        fila = ctk.CTkFrame(ventana, fg_color="transparent")
        fila.pack(fill="x", padx=14, pady=10)
        ctk.CTkButton(fila, text="♻️ Restaurar", height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=restaurar).pack(side="left")
        ctk.CTkButton(fila, text="✖ Cerrar", height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=ventana.destroy).pack(side="right")

    # ================================================================ EXPORTAR
    def exportar_pdf(self):
        from tkinter import filedialog
        ruta = filedialog.asksaveasfilename(
            parent=self, title="Exportar plano a PDF", defaultextension=".pdf",
            initialfile="%s.pdf" % self.plano.nombre.replace(" ", "_"),
            filetypes=[("PDF", "*.pdf")])
        if not ruta:
            return
        try:
            exportar.exportar_pdf(self.plano, ruta)
        except Exception as e:
            return messagebox.showerror("Exportar", "No se pudo exportar:\n%s" % e, parent=self)
        messagebox.showinfo("Exportar", "PDF generado:\n\n%s" % ruta, parent=self)

    def exportar_png(self):
        from tkinter import filedialog
        ruta = filedialog.asksaveasfilename(
            parent=self, title="Exportar plano a PNG", defaultextension=".png",
            initialfile="%s.png" % self.plano.nombre.replace(" ", "_"),
            filetypes=[("Imagen PNG", "*.png")])
        if not ruta:
            return
        try:
            exportar.exportar_png(self.plano, ruta, dpi=200)
        except Exception as e:
            return messagebox.showerror("Exportar", "No se pudo exportar:\n%s" % e, parent=self)
        messagebox.showinfo("Exportar", "Imagen generada:\n\n%s" % ruta, parent=self)

    # ================================================================ TECLADO
    def _mover_con_flechas(self, dx, dy):
        """Mueve la selección con las flechas: 10 cm por pulsación."""
        elegidos = self._elementos_seleccionados()
        if not elegidos:
            return
        paso = 0.10
        for elemento in elegidos:
            elemento.x = round(elemento.x + dx * paso, 3)
            elemento.y = round(elemento.y + dy * paso, 3)
        self.redibujar()
        self._mostrar_propiedades()
        self.marcar_sin_guardar()

    # ================================================================ ACCIONES
    def eliminar(self, _evento=None):
        elegidos = self._elementos_seleccionados()
        if not elegidos:
            return
        self._snapshot()
        for elemento in elegidos:
            self.plano.elementos.remove(elemento)
        self._seleccion = []
        self.redibujar()
        self._mostrar_propiedades()
        self.marcar_sin_guardar()

    def duplicar(self, _evento=None):
        elegidos = self._elementos_seleccionados()
        if not elegidos:
            return
        self._snapshot()
        nuevos = []
        for elemento in elegidos:
            copia = Elemento.desde_dict(elemento.a_dict())
            copia.id = Elemento().id
            copia.x += 0.30
            copia.y += 0.30
            copia.orden = len(self.plano.elementos)
            self.plano.elementos.append(copia)
            nuevos.append(copia.id)
        self._seleccion = nuevos
        self.redibujar()
        self.marcar_sin_guardar()

    def copiar(self, _evento=None):
        self._portapapeles = [e.a_dict() for e in self._elementos_seleccionados()]

    def pegar(self, _evento=None):
        datos = getattr(self, "_portapapeles", None)
        if not datos:
            return
        self._snapshot()
        nuevos = []
        for d in datos:
            copia = Elemento.desde_dict(d)
            copia.id = Elemento().id
            copia.x += 0.40
            copia.y += 0.40
            self.plano.elementos.append(copia)
            nuevos.append(copia.id)
        self._seleccion = nuevos
        self.redibujar()
        self.marcar_sin_guardar()

    def seleccionar_todo(self, _evento=None):
        self._seleccion = [e.id for e in self.plano.elementos if not e.bloqueado]
        self.redibujar()
        self._mostrar_propiedades()

    def cancelar(self, _evento=None):
        self._pendiente = None
        self._modo = "seleccion"
        self._punto_calibracion = []
        self.lbl_modo.configure(text="")
        self._seleccion = []
        self.redibujar()

    # ================================================================ PROPIEDADES
    def _mostrar_propiedades(self):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) != 1:
            for entrada in self.props.values():
                entrada.delete(0, "end")
            self.lbl_escala.configure(
                text="%d objeto(s) seleccionado(s)" % len(elegidos) if elegidos else "")
            return
        elemento = elegidos[0]
        valores = {"nombre": elemento.nombre, "ancho": "%.2f" % elemento.ancho,
                   "alto": "%.2f" % elemento.alto, "x": "%.2f" % elemento.x,
                   "y": "%.2f" % elemento.y, "rotacion": "%.1f" % elemento.rotacion}
        for clave, entrada in self.props.items():
            entrada.delete(0, "end")
            entrada.insert(0, str(valores[clave]))
        capa = self.plano.capa(elemento.capa)
        if capa:
            self.cmb_capa.set(capa.nombre)
        self.btn_color.configure(fg_color=elemento.color)
        self.sld_opacidad.set(int(elemento.opacidad * 100))
        # texto del nombre
        self.cmb_texto_pos.set((elemento.texto_pos or "abajo").capitalize())
        self.sld_texto_tam.set(float(elemento.tamano_texto or 9))
        self.lbl_texto_tam.configure(text=str(int(elemento.tamano_texto or 9)))
        self.btn_color_texto.configure(fg_color=elemento.color_texto or "#555555")
        self.var_bloqueado.set(elemento.bloqueado)
        self.var_visible.set(elemento.visible)
        self.lbl_escala.configure(
            text="Escala: 1 px(imagen) = %.4f m\n%.1f px por metro"
                 % (1.0 / self.escala if self.escala else 0, self.escala))

    def aplicar_propiedades(self, _evento=None):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) != 1:
            return
        elemento = elegidos[0]

        def numero(texto, defecto):
            try:
                return float(str(texto).replace(",", "."))
            except Exception:
                return defecto

        elemento.nombre = self.props["nombre"].get().strip()
        elemento.ancho = max(0.05, numero(self.props["ancho"].get(), elemento.ancho))
        elemento.alto = max(0.05, numero(self.props["alto"].get(), elemento.alto))
        elemento.x = numero(self.props["x"].get(), elemento.x)
        elemento.y = numero(self.props["y"].get(), elemento.y)
        elemento.rotacion = numero(self.props["rotacion"].get(), elemento.rotacion) % 360
        for capa in self.plano.capas:
            if capa.nombre == self.cmb_capa.get():
                elemento.capa = capa.id
                break
        elemento.opacidad = max(0.1, float(self.sld_opacidad.get()) / 100.0)
        elemento.texto_pos = (self.cmb_texto_pos.get() or "Abajo").lower()
        elemento.tamano_texto = max(6, int(round(float(self.sld_texto_tam.get()))))
        try:
            self.lbl_texto_tam.configure(text=str(elemento.tamano_texto))
        except Exception:
            pass
        elemento.bloqueado = bool(self.var_bloqueado.get())
        elemento.visible = bool(self.var_visible.get())
        self.redibujar()
        self.marcar_sin_guardar()

    def elegir_color_texto(self):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) != 1:
            return
        color = colorchooser.askcolor(color=elegidos[0].color_texto or "#555555",
                                      title="Color del nombre", parent=self)[1]
        if color:
            elegidos[0].color_texto = color
            self.btn_color_texto.configure(fg_color=color)
            self.redibujar()
            self.marcar_sin_guardar()

    def elegir_color(self):
        elegidos = self._elementos_seleccionados()
        if len(elegidos) != 1:
            return
        color = colorchooser.askcolor(color=elegidos[0].color, parent=self)[1]
        if color:
            elegidos[0].color = color
            self.btn_color.configure(fg_color=color)
            self.redibujar()
            self.marcar_sin_guardar()

    # ================================================================ CALIBRACIÓN
    def iniciar_calibracion(self):
        self._modo = "calibrar"
        self._punto_calibracion = []
        self.lbl_modo.configure(text="CALIBRAR: clic en el 1er punto de una medida conocida")
        self.lienzo.configure(cursor="tcross")

    def _click_calibracion(self, x, y):
        self._punto_calibracion.append((x, y))
        if len(self._punto_calibracion) == 1:
            self.lbl_modo.configure(text="CALIBRAR: ahora el 2do punto")
            return
        (x1, y1), (x2, y2) = self._punto_calibracion
        pixeles = geometria.distancia((x1, y1), (x2, y2))
        if pixeles < 5:
            self.cancelar()
            return messagebox.showinfo("Calibrar", "Los puntos están muy juntos; probá de nuevo.",
                                       parent=self)
        metros = PedirDistancia(self).pedir()
        self.cancelar()
        self.lienzo.configure(cursor="crosshair")
        if not metros:
            return
        nuevo = geometria.px_por_metro((x1, y1), (x2, y2), metros) / self.zoom
        self.escala = nuevo if nuevo > 0 else self.escala
        if self.plano.paginas:
            self.plano.paginas[0].px_por_metro = self.escala
            self.plano.paginas[0].calibrada = True
            self.plano.paginas[0].distancia_real = float(metros)
        self._renderizar()
        self._actualizar_estado()
        self.marcar_sin_guardar()
        messagebox.showinfo("Calibrar",
                            "Escala calibrada:\n%.1f px de imagen por metro\n\n"
                            "1 cm medido en el plano = %.1f m reales."
                            % (self.escala, 1.0 / self.escala if self.escala else 0),
                            parent=self)

    # ================================================================ HISTORIAL
    def _snapshot(self):
        self._historico.append([e.a_dict() for e in self.plano.elementos])
        if len(self._historico) > 60:
            self._historico.pop(0)
        self._futuro = []

    def deshacer(self, _evento=None):
        if not self._historico:
            return
        self._futuro.append([e.a_dict() for e in self.plano.elementos])
        self.plano.elementos = [Elemento.desde_dict(d) for d in self._historico.pop()]
        self._seleccion = []
        self.redibujar()
        self.marcar_sin_guardar()

    def rehacer(self, _evento=None):
        if not self._futuro:
            return
        self._historico.append([e.a_dict() for e in self.plano.elementos])
        self.plano.elementos = [Elemento.desde_dict(d) for d in self._futuro.pop()]
        self._seleccion = []
        self.redibujar()
        self.marcar_sin_guardar()

    # ================================================================ GUARDAR
    def marcar_sin_guardar(self):
        self._sin_guardar = True
        try:
            self.lbl_guardado.configure(text="● Cambios sin guardar", text_color="#f5b041")
        except Exception:
            pass

    def guardar(self, _evento=None):
        try:
            self.plano.usuario = self.usuario
            almacen.guardar(self.plano, guardar_version=True)
            bd.actualizar_plano(self.plano)
            try:
                bd.registrar_version(self.plano.id, self.plano.version,
                                     almacen.ruta_proyecto(self.plano.id), self.usuario,
                                     "Guardado desde el editor")
            except Exception:
                pass
            self._sin_guardar = False
            self.lbl_guardado.configure(text="✔ Guardado", text_color="#a9dfbf")
            self._actualizar_estado()
            return True
        except Exception as e:
            messagebox.showerror("Guardar", "No se pudo guardar el plano:\n%s" % e, parent=self)
            return False

    def cerrar(self):
        if self._sin_guardar:
            respuesta = messagebox.askyesnocancel(
                "Cerrar", "Hay cambios sin guardar.\n\n¿Guardar antes de cerrar?", parent=self)
            if respuesta is None:
                return
            if respuesta and not self.guardar():
                return
        self.destroy()

    def _actualizar_estado(self):
        self.lbl_estado.configure(
            text="Evento: %s   ·   Estado: %s   ·   Plano: %s   ·   Objetos: %d   ·   Escala: %s"
                 % (self.cotizacion.get("evento", "—"), self.cotizacion.get("estado", "—"),
                    self.plano.nombre, len(self.plano.elementos),
                    ("%.1f px/m" % self.escala) if self.escala else "sin calibrar"))
        try:
            self.lbl_escala.configure(
                text="Escala: 1 px(imagen) = %.4f m\n%.1f px por metro"
                     % (1.0 / self.escala if self.escala else 0, self.escala))
        except Exception:
            pass

    def destroy(self):
        self._cache_imagen.clear()
        super().destroy()


class PedirDistancia(ctk.CTkToplevel):
    """Pide la medida real (en metros) entre los dos puntos marcados."""

    def __init__(self, parent):
        super().__init__(parent)
        self.valor = None
        self.title("📐 Medida real")
        self.geometry("380x180")
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(self, text="¿Cuánto mide esa distancia en la realidad?",
                     font=(FAMILIA, 12, "bold"), text_color="#1f538d").pack(padx=16, pady=(16, 6))
        fila = ctk.CTkFrame(self, fg_color="transparent")
        fila.pack(fill="x", padx=16)
        self.entrada = ctk.CTkEntry(fila, placeholder_text="Ej.: 5.00")
        self.entrada.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(fila, text="metros", font=(FAMILIA, 11, "bold")).pack(side="left", padx=6)
        self.entrada.insert(0, "5.00")
        self.entrada.focus_set()
        botones = ctk.CTkFrame(self, fg_color="transparent")
        botones.pack(fill="x", padx=16, pady=14)
        ctk.CTkButton(botones, text="✔ Calibrar", height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self._aceptar).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(botones, text="✖ Cancelar", height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="left", expand=True, fill="x", padx=(4, 0))
        self.entrada.bind("<Return>", lambda e: self._aceptar())

    def _aceptar(self):
        try:
            valor = float(str(self.entrada.get()).replace(",", "."))
            if valor > 0:
                self.valor = valor
        except Exception:
            self.valor = None
        self.destroy()

    def pedir(self):
        self.wait_window()
        return self.valor
