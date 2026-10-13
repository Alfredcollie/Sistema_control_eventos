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
def _opciones_modo():
    """Modos de escaneo disponibles segun el sistema operativo.

    Se mira sys.platform directamente porque este valor se calcula al importar
    el modulo, antes de que existan las funciones es_windows()/es_mac().
    """
    if sys.platform == "win32":
        return ["Automático (sin ventanas)", "Con la ventana de Windows"]
    if sys.platform == "darwin":
        return ["Automático (sin ventanas)", "Con Image Capture"]
    return ["Automático (sin ventanas)"]


OPCIONES_MODO = _opciones_modo()

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
    "escaner_url": "",                      # macOS: URL de AirScan (http://ip/eSCL)
}

_INTENT_WIA = {"Color": 1, "Escala de grises": 2, "Blanco y negro": 4}
_ORIGEN_WIA = {"Cristal (una página a la vez)": 1, "Alimentador (varias páginas)": 2, "Automático": 0}


def es_windows():
    return sys.platform == "win32"


# Servicios Bonjour que anuncian los escaneres por red (AirScan/eSCL)
_MAC_SERVICIOS = ("_uscan._tcp", "_uscans._tcp", "_scanner._tcp")


def es_mac():
    """¿Corre en macOS (Mac Intel o Apple Silicon M1/M2/M3)?"""
    return sys.platform == "darwin"


def sistema_soportado():
    """El escaneo funciona en Windows (WIA) y en macOS (AirScan/eSCL)."""
    return es_windows() or es_mac()


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
        # El cristal es el modo por defecto del equipo: si no se puede fijar por
        # nombre no es un problema (el escaneo funciona igual), así que no se avisa.
        try { $dev.Properties.Item('Document Handling Select').Value = 2 } catch { }
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
    """Lista de escáneres disponibles: [{'nombre': ..., 'id': ...}, ...].

    En Windows se usa WIA (id = identificador del dispositivo) y en macOS el
    descubrimiento AirScan por Bonjour (id = URL del escáner).
    """
    if es_mac():
        try:
            return listar_escaneres_mac()
        except Exception:
            return []
    if not es_windows():
        return []
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
    if es_mac():
        # macOS (M1/M2): AirScan/eSCL, sin instalar nada
        try:
            return _escanear_mac(carpeta, dispositivo=dispositivo, dpi=dpi, color=color,
                                 origen=origen, paginas=paginas, modo=modo)
        except Exception as e:
            return {"ok": False, "paginas": [], "escaner": "",
                    "error": ("No se pudo escanear en este Mac: " + str(e) +
                              "\n\nRevise que la multifuncional esté encendida y en la misma red.")}
    if not es_windows():
        return {"ok": False, "paginas": [],
                "error": "El escaneo automatico esta disponible en Windows y macOS."}
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


def _ejecutar_con_tiempo(orden, segundos=4):
    """Ejecuta un comando y devuelve su salida aunque siga vivo al vencer el tiempo.

    dns-sd no termina solo: se le da un momento y luego se corta, conservando
    lo que alcanzo a escribir.
    """
    try:
        proceso = subprocess.Popen(orden, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except Exception:
        return ""
    salida = b""
    try:
        salida = proceso.communicate(timeout=segundos)[0] or b""
    except Exception:
        try:
            proceso.kill()
        except Exception:
            pass
        try:
            salida = proceso.communicate()[0] or b""
        except Exception:
            salida = b""
    if isinstance(salida, bytes):
        return salida.decode("utf-8", "ignore")
    return str(salida or "")


def _parsear_busqueda_dns_sd(salida, servicio="_uscan._tcp"):
    """Instancias anunciadas en la salida de 'dns-sd -B <servicio>'.

    Ejemplo de linea:
      13:05:12.456  Add  3  14 local.  _uscan._tcp.  HP Smart Tank 710 series
    Devuelve la lista de nombres de instancia (sin repetir).
    """
    instancias = []
    for linea in str(salida or "").splitlines():
        if servicio not in linea:
            continue
        resto = linea.split(servicio, 1)[1].lstrip(". ").strip()
        resto = " ".join(resto.split())
        if not resto or resto.lower() in ("instance name",):
            continue
        if resto not in instancias:
            instancias.append(resto)
    return instancias


def _parsear_resolucion_dns_sd(salida):
    """Datos de 'dns-sd -L <instancia> <servicio>'.

    Ejemplo:
      13:05:13.7  HP Smart Tank 710._uscan._tcp.local. can be reached at HP7C91CA.local.:8080 (interface 14)
       rs=/eSCL
       ty=HP Smart Tank 710 series
    Devuelve {'host','puerto','ruta','url'} o None.
    """
    import re as _re
    texto = str(salida or "")
    m = _re.search(r"can be reached at\s+([^\s:]+):(\d+)", texto)
    if not m:
        return None
    host, puerto = m.group(1).rstrip("."), m.group(2)
    m_ruta = _re.search(r"^\s*rs=(\S+)", texto, _re.MULTILINE)
    ruta = (m_ruta.group(1) if m_ruta else "/eSCL").strip()
    if not ruta.startswith("/"):
        ruta = "/" + ruta
    return {"host": host, "puerto": puerto, "ruta": ruta,
            "url": f"http://{host}:{puerto}{ruta}"}


def _resolver_instancia(instancia, servicio="_uscan._tcp"):
    """Convierte una instancia Bonjour en su URL de AirScan (o '')."""
    dns_sd = shutil.which("dns-sd") or "/usr/bin/dns-sd"
    salida = _ejecutar_con_tiempo([dns_sd, "-L", instancia, servicio, "local"], segundos=4)
    datos = _parsear_resolucion_dns_sd(salida)
    return datos or {}


def listar_escaneres_mac():
    """Escaneres disponibles en macOS: [{'nombre','id'}] (id = URL de AirScan)."""
    equipos = []
    for servicio in _MAC_SERVICIOS:
        dns_sd = shutil.which("dns-sd") or "/usr/bin/dns-sd"
        salida = _ejecutar_con_tiempo([dns_sd, "-B", servicio, "local"], segundos=4)
        for instancia in _parsear_busqueda_dns_sd(salida, servicio):
            datos = _resolver_instancia(instancia, servicio)
            url = str(datos.get("url") or "")
            if not url:
                continue
            if any(e["id"] == url for e in equipos):
                continue
            equipos.append({"nombre": instancia, "id": url})
    return equipos


def _config_local_escaner():
    """Datos del escaner leídos del archivo de configuración local (respaldo)."""
    try:
        import json
        from app_paths import CONFIG_FILE
        with open(str(CONFIG_FILE), "r", encoding="utf-8") as archivo:
            return json.load(archivo) or {}
    except Exception:
        return {}


def _escl_base_url(dispositivo="", config=None):
    """URL base de AirScan del escaner a usar (la configurada o el primero que aparezca)."""
    candidatos = []
    dispositivo = str(dispositivo or "").strip()
    if dispositivo.startswith("http"):
        candidatos.append(dispositivo)
    if config is None:
        config = _config_local_escaner()
    datos = config_escaner(config)
    url_config = str(datos.get("escaner_url") or "").strip()
    if url_config:
        candidatos.append(url_config)
    for url in candidatos:
        if not url:
            continue
        if not url.startswith("http"):
            url = "http://" + url
        if not url.rstrip("/").endswith(("eSCL", "eSCL/ScanJobs")):
            url = url.rstrip("/") + "/eSCL"
        return url.rstrip("/")
    equipos = listar_escaneres_mac()
    for equipo in equipos:
        if dispositivo and equipo["id"] == dispositivo:
            return equipo["id"]
    if equipos:
        return equipos[0]["id"]
    return ""


def _xml_scan_settings(dpi=300, color="Escala de grises", origen="Cristal (una página a la vez)"):
    """Peticion de escaneo (eSCL) en XML. Las medidas van en 1/300 de pulgada (A4)."""
    try:
        dpi = max(75, min(1200, int(str(dpi).strip() or 300)))
    except Exception:
        dpi = 300
    modo = {"Color": "RGB24", "Escala de grises": "Grayscale8",
            "Blanco y negro": "BlackAndWhite1"}.get(color, "Grayscale8")
    fuente = "Feeder" if str(origen).lower().startswith("alimentador") else "Platen"
    ancho = int(round(8.27 * 300))      # A4 = 210 x 297 mm = 8,27 x 11,69 pulgadas
    alto = int(round(11.69 * 300))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"'
        ' xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">'
        '<pwg:Version>2.1</pwg:Version>'
        '<pwg:ScanRegions><pwg:ScanRegion>'
        '<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>'
        '<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>'
        f'<pwg:Width>{ancho}</pwg:Width><pwg:Height>{alto}</pwg:Height>'
        '</pwg:ScanRegion></pwg:ScanRegions>'
        f'<scan:InputSource>{fuente}</scan:InputSource>'
        f'<scan:ColorMode>{modo}</scan:ColorMode>'
        f'<scan:Resolution>{dpi}</scan:Resolution>'
        '<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>'
        '</scan:ScanSettings>'
    )


def _escanear_mac(carpeta, dispositivo="", dpi=300, color="Escala de grises",
                  origen="Cristal (una página a la vez)", paginas=1, modo=""):
    """Escaneo en macOS con AirScan/eSCL. Mismo formato de salida que en Windows."""
    import urllib.request
    import urllib.error
    import re as _re

    if "image capture" in str(modo).lower() or "con la ventana" in str(modo).lower():
        # Modo alternativo: se escanea con Image Capture y luego se lee el archivo
        try:
            subprocess.Popen(["open", "-a", "Image Capture"])
        except Exception:
            pass
        return {"ok": False, "paginas": [], "escaner": "Image Capture", "aviso": "",
                "error": "Se abrio Image Capture. Escanee alli, guarde el archivo y despues use "
                         "\u00ab\U0001F4C4 Desde PDF\u00bb para leerlo con el OCR del sistema."}

    base = _escl_base_url(dispositivo, config=None)
    if not base:
        return {"ok": False, "paginas": [], "escaner": "",
                "aviso": "",
                "error": "No se encontro ninguna multifuncional por red (AirScan).\n"
                         "Revise que la impresora este encendida y en la misma red, o escriba su "
                         "direccion en Ajustes > Configuracion General > Escaner (campo URL)."}

    try:
        paginas = max(1, int(str(paginas).strip() or 1))
    except Exception:
        paginas = 1

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(carpeta, "escaneo_" + marca)
    os.makedirs(destino, exist_ok=True)
    xml = _xml_scan_settings(dpi=dpi, color=color, origen=origen).encode("utf-8")

    peticion = urllib.request.Request(base + "/ScanJobs", data=xml,
                                      headers={"Content-Type": "text/xml"})
    try:
        with urllib.request.urlopen(peticion, timeout=60) as respuesta:
            ubicacion = respuesta.headers.get("Location") or ""
    except urllib.error.HTTPError as e:
        detalle = ""
        try:
            detalle = e.read().decode("utf-8", "ignore")[:200]
        except Exception:
            pass
        return {"ok": False, "paginas": [], "escaner": base, "aviso": "",
                "error": f"El escaner respondio HTTP {e.code}. {detalle}".strip()}
    except Exception as e:
        return {"ok": False, "paginas": [], "escaner": base, "aviso": "",
                "error": f"No se pudo hablar con el escaner ({base}): {e}"}

    if not ubicacion:
        return {"ok": False, "paginas": [], "escaner": base, "aviso": "",
                "error": "El escaner no devolvio la ubicacion del trabajo de escaneo."}
    if ubicacion.startswith("/"):
        raiz = _re.match(r"https?://[^/]+", base)
        ubicacion = (raiz.group(0) if raiz else base) + ubicacion

    imagenes = []
    for numero in range(1, paginas + 1):
        try:
            with urllib.request.urlopen(ubicacion + "/NextDocument", timeout=180) as respuesta:
                contenido = respuesta.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                break                      # ya no hay mas hojas (fin del alimentador)
            return {"ok": False, "paginas": imagenes, "escaner": base, "aviso": "",
                    "error": f"No se pudo recibir la imagen (HTTP {e.code})."}
        except Exception as e:
            return {"ok": False, "paginas": imagenes, "escaner": base, "aviso": "",
                    "error": f"No se pudo recibir la imagen: {e}"}
        if not contenido:
            break
        ruta = os.path.join(destino, "pagina_%02d.jpg" % numero)
        with open(ruta, "wb") as archivo:
            archivo.write(contenido)
        imagenes.append(ruta)

    aviso = "" if imagenes else "El escaner no devolvio ninguna imagen."
    return {"ok": bool(imagenes), "escaner": base, "paginas": imagenes, "aviso": aviso,
            "error": "" if imagenes else (aviso or "No se pudo escanear.")}


def _ocr_vision(rutas, idioma="Automático"):
    """OCR en macOS con el motor nativo (Vision, por PyObjC). None si no esta."""
    try:
        from Foundation import NSURL
        import Vision
    except Exception:
        return None
    idiomas = {"Español": ["es-ES"], "Inglés": ["en-US"],
               "Español + Inglés": ["es-ES", "en-US"]}.get(idioma, ["es-ES", "en-US"])
    textos = []
    try:
        for ruta in rutas:
            url = NSURL.fileURLWithPath_(str(ruta))
            peticion = Vision.VNRecognizeTextRequest.alloc().init()
            try:
                peticion.setRecognitionLevel_(0)          # 0 = precisa
            except Exception:
                pass
            try:
                peticion.setRecognitionLanguages_(idiomas)
            except Exception:
                pass
            try:
                peticion.setUsesLanguageCorrection_(True)
            except Exception:
                pass
            manejador = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
            manejador.performRequests_error_([peticion], None)
            observaciones = peticion.results() or []
            filas = []
            for observacion in observaciones:
                try:
                    candidato = observacion.topCandidates_(1)[0]
                    texto = str(candidato.string())
                except Exception:
                    continue
                caja = None
                try:
                    caja = observacion.boundingBox()
                except Exception:
                    pass
                y = 0.0
                x = 0.0
                try:
                    if caja is not None:
                        y = float(caja.origin.y)
                        x = float(caja.origin.x)
                except Exception:
                    pass
                filas.append((round(1 - y, 3), x, texto))   # Vision mide desde abajo
            filas.sort(key=lambda f: (f[0], f[1]))
            agrupadas = []
            for y, x, texto in filas:
                if agrupadas and abs(agrupadas[-1][0] - y) <= 0.012:
                    agrupadas[-1][2].append((x, texto))
                else:
                    agrupadas.append([y, y, [(x, texto)]])
            lineas = []
            for _y0, _y1, palabras in agrupadas:
                palabras.sort(key=lambda p: p[0])
                lineas.append(" ".join(t for _x, t in palabras))
            textos.append("\n".join(lineas))
    except Exception:
        return None
    if not any(t.strip() for t in textos):
        return None
    return {"ok": True, "motor": "Apple Vision (macOS)", "idioma": ", ".join(idiomas),
            "resultados": [{"ruta": r, "texto": t} for r, t in zip(rutas, textos)], "error": ""}
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

    if es_mac():
        # macOS: motor nativo (Vision) por PyObjC; si no está, Tesseract; si no, se avisa
        resultado = _ocr_vision(rutas, idioma)
        if resultado:
            return resultado
        respaldo = _ocr_tesseract(rutas)
        if respaldo:
            return respaldo
        return {"ok": False, "motor": "", "idioma": "", "resultados": [],
                "error": "El OCR de macOS (Vision) no está disponible. Reinstale la aplicación "
                         "o complete los datos a mano."}
    if not es_windows():
        resultado = _ocr_tesseract(rutas)
        if resultado:
            return resultado
        return {"ok": False, "motor": "", "idioma": "", "resultados": [],
                "error": "El OCR solo esta disponible en Windows y macOS (o instale Tesseract)."}

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
