"""Run composition training without initializing the QQ plugin runtime."""
from pathlib import Path
import runpy
import sys
from types import ModuleType


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    for name, directory in (('plugins', root / 'plugins'),
                            ('plugins.bazaar_plugin', root / 'plugins/bazaar_plugin')):
        package = ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    runpy.run_module('plugins.bazaar_plugin.comp_family_train', run_name='__main__')


if __name__ == '__main__':
    main()
