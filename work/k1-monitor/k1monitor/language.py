"""Application language, independent of printer settings and browser pairing."""
import ctypes
import json
import os
from pathlib import Path
import re
from .config import atomic_json

SUPPORTED = ('ru', 'en')

def windows_language():
    if os.name == 'nt':
        try:
            kernel = ctypes.WinDLL('kernel32')
            kernel.GetUserDefaultUILanguage.restype = ctypes.c_ushort
            return 'ru' if kernel.GetUserDefaultUILanguage() & 0x3ff == 0x19 else 'en'
        except (OSError, AttributeError):
            pass
    return 'en'

class LanguageSettings:
    def __init__(self, path):
        self.path = Path(path)
        self.language = windows_language()
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding='utf-8-sig')).get('language')
            if value in SUPPORTED:
                self.language = value
        else:
            self.save(self.language)

    def save(self, language):
        if language not in SUPPORTED:
            raise ValueError('Unsupported language')
        atomic_json(self.path, {'schema_version': 1, 'language': language})
        self.language = language

    def translate(self, message):
        return translate(message, self.language)

_catalog = None
_patterns = None

def catalog():
    global _catalog
    if _catalog is None:
        _catalog = json.loads((Path(__file__).parent/'static/locales/en.json').read_text(encoding='utf-8'))
    return _catalog

def translate(message, language):
    if language != 'en' or not isinstance(message, str):
        return message
    translations = catalog()
    if message in translations:
        return translations[message]
    global _patterns
    if _patterns is None:
        _patterns = []
        for key, target in translations.items():
            if re.search(r'\{\d+\}',key):
                parts=re.split(r'(\{\d+\})',key)
                slots=[int(part[1:-1]) for part in parts if re.fullmatch(r'\{\d+\}',part)]
                pattern=''.join('(.*?)' if re.fullmatch(r'\{\d+\}',part) else re.escape(part) for part in parts)
                _patterns.append((re.compile('^'+pattern+'$',re.S), slots, target))
        _patterns.sort(key=lambda item:len(item[0].pattern),reverse=True)
    for pattern, slots, target in _patterns:
        match=pattern.fullmatch(message)
        if match:
            values=dict(zip(slots,match.groups()))
            return re.sub(r'\{(\d+)\}',lambda token:values[int(token[1])],target)
    return message

def translate_markup(source, language):
    """Only static trusted templates. Never apply this to printer/user data."""
    if language != 'en':
        return source
    translations=catalog()
    keys=sorted((key for key in translations if re.search('[А-Яа-яЁё]',key) and not re.search(r'\{\d+\}',key)),key=len,reverse=True)
    return re.sub('|'.join(re.escape(key) for key in keys),lambda match:translations[match[0]],source)
