/* ==========================================================================
   API 어댑터
   화면은 이 파일만 봅니다. 실제 서버가 준비되면 USE_MOCK 을 false 로 바꾸고
   아래 http 구현의 경로만 맞추면 화면 코드는 한 줄도 바뀌지 않습니다.

   서버 쪽에서 만들어야 할 엔드포인트
     GET  /api/products?query&productName&semanticQuery&group&category&gender&color&min_price&max_price&sort
     GET  /api/products/{id}
     GET  /api/cart
     POST /api/cart            {product_id, size, quantity}
     PATCH/api/cart            {product_id, size, quantity}
     DELETE /api/cart          {product_id, size}
     POST /api/checkout        {items?: [{product_id,size}]}   ← 선택 항목만 결제
     GET  /api/orders
     POST /api/chat            {message} -> {reply, trace, products, pending}
     POST /api/approve         {keys: [...]}
     POST /api/reject
   ========================================================================== */
import { mock } from "./mock.js";

/* 기본은 실서버입니다. 서버 없이 화면만 보려면 ?mock 을 붙이세요.
   (http://127.0.0.1:8000/?mock) */
export const USE_MOCK = new URLSearchParams(location.search).has("mock");
const BASE = "/api";

async function http(path, { method = "GET", body } = {}) {
  const res = await fetch(BASE + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}`);
  return res.status === 204 ? null : res.json();
}

const real = {
  products: (o = {}) => http("/products?" + new URLSearchParams(
    Object.fromEntries(Object.entries(o).filter(([, v]) => v != null && v !== "")))),
  product: (id) => http(`/products/${id}`),
  cart: () => http("/cart"),
  addToCart: (product_id, size, quantity = 1) =>
    http("/cart", { method: "POST", body: { product_id, size, quantity } }),
  setQuantity: (product_id, size, quantity) =>
    http("/cart", { method: "PATCH", body: { product_id, size, quantity } }),
  removeFromCart: (product_id, size) =>
    http("/cart", { method: "DELETE", body: { product_id, size } }),
  checkout: (items = null) => http("/checkout", { method: "POST", body: { items } }),
  orders: () => http("/orders"),
  /* 주문 화면의 취소·반품 버튼. 실행이 아니라 확인 대기를 연다 (응답은 chat 과 같은 모양). */
  orderAction: (order_id, kind, reason) =>
    http(`/orders/${encodeURIComponent(order_id)}/action`, { method: "POST", body: { kind, reason } }),
  chat: (message) => http("/chat", { method: "POST", body: { message } }),
  approve: (keys) => http("/approve", { method: "POST", body: { keys } }),
  reject: () => http("/reject", { method: "POST" }),
  /* 확인 대기는 chat/approve 응답에 함께 실려 옵니다.
     여기서 별도 요청을 하면 동기 호출 자리에 Promise 가 들어가 화면이 깨집니다. */
  pending: () => http("/pending"),
  meta: () => http("/meta"),
};

export const api = USE_MOCK ? mock : real;
