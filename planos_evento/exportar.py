# -*- coding: utf-8 -*-
"""Exportación de planos: PDF compuesto, PNG y SVG (sin dependencias nuevas).

El PDF original NUNCA se modifica: se toma su página, se dibujan encima los
objetos del editor (vectoriales, con reportlab) y se compone un PDF nuevo.
"""
import math
import os
import tempfile

try:
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.colors import HexColor
    REPORTLAB_OK = True
except Exception:                                     # pragma: no cover
    REPORTLAB_OK = False

from . import pdf_pagina

# 1 px de imagen (150 dpi) = 72/150 puntos
PT_POR_PX_IMAGEN = 72.0 / 150.0


def pt_por_metro(plano):
    """Puntos (PDF) por metro, según la calibración del plano."""
    if plano.paginas and plano.paginas[0].px_por_metro:
        return float(plano.paginas[0].px_por_metro) * PT_POR_PX_IMAGEN
    return 40.0 * PT_POR_PX_IMAGEN


def _elementos_visibles(plano, solo_visibles=True):
    salida = []
    for elemento in plano.elementos:
        capa = plano.capa(elemento.capa)
        if solo_visibles:
            if capa and not capa.visible:
                continue
            if not elemento.visible:
                continue
        salida.append(elemento)
    return sorted(salida, key=lambda e: e.orden)


def _dibujar_objetos(c, plano, alto_pt, solo_visibles=True, escala_pt=None):
    """Dibuja los objetos sobre el canvas de reportlab (coordenadas en puntos)."""
    escala = escala_pt if escala_pt else pt_por_metro(plano)
    for elemento in _elementos_visibles(plano, solo_visibles):
        try:
            color = HexColor(elemento.color or "#1f538d")
        except Exception:
            color = HexColor("#1f538d")
        c.saveState()
        try:
            c.setStrokeColor(color)
            c.setFillColor(HexColor("#ffffff"))
            c.setLineWidth(1.2)
            ancho = max(0.5, elemento.ancho * escala)
            alto = max(0.5, elemento.alto * escala)
            x = elemento.x * escala
            y = alto_pt - elemento.y * escala
            c.translate(x, y)
            if elemento.rotacion:
                c.rotate(-elemento.rotacion)
            x0, y0 = -ancho / 2.0, -alto / 2.0
            tipo = elemento.tipo
            if tipo in ("circulo", "mesa_redonda", "planta", "flores", "tarima_circular",
                        "arco", "totem", "parlante", "puff"):
                c.ellipse(x0, y0, x0 + ancho, y0 + alto, stroke=1, fill=0)
                if tipo == "mesa_redonda":
                    c.line(x0 + ancho * 0.2, y0 + alto / 2.0, x0 + ancho * 0.8, y0 + alto / 2.0)
                    c.line(x0 + ancho / 2.0, y0 + alto * 0.2, x0 + ancho / 2.0, y0 + alto * 0.8)
            elif tipo == "flecha":
                cuerpo = alto * 0.35
                puntos = [(x0, -cuerpo / 2), (x0 + ancho * 0.7, -cuerpo / 2),
                          (x0 + ancho * 0.7, y0), (x0 + ancho, 0),
                          (x0 + ancho * 0.7, y0 + alto), (x0 + ancho * 0.7, cuerpo / 2),
                          (x0, cuerpo / 2)]
                p = c.beginPath()
                p.moveTo(*puntos[0])
                for px, py in puntos[1:]:
                    p.lineTo(px, py)
                p.close()
                c.drawPath(p, stroke=1, fill=0)
            elif tipo == "texto":
                c.setFont("Helvetica", max(6, 10))
                c.drawCentredString(0, 0, (elemento.texto or elemento.nombre or "")[:80])
            else:
                c.rect(x0, y0, ancho, alto, stroke=1, fill=0)
                if tipo in ("escenario", "tarima"):
                    c.line(x0, y0 + alto * 0.25, x0 + ancho, y0 + alto * 0.25)
                elif tipo == "pantalla":
                    c.line(x0, y0, x0 + ancho, y0)
            if elemento.nombre and tipo not in ("texto",):
                _texto_etiqueta(c, elemento, x0, y0, ancho, alto)
        finally:
            c.restoreState()


def _texto_etiqueta(c, elemento, x0, y0, ancho, alto):
    """Nombre del objeto respetando posición, tamaño y color elegidos."""
    try:
        color = HexColor(elemento.color_texto or "#555555")
    except Exception:
        color = HexColor("#555555")
    tamano = max(4.0, min(48.0, float(elemento.tamano_texto or 9) * 0.75))
    posicion = (elemento.texto_pos or "abajo").lower()
    margen = max(3.0, tamano * 0.5)
    c.setFillColor(color)
    c.setFont("Helvetica", tamano)
    texto = (elemento.nombre or "")[:40]
    cx, cy = x0 + ancho / 2.0, y0 + alto / 2.0
    if posicion == "arriba":
        c.drawCentredString(cx, y0 + alto + margen, texto)
    elif posicion == "dentro":
        c.drawCentredString(cx, cy - tamano * 0.35, texto)
    elif posicion == "izquierda":
        c.drawRightString(x0 - margen, cy - tamano * 0.35, texto)
    elif posicion == "derecha":
        c.drawString(x0 + ancho + margen, cy - tamano * 0.35, texto)
    else:
        c.drawCentredString(cx, y0 - margen - tamano, texto)


def _exportar_hoja_blanca(plano, ruta_salida, solo_visibles=True):
    """⬜ Exporta un lienzo sin PDF: hoja vacía + objetos (1 m = 1 cm, escala 1:100)."""
    if not REPORTLAB_OK:
        raise RuntimeError("Falta reportlab para exportar a PDF.")
    pagina = plano.paginas[0] if plano.paginas else None
    ancho_m = getattr(pagina, "ancho_m", 0) or 20.0
    alto_m = getattr(pagina, "alto_m", 0) or 15.0
    ancho_pt = ancho_m * 28.3465
    alto_pt = alto_m * 28.3465
    c = rl_canvas.Canvas(ruta_salida, pagesize=(ancho_pt, alto_pt))
    c.setStrokeColor(HexColor("#2c3e50"))
    c.setLineWidth(1)
    c.rect(0.5, 0.5, ancho_pt - 0.5, alto_pt - 0.5, stroke=1, fill=0)
    _dibujar_objetos(c, plano, alto_pt, solo_visibles, escala_pt=28.3465)
    c.showPage()
    c.save()
    return ruta_salida


def exportar_pdf(plano, ruta_salida, solo_visibles=True):
    """PDF nuevo: página original + objetos del editor encima."""
    if not REPORTLAB_OK:
        raise RuntimeError("Falta reportlab para exportar a PDF.")
    ruta_pdf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    from . import almacen
    original = almacen.ruta_pdf_original(plano.id)
    if not os.path.isfile(original):
        original = plano.pdf_ruta
    if not original or not os.path.isfile(original):
        return _exportar_hoja_blanca(plano, ruta_salida, solo_visibles)
    paginas = pdf_pagina.info_paginas(original)
    indice = min(max(0, int(plano.pagina or 0)), len(paginas) - 1)
    ancho_pt = paginas[indice]["ancho"]
    alto_pt = paginas[indice]["alto"]

    temporal = os.path.join(tempfile.mkdtemp(prefix="capa_plano_"), "capa.pdf")
    c = rl_canvas.Canvas(temporal, pagesize=(ancho_pt, alto_pt))
    _dibujar_objetos(c, plano, alto_pt, solo_visibles)
    c.save()
    try:
        pdf_pagina.exportar_compuesto(original, ruta_salida, indice + 1, capa_pdf=temporal)
    finally:
        try:
            os.remove(temporal)
        except Exception:
            pass
    return ruta_salida


def exportar_png(plano, ruta_salida, dpi=150, solo_visibles=True):
    """PNG de alta resolución: se compone el PDF (o la hoja) y se rasteriza."""
    temporal = os.path.join(tempfile.mkdtemp(prefix="png_plano_"), "completo.pdf")
    exportar_pdf(plano, temporal, solo_visibles)
    import fitz
    with fitz.open(temporal) as documento:
        pagina = documento[0]
        zoom = float(dpi) / 72.0
        pix = pagina.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(ruta_salida)
    try:
        os.remove(temporal)
    except Exception:
        pass
    return ruta_salida


def exportar_svg(plano, ruta_salida, solo_visibles=True):
    """SVG con los objetos del editor (el fondo PDF no se convierte)."""
    paginas = []
    try:
        from . import almacen
        original = almacen.ruta_pdf_original(plano.id) or plano.pdf_ruta
        if original and os.path.isfile(original):
            paginas = pdf_pagina.info_paginas(original)
    except Exception:
        paginas = []
    indice = min(max(0, int(plano.pagina or 0)), max(0, len(paginas) - 1)) if paginas else 0
    if not paginas:
        pagina = plano.paginas[0] if plano.paginas else None
        ancho_pt = (getattr(pagina, "ancho_m", 0) or 20.0) * 28.3465
        alto_pt = (getattr(pagina, "alto_m", 0) or 15.0) * 28.3465
    else:
        ancho_pt = paginas[indice]["ancho"]
        alto_pt = paginas[indice]["alto"]
    escala = pt_por_metro(plano) if paginas else 28.3465
    partes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<svg xmlns="http://www.w3.org/2000/svg" width="%.2f" height="%.2f" '
              'viewBox="0 0 %.2f %.2f">' % (ancho_pt, alto_pt, ancho_pt, alto_pt),
              '<g stroke-width="1.2" fill="none">']
    for elemento in _elementos_visibles(plano, solo_visibles):
        ancho = max(0.5, elemento.ancho * escala)
        alto = max(0.5, elemento.alto * escala)
        cx = elemento.x * escala
        cy = alto_pt - elemento.y * escala
        color = elemento.color or "#1f538d"
        rot = (' transform="rotate(%.2f %.2f %.2f)"' % (elemento.rotacion, cx, cy)
               if elemento.rotacion else "")
        if elemento.tipo in ("circulo", "mesa_redonda", "planta", "flores", "tarima_circular",
                             "arco", "totem", "parlante", "puff"):
            partes.append('<ellipse cx="%.2f" cy="%.2f" rx="%.2f" ry="%.2f" stroke="%s"%s/>'
                          % (cx, cy, ancho / 2.0, alto / 2.0, color, rot))
        else:
            partes.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" stroke="%s"%s/>'
                          % (cx - ancho / 2.0, cy - alto / 2.0, ancho, alto, color, rot))
        if elemento.nombre and elemento.tipo != "texto":
            tamano_txt = float(elemento.tamano_texto or 9) * 0.75
            posicion = (elemento.texto_pos or "abajo").lower()
            if posicion == "arriba":
                tx, ty, ancla = cx, cy - alto / 2.0 - 4, "middle"
            elif posicion == "dentro":
                tx, ty, ancla = cx, cy + tamano_txt * 0.35, "middle"
            elif posicion == "izquierda":
                tx, ty, ancla = cx - ancho / 2.0 - 4, cy + tamano_txt * 0.35, "end"
            elif posicion == "derecha":
                tx, ty, ancla = cx + ancho / 2.0 + 4, cy + tamano_txt * 0.35, "start"
            else:
                tx, ty, ancla = cx, cy + alto / 2.0 + tamano_txt + 3, "middle"
            partes.append('<text x="%.2f" y="%.2f" font-family="Arial" font-size="%.1f" '
                          'fill="%s" text-anchor="%s">%s</text>'
                          % (tx, ty, tamano_txt, elemento.color_texto or "#555555",
                             ancla, _escapar(elemento.nombre[:40])))
        if elemento.tipo == "texto" and (elemento.texto or elemento.nombre):
            partes.append('<text x="%.2f" y="%.2f" font-family="Arial" font-size="10" '
                          'fill="%s" text-anchor="middle">%s</text>'
                          % (cx, cy, color, _escapar((elemento.texto or elemento.nombre)[:80])))
    partes.append("</g>")
    partes.append("</svg>")
    with open(ruta_salida, "w", encoding="utf-8") as archivo:
        archivo.write("\n".join(partes))
    return ruta_salida


def _escapar(texto):
    return (str(texto).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
