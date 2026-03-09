"""
web_server.py — Warehouse IMS Full Web Dashboard
All pages match the desktop app: Dashboard, Products, Inventory,
Low Stock, Transactions, Location Mapping.
Scanner / Barcode / QR removed.
"""

import sys, os, json, hashlib, threading, re, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# ── Smart import — works locally AND on cloud ─────────────────
if os.environ.get("DATABASE_URL"):
    # Cloud mode — PostgreSQL
    import db_manager_pg as _db_mod
    DatabaseManager = _db_mod.DatabaseManager
    class config_manager:
        @staticmethod
        def get_db_path(): return None
else:
    # Local mode — SQLite
    try:
        from database.db_manager import DatabaseManager
        from database import config_manager
    except ImportError:
        from db_manager import DatabaseManager
        import config_manager

PORT = int(os.environ.get("PORT", 8080))

# ── Sessions ────────────────────────────────────────────────────
_sessions = {}
_sessions_lock = threading.Lock()

# Scan transfer sessions — stores scanned boxes per session
_scan_sessions = {}  # {session_id: {"items": [...], "from": "WA", "to": "CAP"}}
_scan_lock = threading.Lock()

def _get_scan_session(sid):
    with _scan_lock:
        return _scan_sessions.get(sid, {"items": [], "from": "WA", "to": "CAP"})

def _set_scan_session(sid, data):
    with _scan_lock:
        _scan_sessions[sid] = data

def _clear_scan_session(sid):
    with _scan_lock:
        _scan_sessions.pop(sid, None)

def _make_token(uid):
    raw = f"{uid}-{time.time()}-{__import__('random').random()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
def _get_session(t):
    with _sessions_lock: return _sessions.get(t)
def _set_session(t, u):
    with _sessions_lock: _sessions[t] = u
def _del_session(t):
    with _sessions_lock: _sessions.pop(t, None)

# ── Config (must match products.py) ────────────────────────────
AREAS = [
    ("Warehouse A", "WA",  "#81A6C6"),
    ("Warehouse B", "WB",  "#4A8C6F"),
    ("Capitol",     "CAP", "#B8956A"),
]
AREA_MAP    = {code: (name, color) for name, code, color in AREAS}
CATEGORIES  = ["Daimaru", "Daimaru Assembly", "Cybertec", "Ledtec", "High Safety"]

def loc_badge(loc):
    if not loc or loc == "Undecided":
        return '<span style="color:#888;font-size:12px;font-style:italic;">Undecided</span>'
    info = AREA_MAP.get(loc)
    if info:
        name, color = info
        return (f'<span style="background:{color};color:white;padding:2px 10px;'
                f'border-radius:12px;font-size:12px;font-weight:700;">{name}</span>')
    return f'<span style="color:#888;">{loc}</span>'

def qty_badge(qty, threshold):
    qty = qty or 0
    thr = threshold or 10
    if qty == 0:
        return f'<span class="badge badge-out">{qty}</span>'
    elif qty <= thr:
        return f'<span class="badge badge-low">{qty}</span>'
    return f'<span class="badge badge-ok">{qty}</span>'

def esc(s):
    return str(s or "").replace("&","&amp;").replace("<","&lt;").replace('"','&quot;')

# ── CSS ─────────────────────────────────────────────────────────
CSS = """
:root{
  --bg:#F3E3D0;--card:#FFFFFF;--border:#D2C4B4;
  --navy:#81A6C6;--navy-mid:#6A90B0;
  --accent:#5A8AAF;--gold:#AACDDC;--gold-light:#C8E2EC;--cream:#F3E3D0;
  --blue:#5A8AAF;--red:#B05555;--orange:#B8956A;--purple:#8B7BAF;
  --text:#2C3E50;--muted:#7A8C99;
  --success-bg:#EAF4EE;--warn-bg:#F9F0E6;--danger-bg:#F5E8E8;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  background:var(--bg);color:var(--text);font-size:15px;line-height:1.5;}
a{color:var(--blue);text-decoration:none;}

.topbar{background:var(--navy);border-bottom:2px solid var(--gold);
  padding:12px 20px;display:flex;align-items:center;
  justify-content:space-between;position:sticky;top:0;z-index:100;}
.topbar-brand{font-size:18px;font-weight:800;color:#FFFFFF;}
.topbar-brand span{color:var(--gold);}
.topbar-user{font-size:13px;color:#CBD5E0;display:flex;align-items:center;gap:12px;}

.nav{display:flex;overflow-x:auto;background:var(--navy-mid);
  border-bottom:2px solid var(--gold);padding:0 12px;gap:2px;}
.nav a{padding:11px 15px;font-size:13px;font-weight:600;white-space:nowrap;
  color:#CBD5E0;border-bottom:3px solid transparent;display:block;}
.nav a.active,.nav a:hover{color:#FFFFFF;border-bottom-color:var(--gold);}

.page{padding:20px;max-width:1140px;margin:0 auto;}
.page-header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;flex-wrap:wrap;gap:10px;}
.page-title{font-size:22px;font-weight:800;color:var(--navy);}
.page-sub{color:var(--muted);font-size:13px;margin-top:2px;}

.kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px;}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:18px;box-shadow:0 2px 8px rgba(15,29,62,.08);}
.kpi-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;}
.kpi-value{font-size:30px;font-weight:800;margin-top:4px;}
.kpi-value.green{color:var(--accent);}
.kpi-value.blue{color:var(--blue);}
.kpi-value.orange{color:var(--orange);}
.kpi-value.red{color:var(--red);}

.card{background:var(--card);border:1px solid var(--border);border-radius:16px;overflow:hidden;margin-bottom:16px;box-shadow:0 2px 12px rgba(15,29,62,.07);}
.card-header{padding:14px 18px;border-bottom:2px solid var(--gold);
  font-weight:700;font-size:15px;display:flex;align-items:center;justify-content:space-between;
  color:var(--navy);background:var(--cream);}
.card-body{padding:20px;}

table{width:100%;border-collapse:collapse;}
th{background:var(--navy);padding:10px 14px;font-size:11px;color:#FFFFFF;
  text-align:left;font-weight:700;text-transform:uppercase;letter-spacing:.4px;}
td{padding:11px 14px;border-top:1px solid var(--border);font-size:14px;vertical-align:middle;}
tr:hover td{background:#EAF0FF;}
.empty-state{text-align:center;padding:50px 20px;color:var(--muted);font-size:14px;}

.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;}
.badge-ok{background:#E8F5EE;color:#2E7D52;border:1px solid #A8D5B5;}
.badge-low{background:var(--warn-bg);color:#9A6C00;border:1px solid var(--gold);}
.badge-out{background:var(--danger-bg);color:var(--red);border:1px solid #F0A899;}
.badge-in{background:#E8F5EE;color:#2E7D52;border:1px solid #A8D5B5;}
.badge-out-tx{background:var(--danger-bg);color:var(--red);border:1px solid #F0A899;}
.badge-adj{background:#EAF0FF;color:var(--accent);border:1px solid #B0C4FF;}
.badge-imp{background:#EAF0FF;color:var(--accent);border:1px solid #B0C4FF;}

.btn{display:inline-block;padding:9px 20px;border-radius:20px;font-size:13px;
  font-weight:700;cursor:pointer;border:none;color:white;transition:all .2s;}
.btn:hover{opacity:.88;transform:translateY(-1px);}
.btn-blue{background:var(--accent);}
.btn-red{background:var(--red);}
.btn-green{background:var(--success);color:#fff;}
.btn-orange{background:var(--gold);color:#0F1D3E;}
.btn-purple{background:#5B3FA6;}
.btn-muted{background:#95A5A6;}
.btn-sm{padding:5px 14px;font-size:12px;border-radius:14px;}

.form-group{margin-bottom:16px;}
.form-label{font-size:13px;color:var(--muted);margin-bottom:6px;display:block;font-weight:600;}
.form-input,.form-select{width:100%;padding:10px 12px;background:#FFFFFF;
  border:2px solid var(--border);border-radius:10px;color:var(--text);font-size:15px;}
.form-input:focus,.form-select:focus{outline:none;border-color:var(--gold);}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.form-row-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;}

.alert{padding:12px 16px;border-radius:8px;margin-bottom:16px;font-size:14px;}
.alert-success{background:var(--success-bg);color:var(--accent);border:1px solid var(--accent);}
.alert-error{background:var(--danger-bg);color:var(--red);border:1px solid var(--red);}

.search-wrap{display:flex;gap:10px;margin-bottom:16px;}
.search-input{flex:1;padding:10px 14px;background:var(--card);
  border:2px solid var(--border);border-radius:20px;color:var(--text);font-size:15px;}
.search-input:focus{outline:none;border-color:var(--gold);}

.area-btns{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0;}
.area-btn{padding:12px 24px;border-radius:20px;font-size:14px;font-weight:700;
  border:3px solid transparent;cursor:pointer;color:white;transition:all .2s;}
.area-btn:hover{opacity:.9;transform:translateY(-1px);}
.area-btn.selected{border-color:white;box-shadow:0 4px 12px rgba(0,0,0,.2);transform:translateY(-2px);}

.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:linear-gradient(160deg,#81A6C6 0%,#AACDDC 50%,#D2C4B4 100%);}
.login-card{background:var(--card);border:2px solid var(--gold);
  border-radius:24px;padding:36px;width:100%;max-width:360px;box-shadow:0 8px 32px rgba(15,29,62,.15);}
.login-title{font-size:24px;font-weight:800;margin-bottom:4px;}
.login-sub{color:var(--muted);font-size:14px;margin-bottom:28px;}
.pin-dots{display:flex;gap:12px;justify-content:center;margin:20px 0;}
.pin-dot{width:18px;height:18px;border-radius:50%;background:#D5D8DC;transition:background .15s;}
.pin-dot.filled{background:var(--gold);}
.numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:16px;}
.numpad-btn{padding:18px;background:var(--cream);border:1px solid var(--border);
  border-radius:12px;font-size:20px;font-weight:700;color:var(--navy);
  cursor:pointer;text-align:center;user-select:none;}
.numpad-btn:active{background:var(--gold-light);}
.numpad-btn.del{background:#FDEDEC;color:var(--red);border-color:#F0A899;}
.numpad-btn.ok{background:var(--navy);color:#FFFFFF;}

@media(max-width:700px){
  .kpi-grid{grid-template-columns:1fr 1fr;}
  .form-row,.form-row-3{grid-template-columns:1fr;}
  td,th{padding:8px;font-size:13px;}
  .kpi-value{font-size:24px;}
  .page{padding:12px;}
  .area-btn{padding:10px 16px;font-size:13px;}
}
"""

# ── Login page ──────────────────────────────────────────────────
def login_page(err=""):
    err_html = f'<div class="alert alert-error">&#10060; {esc(err)}</div>' if err else ""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Warehouse IMS</title><style>{CSS}</style></head><body>
<div class="login-wrap"><div class="login-card">
  <div class="login-title" style="color:var(--navy);">Bida Sales Inventory</div>
  <div class="login-sub" style="color:var(--muted);">Enter your PIN to continue</div>
  {err_html}
  <div class="pin-dots">
    {''.join(f'<div class="pin-dot" id="d{i}"></div>' for i in range(6))}
  </div>
  <div id="pin-err" style="text-align:center;color:var(--red);font-size:13px;min-height:18px;margin-bottom:4px;"></div>
  <div class="numpad">
    {''.join(f'<div class="numpad-btn" onclick="addPin({n})">{n}</div>' for n in [1,2,3,4,5,6,7,8,9])}
    <div class="numpad-btn del" onclick="delPin()">&#9003;</div>
    <div class="numpad-btn" onclick="addPin(0)">0</div>
    <div class="numpad-btn ok" onclick="submitPin()">&#10003;</div>
  </div>
</div></div>
<script>
let pin="";
function addPin(d){{if(pin.length>=8)return;pin+=d;upd();}}
function delPin(){{pin=pin.slice(0,-1);upd();}}
function upd(){{for(let i=0;i<6;i++)document.getElementById('d'+i).className='pin-dot'+(i<pin.length?' filled':'');}}
function submitPin(){{
  if(!pin)return;
  fetch('/api/login',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{pin}})}})
  .then(r=>r.json()).then(d=>{{
    if(d.ok)location.href='/dashboard';
    else{{document.getElementById('pin-err').textContent='Wrong PIN — try again';pin='';upd();}}
  }}).catch(()=>{{document.getElementById('pin-err').textContent='Connection error';pin='';upd();}});
}}
document.addEventListener('keydown',e=>{{
  if(e.key>='0'&&e.key<='9')addPin(parseInt(e.key));
  else if(e.key==='Backspace')delPin();
  else if(e.key==='Enter')submitPin();
}});
</script></body></html>"""

# ── Page shell ──────────────────────────────────────────────────
def page(title, body, user=None, active="dashboard", msg="", msg_type="success"):
    alert = f'<div class="alert alert-{msg_type}">{msg}</div>' if msg else ""
    is_admin = user and user.get("Role") == "Admin"

    nav = [
        ("dashboard",        "&#127968; Dashboard"),
        ("products",         "&#128203; Products"),
        ("inventory",        "&#128230; Inventory"),
        ("low_stock",        "&#9888;&#65039; Low Stock"),
        ("transactions",     "&#128202; Transactions"),
        ("location_mapping", "&#128205; Location Mapping"),
        ("quick_out",        "&#128244; Quick OUT"),
        ("link_barcode",     "&#128279; Link Barcode"),
        ("assembly",         "&#128295; Assembly"),
        ("transfer",         "&#128666; Transfer"),
        ("scan_transfer",    "&#128249; Scan Transfer"),
    ]
    if is_admin:
        nav.append(("add_product", "&#10133; Add Product"))
        nav.append(("adjust",      "&#9878;&#65039; Adjust Stock"))

    nav_html = "".join(
        f'<a href="/{k}" class="{"active" if k==active else ""}">{lbl}</a>'
        for k, lbl in nav
    )
    role_badge = (
        f'<span style="background:{"#8957E5" if is_admin else "#21262D"};color:white;'
        f'padding:2px 8px;border-radius:10px;font-size:11px;font-weight:700;">'
        f'{"Admin" if is_admin else "Staff"}</span>'
    )
    user_html = ""
    if user:
        user_html = (
            f'{role_badge} '
            f'<span style="color:var(--muted);">{esc(user.get("Name",""))}</span> '
            f'<a href="/logout" style="color:var(--red);">Logout</a>'
        )

    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} — Warehouse IMS</title><style>{CSS}</style></head><body>
<div class="topbar">
  <div class="topbar-brand">Bida Sales <span>Inventory</span></div>
  <div class="topbar-user">{user_html}</div>
</div>
<nav class="nav">{nav_html}</nav>
<div class="page">{alert}{body}</div>
</body></html>"""

# ── Dashboard ────────────────────────────────────────────────────
def build_dashboard(db, user):
    is_admin  = (user or {}).get("Role") == "Admin"
    prods     = db.get_all_products()
    total     = len(prods)
    out_ct    = sum(1 for p in prods if (p.get("Quantity") or 0) == 0)
    low_ct    = sum(1 for p in prods if 0 < (p.get("Quantity") or 0) <= (p.get("LowStockThreshold") or 10))
    total_qty = sum(p.get("Quantity") or 0 for p in prods)
    undecided = sum(1 for p in prods if not p.get("Location") or p.get("Location") == "Undecided")

    # Pending approvals
    try:
        pending_asm  = db.get_assembly_requests(status="PENDING")
        pending_xfer = db.get_transfer_requests(status="PENDING")
    except Exception:
        pending_asm, pending_xfer = [], []

    txs = db.get_transactions(limit=8)
    tx_rows = ""
    for t in txs:
        tp  = t.get("Type","")
        bc  = {"IN":"badge-in","OUT":"badge-out-tx","ADJUST":"badge-adj","IMPORT":"badge-imp"}.get(tp,"badge-adj")
        nm  = esc(t.get("ProductName",""))
        qty = t.get("Quantity",0)
        qs  = f"+{qty}" if tp in ("IN","IMPORT") else str(qty)
        tx_rows += (f"<tr><td>{nm}</td>"
                    f'<td><span class="badge {bc}">{tp}</span></td>'
                    f"<td style=\"font-weight:700;\">{qs}</td>"
                    f'<td style="color:var(--muted);font-size:12px;">{(t.get("Date") or "")[:16]}</td>'
                    f'<td style="color:var(--muted);font-size:12px;">{esc(t.get("UserName",""))}</td></tr>')
    if not tx_rows:
        tx_rows = '<tr><td colspan="5" class="empty-state">No transactions yet</td></tr>'

    area_counts = {}
    for p in prods:
        loc = p.get("Location") or "Undecided"
        area_counts[loc] = area_counts.get(loc, 0) + 1

    area_cards = ""
    for aname, acode, acolor in AREAS:
        cnt = area_counts.get(acode, 0)
        area_cards += (f'<div class="kpi" style="border-top:3px solid {acolor};">'                       f'<div class="kpi-label">{aname}</div>'                       f'<div class="kpi-value" style="color:{acolor};font-size:26px;">{cnt}'                       f'<span style="font-size:13px;color:var(--muted);font-weight:400;"> items</span></div></div>')
    if undecided:
        area_cards += ('<div class="kpi" style="border-top:3px solid #888;">'                       '<div class="kpi-label">Undecided</div>'                       f'<div class="kpi-value" style="color:#888;font-size:26px;">{undecided}'                       '<span style="font-size:13px;color:var(--muted);font-weight:400;"> items</span></div></div>')

    # Approval banners
    banners = ""
    if pending_asm:
        lbl = "your " if is_admin else "Admin "
        act = "✅ Review" if is_admin else "View"
        banners += (f'<div style="background:#FFF9E6;border:1px solid #F0C040;border-radius:8px;'                    f'padding:12px 18px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;">'                    f'<span>&#9201; <strong>{len(pending_asm)} Assembly request(s)</strong> waiting for {lbl}approval</span>'                    f'<a href="/assembly" class="btn btn-blue btn-sm">{act}</a></div>')
    if pending_xfer:
        lbl = "your " if is_admin else "Admin "
        act = "✅ Review" if is_admin else "View"
        banners += (f'<div style="background:#EEF6FD;border:1px solid #90C8F0;border-radius:8px;'                    f'padding:12px 18px;margin-bottom:10px;display:flex;align-items:center;justify-content:space-between;">'                    f'<span>&#128666; <strong>{len(pending_xfer)} Transfer request(s)</strong> waiting for {lbl}approval</span>'                    f'<a href="/transfer" class="btn btn-blue btn-sm">{act}</a></div>')

    # Quick action cards
    def qa(href, icon, label, sub, bg, border, fg):
        return (f'<a href="{href}" style="text-decoration:none;">'                f'<div style="background:{bg};border:1px solid {border};border-radius:10px;'                f'padding:18px;text-align:center;">'                f'<div style="font-size:28px;">{icon}</div>'                f'<div style="font-weight:700;color:{fg};margin-top:6px;">{label}</div>'                f'<div style="font-size:11px;color:var(--muted);">{sub}</div>'                f'</div></a>')

    quick = (
        qa("/quick_out","&#128244;","Quick OUT","Scan &amp; deduct","#FEF2F2","#FCA5A5","#991B1B") +
        qa("/scan_transfer","&#128249;","Scan Transfer","Scan boxes to transfer","#F0F4FF","#93C5FD","#1E3A8F") +
        qa("/assembly","&#128295;","Assembly","Log assembled items","#F0FDF4","#86EFAC","#166534") +
        qa("/transfer","&#128666;","Transfer","Manual transfer request","#EFF6FF","#93C5FD","#1E40AF") +
        qa("/inventory","&#128230;","Inventory","View all stock","#FEFCE8","#FDE047","#854D0E") +
        qa("/low_stock","&#9888;&#65039;","Low Stock","Items needing restock","#FFF7ED","#FDBA74","#9A3412") +
        qa("/transactions","&#128202;","History","Full audit log","#FAF5FF","#D8B4FE","#6B21A8")
    )

    return f"""
<div class="page-header">
  <div><div class="page-title">Dashboard</div>
  <div class="page-sub">Welcome back, {esc(user.get("Name",""))} &#128075;</div></div>
</div>
{banners}
<div class="kpi-grid">
  <div class="kpi"><div class="kpi-label">Total Products</div><div class="kpi-value blue">{total}</div></div>
  <div class="kpi"><div class="kpi-label">Total Units</div><div class="kpi-value green">{total_qty}</div></div>
  <div class="kpi"><div class="kpi-label">Low Stock</div><div class="kpi-value orange">{low_ct}</div></div>
  <div class="kpi"><div class="kpi-label">Out of Stock</div><div class="kpi-value red">{out_ct}</div></div>
</div>
<div style="font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;
            letter-spacing:.5px;margin-bottom:10px;">Storage Areas</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
            gap:12px;margin-bottom:24px;">{area_cards}</div>
<div class="card" style="margin-bottom:20px;">
  <div class="card-header">&#9889; Quick Actions</div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
              gap:12px;padding:16px;">{quick}</div>
</div>
<div class="card">
  <div class="card-header">Recent Transactions
    <a href="/transactions" style="font-size:12px;color:var(--blue);">View all &#8594;</a>
  </div>
  <table><thead><tr><th>Product</th><th>Type</th><th>Qty</th><th>Date</th><th>By</th></tr></thead>
  <tbody>{tx_rows}</tbody></table>
</div>"""


def build_products(db, user, search="", msg="", msg_type="success"):
    is_admin = user and user.get("Role") == "Admin"
    prods    = db.get_all_products()
    if search:
        s = search.lower()
        prods = [p for p in prods if
                 s in (p.get("Name","") or "").lower() or
                 s in (p.get("BaseSKU","") or "").lower() or
                 s in (p.get("Category","") or "").lower()]

    rows = ""
    for p in prods:
        qty    = p.get("Quantity") or 0
        thr    = p.get("LowStockThreshold") or 10
        price  = p.get("Price") or 0
        inv_id = p.get("InventoryID","")
        pid    = p.get("ProductID","")
        actions = ""
        if is_admin:
            actions = f"""
              <a href="/edit_product?id={inv_id}" class="btn btn-blue btn-sm">&#9998; Edit</a>&nbsp;
              <a href="/adjust?inv={inv_id}" class="btn btn-sm" style="background:#C77B00;">&#9878; Adjust</a>&nbsp;
              <a href="/add_location?pid={pid}" class="btn btn-sm" style="background:#8957E5;">&#128205; +Loc</a>&nbsp;
              <a href="/delete_location?inv={inv_id}" class="btn btn-red btn-sm"
                 onclick="return confirm('Delete this location row?')">&#128465; Del</a>"""
        rows += f"""<tr>
          <td><strong>{esc(p.get("Name",""))}</strong></td>
          <td style="color:var(--muted);font-size:13px;">{esc(p.get("Category",""))}</td>
          <td style="color:var(--muted);font-size:12px;font-family:monospace;">{esc(p.get("BaseSKU",""))}</td>
          <td>&#8369;{price:,.2f}</td>
          <td>{qty_badge(qty, thr)}</td>
          <td>{loc_badge(p.get("Location",""))}</td>
          <td style="font-size:12px;color:var(--muted);">{(p.get("UpdatedAt") or "")[:10]}</td>
          <td style="white-space:nowrap;">{actions}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="8" class="empty-state">No products found</td></tr>'

    add_btn = '<a href="/add_product" class="btn btn-green">&#10133; Add Product</a>' if is_admin else ""
    clr     = f'&nbsp;<a href="/products" class="btn btn-muted">Clear</a>' if search else ""

    return f"""
<div class="page-header">
  <div><div class="page-title">Products</div>
  <div class="page-sub">{len(prods)} result(s)</div></div>
  {add_btn}
</div>
<form method="get" action="/products" class="search-wrap">
  <input class="search-input" name="q" value="{esc(search)}"
    placeholder="&#128269;  Search name, SKU, category..." autocomplete="off">
  <button class="btn btn-muted" type="submit">Search</button>{clr}
</form>
<div class="card">
<table><thead><tr>
  <th>Name</th><th>Category</th><th>SKU</th><th>Price</th>
  <th>Qty</th><th>Location</th><th>Updated</th><th>Actions</th>
</tr></thead><tbody>{rows}</tbody></table></div>"""

# ── Inventory ────────────────────────────────────────────────────
def build_inventory(db, user):
    prods   = db.get_all_products()
    grouped = {}
    for p in prods:
        pid = p["ProductID"]
        if pid not in grouped:
            grouped[pid] = {"name":p["Name"],"sku":p["BaseSKU"],"cat":p["Category"],"locs":[]}
        grouped[pid]["locs"].append(p)

    rows = ""
    for pid, g in grouped.items():
        total  = sum(l.get("Quantity") or 0 for l in g["locs"])
        thr    = g["locs"][0].get("LowStockThreshold") or 10
        badges = " ".join(loc_badge(l.get("Location","")) for l in g["locs"])
        per_loc = " &nbsp;|&nbsp; ".join(
            f'<span style="font-size:12px;">{loc_badge(l.get("Location",""))}&nbsp;'
            f'<strong>{l.get("Quantity",0)}</strong></span>'
            for l in g["locs"]
        )
        rows += f"""<tr>
          <td><strong>{esc(g["name"])}</strong></td>
          <td style="font-family:monospace;font-size:12px;color:var(--muted);">{esc(g["sku"])}</td>
          <td style="color:var(--muted);font-size:13px;">{esc(g["cat"])}</td>
          <td>{qty_badge(total, thr)}</td>
          <td>{per_loc}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="5" class="empty-state">No products yet</td></tr>'

    return f"""
<div class="page-header">
  <div><div class="page-title">Inventory</div>
  <div class="page-sub">Total stock per product across all locations</div></div>
</div>
<div class="card">
<table><thead><tr>
  <th>Product</th><th>SKU</th><th>Category</th><th>Total Qty</th><th>Qty by Location</th>
</tr></thead><tbody>{rows}</tbody></table></div>"""

# ── Low Stock ─────────────────────────────────────────────────────
def build_low_stock(db, user):
    prods = db.get_all_products()
    low   = sorted(
        [p for p in prods if (p.get("Quantity") or 0) <= (p.get("LowStockThreshold") or 10)],
        key=lambda p: p.get("Quantity") or 0
    )
    out_ct = sum(1 for p in low if (p.get("Quantity") or 0) == 0)
    low_ct = len(low) - out_ct

    rows = ""
    for p in low:
        qty = p.get("Quantity") or 0
        thr = p.get("LowStockThreshold") or 10
        status = '<span style="color:var(--red);font-weight:700;">&#128308; Out of Stock</span>' if qty == 0 \
            else '<span style="color:var(--orange);font-weight:700;">&#128993; Low Stock</span>'
        inv_id = p.get("InventoryID","")
        adj = f'<a href="/adjust?inv={inv_id}" class="btn btn-sm" style="background:#C77B00;">Adjust</a>' \
            if user and user.get("Role") == "Admin" else ""
        rows += f"""<tr>
          <td><strong>{esc(p.get("Name",""))}</strong></td>
          <td style="font-size:12px;color:var(--muted);font-family:monospace;">{esc(p.get("BaseSKU",""))}</td>
          <td style="color:var(--muted);font-size:13px;">{esc(p.get("Category",""))}</td>
          <td>{qty_badge(qty, thr)}</td>
          <td style="color:var(--muted);">{thr}</td>
          <td>{loc_badge(p.get("Location",""))}</td>
          <td>{status}</td>
          <td>{adj}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="8" class="empty-state">&#9989; All products have sufficient stock!</td></tr>'

    return f"""
<div class="page-header">
  <div><div class="page-title">Low Stock Alerts</div>
  <div class="page-sub">{out_ct} out of stock &nbsp;&middot;&nbsp; {low_ct} running low</div></div>
</div>
<div class="card">
<table><thead><tr>
  <th>Product</th><th>SKU</th><th>Category</th><th>Current</th>
  <th>Min Level</th><th>Location</th><th>Status</th><th></th>
</tr></thead><tbody>{rows}</tbody></table></div>"""

# ── Transactions ──────────────────────────────────────────────────
def build_transactions(db, user):
    txs  = db.get_transactions(limit=500)
    rows = ""
    for t in txs:
        tp  = t.get("Type","")
        bc  = {"IN":"badge-in","OUT":"badge-out-tx","ADJUST":"badge-adj","IMPORT":"badge-imp"}.get(tp,"badge-adj")
        del_= t.get("IsDeleted")
        nm  = esc(t.get("ProductName",""))
        nm_html = f'<span style="color:#888;font-style:italic;">{nm}</span>' if del_ else f"<strong>{nm}</strong>"
        qty = t.get("Quantity",0)
        qs  = f"+{qty}" if tp in ("IN","IMPORT") else str(qty)
        rows += f"""<tr>
          <td style="color:var(--muted);font-size:12px;">{t.get("TransactionID","")}</td>
          <td>{nm_html}</td>
          <td style="font-family:monospace;font-size:12px;color:var(--muted);">{esc(t.get("BaseSKU",""))}</td>
          <td><span class="badge {bc}">{tp}</span></td>
          <td style="font-weight:700;">{qs}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(t.get("Notes","") or "")}</td>
          <td style="font-size:12px;color:var(--muted);">{(t.get("Date") or "")[:16]}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(t.get("UserName",""))}</td>
        </tr>"""
    if not rows:
        rows = '<tr><td colspan="8" class="empty-state">No transactions yet</td></tr>'

    return f"""
<div class="page-header">
  <div><div class="page-title">Transactions</div>
  <div class="page-sub">Full audit log &mdash; {len(txs)} records</div></div>
</div>
<div class="card">
<table><thead><tr>
  <th>#</th><th>Product</th><th>SKU</th><th>Type</th>
  <th>Qty</th><th>Notes</th><th>Date</th><th>By</th>
</tr></thead><tbody>{rows}</tbody></table></div>"""

# ── Location Mapping ──────────────────────────────────────────────
def build_location_mapping(db, user, msg=""):
    prods     = db.get_all_products()
    undecided = [p for p in prods if not p.get("Location") or p.get("Location")=="Undecided"]
    assigned  = [p for p in prods if p.get("Location") and p.get("Location")!="Undecided"]

    def make_rows(items):
        rows = ""
        for p in items:
            inv_id = p.get("InventoryID","")
            pid    = p.get("ProductID","")
            loc    = p.get("Location") or "Undecided"
            opts   = "".join(
                f'<option value="{code}" {"selected" if code==loc else ""}>{name}</option>'
                for name, code, _ in AREAS
            )
            rows += f"""<tr>
              <td><strong>{esc(p.get("Name",""))}</strong></td>
              <td style="font-size:12px;color:var(--muted);font-family:monospace;">{esc(p.get("BaseSKU",""))}</td>
              <td style="color:var(--muted);font-size:13px;">{esc(p.get("Category",""))}</td>
              <td style="font-weight:700;">{p.get("Quantity",0)}</td>
              <td>{loc_badge(loc)}</td>
              <td>
                <form method="post" action="/api/assign_location"
                      style="display:inline-flex;gap:6px;align-items:center;">
                  <input type="hidden" name="inv_id" value="{inv_id}">
                  <input type="hidden" name="pid"    value="{pid}">
                  <select name="location" class="form-select"
                          style="width:auto;padding:5px 8px;font-size:13px;">{opts}</select>
                  <button class="btn btn-green btn-sm" type="submit">&#10003; Assign</button>
                </form>
              </td>
            </tr>"""
        return rows or '<tr><td colspan="6" class="empty-state">None</td></tr>'

    alert = f'<div class="alert alert-success">&#9989; {msg}</div>' if msg else ""

    return f"""
<div class="page-header">
  <div><div class="page-title">Location Mapping</div>
  <div class="page-sub">{len(undecided)} product(s) waiting for location assignment</div></div>
</div>
{alert}
<div class="card">
  <div class="card-header" style="color:var(--orange);">
    &#128993; Undecided &mdash; Need Location &nbsp;<span style="background:var(--warn-bg);color:var(--orange);padding:2px 8px;border-radius:10px;font-size:12px;">{len(undecided)}</span>
  </div>
  <table><thead><tr>
    <th>Product</th><th>SKU</th><th>Category</th><th>Qty</th><th>Location</th><th>Assign</th>
  </tr></thead><tbody>{make_rows(undecided)}</tbody></table>
</div>
<div class="card">
  <div class="card-header" style="color:var(--accent);">
    &#9989; Assigned &nbsp;<span style="background:var(--success-bg);color:var(--accent);padding:2px 8px;border-radius:10px;font-size:12px;">{len(assigned)}</span>
  </div>
  <table><thead><tr>
    <th>Product</th><th>SKU</th><th>Category</th><th>Qty</th><th>Location</th><th>Change</th>
  </tr></thead><tbody>{make_rows(assigned)}</tbody></table>
</div>"""

# ── Add Product ───────────────────────────────────────────────────
def build_add_product(err=""):
    err_html = f'<div class="alert alert-error">&#10060; {esc(err)}</div>' if err else ""
    cat_opts = "".join(f'<option value="{c}">{c}</option>' for c in CATEGORIES)
    area_btns = "".join(
        f'<button type="button" class="area-btn" data-code="{code}" '
        f'style="background:{color};" onclick="selArea(this,\'{code}\')">{name}</button>'
        for name, code, color in AREAS
    )
    return f"""
<div class="page-header">
  <div><div class="page-title">Add Product</div></div>
  <a href="/products" class="btn btn-muted">&#8592; Back</a>
</div>
{err_html}
<div class="card"><div class="card-body">
<form method="post" action="/api/add_product">
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Product Name *</label>
      <input class="form-input" name="name" required placeholder="e.g. 350m Cable Ethernet">
    </div>
    <div class="form-group">
      <label class="form-label">Category *</label>
      <select class="form-select" name="category" required>{cat_opts}</select>
    </div>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">SKU
        <span style="color:var(--muted);font-size:11px;font-weight:400;">(auto-generated if blank)</span>
      </label>
      <input class="form-input" name="sku" placeholder="Leave blank to auto-generate">
    </div>
    <div class="form-group">
      <label class="form-label">Price (&#8369;)</label>
      <input class="form-input" name="price" type="number" step="0.01" min="0" value="0.00">
    </div>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Initial Quantity</label>
      <input class="form-input" name="qty" type="number" value="0" min="0">
    </div>
    <div class="form-group">
      <label class="form-label">Low Stock Alert Level</label>
      <input class="form-input" name="threshold" type="number" value="10" min="0">
    </div>
  </div>
  <div class="form-group">
    <label class="form-label">Barcode <span style="color:var(--muted);font-size:11px;font-weight:400;">(optional)</span></label>
    <input class="form-input" name="barcode" placeholder="Type barcode if available">
  </div>
  <div class="form-group">
    <label class="form-label">Storage Location</label>
    <div class="area-btns">
      {area_btns}
      <button type="button" class="area-btn selected" data-code="Undecided"
        style="background:#555;" onclick="selArea(this,'Undecided')">&#128336; Decide Later</button>
    </div>
    <input type="hidden" name="location" id="locInput" value="Undecided">
  </div>
  <div style="display:flex;gap:10px;margin-top:4px;">
    <button class="btn btn-green" type="submit">&#128190; Save Product</button>
    <a href="/products" class="btn btn-muted">Cancel</a>
  </div>
</form></div></div>
<script>
function selArea(btn,code){{
  document.querySelectorAll('.area-btn').forEach(b=>b.classList.remove('selected'));
  btn.classList.add('selected');
  document.getElementById('locInput').value=code;
}}
</script>"""

# ── Edit Product ──────────────────────────────────────────────────
def build_edit_product(db, inv_id):
    prods = db.get_all_products()
    p     = next((x for x in prods if x.get("InventoryID")==inv_id), None)
    if not p:
        return '<div class="alert alert-error">Product not found.</div>'

    cat_opts = "".join(
        f'<option value="{c}" {"selected" if c==p.get("Category","") else ""}>{c}</option>'
        for c in CATEGORIES
    )
    cur_loc   = p.get("Location") or "Undecided"
    area_btns = "".join(
        f'<button type="button" class="area-btn {"selected" if code==cur_loc else ""}" '
        f'data-code="{code}" style="background:{color};" onclick="selArea(this,\'{code}\')">{name}</button>'
        for name, code, color in AREAS
    )
    und_sel = "selected" if cur_loc == "Undecided" else ""

    return f"""
<div class="page-header">
  <div><div class="page-title">Edit Product</div>
  <div class="page-sub">{esc(p.get("Name",""))}</div></div>
  <a href="/products" class="btn btn-muted">&#8592; Back</a>
</div>
<div class="card"><div class="card-body">
<form method="post" action="/api/edit_product">
  <input type="hidden" name="inventory_id" value="{inv_id}">
  <input type="hidden" name="product_id"   value="{p.get('ProductID','')}">
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Product Name *</label>
      <input class="form-input" name="name" value="{esc(p.get('Name',''))}" required>
    </div>
    <div class="form-group">
      <label class="form-label">Category *</label>
      <select class="form-select" name="category">{cat_opts}</select>
    </div>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">SKU</label>
      <input class="form-input" name="sku" value="{esc(p.get('BaseSKU',''))}">
    </div>
    <div class="form-group">
      <label class="form-label">Price (&#8369;)</label>
      <input class="form-input" name="price" type="number" step="0.01" value="{p.get('Price',0)}">
    </div>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Barcode</label>
      <input class="form-input" name="barcode" value="{esc(p.get('Barcode','') or '')}">
    </div>
    <div class="form-group">
      <label class="form-label">Low Stock Alert Level</label>
      <input class="form-input" name="threshold" type="number" value="{p.get('LowStockThreshold',10)}">
    </div>
  </div>
  <div class="form-group">
    <label class="form-label">Storage Location</label>
    <div class="area-btns">
      {area_btns}
      <button type="button" class="area-btn {und_sel}" data-code="Undecided"
        style="background:#555;" onclick="selArea(this,'Undecided')">&#128336; Decide Later</button>
    </div>
    <input type="hidden" name="location" id="locInput" value="{esc(cur_loc)}">
  </div>
  <div style="display:flex;gap:10px;margin-top:4px;">
    <button class="btn btn-green" type="submit">&#128190; Save Changes</button>
    <a href="/products" class="btn btn-muted">Cancel</a>
  </div>
</form></div></div>
<script>
function selArea(btn,code){{
  document.querySelectorAll('.area-btn').forEach(b=>b.classList.remove('selected'));
  btn.classList.add('selected');
  document.getElementById('locInput').value=code;
}}
</script>"""

# ── Add Location ──────────────────────────────────────────────────
def build_add_location(db, pid, err=""):
    prods = db.get_all_products()
    p     = next((x for x in prods if x.get("ProductID")==pid), None)
    if not p:
        return '<div class="alert alert-error">Product not found.</div>'

    existing = db.get_locations_for_product(pid)
    ex_html  = " ".join(
        f'{loc_badge(l.get("Location",""))} <span style="color:var(--muted);font-size:12px;">{l.get("Quantity",0)} pcs</span>'
        for l in existing
    ) or '<span style="color:var(--muted);">None yet</span>'

    area_btns = "".join(
        f'<button type="button" class="area-btn" data-code="{code}" '
        f'style="background:{color};" onclick="selArea(this,\'{code}\')">{name}</button>'
        for name, code, color in AREAS
    )
    err_html = f'<div class="alert alert-error">&#10060; {esc(err)}</div>' if err else ""

    return f"""
<div class="page-header">
  <div><div class="page-title">Add Location</div>
  <div class="page-sub">{esc(p.get("Name",""))} &mdash; {esc(p.get("BaseSKU",""))}</div></div>
  <a href="/products" class="btn btn-muted">&#8592; Back</a>
</div>
{err_html}
<div class="card">
  <div class="card-header">Current Locations</div>
  <div style="padding:14px 18px;">{ex_html}</div>
</div>
<div class="card"><div class="card-body">
<form method="post" action="/api/add_location">
  <input type="hidden" name="pid" value="{pid}">
  <div class="form-group">
    <label class="form-label">Select New Storage Area</label>
    <div class="area-btns">{area_btns}</div>
    <input type="hidden" name="location" id="locInput" value="">
    <div id="locErr" style="color:var(--red);font-size:12px;margin-top:4px;display:none;">Please select a location.</div>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Initial Qty at this location</label>
      <input class="form-input" name="qty" type="number" value="0" min="0">
    </div>
    <div class="form-group">
      <label class="form-label">Notes (optional)</label>
      <input class="form-input" name="notes" placeholder="e.g. overflow stock">
    </div>
  </div>
  <div style="display:flex;gap:10px;margin-top:4px;">
    <button class="btn btn-purple" type="button" onclick="doSubmit()">&#128205; Add Location</button>
    <a href="/products" class="btn btn-muted">Cancel</a>
  </div>
</form></div></div>
<script>
function selArea(btn,code){{
  document.querySelectorAll('.area-btn').forEach(b=>b.classList.remove('selected'));
  btn.classList.add('selected');
  document.getElementById('locInput').value=code;
  document.getElementById('locErr').style.display='none';
}}
function doSubmit(){{
  if(!document.getElementById('locInput').value){{
    document.getElementById('locErr').style.display='block'; return;
  }}
  btn.closest('form').submit();
}}
</script>"""

# ── Adjust Stock ──────────────────────────────────────────────────
def build_adjust(db, user, preselect_inv=None, msg="", msg_type="success"):
    prods = db.get_all_products()
    opts  = ""
    for p in prods:
        inv_id = p.get("InventoryID","")
        loc    = p.get("Location") or "Undecided"
        sel    = "selected" if str(inv_id)==str(preselect_inv) else ""
        opts  += (f'<option value="{inv_id}" {sel}>'
                  f'{esc(p.get("Name",""))} &mdash; {loc} ({p.get("Quantity",0)} pcs)'
                  f'</option>')
    alert = f'<div class="alert alert-{msg_type}">{msg}</div>' if msg else ""
    return f"""
<div class="page-header">
  <div><div class="page-title">Adjust Stock</div>
  <div class="page-sub">Add, remove, or set absolute quantity</div></div>
</div>
{alert}
<div class="card"><div class="card-body">
<form method="post" action="/api/adjust">
  <div class="form-group">
    <label class="form-label">Select Product &amp; Location</label>
    <select class="form-select" name="inventory_id" required>
      <option value="">— Choose product —</option>{opts}
    </select>
  </div>
  <div class="form-row">
    <div class="form-group">
      <label class="form-label">Operation</label>
      <select class="form-select" name="op">
        <option value="IN">&#10133; Add Stock (IN)</option>
        <option value="OUT">&#10134; Remove Stock (OUT)</option>
        <option value="ADJUST">&#9878; Set Exact Quantity</option>
      </select>
    </div>
    <div class="form-group">
      <label class="form-label">Quantity</label>
      <input class="form-input" name="qty" type="number" min="0" value="1" required>
    </div>
  </div>
  <div class="form-group">
    <label class="form-label">Notes (optional)</label>
    <input class="form-input" name="notes" placeholder="Reason for adjustment...">
  </div>
  <div style="display:flex;gap:10px;">
    <button class="btn btn-orange" type="submit">&#9878; Apply Adjustment</button>
    <a href="/products" class="btn btn-muted">Cancel</a>
  </div>
</form></div></div>"""


# ── Link Barcode Page ────────────────────────────────────────────
def build_link_barcode(db, user, msg="", msg_type="success"):
    """One-time page: scan a label barcode and link it to a product."""
    prods = db.get_all_products()
    opts  = ""
    for p in prods:
        has = "✅ " if p.get("Barcode") else ""
        opts += (f'<option value="{p.get("InventoryID","")}">'
                 f'{has}{esc(p.get("Name",""))} — {esc(p.get("BaseSKU",""))}'
                 f' ({p.get("Location") or "Undecided"})</option>')

    alert = f'<div class="alert alert-{msg_type}" style="margin-bottom:16px;">{msg}</div>' if msg else ""

    return f"""
<div class="page-header">
  <div>
    <div class="page-title">&#128247; Link Barcode to Product</div>
    <div class="page-sub">Scan a product label once to register it — only needed one time per product</div>
  </div>
</div>
{alert}

<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;">

  <!-- STEP 1: Link -->
  <div class="card">
    <div class="card-header" style="background:#EEF6FD;color:#1A3A8F;">
      &#128279; Step 1 — Link a barcode to a product
    </div>
    <div class="card-body">
      <p style="color:var(--muted);font-size:13px;margin-bottom:16px;">
        Select the product, then scan its label barcode (or type it manually).
        After linking, the scanner will recognize it forever.
      </p>
      <form method="post" action="/api/link_barcode">
        <div class="form-group">
          <label class="form-label">Select Product</label>
          <select class="form-select" name="inventory_id" required id="prodSelect">
            <option value="">— Choose product —</option>{opts}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Scan or Type Barcode
            <span style="color:var(--muted);font-size:11px;font-weight:400;">
              (point scanner at label, it fills automatically)
            </span>
          </label>
          <input class="form-input" name="barcode" id="barcodeInput"
                 placeholder="&#128269; Focus here then scan label..."
                 autocomplete="off" autofocus
                 style="font-size:18px;letter-spacing:2px;font-family:monospace;">
        </div>
        <button class="btn btn-blue" type="submit">&#128279; Link Barcode</button>
      </form>
    </div>
  </div>

  <!-- Already linked -->
  <div class="card">
    <div class="card-header" style="background:#E8F5EE;color:#2E7D52;">
      &#9989; Already Linked ({sum(1 for p in prods if p.get('Barcode'))} of {len(prods)})
    </div>
    <div style="max-height:400px;overflow-y:auto;">
    <table><thead><tr>
      <th>Product</th><th>SKU</th><th>Barcode</th><th>Location</th>
    </tr></thead><tbody>
    {"".join(
        f'<tr><td><strong>{esc(p.get("Name",""))}</strong></td>'
        f'<td style="font-family:monospace;font-size:12px;">{esc(p.get("BaseSKU",""))}</td>'
        f'<td style="font-family:monospace;font-size:12px;color:var(--accent);">{esc(p.get("Barcode",""))}</td>'
        f'<td>{loc_badge(p.get("Location",""))}</td></tr>'
        for p in prods if p.get("Barcode")
    ) or '<tr><td colspan="4" class="empty-state">No barcodes linked yet</td></tr>'}
    </tbody></table>
    </div>
  </div>

</div>
<script>
// Auto-focus barcode input when page loads
document.getElementById('barcodeInput').focus();
// After selecting product, re-focus barcode input
document.getElementById('prodSelect').addEventListener('change', function(){{
  document.getElementById('barcodeInput').focus();
}});
</script>"""


# ── Quick OUT Page ────────────────────────────────────────────────
def build_quick_out(db, user, msg="", msg_type="success", scanned_bc="", found_prod=None):
    """Main outbound page — scan barcode → product loads → confirm OUT."""

    alert = f'<div class="alert alert-{msg_type}" style="margin-bottom:16px;">{msg}</div>' if msg else ""

    # Product info panel (shown after scan)
    if found_prod:
        qty      = found_prod.get("Quantity") or 0
        thr      = found_prod.get("LowStockThreshold") or 10
        inv_id   = found_prod.get("InventoryID","")
        prod_panel = f"""
        <div class="card" style="border:2px solid #2B5BA8;margin-top:16px;">
          <div class="card-header" style="background:#EEF6FD;color:#1A3A8F;">
            &#128230; Product Found
          </div>
          <div class="card-body">
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
              <div>
                <div style="font-size:20px;font-weight:800;color:var(--navy);">{esc(found_prod.get("Name",""))}</div>
                <div style="color:var(--muted);font-size:13px;margin-top:4px;">
                  SKU: <span style="font-family:monospace;">{esc(found_prod.get("BaseSKU",""))}</span>
                  &nbsp;&middot;&nbsp; {esc(found_prod.get("Category",""))}
                </div>
                <div style="margin-top:8px;">{loc_badge(found_prod.get("Location",""))}</div>
              </div>
              <div style="text-align:right;">
                <div style="font-size:13px;color:var(--muted);">Current Stock</div>
                <div style="font-size:40px;font-weight:800;">{qty_badge(qty, thr)}</div>
              </div>
            </div>
            <form method="post" action="/api/quick_out">
              <input type="hidden" name="inventory_id" value="{inv_id}">
              <input type="hidden" name="barcode"      value="{esc(scanned_bc)}">
              <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;">
                <div class="form-group" style="margin:0;flex:1;min-width:120px;">
                  <label class="form-label">Qty to Remove</label>
                  <input class="form-input" name="qty" type="number"
                         min="1" max="{qty}" value="1" required
                         style="font-size:22px;font-weight:800;text-align:center;">
                </div>
                <div class="form-group" style="margin:0;flex:2;min-width:180px;">
                  <label class="form-label">Notes (optional)</label>
                  <input class="form-input" name="notes" placeholder="e.g. customer name, PO number...">
                </div>
                <button class="btn btn-red" type="submit"
                        style="padding:12px 28px;font-size:16px;margin-bottom:1px;">
                  &#10134; Confirm OUT
                </button>
              </div>
            </form>
          </div>
        </div>"""
    else:
        prod_panel = ""
        if scanned_bc:
            prod_panel = f"""
            <div class="alert alert-error" style="margin-top:16px;">
              &#10060; Barcode <strong style="font-family:monospace;">{esc(scanned_bc)}</strong>
              not found in system.
              <a href="/link_barcode" style="color:var(--red);text-decoration:underline;margin-left:8px;">
                Link it now &#8594;
              </a>
            </div>"""

    # Recent OUTs today
    txs = db.get_transactions(limit=50)
    recent = [t for t in txs if t.get("Type") == "OUT"][:10]
    recent_rows = ""
    for t in recent:
        recent_rows += f"""<tr>
          <td><strong>{esc(t.get("ProductName",""))}</strong></td>
          <td style="font-family:monospace;font-size:12px;">{esc(t.get("BaseSKU",""))}</td>
          <td style="font-weight:700;color:var(--red);">-{t.get("Quantity",0)}</td>
          <td style="font-size:12px;color:var(--muted);">{(t.get("Date") or "")[:16]}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(t.get("UserName",""))}</td>
        </tr>"""
    if not recent_rows:
        recent_rows = '<tr><td colspan="5" class="empty-state">No outbound transactions yet</td></tr>'

    return f"""
<div class="page-header">
  <div>
    <div class="page-title">&#128250; Quick OUT — Outbound Scanner</div>
    <div class="page-sub">Scan a product label to instantly log stock removal</div>
  </div>
  <a href="/link_barcode" class="btn btn-blue">&#128247; Link New Barcode</a>
</div>
{alert}

<!-- BIG SCAN BOX -->
<div class="card" style="border:2px solid #D2C4B4;">
  <div class="card-header" style="font-size:16px;">
    &#128269; Scan Product Barcode
    <span style="font-size:12px;color:var(--muted);font-weight:400;">
      Point scanner at label — field is always ready
    </span>
  </div>
  <div class="card-body">
    <form method="post" action="/api/scan_lookup" id="scanForm">
      <div style="display:flex;gap:12px;align-items:center;">
        <input class="form-input" name="barcode" id="scanInput"
               value="{esc(scanned_bc)}"
               placeholder="&#128269;  Point scanner here and pull trigger..."
               autocomplete="off"
               style="font-size:22px;font-family:monospace;letter-spacing:3px;flex:1;">
        <button class="btn btn-blue" type="submit"
                style="padding:12px 24px;font-size:15px;">
          &#128270; Lookup
        </button>
        <a href="/quick_out" class="btn btn-muted" style="padding:12px 20px;font-size:15px;">
          &#10005; Clear
        </a>
      </div>
    </form>
    {prod_panel}
  </div>
</div>

<!-- Recent OUT transactions -->
<div class="card">
  <div class="card-header">Recent Outbound Transactions</div>
  <table><thead><tr>
    <th>Product</th><th>SKU</th><th>Qty Out</th><th>Date</th><th>By</th>
  </tr></thead><tbody>{recent_rows}</tbody></table>
</div>

<script>
// Always keep scan input focused so scanner trigger works instantly
const si = document.getElementById('scanInput');
if(si){{
  si.focus();
  si.select();
  // Re-focus if user clicks elsewhere accidentally
  document.addEventListener('click', function(e){{
    if(e.target.tagName !== 'BUTTON' && e.target.tagName !== 'INPUT'
       && e.target.tagName !== 'SELECT' && e.target.tagName !== 'A'){{
      si.focus();
    }}
  }});
  // Auto-submit when scanner sends Enter after barcode
  si.addEventListener('keydown', function(e){{
    if(e.key === 'Enter'){{
      e.preventDefault();
      document.getElementById('scanForm').submit();
    }}
  }});
}}
</script>"""


# ── Assembly Page ─────────────────────────────────────────────────
def build_assembly(db, user, msg="", msg_type="success"):
    is_admin = (user or {}).get("Role") == "Admin"
    prods    = db.get_all_products()
    pending  = db.get_assembly_requests(status="PENDING")
    history  = db.get_assembly_requests()

    alert = f'<div class="alert alert-{msg_type}" style="margin-bottom:16px;">{msg}</div>' if msg else ""

    # Product options for finished product selector
    prod_opts = "".join(
        f'<option value="{p["InventoryID"]}|{p["ProductID"]}">'
        f'{esc(p["Name"])} — {esc(p["BaseSKU"])} ({p.get("Location","?")} · {p.get("Quantity",0)} pcs)'
        f'</option>' for p in prods
    )

    # Pending approvals (admin sees action buttons)
    pending_rows = ""
    for r in pending:
        parts_html = ", ".join(
            f'<span style="font-family:monospace;font-size:11px;background:#F0F4F8;'
            f'padding:2px 6px;border-radius:4px;">'
            f'{esc(p["PartName"])} ×{p["QtyUsed"]}</span>'
            for p in r.get("parts", [])
        ) or "—"
        approve_btns = ""
        if is_admin:
            approve_btns = f"""
              <form method="post" action="/api/approve_assembly" style="display:inline;">
                <input type="hidden" name="request_id" value="{r['RequestID']}">
                <button class="btn btn-green btn-sm" type="submit">✅ Approve</button>
              </form>
              <form method="post" action="/api/reject_assembly" style="display:inline;margin-left:4px;">
                <input type="hidden" name="request_id" value="{r['RequestID']}">
                <button class="btn btn-red btn-sm" type="submit">✗ Reject</button>
              </form>"""
        pending_rows += f"""<tr>
          <td>#{r['RequestID']}</td>
          <td><strong>{esc(r['ProductName'])}</strong><br>
              <span style="font-family:monospace;font-size:11px;">{esc(r['BaseSKU'])}</span></td>
          <td style="font-weight:700;font-size:16px;">{r['QtyToAssemble']}</td>
          <td>{parts_html}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['SubmittedBy'])}<br>{(r['SubmittedAt'] or '')[:16]}</td>
          <td>{approve_btns if is_admin else '<span style="color:var(--muted);font-size:12px;">Waiting for admin</span>'}</td>
        </tr>"""

    if not pending_rows:
        pending_rows = '<tr><td colspan="6" class="empty-state">No pending requests</td></tr>'

    # History rows
    hist_rows = ""
    for r in history[:20]:
        status_badge = {
            "PENDING":  '<span style="background:#FFF3CD;color:#856404;padding:2px 8px;border-radius:10px;font-size:11px;">⏳ Pending</span>',
            "APPROVED": '<span style="background:#D1FAE5;color:#065F46;padding:2px 8px;border-radius:10px;font-size:11px;">✅ Approved</span>',
            "REJECTED": '<span style="background:#FEE2E2;color:#991B1B;padding:2px 8px;border-radius:10px;font-size:11px;">✗ Rejected</span>',
        }.get(r["Status"], r["Status"])
        hist_rows += f"""<tr>
          <td>#{r['RequestID']}</td>
          <td>{esc(r['ProductName'])}</td>
          <td>{r['QtyToAssemble']}</td>
          <td>{status_badge}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['SubmittedBy'])}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['ReviewedBy'] or '—')}</td>
          <td style="font-size:12px;color:var(--muted);">{(r['SubmittedAt'] or '')[:16]}</td>
        </tr>"""

    if not hist_rows:
        hist_rows = '<tr><td colspan="7" class="empty-state">No assembly history yet</td></tr>'

    return f"""
<div class="page-header">
  <div>
    <div class="page-title">🔧 Assembly</div>
    <div class="page-sub">Submit an assembly job — Admin will approve and stock will be updated</div>
  </div>
</div>
{alert}

<div style="display:grid;grid-template-columns:1fr 1.4fr;gap:20px;margin-bottom:20px;">

  <!-- Submit form -->
  <div class="card">
    <div class="card-header" style="background:#EEF6FD;color:#1A3A8F;">
      ➕ New Assembly Request
    </div>
    <div class="card-body">
      <form method="post" action="/api/submit_assembly" id="assemblyForm">

        <div class="form-group">
          <label class="form-label">Finished Product (being assembled)</label>
          <select class="form-select" name="finished_inv" required>
            <option value="">— Select product —</option>{prod_opts}
          </select>
        </div>

        <div class="form-group">
          <label class="form-label">Quantity Assembled Today</label>
          <input class="form-input" name="qty_assembled" type="number"
                 min="1" value="1" required
                 style="font-size:18px;font-weight:700;text-align:center;">
        </div>

        <div class="form-group">
          <label class="form-label" style="font-weight:700;">
            Parts / Components Used
            <span style="font-size:11px;color:var(--muted);font-weight:400;">
              (select which inventory items were consumed)
            </span>
          </label>
          <div id="partsList" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:#FAFAFA;">
            <div class="part-row" style="display:flex;gap:8px;margin-bottom:8px;align-items:center;">
              <select class="form-select" name="part_inv[]" style="flex:2;margin:0;">
                <option value="">— Select part —</option>{prod_opts}
              </select>
              <input class="form-input" name="part_qty[]" type="number"
                     min="1" value="1" placeholder="Qty"
                     style="flex:1;margin:0;text-align:center;">
              <button type="button" onclick="this.parentElement.remove()"
                      style="background:#FEE2E2;color:#991B1B;border:none;
                             border-radius:4px;padding:4px 10px;cursor:pointer;font-size:16px;">✕</button>
            </div>
          </div>
          <button type="button" onclick="addPart()"
                  style="margin-top:8px;background:#EEF6FD;color:#1A3A8F;
                         border:1px dashed #1A3A8F;border-radius:6px;
                         padding:6px 16px;cursor:pointer;font-size:13px;width:100%;">
            + Add Another Part
          </button>
        </div>

        <div class="form-group">
          <label class="form-label">Notes (optional)</label>
          <input class="form-input" name="notes" placeholder="e.g. Batch #12, assembled by John...">
        </div>

        <button class="btn btn-blue" type="submit" style="width:100%;padding:12px;">
          📋 Submit for Approval
        </button>
      </form>
    </div>
  </div>

  <!-- Pending approvals -->
  <div class="card">
    <div class="card-header" style="background:#FFF9E6;color:#856404;">
      ⏳ Pending Approval ({len(pending)})
      {'<span style="font-size:11px;font-weight:400;margin-left:8px;">You can approve below</span>' if is_admin else '<span style="font-size:11px;font-weight:400;margin-left:8px;">Waiting for Admin</span>'}
    </div>
    <div style="overflow-x:auto;">
    <table><thead><tr>
      <th>#</th><th>Product</th><th>Qty</th><th>Parts Used</th><th>Submitted</th>
      <th>{"Action" if is_admin else "Status"}</th>
    </tr></thead><tbody>{pending_rows}</tbody></table>
    </div>
  </div>

</div>

<!-- History -->
<div class="card">
  <div class="card-header">📋 Assembly History</div>
  <div style="overflow-x:auto;">
  <table><thead><tr>
    <th>#</th><th>Product</th><th>Qty</th><th>Status</th>
    <th>Submitted By</th><th>Reviewed By</th><th>Date</th>
  </tr></thead><tbody>{hist_rows}</tbody></table>
  </div>
</div>

<script>
var prodOpts = `{prod_opts}`;
function addPart() {{
  var div = document.createElement('div');
  div.className = 'part-row';
  div.style = 'display:flex;gap:8px;margin-bottom:8px;align-items:center;';
  div.innerHTML = `
    <select class="form-select" name="part_inv[]" style="flex:2;margin:0;">
      <option value="">— Select part —</option>` + prodOpts + `
    </select>
    <input class="form-input" name="part_qty[]" type="number"
           min="1" value="1" placeholder="Qty"
           style="flex:1;margin:0;text-align:center;">
    <button type="button" onclick="this.parentElement.remove()"
            style="background:#FEE2E2;color:#991B1B;border:none;
                   border-radius:4px;padding:4px 10px;cursor:pointer;font-size:16px;">✕</button>`;
  document.getElementById('partsList').appendChild(div);
}}
</script>"""


# ── Transfer Page ──────────────────────────────────────────────────
def build_transfer(db, user, msg="", msg_type="success"):
    is_admin = (user or {}).get("Role") == "Admin"
    prods    = db.get_all_products()
    pending  = db.get_transfer_requests(status="PENDING")
    history  = db.get_transfer_requests()

    alert = f'<div class="alert alert-{msg_type}" style="margin-bottom:16px;">{msg}</div>' if msg else ""

    AREAS = [("Warehouse A", "WA"), ("Warehouse B", "WB"), ("Capitol", "CAP")]
    area_opts = "".join(
        f'<option value="{code}">{name}</option>' for name, code in AREAS
    )

    prod_opts = "".join(
        f'<option value="{p["InventoryID"]}|{p.get("Location","")}">'
        f'{esc(p["Name"])} — {esc(p["BaseSKU"])} '
        f'({p.get("Location","?")} · {p.get("Quantity",0)} pcs)'
        f'</option>' for p in prods
    )

    # Pending rows
    pending_rows = ""
    for r in pending:
        approve_btns = ""
        if is_admin:
            approve_btns = f"""
              <form method="post" action="/api/approve_transfer" style="display:inline;">
                <input type="hidden" name="request_id" value="{r['RequestID']}">
                <button class="btn btn-green btn-sm" type="submit">✅ Approve</button>
              </form>
              <form method="post" action="/api/reject_transfer" style="display:inline;margin-left:4px;">
                <input type="hidden" name="request_id" value="{r['RequestID']}">
                <button class="btn btn-red btn-sm" type="submit">✗ Reject</button>
              </form>"""
        pending_rows += f"""<tr>
          <td>#{r['RequestID']}</td>
          <td><strong>{esc(r['ProductName'])}</strong><br>
              <span style="font-family:monospace;font-size:11px;">{esc(r['BaseSKU'])}</span></td>
          <td style="font-weight:700;">{r['QtyToMove']}</td>
          <td>{loc_badge(r['FromLocation'])} → {loc_badge(r['ToLocation'])}</td>
          <td style="font-size:12px;color:var(--muted);">{r['CurrentQty']} pcs</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['SubmittedBy'])}<br>{(r['SubmittedAt'] or '')[:16]}</td>
          <td>{approve_btns if is_admin else '<span style="color:var(--muted);font-size:12px;">Waiting for admin</span>'}</td>
        </tr>"""

    if not pending_rows:
        pending_rows = '<tr><td colspan="7" class="empty-state">No pending transfers</td></tr>'

    # History rows
    hist_rows = ""
    for r in history[:20]:
        status_badge = {
            "PENDING":  '<span style="background:#FFF3CD;color:#856404;padding:2px 8px;border-radius:10px;font-size:11px;">⏳ Pending</span>',
            "APPROVED": '<span style="background:#D1FAE5;color:#065F46;padding:2px 8px;border-radius:10px;font-size:11px;">✅ Approved</span>',
            "REJECTED": '<span style="background:#FEE2E2;color:#991B1B;padding:2px 8px;border-radius:10px;font-size:11px;">✗ Rejected</span>',
        }.get(r["Status"], r["Status"])
        hist_rows += f"""<tr>
          <td>#{r['RequestID']}</td>
          <td>{esc(r['ProductName'])}</td>
          <td>{r['QtyToMove']}</td>
          <td>{loc_badge(r['FromLocation'])} → {loc_badge(r['ToLocation'])}</td>
          <td>{status_badge}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['SubmittedBy'])}</td>
          <td style="font-size:12px;color:var(--muted);">{esc(r['ReviewedBy'] or '—')}</td>
          <td style="font-size:12px;color:var(--muted);">{(r['SubmittedAt'] or '')[:16]}</td>
        </tr>"""

    if not hist_rows:
        hist_rows = '<tr><td colspan="8" class="empty-state">No transfer history yet</td></tr>'

    return f"""
<div class="page-header">
  <div>
    <div class="page-title">🚛 Stock Transfer</div>
    <div class="page-sub">Move stock between Warehouse A, Warehouse B, and Capitol — Admin approval required</div>
  </div>
</div>
{alert}

<div style="display:grid;grid-template-columns:1fr 1.4fr;gap:20px;margin-bottom:20px;">

  <!-- Submit form -->
  <div class="card">
    <div class="card-header" style="background:#EEF6FD;color:#1A3A8F;">
      ➕ New Transfer Request
    </div>
    <div class="card-body">
      <form method="post" action="/api/submit_transfer">

        <div class="form-group">
          <label class="form-label">Product to Transfer</label>
          <select class="form-select" name="inventory_id_loc" required>
            <option value="">— Select product —</option>{prod_opts}
          </select>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
          <div class="form-group">
            <label class="form-label">From</label>
            <select class="form-select" name="from_location" required>
              {area_opts}
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">To</label>
            <select class="form-select" name="to_location" required>
              {area_opts}
            </select>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Quantity to Move</label>
          <input class="form-input" name="qty" type="number"
                 min="1" value="1" required
                 style="font-size:18px;font-weight:700;text-align:center;">
        </div>

        <div class="form-group">
          <label class="form-label">Notes (optional)</label>
          <input class="form-input" name="notes" placeholder="e.g. reason for transfer...">
        </div>

        <button class="btn btn-blue" type="submit" style="width:100%;padding:12px;">
          📋 Submit for Approval
        </button>
      </form>
    </div>
  </div>

  <!-- Pending -->
  <div class="card">
    <div class="card-header" style="background:#FFF9E6;color:#856404;">
      ⏳ Pending Approval ({len(pending)})
      {'<span style="font-size:11px;font-weight:400;margin-left:8px;">You can approve below</span>' if is_admin else '<span style="font-size:11px;font-weight:400;margin-left:8px;">Waiting for Admin</span>'}
    </div>
    <div style="overflow-x:auto;">
    <table><thead><tr>
      <th>#</th><th>Product</th><th>Qty</th><th>Route</th>
      <th>Available</th><th>Submitted</th>
      <th>{"Action" if is_admin else "Status"}</th>
    </tr></thead><tbody>{pending_rows}</tbody></table>
    </div>
  </div>

</div>

<!-- History -->
<div class="card">
  <div class="card-header">📋 Transfer History</div>
  <div style="overflow-x:auto;">
  <table><thead><tr>
    <th>#</th><th>Product</th><th>Qty</th><th>Route</th><th>Status</th>
    <th>Submitted By</th><th>Reviewed By</th><th>Date</th>
  </tr></thead><tbody>{hist_rows}</tbody></table>
  </div>
</div>"""


# ── Scan Transfer Page ────────────────────────────────────────────
def build_scan_transfer(db, user, msg="", msg_type="success",
                         session_id=None, scanned=None,
                         from_loc="WA", to_loc="CAP"):
    """
    Mobile-optimised page for Android scanner.
    Staff scans boxes one by one — system builds a list.
    When done, one tap submits the full transfer record.
    Session stored in a temp dict keyed by session_id.
    """
    alert = (f'<div class="alert alert-{msg_type}" '
             f'style="margin-bottom:12px;">{msg}</div>') if msg else ""

    AREAS = [("Warehouse A","WA"), ("Warehouse B","WB"), ("Capitol","CAP")]

    area_opts_from = "".join(
        f'<option value="{c}" {"selected" if c==from_loc else ""}>{n}</option>'
        for n, c in AREAS)
    area_opts_to = "".join(
        f'<option value="{c}" {"selected" if c==to_loc else ""}>{n}</option>'
        for n, c in AREAS)

    # Current scan session items
    scanned = scanned or []
    total_boxes = len(scanned)

    # Build scanned list display
    # Group by product for summary
    summary = {}
    for s in scanned:
        key = s["inv_id"]
        if key not in summary:
            summary[key] = {"name": s["name"], "sku": s["sku"],
                            "count": 0, "color": s.get("color","#888")}
        summary[key]["count"] += 1

    scanned_rows = ""
    for inv_id, info in summary.items():
        scanned_rows += f"""
        <tr>
          <td>
            <span style="display:inline-block;width:10px;height:10px;
                         border-radius:50%;background:{info['color']};
                         margin-right:6px;"></span>
            <strong>{esc(info['name'])}</strong>
          </td>
          <td style="font-family:monospace;font-size:12px;">{esc(info['sku'])}</td>
          <td style="text-align:center;">
            <span style="background:#1E40AF;color:white;padding:3px 12px;
                         border-radius:20px;font-weight:700;font-size:16px;">
              {info['count']}
            </span>
            <span style="font-size:11px;color:var(--muted);"> boxes</span>
          </td>
        </tr>"""

    if not scanned_rows:
        scanned_rows = '<tr><td colspan="3" class="empty-state">No boxes scanned yet — scan a label to start</td></tr>'

    # Recent completed transfers
    recent = db.get_transfer_requests()
    recent_rows = ""
    for r in [x for x in recent if x.get("Status") == "APPROVED"][:5]:
        recent_rows += f"""<tr>
          <td style="font-size:12px;">#{r['RequestID']}</td>
          <td style="font-size:12px;"><strong>{esc(r['ProductName'])}</strong></td>
          <td style="font-size:12px;">{loc_badge(r['FromLocation'])} → {loc_badge(r['ToLocation'])}</td>
          <td style="font-size:12px;font-weight:700;">{r['QtyToMove']} boxes</td>
          <td style="font-size:11px;color:var(--muted);">{(r['SubmittedAt'] or '')[:16]}</td>
        </tr>"""
    if not recent_rows:
        recent_rows = '<tr><td colspan="5" class="empty-state">No transfers yet</td></tr>'

    sid = session_id or ""

    return f"""
<div style="max-width:520px;margin:0 auto;padding:8px;">

{alert}

<!-- Route selector -->
<div class="card" style="margin-bottom:12px;">
  <div class="card-header" style="font-size:15px;padding:12px 16px;">
    &#128666; Scan Transfer — Select Route
  </div>
  <div style="padding:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
    <form method="post" action="/api/scan_transfer_route" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;width:100%;">
      <select class="form-select" name="from_loc" style="flex:1;min-width:110px;">
        {area_opts_from}
      </select>
      <span style="font-size:20px;">&#8594;</span>
      <select class="form-select" name="to_loc" style="flex:1;min-width:110px;">
        {area_opts_to}
      </select>
      <input type="hidden" name="session_id" value="{sid}">
      <button class="btn btn-blue" type="submit" style="width:100%;margin-top:8px;padding:10px;">
        Set Route
      </button>
    </form>
  </div>
</div>

<!-- Scan box — BIG for Android -->
<div class="card" style="margin-bottom:12px;border:2px solid #2B5BA8;">
  <div style="padding:14px;">
    <div style="font-size:13px;font-weight:700;color:var(--muted);
                text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;">
      &#128269; Scan Box Label
    </div>
    <form method="post" action="/api/scan_transfer_add" id="scanForm">
      <input type="hidden" name="session_id" value="{sid}">
      <input type="hidden" name="from_loc"   value="{from_loc}">
      <input type="hidden" name="to_loc"     value="{to_loc}">
      <input class="form-input" name="barcode" id="scanInput"
             placeholder="&#128249; Pull trigger to scan..."
             autocomplete="off" autofocus
             style="font-size:20px;font-family:monospace;
                    letter-spacing:3px;text-align:center;
                    padding:16px;border:2px solid #2B5BA8;">
      <button class="btn btn-blue" type="submit"
              style="width:100%;padding:14px;font-size:16px;margin-top:8px;">
        &#10003; Add Box
      </button>
    </form>
  </div>
</div>

<!-- Scanned summary -->
<div class="card" style="margin-bottom:12px;">
  <div class="card-header" style="padding:12px 16px;">
    &#128230; Scanned This Session
    <span style="float:right;background:#1E40AF;color:white;
                 padding:2px 10px;border-radius:20px;font-weight:700;">
      {total_boxes} boxes
    </span>
  </div>
  <div style="overflow-x:auto;">
    <table style="font-size:13px;">
      <thead><tr>
        <th>Product</th><th>SKU</th><th>Boxes</th>
      </tr></thead>
      <tbody>{scanned_rows}</tbody>
    </table>
  </div>

  <!-- Confirm / Clear buttons -->
  {f'''
  <div style="padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:10px;">
    <form method="post" action="/api/scan_transfer_confirm">
      <input type="hidden" name="session_id" value="{sid}">
      <input type="hidden" name="from_loc"   value="{from_loc}">
      <input type="hidden" name="to_loc"     value="{to_loc}">
      <button class="btn btn-green" type="submit"
              style="width:100%;padding:14px;font-size:15px;"
              onclick="return confirm('Submit transfer of {total_boxes} boxes?')">
        &#9989; Confirm Transfer
      </button>
    </form>
    <form method="post" action="/api/scan_transfer_clear">
      <input type="hidden" name="session_id" value="{sid}">
      <button class="btn btn-red" type="submit"
              style="width:100%;padding:14px;font-size:15px;"
              onclick="return confirm('Clear all scanned boxes?')">
        &#128465; Clear All
      </button>
    </form>
  </div>''' if scanned else ''}
</div>

<!-- Recent transfers -->
<div class="card">
  <div class="card-header" style="font-size:13px;padding:10px 14px;">
    Recent Completed Transfers
  </div>
  <div style="overflow-x:auto;">
    <table style="font-size:12px;">
      <thead><tr><th>#</th><th>Product</th><th>Route</th><th>Qty</th><th>Date</th></tr></thead>
      <tbody>{recent_rows}</tbody>
    </table>
  </div>
</div>

</div>

<script>
// Keep scan input always focused on Android
var si = document.getElementById('scanInput');
if(si) {{
  si.focus();
  // Auto-submit when scanner sends Enter
  si.addEventListener('keydown', function(e) {{
    if(e.key === 'Enter') {{
      e.preventDefault();
      document.getElementById('scanForm').submit();
    }}
  }});
  // Re-focus after any tap
  document.addEventListener('click', function(e) {{
    var tag = e.target.tagName;
    if(tag !== 'BUTTON' && tag !== 'SELECT' && tag !== 'A') si.focus();
  }});
}}
</script>"""


# ── HTTP Handler ─────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    db = None

    def log_message(self, fmt, *args): pass

    def _get_token(self):
        for part in self.headers.get("Cookie","").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "wh_token": return v
        return None

    def _get_user(self):
        t = self._get_token()
        return _get_session(t) if t else None

    def _html(self, html, code=200):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, url):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _read_body(self):
        n = int(self.headers.get("Content-Length",0))
        return self.rfile.read(n).decode("utf-8","replace") if n else ""

    def _parse_form(self):
        raw = self._read_body()
        d   = {}
        for part in raw.split("&"):
            if "=" in part:
                k, v = part.split("=",1)
                d[unquote(k.replace("+"," "))] = unquote(v.replace("+"," "))
        return d

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path.rstrip("/") or "/dashboard"
        qs     = parse_qs(parsed.query)
        user   = self._get_user()

        # Public
        if path in ("","/","/login"):
            if user: return self._redirect("/dashboard")
            return self._html(login_page())
        if path == "/logout":
            t = self._get_token()
            if t: _del_session(t)
            self.send_response(302)
            self.send_header("Location","/login")
            self.send_header("Set-Cookie","wh_token=; Max-Age=0; Path=/")
            self.end_headers()
            return

        if not user: return self._redirect("/login")
        is_admin = user.get("Role") == "Admin"
        msg = qs.get("msg",[""])[0]
        err = qs.get("err",[""])[0]

        def ok_msg(m): return "✅ " + m if m else ""

        if path == "/dashboard":
            return self._html(page("Dashboard", build_dashboard(self.db, user), user, "dashboard"))

        if path == "/products":
            q    = qs.get("q",[""])[0]
            pmsg = ok_msg("Saved!") if msg=="saved" else ok_msg("Product added!") if msg=="added" \
                   else ok_msg("Deleted!") if msg=="deleted" else f"❌ {err}" if err else ""
            mt   = "success" if not err else "error"
            body = build_products(self.db, user, search=q, msg=pmsg, msg_type=mt)
            return self._html(page("Products", body, user, "products"))

        if path == "/inventory":
            return self._html(page("Inventory", build_inventory(self.db, user), user, "inventory"))

        if path == "/low_stock":
            return self._html(page("Low Stock", build_low_stock(self.db, user), user, "low_stock"))

        if path == "/transactions":
            return self._html(page("Transactions", build_transactions(self.db, user), user, "transactions"))

        if path == "/location_mapping":
            lmsg = "✅ Location assigned!" if msg=="ok" else ""
            body = build_location_mapping(self.db, user, msg=lmsg)
            return self._html(page("Location Mapping", body, user, "location_mapping"))

        if path == "/adjust":
            if not is_admin: return self._redirect("/dashboard")
            inv  = qs.get("inv",[""])[0]
            amsg = "✅ Stock adjusted!" if msg=="ok" else f"❌ {err}" if err else ""
            mt   = "success" if not err else "error"
            body = build_adjust(self.db, user, preselect_inv=inv, msg=amsg, msg_type=mt)
            return self._html(page("Adjust Stock", body, user, "adjust"))

        if path == "/add_product":
            if not is_admin: return self._redirect("/products")
            return self._html(page("Add Product", build_add_product(err=err), user, "add_product"))

        if path == "/edit_product":
            if not is_admin: return self._redirect("/products")
            inv_id = int(qs.get("id",[0])[0])
            return self._html(page("Edit Product", build_edit_product(self.db, inv_id), user, "products"))

        if path == "/add_location":
            if not is_admin: return self._redirect("/products")
            pid = int(qs.get("pid",[0])[0])
            return self._html(page("Add Location", build_add_location(self.db, pid, err=err), user, "products"))

        if path == "/delete_location":
            if not is_admin: return self._redirect("/products")
            inv_id = int(qs.get("inv",[0])[0])
            self.db.delete_location(inv_id, user=user)
            return self._redirect("/products?msg=deleted")

        if path == "/quick_out":
            smsg = "✅ Stock OUT recorded!" if msg == "ok" else f"❌ {err}" if err else ""
            mt   = "success" if not err else "error"
            body = build_quick_out(self.db, user, msg=smsg, msg_type=mt)
            return self._html(page("Quick OUT", body, user, "quick_out"))

        if path == "/link_barcode":
            smsg = "✅ Barcode linked!" if msg == "ok" else f"❌ {err}" if err else ""
            mt   = "success" if not err else "error"
            body = build_link_barcode(self.db, user, msg=smsg, msg_type=mt)
            return self._html(page("Link Barcode", body, user, "link_barcode"))

        if path == "/assembly":
            smsg = ("✅ Assembly request submitted! Waiting for Admin approval." if msg == "ok"
                    else "✅ Approved! Stock updated." if msg == "approved"
                    else "✅ Request rejected." if msg == "rejected"
                    else f"❌ {err}" if err else "")
            mt = "success" if not err else "error"
            body = build_assembly(self.db, user, msg=smsg, msg_type=mt)
            return self._html(page("Assembly", body, user, "assembly"))

        if path == "/transfer":
            smsg = ("✅ Transfer request submitted! Waiting for Admin approval." if msg == "ok"
                    else "✅ Transfer approved! Stock moved." if msg == "approved"
                    else "✅ Request rejected." if msg == "rejected"
                    else f"❌ {err}" if err else "")
            mt = "success" if not err else "error"
            body = build_transfer(self.db, user, msg=smsg, msg_type=mt)
            return self._html(page("Stock Transfer", body, user, "transfer"))

        if path == "/scan_transfer":
            import uuid
            sid      = qs.get("sid", [""])[0]
            from_loc = qs.get("from", ["WA"])[0]
            to_loc   = qs.get("to",   ["CAP"])[0]
            # Create new session if none
            if not sid:
                sid = str(uuid.uuid4())[:8]
                _set_scan_session(sid, {"items": [], "from": from_loc, "to": to_loc})
            sess     = _get_scan_session(sid)
            smsg = ("✅ Box added!" if msg == "added"
                    else "✅ Transfer submitted for approval!" if msg == "ok"
                    else f"❌ {err}" if err else "")
            mt   = "success" if not err else "error"
            body = build_scan_transfer(self.db, user,
                                       msg=smsg, msg_type=mt,
                                       session_id=sid,
                                       scanned=sess.get("items", []),
                                       from_loc=sess.get("from", from_loc),
                                       to_loc=sess.get("to", to_loc))
            return self._html(page("Scan Transfer", body, user, "scan_transfer"))

        return self._html(page("Not Found",'<div class="empty-state">Page not found.</div>',user),404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path   = parsed.path
        user   = self._get_user()

        # Login
        if path == "/api/login":
            try:
                body = json.loads(self._read_body())
                pin  = str(body.get("pin","")).strip()
                u    = self.db.verify_pin(pin)
                if u:
                    token = _make_token(u["UserID"])
                    _set_session(token, u)
                    self.send_response(200)
                    self.send_header("Content-Type","application/json")
                    self.send_header("Set-Cookie",
                        f"wh_token={token}; Path=/; HttpOnly; Max-Age=86400")
                    out = json.dumps({"ok":True}).encode()
                    self.send_header("Content-Length",len(out))
                    self.end_headers()
                    self.wfile.write(out)
                else:
                    return self._json({"ok":False,"error":"Wrong PIN"},401)
            except Exception as e:
                return self._json({"ok":False,"error":str(e)},500)
            return

        if not user: return self._redirect("/login")
        is_admin = user.get("Role") == "Admin"

        # Adjust
        if path == "/api/adjust":
            if not is_admin: return self._redirect("/dashboard")
            f = self._parse_form()
            try:
                inv_id = int(f.get("inventory_id",0))
                qty    = int(f.get("qty",0))
                op     = f.get("op","IN")
                notes  = f.get("notes","")
                if op == "IN":    delta = abs(qty)
                elif op == "OUT": delta = -abs(qty)
                else:
                    prods   = self.db.get_all_products()
                    prod    = next((p for p in prods if p.get("InventoryID")==inv_id),None)
                    current = prod.get("Quantity",0) if prod else 0
                    delta   = qty - current
                self.db.update_quantity(inv_id, delta, op, notes, user=user)
                return self._redirect("/adjust?msg=ok")
            except Exception as e:
                return self._redirect(f"/adjust?err={e}")

        # Add product
        if path == "/api/add_product":
            if not is_admin: return self._redirect("/products")
            f = self._parse_form()
            try:
                sku = f.get("sku","").strip()
                if not sku:
                    prefix = re.sub(r"[^A-Za-z]","",f.get("name",""))[:3].upper() or "PRD"
                    sku    = f"{prefix}{str(int(time.time()))[-5:]}"
                self.db.add_product(
                    f.get("name",""), f.get("category",""), sku,
                    float(f.get("price",0) or 0),
                    f.get("barcode","") or "",
                    int(f.get("qty",0) or 0),
                    int(f.get("threshold",10) or 10),
                    f.get("location","") or "Undecided",
                    user=user
                )
                return self._redirect("/products?msg=added")
            except Exception as e:
                return self._redirect(f"/add_product?err={e}")

        # Edit product
        if path == "/api/edit_product":
            if not is_admin: return self._redirect("/products")
            f = self._parse_form()
            try:
                inv_id = int(f.get("inventory_id",0) or 0) or None
                self.db.update_product(
                    int(f.get("product_id",0)),
                    f.get("name",""), f.get("category",""), f.get("sku",""),
                    float(f.get("price",0) or 0),
                    f.get("barcode","") or "",
                    int(f.get("threshold",10) or 10),
                    f.get("location","") or "Undecided",
                    inventory_id=inv_id
                )
                return self._redirect("/products?msg=saved")
            except Exception as e:
                return self._redirect(f"/products?err={e}")

        # Add location
        if path == "/api/add_location":
            if not is_admin: return self._redirect("/products")
            f = self._parse_form()
            try:
                pid      = int(f.get("pid",0))
                location = f.get("location","")
                qty      = int(f.get("qty",0) or 0)
                if not location:
                    return self._redirect(f"/add_location?pid={pid}&err=Please+select+a+location")
                self.db.add_location(pid, location, qty, user=user)
                return self._redirect("/products?msg=added")
            except Exception as e:
                return self._redirect(f"/add_location?pid={f.get('pid',0)}&err={e}")

        # Assign location
        if path == "/api/assign_location":
            f = self._parse_form()
            try:
                inv_id   = int(f.get("inv_id",0))
                pid      = int(f.get("pid",0))
                location = f.get("location","")
                prods    = self.db.get_all_products()
                p        = next((x for x in prods if x.get("InventoryID")==inv_id),None)
                if p:
                    self.db.update_product(
                        pid, p["Name"], p["Category"], p["BaseSKU"],
                        p.get("Price",0), p.get("Barcode") or "",
                        p.get("LowStockThreshold",10), location,
                        inventory_id=inv_id
                    )
                return self._redirect("/location_mapping?msg=ok")
            except Exception as e:
                return self._redirect(f"/location_mapping?err={e}")

        # ── Link Barcode ─────────────────────────────────────────────
        if path == "/api/link_barcode":
            f = self._parse_form()
            try:
                inv_id  = int(f.get("inventory_id", 0))
                barcode = f.get("barcode", "").strip()
                if not inv_id:
                    return self._redirect("/link_barcode?err=Select+a+product+first")
                if not barcode:
                    return self._redirect("/link_barcode?err=Barcode+cannot+be+empty")
                prods = self.db.get_all_products()
                p = next((x for x in prods if x.get("InventoryID") == inv_id), None)
                if not p:
                    return self._redirect("/link_barcode?err=Product+not+found")
                self.db.update_product(
                    p["ProductID"], p["Name"], p["Category"], p["BaseSKU"],
                    p.get("Price", 0), barcode,
                    p.get("LowStockThreshold", 10), p.get("Location") or "",
                    inventory_id=inv_id
                )
                return self._redirect("/link_barcode?msg=ok")
            except Exception as e:
                err_msg = str(e).replace(" ", "+")
                if "UNIQUE" in str(e):
                    err_msg = "That+barcode+is+already+linked+to+another+product"
                return self._redirect(f"/link_barcode?err={err_msg}")

        # ── Scan Lookup ──────────────────────────────────────────────
        if path == "/api/scan_lookup":
            f = self._parse_form()
            barcode = f.get("barcode", "").strip()
            if not barcode:
                return self._redirect("/quick_out")
            prods = self.db.get_all_products()
            found = next((p for p in prods
                          if (p.get("Barcode") or "").strip() == barcode), None)
            body = build_quick_out(self.db, user, scanned_bc=barcode, found_prod=found)
            return self._html(page("Quick OUT", body, user, "quick_out"))

        # ── Quick OUT confirm ────────────────────────────────────────
        if path == "/api/quick_out":
            f = self._parse_form()
            try:
                inv_id  = int(f.get("inventory_id", 0))
                qty_out = int(f.get("qty", 1))
                notes   = f.get("notes", "").strip() or "Quick OUT (scanner)"
                if qty_out < 1:
                    return self._redirect("/quick_out?err=Quantity+must+be+at+least+1")
                prods = self.db.get_all_products()
                p = next((x for x in prods if x.get("InventoryID") == inv_id), None)
                if not p:
                    return self._redirect("/quick_out?err=Product+not+found")
                current_qty = p.get("Quantity", 0) or 0
                if qty_out > current_qty:
                    return self._redirect(
                        f"/quick_out?err=Not+enough+stock.+Available:+{current_qty}")
                self.db.adjust_stock(inv_id, -qty_out, "OUT", notes, user=user)
                return self._redirect("/quick_out?msg=ok")
            except Exception as e:
                return self._redirect(f"/quick_out?err={str(e).replace(' ','+')}") 

        # ── Submit Assembly Request ───────────────────────────────────
        if path == "/api/submit_assembly":
            f = self._parse_form()
            try:
                finished_inv = f.get("finished_inv", "").split("|")
                inv_id  = int(finished_inv[0]) if finished_inv[0] else 0
                prod_id = int(finished_inv[1]) if len(finished_inv) > 1 and finished_inv[1] else 0
                qty     = int(f.get("qty_assembled", 1))
                notes   = f.get("notes", "").strip()
                # Collect parts — multiple values from part_inv[] and part_qty[]
                raw_body = self._read_body() if not hasattr(self, '_cached_body') else self._cached_body
                from urllib.parse import parse_qs
                form_data = parse_qs(raw_body if isinstance(raw_body, str) else raw_body.decode())
                part_invs = form_data.get("part_inv[]", [])
                part_qtys = form_data.get("part_qty[]", [])
                parts = []
                for pi, pq in zip(part_invs, part_qtys):
                    pi = pi.split("|")[0] if "|" in pi else pi
                    if pi and pi != "":
                        parts.append((int(pi), int(pq or 1)))
                if not inv_id or not prod_id:
                    return self._redirect("/assembly?err=Select+the+finished+product")
                self.db.submit_assembly_request(
                    prod_id, inv_id, qty, parts, notes, user=user)
                return self._redirect("/assembly?msg=ok")
            except Exception as e:
                return self._redirect(f"/assembly?err={str(e).replace(' ','+')}")

        # ── Approve / Reject Assembly ─────────────────────────────────
        if path == "/api/approve_assembly":
            if not is_admin: return self._redirect("/assembly")
            f = self._parse_form()
            try:
                self.db.approve_assembly_request(int(f.get("request_id", 0)), user=user)
                return self._redirect("/assembly?msg=approved")
            except Exception as e:
                return self._redirect(f"/assembly?err={str(e).replace(' ','+')}")

        if path == "/api/reject_assembly":
            if not is_admin: return self._redirect("/assembly")
            f = self._parse_form()
            try:
                self.db.reject_assembly_request(int(f.get("request_id", 0)), user=user)
                return self._redirect("/assembly?msg=rejected")
            except Exception as e:
                return self._redirect(f"/assembly?err={str(e).replace(' ','+')}")

        # ── Submit Transfer Request ───────────────────────────────────
        if path == "/api/submit_transfer":
            f = self._parse_form()
            try:
                inv_loc  = f.get("inventory_id_loc", "").split("|")
                inv_id   = int(inv_loc[0]) if inv_loc[0] else 0
                from_loc = f.get("from_location", "").strip()
                to_loc   = f.get("to_location", "").strip()
                qty      = int(f.get("qty", 1))
                notes    = f.get("notes", "").strip()
                if not inv_id:
                    return self._redirect("/transfer?err=Select+a+product")
                if from_loc == to_loc:
                    return self._redirect("/transfer?err=From+and+To+location+cannot+be+the+same")
                self.db.submit_transfer_request(
                    inv_id, qty, from_loc, to_loc, notes, user=user)
                return self._redirect("/transfer?msg=ok")
            except Exception as e:
                return self._redirect(f"/transfer?err={str(e).replace(' ','+')}")

        # ── Approve / Reject Transfer ─────────────────────────────────
        if path == "/api/approve_transfer":
            if not is_admin: return self._redirect("/transfer")
            f = self._parse_form()
            try:
                self.db.approve_transfer_request(int(f.get("request_id", 0)), user=user)
                return self._redirect("/transfer?msg=approved")
            except Exception as e:
                return self._redirect(f"/transfer?err={str(e).replace(' ','+')}")

        if path == "/api/reject_transfer":
            if not is_admin: return self._redirect("/transfer")
            f = self._parse_form()
            try:
                self.db.reject_transfer_request(int(f.get("request_id", 0)), user=user)
                return self._redirect("/transfer?msg=rejected")
            except Exception as e:
                return self._redirect(f"/transfer?err={str(e).replace(' ','+')}")

        if path == "/api/reject_transfer":
            if not is_admin: return self._redirect("/transfer")
            f = self._parse_form()
            try:
                self.db.reject_transfer_request(int(f.get("request_id", 0)), user=user)
                return self._redirect("/transfer?msg=rejected")
            except Exception as e:
                return self._redirect(f"/transfer?err={str(e).replace(' ','+')}")

        # ── Scan Transfer: Set Route ──────────────────────────────────
        if path == "/api/scan_transfer_route":
            import uuid
            f        = self._parse_form()
            sid      = f.get("session_id", "").strip()
            from_loc = f.get("from_loc", "WA").strip()
            to_loc   = f.get("to_loc",   "CAP").strip()
            if not sid:
                sid = str(uuid.uuid4())[:8]
            sess = _get_scan_session(sid)
            sess["from"] = from_loc
            sess["to"]   = to_loc
            _set_scan_session(sid, sess)
            return self._redirect(f"/scan_transfer?sid={sid}&from={from_loc}&to={to_loc}")

        # ── Scan Transfer: Add Box ────────────────────────────────────
        if path == "/api/scan_transfer_add":
            import uuid
            f        = self._parse_form()
            sid      = f.get("session_id", "").strip()
            barcode  = f.get("barcode", "").strip()
            from_loc = f.get("from_loc", "WA").strip()
            to_loc   = f.get("to_loc",   "CAP").strip()
            if not sid:
                sid = str(uuid.uuid4())[:8]
            if not barcode:
                return self._redirect(f"/scan_transfer?sid={sid}&err=No+barcode+detected")
            # Look up product by barcode
            prods = self.db.get_all_products()
            found = next((p for p in prods
                          if (p.get("Barcode") or "").strip() == barcode), None)
            if not found:
                return self._redirect(
                    f"/scan_transfer?sid={sid}"
                    f"&err=Barcode+not+found.+Link+it+first+at+/link_barcode"
                    f"&from={from_loc}&to={to_loc}")
            sess = _get_scan_session(sid)
            sess.setdefault("items", []).append({
                "inv_id":  found.get("InventoryID"),
                "prod_id": found.get("ProductID"),
                "name":    found.get("Name", ""),
                "sku":     found.get("BaseSKU", ""),
                "barcode": barcode,
                "color":   next((c for n, code, c in [
                    ("Warehouse A","WA","#2B5BA8"),
                    ("Warehouse B","WB","#2E7D52"),
                    ("Capitol","CAP","#D4A843")
                ] if code == from_loc), "#888"),
            })
            _set_scan_session(sid, sess)
            return self._redirect(
                f"/scan_transfer?sid={sid}&msg=added&from={from_loc}&to={to_loc}")

        # ── Scan Transfer: Confirm (submit one request per product) ───
        if path == "/api/scan_transfer_confirm":
            f        = self._parse_form()
            sid      = f.get("session_id", "").strip()
            from_loc = f.get("from_loc", "WA").strip()
            to_loc   = f.get("to_loc",   "CAP").strip()
            sess     = _get_scan_session(sid)
            items    = sess.get("items", [])
            if not items:
                return self._redirect(
                    f"/scan_transfer?sid={sid}&err=No+boxes+scanned+yet")
            try:
                # Group by inventory ID — one transfer request per product
                grouped = {}
                for item in items:
                    key = item["inv_id"]
                    grouped.setdefault(key, {"item": item, "count": 0})
                    grouped[key]["count"] += 1
                for inv_id, g in grouped.items():
                    self.db.submit_transfer_request(
                        inv_id,
                        g["count"],
                        from_loc,
                        to_loc,
                        notes=f"Scanner transfer — {g['count']} box(es) scanned",
                        user=user
                    )
                _clear_scan_session(sid)
                return self._redirect(f"/scan_transfer?msg=ok&from={from_loc}&to={to_loc}")
            except Exception as e:
                return self._redirect(
                    f"/scan_transfer?sid={sid}&err={str(e).replace(' ','+')}")

        # ── Scan Transfer: Clear Session ──────────────────────────────
        if path == "/api/scan_transfer_clear":
            f   = self._parse_form()
            sid = f.get("session_id", "").strip()
            _clear_scan_session(sid)
            return self._redirect("/scan_transfer")

        return self._redirect("/dashboard")


# ── Entry point ──────────────────────────────────────────────────
def run():
    db_path = config_manager.get_db_path()
    db = DatabaseManager(db_path)
    db.initialize()
    Handler.db = db
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Warehouse IMS Web Server running on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")

if __name__ == "__main__":
    run()
