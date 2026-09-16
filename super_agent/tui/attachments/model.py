"""Files queued for the next turn.

Ported from the Go ``tui/attachments/model.go``. The feature owns the queue and
the port it asks for a file; the root owns the status line the outcome feeds.

Go's ``Attach`` and ``PendingAttachments`` return a ``tea.Cmd`` that runs off the
update loop. Python keeps that shape: both return an awaitable command whose
result message comes back through the runtime, because the port does I/O.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine
from typing import Protocol

from rich.style import Style
from rich.text import Text

__all__ = ["AttachCommand", "Attached", "Item", "Loaded", "Model", "New", "Outcome", "Port"]

#: Go builds this inline with lipgloss; a feature may not reach for the root's
#: styles (R6), so the summary keeps its own.
_ACCENT = Style(color="color(6)", italic=True)

type AttachCommand = Callable[[], Coroutine[object, object, "Loaded | Attached | None"]]
"""A command this feature hands the runtime: an awaitable producing a message."""


@dataclasses.dataclass(frozen=True, slots=True)
class Item:
    """One file queued for the next turn."""

    Name: str = ""
    MIME: str = ""


class Port(Protocol):
    """The attachments capability, implemented at the composition boundary."""

    async def Attach(self, path: str) -> Item: ...

    async def PendingAttachments(self) -> tuple[Item, ...]: ...


@dataclasses.dataclass(frozen=True, slots=True)
class Loaded:
    """The pending attachments the session already holds."""

    Items: tuple[Item, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Attached:
    """The outcome of asking the session for one more attachment."""

    Item: Item = dataclasses.field(default_factory=Item)
    Err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """What an attachment message means for the root."""

    Attached: Item | None = None
    Err: BaseException | None = None


@dataclasses.dataclass(slots=True)
class Model:
    """The attachments the next turn will carry."""

    port: Port
    items: list[Item] = dataclasses.field(default_factory=list[Item])

    def Init(self) -> AttachCommand:
        """Ask the session for the attachments it already has."""

        async def load() -> Loaded:
            return Loaded(Items=tuple(await self.port.PendingAttachments()))

        return load

    def Attach(self, path: str) -> AttachCommand:
        """Queue one file through the port."""

        async def attach() -> Attached:
            try:
                item = await self.port.Attach(path)
            except Exception as err:
                return Attached(Err=err)
            return Attached(Item=item)

        return attach

    def Update(self, message: Loaded | Attached) -> tuple[Model, Outcome | None]:
        """Apply a message from one of this feature's own commands."""
        if isinstance(message, Loaded):
            self.Set(message.Items)
            return self, None
        if message.Err is not None:
            return self, Outcome(Err=message.Err)
        self.items.append(message.Item)
        return self, Outcome(Attached=message.Item)

    def Set(self, items: tuple[Item, ...]) -> None:
        """Replace the queue with a copy of ``items``."""
        self.items = list(items)

    def Items(self) -> tuple[Item, ...]:
        """The queued attachments, as a copy."""
        return tuple(self.items)

    def View(self) -> Text:
        """The one-line attachment summary, or an empty :class:`Text`."""
        if not self.items:
            return Text()
        names = ", ".join(item.Name for item in self.items)
        return Text(f" Attachments: {names}", style=_ACCENT)


def New(port: Port) -> Model:
    """Go's ``attachments.New``."""
    return Model(port=port)
