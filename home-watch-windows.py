#!/usr/bin/env python3
"""Run home-watch.py in the background on this Windows PC: Task Scheduler starts it, without a
window, every time you sign in. Run it with the venv's Python, from any folder:

    venv\\Scripts\\python home-watch-windows.py install 192.168.1.50     # install or update, and start
    venv\\Scripts\\python home-watch-windows.py status                   # is it running? recent log
    venv\\Scripts\\python home-watch-windows.py uninstall                # stop and remove

It uses this folder's venv (pythonw.exe, so no window) and pairing key (.lgtv-key.json): run
install.py first. The log is home-watch.log in this folder. It does not change power settings:
while the PC sleeps, Home opens LG's home as usual, and the watcher reconnects after it wakes.
"""
import json
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
TASK = "webos-vibehack home-watch"
KEY_FILE = os.path.join(HERE, ".lgtv-key.json")
LOG = os.path.join(HERE, "home-watch.log")


def quote(arg):
    return f'"{arg}"' if " " in arg or not arg else arg


def task_xml(pythonw, script, host, log, workdir, user):
    """Task Scheduler definition: at sign-in, no window, no time limit, one instance, restarts if
    it fails to start, keeps running on battery, never wakes the PC."""
    args = " ".join(quote(a) for a in (script, host, "--log", log))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Sends the LG TV's Home button to the webos-vibehack launcher. {escape(script)}</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(pythonw)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def schtasks(*args, check=True):
    r = subprocess.run(["schtasks", *args], capture_output=True, text=True, errors="replace")
    if check and r.returncode:
        sys.exit(f"schtasks {args[0]} failed: {(r.stderr or r.stdout).strip()}")
    return r


def install(host):
    pythonw = os.path.join(HERE, "venv", "Scripts", "pythonw.exe")
    python = os.path.join(HERE, "venv", "Scripts", "python.exe")
    if not os.path.isfile(pythonw):
        sys.exit(f"No virtual environment in {HERE}\\venv: create it first (see the README, step 2).")
    if subprocess.run([python, "-c", "import websockets"], capture_output=True).returncode:
        sys.exit(f"websockets is missing from the venv: run  {quote(python)} -m pip install websockets")
    try:
        with open(KEY_FILE) as f:
            paired = host in json.load(f)
    except (OSError, ValueError):
        paired = False
    if not paired:
        sys.exit(f"No pairing key for {host} in {KEY_FILE}: run install.py {host} first and accept the "
                 "prompt on the TV.")

    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ['USERNAME']}".lstrip("\\")
    xml = task_xml(pythonw, os.path.join(HERE, "home-watch.py"), host, LOG, HERE, user)
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-16") as f:
            f.write(xml)
        schtasks("/End", "/TN", TASK, check=False)        # an update replaces a running watcher
        schtasks("/Create", "/TN", TASK, "/XML", path, "/F")
    finally:
        os.unlink(path)
    schtasks("/Run", "/TN", TASK)
    print(f"Installed and started: Task Scheduler runs the watcher for {host} at every sign-in.")
    print(f"Press Home on the remote. Log: {LOG}")


def uninstall():
    if schtasks("/Query", "/TN", TASK, check=False).returncode:
        print("Not installed.")
        return
    schtasks("/End", "/TN", TASK, check=False)
    schtasks("/Delete", "/TN", TASK, "/F")
    print("Removed. Home opens LG's home again.")


def status():
    r = schtasks("/Query", "/TN", TASK, "/FO", "LIST", check=False)
    if r.returncode:
        print("Not installed.")
        return
    print(r.stdout.strip())
    try:
        with open(LOG, encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-10:]
        print(f"\nLast lines of {LOG}:\n" + "".join(tail))
    except OSError:
        print(f"\nNo log yet at {LOG}")


if __name__ == "__main__":
    if os.name != "nt":
        sys.exit("This sets up Windows Task Scheduler. On Linux or macOS, see the README.")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "install" and len(sys.argv) == 3:
        install(sys.argv[2])
    elif cmd == "uninstall":
        uninstall()
    elif cmd == "status":
        status()
    else:
        sys.exit("usage: home-watch-windows.py install <TV IP> | status | uninstall")
