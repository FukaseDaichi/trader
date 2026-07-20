# 重大・高優先度設計課題の解決計画

- 更新日: 2026-07-20 JST
- 状態: 実装完了・運用移行中
- 対象: 2026-07-20 の設計・実装・仕様差異レビューで抽出した重大／高優先度8件

## 目的

レビュー時点のシステムは日次処理、テスト、静的解析、本番ビルドを完走できる一方、モデル評価、実売買可能時点、実績集計、モデル切替の境界に不整合があった。本計画は、その是正内容、優先度、実施順、運用移行条件を記録する。

- KPIゲートが、実際にシグナルを生成する同一モデルを検証している
- バックテスト、決済、ダッシュボードが実際に約定可能な価格と時点を使う
- 資産曲線、ドローダウン、Sharpe、reliabilityが再現可能で誤解を招かない
- 保存済みモデルが互換性、完全性、品質を確認したうえでatomicに切り替わる
- 既存の安全条件（ゲート未達は`HOLD`、日次処理は縮退継続、Phase 2 shadowは非介入）を維持する

8件のコード・仕様改修は2026-07-20に完了した。DB migration、過去結果の再集計、schema v3モデルの再学習、4週間のshadow監視は運用環境での作業なので未完了である。本ファイルはユーザー指定のP0/P1一覧兼ロールアウト記録として、運用移行完了後も削除しない。日々の未完了タスクは`../06_issues_and_backlog.md`でも追跡する。

## 優先度の定義

| 優先度 | 意味 | リリース判断 |
|---|---|---|
| P0 | 売買判断または成績評価の前提を壊す重大不整合 | 解消までPhase 2 active化を禁止し、関連指標を参考値として扱う |
| P1 | 本番の信頼性、再現性、説明可能性を大きく損なう高優先度課題 | P0直後に解消し、機能拡張より優先する |

## 全体優先順位

「影響優先度」は問題の重大度、「実施順」は依存関係を考慮した推奨着手順である。DR-002の約定時点契約を先に確定し、その契約を使ってDR-001とDR-003を直す。

| ID | 課題 | 影響優先度 | 実施順 | 実装状態 / 運用制約 |
|---|---|---:|---:|---|
| DR-001 | KPIゲートと配信モデルが異なる | P0 | 2 | 実装完了。schema v3モデルの再学習・active化までは旧artifactを使用しない |
| DR-002 | バックテストの約定時点が実運用で再現できない | P0 | 1 | 実装完了。migration 0004適用と過去結果のv2再集計が必要 |
| DR-003 | 重複5日リターンを逐次複利計算している | P0 | 3 | 実装完了。再集計完了まで旧equity/DD/Sharpeを新系列と混ぜない |
| DR-004 | reliabilityが別モデル／現行週だけを参照し得る | P1 | 5 | 実装完了。v2再集計後の観測数・fallback数を監視する |
| DR-005 | 保存済みモデルの互換性確認とpurge gapが不足 | P1 | 4 | 実装完了。旧artifactは互換性検証でfail-closeする |
| DR-006 | `vol_norm`の文書上の回帰と実装の二値分類が不一致 | P1 | 8 | 実装完了。対応モードから除外し、残存envは警告して`triple_barrier`へ縮退 |
| DR-007 | `expectancy`と`trades`の定義が閾値最適化に不適切 | P1 | 6 | 実装完了。旧env名・objectiveは警告付きaliasのみ |
| DR-008 | 週次モデルが部分成功でもactive化される | P1 | 7 | 実装完了。新候補は全対象coverage・checksum・証跡検証を通過した場合だけactive化 |

## DR-001: KPIゲートを配信モデルと一致させる

**優先度: P0 / 実施順: 2**

### 問題

日次KPIゲートは`src/backtest.py`内でテクニカル特徴量だけの一時モデルを学習・評価する。一方、通常の配信シグナルは保存済みPhase 1モデルを使い、マクロ特徴量とisotonic較正を含む。ゲートで選んだ閾値も、異なる確率尺度の配信モデルへ適用される。

### 解決方針

1. KPI判定をモデル成果物の一部にする。
   - 週次学習時に、保存対象と同じ特徴量、ラベル、モデル構造、較正処理でwalk-forward OOS予測を生成する。
   - OOS予測からKPIとシグナル閾値を計算し、銘柄別バンドルへ保存する。
2. ゲート証跡に次を保存する。
   - `model_version`
   - 順序付き特徴量スキーマのハッシュ（`feature_schema_hash`）
   - 実際に配備するbooster bundleと較正器のハッシュ
   - ラベル設定と実効ホライズンのハッシュ
   - 較正方式／較正器の識別子
   - コスト、スリッページ、約定価格基準
   - OOS期間、観測数、KPI、閾値、判定結果
3. 日次推論では、予測バンドルとゲート証跡の識別子が完全一致した場合だけゲートを有効とする。
4. 不一致、欠損、破損時は例外終了せず、次の安全動作を取る。
   - `phase1` strictモード: `HOLD`
   - `auto`モードのephemeral fallback: fallback candidate自身のOOSゲートだけを使用
5. 日次処理内で保存済みモデルとは別の代理モデルを再学習し、その判定を保存済みモデルへ流用する経路を廃止する。

### 完了条件

- シグナルの`model_version`と、利用bundleの特徴量スキーマ、ラベル設定、較正方式、booster／較正器がゲート証跡と一致する（日次入力値の`features_hash`は別の監査値）
- 一つでも不一致ならactionが`HOLD`になる自動テストがある
- 閾値最適化と日次シグナルが同じ較正済み確率を使う
- ephemeral／rollback fallbackについても、別モデルのゲートを流用しない
- 日次処理の縮退継続性を維持する

### 実装結果（2026-07-20）

- 週次保存モデルと日次ephemeral fallbackは、どちらも実際に推論する最終boosterのpurged OOS証跡を使う。代理モデルのゲート流用を廃止した
- 較正器と閾値はtuning区間だけで学習・選択し、H行embargo後のholdoutだけで最終ゲートを評価する。外部OOSはearly stoppingへ渡さない
- booster、較正器、artifact/gate契約、OOS、split、KPI、閾値をSHA-256付きの`gate_evidence`へ保存し、日次推論時に再検証する
- strict `phase1`は証跡不一致を`HOLD`、`auto`は互換性のある保存モデルがなければ自身の証跡を持つephemeral candidateへ縮退する
- ephemeral candidateのversionはartifact schemaとartifact/gate contract hashから安定生成し、契約違いのDB観測を分離する。exact boosterは証跡内のbundle hashで結び付ける

## DR-002: 実行可能な約定時点契約を定義する

**優先度: P0 / 実施順: 1**

### 問題

06:00 JSTの処理は前営業日までの市場データを見てシグナルを作るが、バックテストと決済は、その前営業日の終値で約定できたものとしている。実際に注文できるのは次の取引セッション以降であり、夜間ギャップが事前取得された成績になる。

### 解決方針

1. 次の時点を横断データ契約として定義する。
   - `market_as_of_date`: 判断に利用可能な最後の市場日
   - `decision_at`: シグナル生成時刻
   - `entry_date`: 最初に約定可能な市場日
   - `entry_price_basis`: 寄付き、VWAP、指値などの価格基準
   - `exit_date`／`exit_price_basis`: ホライズンまたはバリア決済基準
2. 現行の06:00 pre-open運用では、既定を「次営業日寄付き＋明示的スリッページ」とする。
3. `src/labels.py`、`src/backtest.py`、`scripts/settle_outcomes.py`を同じ約定関数／契約へ寄せる。
4. トリプルバリアは、エントリー後に観測できる高値・安値だけで評価する。寄付きギャップでバリアを超えた場合の約定規則も明文化する。
5. DBとJSONに価格基準と契約バージョンを保存し、旧方式と新方式を混ぜて集計しない。
6. 契約変更後に過去成績を再計算し、旧系列は明示的に廃止または別バージョンとして隔離する。

### 完了条件

- 前日終値から翌日寄付きへ大きくギャップする合成データで、未来情報を使わないテストが通る
- ラベル、バックテスト、決済が同じエントリー日・価格を返す契約テストがある
- JPX休場日をまたいでも`entry_date`が正しく決まる
- すべての成績成果物に約定契約バージョンが入る
- UIとLINEで「実績」「損益」と表示する値が新契約に基づく

### 実装結果（2026-07-20）

- `src/execution.py`に`next_session_open_to_close_v2`を定義し、ラベル、銘柄別KPI、Phase 2、決済で共有した
- 判断日の次に存在する市場行のopenで入り、entryを1営業日目とするH営業日目のcloseで時間決済する。休日はOHLCV行で解決する
- トリプルバリアはentry後だけを評価し、寄付きギャップを日中高安より先、同一バーのTP/SL同時接触をSL優先とした
- migration 0004と`--restate-execution-contract`を追加した。運用DBへの適用と過去結果再集計は未完了

## DR-003: 重複ホライズンと資産曲線を分離する

**優先度: P0 / 実施順: 3**

### 問題

毎営業日に発生する5日ホライズンの結果は互いに重複する。現在は各日の5日リターンを、全資本を順番に投資した独立期間として複利計算しており、資産曲線、ドローダウン、Sharpeが投資可能なポートフォリオを表していない。

### 解決方針

1. 「シグナル品質」と「投資可能な資産曲線」を別の集計として定義する。
   - シグナル品質: 重複サンプルを許し、hit rate、Brier、平均H日リターン等を集計する。複利資産曲線にはしない。
   - 資産曲線: 日次mark-to-market可能なポジションと資本配分から日次リターンを作る。
2. Phase 1の仮想資産曲線を残す場合は、毎日作るH個のsleeveへ原則`1/H`ずつ資本配分するなど、資本総量が1を超えない方式を採用する。
3. 代替として非重複エントリーだけを使う場合は、そのサンプリング規則を成果物へ保存する。
4. TOPIXも同じ開始日、同じ保有期間、同じ資本配分で比較する。
5. Sharpeは日次mark-to-market系列から`√252`で計算する。H日非重複系列なら頻度に対応した年率化を使う。
6. `performance_detail.json`へ`accounting_method`と`contract_version`を追加する。

### 完了条件

- 同時保有sleeveのウェイト合計が定義したgross上限を超えない
- 同一入力から日次損益を再現できる
- 重複5日リターンを直接逐次複利しない回帰テストがある
- strategyとTOPIXが同じ時間軸・資本配分で比較される
- UIでシグナル品質とポートフォリオ成績が明確に区別される

### 実装結果（2026-07-20）

- 銘柄別KPIは毎日`1/H`のsleeveを建て、gross上限1.0、日次mark-to-market、entry/exit両側コストで評価する
- 実績JSONはentry/eval期間が重ならないcohortだけを逐次運用し、重複を許すrawシグナル品質指標と分離した
- Phase 2バックテストも非重複期間とし、旧book全決済＋新book全建て（同銘柄も相殺せず、最終決済を含む）へ往復コストを課す
- TOPIX openは現在未取得なので同一basis比較を捏造せず`benchmark: null`とcoverage理由を出す。この間Phase 2 activeゲートはfail-closeする
- Phase 2 activeゲートはバックテストと当日snapshotのCS `model_version`一致も要求し、旧レポートを新モデルへ流用しない

## DR-004: reliabilityを実際のシグナル予測へ結び付ける

**優先度: P1 / 実施順: 5**

### 問題

reliability集計はモデル種別を指定せず最新active versionを取得するため、Phase 2 CSモデルを選ぶ可能性がある。また現行モデルバージョンだけを抽出するため、週次切替のたびに長期観測が切断される。

### 解決方針

1. `active_model_version()`をreliabilityの起点にしない。
2. 各`signals`行が参照する`prediction_id`から、実際にaction生成に使った予測を取得する。
3. 既存行で`prediction_id`がない場合だけ、保存済み`signals.conviction`を明示的なfallbackとして使う。
4. Phase 1シグナルのreliabilityとPhase 2スコア較正を別系列・別成果物にする。
5. モデルバージョンを横断して集計し、必要に応じてバージョン別の補助内訳を出す。
6. ラベル定義、ホライズン、約定契約が異なるデータは同じビンへ混ぜない。

### 完了条件

- Phase 1とCSの両方がactiveなDB fixtureでも、Phase 1シグナルに紐付く確率だけが選ばれる
- 週次モデル切替後も互換契約内の観測数が維持される
- reliabilityの観測数が元シグナルへ追跡可能である
- fallback利用数と除外理由が成果物へ出る

### 実装結果（2026-07-20）

- `signals.prediction_id`から実際にactionへ使ったPhase 1予測を取得し、IDがないlegacy行だけ`signals.conviction`へfallbackする
- `next_session_open_to_close_v2`と互換ホライズンの行だけをモデル版横断で集計し、source別件数と除外理由を成果物へ出す
- shadow比較もPhase 1はsignal-linked prediction、Phase 2は各日の`portfolio_snapshots.model_version`に一致する予測だけを使う

## DR-005: モデル互換性と時系列purgeを強制する

**優先度: P1 / 実施順: 4**

### 問題

active Phase 1モデルの互換性確認は、現在マクロ特徴量の有効・無効に限られる。ラベルモード、実効ホライズン、特徴量スキーマ、較正方式が異なる成果物も読み込める。またpurge gapは実効ホライズン以上に強制されず、長いホライズンで学習ラベルが検証区間へ重なる可能性がある。

### 解決方針

1. モデルmanifestへ次を必須保存する。
   - `artifact_schema_version`
   - `label_mode`と`effective_horizon_days`
   - TP／SL／time barrier設定
   - `feature_columns`と順序付き`feature_schema_hash`
   - macro有効フラグ（macro列は順序付き特徴量スキーマへ含める）
   - calibration mode/version
   - 学習コードまたはgit commit識別子
2. 読み込み時にmanifestとruntime要求を完全比較し、不一致理由を構造化して記録する。
3. シグナルのhorizon/provenanceはruntime設定ではなく、実際に使用したバンドルmanifestから取得する。
4. per-ticker学習でも`effective_purge_gap >= effective_horizon_days`を強制する。トリプルバリアの最大参照期間が長い場合はそちらを採用する。
5. 不一致時はDR-001の安全動作へ接続し、ゲート証跡のないfallback予測をactionableにしない。

### 完了条件

- label、horizon、feature、calibrationの各不一致を個別テストできる
- runtimeのホライズン変更だけで旧モデルの予測期間が書き換わらない
- すべてのwalk-forward分割で、学習ラベルの参照期間が検証開始前に終了する
- 不一致時も日次処理全体は継続し、対象銘柄だけ安全に縮退する

### 実装結果（2026-07-20）

- artifact schema v3にラベル、実効H、順序付き特徴量、macro有無、較正実装ID、v2約定契約と各ハッシュを必須化した
- 列名を変えず特徴量計算の意味を変える場合はartifact schemaを更新し、旧版を互換扱いしない運用規約とした
- active pointer、version metadata、manifest、ticker metadata、ファイルchecksum、候補ゲートを日次・drift・dashboardで共通検証する
- `effective_purge_gap = max(configured purge, H)`を学習分割、内部validation、tuning/holdout間へ適用した
- 旧artifactや設定不一致はfail-closeし、日次全体は`auto` fallbackまたはstrict `HOLD`で継続する

## DR-006: `vol_norm`の製品契約を決める

**優先度: P1 / 実施順: 8**

### 問題

仕様は`vol_norm`をボラティリティ正規化H日リターンの回帰としているが、学習処理は連続値`target`を使わず、符号を表す`target_class`で二値分類を行う。設定名、学習目的、`prob_up`、閾値の意味が一致しない。

### 解決方針

短期方針として、回帰が明確な製品要件でない限り`vol_norm`を対応モードから外すことを推奨する。

1. 利用実績と環境設定を確認する。
2. 未使用なら、設定候補、仕様、テストから削除し、明示的な移行エラーまたは安全な設定検証を入れる。
3. 回帰を維持する場合は、別タスクとして次を一式実装する。
   - 連続値目的変数を使う回帰objective
   - 回帰値からactionへ変換する専用閾値契約
   - `prob_up`と混同しない出力名・DB契約・UI表示
   - 回帰向けKPI、較正、ドリフト指標
4. 回帰スコアを二値分類用の確率閾値へ直接流用しない。

### 完了条件

- 対応設定一覧と実装objectiveが一致する
- `vol_norm`を残す場合、学習に連続値`target`を使うことをテストする
- 出力値の意味がDB、JSON、UI、通知で一貫する
- 未対応モードが黙って別アルゴリズムとして動かない

### 実装結果（2026-07-20）

- `vol_norm`を対応モード一覧と仕様から削除した
- 古い環境に`TRADER_LABEL_MODE=vol_norm`が残っていても黙って二値分類せず、deprecation warningを出して`triple_barrier`へ安全に縮退する
- `binary_1d`はrollback用として残すが、約定価格契約はv2を使う

## DR-007: KPI指標と閾値選択を再定義する

**優先度: P1 / 実施順: 6**

### 問題

現在の`trades`はポジション量が変化した日を数え、`expectancy`はその日だけの平均net returnである。保有中損益が除外され、段階的な建玉変化は複数取引として数えられる。さらに、同じOOS標本で閾値を選び、その閾値のKPIを評価すると選択バイアスが入る。

### 解決方針

1. 指標名と計算単位を明確に分離する。
   - `turnover_days`: ポジション変化があった日数
   - `round_trips`: 建玉開始から解消までの取引エピソード数
   - `signal_cohorts`: 独立したシグナル結果数
   - `avg_daily_net_return`: 全保有日を含む日次純リターン平均
   - `expectancy_per_trade`: 完結した取引エピソードの平均純損益
2. KPIゲートはDR-003の日次投資可能リターンを基礎にする。
3. 閾値選択区間と最終ゲート評価区間を時系列で分ける。データ量が足りない場合はnested walk-forwardまたは保守的な固定閾値を使う。
4. 目的関数は単一の`expectancy`だけでなく、最低取引数、DD、コスト後リターン、安定性を制約として扱う。
5. 既存JSONの意味を黙って変更せず、schema versionまたは新しいフィールド名を導入する。

### 完了条件

- 手計算可能な価格・シグナル系列で、取引数と各損益指標が一致する
- 保有中損益がKPIから欠落しない
- 閾値選択に使った期間が最終ゲート評価期間と分離される
- 取引なし、常時保有、段階的建玉、売買反転の境界ケースをテストする

### 実装結果（2026-07-20）

- metrics schema v2で`turnover_days`、`round_trips`、`signal_cohorts`、`avg_daily_net_return`、`expectancy_per_trade`を分離した
- canonical設定を`TRADER_KPI_MIN_AVG_DAILY_NET_RETURN`、`TRADER_KPI_MIN_ROUND_TRIPS`、`TRADER_AUTO_THRESHOLD_MIN_ROUND_TRIPS`、objective=`avg_daily_net_return`へ変更した
- tuningとholdoutをH行embargoで分離し、最低round tripを満たす候補がなければ疎な最良候補を採用せず既定閾値へ戻す
- 必須KPIが欠損・NaN・Infなら閾値比較を通さず`*_unavailable`でfail-closeする
- 旧`EXPECTANCY`／`TRADES` envとobjective=`expectancy`は警告付きaliasとしてのみ残した

## DR-008: 週次モデルを完全・不変・atomicに切り替える

**優先度: P1 / 実施順: 7**

### 問題

週次Phase 1学習は一部銘柄だけ成功してもactive pointerを更新できる。同じ日付のversionを再利用すると、失敗した銘柄の旧ファイルが残り、新旧成果物が混在する可能性がある。

### 解決方針

1. versionを日付だけでなく、実行時刻または一意IDとgit commitを含む不変識別子にする。
2. 学習成果物を`staging`ディレクトリに出力し、既存versionへ上書きしない。
3. version manifestに対象銘柄一覧、成功・失敗、各ファイルchecksum、設定ハッシュを保存する。
4. active化前に候補ゲートを実行する。
   - 必須銘柄カバレッジ
   - 直前active版からのカバレッジ劣化上限
   - DR-001のKPI証跡完備
   - manifestとファイルchecksumの整合
5. 候補ゲート通過後だけ、ディレクトリ確定と`active_model.json`更新をatomicに行う。
6. DB model registry書き込み失敗はoutbox対象とし、ファイルポインタとの整合状態を再生可能にする。
7. 失敗時は直前activeを保持し、日次処理へ影響させない。

### 完了条件

- 1銘柄だけ成功するfixtureでactive版が切り替わらない
- 同日再実行しても旧ファイルと新ファイルが混在しない
- checksum欠損、manifest不一致、pointer更新途中失敗から復旧できる
- 直前activeへのロールバックが自動テストされる
- active化理由と却下理由が週次レポートに残る

### 実装結果（2026-07-20）

- 時刻・git commit・UUIDを含む一意versionを`.staging/`へ作り、既存versionを上書きしない
- 全対象銘柄coverage、exact-candidate証跡、artifact集合/checksum、manifest、前版からのcoverage劣化を候補ゲートで検査する
- 合格時だけimmutable directoryをpromoteし、`active_model.json`をatomic replaceする。失敗時は前activeを維持しstagingを片付ける
- `model_registry`はkind単位でactiveを切り替え、DB障害時はfile pointer provenance付きの安定event IDでoutboxへ保存・再送する。再送時点のfile pointerと一致しない古いactivation eventは履歴行だけを保持し、DBのactive版を巻き戻さない

## 実施フェーズ

### フェーズA: 評価契約の確定

対象: DR-002、DR-001、DR-003

1. [x] 約定時点契約と成果物のversioningを決める。
2. [x] 同一モデルOOSゲートを実装する。
3. [x] 日次mark-to-market／非重複方式で成績系列を再構築する。
4. [ ] 運用DBの過去成果物を再生成し、旧方式と数値差をレビューする。

### フェーズB: モデル証跡と表示の整合

対象: DR-005、DR-004

1. [x] artifact manifestと互換性検証を導入する。
2. [x] purge gapを実効ホライズン以上へ強制する。
3. [x] reliabilityをsignal→predictionの直接参照へ変更する。

### フェーズC: 選択・配備の堅牢化

対象: DR-007、DR-008、DR-006

1. [x] KPI指標と閾値選択／評価分離を実装する。
2. [x] staging、候補ゲート、atomic activationを実装する。
3. [x] `vol_norm`を対応モードから削除し、安全な旧env移行経路を実装する。

## 横断テストとロールアウト条件

- 既存の全`tests/test_*.py`を通す
- 日次処理のDB、macro、saved-model、Phase 2各障害で縮退継続する
- KPI未達または証跡不一致時は必ず`HOLD`になる
- `TRADER_PORTFOLIO_MODE=shadow`のPhase 1 actionと通知が変更されない
- pre-open coreとretryが同一契約・同一環境変数で動く
- `docs/`へ新しい永続JSONを追加する場合、publish workflowの`--exclude`とテストを同時更新する
- 新旧成績差分、観測数、fallback率、ゲート通過率を最低4週間shadow監視する
- Phase 2 active化は、全P0完了、DR-004/005/008完了、shadow reportの`active_readiness`通過後に別途手動判断する

## 完了チェックリスト

- [x] DR-001 同一モデルKPIゲート
- [x] DR-002 実行可能な約定時点契約
- [x] DR-003 重複しない投資可能な成績系列
- [x] DR-004 signalに紐付くreliability
- [x] DR-005 artifact互換性とpurge gap
- [x] DR-006 `vol_norm`契約の確定
- [x] DR-007 KPI指標と閾値評価の再定義
- [x] DR-008 atomic model activation
- [x] 仕様書`00`〜`06`、ルートREADME、AGENTS、`.env.example`を実装に合わせて更新
- [x] 残る運用タスクを`../06_issues_and_backlog.md`へ移管

## 運用移行チェックリスト

- [ ] 本番Neonへ`migrations/0004_execution_contract.sql`を適用する
- [ ] `scripts/settle_outcomes.py --restate-execution-contract`で既存のactionable signalをv2再決済し、件数・欠損・旧新差分を確認する
- [ ] `scripts/weekly_model_retrain.py`を実行し、schema v3候補の全対象coverage、`candidate_validation.passed`、atomic active化、registry登録またはoutbox待機を確認する
- [ ] 実行環境の旧KPI env aliasと`TRADER_LABEL_MODE=vol_norm`をcanonical設定へ移行する
- [ ] 新旧成績差分、観測数、fallback率、ゲート通過率を最低4週間shadow監視する
- [ ] v2のfull exit/full entry/terminal exit定義によるturnover分布を観測し、旧netted定義由来の上限`0.40`をactive判定前に再校正する
- [ ] TOPIXの同一basis open系列を追加するか、benchmark unavailableのまま運用するかを決定する。完全coverageがない間はPhase 2 activeを禁止する
- [ ] 上記完了後も`active_readiness`を確認し、Phase 2 active化は別途人間が判断する
