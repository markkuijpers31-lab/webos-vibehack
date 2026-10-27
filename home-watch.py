#!/usr/bin/env python3
"""Make the Home button open the launcher instead of LG's home.

webOS does not let an app replace Home, or even see which app is in front, so this runs on
another always-on device on the same network (a PC, a Mac, a Raspberry Pi, a phone in Termux). It
watches the foreground app over the TV's remote-control service and, the moment LG's home
appears, launches the launcher on top. LG's home shows for about half a second.

Press Home twice within ESCAPE_SECONDS to stay on LG's home (for its menus).

    python3 home-watch.py 192.168.1.50
    python3 home-watch.py 192.168.1.50 --log home-watch.log    # also log to a file (rotated)

It keeps running while the TV is off, in standby, restarting or off the network, and reconnects
when it's back. Without a console (pythonw on Windows) and without --log, it logs to
home-watch.log next to this script. Only one watcher runs per TV: a second one exits.

Needs: pip install websockets. Pair once first (install.py or sync-apps.py does it).
"""
import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time

import lgtv

LAUNCHER = "com.gathin.tvhome"
LG_HOME = "com.webos.app.home"
ESCAPE_SECONDS = 3.0
RETRY_MIN, RETRY_MAX = 5, 20       # seconds between connection attempts
STABLE_SECONDS = 10                # a connection that drops sooner counts as a failed attempt
STILL_DOWN_EVERY = 3600            # while the TV stays unreachable, log that once an hour

log = logging.getLogger("home-watch")


def setup_logging(path):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    if not path and sys.stderr is None:   # pythonw: no console to write to
        path = os.path.join(lgtv.HERE, "home-watch.log")
    handlers = []
    if path:
        handlers.append(logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                                             encoding="utf-8"))
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for h in handlers:
        h.setFormatter(fmt)
        log.addHandler(h)
    log.setLevel(logging.INFO)


def single_instance(host):
    """Hold a lock for this TV while the process runs; None if another watcher holds it."""
    safe = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in host)
    f = open(os.path.join(lgtv.HERE, f".home-watch-{safe}.lock"), "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


class Redirect:
    """Decides, per foreground change, whether to send LG's home to the launcher."""

    def __init__(self):
        self.app = None
        self.last_redirect = 0.0

    def on_foreground(self, app, now):
        # Only changes count: the TV can report the same app twice, or an empty id mid-switch,
        # and a repeat of LG's home must not count as a second press.
        if not app or app == self.app:
            return None
        self.app = app
        if app != LG_HOME:
            return None
        if now - self.last_redirect < ESCAPE_SECONDS:
            self.last_redirect = 0.0
            return "stay"
        self.last_redirect = now
        return "redirect"


async def watch(host, state, quiet):
    ws = await lgtv.session(host, allow_prompt=False, ping_interval=10, ping_timeout=10)
    state["since"] = time.monotonic()
    if not quiet:
        log.info("connected to %s", host)
    redirect = Redirect()
    try:
        await ws.send(json.dumps({"type": "subscribe", "id": "fg",
                                  "uri": "ssap://com.webos.applicationManager/getForegroundAppInfo",
                                  "payload": {}}))
        async for raw in ws:
            msg = json.loads(raw)
            payload = msg.get("payload") or {}
            if msg.get("id") == "launch":
                if msg.get("type") == "error" or payload.get("returnValue") is False:
                    log.warning("couldn't open the launcher: %s",
                                msg.get("error") or payload.get("errorText") or "no reason given")
                continue
            if msg.get("id") != "fg":
                continue
            if msg.get("type") == "error" or payload.get("returnValue") is False:
                raise lgtv.TVError(f"the TV refused to report the foreground app: {msg.get('error')}")
            action = redirect.on_foreground(payload.get("appId"), time.monotonic())
            if action == "stay":
                log.info("Home pressed twice: staying on LG home")
            elif action == "redirect":
                await ws.send(json.dumps({"type": "request", "id": "launch",
                                          "uri": "ssap://system.launcher/launch", "payload": {"id": LAUNCHER}}))
                log.info("LG home -> launcher")
    finally:
        await ws.close()


async def main(host):
    delay, down_since, last_error, last_logged, quiet = RETRY_MIN, None, None, 0.0, False
    while True:
        state, unexpected = {"since": None}, None
        try:
            await watch(host, state, quiet)
            error = "the TV closed the connection"
        except lgtv.PairingError as e:
            log.error("pairing failed: %s. Run install.py (or sync-apps.py) for %s and accept the prompt "
                      "on the TV, then start the watcher again.", e, host)
            return 2
        except (OSError, asyncio.TimeoutError, lgtv.websockets.WebSocketException, lgtv.TVError) as e:
            # TV off, in standby, restarting or off the network, or this PC just woke up.
            error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        except Exception as e:
            unexpected = e     # a bug or an answer this doesn't understand: log it with its traceback
            error = f"unexpected {type(e).__name__}: {e}"

        now = time.monotonic()
        if state["since"] is not None and now - state["since"] >= STABLE_SECONDS:
            log.info("disconnected (%s); reconnecting", error, exc_info=unexpected)
            delay, down_since, last_error, quiet = RETRY_MIN, now, None, False
        else:
            # Unreachable, or connected and dropped at once (the TV refused the subscription, say):
            # back off, and log each new error once, then hourly.
            delay = min(delay * 2, RETRY_MAX)
            if down_since is None:
                down_since = now
            what = "connection dropped right away" if state["since"] is not None else "TV not reachable"
            if error != last_error:
                log.info("%s (%s); retrying every %d s", what, error, RETRY_MAX, exc_info=unexpected)
                last_error, last_logged = error, now
            elif now - last_logged >= STILL_DOWN_EVERY:
                log.info("%s for %d min (%s)", what, (now - down_since) // 60, error)
                last_logged = now
            quiet = state["since"] is not None and error == last_error
        await asyncio.sleep(delay)


def run():
    args = sys.argv[1:]
    log_path = None
    if "--log" in args:
        i = args.index("--log")
        if i + 1 >= len(args):
            sys.exit("usage: home-watch.py <TV IP> [--log FILE]")
        log_path = os.path.abspath(args[i + 1])
        del args[i:i + 2]
    host = args[0] if args else os.environ.get("TV_HOST")
    if not host:
        sys.exit("usage: home-watch.py <TV IP> [--log FILE]")
    setup_logging(log_path)

    if host not in lgtv.load_keys():
        # Connecting without a key would put a pairing prompt on the TV every few seconds.
        log.error("no pairing key for %s in %s: run install.py (or sync-apps.py) for this TV first",
                  host, lgtv.KEY_FILE)
        return 1
    lock = single_instance(host)
    if lock is None:
        log.info("another home-watch for %s is already running: exiting", host)
        return 0
    log.info("home-watch started for %s (pid %d)", host, os.getpid())
    try:
        return asyncio.run(main(host))
    except KeyboardInterrupt:
        log.info("stopped")
        return 0


if __name__ == "__main__":
    sys.exit(run())
