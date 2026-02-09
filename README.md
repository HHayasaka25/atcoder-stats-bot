# AtCoder Stats Bot

DiscordのチャットログからAtCoderの精進記録を集計し、グラフ化するボットです。
GCP (Google Cloud Platform) のVMサーバー上で、`systemd` を使用して24時間稼働させ、GitHub Actionsで自動デプロイする構成です。

## 📝 入力形式（集計ルール）

ボットは、メッセージの**各行の先頭**を見て自動的にカテゴリを判定し、集計します。

| カテゴリ | 書き方の例 | 説明 |
| :--- | :--- | :--- |
| **ABC/ARC/AGC** | `ABC300 A B C` | コンテスト名 + 回数 + 問題記号。<br>小文字 (`abc`) や `Ex` も自動判定。 |
| **鉄則本** | `鉄則本 A01 B01` | 先頭に「鉄則」が含まれていればカウント。 |
| **典型90問** | `典型90 001 002` | 先頭に「典型」が含まれていればカウント。 |
| **その他** | `企業コン A B` | 上記以外はすべて「Others」として集計。 |

---

## ⚙️ コマンドと機能

| コマンド | 機能 |
| :--- | :--- |
| `/atcoder` | **全期間**の精進記録を集計して表示。 |
| `/atcoder period:week` | **直近1週間**の記録のみを集計。 |
| `/atcoder member:@ユーザー` | 指定した**他のメンバー**の記録を表示。 |
| `/atcoder period:range start_date:YYYY-MM-DD` | **指定期間**の記録を表示。 |

**実行結果:**
1. **Activityグラフ**: 日別AC数（棒）と累積AC数（折れ線）。
2. **Difficulty分布**: 解いた問題の難易度色別ヒストグラム。
3. **集計表**: コンテスト種別ごとの正解数一覧。

---

## 🚀 デプロイと自動反映 (GCP + GitHub Actions)

### 1. サーバー構成
- **OS**: Ubuntu (Minimal)
- **ディレクトリ**: `/home/scoalpha5/atcoder-bot`
- **仮想環境**: `venv/` (Python 3.x)
- **管理ユニット**: `systemd` (`discord-bot.service`)

### 2. 自動反映フロー (CI/CD)
1. ローカルでコードを修正し、`git push origin main` を実行。
2. **GitHub Actions** が自動起動し、SSH経由でGCPサーバーに接続。
3. サーバー側で最新コードを `git pull` し、`sudo systemctl restart discord-bot` を実行して即座に反映。

---

## 🛠 開発者向けセットアップ

### 必須ライブラリ (`requirements.txt`)
```text
discord.py
pandas
matplotlib
requests
```

### サービス管理コマンド
```bash
# 状態確認
sudo systemctl status discord-bot
# 手動再起動
sudo systemctl restart discord-bot
# ログ確認
sudo journalctl -u discord-bot -f
```
