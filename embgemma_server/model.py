"""
EmbeddingGemma-300M 推論ラッパー（PoX embedding 実験 / exp/embedding-model-eval）

PoX 本体の embedding_service._embgemma_encode が叩く常駐推論の中核。
責務は「テキスト → 768 次元ベクトル」だけ。prefix は PoX 側
embedding_config.PREFIX で付与済みで送られてくるため、ここでは付け足さない。

注意: EmbeddingGemma は activations が float16 非対応。
      torch_dtype を bfloat16 か float32 にする（自動選択）。

次元: EmbeddingGemma-300M の出力は 768 次元（設計書 §5 / MODEL_DIMS）。
      実機で確認し、EMBGEMMA_DIM と一致しない場合は RuntimeError。
プーリング: sentence-transformers がモデル設定どおりに処理。
"""
import os

DEFAULT_MODEL = os.environ.get("EMBGEMMA_MODEL", "google/gemma-embedding-300m-v2")
EXPECTED_DIM  = int(os.environ.get("EMBGEMMA_DIM", "768"))
MODEL_TAG     = os.environ.get("EMBGEMMA_MODEL_TAG", "embgemma-300m")


class EmbGemmaEmbedder:
    """
    sentence-transformers を遅延ロードする EmbeddingGemma ラッパー。
    encode(text) -> list[float]（長さ EXPECTED_DIM、L2 正規化済み）。
    """
    def __init__(self, model_name=DEFAULT_MODEL, device=None, expected_dim=EXPECTED_DIM):
        self.model_name = model_name
        self.device = device or os.environ.get("EMBGEMMA_DEVICE")
        self.expected_dim = expected_dim
        self._model = None

    @property
    def loaded(self):
        return self._model is not None

    def warmup(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer
            # float16 非対応のため bfloat16 優先、なければ float32
            dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float32
            self._model = SentenceTransformer(
                self.model_name,
                device=self.device,
                model_kwargs={"torch_dtype": dtype},
            )
            # テストエンコードで実次元を確認し起動時にログ出力
            test_vec = self.encode_batch(["warmup"])[0]
            print(f"[EmbGemmaEmbedder] 実次元確認: {len(test_vec)} 次元 (期待値={self.expected_dim})")
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
            if len(v) != self.expected_dim:
                raise RuntimeError(
                    f"モデル出力次元 {len(v)} が EXPECTED_DIM={self.expected_dim} と不一致。"
                    f"EMBGEMMA_DIM 環境変数を実機次元に合わせてください。"
                )
            out.append(v)
        return out
