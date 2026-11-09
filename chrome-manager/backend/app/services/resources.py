import psutil

class Resources:
    def __init__(self):
        psutil.cpu_percent()
        self.current = {}
        self.sample()

    def sample(self):
        memory = psutil.virtual_memory()
        self.current = {
            'cpu_percent': psutil.cpu_percent(),
            'memory_percent': memory.percent,
            'memory_used_mb': round(memory.used / 1024**2),
            'memory_total_mb': round(memory.total / 1024**2),
            'memory_available_mb': round(memory.available / 1024**2),
        }
        return self.current

    def blocked_reason(self, settings):
        m = self.current
        if m['memory_percent'] >= settings.max_memory_percent or m['memory_available_mb'] < settings.min_available_memory_mb:
            return 'Low RAM: browser launches are paused until memory is available.'
        if m['cpu_percent'] >= settings.max_cpu_percent:
            return 'High CPU: browser launches are paused until load decreases.'
        return None
