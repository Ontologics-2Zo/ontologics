"""Pinned historical models for tests, never an application validator.

Read bundled test fixtures, independent of Git history or checkout depth.
Every source and relative validator dependency is hash checked before execution
in private modules; no sys.modules registration,
live common globals, source files, network or database connections are changed.
ROOT still identifies the current immutable fixture files, not historical data.
"""
import builtins
import hashlib
from importlib.util import resolve_name
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[2]
HISTORY_ROOT = Path(__file__).resolve().parent / 'fixtures' / 'model_history'
LEGACY_REVISION = '4539c904ca05f4529f8da099eff5e6be8c160c50'
MAIN_REVISION = 'ca7c391a9e813b6ab9e6fddfb48ab639c09a764b'
LEGACY_HASHES = {
    'model': '45476cd1f4a6f08691bb4143b6aab602bdb1b856dbecf053e61e0bfd094e0f58',
    'order_fulfillment.fulfillment_validation': '6a3f3ee2cf16df3785a45d18d3bf78ddb192c9ab2ad42566924520eb6bfb6a43',
    'order_fulfillment.production_validation': '4e21bbdcd8e31a0e87ccfeb779baba53d1df03bed25fbbeb17c42ce29b2ef2ce',
    'order_fulfillment.delivery_validation': '347118aafa1350b9a63dc5dbfc5be5b33b678b2ecc67f782eb21c8d8c8e0b06c',
}
MAIN_HASHES = {
    'model': '1dfc814b61433e048ea484dc361cc78d1fa2b0db57865393a895e9d24a2f69de',
    'service': 'f02c21f809e4d0e9c2d35e0bc6028c47db5964e26007525b7fa847d8dbd771f4',
    'host': '7443fd2e2bf0b3352d00e0c9c9e1eb2cdac41de0da174297adc68ba607d26340',
}


def _source(revision, module, hashes):
    expected = hashes[module]
    path = module.replace('.', '/') + '.py.txt'
    raw = (HISTORY_ROOT / revision / path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('Historical test source hash mismatch: ' + path)
    return raw.decode('utf-8')


def legacy_source():
    """Unmodified model text for AST extraction-parity checks."""
    return _source(LEGACY_REVISION, 'model', LEGACY_HASHES)


def main_source(module='model'):
    """Pinned common model/service/host text for boundary comparisons."""
    return _source(MAIN_REVISION, module, MAIN_HASHES)


def _load(revision, hashes):
    package = '_b_test_history'
    modules = {}

    def historical_import(name, globals=None, locals=None, fromlist=(), level=0):
        if not level:
            return builtins.__import__(name, globals, locals, fromlist, level)
        absolute = resolve_name('.' * level + name, globals['__package__'])
        prefix = package + '.'
        if not absolute.startswith(prefix) or absolute[len(prefix):] not in hashes:
            raise ImportError('Unpinned historical dependency: ' + absolute)
        return load(absolute[len(prefix):])

    def load(name):
        if name not in modules:
            source = _source(revision, name, hashes)
            module = ModuleType(package + '.' + name)
            module.__package__ = module.__name__.rpartition('.')[0]
            module.__file__ = str(ROOT / 'ontologics' / (name.replace('.', '/') + '.py'))
            module.__builtins__ = dict(vars(builtins), __import__=historical_import)
            modules[name] = module
            exec(compile(source, '<' + revision + ':' + name + '>', 'exec'), vars(module))
        return modules[name]

    return load('model')


def legacy_model():
    return _load(LEGACY_REVISION, LEGACY_HASHES)


def main_model():
    return _load(MAIN_REVISION, MAIN_HASHES)
