# 66C（L＝含意の判定）の計測の道具（アーカイブ）

- 2026-11-10 の KAORU の決定: 66C（L の判定）と、そのモデル（llm-jp/llm-jp-3-1.8b-instruct）は今後使わない
- ここにあるもの（動かさない。CI のテストからも外した。ルートの `conftest.py` の `collect_ignore_glob`）:
  - `scripts/element_probe_l.py`（L の計測のスクリプト・PR-66C #163）
  - `scripts/l_token_check.py`（YES／NO の token id の確かめ・#162）
  - `tests/test_t66c_element_probe_l.py`（テスト 376〜378）
- 結果と判断の記録は、報告・指示書（`docs/`）に残す
- 接続の説明の local のバックエンドも、既定のモデル（llm-jp）を持たない形にした（`src/explanation_gen.py`。モデルは段階3 で決める）
