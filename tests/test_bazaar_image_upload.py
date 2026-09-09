from pathlib import Path
from types import ModuleType
import sys


def _load_plugin_module(monkeypatch):
    plugin_system = ModuleType("ncatbot.plugin_system")
    core_event = ModuleType("ncatbot.core.event")

    class NcatBotPlugin:
        pass

    class BaseMessageEvent:
        pass

    def on_message(func):
        return func

    plugin_system.NcatBotPlugin = NcatBotPlugin
    plugin_system.on_message = on_message
    core_event.BaseMessageEvent = BaseMessageEvent
    monkeypatch.setitem(sys.modules, "ncatbot", ModuleType("ncatbot"))
    monkeypatch.setitem(sys.modules, "ncatbot.plugin_system", plugin_system)
    monkeypatch.setitem(sys.modules, "ncatbot.core", ModuleType("ncatbot.core"))
    monkeypatch.setitem(sys.modules, "ncatbot.core.event", core_event)

    from plugins.bazaar_plugin import bazaar_plugin

    return bazaar_plugin


def test_image_upload_value_uses_base64_for_local_file(tmp_path, monkeypatch):
    bazaar_plugin = _load_plugin_module(monkeypatch)
    image = tmp_path / "history.png"
    image.write_bytes(b"\x89PNG\r\nminimal")

    value = bazaar_plugin._image_upload_value(str(image))

    assert value.startswith("base64://")
    assert value != str(image)


def test_image_upload_value_rejects_missing_file(tmp_path, monkeypatch):
    bazaar_plugin = _load_plugin_module(monkeypatch)
    missing = tmp_path / "missing.png"

    value = bazaar_plugin._image_upload_value(str(missing))

    assert value is None
