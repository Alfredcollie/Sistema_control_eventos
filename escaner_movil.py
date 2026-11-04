# -*- coding: utf-8 -*-
"""📱 ESCÁNER SOFTWARE COLLIE — servidor para escanear desde el teléfono.

Se enciende cuando el usuario pulsa «📱 Escanear desde el teléfono» en el
módulo de Compras / Ventas y se apaga solo cuando el teléfono termina de enviar
(o al cerrar la ventana).

    PC (este módulo)  ·  http://IP:8765
      GET  /estado           → ¿está encendido? (nombre del equipo y programa)
      GET  /qr.png           → QR para emparejar (lo muestra la ventana)
      POST /emparejar        → {"pin": "123456"} ⇒ {"token": "..."}
      POST /documento        → JPEG en el cuerpo + cabeceras X-Token y X-Tipo

Solo red local (intranet): el servidor escucha en la LAN, nunca en internet.
"""

import json
import os
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROGRAMA = "EVENTOS"      # EVENTOS / FLOTA
PUERTO = 8765
NOMBRE = "Escáner Software Collie"

TIPOS_VALIDOS = ("compra", "venta", "comprobante")


# =========================================================
# Utilidades
# =========================================================
def ip_local():
    """IP del equipo en la red local ("" si no hay red)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def nombre_equipo():
    try:
        return socket.gethostname() or "PC"
    except Exception:
        return "PC"


def enlace_emparejamiento(url, pin):
    """Texto que va dentro del QR (la app lo lee tal cual)."""
    return "escanercollie://pair?url=%s&pin=%s&prog=%s" % (url, pin, PROGRAMA.lower())


def imagen_qr(contenido, lado=320):
    """Imagen PNG (PIL) con el QR, o None si falta 'segno'."""
    try:
        import io as _io

        import segno
        from PIL import Image
    except Exception:
        return None
    try:
        codigo = segno.make(contenido, error="m")
        bufer = _io.BytesIO()
        codigo.save(bufer, kind="png", scale=8, border=2)
        bufer.seek(0)
        imagen = Image.open(bufer).convert("RGB")
        if lado:
            imagen = imagen.resize((lado, lado), Image.NEAREST)
        return imagen
    except Exception:
        return None


# =========================================================
# ⚙️ CONFIGURACIÓN GUARDADA (token por equipo)
# =========================================================
def _archivo_config():
    try:
        from app_paths import DATA_DIR
        base = str(DATA_DIR)
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".escaner_collie")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "escaner_movil.json")


def cargar_config():
    try:
        with open(_archivo_config(), "r", encoding="utf-8") as archivo:
            return json.load(archivo) or {}
    except Exception:
        return {}


def guardar_config(datos):
    try:
        with open(_archivo_config(), "w", encoding="utf-8") as archivo:
            json.dump(datos, archivo, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# =========================================================
# 🖥️ SERVIDOR
# =========================================================
class _Manejador(BaseHTTPRequestHandler):
    servidor_actual = None          # se asigna al crear el servidor

    def log_message(self, formato, *args):      # silencio en consola
        return

    # ------------------------------------------------ respuestas
    def _responder(self, codigo, datos, tipo="application/json"):
        cuerpo = datos if isinstance(datos, bytes) else json.dumps(datos).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(cuerpo)
        except Exception:
            pass

    def _error(self, codigo, mensaje):
        self._responder(codigo, {"ok": False, "error": mensaje})

    # ------------------------------------------------ GET
    def do_GET(self):
        servidor = self.servidor_actual
        ruta = self.path.split("?")[0]
        if ruta in ("/estado", "/"):
            return self._responder(200, {
                "ok": True, "app": NOMBRE, "programa": PROGRAMA,
                "equipo": nombre_equipo(), "version": 1,
                "tipos": list(TIPOS_VALIDOS)})
        if ruta == "/app" or ruta.startswith("/app/"):
            contenido, tipo = servidor.servir_web(self.path)
            if contenido is None:
                return self._error(404, "La app web no esta instalada (falta web_app)")
            return self._responder(200, contenido, tipo or "application/octet-stream")
        if ruta == "/qr.png":
            imagen = servidor.imagen_qr_actual()
            if imagen is None:
                return self._error(500, "Falta la librería 'segno' para el QR")
            try:
                import io as _io
                bufer = _io.BytesIO()
                imagen.save(bufer, format="PNG")
                return self._responder(200, bufer.getvalue(), "image/png")
            except Exception as e:
                return self._error(500, "No se pudo generar el QR: %s" % e)
        return self._error(404, "ruta desconocida")

    # ------------------------------------------------ POST
    def do_POST(self):
        servidor = self.servidor_actual
        ruta = self.path.split("?")[0]
        largo = int(self.headers.get("Content-Length") or 0)
        cuerpo = self.rfile.read(largo) if largo else b""

        if ruta == "/emparejar":
            try:
                datos = json.loads(cuerpo.decode("utf-8") or "{}")
            except Exception:
                return self._error(400, "datos inválidos")
            pin = str(datos.get("pin") or "").strip()
            if not servidor.pin_correcto(pin):
                servidor.registrar_intento(pin)
                return self._error(403, "PIN incorrecto")
            token = servidor.crear_token()
            servidor.avisar("emparejado")
            return self._responder(200, {"ok": True, "token": token,
                                         "programa": PROGRAMA,
                                         "equipo": nombre_equipo()})

        if ruta == "/documento":
            token = (self.headers.get("X-Token") or "").strip()
            if not servidor.token_correcto(token):
                return self._error(403, "token inválido (vuelva a escanear el QR)")
            tipo = (self.headers.get("X-Tipo") or "compra").strip().lower()
            if tipo not in TIPOS_VALIDOS:
                return self._error(400, "tipo de documento inválido")
            if not cuerpo:
                return self._error(400, "no llegó ninguna imagen")
            try:
                resultado = servidor.recibir_documento(cuerpo, tipo)
            except Exception as e:
                return self._error(500, "no se pudo guardar: %s" % e)
            return self._responder(200, {"ok": True, "guardado": resultado,
                                         "programa": PROGRAMA})

        return self._error(404, "ruta desconocida")


class ServidorEscanerMovil:
    """Servidor del escáner por teléfono (se enciende y se apaga solo)."""

    def __init__(self, al_recibir, al_avisar=None, carpeta=None, puerto=PUERTO):
        self.al_recibir = al_recibir           # callback(rutas_imagenes, tipo)
        self.al_avisar = al_avisar             # callback(texto) para la interfaz
        self.carpeta = carpeta                 # dónde guardar las fotos
        self.puerto = int(puerto)
        self.httpd = None
        self.hilo = None
        self.pin = ""
        self.token = ""
        self.activo = False
        self._intentos = 0
        self._cerrar_al_recibir = True

    # ---------------- estado
    def url(self):
        return "http://%s:%d" % (ip_local() or "127.0.0.1", self.puerto)

    def enlace_qr(self):
        return enlace_emparejamiento(self.url(), self.pin)

    def imagen_qr_actual(self):
        return imagen_qr(self.enlace_qr())

    def pin_correcto(self, pin):
        return bool(pin) and secrets.compare_digest(str(pin), str(self.pin))

    def token_correcto(self, token):
        return bool(token) and bool(self.token) and secrets.compare_digest(str(token), str(self.token))

    def crear_token(self):
        self.token = secrets.token_urlsafe(24)
        config = cargar_config()
        config["token_%s" % PROGRAMA.lower()] = self.token
        config["ultimo_emparejamiento"] = time.strftime("%d/%m/%Y %H:%M:%S")
        guardar_config(config)
        return self.token

    def registrar_intento(self, pin):
        self._intentos += 1
        if self._intentos == 5:
            self.avisar("Demasiados PIN incorrectos")

    def avisar(self, texto):
        if self.al_avisar:
            try:
                self.al_avisar(texto)
            except Exception:
                pass

    # ---------------- recibir la foto
    def recibir_documento(self, contenido, tipo):
        carpeta = self.carpeta or os.path.join(os.path.expanduser("~"), "Escaneos")
        os.makedirs(carpeta, exist_ok=True)
        marca = time.strftime("%Y%m%d_%H%M%S")
        ruta = os.path.join(carpeta, "movil_%s_%s_%s.jpg"
                            % (tipo, PROGRAMA.lower(), marca))
        with open(ruta, "wb") as archivo:
            archivo.write(contenido)
        self.avisar("Foto recibida del teléfono: %s" % os.path.basename(ruta))
        if self.al_recibir:
            self.al_recibir([ruta], tipo)
        if self._cerrar_al_recibir:
            threading.Timer(1.0, self.detener).start()
        return ruta


    # ---------------- app web
    def url_web(self):
        """Enlace de la app en el navegador (con el PIN ya incluido)."""
        return "%s/app?pin=%s" % (self.url(), self.pin)

    def servir_web(self, ruta):
        """Devuelve (contenido, tipo_mime) para /app/... o (None, None)."""
        base = carpeta_web()
        if not base:
            return None, None
        pedido = ruta.split("?")[0]
        if pedido in ("/app", "/app/"):
            pedido = "/app/index.html"
        relativo = pedido[len("/app/"):] if pedido.startswith("/app/") else pedido.lstrip("/")
        relativo = relativo.replace("\\", "/")
        partes = [p for p in relativo.split("/") if p not in ("", ".", "..")]
        archivo = os.path.join(base, *partes) if partes else os.path.join(base, "index.html")
        if not os.path.isfile(archivo):
            # rutas internas de Flutter -> index.html (SPA)
            archivo = os.path.join(base, "index.html")
            if not os.path.isfile(archivo):
                return None, None
        try:
            with open(archivo, "rb") as manejador:
                contenido = manejador.read()
        except Exception:
            return None, None
        extension = os.path.splitext(archivo)[1].lower()
        return contenido, TIPOS_MIME.get(extension, "application/octet-stream")

    # ---------------- arranque / parada
    def iniciar(self):
        if self.activo:
            return True, self.pin
        Handler = type("ManejadorEscaner", (_Manejador,), {"servidor_actual": self})
        for intento in range(3):
            try:
                self.httpd = ThreadingHTTPServer(("0.0.0.0", self.puerto), Handler)
                break
            except OSError as e:
                if intento == 2:
                    return False, "No se pudo abrir el puerto %d (%s)" % (self.puerto, e)
                time.sleep(0.4)
        self.httpd.daemon_threads = True
        self.pin = "".join(secrets.choice("0123456789") for _ in range(6))
        self.token = ""
        self._intentos = 0
        self.hilo = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.hilo.start()
        self.activo = True
        return True, self.pin

    def detener(self):
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
            try:
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None
        self.hilo = None
        self.activo = False
        self.avisar("Servidor del teléfono cerrado")
        return True



# =========================================================
# 🌐 APP WEB (la misma app servida por este PC en /app)
# =========================================================
TIPOS_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".otf": "font/otf",
    ".ttf": "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json",
    ".bin": "application/octet-stream",
}


def carpeta_web():
    """Carpeta con la app web (junto al programa o dentro del ejecutable)."""
    candidatas = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_app"),
    ]
    try:
        import sys as _sys
        if getattr(_sys, "_MEIPASS", ""):
            candidatas.append(os.path.join(_sys._MEIPASS, "web_app"))
    except Exception:
        pass
    for ruta in candidatas:
        if os.path.isdir(ruta) and os.path.isfile(os.path.join(ruta, "index.html")):
            return ruta
    return ""

# =========================================================
# 🪟 VENTANA: «📱 Escanear desde el teléfono»
# =========================================================
def escanear_desde_telefono(parent, config=None, al_recibir=None, al_terminar=None,
                            titulo_extra=""):
    """Abre el servidor y la ventana con los QR (app instalada y navegador)."""
    try:
        import customtkinter as ctk
    except Exception:
        return None

    familia = "Helvetica" if __import__("sys").platform == "darwin" else "Arial"

    carpeta = None
    try:
        import escaner
        carpeta = escaner.carpeta_escaneos(config)
    except Exception:
        carpeta = os.path.join(os.path.expanduser("~"), "Escaneos")

    ventana = ctk.CTkToplevel(parent)
    ventana.title("📱 Escanear desde el teléfono")
    try:
        ventana.geometry("560x720")
        ventana.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        ventana.update_idletasks()
        x = (ventana.winfo_screenwidth() - 560) // 2
        y = (ventana.winfo_screenheight() - 720) // 3
        ventana.geometry("560x720+%d+%d" % (max(0, x), max(0, y)))
        ventana.grab_set()
        ventana.lift()
        ventana.focus_force()
    except Exception:
        pass

    ctk.CTkLabel(ventana, text="📱 Escanear desde el teléfono",
                 font=(familia, 16, "bold"), text_color="#1f538d").pack(pady=(12, 0))
    ctk.CTkLabel(ventana, text="Escáner Software Collie" +
                 ((" · " + titulo_extra) if titulo_extra else ""),
                 font=(familia, 10), text_color="#7f8c8d").pack()
    ctk.CTkLabel(ventana, text="Escanee el QR que corresponda a su equipo:",
                 font=(familia, 10), text_color="#555555").pack(pady=(6, 2))

    zona = ctk.CTkFrame(ventana, fg_color="transparent")
    zona.pack(pady=4)
    imagenes = {}

    lbl_url = ctk.CTkLabel(ventana, text="", font=(familia, 12, "bold"), text_color="#2c3e50")
    lbl_url.pack(pady=(6, 0))
    lbl_pin = ctk.CTkLabel(ventana, text="", font=(familia, 22, "bold"), text_color="#c0392b")
    lbl_pin.pack()

    lbl_estado = ctk.CTkLabel(ventana, text="Esperando al teléfono…", font=(familia, 11, "bold"),
                              text_color="#b9770e")
    lbl_estado.pack(pady=(8, 2))
    barra = ctk.CTkProgressBar(ventana, width=380, mode="indeterminate")
    barra.pack(pady=2)
    barra.start()

    def avisar(texto):
        def _actualizar():
            try:
                lbl_estado.configure(text=texto)
                if "cerrado" in str(texto).lower() or "recibida" in str(texto).lower():
                    try:
                        barra.stop()
                    except Exception:
                        pass
            except Exception:
                pass
        try:
            ventana.after(0, _actualizar)
        except Exception:
            pass

    def recibir(rutas, tipo):
        if al_recibir:
            try:
                al_recibir(rutas, tipo)
            except Exception as e:
                print("[Escáner móvil] error al procesar:", e)
        avisar("✅ Foto recibida. Procesando…")
        try:
            ventana.after(1200, cerrar)
        except Exception:
            pass

    servidor = ServidorEscanerMovil(al_recibir=recibir, al_avisar=avisar, carpeta=carpeta)
    ok, resultado = servidor.iniciar()

    def cerrar():
        try:
            servidor.detener()
        except Exception:
            pass
        try:
            ventana.grab_release()
        except Exception:
            pass
        try:
            if al_terminar:
                al_terminar()
        except Exception:
            pass
        try:
            ventana.destroy()
        except Exception:
            pass

    pie = ctk.CTkFrame(ventana, fg_color="transparent")
    pie.pack(side="bottom", pady=10)

    lbl_copiado = ctk.CTkLabel(ventana, text="", font=(familia, 10, "bold"),
                               text_color="#1e8449")
    lbl_copiado.pack(side="bottom")

    def copiar_enlace():
        """Copia el enlace de la app web (Mac / iPhone) al portapapeles."""
        enlace = servidor.url_web()
        try:
            ventana.clipboard_clear()
            ventana.clipboard_append(enlace)
            ventana.update()
            lbl_copiado.configure(text="📋 Enlace copiado: " + enlace)
        except Exception as e:
            lbl_copiado.configure(text="No se pudo copiar: %s" % e, text_color="#c0392b")

    ctk.CTkButton(pie, text="📋 Copiar enlace (Mac / iPhone)", width=250, height=32,
                  font=(familia, 12, "bold"), fg_color="#1f538d", hover_color="#163b65",
                  command=copiar_enlace).pack(side="left", padx=(0, 8))
    ctk.CTkButton(pie, text="✖ Cancelar", width=140, height=32,
                  font=(familia, 12, "bold"), fg_color="#c0392b", hover_color="#922b21",
                  command=cerrar).pack(side="left")

    if not ok:
        avisar("⚠️ " + str(resultado))
        return ventana

    lbl_url.configure(text=servidor.url())
    lbl_pin.configure(text="PIN: %s" % servidor.pin)

    try:
        from PIL import ImageTk
        with PIL_OK_IMPORT if False else _nada():
            pass
    except Exception:
        pass

    # --- los dos QR
    try:
        from PIL import ImageTk
        opciones = [("🌐 Navegador (Mac, iPhone, Android)", servidor.url_web()),
                    ("📱 App instalada (Android)", servidor.enlace_qr())]
        for titulo, enlace in opciones:
            imagen = imagen_qr(enlace, lado=200)
            columna = ctk.CTkFrame(zona, fg_color="#ffffff", corner_radius=8,
                                   border_width=1, border_color="#d5dbdb")
            columna.pack(side="left", padx=6)
            ctk.CTkLabel(columna, text=titulo, font=(familia, 9, "bold"),
                         text_color="#1f538d", wraplength=190).pack(pady=(6, 2))
            etiqueta = ctk.CTkLabel(columna, text="", width=200, height=200)
            etiqueta.pack(padx=6, pady=(0, 6))
            if imagen is not None:
                try:
                    referencia = ctk.CTkImage(light_image=imagen, dark_image=imagen,
                                              size=(200, 200))
                except Exception:
                    from PIL import ImageTk
                    referencia = ImageTk.PhotoImage(imagen)
                imagenes[titulo] = referencia
                etiqueta.configure(image=referencia)
            else:
                etiqueta.configure(text="Falta 'segno'")
        ctk.CTkLabel(ventana, text="En el navegador: " + servidor.url() + "/app",
                     font=(familia, 9), text_color="#7f8c8d").pack()
    except Exception as e_qr:
        print("[Escáner móvil] aviso QR:", e_qr)

    ventana.protocol("WM_DELETE_WINDOW", cerrar)
    return ventana

# =========================================================
# ♻️ REUTILIZAR un flujo de escaneo con la foto del TELÉFONO
# =========================================================
def escanear_reusando_flujo(parent=None, config=None, funcion=None, etiqueta=""):
    """Abre el servidor y, al recibir la foto, ejecuta 'funcion' (el mismo flujo
    del botón del escáner) pero con la imagen del teléfono en lugar del escáner.

    Se sustituye 'escaner.escanear' SOLO mientras corre 'funcion', y se restaura
    siempre (incluso si falla).
    """
    if funcion is None:
        return None
    try:
        import escaner as _escaner
    except Exception as e:
        raise RuntimeError("No se pudo cargar el módulo de escaneo: %s" % e)

    tenia_grab = [False]

    def al_recibir(rutas, tipo):
        def lanzar():
            original = getattr(_escaner, "escanear", None)

            def escanear_falso(*args, **kwargs):
                return {"ok": True, "paginas": list(rutas),
                        "escaner": "Teléfono (Escáner Software Collie)"}

            _escaner.escanear = escanear_falso
            try:
                funcion()
            except Exception as e:
                print("[Escáner móvil] error en el flujo:", e)
            finally:
                if original is not None:
                    _escaner.escanear = original
                devolver_bloqueo()

        def devolver_bloqueo():
            """Vuelve a dejar la ventana del diálogo como modal."""
            try:
                if parent is not None and tenia_grab[0]:
                    parent.after(300, lambda: parent.grab_set())
            except Exception:
                pass

        def en_la_interfaz():
            # se libera el bloqueo modal para poder usar la ventana del teléfono
            try:
                if parent is not None:
                    tenia_grab[0] = bool(parent.grab_current() == parent)
                    parent.grab_release()
            except Exception:
                pass
            threading.Thread(target=lanzar, daemon=True).start()

        try:
            destino = parent if parent is not None else __import__("tkinter")._default_root
            destino.after(0, en_la_interfaz)
        except Exception:
            en_la_interfaz()

    return escanear_desde_telefono(parent, config, al_recibir=al_recibir,
                                   titulo_extra=etiqueta)


# =========================================================
# ❓ ELEGIR POR DÓNDE ADJUNTAR EL COMPROBANTE
# =========================================================
def elegir_como_adjuntar(parent, al_multifuncional=None, al_telefono=None,
                         al_archivo=None, titulo="Comprobante de pago"):
    """Ventana para escoger: multifuncional, teléfono o archivo."""
    try:
        import customtkinter as ctk
    except Exception:
        return None
    familia = "Helvetica" if __import__("sys").platform == "darwin" else "Arial"

    ventana = ctk.CTkToplevel(parent)
    ventana.title("Adjuntar el comprobante")
    try:
        ventana.geometry("430x300")
        ventana.transient(parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent)
        ventana.update_idletasks()
        x = (ventana.winfo_screenwidth() - 430) // 2
        y = (ventana.winfo_screenheight() - 300) // 3
        ventana.geometry("430x300+%d+%d" % (max(0, x), max(0, y)))
        ventana.grab_set()
        ventana.lift()
        ventana.focus_force()
    except Exception:
        pass

    ctk.CTkLabel(ventana, text="¿Cómo desea adjuntar el comprobante?",
                 font=(familia, 14, "bold"), text_color="#1f538d").pack(pady=(18, 2))
    ctk.CTkLabel(ventana, text=titulo, font=(familia, 10),
                 text_color="#7f8c8d").pack()

    def hacer(accion):
        try:
            ventana.grab_release()
        except Exception:
            pass
        try:
            ventana.destroy()
        except Exception:
            pass
        if accion:
            try:
                accion()
            except Exception as e:
                print("[Adjuntar comprobante] error:", e)

    botones = (
        ("🖨️  Escáner multifuncional", "#0e7490", "#155e75", al_multifuncional),
        ("📱  Escanear con el teléfono", "#8e44ad", "#6c3483", al_telefono),
        ("📎  Cargar un archivo", "#16a085", "#11806a", al_archivo),
    )
    for texto, color, hover, accion in botones:
        ctk.CTkButton(ventana, text=texto, height=38, font=(familia, 12, "bold"),
                      fg_color=color, hover_color=hover,
                      command=lambda a=accion: hacer(a)).pack(fill="x", padx=24, pady=5)
    ctk.CTkButton(ventana, text="✖ Ahora no", height=30, font=(familia, 11, "bold"),
                  fg_color="#7f8c8d", hover_color="#606b6b",
                  command=lambda: hacer(None)).pack(fill="x", padx=24, pady=(10, 14))
    return ventana
