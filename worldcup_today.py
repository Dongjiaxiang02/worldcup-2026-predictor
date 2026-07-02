"""
⚽ 世界杯今日赛事通知 — 北京时间

数据来源: FOX Sports (实时抓取) + FIFA 2026年6月世界排名
预测模型: Elo评分 + 泊松分布

使用:
  python worldcup_today.py                    查看今天比赛
  python worldcup_today.py --date 2026-07-03  查看指定日期
  python worldcup_today.py --toast            弹出 Windows 通知
  python worldcup_today.py --watch            持续监控 (30分钟/次)
  python worldcup_today.py --watch -i 60      持续监控 (60分钟/次)
  python worldcup_today.py --offline          仅用本地数据，不联网
"""

import math
import sys
import os
import json
import re
import time
import argparse
from datetime import date, datetime, timedelta, timezone

# ================================================================
# 时区: 北京时间 UTC+8
# ================================================================
TZ_BEIJING = timezone(timedelta(hours=8))
TZ_ET      = timezone(timedelta(hours=-4))
CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "live_cache.json")
CACHE_TTL  = 600  # 缓存有效期: 10分钟


def bj_now() -> datetime:
    return datetime.now(TZ_BEIJING)


def bj_today_str() -> str:
    return bj_now().strftime("%Y-%m-%d")


def et_to_bj(et_str: str) -> tuple:
    """ET时间字符串 '2026-07-02 15:00' → 北京时间 (日期, 时间)"""
    et_dt = datetime.strptime(et_str, "%Y-%m-%d %H:%M")
    et_dt = et_dt.replace(tzinfo=TZ_ET)
    bj_dt = et_dt.astimezone(TZ_BEIJING)
    return bj_dt.strftime("%Y-%m-%d"), bj_dt.strftime("%H:%M")


# ================================================================
# 内置赛程 (ET时间) — 作为离线备份，官方来源: FIFA.com + FOX Sports
# ================================================================
# 格式: (ET日期+时间, 编号, 主队, 客队, 比分/None, 场地)
FALLBACK_SCHEDULE = [
    # 6月28日 (ET)
    ("2026-06-28 13:00", "M73", "南非", "加拿大", "0-1", "Los Angeles"),
    ("2026-06-28 20:00", "M74", "德国", "巴拉圭", "1-1 (点球3-4)", "Boston"),
    # 6月29日
    ("2026-06-29 13:00", "M75", "荷兰", "摩洛哥", "1-1 (点球2-3)", "Monterrey"),
    ("2026-06-29 16:00", "M76", "巴西", "日本", "2-1", "Houston"),
    ("2026-06-29 20:00", "M77", "科特迪瓦", "挪威", "1-2", "Dallas"),
    # 6月30日
    ("2026-06-30 13:00", "M78", "法国", "瑞典", "3-0", "New York New Jersey"),
    ("2026-06-30 16:00", "M79", "墨西哥", "厄瓜多尔", "2-0", "Mexico City"),
    ("2026-06-30 20:00", "M80", "英格兰", "民主刚果", "2-1", "Atlanta"),
    # 7月1日
    ("2026-07-01 16:00", "M81", "美国", "波黑", "2-0", "San Francisco"),
    ("2026-07-01 20:00", "M82", "比利时", "塞内加尔", "3-2", "Seattle"),
    # 7月2日 → 北京7月3日 03:00 / 07:00 / 11:00
    ("2026-07-02 15:00", "M83", "西班牙", "奥地利", None, "Los Angeles (SoFi)"),
    ("2026-07-02 19:00", "M84", "葡萄牙", "克罗地亚", None, "Toronto"),
    ("2026-07-02 23:00", "M85", "瑞士", "阿尔及利亚", None, "Vancouver"),
    # 7月3日 → 北京7月4日
    ("2026-07-03 18:00", "M86", "澳大利亚", "埃及", None, "Dallas"),
    ("2026-07-03 22:00", "M87", "阿根廷", "佛得角", None, "Miami"),
    ("2026-07-04 01:30", "M88", "哥伦比亚", "加纳", None, "Kansas City"),
]

# ================================================================
# FIFA 2026年6月官方世界排名
# ================================================================
FIFA_RANK = {
    "阿根廷": 1,   "西班牙": 2,   "法国": 3,    "英格兰": 4,
    "葡萄牙": 5,   "巴西": 6,     "摩洛哥": 7,  "荷兰": 8,
    "比利时": 9,   "德国": 10,    "克罗地亚": 11, "意大利": 12,
    "哥伦比亚": 13, "墨西哥": 14,  "塞内加尔": 15, "乌拉圭": 16,
    "美国": 17,    "日本": 18,    "瑞士": 19,   "伊朗": 20,
    "丹麦": 21,    "土耳其": 22,  "厄瓜多尔": 23, "奥地利": 24,
    "韩国": 25,    "尼日利亚": 26, "澳大利亚": 27, "阿尔及利亚": 28,
    "埃及": 29,    "加拿大": 30,  "挪威": 31,   "乌克兰": 32,
    "科特迪瓦": 33, "瑞典": 38,   "巴拉圭": 41, "塞尔维亚": 43,
    "喀麦隆": 44,  "突尼斯": 45,  "民主刚果": 46, "智利": 50,
    "南非": 60,    "波黑": 64,    "佛得角": 67, "加纳": 73,
    "新西兰": 85,
}


def to_elo(rank: int) -> int:
    return round(2170 - (rank - 1) * 4.2)


# ================================================================
# 实时数据抓取 (FOX Sports)
# ================================================================
FOX_URL = "https://www.foxsports.com/soccer/fifa-world-cup/schedule"


def _load_cache() -> dict:
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except:
        pass
    return {"ts": 0, "schedule": []}


def _save_cache(data: dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_schedule(force: bool = False) -> list | None:
    """
    从 FOX Sports 抓取最新赛程 & 比分。
    成功返回 [(et_dt_str, mid, home, away, score, venue), ...]
    失败返回 None
    """
    cache = _load_cache()

    # 缓存未过期 → 直接用
    if not force and time.time() - cache["ts"] < CACHE_TTL:
        sched = cache.get("schedule", [])
        if sched:
            return [tuple(x) for x in sched]

    import urllib.request

    try:
        req = urllib.request.Request(FOX_URL, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/125.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None

    # 从 HTML 中提取比赛数据
    # FOX Sports 页面结构: 每场比赛有 data-event-date, team names, scores
    matches = _parse_fox_html(html)
    if not matches:
        return None

    # 更新缓存
    cache["ts"] = time.time()
    cache["schedule"] = [list(m) for m in matches]
    _save_cache(cache)

    return matches


def _parse_fox_html(html: str) -> list:
    """解析 FOX Sports 赛程页面 HTML"""
    results = []

    # 方法1: 用正则提取 JSON-LD 结构化数据
    json_ld_pattern = re.compile(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        re.DOTALL
    )
    for match in json_ld_pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                items = [data]
            else:
                continue

            for item in items:
                if item.get("@type") != "SportsEvent":
                    continue
                home = item.get("homeTeam", {}).get("name", "")
                away = item.get("awayTeam", {}).get("name", "")
                if not home or not away:
                    continue
                date_str = item.get("startDate", "")
                venue = item.get("location", {}).get("name", "")
                # 解析日期
                try:
                    dt = datetime.fromisoformat(
                        date_str.replace("Z", "+00:00")
                    )
                    et_dt = dt.astimezone(TZ_ET)
                    et_str = et_dt.strftime("%Y-%m-%d %H:%M")
                except:
                    continue
                results.append((et_str, "", home, away, None, venue))
        except:
            continue

    if results:
        # 去重
        seen = set()
        unique = []
        for r in results:
            key = (r[0][:10], r[2], r[3])
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    # 方法2: 搜已完赛比分
    team_score_pattern = re.compile(
        r'([A-Za-z\s]+)\s+(\d+)\s*[-–—]\s*(\d+)\s+([A-Za-z\s]+)'
    )

    matches_found = []
    for m in team_score_pattern.finditer(html):
        t1, s1, s2, t2 = m.group(1).strip(), m.group(2), m.group(3), m.group(4).strip()
        # 过滤噪音
        if len(t1) < 3 or len(t2) < 3:
            continue
        matches_found.append((t1, s1, s2, t2))

    if matches_found:
        # 尝试匹配到已知场次
        for et_str, mid, home, away, _score, venue in FALLBACK_SCHEDULE:
            for t1, s1, s2, t2 in matches_found:
                if home.lower() in t1.lower() and away.lower() in t2.lower():
                    # 更新比分
                    idx = None
                    for i, r in enumerate(results):
                        if r[2] == home and r[3] == away:
                            idx = i
                            break
                    if idx is not None:
                        results[idx] = (et_str, mid, home, away, f"{s1}-{s2}", venue)
                    else:
                        results.append((et_str, mid, home, away, f"{s1}-{s2}", venue))

    return results


def merge_schedules(live: list | None, fallback: list) -> list:
    """
    合并实时数据和离线备份:
    - 以离线赛程结构为准
    - 实时数据覆盖: 比分、时间(如有变动)
    """
    if not live:
        return fallback

    # 构建实时数据索引: {(主队, 客队): (et_str, score)}
    live_map = {}
    for et_str, mid, home, away, score, venue in live:
        live_map[(home, away)] = (et_str, score)

    merged = []
    for et_str, mid, home, away, score, venue in fallback:
        key = (home, away)
        if key in live_map:
            new_et, new_score = live_map[key]
            # 优先用实时的时间(如果合理), 比分用非None的
            merged.append((
                new_et if new_et else et_str,
                mid, home, away,
                new_score or score,
                venue
            ))
        else:
            merged.append((et_str, mid, home, away, score, venue))

    return merged


# ================================================================
# Elo + Poisson 预测引擎
# ================================================================
LEAGUE_AVG = 1.38
MAX_G = 7


def poisson_pmf(k: int, lam: float) -> float:
    if k < 0 or lam <= 0:
        return 1.0 if k == 0 else 0.0
    log_p = -lam + k * math.log(lam)
    for i in range(2, k + 1):
        log_p -= math.log(i)
    return math.exp(log_p)


def predict(home: str, away: str) -> dict:
    elo_h = to_elo(FIFA_RANK.get(home, 100))
    elo_a = to_elo(FIFA_RANK.get(away, 100))
    rk_h  = FIFA_RANK.get(home)
    rk_a  = FIFA_RANK.get(away)

    elo_h_adj = elo_h + 32.5
    exp_h = 1.0 / (1.0 + 10.0 ** ((elo_a - elo_h_adj) / 400.0))
    exp_a = 1.0 - exp_h

    f_h = 0.55 + 0.9 * exp_h
    f_a = 0.55 + 0.9 * exp_a
    lam_h = max(0.3, min(LEAGUE_AVG * f_h, 4.5))
    lam_a = max(0.3, min(LEAGUE_AVG * f_a, 4.5))

    ph = [poisson_pmf(i, lam_h) for i in range(MAX_G + 1)]
    pa = [poisson_pmf(j, lam_a) for j in range(MAX_G + 1)]
    win = draw = 0.0
    for i in range(MAX_G + 1):
        for j in range(MAX_G + 1):
            p = ph[i] * pa[j]
            if i > j:      win += p
            elif i == j:   draw += p
    lose = max(0.0, 1.0 - win - draw)

    score_probs = {}
    for i in range(MAX_G + 1):
        for j in range(MAX_G + 1):
            score_probs[(i, j)] = ph[i] * pa[j]
    top3 = sorted(score_probs.items(), key=lambda x: x[1], reverse=True)[:3]

    wp, dp, lp = round(win * 100, 1), round(draw * 100, 1), round(lose * 100, 1)

    if wp > lp + 5:
        verdict = f"🏆 {home} 赢面更大"
    elif lp > wp + 5:
        verdict = f"🏆 {away} 赢面更大"
    elif dp > max(wp, lp):
        verdict = "🤝 平局可能最大, 或进加时"
    else:
        verdict = "⚖️ 势均力敌"

    return {
        "home": home, "away": away,
        "elo_h": elo_h, "elo_a": elo_a, "elo_diff": elo_h - elo_a,
        "rank_h": rk_h, "rank_a": rk_a,
        "wp": wp, "dp": dp, "lp": lp,
        "xg_h": round(lam_h, 2), "xg_a": round(lam_a, 2),
        "total_xg": round(lam_h + lam_a, 2),
        "top3": [(f"{s[0]}-{s[1]}", round(p * 100, 2)) for (s, p) in top3],
        "verdict": verdict,
    }


# ================================================================
# 数据查询
# ================================================================
def get_matches(target_date: str = None, offline: bool = False) -> tuple:
    """
    获取指定日期(北京时间)的比赛。
    返回: (matches_list, data_source_label)
    """
    if target_date is None:
        target_date = bj_today_str()

    # 尝试抓取实时数据
    schedule = FALLBACK_SCHEDULE
    source = "离线数据"

    if not offline:
        live = fetch_schedule()
        if live:
            schedule = merge_schedules(live, FALLBACK_SCHEDULE)
            source = "FOX Sports 实时"

    result = []
    for et_str, mid, home, away, score, venue in schedule:
        bj_date, bj_time = et_to_bj(et_str)
        if bj_date == target_date:
            result.append({
                "id": mid, "date": bj_date, "time": bj_time,
                "home": home, "away": away,
                "score": score, "venue": venue,
                "done": score is not None,
            })

    result.sort(key=lambda m: m["time"])
    return result, source


# ================================================================
# 终端彩色输出
# ================================================================
class C:
    R = "\033[91m"; G = "\033[92m"; Y = "\033[93m"
    B = "\033[94m"; M = "\033[95m"; C_ = "\033[96m"
    BOLD = "\033[1m"; DIM = "\033[2m"; RST = "\033[0m"


def clr(text: str, color: str) -> str:
    return f"{color}{text}{C.RST}"


def bar(label: str, pct: float, w: int = 30, color: str = C.G) -> str:
    n = int(round(pct / 100 * w))
    return f"  {label:<10} {color}{'█' * n}{'░' * (w - n)}{C.RST} {pct:>5.1f}%"


def render_match(m: dict, pred: dict = None) -> str:
    parts = []
    parts.append(f"  ┌{'─' * 56}┐")
    tag = "✅ 已完赛" if m["done"] else "🔮 即将开赛"
    parts.append(f"  │ {tag}  {m['id']}  {m['venue']}")
    parts.append(f"  │")
    parts.append(f"  │   {clr(m['home'], C.BOLD)}  vs  {clr(m['away'], C.BOLD)}")
    parts.append(f"  │   🕐 {m['time']} 北京时间")

    if m["done"]:
        parts.append(f"  │")
        parts.append(f"  │   比分: {clr(m['score'], C.BOLD)}")
        try:
            s = m["score"].replace("(点球", "").replace(")", "").strip().split("-")
            hs, aw = int(s[0]), int(s[1])
            if hs > aw:
                parts.append(f"  │   🏆 {clr(m['home'] + ' 获胜', C.G)}")
            elif aw > hs:
                parts.append(f"  │   🏆 {clr(m['away'] + ' 获胜', C.G)}")
        except:
            pass
        parts.append(f"  └{'─' * 56}┘")
        return "\n".join(parts)

    if pred is None:
        parts.append(f"  └{'─' * 56}┘")
        return "\n".join(parts)

    parts.append(f"  │   {'─' * 50}")
    parts.append(f"  │   📊 实力: FIFA #{pred['rank_h'] or '?'} vs #{pred['rank_a'] or '?'}"
                 f"  |  Elo {pred['elo_h']} vs {pred['elo_a']} ({pred['elo_diff']:+d})")
    parts.append(f"  │")
    parts.append(f"  │   📈 胜平负概率:")

    if pred["wp"] >= pred["lp"]:
        parts.append(bar(f"{m['home']} 获胜", pred["wp"], color=C.G))
        parts.append(bar("平局",       pred["dp"], color=C.Y))
        parts.append(bar(f"{m['away']} 获胜", pred["lp"], color=C.R))
    else:
        parts.append(bar(f"{m['home']} 获胜", pred["wp"], color=C.R))
        parts.append(bar("平局",       pred["dp"], color=C.Y))
        parts.append(bar(f"{m['away']} 获胜", pred["lp"], color=C.G))

    parts.append(f"  │")
    parts.append(f"  │   ⚡ xG: {m['home']} {pred['xg_h']} - {pred['xg_a']} {m['away']}"
                 f"  |  总进球≈{pred['total_xg']}")
    parts.append(f"  │")
    parts.append(f"  │   🎯 最可能比分:")
    for i, (sc, p) in enumerate(pred["top3"], 1):
        icon = "⭐" if i == 1 else "  "
        parts.append(f"  │   {icon} {sc}  ({p}%)")

    parts.append(f"  │")
    parts.append(f"  │   💡 {clr(pred['verdict'], C.G)}")
    parts.append(f"  └{'─' * 56}┘")
    return "\n".join(parts)


def render_report(target_date: str, matches: list, source: str) -> str:
    today = bj_today_str()
    is_today = target_date == today
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    wd = weekdays[dt.weekday()]
    title = "🏆 今日世界杯" if is_today else f"📅 {target_date}"

    out = []
    out.append(f"""
{clr('╔' + '═' * 58 + '╗', C.BOLD)}
{clr('║', C.BOLD)}  {title:<30} {wd:<22} {clr('║', C.BOLD)}
{clr('║', C.BOLD)}  🕐 北京时间 (UTC+8) · 📡 {source:<25} {clr('║', C.BOLD)}
{clr('╚' + '═' * 58 + '╝', C.BOLD)}""")

    if not matches:
        out.append(f"""
  {clr('📭 这天没有世界杯比赛', C.Y)}
  💡 试试: python worldcup_today.py --date 2026-07-03""")
        return "\n".join(out)

    done = [m for m in matches if m["done"]]
    todo = [m for m in matches if not m["done"]]

    if done:
        out.append(f"\n  {clr('══ 已完赛 ══', C.DIM)}")
        for m in done:
            out.append(render_match(m))

    if todo:
        if done:
            out.append(f"\n  {clr('══ 即将开赛 ══', C.BOLD)}")
        for m in todo:
            if m["home"] == "待定" or m["away"] == "待定":
                out.append(render_match(m))
            else:
                pred = predict(m["home"], m["away"])
                out.append(render_match(m, pred))

    out.append(f"""
{clr('╔' + '═' * 58 + '╗', C.DIM)}
{clr('║', C.DIM)}  ⚠️ 预测仅供参考, 实际受临场因素影响                {clr('║', C.DIM)}
{clr('║', C.DIM)}  📊 FIFA排名(2026/06) + Elo评分 + 泊松分布            {clr('║', C.DIM)}
{clr('╚' + '═' * 58 + '╝', C.DIM)}""")

    return "\n".join(out)


# ================================================================
# Windows 通知
# ================================================================
def toast(title: str, msg: str) -> bool:
    t = title.replace("'", "''")
    m = msg.replace("'", "''").replace("\n", " | ")
    ps = (
        f"[Windows.UI.Notifications.ToastNotificationManager, "
        f"Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        f"$t = [Windows.UI.Notifications.ToastNotificationManager]"
        f"::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        f"$n = $t.GetElementsByTagName('text'); "
        f"$n.Item(0).AppendChild($t.CreateTextNode('{t}')) > $null; "
        f"$n.Item(1).AppendChild($t.CreateTextNode('{m}')) > $null; "
        f"$toast = [Windows.UI.Notifications.ToastNotification]::new($t); "
        f"[Windows.UI.Notifications.ToastNotificationManager]"
        f"::CreateToastNotifier('WorldCup2026').Show($toast)"
    )
    try:
        os.system(f'powershell -NoProfile -Command "{ps}"')
        return True
    except:
        return False


# ================================================================
# 主程序
# ================================================================
def main():
    parser = argparse.ArgumentParser(
        description="⚽ 世界杯今日赛事 — 北京时间 · 实时数据抓取 · 智能预测")
    parser.add_argument("-d", "--date", default=None,
                        help="日期 YYYY-MM-DD (默认今天北京时间)")
    parser.add_argument("-t", "--toast", action="store_true",
                        help="弹出 Windows 桌面通知")
    parser.add_argument("-w", "--watch", action="store_true",
                        help="持续监控")
    parser.add_argument("-i", "--interval", type=int, default=30,
                        help="监控间隔(分钟), 默认30")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式，不联网抓取")
    parser.add_argument("--refresh", action="store_true",
                        help="强制刷新缓存，立即抓取最新数据")
    args = parser.parse_args()

    # 强制刷新
    if args.refresh:
        fetch_schedule(force=True)
        print("  ✅ 已从 FOX Sports 拉取最新数据\n")

    if args.watch:
        _watch(args)
        return

    _once(args)


def _once(args):
    target = args.date or bj_today_str()
    matches, source = get_matches(target, offline=args.offline)
    print(render_report(target, matches, source))

    if args.toast:
        for m in matches:
            if m["done"] or m["home"] == "待定":
                continue
            p = predict(m["home"], m["away"])
            toast(f"⚽ {m['home']} vs {m['away']}",
                  f"{m['time']} 北京时间 | {p['verdict']}\n"
                  f"胜{p['wp']}% 平{p['dp']}% 负{p['lp']}% | "
                  f"最可能 {p['top3'][0][0]}")


def _watch(args):
    interval = max(1, args.interval)
    print(f"\n  🔄 持续监控 (每{interval}分钟) · 北京时间 · 自动拉取实时数据")
    print(f"  ⌨️  Ctrl+C 退出\n")
    last_date = None
    try:
        while True:
            now = bj_now()
            today = now.strftime("%Y-%m-%d")
            if last_date and last_date != today:
                print(f"\n{clr('━' * 60, C.BOLD)}")
                print(f"  {clr('📅 ' + today, C.BOLD)}")
                print(f"{clr('━' * 60, C.BOLD)}\n")

            print(f"  [{now.strftime('%H:%M:%S')}] 刷新...")
            # watch模式总是尝试拉取最新
            matches, source = get_matches(today, offline=args.offline)
            print(render_report(today, matches, source))

            if args.toast:
                for m in matches:
                    if m["done"] or m["home"] == "待定":
                        continue
                    p = predict(m["home"], m["away"])
                    toast(f"⚽ {m['home']} vs {m['away']}",
                          f"{m['time']} | {p['verdict']} | {p['top3'][0][0]}")

            last_date = today
            nxt = now + timedelta(minutes=interval)
            print(f"\n  ⏰ 下次: {nxt.strftime('%H:%M:%S')}")
            time.sleep(interval * 60)
    except KeyboardInterrupt:
        print(f"\n  👋 再见!\n")


if __name__ == "__main__":
    main()
