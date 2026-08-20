"""Per-process CPU and memory statistics for the PEP containers.

Used by the /resource endpoint so that concurrent-load experiments can attribute
CPU and memory consumption to each PEP and the PDP
without a host-side docker dependency: the orchestrator polls /resource before
and after each load level and reports the CPU-time delta and peak resident set
size.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def resource_stats() -> Dict[str, Any]:
    stats: Dict[str, Any] = {}
    try:
        import resource as _resource

        ru = _resource.getrusage(_resource.RUSAGE_SELF)
        stats["cpu_user_s"] = round(ru.ru_utime, 6)
        stats["cpu_sys_s"] = round(ru.ru_stime, 6)
        # ru_maxrss is in kilobytes on Linux.
        stats["maxrss_kb"] = int(ru.ru_maxrss)
    except Exception:
        stats["cpu_user_s"] = None
        stats["cpu_sys_s"] = None
        stats["maxrss_kb"] = None

    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as f:
            resident_pages = int(f.read().split()[1])
        page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
        stats["current_rss_kb"] = resident_pages * page_kb
    except Exception:
        stats["current_rss_kb"] = None

    return stats
