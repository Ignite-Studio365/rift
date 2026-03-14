import io
import os
import shutil
import uuid
from pathlib import Path
from typing import BinaryIO, Generator, Optional, Tuple
import logging

from rift.core.config import settings
from rift.core.exceptions import StorageError

log = logging.getLogger("rift.storage")


def _safe(user_id: str) -> str:
    return "".join(c for c in user_id if c.isalnum() or c == "-")


class LocalStorage:
    def __init__(self, root: Optional[Path] = None):
        self.root = root or settings.STORAGE_ROOT

    def save_upload(self, user_id: str, data: BinaryIO, filename: str) -> Tuple[str, str, int]:
        ext = Path(filename).suffix.lower()
        name = uuid.uuid4().hex + ext
        dest = self.root / "uploads" / _safe(user_id)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / name
        total = 0
        try:
            with open(path, "wb") as f:
                for chunk in iter(lambda: data.read(4 * 1024 * 1024), b""):
                    f.write(chunk)
                    total += len(chunk)
        except OSError as e:
            raise StorageError(f"Save failed: {e}")
        return str(path), name, total

    def save_render(self, user_id: str, src: str, filename: str) -> Tuple[str, int]:
        dest = self.root / "renders" / _safe(user_id)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / filename
        shutil.copy2(src, path)
        return str(path), path.stat().st_size

    def get_path(self, storage_path: str) -> Optional[Path]:
        p = Path(storage_path)
        if not p.is_absolute():
            p = self.root / p
        return p if p.exists() else None

    def delete(self, storage_path: str) -> bool:
        p = self.get_path(storage_path)
        if p and p.exists():
            p.unlink()
            return True
        return False

    def stream(self, storage_path: str) -> Generator[bytes, None, None]:
        p = self.get_path(storage_path)
        if p is None:
            raise StorageError(f"File not found: {storage_path}")
        with open(p, "rb") as f:
            while True:
                chunk = f.read(16 * 1024 * 1024)
                if not chunk:
                    break
                yield chunk

    def user_bytes(self, user_id: str) -> int:
        total = 0
        for d in ["uploads", "renders"]:
            ud = self.root / d / _safe(user_id)
            if ud.exists():
                for fp in ud.rglob("*"):
                    if fp.is_file():
                        try: total += fp.stat().st_size
                        except: pass
        return total

    def temp_path(self, user_id: str, name: str) -> Path:
        d = self.root / "temp" / _safe(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / name

    def presigned_url(self, storage_path: str, filename: Optional[str] = None, ttl: int = 3600) -> str:
        import hashlib
        token = hashlib.sha256(storage_path.encode()).hexdigest()[:16]
        return f"/api/v1/jobs/dl/{token}"


class R2Storage:
    def __init__(self):
        import boto3
        self.bucket = settings.R2_BUCKET
        self.public_url = settings.R2_PUBLIC_URL
        self.s3 = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.R2_ACCESS_KEY,
            aws_secret_access_key=settings.R2_SECRET_KEY,
            region_name="auto",
        )
        log.info(f"R2 storage: bucket={self.bucket}")

    def _key(self, user_id: str, subdir: str, name: str) -> str:
        return f"users/{_safe(user_id)}/{subdir}/{name}"

    def save_upload(self, user_id: str, data: BinaryIO, filename: str) -> Tuple[str, str, int]:
        import mimetypes
        ext = Path(filename).suffix.lower()
        name = uuid.uuid4().hex + ext
        key = self._key(user_id, "uploads", name)
        ct = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        data.seek(0, 2); size = data.tell(); data.seek(0)
        self.s3.upload_fileobj(data, self.bucket, key, ExtraArgs={"ContentType": ct})
        return key, name, size

    def save_render(self, user_id: str, src: str, filename: str) -> Tuple[str, int]:
        key = self._key(user_id, "renders", filename)
        size = os.path.getsize(src)
        ext = Path(filename).suffix.lower().lstrip(".")
        mime = {"mp4": "video/mp4", "mov": "video/quicktime", "avi": "video/x-msvideo",
                "mkv": "video/x-matroska", "webm": "video/webm"}.get(ext, "video/mp4")
        with open(src, "rb") as f:
            self.s3.upload_fileobj(f, self.bucket, key, ExtraArgs={"ContentType": mime})
        return key, size

    def get_path(self, storage_path: str) -> Optional[Path]:
        return None

    def delete(self, storage_path: str) -> bool:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=storage_path)
            return True
        except Exception:
            return False

    def stream(self, storage_path: str) -> Generator[bytes, None, None]:
        resp = self.s3.get_object(Bucket=self.bucket, Key=storage_path)
        body = resp["Body"]
        while True:
            chunk = body.read(16 * 1024 * 1024)
            if not chunk:
                break
            yield chunk

    def presigned_url(self, storage_path: str, filename: Optional[str] = None, ttl: int = 3600) -> str:
        params = {"Bucket": self.bucket, "Key": storage_path}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)

    def user_bytes(self, user_id: str) -> int:
        prefix = f"users/{_safe(user_id)}/"
        total = 0
        pager = self.s3.get_paginator("list_objects_v2")
        for page in pager.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                total += obj.get("Size", 0)
        return total

    def temp_path(self, user_id: str, name: str) -> Path:
        import tempfile
        return Path(tempfile.gettempdir()) / name


def get_storage():
    if settings.STORAGE_BACKEND == "r2" and settings.R2_BUCKET:
        return R2Storage()
    return LocalStorage()