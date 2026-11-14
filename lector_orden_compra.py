# -*- coding: utf-8 -*-
"""
=========================================================
LECTOR_ORDEN_COMPRA.PY - Lectura de Órdenes de Compra de clientes
=========================================================
Lee una OC en PDF digital o escaneada (imagen) y devuelve los datos
generales que usa el módulo "Órdenes de Compra (Clientes)":

    numero_oc, fecha, cliente, descripcion, moneda,
    subtotal, igv, total

Estrategia:
    1. Si el PDF trae texto digital, se lee con pdfplumber (rápido y exacto).
    2. Si es un PDF escaneado o una imagen, se usa el OCR del sistema
       (Windows OCR / Apple Vision) a través de escaner.ocr_paginas().
    3. Las reglas entienden el formato de NGR ("ORDEN DE SERVICIO",
       "Pedido N° 4500746961", "SUB-TOTAL PEN", "IGV 18.00 %", "TOTAL PEN")
       y también formatos genéricos de otros clientes.

No guarda nada en la base de datos: solo devuelve un diccionario.
"""

import os
import re
import unicodedata

try:
    import pdfplumber
except Exception:
    pdfplumber = None


# =========================================================
# UTILIDADES DE TEXTO Y NÚMEROS
# =========================================================
def sin_tildes(texto):
    """Devuelve el texto sin tildes y en mayúsculas (para comparar)."""
    normal = unicodedata.normalize("NFD", str(texto or ""))
    limpio = "".join(c for c in normal if unicodedata.category(c) != "Mn")
    return limpio.upper()


def _lineas(texto):
    salida = []
    for linea in str(texto or "").splitlines():
        linea = re.sub(r"[ \t]+", " ", linea).strip()
        if linea:
            salida.append(linea)
    return salida


def numero_a_float(valor):
    """Convierte '22,033.60', 'S/ 1.234,56' o '1,248.91' a float."""
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).replace("\u00a0", " ").strip()
    texto = re.sub(r"[^\d.,\-]", "", texto).strip(".,-")
    if not texto:
        return 0.0
    negativo = texto.startswith("-")
    texto = texto.lstrip("-")
    try:
        if "," in texto and "." in texto:
            if texto.rfind(",") > texto.rfind("."):
                texto = texto.replace(".", "").replace(",", ".")
            else:
                texto = texto.replace(",", "")
        elif "," in texto:
            if re.fullmatch(r"\d{1,3}(,\d{3})+", texto):
                texto = texto.replace(",", "")
            else:
                texto = texto.replace(",", ".")
        else:
            if re.fullmatch(r"\d{1,3}(\.\d{3})+", texto):
                texto = texto.replace(".", "")
        numero = float(texto)
    except Exception:
        return 0.0
    return -numero if negativo else numero


def _numeros_de(linea):
    return re.findall(r"\d+(?:[.,]\d+)*", str(linea or ""))


def _importe_de_linea(linea):
    """Último importe de una línea, ignorando porcentajes como '18.00 %'."""
    limpia = re.sub(r"\b\d{1,2}(?:[.,]\d+)?\s*%", " ", str(linea or ""))
    numeros = _numeros_de(limpia)
    return numero_a_float(numeros[-1]) if numeros else 0.0


def _redondear(valor):
    return round(float(valor or 0.0) + 0.0, 2)


# =========================================================
# FECHA
# =========================================================
MESES = {
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
    "JULIO": 7, "AGOSTO": 8, "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10,
    "NOVIEMBRE": 11, "DICIEMBRE": 12,
    "JANUARY": 1, "FEBRUARY": 2, "MARCH": 3, "APRIL": 4, "MAY": 5, "JUNE": 6,
    "JULY": 7, "AUGUST": 8, "SEPTEMBER": 9, "OCTOBER": 10, "NOVEMBER": 11,
    "DECEMBER": 12,
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "JUN": 6, "JUL": 7, "AGO": 8,
    "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}


def _fecha_valida(dia, mes, anio):
    try:
        dia, mes, anio = int(dia), int(mes), int(anio)
    except Exception:
        return ""
    if anio < 100:
        anio += 2000
    if not (1 <= dia <= 31 and 1 <= mes <= 12 and 1990 <= anio <= 2100):
        return ""
    return f"{dia:02d}/{mes:02d}/{anio:04d}"


def _fecha_desde_texto(texto):
    """Busca una fecha en el texto y la devuelve como dd/mm/aaaa."""
    # 1. "Fecha 12.11.2026" / "Fecha de emisión: 12/11/26"
    patrones = [
        r"FECHA(?:\s+(?:DE\s+)?EMISION)?\s*[:.]?\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{2,4})",
        r"(\d{1,2})\s+DE\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(?:DE|DEL)\s+(\d{4})",
        r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})",
        r"(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{4})",
    ]
    plano = sin_tildes(texto)
    for indice, patron in enumerate(patrones):
        for m in re.finditer(patron, plano, re.IGNORECASE):
            if indice == 1:
                mes = MESES.get(m.group(2).upper(), 0)
                fecha = _fecha_valida(m.group(1), mes, m.group(3))
            elif indice == 2:
                fecha = _fecha_valida(m.group(3), m.group(2), m.group(1))
            else:
                fecha = _fecha_valida(m.group(1), m.group(2), m.group(3))
            if fecha:
                return fecha
    return ""


# =========================================================
# NÚMERO DE ORDEN
# =========================================================
def _candidato_oc_valido(cand):
    cand = str(cand or "").strip().strip(".,;:()[]{}")
    if not (4 <= len(cand) <= 40):
        return ""
    if not re.search(r"\d", cand):
        return ""
    if sin_tildes(cand) in ("FECHA", "PEDIDO", "ORDEN", "COMPRA", "SERVICIO", "CLIENTE"):
        return ""
    return cand


def _numero_oc_desde_texto(texto):
    """Extrae el N° de OC/Pedido, tolerando el orden irregular del OCR."""
    plano = sin_tildes(texto)

    # 1. Etiquetas explícitas con el número en la misma línea.
    patrones = [
        r"PEDIDO\s*(?:N[\u00b0\u00baO.]*|NRO\.?|NUMERO|NO\.?)?\s*[:#\-]?\s*([A-Z]{0,5}-?[0-9][0-9A-Za-z\-_/]{2,30})",
        r"(?:ORDEN\s+DE\s+(?:COMPRA|SERVICIO|PEDIDO)|ORDEN\s+COMPRA)\s*(?:N[\u00b0\u00baO.]*|NRO\.?|NUMERO|NO\.?)?\s*[:#\-]?\s*([A-Z]{0,5}-?[0-9][0-9A-Za-z\-_/]{2,30})",
        r"(?:O/?C|P\.?O\.?)\s*(?:N[\u00b0\u00baO.]*|NRO\.?|NO\.?)?\s*[:#\-]?\s*([A-Z]{0,5}-?[0-9][0-9A-Za-z\-_/]{2,30})",
        r"(?:FOLIO|REFERENCIA|COTIZACION)\s*(?:N[\u00b0\u00baO.]*|NRO\.?|NO\.?)?\s*[:#\-]?\s*([A-Z]{0,5}-?[0-9][0-9A-Za-z\-_/]{2,30})",
        r"\b([A-Z]{2,5}-\d{3,12})\b",
    ]
    for patron in patrones:
        for m in re.finditer(patron, plano, re.IGNORECASE):
            cand = _candidato_oc_valido(m.group(1))
            if cand:
                return cand

    # 2. OCR: la etiqueta y el número pueden quedar en líneas distintas y en
    #    cualquier orden. Se puntúan todos los candidatos y se elige el mejor.
    plano_prov = plano.find("DATOS DEL PROVEEDOR")
    fin_prov = len(plano)
    if plano_prov >= 0:
        for marca in ("CONTACTO", "TERMINOS Y CONDICIONES", "OBSERVACION"):
            pos_marca = plano.find(marca, plano_prov + 1)
            if pos_marca > plano_prov:
                fin_prov = min(fin_prov, pos_marca)
    posiciones_etiqueta = [m.start() for m in re.finditer(
        r"(PEDIDO|ORDEN\s+DE\s+COMPRA|ORDEN\s+DE\s+SERVICIO)", plano)]

    mejor, mejor_puntaje = "", -1.0
    for m in re.finditer(r"(?<![\d.,/])(\d{6,15})(?![\d.,/])", texto):
        cand, pos = m.group(1), m.start()
        # Descarta RUC (11 dígitos que empiezan con 10/15/17/20),
        # teléfonos (9 dígitos que empiezan con 9) y fechas compactas.
        if len(cand) == 11 and cand[:2] in ("10", "15", "17", "20"):
            continue
        if len(cand) == 9 and cand.startswith("9"):
            continue
        if len(cand) == 8 and cand[:2] in ("19", "20"):
            continue

        previo = plano[max(0, pos - 45):pos]
        if re.search(r"(RUC|CODIGO|TELEFONO|CELULAR|DNI|FECHA|DOCUMENTO|ITEM)\s*[:.]?\s*$", previo):
            continue

        inicio = texto.rfind("\n", 0, pos) + 1
        fin = texto.find("\n", pos)
        fin = len(texto) if fin < 0 else fin
        linea = texto[inicio:fin].strip()

        puntaje = 0.0
        # Un número solo en su línea tiene más pinta de N° de OC.
        if linea == cand:
            puntaje += 4.0
        # Fuera del bloque del proveedor (código, RUC, teléfono).
        if plano_prov < 0 or not (plano_prov <= pos < fin_prov):
            puntaje += 3.0
        if 8 <= len(cand) <= 12:
            puntaje += 1.0
        # Cerca de "Pedido" / "Orden de compra".
        distancia = min((abs(pos - e) for e in posiciones_etiqueta), default=99999)
        puntaje += max(0.0, 3.0 - distancia / 600.0)
        # Un código de artículo se repite decenas de veces: se penaliza.
        repeticiones = len(re.findall(r"(?<![\d.,/])" + re.escape(cand) + r"(?![\d.,/])", texto))
        if repeticiones > 3:
            puntaje -= min(8.0, repeticiones / 10.0)
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje = cand, puntaje

    if mejor:
        return mejor

    # 3. Último recurso: una línea que sea solo un número largo.
    for linea in _lineas(texto):
        limpia = linea.strip()
        if re.fullmatch(r"\d{8,15}", limpia):
            if len(limpia) == 11 and limpia[:2] in ("10", "15", "17", "20"):
                continue
            return limpia
    return ""


# =========================================================
# CLIENTE / PROVEEDOR
# =========================================================
SUFIJOS_EMPRESA = re.compile(
    r"\b(S\.?A\.?C\.?|S\.?A\.?S\.?|S\.?A\.?|S\.?R\.?L\.?|E\.?I\.?R\.?L\.?|"
    r"LTDA\.?|SOCIEDAD\s+ANONIMA|CORPORATION|CORP\.?|COMPANY|DISTRIBUIDORA)\b",
    re.IGNORECASE,
)

PALABRAS_NO_CLIENTE = (
    "BLACK CUBE", "COLLIE", "DATOS DEL PROVEEDOR", "ORDEN DE SERVICIO",
    "ORDEN DE COMPRA", "TERMINOS Y CONDICIONES", "OBSERVACION", "CONTACTO",
    "DIRECCION DE ENTREGA", "RUC:", "CODIGO:",
)


def _limpiar_empresa(linea):
    texto = str(linea or "").strip()
    texto = re.sub(r"^(CLIENTE|RAZON SOCIAL|SEÑOR(?:\s*\(ES\)|\s*ES)?|SENOR(?:\s*\(ES\)|\s*ES)?|SENORES|EMPRESA|COMPRADOR)\s*[:.]?\s*", "", texto, flags=re.IGNORECASE)
    # En el PDF digital la razón social puede venir pegada a "Fecha ..." en la misma línea.
    texto = re.split(r"\s+(?:FECHA|RUC|PEDIDO|ORDEN\s+DE|COTIZACION|MONEDA|TELEFONO)\b",
                     texto, maxsplit=1, flags=re.IGNORECASE)[0]
    texto = re.sub(r"\s+\d{1,2}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{2,4}.*$", "", texto)
    # Códigos delante del nombre (p. ej. "2061398914 20613989146 BLACK CUBE E.I.R.L.")
    texto = re.sub(r"^(?:\d{6,}\s+)+", "", texto)
    texto = re.sub(r"\s+RUC\s*[:.]?\s*\d+.*$", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s{2,}", " ", texto).strip(" -|")
    return texto


def _es_posible_cliente(linea):
    texto = str(linea or "").strip()
    if not texto or len(texto) > 120:
        return False
    plano = sin_tildes(texto)
    if any(p in plano for p in PALABRAS_NO_CLIENTE):
        return False
    if not SUFIJOS_EMPRESA.search(texto):
        return False
    if re.match(r"^\d", texto):
        return False
    return True


def _datos_empresas(texto, lineas):
    """Devuelve (cliente, proveedor, ruc_cliente)."""
    plano = sin_tildes(texto)
    pos_proveedor = plano.find("DATOS DEL PROVEEDOR")

    cliente = ""
    for indice, linea in enumerate(lineas):
        if pos_proveedor >= 0:
            # Solo se busca el cliente antes del bloque del proveedor.
            pos_linea = plano.find(sin_tildes(linea))
            if pos_linea > pos_proveedor and pos_proveedor >= 0:
                continue
        if _es_posible_cliente(linea):
            cliente = _limpiar_empresa(linea)
            break

    # Si el orden del OCR dejó al proveedor antes que al cliente, se busca
    # igualmente una razón social distinta a la del proveedor.
    if not cliente:
        for linea in lineas:
            if "BLACK CUBE" in sin_tildes(linea) or "COLLIE" in sin_tildes(linea):
                continue
            if _es_posible_cliente(linea):
                cliente = _limpiar_empresa(linea)
                break

    proveedor = ""
    bloque = plano[pos_proveedor:] if pos_proveedor >= 0 else plano
    for linea in lineas:
        if sin_tildes(linea) not in bloque and pos_proveedor >= 0:
            continue
        if SUFIJOS_EMPRESA.search(linea) and "BLACK CUBE" in sin_tildes(linea):
            proveedor = _limpiar_empresa(linea)
            break
    if not proveedor:
        for linea in lineas:
            if "BLACK CUBE" in sin_tildes(linea):
                proveedor = _limpiar_empresa(linea)
                break

    # RUC del cliente: primero antes del bloque del proveedor, si se puede.
    ruc_cliente = ""
    for m in re.finditer(r"RUC\s*[:.]?\s*(\d{11})", plano):
        if 0 <= m.start() < pos_proveedor or pos_proveedor < 0:
            ruc_cliente = m.group(1)
            break
    if not ruc_cliente:
        m = re.search(r"RUC\s*[:.]?\s*(\d{11})", plano)
        if m:
            ruc_cliente = m.group(1)

    return cliente, proveedor, ruc_cliente


# =========================================================
# DESCRIPCIÓN / EVENTO
# =========================================================
def _descripcion_valida(texto):
    valor = str(texto or "").strip(" -|:.")
    if not (3 <= len(valor) <= 160):
        return ""
    plano = sin_tildes(valor)
    # Encabezados de sección o de la tabla de artículos.
    if re.match(r"^(DIRECCION|TELEFONO|CELULAR|CORREO|MAIL|RUC|FECHA|MONEDA|"
                r"CONDICION|TERMINOS|OBSERVACION|TOTAL|SUBTOTAL|IGV|IVA|"
                r"CANTIDAD|PRECIO|VALOR|UNIDAD|ITEM|CODIGO)\b", plano):
        return ""
    if "CANTIDAD" in plano and ("PRECIO" in plano or "VALOR" in plano):
        return ""
    # Texto legal que suele acompañar a la observación.
    if any(p in plano for p in ("POR FAVOR", "SIRVASE", "ADJUNTAR", "PRESENTE ORDEN")):
        return ""
    if plano in ("PEN", "USD", "EUR", "S/", "S/."):
        return ""
    letras = len(re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]", valor))
    if letras < 3:
        return ""
    if letras / max(1, len(valor)) < 0.30:
        return ""
    return valor


def _descripcion_desde_texto(lineas):
    """Busca la descripción/evento de la OC."""
    # 1. Etiquetas habituales.
    etiquetas = re.compile(
        r"^(OBSERVACION(?:ES)?|DESCRIPCION(?:ES)?|DETALLE(?:S)?|CONCEPTO(?:S)?|"
        r"EVENTO(?:S)?|MOTIVO(?:S)?|ASUNTO(?:S)?|REFERENCIA(?:S)?|"
        r"PROYECTO(?:S)?|SERVICIO(?:S)?)\s*[:.]\s*(.*)$",
        re.IGNORECASE,
    )
    for indice, linea in enumerate(lineas):
        # Se compara sin tildes ("Descripción" -> "DESCRIPCION") pero el texto
        # se toma de la línea original para no perder las tildes.
        m = etiquetas.match(sin_tildes(linea))
        if not m:
            continue
        try:
            resto = linea[m.start(2):m.end(2)]
        except Exception:
            resto = m.group(2)
        # Texto en la misma línea y, si es genérico, las 3 líneas siguientes.
        candidatos = [resto] + lineas[indice + 1: indice + 4]
        for candidato in candidatos:
            valor = _descripcion_valida(candidato)
            if valor:
                return valor

    # 2. NGR: la descripción real suele estar justo antes de "Dirección de entrega".
    for indice, linea in enumerate(lineas):
        if "DIRECCION DE ENTREGA" in sin_tildes(linea):
            for anterior in reversed(lineas[max(0, indice - 3): indice]):
                valor = _descripcion_valida(anterior)
                if valor:
                    return valor

    # 3. Una línea con "//" o "|" que parezca detalle.
    for linea in lineas:
        if "//" in linea:
            valor = _descripcion_valida(linea)
            if valor:
                return valor
    return ""


# =========================================================
# TOTALES
# =========================================================
def _buscar_importe(lineas, patrones, excluir=()):
    for linea in reversed(lineas):
        plano = sin_tildes(linea)
        if any(e in plano for e in excluir):
            continue
        if any(re.search(patron, plano, re.IGNORECASE) for patron in patrones):
            valor = _importe_de_linea(linea)
            if valor > 0:
                return valor
    return 0.0


def _triplete_totales(texto, igv_pct):
    """Respaldo para OCR: busca tres importes consecutivos que cuadren.

    Muchos motores OCR separan las columnas: dejan 'SUB-TOTAL', 'IGV' y
    'TOTAL' en un lado y los importes en otro. En ese caso se buscan los
    últimos tres números que cumplan  subtotal + igv = total  y
    igv ~= subtotal * %IGV.
    """
    numeros = []
    for m in re.finditer(r"\d+(?:[.,]\d+)*", str(texto or "")):
        valor = numero_a_float(m.group(0))
        if valor > 0:
            numeros.append(valor)
    numeros = numeros[-160:]
    for indice in range(len(numeros) - 3, max(-1, len(numeros) - 80), -1):
        sub, igv, total = numeros[indice], numeros[indice + 1], numeros[indice + 2]
        if sub < 50:
            continue
        if abs((sub + igv) - total) > max(0.05, total * 0.002):
            continue
        esperado = sub * float(igv_pct or 18.0) / 100.0
        if abs(igv - esperado) > max(0.50, esperado * 0.02):
            continue
        return _redondear(sub), _redondear(igv), _redondear(total)
    return 0.0, 0.0, 0.0


def _moneda_desde_texto(texto):
    plano = sin_tildes(texto)
    m = re.search(r"MONEDA\s*[:.]?\s*([A-Z]{3})", plano)
    if m:
        return m.group(1)
    for codigo in ("PEN", "USD", "EUR"):
        if re.search(r"\b" + codigo + r"\b", plano):
            return codigo
    if re.search(r"S/", texto):
        return "PEN"
    if "$" in texto:
        return "USD"
    return ""


# =========================================================
# ARTÍCULOS (solo para contar y validar totales)
# =========================================================
PATRON_ITEM = re.compile(
    r"^\s*(\d{2,6}/\d{1,6})\s+(\S+)\s+(.+?)\s+([\d.,]+)\s+([A-Za-z]{2,4})\s+"
    r"(.+?)\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s+([\d.,]+)\s+([\d.,]+)\s*$"
)


def _items_desde_texto(texto):
    items = []
    for linea in _lineas(texto):
        m = PATRON_ITEM.match(linea)
        if not m:
            continue
        items.append({
            "item": m.group(1),
            "codigo": m.group(2),
            "descripcion": m.group(3).strip(),
            "cantidad": numero_a_float(m.group(4)),
            "um": m.group(5),
            "entrega": m.group(6).strip(),
            "fecha_entrega": m.group(7),
            "precio_unitario": numero_a_float(m.group(8)),
            "valor_neto": numero_a_float(m.group(9)),
        })
    return items


def _items_desde_tablas(tablas):
    items = []
    for tabla in tablas or []:
        for fila in tabla or []:
            celdas = [str(c).strip() for c in (fila or []) if c is not None and str(c).strip()]
            if len(celdas) < 6 or not re.match(r"^\d{2,6}/\d+", celdas[0]):
                continue
            # Las celdas combinadas de pdfplumber dejan solo los valores reales:
            # item, código, descripción, cantidad, UM, entrega, fecha, precio, valor.
            item = celdas[0]
            codigo = celdas[1] if len(celdas) > 1 else ""
            descripcion = celdas[2] if len(celdas) > 2 else ""
            cantidad = numero_a_float(celdas[3]) if len(celdas) > 3 else 0.0
            um = celdas[4] if len(celdas) > 4 else ""
            entrega = celdas[5] if len(celdas) > 5 else ""
            fecha_entrega = ""
            precio = 0.0
            valor = 0.0
            for celda in reversed(celdas[6:]):
                if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", celda):
                    fecha_entrega = celda
                    break
            importes = [numero_a_float(c) for c in celdas[6:] if numero_a_float(c) > 0]
            if len(importes) >= 2:
                precio, valor = importes[-2], importes[-1]
            elif importes:
                valor = importes[-1]
            items.append({
                "item": item, "codigo": codigo, "descripcion": descripcion,
                "cantidad": cantidad, "um": um, "entrega": entrega,
                "fecha_entrega": fecha_entrega,
                "precio_unitario": precio, "valor_neto": valor,
            })
    return items


def _unir_items(listas):
    vistos = set()
    salida = []
    for lista in listas:
        for item in lista:
            clave = (str(item.get("item")), str(item.get("codigo")), round(float(item.get("valor_neto") or 0.0), 2))
            if clave in vistos:
                continue
            vistos.add(clave)
            salida.append(item)
    return salida


# =========================================================
# PARSEO PRINCIPAL
# =========================================================
def parsear_orden_compra(texto, tablas=None, igv_pct=18.0):
    """Convierte el texto (digital u OCR) de una OC en un diccionario."""
    texto = str(texto or "")
    lineas = _lineas(texto)
    cliente, proveedor, ruc_cliente = _datos_empresas(texto, lineas)

    subtotal = _buscar_importe(
        lineas,
        (r"\bSUB[\s\-]*TOTAL\b", r"\bSUBTOTAL\b", r"\bBASE\s+IMPONIBLE\b",
         r"\bOP\.?\s*GRAVADAS?\b", r"\bVALOR\s+DE\s+VENTA\b"),
    )
    igv = _buscar_importe(
        lineas,
        (r"\bI\.?\s*G\.?\s*V\.?\b", r"\bIVA\b", r"\bIMPUESTO\b"),
        excluir=("SUB",),
    )
    total = _buscar_importe(
        lineas,
        (r"\bTOTAL\b", r"\bIMPORTE\s+TOTAL\b", r"\bMONTO\s+TOTAL\b"),
        excluir=("SUB", "IGV", "IVA", "IMPUESTO", "CANTIDAD", "ITEMS", "ARTICULOS", "UNIDADES"),
    )

    items = _unir_items([_items_desde_tablas(tablas), _items_desde_texto(texto)])
    suma_items = _redondear(sum(float(i.get("valor_neto") or 0.0) for i in items))

    # Respaldo para OCR: los importes pueden quedar lejos de sus etiquetas.
    # Solo se usa si falta el subtotal o el total; si ambos existen, el IGV se
    # obtiene por resta (evita confundirlo con precios de los artículos).
    if total <= 0 or subtotal <= 0:
        t_sub, t_igv, t_total = _triplete_totales(texto, igv_pct)
        if t_total > 0:
            subtotal = subtotal or t_sub
            igv = igv or t_igv
            total = total or t_total

    # Respaldo: sumar las líneas de artículos cuando no hay totales legibles.
    if subtotal <= 0 and total <= 0 and len(items) >= 3 and suma_items > 0:
        subtotal = suma_items

    # Conciliación matemática.
    if total > 0 and subtotal <= 0:
        subtotal = _redondear(total / (1.0 + float(igv_pct or 18.0) / 100.0))
        igv = _redondear(total - subtotal)
    elif subtotal > 0 and total <= 0:
        igv = _redondear(subtotal * float(igv_pct or 18.0) / 100.0)
        total = _redondear(subtotal + igv)
    elif subtotal > 0 and total > 0 and igv <= 0:
        igv = _redondear(total - subtotal)

    # Si el "IGV" leído es en realidad el porcentaje (18.00), se corrige.
    if 0 < igv <= 30 and total > subtotal > 0:
        igv = _redondear(total - subtotal)

    # Validación final: no aceptar totales incoherentes.
    if subtotal > 0 and total > 0 and abs((subtotal + igv) - total) > max(0.10, total * 0.005):
        if abs(total - (subtotal + igv)) > abs(total - (subtotal + _redondear(subtotal * float(igv_pct or 18.0) / 100.0))):
            igv = _redondear(total - subtotal)
        else:
            total = _redondear(subtotal + igv)

    return {
        "numero_oc": _numero_oc_desde_texto(texto),
        "fecha": _fecha_desde_texto(texto),
        "cliente": cliente,
        "ruc_cliente": ruc_cliente,
        "proveedor": proveedor,
        "descripcion": _descripcion_desde_texto(lineas),
        "moneda": _moneda_desde_texto(texto),
        "subtotal": _redondear(subtotal),
        "igv": _redondear(igv),
        "total": _redondear(total),
        "items": items,
        "items_count": len(items),
        "suma_items": suma_items,
    }


# =========================================================
# LECTURA DEL ARCHIVO (PDF DIGITAL U OCR)
# =========================================================
def _texto_pdf_digital(ruta):
    """Devuelve (texto, tablas, paginas, error)."""
    if pdfplumber is None:
        return "", [], 0, "Falta la librería pdfplumber."
    try:
        partes, tablas = [], []
        with pdfplumber.open(ruta) as pdf:
            paginas = len(pdf.pages)
            for page in pdf.pages:
                try:
                    texto = page.extract_text() or ""
                except Exception:
                    texto = ""
                if texto.strip():
                    partes.append(texto)
                try:
                    for tabla in page.extract_tables() or []:
                        if tabla:
                            tablas.append(tabla)
                except Exception:
                    pass
        return "\n".join(partes), tablas, paginas, ""
    except Exception as e:
        return "", [], 0, str(e)


def _texto_por_ocr(ruta, idioma="Automático"):
    """Devuelve (texto, motor, paginas, error) usando el OCR del sistema."""
    try:
        from escaner import ocr_paginas, texto_de_resultados
    except Exception as e:
        return "", "", 0, f"No se pudo cargar el motor OCR: {e}"
    try:
        resultado = ocr_paginas([ruta], idioma)
    except Exception as e:
        return "", "", 0, str(e)
    texto = texto_de_resultados(resultado or {})
    motor = str((resultado or {}).get("motor") or "")
    paginas = len((resultado or {}).get("resultados") or [])
    if not str(texto).strip():
        return "", motor, paginas, str((resultado or {}).get("error") or "El OCR no reconoció texto.")
    return texto, motor, paginas, ""


def _puntaje_datos(datos):
    campos = ("numero_oc", "fecha", "cliente", "descripcion")
    puntos = sum(1 for c in campos if str((datos or {}).get(c) or "").strip())
    if (datos or {}).get("total", 0) > 0:
        puntos += 2
    if (datos or {}).get("subtotal", 0) > 0:
        puntos += 1
    return puntos


def leer_orden_compra(ruta, idioma="Automático", igv_pct=18.0, usar_ocr=True):
    """Lee una OC en PDF/imagen y devuelve {'ok', 'motor', 'paginas', 'texto', 'datos', 'error'}."""
    ruta = os.path.abspath(os.path.expanduser(str(ruta or "").strip().strip('"')))
    if not ruta or not os.path.exists(ruta):
        return {"ok": False, "motor": "", "paginas": 0, "texto": "", "datos": {},
                "error": "El archivo no existe."}

    extension = os.path.splitext(ruta)[1].lower()
    es_pdf = extension == ".pdf"

    texto_digital, tablas, paginas, error_pdf = ("", [], 0, "")
    if es_pdf:
        texto_digital, tablas, paginas, error_pdf = _texto_pdf_digital(ruta)

    datos = {}
    motor = ""
    texto = texto_digital

    # Un PDF escaneado puede no tener texto o traer solo una capa mínima.
    plano_digital = sin_tildes(texto_digital)
    tiene_texto_util = (
        len(texto_digital.strip()) >= 80
        and any(p in plano_digital for p in ("ORDEN", "PEDIDO", "COMPRA", "TOTAL", "RUC", "SERVICIO"))
    )

    if tiene_texto_util:
        datos = parsear_orden_compra(texto_digital, tablas, igv_pct)
        motor = "PDF digital"

    # También se intenta OCR si el PDF digital no dio un total (documentos
    # híbridos: texto editable con los importes como imagen).
    necesita_ocr = (
        not tiene_texto_util
        or _puntaje_datos(datos) < 3
        or float(datos.get("total") or 0) <= 0
    )
    if usar_ocr and necesita_ocr:
        texto_ocr, motor_ocr, paginas_ocr, error_ocr = _texto_por_ocr(ruta, idioma)
        if texto_ocr.strip():
            datos_ocr = parsear_orden_compra(texto_ocr, [], igv_pct)
            if _puntaje_datos(datos_ocr) > _puntaje_datos(datos):
                datos, texto, motor = datos_ocr, texto_ocr, motor_ocr or "OCR"
                if paginas_ocr:
                    paginas = paginas_ocr
        elif not datos:
            return {"ok": False, "motor": motor_ocr, "paginas": paginas_ocr, "texto": "",
                    "datos": {}, "error": error_ocr or error_pdf or "No se pudo leer el documento."}

    if not datos:
        motivo = error_pdf or "No se encontró texto en el documento."
        return {"ok": False, "motor": motor, "paginas": paginas, "texto": texto,
                "datos": {}, "error": motivo}

    datos.setdefault("moneda", "")
    relevante = any([
        datos.get("numero_oc"), datos.get("fecha"), datos.get("cliente"),
        datos.get("descripcion"), datos.get("total"),
    ])
    if not relevante:
        return {"ok": False, "motor": motor, "paginas": paginas, "texto": texto, "datos": datos,
                "error": "Se leyó el documento, pero no se reconocieron los datos de la OC. "
                         "Puede completarlos a mano."}

    return {"ok": True, "motor": motor, "paginas": paginas, "texto": texto,
            "datos": datos, "error": ""}
