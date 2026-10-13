"""Local process lifecycle, shared by the server owner and the Windows tray."""
import threading


class ServiceControl:
    def __init__(self):
        self._lock=threading.Lock()
        self.server=None
        self.action=None

    def attach(self,server):
        with self._lock:
            self.server=server
            if self.action:server.should_exit=True

    def request(self,action):
        if action not in ('restart','stop'):raise ValueError('Unknown service action')
        with self._lock:
            if self.action=='stop' or self.action=='restart' and action=='restart':return False
            self.action=action
            if self.server:self.server.should_exit=True
            return True

    def next_cycle(self):
        with self._lock:
            self.server=None
            if self.action!='restart':return False
            self.action=None
            return True

    @property
    def status(self):
        with self._lock:
            if self.action=='stop':return 'Остановка'
            if self.action=='restart':return 'Перезапуск'
            if self.server and self.server.started and not self.server.should_exit:return 'Работает'
            return 'Запуск'

    @property
    def busy(self):
        with self._lock:return self.action is not None
