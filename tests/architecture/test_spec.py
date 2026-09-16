"""``docs/machine.md`` is the spec; this module is what makes that true.

The document owns the transition graph: when the machine and the document
disagree, the machine is wrong. The test enumerates every state against every
declared event, asserts the accepted set and its destinations match the documented
table in both directions, and then checks that the mermaid diagram in the same
file agrees with the table.

Both parsers fail loudly when the document's format changes. Silently matching
nothing would turn the whole enforcement mechanism into a no-op.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from super_agent.runtime import machine

#: The document that owns the transition graph.
SPEC_DOCUMENT = "docs/machine.md"

#: The table's wildcard state. The machine stores global rules under the zero
#: State, so the table needs a name for it.
ANY_STATE = "any"

#: Events accepted from every state and deliberately not drawn, because
#: enumerating them would add eighteen edges without adding information.
DIAGRAM_EXCLUDED_EVENTS = frozenset({"ErrorOccurred", "CancelRequested", "ResetRequested"})

#: Every state the machine declares, in declaration order.
EXPECTED_STATES = (
    "Initializing",
    "Idle",
    "WaitingLLM",
    "WaitingApproval",
    "RunningTool",
    "AdvancingQueue",
)

_NAME_ONLY = re.compile(r"^[A-Za-z]+$")
_DIAGRAM_EDGE = re.compile(r"^\s*([A-Za-z]+)\s*-->\s*([A-Za-z]+)\s*:\s*(.+?)\s*$")


@dataclass(frozen=True)
class DocumentedEdge:
    """One row of the transition table."""

    state: str
    event: str
    next: str

    @property
    def key(self) -> str:
        return f"{self.state} + {self.event}"


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def machine_states() -> tuple[str, ...]:
    """Every state the machine declares, pinned so an addition fails loudly."""
    states = tuple(str(state) for state in machine.ALL_STATES)
    assert states == EXPECTED_STATES, f"machine states changed: {states} != {EXPECTED_STATES}"
    return states


def read_spec() -> str:
    path = repository_root() / SPEC_DOCUMENT
    assert path.is_file(), f"cannot read the spec document at {path}"
    return path.read_text(encoding="utf-8")


def parse_transition_table(document: str) -> list[DocumentedEdge]:
    """Extract the documented edges from the markdown table.

    A line is accepted only when it splits into exactly five cells whose first
    three are a known state (or ``any``), an event name, and a next state. The
    document's other tables have different cell counts or backticked first cells,
    so they are skipped without needing to locate the table by heading.
    """
    known = {*machine_states(), ANY_STATE}
    edges: list[DocumentedEdge] = []
    for line_number, line in enumerate(document.split("\n"), start=1):
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        if cells[0] not in known or not _NAME_ONLY.match(cells[1]) or not _NAME_ONLY.match(cells[2]):
            continue
        if cells[1] == "Event":  # header row
            continue
        if cells[2] not in known:
            pytest.fail(f"{SPEC_DOCUMENT}:{line_number}: documented next state {cells[2]!r} is not a declared state")
        edges.append(DocumentedEdge(state=cells[0], event=cells[1], next=cells[2]))
    if not edges:
        pytest.fail(f"{SPEC_DOCUMENT}: parsed no transition table rows; the table format changed")
    return edges


def parse_state_diagram(document: str) -> list[DocumentedEdge]:
    """Extract the ``stateDiagram`` edges from the document."""
    in_diagram = False
    edges: list[DocumentedEdge] = []
    for line in document.split("\n"):
        if line.strip().startswith("```"):
            in_diagram = line.strip() == "```mermaid" and not in_diagram
            continue
        if not in_diagram:
            continue
        match = _DIAGRAM_EDGE.match(line)
        if match is None:
            continue
        # "ApprovalGranted / ApprovalAlwaysGranted" is two edges on one line.
        for event in match.group(3).split("/"):
            edges.append(DocumentedEdge(state=match.group(1), event=event.strip(), next=match.group(2)))
    if not edges:
        pytest.fail(f"{SPEC_DOCUMENT}: parsed no state diagram edges; the diagram format changed")
    return edges


def sample_tool_call() -> machine.ToolCall:
    return machine.ToolCall(id="call-1", name="bash", input="pwd")


def sample_tool_calls() -> tuple[machine.ToolCall, ...]:
    return (
        machine.ToolCall(id="call-1", name="first", input="a"),
        machine.ToolCall(id="call-2", name="second", input="b"),
    )


def well_formed_event(prototype: machine.Event) -> machine.Event:
    """Return an event carrying the content its handler validates.

    ``machine.AllEvents`` holds zero values, which would make ``ToolBatchReceived``
    fail its empty-batch guard and ``ToolResultReceived`` fail its call guard. A
    rejection must mean "the table does not document this edge", never "the sample
    was malformed".
    """
    call = sample_tool_call()
    if isinstance(prototype, machine.UserMessageSubmitted):
        return machine.UserMessageSubmitted(content="hi")
    if isinstance(prototype, machine.AssistantMessageReceived):
        return machine.AssistantMessageReceived(response=machine.ModelResponse(content="hi"))
    if isinstance(prototype, machine.ToolBatchReceived):
        return machine.ToolBatchReceived(content="thinking", calls=sample_tool_calls())
    if isinstance(prototype, machine.ToolCallNeedsApproval):
        return machine.ToolCallNeedsApproval(call=call)
    if isinstance(prototype, machine.ToolCallReadyToRun):
        return machine.ToolCallReadyToRun(call=call)
    if isinstance(prototype, machine.ToolCallDenied):
        return machine.ToolCallDenied(call=call, reason="plan mode")
    if isinstance(prototype, machine.ToolResultReceived):
        return machine.ToolResultReceived(call=call, result="ok")
    if isinstance(prototype, machine.ApprovalGranted):
        return machine.ApprovalGranted(call=call)
    if isinstance(prototype, machine.ApprovalAlwaysGranted):
        return machine.ApprovalAlwaysGranted(call=call)
    if isinstance(prototype, machine.ApprovalDenied):
        return machine.ApprovalDenied(call=call)
    if isinstance(prototype, machine.ErrorOccurred):
        return machine.ErrorOccurred(err=RuntimeError("boom"))
    # EngineReady, ToolBatchFinished, CancelRequested, ResetRequested carry no fields.
    return prototype


def state_snapshot(state_name: str, event: machine.Event) -> machine.MachineSnapshot:
    """Build runtime data satisfying the invariants of ``state_name`` and the
    preconditions of ``event``, so calling ``Transition`` measures the edge
    registry rather than guard setup.

    Mirrors ``transition_snapshot`` in ``tests/runtime/test_transition.py``; the
    logic is duplicated because these two tests must fail independently.
    """
    call = sample_tool_call()
    for attribute in ("Call",):
        candidate = getattr(event, attribute, None)
        if isinstance(candidate, machine.ToolCall):
            call = candidate

    data = machine.RuntimeData(state=machine.State(state_name))
    if data.state == machine.STATE_ADVANCING_QUEUE:
        data.tool_batch = machine.ToolCallBatch(calls=[call])
        if isinstance(event, machine.ToolBatchFinished):
            # ToolBatchFinished requires an exhausted queue.
            data.tool_batch.index = 1
    elif data.state == machine.STATE_WAITING_APPROVAL:
        data.pending_tool = call
        data.pending_permission = machine.PermissionRequest()
        data.tool_batch = machine.ToolCallBatch(calls=[call], index=1)
    elif data.state == machine.STATE_RUNNING_TOOL:
        data.current_tool = call
        data.tool_batch = machine.ToolCallBatch(calls=[call], index=1)

    try:
        return machine.snapshot_from(data)
    except machine.InvariantViolationError as error:  # pragma: no cover - a broken fixture, not a result
        raise AssertionError(f"invalid {state_name} snapshot for {type(event).__name__}: {error}") from error


def observed_edges() -> dict[str, str]:
    """Run every state against every declared event and record the accepted pairs."""
    observed: dict[str, str] = {}
    for state_name in machine_states():
        for prototype in machine.ALL_EVENTS:
            event = well_formed_event(prototype)
            try:
                result = machine.transition(state_snapshot(state_name, event), event)
            except (machine.UnexpectedEventError, machine.ProtocolViolationError):
                continue
            observed[DocumentedEdge(state=state_name, event=type(event).__name__, next="").key] = str(result.next_state)
    return observed


def documented_edges(rows: list[DocumentedEdge]) -> tuple[dict[str, str], dict[str, bool]]:
    """Expand the table into one entry per concrete state."""
    expanded: dict[str, str] = {}
    state_specific: dict[str, bool] = {}
    for row in rows:
        if row.state == ANY_STATE:
            for state_name in machine_states():
                expanded[DocumentedEdge(state=state_name, event=row.event, next="").key] = row.next
            continue
        expanded[row.key] = row.next
        state_specific[row.key] = True
    return expanded, state_specific


def describe(edges: dict[str, str]) -> str:
    return "\n  ".join(sorted(edges))


def test_event_and_state_counts_are_pinned() -> None:
    """The table documents 6 states x 15 events; a silent change would shrink the sweep."""
    assert len(machine_states()) == 6
    assert len(machine.ALL_EVENTS) == 15, [type(event).__name__ for event in machine.ALL_EVENTS]


def test_documented_transition_table_matches_machine() -> None:
    """Pin the spec's table to the machine's real behaviour, in both directions."""
    rows = parse_transition_table(read_spec())
    documented, _ = documented_edges(rows)
    observed = observed_edges()

    mismatches: list[str] = []
    for key, want in sorted(documented.items()):
        got = observed.get(key)
        if got is None:
            mismatches.append(f"{key}: documented but the machine rejects this event")
        elif got != want:
            mismatches.append(f"{key}: documented next state {want}, machine produced {got}")
    for key in sorted(observed):
        if key not in documented:
            mismatches.append(f"{key}: accepted by the machine but missing from the transition table")

    assert not mismatches, (
        "docs/machine.md and the machine disagree:\n  "
        + "\n  ".join(mismatches)
        + f"\n\ndocumented edges:\n  {describe(documented)}\nmachine edges:\n  {describe(observed)}"
    )


def test_documented_state_diagram_matches_transition_table() -> None:
    """Keep the two views in ``docs/machine.md`` from drifting apart."""
    rows = parse_transition_table(read_spec())
    _, state_specific = documented_edges(rows)

    diagram: dict[str, bool] = {}
    problems: list[str] = []
    for edge in parse_state_diagram(read_spec()):
        if diagram.get(edge.key):
            problems.append(f"{edge.key}: drawn twice in the state diagram")
        if edge.event in DIAGRAM_EXCLUDED_EVENTS:
            problems.append(f"{edge.key}: global events must not be drawn; the table defines them")
        diagram[edge.key] = True

    for key in sorted(state_specific):
        if not diagram.get(key):
            problems.append(f"{key}: in the transition table but missing from the state diagram")
    for key in sorted(diagram):
        if not state_specific.get(key):
            problems.append(f"{key}: in the state diagram but missing from the transition table")

    assert not problems, "docs/machine.md diagram and table disagree:\n  " + "\n  ".join(problems)
