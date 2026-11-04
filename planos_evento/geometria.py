# -*- coding: utf-8 -*-
"""Geometría 2D: escalas, transformaciones, rotación, snap y colisiones."""
import math


def distancia(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def px_por_metro(p1, p2, metros):
    """Escala efectiva: cuántos puntos hay por metro (calibración visual)."""
    if not metros or metros <= 0:
        return 0.0
    return distancia(p1, p2) / float(metros)


def snap(valor, paso):
    """Ajusta 'valor' al múltiplo de 'paso' más cercano (cuadrícula)."""
    if not paso or paso <= 0:
        return valor
    return round(valor / paso) * paso


def rotar_punto(x, y, cx, cy, grados):
    """Rota (x, y) alrededor de (cx, cy) 'grados' (sentido antihorario)."""
    if not grados:
        return x, y
    rad = math.radians(grados)
    dx, dy = x - cx, y - cy
    return (cx + dx * math.cos(rad) - dy * math.sin(rad),
            cy + dx * math.sin(rad) + dy * math.cos(rad))


def esquinas(rect):
    """Esquinas de un rectángulo rotado: (x, y, ancho, alto, rotación) en píxeles."""
    x, y, ancho, alto, rot = rect
    cx, cy = x, y
    hw, hh = ancho / 2.0, alto / 2.0
    puntos = [(cx - hw, cy - hh), (cx + hw, cy - hh),
              (cx + hw, cy + hh), (cx - hw, cy + hh)]
    if rot:
        puntos = [rotar_punto(px, py, cx, cy, rot) for px, py in puntos]
    return puntos


def bbox(rect):
    """Caja envolvente (x0, y0, x1, y1) de un rectángulo rotado."""
    puntos = esquinas(rect)
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    return min(xs), min(ys), max(xs), max(ys)


def punto_en_rect(px, py, rect):
    """¿El punto está dentro del rectángulo (aunque esté rotado)?"""
    x, y, ancho, alto, rot = rect
    if rot:
        px, py = rotar_punto(px, py, x, y, -rot)
    return abs(px - x) <= ancho / 2.0 and abs(py - y) <= alto / 2.0


def rects_se_tocan(rect_a, rect_b):
    """Colisión por cajas envolventes (suficiente para avisar superposiciones)."""
    ax0, ay0, ax1, ay1 = bbox(rect_a)
    bx0, by0, bx1, by1 = bbox(rect_b)
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def interseccion_area(rect_a, rect_b):
    """Área aproximada de superposición entre dos rectángulos."""
    ax0, ay0, ax1, ay1 = bbox(rect_a)
    bx0, by0, bx1, by1 = bbox(rect_b)
    ancho = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    alto = max(0.0, min(ay1, by1) - max(ay0, by0))
    return ancho * alto


def ajustes_alineacion(rect, otros, tolerancia=2.0):
    """Valores de alineación (izquierda, centro, derecha, arriba, medio, abajo)."""
    x, y, ancho, alto, _rot = rect
    valores = {"izquierda": x - ancho / 2.0, "centro_x": x, "derecha": x + ancho / 2.0,
               "arriba": y - alto / 2.0, "centro_y": y, "abajo": y + alto / 2.0}
    candidatos = {clave: [] for clave in valores}
    for otro in otros:
        ox, oy, oancho, oalto, _orot = otro
        comparar = {"izquierda": ox - oancho / 2.0, "centro_x": ox,
                    "derecha": ox + oancho / 2.0, "arriba": oy - oalto / 2.0,
                    "centro_y": oy, "abajo": oy + oalto / 2.0}
        for clave, valor in valores.items():
            if abs(comparar[clave] - valor) <= tolerancia:
                candidatos[clave].append(comparar[clave])
    return valores, candidatos


def alinear(rects, modo):
    """Aplica alineación a una lista de rectángulos y devuelve los nuevos centros."""
    if not rects:
        return []
    if modo == "izquierda":
        objetivo = min(r[0] - r[2] / 2.0 for r in rects)
        return [(objetivo + r[2] / 2.0, r[1]) for r in rects]
    if modo == "derecha":
        objetivo = max(r[0] + r[2] / 2.0 for r in rects)
        return [(objetivo - r[2] / 2.0, r[1]) for r in rects]
    if modo == "arriba":
        objetivo = min(r[1] - r[3] / 2.0 for r in rects)
        return [(r[0], objetivo + r[3] / 2.0) for r in rects]
    if modo == "abajo":
        objetivo = max(r[1] + r[3] / 2.0 for r in rects)
        return [(r[0], objetivo - r[3] / 2.0) for r in rects]
    if modo == "centro_h":
        objetivo = sum(r[0] for r in rects) / len(rects)
        return [(objetivo, r[1]) for r in rects]
    if modo == "centro_v":
        objetivo = sum(r[1] for r in rects) / len(rects)
        return [(r[0], objetivo) for r in rects]
    return [(r[0], r[1]) for r in rects]


def distribuir(rects, horizontal=True):
    """Reparte los rectángulos dejando la MISMA separación entre sus bordes.

    El primero y el último no se mueven: sirven de topes. Los del medio se
    colocan de forma que el hueco entre cada par sea idéntico.
    Devuelve [(x, y)] en el mismo orden que 'rects' y el hueco calculado.
    """
    if len(rects) < 3:
        return [(r[0], r[1]) for r in rects], 0.0
    indice = 0 if horizontal else 1
    tamano = 2 if horizontal else 3
    ordenados = sorted(rects, key=lambda r: r[indice])
    primero, ultimo = ordenados[0], ordenados[-1]
    # tramo total entre el borde inicial del primero y el borde final del último
    inicio = primero[indice] - primero[tamano] / 2.0
    fin = ultimo[indice] + ultimo[tamano] / 2.0
    suma_tamanos = sum(r[tamano] for r in ordenados)
    hueco = (fin - inicio - suma_tamanos) / float(len(ordenados) - 1)
    resultado = {}
    puntero = inicio
    for posicion, r in enumerate(ordenados):
        if posicion == 0:
            resultado[id(r)] = (r[0], r[1])
            puntero = inicio + r[tamano]
            continue
        if posicion == len(ordenados) - 1:
            resultado[id(r)] = (r[0], r[1])
            break
        centro = puntero + hueco + r[tamano] / 2.0
        if horizontal:
            resultado[id(r)] = (centro, r[1])
        else:
            resultado[id(r)] = (r[0], centro)
        puntero = puntero + hueco + r[tamano]
    return [resultado.get(id(r), (r[0], r[1])) for r in rects], hueco


def ajustes_alineacion(rect, otros, tolerancia=2.0):
    """Valores de alineación (izquierda, centro, derecha, arriba, medio, abajo)."""
    x, y, ancho, alto, _rot = rect
    valores = {"izquierda": x - ancho / 2.0, "centro_x": x, "derecha": x + ancho / 2.0,
               "arriba": y - alto / 2.0, "centro_y": y, "abajo": y + alto / 2.0}
    candidatos = {clave: [] for clave in valores}
    for otro in otros:
        ox, oy, oancho, oalto, _orot = otro
        comparar = {"izquierda": ox - oancho / 2.0, "centro_x": ox,
                    "derecha": ox + oancho / 2.0, "arriba": oy - oalto / 2.0,
                    "centro_y": oy, "abajo": oy + oalto / 2.0}
        for clave, valor in valores.items():
            if abs(comparar[clave] - valor) <= tolerancia:
                candidatos[clave].append(comparar[clave])
    return valores, candidatos


def alinear(rects, modo):
    """Aplica alineación a una lista de rectángulos y devuelve los nuevos centros."""
    if not rects:
        return []
    if modo == "izquierda":
        objetivo = min(r[0] - r[2] / 2.0 for r in rects)
        return [(objetivo + r[2] / 2.0, r[1]) for r in rects]
    if modo == "derecha":
        objetivo = max(r[0] + r[2] / 2.0 for r in rects)
        return [(objetivo - r[2] / 2.0, r[1]) for r in rects]
    if modo == "arriba":
        objetivo = min(r[1] - r[3] / 2.0 for r in rects)
        return [(r[0], objetivo + r[3] / 2.0) for r in rects]
    if modo == "abajo":
        objetivo = max(r[1] + r[3] / 2.0 for r in rects)
        return [(r[0], objetivo - r[3] / 2.0) for r in rects]
    if modo == "centro_h":
        objetivo = sum(r[0] for r in rects) / len(rects)
        return [(objetivo, r[1]) for r in rects]
    if modo == "centro_v":
        objetivo = sum(r[1] for r in rects) / len(rects)
        return [(r[0], objetivo) for r in rects]
    return [(r[0], r[1]) for r in rects]
