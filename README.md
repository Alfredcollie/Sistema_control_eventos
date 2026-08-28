# Control de Eventos — Empaquetado y despliegue (CI/CD)

Compila la app de escritorio (Windows y macOS) con **GitHub Actions** e inyecta
la configuración y las credenciales mediante **GitHub Secrets**.

## 1. Subir el proyecto a GitHub (primera vez)

El proyecto todavía no es un repositorio Git. Para activar el CI:

```bash
git init
git add .
git commit -m "Primer commit"
```

Luego crea un repositorio **vacío** en GitHub (sin README, sin .gitignore, para no conflictuar) y:

```bash
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

## 2. Configurar los secretos del repositorio

En GitHub → **Settings → Secrets and variables → Actions → New repository secret**, crea estos secretos:

| Secreto | Contenido |
|---|---|
| `SUPABASE_DB_USER` | Usuario del rol de la app principal, formato `rol.<project_ref>` |
| `SUPABASE_DB_PASSWORD` | Contraseña del rol `app_control_eventos` |
| `SUPABASE_LIC_USER` | Usuario del rol de solo-lectura de licencias, formato `rol.<project_ref>` |
| `SUPABASE_LIC_PASSWORD` | Contraseña del rol `app_licencias_lectura` |
| `NUBEFACT_URL` | URL de la API de Nubefact (facturación electrónica) |
| `NUBEFACT_TOKEN` | Token de la API de Nubefact |

> No pongas los valores reales en este archivo ni en el código: van **solo** en los secretos.

## 3. Cómo funciona el build

1. Al hacer push (rama `main`/`master` o un tag `v*`), corre el workflow `.github/workflows/compilar_unificado.yml`.
2. El paso `generar_config_build.py` lee los secretos y los escribe en `config_local.json`.
3. PyInstaller empaqueta la app **con** ese `config_local.json` (config + credenciales) dentro.
4. Genera el `.exe` (Windows) y el `.app`/`.dmg` (macOS), y publica un **Release** en los tags.

## 4. Prioridad de credenciales en la app

La app resuelve las credenciales en este orden (el primero que exista gana):

1. Llavero del sistema (`keyring`).
2. Variables de entorno (`SUPABASE_DB_USER`, etc.).
3. `config_local.json` empaquetado (lo que inyecta el CI).

En desarrollo local, `%APPDATA%\BlackCube\configuracion.json` (o su equivalente en macOS) sobreescribe los valores por defecto.

## 5. Notas de seguridad

- Las credenciales de BD **viajan dentro del binario**: cualquier persona con el `.exe`/`.app` puede extraerlas. Es aceptable para distribución controlada, pero para máxima seguridad la app debería hablar con un **backend/API** en vez de conectarse directo a la base.
- Rota las contraseñas de los roles periódicamente y actualiza los secretos.
- No subas al repo los archivos `crear_roles_app.sql` ni `corregir_rls_licencias.sql` (ya están en `.gitignore`).

## 6. Disparadores del workflow

- `push` a `main`/`master` → compila (sin release).
- `push` de tag `v*` → compila y publica Release.
- `workflow_dispatch` (manual) → opcionalmente indicar un `tag_name` para publicar Release.
