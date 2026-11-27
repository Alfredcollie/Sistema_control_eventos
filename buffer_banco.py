"""BUFFER LOCAL DEL MÓDULO BANCO (SQLite, Windows y macOS M1/M2).

Guarda una copia local de las líneas del estado de cuenta y de los asientos
para que la conciliación no tenga que ir a Supabase en cada repintado.

- Usa solo la biblioteca estándar (`sqlite3`), sin dependencias nativas.
- Vive en la carpeta de datos de cada programa (`app_paths.DATA_DIR`), así que
  Eventos y Flota tienen buffers separados.
- Supabase sigue siendo la fuente oficial: antes de confirmar una conciliación
  se valida contra la base.
"""

import os
import sqlite3
import threading
import time

try:
    from app_paths import DATA_DIR
except Exception:                                     # pragma: no cover
    DATA_DIR = os.path.dirname(os.path.abspath(__file__))

RUTA = os.path.join(str(DATA_DIR), "buffer_banco.sqlite")
_LOCK = threading.RLock()


def _conectar():
    carpeta = os.path.dirname(RUTA)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    con = sqlite3.connect(RUTA, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    con.execute("""CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        banco TEXT NOT NULL DEFAULT '',
        cuenta TEXT NOT NULL DEFAULT '',
        periodo TEXT NOT NULL DEFAULT '',
        fecha TEXT DEFAULT '',
        descripcion TEXT DEFAULT '',
        monto REAL DEFAULT 0,
        saldo REAL,
        referencia TEXT DEFAULT ''
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS asientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        banco TEXT NOT NULL DEFAULT '',
        cuenta TEXT NOT NULL DEFAULT '',
        tabla TEXT DEFAULT '',
        id_movimiento INTEGER DEFAULT 0,
        id_asiento INTEGER DEFAULT 0,
        fecha TEXT DEFAULT '',
        descripcion TEXT DEFAULT '',
        monto REAL DEFAULT 0,
        origen TEXT DEFAULT '',
        estado TEXT DEFAULT '',
        numero_operacion TEXT DEFAULT ''
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS meta (
        clave TEXT PRIMARY KEY,
        valor TEXT DEFAULT ''
    )""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_mov_bc
                   ON movimientos (banco, cuenta, periodo)""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_as_bc
                   ON asientos (banco, cuenta)""")
    return con


def _marca(clave):
    return "%s|%s" % (clave, int(time.time()))


def _vigente(con, clave, max_seg):
    try:
        fila = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
        if not fila:
            return False
        marca = int(str(fila["valor"]).split("|")[-1])
        return (time.time() - marca) <= max(0, int(max_seg))
    except Exception:
        return False


def _poner_marca(con, clave):
    con.execute("""INSERT INTO meta (clave, valor) VALUES (?, ?)
                   ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor""",
                (clave, _marca(clave)))


def guardar_movimientos(banco, cuenta, periodo, filas):
    """Reemplaza el período del banco/cuenta en el buffer local."""
    banco = str(banco or "")
    cuenta = str(cuenta or "")
    periodo = str(periodo or "")
    filas = list(filas or [])
    with _LOCK:
        con = _conectar()
        try:
            with con:
                con.execute("DELETE FROM movimientos WHERE banco=? AND cuenta=? AND periodo=?",
                            (banco, cuenta, periodo))
                con.executemany("""INSERT INTO movimientos
                    (banco, cuenta, periodo, fecha, descripcion, monto, saldo, referencia)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    [(banco, cuenta, periodo,
                      str(f.get("fecha") or ""), str(f.get("descripcion") or ""),
                      float(f.get("monto") or 0),
                      (float(f.get("saldo")) if f.get("saldo") is not None else None),
                      str(f.get("referencia") or ""))
                     for f in filas])
                _poner_marca(con, "mov|%s|%s|%s" % (banco, cuenta, periodo))
        finally:
            con.close()


def leer_movimientos(banco, cuenta, periodo, max_seg=300):
    """Devuelve las líneas del buffer o None si no hay copia vigente."""
    banco = str(banco or "")
    cuenta = str(cuenta or "")
    periodo = str(periodo or "")
    if not banco or not periodo:
        return None
    with _LOCK:
        con = _conectar()
        try:
            if not _vigente(con, "mov|%s|%s|%s" % (banco, cuenta, periodo), max_seg):
                return None
            filas = con.execute("""SELECT fecha, descripcion, monto, saldo
                                   FROM movimientos
                                   WHERE banco=? AND cuenta=? AND periodo=?
                                   ORDER BY id""",
                                (banco, cuenta, periodo)).fetchall()
            if not filas:
                return None
            return [{"fecha": f["fecha"] or "", "descripcion": f["descripcion"] or "",
                     "monto": float(f["monto"] or 0),
                     "saldo": (float(f["saldo"]) if f["saldo"] is not None else None),
                     "periodo": periodo, "banco": banco, "cuenta": cuenta}
                    for f in filas]
        finally:
            con.close()


def guardar_asientos(banco, cuenta, filas):
    """Copia local de los asientos del sistema para el motor."""
    banco = str(banco or "")
    cuenta = str(cuenta or "")
    filas = list(filas or [])
    with _LOCK:
        con = _conectar()
        try:
            with con:
                con.execute("DELETE FROM asientos WHERE banco=? AND cuenta=?", (banco, cuenta))
                con.executemany("""INSERT INTO asientos
                    (banco, cuenta, tabla, id_movimiento, id_asiento, fecha, descripcion,
                     monto, origen, estado, numero_operacion)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    [(banco, cuenta,
                      str(f.get("tabla") or ""), int(f.get("id_movimiento") or 0),
                      int(f.get("id") or f.get("id_asiento") or 0),
                      str(f.get("fecha") or ""), str(f.get("descripcion") or ""),
                      float(f.get("monto") or 0), str(f.get("origen") or ""),
                      str(f.get("estado") or ""), str(f.get("numero_operacion") or ""))
                     for f in filas])
                _poner_marca(con, "asis|%s|%s" % (banco, cuenta))
        finally:
            con.close()


def leer_asientos(banco, cuenta, max_seg=120):
    """Devuelve los asientos del buffer o None si no hay copia vigente."""
    banco = str(banco or "")
    cuenta = str(cuenta or "")
    with _LOCK:
        con = _conectar()
        try:
            if not _vigente(con, "asis|%s|%s" % (banco, cuenta), max_seg):
                return None
            filas = con.execute("""SELECT tabla, id_movimiento, id_asiento, fecha,
                                          descripcion, monto, origen, estado, numero_operacion
                                   FROM asientos WHERE banco=? AND cuenta=? ORDER BY id""",
                                (banco, cuenta)).fetchall()
            if not filas:
                return None
            return [{"tabla": f["tabla"] or "", "id_movimiento": int(f["id_movimiento"] or 0),
                     "id": int(f["id_asiento"] or 0), "fecha": f["fecha"] or "",
                     "descripcion": f["descripcion"] or "", "monto": float(f["monto"] or 0),
                     "origen": f["origen"] or "", "estado": f["estado"] or "",
                     "numero_operacion": f["numero_operacion"] or "",
                     "banco": banco, "cuenta": cuenta}
                    for f in filas]
        finally:
            con.close()


def invalidar(banco=None, cuenta=None):
    """Borra las marcas de vigencia (fuerza a releer de Supabase)."""
    with _LOCK:
        con = _conectar()
        try:
            with con:
                if banco is None:
                    con.execute("DELETE FROM meta")
                elif cuenta is None:
                    con.execute("DELETE FROM meta WHERE clave LIKE ?", ("%%|%s|%%" % banco,))
                else:
                    con.execute("DELETE FROM meta WHERE clave LIKE ?",
                                ("%%|%s|%s|%%" % (banco, cuenta),))
        finally:
            con.close()
