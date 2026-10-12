# -*- coding: utf-8 -*-
"""
ESCANER.PY - Escaneo de facturas con la multifuncional (WIA) + OCR, para Compras.
================================================================================
Sin dependencias nuevas: usa el escaneo WIA y el OCR que ya trae Windows
(powershell.exe). Si además está instalado Tesseract se puede usar como motor OCR.

La configuración vive en Configuración General (claves que empiezan con
"escaner_") y se lee con config_escaner().

Flujo completo (procesar_factura):
    1. escanear()          -> imágenes JPG de cada página
    2. _optimizar_imagen() -> reduce el peso de cada imagen
    3. imagenes_a_pdf()    -> arma el PDF que se adjunta a la factura
    4. ocr_paginas()       -> texto reconocido (Windows OCR o Tesseract)
"""
import os
import sys
import json
import time
import shutil
import subprocess
import tempfile
from datetime import datetime

# =========================================================
# OPCIONES QUE SE MUESTRAN EN CONFIGURACIÓN GENERAL
# =========================================================
OPCIONES_DPI = ["200", "300", "400", "600"]
OPCIONES_COLOR = ["Escala de grises", "Color", "Blanco y negro"]
OPCIONES_ORIGEN = ["Cristal (una página a la vez)", "Alimentador (varias páginas)", "Automático"]
OPCIONES_PAGINAS = ["1", "2", "3", "5", "10", "20"]
OPCIONES_OCR = ["Sí", "No"]
OPCIONES_IDIOMA_OCR = ["Automático", "Español", "Inglés", "Español + Inglés"]
OPCIONES_OPTIMIZAR = ["Sí", "No"]
OPCIONES_MODO = ["Automático (sin ventanas)", "Con la ventana de Windows"]

CONFIG_DEFECTO = {
    "escaner_dispositivo": "",              # id WIA del escáner (propio de cada equipo)
    "escaner_dpi": "300",
    "escaner_color": "Escala de grises",
    "escaner_origen": "Cristal (una página a la vez)",
    "escaner_paginas": "1",
    "escaner_ocr": "Sí",
    "escaner_idioma_ocr": "Automático",
    "escaner_optimizar": "Sí",
    "escaner_modo": "Automático (sin ventanas)",   # o "Con la ventana de Windows"
    "escaner_carpeta": "",                  # vacío = carpeta de Drive del sistema
}

_INTENT_WIA = {"Color": 1, "Escala de grises": 2, "Blanco y negro": 4}
_ORIGEN_WIA = {"Cristal (una página a la vez)": 1, "Alimentador (varias páginas)": 2, "Automático": 0}


def es_windows():
    return sys.platform == "win32"


def _powershell():
    """Ruta del PowerShell de Windows (el de siempre: powershell.exe)."""
    for nombre in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        ruta = shutil.which(nombre)
        if ruta:
            return ruta
    return "powershell.exe"


def config_escaner(config=None):
    """Configuración del escáner ya normalizada (con los valores por defecto)."""
    datos = dict(CONFIG_DEFECTO)
    if isinstance(config, dict):
        for clave in CONFIG_DEFECTO:
            valor = config.get(clave)
            if valor not in (None, ""):
                datos[clave] = str(valor)
    return datos


def carpeta_escaneos(config=None):
    """Carpeta donde se guardan los escaneos (Drive si está configurado)."""
    datos = config_escaner(config)
    base = str(datos.get("escaner_carpeta") or "").strip()
    if not base and isinstance(config, dict):
        base = str(config.get("ruta_drive") or "").strip()
    if not base:
        try:
            from app_paths import CONFIG_DIR
            base = str(CONFIG_DIR)
        except Exception:
            base = os.path.join(os.path.expanduser("~"), "BlackCube_Seguro")
    destino = os.path.join(base, "facturas_recibidas", "escaneos")
    try:
        os.makedirs(destino, exist_ok=True)
    except Exception:
        destino = tempfile.mkdtemp(prefix="escaneos_")
    return destino


# =========================================================
# SCRIPTS DE POWERSHELL (WIA = escaneo, Windows OCR = texto)
# =========================================================
_PS_LISTAR = r'''
$ErrorActionPreference = 'Stop'
$salida = $args[0]
$lista = @()
try {
    $dm = New-Object -ComObject WIA.DeviceManager
    foreach ($info in $dm.DeviceInfos) {
        $tipo = 0
        try { $tipo = $info.Properties.Item('Type').Value } catch { }
        if ($tipo -ne 65537) { continue }
        $nombre = ''
        try { $nombre = $info.Properties.Item('Name').Value } catch { }
        $lista += [pscustomobject]@{ nombre = [string]$nombre; id = [string]$info.DeviceID }
    }
    $json = [pscustomobject]@{ ok = $true; escaneres = $lista } | ConvertTo-Json -Depth 4
} catch {
    $json = [pscustomobject]@{ ok = $false; error = [string]$_.Exception.Message; escaneres = @() } | ConvertTo-Json -Depth 4
}
$json | Out-File -FilePath $salida -Encoding UTF8
'''

_PS_ESCANEAR = r'''
$ErrorActionPreference = 'Stop'
$salida = $args[0]
$carpeta = $args[1]
$dispositivo = $args[2]
$dpi = [int]$args[3]
$intent = [int]$args[4]
$origen = [int]$args[5]
$paginas = [int]$args[6]
$paginasEscaneadas = @()
$aviso = ''
try {
    if (-not (Test-Path $carpeta)) { New-Item -ItemType Directory -Path $carpeta -Force | Out-Null }
    $dm = New-Object -ComObject WIA.DeviceManager
    $elegido = $null
    foreach ($info in $dm.DeviceInfos) {
        $tipo = 0
        try { $tipo = $info.Properties.Item('Type').Value } catch { }
        if ($tipo -ne 65537) { continue }
        if ([string]::IsNullOrEmpty($dispositivo)) { $elegido = $info; break }
        if ([string]$info.DeviceID -eq $dispositivo) { $elegido = $info; break }
    }
    if ($null -eq $elegido) { throw 'No se detecto ningun escaner. Encienda la impresora, revise la conexion (USB/red) y vuelva a intentarlo.' }
    $idElegido = ''
    try { $idElegido = [string]$elegido.DeviceID } catch { }
    if (-not [string]::IsNullOrEmpty($dispositivo) -and $idElegido -ne $dispositivo) {
        $aviso = 'El escaner configurado ya no esta disponible; se uso: ' + [string]$elegido.Properties.Item('Name').Value + '.'
    }
    $nombre = ''
    try { $nombre = $elegido.Properties.Item('Name').Value } catch { }
    $dev = $elegido.Connect()
    if ($origen -eq 1) {
        try { $dev.Properties.Item('Document Handling Select').Value = 2 }
        catch { $aviso = 'No se pudo fijar el cristal: ' + $_.Exception.Message }
    } elseif ($origen -eq 2) {
        try { $dev.Properties.Item('Document Handling Select').Value = 1 }
        catch { $aviso = 'No se pudo fijar el alimentador: ' + $_.Exception.Message }
    }
    $hechas = 0
    while ($hechas -lt $paginas) {
        $item = $dev.Items.Item(1)
        try { if ($intent -gt 0) { $item.Properties.Item('Current Intent').Value = $intent } } catch { }
        try { $item.Properties.Item('Horizontal Resolution').Value = $dpi } catch { }
        try { $item.Properties.Item('Vertical Resolution').Value = $dpi } catch { }
        $imagen = $item.Transfer('{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}')
        $hechas++
        $ruta = Join-Path $carpeta ('pagina_{0:D2}.jpg' -f $hechas)
        if (Test-Path $ruta) { Remove-Item $ruta -Force }
        $imagen.SaveFile($ruta)
        $paginasEscaneadas += [string]$ruta
        if ($paginas -le 1) { break }
        $estado = 0
        try { $estado = $dev.Properties.Item('Document Handling Status').Value } catch { }
        if (($estado -band 1) -ne 1) { break }
    }
    $json = [pscustomobject]@{ ok = $true; escaner = [string]$nombre; paginas = $paginasEscaneadas; aviso = [string]$aviso; error = '' } | ConvertTo-Json -Depth 4
} catch {
    $json = [pscustomobject]@{ ok = $false; escaner = ''; paginas = $paginasEscaneadas; aviso = [string]$aviso; error = [string]$_.Exception.Message } | ConvertTo-Json -Depth 4
}
$json | Out-File -FilePath $salida -Encoding UTF8
'''

_PS_OCR_WINDOWS = r'''
$ErrorActionPreference = 'Stop'
$salida = $args[0]
$idioma = $args[1]
$archivos = @()
for ($i = 2; $i -lt $args.Count; $i++) { if ($args[$i]) { $archivos += $args[$i] } }
$resultados = @()
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
    $tipoGenerico = 'IAsyncOperation' + [char]96 + '1'
    $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq $tipoGenerico })[0]
    function Await($tarea, $tipo) {
        $asTask = $asTaskGeneric.MakeGenericMethod($tipo)
        $netTask = $asTask.Invoke($null, @($tarea))
        $netTask.Wait(-1) | Out-Null
        $netTask.Result
    }
    [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
    [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
    [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
    [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null
    $motor = $null
    if ($idioma -eq 'Espanol') { try { $motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('es-ES')) } catch { } }
    elseif ($idioma -eq 'Ingles') { try { $motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US')) } catch { } }
    if ($null -eq $motor) { $motor = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if ($null -eq $motor) { throw 'Windows no tiene instalado ningun motor OCR.' }
    $tag = ''
    try { $tag = $motor.RecognizerLanguage.LanguageTag } catch { }
    foreach ($ruta in $archivos) {
        $texto = ''
        try {
            $archivo = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ruta)) ([Windows.Storage.StorageFile])
            $flujo = Await ($archivo.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
            $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($flujo)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $res = Await ($motor.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $texto = [string]$res.Text
            $flujo.Dispose()
        } catch { $texto = '' }
        $resultados += [pscustomobject]@{ ruta = [string]$ruta; texto = $texto }
    }
    $json = [pscustomobject]@{ ok = $true; motor = 'Windows OCR'; idioma = $tag; resultados = $resultados; error = '' } | ConvertTo-Json -Depth 5
} catch {
    $json = [pscustomobject]@{ ok = $false; motor = 'Windows OCR'; idioma = ''; resultados = $resultados; error = [string]$_.Exception.Message } | ConvertTo-Json -Depth 5
}
$json | Out-File -FilePath $salida -Encoding UTF8
'''


_PS_ESCANEAR_VENTANA = r'''
$ErrorActionPreference = 'Stop'
$salida = $args[0]
$ruta = $args[1]
$intent = [int]$args[2]
try {
    $dlg = New-Object -ComObject WIA.CommonDialog
    $imagen = $dlg.ShowAcquireImage(65537, $intent, 0, '{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}', $true, $true, $true)
    if ($null -eq $imagen) { throw 'No se selecciono ningun escaner o se cancelo el escaneo.' }
    if (Test-Path $ruta) { Remove-Item $ruta -Force }
    $imagen.SaveFile($ruta)
    $json = [pscustomobject]@{ ok = $true; escaner = 'Ventana de Windows'; paginas = @([string]$ruta); aviso = ''; error = '' } | ConvertTo-Json -Depth 4
} catch {
    $json = [pscustomobject]@{ ok = $false; escaner = ''; paginas = @(); aviso = ''; error = [string]$_.Exception.Message } | ConvertTo-Json -Depth 4
}
$json | Out-File -FilePath $salida -Encoding UTF8
'''


def _ejecutar_ps(script, argumentos, timeout=240):
    """Ejecuta un script de PowerShell y devuelve el JSON que dejó en disco."""
    if not es_windows():
        return {"ok": False, "error": "El escaneo con la multifuncional solo esta disponible en Windows."}
    base = tempfile.mkdtemp(prefix="escaner_")
    ruta_ps = os.path.join(base, "tarea.ps1")
    ruta_json = os.path.join(base, "salida.json")
    try:
        with open(ruta_ps, "w", encoding="utf-8") as f:
            f.write(script)
        orden = [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ruta_ps, ruta_json]
        orden += [str(a) for a in argumentos]
        subprocess.run(orden, capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not os.path.exists(ruta_json):
            return {"ok": False, "error": "El escaner no respondio. Revise que este encendido y conectado."}
        with open(ruta_json, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "El escaneo tardo demasiado. Revise el equipo."}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(base, ignore_errors=True)


# =========================================================
# ESCANEO
# =========================================================
def listar_escaneres():
    """Lista de escáneres instalados: [{'nombre': ..., 'id': ...}, ...]"""
    res = _ejecutar_ps(_PS_LISTAR, [], timeout=90)
    if not res.get("ok"):
        return []
    escaneres = res.get("escaneres") or []
    if isinstance(escaneres, dict):
        escaneres = [escaneres]
    return [{"nombre": str(e.get("nombre") or "(sin nombre)"), "id": str(e.get("id") or "")}
            for e in escaneres]


def escanear(carpeta, dispositivo="", dpi=300, color="Escala de grises",
             origen="Cristal (una página a la vez)", paginas=1, optimizar=True,
             modo="Automático (sin ventanas)"):
    """Escanea y devuelve {'ok', 'escaner', 'paginas': [...], 'error', 'aviso'}.

    Modos:
      * "Automático (sin ventanas)": usa el escáner configurado (o el primero que
        encuentre) y escanea sin mostrar nada.
      * "Con la ventana de Windows": abre el cuadro de escaneo de Windows para
        elegir el escáner y sus opciones (útil si el equipo no aparece en la lista).
    """
    if not es_windows():
        return {"ok": False, "paginas": [], "error": "El escaneo solo esta disponible en Windows."}
    intent = _INTENT_WIA.get(color, 2)
    origen_wia = _ORIGEN_WIA.get(origen, 1)
    try:
        dpi = int(str(dpi).strip() or 300)
    except Exception:
        dpi = 300
    try:
        paginas = max(1, int(str(paginas).strip() or 1))
    except Exception:
        paginas = 1

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(carpeta, "escaneo_" + marca)
    os.makedirs(destino, exist_ok=True)

    paginas_ok = []
    aviso = ""
    error = ""
    escaner_usado = ""
    if str(modo).lower().startswith("con la ventana"):
        # Una página por vez, eligiendo el escáner en la ventana de Windows
        for numero in range(1, paginas + 1):
            ruta = os.path.join(destino, "pagina_%02d.jpg" % numero)
            res = _ejecutar_ps(_PS_ESCANEAR_VENTANA, [ruta, intent], timeout=600)
            if res.get("ok") and os.path.exists(ruta):
                paginas_ok.append(ruta)
                escaner_usado = res.get("escaner", "Ventana de Windows")
            else:
                error = res.get("error", "") or "No se escaneo ninguna pagina."
                break
    else:
        # El escáner a veces responde "dispositivo ocupado" (sobre todo si se acaba
        # de escanear): se reintenta un par de veces antes de rendirse.
        for intento in range(1, 4):
            res = _ejecutar_ps(_PS_ESCANEAR, [destino, dispositivo, dpi, intent, origen_wia, paginas], timeout=420)
            paginas_ok = [str(p) for p in (res.get("paginas") or []) if os.path.exists(str(p))]
            aviso = res.get("aviso", "")
            error = res.get("error", "")
            escaner_usado = res.get("escaner", "")
            if paginas_ok:
                break
            if not _error_ocupado(error) or intento == 3:
                break
            aviso = (aviso + " El escaner estaba ocupado; se reintento.").strip()
            time.sleep(4)

    convertidas = []
    for ruta in paginas_ok:
        # Siempre se convierte a JPEG real (WIA guarda BMP con nombre .jpg);
        # si el usuario pidió no optimizar, se conserva más calidad y el tamaño.
        convertidas.append(_optimizar_imagen(ruta, max_lado=2600 if optimizar else None,
                                              calidad=72 if optimizar else 92) or ruta)
    paginas_ok = convertidas
    return {
        "ok": bool(paginas_ok),
        "escaner": escaner_usado,
        "paginas": paginas_ok,
        "aviso": aviso,
        "error": error or ("" if paginas_ok else
                           "No se detecto ningun escaner. Encienda la impresora, revise la conexion "
                           "(USB o red) y vuelva a intentarlo."),
    }


def _error_ocupado(error):
    """¿El error es porque el escáner estaba ocupado?"""
    texto = str(error or "").lower()
    return any(pista in texto for pista in ("ocupad", "busy", "0x80210006", "in use", "en uso"))


def _optimizar_imagen(ruta, max_lado=2600, calidad=72):
    """Convierte la imagen escaneada en un JPG real y le baja el peso.

    Dos detalles de Windows que hay que respetar:
      * el escáner (WIA/WSD) suele guardar un BMP aunque el archivo se llame .jpg,
        así que se vuelve a guardar como JPEG de verdad;
      * PIL no puede sobrescribir el mismo archivo que acaba de leer
        (OSError 22), por eso se guarda en un temporal y luego se reemplaza.
    """
    temporal = ruta + ".tmp.jpg"
    imagen = None
    try:
        from PIL import Image
        with Image.open(ruta) as original:
            original.load()
            imagen = original.copy()
        # 🪟 Windows: mientras viva el objeto de PIL el archivo sigue mapeado y no se
        # puede reemplazar, así que se suelta la referencia antes de continuar.
        try:
            original.close()
        except Exception:
            pass
        del original
        if max_lado:
            ancho, alto = imagen.size
            escala = min(1.0, float(max_lado) / max(ancho, alto))
            if escala < 1.0:
                imagen = imagen.resize((max(1, int(ancho * escala)), max(1, int(alto * escala))), Image.LANCZOS)
        if imagen.mode not in ("L", "RGB"):
            imagen = imagen.convert("RGB")
        imagen.save(temporal, "JPEG", quality=calidad, optimize=True)
    except Exception:
        _borrar_si_existe(temporal)
        return ruta

    imagen = None
    try:
        os.replace(temporal, ruta)
        return ruta
    except Exception:
        pass
    # Plan B: borrar el original y renombrar el convertido
    try:
        os.chmod(ruta, 0o666)
    except Exception:
        pass
    try:
        os.remove(ruta)
    except Exception:
        pass
    try:
        os.replace(temporal, ruta)
        return ruta
    except Exception:
        pass
    # Plan C: dejar el convertido al lado con otro nombre
    destino = os.path.splitext(ruta)[0] + "_convertida.jpg"
    try:
        os.replace(temporal, destino)
        return destino
    except Exception:
        _borrar_si_existe(temporal)
        return ruta


def _borrar_si_existe(ruta):
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except Exception:
        pass


# =========================================================
# OCR
# =========================================================
def _ocr_tesseract(rutas, idioma="spa+eng"):
    """OCR con Tesseract, solo si está instalado (pytesseract + binario)."""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return None
    try:
        textos = []
        for ruta in rutas:
            with Image.open(ruta) as imagen:
                textos.append(pytesseract.image_to_string(imagen, lang=idioma))
        return {"ok": True, "motor": "Tesseract", "idioma": idioma,
                "resultados": [{"ruta": r, "texto": t} for r, t in zip(rutas, textos)], "error": ""}
    except Exception:
        return None


def ocr_paginas(rutas, idioma="Automático"):
    """Texto de cada imagen: {'ok', 'motor', 'idioma', 'resultados', 'error'}."""
    rutas = [str(r) for r in (rutas or []) if os.path.exists(str(r))]
    if not rutas:
        return {"ok": False, "motor": "", "idioma": "", "resultados": [], "error": "No hay imagenes para leer."}

    if not es_windows():
        resultado = _ocr_tesseract(rutas)
        if resultado:
            return resultado
        return {"ok": False, "motor": "", "idioma": "", "resultados": [],
                "error": "El OCR solo esta disponible en Windows (o instale Tesseract)."}

    idioma_ps = {"Español": "Espanol", "Inglés": "Ingles"}.get(idioma, "Automatico")
    res = _ejecutar_ps(_PS_OCR_WINDOWS, [idioma_ps] + rutas, timeout=300)
    if res.get("ok"):
        resultados = res.get("resultados") or []
        if isinstance(resultados, dict):
            resultados = [resultados]
        if any(str(r.get("texto") or "").strip() for r in resultados):
            return {"ok": True, "motor": res.get("motor", "Windows OCR"), "idioma": res.get("idioma", ""),
                    "resultados": resultados, "error": ""}
    respaldo = _ocr_tesseract(rutas)
    if respaldo:
        return respaldo
    return {"ok": False, "motor": res.get("motor", "Windows OCR"), "idioma": res.get("idioma", ""),
            "resultados": res.get("resultados") or [],
            "error": res.get("error") or "El OCR no reconocio texto en la imagen."}


def texto_de_resultados(resultado_ocr):
    """Une el texto de todas las páginas."""
    partes = []
    for item in (resultado_ocr or {}).get("resultados") or []:
        texto = str(item.get("texto") or "").strip()
        if texto:
            partes.append(texto)
    return "\n".join(partes).strip()


# =========================================================
# PDF
# =========================================================
def _tamano_pagina(ancho, alto, maximo=595.27, maximo_alto=841.89):
    """Tamaño de página del PDF manteniendo la proporción (por defecto A4)."""
    escala = min(maximo / float(ancho or 1), maximo_alto / float(alto or 1))
    return max(1.0, ancho * escala), max(1.0, alto * escala)


def imagenes_a_pdf(rutas, salida_pdf):
    """Arma un PDF (una página por imagen) para adjuntarlo a la factura."""
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdfcanvas

    rutas = [str(r) for r in (rutas or []) if os.path.exists(str(r))]
    if not rutas:
        raise ValueError("No hay imagenes para armar el PDF.")
    carpeta = os.path.dirname(os.path.abspath(salida_pdf))
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    pdf = pdfcanvas.Canvas(salida_pdf)
    for ruta in rutas:
        lector = ImageReader(ruta)
        ancho, alto = lector.getSize()
        ancho_pag, alto_pag = _tamano_pagina(ancho, alto)
        pdf.setPageSize((ancho_pag, alto_pag))
        pdf.drawImage(lector, 0, 0, width=ancho_pag, height=alto_pag)
        pdf.showPage()
    pdf.save()
    return salida_pdf


# =========================================================
# FLUJO COMPLETO
# =========================================================
def procesar_paginas(paginas, config=None, aviso=""):
    """Arma el PDF y hace el OCR de las páginas indicadas (sin escanear).

    Sirve tanto para el escaneo inicial como para agregar páginas después.
    """
    datos = config_escaner(config)
    paginas = [str(p) for p in (paginas or []) if os.path.exists(str(p))]
    if not paginas:
        return {"ok": False, "paginas": [], "pdf": "", "texto": "", "ocr": {},
                "error": "No hay paginas escaneadas."}

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_pdf = os.path.join(os.path.dirname(paginas[0]), "Factura_Escaneada_" + marca + ".pdf")
    try:
        imagenes_a_pdf(paginas, ruta_pdf)
    except Exception as e:
        return {"ok": False, "paginas": paginas, "pdf": "", "texto": "", "ocr": {},
                "error": "No se pudo armar el PDF: " + str(e)}

    resultado = {"ok": True, "paginas": paginas, "pdf": ruta_pdf, "texto": "", "ocr": {},
                 "aviso": aviso or "", "error": ""}
    if str(datos.get("escaner_ocr", "Sí")).lower().startswith("s"):
        ocr = ocr_paginas(paginas, datos.get("escaner_idioma_ocr", "Automático"))
        resultado["ocr"] = ocr
        resultado["texto"] = texto_de_resultados(ocr)
        if not resultado["texto"]:
            resultado["aviso"] = (resultado["aviso"] + " " +
                                  (ocr.get("error") or "El OCR no reconocio texto; complete los datos a mano.")).strip()
    return resultado


def procesar_factura(config=None, paginas=None, paginas_previas=None):
    """Escanea, arma el PDF y hace el OCR. Devuelve un diccionario con el resultado.

    'paginas_previas' permite agregar páginas a un escaneo ya hecho (cristal).
    """
    datos = config_escaner(config)
    carpeta = carpeta_escaneos(config)
    cuantas = int(str(paginas or datos.get("escaner_paginas") or 1).strip() or 1)

    escaneo = escanear(carpeta,
                       dispositivo=datos.get("escaner_dispositivo", ""),
                       dpi=datos.get("escaner_dpi", "300"),
                       color=datos.get("escaner_color", "Escala de grises"),
                       origen=datos.get("escaner_origen", "Cristal (una página a la vez)"),
                       paginas=cuantas,
                       optimizar=str(datos.get("escaner_optimizar", "Sí")).lower().startswith("s"),
                       modo=datos.get("escaner_modo", "Automático (sin ventanas)"))
    if not escaneo.get("ok"):
        return {"ok": False, "paginas": list(paginas_previas or []), "pdf": "", "texto": "", "ocr": {},
                "error": escaneo.get("error") or "No se pudo escanear."}

    todas = [str(p) for p in (paginas_previas or []) if os.path.exists(str(p))] + escaneo["paginas"]
    resultado = procesar_paginas(todas, config, aviso=escaneo.get("aviso", ""))
    resultado["escaner"] = escaneo.get("escaner", "")
    if escaneo.get("aviso"):
        resultado["aviso"] = (resultado.get("aviso", "") + " " + escaneo["aviso"]).strip()
    return resultado