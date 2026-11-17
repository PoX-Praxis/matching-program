#!/usr/bin/env python3
"""指示書76 §0 — 借りた機械に渡す一式を作る（リポジトリの公開のものだけ）。

- 決めたファイル（FILES）だけを写す。`local/`・本番の DB・`.env` などは、道が無いので読まない（テスト 510）
- 走らせる前に、架空の組に、人の名前の印（さん・様・氏）・「縛られているもの」・メールアドレス・URL が無いことを確かめる
- 一式の目録（manifest.json: ファイル → sha256）と、その sha256 を返す
使い方: python3 scripts/stage3a_bundle.py <出力のフォルダ>
"""
import hashlib
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = (
    "src/explanation.py", "src/explanation_gen.py", "src/explanation_llamacpp.py",
    "scripts/explanation_measure.py", "scripts/stage3a_run.py",
    "schemas/explanation_card_v0.4.schema.json", "schemas/explanation_card_v0.7.schema.json",
    "schemas/grammar/explanation_p1_out.gbnf", "schemas/grammar/explanation_p2_out.gbnf",
    "docs/prompts/explanation_p1_v0.4.txt", "docs/prompts/explanation_p2_v0.3.txt",
    "data/explanation_bench/synthetic_v1.json",
)
FORBIDDEN_PARTS = ("local", ".env", "secrets", "pox.db")
PERSON_KEYS = {"id", "purpose", "種類", "needs"}
NEED_KEYS = {"ref", "text", "必須"}
PAIR_KEYS = {"id", "person", "need_ref", "need", "must", "other", "expl_label", "note"}
_BAD = re.compile(r"縛られ|さん|様|氏|@|https?://|www\.")


def check_synthetic(doc):
    """架空の組の確かめ。問題の一覧を返す（空なら通る）。文は返さない（どの組か・何の印かだけ）。"""
    bad = []
    for p in doc.get("people") or []:
        if set(p) - PERSON_KEYS:
            bad.append(f"{p.get('id')}: 人の欄が多い")
        for n in p.get("needs") or []:
            if set(n) - NEED_KEYS:
                bad.append(f"{p.get('id')}: 必要の欄が多い")
        for t in [p.get("purpose") or ""] + [n.get("text") or "" for n in p.get("needs") or []]:
            bad += [f"{p.get('id')}: {m}" for m in sorted(set(_BAD.findall(t)))]
    for pr in doc.get("pairs") or []:
        if set(pr) - PAIR_KEYS:
            bad.append(f"{pr.get('id')}: 組の欄が多い")
        for t in (pr.get("need") or "", pr.get("other") or "", pr.get("note") or ""):
            bad += [f"{pr.get('id')}: {m}" for m in sorted(set(_BAD.findall(t)))]
    return bad


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def build(out_dir, root=ROOT):
    for rel in FILES:
        parts = rel.replace("\\", "/").split("/")
        if any(x in parts for x in FORBIDDEN_PARTS):
            raise ValueError(f"渡さないものが一式に入っている: {rel}")
    with open(os.path.join(root, "data", "explanation_bench", "synthetic_v1.json"), encoding="utf-8") as f:
        problems = check_synthetic(json.load(f))
    if problems:
        raise ValueError(f"架空の組に渡さない印がある: {problems}")
    manifest = {}
    for rel in FILES:
        src = os.path.join(root, rel)
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        manifest[rel] = sha256_file(dst)
    blob = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        f.write(blob)
    return {"files": manifest, "manifest_sha256": hashlib.sha256(blob.encode("utf-8")).hexdigest()}


if __name__ == "__main__":
    res = build(sys.argv[1])
    print(json.dumps({"files": len(res["files"]), "manifest_sha256": res["manifest_sha256"]}, ensure_ascii=False))
