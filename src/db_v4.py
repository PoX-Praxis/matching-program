"""
PoX v4 データアクセス + 取り込み/照合オーケストレーション（仕様書 F章 / Step 6）

非破壊: 既存 v3.1 フロー（seekers/profiles/run_matching）には触れない。
        v4 は profiles_v4 / profile_vectors / derived_necessity / ledger_v4 を
        独自に読み書きし、既存テーブルと並存する（B章/F-1）。

層の責務:
  ① 構造化（外部）→ profile_input(JSON)
  本モジュール     → redact → ②necessity_gen → build_vectors → 永続化（取り込み）
                     → load → shortlist → matcher_v4 → ledger_v4（照合）
  ② / 照合の数値（s,u,γ,p,α,β）は derived_necessity が唯一の所有者（I章）。

ストア抽象（VectorStore）:
  PostgresStore : 本番。pgvector / HNSW を使う。
  MemoryStore   : テスト/ローカル。Postgres 無しで全オーケストレーションを検証。
照合の意味論は両ストアで一致させる（shortlist は3経路の256次元 union）。
"""
from embedding_config import MODEL_TAG, SHORT_DIM, FULL_DIM
from pii_redaction import redact_for_storage, redact_profile_fields
from necessity_gen import generate_necessity
from embedding_service import build_vectors, cosine
from matcher_v4 import rank_candidates
from match_config import SHORTLIST_K

# profile_vectors の full / short 列（B-2: 4ベクトル×2次元）
_FULL_KEYS  = ("will_symmetric", "will_passage", "state_passage", "necessity_query")
_SHORT_KEYS = ("will_sym_256", "will_pas_256", "state_pas_256", "necessity_q_256")

# 候補側 256 インデックス列と、それに当てる seeker 側 query 256 列（E-1 3経路）
#   b: necessity_q(seeker) -> state_pas(candidate)  主補完
#   a: will_sym(seeker)    -> will_sym(candidate)   共鳴
#   c: will_pas(seeker)    -> will_pas(candidate)   意志補完
_SHORTLIST_PATHS = (
    ("necessity_q_256", "state_pas_256"),
    ("will_sym_256",    "will_sym_256"),
    ("will_pas_256",    "will_pas_256"),
)

# profiles_v4 のフラット列（取り込みで保存）
_PROFILE_FIELDS = ("will_text", "state_have", "state_can_type",
                   "state_bound", "state_unsorted")


# ── 取り込み（登録/更新）─────────────────────────────────────────────────────
def ingest_profile_v4(store, profile_id, profile_input, *,
                      generator_fn=None, model_tag=MODEL_TAG, migrated_from=None):
    """
    ① の構造化出力（profile_input）を取り込み、profiles_v4 / derived_necessity /
    profile_vectors を一括保存する（F章の登録/更新フロー）。

    profile_input : {will_text, state_have, state_can_type, state_bound,
                     state_unsorted, supporting_raw(dict)}。
    migrated_from : v3.1 等からの移行時に出所を記録（F-5。新規登録は None）。
    手順（I章遵守）:
      1. supporting_raw を構造 PII redact（raw は保存するが embedding/LLM には出さない）
      2. ② necessity_gen で必要像と s,u,γ,p,α,β を生成（redacted のみ入力）
      3. build_vectors で 4ベクトル（full+short）を生成（防御 redact 込み）
      4. profiles_v4 / derived_necessity / profile_vectors を保存
    戻り値: {"profile_id", "necessity": <derived_necessity dict>}。
    """
    supporting_raw = profile_input.get("supporting_raw") or {}
    supporting_redacted, pii_status = redact_for_storage(supporting_raw)

    # profiles_v4 行（フラット列は redact_profile_fields で防御 redact）
    flat = {k: profile_input.get(k, "") for k in _PROFILE_FIELDS}
    flat_redacted = redact_profile_fields(flat)
    flat_redacted["supporting_redacted"] = supporting_redacted

    # ② 必要像生成（redacted のみ。s,u,γ,p,α,β を所有）
    necessity = generate_necessity(
        {**flat_redacted, "supporting_redacted": supporting_redacted},
        generator_fn=generator_fn, model_tag=model_tag,
    )

    # 4ベクトル生成（build_vectors が入力を防御 redact）
    vectors = build_vectors(flat_redacted, necessity["necessity_text"])

    store.save_profile(
        profile_id,
        fields={k: profile_input.get(k, "") for k in _PROFILE_FIELDS},
        supporting_raw=supporting_raw,
        supporting_redacted=supporting_redacted,
        pii_redaction_status=pii_status,
        migrated_from=migrated_from,
    )
    store.save_necessity(profile_id, model_tag, necessity)
    store.save_vectors(profile_id, model_tag, vectors)

    return {"profile_id": profile_id, "necessity": necessity}


# ── 照合 ─────────────────────────────────────────────────────────────────────
def match_v4(store, seeker_id, *, model_tag=MODEL_TAG,
             shortlist_k=SHORTLIST_K, top_k=None, write_ledger=True):
    """
    seeker_id を起点に候補をランキング（E章）。

    手順:
      1. seeker の vectors + derived_necessity（γ,p,α,β）を取得
      2. 候補プールを 256 次元 3経路で shortlist（同 model_tag・is_active のみ / I章）
      3. shortlist の full ベクトルで matcher_v4.rank_candidates（全次元 nested complement）
      4. 上位を ledger_v4 に監査記録（F章）
    戻り値: {"seeker_id", "model_tag", "results":[{candidate_id,score,attribution}...],
             "shortlist_size", "pool_size"}。
    """
    seeker = store.get_bundle(seeker_id, model_tag)
    if seeker is None:
        raise ValueError(f"seeker_id={seeker_id!r} の v4 ベクトルが見つかりません")

    nec = seeker["necessity"]
    gamma = nec.get("gamma", 0.0)
    p     = nec.get("p_sharpness", 0.0)
    alpha = nec.get("alpha", 1.0)
    beta  = nec.get("beta", 1.0)

    shortlist_ids = store.shortlist(seeker_id, seeker["vectors"], model_tag, shortlist_k)
    pool_size = store.pool_size(seeker_id, model_tag)

    candidate_bundles = store.get_bundles(shortlist_ids, model_tag)
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
        "shortlist_size": len(shortlist_ids),
        "pool_size": pool_size,
    }


# ── shortlist 共通ロジック（両ストアで意味論を一致させる）────────────────────
def _union_shortlist(seeker_short, candidates_short, k):
    """
    3経路（_SHORTLIST_PATHS）の 256次元コサイン近傍 k 件の union を返す。
    seeker_short     : {short_key: vec} seeker 側
    candidates_short : {cid: {short_key: vec}} 候補側
    """
    chosen = set()
    for seeker_key, cand_key in _SHORTLIST_PATHS:
        q = seeker_short.get(seeker_key)
        if q is None:
            continue
        scored = [(cid, cosine(q, sv[cand_key]))
                  for cid, sv in candidates_short.items() if cand_key in sv]
        scored.sort(key=lambda x: x[1], reverse=True)
        chosen.update(cid for cid, _ in scored[:k])
    return chosen


# ── MemoryStore（テスト/ローカル。Postgres 不要）──────────────────────────────
class MemoryStore:
    """
    全 v4 オーケストレーションを Postgres 無しで検証するためのインメモリ実装。
    PostgresStore と同じ意味論（同 model_tag・is_active 相当・3経路 union shortlist）。
    """
    def __init__(self):
        self.profiles = {}                 # profile_id -> dict
        self.necessity = {}                # (pid, model_tag) -> dict
        self.vectors = {}                  # (pid, model_tag) -> dict
        self.ledger = []                   # 監査イベント

    def save_profile(self, profile_id, *, fields, supporting_raw,
                     supporting_redacted, pii_redaction_status, migrated_from=None):
        self.profiles[profile_id] = {
            "id": profile_id, **fields,
            "supporting_raw": supporting_raw,
            "supporting_redacted": supporting_redacted,
            "pii_redaction_status": pii_redaction_status,
            "migrated_from": migrated_from,
        }

    def save_necessity(self, profile_id, model_tag, necessity):
        self.necessity[(profile_id, model_tag)] = dict(necessity)

    def save_vectors(self, profile_id, model_tag, vectors):
        self.vectors[(profile_id, model_tag)] = dict(vectors)

    def has_bundle(self, profile_id, model_tag):
        """profile_vectors に当該 (profile_id, model_tag) が既にあるか（遅延移行の冪等判定）。"""
        return (profile_id, model_tag) in self.vectors

    def get_bundle(self, profile_id, model_tag):
        key = (profile_id, model_tag)
        if key not in self.vectors:
            return None
        return {"vectors": self.vectors[key],
                "necessity": self.necessity.get(key, {})}

    def get_bundles(self, profile_ids, model_tag):
        out = {}
        for pid in profile_ids:
            b = self.get_bundle(pid, model_tag)
            if b is not None:
                out[pid] = b
        return out

    def _active_candidates_short(self, seeker_id, model_tag):
        return {
            pid: {sk: v[sk] for sk in _SHORT_KEYS}
            for (pid, mt), v in self.vectors.items()
            if mt == model_tag and pid != seeker_id
        }

    def shortlist(self, seeker_id, seeker_vectors, model_tag, k):
        seeker_short = {sk: seeker_vectors[sk] for sk in _SHORT_KEYS}
        cands = self._active_candidates_short(seeker_id, model_tag)
        return _union_shortlist(seeker_short, cands, k)

    def pool_size(self, seeker_id, model_tag):
        return sum(1 for (pid, mt) in self.vectors
                   if mt == model_tag and pid != seeker_id)

    def write_ledger(self, seeker_id, candidate_id, event, payload):
        self.ledger.append({"seeker_id": seeker_id, "candidate_id": candidate_id,
                            "event": event, "payload": payload})


# ── PostgresStore（本番。pgvector / HNSW）──────────────────────────────────────
class PostgresStore:
    """
    本番ストア。get_connection（DATABASE_URL → Postgres）越しに v4 テーブルを読み書き。
    照合は WHERE model_tag=:tag AND is_active=true で必ず絞る（I章 跨プール禁止）。
    shortlist は候補側 256 列の HNSW（vector_cosine_ops, <=> 演算子）で 3経路 union。
    """
    def __init__(self, db_path="pox.db"):
        from db_connect import get_connection, is_postgres
        if not is_postgres():
            raise RuntimeError("PostgresStore は DATABASE_URL（Postgres）が必要です。")
        self._get_connection = get_connection
        self.db_path = db_path

    @staticmethod
    def _vec(v):
        """list[float] を pgvector のテキスト表現 '[a,b,...]' に変換。"""
        return "[" + ",".join(repr(float(x)) for x in v) + "]"

    def save_profile(self, profile_id, *, fields, supporting_raw,
                     supporting_redacted, pii_redaction_status, migrated_from=None):
        import json
        with self._get_connection(self.db_path) as con:
            con.execute(
                """
                INSERT INTO profiles_v4
                    (id, will_text, state_have, state_can_type, state_bound,
                     state_unsorted, supporting_raw, supporting_redacted,
                     pii_redaction_status, migrated_from, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
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
                    updated_at           = now()
                """,
                (profile_id, fields.get("will_text", ""), fields.get("state_have"),
                 fields.get("state_can_type"), fields.get("state_bound"),
                 fields.get("state_unsorted"),
                 json.dumps(supporting_raw, ensure_ascii=False),
                 json.dumps(supporting_redacted, ensure_ascii=False),
                 pii_redaction_status, migrated_from),
            )

    def has_bundle(self, profile_id, model_tag):
        """profile_vectors に当該行があるか（遅延移行の冪等判定）。"""
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                """SELECT 1 FROM profile_vectors
                   WHERE profile_id = %s AND model_tag = %s LIMIT 1""",
                (profile_id, model_tag),
            ).fetchone()
        return row is not None

    def save_necessity(self, profile_id, model_tag, n):
        with self._get_connection(self.db_path) as con:
            con.execute(
                """
                INSERT INTO derived_necessity
                    (profile_id, model_tag, necessity_text, is_generated, gate_s,
                     gate_u, gamma, p_sharpness, alpha, beta, evidence_span,
                     src_input_hash, generator_prompt_version, generator_model_tag,
                     generated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (profile_id, model_tag) DO UPDATE SET
                    necessity_text           = EXCLUDED.necessity_text,
                    is_generated             = EXCLUDED.is_generated,
                    gate_s                   = EXCLUDED.gate_s,
                    gate_u                   = EXCLUDED.gate_u,
                    gamma                    = EXCLUDED.gamma,
                    p_sharpness              = EXCLUDED.p_sharpness,
                    alpha                    = EXCLUDED.alpha,
                    beta                     = EXCLUDED.beta,
                    evidence_span            = EXCLUDED.evidence_span,
                    src_input_hash           = EXCLUDED.src_input_hash,
                    generator_prompt_version = EXCLUDED.generator_prompt_version,
                    generator_model_tag      = EXCLUDED.generator_model_tag,
                    generated_at             = now()
                """,
                (profile_id, model_tag, n["necessity_text"], n.get("is_generated", True),
                 n["gate_s"], n["gate_u"], n["gamma"], n["p_sharpness"], n["alpha"],
                 n["beta"], n.get("evidence_span", ""), n["src_input_hash"],
                 n["generator_prompt_version"], n["generator_model_tag"]),
            )

    def save_vectors(self, profile_id, model_tag, v):
        cols = _FULL_KEYS + _SHORT_KEYS
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
        bundles = self.get_bundles([profile_id], model_tag)
        return bundles.get(profile_id)

    def get_bundles(self, profile_ids, model_tag):
        import json
        ids = list(profile_ids)
        if not ids:
            return {}
        ph = ",".join(["%s"] * len(ids))
        vcols = ", ".join(_FULL_KEYS + _SHORT_KEYS)
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
                    WHERE model_tag = %s AND profile_id IN ({ph})""",
                (model_tag, *ids),
            ).fetchall()
        nec_by_id = {r[0]: {
            "necessity_text": r[1], "gate_s": r[2], "gate_u": r[3], "gamma": r[4],
            "p_sharpness": r[5], "alpha": r[6], "beta": r[7], "evidence_span": r[8],
        } for r in nrows}
        keys = _FULL_KEYS + _SHORT_KEYS
        for r in rows:
            pid = r[0]
            vecs = {}
            for i, k in enumerate(keys, start=1):
                val = r[i]
                vecs[k] = json.loads(val) if isinstance(val, str) else list(val)
            out[pid] = {"vectors": vecs, "necessity": nec_by_id.get(pid, {})}
        return out

    def shortlist(self, seeker_id, seeker_vectors, model_tag, k):
        chosen = set()
        with self._get_connection(self.db_path) as con:
            for seeker_key, cand_key in _SHORTLIST_PATHS:
                q = self._vec(seeker_vectors[seeker_key])
                rows = con.execute(
                    f"""SELECT profile_id FROM profile_vectors
                        WHERE model_tag = %s AND is_active = true
                          AND profile_id != %s
                        ORDER BY {cand_key} <=> %s::vector
                        LIMIT %s""",
                    (model_tag, seeker_id, q, k),
                ).fetchall()
                chosen.update(r[0] for r in rows)
        return chosen

    def pool_size(self, seeker_id, model_tag):
        with self._get_connection(self.db_path) as con:
            row = con.execute(
                """SELECT count(*) FROM profile_vectors
                   WHERE model_tag = %s AND is_active = true AND profile_id != %s""",
                (model_tag, seeker_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def write_ledger(self, seeker_id, candidate_id, event, payload):
        import json
        with self._get_connection(self.db_path) as con:
            con.execute(
                """INSERT INTO ledger_v4 (seeker_id, candidate_id, event, payload)
                   VALUES (%s,%s,%s,%s)""",
                (seeker_id, candidate_id, event,
                 json.dumps(payload, ensure_ascii=False)),
            )
