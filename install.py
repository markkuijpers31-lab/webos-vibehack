#!/usr/bin/env python3
"""Sync the TV's apps, package the launcher and install it on an LG TV in Developer Mode.

Works on Windows, Linux and macOS. Run it with the venv's Python, so websockets is found:

    venv/bin/python install.py 192.168.1.50            # Linux, macOS
    venv\\Scripts\\python install.py 192.168.1.50       # Windows

First, once: connect this computer to the TV, with the IP address and passphrase that the
Developer Mode app shows (Key Server on):

    venv/bin/python install.py --setup 192.168.1.50 ABC123
"""
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "com.gathin.tvhome"
DEVICE = os.environ.get("DEVICE", "lgtv")


def run(cmd, check=True):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=check, cwd=HERE)


def ares(tool, *args, check=True):
    # LG's webOS CLI: use it from PATH, otherwise fetch it with npx. shutil.which finds npx.cmd on
    # Windows, which runs where PowerShell's script policy blocks npx.ps1.
    exe = shutil.which("ares-" + tool)
    if exe:
        return run([exe, *args], check)
    npx = shutil.which("npx")
    if not npx:
        sys.exit("Node.js not found: install it from https://nodejs.org (npx fetches LG's ares CLI).")
    return run([npx, "-y", "-p", "@webos-tools/cli", "ares-" + tool, *args], check)


def setup(host, passphrase):
    # Add the device profile, or update it if it exists, then fetch its SSH key from the Key Server.
    profile = "{'host':'%s','port':'9922','username':'prisoner'}" % host
    if ares("setup-device", "-a", DEVICE, "-i", profile, check=False).returncode:
        ares("setup-device", "-m", DEVICE, "-i", profile)
    ares("novacom", "--device", DEVICE, "--getkey", "--passphrase", passphrase)
    print(f"Connected to {host}. Now install: install.py {host}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--setup" in sys.argv:
        if len(args) != 2:
            sys.exit("usage: install.py --setup <TV IP> <passphrase>")
        return setup(*args)
    host = args[0] if args else os.environ.get("TV_HOST")
    if not host:
        sys.exit("usage: install.py <TV IP>   (or set TV_HOST)\n"
                 "       install.py --setup <TV IP> <passphrase>   (once, first)")

    if run([sys.executable, "sync-apps.py", host], check=False).returncode:
        sys.exit("Stopped: the sync failed (see above), so the launcher was not installed.")

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
