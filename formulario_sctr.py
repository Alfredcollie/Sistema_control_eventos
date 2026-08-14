# -*- coding: utf-8 -*-
"""
FORMULARIO_SCTR.PY (v3 - COMPLETO Y CORREGIDO)
Genera el formulario PDF RELLENABLE (campos AcroForm) para que el
proveedor complete personal (nombres + DNI), SCTR y firma desde la
PC o el celular (Adobe Acrobat / Xodo).
- Sin texto quemado de empresa (usa tu Razón Social de Configuración).
- Logo a ancho de página respetando márgenes.
- "Señores proveedores:" y firma separada (no se monta sobre el nombre).
"""
import os
import sys
import json
from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

try:
    from app_paths import CONFIG_FILE
    RUTA_CONFIG = str(CONFIG_FILE)
except Exception:
    RUTA_CONFIG = "config_local.json"


def cargar_config():
    config = {}
    try:
        if os.path.exists(RUTA_CONFIG):
            with open(RUTA_CONFIG, "r", encoding="utf-8") as f:
                config = json.load(f)
    except Exception:
        pass
    return config


def copiar_archivo_portapapeles(ruta):
    try:
        ruta_absoluta = os.path.abspath(ruta)
        if sys.platform == "darwin":
            os.system("osascript -e 'set the clipboard to POSIX file \"%s\"'" % ruta_absoluta)
        elif sys.platform == "win32":
            os.system("powershell -command \"Set-Clipboard -Path '%s'\"" % ruta_absoluta)
    except Exception as e:
        print("Error copiando al portapapeles:", e)


def generar_formulario_sctr(codigo_cot, evento_nombre, fecha_evento, locacion, proveedor):
    config = cargar_config()
    razon_social = str(config.get("razon_social_empresa", "")).strip()
    ruc_emp = str(config.get("ruc_empresa", "")).strip()

    carpeta = "formularios_proveedores"
    ruta_base = str(config.get("ruta_drive", "")).strip()
    if ruta_base and os.path.exists(ruta_base):
        carpeta = os.path.join(ruta_base, "formularios_proveedores")
    if not os.path.exists(carpeta):
        try:
            os.makedirs(carpeta)
        except Exception:
            pass
    nombre_pdf = os.path.join(carpeta, f"Formulario_SCTR_{proveedor.replace(' ', '_')}_{codigo_cot}.pdf")

    W, H = letter
    M = 40.0
    AW = W - 2 * M

    c = canvas.Canvas(nombre_pdf, pagesize=letter)
    c.setTitle(f"Formulario SCTR - {proveedor}")

    # --------------------------------------------------
    # LOGO A ANCHO DE PÁGINA (RESPETANDO MÁRGENES)
    # --------------------------------------------------
    y = H - M
    ruta_logo = str(config.get("ruta_logo_cotizacion", "")).strip()
    if ruta_logo and os.path.exists(ruta_logo):
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(ruta_logo)
            iw, ih = img.getSize()
            iw = iw or 1
            ih = ih or 1
            max_w = AW
            max_h = 120.0
            ratio = min(max_w / float(iw), max_h / float(ih))
            final_w = iw * ratio
            final_h = ih * ratio
            x_logo = M + (AW - final_w) / 2.0
            y_logo = y - final_h
            c.drawImage(ruta_logo, x_logo, y_logo, width=final_w, height=final_h, preserveAspectRatio=True)
            y = y_logo - 14.0
        except Exception:
            y -= 20
    else:
        y -= 20

    # --------------------------------------------------
    # ENCABEZADO (SIN TEXTO QUEMADO)
    # --------------------------------------------------
    c.setFont("Helvetica-Bold", 13)
    c.setFillColorRGB(0.12, 0.32, 0.55)
    c.drawCentredString(W / 2, y, "FORMATO DE PRESENTACIÓN DE PERSONAL Y SCTR")
    y -= 14
    if razon_social:
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0.2, 0.2, 0.2)
        linea = razon_social + (f"  |  RUC: {ruc_emp}" 
    if ruc_emp else "")
        c.drawCentredString(W / 2, y, linea)
        y -= 14
    y -= 6

    # --------------------------------------------------
    # DATOS DEL EVENTO
    # --------------------------------------------------
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(M, y, f"Evento: {evento_nombre or ''}")
    c.drawString(M + 300, y, f"Fecha del evento: {fecha_evento or ''}")
    y -= 12
    c.drawString(M, y, f"Locación: {locacion or ''}")
    c.drawString(M + 300, y, f"Emisión: {datetime.now().strftime('%d/%m/%Y')}")
    y -= 12
    c.drawString(M, y, f"Proveedor: {proveedor or ''}")
    c.drawString(M + 300, y, f"Cotización ref.: {codigo_cot or ''}")
    y -= 16

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.25, 0.25, 0.25)
    c.drawString(M, y, "Señores proveedores: complete los campos marcados (puede hacerlo desde su PC o celular con Adobe")
    y -= 10
    c.drawString(M, y, "Acrobat / Xodo). Devuelva este formulario firmado junto con el SCTR vigente de cada trabajador.")
    y -= 14

    # --------------------------------------------------
    # TABLA DE PERSONAL RELLENABLE (12 FILAS)
    # --------------------------------------------------
    col_n = 24
    col_nom = 268
    col_dni = 120
    col_car = AW - col_n - col_nom - col_dni
    row_h = 16.0

    c.setFillColorRGB(0.12, 0.32, 0.55)
    c.rect(M, y - 16, AW, 16, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(M + 6, y - 11, "N°")
    c.drawString(M + col_n + 6, y - 11, "NOMBRES Y APELLIDOS COMPLETOS")
    c.drawString(M + col_n + col_nom + 6, y - 11, "DNI / C. EXTRANJERÍA")
    c.drawString(M + col_n + col_nom + col_dni + 6, y - 11, "CARGO / FUNCIÓN")
    y -= 16

    acro = c.acroForm
    for i in range(1, 13):
        y_fila = y - row_h
        c.setStrokeColorRGB(0.75, 0.75, 0.75)
        c.setLineWidth(0.5)
        c.rect(M, y_fila, AW, row_h, fill=0, stroke=1)
        c.line(M + col_n, y_fila, M + col_n, y_fila + row_h)
        c.line(M + col_n + col_nom, y_fila, M + col_n + col_nom, y_fila + row_h)
        c.line(M + col_n + col_nom + col_dni, y_fila, M + col_n + col_nom + col_dni, y_fila + row_h)
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.drawCentredString(M + col_n / 2, y_fila + 5, str(i))
        acro.textfield(name=f"p{i}_nombre", x=M + col_n + 2, y=y_fila + 2, width=col_nom - 4, height=row_h - 4, fontSize=8, forceBorder=False)
        acro.textfield(name=f"p{i}_dni", x=M + col_n + col_nom + 2, y=y_fila + 2, width=col_dni - 4, height=row_h - 4, fontSize=8, forceBorder=False)
        acro.textfield(name=f"p{i}_cargo", x=M + col_n + col_nom + col_dni + 2, y=y_fila + 2, width=col_car - 4, height=row_h - 4, fontSize=8, forceBorder=False)
        y = y_fila

    y -= 14

    # --------------------------------------------------
    # DATOS DEL SCTR
    # --------------------------------------------------
    c.setFont("Helvetica-Bold", 9.5)
    c.setFillColorRGB(0.12, 0.32, 0.55)
    c.drawString(M, y, "DATOS DEL SEGURO SCTR (adjuntar póliza / certificados vigentes):")
    y -= 14
    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(M, y + 3, "Compañía de seguros:")
    acro.textfield(name="sctr_compania", x=M + 95, y=y, width=240, height=14, fontSize=8, forceBorder=False)
    c.drawString(M + 350, y + 3, "N° de póliza:")
    acro.textfield(name="sctr_poliza", x=M + 405, y=y, width=127, height=14, fontSize=8, forceBorder=False)
    y -= 18
    c.drawString(M, y + 3, "Vigencia desde:")
    acro.textfield(name="sctr_desde", x=M + 70, y=y, width=110, height=14, fontSize=8, forceBorder=False)
    c.drawString(M + 200, y + 3, "hasta:")
    acro.textfield(name="sctr_hasta", x=M + 228, y=y, width=110, height=14, fontSize=8, forceBorder=False)
    y -= 20

    # --------------------------------------------------
    # REPRESENTANTE
    # --------------------------------------------------
    c.drawString(M, y + 3, "Nombre del representante:")
    acro.textfield(name="rep_nombre", x=M + 115, y=y, width=220, height=14, fontSize=8, forceBorder=False)
    c.drawString(M + 350, y + 3, "DNI:")
    acro.textfield(name="rep_dni", x=M + 375, y=y, width=100, height=14, fontSize=8, forceBorder=False)
    y -= 26

    # --------------------------------------------------
    # DECLARACIÓN + FIRMA (SEPARADA, NO SE MONTA)
    # --------------------------------------------------
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColorRGB(0.25, 0.25, 0.25)
    c.drawString(M, y, "Declaro que el personal detallado se encuentra habilitado y capacitado para los trabajos a realizar y que la")
    y -= 10
    c.drawString(M, y, "información consignada es verídica.")
    y -= 26

    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(M, y + 3, "Firma:")
    acro.textfield(name="rep_firma", x=M + 30, y=y - 8, width=250, height=30, fontSize=9, forceBorder=True)
    c.drawString(M + 310, y + 3, "Fecha:")
    acro.textfield(name="rep_fecha", x=M + 340, y=y - 8, width=110, height=30, fontSize=9, forceBorder=True)

    c.save()
    return nombre_pdf


if __name__ == "__main__":
    pass