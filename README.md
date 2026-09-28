# 楽天 日用品セール速報ボット — セットアップ手順

**仕組み**：GitHub Actions（無料）が1日4回、楽天APIで日用品の価格を取得 → 価格履歴から「本当に安い」商品を判定 → Threadsに自動投稿 → まとめページを更新。
PCの電源を切っていても動きます。費用は0円です。

```
楽天API ──▶ bot.py（GitHub Actions）──▶ Threads投稿
                 │
                 └──▶ まとめページ（GitHub Pages）← Threadsプロフィールのリンク
```

---

## 0. 用意するアカウント（すべて無料）

| # | アカウント | URL |
|---|---|---|
| 1 | 楽天会員＋楽天アフィリエイト | https://affiliate.rakuten.co.jp/ |
| 2 | GitHub | https://github.com/signup |
| 3 | Instagram（**このボット専用で新規作成**）→ Threads | Threadsアプリから開始 |
| 4 | Meta for Developers | https://developers.facebook.com/ |

> 個人のInstagram/Threadsは使わないでください。凍結リスクを本業に波及させないためです。

---

## 1. GitHubにアップロード（Terminal）

zipを「ダウンロード」フォルダに保存してから、順に実行します。

```bash
brew install gh
```

```bash
cd ~ && unzip -o ~/Downloads/nichiyo-deals.zip && cd ~/nichiyo-deals
```

```bash
gh auth login -w -p https
```

```bash
git init -b main && git add . && git commit -m "init" && gh repo create nichiyo-deals --public --source=. --push
```

```bash
gh api -X POST "repos/{owner}/{repo}/pages" -f "source[branch]=main" -f "source[path]=/docs"
```

まとめページのURLを確認します（後の手順で使います）。

```bash
echo "https://$(gh api user -q .login).github.io/nichiyo-deals/"
```

---

## 2. 楽天API（アプリID・アクセスキー）

1. https://webservice.rakuten.co.jp/ →「+アプリID発行」
2. 入力内容
   - アプリ名：`日用品セール速報`
   - **アプリケーションタイプ：Webアプリケーション**（「API/バックエンド」はIP制限でActionsから動きません）
   - **許可されたWebサイト：** `https://＜GitHubユーザー名＞.github.io`（末尾スラッシュなし）
   - 利用API：楽天市場API
   - 利用目的：`楽天市場の商品価格を集計し、セール情報をSNSとWebサイトで紹介するため`
   - 想定QPS：`1`
3. 発行された **アプリID** と **アクセスキー** を控える
4. 同じサイトの「アフィリエイトID」ページで **アフィリエイトID** を控える

---

## 3. Threadsのアクセストークン

1. 専用Instagramを作成 → Threadsアプリでそのアカウントのプロフィールを作成
2. https://developers.facebook.com/apps →「アプリを作成」→ ユースケース **「Threads APIにアクセス」**
3. ユースケース →「カスタマイズ」→ 権限に **threads_basic** と **threads_content_publish** を追加
4. アプリの「役割」→ **Threadsテスター** に専用アカウントを追加
5. Threadsアプリ（専用アカウント）→ 設定 → アカウント → **ウェブサイトのアクセス許可** → 招待を承認
6. ユースケースの設定画面 → **「ユーザートークン生成ツール」** でトークンを生成し、コピーする

> アプリは「開発モード」のままで問題ありません（自分のアカウントへの投稿のみのため、審査は不要です）。

---

## 4. トークン自動更新用のGitHubキー

Threadsのトークンは60日で切れます。月2回自動で延長するために必要です。

1. https://github.com/settings/personal-access-tokens/new
2. Token name：`threads-refresh`／Expiration：最長
3. Repository access：**Only select repositories → nichiyo-deals**
4. Permissions → Repository permissions → **Secrets：Read and write**
5. 生成されたキーをコピーする

---

## 5. 秘密情報を登録（Terminal）

1行ずつ実行し、表示される入力欄に値を貼り付けてEnterを押します。

```bash
cd ~/nichiyo-deals
```
```bash
gh secret set RAKUTEN_APP_ID
```
```bash
gh secret set RAKUTEN_ACCESS_KEY
```
```bash
gh secret set RAKUTEN_AFFILIATE_ID
```
```bash
gh secret set THREADS_TOKEN
```
```bash
gh secret set GH_PAT
```
```bash
echo "https://$(gh api user -q .login).github.io" | gh secret set SITE_ORIGIN
```

---

## 6. テスト → 本番

**テスト（投稿はせず、ログに投稿文を表示）**
```bash
gh workflow run run.yml -f dry_run=1 && sleep 8 && gh run watch
```
ログに「取得商品数」と投稿文が出れば成功です。

**本番1投稿**
```bash
gh workflow run run.yml && sleep 8 && gh run watch
```

**トークン更新の動作確認**
```bash
gh workflow run refresh_token.yml && sleep 8 && gh run watch
```

以後は **毎日 7:15 / 12:10 / 18:20 / 21:35（JST）** に自動で実行されます。
失敗するとGitHubから登録メールアドレスに通知が届きます。

---

## 7. 投稿ロジック

| 期間 | 動き |
|---|---|
| 最初の約3日 | 価格履歴を収集しながら、レビュー高評価の定番品を1日1投稿 |
| 4日目以降 | 「直近30日の最高値から10%以上ダウン」または「ポイント5倍以上」の商品だけを投稿 |
| 共通 | 同じ商品は7日間再投稿しない（さらに5%以上値下がりした場合は例外）／投稿文に【PR】を自動表記 |

調整は `config.yaml` だけで行えます（キーワード追加、値下がり率、1回あたりの投稿数など）。
変更後は次のコマンドで反映します。

```bash
cd ~/nichiyo-deals && git add -A && git commit -m "config" && git pull --rebase && git push
```

**楽天お買い物マラソン・スーパーセール期間**は `per_run: 2` にすると効果的です（終わったら1に戻します）。

---

## 8. 週1回・30分のルーティン

1. 楽天アフィリエイト → レポート：売れたジャンル・商品を確認
2. 売れたジャンルのキーワードを `config.yaml` に追加し、反応がないものは削除
3. Threadsのインサイトで、反応の多い投稿時間を確認
4. GitHub → Actions に赤い×（失敗）がないか確認

---

## トラブル時

| 症状 | 対処 |
|---|---|
| 楽天API `403` | 楽天の「許可されたWebサイト」と `SITE_ORIGIN` が完全一致しているか確認 |
| 楽天API `400 API Configuration not found` | APIのバージョン廃止です。https://webservice.rakuten.co.jp/documentation/ichiba-item-search で最新URLを確認し、`config.yaml` の `search_endpoint` を差し替え |
| Threads `190` / token expired | 手順3でトークンを再発行し、`gh secret set THREADS_TOKEN` |
| 商品が0件 | `config.yaml` の `filter` 条件を緩める |
