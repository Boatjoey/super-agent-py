"""The slash-command catalogue, its input semantics, and its background work.

The feature owns which commands exist, what each one means, and the operations it
starts; the root owns the effects a command asks for, including every
cross-feature one.

``Handle`` is a coroutine rather than a blocking call. The ports it reaches, such
as ``Workspace.Diagnostics``, do I/O, so the caller awaits :meth:`Model.Handle`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine

from super_agent.tui.commands import catalog, format
from super_agent.tui.commands.outcome import CompactDone, MCPDone, Outcome, StatusBar
from super_agent.tui.commands.ports import Attachment, Ports

__all__ = ["Command", "Config", "Input", "Model", "new"]

type Command = Callable[[], Coroutine[object, object, "CompactDone | MCPDone"]]
"""A background operation this feature hands the runtime."""


@dataclasses.dataclass(frozen=True, slots=True)
class Config:
    """The session settings commands display but do not own."""

    cwd: str = ""
    instruction_paths: tuple[str, ...] = ()
    no_tools: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class Input:
    """What the root knows about a submission and the command cannot own."""

    text: str = ""
    attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(slots=True)
class Model:
    """The catalogue, its input semantics, and the background work it started."""

    ports: Ports
    config: Config
    customCommands: tuple[str, ...] = ()
    compacting: bool = False
    managingMCP: bool = False

    def is_compacting(self) -> bool:
        """Whether ``/compact`` is still running."""
        return self.compacting

    def managing_mcp(self) -> bool:
        """Whether an MCP lifecycle change is still running."""
        return self.managingMCP

    def palette(self) -> tuple[catalog.Command, ...]:
        """The palette the composer offers."""
        return catalog.palette(self)

    def update(self, message: CompactDone | MCPDone) -> tuple[Model, Outcome | None]:
        """Apply the result of a background command.

        A ``None`` outcome means the message did not belong to this feature.
        """
        if isinstance(message, CompactDone):
            self.compacting = False
            if message.err is not None:
                return self, Outcome(err=f"Compact failed: {message.err}")
            return self, Outcome(
                status="Compacted conversation",
                output=format.divider("Compacted conversation"),
                refresh_snapshot=True,
            )
        # The union is closed, so anything that is not a compaction is an MCP run.
        self.managingMCP = False
        if message.err is not None:
            return self, Outcome(err=f"MCP failed: {message.err}")
        return self, Outcome(status=message.status)

    async def handle(self, input: Input) -> tuple[Model, Outcome | None, Command | None]:
        """Run the slash command in ``input.Text``. Callers check ``IsCommand`` first."""
        parts = input.text.split()
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
                    return self, Outcome(err="Usage: /mode <plan|build>"), None
                return self, await self._handleAgent(["/agent", parts[1]]), None
            case "/fork":
                return self, await self._handleFork(input.text.removeprefix(command).strip()), None
            case "/memory":
                return self, await self._handleMemory(), None
            case "/remember":
                return self, await self._handleRemember(input.text.removeprefix(command).strip()), None
            case "/forget":
                return self, await self._handleForget(), None
            case "/diff":
                return self, await self._handleGitDiff(), None
            case "/branch":
                return self, await self._handleGitStatus(), None
            case "/review":
                return self, Outcome(prompt=_REVIEW_PROMPT), None
            case "/fix-ci":
                return self, Outcome(prompt=_FIX_CI_PROMPT), None
            case "/commit-message":
                return self, Outcome(prompt=_COMMIT_MESSAGE_PROMPT), None
            case "/export":
                if len(parts) != 2:
                    return self, Outcome(err="Usage: /export <markdown|json>"), None
                return self, await self._handleExport(parts[1]), None
            case "/share":
                return self, await self._handleExport("html"), None
            case "/attach":
                if len(parts) != 2:
                    return self, Outcome(err="Usage: /attach <path>"), None
                return self, Outcome(status="Attaching…", attach_path=parts[1]), None
            case "/attachments":
                if not input.attachments:
                    return self, Outcome(status="No pending attachments"), None
                names = [f"{item.name} ({item.mime})" for item in input.attachments]
                return self, Outcome(status="Attachments", output="Attachments:\n- " + "\n- ".join(names)), None
            case "/commands":
                return self, self._handleNamedItems("Custom commands"), None
            case "/skills":
                return self, self._handleNamedItems("Skills"), None
            case "/plugins":
                return self, self._handleNamedItems("Plugins"), None
            case "/diagnostics":
                if len(parts) != 2:
                    return self, Outcome(err="Usage: /diagnostics <path>"), None
                try:
                    result = await self.ports.workspace.diagnostics(parts[1])
                except Exception as err:
                    return self, Outcome(err=f"Diagnostics failed: {err}"), None
                return self, Outcome(status="Diagnostics", output=result), None
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
                return self, await self._handleRename(input.text, parts), None
            case "/delete-session":
                return self, await self._handleDelete(parts), None
            case "/compact":
                return self._handleCompact(input.text, command)
            case "/undo":
                return self, await self._handleUndo(), None
            case "/quit" | "/exit":
                return self, Outcome(quit=True), None
            case "/help":
                return self, Outcome(show_help=True), None
            case _:
                arguments = input.text.removeprefix(command).strip()
                try:
                    expanded = await self.ports.extensions.expand_custom_command(command.removeprefix("/"), arguments)
                except Exception:
                    return self, Outcome(err=f"Unknown command: {command}"), None
                return self, Outcome(prompt=expanded), None

    def _handleCompact(self, text: str, command: str) -> tuple[Model, Outcome | None, Command | None]:
        """Pass the optional summary; the keep-newest policy stays in the runtime."""
        summary = text.removeprefix(command).strip()
        self.compacting = True

        async def run() -> CompactDone:
            try:
                await self.ports.sessions.compact(summary)
            except Exception as err:
                return CompactDone(err=err)
            return CompactDone()

        return self, Outcome(status="Compacting conversation…"), run

    def _handleMCP(self, parts: list[str]) -> tuple[Model, Outcome | None, Command | None]:
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            return (
                self,
                Outcome(status="MCP servers", output=format.formatMCPServers(self.ports.mcp.list_mcp_servers())),
                None,
            )
        operation = parts[1]
        match operation:
            case "add":
                if len(parts) < 4:
                    return self, Outcome(err="Usage: /mcp add <name> <command> [args...]"), None
                name, command, args = parts[2], parts[3], list(parts[4:])
                status = "Added MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.mcp.add_mcp_server(name, command, args))
            case "remove":
                if len(parts) != 3:
                    return self, Outcome(err="Usage: /mcp remove <name>"), None
                name = parts[2]
                status = "Removed MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.mcp.remove_mcp_server(name))
            case "restart":
                if len(parts) != 3:
                    return self, Outcome(err="Usage: /mcp restart <name>"), None
                name = parts[2]
                status = "Restarted MCP server " + name
                run = self._mcpCommand(status, lambda: self.ports.mcp.restart_mcp_server(name))
            case _:
                return self, Outcome(err="Usage: /mcp <list|add|remove|restart>"), None
        self.managingMCP = True
        return self, Outcome(status="Updating MCP servers…"), run

    def _mcpCommand(self, status: str, operation: Callable[[], Coroutine[object, object, None]]) -> Command:
        """Wrap one MCP lifecycle call as the command that reports its outcome."""

        async def change() -> MCPDone:
            try:
                await operation()
            except Exception as err:
                return MCPDone(status=status, err=err)
            return MCPDone(status=status)

        return change

    async def _handleAgent(self, parts: list[str]) -> Outcome:
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "list"):
            current = self.ports.agents.current_agent().name
            rows = [
                ("* " if profile.name == current else "  ")
                + f"{profile.name} ({profile.provider}/{profile.model}, {profile.permission_mode})"
                for profile in self.ports.agents.list_agents()
            ]
            return Outcome(status="Agents", output="\n".join(rows))
        if len(parts) != 2:
            return Outcome(err="Usage: /agent <list|name>")
        try:
            await self.ports.agents.use_agent(parts[1])
        except Exception as err:
            return Outcome(err=f"Agent failed: {err}")
        profile = self.ports.agents.current_agent()
        return Outcome(
            status="Using agent " + profile.name,
            refresh_snapshot=True,
            status_bar=StatusBar(model_name=profile.model, permission_mode=profile.permission_mode),
        )

    def _handleNamedItems(self, label: str) -> Outcome:
        match label:
            case "Custom commands":
                items = self.ports.extensions.custom_commands()
            case "Skills":
                items = self.ports.extensions.skills()
            case _:
                items = self.ports.extensions.plugins()
        return Outcome(status=label, output=format.formatNamedItems(label, items))

    def _handleInstructions(self) -> Outcome:
        return Outcome(status="Instructions", output=format.formatInstructions(self.config.instruction_paths))

    async def _handlePermissions(self, parts: list[str]) -> Outcome:
        if len(parts) >= 3 and parts[1] == "mode":
            try:
                await self.ports.permissions.set_permission_mode(parts[2])
            except Exception as err:
                return Outcome(err=f"Permissions failed: {err}")
        # Report the runtime's view of the policy instead of deriving it locally,
        # so the display cannot drift from actual behavior. The model is untouched,
        # so the status bar keeps showing it.
        mode = self.ports.permissions.permission_mode()
        return Outcome(
            status="Permissions",
            output=format.formatPermissions(self.config, mode, self.ports.permissions.auto_approve_tools()),
            status_bar=StatusBar(permission_mode=mode),
        )

    async def _handleReset(self) -> Outcome:
        try:
            await self.ports.sessions.reset()
        except Exception as err:
            return Outcome(err=f"Reset failed: {err}", refresh_snapshot=True)
        return Outcome(output=format.divider("New conversation"), refresh_snapshot=True)

    async def _handleSessions(self) -> Outcome:
        try:
            summaries = await self.ports.sessions.list_sessions()
        except Exception as err:
            return Outcome(err=f"Sessions failed: {err}")
        return Outcome(status="Sessions", output=format.formatSessions(summaries))

    async def _handleResume(self, parts: list[str]) -> Outcome:
        if len(parts) < 2:
            return Outcome(err="Usage: /resume <id>")
        try:
            await self.ports.sessions.resume(parts[1])
        except Exception as err:
            return Outcome(err=f"Resume failed: {err}")
        return Outcome(
            status="Resumed " + parts[1],
            output=format.divider("Resumed session " + parts[1]),
            refresh_snapshot=True,
        )

    async def _handleRename(self, text: str, parts: list[str]) -> Outcome:
        if len(parts) < 3:
            return Outcome(err="Usage: /rename <id> <title>")
        title = text.removeprefix(parts[0] + " " + parts[1]).strip()
        try:
            await self.ports.sessions.rename_session(parts[1], title)
        except Exception as err:
            return Outcome(err=f"Rename failed: {err}")
        return Outcome(status="Renamed " + parts[1])

    async def _handleDelete(self, parts: list[str]) -> Outcome:
        if len(parts) < 2:
            return Outcome(err="Usage: /delete-session <id>")
        try:
            await self.ports.sessions.delete_session(parts[1])
        except Exception as err:
            return Outcome(err=f"Delete failed: {err}")
        return Outcome(status="Deleted " + parts[1])

    async def _handleUndo(self) -> Outcome:
        try:
            await self.ports.sessions.undo()
        except Exception as err:
            return Outcome(err=f"Undo failed: {err}")
        return Outcome(
            status="Restored last checkpoint",
            output=format.divider("Restored checkpoint"),
            refresh_snapshot=True,
        )

    async def _handleExport(self, exportFormat: str) -> Outcome:
        try:
            path = await self.ports.sessions.export(exportFormat)
        except Exception as err:
            return Outcome(err=f"Export failed: {err}")
        return Outcome(status="Exported " + path)

    async def _handleFork(self, title: str) -> Outcome:
        try:
            session_id = await self.ports.sessions.fork(title)
        except Exception as err:
            return Outcome(err=f"Fork failed: {err}")
        return Outcome(status="Forked session " + session_id, refresh_snapshot=True)

    async def _handleMemory(self) -> Outcome:
        try:
            items = await self.ports.memory.memories()
        except Exception as err:
            return Outcome(err=f"Memory failed: {err}")
        if not items:
            return Outcome(status="No cross-session memory")
        return Outcome(status="Memory", output="Memory:\n- " + "\n- ".join(items))

    async def _handleRemember(self, value: str) -> Outcome:
        try:
            await self.ports.memory.remember(value)
        except Exception as err:
            return Outcome(err=f"Remember failed: {err}")
        return Outcome(status="Memory saved")

    async def _handleForget(self) -> Outcome:
        try:
            await self.ports.memory.forget_memories()
        except Exception as err:
            return Outcome(err=f"Forget failed: {err}")
        return Outcome(status="Memory cleared")

    async def _handleGitDiff(self) -> Outcome:
        try:
            result = await self.ports.workspace.git_diff()
        except Exception as err:
            return Outcome(err=f"Diff failed: {err}")
        if not result.strip():
            return Outcome(status="No changes")
        return Outcome(status="Patch preview", output=result)

    async def _handleGitStatus(self) -> Outcome:
        try:
            result = await self.ports.workspace.git_status()
        except Exception as err:
            return Outcome(err=f"Branch status failed: {err}")
        return Outcome(status="Branch status", output=result)


#: The workflow prompts, verbatim from ``Handle``.
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


def new(config: Config, ports: Ports) -> Model:
    """The constructor: the catalogue follows the discovered extensions."""
    return Model(ports=ports, config=config, customCommands=tuple(ports.extensions.custom_commands()))
