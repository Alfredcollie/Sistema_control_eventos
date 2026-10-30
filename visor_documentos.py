# -*- coding: utf-8 -*-
"""Visor de documentos DENTRO del programa (PDF e imágenes).

Se usa en Flota y Eventos para que los documentos se vean en una ventana del
propio sistema y no en un programa externo (que muchas veces queda detrás).

Funciona en Windows y en macOS. En macOS el dibujo se hace con CTkImage (que
maneja bien las pantallas Retina); si por algún motivo fallara, se usa un
tk.Canvas como respaldo, y todo queda anotado en visor.log.
"""
import os
import sys
import subprocess
import datetime

import tkinter as tk
import customtkinter as ctk

NL = chr(10)
_REGISTRO = {"ruta": ""}


def _log(mensaje):
    """Anota lo que pasa en el visor (sirve para diagnosticar en macOS)."""
    try:
        if not _REGISTRO["ruta"]:
            try:
                from app_paths import DATA_DIR
                carpeta = str(DATA_DIR)
            except Exception:
                carpeta = os.path.dirname(os.path.abspath(__file__))
            _REGISTRO["ruta"] = os.path.join(carpeta, "visor.log")
        with open(_REGISTRO["ruta"], "a", encoding="utf-8") as archivo:
            archivo.write("%s | %s%s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                         str(mensaje)[:400], NL))
    except Exception:
        pass


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
        _log("ERROR abriendo aparte: %s" % e)
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


def _paginas_del_archivo(real):
    """Imágenes (PIL) de cada página del PDF, o la imagen suelta. Devuelve (lista, error)."""
    paginas = []
    error = ""
    try:
        if str(real).lower().endswith(".pdf"):
            try:
                import fitz                      # PyMuPDF
            except Exception as e_fitz:
                _log("ERROR sin PyMuPDF: %s" % e_fitz)
                return [], ("No está instalado el componente que lee los PDF (PyMuPDF)."
                            + NL + "Use «Abrir aparte» para verlo con el programa del sistema.")
            from PIL import Image
            documento = fitz.open(real)
            for i in range(documento.page_count):
                pix = documento[i].get_pixmap(dpi=130)
                paginas.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
            documento.close()
        else:
            from PIL import Image
            imagen = Image.open(real)
            if imagen.mode not in ("RGB", "L"):
                imagen = imagen.convert("RGB")
            paginas.append(imagen)
    except Exception as e:
        _log("ERROR leyendo el documento: %s" % e)
        error = ("No se pudo abrir el documento." + NL +
                 "El archivo puede estar dañado." + NL +
                 "Use «Abrir aparte» para verlo con el programa del sistema.")
    if not paginas and not error:
        error = "El documento no tiene páginas para mostrar."
    return paginas, error


def ver_documento_ventana(ruta, titulo="Documento", parent=None, avisar=None):
    """Muestra el PDF (página a página) o la imagen en una ventana del programa."""
    real = resolver(ruta)
    _log("abrir visor: %s" % real)
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
        _log("ERROR archivo inexistente")
        return None

    ventana = ctk.CTkToplevel(parent)
    ventana.title("%s — %s" % (titulo, os.path.basename(real)))
    try:
        ventana.geometry("980x760")
        ventana.minsize(500, 400)
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
                    ventana.update_idletasks()      # (macOS) wait_visibility bloquea
                except Exception:
                    pass
            ventana.deiconify()
            ventana.lift()
            try:
                ventana.attributes("-topmost", True)
            except Exception:
                pass
            ventana.focus_force()
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
    estado = {"paginas": [], "i": 0, "error": ""}

    estado["paginas"], estado["error"] = _paginas_del_archivo(real)
    _log("páginas: %d %s" % (len(estado["paginas"]), estado["error"][:60]))

    lbl_info = ctk.CTkLabel(barra, text="", font=("Arial", 11, "bold"), text_color="white")
    lbl_info.pack(side="left", padx=10, pady=6)
    lienzo = ctk.CTkFrame(ventana, fg_color="#2b2b2b")
    lienzo.pack(fill="both", expand=True)

    # 🖼️ Se dibuja con CTkLabel + CTkImage (lo que mejor funciona en macOS/Retina).
    lbl_imagen = ctk.CTkLabel(lienzo, text="", fg_color="#2b2b2b", corner_radius=0)
    lbl_imagen.pack(fill="both", expand=True)
    ref = {"img": None, "tam": None}

    def _mostrar_mensaje(texto):
        try:
            lbl_imagen.configure(image=None, text=texto, text_color="white",
                                 font=("Arial", 13, "bold"))
        except Exception:
            pass

    def pintar(_evento=None, forzar=False):
        paginas = estado["paginas"]
        if not paginas:
            _mostrar_mensaje(estado.get("error") or "No se pudo mostrar el documento.")
            lbl_info.configure(text=os.path.basename(real))
            return
        i = max(0, min(estado["i"], len(paginas) - 1))
        estado["i"] = i
        try:
            ancho = ventana.winfo_width() - 40
            alto = ventana.winfo_height() - 110
        except Exception:
            ancho, alto = 0, 0
        if ancho < 200 or alto < 200:
            ancho, alto = 900, 650
        tam = (int(ancho), int(alto))
        if not forzar and ref["tam"] == tam and ref["img"] is not None:
            return
        ref["tam"] = tam
        try:
            imagen = paginas[i].copy()
            factor = min(ancho / float(imagen.width), alto / float(imagen.height), 1.6)
            if factor <= 0:
                factor = 1.0
            destino = (max(1, int(imagen.width * factor)),
                       max(1, int(imagen.height * factor)))
            if abs(factor - 1) > 0.01:
                try:
                    from PIL import Image as _Image
                    imagen = imagen.resize(destino, _Image.LANCZOS)
                except Exception:
                    imagen = imagen.resize(destino)
            ref["img"] = ctk.CTkImage(light_image=imagen, dark_image=imagen, size=destino)
            lbl_imagen.configure(image=ref["img"], text="")
            lbl_info.configure(text="%s   ·   página %d de %d"
                               % (os.path.basename(real), i + 1, len(paginas)))
        except Exception as e_pintar:
            _log("ERROR dibujando con CTkImage: %s" % e_pintar)
            # 🔁 Respaldo: se dibuja en un Canvas (funciona en cualquier sistema)
            try:
                from PIL import ImageTk
                lbl_imagen.pack_forget()
                global _canvas
                _canvas = tk.Canvas(lienzo, bg="#2b2b2b", highlightthickness=0)
                _canvas.pack(fill="both", expand=True)
                foto = ImageTk.PhotoImage(imagen)
                ref["foto_canvas"] = foto
                _canvas.create_image(0, 0, anchor="nw", image=foto)
                lbl_info.configure(text="%s   ·   página %d de %d"
                                   % (os.path.basename(real), i + 1, len(paginas)))
            except Exception as e_canvas:
                _log("ERROR dibujando con Canvas: %s" % e_canvas)
                _mostrar_mensaje("No se pudo dibujar esta página." + NL +
                                 "Use «Abrir aparte» para verla.")

    def anterior():
        if estado["i"] > 0:
            estado["i"] -= 1
            pintar(forzar=True)

    def siguiente():
        if estado["i"] < len(estado["paginas"]) - 1:
            estado["i"] += 1
            pintar(forzar=True)

    ctk.CTkButton(barra, text="‹", width=36, fg_color="#2c3e50",
                  command=anterior).pack(side="left", padx=2, pady=6)
    ctk.CTkButton(barra, text="›", width=36, fg_color="#2c3e50",
                  command=siguiente).pack(side="left", padx=2, pady=6)
    ctk.CTkButton(barra, text="Abrir aparte", width=110, fg_color="#16a085",
                  command=lambda: abrir_aparte(real)).pack(side="right", padx=6, pady=6)
    ctk.CTkButton(barra, text="Cerrar", width=80, fg_color="#c0392b",
                  command=ventana.destroy).pack(side="right", padx=6, pady=6)

    # 🔄 Al cambiar el tamaño se redibuja, pero con retraso (macOS dispara
    #    <Configure> muchas veces seguidas y eso antes dejaba el visor en negro).
    _redimension = {"pendiente": None}

    def _al_redimensionar(_evento=None):
        try:
            if _redimension["pendiente"] is not None:
                ventana.after_cancel(_redimension["pendiente"])
        except Exception:
            pass
        try:
            _redimension["pendiente"] = ventana.after(250, pintar)
        except Exception:
            pintar()

    ventana.bind("<Configure>", _al_redimensionar)
    ventana.bind("<Left>", lambda e: anterior())
    ventana.bind("<Right>", lambda e: siguiente())

    def _asegurar_tamano(_intento=0):
        """(macOS) A veces la ventana nace con tamaño 1x1: se le fija uno visible."""
        try:
            if not ventana.winfo_exists():
                return
            if ventana.winfo_width() <= 2 or ventana.winfo_height() <= 2:
                ventana.geometry("980x760")
            else:
                return
        except Exception:
            pass
        if _intento < 3:
            try:
                ventana.after(400, lambda: _asegurar_tamano(_intento + 1))
            except Exception:
                pass

    try:
        ventana.after(150, pintar)
        ventana.after(500, _asegurar_tamano)
    except Exception:
        pintar()
    return ventana
