# -*- coding: utf-8 -*-
"""
MODULO_BANDEJA.PY - Pantalla de la Bandeja de Entrada
=====================================================
Muestra los PDFs que llegan a la nube (Bandeja/Compras y Bandeja/Ventas),
con lo que el motor propone, y permite:
  - procesar la carpeta,
  - corregir evento / categoría / descripción / orden de compra,
  - registrar (queda en el módulo real y el PDF pasa a su carpeta definitiva),
  - descartar.
"""
import os
import shutil
import datetime

import tkinter as tk
from tkinter import ttk, messagebox

import customtkinter as ctk

import bandeja_entrada as be

ETIQUETA_ESTADO = {"verde": "🟢 Listo", "amarillo": "🟡 Revisar", "rojo": "🔴 Falta algo"}

CATEGORIAS_BASE = ("Producción de Eventos", "Administrativos - Otro", "Personal / Honorarios",
                   "Gastos Fijos", "Combustible", "Peajes", "Hospedaje", "Alimentación",
                   "Viáticos", "Impresión / Publicidad", "Alquileres", "Servicios Básicos",
                   "Gastos Bancarios", "GENERAL / NO ASIGNADO")

SIN_EVENTO = "— Sin evento —"


def _categorias_bd():
    lista = list(CATEGORIAS_BASE)
    conn = None
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if conn:
            with conn.cursor() as c:
                c.execute("""SELECT DISTINCT categoria FROM facturas_recibidas
                             WHERE COALESCE(categoria,'') <> '' ORDER BY 1""")
                for (valor,) in c.fetchall():
                    if valor and valor not in lista:
                        lista.append(valor)
    except Exception:
        pass
    finally:
        try:
            if conn:
                from conexion import liberar_conexion as _lib
                _lib(conn)
        except Exception:
            pass
    return lista


def _ordenes_compra():
    ordenes = []
    conn = None
    try:
        from conexion import conectar_db, liberar_conexion
        conn = conectar_db(silencioso=True)
        if conn:
            with conn.cursor() as c:
                c.execute("""SELECT numero_oc, cliente, COALESCE(monto_total, 0)
                             FROM ordenes_compra_clientes ORDER BY id DESC LIMIT 200""")
                for numero, cliente, monto in c.fetchall():
                    ordenes.append({"numero": str(numero or ""), "cliente": str(cliente or ""),
                                    "monto": float(monto or 0)})
    except Exception:
        pass
    finally:
        try:
            if conn:
                from conexion import liberar_conexion as _lib
                _lib(conn)
        except Exception:
            pass
    return ordenes


class BandejaEntradaApp:
    def __init__(self, contenedor, usuario=""):
        self.contenedor = contenedor
        self.usuario = usuario or "Sistema"
        self.items = []
        self.eventos = be.eventos_abiertos()
        self.categorias = _categorias_bd()
        self.ordenes = _ordenes_compra()
        self._construir()
        self.refrescar()

    # ------------------------------------------------------------ interfaz
    def _construir(self):
        ctk.CTkLabel(self.contenedor, text="📥 Bandeja de Entrada",
                     font=("Arial", 18, "bold"), text_color="#1f538d").pack(pady=(10, 2))
        ctk.CTkLabel(self.contenedor,
                     text="Deja las facturas y comprobantes de pago en las carpetas de la nube y pulsa Procesar.",
                     font=("Arial", 11), text_color="#555555").pack(pady=(0, 6))

        barra = ctk.CTkFrame(self.contenedor, fg_color="#f4f6f8", corner_radius=8)
        barra.pack(fill="x", padx=10, pady=(0, 6))

        self.lbl_carpetas = ctk.CTkLabel(
            barra, text=self._texto_carpetas(), font=("Arial", 10), text_color="#2c3e50",
            justify="left", anchor="w", wraplength=620)
        self.lbl_carpetas.pack(side="left", padx=8, pady=6)

        ctk.CTkButton(barra, text="📎 Agregar PDF", width=126, font=("Arial", 11, "bold"),
                      fg_color="#27ae60", hover_color="#1e8449",
                      command=self.agregar_pdf).pack(side="right", padx=2, pady=6)
        ctk.CTkButton(barra, text="🖨️ Escanear", width=116, font=("Arial", 11, "bold"),
                      fg_color="#e67e22", hover_color="#ca6f1e",
                      command=self.escanear).pack(side="right", padx=2, pady=6)
        ctk.CTkButton(barra, text="▶ Procesar ahora", width=150, font=("Arial", 11, "bold"),
                      fg_color="#1f538d", hover_color="#163b65",
                      command=self.procesar).pack(side="right", padx=6, pady=6)
        ctk.CTkButton(barra, text="🔄 Actualizar", width=110, font=("Arial", 11, "bold"),
                      fg_color="#7f8c8d", hover_color="#606b6b",
                      command=self.refrescar).pack(side="right", padx=2, pady=6)

        filtros = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        filtros.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkLabel(filtros, text="Carpeta:", font=("Arial", 11, "bold")).pack(side="left")
        self.cmb_bandeja = ctk.CTkComboBox(filtros, values=["Todas", "Compras", "Ventas"],
                                           width=120, state="readonly", command=lambda _v: self.refrescar())
        self.cmb_bandeja.set("Todas")
        self.cmb_bandeja.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(filtros, text="Estado:", font=("Arial", 11, "bold")).pack(side="left")
        self.cmb_estado = ctk.CTkComboBox(filtros, values=["Todos", "🟢 Listo", "🟡 Revisar", "🔴 Falta algo"],
                                          width=140, state="readonly", command=lambda _v: self.refrescar())
        self.cmb_estado.set("Todos")
        self.cmb_estado.pack(side="left", padx=(4, 12))

        self.lbl_resumen = ctk.CTkLabel(filtros, text="", font=("Arial", 11, "bold"),
                                        text_color="#1f538d")
        self.lbl_resumen.pack(side="right")

        f_tabla = ctk.CTkFrame(self.contenedor, fg_color="transparent")
        f_tabla.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        columnas = ("estado", "bandeja", "clase", "entidad", "numero", "fecha", "total",
                    "evento", "categoria", "conf")
        self.tabla = ttk.Treeview(f_tabla, columns=columnas, show="headings", height=10)
        for col, titulo, ancho, anchor in (
                ("estado", "Estado", 100, "w"), ("bandeja", "Carpeta", 70, "center"),
                ("clase", "Tipo", 95, "center"), ("entidad", "Proveedor / Cliente", 210, "w"),
                ("numero", "N° Doc", 90, "center"), ("fecha", "Fecha", 85, "center"),
                ("total", "Total", 90, "e"), ("evento", "Evento", 210, "w"),
                ("categoria", "Categoría", 130, "w"), ("conf", "Conf.", 50, "center")):
            self.tabla.heading(col, text=titulo)
            self.tabla.column(col, width=ancho, anchor=anchor)
        vsb = ttk.Scrollbar(f_tabla, orient="vertical", command=self.tabla.yview)
        self.tabla.configure(yscrollcommand=vsb.set)
        self.tabla.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tabla.bind("<<TreeviewSelect>>", lambda _e: self._cargar_seleccion())
        self.tabla.tag_configure("verde", background="#d5f5e3")
        self.tabla.tag_configure("amarillo", background="#fdf2d0")
        self.tabla.tag_configure("rojo", background="#fadbd8")

        # ---- panel de edición
        panel = ctk.CTkFrame(self.contenedor, fg_color="#eef6ff", corner_radius=8,
                             border_width=1, border_color="#aed6f1")
        panel.pack(fill="x", padx=10, pady=(0, 6))
        ctk.CTkLabel(panel, text="✏️ Revisar / corregir el seleccionado",
                     font=("Arial", 11, "bold"), text_color="#1f538d").pack(anchor="w", padx=10, pady=(6, 2))

        fila1 = ctk.CTkFrame(panel, fg_color="transparent")
        fila1.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(fila1, text="Evento:", font=("Arial", 11, "bold"), width=70, anchor="w").pack(side="left")
        self.cmb_evento = ctk.CTkComboBox(fila1, values=[SIN_EVENTO] + [e["etiqueta"] for e in self.eventos],
                                          width=330, state="readonly")
        self.cmb_evento.pack(side="left", padx=(4, 14))
        ctk.CTkLabel(fila1, text="Categoría:", font=("Arial", 11, "bold"), width=72, anchor="w").pack(side="left")
        self.cmb_categoria = ctk.CTkComboBox(fila1, values=self.categorias, width=240)
        self.cmb_categoria.pack(side="left", padx=4)

        fila2 = ctk.CTkFrame(panel, fg_color="transparent")
        fila2.pack(fill="x", padx=10, pady=2)
        ctk.CTkLabel(fila2, text="Descripción:", font=("Arial", 11, "bold"), width=70, anchor="w").pack(side="left")
        self.ent_descripcion = ctk.CTkEntry(fila2, placeholder_text="Descripción de la compra / venta")
        self.ent_descripcion.pack(side="left", fill="x", expand=True, padx=(4, 14))
        self.fila_oc = ctk.CTkFrame(fila2, fg_color="transparent")
        ctk.CTkLabel(self.fila_oc, text="OC:", font=("Arial", 11, "bold")).pack(side="left")
        self.cmb_oc = ctk.CTkComboBox(self.fila_oc, values=["— Sin OC —"], width=200, state="readonly")
        self.cmb_oc.pack(side="left", padx=4)
        self.fila_oc.pack(side="left")

        acciones = ctk.CTkFrame(panel, fg_color="transparent")
        acciones.pack(fill="x", padx=10, pady=(4, 8))
        ctk.CTkButton(acciones, text="✅ Registrar seleccionado", width=190,
                      font=("Arial", 12, "bold"), fg_color="#27ae60", hover_color="#1e8449",
                      command=self.registrar_seleccionado).pack(side="left", padx=(0, 8))
        ctk.CTkButton(acciones, text="✅ Registrar todas las 🟢", width=190,
                      font=("Arial", 12, "bold"), fg_color="#166534", hover_color="#14532d",
                      command=self.registrar_verdes).pack(side="left", padx=(0, 8))
        ctk.CTkButton(acciones, text="🗑 Descartar", width=120,
                      font=("Arial", 12, "bold"), fg_color="#c0392b", hover_color="#922b21",
                      command=self.descartar_seleccionado).pack(side="left", padx=(0, 8))
        self.lbl_estado = ctk.CTkLabel(acciones, text="", font=("Arial", 11, "bold"),
                                       text_color="#555555")
        self.lbl_estado.pack(side="left", padx=10)

    def _texto_carpetas(self):
        return "Compras: %s\nVentas:  %s" % (be.carpeta_bandeja("compra"),
                                              be.carpeta_bandeja("venta"))

    # ------------------------------------------------------------ datos
    def _bandeja(self):
        valor = self.cmb_bandeja.get()
        return {"Compras": "compra", "Ventas": "venta"}.get(valor)

    def _estados(self):
        valor = self.cmb_estado.get()
        for clave, etiqueta in ETIQUETA_ESTADO.items():
            if valor == etiqueta:
                return [clave]
        return None

    def refrescar(self):
        try:
            self.items = be.listar(self._bandeja(), self._estados())
        except Exception as e:
            self.items = []
            print("[bandeja] listar:", e)
        for fila in self.tabla.get_children():
            self.tabla.delete(fila)
        verdes = 0
        for it in self.items:
            d = it["datos"] or {}
            p = it["propuesta"] or {}
            entidad = p.get("proveedor") or p.get("cliente") or ""
            total = d.get("total")
            total_txt = ("{:,.2f}".format(float(total))).replace(",", " ") if total else ""
            if it["estado"] == "verde":
                verdes += 1
            self.tabla.insert("", tk.END, iid=str(it["id"]),
                              values=(ETIQUETA_ESTADO.get(it["estado"], it["estado"]),
                                      "Compras" if it["bandeja"] == "compra" else "Ventas",
                                      "Factura" if it["clase"] == "factura" else "Comprobante",
                                      entidad[:45], d.get("numero") or "", d.get("fecha") or "",
                                      total_txt, (p.get("evento") or "(sin evento)")[:40],
                                      (p.get("categoria") or "")[:28], it["confianza"]),
                              tags=(it["estado"],))
        self.lbl_resumen.configure(text="%d pendiente(s) · %d listas para registrar" %
                                   (len(self.items), verdes))

    def _seleccionado(self):
        sel = self.tabla.selection()
        if not sel:
            return None
        idx = str(sel[0])
        for it in self.items:
            if str(it["id"]) == idx:
                return it
        return None

    def _cargar_seleccion(self):
        it = self._seleccionado()
        if not it:
            return
        p = it["propuesta"] or {}
        d = it["datos"] or {}
        evento = p.get("evento") or SIN_EVENTO
        valores = self.cmb_evento.cget("values")
        if evento in valores:
            self.cmb_evento.set(evento)
        elif p.get("evento"):
            self.cmb_evento.set(SIN_EVENTO)
        self.cmb_categoria.set(p.get("categoria") or "")
        self.ent_descripcion.delete(0, "end")
        self.ent_descripcion.insert(0, p.get("descripcion") or "")
        if it["bandeja"] == "venta":
            self.fila_oc.pack(side="left")
            cliente = str(p.get("cliente") or "").upper()
            valores_oc = ["— Sin OC —"]
            elegida = "— Sin OC —"
            for oc in self.ordenes:
                texto = "%s | %s | S/ %s" % (oc["numero"], oc["cliente"][:24],
                                                "{:,.2f}".format(oc["monto"]))
                valores_oc.append(texto)
                if (d.get("total") and abs(float(d["total"]) - oc["monto"]) < 0.05
                        and cliente and cliente in oc["cliente"].upper() and elegida == "— Sin OC —"):
                    elegida = texto
            self.cmb_oc.configure(values=valores_oc)
            self.cmb_oc.set(elegida)
        else:
            self.fila_oc.pack_forget()

    # ------------------------------------------------------------ acciones
    def procesar(self):
        self.lbl_estado.configure(text="⏳ Procesando la carpeta... puede tardar si hay escaneos",
                                  text_color="#b9770e")
        try:
            self.contenedor.update_idletasks()
        except Exception:
            pass
        resumen = {"compra": None, "venta": None}
        try:
            resumen["compra"] = be.procesar_carpeta("compra")
            resumen["venta"] = be.procesar_carpeta("venta")
            # 🔄 Los pendientes se releen con las reglas actuales (por si mejoró la lectura)
            try:
                be.reanalizar_pendientes("compra")
                be.reanalizar_pendientes("venta")
            except Exception:
                pass
        except Exception as e:
            return self.lbl_estado.configure(text="❌ Error al procesar: %s" % str(e)[:80],
                                             text_color="#c0392b")
        nuevos = (resumen["compra"]["nuevos"] + resumen["venta"]["nuevos"])
        duplicados = (resumen["compra"]["duplicados"] + resumen["venta"]["duplicados"])
        reabiertos = (resumen["compra"].get("reactivados", 0) + resumen["venta"].get("reactivados", 0))
        bancos = (resumen["compra"].get("ignorados", 0) + resumen["venta"].get("ignorados", 0))
        self.refrescar()
        self.lbl_estado.configure(
            text="✅ %d nuevo(s) · %d reabierto(s) · %d duplicado(s) · %d cargo(s) de banco ignorado(s)"
                 % (nuevos, reabiertos, duplicados, bancos),
            text_color="#1e6b3a")

    # ------------------------------------------------------------ agregar
    def _destino(self):
        """Carpeta destino: la del filtro actual o se le pregunta al usuario."""
        actual = self.cmb_bandeja.get()
        if actual == "Compras":
            return "compra"
        if actual == "Ventas":
            return "venta"
        return self._preguntar_destino()

    def _preguntar_destino(self):
        v = ctk.CTkToplevel(self.contenedor)
        v.title("¿Dónde va el archivo?")
        v.geometry("380x150")
        try:
            v.transient(self.contenedor.winfo_toplevel())
            v.grab_set()
        except Exception:
            pass
        elegido = {"valor": None}

        def elegir(valor):
            elegido["valor"] = valor
            v.destroy()

        ctk.CTkLabel(v, text="¿En qué carpeta de la nube lo guardo?",
                     font=("Arial", 12, "bold")).pack(pady=(16, 6))
        fila = ctk.CTkFrame(v, fg_color="transparent")
        fila.pack()
        ctk.CTkButton(fila, text="🛒 Compras", width=150, fg_color="#1f538d",
                      hover_color="#163b65",
                      command=lambda: elegir("compra")).pack(side="left", padx=6)
        ctk.CTkButton(fila, text="💼 Ventas", width=150, fg_color="#8e44ad",
                      hover_color="#703688",
                      command=lambda: elegir("venta")).pack(side="left", padx=6)
        try:
            v.wait_window()
        except Exception:
            pass
        return elegido["valor"]

    def _procesar_tipo(self, tipo):
        self.lbl_estado.configure(text="⏳ Procesando...", text_color="#b9770e")
        try:
            self.contenedor.update_idletasks()
        except Exception:
            pass
        try:
            return be.procesar_carpeta(tipo)
        except Exception as e:
            print("[bandeja] procesar:", e)
            return None

    def agregar_pdf(self):
        """Copia uno o varios PDF/fotos a la carpeta de la bandeja y los procesa."""
        from tkinter import filedialog
        tipo = self._destino()
        if not tipo:
            return
        rutas = filedialog.askopenfilenames(
            title="Elegir factura o comprobante (PDF o foto)",
            filetypes=[("PDF e imágenes", "*.pdf *.png *.jpg *.jpeg"),
                       ("PDF", "*.pdf"), ("Todos", "*.*")],
            parent=self.contenedor)
        if not rutas:
            return
        destino_dir = be.carpeta_bandeja(tipo)
        copiados, errores = 0, []
        for origen in rutas:
            try:
                nombre = os.path.basename(origen)
                base, ext = os.path.splitext(nombre)
                ext = ext.lower()
                marca = datetime.datetime.now().strftime("%H%M%S")
                if ext == ".pdf":
                    final = os.path.join(destino_dir, nombre)
                    if os.path.exists(final):
                        final = os.path.join(destino_dir, "%s_%s.pdf" % (base, marca))
                    shutil.copy2(origen, final)
                elif ext in (".png", ".jpg", ".jpeg"):
                    # 🖼️ Una foto se convierte en PDF para poder leerla igual
                    from PIL import Image
                    final = os.path.join(destino_dir, "%s_%s.pdf" % (base, marca))
                    with Image.open(origen) as img:
                        img.convert("RGB").save(final, "PDF", resolution=200)
                else:
                    errores.append(nombre + ": formato no soportado")
                    continue
                copiados += 1
            except Exception as e:
                errores.append("%s: %s" % (os.path.basename(origen), str(e)[:60]))
        if not copiados:
            return messagebox.showwarning("Bandeja", "No se agregó ningún archivo." + chr(10) +
                                          chr(10).join(errores[:4]), parent=self.contenedor)
        self._procesar_tipo(tipo)
        self.refrescar()
        self.lbl_estado.configure(text="✅ %d archivo(s) agregados a la bandeja" % copiados,
                                  text_color="#1e6b3a")

    def escanear(self):
        """Escanea una hoja y la deja en la carpeta de la bandeja."""
        tipo = self._destino()
        if not tipo:
            return
        destino = os.path.join(be.carpeta_bandeja(tipo),
                               "Escaneo_%s.pdf" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        pdf = self._escanear_a_pdf(destino)
        if not pdf:
            return
        self._procesar_tipo(tipo)
        self.refrescar()
        self.lbl_estado.configure(text="✅ Escaneado y agregado a la bandeja", text_color="#1e6b3a")

    def _escanear_a_pdf(self, destino_pdf):
        try:
            import escaner
        except Exception as e:
            messagebox.showerror("Escáner", "No se pudo cargar el escáner:" + chr(10) + str(e),
                                 parent=self.contenedor)
            return ""
        if not escaner.sistema_soportado():
            messagebox.showinfo("Escáner", "El escaneo está disponible en Windows y macOS.",
                                parent=self.contenedor)
            return ""
        try:
            from modulo_compras import cargar_configuracion_regional
            config = cargar_configuracion_regional()
        except Exception:
            config = {}
        datos = escaner.config_escaner(config)
        aviso = ctk.CTkToplevel(self.contenedor)
        aviso.title("Escaneando")
        aviso.geometry("440x110")
        try:
            aviso.transient(self.contenedor.winfo_toplevel())
            aviso.grab_set()
        except Exception:
            pass
        ctk.CTkLabel(aviso, text="🖨️ Escaneando... coloque la hoja en el cristal",
                     font=("Arial", 12, "bold")).pack(pady=18)
        try:
            aviso.update()
        except Exception:
            pass
        try:
            escaneo = escaner.escanear(
                escaner.carpeta_escaneos(config),
                dispositivo=datos.get("escaner_dispositivo", ""),
                dpi=datos.get("escaner_dpi", "300"),
                color=datos.get("escaner_color", "Escala de grises"),
                origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                paginas=int(str(datos.get("escaner_paginas") or 1)),
                optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                modo=datos.get("escaner_modo", "Automático (sin ventanas)"))
            if not escaneo.get("ok"):
                messagebox.showerror("Escáner", escaneo.get("error") or "No se pudo escanear.",
                                     parent=self.contenedor)
                return ""
            paginas = [str(p) for p in (escaneo.get("paginas") or []) if os.path.exists(str(p))]
            if not paginas:
                messagebox.showwarning("Escáner", "No se generó ninguna página.",
                                       parent=self.contenedor)
                return ""
            carpeta_dest = os.path.dirname(destino_pdf)
            if carpeta_dest:
                os.makedirs(carpeta_dest, exist_ok=True)
            escaner.imagenes_a_pdf(paginas, destino_pdf)
            return destino_pdf
        except Exception as e:
            messagebox.showerror("Escáner", "No se pudo escanear:" + chr(10) + str(e),
                                 parent=self.contenedor)
            return ""
        finally:
            try:
                aviso.grab_release()
                aviso.destroy()
            except Exception:
                pass

    def _ajustes(self):
        categoria = self.cmb_categoria.get().strip()
        descripcion = self.ent_descripcion.get().strip()
        evento = self.cmb_evento.get()
        if evento == SIN_EVENTO:
            evento = ""
        oc = ""
        if self.fila_oc.winfo_ismapped():
            texto = self.cmb_oc.get().strip()
            if texto and not texto.startswith("—"):
                oc = texto.split("|")[0].strip()
        return {"evento": evento, "categoria": categoria,
                "descripcion": descripcion, "orden_compra": oc}

    def registrar_seleccionado(self):
        it = self._seleccionado()
        if not it:
            return messagebox.showinfo("Bandeja", "Seleccione un documento de la lista.", parent=self.contenedor)
        ajustes = self._ajustes()
        if not ajustes["evento"] and it["bandeja"] == "compra":
            if not messagebox.askyesno("Sin evento",
                                       "No eligió un evento. ¿Registrar como 'GENERAL / NO ASIGNADO'?",
                                       parent=self.contenedor):
                return
            ajustes["evento"] = "GENERAL / NO ASIGNADO"
        ok, msg, nuevo = be.registrar(it["id"], ajustes)
        if ok:
            self.lbl_estado.configure(text="✅ " + msg, text_color="#1e6b3a")
            self.refrescar()
        else:
            self.lbl_estado.configure(text="❌ " + msg, text_color="#c0392b")
            messagebox.showerror("Bandeja", msg, parent=self.contenedor)

    def registrar_verdes(self):
        verdes = [i for i in self.items if i["estado"] == "verde"]
        if not verdes:
            return messagebox.showinfo("Bandeja", "No hay documentos 🟢 listos para registrar.",
                                       parent=self.contenedor)
        if not messagebox.askyesno("Registrar", "¿Registrar los %d documentos 🟢?" % len(verdes),
                                   parent=self.contenedor):
            return
        hechos, errores = 0, []
        for it in verdes:
            p = it["propuesta"] or {}
            ok, msg, _nuevo = be.registrar(it["id"], {
                "evento": p.get("evento") or "", "categoria": p.get("categoria") or "",
                "descripcion": p.get("descripcion") or "", "orden_compra": p.get("orden_compra") or ""})
            if ok:
                hechos += 1
            else:
                errores.append("%s: %s" % (os.path.basename(it["ruta"]), msg))
        self.refrescar()
        if errores:
            messagebox.showwarning("Bandeja", "Registrados %d." + chr(10) + "Con problemas:" +
                                   chr(10) + "%s"
                                   % (hechos, chr(10).join(errores[:5])), parent=self.contenedor)
        else:
            self.lbl_estado.configure(text="✅ %d registrados" % hechos, text_color="#1e6b3a")

    def descartar_seleccionado(self):
        it = self._seleccionado()
        if not it:
            return
        if not messagebox.askyesno("Descartar", "¿Descartar «%s»?\nEl archivo pasará a la carpeta _errores."
                                   % os.path.basename(it["ruta"]), parent=self.contenedor):
            return
        if be.descartar(it["id"]):
            self.lbl_estado.configure(text="🗑 Descartado", text_color="#7f8c8d")
            self.refrescar()
