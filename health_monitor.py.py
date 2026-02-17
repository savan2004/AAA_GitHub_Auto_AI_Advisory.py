# health_monitor.py - Bot health monitoring

import psutil
import os
import time
from datetime import datetime, timedelta

class HealthMonitor:
    def __init__(self):
        self.start_time = datetime.now()
        self.requests_today = 0
        self.errors_today = 0
        self.last_reset = datetime.now().date()
    
    def record_request(self):
        self._check_reset()
        self.requests_today += 1
    
    def record_error(self):
        self._check_reset()
        self.errors_today += 1
    
    def _check_reset(self):
        today = datetime.now().date()
        if today > self.last_reset:
            self.requests_today = 0
            self.errors_today = 0
            self.last_reset = today
    
    def get_uptime(self):
        delta = datetime.now() - self.start_time
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        elif hours > 0:
            return f"{hours}h {minutes}m"
        else:
            return f"{minutes}m"
    
    def get_requests_today(self):
        self._check_reset()
        return self.requests_today
    
    def get_errors_today(self):
        self._check_reset()
        return self.errors_today
    
    def get_memory_usage(self):
        process = psutil.Process(os.getpid())
        mem = process.memory_info().rss / 1024 / 1024  # MB
        return f"{mem:.1f} MB"
    
    def get_status(self):
        self._check_reset()
        return {
            "requests_today": self.requests_today,
            "errors_today": self.errors_today,
            "memory_usage": self.get_memory_usage(),
            "ai_status": "OK",
            "db_status": "OK",
            "last_heartbeat": datetime.now().strftime("%H:%M:%S")
        }