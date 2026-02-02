#!/usr/bin/env python
import os
import logging
import urllib.request
import urllib.error
from urllib.parse import quote
import tempfile
import shutil
import socket
from time import time

logger = logging.getLogger(__name__)

DOWNLOAD_INTERVAL_PASSWORDS = 300
DOWNLOAD_INTERVAL_ENCRYPTION = None

_last_passwords_download = 0
_last_passwords_size = 0
_last_passwords_content = None

def resolve_hostname(hostname, timeout=10):
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        ip = socket.gethostbyname(hostname)
        socket.setdefaulttimeout(old_timeout)
        logger.debug('(SECURITY) Resolved %s to %s', hostname, ip)
        return ip
    except socket.gaierror as e:
        logger.error('(SECURITY) DNS resolution failed for %s: %s', hostname, str(e))
        return None
    except Exception as e:
        logger.error('(SECURITY) Unexpected error resolving %s: %s', hostname, str(e))
        return None

def build_download_url(config, filename):
    url_security = config['GLOBAL'].get('URL_SECURITY', '').strip()
    port_security = config['GLOBAL'].get('PORT_SECURITY', '').strip()
    pass_security = config['GLOBAL'].get('PASS_SECURITY', '').strip()
    
    if not url_security or not port_security or not pass_security:
        return None, None
    
    try:
        socket.inet_aton(url_security)
        host = url_security
    except socket.error:
        host = resolve_hostname(url_security)
        if not host:
            logger.error('(SECURITY) Could not resolve hostname: %s', url_security)
            return None, None
    
    url = f"http://{host}:{port_security}/descargar?pass={quote(pass_security, safe='')}&file={filename}"
    return url, url_security

def download_file_safely(url, dest_path, timeout=60):
    try:
        temp_fd, temp_path = tempfile.mkstemp()
        os.close(temp_fd)
        
        try:
            logger.debug('(SECURITY) Attempting download from: %s', url)
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'ADN-Systems-DMR/1.0')
            
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content = response.read()
                
                if len(content) == 0:
                    logger.warning('(SECURITY) Downloaded file is empty, keeping existing: %s', dest_path)
                    os.unlink(temp_path)
                    return False
                
                with open(temp_path, 'wb') as f:
                    f.write(content)
                
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                shutil.move(temp_path, dest_path)
                logger.info('(SECURITY) Successfully downloaded: %s (%d bytes)', dest_path, len(content))
                return True
                
        except urllib.error.HTTPError as e:
            logger.error('(SECURITY) HTTP error downloading %s: %s (Code: %d)', dest_path, str(e), e.code)
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False
        except urllib.error.URLError as e:
            logger.error('(SECURITY) URL error downloading %s: %s', dest_path, str(e.reason))
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False
        except socket.timeout:
            logger.error('(SECURITY) Timeout downloading %s', dest_path)
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False
            
    except Exception as e:
        logger.error('(SECURITY) Unexpected error downloading %s: %s', dest_path, str(e))
        return False

def download_encryption_key(config):
    hash_encrypt = config['GLOBAL'].get('HASH_ENCRYPT', 'encryption_key.secret').strip()
    dest_path = os.path.join('config', hash_encrypt)
    
    url, original_host = build_download_url(config, hash_encrypt)
    if not url:
        logger.debug('(SECURITY) Security server not configured, skipping encryption key download')
        return False
    
    logger.info('(SECURITY) Downloading encryption key from central server...')
    return download_file_safely(url, dest_path)

def download_user_passwords(config, force=False):
    global _last_passwords_download, _last_passwords_size, _last_passwords_content
    
    current_time = time()
    if not force and (current_time - _last_passwords_download) < DOWNLOAD_INTERVAL_PASSWORDS:
        return False
    
    users_pass = config['GLOBAL'].get('USERS_PASS', 'user_passwords.json').strip()
    dest_path = os.path.join('data', users_pass)
    
    url, original_host = build_download_url(config, users_pass)
    if not url:
        logger.debug('(SECURITY) Security server not configured, skipping passwords download')
        return False
    
    try:
        logger.debug('(SECURITY) Downloading passwords from: %s', url)
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'ADN-Systems-DMR/1.0')
        
        with urllib.request.urlopen(req, timeout=60) as response:
            new_content = response.read()
            new_size = len(new_content)
            
            if new_size == 0:
                logger.warning('(SECURITY) Downloaded passwords file is empty, keeping existing')
                _last_passwords_download = current_time
                return False
            
            if _last_passwords_content is not None:
                if new_content == _last_passwords_content:
                    logger.debug('(SECURITY) Passwords file unchanged, no update needed')
                    _last_passwords_download = current_time
                    return False
            
            temp_fd, temp_path = tempfile.mkstemp()
            os.close(temp_fd)
            
            with open(temp_path, 'wb') as f:
                f.write(new_content)
            
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.move(temp_path, dest_path)
            
            _last_passwords_content = new_content
            _last_passwords_size = new_size
            _last_passwords_download = current_time
            
            logger.info('(SECURITY) Successfully updated passwords file: %s (%d bytes)', dest_path, new_size)
            return True
            
    except urllib.error.HTTPError as e:
        logger.error('(SECURITY) HTTP error downloading passwords: %s (Code: %d)', str(e), e.code)
        _last_passwords_download = current_time
        return False
    except urllib.error.URLError as e:
        logger.error('(SECURITY) URL error downloading passwords: %s', str(e.reason))
        _last_passwords_download = current_time
        return False
    except socket.timeout:
        logger.error('(SECURITY) Timeout downloading passwords')
        _last_passwords_download = current_time
        return False
    except Exception as e:
        logger.error('(SECURITY) Unexpected error downloading passwords: %s', str(e))
        _last_passwords_download = current_time
        return False

def init_security_downloads(config):
    url_security = config['GLOBAL'].get('URL_SECURITY', '').strip()
    if not url_security:
        logger.info('(SECURITY) Central security server not configured')
        return False
    
    port_security = config['GLOBAL'].get('PORT_SECURITY', '').strip()
    
    logger.info('(SECURITY) Initializing centralized security downloads...')
    logger.info('(SECURITY) Security server: %s:%s', url_security, port_security)
    
    try:
        socket.inet_aton(url_security)
        logger.info('(SECURITY) Using IP address: %s', url_security)
    except socket.error:
        resolved_ip = resolve_hostname(url_security)
        if resolved_ip:
            logger.info('(SECURITY) Resolved hostname %s to IP: %s', url_security, resolved_ip)
        else:
            logger.error('(SECURITY) Failed to resolve hostname: %s', url_security)
            return False
    
    download_encryption_key(config)
    
    download_user_passwords(config, force=True)
    
    return True

def periodic_password_download(config):
    download_user_passwords(config)
