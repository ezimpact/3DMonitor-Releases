"""Main-PC sidebar order, shared with Telegram without rewriting printer entries."""
import json
from .config import atomic_json


class PrinterOrder:
    def __init__(self, path):
        self.path = path
        self.saved = json.loads(path.read_text(encoding='utf-8-sig'))['ids'] if path.exists() else None
        if self.saved is not None and (not isinstance(self.saved, list) or
                any(not isinstance(pid, str) for pid in self.saved) or len(set(self.saved)) != len(self.saved)):
            raise ValueError('Некорректный сохранённый порядок принтеров')

    def current(self, printers):
        if self.saved is None:
            return None
        ids = [item['id'] for item in printers]
        return [pid for pid in self.saved if pid in ids] + [pid for pid in ids if pid not in self.saved]

    def save(self, ids, printers):
        known = [item['id'] for item in printers]
        if len(ids) != len(set(ids)) or set(ids) != set(known):
            raise ValueError('Список принтеров изменился. Обновите страницу и повторите перестановку.')
        atomic_json(self.path, {'schema_version': 1, 'ids': ids})
        self.saved = list(ids)
        return self.current(printers)
