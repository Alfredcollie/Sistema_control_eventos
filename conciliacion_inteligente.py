# -*- coding: utf-8 -*-
"""CONCILIACIÓN BANCARIA INTELIGENTE (motor por niveles).

Este archivo es SOLO la lógica: no tiene ventanas ni menú propio.
Lo usa el módulo de Banco (pestaña «Conciliación Bancaria»).

Reglas (prompt maestro + acuerdos con el usuario, 2026-11):
  * Nada se concilia solo salvo el caso SEGURO: misma FECHA, mismo IMPORTE y
    ese importe NO se repite en ningún lado dentro de los días de desfase.
  * Nunca se reutiliza un movimiento bancario ni un asiento contable.
  * Nunca se cruzan bancos, cuentas, monedas distintas ni ingreso con egreso.
  * Ante ambigüedad (dos candidatos equivalentes) no se decide: AMBIGUO.
  * Las diferencias de importe son SOLO informativas (no concilian).
  * El N° de operación, si coincide en los dos lados, desambigua.
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


# =========================================================
# Comparación tolerante de banco / cuenta / moneda / referencia
# =========================================================
def _texto_compacto(valor):
    """Mayúsculas sin tildes ni separadores: 'BCP S.A.' -> 'BCPSA'."""
    limpio = unicodedata.normalize("NFKD", str(valor or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", limpio.upper())


def _solo_digitos(valor):
    return re.sub(r"\D", "", str(valor or ""))


def _bancos_compatibles(a, b):
    """Mismo banco aunque el nombre venga abreviado ('BCP' / 'BCP S.A.')."""
    na, nb = _texto_compacto(a), _texto_compacto(b)
    if not na or not nb:
        return na == nb
    if na == nb:
        return True
    return ((len(na) >= 3 and na in nb) or (len(nb) >= 3 and nb in na))


def _cuentas_compatibles(a, b):
    """Misma cuenta aunque una venga con prefijo o guiones.

    '194-7374220-0-69' y 'BCP 194-7374220-0-69' son la misma cuenta.
    Si algún lado no informa la cuenta, no se descarta el cruce.
    """
    na, nb = _texto_compacto(a), _texto_compacto(b)
    if not na or not nb:
        return True
    if na == nb:
        return True
    da, db = _solo_digitos(na), _solo_digitos(nb)
    if len(da) >= 6 and len(db) >= 6:
        return da in db or db in da
    return na in nb or nb in na


_MONEDAS = {
    "PEN": "SOLES", "SOLES": "SOLES", "SOL": "SOLES", "S/": "SOLES", "S/.": "SOLES",
    "USD": "DOLARES", "DOLARES": "DOLARES", "US$": "DOLARES", "$": "DOLARES",
    "EUR": "EUROS", "EUROS": "EUROS", "€": "EUROS",
}


def _monedas_compatibles(a, b):
    """No se cruzan monedas distintas; si falta el dato, no se descarta."""
    na, nb = str(a or "").strip().upper(), str(b or "").strip().upper()
    if not na or not nb:
        return True
    return _MONEDAS.get(na, na) == _MONEDAS.get(nb, nb)


def _ref_norm(valor):
    """N° de operación/referencia comparable (mínimo 4 caracteres útiles)."""
    txt = _texto_compacto(valor)
    return txt if len(txt) >= 4 else ""


def _refs_compatibles(a, b):
    """N° de operación: igual, o uno es el sufijo del otro (el banco recorta)."""
    na, nb = _texto_compacto(a), _texto_compacto(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 4 and nb.endswith(na):
        return True
    if len(nb) >= 4 and na.endswith(nb):
        return True
    return False


def _acepta_comision(diferencia, ref_ok, comision, tope_comision):
    """¿La diferencia de importe se explica por la comisión del banco?

    - Si hay comisión configurada, la diferencia tiene que ser exactamente esa.
    - Si no hay comisión configurada pero el N° de operación coincide, se
      acepta una diferencia pequeña (la comisión que cobra el banco en la
      misma línea) y SIEMPRE se propone para aprobar, nunca automática.
    """
    try:
        diferencia = float(diferencia)
        comision = float(comision or 0)
        tope = float(tope_comision or 0)
    except Exception:
        return False
    if comision > 0 and abs(diferencia - comision) <= 0.02:
        return True
    if ref_ok and diferencia <= max(0.0, tope) + 0.02:
        return True
    return False


def _clasificar(nivel, dias):
    if nivel == 4:
        return "CONCILIADO AGRUPADO"
    if dias == 0:
        return "CONCILIADO EXACTO"
    return "CONCILIADO CON DESFASE"


def _indexar_por_importe(lista):
    """{(importe, sentido): [fechas]} para medir si un importe se repite."""
    indice = {}
    for item in lista:
        fecha = item.get("fecha")
        if fecha is None or item.get("importe", 0) <= 0:
            continue
        indice.setdefault((item["importe"], item["sentido"]), []).append(fecha)
    return indice


def _cerca(fechas, fecha, ventana_dias, excluir=None):
    """Cuántas fechas de la lista caen dentro de la ventana (sin contar 'excluir')."""
    total = 0
    for otra in fechas:
        if excluir is not None and otra == excluir:
            excluir = None      # solo se descarta una vez (el propio candidato)
            continue
        if abs((otra - fecha).days) <= ventana_dias:
            total += 1
    return total


def proponer(movimientos_banco, asientos, ventana_dias=DIAS_DESFASE_POR_DEFECTO,
             tolerancia=TOLERANCIA_IMPORTE, comision=0.0, tope_comision=10.0,
             es_banco_app_grifo=False, desfase_app_grifo=0,
             tolerancia_app_grifo=1, rechazos=None):
    """Devuelve las propuestas de conciliación (sin escribir nada).

    Reglas aprobadas:
    • Nivel 1 (bancos sin App Grifo): misma fecha + mismo importe + único; si el
      importe se repite ese día, se usa el N° de operación para desempatar.
    • Nivel 2 (solo el banco configurado de App Grifo en Flota): fecha del asiento
      + desfase configurado = fecha del banco, mismo importe y único. No exige
      N° de operación (Interbank no lo imprime).
    • La tolerancia ± del App Grifo solo genera sugerencia, nunca automático.
    • `rechazos` es un set de (tabla, id_asiento, fecha_banco, monto_banco) que
      el usuario deshizo y no debe volver a proponerse solo.
    """
    try:
        ventana_dias = max(0, int(ventana_dias))
    except Exception:
        ventana_dias = DIAS_DESFASE_POR_DEFECTO
    try:
        desfase_app_grifo = max(0, int(desfase_app_grifo or 0))
    except Exception:
        desfase_app_grifo = 0
    try:
        tolerancia_app_grifo = max(0, int(tolerancia_app_grifo or 0))
    except Exception:
        tolerancia_app_grifo = 0
    rechazos = set(rechazos or ())
    if es_banco_app_grifo:
        ventana_dias = max(ventana_dias, desfase_app_grifo + tolerancia_app_grifo)

    movs, asis = [], []
    for i, m in enumerate(movimientos_banco):
        movs.append({
            "i": i, "fecha": normalizar_fecha(m.get("fecha")),
            "importe": normalizar_monto(m.get("monto")),
            "sentido": _sentido(m.get("monto"), m.get("descripcion")),
            "banco": m.get("banco"), "cuenta": m.get("cuenta"),
            "moneda": m.get("moneda"),
            "ref": _ref_norm(m.get("referencia") or m.get("operacion")),
            "desc_digitos": _solo_digitos(m.get("descripcion")),
            "m": m,
        })
    for j, a in enumerate(asientos):
        asis.append({
            "i": j, "fecha": normalizar_fecha(a.get("fecha")),
            "importe": normalizar_monto(a.get("monto")),
            "sentido": _sentido(a.get("monto"), a.get("descripcion")),
            "banco": a.get("banco"), "cuenta": a.get("cuenta"),
            "moneda": a.get("moneda"),
            "ref": _ref_norm(a.get("referencia") or a.get("operacion")),
            "m": a,
        })

    movs_por_importe = _indexar_por_importe(movs)
    asis_por_importe = _indexar_por_importe(asis)

    candidatos = []
    for mv in movs:
        if mv["fecha"] is None or mv["importe"] <= 0:
            continue
        for as_ in asis:
            if as_["fecha"] is None or as_["importe"] <= 0:
                continue
            if not _bancos_compatibles(mv["banco"], as_["banco"]):
                continue                            # 🚫 bancos distintos
            if not _cuentas_compatibles(mv["cuenta"], as_["cuenta"]):
                continue                            # 🚫 cuentas distintas
            if not _monedas_compatibles(mv["moneda"], as_["moneda"]):
                continue                            # 🚫 monedas distintas
            if mv["sentido"] != as_["sentido"]:
                continue                            # 🚫 ingreso con egreso
            dias = abs((mv["fecha"] - as_["fecha"]).days)
            if dias > ventana_dias:
                continue                            # 🚫 fuera de la ventana
            # 🚫 Pares que el usuario deshizo: no se vuelven a proponer solos.
            if rechazos:
                _clave_r = (
                    str(mv["m"].get("banco") or ""),
                    mv["fecha"].strftime("%Y-%m-%d") if mv["fecha"] else "",
                    round(float(mv["importe"]), 2),
                    as_["fecha"].strftime("%Y-%m-%d") if as_["fecha"] else "",
                    round(float(as_["importe"]), 2))
                if _clave_r in rechazos:
                    continue
            # 🔎 N° de operación: igual en los dos lados, o impreso dentro de la
            #    descripción del banco (el PDF imprime los últimos dígitos).
            ref_ok = _refs_compatibles(mv["ref"], as_["ref"])
            if not ref_ok and as_["ref"] and mv["desc_digitos"]:
                sufijo = as_["ref"][-6:]
                ref_ok = (as_["ref"] in mv["desc_digitos"]
                          or (len(sufijo) >= 5 and sufijo in mv["desc_digitos"]))
            diferencia = round(abs(mv["importe"] - as_["importe"]), 2)
            con_comision = False
            if diferencia > tolerancia:
                # 🚫 Importe distinto: solo pasa si es la comisión del banco.
                if not _acepta_comision(diferencia, ref_ok, comision, tope_comision):
                    continue
                con_comision = True
            clave = (mv["importe"], mv["sentido"])
            # ¿El mismo importe se repite ESE MISMO DÍA? (regla Nivel 1)
            dup_dia_sis = sum(
                1 for f in asis_por_importe.get(clave, []) if f == mv["fecha"]) - 1
            dup_dia_banco = sum(
                1 for f in movs_por_importe.get(clave, []) if f == as_["fecha"]) - 1
            # ¿El mismo importe se repite del otro lado dentro de la ventana?
            rivales_sis = _cerca(asis_por_importe.get(clave, []), mv["fecha"],
                                 ventana_dias, excluir=as_["fecha"])
            rivales_banco = _cerca(movs_por_importe.get(clave, []), as_["fecha"],
                                   ventana_dias, excluir=mv["fecha"])
            candidatos.append({
                "mv": mv, "as": as_, "dias": dias, "ref_ok": ref_ok,
                "con_comision": con_comision, "diferencia": diferencia,
                "sim": similitud_nombres(mv["m"].get("descripcion"),
                                         as_["m"].get("descripcion")),
                "rivales_sis": rivales_sis, "rivales_banco": rivales_banco,
                "dup_dia_sis": dup_dia_sis, "dup_dia_banco": dup_dia_banco,
            })

    # 🔎 N° de operación único a cada lado (para desempatar sin arbitrariedad).
    refs_mov, refs_as = {}, {}
    for c in candidatos:
        if c["ref_ok"]:
            refs_mov[c["mv"]["i"]] = refs_mov.get(c["mv"]["i"], 0) + 1
            refs_as[c["as"]["i"]] = refs_as.get(c["as"]["i"], 0) + 1

    # 🔢 Puntaje: el importe ya coincide (50); suma la fecha, la descripción y
    #    la referencia. Con cualquier rival el puntaje se topa en 60 (AMBIGUO).
    for c in candidatos:
        puntos = 50
        if c["dias"] == 0:
            puntos += 25
        elif c["dias"] <= 2:
            puntos += 20
        elif c["dias"] <= 4:
            puntos += 12
        else:
            puntos += 8
        puntos += int(round(min(1.0, max(0.0, c["sim"])) * 20))
        if c["ref_ok"]:
            puntos += 10
        if c["rivales_sis"] or c["rivales_banco"]:
            puntos = min(puntos, 60)
        c["ref_unica"] = bool(
            c["ref_ok"]
            and refs_mov.get(c["mv"]["i"], 0) == 1
            and refs_as.get(c["as"]["i"], 0) == 1)
        if es_banco_app_grifo:
            # Nivel 2: solo el banco App Grifo, fecha + desfase exactos.
            c["seguro"] = bool(
                not c["con_comision"]
                and c["diferencia"] <= tolerancia
                and c["dias"] == desfase_app_grifo
                and not c["rivales_sis"] and not c["rivales_banco"])
        else:
            # Nivel 1: misma fecha + mismo importe. Si el importe se repite,
            # solo el N° de operación único permite el automático.
            c["seguro"] = bool(
                not c["con_comision"]
                and c["diferencia"] <= tolerancia
                and c["dias"] == 0
                and ((not c["dup_dia_sis"] and not c["dup_dia_banco"])
                     or c["ref_unica"]))
        if c["con_comision"]:
            # Con diferencia de importe NUNCA es automático: se propone.
            c["puntaje"] = 90 if c["ref_ok"] else 85
        else:
            c["puntaje"] = 100 if c["seguro"] else min(99, puntos)

    # Se asignan primero las coincidencias seguras y después las de mayor puntaje.
    candidatos.sort(key=lambda c: (0 if c["seguro"] else 1, -c["puntaje"],
                                   c["dias"], -c["sim"], c["as"]["i"]))

    por_mov = {}
    for c in candidatos:
        por_mov.setdefault(c["mv"]["i"], []).append(c)

    usados_banco, usados_as, propuestas = set(), set(), []
    for c in candidatos:
        mv, as_ = c["mv"], c["as"]
        if mv["i"] in usados_banco or as_["i"] in usados_as:
            continue
        rivales = max(c["rivales_sis"], c["rivales_banco"])
        if c["con_comision"]:
            estado = "CONCILIADO CON DIFERENCIA"
            confianza = c["puntaje"]
            if rivales:
                confianza = min(confianza, 75)
        elif c["seguro"]:
            estado = _clasificar(1, c["dias"])
            confianza = 100
        elif rivales:
            estado = "AMBIGUO"
            confianza = min(c["puntaje"], 60)
        else:
            estado = _clasificar(2 if c["dias"] else 1, c["dias"])
            confianza = c["puntaje"]

        observacion = ""
        if rivales:
            observacion = ("El importe se repite %d vez/veces en el período: "
                           "revise cuál corresponde." % rivales)
        if c["con_comision"]:
            extra = ("Diferencia de %s entre el banco y el sistema: puede ser la "
                     "comisión del banco o un redondeo. Revise el comprobante antes "
                     "de aprobar." % ("%.2f" % c["diferencia"]))
            observacion = (observacion + " " + extra).strip()
        propuestas.append({
            "i_banco": mv["i"], "i_asiento": as_["i"],
            "id_movimiento_banco": mv["m"].get("id"),
            "id_asiento": as_["m"].get("id"),
            "banco": mv["m"].get("banco"), "cuenta": mv["m"].get("cuenta", ""),
            "fecha_contable": as_["m"].get("fecha"), "fecha_banco": mv["m"].get("fecha"),
            "dias_desfase": c["dias"],
            "descripcion_contable": as_["m"].get("descripcion"),
            "documento_contable": as_["m"].get("documento", ""),
            "descripcion_banco": mv["m"].get("descripcion"),
            "importe_contable": as_["importe"], "importe_banco": mv["importe"],
            "diferencia": c["diferencia"],
            "sentido": mv["sentido"],
            "referencia": mv["m"].get("referencia", "") or mv["m"].get("operacion", ""),
            "origen": as_["m"].get("origen", ""),
            "id_movimiento": as_["m"].get("id_movimiento", 0),
            "id_gasto_compras": as_["m"].get("id_gasto_compras", 0),
            "id_factura": as_["m"].get("id_factura", 0),
            "pagina_pdf": mv["m"].get("pagina"),
            "tabla_asiento": str(as_["m"].get("tabla") or ""),
            "ref_ok": c["ref_ok"],
            "ref_unica": c["ref_unica"],
            "nivel": (2 if (es_banco_app_grifo and c["seguro"]) else
                      (1 if (c["seguro"] and c["dias"] == 0) else
                       (3 if c["ref_ok"] else (2 if c["con_comision"] else 2)))),
            "estado": estado,
            "confianza": confianza,
            "rivales": rivales,
            "candidatos": rivales + 1,
            "candidatos_detalle": [
                {"id_asiento": otro["as"]["m"].get("id"),
                 "descripcion": otro["as"]["m"].get("descripcion"),
                 "fecha": otro["as"]["m"].get("fecha"),
                 "tabla": otro["as"]["m"].get("tabla", ""),
                 "dias": otro["dias"]}
                for otro in por_mov.get(mv["i"], [c])
            ],
            "reglas": ("importe %s; mismo banco, cuenta y moneda; sentido igual; "
                       "desfase %d día(s); misma fecha: %s; importe único: %s%s"
                       % ("+ comisión" if c["con_comision"] else "exacto",
                          c["dias"], "sí" if c["dias"] == 0 else "no",
                          "sí" if not rivales else "no",
                          "; N° de operación coincide" if c["ref_ok"] else "")),
            "observacion": observacion,
            "accion_sugerida": ("Revisar la comisión y aprobar" if c["con_comision"]
                                else ("Revisar y aprobar" if not rivales
                                      else "Elegir el movimiento correcto")),
        })
        usados_banco.add(mv["i"])
        usados_as.add(as_["i"])

    propuestas.sort(key=lambda p: (normalizar_fecha(p["fecha_banco"]) or datetime.date.min,
                                   p["importe_banco"]))
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
        if p.get("diferencia", 0) > 0.005:
            grupos["G"].append(p)
        elif estado == "AMBIGUO" or p["confianza"] < 60:
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
