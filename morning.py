"""毎朝のリプライ素材づくり

Threads APIで他人の投稿を検索するにはMetaの審査（App Review）が必要なため、
「検索リンク」と「その日の情報を入れたリプライ文の下書き」をまとめて届ける。
実際のリプライは手動で行う（自動リプライは凍結リスクが高いため）。

出力: morning.md（GitHub Actionsがこれを Issue にしてメール通知する）
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent
JST = timezone(timedelta(hours=9))
NOW = datetime.now(JST)
TODAY = NOW.strftime("%Y-%m-%d")
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
M = CFG.get("morning", {})
rnd = random.Random(TODAY)  # 同じ日は同じ内容、日ごとに変わる


def load_deals() -> list[dict]:
    p = ROOT / "data" / "latest_deals.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def today_event() -> str | None:
    for p in CFG["tags"].get("sale_periods") or []:
        if str(p["start"]) <= TODAY <= str(p["end"]):
            return p["tag"]
    if NOW.day == 1:
        return "楽天ワンダフルデー"
    if NOW.day % 5 == 0:
        return "5と0のつく日"
    return None


SEASON = {
    1: "年末年始でストックが減りがちな時期", 2: "花粉対策グッズが必要になってくる時期",
    3: "新生活の準備で日用品をそろえる時期", 4: "新生活でまとめ買いが増える時期",
    5: "梅雨前にカビ対策グッズをそろえたい時期", 6: "梅雨で除湿剤や洗剤の出番が増える時期",
    7: "暑さで飲み物や汗対策グッズが減りやすい時期", 8: "麦茶や水のストックがすぐなくなる時期",
    9: "夏の疲れで家事をラクにしたい時期", 10: "衣替えで洗剤や収納グッズを見直す時期",
    11: "冬支度でティッシュやマスクを買い足す時期", 12: "年末の大掃除で洗剤類が減る時期",
}


def label(d: dict) -> str:
    return (d.get("kw") or "日用品").split()[0]


def drafts(deals: list[dict], event: str | None) -> list[tuple[str, str]]:
    """(使いどころ, 下書き) のリスト。リンクや宣伝は入れない"""
    out = []
    season = SEASON[NOW.month]
    top = deals[0] if deals else None
    pts = [d for d in deals if d["point"] >= 5]
    drops = [d for d in deals if d["drop"] >= 10]

    if event == "5と0のつく日":
        out.append(("楽天で買い物した・する人の投稿に",
                    "今日5と0のつく日なので、楽天カード払いだとポイント上がりますよ〜！日用品のまとめ買いするなら今日がおすすめです✨"))
    elif event == "楽天ワンダフルデー":
        out.append(("楽天で買い物した・する人の投稿に",
                    "今日はワンダフルデーなので、エントリーしてから買うとポイントちょっと上がりますよ〜！忘れがちなので😊"))
    elif event:
        out.append(("セール・買い回りの投稿に",
                    f"{event}、日用品で店舗数を稼ぐのいいですよね！ティッシュや洗剤みたいな「どうせ買うもの」なら無駄買いにならないのでおすすめです✨"))

    if pts:
        d = rnd.choice(pts)
        out.append((f"「{label(d)}」の話題に",
                    f"ちょうど今、楽天で{label(d)}がポイント{d['point']}倍になってるの見かけました！ストック買いするなら今かもです👀"))
    if drops:
        d = rnd.choice(drops)
        out.append((f"「{label(d)}」の値上がりの話題に",
                    f"わかります…！でも今{label(d)}、楽天で最近の中ではかなり安くなってました（{d['drop']}%くらい下がってた）。価格ってほんと日によって違いますよね😳"))
    if top:
        out.append(("ポイ活・節約の報告投稿に",
                    f"すごい参考になります🙌 自分は日用品を「ポイント倍率が高い日だけ買う」ってルールにしてから、地味に節約できてます！"))

    pool = [
        ("値上げを嘆く投稿に", "ほんと最近なんでも高いですよね😢 洗剤とかは詰め替えの大容量が、1回あたりで見るとかなりお得なこと多いです！"),
        ("まとめ買いの投稿に", "まとめ買い気持ちいいですよね🧻 自分は置き場所に困らない分だけ、ポイント高い日に買うようにしてます〜"),
        ("家事・掃除の投稿に", f"{season}ですよね！自分も洗剤類のストック見直してました〜"),
        ("買ってよかった投稿に", "それ気になってました！レビュー件数多いやつは外れが少ない気がします👀"),
        ("楽天の買い物報告に", "いいですね✨ 楽天は同じ商品でもショップによって送料込みかどうかが違うので、比べると結構差が出ますよね！"),
        ("節約初心者の投稿に", "最初は日用品から見直すのが一番ラクで効果ある気がします！毎月必ず買うものなので😊"),
        ("ティッシュ・トイレットペーパーの投稿に", "ティッシュとトイペは切れると困るので、安い時にストックしちゃいます🧻笑"),
        ("水・飲み物の投稿に", "水は重いので通販一択です…！ケース買いだと1本あたりかなり安くなりますよね💧"),
    ]
    rnd.shuffle(pool)
    out += pool
    return out[: M.get("drafts", 8)]


def search_links() -> list[tuple[str, str]]:
    kws = list(M.get("keywords", ["楽天 日用品", "ポイ活", "節約 主婦", "まとめ買い"]))
    rnd.shuffle(kws)
    return [(k, f"https://www.threads.com/search?q={quote(k)}&serp_type=default") for k in kws[: M.get("links", 5)]]


def main():
    deals = load_deals()
    event = today_event()
    lines = [
        f"## ☀️ {NOW.strftime('%-m/%-d')}（{'月火水木金土日'[NOW.weekday()]}）のリプ素材",
        "",
        f"**今日の楽天**：{event or '通常日'}　／　**季節**：{SEASON[NOW.month]}",
        "",
        "### ① 検索リンク（開いて「最新」タブで、日本語の投稿を探す）",
    ]
    for k, u in search_links():
        lines.append(f"- [{k}]({u})")
    lines += [
        "",
        "狙い目：フォロワー数百〜数千人の個人アカウント／投稿から数時間以内／まだリプが少ない投稿",
        "",
        "### ② リプライ下書き（そのまま使わず、相手の投稿に合わせて一言変える）",
    ]
    for i, (when, text) in enumerate(drafts(deals, event), 1):
        lines += [f"**{i}. {when}**", "```", text, "```"]
    lines += [
        "",
        "### ルール",
        "- 1日10〜20件まで／同じ文章を連投しない",
        "- リンク・アカウント宣伝は書かない（プロフを見てもらえればOK）",
    ]
    (ROOT / "morning.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
