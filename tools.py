"""LLM 이 호출할 Tool 계층.

여기 있는 함수들은 **얇아야 합니다**. 판단은 하지 않고 store 에 넘기기만 합니다.

    나쁜 예:  def cancel_order(order_id):
                  order = store.get_order(order_id)
                  if order["shipped_at"] is None:      # <- 정책이 여기 있음
                      ...

    좋은 예:  def cancel_order(order_id):
                  return store.cancel_order(order_id)  # <- 정책은 store 가 안다

이 규칙을 지키면 Tool 이 13개가 되어도 규칙은 store.py 한 곳만 고치면 됩니다.
어기면 can_cancel 과 cancel_order 의 판단이 갈라지는 버그가 생깁니다.

Tool 하나를 추가할 때 할 일은 세 가지입니다.
    1. TOOLS 리스트에 스키마 추가  (모델이 읽는 설명)
    2. Toolbox 에 메서드 추가       (실제 실행)
    3. 끝. 등록은 자동입니다 (call 이 이름으로 찾아 씁니다)

스키마의 description 은 그냥 주석이 아닙니다. 모델이 어떤 Tool 을 고를지
판단하는 **유일한 근거**입니다. 애매하게 쓰면 엉뚱한 Tool 을 부릅니다.
"""

import config
import db
from store import Store

# Tool enum의 단일 출처는 SQLite 카탈로그입니다. 서버 재시작 시 현재 DB 값을 읽어
# 모델과 검색기가 같은 값 집합을 사용하게 합니다.
_CATALOG_META = db.read_catalog_metadata()
_ENUMS = _CATALOG_META["enums"]
MATERIAL_NAMES = _ENUMS["material"]
GROUP_NAMES = list(_CATALOG_META["category_groups"])
CATEGORY_NAMES = [
    category
    for categories in _CATALOG_META["category_groups"].values()
    for category in categories
]
GENDER_NAMES = _ENUMS["gender"]
BRAND_NAMES = _ENUMS["brand"]
COLOR_NAMES = _ENUMS["color"]


# ======================================================================
# 반환 형식
#
# 모든 Tool 은 같은 모양으로 돌려줍니다. 형식이 제각각이면 모델이 결과를
# 해석하는 데 실패하고, 프롬프트로 일일이 설명해 줘야 합니다.
#
#     {"success": bool, "data": ..., "message": str}
#
# message 는 모델이 사용자에게 그대로 옮겨도 되는 한국어 문장으로 쓰세요.
# 실패했을 때 "왜" 실패했는지가 들어 있어야 에이전트가 대안을 안내합니다.
# ======================================================================

# 응답 크기 상한. 모델이 limit=999 나 20개 비교를 요청하면 토큰이 터진다.
MAX_SEARCH_RESULTS = 50
MAX_COMPARE_ITEMS = 6


def ok(data, message=""):
    return {"success": True, "data": data, "message": message}


def fail(message):
    return {"success": False, "data": None, "message": message}


# ======================================================================
# 모델에게 알려줄 Tool 스키마
#
# 검색·비교·장바구니 6개에 주문·취소·반품·결제 7개를 더해 모두 13개입니다.
# 되돌릴 수 없는 4개(remove_from_cart, cancel_order, return_order, buy_from_cart)는
# confirm 없이 부르면 미리보기만 돌려주고, 실행은 화면의 승인 버튼이 합니다.
# ======================================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_product",
            "description": (
                "조건에 맞는 상품을 검색해 상품 ID 목록과 요약 정보를 반환한다. "
                "카테고리, 성별, 브랜드, 색상, 소재, 가격 범위, 특정 사이즈의 재고 유무, "
                "세탁기 사용 가능 여부는 SQL로 걸러내고, 용도·기능·착용감 같은 "
                "자연어 요구는 의미 유사도로 정렬한다. 의미 유사도가 서비스 기준 "
                "이상인 상품만 최대 50개 반환한다. "
                "사용자가 운동화·셔츠·코트 같은 소분류를 말하면 category는 필수다. "
                "예: '가볍고 편한 남성 운동화'는 category=운동화, gender=남성, "
                "semantic_query='가볍고 편한'으로 호출한다. "
                "상품을 찾는 모든 요청의 첫 단계로 사용한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    # enum 을 쓰는 이유:
                    # 사용자가 "블랙"이라고 말해도 모델이 여기 적힌 값 중에서 고르게 됩니다.
                    # 코드에 별칭 표를 두는 것보다 낫습니다 - 유효한 값이 계약에 적혀 있고,
                    # 값이 추가돼도 이 목록 한 곳만 고치면 됩니다.
                    "category": {
                        "type": "string",
                        "enum": CATEGORY_NAMES,
                        "description": "상품 소분류. **사용자가 말한 낱말이 위 목록에 있으면 반드시 이 인자를 쓴다.** "
                            "'비 올 때 걸칠 재킷' -> category=재킷 (group 은 비운다). "
                            "'바지', '청바지', '슬랙스' -> category=팬츠, "
                            "'치마' -> category=스커트. 이때 group=하의는 쓰지 않는다. "
                            "목록에 없는 넓은 말일 때만 group 으로 넘어간다",
                    },
                    "group": {
                        "type": "string",
                        "enum": GROUP_NAMES,
                        "description": (
                            "카테고리 대분류. category 로 말할 수 없을 때만 쓴다. "
                            "사용자가 '신발', '겉옷'처럼 여러 소분류를 아우르는 말을 했을 때다. "
                            "운동화·재킷처럼 category 목록의 품목을 말했으면 생략한다. "
                            "바지·청바지·슬랙스는 하의가 아니라 category=팬츠로 넣는다"
                        ),
                    },
                    "gender": {
                        "type": "string",
                        "enum": GENDER_NAMES,
                        "description": (
                            "성별 구분. 남성 또는 여성으로 검색하면 남녀공용 상품도 함께 나온다. "
                            "사용자가 성별을 언급하지 않으면 생략할 것"
                        ),
                    },
                    "brand": {
                        "type": "string",
                        "enum": BRAND_NAMES,
                        "description": "브랜드명",
                    },
                    "product_name": {
                        "type": "string",
                        "description": (
                            "사용자가 특정 상품명을 직접 말했을 때만 넣는다. 상품명 일부도 가능하다. "
                            "예: '클라우드 워크 2 찾아줘' -> product_name='클라우드 워크 2'. "
                            "품목·용도·분위기를 상품명으로 추측해서 넣지 않는다"
                        ),
                        "minLength": 1,
                        "maxLength": 100,
                    },
                    "max_price": {"type": "integer", "minimum": 0, "description": "이 금액 이하의 상품만. 단위는 원"},
                    "min_price": {"type": "integer", "minimum": 0, "description": "이 금액 이상의 상품만. 단위는 원"},
                    "color": {
                        "type": "string",
                        "enum": COLOR_NAMES,
                        "description": "상품 색상. 사용자가 '블랙' 처럼 말해도 여기 있는 값으로 바꿔서 넣을 것",
                    },
                    "size": {
                        "type": "integer",
                        "description": (
                            "이 사이즈의 재고가 있는 상품만. 카테고리마다 체계가 다르다. "
                            "신발(운동화/구두/부츠/샌들) 220~290, "
                            "상의·아우터 남성 90~110 / 여성 44~77, "
                            "팬츠 남성 28~36 / 여성 25~29"
                        ),
                    },
                    "material": {
                        "type": "string",
                        "enum": MATERIAL_NAMES,
                        "description": (
                            "소재. 사용자가 '린넨 셔츠', '캐시미어 니트' 처럼 소재를 말하면 쓴다. "
                            "'면 100%' 같은 혼용률이 아니라 여기 있는 짧은 이름을 넣을 것"
                        ),
                    },
                    "machine_washable": {
                        "type": "boolean",
                        "description": "true 면 세탁기 사용이 가능한 상품만 검색한다",
                    },
                    "semantic_query": {
                        "type": "string",
                        "description": (
                            "위의 구조화 인자를 모두 채운 뒤 남은 용도, 기능, 분위기, 착용감을 "
                            "짧은 자연어로 적는다. 예: '비 오는 날 신기 좋은', "
                            "'가볍고 오래 걸어도 편한', '격식 있는 출근용'. "
                            "category·group·색상·성별·브랜드·가격·사이즈·소재는 반복하지 않는다. "
                            "이 인자는 category를 대신하지 않는다. 사용자가 '운동화'를 말했으면 "
                            "semantic_query와 별개로 category='운동화'도 반드시 넣는다. "
                            "의미 요구가 없고 정확한 조건만 있으면 생략한다"
                        ),
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "sort": {
                        "type": "string",
                        "enum": ["price_asc", "price_desc", "rating", "review"],
                        "description": (
                            "정렬 기준. price_asc 저가순, price_desc 고가순, "
                            "rating 평점순, review 리뷰많은순. rating은 사용자가 '평점' 또는 "
                            "'별점'이라고 직접 말했을 때만 사용하고, review는 '리뷰'라고 말했을 "
                            "때만 사용한다. '편한 순', '가벼운 순', '잘 어울리는 순', '추천순'은 "
                            "절대로 rating으로 바꾸지 않는다. 이런 표현은 semantic_query에 넣고 "
                            "sort를 생략한다"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_info",
            "description": (
                "상품 하나의 상세 정보를 조회한다. 가격, 색상, 사이즈별 재고 수량, 평점, "
                "소재, 세탁 방법, 세탁기 사용 가능 여부, 배송 소요일을 반환한다. "
                "특정 사이즈의 재고를 확인하거나 세탁·관리 방법을 물어볼 때 사용한다. "
                "product_id 를 모르면 product_name 에 상품명을 넣어도 된다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "조회할 상품 ID. 예: P001"},
                    "product_name": {
                        "type": "string",
                        "description": "상품명. product_id 를 모를 때 대신 사용한다. 예: 시티 라이트 재킷",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "comparing_info",
            "description": (
                "여러 상품의 가격, 평점, 리뷰 수, 소재, 세탁기 사용 가능 여부, 배송일을 "
                "나란히 비교할 수 있는 표를 반환한다. "
                "이 Tool 은 상품을 추천하거나 선택하지 않는다. "
                "어떤 상품이 사용자 조건에 가장 적합한지는 반환된 정보를 보고 직접 판단해야 한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "비교할 상품 ID 목록. search_product 결과에서 고른다",
                    },
                },
                "required": ["product_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": (
                "상품을 장바구니에 담는다. 재고가 부족하면 실패하며 남은 수량을 알려준다. "
                "상품 ID 를 모르면 product_name 에 상품명을 넣어라. ID 를 추측하지 마라. "
                "사이즈를 생략하면 재고가 있는 사이즈 목록을 돌려주므로, "
                "사용자가 사이즈를 말하지 않았다면 추측하지 말고 생략한 뒤 되물어라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "담을 상품 ID. 예: P072"},
                    "product_name": {
                        "type": "string",
                        "description": "상품명. ID 를 모를 때 대신 사용한다. ID 를 추측하지 말 것",
                    },
                    "size": {
                        "type": "integer",
                        "description": "사이즈. 모르면 생략할 것 (추측 금지)",
                    },
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 99,
                                 "description": "수량. 1 이상의 정수. 기본 1"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": (
                "현재 장바구니에 담긴 상품 목록과 합계 금액을 반환한다. "
                "장바구니 내용을 묻는 질문에 답할 때, 그리고 결제하기 전에 "
                "무엇을 구매하는지 사용자에게 확인시킬 때 사용한다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": (
                "장바구니에서 상품을 빼거나 수량을 줄인다. "
                "confirm 없이 호출하면 '...빼시겠어요?' 라는 확인 문장이 돌아오고 "
                "실제로 빠지지 않는다. 그 문장을 사용자에게 전하고, "
                "그 뒤 실행은 앱이 화면의 승인 버튼으로 처리한다. "
                "사용자가 말로 동의하더라도 다시 호출하지 말고 버튼을 안내한다. "
                "확인 질문을 직접 만들지 말고 이 Tool 을 먼저 불러라. "
                "상품 ID 를 모르면 product_name 에 상품명을 넣어라. ID 를 추측하지 마라. "
                "size 를 생략하면 그 상품을 사이즈 상관없이 전부 뺀다. "
                "quantity 를 지정하면 그 수량만큼만 줄인다. "
                "여러 개를 한 번에 뺄 때는 items 배열에 담아 한 번만 호출한다. "
                "'225는 3개 다 빼고 230은 2개만' 처럼 사이즈마다 수량이 다르면 items 를 써라. "
                "Tool 을 두 번 나눠 부르지 말고 items 로 한 번에 보내라. "
                "실행 후 메시지에 무엇을 뺐는지와 장바구니 현황이 들어 있으므로 "
                "그 내용을 사용자에게 알려주고, "
                "'방금 뺀 것 다시 담아줘' 라고 하면 그 메시지를 참고해 다시 담아라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "뺄 상품 ID. 예: P072"},
                    "product_name": {
                        "type": "string",
                        "description": "상품명. ID 를 모를 때 대신 사용한다. ID 를 추측하지 말 것",
                    },
                    "size": {"type": "integer", "description": "특정 사이즈만 뺄 경우 지정"},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 99,
                                 "description": "줄일 수량. 1 이상의 정수. 생략하면 해당 항목 전부"},
                    "items": {
                        "type": "array",
                        "maxItems": 10,
                        "minItems": 1,
                        "description": (
                            "여러 대상을 한 번에 뺄 때 사용한다. "
                            "이 인자를 쓰면 product_id/product_name/size/quantity 는 넣지 않는다"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "string", "description": "뺄 상품 ID"},
                                "product_name": {"type": "string",
                                                 "description": "상품명. ID 를 모를 때"},
                                "size": {"type": "integer", "description": "사이즈"},
                                "quantity": {"type": "integer", "minimum": 1, "maximum": 99,
                                             "description": "줄일 수량. 생략하면 해당 항목 전부"},
                            },
                        },
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": (
                            "앱 내부 실행용입니다. **모델은 이 값을 넣지 마세요.** "
                            "넣어도 실행되지 않고 미리보기만 돌아옵니다. "
                            "실행은 사용자가 화면의 승인 버튼을 눌러야 일어납니다."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    # ------------------------------------------------------------------
    # 주문 관련 Tool (아래에 이어집니다)
    #
    #   search_order      조건(기간, 상품명, 상태)으로 주문 목록 검색
    #   get_order         주문 ID 로 상세 조회 (배송 상태 포함)
    #   cancel_possible   취소 가능 여부 + 이유 + 대안
    #   cancel_order      실제 취소 (미리보기 -> 승인 버튼)
    #   return_possible   반품 가능 여부 + 남은 기간
    #   return_order      실제 반품 신청 (confirm 파라미터 필요)
    #   buy_from_cart     장바구니 결제 (confirm 파라미터 필요)
    #
    # 원래 목록에 있던 track_order 는 get_order 와 겹쳐서 뺐습니다.
    # qna 도 뺐습니다. 세탁 방법 질문은 get_info 의 care 필드로 답할 수 있고,
    # 비슷한 Tool 이 둘 있으면 모델이 어느 쪽을 부를지 헷갈립니다.
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "search_order",
            "description": (
                "주문 내역을 검색한다. 취소·반품·배송 조회의 첫 단계로 사용한다. "
                "주문 ID 를 모를 때 여기서 찾는다. 주문 ID 를 추측하지 마라. "
                "날짜 조건이 세 가지이므로 사용자가 말한 표현에 맞는 것을 골라야 한다. "
                "'어제 주문한' -> ordered_days_ago=1 (오늘 주문한 것이 섞이면 안 된다), "
                "'최근 며칠 안에 주문한' -> ordered_within_days, "
                "'지난주에 받은' -> delivered_within_days=7 (주문일이 아니라 수령일 기준), "
                "'8월 주문' -> ordered_from/ordered_to 로 그 달의 첫날과 마지막날을 지정."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "상품명이나 카테고리. 예: 운동화, 셔츠, 어반 러너 블랙",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["배송 준비 중", "배송 중", "배송 완료", "취소됨", "반품 신청됨"],
                        "description": "주문 상태로 거른다",
                    },
                    "ordered_within_days": {
                        "type": "integer", "minimum": 0, "maximum": 365,
                        "description": "주문일이 오늘로부터 N일 이내. 오늘 주문한 것도 포함된다",
                    },
                    "ordered_days_ago": {
                        "type": "integer", "minimum": 0, "maximum": 365,
                        "description": (
                            "정확히 N일 전에 주문한 것만. '어제' 는 1, '오늘' 은 0. "
                            "'어제 주문한' 처럼 특정 날짜를 말하면 이것을 쓴다"
                        ),
                    },
                    "delivered_within_days": {
                        "type": "integer", "minimum": 0, "maximum": 365,
                        "description": (
                            "수령일이 오늘로부터 N일 이내. '지난주에 받은' 은 7. "
                            "아직 받지 않은 주문은 걸리지 않는다"
                        ),
                    },
                    "ordered_from": {
                        "type": "string",
                        "description": (
                            "이 날짜 이후에 주문한 것만. YYYY-MM-DD 형식. "
                            "'8월 주문 내역' 처럼 달로 끊어 볼 때 ordered_to 와 함께 쓴다"
                        ),
                    },
                    "ordered_to": {
                        "type": "string",
                        "description": "이 날짜 이전에 주문한 것만. YYYY-MM-DD 형식",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_order",
            "description": (
                "주문 하나의 상세 정보를 조회한다. 주문일, 발송일, 수령일, 상태, "
                "취소 가능 여부, 반품 가능 여부와 반품 마감일을 함께 반환한다. "
                "order_id 를 모르면 search_order 로 먼저 찾아라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "주문 ID. 예: ORD-1001"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_possible",
            "description": (
                "이 주문을 지금 취소할 수 있는지 확인한다. 주문 상태는 바뀌지 않는다. "
                "취소할 수 없으면 그 이유와 대신 할 수 있는 방법을 함께 반환한다. "
                "사용자가 '취소되나요?' 처럼 실행 없이 물어볼 때만 쓴다. "
                "취소할 의사가 분명하면 이 Tool 을 건너뛰고 cancel_order 를 바로 불러라. "
                "cancel_order 가 가능 여부를 스스로 확인하므로 먼저 물어볼 필요가 없고, "
                "여기서 한 번 묻고 다시 확인받으면 사용자가 같은 답을 두 번 하게 된다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "주문 ID"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": (
                "주문을 취소한다. confirm 없이 호출하면 '...취소할까요?' 라는 확인 문장이 "
                "돌아오고 실제로 취소되지 않는다. 그 문장을 사용자에게 전하고, "
                "그 뒤 실행은 앱이 화면의 승인 버튼으로 처리한다. "
                "사용자가 말로 동의하더라도 다시 호출하지 말고 버튼을 안내한다. "
                "확인 질문을 직접 만들지 말고 이 Tool 을 먼저 불러라. "
                "가능 여부는 이 Tool 이 스스로 확인하므로 cancel_possible 을 먼저 부를 필요가 없다. "
                "취소가 불가능한 주문이면 이유와 대안을 반환한다. "
                "한 번에 한 건만 취소할 수 있다. 여러 건이면 하나씩 확인받아라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "취소할 주문 ID"},
                    "confirm": {
                        "type": "boolean",
                        "description": (
                            "앱 내부 실행용입니다. **모델은 이 값을 넣지 마세요.** "
                            "넣어도 실행되지 않고 미리보기만 돌아옵니다. "
                            "실행은 사용자가 화면의 승인 버튼을 눌러야 일어납니다."
                        ),
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_possible",
            "description": (
                "이 주문을 반품할 수 있는지 확인한다. 주문 상태는 바뀌지 않는다. "
                "반품 가능 기간은 수령일로부터 계산되며, 마감일도 함께 반환한다. "
                "사용자가 '환불되나요?' 처럼 실행 없이 물어볼 때만 쓴다. "
                "반품할 의사가 분명하면 이 Tool 을 건너뛰고 return_order 를 바로 불러라. "
                "return_order 가 가능 여부를 스스로 확인한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "주문 ID"},
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "return_order",
            "description": (
                "반품을 신청한다. confirm 없이 호출하면 '...신청할까요?' 라는 확인 문장이 "
                "돌아오고 실제로 접수되지 않는다. 그 문장을 사용자에게 전하고, "
                "그 뒤 실행은 앱이 화면의 승인 버튼으로 처리한다. "
                "사용자가 말로 동의하더라도 다시 호출하지 말고 버튼을 안내한다. "
                "확인 질문을 직접 만들지 말고 이 Tool 을 먼저 불러라. "
                "가능 여부는 이 Tool 이 스스로 확인하므로 return_possible 을 먼저 부를 필요가 없다. "
                "이 Tool 은 반품 '접수' 까지만 한다. 환불은 상품 회수가 끝난 뒤에 처리되므로 "
                "사용자에게 '환불되었습니다' 가 아니라 '반품이 접수되었고 회수 후 환불된다' 고 알려야 한다. "
                "한 번에 한 건만 신청할 수 있다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "description": "반품할 주문 ID"},
                    "reason": {
                        "type": "string",
                        "description": "반품 사유. 사용자가 말한 이유를 그대로 적는다. 예: 사이즈가 맞지 않음",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": (
                            "앱 내부 실행용입니다. **모델은 이 값을 넣지 마세요.** "
                            "넣어도 실행되지 않고 미리보기만 돌아옵니다. "
                            "실행은 사용자가 화면의 승인 버튼을 눌러야 일어납니다."
                        ),
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_from_cart",
            "description": (
                "장바구니에 담긴 상품을 실제로 주문한다(결제). "
                "confirm 없이 호출하면 주문 내역과 결제 금액이 담긴 확인 문장이 돌아오고 "
                "실제로 결제되지 않는다. 그 문장을 사용자에게 전하고, "
                "그 뒤 실행은 앱이 화면의 승인 버튼으로 처리한다. "
                "사용자가 말로 동의하더라도 다시 호출하지 말고 버튼을 안내한다. "
                "확인 질문을 직접 만들지 말고 이 Tool 을 먼저 불러라. "
                "사용자가 '주문할게', '결제해줘' 라고 명확히 말했을 때만 사용한다. "
                "장바구니에 담는 것(add_to_cart)과 혼동하지 마라. "
                "장바구니 전체가 아니라 일부만 주문하려면 items 에 그 항목만 담아라. "
                "이때 나머지를 빼려고 remove_from_cart 를 부를 필요가 없다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "maxItems": 10,
                        "minItems": 1,
                        "description": (
                            "장바구니에서 **일부만** 주문할 때 사용한다. "
                            "생략하면 장바구니 전체를 주문한다. "
                            "나머지 항목은 장바구니에 그대로 남는다"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "product_id": {"type": "string",
                                               "description": "주문할 상품 ID"},
                                "product_name": {"type": "string",
                                                 "description": "상품명. ID 를 모를 때"},
                                "size": {"type": "integer", "description": "사이즈"},
                                "quantity": {"type": "integer", "minimum": 1,
                                             "maximum": 99,
                                             "description": "주문할 수량. 생략하면 담긴 만큼 전부"},
                            },
                        },
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": (
                            "앱 내부 실행용입니다. **모델은 이 값을 넣지 마세요.** "
                            "넣어도 실행되지 않고 미리보기만 돌아옵니다. "
                            "실행은 사용자가 화면의 승인 버튼을 눌러야 일어납니다."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
]


# ======================================================================
# 되돌릴 수 없는 작업의 확인 절차
#
# cancel_order, return_order, buy_from_cart 는 한 번 실행하면 되돌릴 수 없습니다.
# 모델이 사용자 말을 잘못 알아듣고 바로 실행하면 사고입니다.
#
# 그래서 이 세 Tool 은 confirm 파라미터를 받게 만드세요.
#
#   confirm=False (기본) -> 실행하지 않고 미리보기만 반환
#       {"success": True,
#        "data": {"requires_confirmation": True,
#                 "preview": {"order_id": "ORD-1001", "상품": "어반 러너 블랙",
#                             "환불액": 129000}},
#        "message": "ORD-1001 어반 러너 블랙 129,000원을 취소합니다. 진행할까요?"}
#
#   confirm=True -> 실제 실행
#
# 시스템 프롬프트에 "confirm=True 는 사용자가 명시적으로 동의한 뒤에만 쓸 것"을
# 적어두세요. 발표 때 설명하기 좋은 포인트이기도 합니다.
# ======================================================================


# ======================================================================
# 인자 검증
#
# 스키마에 "type": "integer" 라고 적어 두는 것은 모델에게 주는 안내일 뿐,
# 모델이 그대로 보낸다는 보장이 없습니다. 실제로 아래가 전부 통과했습니다.
#
#     remove_from_cart(quantity=-5, confirm=True)   -> 1개가 6개로 늘어남
#     add_to_cart(quantity=1.5)                     -> 1.5개가 담김
#     remove_from_cart(confirm="false")             -> 문자열이 참이라 삭제됨
#
# 스키마를 선언만 하고 검사하지 않으면 선언이 아무 일도 하지 않습니다.
# 그래서 실행 직전에 같은 스키마로 한 번 더 검사합니다.
# 검사에 실패하면 Tool 을 부르지 않으므로 Store 상태는 그대로입니다.
# ======================================================================

# 이름으로 스키마를 찾기 위한 색인. TOOLS 에 등록된 것만 실행 대상입니다.
TOOL_INDEX = {tool["function"]["name"]: tool["function"] for tool in TOOLS}


def _type_name(value):
    return {bool: "boolean", int: "integer", float: "number",
            str: "string", list: "array", dict: "object"}.get(type(value), type(value).__name__)


def _check_one(key, value, rule):
    """값 하나를 스키마 규칙에 맞춰 검사한다. (정리된 값, 오류메시지) 를 돌려준다."""
    expected = rule.get("type")

    # boolean 을 먼저 봅니다. 파이썬에서 bool 은 int 의 하위 타입이라
    # isinstance(True, int) 가 참입니다. 순서를 바꾸면 confirm=1 이 통과합니다.
    if expected == "boolean":
        if not isinstance(value, bool):
            return None, (f"'{key}' 는 true 또는 false 여야 합니다. "
                          f"받은 값: {value!r} ({_type_name(value)}). "
                          '문자열 "true" 나 숫자 1 은 받지 않습니다.')
        return value, None

    if expected == "integer":
        if isinstance(value, bool):
            return None, f"'{key}' 는 정수여야 합니다. true/false 는 받지 않습니다."
        # "270" 처럼 정수만 담긴 문자열은 받아 줍니다. 값이 바뀌지 않는 변환이라
        # 안전하고, 이것까지 막으면 모델이 같은 실수를 반복하며 턴만 소모합니다.
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if not isinstance(value, int):
            return None, (f"'{key}' 는 정수여야 합니다. 받은 값: {value!r} "
                          f"({_type_name(value)}). 1.5 같은 소수는 받지 않습니다.")
    elif expected == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"'{key}' 는 숫자여야 합니다. 받은 값: {value!r}"
    elif expected == "string":
        if not isinstance(value, str):
            return None, f"'{key}' 는 문자열이어야 합니다. 받은 값: {value!r}"
        if "minLength" in rule and len(value) < rule["minLength"]:
            return None, f"'{key}' 는 빈 문자열일 수 없습니다."
        if "maxLength" in rule and len(value) > rule["maxLength"]:
            return None, (f"'{key}' 는 최대 {rule['maxLength']}자까지입니다. "
                          f"받은 길이: {len(value)}")
    elif expected == "array":
        if not isinstance(value, list):
            return None, f"'{key}' 는 배열이어야 합니다. 받은 값: {value!r}"
        if "maxItems" in rule and len(value) > rule["maxItems"]:
            return None, (f"'{key}' 는 한 번에 최대 {rule['maxItems']}개까지입니다. "
                          f"받은 개수: {len(value)}")
        if "minItems" in rule and len(value) < rule["minItems"]:
            return None, f"'{key}' 에 항목이 최소 {rule['minItems']}개 필요합니다."
        item_rule = rule.get("items") or {}
        checked = []
        for index, item in enumerate(value):
            cleaned, error = _check_one(f"{key}[{index}]", item, item_rule)
            if error:
                return None, error
            checked.append(cleaned)
        value = checked

    elif expected == "object":
        # 배열 안의 객체까지 재귀로 검사합니다.
        # 이게 없으면 items=[{"quantity": -5}] 같은 값이 그대로 통과합니다.
        # (바깥 인자만 막고 안쪽을 안 보면 검증이 뚫린 것과 같습니다)
        if not isinstance(value, dict):
            return None, f"'{key}' 는 객체여야 합니다. 받은 값: {value!r}"

        properties = rule.get("properties") or {}
        required = rule.get("required") or []

        missing = [name for name in required if name not in value]
        if missing:
            return None, f"'{key}' 에 필수 항목이 빠졌습니다: {', '.join(missing)}"

        unknown = [name for name in value if name not in properties]
        if properties and unknown:
            return None, (f"'{key}' 에 없는 항목입니다: {', '.join(unknown)}. "
                          f"사용 가능한 항목: {', '.join(properties)}")

        cleaned_object = {}
        for name, inner in value.items():
            if inner is None:
                continue
            result, error = _check_one(f"{key}.{name}", inner, properties.get(name) or {})
            if error:
                return None, error
            cleaned_object[name] = result
        value = cleaned_object

    if "enum" in rule and value not in rule["enum"]:
        return None, (f"'{key}' 에 '{value}' 는 쓸 수 없습니다. "
                      f"가능한 값: {', '.join(map(str, rule['enum']))}")

    if "minimum" in rule and value < rule["minimum"]:
        return None, (f"'{key}' 는 {rule['minimum']} 이상이어야 합니다. "
                      f"받은 값: {value}")
    if "maximum" in rule and value > rule["maximum"]:
        return None, (f"'{key}' 는 {rule['maximum']} 이하여야 합니다. "
                      f"받은 값: {value}")

    return value, None


def validate_call(name, arguments):
    """Tool 이름과 인자를 검사한다. (정리된 인자, 오류메시지) 를 돌려준다.

    오류메시지는 모델이 읽고 스스로 고칠 수 있는 문장으로 씁니다.
    "타입 오류" 가 아니라 "정수여야 합니다. 받은 값: 1.5" 처럼 적습니다.
    """
    if name not in TOOL_INDEX:
        return None, (f"'{name}' 이라는 Tool 은 없습니다. "
                      f"사용 가능한 Tool: {', '.join(TOOL_INDEX)}")

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return None, f"'{name}' 의 인자는 객체여야 합니다. 받은 값: {arguments!r}"

    schema = TOOL_INDEX[name].get("parameters") or {}
    properties = schema.get("properties") or {}
    required = schema.get("required") or []

    missing = [key for key in required if key not in arguments]
    if missing:
        return None, f"'{name}' 에 필수 인자가 빠졌습니다: {', '.join(missing)}"

    unknown = [key for key in arguments if key not in properties]
    if unknown:
        return None, (f"'{name}' 에 없는 인자입니다: {', '.join(unknown)}. "
                      f"사용 가능한 인자: {', '.join(properties) or '없음'}")

    cleaned = {}
    for key, value in arguments.items():
        if value is None:            # 생략과 같게 취급
            continue
        result, error = _check_one(key, value, properties[key])
        if error:
            return None, f"{name}: {error}"
        cleaned[key] = result

    if name == "search_product":
        # JSON Schema의 minLength는 공백 문자열까지 막지 못하므로 실행 전에 정리한다.
        for key in ("semantic_query", "product_name"):
            if key in cleaned:
                cleaned[key] = cleaned[key].strip()
                if not cleaned[key]:
                    cleaned.pop(key)

        minimum = cleaned.get("min_price")
        maximum = cleaned.get("max_price")
        if minimum is not None and maximum is not None and minimum > maximum:
            return None, ("search_product: min_price는 max_price보다 클 수 없습니다. "
                          f"받은 범위: {minimum:,}원~{maximum:,}원")

        category = cleaned.get("category")
        group = cleaned.get("group")
        if category and group:
            expected_group = next(
                (name for name, categories in _CATALOG_META["category_groups"].items()
                 if category in categories), None)
            if expected_group != group:
                return None, (f"search_product: category='{category}'는 group='{group}'에 "
                              f"속하지 않습니다. 올바른 group은 '{expected_group}'입니다.")
            # 소분류가 더 정확하므로 중복된 대분류는 실행 인자에서 제거한다.
            cleaned.pop("group")

    return cleaned, None


class Toolbox:
    """Tool 실행기. Store 하나를 붙잡고 그 위에서 동작한다.

    store 를 인자로 받는 이유: Streamlit 세션마다 Store 가 따로이므로
    모듈 전역 변수로 두면 사용자들의 장바구니가 뒤섞입니다.

        store = Store()
        toolbox = Toolbox(store)
        toolbox.call("search_product", {"color": "검은색", "max_price": 150000})
    """

    def __init__(self, store: Store):
        self.store = store

    # ------------------------------------------------------------------
    # 상품 지정 해석
    #
    # 사용자는 "크롭 슬림 티" 라고 말하고 "P072" 라고는 말하지 않습니다.
    # 그래서 모델이 ID 를 지어내는 일이 실제로 자주 일어납니다.
    # (관찰된 사례: 크롭 슬림 티를 P002 로 추측 -> 실패 -> view_cart 로 확인 -> 재시도)
    #
    # 상품을 다루는 모든 Tool 이 이름으로도 찾을 수 있게 해서 그 왕복을 없앱니다.
    # ------------------------------------------------------------------

    def _cart_summary(self):
        """장바구니 현황을 한 줄로 요약한다.

        장바구니를 바꾼 Tool 은 결과 메시지에 이걸 붙입니다.
        data 에도 같은 내용이 들어 있지만, 모델이 message 를 더 확실하게 읽습니다.
        그래야 담기·빼기 직후에 "현재 장바구니는 ..." 을 사용자에게 알려줄 수 있습니다.
        """
        cart = self.store.view_cart()

        if cart["count"] == 0:
            return "현재 장바구니는 비어 있습니다."

        items = " / ".join(
            f"{item['name']} {item['size']} 사이즈 {item['quantity']}개"
            for item in cart["items"]
        )
        return (f"현재 장바구니: {items} "
                f"(총 {cart['quantity']}개, 합계 {cart['total']:,}원)")

    def _resolve_product(self, product_id=None, product_name=None):
        """product_id 또는 product_name 으로 상품을 찾는다.

        반환: (상품, 오류응답)  - 찾으면 (product, None), 못 찾으면 (None, fail(...))
        """
        if not product_id and not product_name:
            return None, fail("product_id 또는 product_name 중 하나는 필요합니다.")

        product = self.store.get_product(product_id) if product_id else None
        if product is not None:
            return product, None

        # ID 로 못 찾았으면 이름으로 시도한다.
        # 모델이 이름을 product_id 자리에 넣는 경우도 있어 둘 다 살펴본다.
        candidate, matches = self.store.find_product_by_name(product_name or product_id)
        if candidate is not None:
            return candidate, None

        if matches:
            names = ", ".join(f"{p['name']}({p['id']})" for p in matches[:6])
            return None, fail(f"이름이 비슷한 상품이 여러 개입니다. 하나를 골라 주세요: {names}")

        given = product_name or product_id
        return None, fail(
            f"'{given}' 상품을 찾을 수 없습니다. 상품 ID 를 추측하지 마세요. "
            "장바구니 안의 상품이면 view_cart, 그 밖이면 search_product 로 확인하세요."
        )

    # ------------------------------------------------------------------
    # 참고용 완성 예시.
    # 나머지 메서드도 이 모양을 따라가면 됩니다:
    #   store 호출 -> 결과를 ok()/fail() 로 감싸기. 끝.
    # ------------------------------------------------------------------
    def search_product(self, semantic_query=None, product_name=None,
                       group=None, category=None, gender=None,
                       brand=None, max_price=None, min_price=None, color=None, size=None,
                       material=None, machine_washable=None, sort=None):
        # 결과 개수는 모델이 결정하지 않는다. 검색 서비스의 안전 상한이다.
        limit = MAX_SEARCH_RESULTS

        conditions = dict(
            product_name=product_name, group=group, category=category,
            gender=gender, brand=brand,
            max_price=max_price, min_price=min_price, color=color, size=size,
            material=material, machine_washable=machine_washable,
        )
        total = self.store.count_products(**conditions)
        # SQL로 후보를 거른 다음 semantic_query가 있으면 임베딩으로 상위 50개를
        # 고른다. 사용자가 가격·평점·리뷰 정렬을 명시했다면 그 50개 안에서만
        # 다시 정렬한다. 의미 조건이 조용히 사라지지 않게 하기 위한 순서다.
        # 임베딩을 쓸 수 없는 환경에서는 SQL 결과로 안전하게 되돌아간다.
        ranking = "sql"
        semantic = None
        if semantic_query:
            semantic = self.store.search_semantic_result(
                semantic_query, top_k=limit,
                min_score=config.SEMANTIC_MIN_SCORE,
                min_results=config.SEMANTIC_MIN_RESULTS,
                # 명시 정렬은 바로 아래에서 최종 순서를 덮어쓰므로 리랭킹하면 낭비다.
                rerank_results=not bool(sort), **conditions)
            products = semantic["products"]
            if semantic["available"]:
                if sort:
                    products = self.store.sort_products(products, sort)
                    base = "semantic_reranked" if semantic.get("reranked") else "semantic"
                    ranking = f"{base}_then_{sort}"
                else:
                    ranking = ("semantic_reranked" if semantic.get("reranked")
                               else "semantic")
            else:
                products = self.store.search_products(
                    sort=sort, limit=limit, **conditions)
                ranking = "sql_fallback"
        else:
            products = self.store.search_products(
                sort=sort, limit=limit, **conditions)

        # 임베딩 계산은 정상적으로 끝났지만 기준을 넘긴 상품이 없는 경우다.
        # 임베딩 자체가 고장 난 경우의 SQL fallback과 구분해야 임계값이 무력화되지 않는다.
        if semantic is not None and semantic["available"] and not products:
            return ok({
                "total": total,
                "qualified": 0,
                "shown": 0,
                "ranking": ranking,
                "semantic_min_score": config.SEMANTIC_MIN_SCORE,
                "products": [],
            }, (f"구조화 조건에는 상품 {total}개가 있지만 의미 유사도 기준 "
                f"{config.SEMANTIC_MIN_SCORE:.2f} 이상인 상품은 없습니다. "
                "semantic_query를 제거해 조건에 덜 맞는 상품을 다시 검색하지 마세요. "
                "조건을 완화하려면 먼저 사용자에게 물어보세요."))

        if not products:
            # 그냥 "없습니다" 로 끝내지 않고 유효한 값을 함께 알려줍니다.
            # 모델이 잘못된 색상/카테고리를 넣었다면 이걸 읽고 스스로 다시 검색합니다.
            # 코드에 별칭 표를 두는 것보다 이 방식이 낫습니다 — 미리 상상한 오타만이 아니라
            # 모든 종류의 어긋남을 처리하기 때문입니다.
            hints = []
            if color and color not in self.store.available_colors():
                hints.append(
                    f"'{color}' 은 없는 색상입니다. "
                    f"가능한 색상: {', '.join(self.store.available_colors())}")
            if category and category not in self.store.available_categories():
                hints.append(
                    f"'{category}' 는 없는 카테고리입니다. "
                    f"가능한 카테고리: {', '.join(self.store.available_categories())}")
            if group and group not in self.store.available_groups():
                hints.append(
                    f"'{group}' 는 없는 대분류입니다. "
                    f"가능한 대분류: {', '.join(self.store.available_groups())}")
            if brand and brand not in self.store.available_brands():
                hints.append(
                    f"'{brand}' 는 없는 브랜드입니다. "
                    f"가능한 브랜드: {', '.join(self.store.available_brands())}")
            if material and material not in MATERIAL_NAMES:
                # 카테고리를 알면 그 안의 소재만 알려줍니다. 36개를 다 나열하면 길어서
                # 모델이 오히려 엉뚱한 것을 고릅니다.
                valid = self.store.available_materials(category)
                hints.append(
                    f"'{material}' 는 없는 소재입니다. "
                    f"가능한 소재: {', '.join(valid)}")
            if size is not None:
                valid = self.store.available_sizes(category, gender)
                if valid and size not in valid:
                    hints.append(
                        f"{size} 는 이 품목에 없는 사이즈입니다. "
                        f"가능한 사이즈: {', '.join(map(str, valid))}")

            if hints:
                return fail(" / ".join(hints))
            return fail("조건에 맞는 상품이 없습니다. 가격 범위나 사이즈 조건을 넓혀 보세요.")

        # 모델에게는 필요한 필드만 넘깁니다. dict 를 통째로 주면 토큰이 낭비되고
        # 모델이 엉뚱한 필드에 주목하기도 합니다.
        summaries = [
            {
                "product_id": product["id"],
                "name": product["name"],
                "category": product["category"],
                "gender": product["gender"],
                "brand": product["brand"],
                "price": product["price"],
                "color": product["color"],
                "material": product["material"],
                "rating": product["rating"],
                "available_sizes": [s for s, stock in product["sizes"].items() if stock > 0],
            }
            for product in products
        ]
        # "이게 전부인지 일부인지" 를 반드시 알려준다.
        # 없으면 모델이 "운동화는 5종류 있습니다" 처럼 잘못 답한다.
        qualified = semantic["qualified_count"] if semantic and semantic["available"] else total
        backfilled = semantic.get("backfilled", 0) if semantic and semantic["available"] else 0
        if semantic and semantic["available"]:
            if backfilled:
                # 기준 통과가 적어 아래에서 채운 경우. 모델이 "N개를 찾았다" 고만 말하면
                # 사용자는 전부 딱 맞는 상품인 줄 안다. 그렇지 않다고 알려야 한다.
                fit = (f"의미 조건에 잘 맞는 상품은 {qualified}개뿐이어서" if qualified
                       else "의미 조건에 잘 맞는 상품이 없어서")
                tell = (f"딱 맞는 상품은 {qualified}개이고 비슷한 상품도 함께 표시했다" if qualified
                        else "딱 맞는 상품은 없어서 비슷한 상품을 표시했다")
                message = (f"검색 결과 화면에 상품 {len(summaries)}개를 표시합니다. "
                           f"{fit} 비슷한 상품 {backfilled}개를 함께 보여줍니다. "
                           f"사용자에게 '{tell}' 고 안내하세요.")
            elif qualified > len(summaries):
                message = (f"검색 결과 화면에 상품 {len(summaries)}개를 표시합니다. "
                           f"구조화 조건 후보는 {total}개, 의미 유사도 기준을 "
                           f"통과한 후보는 {qualified}개이며 화면 표시 상한은 "
                           f"{MAX_SEARCH_RESULTS}개입니다.")
            else:
                message = (f"검색 결과 화면에 상품 {len(summaries)}개를 표시합니다. "
                           f"의미 유사도 기준을 통과한 상품을 모두 표시했습니다.")
        elif total > len(summaries):
            message = (f"조건에 맞는 상품 {total}개 중 상위 "
                       f"{len(summaries)}개를 검색 결과로 보냅니다.")
        else:
            message = f"조건에 맞는 상품 {total}개를 모두 찾았습니다."

        return ok({
            "total": total,
            "qualified": qualified,
            "shown": len(summaries),
            "displayed": len(summaries),
            "ranking": ranking,
            "backfilled": backfilled,
            "reranked": bool(semantic and semantic.get("reranked")),
            "semantic_min_score": (
                config.SEMANTIC_MIN_SCORE
                if semantic and semantic["available"] else None
            ),
            "products": summaries,
        }, message)

    # ------------------------------------------------------------------
    # Tool 본체. 각 메서드는 얇게 두고 판단은 store 가 합니다(7번 원칙).
    # ------------------------------------------------------------------

    def get_info(self, product_id=None, product_name=None):
        """상품 상세 정보.

        sizes 를 그대로 넘기는 것이 중요하다. {270: 3, 280: 0} 형태를 받으면
        모델이 "270은 3개 남았고 280은 품절입니다" 라고 답할 수 있다.
        care(세탁법), material(소재) 도 함께 넘긴다. 과제의 Q&A 예시가 이 필드를 쓴다.
        """
        product, error = self._resolve_product(product_id, product_name)
        if error:
            return error

        in_stock = [size for size, stock in product["sizes"].items() if stock > 0]

        return ok(
            {
                "product_id": product["id"],
                "name": product["name"],
                "category": product["category"],
                "gender": product["gender"],
                "brand": product["brand"],
                "price": product["price"],
                "color": product["color"],
                "sizes": product["sizes"],          # {사이즈: 재고수량}
                "available_sizes": in_stock,        # 재고 있는 사이즈만 추린 것
                "rating": product["rating"],
                "review_count": product["review_count"],
                "description": product["description"],
                "material": product["material"],              # 짧은 이름 ("린넨")
                "material_detail": product["material_detail"],  # 혼용률 ("린넨 100%")
                "care": product["care"],
                "machine_washable": product["machine_washable"],
                "delivery_days": product["delivery_days"],
            },
            f"{product['name']} 상세 정보입니다.",
        )

    def comparing_info(self, product_ids):
        """여러 상품을 나란히 비교할 표를 만든다.

        이 Tool 은 어느 상품이 더 나은지 판단하지 않는다. 판단은 모델의 몫이다.
        (스키마의 description 에도 그렇게 적어두었다)
        """
        if not product_ids:
            return fail("비교할 상품 ID 를 하나 이상 지정하세요.")

        # 20개를 비교하겠다고 하면 응답이 비대해진다.
        if len(product_ids) > MAX_COMPARE_ITEMS:
            return fail(
                f"한 번에 최대 {MAX_COMPARE_ITEMS}개까지 비교할 수 있습니다. "
                f"({len(product_ids)}개 요청) 후보를 좁혀서 다시 요청하세요."
            )

        rows = []
        missing = []

        for product_id in product_ids:
            product = self.store.get_product(product_id)
            if product is None:
                missing.append(product_id)
                continue

            rows.append({
                "product_id": product["id"],
                "name": product["name"],
                "brand": product["brand"],
                "price": product["price"],
                "color": product["color"],
                "rating": product["rating"],
                "review_count": product["review_count"],
                "delivery_days": product["delivery_days"],
                "material": product["material"],
                "material_detail": product["material_detail"],
                "machine_washable": product["machine_washable"],
                "available_sizes": [s for s, stock in product["sizes"].items() if stock > 0],
            })

        if not rows:
            return fail(f"비교할 상품을 찾을 수 없습니다. (요청한 ID: {', '.join(product_ids)})")

        # 없는 ID 를 조용히 빼지 않고 알려준다.
        # 모델이 ID 를 지어낸 것일 수 있고, 그 사실을 알아야 다시 검색한다.
        message = f"{len(rows)}개 상품의 비교 정보입니다."
        if missing:
            message += f" 다음 ID 는 찾지 못했습니다: {', '.join(missing)}"

        return ok(rows, message)

    def add_to_cart(self, product_id=None, size=None, quantity=1, product_name=None):
        """장바구니에 담는다.

        store 가 (성공여부, 메시지) 를 돌려주므로 그걸 ok()/fail() 로 옮기기만 한다.
        실패 메시지도 store 가 만들어 둔 것을 그대로 쓴다.
        ("재고가 2개뿐입니다" 같은 문장이 모델의 다음 행동을 결정한다)

        size 가 없으면 임의로 고르지 않고 선택지를 돌려준다.
        모델이 사이즈를 추측해 담으면 사용자가 원하지 않는 상품을 사게 된다.
        remove_from_cart 에서 쓴 것과 같은 "애매하면 되묻는다" 패턴이다.
        """
        product, error = self._resolve_product(product_id, product_name)
        if error:
            return error
        product_id = product["id"]

        if size is None:
            in_stock = [s for s, q in product["sizes"].items() if q > 0]
            if not in_stock:
                return fail(f"{product['name']} 은 전 사이즈 품절입니다.")

            return fail(
                f"{product['name']} 의 사이즈를 지정해 주세요. "
                f"재고 있는 사이즈: {', '.join(map(str, in_stock))}"
            )

        success, message = self.store.add_to_cart(product_id, size, quantity)

        if not success:
            return fail(message)

        # 성공하면 담긴 뒤의 장바구니 현황을 함께 넘긴다.
        # 모델이 "담았습니다. 현재 장바구니는 ..." 처럼 답할 수 있다.
        return ok(self.store.view_cart(), f"{message} {self._cart_summary()}")

    def view_cart(self):
        """장바구니 조회.

        비어 있는 것은 오류가 아니라 정상 상태이므로 success=True 로 돌려준다.
        fail 로 돌려주면 모델이 뭔가 잘못된 줄 알고 재시도한다.
        """
        cart = self.store.view_cart()

        if cart["count"] == 0:
            return ok(cart, "장바구니가 비어 있습니다.")

        return ok(
            cart,
            f"장바구니에 {cart['count']}종 {cart['quantity']}개, "
            f"합계 {cart['total']:,}원입니다."
        )

    def remove_from_cart(self, product_id=None, size=None, quantity=None,
                         confirm=False, product_name=None, items=None):
        """장바구니에서 빼거나 수량을 줄인다. 실행 전에 확인을 받는다.

        confirm=False (기본) -> 무엇이 빠질지 미리 보여주고 실행하지 않는다
        confirm=True         -> 실제로 뺀다

        items 로 여러 대상을 한 번에 받는다. 사용자가
        "225는 3개 다 빼고 230은 2개만" 이라고 말하는데 인자가 하나뿐이면
        모델이 표현할 방법이 없어 한쪽을 버리게 된다.

        여러 줄을 처리할 때는 전부 계산한 뒤에 실행한다 (전부 아니면 전무).
        한 줄씩 지우면서 계산하면 앞줄이 뒷줄의 계산을 바꿔서,
        사용자가 승인한 미리보기와 실제 결과가 달라진다.

        삭제는 되돌릴 수 없다. 사용자가 "에어 스텝 화이트 삭제하려고" 라고 했을 때
        세 사이즈 11개를 한꺼번에 지워버리면 사고다.
        그래서 한 번 확인받는다. cancel_order / return_order 도 같은 패턴을 쓴다.

        확인 절차를 store 가 아니라 여기에 둔 이유:
        화면의 ✕ 버튼은 사용자가 직접 누른 것이므로 이미 확인이다.
        오해의 여지가 있는 것은 에이전트가 대화로 지우는 경우뿐이다.
        """
        # items 와 단일 인자를 섞으면 무엇을 지울지 애매해진다.
        # 조용히 한쪽을 무시하면 사용자가 요청한 것과 다른 게 지워지므로 거절한다.
        singles = {"product_id": product_id, "product_name": product_name,
                   "size": size, "quantity": quantity}
        given = [key for key, value in singles.items() if value is not None]
        if items and given:
            return fail(f"items 와 {', '.join(given)} 를 함께 쓸 수 없습니다. "
                        "여러 개를 뺄 때는 items 안에 전부 넣으세요.")

        targets = items if items else [singles]

        # 1단계 - 무엇이 빠질지 전부 계산한다. 장바구니는 아직 그대로다.
        #
        # 한 줄씩 지우면서 계산하면 앞줄이 뒷줄의 계산을 바꾼다.
        # 그러면 사용자가 승인한 미리보기와 실제 결과가 달라진다.
        # 그래서 원래 장바구니 기준으로 전부 계산한 뒤에 실행한다.
        merged = {}        # {(product_id, size): 수량}  중복 줄을 합친다
        names = {}
        for index, target in enumerate(targets, 1):
            product, error = self._resolve_product(target.get("product_id"),
                                                   target.get("product_name"))
            if error:
                label = f"{index}번째 항목: " if items else ""
                return fail(label + error["message"])

            rows = self.store.preview_removal(product["id"], target.get("size"),
                                              target.get("quantity"))
            if not rows:
                label = f"{index}번째 항목({product['name']})은 " if items else ""
                return fail(f"{label}장바구니에 없습니다." if items
                            else "장바구니에 해당 상품이 없습니다.")

            for row in rows:
                key = (row["product_id"], row["size"])
                merged[key] = merged.get(key, 0) + row["quantity"]
                names[key] = row["name"]

        # 합친 수량이 실제 담긴 수량을 넘지 않는지 확인한다.
        # ("225 2개 빼고 225 1개 더" 처럼 나눠 적으면 합계가 넘칠 수 있다)
        in_cart = {(line["product"]["id"], line["size"]): line["quantity"]
                   for line in self.store.cart}
        for key, wanted in merged.items():
            have = in_cart.get(key, 0)
            if wanted > have:
                return fail(f"{names[key]} {key[1]} 사이즈는 {have}개만 담겨 있어 "
                            f"{wanted}개를 뺄 수 없습니다.")

        preview = [{"product_id": pid, "name": names[(pid, size)],
                    "size": size, "quantity": quantity}
                   for (pid, size), quantity in sorted(merged.items())]

        detail = ", ".join(
            f"{row['name']} {row['size']} 사이즈 {row['quantity']}개" for row in preview
        )

        if not confirm:
            hint = ""
            if not items and size is None and quantity is None and len(preview) > 1:
                hint = " 일부 사이즈만 빼시려면 사이즈를 지정해 주세요."
            # message 는 사용자에게 그대로 전달될 수 있으므로 사람 말투만 담는다.
            # "confirm 을 true 로 호출하라" 같은 지시는 스키마 description 에만 둔다.
            # (메시지에 넣으면 모델이 사용자에게 그대로 읽어주는 일이 생긴다)
            return ok(
                {"requires_confirmation": True, "preview": preview},
                f"{detail}를 장바구니에서 빼시겠어요?{hint}",
            )

        # 2단계 - 실행. 위에서 전부 검사했으므로 여기서 실패하면 안 된다.
        # 그래도 실패하면 중간에 멈추지 말고 사유를 그대로 올린다.
        for row in preview:
            success, message = self.store.remove_from_cart(
                row["product_id"], row["size"], row["quantity"])
            if not success:
                return fail(message)

        return ok(self.store.view_cart(),
                  f"{detail}를 장바구니에서 뺐습니다. {self._cart_summary()}")

    # ------------------------------------------------------------------
    # 디스패치
    # ------------------------------------------------------------------

    # ==================================================================
    # 주문 · 취소 · 반품
    #
    # 판단은 전부 store 가 한다. 여기서는 결과를 옮기기만 한다.
    # can_cancel 이 된다고 했는데 cancel_order 가 거부하는 모순을 막으려면
    # 판단하는 곳이 한 곳이어야 한다.
    # ==================================================================

    def _order_summary(self, order):
        """주문 한 건을 모델에게 넘길 모양으로 줄인다."""
        return {
            "order_id": order["order_id"],
            "product_id": order["product_id"],
            "product_name": order["product_name"],
            "size": order["size"],
            "quantity": order["quantity"],
            "price": order["price"],
            "status": order["status"],
            "ordered_at": order["ordered_at"],
            "shipped_at": order["shipped_at"],
            "delivered_at": order["delivered_at"],
        }

    def _require_order(self, order_id):
        """주문을 찾는다. 없으면 (None, 실패결과).

        주문 ID 를 추측하지 못하게 막는 자리다.
        상품과 달리 주문은 이름으로 찾을 수 없으므로 search_order 를 안내한다.
        """
        order = self.store.get_order(order_id)
        if order is None:
            return None, fail(
                f"'{order_id}' 주문을 찾을 수 없습니다. 주문 ID 를 추측하지 마세요. "
                "search_order 로 먼저 주문을 찾아 order_id 를 확인하세요."
            )
        return order, None

    def _decision_result(self, order, decision, kind):
        """can_cancel / can_return 판정을 Tool 응답으로 옮긴다."""
        data = {
            "order_id": order["order_id"],
            "product_name": order["product_name"],
            "status": order["status"],
            "allowed": decision.allowed,
            "reason": decision.reason,
            "alternative": decision.alternative,
        }
        if kind == "return":
            deadline = self.store.return_deadline(order["order_id"])
            data["return_deadline"] = deadline

        message = decision.reason
        if decision.alternative:
            message += f" {decision.alternative}"
        # 판정 자체는 정상 동작이므로 success=True 로 돌려준다.
        # allowed=False 를 실패로 돌려주면 모델이 Tool 이 고장난 줄 알고 재시도한다.
        return ok(data, message)

    def search_order(self, keyword=None, status=None, ordered_within_days=None,
                     ordered_days_ago=None, delivered_within_days=None,
                     ordered_from=None, ordered_to=None):
        orders = self.store.search_orders(
            keyword=keyword, status=status,
            ordered_within_days=ordered_within_days,
            ordered_days_ago=ordered_days_ago,
            delivered_within_days=delivered_within_days,
            ordered_from=ordered_from, ordered_to=ordered_to,
        )
        if not orders:
            return fail(
                "조건에 맞는 주문이 없습니다. 날짜 조건을 넓히거나 keyword 를 빼고 "
                "다시 찾아보세요. 전체 주문을 보려면 인자 없이 호출하면 됩니다."
            )

        rows = [self._order_summary(order) for order in orders]
        detail = " / ".join(
            f"{row['order_id']} {row['product_name']}({row['status']})" for row in rows
        )
        # 여러 건이면 모델이 임의로 하나를 고르지 않게 못박는다.
        # 엉뚱한 주문을 취소하면 되돌릴 수 없다.
        if len(rows) > 1:
            message = (f"주문 {len(rows)}건을 찾았습니다: {detail}. "
                       "여러 건이므로 어느 주문인지 사용자에게 확인한 뒤 진행하세요.")
        else:
            message = f"주문 1건을 찾았습니다: {detail}"
        return ok({"total": len(rows), "orders": rows}, message)

    def get_order(self, order_id):
        order, error = self._require_order(order_id)
        if error:
            return error

        cancel = self.store.can_cancel(order_id)
        returnable = self.store.can_return(order_id)

        data = self._order_summary(order)
        data.update({
            "can_cancel": cancel.allowed,
            "cancel_reason": cancel.reason,
            "cancel_alternative": cancel.alternative,
            "can_return": returnable.allowed,
            "return_reason": returnable.reason,
            "return_deadline": self.store.return_deadline(order_id),
        })
        return ok(data, f"{order_id} {order['product_name']} 주문 상세 정보입니다. "
                        f"현재 상태는 '{order['status']}' 입니다.")

    def cancel_possible(self, order_id):
        order, error = self._require_order(order_id)
        if error:
            return error
        return self._decision_result(order, self.store.can_cancel(order_id), "cancel")

    def return_possible(self, order_id):
        order, error = self._require_order(order_id)
        if error:
            return error
        return self._decision_result(order, self.store.can_return(order_id), "return")

    def cancel_order(self, order_id, confirm=False):
        """주문을 취소한다. 실행 전에 확인을 받는다.

        remove_from_cart 와 같은 confirm 패턴이다. 다만 items 는 두지 않았다.
        "응" 한 번에 주문 여러 건이 취소되는 것은 장바구니와 위험도가 다르다.
        """
        order, error = self._require_order(order_id)
        if error:
            return error

        decision = self.store.can_cancel(order_id)
        if not decision.allowed:
            # 취소할 수 없는 주문은 확인 단계로 갈 이유가 없다.
            message = decision.reason
            if decision.alternative:
                message += f" {decision.alternative}"
            return fail(message)

        row = {
            "order_id": order["order_id"],
            "product_id": order["product_id"],
            "name": order["product_name"],
            "size": order["size"],
            "quantity": order["quantity"],
            "price": order["price"],
        }
        detail = (f"{order['order_id']} {order['product_name']} "
                  f"{order['size']} 사이즈 {order['quantity']}개 ({order['price']:,}원)")

        if not confirm:
            return ok({"requires_confirmation": True, "preview": [row]},
                      f"{detail} 주문을 취소할까요?")

        success, message = self.store.cancel_order(order_id)
        if not success:
            return fail(message)
        return ok(self._order_summary(self.store.get_order(order_id)), message)

    def return_order(self, order_id, reason=None, confirm=False):
        """반품을 신청한다. 접수까지만 하고 환불은 회수 후에 처리된다."""
        order, error = self._require_order(order_id)
        if error:
            return error

        decision = self.store.can_return(order_id)
        if not decision.allowed:
            message = decision.reason
            if decision.alternative:
                message += f" {decision.alternative}"
            return fail(message)

        row = {
            "order_id": order["order_id"],
            "product_id": order["product_id"],
            "name": order["product_name"],
            "size": order["size"],
            "quantity": order["quantity"],
            "price": order["price"],
        }
        detail = (f"{order['order_id']} {order['product_name']} "
                  f"{order['size']} 사이즈 {order['quantity']}개 ({order['price']:,}원)")

        if not confirm:
            because = f" 사유: {reason}." if reason else ""
            return ok({"requires_confirmation": True, "preview": [row]},
                      f"{detail} 반품을 신청할까요?{because}")

        success, message = self.store.return_order(order_id, reason)
        if not success:
            return fail(message)

        data = self._order_summary(self.store.get_order(order_id))
        data["refund_status"] = "회수 후 환불 예정"
        return ok(data, message)

    def _selection_from(self, items):
        """모델이 준 items 를 store 가 쓰는 선택 목록으로 바꾼다.

        여기서 이름을 ID 로 바꿔 둡니다. store 는 이름을 모르고,
        모델은 ID 를 모를 때가 많기 때문입니다.

        반환: (선택 목록 또는 None, 오류응답 또는 None)
        """
        if not items:
            return None, None

        selection = []
        for row in items:
            if not isinstance(row, dict):
                return None, fail("items 의 각 항목은 객체여야 합니다.")
            product, error = self._resolve_product(row.get("product_id"),
                                                   row.get("product_name"))
            if error:
                return None, error
            picked = {"product_id": product["id"]}
            if row.get("size") is not None:
                picked["size"] = row["size"]
            if row.get("quantity") is not None:
                picked["quantity"] = row["quantity"]
            selection.append(picked)
        return selection, None

    def buy_from_cart(self, items=None, confirm=False):
        """장바구니를 주문으로 전환한다(결제). 실행 전에 확인을 받는다.

        items 를 주면 그 항목만 주문하고 나머지는 장바구니에 남깁니다.
        전에는 일부만 사려면 나머지를 먼저 빼야 했는데, 그건 사용자가
        요청하지 않은 상태 변경이라 승인 대상이 하나 늘어납니다.
        """
        selection, error = self._selection_from(items)
        if error:
            return error

        rows, problem = self.store.preview_checkout(selection)
        if problem:
            return fail(problem)

        detail = ", ".join(
            f"{row['name']} {row['size']} 사이즈 {row['quantity']}개" for row in rows
        )
        total = sum(row["price"] for row in rows)

        if not confirm:
            scope = "장바구니에서 " if selection else ""
            return ok({"requires_confirmation": True, "preview": rows, "total": total},
                      f"{scope}{detail}를 주문합니다. "
                      f"결제 금액은 {total:,}원입니다. 진행할까요?")

        success, message, created = self.store.checkout(selection)
        if not success:
            return fail(message)
        return ok({"orders": [self._order_summary(order) for order in created],
                   "total": total},
                  f"{message} 주문 번호: "
                  + ", ".join(order["order_id"] for order in created))


    def call(self, name, arguments):
        """이름과 인자 dict 로 Tool 을 실행한다.

        모델이 돌려준 tool_call 을 그대로 넘기면 되도록 만든 진입점입니다.
        모델은 없는 Tool 이름이나 이상한 인자를 만들어낼 수 있으므로
        여기서 방어해야 합니다. 예외가 그대로 터지면 대화가 끊깁니다.
        """
        # 1) 등록된 Tool 인지, 인자가 스키마에 맞는지 먼저 검사합니다.
        #    통과하지 못하면 메서드를 부르지 않으므로 Store 는 그대로입니다.
        cleaned, error = validate_call(name, arguments)
        if error:
            return fail(error)

        # 2) getattr 로 아무 메서드나 부르지 않습니다.
        #    전에는 TOOLS 에 없는 call 이나 view_cart 외 공개 메서드도 불렸습니다.
        method = getattr(self, name, None)
        if method is None or not callable(method):
            return fail(f"'{name}' 은 아직 구현되지 않았습니다. "
                        f"사용 가능한 Tool: {', '.join(TOOL_INDEX)}")

        try:
            return method(**cleaned)
        except NotImplementedError:
            return fail(f"'{name}' 은 아직 구현되지 않았습니다.")
        except TypeError as error:
            # 모델이 인자 이름을 틀렸거나 필수 인자를 빠뜨린 경우
            return fail(f"'{name}' 호출 인자가 잘못되었습니다: {error}")
        except Exception as error:
            return fail(f"'{name}' 실행 중 오류가 발생했습니다: {type(error).__name__}: {error}")


# ======================================================================
# 터미널에서 바로 확인
#
#     python3 tools.py
#
# 모델도 Streamlit 도 없이 Tool 만 따로 테스트할 수 있습니다.
# 에이전트를 붙이기 전에 여기서 먼저 통과시키세요. 그래야 나중에 문제가 생겼을 때
# "모델이 Tool 을 잘못 골랐나, Tool 자체가 버그인가"를 헷갈리지 않습니다.
# ======================================================================

if __name__ == "__main__":
    import json

    toolbox = Toolbox(Store())

    scenarios = [
        ("search_product", {"category": "운동화", "max_price": 150000, "color": "검은색", "size": 270}),
        ("get_info", {"product_id": "P001"}),
        ("comparing_info", {"product_ids": ["P001", "P002", "P003"]}),
        ("add_to_cart", {"product_id": "P001", "size": 270, "quantity": 1}),
        ("view_cart", {}),
        ("remove_from_cart", {"product_id": "P001", "size": 270, "quantity": 1}),
        ("remove_from_cart", {"product_id": "P001", "size": 270}),
        ("없는툴", {}),  # 방어 로직 확인
    ]

    for name, arguments in scenarios:
        print("=" * 60)
        print(f"{name}({json.dumps(arguments, ensure_ascii=False)})")
        result = toolbox.call(name, arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        print()
