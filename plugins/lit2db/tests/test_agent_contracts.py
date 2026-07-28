"""An agent must hold every MCP tool its own contract tells it to call.

This closes a bug class that has now bitten twice. v0.2.0 shipped an `extractor-agent` whose
body said to call `extract_record` and `retrieve_spans` — neither exposed by the MCP server, so
the agent had effectively only `Read` and nobody but the author could run the pipeline. It was
caught by probing the agent, not by a test. Then the same shape survived the fix: the contract
said "Never compute a char offset yourself — call `locate_spans`" while the frontmatter declared
`[Read, Grep, Glob, Write]`, and `contradiction-hunter-agent` was required to return a
`char_offset` with no tool on hand that produces one.

The failure is silent in the worst way. An agent told to call a tool it does not hold does not
error — it improvises. For an offset that means a plausible integer that slices real text out of
the file at the wrong place, which no downstream check can catch because the quote is real and
the offset is well-formed.

The rule enforced here: an MCP tool named in an agent's body must either be declared in that
agent's `tools:`, or be named in a sentence that explicitly negates it ("you do not hold
`locate_spans`", "you do NOT call `gate_upsert`"). Saying who does NOT call a tool is how these
contracts assign the work to the spine, so negation is a first-class outcome, not an escape
hatch.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENTS = sorted((ROOT / "agents").glob("*.md"))
MCP_TOOLS = set(re.findall(r"@mcp\.tool\(\)(?:.|\n)*?def\s+(\w+)",
                           (ROOT / "mcp" / "lit2db_mcp" / "server.py").read_text()))

# The negation must GOVERN THE VERB, so it is matched against the text immediately preceding it
# rather than anywhere in the clause. The shipped defect is why: "Never compute a char offset
# yourself — call `locate_spans`" carries a `never` that scopes over *computing* while `call`
# stays affirmative, and a clause-wide check absolves the exact wording this file exists to catch.
NEGATION = re.compile(r"\b(?:do|does|did|will|would|can|could|must|shall|should|may|is|are|am)\s+"
                      r"(?:not|never)$|\b(?:not|never|cannot|n't)$", re.IGNORECASE)

# A DIRECTIVE — a verb governing the tool — is the thing that actually harms. Naming a tool to
# explain who owns a decision ("that is `aggregate_ensemble`, a deterministic tool") is how these
# contracts assign work to the spine, and flagging it would push authors toward vaguer prose that
# hides the very boundary this architecture depends on.
DIRECTIVE = re.compile(r"\b(call|calls|use|uses|using|via|invoke|run|runs|feed|feeds|send|"
                       r"query|queries)\b[^`\n]{0,40}`(\w+)`", re.IGNORECASE)


def split(path: pathlib.Path) -> tuple[str, str]:
    """Return (frontmatter, body). Agent files are `---\\n...\\n---\\n<body>`."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) == 3, f"{path.name}: no YAML frontmatter delimited by ---"
    return parts[1], parts[2]


def declared_tools(frontmatter: str) -> set[str]:
    """Declared tools, NORMALISED to bare names.

    An MCP tool is declared namespaced (`mcp__plugin_lit2db_lit2db__resolve_structure`) and
    referred to bare in prose (`resolve_structure`). Comparing the two literally made this check
    pass on a declaration it should have caught, and it only passed because a `**` in the
    sentence happened to split the clause before the matcher saw it. Compare identity, using the
    same last-segment rule the PreToolUse hook applies (`lit2db.gate.tool_basename`).
    """
    m = re.search(r"^tools:\s*\[(.*?)\]", frontmatter, re.MULTILINE)
    if not m:
        return set()
    return {t.strip().rsplit("__", 1)[-1] for t in m.group(1).split(",") if t.strip()}


def clauses(body: str) -> list[str]:
    """Clause-sized spans, because a negation binds inside its clause and not beyond it.

    Splitting only on sentences is too coarse and produced a false pass on the first run: the
    bullet "**Queue what you could not get.** Feed the unreachable set to `rank_manual_queue`"
    has a `not` that belongs to a different thought entirely, and it silently absolved a real
    offender. Bold spans, semicolons, colons and commas all end a clause here.
    """
    return [c for c in re.split(r"(?<=[.!?])\s+|\n\s*[-*]\s+|\n\n|\*\*|[;:,]", body) if c.strip()]


def test_the_agent_directory_is_not_empty():
    """A glob that silently matches nothing would make every test below vacuously pass."""
    assert AGENTS, "no agent contracts found — the tests below would pass on an empty set"
    assert MCP_TOOLS, "no @mcp.tool() definitions found — the scan below would find nothing"


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_an_agent_never_directs_itself_to_call_a_tool_it_lacks(path):
    frontmatter, body = split(path)
    held = declared_tools(frontmatter)
    offenders = []
    for clause in clauses(body):
        for match in DIRECTIVE.finditer(clause):
            tool = match.group(2)
            if tool not in MCP_TOOLS or tool in held:
                continue
            if _negated(clause, match.start()):
                continue                       # "you do NOT call `gate_upsert`" — assigned away
            offenders.append((tool, " ".join(clause.split())[:160]))
    assert not offenders, (
        f"{path.name} declares tools={sorted(held) or '[]'} but its body directs it to use "
        f"MCP tools it does not hold. An agent told to call a missing tool improvises silently:\n"
        + "\n".join(f"  - `{t}` in: {s}" for t, s in offenders))


def _negated(clause: str, verb_start: int) -> bool:
    """Is the directive verb at `verb_start` under a negation that governs it?"""
    return bool(NEGATION.search(clause[:verb_start].rstrip()))


def _scan(body: str, held: set[str]) -> list[str]:
    """The rule under test, isolated so the fixtures below can exercise it directly."""
    return [m.group(2) for clause in clauses(body) for m in DIRECTIVE.finditer(clause)
            if m.group(2) in MCP_TOOLS and m.group(2) not in held
            and not _negated(clause, m.start())]


def test_it_catches_the_wording_that_actually_shipped():
    """The v0.2.0 and post-fix defects, verbatim. A linter that cannot catch the bug it was
    written for is decoration."""
    assert _scan("Never compute a char offset yourself — call `locate_spans`.",
                 {"Read", "Grep", "Glob", "Write"}) == ["locate_spans"]
    assert _scan("Use `resolve_access` (Unpaywall-backed).", {"WebFetch", "Read"}) \
        == ["resolve_access"]


def test_it_does_not_flag_a_tool_that_is_described_or_disclaimed():
    """Both false positives found while writing this test, kept as fixtures.

    Over-flagging is not a safe failure here: it pressures authors toward vaguer prose, and the
    boundary between what the agent proposes and what the spine decides is the whole architecture.
    """
    held = {"Read", "Grep", "Glob", "Write"}
    # Describing which component owns a decision — no directive at all.
    assert _scan("**You do not decide whether passes agree.** That is `aggregate_ensemble`, "
                 "a deterministic tool", held) == []
    # An explicit disclaimer, which is how work gets assigned to the spine.
    assert _scan("You do NOT call `gate_upsert`.", held) == []
    # A negation belonging to a NEIGHBOURING clause must not absolve the directive. This one
    # passed on the first run of this file and hid a real offender.
    assert _scan("**Queue what you could not get.** Feed the unreachable set to "
                 "`rank_manual_queue` with the ratified terms", held) == ["rank_manual_queue"]


def test_a_declared_tool_is_never_flagged():
    assert _scan("Call `locate_spans` for every quote.", {"locate_spans"}) == []


@pytest.mark.parametrize("name", ["extractor-agent", "contradiction-hunter-agent"])
def test_the_evidence_emitting_agents_are_told_not_to_compute_offsets(name):
    """Both emit spans a human must be able to re-read, and neither holds `locate_spans`.

    The store's contract is CHARACTER offsets; `grep -b` reports BYTE offsets, and every paper
    in this corpus carries non-ASCII, so an agent that computes its own offset drifts further
    from the truth the deeper into the document it reads.
    """
    frontmatter, body = split(ROOT / "agents" / f"{name}.md")
    assert "locate_spans" not in declared_tools(frontmatter), (
        f"{name} now declares locate_spans — if that is deliberate, this test should be "
        "deleted along with the 'the spine resolves it' wording in the contract")
    assert re.search(r"[Nn]ever compute a char offset|[Dd]o not compute a char offset", body), (
        f"{name} emits evidence spans but is not told to leave offsets to the spine")
