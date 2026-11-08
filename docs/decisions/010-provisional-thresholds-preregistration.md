# 010 閾値は暫定とし、事前登録した規則で決める

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: PR #120（指示書55-2）・PR #127（指示書56）・PR #134（指示書61）・PR #148（`docs/measure/65A_decision_rules_v1.md`）・PR #156（`scripts/probe_data/registration_v2.md`）
- 決めた人: 未確認

---

## 決めたこと

- 作動点（入口・共鳴の門・上層・下層）は暫定の設定値として `src/match_config.py` に置き、根拠をコメントに残す
- 作動点を決めるための計測は、規則を計測の前にコミットしてから行う（事前登録）
- 数値を見た後に規則を変えるときは「規則の変更」として記録し、判断する人の確認を取る

## 理由

- 無関係な文どうしでも g が底上げされる（nomic の異方性）ため、初期値は実測の帯より上に置いた（`src/match_config.py` の注記・指示書55 §4-1）
- 数値を見た後に規則を変えると、結果が楽観に寄るため（registration_v2）

## 捨てた選択肢と理由

- 【推測】数値を見ながら作動点を選ぶ進め方（事前登録の前のやり方）

## 影響範囲

- `src/match_config.py`・`docs/measure/`・`scripts/probe_data/registration_v2.md`・`scripts/element_probe*.py`

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
