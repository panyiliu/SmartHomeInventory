from __future__ import annotations

import os
from pathlib import Path

from fridge_app import create_app

app = create_app()


if __name__ == "__main__":
    cert = (os.getenv("FRIDGE_SSL_CERT") or "").strip()
    key = (os.getenv("FRIDGE_SSL_KEY") or "").strip()

    if not (cert and key):
        base = Path(__file__).resolve().parent
        cert_dir = base / "certs"
        if cert_dir.exists():
            pem_candidates = sorted([p for p in cert_dir.glob("*.pem") if not p.name.endswith("-key.pem")])
            for pem in pem_candidates:
                key_pem = pem.with_name(pem.stem + "-key.pem")
                if key_pem.exists():
                    cert = str(pem)
                    key = str(key_pem)
                    break

    ssl_context = (cert, key) if cert and key else None
    if ssl_context:
        app.run(host="0.0.0.0", port=5443, debug=False, use_reloader=False, ssl_context=ssl_context)
    else:
        app.run(host="0.0.0.0", port=5000, debug=True)
