"""指示書76 — 段階3a の準備（llama.cpp のバックエンド・版の記録・計測の出力・借りた機械に渡す一式）。テスト 507〜512。

507（§9-1）llama.cpp を呼ぶ道が、決まった応答を返す偽のサーバーで、段1 → 確かめ → 段2 → 確かめを通る（候補は Nemotron の 1 つ）
508（§9-2）版の記録（§1-4）が、説明の鍵（version_key）に入る
509（§9-3）計測の出力に、§2 の数と設定一式が書かれる。ログ（標準出力）に文を出さない
510（§9-4）借りた機械に渡す入力を作る道が、local/ を読まない。ジョブに鍵を渡さない・入れ物は digest で固定
511 文法（プロンプトの外の縛り）とプロンプトの sha256・橋渡しの 12 組の選び方
512 決定性の比べ方（出力が一字ずつ同じか）
513 報告76 順1 の確認: LLM-jp-4 は外した・チャットの型の日付と時刻の確かめ・予算 10 ドルの上限
"""
import builtins
import io
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")]

import explanation as X  # noqa: E402
import explanation_gen as G  # noqa: E402
import explanation_llamacpp as L  # noqa: E402
import explanation_measure as M  # noqa: E402
import stage3a_bundle as B  # noqa: E402
import stage3a_jobs as J  # noqa: E402
import stage3a_run as R  # noqa: E402

SHA = "a" * 64
TITLE = "地域の小さな店が続けられる仕組みをつくりたい"
NEED = "売上や在庫の記録を、店の人と一緒に担ってきた人。"
OFFER = "店の売上や在庫の記録を、三年担当してきました。"


def _blocks(text):
    """プロンプトの中の JSON の塊（N・O・段2 の入力）を取り出す。"""
    dec, out, i = json.JSONDecoder(), [], 0
    while (i := text.find("{", i)) >= 0:
        try:
            obj, end = dec.raw_decode(text, i)
            out.append(obj)
            i = end
        except ValueError:
            i += 1
    return out


class FakeServer:
    """llama-server の偽物（/apply-template と /completion）。段1・段2 の答えは規則の応答役で作る。"""
    def __init__(self):
        self.requests = []

    template = "{{ messages[0]['content'] }}"                                         # 日付・時刻を入れない型

    def __call__(self, req, timeout=None):
        path = req.full_url.split("8080", 1)[1]
        if path == "/props":
            return io.BytesIO(json.dumps({"chat_template": self.template}).encode("utf-8"))
        body = json.loads(req.data.decode("utf-8"))
        self.requests.append((path, body))
        if path == "/apply-template":
            out = {"prompt": "<S>" + body["messages"][0]["content"] + "<A>"}
        else:
            blocks = _blocks(body["prompt"])
            if "meanings" not in body["grammar"]:                                    # 段1 の文法
                n = next(b for b in blocks if "purposes" in b)
                o = next(b for b in blocks if "与え像" in b)
                ans = G.rule_responder(1, {"N": n, "O": o})
            else:
                ans = G.rule_responder(2, next(b for b in blocks if "purposes" in b))
            prefix = "<|channel|>final<|message|>" if "<|channel|>" in body["grammar"].splitlines()[0] else ""
            content = prefix + json.dumps(ans, ensure_ascii=False)
            out = {"content": content, "tokens_evaluated": 100, "tokens_predicted": 20, "stop_type": "eos",
                   "timings": {"prompt_ms": 1.5, "predicted_ms": 2.5},
                   "completion_probabilities": [{"top_logprobs": [{"logprob": -0.01}, {"logprob": -3.0}]},
                                                {"top_logprobs": [{"logprob": -0.6}, {"logprob": -0.8}]}]}
        return io.BytesIO(json.dumps(out, ensure_ascii=False).encode("utf-8"))


def _n():
    return {"subject": "n", "purposes": [{"purpose_id": "p1", "title": TITLE, "means": "", "kind": "補完",
                                          "needs": [{"text": NEED, "type": "", "must": True}]}]}


def _backend(profile, fake=None, record=None):
    return L.LlamaCppBackend("http://127.0.0.1:8080", profile, SHA, record=record, opener=fake or FakeServer())


# ── 507（§9-1）偽のサーバーで、今のパイプラインを通る ─────────────────────────────────────────
@pytest.mark.parametrize("profile", sorted(L.PROFILES))
def test_t507_fake_server_through_pipeline(profile):
    fake = FakeServer()
    be = _backend(profile, fake)
    trace = []
    res = G.generate_direction(_n(), M.one_sentence_person(OFFER), be, trace=trace)
    p = res["direction"]["purposes"][0]
    assert p["status"] == "explained" and [e["relation"] for e in p["elements"]] == ["R1"], res["problems"]
    assert p["meaning"] and p["meaning"]["text"].endswith("可能性があります。")
    assert X.showable(X.check(X.assemble(res["direction"], None, True))[0])
    assert M.leak_violations(res["direction"], trace[0]["stage2_payload"]) == 0
    paths = [r[0] for r in fake.requests]
    assert paths == ["/apply-template", "/completion", "/apply-template", "/completion"]          # 段1 → 段2
    tpl, comp = fake.requests[0][1], fake.requests[1][1]
    assert tpl["messages"] == [{"role": "user", "content": G.fill_prompt1(G.need_side(_n())["block"],
                                                                            G.offer_side(M.one_sentence_person(OFFER))["block"])}]
    assert tpl["chat_template_kwargs"] == {"enable_thinking": False}                                # 考える機能を切る
    assert comp["grammar"].startswith("root ::= body\n")
    assert comp["prompt"].startswith("<S>") and comp["prompt"].endswith("<A>")                      # 型で描いたものを送る
    for k, v in {"temperature": 0.0, "top_k": 1, "top_p": 1.0, "min_p": 0.0, "seed": 0, "repeat_penalty": 1.0,
                 "repeat_last_n": 0, "presence_penalty": 0.0, "frequency_penalty": 0.0, "n_probs": 2,
                 "cache_prompt": False, "stream": False, "n_predict": 1536}.items():
        assert comp[k] == v, k
    assert fake.requests[3][1]["n_predict"] == 512 and "meanings" in fake.requests[3][1]["grammar"]
    c = be.calls[0]
    assert c["prompt_sha256"] == L.sha256_text(comp["prompt"]) and c["stage"] == 1
    assert c["margin"]["under_0_1"] == 1 and 0.0 < c["margin"]["min"] < 0.1                         # 選ばれた字と次点の差
    assert c["timings"] == {"prompt_ms": 1.5, "predicted_ms": 2.5} and c["tokens_predicted"] == 20


def test_t507_bad_output_is_kind_a():
    class Broken(FakeServer):
        def __call__(self, req, timeout=None):
            if req.full_url.endswith("/completion"):
                self.requests.append(("/completion", {}))
                return io.BytesIO(json.dumps({"content": "（文だけ）"}, ensure_ascii=False).encode("utf-8"))
            return super().__call__(req, timeout)
    r = M.run_pair(_n(), M.one_sentence_person(OFFER), _backend("nemotron-9b-ja", Broken()))
    assert r["result"] == "a"


# ── 508（§9-2）版の記録が、説明の鍵に入る ─────────────────────────────────────────────────
def test_t508_version_record_in_key():
    rec = {"image": J.IMAGES["runtime"], "gpu": "NVIDIA L4", "driver": "550.0", "cuda": "CUDA Version: 12.4"}
    be = _backend("nemotron-9b-ja", record=rec)
    v = G.versions(be)
    assert v["model"] == "nvidia/NVIDIA-Nemotron-Nano-9B-v2-Japanese@3979dd16634988c34cc3bd911583c51e6a731d10:Q4_K_M"
    assert v["revision"] == SHA                                                                     # モデルのファイルの sha256
    d = v["decoding"]
    assert d["llamacpp"]["commit"] == "b1b770d90d94cad9e9ea5b993bdbc383a522fb28" and d["llamacpp"]["tag"] == "v1.0.0"
    assert d["server"]["parallel"] == 1 and d["server"]["ctx_size"] == 8192
    assert d["grammar_sha256"] == L.grammar_sha("nemotron-9b-ja") and d["image"] == J.IMAGES["runtime"] and d["gpu"] == "NVIDIA L4"
    assert v["prompt1_sha"] == G.PROMPT1_SHA and v["prompt2_sha"] == G.PROMPT2_SHA
    key = G.version_key(be)
    others = [_backend("nemotron-9b-ja", record={**rec, "image": "other@sha256:0"}),
              L.LlamaCppBackend("http://x:8080", "nemotron-9b-ja", "b" * 64, record=rec),
              _backend("nemotron-9b-ja", record={**rec, "driver": "560.0"})]
    for o in others:
        assert G.version_key(o) != key
    changed = _backend("nemotron-9b-ja", record=rec)
    changed.decoding = {**changed.decoding, "llamacpp": {**L.LLAMACPP, "commit": "0" * 40}}
    assert G.version_key(changed) != key                                                            # llama.cpp の版
    changed = _backend("nemotron-9b-ja", record=rec)
    changed.decoding = {**changed.decoding, "grammar_sha256": {"1": "x", "2": "y"}}
    assert G.version_key(changed) != key                                                            # 文法
    with pytest.raises(ValueError):
        L.LlamaCppBackend("http://x", "nemotron-9b-ja", "")                                        # sha256 が無いと動かない
    with pytest.raises(ValueError):
        L.LlamaCppBackend("http://x", "gpt-oss-20b", SHA)                                           # 候補を足さない


# ── 509（§9-3）計測の出力に、§2 の数と設定一式が書かれる ─────────────────────────────────────────
def test_t509_runner_writes_counts_and_settings(tmp_path, capsys):
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps({"image": J.IMAGES["runtime"], "gpu": "NVIDIA L4", "startup_seconds": 12.5}), encoding="utf-8")
    out = tmp_path / "result.json"
    res = R.main(["--profile", "nemotron-9b-ja", "--gguf-sha256", SHA, "--ids", "all", "--repeat", "2",
                  "--record", str(rec), "--out", str(out)],
                 backend_factory=lambda: _backend("nemotron-9b-ja", record={"image": J.IMAGES["runtime"]}))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["synthetic_sha256"] == "018235232543a730603eaad00b43a4b1afe3de413bf76247ccd651f8003eea37"
    assert saved["prompt_sha256"] == {"1": G.PROMPT1_SHA, "2": G.PROMPT2_SHA}
    assert saved["grammar_sha256"] == L.grammar_sha("nemotron-9b-ja") and saved["gguf_sha256"] == SHA
    assert saved["record"]["startup_seconds"] == 12.5 and saved["decoding"]["temperature"] == 0.0
    assert len(saved["runs"]) == 2
    c = saved["runs"][0]["counts"]
    for k in ("errors", "recall", "rule_excluded", "relation_match", "by_relation", "kinds", "reasons", "leak", "open"):
        assert k in c, k
    assert c["classes"] == {"説明できる": 28, "説明できない": 25, "判断できない": 7, "対象外": 0}
    assert c["errors"]["n"] == 25 and c["recall"]["n"] == 24 and c["rule_excluded"]["n"] == 4 and c["leak"] == 0
    assert saved["unrelated"]["n"] == 10                                                            # 「無関係」の 10 組
    assert saved["determinism"]["runs"] == 2 and saved["determinism"]["all_same"] is True
    calls = saved["runs"][0]["calls"]
    assert calls and all({"id", "stage", "prompt_sha256", "output", "output_sha256", "seconds", "margin"} <= set(x)
                         for x in calls)
    log = capsys.readouterr().out
    syn = R.load_synthetic()
    for p in syn["pairs"]:
        for t in (p["need"], p["other"]):
            assert t[:10] not in log                                                                # ログに文を出さない
    assert "[stage3a] run 1/2" in log


# ── 510（§9-4）local/ を読まない・鍵を渡さない・入れ物は digest ──────────────────────────────────────
def test_t510_bundle_does_not_read_local(tmp_path, monkeypatch):
    real_open = builtins.open

    def guarded(path, *a, **k):
        p = os.path.abspath(str(path))
        assert os.sep + "local" + os.sep not in p and not p.endswith(os.sep + "local"), p
        return real_open(path, *a, **k)
    monkeypatch.setattr(builtins, "open", guarded)
    res = B.build(str(tmp_path / "bundle"))
    monkeypatch.setattr(builtins, "open", real_open)
    assert set(res["files"]) == set(B.FILES) and len(res["manifest_sha256"]) == 64
    for rel in B.FILES:
        assert not rel.startswith("local/") and ".env" not in rel and not rel.endswith(".db")
    got = sorted(os.path.relpath(os.path.join(d, f), tmp_path / "bundle") for d, _, fs in os.walk(tmp_path / "bundle") for f in fs)
    assert got == sorted(list(B.FILES) + ["manifest.json"])
    # 架空の組の確かめ（人の名前の印・縛られているもの・連絡先が無い）
    syn = json.load(open(os.path.join(ROOT, "data", "explanation_bench", "synthetic_v1.json"), encoding="utf-8"))
    assert B.check_synthetic(syn) == []
    bad = json.loads(json.dumps(syn))
    bad["pairs"][0]["other"] = "佐藤さんが縛られている"
    bad["pairs"][1]["email"] = "x"
    found = B.check_synthetic(bad)
    assert {"S01: さん", "S01: 縛られ", "S02: 組の欄が多い"} <= set(found), found
    # 走らせるスクリプトにも、手元のデータへの道が無い
    for f in ("scripts/stage3a_run.py", "src/explanation_llamacpp.py"):
        src = open(os.path.join(ROOT, f), encoding="utf-8").read()
        assert '"local"' not in src and "'local'" not in src and "local/" not in src.replace("`local/`", ""), f
    src = open(os.path.join(ROOT, "scripts", "stage3a_bundle.py"), encoding="utf-8").read()
    assert [ln for ln in src.splitlines() if '"local"' in ln] == [ln for ln in src.splitlines()
                                                                  if ln.startswith("FORBIDDEN_PARTS")]  # 渡さないものの印だけ


def test_t510_jobs_have_no_token_and_pinned_images():
    specs = [J.prepare_spec("ns")] + [J.run_spec("ns", p, SHA, "trial", 1, "b" * 64, "trial") for p in L.PROFILES]
    blob = json.dumps(specs, ensure_ascii=False)
    for bad in ("HF_TOKEN", "secrets", "Authorization", "Bearer", "token=", "--token"):
        assert bad not in blob, bad                                                                 # ジョブに鍵を渡さない
    assert not re.search(r"hf_[A-Za-z0-9]{20,}", blob)                                              # トークンの形の文字列も無い
    for s in specs:
        assert re.fullmatch(r"nvidia/cuda:[\w.\-]+@sha256:[0-9a-f]{64}", s["image"])                 # digest で固定
        assert s["timeout"] in ("100m", "40m", "150m")                                               # 時間の上限
        for v in s["volumes"]:
            assert v["type"] in ("bucket", "model")
            if v["type"] == "model":
                assert v["read_only"] and v["revision"] == L.PROFILES["nemotron-9b-ja"]["official_revision"]
    assert specs[0]["flavor"] == "cpu-upgrade" and all(s["flavor"] == "l4x1" for s in specs[1:])  # L4 1 枚（T4 は使わない）
    assert "find /tmp/nemo -name '*.py'" in J.PREPARE_SH and "trust_remote_code" not in J.PREPARE_SH  # リモートのコードを置かない
    assert f"--branch {L.LLAMACPP['tag']}" in J.PREPARE_SH and L.LLAMACPP["commit"] in J.PREPARE_SH
    urls = re.findall(r"https://huggingface\.co/[^\s\"']+", J.PREPARE_SH)
    official = f"https://huggingface.co/{L.PROFILES['nemotron-9b-ja']['official']}/resolve/" \
               f"{L.PROFILES['nemotron-9b-ja']['official_revision']}/"
    assert urls and all(u.startswith(official) for u in urls), urls                                 # 公式・決めた revision だけ
    assert all(not n.endswith(".py") for n in J.NEMOTRON_FILES) and len(J.NEMOTRON_FILES) == 11     # リモートのコードは取らない
    assert all(k in ("sha256", "git") and len(v) in (64, 40) for k, v in J.NEMOTRON_FILES.values())  # 落としたものを確かめる
    assert "> /tmp/server.log" in J.RUN_SH and "--log-verbosity 1" in J.RUN_SH                      # サーバーのログはジョブのログに出さない


# ── 511 文法・プロンプト・橋渡しの組 ───────────────────────────────────────────────────────
def test_t511_grammar_prompts_and_bridge():
    assert G.PROMPT1_SHA == "c222f021707e8ab92f976d328ce7dbb56aa01051ef564863b768e6bda4a7f252"     # プロンプトは変えない
    p1 = G.prompt_text(1)
    out_fmt = p1[p1.index("# 出力"):]
    g1 = open(os.path.join(L.GRAMMAR_DIR, L.GRAMMAR_FILES[1]), encoding="utf-8").read()
    for key in re.findall(r'"([a-z_]+)":', out_fmt):
        assert f'\\"{key}\\"' in g1, key                                                            # 出力の項目がすべて文法にある
    for v in ("explained", "not_covered", "R1", "R2", "R3", "R4"):
        assert f'\\"{v}\\"' in g1
    g2 = open(os.path.join(L.GRAMMAR_DIR, L.GRAMMAR_FILES[2]), encoding="utf-8").read()
    for key in ("meanings", "purpose_ref", "text", "cites"):
        assert f'\\"{key}\\"' in g2
    assert "root ::=" not in g1 and "root ::=" not in g2                                            # 始まりは候補ごと
    shas = L.grammar_sha("nemotron-9b-ja")
    assert shas == {"1": "daec68ca5d7be9d54fcaebe0311e74284bfd6ce54e2ebca8d6236f41fcfe1b7a",
                    "2": "1d5accc7c07eb76b971d6f89a12c32c639518cf0b58bc39310532026365a5cab"}                # 仕様 §17-2 の値
    # 橋渡しの 12 組: 説明できる 6（R1→R2→R3→R4 補完→R4 共鳴 の順に、id の小さいもの）・説明できない 6（無関係→近い話題→そのほか）
    syn = R.load_synthetic()
    kind = {p["id"]: p["種類"] for p in syn["people"]}
    pairs = sorted(syn["pairs"], key=lambda p: p["id"])
    pos_cats = [lambda p: "R1" in p["expl_label"], lambda p: "R2" in p["expl_label"], lambda p: "R3" in p["expl_label"],
                lambda p: "R4" in p["expl_label"] and kind[p["person"]] == "補完",
                lambda p: "R4" in p["expl_label"] and kind[p["person"]] == "共鳴"]
    neg = [p for p in pairs if p["expl_label"] == ["説明できない"]]
    neg_cats = [lambda p: p["note"] == "無関係", lambda p: (p["note"] or "").startswith("話題が近く"),
                lambda p: p["note"] != "無関係" and not (p["note"] or "").startswith("話題が近く")]

    def pick(cands, cats, k):
        got = []
        while len(got) < k:
            for c in cats:
                nxt = next((p["id"] for p in cands if c(p) and p["id"] not in got), None)
                if nxt and len(got) < k:
                    got.append(nxt)
        return got
    pos = [p for p in pairs if M.label_class(p["expl_label"]) == "説明できる"]
    assert sorted(pick(pos, pos_cats, 6) + pick(neg, neg_cats, 6)) == list(R.BRIDGE_IDS)
    assert set(R.TRIAL_IDS) <= set(R.BRIDGE_IDS)
    assert sum(1 for p in syn["pairs"] if p["note"] == "無関係") == 10


# ── 512 決定性の比べ方 ─────────────────────────────────────────────────────────────────
def test_t512_determinism():
    def run(outs):
        return {"calls": [{"id": i, "stage": s, "output_sha256": o} for i, s, o in outs]}
    a = run([("S01", 1, "x"), ("S01", 2, "y"), ("S02", 1, "z")])
    assert R.determinism([a, a, a]) == {"runs": 3, "calls": 3, "same": 3, "all_same": True}
    b = run([("S01", 1, "x"), ("S01", 2, "Y"), ("S02", 1, "z")])
    assert R.determinism([a, b]) == {"runs": 2, "calls": 3, "same": 2, "all_same": False}
    c = run([("S01", 1, "x"), ("S01", 2, "y")])                                                     # 呼んだ数が違う
    assert R.determinism([a, c])["all_same"] is False


# ── 513 報告76 順1 の確認（LLM-jp-4 を外す・型の日付と時刻・予算 10 ドル）─────────────────────────────────
def test_t513_confirmation_after_stage1(tmp_path):
    assert list(L.PROFILES) == ["nemotron-9b-ja"] and "llmjp4-8b" in L.EXCLUDED                    # 候補は 1 つ
    assert list(J.MODELS) == ["nemotron-9b-ja"] and J.MODELS["nemotron-9b-ja"]["how"] == "convert"
    for src in (J.PREPARE_SH, J.RUN_SH, json.dumps(J.prepare_spec("ns"))):
        assert "llm-jp" not in src and "mosh-hf" not in src                                           # 第三者のファイルを写さない
    # チャットの型の日付と時刻: 印があれば分かる。公式の Nemotron の型の sha256 を記録してある
    assert L.template_facts("{{ strftime_now('%Y-%m-%d') }}")["uses_date_or_time"] is True
    assert L.template_facts("{{ messages[0]['content'] }}")["uses_date_or_time"] is False
    assert L.PROFILES["nemotron-9b-ja"]["official_chat_template_sha256"] == \
        "dd617e596ae954cac585ba2c5e972af5f0b917ea7e6c6d74b63c2f67c24ece9e"
    out = tmp_path / "r.json"
    res = R.main(["--profile", "nemotron-9b-ja", "--gguf-sha256", SHA, "--ids", "trial", "--out", str(out)],
                 backend_factory=lambda: _backend("nemotron-9b-ja"))
    assert res["chat_template"]["uses_date_or_time"] is False and res["chat_template"]["same_as_official"] is False
    assert res["ids"] == ["S01", "S04", "S31"]
    # 予算: 10 ドル。仕事ごとの上限（時間の上限 × 料金）を足して超えるなら作らない
    assert J.BUDGET_USD == 10.0
    run = J.run_spec("ns", "nemotron-9b-ja", SHA, "all", 5, "b" * 64, "run")
    assert J.max_cost(run) == 2.0 and J.max_cost(J.prepare_spec("ns")) == 0.05
    J.check_budget(run, 7.9)                                                                          # 7.9 ＋ 2.0 ≦ 10
    with pytest.raises(SystemExit):
        J.check_budget(run, 8.1)
    with pytest.raises(SystemExit):
        J.main(["run", "--namespace", "ns", "--profile", "nemotron-9b-ja"])                           # 使った額が無いと作らない
