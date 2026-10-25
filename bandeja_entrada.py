# -*- coding: utf-8 -*-
"""
BANDEJA_ENTRADA.PY - Procesamiento automático de facturas y comprobantes
========================================================================
Lee las carpetas de la nube (rclone/Drive):

    <carpeta local sincronizada>/Bandeja/Compras/*.pdf
    <carpeta local sincronizada>/Bandeja/Ventas/*.pdf

Para cada PDF:
    1) extrae el texto (PyMuPDF y, si no hay, OCR),
    2) interpreta RUC, número, fecha y total,
    3) decide si es FACTURA o COMPROBANTE DE PAGO,
    4) propone evento (abierto), categoría, descripción y orden de compra,
    5) calcula una confianza (0-100) y la deja en la bandeja.

Cuando el usuario confirma (o la confianza es >= 90), se crea el registro en
'facturas_recibidas' / 'facturas_emitidas' / 'pagos_clientes' y el PDF se copia
a su carpeta definitiva (la misma carpeta sincronizada).
"""
import hashlib
import json
import os
import re
import shutil
import datetime

try:
    from app_paths import ruta_drive_configurada, ruta_para_guardar
except Exception:  # pragma: no cover
    def ruta_drive_configurada():
        return ""

    def ruta_para_guardar(ruta, *_a, **_k):
        return ruta

from conexion import conectar_db, liberar_conexion

UMBRAL_AUTOMATICO = 90

SUBCARPETAS_INTERNAS = ("_procesados", "_errores", "_duplicados", "_ignorados")

# ---------------------------------------------------------------- carpetas
def carpeta_base():
    """Carpeta local de la nube (la misma que sincroniza rclone)."""
    base = ruta_drive_configurada()
    if not base:
        base = os.path.join(os.path.expanduser("~"), "Archivos_control_evento")
    return base


def carpeta_bandeja(tipo):
    """'compra' -> .../Bandeja/Compras   'venta' -> .../Bandeja/Ventas"""
    nombre = "Compras" if str(tipo).lower().startswith("comp") else "Ventas"
    ruta = os.path.join(carpeta_base(), "Bandeja", nombre)
    try:
        os.makedirs(ruta, exist_ok=True)
        for interno in SUBCARPETAS_INTERNAS:
            os.makedirs(os.path.join(ruta, interno), exist_ok=True)
    except Exception:
        pass
    return ruta


def _carpeta_interna(tipo, cual):
    return os.path.join(carpeta_bandeja(tipo), cual)


# ---------------------------------------------------------------- esquema
_SCHEMA_OK = False


def asegurar_schema():
    global _SCHEMA_OK
    if _SCHEMA_OK:
        return True
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS bandeja_documentos (
                    id SERIAL PRIMARY KEY,
                    huella VARCHAR(64) UNIQUE,
                    ruta TEXT NOT NULL,
                    bandeja VARCHAR(10) NOT NULL,
                    clase VARCHAR(20) DEFAULT '',
                    estado VARCHAR(10) DEFAULT 'amarillo',
                    confianza INTEGER DEFAULT 0,
                    datos TEXT DEFAULT '',
                    propuesta TEXT DEFAULT '',
                    id_registro INTEGER DEFAULT 0,
                    creado VARCHAR(30) DEFAULT '',
                    procesado VARCHAR(30) DEFAULT '',
                    nota TEXT DEFAULT ''
                )""")
        conn.commit()
        _SCHEMA_OK = True
        return True
    except Exception as e:
        print("[bandeja] no se pudo crear la tabla:", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)


# ---------------------------------------------------------------- eventos
def eventos_abiertos():
    """Eventos que se consideran 'abiertos': aprobados o en desarrollo."""
    lista = []
    conn = conectar_db(silencioso=True)
    if not conn:
        return lista
    try:
        with conn.cursor() as c:
            c.execute("""SELECT codigo_cotizacion, nombre_evento, fecha_evento, status
                         FROM cotizaciones
                         WHERE COALESCE(status,'') IN ('Aprobada', 'En desarrollo')
                         ORDER BY fecha_evento DESC""")
            for codigo, nombre, fecha, status in c.fetchall():
                codigo = str(codigo or "").strip()
                nombre = str(nombre or "").strip()
                if not codigo and not nombre:
                    continue
                etiqueta = "%s | %s" % (codigo, nombre) if codigo and nombre else (codigo or nombre)
                lista.append({"codigo": codigo, "nombre": nombre,
                              "etiqueta": etiqueta.strip(" |"), "fecha": str(fecha or "")})
    except Exception as e:
        print("[bandeja] eventos:", e)
    finally:
        liberar_conexion(conn)
    return lista


# ---------------------------------------------------------------- texto
def extraer_texto(ruta):
    """Texto del PDF (PyMuPDF). Si no hay capa de texto, intenta OCR."""
    texto = ""
    try:
        import fitz  # PyMuPDF
        with fitz.open(ruta) as doc:
            partes = [pag.get_text("text") or "" for pag in doc]
        texto = "\n".join(partes)
    except Exception as e:
        print("[bandeja] PyMuPDF:", e)
    if len(texto.strip()) < 30:
        otro = _texto_por_ocr_pdf(ruta)
        if len(otro.strip()) > len(texto.strip()):
            texto = otro
    return texto or ""


def _a_numero(txt):
    """'1,234.50' / '1.234,50' / '1234.5' -> float (o None)."""
    if txt is None:
        return None
    t = str(txt).strip().replace("S/", "").replace("S/.", "").replace(" ", "")
    t = t.replace("\u00a0", "")
    if not t:
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) <= 2 else t.replace(",", "")
    try:
        return float(t)
    except Exception:
        return None



def _texto_por_ocr_pdf(ruta, paginas=2):
    """Renderiza el PDF a imágenes y le aplica OCR (para escaneos sin texto)."""
    imagenes = []
    try:
        import fitz
        with fitz.open(ruta) as doc:
            for i, pag in enumerate(doc):
                if i >= paginas:
                    break
                pix = pag.get_pixmap(dpi=300)
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".png")
                os.close(fd)
                pix.save(tmp)
                imagenes.append(tmp)
    except Exception as e:
        print("[bandeja] render PDF:", e)
        return ""
    if not imagenes:
        return ""
    texto = ""
    try:
        import escaner
        texto = escaner.texto_de_resultados(escaner.ocr_paginas(imagenes, idioma="Español"))
    except Exception as e:
        print("[bandeja] OCR PDF:", e)
    finally:
        for img in imagenes:
            try:
                os.remove(img)
            except Exception:
                pass
    return texto or ""


def interpretar(texto):
    """Datos básicos del comprobante: ruc, número, fecha, total, tipo."""
    datos = {"ruc": "", "numero": "", "fecha": "", "total": None,
             "tipo": "", "es_comprobante": False}
    plano = re.sub(r"[ \t]+", " ", texto or "")
    mayus = plano.upper()

    m = re.search(r"\b(10|20)\d{9}\b", plano)
    if m:
        datos["ruc"] = m.group(0)

    m = re.search(r"\b([FBE]\d{3})\s*[-–]\s*(\d{2,8})\b", plano)
    if m:
        datos["numero"] = "%s-%s" % (m.group(1), m.group(2))
    else:
        m = re.search(r"(?:N[°º]|NUMERO|NÚMERO)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-]{3,})", plano, re.I)
        if m:
            datos["numero"] = m.group(1).strip()

    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b", plano)
    if m:
        datos["fecha"] = "%02d/%02d/%s" % (int(m.group(1)), int(m.group(2)), m.group(3))
    else:
        datos["fecha"] = _fecha_con_mes(texto)

    # Total: etiqueta «Importe Total / Total» (el monto suele estar en otra línea)
    datos["total"] = _total_desde_lineas(texto)

    if re.search(r"RECIBO POR HONORARIOS", mayus):
        datos["tipo"] = "Recibo por Honorarios"
    elif re.search(r"NOTA DE CR[EÉ]DITO", mayus):
        datos["tipo"] = "Nota de Crédito"
    elif re.search(r"\bBOLETA\b", mayus):
        datos["tipo"] = "Boleta"
    elif re.search(r"\bFACTURA\b", mayus):
        datos["tipo"] = "Factura"

    # ¿Es un comprobante de pago (voucher) y no una factura?
    señales_voucher = sum(1 for p in (
        "CONSTANCIA", "TRANSFERENCIA", "OPERACI", "DEPOSITO", "DEPÓSITO", "MONTO ENVIADO",
        "YAPE", "PLIN", "PAGO REALIZADO", "COMPROBANTE DE PAGO", "NRO. OPERACION",
        "ITF", "CORRIENTE SOLES", "CUENTA DE AHORROS") if p in mayus)
    señales_factura = sum(1 for p in ("FACTURA", "BOLETA", "IGV", "SUBTOTAL",
                                      "VALOR VENTA", "ADQUIRIENTE") if p in mayus)
    if re.search(r"FACTURA ELECTR|BOLETA ELECTR", mayus):
        datos["es_comprobante"] = False
    else:
        datos["es_comprobante"] = ((señales_voucher >= 2 and señales_factura <= 1) or
                                   (señales_voucher >= 1 and not datos.get("ruc")))

    # Importes sueltos del documento ("s/ 3,271.oo"): sirven para emparejar el
    # comprobante con la factura aunque el OCR lea el total con la comisión.
    montos = []
    for m in re.finditer(r"s/\s*\.?\s*([\d][\d.,]*(?:oo|OO)?)", plano, re.I):
        crudo = m.group(1).replace("oo", "00").replace("OO", "00")
        valor = _a_numero(crudo)
        if valor and valor > 0:
            montos.append(round(valor, 2))
    if datos.get("total"):
        montos.append(round(float(datos["total"]), 2))
    datos["montos"] = sorted(set(montos), reverse=True)
    return datos


_PALABRAS_CATEGORIA = (
    ("COMBUSTIBLE", ("COMBUSTIBLE", "GRIFO", "GASOLINA", "DIESEL", "PETROLEO", "REPSOL", "PRIMAX", "PECSA")),
    ("PEAJES", ("PEAJE", "PEAJES", "TELEPASE")),
    ("HOSPEDAJE", ("HOTEL", "HOSPEDAJE", "ALOJAMIENTO", "HABITACI")),
    ("ALIMENTOS", ("ALIMENTO", "CATERING", "COMIDA", "RESTAURANT", "MENU", "BOCADITO")),
    ("VIÁTICOS", ("VIATICO", "VIÁTICO", "MOVILIDAD", "TRANSPORTE", "TAXI", "AEREO")),
    ("IMPRENTA", ("IMPRENTA", "IMPRESIÓN", "IMPRESION", "BANNER", "GIGANTE", "PLOTEO")),
    ("ALQUILERES", ("ALQUILER", "RENTA", "ARRIENDO")),
    ("PERSONAL / HONORARIOS", ("HONORARIO", "PLANILLA", "SUELDO", "SALARIO")),
    ("SERVICIOS BÁSICOS", ("LUZ", "AGUA", "INTERNET", "TELEFON", "ENEL", "SEDAPAL")),
    ("GASTOS BANCARIOS", ("ITF", "COMISION", "COMISIÓN", "MANTENIMIENTO DE CUENTA", "PORTES")),
)


def categoria_sugerida(texto, proveedor=""):
    mayus = (str(texto or "") + " " + str(proveedor or "")).upper()
    for categoria, palabras in _PALABRAS_CATEGORIA:
        for palabra in palabras:
            if palabra in mayus:
                return categoria
    return ""


def evento_en_texto(texto, abiertos):
    """Busca en el texto el nombre o el código de algún evento abierto."""
    mayus = re.sub(r"[^A-Z0-9 ]+", " ", (texto or "").upper())
    mayus = re.sub(r"\s+", " ", mayus)
    mejor = ""
    for ev in (abiertos or []):
        campos = [ev.get("codigo") or "", ev.get("nombre") or ""]
        for campo in campos:
            limpio = re.sub(r"[^A-Z0-9 ]+", " ", str(campo).upper())
            limpio = re.sub(r"\s+", " ", limpio).strip()
            if len(limpio) >= 6 and limpio in mayus:
                if len(limpio) > len(mejor):
                    mejor = limpio
    if not mejor:
        return None
    for ev in (abiertos or []):
        nombre = re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]+", " ", str(ev.get("nombre") or "").upper())).strip()
        codigo = str(ev.get("codigo") or "").strip()
        if nombre and nombre in mayus:
            return ev
        if codigo and codigo.upper() in mayus:
            return ev
    return None


def proveedor_en_texto(texto):
    """Razón social del emisor de la factura (no la del receptor)."""
    return _proveedor_desde_lineas(texto)



_MESES_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
            "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
            "noviembre": 11, "diciembre": 12}


def _fecha_con_mes(texto):
    """Fecha escrita como '24 de setiembre de 2026' (vouchers del banco)."""
    m = re.search(r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+de\s+(\d{4})", texto or "")
    if not m:
        return ""
    mes = _MESES_ES.get(m.group(2).strip().lower())
    if not mes:
        return ""
    return "%02d/%02d/%s" % (int(m.group(1)), mes, m.group(3))


def _lineas(texto):
    return [l.strip() for l in (texto or "").split(chr(10)) if l.strip()]


def _total_desde_lineas(texto):
    """Importe total: busca la etiqueta y toma el número de su misma línea o de
    las siguientes (los PDF de SUNAT suelen poner el monto en otra línea)."""
    lineas = _lineas(texto)
    def buscar(etiquetas, excluir=()):
        for i, linea in enumerate(lineas):
            mayus = linea.upper()
            if any(x in mayus for x in excluir):
                continue
            if any(e in mayus for e in etiquetas):
                nums = re.findall(r"\d[\d.,]{2,}", linea)
                if nums:
                    valor = _a_numero(nums[-1])
                    if valor and valor > 0:
                        return valor
                for j in range(i + 1, min(i + 4, len(lineas))):
                    if any(x in lineas[j].upper() for x in ("MONTO", "TOTAL", "DETRACC")):
                        continue
                    nums = re.findall(r"\d[\d.,]{2,}", lineas[j])
                    if nums:
                        valor = _a_numero(nums[0])
                        if valor and valor > 0:
                            return valor
        return None
    for etiquetas, excluir in (
            (("IMPORTE TOTAL", "TOTAL A PAGAR", "TOTAL VENTA", "MONTO NETO PENDIENTE"),
             ("SUB TOTAL", "SUBTOTAL", "DETRACC", "REDONDEO", "CUOTAS")),
            (("TOTAL",), ("SUB TOTAL", "SUBTOTAL", "DETRACC", "REDONDEO", "CUOTAS", "MONTO"))):
        valor = buscar(etiquetas, excluir)
        if valor:
            return valor
    # 3) vouchers: "Monto enviado S/ 0.10", "Importe", "Monto"
    for etiqueta in ("MONTO ENVIADO", "MONTO TRANSFERIDO", "IMPORTE", "MONTO"):
        for i, linea in enumerate(lineas):
            if etiqueta in linea.upper():
                for j in range(i, min(i + 3, len(lineas))):
                    m = re.search(r"S/\s*\.?\s*([\d.,]+)", lineas[j], re.I)
                    if m:
                        valor = _a_numero(m.group(1))
                        if valor and valor > 0:
                            return valor
    # 4) último recurso: el importe con "S/" más alto del documento
    importes = []
    for m in re.finditer(r"S/\s*\.?\s*([\d][\d.,]*)", texto or "", re.I):
        valor = _a_numero(m.group(1))
        if valor and valor > 0:
            importes.append(valor)
    return max(importes) if importes else None


def _proveedor_desde_lineas(texto):
    """Razón social del EMISOR: las líneas antes de «Señor(es)/Cliente/RUC»."""
    lineas = _lineas(texto)
    corte = len(lineas)
    for i, linea in enumerate(lineas):
        if re.match(r"^(SE[ÑN]OR|CLIENTE|SE[ÑN]ORES|ADQUIRIENTE)", linea.upper()):
            corte = i
            break
    candidatas = lineas[:corte] or lineas[:8]
    # 1) Razón social con sufijo legal (S.A.C., E.I.R.L., ...)
    for linea in candidatas[:6]:
        if re.search(r"(S\.?A\.?C|E\.?I\.?R\.?L|S\.?R\.?L|S\.?A\.|SOCIEDAD ANONIMA|SOCIEDAD ANÓNIMA)", linea, re.I):
            return linea[:90]
    # 2) Nombre del titular: la última línea con nombre ANTES de la dirección
    #    (en los PDF de SUNAT el nombre va justo encima de la dirección y del RUC).
    primer_direccion = None
    for i, linea in enumerate(candidatas[:8]):
        if _RE_DIRECCION.search(linea):
            primer_direccion = i
            break
    desde = (primer_direccion - 1) if primer_direccion is not None else min(3, len(candidatas) - 1)
    for i in range(max(0, desde), -1, -1):
        if _parece_nombre_persona(candidatas[i]):
            return candidatas[i][:90]
    # 3) Último recurso: la primera línea que parezca un nombre
    for linea in candidatas[:4]:
        if _parece_nombre_persona(linea):
            return linea[:90]
    return ""


_RE_DIRECCION = re.compile(
    r"(\b(AV|AVENIDA|CAL|CALLE|JR|JIRON|URB|URBANIZACION|DPTO|DEPARTAMENTO|BLOCK|MZ|LT|NRO|N[°º]"
    r"|PASAJE|PROLONGACION|CARRETERA|PISO|OFICINA|INTERIOR|KM)\b|\d{2,}|\s-\s)", re.I)


def _parece_nombre_persona(linea):
    """True si la línea parece un nombre (y no una dirección ni un rótulo)."""
    texto = str(linea or "").strip()
    if not (6 <= len(texto) <= 70):
        return False
    if re.search(r"FACTURA|BOLETA|ELECTR|RUC|SE[ÑN]OR|CLIENTE|TOTAL|FECHA|MONEDA|OBSERVACI", texto, re.I):
        return False
    if _RE_DIRECCION.search(texto):
        return False
    palabras = [p for p in re.split(r"\s+", texto) if p]
    if not (2 <= len(palabras) <= 6):
        return False
    letras = sum(1 for ch in texto if ch.isalpha() or ch == " ")
    return letras >= len(texto) * 0.7

# ---------------------------------------------------------------- procesar
def _huella(ruta):
    try:
        h = hashlib.sha1()
        with open(ruta, "rb") as f:
            for bloque in iter(lambda: f.read(65536), b""):
                h.update(bloque)
        return h.hexdigest()
    except Exception:
        return ""


def _ya_existe(huella):
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM bandeja_documentos WHERE huella=%s LIMIT 1", (huella,))
            return c.fetchone() is not None
    except Exception:
        return False
    finally:
        liberar_conexion(conn)


def _normal(ruta):
    try:
        return os.path.normcase(os.path.abspath(str(ruta)))
    except Exception:
        return str(ruta)


def _ruta_guardada(huella):
    """Ruta que tiene guardada la bandeja para ese documento (o '')."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return ""
    try:
        with conn.cursor() as c:
            c.execute("SELECT ruta FROM bandeja_documentos WHERE huella=%s", (huella,))
            fila = c.fetchone()
            return str(fila[0] or "") if fila else ""
    except Exception:
        return ""
    finally:
        liberar_conexion(conn)


def _localizar_archivo(ruta_guardada, bandeja, huella=""):
    """Ruta REAL del archivo aunque se haya movido entre las subcarpetas."""
    if ruta_guardada and os.path.isfile(ruta_guardada):
        return str(ruta_guardada)
    base = carpeta_bandeja(bandeja or "compra")
    nombre = os.path.basename(str(ruta_guardada or ""))
    carpetas = [base] + [os.path.join(base, sub) for sub in SUBCARPETAS_INTERNAS]
    if nombre:
        for carpeta in carpetas:
            candidato = os.path.join(carpeta, nombre)
            if os.path.isfile(candidato):
                return candidato
    if huella:
        for carpeta in carpetas:
            try:
                for archivo in os.listdir(carpeta):
                    completo = os.path.join(carpeta, archivo)
                    if os.path.isfile(completo) and _huella(completo) == huella:
                        return completo
            except Exception:
                continue
    return ""


def _registro_existe(bandeja, clase, id_registro):
    """¿Sigue existiendo el registro creado desde la bandeja?"""
    try:
        id_registro = int(id_registro or 0)
    except Exception:
        return False
    if id_registro <= 0:
        return False
    if clase == "comprobante":
        tabla = "pagos_comprobantes" if bandeja == "compra" else "pagos_clientes"
    else:
        tabla = "facturas_recibidas" if bandeja == "compra" else "facturas_emitidas"
    conn = conectar_db(silencioso=True)
    if not conn:
        return True   # sin conexión no se toca nada
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM %s WHERE id=%%s" % tabla, (id_registro,))
            return c.fetchone() is not None
    except Exception:
        return True
    finally:
        liberar_conexion(conn)


def _reactivar_si_huerfano(huella, ruta_nueva):
    """Si el PDF ya estaba en la bandeja pero su registro se borró, lo reabre.

    Devuelve True si lo devolvió a la bandeja (para contarlo como reabierto).
    """
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    fila = None
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, bandeja, clase, id_registro FROM bandeja_documentos
                         WHERE huella=%s""", (huella,))
            fila = c.fetchone()
    except Exception:
        fila = None
    finally:
        liberar_conexion(conn)
    if not fila:
        return False
    id_bd, bandeja, clase, id_registro = fila
    if (id_registro or 0) <= 0:
        # Ya estaba pendiente en la bandeja: se conserva tal cual (es una copia)
        return False
    if _registro_existe(bandeja, clase, id_registro):
        return False
    # El registro se eliminó: vuelve a la bandeja para revisarlo/registrarlo otra vez
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""UPDATE bandeja_documentos
                         SET id_registro=0, estado='amarillo', ruta=%s, nota=%s, procesado=''
                         WHERE id=%s""",
                      (ruta_nueva, 'Reabierto: el registro #%s se eliminó' % id_registro, id_bd))
        conn.commit()
        return True
    except Exception as e:
        print("[bandeja] reactivar:", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)


def _guardar_documento(huella, ruta, tipo, clase, estado, confianza, datos, propuesta, nota=""):
    conn = conectar_db(silencioso=True)
    if not conn:
        return 0
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO bandeja_documentos
                    (huella, ruta, bandeja, clase, estado, confianza, datos, propuesta, creado, nota)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (huella) DO NOTHING
                RETURNING id""",
                      (huella, ruta, tipo, clase, estado, int(confianza),
                       json.dumps(datos, ensure_ascii=False),
                       json.dumps(propuesta, ensure_ascii=False),
                       datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), nota))
            fila = c.fetchone()
        conn.commit()
        return int(fila[0]) if fila else 0
    except Exception as e:
        print("[bandeja] guardar:", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        liberar_conexion(conn)


def proponer(tipo, datos, texto):
    """Propuesta (evento, categoría, descripción, OC) + confianza 0-100."""
    abiertos = eventos_abiertos()
    propuesta = {"evento": "", "categoria": "", "descripcion": "", "orden_compra": "",
                 "proveedor": "", "cliente": ""}
    confianza = 0

    if datos.get("numero"):
        confianza += 40
    if datos.get("total"):
        confianza += 20
    if datos.get("fecha"):
        confianza += 10

    ev = evento_en_texto(texto, abiertos)
    if ev:
        propuesta["evento"] = ev["etiqueta"]
        confianza += 15
    elif len(abiertos) == 1:
        propuesta["evento"] = abiertos[0]["etiqueta"]
        confianza += 5

    categoria = categoria_sugerida(texto)
    if categoria:
        propuesta["categoria"] = categoria
        confianza += 10

    proveedor = ""
    if not datos.get("es_comprobante"):
        proveedor = proveedor_en_texto(texto)
    if proveedor:
        if tipo == "venta":
            propuesta["cliente"] = proveedor
        else:
            propuesta["proveedor"] = proveedor

    propuesta["descripcion"] = (categoria or proveedor or
                                (datos.get("tipo") or "Documento") +
                                (" " + datos["numero"] if datos.get("numero") else "")).strip()

    if tipo == "compra" and not propuesta["categoria"]:
        confianza -= 5
    if tipo == "venta" and not propuesta["orden_compra"]:
        confianza -= 5

    confianza = max(0, min(100, confianza))
    if confianza >= UMBRAL_AUTOMATICO and propuesta["evento"]:
        estado = "verde"
    elif confianza >= 50:
        estado = "amarillo"
    else:
        estado = "rojo"
    return propuesta, confianza, estado


def procesar_carpeta(tipo, progreso=None):
    """Analiza los PDFs nuevos de la carpeta. Devuelve un resumen."""
    asegurar_schema()
    carpeta = carpeta_bandeja(tipo)
    resumen = {"nuevos": 0, "duplicados": 0, "reactivados": 0, "ignorados": 0,
               "errores": 0, "detalle": []}
    try:
        archivos = sorted(f for f in os.listdir(carpeta)
                          if f.lower().endswith(".pdf") and os.path.isfile(os.path.join(carpeta, f)))
    except Exception:
        archivos = []
    for i, nombre in enumerate(archivos, start=1):
        if callable(progreso):
            try:
                progreso(i, len(archivos), nombre)
            except Exception:
                pass
        ruta = os.path.join(carpeta, nombre)
        huella = _huella(ruta)
        if not huella:
            resumen["errores"] += 1
            continue
        if _ya_existe(huella):
            guardada = _ruta_guardada(huella)
            if _reactivar_si_huerfano(huella, ruta):
                resumen["reactivados"] += 1
                resumen["detalle"].append({"archivo": nombre, "estado": "reabierto"})
            elif guardada and _normal(guardada) == _normal(ruta):
                # Es EL MISMO archivo que ya está pendiente en la bandeja: no se toca
                resumen["duplicados"] += 1
            else:
                resumen["duplicados"] += 1
                _mover(ruta, tipo, "_duplicados")
            continue
        texto = extraer_texto(ruta)
        datos = interpretar(texto)
        datos["archivo"] = nombre
        clase = "comprobante" if datos.get("es_comprobante") else "factura"
        propuesta, confianza, estado = proponer(tipo, datos, texto)
        # 🏦 Los cargos del banco se dejan en la lista pero marcados: el usuario
        #    decide si los registra o los descarta (NO se ignoran solos).
        if propuesta.get("categoria") == "GASTOS BANCARIOS" and not datos.get("ruc") \
                and not datos.get("es_comprobante"):
            nota_banco = "Cargo del banco (ITF / comisión): normalmente no se registra."
            resumen["ignorados"] += 1
        else:
            nota_banco = ""
        if not texto.strip():
            estado = "rojo"
            nota = "No se pudo leer el texto del PDF."
        elif clase == "comprobante":
            nota = "Comprobante de pago (se empareja con su factura)."
        else:
            nota = ""
        if nota_banco:
            nota = (nota + " " + nota_banco).strip()
        nuevo = _guardar_documento(huella, ruta, tipo, clase, estado, confianza, datos, propuesta, nota)
        if nuevo:
            resumen["nuevos"] += 1
            resumen["detalle"].append({"id": nuevo, "archivo": nombre, "estado": estado,
                                       "confianza": confianza, "clase": clase})
    return resumen


# ---------------------------------------------------------------- consultas
def reanalizar(item):
    """Vuelve a leer y proponer un documento pendiente (con las reglas actuales).

    'item' es una fila de listar(). Devuelve True si actualizó algo.
    """
    ruta_archivo = item.get("ruta") or ""
    if not os.path.isfile(ruta_archivo):
        ruta_archivo = _localizar_archivo(ruta_archivo, item.get("bandeja") or "compra")
    if not ruta_archivo:
        return False
    try:
        texto = extraer_texto(ruta_archivo)
        datos = interpretar(texto)
        datos["archivo"] = os.path.basename(ruta_archivo)
        propuesta, confianza, estado = proponer(item.get("bandeja") or "compra", datos, texto)
        nota = item.get("nota") or ""
        if (propuesta.get("categoria") == "GASTOS BANCARIOS" and not datos.get("ruc")
                and not datos.get("es_comprobante")):
            nota = "Cargo del banco (ITF / comisión): normalmente no se registra."
        conn = conectar_db(silencioso=True)
        if not conn:
            return False
        try:
            with conn.cursor() as c:
                c.execute("""UPDATE bandeja_documentos
                             SET ruta=%s, datos=%s, propuesta=%s, confianza=%s, estado=%s, nota=%s
                             WHERE id=%s""",
                          (ruta_archivo, json.dumps(datos, ensure_ascii=False),
                           json.dumps(propuesta, ensure_ascii=False), int(confianza),
                           estado, nota, item["id"]))
            conn.commit()
            return True
        except Exception as e:
            print("[bandeja] reanalizar:", e)
            try:
                conn.rollback()
            except Exception:
                pass
            return False
        finally:
            liberar_conexion(conn)
    except Exception as e:
        print("[bandeja] reanalizar:", e)
        return False


def reanalizar_pendientes(bandeja=None):
    """Relee todos los pendientes con las reglas actuales. Devuelve cuántos."""
    total = 0
    for item in listar(bandeja):
        if item.get("id_registro"):
            continue
        if reanalizar(item):
            total += 1
    return total


def listar(bandeja=None, estados=None):
    asegurar_schema()
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    filas = []
    try:
        with conn.cursor() as c:
            sql = ("SELECT id, ruta, bandeja, clase, estado, confianza, datos, propuesta, "
                   "id_registro, creado, nota FROM bandeja_documentos WHERE id_registro = 0")
            params = []
            if bandeja:
                sql += " AND bandeja=%s"
                params.append(bandeja)
            if estados:
                sql += " AND estado = ANY(%s)"
                params.append(list(estados))
            sql += " ORDER BY CASE estado WHEN 'rojo' THEN 0 WHEN 'amarillo' THEN 1 ELSE 2 END, id"
            c.execute(sql, tuple(params))
            for r in c.fetchall():
                filas.append({
                    "id": r[0], "ruta": r[1], "bandeja": r[2], "clase": r[3],
                    "estado": r[4], "confianza": r[5],
                    "datos": json.loads(r[6] or "{}"),
                    "propuesta": json.loads(r[7] or "{}"),
                    "id_registro": r[8], "creado": r[9], "nota": r[10] or "",
                })
    except Exception as e:
        print("[bandeja] listar:", e)
    finally:
        liberar_conexion(conn)
    return filas


def _mover(ruta, tipo, cual):
    destino = _carpeta_interna(tipo, cual)
    try:
        os.makedirs(destino, exist_ok=True)
        if _normal(os.path.dirname(ruta)) == _normal(destino):
            return str(ruta)   # ya está en esa carpeta
        base = os.path.basename(ruta)
        final = os.path.join(destino, base)
        if os.path.exists(final):
            marca = datetime.datetime.now().strftime("%H%M%S")
            final = os.path.join(destino, "%s_%s" % (marca, base))
        shutil.move(ruta, final)
        return final
    except Exception as e:
        print("[bandeja] mover:", e)
        return ""


def _copiar_a_definitiva(ruta, tipo, clase, datos):
    """Copia el PDF a la carpeta definitiva del programa y devuelve la ruta BD."""
    base = carpeta_base()
    if tipo == "compra":
        sub = "comprobantes_egresos" if clase == "comprobante" else "facturas_recibidas"
    else:
        sub = "comprobantes_ingresos" if clase == "comprobante" else "facturas_emitidas"
    destino = os.path.join(base, sub)
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception:
        pass
    numero = re.sub(r"[^A-Za-z0-9\-]+", "_", str(datos.get("numero") or "")) or "documento"
    marca = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre = "%s_%s.pdf" % (numero, marca)
    final = os.path.join(destino, nombre)
    try:
        shutil.copy2(ruta, final)
        return ruta_para_guardar(final) or final
    except Exception as e:
        print("[bandeja] copiar:", e)
        return ""


def descartar(item_id, motivo=""):
    item = [x for x in listar() if x["id"] == item_id]
    if not item:
        return False
    it = item[0]
    _mover(it["ruta"], it["bandeja"], "_errores")
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("UPDATE bandeja_documentos SET id_registro=-1, nota=%s WHERE id=%s",
                      ((motivo or "Descartado").strip(), item_id))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        liberar_conexion(conn)
# ---------------------------------------------------------------- registrar
def _buscar_factura_por_monto(bandeja, total, entidad=""):
    """Factura del mismo monto (y entidad) para emparejar el comprobante."""
    if not total:
        return 0
    tabla = "facturas_recibidas" if bandeja == "compra" else "facturas_emitidas"
    campo = "proveedor" if bandeja == "compra" else "cliente"
    conn = conectar_db(silencioso=True)
    if not conn:
        return 0
    try:
        with conn.cursor() as c:
            sql = ("SELECT id, %s FROM %s WHERE total BETWEEN %%s AND %%s "
                   "ORDER BY id DESC LIMIT 10" % (campo, tabla))
            c.execute(sql, (float(total) - 0.05, float(total) + 0.05))
            filas = c.fetchall()
        if not filas:
            return 0
        if entidad:
            texto = str(entidad).upper()
            for fid, nombre in filas:
                if texto and (texto in str(nombre or "").upper()
                              or str(nombre or "").upper() in texto):
                    return int(fid)
        # Si hay VARIAS facturas con el mismo monto no se adivina: se deja sin
        # emparejar para que el usuario lo elija (evita cargar el pago a otra).
        if len(filas) > 1:
            return 0
        return int(filas[0][0])
    except Exception as e:
        print("[bandeja] buscar factura:", e)
        return 0
    finally:
        liberar_conexion(conn)


def _buscar_factura_existente(bandeja, numero, ruc="", entidad="", total=0):
    """Id de una factura ya registrada con el mismo número (o mismo RUC+monto)."""
    tabla = "facturas_recibidas" if bandeja == "compra" else "facturas_emitidas"
    campo = "proveedor" if bandeja == "compra" else "cliente"
    conn = conectar_db(silencioso=True)
    if not conn:
        return 0
    try:
        with conn.cursor() as c:
            numero = str(numero or "").strip()
            if numero:
                c.execute("SELECT id, COALESCE(total,0), COALESCE(ruc,'') FROM %s "
                          "WHERE UPPER(TRIM(numero_documento)) = UPPER(TRIM(%%s)) ORDER BY id"
                          % tabla, (numero,))
                for fid, tot_existente, ruc_existente in c.fetchall():
                    # Mismo número: se considera la MISMA factura si el importe
                    # coincide (1%) o si el RUC es el mismo. Si no, es otro documento
                    # (a veces se repite el número entre proveedores distintos).
                    try:
                        tot_existente = float(tot_existente or 0)
                        total_f = float(total or 0)
                    except Exception:
                        tot_existente, total_f = 0.0, 0.0
                    tolerancia = max(1.0, abs(tot_existente) * 0.01)
                    if (tot_existente and total_f and abs(tot_existente - total_f) <= tolerancia)                             or (ruc and str(ruc) == str(ruc_existente)):
                        return int(fid)
                return 0
            # Sin número: mismo RUC + mismo total + mismo nombre
            if ruc and total:
                sql = ("SELECT id FROM %s WHERE COALESCE(ruc,'')=%%s "
                       "AND total BETWEEN %%s AND %%s" % tabla)
                c.execute(sql, (str(ruc), float(total) - 0.05, float(total) + 0.05))
                fila = c.fetchone()
                if fila:
                    return int(fila[0])
            return 0
    except Exception as e:
        print("[bandeja] buscar existente:", e)
        return 0
    finally:
        liberar_conexion(conn)


def _emparejar_comprobante(bandeja, datos, entidad=""):
    """(id_factura, monto_pagado) del comprobante.

    Prueba los importes del documento (incluido el 'monto enviado' sin comisión)
    contra el TOTAL y contra el NETO de detracción: el cliente suele depositar
    el total menos la detracción.
    """
    candidatos = []
    for valor in (datos.get("montos") or []):
        try:
            valor = round(float(valor), 2)
            if valor > 0:
                candidatos.append(valor)
        except Exception:
            continue
    if datos.get("total"):
        try:
            candidatos.append(round(float(datos["total"]), 2))
        except Exception:
            pass
    candidatos = sorted(set(candidatos), reverse=True)
    if not candidatos:
        return 0, 0.0

    tabla = "facturas_recibidas" if bandeja == "compra" else "facturas_emitidas"
    campo = "proveedor" if bandeja == "compra" else "cliente"
    # Rango amplio: el depósito puede ser MENOR que el total (detracción) o algo mayor
    minimo = max(0.0, min(candidatos) * 0.85 - 1)
    maximo = max(candidatos) * 1.20 + 1
    conn = conectar_db(silencioso=True)
    filas = []
    if conn:
        try:
            with conn.cursor() as c:
                c.execute(("SELECT id, COALESCE(total,0), COALESCE(det_monto,0), COALESCE(%s,'') "
                           "FROM %s WHERE total BETWEEN %%s AND %%s ORDER BY id DESC LIMIT 80"
                           % (campo, tabla)), (minimo, maximo))
                filas = c.fetchall()
        except Exception as e:
            print("[bandeja] emparejar comprobante:", e)
        finally:
            liberar_conexion(conn)

    texto = str(entidad or "").upper()

    def _afinidad(nombre):
        nombre = str(nombre or "").upper()
        if not texto or not nombre:
            return 0
        return 2 if (texto in nombre or nombre in texto) else 0

    # 1) coincidencia exacta con el total o con el neto de detracción ya registrado
    for importe in candidatos:
        mejores = []
        for fid, total_f, det, nombre in filas:
            total_f = float(total_f or 0)
            neto = total_f - float(det or 0)
            if abs(importe - total_f) <= 0.05 or (neto > 0 and abs(importe - neto) <= 0.05):
                mejores.append((_afinidad(nombre), int(fid)))
        if mejores:
            mejores.sort(reverse=True)
            return mejores[0][1], importe, 0.0

    # 2) el cliente depositó el TOTAL menos la detracción (12%, 10%, 4%, 3%, 1.5%)
    #    Se elige el importe MÁS CERCANO al neto esperado (no el primero que encaje).
    mejor = None
    for porcentaje in (12.0, 10.0, 4.0, 3.0, 1.5):
        for fid, total_f, det, nombre in filas:
            total_f = float(total_f or 0)
            if total_f <= 0:
                continue
            esperado = total_f * (1 - porcentaje / 100.0)
            for importe in candidatos:
                diferencia = abs(importe - esperado)
                if diferencia <= max(1.0, total_f * 0.005):
                    clave = (round(diferencia, 2), -_afinidad(nombre), porcentaje)
                    if mejor is None or clave < mejor[0]:
                        mejor = (clave, int(fid), importe, porcentaje)
    if mejor:
        return mejor[1], mejor[2], mejor[3]
    return 0, float(datos.get("total") or 0), 0.0


def _pago_duplicado(bandeja, id_factura, total):
    """¿Esa factura ya tiene un pago por el mismo monto?"""
    tabla = "pagos_comprobantes" if bandeja == "compra" else "pagos_clientes"
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1 FROM %s WHERE id_factura=%%s AND monto_pagado BETWEEN %%s AND %%s"
                      % tabla, (id_factura, float(total) - 0.05, float(total) + 0.05))
            return c.fetchone() is not None
    except Exception:
        return False
    finally:
        liberar_conexion(conn)


def _desglose(total, tipo_documento=""):
    """(subtotal, impuesto) a partir del total, según el tipo de documento."""
    try:
        total = float(total or 0)
    except Exception:
        total = 0.0
    tipo = str(tipo_documento or "").lower()
    if "recibo" in tipo or "honorario" in tipo or "nota de cr" in tipo:
        return round(total, 2), 0.0
    subtotal = round(total / 1.18, 2)
    return subtotal, round(total - subtotal, 2)


def registrar(item_id, ajustes=None):
    """Registra el documento en el módulo real y mueve el archivo.

    Devuelve (ok, mensaje, id_registro).
    """
    asegurar_schema()
    conn = conectar_db(silencioso=True)
    if not conn:
        return False, "Sin conexión a la base de datos.", 0
    item = None
    try:
        with conn.cursor() as c:
            c.execute("""SELECT id, ruta, bandeja, clase, datos, propuesta
                         FROM bandeja_documentos WHERE id=%s""", (item_id,))
            r = c.fetchone()
            if r:
                item = {"id": r[0], "ruta": r[1], "bandeja": r[2], "clase": r[3],
                        "datos": json.loads(r[4] or "{}"), "propuesta": json.loads(r[5] or "{}")}
    except Exception as e:
        print("[bandeja] cargar item:", e)
    finally:
        liberar_conexion(conn)
    if not item:
        return False, "El documento ya no está en la bandeja.", 0
    if not os.path.isfile(item["ruta"]):
        # El archivo pudo moverse entre las subcarpetas (_procesados, _duplicados…)
        real = _localizar_archivo(item["ruta"], item["bandeja"])
        if not real:
            return False, ("El archivo ya no está en la carpeta de la bandeja." + chr(10) +
                           "Vuelva a agregarlo con «📎 Agregar PDF»."), 0
        item["ruta"] = real
        conn_fix = conectar_db(silencioso=True)
        try:
            with conn_fix.cursor() as c:
                c.execute("UPDATE bandeja_documentos SET ruta=%s WHERE id=%s", (real, item_id))
            conn_fix.commit()
        except Exception:
            pass
        finally:
            liberar_conexion(conn_fix)

    propuesta = dict(item["propuesta"] or {})
    propuesta.update(ajustes or {})
    datos = item["datos"] or {}
    tipo_doc = str(datos.get("tipo") or "Factura (18% IGV)")
    total = float(datos.get("total") or 0) or 0.0
    subtotal, impuesto = _desglose(total, tipo_doc)
    fecha = str(datos.get("fecha") or datetime.datetime.now().strftime("%d/%m/%Y"))
    numero = str(datos.get("numero") or "")
    ruc = str(datos.get("ruc") or "")
    descripcion = str(propuesta.get("descripcion") or "").strip()
    evento = str(propuesta.get("evento") or "").strip()
    categoria = str(propuesta.get("categoria") or "").strip()
    entidad = str(propuesta.get("proveedor") or propuesta.get("cliente") or "").strip()

    ruta_bd = _copiar_a_definitiva(item["ruta"], item["bandeja"], item["clase"], datos)
    if not ruta_bd:
        return False, "No se pudo copiar el archivo a su carpeta definitiva.", 0

    nuevo_id = 0
    nota = ""
    conn = conectar_db(silencioso=True)
    if not conn:
        return False, "Sin conexión a la base de datos.", 0
    try:
        with conn.cursor() as c:
            if item["clase"] == "factura":
                existente = _buscar_factura_existente(item["bandeja"], numero, ruc, entidad, total)
                if existente:
                    # 🔁 Ya estaba registrada: se COMPLETA en vez de duplicarla
                    campos = ["proveedor", "cliente", "categoria", "evento_asociado",
                              "descripcion", "ruc", "archivo_ruta"]
                    if item["bandeja"] == "compra":
                        campos = ["proveedor", "categoria", "evento_asociado", "descripcion",
                                  "ruc", "archivo_ruta"]
                        valores = {"proveedor": entidad, "categoria": categoria,
                                   "evento_asociado": evento, "descripcion": descripcion,
                                   "ruc": ruc, "archivo_ruta": ruta_bd}
                    else:
                        campos = ["cliente", "evento_asociado", "descripcion", "archivo_ruta",
                                  "orden_compra"]
                        valores = {"cliente": entidad, "evento_asociado": evento,
                                   "descripcion": descripcion, "archivo_ruta": ruta_bd,
                                   "orden_compra": str(propuesta.get("orden_compra") or "")}
                    sets, params = [], []
                    for campo in campos:
                        valor = valores.get(campo)
                        if valor:
                            sets.append("%s = %%s" % campo)
                            params.append(valor)
                    if sets:
                        params.append(existente)
                        c.execute("UPDATE %s SET %s WHERE id = %%s"
                                  % ("facturas_recibidas" if item["bandeja"] == "compra"
                                     else "facturas_emitidas", ", ".join(sets)), tuple(params))
                    nuevo_id = int(existente)
                    nota = ("Ya existía (id #%s): se completaron sus datos en vez de duplicarla"
                            % existente)
                    c.execute("""UPDATE bandeja_documentos
                                 SET id_registro=%s, estado='registrado', procesado=%s, nota=%s
                                 WHERE id=%s""",
                              (nuevo_id, datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                               nota, item_id))
                    conn.commit()
                    _mover(item["ruta"], item["bandeja"], "_procesados")
                    try:
                        from buffer_memoria import cache_sistema
                        cache_sistema.invalidar()
                    except Exception:
                        pass
                    return True, nota, nuevo_id
                if item["bandeja"] == "compra":
                    c.execute("""
                        INSERT INTO facturas_recibidas
                            (tipo_documento, fecha, proveedor, descripcion, evento_asociado,
                             subtotal, impuesto, total, archivo_ruta, numero_documento,
                             categoria, ruc)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (tipo_doc, fecha, entidad, descripcion, evento, subtotal,
                               impuesto, total, ruta_bd, numero, categoria, ruc))
                else:
                    c.execute("""
                        INSERT INTO facturas_emitidas
                            (tipo_documento, fecha, cliente, descripcion, evento_asociado,
                             subtotal, impuesto, total, archivo_ruta, numero_documento,
                             orden_compra)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (tipo_doc, fecha, entidad, descripcion, evento, subtotal,
                               impuesto, total, ruta_bd, numero,
                               str(propuesta.get("orden_compra") or "")))
                nuevo_id = int(c.fetchone()[0])
            else:  # comprobante de pago
                id_factura, monto_pago, detraccion = _emparejar_comprobante(
                    item["bandeja"], datos, entidad)
                nota_detraccion = ""
                if monto_pago:
                    total = float(monto_pago)
                if id_factura and detraccion:
                    # Depositó el total menos la detracción: se completa la detracción
                    # de la factura para que quede cancelada.
                    try:
                        tabla_f = ("facturas_recibidas" if item["bandeja"] == "compra"
                                   else "facturas_emitidas")
                        # La detracción se calcula sobre el TOTAL DE LA FACTURA
                        # (no sobre el depósito, que ya viene sin ella).
                        total_factura = 0.0
                        try:
                            c.execute("SELECT COALESCE(total,0) FROM %s WHERE id=%%s" % tabla_f,
                                      (id_factura,))
                            fila_f = c.fetchone()
                            total_factura = float(fila_f[0] or 0) if fila_f else 0.0
                        except Exception:
                            total_factura = 0.0
                        if not total_factura and detraccion < 100:
                            total_factura = float(total) / (1 - detraccion / 100.0)
                        c.execute(("UPDATE %s SET det_porcentaje=%%s, det_monto=%%s "
                                   "WHERE id=%%s AND COALESCE(det_monto,0)=0" % tabla_f),
                                  (detraccion,
                                   round(total_factura * detraccion / 100.0, 2),
                                   id_factura))
                        nota_detraccion = " con detracción del %g%%" % detraccion
                    except Exception:
                        nota_detraccion = ""
                # 🚫 ¿Ya existe un pago igual para esa factura? (no duplicar)
                if id_factura and _pago_duplicado(item["bandeja"], id_factura, total):
                    return (False, "Ya hay un pago de %s registrado para la factura #%s."
                            % (total, id_factura), 0)
                operacion = str(datos.get("numero") or "")
                if item["bandeja"] == "compra":
                    c.execute("""
                        INSERT INTO pagos_comprobantes
                            (id_factura, monto_pagado, archivo_ruta, proveedor_nombre,
                             fecha_pago, numero_operacion)
                        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (id_factura, total, ruta_bd, entidad, fecha, operacion))
                else:
                    c.execute("""
                        INSERT INTO pagos_clientes
                            (id_factura, monto_pagado, archivo_ruta, cliente_nombre,
                             fecha_pago, numero_operacion)
                        VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (id_factura, total, ruta_bd, entidad, fecha, operacion))
                nuevo_id = int(c.fetchone()[0])
                if id_factura:
                    nota = "Emparejado con la factura #%s" % id_factura
                else:
                    nota = "Sin factura emparejada (monto %s)" % total
            c.execute("""UPDATE bandeja_documentos
                         SET id_registro=%s, estado='registrado', procesado=%s, nota=%s
                         WHERE id=%s""",
                      (nuevo_id, datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
                       nota, item_id))
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, "No se pudo registrar: %s" % str(e)[:160], 0
    finally:
        liberar_conexion(conn)

    _mover(item["ruta"], item["bandeja"], "_procesados")
    try:
        from buffer_memoria import cache_sistema
        cache_sistema.invalidar()
    except Exception:
        pass
    return True, ("Registrado correctamente." + (" " + nota if nota else "")), nuevo_id

