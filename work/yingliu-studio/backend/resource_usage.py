"""Read host and container resource pressure for the Studio job scheduler."""

import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


PROC = Path('/proc')
CGROUP = Path('/sys/fs/cgroup')


@dataclass(frozen=True)
class ResourceUsage:
    cpu_percent: float | None
    available_memory_bytes: int | None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='ascii', errors='replace').strip()
    except OSError:
        return ''


def counter(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdecimal() else None


def field(path: Path, key: str) -> int | None:
    for line in read_text(path).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == key:
            return counter(parts[1])
    return None


def host_cpu_ticks() -> tuple[int, int, int] | None:
    try:
        label, *fields = read_text(PROC / 'stat').split('\n', 1)[0].split()
        if label == 'cpu' and len(fields) >= 8:
            user, nice, system, idle, iowait, irq, softirq, steal = map(int, fields[:8])
            # Guest time is included in user and nice. Stolen time is unavailable.
            return user + nice + system + irq + softirq + steal, idle, iowait
    except ValueError:
        pass
    return None


def host_cpu_percent(previous, current) -> float | None:
    if previous is None or current is None:
        return None
    busy, idle, iowait = (new - old for old, new in zip(previous, current))
    # The kernel's iowait counter can decrease.
    total = busy + idle + max(0, iowait)
    return 100 * busy / total if busy >= 0 and idle >= 0 and total > 0 else None


def cgroup_directories() -> tuple[Path, ...]:
    for line in read_text(PROC / 'self/cgroup').splitlines():
        if not line.startswith('0::/'):
            continue
        parts = PurePosixPath(line[4:]).parts
        if '..' in parts:
            break
        leaf = CGROUP.joinpath(*parts)
        return (leaf, *(p for p in leaf.parents if p.is_relative_to(CGROUP)))
    return ()


def cgroup_quota(path: Path) -> float | None:
    fields = read_text(path / 'cpu.max').split()
    if len(fields) != 2:
        return None
    quota, period = map(counter, fields)
    return quota / period if quota and period else None


class ResourceMonitor:
    def __init__(self) -> None:
        self._cgroups = cgroup_directories() if sys.platform == 'linux' else ()
        self._previous_host_cpu: tuple[int, int, int] | None = None
        self._previous_group_cpu: dict[Path, tuple[int, float, float]] = {}

    def sample(self) -> ResourceUsage:
        if sys.platform != 'linux':
            return ResourceUsage(None, None)

        cpu_values = []
        memory_values = []
        current = host_cpu_ticks()
        host_cpu = host_cpu_percent(self._previous_host_cpu, current)
        self._previous_host_cpu = current
        if host_cpu is not None:
            cpu_values.append(host_cpu)

        host_memory = field(PROC / 'meminfo', 'MemAvailable:')
        if host_memory is not None:
            memory_values.append(host_memory * 1024)

        now = time.monotonic()
        for path in self._cgroups:
            limit = counter(read_text(path / 'memory.max'))
            if limit is not None:
                used = counter(read_text(path / 'memory.current'))
                if used is not None:
                    memory_values.append(max(0, limit - used))

            quota = cgroup_quota(path)
            used = field(path / 'cpu.stat', 'usage_usec') if quota else None
            previous = self._previous_group_cpu.pop(path, None)
            if used is None or quota is None:
                continue
            self._previous_group_cpu[path] = (used, quota, now)
            if previous is None:
                continue
            last_used, last_quota, last_time = previous
            if quota == last_quota and used >= last_used and now > last_time:
                ratio = (used - last_used) / (1_000_000 * (now - last_time) * quota)
                cpu_values.append(min(100.0, 100 * ratio))

        return ResourceUsage(max(cpu_values, default=None), min(memory_values, default=None))
