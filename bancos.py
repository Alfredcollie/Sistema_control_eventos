# -*- coding: utf-8 -*-
"""
BANCOS.PY - CUENTAS BANCARIAS DEL SISTEMA DE CONTROL DE EVENTOS
==============================================================
Punto ÚNICO de las cuentas bancarias del sistema. Lo usan:

  * control_general.py  -> Configuración General -> "🏦 Cuentas Bancarias" (las edita)
  * modulo_banco.py     -> Módulo de Bancos (saldos y conciliación bancaria)
  * modulo_ventas.py    -> al registrar un cobro se elige la cuenta donde entró el dinero
  * modulo_compras.py   -> al registrar un pago se elige la cuenta de la que salió

🔗 DÓNDE SE GUARDAN (importante):
  La lista OFICIAL vive en la BASE DE DATOS, dentro de la configuración del
  sistema (tabla 'configuracion_sistema', columna 'data' -> clave
  'cuentas_bancarias'). Así TODOS los equipos ven exactamente las mismas cuentas.

  El archivo del equipo (configuracion.json) se guarda solo como COPIA DE
  RESPALDO para poder trabajar sin conexión. Si un equipo guarda sin internet,
  queda marcado como pendiente y se sube a la base de datos en cuanto vuelve la
  conexión.

Formato de cada cuenta (el que espera el módulo de Bancos):

    {
        "banco":         "BCP",             # nombre del banco
        "cuenta":        "193-1234567-0-91",# número de cuenta (o cuenta/CCI)
        "saldo_inicial": "15000.00",        # saldo con el que se abre la cuenta
        "fecha":         "01/08/2026"       # desde cuándo rige ese saldo inicial
    }
"""
import json
import os
import time

# Clave con la que se guardan las cuentas en la configuración (local y nube).
CLAVE_CUENTAS = "cuentas_bancarias"
# Marca de "guardado sin conexión": la copia local manda hasta poder subirla.
CLAVE_PENDIENTE = "cuentas_bancarias_pendiente_nube"

# Campos de una cuenta bancaria, en el orden en que se muestran/editan.
CAMPOS_CUENTA = ("banco", "cuenta", "saldo_inicial", "fecha")

# Caché en memoria: evita consultar la base de datos cada vez que se abre un combo.
_TTL_CACHE = 120.0          # segundos
_CACHE = {"valor": None, "momento": 0.0}


# =========================================================
# LECTURA / ESCRITURA BÁSICA
# =========================================================
def _ruta_config_local():
    try:
        from app_paths import CONFIG_FILE
        return str(CONFIG_FILE)
    except Exception:
        return ""


def _leer_json_local():
    ruta = _ruta_config_local()
    if ruta and os.path.exists(ruta):
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            pass
    return {}


def leer_config():
    """Configuración del equipo (config_local.json + configuracion.json del usuario)."""
    try:
        from app_paths import cargar_config_local
        return cargar_config_local() or {}
    except Exception:
        return {}


def normalizar_cuenta(cuenta):
    """Devuelve una cuenta con los 4 campos esperados y sin espacios de sobra."""
    cuenta = cuenta if isinstance(cuenta, dict) else {}
    limpia = {}
    for campo in CAMPOS_CUENTA:
        valor = cuenta.get(campo, "")
        if isinstance(valor, (int, float)):
            valor = f"{valor:.2f}" if campo == "saldo_inicial" else str(valor)
        limpia[campo] = str(valor or "").strip()
    # Compatibilidad con configuraciones antiguas que usaban "nombre"/"numero"
    if not limpia["banco"]:
        limpia["banco"] = str(cuenta.get("nombre", "") or "").strip()
    if not limpia["cuenta"]:
        limpia["cuenta"] = str(cuenta.get("numero", "") or "").strip()
    return limpia


def _cuentas_validas(lista):
    return [c for c in (lista or [])
            if isinstance(c, dict) and (str(c.get("banco", "")).strip()
                                        or str(c.get("cuenta", "")).strip())]


def invalidar_cache():
    """Olvida la caché en memoria (se llama al guardar las cuentas)."""
    _CACHE["valor"] = None
    _CACHE["momento"] = 0.0


# =========================================================
# BASE DE DATOS (fuente oficial, compartida por todos los equipos)
# =========================================================
def leer_cuentas_nube():
    """Cuentas guardadas en la base de datos (configuración del sistema).

    Devuelve:
      * una lista (posiblemente vacía) si se pudo leer,
      * None si NO se pudo leer (sin conexión / tabla no disponible / la clave
        nunca se guardó): en ese caso el módulo trabaja con el respaldo local.
    """
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if not conn:
            return None
        try:
            with conn.cursor() as c:
                c.execute("SELECT data FROM configuracion_sistema WHERE id = 1")
                fila = c.fetchone()
            if not fila:
                return None                     # todavía no hay configuración en la nube
            data = fila[0]
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    data = {}
            if not isinstance(data, dict) or CLAVE_CUENTAS not in data:
                return None                     # nunca se guardaron cuentas: manda el respaldo local
            return _cuentas_validas(data.get(CLAVE_CUENTAS))
        finally:
            liberar_conexion(conn)
    except Exception as e:
        print("[Bancos] No se pudo leer las cuentas de la base de datos:", e)
        return None


def _guardar_nube(cuentas):
    """Guarda las cuentas en la base de datos (sin borrar el resto de la configuración)."""
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if not conn:
            return False
        try:
            with conn.cursor() as c:
                c.execute("SELECT data FROM configuracion_sistema WHERE id = 1")
                fila = c.fetchone()
                data = fila[0] if fila else None
                if isinstance(data, str):
                    try:
                        data = json.loads(data)
                    except Exception:
                        data = {}
                data = dict(data or {})
                data[CLAVE_CUENTAS] = cuentas
                c.execute("""
                    INSERT INTO configuracion_sistema (id, data, actualizado_el)
                    VALUES (1, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data,
                                                   actualizado_el = CURRENT_TIMESTAMP
                """, (json.dumps(data, ensure_ascii=False),))
            conn.commit()
            return True
        finally:
            liberar_conexion(conn)
    except Exception as e:
        print("[Bancos] No se pudieron guardar las cuentas en la base de datos:", e)
        return False


def _guardar_local(cuentas, pendiente=False):
    """Copia de respaldo en el equipo (para poder trabajar sin conexión)."""
    ruta = _ruta_config_local()
    if not ruta:
        return False
    try:
        cfg = _leer_json_local()
        cfg[CLAVE_CUENTAS] = cuentas
        if pendiente:
            cfg[CLAVE_PENDIENTE] = True
        else:
            cfg.pop(CLAVE_PENDIENTE, None)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print("[Bancos] No se pudieron guardar las cuentas en este equipo:", e)
        return False


# =========================================================
# API QUE USAN LOS MÓDULOS
# =========================================================
def cargar_bancos(usar_nube=True, usar_cache=True):
    """Cuentas bancarias configuradas. Devuelve una lista de diccionarios.

    Lee de la BASE DE DATOS (así todos los equipos ven lo mismo) y solo usa el
    archivo del equipo cuando no hay conexión o cuando un guardado quedó
    pendiente de subir.
    """
    ahora = time.time()
    if usar_cache and _CACHE["valor"] is not None and (ahora - _CACHE["momento"]) < _TTL_CACHE:
        return [dict(c) for c in _CACHE["valor"]]

    cfg_local = leer_config() or {}
    locales = _cuentas_validas(cfg_local.get(CLAVE_CUENTAS))
    pendiente = bool(cfg_local.get(CLAVE_PENDIENTE))

    if pendiente and locales:
        # Se guardaron sin conexión: se reintenta subirlas a la base de datos.
        limpias = [normalizar_cuenta(c) for c in locales]
        if _guardar_nube(limpias):
            _guardar_local(limpias, pendiente=False)
        cuentas = limpias
    else:
        de_nube = leer_cuentas_nube() if usar_nube else None
        cuentas = locales if de_nube is None else de_nube

    cuentas = [normalizar_cuenta(c) for c in cuentas]
    _CACHE["valor"] = cuentas
    _CACHE["momento"] = ahora
    return [dict(c) for c in cuentas]


def guardar_bancos(cuentas, sincronizar_nube=True, proteger_vacio=True):
    """Guarda la lista de cuentas en la BASE DE DATOS (y una copia en el equipo).

    🛡️ 'proteger_vacio': si llega una lista vacía y en la base de datos ya hay
    cuentas configuradas, el guardado se IGNORA. Así un formulario abierto con
    datos viejos (o cualquier guardado accidental) no puede borrar las cuentas.
    Para borrar la última cuenta a propósito hay que pasar proteger_vacio=False.

    Devuelve True si se guardó (en la base de datos o, sin conexión, en el equipo).
    """
    limpias = [normalizar_cuenta(c) for c in _cuentas_validas(cuentas)]

    if not limpias and proteger_vacio:
        existentes = leer_cuentas_nube()
        if existentes:
            print("[Bancos] Se ignoró un guardado vacío: la base de datos ya tiene "
                  "cuentas bancarias configuradas.")
            return False

    ok_nube = _guardar_nube(limpias) if sincronizar_nube else False
    # Copia local: siempre (sirve de respaldo y permite seguir sin conexión).
    _guardar_local(limpias, pendiente=bool(sincronizar_nube and not ok_nube))
    invalidar_cache()
    if sincronizar_nube and not ok_nube:
        return True                 # quedó guardado aquí; se subirá al volver la conexión
    return True


# =========================================================
# ETIQUETAS PARA COMBOS
# =========================================================
def etiqueta_banco(cuenta):
    """Texto con el que una cuenta se muestra en los desplegables."""
    c = normalizar_cuenta(cuenta)
    etiqueta = f"{c['banco']} - {c['cuenta']}".strip(" -")
    return etiqueta or "Cuenta sin nombre"


def lista_etiquetas(incluir_vacio=False, texto_vacio="(Sin cuenta bancaria)"):
    """Etiquetas de todas las cuentas configuradas, para llenar un combo."""
    etiquetas = [etiqueta_banco(c) for c in cargar_bancos()]
    if incluir_vacio:
        return [texto_vacio] + etiquetas
    return etiquetas or [texto_vacio]
