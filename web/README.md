# web/ — SHOPMATE 프런트엔드

Streamlit 없이 도는 순수 프런트엔드입니다. 지금은 **목업 데이터**로 혼자 돌고,
나중에 `api.js` 한 파일만 바꾸면 실제 서버에 붙습니다.

## 띄우기

실서버와 함께 (기본):

```bash
python3 server.py            # 프로젝트 루트에서
# http://127.0.0.1:8000
```

`server.py` 가 화면과 `/api/*` 를 같은 포트에서 냅니다. 포트를 나누면 CORS 를
설정해야 하는데 이 프로젝트에서 배울 것이 없는 작업이라 한 서버로 묶었습니다.
`/api/chat` 은 로컬 모델 서버가 떠 있어야 동작합니다 (`.env` 참고).

목업만 (서버 없이 화면 확인):

```bash
python3 server.py            # 또는 cd web && python3 -m http.server 8600
# http://127.0.0.1:8000/?mock
```

`?mock` 이 붙으면 `api.js` 가 `mock.js` 를 씁니다. ES 모듈을 쓰므로
`index.html` 을 파일로 직접 열면 안 되고 반드시 서버로 띄워야 합니다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `index.html` | 뼈대만. 내용은 전부 JS 가 그립니다 |
| `styles.css` | 디자인 토큰(글자 6단계 · 무채색 + 포인트 1색) + 전체 스타일 |
| `art.js` | 카테고리 16종 상품 실루엣을 SVG 로 그림. 상품의 `color` 로 칠함 |
| `mock.js` | `store.py` 흉내. 상품 240개 · 주문 14건 생성, 검색·장바구니·결제·가짜 에이전트 |
| `api.js` | **어댑터.** 화면은 이 파일만 봄 |
| `app.js` | 렌더링과 이벤트 전부 |

## 실제 서버에 붙이기

`api.js` 의 `USE_MOCK = false` 로 바꾸고 아래 엔드포인트만 만들면 됩니다.
화면 코드(`app.js`)는 **한 줄도 바뀌지 않습니다.**

```
GET    /api/products?query&group&category&gender&color&min_price&max_price&sort
GET    /api/products/{id}
GET    /api/cart
POST   /api/cart          {product_id, size, quantity}
PATCH  /api/cart          {product_id, size, quantity}
DELETE /api/cart          {product_id, size}
POST   /api/checkout      {items?: [{product_id, size}]}   ← 선택 항목만 결제
GET    /api/orders
POST   /api/chat          {message} -> {reply, trace, products}
POST   /api/approve       {keys: [...]}
POST   /api/reject
```

응답 필드는 `seed.py` / `store.py` 와 같은 이름을 씁니다
(`id · name · brand · group · category · gender · color · material · price ·
rating · review_count · sizes · machine_washable · description`).

FastAPI 쪽은 기존 객체를 그대로 감싸면 됩니다.

```python
store = Store()
box   = Toolbox(store)
agent = ShoppingAgent(box)

@app.post("/api/chat")
def chat(body: ChatIn):
    answer, trace = agent.run(body.message)
    return {"reply": answer, "trace": trace,
            "products": products_from_trace(trace)}

@app.post("/api/approve")
def approve(body: ApproveIn):
    answer, trace = agent.approve(body.keys)
    return {"reply": answer, "trace": trace}
```

## 화면에 들어간 것

- 스티키 헤더 · 검색 · 카테고리 네비(밑줄 인디케이터)
- 히어로 캐러셀 — 자동 회전 5.2초, 좌우 화살표, 점 인디케이터
- 상품 그리드 — 반응형, 스켈레톤 로딩, 랭킹 뱃지, 찜, 호버 시 바로 담기, 품절 오버레이
- 상세 — 사이즈별 재고, 품절 취소선, 스펙 표
- 장바구니 — **줄마다 체크박스 → "선택 항목만 주문"** (대표 사례에서 인자를 추가한 그 동작)
- 주문 내역 — 상태 뱃지 5종
- 상담 도크 — 타이핑 인디케이터, **Tool 트레이스 접기**, 상품 미니 카드,
  **승인 / N번만 / 아니요 버튼**(되돌릴 수 없는 작업은 여기서만 실행)

## 아직 안 한 것

- 사용자 세션. 지금은 Store·Agent 가 프로세스에 하나뿐이라 단일 사용자
  데모입니다. 확인 대기(`PendingAction`)도 프로세스 메모리에 있어서,
  `/api/chat` 이 만들고 `/api/approve` 가 소비하는 것이 같은 프로세스라
  성립합니다. 여러 프로세스로 띄우거나 재시작하면 사라집니다.
  (`server.py` 의 docstring 에 자세히 적어 두었습니다)
- 모바일 폭에서 도크가 전체 화면을 덮는 처리
- 상품 이미지 (사진 대신 SVG 실루엣을 씁니다)
