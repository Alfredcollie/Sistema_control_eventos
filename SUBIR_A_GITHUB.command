#!/bin/bash
# ============================================================
#  SUBIR A GITHUB - solo los archivos que cambiaron
#  (macOS) Doble clic para ejecutar.
# ============================================================
cd "$(dirname "$0")" || exit 1
echo
if command -v python3 >/dev/null 2>&1; then
  python3 "subir_github.py" "$@"
elif command -v python >/dev/null 2>&1; then
  python "subir_github.py" "$@"
else
  echo "No se encontro Python. Instale Python 3 e intente de nuevo."
fi
echo
read -n 1 -s -r -p "Pulse una tecla para cerrar..."
echo
