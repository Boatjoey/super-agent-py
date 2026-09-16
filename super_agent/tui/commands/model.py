"""The slash-command catalogue, its input semantics, and its background work.

Ported from the Go ``tui/commands/model.go``. The feature owns which commands
exist, what each one means, and the operations it starts; the root owns the
effects a command asks for, including every cross-feature one.

One Go signature changes shape: ``Handle`` is a coroutine. Go calls ports such as
``Workspace.Diagnostics`` from the update goroutine and blocks it; Python must
await them, so the caller awaits :meth:`Model.Handle`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine

from super_agent.tui.commands import catalog, format
from super_agent.tui.commands.outcome import CompactDone, MCPDone, Outcome, StatusBar
from super_agent.tui.commands.ports import Attachment, Ports

__all__ = ["Command", "Config", "Input", "Model", "New"]

type Command = Callable[[], Coroutine[object, object, "CompactDone | MCPDone"]]
"""A background operation this feature hands the runtime."""


@dataclasses.dataclass(frozen=True, slots=True)
class Config:
    """The session settings commands display but do not own."""

    CWD: str = ""
    InstructionPaths: tuple[str, ...] = ()
    NoTools: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Input:
    """What the root knows about a submission and the command cannot own."""

    Text: str = ""
    Attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(slots=True)
class Model:
    """The catalogue, its input semantics, and the background work it started."""

    ports: Ports
    config: Config
    customCommands: tuple[str, ...] = ()
    compacting: bool = False
    managingMCP: bool = False

    def Compacting(self) -> bool:
        """Whether ``/compact`` is still running."""
        return self.compacting

    def ManagingMCP(self) -> bool:
        """Whether an MCP lifecycle change is still running."""
        return self.managingMCP

    def Palette(self) -> tuple[catalog.Command, ...]:
        """The palette the composer offers."""
        return catalog.Palette(self)

    def Update(self, message: CompactDone | MCPDone) -> tuple[Model, Outcome | None]:
        """Apply the result of a background command.

        A ``None`` outcome means the message did not belong to this feature.
        """
        if isinstance(message, CompactDone):
            self.compacting = False
            if message.Err is not None:
                return self, Outcome(Err=f"Compact failed: {message.Err}")
            return self, Outcome(
                Status="Compacted conversation",
                Output=format.divider("Compacted conversation"),
                RefreshSnapshot=True,
            )
        # The union is closed, so anything that is not a compaction is an MCP run.
        self.managingMCP = False
        if message.Err is not None:
            return self, Outcome(Err=f"MCP failed: {message.Err}")
        return self, Outcome(Status=message.Status)

    async def Handle(self, input: Input) -> tuple[Model, Outcome | None, Command | None]:
        """Run the slash command in ``input.Text``. Callers check ``IsCommand`` first."""
        parts = input.Text.split()
        command = parts[0]
        match command:
            case "/agent":
                return self, await self._handleAgent(parts), None
            case "/plan":
                return self, await self._handleAgent(["/agent", "plan"]), None
            case "/build":
                return self, await self._handleAgent(["/agent", "build"]), None
            case "/mode":
                if len(parts) != 2 or parts[1] not in ("plan", "build"):
                    return self, Outcome(Err="Usage: /mode <plan|build>"), None
                return self, await self._handleAgent(["/agent", parts[1]]), None
            case "/fork":
                return self, await self._handleFork(input.Text.removeprefix(command).strip()), None
            case "/memory":
                return self, await self._handleMemory(), None
            case "/remember":
                return self, await self._handleRemember(input.Text.removeprefix(command).strip()), None
            case "/forget":
                return self, await self._handleForget(), None
            case "/diff":
                return self, await self._handleGitDiff(), None
            case "/branch":
                return self, await self._handleGitStatus(), None
            case "/review":
                return self, Outcome(Prompt=_REVIEW_PROMPT), None
            case "/fix-ci":
                return self, Outcome(Prompt=_FIX_CI_PROMPT), None
            case "/commit-message":
                return self, Outcome(Prompt=_COMMIT_MESSAGE_PROMPT), None
            case "/export":
                if len(parts) != 2:
                    return self, Outcome(Err="Usage: /export <markdown|json>"), None
                return self, await self._handleExport(parts[1]), None
            case "/share":
                return self, await self._handleExport("html"), None
            case "/attach":
                if len(parts) != 2:
                    return self, Outcome(Err="Usage: /attach <path>"), None
                return self, Outcome(Status="Attaching…", AttachPath=parts[1]), None
            case "/attachments":
                if not input.Attachments:
                    return self, Outcome(Status="No pending attachments"), None
                names = [f"{item.Name} ({item.MIME})" for item in input.Attachments]
                return self, Outcome(Status="Attachments", Output="Attachments:\n- " + "\n- ".join(names)), None
            case "/commands":
                return self, self._handleNamedItems("Custom commands"), None
            case "/skills":
                return self, self._handleNamedItems("Skills"), None
            case "/plugins":
                return self, self._handleNamedItems("Plugins"), None
            case "/diagnostics":
                if len(parts) != 2:
                    return self, Outcome(Err="Usage: /diagnostics <path>"), None
                try:
                    result = await self.ports.Workspace.Diagnostics(parts[1])
                except Exception as err:
                    return self, Outcome(Err=f"Diagnostics failed: {err}"), None
                return self, Outcome(Status="Diagnostics", Output=result), None
            case "/instructions":
                return self, self._handleInstructions(), None
            case "/permissions":
                return self, await self._handlePermissions(parts), None
            case "/mcp":
                return self._handleMCP(parts)
            case "/clear" | "/reset":
                return self, await self._handleReset(), None
            case "/sessions":
                return self, await self._handleSessions(), None
            case "/resume":
                return self, await self._handleResume(parts), None
            case "/rename":
                return self, await self._handleRename(input.Text, parts), None
            case "/delete-session":
                return self, await self._handleDelete(parts), None
            case "/compact":
                return self._handleCompact(input.Text, command)
            case "/undo":
                return self, await self._handleUndo(), None
            case "/quit" | "/exit":
                return self, Outcome(Quit=True), None
            case "/help":
                return self, Outcome(ShowHelp=True), None
            case _:
                arguments = input.Text.removeprefix(command).strip()
                try:
                    expanded = await self.ports.Extensions.ExpandCustomCommand(command.removeprefix("/"), arguments)
                except Exception:
                    return self, Outcome(Err=f"Unknown command: {command}"), None
                return self, Outcome(Prompt=expanded), None

    def _handleCompact(self, text: str, command: str) -> tuple[Model, Outcome | None, Command | None]:
        """Pass the optional summary; the keep-newest policy stays in the runtime."""
        summary = text.removeprefix(command).strip()
        self.compacting = True

        async def run() -> CompactDone:
            try:
                await self.ports.Sessions.Compact(summary)
            except Exception as err:
                return CompactDone(Err=err)
            return CompactDone()

        return self, Outcome(Status="Compacting conversation…"), run

    def _handleMCP(self, parts: list[str]) -> tuple[Model, Outcome | None, Command | None]:
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            return (
                self,
                Outcome(Status="MCP servers", Output=format.formatMCPServers(self.ports.MCP.ListMCPServers())),
                None,
            )
        operation = parts[1]
        match operation:
            case "add":
                if len(parts) < 4:
                    return self, Outcome(Err="Usage: /mcp add <name> <command> [args...]"), None
                name, command, args = parts[2], parts[3], list(parts[4:])
                status = "Added MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.MCP.AddMCPServer(name, command, args))
            case "remove":
                if len(parts) != 3:
                    return self, Outcome(Err="Usage: /mcp remove <name>"), None
                name = parts[2]
                status = "Removed MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.MCP.RemoveMCPServer(name))
            case "restart":
                if len(parts) != 3:
                    return self, Outcome(Err="Usage: /mcp restart <name>"), None
                name = parts[2]
                status = "Restarted MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.MCP.RestartMCPServer(name))
            case _:
                return self, Outcome(Err="Usage: /mcp <list|add|remove|restart>"), None
        self.managingMCP = True
        return self, Outcome(Status="Updating MCP servers…"), run

    def _mcpCommand(self, status: str, operation: Callable[[], Coroutine[object, object, None]]) -> Command:
        """Wrap one MCP lifecycle call as the command that reports its outcome."""

        async def change() -> MCPDone:
            try:
                await operation()
            except Exception as err:
                return MCPDone(Status=status, Err=err)
            return MCPDone(Status=status)

        return change

    async def _handleAgent(self, parts: list[str]) -> Outcome:
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            current = self.ports.Agents.CurrentAgent().Name
            rows = [
                ("* " if profile.Name == current else "  ")
                + f"{profile.Name} ({profile.Provider}/{profile.Model}, {profile.PermissionMode})"
                for profile in self.ports.Agents.ListAgents()
            ]
            return Outcome(Status="Agents", Output="\n".join(rows))
        if len(parts) != 2:
            return Outcome(Err="Usage: /agent <list|name>")
        try:
            await self.ports.Agents.UseAgent(parts[1])
        except Exception as err:
            return Outcome(Err=f"Agent failed: {err}")
        profile = self.ports.Agents.CurrentAgent()
        return Outcome(
            Status="Using agent " + profile.Name,
            RefreshSnapshot=True,
            StatusBar=StatusBar(ModelName=profile.Model, PermissionMode=profile.PermissionMode),
        )

    def _handleNamedItems(self, label: str) -> Outcome:
        match label:
            case "Custom commands":
                items = self.ports.Extensions.CustomCommands()
            case "Skills":
                items = self.ports.Extensions.Skills()
            case _:
                items = self.ports.Extensions.Plugins()
        return Outcome(Status=label, Output=format.formatNamedItems(label, items))

    def _handleInstructions(self) -> Outcome:
        return Outcome(Status="Instructions", Output=format.formatInstructions(self.config.InstructionPaths))

    async def _handlePermissions(self, parts: list[str]) -> Outcome:
        if len(parts) >= 3 and parts[1] == "mode":
            try:
                await self.ports.Permissions.SetPermissionMode(parts[2])
            except Exception as err:
                return Outcome(Err=f"Permissions failed: {err}")
        # Report the runtime's view of the policy instead of deriving it locally,
        # so the display cannot drift from actual behavior. The model is untouched,
        # so the status bar keeps showing it.
        mode = self.ports.Permissions.PermissionMode()
        return Outcome(
            Status="Permissions",
            Output=format.formatPermissions(self.config, mode, self.ports.Permissions.AutoApproveTools()),
            StatusBar=StatusBar(PermissionMode=mode),
        )

    async def _handleReset(self) -> Outcome:
        try:
            await self.ports.Sessions.Reset()
        except Exception as err:
            return Outcome(Err=f"Reset failed: {err}", RefreshSnapshot=True)
        return Outcome(Output=format.divider("New conversation"), RefreshSnapshot=True)

    async def _handleSessions(self) -> Outcome:
        try:
            summaries = await self.ports.Sessions.ListSessions()
        except Exception as err:
            return Outcome(Err=f"Sessions failed: {err}")
        return Outcome(Status="Sessions", Output=format.formatSessions(summaries))

    async def _handleResume(self, parts: list[str]) -> Outcome:
        if len(parts) < 2:
            return Outcome(Err="Usage: /resume <id>")
        try:
            await self.ports.Sessions.Resume(parts[1])
        except Exception as err:
            return Outcome(Err=f"Resume failed: {err}")
        return Outcome(
            Status="Resumed " + parts[1],
            Output=format.divider("Resumed session " + parts[1]),
            RefreshSnapshot=True,
        )

    async def _handleRename(self, text: str, parts: list[str]) -> Outcome:
        if len(parts) < 3:
            return Outcome(Err="Usage: /rename <id> <title>")
        title = text.removeprefix(parts[0] + " " + parts[1]).strip()
        try:
            await self.ports.Sessions.RenameSession(parts[1], title)
        except Exception as err:
            return Outcome(Err=f"Rename failed: {err}")
        return Outcome(Status="Renamed " + parts[1])

    async def _handleDelete(self, parts: list[str]) -> Outcome:
        if len(parts) < 2:
            return Outcome(Err="Usage: /delete-session <id>")
        try:
            await self.ports.Sessions.DeleteSession(parts[1])
        except Exception as err:
            return Outcome(Err=f"Delete failed: {err}")
        return Outcome(Status="Deleted " + parts[1])

    async def _handleUndo(self) -> Outcome:
        try:
            await self.ports.Sessions.Undo()
        except Exception as err:
            return Outcome(Err=f"Undo failed: {err}")
        return Outcome(
            Status="Restored last checkpoint",
            Output=format.divider("Restored checkpoint"),
            RefreshSnapshot=True,
        )

    async def _handleExport(self, exportFormat: str) -> Outcome:
        try:
            path = await self.ports.Sessions.Export(exportFormat)
        except Exception as err:
            return Outcome(Err=f"Export failed: {err}")
        return Outcome(Status="Exported " + path)

    async def _handleFork(self, title: str) -> Outcome:
        try:
            session_id = await self.ports.Sessions.Fork(title)
        except Exception as err:
            return Outcome(Err=f"Fork failed: {err}")
        return Outcome(Status="Forked session " + session_id, RefreshSnapshot=True)

    async def _handleMemory(self) -> Outcome:
        try:
            items = await self.ports.Memory.Memories()
        except Exception as err:
            return Outcome(Err=f"Memory failed: {err}")
        if not items:
            return Outcome(Status="No cross-session memory")
        return Outcome(Status="Memory", Output="Memory:\n- " + "\n- ".join(items))

    async def _handleRemember(self, value: str) -> Outcome:
        try:
            await self.ports.Memory.Remember(value)
        except Exception as err:
            return Outcome(Err=f"Remember failed: {err}")
        return Outcome(Status="Memory saved")

    async def _handleForget(self) -> Outcome:
        try:
            await self.ports.Memory.ForgetMemories()
        except Exception as err:
            return Outcome(Err=f"Forget failed: {err}")
        return Outcome(Status="Memory cleared")

    async def _handleGitDiff(self) -> Outcome:
        try:
            result = await self.ports.Workspace.GitDiff()
        except Exception as err:
            return Outcome(Err=f"Diff failed: {err}")
        if not result.strip():
            return Outcome(Status="No changes")
        return Outcome(Status="Patch preview", Output=result)

    async def _handleGitStatus(self) -> Outcome:
        try:
            result = await self.ports.Workspace.GitStatus()
        except Exception as err:
            return Outcome(Err=f"Branch status failed: {err}")
        return Outcome(Status="Branch status", Output=result)


#: The workflow prompts, verbatim from the Go ``Handle``.
_REVIEW_PROMPT = (
    "Review the current changes. Inspect the git diff, relevant code, and LSP diagnostics when configured, "
    "then report only actionable defects with file and line references. Do not modify files."
)
_FIX_CI_PROMPT = (
    "Inspect the repository CI configuration and current failures, reproduce them locally, implement the "
    "fixes, and verify the result."
)
_COMMIT_MESSAGE_PROMPT = (
    "Inspect the current git diff and suggest one concise conventional commit subject. Do not modify files or commit."
)


def New(config: Config, ports: Ports) -> Model:
    """Go's ``commands.New``: the catalogue follows the discovered extensions."""
    return Model(ports=ports, config=config, customCommands=tuple(ports.Extensions.CustomCommands()))
