"""
cloud_main.py
Entry point for cloud deployment (Railway).
Uses PostgreSQL instead of SQLite.
"""
import os
import sys

# ── Swap db_manager for PostgreSQL version ────────────────────────
# This must happen BEFORE web_server imports db_manager
import db_manager_pg as _pg_module
sys.modules['db_manager'] = _pg_module

# Also patch config_manager so web_server doesn't look for local files
class _FakeConfig:
    def get_db_path(self): return None
    def get(self, key, default=None): return os.environ.get(key, default)

import types
fake_config = types.ModuleType("config_manager")
fake_config.config_manager = _FakeConfig()
fake_config.get_db_path = lambda: None
sys.modules['config_manager'] = fake_config

# ── Now import and run web_server ─────────────────────────────────
import web_server

# Railway assigns PORT via environment variable
PORT = int(os.environ.get("PORT", 8080))
web_server.PORT = PORT

if __name__ == "__main__":
    from db_manager_pg import DatabaseManager
    db = DatabaseManager()
    db.initialize()
    web_server.Handler.db = db
    from http.server import HTTPServer
    server = HTTPServer(("0.0.0.0", PORT), web_server.Handler)
    print(f"Bida Sales IMS running on port {PORT}")
    server.serve_forever()
