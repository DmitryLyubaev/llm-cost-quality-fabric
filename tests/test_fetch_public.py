import json
import re
from datetime import date
from pathlib import Path

import pytest

from lakehouse.bronze.fetch_public import fetch_manifest
from lakehouse.seeding import SEEDS_DIR

MANIFEST = {
    "owner": "octo",
    "sources": [
        {"repo": "repo-a", "sha": "a" * 40, "paths": ["dir/one.json", "two.md"]},
    ],
}
LOAD_DATE = date(2026, 10, 21)
SEED_MANIFEST = Path(SEEDS_DIR) / "ingest_manifest.json"


def _fake_get(requested=None):
    def http_get(url):
        if requested is not None:
            requested.append(url)
        return 200, ("body of " + url).encode()

    return http_get


def test_writes_pinned_paths(tmp_path):
    requested = []

    written = fetch_manifest(MANIFEST, str(tmp_path), LOAD_DATE, _fake_get(requested))

    base = tmp_path / "public" / "load_date=2026-10-21" / "repo-a"
    one, two = base / "dir" / "one.json", base / "two.md"
    sha = "a" * 40
    url1 = f"https://raw.githubusercontent.com/octo/repo-a/{sha}/dir/one.json"
    url2 = f"https://raw.githubusercontent.com/octo/repo-a/{sha}/two.md"
    assert requested == [url1, url2]
    assert one.read_bytes() == ("body of " + url1).encode()
    assert two.read_bytes() == ("body of " + url2).encode()
    assert sorted(Path(p) for p in written) == sorted([one, two])


def test_rerun_overwrites_same_files(tmp_path):
    fetch_manifest(MANIFEST, str(tmp_path), LOAD_DATE, _fake_get())
    first = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    fetch_manifest(MANIFEST, str(tmp_path), LOAD_DATE, _fake_get())
    second = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    assert len(first) == 2
    assert second == first


class _Untouchable:
    def __bytes__(self):
        raise AssertionError("body was read")

    def __getattribute__(self, name):
        raise AssertionError("body was touched")


def test_non_200_raises_without_reading_body(tmp_path):
    def http_get(url):
        return 404, _Untouchable()

    with pytest.raises(RuntimeError, match=r"404 for repo-a/dir/one\.json"):
        fetch_manifest(MANIFEST, str(tmp_path), LOAD_DATE, http_get)


def test_committed_manifest_is_well_formed():
    manifest = json.loads(SEED_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["owner"] == "DmitryLyubaev"
    assert manifest["sources"]
    seen = set()
    for source in manifest["sources"]:
        assert source["repo"]
        assert re.fullmatch(r"[0-9a-f]{40}", source["sha"])
        assert source["paths"]
        for path in source["paths"]:
            assert path and not path.startswith("/")
            assert (source["repo"], path) not in seen
            seen.add((source["repo"], path))


def test_fetch_uses_writer(tmp_path):
    sink = {}

    written = fetch_manifest(
        MANIFEST, "bronze-root", LOAD_DATE, _fake_get(), write=lambda dest, body: sink.__setitem__(dest, body)
    )

    sha = "a" * 40
    dest1 = "bronze-root/public/load_date=2026-10-21/repo-a/dir/one.json"
    dest2 = "bronze-root/public/load_date=2026-10-21/repo-a/two.md"
    url1 = f"https://raw.githubusercontent.com/octo/repo-a/{sha}/dir/one.json"
    assert sorted(sink) == sorted([dest1, dest2])
    assert sink[dest1] == ("body of " + url1).encode()
    assert sorted(written) == sorted([dest1, dest2])
    assert not (tmp_path / "bronze-root").exists()


@pytest.mark.parametrize("bad", ["../x", "/etc/x", "a/../../x", "a\\..\\x", "C:/x"])
def test_fetch_rejects_traversal(tmp_path, bad):
    manifest = {"owner": "octo", "sources": [{"repo": "repo-a", "sha": "a" * 40, "paths": [bad]}]}
    requested = []

    with pytest.raises(ValueError, match="path"):
        fetch_manifest(manifest, str(tmp_path), LOAD_DATE, _fake_get(requested))

    assert requested == []
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("bad", ["../x", "..", "", "/x", "a/b", "a\\x"])
def test_fetch_rejects_a_repo_that_is_not_one_safe_segment(tmp_path, bad):
    manifest = {"owner": "octo", "sources": [{"repo": bad, "sha": "a" * 40, "paths": ["two.md"]}]}
    requested = []

    with pytest.raises(ValueError, match="repo"):
        fetch_manifest(manifest, str(tmp_path), LOAD_DATE, _fake_get(requested))

    assert requested == []
    assert not list(tmp_path.rglob("*"))


def test_fetch_encodes_spaces(tmp_path):
    manifest = {"owner": "octo", "sources": [{"repo": "repo-a", "sha": "a" * 40, "paths": ["docs/My Report #1.md"]}]}
    requested = []

    written = fetch_manifest(manifest, str(tmp_path), LOAD_DATE, _fake_get(requested))

    assert requested == [f"https://raw.githubusercontent.com/octo/repo-a/{'a' * 40}/docs/My%20Report%20%231.md"]
    assert Path(written[0]).name == "My Report #1.md"
