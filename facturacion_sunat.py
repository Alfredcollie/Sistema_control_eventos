# -*- coding: utf-8 -*-
import json
import os
import sys
import urllib.request
import urllib.error
import threading
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from datetime import datetime
import webbrowser
import shutil

from conexion import conectar_db, registrar_auditoria

def obtener_configuracion_fe():
    config = {
        "proveedor_fe": "Nubefact",
        "url_api_fe": "",
        "token_api_fe": "",
        "ruc_empresa": "",
        "razon_social_empresa": "",
        "ruta_drive": ""
    }
    if os.path.exists("config_local.json"):
        try:
            with open("config_local.json", "r", encoding="utf-8") as f:
                config.update(json.load(f))
        except Exception:
            pass
    return config

def construir_payload_nubefact(datos_factura):
    tipo_doc = datos_factura.get("tipo_doc", "Factura")
    codigo_tipo_comprobante = 1 if "Factura" in tipo_doc else 2 
    
    doc_cliente = datos_factura.get("ruc_dni_cliente", "").strip()
    tipo_doc_cliente = 6 if len(doc_cliente) == 11 else (1 if len(doc_cliente) == 8 else 0)

    subtotal = float(datos_factura.get("subtotal", 0.0))
    igv = float(datos_factura.get("impuesto", 0.0))
    total = float(datos_factura.get("total", 0.0))
    
    moneda_str = datos_factura.get("moneda", "Soles")
    codigo_moneda = 1 if "Sol" in moneda_str else 2 

    serie_original = datos_factura.get("serie", "F001" if codigo_tipo_comprobante == 1 else "B001")
    numero_original = int(datos_factura.get("numero", 1))

    tipo_emision = datos_factura.get("tipo_emision", "factura")

    items = [
        {
            "unidad_de_medida": "ZZ", 
            "codigo": "SERV01",
            "descripcion": datos_factura.get("descripcion", "Servicios Generales"),
            "cantidad": 1,
            "valor_unitario": round(subtotal, 2),
            "precio_unitario": round(total, 2),
            "subtotal": round(subtotal, 2),
            "tipo_de_igv": 1, 
            "igv": round(igv, 2),
            "total": round(total, 2)
        }
    ]

    # 🚀 SI ES UNA NOTA DE CRÉDITO FUERZA LA SERIE FC01 o BC01
    if tipo_emision == "nota_credito":
        serie_nc = "FC01" if codigo_tipo_comprobante == 1 else "BC01"
        motivo_str = datos_factura.get("motivo_nc", "")
        
        tipo_nc = 1 
        if "RUC" in motivo_str: tipo_nc = 2
        elif "Devolución total" in motivo_str: tipo_nc = 7
        elif "ítem" in motivo_str: tipo_nc = 6

        return {
            "operacion": "generar_comprobante",
            "tipo_de_comprobante": 3, 
            "serie": serie_nc,
            "numero": numero_original, 
            "sunat_transaction": 1,
            "cliente_tipo_de_documento": tipo_doc_cliente,
            "cliente_numero_de_documento": doc_cliente,
            "cliente_denominacion": datos_factura.get("nombre_cliente", "CLIENTE GENERAL"),
            "cliente_direccion": datos_factura.get("direccion_cliente", "-"),
            "cliente_email": datos_factura.get("correo_cliente", ""),
            "fecha_de_emision": datos_factura.get("fecha_emision", datetime.now().strftime("%Y-%m-%d")),
            "moneda": codigo_moneda,
            "porcentaje_de_igv": 18.0,
            "total_igv": round(igv, 2),
            "total_gravada": round(subtotal, 2),
            "total": round(total, 2),
            "documento_que_se_modifica_tipo": codigo_tipo_comprobante,
            "documento_que_se_modifica_serie": serie_original,
            "documento_que_se_modifica_numero": numero_original,
            "tipo_de_nota_de_credito": tipo_nc,
            "items": items
        }

    return {
        "operacion": "generar_comprobante",
        "tipo_de_comprobante": codigo_tipo_comprobante,
        "serie": serie_original,
        "numero": numero_original,
        "sunat_transaction": 1,
        "cliente_tipo_de_documento": tipo_doc_cliente,
        "cliente_numero_de_documento": doc_cliente,
        "cliente_denominacion": datos_factura.get("nombre_cliente", "CLIENTE GENERAL"),
        "cliente_direccion": datos_factura.get("direccion_cliente", "-"),
        "cliente_email": datos_factura.get("correo_cliente", ""),
        "fecha_de_emision": datos_factura.get("fecha_emision", datetime.now().strftime("%Y-%m-%d")),
        "moneda": codigo_moneda,
        "porcentaje_de_igv": 18.0,
        "total_igv": round(igv, 2),
        "total_gravada": round(subtotal, 2),
        "total": round(total, 2),
        "items": items
    }

def enviar_factura_sunat(datos_factura):
    cfg = obtener_configuracion_fe()
    proveedor = cfg.get("proveedor_fe", "Nubefact")
    url_api = cfg.get("url_api_fe", "").strip()
    token_api = cfg.get("token_api_fe", "").strip()

    if not url_api or not token_api:
        return False, "⚠️ No ha configurado la Ruta API o el Token en los Ajustes del Sistema.", "", ""

    payload = construir_payload_nubefact(datos_factura)
    auth_header = f"Bearer {token_api}"

    json_data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url_api, data=json_data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", auth_header)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            
            if "errors" in res_json and res_json["errors"]:
                return False, f"Error devuelto por {proveedor}: {res_json['errors']}", "", ""
            
            pdf_link = res_json.get("enlace_del_pdf", res_json.get("pdf", res_json.get("urlPdf", "")))
            xml_link = res_json.get("enlace_del_xml", res_json.get("xml", res_json.get("urlXml", "")))
            sunat_description = res_json.get("sunat_description") or "Comprobante procesado exitosamente por SUNAT."

            tipo_emision = datos_factura.get("tipo_emision", "factura")
            id_factura = datos_factura.get("id_factura_bd")
            
            if id_factura:
                conn = conectar_db()
                if conn:
                    try:
                        cursor = conn.cursor()
                        if tipo_emision == "nota_credito":
                            # 🚀 GUARDAMOS EL ENLACE DEL PDF DE LA NOTA DE CRÉDITO
                            cursor.execute("UPDATE facturas_emitidas SET estado_sunat = %s, enlace_pdf_nc = %s WHERE id = %s", 
                                           ("❌ Anulada (Nota de Crédito Emitida)", pdf_link, id_factura))
                            cursor.execute("DELETE FROM pagos_clientes WHERE id_factura = %s", (id_factura,))
                        else:
                            cursor.execute("UPDATE facturas_emitidas SET estado_sunat = %s, enlace_pdf_sunat = %s, enlace_xml_sunat = %s WHERE id = %s", 
                                           (sunat_description, pdf_link, xml_link, id_factura))
                        conn.commit()
                    except Exception: pass
                    finally: conn.close()

            res_resumen = (
                f"✅ ENVIADO EXITOSAMENTE A TRAVÉS DE {proveedor.upper()}\n\n"
                f"• Mensaje del Servidor: {sunat_description}\n"
                f"• PDF Oficial: {pdf_link}\n"
                f"• XML: {xml_link}\n"
            )
            return True, res_resumen, pdf_link, ""

    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            err_json = json.loads(err_body)
            err_msg = err_json.get("errors", err_json.get("message", str(e)))
            return False, f"Error HTTP {e.code} en {proveedor}:\n{err_msg}", "", ""
        except Exception:
            return False, f"Error de servidor {proveedor} (HTTP {e.code}): {e.reason}", "", ""
    except Exception as e:
        return False, f"Fallo inesperado durante la transmisión: {e}", "", ""

def abrir_ventana_emision_sunat(parent_window, datos_factura, usuario_activo="Desconocido", callback_exito=None):
    v_sunat = ctk.CTkToplevel(parent_window)
    v_sunat.title("Facturación Electrónica SUNAT")
    v_sunat.geometry("520x450")
    v_sunat.grab_set()
    v_sunat.resizable(False, False)

    cfg = obtener_configuracion_fe()
    proveedor_actual = cfg.get("proveedor_fe", "Nubefact")
    
    tipo_emision = datos_factura.get("tipo_emision", "factura")
    titulo_ventana = "⚡ EMISIÓN DIRECTA A SUNAT" if tipo_emision == "factura" else "⚠️ EMITIR NOTA DE CRÉDITO"
    color_titulo = "#1f538d" if tipo_emision == "factura" else "#c0392b"
    
    doc_display = f"{datos_factura.get('tipo_doc', 'Factura')} {datos_factura.get('serie', 'F001')}-{datos_factura.get('numero', 1)}"
    
    if tipo_emision == "nota_credito":
        serie_nc = "FC01" if "Factura" in datos_factura.get('tipo_doc', 'Factura') else "BC01"
        doc_display = f"NOTA DE CRÉDITO ({serie_nc}-{datos_factura.get('numero', 1)})"

    ctk.CTkLabel(v_sunat, text=titulo_ventana, font=("Arial", 14, "bold"), text_color=color_titulo).pack(pady=(20, 10))

    f_info = ctk.CTkFrame(v_sunat, fg_color="#f8f9fa", border_width=1, border_color="#e0e0e0")
    f_info.pack(fill="x", padx=25, pady=10, ipadx=10, ipady=10)

    ctk.CTkLabel(f_info, text=f"Documento: {doc_display}", font=("Arial", 12, "bold")).pack(anchor="w")
    ctk.CTkLabel(f_info, text=f"Cliente: {datos_factura.get('nombre_cliente', '-')}", font=("Arial", 11)).pack(anchor="w")
    ctk.CTkLabel(f_info, text=f"RUC/DNI: {datos_factura.get('ruc_dni_cliente', '-')}", font=("Arial", 11)).pack(anchor="w")
    
    if tipo_emision == "nota_credito":
        ctk.CTkLabel(f_info, text=f"Motivo SUNAT: {datos_factura.get('motivo_nc', '-')}", font=("Arial", 11, "italic"), text_color="#c0392b").pack(anchor="w")
    else:
        ctk.CTkLabel(f_info, text=f"Monto Total: S/. {float(datos_factura.get('total', 0)):,.2f}", font=("Arial", 12, "bold"), text_color="#27ae60").pack(anchor="w", pady=(5, 0))

    lbl_estado = ctk.CTkLabel(v_sunat, text=f"Estado: Listo para transmitir por {proveedor_actual}", font=("Arial", 11, "italic"), text_color="gray")
    lbl_estado.pack(pady=10)

    txt_resultado = ctk.CTkTextbox(v_sunat, height=120, font=("Arial", 11))
    txt_resultado.pack(fill="x", padx=25, pady=5)

    btn_enviar = ctk.CTkButton(v_sunat, text=f"⚡ Transmitir ahora", font=("Arial", 13, "bold"), height=40, fg_color=color_titulo, hover_color="#163b65")
    btn_enviar.pack(pady=15)

    def actualizar_ui_y_abrir(ok, respuesta, link_pdf, ruta_local):
        btn_enviar.configure(state="normal", text="⚡ Reintentar Transmisión")
        if ok:
            lbl_estado.configure(text="✅ Documento Procesado Exitosamente", text_color="#27ae60")
            txt_resultado.delete("1.0", tk.END)
            txt_resultado.insert("1.0", respuesta)
            btn_enviar.configure(state="disabled", text="Proceso Finalizado")
            
            if link_pdf and link_pdf.startswith("http"):
                webbrowser.open(link_pdf)
                
            accion_auditoria = "Anulación (Nota de Crédito)" if tipo_emision == "nota_credito" else "Emisión Electrónica"
            registrar_auditoria(usuario_activo, "Facturación SUNAT", f"Generó {accion_auditoria} para el comprobante {datos_factura.get('serie')}-{datos_factura.get('numero')}")
            
            if callback_exito:
                callback_exito()
        else:
            lbl_estado.configure(text="❌ Error en la Transmisión", text_color="#c0392b")
            txt_resultado.delete("1.0", tk.END)
            txt_resultado.insert("1.0", respuesta)

    def proceso_envio_hilo():
        resultado = enviar_factura_sunat(datos_factura)
        v_sunat.after(0, actualizar_ui_y_abrir, resultado[0], resultado[1], resultado[2], resultado[3])

    def iniciar_transmision():
        btn_enviar.configure(state="disabled", text="⏳ Transmitiendo...")
        lbl_estado.configure(text=f"Procesando en servidores de {proveedor_actual}...", text_color="#d35400")
        threading.Thread(target=proceso_envio_hilo, daemon=True).start()

    btn_enviar.configure(command=iniciar_transmision)