"""Lift the open-file soft limit to the hard limit at startup (upstream #2883).

Docker and systemd start the app with a soft RLIMIT_NOFILE of 1024 and a far
higher hard limit; a process may raise its own soft limit up to the hard one
without privileges. At 1024, running out of descriptors made every new SQLite
connection fail with "disk I/O error" for hours and ended in "database disk
image is malformed".
"""

import logging

logger = logging.getLogger(__name__)

# Tried in order when the hard limit is unlimited: macOS reports RLIM_INFINITY
# but refuses a soft limit above kern.maxfilesperproc (usually 10240).
_UNLIMITED_HARD_TARGETS = (65536, 10240)


def raise_open_file_limit() -> int | None:
    """Raise the soft limit as far as the hard limit allows; return the soft limit in force.

    Never lowers it and never raises: a limit that cannot be changed is logged
    and startup carries on. None where ``resource`` does not exist (Windows).
    """
    try:
        import resource
    except ImportError:
        return None

    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError) as e:
        logger.warning("Could not read the open-file limit: %s", e)
        return None

    infinity = resource.RLIM_INFINITY
    targets = _UNLIMITED_HARD_TARGETS if hard == infinity else (hard,)
    error: Exception | None = None
    for target in targets:
        if soft == infinity or target <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
        except (OSError, ValueError) as e:
            error = e
            continue
        logger.info("Raised the open-file limit from %s to %s (hard limit %s)", soft, target, hard)
        return target

    if error is not None:
        logger.warning("Could not raise the open-file limit above %s (hard limit %s): %s", soft, hard, error)
    else:
        logger.info("Open-file limit: %s (hard limit %s)", soft, hard)
    return soft
