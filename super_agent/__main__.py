"""The process entry point.

Ports ``main.go``. The startup order is part of the contract, because each step
can only be done once the one before it has succeeded:

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
import os
import sys

from dotenv import load_dotenv
from rich.console import Console

from super_agent import app, cli, llm, tui


def main() -> None:
    """Run the terminal interface. Mirrors ``main`` in ``main.go``."""
    try:
        flags = cli.Parse(sys.argv[1:])
    except cli.FlagError as error:
        if error.message:
            print(error.message, file=sys.stderr)
        if error.showUsage:
            sys.stderr.write(cli.Usage())
        raise SystemExit(error.status) from None

    # Loaded from the process working directory, and never overriding a variable
    # that is already exported: a checked-in .env must not win over the
    # operator's environment. A missing file is not an error.
    with contextlib.suppress(Exception):
        load_dotenv(os.path.join(os.getcwd(), ".env"), override=False)

    try:
        cfg = app.LoadConfig(flags, os.environ.get)
    except Exception as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(asyncio.run(run(cfg)))


async def run(cfg: app.Config) -> int:
    """Build the session and run the interface, returning the exit status."""
    try:
        session, mcpController, agentController = await app.NewSessionWithExtensions(cfg)
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    try:
        profile = agentController.Current()
        workspace = cfg.Workspace
        cwd = cfg.Project.Root if workspace is None else workspace.GetCWD()
        program = tui.Program(
            model=tui.New(
                app.NewTUIConversation(session, mcpController, agentController),
                tui.StartupInfo(
                    ModelName=llm.ModelDisplayName(profile.Provider, llm.ProviderConfig(Model=profile.Model)),
                    PermissionMode=str(profile.PermissionMode),
                    NoTools=cfg.NoTools,
                    CWD=cwd,
                    InstructionPaths=tuple(cfg.InstructionSources),
                ),
            ),
            update=tui.Update,
            view=tui.View,
            console=Console(),
        )
        await program.run()
    except Exception as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        # Go defers this, and skips it when the interface fails because os.Exit
        # does not run deferred calls. Closing here as well is better hygiene and
        # changes nothing the user saw.
        with contextlib.suppress(Exception):
            await session.Close()
        with contextlib.suppress(Exception):
            await app.waitPendingClosers()
    return 0


if __name__ == "__main__":
    main()
