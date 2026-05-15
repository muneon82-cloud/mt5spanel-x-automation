# MT5SPanel X 投稿自動化

MT5/FX 関連の話題を混ぜながら、MT5SPanel を自然に宣伝する X 投稿自動化システムです。GitHub Actions で毎日実行し、OpenAI API で投稿文を生成し、X API v2 の `POST /2/tweets` で投稿します。

## できること

- 日本語の投稿を毎日自動生成
- 通常投稿 80%、宣伝投稿 20% の比率で運用
- 宣伝時だけ note / BOOTH の URL をランダム掲載
- 過去30投稿との類似、同じ冒頭表現、禁止語、語尾連続を検査
- 投稿履歴を `data/post_history.json` に保存
- Google News RSS から MT5/FX 周辺の最近話題候補を取得し、自然に反映

## ファイル構成

```text
.github/workflows/daily-post.yml  GitHub Actions
scripts/post_to_x.py              投稿生成・検査・X投稿
data/post_history.json            投稿履歴
.env.example                      ローカル設定テンプレート
requirements.txt                  Python依存関係
```

## GitHub Secrets

GitHub リポジトリの `Settings` -> `Secrets and variables` -> `Actions` に以下を登録してください。

```text
OPENAI_API_KEY
X_API_KEY
X_API_SECRET
X_ACCESS_TOKEN
X_ACCESS_TOKEN_SECRET
```

任意で `Variables` に `OPENAI_MODEL` を登録できます。未設定時は `gpt-5.1` を使います。

## X API 設定

1. [X Developer Portal](https://developer.x.com/) で Project / App を作成します。
2. App の権限を Read and write にします。
3. User authentication settings を有効にし、OAuth 1.0a のキーを使える状態にします。
4. App の `API Key` と `API Key Secret` を取得します。
5. 投稿する X アカウントの `Access Token` と `Access Token Secret` を発行します。
6. 4つの値を GitHub Actions secrets に登録します。

この実装は OAuth 1.0a user context で `https://api.x.com/2/tweets` に投稿します。X のプランやアプリ権限によって投稿 API が使えない場合は、X API 側で 401 / 403 / 429 が返ります。

## ローカル実行

`.env.example` を参考に `.env.local` を作成します。

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
$env:DRY_RUN="true"
python scripts/post_to_x.py
```

`DRY_RUN=true` の場合、X には投稿せず、生成文だけ表示します。履歴には保存しません。

## GitHub Actions

`.github/workflows/daily-post.yml` は毎日 `00:10 UTC` に実行されます。日本時間では `09:10` です。手動実行も可能です。

実行後、`data/post_history.json` を更新して自動コミットします。履歴がリポジトリに残るため、次回以降の重複回避に使えます。

## 重複回避と文体制御

`scripts/post_to_x.py` では生成後に以下を検査します。

- 過去30投稿との類似度
- 過去30投稿と同じ冒頭表現
- 禁止語: `高速`, `爆速`, `革命`, `次世代`, `圧倒的`, `最強`, `完全自動`, `誰でも簡単`
- 「できます」の複数回使用
- ハッシュタグの乱用
- 連続する文の語尾重複
- 通常投稿への URL 混入
- 宣伝投稿の URL 欠落

検査に落ちた場合は、最大5回まで OpenAI API に再生成を依頼します。

## 投稿タイプ

通常投稿では以下から選びます。

- MT5不便あるある
- スキャルピング話題
- 開発進捗
- UI改善
- EA高速化
- トレード環境
- 軽い雑談

宣伝投稿では以下 URL のどちらかを掲載します。

```text
https://note.com/rosy_carp7757/n/n6b24af9946f3
https://umapaka.booth.pm/items/8362889
```

## 参考

- OpenAI Responses API: [platform.openai.com/docs/api-reference/responses](https://platform.openai.com/docs/api-reference/responses)
- OpenAI SDK は環境変数 `OPENAI_API_KEY` を読み取れます: [platform.openai.com/docs/libraries](https://platform.openai.com/docs/libraries)
- X API create post: [docs.x.com/x-api/posts/create-post](https://docs.x.com/x-api/posts/create-post)
- X API OAuth 1.0a: [docs.x.com/fundamentals/authentication/oauth-1-0a/overview](https://docs.x.com/fundamentals/authentication/oauth-1-0a/overview)
