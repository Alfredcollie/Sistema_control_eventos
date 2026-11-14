# -*- coding: utf-8 -*-

"""
=========================================================
VERIFICAR_LOGO_PDF.PY - Revisa el tamano del logo en un PDF
=========================================================
Uso:
    python verificar_logo_pdf.py "C:\\ruta\\cotizacion.pdf"
    python verificar_logo_pdf.py            (pide la ruta)

Sirve para confirmar, sin depender de la vista previa, si el logo quedo
dentro de la hoja o "expandido". Muestra:
  - pixeles reales de la imagen incrustada
  - tamano con el que se dibujo en el papel (en puntos, 72 pt = 1 pulgada)
  - si cabe en el area util de la hoja carta (532 x 100 pt como maximo)
"""

import os
import sys

# La consola de Windows (cp1252) falla con nombres que llevan tildes o la Ñ.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ANCHO_UTIL = 532.0   # 612 pt de la hoja - 40 de margen a cada lado
ALTO_UTIL = 100.0


def _abrir():
    try:
        import fitz  # PyMuPDF
        return fitz
    except Exception:
        print("Falta PyMuPDF. Instalalo con:  pip install PyMuPDF")
        return None


def revisar(ruta, fitz):
    if not os.path.exists(ruta):
        print("No existe el archivo:", ruta)
        return 1

    doc = fitz.open(ruta)
    print("=" * 70)
    print("Archivo :", os.path.basename(ruta))
    print("Paginas :", doc.page_count)
    hoja = doc[0].rect
    print("Hoja    : %.1f x %.1f pt" % (hoja.width, hoja.height))
    print("=" * 70)

    encontrados = 0
    problemas = 0

    for pno in range(doc.page_count):
        pagina = doc[pno]
        for img in pagina.get_images(full=True):
            xref = img[0]
            info = doc.extract_image(xref)
            px_w, px_h = info["width"], info["height"]
            for rect in pagina.get_image_rects(xref):
                encontrados += 1
                dpi_x = px_w * 72.0 / rect.width if rect.width else 0
                dentro = rect.width <= ANCHO_UTIL + 0.5 and rect.height <= ALTO_UTIL + 0.5
                if not dentro:
                    problemas += 1
                print("Pagina %d" % (pno + 1))
                print("  Pixeles de la imagen : %d x %d" % (px_w, px_h))
                print("  Dibujada en el PDF   : %.1f x %.1f pt  (x %.1f a %.1f / y %.1f a %.1f)"
                      % (rect.width, rect.height, rect.x0, rect.x1, rect.y0, rect.y1))
                print("  Densidad efectiva    : %.0f dpi" % dpi_x)
                print("  Estado               : %s" % ("OK - cabe en la hoja" if dentro
                      else "EXPANDIDO - se sale del area util"))
                print("-" * 70)

    if encontrados == 0:
        print("No se encontro ningun logo (imagen) en el PDF.")
        return 0

    print("Imagenes revisadas: %d   |   con problema: %d" % (encontrados, problemas))
    if problemas:
        print("Conclusion: hay %d logo(s) EXPANDIDO(S)." % problemas)
        print("Actualiza el programa a la version con el arreglo del logo")
        print("o configura en Ajustes un logo PNG con DPI declarados.")
    else:
        print("Conclusion: el logo esta dentro de la hoja (correcto).")
    return 0


def main():
    ruta = " ".join(sys.argv[1:]).strip().strip('"')
    if not ruta:
        ruta = input("Ruta del PDF a revisar: ").strip().strip('"')
    fitz = _abrir()
    if not fitz:
        return 1
    return revisar(ruta, fitz)


if __name__ == "__main__":
    sys.exit(main())
