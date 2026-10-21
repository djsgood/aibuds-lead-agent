"""Deployment preparation only. Scheduling is disabled unless explicitly enabled."""
import os
import subprocess
import sys
import time


def main():
    if os.environ.get("ENABLE_SCHEDULE", "false").lower() != "true":
        raise SystemExit("Scheduling disabled. Enable only after controlled tests and deployment approval.")
    path=os.environ.get("LEAD_AGENT_DB", "")
    if not path.startswith("/data/"):
        raise SystemExit("Scheduled Railway worker requires a persistent /data database path.")
    interval=int(os.environ.get("LEAD_AGENT_INTERVAL_SECONDS", "86400"))
    if interval < 3600: raise SystemExit("Minimum schedule interval is one hour.")
    config=os.environ.get("LEAD_AGENT_CONFIG", "config/targets.json")
    while True:
        result=subprocess.run([sys.executable,"-m","lead_agent","run","--config",config,"--execute"],check=False)
        print('{"event":"scheduled_run_finished","exit_code":'+str(result.returncode)+'}',flush=True)
        time.sleep(interval)


if __name__=="__main__": main()
