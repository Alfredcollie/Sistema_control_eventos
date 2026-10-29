# -*- coding: utf-8 -*-
"""Lector LOCAL de comprobantes de venta (facturas, boletas y recibos).

Lee el texto que devuelve el OCR/PDF del comprobante y extrae los datos.
Está pensado para los escaneos de SUNAT, donde el OCR:
  * pierde las vocales acentuadas ("Emisión" -> "Emisin", "detracción" -> "detraccin"),
  * pierde la "ñ" ("Señor(es)" -> "Seor(es)") y los dos puntos,
  * a veces no lee la columna de importes (solo las etiquetas).

No usa internet ni servicios externos.
"""
import re
import unicodedata

MONTOS = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{2})|\d{1,3}(?:[.,]\d{2})|\d+)(?![\d])")


def a_numero(txt):
    """'19,907.19' / '19907.19' / 'S/ 19 907,19' -> float."""
    s = str(txt or "").replace(" ", "").replace("S/", "").replace("s/", "")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        return float(s)
    except Exception:
        return 0.0


def _limpio(txt):
    """Quita acentos y el carácter \x07 que inserta el OCR entre columnas."""
    t = unicodedata.normalize("NFKD", str(txt or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.replace("\x07", " ")


def extraer_datos_venta(texto, ruc_propio=""):
    """Devuelve dict con tipo, numero_documento, fecha, cliente, ruc, subtotal,
    total, detraccion y descripcion. Los campos que no se lean quedan vacíos/0."""
    datos = {"tipo": "factura", "numero_documento": "", "fecha": "", "cliente": "",
             "ruc": "", "descripcion": "", "subtotal": 0.0, "total": 0.0, "detraccion": 0.0}
    plano = _limpio(texto)
    if not plano.strip():
        return datos
    lineas = [l.strip() for l in plano.splitlines() if l.strip()]

    # ---- tipo ----
    if re.search(r"BOLETA\s+DE\s+VENTA", plano, re.I):
        datos["tipo"] = "boleta"
    elif re.search(r"RECIBO\s+POR\s+HONORARIOS", plano, re.I):
        datos["tipo"] = "recibo"
    else:
        datos["tipo"] = "factura"

    # ---- número (serie-correlativo) ----
    m = re.search(r"\b([EFB][0-9A-Z]{2,3})\s*-\s*(\d{1,8})\b", plano)
    if m:
        datos["numero_documento"] = "%s-%s" % (m.group(1), m.group(2))

    # ---- fecha ----
    m = re.search(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})", plano)
    if m:
        dia, mes, anio = m.groups()
        if len(anio) == 2:
            anio = "20" + anio
        datos["fecha"] = "%s/%s/%s" % (dia, mes, anio)

    # ---- cliente ("Señor(es) NOMBRE" llega como "Seor(es) NOMBRE") ----
    for i, linea in enumerate(lineas):
        m = re.match(r"(?:se[nñ]?or(?:es)?(?:\(\w*\))?|se[nñ]?ores|cliente|raz\w*n\s+social|"
                     r"recib\w*\s+de)\s*[:\-]?\s*(.+)$", linea, re.I)
        if m and len(m.group(1).strip(" :-.")) > 2:
            datos["cliente"] = m.group(1).strip(" :-.")
            if i > 0:
                anterior = lineas[i - 1]
                if (len(anterior) > 4
                        and not re.search(r"RUC|FACTURA|BOLETA|FECHA|DIREC|MONEDA|TIPO\s+DE",
                                          anterior, re.I)
                        and not MONTOS.fullmatch(anterior.strip())):
                    datos["cliente_comercial"] = anterior.strip(" :-.")
            break

    # ---- RUC del cliente (el que NO es el de la propia empresa) ----
    rucs = re.findall(r"\b(\d{11})\b", plano)
    excluir = {str(ruc_propio).strip(), "20613989146", "20613993682"}
    ajenos = [r for r in dict.fromkeys(rucs) if r and r not in excluir]
    if ajenos:
        datos["ruc"] = ajenos[0]

    # ---- importes ----
    def monto_de(patron, ventana=2):
        rx = re.compile(patron, re.I)
        for i, linea in enumerate(lineas):
            if rx.search(linea):
                resto = rx.split(linea, maxsplit=1)[-1]
                m2 = MONTOS.search(resto)
                if m2:
                    return a_numero(m2.group(1))
                for j in range(i + 1, min(i + 1 + ventana, len(lineas))):
                    m3 = MONTOS.search(lineas[j])
                    if m3:
                        return a_numero(m3.group(1))
        return 0.0

    total = monto_de(r"importe\s+total")
    subtotal = monto_de(r"valor\s+venta(?!\s+de)|sub\s*total\s+ventas|op\.?\s*gravadas|"
                        r"total\s+por\s+honorarios")
    detraccion = monto_de(r"porcentaje\s+de\s+detracc\w*n")

    if total <= 0:
        # Facturas con detracción: neto pendiente + monto de la detracción
        neto = monto_de(r"monto\s+neto\s+pendiente")
        monto_det = monto_de(r"monto\s+detracc\w*n\s*:")
        if neto > 0 and monto_det > 0:
            total = neto + monto_det
    if subtotal <= 0 and total > 0:
        subtotal = round(total / 1.18, 2) if datos["tipo"] == "factura" else total
    if total <= 0 and subtotal > 0:
        total = round(subtotal * 1.18, 2) if datos["tipo"] == "factura" else subtotal

    datos["subtotal"] = round(subtotal, 2)
    datos["total"] = round(total, 2)
    datos["detraccion"] = detraccion
    return datos
