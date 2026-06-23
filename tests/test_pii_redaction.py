"""
Step 2 DoD 検証テスト — 構造PII redaction（仕様書 B-1 MVP）

DoD: メール/電話/URL/郵便番号/カード様数字/@ハンドルを正規表現でマスクし、
     supporting_raw→supporting_redacted を生成、status を 'structural_done' に。
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pii_redaction import (
    redact_text, redact_supporting, redact_profile_fields, redact_for_storage,
    STATUS_STRUCTURAL_DONE, MASK,
)


def test_email_masked():
    assert redact_text("連絡は alice@example.com まで") == "連絡は [EMAIL] まで"


def test_phone_masked():
    out = redact_text("電話 090-1234-5678 です")
    assert MASK["phone"] in out
    assert "1234" not in out


def test_url_masked():
    assert "[URL]" in redact_text("詳しくは https://example.com/path?q=1 を見て")
    assert "[URL]" in redact_text("www.example.org も")


def test_zip_masked():
    assert "[ZIP]" in redact_text("〒123-4567 東京都")
    assert "[ZIP]" in redact_text("郵便番号 100-0001")


def test_card_masked():
    out = redact_text("カード 4111 1111 1111 1111 を使った")
    assert MASK["card"] in out
    assert "4111" not in out


def test_handle_masked():
    out = redact_text("私のXは @alice_2025 です")
    assert MASK["handle"] in out
    assert "alice_2025" not in out


def test_email_not_misdetected_as_handle():
    """email を先に処理するので @ 以降がハンドル誤爆しないこと。"""
    out = redact_text("bob@example.com")
    assert out == "[EMAIL]"
    assert "[HANDLE]" not in out


def test_unfetched_preserved():
    """"未取得" は捏造・改変せずそのまま（I章: 欠落を埋めない）。"""
    assert redact_text("未取得") == "未取得"


def test_non_string_passthrough():
    assert redact_text(None) is None
    assert redact_text("") == ""


def test_plain_text_unchanged():
    """PII の無い普通の語りは変えない（過剰マスクしない）。"""
    will = "地方の農家と都市の飲食店を直接つなぐ仕組みを作りたい"
    assert redact_text(will) == will


def test_redact_supporting_recursive():
    """supporting の list / str を再帰 redaction、"未取得" は保持。"""
    raw = {
        "生テキスト": ["連絡は test@a.com", "電話は 03-1234-5678"],
        "要約文": "農業に関わってきた人。https://blog.example.com で発信",
        "求めている": "未取得",
        "意志要求の素材": "一緒に背負える人がいい",
    }
    out = redact_supporting(raw)
    assert out["生テキスト"][0] == "連絡は [EMAIL]"
    assert MASK["phone"] in out["生テキスト"][1]
    assert "[URL]" in out["要約文"]
    assert out["求めている"] == "未取得"          # 保持
    assert out["意志要求の素材"] == "一緒に背負える人がいい"  # PII 無し→不変
    # 原本は破壊されていない（非破壊）
    assert raw["生テキスト"][0] == "連絡は test@a.com"


def test_redact_for_storage_returns_status():
    """登録フロー口: (redacted, status) を返し status が structural_done。"""
    redacted, status = redact_for_storage({"要約文": "mail: x@y.com"})
    assert status == STATUS_STRUCTURAL_DONE
    assert redacted["要約文"] == "mail: [EMAIL]"


def test_redact_profile_fields_nested():
    """①v4 ネスト形式（seeker.意志 / seeker.現状）も redaction できること。"""
    profile = {
        "seeker": {
            "意志": "連絡 alice@example.com",
            "現状": {"持っているもの": "電話 090-0000-0000", "未分類": ""},
        },
        "supporting_material": {"要約文": "https://x.io"},
    }
    out = redact_profile_fields(profile)
    assert out["seeker"]["意志"] == "連絡 [EMAIL]"
    assert MASK["phone"] in out["seeker"]["現状"]["持っているもの"]
    assert "[URL]" in out["supporting_material"]["要約文"]
    # 非破壊
    assert profile["seeker"]["意志"] == "連絡 alice@example.com"


def test_redact_profile_fields_flat():
    """profiles_v4 フラット列名形式も redaction できること。"""
    profile = {"will_text": "x@y.com", "state_bound": "tel 03-1111-2222"}
    out = redact_profile_fields(profile)
    assert out["will_text"] == "[EMAIL]"
    assert MASK["phone"] in out["state_bound"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nStep 2 DoD テスト: {len(tests)} 件 全 PASS")
