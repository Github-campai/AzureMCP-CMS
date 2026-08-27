"""Turn an exception into something safe to hand back to a client.

pyodbc raises errors that quote the driver, the server hostname and sometimes a
fragment of the failing SQL. Returned verbatim, that text travels into the
customer's chat window and into the logs of whatever MCP client they use — a
detail leak, and a confusing one, because none of it is actionable for the
person reading it.

So the full text stays here: it goes to the application log and onto the audit
entry for the call in progress. The client gets a neutral sentence plus a
reference. That reference is the audit entry's own id, so an administrator can
pull the whole story with `GET /admin/audit/{reference}`.
"""

from __future__ import annotations

import logging
import uuid

from admcms_mcp import audit

logger = logging.getLogger(__name__)


def client_error(exc: BaseException, *, action: str = "Execution") -> str:
    """Log and audit `exc` in full; return a client-safe message with a reference.

    Args:
        exc: the exception that was caught.
        action: what was being attempted, used to open the message.

    Returns:
        A message free of driver, host and query detail.
    """
    entry = audit.current_entry()
    reference = entry.id if entry is not None else uuid.uuid4().hex
    detail = f"{type(exc).__name__}: {exc}"

    if entry is not None:
        # Set before `_audited` copies the client-facing text over: the audit
        # row must carry the real error, not the masked one.
        entry.error = detail

    logger.error("%s failed [reference=%s]: %s", action, reference, detail, exc_info=exc)

    return (
        f"{action} failed (reference {reference}). The technical detail is in the "
        f"audit log — quote this reference to an administrator."
    )
