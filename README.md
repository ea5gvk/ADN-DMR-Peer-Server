# Instalador ADN DMR Peer Server (Tier III)

Instala o actualiza el servidor ADN DMR Peer Server desde la rama
[`feat/dmr-tier3-trunking`](https://github.com/ea5gvk/ADN-DMR-Peer-Server/tree/feat/dmr-tier3-trunking)
en Debian 12/13, Ubuntu 22.04+, Raspberry Pi OS bookworm o Armbian (amd64, arm64, armhf):
servidor en `/opt/adn-server`, MariaDB local, eco (TG 9990), ADN Monitor (dashboard) detrás
de nginx, servicios systemd y el MASTER Tier III (`TRUNKING: true`) en el puerto 62037.

```bash
curl -fsSL https://raw.githubusercontent.com/ea5gvk/ADN-DMR-Peer-Server/installer/install.sh \
  | sudo bash -s -- --server-id 2141
```

Volver a lanzarlo actualiza el código y las dependencias, y conserva `adn-server.yaml`,
`adn-echo.yaml`, `adn-monitor.yaml` y la base de datos. Opciones: `--help`.

Este instalador no forma parte del proyecto ADN; vive solo en esta rama del fork de EA5GVK.
