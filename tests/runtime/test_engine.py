"""The engine: the run lifecycle, the action loop, and the ports around it.

These cases drive the engine through ``session.run_turn``. The session layer is
not exercised here, so the cases whose subject is engine behaviour call
``Engine.run_turn`` directly with an approval waiter in place of the approval
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
    new_engine,
    new_engine_with_components,
    new_engine_with_executor,
    new_engine_with_executor_and_policy,
)
from super_agent.runtime.execution import (
    ERR_APPROVAL_DISMISSED,
    ApprovalWaiter,
    DefaultActionResultResolver,
    DefaultRunController,
    DefaultScheduledActionExecutor,
    DefaultScheduledActionRunner,
    MemoryApprovalStore,
    PermissionMode,
    PermissionRules,
    ToolDecision,
    new_default_policy,
)
from super_agent.runtime.protocol.run_context import live_context
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
    await engine.run_turn(live_context(), machine.UserMessageSubmitted(content=content), None, waiter)


class _InvalidRuntimeDataChangeApplier:
    """An applier that lies about what it applied.

    Every state except ``Initializing`` gets an impossible result back, which is
    exactly the mistake the engine's post-apply validation exists to catch.
    """

    def apply_runtime_data_changes(
        self, runtime_data: machine.RuntimeData, result: machine.TransitionResult
    ) -> machine.RuntimeDataChangeResult:
        if runtime_data.state == machine.STATE_INITIALIZING:
            return machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(runtime_data, result)
        return machine.RuntimeDataChangeResult(
            runtime_data=machine.RuntimeData(state=machine.STATE_IDLE, tool_batch=machine.ToolCallBatch())
        )


class _IdleAsWaitingLLMApplier:
    """Commits a non-``Idle`` state with nothing queued, to exercise the loop guard."""

    def apply_runtime_data_changes(
        self, runtime_data: machine.RuntimeData, result: machine.TransitionResult
    ) -> machine.RuntimeDataChangeResult:
        if result.next_state == machine.STATE_IDLE:
            return machine.RuntimeDataChangeResult(runtime_data=machine.RuntimeData(state=machine.STATE_WAITING_LLM))
        return machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(runtime_data, result)


# --- Constructors and the ready handshake -----------------------------------


@pytest.mark.asyncio
async def test_new_engine_starts_initializing_and_ready_enters_idle() -> None:
    engine = new_engine(ScriptedModel([]), FakeToolRunner(), None)

    assert engine.state() == machine.STATE_INITIALIZING
    await engine.ready()
    assert engine.state() == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_ready_returns_error_when_already_ready() -> None:
    engine = new_engine(ScriptedModel([]), FakeToolRunner(), None)

    await engine.ready()
    with pytest.raises(machine.UnexpectedEventError):
        await engine.ready()


@pytest.mark.asyncio
async def test_session_run_produces_content() -> None:
    model = ScriptedModel([ModelResponse(content="hello", reasoning_content="thinking")])
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    await run_turn(engine, "hi")

    assert engine.state() == machine.STATE_IDLE
    got = engine.messages()[1]
    assert got.role == machine.ROLE_ASSISTANT
    assert got.content == "hello"
    assert got.reasoning_content == "thinking"


@pytest.mark.asyncio
async def test_cancel_flushes_streaming_assistant_message() -> None:
    model = StreamingCancelModel()
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    ctx = live_context()
    task = asyncio.create_task(
        engine.run_turn(ctx, machine.UserMessageSubmitted(content="hi"), lambda _chunk: None, None)
    )
    await model.started.wait()
    ctx.cancel()
    with pytest.raises(Cancelled):
        await task

    messages = engine.messages()
    assert len(messages) == 2
    got = messages[1]
    assert got.role == machine.ROLE_ASSISTANT
    assert got.content == "partial"
    assert got.reasoning_content == "thinking"
    assert got.interrupted is True
    assert engine.snapshot().streaming_message is None


# --- Model and tool execution -----------------------------------------------


@pytest.mark.asyncio
async def test_engine_runs_scheduled_actions_through_injected_executor() -> None:
    executor = RecordingExecutor()
    engine = new_engine_with_executor(executor, None)
    await engine.ready()

    await run_turn(engine, "hi")

    assert len(executor.actions) == 1
    assert isinstance(executor.actions[0], machine.CallModel)
    assert engine.messages()[1].content == "from executor"


@pytest.mark.asyncio
async def test_model_failure_leaves_no_fabricated_tool_message() -> None:
    executor = FailingOnceExecutor()
    engine = new_engine_with_executor(executor, None)
    await engine.ready()

    with pytest.raises(RuntimeError, match="provider timeout"):
        await run_turn(engine, "hi")

    # A model call that fails before requesting any tool must not leave a tool
    # message behind. A tool result with no matching tool call is rejected by
    # the provider on the next request, and the transcript is persisted.
    messages = engine.messages()
    assert len(messages) == 1
    assert messages[0].role == machine.ROLE_USER

    await run_turn(engine, "continue")

    assert executor.calls == 2
    assert len(executor.seen) == 2
    for message in executor.seen:
        assert message.role != machine.ROLE_TOOL


@pytest.mark.asyncio
async def test_failed_tool_batch_answers_every_tool_call() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(id="call-1", name="bash", input="printf one"),
                    ToolCall(id="call-2", name="bash", input="printf two"),
                    ToolCall(id="call-3", name="bash", input="printf three"),
                )
            ),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine_with_executor_and_policy(
        DefaultScheduledActionExecutor(model, tools), DenyNthPolicy(deny_at=2), None
    )
    await engine.ready()

    await run_turn(engine, "run tools")

    answered: list[str] = []
    denied = ""
    for message in engine.messages():
        if message.role == machine.ROLE_TOOL:
            answered.append(message.tool_call_id)
            if message.tool_call_id == "call-2":
                denied = message.content
    assert answered == ["call-1", "call-2", "call-3"]
    assert denied == "denied by permission policy: denied by test policy"
    assert [call.id for call in tools.calls] == ["call-1", "call-3"]


@pytest.mark.asyncio
async def test_custom_policy_classifies_tool_call_with_input() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    policy = RecordingPolicy(ToolDecision.DECISION_RUN_DIRECTLY)
    engine = new_engine_with_executor_and_policy(DefaultScheduledActionExecutor(model, tools), policy, None)
    await engine.ready()

    await run_turn(engine, "use bash")

    assert [call.name for call in policy.calls] == ["bash"]
    assert len(policy.specs) == 1
    assert policy.specs[0][0].name == "bash"
    assert policy.specs[0][0].risky is True
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_tool_call_feeds_result_back_to_model() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf pong"),)),
            ModelResponse(content="tool said pong"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "pong"}, specs=(ToolSpec(name="bash"),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    await run_turn(engine, "use bash")

    assert engine.state() == machine.STATE_IDLE
    assert [call.name for call in tools.calls] == ["bash"]
    last_model_input = model.calls[1]
    assert last_model_input.role == machine.ROLE_TOOL
    assert last_model_input.content == "pong"


@pytest.mark.asyncio
async def test_multiple_tool_calls_feed_all_results_back_to_model() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(id="call-1", name="first"),
                    ToolCall(id="call-2", name="second"),
                )
            ),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"first": "one", "second": "two"},
        specs=(ToolSpec(name="first"), ToolSpec(name="second")),
    )
    engine = new_engine(model, tools, None)
    await engine.ready()

    await run_turn(engine, "use tools")

    assert [call.name for call in tools.calls] == ["first", "second"]
    messages = engine.messages()
    assert len(messages) >= 4
    assistant = messages[1]
    assert assistant.role == machine.ROLE_ASSISTANT
    assert assistant.tool_calls is not None
    assert len(assistant.tool_calls) == 2
    assert messages[2].role == machine.ROLE_TOOL
    assert messages[2].content == "one"
    assert messages[2].tool_call_id == "call-1"
    assert messages[3].role == machine.ROLE_TOOL
    assert messages[3].content == "two"
    assert messages[3].tool_call_id == "call-2"


@pytest.mark.asyncio
async def test_tool_call_assistant_content_is_preserved() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                content="I will inspect the files.",
                tool_calls=(ToolCall(id="call-1", name="bash", input="ls"),),
            ),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash"),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    await run_turn(engine, "use bash")

    got = engine.messages()[1]
    assert got.role == machine.ROLE_ASSISTANT
    assert got.content == "I will inspect the files."


# --- Approval ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiting_approval_keeps_run_context() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(specs=(ToolSpec(name="bash", risky=True),))
    runs = DefaultRunController()
    approvals = MemoryApprovalStore()
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, tools)),
        DefaultActionResultResolver(new_default_policy(), approvals),
        machine.DefaultRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.ready()

    active: list[bool] = []
    waiter = ScriptedApprovalWaiter([machine.DENY_APPROVAL], lambda: active.append(runs.current_context()[1]))
    await run_turn(engine, "danger", waiter)

    assert active == [True]


@pytest.mark.asyncio
async def test_final_assistant_response_finishes_run() -> None:
    model = ScriptedModel([ModelResponse(content="done")])
    runs = DefaultRunController()
    approvals = MemoryApprovalStore()
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, FakeToolRunner())),
        DefaultActionResultResolver(new_default_policy(), approvals),
        machine.DefaultRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.ready()

    await run_turn(engine, "hi")

    assert runs.current_context()[1] is False


def test_cancel_and_reset_clear_run_context() -> None:
    runs = DefaultRunController()
    _, ctx = runs.start_run(live_context())
    assert runs.current_context()[1] is True
    assert ctx is not None

    runs.cancel_run()
    assert runs.current_context()[1] is False

    runs.start_run(live_context())
    runs.invalidate_current_run()
    assert runs.current_context()[1] is False


@pytest.mark.asyncio
async def test_approve_always_writes_store_without_holding_engine_lock() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    store = LockProbeApprovalStore()
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(model, tools)),
        DefaultActionResultResolver(new_default_policy(), store),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        store,
        None,
    )
    store.probe = lambda: engine.lock.locked()
    await engine.ready()

    await run_turn(engine, "danger", ScriptedApprovalWaiter([machine.APPROVE_ALWAYS]))

    assert store.lock_states == [False]
    assert store.allowed  # the decision really was written


@pytest.mark.asyncio
async def test_approve_always_writes_approval_store_not_policy() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    policy = RecordingPolicy(ToolDecision.DECISION_NEEDS_APPROVAL)
    engine = new_engine_with_executor_and_policy(DefaultScheduledActionExecutor(model, tools), policy, None)
    await engine.ready()

    await run_turn(engine, "use bash", ScriptedApprovalWaiter([machine.APPROVE_ALWAYS]))

    # The repeated approved call must run without a second prompt.
    assert len(tools.calls) == 2


@pytest.mark.asyncio
async def test_queued_risky_tool_waits_for_approval() -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ToolCall(id="call-1", name="first"),
                    ToolCall(id="call-2", name="second"),
                )
            ),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"first": "one", "second": "two"},
        specs=(ToolSpec(name="first"), ToolSpec(name="second", risky=True)),
    )
    engine = new_engine(model, tools, None)
    await engine.ready()

    observed: list[tuple[list[str], str | None]] = []

    def on_wait() -> None:
        pending, _ok = engine.pending_tool()
        observed.append(([call.name for call in tools.calls], None if pending is None else pending.name))

    await run_turn(engine, "use tools", ScriptedApprovalWaiter([machine.APPROVE_ONCE], on_wait))

    assert observed == [(["first"], "second")]
    assert [call.name for call in tools.calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_risky_tool_waits_for_shortcut_approval() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="rm -rf /"),)),
            ModelResponse(content="approved"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    observed: list[tuple[machine.State, bool, int]] = []

    def on_wait() -> None:
        _pending, ok = engine.pending_tool()
        observed.append((engine.state(), ok, len(tools.calls)))

    await run_turn(engine, "danger", ScriptedApprovalWaiter([machine.APPROVE_ONCE], on_wait))

    assert observed == [(machine.STATE_WAITING_APPROVAL, True, 0)]
    assert engine.state() == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_tool_risk_comes_from_tool_spec() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="approved"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    observed: list[int] = []
    await run_turn(
        engine, "danger", ScriptedApprovalWaiter([machine.APPROVE_ONCE], lambda: observed.append(len(tools.calls)))
    )

    assert observed == [0]


@pytest.mark.asyncio
async def test_session_run_starts_and_finishes_run() -> None:
    model = ScriptedModel([ModelResponse(content="hello")])
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    await run_turn(engine, "hi")

    assert engine.snapshot().state == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_session_run_waits_for_approval_channel() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    waiter = ScriptedApprovalWaiter([machine.APPROVE_ONCE])
    await run_turn(engine, "run bash", waiter)

    assert waiter.requests
    assert len(tools.calls) == 1


@pytest.mark.asyncio
async def test_session_run_returns_error_when_approval_channel_closes() -> None:
    model = ScriptedModel([ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),))])
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    with pytest.raises(Exception) as raised:
        await run_turn(engine, "run bash", ScriptedApprovalWaiter([]))

    assert errors_is(raised.value, ERR_APPROVAL_DISMISSED)
    assert engine.state() == machine.STATE_IDLE
    assert engine.pending_tool()[1] is False
    assert tools.calls == []


@pytest.mark.asyncio
async def test_session_emits_advancing_queue_between_tool_calls() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    states: list[machine.State] = []

    async def observer() -> None:
        states.append(engine.state())

    engine.set_state_observer(observer)
    await run_turn(engine, "run bash", ScriptedApprovalWaiter([machine.APPROVE_ONCE]))

    assert machine.STATE_ADVANCING_QUEUE in states


@pytest.mark.asyncio
async def test_approve_always_is_scoped_to_tool_name_and_input() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(name="bash", input="ls"),)),
            ModelResponse(tool_calls=(ToolCall(name="bash", input="rm -rf /"),)),
            ModelResponse(tool_calls=(ToolCall(name="bash", input="ls"),)),
            ModelResponse(tool_calls=(ToolCall(name="read", input="/etc/hosts"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(
        results={"bash": "ok", "read": "ok"},
        specs=(ToolSpec(name="bash", risky=True), ToolSpec(name="read", risky=True)),
    )
    engine = new_engine(model, tools, None)
    await engine.ready()

    waiter = ScriptedApprovalWaiter([machine.APPROVE_ALWAYS, machine.APPROVE_ONCE, machine.APPROVE_ONCE])
    await run_turn(engine, "run tools", waiter)

    # bash ls (always), bash rm (once), read /etc/hosts (once); the repeated
    # bash ls is remembered and never prompts.
    assert len(waiter.requests) == 3


# --- Cancellation and reset --------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_clears_pending_tool_and_scheduled_actions() -> None:
    model = ScriptedModel([ModelResponse(tool_calls=(ToolCall(name="bash", input="rm -rf /"),))])
    engine = new_engine(model, FakeToolRunner(specs=(ToolSpec(name="bash", risky=True),)), None)
    await engine.ready()

    waiter = HangingApprovalWaiter()
    task = asyncio.create_task(
        engine.run_turn(live_context(), machine.UserMessageSubmitted(content="danger"), None, waiter)
    )
    await waiter.requested.wait()
    assert engine.state() == machine.STATE_WAITING_APPROVAL

    await engine.cancel()
    with pytest.raises(Cancelled):
        await task

    assert engine.state() == machine.STATE_IDLE
    assert engine.pending_tool()[1] is False


@pytest.mark.asyncio
async def test_approval_wait_context_cancel_cancels_engine() -> None:
    model = ScriptedModel([ModelResponse(tool_calls=(ToolCall(name="bash", input="rm -rf /"),))])
    engine = new_engine(model, FakeToolRunner(specs=(ToolSpec(name="bash", risky=True),)), None)
    await engine.ready()

    waiter = HangingApprovalWaiter()
    ctx = live_context()
    task = asyncio.create_task(engine.run_turn(ctx, machine.UserMessageSubmitted(content="danger"), None, waiter))
    await waiter.requested.wait()

    ctx.cancel()
    with pytest.raises(Cancelled):
        await task

    assert engine.state() == machine.STATE_IDLE
    assert engine.pending_tool()[1] is False


@pytest.mark.asyncio
async def test_cancel_drops_stale_model_result() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    task = asyncio.create_task(engine.run_turn(live_context(), machine.UserMessageSubmitted(content="hi"), None, None))
    await model.started.wait()
    await engine.cancel()
    release.set()
    await task

    assert [message.content for message in engine.messages()] == ["hi"]
    assert engine.state() == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_reset_drops_stale_model_result_and_clears_messages() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    task = asyncio.create_task(engine.run_turn(live_context(), machine.UserMessageSubmitted(content="hi"), None, None))
    await model.started.wait()
    await engine.reset()
    release.set()
    await task

    assert engine.messages() == []
    assert engine.state() == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_reset_preserves_system_messages_and_drops_conversation_messages() -> None:
    initial = [
        machine.Message(role=machine.ROLE_SYSTEM, content="project instructions"),
        machine.Message(role=machine.ROLE_USER, content="old user"),
        machine.Message(role=machine.ROLE_ASSISTANT, content="old assistant"),
        machine.Message(role=machine.ROLE_TOOL, content="old tool", tool_call_id="call-1", tool_name="bash"),
        machine.Message(role=machine.ROLE_SYSTEM, content="security rules"),
    ]
    engine = new_engine(ScriptedModel([]), FakeToolRunner(), initial)
    await engine.ready()

    await engine.reset()

    assert engine.messages() == [
        machine.Message(role=machine.ROLE_SYSTEM, content="project instructions"),
        machine.Message(role=machine.ROLE_SYSTEM, content="security rules"),
    ]


@pytest.mark.asyncio
async def test_no_tools_tool_call_is_protocol_error() -> None:
    model = ScriptedModel([ModelResponse(tool_calls=(ToolCall(name="bash", input="printf ok"),))])
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    waiter = ScriptedApprovalWaiter([machine.APPROVE_ONCE])
    with pytest.raises(ValueError, match="model returned tool call while tools are disabled"):
        await run_turn(engine, "use bash", waiter)

    assert engine.state() == machine.STATE_IDLE
    assert waiter.requests == []


@pytest.mark.asyncio
async def test_invalid_second_turn_does_not_cancel_active_run() -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    first = asyncio.create_task(
        engine.run_turn(live_context(), machine.UserMessageSubmitted(content="first"), None, None)
    )
    await model.started.wait()

    with pytest.raises(machine.UnexpectedEventError):
        await engine.run_turn(live_context(), machine.UserMessageSubmitted(content="second"), None, None)

    release.set()
    await first

    messages = engine.messages()
    assert len(messages) == 2
    assert messages[1].role == machine.ROLE_ASSISTANT
    assert messages[1].content == "stale"


@pytest.mark.asyncio
async def test_classifier_tool_specs_are_fetched_without_holding_engine_lock() -> None:
    runner = SpecProbeRunner(specs=(ToolSpec(name="bash"),))
    store = MemoryApprovalStore()
    engine = new_engine_with_components(
        runner,
        DefaultActionResultResolver(new_default_policy(), store),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        store,
        None,
    )
    runner.probe = lambda: engine.lock.locked()
    await engine.ready()

    await run_turn(engine, "hi")

    assert runner.lock_states
    assert runner.lock_states == [False] * len(runner.lock_states)


def test_run_controller_current_context_reports_missing_context() -> None:
    controller = DefaultRunController()
    assert controller.current_context()[1] is False

    _, ctx = controller.start_run(live_context())
    got, ok = controller.current_context()
    assert ok is True
    assert got is ctx

    controller.cancel_run()
    assert controller.current_context()[1] is False


# --- Transition decisions ----------------------------------------------------


def test_transition_produces_runtime_data_changes_and_scheduled_actions() -> None:
    event = machine.UserMessageSubmitted(content="hi")
    decision = machine.transition(transition_snapshot(machine.STATE_IDLE, event), event)

    assert decision.next_state == machine.STATE_WAITING_LLM
    assert len(decision.runtime_data_changes) == 1
    assert isinstance(decision.runtime_data_changes[0], machine.AppendUserMessage)
    assert len(decision.action_plan.schedule) == 1
    assert isinstance(decision.action_plan.schedule[0], machine.CallModel)


def test_approval_granted_runs_pending_local_tool() -> None:
    call = machine.ToolCall(id="call-1", name="bash", input="pwd")
    event = machine.ApprovalGranted(call=call)
    decision = machine.transition(transition_snapshot(machine.STATE_WAITING_APPROVAL, event), event)

    assert decision.next_state == machine.STATE_RUNNING_TOOL
    assert len(decision.runtime_data_changes) == 2
    assert isinstance(decision.runtime_data_changes[0], machine.SetCurrentTool)
    assert len(decision.action_plan.schedule) == 1
    action = decision.action_plan.schedule[0]
    assert isinstance(action, machine.RunTool)
    assert action.call.name == call.name


def test_tool_result_advances_queue_through_engine() -> None:
    call = machine.ToolCall(id="call-1", name="bash", input="pwd")
    event = machine.ToolResultReceived(call=call, result="ok")
    decision = machine.transition(transition_snapshot(machine.STATE_RUNNING_TOOL, event), event)

    assert decision.next_state == machine.STATE_ADVANCING_QUEUE
    assert len(decision.action_plan.schedule) == 1
    assert isinstance(decision.action_plan.schedule[0], machine.CheckToolQueue)


def test_denial_advances_queue_through_engine() -> None:
    call = machine.ToolCall(id="call-1", name="bash", input="rm -rf /")
    event = machine.ApprovalDenied(call=call)
    decision = machine.transition(transition_snapshot(machine.STATE_WAITING_APPROVAL, event), event)

    assert decision.next_state == machine.STATE_ADVANCING_QUEUE
    assert len(decision.action_plan.schedule) == 1
    assert isinstance(decision.action_plan.schedule[0], machine.CheckToolQueue)


def test_cancel_requested_returns_runtime_to_idle() -> None:
    event = machine.CancelRequested()
    decision = machine.transition(transition_snapshot(machine.STATE_WAITING_LLM, event), event)

    assert decision.next_state == machine.STATE_IDLE
    assert len(decision.action_plan.schedule) == 0


# --- Engine cases shared with other modules ----------------------------------


@pytest.mark.asyncio
async def test_engine_does_not_commit_invalid_custom_runtime_data_change_result() -> None:
    approvals = MemoryApprovalStore()
    runs = DefaultRunController()
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(new_default_policy(), approvals),
        _InvalidRuntimeDataChangeApplier(),
        runs,
        approvals,
        None,
    )
    await engine.ready()

    with pytest.raises(machine.InvariantViolationError):
        await engine.run_turn(live_context(), machine.UserMessageSubmitted(content="hi"), None, None)

    assert engine.state() == machine.STATE_IDLE
    assert runs.current_context()[1] is False


@pytest.mark.asyncio
async def test_engine_rejects_invalid_permission_mode() -> None:
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(new_default_policy(), MemoryApprovalStore()),
        machine.DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        MemoryApprovalStore(),
        None,
    )

    with pytest.raises(ValueError, match="invalid permission mode: root"):
        await engine.set_permission_policy(PermissionMode("root"), PermissionRules())


# --- Engine guarantees -------------------------------------------------------


@pytest.mark.asyncio
async def test_state_observer_never_runs_while_engine_lock_is_held() -> None:
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(id="call-1", name="bash", input="printf ok"),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner(results={"bash": "ok"}, specs=(ToolSpec(name="bash", risky=True),))
    engine = new_engine(model, tools, None)
    await engine.ready()

    lock_held: list[bool] = []

    async def observer() -> None:
        lock_held.append(engine.lock.locked())

    engine.set_state_observer(observer)
    await run_turn(engine, "use bash", ScriptedApprovalWaiter([machine.APPROVE_ONCE]))

    assert lock_held  # the observer ran
    assert lock_held == [False] * len(lock_held)


@pytest.mark.asyncio
async def test_cancelled_run_cannot_write_into_the_next_run() -> None:
    release = asyncio.Event()
    model = GateModel(release, [ModelResponse(content="stale"), ModelResponse(content="fresh")])
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()

    stale_turn = asyncio.create_task(
        engine.run_turn(live_context(), machine.UserMessageSubmitted(content="first"), None, None)
    )
    await model.started.wait()
    await engine.cancel()

    # The replacement run starts while the cancelled model call is still parked.
    await engine.run_turn(live_context(), machine.UserMessageSubmitted(content="second"), None, None)

    release.set()
    await stale_turn

    pairs = [(message.role, message.content) for message in engine.messages()]
    assert (machine.ROLE_ASSISTANT, "stale") not in pairs
    assert (machine.ROLE_ASSISTANT, "fresh") in pairs
    assert engine.state() == machine.STATE_IDLE


@pytest.mark.asyncio
async def test_empty_action_queue_in_non_idle_state_raises_invariant_violation() -> None:
    engine = new_engine_with_components(
        DefaultScheduledActionRunner(DefaultScheduledActionExecutor(None, None)),
        DefaultActionResultResolver(new_default_policy(), MemoryApprovalStore()),
        _IdleAsWaitingLLMApplier(),
        DefaultRunController(),
        MemoryApprovalStore(),
        None,
    )

    with pytest.raises(machine.InvariantViolationError) as raised:
        await engine.ready()

    assert raised.value.reason == f"action queue is empty in state {machine.STATE_WAITING_LLM}"


@pytest.mark.asyncio
async def test_only_run_turn_accepts_user_message_submitted() -> None:
    engine = new_engine(ScriptedModel([]), FakeToolRunner(), None)
    await engine.ready()

    with pytest.raises(ValueError, match=r"user messages must be submitted through Engine\.RunTurn"):
        await engine.dispatch_event(live_context(), machine.UserMessageSubmitted(content="hi"), None)
