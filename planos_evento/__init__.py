# -*- coding: utf-8 -*-
"""CROQUIS / PLANO DE EVENTO — módulo integrado del programa de Eventos.

Arquitectura (sin dependencias nuevas):

    modelo.py      · dataclasses y serialización del proyecto
    bd.py          · tablas y vínculo con cotizaciones (Supabase/psycopg2)
    almacen.py     · proyecto editable en JSON (guardado atómico y versiones)
    pdf_pagina.py  · lectura del PDF con PyMuPDF (páginas, vectorial/escaneado, render)
    geometria.py   · escalas, transformaciones, bbox rotado, snap y colisiones
    biblioteca.py  · catálogo de símbolos 2D para eventos
    editor.py      · lienzo interactivo (Fase 3-4)
    selector.py    · pantalla de eventos y planos (Fase 2)
    modulo_planos.py · integración con el menú de control_general
"""

__version__ = "1.0.0"
