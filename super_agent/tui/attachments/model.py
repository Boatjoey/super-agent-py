"""Files queued for the next turn.

The feature owns the queue and the port it asks for a file; the root owns the
status line the outcome feeds.

``Attach`` and ``PendingAttachments`` return a command that runs off the update
loop: both return an awaitable command whose result message comes back through
the runtime, because the port does I/O.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Coroutine
from typing import Protocol

from rich.style import Style
from rich.text import Text

__all__ = ["AttachCommand", "Attached", "Item", "Loaded", "Model", "Outcome", "Port", "new"]

#: A feature may not reach for the root's styles (R6), so the summary keeps its
#: own.
_ACCENT = Style(color="color(6)", italic=True)

type AttachCommand = Callable[[], Coroutine[object, object, "Loaded | Attached | None"]]
"""A command this feature hands the runtime: an awaitable producing a message."""


@dataclasses.dataclass(frozen=True, slots=True)
class Item:
    """One file queued for the next turn."""

    name: str = ""
    mime: str = ""


class Port(Protocol):
    """The attachments capability, implemented at the composition boundary."""

    async def attach(self, path: str) -> Item: ...

    async def pending_attachments(self) -> tuple[Item, ...]: ...


@dataclasses.dataclass(frozen=True, slots=True)
class Loaded:
    """The pending attachments the session already holds."""

    items: tuple[Item, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Attached:
    """The outcome of asking the session for one more attachment."""

    item: Item = dataclasses.field(default_factory=Item)
    err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """What an attachment message means for the root."""

    attached: Item | None = None
    err: BaseException | None = None


@dataclasses.dataclass(slots=True)
class Model:
    """The attachments the next turn will carry."""

    port: Port
    items: list[Item] = dataclasses.field(default_factory=list[Item])

    def init(self) -> AttachCommand:
        """Ask the session for the attachments it already has."""

        async def load() -> Loaded:
            return Loaded(items=tuple(await self.port.pending_attachments()))

        return load

    def attach(self, path: str) -> AttachCommand:
        """Queue one file through the port."""

        async def attach() -> Attached:
            try:
                item = await self.port.attach(path)
            except Exception as err:
                return Attached(err=err)
            return Attached(item=item)

        return attach

    def update(self, message: Loaded | Attached) -> tuple[Model, Outcome | None]:
        """Apply a message from one of this feature's own commands."""
        if isinstance(message, Loaded):
            self.set(message.items)
            return self, None
        if message.err is not None:
            return self, Outcome(err=message.err)
        self.items.append(message.item)
        return self, Outcome(attached=message.item)

    def set(self, items: tuple[Item, ...]) -> None:
        """Replace the queue with a copy of ``items``."""
        self.items = list(items)

    def queued_items(self) -> tuple[Item, ...]:
        """The queued attachments, as a copy."""
        return tuple(self.items)

    def view(self) -> Text:
        """The one-line attachment summary, or an empty :class:`Text`."""
        if not self.items:
            return Text()
        names = ", ".join(item.name for item in self.items)
        return Text(f" Attachments: {names}", style=_ACCENT)


def new(port: Port) -> Model:
    """Build the model around its port."""
    return Model(port=port)
