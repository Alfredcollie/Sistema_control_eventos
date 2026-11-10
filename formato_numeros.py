# -*- coding: utf-8 -*-
"""Formato de números según la Configuración General del sistema.

Lee 'formato_numero' del archivo de configuración:
    1,000.00  ->  mil con coma de miles y punto decimal
    1.000,00  ->  mil con punto de miles y coma decimal
Se usa en los módulos para que TODOS muestren los importes igual.
"""
import json
import os

_CACHE = {}


def _config():
    if _CACHE:
        return _CACHE
    config = {"simbolo_moneda": "S/.", "formato_numero": "1,000.00"}
    try:
        from app_paths import CONFIG_FILE
        ruta = str(CONFIG_FILE)
    except Exception:
        ruta = os.path.join(os.path.expanduser("~"), "config.json")
    try:
        if os.path.exists(ruta):
            with open(ruta, "r", encoding="utf-8") as archivo:
                config.update(json.load(archivo) or {})
    except Exception:
        pass
    _CACHE.update(config)
    return _CACHE


def formato_actual():
    """Devuelve el formato configurado ('1,000.00' o '1.000,00')."""
    return str(_config().get("formato_numero", "1,000.00"))


def formatear_numero(valor, decimales=2):
    """Formatea un número con el formato configurado (sin símbolo de moneda)."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        numero = 0.0
    plantilla = "{:,.%df}" % int(decimales)
    texto = plantilla.format(numero)
    if formato_actual() == "1.000,00":
        texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return texto


def formatear_moneda(valor, decimales=2):
    """Igual que formatear_numero() pero con el símbolo de moneda."""
    simbolo = str(_config().get("simbolo_moneda", "S/."))
    return (simbolo + " " + formatear_numero(valor, decimales)).strip()
