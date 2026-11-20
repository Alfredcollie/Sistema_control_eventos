# -*- coding: utf-8 -*-
"""
MODULO_BANCO.PY - MODULO DE BANCO (SALDOS Y CONCILIACION BANCARIA)
- Saldo de cada cuenta bancaria configurada en "Configuracion General":
    Saldo = Saldo Inicial + movimientos del banco. En los meses con estado de
    cuenta cargado se usan sus lineas (conciliadas o no); en los meses sin
    estado se usan los cobros/pagos/transferencias y los movimientos manuales.
- Conciliacion bancaria: subir el PDF del estado de cuenta del banco,
  extraer sus movimientos, compararlos contra los movimientos registrados
  en el sistema (cobros y pagos) y generar un reporte en pantalla.
- Permite conciliar, corregir montos/fechas y agregar movimientos faltantes
  (tanto ajustes manuales como cobros/pagos reales en el sistema).
- Registro unificado de pagos ("Registrar Pago / Compra Cruzada"): al marcar el
  check "Es compra cruzada" el pago a un tercero se registra tambien como factura
  pagada por tercero en el modulo de Compras, adjuntando el PDF del soporte.
"""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import customtkinter as ctk

FAMILIA = "Helvetica" if __import__("sys").platform == "darwin" else "Arial"
import os
import re
import json
import shutil
import calendar
import sys
import subprocess
import tempfile
import threading
import transferencias_soporte as tx_soporte
from datetime import datetime

from colores_montos import preparar as preparar_colores_montos, filtrar_todas as filtrar_montos_negativos
from conexion import conectar_db, registrar_auditoria, liberar_conexion
from dialogos_seguros import (seleccionar_archivo_dialogo, seleccionar_archivos_dialogo,
                              guardar_archivo_dialogo)
from app_paths import (CONFIG_FILE, DATA_DIR, ruta_para_guardar, eliminar_archivo,
                       ruta_utilizable)
# 🏦 En el programa de EVENTOS las cuentas bancarias viven en bancos.py
#    (tabla configuracion_sistema). Si no está, se usa la configuración en la nube.
try:
    from bancos import cargar_bancos
except Exception:
    from config_nube import cargar_bancos
from tareas_seguras import ejecutar_en_hilo

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
def _numero_peru(txt):
    """Convierte '1,340.78' / '1.340,78' / '1340.78' a float (0.0 si no se puede)."""
    import re as _re
    t = _re.sub(r"[^\d.,]", "", str(txt or ""))
    if not t:
        return 0.0
    try:
        if "," in t and "." in t:
            if t.rfind(",") > t.rfind("."):
                t = t.replace(".", "").replace(",", ".")
            else:
                t = t.replace(",", "")
        elif "," in t:
            partes = t.split(",")
            t = t.replace(",", ".") if len(partes[-1]) <= 2 else t.replace(",", "")
        elif t.count(".") > 1:
            partes = t.split(".")
            t = ("".join(partes[:-1]) + "." + partes[-1]) if len(partes[-1]) <= 2 else "".join(partes)
        return float(t)
    except Exception:
        return 0.0


def monto_del_comprobante(texto):
    """💵 Importe leído de un comprobante (0.0 si no se encuentra).

    Prioriza el monto de la transferencia/depósito (una línea «s/ X» sin las
    palabras total/comisión), para no confundirlo con el «Total cobrado», que
    incluye la comisión del banco (ej. transferencia 803.23 + comisión 4.80 =
    «Total cobrado 808.03»).
    """
    import re as _re
    try:
        lineas = [l.strip() for l in str(texto or "").splitlines() if l.strip()]
        for l in lineas:
            bajo = l.lower()
            if any(p in bajo for p in ("total", "importe", "monto", "comis", "cobrado")):
                continue
            m = _re.search(r"s[./]\s*[\d][\d.,]*", bajo)
            if m:
                nums = _re.findall(r"\d[\d.,]*", m.group(0))
                if nums:
                    v = _numero_peru(nums[-1])
                    if v > 0:
                        return float(v)
        comision = 0.0
        for l in lineas:
            if "comis" in l.lower():
                nums = _re.findall(r"\d[\d.,]*", l)
                for n in reversed(nums):
                    c = _numero_peru(n)
                    if 0 < c < 10000000:
                        comision = float(c)
                        break
                if comision:
                    break
        for l in lineas:
            bajo = l.upper()
            if ("TOTAL" in bajo or "IMPORTE" in bajo or "MONTO" in bajo) and "COBRADO" in bajo:
                nums = _re.findall(r"\d[\d.,]*", l)
                if nums:
                    v = _numero_peru(nums[-1])
                    if v > 0:
                        return float(v) - comision
        for l in lineas:
            bajo = l.upper()
            if "TOTAL" in bajo or "IMPORTE" in bajo or "MONTO" in bajo:
                nums = _re.findall(r"\d[\d.,]*", l)
                if nums:
                    val = _numero_peru(nums[-1])
                    if val > 0:
                        return float(val)
        candidatos = []
        for l in lineas:
            for n in _re.findall(r"\d[\d.,]{2,}", l):
                v = _numero_peru(n)
                if 0 < v < 10000000:
                    candidatos.append(float(v))
        return max(candidatos) if candidatos else 0.0
    except Exception:
        return 0.0



def cargar_config():
    config = {
        "simbolo_moneda": "S/.",
        "formato_numero": "1,000.00",
        "formato_fecha": "DD/MM/AAAA",
        "cuentas_bancarias": [],
    }
    try:
        if os.path.exists(str(CONFIG_FILE)):
            with open(str(CONFIG_FILE), "r", encoding="utf-8") as f:
                config.update(json.load(f))
    except Exception:
        pass
    return config


CONFIG = cargar_config()


def obtener_ruta_base():
    """Carpeta de archivos del programa (misma lógica que Compras/Ventas).

    Devuelve "" si el equipo NO está autorizado (equipo secundario sin la cuenta
    Rclone del principal): así ningún flujo del Banco guarda archivos.
    """
    try:
        from politica_almacenamiento import estado_almacenamiento, ruta_base_autorizada
        if not estado_almacenamiento().get("autorizado"):
            return ""
        ruta = ruta_base_autorizada(mostrar_alerta=False)
        if ruta and os.path.isdir(ruta):
            return ruta
    except Exception:
        pass
    ruta = ruta_utilizable(CONFIG.get("ruta_drive", ""))
    if ruta and os.path.isdir(ruta):
        return ruta
    # Respaldo junto al programa: SOLO en modo monousuario (local)
    try:
        from politica_almacenamiento import permitir_respaldo_local
        if not permitir_respaldo_local():
            return ""
    except Exception:
        pass
    if sys.platform == "darwin":
        # 🍎 Dentro del .app esta carpeta es de SOLO LECTURA (y desaparece al
        #    actualizar el programa): el respaldo local va a la carpeta de datos
        #    del usuario, que siempre es escribible.
        return str(DATA_DIR)
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


def abrir_documento(ruta, titulo="Documento"):
    """🖼️ Muestra el documento en el VISOR INTERNO del programa.

    Antes se abría con el programa externo (Acrobat, Fotos…) y la ventana
    quedaba muchas veces detrás del sistema. Ahora se ve dentro del programa.
    """
    try:
        from visor_documentos import ver_documento_ventana
        return ver_documento_ventana(ruta, titulo)
    except Exception as e_visor:
        print("Aviso - visor interno:", e_visor)
    return abrir_documento_externo(ruta)


def abrir_documento_externo(ruta):
    try:
        # Resuelve rutas guardadas en otro equipo/SO (Mac <-> Windows)
        try:
            from app_paths import resolver_ruta_archivo
            ruta = resolver_ruta_archivo(ruta) or ruta
        except Exception:
            pass
        ruta_abs = os.path.abspath(ruta)
        if sys.platform == "win32":
            os.startfile(ruta_abs)
        elif sys.platform == "darwin":
            subprocess.call(["open", ruta_abs])
        else:
            subprocess.call(["xdg-open", ruta_abs])
    except Exception as e:
        messagebox.showerror("Error", f"No se pudo abrir el archivo:\n{e}")


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
CLAVE_ESQUEMA_BANCO = "banco_esquema_ok_v7"
CLAVE_PERMISOS_BANCO = "banco_permisos_ok_v1"


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


def ordenar_categorias_principales(principales):
    """Ordena las categorías principales: primero las de GASTOS.

    Al registrar un pago lo habitual es elegir una categoría de gasto; así el
    desplegable abre directamente en una de ellas (antes abría en "Otros", que
    solo tiene "Varios" y parecía que no había categorías).
    """
    def clave(nombre):
        n = str(nombre).strip().lower()
        if "gasto" in n:
            return (0, n)
        if "ingreso" in n:
            return (2, n)
        return (1, n)
    return sorted([str(p) for p in (principales or []) if str(p).strip()], key=clave)


def cargar_categorias_gastos():
    cfg = leer_config_disco()
    if "categorias_gastos" in cfg and isinstance(cfg["categorias_gastos"], dict):
        cats = cfg["categorias_gastos"]
        return {k: (v if isinstance(v, list) else []) for k, v in cats.items()}
    return dict(CATEGORIAS_GASTOS_DEFAULT)


def guardar_categorias_gastos(cats):
    """Guarda las categorías principales y sus subcategorías en la configuración.

    Es la MISMA lista que comparten Banco, Compras y las estadísticas financieras,
    por eso se guarda en config_local.json igual que el resto de parámetros.
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

    Es la MISMA ventana que usa el módulo de Banco (Conciliación → Agregar
    Movimiento → "⚙️ Gestionar Categorías") y ahora también el módulo de Compras,
    de modo que ambos módulos comparten exactamente la misma lista guardada en la
    configuración. Si se indica "al_guardar", se ejecuta tras guardar y cerrar
    (para refrescar los combos del módulo que la abrió).
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

    ctk.CTkLabel(v, text="⚙️ Categorías (principales y subcategorías)", font=(FAMILIA, 14, "bold"),
                 text_color="#1f538d").pack(pady=(15, 5))

    f_tabla = ctk.CTkFrame(v, fg_color="transparent")
    f_tabla.pack(fill="both", expand=True, padx=15, pady=10)
    columnas = ("principal", "categoria")
    tree = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="browse")
    preparar_colores_montos(tree)
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


def cargar_eventos(usar_cache=True):
    """Eventos a los que se puede asociar un movimiento bancario.

    Es la MISMA lista de eventos aprobados que usan Compras, Ventas y el
    dashboard ('CODIGO | Nombre del Evento'), con la opción OFICINA para los
    gastos internos que no pertenecen a ningún evento.

    (Este sistema trabaja por eventos, no por vehículos.)
    """
    try:
        from buffer_memoria import leer_cache, guardar_cache
    except Exception:
        leer_cache = guardar_cache = None
    eventos = []
    if usar_cache and leer_cache:
        try:
            eventos = leer_cache("lista_eventos_aprobados") or []
        except Exception:
            eventos = []
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
            except Exception as error:
                print("[Banco] eventos:", error)
                eventos = []
            finally:
                liberar_conexion(conn)
        if "OFICINA | Trabajos Internos" not in eventos:
            eventos = ["OFICINA | Trabajos Internos"] + eventos
        try:
            if guardar_cache:
                guardar_cache("lista_eventos_aprobados", eventos)
        except Exception:
            pass
    elif "OFICINA | Trabajos Internos" not in eventos:
        eventos.insert(0, "OFICINA | Trabajos Internos")
    return eventos

def es_categoria_planilla(texto):
    """True si el texto de categoría corresponde a planilla / sueldos."""
    t = (texto or "").lower()
    return "planilla" in t or "sueldo" in t


def cargar_choferes():
    """Choferes registrados en el módulo de Choferes (activos primero)."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("""
                SELECT nombres FROM choferes
                WHERE nombres IS NOT NULL AND TRIM(nombres) != ''
                ORDER BY (COALESCE(estado, '') = 'Activo') DESC, nombres ASC
            """)
            return [str(r[0]) for r in c.fetchall()]
    except Exception:
        return []
    finally:
        liberar_conexion(conn)


def cargar_proveedores():
    """Proveedores registrados en el módulo de Proveedores."""
    conn = conectar_db(silencioso=True)
    if not conn:
        return []
    try:
        with conn.cursor() as c:
            c.execute("SELECT nombre FROM proveedores WHERE nombre IS NOT NULL AND TRIM(nombre) != '' ORDER BY nombre ASC")
            return [str(r[0]) for r in c.fetchall()]
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


_TABLA_DIFERENCIALES_LISTA = [False]


_SOPORTE_LISTO = [False]


def _asegurar_columna_soporte():
    """Agrega la columna del comprobante (soporte) si la tabla no la tiene."""
    if _SOPORTE_LISTO[0]:
        return True
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("ALTER TABLE conciliacion_bancaria "
                      "ADD COLUMN IF NOT EXISTS soporte TEXT DEFAULT ''")
        conn.commit()
        _SOPORTE_LISTO[0] = True
        return True
    except Exception as e:
        print("[Banco] no se pudo agregar la columna soporte:", e)
        return False
    finally:
        liberar_conexion(conn)


def _asegurar_tabla_diferenciales():
    """Crea la tabla de diferenciales si falta (una vez por ejecución)."""
    if _TABLA_DIFERENCIALES_LISTA[0]:
        return True
    conn = conectar_db(silencioso=True)
    if not conn:
        return False
    try:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS conciliacion_diferenciales (
                    id SERIAL PRIMARY KEY,
                    banco VARCHAR(255),
                    cuenta VARCHAR(120) DEFAULT '',
                    fecha VARCHAR(20) DEFAULT '',
                    descripcion TEXT,
                    monto NUMERIC DEFAULT 0,
                    origen VARCHAR(20) DEFAULT '',
                    descripcion_guardada TEXT DEFAULT '',
                    incluido BOOLEAN DEFAULT FALSE,
                    creado VARCHAR(30) DEFAULT ''
                )
            """)
        conn.commit()
        _TABLA_DIFERENCIALES_LISTA[0] = True
        return True
    except Exception as e:
        print("[Diferenciales] no se pudo crear la tabla:", e)
        return False
    finally:
        liberar_conexion(conn)


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



def periodo_de_movimientos(movs):
    """Período (AAAA-MM) de un estado de cuenta: el mes con más líneas.

    Un estado puede traer algún movimiento del mes anterior; se toma el mes
    dominante y, si no hay fechas, el mes actual.
    """
    conteo = {}
    for m in (movs or []):
        fecha = normalizar_fecha(m.get("fecha"))
        if fecha and len(fecha) >= 7:
            conteo[fecha[:7]] = conteo.get(fecha[:7], 0) + 1
    if conteo:
        return max(conteo.items(), key=lambda par: par[1])[0]
    return datetime.now().strftime("%Y-%m")


def _dias_entre_fechas(fecha_a, fecha_b):
    """Diferencia en días entre dos fechas de texto (dd/mm/aaaa o aaaa-mm-dd)."""
    def partes(valor):
        texto = str(valor or "").strip()
        try:
            if "-" in texto and len(texto.split("-")[0]) == 4:      # AAAA-MM-DD
                a, m, d = [int(x) for x in texto.split("-")[:3]]
            else:                                                    # DD/MM/AAAA
                d, m, a = [int(x) for x in texto.replace("-", "/").split("/")[:3]]
                if a < 100:
                    a += 2000
            return datetime(a, m, d).date()
        except Exception:
            return None
    fa, fb = partes(fecha_a), partes(fecha_b)
    if not fa or not fb:
        return 999
    return abs((fa - fb).days)


def _monto_coincide_banco(monto_banco, monto_asiento, comision=0.0):
    """¿El importe del asiento corresponde a la línea del banco?

    Acepta el mismo importe o el importe del sistema + la comisión que el banco
    muestra incluida en una sola línea (ej.: 1,200.00 + 4.80 = 1,204.80).
    """
    try:
        bm = float(monto_banco)
        mm = float(monto_asiento)
    except (TypeError, ValueError):
        return False
    if abs(bm - mm) <= 0.02:
        return True
    if bm * mm > 0 and abs(abs(bm) - abs(mm) - float(comision or 0)) <= 0.02:
        return True
    return False


def _es_duplicado_manual_claro(asiento, linea, principal=None, comision=0.0):
    """¿El asiento manual es una COPIA clara de una línea del banco?

    Se exige que la descripción coincida con la línea (el manual se creó desde
    el estado de cuenta) o que sea el caso de una transferencia donde el sistema
    registra el principal y el manual el total con comisión. Así no se borran
    manuales que sólo coinciden en importe pero son movimientos distintos.
    """
    if str((asiento or {}).get("origen", "")) != "manual":
        return False
    if (_norm_texto(asiento.get("descripcion"))
            == _norm_texto((linea or {}).get("descripcion"))):
        return True
    if principal is None or not str(principal.get("origen", "")).startswith("s:"):
        return False
    try:
        return (abs(abs(float(asiento.get("monto") or 0))
                    - abs(float((linea or {}).get("monto") or 0))) <= 0.02
                and abs(abs(float((linea or {}).get("monto") or 0))
                        - abs(float(principal.get("monto") or 0))
                        - float(comision or 0)) <= 0.02)
    except (TypeError, ValueError):
        return False


def _mejor_linea_banco(asiento, lineas, usados, comision=0.0, solo_usadas=False):
    """Índice de la mejor línea del banco para un asiento (o None)."""
    mejor = None
    for j, linea in enumerate(lineas):
        if solo_usadas:
            if j not in usados:
                continue
        elif j in usados:
            continue
        if not _monto_coincide_banco(linea.get("monto"), asiento.get("monto"), comision):
            continue
        dias = _dias_entre_fechas(asiento.get("fecha"), linea.get("fecha"))
        if dias > 31:
            continue
        exacto_fecha = (dias == 0)
        try:
            exacto_monto = abs(float(linea.get("monto") or 0)
                              - float(asiento.get("monto") or 0)) <= 0.02
        except (TypeError, ValueError):
            exacto_monto = False
        categoria = 0 if (exacto_fecha and exacto_monto) else (
            1 if exacto_fecha else (2 if exacto_monto else 3))
        candidato = (categoria, dias, j)
        if mejor is None or candidato < mejor:
            mejor = candidato
    return mejor[2] if mejor else None


def emparejar_asientos_banco(asientos, lineas, comision=0.0):
    """Empareja asientos (sistema/manuales) con líneas del estado de cuenta.

    Cada línea del banco se usa UNA sola vez. Devuelve:
      * match:      {indice_asiento: indice_linea}  (asiento único)
      * duplicados: [(indice_asiento, indice_linea)] (la línea ya estaba usada)
      * sobrantes:  [indice_asiento]  (asiento sin línea en el banco)
      * faltantes:  [indice_linea]    (línea del banco sin asiento)

    El orden de prioridad se toma de 'asiento["_prioridad"]' (menor = primero):
    así el asiento del sistema gana sobre el manual y no se cuenta dos veces.
    """
    asientos = list(asientos or [])
    lineas = list(lineas or [])
    orden = sorted(range(len(asientos)),
                   key=lambda i: (asientos[i].get("_prioridad", 9),
                                  normalizar_fecha(asientos[i].get("fecha")) or ""))
    usados = set()
    match = {}
    duplicados = []
    for i in orden:
        j = _mejor_linea_banco(asientos[i], lineas, usados, comision)
        if j is not None:
            usados.add(j)
            match[i] = j
            continue
        j = _mejor_linea_banco(asientos[i], lineas, usados, comision, solo_usadas=True)
        if j is not None:
            duplicados.append((i, j))
    dup_idx = {i for i, _ in duplicados}
    sobrantes = [i for i in range(len(asientos)) if i not in match and i not in dup_idx]
    faltantes = [j for j in range(len(lineas)) if j not in usados]
    return match, duplicados, sobrantes, faltantes


def fecha_para_mostrar(valor):
    """Devuelve la fecha como DD/MM/AAAA para mostrarla (aunque venga en ISO)."""
    f = normalizar_fecha(valor)
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", f or ""):
            return datetime.strptime(f, "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        pass
    return valor or ""


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


def cuenta_transferencia_matchea(banco, nombre, cuenta):
    """Transferencias: identifica banco Y cuenta, nunca por nombre parcial."""
    return bool(_norm_texto(cuenta) and _norm_texto(banco.get("cuenta", ""))) and (
        _norm_texto(banco.get("banco", "")) == _norm_texto(nombre)
        and _norm_texto(banco.get("cuenta", "")) == _norm_texto(cuenta))


def traer_al_frente(ventana, retardo_ms=500):
    """Pone una ventana emergente DELANTE y le da el foco.

    En Windows y macOS una CTkToplevel puede nacer DETRÁS de la ventana
    principal (sobre todo si la principal está maximizada). Se usa «-topmost»
    sólo un instante: si quedara fijo, la ventana taparía siempre al resto.
    """
    try:
        ventana.lift()
    except Exception:
        pass
    try:
        ventana.attributes("-topmost", True)

        def _soltar():
            try:
                if ventana.winfo_exists():
                    ventana.attributes("-topmost", False)
            except Exception:
                pass

        ventana.after(retardo_ms, _soltar)
    except Exception:
        pass
    try:
        ventana.focus_force()
    except Exception:
        pass


def aplicar_estilo_treeview():
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Treeview", background="#ffffff", foreground="#000000",
                    fieldbackground="#ffffff", bordercolor="#e0e0e0",
                    borderwidth=1, rowheight=26, font=(FAMILIA, 10))
    style.map("Treeview", background=[("selected", "#1f538d")],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background="#f0f0f0", foreground="#000000",
                    relief="flat", font=(FAMILIA, 10, "bold"),
                    bordercolor="#e0e0e0", borderwidth=1)


# =========================================================
# EXTRACCION Y ANALISIS DE PDF (ESTADO DE CUENTA)
# =========================================================
def _pdf_limpio(ruta):
    """Devuelve (ruta_para_abrir, temporal_a_borrar).

    🏦 Algunos estados de cuenta vienen con bytes de más DELANTE del PDF: por
    ejemplo los del banco empiezan con «$BOP$» y el PDF real arranca en el byte 5.
    Sin quitarlos, ninguna librería lo abre («invalid pdf header») y el programa
    ni siquiera detecta que el archivo pide contraseña.
    """
    try:
        with open(ruta, "rb") as f:
            cabecera = f.read(2048)
    except Exception:
        return ruta, None
    posicion = cabecera.find(b"%PDF")
    if posicion <= 0:
        return ruta, None
    try:
        with open(ruta, "rb") as f:
            contenido = f.read()[posicion:]
    except Exception:
        return ruta, None
    try:
        temporal = os.path.join(tempfile.gettempdir(), "estado_cuenta_limpio.pdf")
        with open(temporal, "wb") as f:
            f.write(contenido)
        return temporal, temporal
    except Exception:
        return ruta, None


def pdf_esta_cifrado(ruta):
    """True si el PDF exige contraseña para abrirse."""
    ruta_ok, temporal = _pdf_limpio(ruta)
    try:
        if _PypdfReader is not None:
            try:
                return bool(getattr(_PypdfReader(ruta_ok), "is_encrypted", False))
            except Exception:
                pass
        if fitz is not None:
            try:
                doc = fitz.open(ruta_ok)
                try:
                    return bool(doc.needs_pass)
                finally:
                    doc.close()
            except Exception:
                pass
        return False
    finally:
        if temporal:
            try:
                os.remove(temporal)
            except Exception:
                pass


def extraer_texto_pdf(ruta, password=None):
    texto = ""
    ruta_ok, temporal = _pdf_limpio(ruta)
    try:
        if pdfplumber is not None:
            try:
                with pdfplumber.open(ruta_ok, password=password or "") as pdf:
                    partes = [p.extract_text() or "" for p in pdf.pages]
                texto = "\n".join(partes).strip()
            except Exception:
                texto = ""
        if not texto and fitz is not None:
            try:
                doc = fitz.open(ruta_ok)
                if getattr(doc, "needs_pass", False):
                    if not doc.authenticate(password or ""):
                        doc.close()
                        doc = None
                if doc is not None:
                    partes = [page.get_text() or "" for page in doc]
                    doc.close()
                    texto = "\n".join(partes).strip()
            except Exception:
                texto = ""
        if not texto and _PypdfReader is not None:
            try:
                reader = _PypdfReader(ruta_ok)
                if getattr(reader, "is_encrypted", False):
                    if reader.decrypt(password or ""):
                        partes = [(p.extract_text() or "") for p in reader.pages]
                        texto = "\n".join(partes).strip()
                else:
                    partes = [(p.extract_text() or "") for p in reader.pages]
                    texto = "\n".join(partes).strip()
            except Exception:
                texto = ""
        return texto
    finally:
        if temporal:
            try:
                os.remove(temporal)
            except Exception:
                pass


FECHA_RE = re.compile(r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b")
MONTO_RE = re.compile(r"[-+]?\s?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|[-+]?\s?\d+(?:[.,]\d{1,2})?")
MONTO_DECIMAL_RE = re.compile(r"[-+]?\s?(?:\d{1,3}(?:,\d{3})+\.\d{1,2}|\d+(?:\.\d{1,2}))")

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


def _anio_del_estado(texto):
    """Año del estado de cuenta (ej. «Mes: Setiembre 2026» → 2026)."""
    m = re.search(r"\b(20\d{2})\b", texto or "")
    return int(m.group(1)) if m else None


def parsear_movimientos(texto):
    """Heuristica para extraer movimientos de un estado de cuenta.

    Soporta el formato Interbank Negocios: cada movimiento trae DOS fechas
    (emisión y proceso) del tipo «DD/MM DD/MM descripción monto saldo».
    """
    movimientos = []
    if not texto:
        return movimientos
    anio = _anio_del_estado(texto)
    # 🏦 BCP («BCB»): formato propio (fecha DD-MM, importe con el signo al final).
    if _es_estado_bcp(texto):
        movimientos = _parsear_movimientos_bcp(texto, anio)
        if movimientos:
            print("[Conciliación] estado de cuenta BCP: %d movimiento(s) detectado(s)"
                  % len(movimientos))
            return movimientos
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        low = linea.lower()
        # Encabezados / rangos de periodo no son transacciones
        if any(k in low for k in ("periodo", "period", "estado de cuenta", "saldo inicial",
                                  "pág.", "pag.", "nota ")):
            continue
        fechas = [(m.group(0), m.start(), m.end()) for m in FECHA_RE.finditer(linea)]
        if not fechas:
            continue
        # Quitar TODAS las fechas (Interbank trae dos) para no confundirlas con montos
        resto = linea
        for _, s, e in reversed(fechas):
            resto = resto[:s] + " " + resto[e:]
        # Solo montos con decimales (evita números de referencia como 1616965)
        montos = [(m.group(0), m.start()) for m in MONTO_DECIMAL_RE.finditer(resto)]
        if not montos:
            continue
        # Fecha del movimiento: la primera (si no trae año se usa el del estado)
        fecha_raw = fechas[0][0]
        partes = fecha_raw.split("/")
        if len(partes) == 2 and anio:
            fecha_raw = "%s/%s/%d" % (partes[0], partes[1], anio)
        elif len(partes) == 3 and anio and str(partes[2]) != str(anio):
            # 🛡️ Punto 4: el estado de cuenta trae un año equivocado -> se ajusta al del período
            try:
                print("[Conciliación] año corregido: %s -> %s/%s/%d"
                      % (fecha_raw, partes[0], partes[1], anio))
            except Exception:
                pass
            fecha_raw = "%s/%s/%d" % (partes[0], partes[1], anio)
        seleccion = _seleccionar_monto(resto, montos)
        if seleccion is None:
            continue
        valor, token = seleccion
        signo_palabras = _signo_por_palabras(linea)
        signo_explicito = token.strip().startswith("+") or token.strip().startswith("-")
        if not signo_explicito and signo_palabras is not None:
            valor = signo_palabras * abs(valor)
        descripcion = re.sub(r"\s+", " ", resto).strip()
        descripcion = MONTO_DECIMAL_RE.sub("", descripcion)
        descripcion = re.sub(r"\s+", " ", descripcion).strip(" -|")
        saldo_fila = None
        if len(montos) >= 2:
            try:
                saldo_fila = normalizar_monto(montos[-1][0])
            except Exception:
                saldo_fila = None
        movimientos.append({
            "fecha": normalizar_fecha(fecha_raw) or fecha_raw,
            "descripcion": descripcion or "Movimiento sin descripción",
            "monto": valor,
            "saldo": saldo_fila,
        })
    return movimientos


def _es_estado_bcp(texto):
    """True si el texto es un estado de cuenta del BCP (formato «BCB»)."""
    t = (texto or "").upper()
    return ("VIABCP" in t or "ESTADO DE CUENTA CORRIENTE" in t)


_BCP_RENGLON = re.compile(r"^(\d{1,2})-(\d{1,2})\s+(.+)$")
_BCP_NUMERO = re.compile(r"^([\d,]*\.\d{2})(-?)$")


def _parsear_movimientos_bcp(texto, anio):
    """Estado de cuenta BCP: «01-09  DESCRIPCIÓN … 176.54- 11,218.42».

    Tres cosas propias de este formato:
      * la fecha viene sin año («01-09»), el año sale del encabezado;
      * el signo va PEGADO al final del importe («176.54-» = cargo);
      * el último número de la línea es el SALDO corrido, no el movimiento.
    """
    movimientos = []
    for linea in (texto or "").splitlines():
        linea = " ".join(linea.split())
        encaje = _BCP_RENGLON.match(linea)
        if not encaje:
            continue
        dia, mes, resto = encaje.groups()
        tokens = resto.split()
        if len(tokens) < 2:
            continue
        saldo_tok = _BCP_NUMERO.match(tokens[-1])
        if not saldo_tok or saldo_tok.group(2):
            continue                      # el saldo nunca lleva «-»
        monto_tok = _BCP_NUMERO.match(tokens[-2])
        if not monto_tok:
            continue
        monto = normalizar_monto(monto_tok.group(1))
        monto = -abs(monto) if monto_tok.group(2) else abs(monto)
        fecha = "%s/%s/%s" % (dia.zfill(2), mes.zfill(2),
                              anio or datetime.now().year)
        movimientos.append({
            "fecha": normalizar_fecha(fecha) or fecha,
            "descripcion": " ".join(tokens[:-2]).strip() or "Movimiento sin descripción",
            "monto": monto,
            "saldo": normalizar_monto(saldo_tok.group(1)),
        })
    return movimientos


def detectar_saldo_final(texto):
    """Intenta detectar el saldo final del estado de cuenta.

    Primero busca un «Saldo Final» explícito; si no, toma el balance de la
    última línea de movimiento.
    """
    if not texto:
        return None
    # 🏦 BCP: el saldo final es el saldo corrido del último movimiento.
    if _es_estado_bcp(texto):
        movs = _parsear_movimientos_bcp(texto, _anio_del_estado(texto))
        if movs and movs[-1].get("saldo") is not None:
            return movs[-1]["saldo"]
    for ln in texto.splitlines():
        if re.search(r"saldo\s+final", ln.lower()):
            montos = [(m.group(0), m.start()) for m in MONTO_DECIMAL_RE.finditer(ln)]
            if montos:
                return normalizar_monto(montos[-1][0])
    ultimo = None
    for linea in texto.splitlines():
        linea = linea.strip()
        if not FECHA_RE.search(linea):
            continue
        sin_fecha = FECHA_RE.sub(" ", linea)
        montos = [(m.group(0), m.start()) for m in MONTO_DECIMAL_RE.finditer(sin_fecha)]
        if len(montos) >= 2:
            try:
                ultimo = normalizar_monto(montos[-1][0])
            except Exception:
                pass
    return ultimo


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
                      hover_color="#163b65", font=(FAMILIA, 14, "bold"),
                      command=self.mes_anterior).pack(side="left", padx=5, pady=10)

        self.cmb_mes = ctk.CTkComboBox(header, values=NOMBRES_MESES, width=105,
                                       command=self.cambiar_mes, state="readonly")
        self.cmb_mes.pack(side="left", padx=2, pady=10)

        anios = [str(y) for y in range(hoy.year - 80, hoy.year + 20)]
        self.cmb_anio = ctk.CTkComboBox(header, values=anios, width=80,
                                        command=self.cambiar_anio, state="readonly")
        self.cmb_anio.pack(side="left", padx=2, pady=10)

        ctk.CTkButton(header, text=">", width=28, fg_color="transparent", text_color="white",
                      hover_color="#163b65", font=(FAMILIA, 14, "bold"),
                      command=self.mes_siguiente).pack(side="right", padx=5, pady=10)

        self.days_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.days_frame.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        for i, dia in enumerate(["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]):
            ctk.CTkLabel(self.days_frame, text=dia, font=(FAMILIA, 11, "bold"),
                         text_color="#1f538d").grid(row=0, column=i, padx=5, pady=5)

        f_pie = ctk.CTkFrame(self, fg_color="transparent")
        f_pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_pie, text="📅 Hoy", width=90, font=(FAMILIA, 11, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.ir_a_hoy).pack(side="left")
        ctk.CTkButton(f_pie, text="✖ Cerrar", width=90, font=(FAMILIA, 11),
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
                                    hover_color="#e0e0e0", font=(FAMILIA, 11),
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


def _escanear_con_progreso(parent, etiqueta, mod_escaner, *args, **kwargs):
    """Escanea mostrando el cuadrito "Escaneando…" y lo cierra al terminar.

    El escaneo es sincrónico (así el resultado nunca se pierde); el cuadrito se
    pinta antes de empezar para que el usuario vea que está trabajando.
    """
    try:
        parent = parent or getattr(tk, "_default_root", None)
    except Exception:
        parent = None
    prog = None
    try:
        prog = ctk.CTkToplevel(parent)
        prog.title("Escaneando")
        prog.geometry("450x165")
        try:
            prog.transient(parent)
        except Exception:
            pass
        try:
            prog.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(prog, text="🖨️ Escaneando %s..." % (etiqueta or "el documento"),
                     font=(FAMILIA, 13, "bold"), text_color="#1f538d").pack(pady=(20, 4))
        ctk.CTkLabel(prog, text="Coloque el documento en el escáner (cristal).\nEspere unos segundos…",
                     font=(FAMILIA, 10), text_color="gray", justify="center").pack()
        barra = ctk.CTkProgressBar(prog, width=350, mode="indeterminate")
        barra.pack(pady=12)
        barra.start()
        try:
            prog.update_idletasks()
            prog.update()
        except Exception:
            pass
    except Exception as e_prog:
        print("[Escáner] No se pudo mostrar el progreso:", e_prog)
        prog = None
    try:
        return mod_escaner.escanear(*args, **kwargs)
    finally:
        if prog is not None:
            try:
                prog.grab_release()
            except Exception:
                pass
            try:
                prog.destroy()
            except Exception:
                pass


class VentanaDiferenciales(ctk.CTkToplevel):
    """🧾 Diferenciales entre el estado de cuenta y el sistema.

    Permite elegir cuáles incluir, escribirles una descripción y exportarlos a Excel.
    """

    def __init__(self, app, banco, faltantes, pendientes):
        super().__init__(app.parent_frame)
        self.app = app
        self.banco = banco or {}
        self.title("🧾 Diferenciales — %s" % construir_etiqueta_banco(self.banco))
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            self.geometry("%dx%d+%d+%d" % (min(1050, sw - 60), min(700, sh - 80),
                                           max(0, (sw - min(1050, sw - 60)) // 2),
                                           2 if sh < 800 else 40))
        except Exception:
            self.geometry("1000x680")
        try:
            self.transient(app.parent_frame.winfo_toplevel())
            self.grab_set()
        except Exception:
            pass

        self.filas = []
        for f in (faltantes or []):
            self.filas.append({"fila": f, "origen": "Estado de cuenta"})
        for f in (pendientes or []):
            self.filas.append({"fila": f, "origen": "Sistema"})

        f_top = ctk.CTkFrame(self, fg_color="transparent")
        f_top.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(f_top, text="🧾 Diferenciales encontrados (%d)" % len(self.filas),
                     font=(FAMILIA, 15, "bold"), text_color="#1f538d").pack(side="left")
        ctk.CTkLabel(f_top, text="   Banco: " + construir_etiqueta_banco(self.banco),
                     font=(FAMILIA, 11), text_color="#555555").pack(side="left")
        ctk.CTkLabel(self, text="Marcá los que querés incluir y escribí la descripción. "
                                "Los del «Sistema» se marcan como conciliados.",
                     font=(FAMILIA, 10), text_color="#7f8c8d").pack(anchor="w", padx=16)

        # encabezado de columnas
        f_cab = ctk.CTkFrame(self, fg_color="#eaf2f8", corner_radius=4)
        f_cab.pack(fill="x", padx=14, pady=(6, 0))
        for texto, ancho in (("Incluir", 70), ("Fecha", 95), ("Origen", 120),
                             ("Descripción del banco/sistema", 330),
                             ("Descripción a guardar", 250), ("Monto", 110)):
            ctk.CTkLabel(f_cab, text=texto, font=(FAMILIA, 11, "bold"), width=ancho,
                         anchor="w").pack(side="left", padx=4, pady=4)

        self.f_scroll = ctk.CTkScrollableFrame(self, fg_color="transparent", height=420)
        self.f_scroll.pack(fill="both", expand=True, padx=14, pady=(2, 6))

        self.widgets = []
        for posicion, item in enumerate(self.filas):
            fila = item["fila"]
            monto = float(fila.get("monto") or 0)
            f_fila = ctk.CTkFrame(self.f_scroll, fg_color=("#ffffff" if posicion % 2 == 0
                                                           else "#f7f9fa"), corner_radius=3)
            f_fila.pack(fill="x", pady=1)
            var = tk.BooleanVar(value=(item["origen"] == "Estado de cuenta"))
            ctk.CTkCheckBox(f_fila, text="", variable=var, width=70, checkbox_width=18,
                            checkbox_height=18).pack(side="left", padx=(6, 0))
            ctk.CTkLabel(f_fila, text=fecha_para_mostrar(fila.get("fecha", "")), width=95,
                         anchor="w", font=(FAMILIA, 10)).pack(side="left", padx=4)
            color_origen = "#b9770e" if item["origen"] == "Estado de cuenta" else "#1f538d"
            ctk.CTkLabel(f_fila, text=item["origen"], width=120, anchor="w",
                         font=(FAMILIA, 10, "bold"), text_color=color_origen).pack(side="left", padx=4)
            ctk.CTkLabel(f_fila, text=str(fila.get("descripcion") or "")[:60], width=330,
                         anchor="w", font=(FAMILIA, 10)).pack(side="left", padx=4)
            entrada = ctk.CTkEntry(f_fila, width=250, font=(FAMILIA, 10),
                                   placeholder_text="descripción…")
            entrada.insert(0, str(fila.get("descripcion") or ""))
            entrada.pack(side="left", padx=4)
            color_monto = "#1e8449" if monto >= 0 else "#c0392b"
            ctk.CTkLabel(f_fila, text=formatear_monto(monto), width=110, anchor="e",
                         font=(FAMILIA, 10, "bold"), text_color=color_monto).pack(side="left", padx=6)
            self.widgets.append({"item": item, "var": var, "entrada": entrada})

        # pie con acciones
        f_pie = ctk.CTkFrame(self, fg_color="transparent")
        f_pie.pack(fill="x", padx=14, pady=(4, 12))
        ctk.CTkButton(f_pie, text="☑ Todos", width=90, font=(FAMILIA, 11, "bold"),
                      fg_color="#5d6d7e", hover_color="#4a5967",
                      command=lambda: self.marcar(True)).pack(side="left", padx=(0, 6))
        ctk.CTkButton(f_pie, text="☐ Ninguno", width=100, font=(FAMILIA, 11, "bold"),
                      fg_color="#5d6d7e", hover_color="#4a5967",
                      command=lambda: self.marcar(False)).pack(side="left", padx=6)
        ctk.CTkButton(f_pie, text="📊 Exportar a Excel", width=170, font=(FAMILIA, 11, "bold"),
                      fg_color="#1e8449", hover_color="#196f3d",
                      command=self.exportar).pack(side="left", padx=6)
        ctk.CTkButton(f_pie, text="❌ Cerrar", width=110, font=(FAMILIA, 11, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.destroy).pack(side="right")
        ctk.CTkButton(f_pie, text="➕ Incluir seleccionados", width=200,
                      font=(FAMILIA, 11, "bold"), fg_color="#e67e22", hover_color="#ca6f1e",
                      command=self.incluir).pack(side="right", padx=6)

    # ---------------------------------------------------------------- acciones
    def marcar(self, valor):
        for w in self.widgets:
            w["var"].set(bool(valor))

    def seleccionados(self):
        return [w for w in self.widgets if w["var"].get()]

    def incluir(self):
        elegidos = self.seleccionados()
        if not elegidos:
            return messagebox.showinfo("Diferenciales",
                                       "Marcá al menos un diferencial para incluirlo.",
                                       parent=self)
        if not messagebox.askyesno(
                "Incluir diferenciales",
                "Se incluirán %d diferencial(es) en el banco." % len(elegidos) + chr(10) + chr(10) +
                "• Los del estado de cuenta se crean como movimiento MANUAL." + chr(10) +
                "• Los del sistema quedan marcados como CONCILIADOS." + chr(10) + chr(10) +
                "¿Desea continuar?", parent=self):
            return
        incluidos, errores = 0, []
        for w in elegidos:
            try:
                if self.app.incluir_diferencial(w["item"]["fila"], w["entrada"].get()):
                    incluidos += 1
                else:
                    errores.append(str(w["item"]["fila"].get("descripcion") or "")[:40])
            except Exception as e:
                errores.append(str(e)[:40])
        try:
            self.app.generar_reporte()
        except Exception:
            pass
        mensaje = "Se incluyeron %d diferencial(es)." % incluidos
        if errores:
            mensaje += chr(10) + chr(10) + "No se pudieron incluir:" + chr(10) + chr(10).join(errores[:5])
        messagebox.showinfo("Diferenciales", mensaje, parent=self)
        if incluidos:
            self.destroy()

    # ---------------------------------------------------------------- exportar
    def exportar(self):
        """Pregunta qué exportar y genera el Excel."""
        respuesta = messagebox.askyesnocancel(
            "Exportar a Excel",
            "¿Exportar SOLO los diferenciales de esta ventana?" + chr(10) + chr(10) +
            "Sí  = este banco / este mes (lo que ves en la ventana)" + chr(10) +
            "No  = TODO el historial (todos los bancos y meses)" + chr(10) +
            "Cancelar = no exportar nada",
            parent=self)
        if respuesta is None:
            return
        if respuesta:
            filas = [(self.banco.get("banco", ""), self.banco.get("cuenta", ""),
                      fecha_para_mostrar(w["item"]["fila"].get("fecha", "")),
                      w["item"]["origen"],
                      str(w["item"]["fila"].get("descripcion") or ""),
                      w["entrada"].get(),
                      float(w["item"]["fila"].get("monto") or 0))
                     for w in self.widgets]
            self.guardar_excel(filas, "Diferenciales_%s" % datetime.now().strftime("%Y%m%d_%H%M"))
        else:
            self.guardar_excel(VentanaDiferenciales.historial(), "Diferenciales_historial_%s"
                               % datetime.now().strftime("%Y%m%d_%H%M"))

    @classmethod
    def exportar_desde_modulo(cls, app):
        """📊 Exportar desde el botón del módulo (sin abrir la ventana)."""
        respuesta = messagebox.askyesnocancel(
            "Exportar diferenciales a Excel",
            "¿Exportar SOLO los diferenciales del banco/mes actual?" + chr(10) + chr(10) +
            "Sí = de este banco y mes" + chr(10) +
            "No = TODO el historial (todos los bancos y meses)" + chr(10) +
            "Cancelar = no exportar nada",
            parent=app.parent_frame)
        if respuesta is None:
            return
        if respuesta:
            banco = app.banco_seleccionado() or {}
            faltantes = list(getattr(app, "_faltantes_estado_cuenta", None) or [])
            pendientes = list(getattr(app, "_pendientes_sistema", None) or [])
            filas = [(banco.get("banco", ""), banco.get("cuenta", ""),
                      fecha_para_mostrar(f.get("fecha", "")),
                      "Estado de cuenta" if f in faltantes else "Sistema",
                      str(f.get("descripcion") or ""), str(f.get("descripcion") or ""),
                      float(f.get("monto") or 0))
                     for f in (faltantes + pendientes)]
            if not filas:
                return messagebox.showinfo("Exportar", "No hay diferenciales en este banco/mes.",
                                           parent=app.parent_frame)
            cls.guardar_excel_estatico(filas, "Diferenciales_%s"
                                       % datetime.now().strftime("%Y%m%d_%H%M"), app)
        else:
            cls.guardar_excel_estatico(cls.historial(), "Diferenciales_historial_%s"
                                       % datetime.now().strftime("%Y%m%d_%H%M"), app)

    @staticmethod
    def historial():
        """Diferenciales de TODOS los bancos y meses (guardados al subir cada PDF)."""
        filas = []
        _asegurar_tabla_diferenciales()
        conn = conectar_db(silencioso=True)
        if not conn:
            return filas
        try:
            with conn.cursor() as c:
                c.execute("""SELECT banco, cuenta, fecha, origen, descripcion, monto
                             FROM conciliacion_diferenciales
                             ORDER BY banco, fecha""")
                guardados = c.fetchall()
        except Exception as e:
            print("[Diferenciales] historial:", e)
            guardados = []
        finally:
            liberar_conexion(conn)
        for banco_d, cuenta_d, fecha_d, origen_d, desc_d, monto_d in guardados:
            etiqueta = ("Estado de cuenta" if str(origen_d or "") == "estado_cuenta"
                        else "Sistema")
            filas.append((str(banco_d or ""), str(cuenta_d or ""),
                          fecha_para_mostrar(fecha_d), etiqueta,
                          str(desc_d or ""), "", float(monto_d or 0)))
        return filas

    def guardar_excel(self, filas, nombre_base):
        VentanaDiferenciales.guardar_excel_estatico(filas, nombre_base, self)

    @staticmethod
    def guardar_excel_estatico(filas, nombre_base, parent=None):
        """Genera el .xlsx con los diferenciales y avisa dónde quedó."""
        if not filas:
            return messagebox.showinfo("Exportar", "No hay diferenciales para exportar.",
                                       parent=parent)
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
        except Exception:
            return messagebox.showerror("Exportar",
                                        "Falta la librería openpyxl." + chr(10) +
                                        "Instálela con: pip install openpyxl", parent=parent)
        ruta = guardar_archivo_dialogo(titulo="Guardar diferenciales en Excel",
                                       nombre_sugerido=nombre_base + ".xlsx",
                                       extensiones=[("Excel", "*.xlsx")])
        if not ruta:
            return
        if not str(ruta).lower().endswith(".xlsx"):
            ruta = str(ruta) + ".xlsx"
        libro = Workbook()
        hoja = libro.active
        hoja.title = "Diferenciales"
        encabezados = ["Banco", "Cuenta", "Fecha", "Origen", "Descripción (banco/sistema)",
                       "Descripción a guardar", "Monto"]
        hoja.append(encabezados)
        for celda in hoja[1]:
            celda.font = Font(bold=True, color="FFFFFF")
            celda.fill = PatternFill("solid", fgColor="1F538D")
            celda.alignment = Alignment(horizontal="center")
        for fila in filas:
            hoja.append(list(fila))
        anchos = (26, 18, 13, 18, 55, 40, 14)
        for i, ancho in enumerate(anchos, start=1):
            hoja.column_dimensions[chr(64 + i)].width = ancho
        for numero_fila in range(2, hoja.max_row + 1):
            hoja.cell(row=numero_fila, column=7).number_format = '#,##0.00'
        hoja.freeze_panes = "A2"
        total = sum(float(f[6] or 0) for f in filas)
        hoja.append([])
        hoja.append(["", "", "", "", "TOTAL", "", total])
        hoja.cell(row=hoja.max_row, column=6).font = Font(bold=True)
        hoja.cell(row=hoja.max_row, column=7).font = Font(bold=True)
        hoja.cell(row=hoja.max_row, column=7).number_format = '#,##0.00'
        try:
            libro.save(ruta)
        except Exception as e:
            return messagebox.showerror("Exportar", "No se pudo guardar el Excel:" + chr(10) + str(e),
                                        parent=parent)
        messagebox.showinfo("Exportar",
                            "Se exportaron %d diferencial(es) a:" % len(filas) + chr(10) + chr(10) +
                            str(ruta), parent=parent)


class ModuloBancoApp:
    def __init__(self, parent_frame, usuario_activo=""):
        self.parent_frame = parent_frame
        self.usuario_activo = usuario_activo or "Desconocido"
        self.config = cargar_config()
        self.bancos = []               # se cargan enseguida, en segundo plano
        self._cache_saldos_extra = {}  # estados de cuenta/manuales por cuenta
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
                     font=(FAMILIA, 18, "bold"), text_color="#1f538d").pack(side="left")

        self.tabview = ctk.CTkTabview(self.frame_main, segmented_button_selected_color="#1f538d")
        self.tabview.pack(fill="both", expand=True, pady=(10, 0))
        self.tab_saldos = self.tabview.add(" 💵 Saldo de Bancos ")
        self.tab_conciliacion = self.tabview.add(" 🧾 Conciliación Bancaria ")
        self.tab_transferencias = self.tabview.add(" 🔄 Transferencias ")

        # 🖱️ Al pulsar una pestaña (sin romper el cambio de pestaña del Tabview)
        # 👀 Vigila la pestaña: al entrar a Conciliación pone el mes en curso y carga
        try:
            self.frame_main.after(400, self._vigilar_tab_conciliacion)
        except Exception:
            pass
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
        # 🔐 Permisos de escritura (se revisa ANTES del atajo del esquema): en Mac
        #    la app puede conectarse con un usuario de la base distinto del que creó
        #    las tablas, y PostgreSQL rechaza el guardado con «permission denied for
        #    table conciliacion_bancaria». Si este equipo SÍ puede escribir, se
        #    comparten los permisos para que el otro equipo también pueda.
        try:
            if leer_config_disco().get(CLAVE_PERMISOS_BANCO) != "ok":
                _usuario_sin_permiso = self._reparar_permisos_bd()
                if not _usuario_sin_permiso:
                    _cfg = leer_config_disco()
                    _cfg[CLAVE_PERMISOS_BANCO] = "ok"
                    guardar_config_disco(_cfg)
                else:
                    self._bd_sin_permiso = _usuario_sin_permiso
        except Exception:
            pass
        if not forzar:
            try:
                if leer_config_disco().get(CLAVE_ESQUEMA_BANCO) == "ok":
                    return
            except Exception:
                pass
        conn = conectar_db(silencioso=True)
        if not conn:
            raise RuntimeError("No se pudo conectar para verificar el esquema del Banco.")
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
                    CREATE TABLE IF NOT EXISTS conciliacion_movimientos_pdf (
                        id SERIAL PRIMARY KEY,
                        banco VARCHAR(255),
                        fecha VARCHAR(20),
                        descripcion TEXT,
                        monto NUMERIC,
                        saldo NUMERIC,
                        creado VARCHAR(30) DEFAULT ''
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
                # 🧾 Diferenciales detectados al subir cada estado de cuenta
                c.execute("""
                    CREATE TABLE IF NOT EXISTS conciliacion_diferenciales (
                        id SERIAL PRIMARY KEY,
                        banco VARCHAR(255),
                        cuenta VARCHAR(120) DEFAULT '',
                        fecha VARCHAR(20) DEFAULT '',
                        descripcion TEXT,
                        monto NUMERIC DEFAULT 0,
                        origen VARCHAR(20) DEFAULT '',
                        descripcion_guardada TEXT DEFAULT '',
                        incluido BOOLEAN DEFAULT FALSE,
                        creado VARCHAR(30) DEFAULT ''
                    )
                """)
                # 🧠 Auditoría de la Conciliación Inteligente. Se creaba a mano en la
                #    base; el programa NUNCA la creaba y, en un equipo/base nuevos,
                #    «Conciliar inteligente» fallaba sin explicación (el historial y
                #    la aprobación dependen de esta tabla).
                c.execute("""
                    CREATE TABLE IF NOT EXISTS conciliacion_auditoria (
                        id SERIAL PRIMARY KEY,
                        id_conciliacion INTEGER DEFAULT 0,
                        id_movimiento_banco INTEGER DEFAULT 0,
                        id_asiento INTEGER,
                        banco VARCHAR(255) DEFAULT '',
                        cuenta VARCHAR(255) DEFAULT '',
                        fecha_contable VARCHAR(20) DEFAULT '',
                        fecha_banco VARCHAR(20) DEFAULT '',
                        dias_desfase INTEGER DEFAULT 0,
                        descripcion_contable TEXT DEFAULT '',
                        descripcion_banco TEXT DEFAULT '',
                        importe_contable NUMERIC DEFAULT 0,
                        importe_banco NUMERIC DEFAULT 0,
                        diferencia NUMERIC DEFAULT 0,
                        moneda VARCHAR(10) DEFAULT 'PEN',
                        sentido VARCHAR(20) DEFAULT '',
                        referencia VARCHAR(120) DEFAULT '',
                        pagina_pdf INTEGER DEFAULT 0,
                        nivel INTEGER DEFAULT 1,
                        estado VARCHAR(20) DEFAULT '',
                        confianza NUMERIC DEFAULT 0,
                        reglas TEXT DEFAULT '',
                        observacion TEXT DEFAULT '',
                        accion_sugerida TEXT DEFAULT '',
                        origen_decision VARCHAR(20) DEFAULT 'manual',
                        aprobado_por VARCHAR(150) DEFAULT '',
                        creado VARCHAR(30) DEFAULT '',
                        modificado VARCHAR(30) DEFAULT '',
                        activo BOOLEAN DEFAULT TRUE
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
                # 🏦 Comisión interbancaria, Nro. de referencia y comprobante adjunto
                for _sql_tx in (
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS comision NUMERIC DEFAULT 0",
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS referencia VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS archivo_ruta TEXT DEFAULT ''",
                ):
                    c.execute(_sql_tx)
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
                for col_sql in (
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS ruc VARCHAR(50)",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS pagado_por_tercero VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS soporte_pago_tercero TEXT DEFAULT ''",
                    "ALTER TABLE facturas_recibidas ADD COLUMN IF NOT EXISTS es_compra_cruzada BOOLEAN DEFAULT FALSE",
                    # Datos del pago manual, para poder reabrirlo y editarlo completo
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS categoria VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS subcategoria VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS evento VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS chofer VARCHAR(150) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS comision NUMERIC DEFAULT 0",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS interbancario BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS es_cruzada BOOLEAN DEFAULT FALSE",
                    # Enlace con el gasto creado en el módulo de Compras
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS id_gasto_compras INTEGER DEFAULT 0",
                    # N° de operación / referencia del pago (Banco y Compras)
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS numero_operacion VARCHAR(100) DEFAULT ''",
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS tabla VARCHAR(60) DEFAULT ''",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS cuenta_origen VARCHAR(255) DEFAULT ''",
                    "ALTER TABLE pagos_comprobantes ADD COLUMN IF NOT EXISTS numero_operacion VARCHAR(100) DEFAULT ''",
                    # 🏦 El estado de cuenta se guardaba SOLO por banco: subir el PDF
                    #    de la segunda cuenta borraba el de la primera.
                    "ALTER TABLE conciliacion_movimientos_pdf ADD COLUMN IF NOT EXISTS cuenta VARCHAR(255) DEFAULT ''",
                    # 📅 Período (AAAA-MM) del estado de cuenta: permite tener
                    #    guardados VARIOS meses del mismo banco/cuenta a la vez.
                    "ALTER TABLE conciliacion_movimientos_pdf ADD COLUMN IF NOT EXISTS periodo VARCHAR(7) DEFAULT ''",
                    # 📎 Respaldo del PDF del estado de cuenta en la carpeta sincronizada.
                    "ALTER TABLE conciliacion_movimientos_pdf ADD COLUMN IF NOT EXISTS archivo_ruta TEXT DEFAULT ''",
                ):
                    c.execute(col_sql)
                # 🚀 Índices: la conciliación consulta por banco en cada repintado.
                for _sql_idx in (
                    "CREATE INDEX IF NOT EXISTS idx_conc_bancaria_banco ON conciliacion_bancaria (banco, estado)",
                    "CREATE INDEX IF NOT EXISTS idx_conc_pdf_banco ON conciliacion_movimientos_pdf (banco, cuenta)",
                    "CREATE INDEX IF NOT EXISTS idx_conc_auditoria_banco ON conciliacion_auditoria (banco, estado)",
                ):
                    try:
                        c.execute(_sql_idx)
                    except Exception as _e_idx:
                        # Un índice es una mejora, no un requisito: no se aborta el esquema.
                        print("[Banco] índice:", _e_idx)
                # Un solo commit: nunca publicar un esquema parcialmente migrado.
                conn.commit()
        except Exception as exc:
            conn.rollback()
            todo_ok = False
            raise RuntimeError("No se pudo actualizar el esquema del Banco; no se guardó ningún cambio.") from exc
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


    TABLAS_BANCO = ("conciliacion_bancaria", "conciliacion_movimientos_pdf",
                    "conciliacion_ignorados", "transferencias_bancarias",
                    "pagos_comprobantes", "facturas_recibidas")

    def _reparar_permisos_bd(self, compartir=True):
        """Asegura que el usuario conectado pueda ESCRIBIR en las tablas del Banco.

        Devuelve el nombre del usuario si sigue SIN permiso ('' si está todo bien).
        1) Intenta reparar el permiso del usuario actual (GRANT / cambiar dueño).
        2) Si este equipo SÍ puede escribir, otorga los permisos a PUBLIC para que
           otro equipo con OTRO usuario de base (típico en macOS) también pueda.
        """
        conn = conectar_db(silencioso=True)
        if not conn:
            return ""
        try:
            with conn.cursor() as c:
                c.execute("SELECT current_user")
                usuario = str((c.fetchone() or [""])[0])
                sin_permiso = []
                for tabla in self.TABLAS_BANCO:
                    try:
                        c.execute("SELECT has_table_privilege(current_user, %s, 'INSERT')", (tabla,))
                        if not bool((c.fetchone() or [False])[0]):
                            sin_permiso.append(tabla)
                    except Exception:
                        conn.rollback()
                if sin_permiso:
                    for tabla in sin_permiso:
                        for sql in ('GRANT ALL PRIVILEGES ON TABLE "%s" TO CURRENT_USER' % tabla,
                                    'ALTER TABLE "%s" OWNER TO CURRENT_USER' % tabla):
                            try:
                                c.execute(sql)
                                conn.commit()
                            except Exception:
                                conn.rollback()
                    quedan = []
                    for tabla in sin_permiso:
                        try:
                            c.execute("SELECT has_table_privilege(current_user, %s, 'INSERT')", (tabla,))
                            if not bool((c.fetchone() or [False])[0]):
                                quedan.append(tabla)
                        except Exception:
                            conn.rollback()
                    if quedan:
                        return usuario
                # Este equipo escribe sin problema: se comparten los permisos para
                # que el otro equipo (usuario de base distinto) también pueda guardar.
                if compartir:
                    try:
                        for tabla in self.TABLAS_BANCO:
                            c.execute('GRANT ALL PRIVILEGES ON TABLE "%s" TO PUBLIC' % tabla)
                        c.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO PUBLIC")
                        conn.commit()
                    except Exception:
                        conn.rollback()
                return ""
        except Exception:
            return ""
        finally:
            liberar_conexion(conn)

    @staticmethod
    def _texto_error_permisos(usuario):
        """Explicación accionable del error de permisos (con el SQL exacto)."""
        u = usuario or "USUARIO_DE_LA_BASE"
        return (
            "El usuario de la base de datos «%s» no tiene permiso para guardar "
            "en conciliacion_bancaria.\n\n"
            "Solución (una sola vez): abra Supabase → SQL Editor, con el usuario "
            "dueño de las tablas, y ejecute:\n\n"
            "  GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO \"%s\";\n"
            "  GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO \"%s\";\n\n"
            "O bien use en este equipo el MISMO usuario de base de datos que en el "
            "otro equipo." % (usuario or "(desconocido)", u, u))

    # -----------------------------------------------------
    # CARGA EN SEGUNDO PLANO (cada consulta a Supabase tarda ~0,3 s)
    # -----------------------------------------------------
    def _mostrar_cargando(self, texto="⏳ Cargando datos del banco..."):
        """Aviso mientras se traen los datos (la ventana ya está visible)."""
        try:
            for w in self.scroll_saldos.winfo_children():
                w.destroy()
            ctk.CTkLabel(self.scroll_saldos, text=texto, font=(FAMILIA, 13),
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

    def _cargar_extras_saldos(self, bancos=None):
        """Líneas de estado de cuenta y manuales por cuenta, para el saldo.

        Se llama en segundo plano: así el saldo puede usar el banco sin
        congelar la ventana con consultas a Supabase.
        """
        extras = {}
        for b in (bancos or self.bancos or []):
            clave = (str(b.get("banco", "")), str(b.get("cuenta", "") or ""))
            try:
                extras[clave] = {
                    "pdf": self._cargar_movimientos_pdf(b) or [],
                    "manuales": self.cargar_manuales(b) or [],
                }
            except Exception as error:
                print("[Saldos] no se pudieron cargar los extras:", error)
                extras[clave] = {"pdf": [], "manuales": []}
        return extras

    def _cargar_datos_iniciales(self):
        """Trae de Supabase lo pesado SIN congelar la ventana."""
        self._mostrar_cargando()

        def _trabajo(estado):
            self.inicializar_db()                      # sólo la primera vez por equipo
            bancos = cargar_bancos()
            estado["bancos"] = bancos
            # Estados de cuenta y manuales de cada cuenta (para el saldo real).
            estado["extras_saldos"] = self._cargar_extras_saldos(bancos)
            # 3 consultas para TODOS los bancos; el reparto por banco y el filtro
            # del periodo elegido se aplican después, al dibujar las tarjetas.
            estado["datos"] = self._cargar_movimientos_crudos()
            estado["transferencias"] = self.cargar_transferencias()

        ejecutar_en_hilo(self.parent_frame, _trabajo, al_terminar=self._aplicar_datos_iniciales)

    def _aplicar_datos_iniciales(self, estado):
        """Se ejecuta en el hilo principal cuando terminó la carga."""
        if estado.get("error"):
            self._mostrar_cargando("❌ No se pudieron cargar los datos del Banco.")
            messagebox.showerror("Banco", str(estado["error"]), parent=self.parent_frame)
            return
        self.bancos = estado.get("bancos") or []
        self._cache_saldos_extra = estado.get("extras_saldos") or {}
        self._actualizar_combos_bancos()
        # 🔄 Si la pestaña de Conciliación ya está abierta, se carga ahora que hay bancos
        try:
            if "conciliaci" in str(self.tabview.get()).lower():
                self._tab_conc_visto = None
                self.frame_main.after(120, self._vigilar_tab_conciliacion)
        except Exception:
            pass
        self.refrescar_saldos(estado.get("datos"))
        # 🔄 Ahora que hay bancos, se carga la conciliación del mes en curso
        try:
            # 🚀 no se bloquea la pestaña: la conciliación se carga un instante después
            self.frame_main.after(600, self._cargar_conciliacion_cuando_haya_bancos)
        except Exception:
            try:
                self._cargar_conciliacion_cuando_haya_bancos()
            except Exception:
                pass
        self._pintar_transferencias(estado.get("transferencias") or [])

    def actualizar_conciliacion_banco(self):
        """Recarga la pestaña visible al pulsar el 🔄 Actualizar de la ventana.

        Antes el botón solo recargaba saldos/la tabla, pero NO volvía a calcular
        el cruce: el aviso naranja y la barra de diferencia quedaban viejos.
        """
        try:
            pestana = str(self.tabview.get())
        except Exception:
            pestana = ""
        try:
            if "Conciliaci" in pestana:
                self.generar_reporte()
            elif "Transferencia" in pestana:
                self.refrescar_transferencias()
            else:
                self.actualizar_saldos_boton()
        except Exception as error:
            print("[Banco] actualizar pestaña:", error)

    def _al_cambiar_pestana(self, nombre=None):
        """Recalcula los saldos cada vez que se entra a la pestaña de saldos.

        Así el saldo no se queda con datos viejos cuando en otro módulo se
        elimina una factura emitida (con sus cobros) o una factura de compra
        (con sus pagos)."""
        # Esta versión de customtkinter llama al comando SIN argumentos, por eso
        # se consulta la pestaña activa directamente.
        if not nombre:
            try:
                nombre = self.tabview.get()
            except Exception:
                nombre = ""
        if "Conciliaci" in (nombre or ""):
            # 🔄 Al entrar a Conciliación se recarga sola: en Compras/Cobranza
            #    pudieron registrarse pagos o borrarse facturas. Así el botón
            #    «Cargar Movimientos» queda sólo para forzar la actualización.
            try:
                self.generar_reporte()
            except Exception as error:
                print("Aviso - refresco de conciliación al entrar:", error)
            return
        if "Saldo de Bancos" not in (nombre or ""):
            return
        try:
            self.actualizar_saldos_boton()
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
            estado["extras_saldos"] = self._cargar_extras_saldos(self.bancos)

        def _terminar(estado):
            if estado.get("error"):
                self._mostrar_cargando("❌ No se pudieron actualizar los saldos.")
                messagebox.showerror("Banco", str(estado["error"]), parent=self.parent_frame)
                return
            self._cache_saldos_extra = estado.get("extras_saldos") or {}
            self.refrescar_saldos(estado["datos"])

        ejecutar_en_hilo(self.parent_frame, _trabajo, al_terminar=_terminar)

    # -----------------------------------------------------
    # TAB: SALDO DE BANCOS
    # -----------------------------------------------------
    def construir_tab_saldos(self):
        f_top = ctk.CTkFrame(self.tab_saldos, fg_color="transparent")
        f_top.pack(fill="x", pady=(5, 10))
        ctk.CTkLabel(f_top, text="Saldo según banco: estados de cuenta cargados + movimientos de meses sin estado.",
                     font=(FAMILIA, 11, "italic"), text_color="gray").pack(side="left")
        ctk.CTkButton(f_top, text="🔄 Actualizar", width=130, font=(FAMILIA, 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.actualizar_saldos_boton).pack(side="right")

        # 🗓️ Selector de periodo: un mes concreto o todos los meses
        # (se empaqueta primero el combo y después la etiqueta para que se lea
        #  "Periodo: [Mes] 🔄 Actualizar")
        self.cmb_mes_saldos = ctk.CTkComboBox(f_top, values=construir_valores_mes(), width=170,
                                              state="readonly", command=self._cambiar_mes_saldos)
        self.cmb_mes_saldos.pack(side="right", padx=(10, 12))
        ctk.CTkLabel(f_top, text="Periodo:", font=(FAMILIA, 12, "bold")).pack(side="right", padx=(0, 6))
        self.mes_saldos = None
        self.mes_saldos_etiqueta = ""
        self.cmb_mes_saldos.set("Todos los meses")

        self.scroll_saldos = ctk.CTkScrollableFrame(self.tab_saldos, fg_color="transparent")
        self.scroll_saldos.pack(fill="both", expand=True)
        # La ventana aparece de inmediato; los saldos se calculan aparte para no
        # congelarla (cada consulta a Supabase tarda ~0,3 s).
        self._mostrar_cargando()

    def _aplicar_filtro_montos(self, modo):
        """Filtra todas las tablas: todos, solo negativos o solo positivos."""
        if modo == "negativos":
            if self.var_solo_negativos.get():
                self.var_solo_positivos.set(False)
            activo = "negativos" if self.var_solo_negativos.get() else "todos"
        elif modo == "positivos":
            if self.var_solo_positivos.get():
                self.var_solo_negativos.set(False)
            activo = "positivos" if self.var_solo_positivos.get() else "todos"
        else:
            self.var_solo_negativos.set(False)
            self.var_solo_positivos.set(False)
            activo = "todos"
        try:
            filtrar_montos_negativos(activo)
        except Exception:
            pass

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
                         font=(FAMILIA, 13), text_color="#856404", justify="left").pack(padx=15, pady=15)
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
                     font=(FAMILIA, 14, "bold"),
                     text_color="white").pack(side="left", padx=15, pady=12)
        ctk.CTkLabel(f_total, text=formatear_monto(total_general), font=(FAMILIA, 16, "bold"),
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
                     font=(FAMILIA, 15, "bold"),
                     text_color="#1f538d").pack(side="left")
        if es_mes:
            ctk.CTkLabel(fila_titulo, text=f"Al cierre de {etiqueta_periodo} ►",
                         font=(FAMILIA, 11, "italic"), text_color="#7f8c8d").pack(side="left", padx=(10, 0))
        ctk.CTkLabel(fila_titulo, text=resumen["saldo_actual_txt"], font=(FAMILIA, 16, "bold"),
                     text_color="#27ae60" if resumen["saldo_actual"] >= 0 else "#c0392b").pack(side="right")

        fila_det = ctk.CTkFrame(card, fg_color="transparent")
        fila_det.pack(fill="x", padx=12, pady=(0, 6))
        ctk.CTkLabel(fila_det, text=f"Saldo inicial: {formatear_monto(resumen['saldo_inicial'])}",
                     font=(FAMILIA, 11), text_color="gray").pack(side="left", padx=(0, 15))
        sufijo = f" ({etiqueta_periodo})" if es_mes else ""
        ctk.CTkLabel(fila_det, text=f"+ Ingresos{sufijo}: {formatear_monto(resumen['ingresos'])}",
                     font=(FAMILIA, 11), text_color="#27ae60").pack(side="left", padx=(0, 15))
        ctk.CTkLabel(fila_det, text=f"− Egresos{sufijo}: {formatear_monto(resumen['egresos'])}",
                     font=(FAMILIA, 11), text_color="#c0392b").pack(side="left", padx=(0, 15))
        ctk.CTkLabel(fila_det, text=f"({resumen['n_movimientos']} movimientos{sufijo})",
                     font=(FAMILIA, 10, "italic"), text_color="#7f8c8d").pack(side="left")

        # 🏦 Si el saldo corrido del estado de cuenta no coincide con la suma de
        #    los movimientos leídos, se explica el ajuste (el banco es la verdad).
        _ajuste = float(resumen.get("ajuste_estado") or 0)
        if abs(_ajuste) >= 0.005:
            ctk.CTkLabel(card,
                         text=(f"🏦 Saldo según estado de cuenta del banco: ajuste de "
                               f"{formatear_monto(_ajuste)} sobre la suma de movimientos."),
                         font=(FAMILIA, 10, "italic"), text_color="#b9770e",
                         justify="left", wraplength=900).pack(anchor="w", padx=12, pady=(0, 4))

        # 📄 Qué estados de cuenta están CARGADOS para esta cuenta (a la vista).
        try:
            _periodos = sorted(self._periodos_cargados(banco))
            _meses = {"01": "ene", "02": "feb", "03": "mar", "04": "abr", "05": "may",
                      "06": "jun", "07": "jul", "08": "ago", "09": "sep", "10": "oct",
                      "11": "nov", "12": "dic"}
            if _periodos:
                _txt_pdf = ", ".join("%s %s" % (_meses.get(p[5:7], p[5:7]), p[:4])
                                     for p in _periodos)
                _color_pdf = "#7d6608"
            else:
                _txt_pdf = "ninguno cargado todavía"
                _color_pdf = "#c0392b"
            _fila_pdf = ctk.CTkFrame(card, fg_color="transparent")
            _fila_pdf.pack(fill="x", padx=12, pady=(0, 8))
            ctk.CTkLabel(_fila_pdf, text="📄 Estados de cuenta cargados: " + _txt_pdf,
                         font=(FAMILIA, 11), text_color=_color_pdf).pack(side="left")
        except Exception as _e_pdf_card:
            print("[Banco] estados cargados en la tarjeta:", _e_pdf_card)

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
            ctk.CTkLabel(card, text=aviso, font=(FAMILIA, 10, "italic"),
                         text_color=color_aviso, justify="left").pack(anchor="w", padx=12, pady=(0, 4))

        fila_btn = ctk.CTkFrame(card, fg_color="transparent")
        fila_btn.pack(fill="x", padx=12, pady=(0, 10))
        ctk.CTkButton(fila_btn, text="👁 Ver Movimientos", width=160, font=(FAMILIA, 11, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda b=banco: self.ver_movimientos(b)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(fila_btn, text="🧾 Conciliar este Banco", width=180, font=(FAMILIA, 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda b=banco: self.ir_a_conciliacion(b)).pack(side="left")

    def _movimientos_para_saldo(self, banco, movs_sistema):
        """Movimientos que debe sumar la tarjeta de saldo.

        En los meses con estado de cuenta cargado manda el BANCO: se usan sus
        líneas tal cual, estén o no conciliadas, porque el saldo bancario incluye
        efectivo, transferencias, comisiones, etc. En los meses sin estado (por
        ejemplo, el mes en curso) se usan los movimientos del sistema y los
        manuales que no dupliquen uno del sistema.
        """
        movs_sistema = list(movs_sistema or [])

        # 🚫 Los asientos que el usuario eliminó de la conciliación NO deben
        #    seguir sumando al saldo (antes se ocultaban de la tabla pero el
        #    saldo los seguía contando: al borrar uno, el saldo no cambiaba).
        try:
            _ignorados = self.cargar_ignorados(banco)
        except Exception:
            _ignorados = {"sistema": set(), "estado_cuenta": set()}
        _ign_sistema = _ignorados.get("sistema", set())
        _ign_pdf = _ignorados.get("estado_cuenta", set())
        movs_sistema = [
            m for m in movs_sistema
            if self._clave_ignorado(m.get("origen", "sistema"), m.get("tabla", ""),
                                    m.get("id", 0), m.get("fecha"), m.get("monto"),
                                    m.get("descripcion", "")) not in _ign_sistema
        ]

        # 0) Lo cargado en segundo plano (si está) evita consultar Supabase aquí.
        clave_cache = (str(banco.get("banco", "")), str(banco.get("cuenta", "") or ""))
        extras = (getattr(self, "_cache_saldos_extra", None) or {}).get(clave_cache) or {}
        lineas_pdf = extras.get("pdf")
        if lineas_pdf is None:
            try:
                lineas_pdf = self._cargar_movimientos_pdf(banco) or []
            except Exception as error:
                print("[Saldos] no se pudieron leer los estados de cuenta:", error)
                lineas_pdf = []

        # 1) Líneas del estado de cuenta, agrupadas por período (AAAA-MM).
        pdf_por_mes = {}
        try:
            for linea in lineas_pdf:
                monto = float(linea.get("monto") or 0)
                if abs(monto) < 0.0001:
                    continue
                # 🚫 Línea descartada en la conciliación: no suma al saldo
                if self._clave_ignorado("estado_cuenta", "", 0, linea.get("fecha"),
                                        monto, linea.get("descripcion", "")) in _ign_pdf:
                    continue
                mes = str(linea.get("periodo") or "") or clave_fecha(linea.get("fecha"))[:7]
                if not mes:
                    continue
                pdf_por_mes.setdefault(mes, []).append({
                    "id": None,
                    "fecha": linea.get("fecha", ""),
                    "descripcion": linea.get("descripcion", ""),
                    "monto": monto,
                    "saldo": linea.get("saldo"),
                    "tipo": "Banco",
                    "origen": "estado_cuenta",
                    "tabla": "conciliacion_movimientos_pdf",
                    "documento": "",
                    "archivo_ruta": "",
                })
        except Exception as error:
            print("[Saldos] no se pudieron leer los estados de cuenta:", error)
            pdf_por_mes = {}

        # 2) Manuales que no estén ya como movimiento del sistema.
        manuales = extras.get("manuales")
        if manuales is None:
            try:
                manuales = self.cargar_manuales(banco) or []
            except Exception as error:
                print("[Saldos] no se pudieron leer los movimientos manuales:", error)
                manuales = []
        pendientes_sistema = {}
        for m in movs_sistema:
            clave = (clave_fecha(m.get("fecha")), round(float(m.get("monto") or 0), 2))
            pendientes_sistema[clave] = pendientes_sistema.get(clave, 0) + 1
        manuales_por_mes = {}
        for m in manuales:
            clave = (clave_fecha(m.get("fecha")), round(float(m.get("monto") or 0), 2))
            if pendientes_sistema.get(clave, 0) > 0:
                pendientes_sistema[clave] -= 1
                continue
            mes = clave_fecha(m.get("fecha"))[:7]
            manuales_por_mes.setdefault(mes, []).append(m)

        # 3) Cada mes se toma de UNA sola fuente para no duplicar.
        sistema_por_mes = {}
        for m in movs_sistema:
            sistema_por_mes.setdefault(clave_fecha(m.get("fecha"))[:7], []).append(m)
        salida = []
        for mes in sorted(set(pdf_por_mes) | set(sistema_por_mes) | set(manuales_por_mes)):
            if mes in pdf_por_mes:
                salida.extend(pdf_por_mes[mes])
            else:
                salida.extend(sistema_por_mes.get(mes, []))
                salida.extend(manuales_por_mes.get(mes, []))
        return salida

    def calcular_resumen(self, banco, datos=None, mes=None):
        """Saldo del banco. Si se pasa 'datos' (movimientos crudos ya leídos de
        la base) no se vuelve a consultar Supabase.

        El saldo se arma con los movimientos REALES del banco: en los meses con
        estado de cuenta cargado se usan sus líneas (conciliadas o no) y en los
        meses sin estado se usan los movimientos del sistema y los manuales.
        Así entran efectivo, transferencias, comisiones, etc.

        Si 'mes' (formato 'YYYY-MM') viene informado, los importes mostrados
        son los del MES y el saldo es el de CIERRE de ese mes
        (inicial + todo lo movido hasta el último día del mes).
        Con 'mes' vacío se muestra el saldo actual con todos los movimientos.
        """
        if mes is None:
            mes = getattr(self, "mes_saldos", None)
        movs_sistema = self._movimientos_de_banco(banco, datos)
        # 🏦 El saldo usa el estado de cuenta en los meses cargados y, en los
        #    meses sin estado, los movimientos del sistema y los manuales.
        movs_todos = self._movimientos_para_saldo(banco, movs_sistema)
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
        # 🏦 Si hay estado de cuenta, manda el SALDO CORRIDO del banco: la suma
        #    de los movimientos leídos puede quedar corta si el PDF trae alguna
        #    línea que no se pudo interpretar (el saldo sí es exacto).
        ancla_fecha, ancla_saldo = None, None
        for m in acumulados:
            if str(m.get("origen") or "") != "estado_cuenta":
                continue
            saldo_m = m.get("saldo")
            if saldo_m is None:
                continue
            f_m = clave_fecha(m.get("fecha"))
            if not f_m:
                continue
            if ancla_fecha is None or f_m >= ancla_fecha:
                ancla_fecha, ancla_saldo = f_m, float(saldo_m)
        if ancla_saldo is not None:
            saldo = ancla_saldo + sum(m["monto"] for m in acumulados
                                      if clave_fecha(m.get("fecha")) > ancla_fecha)
        else:
            saldo = saldo_inicial + sum(m["monto"] for m in acumulados)
        ajuste_estado = round(saldo - (saldo_inicial + sum(m["monto"] for m in acumulados)), 2)
        etiqueta_mes = getattr(self, "mes_saldos_etiqueta", "") if mes else ""
        return {
            "saldo_inicial": saldo_inicial,
            "ingresos": ingresos,
            "egresos": egresos,
            "saldo_actual": saldo,
            "saldo_actual_txt": formatear_monto(saldo),
            "ajuste_estado": ajuste_estado,
            "ancla_fecha": ancla_fecha,
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
            raise RuntimeError("No se pudieron leer movimientos bancarios: sin conexión.")
        try:
            with conn.cursor() as c:
                try:
                    # 🛡️ Solo se cuentan los cobros que APUNTAN A UNA FACTURA EXISTENTE:
                    # si una factura emitida se elimina y su cobro queda huérfano, ese
                    # cobro ya no debe sumar como ingreso en el saldo del banco.
                    c.execute("""
                        SELECT p.id, p.fecha_pago, p.cliente_nombre, p.monto_pagado,
                               COALESCE(p.cuenta_destino, ''), p.id_factura,
                               COALESCE(f.numero_documento, ''), COALESCE(p.codigo_cotizacion, ''),
                               COALESCE(p.archivo_ruta, ''),
                               COALESCE(p.numero_operacion, '')
                        FROM pagos_clientes p
                        LEFT JOIN facturas_emitidas f ON p.id_factura = f.id
                        WHERE TRUE  -- se incluyen tambien los huerfanos (factura borrada)
                    """)
                    datos["cobros"] = c.fetchall()
                except Exception as exc:
                    raise RuntimeError("No se pudieron leer cobros; verifique el esquema del Banco.") from exc
                try:
                    # 🛡️ Igual que los cobros: un pago cuya factura de compra ya no
                    # existe (huérfano) no debe seguir restando en el saldo.
                    c.execute("""
                        SELECT p.id, p.fecha_pago, p.proveedor_nombre, p.monto_pagado,
                               COALESCE(p.cuenta_origen, ''), p.id_factura,
                               COALESCE(f.numero_documento, ''), COALESCE(p.codigo_cotizacion, ''),
                               (COALESCE(f.tipo_documento, '') = 'PAGO BANCO'
                                ) AS espejo_banco,
                               COALESCE(p.archivo_ruta, ''),
                               COALESCE(p.numero_operacion, '')
                        FROM pagos_comprobantes p
                        LEFT JOIN facturas_recibidas f ON p.id_factura = f.id
                        WHERE TRUE  -- se incluyen tambien los huerfanos (factura borrada)
                    """)
                    datos["pagos"] = c.fetchall()
                except Exception as exc:
                    raise RuntimeError("No se pudieron leer pagos; verifique el esquema del Banco.") from exc
                try:
                    c.execute("""
                        SELECT id, banco_origen, cuenta_origen, banco_destino, cuenta_destino,
                               fecha, descripcion, monto,
                               COALESCE(comision, 0), COALESCE(referencia, ''),
                               COALESCE(archivo_ruta, '')
                        FROM transferencias_bancarias
                    """)
                    datos["transferencias"] = c.fetchall()
                except Exception as exc:
                    raise RuntimeError("No se pudieron leer transferencias; verifique el esquema del Banco.") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            liberar_conexion(conn)
        return datos

    def _movimientos_de_banco(self, banco, datos=None, mes=None):
        """Cobros (ingresos), pagos (egresos) y transferencias de ESTE banco.

        Si se recibe 'datos' (lo que devuelve _cargar_movimientos_crudos) NO se
        vuelve a consultar la base de datos. Si se recibe 'mes' (formato
        'YYYY-MM'), filtra solo ese mes.
        """
        if datos is None:
            datos = self._cargar_movimientos_crudos()
        movs = []
        for fila_c in (datos.get("cobros") or []):
            idp, fecha, nombre, monto, cuenta, idf, num_doc, ref = fila_c[:8]
            arch = fila_c[8] if len(fila_c) > 8 else ""
            oper_c = str(fila_c[9] or "").strip() if len(fila_c) > 9 else ""
            if banco_matchea(banco, cuenta):
                doc = (num_doc or ref or "").strip()
                desc = f"Cobro {nombre or ''}".replace("  ", " ").strip()
                movs.append({
                    "id": idp, "fecha": fecha or "", "descripcion": desc,
                    "monto": float(monto or 0), "tipo": "ingreso",
                    "origen": "sistema", "tabla": "pagos_clientes", "documento": doc,
                    "archivo_ruta": arch or "", "id_factura": idf or 0,
                    "operacion": oper_c,
                })
        for fila in (datos.get("pagos") or []):
            idp, fecha, nombre, monto, cuenta, idf, num_doc, ref = fila[:8]
            # 'espejo_banco': el propio módulo de Banco creó este pago en Compras
            # (un egreso registrado aquí, o una compra cruzada). Ese movimiento ya se
            # ve como MANUAL en la conciliación, así que no se lista dos veces.
            espejo = bool(fila[8]) if len(fila) > 8 else False
            arch_p = fila[9] if len(fila) > 9 else ""
            oper_p = str(fila[10] or "").strip() if len(fila) > 10 else ""
            if banco_matchea(banco, cuenta):
                doc = (num_doc or ref or "").strip()
                desc = f"Pago {nombre or ''}".replace("  ", " ").strip()
                movs.append({
                    "id": idp, "fecha": fecha or "", "descripcion": desc,
                    "monto": -float(monto or 0), "tipo": "egreso",
                    "origen": "sistema", "tabla": "pagos_comprobantes", "documento": doc,
                    "espejo_banco": espejo,
                    "archivo_ruta": arch_p or "", "id_factura": idf or 0,
                    "operacion": oper_p,
                })
        for fila_tx in (datos.get("transferencias") or []):
            if len(fila_tx) != 11:
                raise ValueError("Lectura incompleta de transferencia: faltan comisión, referencia o archivo.")
            (idt, bo, co, bd, cd, fecha, desc, monto,
             comision_tx, referencia_tx, archivo_tx) = fila_tx
            monto_v = float(monto or 0)
            com_v = float(comision_tx or 0)
            if cuenta_transferencia_matchea(banco, bo, co):
                desc_eg = f"Transferencia a {bd or 'otra cuenta'}"
                if desc:
                    desc_eg += f" ({desc})"
                # 🏦 El banco debita el TOTAL (monto + comisión) en UNA sola línea.
                #    Antes se creaban dos asientos (-300 y -4,80) y el de la comisión
                #    quedaba como sobrante al conciliar.
                total_debitado = monto_v + com_v
                if com_v > 0:
                    desc_eg += " + comisión %s" % formatear_monto(com_v)
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_eg,
                    "monto": -total_debitado, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias",
                    "documento": str(referencia_tx or ""), "archivo_ruta": archivo_tx or "",
                    "comision": com_v,
                })
            if cuenta_transferencia_matchea(banco, bd, cd):
                desc_in = f"Transferencia de {bo or 'otra cuenta'}"
                if desc:
                    desc_in += f" ({desc})"
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_in,
                    "monto": monto_v, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias",
                    "documento": str(referencia_tx or ""), "archivo_ruta": archivo_tx or "",
                })
        # 🧹 Pagos/cobros en 0.00 (tickets del App Grifo sin importe): no son
        #    movimientos bancarios y ensucian la conciliación (aparecían como
        #    «asiento del sistema» que nunca puede cruzar).
        movs = [m for m in movs if abs(float(m.get("monto") or 0)) > 0.0001]
        # Un mismo pago (mismo N de operacion o mismo comprobante) que cancela
        # VARIAS facturas debe verse como UN SOLO asiento.
        movs = self._agrupar_pagos_multiples(movs)
        if mes:
            movs = [m for m in movs if clave_fecha(m["fecha"]).startswith(mes)]
        movs.sort(key=lambda m: (clave_fecha(m["fecha"]), m["monto"]))
        return movs

    def _agrupar_pagos_multiples(self, movs):
        # Une en un solo asiento los pagos/cobros que comparten operacion o comprobante.
        grupos = {}
        salida = []
        for m in movs:
            if m.get("tabla") not in ("pagos_comprobantes", "pagos_clientes"):
                salida.append(m)
                continue
            clave_unica = str(m.get("operacion") or "").strip() or str(m.get("archivo_ruta") or "").strip()
            if not clave_unica:
                salida.append(m)
                continue
            clave = (m.get("tabla"), clave_fecha(m.get("fecha")), clave_unica)
            if clave in grupos:
                principal = grupos[clave]
                principal["monto"] = float(principal.get("monto") or 0) + float(m.get("monto") or 0)
                principal.setdefault("ids", [principal.get("id")]).append(m.get("id"))
                doc = str(m.get("documento") or "").strip()
                if doc:
                    principal.setdefault("documentos", [str(principal.get("documento") or "")]).append(doc)
                continue
            copia = dict(m)
            copia["ids"] = [m.get("id")]
            copia["documentos"] = [str(m.get("documento") or "")]
            grupos[clave] = copia
            salida.append(copia)

        for copia in grupos.values():
            documentos = [d for d in (copia.get("documentos") or []) if d]
            unicos = list(dict.fromkeys(documentos))
            if len(unicos) > 1:
                prefijo = "Cobro" if copia.get("tabla") == "pagos_clientes" else "Pago"
                nombre = str(copia.get("descripcion") or "").replace(prefijo, "", 1).strip()
                copia["descripcion"] = "%s %s - %d facturas: %s" % (
                    prefijo, nombre, len(unicos), ", ".join(unicos))
                copia["documento"] = ", ".join(unicos)
        return salida

    @staticmethod
    def _limpiar_conciliaciones_transferencia(cursor, ids):
        """Invalida marcas de principal/comisión dentro de la transacción del CRUD.

        No abre conexión ni hace commit/rollback. El llamador debe propagar errores.
        No borra líneas PDF: no existe un vínculo inequívoco con ellas.
        """
        ids = sorted({int(idt) for idt in ids if int(idt) > 0})
        if not ids:
            return
        tablas = ["transferencias_bancarias", "tx_comision"]
        origenes = [prefijo + tabla for prefijo in ("s:", "sistema:") for tabla in tablas]
        cursor.execute("""DELETE FROM conciliacion_bancaria
            WHERE id_movimiento = ANY(%s)
              AND (origen = ANY(%s) OR (origen = 'sistema' AND tabla = ANY(%s)))""",
            (ids, origenes, tablas))
        cursor.execute("""DELETE FROM conciliacion_ignorados
            WHERE id_movimiento = ANY(%s) AND origen = 'sistema' AND tabla = ANY(%s)""",
            (ids, tablas))

    def _abrir_transferencia_desde_movimiento(self, movimiento):
        """Los asientos virtuales solo se editan a través de su transferencia padre."""
        try:
            idt = int(movimiento.get("id") or 0)
            tx = next((t for t in self.cargar_transferencias() if int(t["id"]) == idt), None)
            if not tx:
                raise ValueError("No se encontró la transferencia padre; recargue los movimientos.")
            self._preparar_form_transferencia(tx)
            self.tabview.set(" 🔄 Transferencias ")
        except Exception as exc:
            messagebox.showerror("Transferencia", str(exc), parent=self.parent_frame)

    def _avisar_ya_conciliados(self):
        """Avisa cuántos movimientos ya tenían asiento (y reinicia el contador)."""
        pendientes = int(getattr(self, "_ya_conciliados", 0) or 0)
        if pendientes <= 0:
            return
        self._ya_conciliados = 0
        try:
            messagebox.showinfo(
                "Conciliación",
                "%d movimiento(s) ya tenían su asiento en la conciliación: "
                "se marcaron como conciliados (no se duplicaron)." % pendientes,
                parent=self.parent_frame)
        except Exception as error:
            print("[Conciliación] aviso de ya conciliados:", error)

    def cargar_movimientos_sistema(self, banco, mes=None):
        """Compatibilidad: movimientos de un banco (consulta la base de datos)."""
        self._avisar_ya_conciliados()
        return self._movimientos_de_banco(banco, mes=mes)

    def ver_movimientos(self, banco):
        resumen = self.calcular_resumen(banco)
        movs = resumen["movimientos"]
        periodo = f" — {resumen['etiqueta_mes']}" if resumen.get("es_mes") else ""
        v = ctk.CTkToplevel(self.parent_frame)
        v.title(f"Movimientos - {construir_etiqueta_banco(banco)}{periodo}")
        v.geometry("760x480")
        v.transient(self.parent_frame)
        traer_al_frente(v)

        ctk.CTkLabel(v, text=f"Movimientos de {construir_etiqueta_banco(banco)}{periodo}",
                     font=(FAMILIA, 15, "bold"), text_color="#1f538d").pack(pady=(15, 5))

        f_tabla = ctk.CTkFrame(v, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=15, pady=10)
        columnas = ("fecha", "documento", "descripcion", "tipo", "monto")
        tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", height=14)
        preparar_colores_montos(tabla)
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
        hsb = ttk.Scrollbar(f_tabla, orient="horizontal", command=tabla.xview)
        tabla.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        tabla.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 🖱️ Clic derecho: abrir la factura / comprobante (Windows/Mac)
        tabla.bind("<Button-3>", lambda e: self._menu_contextual_movimiento(e, tabla, tabla_movs, False))
        tabla.bind("<Button-2>", lambda e: self._menu_contextual_movimiento(e, tabla, tabla_movs, False))
        tabla.bind("<Control-Button-1>", lambda e: self._menu_contextual_movimiento(e, tabla, tabla_movs, False))

        # 🔃 Orden por cualquier columna (clic en el encabezado); por defecto fecha ascendente
        etiquetas_col = {"fecha": "Fecha", "documento": "N° Documento",
                         "descripcion": "Descripción", "tipo": "Tipo", "monto": "Monto"}
        tabla_movs = []
        orden = {"columna": "fecha", "ascendente": True}

        def _valores(m):
            if m.get("tipo") == "comision":
                tipo_txt = "Comisión interbancaria"
            elif m.get("tipo") == "transferencia":
                tipo_txt = "Transferencia (Ingreso)" if m["monto"] > 0 else "Transferencia (Egreso)"
            elif m.get("tipo") == "Banco" or m.get("origen") == "estado_cuenta":
                tipo_txt = "Ingreso (Banco)" if m["monto"] > 0 else "Egreso (Banco)"
            else:
                tipo_txt = "Ingreso (Cobro)" if m["monto"] > 0 else "Egreso (Pago)"
            return (fecha_para_mostrar(m["fecha"]), m.get("documento", ""), m["descripcion"],
                    tipo_txt, formatear_monto(m["monto"]))

        def _clave(m):
            col = orden["columna"]
            if col == "fecha":
                return normalizar_fecha(m["fecha"]) or ""
            if col == "monto":
                return float(m.get("monto") or 0)
            if col == "tipo":
                return _valores(m)[3]
            if col == "documento":
                return str(m.get("documento") or "")
            return str(m.get("descripcion") or "")

        def _pintar():
            filas_ord = sorted(movs, key=_clave, reverse=not orden["ascendente"])
            tabla_movs[:] = filas_ord
            tabla.delete(*tabla.get_children())
            for i, m in enumerate(filas_ord):
                tabla.insert("", tk.END, iid=str(i), values=_valores(m))
            for c in columnas:
                flecha = ""
                if c == orden["columna"]:
                    flecha = " ↑" if orden["ascendente"] else " ↓"
                tabla.heading(c, text=etiquetas_col[c] + flecha)

        def _ordenar(col):
            if orden["columna"] == col:
                orden["ascendente"] = not orden["ascendente"]
            else:
                orden["columna"] = col
                orden["ascendente"] = True
            _pintar()

        for c in columnas:
            tabla.heading(c, command=lambda col=c: _ordenar(col))

        _pintar()

        def eliminar_movimiento():
            sel = tabla.selection()
            if not sel:
                return messagebox.showinfo("Eliminar", "Seleccione un movimiento.", parent=v)
            try:
                m = tabla_movs[int(sel[0])]
            except Exception:
                return
            _tabla = str(m.get("tabla") or "")
            _id = int(m.get("id") or 0)
            if _tabla in ("transferencias_bancarias", "tx_comision"):
                messagebox.showinfo("Transferencia",
                                    "Este asiento pertenece a una transferencia. Edítela o elimínela desde Transferencias; la comisión no se borra por separado.", parent=v)
                self._abrir_transferencia_desde_movimiento(m)
                return
            if _tabla not in ("pagos_clientes", "pagos_comprobantes") or not _id:
                return messagebox.showwarning("Eliminar",
                                              "Este movimiento no se puede eliminar desde aquí.", parent=v)
            _desc = str(m.get("descripcion") or "")
            _monto = formatear_monto(m.get("monto", 0))
            if not messagebox.askyesno(
                    "Eliminar movimiento",
                    "¿Eliminar este movimiento del sistema?" + chr(10) + chr(10)
                    + _desc + "   (" + _monto + ")" + chr(10) + chr(10)
                    + "Se borrará de la base de datos (" + _tabla + ").",
                    parent=v):
                return
            conn_del = conectar_db()
            if not conn_del:
                return
            try:
                c_del = conn_del.cursor()
                c_del.execute("DELETE FROM " + _tabla + " WHERE id = %s", (_id,))
                conn_del.commit()
            except Exception as e_del:
                try:
                    conn_del.rollback()
                except Exception:
                    pass
                return messagebox.showerror("Eliminar", str(e_del), parent=v)
            finally:
                liberar_conexion(conn_del)
            try:
                from buffer_memoria import cache_sistema as _cache
                _cache.invalidar()
            except Exception:
                pass
            try:
                registrar_auditoria(getattr(self, "usuario_activo", "Sistema"), "Banco",
                                    "Eliminó movimiento " + _tabla + ": " + _desc[:60])
            except Exception:
                pass
            messagebox.showinfo("Eliminar", "Movimiento eliminado.", parent=v)
            v.destroy()

        f_botones = ctk.CTkFrame(v, fg_color="transparent")
        f_botones.pack(pady=(0, 15))
        ctk.CTkButton(f_botones, text="🗑️ Eliminar movimiento", width=190, fg_color="#c0392b",
                      hover_color="#922b21", command=eliminar_movimiento).pack(side="left", padx=6)
        ctk.CTkButton(f_botones, text="Cerrar", width=120, fg_color="#34495e",
                      command=v.destroy).pack(side="left", padx=6)

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
        ctk.CTkLabel(r1, text="Banco Origen:", width=130, anchor="w", font=(FAMILIA, 12, "bold")).pack(side="left")
        self.cmb_tx_origen = ctk.CTkComboBox(r1, values=etiquetas, width=340, state="readonly")
        self.cmb_tx_origen.pack(side="left", padx=6)
        if etiquetas:
            self.cmb_tx_origen.set(etiquetas[0])

        r2 = ctk.CTkFrame(f_form, fg_color="transparent"); r2.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(r2, text="Banco Destino:", width=130, anchor="w", font=(FAMILIA, 12, "bold")).pack(side="left")
        self.cmb_tx_destino = ctk.CTkComboBox(r2, values=etiquetas, width=340, state="readonly")
        self.cmb_tx_destino.pack(side="left", padx=6)
        if len(etiquetas) > 1:
            self.cmb_tx_destino.set(etiquetas[1])
        elif etiquetas:
            self.cmb_tx_destino.set(etiquetas[0])

        r3 = ctk.CTkFrame(f_form, fg_color="transparent"); r3.pack(fill="x", padx=12, pady=4)
        ctk.CTkLabel(r3, text="Fecha:", width=130, anchor="w", font=(FAMILIA, 12, "bold")).pack(side="left")
        self.ent_tx_fecha = ctk.CTkEntry(r3, width=170)
        self.ent_tx_fecha.pack(side="left", padx=6)
        self.ent_tx_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ctk.CTkButton(r3, text="📅", width=40,
                      command=lambda: CalendarioNativo(self.parent_frame, self.ent_tx_fecha)).pack(side="left")
        ctk.CTkLabel(r3, text="Monto:", width=90, anchor="w", font=(FAMILIA, 12, "bold")).pack(side="left", padx=(16, 0))
        self.ent_tx_monto = ctk.CTkEntry(r3, width=170)
        self.ent_tx_monto.pack(side="left", padx=6)

        r4 = ctk.CTkFrame(f_form, fg_color="transparent"); r4.pack(fill="x", padx=12, pady=(4, 4))
        ctk.CTkLabel(r4, text="Descripción:", width=130, anchor="w", font=(FAMILIA, 12, "bold")).pack(side="left")
        self.ent_tx_desc = ctk.CTkEntry(r4)
        self.ent_tx_desc.pack(side="left", fill="x", expand=True, padx=6)

        r_com = ctk.CTkFrame(f_form, fg_color="transparent"); r_com.pack(fill="x", padx=12, pady=4)
        self.var_tx_comision = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(r_com, text="Comisión interbancaria S/", variable=self.var_tx_comision,
                        command=self._actualizar_comision_tx).pack(side="left")
        self.ent_tx_comision = ctk.CTkEntry(r_com, width=100)
        self.ent_tx_comision.pack(side="left", padx=6)
        self.ent_tx_comision.insert(0, "4.80")
        for _entrada_tx in (self.ent_tx_monto, self.ent_tx_comision):
            _entrada_tx.bind("<KeyRelease>", lambda e, w=_entrada_tx: self._formatear_monto_tx(w))
            _entrada_tx.bind("<FocusOut>", lambda e, w=_entrada_tx: self._formatear_monto_tx(w))
        ctk.CTkLabel(r_com, text="Referencia / N° operación:").pack(side="left", padx=6)
        self.ent_tx_referencia = ctk.CTkEntry(r_com, width=200)
        self.ent_tx_referencia.pack(side="left", padx=6)

        r_comp = ctk.CTkFrame(f_form, fg_color="transparent"); r_comp.pack(fill="x", padx=12, pady=4)
        self.tx_archivo_ruta = ""
        self._tx_archivo_original = ""
        self._tx_ocr_token = 0
        self.btn_tx_cargar = ctk.CTkButton(r_comp, text="📎 Cargar PDF/imagen", width=170,
                                          command=self.cargar_comprobante_transferencia)
        self.btn_tx_cargar.pack(side="left", padx=4)
        self.btn_tx_escanear = ctk.CTkButton(r_comp, text="🖨 Escanear + OCR", width=160,
                                            command=lambda: self._procesar_comprobante_tx(escanear=True))
        self.btn_tx_escanear.pack(side="left", padx=4)
        # 📱 Escanear con el teléfono y leer el comprobante con OCR
        self.btn_tx_tel = ctk.CTkButton(r_comp, text="📱 Escanear con el teléfono", width=200,
                                        fg_color="#8e44ad", hover_color="#6c3483",
                                        command=self.escanear_transferencia_telefono)
        self.btn_tx_tel.pack(side="left", padx=4)
        ctk.CTkButton(r_comp, text="Quitar adjunto", width=120,
                      command=self._quitar_comprobante_tx).pack(side="left", padx=4)
        self.lbl_tx_archivo = ctk.CTkLabel(r_comp, text="Sin comprobante", anchor="w")
        self.lbl_tx_archivo.pack(side="left", padx=8)

        r5 = ctk.CTkFrame(f_form, fg_color="transparent"); r5.pack(fill="x", padx=12, pady=(4, 12))
        self.btn_tx_guardar = ctk.CTkButton(r5, text="💸 Realizar Transferencia", width=220, height=38,
                                            font=(FAMILIA, 13, "bold"), fg_color="#27ae60",
                                            hover_color="#1e8449", command=self.realizar_transferencia)
        self.btn_tx_guardar.pack(side="left", padx=(0, 8))
        self.btn_tx_editar = ctk.CTkButton(r5, text="✏️ Editar", width=120, height=38,
                                           font=(FAMILIA, 12, "bold"), fg_color="#2980b9",
                                           hover_color="#1f618d", command=self.editar_transferencia)
        self.btn_tx_editar.pack(side="left", padx=(0, 8))
        # Este botón solo se muestra mientras se está editando una transferencia
        self.btn_tx_cancelar = ctk.CTkButton(r5, text="✖ Cancelar edición", width=150, height=38,
                                             font=(FAMILIA, 12, "bold"), fg_color="#7f8c8d",
                                             hover_color="#606b6b",
                                             command=self.cancelar_edicion_transferencia)
        ctk.CTkButton(r5, text="🗑️ Eliminar", width=120, height=38,
                      font=(FAMILIA, 12, "bold"), fg_color="#e74c3c", hover_color="#c0392b",
                      command=self.eliminar_transferencia).pack(side="left")

        self.lbl_tx_modo = ctk.CTkLabel(f_form, text="", font=(FAMILIA, 11, "bold"),
                                        text_color="#2980b9")
        self.lbl_tx_modo.pack(anchor="w", padx=12, pady=(0, 10))

        ctk.CTkLabel(self.tab_transferencias, text="Historial de transferencias:",
                     font=(FAMILIA, 13, "bold"), text_color="#1f538d").pack(anchor="w", padx=5, pady=(0, 4))
        f_tabla = ctk.CTkFrame(self.tab_transferencias, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        columnas = ("fecha", "origen", "destino", "descripcion", "monto", "comision", "total", "referencia")
        self.tabla_tx = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="extended")
        preparar_colores_montos(self.tabla_tx)
        for col, txt, w, anc in (("fecha", "Fecha", 100, "center"),
                                 ("origen", "Banco Origen", 200, "center"),
                                 ("destino", "Banco Destino", 200, "center"),
                                 ("descripcion", "Descripción", 260, "w"),
                                 ("monto", "Monto", 130, "e"),
                                 ("comision", "Comisión", 100, "e"),
                                 ("total", "Total debitado", 130, "e"),
                                 ("referencia", "Referencia", 150, "w")):
            self.tabla_tx.heading(col, text=txt)
            self.tabla_tx.column(col, width=w, anchor=anc)
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_tx.yview)
        hsb = ttk.Scrollbar(f_tabla, orient="horizontal", command=self.tabla_tx.xview)
        self.tabla_tx.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        self.tabla_tx.pack(side="left", fill="both", expand=True)
        # Doble clic: carga la transferencia en el mismo formulario para editarla
        self.tabla_tx.bind("<Double-1>", lambda _e: (
            self.editar_transferencia() if self.tabla_tx.selection() else None))
        vsb.pack(side="right", fill="y")
        for evento in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.tabla_tx.bind(evento, self._menu_transferencia)
        # El historial lo rellena la carga inicial en segundo plano

    def _actualizar_comision_tx(self):
        self.ent_tx_comision.configure(state="normal" if self.var_tx_comision.get() else "disabled")

    def _mostrar_archivo_tx(self):
        nombre = os.path.basename(str(self.tx_archivo_ruta or "").replace("\\", "/"))
        self.lbl_tx_archivo.configure(text=nombre or "Sin comprobante")

    def _quitar_comprobante_tx(self):
        self._tx_ocr_token += 1
        self.tx_archivo_ruta = ""
        self._mostrar_archivo_tx()

    def cargar_comprobante_transferencia(self):
        ruta = filedialog.askopenfilename(parent=self.parent_frame,
            title="Comprobante de transferencia",
            filetypes=[("PDF e imágenes", "*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp")])
        if ruta:
            self._procesar_comprobante_tx(ruta=ruta)

    def _formatear_monto_tx(self, entrada):
        """Deja el monto/comisión con separador de miles al escribir."""
        try:
            actual = entrada.get()
            formateado = formatear_numero_entrada(actual)
            if formateado and formateado != actual:
                entrada.delete(0, tk.END)
                entrada.insert(0, formateado)
        except Exception:
            pass

    def _puntaje_ocr(self, texto):
        """Cuántos datos útiles se leyeron (para elegir la mejor lectura)."""
        if not texto or not texto.strip():
            return -1
        datos = tx_soporte.datos_comprobante(texto, monto_del_comprobante)
        return ((1 if datos.get("monto") else 0) + (1 if datos.get("fecha") else 0)
                + (1 if datos.get("referencia") else 0))

    def _lista_para_ocr(self, motor, paginas):
        """OCR de la página: prueba la imagen tal cual y ampliada, y deja la mejor."""
        textos = []
        try:
            textos.append(motor.texto_de_resultados(motor.ocr_paginas(paginas, idioma="Español")))
        except Exception:
            textos.append("")
        # 🔎 Sin volver a escanear: ampliar y subir contraste suele arreglar el monto.
        if paginas:
            ampliada = ""
            try:
                ampliada = tx_soporte.imagen_ampliada(paginas[0]) or ""
            except Exception:
                ampliada = ""
            if ampliada:
                try:
                    textos.append(motor.texto_de_resultados(motor.ocr_paginas([ampliada], idioma="Español")))
                except Exception:
                    pass
                finally:
                    try:
                        os.remove(ampliada)
                    except Exception:
                        pass
        mejor = ""
        for texto in textos:
            if self._puntaje_ocr(texto) > self._puntaje_ocr(mejor):
                mejor = texto
            elif self._puntaje_ocr(texto) == self._puntaje_ocr(mejor) and len(texto.strip()) > len(mejor.strip()):
                mejor = texto
        return mejor

    def _ocr_suficiente(self, texto):
        """¿El OCR leyó bien el comprobante?

        Además del monto y otro dato, se exige que el importe traiga separador
        decimal: a 300 ppp es típico leer "S/ 17700" (sin el punto) y eso obliga
        a repetir el escaneo a 600 ppp.
        """
        if not texto or not texto.strip():
            return False
        datos = tx_soporte.datos_comprobante(texto, monto_del_comprobante)
        if not datos.get("monto"):
            return False
        if not (datos.get("fecha") or datos.get("referencia")):
            return False
        for linea in str(texto).splitlines():
            for token in re.findall(r"(?:s[/.]|\$)\s*([\d.,]+)", linea, re.I):
                if re.fullmatch(r"\d{4,}", token):
                    return False
        return True

    def _escanear_paginas(self, config, motor, dpi, area=None):
        """Escanea una página al DPI indicado y arma el PDF. Devuelve (paginas, pdf, aviso)."""
        datos = motor.config_escaner(config)
        carpeta = motor.carpeta_escaneos(config)
        escaneo = motor.escanear(
            carpeta,
            dispositivo=datos.get("escaner_dispositivo", ""),
            dpi=str(dpi),
            color="Escala de grises",
            origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
            paginas=1,
            optimizar=True,
            modo=datos.get("escaner_modo", "Automático (sin ventanas)"),
            area=area)
        if not escaneo.get("ok") or not escaneo.get("paginas"):
            raise ValueError(escaneo.get("error") or "No se pudo escanear el comprobante.")
        paginas = escaneo["paginas"]
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        pdf = os.path.join(os.path.dirname(paginas[0]), "Transferencia_Escaneada_" + marca + ".pdf")
        aviso = ""
        try:
            motor.imagenes_a_pdf(paginas, pdf)
        except Exception as exc:
            pdf = paginas[0]
            aviso = "No se pudo armar el PDF (" + str(exc) + "); se adjunta la imagen."
        return paginas, pdf, aviso

    def _escanear_transferencia_ocr(self, config, motor):
        """Escanea a 300 ppp; si el OCR no lee bien, repite el escaneo a 600 ppp."""
        paginas, pdf, aviso = self._escanear_paginas(config, motor, 300)
        texto = self._lista_para_ocr(motor, paginas)
        if not self._ocr_suficiente(texto):
            try:
                # 2º intento: se escanea SOLO la zona del comprobante (más rápido).
                # Se repite el escaneo completo: este driver ignora el área y los ppp.
                paginas2, pdf2, aviso2 = self._escanear_paginas(config, motor, 600)
            except Exception as exc:
                paginas2, pdf2, aviso2 = [], "", str(exc)
            if paginas2:
                texto2 = self._lista_para_ocr(motor, paginas2)
                # Si el primer escaneo no sirve, manda el segundo (para eso se repitió).
                if texto2.strip() or not texto.strip():
                    paginas, pdf, texto = paginas2, pdf2, texto2
                    aviso = aviso2 or aviso
        # Último ajuste: importes que el OCR pegó sin punto decimal.
        try:
            texto = tx_soporte.normalizar_montos_ocr(texto)
        except Exception:
            pass
        return paginas, pdf, texto, aviso

    def _ventana_comprobante_escaneado(self, resultado, aplicar):
        """Ventana de revisión del escaneo (mismo flujo que Compras y Ventas)."""
        ventana = ctk.CTkToplevel(self.parent_frame)
        ventana.title("Comprobante escaneado (OCR)")
        ventana.geometry("900x640")
        ventana.transient(self.parent_frame)
        traer_al_frente(ventana)
        ctk.CTkLabel(ventana, text="COMPROBANTE ESCANEADO - REVISE ANTES DE USAR",
                     font=(FAMILIA, 14, "bold"), text_color="#1f538d").pack(pady=(10, 2))
        datos = resultado.get("datos") or {}
        monto_txt = formatear_monto(datos.get("monto")) if datos.get("monto") else "sin dato"
        ctk.CTkLabel(ventana, text="Fecha: " + str(datos.get("fecha") or "sin dato")
                     + "   ·   Monto: " + monto_txt
                     + "   ·   Referencia: " + str(datos.get("referencia") or "sin dato"),
                     font=(FAMILIA, 11, "bold"), text_color="#2c3e50").pack(pady=(0, 6))
        cuerpo = ctk.CTkFrame(ventana, fg_color="transparent")
        cuerpo.pack(fill="both", expand=True, padx=10, pady=4)
        f_izq = ctk.CTkFrame(cuerpo, fg_color="#f8f9fa")
        f_izq.pack(side="left", fill="both", expand=False, padx=(0, 8))
        paginas = resultado.get("paginas") or []
        if paginas:
            try:
                from PIL import Image
                with Image.open(paginas[0]) as imagen:
                    copia = imagen.convert("RGB")
                copia.thumbnail((420, 520))
                foto = ctk.CTkImage(light_image=copia, size=copia.size)
                ctk.CTkLabel(f_izq, text="", image=foto).pack(padx=6, pady=6)
                ventana._foto_escaneo = foto
            except Exception:
                ctk.CTkLabel(f_izq, text="(sin vista previa)", font=(FAMILIA, 10)).pack(padx=8, pady=8)
        f_der = ctk.CTkFrame(cuerpo, fg_color="transparent")
        f_der.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(f_der, text="Texto leído (OCR):", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        caja = ctk.CTkTextbox(f_der, width=420, height=430)
        caja.pack(fill="both", expand=True)
        caja.insert("1.0", resultado.get("texto") or "(sin texto legible)")
        if resultado.get("aviso"):
            ctk.CTkLabel(ventana, text="Aviso: " + str(resultado["aviso"]), font=(FAMILIA, 10),
                         text_color="#c0392b").pack(pady=(2, 0))
        barra = ctk.CTkFrame(ventana, fg_color="transparent")
        barra.pack(fill="x", pady=8)

        def usar():
            try:
                aplicar(resultado)
            finally:
                ventana.destroy()

        ctk.CTkButton(barra, text="Usar estos datos", width=200, height=34,
                      fg_color="#27ae60", hover_color="#1e8449", command=usar).pack(side="left", padx=8)
        ctk.CTkButton(barra, text="Escanear de nuevo", width=190, height=34,
                      fg_color="#8e44ad", hover_color="#71368a",
                      command=lambda: (ventana.destroy(), self._procesar_comprobante_tx(escanear=True))
                      ).pack(side="left", padx=6)
        ctk.CTkButton(barra, text="Cerrar", width=120, height=34, fg_color="#7f8c8d",
                      hover_color="#606b6b", command=ventana.destroy).pack(side="left")

    def escanear_transferencia_telefono(self):
        """📱 Recibe la foto del comprobante desde el teléfono y la lee con OCR.

        La imagen cae en la carpeta de escaneos (la que sincroniza rclone) y se
        procesa igual que un archivo cargado: completa fecha, monto y referencia
        en el formulario de transferencias.
        """
        try:
            import escaner_movil
        except Exception as error:
            return messagebox.showerror(
                "Escáner", "No se pudo cargar el escáner: " + str(error),
                parent=self.parent_frame)
        try:
            config = dict(cargar_config())
            config.update(leer_config_disco())
        except Exception:
            config = {}

        def al_recibir(rutas, tipo_doc):
            if rutas:
                self._procesar_comprobante_tx(ruta=rutas[0], escanear=False)

        try:
            escaner_movil.escanear_desde_telefono(
                self.parent_frame, config, al_recibir=al_recibir,
                titulo_extra="Banco · comprobante de transferencia")
        except Exception as error:
            messagebox.showerror("Escáner", "No se pudo abrir el escáner: " + str(error),
                                 parent=self.parent_frame)

    def _procesar_comprobante_tx(self, ruta="", escanear=False):
        if getattr(self, "_tx_ocr_ocupado", False):
            return
        self._tx_ocr_ocupado = True
        self._tx_ocr_token += 1
        token = self._tx_ocr_token
        iniciales = {"fecha": self.ent_tx_fecha.get(), "monto": self.ent_tx_monto.get(),
                     "referencia": self.ent_tx_referencia.get()}
        config = dict(cargar_config())
        config.update(leer_config_disco())
        self.btn_tx_cargar.configure(state="disabled")
        self.btn_tx_escanear.configure(state="disabled")
        self.btn_tx_guardar.configure(state="disabled")
        try:
            self.btn_tx_tel.configure(state="disabled")
        except Exception:
            pass
        self.lbl_tx_archivo.configure(text="Escaneando / leyendo OCR...")
        progreso = None
        if escanear:
            try:
                progreso = ctk.CTkToplevel(self.parent_frame)
                progreso.title("Escaneando comprobante de transferencia")
                progreso.geometry("470x175")
                progreso.transient(self.parent_frame)
                traer_al_frente(progreso)
                try:
                    progreso.grab_set()
                except Exception:
                    pass
                ctk.CTkLabel(progreso, text="Escaneando y leyendo el comprobante...",
                             font=(FAMILIA, 13, "bold"), text_color="#1f538d").pack(pady=(18, 4))
                ctk.CTkLabel(progreso, text="Coloque el comprobante en el cristal del escáner."
                                            + chr(10) + "Escanea a 300 ppp; si no se lee bien, repite a 600 ppp.",
                             font=(FAMILIA, 10), text_color="gray", justify="center").pack()
                barra_prog = ctk.CTkProgressBar(progreso, width=340, mode="indeterminate")
                barra_prog.pack(pady=10)
                barra_prog.start()
            except Exception:
                progreso = None

        def trabajo():
            import escaner as motor
            if escanear:
                paginas, archivo, texto, aviso = self._escanear_transferencia_ocr(config, motor)
            else:
                paginas, archivo, texto, aviso = [], ruta, "", ""
                try:
                    texto, aviso = tx_soporte.leer_comprobante(archivo, extraer_texto_pdf, motor, fitz)
                except Exception:
                    aviso = "No se pudo leer OCR; complete los datos manualmente."
            return {"archivo": archivo, "paginas": paginas,
                    "datos": tx_soporte.datos_comprobante(texto, monto_del_comprobante),
                    "aviso": aviso, "texto": texto}

        def aplicar(resultado):
            self.tx_archivo_ruta = resultado["archivo"]
            self._mostrar_archivo_tx()
            datos = resultado["datos"]
            for clave, entrada in (("fecha", self.ent_tx_fecha), ("monto", self.ent_tx_monto),
                                   ("referencia", self.ent_tx_referencia)):
                valor = datos.get(clave)
                if not valor or entrada.get() != iniciales[clave]:
                    continue
                entrada.delete(0, tk.END)
                if clave == "monto":
                    entrada.insert(0, formatear_numero_entrada("%.2f" % float(valor)))
                else:
                    entrada.insert(0, str(valor))

        def terminado(resultado, error):
            try:
                if progreso is not None and progreso.winfo_exists():
                    progreso.grab_release()
                    progreso.destroy()
            except Exception:
                pass
            self._tx_ocr_ocupado = False
            self.btn_tx_cargar.configure(state="normal")
            self.btn_tx_escanear.configure(state="normal")
            self.btn_tx_guardar.configure(state="normal")
            try:
                self.btn_tx_tel.configure(state="normal")
            except Exception:
                pass
            if token != self._tx_ocr_token:
                self._mostrar_archivo_tx()
                return
            if error:
                self._mostrar_archivo_tx()
                messagebox.showerror("Comprobante", error, parent=self.parent_frame)
                return
            self.tx_archivo_ruta = resultado["archivo"]
            self._mostrar_archivo_tx()
            if escanear:
                # 🚀 Escaneo: se completan los datos directamente (sin ventana de revisión).
                aplicar(resultado)
                datos = resultado.get("datos") or {}
                if not (datos.get("monto") or datos.get("fecha") or datos.get("referencia")):
                    messagebox.showwarning(
                        "Comprobante",
                        "Se adjuntó el comprobante, pero el OCR no pudo leer los datos."
                        + chr(10) + "Complete la fecha, el monto y la referencia a mano.",
                        parent=self.parent_frame)
                return
            aplicar(resultado)
            aviso = resultado.get("aviso") or ("" if resultado.get("texto") else "OCR sin texto legible.")
            messagebox.showinfo("Comprobante",
                                "Adjunto cargado. Revise fecha, monto y referencia antes de guardar."
                                + (chr(10) + aviso if aviso else ""), parent=self.parent_frame)

        tx_soporte.ejecutar_sin_tk(self.parent_frame, trabajo, terminado)

    def _menu_transferencia(self, event):
        iid = self.tabla_tx.identify_row(event.y)
        if not iid:
            return "break"
        self.tabla_tx.selection_set(iid)
        tx = getattr(self, "_transferencias_por_id", {}).get(int(iid), {})
        ruta = self._ruta_existente(tx.get("archivo_ruta"))
        menu = tk.Menu(self.parent_frame, tearoff=0)
        menu.add_command(label="Ver comprobante" if ruta else "Ver comprobante (sin archivo)",
                         state="normal" if ruta else "disabled",
                         command=lambda: abrir_documento(ruta))
        menu.add_separator()
        menu.add_command(label="Editar", command=self.editar_transferencia)
        menu.add_command(label="Eliminar transferencia y comisión", command=self.eliminar_transferencia)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

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
        self.ent_tx_fecha.insert(0, fecha_para_mostrar(tx.get("fecha")) or datetime.now().strftime("%d/%m/%Y"))
        self.ent_tx_monto.delete(0, tk.END)
        if tx:
            self.ent_tx_monto.insert(0, formatear_numero_entrada(f"{float(tx.get('monto') or 0):.2f}"))
        self.ent_tx_desc.delete(0, tk.END)
        if tx:
            self.ent_tx_desc.insert(0, tx.get("descripcion", ""))

        self._tx_ocr_token += 1  # descarta resultados OCR de un formulario anterior
        self.ent_tx_referencia.delete(0, tk.END)
        self.ent_tx_referencia.insert(0, tx.get("referencia") or "")
        self.var_tx_comision.set(bool(tx.get("comision")) if tx else True)
        self.ent_tx_comision.configure(state="normal")
        self.ent_tx_comision.delete(0, tk.END)
        self.ent_tx_comision.insert(0, f"{float(tx.get('comision') or 0):.2f}" if tx else "4.80")
        self._actualizar_comision_tx()
        self.tx_archivo_ruta = tx.get("archivo_ruta") or ""
        self._tx_archivo_original = self.tx_archivo_ruta
        self._mostrar_archivo_tx()
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
        try:
            tx = next((t for t in self.cargar_transferencias() if t["id"] == id_tx), None)
        except RuntimeError as exc:
            messagebox.showerror("Transferencia", str(exc), parent=self.parent_frame)
            return
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
        if getattr(self, "_tx_ocr_ocupado", False):
            return
        try:
            monto = tx_soporte.importe(self.ent_tx_monto.get(), positivo=True)
            comision = tx_soporte.importe(self.ent_tx_comision.get()) if self.var_tx_comision.get() else tx_soporte.importe("0")
            fecha = tx_soporte.fecha_valida(self.ent_tx_fecha.get().strip())
            referencia = tx_soporte.referencia_valida(self.ent_tx_referencia.get())
        except ValueError as exc:
            messagebox.showerror("Transferencia", str(exc), parent=self.parent_frame)
            return
        archivo = self.tx_archivo_ruta or ""
        if archivo and archivo != self._tx_archivo_original:
            try:
                archivo = tx_soporte.copiar_comprobante(archivo, obtener_ruta_base())
                self.tx_archivo_ruta = archivo  # permite reintentar sin volver a copiar
                self._mostrar_archivo_tx()
            except (ValueError, OSError) as exc:
                messagebox.showerror("Comprobante", str(exc), parent=self.parent_frame)
                return
        desc = self.ent_tx_desc.get().strip() or "Transferencia entre cuentas"

        bo = self.bancos[etiquetas.index(origen)]
        bd = self.bancos[etiquetas.index(destino)]
        id_edicion = self.id_tx_edicion

        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=self.parent_frame)
            return
        try:
            tx_soporte.asegurar_esquema(conn)
            with conn.cursor() as c:
                if id_edicion:
                    c.execute("""
                        UPDATE transferencias_bancarias
                        SET banco_origen=%s, cuenta_origen=%s, banco_destino=%s, cuenta_destino=%s,
                            fecha=%s, descripcion=%s, monto=%s, comision=%s, referencia=%s, archivo_ruta=%s
                        WHERE id=%s
                    """, (bo.get("banco", ""), bo.get("cuenta", ""), bd.get("banco", ""), bd.get("cuenta", ""),
                          fecha, desc, monto, comision, referencia, archivo, id_edicion))
                    if c.rowcount != 1:
                        raise ValueError("La transferencia ya no existe; vuelva a cargar el historial.")
                    tx_soporte.limpiar_marcas(c, [id_edicion])
                else:
                    c.execute("""
                        INSERT INTO transferencias_bancarias
                        (banco_origen, cuenta_origen, banco_destino, cuenta_destino, fecha, descripcion, monto,
                         comision, referencia, archivo_ruta)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (bo.get("banco", ""), bo.get("cuenta", ""), bd.get("banco", ""), bd.get("cuenta", ""),
                          fecha, desc, monto, comision, referencia, archivo))
                conn.commit()
        except Exception as e:
            conn.rollback()
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
        self.actualizar_saldos_boton()
        self.generar_reporte()
        if id_edicion:
            messagebox.showinfo("Éxito", "Transferencia actualizada correctamente.", parent=self.parent_frame)
        else:
            messagebox.showinfo("Éxito", f"Transferencia de {formatear_monto(monto)} registrada:\n"
                                         f"• Egreso total en {origen}: {formatear_monto(monto + comision)} "
                                         f"({formatear_monto(monto)} + comisión {formatear_monto(comision)})\n"
                                         f"• Ingreso en {destino}: {formatear_monto(monto)}", parent=self.parent_frame)

    def cargar_transferencias(self):
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        rows = []
        try:
            tx_soporte.asegurar_esquema(conn)
            with conn.cursor() as c:
                c.execute("""
                    SELECT id, banco_origen, cuenta_origen, banco_destino, cuenta_destino,
                           fecha, descripcion, monto, COALESCE(comision, 0),
                           COALESCE(referencia, ''), COALESCE(archivo_ruta, '')
                    FROM transferencias_bancarias
                    ORDER BY id DESC
                """)
                for idt, bo, co, bd, cd, fecha, desc, monto, comision, referencia, archivo in c.fetchall():
                    rows.append({
                        "id": idt,
                        "origen": construir_etiqueta_banco({"banco": bo, "cuenta": co}),
                        "destino": construir_etiqueta_banco({"banco": bd, "cuenta": cd}),
                        "fecha": fecha_para_mostrar(fecha or ""), "descripcion": desc or "",
                        "monto": float(monto or 0), "comision": float(comision or 0),
                        "referencia": referencia or "", "archivo_ruta": archivo or "",
                    })
        except Exception as exc:
            conn.rollback()
            raise RuntimeError("No se pudo verificar/cargar el historial de transferencias.") from exc
        finally:
            liberar_conexion(conn)
        return rows

    def _pintar_transferencias(self, filas):
        """Dibuja el historial ya cargado (no consulta la base)."""
        self._transferencias_por_id = {int(t["id"]): t for t in filas}
        try:
            self.tabla_tx.delete(*self.tabla_tx.get_children())
        except Exception:
            return
        for t in filas:
            self.tabla_tx.insert("", tk.END, iid=str(t["id"]),
                                 values=(fecha_para_mostrar(t["fecha"]), t["origen"], t["destino"],
                                         t["descripcion"], formatear_monto(t["monto"]),
                                         formatear_monto(t.get("comision", 0)),
                                         formatear_monto(float(t["monto"] or 0)
                                                         + float(t.get("comision", 0) or 0)),
                                         t.get("referencia", "")))

    def refrescar_transferencias(self):
        try:
            self._pintar_transferencias(self.cargar_transferencias())
        except RuntimeError as exc:
            messagebox.showerror("Transferencias", str(exc), parent=self.parent_frame)

    def eliminar_transferencia(self):
        sel = self.tabla_tx.selection()
        if not sel:
            messagebox.showinfo("Eliminar", "Seleccione una o más transferencias para eliminar.", parent=self.parent_frame)
            return
        if not messagebox.askyesno("Confirmar", f"¿Eliminar {len(sel)} transferencia(s) seleccionada(s) y sus comisiones?", parent=self.parent_frame):
            return
        ids = [int(i) for i in sel]
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=self.parent_frame)
            return
        try:
            with conn.cursor() as c:
                tx_soporte.limpiar_marcas(c, ids)
                c.execute("DELETE FROM transferencias_bancarias WHERE id = ANY(%s)", (ids,))
                conn.commit()
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", f"No se pudo eliminar:\n{e}", parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)
        if self.id_tx_edicion in ids:
            self._preparar_form_transferencia()
        registrar_auditoria(self.usuario_activo, "Banco", f"Eliminó {len(ids)} transferencia(s)")
        self.refrescar_transferencias()
        self.actualizar_saldos_boton()
        self.generar_reporte()
        messagebox.showinfo("Éxito", "Transferencia(s) eliminada(s) correctamente.", parent=self.parent_frame)

    # -----------------------------------------------------
    # TAB: CONCILIACION BANCARIA
    # -----------------------------------------------------
    def _cargar_conciliacion_cuando_haya_bancos(self, intentos=60):
        """Carga la conciliación del MES EN CURSO en cuanto los bancos están listos.

        Los bancos llegan de la base en segundo plano; si todavía no están, se
        reintenta cada 500 ms (hasta ~30 s). No depende de detectar la pestaña.
        """
        if not getattr(self, "bancos", None):
            if intentos > 0:
                try:
                    self.frame_main.after(500, lambda: self._cargar_conciliacion_cuando_haya_bancos(intentos - 1))
                except Exception:
                    pass
            return
        try:
            self.cmb_mes.set(mes_actual_etiqueta())      # 📅 mes en curso
        except Exception:
            pass
        try:
            self.generar_reporte()
        except Exception as e:
            print("Aviso - carga de conciliación:", e)

    def _vigilar_tab_conciliacion(self):
        """Detecta cuando se abre la pestaña de Conciliación: mes en curso + carga."""
        try:
            actual = str(self.tabview.get() or "")
        except Exception:
            actual = ""
        es_conc = "conciliaci" in actual.lower()
        anterior = getattr(self, "_tab_conc_visto", None)
        if es_conc and actual != anterior:
            if not getattr(self, "bancos", None):
                # ⏳ los bancos aún se están cargando: se reintenta en 500 ms
                pass
            else:
                self._tab_conc_visto = actual
                try:
                    self.cmb_mes.set(mes_actual_etiqueta())   # mes en curso
                except Exception:
                    pass
                try:
                    self.frame_main.after(80, self.generar_reporte)
                except Exception:
                    pass
        elif not es_conc:
            self._tab_conc_visto = None
        try:
            self.frame_main.after(500, self._vigilar_tab_conciliacion)
        except Exception:
            pass

    def _al_cambiar_tab_banco(self, valor=None):
        """Compatibilidad: carga los movimientos si la pestaña activa es Conciliación."""
        self._cargar_si_conciliacion()

    def _cargar_si_conciliacion(self):
        """Al abrir Conciliación: mes en curso y carga de los movimientos del sistema."""
        try:
            actual = str(self.tabview.get() or "").lower()
        except Exception:
            return
        if "conciliaci" not in actual:
            return
        try:
            self.cmb_mes.set(mes_actual_etiqueta())     # 📅 siempre el mes en curso
        except Exception:
            pass
        try:
            self.generar_reporte()
        except Exception as e:
            print("Aviso - carga de conciliación:", e)

    def construir_tab_conciliacion(self):
        f_filtro = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_filtro.pack(fill="x", padx=15, pady=(5, 0))
        self.var_solo_negativos = tk.BooleanVar(value=False)
        self.var_solo_positivos = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(f_filtro, text="Solo negativos", variable=self.var_solo_negativos,
                        font=(FAMILIA, 11),
                        command=lambda: self._aplicar_filtro_montos("negativos")).pack(side="left")
        ctk.CTkCheckBox(f_filtro, text="Solo positivos", variable=self.var_solo_positivos,
                        font=(FAMILIA, 11),
                        command=lambda: self._aplicar_filtro_montos("positivos")).pack(side="left", padx=(15, 0))
        f_sel = ctk.CTkFrame(self.tab_conciliacion, fg_color="#f8f9fa",
                             border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_sel.pack(fill="x", padx=5, pady=(5, 10))

        ctk.CTkLabel(f_sel, text="Banco a conciliar:", font=(FAMILIA, 12, "bold")).pack(side="left", padx=(12, 6), pady=10)
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        if not etiquetas:
            etiquetas = ["(Sin bancos configurados)"]
        self.cmb_banco = ctk.CTkComboBox(f_sel, values=etiquetas, width=320, state="readonly",
                                          command=lambda _v=None: self.generar_reporte())
        self.cmb_banco.pack(side="left", padx=6, pady=10)
        if etiquetas:
            self.cmb_banco.set(etiquetas[0])

        ctk.CTkLabel(f_sel, text="Mes a conciliar:", font=(FAMILIA, 12, "bold")).pack(side="left", padx=(20, 6), pady=10)
        valores_mes = construir_valores_mes()
        self.cmb_mes = ctk.CTkComboBox(f_sel, values=valores_mes, width=160, state="readonly",
                                         command=lambda _v=None: self.generar_reporte())
        self.cmb_mes.pack(side="left", padx=6, pady=10)
        self.cmb_mes.set(mes_actual_etiqueta())

        f_btns = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns.pack(fill="x", padx=5, pady=(0, 2))
        ctk.CTkButton(f_btns, text="📄 Subir PDF Estado de Cuenta", font=(FAMILIA, 12, "bold"),
                      fg_color="#e67e22", hover_color="#ca6f1e",
                      command=self.subir_pdf).pack(side="left", padx=4)
        # 📚 Lista de estados de cuenta ya guardados (banco, cuenta y período).
        ctk.CTkButton(f_btns, text="📚 Estados cargados", width=170, font=(FAMILIA, 12, "bold"),
                      fg_color="#7d6608", hover_color="#5b4a06",
                      command=self.mostrar_estados_cargados).pack(side="left", padx=4)
        # (El botón «Cargar Movimientos del Sistema» se quitó: la conciliación se
        #  recarga sola al cambiar banco/mes, al entrar a la pestaña y tras cada
        #  acción. Para forzar la lectura está F5.)
        ctk.CTkButton(f_btns, text="🧠 Conciliar inteligente", width=190, fg_color="#6c3483", hover_color="#4a235a",
                      command=self.abrir_conciliacion_inteligente).pack(side="left", padx=5)
        ctk.CTkButton(f_btns, text="✅ Conciliar Seleccionados", font=(FAMILIA, 12, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.conciliar_seleccionados).pack(side="left", padx=4)

        f_btns2 = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns2.pack(fill="x", padx=5, pady=(0, 8))
        ctk.CTkButton(f_btns2, text="➕ Registro de Ingresos / Egresos / Cruzadas", font=(FAMILIA, 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=self.agregar_movimiento_manual).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="🧾 Asentar ITF", font=(FAMILIA, 12, "bold"),
                      fg_color="#b7950b", hover_color="#9a7d0a",
                      command=self.asentar_itf).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="✏️ Editar / Corregir", font=(FAMILIA, 12, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=self.corregir_movimiento).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="🗑️ Eliminar", font=(FAMILIA, 12, "bold"),
                      fg_color="#c0392b", hover_color="#96281b",
                      command=self.eliminar_movimiento_conciliacion).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="♻️ Restaurar Eliminados", font=(FAMILIA, 12, "bold"),
                      fg_color="#16a085", hover_color="#117a65",
                      command=self.restaurar_ignorados).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="🗑️ Eliminar Ocultos", font=(FAMILIA, 12, "bold"),
                      fg_color="#922b21", hover_color="#641e16",
                      command=self.eliminar_ocultos_definitivo).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="💾 Exportar", font=(FAMILIA, 12, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.exportar_reporte).pack(side="left", padx=4)

        # 📊 Fila propia para las herramientas de control (antes se cortaban abajo).
        f_btns3 = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns3.pack(fill="x", padx=5, pady=(0, 4))
        ctk.CTkButton(f_btns3, text="📋 Faltantes / Sobrantes", width=210,
                      font=(FAMILIA, 12, "bold"), fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: self._mostrar_faltantes_banco(
                          self.banco_seleccionado(), [], self.movimientos_pdf)
                      ).pack(side="left", padx=4)
        ctk.CTkButton(f_btns3, text="🧹 Duplicados", width=150,
                      font=(FAMILIA, 12, "bold"), fg_color="#7d6608", hover_color="#5b4a06",
                      command=self.limpiar_duplicados).pack(side="left", padx=4)
        ctk.CTkButton(f_btns3, text="📊 Resumen del período", width=200,
                      font=(FAMILIA, 12, "bold"), fg_color="#117a65", hover_color="#0b5345",
                      command=self.mostrar_resumen).pack(side="left", padx=4)
        ctk.CTkButton(f_btns3, text="🔍 Diferencias por asiento", width=210,
                      font=(FAMILIA, 12, "bold"), fg_color="#8e44ad", hover_color="#6c3483",
                      command=self.mostrar_diferencias_importe).pack(side="left", padx=4)

        self.f_resumen = ctk.CTkFrame(self.tab_conciliacion, fg_color="#eaf2f8",
                                      border_width=1, border_color="#d4e6f1", corner_radius=8)
        self.f_resumen.pack(fill="x", padx=5, pady=(0, 8))
        self.lbl_resumen = ctk.CTkLabel(self.f_resumen, text="Seleccione un banco, suba el PDF del estado de cuenta "
                                                             "y cargue los movimientos del sistema.",
                                        font=(FAMILIA, 12), text_color="#1f538d", justify="left")
        self.lbl_resumen.pack(anchor="w", padx=12, pady=10)

        # 🔄 Los movimientos del sistema se cargan SOLOS al abrir la conciliación
        try:
            self.frame_main.after(600, self.generar_reporte)   # auto: movimientos del sistema
        except Exception:
            pass

        # ⚠️ Movimientos del estado de cuenta que NO están en el sistema
        self.f_faltantes = ctk.CTkFrame(self.tab_conciliacion, fg_color="#fdebd0",
                                        border_width=1, border_color="#f5cba7", corner_radius=8)
        self.f_faltantes.pack(fill="x", padx=5, pady=(0, 6))
        self.lbl_faltantes = ctk.CTkLabel(self.f_faltantes, text="", font=(FAMILIA, 12, "bold"),
                                          text_color="#a04000", justify="left")
        self.lbl_faltantes.pack(side="left", padx=12, pady=8)
        # 🧾 Se abre la ventana para ELEGIR, DESCRIBIR e incluir los diferenciales
        self.btn_faltantes = ctk.CTkButton(
            self.f_faltantes, text="🧾 Ver diferenciales…", width=190,
            font=(FAMILIA, 11, "bold"), fg_color="#e67e22", hover_color="#ca6f1e",
            command=self.abrir_diferenciales)
        self.btn_faltantes.pack(side="right", padx=(6, 12), pady=8)
        # 📊 Exportar los diferenciales a Excel (ventana actual o historial completo)
        self.btn_exportar_dif = ctk.CTkButton(
            self.f_faltantes, text="📊 Exportar a Excel", width=160,
            font=(FAMILIA, 11, "bold"), fg_color="#1e8449", hover_color="#196f3d",
            command=lambda: VentanaDiferenciales.exportar_desde_modulo(self))
        self.btn_exportar_dif.pack(side="right", padx=6, pady=8)

        f_buscar = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_buscar.pack(fill="x", padx=5, pady=(0, 4))
        ctk.CTkLabel(f_buscar, text="🔍 Buscar:", font=(FAMILIA, 12, "bold")).pack(side="left", padx=(4, 6))
        self.ent_buscar_conc = ctk.CTkEntry(
            f_buscar, placeholder_text="cualquier columna: fecha, monto, descripción, tipo…", width=460)
        self.ent_buscar_conc.pack(side="left")
        ctk.CTkButton(f_buscar, text="✖ Limpiar", width=90, font=(FAMILIA, 11),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.limpiar_busqueda_conc).pack(side="left", padx=8)
        self.ent_buscar_conc.bind("<KeyRelease>", lambda _e: self.filtrar_conciliacion_inline())

        f_tabla = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        columnas = ("estado", "fecha", "documento", "descripcion", "tipo", "monto", "origen", "saldo")
        self.tabla_conc = ttk.Treeview(f_tabla, columns=columnas, show="headings",
                                       selectmode="extended")
        preparar_colores_montos(self.tabla_conc)
        for col, txt, w, anc in (("estado", "Estado", 140, "center"),
                                 ("fecha", "Fecha", 95, "center"),
                                 ("documento", "N° Documento", 140, "center"),
                                 ("descripcion", "Descripción", 230, "w"),
                                 ("tipo", "Tipo", 100, "center"),
                                 ("monto", "Monto", 110, "e"),
                                 ("origen", "Origen", 115, "center"),
                                 ("saldo", "Saldo", 110, "e")):
            self.tabla_conc.heading(col, text=txt)
            self.tabla_conc.column(col, width=w, anchor=anc)
        self.columna_orden_conc = "fecha"
        self.orden_asc_conc = True
        for _col in columnas:
            self.tabla_conc.heading(_col, command=lambda c=_col: self.ordenar_conciliacion(c))
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla_conc.yview)
        hsb = ttk.Scrollbar(f_tabla, orient="horizontal", command=self.tabla_conc.xview)
        self.tabla_conc.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        self.tabla_conc.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        # ⌨️ En un teclado Mac la tecla de borrar envía <BackSpace> (el borrado
        #    hacia delante es fn+Delete). Se escucha SOLO en macOS: en Windows
        #    <BackSpace> es la tecla de retroceso y borraría sin querer.
        _teclas_borrar = ("<Delete>", "<BackSpace>") if sys.platform == "darwin" else ("<Delete>",)
        for _tecla_borrar in _teclas_borrar:
            self.tabla_conc.bind(_tecla_borrar,
                                 lambda _e: self.eliminar_movimiento_conciliacion())
        # Doble clic: abre el editor del movimiento (pago del módulo o corrección simple)
        self.tabla_conc.bind("<Double-1>", lambda _e: (
            self.corregir_movimiento() if self.tabla_conc.selection() else None))
        # 🖱️ Clic derecho (Windows/Linux: botón 3 · macOS: botón 2 o Control+clic)
        self.tabla_conc.bind("<Button-3>", lambda e: self._menu_contextual_movimiento(
            e, self.tabla_conc, self.filas_conciliacion))
        self.tabla_conc.bind("<Button-2>", lambda e: self._menu_contextual_movimiento(
            e, self.tabla_conc, self.filas_conciliacion))
        self.tabla_conc.bind("<Control-Button-1>", lambda e: self._menu_contextual_movimiento(
            e, self.tabla_conc, self.filas_conciliacion))
        self.tabla_conc.tag_configure("conciliado", background="#d5f5e3")
        self.tabla_conc.tag_configure("pendiente", background="#fef9e7")
        self.tabla_conc.tag_configure("diferencia", background="#fadbd8")
        self.tabla_conc.tag_configure("estado_cuenta", background="#d6eaf8")
        # Asiento de un mes SIN estado de cuenta cargado (mes en curso): gris.
        self.tabla_conc.tag_configure("sin_estado", background="#eaecee")

        f_pie = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_pie.pack(fill="x", padx=5)
        ctk.CTkButton(f_pie, text="👁 Ver Texto Extraído del PDF", width=220, font=(FAMILIA, 11),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.mostrar_texto_pdf).pack(side="left")
        # (Los botones Faltantes / Duplicados / Resumen están en la fila de abajo:
        #  acá no entraban y se veían cortados.)
        # 🎨 Leyenda de colores: el color indica el estado de CONCILIACIÓN,
        #    no si está pagado o cancelado (eso se ve en el módulo de Compras).
        #    Se usan cuadraditos de texto («■») con color propio: los emojis de
        #    color 🟩🟥🟦 no se dibujan en todas las fuentes de Windows.
        for _color, _texto in (("#2874a6", "Manual pendiente"),
                               ("#c0392b", "Falta conciliar"),
                               ("#27ae60", "Conciliado")):
            ctk.CTkLabel(f_pie, text="■ " + _texto, font=(FAMILIA, 11),
                         text_color=_color).pack(side="right", padx=10)

        # 🔄 Carga inicial de la conciliación (mes en curso) en cuanto haya bancos
        try:
            self.frame_main.after(600, self._cargar_conciliacion_cuando_haya_bancos)
        except Exception:
            pass

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

    # =========================================================
    # 🧠 CONCILIACIÓN INTELIGENTE (pantalla de revisión)
    #    Motor: conciliacion_inteligente.py
    #    Nada se concilia solo: aquí se APRUEBA o RECHAZA.
    # =========================================================
    def abrir_conciliacion_inteligente(self):
        """Analiza el estado de cuenta cargado y muestra las propuestas."""
        try:
            import conciliacion_inteligente as ci
        except Exception as error:
            messagebox.showerror("Conciliación inteligente",
                                 "No se encontró el motor conciliacion_inteligente.py\n%s"
                                 % error, parent=self.parent_frame)
            return
        banco = self.banco_seleccionado()
        if not banco:
            messagebox.showinfo("Conciliación inteligente",
                                "Primero seleccione el banco a conciliar.",
                                parent=self.parent_frame)
            return
        movs = getattr(self, "movimientos_pdf", None) or []
        if not movs:
            try:
                movs = self._cargar_movimientos_pdf(
                    banco, mes_etiqueta_a_key(self.cmb_mes.get())) or []
            except Exception:
                movs = []
        if not movs:
            messagebox.showinfo("Conciliación inteligente",
                                "Primero suba el Estado de Cuenta (PDF).",
                                parent=self.parent_frame)
            return
        ventana = ctk.CTkToplevel(self.parent_frame)
        ventana.title("🧠 Conciliación inteligente · revisar propuestas")
        ventana.geometry("1250x640")
        ventana.resizable(True, True)
        try:
            ventana.transient(self.parent_frame)
        except Exception:
            pass
        traer_al_frente(ventana)

        barra = ctk.CTkFrame(ventana)
        barra.pack(fill="x", padx=10, pady=8)
        ctk.CTkLabel(barra, text="Movimiento del banco", font=(FAMILIA, 12, "bold")).pack(side="left", padx=6)
        ctk.CTkLabel(barra, text="   ·   ").pack(side="left")
        ctk.CTkLabel(barra, text="Días de desfase:", font=(FAMILIA, 11)).pack(side="left", padx=(16, 4))
        ent_dias = ctk.CTkEntry(barra, width=60)
        # 5 días por defecto; se recuerda el último valor usado en esta sesión
        try:
            dias_guardados = int(getattr(self, "_dias_desfase", 5) or 5)
        except Exception:
            dias_guardados = 5
        ent_dias.insert(0, str(dias_guardados))
        ent_dias.pack(side="left")
        ctk.CTkLabel(barra, text="(días después de la fecha contable)",
                     font=(FAMILIA, 10), text_color="#7f8c8d").pack(side="left", padx=4)
        # ⚡ Coincidencia 100 % (importe exacto y UN solo candidato): se concilia
        #    sola. La casilla permite apagarlo en cualquier momento.
        chk_auto = ctk.CTkCheckBox(barra, text="Conciliar los de 100%",
                                   font=(FAMILIA, 11))
        chk_auto.select()
        chk_auto.pack(side="left", padx=(18, 4))

        marco = ctk.CTkFrame(ventana)
        marco.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        columnas = ("fecha_banco", "desc_banco", "monto_banco", "fecha_cont", "desc_cont",
                    "monto_sistema", "dias", "estado", "confianza")
        tabla = ttk.Treeview(marco, columns=columnas, show="headings", height=18)
        preparar_colores_montos(tabla)
        titulos = {"fecha_banco": ("Fecha banco", 100), "desc_banco": ("Descripción banco", 250),
                   "monto_banco": ("Monto banco", 100), "fecha_cont": ("Fecha contable", 100),
                   "desc_cont": ("Descripción contable", 250), "monto_sistema": ("Monto sistema", 100),
                   "dias": ("Días", 50), "estado": ("Estado", 180), "confianza": ("Confianza", 80)}
        for col in columnas:
            texto, ancho = titulos[col]
            tabla.heading(col, text=texto)
            tabla.column(col, width=ancho, anchor="center" if col in ("dias", "confianza", "importe") else "w")
        desplaz = ttk.Scrollbar(marco, orient="vertical", command=tabla.yview)
        tabla.configure(yscrollcommand=desplaz.set)
        tabla.pack(side="left", fill="both", expand=True)
        desplaz.pack(side="right", fill="y")

        estado_ventana = {"propuestas": [], "aplicadas": set()}

        def _cargar_propuestas():
            """Lanza el cálculo en un HILO y pinta cuando termina.

            El cálculo (sincronizar asientos + proponer) tarda varios segundos: si
            corriera en el hilo de la pantalla, la barra quedaría congelada. Ahora
            la barra se anima sola mientras el hilo trabaja.
            """
            for fila in tabla.get_children():
                tabla.delete(fila)
            dias = 5
            try:
                dias = int(str(ent_dias.get()).strip() or 5)
            except Exception:
                dias = 5
            self._dias_desfase = dias
            movimientos = [{"id": m.get("id") or i + 1, "banco": banco.get("banco", ""),
                            "cuenta": banco.get("cuenta", ""),
                            "fecha": m.get("fecha"), "descripcion": m.get("descripcion"),
                            "monto": m.get("monto"), "pagina": m.get("pagina")}
                           for i, m in enumerate(movs)]
            # 📅 El período se lee del combo AQUÍ (hilo principal) y se pasa al hilo.
            try:
                _desde_h, _hasta_h = self._periodo_de_conciliacion()
            except Exception:
                _desde_h = _hasta_h = None

            estado_ventana["animando"] = True
            _pulso()

            def _trabajo(estado):
                try:
                    estado["asientos"] = self._asientos_para_conciliar(
                        banco, _desde_h, _hasta_h)
                    estado["propuestas"] = ci.proponer(
                        movimientos, estado["asientos"], ventana_dias=dias)
                except Exception as error:
                    estado["error"] = str(error)

            ejecutar_en_hilo(ventana, _trabajo,
                             al_terminar=lambda est: _pintar(est, movimientos, dias))

        def _pulso():
            """Mueve la barra del letrero mientras el hilo trabaja."""
            try:
                barra = estado_ventana.get("barra")
                aviso = estado_ventana.get("aviso")
                if barra is None or aviso is None or not estado_ventana.get("animando"):
                    return
                valor = estado_ventana.get("pulso", 0.1) + 0.03
                if valor > 0.9:
                    valor = 0.1
                estado_ventana["pulso"] = valor
                barra.set(valor)
                aviso.after(110, _pulso)
            except Exception:
                pass

        def _cerrar_letrero(mensaje=None):
            """Cierra el letrero de «Analizando» y muestra el aviso pendiente."""
            estado_ventana["animando"] = False
            try:
                if aviso is not None:
                    texto_final = mensaje or mensaje_final[0]
                    if texto_final:
                        try:
                            lbl_aviso.configure(text=texto_final,
                                                text_color="#c0392b" if mensaje else "#1e8449")
                            barra_aviso.set(1)
                            aviso.update_idletasks()
                            aviso.after(1500, aviso.destroy)   # se cierra solo
                        except Exception:
                            aviso.destroy()
                    else:
                        aviso.destroy()
            except Exception:
                pass
            finally:
                estado_ventana["aviso"] = None
                estado_ventana["barra"] = None
                estado_ventana["pulso"] = 0.1
            texto_vacio = estado_ventana.pop("mensaje_vacio", "")
            if texto_vacio:
                try:
                    messagebox.showinfo("Conciliación inteligente", texto_vacio,
                                        parent=ventana)
                except Exception:
                    pass

        def _pintar(estado, movimientos, dias):
            """Dibuja el resultado (siempre en el hilo principal)."""
            estado_ventana["animando"] = False
            asientos = estado.get("asientos") or []
            propuestas = estado.get("propuestas") or []
            if estado.get("error"):
                _cerrar_letrero(mensaje="❌ No se pudo analizar: %s" % estado["error"])
                return
            propuestas = [p for p in propuestas
                          if (str(p.get("id_asiento")), str(p.get("fecha_banco")),
                              round(float(p.get("importe_banco") or 0), 2))
                          not in estado_ventana["aplicadas"]]
            # ⚡ COINCIDENCIA 100 % (importe exacto + un único candidato): se concilia
            #    sola, como pide el usuario. Queda registrada con origen_decision
            #    = 'auto' en la auditoría, así se distingue de lo aprobado a mano y
            #    se puede revertir desde 🕘 Historial. La casilla lo desactiva.
            aplicadas_auto = 0
            try:
                auto_activo = bool(chk_auto.get())
            except Exception:
                auto_activo = True
            if auto_activo:
                for p in list(propuestas):
                    try:
                        if float(p.get("confianza") or 0) < 100:
                            continue
                        if self._aplicar_conciliacion_inteligente(banco, p, "APROBADO",
                                                                  origen="auto"):
                            aplicadas_auto += 1
                            estado_ventana["aplicadas"].add(
                                (str(p.get("id_asiento")), str(p.get("fecha_banco")),
                                 round(float(p.get("importe_banco") or 0), 2)))
                    except Exception as error:
                        print("[Conciliación] automática:", error)
                if aplicadas_auto:
                    propuestas = [p for p in propuestas
                                  if (str(p.get("id_asiento")), str(p.get("fecha_banco")),
                                      round(float(p.get("importe_banco") or 0), 2))
                                  not in estado_ventana["aplicadas"]]
                    print("[Conciliación] conciliadas automáticamente: %d" % aplicadas_auto)
            estado_ventana["propuestas"] = propuestas
            try:
                mensaje_final[0] = (
                    ("✅ %d conciliada(s) automáticamente (100%%) · %d para revisar"
                     % (aplicadas_auto, len(propuestas))) if aplicadas_auto else
                    ("✅ %d propuesta(s) para revisar y aprobar" % len(propuestas)))
            except Exception:
                pass
            if not propuestas:
                # 📅 Se muestra el rango de fechas de cada lado: si no coinciden,
                #    se entiende al instante que se está comparando contra otro mes.
                def _rango(fechas):
                    validas = sorted(normalizar_fecha(f) for f in fechas
                                     if normalizar_fecha(f))
                    if not validas:
                        return ""
                    return " (%s al %s)" % (validas[0], validas[-1])

                rango_pdf = _rango([m.get("fecha") for m in movimientos])
                rango_asi = _rango([a.get("fecha") for a in asientos])
                # ⏳ El mensaje se muestra DESPUÉS de cerrar el letrero de «Analizando»
                #    (si no, quedaba visible detrás del cartel).
                estado_ventana["mensaje_vacio"] = (
                    "No hay propuestas para este banco.\n\n"
                    "• Estado de cuenta: %d movimiento(s)%s\n"
                    "• Asientos pendientes: %d%s\n\n"
                    "%s" % (len(movimientos), rango_pdf, len(asientos), rango_asi,
                            "Todos los asientos de este banco ya figuran como CONCILIADOS."
                            if asientos == [] else
                            "Ninguno coincide en importe y fecha (máximo %d días).\n"
                            "Si los pendientes son de otro mes, suba el estado de cuenta "
                            "de ESE mes y elija ese período." % dias))
            for indice, p in enumerate(propuestas):
                tabla.insert("", "end", iid=str(indice + 1), values=(
                    str(p["fecha_banco"])[:10], str(p["descripcion_banco"])[:55],
                    formatear_monto(p["importe_banco"]), str(p["fecha_contable"])[:10],
                    str(p["descripcion_contable"])[:55],
                    formatear_monto(p["importe_contable"]), p["dias_desfase"],
                    p["estado"] if not p.get("rivales")
                    else "%s · %d candidatos" % (p["estado"], p.get("candidatos") or 2),
                    "%d%%" % p["confianza"]))
            self._resumen_revision = ci.resumen(propuestas, movimientos, asientos)
            _cerrar_letrero()

        def _aplicar_varias(decision):
            seleccion = tabla.selection()
            if not seleccion:
                messagebox.showinfo("Conciliación",
                                    "Seleccione una o más filas (Ctrl+clic o Shift+clic).",
                                    parent=ventana)
                return
            aplicadas = 0
            for iid in seleccion:
                try:
                    propuesta = estado_ventana["propuestas"][int(iid) - 1]
                except Exception:
                    continue
                if self._aplicar_conciliacion_inteligente(banco, propuesta, decision):
                    aplicadas += 1
                    try:
                        estado_ventana["aplicadas"].add(
                            (str(propuesta.get("id_asiento")), str(propuesta.get("fecha_banco")),
                             round(float(propuesta.get("importe_banco") or 0), 2)))
                    except Exception:
                        pass
            if aplicadas:
                messagebox.showinfo(
                    "Conciliación",
                    "%d movimiento(s) %s." % (
                        aplicadas,
                        "conciliados y registrados en la auditoría"
                        if decision == "APROBADO" else "rechazados"),
                    parent=ventana)
            _cargar_propuestas()

        def _aprobar():
            _aplicar_varias("APROBADO")

        def _rechazar():
            _aplicar_varias("RECHAZADO")

        def _ver_candidatos():
            """Muestra los asientos que compiten por la línea seleccionada y deja elegir."""
            seleccion = tabla.selection()
            if len(seleccion) != 1:
                messagebox.showinfo("Conciliación",
                                    "Seleccione UNA fila para ver sus candidatos.",
                                    parent=ventana)
                return
            try:
                propuesta = estado_ventana["propuestas"][int(seleccion[0]) - 1]
            except Exception:
                return
            detalle = list(propuesta.get("candidatos_detalle") or [])
            if len(detalle) < 2:
                messagebox.showinfo("Conciliación",
                                    "Esta fila tiene un solo candidato:\n"
                                    "se puede aprobar directo.", parent=ventana)
                return
            dlg = ctk.CTkToplevel(ventana)
            dlg.title("Candidatos · %s del %s"
                      % (formatear_moneda(propuesta.get("importe_banco")),
                         str(propuesta.get("fecha_banco"))[:10]))
            dlg.geometry("860x390")
            try:
                dlg.transient(ventana)
            except Exception:
                pass
            traer_al_frente(dlg)
            ctk.CTkLabel(dlg, text="Movimiento del banco", font=(FAMILIA, 12, "bold")).pack(
                anchor="w", padx=12, pady=(10, 0))
            ctk.CTkLabel(dlg, text="%s   ·   %s   ·   %s"
                         % (str(propuesta.get("fecha_banco"))[:10],
                            formatear_moneda(propuesta.get("importe_banco")),
                            str(propuesta.get("descripcion_banco"))[:70]),
                         font=(FAMILIA, 11), text_color="#34495e").pack(
                anchor="w", padx=12, pady=(0, 8))
            columnas_c = ("fecha", "descripcion", "dias", "origen")
            tabla_c = ttk.Treeview(dlg, columns=columnas_c, show="headings", height=7)
            preparar_colores_montos(tabla_c)
            for col, titulo, ancho in (("fecha", "Fecha contable", 110),
                                       ("descripcion", "Descripción contable", 520),
                                       ("dias", "Días", 50),
                                       ("origen", "Origen", 130)):
                tabla_c.heading(col, text=titulo)
                tabla_c.column(col, width=ancho)
            tabla_c.pack(fill="both", expand=True, padx=12)
            for i, cand in enumerate(detalle):
                tabla_c.insert("", "end", iid=str(i),
                               values=(str(cand.get("fecha"))[:10],
                                       str(cand.get("descripcion"))[:80],
                                       cand.get("dias"),
                                       str(cand.get("tabla") or cand.get("id_asiento"))))

            def _elegir():
                sel = tabla_c.selection()
                if not sel:
                    messagebox.showinfo("Conciliación", "Seleccione un candidato.",
                                        parent=dlg)
                    return
                elegido = detalle[int(sel[0])]
                nueva = dict(propuesta)
                nueva["id_asiento"] = elegido.get("id_asiento")
                nueva["descripcion_contable"] = elegido.get("descripcion")
                nueva["estado"] = "CONCILIADO EXACTO"
                nueva["confianza"] = 100
                if self._aplicar_conciliacion_inteligente(banco, nueva, "APROBADO"):
                    estado_ventana["aplicadas"].add(
                        (str(nueva.get("id_asiento")), str(nueva.get("fecha_banco")),
                         round(float(nueva.get("importe_banco") or 0), 2)))
                    dlg.destroy()
                    _cargar_propuestas()

            ctk.CTkButton(dlg, text="✅ Conciliar con este", width=200, fg_color="#1e8449",
                          hover_color="#145a32", command=_elegir).pack(pady=10)

        pie = ctk.CTkFrame(ventana)
        pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(pie, text="✅ Aprobar", width=140, fg_color="#1e8449",
                      hover_color="#145a32", command=_aprobar).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="✖ Rechazar", width=140, fg_color="#922b21",
                      hover_color="#641e16", command=_rechazar).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="🔎 Candidatos", width=132, fg_color="#5b2c6f",
                      hover_color="#3d1c4a",
                      command=_ver_candidatos).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="🔄 Recalcular", width=140, fg_color="#1f538d",
                      hover_color="#163b65", command=_cargar_propuestas).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="📊 Reporte", width=130, fg_color="#b9770e",
                      hover_color="#7e5109",
                      command=lambda: self._mostrar_reporte_conciliacion(
                          estado_ventana["propuestas"], movs,
                          self._asientos_para_conciliar(banco),
                          int(str(ent_dias.get()).strip() or 5))).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="📉 Saldos", width=120, fg_color="#1f538d",
                      hover_color="#163b65",
                      command=lambda: self._mostrar_validacion_saldos(
                          banco, estado_ventana["propuestas"])).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="📋 Faltantes", width=130, fg_color="#7e5109",
                      hover_color="#5b4a06",
                      command=lambda: self._mostrar_faltantes_banco(
                          banco, estado_ventana["propuestas"], movs)).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="🕘 Historial", width=130, fg_color="#7d6608",
                      hover_color="#5b4a06",
                      command=lambda: self._historial_conciliaciones(banco)).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=ventana.destroy).pack(side="right", padx=6)
        # ⏳ indicador mientras analiza (la primera carga tarda por la sincronización)
        aviso = None
        mensaje_final = [""]
        try:
            try:
                ventana.update_idletasks()
                ventana.update()
            except Exception:
                pass
            aviso = ctk.CTkToplevel(ventana)
            try:
                aviso.attributes("-topmost", True)
            except Exception:
                pass
            aviso.title("Conciliación inteligente")
            aviso.geometry("420x120")
            try:
                aviso.transient(ventana)
                aviso.resizable(False, False)
                x = ventana.winfo_rootx() + (ventana.winfo_width() // 2) - 210
                y = ventana.winfo_rooty() + (ventana.winfo_height() // 2) - 60
                aviso.geometry("+%d+%d" % (max(0, x), max(0, y)))
            except Exception:
                pass
            lbl_aviso = ctk.CTkLabel(aviso, text="⏳ Analizando movimientos del banco…",
                                     font=(FAMILIA, 14, "bold"))
            lbl_aviso.pack(pady=(22, 6))
            barra_aviso = ctk.CTkProgressBar(aviso, width=350)
            barra_aviso.pack(pady=(0, 16))
            barra_aviso.set(0.1)
            aviso.update_idletasks()
            # 🔗 Se guardan las referencias para que el análisis pueda ir moviendo la barra.
            estado_ventana["aviso"] = aviso
            estado_ventana["barra"] = barra_aviso
            aviso.update_idletasks()
            aviso.update()
            # 🔝 forzar el dibujado ANTES del trabajo pesado (Tk no pinta si el
            #    hilo principal se queda calculando)
            try:
                import time as _t
                aviso.lift()
                aviso.attributes("-topmost", True)
                aviso.focus_force()
                for _pasada in range(6):
                    aviso.update_idletasks()
                    aviso.update()
                    _t.sleep(0.05)
            except Exception as error:
                print("[Conciliación] dibujar letrero:", error)
        except Exception as error:
            print("[Conciliación] indicador:", error)
            aviso = None
        # 🚀 El cálculo va en un hilo: _pintar() cierra el letrero al terminar.
        _cargar_propuestas()

    def _buscar_duplicados(self, banco, mes=None):
        """Asientos MANUALES que copian una línea del banco ya cubierta.

        Antes se agrupaba por (fecha, importe exacto), así que no veía los casos
        en que el sistema registra el principal y el manual el total con comisión
        (1,200.00 vs 1,204.80). Ahora se empareja cada asiento con su línea real
        del estado de cuenta y se marca el manual que sobra.

        Devuelve grupos con 'fecha', 'monto', 'asientos' (tuplas de la base) y
        'lineas'. Sólo se proponen manuales: nunca se borra un asiento del sistema.
        """
        if mes:
            try:
                lineas = self._cargar_movimientos_pdf(banco, mes) or []
            except Exception as error:
                print("[Conciliación] líneas para duplicados:", error)
                lineas = []
        else:
            try:
                lineas = self._cargar_movimientos_pdf(banco) or []
            except Exception as error:
                print("[Conciliación] líneas para duplicados:", error)
                lineas = []
        if not lineas:
            return []           # sin estado de cuenta no hay contra qué comparar

        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        try:
            with conn.cursor() as c:
                # 📄 Se trae el NÚMERO DE FACTURA de cada asiento: dos facturas con
                #    el mismo importe y fecha pero número distinto NO son duplicados.
                c.execute("""SELECT b.id, b.fecha, b.monto, COALESCE(b.origen,''),
                                    COALESCE(b.estado,''), COALESCE(b.descripcion,''),
                                    UPPER(TRIM(COALESCE(f.numero_documento,
                                                        f2.numero_documento, ''))),
                                    COALESCE(b.tabla,''), COALESCE(b.id_movimiento,0)
                             FROM conciliacion_bancaria b
                             LEFT JOIN pagos_comprobantes p
                                    ON (b.tabla = 'pagos_comprobantes' AND p.id = b.id_movimiento)
                             LEFT JOIN facturas_recibidas f
                                    ON f.id = NULLIF(p.id_factura, 0)
                             LEFT JOIN facturas_recibidas f2
                                    ON f2.id = NULLIF(b.id_gasto_compras, 0)
                             WHERE UPPER(COALESCE(b.banco,'')) = UPPER(%s)
                               AND COALESCE(b.cuenta,'') = %s""",
                          (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                filas = c.fetchall()
        except Exception as error:
            print("[Conciliación] buscar duplicados:", error)
            return []
        finally:
            liberar_conexion(conn)

        asientos = []
        sistema_en_tabla = set()
        sistema_claves = set()
        for fila in filas:
            if mes and not clave_fecha(fila[1]).startswith(mes):
                continue
            origen = str(fila[3] or "")
            if origen.startswith("s:"):
                sistema_en_tabla.add((str(fila[7] or ""), int(fila[8] or 0)))
                sistema_claves.add((clave_fecha(fila[1]),
                                    round(float(fila[2] or 0), 2)))
            asientos.append({
                "id": fila[0], "fecha": fila[1], "monto": float(fila[2] or 0),
                "origen": origen, "estado": str(fila[4] or ""),
                "descripcion": str(fila[5] or ""), "numero": str(fila[6] or ""),
                "_prioridad": self._prioridad_asiento(origen, fila[4]),
                "_raw": fila,
            })
        # 🧩 La tabla conciliacion_bancaria no siempre tiene TODOS los pagos del
        #    sistema (los que nunca se conciliaron). Se agregan para comparar
        #    contra el banco igual que hace el Resumen; nunca se borran.
        try:
            for s in (self._movimientos_de_banco(banco, mes=mes) or []):
                if s.get("espejo_banco"):
                    continue
                tabla = str(s.get("tabla") or "")
                idm = int(s.get("id") or 0)
                if (tabla, idm) in sistema_en_tabla:
                    continue
                clave = (clave_fecha(s.get("fecha")),
                         round(float(s.get("monto") or 0), 2))
                if clave in sistema_claves:
                    continue
                origen_s = "s:%s" % (tabla or "sistema")
                raw = (0, s.get("fecha", ""), float(s.get("monto") or 0),
                       origen_s, "conciliado", s.get("descripcion", ""),
                       str(s.get("documento") or ""), tabla, idm)
                asientos.append({
                    "id": 0, "fecha": s.get("fecha", ""),
                    "monto": float(s.get("monto") or 0), "origen": origen_s,
                    "estado": "conciliado",
                    "descripcion": str(s.get("descripcion") or ""),
                    "numero": str(s.get("documento") or ""),
                    "_prioridad": self._prioridad_asiento(origen_s, "conciliado"),
                    "_raw": raw,
                })
        except Exception as error:
            print("[Conciliación] sistema para duplicados:", error)
        try:
            comision = cargar_comision_interbancaria()
        except Exception:
            comision = 0.0
        match, duplicados, _sobrantes, _faltantes = emparejar_asientos_banco(
            asientos, lineas, comision)
        principal_por_linea = {j: i for i, j in match.items()}

        grupos = {}
        for i, j in duplicados:
            asiento = asientos[i]
            # 🔒 Sólo se propone borrar manuales. Los asientos del sistema y los
            #    del estado de cuenta (e) nunca se tocan.
            if asiento["origen"] != "manual":
                continue
            linea = lineas[j]
            pi = principal_por_linea.get(j)
            principal = asientos[pi] if pi is not None else None
            if not _es_duplicado_manual_claro(asiento, linea, principal, comision):
                continue
            grupo = grupos.setdefault(j, {"linea": linea, "asientos": []})
            if principal is not None and principal not in grupo["asientos"]:
                grupo["asientos"].insert(0, principal)
            if asiento not in grupo["asientos"]:
                grupo["asientos"].append(asiento)

        resultado = []
        for j, grupo in grupos.items():
            crudos = [a["_raw"] for a in grupo["asientos"]]
            if not any(str(a[3] or "") == "manual" for a in crudos):
                continue
            linea = grupo["linea"]
            resultado.append({
                "fecha": clave_fecha(linea.get("fecha")),
                "monto": abs(float(linea.get("monto") or 0)),
                "asientos": crudos,
                "fechas": {clave_fecha(a.get("fecha")) for a in grupo["asientos"]},
                "lineas": 1,
            })
        return sorted(resultado, key=lambda g: (g["fecha"], -g["monto"]))

    def _quitar_de_respaldos(self, banco, fecha, monto):
        """Borra de los archivos respaldo_*.json las entradas de ese movimiento.

        Los respaldos que guarda el módulo (respaldo_*.json) también se limpian:
        si el movimiento se elimina, no debe quedar ni en la base ni en el JSON.
        """
        try:
            import glob
            from app_paths import DATA_DIR as _DATA
        except Exception:
            import glob
            _DATA = None
        carpetas = {os.path.dirname(os.path.abspath(__file__))}
        try:
            if _DATA:
                carpetas.add(str(_DATA))
        except Exception:
            pass
        _fecha_iso = normalizar_fecha(fecha)
        _fecha_dma = fecha_para_mostrar(fecha)
        try:
            _monto = abs(float(monto or 0))
        except Exception:
            _monto = 0.0

        def _es_el_mismo(item):
            if not isinstance(item, dict):
                return False
            try:
                if abs(abs(float(item.get("monto") or item.get("importe_banco") or 0)) - _monto) > 0.02:
                    return False
            except Exception:
                return False
            fecha_item = normalizar_fecha(item.get("fecha") or item.get("fecha_banco")
                                          or item.get("fecha_pago") or "")
            return fecha_item in (_fecha_iso, normalizar_fecha(_fecha_dma))

        for carpeta in carpetas:
            for ruta_json in glob.glob(os.path.join(carpeta, "respaldo_*.json")):
                try:
                    with open(ruta_json, "r", encoding="utf-8") as archivo:
                        contenido = json.load(archivo)
                except Exception:
                    continue
                cambiado = False
                # 🧹 Si el respaldo ya estaba vacío, se borra el archivo y listo.
                if ((isinstance(contenido, list) and not contenido)
                        or (isinstance(contenido, dict)
                            and not any(v for v in contenido.values() if v))):
                    try:
                        os.remove(ruta_json)
                        print("[Conciliación] respaldo vacío eliminado:",
                              os.path.basename(ruta_json))
                    except Exception as error:
                        print("[Conciliación] no se pudo borrar el respaldo vacío:", error)
                    continue
                if isinstance(contenido, list):
                    nuevo = [x for x in contenido if not _es_el_mismo(x)]
                    if len(nuevo) != len(contenido):
                        contenido = nuevo
                        cambiado = True
                elif isinstance(contenido, dict):
                    for clave, valor in list(contenido.items()):
                        if isinstance(valor, list):
                            nuevo = [x for x in valor if not _es_el_mismo(x)]
                            if len(nuevo) != len(valor):
                                contenido[clave] = nuevo
                                cambiado = True
                if cambiado:
                    # 🧹 Si el respaldo quedó vacío, se borra el archivo directamente.
                    _vacio = ((isinstance(contenido, list) and not contenido)
                              or (isinstance(contenido, dict)
                                  and not any(v for v in contenido.values() if v)))
                    try:
                        if _vacio:
                            os.remove(ruta_json)
                            print("[Conciliación] respaldo vacío eliminado:",
                                  os.path.basename(ruta_json))
                        else:
                            with open(ruta_json, "w", encoding="utf-8") as archivo:
                                json.dump(contenido, archivo, ensure_ascii=False, indent=1)
                            print("[Conciliación] entrada quitada del respaldo:",
                                  os.path.basename(ruta_json))
                    except Exception as error:
                        print("[Conciliación] no se pudo limpiar el respaldo:", error)

    def _prioridad_asiento(self, origen, estado):
        """Qué asiento se CONSERVA en un grupo duplicado (menor = se queda).

        Se conserva el que viene del SISTEMA (pago real), después el que vino del
        estado de cuenta, y por último el manual que se cargó desde el módulo.
        """
        origen = str(origen or "")
        if origen == "s:pagos_comprobantes":
            return 0
        if origen.startswith("s:"):
            return 1
        if origen == "e":
            return 2
        return 3

    def limpiar_duplicados(self):
        """🧹 Quita los asientos sobrantes de cada grupo duplicado.

        Deja UN asiento por (fecha, importe) — priorizando el del sistema — y borra
        el resto. Antes de borrar guarda un respaldo JSON en la carpeta del sistema.
        """
        banco = self.banco_seleccionado() if hasattr(self, "banco_seleccionado") else None
        if not banco:
            messagebox.showerror("Duplicados", "Elija un banco primero.",
                                 parent=self.parent_frame)
            return
        mes = mes_etiqueta_a_key(self.cmb_mes.get()) if hasattr(self, "cmb_mes") else ""
        grupos = self._buscar_duplicados(banco, mes or None)
        if not grupos:
            messagebox.showinfo("Duplicados",
                                "✅ No hay duplicados: cada línea tiene su asiento.",
                                parent=self.parent_frame)
            return
        detalle = []
        a_borrar = []
        for grupo in grupos:
            ordenados = sorted(
                grupo["asientos"],
                key=lambda a: (self._prioridad_asiento(a[3], a[4]),
                               0 if clave_fecha(a[1]) in grupo.get("fechas", set()) else 1,
                               a[0]))
            # Se conservan tantos asientos como líneas tenga el estado de cuenta
            # (mínimo 1). Antes se conservaba uno solo y se borraban de más.
            cuantos = max(1, int(grupo["lineas"]))
            conservar = ordenados[0]
            sobran = ordenados[cuantos:]
            detalle.append("   %s  %10s  línea(s): %d · asientos: %d"
                           % (grupo["fecha"], formatear_monto(grupo["monto"]),
                              grupo["lineas"], len(grupo["asientos"])))
            for sobra in sobran:
                # 🔒 Nunca se borra un asiento del SISTEMA (s:*) ni uno del estado
                #    de cuenta (e): sólo los manuales que duplican a otro.
                if str(sobra[3] or "") != "manual":
                    continue
                a_borrar.append({"grupo": grupo, "conservar": conservar, "borrar": sobra})
        if not messagebox.askyesno(
                "Limpiar duplicados",
                "Se encontraron %d grupo(s) con asientos de más.\n"
                "Se va a conservar el asiento DEL SISTEMA y borrar el resto.\n\n"
                "%s\n\n¿Continuar? (se guarda un respaldo antes)"
                % (len(grupos), "\n".join(detalle[:12])),
                parent=self.parent_frame):
            return
        respaldo = {"creado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "banco": banco.get("banco", ""), "cuenta": banco.get("cuenta", ""),
                    "grupos": [{"fecha": g["fecha"], "monto": g["monto"],
                                "conservar": {"id": g["asientos"][0][0]},
                                "asientos": [{"id": a[0], "fecha": a[1],
                                              "monto": float(a[2] or 0), "origen": a[3],
                                              "estado": a[4], "descripcion": a[5]}
                                             for a in g["asientos"]]}
                               for g in grupos]}
        ruta_respaldo = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "respaldo_duplicados_%s.json" % datetime.now().strftime("%Y-%m-%d_%H%M"))
        try:
            with open(ruta_respaldo, "w", encoding="utf-8") as archivo:
                json.dump(respaldo, archivo, ensure_ascii=False, indent=1)
        except Exception as error:
            print("[Conciliación] respaldo duplicados:", error)
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        borrados = 0
        try:
            with conn.cursor() as c:
                for item in a_borrar:
                    sobra = item["borrar"]
                    c.execute("DELETE FROM conciliacion_bancaria WHERE id = %s", (sobra[0],))
                    borrados += c.rowcount
                    c.execute("""INSERT INTO conciliacion_auditoria
                        (banco, cuenta, fecha_banco, descripcion_banco, importe_banco,
                         sentido, estado, confianza, reglas, observacion, origen_decision,
                         creado, activo)
                        VALUES (%s,%s,%s,%s,%s,%s,'DUPLICADO_BORRADO',100,%s,%s,'auto',%s,TRUE)""",
                              (banco.get("banco", ""), banco.get("cuenta", ""),
                               str(sobra[1] or ""), str(sobra[5] or ""), float(sobra[2] or 0),
                               "ingreso" if float(sobra[2] or 0) > 0 else "egreso",
                               "se conserva el asiento id=%s" % item["conservar"][0],
                               "duplicado en conciliación",
                               datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
        except Exception as error:
            print("[Conciliación] limpiar duplicados:", error)
            try:
                conn.rollback()
            except Exception:
                pass
            messagebox.showerror("Duplicados", "No se pudo limpiar:\n%s" % error,
                                 parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)
        self._cache_asientos = None
        self._cache_saldos_extra = None
        messagebox.showinfo(
            "Duplicados",
            "🧹 %d asiento(s) duplicado(s) eliminado(s) en %d grupo(s).\n"
            "Se conservó el asiento del sistema.\n\nRespaldo: %s"
            % (borrados, len(grupos), os.path.basename(ruta_respaldo)),
            parent=self.parent_frame)
        try:
            self.generar_reporte()
        except Exception:
            pass

    def mostrar_diferencias_importe(self, diferencias=None, periodo=None):
        """🔍 Lista los asientos emparejados cuyo importe no coincide exacto.

        Sirve para ubicar diferencias de centavos (redondeo) o comisiones que
        antes solo se veían como un total en el Resumen del período.
        """
        if diferencias is None:
            # 🔎 Solo se muestran las diferencias del banco y mes seleccionados.
            #    Si la última lista calculada es de otro período, se recalcula.
            banco_actual = self.banco_seleccionado()
            if not banco_actual:
                messagebox.showerror("Diferencias de importe",
                                     "Elija un banco primero.",
                                     parent=self.parent_frame)
                return
            mes_actual = (mes_etiqueta_a_key(self.cmb_mes.get())
                          if hasattr(self, "cmb_mes") else "")
            clave_actual = (banco_actual.get("banco", ""),
                            banco_actual.get("cuenta", ""),
                            mes_actual or "")
            if getattr(self, "_diferencias_clave", None) != clave_actual:
                try:
                    self.parent_frame.configure(cursor="watch")
                    self.parent_frame.update_idletasks()
                except Exception:
                    pass
                try:
                    self.generar_reporte()
                except Exception as error:
                    messagebox.showerror(
                        "Diferencias de importe",
                        "No se pudieron calcular las diferencias del período:\n%s" % error,
                        parent=self.parent_frame)
                    return
                finally:
                    try:
                        self.parent_frame.configure(cursor="")
                    except Exception:
                        pass
            if periodo is None and hasattr(self, "cmb_mes"):
                periodo = self.cmb_mes.get()

        datos = list(diferencias if diferencias is not None else
                     (getattr(self, "_diferencias_importe", None) or []))
        if not datos:
            messagebox.showinfo(
                "Diferencias de importe",
                "No hay diferencias de importe entre los asientos y el banco.",
                parent=self.parent_frame)
            return
        banco = self.banco_seleccionado()
        etiqueta_banco = construir_etiqueta_banco(banco) if banco else ""
        periodo_txt = (" · " + str(periodo)) if periodo else ""
        v = ctk.CTkToplevel(self.parent_frame)
        v.title("🔍 Diferencias de importe · %s%s" % (etiqueta_banco, periodo_txt))
        v.geometry("1080x560")
        try:
            v.transient(self.parent_frame)
            v.resizable(True, True)
        except Exception:
            pass
        traer_al_frente(v)

        marco = ctk.CTkFrame(v)
        marco.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(marco, text="Diferencias por asiento",
                     font=(FAMILIA, 15, "bold"), text_color="#1f538d").pack(anchor="w", padx=8, pady=(6, 0))
        ctk.CTkLabel(marco, text="Solo se muestran asientos emparejados con una línea del banco "
                                  "cuyo importe no es idéntico.",
                     font=(FAMILIA, 10, "italic"), text_color="#7f8c8d").pack(anchor="w", padx=8)

        _repintar = lambda: None   # se define abajo; evita error si el control llama al crear
        f_ctrl = ctk.CTkFrame(marco, fg_color="transparent")
        f_ctrl.pack(fill="x", padx=8, pady=(4, 0))
        var_sin_comisiones = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(f_ctrl, text="Ocultar comisiones",
                        variable=var_sin_comisiones, font=(FAMILIA, 11),
                        command=lambda: _repintar()).pack(side="left")
        var_movimiento = tk.StringVar(value="Todos")
        ctk.CTkSegmentedButton(f_ctrl, values=["Todos", "Ingresos", "Egresos"],
                               variable=var_movimiento, width=250,
                               command=lambda _valor: _repintar()).pack(side="left", padx=(15, 0))
        label_cuentas = ctk.CTkLabel(f_ctrl, text="", font=(FAMILIA, 11),
                                     text_color="#7f8c8d")
        label_cuentas.pack(side="left", padx=(15, 0))

        cont = ctk.CTkFrame(marco, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=6, pady=6)
        columnas = ("fecha_sistema", "fecha_banco", "documento", "descripcion",
                    "monto_sistema", "monto_banco", "diferencia", "movimiento", "tipo")
        titulos = (("fecha_sistema", "Fecha sistema", 100),
                   ("fecha_banco", "Fecha banco", 100),
                   ("documento", "Documento", 150),
                   ("descripcion", "Descripción", 280),
                   ("monto_sistema", "Monto sistema", 105),
                   ("monto_banco", "Monto banco", 105),
                   ("diferencia", "Diferencia", 90),
                   ("movimiento", "Movimiento", 85),
                   ("tipo", "Tipo", 90))
        tabla = ttk.Treeview(cont, columns=columnas, show="headings", height=16)
        preparar_colores_montos(tabla)
        for col, texto, ancho in titulos:
            tabla.heading(col, text=texto)
            tabla.column(col, width=ancho,
                         anchor="w" if col in ("documento", "descripcion") else "center")
        vsb = ttk.Scrollbar(cont, orient="vertical", command=tabla.yview)
        tabla.configure(yscrollcommand=vsb.set)
        tabla.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        tabla.tag_configure("redondeo", background="#fef9e7")
        tabla.tag_configure("comision", background="#eaf2f8")
        tabla.tag_configure("diferencia", background="#fadbd8")

        def _tipo_txt(t):
            t = str(t or "").lower()
            if t == "comision":
                return "Comisión"
            if t == "redondeo":
                return "Redondeo"
            return "Diferencia"

        ordenados = sorted(datos, key=lambda f: (
            str(f.get("fecha_sistema") or ""),
            -abs(float(f.get("diferencia") or 0))))

        pie = ctk.CTkFrame(v, fg_color="transparent")
        pie.pack(fill="x", padx=12, pady=(0, 10))
        label_pie = ctk.CTkLabel(pie, text="", font=(FAMILIA, 12, "bold"),
                                 text_color="#1f538d")
        label_pie.pack(side="left", padx=8)
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=v.destroy).pack(side="right", padx=4)

        def _repintar():
            for item in tabla.get_children():
                tabla.delete(item)
            modo_mov = var_movimiento.get()
            visibles = []
            n_ing = n_egr = 0
            for f in ordenados:
                if (var_sin_comisiones.get()
                        and str(f.get("tipo") or "").lower() == "comision"):
                    continue
                es_ingreso = float(f.get("monto_sistema") or 0) > 0
                if modo_mov == "Ingresos" and not es_ingreso:
                    continue
                if modo_mov == "Egresos" and es_ingreso:
                    continue
                visibles.append(f)
                if es_ingreso:
                    n_ing += 1
                else:
                    n_egr += 1
            label_cuentas.configure(
                text="Ingresos con diferencia: %d   ·   Egresos con diferencia: %d"
                     % (n_ing, n_egr))
            total_vis = 0.0
            for i, f in enumerate(visibles):
                dif = float(f.get("diferencia") or 0)
                total_vis += dif
                tag = str(f.get("tipo") or "diferencia").lower()
                if tag not in ("redondeo", "comision", "diferencia"):
                    tag = "diferencia"
                tabla.insert("", "end", iid=str(i), tags=(tag,), values=(
                    fecha_para_mostrar(f.get("fecha_sistema") or ""),
                    fecha_para_mostrar(f.get("fecha_banco") or ""),
                    f.get("documento") or "",
                    (f.get("descripcion") or "")[:80],
                    formatear_monto(f.get("monto_sistema") or 0),
                    formatear_monto(f.get("monto_banco") or 0),
                    formatear_monto(dif),
                    ("Ingreso" if float(f.get("monto_sistema") or 0) > 0 else "Egreso"),
                    _tipo_txt(tag)))
            texto_pie = ("%d diferencia(s) por asiento   ·   Suma: %s"
                         % (len(visibles), formatear_monto(total_vis)))
            if var_sin_comisiones.get():
                texto_pie += "   ·   sin comisiones"
            try:
                (n1, ni1, ti1, ne1, te1), (n2, ni2, ti2, ne2, te2) = self._ultimo_resumen
                dif_neta = (ti1 - te1) - (ti2 - te2)
                texto_pie += ("   ·   Diferencia neta del período: %s"
                              % formatear_monto(dif_neta))
            except Exception:
                pass
            label_pie.configure(text=texto_pie)

        _repintar()

    def mostrar_resumen(self):
        """📊 Ventana con las cantidades y totales de cada lado del período.

        Muestra, para el banco y mes elegidos: cuántos asientos tiene el sistema y
        cuántas líneas el estado de cuenta, con ingresos y egresos separados, y la
        diferencia de netos.
        """
        banco = self.banco_seleccionado()
        if not banco:
            messagebox.showerror("Resumen", "Elija un banco primero.",
                                 parent=self.parent_frame)
            return
        mes_key = mes_etiqueta_a_key(self.cmb_mes.get()) if hasattr(self, "cmb_mes") else ""
        etiqueta_mes = self.cmb_mes.get() if hasattr(self, "cmb_mes") else "Todos los meses"
        ignorados = self.cargar_ignorados(banco)
        ign_sistema = ignorados.get("sistema", set())
        ign_pdf = ignorados.get("estado_cuenta", set())

        # 🔎 Asegurar que las diferencias calculadas sean de ESTE banco/mes.
        clave_resumen = (banco.get("banco", ""), banco.get("cuenta", ""), mes_key or "")
        if getattr(self, "_diferencias_clave", None) != clave_resumen:
            try:
                self.generar_reporte()
            except Exception as error:
                print("[Resumen] no se pudieron recalcular las diferencias:", error)

        try:
            sistema = [m for m in self.cargar_movimientos_sistema(banco, mes=mes_key)
                       if not m.get("espejo_banco")]
        except Exception as error:
            print("[Resumen] movimientos del sistema:", error)
            sistema = []
        sistema = [m for m in sistema
                   if self._clave_ignorado(m.get("origen", "sistema"), m.get("tabla", ""),
                                           m.get("id", 0), m.get("fecha"), m.get("monto"),
                                           m.get("descripcion")) not in ign_sistema]
        # 🏦 El banco muestra la transferencia + su comisión en UNA sola línea.
        #    Se suma la comisión a la transferencia; si no, quedaba como un
        #    «sobrante» de 4.80 aunque el banco ya la había cobrado.
        comisiones = {}
        sistema_sin_comision = []
        for m in sistema:
            desc = _norm_texto(m.get("descripcion", ""))
            es_comision = (m.get("tabla") == "tx_comision" or m.get("es_comision")
                           or ("comision" in desc
                               and abs(float(m.get("monto") or 0)) <= 20))
            if es_comision:
                comisiones[(m.get("id"), normalizar_fecha(m.get("fecha")))] = \
                    float(m.get("monto") or 0)
            else:
                sistema_sin_comision.append(m)
        for m in sistema_sin_comision:
            if m.get("tabla") == "transferencias_bancarias":
                com = comisiones.get((m.get("id"), normalizar_fecha(m.get("fecha"))))
                if com:
                    m["monto"] = float(m.get("monto") or 0) + com
        sistema = sistema_sin_comision
        try:
            manuales = [m for m in self.cargar_manuales(banco)
                        if not mes_key
                        or normalizar_fecha(m.get("fecha")).startswith(mes_key)]
        except Exception as error:
            print("[Resumen] manuales:", error)
            manuales = []
        try:
            lineas = self._cargar_movimientos_pdf(banco, mes_key) or []
        except Exception as error:
            print("[Resumen] estado de cuenta:", error)
            lineas = []
        # 🚫 Igual que la pestaña de Conciliación: las líneas marcadas como
        #    eliminadas (ocultas) no se cuentan en el resumen.
        lineas = [b for b in lineas
                  if self._clave_ignorado("estado_cuenta", "", 0, b.get("fecha"),
                                          b.get("monto"), b.get("descripcion")) not in ign_pdf]
        try:
            extras = self.cargar_asientos_extra(banco, mes_key) or []
        except Exception as error:
            print("[Resumen] asientos extra:", error)
            extras = []
        # ⏳ Asientos de fin del mes anterior que el banco posteó en este mes.
        try:
            comision = cargar_comision_interbancaria()
        except Exception:
            comision = 0.0
        try:
            prev_pend = self._traer_pendientes_mes_anterior(
                banco, mes_key, sistema, lineas, comision)
            for m in prev_pend:
                m["_pendiente"] = True
                sistema.append(m)
        except Exception as error:
            print("[Resumen] pendientes del mes anterior:", error)

        def _lado(lista):
            ingresos = [float(m.get("monto") or 0) for m in lista
                        if float(m.get("monto") or 0) > 0]
            egresos = [float(m.get("monto") or 0) for m in lista
                       if float(m.get("monto") or 0) < 0]
            return {"total": len(lista), "n_ing": len(ingresos), "ing": sum(ingresos),
                    "n_egr": len(egresos), "egr": -sum(egresos),
                    "neto": sum(ingresos) + sum(egresos)}

        # 🧮 Comparación justa: cada línea del banco se usa UNA sola vez.
        #    - 'unicos': asientos que representan un movimiento real.
        #    - 'duplicados': asientos extra que copian una línea ya usada.
        #    - 'sobrantes': asientos sin línea en el banco.
        #    - 'faltantes': líneas del banco sin asiento.
        asientos = []
        for m in sistema:
            d = dict(m)
            origen_s = str(m.get("origen") or "")
            if not origen_s.startswith("s:"):
                origen_s = "s:%s" % (m.get("tabla") or "sistema")
            d["origen"] = origen_s
            d["_prioridad"] = 0 if origen_s == "s:pagos_comprobantes" else 1
            asientos.append(d)
        # 🧾 Asientos que no vienen de Compras/Ventas (e, App Grifo, comisiones):
        #    también forman parte del lado del sistema.
        for m in extras:
            d = dict(m)
            d["_prioridad"] = 2
            asientos.append(d)
        for m in manuales:
            d = dict(m)
            d["origen"] = "manual"
            d["_prioridad"] = 3
            asientos.append(d)
        match, dup_idx, sobr_idx, falt_idx = emparejar_asientos_banco(
            asientos, lineas, comision)

        # 🔁 Corregir pares cruzados: si dos asientos quedaron con las líneas
        #    intercambiadas (7,56 con 7,55 y 7,55 con 7,56), se cambian para
        #    que cada uno tome la línea de su importe exacto.
        _items_match = list(match.items())
        for _a in range(len(_items_match)):
            for _b in range(_a + 1, len(_items_match)):
                _i1, _j1 = _items_match[_a]
                _i2, _j2 = _items_match[_b]
                try:
                    _am1 = float(asientos[_i1].get("monto") or 0)
                    _am2 = float(asientos[_i2].get("monto") or 0)
                    _bm1 = float(lineas[_j1].get("monto") or 0)
                    _bm2 = float(lineas[_j2].get("monto") or 0)
                except Exception:
                    continue
                if _am1 * _bm2 <= 0 or _am2 * _bm1 <= 0:
                    continue
                _actual = abs(_bm1 - _am1) + abs(_bm2 - _am2)
                _cruzado = abs(_bm2 - _am1) + abs(_bm1 - _am2)
                if _cruzado + 0.001 < _actual:
                    match[_i1], match[_i2] = _j2, _j1
        # 🏦 Si el banco cobró la comisión en la MISMA línea (principal + 4.80),
        #    el asiento se compara por el total del banco. Antes el sistema
        #    mostraba sólo el principal y aparecía una diferencia de 4.80 por
        #    cada transferencia, aunque no faltara ningún movimiento.
        for i, j in match.items():
            a = asientos[i]
            b = lineas[j]
            try:
                am = float(a.get("monto") or 0)
                bm = float(b.get("monto") or 0)
            except (TypeError, ValueError):
                continue
            if am * bm <= 0:
                continue
            if abs((abs(bm) - abs(am)) - float(comision or 0)) <= 0.02:
                a["monto"] = bm
        # 🔍 Diferencias de importe que quedan en el Resumen (redondeos/comisiones).
        diferencias_resumen = []
        _vistos_dif = set()
        for i, j in match.items():
            a = asientos[i]
            b = lineas[j]
            try:
                am = float(a.get("monto") or 0)
                bm = float(b.get("monto") or 0)
            except (TypeError, ValueError):
                continue
            dif = round(bm - am, 2)
            if abs(dif) < 0.005:
                continue
            clave = (a.get("tabla"), a.get("id"),
                     normalizar_fecha(b.get("fecha")), round(bm, 2))
            if clave in _vistos_dif:
                continue
            _vistos_dif.add(clave)
            try:
                com = abs(float(comision or 0))
            except (TypeError, ValueError):
                com = 0.0
            if abs(abs(dif) - com) <= 0.02 and abs(dif) > 0.02:
                tipo = "comision"
            elif abs(dif) <= 0.05:
                tipo = "redondeo"
            else:
                tipo = "diferencia"
            diferencias_resumen.append({
                "fecha_sistema": a.get("fecha") or "",
                "fecha_banco": b.get("fecha") or "",
                "documento": a.get("documento") or "",
                "descripcion": a.get("descripcion") or "",
                "monto_sistema": am,
                "monto_banco": bm,
                "diferencia": dif,
                "tipo": tipo,
                "origen": a.get("origen") or "",
                "tabla": a.get("tabla") or "",
                "id": a.get("id") or 0,
            })
        # 🔒 Sólo se descuentan los duplicados MANUALES claros (copia de la línea
        #    o principal + comisión). Si el asiento extra no es una copia clara,
        #    se muestra como SOBRANTE para no ocultar un movimiento real.
        dup_final, sobr_final = [], list(sobr_idx)
        for i, j in dup_idx:
            pi = next((i2 for i2, j2 in match.items() if j2 == j), None)
            principal = asientos[pi] if pi is not None else None
            if _es_duplicado_manual_claro(asientos[i], lineas[j], principal, comision):
                dup_final.append((i, j))
            else:
                sobr_final.append(i)
        dup_idx, sobr_idx = dup_final, sobr_final
        # ✅ Los asientos que el usuario ya concilió no son «sobrantes», aunque su
        #    línea del banco esté marcada como eliminada. Así el resumen coincide
        #    con el aviso de la pestaña de Conciliación.
        conciliados = self.cargar_conciliados(banco)
        resueltos = set()
        for i in list(sobr_idx):
            a = asientos[i]
            origen = str(a.get("origen") or "")
            if origen == "manual":
                if str(a.get("estado") or "").lower() == "conciliado":
                    resueltos.add(i)
                continue
            if origen.startswith("s:"):
                base, tabla = "sistema", origen[2:]
            elif origen in ("e", "estado_cuenta"):
                base, tabla = "estado_cuenta", ""
            else:
                base, tabla = origen, str(a.get("tabla") or "")
            clave = self._clave_conciliado(base, tabla, a.get("id", 0), a.get("fecha"),
                                           a.get("monto"), a.get("descripcion"))
            if clave in conciliados:
                resueltos.add(i)
        if resueltos:
            sobr_idx = [i for i in sobr_idx if i not in resueltos]
        # ⏳ Asientos de fin de mes que aparecen en el estado siguiente: no son
        #    sobrantes de este mes; se conciliarán al abrir el mes siguiente.
        pendientes_siguiente_resumen = []
        try:
            filas_pend = []
            for i in sobr_idx:
                a = asientos[i]
                if a.get("tabla") not in ("pagos_comprobantes", "pagos_clientes",
                                          "transferencias_bancarias", "tx_comision"):
                    continue
                filas_pend.append({"origen": "sistema", "estado": "diferencia",
                                   "tabla": a.get("tabla"), "id": a.get("id"),
                                   "fecha": a.get("fecha"), "monto": a.get("monto"),
                                   "descripcion": a.get("descripcion"),
                                   "documento": a.get("documento"),
                                   "id_factura": a.get("id_factura")})
            marcados = self._marcar_pendientes_siguiente(
                filas_pend, banco, mes_key, comision)
            pendientes_siguiente_resumen = list(marcados or [])
            if marcados:
                claves = {(m.get("tabla"), m.get("id"), normalizar_fecha(m.get("fecha")),
                           round(float(m.get("monto") or 0), 2)) for m in marcados}
                sobr_idx = [i for i in sobr_idx
                            if (asientos[i].get("tabla"), asientos[i].get("id"),
                                normalizar_fecha(asientos[i].get("fecha")),
                                round(float(asientos[i].get("monto") or 0), 2)) not in claves]
        except Exception as error:
            print("[Resumen] pendientes próximo mes:", error)
        # 🔄 Manda la decisión que ya tomó la pestaña de Conciliación: si allí
        #    una fila quedó «Pendiente próximo estado», el Resumen debe mostrar
        #    esa misma cantidad y no recalcularla con otro criterio.
        try:
            pend_reporte = [f for f in (getattr(self, "filas_conciliacion", None) or [])
                            if f.get("_pendiente_siguiente")]
        except Exception:
            pend_reporte = []
        if pend_reporte:
            pendientes_siguiente_resumen = list(pend_reporte)
            claves_reporte = {(f.get("tabla"), f.get("id"),
                               normalizar_fecha(f.get("fecha")),
                               round(float(f.get("monto") or 0), 2))
                              for f in pend_reporte}
            sobr_idx = [i for i in sobr_idx
                        if (asientos[i].get("tabla"), asientos[i].get("id"),
                            normalizar_fecha(asientos[i].get("fecha")),
                            round(float(asientos[i].get("monto") or 0), 2))
                        not in claves_reporte]
        # 🔄 También manda el estado final de la tabla: si la Conciliación ya
        #    muestra un asiento como «Conciliado», el Resumen no debe contarlo
        #    como sobrante aunque su propio cruce no lo haya emparejado igual.
        try:
            conciliadas_reporte = [f for f in (getattr(self, "filas_conciliacion", None) or [])
                                   if str(f.get("estado") or "").lower() == "conciliado"
                                   and f.get("origen") != "estado_cuenta"]
        except Exception:
            conciliadas_reporte = []
        if conciliadas_reporte:
            claves_conc = {(f.get("tabla"), f.get("id"),
                            normalizar_fecha(f.get("fecha")),
                            round(float(f.get("monto") or 0), 2))
                           for f in conciliadas_reporte}
            sobr_idx = [i for i in sobr_idx
                        if (asientos[i].get("tabla"), asientos[i].get("id"),
                            normalizar_fecha(asientos[i].get("fecha")),
                            round(float(asientos[i].get("monto") or 0), 2))
                        not in claves_conc]
        unicos = [asientos[i] for i in sorted(match)] + [asientos[i] for i in sobr_idx]
        duplicados = [asientos[i] for i, _ in dup_idx]
        lado_sis = _lado(unicos)
        lado_pdf = _lado(lineas)
        lado_dup = _lado(duplicados)
        neto_sis, neto_pdf = lado_sis["neto"], lado_pdf["neto"]
        neto_sob = sum(float(asientos[i].get("monto") or 0) for i in sobr_idx)
        neto_falt = sum(float(lineas[j].get("monto") or 0) for j in falt_idx)

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("📊 Resumen del período")
        v.geometry("720x520")
        try:
            v.transient(self.parent_frame)
        except Exception:
            pass
        traer_al_frente(v)

        marco = ctk.CTkFrame(v)
        marco.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(marco, text="Banco: %s" % construir_etiqueta_banco(banco),
                     font=(FAMILIA, 14, "bold")).pack(anchor="w", padx=8, pady=(8, 0))
        ctk.CTkLabel(marco, text="Período: %s" % etiqueta_mes,
                     font=(FAMILIA, 12), text_color="#566573").pack(anchor="w", padx=8, pady=(0, 8))

        cont = ctk.CTkFrame(marco, fg_color="transparent")
        cont.pack(fill="both", expand=True, padx=8)
        columnas = ("concepto", "sistema", "estado")
        tabla = ttk.Treeview(cont, columns=columnas, show="headings", height=11)
        preparar_colores_montos(tabla)
        for col, titulo, ancho in (("concepto", "Concepto", 220),
                                   ("sistema", "Asientos (sin duplicados)", 240),
                                   ("estado", "Estado de cuenta", 240)):
            tabla.heading(col, text=titulo)
            tabla.column(col, width=ancho,
                         anchor="w" if col == "concepto" else "center")
        tabla.pack(fill="both", expand=True)
        filas_tabla = [
            ("Cantidad", "%d" % lado_sis["total"], "%d" % lado_pdf["total"]),
            ("Ingresos", "%d  ·  %s" % (lado_sis["n_ing"], formatear_monto(lado_sis["ing"])),
             "%d  ·  %s" % (lado_pdf["n_ing"], formatear_monto(lado_pdf["ing"]))),
            ("Egresos", "%d  ·  %s" % (lado_sis["n_egr"], formatear_monto(lado_sis["egr"])),
             "%d  ·  %s" % (lado_pdf["n_egr"], formatear_monto(lado_pdf["egr"]))),
            ("Neto (ingresos − egresos)", formatear_monto(neto_sis),
             formatear_monto(neto_pdf)),
            ("Diferencia de netos", formatear_monto(neto_sis - neto_pdf), ""),
            ("Diferencias de importe",
             "%d  ·  %s" % (len(diferencias_resumen),
                            formatear_monto(sum(float(f.get("diferencia") or 0)
                                                for f in diferencias_resumen))), ""),
            ("Duplicados (no sumados)",
             "%d  ·  %s" % (lado_dup["total"], formatear_monto(lado_dup["neto"])), ""),
            ("Sobrantes (sistema sin banco)",
             "%d  ·  %s" % (len(sobr_idx), formatear_monto(neto_sob)), ""),
            ("Faltantes (banco sin asiento)",
             "%d  ·  %s" % (len(falt_idx), formatear_monto(neto_falt)), ""),
            ("Pendientes próximo mes",
             "%d  ·  %s" % (len(pendientes_siguiente_resumen),
                            formatear_monto(sum(float(m.get("monto") or 0)
                                                for m in pendientes_siguiente_resumen))), ""),
        ]
        # 🔎 Duplicados / sobrantes / faltantes con el MISMO cruce corregido del
        #    reporte (filas_conciliacion), para que el cuadro quede consistente.
        for i, fila in enumerate(filas_tabla):
            tabla.insert("", "end", iid=str(i), values=fila)

        ctk.CTkLabel(
            marco,
            text=("El resumen usa las mismas líneas visibles que la conciliación: las "
                  "marcadas como eliminadas/ocultas no se cuentan y los asientos ya "
                  "conciliados dejan de ser sobrantes. Cuando el banco cobra la comisión "
                  "en la misma línea, se compara el total del banco. La diferencia que "
                  "quede es real (redondeo, sobrantes o faltantes)."),
            font=(FAMILIA, 10, "italic"), text_color="#7f8c8d",
            wraplength=650, justify="left").pack(anchor="w", padx=8, pady=(6, 0))

        pie = ctk.CTkFrame(v, fg_color="transparent")
        pie.pack(fill="x", padx=12, pady=(4, 12))
        ctk.CTkButton(pie, text="🔍 Ver diferencias", width=160,
                      font=(FAMILIA, 11, "bold"), fg_color="#8e44ad", hover_color="#6c3483",
                      command=lambda: self.mostrar_diferencias_importe(
                          diferencias_resumen, etiqueta_mes)).pack(side="left", padx=(0, 8))
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=v.destroy).pack(side="right")

    def _periodos_cargados(self, banco):
        """{'AAAA-MM'} de los estados de cuenta cargados para ese banco y cuenta."""
        periodos = set()
        if not banco:
            return periodos
        conn = conectar_db(silencioso=True)
        if not conn:
            return periodos
        try:
            with conn.cursor() as c:
                c.execute("""SELECT DISTINCT LEFT(fecha, 7) FROM conciliacion_movimientos_pdf
                             WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                               AND COALESCE(cuenta,'') = %s""",
                          (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                periodos = {str(r[0]) for r in c.fetchall() if r[0]}
        except Exception as error:
            print("[Conciliación] períodos cargados:", error)
        finally:
            liberar_conexion(conn)
        return periodos

    def mostrar_estados_cargados(self):
        """📚 Ventana con TODOS los estados de cuenta guardados.

        Muestra banco, cuenta, período, cantidad de líneas, rango de fechas, neto
        y cuándo se guardó. Sirve para saber qué meses están cargados sin tener que
        ir mes por mes en el desplegable.
        """
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Estados de cuenta",
                                 "Sin conexión a la base de datos.",
                                 parent=self.parent_frame)
            return
        filas = []
        try:
            with conn.cursor() as c:
                c.execute("""SELECT banco, COALESCE(cuenta,''), COALESCE(periodo,''),
                                    COUNT(*), MIN(fecha), MAX(fecha),
                                    COALESCE(SUM(monto),0), COALESCE(MAX(creado),''),
                                    COALESCE(MAX(archivo_ruta),'')
                             FROM conciliacion_movimientos_pdf
                             GROUP BY banco, cuenta, periodo
                             ORDER BY banco, periodo DESC""")
                filas = c.fetchall()
        except Exception as error:
            print("[Conciliación] estados cargados:", error)
        finally:
            liberar_conexion(conn)

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("📚 Estados de cuenta cargados")
        v.geometry("1000x470")
        try:
            v.transient(self.parent_frame)
            v.resizable(True, True)
        except Exception:
            pass
        traer_al_frente(v)

        marco = ctk.CTkFrame(v)
        marco.pack(fill="both", expand=True, padx=10, pady=10)
        total_lineas = sum(int(f[3] or 0) for f in filas)
        ctk.CTkLabel(marco,
                     text="Estados de cuenta guardados: %d  ·  líneas en total: %d"
                          % (len(filas), total_lineas),
                     font=(FAMILIA, 13, "bold")).pack(anchor="w", padx=6, pady=(6, 8))
        if not filas:
            ctk.CTkLabel(marco, text="Todavía no hay ningún estado de cuenta cargado.",
                         font=(FAMILIA, 12), text_color="#a04000").pack(pady=24)
        else:
            cont = ctk.CTkFrame(marco, fg_color="transparent")
            cont.pack(fill="both", expand=True)
            columnas = ("banco", "cuenta", "periodo", "lineas", "desde", "hasta",
                        "neto", "guardado", "respaldo")
            tabla = ttk.Treeview(cont, columns=columnas, show="headings", height=14)
            preparar_colores_montos(tabla)
            for col, titulo, ancho in (("banco", "Banco", 105), ("cuenta", "Cuenta", 145),
                                       ("periodo", "Período", 90), ("lineas", "Líneas", 65),
                                       ("desde", "Desde", 92), ("hasta", "Hasta", 92),
                                       ("neto", "Neto", 115), ("guardado", "Guardado", 120),
                                       ("respaldo", "Respaldo", 95)):
                tabla.heading(col, text=titulo)
                tabla.column(col, width=ancho,
                             anchor="w" if col in ("banco", "cuenta") else "center")
            despl = ttk.Scrollbar(cont, orient="vertical", command=tabla.yview)
            tabla.configure(yscrollcommand=despl.set)
            tabla.pack(side="left", fill="both", expand=True)
            despl.pack(side="right", fill="y")
            for i, f in enumerate(filas):
                periodo = str(f[2] or "").strip()
                if not periodo:
                    # Estados viejos (cargados antes de existir el período): se
                    # muestra el mes de la última fecha, para que igual se entienda.
                    _ult = str(f[5] or "")
                    periodo = (_ult[:7] + " (por fecha)") if len(_ult) >= 7 else "(sin período)"
                tabla.insert("", "end", iid=str(i),
                             values=(f[0] or "", f[1] or "", periodo, f[3],
                                     fecha_para_mostrar(f[4]), fecha_para_mostrar(f[5]),
                                     formatear_monto(f[6]), str(f[7] or "")[:16],
                                     "📄 Sí" if str(f[8] or "").strip() else "—"))

            def _abrir_respaldo(_evento=None):
                """Doble clic: abre el PDF archivado de ese estado de cuenta."""
                seleccion = tabla.selection()
                if not seleccion:
                    return
                archivo = str(filas[int(seleccion[0])][8] or "").strip()
                if not archivo:
                    messagebox.showinfo("Estados de cuenta",
                                        "Ese estado de cuenta se cargó antes de que se "
                                        "guardaran los respaldos.\nVolvé a subir el PDF "
                                        "para archivarlo.",
                                        parent=v)
                    return
                ruta_real = resolver_ruta_archivo(archivo) or archivo
                abrir_documento(ruta_real, "Estado de cuenta")

            tabla.bind("<Double-1>", _abrir_respaldo)
        ctk.CTkButton(marco, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=v.destroy).pack(pady=10)

    def _mostrar_faltantes_banco(self, banco, propuestas, movs):
        """📋 Líneas del estado de cuenta que quedaron SIN pareja en el sistema.

        ⚠️ Antes se calculaba con las propuestas del motor inteligente, y ese motor
        solo mira los asientos de 'conciliacion_bancaria': cualquier línea que ya
        estuviera conciliada por la vía clásica (o emparejada con un pago) salía
        como «faltante» — por eso listaba las 138 líneas. Ahora se usa la MISMA
        lista que el aviso naranja de la pestaña.
        """
        filas_tabla = list(getattr(self, "filas_conciliacion", None) or [])
        if not filas_tabla:
            messagebox.showinfo(
                "Faltantes",
                "Primero cargue los movimientos del sistema (🔄) para poder comparar.",
                parent=self.parent_frame)
            return
        faltantes = [f for f in filas_tabla
                     if f.get("origen") == "estado_cuenta"
                     and f.get("estado") != "conciliado"]
        # 🔄 La otra dirección: asientos del SISTEMA que no aparecen en el estado de
        #    cuenta cargado (los que «sobran», los de otro mes o los duplicados).
        sobrantes = list(getattr(self, "_pendientes_sistema", None) or [])
        # 📄 Sin estado de cuenta cargado no se puede afirmar que el cruce esté
        #    completo: no hay contra qué comparar.
        try:
            _mes_vista = mes_etiqueta_a_key(self.cmb_mes.get()) if hasattr(self, "cmb_mes") else ""
        except Exception:
            _mes_vista = ""
        _periodos_ok = getattr(self, "_periodos_ok", None)
        _periodos_ok = _periodos_ok if isinstance(_periodos_ok, (set, frozenset)) else set()
        if _mes_vista and _mes_vista not in _periodos_ok:
            messagebox.showinfo(
                "Faltantes",
                "📄 Todavía no hay estado de cuenta cargado para %s.\n\n"
                "Sin el estado de cuenta no hay contra qué comparar: subí el PDF de "
                "ese mes para poder cruzar los asientos del sistema."
                % (self.cmb_mes.get() if hasattr(self, "cmb_mes") else _mes_vista),
                parent=self.parent_frame)
            return
        if not faltantes and not sobrantes:
            messagebox.showinfo(
                "Faltantes",
                "El cruce está completo en las dos direcciones:\n\n"
                "• Todas las líneas del estado de cuenta tienen su asiento.\n"
                "• Todos los asientos del sistema aparecen en el estado de cuenta.",
                parent=self.parent_frame)
            return
        ventana_f = ctk.CTkToplevel(self.parent_frame)
        ventana_f.title("📋 Faltantes del banco · %s" % banco.get("banco", ""))
        ventana_f.geometry("1000x640")
        try:
            ventana_f.transient(self.parent_frame)
            ventana_f.resizable(True, True)
        except Exception:
            pass
        traer_al_frente(ventana_f)
        marco = ctk.CTkFrame(ventana_f)
        marco.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        tabs = ctk.CTkTabview(marco)
        tabs.pack(fill="both", expand=True)
        tab_banco_nombre = "FALTANTES · banco sin asiento (%d)" % len(faltantes)
        tab_banco = tabs.add(tab_banco_nombre)
        tab_sistema = tabs.add("SOBRANTES · sistema sin banco (%d)" % len(sobrantes))

        # 🔎 Facturas del sistema por importe: sirve para decir, en cada línea del
        #    banco, si YA hay una factura con ese total (falta sólo el pago) o si
        #    no hay nada registrado.
        # 🔍 Lo que de verdad importa: ¿hay un ASIENTO con ese importe en el banco?
        #    (La columna anterior buscaba facturas por importe y confundía: una
        #    factura no es lo mismo que un movimiento.)
        asientos_por_importe = {}
        conn_as = conectar_db(silencioso=True)
        if conn_as:
            try:
                with conn_as.cursor() as cur_as:
                    cur_as.execute("""SELECT fecha, monto, COALESCE(origen,''), COALESCE(estado,'')
                                      FROM conciliacion_bancaria
                                      WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                        AND COALESCE(cuenta,'') = %s""",
                                   (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                    for _f, _mo, _or, _es in cur_as.fetchall():
                        asientos_por_importe.setdefault(
                            round(abs(float(_mo or 0)), 2), []).append(
                                (clave_fecha(_f), str(_or), str(_es)))
            except Exception as _e_as:
                print("[Conciliación] asientos por importe:", _e_as)
            finally:
                liberar_conexion(conn_as)

        facturas_por_importe = {}
        try:
            _conn_f = conectar_db(silencioso=True)
            if _conn_f:
                with _conn_f.cursor() as _cur_f:
                    # 🔍 Solo facturas REALES con número: se excluyen los registros
                    #    espejo «PAGO BANCO» (no tienen número) para no decir «Sí»
                    #    cuando en realidad la factura no existe.
                    _cur_f.execute("""SELECT COALESCE(numero_documento,''), COALESCE(proveedor,''),
                                             COALESCE(total,0)
                                      FROM facturas_recibidas
                                      WHERE COALESCE(total,0) > 0
                                        AND COALESCE(numero_documento,'') <> ''
                                        AND COALESCE(tipo_documento,'') <> 'PAGO BANCO'""")
                    for _num, _prov, _tot in _cur_f.fetchall():
                        facturas_por_importe.setdefault(
                            round(abs(float(_tot)), 2), []).append((_num, _prov))
        except Exception as _e_fact:
            print("[Conciliación] facturas por importe:", _e_fact)
        finally:
            try:
                liberar_conexion(_conn_f)
            except Exception:
                pass

        def _armar_tabla(padre, filas, texto_vacio, nota="", con_factura=False):
            """Tabla de (fecha, descripción, monto) [+ factura]. Devuelve el Treeview."""
            if nota:
                ctk.CTkLabel(padre, text=nota, font=(FAMILIA, 11), text_color="#7f8c8d",
                             wraplength=900, justify="left").pack(anchor="w", padx=6, pady=(4, 2))
            if not filas:
                ctk.CTkLabel(padre, text=texto_vacio, font=(FAMILIA, 12),
                             text_color="#1e8449").pack(pady=24)
                return None
            cont = ctk.CTkFrame(padre, fg_color="transparent")
            cont.pack(fill="both", expand=True)
            columnas = ("fecha", "descripcion", "monto") + (("asiento",) if con_factura else ())
            titulos = [("fecha", "Fecha", 100), ("descripcion", "Descripción", 520),
                       ("monto", "Monto", 110)]
            if con_factura:
                titulos.append(("asiento", "Asiento con el mismo importe", 260))
            tabla_t = ttk.Treeview(cont, columns=columnas, show="headings", height=15)
            preparar_colores_montos(tabla_t)
            for col, titulo, ancho in titulos:
                tabla_t.heading(col, text=titulo)
                tabla_t.column(col, width=ancho,
                               anchor="w" if col in ("descripcion", "asiento") else "center")
            despl = ttk.Scrollbar(cont, orient="vertical", command=tabla_t.yview)
            tabla_t.configure(yscrollcommand=despl.set)
            tabla_t.pack(side="left", fill="both", expand=True)
            despl.pack(side="right", fill="y")
            for indice, m in enumerate(filas):
                valores = [str(m.get("fecha") or "")[:10],
                           str(m.get("descripcion") or "")[:70],
                           formatear_monto(m.get("monto"))]
                if con_factura:
                    _imp = round(abs(float(m.get("monto") or 0)), 2)
                    _cands = asientos_por_importe.get(_imp) or []
                    if not _cands:
                        valores.append("— ninguno")
                    else:
                        _dia = clave_fecha(m.get("fecha")) or ""
                        def _dias(a, _ref=_dia):
                            try:
                                return abs((datetime.strptime(a[0], "%Y-%m-%d")
                                            - datetime.strptime(_ref, "%Y-%m-%d")).days)
                            except Exception:
                                return 9999
                        _mejor = min(_cands, key=_dias)
                        _fecha_txt = ("%s/%s/%s" % (_mejor[0][8:10], _mejor[0][5:7], _mejor[0][0:4])
                                      if len(_mejor[0]) >= 10 else "sin fecha")
                        _dif = _dias(_mejor)
                        valores.append("%s · %s%s" % (
                            _fecha_txt,
                            "manual" if _mejor[1] == "manual" else "sistema",
                            (" · %d día(s) antes/después" % _dif) if 0 < _dif < 9999 else " · mismo día"))
                tabla_t.insert("", "end", iid=str(indice), values=tuple(valores))
            return tabla_t

        tabla_b = _armar_tabla(tab_banco, faltantes,
                               "✅ Todas las líneas del estado de cuenta tienen su asiento.",
                               con_factura=True)

        def _quitar_faltantes(iids):
            """Quita de la ventana las líneas ya registradas/ignoradas, sin cerrarla."""
            nonlocal tab_banco_nombre
            for iid in (iids or []):
                try:
                    tabla_b.delete(iid)
                except Exception:
                    pass
            restantes = len(tabla_b.get_children()) if tabla_b is not None else 0
            nuevo_nombre = "FALTANTES · banco sin asiento (%d)" % restantes
            try:
                tabs.rename(tab_banco_nombre, nuevo_nombre)
                tab_banco_nombre = nuevo_nombre
            except Exception:
                pass
            if restantes == 0 and tabla_b is not None:
                try:
                    ctk.CTkLabel(tab_banco, text="✅ Ya no quedan líneas del banco sin asiento.",
                                 font=(FAMILIA, 12), text_color="#1e8449").pack(pady=24)
                except Exception:
                    pass
        _armar_tabla(
            tab_sistema, sobrantes,
            "✅ Todos los movimientos del sistema aparecen en el estado de cuenta.",
            nota="Asientos del sistema que NO aparecen en el estado de cuenta cargado. "
                 "Puede que el banco los haya cobrado en otro mes, o que estén duplicados "
                 "o mal registrados en el sistema.")

        def _seleccion():
            if tabla_b is None:
                return []
            return list(tabla_b.selection())

        def _registrar_en_banco():
            """Crea los movimientos en el banco (sin factura ni comprobante)."""
            seleccion = _seleccion()
            if not seleccion:
                messagebox.showinfo("Faltantes", "Seleccione uno o más movimientos.",
                                    parent=ventana_f)
                return
            if not messagebox.askyesno(
                    "Registrar en banco",
                    "¿Registrar %d movimiento(s) directamente en el banco?\n\n"
                    "Se crearán como asientos MANUALES y quedarán conciliados.\n"
                    "No se toca Compras ni Ventas." % len(seleccion),
                    parent=ventana_f):
                return
            conn = conectar_db(silencioso=True)
            if not conn:
                return
            creados = 0
            ya_estaba = 0
            try:
                with conn.cursor() as c:
                    for iid in seleccion:
                        m = faltantes[int(iid)]
                        monto = float(m.get("monto") or 0)
                        # 🛡️ No duplicar: si ese movimiento ya fue registrado como
                        #    manual (misma fecha, importe y descripción), se saltea.
                        c.execute("""SELECT 1 FROM conciliacion_bancaria
                                     WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                       AND COALESCE(cuenta,'') = %s
                                       AND COALESCE(fecha,'') = %s
                                       AND ABS(COALESCE(monto,0) - %s) < 0.005 LIMIT 1""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), monto))
                        if c.fetchone():
                            ya_estaba += 1
                            continue
                        c.execute("""INSERT INTO conciliacion_bancaria
                            (banco, cuenta, fecha, descripcion, monto, tipo, origen,
                             estado, fecha_conciliacion)
                            VALUES (%s,%s,%s,%s,%s,%s,'manual','conciliado',%s)""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), str(m.get("descripcion") or ""),
                                   monto, "ingreso" if monto > 0 else "egreso",
                                   datetime.now().strftime("%d/%m/%Y")))
                        c.execute("""INSERT INTO conciliacion_auditoria
                            (banco, cuenta, fecha_banco, descripcion_banco, importe_banco,
                             sentido, estado, confianza, reglas, observacion,
                             origen_decision, creado, activo)
                            VALUES (%s,%s,%s,%s,%s,%s,'REGISTRADO_BANCO',100,%s,%s,
                                    'manual',%s,TRUE)""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), str(m.get("descripcion") or ""),
                                   abs(monto), "ingreso" if monto > 0 else "egreso",
                                   "registro directo desde el estado de cuenta",
                                   "sin factura ni comprobante",
                                   datetime.now().strftime("%Y-%m-%d %H:%M")))
                        creados += 1
                conn.commit()
            except Exception as error:
                print("[Conciliación] registrar en banco:", error)
                messagebox.showerror("Faltantes", "No se pudo registrar:\n%s" % error,
                                     parent=ventana_f)
            finally:
                liberar_conexion(conn)
            if creados:
                messagebox.showinfo("Faltantes",
                                    "%d movimiento(s) registrados en el banco como "
                                    "MANUAL y conciliados." % creados, parent=ventana_f)
                _quitar_faltantes(seleccion)
                try:
                    self.generar_reporte()
                except Exception:
                    pass
            elif ya_estaba:
                messagebox.showinfo("Faltantes",
                                    "Esos %d movimiento(s) ya estaban registrados como "
                                    "manual: no se duplicó nada." % ya_estaba,
                                    parent=ventana_f)
                _quitar_faltantes(seleccion)
                try:
                    self.generar_reporte()
                except Exception:
                    pass

        def _ignorar():
            seleccion = _seleccion()
            if not seleccion:
                messagebox.showinfo("Faltantes", "Seleccione uno o más movimientos.",
                                    parent=ventana_f)
                return
            if not messagebox.askyesno(
                    "Ignorar",
                    "¿Marcar %d movimiento(s) como ignorados?\n\n"
                    "No se registrará nada en Compras ni en el banco; solo se "
                    "dejará constancia en la auditoría." % len(seleccion),
                    parent=ventana_f):
                return
            conn = conectar_db(silencioso=True)
            if not conn:
                return
            guardados = 0
            guardados_iids = []
            try:
                with conn.cursor() as c:
                    for iid in seleccion:
                        m = faltantes[int(iid)]
                        c.execute("""INSERT INTO conciliacion_auditoria
                            (banco, cuenta, fecha_banco, descripcion_banco, importe_banco,
                             sentido, estado, confianza, reglas, observacion,
                             origen_decision, creado, activo)
                            VALUES (%s,%s,%s,%s,%s,%s,'IGNORADO_BANCO',0,%s,%s,'manual',%s,TRUE)""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), str(m.get("descripcion") or ""),
                                   abs(float(m.get("monto") or 0)),
                                   "ingreso" if float(m.get("monto") or 0) > 0 else "egreso",
                                   "movimiento del banco sin asiento contable",
                                   "marcado como ignorado por el usuario",
                                   datetime.now().strftime("%Y-%m-%d %H:%M")))
                        # 🚫 Persistir el ignorado: así la misma línea no vuelve a
                        #     aparecer en la conciliación de ESTE mes ni de los siguientes.
                        self._registrar_ignorado(c, banco, m)
                        guardados += 1
                        guardados_iids.append(iid)
                conn.commit()
            except Exception as error:
                print("[Conciliación] ignorar faltantes:", error)
            finally:
                liberar_conexion(conn)
            messagebox.showinfo("Faltantes", "%d movimiento(s) marcados como ignorados."
                                % guardados, parent=ventana_f)
            _quitar_faltantes(guardados_iids)
            try:
                self.generar_reporte()
            except Exception:
                pass

        def _integrar_sin_factura():
            """Integra al sistema TODAS las líneas del banco que no tienen factura.

            Crea un asiento MANUAL conciliado por cada una, marcado con
            tabla='sin_factura'. Después salen de la lista de faltantes y quedan en
            verde. Cuando más adelante se cargue la factura, se podrá enlazar.
            """
            objetivos = [m for m in faltantes
                         if not facturas_por_importe.get(
                             round(abs(float(m.get("monto") or 0)), 2))]
            if not objetivos:
                messagebox.showinfo(
                    "Faltantes",
                    "Todas las líneas pendientes ya tienen una factura en el sistema.\n"
                    "Registrá el pago desde Compras o aprobalas en la conciliación.",
                    parent=ventana_f)
                return
            total = sum(float(m.get("monto") or 0) for m in objetivos)
            if not messagebox.askyesno(
                    "Integrar al sistema",
                    "¿Integrar %d línea(s) del estado de cuenta SIN factura?\n\n"
                    "• Se crean como asientos MANUALES, conciliados y marcados\n"
                    "  como «sin factura» (no tocan Compras ni Ventas).\n"
                    "• Salen de la lista de faltantes y quedan en verde.\n"
                    "• Cuando cargues la factura más adelante, la podrás enlazar.\n\n"
                    "Neto: %s" % (len(objetivos), formatear_moneda(total)),
                    parent=ventana_f):
                return
            conn = conectar_db(silencioso=True)
            if not conn:
                return
            creados = 0
            ya_estaba = 0
            try:
                with conn.cursor() as c:
                    for m in objetivos:
                        monto = float(m.get("monto") or 0)
                        c.execute("""SELECT 1 FROM conciliacion_bancaria
                                     WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                       AND COALESCE(cuenta,'') = %s
                                       AND COALESCE(fecha,'') = %s
                                       AND ABS(COALESCE(monto,0) - %s) < 0.005 LIMIT 1""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), monto))
                        if c.fetchone():
                            ya_estaba += 1
                            continue
                        c.execute("""INSERT INTO conciliacion_bancaria
                            (banco, cuenta, fecha, descripcion, monto, tipo, origen,
                             tabla, estado, fecha_conciliacion)
                            VALUES (%s,%s,%s,%s,%s,%s,'manual','sin_factura','conciliado',%s)""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), str(m.get("descripcion") or ""),
                                   monto, "ingreso" if monto > 0 else "egreso",
                                   datetime.now().strftime("%d/%m/%Y")))
                        c.execute("""INSERT INTO conciliacion_auditoria
                            (banco, cuenta, fecha_banco, descripcion_banco, importe_banco,
                             sentido, estado, confianza, reglas, observacion,
                             origen_decision, creado, activo)
                            VALUES (%s,%s,%s,%s,%s,%s,'INTEGRADO_SIN_FACTURA',100,%s,%s,
                                    'auto',%s,TRUE)""",
                                  (banco.get("banco", ""), banco.get("cuenta", ""),
                                   str(m.get("fecha") or ""), str(m.get("descripcion") or ""),
                                   abs(monto), "ingreso" if monto > 0 else "egreso",
                                   "línea del estado de cuenta sin factura",
                                   "integrado sin comprobante",
                                   datetime.now().strftime("%Y-%m-%d %H:%M")))
                        creados += 1
                conn.commit()
            except Exception as error:
                print("[Conciliación] integrar sin factura:", error)
                try:
                    conn.rollback()
                except Exception:
                    pass
                messagebox.showerror("Faltantes", "No se pudo integrar:\n%s" % error,
                                     parent=ventana_f)
            finally:
                liberar_conexion(conn)
            if creados:
                messagebox.showinfo(
                    "Faltantes",
                    "%d línea(s) integradas al sistema como asiento MANUAL «sin factura».\n"
                    "Quedaron conciliadas.%s"
                    % (creados, ("\n%d ya estaban registradas." % ya_estaba) if ya_estaba else ""),
                    parent=ventana_f)
                _quitar_faltantes(objetivos_iids)
                try:
                    self.generar_reporte()
                except Exception:
                    pass
            elif ya_estaba:
                messagebox.showinfo(
                    "Faltantes",
                    "Esas %d línea(s) ya estaban registradas como manual: no se duplicó nada."
                    % ya_estaba, parent=ventana_f)
                _quitar_faltantes(objetivos_iids)
                try:
                    self.generar_reporte()
                except Exception:
                    pass

        pie = ctk.CTkFrame(ventana_f)
        pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(pie, text="➕ Registrar en banco", width=190, fg_color="#1e8449",
                      hover_color="#145a32", command=_registrar_en_banco).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="✅ Integrar todas sin factura", width=240, fg_color="#2874a6",
                      hover_color="#1b4f72",
                      command=_integrar_sin_factura).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="🚫 Marcar como ignorados", width=210, fg_color="#7d6608",
                      hover_color="#5b4a06", command=_ignorar).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=ventana_f.destroy).pack(side="right", padx=6)

    def _mostrar_reporte_conciliacion(self, propuestas, movimientos, asientos, ventana):
        """📊 Reporte A–I de la conciliación (con exportación a Excel)."""
        try:
            import conciliacion_inteligente as ci
        except Exception as error:
            messagebox.showerror("Reporte", str(error), parent=self.parent_frame)
            return
        grupos = ci.reporte(propuestas, movimientos, asientos, ventana_dias=ventana)
        ventana_rep = ctk.CTkToplevel(self.parent_frame)
        ventana_rep.title("📊 Reporte de conciliación")
        ventana_rep.geometry("900x560")
        try:
            ventana_rep.transient(self.parent_frame)
        except Exception:
            pass
        traer_al_frente(ventana_rep)
        marco = ctk.CTkFrame(ventana_rep)
        marco.pack(fill="both", expand=True, padx=10, pady=10)
        total = sum(len(v) for v in grupos.values())
        ctk.CTkLabel(marco, text="Reporte de conciliación bancaria",
                     font=(FAMILIA, 16, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        for clave in sorted(grupos):
            ctk.CTkLabel(marco, text="%s · %-46s %d" % (clave, ci.CATEGORIAS[clave], len(grupos[clave])),
                         font=(FAMILIA, 12), anchor="w").pack(anchor="w", padx=16, pady=1)
        ctk.CTkLabel(marco, text="\nTotal de partidas analizadas: %d" % total,
                     font=(FAMILIA, 12, "bold")).pack(anchor="w", padx=16, pady=(8, 0))

        def _exportar():
            try:
                import openpyxl
                from openpyxl.styles import Font
            except Exception as error:
                messagebox.showerror("Reporte", "Falta openpyxl: %s" % error, parent=ventana_rep)
                return
            ruta = seleccionar_archivo_guardar(titulo="Guardar reporte de conciliación",
                                               nombre="Reporte conciliacion.xlsx")
            if not ruta:
                return
            libro = openpyxl.Workbook()
            hoja = libro.active
            hoja.title = "Reporte"
            hoja["A1"] = "REPORTE DE CONCILIACIÓN BANCARIA"
            hoja["A1"].font = Font(bold=True, size=13)
            fila = 3
            for clave in sorted(grupos):
                hoja.cell(row=fila, column=1, value="%s · %s" % (clave, ci.CATEGORIAS[clave])).font = Font(bold=True)
                fila += 1
                for item in grupos[clave]:
                    if "descripcion_banco" in item:
                        hoja.cell(row=fila, column=1, value=str(item.get("fecha_banco")))
                        hoja.cell(row=fila, column=2, value=item.get("descripcion_banco"))
                        hoja.cell(row=fila, column=3, value=item.get("importe_banco"))
                        hoja.cell(row=fila, column=4, value=str(item.get("fecha_contable")))
                        hoja.cell(row=fila, column=5, value=item.get("descripcion_contable"))
                        hoja.cell(row=fila, column=6, value=item.get("estado"))
                        hoja.cell(row=fila, column=7, value=item.get("confianza"))
                    else:
                        hoja.cell(row=fila, column=1, value=str(item.get("fecha")))
                        hoja.cell(row=fila, column=2, value=str(item.get("descripcion")))
                        hoja.cell(row=fila, column=3, value=item.get("monto"))
                    fila += 1
                fila += 1
            libro.save(ruta)
            messagebox.showinfo("Reporte", "Reporte guardado en:\n%s" % ruta, parent=ventana_rep)

        pie = ctk.CTkFrame(ventana_rep)
        pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(pie, text="📥 Exportar a Excel", width=170, fg_color="#1e8449",
                      hover_color="#145a32", command=_exportar).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=ventana_rep.destroy).pack(side="right", padx=6)

    def _mostrar_validacion_saldos(self, banco, propuestas):
        """📉 Valida los saldos del banco contra el sistema y explica la diferencia."""
        try:
            import conciliacion_inteligente as ci
        except Exception as error:
            messagebox.showerror("Saldos", str(error), parent=self.parent_frame)
            return
        saldos = []
        conn = conectar_db(silencioso=True)
        if conn:
            try:
                with conn.cursor() as c:
                    c.execute("""SELECT saldo FROM conciliacion_movimientos_pdf
                                 WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                   AND saldo IS NOT NULL ORDER BY id""",
                              (banco.get("banco", ""),))
                    saldos = [float(f[0]) for f in c.fetchall() if f[0] is not None]
            except Exception as error:
                print("[Conciliación] saldos:", error)
            finally:
                liberar_conexion(conn)
        inicial_banco = saldos[0] if saldos else 0.0
        final_banco = saldos[-1] if saldos else 0.0
        asientos = self._asientos_para_conciliar(banco)
        try:
            neto_sistema = round(sum(float(a.get("monto") or 0) for a in asientos), 2)
        except Exception:
            neto_sistema = 0.0
        resultado = ci.validar_saldos(inicial_banco, final_banco, 0, neto_sistema, propuestas)

        ventana_s = ctk.CTkToplevel(self.parent_frame)
        ventana_s.title("📉 Validación de saldos · %s" % banco.get("banco", ""))
        ventana_s.geometry("620x420")
        try:
            ventana_s.transient(self.parent_frame)
        except Exception:
            pass
        traer_al_frente(ventana_s)
        marco = ctk.CTkFrame(ventana_s)
        marco.pack(fill="both", expand=True, padx=12, pady=12)
        ctk.CTkLabel(marco, text="Validación de saldos", font=(FAMILIA, 16, "bold")).pack(anchor="w", pady=(6, 10))
        filas = [("Saldo inicial del banco", resultado["saldo_inicial_banco"]),
                 ("Saldo final del banco", resultado["saldo_final_banco"]),
                 ("Importe conciliado", resultado["importe_conciliado"]),
                 ("Neto del sistema (asientos)", resultado["saldo_final_sistema"]),
                 ("Diferencia banco − sistema", resultado["diferencia_final"])]
        for etiqueta, valor in filas:
            fila = ctk.CTkFrame(marco, fg_color="transparent")
            fila.pack(fill="x", padx=10, pady=2)
            ctk.CTkLabel(fila, text=etiqueta, font=(FAMILIA, 12), width=280,
                         anchor="w").pack(side="left")
            ctk.CTkLabel(fila, text="S/ %s" % formatear_moneda(valor),
                         font=(FAMILIA, 12, "bold"), anchor="e").pack(side="right")
        explicacion = ("Los saldos coinciden." if abs(resultado["diferencia_final"]) < 0.01 else
                       "La diferencia corresponde a partidas en tránsito: movimientos del banco "
                       "sin asiento contable y/o asientos sin movimiento bancario (ver Reporte).")
        ctk.CTkLabel(marco, text=explicacion, font=(FAMILIA, 11), wraplength=560,
                     justify="left", text_color="#7f8c8d").pack(anchor="w", padx=12, pady=(12, 0))
        ctk.CTkButton(marco, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=ventana_s.destroy).pack(pady=14)

    def _historial_conciliaciones(self, banco):
        """🕘 Historial de decisiones con opción de REVERTIR."""
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Historial", "Sin conexión a la base.", parent=self.parent_frame)
            return
        filas = []
        try:
            with conn.cursor() as c:
                c.execute("""SELECT id, fecha_banco, descripcion_banco, importe_banco,
                                    fecha_contable, descripcion_contable, estado, confianza,
                                    creado, id_asiento
                             FROM conciliacion_auditoria
                             WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                             ORDER BY id DESC LIMIT 300""", (banco.get("banco", ""),))
                filas = c.fetchall()
        except Exception as error:
            print("[Conciliación] historial:", error)
        finally:
            liberar_conexion(conn)

        ventana_h = ctk.CTkToplevel(self.parent_frame)
        ventana_h.title("🕘 Historial de conciliaciones · %s" % banco.get("banco", ""))
        ventana_h.geometry("1000x520")
        try:
            ventana_h.transient(self.parent_frame)
        except Exception:
            pass
        traer_al_frente(ventana_h)
        marco = ctk.CTkFrame(ventana_h)
        marco.pack(fill="both", expand=True, padx=10, pady=10)
        columnas = ("fecha", "desc_banco", "importe", "estado", "confianza", "creado")
        tabla = ttk.Treeview(marco, columns=columnas, show="headings", height=16)
        preparar_colores_montos(tabla)
        for col, titulo, ancho in (("fecha", "Fecha banco", 100), ("desc_banco", "Descripción banco", 300),
                                   ("importe", "Importe", 90), ("estado", "Estado", 110),
                                   ("confianza", "Conf.", 60), ("creado", "Registrado", 130)):
            tabla.heading(col, text=titulo)
            tabla.column(col, width=ancho)
        tabla.pack(fill="both", expand=True, side="left")
        datos = {}
        for f in filas:
            iid = str(f[0])
            datos[iid] = f
            tabla.insert("", "end", iid=iid, values=(str(f[1])[:10], str(f[2])[:50],
                                                     formatear_monto(f[3]), f[6],
                                                     "%d%%" % float(f[7] or 0), str(f[8])[:16]))

        def _revertir():
            seleccion = tabla.selection()
            if not seleccion:
                messagebox.showinfo("Historial", "Seleccione una conciliación.", parent=ventana_h)
                return
            fila = datos[seleccion[0]]
            if fila[6] != "APROBADO":
                messagebox.showinfo("Historial", "Solo se puede revertir una conciliación APROBADA.",
                                    parent=ventana_h)
                return
            if not messagebox.askyesno("Revertir",
                                       "¿Revertir esta conciliación?\n\n%s\nS/ %.2f"
                                       % (str(fila[4])[:40], float(fila[3] or 0)),
                                       parent=ventana_h):
                return
            self._revertir_conciliacion(int(seleccion[0]), fila[9], fila[1])
            ventana_h.destroy()
            self._historial_conciliaciones(banco)

        pie = ctk.CTkFrame(ventana_h)
        pie.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(pie, text="↩ Revertir la seleccionada", width=200, fg_color="#922b21",
                      hover_color="#641e16", command=_revertir).pack(side="left", padx=6)
        ctk.CTkButton(pie, text="Cerrar", width=100, fg_color="#7f8c8d",
                      hover_color="#5d6d6e", command=ventana_h.destroy).pack(side="right", padx=6)

    def _revertir_conciliacion(self, id_auditoria, id_asiento, fecha_banco):
        """Revierte una conciliación: deja el asiento como pendiente y audita."""
        conn = conectar_db(silencioso=True)
        if not conn:
            return False
        try:
            with conn.cursor() as c:
                c.execute("""UPDATE conciliacion_auditoria
                             SET estado='REVERTIDO', activo=FALSE,
                                 modificado=%s
                             WHERE id=%s""",
                          (datetime.now().strftime("%Y-%m-%d %H:%M"), id_auditoria))
                if id_asiento:
                    c.execute("""UPDATE conciliacion_bancaria
                                 SET estado='pendiente', fecha_conciliacion=NULL
                                 WHERE id=%s""", (id_asiento,))
                c.execute("""INSERT INTO conciliacion_auditoria
                    (id_asiento, fecha_banco, estado, confianza, reglas, origen_decision,
                     creado, activo)
                    VALUES (%s,%s,'REVERTIDO',0,%s,'manual',%s,TRUE)""",
                          (id_asiento, fecha_banco,
                           "reversión de la conciliación %s" % id_auditoria,
                           datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
            # 🧹 Al revertir, el asiento vuelve a estar pendiente: se refresca la caché.
            self._cache_asientos = None
            return True
        except Exception as error:
            print("[Conciliación] no se pudo revertir:", error)
            messagebox.showerror("Revertir", "No se pudo revertir:\n%s" % error,
                                 parent=self.parent_frame)
            return False
        finally:
            liberar_conexion(conn)

    def _periodo_de_conciliacion(self):
        """Rango de fechas del mes elegido (±5 días) para sincronizar solo ese período."""
        import datetime as _dt
        meses = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
                 "julio": 7, "agosto": 8, "setiembre": 9, "septiembre": 9, "octubre": 10,
                 "noviembre": 11, "diciembre": 12}
        try:
            etiqueta = str(self.cmb_mes.get() or "").strip()
        except Exception:
            etiqueta = ""
        if not etiqueta or "todos" in etiqueta.lower():
            return None, None
        partes = etiqueta.split()
        if len(partes) < 2:
            return None, None
        mes = meses.get(partes[0].strip().lower())
        try:
            anio = int(partes[-1])
        except Exception:
            return None, None
        if not mes:
            return None, None
        desde = _dt.date(anio, mes, 1) - _dt.timedelta(days=5)
        hasta = (_dt.date(anio + 1, 1, 1) if mes == 12 else _dt.date(anio, mes + 1, 1))             + _dt.timedelta(days=6)
        return desde, hasta

    def _en_periodo(self, fecha, desde, hasta):
        """True si la fecha (texto) cae dentro del período (o si no hay filtro)."""
        if not desde or not hasta:
            return True
        import datetime as _dt
        limpio = str(fecha or "").strip().replace("-", "/")
        partes = [x for x in limpio.split("/") if x.strip()]
        try:
            if len(partes) >= 3:
                a, b, c = int(partes[0]), int(partes[1]), int(partes[2])
                real = _dt.date(a, b, c) if a > 31 else _dt.date(c, b, a)
                return desde <= real <= hasta
        except Exception:
            pass
        return False

    def _asientos_para_conciliar(self, banco, desde=None, hasta=None):
        """Sincroniza pagos/facturas App_grifo a conciliacion_bancaria y los devuelve.

        ⚠️ Esta función ESCRIBE. La sincronización va acotada a un período: si no
        hay mes elegido («Todos los meses»), se usa el mes actual. Antes, en ese
        caso se insertaba TODO el histórico (miles de filas) cada vez que se abría
        la ventana inteligente.

        'desde'/'hasta' se pueden pasar ya calculados: así la ventana puede llamarla
        desde un hilo sin tocar Tkinter (leer el combo desde otro hilo es peligroso).
        """
        asientos = []
        if desde is None or hasta is None:
            _desde, _hasta = self._periodo_de_conciliacion()
        else:
            _desde, _hasta = desde, hasta
        if not (_desde and _hasta):
            import datetime as _dt
            _hoy = _dt.date.today()
            _desde = _dt.date(_hoy.year, _hoy.month, 1) - _dt.timedelta(days=5)
            _sig_mes = (_hoy.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
            _hasta = _sig_mes + _dt.timedelta(days=6)
        nombre_banco = str(banco.get("banco") or "")
        # ⚡ caché: evita re-sincronizar y re-consultar en cada repintado (tarda ~10 s)
        try:
            _clave_cache = "%s|%s" % (nombre_banco, _desde)
            _cache = getattr(self, "_cache_asientos", None)
            if _cache and _cache.get("clave") == _clave_cache:
                return _cache.get("datos") or []
        except Exception:
            _clave_cache = ""
        conn = conectar_db(silencioso=True)
        if not conn:
            return asientos
        # 🚫 Movimientos que el usuario ELIMINÓ: no se vuelven a crear al
        #    sincronizar (era lo que hacía que un asiento borrado reapareciera).
        try:
            _ign = self.cargar_ignorados(banco) or {}
            ign_sistema = _ign.get("sistema", set())
        except Exception:
            ign_sistema = set()
        try:
            with conn.cursor() as c:
                # 1) pagos a proveedores (el pago es el que sale del banco)
                try:
                    c.execute("""SELECT p.id, p.fecha_pago, p.monto_pagado,
                                        COALESCE(p.proveedor_nombre,''), p.cuenta_origen,
                                        COALESCE(p.numero_operacion,'')
                                 FROM pagos_comprobantes p
                                 WHERE COALESCE(p.monto_pagado,0) > 0
                                   AND NOT EXISTS (
                                       SELECT 1 FROM facturas_recibidas f
                                       WHERE f.id = p.id_factura
                                         AND f.categoria = 'Combustible y Peajes')
                                   -- 🚫 Los PAGOS BANCO son el espejo que crea un
                                   --    egreso MANUAL: el manual ya es el asiento, así
                                   --    que no se duplica acá (era el «2 candidatos»).
                                   AND NOT EXISTS (
                                       SELECT 1 FROM facturas_recibidas f2
                                       WHERE f2.id = p.id_factura
                                         AND COALESCE(f2.tipo_documento,'') = 'PAGO BANCO')""")
                    for f in c.fetchall():
                        if not self._en_periodo(f[1], _desde, _hasta):
                            continue
                        cuenta = str(f[4] or "")
                        if cuenta and nombre_banco and nombre_banco.upper() not in cuenta.upper():
                            continue
                        if self._clave_ignorado("sistema", "pagos_comprobantes", f[0], f[1],
                                                -abs(float(f[2] or 0)), f[3]) in ign_sistema:
                            continue
                        c.execute("""SELECT id FROM conciliacion_bancaria
                                     WHERE origen='s:pagos_comprobantes' AND id_movimiento=%s""",
                                  (f[0],))
                        if c.fetchone():
                            continue
                        c.execute("""INSERT INTO conciliacion_bancaria
                            (banco, cuenta, fecha, descripcion, monto, tipo, origen,
                             id_movimiento, estado)
                            VALUES (%s,%s,%s,%s,%s,'egreso','s:pagos_comprobantes',%s,'')""",
                                  (nombre_banco, cuenta, f[1], f[3],
                                   -abs(float(f[2] or 0)), f[0]))
                except Exception as error:
                    print("[Conciliación] sincronizar pagos:", error)
                    conn.rollback()
                    with conn.cursor() as c:
                        pass
                # 2) cobros de clientes
                try:
                    with conn.cursor() as c2:
                        c2.execute("""SELECT p.id, p.fecha_pago, p.monto_pagado,
                                             COALESCE(p.cliente_nombre,''), p.cuenta_destino
                                      FROM pagos_clientes p
                                      WHERE COALESCE(p.monto_pagado,0) > 0""")
                        for f in c2.fetchall():
                            if not self._en_periodo(f[1], _desde, _hasta):
                                continue
                            cuenta = str(f[4] or "")
                            if cuenta and nombre_banco and nombre_banco.upper() not in cuenta.upper():
                                continue
                            if self._clave_ignorado("sistema", "pagos_clientes", f[0], f[1],
                                                    abs(float(f[2] or 0)), f[3]) in ign_sistema:
                                continue
                            c2.execute("""SELECT id FROM conciliacion_bancaria
                                          WHERE origen='s:pagos_clientes' AND id_movimiento=%s""",
                                       (f[0],))
                            if c2.fetchone():
                                continue
                            c2.execute("""INSERT INTO conciliacion_bancaria
                                (banco, cuenta, fecha, descripcion, monto, tipo, origen,
                                 id_movimiento, estado)
                                VALUES (%s,%s,%s,%s,%s,'ingreso','s:pagos_clientes',%s,'')""",
                                       (nombre_banco, cuenta, f[1], f[3],
                                        abs(float(f[2] or 0)), f[0]))
                except Exception as error:
                    print("[Conciliación] sincronizar cobros:", error)
                    conn.rollback()
                # 3) facturas del App_grifo SIN pago registrado -> van directo al banco
                # 💳 la cuenta del App Grifo se define en Configuración General
                cuenta_grifo = ""
                try:
                    from config_nube import cargar_cuenta_grifo
                    cuenta_grifo = str(cargar_cuenta_grifo() or "")
                except Exception as error:
                    print("[Conciliación] cuenta App Grifo:", error)
                coincide_grifo = True
                if cuenta_grifo:
                    cuenta_banco = str(banco.get("cuenta") or "")
                    coincide_grifo = (nombre_banco.upper() in cuenta_grifo.upper()
                                      or (cuenta_banco and cuenta_banco in cuenta_grifo)
                                      or cuenta_grifo.upper() in nombre_banco.upper())
                    print("[Conciliación] cuenta App Grifo configurada:", cuenta_grifo,
                          "· coincide:", coincide_grifo)
                if coincide_grifo:
                    try:
                        with conn.cursor() as c3:
                            c3.execute("""SELECT f.id, f.fecha, f.proveedor, f.total
                                          FROM facturas_recibidas f
                                          WHERE f.categoria = 'Combustible y Peajes'
                                            AND COALESCE(f.total,0) > 0""")
                            for f in c3.fetchall():
                                if not self._en_periodo(f[1], _desde, _hasta):
                                    continue
                                if self._clave_ignorado("sistema", "facturas_recibidas", f[0],
                                                        f[1], -abs(float(f[3] or 0)),
                                                        f[2]) in ign_sistema:
                                    continue
                                c3.execute("""SELECT id FROM conciliacion_bancaria
                                              WHERE origen='s:facturas_recibidas'
                                                AND id_movimiento=%s""", (f[0],))
                                if c3.fetchone():
                                    continue
                                c3.execute("""INSERT INTO conciliacion_bancaria
                                    (banco, cuenta, fecha, descripcion, monto, tipo, origen,
                                     id_movimiento, estado)
                                    VALUES (%s,%s,%s,%s,%s,'egreso','s:facturas_recibidas',%s,'')""",
                                           (nombre_banco, "", f[1], f[2],
                                            -abs(float(f[3] or 0)), f[0]))
                    except Exception as error:
                        print("[Conciliación] sincronizar App_grifo:", error)
                        conn.rollback()
                try:
                    conn.commit()
                except Exception:
                    pass
                # 4) asientos del banco (ya incluyen lo sincronizado)
                # 🚫 Se excluyen los YA conciliados (por la vía clásica o por el
                #    motor) para que no se vuelvan a proponer y se puedan cruzar
                #    dos veces con la misma línea del estado de cuenta.
                c.execute("""SELECT id, fecha, descripcion, monto
                             FROM conciliacion_bancaria
                             WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                               AND COALESCE(estado,'') <> 'conciliado'
                               AND id NOT IN (
                                   SELECT id_asiento FROM conciliacion_auditoria
                                   WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                     AND estado = 'APROBADO'
                                     AND COALESCE(activo, TRUE) = TRUE
                                     AND id_asiento IS NOT NULL)
                             ORDER BY fecha""", (nombre_banco, nombre_banco))
                for f in c.fetchall():
                    asientos.append({"id": f[0], "id_asiento": f[0],
                                     "tabla": "conciliacion_bancaria",
                                     "fecha": f[1], "descripcion": f[2], "monto": f[3],
                                     "banco": nombre_banco})
        except Exception as error:
            print("[Conciliación] asientos:", error)
        finally:
            liberar_conexion(conn)
        try:
            if _clave_cache:
                self._cache_asientos = {'clave': _clave_cache, 'datos': asientos}
        except Exception:
            pass
        return asientos

    def _aplicar_conciliacion_inteligente(self, banco, propuesta, decision, origen="manual"):
        """Marca el asiento como conciliado (solo si se APRUEBA) y audita.

        'origen' queda en la auditoría: 'manual' si lo aprobó el usuario,
        'auto' si fue una coincidencia del 100 % aplicada automáticamente.
        """
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Conciliación", "Sin conexión a la base de datos.",
                                 parent=self.parent_frame)
            return False
        try:
            with conn.cursor() as c:
                _id_asiento = propuesta.get("id_asiento")
                _id_int = _id_asiento if isinstance(_id_asiento, int) else None
                if decision == "APROBADO" and _id_int:
                    c.execute("""UPDATE conciliacion_bancaria
                                 SET estado='conciliado', fecha_conciliacion=%s
                                 WHERE id=%s""",
                              (datetime.now().strftime("%d/%m/%Y"), _id_int))
                c.execute("""INSERT INTO conciliacion_auditoria
                    (id_asiento, banco, cuenta, fecha_contable, fecha_banco, dias_desfase,
                     descripcion_contable, descripcion_banco, importe_contable, importe_banco,
                     diferencia, sentido, referencia, pagina_pdf, nivel, estado, confianza,
                     reglas, observacion, accion_sugerida, origen_decision, aprobado_por,
                     creado, activo)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (_id_int, propuesta["banco"], propuesta.get("cuenta", ""),
                     propuesta["fecha_contable"], propuesta["fecha_banco"],
                     propuesta["dias_desfase"], propuesta["descripcion_contable"],
                     propuesta["descripcion_banco"], propuesta["importe_contable"],
                     propuesta["importe_banco"], propuesta.get("diferencia", 0),
                     propuesta["sentido"],
                     propuesta.get("referencia") or (str(_id_asiento) if not _id_int else ""),
                     propuesta.get("pagina_pdf"), propuesta["nivel"], decision,
                     propuesta["confianza"], propuesta["reglas"],
                     propuesta.get("observacion", ""), propuesta.get("accion_sugerida", ""),
                     origen or "manual", "", datetime.now().strftime("%Y-%m-%d %H:%M"), True))
            conn.commit()
            # 🧹 El asiento cambió de estado: la lista cacheada de asientos ya no
            #    sirve (si no, el recién conciliado volvía a salir como candidato).
            self._cache_asientos = None
            return True
        except Exception as error:
            try:
                conn.rollback()
            except Exception:
                pass
            self._cache_asientos = None
            print("[Conciliación] no se pudo aplicar:", error)
            messagebox.showerror("Conciliación", "No se pudo aplicar:\n%s" % error,
                                 parent=self.parent_frame)
            return False
        finally:
            liberar_conexion(conn)

    def _auto_seleccionar_periodo(self, movs, texto_pdf=""):
        """Detecta el mes del estado de cuenta, lo selecciona y baja los movimientos."""
        try:
            import datetime as _dt

            def _fecha_de(txt):
                if hasattr(txt, "year") and hasattr(txt, "month"):
                    return _dt.date(txt.year, txt.month, getattr(txt, "day", 1))
                limpio = str(txt or "").strip().replace("-", "/")
                partes = [x for x in limpio.split("/") if x.strip()]
                try:
                    if len(partes) >= 3:
                        a, b, c = int(partes[0]), int(partes[1]), int(partes[2])
                        return _dt.date(a, b, c) if a > 31 else _dt.date(c, b, a)
                except Exception:
                    pass
                return None
            if not (movs or []):
                movs = getattr(self, "movimientos_pdf", []) or []
            fechas = [f for f in (_fecha_de(m.get("fecha")) for m in (movs or [])) if f]
            if not fechas:
                print("[Conciliación] no se pudo leer la fecha de los movimientos del PDF")
                return
            ultima = max(fechas)
            # 🔑 Se compara por número de mes («2026-09»), no por el nombre: antes
            #    buscaba «Setiembre» y el desplegable dice «Septiembre», así que en
            #    septiembre nunca se seleccionaba el mes solo.
            clave_pdf = "%04d-%02d" % (ultima.year, ultima.month)
            etiqueta = "%s %d" % (NOMBRES_MESES[ultima.month - 1], ultima.year)
            try:
                valores = list(self.cmb_mes.cget("values") or [])
                elegido = ""
                for valor in valores:
                    if mes_etiqueta_a_key(valor) == clave_pdf:
                        elegido = valor
                        break
                if not elegido:
                    for valor in valores:
                        if etiqueta.lower() in str(valor).lower():
                            elegido = valor
                            break
                if elegido:
                    self.cmb_mes.set(elegido)
                    print("[Conciliación] mes detectado en el PDF:", elegido)
            except Exception as error:
                print("[Conciliación] mes del PDF:", error)
            # 🏦 detectar el banco por palabras del estado de cuenta
            try:
                pistas = " ".join(str(m.get("descripcion") or "") for m in (movs or []))
                pistas += " " + str(texto_pdf or "") + " " + str(getattr(self, "texto_pdf", "") or "")
                crudo = pistas
                pistas = pistas.upper()
                # 🔢 números de cuenta que aparecen en el PDF (ej. 297-3007235833)
                import re as _re
                cuentas_pdf = set()
                for hallado in _re.finditer(r"\d[\d\-\.]{6,}", crudo):
                    solo_digitos = _re.sub(r"\D", "", hallado.group(0))
                    if len(solo_digitos) >= 8:
                        cuentas_pdf.add(solo_digitos)
                # 1️⃣ Por NÚMERO DE CUENTA (lo más fiable).
                banco_por_cuenta = False
                for banco in (self.bancos or []):
                    cuenta_banco = _re.sub(r"\D", "", str(banco.get("cuenta") or ""))
                    if cuenta_banco and any(cuenta_banco in c or c in cuenta_banco
                                            for c in cuentas_pdf):
                        etiqueta = construir_etiqueta_banco(banco)
                        if self.cmb_banco.get() != etiqueta:
                            self.cmb_banco.set(etiqueta)
                            print("[Conciliación] banco por número de cuenta:", etiqueta)
                        banco_por_cuenta = True
                        break
                # 2️⃣ Solo si la cuenta no lo identificó, por nombre del banco.
                #    ⚠️ No se puede mirar el texto completo cuando la cuenta ya lo
                #    identificó: un estado del BCP menciona «TRANSF.BCO.INTERBANK»
                #    en cada transferencia y antes eso cambiaba el banco a Interbank.
                nombres = {"INTERBANK": "INTERBANK", "BCP": "BCP", "BANCO DE CREDITO": "BCP",
                           "BBVA": "BBVA", "CONTINENTAL": "BBVA", "SCOTIABANK": "SCOTIABANK",
                           "BANBIF": "BANBIF", "PICHINCHA": "PICHINCHA", "NACION": "NACION"}
                detectado = ""
                if not banco_por_cuenta:
                    for clave, nombre in nombres.items():
                        if clave in pistas:
                            detectado = nombre
                            break
                if detectado:
                    for banco in (self.bancos or []):
                        etiqueta = construir_etiqueta_banco(banco)
                        if detectado in etiqueta.upper():
                            if self.cmb_banco.get() != etiqueta:
                                self.cmb_banco.set(etiqueta)
                                print("[Conciliación] banco detectado en el PDF:", etiqueta)
                            break
            except Exception as error:
                print("[Conciliación] banco del PDF:", error)
            try:
                self.actualizar_saldos_boton()
                print("[Conciliación] movimientos del sistema actualizados para ese período.")
            except Exception as error:
                print("[Conciliación] carga automática:", error)
        except Exception as error:
            print("[Conciliación] selección automática:", error)

    def _datos_del_estado_cuenta(self, banco, movs):
        """Resumen del estado de cuenta a guardar y qué hay ya cargado en esa cuenta."""
        fechas = sorted(f for f in (normalizar_fecha(m.get("fecha")) for m in movs) if f)
        datos = {
            "banco": banco.get("banco", "") or "",
            "cuenta": banco.get("cuenta", "") or "",
            "periodo": periodo_de_movimientos(movs),
            "lineas": len(movs),
            "neto": round(sum(float(m.get("monto") or 0) for m in movs), 2),
            "desde": fechas[0] if fechas else "",
            "hasta": fechas[-1] if fechas else "",
            "previo_lineas": 0,
            "previo_neto": 0.0,
        }
        conn = conectar_db(silencioso=True)
        if not conn:
            return datos
        try:
            with conn.cursor() as c:
                c.execute("""SELECT COUNT(*), COALESCE(SUM(monto), 0)
                             FROM conciliacion_movimientos_pdf
                             WHERE banco = %s AND COALESCE(cuenta,'') = %s
                               AND COALESCE(periodo,'') = %s""",
                          (datos["banco"], datos["cuenta"], datos["periodo"]))
                fila = c.fetchone() or (0, 0)
                datos["previo_lineas"] = int(fila[0] or 0)
                datos["previo_neto"] = round(float(fila[1] or 0), 2)
        except Exception as error:
            print("[Conciliación] resumen del estado de cuenta:", error)
        finally:
            liberar_conexion(conn)
        return datos

    def _banco_que_parece_el_pdf(self, movs, texto_pdf=""):
        """Nombre del banco que se deduce del PDF (número de cuenta o palabras)."""
        try:
            crudo = " ".join(str(m.get("descripcion") or "") for m in (movs or []))
            crudo += " " + str(texto_pdf or "") + " " + str(getattr(self, "texto_pdf", "") or "")
            numeros = set()
            for hallado in re.finditer(r"\d[\d\-\.]{6,}", crudo):
                solo = re.sub(r"\D", "", hallado.group(0))
                if len(solo) >= 8:
                    numeros.add(solo)
            for banco in (self.bancos or []):
                cuenta = re.sub(r"\D", "", str(banco.get("cuenta") or ""))
                if cuenta and any(cuenta in n or n in cuenta for n in numeros):
                    return construir_etiqueta_banco(banco)
            pistas = crudo.upper()
            for clave, nombre in (("INTERBANK", "INTERBANK"), ("BANCO DE CREDITO", "BCP"),
                                  ("BCP", "BCP"), ("BBVA", "BBVA"), ("CONTINENTAL", "BBVA"),
                                  ("SCOTIABANK", "SCOTIABANK"), ("BANBIF", "BANBIF"),
                                  ("PICHINCHA", "PICHINCHA")):
                if clave in pistas:
                    for banco in (self.bancos or []):
                        if nombre in construir_etiqueta_banco(banco).upper():
                            return construir_etiqueta_banco(banco)
        except Exception as error:
            print("[Conciliación] banco del PDF:", error)
        return ""

    def _confirmar_guardado_estado_cuenta(self, banco, movs):
        """Cartel de confirmación antes de guardar el PDF. Devuelve True = guardar.

        Muestra banco, cuenta, número de líneas, fechas y neto, avisa si ese estado
        de cuenta YA estaba cargado y si el PDF parece ser de otro banco. Antes se
        guardaba directo con el banco del combo y la detección automática cambiaba
        después el combo, así que un archivo podía quedar cargado en el banco
        equivocado sin que nadie lo notara.
        """
        if not movs:
            return False
        datos = self._datos_del_estado_cuenta(banco, movs)
        etiqueta = construir_etiqueta_banco(banco)

        def _f(valor):
            return fecha_para_mostrar(valor) if valor else "—"

        texto = ("¿Guardar este estado de cuenta?\n\n"
                 "Banco:        %s\n"
                 "Cuenta:       %s\n"
                 "Período:      %s\n"
                 "Movimientos:  %d\n"
                 "Fechas:       %s al %s\n"
                 "Neto:         %s\n"
                 % (datos["banco"] or "(sin nombre)",
                    datos["cuenta"] or "(sin número de cuenta)",
                    datos["periodo"] or "(sin período)",
                    datos["lineas"], _f(datos["desde"]), _f(datos["hasta"]),
                    formatear_monto(datos["neto"])))

        if datos["previo_lineas"]:
            mismo = (datos["previo_lineas"] == datos["lineas"]
                     and abs(datos["previo_neto"] - datos["neto"]) < 0.01)
            if mismo:
                texto += ("\n✅ Este estado de cuenta YA está cargado para esta cuenta y "
                          "período (%d línea(s), neto %s). Se volverá a guardar igual.\n"
                          % (datos["previo_lineas"], formatear_monto(datos["previo_neto"])))
            else:
                texto += ("\nℹ️ Ya hay un estado de cuenta cargado para esta cuenta en "
                          "este período (%d línea(s), neto %s). Se REEMPLAZARÁ por el nuevo; "
                          "los demás meses se conservan.\n"
                          % (datos["previo_lineas"], formatear_monto(datos["previo_neto"])))

        detectado = self._banco_que_parece_el_pdf(movs)
        if detectado and detectado != etiqueta:
            texto += ("\n⚠️ El PDF parece ser de %s, pero está seleccionado %s.\n"
                      "Si lo guarda aquí, la conciliación de %s usará este archivo."
                      % (detectado, etiqueta, etiqueta))
            return messagebox.askyesno("Estado de cuenta · ¡revisar banco!", texto,
                                       parent=self.parent_frame, icon="warning")
        return messagebox.askyesno("Confirmar estado de cuenta", texto,
                                   parent=self.parent_frame)

    def _archivar_estado_cuenta(self, banco, movs, origen):
        """Copia el PDF del estado de cuenta a la carpeta sincronizada.

        Queda en «estados_cuenta/<BANCO>/<cuenta>/<AAAA-MM>.pdf», dentro de la
        carpeta que sincroniza Rclone con Google Drive. Devuelve la ruta como se
        guarda en la base (relativa si está dentro de la carpeta sincronizada).
        """
        if not origen or not os.path.isfile(origen):
            return ""
        try:
            base = obtener_ruta_base()
        except Exception:
            base = ""
        if not base:
            print("[Conciliación] El equipo no tiene carpeta de archivos autorizada: "
                  "el estado de cuenta no se archivó.")
            return ""

        def _limpio(texto):
            return re.sub(r"[^A-Za-z0-9._-]+", "_", str(texto or "")).strip("_") or "sin_dato"

        periodo = periodo_de_movimientos(movs)
        carpeta = os.path.join(base, "estados_cuenta", _limpio(banco.get("banco", "")),
                               _limpio(banco.get("cuenta", "")))
        destino = os.path.join(carpeta, "%s.pdf" % _limpio(periodo))
        try:
            os.makedirs(carpeta, exist_ok=True)
            if os.path.abspath(origen) != os.path.abspath(destino):
                shutil.copy2(origen, destino)
            print("[Conciliación] estado de cuenta archivado en:", destino)
            return ruta_para_guardar(destino) or destino
        except Exception as error:
            print("[Conciliación] no se pudo archivar el estado de cuenta:", error)
            return ""

    def _guardar_movimientos_pdf(self, banco, movs, archivo=""):
        self._cache_asientos = None      # 🧹 nuevo PDF: se refresca la caché
        """Guarda las líneas del estado de cuenta (PDF) para no perderlas al salir."""
        if not banco or not movs:
            return
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            with conn.cursor() as c:
                # 🏦 Clave del estado de cuenta: banco + CUENTA (antes solo banco:
                #    subir el PDF de la 2ª cuenta borraba el de la 1ª).
                nombre_banco = banco.get("banco", "")
                cuenta = banco.get("cuenta", "") or ""
                periodo = periodo_de_movimientos(movs)
                # 🛡️ ¿ya estaba cargado este mismo estado de cuenta? (líneas, total y período)
                try:
                    c.execute("""SELECT COUNT(*), COALESCE(SUM(monto), 0)
                                 FROM conciliacion_movimientos_pdf
                                 WHERE banco = %s AND COALESCE(cuenta,'') = %s
                                   AND COALESCE(periodo,'') = %s""",
                              (nombre_banco, cuenta, periodo))
                    fila_previa = c.fetchone() or (0, 0)
                    total_nuevo = round(sum(float(m.get("monto") or 0) for m in movs), 2)
                    if (int(fila_previa[0] or 0) == len(movs)
                            and abs(float(fila_previa[1] or 0) - total_nuevo) < 0.01
                            and len(movs) > 0):
                        print("[Conciliación] El estado de cuenta ya estaba cargado "
                              "(%d líneas · %.2f): se vuelve a guardar igual y se sigue "
                              "con la conciliación." % (len(movs), total_nuevo))
                except Exception as error_repetido:
                    print("[Conciliación] revisión de estado repetido:", error_repetido)
                # 📅 Se reemplaza SÓLO ese período: los demás meses del mismo banco
                #    y cuenta se conservan (antes se borraba todo el banco/cuenta).
                c.execute("DELETE FROM conciliacion_movimientos_pdf "
                          "WHERE banco = %s AND COALESCE(cuenta,'') = %s "
                          "AND COALESCE(periodo,'') = %s",
                          (nombre_banco, cuenta, periodo))
                # Filas viejas (cargadas antes de existir 'periodo') de ese mismo mes.
                c.execute("DELETE FROM conciliacion_movimientos_pdf "
                          "WHERE banco = %s AND COALESCE(cuenta,'') = %s "
                          "AND COALESCE(periodo,'') = '' AND fecha LIKE %s",
                          (nombre_banco, cuenta, periodo + "%"))
                if cuenta:
                    # Y las que no tienen cuenta (datos viejos) de ese mes.
                    c.execute("DELETE FROM conciliacion_movimientos_pdf "
                              "WHERE banco = %s AND COALESCE(cuenta,'') = '' "
                              "AND fecha LIKE %s",
                              (nombre_banco, periodo + "%"))
                for m in movs:
                    c.execute("""INSERT INTO conciliacion_movimientos_pdf
                        (banco, cuenta, periodo, fecha, descripcion, monto, saldo, creado,
                         archivo_ruta)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (nombre_banco, cuenta, periodo, str(m.get("fecha") or ""),
                         str(m.get("descripcion") or ""), float(m.get("monto") or 0),
                         m.get("saldo"), datetime.now().strftime("%Y-%m-%d %H:%M"),
                         archivo or ""))
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print("[Conciliación] No se pudo guardar el estado de cuenta:", e)
        finally:
            liberar_conexion(conn)

    def _cargar_movimientos_pdf(self, banco, periodo=None):
        """Líneas del estado de cuenta de este banco.

        Si se indica 'periodo' (AAAA-MM) se traen sólo las de ese mes; si no, todas.
        """
        if not banco:
            return []
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        try:
            with conn.cursor() as c:
                nombre_banco = banco.get("banco", "")
                cuenta = banco.get("cuenta", "") or ""
                sql = ("""SELECT fecha, descripcion, monto, saldo, COALESCE(periodo,'')
                          FROM conciliacion_movimientos_pdf
                          WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                            AND COALESCE(cuenta,'') = %s""")
                args = [nombre_banco, cuenta]
                if periodo:
                    # Se aceptan las filas viejas sin período si su fecha es de ese mes.
                    sql += (" AND (COALESCE(periodo,'') = %s"
                            " OR (COALESCE(periodo,'') = '' AND fecha LIKE %s))")
                    args += [periodo, periodo + "%"]
                sql += " ORDER BY id"
                c.execute(sql, tuple(args))
                filas = c.fetchall()
                return [{"fecha": f or "", "descripcion": d or "",
                         "monto": float(m or 0),
                         "saldo": (float(s) if s is not None else None),
                         "periodo": (str(per) or "") if per else ""}
                        for f, d, m, s, per in filas]
        except Exception:
            return []
        finally:
            liberar_conexion(conn)

    def subir_pdf(self):
        ruta = seleccionar_archivo_dialogo(
            titulo="Seleccionar Estado de Cuenta (PDF)",
            tipos=[("Archivos PDF", "*.pdf")])
        if not ruta:
            return

        # 🔒 PDF protegido con contraseña: se pide antes de intentar extraer el texto
        password = None
        if pdf_esta_cifrado(ruta):
            password = simpledialog.askstring(
                "PDF protegido",
                "Este estado de cuenta está protegido con contraseña.\n\n"
                "Ingrese la contraseña del PDF (suele ser el RUC o un número\n"
                "que le dio el banco):",
                parent=self.parent_frame, show="*")
            if password is None:
                self.lbl_resumen.configure(
                    text="⚠️ Conciliación cancelada: el PDF requiere contraseña.")
                return

        self.lbl_resumen.configure(text="⏳ Extrayendo texto del PDF...")
        self.parent_frame.update_idletasks()

        # El hilo solo lee y analiza el PDF; la pantalla se actualiza en el hilo
        # principal (llamar a Tk desde un hilo congela la app en macOS).
        def _leer(estado):
            texto = extraer_texto_pdf(ruta, password)
            estado["texto"] = texto or ""
            if (texto or "").strip():
                estado["movimientos"] = parsear_movimientos(texto)
                estado["saldo_final"] = detectar_saldo_final(texto)

        def _terminar(estado):
            if estado.get("error"):
                self.lbl_resumen.configure(text=f"❌ Error al leer el PDF: {estado['error']}")
                return
            if not (estado.get("texto") or "").strip():
                if password is not None:
                    self.lbl_resumen.configure(
                        text="❌ Contraseña incorrecta o PDF no legible. Vuelva a intentarlo.")
                elif pdf_esta_cifrado(ruta):
                    self.lbl_resumen.configure(
                        text="❌ El PDF está protegido con contraseña y no se pudo abrir.")
                else:
                    self.lbl_resumen.configure(
                        text="❌ No se pudo extraer texto del PDF. "
                             "Verifique que no sea un PDF escaneado (imagen).")
                return
            self.texto_pdf = estado.get("texto") or ""
            self.movimientos_pdf = estado.get("movimientos") or []
            self.saldo_final_estado = estado.get("saldo_final")
            try:
                _banco_pdf = self.banco_seleccionado()
                if _banco_pdf and self.movimientos_pdf:
                    # ✅ Confirmación: banco + cuenta + líneas + fechas + neto, y aviso
                    #    si ya estaba cargado o si el PDF parece de otro banco.
                    if not self._confirmar_guardado_estado_cuenta(_banco_pdf, self.movimientos_pdf):
                        # 🚫 Cancelado: se descarta lo leído y se vuelve a lo que hay
                        #    guardado en la base (no dejar datos sin guardar en pantalla).
                        try:
                            self.movimientos_pdf = self._cargar_movimientos_pdf(
                                _banco_pdf, mes_etiqueta_a_key(self.cmb_mes.get())) or []
                            self._banco_pdf = _banco_pdf.get("banco", "")
                            self._mes_pdf = mes_etiqueta_a_key(self.cmb_mes.get()) or ""
                        except Exception:
                            self.movimientos_pdf = []
                        self.lbl_resumen.configure(
                            text="🚫 No se guardó el estado de cuenta (cancelado).")
                        self.generar_reporte()
                        return
                    # 📎 Se archiva el PDF en la carpeta sincronizada
                    #    (estados_cuenta/<BANCO>/<cuenta>/<AAAA-MM>.pdf).
                    _archivo_pdf = self._archivar_estado_cuenta(
                        _banco_pdf, self.movimientos_pdf, ruta)
                    self._guardar_movimientos_pdf(_banco_pdf, self.movimientos_pdf,
                                                  _archivo_pdf)
                    # 🔁 Se fuerza recargar desde la base con el período correcto
                    #    (la fecha del estado recién subido puede cambiar el mes).
                    self._banco_pdf = None
                    self._mes_pdf = None
                    try:
                        self._auto_seleccionar_periodo((estado.get('movimientos') if isinstance(estado, dict) else None) or getattr(self, 'movimientos_pdf', []) or [], getattr(self, 'texto_pdf', '') or '')
                    except Exception as _e_periodo:
                        print('[Conciliación] período automático:', _e_periodo)
                    self._banco_pdf = _banco_pdf.get("banco", "")
            except Exception as _e_pdf:
                print("[Conciliación] No se pudo guardar el estado de cuenta:", _e_pdf)
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
            lado = ("|ingreso" if float(monto or 0) >= 0 else "|egreso") if tabla == "transferencias_bancarias" else ""
            return f"{tabla or ''}|{idm}{lado}"
        # 📄 Las líneas del estado de cuenta se reconocen por FECHA + IMPORTE
        #    (no por la descripción: un espacio de diferencia hacía que la línea
        #    «eliminada» volviera a aparecer al recalcular).
        return "pdf|%s|%.2f" % (normalizar_fecha(fecha), float(monto or 0))

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

    def eliminar_ocultos_definitivo(self):
        """🗑️ Borra del PDF y de la lista de ocultos las líneas del mes.

        No toca los asientos del sistema: solo elimina definitivamente las
        líneas del estado de cuenta que estaban marcadas como ocultas.
        """
        banco = self.banco_seleccionado()
        if not banco:
            return
        try:
            mes_key = mes_etiqueta_a_key(self.cmb_mes.get())
        except Exception:
            mes_key = ""
        if not mes_key:
            messagebox.showwarning("Eliminar ocultos", "Seleccione un mes.", parent=self.parent_frame)
            return
        conn = conectar_db(silencioso=True)
        if not conn:
            messagebox.showerror("Error", "Sin conexión a la base de datos.", parent=self.parent_frame)
            return
        try:
            with conn.cursor() as c:
                c.execute("""SELECT id, fecha, monto, descripcion
                             FROM conciliacion_ignorados
                             WHERE banco=%s AND origen='estado_cuenta'
                             ORDER BY fecha, id""", (banco.get("banco", ""),))
                filas = []
                for r in c.fetchall():
                    f = normalizar_fecha(r[1])
                    if f.startswith(mes_key):
                        filas.append({"id": r[0], "fecha": f, "monto": float(r[2] or 0),
                                      "descripcion": str(r[3] or "")})
                if not filas:
                    messagebox.showinfo("Eliminar ocultos",
                                        "No hay líneas ocultas en este mes.",
                                        parent=self.parent_frame)
                    return
                detalle = "\n".join(
                    "• %s   %s   %s" % (f["fecha"], formatear_monto(f["monto"]),
                                         f["descripcion"][:55])
                    for f in filas[:8])
                if len(filas) > 8:
                    detalle += "\n… y %d más" % (len(filas) - 8)
                if not messagebox.askyesno(
                        "Eliminar ocultos definitivamente",
                        "Se eliminarán DEFINITIVAMENTE %d línea(s) ocultas del estado de cuenta "
                        "de %s · %s:\n\n%s\n\n"
                        "Se borrarán del PDF cargado y de la lista de ocultos. Se guardará un "
                        "respaldo JSON.\n\n"
                        "No se tocan los asientos del sistema; si alguno queda sin línea, la "
                        "diferencia puede continuar.\n\n¿Continuar?"
                        % (len(filas), construir_etiqueta_banco(banco), self.cmb_mes.get(), detalle),
                        parent=self.parent_frame):
                    return
                # Respaldo antes de borrar
                try:
                    respaldo = {"creado": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "banco": banco.get("banco", ""), "cuenta": banco.get("cuenta", ""),
                                "mes": mes_key, "ignorados": filas}
                    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "respaldo_eliminar_ocultos_%s.json"
                                        % datetime.now().strftime("%Y-%m-%d_%H%M%S"))
                    with open(ruta, "w", encoding="utf-8") as f:
                        json.dump(respaldo, f, ensure_ascii=False, indent=1)
                except Exception as error:
                    print("[Ocultos] respaldo:", error)
                borradas_pdf = 0
                for f in filas:
                    base_sql = """SELECT id FROM conciliacion_movimientos_pdf
                                  WHERE banco=%s AND cuenta=%s
                                    AND fecha=%s AND ABS(monto-%s)<=0.005
                                    AND (periodo=%s OR periodo IS NULL OR periodo='')"""
                    params = (banco.get("banco", ""), banco.get("cuenta", ""),
                              f["fecha"], f["monto"], mes_key)
                    # Primero por descripción exacta: evita borrar otra línea
                    # visible del mismo importe y fecha.
                    c.execute(base_sql + " AND descripcion=%s ORDER BY id LIMIT 1",
                              params + (f["descripcion"],))
                    r = c.fetchone()
                    if not r:
                        pref = f["descripcion"][:18]
                        c.execute(base_sql + " AND descripcion LIKE %s ORDER BY id LIMIT 1",
                                  params + (pref + "%",))
                        r = c.fetchone()
                    if r:
                        c.execute("DELETE FROM conciliacion_movimientos_pdf WHERE id=%s", (r[0],))
                        borradas_pdf += 1
                    c.execute("DELETE FROM conciliacion_ignorados WHERE id=%s", (f["id"],))
            conn.commit()
        except Exception as e:
            conn.rollback()
            messagebox.showerror("Error", f"No se pudieron eliminar los ocultos:\n{e}",
                                 parent=self.parent_frame)
            return
        finally:
            liberar_conexion(conn)
        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Eliminó definitivamente {len(filas)} línea(s) oculta(s) de "
                            f"{construir_etiqueta_banco(banco)} · {self.cmb_mes.get()} "
                            f"(PDF borradas: {borradas_pdf})")
        # Forzar recarga del PDF y del reporte
        self.movimientos_pdf = []
        self._banco_pdf = None
        self._mes_pdf = ""
        self.generar_reporte()

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

    @staticmethod
    def _mes_desplazado(mes_key, delta):
        """'2026-09' + 1 -> '2026-10'; -1 -> '2026-08'."""
        try:
            anio, mes = [int(x) for x in str(mes_key).split("-")[:2]]
            total = anio * 12 + (mes - 1) + int(delta)
            return "%04d-%02d" % (total // 12, (total % 12) + 1)
        except Exception:
            return ""

    def _dias_desfase_actual(self):
        """Mismo ajuste «Días de desfase» de la Conciliación inteligente."""
        try:
            return max(1, int(getattr(self, "_dias_desfase", 5) or 5))
        except Exception:
            return 5

    @staticmethod
    def _asiento_coincide_linea(asiento, linea, comision=0.0, max_dias=7):
        """¿El asiento puede ser la misma operación que la línea del banco?"""
        if not _monto_coincide_banco(linea.get("monto"), asiento.get("monto"), comision):
            return False
        return _dias_entre_fechas(asiento.get("fecha"), linea.get("fecha")) <= max_dias

    def _traer_pendientes_mes_anterior(self, banco, mes_key, sys_movs, bank_movs, comision=0.0):
        """Asientos de fin del mes anterior que aparecen en este estado de cuenta.

        Sirve para pagos como el del 30/09 que el banco posteó el 02/10: se
        incluyen en el mes siguiente para que se crucen con su línea real.
        """
        mes_prev = self._mes_desplazado(mes_key, -1)
        if not mes_prev or not bank_movs:
            return []
        dias_desfase = self._dias_desfase_actual()
        # Sólo hace falta si hay líneas del banco en los primeros días del mes.
        try:
            primeros = [b for b in bank_movs
                        if int(clave_fecha(b.get("fecha"))[8:10] or 99) <= dias_desfase]
        except Exception:
            primeros = []
        if not primeros:
            return []
        try:
            previos = self.cargar_movimientos_sistema(banco, mes=mes_prev) or []
        except Exception as error:
            print("[Conciliación] pendientes mes anterior:", error)
            return []
        try:
            ignorados = self.cargar_ignorados(banco).get("sistema", set())
            conciliados = self.cargar_conciliados(banco)
        except Exception:
            ignorados, conciliados = set(), set()
        existentes = {(m.get("tabla"), m.get("id"),
                       normalizar_fecha(m.get("fecha")),
                       round(float(m.get("monto") or 0), 2)) for m in sys_movs}
        try:
            anio, mes = [int(x) for x in mes_prev.split("-")[:2]]
            ultimo_dia = calendar.monthrange(anio, mes)[1]
        except Exception:
            return []
        salida = []
        for m in previos:
            if m.get("espejo_banco"):
                continue
            f = normalizar_fecha(m.get("fecha"))
            if not f.startswith(mes_prev):
                continue
            try:
                dia = int(f[8:10])
            except Exception:
                continue
            # Últimos N días del mes, con el mismo «Días de desfase» de la
            # Conciliación inteligente.
            if dia < ultimo_dia - (dias_desfase - 1):
                continue
            if self._clave_ignorado(m.get("origen", "sistema"), m.get("tabla", ""),
                                    m.get("id", 0), m.get("fecha"), m.get("monto"),
                                    m.get("descripcion")) in ignorados:
                continue
            clave_conc = self._clave_conciliado(
                "sistema", m.get("tabla", ""), m.get("id", 0), m.get("fecha"),
                m.get("monto"), m.get("descripcion"))
            if clave_conc in conciliados:
                continue
            clave = (m.get("tabla"), m.get("id"), f, round(float(m.get("monto") or 0), 2))
            if clave in existentes:
                continue
            if any(self._asiento_coincide_linea(m, b, comision, dias_desfase)
                   for b in bank_movs):
                copia = dict(m)
                copia["_pendiente"] = True
                salida.append(copia)
        return salida

    def _marcar_pendientes_siguiente(self, filas, banco, mes_key, comision=0.0):
        """Marca los asientos de fin de mes que aparecen en el estado siguiente."""
        mes_next = self._mes_desplazado(mes_key, 1)
        if not mes_next:
            return []
        dias_desfase = self._dias_desfase_actual()
        candidatos = []
        try:
            anio, mes = [int(x) for x in str(mes_key).split("-")[:2]]
            ultimo_dia = calendar.monthrange(anio, mes)[1]
        except Exception:
            return []
        for f in filas:
            if str(f.get("origen", "")) != "sistema":
                continue
            if str(f.get("estado", "")).lower() == "conciliado":
                continue
            try:
                dia = int(clave_fecha(f.get("fecha"))[8:10])
            except Exception:
                continue
            # Últimos N días del mes, con el mismo «Días de desfase» de la
            # Conciliación inteligente.
            if dia >= ultimo_dia - (dias_desfase - 1):
                candidatos.append(f)
        if not candidatos:
            return []
        try:
            lineas_next = self._cargar_movimientos_pdf(banco, mes_next) or []
            ign_pdf = self.cargar_ignorados(banco).get("estado_cuenta", set())
            lineas_next = [b for b in lineas_next
                           if self._clave_ignorado("estado_cuenta", "", 0, b.get("fecha"),
                                                   b.get("monto"), b.get("descripcion"))
                           not in ign_pdf]
        except Exception as error:
            print("[Conciliación] estado del mes siguiente:", error)
            lineas_next = []
        marcados = []
        if lineas_next:
            # Estado siguiente cargado: solo se marca si la línea existe de verdad.
            for f in candidatos:
                if any(self._asiento_coincide_linea(f, b, comision, dias_desfase)
                       for b in lineas_next):
                    f["estado"] = "pendiente"
                    f["_pendiente_siguiente"] = True
                    marcados.append(f)
            return marcados
        # Estado siguiente todavía NO cargado: los asientos de fin de mes que
        # tienen factura/soporte quedan como posibles pendientes. Cuando se
        # cargue el estado, si no aparecen, vuelven a sobrantes.
        for f in candidatos:
            tiene_soporte = (bool(str(f.get("documento") or "").strip())
                             or int(f.get("id_factura") or 0) > 0)
            if tiene_soporte:
                f["estado"] = "pendiente"
                f["_pendiente_siguiente"] = True
                marcados.append(f)
        return marcados

    def generar_reporte(self):
        banco = self.banco_seleccionado()
        if not banco:
            self.lbl_resumen.configure(text="⚠️ No hay banco seleccionado.")
            return
        self._diferencias_importe = []
        # ⏳ Aviso mientras se consultan los movimientos (como en Eventos)
        try:
            self.lbl_resumen.configure(text="⏳ Cargando los movimientos del sistema...")
            self.update_idletasks()
        except Exception:
            pass

        # 🔁 Se recupera el estado de cuenta guardado si cambió el banco o el MES
        #    (ahora conviven varios meses por banco y cuenta).
        _banco_actual = banco.get("banco", "")
        mes_key = mes_etiqueta_a_key(self.cmb_mes.get())
        _mes_actual = mes_key or ""
        # 🔎 Clave del período que se está calculando (para no mezclar meses).
        self._diferencias_clave = (banco.get("banco", ""),
                                   banco.get("cuenta", ""),
                                   mes_key or "")
        if (not getattr(self, "movimientos_pdf", None)
                or getattr(self, "_banco_pdf", None) != _banco_actual
                or getattr(self, "_mes_pdf", "") != _mes_actual):
            try:
                self.movimientos_pdf = self._cargar_movimientos_pdf(banco, mes_key)
                self._banco_pdf = _banco_actual
                self._mes_pdf = _mes_actual
            except Exception:
                self.movimientos_pdf = []


        # 🚫 Movimientos que el usuario eliminó de esta conciliación: no se vuelven a cargar
        ignorados = self.cargar_ignorados(banco)
        ign_sistema = ignorados.get("sistema", set())
        ign_pdf = ignorados.get("estado_cuenta", set())

        try:
            movimientos_sistema = self.cargar_movimientos_sistema(banco, mes=mes_key)
        except Exception as exc:
            messagebox.showerror("Banco", str(exc), parent=self.parent_frame)
            return
        movs_sistema = []
        for m in movimientos_sistema:
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

        # 📅 Solo los manuales del MES elegido: los de otros meses aparecían como
        #    «sobrantes» de este y ensuciaban el cruce.
        manuales = [m for m in self.cargar_manuales(banco)
                    if not mes_key
                    or normalizar_fecha(m.get("fecha")).startswith(mes_key)]

        # 💳 El banco muestra «principal + comisión» en UNA sola línea (ej. 579.91
        #     + 4.80 = 584.71). En el sistema la comisión puede estar separada; se
        #     quita de las listas de cruce y se compara el neto con la comisión.
        comision_bancaria = cargar_comision_interbancaria()

        def _es_mov_comision(m):
            desc = _norm_texto(m.get("descripcion", ""))
            monto_c = abs(float(m.get("monto") or 0))
            return (m.get("tabla") == "tx_comision" or m.get("es_comision")
                    or ("comision" in desc and monto_c <= 20))

        sys_movs = [m for m in sys_movs if not _es_mov_comision(m)]
        manuales = [m for m in manuales if not _es_mov_comision(m)]

        bank_movs = []
        _pdf_periodo = 0
        _pdf_ocultas = 0
        for b in self.movimientos_pdf:
            if mes_key and not normalizar_fecha(b["fecha"]).startswith(mes_key):
                continue
            _pdf_periodo += 1
            clave = self._clave_ignorado("estado_cuenta", "", 0, b.get("fecha"),
                                         b.get("monto"), b.get("descripcion"))
            if clave not in ign_pdf:
                bank_movs.append(b)
            else:
                _pdf_ocultas += 1

        # ⏳ Asientos de fin del mes anterior que el banco posteó en este mes
        #    (ej.: pago del 30/09 que aparece el 02/10). Se cruzan aquí para que
        #    no queden como «faltante» del mes siguiente.
        try:
            prev_pend = self._traer_pendientes_mes_anterior(
                banco, mes_key, sys_movs, bank_movs, comision_bancaria)
            for m in prev_pend:
                m["_pendiente"] = True
                sys_movs.append(m)
        except Exception as error:
            print("[Conciliación] pendientes del mes anterior:", error)
            prev_pend = []

        filas = []
        usados = set()

        def _a_date(s):
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except Exception:
                return None

        def _emparejar_todos(lista, usados=None):
            """Empareja una lista con bank_movs en 3 pasadas.

            Pasada 1: misma fecha + mismo monto (exacto).
            Pasada 2: mismo monto con fecha cercana (hasta 31 días): el sistema
                      suele registrar el pago en otra fecha que el banco.
            Pasada 3: monto único en cualquier fecha.
            Se admite además que la línea del banco sea el principal del sistema
            MÁS la comisión interbancaria (el banco las muestra juntas:
            579.91 + 4.80 = 584.71). Se compara por pasadas para que un movimiento
            con fecha exacta no pierda su línea a manos de otro con igual monto.
            """
            def _coincide(b, m):
                try:
                    bm = float(b["monto"])
                    mm = float(m["monto"])
                except Exception:
                    return False
                if abs(bm - mm) <= 0.02:
                    return True                    # mismo importe (tolera 2 céntimos)
                # El banco cobra la comisión junto con el principal
                if bm * mm > 0 and abs(abs(bm) - abs(mm) - comision_bancaria) <= 0.02:
                    return True
                return False

            if usados is None:
                usados = set()
            res = {}

            def _coincide_exacto(b, m):
                try:
                    return abs(float(b["monto"]) - float(m["monto"])) <= 0.005
                except Exception:
                    return False

            # Pasada 0: misma fecha + importe EXACTO. Va primero para que un
            # asiento de 7,56 no se lleve una línea de 7,55 habiendo una de 7,56.
            for m in lista:
                sf = normalizar_fecha(m["fecha"])
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if sf and normalizar_fecha(b["fecha"]) == sf and _coincide_exacto(b, m):
                        usados.add(i); res[id(m)] = i; break

            # Pasada 0b: misma fecha + importe con tolerancia (comisión/redondeo).
            for m in lista:
                if id(m) in res:
                    continue
                sf = normalizar_fecha(m["fecha"])
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if sf and normalizar_fecha(b["fecha"]) == sf and _coincide(b, m):
                        usados.add(i); res[id(m)] = i; break
            # Pasada 2a: fecha cercana + importe EXACTO.
            for m in lista:
                if id(m) in res:
                    continue
                sd = _a_date(normalizar_fecha(m["fecha"]))
                cands = []
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if _coincide_exacto(b, m):
                        bd = _a_date(normalizar_fecha(b["fecha"]))
                        if sd and bd:
                            cands.append((abs((bd - sd).days), i))
                if cands:
                    cands.sort()
                    if cands[0][0] <= 31:
                        usados.add(cands[0][1]); res[id(m)] = cands[0][1]

            # Pasada 2b: fecha cercana + importe con tolerancia.
            for m in lista:
                if id(m) in res:
                    continue
                sd = _a_date(normalizar_fecha(m["fecha"]))
                cands = []
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if _coincide(b, m):
                        bd = _a_date(normalizar_fecha(b["fecha"]))
                        if sd and bd:
                            cands.append((abs((bd - sd).days), i))
                if cands:
                    cands.sort()
                    if cands[0][0] <= 31:
                        usados.add(cands[0][1]); res[id(m)] = cands[0][1]
            for m in lista:
                if id(m) in res:
                    continue
                cands = [i for i, b in enumerate(bank_movs)
                         if i not in usados and _coincide(b, m)]
                if len(cands) == 1:
                    usados.add(cands[0]); res[id(m)] = cands[0]

            # 🔁 Corrección de cruces: si dos asientos quedaron con las líneas
            #    intercambiadas (9,10 con 9,08 y 9,08 con 9,10), se cambian para
            #    que cada uno tome la línea de su importe exacto.
            por_id = {id(m): m for m in lista}
            claves = list(res.keys())
            for a in range(len(claves)):
                for b in range(a + 1, len(claves)):
                    id1, id2 = claves[a], claves[b]
                    i1, i2 = res[id1], res[id2]
                    m1, m2 = por_id.get(id1), por_id.get(id2)
                    if not m1 or not m2:
                        continue
                    try:
                        am1 = float(m1["monto"]); am2 = float(m2["monto"])
                        bm1 = float(bank_movs[i1]["monto"]); bm2 = float(bank_movs[i2]["monto"])
                    except Exception:
                        continue
                    if am1 * bm2 <= 0 or am2 * bm1 <= 0:
                        continue
                    actual = abs(bm1 - am1) + abs(bm2 - am2)
                    cruzado = abs(bm2 - am1) + abs(bm1 - am2)
                    if cruzado + 0.001 < actual:
                        res[id1], res[id2] = i2, i1
            return res

        def linea_estado_cuenta(b):
            return {**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                    "origen": "estado_cuenta", "tabla": "", "estado": "conciliado"}

        def _monto_mostrar_con_comision(asiento, linea):
            """Monto a mostrar: si el banco cobró la comisión en la misma línea,
            se muestra el total del banco (principal + comisión)."""
            try:
                am = abs(float(asiento.get("monto") or 0))
                bm = abs(float(linea.get("monto") or 0))
                if abs(bm - am - float(comision_bancaria or 0)) <= 0.02:
                    return linea.get("monto")
            except (TypeError, ValueError):
                pass
            return None

        usados = set()
        matches_sys = _emparejar_todos(sys_movs, usados)
        for s in sys_movs:
            idx_match = matches_sys.get(id(s))
            if idx_match is not None:
                # ✅ UNA sola fila por movimiento: se muestra el asiento del sistema
                #    y se le pega el saldo de la línea del banco. Antes se agregaban
                #    las dos caras (Sistema + Estado de Cuenta) y se veía duplicado.
                linea = bank_movs[idx_match]
                fila = {**s, "estado": "conciliado",
                        "saldo": linea.get("saldo"),
                        "fecha_banco": linea.get("fecha"),
                        "descripcion_banco": linea.get("descripcion")}
                _mm = _monto_mostrar_con_comision(s, linea)
                if _mm is not None:
                    fila["monto_mostrar"] = _mm
                filas.append(fila)
            elif str(s.get("estado") or "").lower() == "conciliado":
                # ✅ ya está conciliado en la base: se respeta su estado
                filas.append({**s})
            else:
                filas.append({**s, "estado": "diferencia"})

        # Los movimientos MANUALES también se emparejan con el estado de cuenta.
        matches_man = _emparejar_todos(manuales, usados)
        for m in manuales:
            idx_match = matches_man.get(id(m))
            if idx_match is not None:
                linea = bank_movs[idx_match]
                fila = {**m, "estado": "conciliado", "origen": "manual",
                        "saldo": linea.get("saldo"),
                        "fecha_banco": linea.get("fecha"),
                        "descripcion_banco": linea.get("descripcion")}
                _mm = _monto_mostrar_con_comision(m, linea)
                if _mm is not None:
                    fila["monto_mostrar"] = _mm
                filas.append(fila)
            else:
                # Sin línea disponible: o no está en el estado de cuenta o su
                # línea ya la usó el asiento del sistema (manual duplicado).
                filas.append({**m, "estado": "diferencia", "origen": "manual",
                              "_sin_pareja": True})

        # 📄 Asientos que NO vienen de Compras (facturas App Grifo, comisiones y los
        #    registrados desde el banco): antes el cruce los ignoraba, así que sus
        #    líneas del estado de cuenta quedaban «faltantes» aunque estuvieran.
        extras = []
        try:
            extras = self.cargar_asientos_extra(banco, mes_key)
        except Exception as error_extra:
            print("[Conciliación] asientos extra:", error_extra)
        if extras:
            matches_ext = _emparejar_todos(extras, usados)
            for e in extras:
                idx_match = matches_ext.get(id(e))
                if idx_match is not None:
                    linea = bank_movs[idx_match]
                    filas.append({**e, "estado": "conciliado",
                                  "saldo": linea.get("saldo"),
                                  "fecha_banco": linea.get("fecha"),
                                  "descripcion_banco": linea.get("descripcion")})
                else:
                    filas.append({**e, "estado": e.get("estado") or "diferencia"})

        # 🔍 Diferencias de importe entre asientos emparejados y su línea del banco.
        #    Se guardan para la ventana «Diferencias por asiento».
        vistos_dif = set()

        def _agregar_dif(asiento, linea):
            try:
                am = float(asiento.get("monto") or 0)
                bm = float(linea.get("monto") or 0)
            except (TypeError, ValueError):
                return
            dif = round(bm - am, 2)
            if abs(dif) < 0.005:
                return
            # La comisión ya registrada en la transferencia no es una diferencia:
            # el banco cobró principal + comisión y el sistema ya la tiene.
            try:
                com_reg = abs(float(asiento.get("comision") or 0))
            except (TypeError, ValueError):
                com_reg = 0.0
            if com_reg > 0 and abs(abs(dif) - com_reg) <= 0.02:
                return
            clave = (asiento.get("tabla"), asiento.get("id"),
                     normalizar_fecha(linea.get("fecha")), round(bm, 2))
            if clave in vistos_dif:
                return
            vistos_dif.add(clave)
            try:
                com = abs(float(comision_bancaria or 0))
            except (TypeError, ValueError):
                com = 0.0
            if abs(abs(dif) - com) <= 0.02 and abs(dif) > 0.02:
                tipo = "comision"
            elif abs(dif) <= 0.05:
                tipo = "redondeo"
            else:
                tipo = "diferencia"
            self._diferencias_importe.append({
                "fecha_sistema": asiento.get("fecha") or "",
                "fecha_banco": linea.get("fecha") or "",
                "documento": asiento.get("documento") or "",
                "descripcion": asiento.get("descripcion") or "",
                "monto_sistema": am,
                "monto_banco": bm,
                "diferencia": dif,
                "tipo": tipo,
                "origen": asiento.get("origen") or "",
                "tabla": asiento.get("tabla") or "",
                "id": asiento.get("id") or 0,
            })

        for _s in sys_movs:
            _j = matches_sys.get(id(_s))
            if _j is not None:
                _agregar_dif(_s, bank_movs[_j])
        for _m in manuales:
            _j = matches_man.get(id(_m))
            if _j is not None:
                _agregar_dif(_m, bank_movs[_j])
        if extras:
            for _e in extras:
                _j = matches_ext.get(id(_e))
                if _j is not None:
                    _agregar_dif(_e, bank_movs[_j])

        # 🔎 Si hay un mes seleccionado, la ventana muestra solo ese mes.
        if mes_key:
            self._diferencias_importe = [
                d for d in self._diferencias_importe
                if normalizar_fecha(d.get("fecha_sistema", "")).startswith(mes_key)
                or normalizar_fecha(d.get("fecha_banco", "")).startswith(mes_key)]

        for i, b in enumerate(bank_movs):
            if i not in usados:
                filas.append({**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                              "origen": "estado_cuenta", "tabla": "", "estado": "diferencia"})

        # 🧹 Si se eligió un mes, solo se muestran movimientos de ESE mes
        if mes_key:
            filas = [f for f in filas
                     if normalizar_fecha(f.get("fecha", "")).startswith(mes_key)
                     or (f.get("_pendiente")
                         and normalizar_fecha(f.get("fecha_banco", "")).startswith(mes_key))]
        filas.sort(key=lambda f: (normalizar_fecha(f["fecha"]), f["monto"]))
        # 🔁 Reaplicar los movimientos ya conciliados (persistidos en la base)
        conciliados = self.cargar_conciliados(banco)
        for f in filas:
            if f.get("_sin_pareja"):
                continue
            if self._clave_conciliado_fila(f) in conciliados:
                f["estado"] = "conciliado"
        # ⏳ Asientos de fin de mes que aparecen en el estado de cuenta siguiente:
        #    no se cuentan como sobrantes; quedan pendientes para el mes siguiente.
        pendientes_siguiente = []
        try:
            pendientes_siguiente = self._marcar_pendientes_siguiente(
                filas, banco, mes_key, comision_bancaria)
        except Exception as error:
            print("[Conciliación] pendientes próximo mes:", error)
        self._filas_conc_full = list(filas)
        self.filas_conciliacion = filas
        self.refrescar_tree()
        # ⚠️ Aviso: movimientos del estado de cuenta que NO están en el sistema
        faltantes = [f for f in filas if f.get("origen") == "estado_cuenta"
                     and f.get("estado") != "conciliado"]
        self._faltantes_estado_cuenta = faltantes
        # 📅 Solo se puede hablar de SOBRANTES en meses con estado de cuenta cargado:
        #    si el mes no está cargado (por ejemplo el mes EN CURSO), sus asientos no
        #    son sobrantes, simplemente todavía no tienen estado de cuenta.
        periodos_cargados = self._periodos_cargados(banco)
        # 🛡️ Se guarda en OTRO atributo: llamarlo igual que el método hacía que
        #    getattr devolviera el método y la tabla reventara al dibujarse.
        self._periodos_ok = set(periodos_cargados)
        mes_cargado = (not mes_key) or (mes_key in periodos_cargados)
        pendientes_sis = [f for f in filas
                          if f.get("origen") != "estado_cuenta"
                          and f.get("estado") != "conciliado"
                          and not f.get("_pendiente_siguiente")
                          and (not mes_key
                               or clave_fecha(f.get("fecha"))[:7] in periodos_cargados)]
        self._pendientes_sistema = pendientes_sis
        self._mes_cargado = mes_cargado
        self._guardar_diferenciales(faltantes, pendientes_sis, banco)
        if not mes_cargado:
            try:
                self.lbl_faltantes.configure(
                    text="📄 No hay estado de cuenta cargado para %s. Los asientos de "
                         "ese mes quedan «pendientes de estado de cuenta» (no son "
                         "sobrantes)." % (self.cmb_mes.get() if hasattr(self, "cmb_mes") else mes_key),
                    text_color="#7f8c8d")
                self.btn_faltantes.configure(state="disabled")
            except Exception:
                pass
        else:
            self._actualizar_aviso_faltantes(faltantes, pendientes_sis, _pdf_ocultas, _pdf_periodo,
                                             pendientes_siguiente=len(pendientes_siguiente))

        saldo_inicial = normalizar_monto(banco.get("saldo_inicial", ""))
        # Descartar un asiento TX en conciliación no cambia su efecto contable.
        # Se conserva la conducta histórica de las demás fuentes.
        movs_saldo = [m for m in movs_sistema if m.get("tabla") not in ("transferencias_bancarias", "tx_comision")]
        movs_saldo.extend(m for m in movimientos_sistema if m.get("tabla") in ("transferencias_bancarias", "tx_comision"))
        # ⏳ Los pendientes del mes anterior se cruzan en este mes; la barra
        #    superior sigue mostrando el neto crudo del sistema. El detalle
        #    conciliado (sin pendientes ni duplicados) está en «Resumen del período».
        try:
            movs_saldo.extend(prev_pend)
        except NameError:
            pass
        ingresos = sum(m["monto"] for m in movs_saldo if m["monto"] > 0)
        egresos = sum(-m["monto"] for m in movs_saldo if m["monto"] < 0)
        neto_sistema = ingresos - egresos
        neto_pdf = sum(b["monto"] for b in bank_movs)
        diferencia_mes = neto_sistema - neto_pdf

        # 📊 Cantidades y totales de cada lado (asientos del sistema vs. líneas del
        #    estado de cuenta), separando ingresos y egresos.
        _asientos_lado = [f for f in filas
                          if f.get("origen") != "estado_cuenta"
                          and not f.get("_pendiente_siguiente")]
        _lineas_lado = [f for f in filas if f.get("origen") == "estado_cuenta"]

        def _resumen_lado(lista):
            ingresos_l = [float(f.get("monto") or 0) for f in lista
                          if float(f.get("monto") or 0) > 0]
            egresos_l = [float(f.get("monto") or 0) for f in lista
                         if float(f.get("monto") or 0) < 0]
            return (len(lista), len(ingresos_l), sum(ingresos_l),
                    len(egresos_l), -sum(egresos_l))

        _n_asi, _ni_asi, _ti_asi, _ne_asi, _te_asi = _resumen_lado(_asientos_lado)
        _n_lin, _ni_lin, _ti_lin, _ne_lin, _te_lin = _resumen_lado(_lineas_lado)

        if mes_key:
            etiqueta_mes = self.cmb_mes.get()
            txt = (f"Banco: {construir_etiqueta_banco(banco)}   |   Mes: {etiqueta_mes}   |   "
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
        # 📊 Los totales de cada lado se ven en la ventana «Resumen del período»
        #    (botón), no amontonados en esta barra.
        self._ultimo_resumen = (_resumen_lado(_asientos_lado), _resumen_lado(_lineas_lado))
        # 🔎 Recordar a qué banco/mes corresponden las diferencias calculadas.
        self._diferencias_clave = (banco.get("banco", ""),
                                   banco.get("cuenta", ""),
                                   mes_key or "")

    def cargar_asientos_extra(self, banco, mes=None):
        """Asientos de conciliacion_bancaria que no vienen de pagos/cobros/transferencias.

        Son los registrados desde el estado de cuenta (origen 'e'), las facturas de
        la App Grifo ('s:facturas_recibidas') y las comisiones ('s:tx_comision').
        El cruce clásico no los miraba y por eso sus líneas del banco quedaban como
        «faltantes» aunque el asiento existiera.
        """
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        movs = []
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT id, fecha, descripcion, monto, COALESCE(origen,''),
                           COALESCE(estado,''), COALESCE(tabla,''), COALESCE(categoria,''),
                           COALESCE(subcategoria,''), COALESCE(evento,''), COALESCE(chofer,''),
                           COALESCE(comision,0), COALESCE(interbancario,FALSE),
                           COALESCE(es_cruzada,FALSE), COALESCE(numero_operacion,'')
                    FROM conciliacion_bancaria
                    WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                      AND COALESCE(cuenta,'') = %s
                      AND COALESCE(origen,'') NOT IN (
                          'manual', 's:pagos_comprobantes', 's:pagos_clientes',
                          's:transferencias_bancarias')
                    ORDER BY id
                """, (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                for r in c.fetchall():
                    fecha = r[1]
                    if mes and not normalizar_fecha(fecha).startswith(mes):
                        continue
                    monto = float(r[3] or 0)
                    movs.append({
                        "id": r[0], "fecha": fecha, "descripcion": r[2], "monto": monto,
                        "tipo": "ingreso" if monto > 0 else "egreso",
                        "estado": r[5], "origen": r[4] or "sistema", "tabla": r[6],
                        "categoria": r[7], "subcategoria": r[8], "evento": r[9],
                        "chofer": r[10], "comision": float(r[11] or 0),
                        "interbancario": bool(r[12]), "es_cruzada": bool(r[13]),
                        "numero_operacion": r[14]})
        except Exception as error:
            print("[Conciliación] cargar asientos extra:", error)
        finally:
            liberar_conexion(conn)
        return movs

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
                               COALESCE(evento, ''), COALESCE(chofer, ''),
                               COALESCE(comision, 0), COALESCE(interbancario, FALSE),
                               COALESCE(es_cruzada, FALSE), COALESCE(numero_operacion, '')
                        FROM conciliacion_bancaria
                        WHERE origen = 'manual'
                          AND UPPER(COALESCE(banco,'')) = UPPER(%s)
                          AND COALESCE(cuenta,'') = %s
                        ORDER BY id
                    """, (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                    filas = c.fetchall()
                except Exception:
                    # Base de datos anterior sin las columnas de detalle
                    conn.rollback()
                    c.execute("""
                        SELECT id, fecha, descripcion, monto, tipo, estado
                        FROM conciliacion_bancaria
                        WHERE origen = 'manual'
                          AND UPPER(COALESCE(banco,'')) = UPPER(%s)
                          AND COALESCE(cuenta,'') = %s
                        ORDER BY id
                    """, (banco.get("banco", ""), banco.get("cuenta", "") or ""))
                    filas = [tuple(f) + ("", "", "", "", 0, False, False, "") for f in c.fetchall()]

                for (idp, fecha, desc, monto, tipo, estado, categoria, subcategoria,
                     evento, chofer, comision, interbancario, es_cruzada, num_oper) in filas:
                    movs.append({
                        "id": idp, "fecha": fecha or "", "descripcion": desc or "",
                        "monto": float(monto or 0), "tipo": tipo or "ingreso",
                        "tabla": "conciliacion", "estado": estado or "pendiente",
                        "categoria": categoria or "", "subcategoria": subcategoria or "",
                        "evento": evento or "", "chofer": chofer or "",
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

    def _valores_conc(self, f):
        estado = f.get("estado", "pendiente")
        # 📅 Si el MES de ese asiento no tiene estado de cuenta cargado, no se puede
        #    afirmar que esté conciliado: se muestra que falta el estado de cuenta.
        _mes_f = clave_fecha(f.get("fecha", ""))[:7]
        _ok_f = getattr(self, "_periodos_ok", None)
        _ok_f = _ok_f if isinstance(_ok_f, (set, frozenset)) else set()
        if _mes_f and _mes_f not in _ok_f and f.get("origen") != "estado_cuenta":
            estado_txt = "⏳ Falta el estado de cuenta"
        elif f.get("_pendiente_siguiente"):
            estado_txt = "⏳ Pendiente próximo estado"
        elif estado == "conciliado":
            estado_txt = "✅ Conciliado"
        elif f.get("origen") == "estado_cuenta":
            estado_txt = "❌ Sin conciliar (Banco)"
        elif estado == "diferencia":
            _mes = clave_fecha(f.get("fecha", ""))[:7]
            _ok = getattr(self, "_periodos_ok", None)
            if not isinstance(_ok, (set, frozenset)):
                _ok = set()
            if _mes and _mes not in _ok:
                estado_txt = "⏳ Falta el estado de cuenta"
            else:
                estado_txt = "⚠️ Sin conciliar (Sistema)"
        else:
            estado_txt = "📝 Manual / Pendiente"
        if f.get("tipo") == "comision":
            tipo_txt = "Comisión interbancaria"
        elif f.get("tipo") == "transferencia":
            tipo_txt = "Transferencia (Ingreso)" if f["monto"] >= 0 else "Transferencia (Egreso)"
        else:
            tipo_txt = "Ingreso" if f["monto"] >= 0 else "Egreso"
        origen_txt = {"sistema": "Sistema", "estado_cuenta": "Estado de Cuenta",
                      "manual": "Manual"}.get(f.get("origen"), f.get("origen", ""))
        saldo_txt = ""
        if f.get("saldo") is not None:
            saldo_txt = formatear_monto(f["saldo"])
        monto_txt = f.get("monto_mostrar", f["monto"])
        return (estado_txt, fecha_para_mostrar(f["fecha"]), f.get("documento", ""), f["descripcion"],
                tipo_txt, formatear_monto(monto_txt), origen_txt, saldo_txt)

    def _tag_conc(self, f):
        estado = f.get("estado", "pendiente")
        # 📅 Sin estado de cuenta del mes, el asiento no puede pintarse de verde.
        _mes_t = clave_fecha(f.get("fecha", ""))[:7]
        _ok_t = getattr(self, "_periodos_ok", None)
        _ok_t = _ok_t if isinstance(_ok_t, (set, frozenset)) else set()
        if _mes_t and _mes_t not in _ok_t and f.get("origen") != "estado_cuenta":
            return "sin_estado"
        if f.get("_pendiente_siguiente"):
            return "pendiente"
        if estado == "conciliado":
            return "conciliado"
        if f.get("origen") == "estado_cuenta":
            return "diferencia"
        if estado == "diferencia":
            # Si el mes de ese asiento no tiene estado de cuenta cargado, no es un
            # sobrante: solo falta el estado de cuenta (mes en curso, por ejemplo).
            mes = clave_fecha(f.get("fecha", ""))[:7]
            _ok = getattr(self, "_periodos_ok", None)
            if not isinstance(_ok, (set, frozenset)):
                _ok = set()
            if mes and mes not in _ok:
                return "sin_estado"
            return "diferencia"
        return "estado_cuenta"

    def _clave_conc(self, f):
        col = getattr(self, "columna_orden_conc", "fecha")
        if col == "fecha":
            return normalizar_fecha(f["fecha"]) or ""
        if col == "monto":
            return float(f.get("monto") or 0)
        if col == "estado":
            return self._valores_conc(f)[0]
        if col == "tipo":
            return self._valores_conc(f)[4]
        if col == "origen":
            return self._valores_conc(f)[6]
        if col == "saldo":
            return float(f.get("saldo") or 0)
        if col == "documento":
            return str(f.get("documento") or "")
        return str(f.get("descripcion") or "")

    def ordenar_conciliacion(self, columna):
        if getattr(self, "columna_orden_conc", "fecha") == columna:
            self.orden_asc_conc = not getattr(self, "orden_asc_conc", True)
        else:
            self.columna_orden_conc = columna
            self.orden_asc_conc = True
        self.refrescar_tree()

    def refrescar_tree(self):
        col_act = getattr(self, "columna_orden_conc", "fecha")
        asc = getattr(self, "orden_asc_conc", True)
        self.filas_conciliacion.sort(key=self._clave_conc, reverse=not asc)
        self.tabla_conc.delete(*self.tabla_conc.get_children())
        etiquetas = {"estado": "Estado", "fecha": "Fecha", "documento": "N° Documento",
                     "descripcion": "Descripción", "tipo": "Tipo", "monto": "Monto", "origen": "Origen",
                     "saldo": "Saldo"}
        for i, f in enumerate(self.filas_conciliacion):
            self.tabla_conc.insert("", tk.END, iid=str(i), values=self._valores_conc(f),
                                   tags=(self._tag_conc(f),))
        for _col, txt in etiquetas.items():
            flecha = ""
            if _col == col_act:
                flecha = " ↑" if asc else " ↓"
            self.tabla_conc.heading(_col, text=txt + flecha)

    # -----------------------------------------------------
    # ACCIONES DE CONCILIACION
    # -----------------------------------------------------
    def _filas_seleccionadas(self):
        sel = self.tabla_conc.selection()
        return [self.filas_conciliacion[int(i)] for i in sel if i.isdigit()]

    def _fila_coincide(self, f, termino):
        """True si el término aparece en CUALQUIER columna visible de la fila."""
        try:
            valores = self._valores_conc(f)
        except Exception:
            valores = (str(f.get("fecha") or ""), str(f.get("descripcion") or ""),
                       str(f.get("monto") or ""), str(f.get("origen") or ""))
        hay = " ".join(str(v) for v in valores).lower()
        return termino in hay

    def buscar_en_conciliacion(self):
        """🔍 Lleva el cursor al recuadro de búsqueda inline (sin abrir ventanas)."""
        try:
            self.ent_buscar_conc.focus_set()
        except Exception:
            pass

    def filtrar_conciliacion_inline(self, _event=None):
        """Filtra la tabla de conciliación por CUALQUIER columna (en vivo)."""
        try:
            termino = self.ent_buscar_conc.get().strip().lower()
        except Exception:
            return
        todas = getattr(self, "_filas_conc_full", None)
        if todas is None:
            return
        if not termino:
            self.filas_conciliacion = list(todas)
        else:
            self.filas_conciliacion = [f for f in todas if self._fila_coincide(f, termino)]
        self.refrescar_tree()

    def limpiar_busqueda_conc(self):
        """Quita el filtro y vuelve a mostrar todos los movimientos."""
        try:
            self.ent_buscar_conc.delete(0, tk.END)
        except Exception:
            pass
        self.filtrar_conciliacion_inline()

    def _actualizar_aviso_faltantes(self, faltantes, pendientes_sistema=None,
                                    ocultas_pdf=0, total_pdf=0,
                                    pendientes_siguiente=0):
        """Avisa de lo que falta conciliar (estado de cuenta y pagos del sistema)."""
        try:
            n = len(faltantes or [])
            m = len(pendientes_sistema or [])
            mensajes = []
            if ocultas_pdf:
                # 👁️ Sin este aviso, la pantalla parece "no conciliar nada" y no hay
                #    forma de saber que las líneas del PDF están ocultas.
                mensajes.append("👁️ %d de %d línea(s) del estado de cuenta están OCULTAS "
                                "(marcadas como eliminadas): use «Restaurar Eliminados»."
                                % (ocultas_pdf, total_pdf or ocultas_pdf))
            if n:
                mensajes.append("⚠️ FALTANTES: %d línea(s) del estado de cuenta sin asiento en el sistema." % n)
            if pendientes_siguiente:
                mensajes.append("⏳ %d asiento(s) de fin de mes pueden estar en el estado de cuenta "
                                "del próximo mes; quedan pendientes y no se cuentan como sobrantes."
                                % pendientes_siguiente)
            if m:
                mensajes.append("⚠️ SOBRANTES: %d asiento(s) del sistema que no aparecen en el estado "
                                "de cuenta (puede que el banco los haya posteado en el mes siguiente)." % m)
            if mensajes:
                self.lbl_faltantes.configure(text="   ".join(mensajes), text_color="#a04000")
            else:
                self.lbl_faltantes.configure(
                    text="✅ Todos los movimientos están conciliados.",
                    text_color="#1e8449")
            self.btn_faltantes.configure(state="normal" if n else "disabled")
        except Exception:
            pass

    def _guardar_diferenciales(self, faltantes, pendientes, banco):
        """Guarda en la base los diferenciales detectados (para el historial)."""
        _asegurar_tabla_diferenciales()
        banco = banco or {}
        nombre_banco = str(banco.get("banco") or "")
        cuenta = str(banco.get("cuenta") or "")
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            with conn.cursor() as c:
                for f, origen in ([(x, "estado_cuenta") for x in (faltantes or [])] +
                                  [(x, "sistema") for x in (pendientes or [])]):
                    try:
                        monto = float(f.get("monto") or 0)
                        fecha = normalizar_fecha(f.get("fecha", ""))
                        descripcion = str(f.get("descripcion") or "")
                        c.execute("""SELECT id FROM conciliacion_diferenciales
                                     WHERE banco=%s AND fecha=%s AND origen=%s
                                       AND ABS(monto - %s) <= 0.02""",
                                  (nombre_banco, fecha, origen, monto))
                        if c.fetchone():
                            continue        # ya estaba registrado
                        c.execute("""INSERT INTO conciliacion_diferenciales
                                     (banco, cuenta, fecha, descripcion, monto, origen, creado)
                                     VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                  (nombre_banco, cuenta, fecha, descripcion, monto, origen,
                                   datetime.now().strftime("%d/%m/%Y %H:%M")))
                    except Exception as e:
                        print("[Diferenciales] no se pudo guardar:", e)
            conn.commit()
        finally:
            liberar_conexion(conn)

    def _marcar_diferencial_incluido(self, fila, descripcion, banco):
        """Deja anotado que el diferencial ya fue incluido (con su descripción)."""
        _asegurar_tabla_diferenciales()
        banco = banco or {}
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            with conn.cursor() as c:
                origen_dif = str(fila.get("origen") or "")
                if origen_dif not in ("estado_cuenta", "sistema"):
                    origen_dif = "estado_cuenta"
                c.execute("""UPDATE conciliacion_diferenciales
                             SET incluido = TRUE, descripcion_guardada = %s
                             WHERE banco=%s AND fecha=%s
                               AND ABS(monto - %s) <= 0.02
                               AND COALESCE(origen,'') = %s""",
                          (str(descripcion or "")[:400], str(banco.get("banco") or ""),
                           normalizar_fecha(fila.get("fecha", "")),
                           float(fila.get("monto") or 0), origen_dif))
            conn.commit()
        except Exception as e:
            print("[Diferenciales] no se pudo marcar incluido:", e)
        finally:
            liberar_conexion(conn)

    def abrir_diferenciales(self):
        """🧾 Abre la ventana con los diferenciales (elegir, describir, incluir)."""
        banco = self.banco_seleccionado()
        if not banco:
            return messagebox.showinfo("Diferenciales",
                                       "Primero elija el banco y la cuenta.",
                                       parent=self.parent_frame)
        faltantes = list(getattr(self, "_faltantes_estado_cuenta", None) or [])
        pendientes = list(getattr(self, "_pendientes_sistema", None) or [])
        if not faltantes and not pendientes:
            return messagebox.showinfo(
                "Diferenciales",
                "No hay diferenciales pendientes para este banco." + chr(10) + chr(10) +
                "Subí el PDF del estado de cuenta o elegí otro mes.",
                parent=self.parent_frame)
        VentanaDiferenciales(self, banco, faltantes, pendientes)

    def incluir_diferencial(self, fila, descripcion):
        """Incluye UN diferencial: crea el movimiento manual o lo marca conciliado."""
        banco = self.banco_seleccionado()
        if not banco:
            return False
        texto = str(descripcion or "").strip() or str(fila.get("descripcion") or "")
        origen = str(fila.get("origen") or "")
        if origen == "estado_cuenta":
            # línea del banco que no estaba en el sistema -> movimiento MANUAL conciliado
            conn = conectar_db(silencioso=True)
            if not conn:
                return False
            try:
                monto = float(fila.get("monto") or 0)
                with conn.cursor() as c:
                    c.execute("""INSERT INTO conciliacion_bancaria
                        (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento,
                         estado, fecha_conciliacion)
                        VALUES (%s,%s,%s,%s,%s,%s,'manual',0,'conciliado',%s)""",
                        (banco.get("banco", ""), banco.get("cuenta", ""),
                         fecha_para_mostrar(fila.get("fecha", "")), texto, monto,
                         "ingreso" if monto >= 0 else "egreso",
                         datetime.now().strftime("%d/%m/%Y")))
                conn.commit()
            except Exception as e:
                print("[Diferenciales] no se pudo incluir:", e)
                return False
            finally:
                liberar_conexion(conn)
        else:
            # movimiento del sistema que el banco no registró -> se marca conciliado
            fila["descripcion"] = texto
            try:
                return bool(self._persistir_conciliado(fila, banco))
            except Exception as e:
                print("[Diferenciales] no se pudo conciliar:", e)
                return False
        try:
            self._marcar_diferencial_incluido(fila, texto, banco)
        except Exception:
            pass
        try:
            registrar_auditoria(self.usuario_activo, "Banco",
                                "Incluyó un diferencial: %s (%s)" % (texto[:40], fila.get("fecha", "")))
        except Exception:
            pass
        return True

    def agregar_faltantes_al_sistema(self):
        """➕ Agrega al sistema (como manuales) los movimientos del estado de cuenta que faltan."""
        faltantes = getattr(self, "_faltantes_estado_cuenta", None) or []
        if not faltantes:
            return
        banco = self.banco_seleccionado()
        if not banco:
            return
        if not messagebox.askyesno(
                "Agregar faltantes",
                "Se agregarán %d movimiento(s) del estado de cuenta al sistema\n"
                "como movimientos MANUALES del banco (y quedarán conciliados).\n\n"
                "¿Desea continuar?" % len(faltantes),
                parent=self.parent_frame):
            return
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        agregados = 0
        try:
            with conn.cursor() as c:
                for f in faltantes:
                    try:
                        monto = float(f.get("monto") or 0)
                        c.execute("""INSERT INTO conciliacion_bancaria
                            (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado, fecha_conciliacion)
                            VALUES (%s,%s,%s,%s,%s,%s,'manual',0,'conciliado',%s)""",
                            (banco.get("banco", ""), banco.get("cuenta", ""),
                             fecha_para_mostrar(f.get("fecha", "")),
                             f.get("descripcion", ""), monto,
                             "ingreso" if monto >= 0 else "egreso",
                             datetime.now().strftime("%d/%m/%Y")))
                        agregados += 1
                    except Exception as e:
                        print("[Conciliación] No se pudo agregar faltante:", e)
            conn.commit()
        finally:
            liberar_conexion(conn)
        try:
            registrar_auditoria(self.usuario_activo, "Banco",
                                "Agregó %d movimiento(s) faltantes del estado de cuenta" % agregados)
        except Exception:
            pass
        messagebox.showinfo("Agregar faltantes",
                            "Se agregaron %d movimiento(s) al sistema." % agregados,
                            parent=self.parent_frame)
        self.generar_reporte()

    def _archivo_factura_de(self, id_factura):
        """Ruta del PDF de la factura (facturas_recibidas) o ''."""
        try:
            idf = int(id_factura or 0)
        except Exception:
            return ""
        if not idf:
            return ""
        conn = conectar_db(silencioso=True)
        if not conn:
            return ""
        try:
            with conn.cursor() as c:
                c.execute("SELECT COALESCE(archivo_ruta, '') FROM facturas_recibidas WHERE id = %s", (idf,))
                r = c.fetchone()
            return str(r[0] or "").strip() if r else ""
        except Exception:
            return ""
        finally:
            liberar_conexion(conn)

    def _mostrar_facturas_del_comprobante(self, ruta_comprobante):
        """🔎 Detalle de las facturas que cancela un mismo comprobante de pago."""
        try:
            from modulo_compras import (facturas_que_cancela, texto_facturas_canceladas,
                                        formatear_moneda)
            filas = facturas_que_cancela(ruta_comprobante or "", "")
        except Exception as e:
            return messagebox.showerror("Comprobante", "No se pudo consultar: %s" % e)
        if not filas:
            return messagebox.showinfo("Comprobante",
                                       "Este comprobante todavía no cancela facturas registradas.")
        messagebox.showinfo(
            "Facturas que cancela este comprobante",
            "Cancela %d factura(s):\n\n%s"
            % (len(filas), texto_facturas_canceladas(filas)))

    def _ruta_existente(self, ruta):
        """Ruta real del archivo (resuelta para este equipo/SO) o '' si no existe."""
        ruta = str(ruta or "").strip()
        if not ruta:
            return ""
        try:
            from app_paths import resolver_ruta_archivo
            resuelta = resolver_ruta_archivo(ruta)
        except Exception:
            resuelta = ""
        base = obtener_ruta_base()
        nombre = ruta.replace("\\", "/").rsplit("/", 1)[-1]
        recibo_tx = os.path.join(base, "comprobantes_transferencias", nombre) if base else ""
        for candidata in (resuelta, ruta, os.path.abspath(ruta), recibo_tx):
            try:
                if candidata and os.path.isfile(candidata):
                    return candidata
            except Exception:
                continue
        return ""

    def _documento_de_asiento(self, fila):
        """Busca el PDF (factura o comprobante) asociado a un asiento de la tabla."""
        candidatos = []
        try:
            if fila.get("soporte"):
                candidatos.append(fila.get("soporte"))
        except Exception:
            pass
        consultas = []
        origen = str(fila.get("origen") or "")
        id_movimiento = fila.get("id_movimiento")
        if id_movimiento:
            if "pagos_comprobantes" in origen:
                consultas.append(("SELECT archivo_ruta FROM pagos_comprobantes WHERE id = %s",
                                  id_movimiento))
            elif "pagos_clientes" in origen:
                consultas.append(("SELECT archivo_ruta FROM pagos_clientes WHERE id = %s",
                                  id_movimiento))
        if fila.get("id_gasto_compras"):
            consultas.append(("SELECT archivo_ruta FROM facturas_recibidas WHERE id = %s",
                              fila.get("id_gasto_compras")))
        if fila.get("id_factura"):
            consultas.append(("SELECT archivo_ruta FROM facturas_emitidas WHERE id = %s",
                              fila.get("id_factura")))
        # 📎 Asientos MANUALES (y los registrados desde el banco): el archivo está
        #    en la columna 'soporte' de conciliacion_bancaria. Antes no se miraba,
        #    así que el clic derecho no encontraba el comprobante.
        if str(origen) in ("manual", "e") and fila.get("id"):
            consultas.append(("SELECT soporte FROM conciliacion_bancaria WHERE id = %s",
                              fila.get("id")))
        if not consultas:
            return ""
        conn = conectar_db(silencioso=True)
        if not conn:
            return ""
        try:
            with conn.cursor() as c:
                for consulta, parametro in consultas:
                    try:
                        c.execute(consulta, (parametro,))
                        resultado = c.fetchone()
                        if resultado and resultado[0]:
                            candidatos.append(resultado[0])
                            break
                    except Exception:
                        conn.rollback()
        except Exception as error:
            print("[Banco] búsqueda del documento:", error)
        finally:
            liberar_conexion(conn)
        for ruta in candidatos:
            if ruta and self._ruta_existente(ruta):
                return ruta
        return candidatos[0] if candidatos else ""

    def _menu_contextual_movimiento(self, event, tabla, movimientos, acciones=True):
        """Clic derecho: abre un menú para ver la factura y el comprobante.

        Compatible Windows (botón 3), Linux (botón 3) y macOS (botón 2 y
        Control+clic). El menú es nativo (tk.Menu) en los tres sistemas.
        """
        try:
            iid = tabla.identify_row(event.y)
            if not iid:
                return "break"
            tabla.selection_set(iid)
            idx = int(iid) if str(iid).isdigit() else -1
            if idx < 0 or idx >= len(movimientos):
                return "break"
            f = movimientos[idx]
            # 📄 si la fila no trae el archivo, se busca en la base
            try:
                if not f.get("archivo_ruta"):
                    documento = self._documento_de_asiento(f)
                    if documento:
                        f["archivo_ruta"] = documento
            except Exception as error:
                print("[Banco] documento del asiento:", error)
        except Exception:
            return "break"

        menu = tk.Menu(self.parent_frame, tearoff=0)

        ruta_factura = self._ruta_existente(self._archivo_factura_de(f.get("id_factura")))
        if ruta_factura:
            menu.add_command(label="Abrir factura",
                             command=lambda r=ruta_factura: abrir_documento(r))
        else:
            menu.add_command(label="Abrir factura (sin archivo)", state="disabled")

        ruta_soporte = self._ruta_existente(f.get("archivo_ruta"))
        if ruta_soporte:
            menu.add_command(label="Abrir comprobante de pago",
                             command=lambda r=ruta_soporte: abrir_documento(r))
            # 🧾 Si ese comprobante cancela VARIAS facturas, se muestran los números
            try:
                from modulo_compras import facturas_que_cancela
                vinculadas = facturas_que_cancela(ruta_soporte, "") or []
            except Exception:
                vinculadas = []
            if len(vinculadas) > 1:
                numeros = ", ".join(str(x[0]) for x in vinculadas[:8])
                if len(vinculadas) > 8:
                    numeros += "…"
                menu.add_command(
                    label="🧾 Este comprobante cancela %d facturas: %s" % (len(vinculadas), numeros),
                    state="disabled")
                menu.add_command(label="🔎 Ver el detalle de esas facturas",
                                 command=lambda r=ruta_soporte: self._mostrar_facturas_del_comprobante(r))
        else:
            menu.add_command(label="Abrir comprobante (sin archivo)", state="disabled")

        if f.get("tabla") in ("transferencias_bancarias", "tx_comision"):
            menu.add_command(label="Editar transferencia padre",
                             command=lambda: self._abrir_transferencia_desde_movimiento(f))

        if acciones:
            menu.add_separator()
            menu.add_command(label="Conciliar", command=self.conciliar_seleccionados)
            menu.add_command(label="Editar / Corregir", command=self.corregir_movimiento)
            menu.add_command(label="Eliminar", command=self.eliminar_movimiento_conciliacion)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def conciliar_seleccionados(self):
        filas = self._filas_seleccionadas()
        if not filas:
            messagebox.showinfo("Conciliación", "Seleccione uno o más movimientos para conciliar.",
                                parent=self.parent_frame)
            return
        banco = self.banco_seleccionado()
        guardados = 0
        for f in filas:
            # 🛡️ El ✅ se pinta SÓLO si de verdad quedó guardado en la base.
            if self._persistir_conciliado(f, banco):
                f["estado"] = "conciliado"
                guardados += 1
        self.refrescar_tree()
        # 🔄 Volver a calcular el cruce y el aviso naranja: antes solo se pintaba
        #    la fila de verde y los contadores/estado quedaban viejos.
        try:
            self.generar_reporte()
        except Exception as error:
            print("[Conciliación] refresco tras conciliar:", error)
        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Concilió {guardados} movimiento(s) en "
                            f"{construir_etiqueta_banco(banco) if banco else 'banco'}")
        if guardados < len(filas):
            messagebox.showwarning(
                "Conciliación",
                f"Se guardaron {guardados} de {len(filas)} movimiento(s).\n\n"
                f"Los {len(filas) - guardados} restantes NO se pudieron guardar: "
                "revise la conexión (o el detalle en la consola) y vuelva a intentarlo.",
                parent=self.parent_frame)
        else:
            messagebox.showinfo("Conciliación",
                                f"{len(filas)} movimiento(s) conciliado(s) y guardados.",
                                parent=self.parent_frame)

    @staticmethod
    def _clave_conciliado(origen, tabla, idm, fecha, monto, descripcion):
        """Identificador único de un movimiento conciliado (persistido)."""
        try:
            idm = int(idm or 0)
        except Exception:
            idm = 0
        if (origen or "") == "sistema" and idm:
            lado = ("|ingreso" if float(monto or 0) >= 0 else "|egreso") if tabla == "transferencias_bancarias" else ""
            return "sis|%s|%s%s" % (tabla or "", idm, lado)
        if (origen or "") == "estado_cuenta":
            return "ec|%s|%.2f|%s" % (normalizar_fecha(fecha), float(monto or 0), _norm_texto(descripcion))
        return "otro|%s|%s" % (origen or "", idm)

    def _clave_conciliado_fila(self, f):
        return self._clave_conciliado(f.get("origen", ""), f.get("tabla", ""), f.get("id", 0),
                                      f.get("fecha"), f.get("monto"), f.get("descripcion"))

    def cargar_conciliados(self, banco):
        """Movimientos ya conciliados (persistidos) de este banco.

        El origen se guarda como «sistema:<tabla>» o «estado_cuenta», así no hace
        falta ninguna columna extra en la tabla.
        """
        claves = set()
        if not banco:
            return claves
        conn = conectar_db(silencioso=True)
        if not conn:
            return claves
        try:
            with conn.cursor() as c:
                c.execute("""
                    SELECT COALESCE(origen,''), COALESCE(id_movimiento,0),
                           COALESCE(fecha,''), COALESCE(monto,0), COALESCE(descripcion,''), COALESCE(cuenta,'')
                    FROM conciliacion_bancaria
                    WHERE banco = %s AND estado = 'conciliado' AND COALESCE(origen,'') <> 'manual'
                """, (banco.get("banco", ""),))
                for origen, idm, fecha, monto, desc, cuenta in c.fetchall():
                    o = origen or ""
                    if o.startswith("s:"):
                        base, tabla = "sistema", o[2:]
                    elif o.startswith("sistema:"):
                        base, tabla = "sistema", o[8:]
                    elif o in ("e", "estado_cuenta"):
                        base, tabla = "estado_cuenta", ""
                    else:
                        base, tabla = o, ""
                    if tabla in ("transferencias_bancarias", "tx_comision") and _norm_texto(cuenta) != _norm_texto(banco.get("cuenta")):
                        continue
                    claves.add(self._clave_conciliado(base, tabla, idm, fecha, monto, desc))
        except Exception as e:
            print("[Conciliación] No se pudo leer lo conciliado:", e)
        finally:
            liberar_conexion(conn)
        return claves

    def _persistir_conciliado(self, f, banco):
        """Guarda que un movimiento quedó conciliado. Devuelve True si quedó guardado."""
        if not banco:
            return False
        conn = conectar_db(silencioso=True)
        if not conn:
            return False
        try:
            with conn.cursor() as c:
                origen = f.get("origen", "") or ""
                idm = int(f.get("id", 0) or 0)
                tabla = f.get("tabla", "") or ""
                # 📅 Siempre dd/mm/aaaa (las líneas del PDF llegan en ISO)
                fecha = fecha_para_mostrar(f.get("fecha", "") or "")
                monto = float(f.get("monto", 0) or 0)
                desc = f.get("descripcion", "") or ""
                banco_nombre = banco.get("banco", "")

                # Los movimientos MANUALES ya están en la tabla: solo se cambia su estado
                if origen == "manual":
                    c.execute("""UPDATE conciliacion_bancaria
                                 SET estado='conciliado', fecha_conciliacion=%s
                                 WHERE id=%s AND banco=%s""",
                              (datetime.now().strftime("%d/%m/%Y"), idm, banco_nombre))
                    actualizadas = c.rowcount
                    conn.commit()
                    return actualizadas > 0

                origen_persist = ("s:" + tabla) if origen == "sistema" and tabla else ("e" if origen == "estado_cuenta" else origen)
                # 🎯 El MISMO filtro sirve para buscar y para marcar. Antes solo se
                #    buscaba (COUNT) y, si la fila ya existía, se devolvía True SIN
                #    marcarla: el usuario veía "conciliado / guardado" y al recargar
                #    volvía a ❌ para siempre.
                if origen == "sistema" and idm and tabla in ("transferencias_bancarias", "tx_comision"):
                    filtro = ("banco=%s AND cuenta=%s AND origen=%s AND id_movimiento=%s "
                              "AND SIGN(monto)=SIGN(%s)")
                    parametros = (banco_nombre, banco.get("cuenta", ""), origen_persist, idm, monto)
                elif origen == "sistema" and idm:
                    filtro = "banco=%s AND origen=%s AND id_movimiento=%s"
                    parametros = (banco_nombre, origen_persist, idm)
                else:
                    filtro = ("banco=%s AND origen=%s AND COALESCE(fecha,'')=%s "
                              "AND ABS(COALESCE(monto,0)-%s) < 0.005")
                    parametros = (banco_nombre, origen_persist, fecha, monto)

                c.execute("SELECT COUNT(*) FROM conciliacion_bancaria WHERE " + filtro, parametros)
                ya_existia = c.fetchone()[0] > 0
                if ya_existia:
                    # ✅ La fila ya estaba (la crea el sync de pagos/cobros con
                    #    estado=''): se MARCA conciliada, no se duplica.
                    c.execute("UPDATE conciliacion_bancaria SET estado='conciliado', "
                              "fecha_conciliacion=%s WHERE " + filtro,
                              (datetime.now().strftime("%d/%m/%Y"),) + parametros)
                    actualizadas = c.rowcount
                    conn.commit()
                    try:
                        self._ya_conciliados = getattr(self, "_ya_conciliados", 0) + 1
                    except Exception:
                        pass
                    return actualizadas > 0
                # 🆕 No existía: se registra ya conciliada.
                c.execute("""INSERT INTO conciliacion_bancaria
                    (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado, fecha_conciliacion)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'conciliado',%s)""",
                    (banco_nombre, banco.get("cuenta", ""), fecha, desc, monto,
                     f.get("tipo", "ingreso"), origen_persist, idm,
                     datetime.now().strftime("%d/%m/%Y")))
                conn.commit()
                return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print("[Conciliación] No se pudo guardar:", e)
            return False
        finally:
            liberar_conexion(conn)

    def escanear_comprobante_pago(self, padre, al_terminar):
        """🖨️ Escanea el comprobante de pago con la multifuncional y arma un PDF.

        El escaneo corre en segundo plano; al terminar se llama a al_terminar(ruta)
        con la ruta del PDF (o '' si no se pudo escanear).
        """
        try:
            import escaner
        except Exception as e:
            messagebox.showerror("Escáner", "No se pudo cargar el módulo de escaneo:\n%s" % e, parent=padre)
            return al_terminar("")
        try:
            if not escaner.sistema_soportado():
                messagebox.showinfo("Escáner", "El escaneo con la multifuncional está disponible "
                                    "en Windows y en macOS (M1/M2).", parent=padre)
                return al_terminar("")
            # ⚙️ La configuración del escáner se lee del PROPIO módulo (es la misma
            #    que usa Compras, donde el escaneo funciona). Antes se importaba
            #    control_general y esa carga podía dejar la ventana colgada.
            # 🛠️ Aquí se llamaba a 'cargar_configuracion_regional()', que NO existe
            #    en este archivo: el NameError se tragaba el except y el escaneo
            #    usaba siempre los valores por defecto (ignoraba dispositivo, DPI,
            #    color y modo). Se arma con los valores del módulo + los del equipo.
            config = {}
            try:
                config = dict(cargar_config() or {})
            except Exception:
                config = {}
            try:
                config.update(leer_config_disco() or {})
            except Exception as e_cfg:
                print("[Banco] configuración del escáner:", e_cfg)
        except Exception as e:
            messagebox.showerror("Escáner", "No se pudo leer la configuración del escáner:\n%s" % e, parent=padre)
            return al_terminar("")

        ventana = padre if padre is not None else getattr(self, "main_root", None)
        prog = ctk.CTkToplevel(ventana)
        prog.title("Escaneando comprobante de pago")
        prog.geometry("430x150")
        try:
            prog.transient(ventana)
            prog.grab_set()
            # 🪟 La ventanita de escaneo va SIEMPRE encima mientras se escanea
            prog.lift()
            prog.attributes("-topmost", True)
            prog.focus_force()
        except Exception:
            pass
        ctk.CTkLabel(prog, text="🖨️ Escaneando el comprobante de pago...",
                     font=(FAMILIA, 12, "bold"), text_color="#0e7490").pack(pady=(20, 4))
        ctk.CTkLabel(prog, text="Coloque el comprobante en el escáner (cristal).",
                     font=(FAMILIA, 10), text_color="gray").pack()
        # ⏳ Barra SIN animación: la animación deja temporizadores pendientes y
        #    hacía que update_idletasks() no retornara (la ventana se quedaba ahí).
        barra = ctk.CTkProgressBar(prog, width=330, mode="determinate")
        barra.pack(pady=10)
        try:
            barra.set(0.35)
        except Exception:
            pass

        resultado = {"ok": False, "pdf": "", "error": "", "numero": "", "fecha": "", "monto": 0.0}

        def tarea():
            try:
                datos = escaner.config_escaner(config)
                carpeta = escaner.carpeta_escaneos(config)
                esc = _escanear_con_progreso(ventana, "el comprobante", escaner, 
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
                                       "Comprobante_Pago_%s.pdf" % marca)
                    escaner.imagenes_a_pdf(esc["paginas"], pdf)
                    resultado.update({"ok": True, "pdf": pdf})
                    # 🧠 OCR: se leen N° de operación, fecha e importe del comprobante
                    try:
                        texto_ocr = escaner.texto_de_resultados(escaner.ocr_paginas(esc["paginas"]))
                    except Exception as e_ocr:
                        texto_ocr = ""
                        print("Aviso - OCR del comprobante:", e_ocr)
                    try:
                        from modulo_compras import (extraer_numero_operacion,
                                                    extraer_fecha_comprobante)
                        resultado["numero"] = extraer_numero_operacion(texto_ocr)
                        resultado["fecha"] = extraer_fecha_comprobante(texto_ocr)
                    except Exception as e_op:
                        print("Aviso - número/fecha del comprobante:", e_op)
                    try:
                        resultado["monto"] = monto_del_comprobante(texto_ocr)
                    except Exception as e_mo:
                        print("Aviso - monto del comprobante:", e_mo)
                else:
                    resultado["error"] = esc.get("error") or "No se pudo escanear."
            except Exception as e:
                resultado["error"] = str(e)
            resultado["listo"] = True

        def terminar():
            try:
                prog.grab_release()
                prog.attributes("-topmost", False)
                prog.destroy()
            except Exception:
                pass
            # 🪟 Al cerrar la ventanita, la de "Registrar Pago" vuelve AL FRENTE
            try:
                if padre is not None and padre.winfo_exists():
                    padre.deiconify()
                    padre.lift()
                    padre.focus_force()
                    padre.attributes("-topmost", True)
                    padre.after(600, lambda: padre.winfo_exists() and padre.attributes("-topmost", False))
                    try:
                        padre.grab_set()
                    except Exception:
                        pass
            except Exception:
                pass
            if not resultado["ok"]:
                messagebox.showerror("Escáner", resultado["error"] or "No se pudo escanear.", parent=padre)
            try:
                al_terminar(resultado["pdf"], resultado.get("numero", ""),
                            resultado.get("fecha", ""), resultado.get("monto", 0.0))
            except TypeError:
                try:
                    al_terminar(resultado["pdf"], resultado.get("numero", ""),
                                resultado.get("fecha", ""))
                except TypeError:
                    try:
                        al_terminar(resultado["pdf"])
                    except TypeError:
                        al_terminar()
        # ⚠️ El escaneo (WIA por PowerShell) debe correr en el HILO PRINCIPAL:
        #    en un hilo aparte el proceso de PowerShell se queda esperando y la
        #    ventana nunca sale de "Escaneando...". El corte de tiempo está en
        #    escaner._ejecutar_ps (90 s), que libera el equipo si no responde.
        try:
            # Solo update_idletasks(): con la barra animada, update() procesa
            # los temporizadores de la animación y NO retorna nunca.
            prog.update_idletasks()
        except Exception:
            pass
        tarea()
        terminar()

    def _comprobante_de_movimiento(self, registro):
        """📎 Comprobante de pago de un movimiento del banco.

        La tabla de movimientos no guarda el archivo: se busca en el gasto de
        Compras ('facturas_recibidas') o en el pago ('pagos_comprobantes')
        enlazados por 'conciliacion_bancaria.id_gasto_compras'.
        Devuelve '' si no hay ninguno (nunca falla).
        """
        try:
            ruta = str(registro.get("archivo_ruta") or "").strip()
            if ruta:
                return ruta
            # Un id virtual de transferencia nunca es un id de pago manual.
            if registro.get("tabla") in ("transferencias_bancarias", "tx_comision"):
                return ""
            id_gasto = int(registro.get("id_gasto_compras") or 0)
            conn = conectar_db(silencioso=True)
            if not conn:
                return ""
            try:
                with conn.cursor() as c:
                    if not id_gasto:
                        mov_id = registro.get("id")
                        if mov_id:
                            c.execute("SELECT COALESCE(id_gasto_compras, 0) "
                                      "FROM conciliacion_bancaria WHERE id = %s", (mov_id,))
                            fila = c.fetchone()
                            id_gasto = int(fila[0] or 0) if fila else 0
                    if not id_gasto:
                        return ""
                    c.execute("SELECT COALESCE(archivo_ruta, '') FROM facturas_recibidas WHERE id = %s",
                              (id_gasto,))
                    fila = c.fetchone()
                    ruta = str(fila[0] or "").strip() if fila else ""
                    if ruta:
                        return ruta
                    c.execute("SELECT COALESCE(archivo_ruta, '') FROM pagos_comprobantes "
                              "WHERE id_factura = %s ORDER BY id DESC LIMIT 1", (id_gasto,))
                    fila = c.fetchone()
                    return str(fila[0] or "").strip() if fila else ""
            finally:
                liberar_conexion(conn)
        except Exception as e:
            print("[Banco] No se pudo leer el comprobante del movimiento:", e)
            return ""

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
            "chofer": str(registro.get("chofer") or ""),
            "numero_operacion": str(registro.get("numero_operacion") or ""),
            "comision": float(registro.get("comision") or 0),
            "interbancario": bool(registro.get("interbancario")),
            "es_cruzada": bool(registro.get("es_cruzada")),
            # 📎 Comprobante de pago ya cargado (se conserva al editar):
            #    se busca en el gasto de Compras o en el pago vinculado.
            "comprobante": self._comprobante_de_movimiento(registro),
            "descripcion": "",
        }
        libres = []
        for parte in [p.strip() for p in str(registro.get("descripcion") or "").split(" - ") if p.strip()]:
            bajo = parte.lower()
            if bajo.startswith("evento "):
                datos["evento"] = datos["evento"] or parte[6:].strip()
            elif bajo.startswith("chofer "):
                datos["chofer"] = datos["chofer"] or parte[7:].strip()
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

    def asentar_itf(self):
        """ITF manual: pide fecha, N° de transaccion y monto, y crea el asiento."""
        banco = self.banco_seleccionado()
        if not banco:
            return messagebox.showwarning("Banco", "Seleccione un banco.", parent=self.parent_frame)

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Asentar ITF")
        v.geometry("460x360")
        v.transient(self.parent_frame)
        traer_al_frente(v)
        try:
            v.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(v, text="ITF - Impuesto a las Transacciones Financieras",
                     font=(FAMILIA, 14, "bold"), text_color="#1f538d").pack(pady=(16, 2))
        ctk.CTkLabel(v, text="Banco: " + str(banco.get("banco", "")) + " - " + str(banco.get("cuenta", "")),
                     font=(FAMILIA, 11)).pack(pady=(0, 10))

        f = ctk.CTkFrame(v, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f, text="Fecha (dd/mm/aaaa):", width=150, anchor="w",
                     font=(FAMILIA, 12, "bold")).pack(side="left")
        ent_fecha = ctk.CTkEntry(f, width=170)
        ent_fecha.pack(side="left")
        ent_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ctk.CTkButton(f, text="📅", width=42, font=(FAMILIA, 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        f2 = ctk.CTkFrame(v, fg_color="transparent")
        f2.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f2, text="N° de transaccion:", width=150, anchor="w",
                     font=(FAMILIA, 12, "bold")).pack(side="left")
        ent_num = ctk.CTkEntry(f2, width=200)
        ent_num.pack(side="left")

        f3 = ctk.CTkFrame(v, fg_color="transparent")
        f3.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f3, text="Monto del ITF (S/):", width=150, anchor="w",
                     font=(FAMILIA, 12, "bold")).pack(side="left")
        ent_monto = ctk.CTkEntry(f3, width=200)
        ent_monto.pack(side="left")

        def guardar():
            fecha = (ent_fecha.get() or "").strip()
            numero = (ent_num.get() or "").strip()
            texto_monto = (ent_monto.get() or "").strip().replace(",", ".")
            if not fecha:
                return messagebox.showwarning("ITF", "Indique la fecha.", parent=v)
            if not numero:
                return messagebox.showwarning("ITF", "Indique el N° de transaccion.", parent=v)
            try:
                monto = float(texto_monto)
            except Exception:
                return messagebox.showwarning("ITF", "El monto no es valido.", parent=v)
            if monto <= 0:
                return messagebox.showwarning("ITF", "El monto debe ser mayor a cero.", parent=v)
            if self._guardar_itf(banco, fecha, numero, monto):
                try:
                    self.generar_reporte()
                except Exception:
                    try:
                        self.ver_movimientos(banco)
                    except Exception:
                        pass
                messagebox.showinfo("ITF", "Asiento de ITF registrado por S/ %.2f." % monto, parent=v)
                v.destroy()
            else:
                messagebox.showerror("ITF", "No se pudo registrar el ITF.", parent=v)

        f_pie = ctk.CTkFrame(v, fg_color="transparent")
        f_pie.pack(side="bottom", fill="x", pady=16)
        ctk.CTkButton(f_pie, text="Guardar ITF", width=150, fg_color="#27ae60", hover_color="#1e8449",
                      font=(FAMILIA, 12, "bold"), command=guardar).pack(side="left", padx=40)
        ctk.CTkButton(f_pie, text="Cancelar", width=110, fg_color="#7f8c8d", hover_color="#606b6b",
                      command=v.destroy).pack(side="left")

    def _guardar_itf(self, banco, fecha, numero_operacion, monto):
        """Inserta el asiento del ITF en la conciliacion (egreso, manual)."""
        conn = conectar_db(silencioso=True)
        if not conn:
            return False
        try:
            with conn.cursor() as c:
                c.execute("""INSERT INTO conciliacion_bancaria
                    (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado,
                     categoria, subcategoria, numero_operacion, tabla)
                    VALUES (%s, %s, %s, %s, %s, 'egreso', 'manual', 0, 'pendiente', 'ITF', '', %s, 'itf')""",
                    (banco.get("banco", ""), banco.get("cuenta", ""), fecha,
                     "ITF - Transaccion N " + str(numero_operacion),
                     -abs(float(monto)), str(numero_operacion)))
            conn.commit()
            return True
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print("Aviso - asentando ITF:", e)
            return False
        finally:
            liberar_conexion(conn)


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

        try:
            _asegurar_columna_soporte()
        except Exception:
            pass

        cats = cargar_categorias_gastos()
        principales = ordenar_categorias_principales(list(cats.keys())) or ["Gastos Operativos"]
        comision = cargar_comision_interbancaria()
        en_edicion = bool(registro)
        datos_ini = (self._datos_iniciales_pago(registro, cats, principales) if en_edicion else {})
        id_edicion = registro.get("id") if en_edicion else None

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Editar Movimiento (Conciliación)" if en_edicion
                else "Registrar Ingreso / Egreso (Conciliación)")
        v.geometry("580x700")
        v.minsize(520, 480)
        v.transient(self.parent_frame)
        traer_al_frente(v)
        v.grab_set()

        TITULO_BASE = ("✏️ Editar Ingreso / Egreso" if en_edicion
                       else "➕ Registro de Ingresos / Egresos / Cruzadas")
        lbl_titulo = ctk.CTkLabel(v, text=TITULO_BASE,
                                  font=(FAMILIA, 15, "bold"), text_color="#1f538d")
        lbl_titulo.pack(pady=(15, 5))

        # 💰 EGRESO (pago) o INGRESO (cobro)
        f_tipo = ctk.CTkFrame(v, fg_color="transparent")
        f_tipo.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(f_tipo, text="Tipo de movimiento:", font=(FAMILIA, 11, "bold")
                     ).pack(side="left", padx=(0, 8))
        seg_tipo = ctk.CTkSegmentedButton(f_tipo, values=["Egreso (pago)", "Ingreso (cobro)"],
                                          font=(FAMILIA, 10, "bold"))
        seg_tipo.set("Ingreso (cobro)" if str(datos_ini.get("tipo") or "").lower() == "ingreso"
                     else "Egreso (pago)")
        seg_tipo.pack(side="left")

        # Pie fijo: el botón Guardar siempre visible
        f_pie = ctk.CTkFrame(v, fg_color="transparent")
        f_pie.pack(side="bottom", fill="x")

        # Área desplazable (scroll) con todos los campos
        f = ctk.CTkScrollableFrame(v, fg_color="transparent")
        f.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        principal_ini = str(datos_ini.get("principal") or "")
        if principal_ini and principal_ini not in principales:
            principales = principales + [principal_ini]

        ctk.CTkLabel(f, text="Categoría Principal:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        cmb_principal = ctk.CTkComboBox(f, values=principales, width=300, state="readonly")
        cmb_principal.pack(fill="x", pady=(0, 8))
        cmb_principal.set(principal_ini or principales[0])

        sub_ini = str(datos_ini.get("sub") or "")
        ctk.CTkLabel(f, text="Categoría:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        subs_inicial = list(cats.get(cmb_principal.get(), []) or ["(Sin categoría)"])
        if sub_ini and sub_ini not in subs_inicial:
            subs_inicial.append(sub_ini)
        cmb_sub = ctk.CTkComboBox(f, values=subs_inicial, width=300, state="readonly")
        cmb_sub.pack(fill="x", pady=(0, 8))
        cmb_sub.set(sub_ini or subs_inicial[0])

        # Tercer desplegable: evento (solo para Gastos Operativos)
        eventos = cargar_eventos()
        lbl_evento = ctk.CTkLabel(f, text="Evento Asociado:", font=(FAMILIA, 11, "bold"))
        # "Ninguno" primero: así un pago que no es de un vehículo (o una edición
        # que no tenía evento) no queda con una evento asignada por descuido.
        cmb_evento = ctk.CTkComboBox(f, values=["Ninguno"] + eventos, width=300, state="readonly")
        cmb_evento.set("Ninguno")

        # Cuarto desplegable: chofer (solo para categorías de Planilla / Sueldos)
        choferes = cargar_choferes()
        lbl_chofer = ctk.CTkLabel(f, text="Chofer (planilla):", font=(FAMILIA, 11, "bold"))
        cmb_chofer = ctk.CTkComboBox(f, values=["Ninguno"] + choferes, width=300, state="readonly")
        cmb_chofer.set("Ninguno")

        def actualizar_chofer():
            if es_categoria_planilla(f"{cmb_principal.get()} {cmb_sub.get()}"):
                lbl_chofer.pack(anchor="w", pady=(0, 2), before=btn_gestion)
                cmb_chofer.pack(fill="x", pady=(0, 8), before=btn_gestion)
            else:
                lbl_chofer.pack_forget()
                cmb_chofer.pack_forget()

        def on_principal(_=None):
            subs = cats.get(cmb_principal.get(), []) or ["(Sin categoría)"]
            cmb_sub.configure(values=subs)
            cmb_sub.set(subs[0])
            if cmb_principal.get() == "Gastos Operativos":
                lbl_evento.pack(anchor="w", pady=(0, 2), before=btn_gestion)
                cmb_evento.pack(fill="x", pady=(0, 8), before=btn_gestion)
            else:
                lbl_evento.pack_forget()
                cmb_evento.pack_forget()
            actualizar_chofer()
        cmb_principal.configure(command=on_principal)
        cmb_sub.configure(command=lambda _=None: actualizar_chofer())

        def _al_cambiar_tipo(_valor=None):
            """Pone la categoría Ingresos al elegir ingreso y cambia el título."""
            es_ingreso = seg_tipo.get().startswith("Ingreso")
            try:
                if es_ingreso:
                    if "Ingresos" in (cmb_principal.cget("values") or []):
                        cmb_principal.set("Ingresos")
                        on_principal()
                    lbl_titulo.configure(text="➕ Registro de Ingreso (cobro)")
                else:
                    if cmb_principal.get() == "Ingresos":
                        otras = [p for p in (cmb_principal.cget("values") or [])
                                 if p != "Ingresos"]
                        if otras:
                            cmb_principal.set(otras[0])
                            on_principal()
                    lbl_titulo.configure(text=("✏️ Editar Ingreso / Egreso" if en_edicion
                                               else "➕ Registro de Egreso (pago)"))
            except Exception as e_tipo:
                print("[Banco] tipo de movimiento:", e_tipo)

        seg_tipo.configure(command=_al_cambiar_tipo)

        def recargar_categorias_combos():
            nonlocal cats, principales
            cats = cargar_categorias_gastos()
            principales = ordenar_categorias_principales(list(cats.keys())) or ["Gastos Operativos"]
            cmb_principal.configure(values=principales)
            cmb_principal.set(principales[0])
            on_principal()

        def abrir_gestion():
            # Misma ventana que usa el módulo de Compras ("⚙️" del formulario):
            # al guardar y cerrar se recargan aquí los desplegables.
            self.gestionar_categorias(al_guardar=recargar_categorias_combos)
        btn_gestion = ctk.CTkButton(f, text="⚙️ Gestionar Categorías", height=26, font=(FAMILIA, 11),
                                    fg_color="#8e44ad", hover_color="#703688", command=abrir_gestion)
        btn_gestion.pack(fill="x", pady=(0, 8))
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
                valores_evento.append(evento_ini)          # evento que ya no está en la flota
                cmb_evento.configure(values=valores_evento)
            cmb_evento.set(evento_ini)
        chofer_ini = str(datos_ini.get("chofer") or "")
        if chofer_ini:
            valores_chofer = list(cmb_chofer.cget("values"))
            if chofer_ini not in valores_chofer:
                valores_chofer.append(chofer_ini)
                cmb_chofer.configure(values=valores_chofer)
            cmb_chofer.set(chofer_ini)
        actualizar_chofer()

        ctk.CTkLabel(f, text="Fecha (DD/MM/AAAA):", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(f, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 8))
        ent_fecha = ctk.CTkEntry(f_fecha)
        ent_fecha.pack(side="left", fill="x", expand=True)
        ent_fecha.insert(0, str(datos_ini.get("fecha") or datetime.now().strftime("%d/%m/%Y")))
        ctk.CTkButton(f_fecha, text="📅", width=42, font=(FAMILIA, 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(f, text="N° de Operación (opcional):", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        ent_operacion = ctk.CTkEntry(f, placeholder_text="Ej.: 00123456 / constancia de la transferencia")
        ent_operacion.pack(fill="x", pady=(0, 8))
        ent_operacion.insert(0, str(datos_ini.get("numero_operacion") or ""))

        ctk.CTkLabel(f, text="Descripción:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        ent_desc = ctk.CTkEntry(f)
        ent_desc.pack(fill="x", pady=(0, 8))
        ent_desc.insert(0, str(datos_ini.get("descripcion") or ""))

        # 📎 Comprobante del movimiento: se puede ESCANEAR CON EL TELÉFONO
        estado_soporte = {"ruta": str(datos_ini.get("soporte") or "")}
        ctk.CTkLabel(f, text="Comprobante (soporte del banco):",
                     font=(FAMILIA, 11, "bold")).pack(anchor="w")
        f_soporte = ctk.CTkFrame(f, fg_color="transparent")
        f_soporte.pack(fill="x", pady=(0, 4))
        btn_soporte_tel = ctk.CTkButton(f_soporte, text="📱 Escanear con el teléfono", width=190,
                                        font=(FAMILIA, 10, "bold"), fg_color="#5b2c6f",
                                        hover_color="#4a2360")
        btn_soporte_tel.pack(side="left")
        ctk.CTkButton(f_soporte, text="📎 Archivo", width=90, font=(FAMILIA, 10, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: _elegir_soporte_archivo()).pack(side="left", padx=6)
        ctk.CTkButton(f_soporte, text="🗏 Ver", width=70, font=(FAMILIA, 10, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: _ver_soporte()).pack(side="left")
        lbl_soporte = ctk.CTkLabel(f, text="", font=(FAMILIA, 9), text_color="#7f8c8d")
        lbl_soporte.pack(anchor="w", pady=(0, 8))

        def _pintar_soporte():
            ruta_sop = estado_soporte["ruta"]
            if ruta_sop and os.path.exists(ruta_sop):
                lbl_soporte.configure(text="📎 " + os.path.basename(ruta_sop),
                                      text_color="#1e8449")
            elif ruta_sop:
                lbl_soporte.configure(text="📎 " + os.path.basename(ruta_sop) +
                                      "  (no está en este equipo)", text_color="#b9770e")
            else:
                lbl_soporte.configure(text="Sin comprobante adjunto", text_color="#7f8c8d")

        def _fijar_soporte(ruta_sop):
            estado_soporte["ruta"] = str(ruta_sop or "")
            _pintar_soporte()
            # 🔁 Un mismo comprobante sirve para los dos campos: si el de PAGO está
            #    vacío, se copia ahí también.
            try:
                if ruta_sop and not (ruta_comprobante.get("ruta") or ""):
                    ruta_comprobante["ruta"] = str(ruta_sop)
                    actualizar_lbl_comprobante()
            except Exception as error_sync:
                print("[Banco] sincronizar comprobante:", error_sync)

        def _ver_soporte():
            ruta_sop = estado_soporte["ruta"]
            if ruta_sop and os.path.exists(ruta_sop):
                abrir_documento(ruta_sop, "Comprobante del movimiento")
            else:
                messagebox.showinfo("Comprobante", "No hay comprobante adjunto.", parent=v)

        def _elegir_soporte_archivo():
            elegido = seleccionar_archivo_dialogo(
                "Comprobante del movimiento",
                tipos=[("Documentos", "*.pdf *.jpg *.jpeg *.png"), ("Todos", "*.*")])
            if elegido:
                _fijar_soporte(elegido[0] if isinstance(elegido, (list, tuple)) else elegido)

        def _escanear_soporte_telefono():
            try:
                import escaner_movil
            except Exception as error_esc:
                messagebox.showerror("Escáner",
                                     "No se pudo cargar el escáner: " + str(error_esc), parent=v)
                return
            try:
                config_esc = cargar_config()
            except Exception:
                config_esc = {}

            def _completar_con_texto(texto):
                """🧠 Con el texto del OCR completa N° de operación, fecha y monto."""
                if not texto:
                    return
                avisos = []
                try:
                    from modulo_compras import (extraer_numero_operacion,
                                                extraer_fecha_comprobante)
                    numero_leido = extraer_numero_operacion(texto)
                    fecha_leida = extraer_fecha_comprobante(texto)
                except Exception as error_op:
                    print("[Banco] OCR número/fecha:", error_op)
                    numero_leido, fecha_leida = "", ""
                try:
                    monto_leido = monto_del_comprobante(texto)
                except Exception:
                    monto_leido = 0.0
                try:
                    if numero_leido and not ent_operacion.get().strip():
                        ent_operacion.insert(0, numero_leido)
                        avisos.append("N° operación: " + numero_leido)
                except Exception:
                    pass
                try:
                    if fecha_leida:
                        ent_fecha.delete(0, "end")
                        ent_fecha.insert(0, fecha_leida)
                        avisos.append("Fecha: " + fecha_leida)
                except Exception:
                    pass
                try:
                    if monto_leido and not ent_monto.get().strip():
                        from modulo_compras import formatear_numero_entrada
                        ent_monto.insert(0, formatear_numero_entrada("%.2f" % float(monto_leido)))
                        avisos.append("Monto: %.2f" % float(monto_leido))
                except Exception:
                    pass
                if avisos:
                    try:
                        messagebox.showinfo(
                            "Comprobante",
                            "Se leyó el comprobante con OCR y se completó:\n"
                            + " · ".join(avisos), parent=v)
                    except Exception:
                        pass

            def al_recibir(rutas, tipo_doc):
                if not rutas:
                    return
                _fijar_soporte(rutas[0])
                # 🧠 OCR en segundo plano: no congela la ventana mientras lee
                paginas = list(rutas or [])

                def trabajo_ocr():
                    texto = ""
                    try:
                        import escaner as _esc
                        texto = _esc.texto_de_resultados(_esc.ocr_paginas(paginas)) or ""
                    except Exception as error_ocr:
                        print("[Banco] OCR del comprobante:", error_ocr)
                    try:
                        v.after(0, lambda: _completar_con_texto(texto))
                    except Exception:
                        pass

                try:
                    threading.Thread(target=trabajo_ocr, daemon=True).start()
                except Exception as error_hilo:
                    print("[Banco] hilo de OCR:", error_hilo)
                    _completar_con_texto("")

            try:
                escaner_movil.escanear_desde_telefono(
                    v, config_esc, al_recibir=al_recibir,
                    titulo_extra="Banco · comprobante del movimiento")
            except Exception as error_esc:
                messagebox.showerror("Escáner",
                                     "No se pudo abrir el escáner: " + str(error_esc), parent=v)

        btn_soporte_tel.configure(command=_escanear_soporte_telefono)
        _pintar_soporte()

        ctk.CTkLabel(f, text="Monto:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        ent_monto = ctk.CTkEntry(f)
        ent_monto.pack(fill="x", pady=(0, 8))
        if datos_ini.get("monto"):
            ent_monto.insert(0, formatear_numero_entrada(f"{float(datos_ini['monto']):.2f}"))
        ent_monto.bind("<KeyRelease>", lambda e: self._formatear_entrada_monto(ent_monto))
        ent_monto.bind("<FocusOut>", lambda e: self._formatear_entrada_monto(ent_monto))

        # 💰 Igual que en Compras: el monto se puede digitar SIN IGV (monto base) o
        # CON IGV (total del documento); lo que falte se calcula automáticamente.
        ctk.CTkLabel(f, text="Tipo de Monto a Ingresar:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        seg_monto = ctk.CTkSegmentedButton(
            f, values=["Monto Base (sin IGV)", "Monto con IGV (Total)"],
            font=(FAMILIA, 10, "bold"), command=lambda _v: actualizar_total())
        seg_monto.pack(fill="x", pady=(0, 6))
        seg_monto.set("Monto Base (sin IGV)")

        f_igv = ctk.CTkFrame(f, fg_color="transparent")
        f_igv.pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(f_igv, text="IGV (%):", font=(FAMILIA, 11, "bold")).pack(side="left")
        ent_igv = ctk.CTkEntry(f_igv, width=70)
        ent_igv.pack(side="left", padx=(6, 0))
        ent_igv.insert(0, "0")
        ent_igv.bind("<KeyRelease>", lambda e: actualizar_total())

        lbl_igv_det = ctk.CTkLabel(f, text="", font=(FAMILIA, 10), text_color="#555555",
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
        ctk.CTkLabel(f_com, text="Comisión (S/.):", font=(FAMILIA, 11, "bold")).pack(side="left")
        ent_comision = ctk.CTkEntry(f_com, width=90)
        ent_comision.pack(side="left", padx=6)
        comision_ini = datos_ini.get("comision")
        ent_comision.insert(0, f"{(float(comision_ini) if comision_ini else comision):.2f}")
        lbl_total = ctk.CTkLabel(f_com, text=f"Total: {formatear_monto(0)}", font=(FAMILIA, 12, "bold"),
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
        # 📎 COMPROBANTE DE PAGO (opcional): PDF o imagen del voucher
        # ------------------------------------------------------------------
        ruta_comprobante = {"ruta": str(datos_ini.get("comprobante") or "")}
        ctk.CTkLabel(f, text="📎 Comprobante de pago (opcional):",
                     font=(FAMILIA, 11, "bold")).pack(anchor="w", pady=(6, 0))
        f_comp = ctk.CTkFrame(f, fg_color="transparent")
        f_comp.pack(fill="x", pady=(0, 4))
        lbl_comprobante = ctk.CTkLabel(f, text="Sin comprobante cargado", font=(FAMILIA, 10),
                                       text_color="#999999", anchor="w", justify="left")

        def actualizar_lbl_comprobante():
            ruta = ruta_comprobante.get("ruta") or ""
            if ruta:
                lbl_comprobante.configure(text="📎 " + os.path.basename(ruta), text_color="#1e8449")
            else:
                lbl_comprobante.configure(text="Sin comprobante cargado", text_color="#999999")

        def _fijar_comprobante(ruta_comp):
            """Fija el comprobante de pago y, si no hay soporte del banco, lo copia."""
            ruta_comprobante["ruta"] = str(ruta_comp or "")
            actualizar_lbl_comprobante()
            try:
                if ruta_comp and not (estado_soporte.get("ruta") or ""):
                    _fijar_soporte(ruta_comp)
            except Exception as error_sync:
                print("[Banco] sincronizar soporte:", error_sync)

        def cargar_comprobante():
            try:
                from tkinter import filedialog
                ruta = filedialog.askopenfilename(
                    parent=v, title="Seleccionar el comprobante de pago",
                    filetypes=[("PDF o imagen", "*.pdf *.jpg *.jpeg *.png"), ("Todos los archivos", "*.*")])
            except Exception as e:
                return messagebox.showerror("Comprobante", "No se pudo abrir el selector: " + str(e), parent=v)
            if not ruta:
                return
            _fijar_comprobante(ruta)

        def ver_comprobante():
            ruta = ruta_comprobante.get("ruta") or ""
            if ruta and os.path.isfile(ruta):
                abrir_documento(ruta)
            elif ruta:
                messagebox.showwarning("Comprobante", "El archivo ya no está en esa ubicación:\n" + ruta, parent=v)
            else:
                messagebox.showinfo("Comprobante",
                                    "Todavía no ha cargado el comprobante de pago.\n"
                                    "Use el botón «📎 Cargar comprobante».", parent=v)

        ctk.CTkButton(f_comp, text="📎 Cargar comprobante de pago (PDF/JPG)", height=30,
                      font=(FAMILIA, 11, "bold"), fg_color="#16a085", hover_color="#11806a",
                      command=cargar_comprobante).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_comp, text="👁️ Ver", width=70, height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#5d6d7e", hover_color="#4a5967",
                      command=ver_comprobante).pack(side="left", padx=(6, 0))

        def escanear_comprobante():
            """🖨️ Escanea el comprobante con la multifuncional."""
            def al_terminar(ruta, numero="", fecha_leida="", monto_leido=0.0):
                if not ruta:
                    return
                _fijar_comprobante(ruta)
                avisos = []
                try:
                    if numero and not ent_operacion.get().strip():
                        ent_operacion.insert(0, numero)
                        avisos.append("N° de operación: " + numero)
                except Exception:
                    pass
                try:
                    if fecha_leida:
                        ent_fecha.delete(0, "end")
                        ent_fecha.insert(0, fecha_leida)
                        avisos.append("Fecha: " + fecha_leida)
                except Exception:
                    pass
                try:
                    if monto_leido and not ent_monto.get().strip():
                        from modulo_compras import formatear_numero_entrada
                        ent_monto.insert(0, formatear_numero_entrada("%.2f" % float(monto_leido)))
                        avisos.append("Monto: %.2f" % float(monto_leido))
                except Exception:
                    pass
                detalle = ("\n\nSe completó: " + " · ".join(avisos)) if avisos else ""
                messagebox.showinfo("Comprobante",
                                    "Se escaneó el comprobante de pago:\n"
                                    + os.path.basename(ruta) + detalle, parent=v)
            self.escanear_comprobante_pago(v, al_terminar)

        ctk.CTkButton(f_comp, text="🖨️ Escanear", width=120, height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#0e7490", hover_color="#155e75",
                      command=escanear_comprobante).pack(side="left", padx=(6, 0))

        def escanear_comprobante_telefono():
            """📱 Abre el QR y espera la foto del teléfono."""
            try:
                import escaner_movil as _mov
                _mov.escanear_reusando_flujo(
                    parent=v,
                    config=cargar_config(),
                    funcion=escanear_comprobante,
                    etiqueta="Banco · comprobante de pago")
            except Exception as e:
                messagebox.showerror("Escáner por teléfono", str(e), parent=v)


        lbl_comprobante.pack(anchor="w", pady=(0, 6))
        actualizar_lbl_comprobante()

        # 📱 Escanear el comprobante con el teléfono (fila propia, ancho completo)
        _fila_tel_banco = ctk.CTkFrame(f, fg_color="transparent")
        _fila_tel_banco.pack(fill="x", pady=(0, 6))
        ctk.CTkButton(_fila_tel_banco, text="📱 Escanear con el teléfono",
                      height=30, font=(FAMILIA, 11, "bold"),
                      fg_color="#8e44ad", hover_color="#6c3483",
                      command=escanear_comprobante_telefono
                      ).pack(side="left", fill="x", expand=True)

        # ------------------------------------------------------------------
        # UNIFICACIÓN CON COMPRAS CRUZADAS (pago a tercero)
        # Al marcar el check se habilitan los datos del tercero y el PDF del soporte
        # ------------------------------------------------------------------
        var_cruzada = tk.BooleanVar(value=False)
        chk_cruzada = ctk.CTkCheckBox(f, text="🔁 Es compra cruzada (factura pagada por un tercero)",
                                      font=(FAMILIA, 11, "bold"), variable=var_cruzada)
        if not en_edicion:
            chk_cruzada.pack(anchor="w", pady=(4, 4))
        elif datos_ini.get("es_cruzada"):
            # La compra cruzada ya quedó registrada en Compras: al editar no se repite
            ctk.CTkLabel(f, text=("🔁 Este pago se registró como COMPRA CRUZADA en el módulo de Compras.\n"
                                  "Aquí solo se modifican los datos del movimiento bancario."),
                         font=(FAMILIA, 11, "italic"), text_color="#7d3c98",
                         justify="left").pack(anchor="w", pady=(6, 4))

        estado_cruzada = {"soporte": "", "factura": ""}
        f_cruzada = ctk.CTkFrame(f, fg_color="#f4ecf7", corner_radius=8,
                                 border_width=1, border_color="#d7bde2")

        ctk.CTkLabel(f_cruzada, text="Proveedor de la factura:", font=(FAMILIA, 11, "bold")).pack(
            anchor="w", padx=10, pady=(8, 0))
        cmb_cruz_prov = ctk.CTkComboBox(f_cruzada, values=cargar_proveedores() or ["(Sin proveedores)"],
                                        width=300)
        cmb_cruz_prov.pack(fill="x", padx=10, pady=(0, 6))
        cmb_cruz_prov.set("")

        ctk.CTkLabel(f_cruzada, text="N° Documento / Factura (opcional):", font=(FAMILIA, 11, "bold")).pack(
            anchor="w", padx=10)
        ent_cruz_nro = ctk.CTkEntry(f_cruzada)
        ent_cruz_nro.pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkLabel(f_cruzada, text="Pagado a (tercero):", font=(FAMILIA, 11, "bold")).pack(
            anchor="w", padx=10)
        ent_cruz_tercero = ctk.CTkEntry(f_cruzada)
        ent_cruz_tercero.pack(fill="x", padx=10, pady=(0, 6))

        lbl_cruz_soporte = ctk.CTkLabel(f_cruzada, text="📄 Factura de compra: sin cargar\n🧾 Soporte del pago: sin cargar",
                                        font=(FAMILIA, 11, "italic"), text_color="#7d3c98",
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
                      font=(FAMILIA, 11, "bold"), fg_color="#2980b9", hover_color="#1f618d",
                      command=cargar_documentos_cruzada).pack(fill="x")
        f_ver_cruz = ctk.CTkFrame(f_cruzada, fg_color="transparent")
        f_ver_cruz.pack(fill="x", padx=10, pady=(0, 10))
        ctk.CTkButton(f_ver_cruz, text="👁 Ver factura", width=110, height=26, font=(FAMILIA, 11),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: ver_documento_cruzada("factura")).pack(side="left")
        ctk.CTkButton(f_ver_cruz, text="👁 Ver comprobante", width=140, height=26, font=(FAMILIA, 11),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=lambda: ver_documento_cruzada("soporte")).pack(side="left", padx=6)
        ctk.CTkButton(f_ver_cruz, text="🔄 Intercambiar", width=120, height=26, font=(FAMILIA, 11),
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
            if seg_tipo.get().startswith("Ingreso") and principal != "Ingresos":
                principal = "Ingresos"      # se guarda como INGRESO
            sub = cmb_sub.get()
            if sub == "(Sin categoría)":
                sub = ""
            evento = ""
            if principal == "Gastos Operativos":
                pv = cmb_evento.get().strip()
                if pv and pv not in ("Ninguno", "(Sin eventos)"):
                    evento = pv
            chofer = ""
            if es_categoria_planilla(f"{principal} {sub}"):
                chv = cmb_chofer.get().strip()
                if chv and chv != "Ninguno":
                    chofer = chv
            fecha = ent_fecha.get().strip() or datetime.now().strftime("%d/%m/%Y")
            desc_manual = ent_desc.get().strip()
            numero_operacion = ent_operacion.get().strip()

            tipo = "ingreso" if (principal == "Ingresos"
                                 or seg_tipo.get().startswith("Ingreso")) else "egreso"
            monto_final = total if tipo == "ingreso" else -total

            partes = []
            if principal:
                partes.append(principal)
            if sub:
                partes.append(sub)
            if evento:
                partes.append(f"Evento {evento}")
            if chofer:
                partes.append(f"Chofer {chofer}")
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
                                    categoria=%s, subcategoria=%s, evento=%s, chofer=%s,
                                    comision=%s, interbancario=%s, numero_operacion=%s, soporte=%s
                                WHERE id=%s
                            """, (fecha, desc, monto_final, tipo, principal, sub, evento, chofer,
                                  com, bool(inter), numero_operacion,
                                  estado_soporte["ruta"], id_edicion))
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
                                 categoria, subcategoria, evento, chofer, comision, interbancario, es_cruzada,
                                 numero_operacion, soporte)
                                VALUES (%s, %s, %s, %s, %s, %s, 'manual', 0, 'pendiente',
                                        %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                            """, (banco.get("banco", ""), banco.get("cuenta", ""), fecha, desc,
                                  monto_final, tipo, principal, sub, evento, chofer, com,
                                  bool(inter), es_cruzada, numero_operacion,
                                  estado_soporte["ruta"]))
                            try:
                                id_movimiento = c.fetchone()[0]
                            except Exception:
                                id_movimiento = 0
                        conn.commit()
                except Exception as e:
                    if "permission denied" in str(e).lower():
                        usuario_bd = self._reparar_permisos_bd()
                        if usuario_bd:
                            messagebox.showerror("Sin permiso en la base de datos",
                                                 self._texto_error_permisos(usuario_bd),
                                                 parent=v)
                        else:
                            messagebox.showinfo(
                                "Permisos reparados",
                                "Se corrigieron los permisos de la base de datos.\n"
                                "Vuelva a pulsar Guardar.", parent=v)
                    else:
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
                proveedor_gasto = (desc_manual or (f"Chofer {chofer}" if chofer else "")
                                   or (f"Evento {evento}" if evento else "") or sub or principal)
                self.registrar_egreso_en_compras({
                    "fecha": fecha, "total": total, "descripcion": desc or desc_manual,
                    "categoria": categoria_gasto, "proveedor": proveedor_gasto,
                    "evento": evento, "banco": construir_etiqueta_banco(banco),
                    "numero_operacion": numero_operacion,
                    "soporte": (ruta_comprobante.get("ruta") or
                                 (estado_cruzada.get("soporte", "") if isinstance(estado_cruzada, dict) else "")),
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
                      width=160, height=36, font=(FAMILIA, 13, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=guardar).pack(pady=10)
        ctk.CTkButton(f_pie, text="✖ Cancelar", width=110, height=36, font=(FAMILIA, 12),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=v.destroy).pack(pady=(0, 10))

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
                try:
                    from politica_almacenamiento import advertir
                    advertir(parent)
                except Exception:
                    messagebox.showwarning("Almacenamiento bloqueado",
                                           "Este equipo no está autorizado a guardar archivos.",
                                           parent=parent)
                return False, ""
            marca = datetime.now().strftime('%Y%m%d%H%M%S')
            _fecha_doc = locals().get("fecha") or datetime.now().strftime("%d/%m/%Y")
            if origen_factura and os.path.isfile(origen_factura):
                try:
                    # 📁 Estructura: Compras/<Mes año>/<RUC - Proveedor>/Facturas/
                    from estructura_archivos import ruta_documento
                    prov_limpio = re.sub(r'[\\/*?:"<>|]', '-', proveedor).replace(" ", "_")[:40]
                    ext = os.path.splitext(origen_factura)[1] or ".pdf"
                    ruta_factura = ruta_documento("compra_factura",
                                                  f"Cruzada_{marca}_{prov_limpio}{ext}",
                                                  ruta_base=base, fecha=_fecha_doc,
                                                  nombre=proveedor)
                    if not ruta_factura:
                        raise RuntimeError("No se pudo preparar la carpeta de destino")
                    shutil.copy2(origen_factura, ruta_factura)
                except Exception as e:
                    messagebox.showerror("Error", f"Fallo al guardar la factura de compra:\n{e}",
                                         parent=parent)
                    return False, ""
            if origen_soporte and os.path.isfile(origen_soporte):
                try:
                    # 📁 Estructura: Compras/<Mes año>/<RUC - Proveedor>/Comprobantes de pago/
                    from estructura_archivos import ruta_documento
                    ext = os.path.splitext(origen_soporte)[1] or ".pdf"
                    ruta_soporte = ruta_documento("compra_comprobante",
                                                  f"Soporte_{marca}_{nro or 'sin_doc'}{ext}",
                                                  ruta_base=base, fecha=_fecha_doc,
                                                  nombre=proveedor)
                    if not ruta_soporte:
                        raise RuntimeError("No se pudo preparar la carpeta de destino")
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
                    """, ("FACTURA", nro, fecha, proveedor, descripcion, "",
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
        evento = str(datos.get("evento") or "").strip()
        banco_txt = str(datos.get("banco") or "").strip()
        num_operacion = str(datos.get("numero_operacion") or "").strip()
        soporte = str(datos.get("soporte") or "").strip()
        try:
            from app_paths import ruta_para_guardar
            soporte_bd = ruta_para_guardar(soporte) if soporte else ""
        except Exception:
            soporte_bd = soporte
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
                    if soporte_bd:      # 📎 comprobante nuevo (no borra el anterior)
                        sets.append("archivo_ruta=%s")
                        params.append(soporte_bd)
                    if impuesto_dato is not None:
                        sets.append("impuesto=%s")
                        params.append(impuesto_bd)
                    params.append(id_gasto)
                    c.execute("UPDATE facturas_recibidas SET " + ", ".join(sets) + " WHERE id=%s",
                              tuple(params))
                    c.execute("""
                        UPDATE pagos_comprobantes
                        SET monto_pagado=%s, fecha_pago=%s, cuenta_origen=%s, categoria_suministro=%s,
                            proveedor_nombre=%s, numero_operacion=%s,
                            archivo_ruta=COALESCE(NULLIF(%s, ''), archivo_ruta)
                        WHERE id_factura=%s
                    """, (total, fecha, banco_txt, categoria, proveedor, num_operacion,
                    soporte_bd, id_gasto))
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
        # Ventana compartida con el módulo de Compras: mismas categorías guardadas.
        # "al_guardar" se ejecuta al guardar y cerrar, para refrescar los combos.
        return abrir_gestion_categorias_gastos(self.parent_frame, al_guardar=al_guardar)

    def corregir_movimiento(self):
        filas = self._filas_seleccionadas()
        if len(filas) != 1:
            messagebox.showinfo("Corregir", "Seleccione exactamente un movimiento para corregir.",
                                parent=self.parent_frame)
            return
        f = filas[0]
        if f.get("tabla") in ("transferencias_bancarias", "tx_comision"):
            self._abrir_transferencia_desde_movimiento(f)
            return

        # Los pagos registrados desde este módulo ("➕ Registrar Pago / Compra
        # Cruzada") se editan en ESA MISMA ventana, con todos los datos cargados.
        if f.get("origen") == "manual" and f.get("id"):
            self.agregar_movimiento_manual(registro=f)
            return

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Corregir Movimiento")
        v.geometry("460x400")
        v.transient(self.parent_frame)
        traer_al_frente(v)
        v.grab_set()

        ctk.CTkLabel(v, text=f"✏️ Corregir: {f['descripcion'][:40]}", font=(FAMILIA, 13, "bold"),
                     text_color="#1f538d").pack(pady=(15, 5))
        frm = ctk.CTkFrame(v, fg_color="transparent")
        frm.pack(fill="x", padx=20)

        ctk.CTkLabel(frm, text="Fecha:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        f_fecha = ctk.CTkFrame(frm, fg_color="transparent")
        f_fecha.pack(fill="x", pady=(0, 8))
        ent_fecha = ctk.CTkEntry(f_fecha)
        ent_fecha.pack(side="left", fill="x", expand=True)
        ent_fecha.insert(0, f["fecha"])
        ctk.CTkButton(f_fecha, text="📅", width=42, font=(FAMILIA, 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        ctk.CTkLabel(frm, text="Descripción:", font=(FAMILIA, 11, "bold")).pack(anchor="w")
        ent_desc = ctk.CTkEntry(frm)
        ent_desc.pack(fill="x", pady=(0, 8)); ent_desc.insert(0, f["descripcion"])

        ctk.CTkLabel(frm, text="Monto (negativo = egreso):", font=(FAMILIA, 11, "bold")).pack(anchor="w")
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
            self._cache_asientos = None
            self._cache_saldos_extra = None
            try:
                self.generar_reporte()
            except Exception as error:
                print("[Conciliación] refresco tras corregir:", error)

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
                                # 🧹 SIN RASTROS: se borran también sus filas en los
                                #    diferenciales y en la auditoría del módulo, para que
                                #    no quede ninguna copia ni vuelva a aparecer.
                                _fecha_m = str(f.get("fecha") or "")
                                _monto_m = float(f.get("monto") or 0)
                                _cuenta_m = banco.get("cuenta", "") if banco else ""
                                try:
                                    c.execute("""DELETE FROM conciliacion_diferenciales
                                                 WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                                   AND COALESCE(cuenta,'') = %s
                                                   AND (fecha = %s OR fecha = %s)
                                                   AND ABS(COALESCE(monto,0) - %s) < 0.005""",
                                              (nombre_banco, _cuenta_m, _fecha_m,
                                               fecha_para_mostrar(_fecha_m), _monto_m))
                                    c.execute("""DELETE FROM conciliacion_auditoria
                                                 WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                                   AND COALESCE(cuenta,'') = %s
                                                   AND ABS(COALESCE(importe_banco,0) - %s) < 0.005
                                                   AND (fecha_banco = %s OR fecha_banco = %s)""",
                                              (nombre_banco, _cuenta_m, abs(_monto_m),
                                               _fecha_m, fecha_para_mostrar(_fecha_m)))
                                except Exception as error_rastros:
                                    conn.rollback()
                                    print("[Conciliación] limpiar rastros:", error_rastros)
                                # 🚫 Si este asiento estaba cruzado con una línea del banco,
                                #    se descarta TAMBIÉN esa línea. Si no, al recalcular
                                #    volvía a salir como faltante y se volvía a guardar el
                                #    diferencial: parecía que el borrado «no funcionaba».
                                # 🚫 Se descarta la línea del banco equivalente (misma
                                #    fecha e importe), esté cruzada o no. Si no, al
                                #    recalcular volvía a salir como faltante y parecía
                                #    que el borrado «se restauraba».
                                try:
                                    _cuenta_l = banco.get("cuenta", "") if banco else ""
                                    c.execute("""SELECT fecha, monto, descripcion
                                                 FROM conciliacion_movimientos_pdf
                                                 WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                                   AND COALESCE(cuenta,'') = %s
                                                   AND (fecha = %s OR fecha = %s)
                                                   AND ABS(COALESCE(monto,0) - %s) < 0.01""",
                                              (nombre_banco, _cuenta_l, _fecha_m,
                                               fecha_para_mostrar(_fecha_m), _monto_m))
                                    for _lf, _lm, _ld in c.fetchall():
                                        self._registrar_ignorado(c, banco, {
                                            "origen": "estado_cuenta", "fecha": _lf,
                                            "monto": _lm, "descripcion": _ld or ""})
                                except Exception as error_linea:
                                    print("[Conciliación] descartar línea del banco:", error_linea)
                            elif origen == "sistema" and idp:
                                if f.get("tabla") in ("transferencias_bancarias", "tx_comision"):
                                    c.execute("""DELETE FROM conciliacion_bancaria
                                        WHERE origen = ANY(%s) AND id_movimiento=%s AND banco=%s
                                        AND cuenta=%s AND SIGN(monto)=SIGN(%s)""",
                                        (["s:" + f["tabla"], "sistema:" + f["tabla"]], idp,
                                         nombre_banco, banco.get("cuenta", ""), f.get("monto", 0)))
                                else:
                                    # 🛡️ Se guarda como 's:<tabla>' (o 'sistema:<tabla>' en
                                    #    datos viejos): antes se borraba con origen='sistema'
                                    #    y no borraba NADA, dejando la fila conciliada.
                                    c.execute("""DELETE FROM conciliacion_bancaria
                                                 WHERE origen = ANY(%s) AND id_movimiento = %s
                                                   AND banco = %s""",
                                              (["s:" + str(f.get("tabla") or ""),
                                                "sistema:" + str(f.get("tabla") or "")],
                                               idp, nombre_banco))
                                borrados_db += max(c.rowcount, 0)
                                # 🚫 Queda descartado: no vuelve a aparecer al recargar la lista
                                self._registrar_ignorado(c, banco, f)
                                ignorados_nuevos += 1
                            elif str(origen or "") == "e" or str(origen or "").startswith("s:"):
                                # 📄 Asientos que NO vienen de Compras: los registrados
                                #    desde el banco ('e'), las facturas App Grifo y las
                                #    comisiones ('s:*'). Antes no tenían rama y el botón
                                #    Eliminar NO hacía nada con ellos.
                                if idp:
                                    c.execute("DELETE FROM conciliacion_bancaria WHERE id = %s", (idp,))
                                    borrados_db += max(c.rowcount, 0)
                                    try:
                                        _cuenta_x = banco.get("cuenta", "") if banco else ""
                                        _fecha_x = str(f.get("fecha") or "")
                                        c.execute("""SELECT fecha, monto, descripcion
                                                     FROM conciliacion_movimientos_pdf
                                                     WHERE UPPER(COALESCE(banco,'')) = UPPER(%s)
                                                       AND COALESCE(cuenta,'') = %s
                                                       AND (fecha = %s OR fecha = %s)
                                                       AND ABS(COALESCE(monto,0) - %s) < 0.01""",
                                                  (nombre_banco, _cuenta_x, _fecha_x,
                                                   fecha_para_mostrar(_fecha_x),
                                                   float(f.get("monto") or 0)))
                                        for _lf, _lm, _ld in c.fetchall():
                                            self._registrar_ignorado(c, banco, {
                                                "origen": "estado_cuenta", "fecha": _lf,
                                                "monto": _lm, "descripcion": _ld or ""})
                                    except Exception as error_lin:
                                        print("[Conciliación] descartar línea (extra):", error_lin)
                                    self._registrar_ignorado(c, banco, f)
                                    ignorados_nuevos += 1
                            elif origen == "estado_cuenta":
                                # 🛡️ Se guarda como origen 'e' y la fecha en dd/mm/aaaa
                                #    (la fila llega en ISO): antes se comparaba
                                #    'estado_cuenta' + fecha ISO y nunca borraba nada.
                                c.execute("""DELETE FROM conciliacion_bancaria
                                             WHERE origen = ANY(%s) AND banco = %s
                                               AND fecha = ANY(%s)
                                               AND ABS(COALESCE(monto,0) - %s) < 0.005
                                               AND COALESCE(descripcion,'') = %s""",
                                          (["e", "estado_cuenta"], nombre_banco,
                                           [str(f.get("fecha", "")),
                                            fecha_para_mostrar(f.get("fecha", ""))],
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

        # 📄 También se quita de los archivos de respaldo JSON (respaldo_*.json):
        #    si se eliminó, no debe quedar ni en la base ni en el respaldo.
        for f_borrada in filas:
            self._quitar_de_respaldos(nombre_banco, f_borrada.get("fecha"),
                                      f_borrada.get("monto"))

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
        # 🔄 La base ya quedó actualizada (commit). Ahora se vuelve a calcular el
        #    cruce para que el aviso, la diferencia y la tabla queden al día sin
        #    tener que pulsar otro botón.
        self._cache_asientos = None
        self._cache_saldos_extra = None
        try:
            self.generar_reporte()
        except Exception as error:
            print("[Conciliación] refresco tras eliminar:", error)

        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Eliminó {len(filas)} movimiento(s) de la conciliación "
                            f"en {construir_etiqueta_banco(banco) if banco else 'banco'}"
                            + (f" (y {gastos_borrados} gasto(s) de Compras)" if gastos_borrados else ""))

        # 💰 El saldo debe reflejar lo eliminado ya mismo (sin pulsar «Actualizar»)
        try:
            self._datos_saldos = None
            self.actualizar_saldos_boton()
        except Exception as error_saldos:
            print("[Conciliación] refresco de saldos:", error_saldos)

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
                    monto_export = f.get("monto_mostrar", f["monto"])
                    out.write(sep.join([estado, f["fecha"], f["descripcion"],
                                        tipo, f"{float(monto_export):.2f}", f.get("origen", "")]) + "\n")
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
        traer_al_frente(v)
        txt = tk.Text(v, wrap="word",
                      font=("Menlo", 10) if sys.platform == "darwin" else ("Consolas", 10))
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", self.texto_pdf)
        txt.configure(state="disabled")
        ctk.CTkButton(v, text="Cerrar", width=120, fg_color="#34495e",
                      command=v.destroy).pack(pady=(0, 10))
