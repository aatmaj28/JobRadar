"""Job board fetchers. Each module exposes fetch(http, board, is_seen) and describe(http, job)."""
from __future__ import annotations


class BoardGone(Exception):
    """The board does not exist (404). After a few of these the board is switched off."""


class BoardDown(Exception):
    """Temporary trouble (maintenance, timeouts). Try again next run."""


def _modules():
    from . import ashby, generic, greenhouse, lever, workday

    return {
        "greenhouse": greenhouse,
        "lever": lever,
        "ashby": ashby,
        "workday": workday,
        "generic": generic,
    }


def fetch_board(http, board, is_seen=None):
    return _modules()[board.source].fetch(http, board, is_seen)


def describe(http, job) -> None:
    """Fill job.description (and for Workday, location details) in place. Never raises."""
    if job.description and job.source != "workday":
        return
    mods = _modules()
    module = mods.get(job.source, mods["generic"])
    try:
        module.describe(http, job)
    except Exception as exc:  # a missing description should never sink a run
        job.extra["describe_error"] = str(exc)[:200]
