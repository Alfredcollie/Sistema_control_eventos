# -*- coding: utf-8 -*-
"""
CONTROL_GENERAL.PY - SISTEMA DE CONTROL DE FLOTA AUTOMOTRIZ (v2 SEGURO Y MULTIPLATAFORMA)
Optimizado para máximo rendimiento y compatibilidad nativa en Windows y macOS.
- bcrypt (sin claves planas), sin usuarios hardcoded, migración automática.
- Usuarios con activo / intentos_fallidos, protección del Super Admin único.
- Config con ruta absoluta (app_paths), menú con módulos dinámicos.
- Integración Rclone Inteligente con Sincronización Automática en Segundo Plano.
- Soporte nativo para pantallas Retina (Mac) y High-DPI (Windows).
"""
import psycopg2
import tkinter as tk
import customtkinter as ctk
from tkinter import messagebox, filedialog, ttk, colorchooser
import subprocess
import sys
import os
import shutil
import json
import importlib
import urllib.request
import urllib.error
import ssl
import bcrypt
import threading
from datetime import datetime, timedelta
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema
from app_paths import CONFIG_FILE

if sys.platform == "win32":
    import ctypes

try:
    from PIL import Image
    RESAMPLE_FILTER = getattr(Image, "Resampling", Image).LANCZOS if hasattr(Image, "Resampling") else getattr(Image, "LANCZOS", Image.ANTIALIAS)
    PIL_DISPONIBLE = True
except ImportError:
    PIL_DISPONIBLE = False
    RESAMPLE_FILTER = None

try:
    from validacion_licencia import comprobar_acceso
except ImportError:
    comprobar_acceso = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# =========================================================
# MULTIPLATAFORMA: Ocultar consola solo en Windows
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass


def maximizar_ventana(ventana):
    """Maximiza la ventana de forma nativa según el sistema operativo."""
    try:
        if sys.platform == "win32":
            ventana.state("zoomed")
        elif sys.platform == "darwin":  # macOS no soporta -zoomed
            ventana.update_idletasks()
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h-30}+0+0")
        else:  # Linux
            try:
                ventana.attributes("-zoomed", True)
            except Exception:
                ventana.state("zoomed")
    except Exception:
        try:
            w = ventana.winfo_screenwidth()
            h = ventana.winfo_screenheight()
            ventana.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass


def ruta_recurso(ruta_relativa):
    """Ruta absoluta válida en desarrollo y compilado (.exe / .app)."""
    try:
        ruta_base = sys._MEIPASS
    except Exception:
        ruta_base = os.path.abspath(".")
    return os.path.normpath(os.path.join(ruta_base, ruta_relativa))


def _contexto_ssl_seguro():
    """Contexto SSL que funciona en Windows y macOS.

    En macOS, Python (instalado desde python.org o dentro de un .app de
    PyInstaller) no tiene el bundle de certificados raíz y urllib lanza:
        CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate
    Por eso usamos certifi (incluido con 'requests'), que trae su propio
    bundle de CAs. Si certifi no está disponible, se desactiva la
    verificación como último recurso.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl._create_unverified_context()
        except AttributeError:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            return ctx


# =========================================================
# FUNCIONES GLOBALES DE RCLONE (Sincronización Silenciosa)
# =========================================================
def obtener_comando_rclone():
    """Detecta rclone en Windows, macOS (Intel y Apple Silicon) y Linux."""
    nombre_ejecutable = "rclone.exe" if sys.platform == "win32" else "rclone"
    
    if hasattr(sys, '_MEIPASS'):
        ruta_bundle = os.path.join(sys._MEIPASS, nombre_ejecutable)
        if os.path.exists(ruta_bundle):
            return ruta_bundle
            
    ruta_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), nombre_ejecutable)
    if os.path.exists(ruta_local):
        return ruta_local

    en_path = shutil.which("rclone")
    if en_path:
        return en_path

    if sys.platform != "win32":
        rutas_unix = [
            "/opt/homebrew/bin/rclone",      # Mac Apple Silicon (M1/M2/M3/M4)
            "/usr/local/bin/rclone",         # Mac Intel / Linux local
            "/opt/local/bin/rclone",         # MacPorts
            os.path.expanduser("~/.local/bin/rclone"),
            "/usr/bin/rclone"
        ]
        for r in rutas_unix:
            if os.path.exists(r):
                return r
                
    return "rclone"


def normalizar_ruta_local(ruta):
    """Convierte rutas locales a una ruta válida del sistema actual.

    Si la configuración guarda una ruta absoluta de Windows (ej: C:\\Users\\...
    o C:/Users/...) y el programa corre en macOS/Linux, esa ruta no existe y
    debe ignorarse (el usuario elegirá una carpeta válida en cada equipo).
    """
    if not ruta:
        return ""
    ruta = ruta.strip()
    if sys.platform != "win32":
        # Detectar prefijo de unidad de Windows (C:, D:, ...) sin importar el separador
        if len(ruta) >= 2 and ruta[1] == ":" and ruta[0].isalpha():
            return ""
        # Normalizar separadores de ruta para el sistema actual
        ruta = ruta.replace("\\", "/")
    return ruta


def _carpeta_archivos_programa(ruta_drive=""):
    """Carpeta donde se guardan los archivos generados por el programa.
    Usa la ruta_drive configurada si es válida y escribible; si no, la carpeta
    del programa. Es el mismo lugar donde se guardan cobranzas, órdenes, etc."""
    ruta = normalizar_ruta_local(ruta_drive or "")
    if ruta:
        ruta = os.path.expanduser(ruta)
        if os.path.isdir(ruta):
            try:
                prueba = os.path.join(ruta, ".escritura_programa")
                with open(prueba, "w", encoding="utf-8") as f:
                    f.write("x")
                os.remove(prueba)
                return ruta
            except Exception:
                pass
    return os.path.dirname(os.path.abspath(__file__))


def guardar_logo_en_programa(ruta_origen, ruta_drive=""):
    """Copia el logo a la carpeta de archivos del programa y devuelve la ruta
    canónica. Así el logo vive junto a los demás archivos (se sincroniza con la
    nube) y no depende de una ruta externa temporal."""
    try:
        if not ruta_origen or not os.path.isfile(ruta_origen):
            return ruta_origen or ""
        carpeta = _carpeta_archivos_programa(ruta_drive)
        ext = os.path.splitext(ruta_origen)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            ext = ".png"
        destino = os.path.join(carpeta, f"logo_encabezado{ext}")
        if os.path.abspath(ruta_origen) == os.path.abspath(destino):
            return destino
        shutil.copy2(ruta_origen, destino)
        return destino
    except Exception:
        return ruta_origen or ""


def cargar_configuracion_general():
    config = {
        "ruta_drive": "",
        "rclone_remote": "gdrive:",
        "rclone_ruta_nube": "BlackCube",
        "impresora": "",
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "cuentas_bancarias": [],
        "ruc_empresa": "",
        "razon_social_empresa": "",
        "igv_porcentaje": "0",
        "retencion_porcentaje": "0",
        "detraccion_porcentaje": "12",
        "renta_mensual_porcentaje": "0",
        "renta_anual_porcentaje": "0",
        "regimen_empresa": "MYPE Tributario",
        "proveedor_fe": "Nubefact",
        "url_api_fe": "",
        "token_api_fe": "",
        "ultimo_factura": "F001-0",
        "ultimo_boleta": "B001-0",
        "ultimo_recibo": "E001-0",
        "usuario_sol": "",
        "clave_sol": "",
        "client_id_sire": "",
        "client_secret_sire": "",
        "2fa_metodo": "Inactivo",
        "tel_bot_token": "",
        "tel_chat_id": "",
        "email_smtp": "smtp.gmail.com",
        "email_port": "587",
        "email_user": "",
        "email_pass": "",
        "email_dest": "",
        "twi_sid": "",
        "twi_token": "",
        "twi_from": "",
        "twi_to": "",
        "dias_alerta_vencimiento": "30",
        "alerta_aceite_km": "5000",
        "alerta_aceite_meses": "6",
        "alerta_mantenimiento_general_km": "10000",
        "color_menu_fondo": "#1a252c",
        "color_menu_btn": "#1f538d",
        "color_menu_hover": "#163b65",
        "color_menu_texto": "white",
        "orden_operativos": ["clientes", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "flota", "choferes"],
        "orden_finanzas": ["ventas", "compras", "libro_diario", "libro_mayor", "impuestos", "cobranza", "dashboard"],
        "orden_ajustes": ["configuracion", "usuarios", "bitacora"]
    }
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config


def ejecutar_sincronizacion_silenciosa():
    """Ejecuta una copia bilateral sin bloquear el programa principal."""
    config = cargar_configuracion_general()
    local = os.path.expanduser(normalizar_ruta_local(config.get("ruta_drive", "")))
    remote = config.get("rclone_remote", "gdrive:").strip()
    nube = config.get("rclone_ruta_nube", "BlackCube").strip()

    if not local or not remote or not nube or not os.path.exists(local):
        return

    cmd = obtener_comando_rclone()
    ruta_remota = f"{remote}{nube}" if remote.endswith(":") else f"{remote}:{nube}"

    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    try:
        subprocess.run([cmd, "copy", ruta_remota, local, "--update", "--quiet"], timeout=120, **kwargs)
        subprocess.run([cmd, "copy", local, ruta_remota, "--update", "--quiet"], timeout=120, **kwargs)
    except Exception:
        pass


def lanzar_sync_background():
    threading.Thread(target=ejecutar_sincronizacion_silenciosa, daemon=True).start()


# =========================================================
# SEGURIDAD: HASH DE CONTRASEÑAS (BCRYPT)
# =========================================================
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# =========================================================
# INICIALIZACIÓN DE TABLAS + MIGRACIÓN
# =========================================================
def inicializar_seguridad_db():
    conn = conectar_db(silencioso=True)
    if not conn:
        return
    try:
        with conn.cursor() as cursor:
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
            upgrades = [
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS clave_hash TEXT",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS permisos TEXT DEFAULT '{}'",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT TRUE",
                "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS intentos_fallidos INTEGER DEFAULT 0",
                "ALTER TABLE usuarios ALTER COLUMN clave DROP NOT NULL",
            ]
            for sql in upgrades:
                try:
                    cursor.execute(sql)
                except Exception:
                    conn.rollback()

            try:
                cursor.execute("SELECT id, clave FROM usuarios WHERE clave IS NOT NULL AND (clave_hash IS NULL OR clave_hash = '')")
                for uid, clave_plana in cursor.fetchall():
                    if clave_plana:
                        cursor.execute("UPDATE usuarios SET clave_hash = %s, clave = NULL WHERE id = %s", (hash_password(clave_plana), uid))
                conn.commit()
            except Exception:
                conn.rollback()

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS bitacora_auditoria (
                id SERIAL PRIMARY KEY,
                fecha VARCHAR(20),
                hora VARCHAR(20),
                usuario VARCHAR(100),
                modulo VARCHAR(100),
                accion TEXT
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS configuracion_geocerca (
                id SERIAL PRIMARY KEY,
                latitud NUMERIC,
                longitud NUMERIC,
                radio NUMERIC,
                estado VARCHAR(20) DEFAULT 'Activo'
            )
            """)
            cursor.execute("SELECT COUNT(*) FROM configuracion_geocerca")
            if cursor.fetchone()[0] == 0:
                cursor.execute("INSERT INTO configuracion_geocerca (latitud, longitud, radio) VALUES (-12.046374, -77.042793, 100.0)")

            cursor.execute("""
            CREATE TABLE IF NOT EXISTS registro_asistencia (
                id SERIAL PRIMARY KEY,
                placa VARCHAR(50),
                fecha VARCHAR(20),
                hora_entrada VARCHAR(20),
                hora_salida VARCHAR(20),
                estado VARCHAR(50)
            )
            """)
            conn.commit()
    except Exception as e:
        print("Error inicializando seguridad y GPS:", e)
    finally:
        liberar_conexion(conn)


class ControlGeneralEventos:
    def __init__(self, root):
        self.root = root
        self.root.title("SISTEMA DE CONTROL DE FLOTA AUTOMOTRIZ")
        self.root.protocol("WM_DELETE_WINDOW", self.confirmar_salida)

        # 🔒 Verificación de licencia al entrar al sistema
        if comprobar_acceso is not None:
            try:
                if not comprobar_acceso():
                    # La pantalla de bloqueo ya se mostró; se cierra la aplicación
                    try:
                        self.root.destroy()
                    except Exception:
                        pass
                    sys.exit(0)
            except Exception as e:
                print(f"[Licencia Error] {e}")

        inicializar_seguridad_db()
        self.usuario_activo = "No autenticado"
        self.rol_activo = "Ninguno"
        self.permisos_activos = {}
        self.modulos_sistema = {
            "clientes": "👥 Gestión de Clientes",
            "ordenes_cliente": "📥 Órdenes de Cliente",
            "cronograma": "📅 Cronograma y Vencimientos",
            "ordenes": "📝 Órdenes de Servicio / Compra",
            "proveedores": "📦 Gestión de Proveedores",
            "flota": "🚙 Flota Automotriz",
            "choferes": "👤 Control de Choferes",
            "ventas": "💼 Ventas (Facturas y Cobros)",
            "compras": "🛒 Compras (Facturas y Pagos)",
            "libro_diario": "📖 Libro Diario General",
            "libro_mayor": "📊 Libro Mayor Analítico",
            "impuestos": "🧮 Cálculo de Impuestos",
            "cobranza": "💰 Cálculo de Cobranza",
            "dashboard": "📈 Dashboard Gerencial",
            "configuracion": "⚙️ Configuración General",
            "usuarios": "🛠️ Configurar Usuarios",
            "bitacora": "📜 Bitácora de Auditoría"
        }
        self.funciones_modulos = {
            "clientes": self.abrir_modulo_clientes,
            "ordenes_cliente": self.abrir_modulo_ordenes_cliente,
            "cronograma": self.abrir_modulo_cronograma,
            "ordenes": self.abrir_modulo_ordenes,
            "proveedores": self.abrir_modulo_proveedores,
            "flota": self.abrir_modulo_flota,
            "choferes": self.abrir_modulo_choferes,
            "ventas": self.abrir_modulo_ventas,
            "compras": self.abrir_modulo_compras,
            "libro_diario": self.abrir_modulo_libro_diario,
            "libro_mayor": self.abrir_modulo_libro_mayor,
            "impuestos": self.abrir_calculo_impuestos,
            "cobranza": self.abrir_modulo_cobranza,
            "dashboard": self.abrir_estadisticas_financiera,
            "configuracion": self.abrir_configuracion_general,
            "usuarios": self.abrir_gestion_usuarios,
            "bitacora": self.abrir_modulo_bitacora
        }
        self.root.withdraw()
        self.abrir_ventana_login()

    def tiene_permiso(self, modulo_key):
        if self.rol_activo == "Super Administrador":
            return True
        return self.permisos_activos.get(modulo_key, False)

    # =======================================================
    # LOGIN SEGURO
    # =======================================================
    def abrir_ventana_login(self):
        self.v_login = ctk.CTkToplevel(self.root)
        self.v_login.title("Acceso Seguro")
        ancho_ventana = 400
        alto_ventana = 520
        self.v_login.geometry(f"{ancho_ventana}x{alto_ventana}")
        self.v_login.resizable(False, False)
        self.v_login.protocol("WM_DELETE_WINDOW", self.root.quit)
        
        self.v_login.update_idletasks()
        x = max(0, (self.v_login.winfo_screenwidth() // 2) - (ancho_ventana // 2))
        y = max(0, (self.v_login.winfo_screenheight() // 2) - (alto_ventana // 2))
        self.v_login.geometry(f"{ancho_ventana}x{alto_ventana}+{x}+{y}")
        self.v_login.lift()
        self.v_login.focus_force()
        self.v_login.grab_set()

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
                        if not self.v_login.winfo_exists():
                            return
                        if self.fase_animacion == "creciendo":
                            self.tamanio_actual += 15
                            if self.tamanio_actual >= self.tamanio_maximo:
                                self.fase_animacion = "rebotando"
                        elif self.fase_animacion == "rebotando":
                            self.tamanio_actual -= 4
                            if self.tamanio_actual <= self.tamanio_final:
                                self.fase_animacion = "terminado"
                                self.tamanio_actual = self.tamanio_final
                                
                        img_animada = ctk.CTkImage(
                            light_image=imagen_original,
                            dark_image=imagen_original,
                            size=(self.tamanio_actual, self.tamanio_actual)
                        )
                        self.lbl_logo.configure(image=img_animada)
                        if self.fase_animacion != "terminado":
                            self.v_login.after(16, animar_logo)

                    animar_logo()
                except Exception:
                    self.lbl_logo.configure(text="[ Error cargando logo ]", font=("Arial", 10))
            else:
                self.lbl_logo.configure(text="[ Logo no encontrado ]", font=("Arial", 12, "italic"))
        else:
            self.lbl_logo.configure(text="[ Instalar Pillow para ver logo ]", font=("Arial", 10))

        ctk.CTkLabel(frame_log, text="SISTEMA DE CONTROL DE FLOTA", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(0, 0))
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
                    "Sin conexión a Internet.\n\nIniciando en MODO LECTURA:\n• Podrás revisar clientes, cronograma y flota.\n• No se permitirán modificaciones hasta reconectarte."
                )
                self.usuario_activo = user if user else "Invitado"
                self.rol_activo = "Invitado Offline"
                self.permisos_activos = {"clientes": True, "cronograma": True, "flota": True}
                if hasattr(cache_sistema, "cargar_copia_local"):
                    cache_sistema.cargar_copia_local()
                cache_sistema.modo_lectura = True
                self.v_login.destroy()
                self.construir_dashboard_spa()
                return

            try:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT rol, permisos, clave_hash, activo FROM usuarios WHERE usuario = %s", (user,))
                    resultado = cursor.fetchone()
                    if not resultado:
                        messagebox.showerror("Acceso Denegado", "Usuario o contraseña incorrectos.")
                        registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión fallido (usuario no existe)")
                        ent_pass.delete(0, tk.END)
                        ent_user.focus()
                        return
                        
                    rol_db, permisos_str, clave_hash, activo = resultado
                    if not activo:
                        messagebox.showerror("Acceso Denegado", "Este usuario está desactivado.\nContacta al administrador.")
                        registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión con usuario desactivado")
                        return
                        
                    if not clave_hash or not verify_password(clave, clave_hash):
                        messagebox.showerror("Acceso Denegado", "Usuario o contraseña incorrectos.")
                        registrar_auditoria(user, "Seguridad", "Intento de inicio de sesión fallido")
                        ent_pass.delete(0, tk.END)
                        ent_user.focus()
                        return
                        
                    self.usuario_activo = user
                    self.rol_activo = str(rol_db).strip()
                    try:
                        self.permisos_activos = json.loads(permisos_str) if permisos_str else {}
                    except Exception:
                        self.permisos_activos = {}
                        
                    registrar_auditoria(self.usuario_activo, "Seguridad", "Inicio de sesión exitoso")
                    self.v_login.destroy()
                    self.construir_dashboard_spa()
            except Exception as e:
                messagebox.showerror("Error Crítico", f"Ocurrió un problema en el sistema:\n{e}")
            finally:
                liberar_conexion(conn)

        btn_entrar = ctk.CTkButton(
            frame_log,
            text="Ingresar al Sistema",
            width=200,
            height=35,
            font=("Arial", 12, "bold"),
            fg_color="#1f538d",
            hover_color="#163b65",
            command=verificar_credenciales
        )
        btn_entrar.pack(pady=5)

    # =======================================================
    # DASHBOARD PRINCIPAL Y CICLO DE SINCRONIZACIÓN
    # =======================================================
    def construir_dashboard_spa(self):
        for widget in self.root.winfo_children():
            widget.destroy()
            
        config = cargar_configuracion_general()
        c_fondo = config.get("color_menu_fondo", "#1a252c")
        c_btn = config.get("color_menu_btn", "#1f538d")
        c_hover = config.get("color_menu_hover", "#163b65")
        c_texto = config.get("color_menu_texto", "white")
        fondo_seguro = c_fondo if str(c_fondo).startswith("#") else "#1a252c"
        
        orden_ops = config.get("orden_operativos", ["clientes", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "flota", "choferes"])
        orden_fin = config.get("orden_finanzas", ["ventas", "compras", "libro_diario", "libro_mayor", "impuestos", "cobranza", "dashboard"])
        orden_aju = config.get("orden_ajustes", ["configuracion", "usuarios", "bitacora"])
        todas = orden_ops + orden_fin + orden_aju
        for k in self.modulos_sistema:
            if k not in todas:
                orden_ops.append(k)

        self.root.deiconify()
        maximizar_ventana(self.root)
        cache_sistema.iniciar_ciclo()
        
        self.root.after(5000, self.ciclo_sincronizacion_nube)
        self.root.lift()
        self.root.focus_force()
        
        self.sidebar = ctk.CTkFrame(self.root, width=280, corner_radius=0, fg_color=fondo_seguro)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        
        self.contenedor_central = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        self.contenedor_central.pack(side="right", fill="both", expand=True)
        
        frame_top_sidebar = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        frame_top_sidebar.pack(side="top", fill="x", pady=(10, 5))
        
        ruta_logo_sidebar = ruta_recurso("Logo.png")
        if os.path.exists(ruta_logo_sidebar):
            try:
                if PIL_DISPONIBLE:
                    img_raw = Image.open(ruta_logo_sidebar)
                    self.img_sidebar_ctk = ctk.CTkImage(light_image=img_raw, dark_image=img_raw, size=(120, 50))
                    lbl_img = ctk.CTkLabel(frame_top_sidebar, image=self.img_sidebar_ctk, text="")
                    lbl_img.pack(pady=(15, 10))
                else:
                    self.logo_img = tk.PhotoImage(file=ruta_logo_sidebar)
                    tk.Label(frame_top_sidebar, image=self.logo_img, bg=fondo_seguro).pack(pady=(15, 10))
            except Exception:
                pass

        ctk.CTkLabel(frame_top_sidebar, text=f"👤 {self.usuario_activo.upper()}", font=("Arial", 11, "bold"), text_color="#28a745").pack(pady=(0, 2))
        ctk.CTkLabel(frame_top_sidebar, text=f"Rol: {self.rol_activo}", font=("Arial", 10), text_color="white").pack(pady=(0, 5))
        
        frame_bottom_sidebar = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        frame_bottom_sidebar.pack(side="bottom", fill="x", pady=(5, 10))
        
        lbl_firma_sidebar = ctk.CTkLabel(frame_bottom_sidebar, text="Software desarrollado por Alfred Collie\nVersión 1.5.4 © 2026", font=("Arial", 9, "italic"), text_color="#7f8c8d")
        lbl_firma_sidebar.pack(side="bottom", pady=(2, 5))

        def cerrar_sistema():
            registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró el sistema")
            self.root.quit()
            self.root.destroy()

        def cambiar_usuario():
            registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró sesión para cambiar de usuario")
            self.usuario_activo = "No autenticado"
            self.rol_activo = "Ninguno"
            self.permisos_activos = {}
            self.root.withdraw()
            self.abrir_ventana_login()

        btn_salir = ctk.CTkButton(frame_bottom_sidebar, text="🚪 Salir del Sistema", command=cerrar_sistema, width=240, height=30, font=("Arial", 11, "bold"), fg_color="#c0392b", hover_color="#922b21")
        btn_salir.pack(side="bottom", pady=(2, 5))
        btn_cambio = ctk.CTkButton(frame_bottom_sidebar, text="🔄 Cambiar Usuario", command=cambiar_usuario, width=240, height=30, font=("Arial", 11, "bold"), fg_color="#555555", hover_color="#333333")
        btn_cambio.pack(side="bottom", pady=(2, 5))
        
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
                        crear_btn_menu(self.modulos_sistema[key], self.funciones_modulos[key])
                        
        self.mostrar_pantalla_bienvenida()

    def ciclo_sincronizacion_nube(self):
        lanzar_sync_background()
        self.root.after(600000, self.ciclo_sincronizacion_nube)

    def limpiar_contenedor(self):
        for widget in self.contenedor_central.winfo_children():
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
        
        config = cargar_configuracion_general()
        try:
            dias_alerta = int(config.get("dias_alerta_vencimiento", "30"))
        except ValueError:
            dias_alerta = 30
            
        conn = conectar_db(silencioso=True)
        if not conn:
            ctk.CTkLabel(f_dashboard, text="📡 MODO LECTURA OFFLINE: Mostrando información desde el respaldo local.", font=("Arial", 12, "bold"), text_color="#d35400").pack(anchor="w", pady=5)
            return

        hoy = datetime.now()
        alertas = []

        try:
            with conn.cursor() as cursor:
                if self.tiene_permiso("flota"):
                    try:
                        try:
                            cursor.execute("""
                                SELECT placa, vencimiento_soat, vencimiento_seguro, vencimiento_rt, 
                                       fec_rev_gas, fec_venc_bat, fec_venc_extintor, fec_aceite, 
                                       km_prox_correa, kilometraje 
                                FROM flota_vehiculos 
                                WHERE estado = 'Operativo'
                            """)
                            vehiculos = cursor.fetchall()
                        except Exception:
                            conn.rollback()
                            cursor.execute("SELECT placa, vencimiento_soat, vencimiento_seguro, vencimiento_rt FROM flota_vehiculos WHERE estado = 'Operativo'")
                            vehiculos = [(r[0], r[1], r[2], r[3], None, None, None, None, None, None) for r in cursor.fetchall()]

                        for v in vehiculos:
                            placa = v[0]
                            docs = {
                                "SOAT": v[1],
                                "Seguro": v[2],
                                "Revisión Técnica": v[3],
                                "Rev. Sist. Gas": v[4],
                                "Garantía Batería": v[5],
                                "Extintor": v[6]
                            }
                            for doc_nombre, doc_fecha in docs.items():
                                if doc_fecha and str(doc_fecha).strip():
                                    try:
                                        dt_venc = datetime.strptime(str(doc_fecha), "%d/%m/%Y").date()
                                        dias_restantes = (dt_venc - hoy.date()).days
                                        if dias_restantes < 0:
                                            alertas.append({"tipo": "peligro", "icono": "🚨", "titulo": f"{doc_nombre} Vencido ({placa})", "mensaje": f"Venció hace {abs(dias_restantes)} días ({doc_fecha})."})
                                        elif 0 <= dias_restantes <= dias_alerta:
                                            alertas.append({"tipo": "alerta", "icono": "⚠️", "titulo": f"{doc_nombre} por Vencer ({placa})", "mensaje": f"Vencerá en {dias_restantes} días ({doc_fecha})."})
                                    except Exception:
                                        pass

                            fec_aceite = v[7]
                            if fec_aceite and str(fec_aceite).strip():
                                try:
                                    meses_aceite = int(config.get("alerta_aceite_meses", "6"))
                                    dt_aceite = datetime.strptime(str(fec_aceite), "%d/%m/%Y").date()
                                    dt_prox_aceite = dt_aceite + timedelta(days=30 * meses_aceite)
                                    dias_restantes = (dt_prox_aceite - hoy.date()).days
                                    if dias_restantes < 0:
                                        alertas.append({"tipo": "peligro", "icono": "🛢️", "titulo": f"Cambio de Aceite Vencido ({placa})", "mensaje": f"Venció hace {abs(dias_restantes)} días (por tiempo)."})
                                    elif 0 <= dias_restantes <= dias_alerta:
                                        alertas.append({"tipo": "alerta", "icono": "🛢️", "titulo": f"Próximo Cambio de Aceite ({placa})", "mensaje": f"Vence en {dias_restantes} días (por tiempo)."})
                                except Exception:
                                    pass

                            km_prox = v[8]
                            km_actual = v[9]
                            if km_prox and str(km_prox).strip() and km_actual and str(km_actual).strip():
                                try:
                                    k_p = float(str(km_prox).replace(",", ""))
                                    k_a = float(str(km_actual).replace(",", ""))
                                    km_restantes = k_p - k_a
                                    if km_restantes < 0:
                                        alertas.append({"tipo": "peligro", "icono": "⚙️", "titulo": f"Correa de Distribución Vencida ({placa})", "mensaje": f"Excedido por {abs(km_restantes):g} KM."})
                                    elif 0 <= km_restantes <= 1000:
                                        alertas.append({"tipo": "alerta", "icono": "⚙️", "titulo": f"Correa de Distribución Próxima ({placa})", "mensaje": f"Faltan {km_restantes:g} KM para el cambio."})
                                except Exception:
                                    pass
                    except Exception as e:
                        print("Error alertas flota:", e)

                if self.tiene_permiso("choferes") or self.tiene_permiso("flota"):
                    try:
                        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'choferes')")
                        if cursor.fetchone()[0]:
                            try:
                                cursor.execute("SELECT nombres, vencimiento_licencia, seguro_salud_venc, seguro_vida_venc, fecha_nacimiento FROM choferes WHERE estado = 'Activo'")
                                choferes = cursor.fetchall()
                            except Exception:
                                conn.rollback()
                                cursor.execute("SELECT nombres, vencimiento_licencia FROM choferes WHERE estado = 'Activo'")
                                choferes = [(r[0], r[1], None, None, None) for r in cursor.fetchall()]

                            for c in choferes:
                                nombre, v_lic, v_salud, v_vida, f_nac = c
                                docs = {
                                    "Licencia": v_lic,
                                    "Seguro de Salud": v_salud,
                                    "Seguro Vida Ley": v_vida
                                }
                                for doc_nombre, doc_fecha in docs.items():
                                    if doc_fecha and str(doc_fecha).strip():
                                        try:
                                            dt_venc = datetime.strptime(str(doc_fecha), "%d/%m/%Y").date()
                                            dias_restantes = (dt_venc - hoy.date()).days
                                            if dias_restantes < 0:
                                                alertas.append({"tipo": "peligro", "icono": "🚨", "titulo": f"{doc_nombre} Vencida ({nombre})", "mensaje": f"Venció hace {abs(dias_restantes)} días ({doc_fecha})."})
                                            elif 0 <= dias_restantes <= dias_alerta:
                                                alertas.append({"tipo": "alerta", "icono": "⚠️", "titulo": f"{doc_nombre} por Vencer ({nombre})", "mensaje": f"Vencerá en {dias_restantes} días ({doc_fecha})."})
                                        except Exception:
                                            pass

                                if f_nac and str(f_nac).strip():
                                    try:
                                        dt_nac = datetime.strptime(str(f_nac), "%d/%m/%Y").date()
                                        dt_prox = dt_nac.replace(year=hoy.year)
                                        if dt_prox < hoy.date():
                                            dt_prox = dt_prox.replace(year=hoy.year + 1)
                                        dias_restantes = (dt_prox - hoy.date()).days
                                        if 0 <= dias_restantes <= dias_alerta:
                                            if dias_restantes == 0:
                                                alertas.append({"tipo": "peligro", "icono": "🎉", "titulo": f"¡Cumpleaños de {nombre}!", "mensaje": "Hoy es su cumpleaños."})
                                            else:
                                                alertas.append({"tipo": "alerta", "icono": "🎂", "titulo": f"Cumpleaños de {nombre}", "mensaje": f"Es en {dias_restantes} días ({dt_prox.strftime('%d/%m')})."})
                                    except Exception:
                                        pass
                    except Exception as e:
                        print("Error alertas choferes:", e)

                if self.tiene_permiso("ventas"):
                    try:
                        cursor.execute("SELECT id, fecha, dias_credito, total, COALESCE(det_monto, 0) FROM facturas_emitidas")
                        facturas_ventas = cursor.fetchall()
                        cursor.execute("SELECT id_factura, SUM(monto_pagado) FROM pagos_clientes GROUP BY id_factura")
                        pagos_ventas = {row[0]: float(row[1]) for row in cursor.fetchall()}
                        ventas_vencidas, deuda_ventas = 0, 0.0
                        for f in facturas_ventas:
                            try:
                                f_dt = datetime.strptime(f[1], "%d/%m/%Y")
                                vencimiento = f_dt + timedelta(days=f[2])
                                neto = float(f[3] if f[3] else 0) - float(f[4] if f[4] else 0)
                                saldo = neto - pagos_ventas.get(f[0], 0.0)
                                if saldo > 0.01 and hoy.date() > vencimiento.date():
                                    ventas_vencidas += 1
                                    deuda_ventas += saldo
                            except Exception:
                                pass
                        if ventas_vencidas > 0:
                            alertas.append({"tipo": "peligro", "icono": "⚠️", "titulo": "Cobros Vencidos", "mensaje": f"Tienes {ventas_vencidas} factura(s) de clientes atrasada(s) (S/. {deuda_ventas:,.2f})"})
                    except Exception:
                        pass

                if not alertas:
                    f_ok = ctk.CTkFrame(f_dashboard, fg_color="#d4edda", corner_radius=6, border_width=1, border_color="#c3e6cb", height=40)
                    f_ok.pack(fill="x", pady=5)
                    ctk.CTkLabel(f_ok, text="✅ Todo está al día. No hay notificaciones pendientes.", font=("Arial", 12, "bold"), text_color="#155724").pack(pady=8)
                else:
                    for al in alertas:
                        if al["tipo"] == "peligro":
                            c_fondo_al, c_borde_al, c_texto_al = "#f8d7da", "#f5c6cb", "#721c24"
                        elif al["tipo"] == "alerta":
                            c_fondo_al, c_borde_al, c_texto_al = "#fff3cd", "#ffeeba", "#856404"
                        else:
                            c_fondo_al, c_borde_al, c_texto_al = "#d1ecf1", "#bee5eb", "#0c5460"
                        f_al = ctk.CTkFrame(f_dashboard, fg_color=c_fondo_al, corner_radius=6, border_width=1, border_color=c_borde_al)
                        f_al.pack(fill="x", pady=4)
                        ctk.CTkLabel(f_al, text=f"{al['icono']} {al['titulo']}:", font=("Arial", 12, "bold"), text_color=c_texto_al).pack(side="left", padx=(10, 5), pady=6)
                        ctk.CTkLabel(f_al, text=al["mensaje"], font=("Arial", 12), text_color=c_texto_al).pack(side="left", padx=(0, 10), pady=6)

                ctk.CTkLabel(f_dashboard, text="📅 TAREAS PENDIENTES DEL CRONOGRAMA", font=("Arial", 14, "bold"), text_color="#27ae60").pack(anchor="w", pady=(25, 10))
                f_agenda = ctk.CTkFrame(f_dashboard, fg_color="#f8f9fa", corner_radius=6, border_width=1, border_color="#e0e0e0")
                f_agenda.pack(fill="x", pady=0)
                try:
                    cursor.execute("SELECT nombre_tarea, evento_asociado, fecha_limite FROM tareas_evento WHERE estado = 'Pendiente' ORDER BY fecha_limite ASC LIMIT 20")
                    tareas_agenda = cursor.fetchall()
                    tareas_mostradas = 0
                    for tar in tareas_agenda:
                        nom_tar, ev, fec_str = tar
                        try:
                            f_dt = datetime.strptime(fec_str, "%d/%m/%Y").date()
                            dias_faltan = (f_dt - hoy.date()).days
                            if dias_faltan < 0:
                                texto_dias = f"¡VENCIDA HACE {abs(dias_faltan)} DÍAS!"
                                color_t = "#c0392b"
                            elif dias_faltan == 0:
                                texto_dias = "¡ES HOY!"
                                color_t = "#d35400"
                            elif dias_faltan <= dias_alerta:
                                texto_dias = f"Faltan {dias_faltan} días"
                                color_t = "#e67e22"
                            else:
                                continue
                            lbl_text = f"📍 {f_dt.strftime('%d/%m/%Y')} | {nom_tar} ({ev.split(' | ')[0]}) - {texto_dias}"
                            ctk.CTkLabel(f_agenda, text=lbl_text, font=("Arial", 12, "bold"), text_color=color_t).pack(anchor="w", padx=15, pady=8)
                            tareas_mostradas += 1
                        except Exception:
                            pass
                    if tareas_mostradas == 0:
                        ctk.CTkLabel(f_agenda, text="No hay tareas críticas pendientes en el cronograma.", font=("Arial", 12, "italic"), text_color="gray").pack(pady=15)
                except Exception:
                    ctk.CTkLabel(f_agenda, text="No se pudo cargar la agenda.", font=("Arial", 12, "italic"), text_color="gray").pack(pady=15)

        except Exception as e:
            print("Error cargando dashboard:", e)
        finally:
            liberar_conexion(conn)

        f_pie = ctk.CTkFrame(f_dashboard, fg_color="transparent")
        f_pie.pack(fill="x", pady=(40, 20))
        ctk.CTkLabel(f_pie, text="Bienvenido", font=("Arial", 14, "bold"), text_color="gray").pack()
        ctk.CTkLabel(f_pie, text="Este es el resumen automático de tu operación para el día de hoy.\nSeleccione un módulo en el menú lateral para gestionar sus operaciones.", font=("Arial", 12, "italic"), text_color="gray", justify="center").pack(pady=(5, 0))            

    # =======================================================
    # FUNCIONES DE APERTURA DE MÓDULOS
    # =======================================================
    def abrir_modulo_ventas(self):
        if not self.tiene_permiso("ventas"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import modulo_ventas
            importlib.reload(modulo_ventas)
            app = modulo_ventas.ModuloVentasApp(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")

    def abrir_modulo_compras(self):
        if not self.tiene_permiso("compras"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import modulo_compras
            importlib.reload(modulo_compras)
            app = modulo_compras.ModuloComprasApp(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")

    def abrir_modulo_ordenes(self):
        if not self.tiene_permiso("ordenes"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import ordenes_compra
            importlib.reload(ordenes_compra)
            app = ordenes_compra.OrdenesCompraApp(self.contenedor_central, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")

    def abrir_modulo_ordenes_cliente(self):
        if not self.tiene_permiso("ordenes_cliente"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import ordenes_compra_cliente
            importlib.reload(ordenes_compra_cliente)
            app = ordenes_compra_cliente.OrdenesCompraClienteApp(self.contenedor_central, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir:\n{e}")

    def abrir_estadisticas_financiera(self):
        if not self.tiene_permiso("dashboard"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import estadisticas_financiera
            importlib.reload(estadisticas_financiera)
            estadisticas_financiera.EstadisticasFinancieraApp(self.contenedor_central)
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_calculo_impuestos(self):
        if not self.tiene_permiso("impuestos"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import calculo_impuestos
            importlib.reload(calculo_impuestos)
            calculo_impuestos.CalculoImpuestosApp(self.contenedor_central)
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_cobranza(self):
        if not self.tiene_permiso("cobranza"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import calculo_cobranza
            importlib.reload(calculo_cobranza)
            app = calculo_cobranza.CalculoCobranzaApp(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_proveedores(self):
        if not self.tiene_permiso("proveedores"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import proveedores
            importlib.reload(proveedores)
            app = proveedores.SistemaProveedores(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_libro_diario(self):
        if not self.tiene_permiso("libro_diario"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import libro_diario
            importlib.reload(libro_diario)
            libro_diario.LibroDiarioApp(self.contenedor_central)
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_libro_mayor(self):
        if not self.tiene_permiso("libro_mayor"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import libro_mayor
            importlib.reload(libro_mayor)
            libro_mayor.LibroMayorApp(self.contenedor_central)
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_clientes(self):
        if not self.tiene_permiso("clientes"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import clientes
            importlib.reload(clientes)
            app = clientes.SistemaClientes(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_cronograma(self):
        if not self.tiene_permiso("cronograma"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import cronograma_tareas
            importlib.reload(cronograma_tareas)
            app = cronograma_tareas.CronogramaApp(self.contenedor_central)
            app.usuario_activo = self.usuario_activo
        except Exception as e: messagebox.showerror("Error", str(e))

    def abrir_modulo_bitacora(self):
        if not self.tiene_permiso("bitacora"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import bitacora
            importlib.reload(bitacora)
            bitacora.BitacoraApp(self.contenedor_central)
            registrar_auditoria(self.usuario_activo, "Bitácora", "Accedió a revisar el historial de auditoría")
        except Exception as e: messagebox.showerror("Error", f"No se pudo cargar la Bitácora:\n{e}")

    def abrir_modulo_flota(self):
        if not self.tiene_permiso("flota"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import flota_automotriz
            importlib.reload(flota_automotriz)
            app = flota_automotriz.FlotaAutomotrizApp(self.contenedor_central, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir Flota:\n{e}")

    def abrir_modulo_choferes(self):
        if not self.tiene_permiso("choferes"): return messagebox.showerror("Denegado", "No tiene permisos.")
        self.limpiar_contenedor()
        try:
            import choferes
            importlib.reload(choferes)
            app = choferes.ChoferesApp(self.contenedor_central, self.usuario_activo)
        except Exception as e: messagebox.showerror("Error", f"Fallo al abrir Padrón de Choferes:\n{e}")

    # =======================================================
    # CONFIGURACIÓN GENERAL (CROSS-PLATFORM)
    # =======================================================
    def abrir_configuracion_general(self):
        if not self.tiene_permiso("configuracion"):
            return messagebox.showerror("Acceso Denegado", "No tiene permisos para modificar la configuración.")
            
        v_conf = ctk.CTkToplevel(self.root)
        v_conf.title("Configuración General del Sistema")
        v_conf.geometry("1000x750")
        v_conf.after(50, lambda: maximizar_ventana(v_conf))
        try:
            v_conf.attributes("-topmost", True)
        except Exception:
            pass
        v_conf.grab_set()
        
        archivo_config = str(CONFIG_FILE)
        config_actual = cargar_configuracion_general()
        
        lat_val, lon_val, rad_val = "-12.046374", "-77.042793", "100"
        conn_geo = conectar_db(silencioso=True)
        if conn_geo:
            try:
                with conn_geo.cursor() as c_geo:
                    c_geo.execute("SELECT latitud, longitud, radio FROM configuracion_geocerca LIMIT 1")
                    res_geo = c_geo.fetchone()
                    if res_geo:
                        lat_val, lon_val, rad_val = str(res_geo[0]), str(res_geo[1]), str(res_geo[2])
            except Exception:
                pass
            finally:
                liberar_conexion(conn_geo)
                
        f_header = ctk.CTkFrame(v_conf, fg_color="transparent")
        f_header.pack(fill="x", padx=25, pady=(20, 10))
        ctk.CTkLabel(f_header, text="⚙️ Configuración General del Sistema", font=("Arial", 22, "bold"), text_color="#1f538d").pack(side="left")
        ctk.CTkButton(f_header, text="❌ Cerrar Configuración", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=v_conf.destroy).pack(side="right")
        
        f_scroll = ctk.CTkScrollableFrame(v_conf, fg_color="transparent")
        f_scroll.pack(fill="both", expand=True, padx=20, pady=10)
        ctk.CTkLabel(f_scroll, text="Ajustes locales guardados específicamente para este equipo.", font=("Arial", 12, "italic"), text_color="gray").pack(anchor="w", padx=10, pady=(0, 15))
        
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
        ent_igv = ctk.CTkEntry(f_row2, width=60); ent_igv.pack(side="left", padx=5); ent_igv.insert(0, config_actual.get("igv_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Retención (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_retencion = ctk.CTkEntry(f_row2, width=60); ent_retencion.pack(side="left", padx=5); ent_retencion.insert(0, config_actual.get("retencion_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Renta Mensual (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_renta_m = ctk.CTkEntry(f_row2, width=60); ent_renta_m.pack(side="left", padx=5); ent_renta_m.insert(0, config_actual.get("renta_mensual_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Renta Anual (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_renta_a = ctk.CTkEntry(f_row2, width=60); ent_renta_a.pack(side="left", padx=5); ent_renta_a.insert(0, config_actual.get("renta_anual_porcentaje", "0"))
        ctk.CTkLabel(f_row2, text="Detracción (%):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_detraccion_cfg = ctk.CTkEntry(f_row2, width=60); ent_detraccion_cfg.pack(side="left", padx=5); ent_detraccion_cfg.insert(0, config_actual.get("detraccion_porcentaje", "12"))
        
        f_row3 = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_row3.pack(fill="x", padx=15, pady=10)
        ctk.CTkLabel(f_row3, text="Última Factura SUNAT:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        ent_ult_fac = ctk.CTkEntry(f_row3, width=90, placeholder_text="F001-0"); ent_ult_fac.pack(side="left", padx=5); ent_ult_fac.insert(0, config_actual.get("ultimo_factura", "F001-0"))
        ctk.CTkLabel(f_row3, text="Última Boleta:", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_ult_bol = ctk.CTkEntry(f_row3, width=90, placeholder_text="B001-0"); ent_ult_bol.pack(side="left", padx=5); ent_ult_bol.insert(0, config_actual.get("ultimo_boleta", "B001-0"))
        ctk.CTkLabel(f_row3, text="Último Recibo (RH):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_ult_rec = ctk.CTkEntry(f_row3, width=90, placeholder_text="E001-0"); ent_ult_rec.pack(side="left", padx=5); ent_ult_rec.insert(0, config_actual.get("ultimo_recibo", "E001-0"))

        def actualizar_tasas_regimen(choice):
            ent_igv.delete(0, tk.END); ent_retencion.delete(0, tk.END); ent_renta_m.delete(0, tk.END); ent_renta_a.delete(0, tk.END)
            if "NRUS" in choice:
                ent_igv.insert(0, "0"); ent_retencion.insert(0, "0"); ent_renta_m.insert(0, "0"); ent_renta_a.insert(0, "0")
            elif "RER" in choice:
                ent_igv.insert(0, "18"); ent_retencion.insert(0, "8"); ent_renta_m.insert(0, "1.5"); ent_renta_a.insert(0, "0")
            elif "MYPE" in choice:
                ent_igv.insert(0, "18"); ent_retencion.insert(0, "8"); ent_renta_m.insert(0, "1.0"); ent_renta_a.insert(0, "29.5")
            elif "General" in choice:
                ent_igv.insert(0, "18"); ent_retencion.insert(0, "8"); ent_renta_m.insert(0, "1.5"); ent_renta_a.insert(0, "29.5")
        cmb_regimen.configure(command=actualizar_tasas_regimen)

        def buscar_ruc_empresa(event=None):
            ruc = ent_ruc_empresa.get().strip()
            if len(ruc) != 11 or not ruc.isdigit():
                messagebox.showwarning("RUC Inválido", "Por favor, ingrese un RUC válido de 11 dígitos.", parent=v_conf)
                return
            try:
                # Contexto SSL seguro y compatible con macOS (ver _contexto_ssl_seguro)
                ctx = _contexto_ssl_seguro()

                url = f"https://api.apis.net.pe/v1/ruc?numero={ruc}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, context=ctx, timeout=8) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode())
                        ent_razon_social.delete(0, tk.END)
                        ent_razon_social.insert(0, data.get("nombre", ""))
                        messagebox.showinfo("Éxito", "Datos recuperados correctamente.", parent=v_conf)
                    else:
                        messagebox.showwarning("Sin Resultados", "No se encontró información para este RUC.", parent=v_conf)
            except urllib.error.HTTPError as e:
                if e.code in (404, 422):
                    messagebox.showwarning("RUC Inválido", "El RUC ingresado no existe en SUNAT o no es válido.", parent=v_conf)
                elif e.code in (401, 403):
                    messagebox.showwarning("API Restringida", "Se requiere un Token de API válido o el servicio está bloqueado.", parent=v_conf)
                else:
                    messagebox.showwarning("Error de Servidor", f"El servidor de SUNAT devolvió un error (Código {e.code}).", parent=v_conf)
            except Exception as e:
                messagebox.showwarning("Error", f"Problema al consultar RUC:\n{e}", parent=v_conf)
        ent_ruc_empresa.bind("<Return>", buscar_ruc_empresa)
        
        ctk.CTkLabel(f_empresa, text="🏦 Cuentas Bancarias de la Empresa", font=("Arial", 12, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(15, 5))
        f_bancos_container = ctk.CTkFrame(f_empresa, fg_color="transparent")
        f_bancos_container.pack(fill="x", padx=15, pady=0)
        bancos_peru = ["BCP", "BBVA", "Interbank", "Scotiabank", "Banco de la Nación", "BanBif", "Banco Pichincha", "Banco Falabella", "Banco Ripley", "Mibanco", "Caja Arequipa", "Caja Huancayo", "Caja Piura", "Caja Cusco", "Yape / Plin", "Otro"]
        filas_bancos = []

        def agregar_fila_banco(banco="", cuenta=""):
            fila = ctk.CTkFrame(f_bancos_container, fg_color="transparent")
            fila.pack(fill="x", pady=2)
            cmb_banco = ctk.CTkComboBox(fila, values=bancos_peru, width=180)
            cmb_banco.pack(side="left", padx=(0, 5))
            cmb_banco.set(banco if banco else "BCP")
            ent_cuenta = ctk.CTkEntry(fila, width=280, placeholder_text="N° de Cuenta / CCI")
            ent_cuenta.pack(side="left", padx=5)
            ent_cuenta.insert(0, cuenta)

            def remover_fila(f_eliminar=fila):
                f_eliminar.destroy()
                for item in filas_bancos:
                    if item[0] == f_eliminar:
                        filas_bancos.remove(item)
                        break
            ctk.CTkButton(fila, text="❌", width=30, fg_color="#e74c3c", hover_color="#c0392b", command=remover_fila).pack(side="left", padx=5)
            filas_bancos.append((fila, cmb_banco, ent_cuenta))
            
        ctk.CTkButton(f_empresa, text="➕ Agregar Banco", font=("Arial", 11, "bold"), width=120, fg_color="#27ae60", hover_color="#1e8449", command=agregar_fila_banco).pack(anchor="w", padx=15, pady=(5, 10))
        bancos_guardados = config_actual.get("cuentas_bancarias", [])
        if bancos_guardados:
            for b in bancos_guardados:
                agregar_fila_banco(b.get("banco", ""), b.get("cuenta", ""))
        else:
            agregar_fila_banco()
            
        f_sire = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#f0fdf4", border_width=1, border_color="#bbf7d0")
        f_sire.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_sire, text="🔐 Credenciales de Acceso a SUNAT SIRE (Descarga de Compras)", font=("Arial", 14, "bold"), text_color="#166534").pack(anchor="w", padx=15, pady=(10, 5))
        f_sire_r1 = ctk.CTkFrame(f_sire, fg_color="transparent"); f_sire_r1.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_sire_r1, text="Usuario SOL:", font=("Arial", 11, "bold"), width=140, anchor="w").pack(side="left")
        ent_user_sol = ctk.CTkEntry(f_sire_r1, placeholder_text="Ej: MODDATOS"); ent_user_sol.pack(side="left", fill="x", expand=True, padx=5); ent_user_sol.insert(0, config_actual.get("usuario_sol", ""))
        ctk.CTkLabel(f_sire_r1, text="Clave SOL:", font=("Arial", 11, "bold"), width=100, anchor="w").pack(side="left", padx=(15, 5))
        ent_clave_sol = ctk.CTkEntry(f_sire_r1, show="*", placeholder_text="••••••••"); ent_clave_sol.pack(side="left", fill="x", expand=True, padx=5); ent_clave_sol.insert(0, config_actual.get("clave_sol", ""))
        f_sire_r2 = ctk.CTkFrame(f_sire, fg_color="transparent"); f_sire_r2.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_sire_r2, text="Client ID (API SIRE):", font=("Arial", 11, "bold"), width=140, anchor="w").pack(side="left")
        ent_client_id = ctk.CTkEntry(f_sire_r2); ent_client_id.pack(side="left", fill="x", expand=True, padx=5); ent_client_id.insert(0, config_actual.get("client_id_sire", ""))
        ctk.CTkLabel(f_sire_r2, text="Client Secret:", font=("Arial", 11, "bold"), width=100, anchor="w").pack(side="left", padx=(15, 5))
        ent_client_secret = ctk.CTkEntry(f_sire_r2, show="*"); ent_client_secret.pack(side="left", fill="x", expand=True, padx=5); ent_client_secret.insert(0, config_actual.get("client_secret_sire", ""))
        
        f_fe = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#f0f4f8", border_width=1, border_color="#d0d7de")
        f_fe.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_fe, text="⚡ Facturación Electrónica Directa (Emisión de Ventas)", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        f_fe_row1 = ctk.CTkFrame(f_fe, fg_color="transparent"); f_fe_row1.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_fe_row1, text="Proveedor Servicio PSE:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        cmb_pse = ctk.CTkOptionMenu(f_fe_row1, values=["Nubefact", "Facturactiva", "Efact", "Bsale"], width=220); cmb_pse.pack(side="left", padx=5); cmb_pse.set(config_actual.get("proveedor_fe", "Nubefact"))
        f_fe_row2 = ctk.CTkFrame(f_fe, fg_color="transparent"); f_fe_row2.pack(fill="x", padx=15, pady=4)
        ctk.CTkLabel(f_fe_row2, text="Ruta API (Endpoint):", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        ent_url_api = ctk.CTkEntry(f_fe_row2); ent_url_api.pack(side="left", fill="x", expand=True, padx=5); ent_url_api.insert(0, config_actual.get("url_api_fe", ""))
        f_fe_row3 = ctk.CTkFrame(f_fe, fg_color="transparent"); f_fe_row3.pack(fill="x", padx=15, pady=(4, 8))
        ctk.CTkLabel(f_fe_row3, text="Token de Autorización:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        ent_token_api = ctk.CTkEntry(f_fe_row3, show="*"); ent_token_api.pack(side="left", fill="x", expand=True, padx=5); ent_token_api.insert(0, config_actual.get("token_api_fe", ""))
        
        f_2fa = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#fff3cd", border_width=1, border_color="#ffeeba")
        f_2fa.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_2fa, text="🛡️ Seguridad: Clave Dinámica (OTP) para SUNAT", font=("Arial", 14, "bold"), text_color="#856404").pack(anchor="w", padx=15, pady=(10, 5))
        f_2fa_m = ctk.CTkFrame(f_2fa, fg_color="transparent"); f_2fa_m.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_2fa_m, text="Método de Envío OTP:", font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
        cmb_2fa = ctk.CTkOptionMenu(f_2fa_m, values=["Inactivo", "Telegram (Gratis)", "Correo Electrónico (Gratis)", "SMS Twilio (De Pago)"], width=250); cmb_2fa.pack(side="left", padx=5); cmb_2fa.set(config_actual.get("2fa_metodo", "Inactivo"))
        
        f_tel = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_tel, text="Bot Token:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_tel_token = ctk.CTkEntry(f_tel, width=350); ent_tel_token.grid(row=0, column=1, sticky="w", pady=4); ent_tel_token.insert(0, config_actual.get("tel_bot_token", ""))
        ctk.CTkLabel(f_tel, text="Chat ID:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_tel_chat = ctk.CTkEntry(f_tel, width=350); ent_tel_chat.grid(row=1, column=1, sticky="w", pady=4); ent_tel_chat.insert(0, config_actual.get("tel_chat_id", ""))
        
        f_mail = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_mail, text="Servidor SMTP:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_mail_smtp = ctk.CTkEntry(f_mail, width=200); ent_mail_smtp.grid(row=0, column=1, sticky="w", pady=4); ent_mail_smtp.insert(0, config_actual.get("email_smtp", "smtp.gmail.com"))
        ctk.CTkLabel(f_mail, text="Puerto:", font=("Arial", 11, "bold"), width=60, anchor="w").grid(row=0, column=2, sticky="w", padx=(10, 0), pady=4)
        ent_mail_port = ctk.CTkEntry(f_mail, width=80); ent_mail_port.grid(row=0, column=3, sticky="w", pady=4); ent_mail_port.insert(0, config_actual.get("email_port", "587"))
        ctk.CTkLabel(f_mail, text="Tu Correo:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_mail_user = ctk.CTkEntry(f_mail, width=350); ent_mail_user.grid(row=1, column=1, columnspan=3, sticky="w", pady=4); ent_mail_user.insert(0, config_actual.get("email_user", ""))
        ctk.CTkLabel(f_mail, text="Clave de App:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        ent_mail_pass = ctk.CTkEntry(f_mail, width=350, show="*"); ent_mail_pass.grid(row=2, column=1, columnspan=3, sticky="w", pady=4); ent_mail_pass.insert(0, config_actual.get("email_pass", ""))
        ctk.CTkLabel(f_mail, text="Enviar a:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=3, column=0, sticky="w", pady=4)
        ent_mail_dest = ctk.CTkEntry(f_mail, width=350); ent_mail_dest.grid(row=3, column=1, columnspan=3, sticky="w", pady=4); ent_mail_dest.insert(0, config_actual.get("email_dest", ""))
        
        f_sms = ctk.CTkFrame(f_2fa, fg_color="transparent")
        ctk.CTkLabel(f_sms, text="Account SID:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=0, column=0, sticky="w", pady=4)
        ent_twi_sid = ctk.CTkEntry(f_sms, width=350); ent_twi_sid.grid(row=0, column=1, sticky="w", pady=4); ent_twi_sid.insert(0, config_actual.get("twi_sid", ""))
        ctk.CTkLabel(f_sms, text="Auth Token:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        ent_twi_token = ctk.CTkEntry(f_sms, width=350, show="*"); ent_twi_token.grid(row=1, column=1, sticky="w", pady=4); ent_twi_token.insert(0, config_actual.get("twi_token", ""))
        ctk.CTkLabel(f_sms, text="N° Twilio:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=2, column=0, sticky="w", pady=4)
        ent_twi_from = ctk.CTkEntry(f_sms, width=350); ent_twi_from.grid(row=2, column=1, sticky="w", pady=4); ent_twi_from.insert(0, config_actual.get("twi_from", ""))
        ctk.CTkLabel(f_sms, text="N° Destino:", font=("Arial", 11, "bold"), width=100, anchor="w").grid(row=3, column=0, sticky="w", pady=4)
        ent_twi_to = ctk.CTkEntry(f_sms, width=350); ent_twi_to.grid(row=3, column=1, sticky="w", pady=4); ent_twi_to.insert(0, config_actual.get("twi_to", ""))

        def actualizar_ui_2fa(choice):
            f_tel.pack_forget(); f_mail.pack_forget(); f_sms.pack_forget()
            if "Telegram" in choice: f_tel.pack(fill="x", padx=15, pady=5)
            elif "Correo" in choice: f_mail.pack(fill="x", padx=15, pady=5)
            elif "SMS" in choice: f_sms.pack(fill="x", padx=15, pady=5)
        cmb_2fa.configure(command=actualizar_ui_2fa)
        actualizar_ui_2fa(cmb_2fa.get())
        
        f_logo_docs = ctk.CTkFrame(f_scroll, corner_radius=10)
        f_logo_docs.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_logo_docs, text="🖼️ Logo del Encabezado (Cálculo de Cobranza y Órdenes de Compra)", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 10))
        f_ruta_logo = ctk.CTkFrame(f_logo_docs, fg_color="transparent")
        f_ruta_logo.pack(fill="x", padx=15, pady=(0, 10))
        ent_logo = ctk.CTkEntry(f_ruta_logo, placeholder_text="Ruta de la imagen (JPG/PNG)")
        ent_logo.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ent_logo.insert(0, config_actual.get("ruta_logo_cotizacion", ""))

        f_logo_preview = ctk.CTkFrame(f_logo_docs, fg_color="transparent")
        f_logo_preview.pack(fill="x", padx=15, pady=(0, 10))
        lbl_logo_preview = ctk.CTkLabel(f_logo_preview, text="Sin vista previa", width=150, height=40, fg_color="#eef2f7", corner_radius=8, anchor="center")
        lbl_logo_preview.pack(side="left", padx=(0, 10))

        def _actualizar_vista_previa(ruta):
            try:
                if PIL_DISPONIBLE and ruta and os.path.exists(ruta):
                    img = Image.open(ruta).convert("RGBA")
                    ancho, alto = img.width, img.height
                    # Muestra el logo a su tamaño real; solo se limita si es muy
                    # grande para no romper la ventana de configuración.
                    max_ancho = 900
                    if ancho > max_ancho:
                        alto = int(alto * (max_ancho / ancho))
                        ancho = max_ancho
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(ancho, alto))
                    lbl_logo_preview.configure(image=ctk_img, text="", width=ancho, height=alto)
                    lbl_logo_preview._img = ctk_img
                else:
                    lbl_logo_preview.configure(image=None, text="Sin vista previa", width=150, height=40)
            except Exception:
                lbl_logo_preview.configure(image=None, text="No se pudo cargar", width=150, height=40)

        _actualizar_vista_previa(config_actual.get("ruta_logo_cotizacion", ""))

        def abrir_dialogo_nativo(crear_dialogo, al_seleccionar):
            # 🚀 FIX macOS: libera el grab modal y DIFIERE la apertura del diálogo
            # nativo con after(). Abrir el panel en el mismo callback con un grab
            # activo (Toplevel con grab_set) puede cerrar abruptamente la app
            # (segfault de Tk/Aqua). El after() deja que Tk procese la liberación
            # del grab antes de que el panel nativo tome el foco. Tampoco se pasa
            # 'parent' al selector por el mismo motivo.
            try:
                v_conf.grab_release()
            except Exception:
                pass
            # Desactiva temporalmente el "siempre al frente" para que el
            # selector nativo de archivos/carpetas pueda abrirse POR DELANTE
            # de la ventana de configuración (si no, -topmost lo tapa).
            try:
                v_conf.attributes("-topmost", False)
            except Exception:
                pass

            def _abrir():
                try:
                    ruta = crear_dialogo()
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo abrir el selector de archivos:\n{e}", parent=v_conf)
                    ruta = None
                finally:
                    try:
                        # Restaura el "siempre al frente" y trae la ventana de
                        # configuración de vuelta al frente tras cerrar el selector.
                        v_conf.attributes("-topmost", True)
                    except Exception:
                        pass
                    try:
                        v_conf.lift()
                        v_conf.focus_force()
                    except Exception:
                        pass
                    try:
                        v_conf.grab_set()
                    except Exception:
                        pass
                if ruta and al_seleccionar:
                    al_seleccionar(ruta)

            v_conf.after(150, _abrir)

        def buscar_logo_docs():
            def _poner_ruta(ruta):
                if not ruta:
                    return
                ruta_final = guardar_logo_en_programa(ruta, ent_drive.get().strip())
                ent_logo.delete(0, tk.END)
                ent_logo.insert(0, ruta_final)
                _actualizar_vista_previa(ruta_final)
            abrir_dialogo_nativo(
                lambda: filedialog.askopenfilename(
                    title="Seleccionar Logo",
                    filetypes=[("Imágenes", "*.png *.jpg *.jpeg")]),
                _poner_ruta)

        def quitar_logo():
            ent_logo.delete(0, tk.END)
            _actualizar_vista_previa("")
        ctk.CTkButton(f_ruta_logo, text="📂 Buscar Imagen", width=140, command=buscar_logo_docs).pack(side="right")
        ctk.CTkButton(f_ruta_logo, text="🗑️ Quitar", width=90, fg_color="#e74c3c", hover_color="#c0392b", command=quitar_logo).pack(side="right", padx=(0, 5))

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
            def _poner_carpeta(carpeta):
                ent_drive.delete(0, tk.END)
                ent_drive.insert(0, carpeta)
            abrir_dialogo_nativo(
                lambda: filedialog.askdirectory(title="Seleccionar Carpeta Local"),
                _poner_carpeta)

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
        ent_rclone_nube.insert(0, config_actual.get("rclone_ruta_nube", "FlotaCube"))

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
                result = subprocess.run([cmd_rclone, "config", "create", remote, "drive", "scope", "drive"], capture_output=True, text=True, timeout=60, **kwargs)
                if result.returncode == 0:
                    messagebox.showinfo("Éxito", "¡Google Drive vinculado correctamente!\nYa puedes realizar la prueba de conexión.", parent=v_conf)
                else:
                    messagebox.showerror("Error", f"Falló la vinculación:\n{result.stderr}", parent=v_conf)
            except Exception as e:
                messagebox.showerror("Error Crítico", f"No se pudo ejecutar Rclone:\n{e}", parent=v_conf)

        def probar_rclone():
            remote = ent_rclone_remote.get().strip()
            nube = ent_rclone_nube.get().strip()
            local = os.path.expanduser(normalizar_ruta_local(ent_drive.get().strip()))
            
            if not remote:
                messagebox.showwarning("Aviso", "Ingresa el nombre del remote primero.", parent=v_conf)
                return
                
            if not local:
                default_local = os.path.join(os.path.expanduser("~"), "BlackCube_Archivos")
                ent_drive.delete(0, tk.END)
                ent_drive.insert(0, default_local)
                local = default_local
                os.makedirs(local, exist_ok=True)
                messagebox.showinfo("Atención: Carpeta Local Automática", 
                                    f"Para sincronizar, el sistema necesita una carpeta local válida.\n\n"
                                    f"Hemos creado y asignado por defecto:\n{local}\n\n"
                                    "Los archivos se guardarán aquí y luego se enviarán a la nube.", parent=v_conf)
                
            cmd_rclone = obtener_comando_rclone()

            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000

            try:
                subprocess.run([cmd_rclone, "version"], check=True, capture_output=True, timeout=10, **kwargs)
                result = subprocess.run([cmd_rclone, "about", remote], capture_output=True, text=True, timeout=20, **kwargs)
                
                if result.returncode == 0:
                    msg_adicional = ""
                    if nube:
                        ruta_remota = f"{remote}{nube}" if remote.endswith(":") else f"{remote}:{nube}"
                        res_mkdir = subprocess.run([cmd_rclone, "mkdir", ruta_remota], capture_output=True, text=True, timeout=20, **kwargs)
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

        f_geo = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#fdf2e9", border_width=1, border_color="#fadbd8")
        f_geo.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_geo, text="📍 Geocerca y Asistencia Automática GPS", font=("Arial", 14, "bold"), text_color="#d35400").pack(anchor="w", padx=15, pady=(10, 5))
        f_geo_row = ctk.CTkFrame(f_geo, fg_color="transparent"); f_geo_row.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_geo_row, text="Latitud:", font=("Arial", 11, "bold")).pack(side="left")
        ent_lat_geo = ctk.CTkEntry(f_geo_row, width=150); ent_lat_geo.pack(side="left", padx=5); ent_lat_geo.insert(0, lat_val)
        ctk.CTkLabel(f_geo_row, text="Longitud:", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_lon_geo = ctk.CTkEntry(f_geo_row, width=150); ent_lon_geo.pack(side="left", padx=5); ent_lon_geo.insert(0, lon_val)
        ctk.CTkLabel(f_geo_row, text="Radio (Metros):", font=("Arial", 11, "bold")).pack(side="left", padx=(15, 5))
        ent_rad_geo = ctk.CTkEntry(f_geo_row, width=80); ent_rad_geo.pack(side="left", padx=5); ent_rad_geo.insert(0, rad_val)
        
        f_flota = ctk.CTkFrame(f_scroll, corner_radius=10)
        f_flota.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_flota, text="🚙 Ajustes de Flota Automotriz", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        f_flota_row1 = ctk.CTkFrame(f_flota, fg_color="transparent"); f_flota_row1.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_flota_row1, text="Días de anticipación para alertas de vencimiento (SOAT, Licencias, etc):", font=("Arial", 11, "bold")).pack(side="left")
        ent_dias_alerta = ctk.CTkEntry(f_flota_row1, width=80); ent_dias_alerta.pack(side="left", padx=10); ent_dias_alerta.insert(0, config_actual.get("dias_alerta_vencimiento", "30"))
        
        f_flota_row2 = ctk.CTkFrame(f_flota, fg_color="transparent"); f_flota_row2.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_flota_row2, text="Alerta Cambio Aceite (Kilómetros):", font=("Arial", 11, "bold")).pack(side="left")
        ent_km_aceite = ctk.CTkEntry(f_flota_row2, width=80); ent_km_aceite.pack(side="left", padx=10); ent_km_aceite.insert(0, config_actual.get("alerta_aceite_km", "5000"))
        ctk.CTkLabel(f_flota_row2, text="o Meses:", font=("Arial", 11, "bold")).pack(side="left", padx=(10, 5))
        ent_meses_aceite = ctk.CTkEntry(f_flota_row2, width=80); ent_meses_aceite.pack(side="left", padx=10); ent_meses_aceite.insert(0, config_actual.get("alerta_aceite_meses", "6"))
        
        f_flota_row3 = ctk.CTkFrame(f_flota, fg_color="transparent"); f_flota_row3.pack(fill="x", padx=15, pady=5)
        ctk.CTkLabel(f_flota_row3, text="Alerta Mantenimiento General (Kilómetros):", font=("Arial", 11, "bold")).pack(side="left")
        ent_km_general = ctk.CTkEntry(f_flota_row3, width=80); ent_km_general.pack(side="left", padx=10); ent_km_general.insert(0, config_actual.get("alerta_mantenimiento_general_km", "10000"))
        
        f_menu = ctk.CTkFrame(f_scroll, corner_radius=10, fg_color="#eef2f3", border_width=1, border_color="#ccd1d9")
        f_menu.pack(fill="x", padx=10, pady=10, ipady=10)
        ctk.CTkLabel(f_menu, text="🎨 Apariencia y Orden del Menú Principal", font=("Arial", 14, "bold"), text_color="#1f538d").pack(anchor="w", padx=15, pady=(10, 5))
        f_menu_colors = ctk.CTkFrame(f_menu, fg_color="transparent"); f_menu_colors.pack(fill="x", padx=15, pady=5)

        def crear_selector_color_grid(padre, fila, col, texto, key_config, default):
            f_c = ctk.CTkFrame(padre, fg_color="transparent")
            f_c.grid(row=fila, column=col, padx=10, pady=5, sticky="w")
            ctk.CTkLabel(f_c, text=texto, font=("Arial", 11, "bold"), width=150, anchor="w").pack(side="left")
            ent_c = ctk.CTkEntry(f_c, width=80); ent_c.pack(side="left", padx=5)
            val = str(config_actual.get(key_config, default)).strip()
            ent_c.insert(0, val)
            color_prev = val if val.startswith("#") else default
            f_p = ctk.CTkFrame(f_c, width=25, height=25, fg_color=color_prev, corner_radius=5); f_p.pack(side="left", padx=5)

            def elegir():
                curr = ent_c.get().strip()
                if not curr.startswith("#"): curr = default
                try: color = colorchooser.askcolor(title=f"Elegir {texto}", color=curr)[1]
                except Exception: color = colorchooser.askcolor(title=f"Elegir {texto}")[1]
                if color:
                    ent_c.delete(0, tk.END); ent_c.insert(0, color); f_p.configure(fg_color=color)
            ctk.CTkButton(f_c, text="🎨", width=35, command=elegir).pack(side="left")
            return ent_c
            
        ent_m_fondo = crear_selector_color_grid(f_menu_colors, 0, 0, "Fondo Lateral:", "color_menu_fondo", "#1a252c")
        ent_m_btn = crear_selector_color_grid(f_menu_colors, 0, 1, "Color del Botón:", "color_menu_btn", "#1f538d")
        ent_m_hov = crear_selector_color_grid(f_menu_colors, 1, 0, "Al pasar el Mouse:", "color_menu_hover", "#163b65")
        ent_m_txt = crear_selector_color_grid(f_menu_colors, 1, 1, "Color del Texto:", "color_menu_texto", "#ffffff")
        
        f_menu_order = ctk.CTkFrame(f_menu, fg_color="transparent"); f_menu_order.pack(fill="both", expand=True, padx=15, pady=(15, 0))
        ctk.CTkLabel(f_menu_order, text="Orden de los Módulos por Grupo:", font=("Arial", 11, "bold")).pack(anchor="w")
        f_listas = ctk.CTkFrame(f_menu_order, fg_color="transparent"); f_listas.pack(fill="both", expand=True, pady=5)
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
            f_btns = ctk.CTkFrame(f_col, fg_color="transparent"); f_btns.pack(fill="x")

            def subir():
                sel = lb.curselection()
                if not sel or sel[0] == 0: return
                idx = sel[0]; val = lb.get(idx); lb.delete(idx); lb.insert(idx - 1, val); lb.selection_set(idx - 1)
            def bajar():
                sel = lb.curselection()
                if not sel or sel[0] == lb.size() - 1: return
                idx = sel[0]; val = lb.get(idx); lb.delete(idx); lb.insert(idx + 1, val); lb.selection_set(idx + 1)
            ctk.CTkButton(f_btns, text="⬆️", width=30, command=subir).pack(side="left", expand=True, padx=1)
            ctk.CTkButton(f_btns, text="⬇️", width=30, command=bajar).pack(side="left", expand=True, padx=1)
            return lb
            
        default_ops = ["clientes", "ordenes_cliente", "cronograma", "ordenes", "proveedores", "flota", "choferes"]
        default_fin = ["ventas", "compras", "libro_diario", "libro_mayor", "impuestos", "dashboard"]
        default_aju = ["configuracion", "usuarios", "bitacora"]
        ops = config_actual.get("orden_operativos", default_ops)
        fin = config_actual.get("orden_finanzas", default_fin)
        aju = config_actual.get("orden_ajustes", default_aju)
        todas = ops + fin + aju
        for k in self.modulos_sistema:
            if k not in todas:
                ops.append(k)
        lb_ops = crear_columna_orden(f_listas, "Módulos Operativos", ops)
        lb_fin = crear_columna_orden(f_listas, "Finanzas y Reportes", fin)
        lb_aju = crear_columna_orden(f_listas, "Ajustes de Sistema", aju)

        def guardar_configuracion():
            def ext_ord(lb):
                return [nombres_a_keys[lb.get(i)] for i in range(lb.size())]
                
            lista_bancos = []
            for f_widget, cmb_b, ent_c in filas_bancos:
                b_val = cmb_b.get().strip(); c_val = ent_c.get().strip()
                if b_val or c_val:
                    lista_bancos.append({"banco": b_val, "cuenta": c_val})
                    
            conn_geo_upd = conectar_db(silencioso=True)
            if conn_geo_upd:
                try:
                    with conn_geo_upd.cursor() as c_geo_upd:
                        c_geo_upd.execute("UPDATE configuracion_geocerca SET latitud=%s, longitud=%s, radio=%s", (
                            float(ent_lat_geo.get().strip() or 0), float(ent_lon_geo.get().strip() or 0), float(ent_rad_geo.get().strip() or 100)))
                        conn_geo_upd.commit()
                except Exception as e:
                    print("Error guardando geocerca:", e)
                finally:
                    liberar_conexion(conn_geo_upd)
                    
            nueva_config = config_actual.copy()
            nueva_config.update({
                "cuentas_bancarias": lista_bancos,
                "ruta_drive": ent_drive.get().strip(),
                "rclone_remote": ent_rclone_remote.get().strip(),
                "rclone_ruta_nube": ent_rclone_nube.get().strip(),
                "impresora": ent_impresora.get().strip(),
                "simbolo_moneda": cmb_moneda.get().strip() or "S/.",
                "formato_numero": cmb_num.get(),
                "formato_fecha": cmb_fecha.get(),
                "detraccion_porcentaje": ent_detraccion_cfg.get().strip(),
                "ruc_empresa": ent_ruc_empresa.get().strip(),
                "razon_social_empresa": ent_razon_social.get().strip(),
                "igv_porcentaje": ent_igv.get().strip(),
                "retencion_porcentaje": ent_retencion.get().strip(),
                "renta_mensual_porcentaje": ent_renta_m.get().strip(),
                "renta_anual_porcentaje": ent_renta_a.get().strip(),
                "regimen_empresa": cmb_regimen.get().strip(),
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
                "dias_alerta_vencimiento": ent_dias_alerta.get().strip() or "30",
                "alerta_aceite_km": ent_km_aceite.get().strip() or "5000",
                "alerta_aceite_meses": ent_meses_aceite.get().strip() or "6",
                "alerta_mantenimiento_general_km": ent_km_general.get().strip() or "10000",
                "color_menu_fondo": ent_m_fondo.get().strip(),
                "color_menu_btn": ent_m_btn.get().strip(),
                "color_menu_hover": ent_m_hov.get().strip(),
                "color_menu_texto": ent_m_txt.get().strip(),
                "orden_operativos": ext_ord(lb_ops),
                "orden_finanzas": ext_ord(lb_fin),
                "orden_ajustes": ext_ord(lb_aju),
                "ruta_logo_cotizacion": guardar_logo_en_programa(ent_logo.get().strip(), ent_drive.get().strip())
            })
            try:
                with open(archivo_config, "w", encoding="utf-8") as f:
                    json.dump(nueva_config, f, indent=4)
                messagebox.showinfo("Éxito", "Las configuraciones del sistema se guardaron correctamente.\n\nLos cambios en el diseño se aplicarán inmediatamente.", parent=v_conf)
                lanzar_sync_background()
                v_conf.destroy()
                self.construir_dashboard_spa()
            except Exception as e:
                messagebox.showerror("Error", f"No se pudo guardar la configuración:\n{e}", parent=v_conf)

        ctk.CTkButton(f_scroll, text="💾 Guardar Todos los Cambios", font=("Arial", 14, "bold"), height=45, fg_color="#1f538d", hover_color="#163b65", command=guardar_configuracion).pack(pady=25)

    # =======================================================
    # GESTIÓN DE USUARIOS (BCRYPT + PROTECCIÓN SUPER ADMIN)
    # =======================================================
    def abrir_gestion_usuarios(self):
        if not self.tiene_permiso("usuarios"):
            return messagebox.showerror("Acceso Denegado", "No tiene permisos para modificar usuarios.")
            
        v_usr = ctk.CTkToplevel(self.root)
        v_usr.title("Configuración de Usuarios y Permisos")
        ancho, alto = 1000, 580
        v_usr.geometry(f"{ancho}x{alto}")
        
        v_usr.update_idletasks()
        x = max(0, (v_usr.winfo_screenwidth() // 2) - (ancho // 2))
        y = max(0, (v_usr.winfo_screenheight() // 2) - (alto // 2))
        v_usr.geometry(f"{ancho}x{alto}+{x}+{y}")
        v_usr.lift()
        v_usr.grab_set()

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
        tbl_u.heading("id", text="ID"); tbl_u.heading("usuario", text="Usuario"); tbl_u.heading("rol", text="Rol Nominal")
        tbl_u.column("id", width=55, anchor="center"); tbl_u.column("usuario", width=220); tbl_u.column("rol", width=200)
        
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
            for item in tbl_u.get_children():
                tbl_u.delete(item)
            conn = conectar_db(silencioso=True)
            if not conn:
                return
            try:
                with conn.cursor() as c:
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
                with conn.cursor() as c:
                    c.execute("SELECT id FROM usuarios WHERE usuario = %s", (u,))
                    existe = c.fetchone()
                    if existe:
                        if c_str:
                            c.execute("UPDATE usuarios SET clave_hash=%s, clave=NULL, rol=%s, permisos=%s WHERE usuario=%s", (hash_password(c_str), r, permisos_json, u))
                        else:
                            c.execute("UPDATE usuarios SET rol=%s, permisos=%s WHERE usuario=%s", (r, permisos_json, u))
                        registrar_auditoria(self.usuario_activo, "Seguridad", f"Modificó el usuario '{u}' y sus permisos")
                    else:
                        if not c_str:
                            return messagebox.showwarning("Error", "Falta clave obligatoria para usuario nuevo.", parent=v_usr)
                        c.execute("INSERT INTO usuarios (usuario, clave, clave_hash, rol, permisos) VALUES (%s, NULL, %s, %s, %s)", (u, hash_password(c_str), r, permisos_json))
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
                with conn.cursor() as c:
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

    def confirmar_salida(self):
        if messagebox.askyesno("Confirmar Salida", "⚠️ ¿Estás seguro de que deseas cerrar completamente el sistema?", parent=self.root):
            try:
                registrar_auditoria(self.usuario_activo, "Seguridad", "Cerró el sistema completamente desde la X")
            except Exception:
                pass
            self.root.quit()
            self.root.destroy()


if __name__ == "__main__":
    root = ctk.CTk()
    app = ControlGeneralEventos(root)
    root.mainloop()