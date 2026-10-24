"""A small reader for the TMDL the semantic model is written in (tests only, no dependency).

TMDL is indentation-based, one tab per level. This reads the subset `sm_portfolio` uses:

- an object line, `<keyword> [<name>] [= <expression>]`, where the name is bare or single-quoted
  (`''` inside quotes is one quote);
- a multi-line expression, when the object line ends in `=`: the lines indented two levels deeper
  than the object, kept with that indentation removed;
- a property, `<key>: <value>`, or a bare flag such as `isHidden` (value True);
- a description, `///` lines just before an object;
- child objects, one level deeper.

Anything else (a line indented too deep, an expression with nothing in it) is a ValueError, so a
malformed file fails the tests instead of being read wrongly.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

_PROPERTY = re.compile(r"^([A-Za-z]\w*):\s?(.*)$")
_FLAG = re.compile(r"^[A-Za-z]\w*$")
_KEYWORD = re.compile(r"^([A-Za-z]\w*)(?: (.*))?$")


@dataclass
class Node:
    keyword: str
    name: str | None = None
    expression: str | None = None
    description: str | None = None
    properties: dict[str, object] = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)

    def all(self, keyword: str) -> list["Node"]:
        return [child for child in self.children if child.keyword == keyword]

    def one(self, keyword: str) -> "Node":
        found = self.all(keyword)
        if len(found) != 1:
            raise ValueError(f"{self.keyword} {self.name!r}: expected one {keyword}, found {len(found)}")
        return found[0]


def _depth(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _split_name(rest: str) -> tuple[str | None, str | None]:
    """(name, expression or None) from the text after the keyword; expression '' means multi-line."""
    if rest.startswith("'"):
        i, name = 1, []
        while True:
            if i >= len(rest):
                raise ValueError(f"unterminated quoted name: {rest!r}")
            if rest[i] == "'":
                if rest[i + 1:i + 2] == "'":
                    name.append("'")
                    i += 2
                    continue
                break
            name.append(rest[i])
            i += 1
        tail = rest[i + 1:]
        name_text = "".join(name)
    else:
        bare, _, after = rest.partition(" ")
        name_text, tail = bare, (" " + after if after else "")
        if bare == "=":  # an unnamed object with an expression: `source =`
            name_text, tail = None, " " + rest
    tail = tail.strip()
    if not tail:
        return name_text, None
    if tail == "=":
        return name_text, ""
    if tail.startswith("= "):
        return name_text, tail[2:].strip()
    raise ValueError(f"unexpected text after the name: {rest!r}")


def _parse(lines: list[str], start: int, depth: int) -> tuple[list[Node], int]:
    nodes: list[Node] = []
    description: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        level = _depth(line)
        if level < depth:
            break
        if level > depth:
            raise ValueError(f"line {i + 1}: indented {level}, expected {depth}: {line!r}")
        text = line.strip()
        if text.startswith("///"):
            description.append(text[3:].strip())
            i += 1
            continue
        match = _KEYWORD.match(text)
        if not match:
            raise ValueError(f"line {i + 1}: not an object: {line!r}")
        keyword, rest = match.group(1), match.group(2) or ""
        if rest.startswith("="):
            name, expression = None, (rest[1:].strip() if rest.strip() != "=" else "")
        else:
            name, expression = _split_name(rest) if rest else (None, None)
        node = Node(keyword, name, description="\n".join(description) if description else None)
        description = []
        i += 1
        if expression == "":
            body = []
            while i < len(lines) and (not lines[i].strip() or _depth(lines[i]) >= depth + 2):
                body.append(lines[i][depth + 2:] if lines[i].strip() else "")
                i += 1
            while body and not body[-1]:
                body.pop()
            if not body:
                raise ValueError(f"line {i}: {keyword} {name!r} has an empty expression")
            expression = "\n".join(body)
        node.expression = expression
        # Properties and child objects, one level deeper.
        while i < len(lines):
            child = lines[i]
            if not child.strip():
                i += 1
                continue
            if _depth(child) != depth + 1:
                break
            stripped = child.strip()
            prop = _PROPERTY.match(stripped)
            if prop:
                node.properties[prop.group(1)] = prop.group(2)
                i += 1
            elif _FLAG.match(stripped) and (
                i + 1 >= len(lines) or not lines[i + 1].strip() or _depth(lines[i + 1]) <= depth + 1
            ):
                node.properties[stripped] = True
                i += 1
            else:
                children, i = _parse(lines, i, depth + 1)
                node.children.extend(children)
        nodes.append(node)
    if description:
        raise ValueError("a description with no object after it")
    return nodes, i


def parse(text: str) -> list[Node]:
    """The top-level objects of one TMDL document."""
    lines = text.split("\n")
    nodes, end = _parse(lines, 0, 0)
    rest = [line for line in lines[end:] if line.strip()]
    if rest:
        raise ValueError(f"unparsed text: {rest[0]!r}")
    return nodes


def parse_file(path: Path) -> list[Node]:
    return parse(path.read_text(encoding="utf-8"))
