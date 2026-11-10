#!/usr/bin/env python3
"""L（含意の判定）の YES／NO の token id を、チャットの型の直後に「YES」「NO」を置いたときのトークン分割で確かめる。

指示書66C（2026-11-07 の KAORU の手順）。トークナイザだけを読み込む（モデルの重みも L の出力も使わない）。
チャットの型は計測と同じ（apply_chat_template・add_generation_prompt=True。Qwen3 は enable_thinking=False）。
回答の位置に実際に現れる id を正とする。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTERED = {"llm-jp/llm-jp-3-1.8b-instruct": ("413b7929df092690b0f9449f3045baaf8deba0a5", 38891, 7937, {}),
              "Qwen/Qwen3-1.7B": ("70d244cc86ccca08cf5af4e1e306ecf908b1ad5e", 14004, 8996, {"enable_thinking": False})}
PROMPT = ("次の2つの文を読んで答えてください。\n必要としている人の像: {n}\n相手の自己紹介の文: {o}\n"
          "この自己紹介の文は、必要としている人の像の役割を満たしていますか。YES か NO の一語だけで答えてください。")


def check(repo, rev, yes, no, kw):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(repo, revision=rev)
    text = tok.apply_chat_template([{"role": "user", "content": PROMPT.format(n="x", o="y")}], tokenize=False,
                                   add_generation_prompt=True, **kw)
    base = tok(text, add_special_tokens=False)["input_ids"]
    split = {}
    for w in ("YES", "NO"):
        ids = tok(text + w, add_special_tokens=False)["input_ids"]
        split[w] = {"ids": ids[len(base):], "pieces": tok.convert_ids_to_tokens(ids[len(base):]), "prefix_unchanged": ids[:len(base)] == base}
    nat_yes, nat_no = split["YES"]["ids"], split["NO"]["ids"]
    return {"model": repo, "revision": rev, "template_tail": text[-24:],
            "registered": {"YES": [yes, tok.convert_ids_to_tokens(yes)], "NO": [no, tok.convert_ids_to_tokens(no)]},
            "after_template": split,
            "natural_single_token": len(nat_yes) == 1 and len(nat_no) == 1,
            "registered_matches": nat_yes == [yes] and nat_no == [no]}


def main():
    out = [check(r, *v) for r, v in REGISTERED.items()]
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
