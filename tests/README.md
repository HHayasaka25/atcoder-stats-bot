# テストの検証範囲

実API・Discordの送信はすべてモックです。ネットワークを使うのは依存ライブラリ導入時だけです。

2026-10-08のローカル検証結果: **92 passed**（Python 3.14.4）。コンパイル確認・依存関係の整合性確認・Git差分の空白チェックも成功しました。代表的な積み上げ棒・期間内累計・ヒストグラムPNGを生成して目視確認しました。コマンド経由の描画テストは、制限環境のスレッド終了処理で停止したため、制限の外で検証しました（外部API通信なし）。

```bash
MPLCONFIGDIR=/tmp/atcoder-bot-mpl .venv/bin/python -m pytest -q
```

依頼の22項目との対応は以下です。具体的な関数は各ファイルを参照してください。

| 要件 | 主な検証 |
|---|---|
| 1 初回全履歴保存 | test_service: registration_full_history、test_api: pagination |
| 2 過去AC非投稿 | test_service: registration_full_history_baseline |
| 3 問題ごと一意 | test_service: registration_full_history、incremental_reac |
| 4 非AC除外 | test_service: registration_full_history |
| 5 新規追加のみ | test_service: incremental_reac |
| 6 再AC除外 | test_service: incremental_reac |
| 7 50問超過でも全件保存 | test_service: more_than_50_all_saved |
| 8 投稿最大50問 | test_service: more_than_50、test_statistics_and_formatting: embed_limits |
| 9 省略分を持ち越さない | test_service: more_than_50_all_saved_and_no_carryover |
| 10 API失敗時カーソル不変 | test_service: api_failure_does_not_advance_cursor |
| 11 再処理一意 | test_service: incremental_reac_earlier_first_and_idempotency |
| 12 Discord数値ID対応 | test_service: registration_full_history、duplicate_and_change_confirmation |
| 13 weekly/monthly/all | test_statistics_and_formatting: weekly、monthly、all_daily、all_weekly、all_defaults_monthly |
| 14 0件の日も集計 | test_statistics_and_formatting: weekly_zero_days、empty_history |
| 15 期間内累計 | test_statistics_and_formatting: weekly_zero_days_period_cumulative、total_effort_is_daily_for_every_all_aggregation |
| 16 Difficulty不明もAC計数 | test_statistics_and_formatting: weekly_zero_days、correction_and_histogram_unknown |
| 17 独立Figure・PNG | test_statistics_and_formatting: graphs_are_independent_and_pngs_small |
| 18 色→問題リンク→Difficulty | test_statistics_and_formatting: update_order_unknown_and_jst_grouping |
| 19 Embed制限・1メッセージ | test_statistics_and_formatting: embed_limits、test_service: more_than_50 |
| 20 同時API間隔 | test_api: concurrent_requests_shared_interval |
| 21 自動同期なし | test_statistics_and_formatting: command_surface_and_help_no_automatic_sync |
| 22 開催中保留 | test_service: ongoing_unknown_contests_held_then_released、unreliable_contest_metadata |

加えて、登録失敗・DBエラーのロールバック、登録競合、投稿失敗後のDB再オープンと最新差分取得、失敗分と新規初ACの統合・50問超過と再失敗、送信待ちがある場合のAPI失敗、送信結果不明時の候補保持、開催状況の再判定、古いバッチの成功通知の無効化、バッチ再構築のロールバック、旧DBの非破壊的移行、複数サーバーでの投稿分離、未登録IDキャッシュ、登録済み統計のAPI非アクセス、429・5xx・タイムアウトと再試行上限、同一秒飽和、入力／API応答検証、未知DB保護、稼働中WALバックアップと上書き拒否、Botの二重起動拒否、累計縦軸の0始まりを検証します。

Discordに実接続しないため、実サーバー権限、Interactionの実UI、コマンド反映速度、VMのサービス設定・永続ディスクはここでは検証しません。承認後の導入時に少人数のテストで確認してください。Python 3.14ではdiscord.py内のasyncio非推奨APIに関する警告が出ることがあります。

統計表示の追加検証: 全期間の日別棒グラフ（7日より前のAC・0件の日を含む）、週別／月別の棒グラフでも日別のTotal Effort、長い日別累計の両端マーカーの余白、自分専用statsとID必須stats_idの引数・登録IDの選択・3枚添付の共通処理を確認します。独立したdiffhistコマンドの廃止、weeklyでも生涯Difficulty分布を添付すること、ヒストグラムの空データ・小件数・大件数・長いIDでタイトルやラベルが画像内に収まること、全グラフの縦軸がAC countであることも検証します。

登録表示の追加検証: 初回登録・同じIDでの再実行・確認ボタンからのID変更で直近50ACを実行者だけに表示し、全件保存・古い日時順・過去ACの公開投稿なしを維持すること、開催中の問題を表示しないこと、空履歴を扱えることを確認します。統計の応答文が `AC 件数` だけであることも検証します。

Total Effortの縦軸について、0・1・8・55・675・1000件で0始まり以外の上限と目盛りがMatplotlibのデフォルトと一致することを確認します。横線もAgg描画で確認します。Difficulty不明分が既存色と異なる薄い紫で、網掛けと枠線を使わずに表示されることも確認します。

累計縦軸の上限・目盛りの区間数・整数限定の指定を廃止しています。

入力表記の保存・DB再オープン・同じIDの表記だけの変更・大文字小文字違いの重複登録拒否・旧版1/2からの表示表記の追加移行を確認します。stats/stats_idは引数なしで全期間・日別になることも確認します。
