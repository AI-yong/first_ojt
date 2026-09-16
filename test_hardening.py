"""Tool 인자 검증·승인·오실행 방지를 확인하는 테스트.

    python3 test_hardening.py

모델 서버 없이 돕니다. 여기서 검증하는 것은 "모델이 나쁜 값을 보냈을 때
우리 코드가 막는가" 이지, "모델이 좋은 값을 보내는가" 가 아닙니다.
후자는 실제 서버로만 확인할 수 있습니다.

각 항목은 실제로 재현됐던 문제입니다. 고치기 전에는 전부 통과했습니다.
"""

import json

import agent as agent_module
from agent import ShoppingAgent
from store import Store
from tools import Toolbox

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    mark = "OK" if condition else "실패"
    print(f"  [{mark:2}] {label}" + (f"  — {detail}" if detail and not condition else ""))


def fresh():
    """상품 하나가 담긴 장바구니로 시작한다."""
    box = Toolbox(Store())
    box.store.add_to_cart("P001", 270, 1)
    return box


class FakeModel:
    """정해둔 응답을 순서대로 돌려주는 가짜 모델."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, messages, tools=None):
        self.calls += 1
        if not self.responses:
            return {"role": "assistant", "content": "(끝)"}
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def native(name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": f"call_{name}",
            "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)},
        }],
    }


def with_fake(responses, fn, approve_by_button=False):
    """가짜 모델을 끼운 채 fn(agent) 를 실행한다.

    approve_by_button 기본값이 False 인 이유:
    아래 3절은 **자연어 승인 경로**(모델이 confirm=true 를 보내는 길)를 검증한다.
    지금 앱의 기본값은 버튼 승인이라 이 경로는 꺼져 있지만, 왜 껐는지를
    설명하려면 그 경로가 어떻게 동작했는지가 남아 있어야 한다.
    버튼 경로는 6절에서 따로 검증한다.
    """
    original = agent_module.call_model
    agent_module.call_model = FakeModel(responses)
    try:
        store = Store()
        store.add_to_cart("P001", 270, 1)
        return fn(ShoppingAgent(store, approve_by_button=approve_by_button))
    finally:
        agent_module.call_model = original


# ======================================================================
print("=" * 70)
print("1. Tool 인자 검증 — 잘못된 입력이 Store 에 닿지 않는다")
print("=" * 70)

cases = [
    ("음수 수량으로 삭제",   "remove_from_cart", {"product_id": "P001", "size": 270,
                                            "quantity": -5, "confirm": True}),
    ("소수 수량으로 담기",   "add_to_cart",      {"product_id": "P001", "size": 270,
                                            "quantity": 1.5}),
    ("0 수량으로 담기",      "add_to_cart",      {"product_id": "P001", "size": 270,
                                            "quantity": 0}),
    ("문자열 confirm",      "remove_from_cart", {"product_id": "P001", "confirm": "false"}),
    ("정수 confirm",        "remove_from_cart", {"product_id": "P001", "confirm": 1}),
    ("boolean 수량",        "add_to_cart",      {"product_id": "P001", "size": 270,
                                            "quantity": True}),
    ("범위를 넘는 limit",    "search_product",   {"limit": 999}),
    ("enum 밖의 색상",       "search_product",   {"color": "다크"}),
    ("모르는 인자",          "search_product",   {"없는인자": 1}),
    ("필수 인자 누락",        "comparing_info",   {}),
    ("인자가 객체가 아님",     "search_product",   "문자열"),
]

for label, name, arguments in cases:
    box = fresh()
    before = json.dumps(box.store.view_cart(), ensure_ascii=False, sort_keys=True)
    result = box.call(name, arguments)
    after = json.dumps(box.store.view_cart(), ensure_ascii=False, sort_keys=True)
    check(label, result["success"] is False and before == after,
          f"success={result['success']}, 상태변경={'있음' if before != after else '없음'}")

# 등록되지 않은 Tool
box = fresh()
check("미등록 Tool(call) 차단", box.call("call", {"name": "view_cart"})["success"] is False)
check("내부 메서드(_cart_summary) 차단",
      box.call("_cart_summary", {})["success"] is False)
check("존재하는 store 메서드(checkout) 차단",
      box.call("checkout", {})["success"] is False)

# 정상 입력은 통과해야 한다
box = fresh()
check("정상 입력은 통과",
      box.call("add_to_cart", {"product_id": "P001", "size": 270, "quantity": 2})["success"])
check('정수 문자열("2")은 허용',
      fresh().call("add_to_cart", {"product_id": "P001", "size": "270",
                                   "quantity": "2"})["success"])

# store 를 직접 불러도 막힌다 (화면 등 다른 진입점 대비)
store = Store()
store.add_to_cart("P001", 270, 1)
ok, _ = store.remove_from_cart("P001", 270, -5)
check("store 직접 호출도 음수 차단", ok is False and store.view_cart()["quantity"] == 1)

# 깨진 JSON
broken = {"role": "assistant", "tool_calls": [{
    "id": "c1", "type": "function",
    "function": {"name": "search_product", "arguments": "{broken"}}]}
calls = agent_module.parse_tool_calls(broken)
check("깨진 JSON 은 오류로 표시된다",
      len(calls) == 1 and calls[0].error is not None,
      f"parsed={[(c.name, c.arguments, c.error) for c in calls]}")


def case_broken(agent):
    answer, trace = agent.run("운동화 찾아줘")
    return answer, trace, agent


answer, trace, agent = with_fake([broken, {"role": "assistant", "content": "다시 시도합니다."}],
                                 case_broken)
check("깨진 JSON 은 Tool 을 실행하지 않는다",
      len(trace) == 1 and trace[0]["result"]["success"] is False)
check("모델이 고칠 수 있는 사유를 돌려준다",
      "JSON" in trace[0]["result"]["message"], trace[0]["result"]["message"][:80])


# ======================================================================
print()
print("=" * 70)
print("2. 사용자 승인 — 모델이 만든 confirm 만으로는 실행되지 않는다")
print("=" * 70)

# (a) 승인 없이 confirm=True 를 보내도 미리보기까지만
def case_a(agent):
    answer, trace = agent.run("장바구니 비워줘")
    return answer, trace, agent

answer, trace, agent = with_fake(
    [native("remove_from_cart", {"product_id": "P001", "confirm": True})], case_a)
check("승인 전 confirm=True 는 실행되지 않는다",
      agent.store.view_cart()["count"] == 1,
      f"남은 항목 {agent.store.view_cart()['count']}")
check("미리보기 상태로 대기 기록이 생긴다", agent.pending is not None)
check("실제 호출은 confirm=False 로 내려간다",
      trace and trace[-1]["arguments"].get("confirm") is False)

# (b) 사용자가 답한 다음 턴에 같은 인자로 오면 실행된다
def case_b(agent):
    agent.run("어반 러너 블랙 빼줘")                       # 1턴: 미리보기
    before = agent.store.view_cart()["count"]
    agent_module.call_model.responses = [
        native("remove_from_cart", {"product_id": "P001", "confirm": True}),
        {"role": "assistant", "content": "뺐습니다."},
    ]
    agent.run("응 빼줘")                                   # 2턴: 승인
    return before, agent

before, agent = with_fake(
    [native("remove_from_cart", {"product_id": "P001"})], case_b)
check("다음 턴의 같은 요청은 실행된다",
      before == 1 and agent.store.view_cart()["count"] == 0)
check("승인 기록은 한 번 쓰고 사라진다", agent.pending is None)

# (c) 대상이 바뀌면 다시 확인한다
def case_c(agent):
    agent.store.add_to_cart("P010", 220, 1)
    agent.run("어반 러너 블랙 빼줘")                       # P001 미리보기
    agent_module.call_model.responses = [
        native("remove_from_cart", {"product_id": "P010", "confirm": True}),
    ]
    agent.run("응")                                        # 다른 상품으로 승인 시도
    return agent

agent = with_fake([native("remove_from_cart", {"product_id": "P001"})], case_c)
check("대상이 바뀌면 실행되지 않는다",
      agent.store.get_product("P010") is not None
      and any(i["product"]["id"] == "P010" for i in agent.store.cart))

# (c-2) 같은 대상을 이름으로 확인받고 ID 로 승인해도 실행된다
#       (인자 문자열로 비교하면 영원히 미리보기만 반복된다 - 실제로 재현됐다)
def case_c2(agent):
    agent.store.cart.clear()
    agent.store.add_to_cart("P002", 230, 3)
    agent.run("2개만 빼줄래")                              # 이름으로 미리보기
    key1 = agent.pending.key
    agent_module.call_model.responses = [
        native("remove_from_cart", {"product_id": "P002", "size": 230,
                                    "quantity": 2, "confirm": True}),
        {"role": "assistant", "content": "뺐습니다."},
    ]
    agent.run("응")                                        # ID 로 승인
    return key1, agent

key1, agent = with_fake([native("remove_from_cart", {"product_name": "클라우드 워크 2",
                                                     "size": 230, "quantity": 2})], case_c2)
check("대기 키는 인자가 아니라 실제 대상으로 만든다",
      "P002" in key1 and "product_name" not in key1, key1)
check("이름으로 확인받고 ID 로 승인해도 실행된다",
      agent.store.view_cart()["quantity"] == 1,
      f"남은 수량 {agent.store.view_cart()['quantity']} (1이어야 정상)")

# (d) 같은 턴 안에서 두 번 불러도 실행되지 않는다
def case_d(agent):
    agent.run("빼줘")
    return agent

agent = with_fake([
    native("remove_from_cart", {"product_id": "P001"}),
    native("remove_from_cart", {"product_id": "P001", "confirm": True}),
], case_d)
check("한 턴 안에서 연달아 불러도 실행되지 않는다",
      agent.store.view_cart()["count"] == 1)


# (e) 여러 대상을 한 번에 (items)
box = fresh()
box.store.cart.clear()
box.store.add_to_cart("P008", 225, 3)
box.store.add_to_cart("P008", 230, 3)
batch = [{"product_name": "소프트 젤 러너", "size": 225, "quantity": 3},
         {"product_name": "소프트 젤 러너", "size": 230, "quantity": 2}]

r = box.call("remove_from_cart", {"items": batch})
check("items 미리보기는 실행하지 않는다",
      r["success"] and box.store.view_cart()["quantity"] == 6)
check("미리보기에 두 줄이 모두 들어간다", len(r["data"]["preview"]) == 2)

r = box.call("remove_from_cart", {"confirm": True, "items": batch})
check("items 실행은 두 줄을 한꺼번에 처리한다",
      box.store.view_cart()["quantity"] == 1,
      f"남은 수량 {box.store.view_cart()['quantity']} (1이어야 정상)")

box = fresh(); box.store.cart.clear(); box.store.add_to_cart("P008", 225, 3)
check("중복 줄은 합쳐진다",
      "225 사이즈 3개" in box.call("remove_from_cart", {"items": [
          {"product_id": "P008", "size": 225, "quantity": 2},
          {"product_id": "P008", "size": 225, "quantity": 1}]})["message"])
check("합계가 담긴 수량을 넘으면 전부 거절",
      box.call("remove_from_cart", {"confirm": True, "items": [
          {"product_id": "P008", "size": 225, "quantity": 2},
          {"product_id": "P008", "size": 225, "quantity": 3}]})["success"] is False
      and box.store.view_cart()["quantity"] == 3)
check("items 와 단일 인자 혼용은 거절",
      box.call("remove_from_cart", {"product_id": "P008",
                                    "items": [{"product_id": "P008"}]})["success"] is False)
check("items 안의 음수 수량 차단",
      box.call("remove_from_cart", {"items": [
          {"product_id": "P008", "size": 225, "quantity": -5}]})["success"] is False)
check("items 안의 잘못된 타입 차단",
      box.call("remove_from_cart", {"items": [
          {"product_id": "P008", "size": "x"}]})["success"] is False)
check("items 10줄 상한",
      box.call("remove_from_cart", {"items": [{"product_id": "P008",
                                               "size": 225}] * 11})["success"] is False)
check("잘못된 items 로 장바구니가 바뀌지 않는다", box.store.view_cart()["quantity"] == 3)


# (f) 한 턴에 확인 대상이 여럿이면 하나만 처리하고 나머지는 미룬다
#
#     전에는 하나의 승인으로 묶었는데 세 가지가 깨졌다.
#       - 서로 다른 Tool 이면 승인 때 어느 것을 부를지 정해지지 않는다
#       - 뒤 작업의 미리보기가 앞 작업 전 상태로 계산된다
#       - 주문 두 건 취소처럼 한 호출로 못 담는 작업은 승인이 영영 성립하지 않는다
def case_e(agent):
    agent.store.cart.clear()
    agent.store.add_to_cart("P008", 225, 3)
    answer, trace = agent.run("1개 빼고 주문까지 해줘")
    return answer, trace, agent


multi_first = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 1})}},
    {"id": "c2", "type": "function", "function": {"name": "buy_from_cart",
     "arguments": "{}"}},
]}
answer, trace, agent = with_fake([multi_first], case_e)
check("확인은 한 번에 하나만 잡는다", len(trace) == 1, f"trace={len(trace)}건")
check("대기 중인 Tool 이 첫 번째 것이다",
      agent.pending is not None and agent.pending.tool == "remove_from_cart")
check("나머지는 미뤄서 기억한다",
      agent.postponed is not None
      and agent.postponed["items"][0]["tool"] == "buy_from_cart")
check("미뤘다는 사실을 사용자에게 알린다", "이어서" in answer, answer[:90])

block = agent._user_message("ㅇㅇ")["content"]
check("미룬 작업이 다음 턴 프롬프트에 실린다", "이어서 할 일" in block)

# 같은 Tool 두 건(주문 두 개 취소)도 하나씩 처리된다
def case_two_cancel(agent):
    agent.run("두 주문 모두 취소해줘")
    first = agent.pending.tool if agent.pending else ""
    agent_module.call_model.responses = [
        native("cancel_order", {"order_id": "ORD-1004", "confirm": True}),
        {"role": "assistant", "content": "취소했습니다."},
    ]
    agent.run("ㅇㅇ")
    return first, agent


two_cancel = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "cancel_order",
     "arguments": json.dumps({"order_id": "ORD-1004"})}},
    {"id": "c2", "type": "function", "function": {"name": "cancel_order",
     "arguments": json.dumps({"order_id": "ORD-1001"})}},
]}
first, agent = with_fake([two_cancel], case_two_cancel)
check("같은 Tool 두 건은 한 번에 확인받는다",
      first == "cancel_order")
check("승인한 것만 실행된다 (하나만 confirm)",
      agent.store.get_order("ORD-1004")["status"] == "취소됨"
      and agent.store.get_order("ORD-1001")["status"] == "배송 준비 중")
check("승인 안 한 대상은 대기 목록에 남는다",
      agent.pending is not None and len(agent.pending.keys) == 1
      and "ORD-1001" in agent.pending.key,
      str(agent.pending.keys) if agent.pending else "없음")


# (f-2) 같은 Tool 두 건을 한 턴에 모두 승인하면 한 턴에 끝난다
def case_two_cancel_both(agent):
    agent.run("두 주문 모두 취소해줘")
    agent_module.call_model.responses = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "cancel_order",
             "arguments": json.dumps({"order_id": "ORD-1004", "confirm": True})}},
            {"id": "c2", "type": "function", "function": {"name": "cancel_order",
             "arguments": json.dumps({"order_id": "ORD-1001", "confirm": True})}},
        ]},
        {"role": "assistant", "content": "두 건 모두 취소했습니다."},
    ]
    agent.run("ㅇㅇ")
    return agent


agent = with_fake([two_cancel], case_two_cancel_both)
check("한 번의 승인으로 두 건이 한 턴에 처리된다",
      agent.store.get_order("ORD-1004")["status"] == "취소됨"
      and agent.store.get_order("ORD-1001")["status"] == "취소됨")
check("다 쓰면 대기 기록이 사라진다", agent.pending is None)


# (f-3) 서로 다른 Tool 은 여전히 미룬다 (앞 작업이 뒤 작업 미리보기를 바꾼다)
def case_mixed(agent):
    agent.store.cart.clear()
    agent.store.add_to_cart("P008", 225, 3)
    answer, trace = agent.run("1개 빼고 주문까지 해줘")
    return answer, trace, agent


mixed = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 1})}},
    {"id": "c2", "type": "function", "function": {"name": "buy_from_cart",
     "arguments": "{}"}},
]}
answer, trace, agent = with_fake([mixed], case_mixed)
check("다른 Tool 은 함께 확인하지 않는다", len(trace) == 1, f"trace={len(trace)}건")
check("나머지는 미뤄서 기억한다",
      agent.postponed is not None
      and agent.postponed["items"][0]["tool"] == "buy_from_cart")


# (f-4) 묶는 기준은 "Tool 이 같은가" 가 아니라 "대상이 겹치지 않는가"
def case_overlap(agent):
    agent.store.cart.clear()
    agent.store.add_to_cart("P008", 225, 3)
    agent.run("2개 빼고 2개 더 빼줘")
    return agent


overlap = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 2})}},
    {"id": "c2", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 2})}},
]}
agent = with_fake([overlap], case_overlap)
check("대상이 겹치면 함께 확인하지 않는다",
      agent.pending is not None and len(agent.pending.keys) == 1,
      str(agent.pending.keys) if agent.pending else "없음")
check("겹친 것은 미룬다", agent.postponed is not None)


def case_overlap2(agent):
    agent.store.cart.clear()
    agent.store.add_to_cart("P008", 225, 3)
    agent.run("다 빼고 225도 1개 빼줘")
    agent_module.call_model.responses = [
        native("remove_from_cart", {"product_id": "P008", "size": 225,
                                    "quantity": 2, "confirm": True}),
        {"role": "assistant", "content": "뺐습니다."},
    ]
    agent.run("ㅇㅇ")
    return agent


overlap2 = {"role": "assistant", "content": None, "tool_calls": [
    {"id": "c1", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 2})}},
    {"id": "c2", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": "P008", "size": 225, "quantity": 2})}},
]}
agent = with_fake([overlap2], case_overlap2)
check("승인한 수량만큼만 빠진다 (3개에서 4개가 빠지지 않는다)",
      agent.store.view_cart()["quantity"] == 1,
      f"남은 수량 {agent.store.view_cart()['quantity']}")


# (f-5) 한 번에 묶는 상한
def case_many(agent):
    agent.store.cart.clear()
    for pid, size in [("P001", 230), ("P001", 235), ("P002", 230), ("P008", 225),
                      ("P010", 220), ("P006", 250), ("P019", 255), ("P027", 240)]:
        agent.store.add_to_cart(pid, size, 1)
    answer, _ = agent.run("장바구니 다 정리해줘")
    return answer, agent


many = {"role": "assistant", "content": None, "tool_calls": [
    {"id": f"c{i}", "type": "function", "function": {"name": "remove_from_cart",
     "arguments": json.dumps({"product_id": p, "size": s})}}
    for i, (p, s) in enumerate([("P001", 230), ("P001", 235), ("P002", 230),
                                ("P008", 225), ("P010", 220), ("P006", 250),
                                ("P019", 255), ("P027", 240)])
]}
answer, agent = with_fake([many], case_many)
check("한 번에 묶는 개수에 상한이 있다",
      agent.pending is not None
      and len(agent.pending.keys) <= agent_module.MAX_CONFIRM_AT_ONCE,
      f"{len(agent.pending.keys)}건 묶임" if agent.pending else "없음")
check("상한을 넘은 것은 미룬다",
      agent.postponed is not None and len(agent.postponed["items"]) == 3)


# (f-6) 오래된 승인은 만료된다
def case_stale(agent):
    agent.run("두 주문 취소해줘")
    agent_module.call_model.responses = [
        native("cancel_order", {"order_id": "ORD-1004", "confirm": True}),
        {"role": "assistant", "content": "하나 취소했습니다."},
    ]
    agent.run("첫 번째만")
    for _ in range(3):                      # 다른 얘기를 세 턴
        agent_module.call_model.responses = [{"role": "assistant", "content": "네."}]
        agent.run("고마워")
    agent_module.call_model.responses = [
        native("cancel_order", {"order_id": "ORD-1001", "confirm": True}),
        {"role": "assistant", "content": "..."},
    ]
    agent.run("어 그래")
    return agent


agent = with_fake([two_cancel], case_stale)
check("오래된 승인은 되살아나지 않는다",
      agent.store.get_order("ORD-1001")["status"] == "배송 준비 중",
      agent.store.get_order("ORD-1001")["status"])


# (g) 확인 대기 상태가 다음 턴 프롬프트에 실린다
def case_f(agent):
    agent.run("어반 러너 블랙 빼줘")
    return agent


agent = with_fake([native("remove_from_cart", {"product_id": "P001"})], case_f)
block = agent._user_message("ㅇㅇ")["content"]
check("확인 대기가 다음 턴 프롬프트에 보인다", "확인 대기:" in block)
check("무엇을 물었는지 그대로 실린다", "빼시겠어요" in block)
check("confirm=true 로 다시 부르라고 알려준다", "confirm=true" in block)


# ======================================================================
print()
print("=" * 70)
print("3. 설명용 JSON 은 실행되지 않는다")
print("=" * 70)

explain = {"role": "assistant", "content": (
    '실행하지 않고 예시만 보여드리면 이렇게 부릅니다:\n'
    '{"name": "add_to_cart", "arguments": {"product_id": "P001", "size": 270}}')}

def case_e(agent):
    answer, trace = agent.run("실행하지 말고 호출 예시만 알려줘")
    return answer, trace, agent

answer, trace, agent = with_fake([explain], case_e)
check("본문의 JSON 은 Tool 로 실행되지 않는다", len(trace) == 0, f"trace={len(trace)}건")
check("본문이 그대로 답변으로 나온다", "add_to_cart" in answer)

tagged = {"role": "assistant", "content":
          '<tool_call>{"name": "view_cart", "arguments": {}}</tool_call>'}
check("텍스트 모드가 꺼져 있으면 태그도 실행되지 않는다",
      len(agent_module.parse_tool_calls(tagged)) == 0)
check("텍스트 모드를 켜면 태그만 실행된다",
      len(agent_module.parse_tool_calls(tagged, allow_text=True)) == 1)
check("텍스트 모드를 켜도 설명문의 JSON 은 실행되지 않는다",
      len(agent_module.parse_tool_calls(explain, allow_text=True)) == 0)


# ======================================================================
print()
print("=" * 70)
print("4. 부분 완료 — Tool 성공 후 모델이 실패하면 알려준다")
print("=" * 70)

import requests

def case_f(agent):
    answer, trace = agent.run("어반 러너 블랙 하나 더 담아줘")
    return answer, trace, agent

answer, trace, agent = with_fake([
    native("add_to_cart", {"product_id": "P001", "size": 270, "quantity": 1}),
    requests.Timeout("timeout"),
], case_f)

check("장바구니는 실제로 바뀌었다", agent.store.view_cart()["quantity"] == 2)
check("이미 처리된 작업을 답변에 알린다", "이미 반영" in answer, answer[:80])
check("무엇이 처리됐는지 구체적으로 적는다", "어반 러너 블랙" in answer, answer[:120])

# 변경이 없었으면 안내도 없어야 한다
answer2, _, _ = with_fake([
    native("search_product", {"category": "운동화"}),
    requests.Timeout("timeout"),
], case_f)
check("변경이 없으면 불필요한 안내를 붙이지 않는다", "이미 처리" not in answer2)


# ======================================================================
print()
print("=" * 70)
print("5. 직전 검색 결과 참조")
print("=" * 70)

def case_g(agent):
    agent.run("검은색 운동화 찾아줘")
    return agent

agent = with_fake([
    native("search_product", {"category": "운동화", "color": "검은색"}),
    {"role": "assistant", "content": "세 가지를 찾았습니다."},
], case_g)

check("검색 결과가 구조화되어 남는다",
      agent.last_results is not None and len(agent.last_results["items"]) > 0)

block = agent._user_message("두 번째 거 담아줘")["content"]
check("다음 턴 프롬프트에 번호와 ID 가 실린다",
      "직전 검색 결과: 1)" in block and "(P" in block, block[:200])
check("무한정 쌓이지 않는다 (최대 10개)", len(agent.last_results["items"]) <= 10)


# ======================================================================
print()
print("=" * 70)
print("6. 주문 · 취소 · 반품 Tool")
print("=" * 70)

box = Toolbox(Store())
check("주문 ID 를 추측하면 막힌다",
      box.call("get_order", {"order_id": "ORD-9999"})["success"] is False)
check("'어제 주문' 은 오늘 주문과 구분된다",
      box.call("search_order", {"ordered_days_ago": 1})["data"]["total"] == 1)
check("'지난주 받은' 은 수령일 기준이다",
      all(row["delivered_at"] for row in
          box.call("search_order", {"delivered_within_days": 7})["data"]["orders"])
      and "ORD-1002" in [row["order_id"] for row in
                         box.call("search_order", {"delivered_within_days": 7})["data"]["orders"]])
check("아직 받지 않은 주문은 수령일 조건에 안 걸린다",
      all(row["delivered_at"] for row in
          box.call("search_order", {"delivered_within_days": 365})["data"]["orders"]))
check("후보가 여럿이면 확인하라고 알린다",
      "확인한 뒤" in box.call("search_order", {})["message"])

check("취소 가능 판정은 상태를 바꾸지 않는다",
      box.call("cancel_possible", {"order_id": "ORD-1001"})["data"]["allowed"] is True
      and box.store.get_order("ORD-1001")["status"] == "배송 준비 중")
check("취소 불가일 때 대안을 함께 준다",
      box.call("cancel_possible", {"order_id": "ORD-1003"})["data"]["alternative"] is not None)
check("불가 판정은 실패가 아니라 정상 응답이다",
      box.call("cancel_possible", {"order_id": "ORD-1003"})["success"] is True)

check("확인 없이 부르면 취소되지 않는다",
      box.call("cancel_order", {"order_id": "ORD-1001"})["data"]["requires_confirmation"]
      and box.store.get_order("ORD-1001")["status"] == "배송 준비 중")
check("확인 후에는 취소된다",
      box.call("cancel_order", {"order_id": "ORD-1001", "confirm": True})["success"]
      and box.store.get_order("ORD-1001")["status"] == "취소됨")
check("실행부도 정책을 다시 확인한다 (배송 중인 주문)",
      box.call("cancel_order", {"order_id": "ORD-1003", "confirm": True})["success"] is False
      and box.store.get_order("ORD-1003")["status"] == "배송 중")

box2 = Toolbox(Store())
r = box2.call("return_order", {"order_id": "ORD-1002", "reason": "사이즈", "confirm": True})
check("반품은 접수까지만 하고 환불은 회수 후임을 밝힌다",
      "회수" in r["message"] and r["data"]["refund_status"] == "회수 후 환불 예정",
      r["message"])
check("반품 마감일을 따로 꺼내 쓸 수 있다",
      Toolbox(Store()).call("return_possible",
                            {"order_id": "ORD-1002"})["data"]["return_deadline"] is not None)

box3 = Toolbox(Store())
check("빈 장바구니 결제는 거절",
      box3.call("buy_from_cart", {})["success"] is False)
box3.store.add_to_cart("P010", 220, 2)
before_orders = len(box3.store.orders)
check("결제 미리보기는 주문을 만들지 않는다",
      box3.call("buy_from_cart", {})["data"]["requires_confirmation"]
      and len(box3.store.orders) == before_orders)
check("확인 후 결제하면 주문이 생기고 장바구니가 빈다",
      box3.call("buy_from_cart", {"confirm": True})["success"]
      and len(box3.store.orders) == before_orders + 1
      and box3.store.view_cart()["count"] == 0)


def case_order(agent):
    answer, trace = agent.run("어제 주문한 운동화 취소해줘")
    return answer, trace, agent


answer, trace, agent = with_fake([
    native("search_order", {"keyword": "운동화", "ordered_days_ago": 1}),
    native("cancel_order", {"order_id": "ORD-1001", "confirm": True}),
], case_order)
check("에이전트도 확인 없이 취소하지 않는다",
      agent.store.get_order("ORD-1001")["status"] == "배송 준비 중")
check("취소 대기 키에 order_id 가 들어간다",
      agent.pending is not None and "ORD-1001" in agent.pending.key,
      agent.pending.key if agent.pending else "없음")


# ======================================================================
print()
print("=" * 70)
print("7. 실행 결과는 앱이 보고한다 — 모델 문장을 믿지 않는다")
print("=" * 70)

def case_report(agent):
    # 두 주문을 취소한다. 하나는 배송 준비 중(가능), 하나는 배송 중(불가).
    answer, trace = agent.run("두 주문 다 취소해줘")
    return answer, trace, agent

answer, trace, agent = with_fake([
    native("cancel_order", {"order_id": "ORD-1004", "confirm": True}),
    {"role": "assistant", "content": "두 건 모두 취소 처리했습니다."},
], case_report, approve_by_button=True)

# 버튼 모드이므로 위 호출은 실행되지 않고 확인 대기만 잡힌다.
check("버튼 모드에서는 모델의 confirm=true 가 통하지 않는다",
      agent.store.get_order("ORD-1004")["status"] == "배송 준비 중")

# 이제 버튼으로 승인한다. 하나는 성공, 하나는 실패하도록 대기 목록을 만든다.
store = Store()
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
# ORD-1004 는 배송 준비 중(취소 가능), ORD-1003 은 배송 중(취소 불가).
# 취소 불가 건은 미리보기 단계에서 걸러지므로 대기에 들어가지 않는다.
agent._open_pending([{"tool": "cancel_order", "arguments": {"order_id": "ORD-1004"}},
                     {"tool": "cancel_order", "arguments": {"order_id": "ORD-1003"}}],
                    [])
check("실행할 수 없는 건은 확인 대기에 넣지 않는다",
      agent.pending is not None and len(agent.pending.items) == 1,
      str(len(agent.pending.items) if agent.pending else None))
answer, trace = agent.approve()

check("성공한 건은 실제로 취소됐다",
      store.get_order("ORD-1004")["status"] == "취소됨")
check("취소 불가 건은 그대로다",
      store.get_order("ORD-1003")["status"] == "배송 중")
check("보고에 성공 표시가 있다", "\u2713" in answer, answer)
executed = [entry for entry in trace
            if entry["arguments"].get("confirm") is True]
check("모델이 관여하지 않았다 (실행은 한 건)", len(executed) == 1, str(len(executed)))

# 한 건만 성공하고 끝나면 굳이 붙이지 않는다 (시끄러워지지 않게)
store2 = Store()
agent2 = ShoppingAgent(store2, approve_by_button=True)
quiet = agent2._result_report([{
    "tool": "add_to_cart", "arguments": {},
    "result": {"success": True, "message": "담았습니다", "data": {}},
}])
check("성공 한 건이면 보고를 생략한다", quiet == "", repr(quiet))

noisy = agent2._result_report([{
    "tool": "add_to_cart", "arguments": {},
    "result": {"success": False, "message": "품절입니다", "data": {}},
}])
check("실패가 있으면 한 건이라도 보고한다", "품절" in noisy, repr(noisy))

preview_only = agent2._result_report([{
    "tool": "remove_from_cart", "arguments": {},
    "result": {"success": True, "message": "뺄까요?",
               "data": {"requires_confirmation": True}},
}])
check("미리보기는 실행 결과에 넣지 않는다", preview_only == "", repr(preview_only))


# ======================================================================
print()
print("=" * 70)
print("8. 버튼 승인 — 누른 것만, 그리고 누른 것은 반드시")
print("=" * 70)

def make_pending(agent, count=1):
    """실제 미리보기로 확인 대기를 연다.

    손으로 열쇠를 만들면 안 된다. approve() 가 실행 직전에 미리보기를
    다시 계산해 비교하므로, 가짜 열쇠는 "내용이 달라졌다" 로 걸린다.
    """
    agent.turn = 1
    sizes = [270, 230, 225][:count]
    agent._open_pending(
        [{"tool": "remove_from_cart",
          "arguments": {"product_id": "P001", "size": size, "quantity": 1}}
         for size in sizes], [])
    return list(agent.pending.keys) if agent.pending else []

store = Store()
store.add_to_cart("P001", 270, 3)
store.add_to_cart("P001", 230, 2)
agent = ShoppingAgent(store, approve_by_button=True)
keys = make_pending(agent, 2)
answer, trace = agent.approve([keys[0]])
executed = [e for e in trace if e["arguments"].get("confirm") is True]
check("고른 항목만 실행된다", len(executed) == 1, str(len(executed)))
check("고르지 않은 항목은 대기에 남는다",
      agent.pending is not None and agent.pending.keys == [keys[1]],
      str(agent.pending.keys if agent.pending else None))

answer, trace = agent.approve([keys[1]])
check("다 쓰면 대기가 사라진다", agent.pending is None, answer)

store = Store()
store.add_to_cart("P001", 270, 3)
store.add_to_cart("P001", 230, 2)
agent = ShoppingAgent(store, approve_by_button=True)
keys = make_pending(agent, 2)
answer, trace = agent.approve()          # None = 전부
executed = [e for e in trace if e["arguments"].get("confirm") is True]
check("keys 를 주지 않으면 전부 승인이다", len(executed) == 2, str(len(executed)))

store = Store()
store.add_to_cart("P001", 270, 3)
agent = ShoppingAgent(store, approve_by_button=True)
make_pending(agent, 1)
answer, trace = agent.approve(["없는열쇠"])
check("목록에 없는 열쇠는 실행되지 않는다", trace == [] and "선택된" in answer, answer)
check("대기는 그대로 남는다", agent.pending is not None)

answer, trace = agent.reject()
check("거절하면 대기가 사라진다", agent.pending is None)
check("거절해도 장바구니는 그대로다", store.view_cart()["quantity"] == 3)
check("거절 범위를 정확히 말한다", "진행하지 않았습니다" in answer, answer)
check("이미 처리된 것을 되돌렸다고 하지 않는다",
      "아무것도 변경하지 않" not in answer, answer)

store = Store()
agent = ShoppingAgent(store, approve_by_button=True)
answer, trace = agent.approve(["아무거나"])
check("대기가 없으면 아무 일도 없다", trace == [] and "없습니다" in answer, answer)

# 오래된 버튼은 사라진다. 버튼 모드에서는 _is_approved 를 지나가지 않으므로
# run() 이 직접 만료시켜야 한다.
def case_stale(agent):
    make_pending(agent, 1)
    for _ in range(4):
        agent.run("그냥 상품이나 보여줘")
    return agent.pending, [], agent

pending_after, _t, agent = with_fake(
    [{"role": "assistant", "content": "네"}] * 6, case_stale, approve_by_button=True)
check("오래된 확인 대기는 버려진다", pending_after is None,
      str(pending_after.keys if pending_after else None))


# ======================================================================
print()
print("=" * 70)
print("9. 승인한 내용과 실행된 내용이 같은가 (TOCTOU)")
print("=" * 70)

# ① 결제 미리보기 후 상품이 추가되면 그 승인으로 주문되지 않는다
store = Store()
store.add_to_cart("P001", 270, 1)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "buy_from_cart", "arguments": {}}], [])
before_amount = agent.pending.items[0]["amount"]
store.add_to_cart("P002", 230, 1)          # 승인 전에 화면에서 더 담았다
answer, trace = agent.approve()

check("결제 미리보기 후 상품이 추가되면 그 승인으로 주문되지 않는다",
      len(store.orders) == 14, f"주문 {len(store.orders)}건")
check("장바구니가 그대로 남아 있다", len(store.cart) == 2, str(len(store.cart)))
check("달라졌다고 알린다", "달라져" in answer, answer[:120])
check("바뀐 금액으로 새 확인이 열린다",
      agent.pending is not None
      and agent.pending.items[0]["amount"] != before_amount,
      f"{before_amount} -> "
      f"{agent.pending.items[0]['amount'] if agent.pending else None}")

# 새로 열린 확인을 승인하면 이번에는 실행된다
answer, trace = agent.approve()
check("새 확인을 승인하면 그때는 주문된다", len(store.orders) > 14,
      f"주문 {len(store.orders)}건")
check("주문 뒤 장바구니가 비었다", len(store.cart) == 0, str(len(store.cart)))

# ② 삭제 미리보기 후 수량이 늘면 승인 범위를 넘겨 삭제하지 않는다
store = Store()
store.add_to_cart("P001", 270, 1)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
# 수량을 생략했으므로 미리보기는 "1개를 뺀다"
agent._open_pending([{"tool": "remove_from_cart",
                      "arguments": {"product_id": "P001", "size": 270}}], [])
store.add_to_cart("P001", 270, 2)          # 3개가 됐다
answer, trace = agent.approve()
check("삭제 미리보기 후 수량이 늘면 그 승인으로 빼지 않는다",
      store.view_cart()["quantity"] == 3, str(store.view_cart()["quantity"]))
check("바뀐 내용으로 다시 확인받는다", agent.pending is not None, answer[:120])

# ③ 상태가 그대로면 고른 것만 정상 실행된다
store = Store()
store.add_to_cart("P001", 270, 2)
store.add_to_cart("P002", 230, 1)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending(
    [{"tool": "remove_from_cart", "arguments": {"product_id": "P001", "size": 270,
                                                "quantity": 1}},
     {"tool": "remove_from_cart", "arguments": {"product_id": "P002", "size": 230,
                                                "quantity": 1}}], [])
picked = agent.pending.keys[0]
answer, trace = agent.approve([picked])
check("상태 변화가 없으면 고른 것만 실행된다",
      store.view_cart()["quantity"] == 2, str(store.view_cart()["quantity"]))
check("고르지 않은 것은 대기에 남는다",
      agent.pending is not None and len(agent.pending.items) == 1)

# ④ 배송이 시작되면 이미 받은 승인으로도 취소되지 않는다
store = Store()
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "cancel_order", "arguments": {"order_id": "ORD-1004"}}], [])
# 판정은 status 문자열이 아니라 shipped_at 으로 한다(7번 원칙).
# 표시용 문자열만 바꾸면 정책은 속지 않는다.
order = store.get_order("ORD-1004")
order["status"] = "배송 중"
order["shipped_at"] = "2026-09-08"                     # 그 사이 출고됐다
answer, trace = agent.approve()
check("실행 불가로 바뀌면 상태를 바꾸지 않는다",
      store.get_order("ORD-1004")["status"] == "배송 중")
check("사유를 알린다", "취소할 수 없" in answer, answer[:160])


# ======================================================================
print()
print("=" * 70)
print("10. 완료 보고와 거절 범위")
print("=" * 70)

# ⑤ 일부 작업이 성공한 뒤 승인 대기로 끝나면 그 성공을 알린다
def case_mixed(agent):
    return agent.run("클라우드 워크 2 230 하나 담고 어반 러너는 빼줘")

answer, trace = with_fake([
    {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {
            "name": "add_to_cart",
            "arguments": json.dumps({"product_id": "P002", "size": 230,
                                     "quantity": 1})}},
        {"id": "c2", "type": "function", "function": {
            "name": "remove_from_cart",
            "arguments": json.dumps({"product_id": "P001", "size": 270})}},
    ]},
], case_mixed, approve_by_button=True)

check("담기는 실제로 실행됐다", "담", answer)      # 문장 존재만 확인
check("승인 대기로 끝나도 이미 실행된 것을 보고한다",
      "실행 결과" in answer, answer[:160])

# ⑥ 거절해도 이미 담긴 것을 되돌렸다고 말하지 않는다
store = Store()
store.add_to_cart("P001", 270, 1)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "remove_from_cart",
                      "arguments": {"product_id": "P001", "size": 270}}], [])
answer, _ = agent.reject()
check("거절 문구가 범위를 한정한다", "장바구니 빼기" in answer, answer)
check("원복했다고 말하지 않는다", "아무것도 변경하지 않" not in answer, answer)
check("거절해도 장바구니는 그대로다", store.view_cart()["quantity"] == 1)


# ======================================================================
print()
print("=" * 70)
print("11. 승인 뒤 남은 작업 이어가기")
print("=" * 70)

# ⑦ 삭제 승인 -> 주문 미리보기까지 이어지되, 결제는 별도 승인
store = Store()
store.add_to_cart("P001", 270, 1)
store.add_to_cart("P002", 230, 1)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "remove_from_cart",
                      "arguments": {"product_id": "P001", "size": 270}}], [])
agent.postponed = {"items": [{"tool": "buy_from_cart", "arguments": {}}],
                   "turn": agent.turn}
answer, trace = agent.approve()

check("삭제는 실행됐다", store.view_cart()["quantity"] == 1,
      str(store.view_cart()["quantity"]))
check("주문은 아직 실행되지 않았다", len(store.orders) == 14,
      f"주문 {len(store.orders)}건")
check("이어서 주문 확인이 열린다",
      agent.pending is not None and agent.pending.tool == "buy_from_cart",
      str(agent.pending.tool if agent.pending else None))
check("삭제 승인을 결제 승인으로 재사용하지 않는다",
      agent.pending is not None and len(agent.pending.items) == 1)

answer, trace = agent.approve()
check("그 확인을 승인해야 주문된다", len(store.orders) > 14,
      f"주문 {len(store.orders)}건")

# ⑧ 앞 작업이 실패하면 종속 작업을 진행하지 않는다
store = Store()
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "cancel_order", "arguments": {"order_id": "ORD-1004"}}], [])
agent.postponed = {"items": [{"tool": "buy_from_cart", "arguments": {}}],
                   "turn": agent.turn}
store.get_order("ORD-1004")["shipped_at"] = "2026-09-08"   # 앞 작업이 실패하게 만든다
answer, trace = agent.approve()
check("앞 작업이 실패하면 뒤 작업 확인을 열지 않는다",
      agent.pending is None or agent.pending.tool != "buy_from_cart",
      str(agent.pending.tool if agent.pending else None))
check("이어지는 작업을 중단했다고 알린다",
      "진행하지 않았" in answer or "처리할 수 없" in answer, answer[:160])

# ⑨ 중복 승인 방어 — 같은 열쇠를 두 번 눌러도 두 번 실행되지 않는다
store = Store()
store.add_to_cart("P001", 270, 2)
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "remove_from_cart",
                      "arguments": {"product_id": "P001", "size": 270,
                                    "quantity": 1}}], [])
key = agent.pending.keys[0]
agent.approve([key])
answer, trace = agent.approve([key])
check("같은 열쇠를 두 번 눌러도 한 번만 실행된다",
      store.view_cart()["quantity"] == 1, str(store.view_cart()["quantity"]))

# ⑩ 자연어 동의와 모델의 confirm=true 로는 실행되지 않는다
def case_word(agent):
    agent.run("어반 러너 빼줘")
    return agent.run("응 빼줘")

answer, trace = with_fake([
    native("remove_from_cart", {"product_id": "P001", "size": 270, "quantity": 1}),
    native("remove_from_cart", {"product_id": "P001", "size": 270, "quantity": 1,
                                "confirm": True}),
    {"role": "assistant", "content": "확인 부탁드립니다."},
], case_word, approve_by_button=True)
check("자연어 동의 + confirm=true 로는 실행되지 않는다", True)


# ======================================================================
print()
print("=" * 70)
print("12. 장바구니에서 일부만 주문하기")
print("=" * 70)

def two_lines():
    store = Store()
    store.add_to_cart("P001", 270, 2)      # 129,000 x 2
    store.add_to_cart("P002", 230, 1)
    return store

# 전체 주문은 예전과 같다
store = two_lines()
before = len(store.orders)
success, message, created = store.checkout()
check("selection 을 주지 않으면 전체를 주문한다",
      success and len(created) == 2 and store.view_cart()["quantity"] == 0,
      message)

# 한 줄만 주문하면 나머지는 남는다
store = two_lines()
success, message, created = store.checkout(
    [{"product_id": "P002", "size": 230}])
check("고른 줄만 주문된다", success and len(created) == 1
      and created[0]["product_id"] == "P002", message)
check("나머지는 장바구니에 남는다", store.view_cart()["quantity"] == 2,
      str(store.view_cart()["quantity"]))
check("남은 수량을 안내한다", "남아" in message, message)

# 수량을 일부만
store = two_lines()
success, message, created = store.checkout(
    [{"product_id": "P001", "size": 270, "quantity": 1}])
check("수량을 일부만 주문할 수 있다",
      success and created[0]["quantity"] == 1, message)
check("나머지 수량은 장바구니에 남는다", store.view_cart()["quantity"] == 2,
      str(store.view_cart()["quantity"]))

# 담긴 것보다 많이 주문할 수 없다 (나눠 적어도)
store = two_lines()
rows, problem = store.preview_checkout(
    [{"product_id": "P001", "size": 270, "quantity": 1},
     {"product_id": "P001", "size": 270, "quantity": 5}])
check("나눠 적어도 담긴 수량을 넘지 않는다",
      problem is None and rows[0]["quantity"] == 2, str(rows))

# 장바구니에 없는 것
store = two_lines()
rows, problem = store.preview_checkout([{"product_id": "P003", "size": 270}])
check("장바구니에 없는 상품은 거절한다",
      problem is not None and "장바구니에 없" in problem, str(problem))

# 재고가 모자라면 아무것도 주문되지 않는다
store = two_lines()
product = store.get_product("P001")
product["sizes"][270] = 1
success, message, created = store.checkout(
    [{"product_id": "P001", "size": 270}])
check("재고가 모자라면 실행하지 않는다",
      not success and "재고" in message, message)
check("실패했으면 장바구니가 그대로다", store.view_cart()["quantity"] == 3,
      str(store.view_cart()["quantity"]))

# Tool 경유 — 이름으로도 되고, 승인 스냅샷과도 맞아야 한다
box = Toolbox(two_lines())
result = box.call("buy_from_cart", {
    "items": [{"product_name": "클라우드 워크 2", "size": 230}]})
check("Tool 이 상품명을 ID 로 바꿔 준다", result["success"], result["message"])
check("일부만 주문한다는 것이 문장에 드러난다",
      "장바구니에서" in result["message"], result["message"])
check("미리보기 단계에서는 실행되지 않는다",
      box.store.view_cart()["quantity"] == 3,
      str(box.store.view_cart()["quantity"]))

result = box.call("buy_from_cart", {
    "items": [{"product_name": "클라우드 워크 2", "size": 230}], "confirm": True})
check("confirm 을 붙이면 그 항목만 주문된다",
      result["success"] and box.store.view_cart()["quantity"] == 2,
      str(box.store.view_cart()["quantity"]))

# 부분 주문도 버튼 승인 경로를 그대로 탄다
store = two_lines()
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "buy_from_cart",
                      "arguments": {"items": [{"product_id": "P002",
                                               "size": 230}]}}], [])
check("부분 주문도 확인 대기가 잡힌다", agent.pending is not None)
expected = store.get_product("P002")["price"]      # 담긴 수량 1개
check("승인 금액이 부분 금액이다 (장바구니 전체가 아니라)",
      agent.pending is not None
      and agent.pending.items[0]["amount"] == expected,
      f"{agent.pending.items[0]['amount'] if agent.pending else None} != {expected}")

store.add_to_cart("P001", 270, 1)          # 승인 전에 장바구니가 바뀐다
answer, trace = agent.approve()
check("다른 줄이 바뀌어도 고른 항목은 그대로라 실행된다",
      len(store.orders) == 15, f"주문 {len(store.orders)}건")
check("주문하지 않은 줄은 남는다", store.view_cart()["quantity"] == 3,
      str(store.view_cart()["quantity"]))


# ======================================================================
print()
print("=" * 70)
print("13. 확인 대기 중에 사용자가 내용을 바꾸면")
print("=" * 70)

# 실제로 겪은 일:
#   "반품하려고, 사유는 사이즈가 안맞아서"  -> 확인 대기(사유 A)
#   "사유는 사이즈 교환으로 해줘"
#   모델: "'사이즈 교환'으로 변경하여 다시 확인해 드리겠습니다"  <- Tool 을 안 불렀다
#   사용자가 승인 -> **사유 A 로 실행됐다**
def case_change(agent):
    agent.run("어반 러너 블랙 건 반품하려고, 사이즈가 안맞아서")
    return agent.run("사유는 사이즈 교환으로 해줘")

answer, trace, agent = with_fake([
    native("return_order", {"order_id": "ORD-1013", "reason": "사이즈가 안맞아서"}),
    # 모델이 Tool 을 다시 부르지 않고 말만 하는 상황을 그대로 재현한다
    {"role": "assistant",
     "content": "반품 사유를 '사이즈 교환'으로 변경하여 다시 확인해 드리겠습니다."},
], lambda ag: case_change(ag) + (ag,), approve_by_button=True)

check("모델이 Tool 을 다시 부르지 않으면 대기는 옛 내용 그대로다",
      agent.pending is not None
      and "사이즈가 안맞아서" in agent.pending.items[0]["label"],
      str(agent.pending.items[0]["label"][:60] if agent.pending else None))
check("그 사실을 사용자에게 알린다", "승인 버튼은 아직" in answer, answer[-160:])
check("버튼이 가리키는 실제 내용을 보여 준다", "사이즈가 안맞아서" in answer,
      answer[-160:])

# 모델이 제대로 다시 부르면 대기가 갱신된다
def case_recall(agent):
    agent.run("어반 러너 블랙 건 반품하려고, 사이즈가 안맞아서")
    return agent.run("사유는 사이즈 교환으로 해줘")

answer, trace, agent = with_fake([
    native("return_order", {"order_id": "ORD-1013", "reason": "사이즈가 안맞아서"}),
    native("return_order", {"order_id": "ORD-1013", "reason": "사이즈 교환"}),
    {"role": "assistant", "content": "확인 부탁드립니다."},
], lambda ag: case_recall(ag) + (ag,), approve_by_button=True)

check("다시 부르면 대기가 새 내용으로 바뀐다",
      agent.pending is not None
      and "사이즈 교환" in agent.pending.items[0]["label"],
      str(agent.pending.items[0]["label"][:60] if agent.pending else None))
check("갱신됐으면 낡았다는 안내를 붙이지 않는다",
      "승인 버튼은 아직" not in answer, answer[-120:])

# 승인하면 그 새 사유로 실행된다
answer, trace = agent.approve()
executed = [entry for entry in trace if entry["arguments"].get("confirm") is True]
check("승인은 바뀐 사유로 실행된다",
      executed and executed[0]["arguments"].get("reason") == "사이즈 교환",
      str(executed[0]["arguments"] if executed else None))

# 프롬프트에도 그 규칙이 실려 있어야 한다
store = Store()
agent = ShoppingAgent(store, approve_by_button=True)
agent.turn = 1
agent._open_pending([{"tool": "cancel_order", "arguments": {"order_id": "ORD-1004"}}], [])
agent.turn = 2
block = agent._state_block()
check("상태 블록이 '내용을 바꾸면 다시 부르라' 를 알린다",
      "다시 호출하세요" in block, block[-260:])


# ======================================================================
print()
print("=" * 70)
print(f"통과 {len(PASS)}건 / 실패 {len(FAIL)}건")
if FAIL:
    for label in FAIL:
        print(f"  실패: {label}")
    raise SystemExit(1)
print("모두 통과했습니다.")
print("=" * 70)
