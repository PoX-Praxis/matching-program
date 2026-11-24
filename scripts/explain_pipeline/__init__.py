"""説明の生成の流れ（指示書80。ラベル v0.3 → 確かめ C8 → 選ぶ・まとめる v0.1 → カード）。

KAORU×PA の試し（4〜6 回目。手元の local/ のスクリプト）を、出力を変えずにリポジトリへ移したもの。
- fields: 人の JSON → 名前の付いた欄
- atoms: 欄 → 節ごとの部品（役目・外すもの）と、組の候補（A・B・C）
- prompts: ラベル v0.3・選ぶ・まとめる v0.1 のプロンプト（docs/prompts/ の本文をそのまま使う）
- checks: ラベルの機械の確かめ（C8）
- card: カードの組み立て（指示書68 の形）
- endpoint: 推論先の窓口（local_http。hf_jobs はジョブの中で local_http を使う）
- runner: 1 組の流れと、同時に送る形・何組かをまとめる形・時間の記録（本番の既定は 1 組ずつ・向きも順に）
- vllm_log: vLLM のログから、数だけを取り出す
- job_main: 回（a 順に・b 組の中で同時・c 組どうしも同時）ごとに走らせ、出力・記録・比べた結果を書く
- hf_job: 推論先 hf_jobs（非公開のジョブで vLLM を起こし、job_main を 127.0.0.1 に向ける）
- synthetic: 架空の組（data/explanation_bench/synthetic_v1.json から。部品の数が違う 3 組）
照合の表（src/match_table.py）を使うので、fugashi が要る（requirements-bench.txt）。本番の画面からは呼ばない。
"""
