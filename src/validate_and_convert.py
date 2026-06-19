#!/usr/bin/env python3
"""接続成否ログ: スキーマ検証 + 7.1節「接続イベントの型」への変換。
使い方: python3 validate_and_convert.py samples.json
依存: pip install jsonschema --break-system-packages
"""
import json, sys
from jsonschema import Draft202012Validator

import os
SCHEMA = os.path.join(os.path.dirname(__file__), "..", "schemas", "connection_ledger.schema.json")

def to_connection_events(record):
    """台帳1件 → 7.1節の接続イベント列。established_at の無い参加は学習対象外。"""
    events = []
    for j in record.get("joins", []):
        if not j.get("established_at"):
            continue
        ts = j["terminal_state"]
        strong = 1.0 if ts == "advanced" else (0.0 if ts in ("dissolved","left") else None)
        events.append({
            "context_phase": record.get("phase_at_creation"),         # ①
            "partner": j["joiner"],                                    # ②
            "predicted_role": (j.get("candidate_source") or {}).get("predicted_role"),
            "weak_label_established": True,                            # 弱い正解
            "strong_label_state_change": strong,                       # 強い正解
        })
    return events

def main(path):
    schema = json.load(open(SCHEMA))
    Draft202012Validator.check_schema(schema)
    v = Draft202012Validator(schema)
    data = json.load(open(path))
    records = data if isinstance(data, list) else [data]
    ok = True
    for r in records:
        r.pop("_label", None)
        errs = sorted(v.iter_errors(r), key=lambda e: list(e.path))
        if errs:
            ok = False
            print(f"✗ {r.get('vessel_id')}")
            for e in errs: print("   ", list(e.path), e.message)
        else:
            evs = to_connection_events(r)
            print(f"✓ {r.get('vessel_id')}  接続イベント{len(evs)}件 -> {evs}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples.json")
