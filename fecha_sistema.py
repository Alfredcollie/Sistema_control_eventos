# -*- coding: utf-8 -*-
"""
=========================================================
FECHA_SISTEMA.PY
=========================================================
Fecha de Comienzo del Sistema (corte contable):
- Guarda/lee la fecha de inicio configurada en "Configuración General".
- Elimina compras y ventas anteriores a esa fecha.
- Impide volver a cargar (SUNAT/SIRE o manual) movimientos anteriores a esa fecha.
"""

from datetime import datetime, date

from conexion import conectar_db, liberar_conexion, registrar_auditoria

# Clave con la que se guarda la fecha en configuracion.json / nube.
CLAVE_FECHA_COMIENZO = "fecha_comienzo_sistema"

# Tablas de compras y ventas que se purgan y se protegen.
# (tabla, columna_fecha, grupo)
TABLAS_MOVIMIENTOS = (
    ("facturas_recibidas", "fecha", "Compras"),
    ("pagos_comprobantes", "fecha_pago", "Compras"),
    ("facturas_emitidas", "fecha", "Ventas"),
    ("pagos_clientes", "fecha_pago", "Ventas"),
)

_FORMATOS_BASE = ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y")


def _config_segura(config=None):
    """Devuelve el diccionario de configuración local del equipo."""
    if isinstance(config, dict):
        return config
    try:
        from app_paths import cargar_config_local
        return cargar_config_local() or {}
    except Exception:
        return {}


def parsear_fecha(valor, formato_preferido=None):
    """Convierte texto/fecha a datetime.date. Devuelve None si no se puede interpretar.

    Acepta DD/MM/YYYY, MM/DD/YYYY, YYYY-MM-DD y variantes con guiones.
    Cuando el texto es ambiguo (ej: 04/03/2026) manda el formato configurado por el usuario.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    if not texto:
        return None
    texto = texto.replace("T", " ").split(" ")[0].strip()

    if str(formato_preferido or "").strip().upper().startswith("MM"):
        orden = ("%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y")
    else:
        orden = ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y")
    formatos = _FORMATOS_BASE[:2] + orden
    for fmt in formatos:
        try:
            return datetime.strptime(texto, fmt).date()
        except Exception:
            continue
    return None


def obtener_fecha_comienzo(config=None):
    """Fecha de comienzo configurada (datetime.date) o None si no hay corte definido."""
    cfg = _config_segura(config)
    return parsear_fecha(cfg.get(CLAVE_FECHA_COMIENZO), cfg.get("formato_fecha"))


def texto_fecha_comienzo(config=None, defecto="Sin definir"):
    f = obtener_fecha_comienzo(config)
    return f.strftime("%d/%m/%Y") if f else defecto


def periodo_minimo_sire(config=None):
    """Periodo (YYYYMM) desde el cual se permite descargar de SUNAT. None = sin restricción."""
    f = obtener_fecha_comienzo(config)
    return f.strftime("%Y%m") if f else None


def sugerir_periodo_sire(config=None):
    """Periodo sugerido para el diálogo de descarga: nunca anterior al corte del sistema."""
    actual = datetime.now().strftime("%Y%m")
    minimo = periodo_minimo_sire(config)
    if minimo and minimo > actual:
        return minimo
    return actual


def validar_periodo_sire(periodo, config=None):
    """(permitido, mensaje). Bloquea periodos anteriores a la fecha de comienzo."""
    minimo = periodo_minimo_sire(config)
    if minimo and str(periodo) < minimo:
        return False, (
            f"Descarga bloqueada por la Fecha de Comienzo del Sistema.\n\n"
            f"Fecha de comienzo configurada: {texto_fecha_comienzo(config)}\n"
            f"Periodo mínimo permitido: {minimo}\n\n"
            f"Solo se pueden importar comprobantes desde el periodo {minimo} en adelante.\n"
            f"Si necesitas periodos anteriores, cambia la Fecha de Comienzo en Configuración General."
        )
    return True, ""


def validar_fecha_registro(fecha_texto, config=None):
    """(permitido, mensaje). Evita registrar compras/ventas anteriores al corte."""
    cfg = _config_segura(config)
    corte = obtener_fecha_comienzo(cfg)
    if corte is None:
        return True, ""
    f = parsear_fecha(fecha_texto, cfg.get("formato_fecha"))
    if f is None:
        return True, ""
    if f < corte:
        return False, (
            f"La fecha {f.strftime('%d/%m/%Y')} es anterior a la Fecha de Comienzo del Sistema "
            f"({corte.strftime('%d/%m/%Y')}).\n\n"
            f"El sistema solo registra movimientos desde esa fecha en adelante. "
            f"Si es necesario, actualiza la Fecha de Comienzo en Configuración General."
        )
    return True, ""


def _ids_anteriores(cursor, tabla, columna, corte, formato_preferido=None):
    """Devuelve la lista de IDs de la tabla cuya fecha es anterior al corte."""
    cursor.execute(f"SELECT id, {columna} FROM {tabla}")
    ids = []
    for fila in cursor.fetchall():
        f = parsear_fecha(fila[1], formato_preferido)
        if f is not None and f < corte:
            ids.append(fila[0])
    return ids


def previsualizar_purga(fecha_corte, config=None):
    """Cuenta cuántos registros de compras y ventas se eliminarían. {tabla: n}."""
    if fecha_corte is None:
        return {}
    cfg = _config_segura(config)
    formato = cfg.get("formato_fecha")
    conn = conectar_db(silencioso=True)
    if not conn:
        raise RuntimeError("Sin conexión a la base de datos.")
    resultado = {}
    try:
        cursor = conn.cursor()
        for tabla, columna, _grupo in TABLAS_MOVIMIENTOS:
            try:
                resultado[tabla] = len(_ids_anteriores(cursor, tabla, columna, fecha_corte, formato))
            except Exception:
                conn.rollback()
                resultado[tabla] = 0
    finally:
        liberar_conexion(conn)
    return resultado


def purgar_anteriores(fecha_corte, config=None, usuario=None):
    """Elimina de la base de datos todas las compras y ventas anteriores al corte.

    Devuelve {tabla: registros_eliminados}. Lanza excepción si no hay conexión.
    """
    if fecha_corte is None:
        return {}
    cfg = _config_segura(config)
    formato = cfg.get("formato_fecha")
    conn = conectar_db()
    if not conn:
        raise RuntimeError("Sin conexión a la base de datos (Modo Lectura).")
    resultado = {}
    try:
        cursor = conn.cursor()
        for tabla, columna, _grupo in TABLAS_MOVIMIENTOS:
            try:
                ids = _ids_anteriores(cursor, tabla, columna, fecha_corte, formato)
            except Exception:
                conn.rollback()
                resultado[tabla] = 0
                continue
            borrados = 0
            for i in range(0, len(ids), 400):
                lote = ids[i:i + 400]
                placeholders = ",".join(["%s"] * len(lote))
                try:
                    cursor.execute(f"DELETE FROM {tabla} WHERE id IN ({placeholders})", tuple(lote))
                    conn.commit()
                    borrados += len(lote)
                except Exception:
                    conn.rollback()
            resultado[tabla] = borrados
    finally:
        liberar_conexion(conn)

    if usuario and any(resultado.values()):
        try:
            registrar_auditoria(
                usuario, "Configuración General",
                f"Purgó compras y ventas anteriores al {fecha_corte.strftime('%d/%m/%Y')}: {resumen_purga(resultado, corto=True)}"
            )
        except Exception:
            pass
    return resultado


def resumen_purga(resultado, corto=False):
    """Texto legible del resultado de una purga."""
    if not resultado:
        return "No se eliminó ningún registro."
    grupos = {"Compras": 0, "Ventas": 0}
    detalle = []
    for tabla, _columna, grupo in TABLAS_MOVIMIENTOS:
        n = int(resultado.get(tabla, 0) or 0)
        grupos[grupo] = grupos.get(grupo, 0) + n
        if n and not corto:
            detalle.append(f"• {tabla}: {n:,}")
    if corto:
        return f"{grupos.get('Compras', 0)} de compras y {grupos.get('Ventas', 0)} de ventas"
    total = grupos.get("Compras", 0) + grupos.get("Ventas", 0)
    lineas = [f"Total eliminado: {total:,} registros", f"• Compras: {grupos.get('Compras', 0):,}", f"• Ventas: {grupos.get('Ventas', 0):,}"]
    if detalle:
        lineas.append("")
        lineas.extend(detalle)
    return "\n".join(lineas)
