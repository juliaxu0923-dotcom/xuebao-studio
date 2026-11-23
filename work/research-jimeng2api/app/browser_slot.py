"""Share one Chromium slot across the gateway's browser workers."""

import threading
from collections.abc import Callable


class BrowserBusy(RuntimeError):
    """A browser is starting, running, or still shutting down."""


_slot = threading.Lock()


def start_browser_thread(target: Callable[[], None], *, name: str) -> threading.Thread:
    if not _slot.acquire(blocking=False):
        raise BrowserBusy("即梦浏览器正在使用，请关闭其他扫码或验证窗口后稍等片刻再试")

    def run() -> None:
        try:
            target()
        finally:
            _slot.release()

    try:
        thread = threading.Thread(target=run, name=name, daemon=True)
        thread.start()
    except Exception:
        _slot.release()
        raise
    return thread
