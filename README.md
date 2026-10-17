# JBoss EAP ログ検索（Tera Term 貼り付け用）

`jboss-log-view.sh` の内容全体を、Tera Term で接続した RHEL の **Bash** に貼り付けて実行します。上部の `START_JST`、`END_JST`、`LOG_TZ` を環境に合わせて変更してください。ファイルへの保存や `chmod` は不要です。

- `START_JST`：開始日時。含みます。
- `END_JST`：終了日時。含みません。8月26日全体なら `2026-08-27 00:00:00` を指定します。
- `LOG_TZ`：**ログ本文に書かれた時刻**のタイムゾーン。`JST`（初期値）または `UTC`。検索日時・表示日時は常に JST です。
- `LOG_COLOR`：`auto`（端末なら色付き）、`always`（常に色付き）、`never`（色なし）。
- `LOG_ROOT`：ログのルート。初期値 `/mnt/logs`。

日時は `YYYY-MM-DD HH:MM:SS` または `YYYY-MM-DD HH:MM:SS.mmm` です。日付は例示値のため、実際に検索する日に変更してください。これらの環境変数が既に設定されている場合、その値を優先します。

RHEL 側にファイルを保存した場合は、次の短いコマンドでも実行できます。

```bash
START_JST='2026-08-26 09:00:00' \
END_JST='2026-08-26 10:00:00' \
LOG_TZ='JST' \
bash ./jboss-log-view.sh
```

ログ本文が UTC なら、`LOG_TZ='UTC'` に変更してください。例えば本文の `2026-08-26 00:15:00,123` は JST の `2026-08-26 09:15:00.123` として検索・表示します。ホストの `TZ` 設定には依存しません。

長い出力をページ送りする場合は、色を保持して `less` に渡します。`q` で終了できます。

```bash
START_JST='2026-08-26 09:00:00' \
END_JST='2026-08-26 10:00:00' \
LOG_TZ='JST' \
LOG_COLOR='always' \
bash ./jboss-log-view.sh | less -R
```

`\` の後ろには空白を入れないでください。貼り付け時の最後の `PY` は、行頭から単独で記載してください。

## 検索対象

```text
/mnt/logs/{front,back}/logs/{intra-web,intra-api,inter-api,sf-api}/mid/{32文字の英数字,current}/server.log
/mnt/logs/{front,back}/logs/{intra-web,intra-api,inter-api,sf-api}/mid/{32文字の英数字,current}/server.log.YYYY-MM-DD
```

ファイル名の日付は `YYYY-MM-DD` と解釈しています。質問中の `YYYY-MM=DD` には一致させません。日付として無効な名前、`.gz`、`.bak`、`.1`、`.YYYY-MM-DD.1` なども対象外です。

`current` がタスクIDのディレクトリへのシンボリックリンクの場合も読み取ります。同じコンテナ・サービス内で同一ファイルを指すパスは、デバイス番号と inode で判定し、一度だけ表示します。タスクIDのパスが存在すればそちらを優先し、独立した `current` ディレクトリは別のログとして扱います。内容が同じ別ファイルのログは除去しません。

存在しないサービスとコンテナの組み合わせは読み飛ばします。読み取り権限不足などの失敗、対象ファイルなし、未対応の日時形式は標準エラーに表示し、終了コード `1` で停止します。対象ファイルはあるが時間範囲内のログがない場合は、0件として終了コード `0` を返します。

## 表示

```text
TIME (JST)               LEVEL    ECS SERVICE  CONTAINER
2026-08-26 09:00:01.123  ERROR    intra-api    back
  task=0123456789abcdef0123456789abcdef  file=server.log:42
  [org.example.Api] (worker-1) Request failed
  java.lang.IllegalStateException: example
      at org.example.Api.handle(Api.java:12)
------------------------------------------------------------------------------------------------
2026-08-26 09:00:02.456  WARN     intra-web    front
  task=current  file=server.log.2026-08-26:18
  [org.example.Web] (worker-2) Slow response
```

- 全対象ファイルを横断して、ログ本文の日時を JST に変換し、ミリ秒単位の昇順で表示します。ファイル更新日時やローテーション名の日付では絞り込みません。
- ログ1件ごとに区切り線を付け、サービス・コンテナ・タスクID・ファイル名・先頭行番号を表示します。タスクIDは省略しません。
- `ERROR` / `FATAL` / `SEVERE` は赤、`WARN` / `WARNING` は黄。それ以外も表示します。本文中の単語ではなく、日時直後のレベルで色を判断します。
- スタックトレースなど日時のない継続行は、直前のログとまとめて保持し、そのログの色で表示します。ファイル先頭にある日時不明の行は件数付きで警告し、検索結果に混ぜません。
- 同時刻の別ファイル間の順序はファイルの走査順です。同一ファイル内では行順を保ちます。

## 前提と検証

Python 3.9 以降の標準ライブラリだけを使います。RHEL 9 の標準 Python は 3.9 です（[Red Hat 公式資料](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/installing_and_using_dynamic_programming_languages/index)）。接続先で `python3 --version` を確認してください。

対応する基本形式は `2026-08-26 09:00:00,123 INFO  [logger] (thread) message` です。JBoss EAP の標準的なサーバーログの書式に合わせています（[Red Hat 公式設定ガイド](https://docs.redhat.com/en/documentation/red_hat_jboss_enterprise_application_platform/8.0/html-single/configuration_guide/index)）。区切りの `T`、ミリ秒の `.`、秒までの時刻、末尾の `Z` / `+09:00` / `+0900` にも対応し、ログに明記されたオフセットを `LOG_TZ` より優先します。時刻だけで日付のない形式や JSON ログには対応していません。

ログは UTF-8 として読み取り、不正なバイトは `\xNN` 表示にします。端末の制御文字も表示用にエスケープします。元のログへの書き込み・削除・権限変更は行いません。

検索に一致したログをメモリーに保持して並べ替えるため、大量のログでは時間範囲を絞ってください。処理中の追記・ローテーションを含む全ファイルの同一時点のスナップショットは保証しません。

ローカル検証コマンド：

```powershell
python -B -m unittest discover -s tests -v
```

テストはシェルに埋め込んだ Python 本体を取り出し、合成ログを使って実行します。RHEL 実機での実行とは区別してください。

作成時の検証結果：Windows / Python 3.12.10 で13件成功。シンボリックリンク・ハードリンクの重複除外も実際のファイルで検証しました。ページ送りの途中終了は POSIX のパイプ切断エラーを模擬して確認しています。Python 3.9 の構文チェック、UTF-8・LF・BOMなしの確認、Git 管理外のため `git diff --no-index --check` による空白チェックを実施済みです。

この環境では Git Bash の起動がアクセス拒否で失敗したため、Bash による実行・構文チェック、および RHEL 9.8 実機での動作確認は未実施です。実際のログ形式とタイムゾーンも未確認のため、上記の前提に合うことを確認してください。

初回テストで作成した空の `.test-logs-*` ディレクトリ12個が作業フォルダーに残っています。Windows の権限制約で削除できませんでした。テスト用フォルダーの作成方法は修正済みで、その後のテストは正常に後片付けできています。
