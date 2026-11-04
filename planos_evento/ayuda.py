# -*- coding: utf-8 -*-
"""Ayudas emergentes (tooltips) multiplataforma para los botones del editor."""
import tkinter as tk
import customtkinter as ctk

FAMILIA = "Helvetica" if __import__("sys").platform == "darwin" else "Arial"


class Ayuda:
    """Muestra un texto explicativo al pasar el ratón por encima de un botón."""

    def __init__(self, widget, texto, retardo=450):
        self.widget = widget
        self.texto = texto
        self.retardo = retardo
        self._tarea = None
        self._ventana = None
        widget.bind("<Enter>", self._programar, add="+")
        widget.bind("<Leave>", self._ocultar, add="+")
        widget.bind("<ButtonPress>", self._ocultar, add="+")

    def _programar(self, _evento=None):
        self._cancelar()
        self._tarea = self.widget.after(self.retardo, self._mostrar)

    def _cancelar(self):
        if self._tarea:
            try:
                self.widget.after_cancel(self._tarea)
            except Exception:
                pass
            self._tarea = None

    def _mostrar(self):
        if self._ventana or not self.texto:
            return
        try:
            x = self.widget.winfo_rootx() + 10
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            self._ventana = tk.Toplevel(self.widget)
            self._ventana.wm_overrideredirect(True)      # sin bordes (Windows/Linux/macOS)
            self._ventana.attributes("-topmost", True)
            etiqueta = tk.Label(self._ventana, text=self.texto, justify="left",
                                background="#2c3e50", foreground="#ffffff",
                                font=(FAMILIA, 10), padx=8, pady=5, wraplength=320)
            etiqueta.pack()
            self._ventana.wm_geometry("+%d+%d" % (x, y))
        except Exception:
            self._ventana = None

    def _ocultar(self, _evento=None):
        self._cancelar()
        if self._ventana:
            try:
                self._ventana.destroy()
            except Exception:
                pass
            self._ventana = None


def ayudar(widget, texto):
    """Atajo: agrega la ayuda a un widget y devuelve el widget."""
    Ayuda(widget, texto)
    return widget
