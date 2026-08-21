# -*- coding: utf-8 -*-
"""
SCROLL_UTILS.PY — Scroll de rueda global y multiplataforma.

Soluciona el problema de que la rueda del ratón deja de funcionar cuando el
puntero está sobre un widget con texto (etiquetas, entradas, cajas de texto) o
sobre tablas/listas.

Cómo funciona:
  1. Parchea CTkScrollableFrame de customtkinter para que el scroll del frame
     también se active cuando el puntero está sobre un CTkTextbox (customtkinter
     lo excluía por defecto, por eso el scroll "se detenía" sobre texto).
  2. Instala (una sola vez, a nivel de la ventana raíz) un manejador global de
     rueda que desplaza tablas (ttk.Treeview), listas (tk.Listbox) y textos
     independientes (tk.Text) cuando el puntero está sobre ellas.

Funciona en Windows (<MouseWheel>) y macOS/Linux (<Button-4>/<Button-5>).
"""
import sys
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk

_parche_aplicado = False
_instalado = False


def _check_if_valid_scroll_mejorado(self, widget):
    """Igual que customtkinter, pero sin excluir los CTkTextbox:
    permite que el frame scrolleable se desplace aunque el puntero esté sobre
    una caja de texto (antes se excluía y el scroll quedaba bloqueado)."""
    if widget == self._parent_canvas:
        return True
    elif isinstance(widget, (ctk.CTkScrollbar, ctk.CTkSlider)):
        return False
    elif isinstance(widget, ctk.CTkScrollableFrame):
        return widget._parent_canvas == self._parent_canvas
    elif widget.master is not None:
        return self._check_if_valid_scroll(widget.master)
    else:
        return False


def _paso_de_scroll(event):
    """Devuelve un número entero de 'units' a desplazar (positivo = baja)."""
    num = getattr(event, "num", None)
    if num == 4:                # Linux: <Button-4> rueda hacia arriba
        return -3
    if num == 5:                # Linux: <Button-5> rueda hacia abajo
        return 3
    delta = getattr(event, "delta", 0)
    if sys.platform == "darwin":
        return -int(delta) * 3
    # Windows: <MouseWheel> event.delta es múltiplo de 120
    return -int(delta / 120) * 3


def _manejador_wheel_global(event):
    """Desplaza tablas / listas / texto independiente cuando el puntero está sobre ellas."""
    try:
        nodo = event.widget
        # Si existe un CTkScrollableFrame ancestro, customtkinter (ya parcheado)
        # se encarga del scroll del frame. No duplicamos nada.
        while nodo is not None:
            if isinstance(nodo, ctk.CTkScrollableFrame):
                return None
            nodo = nodo.master

        # No hay frame scrolleable: desplazar la tabla / lista / texto directamente.
        nodo = event.widget
        while nodo is not None:
            if isinstance(nodo, (ttk.Treeview, tk.Listbox, tk.Text)):
                cantidad = _paso_de_scroll(event)
                if cantidad:
                    nodo.yview_scroll(cantidad, "units")
                return "break"
            nodo = nodo.master
    except Exception:
        pass
    return None


def instalar_scroll_global(root):
    """Aplica el parche a customtkinter e instala el manejador global (una sola vez)."""
    global _parche_aplicado, _instalado

    if not _parche_aplicado:
        try:
            ctk.CTkScrollableFrame._check_if_valid_scroll = _check_if_valid_scroll_mejorado
            _parche_aplicado = True
        except Exception:
            pass

    if not _instalado:
        for secuencia in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            try:
                root.bind_all(secuencia, _manejador_wheel_global, add="+")
            except Exception:
                pass
        _instalado = True
