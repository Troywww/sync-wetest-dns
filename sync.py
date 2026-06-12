"""Sync wetest.vip preferred CF IPs to Cloudflare DNS A records.

Sorted by carrier and region, matching wetest.vip grouping.

GitHub Actions Secrets needed:
  CF_API_TOKEN    Cloudflare API token (DNS edit permission)
  CF_ZONE_ID      Cloudflare zone ID
  DOMAIN_MAP      JSON mapping of carrier→subdomain, e.g.
                  {"CM":"cm.example.com","CU":"cu.example.com",
                   "CT":"ct.example.com","CN":"cf.example.com"}
Optional:
  MAX_RECORDS     Max A records per domain (default 2)
  PREFER_COLO     Preferred colo codes, e.g. "HKG,SIN,NRT" (default all)

DOMAIN_MAP example with carriers:
  CM = 移动 (China Mobile)
  CU = 联通 (China Unicom)
  CT = 电信 (China Telecom)
  CN = 三网 (All carriers combined, official premium)
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

WETEST_API = "https://www.wetest.vip/api/cf2dns/get_cloudflare_ip?key=o1zrmHAF&type=v4"
CF_API = "https://api.cloudflare.com/client/v4"

# Label mapping for logging
LINE_LABELS = {"CM": "移动", "CU": "联通", "CT": "电信", "CN": "三网"}


def log(*args):
    print(*args, flush=True)


def fatal(msg):
    log(f"FATAL: {msg}")
    sys.exit(1)


# ── Fetch from wetest ──────────────────────────────────────────

def fetch_wetest() -> dict[str, list[dict]]:
    """Returns {line_code: [{ip, colo, rtt, ...}]}"""
    log(f"Fetching {WETEST_API}")
    req = urllib.request.Request(WETEST_API, headers={"User-Agent": "sync-wetest-dns/1.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode())
    except Exception as e:
        fatal(f"wetest API failed: {e}")

    if not data.get("status"):
        fatal(f"wetest API error: {data.get('msg', 'unknown')}")

    info = data.get("info", {})
    groups = {}
    for line_code, items in info.items():
        records = []
        for item in items:
            records.append({
                "ip": item["ip"],
                "line": item["line"],
                "line_name": item["line_name"],
                "colo": item.get("colo", ""),
                "rtt": item.get("rtt_avg", 9999),
            })
        groups[line_code] = records
        log(f"  {line_code} ({LINE_LABELS.get(line_code, line_code)}): {len(records)} IPs")
    return groups


# ── Sort / filter per carrier group ────────────────────────────

def sort_group(records: list[dict], prefer_colo: set[str], max_records: int) -> list[dict]:
    """Sort by colo preference then latency, return top N."""
    if prefer_colo:
        preferred = [r for r in records if r["colo"] in prefer_colo]
        others = [r for r in records if r["colo"] not in prefer_colo]
        preferred.sort(key=lambda r: r["rtt"])
        others.sort(key=lambda r: r["rtt"])
        combined = preferred + others
    else:
        combined = sorted(records, key=lambda r: r["rtt"])
    return combined[:max_records]


# ── Cloudflare DNS helpers ─────────────────────────────────────

def cf_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def cf_request(token: str, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{CF_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=cf_headers(token), method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_snippet = e.read().decode()[:512] if e.fp else ""
        fatal(f"CF {method} {path} failed: {e.code} {body_snippet}")
    except Exception as e:
        fatal(f"CF {method} {path} error: {e}")


def get_existing_records(token: str, zone_id: str, domain: str) -> list[dict]:
    path = f"/zones/{zone_id}/dns_records?type=A&name={urllib.parse.quote(domain)}&per_page=100"
    result = cf_request(token, "GET", path)
    if not result.get("success"):
        fatal(f"CF list records error: {result.get('errors', [{}])[0].get('message', 'unknown')}")
    return result.get("result", [])


def sync_domain(token: str, zone_id: str, domain: str, target_ips: list[str]) -> str:
    log(f"  Syncing {domain} → {target_ips}")
    existing = get_existing_records(token, zone_id, domain)

    # Delete all old A records
    for rec in existing:
        log(f"    DELETE A {rec['content']} (old)")
        cf_request(token, "DELETE", f"/zones/{zone_id}/dns_records/{rec['id']}")

    # Create new ones
    for ip in target_ips:
        payload = {"type": "A", "name": domain, "content": ip, "ttl": 60, "proxied": False}
        log(f"    CREATE A {ip}")
        cf_request(token, "POST", f"/zones/{zone_id}/dns_records", payload)

    return f"created={len(target_ips)}"


# ── Main ───────────────────────────────────────────────────────

def main():
    token = os.environ.get("CF_API_TOKEN", "").strip()
    zone_id = os.environ.get("CF_ZONE_ID", "").strip()
    domain_map_raw = os.environ.get("DOMAIN_MAP", "").strip()
    prefer_colo_raw = os.environ.get("PREFER_COLO", "").strip()

    if not token:
        fatal("CF_API_TOKEN not set")
    if not zone_id:
        fatal("CF_ZONE_ID not set")
    if not domain_map_raw:
        fatal("DOMAIN_MAP not set")

    max_records = int(os.environ.get("MAX_RECORDS", "2"))
    prefer_colo = {c.strip().upper() for c in prefer_colo_raw.split(",") if c.strip()} if prefer_colo_raw else set()

    # Parse DOMAIN_MAP JSON
    try:
        domain_map = json.loads(domain_map_raw)
    except json.JSONDecodeError as e:
        fatal(f"DOMAIN_MAP is not valid JSON: {e}")
    if not isinstance(domain_map, dict):
        fatal("DOMAIN_MAP must be a JSON object")

    log(f"Max records per group: {max_records}")
    log(f"Prefer colo:           {prefer_colo or 'all'}")
    log(f"Domain map:           {json.dumps(domain_map)}")

    groups = fetch_wetest()
    if not groups:
        fatal("no IPs from wetest")

    total_created = 0
    for line_code in sorted(groups.keys()):
        domain = domain_map.get(line_code)
        if not domain:
            log(f"SKIP {line_code} ({LINE_LABELS.get(line_code, line_code)}): no domain configured")
            continue

        records = groups[line_code]
        selected = sort_group(records, prefer_colo, max_records)
        target_ips = [r["ip"] for r in selected]

        log(f"\n{line_code} ({LINE_LABELS.get(line_code, line_code)}) → {domain}")
        if preferred_preview := [r for r in selected if r["colo"] in prefer_colo]:
            for r in preferred_preview:
                log(f"  {r['ip']:15s}  colo={r['colo']:3s}  rtt={r['rtt']}ms  ← preferred")
        for r in selected:
            if r not in (preferred_preview if prefer_colo else []):
                log(f"  {r['ip']:15s}  colo={r['colo']:3s}  rtt={r['rtt']}ms")

        result = sync_domain(token, zone_id, domain, target_ips)
        log(f"  {result}")
        total_created += len(target_ips)

    log(f"\nDone. Total records created: {total_created}")
    log(f"WARNING: Some existing A records may have been deleted.")


if __name__ == "__main__":
    main()
