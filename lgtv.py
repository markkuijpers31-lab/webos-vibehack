"""Connection to an LG TV's remote-control service (wss://TV:3001), the one LG's phone app uses.

The first connection shows a prompt on the TV: accept it. The key is kept in .lgtv-key.json.
"""
import asyncio
import json
import os
import ssl

import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(HERE, ".lgtv-key.json")

# Unsigned on purpose: newer firmware rejects the signed manifest that open-source clients share
# ("403 Pairing rejected: blacklisted certificate detected") but still accepts an unsigned one.
PERMISSIONS = [
    "LAUNCH", "LAUNCH_WEBAPP", "APP_TO_APP", "CLOSE", "TEST_OPEN", "TEST_PROTECTED", "CONTROL_AUDIO",
    "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK", "CONTROL_INPUT_MEDIA_RECORDING",
    "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_TV", "CONTROL_POWER", "CONTROL_TV_SCREEN",
    "READ_APP_STATUS", "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST", "READ_NETWORK_STATE",
    "READ_RUNNING_APPS", "READ_TV_CHANNEL_LIST", "WRITE_NOTIFICATION_TOAST", "READ_POWER_STATE",
    "READ_COUNTRY_INFO", "CONTROL_INPUT_TEXT", "CONTROL_MOUSE_AND_KEYBOARD", "READ_INSTALLED_APPS",
    "READ_SETTINGS", "READ_STORAGE_DEVICE_LIST",
]


class TVError(Exception):
    """The TV answered a request with an error, or without the data asked for."""


class PairingError(TVError):
    """The TV refused this computer, or wants it to pair again."""


def insecure_ctx():
    # The TV uses a certificate from LG's own CA.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_keys():
    try:
        with open(KEY_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


async def session(host, allow_prompt=True, **connect_args):
    """A registered connection. With allow_prompt=False, a TV that asks to pair again (the saved
    key is missing or no longer valid) raises PairingError instead of waiting for the prompt."""
    keys = load_keys()
    ws = await websockets.connect(f"wss://{host}:3001", ssl=insecure_ctx(), open_timeout=10, max_size=None,
                                  **connect_args)
    payload = {
        "forcePairing": False,
        "pairingType": "PROMPT",
        "manifest": {"manifestVersion": 1, "appVersion": "1.1", "permissions": PERMISSIONS},
    }
    if host in keys:
        payload["client-key"] = keys[host]
    elif allow_prompt:
        print("Accept the prompt on the TV...")
    try:
        await ws.send(json.dumps({"type": "register", "id": "register_0", "payload": payload}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 60))
            if msg.get("type") == "registered":
                break
            if msg.get("type") == "error":
                raise PairingError(msg.get("error") or "the TV refused this computer")
            if not allow_prompt and (msg.get("payload") or {}).get("pairingType") == "PROMPT":
                raise PairingError("the TV asks to pair again: the saved pairing key is missing or no "
                                   "longer valid")
        key = msg["payload"]["client-key"]
    except BaseException:
        await ws.close()
        raise
    if keys.get(host) != key:
        keys[host] = key
        with open(KEY_FILE, "w") as f:
            json.dump(keys, f, indent=1)
    return ws


async def call(ws, uri, payload=None, n=[0]):
    """The answer's payload. Raises TVError when the TV answers with an error or no payload."""
    n[0] += 1
    rid = f"c{n[0]}"
    await ws.send(json.dumps({"type": "request", "id": rid, "uri": uri, "payload": payload or {}}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), 20))
        if msg.get("id") != rid:
            continue
        answer = msg.get("payload")
        if msg.get("type") == "error" or not isinstance(answer, dict) or answer.get("returnValue") is False:
            reason = msg.get("error") or (answer or {}).get("errorText") or "no valid answer"
            raise TVError(f"{uri}: {reason}")
        return answer
