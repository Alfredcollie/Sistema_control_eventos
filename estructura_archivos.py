# -*- coding: utf-8 -*-
"""
ESTRUCTURA_ARCHIVOS.PY - Dónde se guarda cada documento en la carpeta del sistema.

Implementa la estructura acordada (carpeta local sincronizada con Google Drive
vía Rclone). Todos los módulos deben usarla para GUARDAR, en lugar de armar la
carpeta a mano, para que los archivos nuevos caigan siempre en su lugar:

    <carpeta del sistema>/
      Ordenes de compras cliente/<Nombre cliente>/<Mes año>/
      Ordenes de servicio compras/<Placa>/<Mes año>/
      Flota automotriz/<Placa>/<Tarjeta de identificación|Soat|...>/
      Control choferes/<DNI>/<Foto carnet|Brevete|...>/
      Inspección vehicular/<Placa>/<Mes año>/
      Amonestaciones y suspensiones/<Mes año>/<DNI>/Amonestaciones|Suspensiones/
      Cálculos de cobranza/<Mes año>/<RUC>/<1ra quincena|2da quincena>/
      Ventas/<Mes año>/<RUC cliente>/Facturas|Comprobantes de pago/
      Compras/<Mes año>/<RUC proveedor>/Facturas|Comprobantes de pago/
      Cálculo de impuesto/<Mes año>/
      Nómina/<Mes año>/
      Banco/<Mes año>/<Cuenta>/Transferencias/

Uso típico:

    from estructura_archivos import ruta_documento
    ruta = ruta_documento("compra_factura", nombre_archivo,
                          fecha=fecha, ruc=ruc, nombre=proveedor,
                          ruta_base=ruta_base)
    # -> C:\\...\\BlackRiders_Archivos\\Compras\\Octubre 2026\\20123456789 - PROVEEDOR\\Facturas\\archivo.pdf
"""
import os
import re
from datetime import datetime

MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
         "Septiembre", "Octubre", "Noviembre", "Diciembre"]

_NO_VALIDOS = re.compile(r'[\\/:*?"<>|]')

# Documentos del vehículo (clave interna -> carpeta)
DOCS_FLOTA = {
    "soat": "Soat",
    "rt": "Revisión técnica",
    "gas": "Revisión técnica Gas",
    "extintor": "Certificado de extintor",
    "tarjeta": "Tarjeta de identificación",
}

# Documentos del chofer (clave interna -> carpeta)
DOCS_CHOFER = {
    "dni": "DNI",
    "brevete": "Brevete",
    "foto_carnet": "Foto carnet",
    "antecedentes_penales": "Antecedentes penales",
    "antecedentes_policiales": "Antecedentes policiales",
    "carnet_sanidad": "Carné de sanidad",
    "contrato": "Contrato",
}


# --------------------------------------------------------------------- fechas
def a_fecha(valor):
    """Convierte 'dd/mm/aaaa', 'aaaa-mm-dd' o un datetime en datetime."""
    if isinstance(valor, datetime):
        return valor
    texto = str(valor or "").strip()
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(texto[:10], formato)
        except ValueError:
            continue
    return None


def mes_anio(fecha, defecto=None):
    """datetime -> 'Octubre 2026'. Sin fecha usable devuelve [defecto]."""
    dt = a_fecha(fecha) or defecto
    if not dt:
        return "Sin fecha"
    return "%s %d" % (MESES[dt.month - 1], dt.year)


# ------------------------------------------------------------------ limpieza
def sanitizar(texto, limite=60):
    """Quita caracteres inválidos de Windows y recorta el nombre."""
    limpio = _NO_VALIDOS.sub("_", str(texto or "").strip())
    limpio = re.sub(r"\s+", " ", limpio).strip(" .")
    return limpio[:limite].strip(" .") or "SIN DATO"


def ruc_o_nombre(ruc, nombre, limite=70):
    """'20123456789 - PROVEEDOR' (o solo el nombre si no hay RUC)."""
    ruc = str(ruc or "").strip()
    if ruc and ruc.upper() not in ("NONE", "0"):
        return sanitizar("%s - %s" % (ruc, nombre), limite) if nombre else sanitizar(ruc, limite)
    return sanitizar(nombre or "SIN RUC", limite)


# RUC por nombre (se consulta una vez por sesión y se recuerda)
_RUC_CACHE = {}


def ruc_de(tabla, nombre):
    """Busca el RUC de un cliente/proveedor por su nombre ('' si no hay).

    Se usa al guardar, para que la carpeta sea la misma que ya usa la
    estructura: 'RUC - NOMBRE'.
    """
    nombre = str(nombre or "").strip()
    if not nombre:
        return ""
    clave = (tabla, nombre.lower())
    if clave in _RUC_CACHE:
        return _RUC_CACHE[clave]
    ruc = ""
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                cur = conn.cursor()
                if tabla == "proveedores":
                    cur.execute("SELECT ruc FROM proveedores "
                                "WHERE lower(trim(nombre)) = %s AND COALESCE(ruc,'') <> ''",
                                (nombre.lower(),))
                else:
                    cur.execute("SELECT ruc FROM clientes "
                                "WHERE lower(trim(nombre_empresa)) = %s AND COALESCE(ruc,'') <> ''",
                                (nombre.lower(),))
                fila = cur.fetchone()
                ruc = str(fila[0]).strip() if fila and fila[0] else ""
            finally:
                liberar_conexion(conn)
    except Exception:
        ruc = ""
    _RUC_CACHE[clave] = ruc
    return ruc


# ------------------------------------------------------------------ carpetas
def carpeta(tipo, fecha=None, ruc=None, nombre=None, placa=None, dni=None,
            documento=None, quincena=None, cuenta=None):
    """Devuelve la carpeta (relativa) donde va el documento según su tipo."""
    tipo = (tipo or "").strip().lower()
    mes = mes_anio(fecha)

    # Si no llega el RUC, se busca por el nombre (así la carpeta queda igual
    # que la que ya usa la estructura: "RUC - NOMBRE").
    if not ruc and nombre:
        if tipo in ("compra_factura", "factura_recibida", "compra_comprobante",
                    "pago_comprobante", "comprobante_egreso", "soporte_pago_tercero",
                    "compra_orden"):
            ruc = ruc_de("proveedores", nombre)
        elif tipo in ("venta_factura", "factura_emitida", "venta_comprobante",
                      "pago_cliente", "comprobante_ingreso", "cotizacion"):
            ruc = ruc_de("clientes", nombre)

    if tipo in ("compra_factura", "factura_recibida"):
        return "Compras/%s/%s/Facturas" % (mes, ruc_o_nombre(ruc, nombre))
    if tipo in ("compra_comprobante", "pago_comprobante", "comprobante_egreso",
                "soporte_pago_tercero"):
        return "Compras/%s/%s/Comprobantes de pago" % (mes, ruc_o_nombre(ruc, nombre))
    if tipo in ("compra_orden",):
        return "Compras/%s/%s/Órdenes de compra" % (mes, ruc_o_nombre(ruc, nombre))

    if tipo in ("venta_factura", "factura_emitida"):
        return "Ventas/%s/%s/Facturas" % (mes, ruc_o_nombre(ruc, nombre))
    if tipo in ("venta_comprobante", "pago_cliente", "comprobante_ingreso"):
        return "Ventas/%s/%s/Comprobantes de pago" % (mes, ruc_o_nombre(ruc, nombre))
    if tipo in ("cotizacion",):
        return "Ventas/%s/%s/Cotizaciones" % (mes, ruc_o_nombre(ruc, nombre))

    if tipo in ("flota", "vehiculo"):
        doc = DOCS_FLOTA.get(str(documento or "").strip().lower(), sanitizar(documento or "Documento"))
        return "Flota automotriz/%s/%s" % (sanitizar(placa, 20), doc)

    if tipo in ("chofer", "expediente_chofer"):
        doc = DOCS_CHOFER.get(str(documento or "").strip().lower(), sanitizar(documento or "Documento"))
        return "Control choferes/%s/%s" % (sanitizar(dni, 20), doc)

    if tipo in ("inspeccion", "inspeccion_vehicular"):
        return "Inspección vehicular/%s/%s" % (sanitizar(placa, 20), mes)

    if tipo in ("orden_servicio", "orden_servicio_flota"):
        return "Órdenes de servicio compras/%s/%s" % (sanitizar(placa, 20), mes)

    if tipo in ("orden_compra_cliente",):
        return "Ordenes de compras cliente/%s/%s" % (sanitizar(nombre, 60), mes)

    if tipo in ("amonestacion", "suspension", "amonestaciones"):
        sub = "Suspensiones" if "suspension" in tipo else "Amonestaciones"
        return "Amonestaciones y suspensiones/%s/%s/%s" % (mes, sanitizar(dni, 20), sub)

    if tipo in ("cobranza",):
        sub = "1ra quincena" if str(quincena) == "1" else "2da quincena"
        return "Cálculos de cobranza/%s/%s/%s" % (mes, ruc_o_nombre(ruc, nombre), sub)

    if tipo in ("banco", "transferencia"):
        return "Banco/%s/%s/Transferencias" % (mes, sanitizar(cuenta or "Cuenta", 40))

    if tipo in ("impuesto", "calculo_impuesto"):
        return "Cálculo de impuesto/%s" % mes

    if tipo in ("nomina",):
        return "Nómina/%s" % mes

    return "_sin_clasificar/Otros"


def ruta_documento(tipo, nombre_archivo, ruta_base="", **datos):
    """Ruta ABSOLUTA (y carpeta creada) donde guardar el archivo.

    `nombre_archivo` se conserva; si ya existe uno con el mismo nombre pero
    distinto contenido, se agrega " (2)", " (3)"... como hace Windows.
    """
    from app_paths import ruta_drive_configurada

    base = str(ruta_base or "").strip() or ruta_drive_configurada()
    if not base:
        return ""
    destino = os.path.join(base, *carpeta(tipo, **datos).split("/"))
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception:
        return ""
    nombre = sanitizar(os.path.basename(str(nombre_archivo)), 150)
    ruta = os.path.join(destino, nombre)
    contador = 2
    while os.path.exists(ruta):
        raiz, ext = os.path.splitext(nombre)
        ruta = os.path.join(destino, "%s (%d)%s" % (raiz, contador, ext))
        contador += 1
    return ruta


# ---------------------------------------------------------------------------
# Para los documentos que se adjuntan a un registro que YA existe (por ejemplo
# cambiar el PDF de una factura ya guardada): los datos (fecha, cliente, RUC...)
# se leen de la base, así la carpeta siempre coincide con la estructura.
# ---------------------------------------------------------------------------
_CONSULTAS = {
    "facturas_recibidas": ("SELECT fecha, ruc, proveedor FROM facturas_recibidas WHERE id = %s",
                           ("fecha", "ruc", "nombre")),
    "facturas_emitidas": ("SELECT fecha, cliente FROM facturas_emitidas WHERE id = %s",
                          ("fecha", "nombre")),
    "pagos_comprobantes": ("SELECT fecha_pago, proveedor_nombre FROM pagos_comprobantes WHERE id = %s",
                           ("fecha", "nombre")),
    "pagos_clientes": ("SELECT fecha_pago, cliente_nombre FROM pagos_clientes WHERE id = %s",
                       ("fecha", "nombre")),
    "ordenes_servicio_flota": ("SELECT fecha_emision, placa FROM ordenes_servicio_flota WHERE id = %s",
                               ("fecha", "placa")),
    "ordenes_compra": ("SELECT fecha_emision, proveedor FROM ordenes_compra WHERE id = %s",
                       ("fecha", "nombre")),
    "cobranza_quincenas": ("SELECT anio, mes, cliente_ruc, cliente_nombre, quincena "
                           "FROM cobranza_quincenas WHERE id = %s",
                           ("anio", "mes", "ruc", "nombre", "quincena")),
    "amonestaciones": ("SELECT fecha, dni FROM amonestaciones WHERE id = %s", ("fecha", "dni")),
    "transferencias_bancarias": ("SELECT fecha, cuenta_origen FROM transferencias_bancarias WHERE id = %s",
                                 ("fecha", "cuenta")),
    "flota_vehiculos": ("SELECT placa FROM flota_vehiculos WHERE placa = %s", ("placa",)),
    "choferes": ("SELECT dni FROM choferes WHERE dni = %s", ("dni",)),
    "registro_impuestos": ("SELECT periodo FROM registro_impuestos WHERE id = %s", ("fecha",)),
}


def datos_de_registro(tabla, id_registro):
    """Lee de la base los datos que definen la carpeta de un registro existente."""
    consulta = _CONSULTAS.get(tabla)
    if not consulta:
        return {}
    sql, campos = consulta
    datos = {}
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if not conn:
            return {}
        try:
            cur = conn.cursor()
            cur.execute(sql, (id_registro,))
            fila = cur.fetchone()
        finally:
            liberar_conexion(conn)
        if not fila:
            return {}
        for campo, valor in zip(campos, fila):
            datos[campo] = valor
    except Exception:
        return {}
    # cobranza: 'mes' es un número (1-12) y hay que convertirlo a fecha
    if tabla == "cobranza_quincenas" and datos.get("anio") and datos.get("mes"):
        try:
            datos["fecha"] = datetime(int(datos["anio"]), int(datos["mes"]), 15)
        except Exception:
            pass
    return datos


def ruta_para_registro(tipo, nombre_archivo, tabla, id_registro, ruta_base="", **extra):
    """Como ruta_documento, pero tomando fecha/cliente/RUC/placa de la base."""
    datos = datos_de_registro(tabla, id_registro)
    datos.update({k: v for k, v in extra.items() if v not in (None, "")})
    validos = {k: v for k, v in datos.items()
               if k in ("fecha", "ruc", "nombre", "placa", "dni", "documento", "quincena", "cuenta")}
    return ruta_documento(tipo, nombre_archivo, ruta_base=ruta_base, **validos)
