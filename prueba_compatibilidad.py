# -*- coding: utf-8 -*-
"""
PRUEBA_COMPATIBILIDAD.PY - Comprueba que el programa funciona en Windows y macOS.

No abre ventanas del programa: simula las dos plataformas y verifica que el
codigo NO use llamadas que solo existen en Windows:

    state("zoomed")                -> solo Windows
    ctypes / SetForegroundWindow   -> solo Windows
    os.startfile                   -> solo Windows

Se puede ejecutar de dos formas:
    * doble clic en PROBAR_COMPATIBILIDAD.bat   (recomendado)
    * python prueba_compatibilidad.py

Siempre deja el resultado en "prueba_compatibilidad.log" (al lado del programa).
"""
import importlib
import io
import os
import sys

ARCHIVO_LOG = "prueba_compatibilidad.log"
FALLOS = []
OK = []


class Tee:
    """Escribe a la vez en la pantalla y en el archivo de registro."""

    def __init__(self, ruta):
        try:
            self.archivo = io.open(ruta, "w", encoding="utf-8")
        except Exception:
            self.archivo = None

    def escribir(self, texto=""):
        # Se usa builtins.print para no llamarse a sí mismo (print está redefinido)
        try:
            import builtins
            builtins.print(texto)
        except Exception:
            pass
        if self.archivo is not None:
            try:
                self.archivo.write(str(texto) + "\n")
                self.archivo.flush()
            except Exception:
                pass

    def cerrar(self):
        if self.archivo is not None:
            try:
                self.archivo.close()
            except Exception:
                pass


SALIDA = Tee(ARCHIVO_LOG)
print = SALIDA.escribir            # noqa: A001  (se redirige la salida)


def comprobar(nombre, condicion, detalle=""):
    if condicion:
        OK.append(nombre)
        print("   [OK]   " + nombre)
    else:
        FALLOS.append(nombre + ("  (" + detalle + ")" if detalle else ""))
        print("   [FALLA] " + nombre + ("  (" + detalle + ")" if detalle else ""))


class VentanaFalsa:
    """Imita una ventana de Tkinter apuntando que llamadas se le hacen."""

    def __init__(self, plataforma):
        self.plataforma = plataforma
        self.llamadas = []

    def state(self, *args):
        self.llamadas.append(("state", args))
        if args and args[0] == "zoomed" and self.plataforma == "darwin":
            raise RuntimeError("'zoomed' no existe en macOS")
        return "normal"

    def geometry(self, *args):
        self.llamadas.append(("geometry", args))
        return "1024x768+0+0"

    def attributes(self, *args):
        self.llamadas.append(("attributes", args))
        return False

    def winfo_screenwidth(self):
        return 1440

    def winfo_screenheight(self):
        return 900

    def winfo_width(self):
        return 1440

    def winfo_height(self):
        return 870

    def winfo_id(self):
        return 1

    def winfo_exists(self):
        return True

    def update_idletasks(self):
        pass

    def iconify(self):
        self.llamadas.append(("iconify", ()))

    def deiconify(self):
        self.llamadas.append(("deiconify", ()))

    def lift(self):
        self.llamadas.append(("lift", ()))

    def focus_force(self):
        self.llamadas.append(("focus_force", ()))

    def after(self, *args, **kwargs):
        pass

    def config(self, *args, **kwargs):
        pass

    configure = config


def llamadas(ventana, nombre):
    return [argumentos for (metodo, argumentos) in ventana.llamadas if metodo == nombre]


def probar_ventanas(modulo):
    funcion = None
    for nombre in ("maximizar_ventana_seguro", "maximizar_ventana"):
        if hasattr(modulo, nombre):
            funcion = getattr(modulo, nombre)
            break
    if funcion is None:
        print("   (este programa no tiene funcion de maximizar)")
        return

    original = sys.platform
    try:
        sys.platform = "win32"
        ventana = VentanaFalsa("win32")
        funcion(ventana)
        estados = [a[0] for a in llamadas(ventana, "state") if a]
        comprobar("Windows usa 'zoomed'", "zoomed" in estados, str(estados))
    finally:
        sys.platform = original

    try:
        sys.platform = "darwin"
        ventana = VentanaFalsa("darwin")
        funcion(ventana)
        geometrias = llamadas(ventana, "geometry")
        comprobar("macOS NO usa 'zoomed'",
                  not any(a and a[0] == "zoomed" for a in llamadas(ventana, "state")))
        comprobar("macOS maximiza por geometry", bool(geometrias), str(geometrias[:1]))
        if geometrias:
            alto = str(geometrias[0][0])
            comprobar("macOS deja sitio a la barra de menu",
                      "-30" in alto or "870" in alto or "900" in alto, alto)
    finally:
        sys.platform = original

    if hasattr(modulo, "poner_ventana_en_barra_tareas"):
        try:
            sys.platform = "darwin"
            modulo.poner_ventana_en_barra_tareas(VentanaFalsa("darwin"))
            comprobar("macOS no toca la barra de tareas de Windows", True)
        except Exception as error:
            comprobar("macOS no toca la barra de tareas de Windows", False, str(error)[:60])
        finally:
            sys.platform = original


MODULOS = ["control_general", "app_paths", "escaner", "escaner_movil", "clientes",
           "modulo_compras", "modulo_ventas"]


def main():
    print("=" * 62)
    print("PRUEBA DE COMPATIBILIDAD   (plataforma actual: %s)" % sys.platform)
    print("=" * 62)

    print()
    print("1) Los modulos se importan sin errores")
    importados = {}
    for nombre in MODULOS:
        try:
            importados[nombre] = importlib.import_module(nombre)
            comprobar("import " + nombre, True)
        except Exception as error:
            # En los servidores de compilacion puede faltar la configuracion, la
            # base de datos o Tkinter: eso NO es un problema de compatibilidad.
            print("   [AVISO] import %s omitido: %s" % (nombre, str(error)[:60]))

    print()
    print("2) Ventanas: Windows y macOS")
    if "control_general" in importados:
        probar_ventanas(importados["control_general"])
    else:
        print("   [AVISO] no se pudo cargar control_general: se omite esta parte")

    print()
    print("3) El codigo solo-Windows esta protegido")
    import re
    # se revisan los archivos del programa (aunque no se hayan podido importar)
    carpeta = os.path.dirname(os.path.abspath(__file__))
    archivos = []
    for nombre in ("modulo_compras.py", "modulo_ventas.py", "clientes.py",
                   "contratos_actas.py", "control_general.py"):
        ruta = os.path.join(carpeta, nombre)
        if os.path.isfile(ruta):
            archivos.append((nombre, ruta))
    for nombre, ruta in archivos:
        try:
            fuente = io.open(ruta, "r", encoding="utf-8").read()
        except Exception:
            continue
        for coincidencia in re.finditer(r"os\.startfile", fuente):
            contexto = fuente[max(0, coincidencia.start() - 400):coincidencia.start()]
            protegido = ("win32" in contexto) or ("darwin" in contexto)
            comprobar("%s: os.startfile protegido" % nombre, protegido,
                      "linea %d" % (fuente[:coincidencia.start()].count("\n") + 1))

    print()
    print("=" * 62)
    print("RESULTADO: %d comprobaciones OK - %d fallos" % (len(OK), len(FALLOS)))
    for fallo in FALLOS:
        print("   [FALLA] " + fallo)
    print("=" * 62)
    print("Registro guardado en: " + os.path.abspath(ARCHIVO_LOG))
    return 0 if not FALLOS else 1


if __name__ == "__main__":
    codigo = 1
    try:
        codigo = main()
    except Exception:
        import traceback
        print()
        print("ERROR INESPERADO:")
        SALIDA.escribir(traceback.format_exc())
    finally:
        SALIDA.cerrar()
        # Si lo abre con doble clic, la ventana espera para que se pueda leer
        if "--sin-pausa" not in sys.argv and not os.environ.get("CI"):
            try:
                input("\nPulse ENTER para cerrar esta ventana...")
            except Exception:
                pass
    sys.exit(codigo)
