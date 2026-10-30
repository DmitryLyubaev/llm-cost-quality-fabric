r"""The freeze: one hash over everything that decides the capstone's score.

Before a measured run the questions, the expected answers, the scoring rules, the snapshot time,
the data agent definition, the analyst's instructions and both models are hashed. Each run refuses
to start if any of them drifted. Project 3's .NET analyst recomputes these hashes byte for byte, so
this docstring is the specification; `capstone/freeze-vector.json` is the shared test vector.

Algorithm (every hash is SHA-256, written as 64 lowercase hex characters):

Reading files: raw bytes, never text
    Every file is read as raw bytes. A leading UTF-8 BOM (EF BB BF) is hashed like any other bytes
    and is never stripped. WARNING for the C# port: File.ReadAllText and StreamReader strip a BOM
    and decode the text; use File.ReadAllBytes and normalise the bytes. The same goes for the
    instructions read from `git show`: hash the blob's bytes, never a decoded string. There is no
    Unicode normalisation (NFC, NFD...) of any content or any path: bytes and code points are hashed
    exactly as stored.

LF normalisation (applied to file content, in one pass over the bytes)
    Every CR LF pair (0D 0A) becomes one LF (0A), and every CR (0D) that is not followed by an LF
    becomes one LF. Equivalently, replace each match of the pattern \r\n? by \n. Examples:
    "a\r\nb" -> "a\nb"; "a\rb" -> "a\nb"; "a\r\r\nb" -> "a\n\nb" (the first CR is lone, the second
    is part of the CR LF pair); a lone LF is unchanged. Nothing else changes: no trimming, no
    added or removed trailing newline.

Components (eight, in this order; the names are exactly these)
    questions             SHA-256 of the LF-normalised bytes of capstone/questions.json.
    expected              SHA-256 of the LF-normalised bytes of capstone/expected.json.
    rules                 SHA-256 of the LF-normalised bytes of src/lakehouse/capstone/score.py.
    snapshot_time         SHA-256 of the UTF-8 bytes of the snapshot time string, as given (no
                          normalisation: it has no line breaks).
    data_agent            SHA-256 of the concatenation, over the files under the data agent folder
                          taken in sorted path order, of: the file's relative path as UTF-8, one LF,
                          the file's LF-normalised content bytes, one LF. See "The data agent
                          folder" below.
    analyst_instructions  SHA-256 of the LF-normalised bytes of the analyst's
                          src/Portfolio.Analyst/instructions.md (read at a pinned commit).
    model_direct          SHA-256 of the UTF-8 bytes of the direct model name.
    model_foundry         SHA-256 of the UTF-8 bytes of the Foundry model name.

The data agent folder
    - Every file counts, found recursively, with no exclusions: dotfiles and hidden files and
      files in dot-folders are hashed like any other. An empty file contributes its path line and
      an empty content line. A directory contributes nothing by itself, so an empty folder (or one
      holding only empty directories) hashes the empty byte string.
    - A folder that does not exist, or is not a directory, is an error (`DataAgentError`), never an
      empty hash. A symbolic link or reparse point (including Windows junctions) is REJECTED: one
      anywhere beneath the folder, to a file or to a directory, is a `DataAgentError`, and the walk
      never descends into it. (The folder argument itself may be a link.)
    - A path is the file's path relative to the folder: "/"-separated (POSIX style, whatever the
      platform), no leading "./", UTF-8 encoded, case as stored (so "Readme.md" and "readme.md"
      are different files), with no Unicode normalisation.
    - Paths are sorted as whole strings by their UTF-8 BYTES (ordinal byte order), not per folder
      level and not in file system order. So "a.json" sorts before "a/b.md" ("." is 2E, "/" is 2F)
      although a tree walk would visit the folder "a" first. WARNING for the C# port:
      StringComparer.Ordinal compares UTF-16 code units, which orders supplementary characters
      (encoded as surrogates D800..DFFF) before U+E000..U+FFFF, the reverse of UTF-8 byte order. Sort
      by the UTF-8 bytes (for example by comparing the byte arrays) instead.

Overall hash
    Build one line per component, "name=hex" followed by a LF, where hex is the component's hash.
    Sort the lines (ordinal, by UTF-8 bytes; this is the same as sorting by name). The overall
    `hash` is the SHA-256 of the UTF-8 bytes of the sorted lines concatenated.

Short hash
    `short` is the first 12 hex characters of the overall hash.

A freeze is {"components": {name: hex}, "hash": hex, "short": hex12}. `verify_freeze` rebuilds it
from the inputs and raises `FreezeBroken(component)` on the first mismatch.
"""

import hashlib
import os
import re
import stat
from pathlib import Path

COMPONENTS = (
    "questions",
    "expected",
    "rules",
    "snapshot_time",
    "data_agent",
    "analyst_instructions",
    "model_direct",
    "model_foundry",
)
SHORT_LENGTH = 12
RULES_PATH = Path(__file__).resolve().parent / "score.py"


class DataAgentError(Exception):
    """The data agent folder is missing, is not a directory, or holds a symbolic link or reparse point."""


class FreezeBroken(Exception):
    """A frozen component no longer matches; `component` names it ("hash" is the overall hash)."""

    def __init__(self, component: str):
        super().__init__(f"the freeze is broken: {component} changed")
        self.component = component


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_LINE_BREAK = re.compile(rb"\r\n?")


def _lf(data: bytes) -> bytes:
    """CR LF and a lone CR each become one LF, in a single pass (see the module docstring)."""
    return _LINE_BREAK.sub(b"\n", data)


def _file_bytes(source: "str | bytes | Path") -> bytes:
    """A Path is a file to read; a str (UTF-8 encoded) or bytes is the content itself. LF-normalised."""
    if isinstance(source, Path):
        return _lf(source.read_bytes())
    if isinstance(source, str):
        return _lf(source.encode("utf-8"))
    return _lf(bytes(source))


def _raise(error: OSError):
    raise error


def _is_link(path: str) -> bool:
    """A symbolic link, a Windows junction, or any other reparse point."""
    if os.path.islink(path) or os.path.isjunction(path):
        return True
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _data_agent_bytes(folder: Path) -> bytes:
    if not folder.exists():
        raise DataAgentError("the data agent folder does not exist")
    if not folder.is_dir():
        raise DataAgentError("the data agent folder is not a directory")
    files: dict[str, str] = {}
    for current, dirnames, filenames in os.walk(folder, followlinks=False, onerror=_raise):
        # Checked before os.walk descends into `dirnames`, so it never walks into a link or junction.
        for name in dirnames + filenames:
            full = os.path.join(current, name)
            if _is_link(full):
                rel = Path(full).relative_to(folder).as_posix()
                raise DataAgentError(f"the data agent folder holds a symbolic link or reparse point: {rel}")
        for name in filenames:
            full = os.path.join(current, name)
            files[Path(full).relative_to(folder).as_posix()] = full
    out = b""
    for rel in sorted(files, key=lambda r: r.encode("utf-8")):
        with open(files[rel], "rb") as handle:
            out += rel.encode("utf-8") + b"\n" + _lf(handle.read()) + b"\n"
    return out


def build_freeze(
    questions: "str | bytes | Path",
    expected: "str | bytes | Path",
    snapshot_time: str,
    data_agent_dir: Path,
    analyst_instructions: "str | bytes",
    models: dict[str, str],
    rules: "str | bytes | Path | None" = None,
) -> dict:
    """Hash every input (see the module docstring). `models` carries "direct" and "foundry".

    `questions`, `expected`, `rules` and `analyst_instructions` are a Path (read as a file), bytes or the text itself; `rules`
    defaults to this package's score.py.
    """
    components = {
        "questions": _sha(_file_bytes(questions)),
        "expected": _sha(_file_bytes(expected)),
        "rules": _sha(_file_bytes(RULES_PATH if rules is None else rules)),
        "snapshot_time": _sha(snapshot_time.encode("utf-8")),
        "data_agent": _sha(_data_agent_bytes(Path(data_agent_dir))),
        "analyst_instructions": _sha(_file_bytes(analyst_instructions)),
        "model_direct": _sha(models["direct"].encode("utf-8")),
        "model_foundry": _sha(models["foundry"].encode("utf-8")),
    }
    overall = _overall(components)
    return {"components": components, "hash": overall, "short": overall[:SHORT_LENGTH]}


def _overall(components: dict[str, str]) -> str:
    lines = sorted(f"{name}={digest}\n".encode("utf-8") for name, digest in components.items())
    return _sha(b"".join(lines))


def verify_freeze(freeze: dict, **inputs) -> None:
    """Rebuild the freeze from `inputs` (the arguments of build_freeze); raise FreezeBroken on a mismatch."""
    rebuilt = build_freeze(**inputs)
    frozen = freeze.get("components", {})
    for name in COMPONENTS:
        if frozen.get(name) != rebuilt["components"][name]:
            raise FreezeBroken(name)
    for name in sorted(n for n in frozen if n not in COMPONENTS):
        raise FreezeBroken(name)
    if freeze.get("hash") != rebuilt["hash"] or freeze.get("short") != rebuilt["short"]:
        raise FreezeBroken("hash")
