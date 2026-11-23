"""Helpers shared by the perf scripts (restart of the dev db container)."""

import subprocess
import time


def restart_db() -> None:
    subprocess.run(["docker", "compose", "restart", "db"], check=True, capture_output=True)
    for _ in range(60):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "mars", "-d", "mars"],
            capture_output=True,
        )
        if r.returncode == 0:
            time.sleep(1)
            return
        time.sleep(1)
    raise RuntimeError("db did not come back")
