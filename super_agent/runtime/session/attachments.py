"""Staging files for the next turn.

The session reaches attachments through a narrow optional port: a workspace that
can read a file may also provide :class:`AttachmentReader`, and one that cannot
simply reports that attachments are unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from super_agent.runtime.protocol.types import Attachment

if TYPE_CHECKING:
    from super_agent.runtime.session.repository import Workspace


@runtime_checkable
class AttachmentReader(Protocol):
    """A workspace that can turn a path into an attachment."""

    def ReadAttachment(self, path: str) -> Attachment: ...


class AttachmentsMixin:
    """Staging and listing the attachments queued for the next turn."""

    if TYPE_CHECKING:
        workspace: Workspace | None
        _attachments: list[Attachment]

    def Attach(self, path: str) -> Attachment:
        """Stage the file at ``path`` for the next turn."""
        if not isinstance(self.workspace, AttachmentReader):
            raise RuntimeError("attachments are unavailable")
        attachment = self.workspace.ReadAttachment(path)
        self._attachments.append(attachment)
        return attachment

    def PendingAttachments(self) -> list[Attachment]:
        """A copy of what is staged; staging is consumed once per turn."""
        return list(self._attachments)
