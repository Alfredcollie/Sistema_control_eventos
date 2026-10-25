# -*- coding: utf-8 -*-
"""
TOOLTIP_TABLA.PY - Cuadrito de vencimiento para los listados (Compras y Ventas)
==============================================================================
Al pasar el mouse por encima de una factura en los listados de "Cuentas por Pagar"
(Compras) y "Cuentas por Cobrar" (Ventas) aparece un cuadrito con los DÍAS que le
faltan para vencerse, la fecha de vencimiento y el estado.

Se usa igual en los dos módulos:

    TooltipVencimiento(tabla, lambda iid: (fecha_iso_vencimiento, esta_pagada))

El color del cuadrito acompaña al de la fila:

    🟢 verde  -> ya está pagada / cobrada
    🔴 rojo   -> vence en 5 días o menos (o ya está vencida)
    🔵 azul   -> vence entre 6 y 15 días
    ⚪ gris   -> falta más de 15 días
"""
import tkinter as tk
from datetime import date, datetime

# Colores del cuadrito: (fondo, borde, texto)
COLOR_PAGADA = ("#d5f5e3", "#1e8449", "#145a32")
COLOR_URGENTE = ("#f8d7da", "#c0392b", "#7b241c")
COLOR_PROXIMO = ("#d6eaf8", "#1f538d", "#1b4f72")
COLOR_NORMAL = ("#f4f6f7", "#7f8c8d", "#2c3e50")


def _a_fecha(valor):
    """Acepta 'YYYY-MM-DD' o date/datetime y devuelve date (o None)."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    if len(texto) >= 10:
        try:
            return datetime.strptime(texto[:10], "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def texto_vencimiento(vencimiento, pagada=False, hoy=None):
    """(texto, colores) que se muestra en el cuadrito.

    'vencimiento' puede ser la fecha (date o 'YYYY-MM-DD') o None si la factura
    no tiene fecha de vencimiento.
    """
    hoy = hoy or date.today()
    if pagada:
        return "✅ Factura pagada / cobrada\nNo tiene vencimiento pendiente.", COLOR_PAGADA

    f = _a_fecha(vencimiento)
    if f is None:
        return ("ℹ️ Sin fecha de vencimiento calculable\n"
                "(revise la fecha de la factura)"), COLOR_NORMAL

    dias = (f - hoy).days
    fecha_txt = f.strftime("%d/%m/%Y")
    pasados = "día" if abs(dias) == 1 else "días"
    if dias == 0:
        return f"🔴 ¡VENCE HOY! ({fecha_txt})", COLOR_URGENTE
    if dias < 0:
        return (f"⚠️ VENCIDA hace {abs(dias)} {pasados}\n"
                f"📅 Venció el {fecha_txt}"), COLOR_URGENTE
    if dias <= 5:
        return (f"🔴 Le faltan {dias} {pasados} para vencer\n"
                f"📅 Vence el {fecha_txt}"), COLOR_URGENTE
    if dias <= 15:
        return (f"🔵 Le faltan {dias} {pasados} para vencer\n"
                f"📅 Vence el {fecha_txt}"), COLOR_PROXIMO
    return (f"⚪ Le faltan {dias} {pasados} para vencer\n"
            f"📅 Vence el {fecha_txt}"), COLOR_NORMAL


class TooltipVencimiento:
    """Cuadrito que aparece al pasar el mouse por encima de una fila del listado.

    'obtener_datos(iid)' debe devolver (vencimiento, pagada):
      * vencimiento: fecha (date) o texto 'YYYY-MM-DD' (None si no se sabe),
      * pagada: True si la factura ya está pagada/cobrada por completo.
    Si devuelve None, no se muestra nada para esa fila.
    """

    def __init__(self, tabla, obtener_datos, retardo_ms=300, parent=None):
        self.tabla = tabla
        self.obtener_datos = obtener_datos
        self.retardo_ms = retardo_ms
        self.parent = parent or tabla.winfo_toplevel()
        self._tip = None
        self._item = None
        self._job = None

        tabla.bind("<Motion>", self._al_mover, add="+")
        tabla.bind("<Leave>", self._al_salir, add="+")
        tabla.bind("<Button-1>", self._al_salir, add="+")
        tabla.bind("<MouseWheel>", self._al_salir, add="+")      # Windows / macOS
        tabla.bind("<Button-4>", self._al_salir, add="+")        # Linux
        tabla.bind("<Button-5>", self._al_salir, add="+")
        tabla.bind("<Destroy>", self._al_destruir, add="+")

    # ------------------------------------------------------------------
    def _al_mover(self, evento):
        try:
            item = self.tabla.identify_row(evento.y)
        except Exception:
            item = ""
        # También hay que ignorar la zona de encabezados
        try:
            if self.tabla.identify_region(evento.x, evento.y) != "cell":
                item = ""
        except Exception:
            pass

        if not item:
            self._ocultar()
            return
        if item == self._item:
            self._mover(evento)              # ya se está mostrando: solo se acompaña
            return

        self._ocultar()
        self._item = item
        try:
            self._job = self.tabla.after(self.retardo_ms, lambda: self._mostrar(item, evento))
        except Exception:
            self._job = None

    def _al_salir(self, _evento=None):
        self._ocultar()

    def _al_destruir(self, evento=None):
        try:
            if evento is not None and evento.widget is not self.tabla:
                return
        except Exception:
            pass
        self._ocultar()

    # ------------------------------------------------------------------
    def _mostrar(self, item, evento):
        self._job = None
        try:
            if not self.tabla.winfo_exists() or not self.tabla.exists(item):
                return
        except Exception:
            return
        datos = None
        try:
            datos = self.obtener_datos(item)
        except Exception:
            datos = None
        if not datos:
            return
        vencimiento, pagada = (list(datos) + [None, False])[:2] if isinstance(datos, (list, tuple)) else (datos, False)

        texto, (fondo, borde, color_txt) = texto_vencimiento(vencimiento, pagada)
        self._crear_ventana(texto, fondo, borde, color_txt)
        self._mover(evento)

    def _crear_ventana(self, texto, fondo, borde, color_txt):
        self._ocultar_ventana()
        try:
            tip = tk.Toplevel(self.parent)
            tip.wm_overrideredirect(True)                # sin barra de título
            tip.attributes("-topmost", True)
            marco = tk.Frame(tip, background=borde, bd=0)
            marco.pack(fill="both", expand=True, padx=1, pady=1)
            etiqueta = tk.Label(marco, text=texto, justify="left", background=fondo,
                                foreground=color_txt, font=("Arial", 10, "bold"),
                                padx=10, pady=6)
            etiqueta.pack()
            self._tip = tip
        except Exception:
            self._tip = None

    def _mover(self, evento):
        if self._tip is None:
            return
        try:
            x = self.tabla.winfo_rootx() + evento.x + 18
            y = self.tabla.winfo_rooty() + evento.y + 22
            ancho_tip = self._tip.winfo_reqwidth()
            alto_tip = self._tip.winfo_reqheight()
            ancho_pantalla = self._tip.winfo_screenwidth()
            alto_pantalla = self._tip.winfo_screenheight()
            if x + ancho_tip > ancho_pantalla - 8:
                x = max(0, ancho_pantalla - ancho_tip - 8)
            if y + alto_tip > alto_pantalla - 8:
                y = max(0, self.tabla.winfo_rooty() + evento.y - alto_tip - 10)
            self._tip.wm_geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _ocultar_ventana(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _ocultar(self):
        if self._job is not None:
            try:
                self.tabla.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._item = None
        self._ocultar_ventana()
