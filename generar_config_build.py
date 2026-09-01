# -*- coding: utf-8 -*-
"""
GENERAR_CONFIG_BUILD.PY
Genera el config_local.json final para el empaquetado, inyectando los valores
secretos desde variables de entorno (GitHub Secrets).

Se ejecuta SOLO en el CI (GitHub Actions), justo antes de PyInstaller.
No debe ejecutarse en las máquinas de los usuarios finales.
"""
import json
import os

BASE = "config_local.json"

# clave en config_local.json  ->  variable de entorno (GitHub Secret)
SECRETOS = {
    "supabase_db_user": "SUPABASE_DB_USER",
    "supabase_db_password": "SUPABASE_DB_PASSWORD",
    "supabase_lic_user": "SUPABASE_LIC_USER",
    "supabase_lic_password": "SUPABASE_LIC_PASSWORD",
    "url_api_fe": "NUBEFACT_URL",
    "token_api_fe": "NUBEFACT_TOKEN",
}


def main():
    if not os.path.exists(BASE):
        print(f"AVISO: no existe {BASE}; no se inyectaron secretos.")
        return

    with open(BASE, "r", encoding="utf-8") as f:
        config = json.load(f)

    detalle = []
    for clave, env in SECRETOS.items():
        valor = os.environ.get(env)
        if valor:
            config[clave] = valor
            if "user" in clave:
                detalle.append(f"{clave}=len{len(valor)}:{valor[:3]}...{valor[-3:]}")
            else:
                detalle.append(f"{clave}=len{len(valor)}")

    with open(BASE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

    print("config_local.json generado. Detalle: " + (", ".join(detalle) if detalle else "(ninguno)"))


if __name__ == "__main__":
    main()
