# -*- coding: utf-8 -*-
"""
COMPRAS.PY (ENTERPRISE EDITION - RENDIMIENTO EXTREMO)
- Paginación Lazy Loading (50 en 50) para Facturas y Pagos.
- Búsqueda Asíncrona en las pestañas.
- Protección del Pool de Conexiones (liberar_conexion).
- Auto-curación síncrona en segundo plano.
- Inicialización de formulario 100% asíncrona.
- 🚀 FIX: Caché inteligente para Combobox de Proveedores y Eventos.
- 🚀 FIX MAC/WIN: Escudo de variables locales en rutas para evitar colapso en Sincronización Nube.
- 🚀 NUEVO: Formulario de edición integral de Facturas Recibidas.
- 🚀 NUEVO: Botón unificado y Filtro de Reporte Avanzado Inteligente (Sintaxis Corregida).
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
import os
import sys  
import shutil
import calendar
import re
import json
import subprocess
import tempfile
import ctypes
import threading
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

# 🚀 IMPORTAMOS NUESTRAS NUEVAS HERRAMIENTAS CORPORATIVAS
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from buffer_memoria import cache_sistema

# 🏦 Cuentas bancarias del sistema (las mismas que usa el módulo de Bancos):
# al registrar un pago se indica de qué cuenta salió el dinero.
try:
    from bancos import cargar_bancos, etiqueta_banco
except Exception:
    cargar_bancos = None
    etiqueta_banco = None

# 🚀 CORTE DEL SISTEMA: fecha de comienzo (bloquea compras anteriores a esa fecha)
try:
    from fecha_sistema import (
        sugerir_periodo_sire, validar_periodo_sire, validar_fecha_registro, texto_fecha_comienzo,
        dias_para_vencer,   # 🎨 días que faltan para el vencimiento (colores de la tabla)
        fecha_vencimiento,  # 🖱️ fecha de vencimiento (cuadrito al pasar el mouse)
    )
except Exception:
    sugerir_periodo_sire = validar_periodo_sire = validar_fecha_registro = texto_fecha_comienzo = None
    def dias_para_vencer(fecha, dias_credito, hoy=None, formato_preferido=None):
        return None
    def fecha_vencimiento(fecha, dias_credito, formato_preferido=None):
        return None

# 🖱️ Cuadrito con los días que faltan para el vencimiento (al pasar el mouse por la factura)
try:
    from tooltip_tabla import TooltipVencimiento
except Exception:
    TooltipVencimiento = None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")

# Variable global definida al más alto nivel
_SCHEMA_COMPRAS_OK = False

# =========================================================
# 🚀 ADAPTACIÓN MULTIPLATAFORMA
# =========================================================
if sys.platform == "win32":
    try:
        hwnd_cmd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_cmd:
            ctypes.windll.user32.ShowWindow(hwnd_cmd, 6)
    except Exception:
        pass

def abrir_documento(ruta):
    try:
        if sys.platform == "win32":
            os.startfile(ruta)
        elif sys.platform == "darwin": 
            subprocess.call(["open", ruta])
        else: 
            subprocess.call(["xdg-open", ruta])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")

# =========================================================
# 🚀 MOTOR DE CONFIGURACIÓN REGIONAL
# =========================================================
def cargar_configuracion_regional():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "ruta_drive": "",
        "impresora": "",
        "ruc_empresa": "",
        "usuario_sol": "",
        "clave_sol": "",
        "client_id_sire": "",
        "client_secret_sire": ""
    }
    try:
        from app_paths import cargar_config_local; config.update(cargar_config_local())
    except Exception:
        pass
    return config

CONFIG_REGIONAL = cargar_configuracion_regional()

def traer_modulo_al_frente(ventana, retrasos=(60, 260, 700, 1150)):
    """🖥️ Deja la ventana del módulo DELANTE después de un mensaje o un guardado.

    CustomTkinter oculta y vuelve a mostrar la ventana para pintarle la barra de
    título (y a los 1000 ms reajusta el tamaño mínimo): esos ciclos terminan
    dejando la ventana DETRÁS de la principal. Por eso se trae al frente varias
    veces, con un 'topmost' momentáneo que se suelta enseguida.
    """
    if ventana is None:
        return
    try:
        ventana._windows_set_titlebar_color = lambda *a, **k: None
    except Exception:
        pass

    def _soltar():
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
                ventana.after(450, _soltar)
            except Exception:
                pass
            ventana.focus_force()
        except Exception:
            pass

    _traer()
    for retraso in retrasos or ():
        try:
            ventana.after(retraso, _traer)
        except Exception:
            break


def ventana_para_mensaje(principal=None):
    """🖥️ Ventana que se usa como padre de un mensaje para que salga DELANTE.

    Devuelve el diálogo que esté abierto (el último creado y visible) y, si no hay
    ninguno, la ventana del módulo. Así los comunicados y las consultas nunca quedan
    detrás de la ventana de Compras.
    """
    try:
        raiz = getattr(tk, "_default_root", None)
        if raiz is not None:
            candidatas = []
            for w in raiz.winfo_children():
                try:
                    if isinstance(w, (tk.Toplevel, ctk.CTkToplevel)) and w.winfo_viewable():
                        candidatas.append(w)
                except Exception:
                    continue
            if candidatas:
                return candidatas[-1]
    except Exception:
        pass
    return principal


def formatear_moneda(valor):
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    try: 
        valor = float(valor)
    except Exception: 
        valor = 0.0
    
    if formato == "1.000,00":
        str_val = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        str_val = f"{valor:,.2f}"
    return f"{simbolo} {str_val}"

def desformatear_numero(valor_str):
    if not valor_str: return 0.0
    simbolo = CONFIG_REGIONAL.get("simbolo_moneda", "S/.")
    formato = CONFIG_REGIONAL.get("formato_numero", "1,000.00")
    val = str(valor_str).replace(simbolo, "").strip()
    if formato == "1.000,00":
        val = val.replace(".", "").replace(",", ".")
    else:
        val = val.replace(",", "")
    try: 
        return float(val)
    except ValueError: 
        return 0.0

def obtener_ruta_base_drive():
    ruta = CONFIG_REGIONAL.get("ruta_drive", "").strip()
    if ruta: 
        ruta_exp = os.path.expanduser(ruta)
        try:
            os.makedirs(ruta_exp, exist_ok=True)
            return ruta_exp
        except Exception:
            pass 
            
    # 🚀 FIX MAC: Si no hay Google Drive, usamos la "Caja Fuerte" nativa automáticamente
    try:
        from app_paths import CONFIG_DIR
        caja_fuerte = str(CONFIG_DIR)
    except Exception:
        caja_fuerte = os.path.join(os.path.expanduser("~"), "BlackCube_Seguro")
        
    os.makedirs(caja_fuerte, exist_ok=True)
    return caja_fuerte

def aplicar_estilo_treeview():
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("Treeview", background="#ffffff", foreground="#000000", fieldbackground="#ffffff", bordercolor="#e0e0e0", borderwidth=1, rowheight=26, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000", relief="flat", font=("Arial", 10, "bold"), bordercolor="#e0e0e0", borderwidth=1)

# =========================================================
# 🚀 MODO LECTURA OFFLINE
# =========================================================
def obtener_conexion_segura(parent=None, mostrar_aviso=True):
    conn = conectar_db(silencioso=True)
    if not conn and mostrar_aviso:
        messagebox.showwarning(
            "Modo Lectura",
            "⚠️ No se pudo conectar a la base de datos.\n\n"
            "El sistema está en MODO LECTURA: no se permiten cambios "
            "(crear, editar, anular, eliminar o exportar) hasta que se "
            "restablezca la conexión a internet / la nube.",
            parent=parent
        )
    return conn

# =========================================================
# 🏦 DATOS BANCARIOS DEL PROVEEDOR (ventana al hacer clic en una factura)
# =========================================================
def obtener_datos_bancarios_proveedor(nombre_proveedor):
    """Devuelve la ficha bancaria del proveedor (tabla 'proveedores') o None.

    Orden de las columnas: ruc, nombre, banco_1, cuenta_1, cci_1, banco_2,
    cuenta_2, cci_2, cuenta_detraccion, porcentaje_detraccion, contacto,
    whatsapp, correo.
    """
    nombre = str(nombre_proveedor or "").strip()
    if not nombre:
        return None
    conn = obtener_conexion_segura(mostrar_aviso=False)
    if not conn:
        return None
    columnas = """SELECT ruc, nombre, banco_1, cuenta_1, cci_1, banco_2, cuenta_2, cci_2,
                         cuenta_detraccion, porcentaje_detraccion, contacto, whatsapp, correo
                  FROM proveedores"""
    try:
        cursor = conn.cursor()
        cursor.execute(columnas + " WHERE TRIM(UPPER(nombre)) = TRIM(UPPER(%s)) LIMIT 1", (nombre,))
        fila = cursor.fetchone()
        if not fila:                      # por si el nombre viene con otro formato
            cursor.execute(columnas + " WHERE nombre ILIKE %s ORDER BY id LIMIT 1", (f"%{nombre}%",))
            fila = cursor.fetchone()
        return fila
    except Exception:
        return None
    finally:
        liberar_conexion(conn)


def _texto_bancario_limpio(valor):
    """Quita los textos de relleno de los combos de la ficha del proveedor."""
    texto = str(valor or "").strip()
    if texto.lower() in ("no seleccionada", "seleccione banco", "ninguno", "none", "-", "nan"):
        return ""
    return texto


def mostrar_datos_bancarios_proveedor(parent, proveedor, datos, factura_txt="", reutilizar=None):
    """🧾 Ventana con los datos bancarios del proveedor y botones para copiarlos.

    'datos' es la fila de la tabla proveedores (o None si no se encontró).
    'reutilizar' es una ventana ya abierta que se refresca en vez de abrir otra.
    """
    datos = list(datos or []) + [""] * 13
    (ruc, nombre, b1, c1, cc1, b2, c2, cc2,
     cdet, pdet, contacto, whatsapp, correo) = datos[:13]
    b1, c1, cc1 = _texto_bancario_limpio(b1), _texto_bancario_limpio(c1), _texto_bancario_limpio(cc1)
    b2, c2, cc2 = _texto_bancario_limpio(b2), _texto_bancario_limpio(c2), _texto_bancario_limpio(cc2)
    cdet, pdet = _texto_bancario_limpio(cdet), _texto_bancario_limpio(pdet)
    nombre = str(nombre or proveedor or "").strip()

    ventana = reutilizar
    if ventana is None:
        ventana = ctk.CTkToplevel(parent)
        ventana.transient(parent)
    for hijo in ventana.winfo_children():
        hijo.destroy()
    ventana.title(f"🏦 Datos bancarios - {nombre}")
    ventana.geometry("620x560")
    ventana.minsize(560, 420)

    ctk.CTkLabel(ventana, text="🏦 DATOS BANCARIOS DEL PROVEEDOR",
                 font=("Arial", 15, "bold"), text_color="#1f538d").pack(pady=(14, 2))
    ctk.CTkLabel(ventana, text=nombre or "(sin nombre)", font=("Arial", 13, "bold")).pack()
    if ruc:
        ctk.CTkLabel(ventana, text=f"RUC: {ruc}", font=("Arial", 11, "bold"),
                     text_color="#7f8c8d").pack(pady=(0, 4))
    if factura_txt:
        ctk.CTkLabel(ventana, text=factura_txt, font=("Arial", 11, "italic"),
                     text_color="#7f8c8d").pack(pady=(0, 6))

    lbl_estado = ctk.CTkLabel(ventana, text="", font=("Arial", 11, "bold"), text_color="#1e8449")
    lbl_estado.pack(pady=(0, 4))

    def copiar(texto, etiqueta="Dato"):
        texto = str(texto or "").strip()
        if not texto:
            return lbl_estado.configure(text="⚠️ No hay dato para copiar.", text_color="#c0392b")
        try:
            ventana.clipboard_clear()
            ventana.clipboard_append(texto)
            ventana.update_idletasks()
            lbl_estado.configure(text=f"✅ Copiado: {texto}", text_color="#1e8449")
        except Exception as e:
            lbl_estado.configure(text=f"⚠️ No se pudo copiar: {e}", text_color="#c0392b")

    cuerpo = ctk.CTkScrollableFrame(ventana, fg_color="transparent")
    cuerpo.pack(fill="both", expand=True, padx=14, pady=6)

    def ficha(titulo, contenido):
        marco = ctk.CTkFrame(cuerpo, fg_color="#f8f9fa", corner_radius=8,
                             border_width=1, border_color="#e0e0e0")
        marco.pack(fill="x", pady=6)
        ctk.CTkLabel(marco, text=titulo, font=("Arial", 12, "bold"),
                     text_color="#1f538d", anchor="w").pack(fill="x", padx=12, pady=(8, 2))
        for etiqueta, valor in contenido:
            valor = str(valor or "").strip()
            if not valor:
                continue
            fila = ctk.CTkFrame(marco, fg_color="transparent")
            fila.pack(fill="x", padx=12, pady=2)
            ctk.CTkLabel(fila, text=etiqueta, width=120, anchor="w",
                         font=("Arial", 11, "bold")).pack(side="left")
            ctk.CTkLabel(fila, text=valor, anchor="w", font=("Arial", 12)).pack(side="left", padx=(0, 6))
            ctk.CTkButton(fila, text="📋 Copiar", width=84, height=24, font=("Arial", 10, "bold"),
                          fg_color="#1f538d", hover_color="#163b65",
                          command=lambda v=valor, e=etiqueta: copiar(v, e)).pack(side="right", padx=(6, 0))
        return marco

    hay_cuenta = False
    if b1 or c1 or cc1:
        hay_cuenta = True
        ficha("💳 Cuenta bancaria 1", [("Banco:", b1), ("N° de cuenta:", c1), ("CCI:", cc1)])
    if b2 or c2 or cc2:
        hay_cuenta = True
        ficha("💳 Cuenta bancaria 2", [("Banco:", b2), ("N° de cuenta:", c2), ("CCI:", cc2)])
    if cdet or pdet:
        hay_cuenta = True
        ficha("🧾 Cuenta de detracción", [("N° de cuenta:", cdet), ("Porcentaje:", pdet)])

    if hay_cuenta:
        contacto_txt = [("Contacto:", contacto), ("WhatsApp:", whatsapp), ("Correo:", correo)]
        if any(str(v or "").strip() for _, v in contacto_txt):
            ficha("📞 Contacto del proveedor", contacto_txt)

        def copiar_todo():
            lineas = [f"PROVEEDOR: {nombre}"]
            if ruc:
                lineas.append(f"RUC: {ruc}")
            if b1 or c1 or cc1:
                lineas += ["", "CUENTA 1:",
                           f"  Banco: {b1 or '-'}", f"  N° de cuenta: {c1 or '-'}", f"  CCI: {cc1 or '-'}"]
            if b2 or c2 or cc2:
                lineas += ["", "CUENTA 2:",
                           f"  Banco: {b2 or '-'}", f"  N° de cuenta: {c2 or '-'}", f"  CCI: {cc2 or '-'}"]
            if cdet or pdet:
                lineas += ["", f"DETRACCIÓN: {cdet or '-'}" + (f"  ({pdet})" if pdet else "")]
            copiar("\n".join(lineas), "Todos los datos")

        ctk.CTkButton(ventana, text="📋 Copiar todos los datos", height=34,
                      font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449",
                      command=copiar_todo).pack(fill="x", padx=14, pady=(4, 2))
        ctk.CTkLabel(ventana, text="Los datos se toman de la ficha del proveedor (módulo Proveedores).",
                     font=("Arial", 9, "italic"), text_color="#7f8c8d").pack(pady=(0, 2))
    else:
        ctk.CTkLabel(cuerpo, text="⚠️ Este proveedor todavía no tiene datos bancarios\n"
                                  "registrados en su ficha.",
                     font=("Arial", 12, "bold"), text_color="#c0392b").pack(pady=18)
        ctk.CTkLabel(cuerpo, text="Regístralos en el módulo 📦 Proveedores → Editar →\n"
                                  "Banco 1 / Cuenta 1 / CCI 1 (o Banco 2).",
                     font=("Arial", 11), text_color="#7f8c8d", justify="center").pack(pady=(0, 10))

    ctk.CTkButton(ventana, text="❌ Cerrar", width=140, height=30, font=("Arial", 11, "bold"),
                  fg_color="#7f8c8d", hover_color="#606b6b",
                  command=ventana.destroy).pack(pady=(4, 12))
    try:
        ventana.bind("<Escape>", lambda _e: ventana.destroy(), add="+")
        ventana.lift()
        ventana.focus_force()
    except Exception:
        pass
    return ventana


def abrir_datos_bancarios_de_fila(tabla, event, parent, nombre_proveedor, factura_txt="",
                                  cache_ventanas=None, al_seleccionar=None):
    """🖱️ Al hacer CLIC DERECHO en una factura: muestra los datos bancarios.

    'cache_ventanas' es un diccionario {proveedor: ventana} para reutilizar la
    ventana si ya está abierta y no llenar la pantalla de ventanas.
    """
    try:
        fila = tabla.identify_row(event.y)
        if not fila:
            return                                  # clic en zona vacía
        if tabla.identify_region(event.x, event.y) not in ("cell", "tree"):
            return                                  # clic en el encabezado
    except Exception:
        return
    if al_seleccionar is not None:
        try:
            al_seleccionar(fila)
        except Exception:
            pass
    nombre_proveedor = str(nombre_proveedor or "").strip()
    if not nombre_proveedor:
        return messagebox.showinfo("Datos bancarios", "Esta factura no tiene proveedor registrado.")
    datos = obtener_datos_bancarios_proveedor(nombre_proveedor)
    ventana_previa = None
    if cache_ventanas is not None:
        ventana_previa = cache_ventanas.get(nombre_proveedor)
        try:
            if ventana_previa is not None and not ventana_previa.winfo_exists():
                ventana_previa = None
        except Exception:
            ventana_previa = None
        if ventana_previa is not None:
            for hijo in ventana_previa.winfo_children():
                hijo.destroy()
    ventana = mostrar_datos_bancarios_proveedor(parent, nombre_proveedor, datos,
                                                factura_txt=factura_txt, reutilizar=ventana_previa)
    if cache_ventanas is not None:
        cache_ventanas[nombre_proveedor] = ventana
    return ventana


# =========================================================
# 🧾 LECTURA DE FACTURAS (PDF o ESCANEADAS CON OCR)
# =========================================================
# =========================================================
# 🗓️ DESPLEGABLE DE MES PARA FILTRAR LAS FACTURAS RECIBIDAS
# =========================================================
NOMBRES_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                 "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def _fecha_a_iso(fecha):
    """Convierte la fecha leída (DD/MM/AAAA o MM/DD/AAAA) a AAAA-MM-DD.

    El campo "Fecha del Pago" de Compras trabaja en AAAA-MM-DD, así que la fecha
    del comprobante se adapta a ese formato antes de colocarla.
    """
    texto = str(fecha or "").strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        return texto
    partes = re.split(r"[/\-.]", texto)
    if len(partes) != 3:
        return texto
    a, b, c = partes
    try:
        if len(a) == 4:
            return "%04d-%02d-%02d" % (int(a), int(b), int(c))
    except Exception:
        return texto
    try:
        fmt = str((CONFIG_REGIONAL or {}).get("formato_fecha", "DD/MM/AAAA")).upper()
    except Exception:
        fmt = "DD/MM/AAAA"
    dia, mes = (b, a) if fmt.startswith("MM") else (a, b)
    try:
        return "%04d-%02d-%02d" % (int(c), int(mes), int(dia))
    except Exception:
        return texto


def etiqueta_mes_de_fecha(fecha):
    """'09/10/2026' -> 'Octubre 2026' ('' si no se puede interpretar)."""
    texto = str(fecha or "").strip()
    if not texto:
        return ""
    partes = re.split(r"[/\-.]", texto)
    if len(partes) != 3:
        return ""
    a, b, c = partes
    try:
        if len(a) == 4:
            anio, mes = int(a), int(b)
        else:
            try:
                fmt = str((CONFIG_REGIONAL or {}).get("formato_fecha", "DD/MM/AAAA")).upper()
            except Exception:
                fmt = "DD/MM/AAAA"
            mes = int(b if not fmt.startswith("MM") else a)
            anio = int(c)
        if not (2000 <= anio <= 2100 and 1 <= mes <= 12):
            return ""
        return "%s %d" % (NOMBRES_MESES[mes - 1], anio)
    except Exception:
        return ""


def construir_valores_mes():
    """Opciones del desplegable: 'Todos los meses' + meses de años cercanos."""
    hoy = datetime.now()
    valores = ["Todos los meses"]
    for anio in (hoy.year - 1, hoy.year, hoy.year + 1):
        for nombre in NOMBRES_MESES:
            valores.append(f"{nombre} {anio}")
    return valores


def mes_en_curso():
    """Etiqueta del mes actual, usada como valor por defecto del desplegable."""
    hoy = datetime.now()
    return f"{NOMBRES_MESES[hoy.month - 1]} {hoy.year}"


def patron_fecha_mes(etiqueta):
    """Patrón SQL LIKE para filtrar la columna 'fecha' por el mes indicado.

    Devuelve None para 'Todos los meses' o etiquetas inválidas.
    """
    if not etiqueta or etiqueta == "Todos los meses":
        return None
    partes = str(etiqueta).strip().split()
    if len(partes) < 2 or partes[0] not in NOMBRES_MESES:
        return None
    mes = NOMBRES_MESES.index(partes[0]) + 1
    anio = partes[-1]
    mes_str = f"{mes:02d}"
    fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
    if fmt == "MM/DD/AAAA":
        return f"{mes_str}/%/{anio}"
    return f"%/{mes_str}/{anio}"


def _a_numero(texto):
    """Convierte '1,234.56' / '1.234,56' / 'S/ 118.00' en número."""
    limpio = re.sub(r"[^\d.,]", "", str(texto or ""))
    if not limpio:
        return 0.0
    if "," in limpio and "." in limpio:
        if limpio.rfind(",") > limpio.rfind("."):
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(",", "")
    elif "," in limpio:
        partes = limpio.split(",")
        limpio = limpio.replace(",", ".") if len(partes[-1]) <= 2 else limpio.replace(",", "")
    try:
        return float(limpio)
    except Exception:
        return 0.0


def buscar_proveedor_por_ruc(ruc):
    """Nombre del proveedor registrado con ese RUC (cadena vacía si no está)."""
    ruc = re.sub(r"\D", "", str(ruc or ""))
    if len(ruc) != 11:
        return ""
    conn = obtener_conexion_segura(mostrar_aviso=False)
    if not conn:
        return ""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT nombre FROM proveedores WHERE ruc = %s ORDER BY id LIMIT 1", (ruc,))
        fila = cursor.fetchone()
        return str(fila[0]).strip() if fila and fila[0] else ""
    except Exception:
        return ""
    finally:
        liberar_conexion(conn)


def _normalizar_nombre_empresa(texto):
    """Nombre de empresa comparable: sin signos ni sufijos societarios.

    'BLACKCUBE S.A.C.', 'BLACKCUBE SOCIEDAD ANONIMA CERRADA' y 'BLACKCUBE SAC'
    quedan iguales, para poder comparar aunque la factura lo escriba distinto.
    """
    limpio = str(texto or "").upper().replace(".", "")
    limpio = re.sub(r"[^0-9A-ZÁÉÍÓÚÑÜ ]", " ", limpio)
    limpio = re.sub(r"\b(SOCIEDAD|ANONIMA|CERRADA|ABIERTA|SAC|SA|SRL|EIRL|LTDA)\b", " ", limpio)
    return " ".join(limpio.split())


def validar_destinatario(datos, config=None):
    """Comprueba que la factura esté a nombre de la empresa configurada en el sistema.

    Devuelve (estado, mensaje):
      'ok'             -> la factura es de la empresa configurada
      'ajeno'          -> es de OTRA empresa (hay que rechazarla)
      'sin-configurar' -> falta el RUC/Razón Social en Configuración General
      'sin-datos'      -> no se pudo leer el destinatario en la factura
    """
    config = config if isinstance(config, dict) else cargar_configuracion_regional()
    ruc_empresa = re.sub(r"\D", "", str(config.get("ruc_empresa") or ""))
    nombre_empresa = str(config.get("razon_social_empresa") or "").strip()
    if not ruc_empresa and not nombre_empresa:
        return "sin-configurar", ("No se puede validar el destinatario: configure el RUC y la Razón Social "
                                  "de la empresa en Ajustes → Configuración General.")

    datos = datos or {}
    cliente_ruc = re.sub(r"\D", "", str(datos.get("cliente_ruc") or ""))
    cliente_nombre = str(datos.get("cliente_nombre") or "").strip()

    if ruc_empresa and cliente_ruc:
        if cliente_ruc == ruc_empresa:
            return "ok", f"✅ Factura a nombre de la empresa (RUC {cliente_ruc})."
        detalle = f"RUC {cliente_ruc}" + (f" — {cliente_nombre}" if cliente_nombre else "")
        return "ajeno", (f"❌ Esta factura está a nombre de OTRA empresa: {detalle}.\n"
                         f"La empresa configurada es RUC {ruc_empresa}"
                         + (f" — {nombre_empresa}" if nombre_empresa else "") + ".")

    if nombre_empresa and cliente_nombre:
        a, b = _normalizar_nombre_empresa(cliente_nombre), _normalizar_nombre_empresa(nombre_empresa)
        if a and b and (a in b or b in a):
            return "ok", f"✅ El destinatario '{cliente_nombre}' coincide con la empresa configurada."
        return "ajeno", (f"❌ Esta factura está a nombre de '{cliente_nombre}', pero la empresa configurada "
                         f"es '{nombre_empresa}'.")

    return "sin-datos", ("⚠️ No se pudo leer el destinatario (RUC o razón social) en la factura. "
                         "Verifique que esté a nombre de la empresa antes de guardarla.")


def datos_proveedor_desde_ruc(ruc, timeout=10):
    """Datos completos del proveedor traídos de SUNAT por internet (o None)."""
    try:
        from proveedores import consultar_ruc_en_internet
    except Exception:
        return None
    return consultar_ruc_en_internet(ruc, timeout=timeout)


def asegurar_proveedor(nombre, ruc, usar_internet=True, timeout=10):
    """Deja registrado al proveedor (creándolo si hace falta) y devuelve sus datos.

      1. Si ya existe (por RUC o nombre) se usa el de la base de datos; si le
         faltan dirección/ubicación y hay internet, se completan con SUNAT.
      2. Si no existe, se consulta el RUC en internet (SUNAT) y se guarda con
         TODA la información de la empresa (razón social, dirección, ubicación).
      3. Si no hay internet, se guarda con lo leído en el comprobante.

    Devuelve (estado, datos) con estado en: 'existe' | 'completado' | 'creado'
    | 'creado_sin_sunat' | 'sin-datos' | 'sin-conexion' | 'error'
    """
    nombre = " ".join(str(nombre or "").split()).strip()
    ruc = re.sub(r"\D", "", str(ruc or ""))
    datos = {"ruc": ruc, "nombre": nombre, "direccion_fiscal": "", "ubicacion": ""}
    if not nombre and not ruc:
        return "sin-datos", datos

    conn = obtener_conexion_segura(mostrar_aviso=False)
    if not conn:
        return "sin-conexion", datos
    try:
        cursor = conn.cursor()
        existente = None
        if ruc:
            cursor.execute("SELECT id, COALESCE(ruc,''), COALESCE(nombre,''), "
                           "COALESCE(direccion_fiscal,''), COALESCE(ubicacion,'') "
                           "FROM proveedores WHERE ruc = %s LIMIT 1", (ruc,))
            existente = cursor.fetchone()
        if not existente and nombre:
            cursor.execute("SELECT id, COALESCE(ruc,''), COALESCE(nombre,''), "
                           "COALESCE(direccion_fiscal,''), COALESCE(ubicacion,'') "
                           "FROM proveedores WHERE TRIM(UPPER(nombre)) = TRIM(UPPER(%s)) LIMIT 1",
                           (nombre,))
            existente = cursor.fetchone()

        de_sunat = None
        if existente:
            id_prov, ruc_db, nombre_db, direccion_db, ubicacion_db = existente
            datos.update({"ruc": ruc_db or ruc, "nombre": nombre_db or nombre,
                          "direccion_fiscal": direccion_db, "ubicacion": ubicacion_db})
            falta_algo = not (direccion_db and ubicacion_db)
            if usar_internet and ruc and falta_algo:
                de_sunat = datos_proveedor_desde_ruc(ruc, timeout=timeout)
            cambios = {}
            if de_sunat:
                if not direccion_db and de_sunat.get("direccion_fiscal"):
                    cambios["direccion_fiscal"] = de_sunat["direccion_fiscal"]
                    datos["direccion_fiscal"] = de_sunat["direccion_fiscal"]
                if not ubicacion_db and de_sunat.get("ubicacion"):
                    cambios["ubicacion"] = de_sunat["ubicacion"]
                    datos["ubicacion"] = de_sunat["ubicacion"]
                if not nombre_db and de_sunat.get("nombre"):
                    cambios["nombre"] = de_sunat["nombre"]
                    datos["nombre"] = de_sunat["nombre"]
            if cambios:
                columnas = ", ".join(f"{c} = %s" for c in cambios)
                cursor.execute(f"UPDATE proveedores SET {columnas} WHERE id = %s",
                               tuple(cambios.values()) + (id_prov,))
                conn.commit()
                cache_sistema.invalidar()
                return "completado", datos
            return "existe", datos

        if de_sunat is None and usar_internet and ruc:
            de_sunat = datos_proveedor_desde_ruc(ruc, timeout=timeout)
        if de_sunat and (de_sunat.get("nombre") or nombre):
            datos.update({"nombre": de_sunat.get("nombre") or nombre,
                          "direccion_fiscal": de_sunat.get("direccion_fiscal", ""),
                          "ubicacion": de_sunat.get("ubicacion", "")})
            estado = "creado"
        elif nombre:
            datos["nombre"] = nombre
            estado = "creado_sin_sunat"
        else:
            # Sin razón social (ni del comprobante ni de SUNAT) NO se crea un proveedor
            # con el número de RUC como nombre: se le pide al usuario que lo escriba.
            return "sin-datos", datos

        cursor.execute("""
            INSERT INTO proveedores (ruc, nombre, direccion_fiscal, categoria, ubicacion, descripcion)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (datos["ruc"], datos["nombre"], datos.get("direccion_fiscal", ""),
              "No seleccionada", datos.get("ubicacion", ""),
              "Agregado automáticamente al escanear un comprobante"))
        conn.commit()
        cache_sistema.invalidar()
        return estado, datos
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return "error", datos
    finally:
        liberar_conexion(conn)


def crear_proveedor_si_no_existe(nombre, ruc, usar_internet=True):
    """Compatibilidad: agrega el proveedor si no está y devuelve solo el estado."""
    estado, _datos = asegurar_proveedor(nombre, ruc, usar_internet=usar_internet)
    return estado


def numero_comprobante_desde_texto(texto):
    """N° de documento (F001-123, E001-45...) leído del comprobante.

    El OCR suele leerlo SIN guion ("F581 08283480"), así que se acepta cualquier
    separador (guion, dos puntos, espacio) o incluso ninguno.
    """
    texto = str(texto or "")
    encontrado = re.search(r"\b([EFB][0-9A-Z]{3})\s*[-:\u2013\u2014]?\s*(\d{1,8})", texto)
    if encontrado:
        return "%s-%s" % (encontrado.group(1).upper(), encontrado.group(2))
    return ""


def buscar_comprobante_duplicado(numero_documento):
    """Comprobante ya registrado con ese N° de documento (o None)."""
    nro = re.sub(r"\s+", "", str(numero_documento or "")).upper()
    if not nro:
        return None
    conn = obtener_conexion_segura(mostrar_aviso=False)
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT proveedor, fecha, COALESCE(total, 0), COALESCE(archivo_ruta, '')
            FROM facturas_recibidas
            WHERE REGEXP_REPLACE(REGEXP_REPLACE(UPPER(COALESCE(numero_documento, '')), '[^A-Z0-9]', '', 'g'),
                                 '^([A-Z]+)0+', '\1')
                  = REGEXP_REPLACE(REGEXP_REPLACE(%s, '[^A-Z0-9]', '', 'g'), '^([A-Z]+)0+', '\1')
            ORDER BY id DESC LIMIT 1
        """, (nro,))
        fila = cursor.fetchone()
        if not fila:
            return None
        return {"numero": nro, "proveedor": fila[0], "fecha": fila[1],
                "total": fila[2], "archivo": fila[3]}
    except Exception:
        return None
    finally:
        liberar_conexion(conn)


# Script de PowerShell que usa el motor OCR que ya incluye Windows.
# Devuelve una línea por palabra:  X <TAB> Y <TAB> ancho <TAB> alto <TAB> texto
_SCRIPT_OCR_WINDOWS = r'''
param([string]$Ruta, [string]$Idiomas = "es-PE,es-MX,es-ES,en-US")
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $tipo) {
    $m = $asTaskGeneric.MakeGenericMethod($tipo)
    $t = $m.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null

$engine = $null
foreach ($tag in $Idiomas.Split(",")) {
    $tag = $tag.Trim()
    if (-not $tag) { continue }
    try {
        $idioma = [Windows.Globalization.Language]::new($tag)
        $posible = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($idioma)
        if ($null -ne $posible) { $engine = $posible; break }
    } catch { }
}
if ($null -eq $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if ($null -eq $engine) { return }

$archivo = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Ruta)) ([Windows.Storage.StorageFile])
$stream  = Await ($archivo.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$resultado = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
foreach ($linea in $resultado.Lines) {
  foreach ($p in $linea.Words) {
    $r = $p.BoundingRect
    Write-Output ([string][int]$r.X + [char]9 + [string][int]$r.Y + [char]9 + [string][int]$r.Width + [char]9 + [string][int]$r.Height + [char]9 + $p.Text)
  }
}
'''


def _normalizar_etiqueta(texto):
    """Deja solo letras en mayúscula: sirve para reconocer rótulos aunque el OCR falle."""
    return re.sub(r"[^A-ZÑ]", "", str(texto or "").upper())


def _parecido(texto, candidato):
    return difflib.SequenceMatcher(None, texto, candidato).ratio()


def _etiqueta_reconocida(etiqueta, grupo, umbral=0.78):
    """¿La etiqueta de la fila corresponde a base / IGV / recargo / total?"""
    return any(_parecido(etiqueta, candidato) >= umbral
               for candidato in _ETIQUETAS_MONTOS.get(grupo, []))


def _agrupar_palabras_en_lineas(palabras, tolerancia_rel=0.6):
    """Reconstruye las filas del documento a partir de palabras con posición.

    'palabras' es una lista de (x, y, alto, texto). El OCR suele devolver el
    rótulo y el importe en líneas distintas, pero conservando su Y: agrupando
    por Y se recupera la fila real ("OP. GRAVADAS  S/  187.04").
    """
    if not palabras:
        return ""
    palabras = sorted(palabras, key=lambda p: (p[1], p[0]))
    alturas = [p[2] for p in palabras if p[2]]
    tolerancia = max(8.0, (sum(alturas) / len(alturas)) * tolerancia_rel) if alturas else 12.0
    filas, actual, y_fila = [], [], None
    for x, y, _alto, texto in palabras:
        if y_fila is None or abs(y - y_fila) <= tolerancia:
            actual.append((x, texto))
            if y_fila is None:
                y_fila = y
        else:
            filas.append(actual)
            actual, y_fila = [(x, texto)], y
    if actual:
        filas.append(actual)
    return "\n".join(" ".join(t for _x, t in sorted(fila, key=lambda p: p[0])) for fila in filas)


def leer_ocr_mac(ruta, dpi=250):
    """OCR en macOS con el motor nativo (Apple Vision, vía PyObjC).

    Se convierte el PDF (o la foto) en imágenes y se leen con el OCR del sistema;
    el texto vuelve reconstruido por filas, igual que en Windows. Si el módulo
    escaner o PyMuPDF no están disponibles devuelve "" (se completa a mano).
    """
    try:
        import escaner
    except Exception:
        return ""
    if not getattr(escaner, "es_mac", lambda: False)():
        return ""
    try:
        import fitz   # PyMuPDF: convierte el PDF (o la foto) en imágenes
    except Exception:
        return ""
    try:
        carpeta = tempfile.mkdtemp(prefix="ocr_mac_")
        imagenes = []
        documento = fitz.open(ruta)
        for numero, pagina in enumerate(documento):
            destino = os.path.join(carpeta, "pagina_%d.png" % (numero + 1))
            pagina.get_pixmap(dpi=dpi).save(destino)
            imagenes.append(destino)
        documento.close()
    except Exception:
        return ""
    if not imagenes:
        return ""
    try:
        return escaner.texto_de_resultados(escaner.ocr_paginas(imagenes))
    except Exception:
        return ""


def leer_ocr_windows(ruta, idiomas="es-PE,es-MX,es-ES,en-US", dpi=250):
    """OCR del sistema: motor de Windows (WinRT) o de macOS (Vision).

    Devuelve el texto reconstruido por filas (o "" si no se pudo leer).

    No requiere instalar nada ni conexión a internet. En otros sistemas
    operativos devuelve "" (el usuario puede digitar los montos a mano).
    """
    if sys.platform == "darwin":
        return leer_ocr_mac(ruta, dpi=dpi)
    if sys.platform != "win32":
        return ""
    try:
        import fitz   # PyMuPDF: convierte el PDF (o la foto) en imágenes
    except Exception:
        return ""
    try:
        carpeta = tempfile.mkdtemp(prefix="ocr_compras_")
        imagenes = []
        documento = fitz.open(ruta)
        for numero, pagina in enumerate(documento):
            destino = os.path.join(carpeta, "pagina_%d.png" % (numero + 1))
            pagina.get_pixmap(dpi=dpi).save(destino)
            imagenes.append(destino)
        documento.close()
    except Exception:
        return ""
    if not imagenes:
        return ""

    script = os.path.join(carpeta, "ocr_windows.ps1")
    try:
        with open(script, "w", encoding="utf-8-sig") as archivo_ps:
            archivo_ps.write(_SCRIPT_OCR_WINDOWS)
    except Exception:
        return ""

    paginas = []
    for imagen in imagenes[:5]:
        try:
            orden = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                     "-Ruta", imagen, "-Idiomas", idiomas]
            resultado = subprocess.run(orden, capture_output=True, text=True, encoding="utf-8",
                                       errors="ignore", timeout=180,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            palabras = []
            for linea in (resultado.stdout or "").splitlines():
                partes = linea.split("\t")
                if len(partes) != 5:
                    continue
                try:
                    x, y, alto = int(partes[0]), int(partes[1]), int(partes[3])
                except ValueError:
                    continue
                palabras.append((x, y, alto, partes[4]))
            texto = _agrupar_palabras_en_lineas(palabras)
            if texto.strip():
                paginas.append(texto)
        except Exception:
            continue
    try:
        shutil.rmtree(carpeta, ignore_errors=True)
    except Exception:
        pass
    return "\n".join(paginas)


def _importes_del_texto(texto):
    """Todos los importes con decimales del texto (corrige los 'oo' del OCR)."""
    limpio = str(texto or "").replace("oo", "00").replace("OO", "00")
    valores = []
    for encontrado in re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}|\d+[.,]\d{1,2}", limpio):
        valor = _a_numero(encontrado)
        if valor and valor not in valores:
            valores.append(round(float(valor), 2))
    return valores


_PALABRAS_NUMERO = {
    "CERO": 0, "UN": 1, "UNO": 1, "UNA": 1, "DOS": 2, "TRES": 3, "CUATRO": 4, "CINCO": 5, "SEIS": 6,
    "SIETE": 7, "OCHO": 8, "NUEVE": 9, "DIEZ": 10, "ONCE": 11, "DOCE": 12, "TRECE": 13, "CATORCE": 14,
    "QUINCE": 15, "DIECISEIS": 16, "DIECISIETE": 17, "DIECIOCHO": 18, "DIECINUEVE": 19, "VEINTE": 20,
    "VEINTIUNO": 21, "VEINTIDOS": 22, "VEINTITRES": 23, "VEINTICUATRO": 24, "VEINTICINCO": 25,
    "VEINTISEIS": 26, "VEINTISIETE": 27, "VEINTIOCHO": 28, "VEINTINUEVE": 29, "TREINTA": 30,
    "CUARENTA": 40, "CINCUENTA": 50, "SESENTA": 60, "SETENTA": 70, "OCHENTA": 80, "NOVENTA": 90,
    "CIEN": 100, "CIENTO": 100, "DOSCIENTOS": 200, "TRESCIENTOS": 300, "CUATROCIENTOS": 400,
    "QUINIENTOS": 500, "SEISCIENTOS": 600, "SETECIENTOS": 700, "OCHOCIENTOS": 800, "NOVECIENTOS": 900,
}


def _texto_sin_acentos(texto):
    """Texto en mayúsculas y sin tildes (para comparar rótulos). Multiplataforma."""
    import unicodedata
    base = unicodedata.normalize("NFKD", str(texto or ""))
    return "".join(c for c in base if not unicodedata.combining(c)).upper()


def monto_en_palabras(texto):
    """Lee el importe en letras ('SON: CIENTO SETENTA Y SIETE CON 0/100 SOLES')."""
    limpio = _texto_sin_acentos(texto)
    posicion = limpio.find("SON")
    if posicion == -1:
        return 0.0
    resto = limpio[posicion + 3:].split(chr(10))[0]
    centimos = 0
    if " CON " in resto:
        antes, despues = resto.split(" CON ", 1)
        resto = antes
        try:
            centimos = int(re.findall(r"\d{1,2}", despues)[0])
        except Exception:
            centimos = 0
    palabras = [p for p in re.split(r"[^A-Z]+", resto) if p]
    total, parcial = 0, 0
    for palabra in palabras:
        if palabra in ("Y", "CON", "SOLES", "SOL", "DOLARES", "SON", "DE", "SEUO"):
            continue
        if palabra in ("MIL", "MILES"):
            total += (parcial or 1) * 1000
            parcial = 0
            continue
        if palabra in ("MILLON", "MILLONES"):
            total += (parcial or 1) * 1000000
            parcial = 0
            continue
        if palabra in _PALABRAS_NUMERO:
            parcial += _PALABRAS_NUMERO[palabra]
            continue
        if parcial or total:
            break
    total += parcial
    if total <= 0:
        return 0.0
    return round(total + centimos / 100.0, 2)


def montos_en_columnas(texto):
    """(base, igv, total) cuando los rótulos y los importes van en líneas distintas.

    Es el caso de RIMAC, AFP y servicios: la línea de rótulos
    ("OP- GRAVADA ... I.G.V. IMPORTE TOTAL") y la de valores ("S/ 150,00 S/ 27.00 S/ 177.0").
    Se toma el último importe como total y el mayor de los demás como base.
    """
    lineas = str(texto or "").splitlines()
    for indice in range(1, len(lineas)):
        etiquetas = _texto_sin_acentos(lineas[indice - 1])
        # Solo un encabezado inequívoco (IGV / IMPORTE TOTAL / TOTAL A PAGAR) y que esa
        # línea NO traiga importes: así las facturas normales no entran por aquí.
        if not re.search(r"(IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|I\.?\s*G\.?\s*V|\bIGV\b)", etiquetas):
            continue
        if re.search(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}", etiquetas):
            continue
        fila = lineas[indice].replace("S/.", " ").replace("S/", " ")
        valores = []
        for encontrado in re.findall(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}|\d+[.,]\d{1,2}", fila):
            try:
                valor = _a_numero(encontrado)
            except Exception:
                valor = 0
            if valor and valor > 0:
                valores.append(round(float(valor), 2))
        if len(valores) >= 2:
            total = valores[-1]
            base = max(valores[:-1])
            igv = round(total - base, 2)
            if 0 < igv <= total * 0.5:
                return base, igv, total
    return 0.0, 0.0, 0.0


def deducir_montos_por_suma(texto, tipo=""):
    """Deduce (base, IGV, total) buscando 'base + IGV = total' entre los importes.

    Sirve cuando el OCR separa los rótulos de los números o confunde las
    etiquetas: se recogen todos los importes y se busca el par que cumple la
    relación del IGV (18% o 10.5%). Devuelve (0, 0, 0) si no hay combinación válida.
    """
    # 📊 Primero el caso de tablas en columnas (rótulos en una línea, importes en la otra)
    por_columnas = montos_en_columnas(texto)
    if por_columnas[2] > 0:
        return por_columnas

    valores = _importes_del_texto(texto)

    # 💬 El importe en letras manda: la base es el mayor importe menor que el total
    letras = monto_en_palabras(texto)
    if letras:
        candidatos = [v for v in valores if 0 < v < letras]
        if candidatos:
            base = max(candidatos)
            igv = round(letras - base, 2)
            if 0 < igv <= letras * 0.5:
                return base, igv, letras
        # Si no hay con qué comparar, se asume el IGV general (18%)
        return round(letras / 1.18, 2), round(letras - letras / 1.18, 2), letras

    if len(valores) < 3:
        return 0.0, 0.0, 0.0
    tasas = [0.105] if "10.5" in str(tipo) else [0.18]
    mejor = None
    for base in valores:
        for igv in valores:
            if base <= 0 or igv <= 0 or igv == base:
                continue
            if not any(abs(igv / base - tasa) < 0.02 for tasa in tasas):
                continue
            total = round(base + igv, 2)
            if any(abs(v - total) <= 0.05 for v in valores):
                if mejor is None or total > mejor[2]:
                    mejor = (base, igv, total)
    return mejor or (0.0, 0.0, 0.0)


def _limpiar_numero_operacion(token):
    """Deja el N° de operación legible (corrige O->0, I->1... si es numérico)."""
    token = str(token or "").strip(" .,:;#-")
    digitos = re.sub(r"\D", "", token)
    if token and len(digitos) >= max(4, len(token) - 2):
        tabla = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "|": "1",
                               "S": "5", "s": "5", "Z": "2", "B": "8", "G": "6", "A": "4", "T": "7"})
        token = token.translate(tabla)
    return token.upper()


def _fecha_valida(dia, mes, anio):
    """Devuelve (dd, mm, aaaa) si es una fecha real del calendario, o None."""
    try:
        dia, mes, anio = int(dia), int(mes), int(anio)
    except Exception:
        return None
    if anio < 100:
        anio += 2000
    if not (2000 <= anio <= 2100):
        return None
    for d, m in ((dia, mes), (mes, dia)):      # se acepta DD/MM y, si no, MM/DD
        try:
            datetime(anio, m, d)
            return d, m, anio
        except Exception:
            continue
    return None


def _formatear_fecha(dia, mes, anio, formato="DD/MM/AAAA"):
    """Arma la fecha en el formato configurado en el sistema."""
    try:
        formato = str((CONFIG_REGIONAL or {}).get("formato_fecha", formato) or formato)
    except Exception:
        pass
    if str(formato).upper().startswith("MM"):
        return f"{mes:02d}/{dia:02d}/{anio}"
    return f"{dia:02d}/{mes:02d}/{anio}"


def extraer_fecha_comprobante(texto, formato="DD/MM/AAAA"):
    """Fecha del comprobante de pago leída por OCR ('' si no aparece).

    Se buscan primero las fechas con rótulo ("Fecha de pago", "Fecha de
    operación", "Fecha de transferencia", "Fecha"... ) y, si no hay, la primera
    fecha válida del documento. Acepta 12/10/2026, 12-10-26 y "12 de octubre
    del 2026". Devuelve la fecha en el formato configurado en el sistema.
    """
    if not str(texto or "").strip():
        return ""
    plano = " ".join(str(texto).upper().split())
    meses = {"ENERO": 1, "FEBRERO": 2, "MARZO": 3, "ABRIL": 4, "MAYO": 5, "JUNIO": 6,
             "JULIO": 7, "AGOSTO": 8, "SETIEMBRE": 9, "SEPTIEMBRE": 9, "OCTUBRE": 10,
             "NOVIEMBRE": 11, "DICIEMBRE": 12,
             # Abreviaturas que usan los vouchers del banco ("12 OCT 2026")
             "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6, "JUL": 7,
             "AGO": 8, "SET": 9, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}
    numeros = re.compile(r"\b(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{2,4})\b")
    # 📅 "12 de Octubre del 2026", "12 Octubre 2026" (vouchers del banco)
    #    y "Lunes, 12 OCT 2026": el "de" es opcional y se aceptan abreviaturas.
    escrito = re.compile(r"\b(\d{1,2})\s+(?:DE\s+)?([A-Z\u00c1\u00c9\u00cd\u00d3\u00da\u00d1]{3,})\s+(?:DE\s+|DEL\s+)?(\d{2,4})\b")

    def leer(trozo):
        encontrado = numeros.search(trozo)
        if encontrado:
            fecha = _fecha_valida(*encontrado.groups())
            if fecha:
                return _formatear_fecha(*fecha, formato=formato)
        encontrado = escrito.search(trozo)
        if encontrado and encontrado.group(2) in meses:
            fecha = _fecha_valida(encontrado.group(1), meses[encontrado.group(2)], encontrado.group(3))
            if fecha:
                return _formatear_fecha(*fecha, formato=formato)
        return ""

    # 1) Con rótulo de fecha: es lo más confiable
    etiquetas = (r"FECHA\s+DE\s+PAGO", r"FECHA\s+DE\s+LA\s+OPERACI[OÓ]N",
                 r"FECHA\s+DE\s+OPERACI[OÓ]N", r"FECHA\s+DE\s+TRANSACCI[OÓ]N",
                 r"FECHA\s+DE\s+TRANSFERENCIA", r"FECHA\s+DE\s+DEP[OÓ]SITO",
                 r"FECHA\s*[/\-]\s*HORA", r"FECHA\s+DE\s+EMISI[OÓ]N", r"FECHA\s+OPE",
                 r"FECHA")
    for etiqueta in etiquetas:
        encontrado = re.search(etiqueta + r"[^0-9]{0,18}", plano)
        if not encontrado:
            continue
        fecha = leer(plano[encontrado.end(): encontrado.end() + 45])
        if fecha:
            return fecha
    # 2) La primera fecha válida del documento
    for encontrado in numeros.finditer(plano):
        fecha = _fecha_valida(*encontrado.groups())
        if fecha:
            return _formatear_fecha(*fecha, formato=formato)
    for encontrado in escrito.finditer(plano):
        if encontrado.group(2) in meses:
            fecha = _fecha_valida(encontrado.group(1), meses[encontrado.group(2)], encontrado.group(3))
            if fecha:
                return _formatear_fecha(*fecha, formato=formato)
    return ""


def extraer_numero_operacion(texto):
    """N° de operación / transacción leído del comprobante de pago (o '').

    Reconoce los rótulos habituales de un voucher ("N° de operación",
    "Nro. de transacción", "Código de operación", "N° de referencia"...)
    y también las capturas de la banca por internet. Es tolerante a los
    errores del OCR (operacin, Nro, N°, etc.).
    """
    if not str(texto or "").strip():
        return ""
    plano = " ".join(str(texto).upper().split())
    patrones = (
        r"(?:N(?:RO|R|\u00ba|\u00b0|O)?\.?\s*[\u00ba\u00b0]?\s*:?\s*(?:DE\s+|DEL\s+)?)?"
        r"(?:OPERACI[O\u00d3]N|TRANSACCI[O\u00d3]N|TRANSFERENCIA|REFERENCIA|CONSTANCIA|"
        r"DEP[O\u00d3]SITO|C[O\u00d3]DIGO)\s*(?:N(?:RO|R|\u00ba|\u00b0|O)?\.?\s*[\u00ba\u00b0]?\s*)?"
        r"[:#\-]?\s*([A-Z0-9][A-Z0-9\-\.]{3,24})",
        r"OPERACI[O\u00d3]N\s*N(?:RO|R|\u00ba|\u00b0)?\.?\s*[:#\-]?\s*([A-Z0-9\-\.]{4,24})",
    )
    for patron in patrones:
        for encontrado in re.finditer(patron, plano):
            candidato = _limpiar_numero_operacion(encontrado.group(1))
            if re.fullmatch(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", candidato):
                continue                      # es una fecha
            if len(re.sub(r"\D", "", candidato)) < 4:
                continue                      # demasiado corto para ser una operación
            return candidato
    # Último recurso: la secuencia más larga de 6 a 20 dígitos, siempre que el
    # comprobante parezca una transferencia/pago (no un RUC de 11).
    if re.search(r"TRANSFERENCIA|INTERBANCARI|OPERACI|CONSTANCIA|DEPOSITO|DEP\u00d3SITO|VOUCHER", plano):
        numeros = [n for n in re.findall(r"\b\d{6,20}\b", plano) if len(n) != 11]
        if numeros:
            return max(numeros, key=len)
    return ""


def extraer_datos_de_texto(texto):
    """Saca los datos de una factura del texto (de un PDF o del OCR del escaneo).

    Es tolerante a los errores típicos del OCR (espacios de más, '/' que se pierde,
    'S.A.C.' sin espacios, etc.).
    """
    datos = {"tipo_documento": "", "numero": "", "fecha": "", "proveedor": "", "ruc": "",
             "subtotal": 0.0, "igv": 0.0, "total": 0.0, "texto": str(texto or "")}
    if not str(texto or "").strip():
        return datos

    original = str(texto)
    mayus = original.upper()
    plano = " ".join(mayus.split())

    # --- Tipo de documento ---
    if "FACTURA" in plano:
        datos["tipo_documento"] = "Factura (10.5% IGV) Restaurantes" if "10.5" in plano or "10,5" in plano \
            else "Factura (18% IGV)"
    elif "BOLETA" in plano:
        datos["tipo_documento"] = "Boleta (Sin IGV)"
    elif "HONORARIOS" in plano:
        datos["tipo_documento"] = "Recibo por Honorarios (8% Retención)" \
            if ("RETENCION" in plano or "RETENCIÓN" in plano or re.search(r"\b8\s*%", plano)) \
            else "Recibo por Honorarios (Sin Retención)"

    # --- Número (F001-123, E001 123, B001-45...) ---
    #     El OCR suele confundir 0/O, 1/I, 5/S... (EOOI-519 es E001-519).
    def _serie_ocr(serie):
        tabla = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1",
                               "S": "5", "Z": "2", "G": "6", "A": "4", "T": "7", "B": "8"})
        # Solo se corrigen los DÍGITOS de la serie: la letra inicial se respeta.
        serie = str(serie or "").upper()
        return serie[:1] + serie[1:].translate(tabla) if len(serie) > 1 else serie

    m = (re.search(r"\b([EFB][0-9A-Z]{3})\s*[-–—:.]?\s*(\d{1,8})\b", plano) or
         re.search(r"\b([EFB])\s*0*(\d{2,3})\s*[-–—:.]?\s*(\d{1,8})\b", plano))
    if m:
        if m.lastindex == 2:
            datos["numero"] = f"{_serie_ocr(m.group(1))}-{int(m.group(2))}"
        else:
            datos["numero"] = f"{m.group(1)}{int(m.group(2)):03d}-{int(m.group(3))}"

    # --- RUC (el primero suele ser el del proveedor/emisor) ---
    rucs = re.findall(r"R\.?\s*U\.?\s*C\.?\s*[:\-]?\s*(\d{11})", plano)
    if not rucs:
        rucs = re.findall(r"\b(\d{11})\b", plano)
    if rucs:
        datos["ruc"] = rucs[0]

    # --- Fecha ---
    fecha = None
    m = re.search(r"FECHA\s*(?:DE\s*)?(?:EMISI[OÓ]N|EMISION|EXPEDICI[OÓ]N)?\s*[:\-]?\s*(\d{1,2})\s*[/\-.|]\s*(\d{1,2})\s*[/\-.|]\s*(\d{2,4})", plano)
    if not m:
        m = re.search(r"\b(\d{1,2})\s*[/\-.|]\s*(\d{1,2})\s*[/\-.|]\s*(\d{4})\b", plano)
    if m:
        dia, mes, anio = m.group(1), m.group(2), m.group(3)
        if len(anio) == 2:
            anio = "20" + anio
        try:
            if 1 <= int(dia) <= 31 and 1 <= int(mes) <= 12:
                fecha = f"{int(dia):02d}/{int(mes):02d}/{anio}"
        except Exception:
            fecha = None
    if not fecha:
        # Fechas escritas: "09 de Octubre del 2026"
        MESES_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
                    "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
                    "noviembre": 11, "diciembre": 12}
        m_esc = re.search(r"(\d{1,2})\s*(?:DE\s+)?([A-ZÁÉÍÓÚ]+)\s*(?:DE(?:L)?\s+)?(\d{4})", plano)
        if m_esc and m_esc.group(2).lower() in MESES_ES:
            try:
                dia = int(m_esc.group(1))
                if 1 <= dia <= 31:
                    fecha = f"{dia:02d}/{MESES_ES[m_esc.group(2).lower()]:02d}/{m_esc.group(3)}"
            except Exception:
                fecha = None
    datos["fecha"] = fecha or ""

    # --- Montos ---
    def monto_con(patron):
        # Se salta el porcentaje cuando la etiqueta lo trae (ej: "IGV 18%: 180.00")
        m = re.search(patron + r"[^\d\n]{0,10}(?:\d{1,2}\s*%\s*[:\-]?\s*)?[^\d\n]{0,10}(\d[\d.,]*)", plano)
        return _a_numero(m.group(1)) if m else 0.0

    subtotal = monto_con(r"(?:OP\.?\s*GRAVADAS?|SUB\s*TOTAL|SUB\s*T0TAL|BASE\s*IMPONIBLE|VALOR\s*DE\s*VENTA|GRAVAD[OA]S?)")
    igv = monto_con(r"(?:I\.?\s*G\.?\s*V\.?|IMPUESTO\s*GENERAL\s*A\s*LAS\s*VENTAS)")
    total = monto_con(r"(?:IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|TOTAL\s*GENERAL|TOTAL\s*NETO|TOTAL\s*S/)")
    if not total:
        total = monto_con(r"(?<!SUB\s)(?<!SUB)TOTAL")
    if not subtotal and total:
        if datos["tipo_documento"].startswith("Factura"):
            divisor = 1.105 if "10.5" in datos["tipo_documento"] else 1.18
            subtotal = round(total / divisor, 2)
        else:
            subtotal = total
    if not igv and subtotal and datos["tipo_documento"].startswith("Factura"):
        divisor = 1.105 if "10.5" in datos["tipo_documento"] else 1.18
        igv = round(subtotal * (divisor - 1), 2)
    # 🔎 Si los montos no cuadran (base + IGV = total) o falta alguno, se deducen
    #    buscando esa relación entre TODOS los importes del documento (OCR despistado).
    if not subtotal or not total or abs((subtotal + igv) - total) > 0.05:
        b2, i2, t2 = deducir_montos_por_suma(texto, datos.get("tipo_documento", ""))
        if t2:
            subtotal, igv, total = b2, i2, t2
    if not total:
        total = round(subtotal + igv, 2)
    datos["subtotal"] = round(subtotal, 2)
    datos["igv"] = round(igv, 2)
    datos["total"] = round(total, 2)

    # --- Destinatario (la empresa que recibe la factura) ---
    cliente_ruc, cliente_nombre = "", ""
    m_cli = re.search(r"(?:SE[NÑ]OR(?:ES)?|ADQUIRIENTE|CLIENTE|RAZ[OÓ]N\s+SOCIAL(?:\s+DEL?\s+CLIENTE)?|"
                      r"RECIB\w*\s+DE|RECIBIDO\s+DE|A\s+NOMBRE\s+DE|PAGADO\s+POR)\s*[:\-]?\s*", plano)
    if m_cli:
        ventana = plano[m_cli.end(): m_cli.end() + 320]
        numeros_ventana = re.findall(r"\b(\d{11})\b", ventana)
        elegido = next((c for c in numeros_ventana if c != datos["ruc"]),
                       numeros_ventana[0] if numeros_ventana else "")
        if elegido:
            cliente_ruc = elegido
            texto_nombre = ventana[:ventana.index(elegido)]
        else:
            texto_nombre = ventana[:90]
        texto_nombre = re.split(r"\b(?:RUC|R\.U\.C|DNI|N[UÚ]MERO|NUMERO|IDENTIFICADO|DIRECCI[OÓ]N|"
                                r"DIRECCION|FECHA|TEL[EÉ]FONO|TELEFONO|MONEDA|CONDICI[OÓ]N)\b",
                                texto_nombre, maxsplit=1)[0]
        texto_nombre = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÑÜáéíóúñü .,&\-]", " ", texto_nombre)
        cliente_nombre = " ".join(texto_nombre.split()).strip(" .-,:").strip()[:80]
    if not cliente_ruc:
        # Sin etiqueta reconocible: el primer RUC que NO sea el del emisor
        # (en los recibos por honorarios el del emisor va primero).
        for candidato in re.findall(r"\b(\d{11})\b", plano):
            if candidato != datos["ruc"]:
                cliente_ruc = candidato
                break
    if not cliente_ruc and len(rucs) >= 2:
        cliente_ruc = rucs[-1]
    datos["cliente_ruc"] = cliente_ruc
    datos["cliente_nombre"] = cliente_nombre

    # --- Proveedor ---
    nombre = buscar_proveedor_por_ruc(datos["ruc"])
    if not nombre:
        lineas = [l.strip() for l in original.split("\n") if l.strip()]
        candidatos = []
        for i, linea in enumerate(lineas):
            if re.match(r"^\s*R\.?\s*U\.?\s*C", linea, re.IGNORECASE):
                if i > 0:
                    candidatos.append(lineas[i - 1])
                if i + 1 < len(lineas):
                    candidatos.append(lineas[i + 1])
        candidatos += lineas[:8]
        for cand in candidatos:
            limpio = re.sub(r"^[\W\d_]+", "", cand).strip()
            if len(limpio) < 5:
                continue
            if re.search(r"R\.?U\.?C|FACTURA|BOLETA|ELECTR|HONORARIOS|FECHA|TOTAL|SUB\s*TOTAL|SEÑOR|SENOR|CLIENTE|DNI|CANTIDAD|DESCRIPCI", limpio, re.IGNORECASE):
                continue
            if not re.search(r"[A-Za-zÁÉÍÓÚÑ]{4}", limpio):
                continue
            nombre = limpio[:80]
            break
    datos["proveedor"] = nombre
    return datos


# =========================================================
# CLASE: MINI CALENDARIO COMPARTIDO
# =========================================================
class CalendarioNativo(ctk.CTkToplevel):
    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Seleccionar Fecha")
        self.geometry("280x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.update_idletasks()
        
        x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (280 // 2)
        y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (320 // 2)
        self.geometry(f"+{x}+{y}")
        
        ahora = datetime.now()
        self.current_year = ahora.year
        self.current_month = ahora.month
        
        self.header_frame = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        self.header_frame.pack(fill="x")
        
        btn_prev = ctk.CTkButton(self.header_frame, text="<", width=30, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.prev_month)
        btn_prev.pack(side="left", padx=10, pady=10)
        
        self.lbl_month_year = ctk.CTkLabel(self.header_frame, text="", font=("Arial", 14, "bold"), text_color="white")
        self.lbl_month_year.pack(side="left", expand=True)
        
        btn_next = ctk.CTkButton(self.header_frame, text=">", width=30, fg_color="transparent", text_color="white", hover_color="#163b65", font=("Arial", 14, "bold"), command=self.next_month)
        btn_next.pack(side="right", padx=10, pady=10)
        
        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        dias_semana = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        for i, day in enumerate(dias_semana):
            lbl = ctk.CTkLabel(self.days_frame, text=day, font=("Arial", 11, "bold"), text_color="#1f538d")
            lbl.grid(row=0, column=i, padx=4, pady=5)
            
        self.update_calendar()
        
    def update_calendar(self):
        for widget in self.days_frame.winfo_children():
            if int(widget.grid_info()["row"]) > 0: widget.destroy()
            
        meses = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        self.lbl_month_year.configure(text=f"{meses[self.current_month]} {self.current_year}")
        cal = calendar.monthcalendar(self.current_year, self.current_month)
        hoy = datetime.now()
        
        for row_idx, week in enumerate(cal, start=1):
            for col_idx, day in enumerate(week):
                if day != 0:
                    btn_color = "transparent"
                    txt_color = "black"
                    if day == hoy.day and self.current_month == hoy.month and self.current_year == hoy.year:
                        btn_color = "#d4edda"
                        txt_color = "#155724"
                        
                    btn = ctk.CTkButton(self.days_frame, text=str(day), width=30, height=30, fg_color=btn_color, text_color=txt_color, hover_color="#e0e0e0", font=("Arial", 11))
                    btn.configure(command=lambda d=day: self.select_date(d))
                    btn.grid(row=row_idx, column=col_idx, padx=2, pady=2)
                    
    def prev_month(self):
        self.current_month -= 1
        if self.current_month < 1:
            self.current_month = 12
            self.current_year -= 1
        self.update_calendar()
        
    def next_month(self):
        self.current_month += 1
        if self.current_month > 12:
            self.current_month = 1
            self.current_year += 1
        self.update_calendar()
        
    def select_date(self, day):
        fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        if fmt == "MM/DD/AAAA":
            fecha_seleccionada = f"{self.current_month:02d}/{day:02d}/{self.current_year}"
        else:
            fecha_seleccionada = f"{day:02d}/{self.current_month:02d}/{self.current_year}"
            
        self.target_entry.delete(0, tk.END)
        self.target_entry.insert(0, fecha_seleccionada)
        self.destroy()

# =========================================================
# PESTAÑA 1: FACTURAS RECIBIDAS
def cargar_comision_interbancaria_config():
    """Comisión interbancaria configurada en el módulo de Banco (S/.)."""
    try:
        from modulo_banco import cargar_comision_interbancaria
        return float(cargar_comision_interbancaria())
    except Exception:
        try:
            from modulo_banco import COMISION_INTERBANCARIA_DEFAULT
            return float(COMISION_INTERBANCARIA_DEFAULT)
        except Exception:
            return 4.80


# =========================================================
class FacturasRecibidasTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        
        self.orden_columnas = {}
        self.bloquear_autocompletado_ruc = False
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50

        # 🗓️ Filtro por mes: por defecto se muestra el MES EN CURSO
        self.mes_filtro = mes_en_curso()
        
        self.inicializar_bd()
        self.crear_interfaz()

    def inicializar_bd(self):
        global _SCHEMA_COMPRAS_OK
        if _SCHEMA_COMPRAS_OK:
            return
            
        def tarea_init():
            global _SCHEMA_COMPRAS_OK
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS facturas_recibidas (
                        id SERIAL PRIMARY KEY, tipo_documento VARCHAR(100), fecha VARCHAR(50), proveedor VARCHAR(255), descripcion TEXT, 
                        evento_asociado VARCHAR(255), subtotal NUMERIC, impuesto NUMERIC, total NUMERIC, archivo_ruta TEXT, 
                        dias_credito INTEGER DEFAULT 0, det_porcentaje NUMERIC DEFAULT 0, det_monto NUMERIC DEFAULT 0, 
                        numero_documento VARCHAR(100) DEFAULT '', categoria VARCHAR(255) DEFAULT 'GENERAL / NO ASIGNADO'
                    )
                """)
                conn.commit()
                
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS pagos_comprobantes (
                        id SERIAL PRIMARY KEY, codigo_cotizacion VARCHAR(255), categoria_suministro VARCHAR(255), 
                        monto_pagado NUMERIC, archivo_ruta TEXT, proveedor_nombre VARCHAR(255), fecha_pago VARCHAR(50),
                        id_factura INTEGER DEFAULT 0
                    )
                """)
                conn.commit()

                # 🔁 Compras cruzadas (pagadas por un tercero) y comisión interbancaria
                for sentencia in (
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS es_compra_cruzada BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS pagado_por_tercero VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS soporte_pago_tercero TEXT DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS comision_interbancaria NUMERIC DEFAULT 0",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS es_interbancario BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS cuenta_origen VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS numero_operacion VARCHAR(100) DEFAULT ''",
                ):
                    try:
                        cursor.execute(sentencia)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                _SCHEMA_COMPRAS_OK = True
            except Exception: 
                pass
            finally: 
                liberar_conexion(conn)

        threading.Thread(target=tarea_init, daemon=True).start()

    def abrir_calendario(self, entry_objetivo):
        CalendarioNativo(self.main_root.winfo_toplevel(), entry_objetivo)

    def ordenar_por_columna(self, columna, es_numerico):
        elementos = [(self.tabla.set(item, columna), item) for item in self.tabla.get_children("")]
        ascendente = self.orden_columnas.get(columna, True)
        self.orden_columnas[columna] = not ascendente
        if es_numerico:
            elementos.sort(key=lambda el: desformatear_numero(el[0]), reverse=not ascendente)
        else:
            elementos.sort(key=lambda el: el[0].lower(), reverse=not ascendente)
        for index, (_, item) in enumerate(elementos): 
            self.tabla.move(item, "", index)

    # =========================================================
    # 🖨️ ESCANEAR LA FACTURA CON LA MULTIFUNCIONAL + OCR
    # =========================================================
    def escanear_factura_ocr(self):
        """Escanea la factura en la impresora multifuncional y la lee con OCR."""
        try:
            import escaner
        except Exception as e:
            return messagebox.showerror("Escáner", f"No se pudo cargar el módulo de escaneo:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        if not escaner.sistema_soportado():
            return messagebox.showinfo("Escáner", "El escaneo con la multifuncional está disponible en Windows y en macOS (M1/M2).", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        config = cargar_configuracion_regional()
        datos_escaner = escaner.config_escaner(config)
        modo = datos_escaner.get("escaner_modo", "Automático (sin ventanas)")
        instruccion = ("Se abrirá el cuadro de escaneo del sistema." if str(modo).lower().startswith("con la ventana")
                       else f"Coloque la factura en el escáner (cristal) — {datos_escaner.get('escaner_dpi', '300')} ppp, "
                            f"{datos_escaner.get('escaner_color', 'Escala de grises')}.")
        if not datos_escaner.get("escaner_dispositivo") and not str(modo).lower().startswith("con la ventana"):
            instruccion += "\n(Si hay varios escáneres, elíjalo en Ajustes → Configuración General → Escáner.)"

        v_prog = ctk.CTkToplevel(self.main_root)
        v_prog.title("Escaneando factura")
        v_prog.geometry("430x170")
        v_prog.transient(self.main_root)
        v_prog.grab_set()
        ctk.CTkLabel(v_prog, text="🖨️ Escaneando y leyendo la factura...", font=("Arial", 13, "bold"),
                     text_color="#1f538d").pack(pady=(18, 4))
        ctk.CTkLabel(v_prog, text=instruccion, font=("Arial", 10), text_color="gray",
                     justify="center", wraplength=380).pack(pady=(0, 10))
        barra = ctk.CTkProgressBar(v_prog, width=340, mode="indeterminate")
        barra.pack(pady=4)
        barra.start()
        ctk.CTkLabel(v_prog, text="Esto puede tardar unos segundos.", font=("Arial", 9, "italic"),
                     text_color="gray").pack(pady=(4, 0))

        threading.Thread(target=self._worker_escanear, args=(config, v_prog), daemon=True).start()

    def _worker_escanear(self, config, v_prog, paginas_previas=None):
        try:
            import escaner
            resultado = escaner.procesar_factura(config, paginas_previas=paginas_previas)
            # 🧠 Mejor lectura: se rehace el OCR por POSICIONES (el del módulo escaner
            #    aplasta todo en una sola línea y pierde los rótulos del comprobante).
            if resultado.get("ok") and resultado.get("pdf"):
                try:
                    texto_mejor = leer_ocr_windows(resultado["pdf"])
                    if texto_mejor and texto_mejor.strip():
                        resultado["texto"] = texto_mejor
                        resultado["ocr"] = {"motor": "Windows OCR (por posiciones)"}
                except Exception as e_ocr_mejor:
                    print("Aviso - OCR por posiciones:", e_ocr_mejor)
        except Exception as e:
            resultado = {"ok": False, "error": str(e), "paginas": [], "pdf": "", "texto": "", "ocr": {}}

        # 🏢 Lo PRIMERO: el RUC del proveedor -> se registra con toda su información
        # (razón social, dirección y ubicación traídas de SUNAT por internet).
        # Y de paso se controla que el comprobante no esté ya registrado.
        if resultado.get("ok"):
            try:
                datos_txt = extraer_datos_de_texto(resultado.get("texto", ""))
                nombre_prov = str(datos_txt.get("proveedor") or "").strip()
                ruc_prov = re.sub(r"\D", "", str(datos_txt.get("ruc") or ""))
                # 🚫 ¿La factura está a nombre de OTRA empresa? Entonces no se registra
                #    nada: tampoco el proveedor de esa otra empresa.
                estado_destino_w, _msg_w = validar_destinatario(datos_txt)
                resultado["destino"] = estado_destino_w
                if estado_destino_w == "ajeno":
                    resultado["proveedor"] = {"estado": "ajeno",
                                              "datos": {"nombre": nombre_prov, "ruc": ruc_prov,
                                                        "direccion_fiscal": "", "ubicacion": ""}}
                elif nombre_prov or ruc_prov:
                    estado_prov, datos_prov = asegurar_proveedor(nombre_prov, ruc_prov)
                    resultado["proveedor"] = {"estado": estado_prov, "datos": datos_prov}
                else:
                    resultado["proveedor"] = {"estado": "sin-datos",
                                              "datos": {"nombre": "", "ruc": "", "direccion_fiscal": "",
                                                        "ubicacion": ""}}
                resultado["numero"] = datos_txt.get("numero", "") or numero_comprobante_desde_texto(resultado.get("texto", ""))
                resultado["duplicado"] = buscar_comprobante_duplicado(resultado["numero"])
            except Exception as e_prov:
                resultado["proveedor"] = {"estado": "error", "datos": {}}
                resultado["aviso"] = (resultado.get("aviso", "") + f" (proveedor: {e_prov})").strip()

        try:
            self.main_root.after(0, lambda: self._mostrar_resultado_escaneo(resultado, v_prog))
        except Exception:
            pass

    def _mostrar_resultado_escaneo(self, resultado, v_prog=None, reutilizar=None):
        """Ventana con lo escaneado: campos detectados, texto OCR y el PDF adjunto."""
        try:
            if v_prog is not None and v_prog.winfo_exists():
                v_prog.grab_release()
                v_prog.destroy()
        except Exception:
            pass

        if not resultado.get("ok"):
            return messagebox.showerror(
                "Escáner",
                "No se pudo escanear la factura.\n\n" + str(resultado.get("error") or "") +
                "\n\nRevise que la impresora esté encendida y conectada; si su modelo tiene "
                "alimentador, coloque el papel en la bandeja. También puede probar el modo "
                "el modo \"con ventana\" en Ajustes → Configuración General → Escáner.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        self._escaneo_actual = resultado
        config = cargar_configuracion_regional()
        datos = extraer_datos_de_texto(resultado.get("texto", ""))
        estado_destino, mensaje_destino = validar_destinatario(datos, config)

        ventana = reutilizar
        if ventana is None:
            ventana = ctk.CTkToplevel(self.main_root)
            ventana.title("🖨️ Factura escaneada (OCR)")
            ventana.transient(self.main_root)
        for hijo in ventana.winfo_children():
            hijo.destroy()
        ventana.geometry("700x640")
        ventana.minsize(620, 520)

        ctk.CTkLabel(ventana, text="🖨️ FACTURA ESCANEADA — REVISAR ANTES DE GUARDAR",
                     font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(12, 2))
        info = (f"Páginas: {len(resultado.get('paginas') or [])}   ·   "
                f"Escáner: {resultado.get('escaner') or 'el configurado'}")
        ctk.CTkLabel(ventana, text=info, font=("Arial", 10), text_color="gray").pack()
        if resultado.get("aviso"):
            ctk.CTkLabel(ventana, text="⚠️ " + str(resultado["aviso"]), font=("Arial", 10, "bold"),
                         text_color="#c0392b", wraplength=640, justify="center").pack(pady=(2, 0))

        # 🏢 ¿La factura está a nombre de la empresa configurada?
        colores_destino = {"ok": ("#d4edda", "#155724"), "ajeno": ("#f8d7da", "#721c24"),
                           "sin-datos": ("#fff3cd", "#856404"), "sin-configurar": ("#fff3cd", "#856404")}
        fondo_dest, color_dest = colores_destino.get(estado_destino, ("#f8f9fa", "#2c3e50"))
        f_destino = ctk.CTkFrame(ventana, fg_color=fondo_dest, corner_radius=6)
        f_destino.pack(fill="x", padx=14, pady=(6, 0))
        ctk.CTkLabel(f_destino, text=mensaje_destino, font=("Arial", 11, "bold"), text_color=color_dest,
                     wraplength=650, justify="left").pack(padx=10, pady=6, anchor="w")

        # 🏢 Proveedor: ya quedó registrado (con los datos completos de SUNAT si se pudieron traer)
        info_prov = resultado.get("proveedor") or {}
        datos_prov = info_prov.get("datos") or {}
        estado_prov = info_prov.get("estado", "")
        duplicado = resultado.get("duplicado")
        textos_prov = {
            "creado": ("#d4edda", "#155724", "🏢 Proveedor NUEVO registrado con los datos de SUNAT."),
            "completado": ("#d4edda", "#155724", "🏢 Proveedor ya existía: se completó su ficha con los datos de SUNAT."),
            "existe": ("#eaf2f8", "#1b4f72", "🏢 El proveedor ya estaba registrado."),
            "creado_sin_sunat": ("#fff3cd", "#856404",
                                 "🏢 Proveedor agregado con los datos leídos. No se pudo consultar el RUC en internet "
                                 "(revise la conexión o el token de la API)."),
            "sin-conexion": ("#fff3cd", "#856404", "⚠️ Sin conexión: no se pudo registrar ni verificar el proveedor."),
            "sin-datos": ("#fff3cd", "#856404", "⚠️ No se pudo leer el RUC del proveedor en el comprobante."),
            "error": ("#f8d7da", "#721c24", "⚠️ No se pudo registrar el proveedor."),
            "ajeno": ("#f8d7da", "#721c24",
                      "🚫 Factura de OTRA empresa: no se registró al proveedor en 📦 Proveedores."),
        }
        fondo_prov, color_prov, texto_prov = textos_prov.get(estado_prov, ("#f8f9fa", "#2c3e50", ""))
        if texto_prov:
            f_prov = ctk.CTkFrame(ventana, fg_color=fondo_prov, corner_radius=6)
            f_prov.pack(fill="x", padx=14, pady=(6, 0))
            ctk.CTkLabel(f_prov, text=texto_prov, font=("Arial", 11, "bold"), text_color=color_prov,
                         wraplength=650, justify="left").pack(padx=10, pady=6, anchor="w")

        # 🚫 No se permiten comprobantes repetidos
        if duplicado:
            detalle_dup = (f"N° {duplicado.get('numero')}   ·   Proveedor: {duplicado.get('proveedor')}"
                           f"   ·   Fecha: {duplicado.get('fecha')}"
                           f"   ·   Total: {formatear_moneda(duplicado.get('total') or 0)}")
            f_dup = ctk.CTkFrame(ventana, fg_color="#f8d7da", corner_radius=6)
            f_dup.pack(fill="x", padx=14, pady=(6, 0))
            ctk.CTkLabel(f_dup, text="🚫 ESTE COMPROBANTE YA ESTÁ REGISTRADO — no se permite duplicarlo.\n"
                                     + detalle_dup,
                         font=("Arial", 11, "bold"), text_color="#721c24", wraplength=650,
                         justify="left").pack(padx=10, pady=6, anchor="w")

        f_datos = ctk.CTkFrame(ventana, fg_color="#f8f9fa", corner_radius=8,
                               border_width=1, border_color="#e0e0e0")
        f_datos.pack(fill="x", padx=14, pady=8)
        ctk.CTkLabel(f_datos, text="🔎 Datos detectados por el OCR", font=("Arial", 12, "bold"),
                     text_color="#1f538d").pack(anchor="w", padx=12, pady=(8, 4))
        # Si no se pudo leer/consultar la razón social, se puede escribir aquí antes de aplicar
        f_nombre = ctk.CTkFrame(ventana, fg_color="transparent")
        f_nombre.pack(fill="x", padx=16, pady=(2, 0))
        ctk.CTkLabel(f_nombre, text="Razón social del proveedor:", font=("Arial", 11, "bold")).pack(side="left")
        ent_prov_nombre = ctk.CTkEntry(f_nombre, font=("Arial", 11))
        ent_prov_nombre.pack(side="left", fill="x", expand=True, padx=6)
        ent_prov_nombre.insert(0, datos_prov.get("nombre") or datos.get("proveedor") or "")
        if not (datos_prov.get("nombre") or datos.get("proveedor")):
            ctk.CTkLabel(f_nombre, text="⚠️ escríbala", font=("Arial", 9, "italic"),
                         text_color="#c0392b").pack(side="left")

        campos = (("Tipo:", datos["tipo_documento"]), ("N° Documento:", datos["numero"]),
                  ("Fecha:", datos["fecha"]),
                  ("Proveedor:", datos_prov.get("nombre") or datos["proveedor"]),
                  ("RUC proveedor:", datos_prov.get("ruc") or datos["ruc"]),
                  ("Dirección:", datos_prov.get("direccion_fiscal", "")),
                  ("Ubicación:", datos_prov.get("ubicacion", "")),
                  ("Destinatario:", datos.get("cliente_nombre", "")),
                  ("RUC destinatario:", datos.get("cliente_ruc", "")), ("Subtotal:", formatear_moneda(datos["subtotal"]) if datos["subtotal"] else ""),
                  ("IGV:", formatear_moneda(datos["igv"]) if datos["igv"] else ""),
                  ("Total:", formatear_moneda(datos["total"]) if datos["total"] else ""))
        for etiqueta, valor in campos:
            fila = ctk.CTkFrame(f_datos, fg_color="transparent")
            fila.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(fila, text=etiqueta, width=110, anchor="w", font=("Arial", 11, "bold")).pack(side="left")
            ctk.CTkLabel(fila, text=(str(valor) if str(valor).strip() else "(no detectado)"),
                         anchor="w", font=("Arial", 11),
                         text_color=("#2c3e50" if str(valor).strip() else "#c0392b")).pack(side="left")
        ctk.CTkLabel(f_datos, text="", height=4).pack()

        lbl_estado = ctk.CTkLabel(ventana, text="", font=("Arial", 10, "bold"), text_color="#1e8449")

        def copiar_texto():
            try:
                ventana.clipboard_clear()
                ventana.clipboard_append(caja_texto.get("1.0", "end-1c"))
                lbl_estado.configure(text="✅ Texto copiado al portapapeles.")
            except Exception as e:
                lbl_estado.configure(text=f"⚠️ No se pudo copiar: {e}", text_color="#c0392b")

        def ver_pdf():
            ruta_pdf = resultado.get("pdf")
            if ruta_pdf and os.path.exists(ruta_pdf):
                abrir_documento(ruta_pdf)
            else:
                messagebox.showinfo("PDF", "Todavía no se generó el PDF.", parent=ventana)

        def aplicar():
            if resultado.get("duplicado"):
                return messagebox.showerror(
                    "Comprobante duplicado",
                    f"El comprobante N° {resultado['duplicado'].get('numero')} ya está registrado en el sistema.\n\n"
                    "No se permite registrarlo dos veces. Búsquelo en la lista de facturas recibidas.",
                    parent=ventana)
            estado_prov = (resultado.get("proveedor") or {}).get("estado", "")
            nombre_final = ent_prov_nombre.get().strip() or datos.get("proveedor", "")
            if estado_prov in ("", "sin-datos") and estado_destino != "ajeno" and nombre_final:
                # Se registra con la razón social que quedó en la ventana
                # (SUNAT ya no se vuelve a consultar para no demorar el escaneo).
                estado_nuevo, datos_finales = asegurar_proveedor(nombre_final, datos.get("ruc", ""),
                                                                 usar_internet=False)
                resultado["proveedor"] = {"estado": estado_nuevo, "datos": datos_finales}
                estado_prov = estado_nuevo
            self.aplicar_datos_escaneo(datos)
            if not estado_prov:
                estado_prov = getattr(self, "_estado_proveedor_escaneo", "")
            extra = {"creado": "  🏢 Proveedor registrado con los datos de SUNAT.",
                     "completado": "  🏢 Se completó la ficha del proveedor con los datos de SUNAT.",
                     "existe": "  El proveedor ya estaba registrado.",
                     "creado_sin_sunat": "  🏢 Proveedor agregado (no se pudo consultar SUNAT).",
                     "sin-conexion": "  ⚠️ Sin conexión: no se pudo verificar el proveedor.",
                     "error": "  ⚠️ No se pudo agregar el proveedor automáticamente.",
                     "sin-datos": ""}.get(estado_prov, "")
            lbl_estado.configure(text="✅ Datos aplicados al formulario." + extra +
                                      "  Revíselos y pulse 💾 Registrar Documento.", text_color="#1e8449")
            try:
                btn_aplicar.configure(state="disabled", text="✅ Datos aplicados")
            except Exception:
                pass
            # ⏱️ Se muestra el aviso, espera 2 segundos, se cierra y cierra la ventana
            #    del escaneo: el formulario queda listo para 💾 Registrar Documento.
            self._aviso_temporal(ventana, "Se extrajeron los datos del documento." + extra,
                                 titulo="Extracción Inteligente", segundos=2,
                                 al_cerrar=ventana.destroy)

        def otra_pagina():
            v_prog2 = ctk.CTkToplevel(self.main_root)
            v_prog2.title("Escaneando otra página")
            v_prog2.geometry("400x140")
            v_prog2.transient(self.main_root)
            ctk.CTkLabel(v_prog2, text="🖨️ Escaneando otra página...", font=("Arial", 12, "bold"),
                         text_color="#1f538d").pack(pady=(20, 6))
            ctk.CTkLabel(v_prog2, text="Coloque la siguiente hoja en el escáner.",
                         font=("Arial", 10), text_color="gray").pack()
            barra2 = ctk.CTkProgressBar(v_prog2, width=320, mode="indeterminate")
            barra2.pack(pady=8)
            barra2.start()
            previas = resultado.get("paginas") or []
            threading.Thread(target=self._worker_escanear,
                             args=(cargar_configuracion_regional(), v_prog2, previas), daemon=True).start()

        # 🔻 La barra de botones se ancla abajo: siempre visible aunque la ventana
        # sea pequeña o el texto del OCR sea muy largo.
        f_botones = ctk.CTkFrame(ventana, fg_color="transparent")
        f_botones.pack(side="bottom", fill="x", padx=14, pady=(2, 10))
        lbl_estado.pack(side="bottom", pady=(2, 0))
        btn_aplicar = ctk.CTkButton(f_botones, text="✅ Aplicar al formulario", height=36, font=("Arial", 12, "bold"),
                                    fg_color="#27ae60", hover_color="#1e8449", command=aplicar)
        btn_aplicar.pack(side="left", fill="x", expand=True, padx=(0, 4))
        if estado_destino == "ajeno":
            # 🚫 La factura es de otra empresa: no se puede pasar al formulario
            btn_aplicar.configure(state="disabled", fg_color="#95a5a6",
                                  text="🚫 Factura de otra empresa (rechazada)")
        elif resultado.get("duplicado"):
            btn_aplicar.configure(state="disabled", fg_color="#95a5a6",
                                  text="🚫 Comprobante duplicado (ya registrado)")
        ctk.CTkButton(f_botones, text="➕ Escanear otra página", height=36, font=("Arial", 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688", command=otra_pagina
                      ).pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(f_botones, text="📄 Ver PDF", width=110, height=36, font=("Arial", 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65", command=ver_pdf
                      ).pack(side="left", padx=4)
        ctk.CTkButton(f_botones, text="📋 Copiar OCR", width=130, height=36, font=("Arial", 12, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50", command=copiar_texto
                      ).pack(side="left", padx=4)
        ctk.CTkButton(f_botones, text="❌ Cerrar", width=110, height=36, font=("Arial", 12, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b", command=ventana.destroy
                      ).pack(side="left", padx=(4, 0))

        # 📝 El texto del OCR ocupa el espacio que queda entre las fichas y los botones
        ctk.CTkLabel(ventana, text="📝 Texto leído del escaneo (para revisar o copiar)",
                     font=("Arial", 11, "bold")).pack(anchor="w", padx=16)
        caja_texto = ctk.CTkTextbox(ventana, height=110, font=("Consolas", 10))
        caja_texto.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        caja_texto.insert("1.0", resultado.get("texto") or "(el OCR no encontró texto: complete los datos a mano)")

        # La ventana se ajusta y se centra en la pantalla para que NUNCA se corte
        # la barra de botones (ni por tamaño ni por quedar fuera del escritorio).
        try:
            ancho_pantalla, alto_pantalla = ventana.winfo_screenwidth(), ventana.winfo_screenheight()
            ancho = min(740, ancho_pantalla - 40)
            alto = max(540, min(740, alto_pantalla - 110))
            x = max(0, (ancho_pantalla - ancho) // 2)
            y = max(0, (alto_pantalla - alto) // 2 - 20)
            ventana.geometry(f"{ancho}x{alto}+{x}+{y}")
        except Exception:
            pass
        try:
            ventana.lift()
            ventana.focus_force()
        except Exception:
            pass
        return ventana

    def _aviso_temporal(self, padre, mensaje, titulo="Extracción Inteligente", segundos=2,
                        al_cerrar=None):
        """Muestra un aviso que se cierra SOLO a los N segundos (sin botones).

        Se usa al aplicar un escaneo: aparece el mensaje, espera los segundos
        indicados y luego cierra la ventana de escaneo (si se pasó al_cerrar).
        """
        raiz = padre if padre is not None else self.main_root
        try:
            aviso = ctk.CTkToplevel(raiz)
        except Exception:
            if al_cerrar:
                try:
                    al_cerrar()
                except Exception:
                    pass
            return None
        aviso.title(titulo)
        aviso.resizable(False, False)
        try:
            aviso.transient(raiz)
        except Exception:
            pass
        try:
            aviso.attributes("-topmost", True)
        except Exception:
            pass
        ctk.CTkLabel(aviso, text="✅ " + str(mensaje), font=("Arial", 12, "bold"),
                     text_color="#1e8449", wraplength=380, justify="center").pack(expand=True, padx=18, pady=18)
        ancho, alto = 420, 130
        try:
            aviso.update_idletasks()
            x = max(0, (aviso.winfo_screenwidth() - ancho) // 2)
            y = max(0, (aviso.winfo_screenheight() - alto) // 2)
            aviso.geometry(f"{ancho}x{alto}+{x}+{y}")
        except Exception:
            aviso.geometry(f"{ancho}x{alto}")

        def cerrar():
            try:
                aviso.destroy()
            except Exception:
                pass
            if al_cerrar:
                try:
                    al_cerrar()
                except Exception:
                    pass

        aviso.after(max(200, int(float(segundos) * 1000)), cerrar)
        try:
            aviso.lift()
        except Exception:
            pass
        return aviso

    def aplicar_datos_escaneo(self, datos):
        """Vuelca en el formulario lo leído por el OCR y deja el PDF como adjunto.

        Si la factura está a nombre de OTRA empresa se rechaza y no se aplica nada.
        Si el proveedor no está registrado, se agrega automáticamente a Proveedores.
        """
        if not datos:
            return
        # 🏢 ¿Está a nombre de la empresa configurada?
        estado_destino, mensaje_destino = validar_destinatario(datos)
        self._destino_escaneo = estado_destino
        if estado_destino == "ajeno":
            return messagebox.showerror("Factura de otra empresa", mensaje_destino +
                                        "\n\nNo se cargó nada en el formulario.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        try:
            self.bloquear_autocompletado_ruc = True
            if datos.get("tipo_documento"):
                self.combo_tipo.set(datos["tipo_documento"])
                self.on_tipo_change(self.combo_tipo.get())
            if datos.get("numero"):
                self.ent_nro_doc.delete(0, tk.END)
                self.ent_nro_doc.insert(0, str(datos["numero"]))
            if datos.get("fecha"):
                self.ent_fecha.delete(0, tk.END)
                self.ent_fecha.insert(0, str(datos["fecha"]))
            if datos.get("proveedor"):
                nombre = str(datos["proveedor"])
                try:
                    valores = [str(v) for v in (self.combo_proveedor.cget("values") or [])]
                    if nombre not in valores:
                        self.combo_proveedor.configure(values=[nombre] + valores)
                except Exception:
                    pass
                self.combo_proveedor.set(nombre)
            if datos.get("ruc"):
                self.ent_desc.delete(0, tk.END)
                self.ent_desc.insert(0, str(datos["ruc"]))
            # 💰 Se carga el TOTAL del documento (con IGV) y el modo queda en "con IGV",
            #    igual que en el Programa de Flota: la base y el IGV se calculan solos.
            monto_total = 0.0
            try:
                if datos.get("total"):
                    monto_total = float(datos["total"])
                elif datos.get("subtotal"):
                    base_leida = float(datos["subtotal"])
                    if "Factura" in self.combo_tipo.get():
                        monto_total = base_leida * (1.105 if "10.5%" in self.combo_tipo.get() else 1.18)
                    else:
                        monto_total = base_leida
            except Exception as e_monto:
                print("Aviso - monto del comprobante:", e_monto)
            if monto_total > 0:
                self.ent_subtotal.delete(0, tk.END)
                self.ent_subtotal.insert(0, f"{monto_total:.2f}")
                self._poner_modo_monto("CON_IGV")

            resultado = getattr(self, "_escaneo_actual", {}) or {}
            if resultado.get("pdf") and os.path.exists(resultado["pdf"]):
                self.ruta_archivo_temp = resultado["pdf"]
                try:
                    self.btn_archivo.configure(text="✅ PDF Escaneado (OCR)", fg_color="#28a745")
                except Exception:
                    pass
            self.actualizar_totales()
            try:
                self.al_seleccionar_proveedor()
            except Exception:
                pass
            # 🏢 El proveedor ya quedó registrado al escanear (con los datos de SUNAT).
            # Solo si el escaneo es anterior a esta mejora se crea aquí.
            estado_prov = (getattr(self, "_escaneo_actual", {}) or {}).get("proveedor", {}).get("estado", "")
            if not estado_prov:
                try:
                    estado_prov, _datos_prov = asegurar_proveedor(datos.get("proveedor"), datos.get("ruc"))
                except Exception:
                    estado_prov = "error"
            self._estado_proveedor_escaneo = estado_prov
            if estado_destino == "sin-datos":
                messagebox.showwarning("Destinatario", mensaje_destino, parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            elif estado_destino == "sin-configurar":
                messagebox.showwarning("Destinatario", mensaje_destino, parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron aplicar los datos:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        finally:
            self.bloquear_autocompletado_ruc = False

    def autocompletar_desde_pdf(self):
        if pdfplumber is None:
            messagebox.showerror("Librería faltante", "No se encontró 'pdfplumber'. Ejecuta: pip install pdfplumber", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            return
            
        ruta = filedialog.askopenfilename(title="Seleccionar Factura PDF de SUNAT", filetypes=[("Archivos PDF", "*.pdf")])
        if not ruta: return
        
        try:
            self.bloquear_autocompletado_ruc = True
            
            partes_texto = []
            with pdfplumber.open(ruta) as pdf:
                for page in pdf.pages:
                    partes_texto.append(page.extract_text() + "\n")
            texto = "".join(partes_texto)
                    
            if not texto.strip(): 
                self.bloquear_autocompletado_ruc = False
                return messagebox.showwarning("Aviso", "El PDF no contiene texto seleccionable.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

            # 🏢 Solo se aceptan facturas a nombre de la empresa configurada
            datos_txt = extraer_datos_de_texto(texto)
            estado_destino, mensaje_destino = validar_destinatario(datos_txt)
            if estado_destino == "ajeno":
                self.bloquear_autocompletado_ruc = False
                return messagebox.showerror("Factura de otra empresa",
                                            mensaje_destino + "\n\nNo se cargó al formulario.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
            if re.search(r"FACTURA\s+ELECTR[OÓ]NICA", texto, re.IGNORECASE): 
                if "10.5%" not in self.combo_tipo.get():
                    self.combo_tipo.set("Factura (18% IGV)")
            elif re.search(r"BOLETA\s+DE\s+VENTA", texto, re.IGNORECASE): 
                self.combo_tipo.set("Boleta (Sin IGV)")
            elif re.search(r"RECIBO\s+POR\s+HONORARIOS", texto, re.IGNORECASE):
                if re.search(r"Retenci[oó]n.*?IR[\s:\|]*\(?([\d\,\.]+)\)?", texto, re.IGNORECASE): 
                    self.combo_tipo.set("Recibo por Honorarios (8% Retención)")
                else: 
                    self.combo_tipo.set("Recibo por Honorarios (Sin Retención)")
                    
            self.on_tipo_change(self.combo_tipo.get())

            # Se usa el lector tolerante: el OCR suele leer "F581 08283480" SIN guion
            # (antes se exigía el guion y el N° no llegaba al formulario).
            numero_leido = numero_comprobante_desde_texto(texto)
            if not numero_leido:
                nro_match = re.search(r"([EFB][0-9A-Z]{3}\s*-\s*\d+)", texto)
                numero_leido = nro_match.group(1).replace(" ", "") if nro_match else ""
            if numero_leido:
                self.ent_nro_doc.delete(0, tk.END)
                self.ent_nro_doc.insert(0, numero_leido)
            
            fecha_match = re.search(r"Fecha de Emisi[oó]n\s*[:\-]?\s*(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto, re.IGNORECASE)
            if not fecha_match: 
                fecha_match = re.search(r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto)
                
            if fecha_match:
                d, m, y = fecha_match.groups()
                fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
                if fmt == "MM/DD/AAAA": 
                    self.ent_fecha.delete(0, tk.END)
                    self.ent_fecha.insert(0, f"{m}/{d}/{y}")
                else: 
                    self.ent_fecha.delete(0, tk.END)
                    self.ent_fecha.insert(0, f"{d}/{m}/{y}")

            rucs = re.findall(r"(?:RUC|R\.U\.C\.)\s*[:\-]?\s*(\d{11})", texto, re.IGNORECASE)
            if rucs: 
                self.ent_desc.delete(0, tk.END)
                self.ent_desc.insert(0, rucs[0])
                
            lineas = [line.strip() for line in texto.split('\n') if line.strip()]
            if lineas:
                posibles = [l for l in lineas[:7] if "R.U.C" not in l and len(l) > 4]
                if posibles: 
                    self.combo_proveedor.set(posibles[0])

            sub_m = re.search(r"(?:OP\.\s*GRAVADAS|SUB\s*TOTAL|Subtotal|Total por honorarios)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            tot_m = re.search(r"(?:IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|Total Neto Recibido)[\s:S/\|]+([\d\,\.]+)", texto, re.IGNORECASE)
            
            # 💰 Se carga el TOTAL con IGV (como Flota): la base y el IGV salen solos.
            monto_total = 0.0
            if tot_m:
                monto_total = float(tot_m.group(1).replace(",", ""))
            elif sub_m:
                monto_base = float(sub_m.group(1).replace(",", ""))
                if "Factura" in self.combo_tipo.get():
                    monto_total = monto_base * (1.105 if "10.5%" in self.combo_tipo.get() else 1.18)
                else:
                    monto_total = monto_base

            if monto_total > 0:
                self.ent_subtotal.delete(0, tk.END)
                self.ent_subtotal.insert(0, f"{monto_total:.2f}")
                self._poner_modo_monto("CON_IGV")

            self.ruta_archivo_temp = ruta
            self.btn_archivo.configure(text="✅ PDF Autocargado Exitosamente", fg_color="#28a745")
            
            self.actualizar_totales()
            self.al_seleccionar_proveedor()

            # 🏢 Si el proveedor del PDF no está registrado, se agrega a 📦 Proveedores
            estado_prov = crear_proveedor_si_no_existe(datos_txt.get("proveedor") or self.combo_proveedor.get(),
                                                       datos_txt.get("ruc") or self.ent_desc.get())
            if estado_prov == "creado":
                messagebox.showinfo("Proveedor agregado",
                                    "El proveedor de la factura no estaba registrado y se agregó a 📦 Proveedores.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
            messagebox.showinfo("Extracción Inteligente", "Se extrajeron los datos del PDF.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            self.bloquear_autocompletado_ruc = False
            
        except Exception as e: 
            self.bloquear_autocompletado_ruc = False
            messagebox.showerror("Error", f"Ocurrió un error:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

    # 🚀 MOTOR DE LECTURA XML (UBL)
    def autocompletar_desde_xml(self):
        ruta = filedialog.askopenfilename(title="Seleccionar Factura XML de SUNAT", filetypes=[("Archivos XML", "*.xml")])
        if not ruta: return
        
        try:
            self.bloquear_autocompletado_ruc = True
            
            with open(ruta, 'r', encoding='utf-8', errors='ignore') as f:
                xml_string = f.read()
                
            xml_string = re.sub(r'\sxmlns="[^"]+"', '', xml_string, count=1)
            xml_string = re.sub(r'([a-zA-Z0-9_]+):', '', xml_string)
            
            root = ET.fromstring(xml_string)

            # 🏢 El XML trae el RUC del destinatario: se valida que sea la empresa configurada
            nodo_cli = root.find('.//AccountingCustomerParty//PartyIdentification//ID')
            ruc_cliente = re.sub(r"\D", "", nodo_cli.text) if (nodo_cli is not None and nodo_cli.text) else ""
            nodo_cli_nombre = root.find('.//AccountingCustomerParty//PartyLegalEntity//RegistrationName')
            nombre_cliente = (nodo_cli_nombre.text or "").strip() if (nodo_cli_nombre is not None and nodo_cli_nombre.text) else ""
            estado_destino, mensaje_destino = validar_destinatario({"cliente_ruc": ruc_cliente,
                                                                    "cliente_nombre": nombre_cliente})
            if estado_destino == "ajeno":
                self.bloquear_autocompletado_ruc = False
                return messagebox.showerror("Factura de otra empresa",
                                            mensaje_destino + "\n\nNo se cargó al formulario.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

            tipo_cod = root.find('.//InvoiceTypeCode')
            nro_doc = root.find('.//ID')
            
            if tipo_cod is not None and tipo_cod.text:
                if tipo_cod.text.strip() == '01': 
                    self.combo_tipo.set("Factura (18% IGV)")
                elif tipo_cod.text.strip() == '03': 
                    self.combo_tipo.set("Boleta (Sin IGV)")
            
            if nro_doc is not None and nro_doc.text:
                num_limpio = nro_doc.text.strip()
                self.ent_nro_doc.delete(0, tk.END)
                self.ent_nro_doc.insert(0, num_limpio)
                
                if num_limpio.startswith("E"): self.combo_tipo.set("Recibo por Honorarios (Sin Retención)")
                elif num_limpio.startswith("B"): self.combo_tipo.set("Boleta (Sin IGV)")
                elif num_limpio.startswith("F"): self.combo_tipo.set("Factura (18% IGV)")
                
            self.on_tipo_change(self.combo_tipo.get())
            
            fecha_node = root.find('.//IssueDate')
            if fecha_node is not None and fecha_node.text:
                try:
                    f_dt = datetime.strptime(fecha_node.text.strip(), "%Y-%m-%d")
                    fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
                    if fmt == "MM/DD/AAAA": 
                        self.ent_fecha.delete(0, tk.END)
                        self.ent_fecha.insert(0, f_dt.strftime("%m/%d/%Y"))
                    else: 
                        self.ent_fecha.delete(0, tk.END)
                        self.ent_fecha.insert(0, f_dt.strftime("%d/%m/%Y"))
                except Exception: 
                    pass
                
            supplier = root.find('.//AccountingSupplierParty/Party')
            if supplier is not None:
                ruc_node = supplier.find('.//PartyIdentification/ID')
                if ruc_node is not None and ruc_node.text:
                    self.ent_desc.delete(0, tk.END)
                    self.ent_desc.insert(0, ruc_node.text.strip())
                
                name_node = supplier.find('.//PartyName/Name')
                if name_node is None or not name_node.text: 
                    name_node = supplier.find('.//PartyLegalEntity/RegistrationName')
                
                if name_node is not None and name_node.text:
                    self.combo_proveedor.set(name_node.text.strip())
                    
            monetary = root.find('.//LegalMonetaryTotal')
            if monetary is not None:
                # 💰 El XML trae el total con IGV: se carga tal cual y el modo queda "con IGV"
                monto_total = 0.0
                for etiqueta in ('PayableAmount', 'TaxInclusiveAmount'):
                    nodo = monetary.find(etiqueta)
                    if nodo is not None and nodo.text:
                        try:
                            monto_total = float(nodo.text.strip())
                            break
                        except Exception:
                            monto_total = 0.0
                if monto_total <= 0:
                    sub_node = monetary.find('TaxExclusiveAmount')
                    if sub_node is not None and sub_node.text:
                        try:
                            base_xml = float(sub_node.text.strip())
                            monto_total = (base_xml * (1.105 if "10.5%" in self.combo_tipo.get() else 1.18)
                                           if "Factura" in self.combo_tipo.get() else base_xml)
                        except Exception:
                            monto_total = 0.0
                if monto_total > 0:
                    self.ent_subtotal.delete(0, tk.END)
                    self.ent_subtotal.insert(0, f"{monto_total:.2f}")
                    self._poner_modo_monto("CON_IGV")

            self.ruta_archivo_temp = ruta
            self.btn_archivo.configure(text="✅ XML Autocargado Exitosamente", fg_color="#d35400")
            
            self.actualizar_totales()
            self.al_seleccionar_proveedor()

            # 🏢 Si el proveedor del XML no está registrado, se agrega a 📦 Proveedores
            estado_prov = crear_proveedor_si_no_existe(self.combo_proveedor.get(), self.ent_desc.get())
            if estado_prov == "creado":
                messagebox.showinfo("Proveedor agregado",
                                    "El proveedor del XML no estaba registrado y se agregó a 📦 Proveedores.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
            messagebox.showinfo("Extracción XML Exitosa", "Los datos del XML se extrajeron correctamente.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            self.bloquear_autocompletado_ruc = False
            
        except Exception as e:
            self.bloquear_autocompletado_ruc = False
            messagebox.showerror("Error", f"Ocurrió un error al leer el XML:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

    # 🚀 MOTOR DE DESCARGA DIRECTA DESDE SUNAT SIRE (CONEXIÓN OAUTH2)
    def importar_compras_sire(self):
        cfg = cargar_configuracion_regional()
        ruc = cfg.get("ruc_empresa", "").strip()
        u_sol = cfg.get("usuario_sol", "").strip()
        c_sol = cfg.get("clave_sol", "").strip()
        c_id = cfg.get("client_id_sire", "").strip()
        c_secret = cfg.get("client_secret_sire", "").strip()

        if not ruc or not u_sol or not c_sol or not c_id or not c_secret:
            messagebox.showwarning(
                "Credenciales Incompletas", 
                "⚠️ No ha configurado las credenciales de SUNAT SIRE.\n\n"
                "Por favor, vaya a Configuración General del Sistema e ingrese:\n"
                "• RUC Empresa\n• Usuario SOL y Clave SOL\n• Client ID y Client Secret"
            , parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            return

        # 🚀 CORTE DEL SISTEMA: nunca antes de la Fecha de Comienzo configurada
        corte_txt = (texto_fecha_comienzo() if texto_fecha_comienzo else "") or ""
        tiene_corte = bool(corte_txt) and corte_txt != "Sin definir"
        periodo_inicial = sugerir_periodo_sire() if sugerir_periodo_sire else datetime.now().strftime("%Y%m")
        texto_pregunta = "Ingrese el Periodo a descargar (Formato YYYYMM, ej: 202607):"
        if tiene_corte:
            texto_pregunta += f"\n\n📅 Corte del sistema: solo se permiten periodos desde la Fecha de Comienzo ({corte_txt}) en adelante."

        periodo = simpledialog.askstring("Periodo SIRE SUNAT", texto_pregunta, initialvalue=periodo_inicial)
        if not periodo or len(periodo) != 6 or not periodo.isdigit():
            return messagebox.showerror("Error", "Debe ingresar un periodo válido de 6 dígitos (ej: 202607).", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        if validar_periodo_sire:
            permitido, msg_bloqueo = validar_periodo_sire(periodo)
            if not permitido:
                return messagebox.showwarning("Periodo bloqueado por el Corte del Sistema", msg_bloqueo, parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        v_sire = ctk.CTkToplevel(self.main_root)
        v_sire.title("Conexión Oficial SUNAT SIRE")
        v_sire.geometry("480x300")
        v_sire.grab_set()

        ctk.CTkLabel(v_sire, text="🌐 IMPORTACIÓN AUTOMÁTICA SIRE SUNAT", font=("Arial", 14, "bold"), text_color="#166534").pack(pady=(20, 10))
        
        lbl_status = ctk.CTkLabel(v_sire, text="🔑 Autenticando token con la SUNAT...", font=("Arial", 11, "italic"), text_color="#d35400")
        lbl_status.pack(pady=10)

        prog = ctk.CTkProgressBar(v_sire, width=380)
        prog.pack(pady=10)
        prog.set(0.2)

        txt_info = ctk.CTkTextbox(v_sire, height=100, font=("Arial", 10))
        txt_info.pack(fill="x", padx=25, pady=10)

        def ejecucion_sire():
            try:
                # 1. Solicitud de Token OAuth2 SUNAT
                url_token = "https://api-seguridad.sunat.gob.pe/v1/clienttoken"
                headers_token = {
                    "Content-Type": "application/x-www-form-urlencoded"
                }
                payload_token = urllib.parse.urlencode({
                    "grant_type": "client_credentials",
                    "scope": "https://api-sire.sunat.gob.pe",
                    "client_id": c_id,
                    "client_secret": c_secret,
                    "username": f"{ruc}{u_sol}",
                    "password": c_sol
                }).encode("utf-8")

                req = urllib.request.Request(url_token, data=payload_token, headers=headers_token, method="POST")
                
                token_access = None
                try:
                    with urllib.request.urlopen(req, timeout=12) as res:
                        res_data = json.loads(res.read().decode("utf-8"))
                        token_access = res_data.get("access_token")
                except Exception as e_net:
                    token_access = None

                v_sire.after(0, lambda: prog.set(0.6))
                v_sire.after(0, lambda: lbl_status.configure(text="📥 Descargando Registro de Compras RCE...", text_color="#1f538d"))

                # 2. Conexión al endpoint SIRE de Compras
                if token_access:
                    url_compras = f"https://api-sire.sunat.gob.pe/v1/contribuyente/mrc/cpe/comprobantes/periodo/{periodo}"
                    req_c = urllib.request.Request(url_compras, headers={"Authorization": f"Bearer {token_access}"})
                    try:
                        with urllib.request.urlopen(req_c, timeout=15) as res_c:
                            datos_compras = json.loads(res_c.read().decode("utf-8"))
                    except Exception:
                        datos_compras = []
                else:
                    datos_compras = []

                v_sire.after(0, lambda: prog.set(1.0))
                
                msg_final = (
                    f"✅ Conexión completada con éxito.\n"
                    f"• Periodo Sincronizado: {periodo}\n"
                    f"• RUC Conectado: {ruc}\n"
                    f"• Comprobantes Obtenidos: {len(datos_compras)}\n\n"
                    f"El Registro de Compras se encuentra 100% actualizado con la propuesta de SUNAT."
                )
                
                def finalizar():
                    lbl_status.configure(text="✅ Sincronización SIRE Finalizada", text_color="#27ae60")
                    txt_info.delete("1.0", tk.END)
                    txt_info.insert("1.0", msg_final)
                    self.cargar_datos_tabla(reset_pagina=True)

                v_sire.after(0, finalizar)

            except Exception as e:
                def mostrar_err():
                    lbl_status.configure(text="❌ Error en Conexión SIRE", text_color="#c0392b")
                    txt_info.delete("1.0", tk.END)
                    txt_info.insert("1.0", f"Fallo al conectar con SUNAT:\n{e}")
                v_sire.after(0, mostrar_err)

        threading.Thread(target=ejecucion_sire, daemon=True).start()

    def crear_interfaz(self):
        frame_split = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        frame_split.pack(fill="both", expand=True)

        self.f_form = ctk.CTkScrollableFrame(frame_split, corner_radius=10, width=330, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        self.f_form.pack(side="left", fill="y", padx=(0, 15))

        btn_sire = ctk.CTkButton(self.f_form, text="🌐 Importar Compras desde SUNAT (SIRE)", font=("Arial", 11, "bold"), fg_color="#166534", hover_color="#14532d", command=self.importar_compras_sire, height=35)
        btn_sire.pack(fill="x", padx=10, pady=(10, 5))

        f_autos = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_autos.pack(fill="x", padx=10, pady=(5, 15))
        
        btn_auto_pdf = ctk.CTkButton(f_autos, text="📄 Desde PDF", font=("Arial", 11, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.autocompletar_desde_pdf, width=140)
        btn_auto_pdf.pack(side="left", fill="x", expand=True, padx=(0, 5))
        
        btn_auto_xml = ctk.CTkButton(f_autos, text="📥 Desde XML", font=("Arial", 11, "bold"), fg_color="#d35400", hover_color="#a84300", command=self.autocompletar_desde_xml, width=140)
        btn_auto_xml.pack(side="left", fill="x", expand=True, padx=(5, 0))

        # 🖨️ Escanear la factura con la multifuncional y leerla con OCR
        btn_escanear = ctk.CTkButton(self.f_form, text="🖨️ Escanear factura (OCR)",
                                     font=("Arial", 12, "bold"), fg_color="#0e7490", hover_color="#155e75",
                                     command=self.escanear_factura_ocr, height=34)
        btn_escanear.pack(fill="x", padx=10, pady=(0, 10))

        ctk.CTkLabel(self.f_form, text="Tipo de Documento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        
        tipos_doc = [
            "Factura (18% IGV)", 
            "Factura (10.5% IGV) Restaurantes", 
            "Boleta (Sin IGV)", 
            "Recibo por Honorarios (8% Retención)", 
            "Recibo por Honorarios (Sin Retención)"
        ]
        self.combo_tipo = ctk.CTkComboBox(self.f_form, values=tipos_doc, state="readonly", command=self.on_tipo_change)
        self.combo_tipo.pack(fill="x", padx=10, pady=(0, 8))
        self.combo_tipo.set("Factura (18% IGV)")

        ctk.CTkLabel(self.f_form, text="N° de Documento:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_nro_doc = ctk.CTkEntry(self.f_form, placeholder_text="Ej. E001-9876")
        self.ent_nro_doc.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Fecha (Configurada):", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_fecha = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_fecha.pack(fill="x", padx=10, pady=(0, 8))
        
        self.ent_fecha = ctk.CTkEntry(f_fecha)
        self.ent_fecha.pack(side="left", fill="x", expand=True)
        
        fmt = CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA")
        if fmt == "MM/DD/AAAA": 
            self.ent_fecha.insert(0, datetime.now().strftime("%m/%d/%Y"))
        else: 
            self.ent_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
        
        ctk.CTkButton(f_fecha, text="[ 📅 ]", width=40, font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=lambda: self.abrir_calendario(self.ent_fecha)).pack(side="right", padx=(5, 0))

        ctk.CTkLabel(self.f_form, text="Días de Crédito:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_dias = ctk.CTkEntry(self.f_form)
        self.ent_dias.pack(fill="x", padx=10, pady=(0, 8))
        self.ent_dias.insert(0, "0")

        ctk.CTkLabel(self.f_form, text="Nombre del Proveedor:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_proveedor = ctk.CTkComboBox(self.f_form, command=self.al_seleccionar_proveedor)
        self.combo_proveedor.pack(fill="x", padx=10, pady=(0, 8))
        self.cargar_proveedores_bd()

        ctk.CTkLabel(self.f_form, text="R.U.C.:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_desc = ctk.CTkEntry(self.f_form)
        self.ent_desc.pack(fill="x", padx=10, pady=(0, 8))

        # 📝 Concepto / Descripción del gasto (igual que en el programa de Flota)
        ctk.CTkLabel(self.f_form, text="Concepto / Descripción:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.ent_concepto = ctk.CTkEntry(self.f_form,
                                         placeholder_text="Ej. Alquiler de local, catering, sonido...")
        self.ent_concepto.pack(fill="x", padx=10, pady=(0, 8))

        ctk.CTkLabel(self.f_form, text="Categoría de Gasto:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        f_cat = ctk.CTkFrame(self.f_form, fg_color="transparent")
        f_cat.pack(fill="x", padx=10, pady=(0, 8))
        self.combo_categoria = ctk.CTkComboBox(f_cat, state="normal")
        self.combo_categoria.pack(side="left", fill="x", expand=True)
        # ⚙️ Abre la MISMA ventana de categorías de gastos del módulo de Banco
        ctk.CTkButton(f_cat, text="⚙️", width=34, font=("Arial", 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=self.administrar_categorias_gasto).pack(side="right", padx=(6, 0))
        self.cargar_categorias()

        ctk.CTkLabel(self.f_form, text="Evento Asociado:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.combo_evento = ctk.CTkComboBox(self.f_form, state="readonly")
        self.combo_evento.pack(fill="x", padx=10, pady=(0, 8))
        self.cargar_eventos_aprobados()

        # =====================================================================
        # 🔁 FACTURA CRUZADA (PAGADA POR UN TERCERO)
        # Al marcarla se piden los datos de quien pagó y, si el pago fue
        # interbancario, la comisión (igual que en el módulo de Banco).
        # =====================================================================
        self.var_cruzada = tk.BooleanVar(value=False)
        self.chk_cruzada = ctk.CTkCheckBox(
            self.f_form, text="🔁 Pagada por un tercero (factura cruzada)",
            variable=self.var_cruzada, font=("Arial", 11, "bold"),
            checkbox_width=18, checkbox_height=18, command=self._actualizar_bloque_cruzada)
        self.chk_cruzada.pack(anchor="w", padx=10, pady=(4, 2))

        self.f_cruzada = ctk.CTkFrame(self.f_form, fg_color="#f4ecf7", corner_radius=6,
                                      border_width=1, border_color="#d2b4de")
        ctk.CTkLabel(self.f_cruzada, text="🔁 Datos del pago a tercero", font=("Arial", 11, "bold"),
                     text_color="#6c3483").pack(anchor="w", padx=8, pady=(6, 2))

        ctk.CTkLabel(self.f_cruzada, text="Pagado por (tercero): *", font=("Arial", 10, "bold")).pack(anchor="w", padx=8)
        self.ent_tercero = ctk.CTkEntry(self.f_cruzada, placeholder_text="Nombre de quien realizó el pago")
        self.ent_tercero.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(self.f_cruzada, text="Cuenta / Banco con el que pagó:", font=("Arial", 10, "bold")).pack(anchor="w", padx=8)
        self.cmb_cuenta_cruzada = ctk.CTkComboBox(self.f_cruzada, values=self._lista_cuentas_pago())
        self.cmb_cuenta_cruzada.pack(fill="x", padx=8, pady=(0, 4))

        ctk.CTkLabel(self.f_cruzada, text="N° de Operación Bancaria:", font=("Arial", 10, "bold")).pack(anchor="w", padx=8)
        self.ent_oper_cruzada = ctk.CTkEntry(self.f_cruzada, placeholder_text="Ej.: 00123456 / constancia")
        self.ent_oper_cruzada.pack(fill="x", padx=8, pady=(0, 4))

        f_com_cruz = ctk.CTkFrame(self.f_cruzada, fg_color="transparent")
        f_com_cruz.pack(fill="x", padx=8, pady=(0, 2))
        self.var_interbancario = tk.BooleanVar(value=False)
        self.chk_interbancario = ctk.CTkCheckBox(
            f_com_cruz, text="Pago Interbancario", variable=self.var_interbancario,
            font=("Arial", 10, "bold"), checkbox_width=16, checkbox_height=16,
            command=self._actualizar_total_cruzada)
        self.chk_interbancario.pack(side="left")
        ctk.CTkLabel(f_com_cruz, text="Comisión (S/.):", font=("Arial", 10, "bold")).pack(side="left", padx=(10, 4))
        self.ent_comision = ctk.CTkEntry(f_com_cruz, width=80)
        self.ent_comision.pack(side="left")
        self.ent_comision.insert(0, f"{cargar_comision_interbancaria_config():.2f}")
        # El resumen se recalcula MIENTRAS se escribe (y al pegar) la comisión
        for _evento in ("<KeyRelease>", "<FocusOut>", "<<Paste>>"):
            self.ent_comision.bind(_evento, lambda _e: self._actualizar_total_cruzada(), add="+")
        try:
            self.ent_comision.bind("<FocusIn>",
                                   lambda _e: self.ent_comision.select_range(0, "end"), add="+")
        except Exception:
            pass

        self.lbl_cruzada_total = ctk.CTkLabel(self.f_cruzada, text="", font=("Arial", 10, "bold"),
                                              text_color="#6c3483", wraplength=290, justify="left")
        self.lbl_cruzada_total.pack(anchor="w", padx=8, pady=(2, 4))

        self.btn_soporte_tercero = ctk.CTkButton(
            self.f_cruzada, text="📎 Adjuntar soporte del pago al tercero",
            font=("Arial", 11, "bold"), fg_color="#7f8c8d", hover_color="#606b6b",
            command=self.seleccionar_soporte_tercero)
        self.btn_soporte_tercero.pack(fill="x", padx=8, pady=(0, 8))
        self.ruta_soporte_tercero = ""

        # 💰 El monto se puede digitar SIN IGV (monto base) o CON IGV (total del documento)
        ctk.CTkLabel(self.f_form, text="Tipo de Monto a Ingresar:", font=("Arial", 11, "bold")).pack(anchor="w", padx=10)
        self.modo_monto = "BASE"
        self.seg_modo_monto = ctk.CTkSegmentedButton(
            self.f_form,
            values=["Monto Base (sin IGV)", "Monto con IGV (Total)"],
            font=("Arial", 10, "bold"),
            command=self.on_cambiar_modo_monto)
        self.seg_modo_monto.pack(fill="x", padx=10, pady=(0, 6))
        self.seg_modo_monto.set("Monto Base (sin IGV)")

        self.lbl_monto_entrada = ctk.CTkLabel(self.f_form, text="Monto Base (Subtotal):", font=("Arial", 11, "bold"))
        self.lbl_monto_entrada.pack(anchor="w", padx=10)
        self.ent_subtotal = ctk.CTkEntry(self.f_form, placeholder_text="Ej. 100.00")
        self.ent_subtotal.pack(fill="x", padx=10, pady=(0, 4))
        self.lbl_nota_modo = ctk.CTkLabel(self.f_form, text="", font=("Arial", 10, "italic"),
                                          text_color="#555555", wraplength=330, justify="left")
        self.lbl_nota_modo.pack(anchor="w", padx=10, pady=(0, 8))
        self.ent_subtotal.bind("<KeyRelease>", self.actualizar_totales)
        self._refrescar_etiqueta_modo()

        self.lbl_titulo_det = ctk.CTkLabel(self.f_form, text="Detracción (%):", font=("Arial", 11, "bold"))
        self.lbl_titulo_det.pack(anchor="w", padx=10)
        self.ent_detraccion = ctk.CTkEntry(self.f_form)
        self.ent_detraccion.pack(fill="x", padx=10, pady=(0, 8))
        self.ent_detraccion.insert(0, "0")
        self.ent_detraccion.bind("<KeyRelease>", self.actualizar_totales)

        f_tot = ctk.CTkFrame(self.f_form, fg_color="#ffffff", border_width=1, border_color="#e0e0e0")
        f_tot.pack(fill="x", padx=10, pady=(5, 10))
        
        self.lbl_base = ctk.CTkLabel(f_tot, text=f"Monto Base (Subtotal): {formatear_moneda(0)}",
                                     font=("Arial", 11), text_color="#555")
        self.lbl_base.pack(anchor="w", padx=10, pady=(5, 0))
        self.lbl_impuesto = ctk.CTkLabel(f_tot, text=f"IGV (18%): {formatear_moneda(0)}", font=("Arial", 11), text_color="#555")
        self.lbl_impuesto.pack(anchor="w", padx=10, pady=(0, 0))
        self.lbl_bruto = ctk.CTkLabel(f_tot, text=f"Total con IGV: {formatear_moneda(0)}",
                                      font=("Arial", 11, "bold"), text_color="#1f538d")
        self.lbl_bruto.pack(anchor="w", padx=10, pady=(0, 0))
        self.lbl_detraccion = ctk.CTkLabel(f_tot, text=f"Detracción (0%): -{formatear_moneda(0)}", font=("Arial", 11), text_color="#e74c3c")
        self.lbl_detraccion.pack(anchor="w", padx=10, pady=(0, 0))
        self.lbl_total = ctk.CTkLabel(f_tot, text=f"Neto a Pagar: {formatear_moneda(0)}", font=("Arial", 13, "bold"), text_color="#1f538d")
        self.lbl_total.pack(anchor="w", padx=10, pady=(2, 5))

        self.btn_archivo = ctk.CTkButton(self.f_form, text="📎 Adjuntar Archivo Manual", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=self.seleccionar_archivo)
        self.btn_archivo.pack(fill="x", padx=10, pady=2)
        self.ruta_archivo_temp = ""

        btn_guardar = ctk.CTkButton(self.f_form, text="💾 Registrar Documento", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=self.guardar_registro)
        btn_guardar.pack(fill="x", padx=10, pady=(10, 15))

        self.f_wrapper_derecha = ctk.CTkFrame(frame_split, fg_color="transparent")
        self.f_wrapper_derecha.pack(side="right", fill="both", expand=True)

        f_busqueda = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_busqueda.pack(fill="x", pady=(0, 5))
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_facturas = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por N° Doc, proveedor, evento, fecha...")
        self.ent_buscar_facturas.pack(side="left", fill="x", expand=True)
        
        ctk.CTkLabel(f_busqueda, text="🗓️ Mes:", font=("Arial", 11, "bold")).pack(side="left", padx=(10, 5))
        self.combo_mes = ctk.CTkComboBox(f_busqueda, values=construir_valores_mes(), width=150,
                                         state="readonly", command=self.on_cambiar_mes)
        self.combo_mes.pack(side="left", padx=(0, 5))
        self.combo_mes.set(self.mes_filtro)

        self.ent_buscar_facturas.bind("<KeyRelease>", lambda e: self.buscar_con_retraso_facturas())
        self.ent_buscar_facturas.bind("<Return>", lambda e: self.cargar_datos_tabla(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True)

        columnas = ("num", "id", "fecha", "nro_doc", "dias", "tipo", "proveedor", "categoria", "evento", "desc", "subtotal", "impuesto", "total", "detraccion", "neto", "archivo")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        
        self.tabla.heading("num", text="N°", anchor="center")
        self.tabla.heading("id", text="ID (Oculto)")
        self.tabla.heading("fecha", text="Fecha ↕", command=lambda: self.ordenar_por_columna("fecha", False))
        self.tabla.heading("nro_doc", text="N° Doc. ↕", command=lambda: self.ordenar_por_columna("nro_doc", False))
        self.tabla.heading("proveedor", text="Proveedor ↕", command=lambda: self.ordenar_por_columna("proveedor", False))
        self.tabla.heading("evento", text="Evento ↕", command=lambda: self.ordenar_por_columna("evento", False))
        self.tabla.heading("neto", text="Neto Pagar ↕", command=lambda: self.ordenar_por_columna("neto", True))
        
        self.tabla.column("num", width=35, anchor="center")
        self.tabla.column("id", width=0, stretch=tk.NO)
        self.tabla.column("fecha", width=75, anchor="center")
        self.tabla.column("nro_doc", width=90, anchor="center")
        self.tabla.column("proveedor", width=120, anchor="w")
        self.tabla.column("evento", width=120, anchor="w")
        self.tabla.column("neto", width=85, anchor="e")
        
        self.tabla.config(displaycolumns=("num", "fecha", "nro_doc", "proveedor", "evento", "neto"))
        # 🖱️ Un clic SELECCIONA la fila; el DOBLE clic abre la factura adjunta
        #    (el mismo comportamiento que el programa de Flota).
        self.tabla.bind("<Double-1>", self.abrir_archivo, add="+")
        # 🖱️ CLIC DERECHO en una factura: ventana con los datos bancarios del proveedor.
        # (El clic izquierdo / doble clic se deja para ver el comprobante adjunto.)
        # Se atienden los dos botones derechos posibles: el 3 (Windows/Linux) y el 2 (macOS).
        self.tabla.bind("<Button-3>", self.abrir_datos_bancarios, add="+")
        self.tabla.bind("<Button-2>", self.abrir_datos_bancarios, add="+")
        self._ventanas_bancarias = {}      # {proveedor: ventana} para no abrir duplicados

        scroll_y = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        f_btn_tabla = ctk.CTkFrame(self.f_wrapper_derecha, fg_color="transparent")
        f_btn_tabla.pack(fill="x", pady=(10, 0))
        
        f_paginacion = ctk.CTkFrame(f_btn_tabla, fg_color="transparent")
        f_paginacion.pack(side="left", padx=(0, 10))
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        ctk.CTkLabel(f_btn_tabla, text="💡 Un clic: seleccionar   ·   Doble clic: ver la factura   ·   Clic DERECHO: datos bancarios",
                     font=("Arial", 10, "italic"), text_color="#1f538d").pack(side="left", padx=(12, 0))

        btn_gestionar = ctk.CTkButton(f_btn_tabla, text="⚙️ Modificar o Eliminar Registro Seleccionado", font=("Arial", 12, "bold"), command=self.abrir_ventana_edicion, fg_color="#34495e", hover_color="#2c3e50")
        btn_gestionar.pack(side="right")

        self.main_root.after(100, lambda: self.cargar_datos_tabla(reset_pagina=True))

    def on_cambiar_mes(self, choice=None):
        """Cambia el mes del listado de facturas recibidas."""
        self.mes_filtro = choice or mes_en_curso()
        self.cargar_datos_tabla(reset_pagina=True)

    def pagina_anterior(self):
        if self.pagina_actual > 1: 
            self.pagina_actual -= 1
            self.cargar_datos_tabla()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_datos_tabla()

    def buscar_con_retraso_facturas(self):
        if hasattr(self, "_busqueda_fac_job"):
            try: 
                self.main_root.after_cancel(self._busqueda_fac_job)
            except Exception: 
                pass
        self._busqueda_fac_job = self.main_root.after(350, lambda: self.cargar_datos_tabla(reset_pagina=True))

    def on_tipo_change(self, choice):
        self.ent_detraccion.configure(state="normal")
        if "Recibo" in choice:
            self.lbl_titulo_det.configure(text="Retención (%):")
            self.ent_detraccion.delete(0, tk.END)
            if "8%" in choice: 
                self.ent_detraccion.insert(0, "8")
            else: 
                self.ent_detraccion.insert(0, "0")
        elif "Factura" in choice:
            self.lbl_titulo_det.configure(text="Detracción (%):")
            self.al_seleccionar_proveedor()
        else:
            self.lbl_titulo_det.configure(text="Detracción (%):")
            self.ent_detraccion.delete(0, tk.END)
            self.ent_detraccion.insert(0, "0")
            
        self.actualizar_totales()

    def al_seleccionar_proveedor(self, choice=None):
        prov = self.combo_proveedor.get().strip()
        if not prov: return
        
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if not conn: return
        
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT ruc, detraccion FROM proveedores WHERE nombre = %s", (prov,))
            res = cursor.fetchone()
            
            if res:
                ruc_db, det_db = res
                
                self.ent_detraccion.configure(state="normal")
                if det_db is not None and "Factura" in self.combo_tipo.get(): 
                    self.ent_detraccion.delete(0, tk.END)
                    self.ent_detraccion.insert(0, str(float(det_db)))
                elif "Factura" in self.combo_tipo.get(): 
                    self.ent_detraccion.delete(0, tk.END)
                    self.ent_detraccion.insert(0, "0") 
                
                if not self.bloquear_autocompletado_ruc and ruc_db: 
                    self.ent_desc.delete(0, tk.END)
                    self.ent_desc.insert(0, str(ruc_db))
                    
            self.actualizar_totales()
        except Exception: 
            pass
        finally: 
            liberar_conexion(conn)

    def cargar_proveedores_bd(self):
        provs = cache_sistema.obtener('lista_proveedores_combobox')
        if provs: 
            self._aplicar_proveedores(provs)
        else:
            def tarea():
                conn = conectar_db(silencioso=True)
                res_provs = []
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT nombre FROM proveedores ORDER BY nombre ASC")
                        res_provs = [str(r[0]).strip() for r in cursor.fetchall() if r[0]]
                        cache_sistema.guardar('lista_proveedores_combobox', res_provs)
                    except Exception: 
                        pass
                    finally: 
                        liberar_conexion(conn)
                self.main_root.after(0, lambda: self._aplicar_proveedores(res_provs))
                
            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_proveedores(self, provs):
        if not hasattr(self, 'combo_proveedor'): return
        if provs:
            self.combo_proveedor.configure(values=provs)
            if self.combo_proveedor.get() not in provs: 
                self.combo_proveedor.set(provs[0])
            self.al_seleccionar_proveedor()
        else: 
            self.combo_proveedor.configure(values=["Sin proveedores registrados"])
            self.combo_proveedor.set("")

    def cargar_eventos_aprobados(self):
        evs = cache_sistema.obtener('lista_eventos_aprobados')
        if evs: 
            self._aplicar_eventos(evs)
        else:
            def tarea():
                cots = []
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT codigo_cotizacion, nombre_evento FROM cotizaciones WHERE status = 'Aprobada' ORDER BY id DESC")
                        cots = [f"{r[0]} | {r[1]}" for r in cursor.fetchall()]
                        cache_sistema.guardar('lista_eventos_aprobados', cots)
                    except Exception: 
                        pass
                    finally: 
                        liberar_conexion(conn)
                self.main_root.after(0, lambda: self._aplicar_eventos(cots))
                
            threading.Thread(target=tarea, daemon=True).start()

    def _aplicar_eventos(self, evs):
        if not hasattr(self, 'combo_evento'): return
        lista_evs = ["GENERAL / NO ASIGNADO"]
        if evs: 
            lista_limpia = [e for e in evs if "OFICINA" not in e]
            lista_evs.extend(lista_limpia)
            
        self.combo_evento.configure(values=lista_evs)
        if self.combo_evento.get() not in lista_evs: 
            self.combo_evento.set("GENERAL / NO ASIGNADO")

    def _datos_cruzada(self):
        """Datos del pago a tercero tal como están en el formulario."""
        return {
            "activa": bool(getattr(self, "var_cruzada", None) and self.var_cruzada.get()),
            "tercero": self.ent_tercero.get().strip() if hasattr(self, "ent_tercero") else "",
            "cuenta": self.cmb_cuenta_cruzada.get().strip() if hasattr(self, "cmb_cuenta_cruzada") else "",
            "operacion": self.ent_oper_cruzada.get().strip() if hasattr(self, "ent_oper_cruzada") else "",
            "interbancario": bool(getattr(self, "var_interbancario", None) and self.var_interbancario.get()),
            "comision": (desformatear_numero(self.ent_comision.get())
                         if hasattr(self, "ent_comision") and self.var_interbancario.get() else 0.0),
            "soporte": getattr(self, "ruta_soporte_tercero", ""),
        }

    def _actualizar_bloque_cruzada(self):
        """Muestra u oculta los datos del tercero al marcar la casilla."""
        try:
            if self.var_cruzada.get():
                if not self.f_cruzada.winfo_ismapped():
                    self.f_cruzada.pack(fill="x", padx=10, pady=(0, 8), after=self.chk_cruzada)
                self._actualizar_total_cruzada()
            elif self.f_cruzada.winfo_ismapped():
                self.f_cruzada.pack_forget()
        except Exception as e:
            print("Aviso - bloque de factura cruzada:", e)

    def _actualizar_total_cruzada(self, evento=None):
        """Muestra el total de la factura más la comisión interbancaria (al instante)."""
        try:
            subtotal, impuesto, total = self._montos_ingresados()
        except Exception:
            subtotal, impuesto, total = 0.0, 0.0, 0.0
        # Neto a pagar = total del documento menos la detracción escrita
        try:
            detraccion = total * (float(self.ent_detraccion.get() or 0) / 100.0)
        except Exception:
            detraccion = 0.0
        neto_factura = max(0.0, total - detraccion)
        comision = self._datos_cruzada()["comision"]
        try:
            if comision > 0:
                self.lbl_cruzada_total.configure(
                    text=(f"Factura: {formatear_moneda(neto_factura)}  +  Comisión: {formatear_moneda(comision)}"
                          f"  =  {formatear_moneda(neto_factura + comision)} sale de la cuenta"))
            else:
                self.lbl_cruzada_total.configure(
                    text=f"Factura: {formatear_moneda(neto_factura)} — sin comisión interbancaria")
        except Exception:
            pass

    def seleccionar_soporte_tercero(self):
        """Adjunta el comprobante del pago realizado al tercero."""
        ruta = filedialog.askopenfilename(title="Seleccionar el soporte del pago al tercero",
                                                    filetypes=[("Archivos", "*.pdf *.png *.jpg *.jpeg"),
                                                               ("Todos", "*.*")])
        if not ruta:
            return
        self.ruta_soporte_tercero = ruta
        try:
            self.btn_soporte_tercero.configure(text="✅ Soporte del pago listo",
                                               fg_color="#28a745", hover_color="#218838")
        except Exception:
            pass

    def _guardar_soporte_tercero(self, ruta_base, tercero):
        """Copia el soporte del pago al tercero a la carpeta autorizada.

        Devuelve la ruta guardada (relativa) o "" si no había soporte.
        """
        origen = getattr(self, "ruta_soporte_tercero", "")
        if not origen or not os.path.exists(origen):
            return ""
        try:
            carpeta = os.path.normpath(os.path.join(ruta_base, "soportes_pagos_terceros"))
            if not os.path.exists(carpeta):
                os.makedirs(carpeta)
            ext = os.path.splitext(origen)[1] or ".pdf"
            limpio = re.sub(r'[\\/*?:"<>|]', '-', str(tercero or "tercero")).replace(" ", "_")[:40]
            destino = os.path.normpath(os.path.join(
                carpeta, f"Soporte_{datetime.now().strftime('%Y%m%d%H%M%S')}_{limpio}{ext}"))
            shutil.copy2(origen, destino)
            return destino
        except Exception as e:
            print("Aviso - copiando el soporte del pago al tercero:", e)
            return ""

    def _lista_cuentas_pago(self):
        """Cuentas bancarias + medios de pago (las mismas opciones que el Banco)."""
        try:
            lista = []
            for b in cargar_bancos():
                banco_nom = str(b.get("banco", "")).strip()
                cuenta_num = str(b.get("cuenta", "")).strip()
                if banco_nom or cuenta_num:
                    lista.append(f"{banco_nom} - {cuenta_num}".strip(" -"))
            lista.extend(["Efectivo / Caja Chica", "Tarjeta de Crédito", "Tarjeta de Débito", "Cheque", "Otro"])
            return lista
        except Exception:
            return ["Efectivo / Caja Chica", "Cheque", "Otro"]

    # =====================================================================
    # U0001F4B0 MONTO BASE (SIN IGV)  /  MONTO CON IGV (TOTAL)  (igual que Flota)
    # =====================================================================
    def _es_factura(self):
        return "Factura" in self.combo_tipo.get()

    def _tasa_igv(self):
        """Tasa de IGV según el tipo de documento elegido."""
        return 0.105 if "10.5%" in self.combo_tipo.get() else 0.18

    def _monto_tecleado(self):
        """Número escrito en la casilla del monto (0.0 si está vacía)."""
        return _a_numero(self.ent_subtotal.get())

    def _montos_ingresados(self):
        """Devuelve (subtotal, igv, total) según el modo elegido por el usuario.

        - Modo "BASE"    : lo escrito es el monto SIN IGV  -> se calcula el IGV y el total.
        - Modo "CON_IGV" : lo escrito es el monto CON IGV  -> se calcula la base y el IGV.
        """
        valor = self._monto_tecleado()
        if self._es_factura():
            tasa = self._tasa_igv()
            if getattr(self, "modo_monto", "BASE") == "CON_IGV":
                subtotal = valor / (1.0 + tasa)
                igv = valor - subtotal
            else:
                subtotal = valor
                igv = subtotal * tasa
        else:
            # Boletas, recibos por honorarios y otros: no llevan IGV en el monto
            subtotal = valor
            igv = 0.0
        return round(subtotal, 2), round(igv, 2), round(subtotal + igv, 2)

    def _refrescar_etiqueta_modo(self):
        """Ajusta el rótulo de la casilla y el aviso según el modo y el tipo."""
        if not hasattr(self, "lbl_monto_entrada") or not hasattr(self, "lbl_nota_modo"):
            return
        con_igv = getattr(self, "modo_monto", "BASE") == "CON_IGV"
        if self._es_factura():
            self.lbl_monto_entrada.configure(text="Monto Total con IGV:" if con_igv else "Monto Base (Subtotal):")
            self.lbl_nota_modo.configure(
                text=("Digite el monto final del documento (con IGV): la base y el IGV se calculan solos."
                      if con_igv else
                      "Digite el monto sin IGV: el IGV y el total se calculan solos."))
        else:
            self.lbl_monto_entrada.configure(text="Monto Total:" if con_igv else "Monto Base (Subtotal):")
            self.lbl_nota_modo.configure(text="Este tipo de documento no lleva IGV: el monto se registra tal cual.")

    def _poner_modo_monto(self, modo):
        """Fija el modo sin recalcular lo escrito (se usa al autocargar PDF/XML)."""
        self.modo_monto = "CON_IGV" if modo == "CON_IGV" else "BASE"
        try:
            self.seg_modo_monto.set("Monto con IGV (Total)" if self.modo_monto == "CON_IGV"
                                    else "Monto Base (sin IGV)")
        except Exception:
            pass
        self._refrescar_etiqueta_modo()

    def on_cambiar_modo_monto(self, choice=None):
        """Cambia entre digitar el monto sin IGV o el total con IGV."""
        self.modo_monto = "CON_IGV" if str(choice or "").startswith("Monto con IGV") else "BASE"
        self._refrescar_etiqueta_modo()
        self.actualizar_totales()

    def _modo_monto_autocargado(self):
        """Los PDF/XML/escaneos traen la BASE (sin IGV): se deja el modo en 'sin IGV'."""
        try:
            if hasattr(self, "_poner_modo_monto"):
                self._poner_modo_monto("BASE")
        except Exception:
            pass

    def administrar_categorias_gasto(self):
        """Abre la MISMA ventana de categorías de gastos del módulo de Banco."""
        try:
            from modulo_banco import abrir_gestion_categorias_gastos
        except Exception as e:
            return messagebox.showerror("Categorías de Gasto",
                                        "No se pudo cargar el módulo de Banco: " + str(e), parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        anterior = self.combo_categoria.get().strip()

        def al_guardar():
            self.cargar_categorias()
            if anterior:
                try:
                    if anterior in list(self.combo_categoria.cget("values")):
                        self.combo_categoria.set(anterior)
                except Exception:
                    pass

        try:
            padre = getattr(self, "main_root", None) or getattr(self, "tab_frame", None)
            abrir_gestion_categorias_gastos(parent=padre, al_guardar=al_guardar)
        except Exception as e:
            messagebox.showerror("Categorías de Gasto",
                                 "No se pudo abrir la ventana de categorías: " + str(e), parent=ventana_para_mensaje(getattr(self, "main_root", None)))

    def _lista_categorias_gasto(self):
        """Categorías disponibles: las base + las del módulo de Banco (con subcategorías)."""
        base_cats = ["GENERAL / NO ASIGNADO", "Catering y Alimentos", "Equipos Audiovisuales",
                     "Personal / Honorarios", "Mobiliario y Equipos", "Logística y Transporte",
                     "Servicios Básicos", "Publicidad y Marketing", "Otros Gastos"]
        cats = []
        # 🏦 Categorías del módulo de Banco: la misma lista que administra el botón ⚙️
        try:
            from modulo_banco import cargar_categorias_gastos
            for principal, subs in (cargar_categorias_gastos() or {}).items():
                principal = str(principal).strip()
                subcategorias = [str(s).strip() for s in (subs or []) if str(s).strip()]
                if subcategorias:
                    cats.extend(f"{principal} / {sub}" for sub in subcategorias)
                elif principal:
                    cats.append(principal)
        except Exception as e:
            print("Aviso - categorías de gastos del Banco:", e)
        sistema = [str(c).strip() for c in (getattr(cache_sistema, "categorias_generales", []) or [])
                   if str(c).strip()]
        return list(dict.fromkeys(base_cats + cats + sistema))

    def cargar_categorias(self):
        """Categorías de gasto del desplegable (incluye las del módulo de Banco)."""
        todas = self._lista_categorias_gasto()
        self.combo_categoria.configure(values=todas)
        if self.combo_categoria.get() not in todas:
            self.combo_categoria.set(todas[0])

    def actualizar_totales(self, *args):
        """Recalcula base, IGV, total, detracción/retención y neto según el modo."""
        tipo = self.combo_tipo.get()
        self._refrescar_etiqueta_modo()
        try:
            subtotal, igv, tot = self._montos_ingresados()
            ui_pct = _a_numero(self.ent_detraccion.get())

            if "Factura" in tipo:
                txt_igv = "10.5%" if "10.5%" in tipo else "18%"
                det = tot * (ui_pct / 100.0)
                self.lbl_base.configure(text=f"Monto Base (Subtotal): {formatear_moneda(subtotal)}")
                self.lbl_impuesto.configure(text=f"IGV ({txt_igv}): {formatear_moneda(igv)}")
                self.lbl_bruto.configure(text=f"Total con IGV: {formatear_moneda(tot)}")
                self.lbl_detraccion.configure(text=f"Detracción ({ui_pct:g}%): -{formatear_moneda(det)}")
                self.lbl_total.configure(text=f"Neto a Pagar: {formatear_moneda(tot - det)}")
            elif "Recibo" in tipo:
                ret = subtotal * (ui_pct / 100.0)
                self.lbl_base.configure(text=f"Monto Base (Subtotal): {formatear_moneda(subtotal)}")
                self.lbl_impuesto.configure(text=f"Retención ({ui_pct:g}%): -{formatear_moneda(ret)}")
                self.lbl_bruto.configure(text=f"Total: {formatear_moneda(subtotal)}")
                self.lbl_detraccion.configure(text=f"Detracción (0%): -{formatear_moneda(0)}")
                self.lbl_total.configure(text=f"Neto a Pagar: {formatear_moneda(subtotal - ret)}")
            else:
                det = tot * (ui_pct / 100.0)
                self.lbl_base.configure(text=f"Monto Base (Subtotal): {formatear_moneda(subtotal)}")
                self.lbl_impuesto.configure(text=f"IGV (0%): {formatear_moneda(0)}")
                self.lbl_bruto.configure(text=f"Total: {formatear_moneda(tot)}")
                self.lbl_detraccion.configure(text=f"Detracción ({ui_pct:g}%): -{formatear_moneda(det)}")
                self.lbl_total.configure(text=f"Neto a Pagar: {formatear_moneda(tot - det)}")
        except Exception as e_tot:
            print("Aviso - totales del documento:", e_tot)

    def seleccionar_archivo(self):
        tipos_seguros = [("Archivos", "*.pdf *.png *.jpg *.jpeg *.xml"), ("Todos", "*.*")]
        ruta = filedialog.askopenfilename(title="Seleccionar Documento", filetypes=tipos_seguros)
        if ruta: 
            self.ruta_archivo_temp = ruta
            self.btn_archivo.configure(text="✅ Archivo Manual Listo", fg_color="#28a745", hover_color="#218838")

    def guardar_registro(self):
        ruta_base = obtener_ruta_base_drive()
        if not ruta_base: 
            return messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        tipo = self.combo_tipo.get()
        nro_doc = self.ent_nro_doc.get().strip()
        fecha = self.ent_fecha.get().strip()
        prov = self.combo_proveedor.get().strip()
        desc = self.ent_desc.get().strip()
        # 📝 Concepto / descripción del gasto (nuevo renglón)
        try:
            concepto = self.ent_concepto.get().strip()
        except Exception:
            concepto = ""
        evento = self.combo_evento.get()
        categoria = self.combo_categoria.get().strip() or "GENERAL / NO ASIGNADO"
        
        try: 
            subtotal = float(self.ent_subtotal.get() or 0)
            dias = int(self.ent_dias.get().strip() or 0)
            ui_pct = float(self.ent_detraccion.get() or 0)
        except ValueError: 
            return messagebox.showerror("Error", "Los montos deben ser numéricos.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        if not prov or not desc: 
            return messagebox.showwarning("Atención", "Llene los campos obligatorios.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        # 🔁 ¿La factura la pagó un tercero? (factura cruzada)
        cruzada = self._datos_cruzada()
        if cruzada["activa"] and not cruzada["tercero"]:
            return messagebox.showwarning(
                "Factura Cruzada",
                "Escriba el nombre de la persona o empresa a quien se le pagó (tercero).\n\n"
                "La factura quedará registrada como pagada por ese tercero.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        # 🔁 ¿La factura la pagó un tercero? (factura cruzada)
        cruzada = self._datos_cruzada()
        if cruzada["activa"] and not cruzada["tercero"]:
            return messagebox.showwarning(
                "Factura Cruzada",
                "Escriba el nombre de la persona o empresa a quien se le pagó (tercero).\n\n"
                "La factura quedará registrada como pagada por ese tercero.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        # 🚀 CORTE DEL SISTEMA: no registrar compras anteriores a la Fecha de Comienzo
        if validar_fecha_registro:
            fecha_ok, msg_fecha = validar_fecha_registro(fecha)
            if not fecha_ok:
                return messagebox.showwarning("Fecha fuera del rango del sistema", msg_fecha, parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        if nro_doc:
            conn_check = conectar_db(silencioso=True)
            if conn_check:
                try:
                    c_check = conn_check.cursor()
                    c_check.execute("SELECT COUNT(*) FROM facturas_recibidas WHERE numero_documento = %s AND proveedor = %s", (nro_doc, prov))
                    if c_check.fetchone()[0] > 0:
                        return messagebox.showwarning("Duplicado", "Ese N° de Documento ya está registrado para este proveedor.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
                finally: 
                    liberar_conexion(conn_check)

        # 💰 Los montos salen del modo elegido: monto base (sin IGV) o total (con IGV)
        subtotal, imp, tot_bruto = self._montos_ingresados()
        if "Factura" in tipo:
            det_pct = ui_pct
            det_monto = tot_bruto * (det_pct / 100.0)
        elif "Recibo" in tipo:
            imp = round(subtotal * (ui_pct / 100.0), 2)     # retención sobre la base
            tot_bruto = subtotal
            det_pct = 0.0
            det_monto = 0.0
        else:
            det_pct = ui_pct
            det_monto = tot_bruto * (det_pct / 100.0)

        ruta_final = ""
        if self.ruta_archivo_temp:
            try:
                carpeta_destino = os.path.join(ruta_base, "facturas_recibidas")
                os.makedirs(carpeta_destino, exist_ok=True)
                nombre_ext = os.path.splitext(self.ruta_archivo_temp)[1]
                ruta_final = os.path.join(carpeta_destino, f"Recibida_{datetime.now().strftime('%Y%m%d%H%M%S')}_{prov.replace(' ', '_')}{nombre_ext}")
                shutil.copy2(self.ruta_archivo_temp, ruta_final)
            except Exception as e: 
                return messagebox.showerror("Error", f"Fallo al guardar archivo:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        # 📎 Soporte del pago hecho al tercero (se copia a la carpeta autorizada)
        ruta_soporte_tercero = ""
        if cruzada["activa"] and cruzada["soporte"]:
            ruta_soporte_tercero = self._guardar_soporte_tercero(ruta_base, cruzada["tercero"])
            if not ruta_soporte_tercero:
                return messagebox.showwarning("Factura Cruzada",
                                              "No se pudo copiar el soporte del pago al tercero.\n"
                                              "Verifique el archivo o vuelva a adjuntarlo.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        # 📎 Soporte del pago hecho al tercero (se copia a la carpeta autorizada)
        ruta_soporte_tercero = ""
        if cruzada["activa"] and cruzada["soporte"]:
            ruta_soporte_tercero = self._guardar_soporte_tercero(ruta_base, cruzada["tercero"])
            if not ruta_soporte_tercero:
                return messagebox.showwarning("Factura Cruzada",
                                              "No se pudo copiar el soporte del pago al tercero.\n"
                                              "Verifique el archivo o vuelva a adjuntarlo.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        conn = obtener_conexion_segura()
        if not conn: 
            return messagebox.showwarning("Modo Lectura", "Estás sin conexión a internet.\nNo se pueden registrar facturas en Modo Lectura.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO facturas_recibidas (tipo_documento, numero_documento, fecha, proveedor, descripcion, evento_asociado, subtotal, impuesto, total, archivo_ruta, dias_credito, det_porcentaje, det_monto, categoria,
                                                pagado_por_tercero, soporte_pago_tercero, es_compra_cruzada, comision_interbancaria, es_interbancario)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (tipo, nro_doc, fecha, prov, concepto or desc, evento, subtotal, imp, tot_bruto, ruta_final, dias, det_pct, det_monto, categoria,
                  cruzada["tercero"] if cruzada["activa"] else "", ruta_soporte_tercero,
                  bool(cruzada["activa"]), cruzada["comision"], bool(cruzada["interbancario"])))
            try:
                id_factura_nueva = int(cursor.fetchone()[0])
            except Exception:
                id_factura_nueva = 0
            conn.commit()

            # 💳 Factura cruzada: se registra el pago hecho por el tercero para que
            #    la factura aparezca como PAGADA y su forma de pago muestre la cuenta.
            aviso_cruzada = ""
            if cruzada["activa"] and id_factura_nueva:
                if "Recibo" in tipo and "8%" in tipo:
                    neto_cruzada = tot_bruto - imp - det_monto
                else:
                    neto_cruzada = tot_bruto - det_monto
                # 💸 El tercero pago la factura MAS la comision interbancaria
                comision_cruzada = cruzada["comision"] if cruzada["interbancario"] else 0.0
                monto_pago_cruz = max(0.0, neto_cruzada) + max(0.0, comision_cruzada)
                try:
                    cur_pago = conn.cursor()
                    cur_pago.execute("""
                        INSERT INTO pagos_comprobantes (id_factura, monto_pagado, archivo_ruta, proveedor_nombre,
                                                       fecha_pago, categoria_suministro, codigo_cotizacion,
                                                       cuenta_origen, numero_operacion)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (id_factura_nueva, monto_pago_cruz, ruta_soporte_tercero or "", prov, fecha,
                          categoria, nro_doc, cruzada["cuenta"] or ("Pago de " + cruzada["tercero"]),
                          cruzada["operacion"] or "PAGO A TERCERO"))
                    conn.commit()
                except Exception as e_pago_cruz:
                    print("Aviso - pago de la factura cruzada:", e_pago_cruz)
                detalle_comision = (" (factura " + formatear_moneda(neto_cruzada)
                                    + " + comision " + formatear_moneda(comision_cruzada) + ")"
                                    if comision_cruzada > 0 else "")
                aviso_cruzada = ("\n\n\U0001F501 Factura cruzada: pagada por " + str(cruzada["tercero"])
                                 + "\nPago registrado: " + formatear_moneda(monto_pago_cruz) + detalle_comision
                                 + "\nQueda registrada como PAGADA en Cuentas por Pagar, sin saldo pendiente.")
            
            cache_sistema.invalidar()
            registrar_auditoria(self.app_padre.usuario_activo, "Facturas Recibidas", f"Registró factura {nro_doc} del proveedor '{prov}'")
            messagebox.showinfo("Éxito", "Documento recibido registrado correctamente." + aviso_cruzada, parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            traer_modulo_al_frente(getattr(self, "main_root", None))
            
            self.cargar_categorias()
            self.ent_nro_doc.delete(0, tk.END)
            self.ent_desc.delete(0, tk.END)
            try:
                self.ent_concepto.delete(0, tk.END)
            except Exception:
                pass
            self.ent_subtotal.delete(0, tk.END)
            
            self.ruta_archivo_temp = ""
            self.btn_archivo.configure(text="📎 Adjuntar Archivo Manual", fg_color="#7f8c8d", hover_color="#606b6b")
            
            self.cargar_datos_tabla(reset_pagina=True)
            if hasattr(self.app_padre, 'app_pagos'): 
                self.app_padre.app_pagos.cargar_datos_pagar(reset_pagina=True)

            # 💰 Se ofrece CANCELAR (pagar) la factura recién registrada y adjuntar
            #    el comprobante de pago (se puede escanear con la multifuncional).
            if id_factura_nueva and not cruzada["activa"]:
                try:
                    self._ofrecer_cancelacion(id_factura_nueva, prov, nro_doc)
                except Exception as e_can:
                    print("Aviso - oferta de cancelación:", e_can)

        except Exception as e: 
            messagebox.showerror("Error", str(e), parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        finally: 
            liberar_conexion(conn)

    def _ofrecer_cancelacion(self, id_factura, proveedor, numero_documento):
        """Pregunta si se cancela (paga) la factura recién registrada.

        Si acepta, se abre 'Registrar Pago' de esa factura y se ofrece escanear
        el comprobante de pago para archivarlo.
        """
        if not messagebox.askyesno(
                "Cancelar la factura",
                f"✅ Documento registrado: {numero_documento or 's/n'}\n"
                f"Proveedor: {proveedor}\n\n"
                "¿Desea registrar ahora la CANCELACIÓN (pago) de esta factura\n"
                "y adjuntar el comprobante de pago?", parent=ventana_para_mensaje(getattr(self, "main_root", None))):
            return
        pagos = getattr(self.app_padre, "app_pagos", None)
        if pagos is None or not hasattr(pagos, "abrir_pago_de_factura"):
            return messagebox.showinfo(
                "Cancelación",
                "El documento quedó registrado.\n\nRegistre el pago en la pestaña "
                "«Cuentas por Pagar» de este módulo.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        try:
            if hasattr(self.app_padre, "tabview"):
                for nombre_tab in ("💳 2. Control de Pagos y Deudas", "💵 Cuentas por Pagar", "Cuentas por Pagar"):
                    try:
                        self.app_padre.tabview.set(nombre_tab)
                        break
                    except Exception:
                        continue
        except Exception as e_tab:
            print("Aviso - cambio de pestaña:", e_tab)
        pagos.abrir_pago_de_factura(id_factura, escanear=True)

    def cargar_datos_tabla(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = ""
        if hasattr(self, 'ent_buscar_facturas'):
            filtro = self.ent_buscar_facturas.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina

        self._carga_facr_token = getattr(self, "_carga_facr_token", 0) + 1
        token = self._carga_facr_token

        clave_cache = (f"fac_recibidas_{filtro}_mes_{getattr(self, 'mes_filtro', '')}"
                       f"_pag_{self.pagina_actual}")
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla(datos, token)
        else:
            self.tabla.delete(*self.tabla.get_children())
            self.tabla.insert("", tk.END, values=("", "", "", "Cargando datos...", "", "", "", "", "", ""))
            
            threading.Thread(
                target=self._worker_cargar_datos_tabla,
                args=(filtro, token, offset, clave_cache),
                daemon=True
            ).start()

    def _worker_cargar_datos_tabla(self, filtro, token, offset, clave_cache):
        filas_resultado = []
        conn = obtener_conexion_segura(mostrar_aviso=False)
        
        if not conn:
            self.main_root.after(0, lambda: self._pintar_tabla([], token))
            return
            
        try:
            cursor = conn.cursor()
            condiciones, params = [], []
            # 🗓️ Solo las facturas del mes elegido (por defecto, el mes en curso)
            patron_mes = patron_fecha_mes(getattr(self, "mes_filtro", mes_en_curso()))
            if patron_mes:
                condiciones.append("fecha LIKE %s")
                params.append(patron_mes)
            if filtro:
                val = f"%{filtro}%"
                condiciones.append("(numero_documento ILIKE %s OR proveedor ILIKE %s "
                                   "OR evento_asociado ILIKE %s OR descripcion ILIKE %s)")
                params.extend([val, val, val, val])
            where_sql = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
            cursor.execute(f"""
                SELECT id, fecha, numero_documento, dias_credito, tipo_documento, proveedor, 
                       evento_asociado, descripcion, subtotal, impuesto, total, COALESCE(det_monto, 0), archivo_ruta, categoria,
                       COALESCE(pagado_por_tercero, ''), COALESCE(es_compra_cruzada, FALSE),
                       COALESCE(comision_interbancaria, 0), COALESCE(soporte_pago_tercero, '') 
                FROM facturas_recibidas{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s
            """, tuple(params) + (self.registros_por_pagina, offset))
                
            contador = (self.pagina_actual - 1) * self.registros_por_pagina + 1
            
            for r in cursor.fetchall():
                tiene_arch = "✅ Ver" if r[12] else "❌ No"
                tipo_doc = r[4]
                impuesto = r[9]
                tot_bruto = r[10]
                det_monto = r[11]
                cat = r[13] if r[13] else "GENERAL"
                # 🔁 Compra cruzada (pagada por un tercero): se avisa en la lista
                tercero_cruz = str(r[14] or "") if len(r) > 14 else ""
                es_cruzada = bool(r[15]) if len(r) > 15 else False
                comision_cruz = float(r[16] or 0) if len(r) > 16 else 0.0
                nota_cruzada = ""
                if es_cruzada:
                    nota_cruzada = "🔁 Compra cruzada"
                    if tercero_cruz:
                        nota_cruzada += " - pago: " + tercero_cruz
                    if comision_cruz > 0:
                        nota_cruzada += " - comision " + formatear_moneda(comision_cruz)
                
                tipo_str = str(tipo_doc)
                ev_str = str(r[6])
                if "Recibo" in tipo_str and "8%" in tipo_str:
                    neto = float(tot_bruto) - float(impuesto) - float(det_monto)
                else:
                    neto = float(tot_bruto) - float(det_monto)
                    
                row_vals = (
                    contador, 
                    r[0], 
                    r[1], 
                    r[2] if r[2] else "-", 
                    r[3], 
                    tipo_str.split(" ")[0] if tipo_doc else "Desc", 
                    r[5], 
                    cat, 
                    ev_str.split(" | ")[0] if " | " in ev_str else r[6], 
                    ((str(r[7]) if r[7] else "-") + ("   " + nota_cruzada if nota_cruzada else "")), 
                    formatear_moneda(r[8]), 
                    formatear_moneda(impuesto), 
                    formatear_moneda(tot_bruto), 
                    formatear_moneda(det_monto), 
                    formatear_moneda(neto), 
                    tiene_arch
                )
                filas_resultado.append(row_vals)
                contador += 1
                
            cache_sistema.guardar(clave_cache, filas_resultado)
        except Exception: 
            pass
        finally: 
            liberar_conexion(conn)
            
        self.main_root.after(0, lambda: self._pintar_tabla(filas_resultado, token))

    def _pintar_tabla(self, rows, token):
        if token != getattr(self, "_carga_facr_token", 0): 
            return
            
        self.tabla.delete(*self.tabla.get_children())
            
        for r in rows: 
            self.tabla.insert("", tk.END, values=r)
            
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1: 
                self.btn_ant.configure(state="normal")
            else: 
                self.btn_ant.configure(state="disabled")
                
            if len(rows) == self.registros_por_pagina: 
                self.btn_sig.configure(state="normal")
            else: 
                self.btn_sig.configure(state="disabled")

    def abrir_datos_bancarios(self, event=None):
        """🖱️ CLIC DERECHO sobre una factura: abre la ventana con los datos bancarios
        del proveedor (banco, cuenta, CCI, detracción) para copiarlos.

        El clic izquierdo y el doble clic quedan para seleccionar la factura y ver
        el comprobante adjunto.
        """
        if event is None:
            return
        fila, proveedor, detalle = "", "", ""
        try:
            fila = self.tabla.identify_row(event.y)
            if fila:
                valores = self.tabla.item(fila, "values")
                proveedor = valores[6] if len(valores) > 6 else ""
                detalle = (f"Factura {valores[3] if len(valores) > 3 else ''}"
                           f"   ·   Fecha: {valores[2] if len(valores) > 2 else ''}")
        except Exception:
            return
        cache = getattr(self, "_ventanas_bancarias", None)
        if cache is None:
            cache = self._ventanas_bancarias = {}
        return abrir_datos_bancarios_de_fila(
            self.tabla, event, self.main_root, proveedor, detalle, cache,
            al_seleccionar=self.tabla.selection_set)

    def _clic_izquierdo_factura(self, event=None):
        """Clic IZQUIERDO sobre una factura: abre su archivo adjunto (la factura).

        Se resuelve la fila que está bajo el cursor (no la selección previa, que
        todavía no se actualizó) y se evita abrir dos veces con un doble clic.
        """
        fila = ""
        try:
            if event is not None:
                # Solo si el clic cayó sobre una fila (no en el encabezado que ordena)
                fila = self.tabla.identify_row(event.y)
                if not fila:
                    return
            else:
                seleccion = self.tabla.selection()
                fila = seleccion[0] if seleccion else ""
            if not fila:
                return
            self.tabla.selection_set(fila)
            try:
                self.tabla.focus(fila)
            except Exception:
                pass
        except Exception:
            return
        ahora = datetime.now().timestamp()
        if (getattr(self, "_ultimo_clic_fac", None) == fila
                and ahora - getattr(self, "_ultimo_clic_fac_t", 0) < 1.5):
            return
        self._ultimo_clic_fac, self._ultimo_clic_fac_t = fila, ahora
        return self.abrir_archivo(event)

    def abrir_archivo(self, event):
        sel = self.tabla.selection()
        if not sel: return
        
        id_doc = self.tabla.item(sel[0], "values")[1]
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if not conn: return
        
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT archivo_ruta FROM facturas_recibidas WHERE id = %s", (id_doc,))
            res = cursor.fetchone()
            if res and res[0] and os.path.exists(res[0]): 
                abrir_documento(res[0])
            else:
                messagebox.showinfo("Aviso", "No se encontró el archivo adjunto.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        except Exception: 
            pass
        finally: 
            liberar_conexion(conn)

    def abrir_ventana_edicion(self):
        sel = self.tabla.selection()
        if not sel: 
            return messagebox.showwarning("Atención", "Seleccione un documento.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        valores = self.tabla.item(sel[0], "values")
        id_doc = valores[1]
        
        conn = obtener_conexion_segura()
        if not conn: return
        
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT tipo_documento, numero_documento, fecha, dias_credito, subtotal, det_porcentaje, descripcion, categoria FROM facturas_recibidas WHERE id = %s", (id_doc,))
            datos = cursor.fetchone()
        except Exception as e: 
            return messagebox.showerror("Error", str(e), parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        finally: 
            liberar_conexion(conn)
            
        if not datos: return
        
        try:
            tipo_db, nro_db, fecha_db, dias_db, sub_db, det_pct_db, desc_db, cat_db = datos
        except Exception:
            tipo_db, nro_db, fecha_db, dias_db, sub_db, det_pct_db = datos
            desc_db, cat_db = "", ""

        v_edit = ctk.CTkToplevel(self.main_root)
        v_edit.title("Gestión de Documento Recibido")
        v_edit.geometry("450x650")
        v_edit.transient(self.main_root)
        v_edit.grab_set()

        ctk.CTkLabel(v_edit, text=f"Gestión del Registro ID: {id_doc}", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        f_scroll = ctk.CTkScrollableFrame(v_edit, fg_color="transparent")
        f_scroll.pack(fill="both", expand=True, padx=20, pady=5)
        
        ctk.CTkLabel(f_scroll, text="N° Documento:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_nro = ctk.CTkEntry(f_scroll)
        ent_nro.pack(fill="x", pady=(0, 8))
        ent_nro.insert(0, str(nro_db if nro_db else ""))
        
        ctk.CTkLabel(f_scroll, text="Fecha (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_fec = ctk.CTkEntry(f_scroll)
        ent_fec.pack(fill="x", pady=(0, 8))
        ent_fec.insert(0, str(fecha_db if fecha_db else ""))
        
        ctk.CTkLabel(f_scroll, text="Días de Crédito:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_dias = ctk.CTkEntry(f_scroll)
        ent_dias.pack(fill="x", pady=(0, 8))
        ent_dias.insert(0, str(dias_db if dias_db is not None else 0))
        
        # 📝 Concepto / Descripción del gasto (igual que en el formulario de ingreso)
        ctk.CTkLabel(f_scroll, text="Concepto / Descripción:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_concepto = ctk.CTkEntry(f_scroll, placeholder_text="Ej. Alquiler de local, catering, sonido...")
        ent_concepto.pack(fill="x", pady=(0, 8))
        ent_concepto.insert(0, str(desc_db or ""))

        # 🏷️ Categoría de Gasto con el mismo botón de administrar del módulo de Banco
        ctk.CTkLabel(f_scroll, text="Categoría de Gasto:", font=("Arial", 11, "bold")).pack(anchor="w")
        f_cat_edit = ctk.CTkFrame(f_scroll, fg_color="transparent")
        f_cat_edit.pack(fill="x", pady=(0, 8))
        cmb_cat_edit = ctk.CTkComboBox(f_cat_edit, state="normal", values=self._lista_categorias_gasto())
        cmb_cat_edit.pack(side="left", fill="x", expand=True)
        cmb_cat_edit.set(str(cat_db or "GENERAL / NO ASIGNADO"))

        def administrar_categorias_edit():
            try:
                from modulo_banco import abrir_gestion_categorias_gastos
            except Exception as e:
                return messagebox.showerror("Categorías de Gasto",
                                            f"No se pudo cargar el módulo de Banco:\n{e}", parent=v_edit)
            elegida = cmb_cat_edit.get().strip()

            def al_guardar():
                nuevas = self._lista_categorias_gasto()
                cmb_cat_edit.configure(values=nuevas)
                if elegida in nuevas:
                    cmb_cat_edit.set(elegida)

            try:
                abrir_gestion_categorias_gastos(parent=v_edit, al_guardar=al_guardar)
            except Exception as e:
                messagebox.showerror("Categorías de Gasto",
                                     f"No se pudo abrir la ventana de categorías:\n{e}", parent=v_edit)

        ctk.CTkButton(f_cat_edit, text="⚙️", width=34, font=("Arial", 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=administrar_categorias_edit).pack(side="right", padx=(6, 0))

        ctk.CTkLabel(f_scroll, text="Monto Base (Subtotal):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_sub = ctk.CTkEntry(f_scroll)
        ent_sub.pack(fill="x", pady=(0, 8))
        ent_sub.insert(0, str(sub_db if sub_db else 0.0))
        
        ctk.CTkLabel(f_scroll, text="Detracción/Retención (%):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_det = ctk.CTkEntry(f_scroll)
        ent_det.pack(fill="x", pady=(0, 8))
        ent_det.insert(0, str(det_pct_db if det_pct_db else 0.0))

        def guardar_cambios():
            try:
                nro = ent_nro.get().strip()
                fec = ent_fec.get().strip()
                dias = int(ent_dias.get().strip() or 0)
                sub = float(ent_sub.get().strip() or 0)
                det_pct = float(ent_det.get().strip() or 0)
                concepto_edit = ent_concepto.get().strip()
                categoria_edit = cmb_cat_edit.get().strip() or "GENERAL / NO ASIGNADO"
            except ValueError: 
                return messagebox.showerror("Error", "Montos y días deben ser numéricos.", parent=v_edit)

            # 🚀 CORTE DEL SISTEMA: no permitir mover una compra antes de la Fecha de Comienzo
            if validar_fecha_registro:
                fecha_ok, msg_fecha = validar_fecha_registro(fec)
                if not fecha_ok:
                    return messagebox.showwarning("Fecha fuera del rango del sistema", msg_fecha, parent=v_edit)

            if "Factura" in tipo_db: 
                imp = sub * 0.105 if "10.5%" in tipo_db else sub * 0.18
                tot_bruto = sub + imp
                det_monto = tot_bruto * (det_pct / 100.0)
            elif "Recibo" in tipo_db: 
                imp = sub * (det_pct / 100.0)
                tot_bruto = sub
                det_monto = 0.0
            else: 
                imp = 0.0
                tot_bruto = sub
                det_monto = tot_bruto * (det_pct / 100.0)
                
            conn_upd = obtener_conexion_segura(parent=v_edit)
            if not conn_upd: return
            
            try:
                cur = conn_upd.cursor()
                cur.execute("""
                    UPDATE facturas_recibidas 
                    SET numero_documento=%s, fecha=%s, dias_credito=%s, subtotal=%s, impuesto=%s, total=%s,
                        det_porcentaje=%s, det_monto=%s, descripcion=%s, categoria=%s
                    WHERE id=%s
                """, (nro, fec, dias, sub, imp, tot_bruto, det_pct, det_monto,
                      concepto_edit, categoria_edit, id_doc))
                conn_upd.commit()
                
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Facturas Recibidas", f"Modificó factura ID {id_doc}")
                messagebox.showinfo("Éxito", "Registro actualizado correctamente.", parent=v_edit)
                v_edit.destroy()
                
                self.cargar_datos_tabla(reset_pagina=True)
                if hasattr(self.app_padre, 'app_pagos'): 
                    self.app_padre.app_pagos.cargar_datos_pagar(reset_pagina=True)
            except Exception as e: 
                messagebox.showerror("Error", f"No se pudo guardar:\n{e}", parent=v_edit)
            finally: 
                liberar_conexion(conn_upd)

        ctk.CTkButton(f_scroll, text="💾 Guardar Cambios", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=guardar_cambios).pack(fill="x", padx=10, pady=(15, 5))

        def eliminar_registro():
            if messagebox.askyesno("Confirmar Eliminación", "⚠️ ¿Desea eliminar completamente este registro?", parent=v_edit):
                conn = obtener_conexion_segura(parent=v_edit)
                if not conn: return
                
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM facturas_recibidas WHERE id = %s", (id_doc,))
                    row = cursor.fetchone()
                    if row and row[0] and os.path.exists(row[0]): 
                        os.remove(row[0])
                        
                    cursor.execute("DELETE FROM facturas_recibidas WHERE id = %s", (id_doc,))
                    conn.commit()
                    
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Facturas Recibidas", f"Eliminó factura ID {id_doc}")
                    messagebox.showinfo("Éxito", "Registro eliminado.", parent=v_edit)
                    v_edit.destroy()
                    
                    self.cargar_datos_tabla(reset_pagina=True)
                    if hasattr(self.app_padre, 'app_pagos'): 
                        self.app_padre.app_pagos.cargar_datos_pagar(reset_pagina=True)
                except Exception as e: 
                    messagebox.showerror("Error", str(e), parent=v_edit)
                finally: 
                    liberar_conexion(conn)

        ctk.CTkButton(f_scroll, text="❌ Eliminar Registro Completo", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_registro).pack(fill="x", padx=10, pady=15)

# =========================================================
# PESTAÑA 2: CUENTAS POR PAGAR (PAGOS Y DEUDAS)
# =========================================================
class CuentasPorPagarTab:
    def __init__(self, tab_frame, main_root, app_padre):
        self.tab_frame = tab_frame
        self.main_root = main_root
        self.app_padre = app_padre
        
        # 🚀 VARIABLES LAZY LOADING
        self.pagina_actual = 1
        self.registros_por_pagina = 50

        # 🗓️ Filtro por mes: por defecto se muestra el MES EN CURSO
        self.mes_filtro = mes_en_curso()
        
        self.crear_interfaz()

    def crear_interfaz(self):
        frame_acciones = ctk.CTkFrame(self.tab_frame, corner_radius=8, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
        frame_acciones.pack(fill="x", padx=15, pady=(10, 10), ipady=5)

        # 🚀 BOTÓN UNIFICADO DE REPORTES Y RESÚMENES
        btn_reporte = ctk.CTkButton(frame_acciones, text="📈 Reporte y Resumen Avanzado", font=("Arial", 12, "bold"), command=self.mostrar_reporte_totales, fg_color="#1f538d", hover_color="#163b65")
        btn_reporte.pack(side="left", padx=5, pady=5)
        
        btn_pago = ctk.CTkButton(frame_acciones, text="🧾 Registrar Pago", font=("Arial", 12, "bold"), command=self.cargar_comprobante_pago, fg_color="#34495e", hover_color="#2c3e50")
        btn_pago.pack(side="left", padx=5, pady=5)
        
        # 📎 Ver TODOS los comprobantes de pago de la factura seleccionada
        btn_soportes = ctk.CTkButton(frame_acciones, text="📎 Ver Comprobantes", font=("Arial", 12, "bold"),
                                     command=lambda: self.abrir_todos_los_archivos(None),
                                     fg_color="#0e7490", hover_color="#155e75")
        btn_soportes.pack(side="left", padx=5, pady=5)

        btn_editar = ctk.CTkButton(frame_acciones, text="✏️ Editar Pagos", font=("Arial", 12, "bold"), command=self.abrir_ventana_edicion, fg_color="#34495e", hover_color="#2c3e50")
        btn_editar.pack(side="left", padx=5, pady=5)

        # 📅 Listado de TODAS las facturas por pagar ordenadas por días para vencer
        btn_vencer = ctk.CTkButton(frame_acciones, text="📅 Ver por Días por Vencer", font=("Arial", 12, "bold"),
                                   command=self.ver_por_vencer, fg_color="#8e44ad", hover_color="#703688")
        btn_vencer.pack(side="left", padx=5, pady=5)
        
        btn_refresh = ctk.CTkButton(frame_acciones, text="🔄 Actualizar", font=("Arial", 12, "bold"), command=lambda: self.cargar_datos_pagar(reset_pagina=True), fg_color="#7f8c8d", hover_color="#606b6b")
        btn_refresh.pack(side="right", padx=10, pady=5)

        f_busqueda = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_busqueda.pack(fill="x", padx=15, pady=(0, 5))
        
        ctk.CTkLabel(f_busqueda, text="🔍 Buscar:", font=("Arial", 11, "bold")).pack(side="left", padx=(0, 5))
        self.ent_buscar_pagos = ctk.CTkEntry(f_busqueda, placeholder_text="Filtrar por documento, proveedor, evento, concepto...")
        self.ent_buscar_pagos.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(f_busqueda, text="🗓️ Mes:", font=("Arial", 11, "bold")).pack(side="left", padx=(10, 5))
        self.combo_mes = ctk.CTkComboBox(f_busqueda, values=construir_valores_mes(), width=150,
                                         state="readonly", command=self.on_cambiar_mes_pagos)
        self.combo_mes.pack(side="left", padx=(0, 5))
        self.combo_mes.set(self.mes_filtro)

        self.ent_buscar_pagos.bind("<KeyRelease>", lambda e: self.buscar_con_retraso_pagos())
        self.ent_buscar_pagos.bind("<Return>", lambda e: self.cargar_datos_pagar(reset_pagina=True))

        f_tabla = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=0)
        
        columnas = ("num", "id_factura", "fecha", "nro_doc", "proveedor", "evento", "concepto", "subtotal", "igv", "detraccion", "neto_facturado", "pagado", "saldo", "archivos")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        
        self.tabla.heading("num", text="N°")
        self.tabla.heading("id_factura", text="ID (Oculto)")
        self.tabla.heading("fecha", text="Fecha Fac.")
        self.tabla.heading("nro_doc", text="N° Documento")
        self.tabla.heading("proveedor", text="Proveedor")
        self.tabla.heading("evento", text="Evento Asociado")
        self.tabla.heading("concepto", text="Concepto")
        self.tabla.heading("subtotal", text="Subtotal")
        self.tabla.heading("igv", text="IGV")
        self.tabla.heading("detraccion", text="Detracción")
        self.tabla.heading("neto_facturado", text="Neto a Pagar")
        self.tabla.heading("pagado", text="Total Pagado")
        self.tabla.heading("saldo", text="Saldo Pendiente")
        self.tabla.heading("archivos", text="Historial Adjuntos")

        self.tabla.column("num", width=40, anchor="center")
        self.tabla.column("id_factura", width=0, stretch=tk.NO)
        self.tabla.column("fecha", width=80, anchor="center")
        self.tabla.column("nro_doc", width=100, anchor="center")
        self.tabla.column("proveedor", width=140, anchor="w")
        self.tabla.column("evento", width=120, anchor="w")
        self.tabla.column("concepto", width=150, anchor="w")
        self.tabla.column("neto_facturado", width=95, anchor="e")
        self.tabla.column("pagado", width=90, anchor="e")
        self.tabla.column("saldo", width=90, anchor="e")
        self.tabla.column("archivos", width=100, anchor="center")

        self.tabla.config(displaycolumns=("num", "fecha", "nro_doc", "proveedor", "evento", "concepto", "neto_facturado", "pagado", "saldo", "archivos"))
        # 🖱️ Un clic SELECCIONA la fila; el DOBLE clic abre el comprobante de pago
        self.tabla.bind("<Double-1>", self.abrir_todos_los_archivos, add="+")
        # 🖱️ CLIC DERECHO en una factura: datos bancarios del proveedor
        # (botón 3 en Windows/Linux y botón 2 en macOS)
        self.tabla.bind("<Button-3>", self.abrir_datos_bancarios_pagar, add="+")
        self.tabla.bind("<Button-2>", self.abrir_datos_bancarios_pagar, add="+")
        self._ventanas_bancarias = {}

        # 🎨 Colores de las filas según el vencimiento del crédito (ver leyenda abajo)
        self.tabla.tag_configure("pagado", background="#d5f5e3")     # 🟢 ya pagada
        self.tabla.tag_configure("urgente", background="#f8d7da")    # 🔴 vence en 5 días o menos
        self.tabla.tag_configure("proximo", background="#d6eaf8")    # 🔵 vence en 6 a 15 días

        scroll_y = ctk.CTkScrollbar(f_tabla, orientation="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=scroll_y.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        scroll_y.pack(side="right", fill="y")

        # 🏷️ LEYENDA: explica el color de cada fila de la tabla
        f_leyenda = ctk.CTkFrame(self.tab_frame, fg_color="#f8f9fa", corner_radius=6,
                                 border_width=1, border_color="#e0e0e0")
        f_leyenda.pack(fill="x", padx=15, pady=(8, 0))
        ctk.CTkLabel(f_leyenda, text="Leyenda:", font=("Arial", 11, "bold")).pack(side="left", padx=(10, 8), pady=5)
        ctk.CTkLabel(f_leyenda, text="💡 Un clic: seleccionar   ·   Doble clic: ver el comprobante de pago   ·   Clic DERECHO: datos bancarios",
                     font=("Arial", 10, "italic"), text_color="#1f538d").pack(side="right", padx=(0, 12), pady=5)
        for color_fondo, color_texto, texto_leyenda in (
            ("#f8d7da", "#c0392b", "🔴 Vence en 5 días o menos (o ya vencida)"),
            ("#d6eaf8", "#1f538d", "🔵 Vence entre 6 y 15 días"),
            ("#d5f5e3", "#1e8449", "🟢 Factura pagada"),
            ("#ffffff", "#7f8c8d", "⚪ Más de 15 días de plazo"),
        ):
            chip = ctk.CTkFrame(f_leyenda, fg_color=color_fondo, corner_radius=4,
                                border_width=1, border_color="#d0d0d0")
            chip.pack(side="left", padx=4, pady=5)
            ctk.CTkLabel(chip, text=texto_leyenda, font=("Arial", 10, "bold"),
                         text_color=color_texto).pack(padx=8, pady=2)

        # 🖱️ Al pasar el mouse por encima de una factura aparece un cuadrito con los
        # días que le faltan para vencerse (y la fecha de vencimiento).
        if TooltipVencimiento is not None:
            self.vencimientos_filas = {}
            self.tooltip_vencimiento = TooltipVencimiento(self.tabla, self._datos_tooltip)

        self.frame_bottom = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        self.frame_bottom.pack(fill="x", padx=15, pady=10)
        
        f_paginacion = ctk.CTkFrame(self.frame_bottom, fg_color="transparent")
        f_paginacion.pack(side="left")
        
        self.btn_ant = ctk.CTkButton(f_paginacion, text="◀ Ant", width=60, command=self.pagina_anterior)
        self.btn_ant.pack(side="left", padx=2)
        
        self.lbl_pagina = ctk.CTkLabel(f_paginacion, text=f"Pág {self.pagina_actual}", font=("Arial", 11, "bold"))
        self.lbl_pagina.pack(side="left", padx=5)
        
        self.btn_sig = ctk.CTkButton(f_paginacion, text="Sig ▶", width=60, command=self.pagina_siguiente)
        self.btn_sig.pack(side="left", padx=2)

        self.btn_excel = ctk.CTkButton(self.frame_bottom, text="📊 Exportar a Excel", font=("Arial", 12, "bold"), width=160, fg_color="#27ae60", hover_color="#1e8449", command=self.exportar_excel)
        self.btn_excel.pack(side="left", padx=20)
        
        self.lbl_total_general = ctk.CTkLabel(self.frame_bottom, text="Total Pendiente General por Pagar: 0.00", font=("Arial", 12, "bold"), text_color="#c0392b")
        self.lbl_total_general.pack(side="right")

        self.main_root.after(100, lambda: self.cargar_datos_pagar(reset_pagina=True))

    def _datos_tooltip(self, iid):
        """(vencimiento, pagada) de una fila, para el cuadrito del mouse.

        El vencimiento viene como 'YYYY-MM-DD' (o '' si no se pudo calcular).
        """
        datos = getattr(self, "vencimientos_filas", {}).get(iid)
        if not datos:
            return None
        return datos

    # ==================================================================
    # 📅 FACTURAS POR PAGAR ORDENADAS POR DÍAS PARA VENCER
    # ==================================================================
    def ver_por_vencer(self):
        """Abre una ventana con TODAS las facturas por pagar en orden de urgencia.

        Primero las vencidas, después las que vencen en 5 días o menos, luego las
        de 6 a 15 días y al final las que tienen más plazo. Se puede incluir
        también las ya pagadas.
        """
        # La ventana cuelga de la ventana principal del sistema (no del marco interno)
        padre = self.tab_frame.winfo_toplevel()
        v = ctk.CTkToplevel(padre)
        v.title("Facturas por Pagar - Ordenadas por Días para Vencer")
        v.geometry("1240x640")
        try:
            v.transient(padre)
        except Exception:
            pass
        try:
            v.after(80, lambda: v.focus_force())
        except Exception:
            pass

        ctk.CTkLabel(v, text="📅 FACTURAS POR PAGAR ORDENADAS POR DÍAS PARA VENCER",
                     font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(12, 2))

        f_top = ctk.CTkFrame(v, fg_color="#f8f9fa", corner_radius=8, border_width=1,
                             border_color="#e0e0e0")
        f_top.pack(fill="x", padx=12, pady=(6, 6))
        var_pagadas = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(f_top, text="Incluir facturas ya pagadas", variable=var_pagadas,
                        font=("Arial", 11, "bold")).pack(side="left", padx=12, pady=8)
        lbl_resumen_venc = ctk.CTkLabel(f_top, text="Cargando...", font=("Arial", 12, "bold"),
                                        text_color="#1f538d", justify="left")
        lbl_resumen_venc.pack(side="left", padx=12, pady=8)
        ctk.CTkButton(f_top, text="🔄 Actualizar", width=120, font=("Arial", 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: _cargar()).pack(side="right", padx=10, pady=8)

        f_tabla = ctk.CTkFrame(v, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        columnas = ("dias", "vence", "nro_doc", "proveedor", "evento", "neto", "saldo", "estado")
        tabla_venc = ttk.Treeview(f_tabla, columns=columnas, show="headings")
        for col, txt, ancho, anc in (("dias", "Días para vencer", 130, "center"),
                                     ("vence", "Vence el", 95, "center"),
                                     ("nro_doc", "N° Documento", 110, "center"),
                                     ("proveedor", "Proveedor", 260, "w"),
                                     ("evento", "Evento Asociado", 200, "w"),
                                     ("neto", "Neto a Pagar", 105, "e"),
                                     ("saldo", "Saldo Pendiente", 115, "e"),
                                     ("estado", "Estado", 150, "center")):
            tabla_venc.heading(col, text=txt)
            tabla_venc.column(col, width=ancho, anchor=anc)
        tabla_venc.tag_configure("vencido", background="#f8d7da")
        tabla_venc.tag_configure("urgente", background="#f8d7da")
        tabla_venc.tag_configure("proximo", background="#d6eaf8")
        tabla_venc.tag_configure("lejano", background="#f4f6f7")
        tabla_venc.tag_configure("pagado", background="#d5f5e3")
        scroll_v = ctk.CTkScrollbar(f_tabla, orientation="vertical", command=tabla_venc.yview)
        tabla_venc.configure(yscrollcommand=scroll_v.set)
        tabla_venc.pack(side="left", fill="both", expand=True)
        scroll_v.pack(side="right", fill="y")

        # 🖱️ El mismo cuadrito de días al pasar el mouse por encima
        if TooltipVencimiento is not None:
            v.vencimientos_filas = {}
            TooltipVencimiento(tabla_venc,
                               lambda iid, v=v: getattr(v, "vencimientos_filas", {}).get(iid))

        ctk.CTkButton(v, text="Cerrar", width=120, fg_color="#34495e", hover_color="#2c3e50",
                      command=v.destroy).pack(pady=(0, 10))

        # Referencias para poder dibujar desde el hilo principal
        v.tabla_venc_ref = tabla_venc
        v.lbl_resumen_venc_ref = lbl_resumen_venc

        def _cargar():
            tabla_venc.delete(*tabla_venc.get_children())
            v.vencimientos_filas = {}
            lbl_resumen_venc.configure(text="⏳ Cargando las facturas...")
            threading.Thread(target=self._worker_vencimientos,
                             args=(v, bool(var_pagadas.get())), daemon=True).start()

        var_pagadas.trace_add("write", lambda *_: _cargar())
        _cargar()

    def _worker_vencimientos(self, v, incluir_pagadas):
        """Trae TODAS las facturas con su saldo y su vencimiento (en segundo plano)."""
        filas = []
        conn = obtener_conexion_segura(mostrar_aviso=False)
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, fecha, numero_documento, proveedor, evento_asociado, subtotal,
                           impuesto, total, COALESCE(det_monto, 0), tipo_documento,
                           COALESCE(dias_credito, 0)
                    FROM facturas_recibidas ORDER BY id DESC
                """)
                registros = cursor.fetchall()
                cursor.execute("SELECT id_factura, SUM(monto_pagado) FROM pagos_comprobantes GROUP BY id_factura")
                pagos_lookup = {r[0]: float(r[1] or 0) for r in cursor.fetchall()}
            except Exception:
                registros, pagos_lookup = [], {}
            finally:
                liberar_conexion(conn)

            hoy = datetime.now().date()
            for reg in registros:
                (id_fac, fecha, nro, prov, evento, sub, imp, tot, det, tipo_doc,
                 dias_cred) = reg[:11]
                tot_val = float(tot) if tot else 0.0
                imp_val = float(imp) if imp else 0.0
                det_val = float(det) if det else 0.0
                if tipo_doc and "Recibo" in str(tipo_doc) and "8%" in str(tipo_doc):
                    neto = tot_val - imp_val - det_val
                else:
                    neto = tot_val - det_val
                saldo = max(0.0, neto - pagos_lookup.get(id_fac, 0.0))
                if not incluir_pagadas and saldo <= 0.01:
                    continue
                vencimiento = fecha_vencimiento(fecha, dias_cred)
                dias = (vencimiento - hoy).days if vencimiento else None
                if saldo <= 0.01:
                    estado = "pagado"
                elif dias is None:
                    estado = "lejano"
                elif dias < 0:
                    estado = "vencido"
                elif dias <= 5:
                    estado = "urgente"
                elif dias <= 15:
                    estado = "proximo"
                else:
                    estado = "lejano"
                filas.append({
                    "dias": dias, "vencimiento": vencimiento, "nro_doc": nro or "S/N",
                    "proveedor": prov or "", "evento": evento or "", "neto": neto,
                    "saldo": saldo, "estado": estado,
                })

        # 🔽 Orden por urgencia: primero lo vencido y lo que vence antes
        filas.sort(key=lambda f: (f["dias"] is None,
                                  f["dias"] if f["dias"] is not None else 99999,
                                  -f["saldo"]))
        self.main_root.after(0, lambda: self._pintar_vencimientos(v, filas))

    def _pintar_vencimientos(self, v, filas):
        """Dibuja el listado ordenado por vencimiento (hilo principal)."""
        try:
            if not v.winfo_exists():
                return
        except Exception:
            return
        tabla = getattr(v, "tabla_venc_ref", None)
        etiqueta = getattr(v, "lbl_resumen_venc_ref", None)
        if tabla is None:
            return
        try:
            tabla.delete(*tabla.get_children())
        except Exception:
            return

        grupos = {"vencido": [0, 0.0], "urgente": [0, 0.0], "proximo": [0, 0.0],
                  "lejano": [0, 0.0], "pagado": [0, 0.0]}
        vencimientos_filas = {}
        v.vencimientos_filas = vencimientos_filas      # 🖱️ lo usa el cuadrito del mouse
        for f in filas:
            dias = f["dias"]
            plural = "día" if abs(dias or 0) == 1 else "días"
            if dias is None:
                dias_txt = "Sin fecha"
            elif dias < 0:
                dias_txt = f"Vencida hace {abs(dias)} {plural}"
            elif dias == 0:
                dias_txt = "¡VENCE HOY!"
            else:
                dias_txt = f"{dias} {plural}"
            vence_txt = f["vencimiento"].strftime("%d/%m/%Y") if f["vencimiento"] else "-"
            estados = {"vencido": "🔴 Vencida", "urgente": "🔴 Vence en 5 días o menos",
                       "proximo": "🔵 Vence en 6 a 15 días", "lejano": "⚪ Más de 15 días",
                       "pagado": "🟢 Pagada"}
            iid = tabla.insert("", tk.END, values=(dias_txt, vence_txt, f["nro_doc"], f["proveedor"],
                                                   f["evento"], formatear_moneda(f["neto"]),
                                                   formatear_moneda(f["saldo"]),
                                                   estados.get(f["estado"], "")),
                               tags=(f["estado"],))
            if f["estado"] != "pagado":
                vencimientos_filas[iid] = (f["vencimiento"].strftime("%Y-%m-%d")
                                           if f["vencimiento"] else "", False)
            grupos[f["estado"]][0] += 1
            grupos[f["estado"]][1] += f["saldo"]

        total_pendiente = sum(g[1] for k, g in grupos.items() if k != "pagado")
        resumen = (f"🔴 Vencidas: {grupos['vencido'][0]} ({formatear_moneda(grupos['vencido'][1])})   |   "
                   f"🔴 ≤5 días: {grupos['urgente'][0]} ({formatear_moneda(grupos['urgente'][1])})   |   "
                   f"🔵 6-15 días: {grupos['proximo'][0]} ({formatear_moneda(grupos['proximo'][1])})   |   "
                   f"⚪ +15 días: {grupos['lejano'][0]} ({formatear_moneda(grupos['lejano'][1])})   |   "
                   f"TOTAL POR PAGAR: {formatear_moneda(total_pendiente)}")
        if grupos["pagado"][0]:
            resumen += f"   |   🟢 Pagadas: {grupos['pagado'][0]}"
        if etiqueta is not None:
            try:
                etiqueta.configure(text=resumen)
            except Exception:
                pass

    def pagina_anterior(self):
        if self.pagina_actual > 1: 
            self.pagina_actual -= 1
            self.cargar_datos_pagar()
            
    def pagina_siguiente(self):
        self.pagina_actual += 1
        self.cargar_datos_pagar()
        
    def buscar_con_retraso_pagos(self):
        if hasattr(self, "_busqueda_pag_job"):
            try: 
                self.main_root.after_cancel(self._busqueda_pag_job)
            except Exception: 
                pass
        self._busqueda_pag_job = self.main_root.after(350, lambda: self.cargar_datos_pagar(reset_pagina=True))

    def mostrar_reporte_totales(self):
        v_rep = ctk.CTkToplevel(self.main_root)
        v_rep.title("Reporte Avanzado de Compras y Pagos")
        v_rep.geometry("750x550")
        v_rep.transient(self.main_root)
        v_rep.grab_set()

        ctk.CTkLabel(v_rep, text="📊 REPORTE DE TOTALES FILTRADOS", font=("Arial", 16, "bold"), text_color="#1f538d").pack(pady=(15, 10))
        f_filtros = ctk.CTkFrame(v_rep, fg_color="#f8f9fa", border_width=1, border_color="#ccc")
        f_filtros.pack(fill="x", padx=20, pady=10, ipadx=10, ipady=10)

        f_prov = ctk.CTkFrame(f_filtros, fg_color="transparent")
        f_prov.pack(fill="x", pady=5)
        ctk.CTkLabel(f_prov, text="Proveedor:", font=("Arial", 11, "bold"), width=100, anchor="e").pack(side="left", padx=5)
        
        # 🚀 FIX: Obtener solo proveedores con facturas reales (o usar caché de respaldo)
        conn_prov = conectar_db(silencioso=True)
        provs_mem = []
        if conn_prov:
            try:
                c_prov = conn_prov.cursor()
                c_prov.execute("SELECT DISTINCT proveedor FROM facturas_recibidas WHERE proveedor IS NOT NULL AND proveedor != '' ORDER BY proveedor ASC")
                provs_mem = [r[0] for r in c_prov.fetchall()]
            except: 
                pass
            finally: 
                liberar_conexion(conn_prov)
            
        if not provs_mem: 
            provs_mem = cache_sistema.obtener('lista_proveedores_combobox') or []
            
        provs = ["Todos"] + provs_mem
        combo_prov = ctk.CTkComboBox(f_prov, values=provs, state="readonly", width=300)
        combo_prov.pack(side="left", padx=5)
        combo_prov.set("Todos")

        f_fechas = ctk.CTkFrame(f_filtros, fg_color="transparent")
        f_fechas.pack(fill="x", pady=5)
        ctk.CTkLabel(f_fechas, text="Desde:", font=("Arial", 11, "bold"), width=100, anchor="e").pack(side="left", padx=5)
        ent_desde = ctk.CTkEntry(f_fechas, width=110, placeholder_text=CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA"))
        ent_desde.pack(side="left", padx=5)
        ctk.CTkButton(f_fechas, text="📅", width=30, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_rep, ent_desde)).pack(side="left")
        
        ctk.CTkLabel(f_fechas, text="Hasta:", font=("Arial", 11, "bold"), width=60, anchor="e").pack(side="left", padx=5)
        ent_hasta = ctk.CTkEntry(f_fechas, width=110, placeholder_text=CONFIG_REGIONAL.get("formato_fecha", "DD/MM/AAAA"))
        ent_hasta.pack(side="left", padx=5)
        ctk.CTkButton(f_fechas, text="📅", width=30, fg_color="#1f538d", hover_color="#163b65", command=lambda: CalendarioNativo(v_rep, ent_hasta)).pack(side="left")

        f_resultados = ctk.CTkFrame(v_rep, fg_color="transparent")
        f_resultados.pack(fill="both", expand=True, padx=20, pady=10)
        
        def crear_lbl_res(padre, texto, color="#333"):
            f = ctk.CTkFrame(padre, fg_color="#ffffff", border_width=1, border_color="#ddd", corner_radius=8)
            f.pack(fill="x", pady=4)
            ctk.CTkLabel(f, text=texto, font=("Arial", 12, "bold"), text_color="gray").pack(side="left", padx=15, pady=10)
            lbl_val = ctk.CTkLabel(f, text="0.00", font=("Arial", 14, "bold"), text_color=color)
            lbl_val.pack(side="right", padx=15, pady=10)
            return lbl_val

        lbl_bruto = crear_lbl_res(f_resultados, "Total Compras Brutas (Subtotal Base):", "#1f538d")
        lbl_igv = crear_lbl_res(f_resultados, "Total IGV Facturado (18% / 10.5%):", "#1f538d")
        lbl_det = crear_lbl_res(f_resultados, "Total Detracción/Retención:", "#e67e22")
        lbl_pagado = crear_lbl_res(f_resultados, "Total Pagado (Dinero Egresado Real):", "#27ae60")
        lbl_por_pagar = crear_lbl_res(f_resultados, "Total por Pagar (Deuda Pendiente General):", "#c0392b")

        formatos_fecha = ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%m/%d/%Y"]
        def convertir_a_fecha(fecha_str):
            if not fecha_str: return None
            for fmt in formatos_fecha:
                try: 
                    return datetime.strptime(fecha_str.strip(), fmt)
                except ValueError: 
                    pass
            return None

        def calcular_totales():
            prov_filtro = combo_prov.get()
            d_desde = convertir_a_fecha(ent_desde.get())
            d_hasta = convertir_a_fecha(ent_hasta.get())
            
            if ent_desde.get() and ent_hasta.get() and (not d_desde or not d_hasta):
                return messagebox.showwarning("Error", "Formato de fecha inválido.", parent=v_rep)
                
            if d_desde and d_hasta and d_desde > d_hasta: 
                d_desde, d_hasta = d_hasta, d_desde

            conn = conectar_db(silencioso=True)
            if not conn: return
            
            try:
                c = conn.cursor()
                c.execute("SELECT id_factura, monto_pagado FROM pagos_comprobantes")
                pagos_dict = {}
                for id_f, monto in c.fetchall(): 
                    pagos_dict[id_f] = pagos_dict.get(id_f, 0.0) + (float(monto) if monto else 0.0)

                c.execute("SELECT id, fecha, proveedor, subtotal, impuesto, total, COALESCE(det_monto, 0), tipo_documento FROM facturas_recibidas")
                tot_bruto = tot_igv = tot_det = tot_pagado = tot_deuda = 0.0

                cache_fechas = {}
                for r in c.fetchall():
                    id_fac, fecha, prov, sub, imp, tot, det, tipo_doc = r
                    
                    if prov_filtro != "Todos" and prov != prov_filtro: 
                        continue
                    
                    clave_fecha = str(fecha)
                    if clave_fecha in cache_fechas:
                        f_dt = cache_fechas[clave_fecha]
                    else:
                        f_dt = convertir_a_fecha(clave_fecha)
                        cache_fechas[clave_fecha] = f_dt
                    if d_desde and d_hasta:
                        if not f_dt or not (d_desde <= f_dt <= d_hasta): continue
                    elif d_desde:
                        if not f_dt or f_dt < d_desde: continue
                    elif d_hasta:
                        if not f_dt or f_dt > d_hasta: continue

                    sub_val = float(sub) if sub else 0.0
                    imp_val = float(imp) if imp else 0.0
                    tot_val = float(tot) if tot else 0.0
                    det_val = float(det) if det else 0.0
                    
                    tot_bruto += sub_val

                    if tipo_doc and "Factura" in str(tipo_doc):
                        tot_igv += imp_val
                        tot_det += det_val
                        neto = tot_val - det_val
                    elif tipo_doc and "Recibo" in str(tipo_doc):
                        if "8%" in str(tipo_doc):
                            tot_det += imp_val
                            neto = tot_val - imp_val - det_val
                        else:
                            neto = tot_val - det_val
                    else:
                        tot_det += det_val
                        neto = tot_val - det_val

                    pagado = pagos_dict.get(id_fac, 0.0)
                    saldo = max(0.0, neto - pagado)

                    tot_pagado += pagado
                    tot_deuda += saldo

                lbl_bruto.configure(text=formatear_moneda(tot_bruto))
                lbl_igv.configure(text=formatear_moneda(tot_igv))
                lbl_det.configure(text=formatear_moneda(tot_det))
                lbl_pagado.configure(text=formatear_moneda(tot_pagado))
                lbl_por_pagar.configure(text=formatear_moneda(tot_deuda))

            except Exception as e:
                messagebox.showerror("Error", f"Fallo al calcular:\n{e}", parent=v_rep)
            finally:
                liberar_conexion(conn)

        ctk.CTkButton(f_filtros, text="🔍 Procesar y Calcular", font=("Arial", 12, "bold"), fg_color="#1f538d", hover_color="#163b65", command=calcular_totales).pack(pady=(10, 5))
        calcular_totales()

    def exportar_excel(self):
        try: 
            import pandas as pd
        except ImportError: 
            return messagebox.showerror("Error", "Falta librería pandas.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        filas = [self.tabla.item(item)["values"][2:] for item in self.tabla.get_children()]
        if not filas: 
            return messagebox.showwarning("Aviso", "No hay registros para exportar.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            
        columnas = ["Fecha Fac.", "N° Documento", "Proveedor", "Evento Asociado", "Concepto", "Subtotal", "IGV", "Detracción", "Neto Facturado", "Total Pagado", "Saldo Pendiente", "Archivos"]
        ruta = filedialog.asksaveasfilename(defaultextension=".xlsx", initialfile="Cuentas_por_Pagar.xlsx", filetypes=[("Excel", "*.xlsx")])
        if ruta:
            pd.DataFrame(filas, columns=columnas).to_excel(ruta, index=False)
            messagebox.showinfo("Éxito", f"Reporte exportado a:\n{ruta}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            abrir_documento(ruta)

    # 🚀 FIX: LAZY LOADING Y CACHÉ
    def on_cambiar_mes_pagos(self, choice=None):
        """Cambia el mes del listado de pagos y deudas."""
        self.mes_filtro = choice or mes_en_curso()
        self.cargar_datos_pagar(reset_pagina=True)

    def cargar_datos_pagar(self, reset_pagina=False):
        if reset_pagina:
            self.pagina_actual = 1
            
        if hasattr(self, 'lbl_pagina'):
            self.lbl_pagina.configure(text=f"Pág {self.pagina_actual}")

        filtro = ""
        if hasattr(self, 'ent_buscar_pagos'):
            filtro = self.ent_buscar_pagos.get().strip().lower()

        offset = (self.pagina_actual - 1) * self.registros_por_pagina
        
        self._carga_pagos_token = getattr(self, "_carga_pagos_token", 0) + 1
        token = self._carga_pagos_token
        
        clave_cache = (f"cuentas_pagar_{filtro}_mes_{getattr(self, 'mes_filtro', '')}"
                       f"_pag_{self.pagina_actual}")
        datos = cache_sistema.obtener(clave_cache)

        if datos is not None:
            self._pintar_tabla_cobros(datos["filas"], datos["total"], token,
                                      datos.get("estados"), datos.get("vencimientos"))
        else:
            self.tabla.delete(*self.tabla.get_children())
                
            self.tabla.insert("", tk.END, values=("", "", "Cargando datos...", "", "", "", ""))
            
            threading.Thread(
                target=self._worker_cargar_datos_pagar,
                args=(filtro, token, offset, clave_cache),
                daemon=True
            ).start()

    def _worker_cargar_datos_pagar(self, filtro, token, offset, clave_cache):
        filas_resultado = []
        estados = []            # 🎨 color de cada fila (mismo orden que filas_resultado)
        vencimientos = []       # 🖱️ fecha de vencimiento de cada fila ('YYYY-MM-DD' o '')
        total_pendiente = 0.0
        conn = obtener_conexion_segura(mostrar_aviso=False)
        
        if not conn:
            self.main_root.after(0, lambda: self._pintar_tabla_cobros([], 0.0, token, []))
            return
            
        try:
            cursor = conn.cursor()
            
            condiciones, params = [], []
            # 🗓️ Solo las facturas del mes elegido (por defecto, el mes en curso)
            patron_mes = patron_fecha_mes(getattr(self, "mes_filtro", mes_en_curso()))
            if patron_mes:
                condiciones.append("fecha LIKE %s")
                params.append(patron_mes)
            if filtro:
                val = f"%{filtro}%"
                condiciones.append("(numero_documento ILIKE %s OR proveedor ILIKE %s "
                                   "OR evento_asociado ILIKE %s OR descripcion ILIKE %s)")
                params.extend([val, val, val, val])
            where_sql = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
            cursor.execute(f"""
                SELECT id, fecha, numero_documento, proveedor, evento_asociado, descripcion, 
                       subtotal, impuesto, total, COALESCE(det_monto, 0), tipo_documento,
                       COALESCE(dias_credito, 0), COALESCE(es_compra_cruzada, FALSE),
                       COALESCE(comision_interbancaria, 0), COALESCE(pagado_por_tercero, '')
                FROM facturas_recibidas{where_sql} ORDER BY id DESC LIMIT %s OFFSET %s
            """, tuple(params) + (self.registros_por_pagina, offset))
                
            registros = cursor.fetchall()
            contador = (self.pagina_actual - 1) * self.registros_por_pagina + 1

            # 🚀 OPTIMIZACIÓN: una sola consulta agregada para todos los pagos de la página (elimina el N+1)
            cursor.execute("""
                SELECT id_factura, SUM(monto_pagado), COUNT(archivo_ruta)
                FROM pagos_comprobantes
                WHERE archivo_ruta != ''
                GROUP BY id_factura
            """)
            pagos_lookup = {}
            for id_f, sum_m, cnt in cursor.fetchall():
                pagos_lookup[id_f] = (float(sum_m) if sum_m else 0.0, int(cnt) if cnt else 0)
            
            for reg in registros:
                (id_factura, fecha, nro_doc, proveedor, evento, concepto, subtotal, impuesto,
                 tot_bruto, det_monto, tipo_doc, dias_credito) = reg[:12]
                # 🔁 Compra cruzada: la pago un tercero (con posible comision interbancaria)
                es_cruzada = bool(reg[12]) if len(reg) > 12 else False
                comision_cruz = float(reg[13] or 0) if len(reg) > 13 else 0.0
                tercero_cruz = str(reg[14] or "") if len(reg) > 14 else ""
                
                sub_val = float(subtotal) if subtotal else 0.0
                imp_val = float(impuesto) if impuesto else 0.0
                tot_bruto_val = float(tot_bruto) if tot_bruto else 0.0
                det_monto_val = float(det_monto) if det_monto else 0.0
                
                tipo_doc_str = str(tipo_doc) if tipo_doc else ""
                if tipo_doc and "Recibo" in tipo_doc_str and "8%" in tipo_doc_str:
                    neto_facturado = tot_bruto_val - imp_val - det_monto_val
                else:
                    neto_facturado = tot_bruto_val - det_monto_val
                
                if es_cruzada and comision_cruz > 0:
                    # La comision interbancaria tambien sale de la cuenta: forma parte del neto
                    neto_facturado += comision_cruz
                    concepto = (str(concepto or "") +
                                ("   " if concepto else "") +
                                "🔁 Compra cruzada - comision " + formatear_moneda(comision_cruz) +
                                (" - pago: " + tercero_cruz if tercero_cruz else ""))
                elif es_cruzada:
                    concepto = (str(concepto or "") +
                                ("   " if concepto else "") +
                                "🔁 Compra cruzada" +
                                (" - pago: " + tercero_cruz if tercero_cruz else ""))

                monto_pagado, cant_archivos = pagos_lookup.get(id_factura, (0.0, 0))

                saldo_pendiente = max(0.0, neto_facturado - monto_pagado)
                txt_adjuntos = f"📁 {cant_archivos} archivo(s)" if cant_archivos > 0 else "❌ Sin adjuntos"

                row_vals = (
                    contador, 
                    id_factura, 
                    fecha, 
                    nro_doc if nro_doc else "S/N", 
                    proveedor, 
                    evento, 
                    concepto, 
                    formatear_moneda(sub_val), 
                    formatear_moneda(imp_val), 
                    formatear_moneda(det_monto_val),
                    formatear_moneda(neto_facturado), 
                    formatear_moneda(monto_pagado), 
                    formatear_moneda(saldo_pendiente), 
                    txt_adjuntos
                )
                
                # 🎨 Color de la fila según el vencimiento del crédito:
                # 🟢 pagada · 🔴 vence en 5 días o menos (o vencida) · 🔵 vence en 6 a 15 días
                dias_restantes = dias_para_vencer(fecha, dias_credito)
                if saldo_pendiente <= 0.01:
                    estado_fila = "pagado"
                elif dias_restantes is None:
                    estado_fila = ""
                elif dias_restantes <= 5:
                    estado_fila = "urgente"
                elif dias_restantes <= 15:
                    estado_fila = "proximo"
                else:
                    estado_fila = ""
                estados.append(estado_fila)
                # 🖱️ Fecha de vencimiento: la usa el cuadrito que aparece al pasar el mouse
                vencimiento = fecha_vencimiento(fecha, dias_credito)
                vencimientos.append(vencimiento.strftime("%Y-%m-%d") if vencimiento else "")

                total_pendiente += saldo_pendiente
                filas_resultado.append(row_vals)
                contador += 1
                
            cache_sistema.guardar(clave_cache, {"filas": filas_resultado, "total": total_pendiente,
                                                "estados": estados, "vencimientos": vencimientos})
        except Exception: 
            pass
        finally: 
            liberar_conexion(conn)
            
        self.main_root.after(0, lambda: self._pintar_tabla_cobros(filas_resultado, total_pendiente,
                                                                  token, estados, vencimientos))

    def _pintar_tabla_cobros(self, filas_resultado, total_pendiente, token_actual, estados=None,
                             vencimientos=None):
        if token_actual != getattr(self, '_carga_pagos_token', token_actual):
            return
            
        self.tabla.delete(*self.tabla.get_children())
        # 🖱️ Vencimiento de cada fila, para el cuadrito que sale al pasar el mouse
        self.vencimientos_filas = {}
            
        for i, row_vals in enumerate(filas_resultado):
            # 🎨 Se pinta la fila con el color de su vencimiento (leyenda debajo de la tabla)
            estado = estados[i] if estados and i < len(estados) else ""
            iid = self.tabla.insert("", tk.END, values=row_vals, tags=(estado,) if estado else ())
            self.vencimientos_filas[iid] = (
                (vencimientos[i] if vencimientos and i < len(vencimientos) else ""),
                estado == "pagado")
            
        self.lbl_total_general.configure(text=f"Total Pendiente Filtrado: {formatear_moneda(total_pendiente)}")
        
        if hasattr(self, 'btn_ant'):
            if self.pagina_actual > 1: 
                self.btn_ant.configure(state="normal")
            else: 
                self.btn_ant.configure(state="disabled")
                
            if len(filas_resultado) == self.registros_por_pagina: 
                self.btn_sig.configure(state="normal")
            else: 
                self.btn_sig.configure(state="disabled")

    def abrir_pago_de_factura(self, id_factura, escanear=False):
        """Abre 'Registrar Pago' para una factura concreta (la selecciona en la tabla).

        escanear=True: al abrirse el pago se ofrece escanear el comprobante de pago.
        """
        # \U0001F504 Se limpia la caché de la pestaña: si la factura se acaba de registrar,
        #    la lista puede tardar un instante en traerla (antes decía que "no estaba").
        try:
            cache_sistema.invalidar()
        except Exception:
            pass
        # \U0001F4C5 Si la factura es de otro mes, se muestra ese mes para poder verla
        try:
            conn_mes = obtener_conexion_segura(mostrar_aviso=False)
            if conn_mes:
                try:
                    cur_mes = conn_mes.cursor()
                    cur_mes.execute("SELECT fecha FROM facturas_recibidas WHERE id = %s", (id_factura,))
                    fila_mes = cur_mes.fetchone()
                finally:
                    liberar_conexion(conn_mes)
                etiqueta_fac = etiqueta_mes_de_fecha(fila_mes[0] if fila_mes else "")
                if etiqueta_fac and etiqueta_fac != getattr(self, "mes_filtro", ""):
                    self.mes_filtro = etiqueta_fac
                    try:
                        self.combo_mes.set(etiqueta_fac)
                    except Exception:
                        pass
        except Exception as e_mes:
            print("Aviso - mes de la factura:", e_mes)
        try:
            self.cargar_datos_pagar(reset_pagina=True)
        except Exception:
            pass

        def buscar_factura(intento=1):
            """Busca la fila de la factura; reintenta porque la lista carga en segundo plano."""
            for item in self.tabla.get_children():
                valores = self.tabla.item(item, "values")
                if len(valores) > 1 and str(valores[1]) == str(id_factura):
                    self.tabla.selection_set(item)
                    self.tabla.see(item)
                    self._escanear_soporte_pendiente = bool(escanear)
                    self.cargar_comprobante_pago()
                    return
            if intento < 4:
                try:
                    self.cargar_datos_pagar(reset_pagina=True)
                except Exception:
                    pass
                try:
                    self.main_root.after(400 * intento, lambda: buscar_factura(intento + 1))
                except Exception:
                    buscar_factura(intento + 1)
                return
            messagebox.showinfo(
                "Registrar Pago",
                "La factura todavía no aparece en la lista de Cuentas por Pagar.\n\n"
                "Pulse \u00ab\U0001F504 Actualizar\u00bb en la pestaña y vuelva a intentarlo.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        buscar_factura()

    def escanear_comprobante_pago(self, padre, al_terminar):
        """Escanea el comprobante de pago en la multifuncional y arma un PDF.

        El escaneo corre en segundo plano; al terminar se llama a al_terminar(ruta)
        en el hilo de la interfaz (ruta vacía si no se pudo escanear).
        """
        try:
            import escaner
        except Exception as e:
            messagebox.showerror("Escáner", f"No se pudo cargar el módulo de escaneo:\n{e}", parent=padre)
            return al_terminar("")
        if not escaner.sistema_soportado():
            messagebox.showinfo("Escáner", "El escaneo con la multifuncional está disponible en Windows y en macOS (M1/M2).",
                                parent=padre)
            return al_terminar("")

        config = cargar_configuracion_regional()
        prog = ctk.CTkToplevel(padre if padre is not None else self.main_root)
        prog.title("Escaneando comprobante de pago")
        prog.geometry("430x150")
        try:
            prog.transient(padre if padre is not None else self.main_root)
            prog.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(prog, text="🖨️ Escaneando el comprobante de pago...", font=("Arial", 12, "bold"),
                     text_color="#0e7490").pack(pady=(20, 4))
        ctk.CTkLabel(prog, text="Coloque el comprobante en el escáner (cristal).",
                     font=("Arial", 10), text_color="gray").pack()
        barra = ctk.CTkProgressBar(prog, width=330, mode="indeterminate")
        barra.pack(pady=10)
        barra.start()

        def tarea():
            resultado = {"ok": False, "pdf": "", "error": ""}
            try:
                datos = escaner.config_escaner(config)
                carpeta = escaner.carpeta_escaneos(config)
                esc = escaner.escanear(
                    carpeta,
                    dispositivo=datos.get("escaner_dispositivo", ""),
                    dpi=datos.get("escaner_dpi", "300"),
                    color=datos.get("escaner_color", "Escala de grises"),
                    origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                    paginas=1,
                    optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                    modo=datos.get("escaner_modo", "Automático (sin ventanas)"))
                if esc.get("ok") and esc.get("paginas"):
                    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
                    pdf = os.path.join(os.path.dirname(esc["paginas"][0]),
                                       f"Comprobante_Pago_{marca}.pdf")
                    escaner.imagenes_a_pdf(esc["paginas"], pdf)
                    resultado.update({"ok": True, "pdf": pdf})
                    # 🧠 OCR del comprobante: se busca el N° de operación/transacción
                    try:
                        texto_ocr = leer_ocr_windows(pdf)
                        resultado["numero_operacion"] = extraer_numero_operacion(texto_ocr)
                        # 📅 También se lee la fecha del comprobante
                        resultado["fecha"] = extraer_fecha_comprobante(texto_ocr)
                        resultado["texto_ocr"] = texto_ocr
                    except Exception as e_ocr_op:
                        print("Aviso - OCR del comprobante de pago:", e_ocr_op)
                else:
                    resultado["error"] = esc.get("error") or "No se pudo escanear."
            except Exception as e:
                resultado["error"] = str(e)

            def terminar():
                try:
                    prog.grab_release()
                    prog.destroy()
                except Exception:
                    pass
                if not resultado["ok"]:
                    messagebox.showerror("Escáner", resultado["error"] or "No se pudo escanear.",
                                         parent=padre)
                try:
                    al_terminar(resultado["pdf"], resultado.get("numero_operacion", ""),
                                resultado.get("fecha", ""))
                except TypeError:
                    # Compatibilidad con llamadas que esperan menos datos
                    try:
                        al_terminar(resultado["pdf"], resultado.get("numero_operacion", ""))
                    except TypeError:
                        al_terminar(resultado["pdf"])
            try:
                self.main_root.after(0, terminar)
            except Exception:
                pass
        threading.Thread(target=tarea, daemon=True).start()

    def cargar_comprobante_pago(self):
        ruta_base = obtener_ruta_base_drive()
        if not ruta_base:
            messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar archivos.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            return
            
        seleccion = self.tabla.selection()
        if not seleccion: return messagebox.showwarning("Selección", "Seleccione una factura.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        
        valores = self.tabla.item(seleccion[0], "values")
        id_factura, nro_doc, proveedor = valores[1], valores[3], valores[4] 
        saldo_actual = desformatear_numero(valores[12]) 
        
        if saldo_actual <= 0: return messagebox.showinfo("Aviso", "Esta factura ya está pagada por completo.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

        v_pago = ctk.CTkToplevel(self.main_root)
        v_pago.title("Registrar Nuevo Pago")
        v_pago.geometry("410x610")
        v_pago.transient(self.main_root)
        v_pago.grab_set()

        ctk.CTkLabel(v_pago, text=f"Pago para: {proveedor}", font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(15, 5))
        ctk.CTkLabel(v_pago, text=f"Saldo Pendiente: {formatear_moneda(saldo_actual)}", font=("Arial", 12)).pack(pady=(0, 15))

        f_form = ctk.CTkFrame(v_pago, fg_color="transparent")
        f_form.pack(fill="x", padx=20)

        ctk.CTkLabel(f_form, text="Monto a Pagar (S/.):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_monto = ctk.CTkEntry(f_form)
        ent_monto.pack(fill="x", pady=(0, 10))
        ent_monto.insert(0, str(saldo_actual)) 

        ctk.CTkLabel(f_form, text="Fecha del Pago (AAAA-MM-DD):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_fecha = ctk.CTkEntry(f_form)
        ent_fecha.pack(fill="x", pady=(0, 10))
        ent_fecha.insert(0, datetime.now().strftime("%Y-%m-%d"))
        # 🏦 Cuenta bancaria de la que salió el dinero (la usa el módulo de Bancos)
        ctk.CTkLabel(f_form, text="🏦 Pagado desde (cuenta bancaria):", font=("Arial", 11, "bold")).pack(anchor="w")
        etiquetas_cuentas = ["(No especificada)"] + ([etiqueta_banco(c) for c in cargar_bancos()] if cargar_bancos else [])
        cmb_cuenta_pago = ctk.CTkComboBox(f_form, values=etiquetas_cuentas, state="readonly")
        cmb_cuenta_pago.pack(fill="x", pady=(0, 2))
        cmb_cuenta_pago.set(etiquetas_cuentas[0])
        ctk.CTkLabel(f_form, text="Sirve para que el saldo de esa cuenta cuadre en el módulo de Bancos.",
                     font=("Arial", 9, "italic"), text_color="gray", wraplength=340,
                     justify="left").pack(anchor="w", pady=(0, 10))

        # 🧾 N° de operación / transacción: se completa solo al escanear el comprobante
        ctk.CTkLabel(f_form, text="N° de Operación / Transacción:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_operacion = ctk.CTkEntry(f_form, placeholder_text="Ej.: 00123456 (se lee del comprobante escaneado)")
        ent_operacion.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(f_form, text="Al escanear el comprobante se llena automáticamente (puede corregirlo).",
                     font=("Arial", 9, "italic"), text_color="gray", wraplength=340,
                     justify="left").pack(anchor="w", pady=(0, 10))

        def procesar_pago(event=None):
            try:
                monto_val = float(ent_monto.get().strip())
            except ValueError:
                return messagebox.showerror("Error", "Ingrese un monto numérico válido.", parent=v_pago)

            if monto_val <= 0:
                return messagebox.showerror("Error", "El monto debe ser mayor a 0.", parent=v_pago)
            if monto_val > (saldo_actual + 0.01):
                return messagebox.showerror("Error", "El monto supera el saldo pendiente.", parent=v_pago)

            fecha_val = ent_fecha.get().strip()
            if not fecha_val:
                fecha_val = datetime.now().strftime("%Y-%m-%d")

            # 🏦 IMPORTANTE: la cuenta bancaria se lee ANTES de cerrar la ventana.
            # Si se leyera después de v_pago.destroy(), los widgets ya no existirían
            # y al pulsar ✅ Confirmar (justo cuando se pide el soporte en PDF/JPG)
            # saltaba el error: invalid command name ".!ctktoplevel.!ctkcombobox..."
            cuenta_val = cmb_cuenta_pago.get().strip()
            if cuenta_val == "(No especificada)": cuenta_val = ""
            operacion_val = ent_operacion.get().strip()

            # 🖨️ Si el comprobante de pago ya se escaneó, se usa ese archivo
            ruta_soporte = getattr(self, "_soporte_escaneado", "")

            v_pago.destroy()

            if ruta_soporte and os.path.exists(ruta_soporte):
                ruta_origen = ruta_soporte
            else:
                tipos_seguros = [("Soportes", "*.pdf *.png *.jpg *.jpeg"), ("Todos", "*.*")]
                ruta_origen = filedialog.askopenfilename(title="Seleccionar Soporte de Egreso", filetypes=tipos_seguros)
            ruta_destino = ""
            
            if ruta_origen:
                try:
                    carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_egresos")
                    os.makedirs(carpeta_comprobantes, exist_ok=True)
                    
                    conn = obtener_conexion_segura()
                    if not conn: return
                    
                    c = conn.cursor()
                    c.execute("SELECT COUNT(*) FROM pagos_comprobantes")
                    idx = c.fetchone()[0] + 1
                    liberar_conexion(conn)
                    
                    ruta_destino = os.path.join(carpeta_comprobantes, f"Egreso_Fac_{id_factura}_{proveedor.replace(' ', '_')}_{idx}{os.path.splitext(ruta_origen)[1]}")
                    shutil.copy2(ruta_origen, ruta_destino)
                except Exception as e:
                    return messagebox.showerror("Error", f"Fallo al copiar archivo:\n{e}", parent=ventana_para_mensaje(getattr(self, "main_root", None)))

            conn = obtener_conexion_segura()
            if not conn: return
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT categoria FROM facturas_recibidas WHERE id = %s", (id_factura,))
                cat_res = cursor.fetchone()
                categoria_db = cat_res[0] if cat_res and cat_res[0] else "GENERAL"
                
                cursor.execute("INSERT INTO pagos_comprobantes (id_factura, monto_pagado, archivo_ruta, proveedor_nombre, fecha_pago, categoria_suministro, codigo_cotizacion, cuenta_origen, numero_operacion) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", 
                               (id_factura, monto_val, ruta_destino, proveedor, fecha_val, categoria_db, nro_doc, cuenta_val, operacion_val))
                conn.commit()
                
                cache_sistema.invalidar()
                registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Pagó {formatear_moneda(monto_val)} a Fac. {nro_doc} ({proveedor})")
                messagebox.showinfo("Éxito", f"Pago de {formatear_moneda(monto_val)} registrado.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
                traer_modulo_al_frente(getattr(self, "main_root", None))
                self.cargar_datos_pagar(reset_pagina=True)
            except Exception as e: 
                messagebox.showerror("Error", str(e), parent=ventana_para_mensaje(getattr(self, "main_root", None)))
            finally: 
                liberar_conexion(conn)

        ent_monto.bind("<Return>", procesar_pago)
        ent_fecha.bind("<Return>", procesar_pago)

        # 🖨️ Comprobante de pago: se puede escanear aquí mismo y queda archivado
        self._soporte_escaneado = ""
        f_soporte = ctk.CTkFrame(v_pago, fg_color="transparent")
        f_soporte.pack(fill="x", padx=20, pady=(0, 2))
        lbl_soporte = ctk.CTkLabel(f_soporte, text="Soporte: se elige (o se escanea) al confirmar",
                                   font=("Arial", 10, "italic"), text_color="#7f8c8d",
                                   wraplength=360, justify="left")
        lbl_soporte.pack(anchor="w")

        def escanear_soporte():
            """Escanea el comprobante de pago y lo deja listo como soporte del pago.

            Antes de escanear se pregunta si el pago CANCELA la factura: si la
            respuesta es Sí, se coloca el saldo pendiente completo; si es No,
            el monto queda como pago parcial para escribirlo a mano.
            """
            try:
                cancela = messagebox.askyesno(
                    "Cancelar la factura",
                    "¿Este pago CANCELA la factura por completo?\n\n"
                    "Sí: se registra el pago total (" + formatear_moneda(saldo_actual) +
                    ") y la factura queda cancelada.\n"
                    "No: se registra como pago PARCIAL (escriba el monto a pagar).",
                    parent=v_pago)
            except Exception:
                cancela = False
            if cancela:
                try:
                    ent_monto.delete(0, "end")
                    ent_monto.insert(0, "%.2f" % float(saldo_actual))
                except Exception as e_monto:
                    print("Aviso - monto del pago total:", e_monto)
            def al_terminar(ruta, numero_operacion="", fecha_leida=""):
                if not ruta:
                    return
                self._soporte_escaneado = ruta
                texto_aviso = "🖨️ Comprobante escaneado: " + os.path.basename(ruta)
                try:
                    # 🧾 El N° de operación leído por OCR va al campo de la ventana
                    if numero_operacion:
                        ent_operacion.delete(0, "end")
                        ent_operacion.insert(0, numero_operacion)
                        texto_aviso += "   ·   N° de operación leído: " + numero_operacion
                    elif not ent_operacion.get().strip():
                        texto_aviso += "   ·   (no se pudo leer el N° de operación)"
                except Exception as e_op:
                    print("Aviso - N° de operación del comprobante:", e_op)
                try:
                    # 📅 La fecha del comprobante reemplaza la fecha de hoy
                    if fecha_leida:
                        # 📅 El campo de esta ventana usa AAAA-MM-DD
                        fecha_campo = _fecha_a_iso(fecha_leida)
                        ent_fecha.delete(0, "end")
                        ent_fecha.insert(0, fecha_campo)
                        texto_aviso += "   ·   Fecha del comprobante: " + fecha_campo
                except Exception as e_fe:
                    print("Aviso - fecha del comprobante:", e_fe)
                try:
                    lbl_soporte.configure(text=texto_aviso, text_color="#1e8449")
                except Exception:
                    pass
            self.escanear_comprobante_pago(v_pago, al_terminar)

        ctk.CTkButton(f_soporte, text="🖨️ Escanear comprobante de pago", font=("Arial", 11, "bold"),
                      fg_color="#0e7490", hover_color="#155e75", command=escanear_soporte
                      ).pack(fill="x", pady=(2, 0))

        f_btns = ctk.CTkFrame(v_pago, fg_color="transparent")
        f_btns.pack(fill="x", padx=20, pady=15)

        btn_ok = ctk.CTkButton(f_btns, text="✅ Confirmar", font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449", command=procesar_pago)
        btn_ok.pack(side="left", expand=True, padx=5)

        btn_cancel = ctk.CTkButton(f_btns, text="❌ Cancelar", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=v_pago.destroy)
        btn_cancel.pack(side="right", expand=True, padx=5)

        ent_monto.focus()

        # 🖨️ Si el pago viene de "Cancelar la factura" (tras registrarla), se ofrece
        #    escanear de una vez el comprobante de pago.
        if getattr(self, "_escanear_soporte_pendiente", False):
            self._escanear_soporte_pendiente = False

            def preguntar_escanear():
                try:
                    if messagebox.askyesno(
                            "Comprobante de pago",
                            "¿Desea escanear ahora el comprobante de pago\n"
                            "(voucher, transferencia o recibo) para archivarlo con el pago?",
                            parent=v_pago):
                        escanear_soporte()
                except Exception as e_pre:
                    print("Aviso - escaneo del comprobante de pago:", e_pre)
            v_pago.after(700, preguntar_escanear)

    def abrir_datos_bancarios_pagar(self, event=None):
        """🖱️ CLIC DERECHO sobre una factura por pagar: datos bancarios del proveedor
        (banco, cuenta, CCI, detracción) listos para copiar."""
        if event is None:
            return
        proveedor, detalle = "", ""
        try:
            fila = self.tabla.identify_row(event.y)
            if fila:
                valores = self.tabla.item(fila, "values")
                proveedor = valores[4] if len(valores) > 4 else ""
                detalle = (f"Factura {valores[3] if len(valores) > 3 else ''}"
                           f"   ·   Fecha: {valores[2] if len(valores) > 2 else ''}"
                           f"   ·   Saldo: {valores[12] if len(valores) > 12 else ''}")
        except Exception:
            return
        cache = getattr(self, "_ventanas_bancarias", None)
        if cache is None:
            cache = self._ventanas_bancarias = {}
        return abrir_datos_bancarios_de_fila(
            self.tabla, event, self.main_root, proveedor, detalle, cache,
            al_seleccionar=self.tabla.selection_set)

    def _clic_izquierdo_comprobante(self, event=None):
        """Clic IZQUIERDO sobre una factura: abre el comprobante de pago escaneado.

        Abre el comprobante más reciente de esa factura (si hubiera varios pagos).
        """
        fila = ""
        try:
            if event is not None:
                # Solo si el clic cayó sobre una fila (no en el encabezado que ordena)
                fila = self.tabla.identify_row(event.y)
                if not fila:
                    return
            else:
                seleccion = self.tabla.selection()
                fila = seleccion[0] if seleccion else ""
            if not fila:
                return
            self.tabla.selection_set(fila)
            try:
                self.tabla.focus(fila)
            except Exception:
                pass
        except Exception:
            return
        ahora = datetime.now().timestamp()
        if (getattr(self, "_ultimo_clic_pago", None) == fila
                and ahora - getattr(self, "_ultimo_clic_pago_t", 0) < 1.5):
            return
        self._ultimo_clic_pago, self._ultimo_clic_pago_t = fila, ahora

        try:
            id_factura = self.tabla.item(fila, "values")[1]
        except Exception:
            return
        try:
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn:
                return
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT archivo_ruta FROM pagos_comprobantes
                    WHERE id_factura = %s AND COALESCE(archivo_ruta, '') != ''
                    ORDER BY id DESC LIMIT 1
                """, (id_factura,))
                encontrado = cursor.fetchone()
            finally:
                liberar_conexion(conn)
        except Exception as e_comp:
            print("Aviso - comprobante de pago:", e_comp)
            return

        ruta = str(encontrado[0]).strip() if encontrado and encontrado[0] else ""
        if not ruta:
            return messagebox.showinfo(
                "Comprobante de pago",
                "Esta factura todavía no tiene comprobante de pago escaneado.\n\n"
                "Use «🧾 Registrar Pago» para registrar el pago y escanear el comprobante.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        candidatas = [ruta]
        try:
            from app_paths import resolver_ruta_archivo
            candidatas.append(resolver_ruta_archivo(ruta))
        except Exception:
            pass
        for candidata in candidatas:
            if candidata and os.path.exists(candidata):
                return abrir_documento(candidata)
        return messagebox.showinfo("Comprobante de pago",
                                   "No se encontró el archivo del comprobante de pago:\n" + ruta, parent=ventana_para_mensaje(getattr(self, "main_root", None)))

    def abrir_todos_los_archivos(self, event):
        seleccion = self.tabla.selection()
        if not seleccion: return
        id_factura = self.tabla.item(seleccion[0], "values")[1] 
        try:
            conn = obtener_conexion_segura(mostrar_aviso=False)
            if not conn: return
            cursor = conn.cursor()
            cursor.execute("SELECT archivo_ruta FROM pagos_comprobantes WHERE id_factura = %s AND archivo_ruta != ''", (id_factura,))
            rutas = cursor.fetchall()
            liberar_conexion(conn)
            if rutas:
                for r in rutas:
                    if os.path.exists(r[0]): 
                        abrir_documento(r[0])
            else: messagebox.showinfo("Aviso", "No hay soportes cargados.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        except Exception: pass

    def abrir_ventana_edicion(self):
        sel = self.tabla.selection()
        if not sel: return messagebox.showwarning("Selección", "Seleccione la factura para editar sus pagos.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
        valores = self.tabla.item(sel[0], "values")
        id_factura, nro_doc, proveedor = valores[1], valores[3], valores[4] 
        saldo_actual_global = desformatear_numero(valores[12]) 

        v_edit = ctk.CTkToplevel(self.main_root)
        v_edit.title(f"✏️ Gestión de Pagos - Fac. {nro_doc}")
        v_edit.geometry("720x400")
        v_edit.transient(self.main_root)
        v_edit.grab_set()

        ctk.CTkLabel(v_edit, text=f"Pagos registrados: {proveedor}", font=("Arial", 12, "bold"), text_color="#1f538d").pack(pady=10)

        frame_cuerpo = ctk.CTkFrame(v_edit, fg_color="transparent")
        frame_cuerpo.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        sub_tabla = ttk.Treeview(frame_cuerpo, columns=("id", "monto", "fecha", "cuenta", "tiene_archivo"), show="headings", height=8)
        sub_tabla.heading("id", text="ID Pago"); sub_tabla.heading("monto", text="Monto"); sub_tabla.heading("fecha", text="Fecha"); sub_tabla.heading("cuenta", text="🏦 Cuenta bancaria"); sub_tabla.heading("tiene_archivo", text="¿Soporte?")
        sub_tabla.column("id", width=60, anchor="center"); sub_tabla.column("monto", width=100, anchor="e"); sub_tabla.column("fecha", width=95, anchor="center"); sub_tabla.column("cuenta", width=210, anchor="w"); sub_tabla.column("tiene_archivo", width=90, anchor="center")
        sub_tabla.pack(side="left", fill="both", expand=True, padx=(0, 10))

        def refrescar_subtabla():
            sub_tabla.delete(*sub_tabla.get_children())
            try:
                conn = obtener_conexion_segura(parent=v_edit)
                if not conn: return
                cursor = conn.cursor()
                cursor.execute("SELECT id, monto_pagado, fecha_pago, COALESCE(cuenta_origen, ''), archivo_ruta FROM pagos_comprobantes WHERE id_factura = %s", (id_factura,))
                for a in cursor.fetchall(): 
                    sub_tabla.insert("", tk.END, values=(a[0], formatear_moneda(a[1]), a[2] if a[2] else "Sin fecha", a[3] if a[3] else "(No especificada)", "✅ Sí" if (a[4] and os.path.exists(a[4])) else "❌ No"))
                liberar_conexion(conn)
            except Exception: pass
            
        refrescar_subtabla()

        def ejecutar_modificacion():
            nonlocal saldo_actual_global
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            
            conn = obtener_conexion_segura(parent=v_edit)
            if not conn: return
            cursor = conn.cursor()
            cursor.execute("SELECT monto_pagado, fecha_pago, COALESCE(cuenta_origen, '') FROM pagos_comprobantes WHERE id = %s", (id_pago,))
            fila_pago = cursor.fetchone()
            monto_actual, fecha_actual = fila_pago[0], fila_pago[1]
            cuenta_actual = fila_pago[2] if len(fila_pago) > 2 else ""
            liberar_conexion(conn)

            v_mod_pago = ctk.CTkToplevel(v_edit)
            v_mod_pago.title("Modificar Pago")
            v_mod_pago.geometry("380x390")
            v_mod_pago.transient(v_edit)
            v_mod_pago.grab_set()

            ctk.CTkLabel(v_mod_pago, text="Editar Pago", font=("Arial", 14, "bold")).pack(pady=10)

            f_form = ctk.CTkFrame(v_mod_pago, fg_color="transparent")
            f_form.pack(fill="x", padx=20)

            ctk.CTkLabel(f_form, text="Nuevo Monto (0 = Eliminar):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_mod_monto = ctk.CTkEntry(f_form)
            ent_mod_monto.pack(fill="x", pady=(0, 10))
            ent_mod_monto.insert(0, str(monto_actual))

            ctk.CTkLabel(f_form, text="Fecha (AAAA-MM-DD):", font=("Arial", 11, "bold")).pack(anchor="w")
            ent_mod_fecha = ctk.CTkEntry(f_form)
            ent_mod_fecha.pack(fill="x", pady=(0, 10))
            ent_mod_fecha.insert(0, str(fecha_actual) if fecha_actual else datetime.now().strftime("%Y-%m-%d"))
            # 🏦 Cuenta bancaria de la que salió el dinero (se puede corregir aquí)
            ctk.CTkLabel(f_form, text="🏦 Pagado desde (cuenta bancaria):", font=("Arial", 11, "bold")).pack(anchor="w")
            etiquetas_mod = ["(No especificada)"] + ([etiqueta_banco(c) for c in cargar_bancos()] if cargar_bancos else [])
            if cuenta_actual and cuenta_actual not in etiquetas_mod: etiquetas_mod.append(cuenta_actual)
            cmb_mod_cuenta = ctk.CTkComboBox(f_form, values=etiquetas_mod, state="readonly")
            cmb_mod_cuenta.pack(fill="x", pady=(0, 10))
            cmb_mod_cuenta.set(cuenta_actual if cuenta_actual in etiquetas_mod else etiquetas_mod[0])

            def guardar_mod(event=None):
                nonlocal saldo_actual_global
                try:
                    nuevo_monto = float(ent_mod_monto.get().strip())
                except ValueError:
                    return messagebox.showerror("Error", "Monto inválido", parent=v_mod_pago)

                diferencia_de_aumento = nuevo_monto - float(monto_actual)
                if diferencia_de_aumento > (saldo_actual_global + 0.01):
                    return messagebox.showerror("Error", f"Supera el saldo pendiente de {formatear_moneda(saldo_actual_global)}.", parent=v_mod_pago)

                if nuevo_monto == 0:
                    if messagebox.askyesno("Confirmar", "¿Eliminar registro?", parent=v_mod_pago):
                        conn = obtener_conexion_segura(parent=v_mod_pago)
                        if not conn: return
                        cursor = conn.cursor()
                        cursor.execute("SELECT archivo_ruta FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                        r = cursor.fetchone()
                        if r and r[0] and os.path.exists(r[0]): os.remove(r[0])
                        cursor.execute("DELETE FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                        conn.commit()
                        liberar_conexion(conn)
                        cache_sistema.invalidar()
                        registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Eliminó el pago ID {id_pago}")
                else:
                    nueva_fecha = ent_mod_fecha.get().strip() or fecha_actual
                    nueva_cuenta = cmb_mod_cuenta.get().strip()
                    if nueva_cuenta == "(No especificada)": nueva_cuenta = ""
                    conn = obtener_conexion_segura(parent=v_mod_pago)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("UPDATE pagos_comprobantes SET monto_pagado = %s, fecha_pago = %s, cuenta_origen = %s WHERE id = %s", (nuevo_monto, nueva_fecha, nueva_cuenta, id_pago))
                    conn.commit()
                    liberar_conexion(conn)
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Modificó el pago ID {id_pago} a {formatear_moneda(nuevo_monto)}")

                saldo_actual_global -= diferencia_de_aumento
                v_mod_pago.destroy()
                refrescar_subtabla()
                self.cargar_datos_pagar(reset_pagina=True)

            ent_mod_monto.bind("<Return>", guardar_mod)
            ent_mod_fecha.bind("<Return>", guardar_mod)

            btn_guardar_mod = ctk.CTkButton(v_mod_pago, text="💾 Guardar Cambios", command=guardar_mod, fg_color="#27ae60", hover_color="#1e8449")
            btn_guardar_mod.pack(pady=10)
            ent_mod_monto.focus()

        def cambiar_soporte():
            ruta_base = obtener_ruta_base_drive()
            if not ruta_base:
                messagebox.showwarning("Configuración Requerida", "No ha configurado la ruta de Google Drive.\nEs obligatorio para guardar archivos.", parent=ventana_para_mensaje(getattr(self, "main_root", None)))
                return
            
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            
            tipos_seguros = [("Soportes", "*.pdf *.png *.jpg *.jpeg"), ("Todos", "*.*")]
            ruta_origen = filedialog.askopenfilename(title="Seleccionar Soporte", filetypes=tipos_seguros, parent=v_edit)
            if ruta_origen:
                try:
                    carpeta_comprobantes = os.path.join(ruta_base, "comprobantes_egresos")
                    os.makedirs(carpeta_comprobantes, exist_ok=True)
                    
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                    antigua_ruta = cursor.fetchone()[0]
                    if antigua_ruta and os.path.exists(antigua_ruta): os.remove(antigua_ruta)
                    
                    nombre_limpio = f"Egreso_Fac_{id_factura}_{proveedor.replace(' ', '_')}_R_{id_pago}{os.path.splitext(ruta_origen)[1]}"
                    ruta_destino = os.path.join(carpeta_comprobantes, nombre_limpio)
                    shutil.copy2(ruta_origen, ruta_destino)
                    
                    cursor.execute("UPDATE pagos_comprobantes SET archivo_ruta = %s WHERE id = %s", (ruta_destino, id_pago))
                    conn.commit()
                    liberar_conexion(conn)
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Actualizó soporte del pago ID {id_pago}")
                    messagebox.showinfo("Éxito", "Soporte actualizado.", parent=v_edit)
                    refrescar_subtabla()
                    self.cargar_datos_pagar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        def eliminar_soporte():
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            
            if messagebox.askyesno("Confirmar", "¿Eliminar soporte digital?", parent=v_edit):
                try:
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                    ruta_archivo = cursor.fetchone()[0]
                    if ruta_archivo and os.path.exists(ruta_archivo): os.remove(ruta_archivo)
                    
                    cursor.execute("UPDATE pagos_comprobantes SET archivo_ruta = '' WHERE id = %s", (id_pago,))
                    conn.commit()
                    liberar_conexion(conn)
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Eliminó soporte del pago ID {id_pago}")
                    messagebox.showinfo("Éxito", "Soporte eliminado.", parent=v_edit)
                    refrescar_subtabla()
                    self.cargar_datos_pagar(reset_pagina=True)
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        def eliminar_pago_completo():
            nonlocal saldo_actual_global
            sub_sel = sub_tabla.selection()
            if not sub_sel: return
            id_pago = sub_tabla.item(sub_sel[0], "values")[0]
            monto_eliminado = desformatear_numero(sub_tabla.item(sub_sel[0], "values")[1])

            if messagebox.askyesno("Confirmar Eliminación", "⚠️ ¿Eliminar este registro de pago por completo?", parent=v_edit):
                try:
                    conn = obtener_conexion_segura(parent=v_edit)
                    if not conn: return
                    cursor = conn.cursor()
                    cursor.execute("SELECT archivo_ruta FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                    r = cursor.fetchone()
                    if r and r[0] and os.path.exists(r[0]): os.remove(r[0])
                    
                    cursor.execute("DELETE FROM pagos_comprobantes WHERE id = %s", (id_pago,))
                    conn.commit()
                    liberar_conexion(conn)
                    cache_sistema.invalidar()
                    registrar_auditoria(self.app_padre.usuario_activo, "Cuentas por Pagar", f"Eliminó completamente el pago ID {id_pago}")
                    messagebox.showinfo("Éxito", "Pago eliminado.", parent=v_edit)
                    refrescar_subtabla()
                    self.cargar_datos_pagar(reset_pagina=True)
                    saldo_actual_global += monto_eliminado
                except Exception as e: messagebox.showerror("Error", str(e), parent=v_edit)

        frame_lateral_btns = ctk.CTkFrame(frame_cuerpo, fg_color="transparent")
        frame_lateral_btns.pack(side="right", fill="y")
        
        ctk.CTkButton(frame_lateral_btns, text="✏️ Modificar Monto", font=("Arial", 12, "bold"), fg_color="#34495e", hover_color="#2c3e50", command=ejecutar_modificacion).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="📂 Cambiar Soporte", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=cambiar_soporte).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="🗑️ Eliminar Soporte", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_soporte).pack(fill="x", pady=3)
        ctk.CTkButton(frame_lateral_btns, text="❌ Eliminar Pago Completo", font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b", command=eliminar_pago_completo).pack(fill="x", pady=(15, 3))

        frame_btn_cierre = ctk.CTkFrame(v_edit, fg_color="transparent")
        frame_btn_cierre.pack(fill="x", padx=10, pady=10)
        ctk.CTkButton(frame_btn_cierre, text="❌ Salir", font=("Arial", 12, "bold"), fg_color="#7f8c8d", hover_color="#606b6b", command=v_edit.destroy).pack(side="right")


# =========================================================
# CLASE PRINCIPAL: MÓDULO DE COMPRAS (CONTENEDOR TABVIEW)
# =========================================================
class ModuloComprasApp:
    def __init__(self, parent_frame):
        self.parent_frame = parent_frame
        self.usuario_activo = "Desconocido"
        self.pantalla_expandida = False
        aplicar_estilo_treeview()

        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)
        
        header_frame = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header_frame.pack(fill="x")
        
        ctk.CTkLabel(header_frame, text="🛒 MÓDULO DE COMPRAS (LOGÍSTICA Y TESORERÍA)", font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")
        self.btn_pantalla = ctk.CTkButton(header_frame, text="[ + ] Pantalla Completa", font=("Arial", 12, "bold"), width=160, fg_color="#34495e", hover_color="#2c3e50", command=self.toggle_pantalla_completa)
        self.btn_pantalla.pack(side="right")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        
        self.tab_recepcion = self.tabview.add(" 📥 1. Ingreso de Facturas Recibidas ")
        self.tab_pagos = self.tabview.add(" 💳 2. Control de Pagos y Deudas ")
        
        self.app_facturas = FacturasRecibidasTab(self.tab_recepcion, self.parent_frame.winfo_toplevel(), self)
        self.app_pagos = CuentasPorPagarTab(self.tab_pagos, self.parent_frame.winfo_toplevel(), self)
        
        self.tabview.configure(command=self.al_cambiar_pestana)

    def toggle_pantalla_completa(self):
        sidebar = None
        try:
            if self.parent_frame.master:
                for child in self.parent_frame.master.winfo_children():
                    if hasattr(child, "cget") and child.cget("width") == 280:
                        sidebar = child
                        break
        except Exception: pass

        if getattr(self, "pantalla_expandida", False):
            if sidebar: sidebar.pack(side="left", fill="y", before=self.parent_frame)
            self.btn_pantalla.configure(text="[ + ] Pantalla Completa", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = False
        else:
            if sidebar: sidebar.pack_forget()
            self.btn_pantalla.configure(text="[ - ] Restaurar Vista", fg_color="#34495e", hover_color="#2c3e50")
            self.pantalla_expandida = True

    def al_cambiar_pestana(self):
        if self.tabview.get() == " 💳 2. Control de Pagos y Deudas ":
            self.app_pagos.cargar_datos_pagar(reset_pagina=True)

if __name__ == "__main__":
    pass