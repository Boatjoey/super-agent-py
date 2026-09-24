"""Interaction-port doubles shared by interactive CLI tests."""

from __future__ import annotations

from collections.abc import Sequence

from super_agent.tui import (
    ROLE_ASSISTANT,
    AgentStatus,
    AgentSummary,
    App,
    ApprovalDecision,
    AttachmentSummary,
    Cancellation,
    Channel,
    ConversationNotification,
    ConversationView,
    MCPServerSummary,
    Message,
    MessageAppended,
    Option,
    PermissionRequest,
    SessionSummary,
    StartupInfo,
    ToolApprovalCleared,
    ToolApprovalRequested,
    ToolCall,
    new,
)


class FakeConversation:
    """A conversation port with scripted notifications."""

    def __init__(self, script: Sequence[ConversationNotification] = ()) -> None:
        self.script = list(script)
        self.queries: list[str] = []
        self.current_permission_mode = ""

    def snapshot(self) -> ConversationView:
        return ConversationView(agent_status=AgentStatus(label="Idle"))

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        self.queries.append(text)
        for notification in self.script:
            notifications.put(notification)
        notifications.close()
        return None

    async def cancel(self) -> BaseException | None:
        return None

    async def reset(self) -> None:
        return None

    async def list_sessions(self) -> list[SessionSummary]:
        return []

    async def resume(self, session_id: str) -> None:
        return None

    async def rename_session(self, session_id: str, title: str) -> None:
        return None

    async def delete_session(self, session_id: str) -> None:
        return None

    async def compact(self, summary: str) -> None:
        return None

    async def undo(self) -> None:
        return None

    async def fork(self, title: str) -> str:
        return "fork"

    async def export(self, format: str) -> str:
        return "/tmp/export"

    async def set_permission_mode(self, mode: str) -> None:
        self.current_permission_mode = mode

    def permission_mode(self) -> str:
        return self.current_permission_mode

    def auto_approve_tools(self) -> bool:
        return self.current_permission_mode == "bypass"

    def list_mcp_servers(self) -> list[MCPServerSummary]:
        return []

    async def add_mcp_server(self, name: str, command: str, args: list[str]) -> None:
        return None

    async def remove_mcp_server(self, name: str) -> None:
        return None

    async def restart_mcp_server(self, name: str) -> None:
        return None

    def list_agents(self) -> list[AgentSummary]:
        return []

    def current_agent(self) -> AgentSummary:
        return AgentSummary()

    async def use_agent(self, name: str) -> None:
        return None

    async def memories(self) -> list[str]:
        return []

    async def remember(self, text: str) -> None:
        return None

    async def forget_memories(self) -> None:
        return None

    async def git_diff(self) -> str:
        return "diff"

    async def git_status(self) -> str:
        return "status"

    async def diagnostics(self, path: str) -> str:
        return "[]"

    def custom_commands(self) -> list[str]:
        return []

    async def expand_custom_command(self, name: str, arguments: str) -> str:
        raise KeyError(name)

    def skills(self) -> list[str]:
        return []

    def plugins(self) -> list[str]:
        return []

    async def attach(self, path: str) -> AttachmentSummary:
        return AttachmentSummary(name=path, mime="text/plain")

    async def pending_attachments(self) -> tuple[AttachmentSummary, ...]:
        return ()


class ApprovalConversation(FakeConversation):
    """A turn that completes after one inline approval."""

    def __init__(self) -> None:
        super().__init__()
        self.decisions: list[ApprovalDecision] = []

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        self.queries.append(text)
        notifications.put(
            ToolApprovalRequested(
                tool_call=ToolCall(name="bash", input="printf ok"),
                request=PermissionRequest(tool_name="bash", command_class="read-only", cwd="/repo", reason="risky"),
                batch_index=1,
                batch_total=1,
            )
        )
        decision = await approvals.get()
        if decision is not None:
            self.decisions.append(decision)
        notifications.put(ToolApprovalCleared())
        notifications.put(MessageAppended(message=Message(role=ROLE_ASSISTANT, content="approved")))
        notifications.close()
        return None


def new_app(fake: FakeConversation, *options: Option) -> App:
    return new(fake, StartupInfo(model_name="test-model"), *options)
