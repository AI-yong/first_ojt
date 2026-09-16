"""임베딩 생성과 검색용 벡터.

백엔드가 셋입니다. 환경에 따라 고르고, 나머지 코드는 어느 쪽이든 같습니다.

  gateway   OpenAI 호환 /v1/embeddings  (.env 의 LOCAL_API_BASE_URL)
            임베딩 모델을 내주는 OpenAI 호환 서버가 있으면 이게 제일 쉽습니다.
  local     transformers 로 직접 실행
            모델을 받아둔 환경에서 씁니다.
  lsa       numpy 만으로 TF-IDF + SVD
            네트워크도 추가 패키지도 없이 돕니다. 파이프라인 검증용이자,
            "사전학습 모델 없이 코퍼스에서 만든 임베딩" 기준선입니다.

백엔드를 바꿔도 store/tools 는 바뀌지 않습니다. 바뀌는 것은 벡터의 출처뿐입니다.


무엇을 임베딩하는가
-------------------
문서 쪽은 상품의 실제 필드인 `name + description` 입니다. 합성 detail이나
검색 태그는 사용하지 않습니다. 카테고리·색상·성별·가격·소재·재고는 SQL
필터가 정확하게 처리하므로 임베딩 문서에 다시 넣지 않습니다.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

import db

ROOT = Path(__file__).resolve().parent
LSA_MODEL_PATH = ROOT / "data" / "lsa_model.npz"

EMBED_DIM_LSA = 256


# --------------------------------------------------------------------------
# 임베딩 대상 텍스트
# --------------------------------------------------------------------------

def doc_text(product, detail_obj=None, axis_words=None):
    """이 상품의 임베딩 대상 문자열 — 상품명과 실제 설명만 사용한다.

    뒤의 두 인자는 과거 실험 스크립트의 호출 호환을 위해 받기만 합니다.
    합성 데이터나 검색 태그는 읽지 않습니다.
    """
    name = (product.get("name") or "").strip()
    description = (product.get("description") or "").strip()
    return ". ".join(value for value in (name, description) if value)


def build_doc_texts(conn):
    """{product_id: 임베딩 대상 문자열}"""
    out = {}
    for row in conn.execute(
        "SELECT product_id, name, description FROM products ORDER BY product_id"
    ):
        product = {k: row[k] for k in row.keys()}
        product["id"] = row["product_id"]
        out[row["product_id"]] = doc_text(product)
    return out


# --------------------------------------------------------------------------
# 공통
# --------------------------------------------------------------------------

def l2_normalize(matrix):
    """행마다 L2 정규화. 그러면 코사인 유사도가 내적이 된다."""
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim == 1:
        norm = np.linalg.norm(matrix) or 1.0
        return (matrix / norm).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class Backend:
    """encode(texts, kind) -> (n, dim) float32, L2 정규화된 행렬.

    kind 는 "passage" 또는 "query" 입니다. 임베딩 모델 상당수가 질의와 문서에
    다른 접두어를 요구하고(e5 의 "query: "/"passage: ", bge 의 지시문),
    안 붙이면 성능이 눈에 띄게 떨어집니다. 그 차이를 백엔드가 흡수합니다.
    """

    name = "base"
    dim = 0

    def encode(self, texts, kind="passage"):
        raise NotImplementedError


# --------------------------------------------------------------------------
# lsa — numpy 만으로. 네트워크·추가 패키지 없음
# --------------------------------------------------------------------------

_WORD = re.compile(r"[0-9A-Za-z가-힣]+")


def _tokens(text):
    """낱말 + 문자 2-gram.

    한국어는 조사·어미가 붙어서 낱말만 쓰면 "발볼이" 와 "발볼" 이 다른 토큰이
    됩니다(FTS 에서 실제로 0건이 나왔습니다). 문자 2-gram 을 함께 넣어
    그 어긋남을 흡수합니다.
    """
    text = text.lower()
    words = _WORD.findall(text)
    out = list(words)
    for w in words:
        if len(w) >= 2:
            out.extend(w[i:i + 2] for i in range(len(w) - 1))
    return out


class LsaBackend(Backend):
    name = "lsa"

    def __init__(self, dim=EMBED_DIM_LSA):
        self.dim = dim
        self.vocab = None      # {token: index}
        self.idf = None         # (V,)
        self.components = None  # (V, dim)  질의를 잠재공간으로 보내는 행렬

    # ---- 학습 -------------------------------------------------------
    def fit(self, texts, min_df=2, max_df_ratio=0.5):
        n = len(texts)
        token_lists = [_tokens(t) for t in texts]

        df = Counter()
        for toks in token_lists:
            df.update(set(toks))
        max_df = max_df_ratio * n
        vocab_terms = sorted(t for t, c in df.items() if min_df <= c <= max_df)
        self.vocab = {t: i for i, t in enumerate(vocab_terms)}
        self.idf = np.array(
            [math.log(n / df[t]) + 1.0 for t in vocab_terms], dtype=np.float32
        )

        X = self._tfidf(token_lists)                      # (n, V)
        # 경제적인 SVD: V 가 n 보다 크면 (n, n) 그램 행렬 쪽이 싸다.
        # 여기서는 numpy 의 축약 SVD 로 충분합니다 (2,419 x 수천).
        U, S, Vt = np.linalg.svd(X, full_matrices=False)
        k = min(self.dim, len(S))
        self.dim = k
        self.components = (Vt[:k].T).astype(np.float32)    # (V, k)
        doc_vecs = (U[:, :k] * S[:k]).astype(np.float32)
        return l2_normalize(doc_vecs)

    def _tfidf(self, token_lists):
        V = len(self.vocab)
        X = np.zeros((len(token_lists), V), dtype=np.float32)
        for i, toks in enumerate(token_lists):
            counts = Counter(t for t in toks if t in self.vocab)
            for t, c in counts.items():
                # sublinear tf. 같은 낱말이 열 번 나온다고 열 배 중요하지 않다.
                X[i, self.vocab[t]] = (1.0 + math.log(c)) * self.idf[self.vocab[t]]
        return l2_normalize(X)

    # ---- 사용 -------------------------------------------------------
    def encode(self, texts, kind="passage"):
        if self.components is None:
            raise RuntimeError("LsaBackend 가 학습되지 않았습니다. fit() 먼저.")
        X = self._tfidf([_tokens(t) for t in texts])
        return l2_normalize(X @ self.components)

    # ---- 저장/복원 --------------------------------------------------
    def save(self, path=LSA_MODEL_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        terms = np.array(sorted(self.vocab, key=self.vocab.get), dtype=object)
        np.savez_compressed(
            path, terms=terms, idf=self.idf, components=self.components
        )

    @classmethod
    def load(cls, path=LSA_MODEL_PATH):
        data = np.load(path, allow_pickle=True)
        obj = cls()
        terms = list(data["terms"])
        obj.vocab = {t: i for i, t in enumerate(terms)}
        obj.idf = data["idf"]
        obj.components = data["components"]
        obj.dim = obj.components.shape[1]
        return obj


# --------------------------------------------------------------------------
# gateway — OpenAI 호환 /v1/embeddings
# --------------------------------------------------------------------------

class GatewayBackend(Backend):
    name = "gateway"

    # 모델별 접두어. 모델 카드에 적혀 있고, 안 붙이면 성능이 떨어집니다.
    PREFIX = {
        "e5": {"passage": "passage: ", "query": "query: "},
        "bge": {"passage": "",
                "query": "Represent this sentence for searching relevant passages: "},
    }

    def __init__(self, model=None, base_url=None, api_key=None, dim=None, batch=64):
        import config
        self.model = model or os.environ.get("EMBED_MODEL_NAME", "")
        self.base_url = (base_url or config.LOCAL_API_BASE_URL).rstrip("/")
        self.api_key = api_key or getattr(config, "LOCAL_API_KEY", "") or ""
        self.batch = batch
        self.dim = dim or 0
        family = next((k for k in self.PREFIX if k in self.model.lower()), None)
        self.prefix = self.PREFIX.get(family, {"passage": "", "query": ""})

    def encode(self, texts, kind="passage"):
        import requests
        pre = self.prefix.get(kind, "")
        out = []
        for i in range(0, len(texts), self.batch):
            chunk = [pre + t for t in texts[i:i + self.batch]]
            res = requests.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": chunk},
                timeout=120,
            )
            res.raise_for_status()
            body = res.json()
            out.extend(item["embedding"] for item in body["data"])
        matrix = l2_normalize(np.array(out, dtype=np.float32))
        self.dim = matrix.shape[1]
        return matrix


# --------------------------------------------------------------------------
# local — transformers
# --------------------------------------------------------------------------

class LocalBackend(Backend):
    name = "local"

    def __init__(self, model_name="BAAI/bge-m3", batch=16):
        from transformers import AutoModel, AutoTokenizer
        import torch

        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModel.from_pretrained(model_name)
        self.model.eval()
        self.batch = batch
        self.dim = self.model.config.hidden_size
        family = next(
            (k for k in GatewayBackend.PREFIX if k in model_name.lower()), None
        )
        self.prefix = GatewayBackend.PREFIX.get(family, {"passage": "", "query": ""})

    def encode(self, texts, kind="passage"):
        torch = self.torch
        pre = self.prefix.get(kind, "")
        out = []
        with torch.no_grad():
            for i in range(0, len(texts), self.batch):
                chunk = [pre + t for t in texts[i:i + self.batch]]
                enc = self.tokenizer(
                    chunk, padding=True, truncation=True, max_length=256,
                    return_tensors="pt",
                )
                hidden = self.model(**enc).last_hidden_state
                mask = enc["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                out.append(pooled.cpu().numpy())
        return l2_normalize(np.vstack(out))


# --------------------------------------------------------------------------
# st — sentence-transformers
#
# LocalBackend 가 직접 mean pooling 을 하는 것과 달리, 이쪽은 모델이 들고 있는
# 풀링·정규화 설정을 그대로 씁니다. 모델마다 CLS 를 쓰는지 평균을 쓰는지가
# 다르고, 그걸 틀리면 성능이 떨어집니다. 모델을 받아둔 환경이면 이게 맞습니다.
# --------------------------------------------------------------------------

class SentenceTransformerBackend(Backend):
    name = "st"

    def __init__(self, model_name=None, batch=32, device=None):
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name or os.environ.get(
            "EMBED_MODEL_NAME", DEFAULT_MODEL_NAME)
        self.model = SentenceTransformer(self.model_name, device=device)
        self.batch = batch
        # sentence-transformers 6.x 에서 이름이 바뀌었습니다.
        # 옛 이름은 아직 도는데 FutureWarning 이 뜨고 언젠가 사라집니다.
        get_dim = getattr(self.model, "get_embedding_dimension", None) \
            or self.model.get_sentence_embedding_dimension
        self.dim = get_dim()

        # 접두어. 모델 카드가 요구하는 것을 붙여야 합니다.
        # sentence-transformers 가 prompts 를 들고 있으면 그쪽을 우선합니다.
        prompts = getattr(self.model, "prompts", None) or {}
        if prompts:
            self.prefix = {"query": prompts.get("query", ""),
                           "passage": prompts.get("passage",
                                                  prompts.get("document", ""))}
        else:
            family = next(
                (k for k in GatewayBackend.PREFIX if k in self.model_name.lower()),
                None)
            self.prefix = GatewayBackend.PREFIX.get(
                family, {"passage": "", "query": ""})

    def encode(self, texts, kind="passage"):
        pre = self.prefix.get(kind, "")
        vectors = self.model.encode(
            [pre + t for t in texts],
            batch_size=self.batch,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=len(texts) > 500,
        )
        return np.asarray(vectors, dtype=np.float32)


# --------------------------------------------------------------------------
# 백엔드 선택
# --------------------------------------------------------------------------

DEFAULT_BACKEND = "st"
DEFAULT_MODEL_NAME = "jhgan/ko-sroberta-multitask"


def make_backend(kind=None, conn=None, **kwargs):
    """백엔드를 만든다. 기본은 선정된 Sentence Transformer 모델이다.

    FastText 기본값은 삭제한 detail keywords 평가에서 정한 과거 설정입니다.
    현재 `name + description` v2 평가에서는 ko-sroberta가 가장 높았으므로
    별도 설정이 없을 때도 같은 모델을 사용합니다.
    """
    kind = kind or os.environ.get("EMBED_BACKEND") or DEFAULT_BACKEND
    if kind == "gateway":
        return GatewayBackend(**kwargs)
    if kind == "local":
        return LocalBackend(**kwargs)
    if kind == "st":
        return SentenceTransformerBackend(**kwargs)
    if kind == "lsa":
        return LsaBackend(**kwargs)
    if kind in ("ft", "w2v", "ppmi", "tfidf"):
        try:
            import embed_backends
            spec = f"{kind}:128" if kind in ("ft", "w2v", "ppmi") else kind
            return embed_backends.build(spec, conn)
        except ImportError:
            # gensim 이 없는 환경. 의존성 없이 도는 쪽으로 떨어진다.
            return LsaBackend(**kwargs)
    raise ValueError(f"모르는 백엔드: {kind}")


# --------------------------------------------------------------------------
# 인덱싱
# --------------------------------------------------------------------------

def build_index(conn, backend=None, verbose=True):
    """products.embedding 을 채운다. 벡터는 L2 정규화된 float32."""
    backend = backend or make_backend(conn=conn)
    texts_by_id = build_doc_texts(conn)
    ids = list(texts_by_id)
    texts = [texts_by_id[i] for i in ids]

    if isinstance(backend, LsaBackend):
        matrix = backend.fit(texts)
        backend.save()
    else:
        matrix = backend.encode(texts, kind="passage")
        # 낱말 벡터를 학습한 백엔드는 그 상태를 저장해야 질의를 같은 공간에서
        # 인코딩할 수 있습니다. 안 하면 앱이 뜰 때마다 재학습해야 하고,
        # 재학습하면 다른 공간이 나옵니다.
        if hasattr(backend, "save"):
            backend.save()

    conn.executemany(
        "UPDATE products SET embedding = ? WHERE product_id = ?",
        [(matrix[i].tobytes(), pid) for i, pid in enumerate(ids)],
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_backend', ?)",
        (backend.name,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_dim', ?)",
        (str(matrix.shape[1]),),
    )
    # 어떤 모델로 구웠는지 남깁니다. 질의를 다른 모델로 인코딩하면 다른
    # 공간이 되어 유사도가 무의미해집니다.
    model_name = getattr(backend, "model_name", None) or getattr(
        backend, "model", "") or ""
    if isinstance(model_name, str) and model_name:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value)"
            " VALUES ('embed_model', ?)", (model_name,))
    conn.commit()
    if verbose:
        print(f"임베딩 {matrix.shape[0]:,}개 x {matrix.shape[1]}차원  "
              f"백엔드={backend.name}")
        empty = sum(1 for t in texts if not t.strip())
        print(f"  빈 텍스트 {empty}개 · 평균 길이 "
              f"{sum(len(t) for t in texts)//max(len(texts),1)}자")
    return matrix


def load_matrix(conn):
    """(ids, matrix) 를 돌려준다. 아직 인덱싱 안 됐으면 (None, None)."""
    dim_row = conn.execute(
        "SELECT value FROM meta WHERE key = 'embed_dim'"
    ).fetchone()
    if dim_row is None:
        return None, None
    dim = int(dim_row[0])
    ids, blobs = [], []
    for row in conn.execute(
        "SELECT product_id, embedding FROM products"
        " WHERE embedding IS NOT NULL ORDER BY product_id"
    ):
        ids.append(row[0])
        blobs.append(np.frombuffer(row[1], dtype=np.float32))
    if not ids:
        return None, None
    return ids, np.vstack(blobs).reshape(len(ids), dim)


def query_backend(conn):
    """질의를 인코딩할 백엔드. DB 에 기록된 것과 같은 종류로 만든다."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'embed_backend'"
    ).fetchone()
    kind = row[0] if row else DEFAULT_BACKEND
    if kind == "lsa":
        return LsaBackend.load()
    if kind in ("ft", "w2v"):
        import embed_backends
        return embed_backends.GensimBackend.load()
    model_row = conn.execute(
        "SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
    if kind in ("st", "local", "gateway") and model_row:
        return make_backend(kind, conn=conn, model_name=model_row[0]) \
            if kind != "gateway" else make_backend(kind, model=model_row[0])
    return make_backend(kind, conn=conn)
