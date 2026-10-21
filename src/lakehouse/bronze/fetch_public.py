"""Land public GitHub files, pinned to a commit SHA, in bronze as-is."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

RAW_URL = "https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{path}"


def fetch_manifest(
    manifest: dict,
    bronze_root: str,
    load_date: date,
    http_get: Callable[[str], tuple[int, bytes]],
) -> list[str]:
    """Fetch every manifest path at its pinned SHA and write it under the load date.

    Destination: {bronze_root}/public/load_date={YYYY-MM-DD}/{repo}/{path}.
    Re-running overwrites the same files. Raises RuntimeError on any non-200.
    """
    owner = manifest["owner"]
    base = Path(bronze_root) / "public" / f"load_date={load_date.isoformat()}"
    written = []
    for source in manifest["sources"]:
        repo, sha = source["repo"], source["sha"]
        for path in source["paths"]:
            status, body = http_get(RAW_URL.format(owner=owner, repo=repo, sha=sha, path=path))
            if status != 200:
                raise RuntimeError(f"{status} for {repo}/{path}")
            dest = base / repo / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            written.append(str(dest))
    return written
