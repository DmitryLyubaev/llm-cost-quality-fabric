"""The freeze: one hash over everything that decides the capstone's score.

Before a measured run the questions, the expected answers, the scoring rules, the snapshot time,
the data agent definition, the analyst's instructions and both models are hashed. Each run refuses
to start if any of them drifted. Project 3's .NET analyst recomputes these hashes byte for byte, so
this docstring is the specification; `capstone/freeze-vector.json` is the shared test vector.

Algorithm (every hash is SHA-256, written as 64 lowercase hex characters):

LF normalisation
    Replace every CR LF byte pair (0x0D 0x0A) with a single LF (0x0A). A lone CR is kept.
    Nothing else is changed: no trimming, no Unicode normalisation, no BOM handling.

Components (eight, in this order; the names are exactly these)
    questions             SHA-256 of the LF-normalised UTF-8 bytes of capstone/questions.json.
    expected              SHA-256 of the LF-normalised UTF-8 bytes of capstone/expected.json.
    rules                 SHA-256 of the LF-normalised UTF-8 bytes of src/lakehouse/capstone/score.py.
    snapshot_time         SHA-256 of the UTF-8 bytes of the snapshot time string, as given (no
                          normalisation: it has no line breaks).
    data_agent            SHA-256 over the concatenation, for every file under the data agent
                          folder (recursively, files only), taken in sorted order of its relative
                          POSIX path (forward slashes, no leading "./", sorted by the path's UTF-8
                          bytes), of: the path as UTF-8, one LF, the file's LF-normalised content
                          bytes, one LF.
    analyst_instructions  SHA-256 of the LF-normalised UTF-8 bytes of the analyst's
                          src/Portfolio.Analyst/instructions.md (read at a pinned commit).
    model_direct          SHA-256 of the UTF-8 bytes of the direct model name.
    model_foundry         SHA-256 of the UTF-8 bytes of the Foundry model name.

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


class FreezeBroken(Exception):
    """A frozen component no longer matches; `component` names it ("hash" is the overall hash)."""

    def __init__(self, component: str):
        super().__init__(f"the freeze is broken: {component} changed")
        self.component = component


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _file_bytes(source: "str | bytes | Path") -> bytes:
    """A Path is a file to read; a str or bytes is the content itself. Returned LF-normalised."""
    if isinstance(source, Path):
        return _lf(source.read_bytes())
    if isinstance(source, str):
        return _lf(source.encode("utf-8"))
    return _lf(bytes(source))


def _data_agent_bytes(folder: Path) -> bytes:
    files = {p.relative_to(folder).as_posix(): p for p in folder.rglob("*") if p.is_file()}
    out = b""
    for rel in sorted(files, key=lambda r: r.encode("utf-8")):
        out += rel.encode("utf-8") + b"\n" + _lf(files[rel].read_bytes()) + b"\n"
    return out


def build_freeze(
    questions: "str | bytes | Path",
    expected: "str | bytes | Path",
    snapshot_time: str,
    data_agent_dir: Path,
    analyst_instructions: str,
    models: dict[str, str],
    rules: "str | bytes | Path | None" = None,
) -> dict:
    """Hash every input (see the module docstring). `models` carries "direct" and "foundry".

    `questions`, `expected` and `rules` are a Path (read as a file) or the text itself; `rules`
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
    for name in frozen:
        if name not in COMPONENTS:
            raise FreezeBroken(name)
    if freeze.get("hash") != rebuilt["hash"] or freeze.get("short") != rebuilt["short"]:
        raise FreezeBroken("hash")
