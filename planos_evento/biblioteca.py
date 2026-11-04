# -*- coding: utf-8 -*-
"""Catálogo de objetos para planos de eventos (vista superior, 2D).

Cada elemento define: clave (única), nombre visible, categoría, tipo de dibujo,
medidas por defecto en METROS y color. 'simbolos.py' sabe dibujar cada tipo.
"""

CATEGORIAS = ("Muros y aberturas", "Mobiliario", "Escenarios y estructuras",
              "Audiovisuales", "Decoración", "Logística y circulación",
              "Formas básicas")


def _o(clave, nombre, categoria, tipo, ancho, alto, color="#1f538d", extra=None):
    datos = {"clave": clave, "nombre": nombre, "categoria": categoria, "tipo": tipo,
             "ancho": float(ancho), "alto": float(alto), "color": color}
    if extra:
        datos.update(extra)
    return datos


CATALOGO = [
    # ------------------------------------------------ Muros y aberturas
    _o("muro", "Pared / muro", "Muros y aberturas", "muro", 4.00, 0.15, "#2c3e50"),
    _o("muro_grueso", "Muro de carga", "Muros y aberturas", "muro", 4.00, 0.30, "#1c2833"),
    _o("muro_bajo", "Muro bajo / baranda", "Muros y aberturas", "muro_bajo", 3.00, 0.10, "#5d6d7e"),
    _o("muro_curvo", "Muro curvo", "Muros y aberturas", "muro_curvo", 4.00, 0.15, "#2c3e50"),
    _o("puerta", "Puerta", "Muros y aberturas", "puerta", 0.90, 0.15, "#7f8c8d"),
    _o("puerta_doble", "Puerta doble", "Muros y aberturas", "puerta_doble", 1.80, 0.15, "#7f8c8d"),
    _o("porton", "Portón", "Muros y aberturas", "porton", 3.00, 0.20, "#5d6d7e"),
    _o("vano", "Vano / paso libre", "Muros y aberturas", "vano", 1.20, 0.15, "#95a5a6"),
    _o("ventana", "Ventana", "Muros y aberturas", "ventana", 1.20, 0.15, "#2980b9"),
    _o("ventanal", "Ventanal", "Muros y aberturas", "ventana", 3.00, 0.20, "#2980b9"),
    _o("columna", "Columna", "Muros y aberturas", "columna", 0.30, 0.30, "#1c2833"),
    _o("columna_redonda", "Columna redonda", "Muros y aberturas", "columna_redonda", 0.30, 0.30, "#1c2833"),
    _o("escalera", "Escalera", "Muros y aberturas", "escalera", 1.20, 3.00, "#5d6d7e"),
    _o("rampa", "Rampa", "Muros y aberturas", "rampa", 1.50, 3.00, "#7f8c8d"),
    # ------------------------------------------------ Mobiliario
    _o("mesa_redonda", "Mesa redonda", "Mobiliario", "mesa_redonda", 1.80, 1.80, "#8e44ad"),
    _o("mesa_cuadrada", "Mesa cuadrada", "Mobiliario", "rectangulo", 1.00, 1.00, "#8e44ad"),
    _o("mesa_rectangular", "Mesa rectangular", "Mobiliario", "rectangulo", 1.80, 0.80, "#8e44ad"),
    _o("mesa_alta", "Mesa alta", "Mobiliario", "mesa_redonda", 0.80, 0.80, "#9b59b6"),
    _o("mesa_cocktail", "Mesa de cocktail", "Mobiliario", "mesa_redonda", 0.70, 0.70, "#9b59b6"),
    _o("silla", "Silla", "Mobiliario", "silla", 0.45, 0.45, "#7d3c98"),
    _o("banco", "Banco", "Mobiliario", "rectangulo", 1.60, 0.40, "#7d3c98"),
    _o("sofa", "Sofá", "Mobiliario", "sofa", 2.00, 0.85, "#6c3483"),
    _o("sillon", "Sillón", "Mobiliario", "sofa", 0.95, 0.85, "#6c3483"),
    _o("puff", "Puff", "Mobiliario", "mesa_redonda", 0.55, 0.55, "#af7ac5"),
    _o("barra", "Barra", "Mobiliario", "barra", 3.00, 0.60, "#7d3c98"),
    _o("mostrador", "Mostrador", "Mobiliario", "rectangulo", 2.00, 0.70, "#7d3c98"),
    _o("counter", "Counter de atención", "Mobiliario", "rectangulo", 1.60, 0.60, "#7d3c98"),
    _o("mesa_dj", "Mesa de DJ", "Mobiliario", "mesa_dj", 2.00, 0.70, "#6c3483"),
    _o("exhibidor", "Mueble de exhibición", "Mobiliario", "rectangulo", 1.20, 0.50, "#7d3c98"),

    # ------------------------------------------------ Escenarios y estructuras
    _o("escenario", "Escenario rectangular", "Escenarios y estructuras", "escenario", 6.00, 3.00, "#1f538d"),
    _o("tarima", "Tarima", "Escenarios y estructuras", "tarima", 4.00, 2.50, "#2471a3"),
    _o("tarima_circular", "Tarima circular", "Escenarios y estructuras", "tarima_circular", 3.00, 3.00, "#2471a3"),
    _o("backing_recto", "Backing recto", "Escenarios y estructuras", "backing", 6.00, 0.25, "#1b4f72"),
    _o("backing_curvo", "Backing curvo", "Escenarios y estructuras", "backing_curvo", 6.00, 1.00, "#1b4f72"),
    _o("panel_divisor", "Panel divisor", "Escenarios y estructuras", "panel", 2.00, 0.10, "#5d6d7e"),
    _o("pared_temporal", "Pared temporal", "Escenarios y estructuras", "panel", 4.00, 0.15, "#5d6d7e"),
    _o("truss", "Estructura de truss", "Escenarios y estructuras", "truss", 6.00, 0.40, "#2c3e50"),
    _o("portico", "Pórtico", "Escenarios y estructuras", "portico", 4.00, 0.60, "#2c3e50"),
    _o("arco", "Arco", "Escenarios y estructuras", "arco", 3.00, 3.00, "#2c3e50"),
    _o("totem", "Tótem", "Escenarios y estructuras", "totem", 0.80, 0.80, "#1b4f72"),
    _o("podio", "Podio", "Escenarios y estructuras", "rectangulo", 0.80, 0.60, "#1b4f72"),
    _o("glorificador", "Glorificador", "Escenarios y estructuras", "glorificador", 1.20, 0.60, "#2471a3"),
    _o("stand", "Stand", "Escenarios y estructuras", "rectangulo", 3.00, 3.00, "#2471a3"),
    _o("cubo", "Cubo de exhibición", "Escenarios y estructuras", "cubo", 1.00, 1.00, "#2471a3"),

    # ------------------------------------------------ Audiovisuales
    _o("pantalla_led", "Pantalla LED", "Audiovisuales", "pantalla", 4.00, 0.30, "#117864"),
    _o("pantalla_proyeccion", "Pantalla de proyección", "Audiovisuales", "pantalla", 3.00, 0.20, "#117864"),
    _o("monitor", "Monitor", "Audiovisuales", "monitor", 0.60, 0.25, "#148f77"),
    _o("televisor", "Televisor", "Audiovisuales", "monitor", 1.20, 0.20, "#148f77"),
    _o("parlante", "Parlante", "Audiovisuales", "parlante", 0.60, 0.60, "#0e6655"),
    _o("torre_sonido", "Torre de sonido", "Audiovisuales", "torre", 0.80, 0.80, "#0e6655"),
    _o("consola", "Consola", "Audiovisuales", "consola", 2.00, 0.80, "#117864"),
    _o("camara", "Cámara", "Audiovisuales", "camara", 0.50, 0.50, "#148f77"),
    _o("proyector", "Proyector", "Audiovisuales", "proyector", 0.50, 0.40, "#117864"),
    _o("iluminacion", "Estructura de iluminación", "Audiovisuales", "iluminacion", 3.00, 0.50, "#0e6655"),

    # ------------------------------------------------ Decoración
    _o("maceta", "Maceta", "Decoración", "planta", 0.50, 0.50, "#1e8449"),
    _o("planta", "Planta", "Decoración", "planta", 0.80, 0.80, "#229954"),
    _o("arbol", "Árbol", "Decoración", "planta", 2.00, 2.00, "#196f3d"),
    _o("jardinera", "Jardinera", "Decoración", "jardinera", 1.80, 0.50, "#1e8449"),
    _o("arreglo_floral", "Arreglo floral", "Decoración", "flores", 0.60, 0.60, "#d68910"),
    _o("deco_circular", "Elemento decorativo circular", "Decoración", "mesa_redonda", 1.20, 1.20, "#b9770e"),
    _o("alfombra", "Alfombra", "Decoración", "alfombra", 3.00, 2.00, "#a04000"),
    _o("plataforma", "Plataforma decorativa", "Decoración", "tarima", 2.00, 1.50, "#b9770e"),

    # ------------------------------------------------ Logística y circulación
    _o("valla", "Valla", "Logística y circulación", "panel", 2.00, 0.08, "#7f8c8d"),
    _o("separador", "Separador de fila", "Logística y circulación", "separador", 2.00, 0.90, "#95a5a6"),
    _o("balaustre", "Balaustre", "Logística y circulación", "balaustre", 1.00, 0.10, "#7f8c8d"),
    _o("senaletica", "Señalética", "Logística y circulación", "senal", 0.60, 0.40, "#f39c12"),
    _o("flecha", "Flecha de circulación", "Logística y circulación", "flecha", 1.50, 0.60, "#e67e22"),
    _o("acceso", "Punto de acceso", "Logística y circulación", "acceso", 1.20, 0.20, "#1e8449"),
    _o("salida", "Punto de salida", "Logística y circulación", "salida", 1.20, 0.20, "#c0392b"),
    _o("area_restringida", "Área restringida", "Logística y circulación", "area", 3.00, 2.00, "#c0392b"),
    _o("almacenamiento", "Zona de almacenamiento", "Logística y circulación", "area", 3.00, 2.00, "#7f8c8d"),
    _o("zona_personal", "Zona de personal", "Logística y circulación", "area", 3.00, 2.00, "#2980b9"),
    _o("zona_carga", "Zona de carga", "Logística y circulación", "area", 4.00, 3.00, "#8e44ad"),

    # ------------------------------------------------ Formas básicas
    _o("rectangulo", "Rectángulo", "Formas básicas", "rectangulo", 2.00, 1.00, "#34495e"),
    _o("cuadrado", "Cuadrado", "Formas básicas", "rectangulo", 1.00, 1.00, "#34495e"),
    _o("circulo", "Círculo", "Formas básicas", "circulo", 1.00, 1.00, "#34495e"),
    _o("elipse", "Elipse", "Formas básicas", "circulo", 2.00, 1.00, "#34495e"),
    _o("linea", "Línea", "Formas básicas", "linea", 2.00, 0.05, "#2c3e50"),
    _o("flecha_basica", "Flecha", "Formas básicas", "flecha", 1.50, 0.60, "#2c3e50"),
    _o("polilinea", "Polilínea", "Formas básicas", "polilinea", 3.00, 1.50, "#2c3e50"),
    _o("poligono", "Polígono", "Formas básicas", "poligono", 2.00, 2.00, "#34495e"),
    _o("texto", "Texto", "Formas básicas", "texto", 2.00, 0.50, "#2c3e50"),
    _o("etiqueta", "Etiqueta", "Formas básicas", "etiqueta", 2.00, 0.60, "#2c3e50"),
    _o("cota", "Cota", "Formas básicas", "cota", 2.00, 0.40, "#c0392b"),
]


def por_categoria():
    """{categoría: [objetos...]} respetando el orden del catálogo."""
    grupos = {nombre: [] for nombre in CATEGORIAS}
    for objeto in CATALOGO:
        grupos.setdefault(objeto["categoria"], []).append(objeto)
    return grupos


def buscar(texto):
    """Filtra el catálogo por nombre o categoría."""
    texto = (texto or "").strip().lower()
    if not texto:
        return list(CATALOGO)
    return [o for o in CATALOGO
            if texto in o["nombre"].lower() or texto in o["categoria"].lower()]


def obtener(clave):
    for objeto in CATALOGO:
        if objeto["clave"] == clave:
            return dict(objeto)
    return None
