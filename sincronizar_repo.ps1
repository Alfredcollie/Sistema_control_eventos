# ============================================================
# sincronizar_repo.ps1
# Copia los archivos actualizados a tu carpeta del repo de GitHub.
#
# Uso (solo copiar):
#   .\sincronizar_repo.ps1 -RepoPath "C:\ruta\a\tu\repo"
#
# Copiar y ademas hacer git add/commit/push de una vez:
#   .\sincronizar_repo.ps1 -RepoPath "C:\ruta\a\tu\repo" -Push
# ============================================================
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoPath,

    [switch]$Push,

    [string]$CommitMessage = "Empaquetado + credenciales + instalador Windows"
)

# Archivos que modificamos/creamos (rutas relativas a esta carpeta)
$archivos = @(
    "conexion.py",
    "validacion_licencia.py",
    "config_local.json",
    "generar_config_build.py",
    "instalador_windows.iss",
    "guardar_credenciales.py",
    "README.md",
    ".gitignore",
    ".github/workflows/compilar_unificado.yml"
)

$origen = $PSScriptRoot

if (-not (Test-Path -LiteralPath $RepoPath)) {
    Write-Host "ERROR: no existe la carpeta destino: $RepoPath" -ForegroundColor Red
    exit 1
}

$ok = 0
foreach ($f in $archivos) {
    $src = Join-Path $origen $f
    $dst = Join-Path $RepoPath $f
    $dstDir = Split-Path $dst -Parent

    if (-not (Test-Path -LiteralPath $src)) {
        Write-Host "OMITIDO (no existe en origen): $f" -ForegroundColor Yellow
        continue
    }
    if (-not (Test-Path -LiteralPath $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $src -Destination $dst -Force
    Write-Host "OK  $f" -ForegroundColor Green
    $ok++
}

Write-Host ("Copiados {0} de {1} archivos a: {2}" -f $ok, $archivos.Count, $RepoPath) -ForegroundColor Cyan

if ($Push) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".git"))) {
        Write-Host "ERROR: $RepoPath no es un repo git (no tiene .git)." -ForegroundColor Red
        exit 1
    }
    Write-Host "Ejecutando git add/commit/push en: $RepoPath" -ForegroundColor Cyan
    Push-Location $RepoPath
    try {
        git add .
        git commit -m $CommitMessage
        git push
    } finally {
        Pop-Location
    }
} else {
    Write-Host "Ahora, desde '$RepoPath' ejecuta:" -ForegroundColor Cyan
    Write-Host "  git add ."
    Write-Host '  git commit -m "Empaquetado + credenciales + instalador Windows"'
    Write-Host "  git push"
}
