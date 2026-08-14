# -*- coding: utf-8 -*-
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

def generar_pdf_interactivo_proveedor():
    nombre_archivo = "Ficha_Registro_Proveedor.pdf"
    c = canvas.Canvas(nombre_archivo, pagesize=letter)
    form = c.acroForm
    
    # 🚀 CONTROL DE LOGOTIPO
    ruta_logo = "logo.png"
    if os.path.exists(ruta_logo):
        c.drawImage(ruta_logo, 40, 735, width=45, height=45, mask='auto')
        
    # --- ENCABEZADO PRINCIPAL ESTILIZADO ---
    c.setFont("Helvetica-Bold", 13)
    c.drawString(95, 755, "FICHA OFICIAL DE REGISTRO E INCORPORACION DE PROVEEDORES")
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawString(95, 742, "Por favor, complete todos los campos interactivos exactamente como se solicita para la integracion automatica.")
    
    c.setLineWidth(1)
    c.line(40, 725, 570, 725)
    
    # === SECCIÓN 1: DATOS PRINCIPALES Y CATEGORÍAS ===
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, 705, "1. Informacion de Identificacion y Categoria")
    
    c.setFont("Helvetica", 9.5)
    
    # Fila 1: RUC y Razón Social
    c.drawString(40, 680, "Numero de RUC:")
    form.textfield(name="ruc", tooltip="RUC de la empresa (11 digitos)", x=130, y=675, width=120, height=16, fontSize=9.5)
    
    c.drawString(260, 680, "Nombre / Razon Social:")
    form.textfield(name="razon_social", tooltip="Nombre o Razon Social", x=380, y=675, width=190, height=16, fontSize=9.5)
    
    lista_categorias = [
        "Seleccione una opcion",
        "Luces", "Estructuras", "Sonido", "Video", "Generadores",
        "Catering", "Bebidas", "Menaje", "Mobiliario", "Manteleria",
        "Decoracion", "Impresiones", "Merchandising",
        "Personal", "Seguridad", "Movilidad", "Fotografia", "Artistas",
        "Otros"
    ]
    
    # Fila 2: Categoría 1 y 2
    c.drawString(40, 655, "Categoria Principal:")
    form.choice(name="categoria", tooltip="Seleccione el rubro principal", value="Seleccione una opcion", options=lista_categorias, x=150, y=650, width=130, height=16, fontSize=9)
    
    c.drawString(300, 655, "Categoria Adic. 2:")
    form.choice(name="categoria2", tooltip="Seleccione un segundo rubro (Opcional)", value="Seleccione una opcion", options=lista_categorias, x=400, y=650, width=130, height=16, fontSize=9)

    # Fila 3: Categoría 3 y 4
    c.drawString(40, 630, "Categoria Adic. 3:")
    form.choice(name="categoria3", tooltip="Seleccione un tercer rubro (Opcional)", value="Seleccione una opcion", options=lista_categorias, x=150, y=625, width=130, height=16, fontSize=9)
    
    c.drawString(300, 630, "Categoria Adic. 4:")
    form.choice(name="categoria4", tooltip="Seleccione un cuarto rubro (Opcional)", value="Seleccione una opcion", options=lista_categorias, x=400, y=625, width=130, height=16, fontSize=9)

    # Fila 4: Categoría 5 y "Otros"
    c.drawString(40, 605, "Categoria Adic. 5:")
    form.choice(name="categoria5", tooltip="Seleccione un quinto rubro (Opcional)", value="Seleccione una opcion", options=lista_categorias, x=150, y=600, width=130, height=16, fontSize=9)
    
    c.drawString(300, 605, "Especifique 'Otros':")
    form.textfield(name="especifique_otros", tooltip="Escriba rubro si marco Otros", x=400, y=600, width=170, height=16, fontSize=9.5)
    
    # Fila 5: Descripción Comercial
    c.drawString(40, 575, "Descripcion Proveedor:\n(Max 400 carac.)")
    form.textfield(name="descripcion_proveedor", tooltip="Resumen o descripcion comercial", x=165, y=540, width=405, height=40, fontSize=9)
    
    c.line(40, 520, 570, 520)
    
    # === SECCIÓN 2: CONTACTOS Y ENLACES ===
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, 500, "2. Informacion de Contacto, Redes y Ubicacion")
    
    contact_fields_left = [
        ("Nombre Contacto 1:", "contacto_principal", 475),
        ("Nombre Contacto 2:", "contacto_alternativo", 450),
        ("Correo Electronico:", "correo", 425),
        ("Link Web:", "link_web", 400),
        ("Enlace Catalogo:", "enlace_catalogo", 375)
    ]
    for label, name, y in contact_fields_left:
        c.setFont("Helvetica", 9.5)
        c.drawString(40, y, label)
        form.textfield(name=name, tooltip=label, x=150, y=y-4, width=150, height=16, fontSize=9)

    contact_fields_right = [
        ("WhatsApp Principal:", "whatsapp_principal", 475),
        ("WhatsApp Alternativo:", "whatsapp_alternativo", 450),
        ("Zona / Distrito Especifico:", "zona_distrito", 425)
    ]
    for label, name, y in contact_fields_right:
        c.setFont("Helvetica", 9.5)
        c.drawString(320, y, label)
        form.textfield(name=name, tooltip=label, x=440, y=y-4, width=130, height=16, fontSize=9)
        
    c.line(40, 355, 570, 355)
    
    # === SECCIÓN 3: INFORMACIÓN FINANCIERA Y DETRACCIONES ===
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, 335, "3. Informacion Financiera y Estructura de Detracciones")
    
    lista_bancos_peru = [
        "Seleccione Banco",
        "BCP", "BBVA", "Interbank", "Scotiabank", 
        "Banco de la Nacion", "BanBif", "Banco Pichincha", 
        "MiBanco", "Banco GNB", "Banco Falabella", 
        "Banco Ripley", "Santander", "Otros"
    ]
    
    # --- CUENTA PRINCIPAL ---
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(40, 310, "CUENTA PRINCIPAL")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, 290, "Banco Principal:")
    form.choice(name="banco_1", tooltip="Seleccione Banco Principal", value="Seleccione Banco", options=lista_bancos_peru, x=140, y=285, width=110, height=16, fontSize=9)
    c.drawString(255, 290, "N° Cuenta:")
    form.textfield(name="cuenta_1", tooltip="Numero de cuenta 1", x=315, y=285, width=110, height=16, fontSize=9)
    c.drawString(435, 290, "CCI:")
    form.textfield(name="cci_1", tooltip="CCI cuenta 1", x=465, y=285, width=105, height=16, fontSize=9)
    
    # --- CUENTA SECUNDARIA ---
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(40, 255, "CUENTA SECUNDARIA (OPCIONAL)")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, 235, "Banco Secundario:")
    form.choice(name="banco_2", tooltip="Seleccione Banco Secundario", value="Seleccione Banco", options=lista_bancos_peru, x=140, y=230, width=110, height=16, fontSize=9)
    c.drawString(255, 235, "N° Cuenta:")
    form.textfield(name="cuenta_2", tooltip="Numero de cuenta 2", x=315, y=230, width=110, height=16, fontSize=9)
    c.drawString(435, 235, "CCI:")
    form.textfield(name="cci_2", tooltip="CCI cuenta 2", x=465, y=230, width=105, height=16, fontSize=9)
    
    # --- SISTEMA DE DETRACCIONES ---
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(40, 200, "SISTEMA DE DETRACCIONES (BANCO DE LA NACION)")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, 180, "Cuenta Detraccion N°:")
    form.textfield(name="cuenta_detraccion", tooltip="N° Cuenta de Detracciones", x=150, y=175, width=160, height=16, fontSize=9)
    c.drawString(330, 180, "Porcentaje Detraccion (%):")
    form.textfield(name="porcentaje_detraccion", tooltip="Porcentaje de detraccion aplicable", x=460, y=175, width=110, height=16, fontSize=9)
    
    c.line(40, 150, 570, 150)
    
    # Instrucciones de Envío Finales
    c.setFont("Helvetica-BoldOblique", 9)
    c.drawString(40, 130, "Nota importante de validacion:")
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(40, 115, "Una vez completado, guarde el archivo PDF conservando los campos interactivos rellenados.")
    c.drawString(40, 102, "No escanee ni imprima este documento fisico; el sistema lo leera electronicamente en segundos.")
    
    c.save()
    print("Ficha interactiva oficial 'Ficha_Registro_Proveedor.pdf' generada con éxito sin la dirección fiscal y con campos de categorías alineados.")

if __name__ == "__main__":
    generar_pdf_interactivo_proveedor()