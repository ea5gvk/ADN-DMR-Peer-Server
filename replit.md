# ADN Systems DMR Peer Server

## Overview

ADN Systems DMR Peer Server is a fork of FreeDMR, implementing a Digital Mobile Radio (DMR) network server. Launched in April 2024 by international amateur radio enthusiasts, it operates on an Open Bridge Protocol (OBP) fostering a decentralized network architecture. The system handles DMR voice and data communication, acting as a conference bridge/reflector that routes traffic between connected systems (repeaters, hotspots, peers) based on configurable bridge rules.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Core Protocol Implementation

**Problem**: Need to implement HomeBrew Repeater Protocol (HBP) and Open Bridge Protocol for DMR communication
**Solution**: Python-based protocol handlers using Twisted framework for asynchronous networking
- `hblink.py` serves as the core protocol engine implementing HBSYSTEM and OPENBRIDGE classes
- Supports both peer and master/server modes for network topology flexibility
- Uses DatagramProtocol for UDP-based DMR packet handling
- Implements custom authentication using SHA256/BLAKE2b hashing with HMAC for security

**Rationale**: Twisted provides battle-tested async I/O suitable for handling multiple simultaneous connections with low latency requirements critical for voice communication.

### Bridge/Routing Architecture

**Problem**: Route voice traffic between multiple DMR systems based on talkgroups and timeslots
**Solution**: Conference bridge pattern implemented in `bridge_master.py` and `bridge.py`
- Traffic routing based on rules defined in `rules.py` configuration
- Systems can be dynamically activated/deactivated on conference bridges
- Supports timeout-based automatic disconnection
- Static routing option available for permanent connections

**Alternatives Considered**: End-to-end routing was rejected in favor of conference bridge approach for better scalability and simpler management.

**Pros**: Flexible routing, easy management, scalable
**Cons**: Systems must individually join bridges (not transparent end-to-end)

### Proxy Architecture

**Problem**: Allow multiple hotspots/repeaters to connect through a single public IP address
**Solution**: UDP proxy implementation in `hotspot_proxy_v2.py` and `hotspot_proxy_self_service.py`
- Dynamic port allocation for incoming connections
- Connection tracking with timeout-based cleanup
- Blacklist support for access control
- Self-service variant includes database-backed client management

**Rationale**: Many amateur radio operators are behind NAT/firewalls; proxy enables connectivity without port forwarding.

### Voice Synthesis System

**Problem**: Generate voice announcements for system events (linking, unlinking, status)
**Solution**: AMBE (Advanced Multi-Band Excitation) voice codec integration
- Pre-recorded indexed voice files in multiple languages (en_GB, es_ES, fr_FR, etc.)
- `read_ambe.py` handles reading/parsing of AMBE audio files
- `mk_voice.py` generates HBP-compliant voice packets from AMBE data
- `playback.py` and `play_ambe.py` handle voice injection into streams

**Rationale**: Provides accessible feedback to users without requiring external TTS systems, maintains compatibility with DMR audio codecs.

### Individual Password Authentication

**Problem**: Need individual password authentication per Radio ID (indicativo) for enhanced security
**Solution**: JSON-based password storage with automatic reload
- Password file: `data/user_passwords.json` stores individual passwords by Radio ID
- Web dashboard: `dashboard.py` provides admin interface for password management
- Auto-reload: Passwords are reloaded every 10 seconds without server restart
- Fallback: Global passphrase used if no individual password is configured
- Mandatory authentication: Individual or global password required (no null passphrase allowed)

**Suffix Support for Radio IDs**:
- Radio IDs of 9 digits (ej: 214501601-214501699) can use the password of their 7-digit base ID (ej: 2145016)
- Useful for users with multiple hotspots/devices using suffix extensions
- Priority: Exact ID match first, then base ID match

**Authentication Priority**:
1. Individual password for exact Radio ID (if configured)
2. Individual password for base ID (7 digits, for 9-digit IDs with suffix)
3. Global passphrase (if configured)
4. Reject connection (no valid credentials)

**Dashboard Credentials**: Stored in `config/dashboard_credentials.json` file (not uploaded to GitHub)
- Supports multiple administrators with the following format:
```json
{
  "admins": [
    {"user": "admin", "password": "admin123"},
    {"user": "operador1", "password": "clave456"}
  ]
}
```
- Also supports legacy single-admin format for backwards compatibility

**Dashboard Web con Acceso Dual**:
- Pagina principal (`/`): Selector entre acceso usuario y administrador
- Acceso Usuario (`/user/login`): Login con Radio ID y contrasena para cambiar su propia contrasena
- Acceso Administrador (`/admin/login`): Login para gestionar todas las contrasenas
- Los usuarios pueden cambiar su contrasena sin necesitar al administrador

**Busqueda por Indicativo (Callsign)**:
- Descarga automatica diaria de la base de datos de RadioID.net (`data/user.csv`)
- Formato CSV con campos: RADIO_ID, CALLSIGN, FIRST_NAME, LAST_NAME, CITY, STATE, COUNTRY
- Funcion de busqueda en el panel de administracion para encontrar Radio IDs por indicativo
- Muestra toda la informacion del registro: nombre, ciudad, estado, pais
- Soporta busqueda parcial (ej: "EA4" muestra todos los indicativos que empiezan por EA4)
- Limita resultados a 20 para mejor rendimiento
- Boton "Usar ID" para autorellenar el formulario de agregar contrasena

### Servidor de Seguridad Centralizado (Rama: descentralizada)

**Problema**: Gestionar credenciales de forma centralizada desde un servidor remoto
**Solucion**: Descarga automatica de archivos de seguridad desde servidor central

**Configuracion en adn.cfg seccion [GLOBAL]**:
- `URL_SECURITY`: IP o DNS del servidor central de seguridad
- `PORT_SECURITY`: Puerto del servidor central
- `PASS_SECURITY`: Contrasena de autenticacion del administrador
- `USERS_PASS`: Nombre del archivo de contrasenas (default: user_passwords.json)
- `HASH_ENCRYPT`: Nombre del archivo de clave de encriptacion (default: encryption_key.secret)

**Comportamiento de Descarga**:
- `encryption_key.secret`: Se descarga SOLO al arrancar el servidor, sobrescribe el existente
- `user_passwords.json`: Se descarga cada 5 minutos, compara contenido antes de actualizar

**Sintaxis de Descarga (curl)**:
```
curl -L "http://URL_SECURITY:PORT_SECURITY/descargar?pass=PASS_SECURITY&encryption_key.secret"
curl -L "http://URL_SECURITY:PORT_SECURITY/descargar?pass=PASS_SECURITY&user_passwords.json"
```

**Seguridad y Tolerancia a Fallos**:
- Si la descarga falla, se conservan los archivos locales existentes
- No se permite contrasena nula (ALLOW_NULL_PASSPHRASE eliminado)
- Autenticacion obligatoria: contrasena individual o global requerida

**Archivos Descargados**:
- `config/encryption_key.secret`: Clave de encriptacion para descifrar contrasenas
- `data/user_passwords.json`: Archivo JSON con contrasenas encriptadas por Radio ID

### Configuration Management

**Problem**: Complex multi-system configuration with ACLs, bridges, and network parameters
**Solution**: INI-based configuration parsed by `config.py`
- Centralized configuration in `config/adn.cfg`
- ACL (Access Control List) processing for registration and talkgroup filtering
- Support for both whitelist and blacklist approaches
- Language preferences and multi-language support via `languages.py`

### Monitoring and Reporting

**Problem**: Need visibility into system operation, connections, and traffic
**Solution**: Network-based reporting protocol
- `report_receiver.py` and `report_sql.py` consume events from the core server
- Pickle-based serialization for config/bridge state transmission
- Opcode-based protocol defined in `reporting_const.py`
- SQL integration option for persistent event logging

**Rationale**: Separates monitoring concerns from core routing logic, enables multiple monitoring tools.

### Asterisk Integration

**Problem**: Integration with Asterisk PBX for advanced features (app_rpt)
**Solution**: Asterisk Manager Interface (AMI) client in `AMI.py`
- Sends RPT (repeater) commands to Asterisk
- Uses Twisted LineReceiver for line-based protocol handling
- Supports node-specific command routing

### API Layer

**Problem**: External systems need programmatic access to server functions
**Solution**: XML-RPC and SOAP API implementation
- `API.py` implements Spyne-based SOAP service (FD_API)
- Peer validation and authentication endpoints
- `api_client.py` provides example XML-RPC client
- Key-based authentication for peer systems

## External Dependencies

### Network Protocols
- **Twisted** (>= 16.3.0) - Asynchronous networking framework for all protocol handling
- **dmr_utils3** (>= 0.1.19) - DMR protocol utilities for packet encoding/decoding, BPTC, Golay error correction

### Database Systems
- **MySQL/MariaDB** - Via twisted.enterprise.adbapi and MySQLdb
  - Used by `proxy_db.py` for self-service proxy client management
  - Stores client registrations, authentication, and connection tracking
  - Optional for report_sql.py event logging

### Remote Object Communication
- **Pyro5** - Python Remote Objects for inter-process communication
  - Used by proxy services for distributed architecture
  - Enables separation of proxy components across processes/hosts

### Third-Party Services
- **radioid.net API** - DMR ID database lookups
  - `peer_ids.json`, `subscriber_ids.json`, `talkgroup_ids.json` downloaded from external sources
  - Cached locally with staleness checking via `utils.py::try_download()`
  - Provides callsign/name resolution for DMR IDs

### Supporting Libraries
- **bitarray/bitstring** - Binary data manipulation for DMR packet construction
- **configparser** - INI file parsing for hblink.cfg
- **setproctitle** - Process naming for easier system monitoring
- **resettabletimer** - Timeout management for connection tracking
- **hashlib/hmac** - Cryptographic functions for authentication

### Language/Voice Assets
- Pre-recorded AMBE voice files in Audio/ directory
- Multiple language support (en_GB, es_ES, fr_FR, de_DE, dk_DK, it_IT, no_NO, pl_PL, se_SE, pt_PT, cy_GB, el_GR, th_TH, CW)
- Voice file indexing via i8n_voice_map.py