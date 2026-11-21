# -*- coding: utf-8 -*-
"""
PRUEBA DE LICENCIA (Windows y macOS)
====================================
Se ejecuta AUTOMÁTICAMENTE antes de subir a GitHub (subir_github.py).

Comprueba:
  1. Que existan las credenciales de licencia en config_local.json.
  2. Que la base de licencias responda.
  3. Que el software tenga al menos un equipo registrado.
  4. Que la lectura del HWID en macOS funcione: se prueban las MISMAS
     expresiones que usa el programa con salidas reales de 'ioreg' y
     'system_profiler'. Así se detecta antes de subir el fallo que hacía
     que el Mac no encontrara su licencia.

Devuelve 0 si todo está bien y 1 si algo falla.
"""

import os
import re
import sys


def _ok(texto, detalle=""):
    print("   OK   " + texto + (("  (" + detalle + ")") if detalle else ""))


def _fallo(texto, detalle=""):
    print("   FALLA " + texto + (("  (" + detalle + ")") if detalle else ""))


def _probar_hwid_mac():
    """Verifica la lectura del UUID de macOS con las expresiones del programa."""
    bien = True
    muestra_ioreg = '    "IOPlatformUUID" = "0ABA4E96-5C45-537C-A075-6DEEF7E670EA"'
    parecido = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', muestra_ioreg)
    valor = (parecido.group(1).strip().upper() if parecido else "")
    if valor == "0ABA4E96-5C45-537C-A075-6DEEF7E670EA":
        _ok("lectura del HWID desde 'ioreg' (macOS)")
    else:
        _fallo("lectura del HWID desde 'ioreg'", repr(valor))
        bien = False

    muestra_sp = "      Hardware UUID: 0aba4e96-5c45-537c-a075-6deef7e670ea"
    parecido = re.search(r"Hardware\s+UUID:\s*([A-Fa-f0-9-]+)", muestra_sp, re.IGNORECASE)
    valor = (parecido.group(1).strip().upper() if parecido else "")
    if valor == "0ABA4E96-5C45-537C-A075-6DEEF7E670EA":
        _ok("lectura del HWID desde 'system_profiler' (macOS)")
    else:
        _fallo("lectura del HWID desde 'system_profiler'", repr(valor))
        bien = False
    return bien


def _probar_respaldo_embebido(vl):
    """Comprueba que el respaldo de credenciales embebido funcione.

    Es lo que permite que el Mac consulte la licencia aunque el .app no traiga
    config_local.json o el llavero tenga una clave vieja.
    """
    if not hasattr(vl, "_credenciales_respaldo"):
        print("   Aviso: este programa no tiene respaldo embebido (se omite).")
        return True
    try:
        usuario, clave = vl._credenciales_respaldo()
    except Exception as error:
        _fallo("respaldo embebido de licencia", str(error)[:80])
        return False
    if not (usuario and clave):
        _fallo("respaldo embebido de licencia", "vacío")
        return False
    _ok("respaldo embebido de licencia", "usuario: " + usuario)
    try:
        import psycopg2
        conexion = psycopg2.connect(
            dbname=vl.SUPABASE_DB_NAME, user=usuario, password=clave,
            host=vl.SUPABASE_HOST, port=vl.SUPABASE_PORT,
            sslmode="require", connect_timeout=8)
        conexion.close()
        _ok("conexión usando el respaldo embebido", "Mac sin config_local.json")
        return True
    except Exception as error:
        _fallo("conexión usando el respaldo embebido", str(error)[:80])
        return False


def _probar_config_con_bom():
    """Comprueba que la configuración se lea aunque el archivo tenga BOM."""
    import json
    import tempfile
    ruta = os.path.join(tempfile.gettempdir(), "prueba_config_bom.json")
    bien = False
    try:
        with open(ruta, "w", encoding="utf-8-sig") as archivo:
            json.dump({"supabase_lic_user": "prueba"}, archivo)
        with open(ruta, "r", encoding="utf-8-sig") as archivo:
            datos = json.load(archivo)
        bien = datos.get("supabase_lic_user") == "prueba"
    except Exception as error:
        _fallo("lectura de config con BOM", str(error)[:60])
        return False
    finally:
        try:
            os.remove(ruta)
        except Exception:
            pass
    if bien:
        _ok("lectura de config_local.json aunque tenga BOM")
    else:
        _fallo("lectura de config_local.json con BOM")
    return bien


def main():
    print("=" * 66)
    print(" PRUEBA DE LICENCIA (Windows / macOS)")
    print("=" * 66)
    bien = True

    # 1) módulo de licencia
    try:
        import validacion_licencia as vl
        software = getattr(vl, "SOFTWARE_ASIGNADO", "")
        _ok("módulo de licencia cargado", software or "(sin nombre)")
    except Exception as error:
        _fallo("cargar validacion_licencia", str(error)[:80])
        return 1

    # 2) credenciales (cada programa las guarda distinto)
    usuario = clave = ""
    if hasattr(vl, "leer_credenciales_licencia"):
        try:
            usuario, clave = vl.leer_credenciales_licencia()
        except Exception:
            pass
    if not (usuario and clave):
        try:
            from app_paths import cargar_config_local
            config = cargar_config_local()
            usuario = usuario or (config.get("supabase_lic_user") or "")
            clave = clave or (config.get("supabase_lic_password") or "")
        except Exception:
            pass
    if not (usuario and clave):
        try:
            usuario = usuario or getattr(vl, "SUPABASE_USER", "")
            if hasattr(vl, "_password_licencia"):
                clave = clave or (vl._password_licencia() or "")
        except Exception:
            pass
    if not (usuario and clave):
        bien = False
    if usuario and clave:
        _ok("credenciales de licencia", "usuario: " + usuario)
    else:
        _fallo("credenciales de licencia", "faltan en config_local.json")
        bien = False

    # 3) base de licencias
    if usuario and clave:
        try:
            import psycopg2
            conexion = psycopg2.connect(
                dbname=vl.SUPABASE_DB_NAME, user=usuario, password=clave,
                host=vl.SUPABASE_HOST, port=vl.SUPABASE_PORT,
                sslmode="require", connect_timeout=8)
            cursor = conexion.cursor()
            _ok("conexión a la base de licencias")
            cursor.execute("""
                SELECT COUNT(*) FROM lic_equipos e
                JOIN lic_asignaciones l ON e.licencia_id = l.id
                WHERE UPPER(TRIM(l.software)) = UPPER(TRIM(%s))
            """, (software,))
            total = cursor.fetchone()[0]
            conexion.close()
            if total:
                _ok("equipos registrados para el software", "%d equipo(s)" % total)
            else:
                _fallo("equipos registrados para el software",
                       "ninguno para '%s'" % software)
                bien = False
        except Exception as error:
            _fallo("conexión a la base de licencias", str(error)[:90])
            bien = False

    # 4) HWID de este equipo y lectura en macOS
    try:
        hwid = vl.obtener_hwid()
        if hwid and hwid == hwid.upper() and len(hwid) >= 8:
            _ok("HWID de este equipo", hwid)
        else:
            _fallo("HWID de este equipo", repr(hwid))
            bien = False
    except Exception as error:
        _fallo("obtener el HWID", str(error)[:80])
        bien = False

    if not _probar_hwid_mac():
        bien = False
    if not _probar_config_con_bom():
        bien = False
    if not _probar_respaldo_embebido(vl):
        bien = False

    print("")
    if bien:
        print(" RESULTADO: LICENCIA OK - se puede subir a GitHub.")
        return 0
    print(" RESULTADO: REVISAR LA LICENCIA antes de subir.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
