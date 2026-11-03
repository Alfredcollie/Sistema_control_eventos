#!/bin/bash
cd "$(dirname "$0")"
python3 configurar_token_github.py || python configurar_token_github.py
echo ""
read -n 1 -s -r -p "Pulse una tecla para cerrar..."
