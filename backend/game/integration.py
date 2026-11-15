"""External integration layer: everything here is OFF until an administrator configures it.

Nothing is ever sent anywhere automatically, and no NASA/IPAC endpoint is assumed: there is no
public API that accepts citizen-science submissions for SPHEREx, so the layer is built around
packages an administrator downloads and hands to a review partner themselves, plus optional
adapters (outbound webhook, inbound review-result webhook, API client) that need an explicit
URL/secret from the administrator before they can do anything.

Export packages are deterministic JSON (sorted keys) with a SHA-256 checksum of the body.
Contributors are identified by pseudonymous ids; a username appears only for players who opted
in to attribution.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from typing import Any

from .db import audit, j, now, uj

FORMAT = "spherex-citizen-science-package/1"

DEFAULTS = [
    ("json_export", "export", {"description": "Download a candidate evidence package (manual hand-off).", "destination": None}),
    ("review_result_import", "import", {"description": "Import a scientific review result from a JSON file an organisation returned.",
                                        "accepted_formats": [FORMAT + "-review"]}),
    ("outbound_webhook", "webhook", {"description": "POST a package to a partner URL when an admin clicks Send.", "url": None, "secret": None}),
    ("inbound_review_webhook", "webhook", {"description": "Receive review results from a partner (HMAC-SHA256 signed).", "secret": None}),
    ("partner_api_client", "api_client", {"description": "Client for a partner's review API, if one is ever provided.", "base_url": None,
                                          "note": "No endpoint is configured. None is assumed."}),
]


def seed(conn: sqlite3.Connection) -> None:
    for key, kind, spec in DEFAULTS:
        conn.execute("INSERT OR IGNORE INTO integration_configs(key, kind, enabled, spec, updated_at) VALUES (?,?,0,?,?)",
                     (key, kind, j(spec), now()))


def configs(conn: sqlite3.Connection, redact: bool = True) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute("SELECT * FROM integration_configs ORDER BY key"):
        spec = uj(r["spec"], {})
        if redact and spec.get("secret"):
            spec["secret"] = "••••"
        out.append({**dict(r), "spec": spec, "enabled": bool(r["enabled"])})
    return out


def configure(conn: sqlite3.Connection, key: str, enabled: bool, spec: dict[str, Any] | None, actor: int) -> None:
    row = conn.execute("SELECT * FROM integration_configs WHERE key=?", (key,)).fetchone()
    if row is None:
        raise ValueError("unknown integration")
    cur = uj(row["spec"], {})
    if spec:
        for k, v in spec.items():
            if k in cur or k in ("url", "secret", "base_url", "destination"):
                cur[k] = v
    needs = {"outbound_webhook": ["url", "secret"], "inbound_review_webhook": ["secret"], "partner_api_client": ["base_url"]}.get(key, [])
    if enabled and any(not cur.get(n) for n in needs):
        raise ValueError(f"set {', '.join(needs)} before enabling {key}")
    for n in ("url", "base_url"):
        if cur.get(n) and not str(cur[n]).startswith("https://"):
            raise ValueError(f"{n} must be an https:// URL")
    conn.execute("UPDATE integration_configs SET enabled=?, spec=?, updated_at=?, updated_by=? WHERE key=?",
                 (int(enabled), j(cur), now(), actor, key))
    audit(conn, actor, "integration.configure", "integration", key, {"enabled": enabled, "fields": sorted((spec or {}).keys())})


def enabled(conn: sqlite3.Connection, key: str) -> dict | None:
    r = conn.execute("SELECT spec FROM integration_configs WHERE key=? AND enabled=1", (key,)).fetchone()
    return uj(r["spec"], {}) if r else None


def package(conn: sqlite3.Connection, cand_id: str) -> dict[str, Any]:
    """The evidence for one candidate, ready for an external reviewer."""
    from .daily import reliability
    from .reviews import stats
    c = conn.execute("SELECT * FROM candidates WHERE id=?", (cand_id,)).fetchone()
    if c is None:
        raise ValueError("no such candidate")
    inv = conn.execute("SELECT * FROM investigations WHERE id=?", (c["investigation_id"],)).fetchone()
    data = uj(inv["data"], {})
    data.pop("training_answer", None)
    contribs = []
    for r in conn.execute("SELECT c.*, u.pseudonym, u.username, u.share_attribution FROM contributions c JOIN users u ON u.id=c.user_id "
                          "WHERE c.candidate_id=? AND c.hidden=0 AND c.qc_status IN ('passed','passed_with_warnings') ORDER BY c.id", (cand_id,)):
        qc = uj(r["qc"], {})
        contribs.append({"contribution_id": r["id"], "contributor": r["pseudonym"],
                         "attribution": r["username"] if r["share_attribution"] else None,
                         "submitted_at": r["submitted_at"], "payload": uj(r["payload"], {}), "observations": uj(r["obs_refs"], []),
                         "qc_status": r["qc_status"], "qc_checks": qc.get("checks"), "evidence_score": qc.get("evidence_score"),
                         "label": qc.get("label"),
                         # accuracy of this contributor on catalogued stars with known classes (Daily Survey anchors)
                         "contributor_reliability": reliability(conn, r["user_id"])})
    reviews = [{"label": r["independent_label"], "agrees": bool(r["agrees"]), "reason": r["reason_code"], "at": r["created_at"]}
               for r in conn.execute("SELECT * FROM reviews WHERE candidate_id=? ORDER BY id", (cand_id,))]
    body = {"format": FORMAT, "candidate": {"id": c["id"], "category": c["category"], "status": c["status"], "ra_deg": c["ra"],
                                             "dec_deg": c["dec"], "source_ref": c["source_ref"], "summary": uj(c["summary"], {})},
            "source": {"name": inv["source_name"], **{k: data.get(k) for k in sorted(data)}},
            "observations": uj(inv["obs_refs"], []),
            "observation_provenance": "SPHEREx spectral images (Level 2) found via NASA/IPAC IRSA; ids as listed by the archive.",
            "contributions": contribs, "community_reviews": reviews, "agreement": stats(conn, cand_id),
            "disclaimer": ("Citizen-science evidence package. Automated quality checks and community agreement are not scientific "
                           "validation. Not produced or endorsed by NASA, Caltech/IPAC or the SPHEREx team.")}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return {"package": body, "sha256": hashlib.sha256(canon.encode()).hexdigest()}


def record_export(conn: sqlite3.Connection, cand_id: str, actor: int, via: str) -> dict[str, Any]:
    pk = package(conn, cand_id)
    conn.execute("INSERT OR IGNORE INTO integration_events(config_key, direction, idem_key, payload, status, created_at) VALUES (?,?,?,?,?,?)",
                 (via, "out", f"{via}:{cand_id}:{pk['sha256']}", j({"candidate": cand_id, "sha256": pk["sha256"]}), "prepared", now()))
    conn.execute("UPDATE candidates SET status='awaiting_scientific_review', updated_at=? WHERE id=? AND status='evidence_sufficient'",
                 (now(), cand_id))
    audit(conn, actor, "integration.export", "candidate", cand_id, {"via": via, "sha256": pk["sha256"]})
    return pk


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature.removeprefix("sha256="))


def receive_review(conn: sqlite3.Connection, raw: bytes, signature: str | None) -> dict[str, Any]:
    """Inbound review-result webhook. Disabled unless configured; HMAC-checked; idempotent."""
    spec = enabled(conn, "inbound_review_webhook")
    if spec is None:
        raise PermissionError("inbound review webhook is disabled")
    if not verify_signature(spec["secret"], raw, signature):
        raise PermissionError("bad signature")
    msg = json.loads(raw)
    idem = f"in:{msg.get('id') or hashlib.sha256(raw).hexdigest()}"
    if conn.execute("INSERT OR IGNORE INTO integration_events(config_key, direction, idem_key, payload, status, created_at) "
                    "VALUES (?,?,?,?,?,?)", ("inbound_review_webhook", "in", idem, raw.decode()[:20000], "received", now())).rowcount == 0:
        return {"status": "duplicate"}
    # Results are queued for an administrator to confirm; never applied automatically.
    audit(conn, None, "integration.inbound", "integration", "inbound_review_webhook", {"idem": idem})
    return {"status": "queued_for_admin"}
