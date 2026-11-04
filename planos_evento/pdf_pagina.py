# -*- coding: utf-8 -*-
"""Lectura del PDF con PyMuPDF: páginas, tipo de contenido y render."""
import os

try:
    import fitz                      # PyMuPDF
except Exception:                    # pragma: no cover
    fitz = None


def disponible():
    return fitz is not None


def abrir(ruta):
    if not disponible():
        raise RuntimeError("Falta PyMuPDF (pip install PyMuPDF).")
    if not ruta or not os.path.isfile(ruta):
        raise FileNotFoundError("No se encontró el PDF: %s" % ruta)
    return fitz.open(ruta)


def info_paginas(ruta):
    """Lista de páginas: número, tamaño en puntos y si tiene texto/vector.

    Devuelve [{"numero": 1, "ancho": 842.0, "alto": 595.0, "rotacion": 0,
               "vectorial": True, "texto": "..."}]
    """
    documento = abrir(ruta)
    paginas = []
    try:
        for indice in range(documento.page_count):
            pagina = documento[indice]
            rect = pagina.rect
            try:
                texto = (pagina.get_text("text") or "").strip()
            except Exception:
                texto = ""
            vectorial = False
            try:
                dibujos = pagina.get_drawings() or []
                vectorial = bool(dibujos) or bool(texto)
            except Exception:
                vectorial = bool(texto)
            paginas.append({"numero": indice + 1,
                            "ancho": float(rect.width), "alto": float(rect.height),
                            "rotacion": int(pagina.rotation or 0),
                            "vectorial": vectorial,
                            "texto": texto[:400]})
    finally:
        documento.close()
    return paginas


def renderizar_pagina(ruta, numero=1, dpi=150):
    """Devuelve (bytes_png, ancho_pt, alto_pt) de la página indicada."""
    documento = abrir(ruta)
    try:
        pagina = documento[max(0, int(numero) - 1)]
        zoom = float(dpi) / 72.0
        matriz = fitz.Matrix(zoom, zoom)
        pix = pagina.get_pixmap(matrix=matriz, alpha=False)
        return pix.tobytes("png"), float(pagina.rect.width), float(pagina.rect.height)
    finally:
        documento.close()


def exportar_compuesto(ruta_pdf, ruta_salida, numero=1, capa_pdf=None, ancho=None, alto=None):
    """Exporta el plano original + una capa (PDF) encima, conservando lo vectorial.

    'capa_pdf' es un PDF temporal generado con reportlab (objetos del editor).
    """
    if os.path.abspath(ruta_pdf) == os.path.abspath(ruta_salida):
        raise ValueError("El archivo de salida no puede ser el mismo PDF original.")
    documento = abrir(ruta_pdf)
    try:
        seleccion = fitz.open()
        seleccion.insert_pdf(documento, from_page=max(0, int(numero) - 1),
                             to_page=max(0, int(numero) - 1))
        if capa_pdf and os.path.isfile(capa_pdf):
            with fitz.open(capa_pdf) as capa:
                seleccion[0].show_pdf_page(seleccion[0].rect, capa, 0, overlay=True)
        seleccion.save(ruta_salida, garbage=3, deflate=True)
        seleccion.close()
    finally:
        documento.close()
    return ruta_salida
