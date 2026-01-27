from cryptography.fernet import Fernet
import os
import base64
import hashlib

ENCRYPTION_KEY_FILE = 'config/encryption_key.secret'

def get_or_create_key():
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        os.makedirs(os.path.dirname(ENCRYPTION_KEY_FILE), exist_ok=True)
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        return key

def get_fernet():
    key = get_or_create_key()
    return Fernet(key)

def encrypt_password(password):
    if not password:
        return password
    fernet = get_fernet()
    encrypted = fernet.encrypt(password.encode('utf-8'))
    return encrypted.decode('utf-8')

def decrypt_password(encrypted_password):
    if not encrypted_password:
        return encrypted_password
    try:
        fernet = get_fernet()
        decrypted = fernet.decrypt(encrypted_password.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception:
        return encrypted_password

def is_encrypted(value):
    if not value:
        return False
    try:
        fernet = get_fernet()
        fernet.decrypt(value.encode('utf-8'))
        return True
    except Exception:
        return False
