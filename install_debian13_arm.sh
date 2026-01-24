#!/bin/bash
#
# Script de instalacion para ADN Systems DMR Peer Server
# Debian 13 (Trixie) - ARM64/ARMv7
#
# Este script instala las dependencias necesarias para compilar
# y ejecutar el servidor DMR en sistemas ARM con Debian 13
#

set -e

echo "=============================================="
echo " ADN Systems DMR Peer Server"
echo " Instalador para Debian 13 (Trixie) ARM"
echo "=============================================="
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "Error: Este script debe ejecutarse como root (sudo)"
    exit 1
fi

echo "[1/4] Actualizando repositorios..."
apt-get update

echo ""
echo "[2/4] Instalando dependencias de compilacion..."
apt-get install -y \
    build-essential \
    python3-dev \
    python3-pip \
    python3-venv \
    libffi-dev \
    libssl-dev \
    git

echo ""
echo "[3/4] Instalando dependencias de Python..."
pip3 install --break-system-packages -r requirements.txt

echo ""
echo "[4/4] Verificando instalacion..."
python3 -c "import bitarray; import twisted; import flask; print('Todas las dependencias instaladas correctamente')"

echo ""
echo "=============================================="
echo " Instalacion completada exitosamente"
echo "=============================================="
echo ""
echo "Para iniciar el servidor:"
echo "  python3 bridge_master.py -c ./config/adn.cfg"
echo ""
echo "Para iniciar el dashboard:"
echo "  python3 dashboard.py"
echo ""
