"""
Nomic Embed v2 推論ラッパー（PoX embedding 実験 / exp/embedding-model-eval）

PoX 本体の embedding_service._nomic_encode が叩く常駐推論の中核。
責務は「テキスト → ベクトル」だけ。prefix は PoX 側 embedding_config.PREFIX で
付与済みで送られてくるため、ここでは付け足さない。

次元: NOMIC_DIM 環境変数で指定（要実機確認）。
      実機で初回 encode した際に自動検出した次元を NOMIC_DIM と照合。
      不一致なら RuntimeError（設計書: 推測で埋めない）。
プーリング: sentence-transformers がモデル設定どおりに処理。
           Nomic v2 は trust_remote_code=True が必要。
"""
import os

DEFAULT_MODEL = os.environ.get("NOMIC_MODEL", "nomic-ai/nomic-embed-text-v2-moe")
# TODO: 実機で次元を確認して NOMIC_DIM に設定する。
#       起動時に auto_detect=True で実際の次元を出力するので確認すること。
EXPECTED_DIM  = int(os.environ.get("NOMIC_DIM", "0")) or None   # 0 = 自動検出
MODEL_TAG     = os.environ.get("NOMIC_MODEL_TAG", "nomic-emb-v2")

# 重み読み込み精度。既定 bfloat16 = メモリ約半減（fp32 の 1.9GB → ~1GB）で 2GB 級に載せる。
# fp32 に戻したい場合は NOMIC_TORCH_DTYPE=float32（4GB 以上のインスタンス想定）。
# 空/none/float32 → dtype 指定なし（ライブラリ既定 = fp32）。
TORCH_DTYPE   = os.environ.get("NOMIC_TORCH_DTYPE", "bfloat16").strip().lower()


class NomicEmbedder:
    """
    sentence-transformers を遅延ロードする Nomic Embed v2 ラッパー。
    encode(text) -> list[float]（L2 正規化済み）。
    """
    def __init__(self, model_name=DEFAULT_MODEL, device=None, expected_dim=EXPECTED_DIM):
        self.model_name = model_name
        self.device = device or os.environ.get("NOMIC_DEVICE")
        self.expected_dim = expected_dim   # None = 初回 encode で自動検出
        self._model = None
        self._detected_dim = None

    @property
    def loaded(self):
        return self._model is not None

    def warmup(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            # 重み精度を絞ってメモリ削減（既定 bfloat16）。2GB 級インスタンス対策。
            model_kwargs = {"low_cpu_mem_usage": True}
            if TORCH_DTYPE and TORCH_DTYPE not in ("float32", "fp32", "none", "default"):
                model_kwargs["torch_dtype"] = TORCH_DTYPE
                print(f"[NomicEmbedder] torch_dtype={TORCH_DTYPE} で読み込み（メモリ削減）")
            # Nomic は trust_remote_code=True が必要
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                trust_remote_code=True,
                model_kwargs=model_kwargs,
            )
            # テストエンコードで実次元を検出し起動時にログ出力
            self.encode_batch(["warmup"])
        return self._model

    def encode(self, text):
        return self.encode_batch([text])[0]

    def encode_batch(self, texts):
        model = self.warmup()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        out = []
        for vec in vectors:
            v = [float(x) for x in vec.tolist()]
            actual_dim = len(v)

            # 初回: 実次元を検出・ログ
            if self._detected_dim is None:
                self._detected_dim = actual_dim
                print(f"[NomicEmbedder] 実次元検出: {actual_dim} 次元。"
                      f" embedding_config.py の MODEL_DIMS['nomic-emb-v2'] と"
                      f" NOMIC_DIM 環境変数をこの値に設定してください。")

            # 期待次元が指定されていれば照合
            if self.expected_dim is not None and actual_dim != self.expected_dim:
                raise RuntimeError(
                    f"モデル出力次元 {actual_dim} が NOMIC_DIM={self.expected_dim} と不一致。"
                )
            out.append(v)
        return out

    @property
    def reported_dim(self):
        """/health に返す次元。検出済みなら実値、未検出なら期待値（または -1）。"""
        return self._detected_dim or self.expected_dim or -1
