"""Session wall-clock state layered over the monotonic kernel timer.

PythonOS does not yet have an RTC driver.  A learner may set an HH:MM:SS
value for the current boot; time then advances from the monotonic scheduler
clock.  Clearing it returns displays to honest time-since-boot mode.
"""

_base_clock_seconds: int | None = None
_base_uptime_seconds = 0


def _uptime_seconds() -> int:
    try:
        from kernel.scheduler import scheduler
        return max(0, int(scheduler.uptime_ms) // 1000)
    except Exception:
        return 0


def is_set() -> bool:
    return _base_clock_seconds is not None


def set_hms(hour: int, minute: int, second: int = 0) -> None:
    """Set the session clock, validating a 24-hour time."""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 00..23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be 00..59")
    if not 0 <= second <= 59:
        raise ValueError("second must be 00..59")
    global _base_clock_seconds, _base_uptime_seconds
    _base_clock_seconds = hour * 3600 + minute * 60 + second
    _base_uptime_seconds = _uptime_seconds()


def clear() -> None:
    """Return the desktop clock to time-since-boot display."""
    global _base_clock_seconds, _base_uptime_seconds
    _base_clock_seconds = None
    _base_uptime_seconds = 0


def seconds() -> int:
    uptime = _uptime_seconds()
    if _base_clock_seconds is None:
        return uptime
    return (_base_clock_seconds + uptime - _base_uptime_seconds) % 86400


def format_hms() -> str:
    value = seconds()
    hour = value // 3600
    minute = (value // 60) % 60
    second = value % 60
    return f"{hour:02d}:{minute:02d}:{second:02d}"
