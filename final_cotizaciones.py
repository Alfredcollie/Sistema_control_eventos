# -*- coding: utf-8 -*-

"""
=========================================================
FINAL_COTIZACIONES.PY - MOTOR OFICIAL DE PDF (OPTIMIZADO)
=========================================================
- FIX: Ajuste dinámico de la posición del bloque de totales. 
  Si sobran más de 205 puntos, el subtotal se dibujará fijo al pie de la página,
  garantizando que siempre quede en la parte inferior (estilo carta formal).
- FIX: Logo adaptado al ancho de la hoja (borde a borde), respetando 
  estrictamente los márgenes laterales.
- FIX: Términos y Condiciones cargados desde Configuración Global.
- FIX: Exoneración de Fee incorporada dinámicamente.
- FIX: Respeta el orden manual de los ítems (ORDER BY id ASC).
- FIX: Etiqueta "CONTACTO" bajada una línea dentro del recuadro.
"""

import os
import re
import json
import sys
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from tkinter import messagebox

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = "config_local.json"

_PATRON_ETIQUETAS = re.compile(r'(\[B\]|\[/B\]|\[M\]|\[/M\])', re.IGNORECASE)

def hex_to_rgb(hex_color):
    try:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    except Exception:
        return (0.0, 0.0, 0.0)

def parsear_segmentos_formato(texto):
    resultado, negrita, color_p = [], False, False
    for parte in _PATRON_ETIQUETAS.split(str(texto)):
        p_up = parte.upper()
        if p_up == "[B]":
            negrita = True
        elif p_up == "[/B]":
            negrita = False
        elif p_up == "[M]":
            color_p = True
        elif p_up == "[/M]":
            color_p = False
        elif parte:
            resultado.append((parte, negrita, color_p))
    return resultado

def texto_plano_sin_marcado(texto):
    return _PATRON_ETIQUETAS.sub("", str(texto))

_SCHEMA_PDF_OK = False

def generar_reporte_cotizacion_pdf(conn_shared, codigo_cotizacion):
    global _SCHEMA_PDF_OK
    try:
        cursor = conn_shared.cursor()

        if not _SCHEMA_PDF_OK:
            for sql in (
                "ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS tipo_cambio NUMERIC DEFAULT 3.75",
                "ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS forma_pago TEXT DEFAULT '50% adelantado, 50% a 30 días de la primera factura.'",
                "ALTER TABLE cotizaciones ADD COLUMN IF NOT EXISTS sin_fee BOOLEAN DEFAULT FALSE",
                "ALTER TABLE cotizacion_proveedores ADD COLUMN IF NOT EXISTS cantidad INTEGER DEFAULT 1",
            ):
                try:
                    c_alt = conn_shared.cursor()
                    c_alt.execute(sql)
                    conn_shared.commit()
                except Exception:
                    conn_shared.rollback()
            _SCHEMA_PDF_OK = True

        cliente, descripcion_proyecto, proyecto, contacto_cliente = "CLIENTE COMERCIAL", "Servicios Logísticos", "PROYECTO BLACK CUBE", "No especificado"
        fecha_actual = datetime.now().strftime("%d/%m/%Y")
        forma_pago_pdf = "50% adelantado, 50% a 30 días de la primera factura."
        moneda, simbolo_moneda, tipo_cambio_pdf = "Soles", "S/", 3.75
        sin_fee_db = False

        try:
            cursor.execute("SELECT nombre_empresa, descripcion, nombre_evento, tipo_cambio, forma_pago, sin_fee FROM cotizaciones WHERE codigo_cotizacion = %s", (codigo_cotizacion,))
            res_db = cursor.fetchone()
            if res_db:
                cliente = str(res_db[0]).replace('{', '').replace('}', '').strip()
                descripcion_proyecto = str(res_db[1]).replace('{', '').replace('}', '').strip()
                proyecto = str(res_db[2]).replace('{', '').replace('}', '').strip()
                if len(res_db) > 3 and res_db[3] is not None and float(res_db[3]) > 0:
                    tipo_cambio_pdf = float(res_db[3])
                if len(res_db) > 4 and res_db[4]:
                    forma_pago_pdf = str(res_db[4]).strip()
                if len(res_db) > 5 and res_db[5] is not None:
                    sin_fee_db = bool(res_db[5])
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
                    modo_cliente = str(config.get("nombre_cliente_cotizacion", "Razón Social")).strip()
                    if modo_cliente == "Razón Comercial" and len(res_cont) > 1 and res_cont[1]:
                        razon_comercial_bd = str(res_cont[1]).replace('{', '').replace('}', '').strip()
                        if razon_comercial_bd:
                            cliente = razon_comercial_bd
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

        # --------------------------------------------------
        # 🚀 FIX MAC: RUTAS DE GUARDADO BLINDADAS
        # --------------------------------------------------
        ruta_drive = str(config.get("ruta_drive", "")).strip()
        usando_respaldo = False

        if ruta_drive == "/" or ruta_drive == "\\":
            ruta_drive = ""

        if ruta_drive and os.path.exists(ruta_drive):
            carpeta_destino = os.path.join(ruta_drive, "Cotizaciones")
        else:
            if os.path.exists(r"G:\Mi unidad"):
                carpeta_destino = r"G:\Mi unidad\Programa de control black Cube\Cotizaciones"
            else:
                escritorio = os.path.join(os.path.expanduser("~"), "Desktop")
                carpeta_destino = os.path.join(escritorio, "Cotizaciones")
                usando_respaldo = True
                
        try:
            if not os.path.exists(carpeta_destino):
                os.makedirs(carpeta_destino)
        except Exception:
            carpeta_destino = os.path.join(os.path.expanduser("~"), "Downloads", "Cotizaciones")
            usando_respaldo = True
            if not os.path.exists(carpeta_destino):
                try:
                    os.makedirs(carpeta_destino)
                except Exception:
                    pass
                
        nombre_archivo = os.path.join(carpeta_destino, f"Cotizacion_{codigo_cotizacion}.pdf")

        c = canvas.Canvas(nombre_archivo, pagesize=letter)
        ancho_hoja = letter[0]
        margen_izq = 40
        margen_der = 40
        ancho_util = ancho_hoja - margen_izq - margen_der

        ruta_usar = None
        mostrar_logo = True
        
        if "ruta_logo_cotizacion" in config:
            ruta_conf = str(config.get("ruta_logo_cotizacion", "")).strip()
            if ruta_conf != "":
                ruta_conf = os.path.normpath(ruta_conf)
                
            if ruta_conf == "":
                mostrar_logo = False
            elif os.path.exists(ruta_conf):
                ruta_usar = ruta_conf
                
        if mostrar_logo and not ruta_usar:
            fallbacks = [
                "LogoCotizacion.png",
                "LogoCotizacion.jpg",
                "Logo_Collie_Software.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.png",
                r"G:\Mi unidad\Programa de control black Cube\LogoCotizacion.jpg"
            ]
            for fallback in fallbacks:
                fallback_norm = os.path.normpath(fallback)
                if os.path.exists(fallback_norm):
                    ruta_usar = fallback_norm
                    break

        rgb_primario = hex_to_rgb(config.get("color_primario", "#eb337a"))
        rgb_secundario = hex_to_rgb(config.get("color_secundario", "#000000"))
        rgb_franja = hex_to_rgb(config.get("color_franja", config.get("color_primario", "#eb337a")))

        offset = 0
        if mostrar_logo and ruta_usar:
            try:
                img = ImageReader(ruta_usar)
                img_w, img_h = img.getSize()
                
                if img_w == 0: img_w = 1
                if img_h == 0: img_h = 1
                
                ratio = ancho_util / float(img_w)
                
                final_w = ancho_util
                final_h = img_h * ratio
                y_logo = 792 - 40 - final_h
                
                c.drawImage(ruta_usar, margen_izq, y_logo, width=final_w, height=final_h, preserveAspectRatio=True)
                
                techo_textos = 685
                margen_inferior_logo = y_logo - 25
                offset = (margen_inferior_logo - techo_textos) if margen_inferior_logo < techo_textos else 0
            except Exception as e:
                try:
                    c.drawImage(ruta_usar, 40, 685, width=150, height=80, preserveAspectRatio=True)
                except Exception:
                    pass
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
        
        # 🚀 FIX: CONTACTO BAJADO UNA LÍNEA (DE 585 A 570)
        c.drawString(50, 570 + offset, "CONTACTO:")
        
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
        
        # 🚀 FIX: DATO CONTACTO BAJADO UNA LÍNEA (DE 585 A 570)
        c.drawString(115, 570 + offset, contacto_cliente)

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
            cursor.execute("SELECT * FROM cotizacion_proveedores WHERE codigo_cotizacion = %s ORDER BY id ASC", (codigo_cotizacion,))
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

        if y_pos > 205:
            y_totales = 140
        else:
            if y_pos < 170:
                c.showPage()
                y_pos = Y_INICIO_PAGINA_CONTINUACION
            y_totales = y_pos - 65

        fee_produccion = 0.0 if sin_fee_db else (subtotal_acumulado * 0.15)
        total_general_soles = subtotal_acumulado + fee_produccion
        total_general_dolares = total_general_soles / tipo_cambio_pdf

        c.setLineWidth(1)
        c.setStrokeColorRGB(0.85, 0.85, 0.85)
        c.line(40, y_totales + 45, 570, y_totales + 45)
        
        y_cursor = y_totales + 25
        
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawRightString(440, y_cursor, "SUB TOTAL (SOLES)")
        c.drawString(490, y_cursor, "S/")
        c.drawRightString(565, y_cursor, f"{subtotal_acumulado:,.2f}")
        
        if not sin_fee_db:
            y_cursor -= 17
            c.drawRightString(440, y_cursor, "15% FEE PRODUCCIÓN")
            c.drawString(490, y_cursor, "S/")
            c.drawRightString(565, y_cursor, f"{fee_produccion:,.2f}")
            
        y_cursor -= 20
        c.setFont("Helvetica-Bold", 11)
        c.drawRightString(440, y_cursor, "TOTAL (SOLES)")
        c.drawString(490, y_cursor, "S/")
        c.drawRightString(565, y_cursor, f"{total_general_soles:,.2f}")
        
        y_cursor -= 20
        c.setFont("Helvetica-Bold", 10.5)
        c.setFillColorRGB(*rgb_primario)
        c.drawRightString(440, y_cursor, "TOTAL EQUIVALENTE (DÓLARES)")
        c.setFont("Helvetica-Bold", 12)
        c.drawString(475, y_cursor, "$")
        c.drawRightString(565, y_cursor, f"{total_general_dolares:,.2f} USD")

        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColorRGB(*rgb_primario)
        c.drawString(40, y_totales - 55, "TÉRMINOS Y CONDICIONES:")
        
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        y_cond_actual = y_totales - 68
        
        c.drawString(40, y_cond_actual, "Forma de pago: ")
        x_pago = 40 + c.stringWidth("Forma de pago: ", "Helvetica", 8)
        for linea in wrap_text(forma_pago_pdf, "Helvetica", 8, 570 - x_pago):
            c.drawString(x_pago, y_cond_actual, linea)
            y_cond_actual -= 12

        terminos_default = "Precios no incluyen IGV.\nCotización válida por 7 días. Posterior a ello podría haber cambios en el presupuesto.\nPenalidad: Si el presupuesto es aprobado y finalmente el proyecto no se lleva a cabo, se facturará al cliente un 10% del valor total como compensación por gastos administrativos."
        terminos_config = str(config.get("terminos_cotizacion", terminos_default)).strip()
        
        if terminos_config:
            for linea in wrap_text(terminos_config, "Helvetica", 8, 530):
                c.drawString(40, y_cond_actual, linea)
                y_cond_actual -= 12

        c.save()

        if usando_respaldo:
            try:
                messagebox.showwarning(
                    "Google Drive No Encontrado", 
                    "Tu cotización fue guardada en tus Documentos Locales (Escritorio o Descargas) porque no se detectó la ruta de Google Drive.\n\n"
                    "Recuerda revisar la 'Configuración General' del sistema para asegurarte de que la ruta conectada no sea el disco raíz '/' y apunte a tu unidad GDrive."
                )
            except Exception:
                pass

        return True, nombre_archivo
    except Exception as e:
        return False, str(e)