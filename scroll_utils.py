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
     rueda con lógica inteligente:
       - Tablas (ttk.Treeview), listas (tk.Listbox) y textos independientes
         (tk.Text): se desplazan directamente.
       - Cajas de texto DENTRO de un CTkScrollableFrame: primero se desplaza el
         CONTENIDO de la caja y, cuando la caja llega a su límite (arriba/abajo),
         se desplaza el FRAME. Esto evita el doble scroll (que se movían la nota
         y la matriz a la vez) y evita que las notas largas queden congeladas.

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


def _cantidad_frame(event):
    """Velocidad de desplazamiento del frame (igual a la nativa de customtkinter:
    Windows = delta/6, macOS = delta, Linux = 1 unidad por muesca)."""
    try:
        if sys.platform.startswith("win"):
            return -int(event.delta / 6)
        if sys.platform == "darwin":
            return -int(event.delta)
        return -1 if getattr(event, "num", None) == 4 else 1
    except Exception:
        return 3


def _manejador_wheel_global(event):
    """Desplaza tablas / listas / texto cuando el puntero está sobre ellas.

    Reglas:
      - Sobre un CTkScrollableFrame:
          * Si el puntero está sobre un tk.Text (caja de texto): el binding de
            clase de tkinter ya desplazó el contenido del texto; aquí solo
            desplazamos el FRAME cuando el texto ya llegó a su límite, y
            devolvemos "break" para que customtkinter NO lo desplace también
            (evita el doble scroll).
          * Si el puntero está sobre etiquetas/vacío del frame: desplazamos el
            frame directamente y devolvemos "break".
      - Fuera de un frame scrolleable: desplazamos la tabla / lista / texto
        independiente que esté bajo el puntero.
    """
    try:
        cantidad = _paso_de_scroll(event)
        if not cantidad:
            return None

        nodo_text = None
        frame_scrolleable = None
        n = event.widget
        while n is not None:
            if nodo_text is None and isinstance(n, tk.Text):
                nodo_text = n
            if isinstance(n, ctk.CTkScrollableFrame):
                frame_scrolleable = n
                break
            n = n.master

        if frame_scrolleable is not None:
            canvas = frame_scrolleable._parent_canvas
            if nodo_text is not None:
                try:
                    pos = nodo_text.yview()
                    en_top = pos[0] <= 0.001
                    en_bottom = pos[1] >= 0.999
                    # ¿El texto aún tiene contenido por desplazar en esa dirección?
                    if (cantidad < 0 and not en_top) or (cantidad > 0 and not en_bottom):
                        # El binding de clase de tk.Text ya movió el texto: no duplicamos.
                        pass
                    elif canvas.yview() != (0.0, 1.0):
                        canvas.yview_scroll(_cantidad_frame(event), "units")
                except Exception:
                    if canvas.yview() != (0.0, 1.0):
                        canvas.yview_scroll(_cantidad_frame(event), "units")
            elif canvas.yview() != (0.0, 1.0):
                canvas.yview_scroll(_cantidad_frame(event), "units")
            return "break"

        # No hay frame scrolleable: desplazar la tabla / lista / texto directamente.
        n = event.widget
        while n is not None:
            if isinstance(n, (ttk.Treeview, tk.Listbox, tk.Text)):
                n.yview_scroll(cantidad, "units")
                return "break"
            n = n.master
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
