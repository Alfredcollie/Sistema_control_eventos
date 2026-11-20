# -*- coding: utf-8 -*-
"""CONCILIACIÓN BANCARIA INTELIGENTE (motor por niveles).

Este archivo es SOLO la lógica: no tiene ventanas ni menú propio.
Lo usa el módulo de Banco (pestaña «Conciliación Bancaria»).

Reglas (según el prompt maestro):
  * Nada se concilia solo: el motor PROPONE y la pantalla de revisión aprueba.
  * Nunca se reutiliza un movimiento bancario ni un asiento contable.
  * Nunca se cruzan bancos distintos, monedas distintas ni ingreso con egreso.
  * Ante ambigüedad (dos candidatos equivalentes) no se decide: se marca AMBIGUO.
  * Las diferencias de importe son SOLO informativas (no concilian).
"""
import datetime
import re
import unicodedata

TOLERANCIA_IMPORTE = 0.005   # importes idénticos (sin diferencia de céntimos)
DIAS_DESFASE_POR_DEFECTO = 5

# Ruido habitual de las descripciones bancarias
RUIDO = {"PAGO", "PAGOS", "COBRO", "COBROS", "CONSUMO", "POS", "DE", "DEL", "LA", "EL", "LOS",
         "LAS", "SA", "SAC", "SC", "EIRL", "SOCIEDAD", "ANONIMA", "CERRADA", "PERU", "SRL",
         "LTDA", "Y", "EN", "POR", "FACTURA", "FACT", "BOLETA", "TRANSFERENCIA", "TRANSFERIDO",
         "ABONO", "CARGO", "DEPOSITO", "RETIRO", "OPERACION", "NRO", "REF"}


def normalizar_texto(texto):
    """Mayúsculas, sin tildes ni signos; devuelve la lista de palabras útiles."""
    limpio = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^A-Za-z0-9 ]", " ", limpio.upper())
    return [p for p in limpio.split() if len(p) > 2 and p not in RUIDO]


def similitud_nombres(a, b):
    """0.0 a 1.0 según palabras en común (Jaccard sobre palabras útiles)."""
    pa, pb = set(normalizar_texto(a)), set(normalizar_texto(b))
    if not pa or not pb:
        return 0.0
    return round(len(pa & pb) / float(len(pa | pb)), 2)


def normalizar_fecha(valor):
    """Convierte texto (dd/mm/aaaa o aaaa-mm-dd) en date."""
    texto = str(valor or "").strip().replace("-", "/")
    partes = [p for p in texto.split("/") if p.strip()]
    try:
        if len(partes) >= 3:
            a, b, c = int(partes[0]), int(partes[1]), int(partes[2])
            if a > 31:                      # aaaa/mm/dd
                return datetime.date(a, b, c)
            return datetime.date(c, b, a)   # dd/mm/aaaa
    except Exception:
        pass
    return None


def normalizar_monto(valor):
    """Importe absoluto a 2 decimales (acepta 1,234.56 y 1.234,56)."""
    if isinstance(valor, (int, float)):
        return round(abs(float(valor)), 2)
    texto = str(valor or "").replace("S/.", "").replace("S/", "").strip()
    texto = texto.replace(" ", "")
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    else:
        texto = texto.replace(",", ".")
    try:
        return round(abs(float(texto)), 2)
    except Exception:
        return 0.0


def _sentido(monto, texto=""):
    """'ingreso' o 'egreso' (por signo o por palabras del banco)."""
    try:
        if float(monto) > 0:
            return "ingreso"
        if float(monto) < 0:
            return "egreso"
    except Exception:
        pass
    return "ingreso" if any(p in str(texto).upper() for p in ("ABONO", "DEPOSITO", "INGRESO")) \
        else "egreso"


def _confianza(importe_ok, dias, similitud, referencia_ok, unico):
    """Puntaje 0-100 con metodología fija (no arbitraria)."""
    puntos = 0
    if importe_ok:
        puntos += 50
    if dias == 0:
        puntos += 25
    elif dias <= 2:
        puntos += 20
    elif dias <= 4:
        puntos += 12
    elif dias <= 5:
        puntos += 8
    puntos += int(round(min(1.0, max(0.0, similitud)) * 20))
    if referencia_ok:
        puntos += 5
    if not unico:
        puntos = min(puntos, 60)          # con varios candidatos no es confiable
    elif importe_ok and dias <= 5:
        # 🎯 coincidencia ÚNICA por monto. Si además no comparten ninguna palabra
        #    de la descripción, se marca para revisar (75) en vez de 100.
        return 100                        # único por monto: se concilia (la descripción no importa)
    return min(99, puntos)


def _clasificar(nivel, dias):
    if nivel == 4:
        return "CONCILIADO AGRUPADO"
    if dias == 0:
        return "CONCILIADO EXACTO"
    return "CONCILIADO CON DESFASE"


def proponer(movimientos_banco, asientos, ventana_dias=DIAS_DESFASE_POR_DEFECTO,
             tolerancia=TOLERANCIA_IMPORTE):
    """Devuelve las propuestas de conciliación (sin escribir nada).

    movimientos_banco: [{"id", "fecha", "descripcion", "monto", "banco", "pagina"}]
    asientos:          [{"id", "fecha", "descripcion", "monto", "banco"}]
    """
    candidatos = []
    for i_banco, mov in enumerate(movimientos_banco):
        fecha_banco = normalizar_fecha(mov.get("fecha"))
        importe_banco = normalizar_monto(mov.get("monto"))
        sentido_banco = _sentido(mov.get("monto"), mov.get("descripcion"))
        for i_as, asiento in enumerate(asientos):
            if str(asiento.get("banco") or "").upper() != str(mov.get("banco") or "").upper():
                continue                            # 🚫 bancos distintos
            importe_contable = normalizar_monto(asiento.get("monto"))
            if abs(importe_contable - importe_banco) > tolerancia:
                continue                            # 🚫 importe distinto (informativo)
            if _sentido(asiento.get("monto"), asiento.get("descripcion")) != sentido_banco:
                continue                            # 🚫 ingreso con egreso
            fecha_contable = normalizar_fecha(asiento.get("fecha"))
            if not fecha_banco or not fecha_contable:
                continue
            dias = abs((fecha_banco - fecha_contable).days)
            if dias > ventana_dias:
                continue                            # 🚫 fuera de la ventana
            candidatos.append({"i_banco": i_banco, "i_as": i_as, "dias": dias,
                               "similitud": similitud_nombres(mov.get("descripcion"),
                                                              asiento.get("descripcion"))})
    # se evalúa la asignación global: primero los cruces de mayor certeza
    for c in candidatos:
        c["puntaje"] = _confianza(True, c["dias"], c["similitud"], False, True)
    candidatos.sort(key=lambda c: (-c["puntaje"], c["dias"], -c["similitud"]))

    usados_banco, usados_as, propuestas = set(), set(), []
    for c in candidatos:
        if c["i_banco"] in usados_banco or c["i_as"] in usados_as:
            continue
        # ¿hay otro candidato igual de bueno para el mismo movimiento? -> AMBIGUO
        rivales = [x for x in candidatos
                   if x is not c and x["i_banco"] == c["i_banco"] and x["i_as"] not in usados_as
                   and abs(x["puntaje"] - c["puntaje"]) <= 5
                   and x["dias"] == c["dias"]]
        unico = not rivales
        mov, asiento = movimientos_banco[c["i_banco"]], asientos[c["i_as"]]
        confianza = _confianza(True, c["dias"], c["similitud"], False, unico)
        # 💰 si el importe no es EXACTO (diferencia de céntimos) va a revisión
        diferencia = round(abs(normalizar_monto(asiento.get("monto"))
                               - normalizar_monto(mov.get("monto"))), 2)
        if diferencia > 0.005:
            confianza = min(confianza, 70)
        propuestas.append({
            "i_banco": c["i_banco"], "i_asiento": c["i_as"],
            "id_movimiento_banco": mov.get("id"), "id_asiento": asiento.get("id"),
            "banco": mov.get("banco"), "cuenta": mov.get("cuenta", ""),
            "fecha_contable": asiento.get("fecha"), "fecha_banco": mov.get("fecha"),
            "dias_desfase": c["dias"],
            "descripcion_contable": asiento.get("descripcion"),
            "descripcion_banco": mov.get("descripcion"),
            "importe_contable": normalizar_monto(asiento.get("monto")),
            "importe_banco": normalizar_monto(mov.get("monto")),
            "diferencia": diferencia, "sentido": _sentido(mov.get("monto"), mov.get("descripcion")),
            "referencia": mov.get("referencia", ""), "pagina_pdf": mov.get("pagina"),
            "nivel": 2 if c["dias"] else 1,
            "estado": "AMBIGUO" if not unico else _clasificar(1, c["dias"]),
            "confianza": confianza,
            # 🔎 Cuántos candidatos equivalentes hay para esta línea del banco:
            #    con 1 se concilia solo; con 2+ hay que elegir a mano.
            "rivales": len(rivales),
            "candidatos": len(rivales) + 1,
            # Detalle de TODOS los candidatos, para que la pantalla los muestre.
            "candidatos_detalle": [
                {"id_asiento": asientos[x["i_as"]].get("id"),
                 "descripcion": asientos[x["i_as"]].get("descripcion"),
                 "fecha": asientos[x["i_as"]].get("fecha"),
                 "tabla": asientos[x["i_as"]].get("tabla", ""),
                 "dias": x["dias"]}
                for x in ([c] + rivales)
            ],
            "reglas": "importe exacto; mismo banco; sentido igual; desfase %d día(s); "
                      "similitud %.2f" % (c["dias"], c["similitud"]),
            "observacion": "Varios candidatos equivalentes" if not unico else "",
            "accion_sugerida": "Revisar y aprobar" if unico else "Elegir el movimiento correcto",
        })
        usados_banco.add(c["i_banco"]); usados_as.add(c["i_as"])
    return propuestas


def resumen(propuestas, movimientos_banco, asientos):
    """Totales para el reporte (A-I del prompt)."""
    conciliados = [p for p in propuestas if p["estado"].startswith("CONCILIADO")]
    ambiguos = [p for p in propuestas if p["estado"] == "AMBIGUO"]
    return {
        "movimientos_banco": len(movimientos_banco),
        "asientos": len(asientos),
        "propuestas": len(propuestas),
        "conciliables": len(conciliados),
        "ambiguos": len(ambiguos),
        "importe_conciliado": round(sum(p["importe_banco"] for p in conciliados), 2),
        "banco_sin_asiento": len(movimientos_banco) - len(propuestas),
        "asiento_sin_banco": len(asientos) - len(propuestas),
        "porcentaje_movimientos": round(100.0 * len(conciliados) / len(movimientos_banco), 1)
        if movimientos_banco else 0.0,
    }


# =========================================================
# ETAPA 2 · REPORTE A–I  y  VALIDACIÓN DE SALDOS
# =========================================================
CATEGORIAS = {
    "A": "Conciliados exactos",
    "B": "Conciliados con desfase",
    "C": "Conciliados agrupados",
    "D": "Pendientes de procesamiento bancario",
    "E": "Movimientos bancarios sin asiento contable",
    "F": "Asientos contables sin movimiento bancario",
    "G": "Diferencias de importe (informativas)",
    "H": "Posibles duplicados",
    "I": "Coincidencias ambiguas",
}


def reporte(propuestas, movimientos_banco, asientos, ventana_dias=DIAS_DESFASE_POR_DEFECTO):
    """Agrupa los resultados en las categorías A–I del prompt maestro."""
    grupos = {clave: [] for clave in CATEGORIAS}
    usados_banco, usados_as = set(), set()
    for p in propuestas:
        usados_banco.add(p["i_banco"])
        usados_as.add(p["i_asiento"])
        estado = p["estado"]
        if estado == "AMBIGUO" or p["confianza"] < 60:
            grupos["I"].append(p)
        elif "AGRUPADO" in estado:
            grupos["C"].append(p)
        elif p["dias_desfase"] == 0:
            grupos["A"].append(p)
        else:
            grupos["B"].append(p)
    for indice, mov in enumerate(movimientos_banco):
        if indice not in usados_banco:
            grupos["E"].append(mov)
    for indice, asiento in enumerate(asientos):
        if indice in usados_as:
            continue
        fecha = normalizar_fecha(asiento.get("fecha"))
        if fecha is None:
            grupos["F"].append(asiento)
            continue
        dias = (datetime.date.today() - fecha).days
        grupos["D" if 0 <= dias <= ventana_dias else "F"].append(asiento)
    return grupos


def validar_saldos(saldo_inicial_banco, saldo_final_banco, saldo_inicial_sistema,
                   saldo_final_sistema, propuestas):
    """Compara los saldos y explica la diferencia con las partidas en tránsito."""
    try:
        inicial_banco = float(saldo_inicial_banco or 0)
        final_banco = float(saldo_final_banco or 0)
        inicial_sis = float(saldo_inicial_sistema or 0)
        final_sis = float(saldo_final_sistema or 0)
    except Exception:
        inicial_banco = final_banco = inicial_sis = final_sis = 0.0
    conciliado = round(sum(p["importe_banco"] for p in propuestas), 2)
    return {
        "saldo_inicial_banco": round(inicial_banco, 2),
        "saldo_final_banco": round(final_banco, 2),
        "saldo_inicial_sistema": round(inicial_sis, 2),
        "saldo_final_sistema": round(final_sis, 2),
        "importe_conciliado": conciliado,
        "diferencia_banco": round((final_banco - inicial_banco) - conciliado, 2),
        "diferencia_sistema": round((final_sis - inicial_sis) - conciliado, 2),
        "diferencia_final": round(final_banco - final_sis, 2),
    }
