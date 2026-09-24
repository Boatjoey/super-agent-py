"""The process entry point.

The startup order is part of the contract, because each step can only be done
once the one before it has succeeded:

1. parse the flags, which is the only step that may exit with status 2;
2. load ``.env`` from the process working directory, ignoring its failure;
3. resolve the configuration, which fails loudly on a bad permission mode, a bad
   sandbox mode, or a provider with no usable credential;
4. build the session, MCP servers, tools, and controllers;
5. run the terminal interface, and close the session on the way out.

Status 1 means "the app refused to start" or "the interface failed"; the app
itself never produces any other status.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import os
import sys

from dotenv import load_dotenv

from super_agent import app, cli, llm, tui


def main() -> None:
    """Run the terminal interface."""
    try:
        flags = cli.parse(sys.argv[1:])
    except cli.FlagError as error:
        if error.message:
            print(error.message, file=sys.stderr)
        if error.showUsage:
            sys.stderr.write(cli.usage())
        raise SystemExit(error.status) from None

    # Loaded from the process working directory, and never overriding a variable
    # that is already exported: a checked-in .env must not win over the
    # operator's environment. A missing file is not an error.
    with contextlib.suppress(Exception):
        load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)

    try:
        cfg = app.load_config(flags, os.environ.get)
    except Exception as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(asyncio.run(run(cfg)))


async def run(cfg: app.Config) -> int:
    """Build the session and run the interface, returning the exit status."""
    try:
        session, mcpController, agentController = await app.new_session_with_extensions(cfg)
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    try:
        profile = agentController.active_profile()
        workspace = cfg.workspace
        cwd = cfg.project.root if workspace is None else workspace.get_cwd()
        program = tui.TerminalApplication(
            tui.new(
                app.new_terminal_conversation(session, mcpController, agentController),
                tui.StartupInfo(
                    model_name=llm.model_display_name(profile.provider, llm.ProviderConfig(model=profile.model)),
                    version=importlib.metadata.version("super-agent-py"),
                    permission_mode=str(profile.permission_mode),
                    no_tools=cfg.no_tools,
                    cwd=cwd,
                    instruction_paths=tuple(cfg.instruction_sources),
                    session_id=str(session.metaID()),
                    sandbox=str(cfg.sandbox.mode),
                ),
            )
        )
        await program.run_terminal()
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        # The cleanup runs in a ``finally``, so it also happens on the failure
        # path. That is better hygiene than skipping it, and it changes nothing
        # the user saw.
        with contextlib.suppress(Exception):
            await session.close()
        with contextlib.suppress(Exception):
            await app.waitPendingClosers()
    return 0


if __name__ == "__main__":
    main()
