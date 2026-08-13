# -*- coding: utf-8 -*-

import psycopg2
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
from datetime import datetime
import ctypes
import calendar
import sys
import subprocess
import os
import re
import json
import threading
import importlib

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = "config_local.json"

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# MULTIPLATAFORMA: Ocultar consola solo en Windows
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass


# =========================================================
# MULTIPLATAFORMA: Función universal para maximizar
# =========================================================
def maximizar_ventana(ventana):
    try:
        if sys.platform == "win32":
            ventana.state("zoomed")
        elif sys.platform == "darwin":
            ventana.attributes("-zoomed", True)
        else:
            ventana.state("zoomed")
    except Exception:
        try:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass


_PATRON_ETIQUETAS = re.compile(r'(\[B\]|\[/B\]|\[M\]|\[/M\])')


def hex_to_rgb(hex_color):
    try:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16)/255.0 for i in (0, 2, 4))
    except Exception:
        return (0.0, 0.0, 0.0)


def parsear_segmentos_formato(texto):
    resultado, negrita, color_p = [], False, False
    for parte in _PATRON_ETIQUETAS.split(str(texto)):
        if parte == "[B]":
            negrita = True
        elif parte == "[/B]":
            negrita = False
        elif parte == "[M]":
            color_p = True
        elif parte == "[/M]":
            color_p = False
        elif parte:
            resultado.append((parte, negrita, color_p))
    return resultado


def texto_plano_sin_marcado(texto):
    return _PATRON_ETIQUETAS.sub("", str(texto))

# =========================================================
# 🚀 FUNCIONES GENERADORAS DE CÓDIGOS CORRELATIVOS
# =========================================================
def generar_nuevo_codigo_cotizacion(conn):
    prefijo_fecha = datetime.now().strftime("%y%m%d")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT codigo_cotizacion FROM cotizaciones WHERE codigo_cotizacion LIKE %s ORDER BY id DESC LIMIT 1", (f"{prefijo_fecha}-%",))
        res = cursor.fetchone()
        if res and res[0]:
            partes = str(res[0]).split('-')
            if len(partes) >= 2:
                try:
                    secuencial = int(partes[1]) + 1
                except ValueError:
                    secuencial = 1
            else:
                secuencial = 1
        else:
            secuencial = 1
        return f"{prefijo_fecha}-{secuencial:02d}-01"
    except Exception as e:
        print("Error generando código de cotización:", e)
        return f"{prefijo_fecha}-01-01"

def generar_nueva_version_evento_existente(conn, codigo_actual):
    partes = str(codigo_actual).split('-')
    if len(partes) >= 2:
        base = f"{partes[0]}-{partes[1]}"
    else:
        base = str(codigo_actual)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT codigo_cotizacion FROM cotizaciones WHERE codigo_cotizacion LIKE %s ORDER BY id DESC LIMIT 1", (f"{base}-%",))
        res = cursor.fetchone()
        if res and res[0]:
            partes_ult = str(res[0]).split('-')
            if len(partes_ult) == 3:
                try:
                    version = int(partes_ult[2]) + 1
                except ValueError:
                    version = 2
            else:
                version = 2
        else:
            version = 2
        return f"{base}-{version:02d}"
    except Exception as e:
        print("Error generando versión de cotización:", e)
        return f"{base}-02"


_SCHEMA_PDF_OK = False

def generar_reporte_cotizacion_pdf(conn_shared, codigo_cotizacion):
    global _SCHEMA_PDF_OK
    try:
        cursor = conn_shared.cursor()

        if not _SCHEMA_PDF_OK:
            try:
                c_alt = conn_shared.cursor()
                c_alt.execute("ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS tipo_cambio NUMERIC DEFAULT 3.75")
                conn_shared.commit()
            except Exception:
                conn_shared.rollback()
            try:
                c_alt = conn_shared.cursor()
                c_alt.execute("ALTER TABLE cotizacion_proveedores ADD COLUMN IF NOT EXISTS cantidad INTEGER DEFAULT 1")
                conn_shared.commit()
            except Exception:
                conn_shared.rollback()
            _SCHEMA_PDF_OK = True

        cliente, descripcion_proyecto, proyecto, contacto_cliente = "CLIENTE COMERCIAL", "Servicios Logísticos", "PROYECTO BLACK CUBE", "No especificado"
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        forma_pago_pdf = "50% adelantado, 50% a 30 días de la primera factura."
        moneda, simbolo_moneda, tipo_cambio_pdf = "Soles", "S/", 3.75

        try:
            cursor.execute("SELECT nombre_empresa, descripcion, nombre_evento, tipo_cambio, forma_pago FROM cotizaciones WHERE codigo_cotizacion = %s", (codigo_cotizacion,))
            res_db = cursor.fetchone()
            if res_db:
                cliente = str(res_db[0]).replace('{', '').replace('}', '').strip()
                descripcion_proyecto = str(res_db[1]).replace('{', '').replace('}', '').strip()
                proyecto = str(res_db[2]).replace('{', '').replace('}', '').strip()
                if len(res_db) > 3 and res_db[3] is not None and float(res_db[3]) > 0:
                    tipo_cambio_pdf = float(res_db[3])
                if len(res_db) > 4 and res_db[4]:
                    forma_pago_pdf = str(res_db[4]).strip()
            else:
                return False, f"No se encontró el registro {codigo_cotizacion} en la tabla cotizaciones."
        except Exception:
            conn_shared.rollback()

        config = {}
        if os.path.exists(RUTA_CONFIG):
            try:
                with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                    config = json.load(f)
            except Exception:
                pass

        try:
            if cliente:
                cursor.execute("SELECT persona_contacto, razon_comercial FROM clientes WHERE nombre_empresa = %s OR ruc = %s OR nombre_empresa ILIKE %s OR razon_comercial ILIKE %s", (cliente, cliente, f"%{cliente}%", f"%{cliente}%"))
                res_cont = cursor.fetchone()
                if res_cont:
                    if res_cont[0]:
                        contacto_cliente = str(res_cont[0]).replace('{', '').replace('}', '').strip()
        except Exception:
            conn_shared.rollback()

        codigo_impresion = str(codigo_cotizacion)
        try:
            cursor.execute("SELECT COUNT(*) FROM cotizaciones WHERE codigo_cotizacion LIKE %s", (f"{codigo_cotizacion}%",))
            conteo_versiones = cursor.fetchone()
            if conteo_versiones and int(conteo_versiones[0]) > 1:
                codigo_impresion = f"{codigo_cotizacion}-{int(conteo_versiones[0]) - 1}"
        except Exception:
            conn_shared.rollback()

        ruta_drive = config.get("ruta_drive", "").strip()
        if ruta_drive and os.path.exists(ruta_drive):
            carpeta_destino = os.path.join(ruta_drive, "Cotizaciones")
        else:
            ruta_g = r"G:\Mi unidad\Programa de control black Cube\Cotizaciones"
            if os.path.exists(r"G:\Mi unidad"):
                carpeta_destino = ruta_g
            else:
                carpeta_destino = os.path.join(os.getcwd(), "Cotizaciones")
        if not os.path.exists(carpeta_destino):
            try:
                os.makedirs(carpeta_destino)
            except Exception:
                pass
        nombre_archivo = os.path.join(carpeta_destino, f"Cotizacion_{codigo_cotizacion}.pdf")

        c = canvas.Canvas(nombre_archivo, pagesize=letter)
        ruta_usar = None
        ruta_conf = config.get("ruta_logo_cotizacion", "")
        if ruta_conf and os.path.exists(ruta_conf):
            ruta_usar = ruta_conf
        if not ruta_usar:
            fallbacks = [
                "LogoCotizacion.png",
                "LogoCotizacion.jpg",
                "Logo_Collie_Software.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.jpg"
            ]
            for fallback in fallbacks:
                if os.path.exists(fallback):
                    ruta_usar = fallback
                    break

        rgb_primario = hex_to_rgb(config.get("color_primario", "#eb337a"))
        rgb_secundario = hex_to_rgb(config.get("color_secundario", "#000000"))
        rgb_franja = hex_to_rgb(config.get("color_franja", config.get("color_primario", "#eb337a")))

        offset = 0
        if ruta_usar:
            try:
                img = ImageReader(ruta_usar)
                img_w, img_h = img.getSize()
                ancho_hoja = 612
                alto_proporcional = ancho_hoja * (img_h / float(img_w))
                y_logo = 792 - alto_proporcional
                c.drawImage(ruta_usar, 0, y_logo, width=ancho_hoja, height=alto_proporcional, mask='auto')
                offset = (y_logo - 30) - 650
            except Exception:
                c.drawImage(ruta_usar, 40, 685, width=530, height=90, mask='auto', preserveAspectRatio=True)
                offset = 0

        c.setFont("Helvetica-Bold", 26)
        c.drawString(40, 650 + offset, "Cotización")
        c.setFont("Helvetica-Bold", 10.5)
        c.drawRightString(570, 665 + offset, f"No.: {codigo_impresion}")
        c.setFont("Helvetica", 10)
        c.drawRightString(570, 650 + offset, f"Fecha: {fecha_actual}")
        c.drawRightString(570, 635 + offset, f"Moneda: {moneda}")

        c.setLineWidth(1)
        c.setStrokeColorRGB(0.88, 0.88, 0.88)
        c.setFillColorRGB(0.98, 0.98, 0.98)
        c.roundRect(40, 540 + offset, 530, 80, 2, stroke=1, fill=1)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(50, 603 + offset, "CLIENTE:")
        c.drawString(50, 585 + offset, "CONTACTO:")
        c.drawString(365, 603 + offset, "PROYECTO:")

        if len(proyecto) > 26:
            c.drawString(435, 603 + offset, proyecto[:26])
            c.drawString(435, 591 + offset, proyecto[26:52])
            y_etiqueta_desc, y_texto_desc = 573 + offset, 560 + offset
        else:
            c.drawString(435, 603 + offset, proyecto)
            y_etiqueta_desc, y_texto_desc = 585 + offset, 572 + offset

        c.drawString(365, y_etiqueta_desc, "DESCRIPCION DEL PROYECTO:")
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        if len(descripcion_proyecto) > 45:
            c.drawString(365, y_texto_desc, descripcion_proyecto[:45])
            c.drawString(365, y_texto_desc - 11, descripcion_proyecto[45:90])
        else:
            c.drawString(365, y_texto_desc, descripcion_proyecto)

        c.setFont("Helvetica", 9.5)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(105, 603 + offset, cliente)
        c.drawString(115, 585 + offset, contacto_cliente)

        TABLE_LEFT, TABLE_RIGHT, DESC_X, DESC_MAX_WIDTH, ITEM_MAX_WIDTH, HEADER_H, MARGEN_INFERIOR_TABLA, Y_INICIO_PAGINA_CONTINUACION = 40, 570, 135, 205, 88, 20, 55, 745

        def wrap_text(texto, fuente, tam, max_ancho):
            lineas_finales = []
            for parrafo in str(texto).replace('\r', '').split('\n'):
                parrafo = parrafo.strip()
                if not parrafo:
                    continue
                actual = ""
                for palabra in parrafo.split(' '):
                    prueba = (actual + " " + palabra).strip()
                    if c.stringWidth(prueba, fuente, tam) <= max_ancho:
                        actual = prueba
                    else:
                        if actual:
                            lineas_finales.append(actual)
                        actual = palabra
                if actual:
                    lineas_finales.append(actual)
            return lineas_finales if lineas_finales else [""]

        def wrap_texto_formato(texto, tam, max_ancho):
            lineas_finales = []
            for parrafo in str(texto).replace('\r', '').split('\n'):
                if not texto_plano_sin_marcado(parrafo).strip():
                    continue
                tokens = []
                for frag_texto, es_neg, es_col in parsear_segmentos_formato(parrafo):
                    partes = frag_texto.split(' ')
                    for idx, palabra in enumerate(partes):
                        if palabra:
                            tokens.append((palabra, es_neg, es_col))
                        if idx < len(partes) - 1:
                            tokens.append((' ', es_neg, es_col))
                linea_actual, ancho_actual = [], 0.0
                for palabra, es_neg, es_col in tokens:
                    fuente_palabra = "Helvetica-Bold" if es_neg else "Helvetica"
                    ancho_palabra = c.stringWidth(palabra, fuente_palabra, tam)
                    if palabra == ' ':
                        if linea_actual:
                            linea_actual.append((palabra, es_neg, es_col))
                            ancho_actual += ancho_palabra
                        continue
                    if ancho_actual + ancho_palabra > max_ancho and linea_actual:
                        while linea_actual and linea_actual[-1][0] == ' ':
                            linea_actual.pop()
                        lineas_finales.append(linea_actual)
                        linea_actual, ancho_actual = [], 0.0
                    linea_actual.append((palabra, es_neg, es_col))
                    ancho_actual += ancho_palabra
                while linea_actual and linea_actual[-1][0] == ' ':
                    linea_actual.pop()
                if linea_actual:
                    lineas_finales.append(linea_actual)
            return lineas_finales if lineas_finales else [[]]

        def dibujar_linea_formateada(x, y, lista_palabras, tam):
            x_cursor = x
            for palabra, es_neg, es_col in lista_palabras:
                fuente_palabra = "Helvetica-Bold" if es_neg else "Helvetica"
                if es_col:
                    c.setFillColorRGB(*rgb_primario)
                elif es_neg:
                    c.setFillColorRGB(*rgb_secundario)
                else:
                    c.setFillColorRGB(0.25, 0.25, 0.25)
                c.setFont(fuente_palabra, tam)
                c.drawString(x_cursor, y, palabra)
                x_cursor += c.stringWidth(palabra, fuente_palabra, tam)

        def dibujar_encabezado_tabla(y):
            c.setFillColorRGB(*rgb_franja)
            c.rect(TABLE_LEFT, y, TABLE_RIGHT - TABLE_LEFT, HEADER_H, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1)
            c.setFont("Helvetica-Bold", 9)
            c.drawCentredString(84, y + 6.5, "ITEM")
            c.drawCentredString(237.5, y + 6.5, "DESCRIPCIÓN")
            c.drawCentredString(385, y + 6.5, "P. UNIT.")
            c.drawCentredString(450, y + 6.5, "CANT.")
            c.drawCentredString(525, y + 6.5, "PRECIO")

        y_pos, subtotal_acumulado = 500 + offset, 0.0
        dibujar_encabezado_tabla(y_pos)

        try:
            cursor.execute("SELECT * FROM cotizacion_proveedores WHERE codigo_cotizacion = %s ORDER BY categoria_suministro ASC", (codigo_cotizacion,))
            filas_preparadas, bloques_items = [], []
            for r in cursor.fetchall():
                cat_sum = str(r[2]).strip().upper() if len(r) > 2 and r[2] else "SUMINISTRO"
                prov_nom = str(r[3]).strip() if len(r) > 3 and r[3] else "Proveedor"
                precio_final_venta = float(r[8]) if len(r) > 8 and r[8] else 0.0
                nota_solicitud = str(r[9]).strip() if len(r) > 9 and r[9] else ""
                cant_item = int(r[10]) if len(r) > 10 and r[10] else 1
                p_unitario = precio_final_venta / float(cant_item) if cant_item > 0 else precio_final_venta
                texto_base = nota_solicitud if nota_solicitud else f"Servicio especializado provisto por {prov_nom}."
                lineas_desc = wrap_texto_formato(texto_base, 8.5, DESC_MAX_WIDTH)
                filas_preparadas.append({"categoria": cat_sum, "lineas_desc": lineas_desc, "precio": precio_final_venta, "p_unitario": p_unitario, "cantidad": cant_item, "altura": max(28, 15 + len(lineas_desc) * 10.5)})

            for i, f in enumerate(filas_preparadas):
                if bloques_items and bloques_items[-1]["nombre"] == f["categoria"]:
                    bloques_items[-1]["indices"].append(i)
                else:
                    bloques_items.append({"nombre": f["categoria"], "indices": [i]})

            en_tope_pagina, indice_bloque = True, 0
            for bloque in bloques_items:
                color_fondo = 0.95 if indice_bloque % 2 == 0 else 1.0
                indice_bloque += 1
                if not en_tope_pagina and (y_pos - filas_preparadas[bloque["indices"][0]]["altura"]) < MARGEN_INFERIOR_TABLA:
                    c.showPage()
                    y_pos = Y_INICIO_PAGINA_CONTINUACION
                    dibujar_encabezado_tabla(y_pos)
                    c.setFillColorRGB(0, 0, 0)
                    en_tope_pagina = True
                y_inicio_bloque = y_pos
                for i_idx, i in enumerate(bloque["indices"]):
                    f = filas_preparadas[i]
                    if y_pos - f["altura"] < MARGEN_INFERIOR_TABLA:
                        if not en_tope_pagina:
                            c.setFont("Helvetica-Bold", 9)
                            c.setFillColorRGB(0, 0, 0)
                            lineas_cat = wrap_text(bloque["nombre"].strip(), "Helvetica-Bold", 9, ITEM_MAX_WIDTH)
                            y_cat = ((y_inicio_bloque + y_pos) / 2) + ((len(lineas_cat) - 1) * 5.5) - 3
                            for linea in lineas_cat:
                                c.drawCentredString(84, y_cat, linea)
                                y_cat -= 11
                        c.setLineWidth(0.5)
                        c.setStrokeColorRGB(0.85, 0.85, 0.85)
                        c.line(TABLE_LEFT, y_pos, TABLE_RIGHT, y_pos)
                        c.showPage()
                        y_pos = Y_INICIO_PAGINA_CONTINUACION
                        dibujar_encabezado_tabla(y_pos)
                        c.setFillColorRGB(0, 0, 0)
                        en_tope_pagina = True
                        y_inicio_bloque = y_pos
                    en_tope_pagina = False
                    c.setFillColorRGB(color_fondo, color_fondo, color_fondo)
                    c.rect(TABLE_LEFT, y_pos - f["altura"], TABLE_RIGHT - TABLE_LEFT, f["altura"], fill=1, stroke=0)
                    y_pos -= f["altura"]
                    y_renglon = y_pos + f["altura"] - 12
                    for linea_palabras in f["lineas_desc"]:
                        dibujar_linea_formateada(DESC_X, y_renglon, linea_palabras, 8.5)
                        y_renglon -= 10.5
                    y_centro_fila = y_pos + (f["altura"] / 2) - 3
                    c.setFont("Helvetica", 9)
                    c.setFillColorRGB(0, 0, 0)
                    c.drawCentredString(385, y_centro_fila, f"{simbolo_moneda} {f['p_unitario']:,.2f}")
                    c.drawCentredString(450, y_centro_fila, str(f["cantidad"]))
                    c.drawCentredString(525, y_centro_fila, f"{simbolo_moneda} {f['precio']:,.2f}")
                    subtotal_acumulado += f["precio"]
                    if i_idx == len(bloque["indices"]) - 1:
                        c.setFont("Helvetica-Bold", 9)
                        c.setFillColorRGB(0, 0, 0)
                        lineas_cat = wrap_text(bloque["nombre"].strip(), "Helvetica-Bold", 9, ITEM_MAX_WIDTH)
                        y_cat = ((y_inicio_bloque + y_pos) / 2) + ((len(lineas_cat) - 1) * 5.5) - 3
                        for linea in lineas_cat:
                            c.drawCentredString(84, y_cat, linea)
                            y_cat -= 11
                c.setLineWidth(0.5)
                c.setStrokeColorRGB(0.85, 0.85, 0.85)
                c.line(TABLE_LEFT, y_pos, TABLE_RIGHT, y_pos)
        except Exception as e:
            return False, f"Error al compilar las filas dinámicas de la matriz: {str(e)}"

        if y_pos < 205:
            c.showPage()
            y_pos = Y_INICIO_PAGINA_CONTINUACION

        y_totales = y_pos - 65
        fee_produccion = subtotal_acumulado * 0.15
        total_general_soles = subtotal_acumulado + fee_produccion
        total_general_dolares = total_general_soles / tipo_cambio_pdf

        c.setLineWidth(1)
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.line(40, y_totales+45, 570, y_totales+45)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawRightString(440, y_totales+25, "SUB TOTAL (SOLES)")
        c.drawRightString(440, y_totales+8, "15% FEE PRODUCCIÓN")
        c.drawRightString(440, y_totales-12, "TOTAL (SOLES)")
        c.setFont("Helvetica-Bold", 10.5)
        c.setFillColorRGB(*rgb_primario)
        c.drawRightString(440, y_totales-32, "TOTAL EQUIVALENTE (DÓLARES)")
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(490, y_totales+25, "S/")
        c.drawRightString(565, y_totales+25, f"{subtotal_acumulado:,.2f}")
        c.drawString(490, y_totales+8, "S/")
        c.drawRightString(565, y_totales+8, f"{fee_produccion:,.2f}")
        c.setFont("Helvetica-Bold", 11)
        c.drawString(490, y_totales-12, "S/")
        c.drawRightString(565, y_totales-12, f"{total_general_soles:,.2f}")
        c.setFillColorRGB(*rgb_primario)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(475, y_totales-32, "$")
        c.drawRightString(565, y_totales-32, f"{total_general_dolares:,.2f} USD")

        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColorRGB(*rgb_primario)
        c.drawString(40, y_totales-55, "TÉRMINOS Y CONDICIONES:")
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.drawString(40, y_totales-68, "Precios no incluyen IGV.")
        c.drawString(40, y_totales-80, "Cotización válida por 7 días. Posterior a ello podría haber cambios en el presupuesto.")
        y_cond_actual = y_totales - 92
        c.drawString(40, y_cond_actual, "Forma de pago: ")
        x_pago = 40 + c.stringWidth("Forma de pago: ", "Helvetica", 8)
        for linea in wrap_text(forma_pago_pdf, "Helvetica", 8, 570 - x_pago):
            c.drawString(x_pago, y_cond_actual, linea)
            y_cond_actual -= 12
        c.drawString(40, y_cond_actual, "Penalidad: Si el presupuesto es aprobado y finalmente el proyecto no se lleva a cabo, se facturará al cliente un 10% del valor total como compensación por gastos administrativos.")

        c.save()
        return True, nombre_archivo
    except Exception as e:
        return False, str(e)


# =========================================================
# CLASE: CALENDARIO NATIVO (MEJORADO CON COMBOBOX)
# =========================================================
class CalendarioNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Seleccionar Fecha")
        self.geometry("310x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (310 // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (320 // 2)
        self.geometry(f"+{x}+{y}")

        self.current_year = datetime.now().year
        self.current_month = datetime.now().month
        self.meses_nombres = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

        self.header_frame = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        self.header_frame.pack(fill="x")

        ctk.CTkButton(self.header_frame, text="<", width=25, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.prev_month).pack(side="left", padx=5, pady=10)

        self.cmb_mes = ctk.CTkComboBox(self.header_frame, values=self.meses_nombres, width=100, command=self.cambiar_mes_combo)
        self.cmb_mes.pack(side="left", padx=2, pady=10)

        anios = [str(y) for y in range(datetime.now().year - 80, datetime.now().year + 20)]
        self.cmb_anio = ctk.CTkComboBox(self.header_frame, values=anios, width=75, command=self.cambiar_anio_combo)
        self.cmb_anio.pack(side="left", padx=2, pady=10)

        ctk.CTkButton(self.header_frame, text=">", width=25, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.next_month).pack(side="right", padx=5, pady=10)

        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)

        dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i, day in enumerate(dias_semana):
            ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d").grid(row=0, column=i, padx=5, pady=5)

        self.update_calendar()

    def cambiar_mes_combo(self, choice):
        self.current_month = self.meses_nombres.index(choice) + 1
        self.update_calendar()

    def cambiar_anio_combo(self, choice):
        try:
            self.current_year = int(choice)
            self.update_calendar()
        except ValueError:
            pass

    def update_calendar(self):
        self.cmb_mes.set(self.meses_nombres[self.current_month - 1])
        self.cmb_anio.set(str(self.current_year))

        for widget in self.days_frame.winfo_children():
            if int(widget.grid_info()["row"]) > 0: widget.destroy()

        cal = calendar.monthcalendar(self.current_year, self.current_month)
        hoy = datetime.now()

        for row_idx, week in enumerate(cal, start=1):
            for col_idx, day in enumerate(week):
                if day != 0:
                    btn_color = "#d4edda" if day == hoy.day and self.current_month == hoy.month and self.current_year == hoy.year else "transparent"
                    txt_color = "#155724" if btn_color == "#d4edda" else "black"
                    btn = ctk.CTkButton(self.days_frame, text=str(day), width=30, height=30, fg_color=btn_color, text_color=txt_color, hover_color="#e0e0e0", font=("Arial", 11))
                    btn.configure(command=lambda d=day: self.select_date(d))
                    btn.grid(row=row_idx, column=col_idx, padx=3, pady=2)

    def prev_month(self):
        self.current_month -= 1
        if self.current_month < 1: self.current_month = 12; self.current_year -= 1
        self.update_calendar()

    def next_month(self):
        self.current_month += 1
        if self.current_month > 12: self.current_month = 1; self.current_year += 1
        self.update_calendar()

    def select_date(self, day):
        fecha_seleccionada = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, fecha_seleccionada)
        self.destroy()


# =======================================================
# CLASE PRINCIPAL - ETAPA 3 (MATRIZ DE PROVEEDORES)
# =======================================================
_SCHEMA_F3_OK = False

class VentanaEtapaProveedores:
    def __init__(self, parent_ventana, codigo_cot, empresa, evento, callback_on_close=None):
        self.parent_ventana = parent_ventana
        self.root = parent_ventana.root if hasattr(parent_ventana, 'root') else parent_ventana
        self.conn = conectar_db()
        self.codigo_cot = str(codigo_cot).strip()
        self.empresa = empresa
        self.evento = evento
        self.callback_on_close = callback_on_close
        self.usuario_activo = getattr(self.parent_ventana, 'usuario_activo', 'Desconocido')
        ctk.set_appearance_mode("Light")

        if not self.conn:
            messagebox.showwarning("Sin conexión", "No hay conexión con la base de datos.\nLa Etapa 3 no puede abrirse en Modo Lectura.", parent=self.root)
            self.v_prov = None
            return

        self.v_prov = ctk.CTkToplevel(self.root)
        self.v_prov.title(f"Etapa 3: Matriz de Costos - Cotización: {self.codigo_cot}")
        self.v_prov.geometry("1200x780")
        self.v_prov.grab_set()
        self.v_prov.after(100, lambda: maximizar_ventana(self.v_prov))
        self.v_prov.protocol("WM_DELETE_WINDOW", self._cerrar_ventana)

        self.fila_matriz_seleccionada = None
        self.lista_widgets_filas = []
        self.matriz_expandida = False

        global _SCHEMA_F3_OK
        if not _SCHEMA_F3_OK:
            def tarea_init():
                global _SCHEMA_F3_OK
                c_conn = conectar_db(silencioso=True)
                if not c_conn: return
                try:
                    c_alt = c_conn.cursor()
                    alters = [
                        "ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS tipo_cambio NUMERIC DEFAULT 3.75",
                        "ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS forma_pago TEXT DEFAULT '50% adelantado, 50% a 30 días de la primera factura.'",
                        "ALTER TABLE cotizacion_proveedores ADD COLUMN IF NOT EXISTS cantidad INTEGER DEFAULT 1",
                        "ALTER TABLE cotizacion_proveedores ADD COLUMN IF NOT EXISTS dias_credito INTEGER DEFAULT 0",
                    ]
                    for sql in alters:
                        try:
                            c_alt.execute(sql)
                            c_conn.commit()
                        except: c_conn.rollback()
                    _SCHEMA_F3_OK = True
                except Exception: pass
                finally: liberar_conexion(c_conn)
                
            threading.Thread(target=tarea_init, daemon=True).start()

        self.f_info = ctk.CTkFrame(self.v_prov, corner_radius=10, fg_color="#1f538d")
        self.f_info.pack(fill="x", padx=15, pady=(15, 5))
        ctk.CTkLabel(self.f_info, text=f"Cliente: {self.empresa}   |   Evento: {self.evento}   |   N° Cotización: {self.codigo_cot}", font=("Arial", 12, "bold"), text_color="white").pack(anchor="w", padx=15, pady=8)

        self.f_inputs = ctk.CTkFrame(self.v_prov, corner_radius=10)
        self.f_inputs.pack(fill="x", padx=15, pady=5)
        self.f_inputs.grid_columnconfigure(4, weight=1)
        ctk.CTkLabel(self.f_inputs, text="Registrar Costo y Margen Comercial por Categoría", font=("Arial", 14, "bold"), text_color="#1f538d").grid(row=0, column=0, columnspan=8, sticky="w", padx=15, pady=(10, 5))

        ctk.CTkLabel(self.f_inputs, text="Seleccione Categoría:", font=("Arial", 12, "bold")).grid(row=1, column=0, sticky="w", pady=5, padx=(15, 5))
        self.cats_assigned = []
        try:
            c = self.conn.cursor()
            c.execute("SELECT categoria_suministro FROM cotizacion_detalles WHERE codigo_cotizacion = %s", (self.codigo_cot,))
            for row in c.fetchall():
                txt_cat = str(row[0]).replace("('", "").replace("',)", "").replace("',", "").strip("() '\", ")
                if txt_cat and txt_cat not in self.cats_assigned:
                    self.cats_assigned.append(txt_cat)
        except Exception:
            pass
        if not self.cats_assigned:
            self.cats_assigned = ["No hay categorias disponibles"]
            
        self.cmb_cat_e = ctk.CTkComboBox(self.f_inputs, values=self.cats_assigned, state="readonly", width=180, command=self.filtrar_proveedores_por_categoria)
        self.cmb_cat_e.grid(row=1, column=1, sticky="w", pady=5, padx=5)
        self.cmb_cat_e.set(self.cats_assigned[0])

        ctk.CTkLabel(self.f_inputs, text="Proveedor asignado:", font=("Arial", 12, "bold")).grid(row=1, column=2, sticky="w", pady=5, padx=15)
        f_prov_accion = ctk.CTkFrame(self.f_inputs, fg_color="transparent")
        f_prov_accion.grid(row=1, column=3, sticky="w", pady=5, padx=5)
        
        self.cmb_p_list = ctk.CTkComboBox(f_prov_accion, values=["--- Seleccione Proveedor ---"], state="readonly", width=250)
        self.cmb_p_list.pack(side="left", padx=(0, 5))
        self.cmb_p_list.set("--- Seleccione Proveedor ---")

        def abrir_sistema_proveedores():
            try:
                import proveedores
                v_ext = ctk.CTkToplevel(self.v_prov)
                v_ext.after(100, lambda: maximizar_ventana(v_ext))
                v_ext.transient(self.v_prov)
                v_ext.grab_set()
                v_ext.focus_force()
                proveedores.SistemaProveedores(v_ext)
                self.v_prov.wait_window(v_ext)
                if not self.v_prov.winfo_exists() or not hasattr(self, 'cmb_cat_e') or not self.cmb_cat_e.winfo_exists():
                    return
                self.conn.commit()
                self.filtrar_proveedores_por_categoria()
            except Exception as e:
                try:
                    if self.v_prov.winfo_exists():
                        messagebox.showerror("Error", f"Falló la ejecución de Proveedores:\n{e}", parent=self.v_prov)
                except Exception:
                    pass

        ctk.CTkButton(f_prov_accion, text="[+] Crear Proveedor", width=120, command=abrir_sistema_proveedores).pack(side="left")

        ctk.CTkLabel(self.f_inputs, text="Cant.:", font=("Arial", 12, "bold")).grid(row=2, column=0, sticky="w", padx=(15, 2), pady=5)
        self.ent_cant = ctk.CTkEntry(self.f_inputs, width=60)
        self.ent_cant.grid(row=2, column=1, sticky="w", pady=5, padx=2)
        self.ent_cant.insert(0, "1")

        ctk.CTkLabel(self.f_inputs, text="P. Lista (S/.):", font=("Arial", 12, "bold")).grid(row=2, column=2, sticky="w", padx=(10, 2), pady=5)
        self.ent_p_lista = ctk.CTkEntry(self.f_inputs, width=100)
        self.ent_p_lista.grid(row=2, column=3, sticky="w", pady=5, padx=2)
        self.ent_p_lista.insert(0, "0.00")

        ctk.CTkLabel(self.f_inputs, text="Días Créd.:", font=("Arial", 12, "bold"), text_color="#1f538d").grid(row=2, column=5, sticky="e", padx=(15, 5), pady=5)
        self.ent_dias_credito = ctk.CTkEntry(self.f_inputs, width=60)
        self.ent_dias_credito.grid(row=2, column=6, sticky="w", padx=(5, 15), pady=5)
        self.ent_dias_credito.insert(0, "0")

        ctk.CTkLabel(self.f_inputs, text="P. Dscto (S/.):", font=("Arial", 12, "bold")).grid(row=3, column=0, sticky="w", pady=5, padx=(15, 2))
        self.ent_p_desc = ctk.CTkEntry(self.f_inputs, width=100)
        self.ent_p_desc.grid(row=3, column=1, sticky="w", pady=5, padx=2)
        self.ent_p_desc.insert(0, "0.00")

        ctk.CTkLabel(self.f_inputs, text="Tipo / Val. Ganancia:", font=("Arial", 12, "bold")).grid(row=3, column=2, sticky="w", pady=5, padx=(10, 2))
        f_gan_inline = ctk.CTkFrame(self.f_inputs, fg_color="transparent")
        f_gan_inline.grid(row=3, column=3, sticky="w", pady=5, padx=2)
        self.cmb_tipo_ganancia = ctk.CTkComboBox(f_gan_inline, values=["Monto Fijo", "Porcentaje (%)"], state="readonly", width=120)
        self.cmb_tipo_ganancia.pack(side="left", padx=(0, 2))
        self.cmb_tipo_ganancia.set("Monto Fijo")
        self.ent_val_g = ctk.CTkEntry(f_gan_inline, width=80)
        self.ent_val_g.pack(side="left")
        self.ent_val_g.insert(0, "0.00")

        ctk.CTkLabel(self.f_inputs, text="Tipo de Cambio ($):", font=("Arial", 12, "bold")).grid(row=3, column=5, sticky="e", padx=(15, 5), pady=5)
        self.var_tc_rastreador = tk.StringVar()
        self.var_tc_rastreador.trace_add("write", lambda *args: self._recalcular_con_retraso())
        self.ent_tc = ctk.CTkEntry(self.f_inputs, width=100, textvariable=self.var_tc_rastreador)
        self.ent_tc.grid(row=3, column=6, sticky="w", padx=(5, 15), pady=5)
        self.ent_tc.bind("<FocusOut>", lambda e: self.guardar_ajustes_globales_db())
        self.ent_tc.bind("<Return>", lambda e: self.guardar_ajustes_globales_db())

        ctk.CTkLabel(self.f_inputs, text="Forma de Pago (PDF):", font=("Arial", 12, "bold")).grid(row=4, column=5, sticky="e", padx=(15, 5), pady=(10, 5))
        self.ent_forma_pago = ctk.CTkEntry(self.f_inputs, width=320)
        self.ent_forma_pago.grid(row=4, column=6, sticky="w", padx=(5, 15), pady=(10, 5))
        self.ent_forma_pago.bind("<FocusOut>", lambda e: self.guardar_ajustes_globales_db())
        self.ent_forma_pago.bind("<Return>", lambda e: self.guardar_ajustes_globales_db())

        self.cargando_ventana = False
        self.cargar_ajustes_globales()

        f_notas_wrapper = ctk.CTkFrame(self.f_inputs, fg_color="transparent")
        f_notas_wrapper.grid(row=4, column=0, columnspan=4, rowspan=4, sticky="nw", pady=(12, 10), padx=15)
        f_header_notas = ctk.CTkFrame(f_notas_wrapper, fg_color="transparent")
        f_header_notas.pack(fill="x", side="top", pady=(0, 2))
        ctk.CTkLabel(f_header_notas, text="Solicitudes al Proveedor (Máx 15 líneas):", font=("Arial", 12, "bold")).pack(side="left", anchor="w")
        self.txt_p_notes = tk.Text(f_notas_wrapper, width=54, height=7, font=("Arial", 11), wrap="word", relief="solid", bd=1, highlightthickness=1, highlightbackground="#ccc", highlightcolor="#1f538d")
        f_estilos = crear_barra_formato(f_header_notas, self.txt_p_notes)
        f_estilos.pack(side="right", anchor="e")
        self.txt_p_notes.pack(fill="both", expand=True, side="top")
        self.lbl_p_contador = ctk.CTkLabel(self.f_inputs, text="Caracteres restantes: 750", font=("Arial", 10), text_color="#555")
        self.lbl_p_contador.grid(row=7, column=3, sticky="se", padx=10, pady=2)
        self.txt_p_notes.bind("<KeyRelease>", lambda e: self.lbl_p_contador.configure(text=f"Caracteres restantes: {max(0, 750 - len(texto_plano_sin_marcado(self.txt_p_notes.get('1.0', 'end-1c'))))}"))

        f_totales_centro = ctk.CTkFrame(self.f_inputs, border_width=1, border_color="#cccccc", fg_color="#f9f9f9")
        f_totales_centro.grid(row=5, column=5, columnspan=2, rowspan=3, sticky="nsew", padx=(15, 15), pady=(5, 12))
        ctk.CTkLabel(f_totales_centro, text="Resumen Económico Contable", font=("Arial", 13, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        self.lbl_tot_sub = ctk.CTkLabel(f_totales_centro, text="Total Venta al Cliente: S/ 0.00", font=("Arial", 12, "bold"), text_color="#111111")
        self.lbl_tot_sub.pack(anchor="w", padx=15, pady=2)
        self.lbl_tot_igv = ctk.CTkLabel(f_totales_centro, text="15% Fee Producción: S/ 0.00", font=("Arial", 12, "bold"), text_color="#444444")
        self.lbl_tot_igv.pack(anchor="w", padx=15, pady=2)
        self.lbl_tot_gran = ctk.CTkLabel(f_totales_centro, text="Gran Total: S/ 0.00", font=("Arial", 14, "bold"), text_color="#e62060")
        self.lbl_tot_gran.pack(anchor="w", padx=15, pady=2)
        self.lbl_tot_usd = ctk.CTkLabel(f_totales_centro, text="Total Equivalente: $ 0.00 USD", font=("Arial", 12, "bold"), text_color="#222222")
        self.lbl_tot_usd.pack(anchor="w", padx=15, pady=(5, 10))

        def disparar_exportacion_pdf_alberto():
            self.guardar_ajustes_globales_db()
            try:
                try:
                    import final_cotizaciones as motor_pdf
                except Exception:
                    import cotizaciones as motor_pdf
                    
                conn_pdf = conectar_db(silencioso=True)
                if not conn_pdf: return
                try:
                    exito, mensaje_o_ruta = motor_pdf.generar_reporte_cotizacion_pdf(conn_pdf, self.codigo_cot)
                    if exito:
                        cache_sistema.invalidar()
                        registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Exportó a PDF la Matriz Cotización N° {self.codigo_cot}")
                        messagebox.showinfo("Éxito de Exportación", f"¡Excelente!\nLa cotización oficial N° {self.codigo_cot} ha sido fabricada.\n\nArchivo guardado:\n{os.path.basename(mensaje_o_ruta)}", parent=self.v_prov)
                        try:
                            abrir_documento(mensaje_o_ruta)
                        except Exception: pass
                    else:
                        messagebox.showerror("Error de Creación", f"No se pudo maquetar el reporte contable:\n\n{mensaje_o_ruta}", parent=self.v_prov)
                finally:
                    liberar_conexion(conn_pdf)
            except Exception as e:
                messagebox.showerror("Error de Archivo", f"No se encontró el generador de PDF.\n\nDetalle: {str(e)}", parent=self.v_prov)

        btn_pdf = ctk.CTkButton(self.f_inputs, text="[ PDF ] Generar Cotización Oficial", font=("Arial", 14, "bold"), height=40, fg_color=COLOR_PRIMARIO, hover_color="#b71c1c", command=disparar_exportacion_pdf_alberto)
        btn_pdf.grid(row=8, column=0, columnspan=8, sticky="ew", padx=15, pady=(15, 10))

        self.f_b_matriz = ctk.CTkFrame(self.v_prov, fg_color="transparent")
        self.f_b_matriz.pack(fill="x", padx=15, pady=5)
        ctk.CTkButton(self.f_b_matriz, text="<< Atrás", width=100, fg_color="#e0e0e0", text_color="black", hover_color="#c8c8c8", command=self.regresar_a_etapa2).pack(side="left", padx=(0, 5))
        self.btn_toggle_vista = ctk.CTkButton(self.f_b_matriz, text="[ + ] Pantalla Completa", width=140, fg_color="#8E44AD", hover_color="#732D91", command=self.toggle_vista_matriz)
        self.btn_toggle_vista.pack(side="left", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="[ Editar ] Costo/Margen", width=150, fg_color="#f39c12", hover_color="#e67e22", command=self.modificar_proveedor_matriz).pack(side="left", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="[ X ] Retirar Ítem", width=120, fg_color="#D32F2F", hover_color="#B71C1C", command=self.retirar_proveedor_matriz).pack(side="left", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="▲ Subir", width=70, fg_color="#e0e0e0", text_color="black", hover_color="#c8c8c8", command=lambda: self.mover_renglon_matriz("ARRIBA")).pack(side="left", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="▼ Bajar", width=70, fg_color="#e0e0e0", text_color="black", hover_color="#c8c8c8", command=lambda: self.mover_renglon_matriz("ABAJO")).pack(side="left", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="[ OK ] Finalizar Matriz", width=150, command=self._cerrar_ventana).pack(side="right", padx=5)
        ctk.CTkButton(self.f_b_matriz, text="[ + ] Asignar a Matriz", width=150, fg_color="#228B22", hover_color="#1E761E", command=self.asociar_proveedor_a_matriz).pack(side="right", padx=5)

        self.f_grid = ctk.CTkFrame(self.v_prov, corner_radius=10)
        self.f_grid.pack(fill="both", expand=True, padx=15, pady=(5, 10))
        ctk.CTkLabel(self.f_grid, text="Matriz Comparativa y Margen Final de Venta", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=10)
        f_headers = ctk.CTkFrame(self.f_grid, fg_color="#e0e0e0", corner_radius=5)
        f_headers.pack(fill="x", padx=10, pady=(0, 5))
        anchos = [("ID", 35), ("Categoría", 160), ("Proveedor", 160), ("Cant.", 55), ("Precio Lista", 90), ("Con Dscto", 90), ("P. Venta Tot.", 110)]
        for text, w in anchos:
            ctk.CTkLabel(f_headers, text=text, font=("Arial", 11, "bold"), width=w, anchor="center").pack(side="left", padx=2, pady=5)
        ctk.CTkLabel(f_headers, text="Solicitudes / Notas", font=("Arial", 11, "bold"), anchor="center").pack(side="left", fill="x", expand=True, padx=2, pady=5)
        self.f_rows_dinamicas = ctk.CTkScrollableFrame(self.f_grid, fg_color="transparent")
        self.f_rows_dinamicas.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.filtrar_proveedores_por_categoria()
        self.cargar_grid_proveedores()

    # =======================================================
    # TIPO DE CAMBIO EN VIVO (EN SEGUNDO PLANO, NO CONGELA)
    # =======================================================
    def obtener_tipo_cambio_en_vivo(self):
        try:
            url = "https://api.apis.net.pe/v1/tipo-cambio-sunat"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                return float(data.get("venta", 3.75))
        except Exception:
            try:
                url = "https://open.er-api.com/v6/latest/USD"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=3) as response:
                    data = json.loads(response.read().decode())
                    return float(data["rates"]["PEN"])
            except Exception:
                return None

    def _cargar_tipo_cambio_en_vivo(self):
        def tarea():
            tc = self.obtener_tipo_cambio_en_vivo()

            def aplicar():
                try:
                    if not self.v_prov.winfo_exists():
                        return
                except Exception:
                    return
                if tc:
                    self.ent_tc.delete(0, tk.END)
                    self.ent_tc.insert(0, f"{tc:.3f}")
                self.guardar_ajustes_globales_db()

            self.root.after(0, aplicar)

        threading.Thread(target=tarea, daemon=True).start()

    # =======================================================
    # AJUSTES GLOBALES (TC Y FORMA DE PAGO)
    # =======================================================
    def cargar_ajustes_globales(self):
        try:
            c = self.conn.cursor()
            c.execute("SELECT tipo_cambio, forma_pago FROM cotizaciones WHERE codigo_cotizacion = %s", (self.codigo_cot,))
            res = c.fetchone()
            self.ent_tc.delete(0, tk.END)
            self.ent_tc.insert(0, str(res[0]) if res and res[0] and float(res[0]) > 0 else "3.750")
            self.ent_forma_pago.delete(0, tk.END)
            self.ent_forma_pago.insert(0, str(res[1]) if res and res[1] else "50% adelantado, 50% a 30 días de la primera factura.")
        except Exception:
            try:
                self.ent_tc.insert(0, "3.750")
                self.ent_forma_pago.insert(0, "50% adelantado, 50% a 30 días de la primera factura.")
            except Exception:
                pass
        # El tipo de cambio en vivo se carga en segundo plano
        self._cargar_tipo_cambio_en_vivo()

    def guardar_ajustes_globales_db(self):
        if not self.conn:
            return
        try:
            c = self.conn.cursor()
            val_tc = 3.75
            if self.ent_tc.get().strip():
                try:
                    val_tc = float(self.ent_tc.get())
                except ValueError:
                    pass
            val_forma_pago = self.ent_forma_pago.get().strip()
            c.execute("UPDATE cotizaciones SET tipo_cambio = %s, forma_pago = %s WHERE codigo_cotizacion = %s", (val_tc, val_forma_pago, self.codigo_cot))
            self.conn.commit()
        except Exception:
            pass

    # =======================================================
    # TOTALES CON RETRASO INTELIGENTE + HILO (NO CONGELA)
    # =======================================================
    def _recalcular_con_retraso(self, *args):
        if hasattr(self, "_recalc_job"):
            try:
                self.root.after_cancel(self._recalc_job)
            except Exception:
                pass
        self._recalc_job = self.root.after(300, self.actualizar_bloque_totales_pantalla)

    def actualizar_bloque_totales_pantalla(self):
        if getattr(self, "cargando_ventana", False):
            return
        try:
            tc = float(self.ent_tc.get())
        except Exception:
            tc = 3.75
        codigo = self.codigo_cot

        def tarea():
            subtotal = 0.0
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("SELECT precio_final_venta FROM cotizacion_proveedores WHERE codigo_cotizacion = %s", (codigo,))
                    subtotal = sum(float(r[0]) for r in c.fetchall() if r and r[0])
                except Exception:
                    subtotal = 0.0
                finally:
                    liberar_conexion(conn)
            self.root.after(0, lambda s=subtotal, t=tc: self._pintar_totales(s, t))

        threading.Thread(target=tarea, daemon=True).start()

    def _pintar_totales(self, subtotal, tc):
        try:
            if not self.v_prov.winfo_exists():
                return
        except Exception:
            return
        fee = subtotal * 0.15
        try:
            tc_val = float(self.ent_tc.get())
        except Exception:
            tc_val = tc if tc else 3.75
        self.lbl_tot_sub.configure(text=f"Total Venta al Cliente: S/ {subtotal:,.2f}")
        self.lbl_tot_igv.configure(text=f"15% Fee Producción: S/ {fee:,.2f}")
        self.lbl_tot_gran.configure(text=f"Gran Total: S/ {subtotal + fee:,.2f}")
        self.lbl_tot_usd.configure(text=f"Total Equivalente: $ {(subtotal + fee) / tc_val:,.2f} USD")

    # =======================================================
    # VISTAS Y NAVEGACIÓN
    # =======================================================
    def toggle_vista_matriz(self):
        if not self.matriz_expandida:
            self.f_inputs.pack_forget()
            self.btn_toggle_vista.configure(text="[ - ] Mostrar Formulario", fg_color="#2980B9", hover_color="#1A5276")
            self.matriz_expandida = True
        else:
            self.f_inputs.pack(fill="x", padx=15, pady=5, after=self.f_info)
            self.btn_toggle_vista.configure(text="[ + ] Pantalla Completa", fg_color="#8E44AD", hover_color="#732D91")
            self.matriz_expandida = False

    def _liberar_conn(self):
        try:
            if self.conn:
                liberar_conexion(self.conn)
        except Exception:
            pass
        self.conn = None

    def _cerrar_ventana(self):
        self._liberar_conn()
        try:
            self.v_prov.destroy()
        except Exception:
            pass
        if self.callback_on_close:
            self.callback_on_close()

    def regresar_a_etapa2(self):
        self._liberar_conn()
        self.v_prov.destroy()
        if hasattr(self.parent_ventana, 'abrir_ventana_editar'):
            self.parent_ventana.abrir_ventana_editar(codigo_directo=self.codigo_cot)
        elif self.callback_on_close:
            self.callback_on_close()

    def refrescar_combobox_descarte(self):
        if not self.conn:
            return
        try:
            c = self.conn.cursor()
            c.execute("SELECT categoria_suministro FROM cotizacion_detalles WHERE codigo_cotizacion = %s", (self.codigo_cot,))
            nuevas = []
            for r in c.fetchall():
                cat = str(r[0]).replace("('", "").replace("',)", "").replace("',", "").strip("() '\",")
                if cat and cat not in nuevas:
                    nuevas.append(cat)
            if not nuevas:
                nuevas = ["No hay categorias disponibles"]
            sel_actual = self.cmb_cat_e.get()
            self.cmb_cat_e.configure(values=nuevas)
            if sel_actual in nuevas:
                self.cmb_cat_e.set(sel_actual)
            else:
                self.cmb_cat_e.set(nuevas[0])
        except Exception:
            pass

    # 🚀 FIX: FILTRO PROVEEDORES ASÍNCRONO + CACHÉ CON MATCH DE CATEGORÍA
    def filtrar_proveedores_por_categoria(self, choice=None):
        cat_sel = str(self.cmb_cat_e.get()).strip().replace("('", "").replace("',)", "").replace("',", "").strip("() '\",")
        
        clave_cache = "lista_proveedores_completos_con_cat"
        lista_completa = cache_sistema.obtener(clave_cache)
        
        if lista_completa is not None:
            self._filtrar_y_aplicar(lista_completa, cat_sel)
        else:
            self.cmb_p_list.set("Cargando...")
            def tarea_provs():
                data = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        try:
                            # 1. Intentamos obtener la categoría del proveedor
                            cursor.execute("SELECT nombre, categoria FROM proveedores ORDER BY nombre ASC")
                            data = [(str(r[0]).strip(), str(r[1]).strip() if r[1] else "") for r in cursor.fetchall() if r[0]]
                        except Exception:
                            conn.rollback()
                            try:
                                # 2. Si falla, tal vez la columna se llama 'rubro'
                                cursor.execute("SELECT nombre, rubro FROM proveedores ORDER BY nombre ASC")
                                data = [(str(r[0]).strip(), str(r[1]).strip() if r[1] else "") for r in cursor.fetchall() if r[0]]
                            except Exception:
                                conn.rollback()
                                # 3. Si no existe ninguna, traemos solo los nombres (Fallback de seguridad)
                                cursor.execute("SELECT nombre FROM proveedores ORDER BY nombre ASC")
                                data = [(str(r[0]).strip(), "") for r in cursor.fetchall() if r[0]]
                                
                        cache_sistema.guardar(clave_cache, data)
                    except: pass
                    finally: liberar_conexion(conn)
                    
                if hasattr(self, 'root') and self.v_prov.winfo_exists():
                    self.root.after(0, lambda: self._filtrar_y_aplicar(data, cat_sel))
                    
            threading.Thread(target=tarea_provs, daemon=True).start()

    def _filtrar_y_aplicar(self, lista_completa, cat_sel):
        if not getattr(self, 'v_prov', None) or not self.v_prov.winfo_exists(): return
        
        prov_actual = self.cmb_p_list.get() # Guardar el seleccionado
        
        if not cat_sel or cat_sel == "No hay categorias disponibles":
            lista = ["--- Seleccione Proveedor ---"] + [n for n, c in lista_completa]
        else:
            # Filtramos estrictamente comparando la categoría solicitada vs la registrada en proveedor
            provs_filtrados = [n for n, c in lista_completa if cat_sel.lower() in c.lower()]
            
            if provs_filtrados:
                lista = ["--- Seleccione Proveedor ---"] + provs_filtrados
            else:
                # Si la categoría no coincide con ningún proveedor, mostramos todos con una advertencia visual
                lista = ["--- Seleccione (No hay del rubro) ---"] + [n for n, c in lista_completa]
                
        self.cmb_p_list.configure(values=lista)
        
        # Restaurar la selección si sigue siendo válida en la nueva lista
        if prov_actual in lista and "Seleccione" not in prov_actual:
            self.cmb_p_list.set(prov_actual)
        else:
            self.cmb_p_list.set(lista[0])

    # =======================================================
    # OPERACIONES DE MATRIZ (CON BITÁCORA)
    # =======================================================
    def asociar_proveedor_a_matriz(self):
        if not self.conn:
            return
        cat, prov = self.cmb_cat_e.get().strip(), self.cmb_p_list.get().strip()
        
        # Validación de combobox sin seleccionar o cabeceras falsas
        if prov in ["Seleccione un proveedor", "Haga clic en Cargar Proveedores", "", "--- Sin proveedores específicos, mostrando todos ---", "--- Seleccione Proveedor ---", "--- Seleccione (No hay del rubro) ---"]:
            messagebox.showwarning("Atención", "Por favor despliegue la lista y seleccione un proveedor válido.", parent=self.v_prov)
            return
            
        try:
            cant = int(self.ent_cant.get().strip())
            dias_cred = int(self.ent_dias_credito.get().strip() or 0)
            pl, pd, vg = float(self.ent_p_lista.get()), float(self.ent_p_desc.get()), float(self.ent_val_g.get())
        except ValueError:
            messagebox.showwarning("Error numérico", "Importes, días o cantidades inválidas.", parent=self.v_prov)
            return
            
        if pd > pl:
            messagebox.showwarning("Alerta", "El descuento no puede superar al precio de lista.", parent=self.v_prov)
            return
            
        p_unid = pl + vg if self.cmb_tipo_ganancia.get() == "Monto Fijo" else pl * (1 + (vg / 100.0))
        p_final_venta = p_unid * cant
        t_ganancia_db = "Monto Fijo" if self.cmb_tipo_ganancia.get() == "Monto Fijo" else "Porcentaje"
        notes = self.txt_p_notes.get("1.0", "end-1c").strip()
        c = self.conn.cursor()
        
        try:
            c.execute("""
                INSERT INTO cotizacion_proveedores 
                (codigo_cotizacion, categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, tipo_ganancia, valor_ganancia, precio_final_venta, notes_negociacion, cantidad, dias_credito) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (self.codigo_cot, cat, prov, pl, pd, t_ganancia_db, vg, p_final_venta, notes, cant, dias_cred))
            self.conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Agregó proveedor '{prov}' a Cotización N° {self.codigo_cot}")
            self.actualizar_bloque_totales_pantalla()
            self.ent_cant.delete(0, tk.END)
            self.ent_cant.insert(0, "1")
            self.ent_dias_credito.delete(0, tk.END)
            self.ent_dias_credito.insert(0, "0")
            self.ent_p_lista.delete(0, tk.END)
            self.ent_p_lista.insert(0, "0.00")
            self.ent_p_desc.delete(0, tk.END)
            self.ent_p_desc.insert(0, "0.00")
            self.ent_val_g.delete(0, tk.END)
            self.ent_val_g.insert(0, "0.00")
            self.txt_p_notes.delete("1.0", tk.END)
            self.cargar_grid_proveedores()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo asignar al proveedor:\n{e}", parent=self.v_prov)

    def retirar_proveedor_matriz(self):
        if not self.conn:
            return
        if not self.fila_matriz_seleccionada:
            messagebox.showwarning("Advertencia", "Seleccione una fila de la matriz primero.", parent=self.v_prov)
            return
        if messagebox.askyesno("Confirmar", "¿Retirar al proveedor seleccionado?", parent=self.v_prov):
            try:
                c = self.conn.cursor()
                c.execute("DELETE FROM cotizacion_proveedores WHERE id=%s", (self.fila_matriz_seleccionada[0],))
                self.conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Retiró ítem de la Cotización N° {self.codigo_cot}")
            except Exception as e:
                self.conn.rollback()
                messagebox.showerror("Error", f"No se pudo eliminar:\n{e}", parent=self.v_prov)
                return
            self.cargar_grid_proveedores()

    def modificar_proveedor_matriz(self):
        if not self.conn:
            return
        if not self.fila_matriz_seleccionada:
            messagebox.showwarning("Requerido", "Seleccione una fila primero.", parent=self.v_prov)
            return
        id_mat = self.fila_matriz_seleccionada[0]
        c = self.conn.cursor()
        c.execute("SELECT precio_lista, precio_descuento, tipo_ganancia, valor_ganancia, notes_negociacion, cantidad, dias_credito FROM cotizacion_proveedores WHERE id=%s", (id_mat,))
        datos = c.fetchone()
        if not datos:
            return
        v_m = ctk.CTkToplevel(self.v_prov)
        v_m.title("Modificar Costos")
        v_m.geometry("480x600")
        v_m.grab_set()
        f_m = ctk.CTkFrame(v_m, corner_radius=10)
        f_m.pack(fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(f_m, text="Cantidad:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        ent_m_cant = ctk.CTkEntry(f_m, width=100)
        ent_m_cant.pack(anchor="w", padx=10, pady=2)
        ent_m_cant.insert(0, str(datos[5] if len(datos) > 5 and datos[5] else 1))
        ctk.CTkLabel(f_m, text="Días de Crédito:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        ent_m_dcred = ctk.CTkEntry(f_m, width=100)
        ent_m_dcred.pack(anchor="w", padx=10, pady=2)
        ent_m_dcred.insert(0, str(datos[6] if len(datos) > 6 and datos[6] else 0))
        ctk.CTkLabel(f_m, text="Precio Lista (S/.):", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        ent_m_lista = ctk.CTkEntry(f_m, width=200)
        ent_m_lista.pack(anchor="w", padx=10, pady=2)
        ent_m_lista.insert(0, f"{datos[0]:.2f}")
        ctk.CTkLabel(f_m, text="Precio con Descuento (S/.):", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        ent_m_desc = ctk.CTkEntry(f_m, width=200)
        ent_m_desc.pack(anchor="w", padx=10, pady=2)
        ent_m_desc.insert(0, f"{datos[1]:.2f}")
        ctk.CTkLabel(f_m, text="Tipo de Ganancia:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        cmb_m_tipo = ctk.CTkComboBox(f_m, values=["Monto Fijo", "Porcentaje (%)"], state="readonly", width=200)
        cmb_m_tipo.pack(anchor="w", padx=10, pady=2)
        cmb_m_tipo.set(datos[2])
        ctk.CTkLabel(f_m, text="Valor de Ganancia:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=(5, 2))
        ent_m_val = ctk.CTkEntry(f_m, width=200)
        ent_m_val.pack(anchor="w", padx=10, pady=2)
        ent_m_val.insert(0, f"{datos[3]:.2f}")
        f_m_header = ctk.CTkFrame(f_m, fg_color="transparent")
        f_m_header.pack(fill="x", pady=(10, 2), padx=10)
        ctk.CTkLabel(f_m_header, text="Solicitudes / Notas:", font=("Arial", 12, "bold")).pack(side="left")
        txt_m_notas = tk.Text(f_m, width=50, height=4, font=("Arial", 11), wrap="word", relief="solid", bd=1, highlightthickness=1, highlightbackground="#ccc")
        f_barra = crear_barra_formato(f_m_header, txt_m_notas)
        f_barra.pack(side="right")
        txt_m_notas.pack(fill="x", padx=10, pady=2)
        txt_m_notas.insert("1.0", str(datos[4]) if datos[4] else "")

        def ejecutar_update_matriz():
            try:
                mc = int(ent_m_cant.get().strip())
                m_dcred = int(ent_m_dcred.get().strip())
                ml, md, mv = float(ent_m_lista.get()), float(ent_m_desc.get()), float(ent_m_val.get())
            except ValueError:
                messagebox.showwarning("Error numérico", "Valores inválidos.", parent=v_m)
                return
            p_unid = ml + mv if cmb_m_tipo.get() == "Monto Fijo" else ml * (1 + (mv / 100))
            p_final = p_unid * mc
            c_upd = self.conn.cursor()
            c_upd.execute("UPDATE cotizacion_proveedores SET precio_lista=%s, precio_descuento=%s, tipo_ganancia=%s, valor_ganancia=%s, precio_final_venta=%s, notes_negociacion=%s, cantidad=%s, dias_credito=%s WHERE id=%s", (ml, md, cmb_m_tipo.get(), mv, p_final, txt_m_notas.get("1.0", "end-1c").strip(), mc, m_dcred, id_mat))
            self.conn.commit()
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Modificó márgenes en ítem de Cotización N° {self.codigo_cot}")
            v_m.destroy()
            self.cargar_grid_proveedores()

        ctk.CTkButton(f_m, text="[ Guardar Cambios ]", command=ejecutar_update_matriz).pack(pady=15)

    # =======================================================
    # PINTADO DE LA MATRIZ
    # =======================================================
    def cargar_grid_proveedores(self, id_a_seleccionar=None):
        if not self.conn:
            return
        for widget in self.f_rows_dinamicas.winfo_children():
            widget.destroy()
        self.fila_matriz_seleccionada = None
        self.lista_widgets_filas = []
        c = self.conn.cursor()
        c.execute("SELECT id, categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, precio_final_venta, notes_negociacion, cantidad FROM cotizacion_proveedores WHERE codigo_cotizacion = %s ORDER BY id ASC", (self.codigo_cot,))
        registros = c.fetchall()
        if not registros:
            self.actualizar_bloque_totales_pantalla()
            ctk.CTkLabel(self.f_rows_dinamicas, text="No hay costos asignados a este evento aún.", font=("Arial", 12, "italic"), text_color="#888").pack(pady=20)
            return
        self.actualizar_bloque_totales_pantalla()
        for i, r in enumerate(registros, start=1):
            id_real, cat_r, prov_r, pl_r, pd_r, pf_r, notas_r, cant_r = r[0], r[1], r[2], r[3], r[4], r[5], r[6], (r[7] if len(r) > 7 and r[7] else 1)
            cat_limpia = str(cat_r).strip("() '\",")
            prov_limpio = str(prov_r).strip("() '\",")
            data_pack = (id_real, cat_limpia, prov_limpio, pl_r, pd_r, pf_r, notas_r, cant_r)
            f_row = ctk.CTkFrame(self.f_rows_dinamicas, fg_color="#ffffff", border_width=1, border_color="#e0e0e0", corner_radius=0)
            f_row.pack(fill="x", pady=2)

            def marcar_seleccion_f(event, f=f_row, d=data_pack):
                for child in self.f_rows_dinamicas.winfo_children():
                    child.configure(fg_color="#ffffff")
                    for sub in child.winfo_children():
                        if isinstance(sub, tk.Text):
                            sub.config(bg="#ffffff")
                f.configure(fg_color="#cfe2ff")
                for sub in f.winfo_children():
                    if isinstance(sub, tk.Text):
                        sub.config(bg="#cfe2ff")
                self.fila_matriz_seleccionada = d

            f_row.bind("<Button-1>", marcar_seleccion_f)
            lbl_id = ctk.CTkLabel(f_row, text=str(i), font=("Arial", 11), width=35, anchor="center")
            lbl_id.pack(side="left", padx=2, fill="y")
            lbl_id.bind("<Button-1>", marcar_seleccion_f)
            anchos = [(cat_limpia, 160, "w"), (prov_limpio, 160, "w"), (str(cant_r), 55, "center"), (f"S/. {pl_r:.2f}", 90, "e"), (f"S/. {pd_r:.2f}", 90, "e"), (f"S/. {pf_r:.2f}", 110, "e")]
            for text, w, align in anchos:
                lbl = ctk.CTkLabel(f_row, text=text, font=("Arial", 11), width=w, anchor=align)
                lbl.pack(side="left", padx=2, fill="y")
                lbl.bind("<Button-1>", marcar_seleccion_f)
            texto_nota = str(notas_r) if notas_r else "-"
            conteo_lineas = texto_nota.count('\n') + 1
            for linea_texto in texto_nota.split('\n'):
                conteo_lineas += len(texto_plano_sin_marcado(linea_texto)) // 65
            txt_notas = tk.Text(f_row, height=max(3, conteo_lineas), font=("Arial", 10), wrap="word", bg="#ffffff", bd=0, highlightthickness=0)
            configurar_tags_formato(txt_notas, tam=10)
            insertar_texto_formateado(txt_notas, texto_nota)
            txt_notas.pack(side="left", fill="both", expand=True, padx=10, pady=5)
            txt_notas.bind("<Button-1>", marcar_seleccion_f)
            f_row.data_pack = data_pack
            self.lista_widgets_filas.append(f_row)
            if id_a_seleccionar == id_real:
                marcar_seleccion_f(None, f_row, data_pack)

    def mover_renglon_matriz(self, direccion):
        if not self.conn:
            return
        if not self.fila_matriz_seleccionada:
            return
        idx_act = -1
        widget_sel = None
        for idx, widget in enumerate(self.lista_widgets_filas):
            if widget.data_pack == self.fila_matriz_seleccionada:
                idx_act, widget_sel = idx, widget
                break
        if idx_act == -1:
            return
        idx_dest = idx_act - 1 if direccion == "ARRIBA" else idx_act + 1
        if idx_dest < 0 or idx_dest >= len(self.lista_widgets_filas):
            return
        id_act, id_dest = widget_sel.data_pack[0], self.lista_widgets_filas[idx_dest].data_pack[0]
        c = self.conn.cursor()
        try:
            c.execute("SELECT categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, precio_final_venta, notes_negociacion, cantidad, dias_credito FROM cotizacion_proveedores WHERE id = %s", (id_act,))
            d_act = c.fetchone()
            c.execute("SELECT categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, precio_final_venta, notes_negociacion, cantidad, dias_credito FROM cotizacion_proveedores WHERE id = %s", (id_dest,))
            d_dest = c.fetchone()
            if d_act and d_dest:
                query = "UPDATE cotizacion_proveedores SET categoria_suministro=%s, proveedor_nombre=%s, precio_lista=%s, precio_descuento=%s, precio_final_venta=%s, notes_negociacion=%s, cantidad=%s, dias_credito=%s WHERE id=%s"
                c.execute(query, d_dest + (id_act,))
                c.execute(query, d_act + (id_dest,))
                self.conn.commit()
                cache_sistema.invalidar()
        except Exception:
            self.conn.rollback()
        self.cargar_grid_proveedores(id_a_seleccionar=id_dest)


if __name__ == "__main__":
    pass