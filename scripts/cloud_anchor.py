#!/usr/bin/env python3
"""betgirl 체인 앵커링 — GitHub Actions 판.

레포 루트(현재 작업 디렉토리)의 anchors.jsonl 에 5개 체인(베팅·정산·적립·교환·추첨)의
tip 해시를 추가한다. 읽기는 공개 anon 키만 사용하고, 푸시는 워크플로의 GITHUB_TOKEN 이
담당하므로 시크릿이 전혀 필요 없다. 변화가 없으면 아무것도 쓰지 않는다(멱등).

OpenTimestamps 스탬프는 ots CLI 가 설치되어 있으면 수행한다 (실패해도 앵커는 진행).
"""
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SUPABASE_URL = "https://scpijkzdxalswmnljafu.supabase.co"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNjcGlqa3pkeGFsc3dtbmxqYWZ1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU5MDg5NDksImV4cCI6MjEwMTQ4NDk0OX0."
    "w9MAXZorH9-VunRq-Z6_VH7pWGUYLSYlNsvfmSWkYwE"
)
KST = timezone(timedelta(hours=9))
ROOT = Path.cwd()
ANCHOR_FILE = ROOT / "anchors.jsonl"
CHAINS = ("bets", "settlements", "credits", "redemptions", "draws")


def rest(path: str, headers: dict | None = None):
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{path}",
        headers={"apikey": ANON_KEY, **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()), dict(r.headers)


def chain_state(table: str) -> dict | None:
    try:
        _, h = rest(f"{table}?select=seq&limit=1", {"Prefer": "count=exact", "Range": "0-0"})
        total = int(h.get("Content-Range", "0-0/0").split("/")[-1])
        if total == 0:
            return {"rows": 0, "last_seq": None, "tip": None}
        rows, _ = rest(f"{table}?select=seq,row_hash&order=seq.desc&limit=1")
        if rows[0]["row_hash"] is None:
            return None
        return {"rows": total, "last_seq": rows[0]["seq"], "tip": rows[0]["row_hash"]}
    except Exception:
        return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def main() -> int:
    chains = {}
    for name in CHAINS:
        st = chain_state(f"betgirl_{name}")
        if st is not None:
            chains[name] = st
    if "bets" not in chains or "settlements" not in chains:
        print("핵심 체인 조회 실패 — 중단")
        return 1

    prev = None
    if ANCHOR_FILE.exists():
        lines = ANCHOR_FILE.read_text().strip().splitlines()
        prev = json.loads(lines[-1]) if lines else None

    if prev and all(prev.get(k, {}).get("tip") == v["tip"] for k, v in chains.items()):
        print("변화 없음 — 앵커 스킵")
        return 0

    record = {
        "at": datetime.now(KST).isoformat(timespec="seconds"),
        **chains,
        "prev_anchor_sha256": sha256(json.dumps(prev, ensure_ascii=False, sort_keys=True)) if prev else None,
    }
    with ANCHOR_FILE.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print("앵커 기록: " + " / ".join(f"{k} {v['rows']}행" for k, v in chains.items()))

    # OpenTimestamps (선택)
    if shutil.which("ots"):
        day = datetime.now(KST).strftime("%Y%m%d")
        snap = ROOT / "ots" / f"anchors-{day}.txt"
        snap.parent.mkdir(exist_ok=True)
        snap.write_text(ANCHOR_FILE.read_text())
        r = subprocess.run(["ots", "stamp", str(snap)], capture_output=True, text=True, timeout=180)
        if r.returncode == 0:
            print(f"OpenTimestamps 스탬프: {snap.name}.ots")
        else:
            snap.unlink(missing_ok=True)
            print(f"ots 스탬프 실패(계속 진행): {r.stderr.strip()[:150]}")
    else:
        print("ots 미설치 — OpenTimestamps 생략")

    return 0


if __name__ == "__main__":
    sys.exit(main())
