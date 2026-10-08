# AtCoder 初AC管理 Discord Bot

AtCoder Problems APIだけを履歴ソースとし、問題ごとの生涯初ACをSQLiteに保存する小規模Botです。AHCは除外します。取得と公開投稿は別管理です。**コマンド実行時だけ取得・投稿し、自動同期や自動投稿は行いません。**

## コマンド

| コマンド | 用途・例 |
|---|---|
| `/ac register atcoder_id:alice` | Discordの数値IDとAtCoder IDをサーバー単位で登録。全提出を取得して初ACを保存し、直近50ACを実行者だけに表示します。開催中・開催情報不明の問題は表示を保留します。変更には120秒以内のボタン確認が必要です。同一サーバーでIDの重複登録は拒否します。 |
| `/ac update` | 48時間の重複を含む差分取得。新規初ACは全件保存し、投稿可能な直近50問までを古い日時順・JSTの日付別で精進チャンネルに1回だけ投稿します。 |
| `/ac stats` | 自分の直近7日の日別初AC数・期間内累計・生涯Difficulty分布を、3枚の独立したPNGで表示。 |
| `/ac stats period:monthly` | 自分の直近30日を日別表示。ID入力は不要です。 |
| `/ac stats period:all aggregation:daily` | 自分の全期間を日別表示。ACが0件の日も含めます。 |
| `/ac stats period:all aggregation:weekly` | 全期間を月曜始まりの週別表示。allの初期値は月別（aggregation:monthly）。 |
| `/ac stats_id atcoder_id:alice period:all aggregation:monthly` | 指定したAtCoder IDの統計を表示。IDは必須で、period・aggregationはstatsと共通。 |
| `/ac help` | 上記5コマンドの日本語ヘルプ。 |

`/ac stats`は自分専用、`/ac stats_id`はAtCoder ID指定用です。以前の`/ac stats atcoder_id:...`は`/ac stats_id atcoder_id:...`へ変更してください。Total Effortは選択期間内の累計初AC数を**常に日別**で表示し、期間の開始は0です。全期間の棒グラフは`aggregation=daily/weekly/monthly`で日別・週別・月別を選べます（初期値monthly）。直近7日・30日の棒グラフは常に日別です。棒と累計の横軸はそれぞれの集計単位で独立しています。

Difficulty分布はstats/stats_idに統合しました。単独の`/ac diffhist`は廃止しています。分布は従来どおり**生涯初AC**を100刻みで集計し、Difficulty不明を除外して件数を表示します。periodは棒と累計の期間を指定します。1回の履歴取得から3枚を生成し、1回のDiscord応答に添付します。全グラフの縦軸は`AC count`です。ヒストグラムの下余白と上端の余白を調整し、右側に1bin分（Difficulty 100）の余白を設けています。長いIDのタイトルは2行にして画像内に収めています。

registerの応答とupdateの処理結果は実行者だけに表示します。registerの直近50ACは古い日時順・日付別に表示し、Discordの文字数上限を超える場合は古い問題から省略します。同じIDで再実行すると保存済み履歴から再表示します。過去ACは精進チャンネルへ投稿せず、updateの投稿候補にも追加しません。updateの公開投稿先は常に`TARGET_CHANNEL_ID`です。他のチャンネルから実行しても実行場所へは投稿しません。投稿先が同じサーバーに属し、実行者が閲覧可能であることと、Botの閲覧・送信・Embedリンク権限を検証します。stats・stats_id・helpは公開応答です。統計の応答文は選択期間の件数だけ（例: `AC 675`）です。DMでは使用できません。

所有者の厳密な確認は行いません。登録成功は本人確認を意味しません。提出が0件の応答では、ユーザーが存在しないのか履歴がないのかも判別できません。

## 調査結果と旧Botからの変更

EffortのDifficulty不明分は網掛けせず、既存のDifficulty色と異なる薄い紫で表示します。Total Effortの縦軸は0から始まり、Matplotlib標準の整数目盛りを使用します。8ACでは0・2・4・6・8を表示します。

調査開始時のGit HEADは`2bbfa12`、作業ツリーはクリーンでした。既存の全プロジェクトファイル（`atcoder_bot.py`、`requirements.txt`、`README.md`、`.github/workflows/deploy.yml`）を確認しました。サービス定義・既存DB・永続ディスクのマウント設定はリポジトリにありません。

旧READMEによる環境はGoogle CloudのUbuntu VM、systemdの`discord-bot.service`、Pythonの`venv`です。旧READMEの配置先は`/home/scoalpha5/atcoder-bot`、実際のデプロイ設定の配置先は`~/atcoder-stats-bot`で異なっています。正しいパス・Pythonバージョン・実行ユーザー・環境変数の設定場所は、導入前にVMのサービス定義で確認してください。Cloud Runなどへの移行は行いません。

旧`/atcoder`のメッセージ履歴解析、トップレベル`/diffhist`と`/update_diff`、起動時取得・24時間周期取得を廃止し、`/ac`グループに統一しました。メッセージ内容の特権Intentは不要です。Difficulty補正式・色・薄い背景付きヒストグラムを再利用しました。AC数と累計は別Figureで描き、TEE等の時間推定は使いません。

**旧GitHub Actionsはmainへのpushで本番更新・再起動をしていました。本改修では`workflow_dispatch`のみの手動実行に変更しています。変更がmainに反映される前は、既存workflowがまだpushで動きます。最初の反映前にGitHub側でDeploy Bot workflowを無効化し、承認後にのみ有効化・実行してください。** 本作業ではpush・デプロイ・本番再起動を行っていません。

## ローカルセットアップ

Python **3.11以上**を使用します（開発検証: Python 3.14）。本番では既存VMのPythonを確認し、対応した仮想環境で先に依存解決とテストを行ってください。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
MPLCONFIGDIR=/tmp/atcoder-bot-mpl .venv/bin/python -m pytest -q
```

本番では`requirements.txt`だけをインストールできます。バージョンには互換範囲を設定しています。新しい依存版を採用する際もテストを実行してください。

`.env.example`を参考に、環境変数を設定します。`.env`ファイルは自動では読み込みません。systemdの`EnvironmentFile`か実行シェルで設定してください。トークン・DB・バックアップはGitに含めません。

| 環境変数 | 設定 |
|---|---|
| `DISCORD_TOKEN` | 必須。既存の環境変数名を維持。コードやログに出力しません。 |
| `TARGET_CHANNEL_ID` | 公開投稿先の数値チャンネルID。未設定ではupdateを拒否。既存名を維持。 |
| `DATABASE_PATH` | SQLite保存先。開発時初期値`data/ac.sqlite3`。本番では永続ディスク上の絶対パスを必ず指定。 |
| `STATS_CACHE_SECONDS` | 未登録IDを直接指定した統計のキャッシュ有効秒数。初期値3600、正の整数。 |
| `LOG_LEVEL` | 初期値INFO。 |

起動入口は従来どおりです。

```bash
.venv/bin/python atcoder_bot.py
```

Discord側では`bot`と`applications.commands`のスコープ、精進チャンネルの閲覧・メッセージ送信・Embed Links、統計表示先でのAttach Files権限を付与します。起動時にスラッシュコマンドを同期します。旧コマンドの消失、新コマンド反映にはDiscord側の遅延があり得ます。

## 構成とSQLite

| ファイル | 責務 |
|---|---|
| `atcoder_bot.py` | Discordコマンド、確認ボタン、応答と送信先の検証、起動・終了 |
| `atcoder_api.py` | 共通レート制限、ページネーション、リトライ、入力／応答検証 |
| `database.py` | 非破壊的スキーマ初期化、トランザクション、初ACと永続投稿キュー |
| `service.py` | 登録・差分更新・キャッシュ・開催中保留・送信再試行 |
| `ac_statistics.py` | JSTでの期間／週／月集計、Difficulty分布の共通処理 |
| `formatting.py` | Difficulty補正、配色、安全な問題表示名、リンクとEmbedサイズ調整 |
| `plotting.py` | Aggでの独立PNG生成。描画をスレッド間で直列化 |
| `scripts/backup_db.py` | SQLite Online Backup APIによる整合性のあるバックアップ |
| `tests/` | API・Discordをモックした回帰テスト |

標準ライブラリ`statistics`との衝突を避け、集計モジュール名を`ac_statistics.py`にしています。

DBのスキーマバージョンは`PRAGMA user_version=2`です。旧版1からは、バッチの`superseded_at`列と対応する一意インデックスをトランザクションで追加・変更します。既存履歴・登録・送信待ちデータは保持します。

- `ac_registrations`: `(guild_id, discord_id)`が主キー、`(guild_id, atcoder_id)`も一意。AtCoder IDは小文字に正規化。登録ごとのgeneration IDと登録日時を保持。
- `ac_accounts`: AtCoder ID単位の差分カーソル・同期成功日時・初回完了状態。
- `ac_firsts`: `(atcoder_id, problem_id)`が主キー。contest、提出ID、初AC秒を保存し、より古いACが判明すれば更新。
- `ac_deliveries`: 登録generationごとの各問題の状態。`baseline`=登録時過去履歴、`pending`=新規、`held`=開催中／不明保留、`excluded`=上限で省略、`queued`=送信待ち、`posted`=送信済み。
- `ac_batches`: 1メッセージ分の確定Embed本文、送信状態、DiscordメッセージID・送信日時。`superseded_at`があるバッチは置き換え済みで再送対象外です。過去の本文も保持し、各generationの有効な送信待ちは最大1バッチです。
- `ac_resources`: 履歴と独立したDifficulty・問題・コンテスト情報のJSONキャッシュと取得日時。

登録変更時も旧初AC履歴や旧投稿管理行を削除しません。旧generationのキューは新登録では送信しません。同じAtCoder IDを複数サーバーで扱う場合、履歴は共有、投稿状態は別管理です。

SQLiteはWAL、外部キー、30秒のbusy timeoutを使用し、`BEGIN IMMEDIATE`で変更を原子的に確定します。既存の未知のスキーマ版や衝突テーブルは拒否します。DBの削除や上書き移行は行いません。既存DBと衝突する場合は新規パスを指定してください。

## 同期・投稿の保証と制約

[AtCoder Problems公式API文書](https://github.com/kenkoooo/AtCoderProblems/blob/main/doc/api.md)と[公式DB実装](https://github.com/kenkoooo/AtCoderProblems/blob/main/atcoder-problems-backend/server-db/src/submissions.rs)を確認しました。提出APIは最大500件、`from_second`を含む条件で時刻の昇順です。

- 全API要求（リソース、各ページ、各ユーザー、リトライ）は単一クライアントの共通ロックで直列化し、前回要求の完了から次の開始まで1.6秒以上空けます。
- HTTP 429・5xx・通信エラー・30秒タイムアウトは最大3回再試行（初回を含め4回）。2/4/8秒のバックオフと数値Retry-After（上限120秒）を適用。その他HTTPエラーと不正データは同期を中止します。
- 500件のページ末尾の秒を次ページで重ね、提出IDで重複除去します。同じ秒から先に進めない満杯ページではエラーにし、黙って提出を捨てません。
- 取得開始時刻を次回差分カーソルとし、48時間前から重ねます。全ページ取得成功後だけ履歴・カーソルを一括確定します。取得途中の失敗は初回登録も差分更新も未確定です。
- updateは前回の送信待ちバッチがあっても毎回最新のAPI差分を取得し、成功した取得結果を全件保存します。その後、前回の送信失敗分・今回の新規初AC・終了した開催中保留分を合わせ、最新の開催情報とDifficultyから直近50問までの投稿本文を再構築します。古いバッチの置き換えと各問題の状態変更、新しいバッチの保存は1トランザクションです。API取得に失敗した場合は投稿せず、取得失敗を通知し、同期時刻と前回バッチを維持します。
- Discord送信が失敗したり送信結果が不明でも、成功済みのAPI取得結果と選択した投稿候補は保持します。確認できた送信成功だけを投稿済みとし、置き換え済みバッチに対する古い送信結果では新しい候補を投稿済みにしません。50問・文字数制限で省略した問題は、その回の投稿が失敗しても次回へ持ち越しません。
- 50問より多い場合は時刻の新しい50問を選び、その中でEmbed description 4096・全体6000のUTF-16単位以内に収まるまで古い順に省略します。省略行は`excluded`として次回へ持ち越しません。
- 開催情報は`contests.json`を使用し、終了が確認できるまで`held`にします。情報の欠落・不正・取得失敗時は安全側に保留します。過去の取得時点で終了を確認済みのコンテストは、更新失敗時も投稿できます。延期等で不正確な公開メタデータそのものを検出する保証はありません。
- Difficultyと問題情報はコマンド実行時に最大1日1回、開催情報はupdate時に最大5分1回更新します。登録済みIDのstats/stats_idはこれらもAPI取得せず保存済み情報だけを使用します。情報が古い場合はupdateで更新してください。
- 未登録IDの直接指定だけ全履歴を取得して1時間キャッシュします。Discordとの登録は作成しません。

**Discord送信成功直後、DBへの送信済み記録前にプロセスが異常終了した場合、または送信結果が通信断で不明の場合は、再試行による重複投稿があり得ます。** SQLiteとDiscord間に分散トランザクションがないためexactly-onceは保証できません。

Google Cloudの既存Ubuntu/Linux環境を前提に、同一VM・同一DBのBotは1プロセスで運用してください。起動時のOSファイルロック（DBパス末尾に`.lock`）で同じDBの二重起動を拒否し、サービス内のユーザー／登録ごとのロックで同時コマンドを直列化します。複数VM・異なるDBのプロセス間の共通APIレート制限はサポートしません。SQLiteファイルはネットワークファイルシステムに置かず、VMに接続された永続ディスク上に置いてください。

API反映はリアルタイムではなく、48時間を超える遅延は通常の差分更新では拾えない可能性があります。必要な場合は管理者がバックアップを取った上で履歴の再取得方法を検討してください。同一秒500提出の履歴はこのエンドポイントだけでは完全取得できません。全履歴取得がDiscord Interactionの有効期限（15分）を超える場合、処理が成功しても結果応答が届かないことがあります。再登録せず、後でstatsで登録状態を確認してください。

## Google Cloudへの安全な導入（承認後のみ）

以下は手順書です。本作業でVMへの接続や実行はしていません。

1. GitHubの旧自動Deploy Bot workflowを先に無効化します。変更コード・テスト結果を確認し、導入の承認を得ます。
2. VM上で実環境を確認します。`systemctl cat discord-bot`、`systemctl show discord-bot -p User -p WorkingDirectory -p ExecStart -p EnvironmentFiles`でサービス定義・パスを確認してください。トークンを含む環境変数や出力を共有しないでください。`findmnt`で保存先が永続ディスク上にあることを確認します。
3. 現在のGit commitを`git rev-parse HEAD`で記録し、`git status --short`で未コミット変更を確認します。既存設定・仮想環境の依存一覧を安全な場所に保管します。既存DBがあればOnline Backup APIでバックアップします。メッセージ履歴はそのまま残します。
4. サービスの実行ユーザーで書き込み可能な専用ディレクトリ（例`/var/lib/atcoder-bot`）を永続ディスク上に作成します。既存データと異なる新規`DATABASE_PATH`を指定します。環境変数は既存の安全な管理方法に合わせ、例として`/etc/atcoder-bot.env`（600権限）をsystemdの`EnvironmentFile`で読み込めます。`.env.example`をそのまま本番へ適用しないでください。
5. 稼働中Botの仮想環境を直接書き換える前に、別の仮想環境とテスト用DBで依存の導入・pytestを実行します。本番トークンを入れずにテストします。
6. 承認後、Botを停止し、レビュー済みcommitへGitで更新します。通常はVMの確認済みリポジトリで`git fetch origin`→`git checkout <承認済みcommit>`を使います。既存ローカル変更がある場合は先に保存し、強制resetはしません。新仮想環境と保存パスをサービスに設定し、ユニット変更時のみ`sudo systemctl daemon-reload`を行います。
7. `sudo systemctl start discord-bot`で起動し、ログと`/ac help`を確認します。少人数の1ユーザーでregister→stats→updateを確認します。初回登録が過去履歴を投稿しないことと精進チャンネルの設定を確認します。
8. 安定稼働とバックアップを確認してから、手動workflowを有効化できます。workflowの`~/atcoder-stats-bot`・`venv`を実VMに合わせてください。手動実行にも再起動が含まれます。GitHub Environmentで必須レビュアーを設定する運用も可能です。

旧Discord投稿からの数値移行は行いません。各ユーザーの初回registerでAPIの生涯初ACを取り込みます。

### systemdとログ

実際のサービス名・実行パスを上の調査で確認してから、承認済み運用操作として使用してください。

```bash
sudo systemctl status discord-bot
sudo journalctl -u discord-bot -n 100 --no-pager
sudo journalctl -u discord-bot -f
# 以下は稼働変更。承認後のみ
sudo systemctl stop discord-bot
sudo systemctl start discord-bot
sudo systemctl restart discord-bot
```

起動・終了時以外にネットワークバックグラウンドジョブはありません。API失敗・リソース更新失敗・Discord応答期限切れはログに種別を残し、ユーザーの入力内容や認証情報はログに出しません。LOG_LEVEL=DEBUGやライブラリのデバッグ出力を有効にする場合は、ログの取り扱いを別途確認してください。

### バックアップ・復元

稼働中にDBファイルだけを`cp`するとWAL内の変更を失う可能性があります。付属スクリプトのSQLite Online Backup APIを使用してください。バックアップ先はDBと別の保護された保存先を推奨し、定期バックアップ自体は既存の運用手段で設定できます（Botの自動同期とは別）。

```bash
# 新しいファイル名を指定。既存バックアップの上書きは禁止されます。
venv/bin/python scripts/backup_db.py /var/lib/atcoder-bot/ac.sqlite3 /secure-backup/ac-2026-10-08.sqlite3
```

復元は承認後にBotを停止して行います。現在のDBは別名でバックアップして保持し、復元したファイルを**新しい保存先**に配置して`DATABASE_PATH`を切り替えます。所有者・600権限を確認してください。復元したDBはSQLiteの`PRAGMA integrity_check`で`ok`であることを確認後に起動します。古いWAL/SHMのあるパスへバックアップを重ねないでください。バックアップから復元すると取得カーソル・投稿管理も巻き戻るため、その後のupdateで既に投稿済みのACが再投稿される可能性があります。

### ロールバック

承認後に停止し、記録した旧Git commitと旧仮想環境・旧systemd設定に戻して起動します。新SQLite DBは削除せず保管します。旧BotはSQLiteを使わないため履歴の逆変換は不要です。旧Botを起動すると旧スラッシュコマンドが同期されます。新旧Botの同時起動を避けてください。旧workflowは自動再起動を防ぐため引き続き無効化しておきます。

## テスト

実APIやDiscordへ送信しないpytestテストを用意しています。対応表と検証範囲は[tests/README.md](tests/README.md)を参照してください。本番権限・サービスパス・ディスク永続性・Discord UIと配信は導入時に別途確認が必要です。
