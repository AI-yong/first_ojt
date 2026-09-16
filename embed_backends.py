"""임베딩 백엔드 추가분 — 코퍼스에서 직접 학습하는 방식들.

사전학습 모델을 받을 수 없는 환경에서도 파이프라인을 재고 비교하기 위한
것들입니다. 그리고 그 자체로 기준선이 됩니다 — "사전학습 없이 이 데이터만
보고 만든 벡터는 어디까지 하는가".

  tfidf    TF-IDF 코사인. 차원축소 없음. SVD 가 도움인지 해인지 가른다
  lsa      TF-IDF + SVD (embed.LsaBackend). 차원을 인자로 받는다
  ppmi     낱말-낱말 PPMI + SVD 로 낱말 벡터를 만들고 문서는 평균
  w2v      word2vec 을 코퍼스에 학습. 문서는 낱말 벡터 평균
  ft       fastText 를 코퍼스에 학습. subword 가 있어 처음 보는 낱말
           ("장마철")에도 벡터를 준다 - OOV 를 메우는지 보는 것이 목적

낱말 벡터를 학습하는 방식(ppmi/w2v/ft)도 실제 상품 필드만 사용한다.
문서 벡터의 기준은 제품 경로와 똑같이 `name + description`이다.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np

import embed
from embed import ROOT, Backend, l2_normalize, _tokens


def training_corpus(conn):
    """낱말 벡터 학습용 코퍼스. 실제 상품 필드만 사용한다."""
    docs = []
    for row in conn.execute(
        "SELECT name, description, category, material FROM products"
    ):
        docs.append(_tokens(" ".join([
            row["name"], row["description"], row["category"], row["material"],
        ])))
    return docs


# --------------------------------------------------------------------------
# tfidf — 차원축소 없음
# --------------------------------------------------------------------------

class TfidfBackend(Backend):
    name = "tfidf"

    def __init__(self, min_df=2, max_df_ratio=0.5):
        self.min_df, self.max_df_ratio = min_df, max_df_ratio
        self.vocab = None
        self.idf = None

    def fit(self, texts):
        n = len(texts)
        toks = [_tokens(t) for t in texts]
        df = Counter()
        for tk in toks:
            df.update(set(tk))
        terms = sorted(t for t, c in df.items()
                       if self.min_df <= c <= self.max_df_ratio * n)
        self.vocab = {t: i for i, t in enumerate(terms)}
        self.idf = np.array([math.log(n / df[t]) + 1.0 for t in terms],
                            dtype=np.float32)
        self.dim = len(terms)
        return self._matrix(toks)

    def _matrix(self, toks):
        X = np.zeros((len(toks), len(self.vocab)), dtype=np.float32)
        for i, tk in enumerate(toks):
            for t, c in Counter(t for t in tk if t in self.vocab).items():
                j = self.vocab[t]
                X[i, j] = (1.0 + math.log(c)) * self.idf[j]
        return l2_normalize(X)

    def encode(self, texts, kind="passage"):
        return self._matrix([_tokens(t) for t in texts])


# --------------------------------------------------------------------------
# 낱말 벡터를 문서 벡터로 모으는 방법
#
# 단순 평균은 이 데이터에서 무너집니다. 문서가 태그 4~5개뿐이고 그중 절반이
# 모든 상품에 공통이라(데일리 954 · 기본가 1120), 평균이 거의 같은 곳을
# 가리킵니다. 실측: 문서 다섯 개의 질의 유사도가 0.546~0.587 로 붙었고
# MRR 이 전 축에서 0.000 이었습니다.
#
# IDF 가중을 넣으면 흔한 태그의 영향이 줄어듭니다. LSA 가 되는 이유도
# TF-IDF 가중이 들어가 있기 때문입니다.
# --------------------------------------------------------------------------

class WordPooling:
    """낱말 벡터 -> 문서 벡터. idf 가중 여부를 고른다."""

    use_idf = True
    idf = None          # {token: weight}

    def fit_idf(self, docs):
        n = len(docs)
        df = Counter()
        for doc in docs:
            df.update(set(doc))
        self.idf = {w: math.log(n / c) + 1.0 for w, c in df.items()}
        return self

    def _weight(self, token):
        if not self.use_idf or self.idf is None:
            return 1.0
        return self.idf.get(token, math.log(len(self.idf) or 2) + 1.0)

    def _pool(self, tokens, lookup):
        num = None
        total = 0.0
        for tok in tokens:
            vec = lookup(tok)
            if vec is None:
                continue
            w = self._weight(tok)
            num = (vec * w) if num is None else num + vec * w
            total += w
        if num is None or total == 0:
            return np.zeros(self.dim, dtype=np.float32)
        return (num / total).astype(np.float32)


# --------------------------------------------------------------------------
# ppmi — 낱말-낱말 공출현 + SVD
# --------------------------------------------------------------------------

class PpmiBackend(WordPooling, Backend):
    name = "ppmi"

    def __init__(self, dim=128, window=5, min_count=3):
        self.dim, self.window, self.min_count = dim, window, min_count
        self.word_vectors = {}

    def fit_words(self, docs):
        counts = Counter(w for d in docs for w in d)
        vocab = {w: i for i, (w, c) in enumerate(counts.most_common())
                 if c >= self.min_count}
        V = len(vocab)
        M = np.zeros((V, V), dtype=np.float32)
        for doc in docs:
            ids = [vocab[w] for w in doc if w in vocab]
            for pos, i in enumerate(ids):
                lo = max(0, pos - self.window)
                for j in ids[lo:pos]:
                    M[i, j] += 1.0
                    M[j, i] += 1.0
        total = M.sum() or 1.0
        row = M.sum(axis=1, keepdims=True)
        row[row == 0] = 1.0
        with np.errstate(divide="ignore", invalid="ignore"):
            pmi = np.log((M * total) / (row * row.T))
        pmi[~np.isfinite(pmi)] = 0.0
        np.maximum(pmi, 0.0, out=pmi)          # PPMI
        U, S, _ = np.linalg.svd(pmi, full_matrices=False)
        k = min(self.dim, len(S))
        self.dim = k
        vecs = l2_normalize(U[:, :k] * np.sqrt(S[:k]))
        self.word_vectors = {w: vecs[i] for w, i in vocab.items()}
        self.fit_idf(docs)
        return self

    def encode(self, texts, kind="passage"):
        lookup = self.word_vectors.get
        return l2_normalize(np.vstack(
            [self._pool(_tokens(t), lookup) for t in texts]))


# --------------------------------------------------------------------------
# w2v / ft — gensim
# --------------------------------------------------------------------------

class GensimBackend(WordPooling, Backend):
    def __init__(self, kind="w2v", dim=128, epochs=30, window=5, min_count=2):
        self.name = kind
        self.kind = kind
        self.dim = dim
        self.epochs, self.window, self.min_count = epochs, window, min_count
        self.model = None

    def fit_words(self, docs):
        from gensim.models import FastText, Word2Vec
        cls = FastText if self.kind == "ft" else Word2Vec
        kwargs = dict(vector_size=self.dim, window=self.window,
                      min_count=self.min_count, workers=2, epochs=self.epochs,
                      sg=1)                      # skip-gram: 작은 코퍼스에 유리
        if self.kind == "ft":
            # bucket 을 줄이는 이유: 기본값 200만이면 ngram 표가
            # 200만 x 128 x 4B = 1GB 가 됩니다. 실제로 그렇게 나왔습니다.
            # 어휘가 1,331개인 코퍼스에 그만큼 필요하지 않습니다.
            kwargs.update(min_n=2, max_n=4, bucket=20000)   # 한국어 subword
        self.model = cls(sentences=docs, **kwargs)
        self.fit_idf(docs)
        return self

    # ---- 저장/복원 --------------------------------------------------
    #
    # 질의를 인코딩할 때 학습된 낱말 벡터가 필요합니다. 저장하지 않으면
    # 앱이 뜰 때마다 코퍼스를 다시 학습해야 하고(18초), 그러면 문서 벡터와
    # 다른 공간이 나올 수 있습니다.
    MODEL_PATH = ROOT / "data" / "word_vectors.model"
    IDF_PATH = ROOT / "data" / "word_vectors.idf.npz"

    def save(self):
        self.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(str(self.MODEL_PATH))
        terms = np.array(list(self.idf), dtype=object)
        weights = np.array([self.idf[w] for w in terms], dtype=np.float32)
        np.savez_compressed(self.IDF_PATH, terms=terms, weights=weights,
                            kind=np.array([self.kind], dtype=object))

    @classmethod
    def load(cls):
        from gensim.models import FastText, Word2Vec
        data = np.load(cls.IDF_PATH, allow_pickle=True)
        kind = str(data["kind"][0])
        obj = cls(kind=kind)
        loader = FastText if kind == "ft" else Word2Vec
        obj.model = loader.load(str(cls.MODEL_PATH))
        obj.dim = obj.model.wv.vector_size
        obj.idf = {t: float(w) for t, w in zip(data["terms"], data["weights"])}
        return obj

    def encode(self, texts, kind="passage"):
        wv = self.model.wv

        def lookup(tok):
            try:
                return wv[tok]                   # fastText 는 OOV 도 준다
            except KeyError:
                return None

        return l2_normalize(np.vstack(
            [self._pool(_tokens(t), lookup) for t in texts]).astype(np.float32))


# --------------------------------------------------------------------------
# 팩토리
# --------------------------------------------------------------------------

def build(spec, conn):
    """spec 예: 'lsa:256' 'tfidf' 'ppmi:128' 'w2v:128' 'ft:128'

    낱말 벡터를 학습하는 것들은 문서 본문 전체로 먼저 학습합니다.
    """
    no_idf = spec.endswith("-noidf")
    if no_idf:
        spec = spec[: -len("-noidf")]
    parts = spec.split(":")
    kind = parts[0]
    dim = int(parts[1]) if len(parts) > 1 else None

    if kind == "lsa":
        return embed.LsaBackend(dim=dim or embed.EMBED_DIM_LSA)
    if kind == "tfidf":
        return TfidfBackend()
    if kind == "ppmi":
        be = PpmiBackend(dim=dim or 128)
        be.use_idf = not no_idf
        return be.fit_words(training_corpus(conn))
    if kind in ("w2v", "ft"):
        be = GensimBackend(kind=kind, dim=dim or 128)
        be.use_idf = not no_idf
        return be.fit_words(training_corpus(conn))
    raise ValueError(f"모르는 백엔드: {spec}")
