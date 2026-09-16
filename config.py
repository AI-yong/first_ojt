"""설정 한 곳 모음.

서버 주소, 모델 이름, API 키를 .env 파일에서 읽습니다.
코드 어디에도 키를 하드코딩하지 않기 위한 파일입니다.

.env 는 .gitignore 에 등록되어 있어 절대 커밋되지 않습니다.
새로 클론한 사람은 .env.example 을 복사해서 값을 채우면 됩니다.

    cp .env.example .env

이 파일은 완성본입니다. 채울 TODO 가 없습니다.
"""

import json
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """.env 파일을 읽어 os.environ 에 채운다.

    python-dotenv 를 쓰지 않고 직접 파싱합니다. 의존성을 늘리지 않기 위해서이며,
    형식은 KEY=VALUE 한 줄에 하나, # 로 시작하면 주석입니다.

    이미 환경변수로 설정된 값은 덮어쓰지 않습니다.
    (터미널에서 export 한 값이 .env 보다 우선한다는 뜻)
    """
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")  # 따옴표로 감싼 값도 허용

        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(ENV_PATH)


# --- 로컬 모델 서버 설정 ---------------------------------------------------

LOCAL_API_BASE_URL = os.environ.get("LOCAL_API_BASE_URL", "http://127.0.0.1:4000/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "your-tool-calling-model")
LOCAL_API_KEY = os.environ.get("LOCAL_API_KEY", "")

# 모델 호출 기본값
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "120"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.2"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))

# 에이전트가 한 번의 사용자 요청에 대해 Tool 을 연달아 호출할 수 있는 최대 횟수.
# 무한 루프(같은 Tool 을 계속 부르는 상황)를 막는 안전장치입니다.
# 8 이었을 때 "예산 안에서 4종 코디" 처럼 검색·담기가 4번씩 이어지는 요청(8~9왕복)이
# 마지막 한 걸음 앞에서 끊겼다. 같은 호출 반복은 이제 agent 가 따로 막으므로 여유를 둔다.
MAX_TOOL_ITERATIONS = int(os.environ.get("MAX_TOOL_ITERATIONS", "12"))

# 의미 검색 결과에 적용할 코사인 유사도 하한입니다. 모델이 Tool 인자로 정하는
# 값이 아니라 검색 서비스 전체에 동일하게 적용되는 정책입니다.
# 현재 0.40은 실제 질의 표본의 점수 분포로 정한 임시값이며, 사람 평가 후 조정합니다.
SEMANTIC_MIN_SCORE = float(os.environ.get("SEMANTIC_MIN_SCORE", "0.40"))
if not -1.0 <= SEMANTIC_MIN_SCORE <= 1.0:
    raise ValueError("SEMANTIC_MIN_SCORE는 -1.0 이상 1.0 이하여야 합니다.")

# 서버가 tool_calls 필드를 지원하지 않아 본문 텍스트에서 Tool 호출을 읽어야 할 때만 켭니다.
# 기본은 꺼짐입니다. 켜 두면 모델이 설명으로 적은 JSON 예시까지 실행됩니다.
# ("실행하지 말고 호출 예시만 알려줘" 에 대한 답변 안의 JSON 이 실제로 실행됐습니다)
# 켤 때는 반드시 <tool_call>...</tool_call> 로 감싼 것만 인정합니다.
ALLOW_TEXT_TOOL_CALLS = os.environ.get("ALLOW_TEXT_TOOL_CALLS", "").strip().lower() in (
    "1", "true", "yes", "on")


# 되돌릴 수 없는 작업(장바구니 빼기, 주문 취소·반품, 결제)의 승인을
# 화면 버튼으로만 받을지 여부. 기본은 켜짐입니다.
#
# 끄면 예전처럼 모델이 사용자의 말을 읽고 confirm=true 를 붙입니다.
# 그 경로에서 "사용자가 승인한 범위"와 "실제로 실행된 범위"가 어긋나는 문제를
# 코드로 막을 수 없었습니다 (NOTES 18번 ④, 19번).
APPROVE_BY_BUTTON = os.environ.get("APPROVE_BY_BUTTON", "1").strip().lower() not in (
    "0", "false", "no", "off")

# 확인 대기(승인 버튼)가 유효한 시간(초). 지나면 버튼을 눌러도 실행되지 않고
# 다시 확인받습니다. HTTP 에는 "턴" 이 없어서 — 사용자가 아무 말 없이 10분 뒤
# 버튼을 누를 수 있어서 — 턴 수와 별개로 시간으로도 만료시킵니다.
PENDING_TTL_SECONDS = int(os.environ.get("PENDING_TTL_SECONDS", "300"))
if PENDING_TTL_SECONDS <= 0:
    raise ValueError("PENDING_TTL_SECONDS는 1초 이상이어야 합니다.")

# 계획 모드. 켜면 모델에게 make_plan Tool 이 추가되고, 세 단계 이상 걸리는 요청은
# 먼저 순서를 적은 뒤 진행합니다. 계획은 매 턴 [현재 상태] 에 진행 표시와 함께 보입니다.
# 기본 꺼짐. experiments/eval_plan.py 가 켜고/꺼서 같은 요청을 비교합니다.
PLAN_MODE = os.environ.get("PLAN_MODE", "0").strip().lower() in ("1", "true", "yes", "on")

# Tool 실행이 끝난 뒤 별도 검증 모델이 원래 요청과 실제 결과를 대조합니다.
# 부족한 조회처럼 안전하게 보완 가능한 경우에만 제한된 횟수로 다시 시도합니다.
# 계획 모드와는 독립적이며, make_plan/continue_plan의 실행 흐름을 바꾸지 않습니다.
ADAPTIVE_AGENT_MODE = os.environ.get("ADAPTIVE_AGENT_MODE", "1").strip().lower() not in (
    "0", "false", "no", "off")
MAX_VERIFIER_RETRIES = int(os.environ.get("MAX_VERIFIER_RETRIES", "1"))
# 되돌릴 수 없는 작업의 확인 버튼을 띄우기 **직전**에도 검증기를 돌립니다.
# 최종 답 직전에만 돌리면 확인 대기로 끝나는 턴(취소·반품·결제)은 검증을 건너뛰게 되어,
# "둘 다 결제" 인데 담기 하나가 재고로 실패한 채 결제 확인이 뜨는 경우를 잡지 못했습니다(15번).
# 비용을 아끼기 위해 이번 턴에 실패한 Tool 이 있을 때만 돕니다. ADAPTIVE_AGENT_MODE 가 켜져 있어야 합니다.
VERIFY_BEFORE_CONFIRM = os.environ.get("VERIFY_BEFORE_CONFIRM", "1").strip().lower() not in (
    "0", "false", "no", "off")
if not 0 <= MAX_VERIFIER_RETRIES <= 2:
    raise ValueError("MAX_VERIFIER_RETRIES는 0 이상 2 이하여야 합니다.")

# 사용자가 명시적으로 '기억해 달라'고 한 장기 쇼핑 선호만 세션 상태에 저장합니다.
USER_MEMORY_ENABLED = os.environ.get("USER_MEMORY_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off")

# 승인 버튼으로 끊긴 요청의 남은 부분을 승인 직후 이어갑니다(계획 없이도).
# "취소하고 흰 운동화 담아줘" 에서 승인 뒤 담기가 진행되지 않던 문제. 승인 한 번당 모델 호출 +1.
RESUME_AFTER_APPROVAL = os.environ.get("RESUME_AFTER_APPROVAL", "1").strip().lower() not in (
    "0", "false", "no", "off")

# 의미 유사도 기준(SEMANTIC_MIN_SCORE)을 통과한 상품이 이 수보다 적으면, 기준 아래에서
# 점수 순으로 채워 최소 이만큼은 보여준다. "크롭" 처럼 짧은 질의는 임베딩 점수가 전체적으로
# 낮아 정답 5개가 전부 기준 아래로 떨어져 0개가 나오는 일이 있었다(score_curves.md).
# 그때 결과가 적다는 것은 "상품이 없다" 가 아니라 "이 질의엔 기준이 안 맞는다" 는 신호다.
# 6 = 화면 그리드 한 줄. 통과가 이보다 많으면 아무 일도 하지 않는다.
SEMANTIC_MIN_RESULTS = int(os.environ.get("SEMANTIC_MIN_RESULTS", "6"))
if SEMANTIC_MIN_RESULTS < 0:
    raise ValueError("SEMANTIC_MIN_RESULTS는 0 이상이어야 합니다.")

# Dense가 고른 최대 50개를 질의와 상품 설명을 함께 읽는 cross-encoder로 재정렬한다.
RERANK_ENABLED = os.environ.get("RERANK_ENABLED", "1").strip().lower() not in (
    "0", "false", "no", "off")
RERANK_MODEL_NAME = os.environ.get(
    "RERANK_MODEL_NAME", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1")
# 실험에 사용한 모델 파일이 나중에 조용히 바뀌지 않도록 revision을 고정한다.
RERANK_MODEL_REVISION = os.environ.get(
    "RERANK_MODEL_REVISION", "1427fd652930e4ba29e8149678df786c240d8825")
RERANK_DEVICE = os.environ.get("RERANK_DEVICE", "").strip()
RERANK_BATCH_SIZE = int(os.environ.get("RERANK_BATCH_SIZE", "16"))
if RERANK_BATCH_SIZE <= 0:
    raise ValueError("RERANK_BATCH_SIZE는 1 이상이어야 합니다.")


def _load_extra_body():
    """서버별 추가 파라미터를 읽는다.

    OpenAI 표준에 없는 옵션을 요구하는 서버가 있습니다.
    이 프로젝트의 서버(LiteLLM 뒤에 Gemma 4 31B)는 thinking 모드가 기본 켜져 있어
    끄지 않으면 답이 길어지고 max_tokens 를 사고에 다 씁니다.
    enable_thinking 은 Gemma 4 공식 챗 템플릿의 변수입니다 (Qwen3 도 같은 이름을 씁니다).
    thinking 을 켜서 쓰려면 _assistant_message 가 reasoning_content 도 되돌려 보내야 합니다.

        EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}

    이런 걸 코드에 하드코딩하면 서버를 바꿀 때마다 코드를 고쳐야 하므로
    .env 로 빼둡니다. Ollama 로 갈아끼울 때는 이 줄만 지우면 됩니다.
    """
    raw = os.environ.get("EXTRA_BODY", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        print("[config] EXTRA_BODY 가 올바른 JSON 이 아닙니다. 무시합니다.")
        return {}


EXTRA_BODY = _load_extra_body()


def describe() -> str:
    """현재 설정을 사람이 읽을 수 있게 요약한다. 키는 가린다."""
    key_state = f"설정됨(길이 {len(LOCAL_API_KEY)})" if LOCAL_API_KEY else "없음"
    return (
        f"base_url = {LOCAL_API_BASE_URL}\n"
        f"model    = {MODEL_NAME}\n"
        f"api_key  = {key_state}\n"
        f"의미검색 최소점수 = {SEMANTIC_MIN_SCORE:.2f}\n"
        f"extra    = {json.dumps(EXTRA_BODY, ensure_ascii=False) if EXTRA_BODY else '없음'}\n"
        f"env 파일 = {'있음' if ENV_PATH.exists() else '없음 (.env.example 을 복사하세요)'}"
    )


if __name__ == "__main__":
    print(describe())
