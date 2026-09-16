"""에이전트 계층 — 모델을 부르고, Tool 을 대신 실행하고, 다시 모델을 부른다.

중요한 사실 하나부터.

    모델은 Tool 을 실행하지 않는다.

젬마는 글자만 만드는 프로그램이다. 우리 파일을 열지도, store.py 를 부르지도 못한다.
실제로 일어나는 일은 이렇다.

    1. 이 파일이 모델에게 "대화 내용 + 쓸 수 있는 Tool 목록" 을 보낸다
    2. 모델이 "search_product 를 이런 인자로 불러주세요" 라고 글로 답한다  ← 실행 아님
    3. 이 파일이 그 글을 읽고 대신 실행한다                                ← 여기서만 실행됨
    4. 결과를 대화에 덧붙여 다시 모델에게 보낸다
    5. 모델이 결과를 보고 최종 답변을 쓰거나, 또 다른 Tool 을 요청한다

즉 "모델이 도구를 쓴다" 는 말은 비유이고, 실제로 도구를 쥐고 있는 건 이 파일이다.
run() 안의 for 루프가 그 전부다.
"""

import json
import re
import time
from typing import NamedTuple

import requests

import agency
import config
import plan as planning
from tools import TOOLS, TOOL_INDEX, Toolbox


# ======================================================================
# 시스템 프롬프트
#
# 초안입니다. 실제로 돌려보면서 계속 고치게 됩니다 - 그게 정상입니다.
# 프롬프트 수정도 커밋으로 남기면 발표 때 개선 과정을 보여줄 수 있습니다.
# ======================================================================

SYSTEM_PROMPT = """당신은 한국어 온라인 쇼핑몰의 상담 도우미입니다.

역할:
- 사용자의 요청을 이해하고 필요한 Tool 을 스스로 골라 사용합니다.
- 상품 정보, 재고, 주문 상태는 반드시 Tool 로 확인합니다. 절대 추측하지 않습니다.
- Tool 이 실패하면 실패 사유를 사용자에게 설명하고 대안을 제시합니다.

지켜야 할 것:
1. 상품을 추천하기 전에 반드시 search_product 로 실제 상품을 찾습니다.
   상품명이나 가격을 지어내지 마세요.
   search_product 인자는 다음처럼 분리합니다.
   - 운동화·셔츠·코트처럼 category 목록에 있는 품목을 사용자가 직접 말하면
     반드시 category 에 넣고 group 은 비웁니다. 예: "운동화"는
     category="운동화"이지 group="신발"이 아닙니다.
     같은 방식으로 바지·청바지·슬랙스는 category="팬츠"이고,
     치마는 category="스커트"입니다. 이때 group="하의"를 쓰지 않습니다.
   - 색상·성별·가격·사이즈·소재는 각각의 전용 인자에 넣습니다.
   - 사용자가 특정 상품명을 직접 말하면 product_name 에 넣습니다.
     상품명 검색을 semantic_query 로 대신하지 마세요.
   - 나머지 용도·기능·착용감만 semantic_query 에 짧은 자연어로 넣습니다.
     정확한 필터 조건만 있고 의미 요구가 없으면 semantic_query 는 생략합니다.
   - sort=rating은 사용자가 "평점" 또는 "별점"을 직접 말했을 때만 씁니다.
     sort=review도 사용자가 "리뷰"를 직접 말했을 때만 씁니다.
     "편한 순", "가벼운 순", "잘 어울리는 순", "추천순"을 rating으로 바꾸지 마세요.
     이런 속성은 semantic_query에 넣고 sort는 생략합니다.
   - search_product가 성공했지만 shown=0이면 의미 유사도 기준을 통과한 상품이
     없다는 뜻입니다. semantic_query나 사용자가 말한 필터를 임의로 제거해 다시
     검색하지 마세요. 결과가 없다고 알리고, 조건을 완화할지 사용자에게 물으세요.
2. 재고나 세탁 방법을 묻는 질문은 get_info 로 확인한 뒤 답합니다.
3. 여러 상품 중 고를 때는 comparing_info 로 비교 정보를 받고,
   어떤 것이 사용자 조건에 맞는지는 당신이 판단해 이유와 함께 설명합니다.
4. Tool 결과에 "success": false 가 오면 실패한 것입니다. message 를 읽고
   조건을 고쳐서 다시 시도하거나, 사용자에게 되물으세요. 같은 호출을 반복하지 마세요.
5. 상품 ID(P001 같은 값)를 절대 추측하지 마세요. 이것이 가장 흔한 실수입니다.
   - ID 를 모르면 product_name 에 상품명을 넣으세요.
     get_info, add_to_cart, remove_from_cart 는 이름을 받습니다.
     comparing_info 만 product_ids 가 필요하니, 그때는 search_product 로 먼저 찾으세요.
   - 대화에 나온 적 없는 ID 를 만들어내면 엉뚱한 상품이 처리됩니다.
6. Tool 이 돌려준 메시지의 구체적인 값(상품명, 사이즈, 수량)을 기억하세요.
   사용자가 "방금 뺀 것 다시 담아줘" 라고 하면 그 메시지를 보고 그대로 담으면 됩니다.
   이미 알 수 있는 것을 사용자에게 다시 묻지 마세요.
   Tool 결과 원문은 다음 턴에 남지 않고 당신이 쓴 답변만 남습니다.
   그러므로 답변에 상품명·사이즈·수량을 구체적으로 적어 두세요.
   "1개를 뺐습니다" 가 아니라 "크롭 슬림 티 66 사이즈 1개를 뺐습니다" 로 씁니다.
   단, 장바구니나 주문의 "현재 내용" 은 예외입니다. 사용자가 화면에서 직접
   담거나 뺄 수 있으므로 이전 대화의 목록은 이미 낡았을 수 있습니다.
   대신 매 요청 앞에 붙는 [현재 상태] 를 쓰세요. 그것이 지금 값입니다.
   장바구니는 거기 전부 실려 있으니 view_cart 를 또 부르지 마세요.
   주문은 최근 몇 건만 실려 있으므로, 주문을 세거나 목록으로 답할 때는
   반드시 search_order 로 조회하세요. 상태 블록에 보이는 것만 보고 답하면
   나머지를 빠뜨립니다.
7. 되돌릴 수 없는 작업(장바구니 삭제, 결제, 주문 취소, 반품 신청)의 확인 절차.

   **당신의 역할은 미리보기를 만드는 것까지입니다. 실행은 앱이 합니다.**
   대상이 정해졌으면 곧바로 Tool 을 호출하세요. confirm 없이 부르면
   "...할까요?" 라는 확인 문장이 돌아옵니다. 그 문장을 사용자에게 그대로 전하세요.
   그러면 앱이 화면에 승인 버튼을 띄우고, 사용자가 누르면 앱이 실행합니다.
   **확인 질문을 당신이 새로 만들지 마세요. Tool 이 만들어 줍니다.**

   - 당신이 먼저 "정말 하시겠어요?" 라고 묻고, 사용자가 답한 뒤에 Tool 을 부르면
     확인이 두 번 일어납니다. 사용자가 같은 답을 두 번 하게 됩니다.
   - 실행 직전에 cancel_possible / return_possible 을 부를 필요도 없습니다.
     cancel_order / return_order 가 가능 여부를 스스로 다시 확인하고,
     불가능하면 이유와 대안을 돌려줍니다.
     *_possible 은 사용자가 "취소되나요?" 처럼 실행 없이 물어볼 때만 쓰세요.
   - 확인 문장을 전했으면 **당신이 할 일은 거기까지입니다.**
     실행은 사용자가 화면의 승인 버튼을 눌러야 일어납니다.
     사용자가 말로 "네" 라고 답하더라도 같은 Tool 을 다시 부르지 말고,
     버튼을 눌러 달라고 안내하세요.
   - 이 규칙은 앱이 강제합니다. confirm=true 를 붙여도 실행되지 않습니다.
     되돌릴 수 없는 작업의 승인은 말이 아니라 버튼으로만 받습니다.
     대상이나 수량이 바뀌면 새 확인 문장이 다시 나옵니다.

   단, **대상이 아직 정해지지 않았으면 먼저 물어야 합니다.**
   "취소해줘" 인데 후보 주문이 여러 건이면 어느 것인지 확인하세요.
   이건 실행 동의를 구하는 것이 아니라 대상을 좁히는 것이라 별개입니다.
8. "[현재 상태]" 의 "직전 검색 결과" 는 실제 결과의 순서와 ID 입니다.
   사용자가 "두 번째 거", "아까 비교한 것" 이라고 하면 그 목록에서 찾으세요.
   기억에 의존해 순서를 짐작하지 마세요.
9. 주문을 다룰 때 지킬 것.
   - 주문 ID(ORD-1001 같은 값)를 추측하지 마세요. search_order 로 먼저 찾습니다.
   - "어제 주문한" 은 ordered_days_ago=1, "지난주에 받은" 은 delivered_within_days=7 입니다.
     주문일과 수령일은 다릅니다.
   - 찾은 주문이 여러 건이면 임의로 고르지 말고 어느 것인지 사용자에게 물으세요.
   - 취소가 불가능하면 이유와 함께 Tool 이 알려준 대안을 그대로 안내하세요.
   - 반품은 "접수" 까지입니다. "환불되었습니다" 가 아니라
     "반품이 접수되었고 회수 후 환불됩니다" 라고 알려주세요.
10. 장바구니를 바꾼 뒤(담기·빼기)에는 답변 끝에 현재 장바구니를 알려주세요.
   Tool 이 돌려준 "현재 장바구니: ..." 문장에 이미 들어 있으니 그대로 옮기면 됩니다.
   무엇이 담겨 있는지, 총 몇 개이고 합계가 얼마인지까지 적습니다.

답변은 간결한 한국어로 합니다. Tool 이름이나 상품 ID 같은 내부 값은
사용자에게 그대로 노출하지 말고 상품명으로 바꿔서 말하세요.

검색 인자 최종 확인: 사용자가 운동화·셔츠·코트처럼 category 목록의 품목을
직접 말했는데 search_product의 category를 빼면 안 됩니다. semantic_query는
용도·기능·착용감만 담당하며 category를 대신하지 않습니다.

search_product가 성공하면 상품 목록은 화면의 검색 결과 그리드에 따로 표시됩니다.
따라서 채팅 답변에서 상품을 하나씩 다시 나열하거나 임의로 추천 이유를 만들지 말고,
조건에 맞는 검색 결과를 표시했다는 사실과 결과 개수만 간단히 안내하세요.
사용자가 비교를 명시적으로 요청한 경우에만 comparing_info로 비교하세요.
"""


class ToolCall:
    """모델이 요청한 Tool 호출 하나.

    서버가 어떤 형식으로 보내오든 이 모양으로 바꿔두고, 루프는 이것만 다룬다.

    id        : 결과를 돌려줄 때 짝을 맞추는 식별자
    name      : 부를 Tool 이름 (예: "search_product")
    arguments : 인자 dict (예: {"color": "검은색", "max_price": 150000})
    source    : "native" | "text"  - 결과 메시지를 만들 때 형식이 달라진다
    error     : 인자를 읽지 못한 경우의 사유. 있으면 실행하지 않고 모델에게 돌려준다
    """

    def __init__(self, call_id, name, arguments, source="native", error=None):
        self.id = call_id
        self.name = name
        self.arguments = arguments
        self.source = source
        # 인자 JSON 이 깨져 있으면 여기에 사유가 담긴다. 이 호출은 실행하지 않는다.
        self.error = error

    def __repr__(self):
        return f"ToolCall({self.name}, {self.arguments})"


# ======================================================================
# 1단계 - 모델 호출
# ======================================================================

def call_model(messages, tools=None):
    """로컬 모델 서버에 요청하고 assistant 메시지를 그대로 반환한다.

    반환 형태:
        {"role": "assistant", "content": "...", "tool_calls": [...]}

    tools 를 함께 보내면 모델이 그 목록 중에서 골라 호출을 요청할 수 있다.
    보내지 않으면 그냥 대화만 한다.
    """
    headers = {"Content-Type": "application/json"}
    if config.LOCAL_API_KEY:
        headers["Authorization"] = f"Bearer {config.LOCAL_API_KEY}"

    payload = {
        "model": config.MODEL_NAME,
        "messages": messages,
        "temperature": config.TEMPERATURE,
        "max_tokens": config.MAX_TOKENS,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        only_name = ((tools[0].get("function") or {}).get("name")
                     if len(tools) == 1 else None)
        payload["tool_choice"] = "required" if only_name == "verify_outcome" else "auto"

    # 서버가 요구하는 표준 외 파라미터를 합칩니다 (.env 의 EXTRA_BODY).
    payload.update(config.EXTRA_BODY)

    response = requests.post(
        f"{config.LOCAL_API_BASE_URL.rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
        timeout=config.REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    # 실험·계측용. 서버가 usage 를 주면 마지막 호출의 토큰 수를 남긴다 (동작에는 영향 없음).
    global LAST_USAGE
    LAST_USAGE = data.get("usage") or {}
    return data["choices"][0]["message"]


LAST_USAGE = {}


# ======================================================================
# 2단계 - 모델 응답에서 Tool 호출 뽑아내기
#
# 서버 형식 차이를 흡수하는 곳. 루프는 이 함수가 무엇을 하는지 몰라도 된다.
# ======================================================================

def parse_tool_calls(message, allow_text=False):
    """모델 응답에서 Tool 호출을 뽑는다. 없으면 빈 리스트.

    기본 경로는 OpenAI 표준인 tool_calls 필드다.

    allow_text=True 일 때만 본문 텍스트도 뒤진다. 기본은 False 다.
    본문을 항상 뒤지면 모델이 "이렇게 부르면 됩니다" 라고 설명으로 적은 JSON 까지
    실행된다. 실행 요청과 설명을 구분할 방법이 없기 때문에 기본값을 껐다.
    """
    calls = []

    # --- 표준 경로: tool_calls 필드 ---
    for index, item in enumerate(message.get("tool_calls") or []):
        function = item.get("function", {})
        name = function.get("name", "")
        arguments = function.get("arguments")

        # arguments 는 dict 가 아니라 JSON "문자열" 로 온다. 반드시 풀어야 한다.
        #
        # 깨진 JSON 을 빈 dict 로 바꾸면 안 된다. 인자가 사라진 채로 실행되기 때문이다.
        # search_product 라면 조건 없는 전체 검색이 되고, 수량이 빠진 채 담길 수도 있다.
        # 실행하지 않고 "이래서 못 읽었다" 를 모델에게 돌려주면 스스로 고쳐 보낸다.
        error = None
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as problem:
                error = (f"인자 JSON 을 읽지 못했습니다: {problem.msg}. "
                         f"받은 값: {arguments[:120]}")
                arguments = {}

        if error is None and not isinstance(arguments, dict):
            error = f"인자는 객체여야 합니다. 받은 값: {arguments!r}"
            arguments = {}

        calls.append(ToolCall(
            item.get("id") or f"call_{index}",
            name,
            arguments,
            source="native",
            error=error,
        ))

    if calls:
        return calls

    # --- 대비책: 본문에 JSON 으로 적어 보내는 서버용 ---
    #
    # 기본은 꺼져 있습니다. 켜져 있으면 모델이 설명으로 적은 JSON 도 실행됩니다.
    # 실제로 "실행하지 말고 호출 예시만 알려줘" 에 대한 답변 안의
    # add_to_cart JSON 이 그대로 실행됐습니다.
    #
    # 지금 쓰는 서버는 tool_calls 를 지원하므로 이 경로가 필요 없습니다.
    # 지원하지 않는 서버로 옮길 때 .env 에서 ALLOW_TEXT_TOOL_CALLS=1 로 켭니다.
    if not allow_text:
        return []

    return _parse_tool_calls_from_text(message.get("content") or "")


def _iter_json_objects(text):
    """텍스트에서 최상위 JSON 객체를 순서대로 뽑아낸다.

    정규식으로는 못 한다. {"args": {"a": 1}} 처럼 중괄호가 중첩되면
    정규식이 어디서 끝나는지 알 수 없기 때문이다.
    그래서 문자를 하나씩 보며 중괄호 짝을 세는 방식을 쓴다.

    문자열 안의 중괄호(예: "{웃음}")를 세지 않도록 따옴표 상태도 추적한다.
    """
    depth = 0
    start = None
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    yield text[start:index + 1]
                    start = None


def _parse_tool_calls_from_text(content):
    """<tool_call> 태그 안의 JSON 만 Tool 호출로 읽는다.

    tool_calls 필드를 지원하지 않는 서버를 위한 대비책이다.
    태그로 감싼 것만 인정하는 이유는 설명문에 섞인 JSON 과 구분하기 위해서다.
    ```json ... ``` 코드펜스는 태그 안에 함께 나오는 경우가 있어 벗겨낸다.

    주의: 지금 서버(Gemma 4)는 이 형식을 쓰지 않는다 (2026-09-11 확인).
    Gemma 4 의 원문 툴 호출은 <|tool_call>call:name{key:<|"|>값<|"|>}<tool_call|> 로
    JSON 이 아니며, 서버의 --tool-call-parser gemma4 가 그걸 tool_calls 필드로 바꿔 준다.
    즉 진짜 의존점은 서버 파서다. 파서 없는 서버로 옮기면 이 함수도 그 서버 형식에 맞춰
    다시 써야 한다. 켜서 쓰기 전에 그 서버의 응답을 먼저 눈으로 보고 형식을 맞춰야 한다.
    """
    if not content:
        return []

    # 본문 아무 데나 있는 JSON 을 줍지 않습니다.
    # <tool_call>{"name": ..., "arguments": {...}}</tool_call> 로 감싼 것만 인정합니다.
    # 이 형식이 아니면 설명문에 섞인 JSON 과 구분할 수 없습니다.
    blocks = re.findall(r"<tool_call>(.*?)</tool_call>", content, re.S)
    if not blocks:
        return []

    text = re.sub(r"```(?:json)?", "", "\n".join(blocks))
    calls = []

    for index, chunk in enumerate(_iter_json_objects(text)):
        try:
            payload = json.loads(chunk)
        except json.JSONDecodeError:
            continue

        if not isinstance(payload, dict):
            continue

        name = payload.get("tool") or payload.get("name")
        arguments = payload.get("args") or payload.get("arguments") or {}
        if name and isinstance(arguments, dict):
            calls.append(ToolCall(f"text_{index}", name, arguments, source="text"))

    return calls


# ======================================================================
# 3단계 - 실행 결과를 대화에 덧붙일 메시지로 만들기
# ======================================================================

def make_tool_result_message(tool_call, result):
    """Tool 실행 결과를 모델에게 돌려줄 메시지로 만든다.

    ensure_ascii=False 가 중요하다. 빼면 한글이 \\uXXXX 로 나가서
    토큰을 몇 배로 잡아먹는다.
    """
    model_result = result
    # 검색 화면에는 최대 50개를 보내지만, 같은 50개 상세 요약을 모델 문맥에
    # 다시 넣을 필요는 없다. 트레이스의 원본 결과는 그대로 두고 모델에게만
    # 상위 10개를 알려준다. 화면은 server.py가 트레이스에서 전체를 꺼낸다.
    if tool_call.name == "search_product" and result.get("success"):
        data = result.get("data") or {}
        products = data.get("products") if isinstance(data, dict) else None
        if isinstance(products, list) and len(products) > 10:
            model_result = dict(result)
            model_result["data"] = {
                **data,
                "products": products[:10],
                "products_sent_to_model": 10,
                "displayed_in_search_grid": len(products),
            }
    content = json.dumps(model_result, ensure_ascii=False, default=str)

    if tool_call.source == "native":
        # tool_call_id 로 "어느 요청에 대한 답인지" 짝을 맞춰준다.
        return {
            "role": "tool",
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "content": content,
        }

    # role="tool" 을 모르는 서버용. 사용자 메시지로 위장해서 전달한다.
    return {
        "role": "user",
        "content": f"[{tool_call.name} 실행 결과]\n{content}",
    }


def _search_grid_reply(trace):
    """검색만 수행한 턴의 표시 개수는 모델 문장이 아니라 앱이 확정한다.

    모델이 qualified(임계값 통과 전체)를 shown(화면 전달 수)로 잘못 읽어
    화면에는 50개인데 채팅에는 114개라고 말하는 일을 막는다.
    """
    used = [entry for entry in (trace or [])
            if entry.get("tool") and not entry.get("internal")]
    if not used or any(entry.get("tool") != "search_product" for entry in used):
        return None
    for entry in reversed(used):
        result = entry.get("result") or {}
        if not result.get("success"):
            continue
        data = result.get("data") or {}
        shown = data.get("shown")
        if isinstance(shown, int):
            if shown == 0:
                return "조건에 맞는 상품을 검색했지만 표시할 결과가 없습니다."
            return f"조건에 맞는 상품 {shown}개를 검색 결과 화면에 표시했습니다."
    return None


def _assistant_message(message, calls):
    """모델이 무엇을 요청했는지를 대화에 남기기 위한 메시지.

    이걸 빼먹으면 다음 턴에서 모델이 자기가 뭘 요청했는지 모른다.
    또 native 형식은 tool 결과 앞에 반드시 tool_calls 를 가진 assistant 메시지가
    있어야 하므로, 없으면 서버가 400 을 돌려준다.
    """
    if calls and calls[0].source == "native":
        return {
            "role": "assistant",
            # content 가 None 이면 거부하는 서버가 있어 빈 문자열로 바꾼다
            "content": message.get("content") or "",
            # function.arguments 는 서버가 준 JSON "문자열" 그대로 되돌린다.
            # dict 로 풀어 보내면 지금 서버는 400 을 돌려준다 (2026-09-11 probe 2c).
            "tool_calls": message.get("tool_calls", []),
        }

    return {"role": "assistant", "content": message.get("content") or ""}


# ======================================================================
# 4단계 - 에이전트 루프
# ======================================================================

# ======================================================================
# 사용자 승인이 필요한 작업
#
# 되돌릴 수 없는 작업은 모델이 confirm=True 를 보냈다는 이유만으로 실행하면
# 안 된다. 실제로 "미리보기만 보여줘, 아직 지우지 마" 라는 요청에도
# 모델이 confirm=True 를 보내 장바구니가 비워졌다.
#
# 그래서 승인을 프롬프트가 아니라 코드로 관리한다.
#
#   1턴  모델이 remove_from_cart 를 부름 (confirm 이 무엇이든)
#        -> 에이전트가 미리보기만 실행하고 대기 상태에 기록. 루프 중단.
#   2턴  사용자가 무언가 답한 뒤 모델이 같은 인자로 confirm=True 를 보냄
#        -> 대기 기록과 대조해서 일치하면 실행. 기록은 즉시 소멸.
#
# 핵심은 "사용자가 미리보기를 본 뒤 새 메시지를 보냈다" 는 사실이다.
# 모델은 한 턴 안에서 이 조건을 스스로 만들어낼 수 없다.
#
# 결제·주문 취소·반품도 같은 구조를 쓰면 되므로 CONFIRM_REQUIRED 에 이름만 넣는다.
# ======================================================================

CONFIRM_REQUIRED = {"remove_from_cart", "cancel_order", "return_order", "buy_from_cart"}

# 실행되면 되돌릴 수 없는(상태를 바꾸는) Tool.
# 도중에 오류가 나도 이것들이 성공했으면 사용자에게 알려야 한다.
# 상태를 바꾸지 않는 조회 Tool. 확인 대기가 열릴 때 이 결과는 먼저 답으로 내보낸다.
INFORMATIONAL_TOOLS = {"get_info", "comparing_info", "get_order",
                       "cancel_possible", "return_possible", "view_cart"}

STATE_CHANGING = {"add_to_cart", "remove_from_cart",
                  "cancel_order", "return_order", "buy_from_cart"}


# 거절 안내에서 "무엇을" 거절했는지 말하기 위한 이름표.
ACTION_NAME = {
    "remove_from_cart": "장바구니 빼기",
    "cancel_order": "주문 취소",
    "return_order": "반품 신청",
    "buy_from_cart": "주문",
}


class PendingAction:
    """사용자 확인을 기다리는 작업들.

    keys 가 하나가 아니라 **목록**인 이유가 있다.

    "두 주문 모두 취소해줘" 처럼 대상이 여럿이면, 확인은 한 번에 받되
    실행은 Tool 이 한 건씩만 할 수 있다. 열쇠를 합집합 하나로 만들면
    모델이 만들 수 있는 한 칸짜리 열쇠와 영영 맞지 않아 교착에 빠진다.

    그래서 목록으로 들고 있다가, 들어오는 호출이 **그중 하나와 맞으면**
    통과시키고 그 항목을 뺀다. 사용자가 승인한 범위 안에서 실제로 요청한
    것만 처리되므로 의미도 맞다.
    """

    def __init__(self, tool, items, summary, turn, created_at=None):
        self.tool = tool

        # [{"key": ..., "label": ..., "arguments": {...}}]
        #
        # 인자를 함께 들고 있는 이유가 있다. 화면 버튼으로 승인하면 모델이
        # 다시 호출해 주지 않으므로, 앱이 그 자리에서 실행할 수 있어야 한다.
        # 열쇠만 들고 있던 때는 "승인은 받았는데 무엇을 실행할지 모르는" 상태가 됐다.
        self.items = list(items)

        self.summary = summary   # 사용자에게 보여준 문장
        self.turn = turn         # 이 기록이 만들어진 턴 번호
        # 만들어진 시각. 턴은 사용자가 말을 해야 넘어가는데, 화면 버튼은 말 없이
        # 한참 뒤에 눌릴 수 있다. 그래서 시간으로도 만료시킨다 (PENDING_TTL_SECONDS).
        self.created_at = time.time() if created_at is None else created_at

    def seconds_left(self, now=None):
        """만료까지 남은 초. 0 이하면 만료."""
        now = time.time() if now is None else now
        return config.PENDING_TTL_SECONDS - (now - self.created_at)

    def expired(self, now=None):
        return self.seconds_left(now) <= 0

    def to_dict(self):
        return {"tool": self.tool, "items": self.items, "summary": self.summary,
                "turn": self.turn, "created_at": self.created_at}

    @classmethod
    def from_dict(cls, data):
        return cls(data["tool"], data.get("items") or [], data.get("summary", ""),
                   data.get("turn", 0), data.get("created_at"))

    @property
    def keys(self):
        return [item["key"] for item in self.items]

    @property
    def key(self):
        """테스트와 로그용. 목록 전체를 한 문자열로 본다."""
        return json.dumps(self.keys, ensure_ascii=False)

    def find(self, key):
        for item in self.items:
            if item["key"] == key:
                return item
        return None

    def take(self, key):
        """목록에 있으면 빼고 True. 없으면 False."""
        for index, item in enumerate(self.items):
            if item["key"] == key:
                self.items.pop(index)
                return True
        return False

    def empty(self):
        return not self.items


def _action_key(preview_result):
    """미리보기 결과로 "무엇을 얼마나 처리할 것인가" 를 나타내는 키를 만든다.

    인자 문자열을 그대로 쓰면 안 된다. 모델이 같은 대상을 가리키면서도
    표현을 바꾸기 때문이다.

        1턴  {"product_name": "클라우드 워크 2", "size": 230, "quantity": 2}
        2턴  {"product_id": "P002",            "size": 230, "quantity": 2}

    사람이 보기엔 같은 요청인데 문자열로는 다르다. 그러면 승인이 영영 성립하지
    않아 미리보기만 무한히 반복된다 (실제로 재현됐다).

    그래서 미리보기가 계산해 준 실제 대상(product_id, size, quantity)으로 키를
    만든다. preview_removal 은 상태를 바꾸지 않으므로 몇 번을 불러도 안전하다.
    """
    rows = ((preview_result or {}).get("data") or {}).get("preview") or []
    return _key_from_rows(rows)


# 한 번의 확인에 묶을 수 있는 최대 건수.
# 확인 문장이 길어질수록 사용자가 읽지 않고 승인한다.
MAX_CONFIRM_AT_ONCE = 5

# 승인 대기가 유효한 턴 수. 지나면 버리고 처음부터 다시 확인받는다.
PENDING_TTL_TURNS = 2

# 계획이 이 턴 수보다 오래 안 끝나면 버린다. 승인 대기(approve 도 한 턴)로 끊겨
# 이어지는 경우를 살리되, 사용자가 다른 이야기로 넘어간 뒤까지 붙잡지 않기 위한 값.
PLAN_TTL_TURNS = 8


def _target_ids(rows):
    """미리보기 줄들이 가리키는 대상 식별자 집합."""
    return {(row.get("order_id"), row.get("product_id"), row.get("size"))
            for row in rows}


def _overlaps(rows, preview_result):
    """이미 묶인 대상과 새 미리보기의 대상이 겹치는지."""
    new_rows = ((preview_result or {}).get("data") or {}).get("preview") or []
    return bool(_target_ids(rows) & _target_ids(new_rows))


def _rows_amount(rows):
    """미리보기 줄들의 금액 합계. 금액이 없는 Tool 이면 None.

    열쇠(_key_from_rows)에는 금액이 들어가지 않는다.
    같은 상품 같은 수량인데 가격만 바뀐 경우를 열쇠로는 잡을 수 없어서,
    승인 시점에 본 금액을 따로 들고 비교한다.
    """
    total = 0
    found = False
    for row in rows:
        price = row.get("price")
        if price is None:
            continue
        found = True
        try:
            total += int(price)
        except (TypeError, ValueError):
            return None
    return total if found else None


def _snapshot(preview_result):
    """미리보기 결과에서 "무엇을 얼마에 처리할 것인가" 를 뽑는다.

    승인 버튼을 누르는 시점에 이걸 다시 계산해서 비교한다.
    사용자가 본 화면과 실제로 실행될 내용이 같은지 확인하는 유일한 방법이다.
    """
    rows = ((preview_result or {}).get("data") or {}).get("preview") or []
    return {"key": _key_from_rows(rows),
            "amount": _rows_amount(rows),
            "rows": list(rows)}


def _key_from_rows(rows):
    """미리보기 줄 목록을 키 문자열로 만든다. 순서는 무시한다."""
    # order_id 를 함께 넣는다. 주문 취소·반품은 product_id 만으로 구분되지 않는다.
    # (같은 상품을 두 번 주문했으면 두 주문의 키가 같아진다)
    canonical = sorted(
        (row.get("order_id"), row.get("product_id"), row.get("size"), row.get("quantity"))
        for row in rows
    )
    return json.dumps(canonical, ensure_ascii=False, default=str)


class ShoppingAgent:
    """대화 하나를 담당하는 에이전트.

        store = Store()
        agent = ShoppingAgent(store)
        answer, trace = agent.run("15만원 이하 검은색 운동화 찾아줘")

    store 를 인자로 받는 이유: Streamlit 세션마다 Store 가 따로이므로
    같은 창고를 agent 와 toolbox 가 함께 바라보게 해야 한다.
    """

    def __init__(self, store, system_prompt=SYSTEM_PROMPT, approve_by_button=None,
                 adaptive_mode=None):
        self.store = store
        self.toolbox = Toolbox(store)
        self.system_prompt = system_prompt

        # 되돌릴 수 없는 작업의 승인을 화면 버튼으로만 받을지 여부.
        #
        # 켜면 모델이 confirm=true 를 아무리 붙여도 실행되지 않는다.
        # 사용자가 무엇에 동의했는지가 자연어 해석이 아니라 **누른 열쇠**가 된다.
        # 끄면 예전처럼 모델이 사용자의 말을 읽고 승인 여부를 판단한다
        # (그 경로에 어떤 문제가 있었는지는 NOTES 18번 참고).
        self.approve_by_button = (config.APPROVE_BY_BUTTON
                                  if approve_by_button is None else approve_by_button)
        self.adaptive_mode = (config.ADAPTIVE_AGENT_MODE
                              if adaptive_mode is None else bool(adaptive_mode))

        # 턴 번호. 사용자 요청 하나가 한 턴이다.
        # 승인이 "이전 턴에 만들어졌는지" 를 보려면 시간 개념이 필요하다.
        self.turn = 0

        # 사용자 확인을 기다리는 작업. 한 번 쓰면 지운다.
        self.pending = None

        # 확인 대기가 하나 잡혀서 미뤄 둔 작업. {"items": [...], "turn": n}
        # 사용자가 한 문장에 두 가지를 부탁했을 때 뒤의 것을 잃지 않기 위한 것이다.
        self.postponed = None

        # 직전 검색·비교 결과. "두 번째 상품" 같은 말을 실제 ID 로 잇기 위한 것.
        # 대화 내역에는 Tool 결과가 남지 않으므로 여기에 따로 들고 있는다.
        # 매번 덮어쓰므로 무한정 쌓이지 않는다.
        self.last_results = None

        # 직전 검색의 인자와 결과를 함께 보관한다. continue_plan은 승인 뒤 같은 요청의
        # 남은 단계를 잇고, 이 값은 다음 사용자 턴의 "그중", "더 저렴한"을 잇는다.
        self.last_search = None

        # 마지막 실제 사용자 요청. 승인 버튼으로 턴이 끊긴 뒤 "원래 부탁 중 남은 것" 을
        # 이어가기 위해 든다. 앱이 만든 이어가기 문장(RESUME_MESSAGE)은 여기 넣지 않는다.
        self.last_request = None

        # 사용자가 명시적으로 장기 기억을 요청한 선호만 저장한다.
        self.preferences = agency.PreferenceMemory()

        # 계획 모드(config.PLAN_MODE)에서 모델이 make_plan 으로 세운 단계와 진행 상황.
        # 상태 블록에 매 턴 다시 보여 주고, Tool 이 실행될 때마다 표시를 옮긴다.
        # 강제하지 않는다 — 계획은 모델의 메모이고 실행 판단은 여전히 매 걸음 한다.
        self.plan = None

    # ------------------------------------------------------------------
    # 프롬프트 조립
    #
    # 고정된 것과 매 턴 바뀌는 것을 나눠 둡니다.
    # 서버는 요청의 "앞에서부터 같은 부분" 을 재사용합니다 (prefix caching).
    # 그래서 바뀌는 값이 앞에 있으면 그 뒤가 전부 무효가 됩니다.
    #
    #   전:  [system: 규칙 + 현재 상태]  [대화]  [질문]  + tools
    #              └ 매 턴 바뀜 -> 뒤의 tools 2,500 토큰까지 다시 계산
    #
    #   후:  [system: 규칙]  [대화]  [현재 상태 + 질문]  + tools
    #        └───── 안 바뀜, 재사용 ─────┘  └ 여기만 새로
    #
    # 덤으로 모델은 긴 프롬프트의 가운데를 흘리는 경향이 있어서,
    # 최신 정보가 뒤에 있는 편이 더 잘 읽힙니다.
    # ------------------------------------------------------------------

    # 현재 상태에 늘어놓을 최대 줄 수.
    # 전부 나열하면 주문이 쌓일수록 프롬프트가 무한정 커집니다.
    # 관찰은 덤프가 아니라 요약이어야 하고, 자세한 건 Tool 로 가져오면 됩니다.
    MAX_STATE_CART_LINES = 8
    MAX_STATE_ORDERS = 5

    def _cart_line(self):
        cart = self.store.view_cart()
        if cart["count"] == 0:
            return "비어 있음"

        shown = cart["items"][: self.MAX_STATE_CART_LINES]
        items = " / ".join(
            f"{item['name']}({item['product_id']}) {item['size']} 사이즈 {item['quantity']}개"
            for item in shown
        )
        if len(cart["items"]) > len(shown):
            items += f" 외 {len(cart['items']) - len(shown)}종"
        return f"{items} — 총 {cart['quantity']}개, 합계 {cart['total']:,}원"

    def _order_line(self):
        orders = sorted(self.store.orders, key=lambda o: o["ordered_at"], reverse=True)
        if not orders:
            return "없음"

        shown = orders[: self.MAX_STATE_ORDERS]
        line = " / ".join(
            f"{order['order_id']} {order['product_name']}({order['status']})"
            for order in shown
        )
        if len(orders) > len(shown):
            line += f" 외 {len(orders) - len(shown)}건 (search_order 로 조회)"
        return line

    def _state_block(self):
        """이 요청 시점의 장바구니·주문을 글로 적는다.

        모델이 이전 대화의 view_cart 결과를 그대로 재사용하는 문제가 있었다.
        사용자가 화면에서 직접 담거나 빼면 대화 내역은 그 순간 낡는다.
        (관찰: 채팅으로 장바구니 확인 -> 화면에서 클릭으로 담기 ->
               다시 물어보면 옛날 목록을 그대로 답했다)

        "매번 Tool 로 다시 확인하라" 고 지시하는 것보다,
        매 턴 실제 상태를 함께 보내는 쪽이 확실하다.
        모델의 성실함에 기대지 않고 사실을 손에 들려주는 방식이다.
        """
        lines = [
            "[현재 상태] 이 요청이 시작된 시점의 실제 값입니다.",
            f"- 장바구니: {self._cart_line()}",
            f"- 주문: {self._order_line()}",
        ]

        if config.USER_MEMORY_ENABLED:
            lines.append(f"- 사용자 선호 메모리: {self.preferences.state_line()}")

        recent = self._recent_line()
        if recent:
            lines.append(f"- 직전 {recent}")

        search_context = self._search_context_line()
        if search_context:
            lines.append(f"- 직전 검색 조건: {search_context}")

        if self.plan is not None:
            lines.append(f"- {self.plan.state_line(cart_total=self.store.view_cart()['total'])}")

        waiting = self._postponed_line()
        if waiting:
            lines.append(
                f"- 이어서 할 일: 사용자가 같은 요청에서 함께 부탁했는데 아직 안 한 작업입니다 — "
                f"{waiting}\n"
                f"  앞 작업이 끝났으면 이것을 이어서 처리하세요. "
                f"사용자가 다시 말하지 않아도 됩니다."
            )

        if self.pending is not None:
            # 앱은 확인 대기 중인 걸 아는데 모델은 모른다.
            # 미리보기에서 루프를 끊고 돌아가면 그 턴의 tool_calls 와 Tool 결과는
            # 다음 턴 대화에 남지 않기 때문이다 (app.py 는 최종 답변만 저장한다).
            # 그래서 모델이 "이번엔 confirm=true 를 붙여야 한다" 를 알 근거가 없었고,
            # 사용자가 "ㅇㅇ" 를 두 번 말해야 지워졌다.
            if self.approve_by_button:
                # 버튼 모드에서는 모델이 승인 여부를 판단하지 않는다.
                # 그래서 "동의했으면 다시 호출하라" 대신 "관여하지 말라" 를 알린다.
                lines.append(
                    f"- 확인 대기: 방금 사용자에게 이렇게 물었습니다 — "
                    f"\"{self.pending.summary}\"\n"
                    f"  이 작업은 사용자가 **화면의 버튼**을 눌러야 실행됩니다.\n"
                    f"  사용자가 말로 동의하기만 했다면 {self.pending.tool} 를 다시 부르지 말고, "
                    f"버튼을 눌러 달라고 안내하세요.\n"
                    f"  **단, 대상·수량·사유 같은 내용을 바꿔 달라고 하면 반드시 "
                    f"{self.pending.tool} 를 바뀐 값으로 다시 호출하세요.** "
                    f"그래야 화면의 버튼도 바뀐 내용으로 갱신됩니다.\n"
                    f"  호출하지 않고 \"바꿔 드렸습니다\" 라고만 답하면, "
                    f"버튼은 이전 내용 그대로라 사용자가 다른 것을 승인하게 됩니다.\n"
                    f"  다른 것을 요청했으면 그 요청을 처리하세요. 이 대기는 무시하면 됩니다."
                )
            else:
                lines.append(
                    f"- 확인 대기: 방금 사용자에게 이렇게 물었습니다 — "
                    f"\"{self.pending.summary}\"\n"
                    f"  사용자가 동의했으면 {self.pending.tool} 를 **같은 대상으로** "
                    f"confirm=true 를 붙여 다시 호출하세요.\n"
                    f"  대상이 둘 이상이면 items 배열에 전부 담아 한 번만 호출하세요.\n"
                    f"  동의하지 않았으면 호출하지 말고 취소되었다고 답하세요.\n"
                    f"  다른 것을 요청했으면 그 요청을 처리하세요. 이 대기는 무시하면 됩니다."
                )

        lines.append(
            "이전 대화에 적힌 목록은 신뢰하지 마세요. 위 [현재 상태] 가 지금 값입니다.\n"
            "  - 장바구니: 위가 **전부**입니다. 내용을 알려고 view_cart 를 또 부르지 마세요.\n"
            "  - 주문: 위는 **최근 몇 건만** 보여 준 것입니다. 주문을 세거나 목록으로 "
            "답하거나 조건으로 찾을 때는 반드시 search_order 로 조회하세요.\n"
            "  - 그 밖의 것(상품 재고, 세탁 방법, 주문 상세)은 해당 Tool 로 확인하세요."
        )
        return "\n".join(lines)

    def _recent_line(self):
        """직전 검색·비교 결과를 번호와 함께 한 줄로 만든다.

        사용자는 "두 번째 거 담아줘", "아까 비교한 것 중에" 처럼 말한다.
        그런데 다음 턴에는 Tool 결과가 대화에 남지 않아서, 모델은 자기가 쓴
        답변 문장만 보고 순서를 짐작해야 했다. 그러면 엉뚱한 상품이 담긴다.
        번호와 실제 ID 를 함께 주면 짐작할 일이 없다.
        """
        recent = self.last_results
        if not recent:
            return ""

        label = "검색 결과" if recent["tool"] == "search_product" else "비교 결과"
        items = " ".join(
            f"{index}) {item['name']}({item['product_id']})"
            for index, item in enumerate(recent["items"], 1)
        )
        return f"{label}: {items}"

    def _search_context_line(self):
        """후속 요청에서 유지하거나 바꿀 수 있도록 직전 검색 인자를 보여 준다."""
        if not self.last_search:
            return ""
        arguments = json.dumps(self.last_search.get("arguments") or {},
                               ensure_ascii=False, sort_keys=True, default=str)
        total = self.last_search.get("total")
        shown = self.last_search.get("shown")
        counts = []
        if isinstance(total, int):
            counts.append(f"후보 {total}개")
        if isinstance(shown, int):
            counts.append(f"화면 {shown}개")
        suffix = f" ({', '.join(counts)})" if counts else ""
        return arguments + suffix

    def _user_message(self, user_message):
        """현재 상태를 앞에 붙인 사용자 메시지.

        상태를 별도의 system 메시지로 끼워 넣지 않고 사용자 메시지에 붙인 이유:
        대화 중간의 system 메시지를 무시하거나 첫 번째만 인정하는 서버가 있습니다.
        그러면 상태가 조용히 사라져 원래 버그로 되돌아갑니다.
        user 메시지는 어떤 서버든 반드시 읽으므로 이쪽이 안전합니다.

        여기서 만든 문자열은 화면의 대화 기록에는 남지 않습니다.
        app.py 는 사용자가 실제로 친 문장만 저장합니다.
        """
        return {"role": "user", "content": f"{self._state_block()}\n\n{user_message}"}

    # ------------------------------------------------------------------
    # 승인 관리
    # ------------------------------------------------------------------

    def _is_approved(self, name, key):
        """이 호출이 사용자 승인을 받은 것인지 판정한다.

        조건 세 가지를 모두 만족해야 한다.
          1. 모델이 confirm=True 를 보냈다        (부르는 쪽에서 확인)
          2. 대기 목록에 그 대상이 들어 있다 (통과하면 목록에서 뺀다)
          3. 그 대기 기록이 이전 턴에 만들어졌다
             (= 사용자가 미리보기를 보고 나서 새 메시지를 보냈다)

        3번이 핵심이다. 모델은 한 턴 안에서 이 조건을 만들 수 없다.
        """
        if self.approve_by_button:
            # 버튼 모드에서는 자연어 승인을 인정하지 않는다.
            # 승인 경로가 둘이면 둘 다 지켜야 하는데, 언어 쪽은 지킬 수가 없다.
            return False
        if self.pending is None:
            return False
        if self.pending.tool != name:
            return False
        if self.pending.turn >= self.turn:
            return False           # 사용자가 아직 답하지 않았다
        if self.turn - self.pending.turn > PENDING_TTL_TURNS or self.pending.expired():
            # 오래된 승인은 버린다. 안 그러면 한참 뒤에 되살아난다.
            # (다섯 턴 동안 다른 얘기를 하다가 "어 그래" 에 주문이 취소됐다)
            self.pending = None
            return False
        # 대기 목록에 있으면 통과시키고 그 항목을 뺀다.
        # 없으면 대상이 바뀐 것이므로 다시 확인한다.
        return self.pending.take(key)

    def _consume_postponed(self, tool_name, arguments):
        """미뤄 둔 작업 중 지금 실행한 것을 지운다.

        Tool 이름만 보고 지우면 안 된다. "주문 두 건 취소" 는 둘 다
        cancel_order 라서, 한 건을 처리하는 순간 나머지 한 건까지 사라진다.
        인자까지 같아야 같은 작업이다.
        """
        if not self.postponed:
            return
        target = json.dumps({k: v for k, v in arguments.items() if k != "confirm"},
                            sort_keys=True, ensure_ascii=False, default=str)

        remaining = []
        removed = False
        for item in self.postponed["items"]:
            same = json.dumps(item["arguments"], sort_keys=True,
                              ensure_ascii=False, default=str)
            if not removed and item["tool"] == tool_name and same == target:
                removed = True          # 같은 것 하나만 지운다
                continue
            remaining.append(item)

        self.postponed = ({"items": remaining, "turn": self.postponed["turn"]}
                          if remaining else None)

    def _postponed_line(self):
        """미뤄 둔 작업을 프롬프트에 실을 문장으로 만든다.

        세 턴이 지나도 처리되지 않았으면 사용자가 마음을 바꾼 것으로 보고 버린다.
        계속 들고 있으면 한참 뒤에 엉뚱하게 되살아난다.
        """
        if not self.postponed:
            return ""
        if self.turn - self.postponed["turn"] > 3:
            self.postponed = None
            return ""
        items = " / ".join(
            f"{item['tool']}({json.dumps(item['arguments'], ensure_ascii=False)})"
            for item in self.postponed["items"]
        )
        return items

    def _executed(self, trace):
        """이번 턴에 실제로 상태를 바꾸려고 **시도한** 기록만 고른다.

        미리보기(requires_confirmation)는 아직 아무것도 바꾸지 않았으므로 뺀다.
        성공한 것만이 아니라 실패한 것도 남긴다. 실패했다는 사실 자체가
        사용자에게 전달되어야 하는 정보이기 때문이다.
        """
        return [
            entry for entry in trace
            if entry["tool"] in STATE_CHANGING
            and not entry.get("internal")          # 중복 차단·검증기 기록은 실행이 아니다
            and not (entry["result"].get("data") or {}).get("requires_confirmation")
        ]

    def _result_report(self, trace, interrupted=False, always=False):
        """실행 결과를 **앱이** 문장으로 만든다.

        최종 답변은 모델이 쓴다. 그런데 세 건 중 두 건만 성공해도
        모델은 "두 건 모두 처리했습니다" 라고 쓸 수 있다.
        데이터는 store 가 실행부에서 다시 판정하므로 안전하지만(7번),
        **보고가 틀린다.** 사용자 입장에서는 둘이 구분되지 않는다.

        판단하는 곳이 한 곳이어야 하듯 보고하는 곳도 한 곳이어야 한다.
        그래서 trace 에서 사실을 뽑아 모델 문장 아래에 붙인다.
        모델이 무엇을 쓰든 이 줄은 바뀌지 않는다.

        늘 붙이면 시끄러우므로, 모델이 요약하다 틀릴 수 있을 때만 붙인다.
          - 실패가 하나라도 섞여 있을 때
          - 상태를 바꾼 작업이 두 건 이상일 때
          - 모델 문장이 없는 경로일 때 (버튼 승인 always, 중간 실패 interrupted)
        """
        done = self._executed(trace)
        if not done:
            return ""

        failed = [entry for entry in done if not entry["result"].get("success")]
        if not (always or interrupted or failed or len(done) > 1):
            return ""

        lines = []
        for entry in done:
            mark = "\u2713" if entry["result"].get("success") else "\u2717"
            text = entry["result"].get("message") or entry["tool"]
            lines.append(f"  {mark} {text}")

        note = ""
        if interrupted and len(failed) < len(done):
            # 모델 호출이 중간에 끊긴 경우. 이미 반영된 것을 알려 주지 않으면
            # 사용자가 같은 요청을 다시 해서 두 번 처리된다.
            note = "\n  (\u2713 표시된 작업은 이미 반영되었습니다. 다시 요청하지 마세요.)"

        return "\n\n\u2500 실행 결과 \u2500\n" + "\n".join(lines) + note

    def _stale_pending_note(self):
        """이번 턴에 Tool 을 부르지 않았는데 확인 대기가 살아 있으면 알린다.

        모델이 "사유를 바꿔서 다시 확인해 드리겠습니다" 라고 **말만 하고**
        Tool 을 다시 부르지 않은 적이 있다. 그러면 화면의 승인 버튼은
        이전 내용 그대로인데 대화에는 바뀐 것처럼 적힌다.
        사용자는 자기가 말한 것과 다른 것을 승인하게 된다.

        21번과 같은 원칙이다. 모델이 무엇을 쓰든 사실은 앱이 붙인다.
        """
        pending = self.pending
        if pending is None or pending.turn >= self.turn:
            return ""      # 이번 턴에 새로 잡힌 대기라면 방금 그 문장이 맞다

        labels = "\n".join(f"  {index}. {item['label']}"
                            for index, item in enumerate(pending.items, 1))
        return ("\n\n─ 승인 버튼은 아직 아래 내용입니다 ─\n" + labels
                + "\n  바꾸시려면 무엇을 어떻게 바꿀지 말씀해 주세요.")

    def _take_plan(self, arguments):
        """make_plan 호출을 받는다. 검사에 통과하면 계획을 바꿔 들고 결과를 돌려준다."""
        if not config.PLAN_MODE:
            return {"success": False, "data": None,
                    "message": "make_plan 은 지금 사용할 수 없습니다. 바로 필요한 Tool 을 부르세요."}
        known_tools = set(TOOL_INDEX)
        if config.USER_MEMORY_ENABLED:
            known_tools.add("manage_preferences")
        cleaned, error = planning.validate_plan(arguments, known_tools)
        if error:
            return {"success": False, "data": None, "message": f"make_plan: {error}"}
        self.plan = planning.Plan(cleaned["goal"], cleaned["steps"], turn=self.turn,
                                  budget=cleaned.get("budget"))
        names = " → ".join(step["tool"] for step in cleaned["steps"])
        return {"success": True,
                "data": {"steps": len(cleaned["steps"]), "plan": self.plan.checklist()},
                "message": (f"계획 {len(cleaned['steps'])}단계를 적어 두었습니다: {names}. "
                            f"아무것도 실행되지 않았습니다. 이제 1단계 {cleaned['steps'][0]['tool']} 를 부르세요.")}

    def _remember_results(self, name, result, arguments=None):
        """검색·비교 결과의 상품 ID 와 순서를 기록한다.

        다음 턴에는 Tool 결과 원문이 남지 않으므로, "두 번째 상품" 같은 말을
        실제 ID 로 이으려면 여기에 따로 들고 있어야 한다.
        매번 덮어쓰기 때문에 대화가 길어져도 커지지 않는다.
        """
        if name not in ("search_product", "comparing_info"):
            return
        if not result.get("success"):
            return

        data = result.get("data") or {}
        rows = data.get("products") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return

        picked = [
            {"product_id": row.get("product_id"), "name": row.get("name")}
            for row in rows if isinstance(row, dict) and row.get("product_id")
        ][:10]
        if picked:
            self.last_results = {"tool": name, "items": picked, "turn": self.turn}
        elif name == "search_product":
            # 빈 검색 뒤 "그중"이 이전 검색의 상품을 가리키면 안 된다.
            self.last_results = None
        if name == "search_product":
            self.last_search = {
                "arguments": dict(arguments or {}),
                "items": picked,
                "turn": self.turn,
                "total": data.get("total") if isinstance(data, dict) else None,
                "shown": data.get("shown") if isinstance(data, dict) else len(rows),
                "ranking": data.get("ranking") if isinstance(data, dict) else None,
            }

    def _verify_outcome(self, user_message, draft, trace, about_to_confirm=None):
        """하나의 검증 단계에서 완료·안전한 재시도·사용자 질문을 결정한다.

        검증기는 Tool을 실행하지 않는다. 실패해도 기존 답변을 그대로 반환하도록
        격리해 두어, 검증 모델 장애가 주문·승인 흐름을 깨지 않게 한다.
        about_to_confirm 이 있으면 확인 버튼을 띄우기 직전 단계의 검증이다.
        """
        try:
            message = call_model(
                agency.verifier_messages(user_message, draft, trace, self._state_block(),
                                         about_to_confirm=about_to_confirm),
                tools=[agency.VERIFY_TOOL],
            )
            calls = parse_tool_calls(message, allow_text=False)
            if len(calls) != 1 or calls[0].name != "verify_outcome" or calls[0].error:
                return None
            decision = agency.validate_verdict(calls[0].arguments)
            if decision is None:
                return None
            trace.append({
                "tool": "verify_outcome",
                "arguments": calls[0].arguments,
                "result": {"success": True, "data": decision,
                           "message": decision["summary"] or decision["verdict"]},
                "internal": True,
                "stage": "before_confirm" if about_to_confirm else "final",
            })
            return decision
        except (requests.RequestException, KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _call_signature(name, arguments):
        return json.dumps({"tool": name, "arguments": arguments}, sort_keys=True,
                          ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # 실행
    # ------------------------------------------------------------------

    def _preview(self, tool, arguments, trace):
        """미리보기를 돌리고 기록에 남긴다. 상태는 바뀌지 않는다."""
        clean = {key: value for key, value in arguments.items() if key != "confirm"}
        preview = self.toolbox.call(tool, {**clean, "confirm": False})
        trace.append({"tool": tool, "arguments": {**clean, "confirm": False},
                      "result": preview})
        return clean, preview

    def _entry(self, arguments, preview):
        """확인 대기 한 줄. 승인 시점에 비교할 값을 함께 들고 있는다."""
        snap = _snapshot(preview)
        return {"key": snap["key"],
                "amount": snap["amount"],
                "label": preview.get("message", ""),
                "arguments": dict(arguments)}, snap

    def _open_pending(self, requests, trace, note=""):
        """[{tool, arguments}] 를 미리보기 돌려 **새 확인 대기**를 연다.

        모델을 거치지 않는다. 두 곳에서 쓴다.
          - 승인 직전에 내용이 달라져 다시 확인받아야 할 때
          - 앞 작업을 승인한 뒤 미뤄 둔 작업을 이어갈 때

        한 번의 확인은 Tool 하나만 담는다. 다른 Tool 은 다시 미뤄 둔다.
        (섞으면 사용자가 무엇을 승인하는지 흐려지고, 앞 작업 반영 전 상태로
         뒤 작업 미리보기가 계산된다)

        반환: (사용자에게 보여줄 문장, 실행할 수 없던 것들의 사유 목록)
        """
        if not requests:
            return "", []

        tool = requests[0]["tool"]
        entries, rows_all, leftover, problems = [], [], [], []

        for request in requests:
            if request["tool"] != tool or len(entries) >= MAX_CONFIRM_AT_ONCE:
                leftover.append(request)
                continue

            arguments, preview = self._preview(request["tool"],
                                               request["arguments"], trace)
            if not preview.get("success"):
                problems.append(preview.get("message") or request["tool"])
                continue

            if entries and _overlaps(rows_all, preview):
                leftover.append(request)
                continue

            entry, snap = self._entry(arguments, preview)
            entries.append(entry)
            rows_all.extend(snap["rows"])

        self.postponed = ({"items": leftover, "turn": self.turn}
                          if leftover else None)

        if not entries:
            return "", problems

        summary = "\n\n".join(entry["label"] for entry in entries)
        if note:
            summary = note + "\n\n" + summary
        summary += ("\n\n아래 버튼으로 선택해 주세요. "
                    "되돌릴 수 없는 작업이라 채팅 답변으로는 진행되지 않습니다.")
        self.pending = PendingAction(tool=tool, items=entries,
                                     summary=summary, turn=self.turn)
        return summary, problems

    def approve(self, keys=None):
        """화면 버튼으로 들어온 승인. **모델을 거치지 않고** 실행한다.

        모델이 관여하지 않는 것이 핵심이다. "사용자가 무엇에 동의했는가" 가
        자연어 해석 결과가 아니라 사용자가 **누른 열쇠**이므로,
        동의 범위가 어긋날 여지가 없다.

        다만 열쇠가 맞다고 그대로 실행하면 안 된다.
        미리보기를 계산한 시점과 버튼을 누르는 시점 사이에 상태가 바뀔 수 있다.
        (129,000원 결제를 확인받은 뒤 사용자가 화면에서 상품을 더 담으면
         그 버튼으로 238,000원이 결제된다. DB 로 치면 TOCTOU 다.)

        그래서 **실행 직전에 미리보기를 다시 계산해 승인 시점과 비교한다.**
        다르면 실행하지 않고 새 미리보기로 다시 확인받는다.
        """
        pending = self.pending
        if pending is None:
            return "확인 대기 중인 작업이 없습니다.", []

        if pending.expired():
            # 버튼이 화면에 떠 있는 채로 시간이 지났다. 미리보기를 계산한 시점의
            # 장바구니·주문 상태가 지금과 같다는 보장이 없으므로 실행하지 않는다.
            self.pending = None
            self.postponed = None
            return ("확인 시간이 지나 실행하지 않았습니다. "
                    "필요하시면 다시 요청해 주세요."), []

        self.turn += 1
        targets = (list(pending.keys) if keys is None
                   else [key for key in keys if pending.find(key) is not None])
        if not targets:
            return "선택된 작업이 없습니다.", []

        tool = pending.tool
        trace = []
        executed, stale, blocked = [], [], []

        for key in targets:
            item = pending.find(key)
            if item is None:
                continue
            pending.take(key)

            arguments, preview = self._preview(tool, item["arguments"], trace)

            if not preview.get("success"):
                # 배송이 시작됐거나 재고가 빠졌다. 상태를 바꾸지 않고 사유만 알린다.
                blocked.append(preview.get("message") or f"{tool} 을 실행할 수 없습니다.")
                continue

            snap = _snapshot(preview)
            if snap["key"] != item["key"] or snap["amount"] != item.get("amount"):
                # 승인 화면에 보여 준 내용과 지금 실행될 내용이 다르다.
                stale.append({"tool": tool, "arguments": arguments})
                continue

            result = self.toolbox.call(tool, {**arguments, "confirm": True})
            trace.append({"tool": tool, "arguments": {**arguments, "confirm": True},
                          "result": result})
            self._remember_results(tool, result, {**arguments, "confirm": True})
            if self.plan is not None:
                self.plan.advance(tool, result)
            self._consume_postponed(tool, arguments)
            executed.append(result)

        if pending.empty():
            self.pending = None
        else:
            # 일부만 승인했다. 남은 항목의 유효 기간을 지금부터 다시 센다.
            pending.turn = self.turn
            pending.created_at = time.time()

        lines = []
        if executed:
            lines.append("처리했습니다.")
        elif not stale and not blocked:
            lines.append("처리할 항목이 없었습니다.")

        # 이 경로에는 모델이 쓴 문장이 없다. 결과 보고를 반드시 붙인다.
        report = self._result_report(trace, always=True)

        if blocked:
            lines.append("아래는 지금은 처리할 수 없습니다.\n  "
                         + "\n  ".join(blocked))

        failed = any(not entry.get("success") for entry in executed)

        # ① 승인 내용이 달라진 것부터 다시 확인받는다.
        if stale and self.pending is None:
            note = ("승인하신 내용과 지금 상태가 달라져 실행하지 않았습니다. "
                    "바뀐 내용으로 다시 확인해 주세요.")
            # _open_pending 은 남는 것이 없으면 postponed 를 비운다.
            # 여기서는 아직 미뤄 둔 후속 작업을 잃으면 안 되므로 지키고 되돌린다.
            keep = self.postponed
            summary, problems = self._open_pending(stale, trace, note=note)
            if self.postponed is None:
                self.postponed = keep
            if summary:
                lines.append(summary)
            elif problems:
                lines.append("다시 확인하려 했으나 처리할 수 없습니다.\n  "
                             + "\n  ".join(problems))
        elif stale:
            lines.append("일부 항목은 내용이 달라져 실행하지 않았습니다. "
                         "남은 확인을 마친 뒤 다시 요청해 주세요.")

        # ② 미뤄 둔 후속 작업을 이어간다. 앞 작업이 실패했으면 진행하지 않는다.
        elif self.pending is None and self.postponed and not failed and not blocked:
            items = self.postponed["items"]
            self.postponed = None
            summary, problems = self._open_pending(
                items, trace, note="이어서 부탁하신 작업입니다.")
            if summary:
                lines.append(summary)
            elif problems:
                lines.append("이어서 진행하려던 작업은 처리할 수 없습니다.\n  "
                             + "\n  ".join(problems))

        if self.pending is not None and len(self.pending.items) > 1:
            lines.append(f"확인이 필요한 항목이 {len(self.pending.items)}건 있습니다.")

        if (failed or blocked) and self.postponed:
            # 앞 작업이 안 됐는데 뒤 작업을 진행하면 사용자가 원한 결과가 아니다.
            self.postponed = None
            lines.append("앞 작업이 완료되지 않아 이어지는 작업은 진행하지 않았습니다.")

        answer = "\n\n".join(line for line in lines if line)
        return (answer + report).strip(), trace

    # 승인 뒤 이어갈 때 모델에게 보내는 문장. 사용자가 친 말이 아니므로
    # 서버는 이것을 대화 내역의 user 메시지로 남기지 않는다.
    PLAN_RESUME_MESSAGE = ("(앱) 승인된 작업이 처리되었습니다. [현재 상태] 의 계획에서 "
                           "남은 단계를 이어서 진행하세요. 이미 끝난 단계는 다시 하지 않습니다.")
    RESUME_MESSAGE = ("(앱) 승인된 작업이 처리되었습니다. 사용자의 원래 요청은 다음과 같았습니다:\n"
                      "「{request}」\n"
                      "이 요청 중 **아직 하지 않은 부탁**이 있으면 지금 이어서 처리하세요 "
                      "(예: 취소 뒤 다른 상품 담기, 반품 뒤 재고 확인). "
                      "이미 처리된 것은 다시 하지 마세요. 남은 부탁이 없으면 Tool 을 부르지 말고 "
                      "한 문장으로만 마무리하세요.")
    # 승인이 원래 요청과 같은 흐름인지 판단하는 턴 간격. run(요청) → approve 가 +1.
    RESUME_MAX_TURN_GAP = 2

    def continue_plan(self, history=None):
        """승인으로 끊긴 요청에 남은 부분이 있으면 모델을 다시 불러 이어간다.

        "어제 주문 취소하고 흰색 운동화 담아줘" 에서 취소는 승인 버튼으로 끊긴다.
        승인 뒤 뒷부분은 사용자가 다시 말해야만 진행됐다(기준선 실험에서 0/3).
        계획이 있으면 계획의 남은 단계를, 없으면 원래 요청 문장을 들려주고
        "남은 부탁이 있으면 이어가라" 고 한 번 더 부른다. 남은 게 없으면 모델이
        한 문장으로 마무리하므로 비용은 승인 한 번당 모델 호출 한 번이다.
        확인 대기·미룬 작업이 남아 있으면 그쪽 절차가 먼저이므로 부르지 않는다.
        돌려주는 값은 run() 과 같고, 이어갈 것이 없으면 None.
        """
        if self.pending is not None or self.postponed:
            return None
        if config.PLAN_MODE and self.plan is not None and not self.plan.done():
            return self.run(self.PLAN_RESUME_MESSAGE, history)
        if not config.RESUME_AFTER_APPROVAL:
            return None
        request = self.last_request
        if not request or self.turn - request["turn"] > self.RESUME_MAX_TURN_GAP:
            return None
        if not request.get("interrupted"):
            # 승인이 요청의 마지막 단계였다면(“결제해줘” 하나) 이어갈 것이 없다.
            return None
        return self.run(self.RESUME_MESSAGE.format(request=request["text"]), history)

    # 확인 대기가 열릴 때, 같은 턴에 조회한 내용을 사용자에게 먼저 답하게 하는 문장.
    ANSWER_BEFORE_CONFIRM = (
        "(앱) 확인이 필요한 작업은 앱이 버튼으로 처리합니다. 그것을 제외하고, "
        "지금까지 Tool 로 확인한 내용 중 사용자가 물은 것(재고·세탁·주문 상태 등)이 있으면 "
        "그 답만 두 문장 이내로 쓰세요. 확인 질문을 다시 쓰지 말고, Tool 도 부르지 마세요. "
        "답할 것이 없으면 빈 답을 보내세요.")

    def _answer_before_confirm(self, messages, trace, model_content):
        """확인 대기로 턴이 끊기기 전에, 이번 턴의 조회 결과를 답변으로 남긴다.

        "반품 신청하고 105 사이즈 재고도 알려줘" 에서 모델은 get_info 로 재고를
        확인했지만, 반품 확인 대기가 열리면서 그 답을 쓸 기회가 사라졌다(기준선 0/3).
        조회 Tool 이 이번 턴에 실행됐고 모델이 아직 문장을 쓰지 않았다면
        모델을 한 번 더 불러 조회 결과만 답하게 한다. 그 외에는 부르지 않는다.
        """
        if model_content:
            return model_content.strip()
        informational = [
            entry for entry in trace
            if entry.get("tool") in INFORMATIONAL_TOOLS and not entry.get("internal")
            and (entry.get("result") or {}).get("success")
        ]
        if not informational:
            return ""
        try:
            message = call_model(
                messages + [{"role": "user", "content": self.ANSWER_BEFORE_CONFIRM}],
                tools=None)
        except (requests.RequestException, KeyError, ValueError, TypeError):
            return ""
        if parse_tool_calls(message, allow_text=False):
            return ""
        return (message.get("content") or "").strip()

    def reject(self):
        """확인을 거절했다.

        **이번 요청에서 이미 실행된 작업까지 되돌리지는 않는다.**
        "아무것도 변경하지 않았습니다" 라고 안내하면 거짓말이 된다.
        ("B 담고 A 빼줘" 에서 B 는 이미 담긴 상태다)
        거절한 범위만 정확히 말한다.
        """
        if self.pending is None:
            return "확인 대기 중인 작업이 없습니다.", []

        self.turn += 1
        labels = [item["label"] for item in self.pending.items]
        tool = self.pending.tool
        had_postponed = bool(self.postponed)

        self.pending = None
        self.postponed = None

        lines = [f"대기 중이던 {ACTION_NAME.get(tool, tool)} {len(labels)}건을 "
                 f"진행하지 않았습니다."]
        if had_postponed:
            lines.append("이어서 하려던 작업도 함께 중단했습니다.")
        lines.append("이미 처리된 작업은 그대로 남아 있습니다.")
        return "\n\n".join(lines), []

    # ------------------------------------------------------------------
    # 상태 저장/복원 — 서버가 요청 사이에 DB 에 둔다
    #
    # HTTP 서버는 요청마다 다른 스레드에서 이 객체를 부르고, 재시작하면 객체가
    # 사라진다. 확인 대기가 객체 안에만 있으면 재시작 뒤 화면의 승인 버튼이
    # 아무것도 가리키지 않는다. 그래서 요청이 끝날 때마다 아래 dict 를 저장하고,
    # 객체를 새로 만들 때 되살린다. 정책(만료·열쇠 비교)은 그대로 이 파일이 한다.
    # ------------------------------------------------------------------

    def snapshot(self):
        """저장할 상태. 전부 JSON 으로 표현 가능한 값만."""
        return {
            "turn": self.turn,
            "pending": self.pending.to_dict() if self.pending is not None else None,
            "postponed": self.postponed,
            "last_results": self.last_results,
            "last_search": self.last_search,
            "last_request": self.last_request,
            "preferences": self.preferences.to_dict(),
            "plan": self.plan.to_dict() if self.plan is not None else None,
        }

    def restore(self, state):
        """snapshot() 이 만든 dict 로 되살린다. 만료된 확인 대기는 살리지 않는다."""
        if not state:
            return
        self.turn = int(state.get("turn") or 0)
        pending = state.get("pending")
        self.pending = PendingAction.from_dict(pending) if pending else None
        if self.pending is not None and self.pending.expired():
            self.pending = None
            # 이 작업들은 만료된 승인을 전제로 뒤에 이어질 예정이었다.
            # 앞 승인이 무효가 됐으므로 함께 버려야 나중에 되살아나지 않는다.
            self.postponed = None
        else:
            self.postponed = state.get("postponed") or None
        self.last_results = state.get("last_results") or None
        self.last_search = state.get("last_search") or None
        self.last_request = state.get("last_request") or None
        self.preferences = agency.PreferenceMemory.from_dict(state.get("preferences"))
        plan = state.get("plan")
        self.plan = planning.Plan.from_dict(plan) if plan else None

    def run(self, user_message, history=None):
        """사용자 메시지 하나를 처리하고 (최종답변, 실행기록) 을 반환한다.

        trace 는 "에이전트가 어떤 Tool 을 어떤 인자로 불렀는지" 의 기록이다.
        UI 에서 이걸 펼쳐 보여주는 것이 발표에서 가장 중요한 화면 요소다.
        """
        history = history or []
        trace = []
        verifier_retries = 0
        is_resume = user_message.startswith("(앱) ")
        failed_signatures = set()
        completed_mutations = set()
        self.turn += 1

        # 오래된 확인 대기는 버린다.
        #
        # 버튼 모드에서는 _is_approved() 가 항상 False 라 만료 검사를 지나가지 않는다.
        # 그대로 두면 승인 버튼이 화면에 계속 떠 있다가 한참 뒤에 눌린다.
        # 그때는 미리보기가 계산된 시점의 장바구니·주문 상태가 아니다.
        if self.pending is not None and (
                self.turn - self.pending.turn > PENDING_TTL_TURNS
                or self.pending.expired()):
            self.pending = None
            self.postponed = None

        # 지난 요청의 계획은 끝났으면 지운다. 승인 대기로 끊겼다가 이어지는 경우는
        # 남겨 두되, 몇 턴이 지나도 안 끝난 계획은 새 요청을 방해하므로 버린다.
        if self.plan is not None and (
                self.plan.done() or self.turn - self.plan.turn > PLAN_TTL_TURNS):
            self.plan = None

        # 계획 모드면 규칙 한 단락과 make_plan 을 더한다. 둘 다 고정값이라 캐시는 유지된다.
        plan_mode = bool(config.PLAN_MODE)
        system_prompt = self.system_prompt
        tools = list(TOOLS)
        if self.adaptive_mode:
            system_prompt += agency.ADAPTIVE_PROMPT
        if config.USER_MEMORY_ENABLED:
            system_prompt += agency.MEMORY_PROMPT
            tools.append(agency.MEMORY_TOOL)
        if plan_mode:
            # 기존 계획 훅 위에 검증 계층을 얹는다. make_plan/advance/continue_plan은 유지한다.
            system_prompt += planning.PLAN_PROMPT
            tools.append(planning.MAKE_PLAN_TOOL)

        messages = [
            {"role": "system", "content": system_prompt},        # 고정. 캐시가 재사용한다
            *history,
            self._user_message(user_message),                    # 현재 상태 + 질문
        ]
        if not is_resume:
            self.last_request = {"text": user_message[:500], "turn": self.turn,
                                 "interrupted": False}

        for _ in range(config.MAX_TOOL_ITERATIONS):
            # --- ① 모델에게 대화 + Tool 목록을 보낸다 ---
            try:
                message = call_model(messages, tools=tools)
            except requests.Timeout:
                return ("모델 응답 시간이 초과되었습니다. 서버 상태를 확인해 주세요."
                        + self._result_report(trace, interrupted=True)), trace
            except requests.ConnectionError:
                return ("로컬 모델 서버에 연결할 수 없습니다. 서버 실행 여부를 확인해 주세요."
                        + self._result_report(trace, interrupted=True)), trace
            except requests.HTTPError as error:
                status = error.response.status_code if error.response is not None else "알 수 없음"
                return (f"모델 요청에 실패했습니다. HTTP 상태: {status}"
                        + self._result_report(trace, interrupted=True)), trace
            except (KeyError, ValueError, TypeError):
                return ("모델 응답 형식을 읽지 못했습니다. call_model() 의 파싱 부분을 확인하세요."
                        + self._result_report(trace, interrupted=True)), trace

            # --- ② 모델이 Tool 을 요청했는지 확인한다 ---
            calls = parse_tool_calls(message, allow_text=config.ALLOW_TEXT_TOOL_CALLS)

            # 요청이 없으면 그게 최종 답변이다. 루프 끝.
            if not calls:
                answer = (message.get("content") or "").strip()
                if not answer:
                    answer = "답변을 생성하지 못했습니다. 다시 질문해 주시겠어요?"
                verifier_asked = False

                external = [entry for entry in trace
                            if not entry.get("internal")
                            and entry.get("tool") not in {"make_plan", "manage_preferences"}]
                if self.adaptive_mode and external:
                    decision = self._verify_outcome(user_message, answer, trace)
                    if decision and decision["verdict"] == "ask_user":
                        answer = decision["question"]
                        verifier_asked = True
                    elif (decision and decision["verdict"] == "retry"
                          and verifier_retries < config.MAX_VERIFIER_RETRIES):
                        changed = [entry for entry in external
                                   if entry.get("tool") in STATE_CHANGING
                                   and (entry.get("result") or {}).get("success")]
                        if changed:
                            missing = ", ".join(decision["missing_requirements"])
                            answer = (f"{answer}\n\n일부 작업이 이미 반영되어 자동으로 다시 "
                                      f"실행하지 않았습니다. 아직 확인할 내용: {missing}. "
                                      "계속 진행할지 알려주세요.")
                            verifier_asked = True
                        else:
                            verifier_retries += 1
                            messages.append({"role": "assistant", "content": answer})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "[결과 검증] 아직 충족되지 않은 요구가 있습니다: "
                                    + "; ".join(decision["missing_requirements"])
                                    + "\n다음 지시만 안전하게 보완하세요: "
                                    + decision["next_instruction"]
                                    + "\n이미 성공한 상태 변경 Tool은 다시 실행하지 말고, "
                                      "사용자가 명시한 조건을 임의로 완화하지 마세요."
                                ),
                            })
                            continue
                    elif decision and decision["verdict"] == "retry":
                        missing = ", ".join(decision["missing_requirements"])
                        answer = (f"{answer}\n\n다만 자동 보완 횟수를 모두 사용했고 "
                                  f"아직 확인하지 못한 조건이 있습니다: {missing}. "
                                  "필요한 조건을 조금 더 구체적으로 알려주세요.")
                        verifier_asked = True
                if not verifier_asked:
                    answer = _search_grid_reply(trace) or answer
                # 모델이 쓴 문장 아래에 앱이 만든 사실을 붙인다.
                return answer + self._result_report(trace) + self._stale_pending_note(), trace

            # --- ③ 모델이 무엇을 요청했는지 대화에 남긴다 ---
            messages.append(_assistant_message(message, calls))

            # --- ④ 요청서를 보고 우리가 대신 실행한다 ---
            #
            # 확인이 필요한 작업은 한 턴에 하나만 처리한다.
            #
            # 전에는 여러 건을 한 승인으로 묶었는데, 그게 세 가지 문제를 냈다.
            #   - 서로 다른 Tool 을 묶으면 승인 때 어느 Tool 을 부를지 정해지지 않는다.
            #     ("1개 빼고 주문까지" -> pending 은 하나인데 모델은 둘 중 하나만 부른다)
            #   - 뒤 작업의 미리보기가 앞 작업 전의 상태로 계산된다.
            #     (1개 빼기 전 장바구니로 결제 금액을 뽑아 틀린 금액을 보여줬다)
            #   - 주문 두 건 취소처럼 한 호출로 표현할 수 없는 작업은
            #     승인이 영영 성립하지 않아 미리보기만 반복됐다.
            #
            # 여러 대상을 한 번에 처리해야 하면 Tool 이 그것을 받아야 한다
            # (remove_from_cart 의 items). 그건 호출 하나이므로 확인도 한 번이다.
            holding = None        # 이번 턴에 확인을 기다리게 된 작업
                                  # {"tool": 이름, "items": [...], "rows": [...]}
            postponed = []        # 확인이 하나 잡혀서 이번 턴에 미룬 작업들

            for call in calls:
                # 인자를 읽지 못한 호출은 실행하지 않고 사유만 돌려준다.
                if call.error:
                    result = {"success": False, "data": None,
                              "message": f"{call.name}: {call.error}"}
                    trace.append({"tool": call.name, "arguments": {}, "result": result})
                    failed_signatures.add(self._call_signature(call.name, {}))
                    messages.append(make_tool_result_message(call, result))
                    continue

                arguments = call.arguments if isinstance(call.arguments, dict) else {}

                if call.name == "make_plan":
                    # 계획은 Toolbox 가 아니라 여기서 받는다. 아무것도 실행하지 않고
                    # 단계 목록만 검사해 들고 있다가 상태 블록으로 되돌려 준다.
                    result = self._take_plan(arguments)
                    trace.append({"tool": call.name, "arguments": arguments, "result": result})
                    messages.append(make_tool_result_message(call, result))
                    continue

                if call.name == "manage_preferences":
                    result = self.preferences.update(arguments)
                    trace.append({"tool": call.name, "arguments": arguments,
                                  "result": result})
                    if self.plan is not None:
                        self.plan.advance(call.name, result)
                    messages.append(make_tool_result_message(call, result))
                    continue

                signature = self._call_signature(call.name, arguments)
                if signature in completed_mutations:
                    result = {
                        "success": False,
                        "data": None,
                        "message": ("같은 상태 변경은 이번 요청에서 이미 성공했습니다. "
                                    "중복 실행하지 말고 다음 단계로 진행하세요."),
                    }
                    trace.append({"tool": call.name, "arguments": arguments,
                                  "result": result, "internal": True})
                    messages.append(make_tool_result_message(call, result))
                    continue
                if signature in failed_signatures:
                    result = {
                        "success": False,
                        "data": None,
                        "message": ("같은 인자의 호출이 이미 실패했습니다. 같은 호출을 반복하지 말고 "
                                    "유효한 인자로 고치거나 사용자에게 필요한 값을 물으세요."),
                    }
                    trace.append({"tool": call.name, "arguments": arguments,
                                  "result": result, "internal": True})
                    messages.append(make_tool_result_message(call, result))
                    continue

                if call.name in CONFIRM_REQUIRED:
                    # 이미 확인 대기가 잡혔는데 **다른** Tool 이면 미룬다.
                    # 앞 작업이 반영되기 전 값으로 미리보기를 만들면 틀린 숫자가 나온다.
                    # ("1개 빼고 주문" 에서 빼기 전 금액으로 결제 금액을 뽑았다)
                    #
                    # 같은 Tool 이면 함께 확인한다. 서로 독립적인 대상이라
                    # 앞 건이 뒤 건의 미리보기를 바꾸지 않기 때문이다.
                    # ("두 주문 모두 취소" 를 한 번의 확인으로 끝낼 수 있다)
                    if holding is not None and holding["tool"] != call.name:
                        postponed.append({"tool": call.name, "arguments": arguments})
                        continue

                    # 한 번에 너무 많이 묶지 않는다.
                    # 확인 문장이 길어지면 사용자가 대충 읽고 승인하게 된다.
                    if holding is not None and len(holding["items"]) >= MAX_CONFIRM_AT_ONCE:
                        postponed.append({"tool": call.name, "arguments": arguments})
                        continue

                    # 미리보기를 돌려 "무엇을 처리할 것인가" 를 확정한다.
                    # preview 는 상태를 바꾸지 않으므로 매번 불러도 안전하고,
                    # 모델이 이름으로 부르든 ID 로 부르든 같은 답이 나온다.
                    preview_args = {**arguments, "confirm": False}
                    preview = self.toolbox.call(call.name, preview_args)

                    if not preview.get("success"):
                        # 미리보기 자체가 실패(장바구니에 없음, 취소 불가 등)면 모델에게 돌려준다.
                        trace.append({"tool": call.name, "arguments": preview_args,
                                      "result": preview})
                        failed_signatures.add(signature)
                        messages.append(make_tool_result_message(call, preview))
                        continue

                    key = _action_key(preview)
                    wants = arguments.get("confirm") is True

                    # 앞서 묶은 것과 대상이 겹치면 함께 확인하지 않는다.
                    #
                    # 미리보기는 둘 다 "지금 장바구니" 로 계산되는데 실행은 순서대로다.
                    # 겹치면 앞 건이 뒤 건의 대상을 먹어버려, 승인한 것과 결과가 달라진다.
                    #   "2개 빼고 2개 더" -> 3개뿐인데 4개를 승인받는다
                    #   "다 빼고 225도 1개" -> 뒤 건이 "장바구니에 없습니다" 로 실패한다
                    # 묶는 기준은 "Tool 이 같은가" 가 아니라 "대상이 겹치지 않는가" 다.
                    if holding is not None and _overlaps(holding["rows"], preview):
                        postponed.append({"tool": call.name, "arguments": arguments})
                        continue

                    if not (wants and self._is_approved(call.name, key)):
                        # 승인이 없으면 미리보기까지만. 모델이 confirm=True 를
                        # 보냈어도 여기서 무력화된다.
                        trace.append({"tool": call.name, "arguments": preview_args,
                                      "result": preview})
                        rows = (preview.get("data") or {}).get("preview") or []
                        entry = {"key": key,
                                 "amount": _rows_amount(rows),
                                 "label": preview.get("message", "") or call.name,
                                 "arguments": {k: v for k, v in arguments.items()
                                               if k != "confirm"}}
                        if holding is None:
                            holding = {"tool": call.name, "items": [entry],
                                       "rows": list(rows)}
                        else:
                            holding["items"].append(entry)
                            holding["rows"].extend(rows)
                        continue

                    # 승인된 항목은 _is_approved 안에서 이미 목록에서 빠졌다.
                    if self.pending is not None and self.pending.empty():
                        self.pending = None
                    self._consume_postponed(call.name, arguments)
                    arguments = {**arguments, "confirm": True}

                result = self.toolbox.call(call.name, arguments)

                trace.append({
                    "tool": call.name,
                    "arguments": arguments,
                    "result": result,
                })
                self._remember_results(call.name, result, arguments)
                if not result.get("success"):
                    failed_signatures.add(signature)
                elif call.name in STATE_CHANGING:
                    completed_mutations.add(signature)
                if self.plan is not None:
                    self.plan.advance(call.name, result)

                # --- ⑤ 결과를 대화에 덧붙인다 ---
                messages.append(make_tool_result_message(call, result))
                if self.plan is not None:
                    # [현재 상태] 는 턴 시작에 한 번만 붙는다. 같은 턴 안에서 8단계를 이어가는
                    # 요청(예산 안에서 4종 코디)은 진행·남은 예산을 매 걸음 다시 봐야 하므로
                    # Tool 결과 뒤에 계획 줄을 한 줄 덧붙인다.
                    progress = self.plan.state_line(cart_total=self.store.view_cart()["total"])
                    messages[-1]["content"] = f"{messages[-1]['content']}\n[{progress}]"

            # 확인 대기가 생겼으면 여기서 멈추고 사용자 답을 기다린다.
            if holding:
                items = holding["items"]

                # --- 확인 버튼을 띄우기 전에 한 번 검증한다 ---
                # 최종 답 직전의 검증기는 확인 대기로 끝나는 턴에는 돌지 않았다. 그래서
                # "둘 다 결제" 에서 담기 하나가 재고로 실패한 채 결제 확인이 그대로 떴다(15번).
                # 아직 아무것도 실행되지 않은 시점이므로 retry 가 안전하다: 확인 대기를 열지 않고
                # 모델에게 보완 지시를 준 뒤 루프를 이어간다. 비용을 위해 이번 턴에 실패한
                # Tool 이 있을 때만 돈다.
                # 실패로 세는 것: 재고 없음·잘못된 인자처럼 **사용자 의도가 미달된** 실패만.
                # 취소 불가·반품 기간 지남은 store 의 정책 판정(정상 거절)이라 세지 않는다 —
                # 19번에서 "취소 안 되면 이유 알려줘" 의 정상 거절을 실패로 세어 검증기가 돌고,
                # retry 가 답 순서를 흔들어 배송 완료 목록이 빠졌다(3/3 회귀).
                failed_here = [entry for entry in trace
                               if not entry.get("internal")
                               and entry.get("tool") != holding["tool"]
                               and entry.get("tool") not in CONFIRM_REQUIRED
                               and not (entry.get("result") or {}).get("success")]
                if (self.adaptive_mode and config.VERIFY_BEFORE_CONFIRM and failed_here
                        and verifier_retries < config.MAX_VERIFIER_RETRIES):
                    labels = [item["label"] for item in items]
                    decision = self._verify_outcome(user_message, "", trace, about_to_confirm=labels)
                    # 결제 직전에는 retry 를 허용하지 않는다. 15번에서 retry 지시를 받은 모델이
                    # 사용자가 고르지 않은 다른 상품을 대신 담아 2건(411,000원)을 결제한 일이 있었다.
                    # 결제 앞의 "보완" 은 반드시 사용자에게 묻는 것으로만 한다.
                    if (decision and decision["verdict"] == "retry" and holding["tool"] == "buy_from_cart"):
                        question = ("요청하신 상품 중 담지 못한 것이 있습니다: "
                                    + "; ".join(decision["missing_requirements"])
                                    + "\n다른 상품으로 대신 담을까요, 아니면 지금 장바구니에 있는 것만 결제할까요?")
                        decision = {**decision, "verdict": "ask_user", "question": question}
                    if decision and decision["verdict"] == "retry":
                        verifier_retries += 1
                        messages.append({
                            "role": "user",
                            "content": (
                                "[결과 검증] 확인 버튼을 띄우기 전에 아직 충족되지 않은 요구가 있습니다: "
                                + "; ".join(decision["missing_requirements"])
                                + "\n다음 지시만 안전하게 보완하세요: "
                                + decision["next_instruction"]
                                + "\n확인 대기는 열리지 않았습니다. 보완이 끝나면 원래 하려던 작업"
                                  f"({holding['tool']})을 다시 부르세요. "
                                  "최종 답에는 원래 요청의 모든 부탁(조회해서 알려달라고 한 목록 포함)을 담으세요. "
                                  "이미 성공한 상태 변경 Tool은 다시 실행하지 말고, "
                                  "사용자가 명시한 조건을 임의로 완화하지 마세요."
                            ),
                        })
                        continue
                    if decision and decision["verdict"] == "ask_user":
                        prefix = self._result_report(trace, always=True).strip()
                        prefix = (prefix + "\n\n") if prefix else ""
                        return prefix + decision["question"], trace
                # 줄바꿈 하나는 마크다운에서 합쳐진다. 항목이 여럿이면 한 문단으로
                # 붙어 버려서 몇 건인지 보이지 않는다.
                summary = "\n\n".join(item["label"] for item in items)
                if self.approve_by_button:
                    summary += ("\n\n아래 버튼으로 선택해 주세요. "
                                "되돌릴 수 없는 작업이라 채팅 답변으로는 진행되지 않습니다.")
                elif len(items) > 1:
                    summary += "\n(전부 진행하시려면 한 번에 답해 주세요)"
                self.pending = PendingAction(tool=holding["tool"], items=items,
                                             summary=summary, turn=self.turn)
                if self.last_request and self.last_request["turn"] == self.turn:
                    # 이 요청이 승인 버튼으로 끊겼다. 승인 뒤 continue_plan 이 남은 부탁을 잇는다.
                    # 단, 이 턴에 부른 Tool 이 확인 대상 하나뿐이고("결제해줘", "어반 러너 빼줘")
                    # 요청 문장에 이어지는 말("~하고", "그리고", "대신")도 없으면 남은 부탁이
                    # 없다고 보고 이어가지 않는다. 승인마다 모델을 한 번 더 부르는 비용을 아낀다.
                    lookups = {"search_order", "search_product", "get_order", "cancel_possible",
                               "return_possible", "view_cart", "make_plan", "manage_preferences"}
                    others = [entry for entry in trace
                              if entry.get("tool") != holding["tool"] and not entry.get("internal")
                              and entry.get("tool") not in lookups]
                    text = self.last_request["text"]
                    # "~하고", "~빼고", "~담고," 처럼 '고' 로 이어지는 말, 그리고 접속어.
                    connective = bool(re.search(r"[가-힣]고[\s,]", text)) or any(
                        word in text for word in
                        ("그리고", "그 다음", "그다음", "다음에", "대신", "그리구",
                         "이어서", "후에", "뒤에", "도 ", "까지", "랑 "))
                    self.last_request["interrupted"] = bool(others) or connective

                # 같은 턴에 조회한 것(재고·주문 상태)이 있으면 확인 질문보다 먼저 답한다.
                # 그렇지 않으면 그 답은 어디에도 나타나지 않는다.
                spoken = self._answer_before_confirm(messages, trace, message.get("content"))
                spoken = (spoken + "\n\n") if spoken else ""

                # 확인을 묻기 전에, 이번 턴에 이미 실행된 것이 있으면 먼저 알린다.
                #
                # always=True 인 이유: 평소에는 성공 한 건이면 보고를 생략하지만
                # 여기서는 생략하면 안 된다. "B 담고 A 빼줘" 에서 B 는 이미 담겼는데
                # 화면에는 A 삭제 확인만 뜬다. 그 상태로 사용자가 거절하면
                # 아무 일도 없었다고 오해한다.
                prefix = self._result_report(trace, always=True).strip()
                prefix = spoken + ((prefix + "\n\n") if prefix else "")

                if postponed:
                    # 미룬 작업을 기억해 둔다. 안 그러면 사용자가 함께 부탁한 일이
                    # 조용히 사라진다. ("2개 빼고 주문까지" 에서 주문이 증발했다)
                    self.postponed = {"items": postponed, "turn": self.turn}
                    names = ", ".join(item["tool"] for item in postponed)
                    return (prefix + f"{summary}\n"
                            f"(먼저 이것부터 확인할게요. 승인해 주시면 이어서 "
                            f"{len(postponed)}건을 더 진행합니다: {names})"), trace
                return prefix + summary, trace

            # --- ⑥ 다시 ① 로. 모델이 결과를 보고 다음 행동을 정한다 ---

        # 루프를 다 돌았는데도 안 끝난 경우.
        # 모델이 같은 Tool 을 계속 부르는 상황이 실제로 자주 발생한다.
        return (
            "요청을 처리하는 데 시간이 너무 오래 걸립니다. "
            "조건을 조금 더 구체적으로 알려주시겠어요?" + self._result_report(trace, interrupted=True),
            trace,
        )


# ======================================================================
# 터미널에서 에이전트만 따로 테스트
#
#     python3 agent.py
#     python3 agent.py "어제 주문한 운동화 취소해줘"
#
# Streamlit 없이 돌려볼 수 있어야 디버깅이 훨씬 빠릅니다.
# ======================================================================

if __name__ == "__main__":
    import sys

    from store import Store

    print(config.describe())
    print()

    question = sys.argv[1] if len(sys.argv) > 1 else (
        "15만원 이하 검은색 운동화 중 270 사이즈 재고가 있는 상품 3개를 찾아 비교하고, "
        "가장 적합한 상품을 장바구니에 넣어줘."
    )

    agent = ShoppingAgent(Store())
    print(f"사용자: {question}\n")

    answer, trace = agent.run(question)

    if trace:
        print("--- Tool 호출 기록 ---")
        for step, entry in enumerate(trace, 1):
            arguments = json.dumps(entry["arguments"], ensure_ascii=False)
            print(f"  [{step}] {entry['tool']}({arguments})")
            print(f"      -> {str(entry['result'])[:200]}")
        print()

    print(f"에이전트: {answer}")
