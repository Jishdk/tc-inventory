"""Gedeelde DB-helper: leest SUPABASE_DB_URL uit inventory/.env en levert een
SQLAlchemy-engine.

De Supabase-string bevat een wachtwoord met URL-speciale tekens (^ * ( & ...).
We parsen de DSN zelf met een regex en bouwen de engine via URL.create, dat het
wachtwoord veilig escapet — naïeve urlsplit/urlencode zou hierop stuklopen.
SSL wordt afgedwongen (Supabase vereist het)."""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

INVENTORY = Path(__file__).resolve().parent.parent
load_dotenv(INVENTORY / ".env")

# scheme://user:pass@host[:port]/db[?query]  — pass mag speciale tekens bevatten
DSN_RE = re.compile(
    r"^(?P<scheme>[\w+]+)://(?P<user>[^:@/]+):(?P<pw>.*)@"
    r"(?P<host>[^:/@]+)(?::(?P<port>\d+))?/(?P<db>[^?]+)(?:\?(?P<query>.*))?$"
)


def get_engine():
    raw = os.getenv("SUPABASE_DB_URL")
    if not raw:
        sys.exit("SUPABASE_DB_URL niet gevonden in inventory/.env — "
                 "zet de Supabase connection string erin.")
    m = DSN_RE.match(raw.strip())
    if not m:
        sys.exit("SUPABASE_DB_URL heeft een onverwacht formaat.")

    query = dict(p.split("=", 1) for p in m["query"].split("&") if "=" in p) if m["query"] else {}
    query.setdefault("sslmode", "require")

    url = URL.create(
        drivername="postgresql+psycopg2",
        username=m["user"],
        password=m["pw"],          # raw; URL.create escapet dit veilig
        host=m["host"],
        port=int(m["port"]) if m["port"] else 5432,
        database=m["db"],
        query=query,
    )
    return create_engine(url, pool_pre_ping=True)
