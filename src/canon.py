#!/usr/bin/env python3
"""
PoX 台帳 v2 — 正準化とハッシュ（指示書17 §4-2）。

- 正準化は JCS（RFC 8785）に準拠する。本実装は台帳イベントで用いる値域
  （文字列・オブジェクト・配列・整数・真偽・null）に対して JCS と一致する
  実用サブセットである。浮動小数の ECMAScript 数値直列化は未対応だが、
  台帳イベントの payload は 16進ハッシュ・id・列挙・整数（seq / n）のみを
  持ち、実数を含めない設計のため問題は生じない（§4-2・数値素材は necessities
   側に置き content_hash に畳む）。
- ハッシュは SHA-256、16進小文字。

canon_version は正準化規則の版であり、将来規則を変えたとき過去イベントを
再計算できるようにするためにある。値を後から変えてはならない（§4-2）。
"""
import json
import hashlib

CANON_VERSION = "c1"


def canonicalize(obj) -> bytes:
    """JCS 実用サブセット: キー昇順・余白なし・UTF-8。"""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data) -> str:
    """bytes / str を SHA-256 の16進小文字にする。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(*parts) -> str:
    """本文ハッシュ: 与えた各パートを正準化して連結し SHA-256（本文は台帳に載せない）。"""
    joined = b"\x1f".join(
        canonicalize(p) if not isinstance(p, (bytes, bytearray)) else bytes(p)
        for p in parts
    )
    return sha256_hex(joined)
