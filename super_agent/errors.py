"""Error plumbing shared by every layer.

Wrapping, identity checks, and joining use ``__cause__`` chaining,
``isinstance``, and ``BaseExceptionGroup``. Two deliberate choices shape the
error types here:

* :class:`Cancelled` exists because ``asyncio.CancelledError`` derives from
  ``BaseException``. An adapter converts it at its boundary so that ordinary
  ``except Exception`` blocks keep working; the engine also defends against a
  bare ``CancelledError`` reaching it.
* :class:`JoinedError` is a plain ``Exception`` carrying its members rather than
  an ``ExceptionGroup``, because ``ExceptionGroup`` changes ``except`` semantics
  and formats its message differently. Callers here want one error that stands
  for several causes and is still an ``Exception``.
"""

from __future__ import annotations

from collections.abc import Iterator


class Cancelled(Exception):
    """The work was cancelled.

    Adapters translate ``asyncio.CancelledError`` into this at their boundary; the
    engine treats both as cancellation so a stray ``CancelledError`` cannot be
    mistaken for a failure that turns into a tool result.
    """


class JoinedError(Exception):
    """Several errors reported as one.

    ``str`` joins the members with newlines, one per member.
    """

    def __init__(self, *members: BaseException | None) -> None:
        self.members: tuple[BaseException, ...] = tuple(member for member in members if member is not None)
        super().__init__("\n".join(str(member) for member in self.members))


def _walk(error: BaseException | None) -> Iterator[BaseException]:
    """Yield ``error`` and everything it wraps, without revisiting a node.

    Follows ``__cause__`` (written by ``raise ... from``) and ``__context__``
    (written implicitly when an exception is raised while handling another), and
    descends into :class:`JoinedError` members.
    """
    seen: set[int] = set()
    stack: list[BaseException | None] = [error]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, JoinedError):
            stack.extend(current.members)
        stack.append(current.__cause__)
        stack.append(current.__context__)


def errors_is(error: BaseException | None, target: type[BaseException] | BaseException) -> bool:
    """Report whether ``error`` is, or wraps, ``target``.

    A class target matches by ``isinstance``. An instance target matches by
    identity, so a module-level sentinel value can be compared directly.
    """
    for current in _walk(error):
        if isinstance(target, type):
            if isinstance(current, target):
                return True
        elif current is target:
            return True
    return False


def errors_as[E: BaseException](error: BaseException | None, cls: type[E]) -> E | None:
    """Return the first error in the chain that is an instance of ``cls``."""
    for current in _walk(error):
        if isinstance(current, cls):
            return current
    return None
