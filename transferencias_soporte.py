# -*- coding: utf-8 -*-
"""Soporte de transferencias de Eventos: sin conexiones ni efectos al importar.

La comisión vive en la misma fila que la transferencia y se proyecta como un
movimiento separado. Nunca se inserta un gasto espejo adicional en Compras.
"""
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from queue import Queue, Empty
import threading

TABLA_COMISION = "tx_comision"  # identidad lógica, NO una tabla SQL
TABLAS_TX = ("transferencias_bancarias", TABLA_COMISION)
COLUMNAS_TX = (
    "comision NUMERIC DEFAULT 0",
    "referencia VARCHAR(120) DEFAULT ''",
    "archivo_ruta TEXT DEFAULT ''",
)


def asegurar_esquema(conn):
    """Verifica la BD conectada, no confía en marcas locales ni import/reload.

    Usar antes de cualquier escritura en esta conexión. No modifica movimientos.
    Sólo migra ante columnas ausentes; permisos/red/otros errores se propagan.
    """
    with conn.cursor() as c:
        try:
            c.execute("SELECT comision, referencia, archivo_ruta FROM transferencias_bancarias LIMIT 0")
        except Exception as exc:
            conn.rollback()
            if getattr(exc, "pgcode", None) not in ("42703", "42P01"):
                raise
            c.execute("""CREATE TABLE IF NOT EXISTS transferencias_bancarias (
                id SERIAL PRIMARY KEY, banco_origen VARCHAR(255), cuenta_origen VARCHAR(255),
                banco_destino VARCHAR(255), cuenta_destino VARCHAR(255), fecha VARCHAR(20),
                descripcion TEXT, monto NUMERIC)""")
            for columna in COLUMNAS_TX:
                c.execute("ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS " + columna)
            c.execute("SELECT comision, referencia, archivo_ruta FROM transferencias_bancarias LIMIT 0")
            conn.commit()


def limpiar_marcas(cursor, ids):
    """Invalida sólo marcas de estas TX, incluidas las de su comisión."""
    origenes = [prefijo + tabla for prefijo in ("s:", "sistema:") for tabla in TABLAS_TX]
    cursor.execute("""DELETE FROM conciliacion_bancaria
        WHERE id_movimiento = ANY(%s) AND
        (origen = ANY(%s) OR (origen = 'sistema' AND tabla = ANY(%s)))""",
        (list(ids), origenes, list(TABLAS_TX)))
    cursor.execute("""DELETE FROM conciliacion_ignorados
        WHERE id_movimiento = ANY(%s) AND tabla = ANY(%s)""", (list(ids), list(TABLAS_TX)))


def importe(texto, positivo=False):
    """Valida importes monetarios sin aceptar negativos, NaN ni texto arbitrario."""
    s = str(texto or "").strip()
    s = re.sub(r"^(?:S/\.?|PEN)\s*", "", s, flags=re.I)
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", s):
        raise ValueError("Ingrese un importe numérico válido.")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    try:
        valor = Decimal(s)
    except InvalidOperation:
        raise ValueError("Importe inválido.") from None
    if not valor.is_finite() or valor < 0 or (positivo and valor <= 0):
        raise ValueError("El monto debe ser mayor a cero; la comisión no puede ser negativa.")
    if valor != valor.quantize(Decimal("0.01")):
        raise ValueError("Use como máximo dos decimales.")
    return valor.quantize(Decimal("0.01"))


def referencia_valida(texto):
    """Respeta también las instalaciones ya migradas con VARCHAR(120)."""
    referencia = str(texto or "").strip()
    if len(referencia) > 120:
        raise ValueError("La referencia admite como máximo 120 caracteres.")
    return referencia


def fecha_valida(texto):
    if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", str(texto or "")):
        raise ValueError("Ingrese la fecha en formato DD/MM/YYYY.")
    try:
        return datetime.strptime(texto, "%d/%m/%Y").strftime("%d/%m/%Y")
    except ValueError:
        raise ValueError("La fecha no existe. Use DD/MM/YYYY.") from None


def datos_comprobante(texto, leer_monto):
    """Extrae sólo pistas explícitas; nunca toma cuenta/operación como monto."""
    texto = str(texto or "")
    fecha = ""
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", texto):
        try:
            fecha = datetime(int(m[3]), int(m[2]), int(m[1])).strftime("%d/%m/%Y")
            break
        except ValueError:
            continue
    if not fecha:
        meses = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
                 "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
        m = re.search(r"\b(\d{1,2})\s*(?:de\s+)?([a-záé]+)\.?\s*(?:de\s+)?(20\d{2})\b", texto, re.I)
        if m and m[2].lower()[:3] in meses:
            try:
                fecha = datetime(int(m[3]), meses[m[2].lower()[:3]], int(m[1])).strftime("%d/%m/%Y")
            except ValueError:
                pass
    referencia = ""
    patron = r"(?:referencia|(?:n[úu]mero|nro\.?|n[°ºo.])\s*(?:de\s*)?(?:operaci[oó]n|transferencia)|c[oó]digo\s*(?:de\s*)?operaci[oó]n|operaci[oó]n)\s*[:#.-]?\s*([A-Z0-9][A-Z0-9-]*\d[A-Z0-9-]*)"
    m = re.search(patron, texto, re.I)
    if m:
        referencia = m[1]
    if not referencia:
        # Respaldo: códigos de operación sin etiqueta (ej. RIMA00184Z10 de BCP)
        REFERENCIA_GENERICA = (r"\b(?:RIMA|OPE|OP|REF|CONST|NRO)[A-Z0-9]{6,}\b", r"\b\d{8,}\b")
        for linea in (l.strip() for l in texto.splitlines()):
            for expresion in REFERENCIA_GENERICA:
                hallado = re.search(expresion, linea.upper())
                if hallado:
                    referencia = hallado.group(0)
                    break
            if referencia:
                break
    # Mantiene etiquetas y montos en líneas separadas como devuelve OCR nativo.
    lineas = [l.strip() for l in texto.splitlines() if l.strip()]
    monetarias = []
    for i, linea in enumerate(lineas):
        if re.search(r"comis|total|importe|monto|s[/.]", linea, re.I):
            if not re.search(r"\d", linea) and i + 1 < len(lineas):
                linea += " " + lineas[i + 1]
            monetarias.append(linea)
    principales = [l for l in monetarias if not re.search(r"comis", l, re.I)]
    # Una comisión aislada no permite inferir el principal transferido.
    monto = leer_monto("\n".join(monetarias)) if principales else 0
    return {"fecha": fecha, "referencia": referencia, "monto": monto}


def preparar_imagen_ocr(ruta, maximo=2600):
    """Recorta el contenido y mejora el contraste de un comprobante pequeño."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return ""
    try:
        with Image.open(ruta) as original:
            gris = original.convert("L")
            ancho, alto = gris.size
            borde = max(8, min(20, ancho // 120))
            pixeles = gris.load()
            umbral = 238
            filas_pro = [(y, sum(1 for x in range(borde, ancho, 4) if pixeles[x, y] < umbral))
                         for y in range(0, alto, max(1, alto // 1200))]
            columnas_pro = [(x, sum(1 for y in range(0, alto, 4) if pixeles[x, y] < umbral))
                            for x in range(borde, ancho, max(1, ancho // 1200))]

            def limites(valores, minimo=2):
                elegidos = [i for i, v in valores if v >= minimo]
                return (elegidos[0], elegidos[-1] + 1) if elegidos else None

            caja_y = limites(filas_pro)
            caja_x = limites(columnas_pro)
            if not caja_x or not caja_y:
                return ""
            margen = 30
            recorte = gris.crop((max(0, caja_x[0] - margen), max(0, caja_y[0] - margen),
                                 min(ancho, caja_x[1] + margen), min(alto, caja_y[1] + margen)))
            if recorte.width < 60 or recorte.height < 30:
                return ""
            factor = 1
            while recorte.width * factor < maximo and factor < 4:
                factor += 1
            if factor > 1:
                recorte = recorte.resize((recorte.width * factor, recorte.height * factor), Image.LANCZOS)
            recorte = ImageOps.autocontrast(recorte, cutoff=1)
            salida = os.path.join(tempfile.gettempdir(), "ocr_tx_" + uuid.uuid4().hex + ".png")
            recorte.save(salida)
            return salida
    except Exception:
        return ""


def normalizar_montos_ocr(texto):
    """Arregla importes que el OCR dejó sin decimales: "S/ 17700" -> "S/ 177.00"."""
    def arreglar(m):
        prefijo, numero = m.group(1), m.group(2)
        if len(numero) >= 4:
            return prefijo + numero[:-2] + "." + numero[-2:]
        return m.group(0)
    try:
        return re.sub(r"(?i)(s[/.]\s*)(\d{4,})(?![\d.,])", arreglar, str(texto or ""))
    except Exception:
        return texto

def area_contenido_ocr(ruta, margen=25):
    """(x, y, ancho, alto) en píxeles donde hay tinta en la imagen (o None)."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(ruta) as original:
            gris = original.convert("L")
            ancho, alto = gris.size
            borde = max(8, min(20, ancho // 120))
            pixeles = gris.load()
            umbral = 238
            filas = [(y, sum(1 for x in range(borde, ancho, 4) if pixeles[x, y] < umbral))
                     for y in range(0, alto, max(1, alto // 1200))]
            columnas = [(x, sum(1 for y in range(0, alto, 4) if pixeles[x, y] < umbral))
                        for x in range(borde, ancho, max(1, ancho // 1200))]

            def limites(valores, minimo=2):
                elegidos = [i for i, v in valores if v >= minimo]
                return (elegidos[0], elegidos[-1] + 1) if elegidos else None

            caja_y = limites(filas)
            caja_x = limites(columnas)
            if not caja_x or not caja_y:
                return None
            x0 = max(0, caja_x[0] - margen)
            y0 = max(0, caja_y[0] - margen)
            x1 = min(ancho, caja_x[1] + margen)
            y1 = min(alto, caja_y[1] + margen)
            if x1 - x0 < 50 or y1 - y0 < 30:
                return None
            return (x0, y0, x1 - x0, y1 - y0)
    except Exception:
        return None

def imagen_ampliada(ruta, factor=2):
    """Copia ampliada y con más contraste (mejora el OCR sin volver a escanear)."""
    try:
        from PIL import Image, ImageOps
    except Exception:
        return ""
    try:
        with Image.open(ruta) as original:
            gris = original.convert("L")
            grande = gris.resize((gris.width * factor, gris.height * factor), Image.LANCZOS)
            grande = ImageOps.autocontrast(grande, cutoff=1)
            salida = os.path.join(tempfile.gettempdir(), "ocr_amp_" + uuid.uuid4().hex + ".png")
            grande.save(salida)
            return salida
    except Exception:
        return ""

def copiar_comprobante(ruta, base):
    """Copia inmutable con UUID, portátil por nombre. No borra originales."""
    if not ruta:
        return ""
    if not base or not str(base).strip():
        raise ValueError("Configure una carpeta de archivos autorizada antes de adjuntar el comprobante.")
    if not os.path.isfile(ruta):
        raise ValueError("El comprobante seleccionado no existe en este equipo.")
    carpeta = os.path.abspath(os.path.join(base, "comprobantes_transferencias"))
    os.makedirs(carpeta, exist_ok=True)
    if os.path.normcase(os.path.dirname(os.path.abspath(ruta))) == os.path.normcase(carpeta):
        return os.path.normpath(ruta)
    extension = os.path.splitext(ruta)[1].lower()
    if extension not in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"):
        raise ValueError("Seleccione un PDF o una imagen compatible.")
    destino = os.path.join(carpeta, "Transferencia_" + uuid.uuid4().hex + extension)
    shutil.copy2(ruta, destino)
    return os.path.normpath(destino)


def leer_comprobante(ruta, extraer_pdf, mod_escaner, fitz_mod=None):
    """Reutiliza OCR existente; PDF sin texto se rasteriza en temporales aislados."""
    if not ruta.lower().endswith(".pdf"):
        resultado = mod_escaner.ocr_paginas([os.path.abspath(ruta)])
        return mod_escaner.texto_de_resultados(resultado), resultado.get("error", "")
    texto = extraer_pdf(ruta)
    if texto.strip():
        return texto, ""
    if fitz_mod is None:
        return "", "No está disponible PyMuPDF para leer el PDF escaneado. Complete los datos manualmente."
    with tempfile.TemporaryDirectory(prefix="tx_ocr_") as carpeta:
        with fitz_mod.open(ruta) as doc:
            if doc.needs_pass:
                return "", "El PDF está protegido; adjunte una copia sin contraseña o complete los datos manualmente."
            partes = []
            for i, pagina in enumerate(doc):
                imagen = os.path.join(carpeta, str(i) + ".png")
                pagina.get_pixmap(matrix=fitz_mod.Matrix(2, 2)).save(imagen)
                resultado = mod_escaner.ocr_paginas([imagen])
                partes.append(mod_escaner.texto_de_resultados(resultado))
            return "\n".join(partes), ""


def ejecutar_sin_tk(parent, trabajo, terminar):
    """Worker sin llamadas Tk, ni siquiera after; la cola se consulta desde Tk."""
    cola = Queue()

    def worker():
        try:
            cola.put((trabajo(), None))
        except Exception as exc:
            cola.put((None, str(exc)))

    def consultar():
        if not parent.winfo_exists():
            return
        try:
            resultado, error = cola.get_nowait()
        except Empty:
            parent.after(80, consultar)
            return
        terminar(resultado, error)

    threading.Thread(target=worker, daemon=True).start()
    parent.after(80, consultar)
