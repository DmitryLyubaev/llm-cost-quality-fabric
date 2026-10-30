"""The capstone freeze: its hashing is deterministic, byte for byte, and every component is guarded."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import types
import unicodedata
from pathlib import Path

import pytest

import capstone_freeze
from lakehouse.capstone import freeze as freeze_module
from lakehouse.capstone.freeze import DataAgentError, FreezeBroken, build_freeze, verify_freeze

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


def test_unknown_extra_components_are_reported_sorted_after_the_known_ones(agent):
    freeze, inputs = _build(agent)
    freeze["components"] = {**freeze["components"], "zeta": "0" * 64, "alpha": "0" * 64}
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **inputs)
    assert error.value.component == "alpha"
    del freeze["components"]["model_foundry"]
    with pytest.raises(FreezeBroken) as error:
        verify_freeze(freeze, **inputs)
    assert error.value.component == "model_foundry"  # a known one is reported before the extras


def test_a_nonexistent_data_agent_folder_raises(tmp_path):
    with pytest.raises(DataAgentError):
        _build(tmp_path / "nope")


def test_a_file_in_place_of_the_data_agent_folder_raises(tmp_path):
    (tmp_path / "file").write_text("x", encoding="utf-8")
    with pytest.raises(DataAgentError):
        _build(tmp_path / "file")


def test_an_empty_data_agent_folder_hashes_the_empty_byte_string(tmp_path):
    (tmp_path / "empty" / "nested").mkdir(parents=True)  # directories contribute nothing
    freeze, _ = _build(tmp_path / "empty")
    assert freeze["components"]["data_agent"] == hashlib.sha256(b"").hexdigest()


def test_hidden_files_and_dot_folders_count(tmp_path, agent):
    base, _ = _build(agent)
    (agent / ".hidden").write_text("x", encoding="utf-8")
    assert _build(agent)[0]["components"]["data_agent"] != base["components"]["data_agent"]


def test_a_symlink_in_the_data_agent_folder_is_rejected(tmp_path, agent):
    try:
        (agent / "link.json").symlink_to(agent / "agent.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    with pytest.raises(DataAgentError, match="symbolic link"):
        _build(agent)
    (agent / "link.json").unlink()
    (agent / "linked-dir").symlink_to(agent / "sources", target_is_directory=True)
    with pytest.raises(DataAgentError, match="symbolic link"):
        _build(agent)


@pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature (mklink /J)")
def test_a_windows_junction_in_the_data_agent_folder_is_rejected(tmp_path, agent, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text("{}\n", encoding="utf-8")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(agent / "junction"), str(outside)], capture_output=True)
    assert made.returncode == 0, made
    walked = []
    real_walk = os.walk

    def recording_walk(*args, **kwargs):
        for step in real_walk(*args, **kwargs):
            walked.append(step[0])
            yield step

    monkeypatch.setattr(freeze_module.os, "walk", recording_walk)
    with pytest.raises(DataAgentError, match="reparse point: junction$"):
        _build(agent)
    assert not any("junction" in Path(current).name for current in walked)  # never descended into it


def test_any_reparse_point_is_rejected(agent, monkeypatch):
    # A reparse point that is neither a symbolic link nor a junction (say, a OneDrive placeholder)
    # shows only in st_file_attributes; simulate one on every platform.
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        result = real_lstat(path, *args, **kwargs)
        if Path(path).name == "agent.json":
            return types.SimpleNamespace(st_mode=result.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return result

    monkeypatch.setattr(os, "lstat", lstat)
    with pytest.raises(DataAgentError, match="reparse point: agent.json$"):
        _build(agent)


def test_a_bom_is_hashed_and_never_stripped(tmp_path):
    plain = _write_agent(tmp_path / "plain", {"a.txt": "x\n"})
    with_bom = _write_agent(tmp_path / "bom", {"a.txt": "\ufeffx\n"})
    assert _build(plain)[0]["components"]["data_agent"] != _build(with_bom)[0]["components"]["data_agent"]
    assert (
        _build(plain, questions="\ufeffq")[0]["components"]["questions"]
        == hashlib.sha256(b"\xef\xbb\xbfq").hexdigest()
    )


@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        (b"a\r\nb", b"a\nb"),
        (b"a\rb", b"a\nb"),
        (b"a\r\r\nb", b"a\n\nb"),
        (b"a\r\n\r\nb", b"a\n\nb"),
        (b"a\nb", b"a\nb"),
        (b"a\r", b"a\n"),
        (b"\r\r\r", b"\n\n\n"),
    ],
)
def test_line_endings_normalise_in_one_pass(tmp_path, raw, normalised):
    assert freeze_module._lf(raw) == normalised
    assert build_freeze(raw, "", "t", tmp_path, "", {"direct": "d", "foundry": "f"}, rules="")["components"][
        "questions"
    ] == hashlib.sha256(normalised).hexdigest()


# --- the shared test vector (Task 8's .NET analyst copies it and must reproduce every hash) ---


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lf(text: str) -> bytes:
    return re.sub(rb"\r\n?", b"\n", text.encode("utf-8"))


def _case_sensitive(folder: Path) -> bool:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Case").write_text("x", encoding="utf-8")
    return not (folder / "case").exists()


def test_vector_is_reproduced_by_build_freeze(tmp_path):
    if not _case_sensitive(tmp_path / "probe"):
        pytest.skip("the vector holds names that differ only by case")
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


def _vector_inputs():
    return json.loads(VECTOR.read_text(encoding="utf-8"))["inputs"]


def test_vector_has_a_dotfile_and_a_dot_folder():
    paths = _vector_inputs()["data_agent"]
    assert any(p.startswith(".") and "/" not in p for p in paths)
    assert any(p.startswith(".") and "/" in p for p in paths)


def test_vector_has_nfc_non_ascii_paths_with_a_supplementary_character():
    paths = [p for p in _vector_inputs()["data_agent"] if any(ord(c) > 127 for c in p)]
    assert paths
    assert all(unicodedata.is_normalized("NFC", p) for p in paths)
    assert any(ord(c) > 0xFFFF for p in paths for c in p)
    assert any("\u00e9" in p for p in paths)  # an NFC accented letter, one code point


def test_vector_has_paths_whose_walk_order_differs_from_byte_order():
    paths = list(_vector_inputs()["data_agent"])
    assert "a.json" in paths and "a/b.md" in paths
    by_bytes = sorted(paths, key=lambda p: p.encode("utf-8"))
    by_walk = sorted(paths, key=lambda p: p.split("/"))  # a tree walk: folder "a" before "a.json"
    assert by_bytes.index("a.json") < by_bytes.index("a/b.md")
    assert by_walk.index("a/b.md") < by_walk.index("a.json")


def test_vector_has_paths_whose_utf16_order_differs_from_utf8_byte_order():
    paths = list(_vector_inputs()["data_agent"])
    by_bytes = sorted(paths, key=lambda p: p.encode("utf-8"))
    by_utf16 = sorted(paths, key=lambda p: p.encode("utf-16-be"))
    assert by_bytes != by_utf16


def test_vector_has_an_uppercase_and_lowercase_pair():
    paths = list(_vector_inputs()["data_agent"])
    assert any(p != q and p.lower() == q.lower() for p in paths for q in paths)


def test_vector_has_an_empty_file_and_a_file_without_a_trailing_newline():
    contents = list(_vector_inputs()["data_agent"].values())
    assert "" in contents
    assert any(c and not c.endswith(("\n", "\r")) for c in contents)


def test_vector_has_a_bom_a_lone_cr_and_a_cr_cr_lf():
    contents = list(_vector_inputs()["data_agent"].values())
    assert any(c.startswith("\ufeff") for c in contents)
    assert any(re.search(r"\r(?![\r\n])", c) for c in contents)  # a CR followed by neither CR nor LF
    assert any("\r\r\n" in c for c in contents)
    assert any("\r\n" in c for c in contents)  # CR LF too


def test_vector_instructions_and_questions_have_crlf():
    inputs = _vector_inputs()
    assert "\r\n" in inputs["analyst_instructions"]
    assert "\r\n" in inputs["questions"]
    assert "\r\n" in inputs["rules"]
    assert any(ord(c) > 127 for c in inputs["analyst_instructions"])


def test_vector_file_is_pure_ascii_so_no_tool_can_alter_its_characters():
    VECTOR.read_bytes().decode("ascii")


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


def _with(args, flag, value):
    args = list(args)
    args[args.index(flag) + 1] = value
    return args


def _stderr(capsys):
    return capsys.readouterr().err.strip()


def test_cli_reports_a_bad_commit_with_a_fixed_message(tmp_path, agent, analyst_repo, capsys):
    repo, _ = analyst_repo
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "0" * 40, "freeze")) == 2
    assert _stderr(capsys) == capstone_freeze.MSG_NO_COMMIT
    assert _stderr(capsys) == ""


def test_cli_verify_with_a_bad_commit_exits_2(tmp_path, agent, analyst_repo, capsys):
    repo, first = analyst_repo
    capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "freeze"))
    capsys.readouterr()
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "no-such-ref", "verify")) == 2
    assert _stderr(capsys) == "analyst_instructions: --analyst-commit is not a commit in --analyst-repo"


def test_cli_a_valid_commit_without_instructions_exits_2(tmp_path, agent, analyst_repo, capsys):
    repo, _ = analyst_repo
    (repo / "src" / "Portfolio.Analyst" / "instructions.md").unlink()
    _git(repo, "commit", "-q", "-am", "remove the instructions")
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "HEAD", "freeze")) == 2
    assert (
        _stderr(capsys)
        == "analyst_instructions: src/Portfolio.Analyst/instructions.md does not exist at --analyst-commit"
    )


def test_cli_a_path_that_is_not_a_repo_exits_2(tmp_path, agent, capsys):
    not_a_repo = tmp_path / "plain-folder"
    not_a_repo.mkdir()
    assert capstone_freeze.main(_cli_args(tmp_path, agent, not_a_repo, "HEAD", "freeze")) == 2
    assert _stderr(capsys) == "analyst_instructions: --analyst-commit is not a commit in --analyst-repo"
    assert capstone_freeze.main(_cli_args(tmp_path, agent, tmp_path / "missing", "HEAD", "freeze")) == 2
    assert _stderr(capsys) == "analyst_instructions: --analyst-commit is not a commit in --analyst-repo"


def test_cli_rejects_a_commit_that_looks_like_an_option(tmp_path, agent, analyst_repo, capsys):
    repo, _ = analyst_repo
    args = _cli_args(tmp_path, agent, repo, "x", "freeze")
    i = args.index("--analyst-commit")
    args[i : i + 2] = ["--analyst-commit=--output=evil"]
    assert capstone_freeze.main(args) == 2
    assert _stderr(capsys) == "analyst_instructions: --analyst-commit must be a commit id, not an option"
    assert not (tmp_path / "evil").exists()


def test_cli_a_missing_git_binary_exits_2(tmp_path, agent, analyst_repo, capsys, monkeypatch):
    repo, first = analyst_repo

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(capstone_freeze.subprocess, "run", no_git)
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "freeze")) == 2
    assert _stderr(capsys) == "analyst_instructions: git is not installed"


def test_cli_hashes_the_blobs_raw_bytes(tmp_path, agent, analyst_repo):
    repo, _ = analyst_repo
    raw = b"\xef\xbb\xbf caf\xc3\xa9 \xff\xfe not valid utf-8 \r\n"
    (repo / "src" / "Portfolio.Analyst" / "instructions.md").write_bytes(raw)
    _git(repo, "-c", "core.autocrlf=false", "commit", "-q", "-am", "raw bytes")
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, "HEAD", "freeze")) == 0
    written = json.loads((tmp_path / "freeze.json").read_text(encoding="utf-8"))
    assert written["components"]["analyst_instructions"] == _sha(raw.replace(b"\r\n", b"\n"))


@pytest.mark.parametrize(
    ("flag", "message"),
    [
        ("--questions", "--questions: file not found"),
        ("--expected", "--expected: file not found"),
        ("--rules", "--rules: file not found"),
        ("--data-agent-dir", "--data-agent-dir: folder not found"),
    ],
)
def test_cli_a_missing_input_exits_2_naming_the_argument(tmp_path, agent, analyst_repo, capsys, flag, message):
    repo, first = analyst_repo
    args = _with(_cli_args(tmp_path, agent, repo, first, "freeze"), flag, str(tmp_path / "gone"))
    assert capstone_freeze.main(args) == 2
    assert _stderr(capsys) == message


def test_cli_verify_without_a_freeze_file_exits_2(tmp_path, agent, analyst_repo, capsys):
    repo, first = analyst_repo
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "verify")) == 2
    assert _stderr(capsys) == "--freeze: file not found"


def test_cli_a_symlink_in_the_data_agent_exits_2(tmp_path, agent, analyst_repo, capsys):
    repo, first = analyst_repo
    try:
        (agent / "link.json").symlink_to(agent / "agent.json")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "freeze")) == 2
    assert _stderr(capsys) == "--data-agent-dir: the data agent folder holds a symbolic link or reparse point: link.json"


def test_cli_default_data_agent_folder_is_the_portfolio_data_agent():
    args = capstone_freeze._parser().parse_args(
        ["freeze", "--snapshot-time", "t", "--analyst-repo", "r", "--analyst-commit", "c",
         "--model-direct", "d", "--model-foundry", "f"]
    )
    assert args.data_agent_dir == ROOT / "fabric-capstone" / "da_portfolio.DataAgent"


@pytest.mark.parametrize(
    "content, message",
    [
        (b'{"components": ', capstone_freeze.MSG_FREEZE_NOT_JSON),
        (b'["not", "an", "object"]\n', capstone_freeze.MSG_FREEZE_NOT_OBJECT),
        (b'{"short": "\xff\xfe"}\n', capstone_freeze.MSG_FREEZE_NOT_UTF8),
    ],
)
def test_cli_verify_rejects_an_unreadable_freeze_file_with_a_fixed_message(
    tmp_path, agent, analyst_repo, capsys, content, message
):
    repo, first = analyst_repo
    (tmp_path / "freeze.json").write_bytes(content)

    assert capstone_freeze.main(_cli_args(tmp_path, agent, repo, first, "verify")) == 2
    assert _stderr(capsys) == message
