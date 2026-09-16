"""web/ 프런트엔드를 실제 Store·Agent 에 붙이는 HTTP 서버.

    python3 server.py            # http://127.0.0.1:8000
    python3 server.py --port 9000

web/api.js 가 요구하는 엔드포인트를 그대로 구현합니다.

starlette + uvicorn 만 씁니다. 둘 다 이미 설치되어 있고, FastAPI 를 얹으면
pydantic 모델 정의가 늘어나는데 엔드포인트 14개짜리에서는 얻는 게 없습니다.


왜 DB 가 전제인가
-----------------
Streamlit 은 st.session_state 가 프로세스 메모리에 있어서 Store 를 메모리로
들고도 버텼습니다. HTTP 는 요청마다 상태를 어디선가 읽어야 하고, 그게 DB 입니다.
장바구니를 담는 요청과 그것을 조회하는 요청이 서로 다른 요청이기 때문입니다.


사용자와 세션
-------------
브라우저가 처음 오면 user_id 를 하나 만들어 쿠키(sid)로 심고, 이후 모든 요청은
그 쿠키로 사용자를 구분합니다. 로그인은 없습니다 — "이 브라우저 = 이 사용자" 입니다.
사용자마다 Store(장바구니·주문은 그 사용자 것만) 와 Agent 를 하나씩 두고,
`sessions` 에 모아 둡니다. 임베딩 행렬과 질의 모델은 Store 들이 공유합니다.

새 사용자에게는 데모 주문 14건을 그 사용자 것으로 심어 줍니다. 그래야 새 브라우저에서도
"어제 주문한 거 취소해줘" 를 바로 해 볼 수 있습니다.


확인 대기(승인 버튼)는 DB 에 있다
--------------------------------
/api/chat 이 미리보기를 만들고 /api/approve 가 실행하는데, 둘은 서로 다른 요청입니다.
그 사이의 상태(PendingAction·미룬 작업·대화 내역)를 Agent 객체 안에만 두면 서버를
재시작한 순간 화면의 승인 버튼이 아무것도 가리키지 않게 됩니다. 그래서 agent 를 쓰는
요청이 끝날 때마다 `agent.snapshot()` 을 agent_state 테이블에 저장하고, 세션을 새로
만들 때 되살립니다.

만료는 턴이 아니라 **시간**입니다 (config.PENDING_TTL_SECONDS, 기본 5분). HTTP 에는
턴이 없어서 — 사용자가 아무 말 없이 10분 뒤 버튼을 누를 수 있어서 — 버튼이 떠 있는
동안 장바구니·주문 상태가 바뀌었을 가능성을 시간으로 자릅니다. 만료된 승인은 실행하지
않고 다시 요청하라고 안내합니다. 실행 직전에 미리보기를 다시 계산해 비교하는 장치는
그대로 남아 있어, 만료 전이라도 상태가 달라졌으면 실행되지 않습니다.


남은 한계
---------
- 세션 쿠키는 인증이 아닙니다. 쿠키를 아는 사람은 그 장바구니를 봅니다.
- 사용자마다 Store 가 상품 dict 를 따로 들고 있어서, 다른 사용자가 결제한 재고는
  이 사용자의 화면에 바로 반영되지 않습니다. 실제 차감은 DB 의 조건부 UPDATE 가
  판정하므로 틀린 결제는 일어나지 않습니다.
- 프로세스가 여럿이면 `sessions` 가 갈라집니다. 상태는 DB 에 있으니 되살릴 수는
  있지만, 같은 사용자의 요청이 동시에 두 프로세스에 가면 순서를 보장하지 못합니다.
"""

from __future__ import annotations

import argparse
import math
import re
import threading
from collections import OrderedDict
from datetime import date
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import config
import db
from agent import ShoppingAgent
from store import Store
from tools import MAX_SEARCH_RESULTS, Toolbox

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

# 실행 상태 DB. 테스트가 임시 파일로 바꿔 끼울 수 있게 모듈 변수로 둡니다.
DB_PATH = db.SHOP_DB_PATH

SESSION_COOKIE = "sid"
SESSION_MAX_AGE = 30 * 24 * 3600          # 쿠키 30일
MAX_SESSIONS = 64                         # 메모리에 올려 둘 세션 수. 넘으면 오래된 것부터 내린다
_USER_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")


# --------------------------------------------------------------------------
# 세션 — 사용자 하나의 실행 문맥
# --------------------------------------------------------------------------

class Session:
    """사용자 한 명의 Store·Agent·대화 내역과, 그것들을 한 번에 한 요청만 쓰게 하는 락.

    대화 한 턴은 모델 호출을 여러 번 하느라 수십 초가 걸립니다. 그걸 async 핸들러
    안에서 동기로 부르면 이벤트 루프가 통째로 멈춰서, 모델이 생각하는 동안
    장바구니 버튼도 상품 목록도 응답하지 않습니다 (실측: /api/cart 타임아웃).
    그래서 agent 를 부르는 일은 스레드풀로 보내고, 같은 사용자의 agent 요청끼리만
    이 락으로 줄을 세웁니다. 장바구니·상품 조회는 락을 잡지 않습니다 — 밀리초짜리
    요청이고, 채팅을 기다리는 동안 화면을 계속 쓸 수 있어야 하기 때문입니다.
    채팅 중 화면에서 장바구니를 바꾸면 승인 시점에 미리보기를 다시 계산해 비교하는
    장치(agent.approve)가 그것을 잡습니다.
    """

    def __init__(self, user_id, db_path):
        self.user_id = user_id
        self.store = Store(db_path=db_path, user_id=user_id)
        self.toolbox = Toolbox(self.store)
        self.agent = ShoppingAgent(self.store)
        self.history: list[dict] = []
        self.lock = threading.Lock()

        # 서버를 재시작했거나 세션이 메모리에서 내려갔다가 다시 올라온 경우.
        state = db.load_agent_state(self.store.conn, user_id)
        if state:
            self.agent.restore(state)
            self.history = list(state.get("history") or [])

    def save(self):
        """확인 대기·미룬 작업·대화 내역을 DB 에 둔다. agent 를 쓴 요청 끝에 부른다."""
        db.save_agent_state(self.store.conn, self.user_id,
                            {**self.agent.snapshot(), "history": self.history})

    def close(self):
        if self.store.conn is not None:
            self.store.conn.close()


sessions: "OrderedDict[str, Session]" = OrderedDict()
_sessions_lock = threading.Lock()


def get_session(user_id):
    """사용자의 세션. 없으면 만든다 (새 사용자면 데모 주문도 심는다)."""
    with _sessions_lock:
        found = sessions.get(user_id)
        if found is not None:
            sessions.move_to_end(user_id)
            return found

        db.ensure_shop_db(DB_PATH)
        conn = db.connect(DB_PATH)
        try:
            if db.ensure_user(conn, user_id):
                # 처음 온 사용자. 데모 시나리오("어제 주문한 거 취소")가 되도록
                # 주문 14건을 이 사용자 것으로 심는다. 주문번호는 전체에서 새로 받는다.
                db.seed_demo_orders(conn, user_id=user_id)
        finally:
            conn.close()

        session = Session(user_id, DB_PATH)
        sessions[user_id] = session
        while len(sessions) > MAX_SESSIONS:
            _, oldest = sessions.popitem(last=False)
            oldest.save()
            oldest.close()
        return session


def session_of(request):
    return get_session(request.state.user_id)


def close_all_sessions():
    with _sessions_lock:
        for session in sessions.values():
            session.close()
        sessions.clear()


def _with_session(session, func, *args):
    """agent 를 쓰는 동기 함수를 그 사용자의 락 안에서 실행하고 상태를 저장한다."""
    with session.lock:
        result = func(*args)
        session.save()
        return result


class SessionMiddleware(BaseHTTPMiddleware):
    """쿠키에서 user_id 를 읽고, 없으면 만들어 응답에 심는다."""

    async def dispatch(self, request, call_next):
        user_id = request.cookies.get(SESSION_COOKIE)
        fresh = not (user_id and _USER_ID_RE.fullmatch(user_id))
        if fresh:
            user_id = db.new_user_id()
        request.state.user_id = user_id
        response = await call_next(request)
        if fresh:
            response.set_cookie(SESSION_COOKIE, user_id, max_age=SESSION_MAX_AGE,
                                httponly=True, samesite="lax")
        return response


def __getattr__(name):
    """예전 코드 호환 — server.store / server.agent / server.toolbox / server.history.

    단일 사용자 시절의 모듈 변수들입니다. 검증 스크립트 몇 개가 아직 이 이름을
    쓰므로 기본 사용자(demo)의 세션으로 이어 줍니다. 새 코드는 session_of(request) 를 쓸 것.
    """
    if name in ("store", "agent", "toolbox", "history"):
        return getattr(get_session(db.DEMO_USER_ID), name)
    raise AttributeError(name)


# --------------------------------------------------------------------------
# 직렬화 — 화면이 기대하는 모양으로 맞춘다
#
# web/mock.js 가 정의한 모양이 계약입니다. 필드 이름이 하나라도 다르면
# 화면이 조용히 빈칸을 그립니다 (개발노트 6 과 같은 종류의 사고).
# --------------------------------------------------------------------------

def product_json(store, product):
    """상품 하나. mock.js 의 buildProducts() 가 만드는 모양과 같게."""
    return {
        "id": product["id"],
        "name": product["name"],
        "brand": product["brand"],
        "group": store.group_of(product["category"]),
        "category": product["category"],
        "gender": product["gender"],
        "color": product["color"],
        "material": product["material"],
        "material_detail": product.get("material_detail"),
        "price": product["price"],
        "rating": product["rating"],
        "review_count": product["review_count"],
        # 키를 문자열로 보냅니다. JSON 객체의 키는 문자열뿐이고,
        # 화면도 String(size) 로 비교하고 있습니다.
        "sizes": {str(size): qty for size, qty in product["sizes"].items()},
        "machine_washable": product["machine_washable"],
        "care": product.get("care"),
        "delivery_days": product.get("delivery_days"),
        "description": product["description"],
    }


def cart_json(store):
    """장바구니. {lines, quantity, total}"""
    view = store.view_cart()
    lines = []
    for item in view["items"]:
        product = store.get_product(item["product_id"])
        lines.append({
            "product_id": item["product_id"],
            "size": item["size"],
            "quantity": item["quantity"],
            "name": item["name"],
            "brand": product["brand"] if product else "",
            "price": item["price"],
            "category": product["category"] if product else "",
            "color": product["color"] if product else "",
            "group": store.group_of(product["category"]) if product else None,
        })
    return {"lines": lines, "quantity": view["quantity"], "total": view["total"]}


def order_json(store, order):
    """주문 하나.

    모양을 하나 바꿔서 보냅니다. Store 의 주문은 상품 한 종류인데 화면은
    items 배열을 기대합니다. 한 건을 원소 하나인 배열로 감쌉니다.
    화면이 여러 상품 주문을 그릴 수 있게 만들어져 있으므로, 나중에 Store 가
    그렇게 바뀌어도 화면은 안 바뀝니다.
    """
    product = store.get_product(order["product_id"])
    ordered = date.fromisoformat(order["ordered_at"])
    return {
        "id": order["order_id"],
        "status": order["status"],
        "ordered_days_ago": (store.today - ordered).days,
        "ordered_at": order["ordered_at"],
        "shipped_at": order.get("shipped_at"),
        "delivered_at": order.get("delivered_at"),
        "return_reason": order.get("return_reason"),
        "items": [{
            "product_id": order["product_id"],
            "name": order["product_name"],
            "brand": product["brand"] if product else "",
            "size": order["size"],
            "quantity": order["quantity"],
            "price": product["price"] if product else order["price"],
            "category": product["category"] if product else "",
            "color": product["color"] if product else "",
            "group": store.group_of(product["category"]) if product else None,
        }],
        "total": order["price"],
        # 화면의 취소·반품 버튼이 쓰는 판정. 에이전트가 Tool 로 묻는 것과 **같은 함수**
        # (store.can_cancel / can_return)라서, 화면과 에이전트가 같은 규칙을 본다.
        "actions": {
            "cancel": _decision_json(store.can_cancel(order["order_id"])),
            "return": _decision_json(store.can_return(order["order_id"])),
        },
    }


def _decision_json(decision):
    return {"allowed": bool(decision.allowed), "reason": decision.reason,
            "alternative": decision.alternative}


def trace_json(trace):
    """Tool 실행 기록을 화면이 읽는 모양으로.

    이 변환이 발표에서 가장 중요한 화면 요소를 만듭니다 — 어떤 Tool 을
    어떤 인자로 불렀는지 펼쳐 보는 부분입니다.
    """
    rows = []
    for entry in trace or []:
        result = entry.get("result") or {}
        rows.append({
            "name": entry.get("tool"),
            "args": entry.get("arguments") or {},
            "ok": bool(result.get("success", True)),
            "msg": result.get("message", ""),
        })
    return rows


def pending_json(agent):
    """확인 대기를 화면 버튼이 읽는 모양으로. 없으면 None.

    arguments 는 보내지 않습니다. 화면이 필요한 것은 무엇을 승인하는지(label)와
    승인할 때 돌려보낼 열쇠(key)뿐이고, 인자는 앱이 들고 있어야 합니다.
    모델도 화면도 인자를 만들어 보낼 수 없어야 승인이 열쇠로만 성립합니다.

    expires_in 은 남은 초입니다. 화면이 그 시간이 지나면 버튼을 거두고
    "확인 시간이 지났습니다" 를 띄웁니다. 서버도 approve 에서 같은 시각으로 거절합니다.
    """
    pending = getattr(agent, "pending", None)
    if pending is None:
        return None
    if pending.expired():
        agent.pending = None
        agent.postponed = None
        return None
    return {
        "kind": pending.tool,
        "summary": getattr(pending, "summary", ""),
        "expires_in": max(0, int(pending.seconds_left())),
        "items": [
            {"key": item["key"], "label": item["label"]}
            for item in pending.items
        ],
    }


def chat_json(session, answer, trace):
    """대화 응답. 검색 상품은 채팅 카드가 아니라 메인 그리드로 보낸다."""
    return {
        "reply": answer,
        "trace": trace_json(trace),
        # 상품 ID 만 보냅니다. 화면이 api.product(id) 로 하나씩 받아가도록
        # 만들어져 있어서(app.js), 객체를 보내면 그 호출이 깨집니다.
        "products": products_from_trace(session.store, trace),
        "search_results": search_results_from_trace(session.store, trace),
        "search_performed": search_performed(trace),
        "search_note": search_note(trace),
        "pending": pending_json(session.agent),
        # 계획 모드에서 모델이 세운 단계와 진행 상황. 없으면 None. 화면이 체크리스트로 그린다.
        "plan": plan_json(session.agent),
    }


def plan_json(agent):
    plan = getattr(agent, "plan", None)
    return plan.checklist() if plan is not None else None


def search_note(trace):
    """검색 결과에 붙일 한 줄 안내. 기준 통과가 적어 비슷한 상품을 채운 경우에만."""
    for entry in reversed(trace or []):
        if entry.get("tool") != "search_product":
            continue
        result = entry.get("result") or {}
        data = result.get("data") or {}
        if result.get("success") and isinstance(data, dict) and data.get("backfilled"):
            qualified = data.get("qualified", 0)
            if qualified:
                return (f"딱 맞는 상품은 {qualified}개입니다. 비슷한 상품 "
                        f"{data['backfilled']}개를 함께 보여드려요.")
            return f"딱 맞는 상품은 없어서 비슷한 상품 {data['backfilled']}개를 보여드려요."
        return None
    return None


def search_performed(trace):
    """성공한 검색이 0건이어도 화면이 빈 검색 결과를 표시할 수 있게 한다."""
    return any(
        entry.get("tool") == "search_product"
        and (entry.get("result") or {}).get("success")
        for entry in (trace or [])
    )


def search_results_from_trace(store, trace):
    """마지막으로 성공한 search_product 결과를 화면용 상품 객체로 반환한다."""
    for entry in reversed(trace or []):
        if entry.get("tool") != "search_product":
            continue
        result = entry.get("result") or {}
        if not result.get("success"):
            continue
        data = result.get("data") or {}
        rows = data.get("products") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        found = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            product = store.get_product(row.get("product_id") or row.get("id"))
            if product is not None:
                found.append(product_json(store, product))
        return found[:MAX_SEARCH_RESULTS]
    return []


def products_from_trace(store, trace):
    """트레이스에 등장한 상품을 미니 카드용으로 뽑는다.

    검색·조회·비교 Tool 의 결과에서 상품 ID 를 모읍니다. 모델이 답변 문장에
    무엇을 쓰든 화면에 뜨는 카드는 **실제로 Tool 이 돌려준 상품**입니다.
    슬라이드 19 의 "보고는 모델이 쓰지 않는다" 와 같은 이유입니다.
    """
    ids, seen = [], set()
    for entry in trace or []:
        data = (entry.get("result") or {}).get("data")
        candidates = []
        if isinstance(data, dict):
            if isinstance(data.get("products"), list):
                candidates = data["products"]
            elif data.get("product_id"):
                candidates = [data]
        elif isinstance(data, list):
            candidates = data
        for row in candidates:
            if not isinstance(row, dict):
                continue
            pid = row.get("product_id") or row.get("id")
            if pid and pid not in seen:
                seen.add(pid)
                ids.append(pid)
    return [i for i in ids if store.get_product(i) is not None]


# --------------------------------------------------------------------------
# 상품
# --------------------------------------------------------------------------

_SORTS = {
    "price_asc": (lambda p: p["price"], False),
    "price_desc": (lambda p: p["price"], True),
    "rating": (lambda p: p["rating"], True),
    "review": (lambda p: p["review_count"], True),
    # mock.js 와 같은 식. 평점만으로 줄 세우면 리뷰 3개짜리 4.9 가 위로 옵니다.
    "recommend": (
        lambda p: p["rating"] * math.log(max(p["review_count"], 2)), True
    ),
}


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def api_products(request):
    store = session_of(request).store
    q = request.query_params
    semantic_query = (q.get("semanticQuery") or q.get("semantic_query") or "").strip() or None
    product_name = (q.get("productName") or q.get("product_name") or "").strip() or None
    # 화면이 minPrice 로도 min_price 로도 보낼 수 있어 둘 다 받습니다.
    filters = {
        "keyword": None if (semantic_query or product_name) else (q.get("query") or None),
        "product_name": product_name,
        "group": q.get("group") or None,
        "category": q.get("category") or None,
        "gender": q.get("gender") or None,
        "color": q.get("color") or None,
        "brand": q.get("brand") or None,
        "material": q.get("material") or None,
        "min_price": _int_or_none(q.get("minPrice") or q.get("min_price")),
        "max_price": _int_or_none(q.get("maxPrice") or q.get("max_price")),
        "size": _int_or_none(q.get("size")),
        "machine_washable": (
            True if (q.get("machineWashable") or q.get("machine_washable"))
            in ("1", "true") else None
        ),
    }
    filters = {k: v for k, v in filters.items() if v is not None}

    minimum = filters.get("min_price")
    maximum = filters.get("max_price")
    if minimum is not None and minimum < 0 or maximum is not None and maximum < 0:
        return JSONResponse({"error": "가격은 0원 이상이어야 합니다"}, status_code=400)
    if minimum is not None and maximum is not None and minimum > maximum:
        return JSONResponse(
            {"error": "최소 가격은 최대 가격보다 클 수 없습니다"}, status_code=400)

    # 정렬은 여기서 합니다. store 의 sort 는 4종이고 화면은 recommend 도
    # 쓰기 때문입니다. limit 을 크게 줘서 전체를 받고 정렬합니다 —
    # count_products 가 쓰는 방식과 같습니다.
    sort = q.get("sort") or "recommend"
    semantic_pool = False
    # Tool과 동일하게 SQL 필터 -> 의미 상위 50개 -> 명시 정렬 순서로 처리한다.
    # 가격 정렬이 함께 와도 사용자의 의미 조건을 버리지 않는다.
    if semantic_query:
        filters.pop("keyword", None)
        semantic = store.search_semantic_result(
            semantic_query, top_k=MAX_SEARCH_RESULTS,
            min_score=config.SEMANTIC_MIN_SCORE,
            min_results=config.SEMANTIC_MIN_RESULTS,
            rerank_results=sort == "recommend", **filters)
        rows = semantic["products"]
        if semantic["available"]:
            semantic_pool = True
            if sort in ("price_asc", "price_desc", "rating", "review"):
                rows = store.sort_products(rows, sort)
        else:
            # 임베딩을 쓸 수 없는 환경(st 미설치 등)이면 Tool 과 같이 SQL 결과로
            # 되돌아갑니다. 예전에는 여기서 빈 배열이 나가 "조건에 맞는 상품이
            # 없습니다" 가 떴는데, 실제로는 조건에 맞는 상품이 있는 상태였습니다.
            rows = store.search_products(limit=10 ** 9, **filters)
    else:
        rows = store.search_products(limit=10 ** 9, **filters)

    # 의미 검색 결과의 순서가 곧 추천 순서입니다. 여기에 recommend(평점×리뷰)
    # 를 다시 걸면 첫 화면(Tool 결과 순서)과 칩 하나 토글한 뒤의 순서가
    # 달라집니다. 사용자가 가격·평점 정렬을 직접 고른 경우에만 다시 정렬합니다.
    if semantic_pool:
        key = None
    else:
        key = _SORTS.get(sort)
    if key is not None:
        # 동률에서 순서가 흔들리지 않게 product_id 를 타이브레이커로 둡니다.
        # 없으면 같은 요청에 다른 순서가 나올 수 있습니다.
        rows = sorted(rows, key=lambda p: (key[0](p), p["id"]), reverse=key[1])
        if key[1]:
            rows = sorted(rows, key=lambda p: p["id"])
            rows = sorted(rows, key=key[0], reverse=True)

    limit = _int_or_none(q.get("limit"))
    if limit:
        rows = rows[:limit]
    return JSONResponse([product_json(store, p) for p in rows])


async def api_product(request):
    store = session_of(request).store
    product = store.get_product(request.path_params["product_id"])
    if product is None:
        return JSONResponse({"error": "상품을 찾을 수 없습니다"}, status_code=404)
    return JSONResponse(product_json(store, product))


# --------------------------------------------------------------------------
# 장바구니
#
# 화면의 버튼은 사용자가 직접 누른 것이므로 이미 확인입니다 (개발노트 9).
# 그래서 승인 게이트를 지나지 않고 store 를 바로 부릅니다.
# 대화로 지우는 경로만 /api/chat -> /api/approve 를 거칩니다.
# --------------------------------------------------------------------------

async def api_cart(request):
    return JSONResponse(cart_json(session_of(request).store))


class _BadRequest(Exception):
    pass


def _cart_args(body, *, quantity_default=None):
    """장바구니 요청 본문에서 (product_id, size, quantity) 를 읽는다.

    키가 빠지거나 사이즈가 숫자가 아니면 KeyError/ValueError 로 500 이 나던 것을
    400 으로 바꿉니다. 화면이 잘못 보낸 것이지 서버가 죽을 일이 아닙니다.
    """
    try:
        pid = body["product_id"]
        size = int(body["size"])
        quantity = body.get("quantity", quantity_default)
        quantity = int(quantity) if quantity is not None else None
    except (KeyError, TypeError, ValueError):
        raise _BadRequest("product_id, size(정수), quantity(정수) 가 필요합니다")
    return pid, size, quantity


def _bad_request(message):
    return JSONResponse({"ok": False, "message": message}, status_code=400)


async def api_cart_add(request):
    store = session_of(request).store
    body = await request.json()
    try:
        pid, size, quantity = _cart_args(body, quantity_default=1)
    except _BadRequest as problem:
        return _bad_request(str(problem))
    ok, message = store.add_to_cart(pid, size, quantity)
    return JSONResponse({"ok": ok, "message": message, "cart": cart_json(store)})


async def api_cart_patch(request):
    store = session_of(request).store
    body = await request.json()
    try:
        pid, size, want = _cart_args(body)
    except _BadRequest as problem:
        return _bad_request(str(problem))
    if want is None:
        return _bad_request("quantity 가 필요합니다")

    current = next(
        (i["quantity"] for i in store.view_cart()["items"]
         if i["product_id"] == pid and i["size"] == size),
        0,
    )
    if want <= 0:
        ok, message = store.remove_from_cart(pid, size)
    elif want < current:
        ok, message = store.remove_from_cart(pid, size, current - want)
    elif want > current:
        ok, message = store.add_to_cart(pid, size, want - current)
    else:
        ok, message = True, "변경 없음"
    return JSONResponse({"ok": ok, "message": message, "cart": cart_json(store)})


async def api_cart_delete(request):
    store = session_of(request).store
    body = await request.json()
    try:
        pid, size, _ = _cart_args(body)
    except _BadRequest as problem:
        return _bad_request(str(problem))
    ok, message = store.remove_from_cart(pid, size)
    return JSONResponse({"ok": ok, "message": message, "cart": cart_json(store)})


async def api_checkout(request):
    store = session_of(request).store
    body = await request.json() if await request.body() else {}
    items = body.get("items") or None
    selection = None
    if items:
        selection = [
            {"product_id": i["product_id"], "size": int(i["size"])} for i in items
        ]
    ok, message, created = store.checkout(selection)
    return JSONResponse({
        "ok": ok,
        "message": message,
        "total": sum(o["price"] for o in created),
        "orders": [order_json(store, o) for o in created],
        "cart": cart_json(store),
    })


async def api_orders(request):
    store = session_of(request).store
    # 최신 주문이 위로 옵니다. 화면이 그 순서를 기대하고 (mock 은 unshift),
    # 방금 주문한 것이 목록 맨 아래에 있으면 사용자가 못 찾습니다.
    # 같은 날 주문이 여럿이면 주문번호 역순 — 번호가 발급 순서입니다.
    rows = sorted(
        store.orders,
        key=lambda o: (o["ordered_at"], o["order_id"]),
        reverse=True,
    )
    return JSONResponse([order_json(store, o) for o in rows])


# --------------------------------------------------------------------------
# 대화와 승인
# --------------------------------------------------------------------------

async def api_chat(request):
    body = await request.json()
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "message 가 비어 있습니다"}, status_code=400)

    session = session_of(request)

    def turn():
        answer, trace = session.agent.run(message, session.history)
        session.history.append({"role": "user", "content": message})
        session.history.append({"role": "assistant", "content": answer})
        del session.history[:-20]  # 최근 10턴만
        return chat_json(session, answer, trace)

    return JSONResponse(await run_in_threadpool(_with_session, session, turn))


async def api_approve(request):
    body = await request.json() if await request.body() else {}
    keys = body.get("keys")
    session = session_of(request)

    def turn():
        # approve 는 모델을 부르지 않지만 Tool 을 실행하고 미리보기를 다시
        # 계산합니다. run 과 같은 락 안에서 돌아야 순서가 보장됩니다.
        answer, trace = session.agent.approve(keys)
        session.history.append({"role": "assistant", "content": answer})
        # 계획 모드: 승인으로 끊긴 계획에 남은 단계가 있으면 이어서 진행한다.
        more = session.agent.continue_plan(session.history)
        if more is not None:
            extra, extra_trace = more
            answer = f"{answer}\n\n{extra}".strip()
            trace = list(trace) + list(extra_trace)
            session.history.append({"role": "assistant", "content": extra})
            del session.history[:-20]
        return chat_json(session, answer, trace)

    return JSONResponse(await run_in_threadpool(_with_session, session, turn))


async def api_order_action(request):
    """주문 화면의 [주문 취소]·[반품 신청] 버튼.

    바로 실행하지 않는다. 채팅으로 "취소해줘" 라고 했을 때와 **같은 확인 대기**를
    연다 — 미리보기를 계산해 PendingAction 을 만들고, 사용자는 상담창의 승인
    버튼으로 실행한다. 되돌릴 수 없는 작업의 경로가 하나뿐이어야 승인 규칙(열쇠·
    만료·스냅샷 비교)이 화면 버튼에도 똑같이 적용된다. 모델은 부르지 않는다.
    """
    order_id = request.path_params["order_id"]
    body = await request.json() if await request.body() else {}
    kind = (body.get("kind") or "").strip()
    tool = {"cancel": "cancel_order", "return": "return_order"}.get(kind)
    if tool is None:
        return JSONResponse({"error": "kind 는 cancel 또는 return 이어야 합니다"}, status_code=400)
    arguments = {"order_id": order_id}
    if tool == "return_order":
        arguments["reason"] = (body.get("reason") or "").strip() or "고객 요청"
    session = session_of(request)

    def turn():
        agent = session.agent
        if agent.pending is not None:
            # 이미 다른 확인이 떠 있으면 섞지 않는다. 먼저 그것을 끝내게 한다.
            return chat_json(session, "먼저 상담창에 떠 있는 확인을 승인하거나 거절해 주세요.", [])
        agent.turn += 1
        trace = []
        summary, problems = agent._open_pending([{"tool": tool, "arguments": arguments}], trace)
        if summary:
            answer = summary
        else:
            answer = "지금은 처리할 수 없습니다.\n  " + "\n  ".join(problems or [tool])
        session.history.append({"role": "assistant", "content": answer})
        return chat_json(session, answer, trace)

    return JSONResponse(await run_in_threadpool(_with_session, session, turn))


async def api_reject(request):
    session = session_of(request)

    def turn():
        # pending 만 지우면 미뤄 둔 후속 작업(postponed)이 남아서, 나중에 다른
        # 승인을 할 때 "이어서 부탁하신 작업입니다" 로 되살아납니다.
        # agent.reject() 가 둘 다 정리하고, 이미 실행된 것은 그대로임을 말합니다.
        answer, trace = session.agent.reject()
        session.history.append({"role": "assistant", "content": answer})
        return chat_json(session, answer, trace)

    return JSONResponse(await run_in_threadpool(_with_session, session, turn))


async def api_pending(request):
    session = session_of(request)

    def read():
        had_pending = session.agent.pending is not None
        result = pending_json(session.agent)
        # 조회하는 순간 만료를 발견했다면 DB 에도 즉시 반영한다. 그렇지 않으면
        # 재시작 때마다 오래된 JSON 을 다시 읽고 버리는 불필요한 상태가 남는다.
        if had_pending and session.agent.pending is None:
            session.save()
        return result

    def locked_read():
        with session.lock:
            return read()

    # 같은 사용자의 모델 호출이 길어져도 HTTP 이벤트 루프 전체를 막지 않는다.
    return JSONResponse(await run_in_threadpool(locked_read))


async def api_meta(request):
    store = session_of(request).store
    return JSONResponse({
        "products": len(store.products),
        "orders": len(store.orders),
        "groups": store.available_groups(),
        "categories": store.available_categories(),
        "colors": store.available_colors(),
        "brands": store.available_brands(),
        "user_id": store.user_id,
    })


async def api_reset(request):
    """현재 브라우저 사용자만 시연 시작 상태로 되돌린다."""
    session = session_of(request)

    def reset():
        # 같은 사용자의 진행 중인 대화가 끝난 뒤 초기화한다. Session 객체와 락은
        # 유지하고 내부 Store/Agent 만 새로 만들어 다른 요청이 낡은 객체를 잡지 않게 한다.
        with session.lock:
            db.reset_user(session.store.conn, session.user_id)
            session.close()
            session.store = Store(db_path=DB_PATH, user_id=session.user_id)
            session.toolbox = Toolbox(session.store)
            session.agent = ShoppingAgent(session.store)
            session.history = []

    await run_in_threadpool(reset)
    return JSONResponse({"ok": True, "message": "내 데이터를 처음 상태로 되돌렸습니다"})


routes = [
    Route("/api/meta", api_meta),
    Route("/api/products", api_products),
    Route("/api/products/{product_id}", api_product),
    Route("/api/cart", api_cart, methods=["GET"]),
    Route("/api/cart", api_cart_add, methods=["POST"]),
    Route("/api/cart", api_cart_patch, methods=["PATCH"]),
    Route("/api/cart", api_cart_delete, methods=["DELETE"]),
    Route("/api/checkout", api_checkout, methods=["POST"]),
    Route("/api/orders", api_orders),
    Route("/api/orders/{order_id}/action", api_order_action, methods=["POST"]),
    Route("/api/chat", api_chat, methods=["POST"]),
    Route("/api/approve", api_approve, methods=["POST"]),
    Route("/api/reject", api_reject, methods=["POST"]),
    Route("/api/pending", api_pending),
    Route("/api/reset", api_reset, methods=["POST"]),
]

if WEB_DIR.exists():
    # 화면과 API 를 같은 서버에서 냅니다. 포트를 나누면 CORS 를 설정해야 하고,
    # 그건 이 프로젝트에서 배울 것이 없는 종류의 작업입니다.
    routes.append(Mount("/", app=StaticFiles(directory=WEB_DIR, html=True)))

app = Starlette(routes=routes, middleware=[Middleware(SessionMiddleware)])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    if config.RERANK_ENABLED:
        import rerank
        print(f"리랭커 준비 중: {config.RERANK_MODEL_NAME}")
        rerank.warmup()

    boot = get_session(db.DEMO_USER_ID)
    print(f"상품 {len(boot.store.products):,}개 · 기본 사용자 주문 {len(boot.store.orders)}건"
          f" · 확인 대기 만료 {config.PENDING_TTL_SECONDS}초")
    print(f"http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
