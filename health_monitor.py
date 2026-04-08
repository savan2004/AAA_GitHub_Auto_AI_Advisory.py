import time
import logging

class HealthMonitor:
    def __init__(self):
        self.uptime = 0
        self.is_running = True
        self.logger = logging.getLogger('HealthMonitor')
        logging.basicConfig(level=logging.INFO)

    def start_monitoring(self):
        self.logger.info('Starting health monitoring...')
        while self.is_running:
            time.sleep(60)  # Simulate monitoring interval
            self.uptime += 1
            self.logger.info(f'Uptime: {self.uptime} minute(s)')
            self.check_health()

    def check_health(self):
        # Simulate health check logic
        heartbeat = self.get_heartbeat()
        if not heartbeat:
            self.handle_crash()

    def get_heartbeat(self):
        # Simulate heartbeat check (returns True if all is well)
        return True

    def handle_crash(self):
        self.logger.error('Service has crashed!')
        self.is_running = False

if __name__ == '__main__':
    monitor = HealthMonitor()
    try:
        monitor.start_monitoring()
    except KeyboardInterrupt:
        monitor.logger.info('Monitoring interrupted manually.')
        monitor.is_running = False
