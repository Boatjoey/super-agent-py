"""The engine: the run lifecycle, the action loop, and the ports around it.

These cases drive the engine through ``session.RunTurn``. The session layer is
not exercised here, so the cases whose subject is engine behaviour call
``Engine.RunTurn`` directly with an approval waiter in place of the approval
channel. The cases whose subject is a session notification live in
``tests/runtime/test_session_notifications.py``.

Two engine-level cases that the other modules leave to the engine are appended
here: ``test_engine_does_not_commit_invalid_custom_runtime_data_change_result``
(from ``tests/runtime/test_machine_invariants.py``) and
``test_engine_rejects_invalid_permission_mode`` (from
``tests/runtime/test_action_result_resolver.py``).
"""

from __future__ import annotations

import asyncio

import pytest

from super_agent.errors import Cancelled, errors_is
from super_agent.runtime import machine
from super_agent.runtime.engine import (
    Engine,
    NewEngine,
    NewEngineWithComponents,
    NewEngineWithExecutor,
    NewEngineWithExecutorAndPolicy,
)
from super_agent.runtime.execution import (
    ApprovalWaiter,
    DefaultActionResultResolver,
    DefaultRunController,
    DefaultScheduledActionExecutor,
    DefaultScheduledActionRunner,
    ErrApprovalDismissed,
    MemoryApprovalStore,
    NewDefaultPolicy,
    PermissionMode,
    PermissionRules,
    ToolDecision,
)
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ModelResponse, ToolCall, ToolSpec
from tests.fakes.execution import (
    DenyNthPolicy,
    FailingOnceExecutor,
    FakeToolRunner,
    HangingApprovalWaiter,
    LockProbeApprovalStore,
    RecordingExecutor,
    RecordingPolicy,
    ScriptedApprovalWaiter,
    SpecProbeRunner,
)
from tests.fakes.model import BlockingModel, GateModel, ScriptedModel, StreamingCancelModel
from tests.runtime.test_transition import transition_snapshot


async def run_turn(engine: Engine, content: str, waiter: ApprovalWaiter | None = None) -> None:
    """Run one turn from a user message, the way the session layer does."""
    await engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content=content), None, waiter)


class _InvalidRuntimeDataChangeApplier:
    """An applier that lies about what it applied.

    Every state except ``Initializing`` gets an impossible result back, which is
    exactly the mistake the engine's post-apply validation exists to catch.
    """

    def ApplyRuntimeDataChanges(
        self, runtime_data: machine.RuntimeData, result: machine.TransitionResult
    ) -> machine.RuntimeDataChangeResult:
        if runtime_data.State == machine.StateInitializing:
            return machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(runtime_data, result)
        return machine.RuntimeDataChangeResult(
            RuntimeData=machine.RuntimeData(State=machine.StateIdle, ToolBatch=machine.ToolCallBatch())
        )


class _IdleAsWaitingLLMApplier:
    """Commits a non-``Idle`` state with nothing queued, to exercise the loop guard."""

    def ApplyRuntimeDataChanges(
        self, runtime_data: machine.RuntimeData, result: machine.TransitionResult
    ) -> machine.RuntimeDataChangeResult:
        if result.NextState == machine.StateIdle:
            return machine.RuntimeDataChangeResult(RuntimeData=machine.RuntimeData(State=machine.StateWaitingLLM))
        return machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(runtime_data, result)


# --- Constructors and the ready handshake -----------------------------------


@pytest.mark.asyncio
async def test_new_engine_starts_initializing_and_ready_enters_idle() -> None:
    engine = NewEngine(ScriptedModel([]), FakeToolRunner(), None)

    assert engine.State() == machine.StateInitializing
    await engine.Ready()
    assert engine.State() == machine.StateIdle


@pytest.mark.asyncio
async def test_ready_returns_error_when_already_ready() -> None:
    engine = NewEngine(ScriptedModel([]), FakeToolRunner(), None)

    await engine.Ready()
    with pytest.raises(machine.UnexpectedEventError):
        await engine.Ready()


@pytest.mark.asyncio
async def test_session_run_produces_content() -> None:
    model = ScriptedModel([ModelResponse(Content="hello", ReasoningContent="thinking")])
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    await run_turn(engine, "hi")

    assert engine.State() == machine.StateIdle
    got = engine.Messages()[1]
    assert got.Role == machine.RoleAssistant
    assert got.Content == "hello"
    assert got.ReasoningContent == "thinking"


@pytest.mark.asyncio
async def test_cancel_flushes_streaming_assistant_message() -> None:
    model = StreamingCancelModel()
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    ctx = LiveContext()
    task = asyncio.create_task(
        engine.RunTurn(ctx, machine.UserMessageSubmitted(Content="hi"), lambda _chunk: None, None)
    )
    await model.started.wait()
    ctx.Cancel()
    with pytest.raises(Cancelled):
        await task

    messages = engine.Messages()
    assert len(messages) == 2
    got = messages[1]
    assert got.Role == machine.RoleAssistant
    assert got.Content == "partial"
    assert got.ReasoningContent == "thinking"
    assert got.Interrupted is True
    assert engine.Snapshot().StreamingMessage is None


# --- Model and tool execution -----------------------------------------------


@pytest.mark.asyncio
async def test_engine_runs_scheduled_actions_through_injected_executor() -> None:
    executor = RecordingExecutor()
    engine = NewEngineWithExecutor(executor, None)
    await engine.Ready()

    await run_turn(engine, "hi")

    assert len(executor.actions) == 1
    assert isinstance(executor.actions[0], machine.CallModel)
    assert engine.Messages()[1].Content == "from executor"


@pytest.mark.asyncio
async def test_model_failure_leaves_no_fabricated_tool_message() -> None:
    executor = FailingOnceExecutor()
    engine = NewEngineWithExecutor(executor, None)
    await engine.Ready()

    with pytest.raises(RuntimeError, match="provider timeout"):
        await run_turn(engine, "hi")

    # A model call that fails before requesting any tool must not leave a tool
    # message behind. A tool result with no matching tool call is rejected by
    # the provider on the next request, and the transcript is persisted.
    messages = engine.Messages()
    assert len(messages) == 1
    assert messages[0].Role == machine.RoleUser

    await run_turn(engine, "continue")

    assert executor.calls == 2
    assert len(executor.seen) == 2
    for message in executor.seen:
        assert message.Role != machine.RoleTool


@pytest.mark.asyncio
async def test_failed_tool_batch_answers_every_tool_call() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                ToolCalls=(
                    ToolCall(ID="call-1", Name="bash", Input="printf one"),
                    ToolCall(ID="call-2", Name="bash", Input="printf two"),
                    ToolCall(ID="call-3", Name="bash", Input="printf three"),
                )
            ),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngineWithExecutorAndPolicy(
        DefaultScheduledActionExecutor(model, tools), DenyNthPolicy(deny_at=2), None
    )
    await engine.Ready()

    await run_turn(engine, "run tools")

    answered: list[str] = []
    denied = ""
    for message in engine.Messages():
        if message.Role == machine.RoleTool:
            answered.append(message.ToolCallID)
            if message.ToolCallID == "call-2":
                denied = message.Content
    assert answered == ["call-1", "call-2", "call-3"]
    assert denied == "denied by permission policy: denied by test policy"
    assert [call.ID for call in tools.calls] == ["call-1", "call-3"]


@pytest.mark.asyncio
async def test_custom_policy_classifies_tool_call_with_input() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    policy = RecordingPolicy(ToolDecision.DecisionRunDirectly)
    engine = NewEngineWithExecutorAndPolicy(DefaultScheduledActionExecutor(model, tools), policy, None)
    await engine.Ready()

    await run_turn(engine, "use bash")

    assert [call.Name for call in policy.calls] == ["bash"]
    assert len(policy.specs) == 1
    assert policy.specs[0][0].Name == "bash"
    assert policy.specs[0][0].Risky is True
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_tool_call_feeds_result_back_to_model() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf pong"),)),
            ModelResponse(Content="tool said pong"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "pong"}, specs=(ToolSpec(Name="bash"),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    await run_turn(engine, "use bash")

    assert engine.State() == machine.StateIdle
    assert [call.Name for call in tools.calls] == ["bash"]
    last_model_input = model.calls[1]
    assert last_model_input.Role == machine.RoleTool
    assert last_model_input.Content == "pong"


@pytest.mark.asyncio
async def test_multiple_tool_calls_feed_all_results_back_to_model() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                ToolCalls=(
                    ToolCall(ID="call-1", Name="first"),
                    ToolCall(ID="call-2", Name="second"),
                )
            ),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"first": "one", "second": "two"},
        specs=(ToolSpec(Name="first"), ToolSpec(Name="second")),
    )
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    await run_turn(engine, "use tools")

    assert [call.Name for call in tools.calls] == ["first", "second"]
    messages = engine.Messages()
    assert len(messages) >= 4
    assistant = messages[1]
    assert assistant.Role == machine.RoleAssistant
    assert assistant.ToolCalls is not None
    assert len(assistant.ToolCalls) == 2
    assert messages[2].Role == machine.RoleTool
    assert messages[2].Content == "one"
    assert messages[2].ToolCallID == "call-1"
    assert messages[3].Role == machine.RoleTool
    assert messages[3].Content == "two"
    assert messages[3].ToolCallID == "call-2"


@pytest.mark.asyncio
async def test_tool_call_assistant_content_is_preserved() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                Content="I will inspect the files.",
                ToolCalls=(ToolCall(ID="call-1", Name="bash", Input="ls"),),
            ),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash"),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    await run_turn(engine, "use bash")

    got = engine.Messages()[1]
    assert got.Role == machine.RoleAssistant
    assert got.Content == "I will inspect the files."


# --- Approval ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiting_approval_keeps_run_context() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(specs=(ToolSpec(Name="bash", Risky=True),))
    runs = DefaultRunController()
    approvals = MemoryApprovalStore()
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, tools)),
        DefaultActionResultResolver(NewDefaultPolicy(), approvals),
        machine.DefaultRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.Ready()

    active: list[bool] = []
    waiter = ScriptedApprovalWaiter([machine.DenyApproval], lambda: active.append(runs.CurrentContext()[1]))
    await run_turn(engine, "danger", waiter)

    assert active == [True]


@pytest.mark.asyncio
async def test_final_assistant_response_finishes_run() -> None:
    model = ScriptedModel([ModelResponse(Content="done")])
    runs = DefaultRunController()
    approvals = MemoryApprovalStore()
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, FakeToolRunner())),
        DefaultActionResultResolver(NewDefaultPolicy(), approvals),
        machine.DefaultRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.Ready()

    await run_turn(engine, "hi")

    assert runs.CurrentContext()[1] is False


def test_cancel_and_reset_clear_run_context() -> None:
    runs = DefaultRunController()
    _, ctx = runs.StartRun(LiveContext())
    assert runs.CurrentContext()[1] is True
    assert ctx is not None

    runs.CancelRun()
    assert runs.CurrentContext()[1] is False

    runs.StartRun(LiveContext())
    runs.InvalidateCurrentRun()
    assert runs.CurrentContext()[1] is False


@pytest.mark.asyncio
async def test_approve_always_writes_store_without_holding_engine_lock() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    store = LockProbeApprovalStore()
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, tools)),
        DefaultActionResultResolver(NewDefaultPolicy(), store),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        store,
        None,
    )
    store.probe = lambda: engine.lock.locked()
    await engine.Ready()

    await run_turn(engine, "danger", ScriptedApprovalWaiter([machine.ApproveAlways]))

    assert store.lock_states == [False]
    assert store.allowed  # the decision really was written


@pytest.mark.asyncio
async def test_approve_always_writes_approval_store_not_policy() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    policy = RecordingPolicy(ToolDecision.DecisionNeedsApproval)
    engine = NewEngineWithExecutorAndPolicy(DefaultScheduledActionExecutor(model, tools), policy, None)
    await engine.Ready()

    await run_turn(engine, "use bash", ScriptedApprovalWaiter([machine.ApproveAlways]))

    # The repeated approved call must run without a second prompt.
    assert len(tools.calls) == 2


@pytest.mark.asyncio
async def test_queued_risky_tool_waits_for_approval() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                ToolCalls=(
                    ToolCall(ID="call-1", Name="first"),
                    ToolCall(ID="call-2", Name="second"),
                )
            ),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"first": "one", "second": "two"},
        specs=(ToolSpec(Name="first"), ToolSpec(Name="second", Risky=True)),
    )
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    observed: list[tuple[list[str], str | None]] = []

    def on_wait() -> None:
        pending, _ok = engine.PendingTool()
        observed.append(([call.Name for call in tools.calls], None if pending is None else pending.Name))

    await run_turn(engine, "use tools", ScriptedApprovalWaiter([machine.ApproveOnce], on_wait))

    assert observed == [(["first"], "second")]
    assert [call.Name for call in tools.calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_risky_tool_waits_for_shortcut_approval() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="rm -rf /"),)),
            ModelResponse(Content="approved"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    observed: list[tuple[machine.State, bool, int]] = []

    def on_wait() -> None:
        _pending, ok = engine.PendingTool()
        observed.append((engine.State(), ok, len(tools.calls)))

    await run_turn(engine, "danger", ScriptedApprovalWaiter([machine.ApproveOnce], on_wait))

    assert observed == [(machine.StateWaitingApproval, True, 0)]
    assert engine.State() == machine.StateIdle


@pytest.mark.asyncio
async def test_tool_risk_comes_from_tool_spec() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="approved"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    observed: list[int] = []
    await run_turn(
        engine, "danger", ScriptedApprovalWaiter([machine.ApproveOnce], lambda: observed.append(len(tools.calls)))
    )

    assert observed == [0]


@pytest.mark.asyncio
async def test_session_run_starts_and_finishes_run() -> None:
    model = ScriptedModel([ModelResponse(Content="hello")])
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    await run_turn(engine, "hi")

    assert engine.Snapshot().State == machine.StateIdle


@pytest.mark.asyncio
async def test_session_run_waits_for_approval_channel() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    waiter = ScriptedApprovalWaiter([machine.ApproveOnce])
    await run_turn(engine, "run bash", waiter)

    assert waiter.requests
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_session_run_returns_error_when_approval_channel_closes() -> None:
    model = ScriptedModel([ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),))])
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    with pytest.raises(Exception) as raised:
        await run_turn(engine, "run bash", ScriptedApprovalWaiter([]))

    assert errors_is(raised.value, ErrApprovalDismissed)
    assert engine.State() == machine.StateIdle
    assert engine.PendingTool()[1] is False
    assert tools.calls == []


@pytest.mark.asyncio
async def test_session_emits_advancing_queue_between_tool_calls() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    states: list[machine.State] = []

    async def observer() -> None:
        states.append(engine.State())

    engine.SetStateObserver(observer)
    await run_turn(engine, "run bash", ScriptedApprovalWaiter([machine.ApproveOnce]))

    assert machine.StateAdvancingQueue in states


@pytest.mark.asyncio
async def test_approve_always_is_scoped_to_tool_name_and_input() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="ls"),)),
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="rm -rf /"),)),
            ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="ls"),)),
            ModelResponse(ToolCalls=(ToolCall(Name="read", Input="/etc/hosts"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"bash": "ok", "read": "ok"},
        specs=(ToolSpec(Name="bash", Risky=True), ToolSpec(Name="read", Risky=True)),
    )
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    waiter = ScriptedApprovalWaiter([machine.ApproveAlways, machine.ApproveOnce, machine.ApproveOnce])
    await run_turn(engine, "run tools", waiter)

    # bash ls (always), bash rm (once), read /etc/hosts (once); the repeated
    # bash ls is remembered and never prompts.
    assert len(waiter.requests) == 3


# --- Cancellation and reset --------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_clears_pending_tool_and_scheduled_actions() -> None:
    model = ScriptedModel([ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="rm -rf /"),))])
    engine = NewEngine(model, FakeToolRunner(specs=(ToolSpec(Name="bash", Risky=True),)), None)
    await engine.Ready()

    waiter = HangingApprovalWaiter()
    task = asyncio.create_task(
        engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="danger"), None, waiter)
    )
    await waiter.requested.wait()
    assert engine.State() == machine.StateWaitingApproval

    await engine.Cancel()
    with pytest.raises(Cancelled):
        await task

    assert engine.State() == machine.StateIdle
    assert engine.PendingTool()[1] is False


@pytest.mark.asyncio
async def test_approval_wait_context_cancel_cancels_engine() -> None:
    model = ScriptedModel([ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="rm -rf /"),))])
    engine = NewEngine(model, FakeToolRunner(specs=(ToolSpec(Name="bash", Risky=True),)), None)
    await engine.Ready()

    waiter = HangingApprovalWaiter()
    ctx = LiveContext()
    task = asyncio.create_task(engine.RunTurn(ctx, machine.UserMessageSubmitted(Content="danger"), None, waiter))
    await waiter.requested.wait()

    ctx.Cancel()
    with pytest.raises(Cancelled):
        await task

    assert engine.State() == machine.StateIdle
    assert engine.PendingTool()[1] is False


@pytest.mark.asyncio
async def test_cancel_drops_stale_model_result() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    task = asyncio.create_task(engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="hi"), None, None))
    await model.started.wait()
    await engine.Cancel()
    release.set()
    await task

    assert [message.Content for message in engine.Messages()] == ["hi"]
    assert engine.State() == machine.StateIdle


@pytest.mark.asyncio
async def test_reset_drops_stale_model_result_and_clears_messages() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    task = asyncio.create_task(engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="hi"), None, None))
    await model.started.wait()
    await engine.Reset()
    release.set()
    await task

    assert engine.Messages() == []
    assert engine.State() == machine.StateIdle


@pytest.mark.asyncio
async def test_reset_preserves_system_messages_and_drops_conversation_messages() -> None:
    initial = [
        machine.Message(Role=machine.RoleSystem, Content="project instructions"),
        machine.Message(Role=machine.RoleUser, Content="old user"),
        machine.Message(Role=machine.RoleAssistant, Content="old assistant"),
        machine.Message(Role=machine.RoleTool, Content="old tool", ToolCallID="call-1", ToolName="bash"),
        machine.Message(Role=machine.RoleSystem, Content="security rules"),
    ]
    engine = NewEngine(ScriptedModel([]), FakeToolRunner(), initial)
    await engine.Ready()

    await engine.Reset()

    assert engine.Messages() == [
        machine.Message(Role=machine.RoleSystem, Content="project instructions"),
        machine.Message(Role=machine.RoleSystem, Content="security rules"),
    ]


@pytest.mark.asyncio
async def test_no_tools_tool_call_is_protocol_error() -> None:
    model = ScriptedModel([ModelResponse(ToolCalls=(ToolCall(Name="bash", Input="printf ok"),))])
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    waiter = ScriptedApprovalWaiter([machine.ApproveOnce])
    with pytest.raises(ValueError, match="model returned tool call while tools are disabled"):
        await run_turn(engine, "use bash", waiter)

    assert engine.State() == machine.StateIdle
    assert waiter.requests == []


@pytest.mark.asyncio
async def test_invalid_second_turn_does_not_cancel_active_run() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    first = asyncio.create_task(
        engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="first"), None, None)
    )
    await model.started.wait()

    with pytest.raises(machine.UnexpectedEventError):
        await engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="second"), None, None)

    release.set()
    await first

    messages = engine.Messages()
    assert len(messages) == 2
    assert messages[1].Role == machine.RoleAssistant
    assert messages[1].Content == "stale"


@pytest.mark.asyncio
async def test_classifier_tool_specs_are_fetched_without_holding_engine_lock() -> None:
    runner = SpecProbeRunner(specs=(ToolSpec(Name="bash"),))
    store = MemoryApprovalStore()
    engine = NewEngineWithComponents(
        runner,
        DefaultActionResultResolver(NewDefaultPolicy(), store),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        store,
        None,
    )
    runner.probe = lambda: engine.lock.locked()
    await engine.Ready()

    await run_turn(engine, "hi")

    assert runner.lock_states
    assert runner.lock_states == [False] * len(runner.lock_states)


def test_run_controller_current_context_reports_missing_context() -> None:
    controller = DefaultRunController()
    assert controller.CurrentContext()[1] is False

    _, ctx = controller.StartRun(LiveContext())
    got, ok = controller.CurrentContext()
    assert ok is True
    assert got is ctx

    controller.CancelRun()
    assert controller.CurrentContext()[1] is False


# --- Transition decisions ----------------------------------------------------


def test_transition_produces_runtime_data_changes_and_scheduled_actions() -> None:
    event = machine.UserMessageSubmitted(Content="hi")
    decision = machine.Transition(transition_snapshot(machine.StateIdle, event), event)

    assert decision.NextState == machine.StateWaitingLLM
    assert len(decision.RuntimeDataChanges) == 1
    assert isinstance(decision.RuntimeDataChanges[0], machine.AppendUserMessage)
    assert len(decision.ActionPlan.Schedule) == 1
    assert isinstance(decision.ActionPlan.Schedule[0], machine.CallModel)


def test_approval_granted_runs_pending_local_tool() -> None:
    call = machine.ToolCall(ID="call-1", Name="bash", Input="pwd")
    event = machine.ApprovalGranted(Call=call)
    decision = machine.Transition(transition_snapshot(machine.StateWaitingApproval, event), event)

    assert decision.NextState == machine.StateRunningTool
    assert len(decision.RuntimeDataChanges) == 2
    assert isinstance(decision.RuntimeDataChanges[0], machine.SetCurrentTool)
    assert len(decision.ActionPlan.Schedule) == 1
    action = decision.ActionPlan.Schedule[0]
    assert isinstance(action, machine.RunTool)
    assert action.Call.Name == call.Name


def test_tool_result_advances_queue_through_engine() -> None:
    call = machine.ToolCall(ID="call-1", Name="bash", Input="pwd")
    event = machine.ToolResultReceived(Call=call, Result="ok")
    decision = machine.Transition(transition_snapshot(machine.StateRunningTool, event), event)

    assert decision.NextState == machine.StateAdvancingQueue
    assert len(decision.ActionPlan.Schedule) == 1
    assert isinstance(decision.ActionPlan.Schedule[0], machine.CheckToolQueue)


def test_denial_advances_queue_through_engine() -> None:
    call = machine.ToolCall(ID="call-1", Name="bash", Input="rm -rf /")
    event = machine.ApprovalDenied(Call=call)
    decision = machine.Transition(transition_snapshot(machine.StateWaitingApproval, event), event)

    assert decision.NextState == machine.StateAdvancingQueue
    assert len(decision.ActionPlan.Schedule) == 1
    assert isinstance(decision.ActionPlan.Schedule[0], machine.CheckToolQueue)


def test_cancel_requested_returns_runtime_to_idle() -> None:
    event = machine.CancelRequested()
    decision = machine.Transition(transition_snapshot(machine.StateWaitingLLM, event), event)

    assert decision.NextState == machine.StateIdle
    assert len(decision.ActionPlan.Schedule) == 0


# --- Engine cases shared with other modules ----------------------------------


@pytest.mark.asyncio
async def test_engine_does_not_commit_invalid_custom_runtime_data_change_result() -> None:
    approvals = MemoryApprovalStore()
    runs = DefaultRunController()
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(NewDefaultPolicy(), approvals),
        _InvalidRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.Ready()

    with pytest.raises(machine.InvariantViolationError):
        await engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="hi"), None, None)

    assert engine.State() == machine.StateIdle
    assert runs.CurrentContext()[1] is False


@pytest.mark.asyncio
async def test_engine_rejects_invalid_permission_mode() -> None:
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(NewDefaultPolicy(), MemoryApprovalStore()),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        MemoryApprovalStore(),
        None,
    )

    with pytest.raises(ValueError, match="invalid permission mode: root"):
        await engine.SetPermissionPolicy(PermissionMode("root"), PermissionRules())


# --- Engine guarantees -------------------------------------------------------


@pytest.mark.asyncio
async def test_state_observer_never_runs_while_engine_lock_is_held() -> None:
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(ID="call-1", Name="bash", Input="printf ok"),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(Name="bash", Risky=True),))
    engine = NewEngine(model, tools, None)
    await engine.Ready()

    lock_held: list[bool] = []

    async def observer() -> None:
        lock_held.append(engine.lock.locked())

    engine.SetStateObserver(observer)
    await run_turn(engine, "use bash", ScriptedApprovalWaiter([machine.ApproveOnce]))

    assert lock_held  # the observer ran
    assert lock_held == [False] * len(lock_held)


@pytest.mark.asyncio
async def test_cancelled_run_cannot_write_into_the_next_run() -> None:
    release = asyncio.Event()
    model = GateModel(release, [ModelResponse(Content="stale"), ModelResponse(Content="fresh")])
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()

    stale_turn = asyncio.create_task(
        engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="first"), None, None)
    )
    await model.started.wait()
    await engine.Cancel()

    # The replacement run starts while the cancelled model call is still parked.
    await engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="second"), None, None)

    release.set()
    await stale_turn

    pairs = [(message.Role, message.Content) for message in engine.Messages()]
    assert (machine.RoleAssistant, "stale") not in pairs
    assert (machine.RoleAssistant, "fresh") in pairs
    assert engine.State() == machine.StateIdle


@pytest.mark.asyncio
async def test_empty_action_queue_in_non_idle_state_raises_invariant_violation() -> None:
    engine = NewEngineWithComponents(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(NewDefaultPolicy(), MemoryApprovalStore()),
        _IdleAsWaitingLLMApplier(),
        DefaultRunController(),
        MemoryApprovalStore(),
        None,
    )

    with pytest.raises(machine.InvariantViolationError) as raised:
        await engine.Ready()

    assert raised.value.Reason == f"action queue is empty in state {machine.StateWaitingLLM}"


@pytest.mark.asyncio
async def test_only_run_turn_accepts_user_message_submitted() -> None:
    engine = NewEngine(ScriptedModel([]), FakeToolRunner(), None)
    await engine.Ready()

    with pytest.raises(ValueError, match=r"user messages must be submitted through Engine\.RunTurn"):
        await engine.DispatchEvent(LiveContext(), machine.UserMessageSubmitted(Content="hi"), None)
