"""
生成玩家最近5天对局历史表格图。
每行一天：日期 / 局数 / 每局胜场方块（加分绿/扣分红，上行胜场，下行时间）/ 当日总分变化

色盲模式（colorblind=True）：绿/红 → 蓝/橙
"""
from __future__ import annotations

from pathlib import Path
from collections import defaultdict

_CHART_DIR = Path(__file__).resolve().parents[2] / "data" / "charts"


def _ensure_dir():
    _CHART_DIR.mkdir(parents=True, exist_ok=True)


def _wsl_to_win(path: Path) -> str:
    p = str(path.resolve())
    if p.startswith("/mnt/"):
        parts = p[5:].split("/", 1)
        drive = parts[0].upper()
        rest = parts[1].replace("/", "\\") if len(parts) > 1 else ""
        return f"{drive}:\\{rest}"
    return p


def _infer_wins(prev_r: int, delta: int) -> int:
    """Estimate S16+ wins using the same rating cap as the forward formula."""
    effective_rating = min(prev_r, 1080)
    if delta > 0:
        for bonus in (5, 0):
            if delta < bonus:
                continue
            w = round((delta-bonus)/5+effective_rating/100)
            if bonus == 5 and w >= 9:
                return 10
            if bonus == 0 and w < 10:
                return max(0, w)
        return min(10, max(0, round(delta/5+effective_rating/100)))
    if delta < 0:
        wins = round(effective_rating / 100 - abs(delta) ** 1.5 / 5)
        # A ten-win finish always gains rating, even above the cap.
        return min(9, max(0, wins))
    return 0


def parse_history(rh: list[dict], days: int = 5) -> list[dict]:
    """
    解析最近 N 天对局，返回列表，每项：
      { date, games: [{prev_r, cur_r, delta, wins, end_time}], day_delta }
    """
    if not rh:
        return []

    by_date: dict[str, list[dict]] = defaultdict(list)
    for p in rh:
        by_date[p["timestamp"][:10]].append(p)

    sorted_dates = sorted(by_date.keys())
    recent_dates = sorted_dates[-days:]

    result = []
    for date in recent_dates:
        pts = by_date[date]
        date_idx = sorted_dates.index(date)

        # 跨天：用前一天最后一条作起点
        prev_day_last = by_date[sorted_dates[date_idx - 1]][-1] if date_idx > 0 else None
        chain = ([prev_day_last] if prev_day_last else []) + pts

        games = []
        for i in range(1, len(chain)):
            prev_r = chain[i - 1]["rating"]
            cur_r  = chain[i]["rating"]
            if cur_r != prev_r:
                delta = cur_r - prev_r
                wins  = _infer_wins(prev_r, delta)
                end_time = chain[i]["timestamp"][11:16]
                games.append({
                    "prev_r":   prev_r,
                    "cur_r":    cur_r,
                    "delta":    delta,
                    "wins":     wins,
                    "end_time": end_time,
                })

        end_r   = pts[-1]["rating"]
        base_r  = prev_day_last["rating"] if prev_day_last else pts[0]["rating"]
        day_delta = end_r - base_r

        if games:  # 跳过无对战记录的天
            result.append({
                "date":      date,
                "games":     games,
                "day_delta": day_delta,
            })

    return result[-days:]  # 取最近有记录的 N 天


def generate_history_chart(username: str, rh: list[dict], colorblind: bool = False) -> str:
    """生成历史表格图，返回绝对路径。colorblind=True 启用色盲友好配色。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.patches import FancyBboxPatch

    plt.rcParams["font.family"] = "Noto Sans CJK SC"
    plt.rcParams["axes.unicode_minus"] = False

    history = parse_history(rh, days=5)
    if not history:
        return ""

    # ── 配色（白底）──
    BG     = "#ffffff"
    PANEL  = "#f5f6f8"
    BORDER = "#dde1e7"
    TEXT   = "#1a1d23"
    MUTED  = "#6e7681"
    BOX_TEXT = "#ffffff"   # 方块内文字

    if colorblind:
        # 色盲友好：蓝/橙/灰
        WIN_C  = "#0072B2"   # 加分：蓝
        LOSS_C = "#E69F00"   # 扣分：橙
        ZERO_C = "#999999"   # 平局：灰
        POS_C  = "#0072B2"   # 总分正
        NEG_C  = "#E69F00"   # 总分负
        cb_tag = "_cb"
    else:
        # 默认：绿/红
        WIN_C  = "#2e7d32"   # 加分：深绿
        LOSS_C = "#c62828"   # 扣分：深红
        ZERO_C = "#9e9e9e"   # 平局：灰
        POS_C  = "#2e7d32"   # 总分正
        NEG_C  = "#c62828"   # 总分负
        cb_tag = ""

    def box_color(delta: int) -> str:
        if delta > 0: return WIN_C
        if delta < 0: return LOSS_C
        return ZERO_C

    # ── 布局 ──
    n_rows  = len(history)
    ROW_H   = 1.4
    fig_h   = 1.2 + n_rows * ROW_H * 0.65
    fig, ax = plt.subplots(figsize=(12, fig_h))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    total_h = n_rows * ROW_H
    ax.set_xlim(0, 12)
    ax.set_ylim(0, total_h)
    ax.axis("off")

    # ── 标题 ──
    cb_hint = "  🎨色盲模式" if colorblind else ""
    fig.text(0.5, 0.97, f"{username}  最近对局记录{cb_hint}",
             ha="center", va="top", color=TEXT, fontsize=13, fontweight="bold")

    # ── 列标题 ──
    header_y = total_h - 0.08
    ax.text(0.3,  header_y, "日期",    color=MUTED, fontsize=9, va="top", ha="left")
    ax.text(1.65, header_y, "局数",    color=MUTED, fontsize=9, va="top", ha="center")
    ax.text(2.5,  header_y, "每局对局（胜场 / 分数变化 / 时间）",
            color=MUTED, fontsize=8.5, va="top", ha="left")
    ax.text(11.7, header_y, "当日总分", color=MUTED, fontsize=9, va="top", ha="right")
    ax.axhline(y=total_h - 0.28, color=BORDER, linewidth=0.8, xmin=0.01, xmax=0.99)

    # ── 方块参数 ──
    max_games = max((len(d["games"]) for d in history), default=1)
    max_show  = max(6, min(max_games, 20))
    BOX_AREA  = 11.4 - 2.5
    BOX_GAP   = max(0.03, 0.045 - max_show * 0.0005)
    BOX_W     = min(0.80, (BOX_AREA - BOX_GAP * (max_show - 1)) / max_show)
    BOX_H     = 0.90
    MAX_X     = 11.4

    font_wins  = max(4.5, min(7.5, BOX_W * 16))
    font_delta = max(4.0, min(7.0, BOX_W * 15))
    font_time  = max(3.5, min(6.5, BOX_W * 13))

    # ── 每行 ──
    for row_idx, day in enumerate(reversed(history)):
        row_y  = total_h - (row_idx + 1) * ROW_H
        cy     = row_y + ROW_H * 0.5

        # 交替行背景
        if row_idx % 2 == 0:
            bg = FancyBboxPatch((0.05, row_y + 0.07), 11.9, ROW_H - 0.12,
                                boxstyle="round,pad=0.02",
                                facecolor=PANEL, edgecolor="none", zorder=0)
            ax.add_patch(bg)

        # 日期
        ax.text(0.3, cy, day["date"][5:], color=TEXT, fontsize=10,
                va="center", ha="left", fontweight="bold")

        # 局数
        n_games = len(day["games"])
        ax.text(1.65, cy, str(n_games), color=TEXT, fontsize=10,
                va="center", ha="center", fontweight="bold")

        # 方块
        for gi, game in enumerate(day["games"]):
            bx = 2.5 + gi * (BOX_W + BOX_GAP)
            if bx + BOX_W > MAX_X:
                ax.text(bx + 0.02, cy, f"+{n_games - gi}局",
                        color=MUTED, fontsize=7, va="center", ha="left")
                break

            bc = box_color(game["delta"])
            rect = FancyBboxPatch((bx, row_y + 0.15), BOX_W, BOX_H,
                                  boxstyle="round,pad=0.04",
                                  facecolor=bc, edgecolor="none",
                                  alpha=0.92, zorder=1)
            ax.add_patch(rect)

            top_y = row_y + 0.15 + BOX_H * 0.72
            ax.text(bx + BOX_W / 2, top_y,
                    f"{game['wins']}胜",
                    color=BOX_TEXT, fontsize=font_wins, fontweight="bold",
                    va="center", ha="center", zorder=2)

            mid_y = row_y + 0.15 + BOX_H * 0.45
            d_str = (f"+{game['delta']}" if game['delta'] > 0 else str(game['delta']))
            ax.text(bx + BOX_W / 2, mid_y,
                    d_str,
                    color=BOX_TEXT, fontsize=font_delta, fontweight="bold",
                    va="center", ha="center", zorder=2)

            bot_y = row_y + 0.15 + BOX_H * 0.16
            ax.text(bx + BOX_W / 2, bot_y,
                    game["end_time"],
                    color=BOX_TEXT, fontsize=font_time, alpha=0.85,
                    va="center", ha="center", zorder=2)

        # 当日总分
        d = day["day_delta"]
        d_str   = f"{d:+d}" if d != 0 else "±0"
        d_color = POS_C if d > 0 else (NEG_C if d < 0 else MUTED)
        ax.text(11.7, cy, d_str, color=d_color, fontsize=10,
                va="center", ha="right", fontweight="bold")

        # 行分隔线
        if row_idx < n_rows - 1:
            ax.axhline(y=row_y + 0.07, color=BORDER, linewidth=0.5,
                       xmin=0.01, xmax=0.99)

    # ── 图例 ──
    patches = [
        mpatches.Patch(color=WIN_C,  label="加分局"),
        mpatches.Patch(color=LOSS_C, label="扣分局"),
        mpatches.Patch(color=ZERO_C, label="平局"),
    ]
    fig.legend(handles=patches,
               loc="lower center", bbox_to_anchor=(0.5, 0.0),
               ncol=3, fontsize=8,
               facecolor=PANEL, edgecolor=BORDER,
               labelcolor=TEXT, framealpha=0.9)

    plt.tight_layout(rect=[0, 0.06, 1, 0.93])

    _ensure_dir()
    out_path = _CHART_DIR / f"history_{username.lower()}{cb_tag}.png"
    fig.savefig(str(out_path), dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)

    return _wsl_to_win(out_path)
