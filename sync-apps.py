#!/usr/bin/env python3
"""Fetch the TV's apps, icons and HDMI inputs into app/apps.js and app/icons/.

webOS does not let a third-party app list installed apps, so the launcher reads them from here.

    python3 sync-apps.py 192.168.1.50          # sync
    python3 sync-apps.py 192.168.1.50 --list   # just print app ids and titles

When the TV can't be reached or sends no valid app list, nothing is changed and it exits with an
error. An app whose icon won't download keeps its icon from the last sync, or gets a plain tile.
If only the inputs fail, the inputs from the last sync are kept.

Needs: pip install websockets
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import urllib.request

import lgtv

APP_DIR = os.path.join(lgtv.HERE, "app")
APPS_JS = os.path.join(APP_DIR, "apps.js")
ICON_DIR = os.path.join(APP_DIR, "icons")
DEFAULT_ICON = "default-icon.png"   # in app/: a plain tile in the app's colour
MAX_ICON_BYTES = 5 * 1024 * 1024
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


class SyncError(Exception):
    pass


def describe(e):
    return str(e) or type(e).__name__


def write_atomic(path, data):
    # Write next to the target, then swap it in: an interrupted sync never leaves half a file.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + os.path.basename(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_previous():
    """The data from the last sync's apps.js, or {} when there is none or it can't be read."""
    try:
        with open(APPS_JS, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S)
    try:
        data = json.loads(m.group(1))
        if isinstance(data.get("apps"), list) and isinstance(data.get("inputs"), list):
            return data
    except (AttributeError, ValueError):
        pass
    print("  app/apps.js is unreadable: ignoring it")
    return {}


def valid_apps(answer):
    points = answer.get("launchPoints")
    if not isinstance(points, list) or not points:
        raise SyncError("the TV sent no app list")
    good = [p for p in points if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]
            and isinstance(p.get("title"), str)]
    if not good:
        raise SyncError("the TV's app list has no valid apps")
    if len(good) < len(points):
        print(f"  skipped {len(points) - len(good)} malformed app entries")
    return good


def valid_inputs(answer):
    devices = answer.get("devices")
    if not isinstance(devices, list):
        raise lgtv.TVError("no input list in the answer")
    return [{"appId": d["appId"], "label": d.get("label") or d.get("id") or d["appId"]}
            for d in devices if isinstance(d, dict) and isinstance(d.get("appId"), str) and d["appId"]]


def fetch_icon(point, ctx):
    urls = [u for u in (point.get("largeIcon"), point.get("icon"))
            if isinstance(u, str) and u.startswith(("http://", "https://"))]
    if not urls:
        raise ValueError("no icon URL")
    error = None
    for url in urls:
        try:
            with urllib.request.urlopen(url, context=ctx, timeout=10) as r:
                data = r.read(MAX_ICON_BYTES + 1)
            if len(data) > MAX_ICON_BYTES or not (data.startswith(IMAGE_MAGIC)
                                                  or data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
                raise ValueError("not an image")
            return data
        except Exception as e:
            error = e
    raise error


async def fetch(host):
    try:
        ws = await lgtv.session(host)
    except lgtv.PairingError:
        raise
    except (OSError, asyncio.TimeoutError, lgtv.websockets.WebSocketException) as e:
        raise SyncError(f"can't reach the TV at {host} ({describe(e)}); check that it's on and on "
                        "the same network")
    try:
        try:
            apps = valid_apps(await lgtv.call(ws, "ssap://com.webos.applicationManager/listLaunchPoints"))
        except (lgtv.TVError, asyncio.TimeoutError, lgtv.websockets.WebSocketException) as e:
            raise SyncError(f"couldn't read the app list: {describe(e)}")
        # Inputs are optional: on any failure here the last sync's inputs are kept.
        try:
            inputs = valid_inputs(await lgtv.call(ws, "ssap://tv/getExternalInputList"))
        except (lgtv.TVError, asyncio.TimeoutError, lgtv.websockets.WebSocketException) as e:
            print(f"  couldn't read the inputs: {describe(e)}")
            inputs = None
    finally:
        await ws.close()
    return apps, inputs


def sync(apps, inputs):
    previous = load_previous()
    old_inputs = previous.get("inputs") or []
    if inputs is None or (not inputs and old_inputs):
        print(f"  keeping the {len(old_inputs)} inputs from the last sync")
        inputs = old_inputs

    os.makedirs(ICON_DIR, exist_ok=True)
    ctx = lgtv.insecure_ctx()
    out_apps, stale = [], 0
    for p in apps:
        if p.get("hidden"):
            continue
        name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in p["id"]) + ".png"
        path = os.path.join(ICON_DIR, name)
        icon = "icons/" + name
        try:
            write_atomic(path, fetch_icon(p, ctx))
        except Exception as e:
            stale += 1
            if os.path.isfile(path) and os.path.getsize(path):
                print(f"  no icon for {p['id']} ({describe(e)}): keeping the one from the last sync")
            else:
                print(f"  no icon for {p['id']} ({describe(e)}): showing a plain tile")
                icon = DEFAULT_ICON
        out_apps.append({
            "id": p["id"],
            "title": p["title"],
            "icon": icon,
            "iconColor": p.get("iconColor") or p.get("bgColor") or "",
        })

    data = {"apps": out_apps, "inputs": inputs}
    text = ("// Generated by sync-apps.py: the TV's apps and inputs.\n"
            "window.TV_APPS = " + json.dumps(data, indent=1, ensure_ascii=False) + ";\n")
    write_atomic(APPS_JS, text.encode("utf-8"))
    note = f" ({stale} without a fresh icon)" if stale else ""
    print(f"Synced {len(out_apps)} apps{note} and {len(inputs)} inputs.")


async def main(host, list_only):
    apps, inputs = await fetch(host)
    if list_only:
        for p in apps:
            print(f'{p["id"]:45} {p["title"]}')
        return
    sync(apps, inputs)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = args[0] if args else os.environ.get("TV_HOST")
    if not host:
        sys.exit("usage: sync-apps.py <TV IP> [--list]")
    unchanged = "app/apps.js was left unchanged."
    try:
        asyncio.run(main(host, "--list" in sys.argv))
    except lgtv.PairingError as e:
        sys.exit(f"Pairing failed: {describe(e)}. {unchanged}")
    except SyncError as e:
        sys.exit(f"Sync failed: {e}. {unchanged}")
