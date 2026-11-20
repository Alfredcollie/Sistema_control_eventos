# -*- coding: utf-8 -*-
"""
TABLA_UNIVERSAL - Búsqueda y orden por CUALQUIER columna, sobre TODOS los
registros cargados en un ttk.Treeview (no solo la página visible).

- La tabla debe cargarse completa (sin LIMIT por página) antes de usar esto.
- No consulta la base de datos ni modifica datos: solo oculta/ordena filas.
- Los importes se comparan como números (S/. 1.234,56 o 1,234.56) y las fechas
  dd/mm/aaaa como fechas; el resto como texto sin tildes.
"""
import re
import unicodedata


def _norm(texto):
    s = unicodedata.normalize("NFKD", str(texto if texto is not None else ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def _numero(texto):
    s = _norm(texto)
    if not s or not re.search(r"\d", s):
        return None
    limpio = re.sub(r"[^0-9,.-]", "", s)
    if not limpio:
        return None
    if "," in limpio and "." in limpio:
        if limpio.rfind(",") > limpio.rfind("."):
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    elif "," in limpio:
        partes = limpio.split(",")
        if len(partes) == 2 and 1 <= len(partes[1]) <= 2:
            limpio = limpio.replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    try:
        return float(limpio)
    except ValueError:
        return None


def _fecha(texto):
    s = str(texto or "").strip()
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
    if m:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _clave(valor):
    """Clave de orden: fechas primero, luego números, luego texto."""
    f = _fecha(valor)
    if f is not None:
        return (0, f, "")
    n = _numero(valor)
    if n is not None:
        return (1, n, "")
    return (2, 0.0, _norm(valor))


def _valores(tabla, iid):
    cache = getattr(tabla, "_universal_valores", None)
    if cache is not None and iid in cache:
        return cache[iid]
    try:
        return tabla.item(iid, "values")
    except Exception:
        return ()


def limpiar(tabla):
    """Reincorpora las filas ocultas para que el borrado/repintado las elimine."""
    for iid in list(getattr(tabla, "_universal_todos", []) or []):
        try:
            tabla.reattach(iid, "", "end")
        except Exception:
            pass
    tabla._universal_todos = []
    tabla._universal_valores = {}


def refrescar(tabla):
    """Captura todas las filas (después de cargar/repintar) y reaplica filtro/orden."""
    try:
        tabla._universal_todos = list(tabla.get_children(""))
        tabla._universal_valores = {iid: tabla.item(iid, "values")
                                    for iid in tabla._universal_todos}
    except Exception:
        tabla._universal_todos = []
        tabla._universal_valores = {}
    aplicar(tabla)


def filtrar(tabla, texto=None, columna=None, ascendente=None):
    if texto is not None:
        tabla._universal_texto = str(texto or "")
    if columna is not None:
        if getattr(tabla, "_universal_col", None) == columna:
            tabla._universal_asc = not bool(getattr(tabla, "_universal_asc", True))
        else:
            tabla._universal_col = columna
            tabla._universal_asc = True
        if ascendente is not None:
            tabla._universal_asc = bool(ascendente)
    aplicar(tabla)


def aplicar(tabla):
    todos = list(getattr(tabla, "_universal_todos", []))
    if not todos:
        return
    texto = _norm(getattr(tabla, "_universal_texto", ""))
    visibles = []
    for iid in todos:
        vals = _valores(tabla, iid)
        if not texto or any(texto in _norm(v) for v in vals):
            visibles.append(iid)

    col = getattr(tabla, "_universal_col", None)
    if col:
        asc = bool(getattr(tabla, "_universal_asc", True))
        todos.sort(key=lambda i: _clave(_valor_columna(tabla, i, col)), reverse=not asc)
        visibles.sort(key=lambda i: _clave(_valor_columna(tabla, i, col)), reverse=not asc)
        tabla._universal_todos = todos

    # Reubicar: primero se reincorporan las visibles en orden y se ocultan las demás.
    for iid in todos:
        try:
            if iid in visibles:
                tabla.reattach(iid, "", "end")
            else:
                tabla.detach(iid)
        except Exception:
            pass
    for indice, iid in enumerate(visibles):
        try:
            tabla.move(iid, "", indice)
        except Exception:
            pass

    # Renumerar la columna "num" si existe (1, 2, 3...)
    try:
        if "num" in tabla["columns"]:
            for indice, iid in enumerate(visibles, start=1):
                tabla.set(iid, "num", indice)
    except Exception:
        pass


def _valor_columna(tabla, iid, columna):
    vals = _valores(tabla, iid)
    try:
        pos = list(tabla["columns"]).index(columna)
    except ValueError:
        return ""
    return vals[pos] if pos < len(vals) else ""


def conectar_busqueda(tabla, entrada):
    """El buscador filtra en memoria (todas las filas), sin recargar la BD."""
    def _aplicar(event=None):
        try:
            filtrar(tabla, texto=entrada.get())
        except Exception:
            pass
    try:
        entrada.bind("<KeyRelease>", _aplicar)
        entrada.bind("<Return>", _aplicar)
    except Exception:
        pass
    return _aplicar


def conectar_orden(tabla):
    """Clic en CUALQUIER título: ordena todas las filas por esa columna."""
    for col in tabla["columns"]:
        try:
            tabla.heading(col, command=lambda c=col: filtrar(tabla, columna=c))
        except Exception:
            pass
