"""楽天 日用品セール速報ボット

1. 楽天市場APIでキーワードごとに商品を取得
2. 価格履歴を保存し「過去30日の最高値から◯%値下がり」「ポイント高倍率」を判定
3. お得な商品をThreadsに自動投稿（PR表記つき）
4. まとめページ docs/index.html を生成（GitHub Pagesで公開）

環境変数:
  RAKUTEN_APP_ID, RAKUTEN_ACCESS_KEY, RAKUTEN_AFFILIATE_ID, SITE_ORIGIN  … 楽天API
  THREADS_TOKEN                                                        … Threads投稿
  DRY_RUN=1                                                            … 投稿せず内容だけ表示
"""
from __future__ import annotations

import html
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY = NOW.strftime("%Y-%m-%d")

CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
DRY_RUN = os.getenv("DRY_RUN") == "1"


def log(*a):
    print(*a, flush=True)


def load_json(name, default):
    p = DATA / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def save_json(name, obj):
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 楽天API
def rakuten_search(keyword: str) -> list[dict]:
    origin = os.environ["SITE_ORIGIN"].rstrip("/")
    f = CFG["filter"]
    params = {
        "applicationId": os.environ["RAKUTEN_APP_ID"],
        "accessKey": os.environ["RAKUTEN_ACCESS_KEY"],
        "affiliateId": os.environ["RAKUTEN_AFFILIATE_ID"],
        "format": "json",
        "formatVersion": 2,
        "keyword": keyword,
        "hits": 30,
        "sort": "-reviewCount",
        "availability": 1,
        "imageFlag": 1,
        "minPrice": f["min_price"],
        "maxPrice": f["max_price"],
    }
    if f.get("free_shipping_only"):
        params["postageFlag"] = 1
    headers = {"Origin": origin, "Referer": origin + "/", "User-Agent": "nichiyo-deals-bot/1.0"}
    for attempt in range(3):
        r = requests.get(CFG["rakuten"]["search_endpoint"], params=params, headers=headers, timeout=20)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code != 200:
            log(f"[楽天API] {keyword}: HTTP {r.status_code} {r.text[:300]}")
            if r.status_code in (400, 401, 403):
                raise SystemExit(f"楽天APIエラー（設定を確認）: {r.status_code} {r.text[:300]}")
            return []
        items = r.json().get("Items", [])
        return [i.get("Item", i) for i in items]
    return []


def clean_name(name: str, limit: int = 42) -> str:
    s = re.sub(r"[【\[［<＜(（][^】\]］>＞)）]{0,40}[】\]］>＞)）]", " ", name)
    s = re.sub(r"[★☆◆◇■□●○♪！!※]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:limit] + "…") if len(s) > limit else s


def passes_filter(it: dict) -> bool:
    f = CFG["filter"]
    if any(w in it.get("itemName", "") for w in f["ng_words"]):
        return False
    if int(it.get("reviewCount") or 0) < f["min_review_count"]:
        return False
    if float(it.get("reviewAverage") or 0) < f["min_review_avg"]:
        return False
    return bool(it.get("affiliateUrl") or it.get("itemUrl"))


# ---------------------------------------------------------------- 価格履歴・判定
def update_history(hist: dict, it: dict):
    code = it["itemCode"]
    price = int(it["itemPrice"])
    rec = hist.setdefault(code, {"h": []})
    rec["name"] = it["itemName"][:80]
    h = rec["h"]
    if h and h[-1][0] == TODAY:
        h[-1][1] = min(h[-1][1], price)
    else:
        h.append([TODAY, price])
    cutoff = (NOW - timedelta(days=90)).strftime("%Y-%m-%d")
    rec["h"] = [x for x in h if x[0] >= cutoff]


def prune_history(hist: dict):
    cutoff = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
    for code in [c for c, r in hist.items() if not r["h"] or r["h"][-1][0] < cutoff]:
        del hist[code]


def is_goods(it: dict) -> bool:
    """収納・雑貨系（消耗品ではない）かどうか"""
    return any(w in it.get("itemName", "") for w in CFG["deal"].get("goods_words", []))


def evaluate(it: dict, hist: dict) -> dict | None:
    d = CFG["deal"]
    price = int(it["itemPrice"])
    rec = hist.get(it["itemCode"], {"h": []})
    since = (NOW - timedelta(days=d["lookback_days"])).strftime("%Y-%m-%d")
    past = [p for day, p in rec["h"] if since <= day < TODAY]
    drop = 0.0
    ref = None
    if len(past) >= d["min_history_days"]:
        ref = max(past)
        drop = (ref - price) / ref if ref else 0
    point = int(it.get("pointRate") or 1)
    reasons = []
    if drop >= d["min_drop"]:
        reasons.append(f"直近{d['lookback_days']}日の最高値{ref:,}円から{round(drop * 100)}%ダウン")
    min_pt = d["min_point_rate"] if is_goods(it) else d.get("min_point_rate_consumable", d["min_point_rate"])
    if point >= min_pt:
        reasons.append(f"ポイント{point}倍")
    if not reasons:
        return None
    score = drop * 100 + (point - 1) * 3 + math.log10(int(it.get("reviewCount") or 1) + 1) * 2
    return {"item": it, "drop": drop, "ref": ref, "point": point, "reasons": reasons, "score": score}


def bootstrap_pick(it: dict) -> dict:
    """価格履歴がたまるまでの期間用：高評価の定番品"""
    point = int(it.get("pointRate") or 1)
    score = float(it.get("reviewAverage") or 0) * 10 + math.log10(int(it.get("reviewCount") or 1) + 1) * 5
    return {"item": it, "drop": 0, "ref": None, "point": point,
            "reasons": ["レビュー高評価の定番品"], "score": score}


# ---------------------------------------------------------------- 投稿文
def choose_tag() -> str:
    """その日の状況に合わせてトピックタグを自動で選ぶ"""
    t = CFG["tags"]
    for p in t.get("sale_periods") or []:
        if str(p["start"]) <= TODAY <= str(p["end"]):
            return p["tag"]
    if NOW.day == 1 and t.get("day_1"):
        return t["day_1"]
    if NOW.day % 5 == 0 and t.get("day_5_0"):
        return t["day_5_0"]
    normal = t["normal"]
    return normal[(NOW.timetuple().tm_yday * 4 + NOW.hour // 6) % len(normal)]


def compose(deal: dict) -> tuple[str, str]:
    """(本文, リプライ) を返す。リンクはリプライ側に入れる"""
    it = deal["item"]
    price = int(it["itemPrice"])
    url = it.get("affiliateUrl") or it["itemUrl"]
    head = f"{round(deal['drop'] * 100)}%OFF" if deal["drop"] >= CFG["deal"]["min_drop"] else (
        f"ポイント{deal['point']}倍" if any(r.startswith("ポイント") for r in deal["reasons"]) else "定番")
    lines = [
        "【PR】",
        f"🛒{head}｜{clean_name(it['itemName'])}",
        "",
        f"💴 {price:,}円（送料込）" + (f"＋ポイント{deal['point']}倍" if deal["point"] > 1 else ""),
        f"⭐ {float(it.get('reviewAverage') or 0):.2f}（レビュー{int(it.get('reviewCount') or 0):,}件）",
        f"📉 {' / '.join(deal['reasons'])}",
        f"🏪 {it.get('shopName', '')}",
        "",
        "👇商品ページはリプ欄に貼っています",
        f"#{choose_tag()}",
    ]
    text = "\n".join(lines)
    if len(text) > 500:  # Threadsの上限
        lines[1] = f"🛒{head}｜{clean_name(it['itemName'], 20)}"
        lines.pop(6)
        text = "\n".join(lines)
    reply = "\n".join([
        "【PR】楽天市場の商品ページはこちら👇",
        url,
        "※価格・ポイントは投稿時点。購入前にご確認ください",
    ])
    return text, reply


def image_url(it: dict) -> str | None:
    urls = it.get("mediumImageUrls") or []
    if not urls:
        return None
    u = urls[0]["imageUrl"] if isinstance(urls[0], dict) else urls[0]
    return re.sub(r"\?_ex=\d+x\d+", "?_ex=600x600", u)


# ---------------------------------------------------------------- Threads
THREADS = "https://graph.threads.net/v1.0"


def _publish(cid: str, token: str, wait: int) -> str:
    time.sleep(wait)
    for attempt in range(5):
        p = requests.post(f"{THREADS}/me/threads_publish",
                          data={"creation_id": cid, "access_token": token}, timeout=30)
        if p.status_code == 200:
            return p.json()["id"]
        log("[Threads] publish待機中", p.text[:200])
        time.sleep(10 * (attempt + 1))
    p.raise_for_status()
    return ""


def threads_post(text: str, img: str | None, reply: str | None = None) -> str:
    token = os.environ["THREADS_TOKEN"]
    data = {"text": text, "access_token": token}
    if img and CFG["post"]["use_image"]:
        data.update(media_type="IMAGE", image_url=img)
    else:
        data["media_type"] = "TEXT"
    r = requests.post(f"{THREADS}/me/threads", data=data, timeout=30)
    if r.status_code != 200 and data["media_type"] == "IMAGE":
        log("[Threads] 画像投稿に失敗→テキストで再試行", r.text[:200])
        data.pop("image_url")
        data["media_type"] = "TEXT"
        r = requests.post(f"{THREADS}/me/threads", data=data, timeout=30)
    r.raise_for_status()
    post_id = _publish(r.json()["id"], token, 15 if data["media_type"] == "IMAGE" else 3)

    if reply and post_id:  # リンクは自分のリプライに付ける
        rr = requests.post(f"{THREADS}/me/threads", data={
            "media_type": "TEXT", "text": reply, "reply_to_id": post_id, "access_token": token}, timeout=30)
        if rr.status_code == 200:
            rid = _publish(rr.json()["id"], token, 5)
            log(f"[Threads] リンク返信 id={rid}")
        else:  # 失敗時はActionsを赤くしてメール通知させる
            raise RuntimeError(f"リンク返信に失敗（threads_manage_repliesの権限を確認）: {rr.text[:300]}")
    return post_id


# ---------------------------------------------------------------- まとめページ
def build_site(deals: list[dict]):
    s = CFG["site"]
    cards = []
    for d in deals[: s["max_items"]]:
        it = d["item"]
        url = html.escape(it.get("affiliateUrl") or it["itemUrl"])
        img = html.escape(image_url(it) or "")
        cards.append(f"""
<a class="card" href="{url}" target="_blank" rel="nofollow sponsored noopener">
  <img src="{img}" alt="" loading="lazy">
  <div class="body">
    <div class="tag">{html.escape(' / '.join(d['reasons']))}</div>
    <div class="name">{html.escape(clean_name(it['itemName'], 60))}</div>
    <div class="price">{int(it['itemPrice']):,}円<span>送料込{'・P' + str(d['point']) + '倍' if d['point'] > 1 else ''}</span></div>
    <div class="rev">★{float(it.get('reviewAverage') or 0):.2f}（{int(it.get('reviewCount') or 0):,}件）</div>
  </div>
</a>""")
    page = f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(s['title'])}</title>
<style>
:root{{--bg:#faf7f5;--fg:#222;--sub:#777;--card:#fff;--accent:#bf0000;--line:#eee}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151515;--fg:#eee;--sub:#aaa;--card:#222;--line:#333}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif}}
header{{padding:20px 16px 8px;max-width:960px;margin:auto}}h1{{font-size:22px;margin:0 0 4px}}
.note{{font-size:12px;color:var(--sub);line-height:1.6}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;padding:12px 16px 40px;max-width:960px;margin:auto}}
.card{{display:flex;flex-direction:column;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;color:inherit;text-decoration:none}}
.card img{{width:100%;aspect-ratio:1;object-fit:contain;background:#fff}}
.body{{padding:10px 12px 12px}}.tag{{font-size:11px;color:var(--accent);font-weight:700;margin-bottom:4px}}
.name{{font-size:13px;line-height:1.45;min-height:3.8em}}.price{{font-size:18px;font-weight:700;margin-top:6px}}
.price span{{font-size:11px;font-weight:400;color:var(--sub);margin-left:6px}}.rev{{font-size:12px;color:var(--sub)}}
</style></head><body>
<header><h1>{html.escape(s['title'])}</h1>
<p class="note">【PR】当ページは楽天アフィリエイトを利用しています。価格・ポイントは {NOW.strftime('%Y/%m/%d %H:%M')} 時点のもので、変動する場合があります。購入前に商品ページでご確認ください。</p></header>
<main class="grid">{''.join(cards) or '<p class="note">現在セール情報を集計中です。</p>'}</main>
</body></html>"""
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")


# ---------------------------------------------------------------- main
def main():
    hist = load_json("prices.json", {})
    posted = load_json("posted.json", {})

    seen: dict[str, dict] = {}
    for kw in CFG["keywords"]:
        for it in rakuten_search(kw):
            if "itemCode" in it and passes_filter(it):
                it["_kw"] = kw
                seen.setdefault(it["itemCode"], it)
        time.sleep(CFG["rakuten"]["sleep_sec"])
    log(f"取得商品数: {len(seen)}")
    if not seen:
        raise SystemExit("商品が1件も取得できませんでした（API設定を確認）")

    # 判定は「今日の価格を記録する前」の履歴で行う
    deals = [d for it in seen.values() if (d := evaluate(it, hist))]
    for it in seen.values():
        update_history(hist, it)
    prune_history(hist)

    bootstrap = not deals
    if bootstrap:
        log("お得判定の商品なし→定番品モード（価格履歴が貯まるまで）")
        deals = [bootstrap_pick(it) for it in seen.values()]
    deals.sort(key=lambda d: d["score"], reverse=True)
    log(f"候補: {len(deals)}件")

    build_site(deals)
    save_json("latest_deals.json", [{
        "name": clean_name(d["item"]["itemName"], 30), "kw": d["item"].get("_kw", ""),
        "price": int(d["item"]["itemPrice"]), "point": d["point"],
        "drop": round(d["drop"] * 100), "reasons": d["reasons"]} for d in deals[:20]])

    block = (NOW - timedelta(days=CFG["deal"]["repost_block_days"])).strftime("%Y-%m-%d")
    def can_post(d):
        last = posted.get(d["item"]["itemCode"])
        if not last or last["d"] < block:
            return True
        # 7日以内でも「前回投稿時より5%以上安い」「ポイント倍率アップ」「前回が定番枠」なら再投稿OK
        return (not bootstrap) and (
            int(d["item"]["itemPrice"]) <= last["p"] * 0.95 or d["point"] > last["pt"] or last.get("boot"))

    queue = [d for d in deals if can_post(d)]
    # 定番品モードは1日1投稿まで（価値の低い投稿を連投しない）
    if bootstrap and any(v["d"] == TODAY for v in posted.values()):
        queue = []

    # 日用品（消耗品）と雑貨を交互に投稿する
    last = max(posted.values(), key=lambda v: v.get("t", v["d"]), default=None)
    want_consumable = not (last and last.get("g") is False)
    picks = []
    for _ in range(CFG["post"]["per_run"]):
        pool = [d for d in queue if d not in picks]
        pref = [d for d in pool if is_goods(d["item"]) != want_consumable]
        if pref or pool:
            picks.append((pref or pool)[0])
            want_consumable = is_goods(picks[-1]["item"])
    n = 0
    for d in picks:
        text, reply = compose(d)
        log("-" * 40 + "\n" + text + "\n--- リプライ ---\n" + reply + "\n" + "-" * 40)
        if DRY_RUN:
            log("[DRY_RUN] 投稿スキップ")
            continue
        pid = threads_post(text, image_url(d["item"]), reply)
        log(f"[Threads] 投稿完了 id={pid}")
        posted[d["item"]["itemCode"]] = {
            "d": TODAY, "t": NOW.isoformat(), "p": int(d["item"]["itemPrice"]), "pt": d["point"],
            "boot": bootstrap, "g": is_goods(d["item"])}
        n += 1

    old = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
    posted = {k: v for k, v in posted.items() if v["d"] >= old}
    save_json("prices.json", hist)
    save_json("posted.json", posted)
    log(f"完了: 投稿{n}件 / 履歴{len(hist)}商品")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # 失敗時はActionsを赤くしてメール通知させる
        log(f"エラー: {e!r}")
        sys.exit(1)
