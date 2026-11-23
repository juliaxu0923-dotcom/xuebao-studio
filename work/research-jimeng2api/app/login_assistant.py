#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫码登录助手：**无头浏览器 + 抖音 OAuth 二维码**，把登录过程搬进面板。

为什么需要它：即梦的 `sessionid` 是 **HttpOnly** cookie（`document.cookie` 里读不到，
浏览器扩展按策略也不暴露 cookie 值），所以"从已登录的浏览器里把 sessionid 抠出来"
在工具层面走不通。服务器上更没有图形界面去开 DevTools。

这里换一条**正路**：服务端自己起一个浏览器，走站点真实的登录流程
（登录 → 勾选协议 → 抖音登录 → OAuth 二维码），用户在面板里用抖音 App 扫一下；
扫码成功后 `BrowserContext.cookies()` 能拿到 HttpOnly 的 sessionid，直接入池。

安全边界：
* 浏览器只访问 `jimeng.jianying.com` / `open.douyin.com`；
* sessionid 只写进本地 DB，**不打印、不返回给前端**（面板只看到尾号）；
* 一次只允许一个登录会话（每个浏览器约 200MB，且没有并发的必要）。
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from . import db, repo
from .browser_slot import start_browser_thread
from .config import settings

HOME = "https://jimeng.jianying.com/ai-tool/home"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
TIMEOUT = 300         # 二维码有效期按 5 分钟算
QR_REFRESH = 8.0      # 每 8 秒重截一次二维码（二维码会轮换）

#: 登录方式 → (弹窗里的按钮 aria-label, 提示里用的 App 名)
PROVIDERS: dict[str, tuple[str, str]] = {
    "douyin": ("抖音登录", "抖音"),
    "feishu": ("飞书登录", "飞书"),
}

_LOCK = threading.Lock()
_SESSIONS: dict[str, "LoginSession"] = {}


class LoginUnavailable(RuntimeError):
    """Playwright 未安装/浏览器不可用 —— 面板据此显示安装指引。"""


class LoginCancelled(Exception):
    pass


def _check_cancelled(s):
    if s.stop.is_set():
        raise LoginCancelled()


def _proxy_options(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https", "socks5") or not parsed.hostname:
        raise LoginUnavailable("登录代理地址无效，请检查服务器代理配置")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    server = f"{parsed.scheme}://{host}"
    if parsed.port:
        server += f":{parsed.port}"
    result = {"server": server}
    if parsed.username is not None:
        result["username"] = unquote(parsed.username)
        result["password"] = unquote(parsed.password or "")
    return result


@dataclass
class LoginSession:
    id: str
    remark: str = ""
    provider: str = "douyin"
    state: str = "starting"   # starting|waiting|success|error|expired|cancelled
    message: str = "正在打开登录页…"
    qr: bytes = b""
    qr_version: int = 0
    account_id: int | None = None
    credits: int | None = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    stop: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict:
        return {
            "session_id": self.id, "state": self.state,
            "provider": self.provider,
            "message": self.message, "qr_version": self.qr_version,
            "account_id": self.account_id, "credits": self.credits,
            "error": self.error, "age": int(time.time() - self.created_at),
            "has_qr": bool(self.qr),
        }


def _launch(p):
    """优先用本机 Chrome（免下载）；服务器上没有 Chrome 就退回 Playwright 自带 chromium。"""
    errors: list[str] = []
    for channel in ("chrome", None):
        try:
            options = {
                "channel": channel, "headless": True,
                "args": ["--no-sandbox", "--disable-dev-shm-usage"],
            }
            if settings.http_proxy:
                options["proxy"] = _proxy_options(settings.http_proxy)
            return p.chromium.launch(**options)
        except Exception as e:  # noqa: BLE001 - 逐个候选尝试
            errors.append(f"{channel}: {type(e).__name__}")
    raise LoginUnavailable(
        "无法启动浏览器（" + "; ".join(errors) + "）。"
        "请安装：uv pip install -r requirements-login.txt && "
        "playwright install chromium")


def _qr_png(popup) -> bytes:
    """只截已加载的二维码候选，不把空白弹窗当成二维码。

    抖音的码是 `<img>`，飞书的码是 `<canvas>`（2026-11-19 实测，184×184）——
    只找 img 会把飞书那张整页截图塞进面板，二维码小到扫不出来。
    """
    best = None
    best_area = 0
    frames = [popup] + [f for f in popup.frames if f != popup.main_frame]
    for frame in frames:
        try:
            elems = frame.locator("img, canvas")
            for i in range(min(elems.count(), 20)):
                element = elems.nth(i)
                box = element.bounding_box(timeout=2000)
                if not box:
                    continue
                w, h = box["width"], box["height"]
                if 140 <= w <= 480 and abs(w - h) <= 24 and w * h > best_area:
                    loaded = element.evaluate("""e => e.tagName === 'IMG'
                        ? e.complete && e.naturalWidth > 0 && e.naturalHeight > 0
                        : e.width > 0 && e.height > 0""")
                    if loaded:
                        best, best_area = element, w * h
        except Exception:  # noqa: BLE001
            continue
    if best is not None:
        try:
            return best.screenshot(type="png", timeout=3000)
        except Exception:  # noqa: BLE001
            pass
    return b""


def _open_login_page(page, s):
    from playwright.sync_api import TimeoutError as BrowserTimeout, Error as BrowserError

    button = page.get_by_role("button", name="登录", exact=True).first
    for attempt in range(2):
        _check_cancelled(s)
        s.message = "正在连接即梦登录页…" if not attempt else "即梦页面加载较慢，正在重试…"
        try:
            page.goto(HOME, wait_until="commit", timeout=45000)
        except BrowserError:
            # A navigation timeout can leave a usable document. Check the control
            # before retrying the navigation and discarding its progress.
            if not button.is_visible():
                if not attempt:
                    continue
                raise LoginUnavailable("服务器无法连接即梦登录页，请检查服务器到即梦的网络或配置出站代理") from None
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            _check_cancelled(s)
            try:
                button.wait_for(state="visible", timeout=3000)
                return
            except BrowserTimeout:
                pass
        if attempt:
            raise LoginUnavailable("即梦登录按钮未加载完成，请检查服务器网络后重试")


def _wait_for_qr(popup, s, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _check_cancelled(s)
        if popup.is_closed():
            raise LoginUnavailable("登录窗口已关闭，请重新发起扫码")
        qr = _qr_png(popup)
        if qr:
            return qr
        popup.wait_for_timeout(500)
    raise LoginUnavailable("登录页面已打开，但二维码未加载。请检查服务器到抖音或飞书登录服务的网络后重试")


def _sid_of(ctx) -> tuple[str, int | None]:
    jar = {c["name"]: c["value"] for c in ctx.cookies([HOME])}
    sid = jar.get("sessionid") or ""
    expires = None
    guard = jar.get("sid_guard") or ""
    bits = guard.split("|")
    if len(bits) >= 3 and bits[1].isdigit() and bits[2].isdigit():
        expires = int(bits[1]) + int(bits[2])
    return sid, expires


def register_session(sid: str, expires: int | None, remark: str = "") -> dict:
    """把一个已登录的 sessionid 落库并探活。扫码助手和远程浏览器共用。"""
    row = repo.find_account_session(sid)
    if row:
        return {"account_id": int(row["id"]), "credits": None,
                "message": "该账号已在池中，无需重复添加"}
    account_id = repo.add_account(sid, remark or "登录助手")
    if expires:
        repo.update_account(account_id, expires_at=expires)
    out = {"account_id": account_id, "credits": None,
           "message": "登录成功，已加入账号池"}
    # 探活只是为了把昵称/积分填上；失败不影响入池
    try:
        from .pool import pool
        res = pool.probe(account_id)
        out["credits"] = res.get("credits")
        out["message"] = (f"登录成功：{res.get('name') or '未命名'} · "
                          f"积分 {res.get('credits')}")
    except Exception as e:  # noqa: BLE001
        out["message"] = f"登录成功，但探活失败：{e}"
    return out


def _finish_success(s: LoginSession, sid: str, expires: int | None) -> None:
    res = register_session(sid, expires, s.remark or "扫码登录")
    s.account_id = res["account_id"]
    s.credits = res["credits"]
    s.state = "success"
    s.message = res["message"]


def _run(s: LoginSession) -> None:
    try:
        _check_cancelled(s)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - 环境相关
            raise LoginUnavailable(
                "登录助手未安装：uv pip install -r requirements-login.txt && "
                "playwright install chromium") from e

        with sync_playwright() as p:
            browser = _launch(p)
            try:
                _check_cancelled(s)
                ctx = browser.new_context(
                    locale="zh-CN", viewport={"width": 1280, "height": 900},
                    user_agent=UA)
                page = ctx.new_page()
                page.route("**/*", lambda route: route.abort() if route.request.resource_type == "media"
                           else route.continue_())
                s.message = "正在打开即梦登录页…"
                _open_login_page(page, s)

                # 已经登录（复用了浏览器 profile 之类）：直接收
                sid, expires = _sid_of(ctx)
                if sid:
                    _finish_success(s, sid, expires)
                    return

                label, app_name = PROVIDERS.get(s.provider,
                                                PROVIDERS["douyin"])
                # 打开登录弹窗 → 勾协议 → 选中的第三方登录（弹出 OAuth 二维码页）
                provider_button = page.get_by_role("button", name=label, exact=True)
                for _ in range(20):
                    _check_cancelled(s)
                    if provider_button.is_visible():
                        break
                    # Promotional overlays can cover the login button. Trigger its
                    # own handler without interacting with the promotion.
                    page.get_by_role("button", name="登录", exact=True).first.evaluate("b => b.click()")
                    page.wait_for_timeout(500)
                provider_button.wait_for(state="visible", timeout=15000)
                s.message = "已打开登录弹窗，正在拉起第三方登录…"
                provider_button.evaluate(
                    "b => {const dialog=b.closest('[role=dialog]') || b.closest('.lv-modal-content') || document;"
                    " const c=dialog.querySelector('input[type=checkbox]');"
                    " if(c && !c.checked) c.click();}")
                _check_cancelled(s)
                with ctx.expect_page(timeout=30000) as newp:
                    provider_button.evaluate("b => b.click()")
                popup = newp.value
                s.message = f"正在加载{app_name}登录二维码…"
                s.qr = _wait_for_qr(popup, s)
                s.qr_version += 1
                s.state = "waiting"
                s.message = f"用{app_name} App 扫码，并在手机上确认"
                last_shot = time.time()
                deadline = time.time() + TIMEOUT
                closed_at: float | None = None

                while time.time() < deadline:
                    if s.stop.is_set():
                        s.state = "cancelled"
                        s.message = "已取消扫码"
                        return
                    sid, expires = _sid_of(ctx)
                    if sid:
                        _finish_success(s, sid, expires)
                        return
                    if popup.is_closed():
                        # 扫码确认后弹窗会自己关，cookie 可能晚 1~2 秒才写进来
                        closed_at = closed_at or time.time()
                        if time.time() - closed_at > 5:
                            s.state = "error"
                            s.error = "扫码窗口已关闭，请重新发起"
                            s.message = s.error
                            return
                    elif closed_at:
                        closed_at = None
                    if time.time() - last_shot > QR_REFRESH:
                        try:
                            shot = _qr_png(popup)
                            if shot and shot != s.qr:  # 图没变就不加版本，别让前端白刷
                                s.qr = shot
                                s.qr_version += 1
                            last_shot = time.time()
                        except Exception:  # noqa: BLE001 - 截图失败不影响主流程
                            pass
                    time.sleep(1.0)

                s.state = "expired"
                s.message = "二维码已过期，请重新发起扫码"
            finally:
                browser.close()
    except LoginCancelled:
        s.state = "cancelled"
        s.message = "已取消扫码"
    except LoginUnavailable as e:
        s.state = "error"
        s.error = str(e)
        s.message = str(e)
    except Exception as e:  # noqa: BLE001 - 任何异常都要变成面板可读的状态
        s.state = "error"
        s.error = f"{type(e).__name__}: {e}"
        s.message = "扫码登录失败：" + s.error
    finally:
        if s.state in ("success", "error", "expired", "cancelled"):
            s.stop.set()


def start(remark: str = "", provider: str = "douyin") -> LoginSession:
    """发起一个扫码会话；已有进行中的会话时直接复用它。"""
    with _LOCK:
        for s in _SESSIONS.values():
            if s.state in ("starting", "waiting") and not s.stop.is_set():
                return s
        # 清理 10 分钟前的终态会话，避免内存堆积
        for k, v in list(_SESSIONS.items()):
            if v.state not in ("starting", "waiting") and time.time() - v.created_at > 600:
                _SESSIONS.pop(k, None)
        provider = provider if provider in PROVIDERS else "douyin"
        s = LoginSession(id=uuid.uuid4().hex[:12], remark=remark,
                         provider=provider)
        start_browser_thread(lambda: _run(s), name="jimeng-login")
        _SESSIONS[s.id] = s
        return s


def get(session_id: str) -> LoginSession | None:
    return _SESSIONS.get(session_id)


def cancel(session_id: str) -> bool:
    s = _SESSIONS.get(session_id)
    if not s:
        return False
    s.stop.set()
    return True


def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True
