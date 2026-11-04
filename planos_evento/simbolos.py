# -*- coding: utf-8 -*-
"""Dibujo 2D (vista superior) de los objetos del plano sobre un tk.Canvas.

Todas las funciones reciben el rectángulo del objeto en píxeles de pantalla:
(x0, y0, x1, y1) y devuelven la lista de ids creados, etiquetados con 'obj'.
No usan imágenes: son formas vectoriales simples, como en un plano técnico.
"""


def _etiqueta(tags, extra=None):
    base = ["obj"] + list(tags or [])
    if extra:
        base.append(extra)
    return tuple(base)


def _texto(canvas, x, y, texto, color, tamano, tags, anchor="center"):
    if not texto:
        return []
    return [canvas.create_text(x, y, text=texto, fill=color, anchor=anchor,
                               font=("Arial", max(6, int(tamano))), tags=tags)]


def dibujar(canvas, elemento, caja, tags, zoom=1.0, con_texto=True):
    """Dibuja 'elemento' en 'caja' = (x0, y0, x1, y1) de pantalla.

    Devuelve la lista de ids creados (todos con la misma etiqueta 'tags').
    """
    x0, y0, x1, y1 = caja
    ancho, alto = max(1.0, x1 - x0), max(1.0, y1 - y0)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    color = elemento.color or "#1f538d"
    tipo = elemento.tipo or "rectangulo"
    ids = []

    def rect(**kw):
        ids.append(canvas.create_rectangle(x0, y0, x1, y1, outline=color, tags=tags, **kw))

    def oval(**kw):
        ids.append(canvas.create_oval(x0, y0, x1, y1, outline=color, tags=tags, **kw))

    def linea(ax, ay, bx, by, **kw):
        ids.append(canvas.create_line(ax, ay, bx, by, fill=color, tags=tags, **kw))

    if tipo in ("rectangulo", "area", "alfombra"):
        if tipo == "alfombra":
            rect(fill=_suave(color))
        elif tipo == "area":
            rect(dash=(4, 3), width=2)
        else:
            rect(width=1.5)

    elif tipo == "circulo":
        oval(width=1.5)

    elif tipo == "mesa_redonda":
        oval(width=1.5)
        radio = min(ancho, alto) / 2.0
        if radio > 8:
            linea(cx - radio * 0.6, cy, cx + radio * 0.6, cy)
            linea(cx, cy - radio * 0.6, cx, cy + radio * 0.6)

    elif tipo == "silla":
        rect(width=1.2)
        # respaldo del asiento (vista superior)
        margen = alto * 0.18
        ids.append(canvas.create_rectangle(x0 + margen, y0 + margen,
                                           x1 - margen, y0 + alto * 0.34,
                                           outline=color, fill=color, tags=tags))

    elif tipo == "sofa":
        rect(width=1.5, fill=_suave(color))
        linea(x0, y0 + alto * 0.28, x1, y0 + alto * 0.28, width=1)
        if ancho > 40:
            linea(x0 + ancho * 0.5, y0 + alto * 0.28, x0 + ancho * 0.5, y1, width=1)

    elif tipo == "barra":
        rect(width=1.6, fill=_suave(color))
        linea(x0, y0 + alto * 0.55, x1, y0 + alto * 0.55, width=1)

    elif tipo == "mesa_dj":
        rect(width=1.5, fill=_suave(color))
        oval_ancho = min(ancho, alto) * 0.5
        ids.append(canvas.create_oval(cx - oval_ancho / 2, cy - oval_ancho / 2,
                                      cx + oval_ancho / 2, cy + oval_ancho / 2,
                                      outline=color, tags=tags))

    elif tipo == "escenario":
        rect(width=2, fill="#eaf2f8")
        linea(x0, y1 - alto * 0.25, x1, y1 - alto * 0.25, width=1)

    elif tipo == "tarima":
        rect(width=1.8)
        ids.append(canvas.create_rectangle(x0 + 3, y0 + 3, x1 - 3, y1 - 3,
                                           outline=color, dash=(3, 2), tags=tags))

    elif tipo == "tarima_circular":
        oval(width=2)
        ids.append(canvas.create_oval(x0 + 3, y0 + 3, x1 - 3, y1 - 3,
                                      outline=color, dash=(3, 2), tags=tags))

    elif tipo == "backing":
        rect(width=2, fill=color)

    elif tipo == "backing_curvo":
        ids.append(canvas.create_arc(x0, y0, x1, y1, start=0, extent=180,
                                     style="arc", outline=color, width=2, tags=tags))
        linea(x0, y0 + alto * 0.5, x1, y0 + alto * 0.5, width=1)

    elif tipo == "panel":
        rect(width=2, fill=color)

    elif tipo == "truss":
        rect(width=1.5)
        pasos = max(2, int(ancho / 12))
        for i in range(1, pasos):
            x = x0 + ancho * i / pasos
            linea(x, y0, x, y1, width=1)

    elif tipo == "portico":
        rect(width=2)
        linea(x0, y0, x1, y1, width=1)
        linea(x0, y1, x1, y0, width=1)

    elif tipo == "arco":
        ids.append(canvas.create_arc(x0, y0, x1, y1, start=0, extent=180,
                                     style="arc", outline=color, width=2, tags=tags))

    elif tipo == "totem":
        oval(width=1.5, fill=_suave(color))
        ids.append(canvas.create_text(cx, cy, text="i", fill=color,
                                      font=("Arial", max(6, int(alto * 0.5))), tags=tags))

    elif tipo == "glorificador":
        rect(width=1.5)
        linea(x0, y0, x1, y1, width=1)

    elif tipo == "cubo":
        rect(width=1.5)
        ids.append(canvas.create_rectangle(x0 + 2, y0 + 2, x1 - 2, y1 - 2,
                                           outline=color, tags=tags))

    elif tipo == "pantalla":
        rect(width=2, fill=color)
        linea(x0, y1, x1, y1, width=2)

    elif tipo == "monitor":
        rect(width=1.5, fill=_suave(color))
        linea(cx, y1, cx, y1 + max(2, alto * 0.35), width=1)

    elif tipo == "parlante":
        rect(width=1.5)
        ids.append(canvas.create_oval(x0 + 2, y0 + 2, x1 - 2, y1 - 2,
                                      outline=color, tags=tags))

    elif tipo == "torre":
        rect(width=1.5)
        ids.append(canvas.create_text(cx, cy, text="♫", fill=color,
                                      font=("Arial", max(6, int(alto * 0.5))), tags=tags))

    elif tipo == "consola":
        rect(width=1.5, fill=_suave(color))
        linea(x0, cy, x1, cy, width=1)

    elif tipo == "camara":
        rect(width=1.3)
        linea(cx, cy, x1 + max(4, ancho * 0.6), cy, width=1, dash=(2, 2))

    elif tipo == "proyector":
        rect(width=1.3)
        ids.append(canvas.create_polygon(x1, cy - alto * 0.25, x1 + ancho * 0.7, cy - alto * 0.5,
                                         x1 + ancho * 0.7, cy + alto * 0.5, x1, cy + alto * 0.25,
                                         outline=color, dash=(2, 2), tags=tags))

    elif tipo == "iluminacion":
        rect(width=1.5)
        pasos = max(2, int(ancho / 14))
        for i in range(1, pasos):
            x = x0 + ancho * i / pasos
            ids.append(canvas.create_oval(x - 3, cy - 3, x + 3, cy + 3,
                                          outline=color, tags=tags))

    elif tipo == "planta":
        oval(width=1.4, fill=_suave(color))
        radio = min(ancho, alto) / 2.0
        if radio > 10:
            for factor in (0.45, 0.75):
                ids.append(canvas.create_oval(cx - radio * factor, cy - radio * factor,
                                              cx + radio * factor, cy + radio * factor,
                                              outline=color, tags=tags))

    elif tipo == "flores":
        oval(width=1.2)
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            ids.append(canvas.create_line(cx, cy, cx + dx * ancho * 0.3, cy + dy * alto * 0.3,
                                          fill=color, tags=tags))

    elif tipo == "jardinera":
        rect(width=1.4)
        pasos = max(2, int(ancho / 12))
        for i in range(1, pasos):
            x = x0 + ancho * i / pasos
            ids.append(canvas.create_oval(x - 2, cy - 2, x + 2, cy + 2,
                                          outline=color, tags=tags))

    elif tipo == "separador":
        rect(width=1.3)
        pasos = max(2, int(ancho / 10))
        for i in range(1, pasos):
            x = x0 + ancho * i / pasos
            linea(x, y0, x, y1, width=1)

    elif tipo == "balaustre":
        linea(x0, cy, x1, cy, width=2)
        for i in range(3):
            x = x0 + ancho * (i + 0.5) / 3.0
            ids.append(canvas.create_oval(x - 2, cy - 2, x + 2, cy + 2,
                                          outline=color, tags=tags))

    elif tipo == "senal":
        rect(width=1.3, fill=_suave(color))
        ids.append(canvas.create_text(cx, cy, text="!", fill=color,
                                      font=("Arial", max(6, int(alto * 0.6)), "bold"), tags=tags))

    elif tipo == "flecha":
        cuerpo = alto * 0.35
        ids.append(canvas.create_polygon(
            x0, cy - cuerpo / 2, x1 - ancho * 0.3, cy - cuerpo / 2,
            x1 - ancho * 0.3, y0, x1, cy,
            x1 - ancho * 0.3, y1, x1 - ancho * 0.3, cy + cuerpo / 2,
            x0, cy + cuerpo / 2, outline=color, fill=_suave(color), tags=tags))

    elif tipo == "acceso":
        rect(width=2, fill="#d5f5e3")
        ids.append(canvas.create_text(cx, cy, text="ENTRADA", fill=color,
                                      font=("Arial", max(5, int(alto * 0.45)), "bold"), tags=tags))

    elif tipo == "salida":
        rect(width=2, fill="#fadbd8")
        ids.append(canvas.create_text(cx, cy, text="SALIDA", fill=color,
                                      font=("Arial", max(5, int(alto * 0.45)), "bold"), tags=tags))

    elif tipo == "linea":
        linea(x0, cy, x1, cy, width=2)

    elif tipo == "polilinea":
        ids.append(canvas.create_line(x0, y1, x0 + ancho * 0.35, y0,
                                      x0 + ancho * 0.7, y1, x1, y0,
                                      fill=color, width=2, tags=tags))

    elif tipo == "poligono":
        ids.append(canvas.create_polygon(cx, y0, x1, cy, cx, y1, x0, cy,
                                         outline=color, tags=tags))

    elif tipo == "texto":
        ids.extend(_texto(canvas, cx, cy, elemento.texto or "Texto", color,
                          max(7, int(11 * zoom)), tags))

    elif tipo == "etiqueta":
        rect(width=1.2, fill=_suave(color))
        ids.extend(_texto(canvas, cx, cy, elemento.texto or "Etiqueta", color,
                          max(6, int(9 * zoom)), tags))

    elif tipo == "muro":
        # 🧱 Pared en planta: rectángulo relleno (el alto = espesor del muro)
        ids.append(canvas.create_rectangle(x0, y0, x1, y1, outline=color, fill=color,
                                           tags=tags))

    elif tipo == "muro_bajo":
        rect(width=1.5, fill=_suave(color))
        linea(x0, cy, x1, cy, width=1, dash=(3, 2))

    elif tipo == "muro_curvo":
        ids.append(canvas.create_arc(x0, y0 - alto * 2, x1, y1 + alto * 2,
                                     start=0, extent=180, style="arc", outline=color,
                                     width=max(2, int(alto * 0.8)), tags=tags))

    elif tipo == "puerta":
        # 🚪 Puerta: hoja + arco de giro
        ancho_hoja = min(ancho, alto * 6)
        linea(x0, y1, x0, y1 - ancho_hoja, width=2)                     # hoja
        ids.append(canvas.create_arc(x0, y1 - ancho_hoja * 2, x0 + ancho_hoja * 2, y1,
                                     start=90, extent=-90, style="arc",
                                     outline=color, tags=tags))          # giro
        ids.append(canvas.create_oval(x0 - 2, y1 - 2, x0 + 2, y1 + 2,
                                      fill=color, outline=color, tags=tags))

    elif tipo == "puerta_doble":
        hoja = min(ancho / 2.0, alto * 6)
        linea(x0, y1, x0, y1 - hoja, width=2)
        linea(x1, y1, x1, y1 - hoja, width=2)
        ids.append(canvas.create_arc(x0, y1 - hoja * 2, x0 + hoja * 2, y1,
                                     start=90, extent=-90, style="arc", outline=color, tags=tags))
        ids.append(canvas.create_arc(x1 - hoja * 2, y1 - hoja * 2, x1, y1,
                                     start=90, extent=90, style="arc", outline=color, tags=tags))

    elif tipo == "porton":
        rect(width=2, fill=_suave(color))
        linea(x0, y1, x1, y0, width=1)
        linea(x0 + ancho * 0.15, y1, x0 + ancho * 0.15, y0, width=1, dash=(2, 2))

    elif tipo == "vano":
        ids.append(canvas.create_rectangle(x0, y0, x1, y1, outline=color, dash=(3, 3),
                                           tags=tags))
        linea(x0, cy, x1, cy, width=1, dash=(2, 2))

    elif tipo == "ventana":
        # 🪟 Ventana: doble línea en el muro
        linea(x0, y0, x1, y0, width=2)
        linea(x0, y1, x1, y1, width=2)
        linea(x0, cy, x1, cy, width=1)
        pasos = max(2, int(ancho / 25))
        for i in range(1, pasos):
            x = x0 + ancho * i / pasos
            linea(x, y0, x, y1, width=1)

    elif tipo == "columna":
        ids.append(canvas.create_rectangle(x0, y0, x1, y1, outline=color, fill=color,
                                           tags=tags))

    elif tipo == "columna_redonda":
        oval(width=1.5, fill=color)

    elif tipo == "escalera":
        rect(width=2)
        pasos = max(3, int(alto / 12))
        for i in range(1, pasos):
            y = y0 + alto * i / pasos
            linea(x0, y, x1, y, width=1)
        ids.append(canvas.create_line(cx, y1 - alto * 0.08, cx, y0 + alto * 0.12,
                                      fill=color, width=2, arrow="last",
                                      arrowshape=(8, 10, 4), tags=tags))

    elif tipo == "rampa":
        # ♿ Rampa: contorno + líneas de pendiente + flecha de subida
        rect(width=2)
        for i in range(1, 4):
            y = y0 + alto * i / 4.0
            linea(x0 + 3, y, x1 - 3, y, width=1, dash=(3, 2))
        ids.append(canvas.create_line(cx, y1 - max(4, alto * 0.08), cx, y0 + max(4, alto * 0.06),
                                      fill=color, width=2, arrow="last",
                                      arrowshape=(8, 10, 4), tags=tags))
        ids.extend(_texto(canvas, x0 + max(8, ancho * 0.22), y0 + max(8, alto * 0.12),
                          "R", color, max(6, int(min(ancho, alto) * 0.3)), tags))

    elif tipo == "mascara":
        # ⬜ Tapa una zona del PDF escaneado (NO borra el original: solo lo cubre)
        ids.append(canvas.create_rectangle(x0, y0, x1, y1, outline="#c0392b", width=1,
                                           dash=(3, 2), fill="#ffffff", tags=tags))

    elif tipo == "imagen":
        # 🖼️ Objeto personalizado: el editor dibuja la imagen encima de este marco
        rect(width=1.2, dash=(2, 2))
        ids.extend(_texto(canvas, cx, cy, elemento.nombre or "imagen", "#7f8c8d",
                          max(6, int(8 * zoom)), tags))

    elif tipo == "cota":
        linea(x0, y0, x1, y0, width=1)
        for x in (x0, x1):
            linea(x, y0 - alto * 0.4, x, y0 + alto * 0.4, width=1)
        ids.extend(_texto(canvas, cx, y0 - max(6, alto * 0.6),
                          elemento.texto or "0.00 m", color, max(6, int(9 * zoom)), tags))

    else:                                  # cualquier tipo desconocido
        rect(width=1.5)

    if con_texto and elemento.nombre and tipo not in ("texto", "etiqueta", "cota"):
        ids.extend(_etiqueta_objeto(canvas, elemento, ancho, alto, caja, tags, zoom))
    return ids


def _etiqueta_objeto(canvas, elemento, ancho, alto, caja, tags, zoom):
    """Dibuja el nombre del objeto según su posición, tamaño y color."""
    x0, y0, x1, y1 = caja
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    tamano = max(6, int(round(float(elemento.tamano_texto or 9) * zoom)))
    color = elemento.color_texto or "#555555"
    posicion = (elemento.texto_pos or "abajo").lower()
    margen = max(3, int(4 * zoom))
    if posicion == "arriba":
        x, y, ancla = cx, y0 - margen, "s"
    elif posicion == "dentro":
        x, y, ancla = cx, cy, "center"
    elif posicion == "izquierda":
        x, y, ancla = x0 - margen, cy, "e"
    elif posicion == "derecha":
        x, y, ancla = x1 + margen, cy, "w"
    else:                                  # abajo (por defecto)
        x, y, ancla = cx, y1 + margen, "n"
    return _texto(canvas, x, y, elemento.nombre, color, tamano, tags, anchor=ancla)


def _suave(color):
    """Versión muy clara del color (relleno de símbolos)."""
    try:
        color = color.lstrip("#")
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        r = int(r + (255 - r) * 0.82)
        g = int(g + (255 - g) * 0.82)
        b = int(b + (255 - b) * 0.82)
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:
        return "#eaf2f8"
