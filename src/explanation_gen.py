"""接続の説明 — 段階2 の生成の仕組み（指示書69 §1・§3。仕様 expl-v0.4 §1・§2・§5・§6・§12）。

向き（必要を出した側 N × 文を出した側 O）ごとに、二段で生成する。
  段1: 二人の公開の構造化の文（参照の名前つき・型付きの JSON）→ 要素の組の JSON（自由な文を書かない）
  確かめ: 段階1 の check()（引用の実在・句の長さ・ラベル・R1/R2 の述語・補充しない）＋生成の分の追加（下）
  段2: 確かめ済みの組だけ（句・ラベル・見出し。原文は渡さない）→ 目的ごとの ① と根拠の印
  確かめ: check() ＋追加。表示してよい向きのレコードだけを返す

モデルは差し替えられる（POX_EXPLANATION_BACKEND）:
  off（既定）: 生成しない（保存済みの取り込み分だけを出す）
  fixed: テスト用。決まった入力に決まった出力を返す（規則で句を選ぶ。モデルではない）
  local: 手元の自前のモデル（transformers）。仕組みを通すためだけ（質は問わない）
  server: 段階3 で決めるモデルの推論サーバー（段階2 では使えない）

外部の AI の API は使わない。数値・生テキスト・根拠・evidence_span・要約文・背景・経験・意志_なぜは入力に使わない。
"""
import hashlib
import json
import os
import re
import unicodedata

import explanation as X

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPT_DIR = os.path.join(ROOT, "docs", "prompts")
PROMPT1_FILE, PROMPT2_FILE = "explanation_p1_v0.3.txt", "explanation_p2_v0.3.txt"
PROMPT1_VERSION, PROMPT2_VERSION = "expl-p1-v0.3", "expl-p2-v0.3"
# プロンプトのファイルが唯一の正。sha256 は中身から計算し、ここに固定して食い違いを止める（テストで確かめる）
PROMPT1_SHA = "92f1a316bf6d155dd489b4ff5c144f22349353b98ac70bfcd8dfa0213a0aa261"
PROMPT2_SHA = "19f3bdc7199f7502d6cb1f1c30679976f3a4b8a49a40ec637982cab8d9a3432e"
P1_N_SLOT = "{N の目的・必要像の文（参照の名前・型・必須つき）。目的の種類（補完／共鳴）}"
P1_O_SLOT = "{O の与え像・現状（縛られているものを除く）・関心の文（参照の名前つき）}"
P2_SLOT = "{目的の参照・見出し・種類と、elements（need_quote・offer_quote・relation）}"

BACKENDS = ("off", "fixed", "local", "server")
FIELD_MAX = 300                                  # 入力の欄ごとの長さの上限（乗っ取りへの手当て）
STATE_READ = ("持っているもの", "できること_型", "未分類")   # O の側の現状（縛られているものを除く）
BOUND = "縛られているもの"
LOCAL_DEFAULT = {"repo": "llm-jp/llm-jp-3-1.8b-instruct", "revision": "413b7929df092690b0f9449f3045baaf8deba0a5"}


# ── プロンプト ─────────────────────────────────────────────────────────────────────
def prompt_text(n):
    with open(os.path.join(PROMPT_DIR, PROMPT1_FILE if n == 1 else PROMPT2_FILE), encoding="utf-8") as f:
        return f.read()


def prompt_sha(n):
    return hashlib.sha256(prompt_text(n).encode("utf-8")).hexdigest()


def fill_prompt1(n_block, o_block):
    t = prompt_text(1)
    assert P1_N_SLOT in t and P1_O_SLOT in t
    return t.replace(P1_N_SLOT, json.dumps(n_block, ensure_ascii=False, indent=1)).replace(
        P1_O_SLOT, json.dumps(o_block, ensure_ascii=False, indent=1))


def fill_prompt2(p2_block):
    t = prompt_text(2)
    assert P2_SLOT in t
    return t.replace(P2_SLOT, json.dumps(p2_block, ensure_ascii=False, indent=1))


# ── 入力（仕様 §1）─────────────────────────────────────────────────────────────────
_ZW = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")
_URL = re.compile(r"https?://\S+|www\.\S+")
_MD = re.compile(r"[`*#>\[\]|~_]")


def sanitize(text):
    """モデルに渡す文: 制御文字・ゼロ幅文字・URL・マークダウンの記号を除き、長さを切る（原文の確かめには原文を使う）。"""
    t = unicodedata.normalize("NFC", str(text or ""))
    t = "".join(ch for ch in t if unicodedata.category(ch)[0] != "C" or ch in "\n")
    t = _ZW.sub("", t)
    t = _URL.sub("", t)
    t = _MD.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:FIELD_MAX]


def _canon_hash(obj):
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(unicodedata.normalize("NFC", s).encode("utf-8")).hexdigest()


def purpose_kind_rule(needs):
    """目的の種類の仮の規則（指示書69 §4-4 の案）: 欠かせない要素がある → 補完。
    欠かせない要素が無く、関心の型の要素がある → 共鳴。どちらでもなければ補完。"""
    if any(n.get("must") for n in needs):
        return "補完"
    if any(n.get("type") == "関心" for n in needs):
        return "共鳴"
    return "補完"


def need_side(person, bound_allowed=False):
    """N の側（必要を出した側）の入力・引用・欠かせない要素・内容ハッシュ。目的が無ければ None。
    person: {"subject", "purposes": [{"purpose_id", "title", "means", "kind"?, "needs": [{"text","type","must"}]}], "bound"?}"""
    purposes = [p for p in person.get("purposes") or [] if p.get("needs")]
    if not purposes:
        return None
    block, cites, musts, order = {"purposes": []}, {}, {}, []
    read = {"purposes": [], "bound": None}
    for i, p in enumerate(purposes, 1):
        pid = str(p["purpose_id"])
        kind = p.get("kind") or purpose_kind_rule(p["needs"])
        pref = f"N.purpose.{pid}"
        item = {"ref": pref, "見出し": sanitize(p.get("title")), "手段": sanitize(p.get("means")), "種類": kind, "必要像": []}
        for j, n in enumerate(p["needs"], 1):
            ref = f"N.need.{pid}.{i}-{j}"
            cites[ref] = str(n["text"])
            musts[ref] = bool(n.get("must"))
            item["必要像"].append({"ref": ref, "文": sanitize(n["text"]), "型": n.get("type") or "", "必須": bool(n.get("must"))})
        if bound_allowed and person.get("bound"):
            ref = f"N.need.{pid}.bound"                    # 「縛られているもの」は許可がある人だけ、必要の一つとして
            cites[ref] = str(person["bound"])
            musts[ref] = False
            item["必要像"].append({"ref": ref, "文": sanitize(person["bound"]), "型": BOUND, "必須": False})
        block["purposes"].append(item)
        order.append({"ref": pref, "order": i - 1, "number": i, "title": str(p.get("title") or ""), "kind": kind,
                      "needs": [x["ref"] for x in item["必要像"]]})
        read["purposes"].append({"id": pid, "title": p.get("title"), "means": p.get("means"), "kind": kind,
                                 "needs": [[n["text"], n.get("type"), bool(n.get("must"))] for n in p["needs"]]})
    if bound_allowed and person.get("bound"):
        read["bound"] = person["bound"]
    return {"block": block, "cites": cites, "musts": musts, "purposes": order, "hash": _canon_hash(read),
            "bound_used": bool(bound_allowed and person.get("bound"))}


def offer_side(person):
    """O の側（文を出した側）の入力・引用・内容ハッシュ。与え像は公開の閾値を通った版だけ（呼ぶ側が渡す）。
    「縛られているもの」は使わない。"""
    block, cites, read = {"与え像": [], "現状": [], "関心": []}, {}, {}
    for k, o in enumerate(person.get("offers") or [], 1):
        ref = f"O.offer.{k}"
        cites[ref] = str(o["text"])
        block["与え像"].append({"ref": ref, "文": sanitize(o["text"]), "型": o.get("type") or ""})
    for slot in STATE_READ:
        t = str((person.get("state") or {}).get(slot) or "").strip()
        if t:
            ref = f"O.state.{slot}"
            cites[ref] = t
            block["現状"].append({"ref": ref, "文": sanitize(t)})
    for k, t in enumerate(person.get("interests") or [], 1):
        if str(t).strip():
            ref = f"O.interest.{k}"
            cites[ref] = str(t)
            block["関心"].append({"ref": ref, "文": sanitize(t)})
    read = {"offers": [[o["text"], o.get("type")] for o in person.get("offers") or []],
            "state": {s: (person.get("state") or {}).get(s) for s in STATE_READ},
            "interests": list(person.get("interests") or [])}
    return {"block": block, "cites": cites, "hash": _canon_hash(read)}


# ── 版と鍵（指示書69 §3）────────────────────────────────────────────────────────────
def versions(backend):
    return {"spec": X.SCHEMA_VERSION, "rel": X.RELATION_RULE_VERSION, "forbid": X.FORBIDDEN_VERSION,
            "prompt1_sha": PROMPT1_SHA, "prompt2_sha": PROMPT2_SHA, "model": backend.model,
            "revision": backend.revision, "decoding": backend.decoding}


def as_of(n_hash, o_hash):
    """向きの as-of: N 側・O 側の読む欄の内容ハッシュ（プロフィール全体ではなく、説明が読む欄だけ）。"""
    return {"need": {"profile": f"h:{n_hash}", "necessity": None}, "offer": {"profile": f"h:{o_hash}", "necessity": None}}


def version_key(backend):
    return _canon_hash(versions(backend))


def gen_key(n_sid, o_sid, n_hash, o_hash, backend):
    """鍵: 向き × N 側の読む欄の内容ハッシュ × O 側の読む欄の内容ハッシュ × 仕様・規則・禁止の語の版 × プロンプトの sha ×
    モデルと revision × decoding。同じ鍵の説明は二度作らない。"""
    return _canon_hash({"dir": [n_sid, o_sid], "n": n_hash, "o": o_hash, "v": versions(backend)})


# ── バックエンド（指示書69 §1-2）─────────────────────────────────────────────────────
class Backend:
    name, model, revision = "off", "off", None
    decoding = {"temperature": 0, "seed": 0}

    def run(self, stage, prompt, payload):
        """段（1・2）・埋めたプロンプト・構造化した入力 → 生の出力の文字列（読めなければ None）。"""
        raise NotImplementedError


class FixedBackend(Backend):
    """テスト用。決まった入力に決まった出力を返す。responder(stage, payload) を渡せば、それを使う。
    渡さなければ、規則で句を選ぶ（モデルではない。fixed-rule-v1）。"""
    name = "fixed"

    def __init__(self, responder=None, model="fixed-rule-v1"):
        self.responder = responder or rule_responder
        self.model = model

    def run(self, stage, prompt, payload):
        out = self.responder(stage, payload)
        return out if isinstance(out, str) or out is None else json.dumps(out, ensure_ascii=False)


class LocalBackend(Backend):
    """手元で動く自前のモデル（transformers・貪欲・温度 0 相当）。仕組みを通すためだけに使う。"""
    name = "local"

    def __init__(self, repo=None, revision=None, seed=0, max_new_tokens=(1024, 400)):
        self.model = repo or os.environ.get("POX_EXPLANATION_LOCAL_MODEL") or LOCAL_DEFAULT["repo"]
        self.revision = revision or os.environ.get("POX_EXPLANATION_LOCAL_REVISION") or (
            LOCAL_DEFAULT["revision"] if self.model == LOCAL_DEFAULT["repo"] else None)
        self.decoding = {"temperature": 0, "do_sample": False, "seed": seed,
                         "max_new_tokens": {"1": max_new_tokens[0], "2": max_new_tokens[1]}}
        self._tok = self._lm = None

    def _load(self):
        if self._lm is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            self._tok = AutoTokenizer.from_pretrained(self.model, revision=self.revision)
            self._lm = AutoModelForCausalLM.from_pretrained(self.model, revision=self.revision, dtype=torch.float32)
            self._lm.eval()

    def run(self, stage, prompt, payload):
        import torch
        self._load()
        torch.manual_seed(self.decoding["seed"])
        msgs = [{"role": "user", "content": prompt}]
        ids = self._tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")
        with torch.no_grad():
            out = self._lm.generate(ids, attention_mask=torch.ones_like(ids),
                                    max_new_tokens=self.decoding["max_new_tokens"][str(stage)], do_sample=False,
                                    pad_token_id=self._tok.eos_token_id)
        return self._tok.decode(out[0][ids.shape[1]:], skip_special_tokens=True)


class OffBackend(Backend):
    def run(self, stage, prompt, payload):
        return None


def backend_name():
    v = (os.environ.get("POX_EXPLANATION_BACKEND") or "off").strip().lower()
    return v if v in BACKENDS else "off"


def get_backend():
    """設定のバックエンド。server は段階3 で作るので、段階2 では使えない（生成しない）。"""
    name = backend_name()
    if name == "fixed":
        return FixedBackend()
    if name == "local":
        return LocalBackend()
    return OffBackend()


def startup_check():
    """本番（Render）で fixed・local にしていたら起動しない（テスト・開発・計測のためのもの）。server は段階3 まで使えない。"""
    name = backend_name()
    if os.environ.get("RENDER") and name in ("fixed", "local"):
        raise SystemExit(f"[FATAL] POX_EXPLANATION_BACKEND={name} はテスト・開発用です。本番では off にしてください。")
    return {"backend": name, "generates": name in ("fixed", "local")}


# ── 規則で句を選ぶ（fixed の既定。モデルではない）─────────────────────────────────────────────
_R1_END = re.compile(r"(てきた|を担ってきた)")
_R2_END = re.compile(r"(できる|関われる|手伝える)")


def _lcs(a, b):
    best, at = 0, 0
    for i in range(len(a)):
        for j in range(len(b)):
            k = 0
            while i + k < len(a) and j + k < len(b) and a[i + k] == b[j + k]:
                k += 1
            if k > best:
                best, at = k, i
    return a[at:at + best]


def _tail(text, n=X.QUOTE_MAX):
    t = text.rstrip("。．. ")
    return t[-n:]


def rule_responder(stage, payload):
    """段1: 必要像の文ごとに、共通の部分（4 字以上）が最も長い相手の文を選ぶ。句は、必要像の文のその部分と、相手の文の末尾 40 字。
    段2: 決まった型の文（{O}は、〈相手の句〉人で、{N}の〈見出し〉に関われる可能性があります。）。"""
    if stage == 1:
        n, o = payload["N"], payload["O"]
        offers = [(x["ref"], x["文"], "offer", x.get("型")) for x in o["与え像"]] + \
                 [(x["ref"], x["文"], "state", None) for x in o["現状"]] + [(x["ref"], x["文"], "interest", None) for x in o["関心"]]
        out = []
        for p in n["purposes"]:
            els = []
            for need in p["必要像"]:
                cand = []
                for ref, text, kind, typ in offers:
                    c = _lcs(need["文"], text)
                    if len(c) >= 4:
                        cand.append((len(c), ref, text, kind, typ, c))
                if not cand:
                    continue
                _, ref, text, kind, typ, common = max(cand, key=lambda x: (x[0], x[1]))
                oq = _tail(text)
                if kind == "interest" or need.get("型") == "関心":
                    rel = "R4"
                elif _R1_END.search(oq):
                    rel = "R1"
                elif _R2_END.search(oq):
                    rel = "R2"
                elif typ == "資源" or ref.endswith("持っているもの"):
                    rel = "R3"
                else:
                    continue
                nq = need["文"][max(0, need["文"].find(common) - 10):][:X.QUOTE_MAX]
                els.append({"need_ref": need["ref"], "must": need["必須"], "need_quote": nq, "offer_ref": ref,
                            "offer_quote": oq, "relation": rel})
            out.append({"ref": p["ref"], "status": "explained" if els else "not_covered", "elements": els,
                        "unknown": [], "missing_must": []})
        return {"purposes": out}
    meanings = []
    for p in payload["purposes"]:
        if not p["elements"]:
            continue
        e = p["elements"][0]
        meanings.append({"purpose_ref": p["purpose_ref"], "cites": [e["no"]],
                         "text": f"{{O}}は、{e['offer_quote']}人で、{{N}}の{p['見出し'].rstrip('。．.')}に関われる可能性があります。"})
    return {"meanings": meanings}


# ── 出力の読み取り ──────────────────────────────────────────────────────────────────
def parse_json(raw):
    """生の出力から、最初の { から最後の } までを JSON として読む。読めなければ None。"""
    if raw is None:
        return None
    s = str(raw)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


# ── 確かめの追加（指示書69 §1-3。生成した分だけに当てる。段階1 の check() は変えない）──────────────────
_IMPERATIVE = re.compile(r"(なさい|ください|しろ|せよ|しましょう|すべき|してね)")
_CONTENT_RUN = re.compile(r"[一-鿿㐀-䶿々〆ヵヶ゠-ヿA-Za-z0-9０-９Ａ-Ｚａ-ｚ]+")
_MEANING_FORM = re.compile(r"^\{O\}は、.+人で、\{N\}の.+(になれる|に関われる)可能性があります。$")
TEMPLATE_WORDS = "人で可能性関"


def _covered(word, vocab):
    """漢字・カタカナ・英数の語が、組の句・ラベル・見出しの 2 字以上の部分（と、決まりの形の 1 字の語）で組み立てられるか。
    1 字だけの語（例「作」）は、句・ラベル・見出しのどこかにあればよい。"""
    if len(word) == 1:
        return word in vocab
    ok = [True] + [False] * len(word)
    for i in range(len(word)):
        if not ok[i]:
            continue
        if word[i] in TEMPLATE_WORDS:
            ok[i + 1] = True
        for j in range(i + 2, len(word) + 1):
            if word[i:j] in vocab:
                ok[j] = True
    return ok[-1]


def _bad_text(text):
    return X._forbidden(text) + (["命令形"] if _IMPERATIVE.search(text) else [])


def check_generated(direction, n_side, o_side_refs, bound_allowed):
    """生成した向きに、段階1 の check() に足す確かめを当てる。落ちた組・目的を除いた向きと、理由の一覧を返す。
    - 句と ① に禁止の語・URL・連絡先・命令形が無い（入力の文の中の指示に従った形跡）
    - 参照は入力にあったものだけ（公開の閾値より前の文・渡していない欄を使わない）
    - 「縛られているもの」は許可がある N の側だけ。O の側では使わない
    - 欠かせない要素の印は入力のとおり（§12-4 の 13）
    - ① の文の形と、漢字・カタカナ・英数の語が組の句・ラベル・見出しにあるか（閉じた語彙。ひらがなだけの語は確かめない）"""
    problems = []
    allowed = set(n_side["cites"]) | set(o_side_refs)
    for p in direction["purposes"]:
        keep, remap = [], {}
        for i, e in enumerate(p["elements"]):
            why = []
            for ref in (e["need_ref"], e["offer_ref"]):
                if ref not in allowed:
                    why.append("入力に無い参照")
                if BOUND in ref and not ref.startswith("N."):
                    why.append("O の側の縛られているもの")
            if e["need_ref"].endswith(".bound") and not bound_allowed:
                why.append("許可の無い縛られているもの")
            if e.get("must") != n_side["musts"].get(e["need_ref"]):
                why.append("欠かせない要素の印が入力と違う")
            for q in (e["need_quote"], e["offer_quote"]):
                why += _bad_text(q)
            if why:
                problems.append(f"{p['ref']}: 組 {i} を出さない（{'・'.join(dict.fromkeys(why))}）")
                continue
            remap[i] = len(keep)
            keep.append(e)
        p["elements"] = keep
        p["unknown"] = [u for u in p["unknown"] if u["need_ref"] in allowed and not _bad_text(u["need_quote"])
                        and (bound_allowed or not u["need_ref"].endswith(".bound"))]
        if p["meaning"] is not None:
            m = p["meaning"]
            cites = [remap[c] for c in m["cites"] if c in remap]
            why = _bad_text(m["text"])
            if len(cites) != len(m["cites"]):
                why.append("① の根拠の組が落ちた")
            sentences = [s for s in re.split(r"(?<=。)", m["text"]) if s.strip()]
            if not sentences or any(not _MEANING_FORM.match(s.strip()) for s in sentences):
                why.append("① の文の形が決まりの形でない")
            vocab = "".join(e["need_quote"] + e["offer_quote"] for e in keep) + p["title"] + TEMPLATE_WORDS + \
                "".join(X.RELATIONS[e["relation"]][0] + X.RELATIONS[e["relation"]][1] for e in keep)
            body = m["text"].replace("{N}", "").replace("{O}", "")
            new_words = [w for w in _CONTENT_RUN.findall(body) if not _covered(w, vocab)]
            if new_words:
                why.append("① に組・ラベル・見出しに無い語がある")
            if why:
                problems.append(f"{p['ref']}: ① を出さない（{'・'.join(dict.fromkeys(why))}）")
                p["meaning"] = None
                p["status"] = "not_covered" if p["missing_must"] else "explained_dropped"
            else:
                p["meaning"] = {"text": m["text"], "cites": cites}
    direction["purposes"] = [p for p in direction["purposes"] if p["status"] != "explained_dropped"]
    return direction, problems


# ── 二段の生成（指示書69 §1-1）────────────────────────────────────────────────────────
def stage2_payload(direction):
    """段2 の入力: 確かめ済みの組の句・ラベル・目的の見出しと種類だけ（原文・参照の本文は渡さない）。"""
    out = []
    for p in direction["purposes"]:
        if p["status"] != "explained":
            continue
        out.append({"purpose_ref": p["ref"], "見出し": p["title"], "種類": p["kind"],
                    "elements": [{"no": i, "need_quote": e["need_quote"], "offer_quote": e["offer_quote"],
                                  "relation": e["relation"], "relation_name": X.RELATIONS[e["relation"]][0]}
                                 for i, e in enumerate(p["elements"])]})
    return {"purposes": out}


def _machine_status(p, musts):
    """出すかどうかは機械が決める（仕様 §4-1）。欠かせない要素がそろわなければ not_covered。補完で R4 だけなら出さない。
    モデルが not_covered と言った目的は、説明しない（落とす方向にだけ従う）。"""
    covered = {e["need_ref"] for e in p["elements"]}
    missing = [r for r in p["needs"] if musts.get(r) and r not in covered]
    if missing:
        return "not_covered", missing
    if not p["elements"] or p.get("model_status") == "not_covered":
        return "not_covered", []
    if p["kind"] == "補完" and all(e["relation"] == "R4" for e in p["elements"]):
        return "not_covered", []
    return "explained", []


def generate_direction(n_person, o_person, backend, bound_allowed=False, trace=None):
    """向き 1 つを生成する。戻り値: {"direction", "show_reason", "problems", "n_hash", "o_hash", "stage2_inputs"}。
    N に目的が無ければ None。direction は向きのレコード（参照は N.*・O.*）で、表示してよい部分だけ。"""
    ns = need_side(n_person, bound_allowed=bound_allowed)
    if ns is None:
        return None
    os_ = offer_side(o_person)
    payload1 = {"N": ns["block"], "O": os_["block"]}
    raw1 = backend.run(1, fill_prompt1(ns["block"], os_["block"]), payload1)
    out1 = parse_json(raw1)
    problems, show_reason = [], None
    cites = {**ns["cites"], **os_["cites"]}
    by_ref = {p.get("ref"): p for p in (out1 or {}).get("purposes") or [] if isinstance(p, dict)}
    purposes = []
    for meta in ns["purposes"]:
        got = by_ref.get(meta["ref"]) or {}
        els = []
        for e in got.get("elements") or []:
            if not isinstance(e, dict):
                continue
            nr = str(e.get("need_ref") or "")
            els.append({"need_ref": nr, "must": bool(ns["musts"].get(nr, False)),
                        "need_quote": str(e.get("need_quote") or ""), "offer_ref": str(e.get("offer_ref") or ""),
                        "offer_quote": str(e.get("offer_quote") or ""), "relation": str(e.get("relation") or "")})
        unknown = [{"need_ref": str(u.get("need_ref") or ""), "need_quote": str(u.get("need_quote") or "")}
                   for u in got.get("unknown") or [] if isinstance(u, dict)]
        purposes.append({"ref": meta["ref"], "order": meta["order"], "number": meta["number"], "title": meta["title"],
                         "kind": meta["kind"], "status": "not_covered", "meaning": None, "elements": els,
                         "unknown": unknown, "missing_must": [], "needs": meta["needs"],
                         "model_status": got.get("status")})
    gen = {"prompt1_sha": PROMPT1_SHA, "prompt2_sha": PROMPT2_SHA, "model": backend.model, "revision": backend.revision,
           "decoding": backend.decoding}
    direction = {"purposes": purposes, "citations": [{"ref": r, "text": t} for r, t in cites.items()],
                 "as_of": as_of(ns["hash"], os_["hash"]), "generation": gen}
    if out1 is None:
        show_reason = "model_declined"
    produced = sum(len(p["elements"]) for p in purposes)
    # 確かめ（段1）: 段階1 の check() を、① の無い形で当てる。そのあと生成の分の追加
    stage1 = json.loads(json.dumps(direction))
    for p in stage1["purposes"]:
        p.pop("needs"), p.pop("model_status")
    card = X.assemble(stage1, None, True)
    checked, probs = X.check(card)
    problems += probs
    kept = {p["ref"]: p for p in checked["purposes"]}
    for p in purposes:
        k = kept.get(p["ref"].replace("N.", "viewer.", 1))
        p["elements"] = [_to_dir(e) for e in (k or {}).get("elements", [])]
        p["unknown"] = [{"need_ref": u["need_ref"].replace("viewer.", "N.", 1), "need_quote": u["need_quote"]}
                        for u in (k or {}).get("unknown", [])]
    direction, probs = check_generated(direction, ns, os_["cites"].keys(), bound_allowed)
    problems += probs
    for p in direction["purposes"]:
        p["status"], p["missing_must"] = _machine_status(p, ns["musts"])
    if show_reason is None and produced == 0:
        show_reason = "no_pair"
    elif show_reason is None and not any(p["elements"] for p in direction["purposes"]):
        show_reason = "quote_failed"
    # 段2: 確かめ済みの組だけ
    p2 = stage2_payload(direction)
    if trace is not None:
        trace.append({"stage1_payload": payload1, "stage2_payload": p2})
    if p2["purposes"]:
        out2 = parse_json(backend.run(2, fill_prompt2(p2), p2))
        meanings = {m.get("purpose_ref"): m for m in (out2 or {}).get("meanings") or [] if isinstance(m, dict)}
        for p in direction["purposes"]:
            if p["status"] != "explained":
                continue
            m = meanings.get(p["ref"])
            if not m or not isinstance(m.get("cites"), list) or not str(m.get("text") or "").strip():
                p["status"] = "explained_dropped"
                problems.append(f"{p['ref']}: ① が無い")
                continue
            p["meaning"] = {"text": str(m["text"]), "cites": [c for c in m["cites"] if isinstance(c, int)]}
        direction["purposes"] = [p for p in direction["purposes"] if p["status"] != "explained_dropped"]
        direction, probs = check_generated(direction, ns, os_["cites"].keys(), bound_allowed)
        problems += probs
    for p in direction["purposes"]:
        p.pop("needs", None), p.pop("model_status", None)
        if p["status"] != "explained":
            p["meaning"] = None
    # 確かめ（全体）: 段階1 の check() をそのまま。落ちた目的は出さない
    final, probs = X.check(X.assemble(direction, None, True))
    problems += probs
    direction["purposes"] = [_purpose_to_dir(p) for p in final["purposes"]]
    if not any(p["status"] == "explained" for p in direction["purposes"]):
        meaning_failed = any("① を出さない" in x or "① が無い" in x or "この目的の説明を出さない" in x for x in problems)
        if meaning_failed and show_reason in (None, "quote_failed"):
            show_reason = "meaning_failed"             # 組は確かめを通ったが、① が確かめに落ちた（記録だけ。スキーマの 3 つに足した）
        elif show_reason is None:
            show_reason = "no_pair" if produced == 0 else (
                "quote_failed" if not any(p["elements"] for p in direction["purposes"]) else None)
    return {"direction": direction, "show_reason": show_reason, "problems": problems, "n_hash": ns["hash"],
            "o_hash": os_["hash"], "bound_used": ns["bound_used"], "output_digest": _canon_hash(direction)}


def _to_dir(e):
    return {**e, "need_ref": e["need_ref"].replace("viewer.", "N.", 1), "offer_ref": e["offer_ref"].replace("target.", "O.", 1)}


def _purpose_to_dir(p):
    q = json.loads(json.dumps(p))
    q["ref"] = q["ref"].replace("viewer.", "N.", 1)
    q["elements"] = [_to_dir(e) for e in q["elements"]]
    q["unknown"] = [{**u, "need_ref": u["need_ref"].replace("viewer.", "N.", 1)} for u in q["unknown"]]
    q["missing_must"] = [m.replace("viewer.", "N.", 1) for m in q["missing_must"]]
    return q
