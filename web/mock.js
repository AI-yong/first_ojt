/* ==========================================================================
   목업 백엔드
   실제 서버가 붙기 전까지 브라우저 안에서 store.py 흉내를 냅니다.
   필드 이름은 seed.py / store.py 와 똑같이 맞췄습니다.
   나중에 api.js 만 실제 엔드포인트로 바꾸면 이 파일은 지워도 됩니다.
   ========================================================================== */

const MOCK_GROUPS = {
  "신발": ["운동화", "구두", "부츠", "샌들"],
  "상의": ["티셔츠", "셔츠", "니트", "후드"],
  "하의": ["팬츠", "스커트"],
  "아우터": ["재킷", "코트", "아웃도어"],
  "원피스·정장": ["원피스", "정장"],
  "이너웨어": ["이너"],
};
const BRANDS = ["BELLE", "COURTLINE", "DAYLY", "DENIMWORKS", "MERIDIAN",
  "NORTHBAY", "PACEUP", "RIDGE", "STRIDE", "THREADCO", "WOOLNEST"];
const COLORS = ["검은색", "흰색", "회색", "네이비", "파란색", "하늘색",
  "카키색", "갈색", "베이지색", "빨간색", "분홍색", "형광색"];
const GENDERS = ["남성", "여성", "공용"];
const MATERIALS = ["코튼", "린넨", "울", "캐시미어", "데님", "나일론", "폴리에스터",
  "메시", "스웨이드", "가죽", "캔버스", "고어텍스", "모달", "트위드"];
const WORD_A = ["어반", "클라우드", "네온", "트랙", "소프트", "베이직", "레트로", "메쉬",
  "코트", "데일리", "라이트", "웜", "시티", "에어", "트레일", "스톰"];
const WORD_B = ["러너", "워크", "스피드", "라이트", "젤", "캔버스", "프로", "마스터",
  "브리즈", "스텝", "코어", "라인", "폼", "웨이브"];
const SHOE_SIZES = [220, 225, 230, 235, 240, 245, 250, 255, 260, 265, 270, 275, 280, 285];
const TOP_SIZES = ["XS", "S", "M", "L", "XL", "2XL"];

/* 결정적 난수 — 새로고침해도 같은 카탈로그가 나옵니다. */
let _s = 20260910;
const rnd = () => (_s = (_s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
const pick = (a) => a[Math.floor(rnd() * a.length)];
const int = (a, b) => a + Math.floor(rnd() * (b - a + 1));

function buildProducts(n = 240) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const group = pick(Object.keys(MOCK_GROUPS));
    const category = pick(MOCK_GROUPS[group]);
    const shoes = group === "신발";
    const base = shoes ? SHOE_SIZES : TOP_SIZES;
    const start = shoes ? int(0, 4) : 0;
    const sizes = {};
    base.slice(start, start + (shoes ? int(6, 9) : int(3, 6))).forEach((s) => {
      sizes[s] = rnd() < 0.16 ? 0 : int(1, 24);
    });
    const price = int(3, 34) * 10000 - 1000;
    out.push({
      id: "P" + String(i + 1).padStart(4, "0"),
      name: `${pick(WORD_A)} ${pick(WORD_B)}`,
      brand: pick(BRANDS),
      group, category,
      gender: pick(GENDERS),
      color: pick(COLORS),
      material: pick(MATERIALS),
      price,
      rating: Math.round((35 + rnd() * 15)) / 10,
      review_count: int(8, 640),
      sizes,
      machine_washable: rnd() < 0.55,
      detail: {
        highlights: [
          "소재 특유의 질감이 살아 있습니다",
          "일상에서 두루 입기 좋습니다",
          rnd() < .5 ? "세탁기에 그대로 돌릴 수 있습니다" : "드라이클리닝을 권합니다",
        ],
        fit: pick(["기본 핏", "여유 핏", "슬림 핏", "오버 핏"]),
        styling: pick(["데님과 맞추기 좋습니다", "재킷 안에 받쳐 입기 좋습니다"]),
        spec: [["총장", `${68 + int(0, 9)}cm`], ["어깨 너비", `${42 + int(0, 6)}cm`],
               ["가슴 단면", `${50 + int(0, 8)}cm`]],
        feel: [["두께감", pick(["얇음", "보통", "두꺼움"])],
               ["신축성", pick(["없음", "약간 있음", "잘 늘어남"])]],
        keywords: ["데일리", "기본가"],
      },
      material_detail: null,
      care: "세탁 표기를 확인하세요.",
      delivery_days: int(1, 4),
      description:
        `${pick(["가볍고 부드러운", "탄탄한", "산뜻한", "포근한", "단정한"])} ` +
        `${pick(MATERIALS)} 소재로 만든 ${category}. ` +
        `${pick(["매일 신기 좋은", "출퇴근에 어울리는", "주말 나들이용", "사계절 활용도 높은"])} 기본형입니다.`,
    });
  }
  return out;
}

const STATUS = ["배송 준비 중", "배송 중", "배송 완료", "취소됨", "반품 신청됨"];

function buildOrders(products) {
  const out = [];
  for (let i = 0; i < 14; i++) {
    const p = products[int(0, products.length - 1)];
    const size = Object.keys(p.sizes)[0];
    const qty = int(1, 3);
    out.push({
      id: "ORD-" + (1001 + i),
      status: STATUS[Math.min(4, Math.floor(rnd() * 5))],
      ordered_days_ago: int(0, 20),
      items: [{ product_id: p.id, name: p.name, brand: p.brand, size, quantity: qty,
                price: p.price, category: p.category, color: p.color, group: p.group }],
      total: p.price * qty,
    });
  }
  return out;
}

/* ---------------------------------------------------------------- 상태 */
const products = buildProducts();
const orders = buildOrders(products);
let cart = [];
let pending = null;

const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const byId = (id) => products.find((p) => p.id === id);
const stockOf = (p) => Object.values(p.sizes).reduce((a, b) => a + b, 0);

/* ---------------------------------------------------------------- 검색 */
function search({ query = "", group = null, category = null, gender = null,
                  color = null, minPrice = null, maxPrice = null,
                  sort = "recommend" } = {}) {
  const q = query.trim().toLowerCase();
  let rows = products.filter((p) => {
    if (group && p.group !== group) return false;
    if (category && p.category !== category) return false;
    if (gender && p.gender !== gender) return false;
    if (color && p.color !== color) return false;
    if (minPrice != null && p.price < minPrice) return false;
    if (maxPrice != null && p.price > maxPrice) return false;
    if (!q) return true;
    return [p.name, p.brand, p.category, p.gender, p.color, p.material]
      .join(" ").toLowerCase().includes(q);
  });
  const cmp = {
    price_asc: (a, b) => a.price - b.price,
    price_desc: (a, b) => b.price - a.price,
    rating: (a, b) => b.rating - a.rating,
    review: (a, b) => b.review_count - a.review_count,
    recommend: (a, b) => b.rating * Math.log(b.review_count) - a.rating * Math.log(a.review_count),
  }[sort] || null;
  if (cmp) rows = rows.slice().sort(cmp);
  return rows;
}

/* ---------------------------------------------------------------- 장바구니 */
function cartView() {
  const lines = cart.map((c) => {
    const p = byId(c.product_id);
    return { ...c, name: p.name, brand: p.brand, price: p.price, category: p.category,
             color: p.color, group: p.group };
  });
  return {
    lines,
    quantity: lines.reduce((a, l) => a + l.quantity, 0),
    total: lines.reduce((a, l) => a + l.price * l.quantity, 0),
  };
}

/* ---------------------------------------------------------------- 가짜 에이전트
   실제로는 agent.py 가 하는 일입니다. 여기서는 대표 시나리오 몇 개만
   흉내 내어 Tool 트레이스와 승인 대기가 화면에 어떻게 보이는지 확인합니다. */
function fakeAgent(text) {
  const t = text.toLowerCase();
  const num = (text.match(/(\d+)\s*만원/) || [])[1];
  const maxPrice = num ? Number(num) * 10000 : null;
  const color = COLORS.find((c) => text.includes(c)) || null;
  const category = Object.values(MOCK_GROUPS).flat().find((c) => text.includes(c)) || null;

  if (/취소|반품/.test(t)) {
    const target = orders.find((o) => o.status === "배송 준비 중") || orders[0];
    const kind = /반품/.test(t) ? "return_order" : "cancel_order";
    pending = {
      items: [{ key: `${kind}:${target.id}`,
                label: `${target.id} · ${target.items[0].name} · ${target.total.toLocaleString()}원` }],
      kind,
    };
    return {
      reply: `${target.id} 주문을 확인했습니다. 되돌릴 수 없는 작업이라 아래 버튼으로만 진행합니다.`,
      trace: [
        { name: "search_order", args: { status: "배송 준비 중" }, ok: true,
          msg: `${orders.length}건 중 조건에 맞는 주문 확인` },
        { name: kind === "cancel_order" ? "cancel_possible" : "return_possible",
          args: { order_id: target.id }, ok: true, msg: "가능 · 사유 없음" },
        { name: kind, args: { order_id: target.id }, ok: true,
          msg: "confirm 없음 → 미리보기만 · 확인 대기로 전환" },
      ],
      products: [],
    };
  }

  if (/장바구니/.test(t)) {
    const v = cartView();
    return {
      reply: v.quantity
        ? `장바구니에 ${v.lines.length}종 ${v.quantity}개, 합계 ${v.total.toLocaleString()}원 담겨 있습니다.`
        : "장바구니가 비어 있습니다.",
      trace: [{ name: "view_cart", args: {}, ok: true, msg: `${v.lines.length}줄` }],
      products: [],
    };
  }

  const rows = search({ color, category, maxPrice, sort: "rating" }).slice(0, 4);
  return {
    reply: rows.length
      ? `조건에 맞는 상품 ${rows.length}개를 찾았습니다. 평점이 높은 순으로 보여드릴게요.`
      : "조건에 맞는 상품을 찾지 못했습니다. 조건을 넓혀 볼까요?",
    trace: [{
      name: "search_product",
      args: { ...(color && { color }), ...(category && { category }),
              ...(maxPrice && { max_price: maxPrice }), sort: "rating", limit: 4 },
      ok: true, msg: `${rows.length}건`,
    }],
    products: rows.map((p) => p.id),
  };
}

/* ---------------------------------------------------------------- 공개 API */
export const mock = {
  async products(opts) { await wait(120); return search(opts); },
  async product(id) { await wait(80); return byId(id); },

  async cart() { await wait(60); return cartView(); },

  async addToCart(id, size, quantity = 1) {
    await wait(120);
    const p = byId(id);
    if (!p || !p.sizes[size]) return { ok: false, message: "재고가 없습니다" };
    const line = cart.find((c) => c.product_id === id && String(c.size) === String(size));
    if (line) line.quantity += quantity;
    else cart.push({ product_id: id, size, quantity });
    return { ok: true, message: `${p.name} ${size} 담았습니다` };
  },

  async setQuantity(id, size, quantity) {
    await wait(60);
    cart = cart.map((c) =>
      c.product_id === id && String(c.size) === String(size) ? { ...c, quantity } : c
    ).filter((c) => c.quantity > 0);
    return { ok: true };
  },

  async removeFromCart(id, size) {
    await wait(80);
    cart = cart.filter((c) => !(c.product_id === id && String(c.size) === String(size)));
    return { ok: true, message: "장바구니에서 뺐습니다" };
  },

  /* 선택 항목만 결제 — 대표 사례에서 인자를 추가했던 그 지점입니다. */
  async checkout(items = null) {
    await wait(220);
    const target = items && items.length
      ? cart.filter((c) => items.some((i) => i.product_id === c.product_id &&
                                             String(i.size) === String(c.size)))
      : cart.slice();
    if (!target.length) return { ok: false, message: "주문할 항목이 없습니다" };
    const total = target.reduce((a, c) => a + byId(c.product_id).price * c.quantity, 0);
    const p0 = byId(target[0].product_id);
    orders.unshift({
      id: "ORD-" + (2000 + orders.length),
      status: "배송 준비 중",
      ordered_days_ago: 0,
      items: target.map((c) => {
        const p = byId(c.product_id);
        return { product_id: p.id, name: p.name, brand: p.brand, size: c.size,
                 quantity: c.quantity, price: p.price, category: p.category,
                 color: p.color, group: p.group };
      }),
      total,
    });
    /* 주문한 줄만 빠지고 나머지는 그대로 남습니다. */
    cart = cart.filter((c) => !target.includes(c));
    return { ok: true, message: `${p0.name} 외 ${target.length - 1}건 주문 완료`, total };
  },

  async orders() { await wait(90); return orders.slice(); },

  async chat(text) {
    await wait(520 + Math.random() * 420);
    const r = fakeAgent(text);
    return { ...r, pending };
  },

  async approve(keys) {
    await wait(240);
    if (!pending) return { reply: "대기 중인 작업이 없습니다.", trace: [] };
    const [kind, id] = keys[0].split(":");
    const order = orders.find((o) => o.id === id);
    if (order) order.status = kind === "cancel_order" ? "취소됨" : "반품 신청됨";
    const done = pending; pending = null;
    return {
      reply: `${id} ${kind === "cancel_order" ? "취소" : "반품 접수"}되었습니다.`,
      trace: [{ name: done.kind, args: { order_id: id, confirm: true }, ok: true,
                msg: "스냅샷 대조 후 실행 · 상태 변경 완료" }],
      products: [], pending: null,
    };
  },

  async reject() {
    await wait(140);
    pending = null;
    return { reply: "실행하지 않았습니다. 다른 것을 도와드릴까요?", trace: [], products: [], pending: null };
  },

  pending: () => pending,
  meta: () => ({ products: products.length, orders: orders.length,
                 groups: Object.keys(MOCK_GROUPS) }),
};
