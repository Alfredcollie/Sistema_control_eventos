# -*- coding: utf-8 -*-
"""
MODULO_BANCO.PY - MODULO DE BANCOS (SALDOS Y CONCILIACION BANCARIA)
Adaptado al SISTEMA DE CONTROL GENERAL DE EVENTOS:

- Saldo de cada cuenta bancaria configurada en:
    Ajustes de Sistema -> Configuracion General -> "Cuentas Bancarias"
    Saldo = Saldo Inicial + Ingresos Cobrados - Egresos Pagados.
- Conciliacion bancaria: subir el PDF del estado de cuenta del banco,
  extraer sus movimientos, compararlos contra los movimientos registrados
  en el sistema (cobros de Ventas y pagos de Compras) y generar un reporte.
- Permite conciliar, corregir montos/fechas y agregar movimientos faltantes
  (tanto ajustes manuales como cobros/pagos reales en el sistema).
- Cada movimiento se clasifica por CATEGORIA/CONCEPTO y por EVENTO
  (codigo de cotizacion), que es la dimension de trabajo de este sistema:
  aqui NO se manejan vehiculos, placas ni choferes.
- Registro unificado de pagos ("Registrar Pago / Compra Cruzada"): al marcar el
  check "Es compra cruzada" el pago a un tercero se registra tambien como factura
  pagada por tercero en el modulo de Compras, adjuntando el PDF del soporte.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk
import os
import re
import json
import shutil
import calendar
import sys
import subprocess
import threading
from datetime import datetime

from conexion import conectar_db, registrar_auditoria, liberar_conexion
from app_paths import CONFIG_FILE, CONFIG_DIR
from bancos import cargar_bancos, CLAVE_CUENTAS

# 🚀 CORTE DEL SISTEMA: impide registrar movimientos anteriores a la fecha de
# comienzo configurada en Configuración General (igual que Compras y Ventas).
# 'obtener_fecha_comienzo' se usa para conciliar desde esa fecha hasta hoy.
try:
    from fecha_sistema import validar_fecha_registro, obtener_fecha_comienzo
except Exception:
    validar_fecha_registro = None
    obtener_fecha_comienzo = None


# =========================================================
# UTILIDADES DE INTEGRACION CON EL SISTEMA DE EVENTOS
# (reemplazan a los modulos externos dialogos_seguros, tareas_seguras,
#  politica_almacenamiento y config_nube del programa original)
# =========================================================
def seleccionar_archivo_dialogo(titulo="Seleccionar archivo", tipos=None, **kwargs):
    """Selector de un solo archivo (usa los dialogos nativos del sistema)."""
    try:
        return filedialog.askopenfilename(
            title=titulo, filetypes=tipos or [("Todos los archivos", "*.*")]) or ""
    except Exception:
        return ""


def seleccionar_archivos_dialogo(titulo="Seleccionar archivos", tipos=None, **kwargs):
    """Selector de varios archivos; devuelve una lista (vacia si se cancela)."""
    try:
        return list(filedialog.askopenfilenames(
            title=titulo, filetypes=tipos or [("Todos los archivos", "*.*")]) or [])
    except Exception:
        return []


def guardar_archivo_dialogo(titulo="Guardar archivo", defaultextension="", tipos=None,
                            nombre_inicial="", **kwargs):
    """Cuadro 'Guardar como...'; devuelve la ruta elegida o ''."""
    try:
        return filedialog.asksaveasfilename(
            title=titulo, defaultextension=defaultextension,
            filetypes=tipos or [("Todos los archivos", "*.*")],
            initialfile=nombre_inicial) or ""
    except Exception:
        return ""


def ejecutar_en_hilo(parent, trabajo, al_terminar=None):
    """Ejecuta 'trabajo(estado)' en segundo plano y devuelve el resultado al hilo
    principal de Tk (llamar a la interfaz desde otro hilo la congela en macOS).

    Si el trabajo falla, el error viaja en estado['error'] y la pantalla lo muestra.
    """
    estado = {}

    def _correr():
        try:
            trabajo(estado)
        except Exception as e:
            estado["error"] = str(e)
        if al_terminar is None:
            return

        def _aplicar():
            try:
                al_terminar(estado)
            except Exception as e:
                print("[Banco] Error al mostrar el resultado:", e)
        try:
            parent.after(0, _aplicar)
        except Exception as e:
            print("[Banco] No se pudo actualizar la pantalla desde el hilo:", e)

    threading.Thread(target=_correr, daemon=True).start()


def ruta_para_guardar(ruta):
    """Ruta tal como queda guardada en la base de datos."""
    if not ruta:
        return ""
    return os.path.normpath(str(ruta))


def eliminar_archivo(ruta):
    """Borra un archivo del disco sin lanzar error si ya no existe."""
    try:
        if ruta and os.path.isfile(ruta):
            os.remove(ruta)
            return True
    except Exception:
        pass
    return False


def resolver_ruta_archivo(ruta):
    """Devuelve una ruta existente del archivo.

    Si el equipo cambio (por ejemplo Mac <-> Windows) la ruta guardada ya no
    existe: en ese caso se busca el mismo nombre dentro de las carpetas del
    programa (facturas recibidas y soportes de pago).
    """
    if not ruta:
        return None
    ruta = str(ruta)
    if os.path.isfile(ruta):
        return ruta
    try:
        nombre = os.path.basename(ruta.replace("\\", "/"))
        if not nombre:
            return None
        for base in (obtener_ruta_base(), str(CONFIG_DIR)):
            if not base:
                continue
            for sub in ("facturas_recibidas", "soportes_pagos_terceros", ""):
                candidato = os.path.join(base, sub, nombre) if sub else os.path.join(base, nombre)
                if os.path.isfile(candidato):
                    return candidato
    except Exception:
        pass
    return None

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from pypdf import PdfReader as _PypdfReader
except ImportError:
    try:
        from PyPDF2 import PdfReader as _PypdfReader
    except ImportError:
        _PypdfReader = None

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

ctk.set_appearance_mode("Light")
ctk.set_default_color_theme("blue")


# =========================================================
# CONFIGURACION Y UTILIDADES
# =========================================================
def cargar_config():
    """Configuracion regional del sistema (moneda, formatos, ruta de archivos).

    Es la MISMA configuracion que usan Compras, Ventas y Configuracion General:
    config_local.json (valores por defecto) + configuracion.json del equipo.
    """
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "ruta_drive": "",
        CLAVE_CUENTAS: [],
    }
    try:
        from app_paths import cargar_config_local
        config.update(cargar_config_local() or {})
    except Exception:
        pass
    return config


CONFIG = cargar_config()


def obtener_ruta_base():
    """Carpeta de archivos del programa (misma lógica que Compras y Ventas).

    Usa la ruta de Google Drive configurada en Configuración General y, si no
    existe o no se puede escribir, la carpeta segura de datos del usuario.
    """
    ruta = str(CONFIG.get("ruta_drive", "") or "").strip()
    if ruta:
        ruta = os.path.expanduser(ruta)
        try:
            os.makedirs(ruta, exist_ok=True)
            return ruta
        except Exception:
            pass
    try:
        return str(CONFIG_DIR)
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def eliminar_gasto_vinculado(cursor, id_gasto):
    """🔗 Borra en Compras el gasto creado por un movimiento del Banco.

    Los egresos registrados en el Banco generan su registro espejo en Compras
    (factura + pago, enlazados por 'conciliacion_bancaria.id_gasto_compras'). Al
    eliminar el movimiento bancario hay que borrar también ese gasto para que los
    dos módulos queden sincronizados. Devuelve la ruta del soporte digital, para
    borrarla después (sólo si ya no la usa ningún otro registro).

    Devuelve la tupla (ruta_del_soporte, se_borro_el_gasto).
    """
    if not id_gasto:
        return None, False
    ruta = None
    try:
        cursor.execute("SELECT archivo_ruta FROM facturas_recibidas WHERE id = %s", (id_gasto,))
        fila = cursor.fetchone()
        if fila and fila[0]:
            ruta = os.path.normpath(str(fila[0]))
    except Exception:
        ruta = None
    cursor.execute("DELETE FROM pagos_comprobantes WHERE id_factura = %s", (id_gasto,))
    cursor.execute("DELETE FROM facturas_recibidas WHERE id = %s", (id_gasto,))
    return ruta, bool(max(cursor.rowcount, 0))


def borrar_soporte_si_huerfano(ruta):
    """🗑️ Borra el archivo del soporte SÓLO si ya no lo referencia ningún registro."""
    if not ruta:
        return
    try:
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        usos = 0
        try:
            with conn.cursor() as c:
                try:
                    c.execute("""SELECT COUNT(*) FROM facturas_recibidas
                                 WHERE archivo_ruta = %s OR soporte_pago_tercero = %s""", (ruta, ruta))
                except Exception:
                    conn.rollback()
                    c.execute("SELECT COUNT(*) FROM facturas_recibidas WHERE archivo_ruta = %s", (ruta,))
                usos += int(c.fetchone()[0] or 0)
                c.execute("SELECT COUNT(*) FROM pagos_comprobantes WHERE archivo_ruta = %s", (ruta,))
                usos += int(c.fetchone()[0] or 0)
        finally:
            liberar_conexion(conn)
        if not usos:
            eliminar_archivo(ruta)
    except Exception as e:
        print("[Banco] No se pudo borrar el soporte digital:", e)


def abrir_documento(ruta):
    try:
        # Resuelve rutas guardadas en otro equipo/SO (Mac <-> Windows)
        ruta = resolver_ruta_archivo(ruta) or ruta
        ruta_abs = os.path.abspath(ruta)
        if sys.platform == "win32":
            os.startfile(ruta_abs)
        elif sys.platform == "darwin":
            subprocess.call(["open", ruta_abs])
        else:
            subprocess.call(["xdg-open", ruta_abs])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")


# Categorías de gastos por defecto para una empresa de EVENTOS.
# Son las que comparten el módulo de Bancos, Compras y los reportes; el usuario
# puede agregar/quitar categorías desde "⚙️ Gestionar Categorías".
CATEGORIAS_GASTOS_DEFAULT = {
    "Gastos Fijos": ["Alquiler de Oficina / Almacén", "Planilla / Sueldos",
                     "Servicios (Luz, Agua, Internet)", "Seguros", "Préstamos / Cuotas"],
    "Producción de Eventos": ["Alquiler de Locación", "Montaje y Desmontaje",
                              "Personal / Staff del Evento", "Sonido e Iluminación",
                              "Equipos y Mobiliario", "Decoración y Ambientación",
                              "Transporte y Traslados", "Catering / Alimentación",
                              "Otros operativos del evento"],
    "Administrativos": ["Útiles y Papelería", "Mantenimiento de Oficina",
                        "Comisiones y Gastos Bancarios", "Otros administrativos"],
    "Ingresos": ["Cobro de cliente", "Otro ingreso"],
    "Otros": ["Varios"],
}

COMISION_INTERBANCARIA_DEFAULT = "4.80"

# Marca local: indica que las tablas/columnas del Banco ya se verificaron en este
# equipo. Cada sentencia DDL es un viaje a Supabase (~0,3 s) y son ~24 seguidas,
# así que sólo se ejecutan la primera vez (o cuando cambie esta clave).
CLAVE_ESQUEMA_BANCO = "banco_esquema_ok_v4"


def leer_config_disco():
    cfg = {}
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                cfg = json.load(f)
    except Exception:
        pass
    return cfg


def guardar_config_disco(cfg):
    try:
        with open(str(CONFIG_FILE), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
        return True
    except Exception:
        return False


def cargar_categorias_gastos():
    cfg = leer_config_disco()
    if "categorias_gastos" in cfg and isinstance(cfg["categorias_gastos"], dict):
        cats = cfg["categorias_gastos"]
        return {k: (v if isinstance(v, list) else []) for k, v in cats.items()}
    return dict(CATEGORIAS_GASTOS_DEFAULT)


def guardar_categorias_gastos(cats):
    """Guarda las categorías principales y sus subcategorías en la configuración.

    Es la MISMA lista que comparten Banco, Compras y las estadísticas financieras,
    por eso se guarda en la configuración del sistema (configuracion.json)
    igual que el resto de parámetros.
    Devuelve True si se pudo escribir en el disco.
    """
    try:
        limpias = {}
        for principal, subs in (cats or {}).items():
            principal = str(principal).strip()
            if not principal:
                continue
            if isinstance(subs, str):
                subs = [subs]
            limpias[principal] = [str(s).strip() for s in (subs or []) if str(s).strip()]
        cfg = leer_config_disco()
        cfg["categorias_gastos"] = limpias
        return bool(guardar_config_disco(cfg))
    except Exception as e:
        print(f"[Categorías de gastos] No se pudieron guardar: {e}")
        return False


def abrir_gestion_categorias_gastos(parent=None, al_guardar=None):
    """Ventana "Gestionar Categorías de Gastos" (principales y subcategorías).

    Es la lista con la que el módulo de Bancos clasifica cada movimiento
    (Conciliación → Registrar Pago → "⚙️ Gestionar Categorías"). Se guarda en la
    configuración del sistema, así que se mantiene entre equipos y actualizaciones.
    La categoría elegida viaja a Compras como la categoría del gasto cuando el
    movimiento es un egreso. Si se indica "al_guardar", se ejecuta tras guardar y
    cerrar (para refrescar los combos del módulo que la abrió).
    """
    cats = cargar_categorias_gastos()
    v = ctk.CTkToplevel(parent)
    v.title("Gestionar Categorías de Gastos")
    v.geometry("640x500")
    if parent is not None:
        try:
            v.transient(parent)
        except Exception:
            pass

    def _tomar_foco():
        try:
            v.grab_set()
            v.lift()
            v.focus_force()
        except Exception:
            pass

    _tomar_foco()
    try:
        v.after(200, _tomar_foco)
    except Exception:
        pass

    ctk.CTkLabel(v, text="⚙️ Categorías (principales y subcategorías)", font=("Arial", 14, "bold"),
                 text_color="#1f538d").pack(pady=(15, 5))

    f_tabla = ctk.CTkFrame(v, fg_color="transparent")
    f_tabla.pack(fill="both", expand=True, padx=15, pady=10)
    columnas = ("principal", "categoria")
    tree = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="browse")
    tree.heading("principal", text="Principal")
    tree.heading("categoria", text="Categoría")
    tree.column("principal", width=220, anchor="w")
    tree.column("categoria", width=320, anchor="w")
    vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    def cargar_tree():
        tree.delete(*tree.get_children())
        for p, subs in cats.items():
            for s in subs:
                tree.insert("", tk.END, values=(p, s))

    cargar_tree()

    def seleccion():
        sel = tree.selection()
        if not sel:
            return None, None
        vals = tree.item(sel[0], "values")
        return vals[0], vals[1]

    def _seguro(funcion):
        """Envuelve una acción para que cualquier error se muestre (antes se perdía)."""
        def envuelto(*args, **kwargs):
            try:
                return funcion(*args, **kwargs)
            except Exception as e:
                messagebox.showerror("Categorías de Gastos",
                                     f"No se pudo completar la acción:\n{e}", parent=v)
        return envuelto

    def add_principal():
        nombre = simpledialog.askstring("Nueva principal", "Nombre de la categoría principal:", parent=v)
        if nombre:
            nombre = nombre.strip()
            if nombre and nombre not in cats:
                cats[nombre] = ["Varios"]
                cargar_tree()

    def add_sub():
        p, s = seleccion()
        if not p:
            messagebox.showinfo("Aviso", "Seleccione una fila para saber a qué principal agregar.", parent=v)
            return
        nombre = simpledialog.askstring("Nueva categoría", f"Categoría para '{p}':", parent=v)
        if nombre:
            nombre = nombre.strip()
            if nombre and nombre not in cats.get(p, []):
                cats.setdefault(p, []).append(nombre)
                cargar_tree()

    def edit():
        p, s = seleccion()
        if not p:
            messagebox.showinfo("Aviso", "Seleccione una fila para editar.", parent=v)
            return
        if s:
            nuevo = simpledialog.askstring("Editar categoría", f"Editar '{s}' en '{p}':", parent=v, initialvalue=s)
            if nuevo:
                nuevo = nuevo.strip()
                if nuevo and nuevo != s:
                    lista = cats.get(p, [])
                    if s in lista:
                        lista[lista.index(s)] = nuevo
                    cargar_tree()
        else:
            nuevo = simpledialog.askstring("Editar principal", f"Editar '{p}':", parent=v, initialvalue=p)
            if nuevo:
                nuevo = nuevo.strip()
                if nuevo and nuevo != p:
                    cats[nuevo] = cats.pop(p, [])
                    cargar_tree()

    def delete():
        p, s = seleccion()
        if not p:
            messagebox.showinfo("Aviso", "Seleccione una fila para eliminar.", parent=v)
            return
        if s:
            if messagebox.askyesno("Eliminar", f"¿Eliminar la categoría '{s}' de '{p}'?", parent=v):
                lista = cats.get(p, [])
                if s in lista:
                    lista.remove(s)
                cargar_tree()
        else:
            if messagebox.askyesno("Eliminar", f"¿Eliminar la principal '{p}' y todas sus categorías?", parent=v):
                cats.pop(p, None)
                cargar_tree()

    def guardar():
        for p in list(cats.keys()):
            if not cats.get(p):
                cats[p] = ["Varios"]
        if guardar_categorias_gastos(cats):
            v.destroy()
            if al_guardar:
                try:
                    al_guardar()
                except Exception as e:
                    print(f"No se pudo refrescar las categorías: {e}")
            messagebox.showinfo("Éxito", "Categorías guardadas.", parent=parent)
        else:
            messagebox.showerror("Error", "No se pudieron guardar las categorías.", parent=v)

    f_btns = ctk.CTkFrame(v, fg_color="transparent"); f_btns.pack(fill="x", padx=15, pady=(0, 15))
    ctk.CTkButton(f_btns, text="➕ Principal", width=120, command=_seguro(add_principal)).pack(side="left", padx=4)
    ctk.CTkButton(f_btns, text="➕ Categoría", width=120, command=_seguro(add_sub)).pack(side="left", padx=4)
    ctk.CTkButton(f_btns, text="✏️ Editar", width=100, command=_seguro(edit)).pack(side="left", padx=4)
    ctk.CTkButton(f_btns, text="🗑️ Eliminar", width=100, command=_seguro(delete)).pack(side="left", padx=4)
    ctk.CTkButton(f_btns, text="💾 Guardar y Cerrar", width=150, fg_color="#27ae60", command=_seguro(guardar)).pack(side="right", padx=4)

    return v


def cargar_comision_interbancaria():
    v = leer_config_disco().get("comision_interbancaria", COMISION_INTERBANCARIA_DEFAULT)
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return 4.8


def guardar_comision_interbancaria(valor):
    cfg = leer_config_disco()
    cfg["comision_interbancaria"] = valor
    return guardar_config_disco(cfg)


# =========================================================
# CACHE DE LISTAS (las consultas a Supabase tardan entre 0,5 s y 5 s)
# =========================================================
def leer_cache(clave):
    """Devuelve una lista guardada en la caché del sistema ( [] si no está o caducó)."""
    try:
        from buffer_memoria import cache_sistema
        return list(cache_sistema.obtener(clave) or [])
    except Exception:
        return []


def guardar_cache(clave, valores):
    """Guarda una lista en la caché del sistema (5 minutos, igual que el resto)."""
    try:
        from buffer_memoria import cache_sistema
        if valores:
            cache_sistema.guardar(clave, list(valores))
    except Exception:
        pass


def cargar_eventos(usar_cache=True):
    """Eventos a los que se puede asociar un movimiento bancario.

    Es la MISMA lista de eventos aprobados que usan Compras, Ventas y el
    dashboard ('CODIGO | Nombre del Evento'), con la opción OFICINA para los
    gastos internos que no pertenecen a ningún evento.

    (En el programa original este desplegable mostraba las PLACAS de la flota:
    este sistema trabaja por eventos, no por vehículos.)
    """
    # 1) Caché del sistema: control_general ya trae esta lista al iniciar sesión.
    eventos = leer_cache("lista_eventos_aprobados") if usar_cache else []
    if not eventos:
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                with conn.cursor() as c:
                    c.execute("""
                        SELECT codigo_cotizacion, nombre_evento
                        FROM cotizaciones
                        WHERE status = 'Aprobada'
                        ORDER BY id DESC
                    """)
                    eventos = [f"{r[0]} | {r[1]}".strip(" |") for r in c.fetchall() if r[0]]
            except Exception:
                eventos = []
            finally:
                liberar_conexion(conn)
        if "OFICINA | Trabajos Internos" not in eventos:
            eventos = ["OFICINA | Trabajos Internos"] + eventos
        guardar_cache("lista_eventos_aprobados", eventos)
    elif "OFICINA | Trabajos Internos" not in eventos:
        eventos.insert(0, "OFICINA | Trabajos Internos")
    return eventos


def es_categoria_personal(texto):
    """True si la categoría corresponde a planilla, sueldos o personal del evento.

    Con estas categorías el formulario de pago pide el NOMBRE del personal /
    beneficiario que recibe el dinero.
    """
    t = (texto or "").lower()
    return any(p in t for p in ("planilla", "sueldo", "personal", "staff", "beneficiario",
                                "trabajador", "honorario"))


def cargar_personal_eventos():
    """Personal que ya trabaja en los eventos del sistema.

    Se toma de las personas registradas en las Solicitudes a Proveedores
    (personal propuesto para cada evento), que es la nómina que maneja este
    sistema de eventos.

    (En el programa original este desplegable mostraba los CHOFERES de la flota.)
    """
    en_cache = leer_cache("banco_lista_personal")
    if en_cache:
        return en_cache
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT DISTINCT nombre_completo
                FROM personal_proveedor
                WHERE nombre_completo IS NOT NULL AND TRIM(nombre_completo) != ''
                ORDER BY nombre_completo ASC
            """)
            nombres = [str(r[0]).strip() for r in c.fetchall() if str(r[0]).strip()]
        guardar_cache("banco_lista_personal", nombres)
        return nombres
    except Exception:
        return []
    finally:
        liberar_conexion(conn)


def cargar_proveedores(usar_cache=True):
    """Proveedores registrados en el módulo de Proveedores."""
    # control_general ya deja esta lista en la caché al iniciar sesión.
    if usar_cache:
        en_cache = leer_cache("lista_proveedores_combobox")
        if en_cache:
            return en_cache
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("SELECT nombre FROM proveedores WHERE nombre IS NOT NULL AND TRIM(nombre) != '' ORDER BY nombre ASC")
            proveedores = [str(r[0]) for r in c.fetchall()]
        guardar_cache("lista_proveedores_combobox", proveedores)
        return proveedores
    except Exception:
        return []
    finally:
        liberar_conexion(conn)


def formatear_numero_entrada(texto):
    """Formatea un monto con separador de miles y decimal según la configuración,
    sin símbolo de moneda, para mostrarlo mientras el usuario escribe."""
    formato = CONFIG.get("formato_numero", "1,000.00")
    es_latino = (formato == "1.000,00")
    sep_dec = "," if es_latino else "."
    s = (texto or "").strip()
    if not s:
        return ""
    m = re.search(r"[.,](\d{1,2})$", s)
    termina_sep = bool(re.search(r"[.,]$", s))
    if m:
        parte_dec = m.group(1)
        base = s[:m.start()]
    elif termina_sep:
        parte_dec = None
        base = s[:-1]
    else:
        parte_dec = None
        base = s
    digitos = re.sub(r"\D", "", base)
    if not digitos and parte_dec is None:
        return ""
    entero = int(digitos) if digitos else 0
    ent_fmt = f"{entero:,}"
    if es_latino:
        ent_fmt = ent_fmt.replace(",", ".")
    if m:
        return f"{ent_fmt}{sep_dec}{parte_dec}"
    if termina_sep:
        return f"{ent_fmt}{sep_dec}"
    return ent_fmt


def normalizar_monto(v):
    """Convierte cualquier texto de monto a float, aceptando ambos formatos
    (1,234.56 y 1.234,56) y simbolos de moneda."""
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    s = re.sub(r"[^\d.,+-]", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        dig = re.sub(r"[^\d.]", "", s)
        try:
            return float(dig)
        except ValueError:
            return 0.0


def monto_opcional(valor):
    """Convierte un monto opcional a float (None si no se especificó)."""
    if valor is None or valor == "":
        return None
    try:
        return round(float(valor), 2)
    except (TypeError, ValueError):
        return None


def detalle_base_igv(datos):
    """(subtotal, impuesto) de un gasto cuando el usuario definió base + IGV.

    Devuelve (None, None) cuando el pago no lleva IGV, para que en Compras se
    registre como siempre (monto completo sin impuesto).
    """
    subtotal = monto_opcional((datos or {}).get("subtotal"))
    impuesto = monto_opcional((datos or {}).get("impuesto"))
    if subtotal is None or impuesto is None or impuesto <= 0:
        return None, None
    return subtotal, impuesto


def monto_desde_texto(texto):
    """Extrae el primer monto que aparezca en un texto (ej. 'Comisión S/ 12.00')."""
    coincidencia = re.search(r"\d+(?:[.,]\d+)?", str(texto or ""))
    return normalizar_monto(coincidencia.group(0)) if coincidencia else 0.0


def formatear_monto(valor):
    simbolo = CONFIG.get("simbolo_moneda", "S/.")
    formato = CONFIG.get("formato_numero", "1,000.00")
    try:
        valor = float(valor)
    except Exception:
        valor = 0.0
    signo = "-" if valor < 0 else ""
    valor = abs(valor)
    if formato == "1.000,00":
        txt = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    else:
        txt = f"{valor:,.2f}"
    return f"{signo}{simbolo} {txt}"


def normalizar_fecha(s):
    s = (s or "").strip()
    if not s:
        return ""
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return s


def clave_fecha(fecha):
    """Devuelve 'YYYY-MM-DD' si la fecha se puede interpretar; '' si no.

    Se usa para comparar/filtrar por mes sin que una fecha rara (texto libre)
    se cuele por comparación alfabética."""
    f = normalizar_fecha(fecha)
    return f if re.match(r"^\d{4}-\d{2}-\d{2}$", f or "") else ""


def construir_etiqueta_banco(b):
    return f"{b.get('banco', '')} - {b.get('cuenta', '')}".strip(" -")


def _norm_texto(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def banco_matchea(banco, texto_cuenta):
    """Determina si un texto de cuenta (cuenta_destino / cuenta_origen)
    corresponde al banco indicado."""
    t = _norm_texto(texto_cuenta)
    if not t:
        return False
    nombre = _norm_texto(banco.get("banco", ""))
    cuenta = _norm_texto(banco.get("cuenta", ""))
    etiqueta = _norm_texto(construir_etiqueta_banco(banco))
    if t == etiqueta and etiqueta:
        return True
    if nombre and nombre in t:
        return True
    if cuenta:
        digitos_cuenta = re.sub(r"\D", "", cuenta)
        digitos_t = re.sub(r"\D", "", t)
        if len(digitos_cuenta) >= 4 and digitos_cuenta in digitos_t:
            return True
    return False


def aplicar_estilo_treeview():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Treeview", background="#ffffff", foreground="#000000",
                    fieldbackground="#ffffff", bordercolor="#e0e0e0",
                    borderwidth=1, rowheight=26, font=("Arial", 10))
    style.map("Treeview", background=[("selected", "#1f538d")],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000",
                    relief="flat", font=("Arial", 10, "bold"),
                    bordercolor="#e0e0e0", borderwidth=1)


# =========================================================
# EXTRACCION Y ANALISIS DE PDF (ESTADO DE CUENTA)
# =========================================================
def extraer_texto_pdf(ruta):
    texto = ""
    if pdfplumber is not None:
        try:
            with pdfplumber.open(ruta) as pdf:
                partes = [p.extract_text() or "" for p in pdf.pages]
            texto = "\n".join(partes).strip()
        except Exception:
            texto = ""
    if not texto and fitz is not None:
        try:
            doc = fitz.open(ruta)
            partes = [page.get_text() or "" for page in doc]
            doc.close()
            texto = "\n".join(partes).strip()
        except Exception:
            texto = ""
    if not texto and _PypdfReader is not None:
        try:
            reader = _PypdfReader(ruta)
            partes = [(p.extract_text() or "") for p in reader.pages]
            texto = "\n".join(partes).strip()
        except Exception:
            texto = ""
    return texto


FECHA_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b")
MONTO_RE = re.compile(r"[-+]?\s?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|[-+]?\s?\d+(?:[.,]\d{1,2})?")

PALABRAS_ABONO = ["abono", "abon", "deposito", "depósito", "credito", "crédito", "ingreso",
                  "entrada", "transferencia recibida", "recibido", "cobro", "cobranza", "dep"]
PALABRAS_CARGO = ["cargo", "retiro", "debito", "débito", "pago", "egreso", "salida",
                  "transferencia enviada", "compra", "ret", "cargos"]


def _signo_por_palabras(linea):
    low = linea.lower()
    for p in PALABRAS_ABONO:
        if p in low:
            return 1
    for p in PALABRAS_CARGO:
        if p in low:
            return -1
    return None


def _seleccionar_monto(linea, montos):
    """montos: lista de (texto, posicion). Devuelve (valor, token) del movimiento."""
    if not montos:
        return None
    if len(montos) >= 2:
        candidatos = montos[:-1]
        vals = []
        for tok, pos in candidatos:
            v = normalizar_monto(tok)
            vals.append((v, tok))
        no_cero = [x for x in vals if abs(x[0]) > 0.0009]
        if no_cero:
            return max(no_cero, key=lambda x: abs(x[0]))
        return vals[-1]
    tok, pos = montos[0]
    return normalizar_monto(tok), tok


def parsear_movimientos(texto):
    """Heuristica para extraer movimientos de un estado de cuenta."""
    movimientos = []
    if not texto:
        return movimientos
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        low = linea.lower()
        # Encabezados / rangos de periodo no son transacciones
        if any(k in low for k in ("periodo", "period", "estado de cuenta", "saldo inicial")):
            continue
        fechas = FECHA_RE.findall(linea)
        if len(fechas) >= 2:
            continue
        mf = FECHA_RE.search(linea)
        if not mf:
            continue
        fecha = mf.group(1)
        resto = linea[:mf.start()] + " " + linea[mf.end():]
        montos = [(m.group(0), m.start()) for m in MONTO_RE.finditer(resto)]
        if not montos:
            continue
        seleccion = _seleccionar_monto(resto, montos)
        if seleccion is None:
            continue
        valor, token = seleccion
        signo_palabras = _signo_por_palabras(linea)
        signo_explicito = token.strip().startswith("+") or token.strip().startswith("-")
        if not signo_explicito and signo_palabras is not None:
            valor = signo_palabras * abs(valor)
        descripcion = re.sub(r"\s+", " ", resto).strip()
        descripcion = MONTO_RE.sub("", descripcion)
        descripcion = re.sub(r"\s+", " ", descripcion).strip(" -|")
        movimientos.append({
            "fecha": normalizar_fecha(fecha) or fecha,
            "descripcion": descripcion or "Movimiento sin descripción",
            "monto": valor,
        })
    return movimientos


def detectar_saldo_final(texto):
    """Intenta detectar el saldo final del estado de cuenta."""
    if not texto:
        return None
    lineas_saldo = [ln for ln in texto.splitlines() if "saldo" in ln.lower()]
    if not lineas_saldo:
        return None
    ultima = lineas_saldo[-1]
    montos = [(m.group(0), m.start()) for m in MONTO_RE.finditer(ultima)]
    if not montos:
        return None
    return normalizar_monto(montos[-1][0])


def extraer_datos_factura_pdf(ruta):
    """Extrae datos básicos de una factura de compra (SUNAT) desde un PDF."""
    datos = {
        "tipo_documento": "FACTURA",
        "numero_documento": "",
        "fecha": datetime.now().strftime("%d/%m/%Y"),
        "proveedor": "",
        "ruc": "",
        "total": 0.0,
    }
    texto = extraer_texto_pdf(ruta)
    if not texto:
        return datos
    if re.search(r"BOLETA\s+DE\s+VENTA", texto, re.IGNORECASE):
        datos["tipo_documento"] = "BOLETA"
    elif re.search(r"RECIBO\s+POR\s+HONORARIOS", texto, re.IGNORECASE):
        datos["tipo_documento"] = "RECIBO"
    m = re.search(r"([EFB][0-9A-Z]{3}\s*-\s*\d+)", texto)
    if m:
        datos["numero_documento"] = m.group(1).replace(" ", "")
    m = re.search(r"Fecha de Emisi[oó]n\s*[:\-]?\s*(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto, re.IGNORECASE)
    if not m:
        m = re.search(r"(\d{2})[/\-.](\d{2})[/\-.](\d{4})", texto)
    if m:
        datos["fecha"] = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    rucs = re.findall(r"(?:RUC|R\.U\.C\.)\s*[:\-]?\s*(\d{11})", texto, re.IGNORECASE)
    if rucs:
        datos["ruc"] = rucs[0]
    lineas = [re.sub(r"\s+", " ", l).strip() for l in texto.splitlines() if l.strip()]
    for l in lineas[:12]:
        if re.search(r"S\.A\.C\.|S\.A\.|E\.I\.R\.L\.|S\.R\.L\.|SAC|SRL|S\.C\.R\.L", l, re.IGNORECASE):
            datos["proveedor"] = l
            break
    if not datos["proveedor"]:
        for l in lineas[:8]:
            if re.search(r"R\.?U\.?C|DIRECCI|TEL[ÉE]FONO|CIUDAD|SE[ÑN]OR", l, re.IGNORECASE) or re.search(r"\d{11}", l):
                continue
            if len(l) > 4:
                datos["proveedor"] = l
                break
    m = re.search(r"(?:IMPORTE\s*TOTAL|TOTAL\s*A\s*PAGAR|Total Neto Recibido|TOTAL)[\s:S/\|]+([\d,\.]+)", texto, re.IGNORECASE)
    if m:
        datos["total"] = normalizar_monto(m.group(1))
    return datos


NOMBRES_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                 "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


# 🗓️ Periodo especial de la conciliación: desde la "Fecha de Comienzo del Sistema"
# configurada en Configuración General hasta el día de hoy.
ETIQUETA_DESDE_COMIENZO = "Desde la Fecha de Comienzo"


def construir_valores_mes():
    hoy = datetime.now()
    valores = ["Todos los meses"]
    for anio in (hoy.year - 1, hoy.year, hoy.year + 1):
        for nombre in NOMBRES_MESES:
            valores.append(f"{nombre} {anio}")
    return valores


def mes_actual_etiqueta():
    hoy = datetime.now()
    return f"{NOMBRES_MESES[hoy.month - 1]} {hoy.year}"


def mes_etiqueta_a_key(etiqueta):
    if not etiqueta or etiqueta == "Todos los meses":
        return None
    partes = (etiqueta or "").strip().split()
    if len(partes) < 2 or partes[0] not in NOMBRES_MESES:
        return None
    mes = NOMBRES_MESES.index(partes[0]) + 1
    anio = partes[-1]
    return f"{anio}-{mes:02d}"


# =========================================================
# CLASE PRINCIPAL
# =========================================================
def centrar_ventana(ventana, parent, ancho, alto):
    """Coloca una ventana secundaria centrada sobre su ventana padre."""
    ventana.update_idletasks()
    try:
        if parent and parent.winfo_ismapped():
            x = parent.winfo_rootx() + (parent.winfo_width() // 2) - (ancho // 2)
            y = parent.winfo_rooty() + (parent.winfo_height() // 2) - (alto // 2)
        else:
            x = (ventana.winfo_screenwidth() // 2) - (ancho // 2)
            y = (ventana.winfo_screenheight() // 2) - (alto // 2)
    except Exception:
        x = (ventana.winfo_screenwidth() // 2) - (ancho // 2)
        y = (ventana.winfo_screenheight() // 2) - (alto // 2)
    ventana.geometry(f"{ancho}x{alto}+{max(10, x)}+{max(35, y)}")


class CalendarioNativo(ctk.CTkToplevel):
    """Calendario emergente para escribir la fecha en un campo de texto.

    Se abre en el mes de la fecha que ya tenga el campo (o en el mes actual),
    resalta el día de hoy y trae un botón "Hoy" para ir a la fecha de hoy."""

    def __init__(self, parent, target_entry):
        super().__init__(parent)
        self.target_entry = target_entry
        self.title("Seleccionar Fecha")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        hoy = datetime.now()
        self.current_year = hoy.year
        self.current_month = hoy.month
        self.dia_marcado = None

        # Si el campo ya trae una fecha válida, se abre en ese mes y se resalta ese día
        texto = ""
        try:
            texto = (target_entry.get() or "").strip()
        except Exception:
            texto = ""
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", texto)
        if m:
            d, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if anio < 100:
                anio += 2000
            if 1 <= mes <= 12 and 1 <= d <= 31:
                self.current_month, self.current_year = mes, anio
                self.dia_marcado = d

        centrar_ventana(self, parent, 330, 350)

        header = ctk.CTkFrame(self, fg_color="#1f538d", corner_radius=0)
        header.pack(fill="x")

        ctk.CTkButton(header, text="<", width=28, fg_color="transparent", text_color="white",
                      hover_color="#163b65", font=("Arial", 14, "bold"),
                      command=self.mes_anterior).pack(side="left", padx=5, pady=10)

        self.cmb_mes = ctk.CTkComboBox(header, values=NOMBRES_MESES, width=105,
                                       command=self.cambiar_mes, state="readonly")
        self.cmb_mes.pack(side="left", padx=2, pady=10)

        anios = [str(y) for y in range(hoy.year - 80, hoy.year + 20)]
        self.cmb_anio = ctk.CTkComboBox(header, values=anios, width=80,
                                        command=self.cambiar_anio, state="readonly")
        self.cmb_anio.pack(side="left", padx=2, pady=10)

        ctk.CTkButton(header, text=">", width=28, fg_color="transparent", text_color="white",
                      hover_color="#163b65", font=("Arial", 14, "bold"),
                      command=self.mes_siguiente).pack(side="right", padx=5, pady=10)

        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        for i, dia in enumerate(["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]):
            ctk.CTkLabel(self.days_frame, text=dia, font=("Arial", 11, "bold"),
                         text_color="#1f538d").grid(row=0, column=i, padx=5, pady=5)

        f_pie = ctk.CTkFrame(self, fg_color="transparent")
        f_pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_pie, text="📅 Hoy", width=90, font=("Arial", 11, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.ir_a_hoy).pack(side="left")
        ctk.CTkButton(f_pie, text="✖ Cerrar", width=90, font=("Arial", 11),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="right")

        self.dibujar_mes()

    # ------------------------------------------------------------------
    def dibujar_mes(self):
        self.cmb_mes.set(NOMBRES_MESES[self.current_month - 1])
        self.cmb_anio.set(str(self.current_year))

        for widget in self.days_frame.winfo_children():
            try:
                if int(widget.grid_info()["row"]) > 0:
                    widget.destroy()
            except Exception:
                pass

        hoy = datetime.now()
        for fila, semana in enumerate(calendar.monthcalendar(self.current_year, self.current_month), start=1):
            for col, dia in enumerate(semana):
                if dia == 0:
                    continue
                es_hoy = (dia == hoy.day and self.current_month == hoy.month
                          and self.current_year == hoy.year)
                es_marcado = (self.dia_marcado == dia)
                if es_marcado:
                    color, texto_color = "#1f538d", "white"
                elif es_hoy:
                    color, texto_color = "#d4edda", "#155724"
                else:
                    color, texto_color = "transparent", "black"
                btn = ctk.CTkButton(self.days_frame, text=str(dia), width=30, height=30,
                                    fg_color=color, text_color=texto_color,
                                    hover_color="#e0e0e0", font=("Arial", 11),
                                    command=lambda d=dia: self.seleccionar(d))
                btn.grid(row=fila, column=col, padx=3, pady=2)

    def cambiar_mes(self, eleccion):
        try:
            self.current_month = NOMBRES_MESES.index(eleccion) + 1
        except ValueError:
            return
        self.dia_marcado = None
        self.dibujar_mes()

    def cambiar_anio(self, eleccion):
        try:
            self.current_year = int(eleccion)
        except (TypeError, ValueError):
            return
        self.dia_marcado = None
        self.dibujar_mes()

    def mes_anterior(self):
        self.current_month -= 1
        if self.current_month < 1:
            self.current_month, self.current_year = 12, self.current_year - 1
        self.dia_marcado = None
        self.dibujar_mes()

    def mes_siguiente(self):
        self.current_month += 1
        if self.current_month > 12:
            self.current_month, self.current_year = 1, self.current_year + 1
        self.dia_marcado = None
        self.dibujar_mes()

    def ir_a_hoy(self):
        hoy = datetime.now()
        self.current_year, self.current_month, self.dia_marcado = hoy.year, hoy.month, hoy.day
        self.dibujar_mes()

    def seleccionar(self, dia):
        try:
            self.target_entry.delete(0, tk.END)
            self.target_entry.insert(0, f"{dia:02d}/{self.current_month:02d}/{self.current_year}")
        except Exception:
            pass
        self.destroy()


class ModuloBancoApp:
    def __init__(self, parent_frame, usuario_activo=""):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo or "Desconocido"
        self.config = cargar_config()
        self.bancos = []               # se cargan enseguida, en segundo plano
        self.movimientos_pdf = []
        self.texto_pdf = ""
        self.filas_conciliacion = []
        self.saldo_final_estado = None
        self.banco_conciliado = None
        self.id_tx_edicion = None      # transferencia que se está editando (None = nueva)
        aplicar_estilo_treeview()

        self.frame_main = ctk.CTkFrame(self.parent_frame, fg_color="transparent")
        self.frame_main.pack(fill="both", expand=True, padx=15, pady=15)

        header = ctk.CTkFrame(self.frame_main, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(header, text="🏦 MÓDULO DE BANCO (SALDOS Y CONCILIACIÓN BANCARIA)",
                     font=("Arial", 18, "bold"), text_color="#1f538d").pack(side="left")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        self.tab_saldos = self.tabview.add(" 💵 Saldo de Bancos ")
        self.tab_conciliacion = self.tabview.add(" 🧾 Conciliación Bancaria ")
        self.tab_transferencias = self.tabview.add(" 🔄 Transferencias ")

        self.construir_tab_saldos()
        self.construir_tab_conciliacion()
        self.construir_tab_transferencias()

        # 🔄 Al entrar a "Saldo de Bancos" se recalcula: en otros módulos pueden
        # haberse eliminado facturas emitidas/recibidas o sus cobros/pagos.
        self.tabview.configure(command=self._al_cambiar_pestana)

        # Los datos (varias consultas a Supabase) se traen aparte, en un hilo,
        # para que el módulo abra al instante y no se congele.
        self._cargar_datos_iniciales()

    # -----------------------------------------------------
    # BASE DE DATOS DE CONCILIACION
    # -----------------------------------------------------
    def inicializar_db(self, forzar=False):
        """Crea/actualiza las tablas y columnas que usa el módulo de Banco.

        Es idempotente, pero son ~24 sentencias y cada una es un viaje a
        Supabase (~0,3 s): por eso se ejecuta UNA vez por equipo y versión y
        después se salta (marca en la configuración local). Con 'forzar=True'
        se vuelve a ejecutar (se usa si alguna consulta falla por esquema).
        """
        if not forzar:
            try:
                if leer_config_disco().get(CLAVE_ESQUEMA_BANCO) == "ok":
                    return
            except Exception:
                pass
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        todo_ok = True
        try:
            with conn.cursor() as c:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS conciliacion_bancaria (
                        id SERIAL PRIMARY KEY,
                        banco VARCHAR(255),
                        cuenta VARCHAR(255),
                        fecha VARCHAR(20),
                        descripcion TEXT,
                        monto NUMERIC,
                        tipo VARCHAR(20),
                        origen VARCHAR(30),
                        id_movimiento INTEGER DEFAULT 0,
                        estado VARCHAR(20) DEFAULT 'pendiente',
                        fecha_conciliacion VARCHAR(20) DEFAULT ''
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS conciliacion_ignorados (
                        id SERIAL PRIMARY KEY,
                        banco VARCHAR(255),
                        origen VARCHAR(30),
                        tabla VARCHAR(60),
                        id_movimiento INTEGER DEFAULT 0,
                        fecha VARCHAR(20) DEFAULT '',
                        descripcion TEXT,
                        monto NUMERIC DEFAULT 0,
                        creado VARCHAR(20) DEFAULT ''
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS transferencias_bancarias (
                        id SERIAL PRIMARY KEY,
                        banco_origen VARCHAR(255),
                        cuenta_origen VARCHAR(255),
                        banco_destino VARCHAR(255),
                        cuenta_destino VARCHAR(255),
                        fecha VARCHAR(20),
                        descripcion TEXT,
                        monto NUMERIC
                    )
                """)
                c.execute("""
                    CREATE TABLE IF NOT EXISTS facturas_recibidas (
                        id SERIAL PRIMARY KEY, tipo_documento VARCHAR(100), fecha VARCHAR(50),
                        proveedor VARCHAR(255), descripcion TEXT, evento_asociado VARCHAR(255),
                        subtotal NUMERIC, impuesto NUMERIC, total NUMERIC, archivo_ruta TEXT,
                        dias_credito INTEGER DEFAULT 0, det_porcentaje NUMERIC DEFAULT 0,
                        det_monto NUMERIC DEFAULT 0, numero_documento VARCHAR(100) DEFAULT '',
                        categoria VARCHAR(255) DEFAULT 'GENERAL / NO ASIGNADO'
                    )
                """)
                conn.commit()
                for col_sql in (
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS ruc VARCHAR(50)",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS pagado_por_tercero VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS soporte_pago_tercero TEXT DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS es_compra_cruzada BOOLEAN DEFAULT FALSE",
                    # Datos del pago manual, para poder reabrirlo y editarlo completo
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS categoria VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS subcategoria VARCHAR(120) DEFAULT ''",
                    # 🎪 Dimensión de este sistema de EVENTOS (no hay vehículos ni placas):
                    # 'evento' = CÓDIGO | Nombre del Evento y 'personal' = quién cobra la planilla.
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS evento VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS personal VARCHAR(150) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS comision NUMERIC DEFAULT 0",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS interbancario BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS es_cruzada BOOLEAN DEFAULT FALSE",
                    # Enlace con el gasto creado en el módulo de Compras
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS id_gasto_compras INTEGER DEFAULT 0",
                    # N° de operación / referencia del pago (Banco y Compras)
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS numero_operacion VARCHAR(100) DEFAULT ''",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS cuenta_origen VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS numero_operacion VARCHAR(100) DEFAULT ''",
                    # 💳 Cuenta bancaria donde entró el dinero de cada cobro de Ventas:
                    # es lo que permite que el saldo de cada banco cuadre (lo llena Ventas).
                    "ALTER TABLE pagos_clientes ADD COLUMN IF NOT EXISTS cuenta_destino VARCHAR(255) DEFAULT ''",
                ):
                    try:
                        c.execute(col_sql)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        todo_ok = False
        except Exception:
            todo_ok = False
        finally:
            liberar_conexion(conn)

        # Sólo se marca como verificada si TODO salió bien
        if todo_ok:
            try:
                cfg = leer_config_disco()
                cfg[CLAVE_ESQUEMA_BANCO] = "ok"
                guardar_config_disco(cfg)
            except Exception:
                pass

    # -----------------------------------------------------
    # CARGA EN SEGUNDO PLANO (cada consulta a Supabase tarda ~0,3 s)
    # -----------------------------------------------------
    def _mostrar_cargando(self, texto="⏳ Cargando datos del banco..."):
        """Aviso mientras se traen los datos (la ventana ya está visible)."""
        try:
            for w in self.scroll_saldos.winfo_children():
                w.destroy()
            ctk.CTkLabel(self.scroll_saldos, text=texto, font=("Arial", 13),
                         text_color="#7f8c8d").pack(pady=30)
        except Exception:
            pass

    def _actualizar_combos_bancos(self):
        """Vuelca la lista de bancos (ya cargada) en los combos de las pestañas."""
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        if not etiquetas:
            etiquetas = ["(Sin bancos configurados)"]
        for nombre, indice in (("cmb_banco", 0), ("cmb_tx_origen", 0), ("cmb_tx_destino", 1)):
            combo = getattr(self, nombre, None)
            if combo is None:
                continue
            try:
                actual = combo.get()
                combo.configure(values=etiquetas)
                if actual in etiquetas:
                    combo.set(actual)
                else:
                    combo.set(etiquetas[min(indice, len(etiquetas) - 1)])
            except Exception:
                pass

    def _cargar_datos_iniciales(self):
        """Trae de Supabase lo pesado SIN congelar la ventana."""
        self._mostrar_cargando()

        def _trabajo(estado):
            self.inicializar_db()                      # sólo la primera vez por equipo
            # 🔗 Las cuentas se leen de la BASE DE DATOS (fuente compartida por los
            # equipos), no de la copia local.
            bancos = cargar_bancos(usar_cache=False)
            estado["bancos"] = bancos
            # 3 consultas para TODOS los bancos; el reparto por banco y el filtro
            # del periodo elegido se aplican después, al dibujar las tarjetas.
            estado["datos"] = self._cargar_movimientos_crudos()
            estado["transferencias"] = self.cargar_transferencias()

        ejecutar_en_hilo(self.parent_frame, _trabajo, al_terminar=self._aplicar_datos_iniciales)

    def _aplicar_datos_iniciales(self, estado):
        """Se ejecuta en el hilo principal cuando terminó la carga."""
        self.bancos = estado.get("bancos") or []
        self._actualizar_combos_bancos()
        self.refrescar_saldos(estado.get("datos"))
        self._pintar_transferencias(estado.get("transferencias") or [])

    def _al_cambiar_pestana(self, nombre=None):
        """Al entrar a una pestaña se traen los datos que le corresponden.

        * Saldo de Bancos: recalcula los saldos (en otro módulo pueden haber
          eliminado una factura emitida con sus cobros, o una compra con sus pagos).
        * Conciliación Bancaria: carga los movimientos del sistema del periodo
          elegido (desde la Fecha de Comienzo hasta hoy) la primera vez que se entra.
        """
        # Esta versión de customtkinter llama al comando SIN argumentos, por eso
        # se consulta la pestaña activa directamente.
        if not nombre:
            try:
                nombre = self.tabview.get()
            except Exception:
                nombre = ""
        nombre = nombre or ""
        if "Saldo de Bancos" in nombre:
            try:
                self.actualizar_saldos_boton()
            except Exception:
                pass
            return
        if "Conciliación" in nombre and not self.filas_conciliacion:
            # Solo la primera vez: si el usuario ya tiene la lista cargada (con su
            # PDF y sus marcas de conciliado) no se le borra al volver a la pestaña.
            try:
                self.generar_reporte()
            except Exception:
                pass

    def actualizar_saldos_boton(self):
        """Botón 🔄 Actualizar: recalcula sin congelar la ventana."""
        if not self.bancos:
            self.refrescar_saldos()
            return
        self._mostrar_cargando("⏳ Actualizando saldos...")

        def _trabajo(estado):
            estado["datos"] = self._cargar_movimientos_crudos()

        ejecutar_en_hilo(self.parent_frame, _trabajo,
                         al_terminar=lambda e: self.refrescar_saldos(e.get("datos")))

    # -----------------------------------------------------
    # TAB: SALDO DE BANCOS
    # -----------------------------------------------------
    def construir_tab_saldos(self):
        f_top = ctk.CTkFrame(self.tab_saldos, fg_color="transparent")
        f_top.pack(fill="x", pady=(5, 10))
        ctk.CTkLabel(f_top, text="Saldo calculado como: Saldo Inicial + Ingresos Cobrados − Egresos Pagados.",
                     font=("Arial", 12, "italic"), text_color="gray").pack(side="left")
        ctk.CTkButton(f_top, text="🔄 Actualizar", width=130, font=("Arial", 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.actualizar_saldos_boton).pack(side="right")

        # 🗓️ Selector de periodo: un mes concreto o todos los meses
        # (se empaqueta primero el combo y después la etiqueta para que se lea
        #  "Periodo: [Mes] 🔄 Actualizar")
        self.cmb_mes_saldos = ctk.CTkComboBox(f_top, values=construir_valores_mes(), width=170,
                                              state="readonly", command=self._cambiar_mes_saldos)
        self.cmb_mes_saldos.pack(side="right", padx=(10, 12))
        ctk.CTkLabel(f_top, text="Periodo:", font=("Arial", 12, "bold")).pack(side="right", padx=(0, 6))
        self.mes_saldos = None
        self.mes_saldos_etiqueta = ""
        self.cmb_mes_saldos.set("Todos los meses")

        self.scroll_saldos = ctk.CTkScrollableFrame(self.tab_saldos, fg_color="transparent")
        self.scroll_saldos.pack(fill="both", expand=True)
        # La ventana aparece de inmediato; los saldos se calculan aparte para no
        # congelarla (cada consulta a Supabase tarda ~0,3 s).
        self._mostrar_cargando()

    def _cambiar_mes_saldos(self, etiqueta=None):
        """Cambia el periodo visible (un mes o todos) y redibuja las tarjetas.

        No vuelve a consultar la base de datos: reutiliza los movimientos ya
        traídos (_datos_saldos), así el cambio es instantáneo."""
        try:
            etiqueta = etiqueta or self.cmb_mes_saldos.get()
        except Exception:
            etiqueta = etiqueta or ""
        self.mes_saldos = mes_etiqueta_a_key(etiqueta)
        self.mes_saldos_etiqueta = "" if not self.mes_saldos else etiqueta
        if getattr(self, "_datos_saldos", None) is not None:
            self.refrescar_saldos(self._datos_saldos)
        else:
            self.actualizar_saldos_boton()

    def refrescar_saldos(self, datos=None):
        """Dibuja las tarjetas de saldo.

        'datos' es opcional: si ya vienen los movimientos crudos (carga en
        segundo plano, o el cambio de periodo) no se vuelve a consultar la
        base de datos. El saldo de cada banco se calcula aquí, aplicando el
        periodo elegido en el desplegable de meses.
        """
        for w in self.scroll_saldos.winfo_children():
            w.destroy()

        if not self.bancos:
            f_aviso = ctk.CTkFrame(self.scroll_saldos, corner_radius=10, fg_color="#fff3cd",
                                   border_width=1, border_color="#ffeeba")
            f_aviso.pack(fill="x", pady=10, padx=5)
            ctk.CTkLabel(f_aviso, text="⚠️ No hay cuentas bancarias configuradas.\n"
                                       "Agréguelas en: Ajustes de Sistema → ⚙️ Configuración General → Cuentas Bancarias.",
                         font=("Arial", 13), text_color="#856404", justify="left").pack(padx=15, pady=15)
            return

        if datos is None:
            datos = self._cargar_movimientos_crudos()
        self._datos_saldos = datos
        total_general = 0.0
        for idx, banco in enumerate(self.bancos):
            resumen = self.calcular_resumen(banco, datos)
            total_general += resumen["saldo_actual"]
            self._crear_tarjeta_banco(banco, resumen, idx)

        mes_txt = getattr(self, "mes_saldos_etiqueta", "") or ""
        f_total = ctk.CTkFrame(self.scroll_saldos, corner_radius=10, fg_color="#1f538d")
        f_total.pack(fill="x", pady=(15, 5), padx=5)
        ctk.CTkLabel(f_total,
                     text=("TOTAL DISPONIBLE EN BANCOS" if not mes_txt
                           else f"TOTAL AL CIERRE DE {mes_txt.upper()}"),
                     font=("Arial", 14, "bold"),
                     text_color="white").pack(side="left", padx=15, pady=12)
        ctk.CTkLabel(f_total, text=formatear_monto(total_general), font=("Arial", 16, "bold"),
                     text_color="white").pack(side="right", padx=15, pady=12)

    def _crear_tarjeta_banco(self, banco, resumen, idx):
        etiqueta = construir_etiqueta_banco(banco) or f"Banco {idx + 1}"
        card = ctk.CTkFrame(self.scroll_saldos, corner_radius=10, fg_color="#f8f9fa",
                            border_width=1, border_color="#e0e0e0")
        card.pack(fill="x", pady=6, padx=5)

        fila_titulo = ctk.CTkFrame(card, fg_color="transparent")
        fila_titulo.pack(fill="x", padx=12, pady=(10, 4))
        es_mes = bool(resumen.get("es_mes"))
        etiqueta_periodo = resumen.get("etiqueta_mes") or ""
        ctk.CTkLabel(fila_titulo, text=f"🏦 {etiqueta}",
                     font=("Arial", 15, "bold"),
                     text_color="#1f538d").pack(side="left")
        if es_mes:
            ctk.CTkLabel(fila_titulo, text=f"Al cierre de {etiqueta_periodo} ►",
                         font=("Arial", 11, "italic"), text_color="#7f8c8d").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(fila_titulo, text=resumen["saldo_actual_txt"], font=("Arial", 16, "bold"),
                     text_color="#27ae60" if resumen["saldo_actual"] >= 0 else "#c0392b").pack(side="right")

        fila_det = ctk.CTkFrame(card, fg_color="transparent")
        fila_det.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(fila_det, text=f"Saldo inicial: {formatear_monto(resumen['saldo_inicial'])}",
                     font=("Arial", 11), text_color="gray").pack(side="left", padx=(0, 15))
        sufijo = f" ({etiqueta_periodo})" if es_mes else ""
        ctk.CTkLabel(fila_det, text=f"+ Ingresos{sufijo}: {formatear_monto(resumen['ingresos'])}",
                     font=("Arial", 11), text_color="#27ae60").pack(side="left", padx=(0, 15))
        ctk.CTkLabel(fila_det, text=f"− Egresos{sufijo}: {formatear_monto(resumen['egresos'])}",
                     font=("Arial", 11), text_color="#c0392b").pack(side="left", padx=(0, 15))
        ctk.CTkLabel(fila_det, text=f"({resumen['n_movimientos']} movimientos{sufijo})",
                     font=("Arial", 10, "italic"), text_color="#7f8c8d").pack(side="left")

        # Fecha desde la que rige el saldo inicial de la cuenta
        fecha_inicio = resumen.get("fecha_inicio") or ""
        if fecha_inicio:
            d, m_, a = fecha_inicio[8:10], fecha_inicio[5:7], fecha_inicio[0:4]
            if resumen.get("antes_del_inicio"):
                aviso = (f"⚠️ La cuenta inicia el {d}/{m_}/{a}: no hay movimientos "
                         f"anteriores a esa fecha.")
                color_aviso = "#856404"
            else:
                aviso = f"Movimientos considerados desde el {d}/{m_}/{a} (fecha del saldo inicial)."
                color_aviso = "#7f8c8d"
            ctk.CTkLabel(card, text=aviso, font=("Arial", 10, "italic"),
                         text_color=color_aviso, justify="left").pack(anchor="w", padx=12, pady=(0, 4))

        fila_btn = ctk.CTkFrame(card, fg_color="transparent")
        fila_btn.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(fila_btn, text="👁 Ver Movimientos", width=160, font=("Arial", 11, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda b=banco: self.ver_movimientos(b)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(fila_btn, text="🧾 Conciliar este Banco", width=180, font=("Arial", 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda b=banco: self.ir_a_conciliacion(b)).pack(side="left")

    def calcular_resumen(self, banco, datos=None, mes=None):
        """Saldo del banco. Si se pasa 'datos' (movimientos crudos ya leídos de
        la base) no se vuelve a consultar Supabase.

        Si 'mes' (formato 'YYYY-MM') viene informado, los importes mostrados
        son los del MES y el saldo es el de CIERRE de ese mes
        (inicial + todo lo movido hasta el último día del mes).
        Con 'mes' vacío se muestra el saldo actual con todos los movimientos.
        """
        if mes is None:
            mes = getattr(self, "mes_saldos", None)
        movs_todos = self._movimientos_de_banco(banco, datos)
        saldo_inicial = normalizar_monto(banco.get("saldo_inicial", ""))

        # 📌 'fecha' de la cuenta = desde cuándo rige el saldo inicial. Lo movido
        # ANTES de esa fecha ya está incluido en el saldo inicial, así que no se
        # vuelve a contar (si no, el saldo sale descuadrado).
        fecha_inicio = clave_fecha(banco.get("fecha", ""))
        if fecha_inicio:
            movs_todos = [m for m in movs_todos
                          if not (clave_fecha(m["fecha"]) and clave_fecha(m["fecha"]) < fecha_inicio)]

        antes_del_inicio = bool(mes and fecha_inicio and mes < fecha_inicio[:7])
        if antes_del_inicio:
            # Mes anterior a la apertura de la cuenta: todavía no había movimientos
            movs, acumulados, saldo_inicial = [], [], 0.0
        elif mes:
            validos = [(m, clave_fecha(m["fecha"])) for m in movs_todos]
            movs = [m for m, f in validos if f[:7] == mes]
            acumulados = [m for m, f in validos if f and f[:7] <= mes]
        else:
            movs = movs_todos
            acumulados = movs_todos

        ingresos = sum(m["monto"] for m in movs if m["monto"] > 0)
        egresos = sum(-m["monto"] for m in movs if m["monto"] < 0)
        saldo = saldo_inicial + sum(m["monto"] for m in acumulados)
        etiqueta_mes = getattr(self, "mes_saldos_etiqueta", "") if mes else ""
        return {
            "saldo_inicial": saldo_inicial,
            "ingresos": ingresos,
            "egresos": egresos,
            "saldo_actual": saldo,
            "saldo_actual_txt": formatear_monto(saldo),
            "n_movimientos": len(movs),
            "movimientos": movs,
            "mes": mes or "",
            "etiqueta_mes": etiqueta_mes or "",
            "es_mes": bool(mes),
            "fecha_inicio": fecha_inicio,
            "antes_del_inicio": antes_del_inicio,
        }

    def _cargar_movimientos_crudos(self):
        """Trae de una sola vez los cobros, los pagos y las transferencias.

        Antes se consultaba por CADA banco (con 2 cuentas eran 6-9 viajes a
        Supabase de ~0,3 s cada uno); ahora son 3 consultas en total y el
        reparto por banco se hace en memoria.
        """
        datos = {"cobros": [], "pagos": [], "transferencias": []}
        conn = conectar_db(silencioso=True)
        if not conn:
            return datos
        try:
            with conn.cursor() as c:
                try:
                    # 🛡️ Solo se cuentan los cobros que APUNTAN A UNA FACTURA EXISTENTE:
                    # si una factura emitida se elimina y su cobro queda huérfano, ese
                    # cobro ya no debe sumar como ingreso en el saldo del banco.
                    c.execute("""
                        SELECT p.id, p.fecha_pago, p.cliente_nombre, p.monto_pagado,
                               COALESCE(p.cuenta_destino, ''), p.id_factura,
                               COALESCE(f.numero_documento, ''), COALESCE(p.codigo_cotizacion, '')
                        FROM pagos_clientes p
                        LEFT JOIN facturas_emitidas f ON p.id_factura = f.id
                        WHERE COALESCE(p.id_factura, 0) = 0 OR f.id IS NOT NULL
                    """)
                    datos["cobros"] = c.fetchall()
                except Exception:
                    conn.rollback()
                try:
                    # 🛡️ Igual que los cobros: un pago cuya factura de compra ya no
                    # existe (huérfano) no debe seguir restando en el saldo.
                    c.execute("""
                        SELECT p.id, p.fecha_pago, p.proveedor_nombre, p.monto_pagado,
                               COALESCE(p.cuenta_origen, ''), p.id_factura,
                               COALESCE(f.numero_documento, ''), COALESCE(p.codigo_cotizacion, ''),
                               (COALESCE(f.tipo_documento, '') = 'PAGO BANCO'
                                OR COALESCE(f.es_compra_cruzada, FALSE)) AS espejo_banco
                        FROM pagos_comprobantes p
                        LEFT JOIN facturas_recibidas f ON p.id_factura = f.id
                        WHERE COALESCE(p.id_factura, 0) = 0 OR f.id IS NOT NULL
                    """)
                    datos["pagos"] = c.fetchall()
                except Exception:
                    conn.rollback()
                try:
                    c.execute("""
                        SELECT id, banco_origen, cuenta_origen, banco_destino, cuenta_destino,
                               fecha, descripcion, monto
                        FROM transferencias_bancarias
                    """)
                    datos["transferencias"] = c.fetchall()
                except Exception:
                    conn.rollback()
        except Exception:
            pass
        finally:
            liberar_conexion(conn)
        return datos

    def _movimientos_de_banco(self, banco, datos=None, mes=None, incluir_sin_cuenta=False):
        """Cobros (ingresos), pagos (egresos) y transferencias de ESTE banco.

        Si se recibe 'datos' (lo que devuelve _cargar_movimientos_crudos) NO se
        vuelve a consultar la base de datos. Si se recibe 'mes' (formato
        'YYYY-MM'), filtra solo ese mes.

        'incluir_sin_cuenta': la conciliación también lista los cobros y pagos que
        todavía NO tienen cuenta bancaria asignada (se marcan con ⚠️), porque de lo
        contrario no aparecerían en ninguna cuenta. El SALDO de cada banco sigue
        siendo estricto: solo cuenta lo que tiene su cuenta asignada.
        """
        if datos is None:
            datos = self._cargar_movimientos_crudos()
        movs = []
        for idp, fecha, nombre, monto, cuenta, idf, num_doc, ref in (datos.get("cobros") or []):
            coincide = banco_matchea(banco, cuenta)
            sin_cuenta = bool(incluir_sin_cuenta and not str(cuenta or "").strip())
            if coincide or sin_cuenta:
                doc = (num_doc or ref or "").strip()
                desc = f"Cobro {nombre or ''}".replace("  ", " ").strip()
                movs.append({
                    "id": idp, "fecha": fecha or "", "descripcion": desc,
                    "monto": float(monto or 0), "tipo": "ingreso",
                    "origen": "sistema", "tabla": "pagos_clientes", "documento": doc,
                    "sin_cuenta": sin_cuenta,
                })
        for fila in (datos.get("pagos") or []):
            idp, fecha, nombre, monto, cuenta, idf, num_doc, ref = fila[:8]
            # 'espejo_banco': el propio módulo de Banco creó este pago en Compras
            # (un egreso registrado aquí, o una compra cruzada). Ese movimiento ya se
            # ve como MANUAL en la conciliación, así que no se lista dos veces.
            espejo = bool(fila[8]) if len(fila) > 8 else False
            coincide = banco_matchea(banco, cuenta)
            sin_cuenta = bool(incluir_sin_cuenta and not str(cuenta or "").strip())
            if coincide or sin_cuenta:
                doc = (num_doc or ref or "").strip()
                desc = f"Pago {nombre or ''}".replace("  ", " ").strip()
                movs.append({
                    "id": idp, "fecha": fecha or "", "descripcion": desc,
                    "monto": -float(monto or 0), "tipo": "egreso",
                    "origen": "sistema", "tabla": "pagos_comprobantes", "documento": doc,
                    "espejo_banco": espejo,
                    "sin_cuenta": sin_cuenta,
                })
        for idt, bo, co, bd, cd, fecha, desc, monto in (datos.get("transferencias") or []):
            monto_v = float(monto or 0)
            et_origen = construir_etiqueta_banco({"banco": bo, "cuenta": co})
            et_destino = construir_etiqueta_banco({"banco": bd, "cuenta": cd})
            if banco_matchea(banco, et_origen):
                desc_eg = f"Transferencia a {bd or 'otra cuenta'}"
                if desc:
                    desc_eg += f" ({desc})"
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_eg,
                    "monto": -monto_v, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias", "documento": "",
                })
            if banco_matchea(banco, et_destino):
                desc_in = f"Transferencia de {bo or 'otra cuenta'}"
                if desc:
                    desc_in += f" ({desc})"
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_in,
                    "monto": monto_v, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias", "documento": "",
                })
        if mes:
            movs = [m for m in movs if clave_fecha(m["fecha"]).startswith(mes)]
        movs.sort(key=lambda m: (clave_fecha(m["fecha"]), m["monto"]))
        return movs

    def cargar_movimientos_sistema(self, banco, mes=None, incluir_sin_cuenta=False):
        """Movimientos del sistema para un banco (consulta la base de datos)."""
        return self._movimientos_de_banco(banco, mes=mes, incluir_sin_cuenta=incluir_sin_cuenta)

    def ver_movimientos(self, banco):
        # ⚡ Si los movimientos ya están cargados (pestaña de saldos) se usan esos:
        # antes esta ventana volvía a consultar Supabase y tardaba varios segundos.
        datos = getattr(self, "_datos_saldos", None)
        if datos is None:
            def _trabajo(estado):
                estado["resumen"] = self.calcular_resumen(banco)

            def _aplicar(estado):
                if estado.get("error"):
                    return messagebox.showerror("Movimientos",
                                                f"No se pudieron cargar los movimientos:\n{estado['error']}",
                                                parent=self.parent_frame)
                self._mostrar_ventana_movimientos(banco, estado.get("resumen") or {})

            ejecutar_en_hilo(self.parent_frame, _trabajo, al_terminar=_aplicar)
            return
        self._mostrar_ventana_movimientos(banco, self.calcular_resumen(banco, datos))

    def _mostrar_ventana_movimientos(self, banco, resumen):
        movs = resumen.get("movimientos") or []
        periodo = f" — {resumen['etiqueta_mes']}" if resumen.get("es_mes") else ""
        v = ctk.CTkToplevel(self.parent_frame)
        v.title(f"Movimientos - {construir_etiqueta_banco(banco)}{periodo}")
        v.geometry("760x480")
        v.transient(self.parent_frame)

        ctk.CTkLabel(v, text=f"Movimientos de {construir_etiqueta_banco(banco)}{periodo}",
                     font=("Arial", 15, "bold"), text_color="#1f538d").pack(pady=(15, 5))

        f_tabla = ctk.CTkFrame(v, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=10)
        columnas = ("fecha", "documento", "descripcion", "tipo", "monto")
        tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", height=14)
        tabla.heading("fecha", text="Fecha")
        tabla.heading("documento", text="N° Documento")
        tabla.heading("descripcion", text="Descripción")
        tabla.heading("tipo", text="Tipo")
        tabla.heading("monto", text="Monto")
        tabla.column("fecha", width=95, anchor="center")
        tabla.column("documento", width=150, anchor="center")
        tabla.column("descripcion", width=260, anchor="w")
        tabla.column("tipo", width=110, anchor="center")
        tabla.column("monto", width=130, anchor="e")
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=tabla.yview)
        tabla.configure(yscrollcommand=vsb.set)
        tabla.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        movs = sorted(movs, key=lambda m: (normalizar_fecha(m["fecha"]), m["monto"]))
        for m in movs:
            if m.get("tipo") == "transferencia":
                tipo_txt = "Transferencia (Ingreso)" if m["monto"] > 0 else "Transferencia (Egreso)"
            else:
                tipo_txt = "Ingreso (Cobro)" if m["monto"] > 0 else "Egreso (Pago)"
            tabla.insert("", tk.END, values=(m["fecha"], m.get("documento", ""), m["descripcion"],
                                             tipo_txt, formatear_monto(m["monto"])))
        ctk.CTkButton(v, text="Cerrar", width=120, fg_color="#34495e",
                      command=v.destroy).pack(pady=(0, 15))

    # -----------------------------------------------------
    # TAB: TRANSFERENCIAS ENTRE CUENTAS
    # -----------------------------------------------------
    def construir_tab_transferencias(self):
        f_form = ctk.CTkFrame(self.tab_transferencias, fg_color="#f8f9fa",
                              border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_form.pack(fill="x", padx=5, pady=(5, 10))

        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        if not etiquetas:
            etiquetas = ["(Sin bancos configurados)"]

        r1 = ctk.CTkFrame(f_form, fg_color="transparent"); r1.pack(fill="x", padx=12, pady=(12, 4))
        ctk.CTkLabel(r1, text="Banco Origen:", width=130, anchor="w", font=("Arial", 12, "bold")).pack(side="left")
        self.cmb_tx_origen = ctk.CTkComboBox(r1, values=etiquetas, width=340, state="readonly")
        self.cmb_tx_origen.pack(side="left", padx=6)
        if etiquetas:
            self.cmb_tx_origen.set(etiquetas[0])

        r2 = ctk.CTkFrame(f_form, fg_color="transparent"); r2.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(r2, text="Banco Destino:", width=130, anchor="w", font=("Arial", 12, "bold")).pack(side="left")
        self.cmb_tx_destino = ctk.CTkComboBox(r2, values=etiquetas, width=340, state="readonly")
        self.cmb_tx_destino.pack(side="left", padx=6)
        if len(etiquetas) > 1:
            self.cmb_tx_destino.set(etiquetas[1])
        elif etiquetas:
            self.cmb_tx_destino.set(etiquetas[0])

        r3 = ctk.CTkFrame(f_form, fg_color="transparent"); r3.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(r3, text="Fecha:", width=130, anchor="w", font=("Arial", 12, "bold")).pack(side="left")
        self.ent_tx_fecha = ctk.CTkEntry(r3, width=170)
        self.ent_tx_fecha.pack(side="left", padx=6)
        self.ent_tx_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ctk.CTkLabel(r3, text="Monto:", width=90, anchor="w", font=("Arial", 12, "bold")).pack(side="left", padx=(16, 0))
        self.ent_tx_monto = ctk.CTkEntry(r3, width=170)
        self.ent_tx_monto.pack(side="left", padx=6)

        r4 = ctk.CTkFrame(f_form, fg_color="transparent"); r4.pack(fill="x", padx=12, pady=(4, 4))
        ctk.CTkLabel(r4, text="Descripción:", width=130, anchor="w", font=("Arial", 12, "bold")).pack(side="left")
        self.ent_tx_desc = ctk.CTkEntry(r4)
        self.ent_tx_desc.pack(side="left", fill="x", expand=True, padx=6)

        r5 = ctk.CTkFrame(f_form, fg_color="transparent"); r5.pack(fill="x", padx=12, pady=(4, 12))
        self.btn_tx_guardar = ctk.CTkButton(r5, text="💸 Realizar Transferencia", width=220, height=38,
                                            font=("Arial", 13, "bold"), fg_color="#27ae60",
                                            hover_color="#1e8449", command=self.realizar_transferencia)
        self.btn_tx_guardar.pack(side="left", padx=(0, 8))
        self.btn_tx_editar = ctk.CTkButton(r5, text="✏️ Editar", width=120, height=38,
                                           font=("Arial", 12, "bold"), fg_color="#2980b9",
                                           hover_color="#1f618d", command=self.editar_transferencia)
        self.btn_tx_editar.pack(side="left", padx=(0, 8))
        # Este botón solo se muestra mientras se está editando una transferencia
        self.btn_tx_cancelar = ctk.CTkButton(r5, text="✖ Cancelar edición", width=150, height=38,
                                             font=("Arial", 12, "bold"), fg_color="#7f8c8d",
                                             hover_color="#606b6b",
                                             command=self.cancelar_edicion_transferencia)
        ctk.CTkButton(r5, text="🗑️ Eliminar", width=120, height=38,
                      font=("Arial", 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b",
                      command=self.eliminar_transferencia).pack(side="left")

        self.lbl_tx_modo = ctk.CTkLabel(f_form, text="", font=("Arial", 11, "bold"),
                                        text_color="#2980b9")
        self.lbl_tx_modo.pack(anchor="w", padx=12, pady=(0, 10))

        ctk.CTkLabel(self.tab_transferencias, text="Historial de transferencias:",
                     font=("Arial", 13, "bold"), text_color="#1f538d").pack(anchor="w", padx=5, pady=(0, 4))
        f_tabla = ctk.CTkFrame(self.tab_transferencias, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        columnas = ("fecha", "origen", "destino", "descripcion", "monto")
        self.tabla_tx = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="extended")
        for col, txt, w, anc in (("fecha", "Fecha", 100, "center"),
                                 ("origen", "Banco Origen", 200, "center"),
                                 ("destino", "Banco Destino", 200, "center"),
                                 ("descripcion", "Descripción", 260, "w"),
                                 ("monto", "Monto", 130, "e")):
            self.tabla_tx.heading(col, text=txt)
            self.tabla_tx.column(col, width=w, anchor=anc)
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_tx.yview)
        self.tabla_tx.configure(yscrollcommand=vsb.set)
        self.tabla_tx.pack(side="left", fill="both", expand=True)
        # Doble clic: carga la transferencia en el mismo formulario para editarla
        self.tabla_tx.bind("<Double-1>", lambda _e: (
            self.editar_transferencia() if self.tabla_tx.selection() else None))
        vsb.pack(side="right", fill="y")
        # El historial lo rellena la carga inicial en segundo plano

    def _preparar_form_transferencia(self, tx=None):
        """Deja el formulario de transferencias listo: en blanco (nueva) o con los datos de 'tx'.

        Se usa TANTO para registrar como para editar: el mismo formulario sirve
        para las dos cosas (al editar, el botón pasa a 'Guardar Cambios').
        """
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos] or ["(Sin bancos configurados)"]
        tx = tx or {}

        valores_origen = list(self.cmb_tx_origen.cget("values"))
        valores_destino = list(self.cmb_tx_destino.cget("values"))
        origen = tx.get("origen") or etiquetas[0]
        destino = tx.get("destino") or (etiquetas[1] if len(etiquetas) > 1 else etiquetas[0])
        if origen not in valores_origen:
            valores_origen.append(origen)
            self.cmb_tx_origen.configure(values=valores_origen)
        if destino not in valores_destino:
            valores_destino.append(destino)
            self.cmb_tx_destino.configure(values=valores_destino)
        self.cmb_tx_origen.set(origen)
        self.cmb_tx_destino.set(destino)

        self.ent_tx_fecha.delete(0, tk.END)
        self.ent_tx_fecha.insert(0, tx.get("fecha") or datetime.now().strftime("%d/%m/%Y"))
        self.ent_tx_monto.delete(0, tk.END)
        if tx:
            self.ent_tx_monto.insert(0, formatear_numero_entrada(f"{float(tx.get('monto') or 0):.2f}"))
        self.ent_tx_desc.delete(0, tk.END)
        if tx:
            self.ent_tx_desc.insert(0, tx.get("descripcion", ""))

        self.id_tx_edicion = tx.get("id")
        try:
            if self.id_tx_edicion:
                self.btn_tx_guardar.configure(text="💾 Guardar Cambios")
                self.lbl_tx_modo.configure(
                    text=f"✏️ Editando la transferencia #{self.id_tx_edicion}: "
                         f"modifique los datos y pulse «Guardar Cambios».")
                self.btn_tx_cancelar.pack(side="left", padx=(0, 8), before=self.btn_tx_editar)
            else:
                self.btn_tx_guardar.configure(text="💸 Realizar Transferencia")
                self.lbl_tx_modo.configure(text="")
                self.btn_tx_cancelar.pack_forget()
        except Exception:
            pass

    def cancelar_edicion_transferencia(self):
        """Sale del modo edición y deja el formulario listo para una transferencia nueva."""
        self._preparar_form_transferencia()

    def editar_transferencia(self):
        """Edita la transferencia seleccionada EN EL MISMO formulario con el que se crea."""
        sel = self.tabla_tx.selection()
        if len(sel) != 1:
            messagebox.showinfo("Editar", "Seleccione exactamente una transferencia para editar.",
                                parent=self.parent_frame)
            return
        id_tx = int(sel[0])
        tx = next((t for t in self.cargar_transferencias() if t["id"] == id_tx), None)
        if not tx:
            messagebox.showerror("Error", "No se encontró la transferencia.", parent=self.parent_frame)
            return
        self._preparar_form_transferencia(tx)
        try:
            self.tabview.set(" 🔄 Transferencias ")
        except Exception:
            pass

    def realizar_transferencia(self):
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        origen = self.cmb_tx_origen.get()
        destino = self.cmb_tx_destino.get()
        if origen not in etiquetas or destino not in etiquetas:
            messagebox.showwarning("Transferencia", "Seleccione bancos válidos.", parent=self.parent_frame)
            return
        if origen == destino:
            messagebox.showwarning("Transferencia", "El banco origen y destino deben ser diferentes.", parent=self.parent_frame)
            return
        monto = normalizar_monto(self.ent_tx_monto.get())
        if monto <= 0:
            messagebox.showerror("Error", "Ingrese un monto mayor a 0.", parent=self.parent_frame)
            return
        fecha = self.ent_tx_fecha.get().strip() or datetime.now().strftime("%d/%m/%Y")
        desc = self.ent_tx_desc.get().strip() or "Transferencia entre cuentas"

        bo = self.bancos[etiquetas.index(origen)]
        bd = self.bancos[etiquetas.index(destino)]
        id_edicion = self.id_tx_edicion

        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=self.parent_frame)
            return
        try:
            with conn.cursor() as c:
                if id_edicion:
                    c.execute("""
                        UPDATE transferencias_bancarias
                        SET banco_origen=%s, cuenta_origen=%s, banco_destino=%s, cuenta_destino=%s,
                            fecha=%s, descripcion=%s, monto=%s
                        WHERE id=%s
                    """, (bo.get("banco", ""), bo.get("cuenta", ""), bd.get("banco", ""), bd.get("cuenta", ""),
                          fecha, desc, monto, id_edicion))
                else:
                    c.execute("""
                        INSERT INTO transferencias_bancarias
                        (banco_origen, cuenta_origen, banco_destino, cuenta_destino, fecha, descripcion, monto)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (bo.get("banco", ""), bo.get("cuenta", ""), bd.get("banco", ""), bd.get("cuenta", ""),
                          fecha, desc, monto))
                conn.commit()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar la transferencia:\n{e}", parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)

        registrar_auditoria(self.usuario_activo, "Banco",
                            (f"Editó la transferencia #{id_edicion} ({formatear_monto(monto)} {origen} → {destino})"
                             if id_edicion else
                             f"Transferencia {formatear_monto(monto)} de {origen} a {destino}"))
        self._preparar_form_transferencia()      # el formulario vuelve al modo "nueva"
        self.refrescar_transferencias()
        self.refrescar_saldos()
        if id_edicion:
            messagebox.showinfo("Éxito", "Transferencia actualizada correctamente.", parent=self.parent_frame)
        else:
            messagebox.showinfo("Éxito", f"Transferencia de {formatear_monto(monto)} registrada:\n"
                                         f"• Egreso en {origen}\n• Ingreso en {destino}", parent=self.parent_frame)

    def cargar_transferencias(self):
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        rows = []
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT id, banco_origen, cuenta_origen, banco_destino, cuenta_destino,
                           fecha, descripcion, monto
                    FROM transferencias_bancarias
                    ORDER BY id DESC
                """)
                for idt, bo, co, bd, cd, fecha, desc, monto in c.fetchall():
                    rows.append({
                        "id": idt,
                        "origen": construir_etiqueta_banco({"banco": bo, "cuenta": co}),
                        "destino": construir_etiqueta_banco({"banco": bd, "cuenta": cd}),
                        "fecha": fecha or "", "descripcion": desc or "", "monto": float(monto or 0),
                    })
        except Exception:
            pass
        finally:
            liberar_conexion(conn)
        return rows

    def _pintar_transferencias(self, filas):
        """Dibuja el historial ya cargado (no consulta la base)."""
        try:
            self.tabla_tx.delete(*self.tabla_tx.get_children())
        except Exception:
            return
        for t in filas:
            self.tabla_tx.insert("", tk.END, iid=str(t["id"]),
                                 values=(t["fecha"], t["origen"], t["destino"],
                                         t["descripcion"], formatear_monto(t["monto"])))

    def refrescar_transferencias(self):
        self._pintar_transferencias(self.cargar_transferencias())

    def eliminar_transferencia(self):
        sel = self.tabla_tx.selection()
        if not sel:
            messagebox.showinfo("Eliminar", "Seleccione una o más transferencias para eliminar.", parent=self.parent_frame)
            return
        if not messagebox.askyesno("Confirmar", f"¿Eliminar {len(sel)} transferencia(s) seleccionada(s)?", parent=self.parent_frame):
            return
        ids = [int(i) for i in sel]
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=self.parent_frame)
            return
        try:
            with conn.cursor() as c:
                c.execute("DELETE FROM transferencias_bancarias WHERE id = ANY(%s)", (ids,))
                conn.commit()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo eliminar:\n{e}", parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)
        registrar_auditoria(self.usuario_activo, "Banco", f"Eliminó {len(ids)} transferencia(s)")
        self.refrescar_transferencias()
        self.refrescar_saldos()
        messagebox.showinfo("Éxito", "Transferencia(s) eliminada(s) correctamente.", parent=self.parent_frame)

    # -----------------------------------------------------
    # TAB: CONCILIACION BANCARIA
    # -----------------------------------------------------
    def construir_tab_conciliacion(self):
        f_sel = ctk.CTkFrame(self.tab_conciliacion, fg_color="#f8f9fa",
                             border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_sel.pack(fill="x", padx=5, pady=(5, 10))

        ctk.CTkLabel(f_sel, text="Banco a conciliar:", font=("Arial", 12, "bold")).pack(side="left", padx=(12, 6), pady=10)
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        if not etiquetas:
            etiquetas = ["(Sin bancos configurados)"]
        self.cmb_banco = ctk.CTkComboBox(f_sel, values=etiquetas, width=320, state="readonly")
        self.cmb_banco.pack(side="left", padx=6, pady=10)
        if etiquetas:
            self.cmb_banco.set(etiquetas[0])

        ctk.CTkLabel(f_sel, text="Periodo a conciliar:", font=("Arial", 12, "bold")).pack(side="left", padx=(20, 6), pady=10)
        # 🗓️ Primera opción por defecto: todo lo registrado desde la Fecha de Comienzo
        # del Sistema (Configuración General) hasta hoy.
        valores_mes = [ETIQUETA_DESDE_COMIENZO] + construir_valores_mes()
        self.cmb_mes = ctk.CTkComboBox(f_sel, values=valores_mes, width=210, state="readonly")
        self.cmb_mes.pack(side="left", padx=6, pady=10)
        self.cmb_mes.set(ETIQUETA_DESDE_COMIENZO)

        f_btns = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns.pack(fill="x", padx=5, pady=(0, 8))
        ctk.CTkButton(f_btns, text="📄 Subir PDF Estado de Cuenta", font=("Arial", 12, "bold"),
                      fg_color="#e67e22", hover_color="#ca6f1e",
                      command=self.subir_pdf).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="🔄 Cargar Movimientos del Sistema", font=("Arial", 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.generar_reporte).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="🏦 Asignar esta cuenta a los movimientos sin cuenta",
                      font=("Arial", 12, "bold"), fg_color="#16a085", hover_color="#117a65",
                      command=self.asignar_cuenta_a_movimientos).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="✅ Conciliar Seleccionados", font=("Arial", 12, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.conciliar_seleccionados).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="➕ Registrar Pago / Compra Cruzada", font=("Arial", 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=self.agregar_movimiento_manual).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="✏️ Editar / Corregir", font=("Arial", 12, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=self.corregir_movimiento).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="🗑️ Eliminar", font=("Arial", 12, "bold"),
                      fg_color="#c0392b", hover_color="#96281b",
                      command=self.eliminar_movimiento_conciliacion).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="♻️ Restaurar Eliminados", font=("Arial", 12, "bold"),
                      fg_color="#16a085", hover_color="#117a65",
                      command=self.restaurar_ignorados).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="💾 Exportar", font=("Arial", 12, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.exportar_reporte).pack(side="left", padx=4)

        self.f_resumen = ctk.CTkFrame(self.tab_conciliacion, fg_color="#eaf2f8",
                                      border_width=1, border_color="#d4e6f1", corner_radius=8)
        self.f_resumen.pack(fill="x", padx=5, pady=(0, 8))
        self.lbl_resumen = ctk.CTkLabel(self.f_resumen, text="Seleccione un banco, suba el PDF del estado de cuenta "
                                                             "y cargue los movimientos del sistema.",
                                        font=("Arial", 12), text_color="#1f538d", justify="left")
        self.lbl_resumen.pack(anchor="w", padx=12, pady=10)

        f_tabla = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        columnas = ("estado", "fecha", "documento", "descripcion", "tipo", "monto", "origen")
        self.tabla_conc = ttk.Treeview(f_tabla, columns=columnas, show="headings",
                                       selectmode="extended")
        for col, txt, w, anc in (("estado", "Estado", 140, "center"),
                                 ("fecha", "Fecha", 90, "center"),
                                 ("documento", "N° Documento", 140, "center"),
                                 ("descripcion", "Descripción", 250, "w"),
                                 ("tipo", "Tipo", 100, "center"),
                                 ("monto", "Monto", 110, "e"),
                                 ("origen", "Origen", 120, "center")):
            self.tabla_conc.heading(col, text=txt)
            self.tabla_conc.column(col, width=w, anchor=anc)
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_conc.yview)
        self.tabla_conc.configure(yscrollcommand=vsb.set)
        self.tabla_conc.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tabla_conc.bind("<Delete>", lambda _e: self.eliminar_movimiento_conciliacion())
        # Doble clic: abre el editor del movimiento (pago del módulo o corrección simple)
        self.tabla_conc.bind("<Double-1>", lambda _e: (
            self.corregir_movimiento() if self.tabla_conc.selection() else None))
        self.tabla_conc.tag_configure("conciliado", background="#d5f5e3")
        self.tabla_conc.tag_configure("diferencia", background="#fadbd8")
        self.tabla_conc.tag_configure("estado_cuenta", background="#d6eaf8")

        f_pie = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_pie.pack(fill="x", padx=5)
        ctk.CTkButton(f_pie, text="👁 Ver Texto Extraído del PDF", width=220, font=("Arial", 11),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.mostrar_texto_pdf).pack(side="left")

    def banco_seleccionado(self):
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        sel = self.cmb_banco.get()
        if sel in etiquetas:
            return self.bancos[etiquetas.index(sel)]
        if self.bancos:
            return self.bancos[0]
        return None

    def ir_a_conciliacion(self, banco):
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        et = construir_etiqueta_banco(banco)
        if et in etiquetas:
            self.cmb_banco.set(et)
        self.tabview.set(" 🧾 Conciliación Bancaria ")

    def subir_pdf(self):
        ruta = seleccionar_archivo_dialogo(
            titulo="Seleccionar Estado de Cuenta (PDF)",
            tipos=[("Archivos PDF", "*.pdf")])
        if not ruta:
            return
        self.lbl_resumen.configure(text="⏳ Extrayendo texto del PDF...")
        self.parent_frame.update_idletasks()

        # El hilo solo lee y analiza el PDF; la pantalla se actualiza en el hilo
        # principal (llamar a Tk desde un hilo congela la app en macOS).
        def _leer(estado):
            texto = extraer_texto_pdf(ruta)
            estado["texto"] = texto or ""
            if (texto or "").strip():
                estado["movimientos"] = parsear_movimientos(texto)
                estado["saldo_final"] = detectar_saldo_final(texto)

        def _terminar(estado):
            if estado.get("error"):
                self.lbl_resumen.configure(text=f"❌ Error al leer el PDF: {estado['error']}")
                return
            if not (estado.get("texto") or "").strip():
                self.lbl_resumen.configure(text="❌ No se pudo extraer texto del PDF. "
                                                "Verifique que no sea un PDF escaneado (imagen).")
                return
            self.texto_pdf = estado.get("texto") or ""
            self.movimientos_pdf = estado.get("movimientos") or []
            self.saldo_final_estado = estado.get("saldo_final")
            self.lbl_resumen.configure(text=f"✅ PDF procesado: {len(self.movimientos_pdf)} movimientos detectados.")
            self.generar_reporte()

        ejecutar_en_hilo(self.parent_frame, _leer, al_terminar=_terminar)

    @staticmethod
    def _clave_ignorado(origen, tabla, id_movimiento, fecha, monto, descripcion):
        """Firma para reconocer un movimiento eliminado de la conciliación.

        Los movimientos del sistema tienen id propio (tabla + id); las líneas
        leídas del PDF no, así que se identifican por fecha + monto + descripción.
        """
        try:
            idm = int(id_movimiento or 0)
        except Exception:
            idm = 0
        if idm:
            return f"{tabla or ''}|{idm}"
        return "pdf|%s|%.2f|%s" % (normalizar_fecha(fecha), float(monto or 0),
                                   _norm_texto(descripcion))

    def cargar_ignorados(self, banco):
        """Movimientos que el usuario eliminó en la conciliación de este banco.

        Se guardan en la base de datos para que NO vuelvan a aparecer al pulsar
        '🔄 Cargar Movimientos del Sistema' (antes reaparecían siempre).
        """
        ignorados = {"sistema": set(), "estado_cuenta": set()}
        if not banco:
            return ignorados
        conn = conectar_db(silencioso=True)
        if not conn:
            return ignorados
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT origen, COALESCE(tabla, ''), COALESCE(id_movimiento, 0),
                           COALESCE(fecha, ''), COALESCE(monto, 0), COALESCE(descripcion, '')
                    FROM conciliacion_ignorados
                    WHERE banco = %s
                """, (banco.get("banco", ""),))
                for origen, tabla, idm, fecha, monto, desc in c.fetchall():
                    ignorados.setdefault(origen or "", set()).add(
                        self._clave_ignorado(origen, tabla, idm, fecha, monto, desc))
        except Exception:
            pass
        finally:
            liberar_conexion(conn)
        return ignorados

    @staticmethod
    def _registrar_ignorado(cursor, banco, f):
        """Anota en la base que este movimiento fue eliminado de la conciliación."""
        try:
            idm = int(f.get("id", 0) or 0)
        except Exception:
            idm = 0
        cursor.execute("""
            INSERT INTO conciliacion_ignorados
            (banco, origen, tabla, id_movimiento, fecha, descripcion, monto, creado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (banco.get("banco", "") if banco else "",
              f.get("origen", ""), f.get("tabla", "") or "", idm,
              str(f.get("fecha", "") or ""), str(f.get("descripcion", "") or ""),
              float(f.get("monto", 0) or 0), datetime.now().strftime("%Y-%m-%d %H:%M")))

    def restaurar_ignorados(self):
        """Vuelve a mostrar los movimientos que se habían eliminado de la lista."""
        banco = self.banco_seleccionado()
        if not banco:
            return
        if not messagebox.askyesno(
                "Restaurar movimientos",
                f"¿Volver a mostrar en la conciliación los movimientos que eliminó "
                f"en {construir_etiqueta_banco(banco)}?\n\n"
                "Se quitarán de la lista de descartados (no se vuelve a crear nada: "
                "los movimientos del sistema y del estado de cuenta siguen intactos).",
                parent=self.parent_frame):
            return
        conn = conectar_db(silencioso=True)
        try:
            if conn:
                with conn.cursor() as c:
                    c.execute("DELETE FROM conciliacion_ignorados WHERE banco = %s",
                              (banco.get("banco", ""),))
                conn.commit()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo restaurar:\n{e}", parent=self.parent_frame)
            return
        finally:
            if conn:
                liberar_conexion(conn)
        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Restauró los movimientos eliminados de la conciliación en "
                            f"{construir_etiqueta_banco(banco)}")
        self.generar_reporte()

    def generar_reporte(self):
        """Recalcula la conciliación del banco y del mes elegidos.

        Las consultas (cobros, pagos, transferencias, movimientos manuales e
        ignorados) suman entre 2 y 6 segundos: se hacen en segundo plano para que
        la ventana NO se congele mientras se arma la conciliación.
        """
        banco = self.banco_seleccionado()
        if not banco:
            self.lbl_resumen.configure(text="⚠️ No hay banco seleccionado.")
            return

        # 🗓️ Periodo elegido: por defecto "Desde la Fecha de Comienzo" (la que se
        # configura en Configuración General) hasta el día de hoy.
        etiqueta_periodo = self.cmb_mes.get()
        mes_key, desde, hasta = self._periodo_conciliacion(etiqueta_periodo)

        # Cada cálculo lleva un token: así un resultado viejo no pisa al nuevo.
        self._token_reporte = getattr(self, "_token_reporte", 0) + 1
        token = self._token_reporte
        self.lbl_resumen.configure(text="⏳ Cargando los movimientos del sistema...")

        def _recolectar(estado):
            estado["datos"] = self._reunir_movimientos(banco, mes_key, desde, hasta)

        def _aplicar(estado):
            if token != getattr(self, "_token_reporte", token):
                return                     # hay un cálculo más reciente en curso
            if estado.get("error"):
                self.lbl_resumen.configure(
                    text=f"❌ No se pudieron cargar los movimientos: {estado['error']}")
                return
            try:
                self._pintar_reporte(banco, etiqueta_periodo, estado.get("datos") or {})
            except Exception as e:
                print("[Banco] Error dibujando la conciliación:", e)

        ejecutar_en_hilo(self.parent_frame, _recolectar, al_terminar=_aplicar)

    def asignar_cuenta_a_movimientos(self):
        """Asigna la cuenta bancaria elegida a los cobros/pagos que no tienen ninguna.

        Los movimientos registrados en Compras y Ventas antes de configurar las
        cuentas bancarias quedaron sin cuenta: por eso no coincidían con ningún banco
        y la conciliación salía vacía. Con este botón se les asigna la cuenta que se
        está conciliando (el módulo de Compras/Ventas queda actualizado) y el saldo
        del banco empieza a cuadrar.
        """
        banco = self.banco_seleccionado()
        if not banco:
            messagebox.showwarning("Banco", "Seleccione un banco.", parent=self.parent_frame)
            return
        pendientes = [f for f in self.filas_conciliacion
                      if f.get("sin_cuenta") and f.get("tabla") in ("pagos_comprobantes", "pagos_clientes")]
        if not pendientes:
            messagebox.showinfo(
                "Asignar cuenta",
                "No hay movimientos del sistema sin cuenta bancaria en la lista actual.\n\n"
                "Pulse primero «🔄 Cargar Movimientos del Sistema».",
                parent=self.parent_frame)
            return

        etiqueta = construir_etiqueta_banco(banco)
        pagos = len([f for f in pendientes if f.get("tabla") == "pagos_comprobantes"])
        cobros = len(pendientes) - pagos
        if not messagebox.askyesno(
                "Asignar cuenta bancaria",
                f"Se asignará la cuenta {etiqueta} a {len(pendientes)} movimiento(s) que no tienen "
                f"ninguna cuenta:\n\n• {pagos} pago(s) de Compras\n• {cobros} cobro(s) de Ventas\n\n"
                "Se actualizarán los módulos de Compras y Ventas, y el saldo de esta cuenta pasará a "
                "incluirlos. ¿Deseas continuar?",
                parent=self.parent_frame):
            return

        conn = conectar_db(silencioso=True)
        if not conn:
            return messagebox.showerror("Sin conexión", "No hay conexión con la base de datos.",
                                        parent=self.parent_frame)
        actualizados = 0
        try:
            with conn.cursor() as c:
                for f in pendientes:
                    try:
                        if f.get("tabla") == "pagos_comprobantes":
                            c.execute("UPDATE pagos_comprobantes SET cuenta_origen = %s WHERE id = %s",
                                      (etiqueta, f.get("id")))
                        else:
                            c.execute("UPDATE pagos_clientes SET cuenta_destino = %s WHERE id = %s",
                                      (etiqueta, f.get("id")))
                        actualizados += max(c.rowcount, 0)
                    except Exception:
                        conn.rollback()
            conn.commit()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron asignar las cuentas:\n{e}",
                                 parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)

        try:
            from buffer_memoria import cache_sistema
            cache_sistema.invalidar()      # Compras y Ventas trabajan con caché
        except Exception:
            pass

        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Asignó la cuenta {etiqueta} a {actualizados} movimiento(s) sin cuenta")
        messagebox.showinfo("Cuenta asignada",
                            f"{actualizados} movimiento(s) quedaron asignados a {etiqueta}.",
                            parent=self.parent_frame)
        self.generar_reporte()

    def _periodo_conciliacion(self, etiqueta):
        """(mes_key, desde, hasta) según el periodo elegido en el desplegable.

        * "Desde la Fecha de Comienzo": desde la fecha configurada en Configuración
          General (o sin límite si no hay corte) hasta HOY.
        * un mes concreto: ese mes.
        * "Todos los meses": sin filtro de fechas.
        """
        etiqueta = (etiqueta or "").strip()
        if etiqueta == ETIQUETA_DESDE_COMIENZO:
            fecha_comienzo = obtener_fecha_comienzo() if obtener_fecha_comienzo else None
            desde = fecha_comienzo.strftime("%Y-%m-%d") if fecha_comienzo else None
            return None, desde, datetime.now().strftime("%Y-%m-%d")
        return mes_etiqueta_a_key(etiqueta), None, None

    @staticmethod
    def _en_periodo(fecha, mes_key=None, desde=None, hasta=None):
        """True si la fecha cae dentro del periodo pedido."""
        clave = clave_fecha(fecha)
        if not clave:
            # Sin fecha interpretable: solo se muestra cuando no hay filtro
            return not (mes_key or desde or hasta)
        if mes_key and not clave.startswith(mes_key):
            return False
        if desde and clave < desde:
            return False
        if hasta and clave > hasta:
            return False
        return True

    def _reunir_movimientos(self, banco, mes_key=None, desde=None, hasta=None):
        """Reúne los datos de la conciliación SIN tocar la interfaz (segundo plano)."""
        # 🚫 Movimientos que el usuario eliminó de esta conciliación: no se vuelven a cargar
        ignorados = self.cargar_ignorados(banco)
        ign_sistema = ignorados.get("sistema", set())
        ign_pdf = ignorados.get("estado_cuenta", set())

        movs_sistema = []
        # ⚠️ Se incluyen también los cobros/pagos SIN cuenta bancaria asignada: si no,
        # no aparecerían en ninguna cuenta y la conciliación saldría vacía.
        for m in self.cargar_movimientos_sistema(banco, incluir_sin_cuenta=True):
            if not self._en_periodo(m.get("fecha"), mes_key, desde, hasta):
                continue
            if m.get("sin_cuenta"):
                m = {**m, "descripcion": f"⚠️ Sin cuenta asignada | {m.get('descripcion', '')}"}
            clave = self._clave_ignorado(m.get("origen", "sistema"), m.get("tabla", ""),
                                         m.get("id", 0), m.get("fecha"), m.get("monto"),
                                         m.get("descripcion"))
            if clave not in ign_sistema:
                movs_sistema.append(m)

        # 🚫 Los pagos y compras cruzadas registrados DESDE el Banco generan su
        # movimiento espejo en Compras (para que el gasto figure pagado), pero en la
        # conciliación ya se ven como movimiento MANUAL: no se listan dos veces.
        # (Sí se usan para los totales: son los que dan el saldo del sistema.)
        sys_movs = [m for m in movs_sistema if not m.get("espejo_banco")]

        manuales = [m for m in self.cargar_manuales(banco)
                    if self._en_periodo(m.get("fecha"), mes_key, desde, hasta)]

        bank_movs = []
        for b in self.movimientos_pdf:
            if not self._en_periodo(b.get("fecha"), mes_key, desde, hasta):
                continue
            clave = self._clave_ignorado("estado_cuenta", "", 0, b.get("fecha"),
                                         b.get("monto"), b.get("descripcion"))
            if clave not in ign_pdf:
                bank_movs.append(b)

        filas = []
        usados = set()

        def emparejar(mov):
            """Busca la línea del estado de cuenta que corresponde a 'mov'."""
            s_fecha = normalizar_fecha(mov["fecha"])
            for i, b in enumerate(bank_movs):
                if i in usados:
                    continue
                if s_fecha and normalizar_fecha(b["fecha"]) == s_fecha and abs(b["monto"] - mov["monto"]) <= 0.02:
                    return i
            cands = [i for i, b in enumerate(bank_movs)
                     if i not in usados and abs(b["monto"] - mov["monto"]) <= 0.02]
            return cands[0] if len(cands) == 1 else None

        def linea_estado_cuenta(b):
            return {**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                    "origen": "estado_cuenta", "tabla": "", "estado": "conciliado"}

        for s in sys_movs:
            idx_match = emparejar(s)
            if idx_match is not None:
                usados.add(idx_match)
                filas.append({**s, "estado": "conciliado"})
                filas.append(linea_estado_cuenta(bank_movs[idx_match]))
            else:
                filas.append({**s, "estado": "diferencia"})

        # Los movimientos MANUALES también se emparejan con el estado de cuenta: así la
        # línea del banco no queda como "sin conciliar" por ocultarse su espejo.
        for m in manuales:
            idx_match = emparejar(m)
            if idx_match is not None:
                usados.add(idx_match)
                filas.append({**m, "estado": "conciliado", "origen": "manual"})
                filas.append(linea_estado_cuenta(bank_movs[idx_match]))
            else:
                filas.append({**m, "estado": m.get("estado", "pendiente"), "origen": "manual"})

        for i, b in enumerate(bank_movs):
            if i not in usados:
                filas.append({**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                              "origen": "estado_cuenta", "tabla": "", "estado": "diferencia"})

        filas.sort(key=lambda f: (normalizar_fecha(f["fecha"]), f["monto"]))
        return {"filas": filas, "movs_sistema": movs_sistema, "bank_movs": bank_movs,
                "desde": desde or "", "hasta": hasta or ""}

    def _pintar_reporte(self, banco, etiqueta_periodo, datos):
        """Dibuja en pantalla la conciliación ya calculada (hilo principal)."""
        filas = datos.get("filas") or []
        movs_sistema = datos.get("movs_sistema") or []
        bank_movs = datos.get("bank_movs") or []
        desde = datos.get("desde") or ""
        hasta = datos.get("hasta") or ""

        self.filas_conciliacion = filas
        self.refrescar_tree()

        saldo_inicial = normalizar_monto(banco.get("saldo_inicial", ""))
        ingresos = sum(m["monto"] for m in movs_sistema if m["monto"] > 0)
        egresos = sum(-m["monto"] for m in movs_sistema if m["monto"] < 0)
        neto_sistema = ingresos - egresos
        neto_pdf = sum(b["monto"] for b in bank_movs)
        diferencia_mes = neto_sistema - neto_pdf

        def _bonita(iso):
            return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}" if len(iso) == 10 else ""

        rango_txt = ""
        if desde or hasta:
            rango_txt = f" ({_bonita(desde) or 'inicio'} al {_bonita(hasta) or 'hoy'})"

        if etiqueta_periodo and etiqueta_periodo != "Todos los meses":
            txt = (f"Banco: {construir_etiqueta_banco(banco)}   |   "
                   f"Periodo: {etiqueta_periodo}{rango_txt}   |   "
                   f"Sistema (neto): {formatear_monto(neto_sistema)}   |   "
                   f"Estado de cuenta (neto): {formatear_monto(neto_pdf)}   |   "
                   f"Diferencia: {formatear_monto(diferencia_mes)}")
        else:
            saldo_sistema = saldo_inicial + neto_sistema
            if self.saldo_final_estado is not None:
                diferencia = saldo_sistema - self.saldo_final_estado
                txt = (f"Banco: {construir_etiqueta_banco(banco)}   |   "
                       f"Saldo Sistema: {formatear_monto(saldo_sistema)}   |   "
                       f"Saldo Estado de Cuenta: {formatear_monto(self.saldo_final_estado)}   |   "
                       f"Diferencia: {formatear_monto(diferencia)}")
            else:
                txt = (f"Banco: {construir_etiqueta_banco(banco)}   |   "
                       f"Saldo Sistema: {formatear_monto(saldo_sistema)}   |   "
                       f"Movimientos PDF (neto): {formatear_monto(neto_pdf)}   |   "
                       f"(No se detectó saldo final en el PDF)")
        self.lbl_resumen.configure(text=txt)

    def cargar_manuales(self, banco):
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        movs = []
        try:
            with conn.cursor() as c:
                try:
                    c.execute("""
                        SELECT id, fecha, descripcion, monto, tipo, estado,
                               COALESCE(categoria, ''), COALESCE(subcategoria, ''),
                               COALESCE(evento, ''), COALESCE(personal, ''),
                               COALESCE(comision, 0), COALESCE(interbancario, FALSE),
                               COALESCE(es_cruzada, FALSE), COALESCE(numero_operacion, '')
                        FROM conciliacion_bancaria
                        WHERE origen = 'manual' AND banco = %s
                        ORDER BY id
                    """, (banco.get("banco", ""),))
                    filas = c.fetchall()
                except Exception:
                    # Base de datos anterior sin las columnas de detalle
                    conn.rollback()
                    c.execute("""
                        SELECT id, fecha, descripcion, monto, tipo, estado
                        FROM conciliacion_bancaria
                        WHERE origen = 'manual' AND banco = %s
                        ORDER BY id
                    """, (banco.get("banco", ""),))
                    filas = [tuple(f) + ("", "", "", "", 0, False, False, "") for f in c.fetchall()]

                for (idp, fecha, desc, monto, tipo, estado, categoria, subcategoria,
                     evento, personal, comision, interbancario, es_cruzada, num_oper) in filas:
                    movs.append({
                        "id": idp, "fecha": fecha or "", "descripcion": desc or "",
                        "monto": float(monto or 0), "tipo": tipo or "ingreso",
                        "tabla": "conciliacion", "estado": estado or "pendiente",
                        "categoria": categoria or "", "subcategoria": subcategoria or "",
                        "evento": evento or "", "personal": personal or "",
                        "comision": float(comision or 0), "interbancario": bool(interbancario),
                        "es_cruzada": bool(es_cruzada),
                        "numero_operacion": num_oper or "",
                        # En la conciliación el N° de operación se ve como N° de documento
                        "documento": num_oper or "",
                    })
        except Exception:
            pass
        finally:
            liberar_conexion(conn)
        return movs

    def refrescar_tree(self):
        self.tabla_conc.delete(*self.tabla_conc.get_children())
        for i, f in enumerate(self.filas_conciliacion):
            estado = f.get("estado", "pendiente")
            if estado == "conciliado":
                estado_txt = "✅ Conciliado"
                tag = "conciliado"
            elif f.get("origen") == "estado_cuenta":
                estado_txt = "❌ Sin conciliar (Banco)"
                tag = "diferencia"
            elif estado == "diferencia":
                estado_txt = "⚠️ Sin conciliar (Sistema)"
                tag = "diferencia"
            else:
                estado_txt = "📝 Manual / Pendiente"
                tag = "estado_cuenta"
            if f.get("tipo") == "transferencia":
                tipo_txt = "Transferencia (Ingreso)" if f["monto"] >= 0 else "Transferencia (Egreso)"
            else:
                tipo_txt = "Ingreso" if f["monto"] >= 0 else "Egreso"
            origen_txt = {"sistema": "Sistema", "estado_cuenta": "Estado de Cuenta",
                          "manual": "Manual"}.get(f.get("origen"), f.get("origen", ""))
            self.tabla_conc.insert("", tk.END, iid=str(i),
                                   values=(estado_txt, f["fecha"], f.get("documento", ""),
                                           f["descripcion"], tipo_txt, formatear_monto(f["monto"]),
                                           origen_txt),
                                   tags=(tag,))

    # -----------------------------------------------------
    # ACCIONES DE CONCILIACION
    # -----------------------------------------------------
    def _filas_seleccionadas(self):
        sel = self.tabla_conc.selection()
        return [self.filas_conciliacion[int(i)] for i in sel if i.isdigit()]

    def conciliar_seleccionados(self):
        filas = self._filas_seleccionadas()
        if not filas:
            messagebox.showinfo("Conciliación", "Seleccione uno o más movimientos para conciliar.",
                                parent=self.parent_frame)
            return
        banco = self.banco_seleccionado()
        for f in filas:
            f["estado"] = "conciliado"
            self._persistir_conciliado(f, banco)
        self.refrescar_tree()
        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Concilió {len(filas)} movimiento(s) en {construir_etiqueta_banco(banco) if banco else 'banco'}")
        messagebox.showinfo("Conciliación", f"{len(filas)} movimiento(s) marcado(s) como conciliado(s).",
                            parent=self.parent_frame)

    def _persistir_conciliado(self, f, banco):
        if not banco:
            return
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT COUNT(*) FROM conciliacion_bancaria
                    WHERE origen = %s AND id_movimiento = %s AND banco = %s
                """, (f.get("origen", ""), f.get("id", 0), banco.get("banco", "")))
                if c.fetchone()[0] == 0:
                    c.execute("""
                        INSERT INTO conciliacion_bancaria
                        (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado, fecha_conciliacion)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'conciliado', %s)
                    """, (banco.get("banco", ""), banco.get("cuenta", ""), f.get("fecha", ""),
                          f.get("descripcion", ""), f.get("monto", 0), f.get("tipo", "ingreso"),
                          f.get("origen", ""), f.get("id", 0), datetime.now().strftime("%Y-%m-%d")))
                    conn.commit()
        except Exception:
            pass
        finally:
            liberar_conexion(conn)

    def _datos_iniciales_pago(self, registro, cats, principales):
        """Datos de un pago ya registrado, para abrir la ventana en modo edición.

        Se toman de las columnas de detalle del pago y, cuando el registro es
        antiguo y no las tiene, se deducen de su descripción.
        """
        datos = {
            "fecha": str(registro.get("fecha") or ""),
            "monto": abs(float(registro.get("monto") or 0)),
            "principal": str(registro.get("categoria") or ""),
            "sub": str(registro.get("subcategoria") or ""),
            "evento": str(registro.get("evento") or ""),
            "personal": str(registro.get("personal") or ""),
            "numero_operacion": str(registro.get("numero_operacion") or ""),
            "comision": float(registro.get("comision") or 0),
            "interbancario": bool(registro.get("interbancario")),
            "es_cruzada": bool(registro.get("es_cruzada")),
            "descripcion": "",
        }
        libres = []
        for parte in [p.strip() for p in str(registro.get("descripcion") or "").split(" - ") if p.strip()]:
            bajo = parte.lower()
            if bajo.startswith("evento "):
                datos["evento"] = datos["evento"] or parte[7:].strip()
            elif bajo.startswith("placa "):
                # Movimientos antiguos (programa original): la placa era el evento
                datos["evento"] = datos["evento"] or parte[6:].strip()
            elif bajo.startswith("personal "):
                datos["personal"] = datos["personal"] or parte[9:].strip()
            elif bajo.startswith("chofer "):
                datos["personal"] = datos["personal"] or parte[7:].strip()
            elif bajo.startswith("comisión interbancaria") or bajo.startswith("comision interbancaria"):
                datos["interbancario"] = True
                if not datos["comision"]:
                    datos["comision"] = monto_desde_texto(parte)
            elif parte == datos["principal"] or (datos["sub"] and parte == datos["sub"]):
                pass                      # ya se recuperó de las columnas del pago
            elif not datos["principal"] and parte in principales:
                datos["principal"] = parte
            elif datos["principal"] and not datos["sub"] and parte in (cats.get(datos["principal"]) or []):
                datos["sub"] = parte
            else:
                libres.append(parte)
        datos["descripcion"] = " - ".join(libres)
        return datos

    def agregar_movimiento_manual(self, registro=None):
        """Ventana de Registrar Pago / Compra Cruzada.

        Si se recibe 'registro' (una fila de la conciliación de origen manual)
        se abre ESTA MISMA ventana en modo edición, con todos los datos del
        pago cargados, y al guardar se actualiza ese registro en lugar de
        crear uno nuevo.
        """
        banco = self.banco_seleccionado()
        if not banco:
            messagebox.showwarning("Banco", "Seleccione un banco.", parent=self.parent_frame)
            return

        cats = cargar_categorias_gastos()
        principales = list(cats.keys()) or ["Producción de Eventos"]
        comision = cargar_comision_interbancaria()
        en_edicion = bool(registro)
        datos_ini = (self._datos_iniciales_pago(registro, cats, principales) if en_edicion else {})
        id_edicion = registro.get("id") if en_edicion else None

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Editar Pago (Conciliación)" if en_edicion else "Registrar Pago (Conciliación)")
        v.geometry("580x700")
        v.minsize(520, 480)
        v.transient(self.parent_frame)
        v.grab_set()

        ctk.CTkLabel(v, text=("✏️ Editar Pago / Compra Cruzada" if en_edicion
                              else "➕ Registrar Pago / Compra Cruzada"),
                     font=("Arial", 15, "bold"), text_color="#1f538d").pack(pady=(15, 5))

        # Pie fijo: el botón Guardar siempre visible
        f_pie = ctk.CTkFrame(v, fg_color="transparent")
        f_pie.pack(side="bottom", fill="x")

        # Área desplazable (scroll) con todos los campos
        f = ctk.CTkScrollableFrame(v, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        principal_ini = str(datos_ini.get("principal") or "")
        if principal_ini and principal_ini not in principales:
            principales = principales + [principal_ini]

        ctk.CTkLabel(f, text="Categoría Principal:", font=("Arial", 11, "bold")).pack(anchor="w")
        cmb_principal = ctk.CTkComboBox(f, values=principales, width=300, state="readonly")
        cmb_principal.pack(fill="x", pady=(0, 8))
        cmb_principal.set(principal_ini or principales[0])

        sub_ini = str(datos_ini.get("sub") or "")
        ctk.CTkLabel(f, text="Categoría:", font=("Arial", 11, "bold")).pack(anchor="w")
        subs_inicial = list(cats.get(cmb_principal.get(), []) or ["(Sin categoría)"])
        if sub_ini and sub_ini not in subs_inicial:
            subs_inicial.append(sub_ini)
        cmb_sub = ctk.CTkComboBox(f, values=subs_inicial, width=300, state="readonly")
        cmb_sub.pack(fill="x", pady=(0, 8))
        cmb_sub.set(sub_ini or subs_inicial[0])

        # 🎪 Evento asociado: la dimensión de trabajo de este sistema de EVENTOS
        # (en el programa original este desplegable mostraba las placas de la flota).
        # ⚡ Las listas que vienen de Supabase (eventos, personal y proveedores) NO se
        # consultan aquí: la ventana abre al instante con lo que ya está en la caché
        # del sistema y el resto se completa después, en segundo plano.
        eventos = leer_cache("lista_eventos_aprobados")
        if "OFICINA | Trabajos Internos" not in eventos:
            eventos = ["OFICINA | Trabajos Internos"] + eventos
        lbl_evento = ctk.CTkLabel(f, text="Evento Asociado:", font=("Arial", 11, "bold"))
        cmb_evento = ctk.CTkComboBox(f, values=["Ninguno"] + eventos, width=300)
        cmb_evento.set("Ninguno")

        # 👤 Personal / beneficiario: se pide solo en los pagos de planilla o de
        # personal del evento (en el original eran los choferes de la flota).
        personal_nomina = leer_cache("banco_lista_personal")
        lbl_personal = ctk.CTkLabel(f, text="Personal / Beneficiario (planilla):",
                                    font=("Arial", 11, "bold"))
        cmb_personal = ctk.CTkComboBox(f, values=["Ninguno"] + personal_nomina, width=300)
        cmb_personal.set("Ninguno")

        def actualizar_personal():
            if es_categoria_personal(f"{cmb_principal.get()} {cmb_sub.get()}"):
                lbl_personal.pack(anchor="w", pady=(0, 2), before=btn_gestion)
                cmb_personal.pack(fill="x", pady=(0, 8), before=btn_gestion)
            else:
                lbl_personal.pack_forget()
                cmb_personal.pack_forget()

        def on_principal(_=None):
            subs = cats.get(cmb_principal.get(), []) or ["(Sin categoría)"]
            cmb_sub.configure(values=subs)
            cmb_sub.set(subs[0])
            actualizar_personal()
        cmb_principal.configure(command=on_principal)
        cmb_sub.configure(command=lambda _=None: actualizar_personal())

        def recargar_categorias_combos():
            nonlocal cats, principales
            cats = cargar_categorias_gastos()
            principales = list(cats.keys()) or ["Producción de Eventos"]
            cmb_principal.configure(values=principales)
            cmb_principal.set(principales[0])
            on_principal()

        def abrir_gestion():
            # Categorías de gastos del sistema: al guardar y cerrar se recargan
            # aquí los desplegables con la lista nueva.
            self.gestionar_categorias(al_guardar=recargar_categorias_combos)
        btn_gestion = ctk.CTkButton(f, text="⚙️ Gestionar Categorías", height=26, font=("Arial", 11),
                                    fg_color="#8e44ad", hover_color="#703688", command=abrir_gestion)
        btn_gestion.pack(fill="x", pady=(0, 8))
        # 🎪 El evento se pide SIEMPRE: en este sistema todo movimiento se imputa a un evento.
        lbl_evento.pack(anchor="w", pady=(0, 2), before=btn_gestion)
        cmb_evento.pack(fill="x", pady=(0, 8), before=btn_gestion)
        on_principal()

        # En edición se restauran los valores guardados (on_principal los reinicia)
        if sub_ini:
            valores_sub = list(cmb_sub.cget("values"))
            if sub_ini not in valores_sub:
                cmb_sub.configure(values=valores_sub + [sub_ini])
            cmb_sub.set(sub_ini)
        evento_ini = str(datos_ini.get("evento") or "")
        if evento_ini and evento_ini not in ("Ninguno", "(Sin eventos)"):
            valores_evento = list(cmb_evento.cget("values"))
            if evento_ini not in valores_evento:
                valores_evento.append(evento_ini)        # evento ya no aprobado / antiguo
                cmb_evento.configure(values=valores_evento)
            cmb_evento.set(evento_ini)
        personal_ini = str(datos_ini.get("personal") or "")
        if personal_ini:
            valores_personal = list(cmb_personal.cget("values"))
            if personal_ini not in valores_personal:
                valores_personal.append(personal_ini)
                cmb_personal.configure(values=valores_personal)
            cmb_personal.set(personal_ini)
        actualizar_personal()

        ctk.CTkLabel(f, text="Fecha (DD/MM/AAAA):", font=("Arial", 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(f, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 8))
        ent_fecha = ctk.CTkEntry(f_fecha)
        ent_fecha.pack(side="left", fill="x", expand=True)
        ent_fecha.insert(0, str(datos_ini.get("fecha") or datetime.now().strftime("%d/%m/%Y")))
        ctk.CTkButton(f_fecha, text="📅", width=42, font=("Arial", 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(f, text="N° de Operación (opcional):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_operacion = ctk.CTkEntry(f, placeholder_text="Ej.: 00123456 / constancia de la transferencia")
        ent_operacion.pack(fill="x", pady=(0, 8))
        ent_operacion.insert(0, str(datos_ini.get("numero_operacion") or ""))

        ctk.CTkLabel(f, text="Descripción:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_desc = ctk.CTkEntry(f)
        ent_desc.pack(fill="x", pady=(0, 8))
        ent_desc.insert(0, str(datos_ini.get("descripcion") or ""))

        ctk.CTkLabel(f, text="Monto:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_monto = ctk.CTkEntry(f)
        ent_monto.pack(fill="x", pady=(0, 8))
        if datos_ini.get("monto"):
            ent_monto.insert(0, formatear_numero_entrada(f"{float(datos_ini['monto']):.2f}"))
        ent_monto.bind("<KeyRelease>", lambda e: self._formatear_entrada_monto(ent_monto))
        ent_monto.bind("<FocusOut>", lambda e: self._formatear_entrada_monto(ent_monto))

        # 💰 Igual que en Compras: el monto se puede digitar SIN IGV (monto base) o
        # CON IGV (total del documento); lo que falte se calcula automáticamente.
        ctk.CTkLabel(f, text="Tipo de Monto a Ingresar:", font=("Arial", 11, "bold")).pack(anchor="w")
        seg_monto = ctk.CTkSegmentedButton(
            f, values=["Monto Base (sin IGV)", "Monto con IGV (Total)"],
            font=("Arial", 10, "bold"), command=lambda _v: actualizar_total())
        seg_monto.pack(fill="x", pady=(0, 6))
        seg_monto.set("Monto Base (sin IGV)")

        f_igv = ctk.CTkFrame(f, fg_color="transparent")
        f_igv.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(f_igv, text="IGV (%):", font=("Arial", 11, "bold")).pack(side="left")
        ent_igv = ctk.CTkEntry(f_igv, width=70)
        ent_igv.pack(side="left", padx=(6, 0))
        ent_igv.insert(0, "0")
        ent_igv.bind("<KeyRelease>", lambda e: actualizar_total())

        lbl_igv_det = ctk.CTkLabel(f, text="", font=("Arial", 10), text_color="#555555",
                                   wraplength=520, justify="left")
        lbl_igv_det.pack(anchor="w", pady=(0, 8))

        def montos_con_igv():
            """(subtotal, igv, total) del gasto según el modo elegido y el % de IGV.

            Con IGV 0% el monto se registra tal cual (como se hacía antes).
            """
            valor = normalizar_monto(ent_monto.get())
            pct = max(normalizar_monto(ent_igv.get()), 0.0)
            if "con igv" in str(seg_monto.get()).lower():
                total_gasto = valor
                subtotal = (total_gasto / (1.0 + pct / 100.0)) if pct else total_gasto
            else:
                subtotal = valor
                total_gasto = (subtotal * (1.0 + pct / 100.0)) if pct else subtotal
            igv = total_gasto - subtotal
            return round(subtotal, 2), round(igv, 2), round(total_gasto, 2)

        var_inter = tk.BooleanVar(value=bool(datos_ini.get("interbancario")))
        chk_inter = ctk.CTkCheckBox(f, text="Pago Interbancario", variable=var_inter)
        chk_inter.pack(anchor="w", pady=(0, 4))
        f_com = ctk.CTkFrame(f, fg_color="transparent")
        f_com.pack(fill="x", pady=(0, 6))
        ctk.CTkLabel(f_com, text="Comisión (S/.):", font=("Arial", 11, "bold")).pack(side="left")
        ent_comision = ctk.CTkEntry(f_com, width=90)
        ent_comision.pack(side="left", padx=6)
        comision_ini = datos_ini.get("comision")
        ent_comision.insert(0, f"{(float(comision_ini) if comision_ini else comision):.2f}")
        lbl_total = ctk.CTkLabel(f_com, text=f"Total: {formatear_monto(0)}", font=("Arial", 12, "bold"),
                                 text_color="#1f538d")
        lbl_total.pack(side="right")

        def actualizar_total(*_):
            sub_gasto, igv_gasto, total_gasto = montos_con_igv()
            com = normalizar_monto(ent_comision.get())
            total = total_gasto + (com if var_inter.get() else 0)
            lbl_total.configure(text=f"Total: {formatear_monto(total)}")
            if igv_gasto > 0:
                lbl_igv_det.configure(
                    text=f"Base: {formatear_monto(sub_gasto)}   +   IGV: {formatear_monto(igv_gasto)}"
                         f"   =   Compra en Compras: {formatear_monto(total_gasto)}")
            else:
                lbl_igv_det.configure(text="Sin IGV (0%): el monto se registra tal cual.")
        chk_inter.configure(command=actualizar_total)
        ent_monto.bind("<KeyRelease>", actualizar_total)
        ent_comision.bind("<KeyRelease>", actualizar_total)
        actualizar_total()   # muestra de una vez el total y el detalle base/IGV

        # ------------------------------------------------------------------
        # UNIFICACIÓN CON COMPRAS CRUZADAS (pago a tercero)
        # Al marcar el check se habilitan los datos del tercero y el PDF del soporte
        # ------------------------------------------------------------------
        var_cruzada = tk.BooleanVar(value=False)
        chk_cruzada = ctk.CTkCheckBox(f, text="🔁 Es compra cruzada (factura pagada por un tercero)",
                                      font=("Arial", 11, "bold"), variable=var_cruzada)
        if not en_edicion:
            chk_cruzada.pack(anchor="w", pady=(4, 4))
        elif datos_ini.get("es_cruzada"):
            # La compra cruzada ya quedó registrada en Compras: al editar no se repite
            ctk.CTkLabel(f, text=("🔁 Este pago se registró como COMPRA CRUZADA en el módulo de Compras.\n"
                                  "Aquí solo se modifican los datos del movimiento bancario."),
                         font=("Arial", 11, "italic"), text_color="#7d3c98",
                         justify="left").pack(anchor="w", pady=(6, 4))

        estado_cruzada = {"soporte": "", "factura": ""}
        f_cruzada = ctk.CTkFrame(f, fg_color="#f4ecf7", corner_radius=8,
                                 border_width=1, border_color="#d7bde2")

        ctk.CTkLabel(f_cruzada, text="Proveedor de la factura:", font=("Arial", 11, "bold")).pack(
            anchor="w", padx=10, pady=(8, 0))
        cmb_cruz_prov = ctk.CTkComboBox(
            f_cruzada, values=leer_cache("lista_proveedores_combobox") or ["⏳ Cargando proveedores..."],
            width=300)
        cmb_cruz_prov.pack(fill="x", padx=10, pady=(0, 6))
        cmb_cruz_prov.set("")

        ctk.CTkLabel(f_cruzada, text="N° Documento / Factura (opcional):", font=("Arial", 11, "bold")).pack(
            anchor="w", padx=10)
        ent_cruz_nro = ctk.CTkEntry(f_cruzada)
        ent_cruz_nro.pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkLabel(f_cruzada, text="Pagado a (tercero):", font=("Arial", 11, "bold")).pack(
            anchor="w", padx=10)
        ent_cruz_tercero = ctk.CTkEntry(f_cruzada)
        ent_cruz_tercero.pack(fill="x", padx=10, pady=(0, 6))

        lbl_cruz_soporte = ctk.CTkLabel(f_cruzada, text="📄 Factura de compra: sin cargar\n🧾 Soporte del pago: sin cargar",
                                        font=("Arial", 11, "italic"), text_color="#7d3c98",
                                        wraplength=400, justify="left")
        lbl_cruz_soporte.pack(anchor="w", padx=10, pady=(0, 4))

        def actualizar_lbl_docs():
            fact = os.path.basename(estado_cruzada["factura"]) if estado_cruzada["factura"] else "sin cargar"
            sop = os.path.basename(estado_cruzada["soporte"]) if estado_cruzada["soporte"] else "sin cargar"
            lbl_cruz_soporte.configure(text=f"📄 Factura de compra: {fact}\n🧾 Soporte del pago: {sop}")

        def cargar_documentos_cruzada():
            """Un solo botón: carga la factura de compra y/o el comprobante del pago a tercero."""
            rutas = seleccionar_archivos_dialogo(
                titulo="Cargar Factura de Compra y/o Comprobante de Pago (puede elegir ambos)",
                tipos=[("Archivos PDF", "*.pdf"), ("Imágenes", "*.png;*.jpg;*.jpeg")])
            if not rutas:
                return
            for ruta in rutas:
                datos = {}
                if ruta.lower().endswith(".pdf"):
                    try:
                        datos = extraer_datos_factura_pdf(ruta) or {}
                    except Exception:
                        datos = {}
                # Se reconoce como factura de compra si el PDF trae N° de documento o RUC SUNAT
                es_factura = bool(datos.get("numero_documento") or datos.get("ruc"))
                if es_factura and not estado_cruzada["factura"]:
                    estado_cruzada["factura"] = ruta
                    if datos.get("proveedor") and not cmb_cruz_prov.get().strip():
                        cmb_cruz_prov.set(datos["proveedor"])
                    if datos.get("numero_documento"):
                        ent_cruz_nro.delete(0, tk.END)
                        ent_cruz_nro.insert(0, datos["numero_documento"])
                    if datos.get("fecha"):
                        ent_fecha.delete(0, tk.END)
                        ent_fecha.insert(0, datos["fecha"])
                    if datos.get("total") and not ent_monto.get().strip():
                        ent_monto.insert(0, formatear_numero_entrada(f"{datos['total']:.2f}"))
                        # 💰 El PDF trae el IMPORTE TOTAL: si es factura se asume 18% de IGV
                        # (el usuario puede cambiar el modo o el % antes de guardar).
                        tipo_pdf = str(datos.get("tipo_documento") or "").upper()
                        ent_igv.delete(0, tk.END)
                        if tipo_pdf == "FACTURA":
                            ent_igv.insert(0, "18")
                            seg_monto.set("Monto con IGV (Total)")
                        else:
                            ent_igv.insert(0, "0")
                            seg_monto.set("Monto Base (sin IGV)")
                        actualizar_total()
                else:
                    estado_cruzada["soporte"] = ruta
            actualizar_lbl_docs()

        def intercambiar_documentos():
            """Si el reconocimiento automático los asignó al revés, se intercambian."""
            estado_cruzada["factura"], estado_cruzada["soporte"] = \
                estado_cruzada["soporte"], estado_cruzada["factura"]
            actualizar_lbl_docs()

        def ver_documento_cruzada(cual):
            ruta = estado_cruzada.get(cual, "")
            if ruta and os.path.isfile(ruta):
                abrir_documento(ruta)
            else:
                messagebox.showinfo("Documentos",
                                    "Todavía no ha cargado " +
                                    ("la factura de compra." if cual == "factura"
                                     else "el comprobante del pago."),
                                    parent=v)

        f_btn_cruz = ctk.CTkFrame(f_cruzada, fg_color="transparent")
        f_btn_cruz.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_btn_cruz, text="🧾 Cargar Factura de Compra y Comprobante de Pago", height=30,
                      font=("Arial", 11, "bold"), fg_color="#2980b9", hover_color="#1f618d",
                      command=cargar_documentos_cruzada).pack(fill="x")
        f_ver_cruz = ctk.CTkFrame(f_cruzada, fg_color="transparent")
        f_ver_cruz.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_ver_cruz, text="👁 Ver factura", width=110, height=26, font=("Arial", 11),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: ver_documento_cruzada("factura")).pack(side="left")
        ctk.CTkButton(f_ver_cruz, text="👁 Ver comprobante", width=140, height=26, font=("Arial", 11),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: ver_documento_cruzada("soporte")).pack(side="left", padx=6)
        ctk.CTkButton(f_ver_cruz, text="🔄 Intercambiar", width=120, height=26, font=("Arial", 11),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=intercambiar_documentos).pack(side="left")

        def on_cruzada():
            if var_cruzada.get():
                f_cruzada.pack(fill="x", pady=(2, 8))
            else:
                f_cruzada.pack_forget()
        chk_cruzada.configure(command=on_cruzada)

        def guardar():
            base = normalizar_monto(ent_monto.get())
            if base <= 0:
                messagebox.showerror("Error", "El monto debe ser mayor a 0.", parent=v)
                return
            com = normalizar_monto(ent_comision.get())
            inter = var_inter.get()
            # 💰 Base e IGV del gasto (según el modo y el % elegidos en la ventana)
            sub_gasto, igv_gasto, total_gasto = montos_con_igv()
            total = total_gasto + (com if inter else 0)
            # Solo se envía el detalle cuando el gasto SÍ tiene IGV: así los pagos
            # sin IGV (planilla, transferencias, etc.) se registran como siempre.
            detalle_igv = {}
            if igv_gasto > 0:
                detalle_igv = {"subtotal": round(sub_gasto + (com if inter else 0), 2),
                               "impuesto": round(igv_gasto, 2)}

            guardar_comision_interbancaria(f"{com:.2f}")

            principal = cmb_principal.get()
            sub = cmb_sub.get()
            if sub == "(Sin categoría)":
                sub = ""
            # 🎪 Evento al que se imputa el movimiento (dimensión del sistema de eventos)
            evento = ""
            ev = cmb_evento.get().strip()
            if ev and ev not in ("Ninguno", "(Sin eventos)"):
                evento = ev
            # 👤 Personal / beneficiario, solo en los pagos de planilla o personal
            personal_pago = ""
            if es_categoria_personal(f"{principal} {sub}"):
                pv = cmb_personal.get().strip()
                if pv and pv != "Ninguno":
                    personal_pago = pv
            fecha = ent_fecha.get().strip() or datetime.now().strftime("%d/%m/%Y")
            # 🚀 Corte del sistema: no se registran movimientos anteriores a la fecha de comienzo
            if validar_fecha_registro is not None:
                try:
                    permitido, aviso = validar_fecha_registro(fecha)
                except Exception:
                    permitido, aviso = True, ""
                if not permitido:
                    return messagebox.showwarning("Fecha fuera del corte del sistema", aviso, parent=v)
            desc_manual = ent_desc.get().strip()
            numero_operacion = ent_operacion.get().strip()

            tipo = "ingreso" if principal == "Ingresos" else "egreso"
            monto_final = total if tipo == "ingreso" else -total

            partes = []
            if principal:
                partes.append(principal)
            if sub:
                partes.append(sub)
            if evento:
                partes.append(f"Evento {evento}")
            if personal_pago:
                partes.append(f"Personal {personal_pago}")
            if desc_manual:
                partes.append(desc_manual)
            if inter:
                partes.append(f"Comisión interbancaria {formatear_monto(com)}")
            desc = " - ".join(p for p in partes if p) or "Ajuste manual de conciliación"

            es_cruzada = bool(var_cruzada.get())
            if es_cruzada:
                if not cmb_cruz_prov.get().strip():
                    messagebox.showwarning("Compra cruzada", "Ingrese el proveedor de la factura.",
                                           parent=v)
                    return
                if not ent_cruz_tercero.get().strip():
                    messagebox.showwarning("Compra cruzada", "Ingrese quién pagó (tercero).", parent=v)
                    return
                faltantes = []
                if not (estado_cruzada["factura"] and os.path.isfile(estado_cruzada["factura"])):
                    faltantes.append("la factura de compra")
                if not (estado_cruzada["soporte"] and os.path.isfile(estado_cruzada["soporte"])):
                    faltantes.append("el comprobante del pago a tercero")
                if faltantes:
                    if not messagebox.askyesno(
                            "Compra cruzada",
                            "No se ha cargado " + " ni ".join(faltantes) + ".\n"
                            "¿Desea registrar la compra cruzada sin " +
                            ("esos documentos" if len(faltantes) > 1 else "ese documento") + "?",
                            parent=v):
                        return

            id_movimiento = id_edicion or 0
            id_gasto_previo = 0
            conn = conectar_db(silencioso=True)
            if conn:
                try:
                    with conn.cursor() as c:
                        if en_edicion:
                            c.execute("""
                                UPDATE conciliacion_bancaria
                                SET fecha=%s, descripcion=%s, monto=%s, tipo=%s,
                                    categoria=%s, subcategoria=%s, evento=%s, personal=%s,
                                    comision=%s, interbancario=%s, numero_operacion=%s
                                WHERE id=%s
                            """, (fecha, desc, monto_final, tipo, principal, sub, evento, personal_pago,
                                  com, bool(inter), numero_operacion, id_edicion))
                            try:
                                c.execute("SELECT COALESCE(id_gasto_compras, 0) FROM conciliacion_bancaria WHERE id=%s",
                                          (id_edicion,))
                                fila = c.fetchone()
                                id_gasto_previo = int(fila[0] or 0) if fila else 0
                            except Exception:
                                conn.rollback()
                                id_gasto_previo = 0
                        else:
                            c.execute("""
                                INSERT INTO conciliacion_bancaria
                                (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado,
                                 categoria, subcategoria, evento, personal, comision, interbancario, es_cruzada,
                                 numero_operacion)
                                VALUES (%s, %s, %s, %s, %s, %s, 'manual', 0, 'pendiente',
                                        %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                            """, (banco.get("banco", ""), banco.get("cuenta", ""), fecha, desc,
                                  monto_final, tipo, principal, sub, evento, personal_pago, com,
                                  bool(inter), es_cruzada, numero_operacion))
                            try:
                                id_movimiento = c.fetchone()[0]
                            except Exception:
                                id_movimiento = 0
                        conn.commit()
                except Exception as e:
                    messagebox.showerror("Error", f"No se pudo guardar:\n{e}", parent=v)
                    liberar_conexion(conn)
                    return
                finally:
                    liberar_conexion(conn)
            registrar_auditoria(self.usuario_activo, "Banco",
                                (f"Editó el pago {formatear_monto(monto_final)} en {construir_etiqueta_banco(banco)}"
                                 if en_edicion else
                                 f"Agregó movimiento manual {formatear_monto(monto_final)} en {construir_etiqueta_banco(banco)}"))

            # 🧾 El EGRESO también se registra en Compras como gasto YA PAGADO,
            # para que se vea en las dos pestañas de ese módulo y en sus reportes.
            if tipo == "egreso" and not es_cruzada:
                categoria_gasto = f"{principal} - {sub}".strip(" -") if sub else principal
                proveedor_gasto = (desc_manual or (f"Personal {personal_pago}" if personal_pago else "")
                                   or (f"Evento {evento}" if evento else "") or sub or principal)
                self.registrar_egreso_en_compras({
                    "fecha": fecha, "total": total, "descripcion": desc or desc_manual,
                    "categoria": categoria_gasto, "proveedor": proveedor_gasto,
                    "evento": evento, "banco": construir_etiqueta_banco(banco),
                    "numero_operacion": numero_operacion,
                    "soporte": estado_cruzada.get("soporte", "") if isinstance(estado_cruzada, dict) else "",
                    "id_gasto": id_gasto_previo,
                    **detalle_igv,
                }, id_movimiento=id_movimiento, parent=v)

            if es_cruzada:
                ok, estado_txt = self.registrar_compra_cruzada_desde_pago({
                    "proveedor": cmb_cruz_prov.get().strip(),
                    "nro": ent_cruz_nro.get().strip(),
                    "fecha": fecha,
                    "total": total,
                    "tercero": ent_cruz_tercero.get().strip(),
                    "categoria": f"{principal} - {sub}".strip(" -") if sub else principal,
                    "descripcion": desc,
                    "evento": evento,
                    "soporte_origen": estado_cruzada["soporte"],
                    "factura_origen": estado_cruzada["factura"],
                    "banco": construir_etiqueta_banco(banco),
                    "numero_operacion": numero_operacion,
                    "id_movimiento": id_movimiento,
                    **detalle_igv,
                }, v)
                if not ok:
                    messagebox.showwarning(
                        "Compra cruzada",
                        "El movimiento del banco quedó registrado, pero la compra cruzada no se pudo "
                        "guardar en Compras. Vuelva a intentar el registro del pago.",
                        parent=v)
                    self.generar_reporte()
                    return
                messagebox.showinfo(
                    "Compra cruzada",
                    f"Compra cruzada {estado_txt} en el módulo de Compras "
                    f"(proveedor {cmb_cruz_prov.get().strip()}, pagada por "
                    f"{ent_cruz_tercero.get().strip()}).",
                    parent=v)

            if en_edicion:
                messagebox.showinfo("Pago actualizado",
                                    "Los datos del pago se actualizaron correctamente.", parent=v)
            v.destroy()
            self.generar_reporte()

        ctk.CTkButton(f_pie, text=("💾 Guardar Cambios" if en_edicion else "✅ Guardar"),
                      width=160, height=36, font=("Arial", 13, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=guardar).pack(pady=10)
        ctk.CTkButton(f_pie, text="✖ Cancelar", width=110, height=36, font=("Arial", 12),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=v.destroy).pack(pady=(0, 10))

        # ------------------------------------------------------------------
        # ⚡ LISTAS DE SUPABASE EN SEGUNDO PLANO
        # Cada consulta tarda entre 0,5 y 5 s: si se hicieran al construir la
        # ventana, esta tardaría varios segundos en abrir. Se traen aparte y, al
        # llegar, se completan los desplegables sin perder lo ya elegido.
        # ------------------------------------------------------------------
        def _traer_listas(estado):
            estado["eventos"] = cargar_eventos()
            estado["personal"] = cargar_personal_eventos()
            estado["proveedores"] = cargar_proveedores()

        def _completar_combo(combo, nuevos, texto_vacio):
            valores = [str(x) for x in (nuevos or [])] or [texto_vacio]
            actual = combo.get().strip()
            if actual and actual not in valores:
                valores.append(actual)        # elegido antes o escrito a mano
            combo.configure(values=valores)
            if actual:
                combo.set(actual)

        def _aplicar_listas(estado):
            try:
                if not v.winfo_exists():
                    return
            except Exception:
                return
            _completar_combo(cmb_evento, ["Ninguno"] + list(estado.get("eventos") or []), "Ninguno")
            _completar_combo(cmb_personal, ["Ninguno"] + list(estado.get("personal") or []), "Ninguno")
            _completar_combo(cmb_cruz_prov, list(estado.get("proveedores") or []), "(Sin proveedores)")

        ejecutar_en_hilo(v, _traer_listas, al_terminar=_aplicar_listas)

    def _formatear_entrada_monto(self, entry):
        """Da formato con separador de miles al escribir un monto en un campo de texto."""
        try:
            txt = entry.get()
        except Exception:
            return
        fmt = formatear_numero_entrada(txt)
        if fmt != txt:
            entry.delete(0, tk.END)
            entry.insert(0, fmt)

    def registrar_compra_cruzada_desde_pago(self, datos, parent=None):
        """Registra (o actualiza, sin duplicar) una compra cruzada en el módulo de Compras
        a partir del pago registrado en la pestaña de Conciliación.

        Devuelve (ok, estado) donde estado es 'creada' o 'actualizada'."""
        parent = parent or self.parent_frame
        proveedor = (datos.get("proveedor") or "").strip()
        tercero = (datos.get("tercero") or "").strip()
        if not proveedor:
            messagebox.showwarning("Compra cruzada", "Ingrese el proveedor de la factura.", parent=parent)
            return False, ""
        if not tercero:
            messagebox.showwarning("Compra cruzada", "Ingrese quién pagó (tercero).", parent=parent)
            return False, ""

        nro = (datos.get("nro") or "").strip().replace(" ", "")
        fecha = datos.get("fecha") or datetime.now().strftime("%d/%m/%Y")
        total = float(datos.get("total") or 0)
        # 💰 Base + IGV definidos en la ventana de pago (si el gasto tiene IGV)
        subtotal_cruz, impuesto_cruz = detalle_base_igv(datos)
        subtotal_bd = subtotal_cruz if subtotal_cruz is not None else total
        impuesto_bd = impuesto_cruz if impuesto_cruz is not None else 0.0
        categoria = datos.get("categoria") or "Compra Cruzada"
        descripcion = (datos.get("descripcion") or "").strip() or "Compra cruzada pagada por tercero"
        # 🎪 Evento al que se imputa la compra (columna evento_asociado del módulo de Compras)
        evento_cruz = str(datos.get("evento") or "").strip()
        # Cuenta bancaria con la que salió el dinero (se muestra en Compras)
        cuenta_banco = str(datos.get("banco") or "").strip()
        num_operacion_cruz = str(datos.get("numero_operacion") or "").strip()
        id_movimiento_banco = int(datos.get("id_movimiento") or 0)

        # Copiar los PDF (factura de compra y soporte del pago a tercero) a las carpetas autorizadas
        origen_soporte = datos.get("soporte_origen") or ""
        origen_factura = datos.get("factura_origen") or ""
        ruta_soporte = ""
        ruta_factura = ""

        if (origen_soporte and os.path.isfile(origen_soporte)) or            (origen_factura and os.path.isfile(origen_factura)):
            base = obtener_ruta_base()
            if not base:
                messagebox.showwarning("Almacenamiento",
                                       "No se encontró la carpeta de archivos del sistema "
                                       "(revise la ruta de Google Drive en Configuración General).",
                                       parent=parent)
                return False, ""
            marca = datetime.now().strftime('%Y%m%d%H%M%S')
            if origen_factura and os.path.isfile(origen_factura):
                try:
                    carpeta = os.path.normpath(os.path.join(base, "facturas_recibidas"))
                    if not os.path.exists(carpeta):
                        os.makedirs(carpeta)
                    prov_limpio = re.sub(r'[\\/*?:"<>|]', '-', proveedor).replace(" ", "_")[:40]
                    ext = os.path.splitext(origen_factura)[1] or ".pdf"
                    ruta_factura = os.path.normpath(os.path.join(
                        carpeta, f"Cruzada_{marca}_{prov_limpio}{ext}"))
                    shutil.copy2(origen_factura, ruta_factura)
                except Exception as e:
                    messagebox.showerror("Error", f"Fallo al guardar la factura de compra:\n{e}",
                                         parent=parent)
                    return False, ""
            if origen_soporte and os.path.isfile(origen_soporte):
                try:
                    carpeta = os.path.normpath(os.path.join(base, "soportes_pagos_terceros"))
                    if not os.path.exists(carpeta):
                        os.makedirs(carpeta)
                    ext = os.path.splitext(origen_soporte)[1] or ".pdf"
                    ruta_soporte = os.path.normpath(os.path.join(
                        carpeta, f"Soporte_{marca}_{nro or 'sin_doc'}{ext}"))
                    shutil.copy2(origen_soporte, ruta_soporte)
                except Exception as e:
                    messagebox.showerror("Error", f"Fallo al guardar el soporte del pago a tercero:\n{e}",
                                         parent=parent)
                    return False, ""

        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=parent)
            return False, ""

        actualizada = False
        try:
            with conn.cursor() as c:
                # Evitar duplicar: ¿la factura ya está registrada en Compras?
                existente = None
                if nro:
                    c.execute("""SELECT id, COALESCE(archivo_ruta, '') FROM facturas_recibidas
                                 WHERE REPLACE(numero_documento, ' ', '') = %s ORDER BY id LIMIT 1""", (nro,))
                    existente = c.fetchone()
                    if not existente and proveedor:
                        c.execute("""SELECT id, COALESCE(archivo_ruta, '') FROM facturas_recibidas
                                     WHERE REPLACE(numero_documento, ' ', '') = %s AND proveedor ILIKE %s
                                     ORDER BY id LIMIT 1""", (nro, proveedor))
                        existente = c.fetchone()

                id_gasto_cruz = 0
                if existente:
                    sets = ["es_compra_cruzada = TRUE", "categoria = %s", "pagado_por_tercero = %s"]
                    params = [categoria, tercero]
                    if ruta_soporte:
                        sets.append("soporte_pago_tercero = %s")
                        params.append(ruta_para_guardar(ruta_soporte))
                    # La factura ya adjunta no se reemplaza (igual que en el flujo anterior)
                    if ruta_factura and not existente[1]:
                        sets.append("archivo_ruta = %s")
                        params.append(ruta_para_guardar(ruta_factura))
                    params.append(existente[0])
                    c.execute("UPDATE facturas_recibidas SET " + ", ".join(sets) + " WHERE id = %s",
                              tuple(params))
                    id_gasto_cruz = int(existente[0])
                    actualizada = True
                else:
                    c.execute("""
                        INSERT INTO facturas_recibidas
                        (tipo_documento, numero_documento, fecha, proveedor, descripcion, evento_asociado,
                         subtotal, impuesto, total, archivo_ruta, dias_credito, det_porcentaje, det_monto,
                         categoria, ruc, pagado_por_tercero, soporte_pago_tercero, es_compra_cruzada)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """, ("FACTURA", nro, fecha, proveedor, descripcion, evento_cruz,
                            subtotal_bd, impuesto_bd, total, ruta_para_guardar(ruta_factura), 0, 0, 0,
                            categoria, "", tercero, ruta_para_guardar(ruta_soporte), True))
                    try:
                        id_gasto_cruz = int(c.fetchone()[0])
                    except Exception:
                        id_gasto_cruz = 0

                # 💳 El pago se registra además a nombre de la CUENTA BANCARIA con la
                # que se pagó: así en Compras la factura figura como PAGADA y su
                # "Forma de Pago" muestra el banco (el soporte del tercero queda adjunto).
                if id_gasto_cruz:
                    c.execute("SELECT COUNT(*) FROM pagos_comprobantes WHERE id_factura = %s",
                              (id_gasto_cruz,))
                    fila_pago = c.fetchone()
                    if not fila_pago or int(fila_pago[0] or 0) == 0:
                        c.execute("""
                            INSERT INTO pagos_comprobantes
                            (id_factura, monto_pagado, archivo_ruta, proveedor_nombre, fecha_pago,
                             categoria_suministro, codigo_cotizacion, cuenta_origen, numero_operacion)
                            VALUES (%s, %s, %s, %s, %s, %s, '', %s, %s)
                        """, (id_gasto_cruz, total, ruta_para_guardar(ruta_soporte), proveedor,
                              fecha, categoria, cuenta_banco, num_operacion_cruz))

                # 🔗 Se enlaza el movimiento del banco con el gasto de Compras, para
                # que ambos módulos queden sincronizados (como en el pago normal).
                if id_gasto_cruz and id_movimiento_banco:
                    try:
                        c.execute("UPDATE conciliacion_bancaria SET id_gasto_compras=%s WHERE id=%s",
                                  (id_gasto_cruz, id_movimiento_banco))
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                conn.commit()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo registrar la compra cruzada:\n{e}", parent=parent)
            return False, ""
        finally:
            liberar_conexion(conn)

        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Compra cruzada {nro or ''} de {proveedor} pagada a {tercero} "
                            f"(registrada desde Registrar Pago)")
        return True, ("actualizada" if actualizada else "creada")

    def registrar_egreso_en_compras(self, datos, id_movimiento=None, parent=None):
        """Registra un pago hecho desde el módulo de Banco como GASTO YA PAGADO en Compras.

        Crea el gasto en 'facturas_recibidas' y su pago en 'pagos_comprobantes' por
        el importe total, para que el egreso se vea en las dos pestañas del módulo
        de Compras y en los reportes, SIN quedar como deuda pendiente.
        Si ya existe (datos['id_gasto']) se actualiza en lugar de duplicarlo.

        Devuelve el id del gasto en Compras (0 si no se pudo registrar).
        """
        if not datos:
            return 0
        try:
            total = abs(float(datos.get("total") or 0))
        except Exception:
            total = 0.0
        if total <= 0:
            return 0

        fecha = str(datos.get("fecha") or "").strip()
        categoria = str(datos.get("categoria") or "").strip() or "GENERAL / NO ASIGNADO"
        descripcion = str(datos.get("descripcion") or "").strip()
        proveedor = str(datos.get("proveedor") or "").strip() or descripcion or categoria
        # 🎪 Evento asociado: se guarda en 'facturas_recibidas.evento_asociado', que es
        # la misma columna que usa el módulo de Compras para imputar el gasto al evento.
        evento = str(datos.get("evento") or "").strip()
        banco_txt = str(datos.get("banco") or "").strip()
        num_operacion = str(datos.get("numero_operacion") or "").strip()
        soporte = str(datos.get("soporte") or "").strip()
        soporte_bd = ruta_para_guardar(soporte) if soporte else ""
        id_gasto = int(datos.get("id_gasto") or 0)

        # 💰 Detalle base + IGV definido en la ventana de pago (opcional).
        #    Si no llega, se registra el total tal cual (comportamiento de siempre).
        subtotal_dato, impuesto_dato = detalle_base_igv(datos)
        subtotal_bd = subtotal_dato if subtotal_dato is not None else total
        impuesto_bd = impuesto_dato if impuesto_dato is not None else 0.0

        conn = conectar_db(silencioso=True)
        if not conn:
            return 0
        try:
            with conn.cursor() as c:
                if id_gasto:
                    # El gasto ya estaba registrado en Compras: se actualiza
                    sets = ["fecha=%s", "total=%s", "subtotal=%s", "categoria=%s", "descripcion=%s",
                            "proveedor=%s", "evento_asociado=%s"]
                    params = [fecha, total, subtotal_bd, categoria, descripcion, proveedor, evento]
                    if impuesto_dato is not None:
                        sets.append("impuesto=%s")
                        params.append(impuesto_bd)
                    params.append(id_gasto)
                    c.execute("UPDATE facturas_recibidas SET " + ", ".join(sets) + " WHERE id=%s",
                              tuple(params))
                    c.execute("""
                        UPDATE pagos_comprobantes
                        SET monto_pagado=%s, fecha_pago=%s, cuenta_origen=%s, categoria_suministro=%s,
                            proveedor_nombre=%s, numero_operacion=%s
                        WHERE id_factura=%s
                    """, (total, fecha, banco_txt, categoria, proveedor, num_operacion, id_gasto))
                else:
                    c.execute("""
                        INSERT INTO facturas_recibidas
                        (tipo_documento, numero_documento, fecha, proveedor, descripcion, evento_asociado,
                         subtotal, impuesto, total, archivo_ruta, dias_credito, det_porcentaje, det_monto,
                         categoria, ruc, es_compra_cruzada, pagado_por_tercero, soporte_pago_tercero)
                        VALUES ('PAGO BANCO', '', %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 0, %s, '', FALSE, '', %s)
                        RETURNING id
                    """, (fecha, proveedor, descripcion, evento, subtotal_bd, impuesto_bd, total, soporte_bd,
                          categoria, soporte_bd))
                    id_gasto = c.fetchone()[0]
                    # El pago por el importe total: así no queda saldo pendiente
                    c.execute("""
                        INSERT INTO pagos_comprobantes
                        (id_factura, monto_pagado, archivo_ruta, proveedor_nombre, fecha_pago,
                         categoria_suministro, codigo_cotizacion, cuenta_origen, numero_operacion)
                        VALUES (%s, %s, %s, %s, %s, %s, '', %s, %s)
                    """, (id_gasto, total, soporte_bd, proveedor, fecha, categoria, banco_txt,
                          num_operacion))
                    if id_movimiento:
                        c.execute("UPDATE conciliacion_bancaria SET id_gasto_compras=%s WHERE id=%s",
                                  (id_gasto, id_movimiento))
                conn.commit()
        except Exception as e:
            print("[Banco -> Compras]", e)
            try:
                conn.rollback()
            except Exception:
                pass
            return 0
        finally:
            liberar_conexion(conn)

        # El módulo de Compras trabaja con caché: se limpia para que aparezca de inmediato
        try:
            from buffer_memoria import cache_sistema
            cache_sistema.invalidar()
        except Exception:
            pass
        return id_gasto

    def gestionar_categorias(self, al_guardar=None):
        # Categorías de gastos guardadas en la configuración del sistema (las mismas
        # para todos los equipos). "al_guardar" refresca los combos al cerrar.
        return abrir_gestion_categorias_gastos(self.parent_frame, al_guardar=al_guardar)

    def corregir_movimiento(self):
        filas = self._filas_seleccionadas()
        if len(filas) != 1:
            messagebox.showinfo("Corregir", "Seleccione exactamente un movimiento para corregir.",
                                parent=self.parent_frame)
            return
        f = filas[0]

        # Los pagos registrados desde este módulo ("➕ Registrar Pago / Compra
        # Cruzada") se editan en ESA MISMA ventana, con todos los datos cargados.
        if f.get("origen") == "manual" and f.get("id"):
            self.agregar_movimiento_manual(registro=f)
            return

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Corregir Movimiento")
        v.geometry("460x400")
        v.transient(self.parent_frame)
        v.grab_set()

        ctk.CTkLabel(v, text=f"✏️ Corregir: {f['descripcion'][:40]}", font=("Arial", 13, "bold"),
                     text_color="#1f538d").pack(pady=(15, 5))
        frm = ctk.CTkFrame(v, fg_color="transparent")
        frm.pack(fill="x", padx=20)

        ctk.CTkLabel(frm, text="Fecha:", font=("Arial", 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(frm, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 8))
        ent_fecha = ctk.CTkEntry(f_fecha)
        ent_fecha.pack(side="left", fill="x", expand=True)
        ent_fecha.insert(0, f["fecha"])
        ctk.CTkButton(f_fecha, text="📅", width=42, font=("Arial", 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(frm, text="Descripción:", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_desc = ctk.CTkEntry(frm)
        ent_desc.pack(fill="x", pady=(0, 8)); ent_desc.insert(0, f["descripcion"])

        ctk.CTkLabel(frm, text="Monto (negativo = egreso):", font=("Arial", 11, "bold")).pack(anchor="w")
        ent_monto = ctk.CTkEntry(frm)
        ent_monto.pack(fill="x", pady=(0, 8)); ent_monto.insert(0, f"{f['monto']:.2f}")

        def guardar():
            try:
                monto = float(ent_monto.get().strip())
            except ValueError:
                messagebox.showerror("Error", "Monto inválido.", parent=v)
                return
            fecha = ent_fecha.get().strip()
            desc = ent_desc.get().strip()
            origen = f.get("origen", "")

            if origen == "sistema":
                tabla = f.get("tabla", "")
                idp = f.get("id", 0)
                conn = conectar_db(silencioso=True)
                if conn and idp:
                    try:
                        with conn.cursor() as c:
                            if tabla == "pagos_clientes":
                                c.execute("UPDATE pagos_clientes SET monto_pagado=%s, fecha_pago=%s WHERE id=%s",
                                          (abs(monto), fecha, idp))
                            elif tabla == "pagos_comprobantes":
                                c.execute("UPDATE pagos_comprobantes SET monto_pagado=%s, fecha_pago=%s WHERE id=%s",
                                          (abs(monto), fecha, idp))
                            conn.commit()
                    except Exception as e:
                        messagebox.showerror("Error", f"No se pudo corregir:\n{e}", parent=v)
                        liberar_conexion(conn)
                        return
                    finally:
                        liberar_conexion(conn)
            elif origen == "manual" and f.get("id"):
                conn = conectar_db(silencioso=True)
                if conn:
                    try:
                        with conn.cursor() as c:
                            c.execute("UPDATE conciliacion_bancaria SET monto=%s, fecha=%s, descripcion=%s WHERE id=%s",
                                      (monto, fecha, desc, f["id"]))
                            conn.commit()
                    except Exception as e:
                        messagebox.showerror("Error", f"No se pudo corregir:\n{e}", parent=v)
                        liberar_conexion(conn)
                        return
                    finally:
                        liberar_conexion(conn)

            f["monto"] = monto
            f["fecha"] = fecha
            f["descripcion"] = desc
            registrar_auditoria(self.usuario_activo, "Banco", f"Corrigió movimiento: {desc[:60]}")
            v.destroy()
            self.refrescar_tree()

        ctk.CTkButton(v, text="✅ Guardar", width=140, fg_color="#27ae60", command=guardar).pack(pady=10)

    def eliminar_movimiento_conciliacion(self):
        sel = self.tabla_conc.selection()
        if not sel:
            messagebox.showinfo("Eliminar", "Seleccione uno o más movimientos para eliminar.",
                                parent=self.parent_frame)
            return
        indices = sorted({int(i) for i in sel if str(i).isdigit()})
        filas = [self.filas_conciliacion[i] for i in indices
                 if 0 <= i < len(self.filas_conciliacion)]
        if not filas:
            return

        if not messagebox.askyesno(
                "Confirmar eliminación",
                f"¿Eliminar {len(filas)} movimiento(s) de la conciliación?\n\n"
                "• Los movimientos manuales se borran de la base de datos.\n"
                "• Los movimientos del sistema (cobros/pagos/transferencias) y las líneas del "
                "estado de cuenta NO se borran de sus tablas: quedan descartados y ya no vuelven "
                "a aparecer al pulsar «Cargar Movimientos del Sistema».\n"
                "• Para volver a verlos use el botón «♻️ Restaurar Eliminados».\n"
                "• 🔗 Si un movimiento manual creó su egreso en COMPRAS, ese gasto (factura + "
                "pago) también se eliminará: los dos módulos quedan sincronizados.",
                parent=self.parent_frame):
            return

        banco = self.banco_seleccionado()
        nombre_banco = banco.get("banco", "") if banco else ""
        borrados_db = 0
        gastos_borrados = 0
        ignorados_nuevos = 0
        soportes_a_revisar = []
        errores = []

        conn = conectar_db(silencioso=True)
        try:
            if conn:
                with conn.cursor() as c:
                    # 🔗 Egresos del banco que tienen su gasto espejo en Compras
                    gastos_vinculados = {}
                    ids_manuales = [int(f.get("id", 0) or 0) for f in filas
                                    if f.get("origen", "") == "manual" and int(f.get("id", 0) or 0)]
                    if ids_manuales:
                        try:
                            c.execute("""SELECT id, COALESCE(id_gasto_compras, 0)
                                         FROM conciliacion_bancaria WHERE id = ANY(%s)""", (ids_manuales,))
                            for id_mov, id_gasto_bd in c.fetchall():
                                if int(id_gasto_bd or 0):
                                    gastos_vinculados[int(id_mov)] = int(id_gasto_bd)
                        except Exception:
                            conn.rollback()
                            gastos_vinculados = {}

                    for f in filas:
                        origen = f.get("origen", "")
                        idp = f.get("id", 0) or 0
                        try:
                            if origen == "manual" and idp:
                                c.execute("DELETE FROM conciliacion_bancaria WHERE id = %s", (idp,))
                                borrados_db += max(c.rowcount, 0)
                                # 🔗 Se elimina también el gasto que este movimiento creó en Compras
                                id_gasto = gastos_vinculados.get(int(idp), 0)
                                if id_gasto:
                                    ruta_soporte, borrado = eliminar_gasto_vinculado(c, id_gasto)
                                    if ruta_soporte:
                                        soportes_a_revisar.append(ruta_soporte)
                                    if borrado:
                                        gastos_borrados += 1
                            elif origen == "sistema" and idp:
                                c.execute("""DELETE FROM conciliacion_bancaria
                                             WHERE origen = 'sistema' AND id_movimiento = %s AND banco = %s""",
                                          (idp, nombre_banco))
                                borrados_db += max(c.rowcount, 0)
                                # 🚫 Queda descartado: no vuelve a aparecer al recargar la lista
                                self._registrar_ignorado(c, banco, f)
                                ignorados_nuevos += 1
                            elif origen == "estado_cuenta":
                                c.execute("""DELETE FROM conciliacion_bancaria
                                             WHERE origen = 'estado_cuenta' AND banco = %s
                                               AND fecha = %s AND monto = %s AND descripcion = %s""",
                                          (nombre_banco, f.get("fecha", ""),
                                           f.get("monto", 0), f.get("descripcion", "")))
                                borrados_db += max(c.rowcount, 0)
                                # 🚫 Queda descartado: no vuelve a aparecer (ni releyendo el PDF)
                                self._registrar_ignorado(c, banco, f)
                                ignorados_nuevos += 1
                        except Exception as e:
                            errores.append(str(e))
                    conn.commit()
        except Exception as e:
            errores.append(str(e))
        finally:
            if conn:
                liberar_conexion(conn)

        # 🗑️ Soportes digitales de los gastos borrados: se eliminan sólo si ya no los usa nadie
        for ruta_soporte in soportes_a_revisar:
            borrar_soporte_si_huerfano(ruta_soporte)

        # El módulo de Compras trabaja con caché: se limpia para que el cambio se vea al instante
        if gastos_borrados:
            try:
                from buffer_memoria import cache_sistema
                cache_sistema.invalidar()
            except Exception:
                pass

        excluir = set(indices)
        self.filas_conciliacion = [f for i, f in enumerate(self.filas_conciliacion)
                                   if i not in excluir]
        self.refrescar_tree()

        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Eliminó {len(filas)} movimiento(s) de la conciliación "
                            f"en {construir_etiqueta_banco(banco) if banco else 'banco'}"
                            + (f" (y {gastos_borrados} gasto(s) de Compras)" if gastos_borrados else ""))

        msg = f"{len(filas)} movimiento(s) eliminado(s) de la conciliación."
        if borrados_db:
            msg += f"\n{borrados_db} registro(s) eliminado(s) de la base de datos."
        if gastos_borrados:
            msg += (f"\n🔗 También se eliminó/eliminaron {gastos_borrados} gasto(s) en el módulo de "
                    f"COMPRAS (factura + pago). Los dos módulos quedan sincronizados.")
        if ignorados_nuevos:
            msg += (f"\n🚫 {ignorados_nuevos} movimiento(s) del sistema / estado de cuenta quedaron "
                    f"descartados: no volverán a aparecer al pulsar «Cargar Movimientos del Sistema».")
        if errores:
            msg += f"\n⚠️ Algunos registros no se pudieron borrar: {errores[0]}"
        messagebox.showinfo("Eliminar", msg, parent=self.parent_frame)

    def exportar_reporte(self):
        if not self.filas_conciliacion:
            messagebox.showinfo("Exportar", "No hay movimientos para exportar.", parent=self.parent_frame)
            return
        ruta = guardar_archivo_dialogo(
            titulo="Guardar Reporte de Conciliación",
            defaultextension=".txt",
            tipos=[("Archivo de texto", "*.txt"), ("CSV", "*.csv")])
        if not ruta:
            return
        try:
            es_csv = ruta.lower().endswith(".csv")
            sep = ";" if es_csv else "\t"
            with open(ruta, "w", encoding="utf-8-sig") as out:
                cab = ["Estado", "Fecha", "Descripción", "Tipo", "Monto", "Origen"]
                out.write(sep.join(cab) + "\n")
                for f in self.filas_conciliacion:
                    tipo = "Ingreso" if f["monto"] >= 0 else "Egreso"
                    estado = f.get("estado", "pendiente")
                    out.write(sep.join([estado, f["fecha"], f["descripcion"],
                                        tipo, f"{f['monto']:.2f}", f.get("origen", "")]) + "\n")
            messagebox.showinfo("Exportar", f"Reporte guardado en:\n{ruta}", parent=self.parent_frame)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo exportar:\n{e}", parent=self.parent_frame)

    def mostrar_texto_pdf(self):
        if not self.texto_pdf:
            messagebox.showinfo("Texto PDF", "Aún no se ha cargado ningún PDF.", parent=self.parent_frame)
            return
        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Texto Extraído del Estado de Cuenta")
        v.geometry("820x560")
        v.transient(self.parent_frame)
        txt = tk.Text(v, wrap="word", font=("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self.texto_pdf)
        txt.configure(state="disabled")
        ctk.CTkButton(v, text="Cerrar", width=120, fg_color="#34495e",
                      command=v.destroy).pack(pady=(0, 10))
