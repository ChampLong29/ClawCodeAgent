"""Content-addressed artifact storage used by trajectories and experiments."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union


class ArtifactIntegrityError(ValueError):
    """Raised when stored artifact bytes do not match their reference."""


@dataclass(frozen=True)
class ArtifactRef:
    """Stable reference to a content-addressed artifact."""

    uri: str
    sha256: str
    size_bytes: int
    media_type: str = "application/octet-stream"
    encoding: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "uri": self.uri,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
        }
        if self.encoding:
            data["encoding"] = self.encoding
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRef":
        return cls(
            uri=str(data["uri"]),
            sha256=str(data["sha256"]),
            size_bytes=int(data["size_bytes"]),
            media_type=str(data.get("media_type", "application/octet-stream")),
            encoding=data.get("encoding"),
        )


class ArtifactStore:
    """Atomic, deduplicating artifact store addressed by SHA-256."""

    URI_PREFIX = "artifact://sha256/"

    def __init__(self, root: Union[str, os.PathLike[str]]):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str = "application/octet-stream",
        encoding: Optional[str] = None,
    ) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        destination = self._path_for_digest(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(
                prefix=f".{digest}.", suffix=".tmp", dir=str(destination.parent)
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return ArtifactRef(
            uri=f"{self.URI_PREFIX}{digest}",
            sha256=digest,
            size_bytes=len(content),
            media_type=media_type,
            encoding=encoding,
        )

    def put_text(
        self,
        content: str,
        *,
        media_type: str = "text/plain",
        encoding: str = "utf-8",
    ) -> ArtifactRef:
        return self.put_bytes(
            content.encode(encoding), media_type=media_type, encoding=encoding
        )

    def put_json(self, value: Any) -> ArtifactRef:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return self.put_text(
            payload, media_type="application/json", encoding="utf-8"
        )

    def get_bytes(self, ref: Union[ArtifactRef, Dict[str, Any], str]) -> bytes:
        artifact_ref = self._coerce_ref(ref)
        content = self._path_for_digest(artifact_ref.sha256).read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != artifact_ref.sha256:
            raise ArtifactIntegrityError(
                f"artifact hash mismatch: expected {artifact_ref.sha256}, got {actual}"
            )
        if len(content) != artifact_ref.size_bytes:
            raise ArtifactIntegrityError(
                f"artifact size mismatch: expected {artifact_ref.size_bytes}, "
                f"got {len(content)}"
            )
        return content

    def get_text(self, ref: Union[ArtifactRef, Dict[str, Any], str]) -> str:
        artifact_ref = self._coerce_ref(ref)
        return self.get_bytes(artifact_ref).decode(artifact_ref.encoding or "utf-8")

    def get_json(self, ref: Union[ArtifactRef, Dict[str, Any], str]) -> Any:
        return json.loads(self.get_text(ref))

    def verify(self, ref: Union[ArtifactRef, Dict[str, Any], str]) -> bool:
        try:
            self.get_bytes(ref)
            return True
        except (FileNotFoundError, ArtifactIntegrityError):
            return False

    def path_for(self, ref: Union[ArtifactRef, Dict[str, Any], str]) -> Path:
        return self._path_for_digest(self._coerce_ref(ref).sha256)

    def _path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("artifact digest must be a lowercase SHA-256 hex string")
        return self.root / "sha256" / digest[:2] / digest

    def _coerce_ref(
        self, ref: Union[ArtifactRef, Dict[str, Any], str]
    ) -> ArtifactRef:
        if isinstance(ref, ArtifactRef):
            return ref
        if isinstance(ref, dict):
            return ArtifactRef.from_dict(ref)
        if not ref.startswith(self.URI_PREFIX):
            raise ValueError(f"unsupported artifact URI: {ref}")
        digest = ref[len(self.URI_PREFIX) :]
        path = self._path_for_digest(digest)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return ArtifactRef(
            uri=ref,
            sha256=digest,
            size_bytes=path.stat().st_size,
            media_type=media_type,
        )
