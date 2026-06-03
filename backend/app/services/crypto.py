"""密码加密/解密工具 — 使用 Fernet (AES-128-CBC + HMAC)"""
from cryptography.fernet import Fernet
from app.config import settings

_fernet = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(settings.TASK_ENCRYPTION_KEY.encode())
    return _fernet


def encrypt_password(plain: str) -> str:
    """加密明文密码，返回 base64 字符串"""
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt_password(cipher: str) -> str:
    """解密 base64 密码，返回明文"""
    if not cipher:
        return ""
    return _get_fernet().decrypt(cipher.encode()).decode()
