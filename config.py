#!/usr/bin/env python
#
###############################################################################
# Copyright (C) 2026 Joaquin Madrid Belando, EA5GVK <ea5gvk@gmail.com> 
# Copyright (C) 2025 Esteban Mackay, HP3ICC <setcom40@gmail.com> 
# Copyright (C) 2025 Bruno Farias, CS8ABG <cs8abg@gmail.com> 
#   Copyright (C) 2016-2018 Cortney T. Buffington, N0MJS <n0mjs@me.com>
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
###############################################################################

'''
This module generates the configuration data structure for hblink.py and
assoicated programs that use it. It has been seaparated into a different
module so as to keep hblink.py easeier to navigate. This file only needs
updated if the items in the main configuraiton file (usually hblink.cfg)
change.
'''

import configparser
import sys
import os
import re
import const

import socket
import ipaddress 
from socket import gethostbyname
from languages import languages


# Does anybody read this stuff? There's a PEP somewhere that says I should do this.
__author__     = 'Cortney T. Buffington, N0MJS, Forked by Simon Adlem - G7RZU, Forked by Esteban Mackay HP3ICC'
__copyright__  = 'Copyright (c) 2016-2019 Cortney T. Buffington, N0MJS and the K0USY Group, Simon Adlem G7RZU 2020-2023, Esteban Mackay, HP3ICC 2024-2026'
__credits__    = 'Colin Durbridge, G4EML, Steve Zingman, N4IRS; Mike Zingman, N4IRR; Jonathan Naylor, G4KLX; Hans Barthen, DL5DI; Torsten Shultze, DG1HT; Jon Lee, G4TSN; Norman Williams, M6NBP, Eric Craw KF7EEL, Simon Adlem - G7RZU, Bruno Farias CS8ABG, Esteban Mackay HP3ICC, Joaquin Madrid Belando EA5GVK'
__license__    = 'GNU GPLv3'
__maintainer__ = 'Esteban Mackay, HP3ICC and Joaquin Madrid, EA5GVK'
__email__      = 'setcom40@gmail.com, ea5gvk@gmail.com'

# Processing of ALS goes here. It's separated from the acl_build function because this
# code is hblink config-file format specific, and acl_build is abstracted
def process_acls(_config):
    # Global registration ACL
    _config['GLOBAL']['REG_ACL'] = acl_build(_config['GLOBAL']['REG_ACL'], const.PEER_MAX)

    # Global subscriber and TGID ACLs
    for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
        _config['GLOBAL'][acl] = acl_build(_config['GLOBAL'][acl], const.ID_MAX)

    # System level ACLs
    for system in _config['SYSTEMS']:
        # Registration ACLs (which make no sense for peer systems)
        if _config['SYSTEMS'][system]['MODE'] == 'MASTER':
            _config['SYSTEMS'][system]['REG_ACL'] = acl_build(_config['SYSTEMS'][system]['REG_ACL'], const.PEER_MAX)

        # Subscriber and TGID ACLs (valid for all system types)
        for acl in ['SUB_ACL', 'TG1_ACL', 'TG2_ACL']:
            _config['SYSTEMS'][system][acl] = acl_build(_config['SYSTEMS'][system][acl], const.ID_MAX)

# Create an access control list that is programatically useable from human readable:
# ORIGINAL:  'DENY:1-5,3120101,3120124'
# PROCESSED: (False, set([(1, 5), (3120124, 3120124), (3120101, 3120101)]))
def acl_build(_acl, _max):
    if not _acl:
        return(True, [(const.ID_MIN, _max)])

    acl = [] #set()
    sections = _acl.split(':')

    if sections[0] == 'PERMIT':
        action = True
    else:
        action = False

    for entry in sections[1].split(','):
        if entry == 'ALL':
            acl.append((const.ID_MIN, _max))
            break

        elif '-' in entry:
            start,end = entry.split('-')
            start,end = int(start), int(end)
            if (const.ID_MIN <= start <= _max) or (const.ID_MIN <= end <= _max):
                acl.append((start, end))
            else:
                sys.exit('ACL CREATION ERROR, VALUE OUT OF RANGE ({} - {})IN RANGE-BASED ENTRY: {}'.format(const.ID_MIN, _max, entry))
        else:
            id = int(entry)
            if (const.ID_MIN <= id <= _max):
                acl.append((id, id))
            else:
                sys.exit('ACL CREATION ERROR, VALUE OUT OF RANGE ({} - {}) IN SINGLE ID ENTRY: {}'.format(const.ID_MIN, _max, entry))

    return (action, acl)

def IsIPv4Address(ip):
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ValueError as errorCode:
        pass
        return False
    
def IsIPv6Address(ip):
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ValueError as errorCode:
        pass
        return False   

def build_config(_config_file):
    config = configparser.ConfigParser()

    if not config.read(_config_file):
        sys.exit('Configuration file \''+_config_file+'\' is not a valid configuration file! Exiting...')        

    CONFIG = {}
    CONFIG['GLOBAL'] = {}
    CONFIG['REPORTS'] = {}
    CONFIG['LOGGER'] = {}
    CONFIG['ALIASES'] = {}
    CONFIG['SYSTEMS'] = {}
    CONFIG['ALLSTAR'] = {}

    try:
        for section in config.sections():
            if section == 'GLOBAL':
                CONFIG['GLOBAL'].update({
                    'PATH': config.get(section, 'PATH',fallback='./'),
                    'PING_TIME': config.getint(section, 'PING_TIME', fallback=10),
                    'MAX_MISSED': config.getint(section, 'MAX_MISSED', fallback=3),
                    'USE_ACL': config.get(section, 'USE_ACL', fallback=True),
                    'REG_ACL': config.get(section, 'REG_ACL', fallback='PERMIT:ALL'),
                    'SUB_ACL': config.get(section, 'SUB_ACL', fallback='DENY:1'),
                    'TG1_ACL': config.get(section, 'TGID_TS1_ACL', fallback='PERMIT:ALL'),
                    'TG2_ACL': config.get(section, 'TGID_TS2_ACL', fallback='PERMIT:ALL'),
                    'GEN_STAT_BRIDGES': config.getboolean(section, 'GEN_STAT_BRIDGES', fallback=True),
                    'ANNOUNCEMENT_LANGUAGES': config.get(section, 'ANNOUNCEMENT_LANGUAGES', fallback=''),
                    'URL_SECURITY': config.get(section, 'URL_SECURITY', fallback=''),
                    'PORT_SECURITY': config.get(section, 'PORT_SECURITY', fallback=''),
                    'PASS_SECURITY': config.get(section, 'PASS_SECURITY', fallback=''),
                    'USERS_PASS': config.get(section, 'USERS_PASS', fallback='user_passwords.json'),
                    'HASH_ENCRYPT': config.get(section, 'HASH_ENCRYPT', fallback='encryption_key.secret'),
                    'SERVER_ID': config.getint(section, 'SERVER_ID', fallback=0).to_bytes(4, 'big'),
                    'DATA_GATEWAY': config.getboolean(section, 'DATA_GATEWAY', fallback=False),
                    'VALIDATE_SERVER_IDS': config.getboolean(section, 'VALIDATE_SERVER_IDS', fallback=True),
                    'DEBUG_BRIDGES' : config.getboolean(section, 'DEBUG_BRIDGES', fallback=False),
                    'ENABLE_API' : config.getboolean(section, 'ENABLE_API', fallback=False),
                    'TALKER_ALIAS': config.getboolean(section, 'TALKER_ALIAS', fallback=False),
                    'TALKER_ALIAS_MODE': config.get(section, 'TALKER_ALIAS_MODE', fallback='both'),
                    'TALKER_ALIAS_FORMAT': config.get(section, 'TALKER_ALIAS_FORMAT', fallback='{callsign} {fname}'),
                })
                if not CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES']:
                    CONFIG['GLOBAL']['ANNOUNCEMENT_LANGUAGES'] = languages

            elif section == 'REPORTS':
                CONFIG['REPORTS'].update({
                    'REPORT': config.getboolean(section, 'REPORT', fallback=True),
                    'REPORT_INTERVAL': config.getint(section, 'REPORT_INTERVAL', fallback=60),
                    'REPORT_PORT': config.getint(section, 'REPORT_PORT', fallback=4321),
                    'REPORT_CLIENTS': config.get(section, 'REPORT_CLIENTS',fallback='*').split(',')
                })

            elif section == 'LOGGER':
                CONFIG['LOGGER'].update({
                    'LOG_FILE': config.get(section, 'LOG_FILE', fallback='/dev/null'),
                    'LOG_HANDLERS': config.get(section, 'LOG_HANDLERS', fallback='console-timed'),
                    'LOG_LEVEL': config.get(section, 'LOG_LEVEL', fallback='INFO'),
                    'LOG_NAME': config.get(section, 'LOG_NAME', fallback='ADN')
                })


            elif section == 'ALIASES':
                CONFIG['ALIASES'].update({
                    'TRY_DOWNLOAD': config.getboolean(section, 'TRY_DOWNLOAD', fallback=True),
                    'PATH': config.get(section, 'PATH', fallback='./data/'),
                    'PEER_FILE': config.get(section, 'PEER_FILE', fallback='peer_ids.json'),
                    'SUBSCRIBER_FILE': config.get(section, 'SUBSCRIBER_FILE', fallback='subscriber_ids.json'),
                    'TGID_FILE': config.get(section, 'TGID_FILE', fallback='talkgroup_ids.json'),
                    'PEER_URL': config.get(section, 'PEER_URL', fallback='https://servers.adn.systems/peer_ids.json'),
                    'SUBSCRIBER_URL': config.get(section, 'SUBSCRIBER_URL', fallback='https://servers.adn.systems/subscriber_ids.json'),
                    'TGID_URL': config.get(section, 'TGID_URL', fallback='https://servers.adn.systems/talkgroup_ids.json'),
                    'STALE_TIME': config.getint(section, 'STALE_DAYS', fallback=1) * 86400,
                    'SUB_MAP_FILE': config.get(section, 'SUB_MAP_FILE', fallback='sub_map.pkl'),
                    'LOCAL_SUBSCRIBER_FILE': config.get(section, 'LOCAL_SUBSCRIBER_FILE', fallback='local_subscribers.json'),
                    'SERVER_ID_URL': config.get(section, 'SERVER_ID_URL', fallback='https://servers.adn.systems/server_ids.tsv'),
                    'SERVER_ID_FILE': config.get(section, 'SERVER_ID_FILE', fallback='server_ids.tsv'),
                    'CHECKSUM_URL': config.get(section, 'CHECKSUM_URL', fallback='https://servers.adn.systems/file_checksums.json'),
                    'CHECKSUM_FILE': config.get(section, 'CHECKSUM_FILE', fallback='file_checksums.json'),
                    'KEYS_FILE': config.get(section, 'KEYS_FILE', fallback='keys.json')
                })
                
                
            elif section == 'ALLSTAR':
                CONFIG['ALLSTAR'].update({
                    'ENABLED': config.getboolean(section, 'ENABLED', fallback=False),
                    'USER': config.get(section, 'USER', fallback='llcgi'),
                    'PASS': config.get(section, 'PASS', fallback='mypass'),
                    'SERVER': config.get(section, 'SERVER', fallback='my.asl.server'),
                    'PORT': config.getint(section,'PORT', fallback=5038),
                    'NODE' : config.getint(section,'NODE', fallback=0)
            })
                
            elif section == 'PROXY':
                pass

            elif config.getboolean(section, 'ENABLED'):
                if config.get(section, 'MODE') == 'PEER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'LOOSE': config.getboolean(section, 'LOOSE'),
                        'SOCK_ADDR': (gethostbyname(config.get(section, 'IP')), config.getint(section, 'PORT')),
                        'IP': gethostbyname(config.get(section, 'IP')),
                        'PORT': config.getint(section, 'PORT'),
                        'MASTER_SOCKADDR': (gethostbyname(config.get(section, 'MASTER_IP')), config.getint(section, 'MASTER_PORT')),
                        'MASTER_IP': gethostbyname(config.get(section, 'MASTER_IP')),
                        '_MASTER_IP': config.get(section, 'MASTER_IP'),
                        'MASTER_PORT': config.getint(section, 'MASTER_PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        'CALLSIGN': bytes(config.get(section, 'CALLSIGN').ljust(8)[:8], 'utf-8'),
                        'RADIO_ID': config.getint(section, 'RADIO_ID').to_bytes(4, 'big'),
                        'RX_FREQ': bytes(config.get(section, 'RX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_FREQ': bytes(config.get(section, 'TX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_POWER': bytes(config.get(section, 'TX_POWER').rjust(2,'0'), 'utf-8'),
                        'COLORCODE': bytes(config.get(section, 'COLORCODE').rjust(2,'0'), 'utf-8'),
                        'LATITUDE': bytes(config.get(section, 'LATITUDE').ljust(8)[:8], 'utf-8'),
                        'LONGITUDE': bytes(config.get(section, 'LONGITUDE').ljust(9)[:9], 'utf-8'),
                        'HEIGHT': bytes(config.get(section, 'HEIGHT').rjust(3,'0'), 'utf-8'),
                        'LOCATION': bytes(config.get(section, 'LOCATION').ljust(20)[:20], 'utf-8'),
                        'DESCRIPTION': bytes(config.get(section, 'DESCRIPTION').ljust(19)[:19], 'utf-8'),
                        'SLOTS': bytes(config.get(section, 'SLOTS'), 'utf-8'),
                        'URL': bytes(config.get(section, 'URL').ljust(124)[:124], 'utf-8'),
                        'SOFTWARE_ID': bytes(config.get(section, 'SOFTWARE_ID').ljust(40)[:40], 'utf-8'),
                        'PACKAGE_ID': bytes(config.get(section, 'PACKAGE_ID').ljust(40)[:40], 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        'OPTIONS': bytes(config.get(section, 'OPTIONS'), 'utf-8'),
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE')
                    }})
                    CONFIG['SYSTEMS'][section].update({'STATS': {
                        'CONNECTION': 'NO',             # NO, RTPL_SENT, AUTHENTICATED, CONFIG-SENT, YES 
                        'CONNECTED': None,
                        'PINGS_SENT': 0,
                        'PINGS_ACKD': 0,
                        'NUM_OUTSTANDING': 0,
                        'PING_OUTSTANDING': False,
                        'LAST_PING_TX_TIME': 0,
                        'LAST_PING_ACK_TIME': 0,
                    }})

                if config.get(section, 'MODE') == 'XLXPEER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED'),
                        'LOOSE': config.getboolean(section, 'LOOSE'),
                        'SOCK_ADDR': (gethostbyname(config.get(section, 'IP')), config.getint(section, 'PORT')),
                        'IP': gethostbyname(config.get(section, 'IP')),
                        'PORT': config.getint(section, 'PORT'),
                        'MASTER_SOCKADDR': (gethostbyname(config.get(section, 'MASTER_IP')), config.getint(section, 'MASTER_PORT')),
                        'MASTER_IP': gethostbyname(config.get(section, 'MASTER_IP')),
                        '_MASTER_IP': config.get(section, 'MASTER_IP'),
                        'MASTER_PORT': config.getint(section, 'MASTER_PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE'), 'utf-8'),
                        'CALLSIGN': bytes(config.get(section, 'CALLSIGN').ljust(8)[:8], 'utf-8'),
                        'RADIO_ID': config.getint(section, 'RADIO_ID').to_bytes(4, 'big'),
                        'RX_FREQ': bytes(config.get(section, 'RX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_FREQ': bytes(config.get(section, 'TX_FREQ').ljust(9)[:9], 'utf-8'),
                        'TX_POWER': bytes(config.get(section, 'TX_POWER').rjust(2,'0'), 'utf-8'),
                        'COLORCODE': bytes(config.get(section, 'COLORCODE').rjust(2,'0'), 'utf-8'),
                        'LATITUDE': bytes(config.get(section, 'LATITUDE').ljust(8)[:8], 'utf-8'),
                        'LONGITUDE': bytes(config.get(section, 'LONGITUDE').ljust(9)[:9], 'utf-8'),
                        'HEIGHT': bytes(config.get(section, 'HEIGHT').rjust(3,'0'), 'utf-8'),
                        'LOCATION': bytes(config.get(section, 'LOCATION').ljust(20)[:20], 'utf-8'),
                        'DESCRIPTION': bytes(config.get(section, 'DESCRIPTION').ljust(19)[:19], 'utf-8'),
                        'SLOTS': bytes(config.get(section, 'SLOTS'), 'utf-8'),
                        'URL': bytes(config.get(section, 'URL').ljust(124)[:124], 'utf-8'),
                        'SOFTWARE_ID': bytes(config.get(section, 'SOFTWARE_ID').ljust(40)[:40], 'utf-8'),
                        'PACKAGE_ID': bytes(config.get(section, 'PACKAGE_ID').ljust(40)[:40], 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME'),
                        'XLXMODULE': config.getint(section, 'XLXMODULE'),
                        'OPTIONS': '',
                        'USE_ACL': config.getboolean(section, 'USE_ACL'),
                        'SUB_ACL': config.get(section, 'SUB_ACL'),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL'),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL'),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE')
                    }})
                    CONFIG['SYSTEMS'][section].update({'XLXSTATS': {
                        'CONNECTION': 'NO',             # NO, RTPL_SENT, AUTHENTICATED, CONFIG-SENT, YES 
                        'CONNECTED': None,
                        'PINGS_SENT': 0,
                        'PINGS_ACKD': 0,
                        'NUM_OUTSTANDING': 0,
                        'PING_OUTSTANDING': False,
                        'LAST_PING_TX_TIME': 0,
                        'LAST_PING_ACK_TIME': 0,
                    }})

                elif config.get(section, 'MODE') == 'MASTER':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED', fallback=True  ),
                        'REPEAT': config.getboolean(section, 'REPEAT', fallback=True),
                        'MAX_PEERS': config.getint(section, 'MAX_PEERS', fallback=1),
                        'IP': config.get(section, 'IP', fallback='127.0.0.1'),
                        'PORT': config.getint(section, 'PORT', fallback=56400),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE', fallback='passw0rd'), 'utf-8'),
                        'GROUP_HANGTIME': config.getint(section, 'GROUP_HANGTIME',fallback=5),
                        'USE_ACL': config.getboolean(section, 'USE_ACL', fallback=False),
                        'REG_ACL': config.get(section, 'REG_ACL', fallback=''),
                        'SUB_ACL': config.get(section, 'SUB_ACL', fallback=''),
                        'TG1_ACL': config.get(section, 'TGID_TS1_ACL', fallback=''),
                        'TG2_ACL': config.get(section, 'TGID_TS2_ACL', fallback=''),
                        'DEFAULT_UA_TIMER': config.getint(section, 'DEFAULT_UA_TIMER', fallback=10),
                        'SINGLE_MODE': config.getboolean(section, 'SINGLE_MODE', fallback=True),
                        'VOICE_IDENT': config.getboolean(section, 'VOICE_IDENT', fallback=True),
                        'TS1_STATIC': config.get(section,'TS1_STATIC', fallback=''),
                        'TS2_STATIC': config.get(section,'TS2_STATIC', fallback=''),
                        'DEFAULT_REFLECTOR': config.getint(section, 'DEFAULT_REFLECTOR'),
                        'GENERATOR': config.getint(section, 'GENERATOR', fallback=100),
                        'ANNOUNCEMENT_LANGUAGE': config.get(section, 'ANNOUNCEMENT_LANGUAGE', fallback='en_GB'),
                        'ALLOW_UNREG_ID': config.getboolean(section,'ALLOW_UNREG_ID', fallback=False),
                        'PROXY_CONTROL' : config.getboolean(section,'PROXY_CONTROL', fallback=False),
                        'OVERRIDE_IDENT_TG': config.get(section, 'OVERRIDE_IDENT_TG', fallback=False)
                    }})
                    CONFIG['SYSTEMS'][section].update({'PEERS': {}})
                    
                elif config.get(section, 'MODE') == 'OPENBRIDGE':
                    CONFIG['SYSTEMS'].update({section: {
                        'MODE': config.get(section, 'MODE'),
                        'ENABLED': config.getboolean(section, 'ENABLED', fallback=True),
                        'NETWORK_ID': config.getint(section, 'NETWORK_ID').to_bytes(4, 'big'),
                        #'OVERRIDE_SERVER_ID': config.getint(section, 'OVERRIDE_SERVER_ID').to_bytes(4, 'big'),
                        'IP': config.get(section, 'IP', fallback=''),
                        'PORT': config.getint(section, 'PORT'),
                        'PASSPHRASE': bytes(config.get(section, 'PASSPHRASE').ljust(20,'\x00')[:20], 'utf-8'),
                        #'TARGET_SOCK': (gethostbyname(config.get(section, 'TARGET_IP')), config.getint(section, 'TARGET_PORT')),
                        'TARGET_IP': config.get(section, 'TARGET_IP'),
                        'TARGET_PORT': config.getint(section, 'TARGET_PORT'),
                        'USE_ACL': config.getboolean(section, 'USE_ACL', fallback=False),
                        'SUB_ACL': config.get(section, 'SUB_ACL', fallback=''),
                        'TG1_ACL': config.get(section, 'TGID_ACL', fallback=''),
                        'TG2_ACL': 'PERMIT:ALL',
                        'RELAX_CHECKS': config.getboolean(section, 'RELAX_CHECKS', fallback=True),
                        'ENHANCED_OBP': config.getboolean(section, 'ENHANCED_OBP',fallback=True),
                        'VER' : config.getint(section, 'PROTO_VER', fallback=5)
                    }})

                    if CONFIG['SYSTEMS'][section]['VER'] in (0,2,3) or CONFIG['SYSTEMS'][section]['VER'] > 5:
                        sys.exit('(%s) PROTO_VER not valid',section)
                    
                    try:
                        
                        if CONFIG['SYSTEMS'][section]['IP'] == '::':
                            try:
                                addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET6, socket.IPPROTO_IP)
                            except gaierror:
                                addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET, socket.IPPROTO_IP)
                        
                        elif CONFIG['SYSTEMS'][section]['IP'] and IsIPv6Address(CONFIG['SYSTEMS'][section]['IP']):
                            addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET6, socket.IPPROTO_IP)
                                
                        elif not CONFIG['SYSTEMS'][section]['IP'] or IsIPv4Address(CONFIG['SYSTEMS'][section]['IP']):
                            addr_info = socket.getaddrinfo(CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'],socket.AF_INET, socket.IPPROTO_IP)
                        else:
                            raise
                        
                        family, socktype, proto, canonname, sockaddr = addr_info[0]
                        CONFIG['SYSTEMS'][section]['TARGET_IP'] = sockaddr[0]
                        
                        if CONFIG['SYSTEMS'][section]['IP'] == '::' and IsIPv4Address(CONFIG['SYSTEMS'][section]['TARGET_IP']):
                                CONFIG['SYSTEMS'][section]['TARGET_IP'] = '::ffff:' + CONFIG['SYSTEMS'][section]['TARGET_IP']
                        
                        CONFIG['SYSTEMS'][section]['TARGET_SOCK'] = (CONFIG['SYSTEMS'][section]['TARGET_IP'],CONFIG['SYSTEMS'][section]['TARGET_PORT'])
                    
                    except:
                        CONFIG['SYSTEMS'][section]['TARGET_IP'] = False
                        CONFIG['SYSTEMS'][section]['TARGET_SOCK'] = (CONFIG['SYSTEMS'][section]['TARGET_IP'], CONFIG['SYSTEMS'][section]['TARGET_PORT'])
                        
    
    except configparser.Error as err:
        sys.exit('Error processing configuration file -- {}'.format(err))
        
    process_acls(CONFIG)

    _voice_cfg_dir = os.path.dirname(os.path.abspath(_config_file))
    _voice_cfg_file = os.path.join(_voice_cfg_dir, 'voice.cfg')

    voice_config = configparser.ConfigParser()
    if os.path.isfile(_voice_cfg_file):
        voice_config.read(_voice_cfg_file)
        print('(CONFIG) Voice configuration loaded from {}'.format(_voice_cfg_file))
    else:
        print('(CONFIG) Voice configuration file not found ({}), using defaults (announcements/recording disabled)'.format(_voice_cfg_file))

    _voice_section = 'VOICE'
    _has_voice = voice_config.has_section(_voice_section)

    _ann_nums = []
    _tts_nums = []
    if _has_voice:
        for _key in voice_config.options(_voice_section):
            _key_upper = _key.upper()
            _m = re.match(r'^ANNOUNCEMENT(\d*)_ENABLED$', _key_upper)
            if _m:
                _ann_nums.append(1 if _m.group(1) == '' else int(_m.group(1)))
            _m = re.match(r'^TTS_ANNOUNCEMENT(\d+)_ENABLED$', _key_upper)
            if _m:
                _tts_nums.append(int(_m.group(1)))
    _ann_nums = sorted(set(_ann_nums))
    _tts_nums = sorted(set(_tts_nums))

    _voice_data = {
        '_ANN_NUMS': list(_ann_nums),
        '_TTS_NUMS': list(_tts_nums),
    }

    for _ann_num in _ann_nums:
        _prefix = 'ANNOUNCEMENT' if _ann_num == 1 else 'ANNOUNCEMENT{}'.format(_ann_num)
        _voice_data['{}_ENABLED'.format(_prefix)] = voice_config.getboolean(_voice_section, '{}_ENABLED'.format(_prefix), fallback=False) if _has_voice else False
        _voice_data['{}_FILE'.format(_prefix)] = voice_config.get(_voice_section, '{}_FILE'.format(_prefix), fallback='locucion') if _has_voice else 'locucion'
        _voice_data['{}_TG'.format(_prefix)] = voice_config.getint(_voice_section, '{}_TG'.format(_prefix), fallback=9) if _has_voice else 9
        _voice_data['{}_MODE'.format(_prefix)] = voice_config.get(_voice_section, '{}_MODE'.format(_prefix), fallback='hourly') if _has_voice else 'hourly'
        _voice_data['{}_INTERVAL'.format(_prefix)] = voice_config.getint(_voice_section, '{}_INTERVAL'.format(_prefix), fallback=3600) if _has_voice else 3600
        _voice_data['{}_LANGUAGE'.format(_prefix)] = voice_config.get(_voice_section, '{}_LANGUAGE'.format(_prefix), fallback='es_ES') if _has_voice else 'es_ES'

    _voice_data.update({
        'RECORDING_ENABLED': voice_config.getboolean(_voice_section, 'RECORDING_ENABLED', fallback=False) if _has_voice else False,
        'RECORDING_TG': voice_config.getint(_voice_section, 'RECORDING_TG', fallback=9) if _has_voice else 9,
        'RECORDING_TIMESLOT': voice_config.getint(_voice_section, 'RECORDING_TIMESLOT', fallback=2) if _has_voice else 2,
        'RECORDING_FILE': voice_config.get(_voice_section, 'RECORDING_FILE', fallback='grabacion') if _has_voice else 'grabacion',
        'RECORDING_LANGUAGE': voice_config.get(_voice_section, 'RECORDING_LANGUAGE', fallback='es_ES') if _has_voice else 'es_ES',
        'TTS_VOCODER_CMD': voice_config.get(_voice_section, 'TTS_VOCODER_CMD', fallback='') if _has_voice else '',
        'TTS_AMBESERVER_HOST': voice_config.get(_voice_section, 'TTS_AMBESERVER_HOST', fallback='') if _has_voice else '',
        'TTS_AMBESERVER_PORT': voice_config.getint(_voice_section, 'TTS_AMBESERVER_PORT', fallback=2460) if _has_voice else 2460,
        'TTS_VOLUME': voice_config.getint(_voice_section, 'TTS_VOLUME', fallback=-3) if _has_voice else -3,
        'TTS_SPEED': voice_config.getfloat(_voice_section, 'TTS_SPEED', fallback=1.0) if _has_voice else 1.0,
    })

    for _tts_num in _tts_nums:
        _prefix = 'TTS_ANNOUNCEMENT{}'.format(_tts_num)
        _voice_data['{}_ENABLED'.format(_prefix)] = voice_config.getboolean(_voice_section, '{}_ENABLED'.format(_prefix), fallback=False) if _has_voice else False
        _voice_data['{}_FILE'.format(_prefix)] = voice_config.get(_voice_section, '{}_FILE'.format(_prefix), fallback='texto{}'.format(_tts_num)) if _has_voice else 'texto{}'.format(_tts_num)
        _voice_data['{}_TG'.format(_prefix)] = voice_config.getint(_voice_section, '{}_TG'.format(_prefix), fallback=9) if _has_voice else 9
        _voice_data['{}_MODE'.format(_prefix)] = voice_config.get(_voice_section, '{}_MODE'.format(_prefix), fallback='hourly') if _has_voice else 'hourly'
        _voice_data['{}_INTERVAL'.format(_prefix)] = voice_config.getint(_voice_section, '{}_INTERVAL'.format(_prefix), fallback=3600) if _has_voice else 3600
        _voice_data['{}_LANGUAGE'.format(_prefix)] = voice_config.get(_voice_section, '{}_LANGUAGE'.format(_prefix), fallback='es_ES') if _has_voice else 'es_ES'

    CONFIG['GLOBAL'].update(_voice_data)

    return CONFIG


def reload_voice_config(CONFIG, config_file):
    _voice_cfg_dir = os.path.dirname(os.path.abspath(config_file))
    _voice_cfg_file = os.path.join(_voice_cfg_dir, 'voice.cfg')

    if not os.path.isfile(_voice_cfg_file):
        return False

    voice_config = configparser.ConfigParser()
    try:
        voice_config.read(_voice_cfg_file)
    except Exception:
        return False

    _voice_section = 'VOICE'
    _has_voice = voice_config.has_section(_voice_section)
    if not _has_voice:
        return False

    _voice_keys = {}

    _ann_nums = []
    _tts_nums = []
    for _key in voice_config.options(_voice_section):
        _key_upper = _key.upper()
        _m = re.match(r'^ANNOUNCEMENT(\d*)_ENABLED$', _key_upper)
        if _m:
            _ann_nums.append(1 if _m.group(1) == '' else int(_m.group(1)))
        _m = re.match(r'^TTS_ANNOUNCEMENT(\d+)_ENABLED$', _key_upper)
        if _m:
            _tts_nums.append(int(_m.group(1)))
    _ann_nums = sorted(set(_ann_nums))
    _tts_nums = sorted(set(_tts_nums))

    _voice_keys['_ANN_NUMS'] = list(_ann_nums)
    _voice_keys['_TTS_NUMS'] = list(_tts_nums)

    for _ann_num in _ann_nums:
        _prefix = 'ANNOUNCEMENT' if _ann_num == 1 else 'ANNOUNCEMENT{}'.format(_ann_num)
        _voice_keys['{}_ENABLED'.format(_prefix)] = voice_config.getboolean(_voice_section, '{}_ENABLED'.format(_prefix), fallback=False)
        _voice_keys['{}_FILE'.format(_prefix)] = voice_config.get(_voice_section, '{}_FILE'.format(_prefix), fallback='locucion')
        _voice_keys['{}_TG'.format(_prefix)] = voice_config.getint(_voice_section, '{}_TG'.format(_prefix), fallback=9)
        _voice_keys['{}_MODE'.format(_prefix)] = voice_config.get(_voice_section, '{}_MODE'.format(_prefix), fallback='hourly')
        _voice_keys['{}_INTERVAL'.format(_prefix)] = voice_config.getint(_voice_section, '{}_INTERVAL'.format(_prefix), fallback=3600)
        _voice_keys['{}_LANGUAGE'.format(_prefix)] = voice_config.get(_voice_section, '{}_LANGUAGE'.format(_prefix), fallback='es_ES')

    _voice_keys['RECORDING_ENABLED'] = voice_config.getboolean(_voice_section, 'RECORDING_ENABLED', fallback=False)
    _voice_keys['RECORDING_TG'] = voice_config.getint(_voice_section, 'RECORDING_TG', fallback=9)
    _voice_keys['RECORDING_TIMESLOT'] = voice_config.getint(_voice_section, 'RECORDING_TIMESLOT', fallback=2)
    _voice_keys['RECORDING_FILE'] = voice_config.get(_voice_section, 'RECORDING_FILE', fallback='grabacion')
    _voice_keys['RECORDING_LANGUAGE'] = voice_config.get(_voice_section, 'RECORDING_LANGUAGE', fallback='es_ES')

    _voice_keys['TTS_VOCODER_CMD'] = voice_config.get(_voice_section, 'TTS_VOCODER_CMD', fallback='')
    _voice_keys['TTS_AMBESERVER_HOST'] = voice_config.get(_voice_section, 'TTS_AMBESERVER_HOST', fallback='')
    _voice_keys['TTS_AMBESERVER_PORT'] = voice_config.getint(_voice_section, 'TTS_AMBESERVER_PORT', fallback=2460)
    _voice_keys['TTS_VOLUME'] = voice_config.getint(_voice_section, 'TTS_VOLUME', fallback=-3)
    _voice_keys['TTS_SPEED'] = voice_config.getfloat(_voice_section, 'TTS_SPEED', fallback=1.0)

    for _tts_num in _tts_nums:
        _prefix = 'TTS_ANNOUNCEMENT{}'.format(_tts_num)
        _voice_keys['{}_ENABLED'.format(_prefix)] = voice_config.getboolean(_voice_section, '{}_ENABLED'.format(_prefix), fallback=False)
        _voice_keys['{}_FILE'.format(_prefix)] = voice_config.get(_voice_section, '{}_FILE'.format(_prefix), fallback='texto{}'.format(_tts_num))
        _voice_keys['{}_TG'.format(_prefix)] = voice_config.getint(_voice_section, '{}_TG'.format(_prefix), fallback=9)
        _voice_keys['{}_MODE'.format(_prefix)] = voice_config.get(_voice_section, '{}_MODE'.format(_prefix), fallback='hourly')
        _voice_keys['{}_INTERVAL'.format(_prefix)] = voice_config.getint(_voice_section, '{}_INTERVAL'.format(_prefix), fallback=3600)
        _voice_keys['{}_LANGUAGE'.format(_prefix)] = voice_config.get(_voice_section, '{}_LANGUAGE'.format(_prefix), fallback='es_ES')

    _old_ann = CONFIG['GLOBAL'].get('_ANN_NUMS', [])
    for _old_num in _old_ann:
        if _old_num not in _ann_nums:
            _old_prefix = 'ANNOUNCEMENT' if _old_num == 1 else 'ANNOUNCEMENT{}'.format(_old_num)
            for _sfx in ('_ENABLED', '_FILE', '_TG', '_MODE', '_INTERVAL', '_LANGUAGE'):
                CONFIG['GLOBAL'].pop('{}{}'.format(_old_prefix, _sfx), None)
    _old_tts = CONFIG['GLOBAL'].get('_TTS_NUMS', [])
    for _old_num in _old_tts:
        if _old_num not in _tts_nums:
            _old_prefix = 'TTS_ANNOUNCEMENT{}'.format(_old_num)
            for _sfx in ('_ENABLED', '_FILE', '_TG', '_MODE', '_INTERVAL', '_LANGUAGE'):
                CONFIG['GLOBAL'].pop('{}{}'.format(_old_prefix, _sfx), None)

    CONFIG['GLOBAL'].update(_voice_keys)
    return True


# Used to run this file direclty and print the config,
# which might be useful for debugging
if __name__ == '__main__':
    import sys
    import os
    import argparse
    from pprint import pprint
    from dmr_utils3.utils import int_id
    
    # Change the current directory to the location of the application
    os.chdir(os.path.dirname(os.path.realpath(sys.argv[0])))

    # CLI argument parser - handles picking up the config file from the command line, and sending a "help" message
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', action='store', dest='CONFIG_FILE', help='/full/path/to/config.file (usually adn.cfg)')
    cli_args = parser.parse_args()


    # Ensure we have a path for the config file, if one wasn't specified, then use the execution directory
    if not cli_args.CONFIG_FILE:
        cli_args.CONFIG_FILE = os.path.dirname(os.path.abspath(__file__))+'/config/adn.cfg'
    
    CONFIG = build_config(cli_args.CONFIG_FILE)
    pprint(CONFIG)
    
    def acl_check(_id, _acl):
        id = int_id(_id)
        for entry in _acl[1]:
            if entry[0] <= id <= entry[1]:
                return _acl[0]
        return not _acl[0]
        
    print(acl_check(b'\x00\x01\x37', CONFIG['GLOBAL']['TG1_ACL']))
