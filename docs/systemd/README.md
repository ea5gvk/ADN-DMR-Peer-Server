# Servicios Systemd para ADN Systems DMR

## Archivos de Servicio

- **adn-bridge.service** - Servidor DMR principal (bridge_master.py)
- **adn-dashboard.service** - Panel de administración de contraseñas (dashboard.py)

## Instalación

### 1. Copiar el proyecto a /opt/adn-dmr
```bash
sudo mkdir -p /opt/adn-dmr
sudo cp -r /ruta/del/proyecto/* /opt/adn-dmr/
```

### 2. Copiar los servicios a systemd
```bash
sudo cp adn-bridge.service /etc/systemd/system/
sudo cp adn-dashboard.service /etc/systemd/system/
```

### 3. Recargar systemd
```bash
sudo systemctl daemon-reload
```

### 4. Habilitar servicios para arranque automático
```bash
sudo systemctl enable adn-bridge.service
sudo systemctl enable adn-dashboard.service
```

### 5. Iniciar los servicios
```bash
sudo systemctl start adn-bridge.service
sudo systemctl start adn-dashboard.service
```

## Comandos Útiles

### Ver estado de los servicios
```bash
sudo systemctl status adn-bridge.service
sudo systemctl status adn-dashboard.service
```

### Ver logs en tiempo real
```bash
sudo journalctl -u adn-bridge.service -f
sudo journalctl -u adn-dashboard.service -f
```

### Reiniciar servicios
```bash
sudo systemctl restart adn-bridge.service
sudo systemctl restart adn-dashboard.service
```

### Detener servicios
```bash
sudo systemctl stop adn-bridge.service
sudo systemctl stop adn-dashboard.service
```

### Deshabilitar arranque automático
```bash
sudo systemctl disable adn-bridge.service
sudo systemctl disable adn-dashboard.service
```

## Notas

- Los servicios asumen que el proyecto está instalado en `/opt/adn-dmr`
- Si usas otra ruta, edita `WorkingDirectory` en los archivos .service
- El dashboard escucha en el puerto 5000
- Asegúrate de tener las dependencias Python instaladas antes de iniciar
