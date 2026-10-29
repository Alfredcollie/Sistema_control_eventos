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
import transferencias_soporte as tx_soporte

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


# =========================================================
# 📄 COMPROBANTES: facturas que cancela un mismo pago
# =========================================================
def facturas_que_cancela_comprobante(archivo_ruta="", numero_operacion=""):
    """Facturas (número y proveedor) canceladas por el MISMO comprobante u operación."""
    filas = []
    try:
        conn = conectar_db(silencioso=True)
        if not conn:
            return filas
        try:
            with conn.cursor() as cur:
                # 🌍 La base puede guardar la ruta RELATIVA y la app maneja la ABSOLUTA:
                #    se comparan ambas formas y, como respaldo, el nombre del archivo.
                ruta_txt = str(archivo_ruta or "").replace("\\", "/").strip()
                nombre = ruta_txt.split("/")[-1] if ruta_txt else ""
                relativa = ruta_txt
                try:
                    from app_paths import partir_ruta_guardada
                    sub, nom = partir_ruta_guardada(ruta_txt)
                    if nom:
                        relativa = (sub + "/" + nom) if sub else nom
                except Exception:
                    pass
                cur.execute("""
                    SELECT DISTINCT f.numero_documento, COALESCE(f.proveedor, ''),
                                    COALESCE(pc.monto_pagado, 0), COALESCE(pc.fecha_pago, '')
                    FROM pagos_comprobantes pc
                    JOIN facturas_recibidas f ON f.id = pc.id_factura
                    WHERE (%s <> '' AND (
                              COALESCE(pc.archivo_ruta, '') = %s
                           OR COALESCE(pc.archivo_ruta, '') = %s
                           OR COALESCE(pc.archivo_ruta, '') LIKE %s))
                       OR (%s <> '' AND COALESCE(pc.numero_operacion, '') = %s)
                    ORDER BY f.numero_documento
                """, (ruta_txt, ruta_txt, relativa, ("%" + nombre) if nombre else "\x00",
                      str(numero_operacion or ""), str(numero_operacion or "")))
                filas = cur.fetchall()
        finally:
            liberar_conexion(conn)
    except Exception as e:
        print("Aviso - facturas del comprobante:", e)
    return filas


def texto_facturas_canceladas(filas, formatear=None):
    """Texto listo para mostrar con las facturas que cancela un comprobante."""
    if not filas:
        return ""
    lineas = []
    for numero, proveedor, monto, fecha in filas:
        try:
            monto_txt = formatear(monto) if formatear else ("%.2f" % float(monto or 0))
        except Exception:
            monto_txt = str(monto)
        lineas.append("• %s  %s  %s  (%s)" % (numero, str(proveedor)[:28], monto_txt, fecha))
    return "\n".join(lineas)


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
    """Ruta tal como queda guardada en la base de datos.

    Se guarda RELATIVA a la carpeta sincronizada (ej. 'facturas_emitidas/x.pdf')
    para que la misma fila sirva en Windows y en Mac.
    """
    if not ruta:
        return ""
    try:
        from app_paths import ruta_para_guardar as _portable
        return _portable(ruta) or os.path.normpath(str(ruta))
    except Exception:
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
        from app_paths import resolver_ruta_archivo as _portable
        encontrada = _portable(ruta)
        if encontrada:
            return encontrada
    except Exception:
        pass
    try:
        nombre = os.path.basename(ruta.replace("\\", "/"))
        if not nombre:
            return None
        for base in (obtener_ruta_base(), str(CONFIG_DIR)):
            if not base:
                continue
            for sub in ("comprobantes_transferencias", "facturas_recibidas", "soportes_pagos_terceros", ""):
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
                c.execute("SELECT COUNT(*) FROM transferencias_bancarias WHERE archivo_ruta = %s", (ruta,))
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
CLAVE_ESQUEMA_BANCO = "banco_esquema_ok_v5"
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
def pdf_esta_cifrado(ruta):
    """True si el PDF exige contraseña para abrirse."""
    if _PypdfReader is not None:
        try:
            return bool(getattr(_PypdfReader(ruta), "is_encrypted", False))
        except Exception:
            pass
    if fitz is not None:
        try:
            doc = fitz.open(ruta)
            try:
                return bool(doc.needs_pass)
            finally:
                doc.close()
        except Exception:
            pass
    return False


def extraer_texto_pdf(ruta, password=None):
    texto = ""
    if pdfplumber is not None:
        try:
            with pdfplumber.open(ruta, password=password or "") as pdf:
                partes = [p.extract_text() or "" for p in pdf.pages]
            texto = "\n".join(partes).strip()
        except Exception:
            texto = ""
    if not texto and fitz is not None:
        try:
            doc = fitz.open(ruta)
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
            reader = _PypdfReader(ruta)
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


def detectar_saldo_final(texto):
    """Intenta detectar el saldo final del estado de cuenta.

    Primero busca un «Saldo Final» explícito; si no, toma el balance de la
    última línea de movimiento.
    """
    if not texto:
        return None
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
                     font=("Arial", 13, "bold"), text_color="#1f538d").pack(pady=(20, 4))
        ctk.CTkLabel(prog, text="Coloque el documento en el escáner (cristal).\nEspere unos segundos…",
                     font=("Arial", 10), text_color="gray", justify="center").pack()
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

        # 🖱️ Al pulsar una pestaña (sin romper el cambio de pestaña del Tabview)
        try:
            self.tabview._segmented_button.bind(
                "<ButtonRelease-1>", lambda e: self.frame_main.after(150, self._cargar_si_conciliacion), add="+")
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
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS comision NUMERIC DEFAULT 0",
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS referencia VARCHAR(120) DEFAULT ''",
                    "ALTER TABLE transferencias_bancarias ADD COLUMN IF NOT EXISTS archivo_ruta TEXT DEFAULT ''",
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
                    "ALTER TABLE conciliacion_bancaria ADD COLUMN IF NOT EXISTS tabla VARCHAR(60) DEFAULT ''",
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
        if estado.get("error"):
            self._mostrar_cargando("No se pudieron cargar los saldos. Revise conexión y esquema de Eventos.")
            messagebox.showerror("Banco", estado["error"], parent=self.parent_frame)
            return
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

        def _terminar(estado):
            if estado.get("error"):
                self._mostrar_cargando("No se pudieron cargar los saldos; revise la conexión y el esquema.")
                return
            self.refrescar_saldos(estado.get("datos"))

        ejecutar_en_hilo(self.parent_frame, _trabajo, al_terminar=_terminar)

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

        # ⚠️ Movimientos SIN cuenta bancaria asignada: no se suman a ninguna
        # cuenta, por eso el saldo puede verse "sin ingresos ni egresos".
        try:
            sin_cuenta = self._contar_movimientos_sin_cuenta(datos)
        except Exception:
            sin_cuenta = 0
        if sin_cuenta:
            f_sc = ctk.CTkFrame(self.scroll_saldos, corner_radius=10, fg_color="#fff3cd",
                                border_width=1, border_color="#ffeeba")
            f_sc.pack(fill="x", pady=(10, 5), padx=5)
            ctk.CTkLabel(
                f_sc,
                text=(f"⚠️ Hay {sin_cuenta} movimiento(s) SIN cuenta bancaria asignada"
                      + (f" en {mes_txt}" if mes_txt else "") +
                      ".\nNo se suman al saldo de ninguna cuenta (por eso puede ver 0.00 en "
                      "ingresos/egresos).\nAsígneles su cuenta con el botón de abajo "
                      "o desde la pestaña Conciliación Bancaria."),
                font=("Arial", 12, "bold"), text_color="#856404", justify="left",
                wraplength=900).pack(anchor="w", padx=15, pady=(12, 4))
            ctk.CTkButton(f_sc, text="🛠️ Asignar cuentas a estos movimientos",
                          font=("Arial", 12, "bold"), fg_color="#e67e22", hover_color="#b9651a",
                          command=self._ir_a_conciliacion_sin_cuenta,
                          width=320).pack(anchor="w", padx=15, pady=(0, 12))

    def _contar_movimientos_sin_cuenta(self, datos):
        """Cuenta los cobros/pagos del periodo visible que no tienen cuenta asignada."""
        mes = getattr(self, "mes_saldos", None)
        total = 0
        for clave, idx_cuenta in (("cobros", 4), ("pagos", 4)):
            for fila in (datos.get(clave) or []):
                try:
                    cuenta = str(fila[idx_cuenta] or "").strip()
                    if cuenta:
                        continue
                    # 🔁 Los pagos ESPEJO (los crea este mismo módulo y se ven como
                    #    MANUAL en la conciliación) no cuentan como "sin cuenta".
                    if clave == "pagos" and len(fila) > 8 and bool(fila[8]):
                        continue
                    fecha = str(fila[1] or "")
                    if mes and not clave_fecha(fecha).startswith(mes):
                        continue
                    total += 1
                except Exception:
                    continue
        return total

    def _ir_a_conciliacion_sin_cuenta(self):
        """Lleva a Conciliación, carga los movimientos y asigna la cuenta que falta."""
        try:
            self.tabview.set(" 🧾 Conciliación Bancaria ")
        except Exception:
            pass

        def _asignar(_estado=None):
            try:
                self.asignar_cuenta_a_movimientos()
            except Exception as e:
                messagebox.showinfo("Conciliación",
                                    "Use el botón «Asignar cuenta» de la pestaña "
                                    "Conciliación Bancaria para dar cuenta a los movimientos "
                                    f"sin asignar.\n\nDetalle: {e}")

        # Primero se cargan los movimientos del sistema (el botón de asignar
        # trabaja sobre esa lista).
        try:
            self.generar_reporte()
            self.parent_frame.after(2500, _asignar)
        except Exception:
            _asignar()

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
            tx_soporte.asegurar_esquema(conn)
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
                                ) AS espejo_banco,
                               COALESCE(p.archivo_ruta, ''),
                               COALESCE(p.numero_operacion, '')
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
                               fecha, descripcion, monto, COALESCE(comision, 0),
                               COALESCE(referencia, ''), COALESCE(archivo_ruta, '')
                        FROM transferencias_bancarias
                    """)
                    datos["transferencias"] = c.fetchall()
                except Exception:
                    conn.rollback()
                    raise
        except Exception as exc:
            conn.rollback()
            raise RuntimeError("No se pudieron verificar/cargar las transferencias; no se mostrará un saldo incompleto.") from exc
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
        for fila_c in (datos.get("cobros") or []):
            idp, fecha, nombre, monto, cuenta, idf, num_doc, ref = fila_c[:8]
            arch = fila_c[8] if len(fila_c) > 8 else ""
            oper_c = str(fila_c[9] or "").strip() if len(fila_c) > 9 else ""
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
                    "archivo_ruta": arch_p or "", "id_factura": idf or 0,
                    "operacion": oper_p,
                })
        for fila_tx in (datos.get("transferencias") or []):
            idt, bo, co, bd, cd, fecha, desc, monto = fila_tx[:8]
            comision, referencia, archivo = (tuple(fila_tx[8:11]) + (0, "", ""))[:3]
            monto_v = float(monto or 0)
            et_origen = construir_etiqueta_banco({"banco": bo, "cuenta": co})
            et_destino = construir_etiqueta_banco({"banco": bd, "cuenta": cd})
            if (_norm_texto(banco.get("banco")) == _norm_texto(bo)
                    and _norm_texto(banco.get("cuenta")) == _norm_texto(co)):
                desc_eg = f"Transferencia a {bd or 'otra cuenta'}"
                if desc:
                    desc_eg += f" ({desc})"
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_eg,
                    "monto": -monto_v, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias",
                    "documento": referencia or "", "archivo_ruta": archivo or "",
                })
                if float(comision or 0) > 0:
                    movs.append({
                        "id": idt, "fecha": fecha or "",
                        "descripcion": f"Comisión transferencia a {bd or 'otra cuenta'}",
                        "monto": -float(comision), "tipo": "egreso",
                        "origen": "sistema", "tabla": tx_soporte.TABLA_COMISION,
                        "documento": referencia or "", "archivo_ruta": archivo or "",
                    })
            if (_norm_texto(banco.get("banco")) == _norm_texto(bd)
                    and _norm_texto(banco.get("cuenta")) == _norm_texto(cd)):
                desc_in = f"Transferencia de {bo or 'otra cuenta'}"
                if desc:
                    desc_in += f" ({desc})"
                movs.append({
                    "id": idt, "fecha": fecha or "", "descripcion": desc_in,
                    "monto": monto_v, "tipo": "transferencia",
                    "origen": "sistema", "tabla": "transferencias_bancarias",
                    "documento": referencia or "", "archivo_ruta": archivo or "",
                })
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
            es_tx = _tabla in tx_soporte.TABLAS_TX
            if es_tx:
                _tabla = "transferencias_bancarias"
            _id = int(m.get("id") or 0)
            if _tabla not in ("pagos_clientes", "pagos_comprobantes", "transferencias_bancarias") or not _id:
                return messagebox.showwarning("Eliminar",
                                              "Este movimiento no se puede eliminar desde aquí.", parent=v)
            _desc = str(m.get("descripcion") or "")
            _monto = formatear_monto(m.get("monto", 0))
            if not messagebox.askyesno(
                    "Eliminar movimiento",
                    "¿Eliminar este movimiento del sistema?" + chr(10) + chr(10)
                    + _desc + "   (" + _monto + ")" + chr(10) + chr(10)
                    + ("Se eliminará la transferencia COMPLETA (origen, destino y comisión)." if es_tx
                       else "Se borrará de la base de datos (" + _tabla + ")."),
                    parent=v):
                return
            conn_del = conectar_db()
            if not conn_del:
                return
            try:
                c_del = conn_del.cursor()
                if es_tx:
                    tx_soporte.limpiar_marcas(c_del, [_id])
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
            if es_tx:
                if self.id_tx_edicion == _id:
                    self._preparar_form_transferencia()
                self.refrescar_transferencias()
            self.actualizar_saldos_boton()
            self.generar_reporte()

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
        ctk.CTkButton(r3, text="📅", width=40,
                      command=lambda: CalendarioNativo(self.parent_frame, self.ent_tx_fecha)).pack(side="left")
        ctk.CTkLabel(r3, text="Monto:", width=90, anchor="w", font=("Arial", 12, "bold")).pack(side="left", padx=(16, 0))
        self.ent_tx_monto = ctk.CTkEntry(r3, width=170)
        self.ent_tx_monto.pack(side="left", padx=6)

        r4 = ctk.CTkFrame(f_form, fg_color="transparent"); r4.pack(fill="x", padx=12, pady=(4, 4))
        ctk.CTkLabel(r4, text="Descripción:", width=130, anchor="w", font=("Arial", 12, "bold")).pack(side="left")
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
        ctk.CTkButton(r_comp, text="Quitar adjunto", width=120,
                      command=self._quitar_comprobante_tx).pack(side="left", padx=4)
        self.lbl_tx_archivo = ctk.CTkLabel(r_comp, text="Sin comprobante", anchor="w")
        self.lbl_tx_archivo.pack(side="left", padx=8)

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
        columnas = ("fecha", "origen", "destino", "descripcion", "monto", "comision", "referencia")
        self.tabla_tx = ttk.Treeview(f_tabla, columns=columnas, show="headings", selectmode="extended")
        for col, txt, w, anc in (("fecha", "Fecha", 100, "center"),
                                 ("origen", "Banco Origen", 200, "center"),
                                 ("destino", "Banco Destino", 200, "center"),
                                 ("descripcion", "Descripción", 260, "w"),
                                 ("monto", "Monto", 130, "e"),
                                 ("comision", "Comisión", 100, "e"),
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
        ctk.CTkLabel(ventana, text="COMPROBANTE ESCANEADO - REVISE ANTES DE USAR",
                     font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(10, 2))
        datos = resultado.get("datos") or {}
        monto_txt = ("%.2f" % float(datos.get("monto") or 0)) if datos.get("monto") else "sin dato"
        ctk.CTkLabel(ventana, text="Fecha: " + str(datos.get("fecha") or "sin dato")
                     + "   ·   Monto: " + monto_txt
                     + "   ·   Referencia: " + str(datos.get("referencia") or "sin dato"),
                     font=("Arial", 11, "bold"), text_color="#2c3e50").pack(pady=(0, 6))
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
                ctk.CTkLabel(f_izq, text="(sin vista previa)", font=("Arial", 10)).pack(padx=8, pady=8)
        f_der = ctk.CTkFrame(cuerpo, fg_color="transparent")
        f_der.pack(side="left", fill="both", expand=True)
        ctk.CTkLabel(f_der, text="Texto leído (OCR):", font=("Arial", 11, "bold")).pack(anchor="w")
        caja = ctk.CTkTextbox(f_der, width=420, height=430)
        caja.pack(fill="both", expand=True)
        caja.insert("1.0", resultado.get("texto") or "(sin texto legible)")
        if resultado.get("aviso"):
            ctk.CTkLabel(ventana, text="Aviso: " + str(resultado["aviso"]), font=("Arial", 10),
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
        self.lbl_tx_archivo.configure(text="Escaneando / leyendo OCR...")
        progreso = None
        if escanear:
            try:
                progreso = ctk.CTkToplevel(self.parent_frame)
                progreso.title("Escaneando comprobante de transferencia")
                progreso.geometry("470x175")
                progreso.transient(self.parent_frame)
                try:
                    progreso.grab_set()
                except Exception:
                    pass
                ctk.CTkLabel(progreso, text="Escaneando y leyendo el comprobante...",
                             font=("Arial", 13, "bold"), text_color="#1f538d").pack(pady=(18, 4))
                ctk.CTkLabel(progreso, text="Coloque el comprobante en el cristal del escáner."
                                            + chr(10) + "Escanea a 300 ppp; si no se lee bien, repite a 600 ppp.",
                             font=("Arial", 10), text_color="gray", justify="center").pack()
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
        if validar_fecha_registro is not None:
            permitido, aviso = validar_fecha_registro(fecha)
            if not permitido:
                messagebox.showwarning("Fecha fuera del corte del sistema", aviso, parent=self.parent_frame)
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
                                         f"• Egreso en {origen}: {formatear_monto(monto)}\n"
                                         f"• Comisión separada: {formatear_monto(comision)}\n"
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
                                         formatear_monto(t.get("comision", 0)), t.get("referencia", "")))

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
        f_sel = ctk.CTkFrame(self.tab_conciliacion, fg_color="#f8f9fa",
                             border_width=1, border_color="#e0e0e0", corner_radius=8)
        f_sel.pack(fill="x", padx=5, pady=(5, 10))

        ctk.CTkLabel(f_sel, text="Banco a conciliar:", font=("Arial", 12, "bold")).pack(side="left", padx=(12, 6), pady=10)
        etiquetas = [construir_etiqueta_banco(b) for b in self.bancos]
        if not etiquetas:
            etiquetas = ["(Sin bancos configurados)"]
        self.cmb_banco = ctk.CTkComboBox(f_sel, values=etiquetas, width=320, state="readonly",
                                          command=lambda _v=None: self.generar_reporte())
        self.cmb_banco.pack(side="left", padx=6, pady=10)
        if etiquetas:
            self.cmb_banco.set(etiquetas[0])

        ctk.CTkLabel(f_sel, text="Periodo a conciliar:", font=("Arial", 12, "bold")).pack(side="left", padx=(20, 6), pady=10)
        # 🗓️ Primera opción por defecto: todo lo registrado desde la Fecha de Comienzo
        # del Sistema (Configuración General) hasta hoy.
        valores_mes = [ETIQUETA_DESDE_COMIENZO] + construir_valores_mes()
        self.cmb_mes = ctk.CTkComboBox(f_sel, values=valores_mes, width=210, state="readonly",
                                         command=lambda _v=None: self.generar_reporte())
        self.cmb_mes.pack(side="left", padx=6, pady=10)
        self.cmb_mes.set(ETIQUETA_DESDE_COMIENZO)

        f_btns = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns.pack(fill="x", padx=5, pady=(0, 2))
        ctk.CTkButton(f_btns, text="📄 Subir PDF Estado de Cuenta", font=("Arial", 12, "bold"),
                      fg_color="#e67e22", hover_color="#ca6f1e",
                      command=self.subir_pdf).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="🔄 Cargar Movimientos del Sistema", font=("Arial", 12, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.generar_reporte).pack(side="left", padx=4)
        ctk.CTkButton(f_btns, text="✅ Conciliar Seleccionados", font=("Arial", 12, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.conciliar_seleccionados).pack(side="left", padx=4)

        f_btns2 = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_btns2.pack(fill="x", padx=5, pady=(0, 8))
        ctk.CTkButton(f_btns2, text="🏦 Asignar cuenta",
                      font=("Arial", 12, "bold"), fg_color="#16a085", hover_color="#117a65",
                      command=self.asignar_cuenta_a_movimientos).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="➕ Registrar Pago / Compra Cruzada", font=("Arial", 12, "bold"),
                      fg_color="#8e44ad", hover_color="#703688",
                      command=self.agregar_movimiento_manual).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="🧾 Asentar ITF", font=("Arial", 12, "bold"),
                      fg_color="#b7950b", hover_color="#9a7d0a",
                      command=self.asentar_itf).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="✏️ Editar / Corregir", font=("Arial", 12, "bold"),
                      fg_color="#34495e", hover_color="#2c3e50",
                      command=self.corregir_movimiento).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="🗑️ Eliminar", font=("Arial", 12, "bold"),
                      fg_color="#c0392b", hover_color="#96281b",
                      command=self.eliminar_movimiento_conciliacion).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="♻️ Restaurar Eliminados", font=("Arial", 12, "bold"),
                      fg_color="#16a085", hover_color="#117a65",
                      command=self.restaurar_ignorados).pack(side="left", padx=4)
        ctk.CTkButton(f_btns2, text="💾 Exportar", font=("Arial", 12, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.exportar_reporte).pack(side="left", padx=4)

        self.f_resumen = ctk.CTkFrame(self.tab_conciliacion, fg_color="#eaf2f8",
                                      border_width=1, border_color="#d4e6f1", corner_radius=8)
        self.f_resumen.pack(fill="x", padx=5, pady=(0, 8))
        self.lbl_resumen = ctk.CTkLabel(self.f_resumen, text="Seleccione un banco, suba el PDF del estado de cuenta "
                                                             "y cargue los movimientos del sistema.",
                                        font=("Arial", 12), text_color="#1f538d", justify="left")
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
        self.lbl_faltantes = ctk.CTkLabel(self.f_faltantes, text="", font=("Arial", 12, "bold"),
                                          text_color="#a04000", justify="left")
        self.lbl_faltantes.pack(side="left", padx=12, pady=8)
        self.btn_faltantes = ctk.CTkButton(
            self.f_faltantes, text="➕ Agregar faltantes al sistema", width=230,
            font=("Arial", 11, "bold"), fg_color="#e67e22", hover_color="#ca6f1e",
            command=self.agregar_faltantes_al_sistema)
        self.btn_faltantes.pack(side="right", padx=12, pady=8)

        f_buscar = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_buscar.pack(fill="x", padx=5, pady=(0, 4))
        ctk.CTkLabel(f_buscar, text="🔍 Buscar:", font=("Arial", 12, "bold")).pack(side="left", padx=(4, 6))
        self.ent_buscar_conc = ctk.CTkEntry(
            f_buscar, placeholder_text="cualquier columna: fecha, monto, descripción, tipo…", width=460)
        self.ent_buscar_conc.pack(side="left")
        ctk.CTkButton(f_buscar, text="✖ Limpiar", width=90, font=("Arial", 11),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.limpiar_busqueda_conc).pack(side="left", padx=8)
        self.ent_buscar_conc.bind("<KeyRelease>", lambda _e: self.filtrar_conciliacion_inline())

        f_tabla = ctk.CTkFrame(self.tab_conciliacion, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=5, pady=(0, 5))
        columnas = ("estado", "fecha", "documento", "descripcion", "tipo", "monto", "origen", "saldo")
        self.tabla_conc = ttk.Treeview(f_tabla, columns=columnas, show="headings",
                                       selectmode="extended")
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
        self.tabla_conc.bind("<Delete>", lambda _e: self.eliminar_movimiento_conciliacion())
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

    def _guardar_movimientos_pdf(self, banco, movs):
        """Guarda las líneas del estado de cuenta (PDF) para no perderlas al salir."""
        if not banco or not movs:
            return
        conn = conectar_db(silencioso=True)
        if not conn:
            return
        try:
            with conn.cursor() as c:
                c.execute("DELETE FROM conciliacion_movimientos_pdf WHERE banco = %s",
                          (banco.get("banco", ""),))
                for m in movs:
                    c.execute("""INSERT INTO conciliacion_movimientos_pdf
                        (banco, fecha, descripcion, monto, saldo, creado)
                        VALUES (%s,%s,%s,%s,%s,%s)""",
                        (banco.get("banco", ""), str(m.get("fecha") or ""),
                         str(m.get("descripcion") or ""), float(m.get("monto") or 0),
                         m.get("saldo"), datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()
        except Exception as e:
            print("[Conciliación] No se pudo guardar el estado de cuenta:", e)
        finally:
            liberar_conexion(conn)

    def _cargar_movimientos_pdf(self, banco):
        """Recupera las líneas del estado de cuenta guardadas para este banco."""
        if not banco:
            return []
        conn = conectar_db(silencioso=True)
        if not conn:
            return []
        try:
            with conn.cursor() as c:
                c.execute("""SELECT fecha, descripcion, monto, saldo
                             FROM conciliacion_movimientos_pdf WHERE banco = %s ORDER BY id""",
                          (banco.get("banco", ""),))
                return [{"fecha": f or "", "descripcion": d or "",
                         "monto": float(m or 0),
                         "saldo": (float(s) if s is not None else None)}
                        for f, d, m, s in c.fetchall()]
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
                    self._guardar_movimientos_pdf(_banco_pdf, self.movimientos_pdf)
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

        # 🔁 Si no hay estado de cuenta en memoria (o cambió el banco), se recupera
        #    el último guardado para que la conciliación no se pierda al reentrar.
        _banco_actual = banco.get("banco", "")
        if (not getattr(self, "movimientos_pdf", None)
                or getattr(self, "_banco_pdf", None) != _banco_actual):
            try:
                self.movimientos_pdf = self._cargar_movimientos_pdf(banco)
                self._banco_pdf = _banco_actual
            except Exception:
                self.movimientos_pdf = []

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
        fecha_inicio = clave_fecha(banco.get("fecha", ""))
        if fecha_inicio:
            desde = max(desde or "", fecha_inicio)
        # 🚫 Movimientos que el usuario eliminó de esta conciliación: no se vuelven a cargar
        ignorados = self.cargar_ignorados(banco)
        ign_sistema = ignorados.get("sistema", set())
        ign_pdf = ignorados.get("estado_cuenta", set())

        movs_sistema = []
        movs_totales = []
        # ⚠️ Se incluyen también los cobros/pagos SIN cuenta bancaria asignada: si no,
        # no aparecerían en ninguna cuenta y la conciliación saldría vacía.
        for m in self.cargar_movimientos_sistema(banco, incluir_sin_cuenta=True):
            if not self._en_periodo(m.get("fecha"), mes_key, desde, hasta):
                continue
            # Ocultar una línea no altera el saldo del sistema.
            if not m.get("sin_cuenta"):
                movs_totales.append(m)
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
        bank_totales = []
        for b in self.movimientos_pdf:
            if not self._en_periodo(b.get("fecha"), mes_key, desde, hasta):
                continue
            bank_totales.append(b)
            clave = self._clave_ignorado("estado_cuenta", "", 0, b.get("fecha"),
                                         b.get("monto"), b.get("descripcion"))
            if clave not in ign_pdf:
                bank_movs.append(b)

        filas = []
        usados = set()

        def _a_date(s):
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except Exception:
                return None

        def _emparejar_todos(lista):
            """Empareja una lista con bank_movs en 3 pasadas.

            Pasada 1: misma fecha + mismo monto (exacto).
            Pasada 2: mismo monto con fecha cercana (hasta 7 días): el sistema
                      suele registrar el pago en otra fecha que el banco.
            Pasada 3: monto único en cualquier fecha.
            Se hace por pasadas para que un movimiento con fecha exacta no
            pierda su línea del banco a manos de otro con igual monto.
            """
            res = {}
            for m in lista:
                sf = normalizar_fecha(m["fecha"])
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if sf and normalizar_fecha(b["fecha"]) == sf and abs(b["monto"] - m["monto"]) <= 0.02:
                        usados.add(i); res[id(m)] = i; break
            for m in lista:
                if id(m) in res:
                    continue
                sd = _a_date(normalizar_fecha(m["fecha"]))
                cands = []
                for i, b in enumerate(bank_movs):
                    if i in usados:
                        continue
                    if abs(b["monto"] - m["monto"]) <= 0.02:
                        bd = _a_date(normalizar_fecha(b["fecha"]))
                        if sd and bd:
                            cands.append((abs((bd - sd).days), i))
                if cands:
                    cands.sort()
                    if cands[0][0] <= 7:
                        usados.add(cands[0][1]); res[id(m)] = cands[0][1]
            for m in lista:
                if id(m) in res:
                    continue
                cands = [i for i, b in enumerate(bank_movs)
                         if i not in usados and abs(b["monto"] - m["monto"]) <= 0.02]
                if len(cands) == 1:
                    usados.add(cands[0]); res[id(m)] = cands[0]
            return res

        def linea_estado_cuenta(b):
            return {**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                    "origen": "estado_cuenta", "tabla": "", "estado": "conciliado"}

        matches_sys = _emparejar_todos(sys_movs)
        for s in sys_movs:
            idx_match = matches_sys.get(id(s))
            if idx_match is not None:
                filas.append({**s, "estado": "conciliado"})
                filas.append(linea_estado_cuenta(bank_movs[idx_match]))
            else:
                filas.append({**s, "estado": "diferencia"})

        # Los movimientos MANUALES también se emparejan con el estado de cuenta: así la
        # línea del banco no queda como "sin conciliar" por ocultarse su espejo.
        matches_man = _emparejar_todos(manuales)
        for m in manuales:
            idx_match = matches_man.get(id(m))
            if idx_match is not None:
                filas.append({**m, "estado": "conciliado", "origen": "manual"})
                filas.append(linea_estado_cuenta(bank_movs[idx_match]))
            else:
                filas.append({**m, "estado": m.get("estado", "pendiente"), "origen": "manual"})

        for i, b in enumerate(bank_movs):
            if i not in usados:
                filas.append({**b, "id": 0, "tipo": "ingreso" if b["monto"] > 0 else "egreso",
                              "origen": "estado_cuenta", "tabla": "", "estado": "diferencia"})

        # 🧹 Si se eligió un mes, solo se muestran movimientos de ESE mes
        if mes_key:
            filas = [f for f in filas if normalizar_fecha(f.get("fecha", "")).startswith(mes_key)]
        filas.sort(key=lambda f: (normalizar_fecha(f["fecha"]), f["monto"]))
        return {"filas": filas, "movs_sistema": movs_totales, "bank_movs": bank_totales,
                "desde": desde or "", "hasta": hasta or ""}

    def _pintar_reporte(self, banco, etiqueta_periodo, datos):
        """Dibuja en pantalla la conciliación ya calculada (hilo principal)."""
        filas = datos.get("filas") or []
        movs_sistema = datos.get("movs_sistema") or []
        bank_movs = datos.get("bank_movs") or []
        desde = datos.get("desde") or ""
        hasta = datos.get("hasta") or ""

        # 🔁 Reaplicar los movimientos ya conciliados (persistidos en la base)
        conciliados = self.cargar_conciliados(banco)
        for f in filas:
            if self._clave_conciliado_fila(f) in conciliados:
                f["estado"] = "conciliado"
        self._filas_conc_full = list(filas)
        self.filas_conciliacion = filas
        self.refrescar_tree()
        # ⚠️ Aviso: movimientos del estado de cuenta que NO están en el sistema
        faltantes = [f for f in filas if f.get("origen") == "estado_cuenta"
                     and f.get("estado") != "conciliado"]
        self._faltantes_estado_cuenta = faltantes
        # ⚠️ Pagos del sistema que el banco NO cobró en este mes: casi siempre se
        #    cobran al mes siguiente (el banco los postea con retraso).
        pendientes_sis = [f for f in filas if f.get("origen") == "sistema"
                          and f.get("estado") != "conciliado"]
        self._actualizar_aviso_faltantes(faltantes, pendientes_sis)

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

    def _valores_conc(self, f):
        estado = f.get("estado", "pendiente")
        if estado == "conciliado":
            estado_txt = "✅ Conciliado"
        elif f.get("origen") == "estado_cuenta":
            estado_txt = "❌ Sin conciliar (Banco)"
        elif estado == "diferencia":
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
        return (estado_txt, fecha_para_mostrar(f["fecha"]), f.get("documento", ""), f["descripcion"],
                tipo_txt, formatear_monto(f["monto"]), origen_txt, saldo_txt)

    def _tag_conc(self, f):
        estado = f.get("estado", "pendiente")
        if estado == "conciliado":
            return "conciliado"
        if f.get("origen") == "estado_cuenta":
            return "diferencia"
        if estado == "diferencia":
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

    def _actualizar_aviso_faltantes(self, faltantes, pendientes_sistema=None):
        """Avisa de lo que falta conciliar (estado de cuenta y pagos del sistema)."""
        try:
            n = len(faltantes or [])
            m = len(pendientes_sistema or [])
            mensajes = []
            if n:
                mensajes.append("⚠️ %d movimiento(s) del estado de cuenta NO están en el sistema." % n)
            if m:
                mensajes.append("⚠️ %d pago(s) del sistema sin conciliar: puede que el banco "
                                "los haya cobrado en el mes siguiente." % m)
            if mensajes:
                self.lbl_faltantes.configure(text="   ".join(mensajes), text_color="#a04000")
            else:
                self.lbl_faltantes.configure(
                    text="✅ Todos los movimientos están conciliados.",
                    text_color="#1e8449")
            self.btn_faltantes.configure(state="normal" if n else "disabled")
        except Exception:
            pass

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
            filas = facturas_que_cancela_comprobante(ruta_comprobante or "", "")
        except Exception as e:
            return messagebox.showerror("Comprobante", "No se pudo consultar: %s" % e)
        if not filas:
            return messagebox.showinfo("Comprobante",
                                       "Este comprobante todavía no cancela facturas registradas.")
        try:
            from modulo_compras import formatear_moneda as _fm
        except Exception:
            _fm = None
        messagebox.showinfo(
            "Facturas que cancela este comprobante",
            "Cancela %d factura(s):\n\n%s" % (len(filas), texto_facturas_canceladas(filas, _fm)))

    def _ruta_existente(self, ruta):
        """Ruta real del archivo (resuelta para este equipo/SO) o '' si no existe."""
        ruta = str(ruta or "").strip()
        if not ruta:
            return ""
        try:
            from app_paths import resolver_ruta_archivo as resolver_compartida
            resuelta = resolver_compartida(ruta)
        except Exception:
            resuelta = ""
        for candidata in (resuelta, resolver_ruta_archivo(ruta), ruta, os.path.abspath(ruta)):
            try:
                if candidata and os.path.isfile(candidata):
                    return candidata
            except Exception:
                continue
        return ""

    def _menu_contextual_movimiento(self, event, tabla, movimientos, acciones=True):
        """Clic derecho: menú para ver la factura y el comprobante (Windows/Mac)."""
        try:
            iid = tabla.identify_row(event.y)
            if not iid:
                return "break"
            tabla.selection_set(iid)
            idx = int(iid) if str(iid).isdigit() else -1
            if idx < 0 or idx >= len(movimientos):
                return "break"
            f = movimientos[idx]
        except Exception:
            return "break"

        menu = tk.Menu(self.parent_frame, tearoff=0)
        ruta_factura = self._ruta_existente(self._archivo_factura_de(f.get("id_factura")))
        if ruta_factura:
            menu.add_command(label="Abrir factura", command=lambda r=ruta_factura: abrir_documento(r))
        else:
            menu.add_command(label="Abrir factura (sin archivo)", state="disabled")

        ruta_soporte = self._ruta_existente(f.get("archivo_ruta"))
        if ruta_soporte:
            menu.add_command(label="Abrir comprobante de pago", command=lambda r=ruta_soporte: abrir_documento(r))
            # 🧾 Si ese comprobante cancela VARIAS facturas, se muestran los números
            try:
                vinculadas = facturas_que_cancela_comprobante(ruta_soporte, "") or []
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
            f["estado"] = "conciliado"
            if self._persistir_conciliado(f, banco):
                guardados += 1
        self.refrescar_tree()
        registrar_auditoria(self.usuario_activo, "Banco",
                            f"Concilió {len(filas)} movimiento(s) en {construir_etiqueta_banco(banco) if banco else 'banco'}")
        messagebox.showinfo("Conciliación",
                            f"{len(filas)} movimiento(s) conciliado(s).\n"
                            f"💾 Guardados en la base de datos: {guardados} de {len(filas)}.",
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
                    if tabla in tx_soporte.TABLAS_TX and _norm_texto(cuenta) != _norm_texto(banco.get("cuenta")):
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
                    c.execute("UPDATE conciliacion_bancaria SET estado='conciliado' WHERE id=%s AND banco=%s",
                              (idm, banco_nombre))
                    conn.commit()
                    return True

                origen_persist = ("s:" + tabla) if origen == "sistema" and tabla else ("e" if origen == "estado_cuenta" else origen)
                if origen == "sistema" and idm and tabla in tx_soporte.TABLAS_TX:
                    c.execute("""SELECT COUNT(*) FROM conciliacion_bancaria
                                 WHERE banco=%s AND cuenta=%s AND origen=%s AND id_movimiento=%s
                                 AND SIGN(monto)=SIGN(%s)""",
                              (banco_nombre, banco.get("cuenta", ""), origen_persist, idm, monto))
                elif origen == "sistema" and idm:
                    c.execute("""SELECT COUNT(*) FROM conciliacion_bancaria
                                 WHERE banco=%s AND origen=%s AND id_movimiento=%s""",
                              (banco_nombre, origen_persist, idm))
                else:
                    c.execute("""SELECT COUNT(*) FROM conciliacion_bancaria
                                 WHERE banco=%s AND origen=%s AND COALESCE(fecha,'')=%s
                                   AND ABS(COALESCE(monto,0)-%s) < 0.005""",
                              (banco_nombre, origen_persist, fecha, monto))
                if c.fetchone()[0] == 0:
                    c.execute("""INSERT INTO conciliacion_bancaria
                        (banco, cuenta, fecha, descripcion, monto, tipo, origen, id_movimiento, estado, fecha_conciliacion)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'conciliado',%s)""",
                        (banco_nombre, banco.get("cuenta", ""), fecha, desc, monto,
                         f.get("tipo", "ingreso"), origen_persist, idm,
                         datetime.now().strftime("%d/%m/%Y")))
                    conn.commit()
                return True
        except Exception as e:
            print("[Conciliación] No se pudo guardar:", e)
            return False
        finally:
            liberar_conexion(conn)

    def escanear_comprobante_pago(self, padre, al_terminar):
        """🖨️ Escanea el comprobante de pago con la multifuncional y arma un PDF."""
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
            config = {}
            try:
                config = cargar_configuracion_regional() or {}
            except Exception as e_cfg:
                config = {}
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
                     font=("Arial", 12, "bold"), text_color="#0e7490").pack(pady=(20, 4))
        ctk.CTkLabel(prog, text="Coloque el comprobante en el escáner (cristal).",
                     font=("Arial", 10), text_color="gray").pack()
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
        """📎 Comprobante de pago de un movimiento del banco ('' si no hay)."""
        try:
            ruta = str(registro.get("archivo_ruta") or "").strip()
            if ruta:
                return ruta
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
            "personal": str(registro.get("personal") or ""),
            "numero_operacion": str(registro.get("numero_operacion") or ""),
            "comision": float(registro.get("comision") or 0),
            "interbancario": bool(registro.get("interbancario")),
            "es_cruzada": bool(registro.get("es_cruzada")),
            "comprobante": self._comprobante_de_movimiento(registro),
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

    def asentar_itf(self):
        """ITF manual: pide fecha, N° de transaccion y monto, y crea el asiento."""
        banco = self.banco_seleccionado()
        if not banco:
            return messagebox.showwarning("Banco", "Seleccione un banco.", parent=self.parent_frame)

        v = ctk.CTkToplevel(self.parent_frame)
        v.title("Asentar ITF")
        v.geometry("460x360")
        v.transient(self.parent_frame)
        try:
            v.grab_set()
        except Exception:
            pass

        ctk.CTkLabel(v, text="ITF - Impuesto a las Transacciones Financieras",
                     font=("Arial", 14, "bold"), text_color="#1f538d").pack(pady=(16, 2))
        ctk.CTkLabel(v, text="Banco: " + str(banco.get("banco", "")) + " - " + str(banco.get("cuenta", "")),
                     font=("Arial", 11)).pack(pady=(0, 10))

        f = ctk.CTkFrame(v, fg_color="transparent")
        f.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f, text="Fecha (dd/mm/aaaa):", width=150, anchor="w",
                     font=("Arial", 12, "bold")).pack(side="left")
        ent_fecha = ctk.CTkEntry(f, width=170)
        ent_fecha.pack(side="left")
        ent_fecha.insert(0, datetime.now().strftime("%d/%m/%Y"))
        ctk.CTkButton(f, text="📅", width=42, font=("Arial", 13, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=lambda: CalendarioNativo(v, ent_fecha)).pack(side="left", padx=(6, 0))

        f2 = ctk.CTkFrame(v, fg_color="transparent")
        f2.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f2, text="N° de transaccion:", width=150, anchor="w",
                     font=("Arial", 12, "bold")).pack(side="left")
        ent_num = ctk.CTkEntry(f2, width=200)
        ent_num.pack(side="left")

        f3 = ctk.CTkFrame(v, fg_color="transparent")
        f3.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(f3, text="Monto del ITF (S/):", width=150, anchor="w",
                     font=("Arial", 12, "bold")).pack(side="left")
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
                      font=("Arial", 12, "bold"), command=guardar).pack(side="left", padx=40)
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

        cats = cargar_categorias_gastos()
        principales = ordenar_categorias_principales(list(cats.keys())) or ["Producción de Eventos"]
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
            principales = ordenar_categorias_principales(list(cats.keys())) or ["Producción de Eventos"]
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
        # 📎 COMPROBANTE DE PAGO (opcional): cargar, escanear o ver el voucher
        ruta_comprobante = {"ruta": str(datos_ini.get("comprobante") or "")}
        ctk.CTkLabel(f, text="📎 Comprobante de pago (opcional):",
                     font=("Arial", 11, "bold")).pack(anchor="w", pady=(6, 0))
        f_comp = ctk.CTkFrame(f, fg_color="transparent")
        f_comp.pack(fill="x", pady=(0, 4))
        lbl_comprobante = ctk.CTkLabel(f, text="Sin comprobante cargado", font=("Arial", 10),
                                       text_color="#999999", anchor="w", justify="left")

        def actualizar_lbl_comprobante():
            ruta = ruta_comprobante.get("ruta") or ""
            if ruta:
                lbl_comprobante.configure(text="📎 " + os.path.basename(ruta), text_color="#1e8449")
            else:
                lbl_comprobante.configure(text="Sin comprobante cargado", text_color="#999999")

        def cargar_comprobante():
            try:
                from tkinter import filedialog
                ruta = filedialog.askopenfilename(
                    parent=v, title="Seleccionar el comprobante de pago",
                    filetypes=[("PDF o imagen", "*.pdf *.jpg *.jpeg *.png"), ("Todos los archivos", "*.*")])
            except Exception as e:
                return messagebox.showerror("Comprobante", "No se pudo abrir el selector: " + str(e), parent=v)
            if ruta:
                ruta_comprobante["ruta"] = ruta
                actualizar_lbl_comprobante()

        def escanear_comprobante():
            def al_terminar(ruta, numero="", fecha_leida="", monto_leido=0.0):
                if not ruta:
                    return
                ruta_comprobante["ruta"] = ruta
                actualizar_lbl_comprobante()
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

        def ver_comprobante():
            ruta = ruta_comprobante.get("ruta") or ""
            if ruta and os.path.isfile(ruta):
                abrir_documento(ruta)
            elif ruta:
                messagebox.showwarning("Comprobante", "El archivo ya no está en esa ubicación:\n" + ruta, parent=v)
            else:
                messagebox.showinfo("Comprobante", "Todavía no ha cargado el comprobante de pago.", parent=v)

        ctk.CTkButton(f_comp, text="📎 Cargar", width=110, height=30, font=("Arial", 11, "bold"),
                      fg_color="#16a085", hover_color="#11806a",
                      command=cargar_comprobante).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(f_comp, text="🖨️ Escanear", width=120, height=30, font=("Arial", 11, "bold"),
                      fg_color="#0e7490", hover_color="#155e75",
                      command=escanear_comprobante).pack(side="left", padx=(6, 0))
        ctk.CTkButton(f_comp, text="👁️ Ver", width=70, height=30, font=("Arial", 11, "bold"),
                      fg_color="#5d6d7e", hover_color="#4a5967",
                      command=ver_comprobante).pack(side="left", padx=(6, 0))

        def consultar_facturas_canceladas():
            ruta = ruta_comprobante.get("ruta") or ""
            operacion = ""
            try:
                operacion = ent_operacion.get().strip()
            except Exception:
                pass
            filas = facturas_que_cancela_comprobante(ruta, operacion)
            if not filas:
                return messagebox.showinfo(
                    "Comprobante",
                    "Este comprobante todavía no cancela ninguna factura registrada.\n\n"
                    "Los números aparecerán cuando se guarden pagos con este mismo comprobante "
                    "o N° de operación.", parent=v)
            messagebox.showinfo("Facturas que cancela este comprobante",
                                "Cancela %d factura(s):\n\n%s"
                                % (len(filas), texto_facturas_canceladas(filas)), parent=v)


        lbl_comprobante.pack(anchor="w", pady=(0, 6))
        actualizar_lbl_comprobante()

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
                proveedor_gasto = (desc_manual or (f"Personal {personal_pago}" if personal_pago else "")
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
        if f.get("tabla") in tx_soporte.TABLAS_TX:
            try:
                tx = next((t for t in self.cargar_transferencias() if t["id"] == f.get("id")), None)
            except RuntimeError as exc:
                messagebox.showerror("Transferencia", str(exc), parent=self.parent_frame)
                return
            if tx:
                self._preparar_form_transferencia(tx)
                self.tabview.set(" 🔄 Transferencias ")
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
                                if f.get("tabla") in tx_soporte.TABLAS_TX:
                                    c.execute("""DELETE FROM conciliacion_bancaria
                                        WHERE origen = ANY(%s) AND id_movimiento=%s AND banco=%s
                                        AND cuenta=%s AND SIGN(monto)=SIGN(%s)""",
                                        (["s:" + f["tabla"], "sistema:" + f["tabla"]], idp,
                                         nombre_banco, banco.get("cuenta", ""), f.get("monto", 0)))
                                else:
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
