"""本地加密密钥存储。

用户在网页端输入 API key → 加密写入 `~/.config/booktran/keys.enc`（0600）。
主密钥存 `~/.config/booktran/.master_key`（0600，首次生成）。

安全边界：这是本地工具，默认只监听 127.0.0.1。加密保护的是
"别人扫一眼文件系统"的场景（备份、同步、误分享），不是对抗 root。
"""
from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_KEYS_DIR = Path.home() / ".config" / "booktran"
_ENC_PATH = _KEYS_DIR / "keys.enc"
_MASTER_PATH = _KEYS_DIR / ".master_key"


def _ensure_dir() -> None:
    _KEYS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _KEYS_DIR.chmod(0o700)
    except OSError:
        pass


def _load_or_create_master() -> bytes:
    """读取或生成主密钥（首次运行时生成）。"""
    if _MASTER_PATH.exists():
        return _MASTER_PATH.read_bytes()
    key = Fernet.generate_key()
    _ensure_dir()
    _MASTER_PATH.write_bytes(key)
    try:
        _MASTER_PATH.chmod(0o600)
    except OSError:
        pass
    return key


def _load_encrypted() -> dict[str, str]:
    """读取加密的密钥库，解密为 {provider_or_profile: api_key}。"""
    if not _ENC_PATH.exists():
        return {}
    master = _load_or_create_master()
    f = Fernet(master)
    try:
        raw = f.decrypt(_ENC_PATH.read_bytes())
        return json.loads(raw)
    except (InvalidToken, ValueError):
        return {}


def _save_encrypted(data: dict[str, str]) -> None:
    """加密写入密钥库。"""
    master = _load_or_create_master()
    f = Fernet(master)
    encrypted = f.encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    _ensure_dir()
    _ENC_PATH.write_bytes(encrypted)
    try:
        _ENC_PATH.chmod(0o600)
    except OSError:
        pass


def set_key(name: str, api_key: str) -> None:
    """保存一个密钥（name = provider 名或 profile id）。"""
    data = _load_encrypted()
    data[name] = api_key
    _save_encrypted(data)


def get_key(name: str) -> str | None:
    """读取一个密钥。"""
    return _load_encrypted().get(name)


def delete_key(name: str) -> bool:
    """删除一个密钥。"""
    data = _load_encrypted()
    if name not in data:
        return False
    del data[name]
    _save_encrypted(data)
    return True


def key_exists(name: str) -> bool:
    """检查密钥是否存在（不返回内容）。"""
    return name in _load_encrypted()


def list_key_names() -> list[str]:
    """列出所有已存密钥的名称（不返回内容）。"""
    return list(_load_encrypted().keys())

