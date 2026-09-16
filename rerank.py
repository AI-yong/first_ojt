"""의미 검색 후보를 cross-encoder로 재정렬한다.

상품 전체를 찾는 검색기가 아니다. SQL과 Dense가 고른 최대 50개만
`(semantic_query, 상품명 + description)` 쌍으로 다시 채점한다.
"""

from __future__ import annotations

import sys
import threading

import config


_model = None
_load_failed = False
_load_lock = threading.Lock()
_predict_lock = threading.Lock()


def _get_model():
    """프로세스 전체에서 모델 하나만 만들고 모든 사용자 Store가 공유한다."""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed or not config.RERANK_ENABLED:
        return None
    with _load_lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(
                config.RERANK_MODEL_NAME,
                revision=config.RERANK_MODEL_REVISION or None,
                device=config.RERANK_DEVICE or None,
            )
        except Exception as exc:
            _load_failed = True
            print(f"[검색] 리랭커를 불러오지 못해 Dense 순서를 유지합니다 — "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return None
    return _model


def warmup():
    """서버 시작 때 선택적으로 모델을 미리 올린다."""
    return _get_model() is not None


def rerank_products(query, products):
    """(재정렬 상품, 적용 여부)를 반환한다. 실패하면 원래 순서를 보존한다."""
    rows = list(products)
    if not config.RERANK_ENABLED or not query or len(rows) < 2:
        return rows, False
    model = _get_model()
    if model is None:
        return rows, False

    pairs = [
        (query, ". ".join(filter(None, (
            (product.get("name") or "").strip(),
            (product.get("description") or "").strip(),
        ))))
        for product in rows
    ]
    try:
        # 같은 모델 객체에 여러 사용자 요청이 동시에 들어가는 것을 막는다.
        with _predict_lock:
            scores = model.predict(
                pairs,
                batch_size=config.RERANK_BATCH_SIZE,
                show_progress_bar=False,
            )
        scored = [
            (float(score), index, product)
            for index, (score, product) in enumerate(zip(scores, rows))
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [product for _, _, product in scored], True
    except Exception as exc:
        print(f"[검색] 리랭킹에 실패해 Dense 순서를 유지합니다 — "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return rows, False
