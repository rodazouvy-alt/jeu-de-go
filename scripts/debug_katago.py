"""Debug script — test KataGo analysis on one SGF."""
import json
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from goprogress.config import load_config, resolve_path

cfg = load_config()
katago = cfg["katago"]

cmd = [
    katago["executable"],
    "analysis",
    "-config", str(resolve_path(katago["config"])),
    "-model", katago["model"],
]

print("CMD:", cmd)
proc = subprocess.Popen(
    cmd,
    cwd=katago["working_dir"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)

stderr_lines = []

def read_stderr():
    for line in proc.stderr:
        stderr_lines.append(line.rstrip())
        print("STDERR:", line.rstrip())

t = threading.Thread(target=read_stderr, daemon=True)
t.start()

query = {
    "id": "test",
    "moves": [["B", "D4"], ["W", "Q16"]],
    "rules": "chinese",
    "komi": 6.5,
    "boardXSize": 19,
    "boardYSize": 19,
    "analyzeTurns": [0, 1, 2],
    "maxVisits": 50,
}
print("QUERY:", json.dumps(query))
proc.stdin.write(json.dumps(query) + "\n")
proc.stdin.flush()

import time
time.sleep(30)

print("--- STDOUT ---")
while True:
    line = proc.stdout.readline()
    if not line:
        break
    print("OUT:", line.rstrip()[:200])

proc.terminate()
print("--- done, stderr count:", len(stderr_lines))
