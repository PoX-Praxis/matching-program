"""
Qwen3-Embedding-0.6B 推論ラッパー（PoX v4 / C章 A-3）

PoX 本体の embedding_service._qwen3_encode が叩く常駐推論の中核。
責務は「テキスト → 1024 次元ベクトル」だけ。prefix（query instruction 等）は
PoX 側 embedding_config.PREFIX で既に付与済みで送られてくるため、ここでは
一切付け足さない（非対称ロジックの単一ソースを H-1 に保つ＝二重付与の禁止）。

次元: Qwen3-Embedding-0.6B のネイティブ出力は 1024（FULL_DIM と一致）。
      256 への MRL 切出しは PoX クライアント側で行う（サーバは full のみ返す）。
プーリング: Qwen3-Embedding は last-token pooling。sentence-transformers が
            モデルリポジトリの設定どおりに処理するため、ここで指定は不要。
"""
import os

DEFAULT_MODEL = os.environ.get("QWEN3_MODEL", "Qwen/Qwen3-Embedding-0.6B")
EXPECTED_DIM  = int(os.environ.get("QWEN3_DIM", "1024"))
# このサーバが供給するベクトルの model_tag（PoX 側 POX_EMBED_MODEL_TAG と一致必須）。
MODEL_TAG     = os.environ.get("QWEN3_MODEL_TAG", "qwen3-embedding-0.6b-d1024")


class Qwen3Embedder:
    """
    sentence-transformers を遅延ロードする Qwen3-Embedding ラッパー。
    encode(text) -> list[float]（長さ EXPECTED_DIM、L2 正規化済み）。

    遅延ロードの理由: import 時にモデル（~1.2GB）を掴まない。/health は即応、
    最初の /embed もしくは明示 warmup() で初めてロードする。
    """
    def __init__(self, model_name=DEFAULT_MODEL, device=None, expected_dim=EXPECTED_DIM):
        self.model_name = model_name
        self.device = device or os.environ.get("QWEN3_DEVICE")  # None=自動（cuda優先）
        self.expected_dim = expected_dim
        self._model = None

    @property
    def loaded(self):
        return self._model is not None

    def warmup(self):
        """モデルを実ロードする（起動時に呼べばコールドスタートを前倒しできる）。"""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    def encode(self, text):
        """単一テキストを 1024 次元 L2 正規化ベクトル（list[float]）にする。"""
        return self.encode_batch([text])[0]

    def encode_batch(self, texts):
        """複数テキストをまとめて埋め込む（list[list[float]]）。"""
        model = self.warmup()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,   # 単位ベクトル（クライアント側でも再正規化され冪等）
            convert_to_numpy=True,
        )
        out = []
        for vec in vectors:
            v = [float(x) for x in vec.tolist()]
            if len(v) != self.expected_dim:
                raise RuntimeError(
                    f"モデル出力次元 {len(v)} が EXPECTED_DIM={self.expected_dim} と不一致。"
                    f"QWEN3_MODEL={self.model_name} を確認してください。"
                )
            out.append(v)
        return out
