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
_MAC_SERVICIOS = ("_uscan._tcp", "_uscans._tcp")
# Respaldo: impresoras AirPrint (casi todas sirven eSCL en /eSCL)
_MAC_SERVICIOS_RESPALDO = ("_ipp._tcp", "_ipps._tcp")


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
        # Recorte opcional: escanear solo la zona del comprobante (2º intento).
        if ($areaX -ne '' -and $areaAncho -ne '' -and $areaAlto -ne '') {
            try { $item.Properties.Item('Horizontal Start Position').Value = [int]$areaX } catch { }
            try { $item.Properties.Item('Vertical Start Position').Value = [int]$areaY } catch { }
            try { $item.Properties.Item('Horizontal Extent').Value = [int]$areaAncho } catch { }
            try { $item.Properties.Item('Vertical Extent').Value = [int]$areaAlto } catch { }
        }
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
            # Se unen las lineas del OCR (no todo en una sola linea): asi el
            # sistema puede leer cada dato en su renglon. Vale en Windows;
            # macOS (Vision) y Tesseract ya devuelven lineas separadas.
            $lineas_ocr = @()
            try { foreach ($ln in $res.Lines) { $lineas_ocr += [string]$ln.Text } } catch { }
            if ($lineas_ocr.Count -gt 0) { $texto = ($lineas_ocr -join "`n") }
            else { $texto = [string]$res.Text }
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
        banderas = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proceso = subprocess.Popen(orden, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, creationflags=banderas)
        try:
            proceso.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # ⏱️ El escáner se quedó colgado: se mata el proceso (y sus hijos) para
            #    liberar el equipo; si no, la ventana se queda "Escaneando..." para siempre.
            try:
                if es_windows():
                    # Windows: se mata también el árbol de procesos (PowerShell hijos)
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proceso.pid)],
                                   capture_output=True, creationflags=banderas)
                else:
                    proceso.kill()
            except Exception:
                try:
                    proceso.kill()
                except Exception:
                    pass
            try:
                proceso.communicate(timeout=5)
            except Exception:
                pass
            return {"ok": False,
                    "error": ("El escaner no respondio en %d segundos y se reinicio la conexion. "
                              "Revise que este encendido, con papel y sin trabajos pendientes; "
                              "luego vuelva a escanear." % int(timeout))}
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


def _aviso_sin_escaner():
    """Mensaje claro cuando no hay ningún escáner conectado o encendido."""
    return ("No hay conexión con el escáner.\n\n"
            "Verifique que la impresora/escáner esté ENCENDIDO y conectado "
            "(cable USB o a la misma red WiFi), con papel en la bandeja. "
            "Luego intente escanear de nuevo.")


def _endurecer_error(resultado):
    """Si no se pudo escanear y no hay escáner visible, avisa que no hay conexión."""
    try:
        if resultado and not (resultado.get("paginas") or []):
            try:
                hay = bool(listar_escaneres())
            except Exception:
                hay = False
            _err = str(resultado.get("error") or "").lower()
            if not hay or "no se detecto" in _err or "no se detectó" in _err:
                resultado["error"] = _aviso_sin_escaner()
    except Exception:
        pass
    return resultado


def escanear(carpeta, dispositivo="", dpi=300, color="Escala de grises",
             origen="Cristal (una página a la vez)", paginas=1, optimizar=True,
             modo="Automático (sin ventanas)", area=None):
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
            return _endurecer_error(_escanear_mac(carpeta, dispositivo=dispositivo, dpi=dpi,
                                                  color=color, origen=origen,
                                                  paginas=paginas, modo=modo))
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

    # 🔧 Si el identificador guardado ya no existe (Windows a veces le agrega un
    #    sufijo al DeviceID), se usa el que el sistema detecta ahora. Se hace
    #    SOLO al escanear, para no tocar la detección de la ventana de ajustes.
    try:
        if dispositivo:
            _detectados = [str(d.get("id") or "") for d in listar_escaneres()]
            if _detectados and dispositivo not in _detectados:
                _elegido = ""
                for _idd in _detectados:
                    if _idd.startswith(dispositivo) or dispositivo.startswith(_idd):
                        _elegido = _idd
                        break
                dispositivo = _elegido or _detectados[0]
                print("[Escáner] Dispositivo ajustado a:", dispositivo)
    except Exception as e_aj:
        print("[Escáner] No se pudo validar el dispositivo:", e_aj)

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
            res = _ejecutar_ps(_PS_ESCANEAR, [destino, dispositivo, dpi, intent, origen_wia, paginas] + list(area if area else ['', '', '', '']), timeout=90)
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
    return _endurecer_error({
        "ok": bool(paginas_ok),
        "escaner": escaner_usado,
        "paginas": paginas_ok,
        "aviso": aviso,
        "error": error or ("" if paginas_ok else
                           "No se detecto ningun escaner. Encienda la impresora, revise la conexion "
                           "(USB o red) y vuelva a intentarlo."),
    })


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
    """Convierte una instancia Bonjour en su URL de AirScan (o {}).

    Los servicios que terminan en "s" (_uscans / _ipps) van cifrados: se marca
    'tls' y la URL se arma con https (las impresoras usan certificado propio).
    """
    dns_sd = shutil.which("dns-sd") or "/usr/bin/dns-sd"
    salida = _ejecutar_con_tiempo([dns_sd, "-L", instancia, servicio, "local"], segundos=4)
    datos = _parsear_resolucion_dns_sd(salida) or {}
    if datos:
        seguro = servicio.rstrip(".").endswith("s._tcp") or "scans" in servicio or "ipps" in servicio
        datos["tls"] = bool(seguro)
        if seguro and str(datos.get("url", "")).startswith("http://"):
            datos["url"] = "https://" + datos["url"][len("http://"):]
    return datos


# =========================================================
# 🔎 DESCUBRIMIENTO DEL ESCANER EN macOS
# =========================================================
# Se hace en Python (consulta multicast de Bonjour) para no depender de 'dns-sd':
# asi funciona dentro de la aplicacion y macOS pide el permiso de "Red local"
# directamente al programa. Como respaldo se leen tambien las impresoras que ya
# estan configuradas en el sistema (lpstat -v).

def _mdns_consulta(servicio="_uscan._tcp.local"):
    """Paquete DNS para preguntar por ese servicio (PTR)."""
    import struct
    partes = [p for p in str(servicio).rstrip(".").split(".") if p]
    nombre = b""
    for parte in partes:
        datos = parte.encode("utf-8")[:63]
        nombre += bytes([len(datos)]) + datos
    nombre += b"\x00"
    return (struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + nombre +
            struct.pack(">HH", 12, 1))          # 12 = PTR, 1 = IN


def _mdns_leer_nombre(datos, posicion):
    """Lee un nombre del paquete (admite la compresion con punteros)."""
    partes, saltos = [], 0
    while posicion < len(datos):
        largo = datos[posicion]
        if largo == 0:
            posicion += 1
            break
        if largo & 0xC0 == 0xC0:                 # puntero a otra parte
            if posicion + 1 >= len(datos):
                break
            puntero = ((largo & 0x3F) << 8) | datos[posicion + 1]
            if saltos == 0:
                siguiente = posicion + 2
            posicion = puntero
            saltos += 1
            if saltos > 8:
                break
            continue
        partes.append(datos[posicion + 1: posicion + 1 + largo].decode("utf-8", "ignore"))
        posicion += 1 + largo
    if saltos:
        posicion = siguiente
    return ".".join([p for p in partes if p]), posicion


def _mdns_leer_respuesta(datos):
    """Saca de la respuesta de Bonjour los servicios (instancia, host, puerto)."""
    import struct
    encontrados = []
    try:
        if len(datos) < 12:
            return encontrados
        _, _, total_preguntas, total_resp, total_extras, _ = struct.unpack(">HHHHHH", datos[:12])
        posicion = 12
        for _ in range(total_preguntas):
            _, posicion = _mdns_leer_nombre(datos, posicion)
            posicion += 4
        registros = []
        for _ in range(total_resp + total_extras):
            nombre, posicion = _mdns_leer_nombre(datos, posicion)
            if posicion + 10 > len(datos):
                break
            tipo, clase, ttl, largo = struct.unpack(">HHIH", datos[posicion:posicion + 10])
            posicion += 10
            inicio = posicion
            valor = datos[posicion:posicion + largo]
            posicion += largo
            if tipo == 12:                        # PTR: "servicio -> instancia"
                destino, _ = _mdns_leer_nombre(datos, inicio)
                registros.append(("ptr", nombre, destino))
            elif tipo == 33 and largo >= 6:       # SRV: puerto y equipo
                try:
                    puerto = struct.unpack(">H", valor[4:6])[0]
                    equipo, _ = _mdns_leer_nombre(datos, inicio + 6)
                    registros.append(("srv", nombre, equipo, puerto))
                except Exception:
                    pass
    except Exception as e:
        print("Aviso - leyendo respuesta Bonjour:", e)
        return encontrados
    # Se cruzan PTR (instancia) con SRV (equipo y puerto)
    for reg in registros:
        if reg[0] != "ptr":
            continue
        instancia = reg[2].rstrip(".")
        host, puerto = "", 0
        for otro in registros:
            if otro[0] == "srv" and otro[1].rstrip(".") == instancia:
                host, puerto = otro[2].rstrip("."), otro[3]
                break
        encontrados.append((instancia, host, puerto))
    return encontrados


def _mdns_buscar(servicio="_uscan._tcp.local", segundos=3, maximo=45):
    """Pregunta por multicast que equipos anuncian ese servicio (sin dns-sd)."""
    import socket
    import time as _time
    encontrados = []
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        sock.sendto(_mdns_consulta(servicio), ("224.0.0.251", 5353))
        limite = _time.time() + segundos
        while _time.time() < limite and len(encontrados) < maximo:
            try:
                datos, _ = sock.recvfrom(9000)
            except socket.timeout:
                continue
            except Exception:
                break
            for hallado in _mdns_leer_respuesta(datos):
                if hallado not in encontrados:
                    encontrados.append(hallado)
    except Exception as e:
        print("Aviso - consulta Bonjour (%s):" % servicio, e)
    finally:
        try:
            if sock is not None:
                sock.close()
        except Exception:
            pass
    return encontrados


def _escaneres_por_lpstat():
    """Equipos de las impresoras ya configuradas en macOS (lpstat -v)."""
    import re as _re
    hosts = []
    salida = _ejecutar_con_tiempo(["lpstat", "-v"], segundos=6)
    for linea in str(salida or "").splitlines():
        if ":" not in linea:
            continue
        uri = linea.split(":", 1)[1].strip()
        encontrado = _re.search(r"://([^/]+)", uri)
        if not encontrado:
            continue
        host = encontrado.group(1).split("@")[-1]
        if "]" in host:                          # IPv6 -> se corta el puerto
            host = host.split("]")[0].lstrip("[")
        elif ":" in host:
            host = host.split(":")[0]
        host = host.replace("%20", " ").strip()
        # Se descartan los nombres de servicio de Bonjour (no son equipos)
        if not host or "_tcp" in host or "_udp" in host or host.startswith("_"):
            continue
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _probar_escl(base, timeout=2):
    """¿El escaner responde en esa URL? (evita ofrecer equipos que no contestan)."""
    import urllib.error
    try:
        with _abrir_url(str(base).rstrip("/") + "/ScannerCapabilities", timeout=timeout) as respuesta:
            respuesta.read(90)
        return True
    except urllib.error.HTTPError:
        return True                              # responde (aunque no de la ficha)
    except Exception:
        return False


def _candidatos_escl(host):
    """URLs de eSCL que se prueban para un equipo concreto."""
    host = str(host or "").strip().rstrip(".")
    if not host:
        return []
    candidatos = ["http://%s/eSCL" % host, "http://%s:8080/eSCL" % host, "https://%s/eSCL" % host]
    return candidatos


def diagnostico_escaner_mac():
    """Texto que explica qué se encontró al buscar el escáner en macOS."""
    lineas = []
    for servicio in _MAC_SERVICIOS:
        hallados = _mdns_buscar(servicio + ".local", segundos=2)
        lineas.append("Bonjour %s: %s" % (servicio, hallados if hallados else "nada"))
    lineas.append("Impresoras del sistema (lpstat): %s" % (_escaneres_por_lpstat() or "ninguna"))
    return "\n".join(lineas)


def listar_escaneres_mac():
    """Escanéres disponibles en macOS (AirScan/eSCL).

    Orden: 1) Bonjour en Python (sin depender de dns-sd), 2) respaldo con dns-sd,
    3) impresoras AirPrint y las ya configuradas en el sistema (lpstat), 4) la URL
    escrita a mano en Ajustes. Al final se comprueba quién responde de verdad.
    """
    candidatos = []

    # 1) 📡 Bonjour en Python
    for servicio, seguro in (("_uscan._tcp.local", False), ("_uscans._tcp.local", True)):
        for instancia, host, puerto in _mdns_buscar(servicio, segundos=3):
            if not host:
                continue
            esquema = "https" if seguro else "http"
            puerto_txt = (":%d" % puerto) if puerto and puerto not in (80, 443) else ""
            candidatos.append((instancia, "%s://%s%s/eSCL" % (esquema, host, puerto_txt)))

    # 2) 🔄 Respaldo: la herramienta dns-sd del sistema
    if not candidatos:
        dns_sd = shutil.which("dns-sd") or "/usr/bin/dns-sd"
        for servicio in _MAC_SERVICIOS:
            salida = _ejecutar_con_tiempo([dns_sd, "-B", servicio, "local"], segundos=4)
            for instancia in _parsear_busqueda_dns_sd(salida, servicio):
                datos = _resolver_instancia(instancia, servicio)
                if datos.get("url"):
                    candidatos.append((instancia, datos["url"]))

    # 3) 🖨️ Impresoras AirPrint y las del sistema
    hosts = []
    for servicio in _MAC_SERVICIOS_RESPALDO:
        for instancia, host, puerto in _mdns_buscar(servicio + ".local", segundos=2):
            if host and host not in hosts:
                hosts.append(host)
    for host in _escaneres_por_lpstat():
        if host not in hosts:
            hosts.append(host)
    for host in hosts:
        for url in _candidatos_escl(host):
            candidatos.append((host, url))

    # 4) La URL escrita a mano en Ajustes (siempre primero)
    try:
        datos_cfg = config_escaner(_config_local_escaner())
        url_cfg = str(datos_cfg.get("escaner_url") or "").strip()
        if url_cfg:
            if not url_cfg.startswith("http"):
                url_cfg = "http://" + url_cfg
            candidatos.insert(0, ("Escaner configurado", url_cfg))
    except Exception as e_cfg:
        print("Aviso - URL del escaner en la configuracion:", e_cfg)

    # 5) ✅ Se comprueba quien responde de verdad
    responden, vistos = [], set()
    for nombre, url in candidatos:
        url = str(url).rstrip("/")
        if not url or url in vistos:
            continue
        vistos.add(url)
        if _probar_escl(url, timeout=1.5):
            responden.append({"nombre": nombre, "id": url})
    if responden:
        return responden

    # Si nadie respondio se ofrecen igual (asi el usuario puede probar)
    equipos = []
    for nombre, url in candidatos:
        url = str(url).rstrip("/")
        if url and url not in [e["id"] for e in equipos]:
            equipos.append({"nombre": nombre + " (sin respuesta)", "id": url})
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


def _xml_scan_settings(dpi=300, color="Escala de grises", origen="Cristal (una página a la vez)",
                       version="2.1", formato_ext=True, intencion=True,
                       modo_color=None, fuente=None):
    """Petición de escaneo (eSCL) en XML. Las medidas van en 1/300 de pulgada (A4).

    Se puede ajustar para probar variantes: hay impresoras que solo aceptan
    <pwg:DocumentFormat>, otras exigen <scan:DocumentFormatExt>, y algunas solo
    entienden la versión 2.0 o el modo de color "Grayscale".
    """
    try:
        dpi = max(75, min(1200, int(str(dpi).strip() or 300)))
    except Exception:
        dpi = 300
    modo = modo_color or {"Color": "RGB24", "Escala de grises": "Grayscale8",
                          "Blanco y negro": "BlackAndWhite1"}.get(color, "Grayscale8")
    if fuente is None:
        fuente = "Feeder" if str(origen).lower().startswith("alimentador") else "Platen"
    ancho = int(round(8.27 * 300))      # A4 = 210 x 297 mm = 8,27 x 11,69 pulgadas
    alto = int(round(11.69 * 300))
    partes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"',
              ' xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">',
              f'<pwg:Version>{version}</pwg:Version>',
              '<pwg:ScanRegions><pwg:ScanRegion>',
              '<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>',
              '<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>',
              f'<pwg:Width>{ancho}</pwg:Width><pwg:Height>{alto}</pwg:Height>',
              '</pwg:ScanRegion></pwg:ScanRegions>',
              f'<scan:InputSource>{fuente}</scan:InputSource>']
    if intencion:
        partes.append('<scan:Intent>Document</scan:Intent>')
    partes.append(f'<scan:ColorMode>{modo}</scan:ColorMode>')
    partes.append(f'<scan:Resolution>{dpi}</scan:Resolution>')
    partes.append('<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>')
    if formato_ext:
        partes.append('<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>')
    partes.append('</scan:ScanSettings>')
    return "".join(partes)


def _variantes_scan_settings(dpi=300, color="Escala de grises",
                             origen="Cristal (una página a la vez)"):
    """Peticiones eSCL que se prueban EN ORDEN: cada impresora acepta unas y otras."""
    modos = [None]
    texto_color = str(color).lower()
    if "gris" in texto_color:
        modos.append("Grayscale")                 # algunas HP solo aceptan "Grayscale"
    if "negro" in texto_color:
        modos.append("BlackAndWhite")
    fuentes = [None]
    if str(origen).lower().startswith("alimentador"):
        fuentes.append("ADF")
    variantes, vistas = [], set()
    for version in ("2.1", "2.0"):
        for ext in (True, False):
            for modo in modos:
                for fuente in fuentes:
                    xml = _xml_scan_settings(dpi=dpi, color=color, origen=origen, version=version,
                                             formato_ext=ext, modo_color=modo, fuente=fuente)
                    if xml not in vistas:
                        vistas.add(xml)
                        variantes.append(xml)
    return variantes


def _abrir_url(url, datos=None, cabeceras=None, timeout=60):
    """Abre una URL del escáner. En https acepta el certificado propio de la impresora."""
    import urllib.request
    import ssl
    peticion = urllib.request.Request(url, data=datos, headers=cabeceras or {})
    if str(url).lower().startswith("https"):
        try:
            contexto = ssl._create_unverified_context()   # las impresoras usan certificado propio
            return urllib.request.urlopen(peticion, timeout=timeout, context=contexto)
        except Exception:
            pass
    return urllib.request.urlopen(peticion, timeout=timeout)


def _escanear_mac(carpeta, dispositivo="", dpi=300, color="Escala de grises",
                  origen="Cristal (una página a la vez)", paginas=1, modo=""):
    """Escaneo en macOS con AirScan/eSCL. Mismo formato de salida que en Windows.

    Se prueban varias formas de la petición porque no todas las impresoras
    aceptan lo mismo (unas quieren <scan:DocumentFormatExt>, otras la versión 2.0,
    otras el modo "Grayscale"...). Si todo falla, se explica exactamente qué pasó.
    """
    import urllib.request
    import urllib.error
    import re as _re

    if "image capture" in str(modo).lower() or "con la ventana" in str(modo).lower():
        # Modo alternativo (siempre funciona): escanear con Image Capture
        try:
            subprocess.Popen(["open", "-a", "Image Capture"])
        except Exception:
            pass
        return {"ok": False, "paginas": [], "escaner": "Image Capture", "aviso": "",
                "error": "Se abrio Image Capture para escanear.\n\n"
                         "1) Escanee ahi y guarde el archivo (PDF o JPG).\n"
                         "2) Vuelva al sistema y use \u00ab\U0001F4C4 Desde PDF\u00bb (o "
                         "\u00ab\U0001F4CE Adjuntar Archivo Manual\u00bb) para leerlo con el OCR del Mac.\n\n"
                         "Si prefiere el escaneo automático: revise que la impresora esté en la "
                         "MISMA red wifi y escriba su dirección en Ajustes \u2192 Configuración "
                         "General \u2192 Escáner (campo URL, ej. http://192.168.1.50/eSCL)."}

    base = _escl_base_url(dispositivo, config=None)
    if not base:
        return {"ok": False, "paginas": [], "escaner": "",
                "aviso": "",
                "error": "No se encontro ninguna multifuncional por red (AirScan).\n\n"
                         "\u2022 Revise que la impresora este encendida y en la MISMA red wifi "
                         "(no en la red de invitados).\n"
                         "\u2022 Si esta conectada por USB, use el modo \u00abCon Image Capture\u00bb.\n"
                         "\u2022 O escriba su direccion en Ajustes \u2192 Configuracion General \u2192 "
                         "Escaner (campo URL, ej. http://192.168.1.50/eSCL)."}

    try:
        paginas = max(1, int(str(paginas).strip() or 1))
    except Exception:
        paginas = 1

    # 🔎 Se comprueba que el escaner responda (aviso, no bloquea)
    aviso_capacidades = ""
    try:
        with _abrir_url(base + "/ScannerCapabilities", timeout=8) as respuestas:
            respuestas.read(200)
    except Exception as e_cap:
        aviso_capacidades = ("No se pudo leer la ficha del escaner (%s). " % str(e_cap)[:60])
        print("Aviso - ScannerCapabilities del escaner:", e_cap)

    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = os.path.join(carpeta, "escaneo_" + marca)
    os.makedirs(destino, exist_ok=True)

    # 📄 Se prueban las variantes de peticion hasta que una sea aceptada
    ubicacion = ""
    ultimo_error = ""
    variantes = _variantes_scan_settings(dpi=dpi, color=color, origen=origen)
    for indice, xml_texto in enumerate(variantes):
        try:
            peticion = urllib.request.Request(base + "/ScanJobs", data=xml_texto.encode("utf-8"),
                                              headers={"Content-Type": "text/xml"})
            with _abrir_url(base + "/ScanJobs", datos=xml_texto.encode("utf-8"),
                            cabeceras={"Content-Type": "text/xml"}, timeout=60) as respuesta:
                ubicacion = respuesta.headers.get("Location") or ""
            if ubicacion:
                break
            ultimo_error = "El escaner acepto la orden pero no devolvio donde recoger la imagen."
        except urllib.error.HTTPError as e:
            detalle = ""
            try:
                detalle = e.read().decode("utf-8", "ignore")[:160]
            except Exception:
                pass
            ultimo_error = "HTTP %s. %s" % (e.code, detalle.strip())
            print("Aviso - escaneo eSCL variante %d: %s" % (indice + 1, ultimo_error))
            continue
        except Exception as e:
            return {"ok": False, "paginas": [], "escaner": base, "aviso": aviso_capacidades,
                    "error": ("No se pudo hablar con el escaner (%s): %s\n\n"
                              "Revise que la impresora este en la MISMA red wifi y que el "
                              "escaneo por red (AirScan) este activado." % (base, e))}

    if not ubicacion:
        return {"ok": False, "paginas": [], "escaner": base, "aviso": aviso_capacidades,
                "error": ("El escaner no acepto la orden de escaneo.\n%s\n\n"
                          "Pruebe: Ajustes \u2192 Configuracion General \u2192 Escaner \u2192 "
                          "modo \u00abCon Image Capture\u00bb, o escriba la URL del escaner "
                          "(ej. http://192.168.1.50/eSCL)." % ultimo_error)}

    if ubicacion.startswith("/"):
        raiz = _re.match(r"https?://[^/]+", base)
        ubicacion = (raiz.group(0) if raiz else base) + ubicacion

    imagenes = []
    for numero in range(1, paginas + 1):
        try:
            with _abrir_url(ubicacion + "/NextDocument", timeout=180) as respuesta:
                contenido = respuesta.read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                break                      # ya no hay mas hojas (fin del alimentador)
            return {"ok": False, "paginas": imagenes, "escaner": base, "aviso": aviso_capacidades,
                    "error": "No se pudo recibir la imagen (HTTP %s)." % e.code}
        except Exception as e:
            return {"ok": False, "paginas": imagenes, "escaner": base, "aviso": aviso_capacidades,
                    "error": "No se pudo recibir la imagen: %s" % e}
        if not contenido:
            break
        ruta = os.path.join(destino, "pagina_%02d.jpg" % numero)
        with open(ruta, "wb") as archivo:
            archivo.write(contenido)
        imagenes.append(ruta)

    aviso = aviso_capacidades if imagenes else "El escaner no devolvio ninguna imagen."
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
