# promo — 紹介動画（Motion 製）

日次で株価を学習・判定・公開する自動化パイプラインを紹介する約104秒の動画。
アニメーションはすべて [Motion](https://motion.dev)（`vendor/motion-12.43.0.js`、MIT）の
単一タイムライン（`animate(sequence)`）で組んであり、ブラウザでそのまま再生できる。

| ファイル | 内容 |
|---|---|
| `index.html` | 動画本体（1920×1080 ステージ、ウィンドウに合わせて拡縮）。開くと自動再生、Space で一時停止 |
| `assets/*.jpg` | 公開ダッシュボードのスクリーンショット（ダークテーマ） |
| `scripts/render.js` | Playwright でタイムラインを1フレームずつシークして撮影し、ffmpeg で MP4 化 |
| `scripts/bgm.py` | BGM を numpy だけで合成（外部音源なし）。キュー時刻は `index.html` の `T` と対応 |
| `ai-stock-trader.mp4` | 書き出し済みの完成動画（BGM 付き） |

## 構成（`index.html` の `T`）

| 秒 | 章 | 見せること |
|---|---|---|
| 0–7 | オープニング | 05:59:57 → 06:00:00、市場が開く3時間前 |
| 7–14.5 | タイトル | 株価を学び、判断し、届ける。全自動で |
| 14.5–26 | 01 DAILY AUTOPILOT | 04:30 キュレーション → 06:00 予測 → リトライ → 公開 → 12:30 監査 |
| 26–41 | 02 HOW IT LEARNS | 日足 → 45特徴量 → LightGBM → isotonic 較正 → 上がる確率 |
| 41–49.5 | 03 LABELING | トリプルバリア（+1.5 ATR / −1.0 ATR / 5営業日） |
| 49.5–60 | 04 KPI GATE | 基準未達は強制 HOLD |
| 60–68.5 | 05 SELF-RENEWAL | 週次再学習 → 検証 → 署名 → 切替 → 監視 → 起票 |
| 68.5–76 | 06 NEVER STOP | フォールバック／outbox／ephemeral 候補／Phase 2 スキップ |
| 76–91 | 07 DESIGN | ホーム・銘柄詳細・スマホ画面、朝のダイジェスト |
| 91–97 | 08 BY THE NUMBERS | 50銘柄・45特徴量・15ワークフロー・5段階・48テスト・手作業0 |
| 97–104 | エンディング | ロゴ、技術スタック、免責 |

## 書き出し

```bash
# 映像（Playwright 同梱 Chromium と ffmpeg が必要）
node promo/scripts/render.js /tmp/video.mp4 --workers 6
# 特定時刻の静止画だけ確認
node promo/scripts/render.js /tmp/x.mp4 --only 12.5,40,80

# BGM を合成して多重化
python3 promo/scripts/bgm.py /tmp/bgm.wav
ffmpeg -i /tmp/video.mp4 -i /tmp/bgm.wav -c:v copy -c:a aac -b:a 192k -shortest promo/ai-stock-trader.mp4
```

フォントは Google Fonts（Inter / Noto Sans JP / JetBrains Mono）。オフラインで書き出すときは
同名フォントをローカルにインストールしておく（`render.js` は Google Fonts へのリクエストを遮断する）。

動画内の数値・時刻は 2026-10 時点の実装（`.github/workflows/`、`src/model.py`、`src/macro.py`、
`src/backtest.py` の既定値）に合わせている。仕様が変わったら `index.html` の該当テキストも直すこと。
