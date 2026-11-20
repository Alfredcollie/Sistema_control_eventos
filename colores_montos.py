# -*- coding: utf-8 -*-
"""Colores y filtro de montos para las tablas del módulo de Banco.

- Monto negativo: texto rojo (#c0392b), fondo rojo muy suave y negrita.
- Monto positivo: color normal (negro), conservando las etiquetas de estado.
- Filtro: todos / solo negativos / solo positivos, sin borrar datos.

Compatible con Windows y macOS (Tk 8.6, Intel y Apple Silicon).
No modifica datos: solo etiquetas y visibilidad del Treeview.
"""
import re

PALABRAS_MONTO = (
    "monto", "saldo", "importe", "total", "egreso", "ingreso", "cargo",
    "abono", "debe", "haber", "debito", "crédito", "credito", "neto",
    "diferencia", "comision", "comisión", "pagado", "pendiente",
)

_TABLAS = []
_FILTRO_MODO = "todos"


def _es_negativo(valor):
    s = str(valor if valor is not None else "").strip()
    if not s or not re.search(r"\d", s):
        return False
    # Quitar el símbolo de moneda delante: "S/. -1.234,56" -> "-1.234,56"
    s = re.sub(r"^(S/\.?|US\$|\$|€|USD|EUR)\s*", "", s, flags=re.I).strip()
    if s.startswith("-") or s.endswith("-"):
        return True
    if s.startswith("(") and s.endswith(")"):
        return True
    return False


def _columnas_monto(tabla):
    columnas = list(tabla["columns"])
    elegidas = []
    for col in columnas:
        try:
            texto = str(tabla.heading(col, "text")).lower()
        except Exception:
            texto = ""
        if any(p in texto for p in PALABRAS_MONTO):
            elegidas.append(col)
    return elegidas


def _colorear_fila(tabla, iid):
    valores = tabla.item(iid, "values")
    columnas = list(tabla["columns"])
    posiciones = {c: i for i, c in enumerate(columnas)}
    negativo = False
    columnas_monto = _columnas_monto(tabla)
    if not columnas_monto:
        return
    for col in columnas_monto:
        i = posiciones.get(col)
        if i is not None and i < len(valores) and _es_negativo(valores[i]):
            negativo = True
            break
    etiquetas = [t for t in (tabla.item(iid, "tags") or ()) if t != "monto_negativo"]
    if negativo:
        etiquetas.append("monto_negativo")
    tabla.item(iid, tags=tuple(etiquetas))


def _tiene_negativo(tabla, iid):
    try:
        return "monto_negativo" in (tabla.item(iid, "tags") or ())
    except Exception:
        return False


def _ocultar(tabla, iid, modo):
    negativo = _tiene_negativo(tabla, iid)
    if modo == "negativos":
        return not negativo
    if modo == "positivos":
        return negativo
    return False


def preparar(tabla):
    """Activa colores/filtro en una tabla y en las filas que se inserten después."""
    try:
        tabla.tag_configure("monto_negativo", foreground="#c0392b",
                            background="#fdecea", font=("Arial", 10, "bold"))
    except Exception:
        try:
            tabla.tag_configure("monto_negativo", foreground="#c0392b",
                                background="#fdecea")
        except Exception:
            pass

    if getattr(tabla, "_colores_montos", False):
        for iid in tabla.get_children(""):
            try:
                _colorear_fila(tabla, iid)
            except Exception:
                pass
        return

    tabla._colores_montos = True
    tabla._filas_todas = []
    tabla._filtro_modo = _FILTRO_MODO
    insert_original = tabla.insert
    delete_original = tabla.delete

    def insert(*args, **kwargs):
        iid = insert_original(*args, **kwargs)
        try:
            tabla._filas_todas.append(iid)
            _colorear_fila(tabla, iid)
            if (_columnas_monto(tabla)
                    and _ocultar(tabla, iid, tabla._filtro_modo)):
                tabla.detach(iid)
        except Exception:
            pass
        return iid

    def delete(*items):
        try:
            if not items:
                items = tuple(tabla.get_children(""))
            visibles = set(tabla.get_children(""))
            if len(items) > 1 and set(items) >= visibles:
                # Se está vaciando la tabla: borrar también las filas ocultas.
                for iid in list(getattr(tabla, "_filas_todas", [])):
                    if iid not in items:
                        try:
                            delete_original(iid)
                        except Exception:
                            pass
                tabla._filas_todas = []
            else:
                for iid in items:
                    try:
                        tabla._filas_todas.remove(iid)
                    except ValueError:
                        pass
        except Exception:
            pass
        return delete_original(*items)

    tabla.insert = insert
    tabla.delete = delete
    _TABLAS.append(tabla)
    if tabla._filtro_modo != "todos":
        filtrar(tabla, tabla._filtro_modo)


def filtrar(tabla, modo="todos"):
    """Muestra todas, solo negativas o solo positivas (sin borrar filas)."""
    modo = str(modo or "todos").lower()
    if modo not in ("todos", "negativos", "positivos"):
        modo = "todos"
    tabla._filtro_modo = modo
    if not _columnas_monto(tabla):
        return
    visibles = set(tabla.get_children(""))
    for indice, iid in enumerate(list(getattr(tabla, "_filas_todas", []))):
        try:
            if _ocultar(tabla, iid, modo):
                if iid in visibles:
                    tabla.detach(iid)
            else:
                if iid in visibles:
                    tabla.move(iid, "", indice)
                else:
                    tabla.reattach(iid, "", indice)
        except Exception:
            pass


def filtrar_todas(modo="todos"):
    """Aplica el filtro a todas las tablas ya preparadas y a las futuras."""
    global _FILTRO_MODO
    modo = str(modo or "todos").lower()
    _FILTRO_MODO = modo if modo in ("todos", "negativos", "positivos") else "todos"
    for tabla in list(_TABLAS):
        try:
            filtrar(tabla, _FILTRO_MODO)
        except Exception:
            pass
