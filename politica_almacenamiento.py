# -*- coding: utf-8 -*-
"""
POLITICA_ALMACENAMIENTO.PY - Reglas de dónde PUEDE guardar archivos el sistema.

Regla de negocio (modo único):
  * MULTIUSUARIO (nube): existe una cuenta Rclone registrada por el EQUIPO
    PRINCIPAL (tabla config_general de Supabase, claves 'rclone_sync' +
    'rclone_token'). Todos los equipos deben usar ESA MISMA cuenta. Si un equipo
    secundario usa otra cuenta (o no tiene ninguna), se BLOQUEA el guardado de
    archivos en todo el sistema (ni local ni nube), para que la información no
    se guarde en otro lugar.
  * MONOUSUARIO (local): mientras NO exista una cuenta del equipo principal
    registrada, el equipo trabaja en local y sí puede guardar archivos.

⚠️ IMPORTANTE (identidad vs token):
  rclone REESCRIBE su rclone.conf cada vez que renueva el access_token de Google
  (cada ~1 hora de uso). Por eso NO se compara el texto del archivo, sino la
  IDENTIDAD de la cuenta: remoto + tipo + scope + team_drive + root_folder_id +
  refresh_token. Esa identidad NO cambia al renovarse el token, pero sí cuando
  se usa otra cuenta. Así la conexión con el equipo principal no se "pierde"
  al reiniciar el programa.

También bloquea la descarga de los tickets del App Grifo cuando el equipo no
está autorizado, sin borrarlos de la nube (imagen_base64 se conserva) para que
un equipo correctamente configurado los pueda descargar.

Uso típico en cualquier módulo:
    from politica_almacenamiento import ruta_base_autorizada
    ruta_base = ruta_base_autorizada(parent=ventana)
    if not ruta_base:
        return          # ya se mostró la advertencia correspondiente
"""
import os
import sys
import json
import time
import hashlib

from app_paths import DATA_DIR, CONFIG_FILE, obtener_device_id

try:
    from config_nube import cargar_rclone_sync, obtener_rclone_token
except Exception:  # pragma: no cover - sin acceso a la nube
    cargar_rclone_sync = lambda: None          # noqa: E731
    obtener_rclone_token = lambda: ""          # noqa: E731

MODO_MONOUSUARIO = "monousuario"
MODO_MULTIUSUARIO = "multiusuario"

ARCHIVO_RCLONE = DATA_DIR / "rclone.conf"
ESTADO_FILE = DATA_DIR / "estado_almacenamiento.json"

_TTL_SEGUNDOS = 20          # cada cuánto se re-verifica la nube
_TTL_AVISO = 15             # no repetir la misma advertencia antes de este tiempo

_cache = {"t": 0.0, "estado": None}
_ultimo_aviso = {"t": 0.0, "codigo": ""}


# =========================================================
# UTILIDADES INTERNAS
# =========================================================
def _leer_config_local():
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, dict):
                    return datos
    except Exception:
        pass
    return {}


def ruta_local_configurada():
    """Carpeta local configurada en Configuración del Sistema (ruta_drive)."""
    return str(_leer_config_local().get("ruta_drive", "") or "").strip()


def normalizar_ruta(ruta):
    """Expande ~ y descarta rutas de Windows cuando se corre en macOS/Linux."""
    if not ruta:
        return ""
    ruta = os.path.expanduser(str(ruta).strip())
    if sys.platform != "win32" and len(ruta) > 2 and ruta[1] == ":" and ruta[2] in ("\\", "/"):
        return ""
    return ruta


def _ruta_rclone_por_defecto():
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.join(os.path.expanduser("~"), "AppData", "Roaming"))
        return os.path.join(base, "rclone", "rclone.conf")
    return os.path.expanduser("~/.config/rclone/rclone.conf")


def leer_rclone_local():
    """Contenido del rclone.conf que usa la app (o del sistema como respaldo)."""
    for ruta in (str(ARCHIVO_RCLONE), _ruta_rclone_por_defecto()):
        try:
            if ruta and os.path.exists(ruta):
                with open(ruta, "r", encoding="utf-8", errors="ignore") as f:
                    texto = f.read()
                if texto.strip():
                    return texto
        except Exception:
            continue
    return ""


def _normalizar_conf(texto):
    """Normaliza el rclone.conf (ignora comentarios y líneas vacías)."""
    if not texto:
        return ""
    lineas = str(texto).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    limpias = []
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#") or linea.startswith(";"):
            continue
        limpias.append(linea)
    return "\n".join(limpias)


def _huella(texto):
    normalizado = _normalizar_conf(texto)
    if not normalizado:
        return ""
    return hashlib.sha256(normalizado.encode("utf-8", "ignore")).hexdigest()


# =========================================================
# IDENTIDAD DE LA CUENTA (estable ante la renovación del token)
# =========================================================
def _parsear_conf(texto):
    """Devuelve {remote: {clave: valor}} del contenido de un rclone.conf."""
    remotos = {}
    actual = None
    for linea in str(texto or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        linea = linea.strip()
        if not linea or linea.startswith("#") or linea.startswith(";"):
            continue
        if linea.startswith("[") and linea.endswith("]"):
            actual = linea[1:-1].strip().lower()
            remotos.setdefault(actual, {})
            continue
        if actual is not None and "=" in linea:
            clave, valor = linea.split("=", 1)
            remotos[actual][clave.strip().lower()] = valor.strip()
    return remotos


def _refresh_token(valor_token):
    """Extrae el refresh_token del campo 'token' (JSON) del rclone.conf."""
    try:
        datos = json.loads(valor_token)
        if isinstance(datos, dict):
            return str(datos.get("refresh_token") or "")
    except Exception:
        pass
    return ""


def identidad_remotos(texto):
    """Identidad ESTABLE de cada cuenta configurada: {remote: huella}.

    No depende del access_token (que rclone renueva y reescribe), por lo que la
    verificación sigue siendo válida después de reiniciar el programa.
    """
    identidad = {}
    for nombre, campos in _parsear_conf(texto).items():
        base = "|".join([
            campos.get("type", ""),
            campos.get("scope", ""),
            campos.get("team_drive", ""),
            campos.get("root_folder_id", ""),
            _refresh_token(campos.get("token", "")),
        ])
        identidad[nombre] = hashlib.sha256(base.encode("utf-8", "ignore")).hexdigest()[:16]
    return identidad


def cuenta_coincide(identidad_principal, identidad_local):
    """True si el equipo local tiene (al menos) las mismas cuentas del principal."""
    if not identidad_principal or not identidad_local:
        return False
    for remoto, huella in identidad_principal.items():
        if identidad_local.get(remoto) != huella:
            return False
    return True


# =========================================================
# ESTADO PERSISTIDO
# =========================================================
def _estado_persistido():
    try:
        if ESTADO_FILE.exists():
            with open(str(ESTADO_FILE), "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, dict):
                    return datos
    except Exception:
        pass
    return {}


def _guardar_estado_persistido(datos):
    try:
        with open(str(ESTADO_FILE), "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# =========================================================
# ESTADO
# =========================================================
def _calcular_estado():
    device_id = obtener_device_id()
    persistido = _estado_persistido()

    try:
        registro = cargar_rclone_sync()
    except Exception:
        registro = None

    identidad_principal = {}
    if registro:
        compartida = registro.get("cuenta_remotos")
        if isinstance(compartida, dict):
            identidad_principal = {str(k).lower(): str(v) for k, v in compartida.items()}
        token = ""
        try:
            token = obtener_rclone_token() or ""
        except Exception:
            token = ""
        if not identidad_principal and token.strip():
            identidad_principal = identidad_remotos(token)
        estado = {
            "modo": MODO_MULTIUSUARIO,
            "principal_definido": True,
            "es_principal": (registro.get("linked_by_device") or "") == device_id,
            "remoto": (registro.get("rclone_remote") or "").strip(),
            "carpeta_nube": (registro.get("rclone_ruta_nube") or "").strip(),
            "huella_principal": _huella(token),
            "equipo_principal": registro.get("linked_by_device") or "",
            "cuenta_compartida": bool(token.strip()) or bool(identidad_principal),
            "cuenta_principal": identidad_principal,
        }
        # Memoria local: permite verificar incluso sin conexión a la nube
        _guardar_estado_persistido({
            "principal_definido": True,
            "equipo_principal": estado["equipo_principal"],
            "remoto": estado["remoto"],
            "carpeta_nube": estado["carpeta_nube"],
            "huella_principal": estado["huella_principal"],
            "cuenta_compartida": estado["cuenta_compartida"],
            "cuenta_principal": identidad_principal,
            "actualizado": time.strftime("%d/%m/%Y %H:%M:%S"),
        })
    else:
        principal_definido = bool(persistido.get("principal_definido"))
        identidad_principal = persistido.get("cuenta_principal") or {}
        if not isinstance(identidad_principal, dict):
            identidad_principal = {}
        estado = {
            "modo": MODO_MULTIUSUARIO if principal_definido else MODO_MONOUSUARIO,
            "principal_definido": principal_definido,
            "es_principal": (persistido.get("equipo_principal") or "") == device_id,
            "remoto": persistido.get("remoto", ""),
            "carpeta_nube": persistido.get("carpeta_nube", ""),
            "huella_principal": persistido.get("huella_principal", ""),
            "equipo_principal": persistido.get("equipo_principal", ""),
            "cuenta_compartida": bool(persistido.get("cuenta_compartida")),
            "cuenta_principal": identidad_principal,
            "sin_conexion": True,
        }

    texto_local = leer_rclone_local()
    estado["huella_local"] = _huella(texto_local)
    estado["cuenta_local"] = identidad_remotos(texto_local)
    estado["ruta_local"] = ruta_local_configurada()

    coincide_identidad = cuenta_coincide(estado["cuenta_principal"], estado["cuenta_local"])
    coincide_texto = bool(estado["huella_local"]) and estado["huella_local"] == estado["huella_principal"]

    if not estado["principal_definido"]:
        estado.update({"autorizado": True, "codigo": "OK_MONOUSUARIO",
                       "motivo": "Modo monousuario: aún no hay una cuenta Rclone del equipo principal asignada."})
    elif estado["es_principal"]:
        estado.update({"autorizado": True, "codigo": "OK_PRINCIPAL",
                       "motivo": "Este equipo es el principal (dueño de la cuenta Rclone)."})
    elif not estado["cuenta_principal"] and not estado["cuenta_compartida"]:
        estado.update({"autorizado": False, "codigo": "BLOQUEADO_SIN_CUENTA_COMPARTIDA",
                       "motivo": "El equipo principal todavía no compartió su cuenta de Rclone."})
    elif coincide_identidad or coincide_texto:
        # La identidad de la cuenta coincide: el archivo puede diferir solo porque
        # rclone renovó el access_token (eso es normal y no se toca el archivo).
        estado.update({"autorizado": True, "codigo": "OK_SECUNDARIO",
                       "motivo": "Este equipo usa la misma cuenta de Rclone del equipo principal."})
    else:
        estado.update({"autorizado": False, "codigo": "BLOQUEADO_RCLONE_DISTINTO",
                       "motivo": "Este equipo no tiene la cuenta de Rclone del equipo principal."})
    return estado


def estado_almacenamiento(forzar=False):
    """Estado actual de la política (con caché corta para no consultar la nube siempre)."""
    ahora = time.time()
    if not forzar and _cache["estado"] is not None and (ahora - _cache["t"]) < _TTL_SEGUNDOS:
        return _cache["estado"]
    estado = _calcular_estado()
    _cache["t"] = ahora
    _cache["estado"] = estado
    return estado


def invalidar_cache():
    _cache["t"] = 0.0
    _cache["estado"] = None


def puede_guardar_archivos():
    """(bool, estado) -> True si este equipo puede guardar archivos."""
    estado = estado_almacenamiento()
    return bool(estado.get("autorizado")), estado


def bloqueo_activo():
    return not bool(estado_almacenamiento().get("autorizado"))


def modo_monousuario():
    return estado_almacenamiento().get("modo") == MODO_MONOUSUARIO


def permitir_respaldo_local():
    """True si se permite el respaldo en la carpeta del PROGRAMA (no sincronizada).

    Solo en modo monousuario (aún no hay cuenta del principal). En multiusuario
    los archivos deben ir a la carpeta local sincronizada con la cuenta del
    principal; guardar en la carpeta del programa los dejaría "en otro lugar".
    """
    return modo_monousuario()


# =========================================================
# MENSAJES Y PERMISOS
# =========================================================
def mensaje_bloqueo(estado=None):
    estado = estado or estado_almacenamiento()
    if estado.get("codigo") == "BLOQUEADO_SIN_CUENTA_COMPARTIDA":
        return ("🔒 GUARDADO DE ARCHIVOS BLOQUEADO\n\n"
                "El equipo PRINCIPAL aún no ha compartido su cuenta de Rclone.\n\n"
                "Para no guardar información en otro lugar, ningún módulo puede guardar archivos "
                "hasta que la cuenta del principal esté disponible.\n\n"
                "Pide al equipo principal que abra Configuración del Sistema y pulse "
                "\"Compartir cuenta con los demás equipos\".")
    remoto = (estado.get("remoto") or "—")
    nube = (estado.get("carpeta_nube") or "")
    destino = f"{remoto}{nube}" if remoto.endswith(":") else f"{remoto}:{nube}" if nube else remoto
    return ("🔒 GUARDADO DE ARCHIVOS BLOQUEADO\n\n"
            "Este equipo NO está usando la cuenta de Rclone del equipo principal, "
            "por lo que la información podría guardarse en otro lugar.\n\n"
            f"Cuenta del principal: {destino}\n\n"
            "No se permite guardar archivos (ni en local ni en la nube) en ningún módulo "
            "hasta que este equipo quede vinculado con esa misma cuenta.\n\n"
            "Solución: en Configuración del Sistema pulsa \"Usar la cuenta del equipo principal\".")


def advertir(parent=None, forzar=False):
    """Muestra la advertencia de bloqueo (con anti-repetición)."""
    estado = estado_almacenamiento()
    if estado.get("autorizado"):
        return
    ahora = time.time()
    if not forzar and _ultimo_aviso["codigo"] == estado.get("codigo") and (ahora - _ultimo_aviso["t"]) < _TTL_AVISO:
        return
    _ultimo_aviso["t"] = ahora
    _ultimo_aviso["codigo"] = estado.get("codigo", "")
    try:
        from tkinter import messagebox
        messagebox.showwarning("Almacenamiento bloqueado", mensaje_bloqueo(estado), parent=parent)
    except Exception:
        print(mensaje_bloqueo(estado))


def exigir_permiso(parent=None):
    """True si este equipo puede guardar archivos.

    Si no puede, muestra la advertencia correspondiente y devuelve False, para
    que el módulo simplemente aborte el guardado.
    """
    autorizado, _estado = puede_guardar_archivos()
    if autorizado:
        return True
    advertir(parent)
    return False


def avisar_sin_ruta(parent=None):
    """Aviso correcto cuando no hay carpeta base disponible.

    Si el bloqueo es por la política de almacenamiento muestra esa advertencia;
    si no, la de configuración faltante. Devuelve True si fue por bloqueo.
    """
    estado = estado_almacenamiento()
    if not estado.get("autorizado"):
        advertir(parent, forzar=True)
        return True
    try:
        from tkinter import messagebox
        messagebox.showwarning("Configuración Requerida",
                               "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar archivos.",
                               parent=parent)
    except Exception:
        pass
    return False


def ruta_base_autorizada(parent=None, mostrar_alerta=True):
    """Carpeta base donde el sistema puede guardar archivos.

    Devuelve "" cuando el equipo no está autorizado (y muestra la advertencia),
    de modo que los módulos no guarden absolutamente nada.
    """
    estado = estado_almacenamiento()
    if not estado.get("autorizado"):
        if mostrar_alerta:
            advertir(parent)
        return ""
    return normalizar_ruta(estado.get("ruta_local", ""))


# =========================================================
# REPARAR: USAR LA CUENTA DEL EQUIPO PRINCIPAL
# =========================================================
def reparar_cuenta_principal():
    """Escribe en el rclone.conf de este equipo la cuenta del equipo principal.

    Devuelve (ok, mensaje). Solo tiene efecto si el principal ya compartió su
    token; si no, informa que aún no está disponible.
    """
    try:
        token = obtener_rclone_token() or ""
    except Exception:
        token = ""
    if not token.strip():
        return False, ("El equipo principal todavía no ha compartido su cuenta de Rclone.\n\n"
                       "Pídele que la comparta desde Configuración del Sistema "
                       "(\"Compartir cuenta con los demás equipos\").")
    try:
        ARCHIVO_RCLONE.parent.mkdir(parents=True, exist_ok=True)
        with open(str(ARCHIVO_RCLONE), "w", encoding="utf-8") as f:
            f.write(token)
    except Exception as e:
        return False, f"No se pudo escribir la configuración de Rclone:\n{e}"

    invalidar_cache()
    estado = estado_almacenamiento(forzar=True)
    if estado.get("autorizado"):
        return True, "✅ Este equipo ya usa la misma cuenta de Rclone del equipo principal."
    return False, ("La cuenta se descargó, pero la verificación falló. "
                   "Revisa que el equipo principal sea el dueño del registro de sincronización.")


def identidad_local_actual():
    """Identidad de las cuentas configuradas en este equipo (para publicarla)."""
    return identidad_remotos(leer_rclone_local())


def es_equipo_principal():
    estado = estado_almacenamiento()
    return bool(estado.get("es_principal"))


def principal_definido():
    return bool(estado_almacenamiento().get("principal_definido"))
