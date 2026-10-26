"""Compatibility loader for the shared production composition engine."""
import importlib.util
from pathlib import Path
import sys
import types
root = Path(__file__).resolve().parents[2]
for name, directory in [('plugins', root/'plugins'), ('plugins.bazaar_plugin', root/'plugins/bazaar_plugin')]:
    if name not in sys.modules:
        package = types.ModuleType(name); package.__path__ = [str(directory)]; sys.modules[name] = package
spec = importlib.util.spec_from_file_location("plugins.bazaar_plugin.comp_analysis", root/"plugins/bazaar_plugin/comp_analysis.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Analysis, lower_bound = module.Analysis, module.lower_bound
