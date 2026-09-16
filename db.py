"""SQLite 저장 계층.

이 모듈이 아는 것은 "어떻게 저장하는가" 뿐입니다.
"이 주문을 취소할 수 있는가" 같은 정책 판단은 store.py 에 그대로 둡니다.
판단하는 곳이 둘이 되면 둘은 반드시 어긋나기 때문입니다 (개발노트 7).
그래서 여기에는 트리거도 뷰도 없습니다.

데이터를 두 종류로 나눕니다.

  카탈로그   products · materials · category_groups · product_stock 의 초기값
             build_catalog.py 가 data/catalog.seed.db 로 구워 둡니다.

  상태       product_stock 의 현재값 · cart_items · orders
             실행 중에 바뀝니다. DB 를 두는 진짜 이유는 이쪽입니다.

주문 날짜는 카탈로그에 절대 날짜로 굽지 않습니다. 상대 날짜 템플릿을 DB에
저장하고 shop.db를 처음 만들 때 그 시점의 today를 기준으로 계산합니다.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

# 프로젝트 기준 경로
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CATALOG_SEED_PATH = DATA_DIR / "catalog.seed.db"   # 원본. 커밋한다
SHOP_DB_PATH = DATA_DIR / "shop.db"                # 실행 상태. .gitignore

# 4: agent_state 테이블 (확인 대기·미룬 작업·대화 내역을 사용자별로 영속)
SCHEMA_VERSION = 4

# 기본 사용자. 테스트·터미널 실행처럼 세션이 없는 곳은 이 사용자로 동작합니다.
# HTTP 서버는 브라우저마다 새 user_id 를 발급합니다 (server.py 의 세션 쿠키).
# cart_items / orders / agent_state 가 user_id 로 갈라집니다.
DEMO_USER_ID = "demo"


# --------------------------------------------------------------------------
# 스키마
# --------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    material         TEXT PRIMARY KEY,
    material_detail  TEXT NOT NULL,
    machine_washable INTEGER NOT NULL CHECK (machine_washable IN (0, 1)),
    care             TEXT NOT NULL
);

-- 대분류 -> 소분류. 순서에 의미가 있어서 sort_order 를 함께 둡니다
-- (available_groups() 가 정의 순서를 그대로 돌려주고 있습니다).
CREATE TABLE IF NOT EXISTS category_groups (
    group_name TEXT NOT NULL,
    category   TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (group_name, category)
);

-- 데모 초기화용 주문 원본. 날짜 자체가 아니라 오늘로부터 며칠 전인지 저장합니다.
-- 실행 중 seed.py를 import하지 않고도 reset_demo()가 주문 시나리오를 복원합니다.
CREATE TABLE IF NOT EXISTS demo_order_templates (
    order_id           TEXT PRIMARY KEY,
    product_id         TEXT NOT NULL REFERENCES products (product_id),
    size               INTEGER NOT NULL,
    quantity           INTEGER NOT NULL CHECK (quantity > 0),
    ordered_days_ago   INTEGER NOT NULL,
    shipped_days_ago   INTEGER,
    delivered_days_ago INTEGER,
    cancelled_days_ago INTEGER,
    returned_days_ago  INTEGER,
    status             TEXT NOT NULL,
    return_reason      TEXT
);

CREATE TABLE IF NOT EXISTS products (
    product_id       TEXT PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
    category         TEXT NOT NULL,
    gender           TEXT NOT NULL,
    brand            TEXT NOT NULL,
    price            INTEGER NOT NULL CHECK (price >= 0),
    color            TEXT NOT NULL,
    rating           REAL    NOT NULL,
    review_count     INTEGER NOT NULL,
    description      TEXT NOT NULL,
    material         TEXT NOT NULL,
    material_detail  TEXT NOT NULL,
    care             TEXT NOT NULL,
    machine_washable INTEGER NOT NULL CHECK (machine_washable IN (0, 1)),
    delivery_days    INTEGER NOT NULL,
    -- 임베딩 벡터. L2 정규화된 float32 를 그대로 담습니다(np.tobytes).
    -- 차원과 백엔드는 meta 테이블의 embed_dim / embed_backend 에 적힙니다.
    -- 이 컬럼이 NULL 이면 아직 인덱싱 안 된 것이고, 그때 의미 검색은
    -- 조용히 건너뜁니다 (기존 검색은 그대로 동작).
    embedding        BLOB
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);
CREATE INDEX IF NOT EXISTS idx_products_color    ON products (color);
CREATE INDEX IF NOT EXISTS idx_products_brand    ON products (brand);

-- 재고. 카탈로그가 초기값을 주지만 그 뒤로는 상태입니다.
-- CHECK 가 음수 재고를 막는 두 번째 벽입니다 (첫 번째는 tools.validate_call).
CREATE TABLE IF NOT EXISTS product_stock (
    product_id TEXT NOT NULL REFERENCES products (product_id),
    size       INTEGER NOT NULL,
    stock      INTEGER NOT NULL CHECK (stock >= 0),
    PRIMARY KEY (product_id, size)
);

CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY
);

-- 에이전트의 사용자별 상태. 확인 대기(pending)·미룬 작업(postponed)·대화 내역이
-- 여기 있어야 서버를 재시작해도 화면의 승인 버튼이 살아 있고, 두 사용자의
-- 대기가 섞이지 않습니다. 값은 JSON 하나입니다 — 정책(만료·열쇠 비교)은
-- agent.py 가 판단하고 여기는 저장만 합니다 (개발노트 7).
CREATE TABLE IF NOT EXISTS agent_state (
    user_id    TEXT PRIMARY KEY REFERENCES users (user_id),
    state      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- (user_id, product_id, size) 가 한 줄. 같은 상품 같은 사이즈는 병합됩니다.
-- 개발노트 5 의 count(줄 수) 와 quantity(총 수량) 구분이 여기서 스키마로 굳습니다.
--   count    = COUNT(*)
--   quantity = SUM(quantity)
CREATE TABLE IF NOT EXISTS cart_items (
    user_id    TEXT    NOT NULL REFERENCES users (user_id),
    product_id TEXT    NOT NULL REFERENCES products (product_id),
    size       INTEGER NOT NULL,
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (user_id, product_id, size)
);

-- product_name 과 price 를 주문에 복사해 둡니다. 정규화 위반이 아니라
-- 주문은 그 시점의 값을 박제해야 하기 때문입니다. 나중에 상품 가격이
-- 바뀌어도 과거 주문 금액은 바뀌면 안 됩니다.
-- return_reason 은 nullable 입니다 — checkout 이 만드는 주문에는 이 키가
-- 아예 없고 return_order 가 나중에 채웁니다.
CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    user_id       TEXT    NOT NULL REFERENCES users (user_id),
    product_id    TEXT    NOT NULL REFERENCES products (product_id),
    product_name  TEXT    NOT NULL,
    size          INTEGER NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    price         INTEGER NOT NULL,
    ordered_at    TEXT    NOT NULL,
    shipped_at    TEXT,
    delivered_at  TEXT,
    cancelled_at  TEXT,
    returned_at   TEXT,
    status        TEXT    NOT NULL,
    return_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_ordered_at ON orders (ordered_at);
CREATE INDEX IF NOT EXISTS idx_orders_status     ON orders (status);
"""


# --------------------------------------------------------------------------
# 커넥션
# --------------------------------------------------------------------------

def connect(path=SHOP_DB_PATH):
    """커넥션을 연다.

    PRAGMA 세 개가 중요합니다.
      foreign_keys  SQLite 는 기본이 꺼져 있습니다. FK 를 적어 놓고
                    안 지켜지는 일이 흔합니다.
      journal_mode  WAL 이 아니면 Streamlit 리런이 겹칠 때
                    "database is locked" 가 납니다.
      busy_timeout  그래도 겹치면 5초까지 기다립니다.

    check_same_thread=False 는 Streamlit 이 세션마다 다른 스레드에서
    돌기 때문입니다.
    """
    path = str(path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn):
    """스키마를 만든다. 이미 있으면 빠진 테이블만 더한다.

    전부 CREATE IF NOT EXISTS 라 몇 번 불러도 안전합니다. 그래서 기존 shop.db 를
    열 때도 부릅니다 — 스키마가 3 에서 4 로 올라가면서 agent_state 가 생겼는데,
    파일을 지우고 다시 복사하면 장바구니·주문이 날아가기 때문입니다.
    """
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)", (DEMO_USER_ID,)
    )
    conn.commit()


# --------------------------------------------------------------------------
# 사용자
# --------------------------------------------------------------------------

def new_user_id():
    """세션 쿠키에 담을 사용자 ID. 추측할 수 없어야 다른 사람의 장바구니를 못 본다."""
    return "u_" + secrets.token_urlsafe(12)


def ensure_user(conn, user_id):
    """사용자 행을 만든다. 이미 있으면 그대로. 새로 만들었으면 True."""
    cur = conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    return cur.rowcount == 1


def user_exists(conn, user_id):
    return conn.execute(
        "SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone() is not None


def next_order_number(conn):
    """다음 주문 번호(정수). 모든 사용자를 통틀어 유일해야 하므로 DB 전체에서 본다.

    checkout 은 BEGIN IMMEDIATE 안에서 이걸 부르므로, 두 사용자가 동시에 결제해도
    SQLite 의 쓰기 락이 번호 발급을 한 줄로 세웁니다.
    """
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(order_id, 5) AS INTEGER)) FROM orders"
        " WHERE order_id LIKE 'ORD-%'"
    ).fetchone()
    return (row[0] or 1000) + 1


# --------------------------------------------------------------------------
# 에이전트 상태 — 확인 대기·미룬 작업·대화 내역
# --------------------------------------------------------------------------

def save_agent_state(conn, user_id, state):
    """{turn, pending, postponed, last_results, history} 같은 dict 를 JSON 으로 저장."""
    conn.execute(
        "INSERT INTO agent_state (user_id, state, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET state = excluded.state,"
        " updated_at = excluded.updated_at",
        (user_id, json.dumps(state, ensure_ascii=False, default=str), time.time()),
    )
    conn.commit()


def load_agent_state(conn, user_id):
    """저장된 상태 dict. 없으면 None."""
    row = conn.execute(
        "SELECT state FROM agent_state WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def delete_agent_state(conn, user_id):
    conn.execute("DELETE FROM agent_state WHERE user_id = ?", (user_id,))
    conn.commit()


# --------------------------------------------------------------------------
# 카탈로그 적재 — build_catalog.py 가 부른다
# --------------------------------------------------------------------------

def load_catalog(conn, seed):
    """seed 모듈의 카탈로그를 DB 에 적재한다. 기존 카탈로그는 지우고 다시 넣는다.

    이 함수는 오프라인 카탈로그 생성에서만 호출됩니다. 서버와 Tool은 생성된
    SQLite 테이블을 직접 읽습니다.
    """
    conn.execute("DELETE FROM product_stock")
    conn.execute("DELETE FROM products")
    conn.execute("DELETE FROM materials")
    conn.execute("DELETE FROM category_groups")

    conn.executemany(
        "INSERT INTO materials (material, material_detail, machine_washable, care)"
        " VALUES (?, ?, ?, ?)",
        [
            (name, detail, int(washable), care)
            for name, (detail, washable, care) in seed.MATERIAL_INFO.items()
        ],
    )

    group_rows = []
    order = 0
    for group_name, categories in seed.CATEGORY_GROUPS.items():
        for category in categories:
            group_rows.append((group_name, category, order))
            order += 1
    conn.executemany(
        "INSERT INTO category_groups (group_name, category, sort_order)"
        " VALUES (?, ?, ?)",
        group_rows,
    )

    product_rows = []
    stock_rows = []
    for p in seed.PRODUCTS:
        product_rows.append((
            p["id"], p["name"], p["category"], p["gender"], p["brand"],
            p["price"], p["color"], p["rating"], p["review_count"],
            p["description"], p["material"], p["material_detail"],
            p["care"], int(p["machine_washable"]), p["delivery_days"],
        ))
        for size, stock in p["sizes"].items():
            stock_rows.append((p["id"], int(size), int(stock)))

    conn.executemany(
        "INSERT INTO products (product_id, name, category, gender, brand,"
        " price, color, rating, review_count, description, material,"
        " material_detail, care, machine_washable, delivery_days)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        product_rows,
    )
    conn.executemany(
        "INSERT INTO product_stock (product_id, size, stock) VALUES (?, ?, ?)",
        stock_rows,
    )
    conn.commit()
    return len(product_rows), len(stock_rows)


def load_demo_order_templates(conn, orders, today=None):
    """카탈로그를 만들 때 데모 주문을 상대 날짜 템플릿으로 저장한다."""
    today = today or date.today()

    def days_ago(value):
        return None if value is None else (today - date.fromisoformat(value)).days

    conn.execute("DELETE FROM demo_order_templates")
    conn.executemany(
        "INSERT INTO demo_order_templates (order_id, product_id, size, quantity,"
        " ordered_days_ago, shipped_days_ago, delivered_days_ago,"
        " cancelled_days_ago, returned_days_ago, status, return_reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["order_id"], row["product_id"], row["size"], row["quantity"],
                days_ago(row["ordered_at"]), days_ago(row.get("shipped_at")),
                days_ago(row.get("delivered_at")), days_ago(row.get("cancelled_at")),
                days_ago(row.get("returned_at")), row["status"],
                row.get("return_reason"),
            )
            for row in orders
        ],
    )
    conn.commit()


def seed_demo_orders(conn, today=None, user_id=DEMO_USER_ID):
    """DB의 상대 날짜 템플릿으로 데모 주문을 복원한다.

    상품명·가격도 products에서 읽으므로 실행 중 Python 시드 데이터에 의존하지 않는다.

    user_id 를 주면 그 사용자의 주문만 지우고 그 사용자 것으로 심는다.
    새 브라우저(새 사용자)도 "어제 주문한 거 취소해줘" 시나리오를 바로 해 볼 수
    있어야 하기 때문이다. 기본 사용자는 템플릿의 주문번호(ORD-1001…)를 그대로
    쓰고 — 테스트가 그 번호를 안다 — 다른 사용자는 전체에서 유일한 번호를 새로 받는다.
    """
    today = today or date.today()

    def when(days_ago):
        return None if days_ago is None else (
            today - timedelta(days=days_ago)).isoformat()

    conn.execute("DELETE FROM orders WHERE user_id = ?", (user_id,))
    rows = []
    templates = conn.execute(
        "SELECT t.*, p.name, p.price FROM demo_order_templates t"
        " JOIN products p ON p.product_id = t.product_id"
        " ORDER BY t.rowid"
    ).fetchall()
    number = next_order_number(conn)
    taken = {r[0] for r in conn.execute("SELECT order_id FROM orders")}
    for o in templates:
        if user_id == DEMO_USER_ID and o["order_id"] not in taken:
            order_id = o["order_id"]
        else:
            # 다른 사용자가 이미 그 번호를 받았으면 기본 사용자도 새 번호를 받는다.
            order_id = f"ORD-{number}"
            number += 1
        taken.add(order_id)
        rows.append((
            order_id, user_id, o["product_id"], o["name"],
            o["size"], o["quantity"], o["price"] * o["quantity"],
            when(o["ordered_days_ago"]), when(o["shipped_days_ago"]),
            when(o["delivered_days_ago"]), when(o["cancelled_days_ago"]),
            when(o["returned_days_ago"]), o["status"], o["return_reason"],
        ))
    conn.executemany(
        "INSERT INTO orders (order_id, user_id, product_id, product_name,"
        " size, quantity, price, ordered_at, shipped_at, delivered_at,"
        " cancelled_at, returned_at, status, return_reason)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


# --------------------------------------------------------------------------
# shop.db 준비 / 리셋
# --------------------------------------------------------------------------

def _looks_initialised(path):
    """카탈로그가 들어 있는 DB 인가.

    sqlite3.connect() 는 파일이 없으면 빈 파일을 만들어 버립니다.
    그래서 "파일이 있다" 만으로는 쓸 수 있는 DB 인지 알 수 없습니다.
    실제로 products 테이블에 행이 있는지까지 봐야 합니다.
    (빈 shop.db 가 만들어진 뒤로 서버가 계속 죽던 원인입니다)
    """
    try:
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master"
                " WHERE type='table' AND name='products'"
            ).fetchone()
            if not row or row[0] == 0:
                return False
            return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] > 0
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def ensure_shop_db(path=SHOP_DB_PATH, today=None):
    """실행용 DB 가 없으면 카탈로그 원본을 복사해서 만든다.

    seed 를 다시 돌려 만들지 않고 파일을 복사하는 이유는, 카탈로그 재생성이
    실행 중인 주문·장바구니를 날리지 않게 하기 위해서입니다.
    """
    path = Path(path)
    if path.exists() and _looks_initialised(path):
        # 이미 있는 DB 라도 스키마가 옛 버전일 수 있다 (예: agent_state 없음).
        # init_schema 는 빠진 테이블만 더하고 데이터는 건드리지 않는다.
        conn = connect(path)
        try:
            init_schema(conn)
        finally:
            conn.close()
        return path
    if not CATALOG_SEED_PATH.exists():
        raise FileNotFoundError(
            f"카탈로그 원본이 없습니다: {CATALOG_SEED_PATH}\n"
            f"먼저 `python build_catalog.py` 를 실행하세요."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("-wal", "-shm"):
        stale = path.with_name(path.name + suffix)
        try:
            stale.unlink()
        except (FileNotFoundError, OSError):
            pass
    shutil.copyfile(CATALOG_SEED_PATH, path)

    conn = connect(path)
    try:
        init_schema(conn)
        seed_demo_orders(conn, today or date.today())
    finally:
        conn.close()
    return path


def reset_demo(path=SHOP_DB_PATH, today=None):
    """시연용 초기화. 장바구니를 비우고 카탈로그·재고·주문을 처음 상태로 돌린다.

    파일을 지우고 다시 복사합니다. 부분 초기화보다 단순하고,
    "무엇이 남았는지" 를 고민할 필요가 없습니다.
    """
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    return ensure_shop_db(path, today)


def reset_user(conn, user_id, today=None):
    """한 사용자의 장바구니·주문·에이전트 상태만 처음 상태로 되돌린다.

    상품과 재고는 사용자들이 함께 보는 카탈로그라 건드리지 않는다. HTTP 서버에서
    한 사용자가 초기화 버튼을 눌렀다는 이유로 다른 사용자의 데이터나 진행 중인
    승인 대기까지 사라져서는 안 된다.
    """
    ensure_user(conn, user_id)
    conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM agent_state WHERE user_id = ?", (user_id,))
    # 이 함수가 해당 사용자의 기존 주문을 지우고 데모 주문을 다시 넣은 뒤 commit 한다.
    return seed_demo_orders(conn, today=today, user_id=user_id)


# --------------------------------------------------------------------------
# 재고 — 확인과 차감을 한 문장으로
# --------------------------------------------------------------------------

def take_stock(conn, product_id, size, quantity):
    """재고를 차감한다. 성공하면 True.

    지금 store.checkout 은 "재고를 확인하고 → 차감" 두 걸음입니다.
    그 사이에 다른 경로가 같은 재고를 가져가면 음수가 됩니다.
    발표 3막의 승인 TOCTOU 와 같은 모양이고, 여기서는 조건을 UPDATE 안으로
    넣어서 한 문장으로 만듭니다. rowcount 가 0이면 조건이 안 맞은 것입니다.
    """
    cur = conn.execute(
        "UPDATE product_stock SET stock = stock - ?"
        " WHERE product_id = ? AND size = ? AND stock >= ?",
        (quantity, product_id, size, quantity),
    )
    return cur.rowcount == 1


def give_back_stock(conn, product_id, size, quantity):
    """취소로 재고를 되돌린다."""
    cur = conn.execute(
        "UPDATE product_stock SET stock = stock + ?"
        " WHERE product_id = ? AND size = ?",
        (quantity, product_id, size),
    )
    return cur.rowcount == 1


# --------------------------------------------------------------------------
# 읽기 — store.py 가 메모리 구조를 채울 때 쓴다
# --------------------------------------------------------------------------

def fetch_products(conn):
    """상품을 seed.PRODUCTS 와 같은 모양의 dict 리스트로 돌려준다.

    store.py 가 지금 쓰고 있는 구조를 그대로 만들어 주는 것이 목적입니다.
    이 함수가 있으면 Store.__init__ 의 두 줄만 바꾸면 되고
    tools.py 아래는 한 줄도 바뀌지 않습니다.
    """
    stock_by_product = {}
    for row in conn.execute(
        "SELECT product_id, size, stock FROM product_stock ORDER BY product_id, size"
    ):
        stock_by_product.setdefault(row["product_id"], {})[row["size"]] = row["stock"]

    products = []
    for row in conn.execute("SELECT * FROM products ORDER BY product_id"):
        products.append({
            "id": row["product_id"],
            "name": row["name"],
            "category": row["category"],
            "gender": row["gender"],
            "brand": row["brand"],
            "price": row["price"],
            "color": row["color"],
            "sizes": stock_by_product.get(row["product_id"], {}),
            "rating": row["rating"],
            "review_count": row["review_count"],
            "description": row["description"],
            "material": row["material"],
            "material_detail": row["material_detail"],
            "care": row["care"],
            "machine_washable": bool(row["machine_washable"]),
            "delivery_days": row["delivery_days"],
        })
    return products


def _product_filter_where(product_name=None, group=None, category=None, gender=None, brand=None,
                          max_price=None, min_price=None, color=None, size=None,
                          material=None, machine_washable=None):
    """상품 필터의 WHERE와 바인딩 값을 한 곳에서 만든다."""
    clauses = []
    params = []

    if product_name is not None:
        # 부분 일치의 %/_는 검색 문법이 아니라 사용자 입력 자체로 취급한다.
        # 값은 바인딩하고 LIKE 와일드카드도 이스케이프한다.
        needle = product_name.lower().replace("\\", "\\\\")
        needle = needle.replace("%", "\\%").replace("_", "\\_")
        clauses.append("LOWER(p.name) LIKE ? ESCAPE '\\'")
        params.append(f"%{needle}%")
    if group is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM category_groups cg"
            " WHERE cg.category = p.category AND cg.group_name = ?)"
        )
        params.append(group)
    if category is not None:
        clauses.append("p.category = ?")
        params.append(category)
    if gender is not None:
        clauses.append("p.gender IN (?, '공용')")
        params.append(gender)
    if brand is not None:
        clauses.append("p.brand = ?")
        params.append(brand)
    if max_price is not None:
        clauses.append("p.price <= ?")
        params.append(max_price)
    if min_price is not None:
        clauses.append("p.price >= ?")
        params.append(min_price)
    if color is not None:
        clauses.append("p.color = ?")
        params.append(color)
    if material is not None:
        clauses.append("p.material = ?")
        params.append(material)
    if machine_washable is not None:
        clauses.append("p.machine_washable = ?")
        params.append(int(machine_washable))
    if size is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM product_stock ps"
            " WHERE ps.product_id = p.product_id"
            " AND ps.size = ? AND ps.stock > 0)"
        )
        params.append(size)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def search_product_ids(conn, *, sort=None, limit=5, **filters):
    """구조화 조건만 SQL로 검색한다. 모든 값은 파라미터로 바인딩한다."""
    where, params = _product_filter_where(**filters)
    order_by = {
        "price_asc": "p.price ASC, p.product_id ASC",
        "price_desc": "p.price DESC, p.product_id ASC",
        "rating": "p.rating DESC, p.review_count DESC, p.product_id ASC",
        "review": "p.review_count DESC, p.rating DESC, p.product_id ASC",
    }.get(sort, "p.product_id ASC")
    rows = conn.execute(
        f"SELECT p.product_id FROM products p{where}"
        f" ORDER BY {order_by} LIMIT ?",
        (*params, int(limit)),
    )
    return [row[0] for row in rows]


def count_products(conn, **filters):
    """search_product_ids와 동일한 구조화 조건의 전체 상품 수."""
    where, params = _product_filter_where(**filters)
    return conn.execute(
        f"SELECT COUNT(*) FROM products p{where}", params
    ).fetchone()[0]


def fetch_orders(conn, user_id=DEMO_USER_ID):
    """주문을 seed.build_orders() 와 같은 모양의 dict 리스트로 돌려준다."""
    orders = []
    for row in conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY ordered_at DESC, order_id",
        (user_id,),
    ):
        orders.append({
            "order_id": row["order_id"],
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "size": row["size"],
            "quantity": row["quantity"],
            "price": row["price"],
            "ordered_at": row["ordered_at"],
            "shipped_at": row["shipped_at"],
            "delivered_at": row["delivered_at"],
            "cancelled_at": row["cancelled_at"],
            "returned_at": row["returned_at"],
            "status": row["status"],
            "return_reason": row["return_reason"],
        })
    return orders


def enum_values(conn):
    """Tool 스키마 enum에 쓸 값을 현재 DB에서 뽑는다."""
    def distinct(column, table="products"):
        return [r[0] for r in conn.execute(
            f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}"
        )]

    groups = [r[0] for r in conn.execute(
        "SELECT group_name FROM category_groups GROUP BY group_name"
        " ORDER BY MIN(sort_order)"
    )]
    return {
        "group": groups,
        "category": distinct("category"),
        "gender": distinct("gender"),
        "brand": distinct("brand"),
        "color": distinct("color"),
        "material": distinct("material", "materials"),
        "status": [r[0] for r in conn.execute(
            "SELECT DISTINCT status FROM orders ORDER BY status"
        )],
    }


def category_group_map(conn):
    """DB에 저장된 대분류 → 소분류 매핑을 정의 순서대로 반환한다."""
    groups = {}
    for row in conn.execute(
        "SELECT group_name, category FROM category_groups ORDER BY sort_order"
    ):
        groups.setdefault(row["group_name"], []).append(row["category"])
    return groups


def material_info_map(conn):
    """DB에 저장된 소재 정보를 {소재: (혼용률, 세탁가능, 관리법)}로 반환한다."""
    return {
        row["material"]: (
            row["material_detail"], bool(row["machine_washable"]), row["care"]
        )
        for row in conn.execute(
            "SELECT material, material_detail, machine_washable, care FROM materials"
        )
    }


def read_catalog_metadata(path=None):
    """서버와 Tool이 사용할 카탈로그 메타데이터를 SQLite에서 읽는다."""
    if path is None:
        path = SHOP_DB_PATH if _looks_initialised(SHOP_DB_PATH) else CATALOG_SEED_PATH
    conn = connect(path)
    try:
        return {
            "enums": enum_values(conn),
            "category_groups": category_group_map(conn),
            "materials": material_info_map(conn),
        }
    finally:
        conn.close()


def memory_db(today=None):
    """카탈로그 원본을 그대로 담은 인메모리 DB. 격리된 사본이다.

    Connection.backup() 이 파일 DB 를 메모리로 통째 복제합니다.
    Store 를 deepcopy 하는 것보다 확실하고, 평가셋의 "매 시행 전 초기 상태
    복원" 도 여기에 얹힙니다 (노트 17 의 샌드박스가 하던 역할).

    테스트가 이걸 쓰면 shop.db 를 건드리지 않으므로 서로 간섭하지 않습니다.
    """
    if not CATALOG_SEED_PATH.exists():
        raise FileNotFoundError(
            f"카탈로그 원본이 없습니다: {CATALOG_SEED_PATH}\n"
            f"먼저 `python build_catalog.py` 를 실행하세요."
        )
    source = connect(CATALOG_SEED_PATH)
    conn = connect(":memory:")
    try:
        source.backup(conn)
    finally:
        source.close()
    init_schema(conn)
    seed_demo_orders(conn, today)
    return conn


def fetch_cart(conn, products, user_id=DEMO_USER_ID):
    """장바구니를 store.py 가 쓰는 모양으로 돌려준다.

    한 줄의 모양이 이렇고, product 가 **참조**여야 합니다.
        {"product": <self.products 안의 dict 객체>, "size": 270, "quantity": 2}
    store 가 이 가정 위에서 동작합니다 — 상품 가격이 바뀌면 장바구니 금액도
    같이 따라가야 하므로 값을 복사하면 안 됩니다.

    ORDER BY rowid 는 담은 순서를 지키기 위한 것입니다.
    "두 번째 거 담아줘" 가 가리키는 순서와 화면 순서가 같아야 합니다.
    """
    by_id = {p["id"]: p for p in products}
    cart = []
    for row in conn.execute(
        "SELECT product_id, size, quantity FROM cart_items"
        " WHERE user_id = ? ORDER BY rowid",
        (user_id,),
    ):
        product = by_id.get(row["product_id"])
        if product is None:
            continue  # 카탈로그에서 사라진 상품. 조용히 건너뛴다
        cart.append({
            "product": product,
            "size": row["size"],
            "quantity": row["quantity"],
        })
    return cart
