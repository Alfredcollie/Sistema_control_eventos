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
# CLASE PRINCIPAL
# =========================================================
_SCHEMA_COT_OK = False

class VentanaCotizaciones:
    def __init__(self, root):
        self.root = root
        self.usuario_activo = "Desconocido"
        
        # 🚀 VARIABLES PAGINACIÓN
        self.pagina_actual = 1
        self.registros_por_pagina = 50

        if hasattr(self.root, 'title'):
            self.root.title("Gestión de Cotizaciones - BLACK CUBE")
        if hasattr(self.root, 'geometry') and isinstance(self.root, (tk.Tk, ctk.CTkToplevel)):
            self.root.geometry("1200x720")

        # 🚀 FIX: AUTO-CURACIÓN SÍNCRONA (EVITA CONGELAR / CORROMPER CACHÉ)
        global _SCHEMA_COT_OK
        if not _SCHEMA_COT_OK:
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS fecha_evento VARCHAR(50) DEFAULT ''")
                    c.execute("ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS locacion_evento VARCHAR(255) DEFAULT ''")
                    c.execute("ALTER TABLE clientes ADD COLUMN IF NOT EXISTS razon_comercial VARCHAR(255) DEFAULT ''")
                    conn.commit()
                    _SCHEMA_COT_OK = True
                except Exception:
                    conn.rollback()
                finally:
                    liberar_conexion(conn)

        self.crear_interfaz()

    def abrir_calendario(self, entry_objetivo, ventana_padre=None):
        padre_final = ventana_padre if ventana_padre else self.root
        CalendarioNativo(padre_final, entry_objetivo)

    def crear_interfaz(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", rowheight=28, font=("Arial", 10))
        style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background="#f0f0f0", font=("Arial", 10, "bold"))
        if hasattr(self.root, 'option_add'):
            self.root.option_add('*tearOff', False)

        frame_izq = ctk.CTkFrame(self.root, corner_radius=10)
        frame_izq.pack(side="left", fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(frame_izq, text="Registro de Nueva Cotización", font=("Arial", 15, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(15, 5))

        frame_der = ctk.CTkFrame(self.root, corner_radius=10)
        frame_der.pack(side="right", fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(frame_der, text="Bandeja de Cotizaciones Realizadas", font=("Arial", 15, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(15, 5))

        ctk.CTkLabel(frame_izq, text="Cotizar a nombre de:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.var_tipo_cliente = tk.StringVar(value="Razón Social")
        self.combo_tipo_cliente = ctk.CTkComboBox(frame_izq, values=["Razón Social", "Razón Comercial"], variable=self.var_tipo_cliente, command=self.cambiar_tipo_cliente, font=("Arial", 12))
        self.combo_tipo_cliente.pack(fill="x", padx=15, pady=2)

        self.lbl_empresa_dinamico = ctk.CTkLabel(frame_izq, text="Seleccione Empresa (Razón Social):", font=("Arial", 12, "bold"))
        self.lbl_empresa_dinamico.pack(anchor="w", padx=15, pady=(10, 2))

        self.var_empresa = tk.StringVar()
        frame_combo = ctk.CTkFrame(frame_izq, fg_color="transparent")
        frame_combo.pack(fill="x", padx=15, pady=2)
        self.combo_empresa = ctk.CTkComboBox(frame_combo, variable=self.var_empresa, font=("Arial", 12))
        self.combo_empresa.pack(side="left", fill="x", expand=True)
        self.combo_empresa.bind("<Button-1>", lambda e: self.autocompletar_clientes_combo())
        btn_refresh_cli = ctk.CTkButton(frame_combo, text="[ O ]", width=40, fg_color="#e0e0e0", text_color="black", command=self.autocompletar_clientes_combo)
        btn_refresh_cli.pack(side="right", padx=5)

        ctk.CTkLabel(frame_izq, text="Nombre del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.ent_evento = ctk.CTkEntry(frame_izq, font=("Arial", 12))
        self.ent_evento.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(frame_izq, text="Fecha del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        f_fecha = ctk.CTkFrame(frame_izq, fg_color="transparent")
        f_fecha.pack(fill="x", padx=15, pady=2)
        self.ent_fecha_evento = ctk.CTkEntry(f_fecha, font=("Arial", 12), placeholder_text="DD/MM/AAAA")
        self.ent_fecha_evento.pack(side="left", fill="x", expand=True)
        btn_cal = ctk.CTkButton(f_fecha, text="[ 📅 ]", width=60, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha_evento, self.root))
        btn_cal.pack(side="right", padx=(5, 0))

        ctk.CTkLabel(frame_izq, text="Locación del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.ent_locacion = ctk.CTkEntry(frame_izq, font=("Arial", 12), placeholder_text="Ej: Hacienda Los Ficus")
        self.ent_locacion.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(frame_izq, text="Descripción General:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.txt_descripcion = ctk.CTkTextbox(frame_izq, height=100, font=("Arial", 12), border_width=1)
        self.txt_descripcion.pack(fill="x", padx=15, pady=2)

        btn_guardar = ctk.CTkButton(frame_izq, text="[ + ] Registrar Nueva Cotización", font=("Arial", 13, "bold"), height=40, command=self.registrar_cotizacion)
        btn_guardar.pack(fill="x", padx=15, pady=25)

        frame_busqueda = ctk.CTkFrame(frame_der, fg_color="transparent")
        frame_busqueda.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(frame_busqueda, text="Buscar: ", font=("Arial", 12, "bold")).pack(side="left")
        self.ent_buscar = ctk.CTkEntry(frame_busqueda, font=("Arial", 12), placeholder_text="Código, Empresa o Evento...")
        self.ent_buscar.pack(side="left", fill="x", expand=True, padx=5)
        
        self.ent_buscar.bind("<KeyRelease>", lambda e: self.filtrar_con_retraso())
        self.ent_buscar.bind("<Return>", lambda e: self.cargar_cotizaciones_tabla(reset_pagina=True))

        frame_tabla = ctk.CTkFrame(frame_der, fg_color="transparent")
        frame_tabla.pack(fill="both", expand=True, padx=15, pady=5)
        columnas = ("num", "id", "codigo", "cliente", "evento", "fecha_e", "status")
        self.tree_cot = ttk.Treeview(frame_tabla, columns=columnas, show="headings")
        self.tree_cot.heading("num", text="N°", anchor="center")
        self.tree_cot.heading("id", text="ID")
        self.tree_cot.heading("codigo", text="Código")
        self.tree_cot.heading("cliente", text="Empresa")
        self.tree_cot.heading("evento", text="Evento")
        self.tree_cot.heading("fecha_e", text="Fecha Evento")
        self.tree_cot.heading("status", text="Status")
        
        self.tree_cot.column("num", width=40, anchor="center")
        self.tree_cot.column("id", width=0, stretch=tk.NO)
        self.tree_cot.column("codigo", width=90, anchor="center")
        self.tree_cot.column("cliente", width=130, anchor="w")
        self.tree_cot.column("evento", width=130, anchor="w")
        self.tree_cot.column("fecha_e", width=90, anchor="center")
        self.tree_cot.column("status", width=90, anchor="center")
        self.tree_cot.config(displaycolumns=("num", "codigo", "cliente", "evento", "fecha_e", "status"))
        
        self.tree_cot.tag_configure("No aprobada", background="#f8d7da", foreground="#721c24")
        self.tree_cot.tag_configure("En evaluación", background="#fff3cd", foreground="#856404")
        self.tree_cot.tag_configure("Aprobada", background="#d4edda", foreground="#155724")
        self.tree_cot.tag_configure("En desarrollo", background="#cce5ff", foreground="#004085")

        scrollbar = ctk.CTkScrollbar(frame_tabla, orientation="vertical", command=self.tree_cot.yview)
        self.tree_cot.configure(yscrollcommand=scrollbar.set)
        self.tree_cot.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(5, 0))

        # 🚀 BOTONES DE PAGINACIÓN
        f_paginacion = ctk.CTkFrame(frame_der, fg_color="transparent")
        f_paginacion.pack(fill="x", padx=15, pady=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        frame_leyenda = ctk.CTkFrame(frame_der, fg_color="transparent")
        frame_leyenda.pack(fill="x", padx=15, pady=(10, 10))
        ctk.CTkLabel(frame_leyenda, text="■ No aprobada", text_color="#721c24", fg_color="#f8d7da", font=("Arial", 11, "bold"), corner_radius=5).pack(side="left", padx=5, ipadx=5, ipady=2)
        ctk.CTkLabel(frame_leyenda, text="■ En evaluación", text_color="#856404", fg_color="#fff3cd", font=("Arial", 11, "bold"), corner_radius=5).pack(side="left", padx=5, ipadx=5, ipady=2)
        ctk.CTkLabel(frame_leyenda, text="■ Aprobada", text_color="#155724", fg_color="#d4edda", font=("Arial", 11, "bold"), corner_radius=5).pack(side="left", padx=5, ipadx=5, ipady=2)
        ctk.CTkLabel(frame_leyenda, text="■ En desarrollo", text_color="#004085", fg_color="#cce5ff", font=("Arial", 11, "bold"), corner_radius=5).pack(side="left", padx=5, ipadx=5, ipady=2)

        self.menu_ctx = tk.Menu(self.root, tearoff=0)
        self.menu_ctx.add_command(label="Modificar Encabezado y Categorías", command=self.abrir_ventana_editar)
        self.menu_ctx.add_separator()
        self.menu_ctx.add_command(label="Crear Nueva Versión (Mismo Evento)", command=self.duplicar_a_nueva_version)
        self.menu_ctx.add_command(label="Eliminar Registro de Cotización", command=self.eliminar_cotizacion)
        
        self.tree_cot.bind("<Button-3>", self.mostrar_menu_contextual)
        self.tree_cot.bind("<Double-1>", lambda event: self.abrir_ventana_editar())

        self.root.after(100, self.carga_inicial_diferida)

    def carga_inicial_diferida(self):
        self.autocompletar_clientes_combo()
        cache_sistema.invalidar()
        self.cargar_cotizaciones_tabla(reset_pagina=True)

    def cambiar_tipo_cliente(self, choice):
        if choice == "Razón Comercial":
            self.lbl_empresa_dinamico.configure(text="Seleccione Empresa (Razón Comercial):")
        else:
            self.lbl_empresa_dinamico.configure(text="Seleccione Empresa (Razón Social):")
        self.autocompletar_clientes_combo()

    def pagina_anterior(self):
        if self.pagina_actual > 1:
            self.pagina_actual -= 1
            self.cargar_cotizaciones_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_cotizaciones_tabla()

    # 🚀 FIX: COMBO DE CLIENTES CON CACHÉ
    def autocompletar_clientes_combo(self):
        tipo_busqueda = self.var_tipo_cliente.get()
        clave_cache = f"clientes_combo_{tipo_busqueda.replace(' ', '_')}"
        empresas_cache = cache_sistema.obtener(clave_cache)

        if empresas_cache is not None:
            self._actualizar_combo_empresas(empresas_cache)
        else:
            self.combo_empresa.set("Cargando...")
            def tarea():
                empresas = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        if tipo_busqueda == "Razón Comercial":
                            cursor.execute("SELECT razon_comercial FROM clientes WHERE razon_comercial IS NOT NULL AND TRIM(razon_comercial) != '' ORDER BY razon_comercial ASC")
                        else:
                            cursor.execute("SELECT nombre_empresa FROM clientes WHERE nombre_empresa IS NOT NULL AND TRIM(nombre_empresa) != '' ORDER BY nombre_empresa ASC")
                        empresas = [r[0] for r in cursor.fetchall() if r[0]]
                        cache_sistema.guardar(clave_cache, empresas)
                    except Exception:
                        pass
                    finally:
                        liberar_conexion(conn)

                self.root.after(0, lambda: self._actualizar_combo_empresas(empresas))

            threading.Thread(target=tarea, daemon=True).start()

    def _actualizar_combo_empresas(self, empresas):
        if empresas:
            self.combo_empresa.configure(values=empresas)
            if self.combo_empresa.get() not in empresas:
                self.combo_empresa.set(empresas[0])
        else:
            self.combo_empresa.configure(values=["--- Sin registros ---"])
            self.combo_empresa.set("--- Sin registros ---")

    def filtrar_con_retraso(self):
        if hasattr(self, "_filtro_job"):
            try:
                self.root.after_cancel(self._filtro_job)
            except Exception:
                pass
        self._filtro_job = self.root.after(350, lambda: self.cargar_cotizaciones_tabla(reset_pagina=True))

    # 🚀 FIX: CARGA LAZY LOADING + CACHÉ
    def cargar_cotizaciones_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        for item in self.tree_cot.get_children():
            self.tree_cot.delete(item)

        keyword = self.ent_buscar.get().strip().lower()
        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        clave_cache = f"cotizaciones_{keyword}_pag_{self.pagina_actual}"
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla_cotizaciones(datos)
        else:
            self.tree_cot.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", ""))
            
            def tarea_descarga():
                conn = conectar_db(silencioso=True)
                rows = [] 
                if conn:
                    try:
                        cursor = conn.cursor()
                        if keyword:
                            query = "SELECT id, codigo_cotizacion, nombre_empresa, nombre_evento, fecha_evento, status FROM cotizaciones WHERE codigo_cotizacion ILIKE %s OR nombre_empresa ILIKE %s OR nombre_evento ILIKE %s ORDER BY id DESC LIMIT %s OFFSET %s"
                            cursor.execute(query, (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", self.registros_por_pagina, offset))
                        else:
                            cursor.execute("SELECT id, codigo_cotizacion, nombre_empresa, nombre_evento, fecha_evento, status FROM cotizaciones ORDER BY id DESC LIMIT %s OFFSET %s", (self.registros_por_pagina, offset))
                        
                        rows = cursor.fetchall()
                        cache_sistema.guardar(clave_cache, rows)
                    except Exception as e:
                        print("Error cargando cotizaciones:", e)
                    finally:
                        liberar_conexion(conn)

                self.root.after(0, lambda: self._pintar_tabla_cotizaciones(rows))

            threading.Thread(target=tarea_descarga, daemon=True).start()

    def _pintar_tabla_cotizaciones(self, rows):
        for item in self.tree_cot.get_children():
            self.tree_cot.delete(item)
            
        contador = ((self.pagina_actual - 1) * self.registros_por_pagina) + 1
        for r in rows:
            status_actual = r[5] if r[5] else "En desarrollo"
            valores = (contador, r[0], r[1], r[2], r[3], r[4], r[5])
            self.tree_cot.insert("", "end", values=valores, tags=(status_actual,))
            contador += 1

        if self.pagina_actual > 1:
            self.btn_ant.configure(state="normal")
        else:
            self.btn_ant.configure(state="disabled")

        if len(rows) == self.registros_por_pagina:
            self.btn_sig.configure(state="normal")
        else:
            self.btn_sig.configure(state="disabled")

    def registrar_cotizacion(self):
        empresa = self.var_empresa.get().strip()
        evento = self.ent_evento.get().strip()
        f_evento = self.ent_fecha_evento.get().strip()
        locacion = self.ent_locacion.get().strip()
        desc = self.txt_descripcion.get("1.0", "end-1c").strip()
        status = "En desarrollo"
        
        if not empresa or empresa == "--- Sin registros ---" or not evento:
            messagebox.showwarning("Atención", "Los campos Empresa y Nombre del Evento son obligatorios.")
            return
            
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede registrar cotizaciones en Modo Lectura.")
            return
            
        try:
            cursor = conn.cursor()
            codigo_cotizacion = generar_nuevo_codigo_cotizacion(conn)
            fecha_registro = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            cursor.execute(
                "INSERT INTO cotizaciones (codigo_cotizacion, nombre_empresa, nombre_evento, descripcion, fecha_registro, status, fecha_evento, locacion_evento) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (codigo_cotizacion, empresa, evento, desc, fecha_registro, status, f_evento, locacion)
            )
            conn.commit()
            
            cache_sistema.invalidar()
            registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Generó la nueva cotización {codigo_cotizacion} para '{empresa}'")
            messagebox.showinfo("Éxito", f"Cotización {codigo_cotizacion} registrada.")
            
            self.ent_evento.delete(0, tk.END)
            self.ent_fecha_evento.delete(0, tk.END)
            self.ent_locacion.delete(0, tk.END)
            self.txt_descripcion.delete("1.0", tk.END)
            self.cargar_cotizaciones_tabla(reset_pagina=True)
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", f"Fallo al registrar:\n{str(e)}")
        finally:
            liberar_conexion(conn)
            
    def mostrar_menu_contextual(self, event):
        item = self.tree_cot.identify_row(event.y)
        if item:
            self.tree_cot.selection_set(item)
            self.menu_ctx.post(event.x_root, event.y_root)

    def eliminar_cotizacion(self):
        selected = self.tree_cot.selection()
        if not selected:
            return
        values = self.tree_cot.item(selected[0], "values")
        id_cot, codigo_cot = values[1], values[2]
        
        if messagebox.askyesno("Confirmar Eliminación", f"¿Desea eliminar la cotización {codigo_cot}?"):
            conn = conectar_db()
            if not conn:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede eliminar en Modo Lectura.")
                return
            try:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM cotizaciones WHERE id = %s", (id_cot,))
                cursor.execute("DELETE FROM cotizacion_detalles WHERE codigo_cotizacion = %s", (codigo_cot,))
                try:
                    cursor.execute("DELETE FROM cotizacion_proveedores WHERE codigo_cotizacion = %s", (codigo_cot,))
                except Exception: pass
                
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Eliminó por completo la cotización {codigo_cot}")
                self.cargar_cotizaciones_tabla(reset_pagina=True)
            except Exception as e:
                conn.rollback()
                messagebox.showerror("Error", f"No se pudo completar la eliminación:\n{str(e)}")
            finally:
                liberar_conexion(conn)

    def duplicar_a_nueva_version(self):
        selected = self.tree_cot.selection()
        if not selected:
            return
        values = self.tree_cot.item(selected[0], "values")
        id_cot, codigo_actual = values[1], values[2]
        
        conn = conectar_db()
        if not conn:
            messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede clonar en Modo Lectura.")
            return
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT nombre_empresa, nombre_evento, descripcion, status, fecha_evento, locacion_evento FROM cotizaciones WHERE id = %s", (id_cot,))
            orig = cursor.fetchone()
            if orig:
                empresa, evento, desc, status, f_evento, locacion = orig
                nuevo_codigo = generar_nueva_version_evento_existente(conn, codigo_actual)
                fecha_registro = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
                
                cursor.execute("INSERT INTO cotizaciones (codigo_cotizacion, nombre_empresa, nombre_evento, descripcion, fecha_registro, status, fecha_evento, locacion_evento) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                               (nuevo_codigo, empresa, evento, desc, fecha_registro, status, f_evento, locacion))
                cursor.execute("INSERT INTO cotizacion_detalles (codigo_cotizacion, categoria_suministro, cantidad) SELECT %s, categoria_suministro, cantidad FROM cotizacion_detalles WHERE codigo_cotizacion = %s", (nuevo_codigo, codigo_actual))
                try:
                    cursor.execute("INSERT INTO cotizacion_proveedores (codigo_cotizacion, categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, tipo_ganancia, valor_ganancia, precio_final_venta, notes_negociacion, cantidad, dias_credito) SELECT %s, categoria_suministro, proveedor_nombre, precio_lista, precio_descuento, tipo_ganancia, valor_ganancia, precio_final_venta, notes_negociacion, cantidad, dias_credito FROM cotizacion_proveedores WHERE codigo_cotizacion = %s", (nuevo_codigo, codigo_actual))
                except Exception:
                    pass
                    
                conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Clonó la cotización {codigo_actual} generando la versión {nuevo_codigo}")
                messagebox.showinfo("Éxito", f"Nueva versión generada: {nuevo_codigo}")
                self.cargar_cotizaciones_tabla(reset_pagina=True)
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", f"Fallo al clonar versión:\n{str(e)}")
        finally:
            liberar_conexion(conn)

    def abrir_ventana_editar(self, codigo_directo=None):
        conn = conectar_db()
        if not conn:
            return
        try:
            cursor = conn.cursor()
            if codigo_directo:
                codigo_cot = codigo_directo
                cursor.execute("SELECT id, nombre_empresa, nombre_evento, descripcion, status, fecha_evento, locacion_evento FROM cotizaciones WHERE codigo_cotizacion = %s", (codigo_cot,))
            else:
                selected = self.tree_cot.selection()
                if not selected:
                    return
                values = self.tree_cot.item(selected[0], "values")
                id_cot, codigo_cot = values[1], values[2]
                cursor.execute("SELECT id, nombre_empresa, nombre_evento, descripcion, status, fecha_evento, locacion_evento FROM cotizaciones WHERE id = %s", (id_cot,))
            
            registro = cursor.fetchone()
            if not registro:
                return
            id_cot, empresa_reg, evento_reg, desc_reg, status_reg, fev_reg, locacion_reg = registro
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al buscar datos:\n{e}")
            return
        finally:
            liberar_conexion(conn)

        v_edit = ctk.CTkToplevel(self.root)
        v_edit.title(f"Configuración de Cotización - {codigo_cot}")
        v_edit.after(100, lambda: maximizar_ventana(v_edit))
        v_edit.grab_set()

        f_izq = ctk.CTkFrame(v_edit, corner_radius=10)
        f_izq.pack(side="left", fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(f_izq, text="Datos Generales", font=("Arial", 15, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(15, 5))
        ctk.CTkLabel(f_izq, text=f"Código: {codigo_cot}", font=("Arial", 12, "bold", "underline")).pack(anchor="w", padx=15, pady=5)
        
        ctk.CTkLabel(f_izq, text="Empresa Cliente:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        ent_sidebar_empresa = ctk.CTkEntry(f_izq, font=("Arial", 12))
        ent_sidebar_empresa.pack(fill="x", padx=15, pady=2)
        ent_sidebar_empresa.insert(0, empresa_reg)
        
        ctk.CTkLabel(f_izq, text="Nombre del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        ent_sidebar_evento = ctk.CTkEntry(f_izq, font=("Arial", 12))
        ent_sidebar_evento.pack(fill="x", padx=15, pady=2)
        ent_sidebar_evento.insert(0, evento_reg)
        
        ctk.CTkLabel(f_izq, text="Fecha del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        f_fecha_edit = ctk.CTkFrame(f_izq, fg_color="transparent")
        f_fecha_edit.pack(fill="x", padx=15, pady=2)
        ent_sidebar_fecha_e = ctk.CTkEntry(f_fecha_edit, font=("Arial", 12), placeholder_text="DD/MM/AAAA")
        ent_sidebar_fecha_e.pack(side="left", fill="x", expand=True)
        ent_sidebar_fecha_e.insert(0, fev_reg if fev_reg else "")
        btn_cal_edit = ctk.CTkButton(f_fecha_edit, text="[ 📅 ]", width=60, fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(ent_sidebar_fecha_e, v_edit))
        btn_cal_edit.pack(side="right", padx=(5, 0))
        
        ctk.CTkLabel(f_izq, text="Locación del Evento:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        ent_sidebar_locacion = ctk.CTkEntry(f_izq, font=("Arial", 12))
        ent_sidebar_locacion.pack(fill="x", padx=15, pady=2)
        ent_sidebar_locacion.insert(0, locacion_reg if locacion_reg else "")
        
        ctk.CTkLabel(f_izq, text="Descripción del Proyecto:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        txt_sidebar_desc = ctk.CTkTextbox(f_izq, height=100, font=("Arial", 12), border_width=1)
        txt_sidebar_desc.pack(fill="x", padx=15, pady=2)
        txt_sidebar_desc.insert("1.0", desc_reg if desc_reg else "")
        
        ctk.CTkLabel(f_izq, text="Status de la Cotización:", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(2, 2))
        combo_sidebar_status = ctk.CTkComboBox(f_izq, values=["No aprobada", "En evaluación", "Aprobada", "En desarrollo"], state="readonly", font=("Arial", 12))
        combo_sidebar_status.pack(fill="x", padx=15, pady=2)
        combo_sidebar_status.set(status_reg if status_reg else "En desarrollo")

        f_der = ctk.CTkFrame(v_edit, corner_radius=10)
        f_der.pack(side="right", fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(f_der, text="Asignación de Categorías", font=("Arial", 15, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(15, 5))
        f_alta_cat = ctk.CTkFrame(f_der, fg_color="transparent")
        f_alta_cat.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_alta_cat, text="Categoría:", font=("Arial", 12, "bold")).pack(side="left", padx=(0, 5))
        combo_cat = ctk.CTkComboBox(f_alta_cat, font=("Arial", 12), width=180)
        combo_cat.pack(side="left", padx=5)

        def actualizar_combo_categorias():
            categorias = list(getattr(cache_sistema, "categorias_generales", []) or [])
            if not categorias:
                conn_cat = conectar_db(silencioso=True)
                if conn_cat:
                    try:
                        c = conn_cat.cursor()
                        c.execute("SELECT nombre FROM categorias ORDER BY nombre ASC")
                        categorias = [str(r[0]).strip() for r in c.fetchall() if r]
                    except Exception:
                        pass
                    finally:
                        liberar_conexion(conn_cat)
            if not categorias:
                categorias = ["Estructuras", "Audio", "Iluminación", "Video", "Pantallas", "Mobiliario", "Personal", "Catering"]
            combo_cat.configure(values=categorias)
            if categorias:
                combo_cat.set(categorias[0])

        actualizar_combo_categorias()
        ctk.CTkLabel(f_alta_cat, text="Cant:", font=("Arial", 12, "bold")).pack(side="left", padx=(15, 5))
        spin_cant = ctk.CTkEntry(f_alta_cat, width=60, font=("Arial", 12))
        spin_cant.insert(0, "1")
        spin_cant.pack(side="left", padx=5)

        f_tabla_cat = ctk.CTkFrame(f_der, fg_color="transparent")
        f_tabla_cat.pack(fill="both", expand=True, padx=15, pady=5)
        tree_cat_asis = ttk.Treeview(f_tabla_cat, columns=("id", "categoria", "cantidad"), show="headings", height=8)
        tree_cat_asis.heading("id", text="ID")
        tree_cat_asis.heading("categoria", text="Categoría Seleccionada")
        tree_cat_asis.heading("cantidad", text="Cantidad")
        tree_cat_asis.column("id", width=40, anchor="center")
        tree_cat_asis.column("categoria", width=220, anchor="w")
        tree_cat_asis.column("cantidad", width=80, anchor="center")
        scroll_cat = ctk.CTkScrollbar(f_tabla_cat, command=tree_cat_asis.yview)
        tree_cat_asis.configure(yscrollcommand=scroll_cat.set)
        tree_cat_asis.pack(side="left", fill="both", expand=True)
        scroll_cat.pack(side="right", fill="y")

        def refrescar_tabla_categorias():
            for item in tree_cat_asis.get_children():
                tree_cat_asis.delete(item)
            c_conn = conectar_db(silencioso=True)
            if not c_conn:
                return
            try:
                c = c_conn.cursor()
                c.execute("SELECT id, categoria_suministro, cantidad FROM cotizacion_detalles WHERE codigo_cotizacion = %s", (codigo_cot,))
                for i, r in enumerate(c.fetchall(), start=1):
                    tree_cat_asis.insert("", "end", values=(i, r[1], r[2], r[0]))
            except Exception:
                pass
            finally:
                liberar_conexion(c_conn)

        def agregar_categoria_lista():
            cat = combo_cat.get().strip()
            try:
                cant = int(spin_cant.get())
            except ValueError:
                messagebox.showerror("Error", "Cantidad inválida.", parent=v_edit)
                return
            if not cat:
                return
            c_conn = conectar_db()
            if not c_conn:
                messagebox.showwarning("Modo Lectura", "Sin conexión a internet. No se puede añadir categoría.", parent=v_edit)
                return
            try:
                c = c_conn.cursor()
                c.execute("SELECT id, cantidad FROM cotizacion_detalles WHERE codigo_cotizacion = %s AND categoria_suministro = %s", (codigo_cot, cat))
                reg = c.fetchone()
                if reg:
                    c.execute("UPDATE cotizacion_detalles SET cantidad = %s WHERE id = %s", (reg[1] + cant, reg[0]))
                else:
                    c.execute("INSERT INTO cotizacion_detalles (codigo_cotizacion, categoria_suministro, cantidad) VALUES (%s, %s, %s)", (codigo_cot, cat, cant))
                c_conn.commit()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Agregó/actualizó la categoría '{cat}' en la cotización {codigo_cot}")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=v_edit)
            finally:
                liberar_conexion(c_conn)
            refrescar_tabla_categorias()

        def remover_categoria_lista():
            sel = tree_cat_asis.selection()
            if not sel:
                return
            c_conn = conectar_db()
            if not c_conn:
                messagebox.showwarning("Modo Lectura", "Sin conexión a internet. No se puede eliminar categoría.", parent=v_edit)
                return
            try:
                c = c_conn.cursor()
                c.execute("DELETE FROM cotizacion_detalles WHERE id = %s", (tree_cat_asis.item(sel[0], "values")[3],))
                c_conn.commit()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Removió una categoría de la cotización {codigo_cot}")
            except Exception:
                pass
            finally:
                liberar_conexion(c_conn)
            refrescar_tabla_categorias()

        btn_add_cat = ctk.CTkButton(f_alta_cat, text="Añadir", width=80, command=agregar_categoria_lista)
        btn_add_cat.pack(side="left", padx=10)
        btn_del_cat = ctk.CTkButton(f_der, text="[ X ] Eliminar Seleccionada", fg_color="#D32F2F", hover_color="#B71C1C", command=remover_categoria_lista)
        btn_del_cat.pack(anchor="w", padx=15, pady=5)
        refrescar_tabla_categorias()

        def ejecutar_update():
            if not ent_sidebar_empresa.get().strip() or not ent_sidebar_evento.get().strip():
                return
            c_conn = conectar_db()
            if not c_conn:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se puede modificar en Modo Lectura.", parent=v_edit)
                return
            try:
                c_conn.cursor().execute("UPDATE cotizaciones SET nombre_empresa=%s, nombre_evento=%s, descripcion=%s, status=%s, fecha_evento=%s, locacion_evento=%s WHERE id=%s",
                                 (ent_sidebar_empresa.get().strip(), ent_sidebar_evento.get().strip(), txt_sidebar_desc.get("1.0", "end-1c").strip(), combo_sidebar_status.get(), ent_sidebar_fecha_e.get().strip(), ent_sidebar_locacion.get().strip(), id_cot))
                c_conn.commit()
                cache_sistema.invalidar()
                registrar_auditoria(self.usuario_activo, "Cotizaciones", f"Actualizó los datos generales de la cotización ID {id_cot}")
                v_edit.destroy()
                self.cargar_cotizaciones_tabla(reset_pagina=True)
                messagebox.showinfo("Éxito", "Cambios guardados con éxito.")
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=v_edit)
            finally:
                liberar_conexion(c_conn)

        def abrir_etapa3_desde_modificador():
            empresa_val = ent_sidebar_empresa.get().strip()
            evento_val = ent_sidebar_evento.get().strip()
            desc_val = txt_sidebar_desc.get("1.0", "end-1c").strip()
            status_val = combo_sidebar_status.get()
            f_evento_val = ent_sidebar_fecha_e.get().strip()
            locacion_val = ent_sidebar_locacion.get().strip()
            if not empresa_val or not evento_val:
                return
            c_conn = conectar_db()
            if not c_conn:
                messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nLos cambios en el encabezado no se guardarán en Modo Lectura.", parent=v_edit)
                return
            try:
                c_conn.cursor().execute("UPDATE cotizaciones SET nombre_empresa=%s, nombre_evento=%s, descripcion=%s, status=%s, fecha_evento=%s, locacion_evento=%s WHERE id=%s",
                                 (empresa_val, evento_val, desc_val, status_val, f_evento_val, locacion_val, id_cot))
                c_conn.commit()
                cache_sistema.invalidar()
            except Exception:
                pass
            finally:
                liberar_conexion(c_conn)
            
            v_edit.destroy()
            try:
                import cotizaciones_fase3
                importlib.reload(cotizaciones_fase3)
                cotizaciones_fase3.VentanaEtapaProveedores(self, codigo_cot, empresa_val, evento_val, callback_on_close=lambda: self.cargar_cotizaciones_tabla(reset_pagina=True))
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo llamar a la Etapa 3:\n{str(e)}")

        f_botones = ctk.CTkFrame(f_izq, fg_color="transparent")
        f_botones.pack(fill="x", padx=15, pady=20)
        ctk.CTkButton(f_botones, text="[ Guardar Cambios ]", command=ejecutar_update).pack(side="left", padx=5, expand=True, fill="x")
        ctk.CTkButton(f_botones, text="[ >> ] Ir a Matriz (Etapa 3)", fg_color="#228B22", hover_color="#1E761E", command=abrir_etapa3_desde_modificador).pack(side="right", padx=5, expand=True, fill="x")


if __name__ == "__main__":
    root = ctk.CTk()
    app = VentanaCotizaciones(root)
    root.after(100, lambda: maximizar_ventana(root))
    root.mainloop()