"""Compatibility loader for the shared production composition engine."""
import importlib.util
from pathlib import Path
spec = importlib.util.spec_from_file_location("qiubot_comp_analysis", Path(__file__).resolve().parents[2]/"plugins/bazaar_plugin/comp_analysis.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Analysis, lower_bound = module.Analysis, module.lower_bound
