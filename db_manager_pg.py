"""
db_manager_pg.py — PostgreSQL version for cloud deployment
Replaces SQLite db_manager.py when hosted on Railway + Supabase.

Key differences from SQLite version:
- Uses psycopg2 instead of sqlite3
- SERIAL instead of AUTOINCREMENT
- NOW() instead of datetime('now')
- %s placeholders instead of ?
- RETURNING id instead of lastrowid
- No executescript — uses individual CREATE TABLE statements
"""

import os
import re
from contextlib import contextmanager

# Use pg8000 — pure Python, no system libraries needed
import pg8000.native as pg8000

# ── Connection ────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def _parse_url(url):
    """Parse postgresql://user:pass@host:port/dbname"""
    m = re.match(
        r'postgresql(?:\+\w+)?://([^:]+):([^@]+)@([^:/]+):?(\d*)/(\S+)', url)
    if not m:
        raise ValueError(f"Cannot parse DATABASE_URL: {url[:40]}...")
    user, password, host, port, dbname = m.groups()
    return dict(user=user, password=password, host=host,
                port=int(port or 5432), database=dbname)


class _DictConn:
    """Wraps pg8000 to return dicts like psycopg2 RealDictCursor."""
    def __init__(self, params):
        self._conn = pg8000.Connection(**params, ssl_context=True)

    def cursor(self):
        return _DictCursor(self._conn)

    def commit(self):   self._conn.run("COMMIT")
    def rollback(self): self._conn.run("ROLLBACK")
    def close(self):    self._conn.close()


class _DictCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []
        self._cols = []

    def execute(self, sql, params=None):
        # pg8000 native uses :1 :2 style — convert %s to $1 $2
        idx = [0]
        def replacer(m):
            idx[0] += 1
            return f"${idx[0]}"
        sql2 = re.sub(r'%s', replacer, sql)
        if params:
            result = self._conn.run(sql2, *params)
        else:
            result = self._conn.run(sql2)
        self._cols = [c["name"] for c in (self._conn.columns or [])]
        self._rows = [dict(zip(self._cols, r)) for r in (result or [])]
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class DatabaseManager:
    def __init__(self, db_path=None):
        self.params = _parse_url(DATABASE_URL)

    @contextmanager
    def get_connection(self):
        conn = _DictConn(self.params)
        conn._conn.run("BEGIN")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Initialize Tables ─────────────────────────────────────────
    def initialize(self):
        """Create all tables if they don't exist."""
        with self.get_connection() as conn:
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    ProductID   SERIAL PRIMARY KEY,
                    Name        TEXT NOT NULL,
                    Category    TEXT NOT NULL DEFAULT 'Uncategorized',
                    BaseSKU     TEXT UNIQUE NOT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS inventory (
                    InventoryID       SERIAL PRIMARY KEY,
                    ProductID         INTEGER NOT NULL REFERENCES products(ProductID) ON DELETE CASCADE,
                    Price             REAL NOT NULL DEFAULT 0.0,
                    Barcode           TEXT UNIQUE,
                    Quantity          INTEGER NOT NULL DEFAULT 0,
                    LowStockThreshold INTEGER NOT NULL DEFAULT 10,
                    Location          TEXT DEFAULT '',
                    UpdatedAt         TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    TransactionID SERIAL PRIMARY KEY,
                    InventoryID   INTEGER REFERENCES inventory(InventoryID) ON DELETE SET NULL,
                    Type          TEXT NOT NULL CHECK(Type IN ('IN','OUT','ADJUST','IMPORT')),
                    Quantity      INTEGER NOT NULL,
                    Notes         TEXT DEFAULT '',
                    Date          TIMESTAMP DEFAULT NOW(),
                    UserID        INTEGER DEFAULT NULL,
                    UserName      TEXT DEFAULT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    UserID    SERIAL PRIMARY KEY,
                    Name      TEXT NOT NULL,
                    PIN       TEXT NOT NULL,
                    Role      TEXT NOT NULL DEFAULT 'Staff'
                              CHECK(Role IN ('Admin','Staff')),
                    Active    INTEGER NOT NULL DEFAULT 1,
                    CreatedAt TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS assembly_requests (
                    RequestID     SERIAL PRIMARY KEY,
                    ProductID     INTEGER NOT NULL REFERENCES products(ProductID),
                    InventoryID   INTEGER NOT NULL REFERENCES inventory(InventoryID),
                    QtyToAssemble INTEGER NOT NULL DEFAULT 1,
                    Notes         TEXT DEFAULT '',
                    Status        TEXT NOT NULL DEFAULT 'PENDING'
                                  CHECK(Status IN ('PENDING','APPROVED','REJECTED')),
                    SubmittedBy   TEXT DEFAULT '',
                    SubmittedAt   TIMESTAMP DEFAULT NOW(),
                    ReviewedBy    TEXT DEFAULT '',
                    ReviewedAt    TIMESTAMP DEFAULT NULL
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS assembly_request_parts (
                    PartRowID   SERIAL PRIMARY KEY,
                    RequestID   INTEGER NOT NULL REFERENCES assembly_requests(RequestID) ON DELETE CASCADE,
                    InventoryID INTEGER NOT NULL REFERENCES inventory(InventoryID),
                    QtyUsed     INTEGER NOT NULL DEFAULT 1
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS transfer_requests (
                    RequestID    SERIAL PRIMARY KEY,
                    InventoryID  INTEGER NOT NULL REFERENCES inventory(InventoryID),
                    QtyToMove    INTEGER NOT NULL DEFAULT 1,
                    FromLocation TEXT NOT NULL DEFAULT '',
                    ToLocation   TEXT NOT NULL DEFAULT '',
                    Notes        TEXT DEFAULT '',
                    Status       TEXT NOT NULL DEFAULT 'PENDING'
                                 CHECK(Status IN ('PENDING','APPROVED','REJECTED')),
                    SubmittedBy  TEXT DEFAULT '',
                    SubmittedAt  TIMESTAMP DEFAULT NOW(),
                    ReviewedBy   TEXT DEFAULT '',
                    ReviewedAt   TIMESTAMP DEFAULT NULL
                )
            """)

            # Default admin user if no users exist
            cur.execute("SELECT COUNT(*) as cnt FROM users")
            row = cur.fetchone()
            if (row["cnt"] if row else 0) == 0:
                cur.execute(
                    "INSERT INTO users (Name, PIN, Role) VALUES (%s, %s, %s)",
                    ("Admin", "1234", "Admin")
                )

    # ── Auth ──────────────────────────────────────────────────────
    def verify_pin(self, pin: str) -> dict:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM users WHERE PIN=%s AND Active=1", (pin,)
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> list:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM users ORDER BY Name")
            return [dict(r) for r in cur.fetchall()]

    def add_user(self, name, pin, role="Staff"):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO users (Name, PIN, Role) VALUES (%s,%s,%s)",
                (name, pin, role)
            )

    def delete_user(self, user_id):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM users WHERE UserID=%s", (user_id,))

    # ── Products ──────────────────────────────────────────────────
    def add_product(self, name, category, sku, price, barcode,
                    qty, threshold, location, user=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO products (Name, Category, BaseSKU) VALUES (%s,%s,%s) RETURNING ProductID",
                (name, category, sku)
            )
            product_id = cur.fetchone()["productid"]
            cur.execute(
                """INSERT INTO inventory
                   (ProductID, Price, Barcode, Quantity, LowStockThreshold, Location)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING InventoryID""",
                (product_id, price, barcode or None, qty, threshold, location)
            )
            inv_id = cur.fetchone()["inventoryid"]
            if qty > 0:
                uname = (user or {}).get("Name", "System")
                cur.execute(
                    """INSERT INTO transactions
                       (InventoryID, Type, Quantity, Notes, UserName)
                       VALUES (%s,'IN',%s,%s,%s)""",
                    (inv_id, qty, f"Initial stock | Added by {uname}", uname)
                )
            return product_id

    def add_location(self, product_id, location, qty=0,
                     price=None, barcode=None, threshold=10, user=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            if price is None:
                cur.execute(
                    "SELECT Price FROM inventory WHERE ProductID=%s LIMIT 1",
                    (product_id,)
                )
                row = cur.fetchone()
                price = row["price"] if row else 0
            cur.execute(
                """INSERT INTO inventory
                   (ProductID, Price, Barcode, Quantity, LowStockThreshold, Location)
                   VALUES (%s,%s,%s,%s,%s,%s) RETURNING InventoryID""",
                (product_id, price or 0, None, qty, threshold, location)
            )
            inv_id = cur.fetchone()["inventoryid"]
            if qty > 0:
                uname = (user or {}).get("Name", "System")
                cur.execute(
                    """INSERT INTO transactions
                       (InventoryID, Type, Quantity, Notes, UserName)
                       VALUES (%s,'IN',%s,%s,%s)""",
                    (inv_id, qty, f"Initial stock at {location}", uname)
                )
            return inv_id

    def get_all_products(self) -> list:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.ProductID, p.Name, p.Category, p.BaseSKU,
                       i.InventoryID, i.Price, i.Barcode, i.Quantity,
                       i.LowStockThreshold, i.Location,
                       i.UpdatedAt::text as UpdatedAt
                FROM products p
                LEFT JOIN inventory i ON p.ProductID = i.ProductID
                ORDER BY p.Name, i.Location
            """)
            return [dict(r) for r in cur.fetchall()]

    def get_locations_for_product(self, product_id) -> list:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT InventoryID, Location, Quantity, Price,
                       Barcode, LowStockThreshold, UpdatedAt::text as UpdatedAt
                FROM inventory WHERE ProductID=%s ORDER BY Location
            """, (product_id,))
            return [dict(r) for r in cur.fetchall()]

    def update_product(self, product_id, name, category, sku,
                       price, barcode, threshold, location, inventory_id=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE products SET Name=%s, Category=%s, BaseSKU=%s WHERE ProductID=%s",
                (name, category, sku, product_id)
            )
            if inventory_id:
                cur.execute(
                    """UPDATE inventory SET Price=%s, Barcode=%s,
                       LowStockThreshold=%s, Location=%s, UpdatedAt=NOW()
                       WHERE InventoryID=%s""",
                    (price, barcode or None, threshold, location, inventory_id)
                )
            else:
                cur.execute(
                    """UPDATE inventory SET Price=%s, Barcode=%s,
                       LowStockThreshold=%s, Location=%s, UpdatedAt=NOW()
                       WHERE ProductID=%s
                       AND InventoryID=(SELECT MIN(InventoryID) FROM inventory
                                        WHERE ProductID=%s)""",
                    (price, barcode or None, threshold, location,
                     product_id, product_id)
                )

    def delete_location(self, inventory_id, user=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT i.InventoryID, i.Location, p.Name, p.BaseSKU, i.ProductID
                FROM inventory i JOIN products p ON i.ProductID=p.ProductID
                WHERE i.InventoryID=%s""", (inventory_id,))
            row = cur.fetchone()
            if row:
                label = f"{row['name']} @ {row['location']}"
                cur.execute(
                    """UPDATE transactions
                       SET Notes = Notes || %s, InventoryID = NULL
                       WHERE InventoryID=%s""",
                    (f" [DELETED: {label}]", inventory_id)
                )
                cur.execute("DELETE FROM inventory WHERE InventoryID=%s", (inventory_id,))
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM inventory WHERE ProductID=%s",
                    (row["productid"],)
                )
                if cur.fetchone()["cnt"] == 0:
                    cur.execute("DELETE FROM products WHERE ProductID=%s",
                                (row["productid"],))

    def delete_product(self, product_id, user=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT Name, BaseSKU FROM products WHERE ProductID=%s",
                (product_id,)
            )
            row = cur.fetchone()
            label = f"{row['name']} ({row['basesku']})" if row else f"ID:{product_id}"
            cur.execute("""
                UPDATE transactions SET
                  Notes = CASE WHEN Notes IS NULL OR Notes='' THEN %s
                               ELSE Notes || ' ' || %s END,
                  InventoryID = NULL
                WHERE InventoryID IN (
                    SELECT InventoryID FROM inventory WHERE ProductID=%s)
            """, (f"[DELETED: {label}]", f"[DELETED: {label}]", product_id))
            cur.execute("DELETE FROM inventory WHERE ProductID=%s", (product_id,))
            cur.execute("DELETE FROM products WHERE ProductID=%s", (product_id,))

    # ── Stock ─────────────────────────────────────────────────────
    def update_quantity(self, inventory_id, delta, tx_type, notes="", user=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE inventory SET Quantity=Quantity+%s, UpdatedAt=NOW()
                   WHERE InventoryID=%s""",
                (delta, inventory_id)
            )
            cur.execute(
                """INSERT INTO transactions
                   (InventoryID, Type, Quantity, Notes, UserID, UserName)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (inventory_id, tx_type, delta, notes,
                 (user or {}).get("UserID"),
                 (user or {}).get("Name", "System"))
            )

    # adjust_stock alias used in web_server
    def adjust_stock(self, inventory_id, delta, tx_type, notes="", user=None):
        self.update_quantity(inventory_id, delta, tx_type, notes, user)

    def get_low_stock(self) -> list:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.ProductID, p.Name, p.Category, p.BaseSKU,
                       i.InventoryID, i.Price, i.Barcode, i.Quantity,
                       i.LowStockThreshold, i.Location
                FROM products p JOIN inventory i ON p.ProductID=i.ProductID
                WHERE i.Quantity <= i.LowStockThreshold
                ORDER BY i.Quantity ASC
            """)
            return [dict(r) for r in cur.fetchall()]

    # ── Transactions ──────────────────────────────────────────────
    def get_transactions(self, limit=200) -> list:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT t.TransactionID, t.Type, t.Quantity, t.Notes,
                       t.Date::text as Date, t.UserName,
                       p.Name as ProductName, p.BaseSKU,
                       CASE WHEN i.InventoryID IS NULL THEN TRUE ELSE FALSE END as IsDeleted
                FROM transactions t
                LEFT JOIN inventory i ON t.InventoryID=i.InventoryID
                LEFT JOIN products p ON i.ProductID=p.ProductID
                ORDER BY t.TransactionID DESC LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]

    # ── Dashboard stats ───────────────────────────────────────────
    def get_dashboard_stats(self) -> dict:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM products")
            total_products = cur.fetchone()["cnt"]
            cur.execute("SELECT COALESCE(SUM(Quantity),0) as s FROM inventory")
            total_items = cur.fetchone()["s"]
            cur.execute(
                "SELECT COUNT(*) as cnt FROM inventory WHERE Quantity<=LowStockThreshold"
            )
            low_stock = cur.fetchone()["cnt"]
            cur.execute(
                "SELECT COALESCE(SUM(Price*Quantity),0) as s FROM inventory"
            )
            total_value = cur.fetchone()["s"]
            return {
                "total_products": total_products,
                "total_items":    total_items,
                "low_stock":      low_stock,
                "total_value":    total_value,
            }

    # ── Assembly Requests ─────────────────────────────────────────
    def submit_assembly_request(self, product_id, inventory_id, qty,
                                 parts, notes="", user=None):
        uname = (user or {}).get("Name", "Staff")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO assembly_requests
                   (ProductID, InventoryID, QtyToAssemble, Notes, SubmittedBy)
                   VALUES (%s,%s,%s,%s,%s) RETURNING RequestID""",
                (product_id, inventory_id, qty, notes, uname)
            )
            req_id = cur.fetchone()["requestid"]
            for (part_inv_id, qty_used) in parts:
                cur.execute(
                    """INSERT INTO assembly_request_parts
                       (RequestID, InventoryID, QtyUsed) VALUES (%s,%s,%s)""",
                    (req_id, part_inv_id, qty_used)
                )
            return req_id

    def get_assembly_requests(self, status=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            q = """SELECT ar.RequestID, ar.QtyToAssemble, ar.Notes, ar.Status,
                          ar.SubmittedBy, ar.SubmittedAt::text as SubmittedAt,
                          ar.ReviewedBy, ar.ReviewedAt::text as ReviewedAt,
                          p.Name as ProductName, p.BaseSKU,
                          i.Location, i.Quantity as CurrentQty
                   FROM assembly_requests ar
                   JOIN products p ON ar.ProductID=p.ProductID
                   JOIN inventory i ON ar.InventoryID=i.InventoryID"""
            args = []
            if status:
                q += " WHERE ar.Status=%s"
                args.append(status)
            q += " ORDER BY ar.RequestID DESC"
            cur.execute(q, args)
            results = []
            for r in cur.fetchall():
                d = dict(r)
                cur2 = conn.cursor()
                cur2.execute("""
                    SELECT arp.QtyUsed, p2.Name as PartName, p2.BaseSKU as PartSKU,
                           i2.Quantity as PartQty, i2.Location as PartLocation,
                           arp.InventoryID
                    FROM assembly_request_parts arp
                    JOIN inventory i2 ON arp.InventoryID=i2.InventoryID
                    JOIN products p2 ON i2.ProductID=p2.ProductID
                    WHERE arp.RequestID=%s
                """, (r["requestid"],))
                d["parts"] = [dict(p) for p in cur2.fetchall()]
                results.append(d)
            return results

    def approve_assembly_request(self, request_id, user=None):
        uname = (user or {}).get("Name", "Admin")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM assembly_requests WHERE RequestID=%s",
                (request_id,)
            )
            req = cur.fetchone()
            if not req or req["status"] != "PENDING":
                raise ValueError("Request not found or already reviewed")
            cur.execute(
                "SELECT * FROM assembly_request_parts WHERE RequestID=%s",
                (request_id,)
            )
            for part in cur.fetchall():
                cur.execute(
                    "UPDATE inventory SET Quantity=Quantity-%s, UpdatedAt=NOW() WHERE InventoryID=%s",
                    (part["qtyused"], part["inventoryid"])
                )
                cur.execute(
                    """INSERT INTO transactions (InventoryID,Type,Quantity,Notes,UserName)
                       VALUES (%s,'OUT',%s,%s,%s)""",
                    (part["inventoryid"], -part["qtyused"],
                     f"Assembly #{request_id} - parts used", uname)
                )
            cur.execute(
                "UPDATE inventory SET Quantity=Quantity+%s, UpdatedAt=NOW() WHERE InventoryID=%s",
                (req["qtytoassemble"], req["inventoryid"])
            )
            cur.execute(
                """INSERT INTO transactions (InventoryID,Type,Quantity,Notes,UserName)
                   VALUES (%s,'IN',%s,%s,%s)""",
                (req["inventoryid"], req["qtytoassemble"],
                 f"Assembly #{request_id} - finished goods", uname)
            )
            cur.execute(
                """UPDATE assembly_requests SET Status='APPROVED',
                   ReviewedBy=%s, ReviewedAt=NOW() WHERE RequestID=%s""",
                (uname, request_id)
            )

    def reject_assembly_request(self, request_id, user=None):
        uname = (user or {}).get("Name", "Admin")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE assembly_requests SET Status='REJECTED',
                   ReviewedBy=%s, ReviewedAt=NOW() WHERE RequestID=%s""",
                (uname, request_id)
            )

    # ── Transfer Requests ─────────────────────────────────────────
    def submit_transfer_request(self, inventory_id, qty, from_loc,
                                 to_loc, notes="", user=None):
        uname = (user or {}).get("Name", "Staff")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO transfer_requests
                   (InventoryID, QtyToMove, FromLocation, ToLocation, Notes, SubmittedBy)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (inventory_id, qty, from_loc, to_loc, notes, uname)
            )

    def get_transfer_requests(self, status=None):
        with self.get_connection() as conn:
            cur = conn.cursor()
            q = """SELECT tr.RequestID, tr.QtyToMove, tr.FromLocation, tr.ToLocation,
                          tr.Notes, tr.Status, tr.SubmittedBy,
                          tr.SubmittedAt::text as SubmittedAt,
                          tr.ReviewedBy, tr.ReviewedAt::text as ReviewedAt,
                          p.Name as ProductName, p.BaseSKU,
                          i.Quantity as CurrentQty
                   FROM transfer_requests tr
                   JOIN inventory i ON tr.InventoryID=i.InventoryID
                   JOIN products p ON i.ProductID=p.ProductID"""
            args = []
            if status:
                q += " WHERE tr.Status=%s"
                args.append(status)
            q += " ORDER BY tr.RequestID DESC"
            cur.execute(q, args)
            return [dict(r) for r in cur.fetchall()]

    def approve_transfer_request(self, request_id, user=None):
        uname = (user or {}).get("Name", "Admin")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM transfer_requests WHERE RequestID=%s", (request_id,)
            )
            req = cur.fetchone()
            if not req or req["status"] != "PENDING":
                raise ValueError("Request not found or already reviewed")
            cur.execute(
                "SELECT * FROM inventory WHERE InventoryID=%s", (req["inventoryid"],)
            )
            src = cur.fetchone()
            if not src:
                raise ValueError("Source inventory not found")
            if src["quantity"] < req["qtytomove"]:
                raise ValueError(f"Not enough stock. Available: {src['quantity']}")
            cur.execute(
                "UPDATE inventory SET Quantity=Quantity-%s, UpdatedAt=NOW() WHERE InventoryID=%s",
                (req["qtytomove"], req["inventoryid"])
            )
            cur.execute(
                """INSERT INTO transactions (InventoryID,Type,Quantity,Notes,UserName)
                   VALUES (%s,'OUT',%s,%s,%s)""",
                (req["inventoryid"], -req["qtytomove"],
                 f"Transfer #{request_id} → {req['tolocation']}", uname)
            )
            # Find or create destination
            cur.execute(
                "SELECT InventoryID FROM inventory WHERE ProductID=%s AND Location=%s",
                (src["productid"], req["tolocation"])
            )
            dest = cur.fetchone()
            if dest:
                cur.execute(
                    "UPDATE inventory SET Quantity=Quantity+%s, UpdatedAt=NOW() WHERE InventoryID=%s",
                    (req["qtytomove"], dest["inventoryid"])
                )
                dest_inv_id = dest["inventoryid"]
            else:
                cur.execute(
                    """INSERT INTO inventory
                       (ProductID,Price,Quantity,LowStockThreshold,Location)
                       VALUES (%s,%s,%s,%s,%s) RETURNING InventoryID""",
                    (src["productid"], src["price"], req["qtytomove"],
                     src["lowstockthreshold"], req["tolocation"])
                )
                dest_inv_id = cur.fetchone()["inventoryid"]
            cur.execute(
                """INSERT INTO transactions (InventoryID,Type,Quantity,Notes,UserName)
                   VALUES (%s,'IN',%s,%s,%s)""",
                (dest_inv_id, req["qtytomove"],
                 f"Transfer #{request_id} ← {req['fromlocation']}", uname)
            )
            cur.execute(
                """UPDATE transfer_requests SET Status='APPROVED',
                   ReviewedBy=%s, ReviewedAt=NOW() WHERE RequestID=%s""",
                (uname, request_id)
            )

    def reject_transfer_request(self, request_id, user=None):
        uname = (user or {}).get("Name", "Admin")
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE transfer_requests SET Status='REJECTED',
                   ReviewedBy=%s, ReviewedAt=NOW() WHERE RequestID=%s""",
                (uname, request_id)
            )
