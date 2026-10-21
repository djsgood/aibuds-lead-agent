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
    autostage=os.environ.get("LEAD_AGENT_AUTO_STAGE", "true").lower() == "true"
    while True:
        result=subprocess.run([sys.executable,"-m","lead_agent","run","--config",config,"--execute"],check=False)
        stage_code=None
        if autostage and result.returncode == 0:
            stage_code=subprocess.run([sys.executable,"-m","lead_agent","stage"],check=False).returncode
        message='{"event":"scheduled_run_finished","run_exit_code":'+str(result.returncode)
        if stage_code is not None: message+=',"stage_exit_code":'+str(stage_code)
        message+="}"
        print(message,flush=True)
        time.sleep(interval)


if __name__=="__main__": main()
