"""Land public GitHub files, pinned to a commit SHA, in bronze as-is."""

from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote

RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"


def _local_write(dest: str, body: bytes) -> None:
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


def _checked_segments(path: str) -> list[str]:
    """The `/`-separated segments of a manifest path; ValueError if it could leave its folder."""
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or PurePosixPath(path).is_absolute()
        or PureWindowsPath(path).drive
    ):
        raise ValueError(f"manifest path must be relative: {path!r}")
    segments = path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"manifest path must not contain '..' or empty segments: {path!r}")
    return segments


def fetch_manifest(
    manifest: dict,
    bronze_root: str,
    load_date: date,
    http_get: Callable[[str], tuple[int, bytes]],
    write: Callable[[str, bytes], None] | None = None,
) -> list[str]:
    """Fetch every manifest path at its pinned SHA and write it under the load date.

    Destination: {bronze_root}/public/load_date={YYYY-MM-DD}/{repo}/{path}, passed to `write(dest,
    body)` (default: a local file writer that creates the folders). Re-running overwrites the same
    files. Raises ValueError for a path that is absolute or has a `..` segment (before any fetch),
    and RuntimeError on any non-200. The URL percent-encodes each path segment.
    """
    write = write or _local_write
    owner = manifest["owner"]
    base = f"{bronze_root.rstrip('/')}/public/load_date={load_date.isoformat()}"
    plan = []
    for source in manifest["sources"]:
        repo, sha = source["repo"], source["sha"]
        for path in source["paths"]:
            segments = _checked_segments(path)
            url_path = "/".join(quote(segment, safe="") for segment in segments)
            plan.append((repo, sha, path, RAW_URL.format(owner=owner, repo=repo, sha=sha, path=url_path)))

    written = []
    for repo, _, path, url in plan:
        status, body = http_get(url)
        if status != 200:
            raise RuntimeError(f"{status} for {repo}/{path}")
        dest = f"{base}/{repo}/{path}"
        write(dest, body)
        written.append(dest)
    return written
