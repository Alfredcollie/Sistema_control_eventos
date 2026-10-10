# -*- coding: utf-8 -*-
"""
CONTROL_GENERAL.PY - SISTEMA DE CONTROL GENERAL DE EVENTOS (ENTERPRISE)
- 100% Compatibilidad macOS / Windows / Linux.
- 7 Secciones completas de Configuración General.
- Sincronización Nube <-> Local con protección de rutas de disco.
- Verificador de Actualizaciones y Consulta SUNAT con SSL tolerante para macOS.
- Carga Asíncrona de Alertas y Listas.
"""
import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox, filedialog, ttk, colorchooser
import sys
import ctypes
import os
import json
import importlib
import inspect
import urllib.request
import ssl
import bcrypt
import subprocess
import threading
import webbrowser
from datetime import datetime, timedelta

VERSION_ACTUAL = "v1.5.6"


def _version_a_tupla(v):
    """Convierte 'v1.5.6' en (1, 5, 6) para comparar versiones numéricamente."""
    v = (v or "").strip().lstrip("vV")
    partes = []
    for segmento in v.split("."):
        digitos = ""
        for c in segmento:
            if c.isdigit():
                digitos += c
            else:
                break
        if not digitos:
            break
        partes.append(int(digitos))
    return tuple(partes)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema
from app_paths import CONFIG_FILE, cargar_config_local

# 🏦 Cuentas bancarias del sistema (las comparten Configuración General,
# el módulo de Bancos y el registro de cobros de Ventas).
try:
    from bancos import cargar_bancos, guardar_bancos, normalizar_cuenta, CLAVE_CUENTAS
except Exception:
    CLAVE_CUENTAS = "cuentas_bancarias"
    def cargar_bancos(usar_nube=True): return []
    def guardar_bancos(cuentas, sincronizar_nube=True): return False
    def normalizar_cuenta(cuenta): return dict(cuenta or {})

# 🚀 Fecha de Comienzo del Sistema (corte de compras y ventas)
try:
    from fecha_sistema import (
        obtener_fecha_comienzo, purgar_anteriores, resumen_purga,
        parsear_fecha as parsear_fecha_corte, CLAVE_FECHA_COMIENZO,
    )
    FECHA_SISTEMA_DISPONIBLE = True
except Exception:
    FECHA_SISTEMA_DISPONIBLE = False

try:
    from PIL import Image
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass

def ajustar_al_area_de_trabajo(ventana):
    """🪟 En Windows, si la ventana maximizada sobresale del área de trabajo (la
    barra de tareas tapa el borde inferior), se ajusta para que se vea completa:
    así la barra de pestañas de abajo nunca queda oculta."""
    if sys.platform != "win32":
        return
    try:
        u = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        area = RECT()
        if not u.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0):   # SPI_GETWORKAREA
            return
        class PUNTO(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        hwnd = u.GetParent(ventana.winfo_id()) or ventana.winfo_id()
        cliente = RECT()
        u.GetClientRect(hwnd, ctypes.byref(cliente))
        origen = PUNTO(0, 0)
        u.ClientToScreen(hwnd, ctypes.byref(origen))
        x1, y1 = origen.x, origen.y
        x2, y2 = x1 + cliente.right, y1 + cliente.bottom
        if x1 >= area.left and y1 >= area.top and x2 <= area.right and y2 <= area.bottom:
            return                       # ya entra completo: no se toca
        ancho = area.right - area.left
        alto = area.bottom - area.top
        ventana.geometry(f"{ancho}x{alto}+{area.left}+{area.top}")
    except Exception:
        pass


def maximizar_ventana(ventana):
    """Maximiza ventanas sin errores en macOS ni Windows."""
    try:
        if sys.platform == "win32":
            ventana.state("zoomed")
            try:
                ventana.after(150, lambda v=ventana: ajustar_al_area_de_trabajo(v))
            except Exception:
                pass
        else:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
    except Exception:
        try:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

def traer_al_frente(ventana, retrasos=(60, 260, 700, 1150)):
    """🪟 Deja la ventana DELANTE de la ventana principal.

    CustomTkinter oculta y vuelve a mostrar cada ventana nueva para pintarle la
    barra de título (y a los 1000 ms vuelve a ajustar el tamaño mínimo): esos
    ciclos hacen que la ventana termine DETRÁS de la principal. Por eso se trae al
    frente varias veces, con un 'topmost' momentáneo que se suelta enseguida.
    """
    try:
        ventana._windows_set_titlebar_color = lambda *a, **k: None
    except Exception:
        pass

    def _soltar_topmost():
        try:
            if ventana.winfo_exists():
                ventana.attributes("-topmost", False)
        except Exception:
            pass

    def _traer():
        try:
            if not ventana.winfo_exists():
                return
            ventana.deiconify()
            ventana.lift()
            try:
                ventana.attributes("-topmost", True)
                ventana.after(450, _soltar_topmost)
            except Exception:
                pass
            ventana.focus_force()
        except Exception:
            pass

    for retardo in retrasos:
        try:
            ventana.after(retardo, _traer)
        except Exception:
            pass


# ==========================================================
# 🪟 AYUDAS DE LAS VENTANAS DE MÓDULOS
# ==========================================================
# Métodos que vuelven a traer los datos de un módulo desde la base de datos.
# El sistema los usa cuando se abre un módulo que YA estaba abierto, para que la
# ventana que se trae al frente muestre la información actualizada.
METODOS_RECARGA_MODULOS = (
    "cargar_datos_tabla", "cargar_datos_cobrar", "cargar_datos_pagar", "cargar_datos_nc",
    "cargar_clientes_tabla", "cargar_proveedores_tabla", "cargar_tabla",
    "cargar_historial_ordenes", "cargar_historial", "cargar_registros",
    "cargar_solicitudes_tab", "cargar_datos_diario", "cargar_datos_mayor", "cargar_datos_db",
    "cargar_kpis", "cargar_categorias", "cargar_combos",
    "refrescar_saldos", "refrescar_transferencias", "refrescar_tree",
    "refrescar_tabla_categorias", "refrescar_datos", "actualizar_tabla", "recargar_datos",
)


def _acepta_reset_pagina(metodo):
    """¿El método de recarga acepta el argumento reset_pagina?"""
    try:
        return "reset_pagina" in inspect.signature(metodo).parameters
    except Exception:
        return False


def _buscar_metodos_recarga(objeto, encontrados, vistos, profundidad=0):
    """Busca (sin repetir) los métodos de recarga dentro de un módulo abierto.

    Entra también en los paneles internos (las pestañas del módulo) pero nunca en
    los widgets: sus métodos no recargan datos y son cientos.
    """
    if objeto is None or id(objeto) in vistos or profundidad > 2:
        return
    vistos.add(id(objeto))
    for nombre in dir(objeto):
        if nombre.startswith("_"):
            continue
        try:
            valor = getattr(objeto, nombre)
        except Exception:
            continue
        if callable(valor):
            if nombre in METODOS_RECARGA_MODULOS:
                encontrados.append(valor)
        elif not isinstance(valor, (str, bytes, int, float, bool, list, tuple, dict, set)):
            if not isinstance(valor, tk.Misc):
                _buscar_metodos_recarga(valor, encontrados, vistos, profundidad + 1)


def refrescar_instancia_modulo(instancia):
    """🔄 Vuelve a cargar los datos de un módulo abierto.

    Devuelve cuántos apartados se recargaron (0 si el módulo no expone ninguno).
    """
    encontrados = []
    try:
        _buscar_metodos_recarga(instancia, encontrados, set())
    except Exception:
        return 0
    ejecutados = 0
    for metodo in encontrados:
        try:
            if _acepta_reset_pagina(metodo):
                metodo(reset_pagina=True)
            else:
                metodo()
            ejecutados += 1
        except Exception:
            pass
    return ejecutados


def poner_ventana_en_barra_tareas(ventana):
    """🪟 En Windows asegura que la ventana del módulo tenga su propio botón en la
    barra de tareas: así se puede minimizar, restaurar y cerrar desde ahí."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(ventana.winfo_id()) or ventana.winfo_id()
        GWL_EXSTYLE, WS_EX_APPWINDOW = -20, 0x00040000
        estilo = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, estilo | WS_EX_APPWINDOW)
        SWP = 0x0020 | 0x0002 | 0x0001 | 0x0004      # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP)
    except Exception:
        pass


def ruta_recurso(ruta_relativa):
    try:
        ruta_base = sys._MEIPASS
    except Exception:
        ruta_base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(ruta_base, ruta_relativa)

def aplicar_icono_ventana(ventana):
    if sys.platform == "win32":
        try:
            ruta_ico = ruta_recurso("Ico_Collie_Software.ico")
            if os.path.exists(ruta_ico):
                ventana.iconbitmap(ruta_ico)
        except Exception:
            pass

def obtener_comando_rclone():
    nombre_ejecutable = "rclone.exe" if sys.platform == "win32" else "rclone"
    if hasattr(sys, '_MEIPASS'):
        ruta_bundle = os.path.join(sys._MEIPASS, nombre_ejecutable)
        if os.path.exists(ruta_bundle):
            return ruta_bundle
    ruta_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre_ejecutable)
    if os.path.exists(ruta_local):
        return ruta_local
    rutas_mac = ["/opt/homebrew/bin/rclone", "/usr/local/bin/rclone", "/usr/bin/rclone"]
    if sys.platform != "win32":
        for r in rutas_mac:
            if os.path.exists(r):
                return r
    return "rclone"

def crear_contexto_ssl_seguro():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def cargar_configuracion_general():
    config = {
        "ruta_drive": "", "rclone_remote": "gdrive:", "rclone_ruta_nube": "BlackCube",
        "impresora": "", "simbolo_moneda": "S/.", "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA", "ruta_logo_cotizacion": "",
        "color_primario": "#eb337a", "color_secundario": "#000000", "color_franja": "#eb337a",
        "ruc_empresa": "", "razon_social_empresa": "", "igv_porcentaje": "0",
        "detraccion_porcentaje": "12",
        "renta_mensual_porcentaje": "0", "renta_anual_porcentaje": "0",
        "regimen_empresa": "MYPE Tributario", "nombre_cliente_cotizacion": "Razón Social",
        "proveedor_fe": "Nubefact", "url_api_fe": "", "token_api_fe": "",
        "ultimo_factura": "F001-0", "ultimo_boleta": "B001-0", "ultimo_recibo": "E001-0",
        "usuario_sol": "", "clave_sol": "", "client_id_sire": "", "client_secret_sire": "",
        "2fa_metodo": "Inactivo", "tel_bot_token": "", "tel_chat_id": "",
        "email_smtp": "smtp.gmail.com", "email_port": "587", "email_user": "", "email_pass": "", "email_dest": "",
        "twi_sid": "", "twi_token": "", "twi_from": "", "twi_to": "",
        "color_menu_fondo": "#1a252c", "color_menu_btn": "#1f538d",
        "color_menu_hover": "#163b65", "color_menu_texto": "white",
        "terminos_cotizacion": "Precios no incluyen IGV.\nCotización válida por 7 días. Posterior a ello podría haber cambios en el presupuesto.\nPenalidad: Si el presupuesto es aprobado y finalmente el proyecto no se lleva a cabo, se facturará al cliente un 10% del valor total como compensación por gastos administrativos.",
        "fecha_comienzo_sistema": "",
        "cuentas_bancarias": [],
        "orden_operativos": ["clientes", "cotizaciones", "pautas", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "inventario", "locaciones"],
        "orden_finanzas": ["ventas", "compras", "bancos", "libro_diario", "libro_mayor", "impuestos", "dashboard"],
        "orden_ajustes": ["configuracion", "usuarios", "bitacora"]
    }
    
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
        
    conn = conectar_db(silencioso=True)
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT data FROM configuracion_sistema WHERE id = 1")
            res = cursor.fetchone()
            if res and res[0]:
                config_nube = res[0]
                impresora_local = config.get("impresora", "")
                ruta_drive_local = config.get("ruta_drive", "")
                rclone_remote_local = config.get("rclone_remote", "gdrive:")
                ruta_logo_local = config.get("ruta_logo_cotizacion", "")
                
                config.update(config_nube)
                
                if impresora_local: config["impresora"] = impresora_local
                if ruta_drive_local: config["ruta_drive"] = ruta_drive_local
                if rclone_remote_local != "gdrive:": config["rclone_remote"] = rclone_remote_local
                if ruta_logo_local: config["ruta_logo_cotizacion"] = ruta_logo_local
                
                with open(str(CONFIG_FILE), "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4)
        except Exception as e:
            print("No se pudo sincronizar config desde la nube:", e)
        finally:
            liberar_conexion(conn)
            
    return config

def ejecutar_sincronizacion_silenciosa():
    config = cargar_configuracion_general()
    local = config.get("ruta_drive", "").strip()
    remote = config.get("rclone_remote", "gdrive:").strip()
    nube = config.get("rclone_ruta_nube", "BlackCube").strip()
    if not local or not remote or not nube: return
    cmd = obtener_comando_rclone()
    ruta_remota = f"{remote}{nube}" if remote.endswith(":") else f"{remote}:{nube}"
    kwargs = {}
    if sys.platform == "win32": kwargs["creationflags"] = 0x08000000
    try:
        subprocess.run([cmd, "copy", ruta_remota, local, "--update", "--quiet"], **kwargs)
        subprocess.run([cmd, "copy", local, ruta_remota, "--update", "--quiet"], **kwargs)
    except Exception: pass

def lanzar_sync_background():
    threading.Thread(target=ejecutar_sincronizacion_silenciosa, daemon=True).start()

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, hashed: str) -> bool:
    try: return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception: return False

_SCHEMA_SEGURIDAD_OK = False

def inicializar_seguridad_db():
    global _SCHEMA_SEGURIDAD_OK
    if _SCHEMA_SEGURIDAD_OK: return
    
    def tarea_init():
        global _SCHEMA_SEGURIDAD_OK
        conn = conectar_db(silencioso=True)
        if not conn: return
        try:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracion_sistema (
                id INTEGER PRIMARY KEY DEFAULT 1,
                data JSONB NOT NULL,
                actualizado_el TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unica_config CHECK (id = 1)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                usuario VARCHAR(255) UNIQUE NOT NULL,
                clave VARCHAR(255),
                clave_hash TEXT,
                rol VARCHAR(50) NOT NULL,
                permisos TEXT DEFAULT '{}',
                activo BOOLEAN DEFAULT TRUE,
                intentos_fallidos INTEGER DEFAULT 0
            )
            """)
            for sql in (
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS clave_hash TEXT",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS permisos TEXT DEFAULT '{}'",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT TRUE",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS intentos_fallidos INTEGER DEFAULT 0",
                "ALTER TABLE usuarios ALTER COLUMN clave DROP NOT NULL",
            ):
                try: cursor.execute(sql)
                except Exception: conn.rollback()
                
            try:
                cursor.execute("SELECT id, clave FROM usuarios WHERE clave IS NOT NULL AND (clave_hash IS NULL OR clave_hash = '')")
                for uid, clave_plana in cursor.fetchall():
                    if clave_plana:
                        cursor.execute("UPDATE usuarios SET clave_hash = %s, clave = NULL WHERE id = %s", (hash_password(clave_plana), uid))
                conn.commit()
            except Exception: conn.rollback()
            
            cursor.execute("SELECT COUNT(*) FROM usuarios")
            if cursor.fetchone()[0] == 0:
                todos_permisos = json.dumps({k: True for k in (
                    "clientes", "cotizaciones", "pautas", "ordenes_cliente", "cronograma", "ordenes", "proveedores",
                    "inventario", "locaciones", "ventas", "compras", "bancos", "libro_diario", "libro_mayor",
                    "impuestos", "dashboard", "configuracion", "usuarios", "bitacora", "solicitud_proveedor")})
                cursor.execute(
                    "INSERT INTO usuarios (usuario, clave, clave_hash, rol, permisos) VALUES (%s, NULL, %s, %s, %s)",
                    ("alfred", hash_password("admin123"), "Super Administrador", todos_permisos)
                )
                conn.commit()
                
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bitacora_auditoria (
                id SERIAL PRIMARY KEY, fecha VARCHAR(20), hora VARCHAR(20),
                usuario VARCHAR(100), modulo VARCHAR(100), accion TEXT
            )
            """)
            conn.commit()
            _SCHEMA_SEGURIDAD_OK = True
        except Exception as e: print("Error inicializando seguridad:", e)
        finally: liberar_conexion(conn)

    threading.Thread(target=tarea_init, daemon=True).start()

class ControlGeneralEventos:
    def __init__(self, root):
        self.root = root
        self.root.title("SISTEMA DE CONTROL GENERAL DE EVENTOS")
        aplicar_icono_ventana(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self.confirmar_salida)
        inicializar_seguridad_db()
        self.usuario_activo = "No autenticado"
        self.rol_activo = "Ninguno"
        self.permisos_activos = {}
        
        self.modulos_sistema = {
            "clientes": "👥 Gestión de Clientes",
            "cotizaciones": "🧾 Generador de Cotizaciones",
            "pautas": "🕒 Pautas de Evento",
            "ordenes_cliente": "📥 Órdenes de Cliente",
            "cronograma": "📅 Cronograma (Gantt)",
            "ordenes": "📝 Órdenes de Servicio / Compra",
            "proveedores": "📦 Gestión de Proveedores",
            "inventario": "📋 Inventario y Almacén",
            "locaciones": "📍 Inventario de Locaciones",
            "ventas": "💼 Ventas (Facturas y Cobros)",
            "compras": "🛒 Compras (Facturas y Pagos)",
            "bancos": "🏦 Bancos (Saldos y Conciliación)",
            "libro_diario": "📖 Libro Diario General",
            "libro_mayor": "📊 Libro Mayor Analítico",
            "impuestos": "🧮 Cálculo de Impuestos",
            "dashboard": "📈 Dashboard Gerencial",
            "configuracion": "⚙️ Configuración General",
            "usuarios": "🛠️ Configurar Usuarios",
            "bitacora": "📜 Bitácora de Auditoría",
            "solicitud_proveedor": "📨 Solicitud a Proveedores",
        }
        # Cada módulo se abre en SU PROPIA VENTANA (sistema multi-ventana).
        # Estas funciones construyen el contenido del módulo dentro del marco que
        # les pasa 'abrir_ventana_modulo' (para Configuración y Usuarios, que ya
        # abren su propia ventana, se llaman directamente).
        self.funciones_modulos = {
            "clientes": self._crear_modulo_clientes,
            "cotizaciones": self._crear_modulo_cotizaciones,
            "pautas": self._crear_modulo_pautas,
            "ordenes_cliente": self._crear_modulo_ordenes_cliente,
            "cronograma": self._crear_modulo_cronograma,
            "ordenes": self._crear_modulo_ordenes,
            "proveedores": self._crear_modulo_proveedores,
            "inventario": self._crear_modulo_inventario,
            "locaciones": self._crear_modulo_locaciones,
            "ventas": self._crear_modulo_ventas,
            "compras": self._crear_modulo_compras,
            "bancos": self._crear_modulo_bancos,
            "libro_diario": self._crear_modulo_libro_diario,
            "libro_mayor": self._crear_modulo_libro_mayor,
            "impuestos": self._crear_calculo_impuestos,
            "dashboard": self._crear_estadisticas_financiera,
            "configuracion": self.abrir_configuracion_general,
            "usuarios": self.abrir_gestion_usuarios,
            "bitacora": self._crear_modulo_bitacora,
            "solicitud_proveedor": self._crear_modulo_solicitud_proveedor,
        }
        # 🪟 Ventanas de módulos abiertas: {clave_modulo: ventana}
        self.ventanas_modulos = {}
        # 🪟 Instancia de cada módulo abierto: sirve para ACTUALIZAR sus datos
        # cuando se vuelve a pulsar el módulo en el menú lateral.
        self.instancias_modulos = {}
        self.root.withdraw()
        self.abrir_ventana_login()

    def tiene_permiso(self, modulo_key):
        if self.rol_activo == "Super Administrador":
            return True
        return self.permisos_activos.get(modulo_key, False)

    def abrir_ventana_login(self):
        self.v_login = ctk.CTkToplevel(self.root)
        self.v_login.title("Acceso Seguro")
        aplicar_icono_ventana(self.v_login)
        ancho_ventana, alto_ventana = 400, 520
        self.v_login.geometry(f"{ancho_ventana}x{alto_ventana}")
        self.v_login.resizable(False, False)
        self.v_login.grab_set()
        self.v_login.protocol("WM_DELETE_WINDOW", self.root.quit)
        self.v_login.attributes("-topmost", True)
        self.v_login.update()
        self.v_login.attributes("-topmost", False)
        self.v_login.focus_force()
        self.v_login.update_idletasks()
        x = (self.v_login.winfo_screenwidth() // 2) - (ancho_ventana // 2)
        y = (self.v_login.winfo_screenheight() // 2) - (alto_ventana // 2)
        self.v_login.geometry(f"{ancho_ventana}x{alto_ventana}+{x}+{y}")
        frame_log = ctk.CTkFrame(self.v_login, corner_radius=15, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        frame_log.pack(fill="both", expand=True, padx=20, pady=20)
        self.lbl_logo = ctk.CTkLabel(frame_log, text="")
        self.lbl_logo.pack(pady=(20, 5))
        if PIL_DISPONIBLE:
            ruta_logo = ruta_recurso("Logo_Collie_Software.png")
            if os.path.exists(ruta_logo):
                try:
                    imagen_original = Image.open(ruta_logo)
                    self.tamanio_actual = 10
                    self.tamanio_maximo = 135
                    self.tamanio_final = 115
                    self.fase_animacion = "creciendo"

                    def animar_logo():
                        if self.fase_animacion == "creciendo":
                            self.tamanio_actual += 15
                            if self.tamanio_actual >= self.tamanio_maximo:
                                self.fase_animacion = "rebotando"
                        elif self.fase_animacion == "rebotando":
                            self.tamanio_actual -= 4
                            if self.tamanio_actual <= self.tamanio_final:
                                self.fase_animacion = "terminado"
                                self.tamanio_actual = self.tamanio_final
                        img_animada = ctk.CTkImage(light_image=imagen_original, dark_image=imagen_original, size=(self.tamanio_actual, self.tamanio_actual))
                        self.lbl_logo.configure(image=img_animada)
                        if self.fase_animacion != "terminado":
                            self.v_login.after(20, animar_logo)
                    animar_logo()
                except Exception:
                    self.lbl_logo.configure(text="[ Error cargando logo ]", font=("Arial", 10))
            else:
                self.lbl_logo.configure(text="[ Logo no encontrado ]", font=("Arial", 12, "italic"))
        else:
            self.lbl_logo.configure(text="[ Instalar Pillow para ver logo ]", font=("Arial", 10))
        ctk.CTkLabel(frame_log, text="SISTEMA DE CONTROL DE EVENTOS", font=("Arial", 15, "bold"), text_color="#1f538d").pack(pady=(0, 0))
        ctk.CTkLabel(frame_log, text="Inicio de Sesión", font=("Arial", 11, "italic"), text_color="#7f8c8d").pack(pady=(0, 20))
        ctk.CTkLabel(frame_log, text="Usuario:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=2)
        ent_user = ctk.CTkEntry(frame_log, width=300, font=("Arial", 12))
        ent_user.pack(pady=(0, 15))
        ent_user.focus()
        ctk.CTkLabel(frame_log, text="Contraseña:", font=("Arial", 11, "bold")).pack(anchor="w", padx=30, pady=2)
        ent_pass = ctk.CTkEntry(frame_log, width=300, font=("Arial", 12), show="*")
        ent_pass.pack(pady=(0, 25))
        ent_pass.bind("<Return>", lambda e: verificar_credenciales())

        def verificar_credenciales():
            user = ent_user.get().strip().lower()
            clave = ent_pass.get().strip()
            
            conn = conectar_db(silencioso=True)
            if not conn:
                messagebox.showinfo(
                    "📡 MODO LECTURA OFFLINE ACTIVADO",
                    "Sin conexión a Internet.\n\nIniciando en MODO LECTURA:\n"
                    "• Podrás revisar clientes, cotizaciones y eventos.\n"
                    "• No se permitirán modificaciones hasta reconectarte."
                )
                self.usuario_activo = user if user else "Invitado"
                self.rol_activo = "Invitado Offline"
                self.permisos_activos = {"clientes": True, "cotizaciones": True, "cronograma": True}
                if hasattr(cache_sistema, "cargar_copia_local"): cache_sistema.cargar_copia_local()
                cache_sistema.modo_lectura = True
                self.v_login.destroy()
                self.construir_dashboard_spa()
                return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT rol, permisos, clave_hash, activo FROM usuarios WHERE usuario = %s", (user,))
                resultado = cursor.fetchone()
                if not resultado:
                    messagebox.showerror("Acceso Denegado", "Usuario o contraseña incorrectos.")
                    registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión fallido (usuario no existe)")
                    ent_pass.delete(0, tk.END); ent_user.focus()
                    return
                rol_db, permisos_str, clave_hash, activo = resultado
                if not activo:
                    messagebox.showerror("Acceso Denegado", "Este usuario está desactivado.\nContacta al administrador.")
                    registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión con usuario desactivado")
                    return
                if not clave_hash or not verify_password(clave, clave_hash):
                    messagebox.showerror("Acceso Denegado", "Usuario o contraseña incorrectos.")
                    registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión fallido")
                    ent_pass.delete(0, tk.END); ent_user.focus()
                    return
                self.usuario_activo = user
                self.rol_activo = str(rol_db).strip()
                try: self.permisos_activos = json.loads(permisos_str) if permisos_str else {}
                except Exception: self.permisos_activos = {}
                registrar_auditoria(self.usuario_activo, "Seguridad", "Inicio de sesión exitoso")
                self.v_login.destroy()
                self.construir_dashboard_spa()
            except Exception as e:
                messagebox.showerror("Error Crítico", f"Ocurrió un problema en el sistema:\n{e}")
            finally:
                liberar_conexion(conn)

        btn_entrar = ctk.CTkButton(frame_log, text="Ingresar al Sistema", width=200, height=35, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=verificar_credenciales)
        btn_entrar.pack(pady=5)

    def construir_dashboard_spa(self):
        # 🪟 Al volver a entrar (o cambiar de usuario) se cierran las ventanas de módulos
        self.cerrar_todas_las_ventanas()
        for widget in self.root.winfo_children():
            widget.destroy()
        # ⛶ Se vuelve al modo normal (el menú lateral siempre debe estar visible al entrar)
        try:
            self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        self.modo_pantalla_completa = False
        config = cargar_configuracion_general()
        c_fondo = config.get("color_menu_fondo", "#1a252c")
        c_btn = config.get("color_menu_btn", "#1f538d")
        c_hover = config.get("color_menu_hover", "#163b65")
        c_texto = config.get("color_menu_texto", "white")
        fondo_seguro = c_fondo if str(c_fondo).startswith("#") else "#1a252c"
        default_fin = ["ventas", "compras", "bancos", "libro_diario", "libro_mayor", "impuestos", "dashboard"]
        orden_ops = config.get("orden_operativos", ["clientes", "cotizaciones", "pautas", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "inventario", "locaciones"])
        orden_fin = config.get("orden_finanzas", list(default_fin))
        orden_aju = config.get("orden_ajustes", ["configuracion", "usuarios", "bitacora"])
        todas_las_ordenes = orden_ops + orden_fin + orden_aju
        # Los módulos nuevos que aún no están en la configuración guardada se colocan
        # en su grupo natural (Bancos va en Finanzas, justo después de Compras, y no
        # en Operativos). Al guardar la Configuración General el orden queda fijo.
        for k in self.modulos_sistema:
            if k not in todas_las_ordenes:
                grupo = orden_fin if k in default_fin else orden_ops
                if k == "bancos" and "compras" in grupo:
                    grupo.insert(grupo.index("compras") + 1, k)
                else:
                    grupo.append(k)
        self.root.deiconify()
        maximizar_ventana(self.root)
        cache_sistema.iniciar_ciclo()
        
        self.root.after(5000, self.ciclo_sincronizacion_nube)
        self.buscar_actualizaciones_github()
        
        def pre_cargar_listas():
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    c = conn.cursor()
                    c.execute("SELECT nombre_empresa FROM clientes ORDER BY nombre_empresa ASC")
                    cache_sistema.guardar('lista_clientes_combobox', [str(r[0]).strip() for r in c.fetchall() if r[0]])
                    
                    c.execute("SELECT nombre FROM proveedores ORDER BY nombre ASC")
                    cache_sistema.guardar('lista_proveedores_combobox', [str(r[0]).strip() for r in c.fetchall() if r[0]])
                    
                    c.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                    evs = [f"{r[0]} | {r[1]}" for r in c.fetchall()]
                    evs.insert(0, "OFICINA | Trabajos Internos")
                    cache_sistema.guardar('lista_eventos_aprobados', evs)
                    
                    c.execute("SELECT numero_oc FROM ordenes_compra_clientes ORDER BY id DESC")
                    cache_sistema.guardar('lista_ocs_combobox', ["--- Sin Orden de Compra ---"] + [r[0] for r in c.fetchall()])
                except Exception: pass
                finally: liberar_conexion(conn)
            # 🏦 Las cuentas bancarias se leen de la base de datos una vez al entrar:
            # así los combos de Bancos, Ventas y Compras se abren al instante.
            try:
                cargar_bancos(usar_cache=False)
            except Exception:
                pass
        threading.Thread(target=pre_cargar_listas, daemon=True).start()
        
        self.root.attributes("-topmost", True); self.root.update(); self.root.attributes("-topmost", False)
        self.root.focus_force()
        self.sidebar = ctk.CTkFrame(self.root, width=280, corner_radius=0, fg_color=fondo_seguro)
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        self.contenedor_central = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        self.contenedor_central.pack(side="right", fill="both", expand=True)

        # ==================================================
        # 🪟 PESTAÑAS DE LOS MÓDULOS ABIERTOS (parte de abajo)
        # Cada módulo abierto aparece aquí como una pestaña: al pulsarla se trae al
        # frente y se actualiza; con su ✖ se cierra. Se arma sola.
        # ==================================================
        # Línea superior: separa visualmente la barra de pestañas del contenido
        self.linea_pestanas = ctk.CTkFrame(self.contenedor_central, height=2, corner_radius=0,
                                           fg_color=c_btn)
        self.linea_pestanas.pack(side="bottom", fill="x")
        self.barra_pestanas = ctk.CTkFrame(self.contenedor_central, corner_radius=0,
                                           fg_color=fondo_seguro, height=42)
        self.barra_pestanas.pack(side="bottom", fill="x")
        self.barra_pestanas.pack_propagate(False)
        self.lbl_pestanas = ctk.CTkLabel(self.barra_pestanas, text="🪟 Módulos abiertos: (ninguno)",
                                         font=("Arial", 10, "bold"), text_color="#7fb3d5")
        self.lbl_pestanas.pack(side="left", padx=(12, 6))
        # 🖱️ Aquí se dibujan las pestañas. Es un marco normal (no un CTkScrollableFrame):
        # dentro de una barra con pack_propagate(False) el marco con scroll se aplastaba
        # y las pestañas quedaban de 7 píxeles (invisibles).
        self.pestanas_area = ctk.CTkFrame(self.barra_pestanas, fg_color="transparent")
        self.pestanas_area.pack(side="left", fill="both", expand=True, padx=(0, 10), pady=4)
        # 🖱️ Pestañas dibujadas: {clave_modulo: marco de la pestaña}
        self.pestanas_modulos = {}
        # 🪟 Módulo que tiene el foco (su pestaña se pinta resaltada)
        self._modulo_activo = None
        frame_top_sidebar = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        frame_top_sidebar.pack(side="top", fill="x", pady=(10, 5))
        ruta_logo_sidebar = ruta_recurso("logo.png")
        if os.path.exists(ruta_logo_sidebar):
            try:
                self.logo_img = tk.PhotoImage(file=ruta_logo_sidebar)
                tk.Label(frame_top_sidebar, image=self.logo_img, bg=fondo_seguro).pack(pady=(15, 10))
            except Exception: pass
        ctk.CTkLabel(frame_top_sidebar, text=f"👤 {self.usuario_activo.upper()}", font=("Arial", 11, "bold"), text_color="#28a745").pack(pady=(0, 2))
        ctk.CTkLabel(frame_top_sidebar, text=f"Rol: {self.rol_activo}", font=("Arial", 10), text_color="white").pack(pady=(0, 5))
        frame_bottom_sidebar = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        frame_bottom_sidebar.pack(side="bottom", fill="x", pady=(5, 10))
        lbl_firma_sidebar = ctk.CTkLabel(frame_bottom_sidebar, text=f"Software desarrollado por Alfred Collie\nVersión {VERSION_ACTUAL} © 2026", font=("Arial", 9, "italic"), text_color="#7f8c8d")
        lbl_firma_sidebar.pack(side="bottom", pady=(2, 5))


        # ==================================================
        # ⛶ PANTALLA COMPLETA (oculta todo el menú de la izquierda)
        # ==================================================
        self.modo_pantalla_completa = False

        def _salir_de_pantalla_completa():
            """Restaura el menú lateral y el tamaño normal de la ventana."""
            self.modo_pantalla_completa = False
            try:
                self.root.attributes("-fullscreen", False)
            except Exception:
                pass
            try:
                self.btn_volver_pantalla.place_forget()
            except Exception:
                pass
            try:
                self.sidebar.pack(side="left", fill="y", before=self.contenedor_central)
                self.sidebar.pack_propagate(False)
            except Exception:
                pass
            try:
                self.btn_pantalla_completa.configure(text="⛶ Pantalla Completa")
            except Exception:
                pass
            maximizar_ventana(self.root)

        def alternar_pantalla_completa():
            """⛶ Pone la ventana a pantalla completa y esconde el menú lateral.

            Para volver se usa el botón flotante 'Salir de Pantalla Completa'
            (arriba a la derecha) o la tecla Escape / F11.
            """
            if getattr(self, "modo_pantalla_completa", False):
                _salir_de_pantalla_completa()
                return
            self.modo_pantalla_completa = True
            try:
                self.sidebar.pack_forget()          # se elimina todo el menú lateral
            except Exception:
                pass
            try:
                self.root.attributes("-fullscreen", True)
            except Exception:
                maximizar_ventana(self.root)
            try:
                self.btn_volver_pantalla.place(relx=1.0, y=10, x=-14, anchor="ne")
                self.btn_volver_pantalla.lift()
            except Exception:
                pass
            try:
                self.btn_pantalla_completa.configure(text="⛶ Salir de Pantalla Completa")
            except Exception:
                pass

        # Botón flotante: es la única forma de volver mientras el menú está oculto
        self.btn_volver_pantalla = ctk.CTkButton(
            self.root, text="⛶ Salir de Pantalla Completa", command=alternar_pantalla_completa,
            height=28, font=("Arial", 11, "bold"), fg_color="#2c3e50", hover_color="#1b2631")
        try:
            self.root.bind("<F11>", lambda _e: alternar_pantalla_completa())
            self.root.bind("<Escape>", lambda _e: _salir_de_pantalla_completa()
                           if getattr(self, "modo_pantalla_completa", False) else None)
        except Exception:
            pass

        def cerrar_sistema():
            registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró el sistema")
            self.cerrar_todas_las_ventanas()      # 🪟 se cierran los módulos abiertos
            self.root.quit(); self.root.destroy()

        def cambiar_usuario():
            registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró sesión para cambiar de usuario")
            self.cerrar_todas_las_ventanas()      # 🪟 se cierran los módulos abiertos
            self.usuario_activo = "No autenticado"
            self.rol_activo = "Ninguno"
            self.permisos_activos = {}
            self.root.withdraw()
            self.abrir_ventana_login()

        btn_salir = ctk.CTkButton(frame_bottom_sidebar, text="🚪 Salir del Sistema", command=cerrar_sistema, width=240, height=30, font=("Arial", 11, "bold"), fg_color="#c0392b", hover_color="#922b21")
        btn_salir.pack(side="bottom", pady=(2, 5))
        btn_cambio = ctk.CTkButton(frame_bottom_sidebar, text="🔄 Cambiar Usuario", command=cambiar_usuario, width=240, height=30, font=("Arial", 11, "bold"), fg_color="#555555", hover_color="#333333")
        btn_cambio.pack(side="bottom", pady=(2, 5))
        # ⛶ Queda arriba del botón "Cambiar Usuario"
        self.btn_pantalla_completa = ctk.CTkButton(
            frame_bottom_sidebar, text="⛶ Pantalla Completa", command=alternar_pantalla_completa,
            width=240, height=30, font=("Arial", 11, "bold"), fg_color="#2c3e50", hover_color="#1b2631")
        self.btn_pantalla_completa.pack(side="bottom", pady=(2, 5))
        linea_separadora = ctk.CTkFrame(frame_bottom_sidebar, height=2, fg_color="#34495e")
        linea_separadora.pack(side="bottom", fill="x", padx=20, pady=(5, 5))
        self.menu_scrollable = ctk.CTkScrollableFrame(self.sidebar, fg_color="transparent", scrollbar_button_color="#34495e")
        self.menu_scrollable.pack(side="top", fill="both", expand=True, padx=5, pady=0)

        def crear_btn_menu(texto, comando):
            btn = ctk.CTkButton(self.menu_scrollable, text=texto, command=comando, width=230, height=28, font=("Arial", 11, "bold"), fg_color=c_btn, hover_color=c_hover, text_color=c_texto, anchor="w")
            btn.pack(pady=2, padx=10)
            return btn

        grupos_render = [
            ("MÓDULOS OPERATIVOS", orden_ops),
            ("FINANZAS Y REPORTES", orden_fin),
            ("AJUSTES DE SISTEMA", orden_aju)
        ]
        for titulo_grupo, orden_grupo in grupos_render:
            modulos_permitidos = [m for m in orden_grupo if self.tiene_permiso(m)]
            if modulos_permitidos:
                espaciado_sup = 5 if titulo_grupo == "MÓDULOS OPERATIVOS" else 15
                ctk.CTkLabel(self.menu_scrollable, text=titulo_grupo, font=("Arial", 9, "bold"), text_color="#7f8c8d").pack(anchor="w", padx=15, pady=(espaciado_sup, 2))
                for key in modulos_permitidos:
                    if key in self.modulos_sistema and key in self.funciones_modulos:
                        # 🪟 Cada módulo se abre en su propia ventana (y si ya está
                        # abierto, esa ventana se trae al frente)
                        crear_btn_menu(self.modulos_sistema[key],
                                       lambda k=key: self.abrir_ventana_modulo(k))
        self.mostrar_pantalla_bienvenida()

    def ciclo_sincronizacion_nube(self):
        lanzar_sync_background()
        self.root.after(600000, self.ciclo_sincronizacion_nube) 

    # ==========================================================
    # 🪟 SISTEMA MULTI-VENTANA: una ventana por módulo
    # ==========================================================
    def abrir_ventana_modulo(self, clave):
        """Abre el módulo elegido en SU PROPIA VENTANA.

        El sistema es MULTI-VENTANA: cada módulo queda abierto en una ventana
        independiente (se pueden tener varios a la vez, incluso de módulos
        distintos) y solo se cierra cuando el usuario pulsa la X de esa ventana.
        Si el módulo ya está abierto, su ventana se trae al frente en lugar de
        abrirse dos veces.
        """
        if not self.tiene_permiso(clave):
            return messagebox.showerror("Acceso Denegado", "No tiene permisos para este módulo.")

        # Configuración General y Usuarios ya abren su propia ventana: si esa
        # ventana ya está abierta se trae al frente en lugar de abrir otra.
        if clave in ("configuracion", "usuarios"):
            if self._ventana_viva(self.ventanas_modulos.get(clave)):
                return self.traer_al_frente_ventana_modulo(clave)
            self.ventanas_modulos.pop(clave, None)
            abridor = self.funciones_modulos.get(clave)
            if abridor is not None:
                abridor()
            return

        # ¿Ya está abierto? -> se trae al frente y se ACTUALIZA la información
        if self._ventana_viva(self.ventanas_modulos.get(clave)):
            self.traer_al_frente_ventana_modulo(clave)
            self.refrescar_ventana_modulo(clave)
            return
        self.ventanas_modulos.pop(clave, None)
        self.instancias_modulos.pop(clave, None)

        creador = self.funciones_modulos.get(clave)
        if creador is None:
            return messagebox.showerror("Error", "Este módulo no está disponible.")

        titulo_modulo = self.modulos_sistema.get(clave, clave)
        ventana = ctk.CTkToplevel(self.root)
        ventana.title(f"{titulo_modulo}  |  Sistema de Control de Eventos")
        aplicar_icono_ventana(ventana)

        # 🪟 La ventana del módulo debe quedar DELANTE de la ventana principal.
        # CustomTkinter oculta y vuelve a mostrar la ventana para pintar la barra de
        # título (y a los 1000 ms vuelve a ajustar el tamaño): esos ciclos hacían que
        # el módulo quedara detrás. Por eso se trae al frente varias veces.
        # 🪟 OJO: no se usa 'transient' a propósito. Las ventanas de los módulos son
        # ventanas independientes y normales: así conservan sus botones de MINIMIZAR,
        # maximizar y cerrar (Windows les quita minimizar a las ventanas dependientes).
        try:
            ventana.resizable(True, True)
        except Exception:
            pass

        # 📐 Tamaño y posición: en cascada, para que no queden una encima de otra
        pantalla_w = ventana.winfo_screenwidth()
        pantalla_h = ventana.winfo_screenheight()
        ancho = min(1260, max(900, pantalla_w - 90))
        alto = min(800, max(600, pantalla_h - 110))
        posicion = len(self.ventanas_modulos)
        x = max(0, min(40 + posicion * 32, pantalla_w - ancho - 30))
        y = max(0, min(20 + posicion * 28, pantalla_h - alto - 60))
        ventana.geometry(f"{ancho}x{alto}+{x}+{y}")
        try:
            ventana.minsize(880, 560)
        except Exception:
            pass

        # ==================================================
        # 🪟 BARRA DE LA VENTANA: actualizar, minimizar, expandir y cerrar
        # (además de los botones normales de la barra de título del sistema)
        # ==================================================
        barra = ctk.CTkFrame(ventana, corner_radius=0, fg_color="#1a252c", height=34)
        barra.pack(side="top", fill="x")
        ctk.CTkLabel(barra, text=f"🪟 {titulo_modulo}", font=("Arial", 11, "bold"),
                     text_color="white", anchor="w").pack(side="left", padx=(10, 6), pady=5)

        def _cerrar(c=clave): self.cerrar_ventana_modulo(c)
        def _minimizar(c=clave): self.minimizar_ventana_modulo(c)
        def _expandir(c=clave): self.alternar_maximizar_ventana_modulo(c)
        def _actualizar(c=clave): self.refrescar_ventana_modulo(c, aviso=True)

        ctk.CTkButton(barra, text="✖ Cerrar", width=86, height=24, font=("Arial", 10, "bold"),
                      fg_color="#c0392b", hover_color="#922b21", command=_cerrar
                      ).pack(side="right", padx=(2, 8), pady=5)
        btn_expandir = ctk.CTkButton(barra, text="🗖 Expandir", width=98, height=24, font=("Arial", 10, "bold"),
                                     fg_color="#34495e", hover_color="#2c3e50", command=_expandir)
        btn_expandir.pack(side="right", padx=2, pady=5)
        ctk.CTkButton(barra, text="🗕 Minimizar", width=100, height=24, font=("Arial", 10, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50", command=_minimizar
                      ).pack(side="right", padx=2, pady=5)
        ctk.CTkButton(barra, text="🔄 Actualizar", width=105, height=24, font=("Arial", 10, "bold"),
                      fg_color="#1f538d", hover_color="#163b65", command=_actualizar
                      ).pack(side="right", padx=2, pady=5)
        ctk.CTkLabel(barra, text="Atajos: F5 actualizar · F11 expandir",
                     font=("Arial", 9, "italic"), text_color="#7f8c8d").pack(side="right", padx=10)

        contenedor = ctk.CTkFrame(ventana, corner_radius=0, fg_color="transparent")
        contenedor.pack(fill="both", expand=True)
        # Compatibilidad: algunos módulos llaman a estos métodos del marco contenedor
        def dummy(*args, **kwargs): pass
        for metodo in ("title", "geometry", "resizable", "iconbitmap"):
            if not hasattr(contenedor, metodo):
                setattr(contenedor, metodo, dummy)

        self.ventanas_modulos[clave] = ventana
        ventana._btn_expandir = btn_expandir          # para cambiar su texto al expandir
        ventana._modulo_maximizado = False
        # La X de la ventana cierra el módulo y lo quita de la lista de abiertos
        ventana.protocol("WM_DELETE_WINDOW", lambda c=clave: self.cerrar_ventana_modulo(c))
        ventana.bind("<Destroy>", lambda e, c=clave: self._al_destruir_ventana(e, c), add="+")
        # ⌨️ Atajos de la ventana del módulo: F5 actualiza · F11 expande o restaura
        ventana.bind("<F5>", lambda _e, c=clave: self.refrescar_ventana_modulo(c, aviso=True), add="+")
        ventana.bind("<F11>", lambda _e, c=clave: self.alternar_maximizar_ventana_modulo(c), add="+")
        # 🪟 Al usar esta ventana, su pestaña de abajo queda resaltada
        ventana.bind("<FocusIn>", lambda _e, c=clave: self._marcar_modulo_activo(c), add="+")

        try:
            # La función creadora devuelve la instancia del módulo: se guarda para
            # poder ACTUALIZAR sus datos cuando se vuelva a abrir el módulo.
            instancia = creador(contenedor)
            if instancia is not None:
                self.instancias_modulos[clave] = instancia
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al abrir {titulo_modulo}:\n{e}")
            self.cerrar_ventana_modulo(clave)
            return

        # 🪟 Botón propio en la barra de tareas (Windows), ventana al frente y
        # pestaña del módulo en la barra de abajo.
        poner_ventana_en_barra_tareas(ventana)
        traer_al_frente(ventana)
        self._modulo_activo = clave          # su pestaña queda resaltada
        self._actualizar_pestanas_modulos()

        registrar_auditoria(self.usuario_activo, "Sistema", f"Abrió el módulo {titulo_modulo}")

    # ------------------------------------------------------------------
    # 🪟 ACCIONES SOBRE LAS VENTANAS DE LOS MÓDULOS
    # ------------------------------------------------------------------
    def _ventana_viva(self, ventana):
        """¿La ventana existe todavía?"""
        if ventana is None:
            return False
        try:
            return bool(ventana.winfo_exists())
        except Exception:
            return False

    def traer_al_frente_ventana_modulo(self, clave):
        """🪟 Restaura (si estaba minimizada), trae al frente y enfoca la ventana."""
        ventana = self.ventanas_modulos.get(clave)
        if not self._ventana_viva(ventana):
            return
        try:
            if str(ventana.state()) == "iconic":
                ventana.deiconify()          # estaba minimizada: se restaura
        except Exception:
            pass
        try:
            ventana.deiconify()
        except Exception:
            pass
        try:
            ventana.lift()
        except Exception:
            pass
        # Un 'topmost' momentáneo: garantiza que quede delante de la ventana
        # principal (CustomTkinter repinta la barra de título y la manda atrás).
        def _soltar():
            try:
                if self._ventana_viva(ventana):
                    ventana.attributes("-topmost", False)
            except Exception:
                pass
        try:
            ventana.attributes("-topmost", True)
            ventana.after(400, _soltar)
        except Exception:
            pass
        try:
            ventana.focus_force()
        except Exception:
            pass

    def refrescar_ventana_modulo(self, clave, aviso=False):
        """🔄 Vuelve a cargar los datos del módulo abierto.

        Se llama al pulsar otra vez el módulo en el menú lateral (o el botón
        '🔄 Actualizar' de la ventana), para que la información esté al día.
        """
        instancia = self.instancias_modulos.get(clave)
        if instancia is None:
            if aviso:
                messagebox.showinfo("Actualizar", "Este módulo no tiene datos para actualizar.")
            return 0

        def _hacer():
            recargados = refrescar_instancia_modulo(instancia)
            if aviso:
                if recargados:
                    messagebox.showinfo("Actualizar", "✅ Información actualizada.")
                else:
                    messagebox.showinfo("Actualizar",
                                        "El módulo ya está al día (usa sus propios botones de actualizar).")

        try:
            self.root.after(60, _hacer)      # se deja que la ventana pinte primero
        except Exception:
            _hacer()
        return True

    def minimizar_ventana_modulo(self, clave):
        """🗕 Minimiza la ventana del módulo (queda en la barra de tareas)."""
        ventana = self.ventanas_modulos.get(clave)
        if not self._ventana_viva(ventana):
            return
        try:
            ventana.iconify()
        except Exception:
            pass

    def alternar_maximizar_ventana_modulo(self, clave):
        """🗖 Expande la ventana del módulo a toda la pantalla y la vuelve a su
        tamaño anterior si ya estaba expandida."""
        ventana = self.ventanas_modulos.get(clave)
        if not self._ventana_viva(ventana):
            return
        boton = getattr(ventana, "_btn_expandir", None)
        if getattr(ventana, "_modulo_maximizado", False):
            # 🔽 Restaurar: se vuelve al tamaño y posición que tenía antes
            try:
                if sys.platform == "win32":
                    ventana.state("normal")
            except Exception:
                pass
            geometria = getattr(ventana, "_modulo_geometria_previa", None)
            if geometria:
                try:
                    ventana.geometry(geometria)
                except Exception:
                    pass
            ventana._modulo_maximizado = False
            try:
                if boton is not None:
                    boton.configure(text="🗖 Expandir")
            except Exception:
                pass
        else:
            try:
                ventana._modulo_geometria_previa = ventana.geometry()
            except Exception:
                pass
            maximizar_ventana(ventana)
            ventana._modulo_maximizado = True
            try:
                if boton is not None:
                    boton.configure(text="🗗 Restaurar")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 🪟 PESTAÑAS DE MÓDULOS ABIERTOS (parte inferior de la ventana)
    # ------------------------------------------------------------------
    def _etiqueta_corta_modulo(self, clave):
        """Nombre corto del módulo para la pestaña: '💼 Ventas (Facturas y Cobros)' -> 'Ventas'."""
        titulo = str(self.modulos_sistema.get(clave, clave) or clave).strip()
        partes = titulo.split(" ", 1)
        if len(partes) == 2 and not partes[0][:1].isalnum():
            titulo = partes[1]          # se quita el icono del principio
        if "(" in titulo:
            titulo = titulo.split("(")[0]
        titulo = titulo.strip() or clave
        if len(titulo) > 16:                 # los nombres largos se abrevian
            titulo = titulo[:15].rstrip() + "…"
        return titulo

    def _actualizar_pestanas_modulos(self):
        """Redibuja las pestañas de los módulos abiertos (parte de abajo)."""
        contenedor = getattr(self, "pestanas_area", None)
        if contenedor is None:
            return
        try:
            for widget in contenedor.winfo_children():
                widget.destroy()
            self.pestanas_modulos = {}

            abiertos = [(c, v) for c, v in (getattr(self, "ventanas_modulos", {}) or {}).items()
                        if self._ventana_viva(v)]
            try:
                if abiertos:
                    self.lbl_pestanas.configure(text=f"🪟 Módulos abiertos ({len(abiertos)}):")
                else:
                    self.lbl_pestanas.configure(text="🪟 Módulos abiertos: (ninguno)")
            except Exception:
                pass

            # 📐 Ancho de cada pestaña: se reparte el espacio disponible para que
            # TODAS se vean, aunque haya muchos módulos abiertos.
            ancho_area = contenedor.winfo_width()
            if ancho_area < 120:                 # todavía no se dibujó el área
                try:
                    ancho_area = max(420, self.root.winfo_width() - 300)
                except Exception:
                    ancho_area = 800
            cuantas = max(1, len(abiertos))
            ancho_fila = max(46, (ancho_area - 12) // cuantas - 6)
            mostrar_cerrar = ancho_fila >= 104   # en pestañas angostas no cabe la ✖
            ancho_boton = max(28, ancho_fila - (30 if mostrar_cerrar else 10))
            # Cuántos caracteres caben en el nombre (letra de 10 px ≈ 7,2 px por carácter)
            limite_txt = max(3, int(ancho_boton / 7.2) - 1)

            activo = getattr(self, "_modulo_activo", None)
            for clave, _ventana in abiertos:
                es_activo = (clave == activo)
                color_fondo = "#1f538d" if es_activo else "#22303a"
                color_hover = "#163b65" if es_activo else "#34495e"
                titulo = self._etiqueta_corta_modulo(clave)
                if len(titulo) > limite_txt:
                    titulo = titulo[:limite_txt].rstrip() + "…"
                fila = ctk.CTkFrame(contenedor, fg_color=color_fondo, corner_radius=6,
                                    width=ancho_fila, height=30)
                fila.pack(side="left", padx=3, pady=2)
                fila.pack_propagate(False)
                ctk.CTkButton(fila, text=titulo, width=ancho_boton, height=26, corner_radius=4,
                              font=("Arial", 10, "bold"), fg_color="transparent", text_color="white",
                              hover_color=color_hover, anchor="w",
                              command=lambda c=clave: self._ir_a_modulo(c)
                              ).pack(side="left", padx=(4, 2), pady=2)
                if mostrar_cerrar:
                    ctk.CTkButton(fila, text="✖", width=20, height=20, corner_radius=4,
                                  font=("Arial", 9, "bold"), fg_color="#c0392b", hover_color="#922b21",
                                  command=lambda c=clave: self.cerrar_ventana_modulo(c)
                                  ).pack(side="left", padx=(0, 4), pady=2)
                self.pestanas_modulos[clave] = fila
        except Exception:
            pass

    def _marcar_modulo_activo(self, clave):
        """Resalta la pestaña del módulo que tiene el foco."""
        if getattr(self, "_modulo_activo", None) == clave:
            return
        self._modulo_activo = clave
        self._actualizar_pestanas_modulos()

    def _ir_a_modulo(self, clave):
        """Trae al frente la ventana de un módulo y actualiza su información."""
        self._modulo_activo = clave
        self.traer_al_frente_ventana_modulo(clave)
        self.refrescar_ventana_modulo(clave)
        self._actualizar_pestanas_modulos()

    def _al_destruir_ventana(self, evento, clave):
        """Quita la ventana del registro cuando se cierra (la X o el sistema)."""
        try:
            if evento.widget is not self.ventanas_modulos.get(clave):
                return          # el evento es de un widget hijo, no de la ventana
        except Exception:
            pass
        self.ventanas_modulos.pop(clave, None)
        self.instancias_modulos.pop(clave, None)
        self._actualizar_pestanas_modulos()

    def cerrar_ventana_modulo(self, clave):
        """❌ Cierra la ventana de un módulo (la X de la ventana o los botones)."""
        ventana = self.ventanas_modulos.pop(clave, None)
        self.instancias_modulos.pop(clave, None)
        if ventana is not None:
            try:
                ventana.destroy()
            except Exception:
                pass
        self._actualizar_pestanas_modulos()

    def cerrar_todas_las_ventanas(self):
        """Cierra todas las ventanas de módulos abiertas (al cambiar de usuario o salir)."""
        for clave in list(getattr(self, "ventanas_modulos", {}) or {}):
            self.cerrar_ventana_modulo(clave)
        self.ventanas_modulos = {}
        self.instancias_modulos = {}
        self._actualizar_pestanas_modulos()

    def limpiar_contenedor(self):
        barra_pestanas = getattr(self, "barra_pestanas", None)
        for widget in self.contenedor_central.winfo_children():
            if widget is barra_pestanas:
                continue          # 🪟 las pestañas de módulos abiertos siempre quedan
            widget.destroy()
        def dummy(*args, **kwargs): pass
        if not hasattr(self.contenedor_central, 'title'): self.contenedor_central.title = dummy
        if not hasattr(self.contenedor_central, 'geometry'): self.contenedor_central.geometry = dummy
        if not hasattr(self.contenedor_central, 'resizable'): self.contenedor_central.resizable = dummy
        if not hasattr(self.contenedor_central, 'iconbitmap'): self.contenedor_central.iconbitmap = dummy

    def mostrar_pantalla_bienvenida(self):
        self.limpiar_contenedor()
        f_dashboard = ctk.CTkScrollableFrame(self.contenedor_central, fg_color="transparent")
        f_dashboard.pack(fill="both", expand=True, padx=20, pady=20)
        f_header = ctk.CTkFrame(f_dashboard, fg_color="transparent")
        f_header.pack(fill="x", pady=(0, 20))
        ctk.CTkLabel(f_header, text=f"👋 Hola, {self.usuario_activo.upper()}", font=("Arial", 24, "bold"), text_color="#1f538d").pack(side="left")
        ctk.CTkLabel(f_header, text=f"{datetime.now().strftime('%d/%m/%Y')}", font=("Arial", 16, "bold"), text_color="gray").pack(side="right")
        
        ctk.CTkLabel(f_dashboard, text="🔔 PANEL DE NOTIFICACIONES Y ALERTAS", font=("Arial", 14, "bold"), text_color="#d35400").pack(anchor="w", pady=(0, 10))
        
        self.f_alertas_container = ctk.CTkFrame(f_dashboard, fg_color="transparent")
        self.f_alertas_container.pack(fill="x")
        
        ctk.CTkLabel(self.f_alertas_container, text="Buscando alertas del sistema... ⏳", font=("Arial", 12, "italic"), text_color="gray").pack(pady=10)

        ctk.CTkLabel(f_dashboard, text="📅 PRÓXIMOS EVENTOS (AGENDA)", font=("Arial", 14, "bold"), text_color="#27ae60").pack(anchor="w", pady=(25, 10))
        self.f_agenda_container = ctk.CTkFrame(f_dashboard, fg_color="#f8f9fa", corner_radius=6, border_width=1, border_color="#e0e0e0")
        self.f_agenda_container.pack(fill="x", pady=0)
        ctk.CTkLabel(self.f_agenda_container, text="Cargando agenda... ⏳", font=("Arial", 12, "italic"), text_color="gray").pack(pady=15)

        f_pie = ctk.CTkFrame(f_dashboard, fg_color="transparent")
        f_pie.pack(fill="x", pady=(40, 20))
        ctk.CTkLabel(f_pie, text="Bienvenido", font=("Arial", 14, "bold"), text_color="gray").pack()
        ctk.CTkLabel(f_pie, text="Este es el resumen automático de tu operación para el día de hoy.\nSeleccione un módulo en el menú lateral para gestionar sus operaciones.", font=("Arial", 12, "italic"), text_color="gray", justify="center").pack(pady=(5, 0))

        def tarea_dashboard():
            hoy = datetime.now()
            alertas = []
            tareas_mostrar = []
            
            conn = conectar_db(silencioso=True)
            if not conn:
                self.root.after(0, lambda: self._pintar_offline())
                return
                
            try:
                cursor = conn.cursor()
                
                # RADAR: DEUDAS FINANCIERAS (Ventas)
                if self.tiene_permiso("ventas"):
                    try:
                        cursor.execute("SELECT id, fecha, dias_credito, total, COALESCE(det_monto, 0) FROM facturas_emitidas")
                        facturas_ventas = cursor.fetchall()
                        cursor.execute("SELECT id_factura, SUM(monto_pagado) FROM pagos_clientes GROUP BY id_factura")
                        pagos_ventas = {row[0]: float(row[1]) for row in cursor.fetchall()}
                        ventas_vencidas, deuda_ventas = 0, 0.0
                        for f in facturas_ventas:
                            f_id, fecha_str, dias, total, det = f
                            try:
                                f_dt = datetime.strptime(fecha_str, "%d/%m/%Y")
                                vencimiento = f_dt + timedelta(days=dias)
                                neto = float(total if total else 0) - float(det if det else 0)
                                saldo = neto - pagos_ventas.get(f_id, 0.0)
                                if saldo > 0.01 and hoy > vencimiento:
                                    ventas_vencidas += 1; deuda_ventas += saldo
                            except Exception: pass
                        if ventas_vencidas > 0: alertas.append({"tipo": "peligro", "icono": "⚠️", "titulo": "Cobros Vencidos", "mensaje": f"Tienes {ventas_vencidas} factura(s) de clientes atrasada(s) (S/. {deuda_ventas:,.2f})"})
                    except Exception: pass
                    
                # RADAR: PAGOS FINANCIEROS (Compras)
                if self.tiene_permiso("compras"):
                    try:
                        cursor.execute("SELECT id, fecha, dias_credito, total, COALESCE(det_monto, 0), tipo_documento, impuesto FROM facturas_recibidas")
                        facturas_compras = cursor.fetchall()
                        cursor.execute("SELECT id_factura, SUM(monto_pagado) FROM pagos_comprobantes GROUP BY id_factura")
                        pagos_compras = {row[0]: float(row[1]) for row in cursor.fetchall()}
                        compras_vencidas, deuda_compras = 0, 0.0
                        for f in facturas_compras:
                            f_id, fecha_str, dias, total, det, tipo_doc, imp = f
                            try:
                                f_dt = datetime.strptime(fecha_str, "%d/%m/%Y")
                                vencimiento = f_dt + timedelta(days=dias)
                                tot_bruto = float(total if total else 0.0)
                                det_monto = float(det if det else 0.0)
                                imp_val = float(imp if imp else 0.0)
                                if tipo_doc and "Recibo" in tipo_doc and "8%" in tipo_doc: neto = tot_bruto - imp_val - det_monto
                                else: neto = tot_bruto - det_monto
                                saldo = neto - pagos_compras.get(f_id, 0.0)
                                if saldo > 0.01 and hoy > vencimiento:
                                    compras_vencidas += 1; deuda_compras += saldo
                            except Exception: pass
                        if compras_vencidas > 0: alertas.append({"tipo": "peligro", "icono": "🚨", "titulo": "Pagos Vencidos", "mensaje": f"Tienes {compras_vencidas} factura(s) de proveedores atrasada(s) (S/. {deuda_compras:,.2f})"})
                    except Exception: pass
                    
                # RADAR: IMPUESTOS SUNAT
                if self.tiene_permiso("impuestos"):
                    try:
                        mes_ant = hoy.month - 1; anio_ant = hoy.year
                        if mes_ant == 0: mes_ant = 12; anio_ant -= 1
                        periodo_ant = f"{mes_ant:02d}/{anio_ant}"
                        cursor.execute("SELECT COUNT(*) FROM registro_impuestos WHERE periodo = %s", (periodo_ant,))
                        if cursor.fetchone()[0] == 0: alertas.append({"tipo": "alerta", "icono": "🏦", "titulo": "Impuestos SUNAT", "mensaje": f"Declaración pendiente del mes anterior ({periodo_ant})."})
                    except Exception: pass
                    
                # RADAR: INVENTARIO Y ALMACÉN
                if self.tiene_permiso("inventario"):
                    try:
                        cursor.execute("SELECT COUNT(*) FROM inventario_equipos WHERE estado != 'Operativo'")
                        equipos_malos = cursor.fetchone()[0]
                        if equipos_malos > 0: alertas.append({"tipo": "alerta", "icono": "🛠️", "titulo": "Almacén", "mensaje": f"Tienes {equipos_malos} equipo(s) en mantenimiento o dados de baja."})
                    except Exception: pass
                    
                # RADAR: COTIZACIONES SIN OC
                if self.tiene_permiso("ordenes_cliente"):
                    try:
                        cursor.execute("""
                            SELECT COUNT(*) FROM cotizaciones c
                            WHERE c.status = 'Aprobada' AND NOT EXISTS (SELECT 1 FROM ordenes_compra_clientes o WHERE o.cotizacion_asociada = c.codigo)
                        """)
                        cots_sin_orden = cursor.fetchone()[0]
                        if cots_sin_orden > 0: alertas.append({"tipo": "alerta", "icono": "📥", "titulo": "Órdenes de Cliente Pendientes", "mensaje": f"Tienes {cots_sin_orden} cotización(es) aprobada(s) que aún no tienen Orden de Compra subida."})
                    except Exception: pass
                    
                # AGENDA Y EVENTOS FUTUROS
                try:
                    cursor.execute("SELECT codigo_cotizacion, nombre_evento, fecha_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                    for ev in cursor.fetchall():
                        cod, nom, fec_str = ev
                        try: f_dt = datetime.strptime(fec_str, "%d/%m/%Y").date()
                        except Exception:
                            try: f_dt = datetime.strptime(fec_str, "%Y-%m-%d").date()
                            except Exception: f_dt = None
                            
                        if f_dt and f_dt >= hoy.date():
                            dias_faltan = (f_dt - hoy.date()).days
                            if dias_faltan == 0: texto_dias, color_t = "¡ES HOY!", "#d35400"
                            elif dias_faltan <= 5: texto_dias, color_t = f"Faltan {dias_faltan} días", "#c0392b"
                            else: texto_dias, color_t = f"Faltan {dias_faltan} días", "#1f538d"
                            tareas_mostrar.append((color_t, f"📍 {f_dt.strftime('%d/%m/%Y')} | {nom} ({cod}) - {texto_dias}"))
                except Exception: pass

            except Exception as e:
                print("Error general de alertas:", e)
            finally:
                liberar_conexion(conn)

            self.root.after(0, lambda: self._pintar_alertas(alertas, tareas_mostrar))

        threading.Thread(target=tarea_dashboard, daemon=True).start()

    def _pintar_offline(self):
        for widget in self.f_alertas_container.winfo_children(): widget.destroy()
        ctk.CTkLabel(self.f_alertas_container, text="📡 MODO LECTURA OFFLINE: Mostrando información desde el respaldo local.", font=("Arial", 12, "bold"), text_color="#d35400").pack(anchor="w", pady=5)
        for widget in self.f_agenda_container.winfo_children(): widget.destroy()
        
        evs_mem = getattr(cache_sistema, 'eventos_aprobados', [])
        if evs_mem:
            ctk.CTkLabel(self.f_agenda_container, text="📌 EVENTOS GUARDADOS EN MEMORIA:", font=("Arial", 12, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
            for ev_txt in evs_mem: ctk.CTkLabel(self.f_agenda_container, text=f"📍 {ev_txt}", font=("Arial", 12)).pack(anchor="w", padx=25, pady=3)
        else:
            ctk.CTkLabel(self.f_agenda_container, text="No hay eventos guardados en la memoria local aún.", font=("Arial", 12, "italic"), text_color="gray").pack(pady=15)

    def _pintar_alertas(self, alertas, tareas_mostrar):
        for widget in self.f_alertas_container.winfo_children(): widget.destroy()
        for widget in self.f_agenda_container.winfo_children(): widget.destroy()
        
        if not alertas:
            f_ok = ctk.CTkFrame(self.f_alertas_container, fg_color="#d4edda", corner_radius=6, border_width=1, border_color="#c3e6cb", height=40)
            f_ok.pack(fill="x", pady=5)
            ctk.CTkLabel(f_ok, text="✅ Todo está al día. No hay notificaciones pendientes.", font=("Arial", 12, "bold"), text_color="#155724").pack(pady=8)
        else:
            for al in alertas:
                if al["tipo"] == "peligro": c_fondo_al, c_borde_al, c_texto_al = "#f8d7da", "#f5c6cb", "#721c24"
                elif al["tipo"] == "alerta": c_fondo_al, c_borde_al, c_texto_al = "#fff3cd", "#ffeeba", "#856404"
                else: c_fondo_al, c_borde_al, c_texto_al = "#d1ecf1", "#bee5eb", "#0c5460"
                f_al = ctk.CTkFrame(self.f_alertas_container, fg_color=c_fondo_al, corner_radius=6, border_width=1, border_color=c_borde_al)
                f_al.pack(fill="x", pady=4)
                ctk.CTkLabel(f_al, text=f"{al['icono']} {al['titulo']}:", font=("Arial", 12, "bold"), text_color=c_texto_al).pack(side="left", padx=(10, 5), pady=6)
                ctk.CTkLabel(f_al, text=al["mensaje"], font=("Arial", 12), text_color=c_texto_al).pack(side="left", padx=(0, 10), pady=6)
                
        if not tareas_mostrar:
            ctk.CTkLabel(self.f_agenda_container, text="No hay eventos próximos programados en la agenda.", font=("Arial", 12, "italic"), text_color="gray").pack(pady=15)
        else:
            for color, txt in tareas_mostrar:
                ctk.CTkLabel(self.f_agenda_container, text=txt, font=("Arial", 12, "bold"), text_color=color).pack(anchor="w", padx=15, pady=8)

    def _crear_modulo_pautas(self, contenedor):
        try:
            import pautas_evento
            importlib.reload(pautas_evento)
            app = pautas_evento.PautasEventoApp(contenedor, self.usuario_activo)
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_ventas(self, contenedor):
        try:
            import modulo_ventas
            importlib.reload(modulo_ventas)
            app = modulo_ventas.ModuloVentasApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_compras(self, contenedor):
        try:
            import modulo_compras
            importlib.reload(modulo_compras)
            app = modulo_compras.ModuloComprasApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_bancos(self, contenedor):
        """🏦 Módulo de Bancos: saldos por cuenta y conciliación bancaria."""
        try:
            import modulo_banco
            importlib.reload(modulo_banco)
            app = modulo_banco.ModuloBancoApp(contenedor, self.usuario_activo)
            app.usuario_activo = self.usuario_activo
            registrar_auditoria(self.usuario_activo, "Bancos", "Abrió el módulo de Bancos (saldos y conciliación)")
        except Exception as e:
            messagebox.showerror("Error", f"Fallo al abrir el módulo de Bancos:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_ordenes(self, contenedor):
        try:
            import ordenes_compra
            importlib.reload(ordenes_compra)
            app = ordenes_compra.OrdenesCompraApp(contenedor, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_ordenes_cliente(self, contenedor):
        try:
            import ordenes_compra_cliente
            importlib.reload(ordenes_compra_cliente)
            app = ordenes_compra_cliente.OrdenesCompraClienteApp(contenedor, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_inventario(self, contenedor):
        try:
            import inventario
            importlib.reload(inventario)
            app = inventario.InventarioApp(contenedor, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_locaciones(self, contenedor):
        try:
            try:
                import inventario_locacion as mod_loc
            except ImportError:
                import Inventario_locacion as mod_loc
            importlib.reload(mod_loc)
            app = mod_loc.InventarioLocacionesApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_estadisticas_financiera(self, contenedor):
        try:
            import estadisticas_financiera
            importlib.reload(estadisticas_financiera)
            app = estadisticas_financiera.EstadisticasFinancieraApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_calculo_impuestos(self, contenedor):
        try:
            import calculo_impuestos
            importlib.reload(calculo_impuestos)
            app = calculo_impuestos.CalculoImpuestosApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_proveedores(self, contenedor):
        try:
            import proveedores
            importlib.reload(proveedores)
            app = proveedores.SistemaProveedores(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_solicitud_proveedor(self, contenedor):
        try:
            import solicitud_proveedor
            importlib.reload(solicitud_proveedor)
            app = solicitud_proveedor.SolicitudProveedorApp(contenedor, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_libro_diario(self, contenedor):
        try:
            import libro_diario
            importlib.reload(libro_diario)
            app = libro_diario.LibroDiarioApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_libro_mayor(self, contenedor):
        try:
            import libro_mayor
            importlib.reload(libro_mayor)
            app = libro_mayor.LibroMayorApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_clientes(self, contenedor):
        try:
            import clientes
            importlib.reload(clientes)
            app = clientes.SistemaClientes(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_cotizaciones(self, contenedor):
        try:
            import cotizaciones
            importlib.reload(cotizaciones)
            app = cotizaciones.VentanaCotizaciones(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_cronograma(self, contenedor):
        try:
            import cronograma_tareas
            importlib.reload(cronograma_tareas)
            app = cronograma_tareas.CronogramaApp(contenedor)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    def _crear_modulo_bitacora(self, contenedor):
        try:
            import bitacora
            importlib.reload(bitacora)
            bitacora.BitacoraApp(contenedor)
            registrar_auditoria(self.usuario_activo, "Bitácora", "Accedió a revisar el historial de auditoría")
        except Exception as e: messagebox.showerror("Error", f"No se pudo cargar la Bitácora:\n{e}")
        return locals().get("app")          # 🪟 instancia del módulo (None si falló)

    # =======================================================
    # CONFIGURACIÓN GENERAL (EVENTOS) - 100% COMPLETA
    # =======================================================
    def abrir_configuracion_general(self):
        if not self.tiene_permiso("configuracion"):
            return messagebox.showerror("Acceso Denegado", "No tiene permisos para modificar la configuración.")
        v_conf = ctk.CTkToplevel(self.root)
        v_conf.title("Configuración General del Sistema")
        v_conf.geometry("1000x750")
        v_conf.after(100, lambda: maximizar_ventana(v_conf))
        # 🪟 Ya NO se usa grab_set(): el sistema es multi-ventana y esta ventana no
        # debe bloquear los demás módulos que queden abiertos.
        # 🪟 Se registra como un módulo más: aparece en "Módulos abiertos" y si se
        # vuelve a pulsar en el menú se trae al frente en vez de abrir otra copia.
        self.ventanas_modulos["configuracion"] = v_conf
        v_conf.bind("<Destroy>", lambda e, c="configuracion": self._al_destruir_ventana(e, c), add="+")
        self._actualizar_pestanas_modulos()
        traer_al_frente(v_conf)
        archivo_config = str(CONFIG_FILE)
        config_actual = cargar_configuracion_general()
        todas_guardadas = (
            config_actual.get("orden_operativos", []) +
            config_actual.get("orden_finanzas", []) +
            config_actual.get("orden_ajustes", [])
        )
        default_fin_cfg = ["ventas", "compras", "bancos", "libro_diario", "libro_mayor", "impuestos", "dashboard"]
        for k in self.modulos_sistema:
            if k not in todas_guardadas:
                grupo = "orden_finanzas" if k in default_fin_cfg else "orden_operativos"
                lista_grupo = config_actual.setdefault(grupo, [])
                if k == "bancos" and "compras" in lista_grupo:
                    lista_grupo.insert(lista_grupo.index("compras") + 1, k)
                else:
                    lista_grupo.append(k)
        f_header = ctk.CTkFrame(v_conf, fg_color="transparent")
        f_header.pack(fill="x", padx=25, pady=(20, 10))
        ctk.CTkLabel(f_header, text="⚙️ Configuración General del Sistema", font=("Arial", 22, "bold"), text_color="#1f538d").pack(side="left")
        ctk.CTkButton(f_header, text="❌ Cerrar Configuración", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=v_conf.destroy).pack(side="right")
        f_scroll = ctk.CTkScrollableFrame(v_conf, fg_color="transparent")
        f_scroll.pack(fill="both", expand=True, padx=20, pady=10)
        ctk.CTkLabel(f_scroll, text="Ajustes locales guardados específicamente para este equipo.", font=("Arial", 12, "italic"), text_color="gray").pack(anchor="w", padx=10, pady=(0, 15))
        
        # ---------- 1. EMPRESA Y TRIBUTACIÓN ----------
        f_empresa = ctk.CTkFrame(f_scroll, corner_radius=10)
        f_empresa.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_empresa, text="🏢 Datos de tu Empresa y Tributación", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 10))
        f_row1 = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_row1.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_row1, text="RUC Empresa:\n(Enter para SUNAT)", font=("Arial", 11, "bold"), width=120, anchor="w").pack(side="left")
        ent_ruc_empresa = ctk.CTkEntry(f_row1, width=150)
        ent_ruc_empresa.pack(side="left", padx=5)
        ent_ruc_empresa.insert(0, config_actual.get("ruc_empresa", ""))
        ctk.CTkLabel(f_row1, text="Razón Social:", font=("Arial", 11, "bold"), width=100, anchor="w").pack(side="left", padx=(15, 5))
        ent_razon_social = ctk.CTkEntry(f_row1, width=280)
        ent_razon_social.pack(side="left", padx=5, fill="x", expand=True)
        ent_razon_social.insert(0, config_actual.get("razon_social_empresa", ""))
        f_row1_5 = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_row1_5.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_row1_5, text="Régimen Tributario:", font=("Arial", 11, "bold"), width=120, anchor="w").pack(side="left")
        regimenes_peru = ["NRUS - Nuevo Régimen Único Simplificado", "RER - Régimen Especial de Renta", "MYPE Tributario", "Régimen General"]
        cmb_regimen = ctk.CTkOptionMenu(f_row1_5, values=regimenes_peru, width=300)
        cmb_regimen.pack(side="left", padx=5)
        reg_guardado = config_actual.get("regimen_empresa", "MYPE Tributario")
        cmb_regimen.set(reg_guardado if reg_guardado in regimenes_peru else regimenes_peru[2])
        f_row2 = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_row2.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(f_row2, text="IGV (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        ent_igv = ctk.CTkEntry(f_row2, width=60)
        ent_igv.pack(side="left", padx=5)
        ent_igv.insert(0, config_actual.get("igv_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Detracción (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_detraccion = ctk.CTkEntry(f_row2, width=60)
        ent_detraccion.pack(side="left", padx=5)
        ent_detraccion.insert(0, config_actual.get("detraccion_porcentaje", "12"))
        ctk.CTkLabel(f_row2, text="Renta Mensual (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_renta_m = ctk.CTkEntry(f_row2, width=60)
        ent_renta_m.pack(side="left", padx=5)
        ent_renta_m.insert(0, config_actual.get("renta_mensual_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Renta Anual (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_renta_a = ctk.CTkEntry(f_row2, width=60)
        ent_renta_a.pack(side="left", padx=5)
        ent_renta_a.insert(0, config_actual.get("renta_anual_porcentaje", "0"))
        f_row3 = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_row3.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(f_row3, text="Última Factura SUNAT:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        ent_ult_fac = ctk.CTkEntry(f_row3, width=90, placeholder_text="F001-0")
        ent_ult_fac.pack(side="left", padx=5)
        ent_ult_fac.insert(0, config_actual.get("ultimo_factura", "F001-0"))
        ctk.CTkLabel(f_row3, text="Última Boleta:", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_ult_bol = ctk.CTkEntry(f_row3, width=90, placeholder_text="B001-0")
        ent_ult_bol.pack(side="left", padx=5)
        ent_ult_bol.insert(0, config_actual.get("ultimo_boleta", "B001-0"))
        ctk.CTkLabel(f_row3, text="Último Recibo (RH):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_ult_rec = ctk.CTkEntry(f_row3, width=90, placeholder_text="E001-0")
        ent_ult_rec.pack(side="left", padx=5)
        ent_ult_rec.insert(0, config_actual.get("ultimo_recibo", "E001-0"))

        # ---------- 1.5 FECHA DE COMIENZO DEL SISTEMA (CORTE DE COMPRAS Y VENTAS) ----------
        f_corte = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#fff7ed", border_width=1, border_color="#fdba74")
        f_corte.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_corte, text="📅 Fecha de Comienzo del Sistema (Corte de Compras y Ventas)", font=("Arial", 14, "bold"), text_color="#9a3412").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(
            f_corte,
            text=("Todo lo anterior a esta fecha se elimina de forma definitiva y SUNAT/SIRE ya no volverá a descargarlo.\n"
                  "El sistema solo carga y registra compras y ventas desde esta fecha en adelante. Déjala vacía si no deseas ningún corte."),
            font=("Arial", 11, "italic"), text_color="#7c2d12", justify="left"
        ).pack(anchor="w", padx=15, pady=(0, 8))

        f_corte_row = ctk.CTkFrame(f_corte, fg_color="transparent")
        f_corte_row.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(f_corte_row, text="Fecha de Comienzo:", font=("Arial", 11, "bold"), width=140, anchor="w").pack(side="left")
        ent_fecha_corte = ctk.CTkEntry(f_corte_row, width=130, placeholder_text="DD/MM/AAAA")
        ent_fecha_corte.pack(side="left", padx=5)
        _fecha_corte_guardada = obtener_fecha_comienzo(config_actual) if FECHA_SISTEMA_DISPONIBLE else None
        if _fecha_corte_guardada:
            ent_fecha_corte.insert(0, _fecha_corte_guardada.strftime("%d/%m/%Y"))

        def _poner_fecha_hoy():
            ent_fecha_corte.delete(0, tk.END)
            ent_fecha_corte.insert(0, datetime.now().strftime("%d/%m/%Y"))

        ctk.CTkButton(f_corte_row, text="📅 Hoy", width=70, fg_color="#7f8c8d", hover_color="#606b6b", command=_poner_fecha_hoy).pack(side="left", padx=5)
        ctk.CTkButton(
            f_corte_row, text="🧹 Eliminar Compras y Ventas de esta fecha hacia atrás",
            font=("Arial", 11, "bold"), fg_color="#c0392b", hover_color="#922b21",
            command=lambda: limpiar_anteriores()
        ).pack(side="left", padx=10)

        lbl_corte_info = ctk.CTkLabel(f_corte, text="", font=("Arial", 11, "bold"), text_color="gray", justify="left")
        lbl_corte_info.pack(anchor="w", padx=15, pady=(8, 4))

        if not FECHA_SISTEMA_DISPONIBLE:
            ent_fecha_corte.configure(state="disabled")

        def _refrescar_label_corte():
            if not FECHA_SISTEMA_DISPONIBLE:
                lbl_corte_info.configure(text="⚠️ No se pudo cargar el módulo de corte (fecha_sistema.py).", text_color="#c0392b")
                return
            f = obtener_fecha_comienzo()
            if f:
                lbl_corte_info.configure(
                    text=f"📌 Corte activo: se eliminan y bloquean las compras/ventas anteriores al {f.strftime('%d/%m/%Y')}. "
                         f"SUNAT/SIRE solo descarga desde el periodo {f.strftime('%Y%m')} en adelante.",
                    text_color="#9a3412"
                )
            else:
                lbl_corte_info.configure(text="📌 Sin corte definido: el sistema muestra y descarga todo el historial disponible.", text_color="gray")

        def _purgar_con_progreso(fecha_corte, al_terminar):
            """Elimina en segundo plano para no congelar la interfaz."""
            v_prog = ctk.CTkToplevel(v_conf)
            v_prog.title("Eliminando compras y ventas...")
            v_prog.geometry("440x170")
            v_prog.transient(v_conf)
            v_prog.grab_set()
            v_prog.resizable(False, False)
            v_prog.update_idletasks()
            try:
                x = v_conf.winfo_rootx() + (v_conf.winfo_width() // 2) - 220
                y = v_conf.winfo_rooty() + (v_conf.winfo_height() // 2) - 85
                v_prog.geometry(f"+{max(0, x)}+{max(0, y)}")
            except Exception:
                pass
            ctk.CTkLabel(v_prog, text=f"🧹 Eliminando compras y ventas anteriores al\n{fecha_corte.strftime('%d/%m/%Y')}", font=("Arial", 13, "bold"), text_color="#c0392b").pack(pady=(22, 10))
            pbar = ctk.CTkProgressBar(v_prog, width=360)
            pbar.pack(pady=5)
            pbar.set(0.4)
            ctk.CTkLabel(v_prog, text="Este proceso es definitivo. No cierres la ventana...", font=("Arial", 10, "italic"), text_color="gray").pack(pady=5)

            def tarea():
                try:
                    resultado = purgar_anteriores(fecha_corte, usuario=self.usuario_activo)
                    error = None
                except Exception as e:
                    resultado, error = None, str(e)

                def terminar():
                    try:
                        v_prog.destroy()
                    except Exception:
                        pass
                    al_terminar(resultado, error)

                try:
                    v_conf.after(0, terminar)
                except Exception:
                    terminar()

            threading.Thread(target=tarea, daemon=True).start()

        def limpiar_anteriores():
            if not FECHA_SISTEMA_DISPONIBLE:
                return messagebox.showerror("No disponible", "No se pudo cargar el módulo de corte (fecha_sistema.py).", parent=v_conf)
            fecha_corte = parsear_fecha_corte(ent_fecha_corte.get().strip())
            if fecha_corte is None:
                return messagebox.showwarning("Fecha inválida", "Escribe la Fecha de Comienzo con el formato DD/MM/AAAA (ej: 01/08/2026).", parent=v_conf)

            if not messagebox.askyesno(
                "Confirmar eliminación definitiva",
                f"Se eliminarán DEFINITIVAMENTE de la base de datos todas las COMPRAS y VENTAS anteriores al {fecha_corte.strftime('%d/%m/%Y')}.\n\n"
                "Esto incluye facturas recibidas, facturas emitidas, sus pagos y notas de crédito.\n\n"
                "¿Deseas continuar?",
                parent=v_conf
            ):
                return

            def al_terminar(resultado, error):
                if error:
                    return messagebox.showerror("Error", f"No se pudo eliminar la información:\n{error}", parent=v_conf)
                cache_sistema.invalidar()
                messagebox.showinfo("Limpieza completada", resumen_purga(resultado), parent=v_conf)
                _refrescar_label_corte()

            _purgar_con_progreso(fecha_corte, al_terminar)

        _refrescar_label_corte()

        # ---------- 1.6 CUENTAS BANCARIAS (MÓDULO DE BANCOS) ----------
        # Estas cuentas son las que usa el módulo 🏦 Bancos (saldos y conciliación),
        # el registro de cobros de Ventas y el de pagos de Compras.
        # 🔗 Se leen SIEMPRE de la base de datos (fuente compartida por los equipos).
        cuentas_bancarias_actual = []
        try:
            cuentas_bancarias_actual = [dict(c) for c in cargar_bancos(usar_cache=False)]
        except Exception:
            cuentas_bancarias_actual = []
        cuentas_editadas = {"cambiado": False}      # ¿el usuario tocó las cuentas aquí?

        f_bancos_cfg = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#eef7ff",
                                    border_width=1, border_color="#bcd9f5")
        f_bancos_cfg.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_bancos_cfg, text="🏦 Cuentas Bancarias (módulo de Bancos)",
                     font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(
            f_bancos_cfg,
            text=("Registre aquí cada cuenta bancaria de la empresa. El módulo de Bancos calcula el saldo así:\n"
                  "Saldo = Saldo inicial + cobros depositados − pagos realizados (desde la fecha indicada).\n"
                  "Ponga como 'Saldo inicial' el dinero que había en la cuenta en esa fecha."),
            font=("Arial", 11, "italic"), text_color="#1c4e80", justify="left"
        ).pack(anchor="w", padx=15, pady=(0, 8))

        f_lista_bancos = ctk.CTkFrame(f_bancos_cfg, fg_color="transparent")
        f_lista_bancos.pack(fill="x", padx=15, pady=(0, 6))
        lst_bancos = tk.Listbox(f_lista_bancos, height=5, font=("Consolas", 10), activestyle="none")
        lst_bancos.pack(side="left", fill="both", expand=True)

        def _refrescar_lista_bancos():
            lst_bancos.delete(0, tk.END)
            for cta in cuentas_bancarias_actual:
                nombre = f"{cta.get('banco', '')} - {cta.get('cuenta', '')}".strip(" -") or "(Cuenta sin nombre)"
                lst_bancos.insert(tk.END, f"{nombre}   |   Saldo inicial: {cta.get('saldo_inicial', '') or '0.00'}"
                                          f"   |   Desde: {cta.get('fecha', '') or 'sin fecha'}")
            if not cuentas_bancarias_actual:
                lst_bancos.insert(tk.END, "   (Todavía no hay cuentas bancarias configuradas)")

        def _guardar_cuentas_bancarias(borrado_explicito=False):
            """Guarda las cuentas en la BASE DE DATOS (y deja copia en el equipo)
            para que todos los módulos y todos los equipos vean lo mismo.

            'borrado_explicito' se usa cuando el usuario elimina una cuenta: ahí sí
            se permite que la lista quede vacía.
            """
            cuentas_editadas["cambiado"] = True
            try:
                guardar_bancos(cuentas_bancarias_actual,
                               proteger_vacio=not borrado_explicito)
            except Exception as e:
                print("No se pudieron guardar las cuentas bancarias:", e)

        def _dialogo_cuenta_bancaria(indice=None):
            base = {}
            if indice is not None and 0 <= indice < len(cuentas_bancarias_actual):
                base = cuentas_bancarias_actual[indice]
            v_banco = ctk.CTkToplevel(v_conf)
            v_banco.title("Cuenta Bancaria")
            v_banco.geometry("430x360")
            v_banco.transient(v_conf)
            v_banco.grab_set()
            ctk.CTkLabel(v_banco, text=("✏️ Editar cuenta bancaria" if base else "➕ Nueva cuenta bancaria"),
                         font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
            f_b = ctk.CTkFrame(v_banco, fg_color="transparent")
            f_b.pack(fill="x", padx=20)
            ctk.CTkLabel(f_b, text="Banco:", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_banco_n = ctk.CTkEntry(f_b, placeholder_text="Ej.: BCP, BBVA, Interbank, Banco de la Nación")
            ent_banco_n.pack(fill="x", pady=(0, 8))
            ent_banco_n.insert(0, str(base.get("banco", "") or ""))
            ctk.CTkLabel(f_b, text="Número de cuenta (o cuenta / CCI):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_cuenta_n = ctk.CTkEntry(f_b, placeholder_text="Ej.: 193-1234567-0-91")
            ent_cuenta_n.pack(fill="x", pady=(0, 8))
            ent_cuenta_n.insert(0, str(base.get("cuenta", "") or ""))
            f_saldo = ctk.CTkFrame(f_b, fg_color="transparent")
            f_saldo.pack(fill="x", pady=(0, 8))
            f_saldo.columnconfigure((0, 1), weight=1)
            ctk.CTkLabel(f_saldo, text="Saldo inicial:", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(f_saldo, text="Desde la fecha (DD/MM/AAAA):", font=("Arial", 11, "bold")).grid(row=0, column=1, sticky="w", padx=(8, 0))
            ent_saldo_n = ctk.CTkEntry(f_saldo, placeholder_text="0.00")
            ent_saldo_n.grid(row=1, column=0, sticky="ew")
            ent_saldo_n.insert(0, str(base.get("saldo_inicial", "") or ""))
            ent_fecha_n = ctk.CTkEntry(f_saldo, placeholder_text="DD/MM/AAAA")
            ent_fecha_n.grid(row=1, column=1, sticky="ew", padx=(8, 0))
            ent_fecha_n.insert(0, str(base.get("fecha", "") or ""))

            def _guardar_cuenta():
                banco_txt = ent_banco_n.get().strip()
                cuenta_txt = ent_cuenta_n.get().strip()
                if not banco_txt and not cuenta_txt:
                    return messagebox.showwarning("Datos incompletos",
                                                  "Escriba al menos el banco o el número de cuenta.",
                                                  parent=v_banco)
                registro = normalizar_cuenta({
                    "banco": banco_txt,
                    "cuenta": cuenta_txt,
                    "saldo_inicial": ent_saldo_n.get().strip(),
                    "fecha": ent_fecha_n.get().strip(),
                })
                if indice is None:
                    cuentas_bancarias_actual.append(registro)
                else:
                    cuentas_bancarias_actual[indice] = registro
                _refrescar_lista_bancos()
                _guardar_cuentas_bancarias()
                registrar_auditoria(self.usuario_activo, "Bancos",
                                    f"{'Editó' if base else 'Agregó'} la cuenta bancaria "
                                    f"{registro.get('banco', '')} {registro.get('cuenta', '')}".strip())
                v_banco.destroy()

            f_btn_banco = ctk.CTkFrame(v_banco, fg_color="transparent")
            f_btn_banco.pack(fill="x", padx=20, pady=15)
            ctk.CTkButton(f_btn_banco, text="💾 Guardar Cuenta", fg_color="#27ae60",
                          hover_color="#1e8449", font=("Arial", 12, "bold"),
                          command=_guardar_cuenta).pack(side="left", expand=True, padx=5)
            ctk.CTkButton(f_btn_banco, text="✖ Cancelar", fg_color="#7f8c8d",
                          hover_color="#606b6b", font=("Arial", 12, "bold"),
                          command=v_banco.destroy).pack(side="right", expand=True, padx=5)
            ent_banco_n.focus()

        def agregar_cuenta_bancaria():
            _dialogo_cuenta_bancaria(None)

        def editar_cuenta_bancaria():
            sel = lst_bancos.curselection()
            if not sel or not cuentas_bancarias_actual:
                return messagebox.showinfo("Cuentas bancarias", "Seleccione la cuenta que desea editar.",
                                           parent=v_conf)
            _dialogo_cuenta_bancaria(sel[0])

        def eliminar_cuenta_bancaria():
            sel = lst_bancos.curselection()
            if not sel or not cuentas_bancarias_actual:
                return messagebox.showinfo("Cuentas bancarias", "Seleccione la cuenta que desea eliminar.",
                                           parent=v_conf)
            cta = cuentas_bancarias_actual[sel[0]]
            if not messagebox.askyesno(
                    "Confirmar eliminación",
                    f"¿Quitar la cuenta {cta.get('banco', '')} {cta.get('cuenta', '')} del sistema?\n\n"
                    "Los movimientos ya conciliados de esa cuenta NO se borran.",
                    parent=v_conf):
                return
            cuentas_bancarias_actual.pop(sel[0])
            _refrescar_lista_bancos()
            _guardar_cuentas_bancarias(borrado_explicito=True)
            registrar_auditoria(self.usuario_activo, "Bancos",
                                f"Eliminó la cuenta bancaria {cta.get('banco', '')} {cta.get('cuenta', '')}".strip())

        f_botones_bancos = ctk.CTkFrame(f_bancos_cfg, fg_color="transparent")
        f_botones_bancos.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkButton(f_botones_bancos, text="➕ Agregar Cuenta", width=160, font=("Arial", 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=agregar_cuenta_bancaria).pack(side="left", padx=(0, 6))
        ctk.CTkButton(f_botones_bancos, text="✏️ Editar", width=110, font=("Arial", 11, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=editar_cuenta_bancaria).pack(side="left", padx=6)
        ctk.CTkButton(f_botones_bancos, text="🗑️ Eliminar", width=110, font=("Arial", 11, "bold"),
                      fg_color="#c0392b", hover_color="#96281b",
                      command=eliminar_cuenta_bancaria).pack(side="left", padx=6)
        _refrescar_lista_bancos()

        def actualizar_tasas_regimen(choice):
            ent_igv.delete(0, tk.END)
            ent_detraccion.delete(0, tk.END)
            ent_renta_m.delete(0, tk.END)
            ent_renta_a.delete(0, tk.END)
            if "NRUS" in choice:
                ent_igv.insert(0, "0"); ent_detraccion.insert(0, "0"); ent_renta_m.insert(0, "0"); ent_renta_a.insert(0, "0")
            elif "RER" in choice:
                ent_igv.insert(0, "18"); ent_detraccion.insert(0, "12"); ent_renta_m.insert(0, "1.5"); ent_renta_a.insert(0, "0")
            elif "MYPE" in choice:
                ent_igv.insert(0, "18"); ent_detraccion.insert(0, "12"); ent_renta_m.insert(0, "1.0"); ent_renta_a.insert(0, "29.5")
            elif "General" in choice:
                ent_igv.insert(0, "18"); ent_detraccion.insert(0, "12"); ent_renta_m.insert(0, "1.5"); ent_renta_a.insert(0, "29.5")
        cmb_regimen.configure(command=actualizar_tasas_regimen)

        def buscar_ruc_empresa(event=None):
            ruc = ent_ruc_empresa.get().strip()
            if len(ruc) != 11 or not ruc.isdigit():
                messagebox.showwarning("RUC Inválido", "Por favor, ingrese un RUC válido de 11 dígitos.", parent=v_conf)
                return
            
            def tarea():
                try:
                    ctx = crear_contexto_ssl_seguro()
                    token = config_actual.get("token_api_ruc", "")
                    url = f"https://api.apis.net.pe/v1/ruc?numero={ruc}"
                    headers = {'User-Agent': 'Mozilla/5.0'}
                    if token:
                        headers['Authorization'] = f'Bearer {token}'
                        
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
                        if response.status == 200:
                            data = json.loads(response.read().decode())
                            v_conf.after(0, lambda: _aplicar_datos_empresa(data))
                        else:
                            v_conf.after(0, lambda: messagebox.showwarning("Sin Resultados", "No se encontró información para este RUC.", parent=v_conf))
                except Exception as e:
                    v_conf.after(0, lambda err=e: messagebox.showwarning("Error", f"Problema al consultar RUC:\n{err}", parent=v_conf))

            def _aplicar_datos_empresa(data):
                ent_razon_social.delete(0, tk.END)
                ent_razon_social.insert(0, data.get("nombre", ""))
                messagebox.showinfo("Éxito", "Datos de la empresa recuperados correctamente.", parent=v_conf)

            threading.Thread(target=tarea, daemon=True).start()
            
        ent_ruc_empresa.bind("<Return>", buscar_ruc_empresa)
        
        # ---------- 2. API SUNAT SIRE ----------
        f_sire = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#f0fdf4", border_width=1, border_color="#bbf7d0")
        f_sire.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_sire, text="🔐 Credenciales de Acceso a SUNAT SIRE (Descarga de Compras)", font=("Arial", 14, "bold"), text_color="#166534").pack(anchor="w", padx=15, pady=(10, 5))
        f_sire_r1 = ctk.CTkFrame(f_sire, fg_color="transparent")
        f_sire_r1.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_sire_r1, text="Usuario SOL:", font=("Arial", 11, "bold"), width=140, anchor="w").pack(side="left")
        ent_user_sol = ctk.CTkEntry(f_sire_r1, placeholder_text="Ej: MODDATOS")
        ent_user_sol.pack(side="left", fill="x", expand=True, padx=5)
        ent_user_sol.insert(0, config_actual.get("usuario_sol", ""))
        ctk.CTkLabel(f_sire_r1, text="Clave SOL:", font=("Arial", 11, "bold"), width=100, anchor="w").pack(side="left", padx=(15, 5))
        ent_clave_sol = ctk.CTkEntry(f_sire_r1, show="*", placeholder_text="••••••••")
        ent_clave_sol.pack(side="left", fill="x", expand=True, padx=5)
        ent_clave_sol.insert(0, config_actual.get("clave_sol", ""))
        f_sire_r2 = ctk.CTkFrame(f_sire, fg_color="transparent")
        f_sire_r2.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_sire_r2, text="Client ID (API SIRE):", font=("Arial", 11, "bold"), width=140, anchor="w").pack(side="left")
        ent_client_id = ctk.CTkEntry(f_sire_r2)
        ent_client_id.pack(side="left", fill="x", expand=True, padx=5)
        ent_client_id.insert(0, config_actual.get("client_id_sire", ""))
        ctk.CTkLabel(f_sire_r2, text="Client Secret:", font=("Arial", 11, "bold"), width=100, anchor="w").pack(side="left", padx=(15, 5))
        ent_client_secret = ctk.CTkEntry(f_sire_r2, show="*")
        ent_client_secret.pack(side="left", fill="x", expand=True, padx=5)
        ent_client_secret.insert(0, config_actual.get("client_secret_sire", ""))
        
        # ---------- 3. FACTURACIÓN ELECTRÓNICA ----------
        f_fe = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#f0f4f8", border_width=1, border_color="#d0d7de")
        f_fe.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_fe, text="⚡ Facturación Electrónica Directa (Emisión de Ventas)", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        f_fe_row1 = ctk.CTkFrame(f_fe, fg_color="transparent")
        f_fe_row1.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_fe_row1, text="Proveedor Servicio PSE:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        cmb_pse = ctk.CTkOptionMenu(f_fe_row1, values=["Nubefact", "Facturactiva", "Efact", "Bsale"], width=220)
        cmb_pse.pack(side="left", padx=5)
        cmb_pse.set(config_actual.get("proveedor_fe", "Nubefact"))
        f_fe_row2 = ctk.CTkFrame(f_fe, fg_color="transparent")
        f_fe_row2.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_fe_row2, text="Ruta API (Endpoint):", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        ent_url_api = ctk.CTkEntry(f_fe_row2)
        ent_url_api.pack(side="left", fill="x", expand=True, padx=5)
        ent_url_api.insert(0, config_actual.get("url_api_fe", ""))
        f_fe_row3 = ctk.CTkFrame(f_fe, fg_color="transparent")
        f_fe_row3.pack(fill="x", padx=15, pady=(4, 8))
        ctk.CTkLabel(f_fe_row3, text="Token de Autorización:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        ent_token_api = ctk.CTkEntry(f_fe_row3, show="*")
        ent_token_api.pack(side="left", fill="x", expand=True, padx=5)
        ent_token_api.insert(0, config_actual.get("token_api_fe", ""))
        
        # ---------- 4. 2FA ----------
        f_2fa = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#fff3cd", border_width=1, border_color="#ffeeba")
        f_2fa.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_2fa, text="🛡️ Seguridad: Clave Dinámica (OTP) para SUNAT", font=("Arial", 14, "bold"), text_color="#856404").pack(anchor="w", padx=15, pady=(10, 5))
        f_2fa_m = ctk.CTkFrame(f_2fa, fg_color="transparent")
        f_2fa_m.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_2fa_m, text="Método de Envío OTP:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        cmb_2fa = ctk.CTkOptionMenu(f_2fa_m, values=["Inactivo", "Telegram (Gratis)", "Correo Electrónico (Gratis)", "SMS Twilio (De Pago)"], width=250)
        cmb_2fa.pack(side="left", padx=5)
        cmb_2fa.set(config_actual.get("2fa_metodo", "Inactivo"))
        f_tel = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_tel, text="Bot Token:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_tel_token = ctk.CTkEntry(f_tel, width=350)
        ent_tel_token.grid(row=0, column=1, sticky="w", pady=4)
        ent_tel_token.insert(0, config_actual.get("tel_bot_token", ""))
        ctk.CTkLabel(f_tel, text="Chat ID:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_tel_chat = ctk.CTkEntry(f_tel, width=350)
        ent_tel_chat.grid(row=1, column=1, sticky="w", pady=4)
        ent_tel_chat.insert(0, config_actual.get("tel_chat_id", ""))
        f_mail = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_mail, text="Servidor SMTP:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_mail_smtp = ctk.CTkEntry(f_mail, width=200)
        ent_mail_smtp.grid(row=0, column=1, sticky="w", pady=4)
        ent_mail_smtp.insert(0, config_actual.get("email_smtp", "smtp.gmail.com"))
        ctk.CTkLabel(f_mail, text="Puerto:", font=("Arial", 11, "bold"), width=60, anchor="w").grid(row=0, column=2, sticky="w", padx=(10, 0), pady=4)
        ent_mail_port = ctk.CTkEntry(f_mail, width=80)
        ent_mail_port.grid(row=0, column=3, sticky="w", pady=4)
        ent_mail_port.insert(0, config_actual.get("email_port", "587"))
        ctk.CTkLabel(f_mail, text="Tu Correo:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_mail_user = ctk.CTkEntry(f_mail, width=350)
        ent_mail_user.grid(row=1, column=1, columnspan=3, sticky="w", pady=4)
        ent_mail_user.insert(0, config_actual.get("email_user", ""))
        ctk.CTkLabel(f_mail, text="Clave de App:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        ent_mail_pass = ctk.CTkEntry(f_mail, width=350, show="*")
        ent_mail_pass.grid(row=2, column=1, columnspan=3, sticky="w", pady=4)
        ent_mail_pass.insert(0, config_actual.get("email_pass", ""))
        ctk.CTkLabel(f_mail, text="Enviar a:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=3, column=0, sticky="w", pady=4)
        ent_mail_dest = ctk.CTkEntry(f_mail, width=350)
        ent_mail_dest.grid(row=3, column=1, columnspan=3, sticky="w", pady=4)
        ent_mail_dest.insert(0, config_actual.get("email_dest", ""))
        f_sms = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_sms, text="Account SID:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_twi_sid = ctk.CTkEntry(f_sms, width=350)
        ent_twi_sid.grid(row=0, column=1, sticky="w", pady=4)
        ent_twi_sid.insert(0, config_actual.get("twi_sid", ""))
        ctk.CTkLabel(f_sms, text="Auth Token:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_twi_token = ctk.CTkEntry(f_sms, width=350, show="*")
        ent_twi_token.grid(row=1, column=1, sticky="w", pady=4)
        ent_twi_token.insert(0, config_actual.get("twi_token", ""))
        ctk.CTkLabel(f_sms, text="N° Twilio:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        ent_twi_from = ctk.CTkEntry(f_sms, width=350)
        ent_twi_from.grid(row=2, column=1, sticky="w", pady=4)
        ent_twi_from.insert(0, config_actual.get("twi_from", ""))
        ctk.CTkLabel(f_sms, text="N° Destino:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=3, column=0, sticky="w", pady=4)
        ent_twi_to = ctk.CTkEntry(f_sms, width=350)
        ent_twi_to.grid(row=3, column=1, sticky="w", pady=4)
        ent_twi_to.insert(0, config_actual.get("twi_to", ""))

        def actualizar_ui_2fa(choice):
            f_tel.pack_forget()
            f_mail.pack_forget()
            f_sms.pack_forget()
            if "Telegram" in choice:
                f_tel.pack(fill="x", padx=15, pady=5)
            elif "Correo" in choice:
                f_mail.pack(fill="x", padx=15, pady=5)
            elif "SMS" in choice:
                f_sms.pack(fill="x", padx=15, pady=5)
        cmb_2fa.configure(command=actualizar_ui_2fa)
        actualizar_ui_2fa(cmb_2fa.get())
        
        # ---------- 5. PERSONALIZACIÓN VISUAL DE COTIZACIONES ----------
        f_diseno = ctk.CTkFrame(f_scroll, corner_radius=10)
        f_diseno.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_diseno, text="🎨 Personalización Visual de Cotizaciones (PDF y Pantalla)", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 10))
        f_nombre_cot = ctk.CTkFrame(f_diseno, fg_color="transparent")
        f_nombre_cot.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_nombre_cot, text="Mostrar en Cotizaciones (Cliente):", font=("Arial", 11, "bold"), width=220, anchor="w").pack(side="left")
        cmb_nombre_cot = ctk.CTkOptionMenu(f_nombre_cot, values=["Razón Social", "Razón Comercial"], width=180)
        cmb_nombre_cot.pack(side="left", padx=5)
        cmb_nombre_cot.set(config_actual.get("nombre_cliente_cotizacion", "Razón Social"))

        def crear_color_picker(padre, texto, key, default):
            f_col = ctk.CTkFrame(padre, fg_color="transparent")
            f_col.pack(fill="x", padx=15, pady=5)
            ctk.CTkLabel(f_col, text=texto, font=("Arial", 11, "bold"), width=220, anchor="w").pack(side="left")
            ent = ctk.CTkEntry(f_col, width=120)
            ent.pack(side="left", padx=5)
            valor_guardado = str(config_actual.get(key, default)).strip()
            ent.insert(0, valor_guardado)
            color_prev = valor_guardado if valor_guardado.startswith("#") else default
            f_prev = ctk.CTkFrame(f_col, width=30, height=30, fg_color=color_prev, corner_radius=5)
            f_prev.pack(side="left", padx=10)

            def elegir():
                curr = ent.get().strip()
                if not curr.startswith("#"):
                    curr = default
                try:
                    color = colorchooser.askcolor(title="Elegir Color", color=curr, parent=v_conf)[1]
                except Exception:
                    color = colorchooser.askcolor(title="Elegir Color")[1]
                if color:
                    ent.delete(0, tk.END)
                    ent.insert(0, color)
                    f_prev.configure(fg_color=color)
            ctk.CTkButton(f_col, text="🎨 Elegir Color", width=120, command=elegir).pack(side="left")
            return ent
        ent_color_1 = crear_color_picker(f_diseno, "Color Principal (Letras M):", "color_primario", "#eb337a")
        ent_color_2 = crear_color_picker(f_diseno, "Color Secundario (Letras N):", "color_secundario", "#000000")
        ent_color_3 = crear_color_picker(f_diseno, "Color de Franja (Tabla PDF):", "color_franja", "#eb337a")
        ctk.CTkLabel(f_diseno, text="🖼️ Logo del Encabezado de Cotización (PDF):", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 2))
        f_ruta_logo = ctk.CTkFrame(f_diseno, fg_color="transparent")
        f_ruta_logo.pack(fill="x", padx=15)
        ent_logo = ctk.CTkEntry(f_ruta_logo, placeholder_text="Ruta de la imagen (JPG/PNG)")
        ent_logo.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ent_logo.insert(0, config_actual.get("ruta_logo_cotizacion", ""))

        def buscar_logo_cotizacion():
            tipos_seguros = [
                ("Archivos de Imagen", "*.png *.jpg *.jpeg *.PNG *.JPG *.JPEG"),
                ("Todos", "*.*")
            ]
            ruta = filedialog.askopenfilename(title="Seleccionar Logo", filetypes=tipos_seguros, parent=v_conf)
            if ruta:
                ent_logo.delete(0, tk.END)
                ent_logo.insert(0, ruta)
                
        ctk.CTkButton(f_ruta_logo, text="📂 Buscar Imagen", width=140, command=buscar_logo_cotizacion).pack(side="right")
        
        ctk.CTkLabel(f_diseno, text="📝 Términos y Condiciones (Por defecto en el PDF):", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 2))
        txt_terminos = ctk.CTkTextbox(f_diseno, height=70, font=("Arial", 11), border_width=1)
        txt_terminos.pack(fill="x", padx=15, pady=2)
        txt_terminos.insert("1.0", config_actual.get("terminos_cotizacion", ""))

        # ---------- 6. PREFERENCIAS REGIONALES Y RCLONE ----------
        f_region = ctk.CTkFrame(f_scroll, corner_radius=10)
        f_region.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_region, text="🌍 Preferencias Regionales y Sincronización", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 10))
        
        f_mon = ctk.CTkFrame(f_region, fg_color="transparent")
        f_mon.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_mon, text="Símbolo de Moneda:", font=("Arial", 11, "bold"), width=180, anchor="w").pack(side="left")
        cmb_moneda = ctk.CTkOptionMenu(f_mon, values=["S/.", "$", "€", "Bs.", "CLP$", "COP$", "MXN$"], width=180)
        cmb_moneda.pack(side="left")
        cmb_moneda.set(config_actual.get("simbolo_moneda", "S/."))
        
        f_num = ctk.CTkFrame(f_region, fg_color="transparent")
        f_num.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_num, text="Formato Decimal:", font=("Arial", 11, "bold"), width=180, anchor="w").pack(side="left")
        cmb_num = ctk.CTkOptionMenu(f_num, values=["1,000.00", "1.000,00"], width=180)
        cmb_num.pack(side="left")
        cmb_num.set(config_actual.get("formato_numero", "1,000.00"))
        
        f_fec = ctk.CTkFrame(f_region, fg_color="transparent")
        f_fec.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_fec, text="Formato de Fecha:", font=("Arial", 11, "bold"), width=180, anchor="w").pack(side="left")
        cmb_fecha = ctk.CTkOptionMenu(f_fec, values=["DD/MM/AAAA", "MM/DD/AAAA"], width=180)
        cmb_fecha.pack(side="left")
        cmb_fecha.set(config_actual.get("formato_fecha", "DD/MM/AAAA"))

        ctk.CTkLabel(f_region, text="☁️ Sincronización en la Nube (Rclone - Google Drive)", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 5))

        f_rclone_1 = ctk.CTkFrame(f_region, fg_color="transparent")
        f_rclone_1.pack(fill="x", padx=15, pady=2)
        ctk.CTkLabel(f_rclone_1, text="Carpeta Local:", font=("Arial", 11, "bold"), width=120, anchor="w").pack(side="left")
        ent_drive = ctk.CTkEntry(f_rclone_1, placeholder_text="Ej: ~/Documents/ArchivosFlota")
        ent_drive.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ent_drive.insert(0, config_actual.get("ruta_drive", ""))

        def buscar_carpeta_drive():
            carpeta = filedialog.askdirectory(title="Seleccionar Carpeta Local", parent=v_conf)
            if carpeta:
                ent_drive.delete(0, tk.END)
                ent_drive.insert(0, carpeta)

        ctk.CTkButton(f_rclone_1, text="📁 Buscar", width=100, command=buscar_carpeta_drive).pack(side="right")

        f_rclone_2 = ctk.CTkFrame(f_region, fg_color="transparent")
        f_rclone_2.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(f_rclone_2, text="Nombre Remote:", font=("Arial", 11, "bold"), width=120, anchor="w").pack(side="left")
        ent_rclone_remote = ctk.CTkEntry(f_rclone_2, placeholder_text="Ej: gdrive:")
        ent_rclone_remote.pack(side="left", fill="x", expand=True, padx=(0, 15))
        ent_rclone_remote.insert(0, config_actual.get("rclone_remote", "gdrive:"))

        ctk.CTkLabel(f_rclone_2, text="Carpeta Nube:", font=("Arial", 11, "bold"), width=90, anchor="w").pack(side="left")
        ent_rclone_nube = ctk.CTkEntry(f_rclone_2, placeholder_text="Ej: FlotaCube")
        ent_rclone_nube.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ent_rclone_nube.insert(0, config_actual.get("rclone_ruta_nube", "BlackCube"))

        def vincular_drive_automatico():
            remote = ent_rclone_remote.get().strip().replace(":", "")
            if not remote:
                remote = "gdrive"
            cmd_rclone = obtener_comando_rclone()

            msg = ("Se abrirá el navegador web automáticamente.\n\n"
                   "1. Selecciona tu cuenta de Google.\n"
                   "2. Concede todos los permisos que solicite.\n"
                   "3. Cuando veas el mensaje de 'Success!', cierra el navegador y presiona Aceptar aquí.")
            messagebox.showinfo("Vincular Google Drive", msg, parent=v_conf)

            try:
                kwargs = {}
                if sys.platform == "win32":
                    kwargs["creationflags"] = 0x08000000
                result = subprocess.run([cmd_rclone, "config", "create", remote, "drive", "scope", "drive"], capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
                if result.returncode == 0:
                    messagebox.showinfo("Éxito", "¡Google Drive vinculado correctamente!\nYa puedes realizar la prueba de conexión.", parent=v_conf)
                else:
                    messagebox.showerror("Error", f"Falló la vinculación:\n{result.stderr}", parent=v_conf)
            except Exception as e:
                messagebox.showerror("Error Crítico", f"No se pudo ejecutar Rclone:\n{e}", parent=v_conf)

        def probar_rclone():
            remote = ent_rclone_remote.get().strip()
            nube = ent_rclone_nube.get().strip()
            local = ent_drive.get().strip()
            
            if not remote:
                messagebox.showwarning("Aviso", "Ingresa el nombre del remote primero.", parent=v_conf)
                return
                
            if not local:
                default_local = os.path.join(os.path.expanduser("~"), "BlackCube_Archivos")
                ent_drive.insert(0, default_local)
                local = default_local
                messagebox.showinfo("Atención: Carpeta Local Automática", 
                                    f"Para sincronizar, el sistema necesita un lugar donde colocar los archivos primero.\n\n"
                                    f"Hemos asignado por defecto tu carpeta:\n{local}\n\n"
                                    "Los archivos se guardarán aquí y luego se enviarán a la nube.", parent=v_conf)
                
            cmd_rclone = obtener_comando_rclone()
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000

            try:
                subprocess.run([cmd_rclone, "version"], check=True, capture_output=True, **kwargs)
                result = subprocess.run([cmd_rclone, "about", remote], capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
                
                if result.returncode == 0:
                    msg_adicional = ""
                    if nube:
                        ruta_remota = f"{remote}{nube}" if remote.endswith(":") else f"{remote}:{nube}"
                        res_mkdir = subprocess.run([cmd_rclone, "mkdir", ruta_remota], capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)
                        if res_mkdir.returncode == 0:
                            msg_adicional = f"\n\n📁 Se verificó/creó la carpeta en la nube: '{nube}'"
                            lanzar_sync_background() 
                        else:
                            msg_adicional = f"\n\n⚠️ No se pudo crear la carpeta en la nube. Detalle:\n{res_mkdir.stderr}"

                    messagebox.showinfo("Conexión Exitosa", f"¡Rclone detectado y conectado correctamente a '{remote}'!\n\nInfo de tu Google Drive:\n{result.stdout}{msg_adicional}", parent=v_conf)
                else:
                    messagebox.showerror("Error Rclone", f"Rclone falló al conectar a '{remote}'.\n¿Configuraste bien el remote?\n\nDetalle:\n{result.stderr}", parent=v_conf)
            except FileNotFoundError:
                messagebox.showerror("No encontrado", "Rclone no está instalado o no fue incluido en el paquete.\nDescárgalo de rclone.org o asegúrate de que el archivo exista.", parent=v_conf)
            except Exception as e:
                messagebox.showerror("Error", f"Error al ejecutar Rclone:\n{e}", parent=v_conf)

        f_rclone_3 = ctk.CTkFrame(f_region, fg_color="transparent")
        f_rclone_3.pack(fill="x", padx=15, pady=(5, 0))
        ctk.CTkButton(f_rclone_3, text="🚀 Probar Rclone", font=("Arial", 11, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=probar_rclone).pack(side="right")
        ctk.CTkButton(f_rclone_3, text="🔗 Vincular Cuenta Auto", font=("Arial", 11, "bold"), fg_color="#8e44ad", hover_color="#732d91", command=vincular_drive_automatico).pack(side="right", padx=(0, 10))
        
        ctk.CTkLabel(f_region, text="🖨️ Impresora por Defecto", font=("Arial", 12, "bold")).pack(anchor="w", padx=15, pady=(15, 5))
        ent_impresora = ctk.CTkEntry(f_region, placeholder_text="Ej: Epson L3150 Series")
        ent_impresora.pack(fill="x", padx=15)
        ent_impresora.insert(0, config_actual.get("impresora", ""))

        # ---------- 7. DISEÑO Y ORDEN DEL MENÚ ----------
        f_menu = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#eef2f3", border_width=1, border_color="#ccd1d9")
        f_menu.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_menu, text="🎨 Apariencia y Orden del Menú Principal", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        f_menu_colors = ctk.CTkFrame(f_menu, fg_color="transparent")
        f_menu_colors.pack(fill="x", padx=15, pady=5)

        def crear_selector_color_grid(padre, fila, col, texto, key_config, default):
            f_c = ctk.CTkFrame(padre, fg_color="transparent")
            f_c.grid(row=fila, column=col, padx=10, pady=5, sticky="w")
            ctk.CTkLabel(f_c, text=texto, font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
            ent_c = ctk.CTkEntry(f_c, width=80)
            ent_c.pack(side="left", padx=5)
            val = str(config_actual.get(key_config, default)).strip()
            ent_c.insert(0, val)
            color_prev = val if val.startswith("#") else default
            f_p = ctk.CTkFrame(f_c, width=25, height=25, fg_color=color_prev, corner_radius=5)
            f_p.pack(side="left", padx=5)

            def elegir():
                curr = ent_c.get().strip()
                if not curr.startswith("#"):
                    curr = default
                try:
                    color = colorchooser.askcolor(title=f"Elegir {texto}", color=curr, parent=v_conf)[1]
                except Exception:
                    color = colorchooser.askcolor(title=f"Elegir {texto}")[1]
                if color:
                    ent_c.delete(0, tk.END)
                    ent_c.insert(0, color)
                    f_p.configure(fg_color=color)
            ctk.CTkButton(f_c, text="🎨", width=35, command=elegir).pack(side="left")
            return ent_c
            
        ent_m_fondo = crear_selector_color_grid(f_menu_colors, 0, 0, "Fondo Lateral:", "color_menu_fondo", "#1a252c")
        ent_m_btn = crear_selector_color_grid(f_menu_colors, 0, 1, "Color del Botón:", "color_menu_btn", "#1f538d")
        ent_m_hov = crear_selector_color_grid(f_menu_colors, 1, 0, "Al pasar el Mouse:", "color_menu_hover", "#163b65")
        ent_m_txt = crear_selector_color_grid(f_menu_colors, 1, 1, "Color del Texto:", "color_menu_texto", "#ffffff")
        f_menu_order = ctk.CTkFrame(f_menu, fg_color="transparent")
        f_menu_order.pack(fill="both", expand=True, padx=15, pady=(15, 0))
        ctk.CTkLabel(f_menu_order, text="Orden de los Módulos por Grupo:", font=("Arial", 11, "bold")).pack(anchor="w")
        f_listas = ctk.CTkFrame(f_menu_order, fg_color="transparent")
        f_listas.pack(fill="both", expand=True, pady=5)
        nombres_a_keys = {v: k for k, v in self.modulos_sistema.items()}

        def crear_columna_orden(padre, titulo, lista_keys):
            f_col = ctk.CTkFrame(padre, fg_color="transparent")
            f_col.pack(side="left", fill="both", expand=True, padx=5)
            ctk.CTkLabel(f_col, text=titulo, font=("Arial", 10, "bold")).pack()
            lb = tk.Listbox(f_col, selectmode=tk.SINGLE, font=("Arial", 9), height=7)
            lb.pack(fill="both", expand=True, pady=2)
            for k in lista_keys:
                if k in self.modulos_sistema:
                    lb.insert(tk.END, self.modulos_sistema[k])
            f_btns = ctk.CTkFrame(f_col, fg_color="transparent")
            f_btns.pack(fill="x")

            def subir():
                sel = lb.curselection()
                if not sel or sel[0] == 0:
                    return
                idx = sel[0]
                val = lb.get(idx)
                lb.delete(idx)
                lb.insert(idx - 1, val)
                lb.selection_set(idx - 1)

            def bajar():
                sel = lb.curselection()
                if not sel or sel[0] == lb.size() - 1:
                    return
                idx = sel[0]
                val = lb.get(idx)
                lb.delete(idx)
                lb.insert(idx + 1, val)
                lb.selection_set(idx + 1)
            ctk.CTkButton(f_btns, text="⬆️", width=30, command=subir).pack(side="left", expand=True, padx=1)
            ctk.CTkButton(f_btns, text="⬇️", width=30, command=bajar).pack(side="left", expand=True, padx=1)
            return lb
            
        default_ops = ["clientes", "cotizaciones", "pautas", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "inventario", "locaciones"]
        default_fin = ["ventas", "compras", "bancos", "libro_diario", "libro_mayor", "impuestos", "dashboard"]
        default_aju = ["configuracion", "usuarios", "bitacora"]
        lb_ops = crear_columna_orden(f_listas, "Módulos Operativos", config_actual.get("orden_operativos", default_ops))
        lb_fin = crear_columna_orden(f_listas, "Finanzas y Reportes", config_actual.get("orden_finanzas", default_fin))
        lb_aju = crear_columna_orden(f_listas, "Ajustes de Sistema", config_actual.get("orden_ajustes", default_aju))

        def guardar_configuracion():
            def ext_ord(lb, clave_config):
                resultado = []
                for i in range(lb.size()):
                    texto = lb.get(i)
                    if texto in nombres_a_keys:
                        resultado.append(nombres_a_keys[texto])
                if not resultado:
                    # 🛡️ Si la lista quedó vacía (carga incompleta, un módulo nuevo...)
                    # se conserva el orden anterior en lugar de dejar el menú sin módulos.
                    anterior = config_actual.get(clave_config) or []
                    return [k for k in anterior if k in self.modulos_sistema]
                return resultado
            fecha_corte_txt = ent_fecha_corte.get().strip()
            fecha_corte_nueva = parsear_fecha_corte(fecha_corte_txt) if fecha_corte_txt else None
            if fecha_corte_txt and fecha_corte_nueva is None:
                return messagebox.showerror(
                    "Fecha inválida",
                    "La Fecha de Comienzo del Sistema debe tener el formato DD/MM/AAAA (ej: 01/08/2026).",
                    parent=v_conf
                )

            nueva_config = config_actual.copy()
            nueva_config.pop("retencion_porcentaje", None)  # Retención ya no se usa en Perú
            nueva_config.update({
                "fecha_comienzo_sistema": fecha_corte_nueva.isoformat() if fecha_corte_nueva else "",
                "ruta_drive": ent_drive.get().strip(),
                "rclone_remote": ent_rclone_remote.get().strip(),
                "rclone_ruta_nube": ent_rclone_nube.get().strip(),
                "impresora": ent_impresora.get().strip(),
                "simbolo_moneda": cmb_moneda.get().strip() or "S/.",
                "formato_numero": cmb_num.get(),
                "formato_fecha": cmb_fecha.get(),
                "ruta_logo_cotizacion": ent_logo.get().strip(),
                "color_primario": ent_color_1.get().strip(),
                "color_secundario": ent_color_2.get().strip(),
                "color_franja": ent_color_3.get().strip(),
                "ruc_empresa": ent_ruc_empresa.get().strip(),
                "razon_social_empresa": ent_razon_social.get().strip(),
                "igv_porcentaje": ent_igv.get().strip(),
                "detraccion_porcentaje": ent_detraccion.get().strip(),
                "renta_mensual_porcentaje": ent_renta_m.get().strip(),
                "renta_anual_porcentaje": ent_renta_a.get().strip(),
                "regimen_empresa": cmb_regimen.get().strip(),
                "nombre_cliente_cotizacion": cmb_nombre_cot.get().strip(),
                "proveedor_fe": cmb_pse.get().strip(),
                "url_api_fe": ent_url_api.get().strip(),
                "token_api_fe": ent_token_api.get().strip(),
                "ultimo_factura": ent_ult_fac.get().strip() or "F001-0",
                "ultimo_boleta": ent_ult_bol.get().strip() or "B001-0",
                "ultimo_recibo": ent_ult_rec.get().strip() or "E001-0",
                "usuario_sol": ent_user_sol.get().strip(),
                "clave_sol": ent_clave_sol.get().strip(),
                "client_id_sire": ent_client_id.get().strip(),
                "client_secret_sire": ent_client_secret.get().strip(),
                "2fa_metodo": cmb_2fa.get().strip(),
                "tel_bot_token": ent_tel_token.get().strip(),
                "tel_chat_id": ent_tel_chat.get().strip(),
                "email_smtp": ent_mail_smtp.get().strip(),
                "email_port": ent_mail_port.get().strip(),
                "email_user": ent_mail_user.get().strip(),
                "email_pass": ent_mail_pass.get().strip(),
                "email_dest": ent_mail_dest.get().strip(),
                "twi_sid": ent_twi_sid.get().strip(),
                "twi_token": ent_twi_token.get().strip(),
                "twi_from": ent_twi_from.get().strip(),
                "twi_to": ent_twi_to.get().strip(),
                "color_menu_fondo": ent_m_fondo.get().strip(),
                "color_menu_btn": ent_m_btn.get().strip(),
                "color_menu_hover": ent_m_hov.get().strip(),
                "color_menu_texto": ent_m_txt.get().strip(),
                "terminos_cotizacion": txt_terminos.get("1.0", "end-1c").strip(),
                "orden_operativos": ext_ord(lb_ops, "orden_operativos"),
                "orden_finanzas": ext_ord(lb_fin, "orden_finanzas"),
                "orden_ajustes": ext_ord(lb_aju, "orden_ajustes")
            })

            # 🧩 Claves que administran otros módulos (Bancos, Compras): se releen del
            # disco para que guardar la configuración general NO las borre.
            try:
                en_disco = cargar_config_local() or {}
                for clave_externa in ("categorias_gastos", "comision_interbancaria"):
                    if clave_externa in en_disco:
                        nueva_config[clave_externa] = en_disco[clave_externa]
                # 🏦 Las cuentas bancarias solo se escriben si el usuario las editó en
                # ESTA ventana; si no, se conserva lo que ya hay guardado (en el equipo
                # y en la base de datos) para no borrarlas por un formulario viejo.
                if cuentas_editadas["cambiado"]:
                    nueva_config["cuentas_bancarias"] = [dict(c) for c in cuentas_bancarias_actual]
                elif "cuentas_bancarias" in en_disco:
                    nueva_config["cuentas_bancarias"] = en_disco["cuentas_bancarias"]
                else:
                    nueva_config.pop("cuentas_bancarias", None)
            except Exception:
                pass

            try:
                with open(archivo_config, "w", encoding="utf-8") as f:
                    json.dump(nueva_config, f, indent=4)
                
                config_para_nube = nueva_config.copy()
                for clave_local in ["ruta_drive", "impresora", "ruta_logo_cotizacion", "rclone_remote"]:
                    config_para_nube.pop(clave_local, None)

                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        # 🧩 Antes de reemplazar la configuración de la nube se conservan
                        # las claves que administran otros módulos o los demás equipos
                        # (cuentas bancarias, categorías de gastos...). Sin esto, guardar
                        # la configuración general borraba la configuración del banco.
                        try:
                            cursor.execute("SELECT data FROM configuracion_sistema WHERE id = 1")
                            fila_nube = cursor.fetchone()
                            datos_nube = fila_nube[0] if fila_nube else None
                            if isinstance(datos_nube, str):
                                datos_nube = json.loads(datos_nube)
                            datos_nube = datos_nube if isinstance(datos_nube, dict) else {}
                            for clave_externa in ("categorias_gastos", "comision_interbancaria"):
                                if clave_externa in datos_nube:
                                    config_para_nube[clave_externa] = datos_nube[clave_externa]
                            if not cuentas_editadas["cambiado"] and "cuentas_bancarias" in datos_nube:
                                config_para_nube["cuentas_bancarias"] = datos_nube["cuentas_bancarias"]
                        except Exception:
                            conn.rollback()
                        cursor.execute("""
                        INSERT INTO configuracion_sistema (id, data, actualizado_el)
                        VALUES (1, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, actualizado_el = CURRENT_TIMESTAMP
                        """, (json.dumps(config_para_nube),))
                        conn.commit()
                    except Exception as e:
                        print("Error respaldando config en la nube:", e)
                    finally:
                        liberar_conexion(conn)

                # 🚀 CORTE DE COMPRAS Y VENTAS: si hay fecha de comienzo, ofrecer la limpieza definitiva
                if fecha_corte_nueva and FECHA_SISTEMA_DISPONIBLE:
                    fecha_corte_previa = obtener_fecha_comienzo(config_actual)
                    aviso_cambio = ""
                    if fecha_corte_previa != fecha_corte_nueva:
                        aviso_cambio = f"La fecha de comienzo cambió de {fecha_corte_previa.strftime('%d/%m/%Y') if fecha_corte_previa else 'Sin definir'} a {fecha_corte_nueva.strftime('%d/%m/%Y')}.\n\n"
                    if messagebox.askyesno(
                        "Aplicar corte de compras y ventas",
                        aviso_cambio +
                        f"¿Deseas eliminar ahora todas las COMPRAS y VENTAS anteriores al {fecha_corte_nueva.strftime('%d/%m/%Y')}?\n\n"
                        "La eliminación es definitiva (facturas recibidas, emitidas, pagos y notas de crédito).\n\n"
                        "Si eliges 'No', la fecha quedará guardada y podrás ejecutar la limpieza después con el botón rojo.",
                        parent=v_conf
                    ):
                        def al_terminar_corte(resultado, error):
                            if error:
                                messagebox.showerror("Configuración guardada, limpieza fallida", f"La fecha se guardó correctamente, pero no se pudo eliminar la información:\n{error}", parent=v_conf)
                            else:
                                cache_sistema.invalidar()
                                messagebox.showinfo("Limpieza completada", resumen_purga(resultado), parent=v_conf)
                            lanzar_sync_background()
                            try:
                                v_conf.destroy()
                            except Exception:
                                pass
                            self.construir_dashboard_spa()

                        _purgar_con_progreso(fecha_corte_nueva, al_terminar_corte)
                        return
                    messagebox.showinfo(
                        "Corte guardado sin limpiar",
                        "La Fecha de Comienzo quedó guardada.\n\nNo se eliminó ningún registro. Puedes ejecutar la limpieza cuando quieras con el botón rojo "
                        "'Eliminar Compras y Ventas de esta fecha hacia atrás'.",
                        parent=v_conf
                    )

                messagebox.showinfo("Éxito", "Las configuraciones del sistema se guardaron correctamente en la Nube y en este equipo.\n\nLos cambios visuales se aplicarán inmediatamente.", parent=v_conf)
                lanzar_sync_background()
                v_conf.destroy()
                self.construir_dashboard_spa()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar la configuración:\n{e}", parent=v_conf)
                
        ctk.CTkButton(f_scroll, text="💾 Guardar Todos los Cambios", font=("Arial", 14, "bold"), height=45, fg_color="#1f538d", hover_color="#163b65", command=guardar_configuracion).pack(pady=25)

    def abrir_gestion_usuarios(self):
        if not self.tiene_permiso("usuarios"):
            return messagebox.showerror("Acceso Denegado", "No tiene permisos para modificar usuarios.")
        v_usr = ctk.CTkToplevel(self.root)
        v_usr.title("Configuración de Usuarios y Permisos")
        v_usr.geometry("1000x580")
        # 🪟 Sin grab_set(): no debe bloquear los demás módulos abiertos.
        self.ventanas_modulos["usuarios"] = v_usr
        v_usr.bind("<Destroy>", lambda e, c="usuarios": self._al_destruir_ventana(e, c), add="+")
        self._actualizar_pestanas_modulos()
        traer_al_frente(v_usr)
        main_split = ctk.CTkFrame(v_usr, fg_color="transparent")
        main_split.pack(fill="both", expand=True, padx=15, pady=15)
        left_panel = ctk.CTkFrame(main_split, fg_color="transparent")
        left_panel.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(left_panel, text="👥 CREAR O MODIFICAR USUARIO", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(0, 15))
        f_form = ctk.CTkFrame(left_panel, fg_color="transparent")
        f_form.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(f_form, text="Usuario:", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky="w", pady=4)
        ent_u = ctk.CTkEntry(f_form, width=160)
        ent_u.grid(row=0, column=1, sticky="w", pady=4, padx=5)
        ctk.CTkLabel(f_form, text="Clave (Nueva):", font=("Arial", 11, "bold")).grid(row=0, column=2, sticky="w", pady=4, padx=10)
        ent_c = ctk.CTkEntry(f_form, width=160, placeholder_text="(Dejar en blanco)")
        ent_c.grid(row=0, column=3, sticky="w", pady=4)
        ctk.CTkLabel(f_form, text="Rol Nominal:", font=("Arial", 11, "bold")).grid(row=1, column=0, sticky="w", pady=10)
        cmb_r = ctk.CTkComboBox(f_form, values=["Super Administrador", "Administrador", "Comercial", "Logistica"], width=180, state="readonly")
        cmb_r.grid(row=1, column=1, sticky="w", pady=10, padx=5)
        f_tbl = ctk.CTkFrame(left_panel, corner_radius=8)
        f_tbl.pack(fill="both", expand=True, padx=10, pady=5)
        tbl_u = ttk.Treeview(f_tbl, columns=("id", "usuario", "rol"), show="headings", height=8)
        tbl_u.heading("id", text="ID")
        tbl_u.heading("usuario", text="Usuario")
        tbl_u.heading("rol", text="Rol Nominal")
        tbl_u.column("id", width=55, anchor="center")
        tbl_u.column("usuario", width=220)
        tbl_u.column("rol", width=200)
        scr_u = ttk.Scrollbar(f_tbl, orient="vertical", command=tbl_u.yview)
        tbl_u.configure(yscrollcommand=scr_u.set)
        tbl_u.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scr_u.pack(side="right", fill="y", pady=10, padx=(0, 10))
        right_panel = ctk.CTkScrollableFrame(main_split, width=320, fg_color="#f8f9fa", corner_radius=10, border_width=1, border_color="#cccccc")
        right_panel.pack(side="right", fill="y", padx=(15, 0))
        ctk.CTkLabel(right_panel, text="🛡️ PERMISOS POR MÓDULO", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        self.vars_permisos = {}
        for key_mod, nombre_mod in self.modulos_sistema.items():
            var_cb = tk.BooleanVar(value=False)
            self.vars_permisos[key_mod] = var_cb
            ctk.CTkCheckBox(right_panel, text=nombre_mod, variable=var_cb, font=("Arial", 12)).pack(anchor="w", padx=20, pady=5)
        f_acciones_perm = ctk.CTkFrame(right_panel, fg_color="transparent")
        f_acciones_perm.pack(fill="x", padx=10, pady=15)
        ctk.CTkButton(f_acciones_perm, text="Marcar Todo", width=120, fg_color="#34495e", command=lambda: [v.set(True) for v in self.vars_permisos.values()]).pack(side="left", padx=5)
        ctk.CTkButton(f_acciones_perm, text="Desmarcar", width=120, fg_color="#7f8c8d", command=lambda: [v.set(False) for v in self.vars_permisos.values()]).pack(side="right", padx=5)
        self.dict_permisos_usuarios = {}

        def cargar_usuarios():
            tbl_u.delete(*tbl_u.get_children())
            conn = conectar_db(silencioso=True)
            if not conn:
                return
            try:
                c = conn.cursor()
                c.execute("SELECT id, usuario, rol, permisos FROM usuarios ORDER BY usuario ASC")
                for r in c.fetchall():
                    tbl_u.insert("", tk.END, values=(r[0], r[1], r[2]))
                    try:
                        self.dict_permisos_usuarios[r[1]] = json.loads(r[3]) if r[3] else {}
                    except Exception:
                        self.dict_permisos_usuarios[r[1]] = {}
            except Exception:
                pass
            finally:
                liberar_conexion(conn)

        def al_seleccionar_usuario(e):
            sel = tbl_u.selection()
            if not sel:
                return
            user_sel = tbl_u.item(sel[0], "values")[1]
            ent_u.delete(0, tk.END)
            ent_u.insert(0, user_sel)
            ent_c.delete(0, tk.END)
            cmb_r.set(tbl_u.item(sel[0], "values")[2])
            permisos_guardados = self.dict_permisos_usuarios.get(user_sel, {})
            for key_mod, var_cb in self.vars_permisos.items():
                var_cb.set(permisos_guardados.get(key_mod, False))
        tbl_u.bind("<<TreeviewSelect>>", al_seleccionar_usuario)

        def registrar_o_modificar():
            u = ent_u.get().strip().lower()
            c_str = ent_c.get().strip()
            r = cmb_r.get()
            if not u:
                return
            nuevos_permisos = {key: var.get() for key, var in self.vars_permisos.items()}
            permisos_json = json.dumps(nuevos_permisos)
            conn = conectar_db()
            if not conn:
                return
            try:
                c = conn.cursor()
                c.execute("SELECT id FROM usuarios WHERE usuario = %s", (u,))
                existe = c.fetchone()
                if existe:
                    if c_str:
                        c.execute(
                            "UPDATE usuarios SET clave_hash=%s, clave=NULL, rol=%s, permisos=%s WHERE usuario=%s",
                            (hash_password(c_str), r, permisos_json, u)
                        )
                    else:
                        c.execute(
                            "UPDATE usuarios SET rol=%s, permisos=%s WHERE usuario=%s",
                            (r, permisos_json, u)
                        )
                    registrar_auditoria(self.usuario_activo, "Seguridad", f"Modificó el usuario '{u}' y sus permisos")
                else:
                    if not c_str:
                        liberar_conexion(conn)
                        return messagebox.showwarning("Error", "Falta clave obligatoria para usuario nuevo.", parent=v_usr)
                    c.execute(
                        "INSERT INTO usuarios (usuario, clave, clave_hash, rol, permisos) VALUES (%s, NULL, %s, %s, %s)",
                        (u, hash_password(c_str), r, permisos_json)
                    )
                    registrar_auditoria(self.usuario_activo, "Seguridad", f"Creó al usuario '{u}'")
                conn.commit()
                messagebox.showinfo("Éxito", "Usuario y permisos guardados exitosamente.", parent=v_usr)
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=v_usr)
            finally:
                liberar_conexion(conn)
            ent_u.delete(0, tk.END)
            ent_c.delete(0, tk.END)
            cargar_usuarios()

        def eliminar_usuario():
            if not tbl_u.selection():
                return
            u_borrar = tbl_u.item(tbl_u.selection(), "values")[1]
            if u_borrar == self.usuario_activo:
                return messagebox.showwarning("Error", "No puedes eliminar tu propia cuenta activa.", parent=v_usr)
            conn = conectar_db()
            if not conn:
                return
            try:
                c = conn.cursor()
                c.execute("SELECT rol FROM usuarios WHERE usuario = %s", (u_borrar,))
                fila = c.fetchone()
                if fila and str(fila[0]).strip() == "Super Administrador":
                    c.execute("SELECT COUNT(*) FROM usuarios WHERE rol = 'Super Administrador'")
                    if (c.fetchone()[0] or 0) <= 1:
                        messagebox.showwarning("Error", "No puedes eliminar al único Super Administrador del sistema.", parent=v_usr)
                        return
                if messagebox.askyesno("Confirmar", f"¿Eliminar al usuario '{u_borrar}' permanentemente?", parent=v_usr):
                    c.execute("DELETE FROM usuarios WHERE usuario = %s", (u_borrar,))
                    conn.commit()
                    registrar_auditoria(self.usuario_activo, "Seguridad", f"Eliminó permanentemente al usuario '{u_borrar}'")
                    cargar_usuarios()
            except Exception as e:
                messagebox.showerror("Error", str(e), parent=v_usr)
            finally:
                liberar_conexion(conn)
                
        f_btn = ctk.CTkFrame(left_panel, fg_color="transparent")
        f_btn.pack(fill="x", padx=10, pady=15)
        ctk.CTkButton(f_btn, text="💾 Guardar Usuario", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=registrar_o_modificar).pack(side="left", padx=5)
        ctk.CTkButton(f_btn, text="🗑️ Eliminar Usuario", font=("Arial", 12, "bold"), command=eliminar_usuario, fg_color="#e74c3c", hover_color="#c0392b").pack(side="left", padx=5)
        cargar_usuarios()

    def buscar_actualizaciones_github(self):
        # En macOS no se revisan actualizaciones: el cliente se actualiza
        # ejecutando el archivo .command del instalador.
        if sys.platform == "darwin":
            return
        def tarea_check():
            try:
                ctx = crear_contexto_ssl_seguro()
                url = "https://api.github.com/repos/Alfredcollie/Sistema_control_eventos/releases/latest"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, context=ctx, timeout=5) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        version_github = data.get("tag_name", "")
                        
                        if version_github and _version_a_tupla(version_github) > _version_a_tupla(VERSION_ACTUAL):
                            def preguntar_actualizacion():
                                respuesta = messagebox.askyesno(
                                    "🚀 Nueva Actualización Disponible",
                                    f"¡Hay una nueva versión de Black Cube disponible!\n\n"
                                    f"Versión actual: {VERSION_ACTUAL}\n"
                                    f"Versión nueva: {version_github}\n\n"
                                    "¿Deseas descargar e instalar la actualización ahora?",
                                    parent=self.root
                                )
                                if respuesta:
                                    webbrowser.open("https://github.com/Alfredcollie/Sistema_control_eventos/releases/latest")
                                    
                            self.root.after(2000, preguntar_actualizacion)
            except Exception:
                pass

        threading.Thread(target=tarea_check, daemon=True).start()

    def confirmar_salida(self):
        if messagebox.askyesno("Confirmar Salida", "⚠️ ¿Estás seguro de que deseas cerrar completamente el sistema?", parent=self.root):
            try:
                registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró el sistema completamente desde la X")
            except Exception:
                pass
            self.root.quit()
            self.root.destroy()


if __name__ == "__main__":
    try:
        from validacion_licencia import comprobar_acceso
        if not comprobar_acceso():
            sys.exit()
    except ImportError:
        pass

    root = ctk.CTk()

    # Scroll de rueda global: funciona sobre toda la ventana (y subventanas),
    # no solo sobre widgets internos concretos.
    try:
        from scroll_utils import instalar_scroll_global
        instalar_scroll_global(root)
    except Exception:
        pass

    app = ControlGeneralEventos(root)
    root.mainloop()