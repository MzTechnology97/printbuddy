"""NAS settings (stored in the upstream key/value table) and the shared SMB session."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core.config import settings as app_settings
from backend.app.core.encryption import is_encryption_active, mfa_decrypt, mfa_encrypt
from backend.app.farm.models import FarmWatchedFolder
from backend.app.farm.nas import access
from backend.app.farm.nas.remote import Remote, SmbRemote, split_path
from backend.app.models.library import LibraryFolder

logger = logging.getLogger(__name__)

# Keys are not in the upstream AppSettings schema, so GET /settings never returns them.
KEYS = ("host", "share", "path", "username", "password", "folder_id", "last_result", "cache_mb")
# Cautious default: the Printbuddy container has little free disk.
DEFAULT_CACHE_MB = 4096


def _key(name: str) -> str:
    return f"farm_nas_{name}"


@dataclass(slots=True)
class NasConfig:
    host: str = ""
    share: str = ""
    path: str = ""
    username: str = ""
    password: str = ""  # decrypted; never leaves the backend
    folder_id: int | None = None
    last_result: dict | None = None
    cache_mb: int = DEFAULT_CACHE_MB

    @property
    def configured(self) -> bool:
        return bool(self.host and self.share and self.path)

    @property
    def session_key(self) -> tuple:
        return (self.host, self.share, self.username, self.password, self.path)

    def same_target(self, host: str, share: str, username: str) -> bool:
        """Whether a request points at the stored host/share/user (the stored password may be reused)."""
        return (host.strip().casefold(), share.strip().casefold(), username) == (
            self.host.casefold(),
            self.share.casefold(),
            self.username,
        )


def cache_dir() -> Path:
    """Where used NAS files are kept (LRU); also the library folder's external path."""
    return Path(app_settings.base_dir) / "farm_nas" / "cache"


async def load(db: AsyncSession) -> NasConfig:
    raw = {name: await get_setting(db, _key(name)) for name in KEYS}
    cfg = NasConfig(
        host=raw["host"] or "", share=raw["share"] or "", path=raw["path"] or "", username=raw["username"] or ""
    )
    if raw["password"]:
        cfg.password = mfa_decrypt(raw["password"])
    if raw["folder_id"]:
        cfg.folder_id = int(raw["folder_id"])
    if raw["last_result"]:
        cfg.last_result = json.loads(raw["last_result"])
    if raw["cache_mb"]:
        cfg.cache_mb = int(raw["cache_mb"])
    return cfg


async def save(
    db: AsyncSession,
    *,
    host: str,
    share: str,
    path: str,
    username: str,
    password: str | None,
    interval: int,
    cache_mb: int = DEFAULT_CACHE_MB,
) -> NasConfig:
    """Store the settings and make sure the mirror is a watched library folder.

    ``password=None`` keeps the stored one. Raises ValueError on invalid input.
    """
    parts = split_path(path)
    if not parts:
        raise ValueError("A subfolder is required: the share root is never read")
    split_path(share)
    if password is not None and not is_encryption_active():
        raise ValueError("No encryption key available: refusing to store the NAS password in clear")
    old = await load(db)
    if password is None and old.password and not old.same_target(host, share, username):
        # The stored password must never follow a new host/share/user.
        raise ValueError("Host, share or user changed: type the password again")
    for name, value in (("host", host.strip()), ("share", share.strip()), ("path", "/".join(parts))):
        await set_setting(db, _key(name), value)
    await set_setting(db, _key("username"), username)
    await set_setting(db, _key("cache_mb"), str(cache_mb))
    if password is not None:
        await set_setting(db, _key("password"), mfa_encrypt(password) if password else "")

    cfg = await load(db)
    folder = await db.get(LibraryFolder, cfg.folder_id) if cfg.folder_id else None
    if folder is None:
        cache_dir().mkdir(parents=True, exist_ok=True)
        folder = LibraryFolder(
            name=f"NAS · {parts[-1]}",
            is_external=True,
            external_path=str(cache_dir()),
            # Upstream blocks uploads, moves and deletes in read-only folders: the library never deletes on the NAS.
            external_readonly=True,
        )
        db.add(folder)
        await db.flush()
        await set_setting(db, _key("folder_id"), str(folder.id))
        cfg.folder_id = folder.id
    else:
        folder.name = f"NAS · {parts[-1]}"
    watch = await db.get(FarmWatchedFolder, folder.id)
    if watch is None:
        watch = FarmWatchedFolder(folder_id=folder.id)
        db.add(watch)
    watch.scan_interval_seconds = interval
    await db.commit()
    await refresh_access(db)
    return cfg


def make_remote(host: str, share: str, username: str, password: str) -> Remote:
    """Factory, replaced in tests by a local fake."""
    return SmbRemote(host, share, username, password)


async def refresh_access(db: AsyncSession) -> access.Active | None:
    """Keep the shared session in line with the settings (reused while they don't change)."""
    cfg = await load(db)
    if not cfg.configured or cfg.folder_id is None:
        access.activate(None)
        return None
    current = access.current()
    if current is not None and current.key == cfg.session_key:
        return current
    cache_dir().mkdir(parents=True, exist_ok=True)
    active = access.Active(
        key=cfg.session_key,
        remote=make_remote(cfg.host, cfg.share, cfg.username, cfg.password),
        base=split_path(cfg.path),
        cache_root=cache_dir(),
    )
    access.activate(active)
    return active


async def nas_folder_id(db: AsyncSession) -> int | None:
    cfg = await load(db)
    return cfg.folder_id if cfg.configured else None


async def store_result(db: AsyncSession, result: dict) -> None:
    result = {**result, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    await set_setting(db, _key("last_result"), json.dumps(result))
    await db.commit()
