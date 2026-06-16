# cleaning-task-api

清掃スタッフのタスク・シフト・給与を管理する REST API。  
FastAPI + Supabase で構築し、Render にデプロイしています。

---

## 作成者 / 担当領域

### 村上 武志（Takashi Murakami）

**Project Owner / Product Manager / System Architect**

本プロジェクトの業務設計、要件定義、システム設計、運用設計を担当。
ホテル・民泊運営における清掃、シフト、勤怠、チェック、設備管理、スタッフ教育の業務フローをもとに、管理システム全体の設計を行っています。

| 担当領域 | 内容 |
|---|---|
| 事業設計 | ホテル・民泊運営フロー設計、業務要件定義、運用改善、KPI 設計 |
| システム設計 | API 設計、データベース設計、権限設計、外部サービス連携設計 |
| プロダクト企画 | 機能要件定義、UI/UX 要件整理、改善ロードマップ策定 |
| 清掃管理 | 清掃タスク、チェックタスク、担当者割当、ステータス管理 |
| シフト・勤怠管理 | シフト管理、Jinjer 打刻同期、未打刻判定、勤怠確認 |
| スタッフ管理 | アカウント管理、対応可能物件、メイトカルテ、教育履歴管理 |
| 設備・報告管理 | 設備トラブル、忘れ物、備考、現場報告フロー管理 |
| 外部連携 | Beds24、Jinjer、LINE WORKS、Supabase、Render との連携設計 |

> 本システムは、村上武志による業務設計・システム設計をもとに、AI 開発支援ツールを活用して実装・改善を進めています。

---

## 技術スタック

| 項目 | 内容 |
|---|---|
| 言語 | Python 3 |
| フレームワーク | FastAPI / uvicorn |
| データベース | Supabase (PostgreSQL) |
| 認証 | PyJWT（Bearer JWT / 有効期限 12 時間） |
| 外部連携 | Beds24 API / Jinjer API / LINE WORKS API |
| ホスティング | Render (Web Service + Cron Job) |

---

## ローカル開発

### 1. リポジトリをクローン

```bash
git clone https://github.com/murakami567/cleaning-task-api.git
cd cleaning-task-api
```

### 2. 依存パッケージをインストール

```bash
pip install -r requirements.txt
```

### 3. 環境変数を設定

プロジェクトルートに `.env` を作成します。

```env
SUPABASE_URL=https://xxxxxxxxxx.supabase.co
SUPABASE_KEY=your-supabase-anon-key
SYNC_API_KEY=your-sync-api-key
```

### 4. サーバーを起動

```bash
uvicorn app.main:app --reload
```

起動後、以下の URL で API ドキュメントを確認できます。

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## 環境変数

| 変数名 | 必須 | 説明 |
|---|---|---|
| `SUPABASE_URL` | ✅ | Supabase の接続 URL |
| `SUPABASE_KEY` | ✅ | Supabase の API キー |
| `SYNC_API_KEY` | ✅ | Beds24 同期エンドポイントの認証キー |
| `CRON_SECRET` | 任意 | Render Cron Job からの同期API認証キー |
| `JINJER_API_TOKEN` | 任意 | Jinjer API 連携用トークン |

> `SUPABASE_URL` / `SUPABASE_KEY` が未設定の場合、起動時にエラーになります。

---

## API 構成

| ルーター | プレフィックス | 主な機能 |
|---|---|---|
| auth | `/api/auth` | ログイン・JWT 発行 |
| tasks | `/tasks` | 清掃タスク・物件・部屋・シフト管理 |
| employee | `/api/employee` | 従業員向け（タスク・シフト・実働登録） |
| admin_portal | `/api/admin-portal` | 管理者向け（メッセージ・スケジュール・実働確認） |
| payroll | `/payroll` | 給与設定・月次計算 |
| beds24 | `/beds24` | Beds24 CSV 同期 |
| jinjer | `/jinjer` | Jinjer シフト・打刻同期 |
| mate-cartes | `/mate-cartes` | メイトカルテ管理 |
| facilities | `/facilities` | 設備管理・設備トラブル報告 |

---

## Beds24 自動同期

Render の Cron Job（`beds24-sync`）が毎日 **04:00 JST** に以下を実行します。

```bash
curl -X POST "https://cleaning-task-api.onrender.com/beds24/csv/sync" \
  -H "x-api-key: YOUR_SYNC_API_KEY"
```

- 取得期間: 翌日〜60 日後（省略時のデフォルト）
- Supabase へ upsert（`booking_id` で重複排除）
- 初期ステータス: `未着手`

---

## Jinjer 打刻同期

Render の Cron Job から Jinjer 打刻同期 API を実行します。

```bash
curl -X POST "https://cleaning-task-api.onrender.com/jinjer/attendances/cron-sync" \
  -H "X-CRON-KEY: YOUR_CRON_SECRET"
```

- 同期対象: 当日分の打刻
- 保存先: `attendance_logs`
- 用途: 管理ホームの未打刻判定、出勤打刻確認

---

## デプロイ

Render に `render.yaml` の設定で自動デプロイされます。

```yaml
buildCommand: pip install -r requirements.txt
startCommand: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

`main` ブランチへのプッシュで自動デプロイが実行されます。

---

## ロギング

全エンドポイントで構造化ログを出力します。Render のログ画面で確認できます。

```
2026-05-10 04:00:01 [INFO]  app.routers.beds24: beds24 sync completed
2026-05-10 09:12:34 [WARNING] app.routers.auth: login failed: wrong password login_id=staff001
2026-05-10 10:05:22 [ERROR] app.routers.tasks: get_today_tasks failed: ...
```

| レベル | 用途 |
|---|---|
| `INFO` | 正常操作（取得件数・作成 ID 等） |
| `WARNING` | 認証失敗・権限不足 |
| `ERROR` | DB エラー・例外（スタックトレース付き） |

---

## 関連リポジトリ

- フロントエンド（管理画面）: [cleaning-task-admin](https://github.com/murakami567/cleaning-task-admin)

---

## Copyright

Copyright © 2026 Takashi Murakami

All Rights Reserved.
