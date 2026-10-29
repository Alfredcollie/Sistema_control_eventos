# -*- coding: utf-8 -*-
"""Visor de documentos DENTRO del programa (PDF e imágenes).

Se usa en los módulos de Flota (Choferes, Vehículos, Compras, Banco) para que
los documentos se vean en una ventana del propio sistema y no en un programa
externo (que muchas veces queda detrás de la ventana principal).
"""
import os
import sys
import subprocess

import tkinter as tk
import customtkinter as ctk

NL = chr(10)


def abrir_aparte(ruta):
    """Abre el archivo con el programa externo del sistema (respaldo)."""
    try:
        if sys.platform == "win32":
            os.startfile(str(ruta))          # noqa: S606 (solo Windows)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(ruta)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(ruta)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except Exception as e:
        print("Aviso - abriendo aparte:", e)
        return False


def resolver(ruta):
    """Devuelve la ruta real del archivo (relativa, de otro equipo, de la nube…)."""
    try:
        from app_paths import resolver_ruta_archivo
        hallada = resolver_ruta_archivo(ruta)
        if hallada:
            return hallada
    except Exception:
        pass
    return str(ruta or "")


def ver_documento_ventana(ruta, titulo="Documento", parent=None, avisar=None):
    """Muestra el PDF (página a página) o la imagen en una ventana del programa."""
    real = resolver(ruta)
    if not real or not os.path.exists(real):
        mensaje = ("El archivo no está en este equipo:" + NL + NL + str(ruta) + NL + NL +
                   "Vuelva a cargarlo desde el módulo.")
        if avisar is not None:
            try:
                avisar("Documento no encontrado", mensaje)
                return None
            except Exception:
                pass
        try:
            from tkinter import messagebox
            messagebox.showerror("Documento no encontrado", mensaje, parent=parent)
        except Exception:
            print(mensaje)
        return None

    ventana = ctk.CTkToplevel(parent)
    ventana.title("%s — %s" % (titulo, os.path.basename(real)))
    try:
        ventana.geometry("980x760")
        ventana.transient(parent)
    except Exception:
        pass
    # 🪟 El visor debe quedar SIEMPRE encima (la ventana que lo abre puede ser
    #    modal o estar maximizada y, si no, el documento se ve detrás).
    soltado = {"hecho": False}

    def _soltar_topmost(_evento=None):
        if soltado["hecho"]:
            return
        soltado["hecho"] = True
        try:
            if ventana.winfo_exists():
                ventana.attributes("-topmost", False)
        except Exception:
            pass

    def _traer_al_frente(_intento=0):
        try:
            if not ventana.winfo_exists():
                return
            if _intento == 0:
                try:
                    ventana.wait_visibility()
                except Exception:
                    pass
            ventana.deiconify()
            ventana.lift()
            ventana.attributes("-topmost", True)
            ventana.focus_force()
            # Se suelta el "siempre encima" cuando el usuario toca el visor
            # (o a los 3 s como máximo), para que no se quede pegado arriba.
            ventana.after(3000, _soltar_topmost)
            if _intento < 4:
                ventana.after(250 + 300 * _intento, lambda: _traer_al_frente(_intento + 1))
        except Exception:
            pass

    for _evt in ("<FocusIn>", "<Button-1>", "<Enter>"):
        try:
            ventana.bind(_evt, _soltar_topmost, add="+")
        except Exception:
            pass
    try:
        ventana.after(60, _traer_al_frente)
    except Exception:
        _traer_al_frente()

    barra = ctk.CTkFrame(ventana, fg_color="#1f538d", corner_radius=0)
    barra.pack(fill="x")
    estado = {"paginas": [], "i": 0}

    def cargar_paginas():
        paginas = []
        try:
            if str(real).lower().endswith(".pdf"):
                import fitz
                from PIL import Image
                documento = fitz.open(real)
                for i in range(documento.page_count):
                    pix = documento[i].get_pixmap(dpi=140)
                    paginas.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
                documento.close()
            else:
                from PIL import Image
                paginas.append(Image.open(real))
        except Exception as e:
            print("Aviso - cargando el documento:", e)
        return paginas

    estado["paginas"] = cargar_paginas()

    lbl_info = ctk.CTkLabel(barra, text="", font=("Arial", 11, "bold"), text_color="white")
    lbl_info.pack(side="left", padx=10, pady=6)
    lienzo = ctk.CTkFrame(ventana, fg_color="#2b2b2b")
    lienzo.pack(fill="both", expand=True)
    lbl_imagen = tk.Label(lienzo, text="", bg="#2b2b2b", fg="white")
    lbl_imagen.pack(fill="both", expand=True)
    ref = {"img": None}

    def pintar(_evento=None):
        paginas = estado["paginas"]
        if not paginas:
            lbl_imagen.configure(text="No se pudo mostrar el documento.", fg="white")
            lbl_info.configure(text=os.path.basename(real))
            return
        i = max(0, min(estado["i"], len(paginas) - 1))
        estado["i"] = i
        try:
            ancho = max(400, ventana.winfo_width() - 40)
            alto = max(300, ventana.winfo_height() - 110)
        except Exception:
            ancho, alto = 900, 650
        imagen = paginas[i].copy()
        factor = min(ancho / float(imagen.width), alto / float(imagen.height), 1.6)
        if factor != 1:
            imagen = imagen.resize((max(1, int(imagen.width * factor)),
                                   max(1, int(imagen.height * factor))))
        from PIL import ImageTk
        ref["img"] = ImageTk.PhotoImage(imagen)
        lbl_imagen.configure(image=ref["img"], text="")
        lbl_info.configure(text="%s   ·   página %d de %d"
                           % (os.path.basename(real), i + 1, len(paginas)))

    def anterior():
        if estado["i"] > 0:
            estado["i"] -= 1
            pintar()

    def siguiente():
        if estado["i"] < len(estado["paginas"]) - 1:
            estado["i"] += 1
            pintar()

    ctk.CTkButton(barra, text="‹", width=36, fg_color="#2c3e50",
                  command=anterior).pack(side="left", padx=2, pady=6)
    ctk.CTkButton(barra, text="›", width=36, fg_color="#2c3e50",
                  command=siguiente).pack(side="left", padx=2, pady=6)
    ctk.CTkButton(barra, text="Abrir aparte", width=110, fg_color="#16a085",
                  command=lambda: abrir_aparte(real)).pack(side="right", padx=6, pady=6)
    ctk.CTkButton(barra, text="Cerrar", width=80, fg_color="#c0392b",
                  command=ventana.destroy).pack(side="right", padx=6, pady=6)

    ventana.bind("<Configure>", pintar)
    ventana.bind("<Left>", lambda e: anterior())
    ventana.bind("<Right>", lambda e: siguiente())
    try:
        ventana.after(120, pintar)
    except Exception:
        pintar()
    return ventana
