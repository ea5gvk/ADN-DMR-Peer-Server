#!/bin/bash
#
############################################################################### 
#
#   This program is free software; you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program; if not, write to the Free Software Foundation,
#   Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
#
###############################################################################
echo remove ipv6...
bash -c "$(curl -fsSLk https://gitlab.com/hp3icc/emq-TE1/-/raw/main/install/ipv6off.sh)" &&

echo ADN-DMR-Peer-Server Docker installer...
if ! command -v docker &> /dev/null; then
    echo "Docker no está instalado. Instalando..."
    bash -c "$(curl -fsSLk https://gitlab.com/hp3icc/emq-TE1/-/raw/main/install/docker.sh)"
else
    echo "Docker ya está instalado."
fi

echo Make config directory...
mkdir -p /etc/ADN-Systems/certs &&
chmod -R 755 /etc/ADN-Systems &&

echo make json directory...
mkdir -p /etc/ADN-Systems/data &&
chown 54000:54000 /etc/ADN-Systems/data &&

echo Install /etc/ADN-Systems/adn.cfg ... 
cat << EOF > /etc/ADN-Systems/adn.cfg
[GLOBAL]
PATH: ./
PING_TIME: 10
MAX_MISSED: 3
USE_ACL: True
REG_ACL: PERMIT:ALL
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
GEN_STAT_BRIDGES: True
ANNOUNCEMENT_LANGUAGES:
SERVER_ID: 0000
DATA_GATEWAY: False
VALIDATE_SERVER_IDS: True

# Servidor de seguridad centralizado
URL_SECURITY: 
PORT_SECURITY: 
PASS_SECURITY: 
USERS_PASS: user_passwords.json
HASH_ENCRYPT: encryption_key.secret

[REPORTS]
REPORT: True
REPORT_INTERVAL: 60
REPORT_PORT: 4321
REPORT_CLIENTS: *

[LOGGER]
LOG_FILE: /dev/null
LOG_HANDLERS: console-timed
LOG_LEVEL: DEBUG
LOG_NAME: ADN

[ALIASES]
TRY_DOWNLOAD: True
PATH: ./data/
PEER_FILE: peer_ids.json
SUBSCRIBER_FILE: subscriber_ids.json
TGID_FILE: talkgroup_ids.json
PEER_URL: https://adn.systems/files/peer_ids.json
SUBSCRIBER_URL: https://adn.systems/files/subscriber_ids.json
TGID_URL: https://adn.systems/files/talkgroup_ids.json
SERVER_ID_URL: https://adn.systems/files/server_ids.tsv
CHECKSUM_URL: https://adn.systems/files/file_checksums.json
LOCAL_SUBSCRIBER_FILE: subscriber_ids.json
STALE_DAYS: 1
SUB_MAP_FILE: sub_map.pkl
SERVER_ID_FILE: server_ids.tsv
CHECKSUM_FILE: file_checksums.json
KEYS_FILE: keys.json

#Control server shared allstar instance via dial / AMI
[ALLSTAR]
ENABLED: False
USER:llcgi
PASS: mypass
SERVER: my.asl.server
PORT: 5038
NODE: 0000

[OBP-TEST]
MODE: OPENBRIDGE
ENABLED: False
IP:
PORT: 62044
NETWORK_ID: 1
PASSPHRASE: mypass
TARGET_IP: 
TARGET_PORT: 62044
USE_ACL: True
SUB_ACL: DENY:1
TGID_ACL: DENY:0-82,92-199,800-899,9990-9999,900999
RELAX_CHECKS: True
ENHANCED_OBP: True
PROTO_VER: 5

[SYSTEM]
MODE: MASTER
ENABLED: True
REPEAT: True
MAX_PEERS: 1
EXPORT_AMBE: False
IP: 
PORT: 56400
PASSPHRASE:
GROUP_HANGTIME: 5
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
DEFAULT_UA_TIMER: 60
SINGLE_MODE: False
VOICE_IDENT: False
TS1_STATIC:
TS2_STATIC:
DEFAULT_REFLECTOR: 0
ANNOUNCEMENT_LANGUAGE: en_GB
GENERATOR: 100
ALLOW_UNREG_ID: False
PROXY_CONTROL: False
OVERRIDE_IDENT_TG:

[ECHO]
MODE: MASTER
ENABLED: True
REPEAT: True
MAX_PEERS: 1
EXPORT_AMBE: False
IP: 127.0.0.1
PORT: 54917
PASSPHRASE:
GROUP_HANGTIME: 5
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: DENY:ALL
TGID_TS2_ACL: PERMIT:9990
DEFAULT_UA_TIMER: 1
SINGLE_MODE: True
VOICE_IDENT: False
TS1_STATIC:
TS2_STATIC:9990
DEFAULT_REFLECTOR: 0
ANNOUNCEMENT_LANGUAGE: en_GB
GENERATOR: 0
ALLOW_UNREG_ID: True
PROXY_CONTROL: False
OVERRIDE_IDENT_TG:

[D-APRS]
MODE: MASTER
ENABLED: True
REPEAT: False
MAX_PEERS: 1
EXPORT_AMBE: False
IP:
PORT: 52555
PASSPHRASE:
GROUP_HANGTIME: 0
USE_ACL: True
REG_ACL: DENY:1
SUB_ACL: DENY:1
TGID_TS1_ACL: PERMIT:ALL
TGID_TS2_ACL: PERMIT:ALL
DEFAULT_UA_TIMER: 10
SINGLE_MODE: False
VOICE_IDENT: False
TS1_STATIC:
TS2_STATIC:
DEFAULT_REFLECTOR: 0
ANNOUNCEMENT_LANGUAGE: es_ES
GENERATOR: 2
ALLOW_UNREG_ID: True
PROXY_CONTROL: False
OVERRIDE_IDENT_TG:


EOF
#
echo Install /etc/ADN-Systems/fdmr-mon.cfg ... 
cat << EOF > /etc/ADN-Systems/fdmr-mon.cfg
[GLOBAL]
# Display Bridge status
BRIDGES_INC = False
# Display Peers status
HOMEBREW_INC = True
# Lastheard table on main page                                                  
LASTHEARD_ROWS = 20
# Display empty masters                          
EMPTY_MASTERS = False                          
# TG Count on TOP TG's page
TGCOUNT_ROWS = 20

[FDMR CONNECTION]
# FDMR server's IP Address or hostname
FDMR_IP = adn-server
# FDMR server's TCP reporting socket
FDMR_PORT = 4321

[OPB FILTER]
# if you don't want to show in lastherad received traffic from OBP link put NETWORK ID
# for example: 260210, 260211, 260212
OPB_FILTER =

[FILES]
# Files and stuff for loading alias files for mapping numbers to names
FILES_PATH = ./data
# This files will auto-download
PEER_FILE = peer_ids.json
SUBSCRIBER_FILE = subscriber_ids.json
TGID_FILE = talkgroup_ids.json
# User provided files, if you don't use it, you can comment it.
LOCAL_SUB_FILE = local_subscriber_ids.json
LOCAL_PEER_FILE = local_peer_ids.json
LOCAL_TGID_FILE = local_talkgroup_ids.json
# Number of days before we reload DMR-MARC database files.
RELOAD_TIME = 1
PEER_URL = https://adn.systems/files/peer_ids.json
SUBSCRIBER_URL = https://adn.systems/files/subscriber_ids.json
TGID_URL = https://adn.systems/files/talkgroup_ids.json



[LOGGER]
# Settings for log files
LOG_PATH = /dev/
LOG_FILE = null
LOG_LEVEL = WARN

[WEBSOCKET SERVER]
WEBSOCKET_PORT = 9000
# Frequency to push updates to web clients
FREQUENCY = 1
# Clients are timed out after this many seconds, 0 to disable
CLIENT_TIMEOUT = 0
# SSL configuration
USE_SSL = False
SSL_PATH = ./ssl
SSL_CERTIFICATE = cert.pem
SSL_PRIVATEKEY = key.pem

[DASHBOARD]
  # Dashboard Title
DASHTITLE = "DMR Server"
  # Background image True or False if True put a bk.jpg 1920x1080 in img folder
BACKGROUND = False
  # this defines the default language
  # available languages: en, es, fr, pt, it, nl, de
LANGUAGE = "en"
  # Navbar Title
NAVTITLE= "DMR Server"
  # --Navbar Links--  #
#NAV_LNK_NAME = "Links"
#LINK1 = "Name 1", "http://url.link"
#LINK2 = "Name 2", "https://site.link"
#LINK3 = "Name 3", "https://goaway.link"
  #LINKx put as many as you want
  # World Wide Server List
#SERVER_LIST = "http://url/Hosts.txt"
  # World Wide Bridge List
#BRIDGES_LIST = "https://url/Bridges.csv"
  # World Wide TalkGroups List
#TG_LIST = "https://url/Talkgroups.csv"
#TELEGRAM = "url"
#WHATSAPP = "url"
#FACEBOOK = "url"
#SERVER_LIST = "http://yourwebsite/Hosts.txt"
  # --Footer Links-- #
  # Beginning of footer
#FOOTER1 = "SYSOP <a href='http://your.link'>N0CALL</a>"
  # End of footer
#FOOTER2 = "Your Project <a href='http://your.link'>Project</a>"

EOF


echo Set perms on config directory...
chown -R 54000 /etc/ADN-Systems &&

echo Get docker-compose.yml...
cd /etc/ADN-Systems &&
curl https://raw.githubusercontent.com/Amateur-Digital-Network/ADN-DMR-Peer-Server/develop/docker-configs/docker-compose.yml -o docker-compose.yml &&

if grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
    sed -i "s/^cpu_shares/#cpu_shares/g" /etc/ADN-Systems/docker-compose.yml
    sed -i "s/^mem_reservation/#mem_reservation/g" /etc/ADN-Systems/docker-compose.yml
fi

chmod 755 /etc/cron.daily/lastheard

echo Tune network stack...
cat << EOF > /etc/sysctl.conf &&
net.core.rmem_default=134217728
net.core.rmem_max=134217728
net.core.wmem_max=134217728                       
net.core.rmem_default=134217728
net.core.netdev_max_backlog=250000
net.netfilter.nf_conntrack_udp_timeout=15
net.netfilter.nf_conntrack_udp_timeout_stream=35
EOF

/usr/sbin/sysctl -p &&

echo Run ADN-Systems container...
docker-compose up -d

echo Read notes in /etc/ADN-Systems/docker-compose.yml to understand how to implement extra functionality.
echo ADN-Systems setup complete!
