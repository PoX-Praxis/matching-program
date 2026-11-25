"""プロンプトを組み立てる（ラベル v0.3・選ぶ・まとめる v0.2。v0.1 も残す）。本文は docs/prompts/ のまま（直さない）。
部品の行は「番号〔欄・役目〕文」、候補の行は「番号（cell）：左 × 右」（KAORU×PA の 5 回目・6 回目と同じ）。"""
import hashlib
import os
import re

from . import atoms as AT

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LABEL_FILE = os.path.join(_ROOT, "docs", "prompts", "explanation_label_v0.3.txt")
SELECT_FILE = os.path.join(_ROOT, "docs", "prompts", "explanation_select_v0.2.txt")       # 指示書81 追補1 §3-2
SELECT_FILE_V01 = os.path.join(_ROOT, "docs", "prompts", "explanation_select_v0.1.txt")   # 前の版（残す）
LABEL_SHA256 = "02ec1ca8362431e8caafd0dd59c62e461e532e53ec5632f4d16beb268c7e9f6e"   # prompt_label_v0.3（直さない）
SELECT_SHA256 = "b767abf2c302810b807289562a0f2050399b5af8490a13ab3f69f9bc0d223e70"  # prompt_select_v0.2（v0.1 に「まとめる」の 2 行）
SELECT_SHA256_V01 = "19e818fb98ac905b4e59803c39b6310df0e3c38997e0976206bb6382b8b2022b"  # prompt_select_v0.1（直さない）
BOUND_USE = ("resolves",)       # 目的の欄が無い人の縛り（足りないもの）× 相手の力：説明に使うラベル（指示書81 追補1 §3-3）
USE = ("same_kind", "relational", "partial", "same_interest")     # 選ぶ段に渡すラベル（確かめの後）
# ラベルの段の向き：(番号, 向きの文, 求める側, 相手)。一人目（A）が N、二人目（B）が O
LABEL_DIRS = (("N_by_O", "N_need_met_by_O（左が {N} の部品、右が {O} の部品）", "A", "B"),
              ("O_by_N", "O_need_met_by_N（左が {O} の部品、右が {N} の部品）", "B", "A"))
# 選ぶ段：(番号, 求める側, 相手, ラベルの向き)。選ぶ段では求める側を N と呼ぶ
SELECT_DIRS = (("A_need", "A", "B", "N_by_O"), ("B_need", "B", "A", "O_by_N"))


def template(path, sha):
    t = open(path, encoding="utf-8").read()
    got = hashlib.sha256(t.encode("utf-8")).hexdigest()
    if got != sha:
        raise ValueError(f"プロンプトの sha256 が違う: {os.path.basename(path)}")
    return t


def role_text(a):
    return {"側": "側", "縛り": f"縛り（bound.{a['bound']}）", "力": "力", "関心": "関心", "文脈": "文脈", "未分類": "未分類"}[a["kind"]]


def numbering(atoms, role):
    """部品の番号（N01・O01…）。role は 人の印 → "N"・"O"。"""
    num, cnt = {}, {"N": 0, "O": 0}
    for a in atoms:
        r = role[a["who"]]
        cnt[r] += 1
        num[a["id"]] = f"{r}{cnt[r]:02d}"
    return num


def atom_lines(atoms, num, role, r):
    return [f"{num[a['id']]}〔{role[a['who']]}{a['field'][1:].split('#')[0]}・{role_text(a)}〕{a['text']}" for a in atoms if role[a["who"]] == r]


def label_prompts(atoms, cells=("A", "B", "C"), bound_for=()):
    """ラベルの段のプロンプト 2 つ。返り値: (prompts {向き: 文}, cands {番号: 候補}, info {向き: 数})。
    cells: 並べる候補の種類（本番は A だけ。指示書81 §1。B・C を作るコードは残し、ここで切る）。
    bound_for: 目的の欄が無い人（"A"・"B"）。B を切っていても、その人についてだけ「縛り（足りないもの）× 相手の力」を並べる
    （指示書81 追補1 §3-3。スイッチを入れたときだけ渡す）。"""
    tpl = template(LABEL_FILE, LABEL_SHA256)
    role = {"A": "N", "B": "O"}
    num = numbering(atoms, role)
    n_lines, o_lines = atom_lines(atoms, num, role, "N"), atom_lines(atoms, num, role, "O")
    prompts, cands, info, c = {}, {}, {}, 0
    for pid, dname, x, y in LABEL_DIRS:
        cs, info[pid] = AT.candidates(atoms, x, y)
        rows = []
        for cell in ("A", "B", "C"):
            pick = cs[cell] if cell in cells else []
            if cell == "B" and "B" not in cells and x in bound_for:
                pick = [(l, r) for l, r in cs["B"] if l.get("bound") == "lack"]
            for left, right in pick:
                c += 1
                cid = f"C{c:03d}"
                cands[cid] = {"pair": cid, "dir": pid, "cell": cell, "left": left["id"], "right": right["id"]}
                rows.append(f"{cid}（{cell}）：{num[left['id']]} × {num[right['id']]}")
        info[pid]["cells"] = {k: len(v) for k, v in cs.items()}
        body = (tpl.replace("@@DIRECTION@@", dname).replace("@@N_ATOMS@@", "\n".join(n_lines))
                .replace("@@O_ATOMS@@", "\n".join(o_lines)).replace("@@CANDS@@", "\n".join(rows)))
        assert "@@" not in body
        prompts[pid] = body
    return prompts, cands, info


def swap_no(t):
    """{N} と {O} を入れ替える（ラベルの段は一人目が N。選ぶ段で二人目が求める側のとき）。"""
    return str(t or "").replace("{N}", "\0").replace("{O}", "{N}").replace("\0", "{O}")


def purposes(atoms, F, META, who, bound_for=()):
    """選ぶ段とカードが使う、who の目的の並び。目的の欄が無い人で bound_for に入っていれば、縛られているもの（足りないもの）を
    「求めるもの」として 1 つ足す（purpose_id N.bound）。各要素に side_ids と use（説明に使うラベル）を付ける。"""
    out = []
    for p in AT.purposes_of(F, META, who):
        out.append({**p, "pid": f"N.purpose.p{p['n']}" if p["field"] else "N.need", "side_ids": AT.side_ids_of(atoms, who, p),
                    "cell": "A", "use": USE})
    if who in bound_for and not any(p["field"] for p in out):
        ids = [a["id"] for a in atoms if a["who"] == who and a["kind"] == "縛り" and a.get("bound") == "lack"]
        if ids:
            out.append({"n": str(len(out) + 1), "field": None, "title": "", "kind": "補完", "needs": [], "pid": "N.bound",
                        "side_ids": ids, "cell": "B", "use": BOUND_USE, "bound": True})
    return out


def select_prompts(atoms, labels, F, META, bound_for=(), file=None, sha=None):
    """選ぶ・まとめるの段のプロンプト 2 つ。labels は checks.check_labels の返り値（確かめの後）。
    返り値: (prompts {番号: 文}, pairs {組の番号: 組}, meta {番号: {num, purposes}})。
    file・sha を渡せば、その版のプロンプト（6 回目と比べるときの v0.1）。"""
    tpl = template(file or SELECT_FILE, sha or SELECT_SHA256)
    A = {a["id"]: a for a in atoms}
    prompts, pairs, meta, pc = {}, {}, {}, 0
    for pid, nw, ow, src_dir in SELECT_DIRS:
        role = {nw: "N", ow: "O"}
        num = numbering(atoms, role)
        lines = {r: atom_lines(atoms, num, role, r) for r in ("N", "O")}
        blocks, pinfo = [], []
        for p in purposes(atoms, F, META, nw, bound_for):
            side_ids = p["side_ids"]
            must_atoms = [a for a in side_ids if META.get(A[a]["field"].split("#")[0], {}).get("必須")]
            pref = p["pid"]
            mine = sorted((v for v in labels.values() if v["cand"]["dir"] == src_dir and v["cand"]["cell"] == p["cell"]
                           and v["cand"]["left"] in side_ids and v["final"] in p["use"]), key=lambda v: v["cand"]["pair"])
            rows, mine_ids = [], []
            for v in mine:
                pc += 1
                pno = f"P{pc:03d}"
                R = A[v["cand"]["right"]]
                basis = R.get("basis") or ("関心" if R["kind"] == "関心" else "")
                fi = v["lab"].get("fails_if") or ""
                fi = swap_no(fi) if nw == "B" else fi
                pairs[pno] = {"pair": pno, "prompt": pid, "purpose": pref, "cand": v["cand"]["pair"], "left": v["cand"]["left"],
                              "right": v["cand"]["right"], "label": v["final"], "basis": basis}
                mine_ids.append(pno)
                rows.append(f"  - {pno}：求める側 {num[v['cand']['left']]} × 相手 {num[v['cand']['right']]}・{v['final']}・{basis}・fails_if：{fi}")
            head = (f"## 目的 {pref}（{p['kind']}）「{p['title']}」" if p["field"] else
                    f"## 目的 {pref}（{p['kind']}。目的の欄が無い人なので、縛られているもの〔bound の足りないもの〕を、求めるものとして扱う）"
                    if p.get("bound") else
                    f"## 目的 {pref}（{p['kind']}。目的の欄が無い人なので、求めている相手の文〔necessity〕を目的として扱う）")
            must = "・".join(num[a] for a in must_atoms) or "なし"
            nec = "・".join(num[a] for a in side_ids if A[a]["field"].startswith(nw + ".necessity."))
            blocks.append(head + f"\n- 欠かせない要素（部品の番号）：{must}\n- 必要の部品：{nec}\n- ラベルの付いた組：\n" + ("\n".join(rows) or "  - なし"))
            pinfo.append({"purpose_id": pref, "n": p["n"], "kind": p["kind"], "side_ids": side_ids, "must_atoms": must_atoms, "pairs": mine_ids})
        body = (tpl.replace("@@N_ATOMS@@", "\n".join(lines["N"])).replace("@@O_ATOMS@@", "\n".join(lines["O"]))
                .replace("@@PURPOSES_AND_PAIRS@@", "\n\n".join(blocks)))
        assert "@@" not in body
        prompts[pid] = body
        meta[pid] = {"num": num, "purposes": pinfo}
    return prompts, pairs, meta


_PH = re.compile(r"@@[A-Z_]+@@")


def placeholders(path):
    return sorted(set(_PH.findall(open(path, encoding="utf-8").read())))
