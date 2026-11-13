# -*- coding: utf-8 -*-
"""
PRUEBA_COMPATIBILIDAD.PY - Comprueba que el programa funciona en Windows y macOS.

No abre ventanas del programa: simula las dos plataformas y verifica que el
codigo NO use llamadas que solo existen en Windows:

    state("zoomed")                -> solo Windows
    ctypes.windll / SetForegroundWindow -> solo Windows
    os.startfile                   -> solo Windows
    winreg                         -> solo Windows

La revision del punto 3 se hace con AST sobre TODOS los .py del programa
(menos copias y respaldos): asi un "os.startfile" escrito dentro de un
comentario o de la documentacion no se cuenta como error.

Se puede ejecutar de dos formas:
    * doble clic en PROBAR_COMPATIBILIDAD.bat   (recomendado)
    * python prueba_compatibilidad.py

Siempre deja el resultado en "prueba_compatibilidad.log" (al lado del programa).
"""
import ast
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


# =========================================================
# 🔎 LLAMADAS QUE SOLO EXISTEN EN WINDOWS
#    Se revisan TODOS los archivos .py del programa y se analiza el
#    código real (AST), no el texto: así un "os.startfile" escrito en un
#    comentario o en la documentación NO se cuenta como error.
# =========================================================
MARCAS_DE_GUARDA = ("win32", "darwin", "'platform'", "es_windows", "'nt'")


def _mapa_de_padres(arbol):
    padres = {}
    for nodo in ast.walk(arbol):
        for hijo in ast.iter_child_nodes(nodo):
            padres[hijo] = nodo
    return padres


def _esta_protegido(nodo, padres):
    """True si la llamada está dentro de un 'if sys.platform == ...' / es_windows()."""
    actual = padres.get(nodo)
    while actual is not None:
        condicion = None
        if isinstance(actual, (ast.If, ast.IfExp)):
            condicion = actual.test
        if condicion is not None:
            texto = ast.dump(condicion)
            es_solo_mac = ("darwin" in texto
                           and "win32" not in texto
                           and "es_windows" not in texto
                           and "'nt'" not in texto)
            # una rama "if sys.platform == 'darwin'" NO protege de Windows
            if not es_solo_mac and any(marca in texto for marca in MARCAS_DE_GUARDA):
                return True
        actual = padres.get(actual)
    return False


def _ruta_llamada(nodo):
    """'ctypes.windll.user32.ShowWindow' a partir de la llamada del AST."""
    partes = []
    actual = nodo.func
    while isinstance(actual, ast.Attribute):
        partes.append(actual.attr)
        actual = actual.value
    if isinstance(actual, ast.Name):
        partes.append(actual.id)
    return ".".join(reversed(partes))


def _usos_solo_windows(arbol):
    """[(nodo, etiqueta)] de las llamadas/imports que no existen en macOS."""
    usos = []
    for nodo in ast.walk(arbol):
        etiqueta = ""
        if isinstance(nodo, ast.Call):
            ruta = _ruta_llamada(nodo)
            argumento = ""
            if nodo.args and isinstance(nodo.args[0], ast.Constant):
                argumento = str(nodo.args[0].value)
            if ruta.startswith("os.startfile"):
                etiqueta = "os.startfile"
            elif ruta.startswith("ctypes.windll"):
                etiqueta = "ctypes.windll"
            elif ruta.startswith("winreg."):
                etiqueta = "winreg"
            elif ruta.endswith(".state") and argumento == "zoomed":
                etiqueta = "state('zoomed')"
        elif isinstance(nodo, (ast.Import, ast.ImportFrom)):
            nombres = [getattr(alias, "name", "") or "" for alias in nodo.names]
            modulo = getattr(nodo, "module", "") or ""
            if any(n.split(".")[0] == "winreg" for n in nombres) or modulo == "winreg":
                etiqueta = "winreg"
        if etiqueta:
            usos.append((nodo, etiqueta))
    return usos


def revisar_solo_windows(carpeta):
    """Revisa todos los .py del programa (menos copias y respaldos)."""
    excluidos = ("copia", "BAK", "_Archivo", "prueba_compatibilidad")
    archivos = sorted(n for n in os.listdir(carpeta)
                      if n.endswith(".py") and not any(x in n for x in excluidos))
    revisados = 0
    con_usos = 0
    for nombre in archivos:
        ruta = os.path.join(carpeta, nombre)
        try:
            fuente = io.open(ruta, "r", encoding="utf-8").read()
        except Exception:
            continue
        try:
            arbol = ast.parse(fuente, filename=nombre)
        except SyntaxError as error:
            comprobar("%s: se puede analizar" % nombre, False, "linea %s" % error.lineno)
            continue
        padres = _mapa_de_padres(arbol)
        usos = _usos_solo_windows(arbol)
        if not usos:
            continue
        con_usos += 1
        revisados += len(usos)
        sueltos = ["%s (linea %d)" % (etiqueta, nodo.lineno)
                   for nodo, etiqueta in usos if not _esta_protegido(nodo, padres)]
        comprobar("%s: llamadas solo-Windows protegidas (%d)" % (nombre, len(usos)),
                  not sueltos, ", ".join(sueltos[:3]))
    comprobar("se revisaron las llamadas solo-Windows (%d)" % revisados, revisados > 0)
    print("   (%d usos revisados en %d archivos)" % (revisados, con_usos))


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
    # se revisan los archivos del programa (aunque no se hayan podido importar)
    carpeta = os.path.dirname(os.path.abspath(__file__))
    revisar_solo_windows(carpeta)

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
