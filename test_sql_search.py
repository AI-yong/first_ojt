"""search_product의 SQL 필터 + 의미 정렬 경로 회귀 테스트."""

import db
import numpy as np
import rerank
from agent import _search_grid_reply
from store import Store
from tools import TOOLS, Toolbox


def search_schema():
    tool = next(item for item in TOOLS
                if item["function"]["name"] == "search_product")
    return tool["function"]["parameters"]["properties"]


def test_tool_exposes_structured_filters_and_semantic_query():
    properties = search_schema()
    assert "keyword" not in properties
    assert "concepts" not in properties
    assert "semantic_query" in properties
    assert "product_name" in properties
    assert "category" in properties
    assert "max_price" in properties
    assert "size" in properties
    assert "limit" not in properties
    assert "min_score" not in properties


def test_category_and_group_enums_match_catalog_mapping():
    properties = search_schema()
    metadata = db.read_catalog_metadata()
    mapping = metadata["category_groups"]
    expected_groups = list(mapping)
    expected_categories = [
        category
        for categories in mapping.values()
        for category in categories
    ]
    assert properties["group"]["enum"] == expected_groups
    assert properties["category"]["enum"] == expected_categories
    assert set(Store().available_categories()) == set(expected_categories)


def test_sql_filters_and_count_use_the_same_conditions():
    store = Store()
    filters = dict(category="운동화", gender="여성", color="검은색",
                   max_price=150000, size=240)
    found = store.search_products(limit=20, **filters)
    assert found
    assert store.count_products(**filters) >= len(found)
    assert all(p["category"] == "운동화" for p in found)
    assert all(p["gender"] in ("여성", "공용") for p in found)
    assert all(p["color"] == "검은색" for p in found)
    assert all(p["price"] <= 150000 for p in found)
    assert all(p["sizes"].get(240, 0) > 0 for p in found)


def test_sql_sort_is_applied_before_limit():
    store = Store()
    found = store.search_products(category="운동화", sort="price_asc", limit=5)
    prices = [p["price"] for p in found]
    assert prices == sorted(prices)


def test_product_name_uses_sql_substring_filter():
    store = Store()
    found = store.search_products(product_name="클라우드 워크 2", limit=50)
    assert [p["name"] for p in found] == ["클라우드 워크 2"]


def test_toolbox_uses_sql_filter_search():
    result = Toolbox(Store()).search_product(
        category="운동화", color="검은색", max_price=150000)
    assert result["success"] is True
    assert result["data"]["shown"] > 0
    assert result["data"]["ranking"] == "sql"


def test_tool_call_filters_with_sql_then_ranks_with_query_embedding():
    store = Store()
    candidates = store.search_products(category="운동화", color="검은색",
                                       limit=10 ** 9)
    assert len(candidates) > 1
    target = candidates[-1]

    class FakeQueryBackend:
        def encode(self, texts, kind="query"):
            assert texts == ["비 오는 날 신기 좋은"]
            assert kind == "query"
            return np.asarray([[1.0, 0.0]], dtype=np.float32)

    # 실제 모델 대신 방향이 명확한 작은 벡터를 써서 파이프라인 자체를 검사한다.
    store._vec_ready = True
    store._vec_ids = [p["id"] for p in candidates]
    store._vec_index = {pid: i for i, pid in enumerate(store._vec_ids)}
    store._vec_matrix = np.asarray(
        [[1.0, 0.0] if p["id"] == target["id"] else [0.0, 1.0]
         for p in candidates],
        dtype=np.float32,
    )
    store._query_backend = FakeQueryBackend()

    result = Toolbox(store).call("search_product", {
        "semantic_query": "비 오는 날 신기 좋은",
        "category": "운동화",
        "color": "검은색",
    })
    assert result["success"] is True
    assert result["data"]["ranking"] == "semantic"
    assert result["data"]["products"][0]["product_id"] == target["id"]
    assert all(p["category"] == "운동화" for p in result["data"]["products"])
    assert all(p["color"] == "검은색" for p in result["data"]["products"])


def test_semantic_then_explicit_sort_keeps_semantic_pool():
    store = Store()
    candidates = store.search_products(category="운동화", limit=10 ** 9)
    selected = candidates[:3]

    store.search_semantic_result = lambda keyword, top_k=20, min_score=None, **filters: {
        "available": True, "products": list(selected),
        "qualified_count": len(selected), "min_score": min_score,
    }
    result = Toolbox(store).call("search_product", {
        "category": "운동화", "semantic_query": "오래 걸어도 편한",
        "sort": "price_asc",
    })
    assert result["success"] is True
    assert result["data"]["ranking"] == "semantic_then_price_asc"
    prices = [p["price"] for p in result["data"]["products"]]
    assert prices == sorted(prices)


def test_cross_encoder_reranks_only_the_given_candidates(monkeypatch):
    products = [
        {"id": "winter", "name": "겨울 니트", "description": "추운 겨울용"},
        {"id": "spring", "name": "간절기 니트", "description": "봄가을용"},
    ]

    class FakeCrossEncoder:
        def predict(self, pairs, **kwargs):
            assert len(pairs) == 2
            assert pairs[0][0] == "봄이나 가을에 입기 좋은"
            return np.asarray([0.1, 0.9])

    monkeypatch.setattr(rerank, "_get_model", lambda: FakeCrossEncoder())
    ordered, applied = rerank.rerank_products("봄이나 가을에 입기 좋은", products)
    assert applied is True
    assert [product["id"] for product in ordered] == ["spring", "winter"]


def test_search_reply_uses_shown_not_all_qualified_candidates():
    trace = [{
        "tool": "search_product", "arguments": {},
        "result": {"success": True, "data": {
            "total": 151, "qualified": 114, "shown": 50, "products": [],
        }},
    }]
    reply = _search_grid_reply(trace)
    assert "50개" in reply
    assert "114개" not in reply


def test_semantic_threshold_does_not_fall_back_to_unrelated_sql_rows():
    store = Store()
    store.search_semantic_result = lambda keyword, top_k=20, min_score=None, **filters: {
        "available": True, "products": [], "qualified_count": 0,
        "min_score": min_score,
    }
    result = Toolbox(store).call("search_product", {
        "category": "운동화", "semantic_query": "우주여행에 적합한",
    })
    assert result["success"] is True
    assert result["data"]["shown"] == 0
    assert result["data"]["qualified"] == 0
    assert result["data"]["ranking"] == "semantic"


def test_search_validation_rejects_invalid_range_and_normalizes_group():
    bad = Toolbox(Store()).call("search_product", {
        "min_price": 150000, "max_price": 100000,
    })
    assert bad["success"] is False
    assert "클 수 없습니다" in bad["message"]

    cleaned, error = __import__("tools").validate_call(
        "search_product", {"group": "신발", "category": "운동화"})
    assert error is None
    assert cleaned == {"category": "운동화"}


def test_sql_uses_bound_parameters():
    conn = db.memory_db()
    try:
        # 값이 SQL 문법으로 해석되면 전체 행이 나오는 고전적인 인젝션 문자열.
        ids = db.search_product_ids(conn, category="운동화' OR 1=1 --", limit=20)
        assert ids == []
    finally:
        conn.close()
