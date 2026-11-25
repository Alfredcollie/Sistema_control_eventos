# -*- coding: utf-8 -*-
"""Cargar un documento eligiendo la modalidad: PC, teléfono o multifuncional.

Se usa desde los botones «Cargar PDF / Adjuntar comprobante» de los módulos:

    from cargar_documento import elegir_documento
    ruta = elegir_documento(self, "Seleccionar el comprobante",
                            [("PDF o imagen", "*.pdf *.jpg *.jpeg *.png")])
    if not ruta:
        return
    ...  # el flujo normal del módulo con esa ruta

La ventana pregunta cómo cargar y, para teléfono/multifuncional, devuelve un
PDF ya armado (o la imagen si no se pudo convertir).
"""
import os
import sys
import threading
from datetime import datetime

try:
    import customtkinter as ctk
    from tkinter import messagebox
except Exception:                     # pragma: no cover
    ctk = None
    messagebox = None

FAMILIA = "Helvetica" if sys.platform == "darwin" else "Arial"


def _ventana_activa():
    """Ventana del módulo que el usuario está usando (aunque no se pase parent)."""
    try:
        import tkinter as tk
        raiz = tk._default_root
        if raiz is None:
            return None
        # 1) la ventana que tiene el foco (módulo o diálogo de trabajo)
        try:
            foco = raiz.focus_get()
            if foco is not None:
                v = foco.winfo_toplevel()
                if v is not None and v is not raiz and v.winfo_exists():
                    return v
        except Exception:
            pass
        # 2) la ventana que tiene el grab (diálogo modal abierto)
        try:
            g = raiz.grab_current()
            if g is not None:
                v = g.winfo_toplevel()
                if v is not None and v is not raiz:
                    return v
        except Exception:
            pass
        # 3) la última ventana visible (la que se abrió más recientemente)
        candidatas = []
        for w in raiz.winfo_children():
            try:
                if isinstance(w, tk.Toplevel) and w is not raiz and w.winfo_viewable():
                    candidatas.append(w)
            except Exception:
                pass
        if candidatas:
            return candidatas[-1]
    except Exception:
        pass
    return None


def _padre(parent):
    """Ventana principal para colgar los diálogos (aunque no se pase parent)."""
    try:
        if parent is not None:
            return parent.winfo_toplevel()
    except Exception:
        pass
    ventana = _ventana_activa()
    if ventana is not None:
        return ventana
    try:
        import tkinter as tk
        if tk._default_root is not None:
            return tk._default_root
    except Exception:
        pass
    return parent


def _config():
    """Configuración local del programa (ajustes del escáner)."""
    try:
        from app_paths import cargar_config_local
        datos = cargar_config_local()
        if isinstance(datos, dict) and datos:
            return datos
    except Exception:
        pass
    try:
        import json
        from app_paths import CONFIG_FILE
        with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, dict) and datos:
            return datos
    except Exception:
        pass
    return {}


def _carpeta(config):
    try:
        import escaner
        return escaner.carpeta_escaneos(config)
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Escaneos")


def _a_pdf(rutas, carpeta, prefijo):
    """Devuelve un PDF a partir de las páginas escaneadas (o la ruta tal cual)."""
    if isinstance(rutas, str):
        rutas = [rutas]
    rutas = [str(r) for r in (rutas or []) if r and os.path.exists(str(r))]
    if not rutas:
        return ""
    if len(rutas) == 1 and rutas[0].lower().endswith(".pdf"):
        return rutas[0]
    try:
        import escaner
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        salida = os.path.join(carpeta, "%s_%s.pdf" % (prefijo, marca))
        escaner.imagenes_a_pdf(rutas, salida)
        if os.path.exists(salida):
            return salida
    except Exception as e:
        print("[Cargar documento] PDF:", e)
    return rutas[0]


def _por_computadora(titulo, tipos, multiple):
    try:
        from dialogos_seguros import (seleccionar_archivo_dialogo,
                                      seleccionar_archivos_dialogo)
    except Exception:
        from tkinter import filedialog
        if multiple:
            return [r for r in (filedialog.askopenfilenames(title=titulo, filetypes=tipos) or []) if r]
        ruta = filedialog.askopenfilename(title=titulo, filetypes=tipos)
        return [ruta] if ruta else []
    if multiple:
        return seleccionar_archivos_dialogo(titulo, tipos)
    ruta = seleccionar_archivo_dialogo(titulo, tipos)
    return [ruta] if ruta else []


def _por_telefono(parent, etiqueta):
    try:
        import escaner_movil
    except Exception as e:
        messagebox.showerror("Teléfono", "No se pudo cargar el escáner del teléfono:\n\n%s" % e,
                             parent=_padre(parent))
        return ""
    config = _config()
    recibidas = {"rutas": []}

    def al_recibir(rutas, tipo=None):
        if isinstance(rutas, str):
            rutas = [rutas]
        recibidas["rutas"] = [str(r) for r in (rutas or []) if r]

    try:
        ventana = escaner_movil.escanear_desde_telefono(
            _padre(parent), config, al_recibir=al_recibir,
            titulo_extra=etiqueta or "Documento")
    except Exception as e:
        messagebox.showerror("Teléfono", "No se pudo abrir el servidor del teléfono:\n\n%s" % e,
                             parent=_padre(parent))
        return ""
    if ventana is None:
        return ""
    try:
        _padre(parent).wait_window(ventana)
    except Exception:
        pass
    if not recibidas["rutas"]:
        return ""
    return _a_pdf(recibidas["rutas"], _carpeta(config), "Escaneo_Telefono")


def _por_escaner(parent):
    try:
        import escaner
    except Exception as e:
        messagebox.showerror("Escáner", "No se pudo cargar el módulo de escaneo:\n\n%s" % e,
                             parent=_padre(parent))
        return ""
    try:
        if not escaner.sistema_soportado():
            messagebox.showinfo("Escáner",
                                "El escaneo con la multifuncional está disponible en Windows y macOS.",
                                parent=_padre(parent))
            return ""
    except Exception:
        pass
    config = _config()
    try:
        datos = escaner.config_escaner(config)
    except Exception:
        datos = {}
    carpeta = _carpeta(config)
    resultado = {"ruta": "", "error": ""}

    ventana = ctk.CTkToplevel(_padre(parent))
    ventana.title("🖨️ Escaneando documento")
    try:
        ventana.geometry("430x170")
        ventana.transient(_padre(parent))
        ventana.grab_set()
    except Exception:
        pass
    ctk.CTkLabel(ventana, text="🖨️ Escaneando el documento…",
                 font=(FAMILIA, 14, "bold"), text_color="#1f538d").pack(pady=(20, 4))
    instruccion = "Coloque el documento en el escáner (%s ppp, %s)." % (
        datos.get("escaner_dpi", "300"), datos.get("escaner_color", "Escala de grises"))
    ctk.CTkLabel(ventana, text=instruccion, font=(FAMILIA, 10), text_color="gray",
                 justify="center", wraplength=390).pack(pady=(0, 8))
    barra = ctk.CTkProgressBar(ventana, width=340, mode="indeterminate")
    barra.pack(pady=4)
    barra.start()
    try:
        ventana.lift()
        ventana.focus_force()
    except Exception:
        pass

    def tarea():
        try:
            try:
                paginas_pedidas = max(1, int(str(datos.get("escaner_paginas") or 1)))
            except Exception:
                paginas_pedidas = 1
            escaneo = escaner.escanear(
                carpeta,
                dispositivo=datos.get("escaner_dispositivo", ""),
                dpi=datos.get("escaner_dpi", "300"),
                color=datos.get("escaner_color", "Escala de grises"),
                origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                paginas=paginas_pedidas,
                optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                modo=datos.get("escaner_modo", "Automático (sin ventanas)"))
            if not escaneo.get("ok"):
                resultado["error"] = str(escaneo.get("error") or "No se pudo escanear.")
            else:
                paginas = [str(p) for p in (escaneo.get("paginas") or []) if os.path.exists(str(p))]
                if not paginas:
                    resultado["error"] = "El escáner no devolvió ninguna página."
                else:
                    resultado["ruta"] = _a_pdf(paginas, carpeta, "Escaneo_Multifuncional")
        except Exception as e:
            resultado["error"] = str(e)
        finally:
            try:
                ven = ventana
                ven.after(0, ven.destroy)
            except Exception:
                pass

    threading.Thread(target=tarea, daemon=True).start()
    try:
        _padre(parent).wait_window(ventana)
    except Exception:
        pass
    if resultado["error"]:
        messagebox.showerror("Escáner", resultado["error"], parent=_padre(parent))
        return ""
    return resultado["ruta"]


def elegir_documentos(parent, titulo="Seleccionar documento", tipos=None,
                      etiqueta="", multiple=False):
    """Ventana «Computadora / Teléfono / Multifuncional». Devuelve una lista de rutas."""
    tipos = tipos or [("PDF o imagen", "*.pdf *.jpg *.jpeg *.png"),
                      ("Todos los archivos", "*.*")]
    origen = _padre(parent)
    if ctk is None:
        return _por_computadora(titulo, tipos, multiple)

    eleccion = {"modo": ""}
    ventana = ctk.CTkToplevel(origen)
    ventana.title("📄 Cargar documento")
    try:
        ventana.geometry("430x330")
        ventana.transient(_padre(parent))
        ventana.grab_set()
        ventana.lift()
        ventana.focus_force()
    except Exception:
        pass

    ctk.CTkLabel(ventana, text="📄 ¿Cómo desea cargar el documento?",
                 font=(FAMILIA, 15, "bold"), text_color="#1f538d").pack(pady=(18, 2))
    ctk.CTkLabel(ventana, text=titulo, font=(FAMILIA, 10), text_color="#7f8c8d",
                 wraplength=390).pack(pady=(0, 10))

    def elegir(modo):
        eleccion["modo"] = modo
        try:
            ventana.grab_release()
        except Exception:
            pass
        try:
            ventana.destroy()
        except Exception:
            pass

    for texto, modo, color, hover in (
            ("📁  Desde la computadora", "pc", "#1f538d", "#163b65"),
            ("📱  Escanear desde el teléfono", "telefono", "#27ae60", "#1e8449"),
            ("🖨️  Escáner multifuncional", "escaner", "#8e44ad", "#6c3483")):
        ctk.CTkButton(ventana, text=texto, width=350, height=42,
                      font=(FAMILIA, 13, "bold"), fg_color=color, hover_color=hover,
                      command=lambda m=modo: elegir(m)).pack(pady=5)
    ctk.CTkButton(ventana, text="✖ Cancelar", width=160, height=34,
                  font=(FAMILIA, 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b",
                  command=lambda: elegir("")).pack(pady=(10, 12))

    try:
        _padre(parent).wait_window(ventana)
    except Exception:
        pass

    modo = eleccion["modo"]
    # 🪟 Apenas se elige la modalidad, la ventana de trabajo vuelve al frente
    #    (si Windows la mandó atrás al abrir el selector, se restaura ya).
    try:
        if origen is not None and origen.winfo_exists():
            origen.deiconify()
            origen.lift()
            origen.focus_force()
    except Exception:
        pass
    rutas = []
    if modo == "pc":
        rutas = _por_computadora(titulo, tipos, multiple)
    elif modo == "telefono":
        ruta = _por_telefono(origen, etiqueta)
        rutas = [ruta] if ruta else []
    elif modo == "escaner":
        ruta = _por_escaner(origen)
        rutas = [ruta] if ruta else []
    # 🪟 La ventana desde donde se abrió el diálogo vuelve al frente: en Windows
    #    el selector mandaba el módulo detrás de la ventana principal.
    try:
        if origen is not None and origen.winfo_exists():
            origen.deiconify()
            origen.lift()
            origen.focus_force()
    except Exception:
        pass
    return rutas


def elegir_documento(parent, titulo="Seleccionar documento", tipos=None,
                     etiqueta="", multiple=False):
    """Igual que elegir_documentos() pero devuelve una sola ruta (o '')."""
    rutas = elegir_documentos(parent, titulo, tipos, etiqueta, multiple)
    return rutas[0] if rutas else ""


# =========================================================
# 🌍 INSTALADOR GLOBAL: todos los «abrir PDF» preguntan la modalidad
# =========================================================
_instalado = False
_en_curso = False
_original_open = None
_original_open_multi = None

# Títulos que NO son documentos para escanear (catálogos, fichas, planos…)
_TITULOS_SIN_ESCANEO = ("catálogo", "catalogo", "ficha", "plano", "logo",
                        "plantilla")


def _es_documento(tipos, titulo):
    """True si el diálogo es para cargar un documento (PDF) y conviene escanearlo."""
    t = str(titulo or "").lower()
    if any(p in t for p in _TITULOS_SIN_ESCANEO):
        return False
    for item in (tipos or []):
        try:
            _desc, patrones = item
        except Exception:
            continue
        if "pdf" in str(patrones).lower():
            return True
    return False


def instalar_selector_global():
    """Hace que los diálogos «abrir PDF» de TODOS los módulos ofrezcan
    Computadora / Teléfono / Multifuncional, sin tocar cada botón."""
    global _instalado, _original_open, _original_open_multi
    if _instalado:
        return
    try:
        from tkinter import filedialog
    except Exception:
        return
    _original_open = filedialog.askopenfilename
    _original_open_multi = filedialog.askopenfilenames

    def _envoltura(original, multiple):
        def _pedir(*args, **kwargs):
            global _en_curso
            if _en_curso:
                return original(*args, **kwargs)
            tipos = kwargs.get("filetypes") or []
            titulo = kwargs.get("title") or "Seleccionar documento"
            if not _es_documento(tipos, titulo):
                return original(*args, **kwargs)
            padre = kwargs.get("parent")
            _en_curso = True
            try:
                rutas = elegir_documentos(padre, titulo, tipos, multiple=multiple)
            except Exception as e:
                print("[Selector de documentos]", e)
                rutas = []
            finally:
                _en_curso = False
            if multiple:
                return tuple(rutas or [])
            return rutas[0] if rutas else ""
        return _pedir

    filedialog.askopenfilename = _envoltura(_original_open, False)
    filedialog.askopenfilenames = _envoltura(_original_open_multi, True)
    _instalado = True
    print("[Selector de documentos] activo: PC / teléfono / multifuncional")
