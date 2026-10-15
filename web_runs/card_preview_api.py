"""Independent card preview HTTP boundary, with no database writes or upstream requests."""
import hashlib
import json
import threading
from uuid import UUID

from flask import Blueprint, current_app, jsonify, request


def create_card_preview_blueprint(repository=None, data_label="当前游戏数据"):
    blueprint = Blueprint("card_preview", __name__)
    lock = threading.Lock()

    @blueprint.get("/api/card-preview/<card_id>")
    def preview(card_id):
        nonlocal repository
        try:
            card_id = str(UUID(card_id))
        except ValueError:
            return jsonify(error="无效的卡牌 ID"), 400
        if any(len(request.args.get(key, "")) > 40 for key in ("quality", "enchantment", "lang")):
            return jsonify(error="无效的预览参数"), 400
        try:
            if repository is None:
                with lock:
                    if repository is None:
                        from plugins.bazaar_plugin.card_preview import CardPreviewRepository
                        repository = CardPreviewRepository()
            result = repository.get(card_id, request.args.get("quality", ""),
                                    request.args.get("enchantment", ""), request.args.get("lang", "zh-CN"))
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except Exception:
            current_app.logger.exception("Card preview unavailable")
            return jsonify(error="卡牌详情暂不可用，请稍后重试"), 503
        result = dict(result, dataLabel=data_label)
        response = jsonify(result)
        response.set_etag(hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        response.cache_control.public = True
        response.cache_control.max_age = 60
        return response.make_conditional(request)

    return blueprint
