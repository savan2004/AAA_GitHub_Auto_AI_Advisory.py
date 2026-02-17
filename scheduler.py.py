# scheduler.py - Cron jobs for maintenance

import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class Scheduler:
    def __init__(self):
        self.jobs = []
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
    
    def add_job(self, interval_hours, func, *args, **kwargs):
        """Add a recurring job"""
        self.jobs.append({
            "interval": interval_hours * 3600,
            "func": func,
            "args": args,
            "kwargs": kwargs,
            "last_run": 0
        })
    
    def _run(self):
        while self.running:
            now = time.time()
            for job in self.jobs:
                if now - job["last_run"] >= job["interval"]:
                    try:
                        job["func"](*job["args"], **job["kwargs"])
                        job["last_run"] = now
                    except Exception as e:
                        logger.error(f"Scheduler job failed: {e}")
            time.sleep(60)  # Check every minute
    
    def stop(self):
        self.running = False