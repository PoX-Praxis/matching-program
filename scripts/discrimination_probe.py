#!/usr/bin/env python3
"""指示書64 §3 — 照合の見分ける力の調査（本人が手元で実行する）。

閾値 0.70（g = (1 + cos) / 2。cos 0.40 にあたる）が「内容が噛み合う」ことを見分けているのか、無関係な日本語の
文どうしでも出る値なのかを確かめる。**本番に触れない**（DB・台帳・トークンを使わない）。**判定は変えない**。

- 埋め込みは本番と同じ `nomic-emb-v2`（nomic-ai/nomic-embed-text-v2-moe）。前処理も本番と同じ経路を呼ぶ:
  文 → necessities._default_embed（PII のマスク → 役割ごとの接頭辞 → L2 正規化）。v4 の現状の全文は
  build_vectors と同じ（欄ごとにマスク → state_concat → passage）
- 判定は本番の関数（matcher_v5.judge・judge_direction）をそのまま呼ぶ
- 比べる文: 架空の文（scripts/probe_data/。コミット済み）と、KAORU・PA の公開の文（手元のファイルから読む。
  **リポジトリに入れない**。local/ は .gitignore 済み）

入力（既定の置き場所は local/）:
  --kaoru    local/kaoru_v5.json          KAORU が登録に使った v5 の JSON（目的・必要像・数値・与え像を使う）
  --audit    local/audit_sentences.json   監査の応答（PA の現状の全文と、再現の確かめに使う g）。無ければ D 群は省く
  --pa-state local/pa_state.json          PA の現状の 4 欄 {"持っているもの", "できること_型", "縛られているもの", "未分類"}
                                          （任意。無ければ監査の応答から組み立て、g が再現する組み合わせを選ぶ）
埋め込みの計算:
  既定はこの PC で nomic_server/model.py の NomicEmbedder を読み込む（初回はモデルを取得する。約 1GB）。
  --endpoint http://localhost:8002/embed を付けると、手元で起動した nomic_server を使う。
  --dtype は本番の nomic_server の NOMIC_TORCH_DTYPE と同じにする（既定 bfloat16）。再現が合わなければ float32 を試す。
出力: local/probe/probe_result.md と probe_result.json（生テキスト・根拠・数値の申告値は書かない）
"""
import argparse
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "scripts", "probe_data")
MODEL_TAG = "nomic-emb-v2"
STATE_SLOTS = (("state_have", "持っているもの", "持っているもの"), ("state_can_type", "できること_型", "できること（型）"),
               ("state_bound", "縛られているもの", "縛られているもの"), ("state_unsorted", "未分類", "未分類"))


# ── 埋め込み（本番と同じ経路）────────────────────────────────────────────────────────
def make_embedder(endpoint=None, dtype=None, encode=None, strict=True):
    """(emb(text, role), emb_state(slots), 条件の説明) を返す。encode を渡すとそれで計算する（テスト用）。"""
    if strict:
        os.environ["POX_EMBED_BACKEND"] = "nomic"
        os.environ["POX_EMBED_MODEL_TAG"] = MODEL_TAG
        if endpoint:
            os.environ["POX_NOMIC_ENDPOINT"] = endpoint
        if dtype:
            os.environ["NOMIC_TORCH_DTYPE"] = dtype
    sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "nomic_server"))
                    if p not in sys.path]
    import embedding_config as cfg
    import embedding_service as es
    if strict and (cfg.MODEL_TAG != MODEL_TAG or cfg.BACKEND != "nomic"):
        raise SystemExit(f"埋め込みの設定が本番と違います（backend={cfg.BACKEND} tag={cfg.MODEL_TAG}）。"
                         "このスクリプトは他のモジュールより先に読み込まれる必要があります。")
    mode = "注入（テスト）"
    if encode is not None:
        es._encode = encode
    elif not endpoint:
        from model import NomicEmbedder                     # nomic_server/model.py（本番の推論サーバーと同じ読み込み）
        embedder = NomicEmbedder()
        es._encode = embedder.encode
        mode = f"この PC で読み込み（{embedder.model_name}・dtype={os.environ.get('NOMIC_TORCH_DTYPE', 'bfloat16')}）"
    else:
        mode = f"手元の nomic_server（{endpoint}）"
    from necessities import _default_embed                  # 本番の文単位ベクトルと同じ（マスク → 接頭辞 → 正規化）
    from pii_redaction import redact_text
    cache = {}

    def emb(text, role):
        key = (text, role)
        if key not in cache:
            cache[key] = _default_embed(text, role)
        return cache[key]

    def emb_state(slots):
        """v4 の現状の全文（build_vectors と同じ: 欄ごとにマスク → state_concat → passage）。"""
        prof = {k: redact_text(slots.get(jp) or "") for k, jp, _ in STATE_SLOTS}
        text = es.state_concat(prof)
        key = ("__state__", text)
        if key not in cache:
            cache[key] = es.embed(text, "passage")[0]
        return cache[key]

    info = {"model_tag": cfg.MODEL_TAG, "backend": cfg.BACKEND, "mode": mode,
            "prefix": dict(cfg.PREFIX), "normalize": "L2（embedding_service.embed）"}
    return emb, emb_state, info


def g_of(a, b):
    from embedding_service import cosine, guard
    return guard(cosine(a, b))


# ── 入力 ─────────────────────────────────────────────────────────────────────────
def load_kaoru(path):
    """KAORU の v5 の JSON から、目的（向かう先・必要像・門の値）と与え像の文だけを取り出す（生テキスト・根拠は読まない）。"""
    from profile_view import parse_registration_text_report
    with open(path, encoding="utf-8") as f:
        doc, _ = parse_registration_text_report(f.read())
    purposes = []
    for p in doc.get("purposes") or []:
        nums = p.get("数値") or {}
        purposes.append({"dest": str(p.get("向かう先") or ""),
                         "needs": [{"text": str(x.get("文") or ""), "must": bool(x.get("必須"))}
                                   for x in (p.get("必要像") or [])],
                         "gate_s": float(nums.get("gate_s") or 0.0), "gate_u": float(nums.get("gate_u") or 0.0)})
    offers = [str(x.get("文") or "") for x in (doc.get("与え像") or []) if str(x.get("文") or "").strip()]
    return {"purposes": purposes, "offers": offers}


def load_data():
    with open(os.path.join(DATA, "probe_sentences.json"), encoding="utf-8") as f:
        sents = json.load(f)
    with open(os.path.join(DATA, "probe_people.json"), encoding="utf-8") as f:
        people = json.load(f)["people"]
    return sents, people


def audit_rows(audit):
    """監査の応答から、PA（v4）の現状の全文と比べた行（必要像の文・g・表示の欄）を取り出す。"""
    rows, seen = [], set()
    for e in ((audit or {}).get("v5") or {}).get("sentences") or []:
        if e.get("direction") != "a_to_b":
            continue
        for s in e.get("sentences") or []:
            if s.get("best_source") == "現状（全文）" and s["need"] not in seen:
                seen.add(s["need"])
                rows.append(s)
    return rows


def slot_candidates(rows):
    """監査の全文（欄を " / " でつないだもの）から、欄の割り当ての候補を作る（表示の欄で 1 つを固定する）。"""
    from itertools import combinations
    if not rows:
        return []
    parts = rows[0]["best"].split(" / ")
    df = rows[0].get("display_field") or {}
    out = []
    for pos in combinations(range(len(STATE_SLOTS)), len(parts)):
        slots = {STATE_SLOTS[i][1]: parts[k] for k, i in enumerate(pos)}
        if df.get("field") and df.get("text"):
            jp = next((jp for _, jp, label in STATE_SLOTS if label == df["field"]), None)
            if slots.get(jp) != df["text"]:
                continue
        out.append(slots)
    return out


# ── 計測 ─────────────────────────────────────────────────────────────────────────
def stats(values, th):
    if not values:
        return {"n": 0}
    return {"n": len(values), "min": round(min(values), 3), "median": round(statistics.median(values), 3),
            "max": round(max(values), 3), "pass": sum(1 for v in values if v >= th)}


def need_keys(kaoru):
    return [(f"{i}-{j}", n) for i, p in enumerate(kaoru["purposes"], 1) for j, n in enumerate(p["needs"], 1)]


def side_of(purposes, offers, emb):
    """matcher_v5 の照合の片側（_side_of の v5 の形）。"""
    ps = [{"purpose_id": f"p{i}", "label": p["dest"],
           "needs": [{"text": n["text"], "vec": emb(n["text"], "query"), "required": n["must"]} for n in p["needs"]],
           "dest_vec": emb(p["dest"], "symmetric"), "gate_s": p["gate_s"], "gate_u": p["gate_u"]}
          for i, p in enumerate(purposes, 1)]
    return {"purposes": ps, "offers": [{"text": t, "vec": emb(t, "passage")} for t in offers],
            "has_offer": bool(offers), "dests": [p["dest_vec"] for p in ps]}


def run(kaoru, sents, people, emb, emb_state, audit=None, pa_state=None):
    from matcher_v5 import SENTENCE_JUDGE_THRESHOLD as TH
    from matcher_v5 import judge_direction
    keys = need_keys(kaoru)
    qv = {k: emb(n["text"], "query") for k, n in keys}
    groups = {"A": [(t, None) for t in sents["A"]], "B": [(t, None) for t in sents["B"]],
              "C": [(c["text"], c["target"]) for c in sents["C"]]}
    gv = {name: [(t, tg, emb(t, "passage")) for t, tg in items] for name, items in groups.items()}

    # (2) 群ごとの分布
    dist = {}
    for name, items in gv.items():
        dist[name] = {k: stats([g_of(qv[k], v) for _, _, v in items], TH) for k, _ in keys}
        allv = [g_of(qv[k], v) for k, _ in keys for _, _, v in items]
        dist[name]["_all"] = stats(allv, TH)
    c_target = {k: stats([g_of(qv[k], v) for _, tg, v in gv["C"] if tg == k], TH) for k, _ in keys}

    # D 群（実際の相手 PA）: 全文と各欄。監査の g を再現できる欄の割り当てを選ぶ
    d, repro = None, None
    rows = audit_rows(audit)
    cands = [pa_state] if pa_state else slot_candidates(rows)
    if cands:
        scored = []
        for slots in cands:
            sv = emb_state(slots)
            hits = [(r["need"], r["g"], round(g_of(emb(r["need"], "query"), sv), 2)) for r in rows]
            scored.append((sum(1 for _, a, b in hits if abs(a - b) < 0.0051), slots, sv, hits))
        scored.sort(key=lambda x: -x[0])
        best_hits, slots, sv, hits = scored[0]
        repro = {"rows": len(hits), "matched": best_hits, "candidates": len(cands),
                 "slots_used": [jp for _, jp, _ in STATE_SLOTS if slots.get(jp)],
                 "pairs": [{"need": n[:24], "audit_g": a, "probe_g": b} for n, a, b in hits]}
        items = [("全文", sv)] + [(f"欄: {jp}", emb(slots[jp], "passage")) for _, jp, _ in STATE_SLOTS if slots.get(jp)]
        d = {label: {k: round(g_of(qv[k], v), 3) for k, _ in keys} for label, v in items}

    # (3) 何人に通るか（現在の判定）
    ks = side_of(kaoru["purposes"], kaoru["offers"], emb)
    people_out = []
    for person in people:
        ps = side_of([{"dest": p["向かう先"], "needs": [{"text": x["文"], "must": bool(x.get("必須"))} for x in p["必要像"]],
                       "gate_s": p["gate_s"], "gate_u": p["gate_u"]} for p in person["purposes"]],
                     person["与え像"], emb)
        a = []
        for p in ks["purposes"]:
            ok, _, info = judge_direction(p, ps)
            a.append({"ok": ok, "gate": info["gate"], "gate_passed": info["gate_passed"]})
        b = [judge_direction(q, ks)[0] for q in ps["purposes"]]
        people_out.append({"id": person["id"], "type": person["type"], "a": a, "b": any(b),
                           "mutual": any(x["ok"] for x in a) and any(b)})
    types = ("fit", "topic", "unrelated")
    total = {t: sum(1 for p in people if p["type"] == t) for t in types}
    table = []
    for i in range(len(ks["purposes"])):
        table.append({"row": f"KAORU の目的 {i + 1} → 相手",
                      **{t: sum(1 for p in people_out if p["type"] == t and p["a"][i]["ok"]) for t in types},
                      "ids": [p["id"] for p in people_out if p["a"][i]["ok"]]})
    table.append({"row": "相手 → KAORU", **{t: sum(1 for p in people_out if p["type"] == t and p["b"]) for t in types},
                  "ids": [p["id"] for p in people_out if p["b"]]})
    table.append({"row": "互いに埋める", **{t: sum(1 for p in people_out if p["type"] == t and p["mutual"]) for t in types},
                  "ids": [p["id"] for p in people_out if p["mutual"]]})

    # (4) 文単位と全文どうし（C と B）
    sent_cmp = []
    for k, _ in keys:
        c_best = max(g_of(qv[k], v) for _, _, v in gv["C"])
        b_best = max(g_of(qv[k], v) for _, _, v in gv["B"])
        sent_cmp.append({"need": k, "C_best": round(c_best, 3), "B_best": round(b_best, 3), "gap": round(c_best - b_best, 3)})
    full_cmp = []
    for i, p in enumerate(kaoru["purposes"], 1):
        c_texts = [t for t, tg, _ in gv["C"] if tg and tg.startswith(f"{i}-")]
        if not c_texts:
            continue
        b_texts = [t for t, _, _ in gv["B"]][:len(c_texts)]
        q = emb("\n".join(n["text"] for n in p["needs"]), "query")
        gc = g_of(q, emb("\n".join(c_texts), "passage"))
        gb = g_of(q, emb("\n".join(b_texts), "passage"))
        full_cmp.append({"purpose": i, "C_full": round(gc, 3), "B_full": round(gb, 3), "gap": round(gc - gb, 3),
                         "sentences": len(c_texts)})
    return {"threshold": TH, "needs": [{"key": k, "must": n["must"], "head": n["text"][:24]} for k, n in keys],
            "dist": dist, "c_target": c_target, "d": d, "repro": repro,
            "people": {"total": total, "table": table, "detail": people_out},
            "sentence_vs_full": {"sentence": sent_cmp, "full": full_cmp}}


# ── 出力 ─────────────────────────────────────────────────────────────────────────
def to_markdown(res, info):
    th = res["threshold"]
    L = ["# 照合の見分ける力の調査（指示書64 §3）— 手元で実行した結果", "",
         "## 条件", "",
         f"- モデル: `{info['model_tag']}`（{info['mode']}）",
         f"- 接頭辞: 必要像（query）=`{info['prefix'].get('query')}` / 与え像・現状（passage）=`{info['prefix'].get('passage')}`"
         f" / 向かう先（symmetric）=`{info['prefix'].get('symmetric')}`",
         f"- 正規化: {info['normalize']}。g = (1 + cos) / 2。判定は g ≥ {th}", "",
         "## KAORU の必要像（番号）", "", "| 番号 | 欠かせない | 文（先頭 24 字） |", "|---|---|---|"]
    L += [f"| {n['key']} | {'✓' if n['must'] else ''} | {n['head']}… |" for n in res["needs"]]
    L += ["", f"## (2) 群ごとの g（最小 / 中央 / 最大 / {th} 以上の件数）", ""]
    for name, title in (("A", "A 無関係（20）"), ("B", "B 話題が近く役割が違う（10）"), ("C", "C 役割が合う（10）")):
        L += [f"### {title}", "", "| 必要像 | 最小 | 中央 | 最大 | 通る件数 |", "|---|---|---|---|---|"]
        for n in res["needs"]:
            s = res["dist"][name][n["key"]]
            L.append(f"| {n['key']} | {s['min']} | {s['median']} | {s['max']} | {s['pass']}/{s['n']} |")
        s = res["dist"][name]["_all"]
        L += [f"| 全体 | {s['min']} | {s['median']} | {s['max']} | {s['pass']}/{s['n']} |", ""]
    L += ["### C のうち、その必要像を満たすつもりで書いた文（target）", "", "| 必要像 | 件数 | 最大 | 通る件数 |", "|---|---|---|---|"]
    for n in res["needs"]:
        s = res["c_target"][n["key"]]
        L.append(f"| {n['key']} | {s['n']} | {s.get('max', '—')} | {s.get('pass', 0)}/{s['n']} |")
    if res["d"]:
        r = res["repro"]
        L += ["", "### D 実際の相手（PA の現状）", "",
              f"監査の g の再現: {r['matched']}/{r['rows']} 行が一致（欄の割り当ての候補 {r['candidates']} 件から最も合うもの。"
              f"使った欄: {'・'.join(r['slots_used'])}）", "",
              "| 必要像（先頭） | 監査の g | この計測の g |", "|---|---|---|"]
        L += [f"| {p['need']}… | {p['audit_g']} | {p['probe_g']} |" for p in r["pairs"]]
        L += ["", "| 相手の文 | " + " | ".join(n["key"] for n in res["needs"]) + " |",
              "|---|" + "---|" * len(res["needs"])]
        L += [f"| {label} | " + " | ".join(str(v[n["key"]]) for n in res["needs"]) + " |" for label, v in res["d"].items()]
    p = res["people"]
    L += ["", "## (3) 何人に通るか（架空の v5 プロフィール "
          f"{sum(p['total'].values())} 人: 合う {p['total']['fit']}・話題だけ {p['total']['topic']}・無関係 {p['total']['unrelated']}）", "",
          "| 方向 | 合う | 話題だけ | 無関係 | 通った人 |", "|---|---|---|---|---|"]
    L += [f"| {t['row']} | {t['fit']} | {t['topic']} | {t['unrelated']} | {'・'.join(t['ids']) or '—'} |" for t in p["table"]]
    L += ["", "## (4) 文単位と全文どうし（C と B の差）", "", "### 文単位（必要像ごとに、C・B それぞれで最も高い g）", "",
          "| 必要像 | C の最大 | B の最大 | 差 |", "|---|---|---|---|"]
    L += [f"| {s['need']} | {s['C_best']} | {s['B_best']} | {s['gap']} |" for s in res["sentence_vs_full"]["sentence"]]
    L += ["", "### 全文どうし（目的の必要像をつないだ文 × C／B の文をつないだ文。v4 と同じ比べ方）", "",
          "| 目的 | C の全文 | B の全文 | 差 | つないだ文の数 |", "|---|---|---|---|---|"]
    L += [f"| {s['purpose']} | {s['C_full']} | {s['B_full']} | {s['gap']} | {s['sentences']} |"
          for s in res["sentence_vs_full"]["full"]]
    L += ["", "（この表には、生テキスト・根拠・門の数値の申告値は入っていない）", ""]
    return "\n".join(L)


def main(argv=None, encode=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5.json"))
    ap.add_argument("--audit", default=os.path.join(ROOT, "local", "audit_sentences.json"))
    ap.add_argument("--pa-state", default=os.path.join(ROOT, "local", "pa_state.json"))
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--dtype", default=None, help="bfloat16（既定）／float32")
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "probe"))
    args = ap.parse_args(argv)
    emb, emb_state, info = make_embedder(args.endpoint, args.dtype, encode=encode, strict=encode is None)
    kaoru = load_kaoru(args.kaoru)
    sents, people = load_data()
    audit = None
    if args.audit and os.path.exists(args.audit):
        with open(args.audit, encoding="utf-8") as f:
            audit = json.load(f)
    pa_state = None
    if args.pa_state and os.path.exists(args.pa_state):
        with open(args.pa_state, encoding="utf-8") as f:
            pa_state = json.load(f)
    res = run(kaoru, sents, people, emb, emb_state, audit=audit, pa_state=pa_state)
    os.makedirs(args.out, exist_ok=True)
    md = to_markdown(res, info)
    with open(os.path.join(args.out, "probe_result.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(args.out, "probe_result.json"), "w", encoding="utf-8") as f:
        json.dump({"info": info, **res}, f, ensure_ascii=False, indent=1)
    print(f"保存しました: {os.path.join(args.out, 'probe_result.md')}")
    if res["repro"]:
        print(f"監査の g の再現: {res['repro']['matched']}/{res['repro']['rows']} 行が一致")
    return res


if __name__ == "__main__":
    main()
