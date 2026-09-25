"""Drive the review tool in headless Chrome over the DevTools protocol.

There is no Playwright or chromium-cli on this machine; google-chrome and the `websocket-client`
Python package are. Import this from a check script (see example_mismatch.py):

    import sys; sys.path.insert(0, "<skill>/scripts")
    from cdp import Browser, new_session, recorded_actions

    view = new_session(base, game_id=78921, seat=0)
    sid = view["session"]["session_id"]
    with Browser(scratch) as b:
        b.nav(f"{base}/#/label/{sid}/1")
        b.click_card(holder=0, index=2)            # left click on your own card = play it
        b.shot("after-play")                        # -> <scratch>/shots/after-play.png
        assert not b.errors, b.errors

Each Browser gets its own free debugging port, its own throwaway profile and its own process group,
and the whole group is killed on exit, so no headless Chrome is left holding a port.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websocket  # websocket-client

# ---- Server API ------------------------------------------------------------------------------------


def api(base: str, path: str, body: dict | None = None):
    """GET (or POST `body`) a JSON API route; raises RuntimeError with the server's error message."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{e.code} {path}: {e.read().decode(errors='replace')}") from None


def new_session(base: str, game_id: int | None = None, seat: int | None = None, labeller: str = "ui-check") -> dict:
    """Start a Label session. Returns the label view; the id is view["session"]["session_id"]."""
    body: dict = {"labeller": labeller}
    if game_id is not None:
        body["game_id"] = game_id
    if seat is not None:
        body["seat"] = seat
    return api(base, "/api/sessions", body)


def recorded_actions(base: str, game_id: int) -> tuple[list[dict], int, int]:
    """The game's recorded actions, its starting player and player count.

    Action i is UI turn i + 1, made by seat (i + start) % n. type: 0 play, 1 discard (target = card
    id), 2 colour clue (value = suit index), 3 rank clue (target = seat), 4 game over.
    """
    ex = api(base, f"/api/games/{game_id}/inspect")["game"]["export"]
    return ex["actions"], int(ex["options"].get("startingPlayer") or 0), len(ex["players"])


# ---- Browser ---------------------------------------------------------------------------------------

_KEYS = {  # key -> (code, windowsVirtualKeyCode)
    " ": ("Space", 32), "ArrowRight": ("ArrowRight", 39), "ArrowLeft": ("ArrowLeft", 37),
    "Tab": ("Tab", 9), "Backspace": ("Backspace", 8), "Home": ("Home", 36), "End": ("End", 35),
    "Escape": ("Escape", 27), "[": ("BracketLeft", 219), "]": ("BracketRight", 221),
    "o": ("KeyO", 79), "j": ("KeyJ", 74),
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Browser:
    def __init__(self, scratch: str | Path, width: int = 1440, height: int = 810):
        self.scratch = Path(scratch)
        self.size = (width, height)
        self.errors: list[str] = []  # uncaught exceptions and console.error calls, in order
        self._id = 0

    def __enter__(self) -> "Browser":
        self.shots = self.scratch / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.profile = tempfile.mkdtemp(prefix="chrome-", dir=self.scratch)
        port = _free_port()
        self.proc = subprocess.Popen(
            ["google-chrome", "--headless=new", f"--remote-debugging-port={port}", "--remote-allow-origins=*",
             f"--user-data-dir={self.profile}", f"--window-size={self.size[0]},{self.size[1]}",
             "--no-first-run", "--no-default-browser-check", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.time() + 15
            while True:
                try:
                    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                    page = next(t for t in targets if t["type"] == "page")
                    break
                except Exception:
                    if time.time() > deadline or self.proc.poll() is not None:
                        raise RuntimeError("headless Chrome did not start") from None
                    time.sleep(0.2)
            self.ws = websocket.create_connection(page["webSocketDebuggerUrl"], suppress_origin=True)
            self.cdp("Page.enable")
            self.cdp("Runtime.enable")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc) -> None:
        if getattr(self, "ws", None) is not None:
            self.ws.close()
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
        shutil.rmtree(self.profile, ignore_errors=True)

    # -- Protocol --

    def cdp(self, method: str, **params):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            self._event(m)
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def _event(self, m: dict) -> None:
        if m.get("method") == "Runtime.exceptionThrown":
            d = m["params"]["exceptionDetails"]
            self.errors.append(d.get("exception", {}).get("description") or d.get("text", "exception"))
        elif m.get("method") == "Runtime.consoleAPICalled" and m["params"]["type"] == "error":
            self.errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in m["params"]["args"]))

    def js(self, expr: str):
        """Evaluate an expression in the page (promises awaited) and return its JSON value."""
        r = self.cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"js failed: {expr}\n{r['exceptionDetails'].get('exception', {}).get('description')}")
        return r.get("result", {}).get("value")

    def wait_for(self, expr: str, timeout: float = 10) -> None:
        """Wait until `expr` is truthy in the page."""
        deadline = time.time() + timeout
        while not self.js(f"!!({expr})"):
            if time.time() > deadline:
                raise TimeoutError(f"timed out waiting for {expr}")
            time.sleep(0.1)

    # -- Actions --

    def nav(self, url: str) -> None:
        """Open a URL and wait for the stage to be drawn (any hash route: lobby, label, admin, game)."""
        target = url.partition("#")[2]
        # The route to land on; the app may rewrite a trailing turn (clamped, or added for #/label/<sid>).
        route = "#" + re.sub(r"/\d+$", "", target)
        r = self.cdp("Page.navigate", url=url)
        if "loaderId" not in r:
            # Same-document (hash-only) navigation. Page.navigate sometimes leaves the hash unchanged here
            # (seen switching between two label sessions), and a reload then reopens the old route. Set
            # the hash from the page, wait for it, then reload: the app keeps in-memory state otherwise.
            self.js(f"location.hash = {json.dumps('#' + target)}")
            self.wait_for(f"location.hash.startsWith({json.dumps(route)})", timeout=5)
            self.cdp("Page.reload")
        time.sleep(0.2)
        self.wait_for("document.readyState === 'complete' && document.querySelector('#app *')")
        if target and not self.js(f"location.hash.startsWith({json.dumps(route)})"):
            raise RuntimeError(f"nav to {url} landed on {self.js('location.href')}")

    def click(self, x: float, y: float, button: str = "left", shift: bool = False) -> None:
        mods = 8 if shift else 0
        for kind in ("mousePressed", "mouseReleased"):
            self.cdp("Input.dispatchMouseEvent", type=kind, x=x, y=y, button=button, clickCount=1, modifiers=mods)

    def _center(self, selector: str, index: int) -> tuple[float, float]:
        x, y = self.js(f"""(() => {{ const e = document.querySelectorAll({json.dumps(selector)})[{index}];
            if (!e) throw new Error('no element {selector} [{index}]');
            const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }})()""")
        return x, y

    def click_selector(self, selector: str, index: int = 0, **kw) -> None:
        self.click(*self._center(selector, index), **kw)

    def hover(self, selector: str, index: int = 0) -> None:
        """Move the mouse over an element, e.g. to show its `data-tip` tooltip (wait ~0.3 s, then shot)."""
        x, y = self._center(selector, index)
        self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)

    def unhover(self) -> None:
        """Move the mouse to the page's top-left corner, so no tooltip covers the next screenshot."""
        self.cdp("Input.dispatchMouseEvent", type="mouseMoved", x=1, y=1)

    def click_card(self, holder: int, index: int, button: str = "left", shift: bool = False) -> None:
        """Mouse down on the `index`-th card (0 = leftmost = slot 1) in seat `holder`'s hand.

        Own hand: left = play, right = discard. Teammate's hand: left = colour clue, right = rank clue.
        Shift = mark as "also OK" instead of choosing.
        """
        self.click_selector(f'img[data-holder="{holder}"]', index, button=button, shift=shift)

    def key(self, key: str) -> None:
        code, vk = _KEYS[key]
        for kind in ("keyDown", "keyUp"):
            self.cdp("Input.dispatchKeyEvent", type=kind, key=key, code=code, windowsVirtualKeyCode=vk)

    def shot(self, name: str) -> Path:
        """Save a PNG screenshot to <scratch>/shots/<name>.png and return its path (then Read it)."""
        path = self.shots / f"{name}.png"
        path.write_bytes(base64.b64decode(self.cdp("Page.captureScreenshot")["data"]))
        return path

    def text(self, selector: str) -> str | None:
        return self.js(f"document.querySelector({json.dumps(selector)})?.innerText ?? null")
