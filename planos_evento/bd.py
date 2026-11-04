# -*- coding: utf-8 -*-
"""Tablas y consultas del módulo de planos.

No toca ninguna tabla existente: sólo crea las suyas y LEE de 'cotizaciones'.
"""
from datetime import datetime

from conexion import conectar_db, liberar_conexion

ESTADOS_PERMITIDOS = ("Aprobada", "En desarrollo", "En evaluación")
TABLAS_LISTAS = [False]


def _cur(conn):
    return conn.cursor()


def asegurar_tablas():
    """Crea las tablas del módulo si faltan (idempotente, una vez por proceso)."""
    if TABLAS_LISTAS[0]:
        return True
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS planos_evento (
                    id VARCHAR(40) PRIMARY KEY,
                    cotizacion_id INTEGER NOT NULL,
                    nombre VARCHAR(180) DEFAULT '',
                    descripcion TEXT DEFAULT '',
                    pdf_ruta TEXT DEFAULT '',
                    pagina INTEGER DEFAULT 0,
                    unidad VARCHAR(4) DEFAULT 'm',
                    version INTEGER DEFAULT 1,
                    creacion VARCHAR(30) DEFAULT '',
                    modificacion VARCHAR(30) DEFAULT '',
                    usuario VARCHAR(120) DEFAULT ''
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS planos_paginas (
                    id SERIAL PRIMARY KEY,
                    plano_id VARCHAR(40) NOT NULL,
                    numero INTEGER DEFAULT 1,
                    ancho_pt NUMERIC DEFAULT 0,
                    alto_pt NUMERIC DEFAULT 0,
                    px_por_metro NUMERIC DEFAULT 0,
                    calibrada BOOLEAN DEFAULT FALSE
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS planos_versiones (
                    id SERIAL PRIMARY KEY,
                    plano_id VARCHAR(40) NOT NULL,
                    version INTEGER DEFAULT 1,
                    fecha VARCHAR(30) DEFAULT '',
                    usuario VARCHAR(120) DEFAULT '',
                    archivo TEXT DEFAULT '',
                    nota TEXT DEFAULT ''
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS biblioteca_objetos (
                    id SERIAL PRIMARY KEY,
                    nombre VARCHAR(120) NOT NULL,
                    categoria VARCHAR(60) DEFAULT '',
                    tipo VARCHAR(40) DEFAULT '',
                    archivo TEXT DEFAULT '',
                    ancho NUMERIC DEFAULT 1,
                    alto NUMERIC DEFAULT 1,
                    color VARCHAR(20) DEFAULT '#1f538d',
                    descripcion TEXT DEFAULT ''
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_planos_cotizacion "
                      "ON planos_evento (cotizacion_id)")
        conn.commit()
        TABLAS_LISTAS[0] = True
        return True
    except Exception as e:
        print("[Planos] no se pudieron crear las tablas:", e)
        return False
    finally:
        liberar_conexion(conn)


def cotizaciones_disponibles(estados=ESTADOS_PERMITIDOS):
    """Eventos del módulo de Cotización que permiten trabajar con planos."""
    asegurar_tablas()
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, COALESCE(codigo_cotizacion,''), COALESCE(nombre_empresa,''),
                       COALESCE(nombre_evento,''), COALESCE(fecha_evento,''),
                       COALESCE(locacion_evento,''), COALESCE(status,'')
                FROM cotizaciones
                WHERE COALESCE(status,'') = ANY(%s)
                ORDER BY fecha_evento DESC, id DESC
            """, (list(estados),))
            return [{"id": r[0], "codigo": r[1], "cliente": r[2], "evento": r[3],
                     "fecha": r[4], "locacion": r[5], "estado": r[6]}
                    for r in c.fetchall()]
    except Exception as e:
        print("[Planos] cotizaciones:", e)
        return []
    finally:
        liberar_conexion(conn)


def cotizacion_por_id(id_cotizacion):
    """Datos del evento (para la barra de estado y el título del editor)."""
    asegurar_tablas()
    conn = conectar_db(silencioso=True)
    if not conn:
        return None
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, COALESCE(codigo_cotizacion,''), COALESCE(nombre_empresa,''),
                       COALESCE(nombre_evento,''), COALESCE(fecha_evento,''),
                       COALESCE(locacion_evento,''), COALESCE(status,'')
                FROM cotizaciones WHERE id = %s
            """, (int(id_cotizacion),))
            r = c.fetchone()
            if not r:
                return None
            return {"id": r[0], "codigo": r[1], "cliente": r[2], "evento": r[3],
                    "fecha": r[4], "locacion": r[5], "estado": r[6]}
    except Exception as e:
        print("[Planos] cotizacion_por_id:", e)
        return None
    finally:
        liberar_conexion(conn)


def puede_editar(estado):
    """¿El estado del evento permite crear/modificar planos?"""
    return str(estado or "") in ESTADOS_PERMITIDOS


def listar_planos(id_cotizacion):
    asegurar_tablas()
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT id, nombre, COALESCE(descripcion,''), COALESCE(pdf_ruta,''),
                       COALESCE(pagina,0), COALESCE(unidad,'m'), COALESCE(version,1),
                       COALESCE(creacion,''), COALESCE(modificacion,''), COALESCE(usuario,'')
                FROM planos_evento WHERE cotizacion_id = %s
                ORDER BY modificacion DESC, nombre
            """, (int(id_cotizacion),))
            return [{"id": r[0], "nombre": r[1], "descripcion": r[2], "pdf_ruta": r[3],
                     "pagina": r[4], "unidad": r[5], "version": r[6],
                     "creacion": r[7], "modificacion": r[8], "usuario": r[9]}
                    for r in c.fetchall()]
    except Exception as e:
        print("[Planos] listar_planos:", e)
        return []
    finally:
        liberar_conexion(conn)


def insertar_plano(plano):
    asegurar_tablas()
    conn = conectar_db()
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO planos_evento
                (id, cotizacion_id, nombre, descripcion, pdf_ruta, pagina, unidad,
                 version, creacion, modificacion, usuario)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (plano.id, int(plano.cotizacion_id), plano.nombre, plano.descripcion,
                  plano.pdf_ruta, int(plano.pagina), plano.unidad, int(plano.version),
                  plano.creacion or datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                  plano.modificacion or "", plano.usuario))
        conn.commit()
        return True
    except Exception as e:
        print("[Planos] insertar:", e)
        return False
    finally:
        liberar_conexion(conn)


def actualizar_plano(plano):
    asegurar_tablas()
    conn = conectar_db()
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                UPDATE planos_evento
                   SET nombre=%s, descripcion=%s, pdf_ruta=%s, pagina=%s, unidad=%s,
                       version=%s, modificacion=%s, usuario=%s
                 WHERE id=%s
            """, (plano.nombre, plano.descripcion, plano.pdf_ruta, int(plano.pagina),
                  plano.unidad, int(plano.version), plano.modificacion, plano.usuario,
                  plano.id))
        conn.commit()
        return True
    except Exception as e:
        print("[Planos] actualizar:", e)
        return False
    finally:
        liberar_conexion(conn)


def borrar_plano(id_plano):
    asegurar_tablas()
    conn = conectar_db()
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("DELETE FROM planos_evento WHERE id=%s", (id_plano,))
            c.execute("DELETE FROM planos_paginas WHERE plano_id=%s", (id_plano,))
            c.execute("DELETE FROM planos_versiones WHERE plano_id=%s", (id_plano,))
        conn.commit()
        return True
    except Exception as e:
        print("[Planos] borrar:", e)
        return False
    finally:
        liberar_conexion(conn)


def registrar_version(plano_id, version, archivo, usuario="", nota=""):
    asegurar_tablas()
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                INSERT INTO planos_versiones (plano_id, version, fecha, usuario, archivo, nota)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (plano_id, int(version), datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                  usuario, archivo, nota))
        conn.commit()
        return True
    except Exception as e:
        print("[Planos] registrar_version:", e)
        return False
    finally:
        liberar_conexion(conn)


def listar_versiones_bd(plano_id):
    asegurar_tablas()
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("""SELECT version, fecha, COALESCE(usuario,''), COALESCE(archivo,''),
                                COALESCE(nota,'')
                         FROM planos_versiones WHERE plano_id=%s ORDER BY version DESC""",
                      (plano_id,))
            return [{"version": r[0], "fecha": r[1], "usuario": r[2],
                     "archivo": r[3], "nota": r[4]} for r in c.fetchall()]
    except Exception as e:
        print("[Planos] listar_versiones_bd:", e)
        return []
    finally:
        liberar_conexion(conn)
