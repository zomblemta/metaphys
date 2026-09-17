"""受邀用户密码散列；仅保存带盐 scrypt，不保存原密码。"""

import hashlib
import hmac
import secrets


def hash_password(password):
    if len(password) < 12:
        raise ValueError("密码至少 12 字符")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def verify_password(password, encoded):
    try:
        scheme, salt, expected = encoded.split("$")
        if scheme != "scrypt" or len(password) > 1024:
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False
