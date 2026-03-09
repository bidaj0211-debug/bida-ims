"""
cloud_main.py — Entry point for Railway cloud deployment
"""
import os, sys, types

# ── Inject cloud db before web_server loads ───────────────────
import db_manager_pg as _pg_module
sys.modules['db_manager'] = _pg_module

# Fake config_manager so web_server doesn't crash
fake_cm = types.ModuleType("config_manager")
class _Cfg:
    def get_db_path(self): return None
    def get(self, k, d=None): return os.environ.get(k, d)
fake_cm.config_manager = _Cfg()
sys.modules['config_manager'] = fake_cm

# ── Now import web_server ─────────────────────────────────────
import web_server

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))

    # Initialize DB and attach to Handler class
    from db_manager_pg import DatabaseManager
    db = DatabaseManager()
    db.initialize()
    web_server.Handler.db = db

    from http.server import HTTPServer
    server = HTTPServer(("0.0.0.0", PORT), web_server.Handler)
    print(f"✅ Bida Sales IMS running on port {PORT}", flush=True)
    server.serve_forever()
