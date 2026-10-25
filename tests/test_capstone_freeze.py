"""The capstone freeze: its hashing is deterministic, byte for byte, and every component is guarded."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import capstone_freeze
from lakehouse.capstone import freeze as freeze_module
from lakehouse.capstone.freeze import FreezeBroken, build_freeze, verify_freeze

ROOT = Path(__file__).resolve().parent.parent
VECTOR = ROOT / "capstone" / "freeze-vector.json"
COMPONENTS = [
    "questions",
    "expected",
    "rules",
    "snapshot_time",
    "data_agent",
    "analyst_instructions",
    "model_direct",
    "model_foundry",
]

QUESTIONS = '[{"id": "q01"}]\n'
EXPECTED = '{"q01": {"kind": "money", "value": 1.5}}\n'
RULES = "def score_question():\n    return True\n"
INSTRUCTIONS = "You are the analyst.\nAnswer from the data agent only.\n"
SNAPSHOT = "2026-10-25T00:00:00Z"
MODELS = {"direct": "gpt-5.1", "foundry": "gpt-5.1-foundry"}
AGENT_FILES = {"agent.json": '{"name": "capstone"}\n', "sources/lh.json": '{"lakehouse": "lh_capstone"}\n'}


def _write_agent(folder: Path, files=None, newline="\n"):
    for rel, text in (files or AGENT_FILES).items():
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return folder


@pytest.fixture
def agent(tmp_path):
    return _write_agent(tmp_path / "agent")


def _build(agent, **over):
    inputs = {
        "questions": QUESTIONS,
        "expected": EXPECTED,
        "snapshot_time": SNAPSHOT,
        "data_agent_dir": agent,
        "analyst_instructions": INSTRUCTIONS,
        "models": dict(MODELS),
        "rules": RULES,
    }
    inputs.update(over)
    return build_freeze(**inputs), inputs


def test_freeze_shape_and_short(agent):
    freeze, _ = _build(agent)
    assert list(freeze["components"]) == COMPONENTS
    assert all(len(v) == 64 for v in freeze["components"].values())
    assert len(freeze["hash"]) == 64
    assert freeze["short"] == freeze["hash"][:12]
    assert set(freeze) == {"components", "hash", "short"}


def test_hash_is_deterministic_and_ignores_crlf(tmp_path, agent):
    first, _ = _build(agent)
    crlf_agent = _write_agent(tmp_path / "crlf", newline="\r\n")
    second, _ = _build(
        crlf_agent,
        questions=QUESTIONS.replace("\n", "\r\n"),
        expected=EXPECTED.replace("\n", "\r\n"),
        rules=RULES.replace("\n", "\r\n"),
        analyst_instructions=INSTRUCTIONS.replace("\n", "\r\n"),
    )
    assert first == second


def test_hash_ignores_file_creation_order(tmp_path):
    forward = _write_agent(tmp_path / "a", dict(sorted(AGENT_FILES.items())))
    backward = _write_agent(tmp_path / "b", dict(sorted(AGENT_FILES.items(), reverse=True)))
    assert _build(forward)[0] == _build(backward)[0]


def test_data_agent_depends_on_paths_and_content(tmp_path, agent):
    base, _ = _build(agent)
    renamed = _write_agent(
        tmp_path / "renamed",
        {"agent2.json": AGENT_FILES["agent.json"], "sources/lh.json": AGENT_FILES["sources/lh.json"]},
    )
    assert _build(renamed)[0]["components"]["data_agent"] != base["components"]["data_agent"]
    edited = _write_agent(tmp_path / "edited", {**AGENT_FILES, "agent.json": '{"name": "other"}\n'})
    assert _build(edited)[0]["components"]["data_agent"] != base["components"]["data_agent"]


def test_files_may_be_paths(tmp_path, agent):
    from_text, _ = _build(agent)
    paths = {}
    for name, text in (("q.json", QUESTIONS), ("e.json", EXPECTED), ("r.py", RULES)):
        paths[name] = tmp_path / name
        paths[name].write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    from_files, _ = _build(agent, questions=paths["q.json"], expected=paths["e.json"], rules=paths["r.py"])
    assert from_files == from_text


def test_default_rules_are_score_py(agent):
    default, _ = _build(agent, rules=None)
    explicit, _ = _build(agent, rules=ROOT / "src" / "lakehouse" / "capstone" / "score.py")
    assert default == explicit


def _changed(tmp_path, component):
    if component == "data_agent":
        return {"data_agent_dir": _write_agent(tmp_path / "changed", {**AGENT_FILES, "agent.json": "{}\n"})}
    return {
        "questions": {"questions": QUESTIONS + " "},
        "expected": {"expected": EXPECTED + " "},
        "rules": {"rules": RULES + "# x\n"},
        "snapshot_time": {"snapshot_time": "2026-10-26T00:00:00Z"},
        "analyst_instructions": {"analyst_instructions": INSTRUCTIONS + "More.\n"},
        "model_direct": {"models": {**MODELS, "direct": "other"}},
        "model_foundry": {"models": {**MODELS, "foundry": "other"}},
    }[component]


@pytest.mark.parametrize("component", COMPONENTS)
def test_each_component_changed_alone_breaks_and_names_it(tmp_path, agent, component):
    freeze, inputs = _build(agent)
    verify_freeze(freeze, **inputs)  # untouched: passes
    drifted = {**inputs, **_changed(tmp_path, component)}
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **drifted)
    assert component in str(error.value)
    assert error.value.component == component
    # and only that one component moved
    rebuilt = build_freeze(**drifted)
    moved = [n for n in COMPONENTS if rebuilt["components"][n] != freeze["components"][n]]
    assert moved == [component]


def test_a_tampered_overall_hash_is_named(agent):
    freeze, inputs = _build(agent)
    freeze = {**freeze, "hash": "0" * 64}
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **inputs)
    assert error.value.component == "hash"


def test_a_tampered_component_hash_is_named(agent):
    freeze, inputs = _build(agent)
    freeze["components"] = {**freeze["components"], "rules": "0" * 64}
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **inputs)
    assert error.value.component == "rules"


def test_a_missing_component_is_named(agent):
    freeze, inputs = _build(agent)
    del freeze["components"]["model_direct"]
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **inputs)
    assert error.value.component == "model_direct"


# --- the shared test vector (Task 8's .NET analyst copies it and must reproduce every hash) ---


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf(text: str) -> bytes:
    return text.encode("utf-8").replace(b"\r\n", b"\n")


def test_vector_is_reproduced_by_build_freeze(tmp_path):
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    inputs = vector["inputs"]
    folder = _write_agent(tmp_path / "vec", inputs["data_agent"])
    freeze = build_freeze(
        questions=inputs["questions"],
        expected=inputs["expected"],
        rules=inputs["rules"],
        snapshot_time=inputs["snapshot_time"],
        data_agent_dir=folder,
        analyst_instructions=inputs["analyst_instructions"],
        models={"direct": inputs["model_direct"], "foundry": inputs["model_foundry"]},
    )
    assert freeze == vector["expected"]


def test_vector_is_reproduced_independently_of_the_module():
    """Recompute the vector with plain hashlib, as the C# analyst will, from the documented algorithm."""
    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    inputs, expected = vector["inputs"], vector["expected"]
    agent = b""
    for path in sorted(inputs["data_agent"], key=lambda p: p.encode("utf-8")):
        agent += path.encode("utf-8") + b"\n" + _lf(inputs["data_agent"][path]) + b"\n"
    components = {
        "questions": _sha(_lf(inputs["questions"])),
        "expected": _sha(_lf(inputs["expected"])),
        "rules": _sha(_lf(inputs["rules"])),
        "snapshot_time": _sha(inputs["snapshot_time"].encode("utf-8")),
        "data_agent": _sha(agent),
        "analyst_instructions": _sha(_lf(inputs["analyst_instructions"])),
        "model_direct": _sha(inputs["model_direct"].encode("utf-8")),
        "model_foundry": _sha(inputs["model_foundry"].encode("utf-8")),
    }
    lines = sorted(f"{name}={digest}\n" for name, digest in components.items())
    overall = _sha("".join(lines).encode("utf-8"))
    assert components == expected["components"]
    assert overall == expected["hash"]
    assert overall[:12] == expected["short"]


def test_vector_exercises_crlf_and_non_ascii():
    inputs = json.loads(VECTOR.read_text(encoding="utf-8"))["inputs"]
    assert "\r\n" in "".join([inputs["questions"], inputs["rules"], *inputs["data_agent"].values()])
    assert any(ord(c) > 127 for c in json.dumps(inputs, ensure_ascii=False))
    assert len(inputs["data_agent"]) == 2


def test_module_docstring_documents_the_algorithm():
    doc = freeze_module.__doc__
    for word in ("SHA-256", "LF", "UTF-8", "name=hex", "sorted", "12"):
        assert word in doc


# --- the CLI ---


def _git(repo: Path, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def analyst_repo(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "analyst"
    (repo / "src" / "Portfolio.Analyst").mkdir(parents=True)
    _git(repo, "init", "-q")
    target = repo / "src" / "Portfolio.Analyst" / "instructions.md"
    target.write_bytes(b"Frozen instructions\r\nline two\r\n")
    _git(repo, "-c", "core.autocrlf=false", "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    first = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    target.write_bytes(b"Edited later\n")
    _git(repo, "commit", "-q", "-am", "two")
    return repo, first


def _cli_args(tmp_path, agent, repo, commit, command):
    questions = tmp_path / "questions.json"
    expected = tmp_path / "expected.json"
    rules = tmp_path / "score.py"
    questions.write_text(QUESTIONS, encoding="utf-8")
    expected.write_text(EXPECTED, encoding="utf-8")
    rules.write_text(RULES, encoding="utf-8")
    return [
        command,
        "--questions", str(questions),
        "--expected", str(expected),
        "--rules", str(rules),
        "--data-agent-dir", str(agent),
        "--snapshot-time", SNAPSHOT,
        "--analyst-repo", str(repo),
        "--analyst-commit", commit,
        "--model-direct", MODELS["direct"],
        "--model-foundry", MODELS["foundry"],
        "--freeze", str(tmp_path / "freeze.json"),
    ]


def test_cli_freezes_then_verifies_reading_instructions_with_git_show(tmp_path, agent, analyst_repo):
    repo, first = analyst_repo
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "freeze")) == 0
    raw = (tmp_path / "freeze.json").read_bytes()
    written = json.loads(raw)
    assert written["components"]["analyst_instructions"] == _sha(b"Frozen instructions\nline two\n")
    assert raw.endswith(b"\n") and b"\r" not in raw
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "verify")) == 0


def test_cli_verify_fails_naming_the_component(tmp_path, agent, analyst_repo, capsys):
    repo, first = analyst_repo
    capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "freeze"))
    # HEAD's instructions differ from the frozen commit's
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "HEAD", "verify")) == 1
    assert "analyst_instructions" in capsys.readouterr().err
    (agent / "agent.json").write_text("{}\n", encoding="utf-8")
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "verify")) == 1
    assert "data_agent" in capsys.readouterr().err


def test_cli_reports_a_bad_commit_without_a_traceback(tmp_path, agent, analyst_repo, capsys):
    repo, _ = analyst_repo
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "0" * 40, "freeze")) == 2
    assert "instructions" in capsys.readouterr().err
