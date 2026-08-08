"""Object storage for uploaded evidence: photos, signed sheets, certificates.

Two changes from the original inline implementation:

* **It is async.** The previous code called the `requests` library -- which
  blocks -- from inside `async def` handlers. Every upload and download stalled
  the entire event loop for the duration of the round trip, so one slow
  attachment fetch delayed every other request the process was serving. These
  use `httpx.AsyncClient`.

* **It is swappable.** `EmergentStorage` reproduces the existing behaviour
  exactly and stays the default. `S3Storage` is the migration path off the
  platform. `NullStorage` lets the app run and the suite pass with no keys.
"""
import logging
import os
from abc import ABC, abstractmethod
from typing import Optional, Tuple

import httpx

from . import _sigv4

MIME_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "pdf": "application/pdf", "heic": "image/heic",
}


class StorageUnavailable(RuntimeError):
    """Raised when the configured backend cannot serve a request."""


class StorageProvider(ABC):
    name = "abstract"

    @abstractmethod
    async def put(self, path: str, data: bytes, content_type: str) -> dict:
        """Store bytes and return provider metadata (must include 'path')."""

    @abstractmethod
    async def get(self, path: str) -> Tuple[bytes, str]:
        """Return (bytes, content_type)."""

    async def delete(self, path: str) -> None:
        """Remove an object. Optional -- the app soft-deletes file records."""
        raise NotImplementedError

    async def healthy(self) -> bool:
        return True


class EmergentStorage(StorageProvider):
    """The Emergent platform object store (current default)."""

    name = "emergent"
    BASE = "https://integrations.emergentagent.com/objstore/api/v1/storage"

    def __init__(self, key: str, app_name: str = "haulcheck"):
        self._emergent_key = key
        self._app_name = app_name
        self._storage_key: Optional[str] = None

    async def _auth(self) -> str:
        """Exchange the platform key for a storage key, once."""
        if self._storage_key:
            return self._storage_key
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(f"{self.BASE}/init", json={"emergent_key": self._emergent_key})
            r.raise_for_status()
            self._storage_key = r.json()["storage_key"]
        return self._storage_key

    async def put(self, path: str, data: bytes, content_type: str) -> dict:
        key = await self._auth()
        async with httpx.AsyncClient(timeout=120) as http:
            r = await http.put(f"{self.BASE}/objects/{path}",
                               headers={"X-Storage-Key": key, "Content-Type": content_type},
                               content=data)
            r.raise_for_status()
            return r.json()

    async def get(self, path: str) -> Tuple[bytes, str]:
        key = await self._auth()
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(f"{self.BASE}/objects/{path}", headers={"X-Storage-Key": key})
            r.raise_for_status()
            return r.content, r.headers.get("Content-Type", "application/octet-stream")

    async def healthy(self) -> bool:
        try:
            await self._auth()
            return True
        except Exception:
            return False


class S3Storage(StorageProvider):
    """S3-compatible storage: Cloudflare R2, AWS S3, MinIO, Backblaze B2.

    Implemented against the S3 REST API through httpx rather than boto3, which
    is synchronous and would reintroduce the blocking this module exists to fix.
    Requests are signed by `_sigv4`, verified against botocore in
    `tests/test_sigv4.py`.

    Addressing is path-style (`<endpoint>/<bucket>/<key>`) because R2 does not
    serve virtual-host style on the default endpoint.
    """

    name = "s3"

    def __init__(self, bucket: str, endpoint: str, access_key: str, secret_key: str,
                 region: str = "auto"):
        self.bucket = bucket
        self.endpoint = endpoint.rstrip("/")
        self.access_key = access_key
        self.secret_key = secret_key
        # R2 accepts only "auto". An empty value signs a scope the server
        # rejects, with an error that does not mention the region.
        self.region = region or "auto"

    def _url(self, path: str) -> str:
        return f"{self.endpoint}/{self.bucket}/{path.lstrip('/')}"

    async def _send(self, method: str, path: str, *, data: bytes = b"",
                    content_type: str = "", timeout: int = 120):
        url = self._url(path)
        headers = {"Content-Type": content_type} if content_type else {}
        headers = _sigv4.sign(
            method=method, url=url, headers=headers, payload=data,
            access_key=self.access_key, secret_key=self.secret_key,
            region=self.region, service="s3")
        async with httpx.AsyncClient(timeout=timeout) as http:
            return await http.request(method, url, headers=headers,
                                      content=data or None)

    @staticmethod
    def _fail(action: str, path: str, response) -> StorageUnavailable:
        # S3 returns the reason in an XML body. Including a slice of it turns
        # "upload failed" into something diagnosable: SignatureDoesNotMatch,
        # NoSuchBucket and AccessDenied need three different fixes, and the
        # status code alone distinguishes none of them.
        return StorageUnavailable(
            f"Storage {action} failed for '{path}': HTTP {response.status_code}. "
            f"{response.text[:300]}")

    async def put(self, path: str, data: bytes, content_type: str) -> dict:
        response = await self._send("PUT", path, data=data, content_type=content_type)
        if response.status_code >= 400:
            raise self._fail("upload", path, response)
        return {
            "path": path,
            "size": len(data),
            "content_type": content_type,
            "etag": response.headers.get("ETag", "").strip('"'),
        }

    async def get(self, path: str) -> Tuple[bytes, str]:
        response = await self._send("GET", path, timeout=60)
        if response.status_code == 404:
            raise StorageUnavailable(f"No stored object at '{path}'.")
        if response.status_code >= 400:
            raise self._fail("download", path, response)
        return response.content, response.headers.get(
            "Content-Type", "application/octet-stream")

    async def delete(self, path: str) -> None:
        response = await self._send("DELETE", path, timeout=60)
        # 404 counts as success: the object is not there, which is the state
        # the caller asked for.
        if response.status_code not in (200, 202, 204, 404):
            raise self._fail("delete", path, response)

    async def healthy(self) -> bool:
        """True when the bucket exists and these credentials can reach it."""
        try:
            response = await self._send("HEAD", "", timeout=15)
            return response.status_code < 400
        except Exception as e:
            logging.error(f"S3 storage health check failed: {e}")
            return False


class NullStorage(StorageProvider):
    """No storage configured.

    Fails uploads with a clear message instead of an opaque 502 from a service
    that was never reachable. Used automatically when no key is present, which
    is the normal state in local development.
    """

    name = "null"

    async def put(self, path: str, data: bytes, content_type: str) -> dict:
        raise StorageUnavailable(
            "File storage is not configured. Set EMERGENT_LLM_KEY (or configure "
            "STORAGE_PROVIDER) to enable uploads."
        )

    async def get(self, path: str) -> Tuple[bytes, str]:
        raise StorageUnavailable("File storage is not configured.")

    async def healthy(self) -> bool:
        return False


_provider: Optional[StorageProvider] = None


def get_provider() -> StorageProvider:
    """The configured storage backend, built once."""
    global _provider
    if _provider is not None:
        return _provider

    choice = (os.environ.get("STORAGE_PROVIDER") or "").strip().lower()
    emergent_key = os.environ.get("EMERGENT_LLM_KEY", "").strip()

    if choice == "s3":
        # os.environ[...] here raised a bare KeyError naming one variable, from
        # inside whichever caller touched storage first -- in practice the
        # health endpoint, so the symptom was a 500 on /api/health and a deploy
        # that never went live, with nothing saying "storage is not configured".
        #
        # .get() rather than `in`: a deploy dashboard set to an empty string is
        # the same mistake as forgetting the variable, and reads the same way.
        required = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY")
        missing = [k for k in required if not (os.environ.get(k) or "").strip()]
        if missing:
            raise RuntimeError(
                "STORAGE_PROVIDER=s3 but these are unset or empty: "
                + ", ".join(missing) + ".\n"
                "Either set them, or set STORAGE_PROVIDER=null to run without "
                "file uploads.\n"
                "This refuses to start rather than quietly falling back: in a "
                "compliance product, silently not storing defect photos and "
                "signed walkaround sheets is worse than not starting."
            )
        _provider = S3Storage(
            bucket=os.environ["S3_BUCKET"], endpoint=os.environ["S3_ENDPOINT"],
            access_key=os.environ["S3_ACCESS_KEY"], secret_key=os.environ["S3_SECRET_KEY"],
            region=os.environ.get("S3_REGION", "auto"))
    elif choice == "null" or (not choice and not emergent_key):
        # No key locally is the normal case, not an error worth crashing over.
        _provider = NullStorage()
        logging.info("Storage provider: null (no EMERGENT_LLM_KEY configured)")
    else:
        _provider = EmergentStorage(emergent_key)

    logging.info(f"Storage provider: {_provider.name}")
    return _provider


def reset_provider() -> None:
    """Drop the cached provider. For tests that change the environment."""
    global _provider
    _provider = None
