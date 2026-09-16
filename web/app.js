/* ==========================================================================
   SHOPMATE — 화면
   데이터는 전부 api.js 를 통해서만 가져옵니다 (지금은 목업).
   ========================================================================== */
import { api } from "./api.js";
import { art, swatchDot } from "./art.js";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const won = (n) => n.toLocaleString("ko-KR") + "원";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const GROUPS = {
  "신발": ["운동화", "구두", "부츠", "샌들"],
  "상의": ["티셔츠", "셔츠", "니트", "후드"],
  "하의": ["팬츠", "스커트"],
  "아우터": ["재킷", "코트", "아웃도어"],
  "원피스·정장": ["원피스", "정장"],
  "이너웨어": ["이너"],
};

const S = {
  view: "shop",
  query: "", group: null, category: null, gender: null, color: null,
  semanticQuery: null, searchArgs: null,
  sort: "recommend",
  shown: 20, rows: [],
  detailId: null, detailSize: null,
  picked: new Set(),          // 장바구니에서 고른 줄
  seenLines: new Set(),       // 장바구니 화면에 한 번 나타난 줄 (새 줄만 기본 선택하기 위해)
  wish: new Set(),
  messages: [{ role: "bot", text: "안녕하세요. 찾으시는 상품을 말로 알려주시면 제가 찾아 담아 드릴게요." }],
  pending: null, plan: null, busy: false, scrollY: 0,
};

/* ============================ 토스트 ============================ */
function toast(text, warn = false) {
  const el = document.createElement("div");
  el.className = "toast" + (warn ? " warn" : "");
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 1900);
  setTimeout(() => el.remove(), 2300);
}

/* ============================ 히어로 ============================ */
const SLIDES = [
  { kick: "TALK & SHOP", title: "말로 찾고, 말로 담고,<br>말로 취소하세요",
    sub: "상품 검색부터 주문 취소까지 — 필요한 도구는 에이전트가 고릅니다",
    cta: "상담 열기", act: "dock",
    bg: "linear-gradient(118deg,#101014 0%,#22222B 48%,#3E2224 100%)",
    dots: ["#E2231A", "#5B5BE0", "#E29A1A"] },
  { kick: "NEW ARRIVALS", title: "이번 주 새로 들어온<br>240가지",
    sub: "평점과 리뷰가 함께 쌓인 상품부터 보여드립니다",
    cta: "신상품 보기", act: "new",
    bg: "linear-gradient(118deg,#14212B 0%,#1E3442 52%,#2C4C4A 100%)",
    dots: ["#3FB0A0", "#7FD1C4", "#2C6BD1"] },
  { kick: "SAFE BY DESIGN", title: "되돌릴 수 없는 일은<br>버튼으로만",
    sub: "삭제 · 결제 · 취소 · 반품은 사람이 누르기 전에는 실행되지 않습니다",
    cta: "어떻게 동작하나", act: "dock",
    bg: "linear-gradient(118deg,#1B1520 0%,#2E2033 50%,#432433 100%)",
    dots: ["#E27AA8", "#B36BE0", "#E2231A"] },
];
let heroIx = 0, heroTimer = null;

function renderHero() {
  $("#heroTrack").innerHTML = SLIDES.map((s) => `
    <div class="slide" style="background:${s.bg}">
      <div class="slide-deco">
        ${s.dots.map((c, i) => `<i style="background:${c};width:${180 + i * 90}px;
          height:${180 + i * 90}px;right:${-40 + i * 120}px;top:${-60 + i * 40}px;
          opacity:${.34 - i * .08}"></i>`).join("")}
      </div>
      <div class="slide-copy">
        <div class="slide-kick" style="color:${s.dots[0]}">${s.kick}</div>
        <div class="slide-title">${s.title}</div>
        <div class="slide-sub">${s.sub}</div>
        <button class="slide-cta" data-hero-act="${s.act}">${s.cta} <span>&rarr;</span></button>
      </div>
      <div class="slide-art" data-slide-art></div>
    </div>`).join("");
  $("#heroDots").innerHTML = SLIDES.map((_, i) =>
    `<button data-hero-go="${i}" class="${i === 0 ? "on" : ""}"></button>`).join("");
  goHero(0);
  startHero();
}
function goHero(i) {
  heroIx = (i + SLIDES.length) % SLIDES.length;
  $("#heroTrack").style.transform = `translateX(-${heroIx * 100}%)`;
  $$("#heroDots button").forEach((b, k) => b.classList.toggle("on", k === heroIx));
}
function startHero() {
  clearInterval(heroTimer);
  heroTimer = setInterval(() => goHero(heroIx + 1), 5200);
}

/* ============================ 카테고리 네비 ============================ */
function renderCatnav() {
  const items = [["전체", null]].concat(Object.keys(GROUPS).map((g) => [g, g]));
  $("#catnav").innerHTML = items.map(([label, g]) =>
    `<button data-group="${g ?? ""}" class="${S.group === g ? "on" : ""}">${label}</button>`
  ).join("");
}

/* ============================ 필터 칩 ============================ */
function renderChips() {
  const subs = S.group ? GROUPS[S.group] : [];
  const chips = [];
  subs.forEach((c) => chips.push(
    `<button class="chip ${S.category === c ? "on" : ""}" data-cat="${c}">${c}</button>`));
  ["남성", "여성", "공용"].forEach((g) => chips.push(
    `<button class="chip ${S.gender === g ? "on" : ""}" data-gender="${g}">${g}</button>`));
  if (S.color) chips.push(
    `<button class="chip on" data-color="${S.color}">${swatchDot(S.color)}${S.color}<span class="x">&times;</span></button>`);
  // 상담에서 넘어온 조건(브랜드·소재·가격·사이즈·세탁)도 칩으로 드러냅니다.
  // 보이지 않는 채로 남아 있으면 "왜 상의가 15만원 이하만 나오지?" 가 됩니다.
  // 칩의 × 는 그 조건 하나만 지웁니다.
  searchArgChips().forEach(([label, keys]) => chips.push(
    `<button class="chip on" data-arg="${keys.join(",")}">${esc(label)}<span class="x">&times;</span></button>`));
  if (S.query) chips.push(
    `<button class="chip on" data-clearq="1">"${esc(S.query)}"<span class="x">&times;</span></button>`);
  $("#filterChips").innerHTML = chips.join("");
}

/* S.searchArgs 중 전용 칩이 없는 조건을 [표시 문구, 지울 키들] 로 만든다.
   category·gender·color·group 은 위에서 이미 칩이 있으므로 여기서 제외합니다. */
function searchArgChips() {
  const a = S.searchArgs || {};
  const out = [];
  if (a.brand) out.push([a.brand, ["brand"]]);
  if (a.material) out.push([`소재 ${a.material}`, ["material"]]);
  if (a.min_price != null && a.max_price != null)
    out.push([`${won(a.min_price)} ~ ${won(a.max_price)}`, ["min_price", "max_price"]]);
  else if (a.max_price != null) out.push([`${won(a.max_price)} 이하`, ["max_price"]]);
  else if (a.min_price != null) out.push([`${won(a.min_price)} 이상`, ["min_price"]]);
  if (a.size != null) out.push([`${a.size} 사이즈 재고`, ["size"]]);
  if (a.machine_washable === true) out.push(["세탁기 사용 가능", ["machine_washable"]]);
  return out;
}

/* 상담 검색에서 넘어온 조건을 모두 지운다. 검색창·의미 검색어·부가 조건 전부. */
function clearChatSearch() {
  S.query = ""; S.semanticQuery = null; S.searchArgs = null;
  $("#searchInput").value = ""; $("#searchClear").hidden = true;
}

/* ============================ 상품 그리드 ============================ */
function tileHTML(p, rank) {
  const sold = Object.values(p.sizes).every((v) => v === 0);
  const wished = S.wish.has(p.id);
  return `<article class="tile" data-id="${p.id}" style="animation-delay:${Math.min(rank, 12) * 22}ms">
    <div class="tile-art" data-open="${p.id}">
      ${rank < 3 ? `<span class="tile-rank ${rank === 0 ? "top" : ""}">${rank + 1}</span>` : ""}
      ${art(p)}
      ${sold ? '<div class="tile-soldout">품절</div>' : ""}
      <button class="tile-wish ${wished ? "on" : ""}" data-wish="${p.id}" aria-label="찜">
        <svg viewBox="0 0 24 24" class="ic"><path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.6-7 9-7 9z"/></svg>
      </button>
      ${sold ? "" : `<div class="tile-quick">
        <button class="btn sm point" data-quick="${p.id}">바로 담기</button>
      </div>`}
    </div>
    <div class="tile-info">
      <div class="tile-brand">${esc(p.brand)}</div>
      <div class="tile-name">${esc(p.name)}</div>
      <div class="tile-price">${p.price.toLocaleString("ko-KR")}<small>원</small></div>
      <div class="tile-meta">
        <span class="stars">${p.rating.toFixed(1)}</span><span>(${p.review_count.toLocaleString()})</span>
        ${swatchDot(p.color)}${esc(p.color)}
        <span class="tile-likes"><svg viewBox="0 0 24 24" class="ic"><path d="M12 20s-7-4.4-7-9a4 4 0 0 1 7-2.6A4 4 0 0 1 19 11c0 4.6-7 9-7 9z"/></svg>${likes(p)}</span>
      </div>
      ${tagsHTML(p)}
    </div>
  </article>`;
}

/* 카드 배지. 전부 데이터에서 나오는 값이라 지어낸 게 없습니다.
   BEST 는 리뷰 400개 이상, 빠른배송은 배송 1일, 세탁기는 machine_washable. */
function tagsHTML(p) {
  const tags = [];
  if (p.review_count >= 400) tags.push('<span class="tag-s best">BEST</span>');
  if (p.delivery_days != null && p.delivery_days <= 1) tags.push('<span class="tag-s fast">빠른배송</span>');
  return tags.length ? `<div class="tile-tags">${tags.join("")}</div>` : "";
}
/* 좋아요 수는 데이터에 없어서 리뷰 수에서 파생합니다 (리뷰의 약 4배, 상품마다 고정). */
function likes(p) {
  let h = 0; for (const ch of String(p.id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const n = Math.round(p.review_count * (3.4 + (h % 13) / 10));
  return n >= 1000 ? (n / 1000).toFixed(1) + "천" : String(n);
}

function skeletons(n = 12) {
  return Array.from({ length: n }, () =>
    `<div><div class="sk sk-art"></div><div class="sk sk-l" style="width:38%"></div>
     <div class="sk sk-l" style="width:76%"></div><div class="sk sk-l" style="width:46%"></div></div>`
  ).join("");
}

async function loadGrid() {
  $("#grid").innerHTML = skeletons();
  S.rows = await api.products({
    query: S.query, group: S.group, category: S.category,
    gender: S.gender, color: S.color, sort: S.sort,
    semanticQuery: S.semanticQuery,
    productName: S.searchArgs?.product_name,
    brand: S.searchArgs?.brand, material: S.searchArgs?.material,
    minPrice: S.searchArgs?.min_price, maxPrice: S.searchArgs?.max_price,
    size: S.searchArgs?.size,
    machineWashable: S.searchArgs?.machine_washable === true ? 1 : null,
  });
  renderGrid();
}
/* 히어로 오른쪽 상품 콜라주. 실제 목록에서 고른 상품이라 클릭하면 상세로 갑니다.
   슬라이드마다 다른 셋을 보여 줍니다. */
function renderHeroArt() {
  const pool = S.rows.filter((p) => !Object.values(p.sizes).every((v) => v === 0));
  if (pool.length < 3) return;
  $$("[data-slide-art]").forEach((el, k) => {
    if (el.children.length) return;                 // 한 번 채웠으면 그대로 둔다
    const picks = [0, 1, 2].map((i) => pool[(k * 3 + i) % pool.length]);
    el.innerHTML = picks.map((p) => `<div class="pc" data-open="${p.id}">${art(p)}</div>`).join("");
  });
}

function renderGrid() {
  renderHeroArt();
  const shown = S.rows.slice(0, S.shown);
  $("#grid").innerHTML = shown.length
    ? shown.map(tileHTML).join("")
    : `<div class="empty" style="grid-column:1/-1"><b>조건에 맞는 상품이 없습니다</b>
       검색어를 지우거나 필터를 넓혀 보세요</div>`;
  $("#resultCount").textContent =
    `${S.rows.length.toLocaleString()}개 중 ${shown.length}개`;
  $("#moreBtn").hidden = shown.length >= S.rows.length;
  $("#moreBtn").textContent = `더 보기 (${(S.rows.length - shown.length).toLocaleString()}개 남음)`;
}

/* ============================ 상세 ============================ */
async function renderDetail(id) {
  const p = await api.product(id);
  S.detailId = id;
  S.detailSize = Object.entries(p.sizes).find(([, v]) => v > 0)?.[0] ?? null;
  const rows = [
    ["브랜드", p.brand], ["분류", `${p.group} · ${p.category}`],
    ["색상", `${swatchDot(p.color)} ${p.color}`],
    ["소재", p.material_detail || p.material],
    ["성별", p.gender],
    ["세탁", p.machine_washable ? "세탁기 사용 가능" : "세탁기 사용 불가 · 드라이 권장"],
    ["배송", p.delivery_days != null ? `주문 후 약 ${p.delivery_days}일` : "-"],
  ];
  $("#view-detail").innerHTML = `
    <div class="crumb"><button data-nav="shop">상품</button><span>›</span>
      <span>${p.group}</span><span>›</span><span>${p.category}</span></div>
    <div class="dt">
      <div class="dt-art">${art(p)}</div>
      <div>
        <div class="dt-brand">${p.brand}</div>
        <h1 class="dt-name">${esc(p.name)}</h1>
        <div class="tile-meta" style="margin-top:10px">
          <span class="stars">★ ${p.rating.toFixed(1)}</span><span>리뷰 ${p.review_count}개</span></div>
        <div class="dt-price">${won(p.price)}</div>
        <p class="dt-desc">${esc(p.description)}</p>
        <div style="margin-top:26px" class="lbl">사이즈</div>
        <div class="sz-grid" id="szGrid">
          ${Object.entries(p.sizes).map(([s, q]) => `
            <button class="sz ${q === 0 ? "out" : ""} ${String(s) === String(S.detailSize) ? "on" : ""}"
                    data-size="${s}"><b>${s}</b><i>${q === 0 ? "품절" : q + "개"}</i></button>`).join("")}
        </div>
        <div class="dt-actions">
          <button class="btn ghost" data-detail-wish="${p.id}">
            ${S.wish.has(p.id) ? "찜 해제" : "찜하기"}</button>
          <button class="btn point" data-detail-add="${p.id}">장바구니에 담기</button>
        </div>
        <div class="dt-rows">
          ${rows.map(([k, v]) => `<div class="dt-row"><span class="k">${k}</span>
            <span class="v">${v}</span></div>`).join("")}
        </div>
      </div>
    </div>`;
}

/* ============================ 장바구니 ============================ */
async function renderCart() {
  const v = await api.cart();
  if (!v.lines.length) {
    S.picked.clear(); S.seenLines.clear();
    $("#view-cart").innerHTML = `<div class="page-hd"><div><h2>장바구니</h2></div></div>
      <div class="empty"><b>장바구니가 비어 있습니다</b>상품을 담으면 여기에서 한 번에 주문할 수 있습니다</div>`;
    return;
  }
  const key = (l) => l.product_id + "|" + l.size;
  // 새로 담긴 줄만 기본 선택합니다. 예전에는 매 렌더마다 모든 줄을 picked 에
  // 다시 넣어서, 체크를 풀어도 다시 그리는 순간 다시 체크되는 버그가 있었습니다
  // ("전체 해제" 도 같은 이유로 동작하지 않았습니다). 한 번 본 줄은 seenLines 에
  // 기억해 두고, 이후 선택 상태는 사용자의 클릭만 바꿉니다.
  const present = new Set(v.lines.map(key));
  [...S.seenLines].forEach((k) => {           // 사라진 줄(상담으로 뺀 것 등)은 잊는다
    if (!present.has(k)) { S.seenLines.delete(k); S.picked.delete(k); }
  });
  v.lines.forEach((l) => {
    const k = key(l);
    if (!S.seenLines.has(k)) { S.seenLines.add(k); S.picked.add(k); }
  });
  const pickedLines = v.lines.filter((l) => S.picked.has(key(l)));
  const total = pickedLines.reduce((a, l) => a + l.price * l.quantity, 0);
  $("#view-cart").innerHTML = `
    <div class="page-hd">
      <div><h2>장바구니</h2><p>${v.lines.length}종 · ${v.quantity}개</p></div>
      <button class="btn ghost sm" id="pickAll">전체 선택 / 해제</button>
    </div>
    ${v.lines.map((l) => `
      <div class="line-row">
        <button class="pick ${S.picked.has(key(l)) ? "on" : ""}" data-pick="${key(l)}">
          <svg viewBox="0 0 24 24" class="ic" style="width:13px;height:13px"><path d="M5 12l5 5 9-10"/></svg>
        </button>
        <div class="line-art" data-open="${l.product_id}">${art(l)}</div>
        <div class="line-mid">
          <div class="tile-brand">${l.brand}</div>
          <div class="line-name">${esc(l.name)}</div>
          <div class="line-sub">사이즈 ${l.size} · ${swatchDot(l.color)} ${l.color}</div>
        </div>
        <div class="line-right">
          <div class="line-price">${won(l.price * l.quantity)}</div>
          <div class="qty">
            <button data-q="-1" data-k="${key(l)}">−</button>
            <span>${l.quantity}</span>
            <button data-q="1" data-k="${key(l)}">+</button>
          </div>
          <button class="btn ghost sm" data-del="${key(l)}">빼기</button>
        </div>
      </div>`).join("")}
    <div class="sum">
      <div class="sum-total">선택한 ${pickedLines.length}종 합계<b>${won(total)}</b></div>
      <button class="btn ghost" id="buyAll">전체 주문</button>
      <button class="btn point" id="buyPicked" ${pickedLines.length ? "" : "disabled"}>
        선택 항목만 주문</button>
    </div>`;
}

/* ============================ 주문 ============================ */
const BADGE = { "배송 준비 중": "prep", "배송 중": "ship", "배송 완료": "done",
                "취소됨": "cancel", "반품 신청됨": "ret" };
async function renderOrders() {
  const rows = await api.orders();
  $("#ordersCount").textContent = rows.length;
  $("#view-orders").innerHTML = `
    <div class="page-hd"><div><h2>주문 내역</h2><p>${rows.length}건</p></div></div>
    ${rows.map((o) => `
      <div class="line-row">
        <div class="line-art" data-open="${o.items[0].product_id}">${art(o.items[0])}</div>
        <div class="line-mid">
          <div class="line-sub" style="margin:0 0 6px">${o.id} · ${o.ordered_days_ago}일 전</div>
          <div class="line-name">${esc(o.items[0].name)}
            ${o.items.length > 1 ? ` 외 ${o.items.length - 1}건` : ""}</div>
          <div class="line-sub">사이즈 ${o.items[0].size} · ${o.items[0].quantity}개</div>
        </div>
        <div class="line-right">
          <span class="badge ${BADGE[o.status]}">${o.status}</span>
          <div class="line-price">${won(o.total)}</div>
          ${orderActionsHTML(o)}
        </div>
      </div>`).join("")}`;
}

/* 주문 카드의 취소·반품 버튼.
   판정은 화면이 하지 않는다. 서버가 store.can_cancel / can_return 로 계산해 준
   actions 를 그대로 그린다 — 에이전트가 Tool 로 묻는 것과 같은 함수다.
   버튼을 눌러도 바로 실행되지 않고 상담창에 승인 버튼이 뜬다(채팅으로 부탁했을 때와
   같은 경로). 둘 다 안 되면 이유와 대안을 그대로 보여 준다. */
function orderActionsHTML(o) {
  const a = o.actions || {};
  if (a.cancel?.allowed)
    return `<button class="btn ghost sm" data-order-act="cancel" data-order-id="${esc(o.id)}" title="${esc(a.cancel.reason || "")}">주문 취소</button>`;
  if (a.return?.allowed)
    return `<button class="btn ghost sm" data-order-act="return" data-order-id="${esc(o.id)}" title="${esc(a.return.reason || "")}">반품 신청</button>`;
  const reason = a.cancel?.reason || a.return?.reason || "";
  const alt = a.cancel?.alternative || a.return?.alternative || "";
  if (!reason && !alt) return "";
  return `<div class="line-note">${esc(reason)}${alt ? `<b>${esc(alt)}</b>` : ""}</div>`;
}

/* ============================ 상담 도크 ============================ */
const SAMPLES = ["15만원 이하 검은색 운동화 보여줘", "여성 니트 평점 높은 거",
                 "장바구니에 뭐 담겨 있어?", "어제 주문한 거 취소해줘"];

function renderSamples() {
  $("#dockSamples").innerHTML = SAMPLES.map((s) =>
    `<button data-sample="${esc(s)}"><span>${esc(s)}</span><svg viewBox="0 0 24 24" class="ic"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>`).join("");
}

/* 상담원 표식. 이모지 대신 SVG 모노그램을 쓴다. */
const BOT_MARK = `<div class="av" aria-hidden="true"><svg viewBox="0 0 32 32"><path d="M10 12V9.5a6 6 0 0 1 12 0V12h3.2l1.3 14.5H5.5L6.8 12H10zm2.2 0h7.6V9.5a3.8 3.8 0 0 0-7.6 0V12z"/></svg></div>`;

/* 모델 답변 본문을 다듬어 그린다.
   - **굵게** 만 허용 (모델이 마크다운을 섞어 쓴다)
   - "─ 실행 결과 ─" 아래 ✓/✗ 줄은 앱이 붙인 사실이므로 별도 블록으로 뗀다 */
function richText(text) {
  const [body, report] = String(text).split(/\n*─ 실행 결과 ─\n?/);
  const bold = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  let html = `<p>${bold(body.trim()).replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>")}</p>`;
  if (report) {
    const rows = report.split("\n").map((l) => l.trim()).filter(Boolean);
    html += `<ul class="report">${rows.map((l) => {
      const ok = l.startsWith("✓"), bad = l.startsWith("✗");
      const t = (ok || bad) ? l.slice(1).trim() : l;
      return `<li class="${ok ? "ok" : bad ? "bad" : "note"}"><i></i><span>${bold(t)}</span></li>`;
    }).join("")}</ul>`;
  }
  return html;
}

function msgHTML(m) {
  if (m.role === "me")
    return `<div class="msg me"><div class="bubble">${esc(m.text)}</div></div>`;
  const okCount = (m.trace || []).filter((t) => t.ok).length;
  const trace = (m.trace || []).length ? `
    <details class="trace"><summary><span class="trace-k">실행 기록</span><span class="trace-c">${m.trace.length}단계${okCount < m.trace.length ? ` · 실패 ${m.trace.length - okCount}` : ""}</span><svg viewBox="0 0 24 24" class="ic chev"><path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></summary>
      <ol class="trace-in">${m.trace.map((t) => `
        <li class="trace-row ${t.ok ? "" : "bad"}"><i></i>
          <div><code class="trace-call"><b>${esc(t.name)}</b><span>${esc(JSON.stringify(t.args))}</span></code>
          ${t.msg ? `<div class="trace-msg">${esc(t.msg)}</div>` : ""}</div></li>`).join("")}
      </ol></details>` : "";
  const minis = (m.products || []).length ? `
    <div class="mini-grid">${m.products.map((p) => `
      <div class="mini-card" data-open="${p.id}"><div class="mini">${art(p)}</div>
        <div class="mini-cap"><span>${esc(p.brand)}</span><b>${won(p.price)}</b></div></div>`).join("")}
    </div>` : "";
  return `<div class="msg bot">${BOT_MARK}
    <div class="msg-col"><div class="bubble">${richText(m.text)}</div>${trace}${minis}</div></div>`;
}

/* 첫 화면 — 아직 대화가 없을 때 무엇을 시킬 수 있는지 보여 준다. */
function welcomeHTML() {
  const rows = [
    ["M4 7h16M4 12h10M4 17h7", "상품 찾기·비교", "조건을 말하면 실제 재고에서 찾아 비교해 드려요"],
    ["M5 6h14l-1.5 10h-11z M9 20h.01M15 20h.01", "장바구니·결제", "담기와 결제까지, 결제는 버튼으로 한 번 더 확인"],
    ["M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z", "주문 조회·취소·반품", "어제 주문한 것, 지난주 받은 것처럼 말해도 찾습니다"],
  ];
  return `<div class="welcome">${rows.map(([d, t, sub]) => `
    <div class="wl"><svg viewBox="0 0 24 24"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <div><b>${t}</b><span>${sub}</span></div></div>`).join("")}</div>`;
}

function renderDock() {
  $("#dockBody").innerHTML = S.messages.map(msgHTML).join("") +
    (S.messages.length <= 1 && !S.busy ? welcomeHTML() : "") +
    (S.busy ? `<div class="msg bot">${BOT_MARK}
       <div class="bubble typing"><i></i><i></i><i></i><span>찾고 있어요</span></div></div>` : "");
  $("#dockBody").scrollTop = $("#dockBody").scrollHeight;
  $("#dockState").textContent = S.busy ? "생각하는 중" : "대기 중";

  renderPlan();
  const p = S.pending;
  $("#dockPending").hidden = !p;
  armPendingExpiry(p);
  if (p) {
    const mins = p.expires_in != null ? Math.max(1, Math.round(p.expires_in / 60)) : null;
    $("#dockPending").innerHTML = `
      <h4>확인이 필요합니다</h4>
      <p>되돌릴 수 없는 작업이라 버튼으로만 진행됩니다. 채팅으로 "네"라고 답해도 실행되지 않습니다.${
        mins ? ` 약 ${mins}분 안에 눌러 주세요.` : ""}</p>
      ${p.items.map((it, i) => `<div class="pend-item"><i>${i + 1}</i>${esc(it.label)}</div>`).join("")}
      <div class="pend-btns">
        <button class="btn point" data-approve="all">${p.items.length > 1 ? "모두 승인" : "승인"}</button>
        ${p.items.map((_, i) => p.items.length > 1
          ? `<button class="btn ghost" data-approve="${i}">${i + 1}번만</button>` : "").join("")}
        <button class="btn ghost" data-reject="1">아니요</button>
      </div>`;
  }
}

/* 계획 체크리스트. 서버가 응답에 plan 을 실어 보내면(계획 모드) 도크 위쪽에
   "무엇을 어떤 순서로 할 계획이고 어디까지 왔는지" 를 그립니다. 모델이 세운
   계획을 앱이 실행 기록으로 대조해 표시를 옮기므로, 모델의 말이 아니라 실제
   실행 여부가 보입니다. */
function renderPlan() {
  const el = $("#dockPlan"); if (!el) return;
  const pl = S.plan;
  el.hidden = !pl;
  if (!pl) return;
  const done = pl.steps.filter((s) => s.status === "done").length;
  const icon = { todo: "○", done: "●", failed: "✕" };
  el.innerHTML = `
    <h4>계획 <span>${done}/${pl.steps.length}</span></h4>
    <p>${esc(pl.goal || "")}${pl.budget ? ` <span class="plan-budget">예산 ${won(pl.budget)}</span>` : ""}</p>
    <ol>${pl.steps.map((s) => `<li class="${s.status}"><i>${icon[s.status] || "○"}</i>
      <b>${esc(s.tool)}</b>${s.why ? ` <span>${esc(s.why)}</span>` : ""}</li>`).join("")}</ol>
    ${pl.off_plan?.length ? `<small>계획에 없던 호출: ${esc(pl.off_plan.join(", "))}</small>` : ""}`;
}

/* 승인 버튼의 유효 시간. 서버가 pending.expires_in(초)을 주면 그 시각에 버튼을
   거두고 알립니다. 서버도 같은 시각부터 승인을 거절하므로, 여기서 거두지 않으면
   사용자가 눌러도 "확인 시간이 지났습니다" 만 돌아옵니다. */
let pendingTimer = null;
function armPendingExpiry(p) {
  clearTimeout(pendingTimer); pendingTimer = null;
  if (!p || p.expires_in == null) return;
  pendingTimer = setTimeout(() => {
    if (S.pending !== p) return;
    S.pending = null;
    S.messages.push({ role: "bot", text: "확인 시간이 지나 승인 버튼을 거두었습니다. 필요하시면 다시 요청해 주세요.", trace: [] });
    renderDock();
  }, Math.max(0, p.expires_in) * 1000 + 500);
}

async function send(text) {
  if (!text.trim() || S.busy) return;
  S.messages.push({ role: "me", text });
  S.busy = true; renderDock();
  let r, products;
  try {
    r = await api.chat(text);
    const searchResults = r.search_results || [];
    products = searchResults.length
      ? []
      : await Promise.all((r.products || []).map((id) => api.product(id)));
  } catch (err) {
    // 서버가 500 을 내거나 연결이 끊기면 여기로 옵니다. 예전에는 이 경우
    // S.busy 가 true 로 남아 상담창이 영구히 "생각하는 중" 에 잠겼습니다.
    S.messages.push({ role: "bot", text: `요청을 처리하지 못했습니다. (${err.message})`, trace: [] });
    toast("상담 요청이 실패했습니다", true);
    return;
  } finally {
    S.busy = false;
    renderDock();
  }
  const searchResults = r.search_results || [];
  S.messages.push({ role: "bot", text: r.reply, trace: r.trace, products });
  // 서버가 /api/chat 응답에 pending 을 같이 실어 보냅니다.
  // 따로 물어보지 않고 응답에서 바로 읽습니다.
  S.pending = r.pending || null;
  S.plan = r.plan || null;
  renderDock();
  if (r.search_note) toast(r.search_note);
  if (r.search_performed) {
    const searchTrace = [...(r.trace || [])].reverse()
      .find((item) => item.name === "search_product" && item.ok);
    const args = searchTrace?.args || {};
    S.searchArgs = args;
    S.semanticQuery = args.semantic_query || null;
    S.query = args.product_name || args.semantic_query || "";
    S.category = args.category || null;
    S.group = args.group || (S.category
      ? Object.entries(GROUPS).find(([, values]) => values.includes(S.category))?.[0] || null
      : null);
    S.gender = args.gender || null;
    S.color = args.color || null;
    S.sort = args.sort || "recommend";
    S.shown = 20;
    $("#searchInput").value = S.query;
    $("#searchClear").hidden = !S.query;
    $("#sortSel").value = S.sort;
    S.rows = searchResults;
    await go("shop");
    renderCatnav(); renderChips(); renderGrid();
    if (window.innerWidth <= 1180) openDock(false);
  }
}

function openDock(on = true) {
  document.body.classList.toggle("dock-open", on);
  $("#scrim").hidden = !on || window.innerWidth > 1180;
  if (on) setTimeout(() => $("#chatInput").focus(), 240);
}

/* ============================ 라우팅 ============================ */
/* 주소의 해시로 화면을 표현합니다.
   #/            상품 목록
   #/product/P1  상품 상세
   #/cart        장바구니
   #/orders      주문 내역
   해시를 쓰면 브라우저 뒤로/앞으로 가기가 그대로 동작합니다. */
function toHash(view, arg) {
  if (view === "detail") return "#/product/" + arg;
  if (view === "shop") return "#/";
  return "#/" + view;
}
function fromHash() {
  const parts = (location.hash || "#/").replace(/^#\/?/, "").split("/");
  if (parts[0] === "product" && parts[1]) return { view: "detail", arg: parts[1] };
  if (parts[0] === "cart" || parts[0] === "orders") return { view: parts[0] };
  return { view: "shop" };
}

async function render(view, arg) {
  // 목록을 떠날 때 스크롤 위치를 기억해 두었다가 돌아오면 그 자리로.
  if (S.view === "shop" && view !== "shop") S.scrollY = window.scrollY;
  S.view = view;
  ["shop", "detail", "cart", "orders"].forEach((v) =>
    $("#view-" + v).hidden = v !== view);
  if (view === "detail") await renderDetail(arg);
  if (view === "cart") await renderCart();
  if (view === "orders") await renderOrders();
  if (view === "shop") { renderCatnav(); renderChips(); }
  // 스크롤은 항상 즉시 이동합니다. 부드러운 스크롤을 쓰면 애니메이션이
  // 끝나기 전에 다음 화면이 그려져 복원한 위치를 덮어씁니다.
  let y = 0;
  if (view === "shop") { y = S.scrollY || 0; S.scrollY = 0; }  // 돌아올 때만 소비
  requestAnimationFrame(() => window.scrollTo(0, y));
}

/* 화면 전환은 해시만 바꿉니다. 실제 그리기는 hashchange 가 맡습니다.
   이렇게 해야 링크로 이동하든 뒤로가기로 오든 경로가 하나로 유지됩니다. */
function go(view, arg) {
  const h = toHash(view, arg);
  if (location.hash === h || (!location.hash && h === "#/")) return render(view, arg);
  location.hash = h;
}

window.addEventListener("hashchange", () => {
  const r = fromHash();
  render(r.view, r.arg);
});

async function refreshCounts() {
  const v = await api.cart();
  $("#cartCount").textContent = v.quantity;
  const o = await api.orders();
  $("#ordersCount").textContent = o.length;
}

/* ============================ 이벤트 ============================ */
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-nav],[data-group],[data-cat],[data-gender],[data-color],"
    + "[data-clearq],[data-open],[data-quick],[data-wish],[data-hero],[data-hero-go],"
    + "[data-hero-act],[data-size],[data-detail-add],[data-detail-wish],[data-pick],"
    + "[data-q],[data-del],[data-sample],[data-approve],[data-reject],[data-tag],[data-arg],"
    + "[data-order-act]");
  if (!t) return;
  const d = t.dataset;

  if (d.nav) return go(d.nav);
  if (d.hero) { goHero(heroIx + Number(d.hero)); startHero(); return; }
  if (d.heroGo) { goHero(Number(d.heroGo)); startHero(); return; }
  if (d.heroAct) {
    if (d.heroAct === "dock") openDock(true);
    else { S.sort = "review"; $("#sortSel").value = "review"; S.shown = 20; await go("shop"); loadGrid(); }
    return;
  }
  if ("group" in d) {
    // 상단 대분류를 고르는 것은 "새로 둘러보기" 입니다. 상담 검색에서 넘어온
    // 가격·브랜드·의미 검색어를 여기서 지웁니다. 안 지우면 "15만원 이하 검은색
    // 운동화" 뒤에 "상의" 를 눌렀을 때 15만원 이하 상의만 조용히 나옵니다.
    S.group = d.group || null; S.category = null; S.shown = 20;
    clearChatSearch();          // 색상·성별 칩은 눈에 보이므로 그대로 둔다
    renderCatnav(); renderChips(); await go("shop"); loadGrid(); return;
  }
  if (d.arg) {
    d.arg.split(",").forEach((k) => { if (S.searchArgs) delete S.searchArgs[k]; });
    if (S.searchArgs && !Object.keys(S.searchArgs).length) S.searchArgs = null;
    S.shown = 20; renderChips(); loadGrid(); return;
  }
  if (d.cat) { S.category = S.category === d.cat ? null : d.cat; S.shown = 20; renderChips(); loadGrid(); return; }
  if (d.gender) { S.gender = S.gender === d.gender ? null : d.gender; S.shown = 20; renderChips(); loadGrid(); return; }
  if (d.color) { S.color = null; S.shown = 20; renderChips(); loadGrid(); return; }
  if (d.clearq) { clearChatSearch(); S.shown = 20; renderChips(); loadGrid(); return; }
  if (d.tag) {
    S.query = d.tag; $("#searchInput").value = d.tag; $("#searchClear").hidden = false;
    S.shown = 20; await go("shop"); renderChips(); loadGrid(); return;
  }
  if (d.open) return go("detail", d.open);

  if (d.quick) {
    const p = await api.product(d.quick);
    const size = Object.entries(p.sizes).find(([, q]) => q > 0)?.[0];
    const r = await api.addToCart(p.id, size, 1);
    toast(r.message, !r.ok); refreshCounts(); return;
  }
  if (d.wish || d.detailWish) {
    const id = d.wish || d.detailWish;
    S.wish.has(id) ? S.wish.delete(id) : S.wish.add(id);
    if (d.wish) t.classList.toggle("on"); else renderDetail(id);
    toast(S.wish.has(id) ? "찜했습니다" : "찜을 해제했습니다"); return;
  }
  if (d.size) {
    S.detailSize = d.size;
    $$("#szGrid .sz").forEach((b) => b.classList.toggle("on", b.dataset.size === d.size));
    return;
  }
  if (d.detailAdd) {
    if (!S.detailSize) return toast("사이즈를 골라 주세요", true);
    const r = await api.addToCart(d.detailAdd, S.detailSize, 1);
    toast(r.message, !r.ok); refreshCounts(); return;
  }
  if (d.pick) {
    S.picked.has(d.pick) ? S.picked.delete(d.pick) : S.picked.add(d.pick);
    renderCart(); return;
  }
  if (d.q) {
    const [id, size] = d.k.split("|");
    const v = await api.cart();
    const line = v.lines.find((l) => l.product_id === id && String(l.size) === size);
    await api.setQuantity(id, size, line.quantity + Number(d.q));
    renderCart(); refreshCounts(); return;
  }
  if (d.del) {
    const [id, size] = d.del.split("|");
    const r = await api.removeFromCart(id, size);
    S.picked.delete(d.del); S.seenLines.delete(d.del);
    toast(r.message); renderCart(); refreshCounts(); return;
  }
  if (d.sample) { openDock(true); send(d.sample); return; }
  if (d.approve) {
    const p = S.pending; if (!p) return;
    const keys = d.approve === "all" ? p.items.map((i) => i.key) : [p.items[Number(d.approve)].key];
    if (S.busy) return;
    S.busy = true; renderDock();
    try {
      const r = await api.approve(keys);
      S.pending = r.pending || null;
      S.plan = r.plan || null;
      S.messages.push({ role: "bot", text: r.reply, trace: r.trace });
    } catch (err) {
      // 승인 요청이 실패하면 대기 항목은 그대로 두고 알립니다.
      // 실행됐는지 확인되지 않은 상태라 버튼을 없애면 안 됩니다.
      S.messages.push({ role: "bot", text: `승인 요청을 처리하지 못했습니다. (${err.message})`, trace: [] });
      toast("승인 요청이 실패했습니다", true);
    } finally {
      S.busy = false; renderDock();
    }
    refreshCounts();
    if (S.view === "orders") renderOrders();
    if (S.view === "cart") renderCart();
    return;
  }
  if (d.orderAct) {
    // 주문 화면의 취소·반품 버튼 → 서버가 확인 대기를 열고, 승인은 상담창에서.
    if (S.busy) return;
    S.busy = true; renderDock();
    try {
      const r = await api.orderAction(d.orderId, d.orderAct);
      S.pending = r.pending || null;
      S.plan = r.plan || null;
      S.messages.push({ role: "bot", text: r.reply, trace: r.trace || [] });
      openDock(true);
    } catch (err) {
      toast("요청을 처리하지 못했습니다", true);
    } finally {
      S.busy = false; renderDock();
    }
    return;
  }
  if (d.reject) {
    if (S.busy) return;
    try {
      const r = await api.reject();
      S.pending = r.pending || null;
      S.plan = r.plan || null;
      S.messages.push({ role: "bot", text: r.reply, trace: [] });
    } catch (err) {
      toast("요청이 실패했습니다", true);
    }
    renderDock(); return;
  }
});

/* id 로 잡는 버튼들.
   버튼 안에 아이콘·글자가 들어 있으면 e.target 이 그 자식이 되므로
   반드시 closest 로 버튼 자체를 찾아야 합니다. (상담 버튼이 안 눌리던 원인) */
document.addEventListener("click", async (e) => {
  const hit = (id) => e.target.closest("#" + id);
  if (hit("pickAll")) {
    const v = await api.cart();
    const all = v.lines.map((l) => l.product_id + "|" + l.size);
    if (all.every((k) => S.picked.has(k))) S.picked.clear();
    else all.forEach((k) => S.picked.add(k));
    renderCart(); return;
  }
  if (hit("buyAll") || hit("buyPicked")) {
    const v = await api.cart();
    const items = hit("buyAll") ? null
      : v.lines.filter((l) => S.picked.has(l.product_id + "|" + l.size))
               .map((l) => ({ product_id: l.product_id, size: l.size }));
    const r = await api.checkout(items);
    toast(r.ok ? `주문 완료 · ${won(r.total)}` : r.message, !r.ok);
    // 주문된 줄은 사라지므로 선택 기록도 함께 비웁니다. 남은 줄은 다음 렌더에서
    // 새 줄로 취급되어 다시 기본 선택됩니다.
    S.picked.clear(); S.seenLines.clear(); renderCart(); refreshCounts();
    return;
  }
  if (hit("moreBtn")) { S.shown += 20; renderGrid(); }
  if (hit("dockToggle")) openDock(!document.body.classList.contains("dock-open"));
  if (hit("dockClose") || hit("scrim")) openDock(false);
  if (hit("searchClear")) { clearChatSearch(); S.shown = 20; renderChips(); loadGrid(); }
});

$("#searchForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  S.query = $("#searchInput").value.trim();
  S.semanticQuery = null; S.searchArgs = null; S.shown = 20;
  $("#searchClear").hidden = !S.query;
  await go("shop"); renderChips(); loadGrid();
});
$("#searchInput").addEventListener("input", (e) => {
  $("#searchClear").hidden = !e.target.value;
});
$("#sortSel").addEventListener("change", (e) => {
  S.sort = e.target.value; S.shown = 20; loadGrid();
});
$("#chatForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("#chatInput").value; $("#chatInput").value = "";
  send(v);
});
window.addEventListener("resize", () => {
  $("#scrim").hidden = !document.body.classList.contains("dock-open") || window.innerWidth > 1180;
});

/* ============================ 시작 ============================ */
renderHero();
renderCatnav();
renderChips();
renderSamples();
renderDock();
loadGrid();
refreshCounts();
if (window.innerWidth > 1180) openDock(true);

/* 주소에 해시가 있으면 그 화면으로 시작합니다. (새로고침·북마크·공유 링크) */
{
  const r0 = fromHash();
  if (r0.view !== "shop") render(r0.view, r0.arg);
}
