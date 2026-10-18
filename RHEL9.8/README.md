# JBoss EAP アクセスログ検索（RHEL 9.8 / SSH端末貼り付け用）

`jboss-access-log-view.sh` の内容全体を、SSHで接続した RHEL の **Bash** に貼り付けて実行します。冒頭の `START_JST`、`END_JST` を検索したい日時に変更してください。ファイルの保存や `chmod` は不要です。Bash と Python 3.9 以降を使い、Python の追加パッケージは必要ありません。

## 日時を指定して実行

- `START_JST`：検索開始日時（含む）。
- `END_JST`：検索終了日時（含まない）。8月20日全体なら、開始を `2026-08-20 00:00:00`、終了を `2026-08-21 00:00:00` にします。
- `LOG_ROOT`：ログのルート。初期値 `/mnt/logs`。
- `LOG_COLOR`：`auto`（端末なら色付き、初期値）、`always`（常に色付き）、`never`（色なし）。

指定日時・表示日時は常に JST です。日時指定は `YYYY-MM-DD HH:MM:SS` または `YYYY-MM-DD HH:MM:SS.mmm`。スクリプト内の日付は例示値です。環境変数が既に設定されている場合、その値を優先します。

ファイルとして保存した場合は、リポジトリルートで次のように実行します。

```bash
START_JST='2026-08-20 09:00:00' \
END_JST='2026-08-20 10:00:00' \
bash ./RHEL9.8/jboss-access-log-view.sh
```

長い出力をページ送りする場合は、次のように色を保持できます。`q` で終了します。

```bash
START_JST='2026-08-20 09:00:00' \
END_JST='2026-08-20 10:00:00' \
LOG_COLOR='always' \
bash ./RHEL9.8/jboss-access-log-view.sh | less -R
```

各行末の `\` の後ろに空白を入れないでください。スクリプトを直接貼り付ける場合、末尾の `PY` は行頭から単独で置きます。

## 検索対象

```text
/mnt/logs/{front,back}/logs/{intra-web,intra-api,inter-api,sf-api}/mid/{英数字32文字,current}/access_log.log
/mnt/logs/{front,back}/logs/{intra-web,intra-api,inter-api,sf-api}/mid/{英数字32文字,current}/access_log.YYYY-MM-DD.log
```

タスクIDは大文字・小文字の英数字32文字に限定します。実在する日付の標準ローテーション名だけを対象とし、`access_log.log.YYYY-MM-DD`、`.gz`、`.bak`、連番付きファイル、別のログ名は除外します。

存在しないコンテナとサービスの組み合わせは読み飛ばします。`current` がタスクディレクトリへのシンボリックリンクでも読み取ります。同じコンテナ・サービス内でデバイス番号と inode が一致するファイルは一度だけ読み、タスクID側のパスを優先します。独立した `current` ディレクトリや、内容が同じ別ファイルはそれぞれ読みます。

検索は**ログ本文の日時**で行います。ファイル名の日付や更新時刻では絞り込まないため、UTC のログが JST の日付をまたぐ場合も検索できます。すべての該当ログを JST に変換し、ミリ秒単位の昇順で整列します。同時刻はファイルの走査順、同一ファイル内では行順を保ちます。

## ログ形式と色分け

JBoss EAP の `common`／`combined` アクセスログ形式に対応します。形式の定義は [Red Hat 公式設定ガイド](https://docs.redhat.com/ja/documentation/red_hat_jboss_enterprise_application_platform/8.0/html/configuration_guide/configuring-the-web-server-undertow-in-jboss-eap_jakarta-connectors-management)で確認できます。

```text
192.0.2.10 - - [20/Aug/2026:00:00:01 +0000] "GET /example HTTP/1.1" 200 123
192.0.2.10 - - [20/Aug/2026:09:00:02 +0900] "POST /example HTTP/1.1" 500 456 "-" "ExampleAgent"
```

英語月名の日時と明示的なタイムゾーン（`+0900`、`+0000`、`-0500`、`+09:00` など）を読み取り、JST に変換します。秒の後ろに `.123`／`,123` を付けたミリ秒形式にも対応します。ホストのタイムゾーン・ロケール設定には依存しません。

| HTTPステータス | 表示 | 色 |
| --- | --- | --- |
| 500–599 | ERROR | 赤 |
| 400–499 | WARN | 黄 |
| 100–399 | INFO | 通常色 |

アクセスログには通常ログレベルがないため、HTTPステータスで分類します。URI・User-Agent などに含まれる `ERROR`／`WARN` という文字列では判定しません。

## 出力例

```text
TIME (JST)               HTTP  LEVEL  ECS SERVICE  CONTAINER
2026-08-20 09:00:01.000  200   INFO   intra-web    front
  GET /example HTTP/1.1
  client=192.0.2.10  bytes=123  user=-  ident=-
  task=0123456789abcdef0123456789abcdef  file=access_log.log:1
------------------------------------------------------------------------------------------------
2026-08-20 09:00:02.000  500   ERROR  intra-api    back
  POST /example HTTP/1.1
  client=192.0.2.10  bytes=456  user=-  ident=-
  task=current  file=access_log.2026-08-20.log:42
  extra="-" "ExampleAgent"
------------------------------------------------------------------------------------------------
```

冒頭に検索範囲、対象ファイル数、リクエスト数、エラー・警告数、重複パスの除外数を表示します。リクエストごとに区切り線を付け、タスクIDを省略せず、元のファイル名と行番号を表示します。`combined` の Referer／User-Agent や追加フィールドは `extra` に保持し、長いリクエストも切り捨てません。

読み取り権限不足・対象ファイルなし・非対応形式・無効な日時は、標準エラーに原因を表示して終了コード `1` を返します。形式エラーにはファイル名と行番号を含めます。形式エラーを含む場合は検索結果を出力しません。対象ファイルは存在し、範囲内のログがない場合は0件として終了コード `0` を返します。空行は読み飛ばします。

ログは UTF-8 として読み取り、不正なバイトは `\xNN` 表示にします。端末の制御文字もエスケープします。元のログに書き込みは行いません。

検索範囲内のログをメモリーに保持して並べ替えるため、大量のログでは時間範囲を絞ってください。読み取り中の追記やローテーションを含む全ファイルの同一時点のスナップショットは保証しません。日時・リクエスト・ステータスの配置が異なる独自形式や JSON ログは非対応です。

## 検証方法

リポジトリルートの Windows PowerShell で実行します。

```powershell
python -B -m unittest discover -s tests -v
git diff --check
```

テストでは、既存スクリプトと同じ方法でシェルに埋め込んだ Python 本体を取り出し、合成ログで日時変換・整列・検索範囲・ファイル選別・重複除外・色分け・異常系を確認します。RHEL 実機および実ログでの動作確認とは区別してください。
