# -*- coding: utf-8 -*-
"""
TAREAS_SEGURAS.PY — Tareas en segundo plano compatibles con Windows y macOS
===========================================================================
PROBLEMA QUE RESUELVE
---------------------
Llamar a la interfaz (Tk / customtkinter) desde un hilo secundario NO es seguro:
en Windows suele "funcionar", pero en macOS CONGELA la aplicación (sobre todo al
abrir/editar varias ventanas seguidas).

CÓMO SE USA
-----------
El hilo hace SOLO el trabajo pesado (consultas a la base de datos, internet,
copiar archivos) y deja los datos en el diccionario 'estado'. La ventana los lee
por SONDEO, siempre desde el hilo principal:

    def _leer(estado):                       # 👈 corre en el hilo secundario
        estado["filas"] = consultar_bd()     #    (nada de tocar widgets aquí)
        estado["avance"] = 0.5               #    opcional: para barras de progreso

    def _pintar(estado):                     # 👈 corre en el hilo PRINCIPAL
        tabla.delete(*tabla.get_children())
        for f in estado["filas"]:
            tabla.insert("", tk.END, values=f)

    ejecutar_en_hilo(mi_ventana, _leer, al_terminar=_pintar)

- 'aplicar(estado)'     : se llama en cada sondeo (para barras de progreso).
- 'al_terminar(estado)' : se llama UNA vez al terminar. Si algo falló, el error
                          viene en estado["error"] (y la tarea se detiene).
"""
import threading

__all__ = ["ejecutar_en_hilo", "esperar_resultado"]


def ejecutar_en_hilo(widget, funcion, aplicar=None, al_terminar=None,
                     intervalo=120, max_intentos=5000):
    """Ejecuta 'funcion(estado)' en un hilo y entrega el resultado al hilo principal.

    Parámetros:
      widget       : cualquier widget Tk (se usa su .after() para el sondeo).
      funcion      : recibe el diccionario 'estado' y lo rellena con los datos.
      aplicar      : opcional; se ejecuta en cada sondeo, desde el hilo principal.
      al_terminar  : se ejecuta una sola vez al terminar, desde el hilo principal.
      intervalo    : milisegundos entre sondeos (120 por defecto).
      max_intentos : tope de sondeos (~10 minutos con el valor por defecto).

    El diccionario 'estado' siempre lleva 'listo' (bool) y 'error' (excepción o None).
    """
    estado = {"listo": False, "error": None}

    def _trabajo():
        try:
            funcion(estado)
        except Exception as e:            # se informa en el hilo principal
            estado["error"] = e
        finally:
            estado["listo"] = True

    def _revisar(intentos=0):
        # Todo este bloque corre en el hilo principal (widget.after)
        try:
            if aplicar is not None:
                try:
                    aplicar(estado)
                except Exception:
                    pass
            if estado["listo"]:
                if al_terminar is not None:
                    try:
                        al_terminar(estado)
                    except Exception:
                        pass
                return
            if intentos < max_intentos:
                widget.after(intervalo, lambda: _revisar(intentos + 1))
        except Exception:
            pass

    threading.Thread(target=_trabajo, daemon=True).start()
    _revisar()
    return estado


def esperar_resultado(widget, funcion, aplicar=None, intervalo=120, max_intentos=5000):
    """Igual que 'ejecutar_en_hilo' pero devuelve el diccionario 'estado'.

    Útil cuando el llamador quiere revisar por su cuenta: se debe consultar
    estado["listo"], estado["error"] y los datos que dejó la función.
    """
    return ejecutar_en_hilo(widget, funcion, aplicar=aplicar,
                            intervalo=intervalo, max_intentos=max_intentos)
