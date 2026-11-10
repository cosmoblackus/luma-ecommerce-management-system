import json
import hmac
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import CookieError, SimpleCookie
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE = Path(os.environ.get("LUMASTORE_DB_PATH", ROOT / "data" / "lumastore.sqlite3"))
HOST = os.environ.get("LUMASTORE_HOST", "0.0.0.0" if os.environ.get("RENDER") else "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("LUMASTORE_PORT", "8000")))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "")
SESSION_TTL = 8 * 60 * 60
LOGIN_WINDOW = 15 * 60
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPTS = {}
LOGIN_ATTEMPTS_LOCK = threading.Lock()
CATEGORIES = {"Home", "Accessories", "Wellness", "Office"}
ORDER_STATUSES = {"Processing", "Shipped", "Delivered"}
PUBLIC_FILES = {"index.html", "app.js", "styles.css"}

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
    DATABASE_ERROR = sqlite3.Error
else:
    DATABASE_ERROR = (sqlite3.Error, psycopg.Error)

PRODUCTS = [
    (1, "Ripple Glass Set", "Home", 38, 31, 4.8, ["Beautifully made and surprisingly sturdy."], "Set of two hand-finished tumblers with a softly rippled silhouette.", "🥃", "#dfc4ae", 84),
    (2, "Cedar Table Lamp", "Home", 124, 12, 4.9, ["Warm light and a lovely minimal shape."], "A dimmable table lamp with a sculptural cedar base and linen shade.", "💡", "#d7c8a9", 63),
    (3, "Soft Form Tote", "Accessories", 68, 21, 4.7, ["My daily go-to. Fits everything."], "A generous carryall in supple recycled canvas with an inner pocket.", "👜", "#b8c8a7", 98),
    (4, "Stillness Incense", "Wellness", 22, 42, 4.6, ["A calm, clean scent for evenings."], "Japanese-style incense with hinoki, moss and a trace of bergamot.", "🕯️", "#d4b5ab", 121),
    (5, "Arc Desk Organiser", "Office", 52, 8, 4.8, ["Makes my desk feel instantly tidier."], "Powder-coated steel organiser for the essentials that deserve a place.", "🗃️", "#aec6cc", 57),
    (6, "Sunday Throw", "Home", 96, 16, 4.9, ["Soft, weighty and exactly the right size."], "Brushed cotton throw woven in a warm, oat-toned check.", "🧶", "#d8c58d", 75),
    (7, "Horizon Notebook", "Office", 18, 56, 4.5, ["The paper feels excellent."], "A lay-flat notebook made with fountain-pen-friendly, recycled paper.", "📒", "#b7c2d7", 136),
    (8, "Stoneware Pourer", "Home", 44, 20, 4.7, ["A beautiful object that gets used every day."], "A small glazed stoneware pourer for olive oil, milk or wildflowers.", "🏺", "#cab4a0", 68),
]

ORDERS = [
    ("LS-1048", "Tara Okafor", "2026-11-08", 162, "Processing"),
    ("LS-1047", "Nia Mensah", "2026-11-08", 68, "Shipped"),
    ("LS-1046", "Mia Johnson", "2026-11-07", 124, "Delivered"),
    ("LS-1045", "Sofia Bello", "2026-11-07", 90, "Delivered"),
    ("LS-1044", "Zainab Ali", "2026-11-06", 52, "Delivered"),
]


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def connect_database():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("PostgreSQL requires the psycopg package.")
        return psycopg.connect(DATABASE_URL, row_factory=dict_row)
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def execute(db, statement, params=()):
    if DATABASE_URL:
        statement = statement.replace("?", "%s")
    return db.execute(statement, params)


def execute_many(db, statement, rows):
    if DATABASE_URL:
        statement = statement.replace("?", "%s")
    return db.executemany(statement, rows)


def initialize_database():
    with connect_database() as db:
        schema = [
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL CHECK (price > 0),
                stock INTEGER NOT NULL CHECK (stock >= 0),
                rating REAL NOT NULL DEFAULT 5,
                reviews TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL,
                icon TEXT NOT NULL DEFAULT '✦',
                color TEXT NOT NULL DEFAULT '#c8d6bc',
                sales INTEGER NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                customer TEXT NOT NULL,
                email TEXT NOT NULL,
                order_date TEXT NOT NULL,
                total REAL NOT NULL CHECK (total >= 0),
                status TEXT NOT NULL CHECK (status IN ('Processing', 'Shipped', 'Delivered'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
                product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                unit_price REAL NOT NULL CHECK (unit_price >= 0)
            )
            """,
        ]
        if DATABASE_URL:
            schema = [
                statement.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
                for statement in schema
            ]
        for statement in schema:
            execute(db, statement)
        if execute(db, "SELECT COUNT(*) AS count FROM products").fetchone()["count"] == 0:
            execute_many(
                db,
                """INSERT INTO products
                   (id, name, category, price, stock, rating, reviews, description, icon, color, sales)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(p[0], p[1], p[2], p[3], p[4], p[5], json.dumps(p[6]), *p[7:]) for p in PRODUCTS],
            )
        if DATABASE_URL:
            execute(
                db,
                """SELECT setval(pg_get_serial_sequence('products', 'id'),
                                COALESCE(MAX(id), 0) + 1, false) FROM products"""
            )
        if execute(db, "SELECT COUNT(*) AS count FROM orders").fetchone()["count"] == 0:
            execute_many(
                db,
                """INSERT INTO orders (order_number, customer, email, order_date, total, status)
                   VALUES (?, ?, '', ?, ?, ?)""",
                ORDERS,
            )


def product_json(row):
    product = dict(row)
    product["reviews"] = json.loads(product["reviews"])
    return product


def order_json(row):
    order = dict(row)
    order.pop("email", None)
    order["date"] = date.fromisoformat(order.pop("order_date")).strftime("%b %d, %Y").replace(" 0", " ")
    return order


def session_signature(expiration):
    return hmac.new(
        ADMIN_SESSION_SECRET.encode("utf-8"),
        str(expiration).encode("ascii"),
        "sha256",
    ).hexdigest()


def is_valid_admin_session(handler):
    if not ADMIN_SESSION_SECRET:
        return False
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
    except CookieError:
        return False
    session = cookie.get("luma_admin")
    if not session:
        return False
    try:
        expiration_text, signature = session.value.split(".", 1)
        expiration = int(expiration_text)
    except (ValueError, TypeError):
        return False
    if expiration < time.time():
        return False
    return hmac.compare_digest(signature, session_signature(expiration))


def client_address(handler):
    return handler.client_address[0]


def check_login_rate_limit(handler):
    now = time.time()
    address = client_address(handler)
    with LOGIN_ATTEMPTS_LOCK:
        attempts, started = LOGIN_ATTEMPTS.get(address, (0, now))
        if now - started >= LOGIN_WINDOW:
            LOGIN_ATTEMPTS[address] = (0, now)
        elif attempts >= MAX_LOGIN_ATTEMPTS:
            raise ApiError("Too many sign-in attempts. Try again in 15 minutes.", 429)


def record_login_failure(handler):
    now = time.time()
    address = client_address(handler)
    with LOGIN_ATTEMPTS_LOCK:
        attempts, started = LOGIN_ATTEMPTS.get(address, (0, now))
        if now - started >= LOGIN_WINDOW:
            attempts, started = 0, now
        LOGIN_ATTEMPTS[address] = (attempts + 1, started)


def clear_login_failures(handler):
    with LOGIN_ATTEMPTS_LOCK:
        LOGIN_ATTEMPTS.pop(client_address(handler), None)


def validate_product(payload):
    if not isinstance(payload, dict):
        raise ApiError("Expected a product object.")
    name = payload.get("name")
    category = payload.get("category")
    description = payload.get("description")
    try:
        price = float(payload.get("price"))
        stock = int(payload.get("stock"))
    except (TypeError, ValueError):
        raise ApiError("Price and stock must be valid numbers.")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 40:
        raise ApiError("Product name must be between 1 and 40 characters.")
    if category not in CATEGORIES:
        raise ApiError("Choose a valid product category.")
    if price <= 0 or price > 1000000:
        raise ApiError("Price must be greater than zero.")
    if stock < 0 or stock > 1000000:
        raise ApiError("Stock must be zero or greater.")
    if not isinstance(description, str) or len(description.strip()) < 12 or len(description) > 2000:
        raise ApiError("Description must be between 12 and 2000 characters.")
    return {
        "name": name.strip(),
        "category": category,
        "price": round(price, 2),
        "stock": stock,
        "description": description.strip(),
    }


def state_json(db, include_orders):
    products = [product_json(row) for row in execute(db, "SELECT * FROM products ORDER BY id")]
    orders = []
    if include_orders:
        orders = [
            order_json(row) for row in execute(
                db,
                """SELECT id, order_number, customer, order_date, total, status FROM orders
                   ORDER BY order_date DESC, CAST(SUBSTR(order_number, 4) AS INTEGER) DESC"""
            )
        ]
    return {"products": products, "orders": orders, "authenticated": include_orders}


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "LumaStoreDemo/1.0"

    def send_json(self, payload, status=200, extra_headers=()):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def is_admin(self):
        return is_valid_admin_session(self)

    def require_admin(self):
        if not self.is_admin():
            raise ApiError("Admin sign-in is required for this action.", 401)

    def login(self):
        check_login_rate_limit(self)
        if len(ADMIN_PASSWORD) < 12 or len(ADMIN_SESSION_SECRET) < 32:
            raise ApiError("Admin sign-in requires a 12-character password and a 32-character session secret.", 503)
        payload = self.read_json()
        password = payload.get("password") if isinstance(payload, dict) else None
        if not isinstance(password, str) or not hmac.compare_digest(password, ADMIN_PASSWORD):
            record_login_failure(self)
            raise ApiError("The admin password is incorrect.", 401)
        clear_login_failures(self)
        expiration = int(time.time()) + SESSION_TTL
        value = f"{expiration}.{session_signature(expiration)}"
        secure = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https"
        cookie = f"luma_admin={value}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"
        if secure:
            cookie += "; Secure"
        self.send_json(
            {"authenticated": True},
            extra_headers=(("Set-Cookie", cookie),),
        )

    def logout(self):
        secure = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https"
        cookie = "luma_admin=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
        if secure:
            cookie += "; Secure"
        self.send_json(
            {"authenticated": False},
            extra_headers=(("Set-Cookie", cookie),),
        )

    def read_json(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError("Requests must use application/json.", 415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("Invalid request body size.")
        if length <= 0 or length > 65536:
            raise ApiError("Request body must be between 1 and 65536 bytes.")
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ApiError("Request body must be valid JSON.")

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            with connect_database() as db:
                execute(db, "SELECT 1")
            self.send_json({"status": "ok"})
            return
        if path == "/api/auth/status":
            self.send_json({"authenticated": self.is_admin()})
            return
        if path == "/api/state":
            with connect_database() as db:
                self.send_json(state_json(db, self.is_admin()))
            return
        if path.startswith("/api/"):
            self.send_json({"error": "API route not found."}, 404)
            return
        filename = "index.html" if path == "/" else unquote(path.lstrip("/"))
        if filename not in PUBLIC_FILES:
            self.send_error(404)
            return
        content = (ROOT / filename).read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; img-src 'self' data:")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            self.check_same_origin()
            if path == "/api/auth/login":
                self.login()
                return
            if path == "/api/auth/logout":
                self.logout()
                return
            payload = self.read_json()
            if path == "/api/products":
                self.require_admin()
                self.create_product(payload)
            elif path == "/api/orders":
                self.create_order(payload)
            else:
                match = re.fullmatch(r"/api/products/(\d+)/reviews", path)
                if match:
                    self.create_review(int(match.group(1)), payload)
                else:
                    raise ApiError("API route not found.", 404)
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)
        except DATABASE_ERROR:
            self.send_json({"error": "The database could not complete this request."}, 500)

    def do_PUT(self):
        match = re.fullmatch(r"/api/(products|orders)/(\d+)", urlsplit(self.path).path)
        if not match:
            self.send_json({"error": "API route not found."}, 404)
            return
        try:
            self.check_same_origin()
            self.require_admin()
            payload = self.read_json()
            with connect_database() as db:
                if match.group(1) == "products":
                    product = validate_product(payload)
                    result = execute(
                        db,
                        """UPDATE products SET name=?, category=?, price=?, stock=?, description=?
                           WHERE id=?""",
                        (product["name"], product["category"], product["price"], product["stock"],
                         product["description"], int(match.group(2))),
                    )
                    if not result.rowcount:
                        raise ApiError("Product not found.", 404)
                    row = execute(db, "SELECT * FROM products WHERE id=?", (int(match.group(2)),)).fetchone()
                    self.send_json(product_json(row))
                else:
                    status = payload.get("status") if isinstance(payload, dict) else None
                    if status not in ORDER_STATUSES:
                        raise ApiError("Choose a valid order status.")
                    result = execute(db, "UPDATE orders SET status=? WHERE id=?", (status, int(match.group(2))))
                    if not result.rowcount:
                        raise ApiError("Order not found.", 404)
                    row = execute(db, "SELECT * FROM orders WHERE id=?", (int(match.group(2)),)).fetchone()
                    self.send_json(order_json(row))
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)
        except ValueError:
            self.send_json({"error": "The database could not complete this request."}, 500)
        except DATABASE_ERROR:
            self.send_json({"error": "The database could not complete this request."}, 500)

    def do_DELETE(self):
        match = re.fullmatch(r"/api/products/(\d+)", urlsplit(self.path).path)
        if not match:
            self.send_json({"error": "API route not found."}, 404)
            return
        try:
            self.check_same_origin()
            self.require_admin()
            with connect_database() as db:
                result = execute(db, "DELETE FROM products WHERE id=?", (int(match.group(1)),))
                if not result.rowcount:
                    raise ApiError("Product not found.", 404)
            self.send_json({"deleted": True})
        except ApiError as error:
            self.send_json({"error": str(error)}, error.status)
        except DATABASE_ERROR:
            self.send_json({"error": "The database could not complete this request."}, 500)

    def create_product(self, payload):
        try:
            product = validate_product(payload)
            with connect_database() as db:
                row_id = execute(
                    db,
                    """INSERT INTO products (name, category, price, stock, description)
                       VALUES (?, ?, ?, ?, ?) RETURNING id""",
                    (product["name"], product["category"], product["price"], product["stock"],
                     product["description"]),
                ).fetchone()["id"]
                row = execute(db, "SELECT * FROM products WHERE id=?", (row_id,)).fetchone()
                self.send_json(product_json(row), 201)
        except ApiError:
            raise
        except DATABASE_ERROR:
            self.send_json({"error": "The database could not save this product."}, 500)

    def check_same_origin(self):
        origin = self.headers.get("Origin")
        scheme = self.headers.get("X-Forwarded-Proto", "http").split(",", 1)[0].strip()
        expected_origin = f"{scheme}://{self.headers.get('Host', '')}"
        if origin and origin != expected_origin:
            raise ApiError("Cross-origin requests are not allowed.", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ApiError("Cross-origin requests are not allowed.", 403)

    def create_order(self, payload):
        if not isinstance(payload, dict):
            raise ApiError("Expected an order object.")
        first = payload.get("firstName", "").strip() if isinstance(payload.get("firstName"), str) else ""
        last = payload.get("lastName", "").strip() if isinstance(payload.get("lastName"), str) else ""
        items = payload.get("items")
        if not first or len(first) > 80 or not last or len(last) > 80:
            raise ApiError("Enter a valid first and last name.")
        if not isinstance(items, list) or not items or len(items) > 100:
            raise ApiError("An order must contain at least one item.")
        normalized = {}
        for item in items:
            if not isinstance(item, dict):
                raise ApiError("Each order item must include a product and quantity.")
            try:
                product_id = int(item.get("productId"))
                quantity = int(item.get("quantity"))
            except (TypeError, ValueError):
                raise ApiError("Order item IDs and quantities must be valid numbers.")
            if product_id < 1 or quantity < 1 or quantity > 99:
                raise ApiError("Order quantities must be between 1 and 99.")
            normalized[product_id] = normalized.get(product_id, 0) + quantity
        if any(quantity > 99 for quantity in normalized.values()):
            raise ApiError("Order quantities must be between 1 and 99.")

        with connect_database() as db:
            if DATABASE_URL:
                execute(db, "SELECT pg_advisory_xact_lock(726, 1049)")
            else:
                execute(db, "BEGIN IMMEDIATE")
            lines = []
            total = 0
            for product_id, quantity in normalized.items():
                lock_clause = " FOR UPDATE" if DATABASE_URL else ""
                row = execute(
                    db,
                    f"SELECT id, name, price, stock FROM products WHERE id=?{lock_clause}",
                    (product_id,),
                ).fetchone()
                if row is None:
                    raise ApiError("A product in your cart is no longer available.")
                if quantity > row["stock"]:
                    raise ApiError(f"Only {row['stock']} of {row['name']} remain in stock.")
                lines.append((row, quantity))
                total += row["price"] * quantity
            if DATABASE_URL:
                previous = execute(
                    db,
                    """SELECT MAX(CAST(SUBSTR(order_number, 4) AS INTEGER))
                       FROM orders WHERE order_number ~ '^LS-[0-9]+$'"""
                ).fetchone()["max"]
            else:
                previous = execute(
                    db,
                    """SELECT MAX(CAST(SUBSTR(order_number, 4) AS INTEGER))
                       FROM orders WHERE order_number GLOB 'LS-[0-9]*'"""
                ).fetchone()[0]
            previous = previous or 1048
            order_number = f"LS-{previous + 1}"
            order_id = execute(
                db,
                """INSERT INTO orders (order_number, customer, email, order_date, total, status)
                   VALUES (?, ?, '', ?, ?, 'Processing') RETURNING id""",
                (order_number, f"{first} {last}", date.today().isoformat(), round(total, 2)),
            ).fetchone()["id"]
            for row, quantity in lines:
                execute(
                    db,
                    """INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?)""",
                    (order_id, row["id"], row["name"], quantity, row["price"]),
                )
                execute(
                    db,
                    "UPDATE products SET stock=stock-?, sales=sales+? WHERE id=?",
                    (quantity, quantity, row["id"]),
                )
            result = execute(db, "SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            updated_products = [
                product_json(row) for row in execute(db, "SELECT * FROM products ORDER BY id")
            ]
            self.send_json({"order": order_json(result), "products": updated_products}, 201)

    def create_review(self, product_id, payload):
        review = payload.get("review") if isinstance(payload, dict) else None
        if not isinstance(review, str) or len(review.strip()) < 4 or len(review.strip()) > 500:
            raise ApiError("A review must be between 4 and 500 characters.")
        with connect_database() as db:
            row = execute(db, "SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if row is None:
                raise ApiError("Product not found.", 404)
            reviews = json.loads(row["reviews"])
            reviews.append(review.strip())
            rating = min(5, round(row["rating"] + 0.1, 1))
            execute(db, "UPDATE products SET reviews=?, rating=? WHERE id=?",
                    (json.dumps(reviews, ensure_ascii=False), rating, product_id))
            updated = execute(db, "SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            self.send_json(product_json(updated), 201)

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"LumaStore is running at http://{HOST}:{PORT}/")
    print(f"Database: {'PostgreSQL' if DATABASE_URL else DATABASE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping LumaStore.")
    finally:
        server.server_close()
