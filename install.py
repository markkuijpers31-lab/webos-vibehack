#!/usr/bin/env python3
"""Sync the TV's apps, package the launcher and install it on an LG TV in Developer Mode.

Works on Windows, Linux and macOS. Run it with the venv's Python, so websockets is found:

    venv/bin/python install.py 192.168.1.50            # Linux, macOS
    venv\\Scripts\\python install.py 192.168.1.50       # Windows

One-time setup (Developer Mode app open on the TV, Key Server on), see the README:
    ares-setup-device -a lgtv -i "{'host':'192.168.1.50','port':'9922','username':'prisoner'}"
    ares-novacom --device lgtv --getkey          # enter the passphrase shown on the TV
    ares-setup-device -m lgtv -i "{'privatekey':'lgtv_webos','passphrase':'<passphrase>'}"
"""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "com.gathin.tvhome"
DEVICE = os.environ.get("DEVICE", "lgtv")


def run(cmd):
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=HERE)


def ares(tool, *args):
    # LG's webOS CLI: use it from PATH, otherwise fetch it with npx.
    exe = shutil.which("ares-" + tool)
    if exe:
        run([exe, *args])
        return
    npx = shutil.which("npx")
    if not npx:
        sys.exit("Node.js not found: install it from https://nodejs.org (npx fetches LG's ares CLI).")
    run([npx, "-y", "-p", "@webos-tools/cli", "ares-" + tool, *args])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = args[0] if args else os.environ.get("TV_HOST")
    if not host:
        sys.exit("usage: install.py <TV IP>   (or set TV_HOST)")

    run([sys.executable, "sync-apps.py", host])

    build = os.path.join(HERE, "build")
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(build)
    ares("package", "app", "-o", "build")
    ipk = glob.glob(os.path.join(build, APP_ID + "_*.ipk"))[0]
    ares("install", "-d", DEVICE, ipk)
    ares("launch", "-d", DEVICE, APP_ID)
    print("Installed. Hold a number key on the remote while it is open to make it a one-press shortcut.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
