#!/usr/bin/env bash
# ADN DMR Peer Server - installer / updater for Debian-based systems:
# VPS (amd64), Raspberry Pi (arm64 / armhf), Odroid XU4 (armhf).
#
# Installs the server from git into /opt/adn-server with its own Python venv, a local
# MariaDB (required for dynamic TG persistence), the echo (parrot 9990) process and
# systemd units, and enables the Tier III trunking MASTER (TRUNKING: true).
#
#   curl -fsSL https://raw.githubusercontent.com/ea5gvk/ADN-DMR-Peer-Server/feat/dmr-tier3-trunking/tools/install.sh \
#     | sudo bash -s -- --server-id 2141
#   sudo bash tools/install.sh --server-id 2141 --trunk-port 62037
#
# Running it again updates the code and dependencies and restarts the services; existing
# adn-server.yaml / adn-echo.yaml and the database are kept as they are.
# Requires Python 3.10+: Debian 12/13, Ubuntu 22.04+, Raspberry Pi OS bookworm, Armbian bookworm/trixie.

set -euo pipefail

REPO_URL="https://github.com/ea5gvk/ADN-DMR-Peer-Server.git"
BRANCH="feat/dmr-tier3-trunking"
INSTALL_DIR="/opt/adn-server"
SVC_USER="adn"
SVC_HOME="/var/lib/adn-server"
LOG_DIR="/var/log/adn-server"
SERVER_ID=""
TRUNK_PORT="62037"
TRUNK_PASS=""
HOTSPOT_PASS="passw0rd"
DB_HOST=""
DB_PORT="3306"
DB_NAME="hbmon"
DB_USER="hbmon"
DB_PASS=""
WITH_ECHO=1
WITH_FFMPEG=1
WITH_FIREWALL=1
SELFSERVICE=0
FORCE=0

usage() {
    cat <<'EOF'
Uso: sudo bash install.sh [opciones]

  --server-id N        GLOBAL.SERVER_ID de este servidor (por defecto, el del ejemplo: 73010)
  --trunk-port N       Puerto UDP del MASTER Tier III (por defecto 62037)
  --trunk-pass TEXTO   Contraseña de los emplazamientos Tier III (por defecto, aleatoria)
  --hotspot-pass TEXTO Contraseña de los hotspots en el proxy 62031 (por defecto passw0rd)
  --branch NOMBRE      Rama de git (por defecto feat/dmr-tier3-trunking)
  --repo URL           Repositorio de git
  --dir RUTA           Directorio de instalación (por defecto /opt/adn-server)
  --db-host HOST       MariaDB externa: no instala MariaDB local (con --db-port, --db-name,
                       --db-user, --db-pass)
  --selfservice        Deja SELF_SERVICE activado (necesita la tabla Clients de adn-monitor)
  --no-echo            No instala el proceso de eco (loro 9990)
  --no-ffmpeg          No instala ffmpeg (solo hace falta para TTS)
  --no-firewall        No abre puertos en ufw / firewalld
  --force              Sigue aunque el directorio venga de otro repositorio
  -h, --help           Esta ayuda
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-id) SERVER_ID="$2"; shift 2 ;;
        --trunk-port) TRUNK_PORT="$2"; shift 2 ;;
        --trunk-pass) TRUNK_PASS="$2"; shift 2 ;;
        --hotspot-pass) HOTSPOT_PASS="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --repo) REPO_URL="$2"; shift 2 ;;
        --dir) INSTALL_DIR="$2"; shift 2 ;;
        --db-host) DB_HOST="$2"; shift 2 ;;
        --db-port) DB_PORT="$2"; shift 2 ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --db-user) DB_USER="$2"; shift 2 ;;
        --db-pass) DB_PASS="$2"; shift 2 ;;
        --selfservice) SELFSERVICE=1; shift ;;
        --no-echo) WITH_ECHO=0; shift ;;
        --no-ffmpeg) WITH_FFMPEG=0; shift ;;
        --no-firewall) WITH_FIREWALL=0; shift ;;
        --force) FORCE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Opción desconocida: $1" >&2; usage >&2; exit 2 ;;
    esac
done

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m[aviso] %s\033[0m\n' "$*" >&2; }
die() { printf '\033[1;31m[error] %s\033[0m\n' "$*" >&2; exit 1; }
as_user() { runuser -u "$SVC_USER" -- env HOME="$SVC_HOME" "$@"; }
rand_hex() { openssl rand -hex "${1:-12}"; }

# Everything runs from main so that `curl ... | bash` reads the whole script before any
# command (apt, mysql, pip) can touch stdin.
main() {
[[ $EUID -eq 0 ]] || die "Ejecuta el script como root (sudo)."
command -v apt-get >/dev/null || die "Solo para sistemas con apt (Debian, Ubuntu, Raspberry Pi OS, Armbian)."
[[ $TRUNK_PORT =~ ^[0-9]+$ ]] || die "--trunk-port debe ser un número."
[[ -z $SERVER_ID || $SERVER_ID =~ ^[0-9]+$ ]] || die "--server-id debe ser un número."
# The passwords go into YAML double-quoted strings and the database one into SQL.
for _p in "$TRUNK_PASS" "$HOTSPOT_PASS" "$DB_PASS"; do
    [[ $_p != *[\"\'\\]* ]] || die "Las contraseñas no pueden llevar comillas ni barras invertidas."
done

ARCH="$(dpkg --print-architecture)"
. /etc/os-release
case "$ARCH" in
    amd64|arm64|armhf) ;;
    i386) warn "i386: cryptography no tiene binarios para 32 bits x86; se compilará con Rust (lento)." ;;
    *) die "Arquitectura $ARCH no soportada." ;;
esac
log "Sistema: ${PRETTY_NAME:-desconocido} ($ARCH)"

# --- System packages ---------------------------------------------------------------
log "Instalando paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
# needrestart (Ubuntu) would otherwise restart unrelated services after apt; only list them.
export NEEDRESTART_MODE=l NEEDRESTART_SUSPEND=1
PKGS=(git ca-certificates curl openssl python3 python3-venv python3-dev python3-pip
      build-essential pkg-config default-libmysqlclient-dev libffi-dev libssl-dev)
[[ $WITH_FFMPEG -eq 1 ]] && PKGS+=(ffmpeg)
[[ -z $DB_HOST && ! -f $INSTALL_DIR/adn-server.yaml ]] && PKGS+=(mariadb-server)
[[ $ARCH == i386 ]] && PKGS+=(rustc cargo)
APT_OPTS=(-y -q --no-install-recommends -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
apt-get update -q
apt-get install "${APT_OPTS[@]}" "${PKGS[@]}"

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python $(python3 -V 2>&1 | cut -d' ' -f2) es demasiado antiguo: hace falta 3.10 o superior (Debian 12+, Ubuntu 22.04+, Raspberry Pi OS bookworm, Armbian bookworm/trixie)."

# --- Service user and directories -------------------------------------------------
log "Usuario $SVC_USER y directorios"
id -u "$SVC_USER" >/dev/null 2>&1 || useradd --system --home-dir "$SVC_HOME" --shell /usr/sbin/nologin "$SVC_USER"
install -d -o "$SVC_USER" -g "$SVC_USER" "$SVC_HOME" "$LOG_DIR"
cd "$SVC_HOME"  # git and pip run as $SVC_USER and fail in an unreadable cwd (e.g. /root)

# --- Code ---------------------------------------------------------------------------
if [[ -d $INSTALL_DIR/.git ]]; then
    log "Actualizando $INSTALL_DIR (rama $BRANCH)"
    chown -R "$SVC_USER:$SVC_USER" "$INSTALL_DIR"
    current_url="$(as_user git -C "$INSTALL_DIR" remote get-url origin)"
    if [[ $current_url != "$REPO_URL" && $FORCE -ne 1 ]]; then
        die "$INSTALL_DIR viene de $current_url, no de $REPO_URL. Usa --force para cambiarlo."
    fi
    as_user git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
    as_user git -C "$INSTALL_DIR" fetch -q origin "$BRANCH"
    as_user git -C "$INSTALL_DIR" checkout -q "$BRANCH" 2>/dev/null \
        || as_user git -C "$INSTALL_DIR" checkout -q -b "$BRANCH" --track "origin/$BRANCH"
    as_user git -C "$INSTALL_DIR" pull -q --ff-only origin "$BRANCH" \
        || die "No se pudo actualizar: hay cambios locales en ficheros del repositorio ($INSTALL_DIR). Revísalos con git status."
elif [[ -d $INSTALL_DIR && -n "$(ls -A "$INSTALL_DIR")" ]]; then
    die "$INSTALL_DIR existe, no está vacío y no es un repositorio git."
else
    log "Descargando $REPO_URL (rama $BRANCH) en $INSTALL_DIR"
    install -d -o "$SVC_USER" -g "$SVC_USER" "$INSTALL_DIR"
    as_user git clone -q --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
log "Código en $(as_user git log -1 --format='%h %s')"

# --- Python environment -------------------------------------------------------------
log "Entorno Python (.venv) y dependencias (en ARM puede tardar unos minutos)"
VENV="$INSTALL_DIR/.venv"
[[ -x $VENV/bin/python ]] || as_user python3 -m venv "$VENV"
as_user "$VENV/bin/pip" install -q --upgrade pip wheel
as_user "$VENV/bin/pip" install -q --prefer-binary -r requirements.txt "mysqlclient>=2.0"

# --- Database settings: from an existing adn-server.yaml, else from the options ----
CONFIG="$INSTALL_DIR/adn-server.yaml"
NEW_CONFIG=0
if [[ -f $CONFIG ]]; then
    eval "$("$VENV/bin/python" - "$CONFIG" <<'PY'
import shlex, sys, yaml
db = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("DATABASE") or {}
for key, name in (("DB_SERVER", "DB_HOST"), ("DB_PORT", "DB_PORT"), ("DB_NAME", "DB_NAME"),
                  ("DB_USERNAME", "DB_USER"), ("DB_PASSWORD", "DB_PASS")):
    print(f"{name}={shlex.quote(str(db.get(key, '')))}")
PY
)"
    log "Se conserva $CONFIG (base de datos $DB_USER@$DB_HOST/$DB_NAME)"
else
    NEW_CONFIG=1
    [[ -n $DB_HOST ]] || DB_HOST="localhost"
    [[ -n $DB_PASS ]] || DB_PASS="$(rand_hex 16)"
fi

# --- Local MariaDB --------------------------------------------------------------------
if [[ ($DB_HOST == localhost || $DB_HOST == 127.0.0.1) && $DB_PASS == *[\'\\]* ]]; then
    warn "La contraseña de MariaDB de $CONFIG lleva comillas o barras: no se toca el usuario $DB_USER."
elif [[ $DB_HOST == localhost || $DB_HOST == 127.0.0.1 ]]; then
    command -v mysql >/dev/null || apt-get install "${APT_OPTS[@]}" mariadb-server
    log "MariaDB local: base de datos $DB_NAME, usuario $DB_USER"
    systemctl enable --now mariadb >/dev/null 2>&1 || systemctl enable --now mysql >/dev/null 2>&1 || true
    mysql -u root <<SQL || die "No se pudo entrar en MariaDB como root (¿tiene contraseña?). Crea a mano la base $DB_NAME y el usuario $DB_USER, y vuelve a lanzar el script."
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
CREATE USER IF NOT EXISTS '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'localhost' IDENTIFIED BY '$DB_PASS';
ALTER USER '$DB_USER'@'127.0.0.1' IDENTIFIED BY '$DB_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'localhost';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$DB_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL
else
    log "MariaDB externa en $DB_HOST:$DB_PORT (no se toca)"
fi

# --- Configuration ------------------------------------------------------------------------
[[ -n $TRUNK_PASS ]] || TRUNK_PASS="$(rand_hex 12)"
ECHO_PASS=""
if [[ $NEW_CONFIG -eq 1 ]]; then
    log "Creando $CONFIG a partir de adn-server.example.yaml (MASTER TRUNK en el puerto $TRUNK_PORT)"
    [[ -n $SERVER_ID ]] || warn "Sin --server-id: se deja SERVER_ID 73010 del ejemplo. Cámbialo en $CONFIG si este servidor va a la red ADN."
    ECHO_PASS="$(rand_hex 12)"
    SERVER_ID="$SERVER_ID" TRUNK_PORT="$TRUNK_PORT" TRUNK_PASS="$TRUNK_PASS" HOTSPOT_PASS="$HOTSPOT_PASS" \
    ECHO_PASS="$ECHO_PASS" DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_NAME="$DB_NAME" DB_USER="$DB_USER" \
    DB_PASS="$DB_PASS" SELFSERVICE="$SELFSERVICE" \
    "$VENV/bin/python" - adn-server.example.yaml "$CONFIG" <<'PY'
# Fill the example in place, line by line, so its comments stay.
import os, re, secrets, sys, yaml

src, dst = sys.argv[1], sys.argv[2]
env = os.environ
lines = open(src, encoding="utf-8").read().splitlines()

# Uncomment the TRUNK block (it ends at the first blank line).
out, in_trunk = [], False
for line in lines:
    if line.strip() == "# TRUNK:":
        in_trunk = True
    if in_trunk and not line.strip():
        in_trunk = False
    out.append(re.sub(r"^(\s*)# ", r"\1", line, count=1) if in_trunk else line)

passwords = {"SYSTEM": env["HOTSPOT_PASS"], "TRUNK": env["TRUNK_PASS"], "ECHO": env["ECHO_PASS"]}
values = {
    "URL_SECURITY": '""', "PORT_SECURITY": '""', "PASS_SECURITY": '""',
    "DB_SERVER": env["DB_HOST"], "DB_PORT": env["DB_PORT"], "DB_NAME": env["DB_NAME"],
    "DB_USERNAME": env["DB_USER"], "DB_PASSWORD": f'"{env["DB_PASS"]}"',
    "USE_SELFSERVICE": "true" if env["SELFSERVICE"] == "1" else "false",
}
if env["SERVER_ID"]:
    values["SERVER_ID"] = env["SERVER_ID"]
section, system = None, None
for i, line in enumerate(out):
    if re.match(r"^[A-Z_]+:\s*$", line):
        section, system = line.rstrip(":").strip(), None
        continue
    m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
    if section == "SYSTEMS" and m:
        system = m.group(1)
        continue
    m = re.match(r"^(\s+)([A-Z_]+): ?(.*)$", line)
    if not m or line.lstrip().startswith("#"):
        continue
    indent, key = m.group(1), m.group(2)
    if section == "SYSTEMS" and key == "PASSPHRASE":
        out[i] = f'{indent}PASSPHRASE: "{passwords.get(system) or secrets.token_hex(12)}"'
    elif section == "SYSTEMS" and system == "TRUNK" and key in ("ENABLED", "PORT"):
        out[i] = f'{indent}{key}: {"true" if key == "ENABLED" else env["TRUNK_PORT"]}'
    elif section != "SYSTEMS" and key in values and indent == "  ":
        out[i] = f"{indent}{key}: {values[key]}"

text = "\n".join(out) + "\n"
cfg = yaml.safe_load(text)
trunk = cfg["SYSTEMS"]["TRUNK"]
assert trunk["TRUNKING"] is True and trunk["ENABLED"] is True and trunk["PORT"] == int(env["TRUNK_PORT"])
leftover = [l for l in text.splitlines() if "<set-in-adn-server.yaml>" in l and not l.lstrip().startswith("#")]
assert not leftover, leftover
with open(dst, "w", encoding="utf-8") as f:
    f.write(text)
PY
    chown "$SVC_USER:$SVC_USER" "$CONFIG"
    chmod 640 "$CONFIG"
else
    # The Tier III MASTER already configured (any name with TRUNKING: true), for the summary.
    eval "$("$VENV/bin/python" - "$CONFIG" <<'PY'
import shlex, sys, yaml
systems = (yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("SYSTEMS") or {}
trunk = next((s for s in systems.values() if isinstance(s, dict) and s.get("TRUNKING") is True
              and s.get("MODE") == "MASTER" and s.get("ENABLED", True)), None)
if trunk:
    print(f"TRUNK_PORT={shlex.quote(str(trunk.get('PORT', '')))}")
    print(f"TRUNK_PASS={shlex.quote(str(trunk.get('PASSPHRASE', '')))}")
else:
    print("TRUNK_PORT=''; TRUNK_PASS=''")
PY
)"
    [[ -n $TRUNK_PORT ]] || warn "$CONFIG no tiene ningún MASTER activo con TRUNKING: true. Añádelo copiando el bloque TRUNK de adn-server.example.yaml."
fi

ECHO_CONFIG="$INSTALL_DIR/adn-echo.yaml"
if [[ $WITH_ECHO -eq 1 && ! -f $ECHO_CONFIG ]]; then
    [[ -n $ECHO_PASS ]] || ECHO_PASS="$("$VENV/bin/python" -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("SYSTEMS") or {}).get("ECHO", {}).get("PASSPHRASE", ""))' "$CONFIG")"
    [[ -n $ECHO_PASS ]] || { warn "$CONFIG no tiene sistema ECHO: no se instala el eco."; WITH_ECHO=0; }
fi
if [[ $WITH_ECHO -eq 1 && ! -f $ECHO_CONFIG ]]; then
    log "Creando $ECHO_CONFIG"
    sed "s|\"<match ECHO.PASSPHRASE>\"|\"$ECHO_PASS\"|" adn-echo.example.yaml > "$ECHO_CONFIG"
    chown "$SVC_USER:$SVC_USER" "$ECHO_CONFIG"
    chmod 640 "$ECHO_CONFIG"
fi

# --- systemd -----------------------------------------------------------------------------
log "Servicios systemd"
systemctl stop adn-echo adn-server >/dev/null 2>&1 || true
write_unit() {
    local name="$1" desc="$2" args="$3" reload="$4" after="$5"
    local unit="/etc/systemd/system/$name.service"
    [[ -f $unit ]] && ! grep -q "tools/install.sh" "$unit" && cp "$unit" "$unit.bak" && warn "$unit existía: copia en $unit.bak"
    cat > "$unit" <<EOF
# Created by $INSTALL_DIR/tools/install.sh
[Unit]
Description=$desc
After=network-online.target $after
Wants=network-online.target

[Service]
User=$SVC_USER
Type=simple
Restart=always
RestartSec=3
SyslogIdentifier=$name
WorkingDirectory=$INSTALL_DIR
Environment=HOME=$SVC_HOME
ExecStart=$VENV/bin/python $INSTALL_DIR/adn-server.py $args
ExecReload=/bin/kill -$reload \$MAINPID
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
}
write_unit adn-server "ADN DMR Peer Server" "-c $CONFIG" HUP "mariadb.service"
[[ $WITH_ECHO -eq 1 ]] && write_unit adn-echo "ADN DMR Echo (playback)" "--echo -c $ECHO_CONFIG" USR2 "adn-server.service"
systemctl daemon-reload

log "Comprobando la configuración (--doctor)"
as_user "$VENV/bin/python" adn-server.py --doctor -c "$CONFIG" \
    || die "--doctor ha encontrado errores (arriba). Corrige $CONFIG y vuelve a lanzar el script."

systemctl enable --now adn-server >/dev/null
[[ $WITH_ECHO -eq 1 ]] && systemctl enable --now adn-echo >/dev/null
sleep 5
if ! systemctl is-active --quiet adn-server; then
    journalctl -u adn-server -n 40 --no-pager >&2 || true
    die "adn-server no ha arrancado (log arriba)."
fi

# --- Firewall --------------------------------------------------------------------------
PORTS=("62031/udp" "62032/udp")
[[ -n $TRUNK_PORT ]] && PORTS+=("$TRUNK_PORT/udp")
if [[ $WITH_FIREWALL -eq 1 ]]; then
    if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        log "Abriendo puertos en ufw: ${PORTS[*]}"
        for p in "${PORTS[@]}"; do ufw allow "$p" >/dev/null; done
    elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
        log "Abriendo puertos en firewalld: ${PORTS[*]}"
        for p in "${PORTS[@]}"; do firewall-cmd -q --permanent --add-port="$p"; done
        firewall-cmd -q --reload
    fi
fi

# --- Summary ---------------------------------------------------------------------------
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SUMMARY="/root/adn-server-install.txt"
umask 077
cat > "$SUMMARY" <<EOF
ADN DMR Peer Server instalado en $INSTALL_DIR (rama $BRANCH, $(as_user git -C "$INSTALL_DIR" log -1 --format=%h))

Puertos UDP (ábrelos también en el cortafuegos del proveedor del VPS o en el router):
  62031  hotspots (proxy integrado)       contraseña: $HOTSPOT_PASS (o la individual de USERS_PASS)
  $TRUNK_PORT  emplazamientos Tier III (TRUNK)  contraseña: $TRUNK_PASS
  62032  OpenBridge (malla)

Configuración: $CONFIG$( [[ $WITH_ECHO -eq 1 ]] && echo ", $ECHO_CONFIG" )
Logs:          journalctl -u adn-server -f    |  $LOG_DIR/adn-server.log
Recargar YAML: systemctl reload adn-server     Reiniciar: systemctl restart adn-server
Actualizar:    sudo bash $INSTALL_DIR/tools/install.sh

DMRGateway del emplazamiento Tier III (DMRGateway.ini, junto a dmrtc):
  [General]
  TrunkingEnabled=1

  [DMR Network 1]
  Enabled=1
  Name=ADN-TIER3
  Address=${IP:-<IP del servidor>}
  Port=$TRUNK_PORT
  Id=<ID del emplazamiento>
  Password=$TRUNK_PASS
  PassAllTG=1
  PassAllTG=2
  PassAllPC=1
  PassAllPC=2
  Location=0

Guía completa: $INSTALL_DIR/docs/es/server/user-guide/tier3-trunking.md
EOF
log "Instalación terminada"
cat "$SUMMARY"
echo
echo "(Este resumen, con las contraseñas, queda en $SUMMARY)"
}

main
