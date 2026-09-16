"""계획 계층 — 복합 요청을 먼저 단계로 쪼개고, 그 단계가 실제로 지켜지는지 앱이 센다.

왜 있는가.

    지금 에이전트는 "결과를 보고 다음 한 걸음" 을 반복한다(ReAct).
    "270 검은 운동화 3개 비교해서 제일 싼 거 담고 어제 주문은 취소" 같은
    요청은 걸음이 넷인데, 모델이 둘째 걸음쯤에서 원래 요청을 잊거나
    같은 Tool 을 다시 부르는 일이 있다.

    계획 모드에서는 모델이 먼저 make_plan 으로 "무엇을 어떤 순서로 부를지" 를
    적고, 앱이 그 계획을 **상태 블록에 매 턴 다시 보여 준다**. 모델이 지금 몇
    단계까지 왔는지를 기억이 아니라 눈앞의 글로 안다.

무엇을 하지 않는가.

    계획대로 강제 실행하지 않는다. 계획은 모델이 스스로 세운 메모이고,
    Tool 을 어떤 인자로 부를지는 여전히 매 걸음 결과를 보고 정한다.
    계획과 다른 Tool 을 불러도 막지 않는다 — 검색 결과가 비었으면 계획을
    바꿔야 하는 게 맞기 때문이다. 앱은 어긋났다는 사실만 상태 블록에 적는다.

    되돌릴 수 없는 작업의 승인 절차(PendingAction)는 이 파일과 무관하게
    그대로 적용된다. 계획에 있다고 해서 확인 없이 실행되는 일은 없다.

켜고 끄기: config.PLAN_MODE (기본 꺼짐). 실험(experiments/eval_plan.py)이
같은 요청을 켜고/꺼서 두 번 돌려 비교한다.
"""

from __future__ import annotations

import time

MAX_STEPS = 10
MIN_STEPS = 2

MAKE_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "make_plan",
        "description": (
            "계획은 가장 효율적으로 수립합니다. Tool 을 가장 효율적인 개수로 부릅니다."
            "여러 단계가 필요한 요청을 처리하기 전에 순서를 적어 둡니다. "
            "Tool 을 세 번 이상 불러야 끝나는 요청(예: 찾고 → 비교하고 → 담기, "
            "주문 찾고 → 취소하고 → 다른 상품 담기)에서 **가장 먼저 한 번** 부릅니다. "
            "steps 배열에는 앞으로 호출할 Tool 을 실행 순서대로 2개 이상 8개 이하로 넣고, "
            "각 단계에는 Tool 하나만 지정합니다. "
            "한 번의 Tool 로 끝나는 요청이나 단순 질문에는 부르지 않습니다. "
            "총 예산이 있는 요청(예: 50만원으로 코디)이면 budget 에 총액(원)을 넣고, "
            "각 단계의 why 에 그 단계에 쓸 금액 배분을 적습니다(예: '아우터 20만원 이내'). "
            "이 Tool 은 아무것도 실행하지 않습니다. 계획을 적은 뒤 1단계 Tool 을 실제로 부르세요. "
            "계획은 매 요청의 [현재 상태] 에 진행 상황(예산이 있으면 남은 금액)과 함께 다시 보입니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "사용자 요청을 한 문장으로. 예: '검은 운동화 3개 비교 후 가장 싼 것 담기'",
                },
                "budget": {
                    "type": "integer",
                    "description": "총 예산(원). 사용자가 총액을 말했을 때만. 예: 500000",
                },
                "steps": {
                    "type": "array",
                    "minItems": MIN_STEPS,
                    "maxItems": MAX_STEPS,
                    "description": (
                        f"앞으로 호출할 Tool 을 계획 실행 순서대로 나열한 배열입니다. "
                        f"{MIN_STEPS}개 이상 {MAX_STEPS}개 이하의 단계를 넣고, "
                        "각 단계에는 Tool 하나만 지정합니다."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string",
                                     "description": "부를 Tool 이름. 목록에 있는 이름만."},
                            "why": {"type": "string",
                                    "description": "이 단계로 얻으려는 것. 짧게."},
                        },
                        "required": ["tool", "why"],
                    },
                },
            },
            "required": ["goal", "steps"],
        },
    },
}

# 시스템 프롬프트 뒤에 붙는 규칙. 계획 모드일 때만.
PLAN_PROMPT = """
계획 세우기:
- Tool 을 세 번 이상 불러야 끝나는 요청은 **가장 먼저 make_plan 을 한 번** 부르고,
  그 다음 요청부터 1단계 Tool 을 실제로 부릅니다. make_plan 만 부르고 멈추지 마세요.
- 단계는 "Tool 하나 = 한 단계" 입니다. 되돌릴 수 없는 Tool(장바구니 빼기, 결제, 취소,
  반품)은 확인 절차가 붙으므로 가능하면 마지막 단계들에 둡니다.
- [현재 상태] 의 "계획" 줄이 지금 몇 단계까지 왔는지 알려 줍니다. 그 다음 단계를
  진행하세요. 끝난 단계를 다시 부르지 마세요.
- 단계 결과가 계획과 맞지 않으면(검색 결과 없음, 주문 여러 건) 계획을 고집하지 말고
  사용자에게 묻거나 조건을 고치세요. 필요하면 make_plan 을 다시 불러 계획을 바꿉니다.
- 한 번의 Tool 로 끝나는 요청, 단순 질문, 확인 대기에 대한 답에는 make_plan 을 부르지 않습니다.
- 여러 종류(아우터·상의·하의·신발 등)를 한 예산 안에서 맞추는 요청은 종류마다
  search_product → add_to_cart 를 한 단계씩 넣고, [현재 상태] 계획 줄의 **남은 예산** 안에서
  다음 상품을 고릅니다. 마지막 종류에서 예산이 넘으면 더 싼 상품으로 바꿔 담습니다.
- 사용자가 "하나씩 보여줘", "상의부터" 처럼 단계마다 확인을 원하면, 한 종류를 보여 준 뒤 멈추고
  다음 턴에 계획 줄의 ◀ 다음 단계를 이어갑니다. 계획은 턴이 바뀌어도 유지됩니다.
"""


def validate_plan(arguments, known_tools):
    """모델이 보낸 make_plan 인자를 검사해 (steps, error) 를 돌려준다.

    모델은 없는 Tool 이름, 빈 배열, 문자열 하나를 보낼 수 있다.
    여기서 걸러야 Plan 이 늘 같은 모양을 가진다.
    """
    if not isinstance(arguments, dict):
        return None, "make_plan 인자는 객체여야 합니다."
    goal = str(arguments.get("goal") or "").strip()
    raw = arguments.get("steps")
    if not isinstance(raw, list) or len(raw) < MIN_STEPS:
        return None, f"steps 는 {MIN_STEPS}개 이상의 배열이어야 합니다. 한 단계면 계획 없이 바로 Tool 을 부르세요."
    if len(raw) > MAX_STEPS:
        return None, f"steps 는 {MAX_STEPS}개까지입니다. 요청을 나눠 처리하세요."

    budget = arguments.get("budget")
    try:
        budget = int(budget) if budget not in (None, "") else None
    except (TypeError, ValueError):
        return None, "budget 은 원 단위 정수여야 합니다."
    if budget is not None and budget <= 0:
        return None, "budget 은 0보다 커야 합니다."

    steps = []
    for index, item in enumerate(raw, 1):
        if isinstance(item, str):
            item = {"tool": item, "why": ""}
        if not isinstance(item, dict):
            return None, f"{index}단계가 객체가 아닙니다."
        tool = str(item.get("tool") or "").strip()
        if tool == "make_plan":
            return None, "계획 안에 make_plan 을 넣을 수 없습니다."
        if tool not in known_tools:
            return None, (f"{index}단계의 '{tool}' 은 없는 Tool 입니다. "
                          f"사용 가능한 Tool: {', '.join(sorted(known_tools))}")
        steps.append({"tool": tool, "why": str(item.get("why") or "").strip()[:80],
                      "status": "todo"})
    return {"goal": goal[:120], "steps": steps, "budget": budget}, None


class Plan:
    """세운 계획과 진행 상황. 앱이 Tool 실행을 볼 때마다 advance() 로 표시를 옮긴다."""

    def __init__(self, goal, steps, turn, created_at=None, budget=None):
        self.goal = goal
        self.budget = budget         # 총 예산(원). 없으면 None
        self.steps = steps           # [{"tool", "why", "status": todo|done|failed}]
        self.turn = turn
        self.created_at = created_at if created_at is not None else time.time()
        self.off_plan = []           # 계획에 없던 Tool 호출 이름들(어긋남 기록)

    # ----- 진행 -----
    def cursor(self):
        """다음에 할 단계의 번호(0부터). 실패한 단계도 "다시 할 차례" 로 본다. 전부 끝났으면 len(steps)."""
        for index, step in enumerate(self.steps):
            if step["status"] != "done":
                return index
        return len(self.steps)

    def done(self):
        """모든 단계가 성공으로 끝났는가. 실패한 단계가 남아 있으면 끝난 것이 아니다."""
        return all(step["status"] == "done" for step in self.steps)

    def advance(self, tool_name, result):
        """Tool 하나가 실행됐다. 계획의 어느 단계인지 찾아 표시한다.

        규칙은 단순하다: 아직 안 한 단계 중 **가장 앞의 같은 이름**을 끝낸 것으로 본다.
        미리보기(requires_confirmation)는 아직 실행이 아니므로 표시하지 않는다.
        계획에 없는 이름이면 off_plan 에 적는다 — 막지 않는다.
        """
        data = (result or {}).get("data") or {}
        if isinstance(data, dict) and data.get("requires_confirmation"):
            return
        success = bool((result or {}).get("success"))
        for step in self.steps:
            if step["status"] == "todo" and step["tool"] == tool_name:
                step["status"] = "done" if success else "failed"
                return
        # 이미 끝낸 단계를 다시 불렀거나 계획에 없던 Tool. 재시도(failed 뒤 같은 Tool)는 봐준다.
        for step in self.steps:
            if step["status"] == "failed" and step["tool"] == tool_name and success:
                step["status"] = "done"
                return
        self.off_plan.append(tool_name)

    # ----- 표시 -----
    MARK = {"todo": "□", "done": "☑", "failed": "☒"}

    def state_line(self, cart_total=None):
        """[현재 상태] 에 넣는 한 줄. 모델이 읽는다. cart_total 이 있고 예산이 있으면 남은 금액도."""
        parts = []
        cursor = self.cursor()
        for index, step in enumerate(self.steps):
            mark = self.MARK[step["status"]]
            arrow = " ◀ 다음" if index == cursor else ""
            parts.append(f"{index + 1}) {mark} {step['tool']}{arrow}")
        line = f"계획 \"{self.goal}\": " + "  ".join(parts)
        if self.done():
            line += "  — 전부 끝났습니다. 결과를 정리해 답하세요."
        elif any(step["status"] == "failed" for step in self.steps):
            line += "  (☒ 는 실패한 단계입니다. 조건을 고쳐 다시 하거나, 안 되면 사용자에게 알리세요)"
        if self.off_plan:
            line += f"  (계획에 없던 호출: {', '.join(self.off_plan[-3:])})"
        if self.budget:
            if cart_total is None:
                line += f"\n  예산 {self.budget:,}원"
            else:
                left = self.budget - cart_total
                line += (f"\n  예산 {self.budget:,}원 · 장바구니 합계 {cart_total:,}원 · "
                         + (f"남은 예산 {left:,}원" if left >= 0
                            else f"**예산 초과 {-left:,}원 — 더 싼 상품으로 바꿔 담으세요**"))
        return line

    def checklist(self):
        """화면(도크)이 그리는 모양."""
        return {"goal": self.goal, "turn": self.turn, "budget": self.budget,
                "steps": [{"tool": s["tool"], "why": s["why"], "status": s["status"]}
                          for s in self.steps],
                "off_plan": list(self.off_plan)}

    # ----- 저장 -----
    def to_dict(self):
        return {"goal": self.goal, "steps": self.steps, "turn": self.turn,
                "created_at": self.created_at, "off_plan": self.off_plan,
                "budget": self.budget}

    @classmethod
    def from_dict(cls, data):
        plan = cls(data.get("goal", ""), list(data.get("steps") or []),
                   int(data.get("turn") or 0), data.get("created_at"),
                   budget=data.get("budget"))
        plan.off_plan = list(data.get("off_plan") or [])
        return plan
