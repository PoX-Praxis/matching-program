"""
PoX v4 データアクセス + 取り込み/照合オーケストレーション（仕様書 F章 / Step 6）

非破壊: 既存 v3.1 フロー（seekers/profiles/run_matching）には触れない。
        v4 は profiles_v4 / profile_vectors / derived_necessity / ledger_v4 を
        独自に読み書きし、既存テーブルと並存する（B章/F-1）。

束2a（2026-07）変更:
  - フル次元総当たり（スケール戦略 stage1）。256次元 shortlist は廃止。
    照合は同 model_tag・is_active の候補全件を full ベクトルで rank する。
  - derived_necessity 履歴保存化：save_necessity は旧有効行を superseded_at で無効化し
    新行を積む（最新 = superseded_at IS NULL）。generator_name（利用者申告AI名）を保存。
  - 非同期生成のため、取り込みを「受付（profile+necessity 保存）」と
    「ベクトル化（build_vectors+save_vectors）」に分割可能にした。generation_status を持つ。

層の責務:
  ① 構造化（外部）→ profile_input(JSON)（＋各自AI生成の必要像フィールドがあれば同梱）
  本モジュール     → redact → ②necessity（受領 or 生成）→ build_vectors → 永続化
  ② / 照合の数値（s,u,γ,p,α,β）は derived_necessity が唯一の所有者（I章）。
"""
from embedding_config import MODEL_TAG, FULL_DIM
from pii_redaction import redact_for_storage, redact_profile_fields
from necessity_gen import generate_necessity, needs_regeneration
from embedding_service import build_vectors, cosine  # noqa: F401 (cosine は将来用)
from matcher_v4 import rank_candidates

# profile_vectors のフル列（束2a: 256系は廃止）
_FULL_KEYS = ("will_symmetric", "will_passage", "state_passage", "necessity_query")

# profiles_v4 のフラット列（取り込みで保存）
_PROFILE_FIELDS = ("will_text", "state_have", "state_can_type",
                   "state_bound", "state_unsorted")

# 生成状態
GEN_PREPARING = "preparing"
GEN_READY     = "ready"
GEN_ERROR     = "error"
GEN_NEEDS_REGEN = "needs_regeneration"


def _necessity_row(n):
    """save 用に necessity dict を正規化（欠損キーを補完）。"""
    return {
        "necessity_text": n["necessity_text"],
        "is_generated": n.get("is_generated", True),
        "gate_s": n.get("gate_s"), "gate_u": n.get("gate_u"),
        "gamma": n.get("gamma"), "p_sharpness": n.get("p_sharpness"),
        "alpha": n.get("alpha"), "beta": n.get("beta"),
        "evidence_span": n.get("evidence_span", ""),
        "src_input_hash": n.get("src_input_hash"),
        "generator_prompt_version": n.get("generator_prompt_version"),
        "generator_model_tag": n.get("generator_model_tag"),
        "generator_name": n.get("generator_name"),
    }


# ── 取り込み（受付＝同期）─────────────────────────────────────────────────────
def receive_profile_v4(store, profile_id, profile_input, necessity=None, *,
                       model_tag=MODEL_TAG, migrated_from=None,
                       generation_status=GEN_PREPARING):
    """
    受付（同期・高速）: profiles_v4（既定 status=preparing）を保存する。
    ベクトル化は行わない（vectorize_profile_v4 を非同期で呼ぶ）。

    necessity:
      - dict（各自AI生成を受領 or フォールバック生成済み）→ derived_necessity も保存。
      - None（フォールバック経路の受付。必要像はまだ非同期生成前）→ profile のみ保存。
    """
    supporting_raw = profile_input.get("supporting_raw") or {}
    supporting_redacted, pii_status = redact_for_storage(supporting_raw)

    store.save_profile(
        profile_id,
        fields={k: profile_input.get(k, "") for k in _PROFILE_FIELDS},
        supporting_raw=supporting_raw,
        supporting_redacted=supporting_redacted,
        pii_redaction_status=pii_status,
        migrated_from=migrated_from,
        generation_status=generation_status,
    )
    if necessity is not None:
        store.save_necessity(profile_id, model_tag, _necessity_row(necessity))
    return {"profile_id": profile_id, "necessity": necessity}


def generate_necessity_v4(store, profile_id, profile_input, *,
                          generator_fn=None, model_tag=MODEL_TAG):
    """
    フォールバック（判断B）: サーバー側 ② でnecessityを生成し derived_necessity に保存する。
    ANTHROPIC_API_KEY の有無判定は呼び出し側（非同期ジョブ）が行う。
    ② には redacted のみ渡す（I章）。生成物 dict を返す。
    """
    flat = {k: profile_input.get(k, "") for k in _PROFILE_FIELDS}
    flat_redacted = redact_profile_fields(flat)
    supporting_redacted, _ = redact_for_storage(profile_input.get("supporting_raw") or {})
    necessity = generate_necessity(
        {**flat_redacted, "supporting_redacted": supporting_redacted},
        generator_fn=generator_fn, model_tag=model_tag,
    )
    store.save_necessity(profile_id, model_tag, _necessity_row(necessity))
    return necessity


def check_and_flag_regeneration(store, profile_id, *, model_tag=MODEL_TAG):
    """
    B-3: 意志/現状（＋素材/prompt/model_tag）が編集され、保存済み necessity の
    src_input_hash と食い違う場合に generation_status=needs_regeneration を立てる。
    戻り値: True=再生成が必要（フラグを立てた） / False=最新（何もしない）。
    保存済み necessity が無い場合は判定不能として False。
    """
    profile = store.get_profile(profile_id)
    if profile is None:
        return False
    nec = store.get_necessity(profile_id, model_tag)
    if not nec or not nec.get("src_input_hash"):
        return False
    if needs_regeneration(profile, nec["src_input_hash"], model_tag=model_tag):
        store.set_generation_status(profile_id, GEN_NEEDS_REGEN, error=None)
        return True
    return False


def vectorize_profile_v4(store, profile_id, profile_input, necessity_text, *,
                         model_tag=MODEL_TAG):
    """
    ベクトル化（非同期で呼ぶ想定）: build_vectors → save_vectors → status=ready。
    失敗時は status=error + error 文言を保存し、例外を送出（呼び出し側でリトライ）。
    """
    try:
        flat = {k: profile_input.get(k, "") for k in _PROFILE_FIELDS}
        flat_redacted = redact_profile_fields(flat)
        vectors = build_vectors(flat_redacted, necessity_text)  # build_vectors 本体は不変（8ベクトル返す）
        store.save_vectors(profile_id, model_tag, vectors)      # 保存層で full のみ保存（_256 は落とす）
        store.set_generation_status(profile_id, GEN_READY, error=None)
    except Exception as e:
        store.set_generation_status(profile_id, GEN_ERROR, error=str(e)[:500])
        raise


def ingest_profile_v4(store, profile_id, profile_input, *,
                      generator_fn=None, model_tag=MODEL_TAG, migrated_from=None):
    """
    同期一括取り込み（移行・フォールバックの convenience）。
    必要像はサーバー生成（generate_necessity）。受付→ベクトル化を同期で通す。
    戻り値: {"profile_id", "necessity"}。
    """
    supporting_raw = profile_input.get("supporting_raw") or {}
    _, _ = redact_for_storage(supporting_raw)  # redact 副作用の一貫性（保存は receive で行う）
    flat = {k: profile_input.get(k, "") for k in _PROFILE_FIELDS}
    flat_redacted = redact_profile_fields(flat)
    supporting_redacted, _ = redact_for_storage(supporting_raw)

    necessity = generate_necessity(
        {**flat_redacted, "supporting_redacted": supporting_redacted},
        generator_fn=generator_fn, model_tag=model_tag,
    )
    receive_profile_v4(store, profile_id, profile_input, necessity,
                       model_tag=model_tag, migrated_from=migrated_from)
    vectorize_profile_v4(store, profile_id, profile_input,
                         necessity["necessity_text"], model_tag=model_tag)
    return {"profile_id": profile_id, "necessity": necessity}


# ── 照合（stage1: フル次元総当たり）───────────────────────────────────────────
def match_v4(store, seeker_id, *, model_tag=MODEL_TAG,
             shortlist_k=None, top_k=None, write_ledger=True):
    """
    seeker_id を起点に候補をランキング（E章・stage1 フル次元総当たり）。
    shortlist_k は後方互換のため受けるが stage1 では未使用（全件 rank）。
    """
    seeker = store.get_bundle(seeker_id, model_tag)
    if seeker is None:
        raise ValueError(f"seeker_id={seeker_id!r} の v4 ベクトルが見つかりません")

    nec = seeker["necessity"]
    gamma = nec.get("gamma", 0.0)
    p     = nec.get("p_sharpness", 0.0)
    alpha = nec.get("alpha", 1.0)
    beta  = nec.get("beta", 1.0)

    candidate_ids = store.candidate_ids(seeker_id, model_tag)  # 同 model_tag・is_active・全件
    candidate_bundles = store.get_bundles(candidate_ids, model_tag)
    cand_list = [(cid, b["vectors"]) for cid, b in candidate_bundles.items()]

    results = rank_candidates(
        seeker["vectors"], cand_list, gamma, p=p, alpha=alpha, beta=beta, top_k=top_k,
    )

    if write_ledger:
        for r in results:
            attr = r["attribution"]
            store.write_ledger(seeker_id, r["candidate_id"], "match_ranked", {
                "score": r["score"],
                "limiting_axis": attr["limiting_axis"],
                "a_sim": attr["a_sim"], "b_sim": attr["b_sim"], "c_sim": attr["c_sim"],
                "gamma": gamma, "p_sharpness": p, "alpha": alpha, "beta": beta,
                "model_tag": model_tag,
            })

    return {
        "seeker_id": seeker_id,
        "model_tag": model_tag,
        "results": results,
        "pool_size": len(cand_list),
    }


# ── MemoryStore（テスト/ローカル。Postgres 不要）──────────────────────────────
class MemoryStore:
    """
    全 v4 オーケストレーションを Postgres 無しで検証するためのインメモリ実装。
    PostgresStore と同じ意味論（同 model_tag・is_active 相当・フル次元総当たり・necessity履歴）。
    """
    def __init__(self):
        self.profiles = {}     # profile_id -> dict（generation_status/error 含む）
        self.necessity = {}    # (pid, model_tag) -> [dict, ...] 履歴（末尾が最新）
        self.vectors = {}      # (pid, model_tag) -> dict（full のみ）
        self.ledger = []

    def save_profile(self, profile_id, *, fields, supporting_raw,
                     supporting_redacted, pii_redaction_status, migrated_from=None,
                     generation_status=None):
        prev = self.profiles.get(profile_id, {})
        self.profiles[profile_id] = {
            "id": profile_id, **fields,
            "supporting_raw": supporting_raw,
            "supporting_redacted": supporting_redacted,
            "pii_redaction_status": pii_redaction_status,
            "migrated_from": migrated_from,
            "generation_status": generation_status or prev.get("generation_status", GEN_PREPARING),
            "generation_error": None,
        }

    def set_generation_status(self, profile_id, status, error=None):
        if profile_id in self.profiles:
            self.profiles[profile_id]["generation_status"] = status
            self.profiles[profile_id]["generation_error"] = error

    def get_profile_status(self, profile_id):
        p = self.profiles.get(profile_id)
        return None if p is None else {
            "generation_status": p.get("generation_status"),
            "generation_error": p.get("generation_error"),
        }

    def get_profile(self, profile_id):
        """取り込み済みプロフィールを profile_input 形（flat+supporting）で返す（retry/再生成用）。"""
        p = self.profiles.get(profile_id)
        if p is None:
            return None
        out = {k: p.get(k, "") for k in _PROFILE_FIELDS}
        out["supporting_raw"] = p.get("supporting_raw") or {}
        out["supporting_redacted"] = p.get("supporting_redacted") or {}
        return out

    def save_necessity(self, profile_id, model_tag, necessity):
        # 履歴保存: 既存有効行はそのまま残し、新行を末尾に積む（末尾=最新）
        self.necessity.setdefault((profile_id, model_tag), []).append(_necessity_row(necessity))

    def get_necessity(self, profile_id, model_tag):
        hist = self.necessity.get((profile_id, model_tag))
        return dict(hist[-1]) if hist else None

    def save_vectors(self, profile_id, model_tag, vectors):
        self.vectors[(profile_id, model_tag)] = {k: list(vectors[k]) for k in _FULL_KEYS}

    def has_bundle(self, profile_id, model_tag):
        return (profile_id, model_tag) in self.vectors

    def get_bundle(self, profile_id, model_tag):
        key = (profile_id, model_tag)
        if key not in self.vectors:
            return None
        return {"vectors": self.vectors[key],
                "necessity": self.get_necessity(profile_id, model_tag) or {}}

    def get_bundles(self, profile_ids, model_tag):
        out = {}
        for pid in profile_ids:
            b = self.get_bundle(pid, model_tag)
            if b is not None:
                out[pid] = b
        return out

    def candidate_ids(self, seeker_id, model_tag):
        return [pid for (pid, mt) in self.vectors if mt == model_tag and pid != seeker_id]

    def write_ledger(self, seeker_id, candidate_id, event, payload):
        self.ledger.append({"seeker_id": seeker_id, "candidate_id": candidate_id,
                            "event": event, "payload": payload})


# ── PostgresStore（本番。pgvector）─────────────────────────────────────────────
class PostgresStore:
    """
    本番ストア。get_connection（DATABASE_URL → Postgres）越しに v4 テーブルを読み書き。
    照合は WHERE model_tag=:tag AND is_active=true で必ず絞る（I章 跨プール禁止）。
    束2a: 256 shortlist を廃止。candidate_ids で全件返し、full ベクトルで rank。
    """
    def __init__(self, db_path="pox.db"):
        from db_connect import get_connection, is_postgres
        if not is_postgres():
            raise RuntimeError("PostgresStore は DATABASE_URL（Postgres）が必要です。")
        self._get_connection = get_connection
        self.db_path = db_path

    @staticmethod
    def _vec(v):
        return "[" + ",".join(repr(float(x)) for x in v) + "]"

    def save_profile(self, profile_id, *, fields, supporting_raw,
                     supporting_redacted, pii_redaction_status, migrated_from=None,
                     generation_status=None):
        import json
        with self._get_connection(self.db_path) as con:
            con.execute(
                """
                INSERT INTO profiles_v4
                    (id, will_text, state_have, state_can_type, state_bound,
                     state_unsorted, supporting_raw, supporting_redacted,
                     pii_redaction_status, migrated_from, generation_status, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (id) DO UPDATE SET
                    will_text            = EXCLUDED.will_text,
                    state_have           = EXCLUDED.state_have,
                    state_can_type       = EXCLUDED.state_can_type,
                    state_bound          = EXCLUDED.state_bound,
                    state_unsorted       = EXCLUDED.state_unsorted,
                    supporting_raw       = EXCLUDED.supporting_raw,
                    supporting_redacted  = EXCLUDED.supporting_redacted,
                    pii_redaction_status = EXCLUDED.pii_redaction_status,
                    migrated_from        = EXCLUDED.migrated_from,
                    generation_status    = EXCLUDED.generation_status,
                    updated_at           = now()
                """,
                (profile_id, fields.get("will_text", ""), fields.get("state_have"),
                 fields.get("state_can_type"), fields.get("state_bound"),
                 fields.get("state_unsorted"),
                 json.dumps(supporting_raw, ensure_ascii=False),
                 json.dumps(supporting_redacted, ensure_ascii=False),
                 pii_redaction_status, migrated_from,
                 generation_status or GEN_PREPARING),
            )

    def set_generation_status(self, profile_id, status, error=None):
        with self._get_connection(self.db_path) as con:
            con.execute(
                "UPDATE profiles_v4 SET generation_status=%s, generation_error=%s, "
                "updated_at=now() WHERE id=%s",
                (status, error, profile_id),
            )

    def get_profile_status(self, profile_id):
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                "SELECT generation_status, generation_error FROM profiles_v4 WHERE id=%s",
                (profile_id,),
            ).fetchone()
        if not row:
            return None
        return {"generation_status": row[0], "generation_error": row[1]}

    def get_profile(self, profile_id):
        """取り込み済みプロフィールを profile_input 形（flat+supporting）で返す（retry/再生成用）。"""
        import json
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                """SELECT will_text, state_have, state_can_type, state_bound,
                          state_unsorted, supporting_raw, supporting_redacted
                   FROM profiles_v4 WHERE id=%s""",
                (profile_id,),
            ).fetchone()
        if not row:
            return None

        def _load(x):
            if isinstance(x, str):
                return json.loads(x)
            return x or {}

        return {
            "will_text": row[0], "state_have": row[1], "state_can_type": row[2],
            "state_bound": row[3], "state_unsorted": row[4],
            "supporting_raw": _load(row[5]), "supporting_redacted": _load(row[6]),
        }

    def has_bundle(self, profile_id, model_tag):
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                "SELECT 1 FROM profile_vectors WHERE profile_id=%s AND model_tag=%s LIMIT 1",
                (profile_id, model_tag),
            ).fetchone()
        return row is not None

    def save_necessity(self, profile_id, model_tag, n):
        """履歴保存: 旧有効行を superseded_at で無効化してから新行を INSERT（同一TX）。"""
        r = _necessity_row(n)
        with self._get_connection(self.db_path) as con:
            con.execute(
                "UPDATE derived_necessity SET superseded_at=now() "
                "WHERE profile_id=%s AND model_tag=%s AND superseded_at IS NULL",
                (profile_id, model_tag),
            )
            con.execute(
                """
                INSERT INTO derived_necessity
                    (profile_id, model_tag, necessity_text, is_generated, gate_s,
                     gate_u, gamma, p_sharpness, alpha, beta, evidence_span,
                     src_input_hash, generator_prompt_version, generator_model_tag,
                     generator_name, generated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                """,
                (profile_id, model_tag, r["necessity_text"], r["is_generated"],
                 r["gate_s"], r["gate_u"], r["gamma"], r["p_sharpness"], r["alpha"],
                 r["beta"], r["evidence_span"], r["src_input_hash"],
                 r["generator_prompt_version"], r["generator_model_tag"],
                 r["generator_name"]),
            )

    def get_necessity(self, profile_id, model_tag):
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                """SELECT necessity_text, gate_s, gate_u, gamma, p_sharpness, alpha, beta,
                          evidence_span, src_input_hash, generator_model_tag, generator_name
                   FROM derived_necessity
                   WHERE profile_id=%s AND model_tag=%s AND superseded_at IS NULL""",
                (profile_id, model_tag),
            ).fetchone()
        if not row:
            return None
        return {"necessity_text": row[0], "gate_s": row[1], "gate_u": row[2],
                "gamma": row[3], "p_sharpness": row[4], "alpha": row[5], "beta": row[6],
                "evidence_span": row[7], "src_input_hash": row[8],
                "generator_model_tag": row[9], "generator_name": row[10]}

    def save_vectors(self, profile_id, model_tag, v):
        cols = _FULL_KEYS  # 束2a: full のみ保存（_256 は保存しない）
        with self._get_connection(self.db_path) as con:
            placeholders = ",".join(["%s"] * (2 + len(cols)))
            setters = ",".join(f"{c} = EXCLUDED.{c}" for c in cols)
            con.execute(
                f"""
                INSERT INTO profile_vectors
                    (profile_id, model_tag, {", ".join(cols)}, is_active, embedded_at)
                VALUES ({placeholders}, true, now())
                ON CONFLICT (profile_id, model_tag) DO UPDATE SET
                    {setters}, is_active = true, embedded_at = now()
                """,
                (profile_id, model_tag, *[self._vec(v[c]) for c in cols]),
            )

    def get_bundle(self, profile_id, model_tag):
        return self.get_bundles([profile_id], model_tag).get(profile_id)

    def get_bundles(self, profile_ids, model_tag):
        import json
        ids = list(profile_ids)
        if not ids:
            return {}
        ph = ",".join(["%s"] * len(ids))
        vcols = ", ".join(_FULL_KEYS)
        out = {}
        with self._get_connection(self.db_path) as con:
            rows = con.execute(
                f"""SELECT profile_id, {vcols} FROM profile_vectors
                    WHERE model_tag = %s AND is_active = true
                      AND profile_id IN ({ph})""",
                (model_tag, *ids),
            ).fetchall()
            nrows = con.execute(
                f"""SELECT profile_id, necessity_text, gate_s, gate_u, gamma,
                           p_sharpness, alpha, beta, evidence_span
                    FROM derived_necessity
                    WHERE model_tag = %s AND superseded_at IS NULL
                      AND profile_id IN ({ph})""",
                (model_tag, *ids),
            ).fetchall()
        nec_by_id = {r[0]: {
            "necessity_text": r[1], "gate_s": r[2], "gate_u": r[3], "gamma": r[4],
            "p_sharpness": r[5], "alpha": r[6], "beta": r[7], "evidence_span": r[8],
        } for r in nrows}
        for r in rows:
            pid = r[0]
            vecs = {}
            for i, k in enumerate(_FULL_KEYS, start=1):
                val = r[i]
                vecs[k] = json.loads(val) if isinstance(val, str) else list(val)
            out[pid] = {"vectors": vecs, "necessity": nec_by_id.get(pid, {})}
        return out

    def candidate_ids(self, seeker_id, model_tag):
        """同 model_tag・is_active の候補 profile_id を全件返す（stage1 総当たり）。"""
        with self._get_connection(self.db_path) as con:
            rows = con.execute(
                """SELECT profile_id FROM profile_vectors
                   WHERE model_tag=%s AND is_active=true AND profile_id != %s""",
                (model_tag, seeker_id),
            ).fetchall()
        return [r[0] for r in rows]

    def write_ledger(self, seeker_id, candidate_id, event, payload):
        import json
        with self._get_connection(self.db_path) as con:
            con.execute(
                """INSERT INTO ledger_v4 (seeker_id, candidate_id, event, payload)
                   VALUES (%s,%s,%s,%s)""",
                (seeker_id, candidate_id, event,
                 json.dumps(payload, ensure_ascii=False)),
            )
