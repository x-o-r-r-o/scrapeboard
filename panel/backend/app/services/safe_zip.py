"""Safe archive helpers for worker uploads."""

from __future__ import annotations

import zipfile
from pathlib import Path


class UnsafeArchiveError(ValueError):
    pass


def safe_extract_csv_zip(
    zip_path: Path,
    dest_dir: Path,
    *,
    max_members: int = 200,
    max_uncompressed_bytes: int = 200 * 1024 * 1024,
) -> list[str]:
    """Extract only *.csv members; reject Zip Slip and oversized archives.

    Returns list of extracted basenames.
    """
    dest = dest_dir.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    total = 0

    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = zf.infolist()
        if len(infos) > max_members:
            raise UnsafeArchiveError(f"Too many archive members ({len(infos)} > {max_members})")

        for info in infos:
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            base = Path(name).name
            if not base or base in (".", "..") or base.startswith("."):
                continue
            if not base.lower().endswith(".csv"):
                continue
            if info.file_size < 0:
                raise UnsafeArchiveError("Invalid member size")
            total += int(info.file_size)
            if total > max_uncompressed_bytes:
                raise UnsafeArchiveError("Uncompressed archive too large")

            target = (dest / base).resolve()
            try:
                target.relative_to(dest)
            except ValueError as e:
                raise UnsafeArchiveError(f"Illegal path in archive: {name}") from e

            with zf.open(info, "r") as src, open(target, "wb") as out:
                remaining = int(info.file_size)
                while remaining > 0:
                    chunk = src.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    out.write(chunk)
                    remaining -= len(chunk)
            written.append(base)

    return written
