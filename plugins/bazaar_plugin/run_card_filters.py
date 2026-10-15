"""Validation and display names for per-card run filters."""

import json


ENCHANTMENTS = {
    'Deadly': '致命', 'Fiery': '炽焰', 'Golden': '黄金', 'Heavy': '沉重',
    'Icy': '寒冰', 'Obsidian': '黑曜石', 'Radiant': '辉耀',
    'Restorative': '回复', 'Shielded': '护盾', 'Shiny': '闪亮',
    'Toxic': '毒素', 'Turbo': '疾速', 'Mossy': '长青',
}


def normalize_run_card_filters(value):
    """Accept JSON or a list; a null enchantment means no enchantment constraint."""
    if value is None:
        return []
    if isinstance(value, str):
        if len(value) > 16384:
            raise ValueError('卡牌筛选参数过长')
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError('card_filters 必须是有效的 JSON 数组') from None
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('卡牌筛选必须是数组，最多支持 20 个条件')
    result = []
    seen = set()
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError('每个卡牌筛选条件必须包含卡牌名称')
        name = entry.get('name')
        enchantment = entry.get('enchantment')
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError('卡牌名称不能为空，且不能超过 160 个字符')
        if enchantment is not None and (
                not isinstance(enchantment, str) or enchantment not in ENCHANTMENTS):
            raise ValueError('未识别的附魔类型')
        name = name.strip()
        key = (name, enchantment)
        if key not in seen:
            seen.add(key)
            result.append({'name': name, 'enchantment': enchantment})
    return result
