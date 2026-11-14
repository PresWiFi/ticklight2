import base64
import importlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

ISSUER = "https://auth.example.com/application/o/ticklight"


def make_client(tmp_path, monkeypatch, **env):
    base = {"DATA_DIR": str(tmp_path), "AUTH_MODE": "none", "TELEGRAM_BOT_TOKEN": "",
            "OPENROUTER_API_KEY": "", "LOCAL_AI_URL": "", "LOCAL_AI_MODEL": ""}
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    import app.config
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    return TestClient(app.main.app, base_url="https://testserver"), app.main


def test_flow_without_ai(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        assert c.get("/").status_code == 200
        r = c.post("/api/capture?wait=1", json={"text": "call dentist tomorrow 3pm\nbuy milk\n  + check fridge"})
        assert r.json()["method"] == "parser"
        titles = [t["title"] for t in r.json()["tasks"]]
        assert titles == ["call dentist", "buy milk"]
        assert r.json()["tasks"][1]["next_step"]["title"] == "check fridge"
        b = c.get("/api/board").json()
        assert {t["title"] for t in b["focus"]} >= {"buy milk"}
        tid = r.json()["tasks"][1]["id"]
        done = c.post(f"/api/tasks/{tid}/complete").json()
        assert done["reward"]["xp"] > 0
        assert c.get("/api/board").json()["stats"]["xp"] == done["reward"]["xp"]
        assert c.post(f"/api/tasks/{tid}/not-now", json={"hours": 1}).status_code == 200
        assert c.post(f"/api/tasks/{tid}/nope").status_code == 404
        assert c.post(f"/api/tasks/{tid}/breakdown").status_code == 503
        cid = r.json()["capture_id"]
        assert c.post(f"/api/captures/{cid}/undo").json()["deleted"] == 2


def test_plain_text_capture_with_token(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        tok = c.post("/api/tokens", json={"name": "watch"}).json()["token"]
        r = c.post("/api/capture?wait=1&source=watch", content="water plants every 3 days",
                   headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/plain"})
        assert r.status_code == 202 and r.json()["tasks"][0]["recurrence_text"] == "every 3 days"
        bad = c.post("/api/capture", content="x", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


def test_capture_with_ai(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch, OPENROUTER_API_KEY="k",
                          OPENROUTER_FREE_MODELS="test/model:free")
    reply = {"tasks": [
        {"title": "Book dentist", "when": "friday", "priority": "high", "area": "health",
         "energy": "high", "minutes": 10, "steps": ["Find dentist's number", "Call"], "someday": False},
        {"title": "Learn piano", "when": "", "area": "fun", "energy": "medium", "someday": True},
        {"title": "Bogus", "area": "space", "energy": "huge", "minutes": "lots"},
    ]}
    seen = {}

    async def fake_post(self, url, **kw):
        seen["model"] = kw["json"]["model"]
        content = "<think>hmm</think>Sure! " + json.dumps(reply)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with c:
        r = c.post("/api/capture?wait=1", json={"text": "ugh need dentist asap friday, piano someday"}).json()
        assert r["method"] == "openrouter:test/model:free" and seen["model"] == "test/model:free"
        dent, piano, bogus = r["tasks"]
        assert dent["due_date"] and dent["parsed_summary"].startswith("Fri")
        assert (dent["area"], dent["energy"], dent["priority"]) == ("health", 3, 2)
        assert dent["next_step"]["title"] == "Find dentist's number"
        assert piano["status"] == "someday"
        assert (bogus["area"], bogus["energy"], bogus["minutes"]) == (None, None, None)


def _jwt(claims):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"


def test_oidc_login(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch, AUTH_MODE="oidc", OIDC_ISSUER=ISSUER,
                          OIDC_CLIENT_ID="cid", OIDC_CLIENT_SECRET="secret",
                          BASE_URL="https://tl.example.com", OIDC_ALLOWED_GROUPS="ticklight")
    state = {}

    async def fake_get(self, url, **kw):
        req = httpx.Request("GET", url)
        if url.endswith("openid-configuration"):
            return httpx.Response(200, request=req, json={
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "userinfo_endpoint": "https://auth.example.com/userinfo",
                "end_session_endpoint": "https://auth.example.com/end"})
        return httpx.Response(200, request=req, json={
            "sub": "u1", "name": "Alex", "groups": state.get("groups", ["ticklight"])})

    async def fake_post(self, url, **kw):
        assert kw["data"]["code_verifier"]
        tok = _jwt({"iss": ISSUER + "/", "aud": "cid", "sub": "u1", "nonce": state["nonce"],
                    "exp": time.time() + 60})
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"id_token": tok, "access_token": "at"})
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    def login():
        r = c.get("/auth/login?next=/?display", follow_redirects=False)
        q = parse_qs(urlparse(r.headers["location"]).query)
        assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["https://tl.example.com/auth/callback"]
        state["nonce"] = q["nonce"][0]
        return c.get(f"/auth/callback?code=abc&state={q['state'][0]}", follow_redirects=False)

    with c:
        assert c.get("/api/board").status_code == 401
        r = login()
        assert r.status_code == 307 and r.headers["location"] == "/?display"
        assert c.get("/api/me").json()["user"]["name"] == "Alex"
        tok = c.post("/api/tokens", json={"name": "desk"}).json()["token"]
        c.get("/auth/logout", follow_redirects=False)
        c.cookies.clear()
        assert c.get("/api/board").status_code == 401
        assert c.get(f"/api/board?token={tok}").status_code == 200
        state["groups"] = ["other"]
        assert login().status_code == 403
        assert c.get("/auth/callback?code=abc&state=forged").status_code == 403


def test_watch_pairing(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        start = c.post("/api/pair/start").json()
        assert len(start["code"]) == 6
        assert c.post("/api/pair/poll", json={"code": start["code"], "secret": start["secret"]}).json() == {"pending": True}
        assert c.post("/api/pair/poll", json={"code": start["code"], "secret": "wrong"}).status_code == 400
        assert c.post("/api/pair/confirm", json={"code": "000000"}).status_code == 400
        assert c.post("/api/pair/confirm", json={"code": start["code"], "name": "Galaxy Watch"}).json()["ok"]
        tok = c.post("/api/pair/poll", json={"code": start["code"], "secret": start["secret"]}).json()["token"]
        assert c.post("/api/pair/poll", json={"code": start["code"], "secret": start["secret"]}).status_code == 400
        r = c.post("/api/capture?wait=1", content="buy milk", headers={"Authorization": f"Bearer {tok}"})
        assert r.json()["tasks"][0]["title"] == "buy milk"
        assert [t["name"] for t in c.get("/api/tokens").json()] == ["Galaxy Watch"]


def test_push_subscribe_and_send(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch, BASE_URL="https://tl.example.com")
    sent = []
    monkeypatch.setattr("app.push.webpush", lambda sub, data, **kw: sent.append((sub, data, kw)))
    with c:
        me = c.get("/api/me").json()
        assert len(me["push_key"]) == 87  # 65-byte P-256 point, base64url
        sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
        assert c.post("/api/push/subscribe", json={"subscription": sub, "label": "phone"}).json()["devices"] == 1
        assert c.post("/api/push/subscribe", json={"subscription": {"endpoint": "http://x"}}).status_code == 400
        assert c.post("/api/push/test").json() == {"sent": 1}
        assert sent[0][2]["vapid_claims"] == {"sub": "https://tl.example.com"}
        # keys survive a restart
        key = me["push_key"]
    c2, _ = make_client(tmp_path, monkeypatch, BASE_URL="https://tl.example.com")
    with c2:
        assert c2.get("/api/me").json()["push_key"] == key


def test_voice_capture(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch, WHISPER_URL="http://whisper:8000/v1")

    async def fake_post(self, url, **kw):
        assert url == "http://whisper:8000/v1/audio/transcriptions"
        assert kw["files"]["file"][0] == "voice.webm"
        return httpx.Response(200, json={"text": " call mum tomorrow "}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    with c:
        r = c.post("/api/capture/voice", content=b"\x1aE\xdf\xa3", headers={"Content-Type": "audio/webm;codecs=opus"}).json()
        assert r["heard"] == "call mum tomorrow" and r["tasks"][0]["title"] == "call mum"


def test_voice_not_configured(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch, WHISPER_URL="")
    with c:
        assert c.post("/api/capture/voice", content=b"x").status_code == 503


def test_routine_endpoints_and_prefs(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        p = c.patch("/api/prefs", json={"evening": {"time": "22:15"}}).json()
        assert p["evening"] == {"enabled": True, "time": "22:15"}
        t = c.post("/api/quick", json={"text": "taxes"}).json()
        assert c.post(f"/api/tasks/{t['id']}/pin").json()["pinned"]
        m = c.get("/api/routine/morning").json()
        assert [x["title"] for x in m["pinned"]] == ["taxes"]
        assert c.post(f"/api/tasks/{t['id']}/move", json={"when": "tomorrow"}).json()["due_date"]
        assert "leftovers" in c.get("/api/routine/evening").json()
        assert "items" in c.get("/api/routine/weekly").json()
        assert c.post("/api/routine/evening/done").json()["xp"] == 15
        assert c.get("/api/routine/nope").status_code == 404
        r = c.post("/api/action", json={"action": f"done:{t['id']}"}).json()
        assert r["ok"] and r["reward"]["xp"] > 0
        assert c.post("/api/hyperfocus", json={}).json()["until"]
        assert c.post("/api/hyperfocus", json={"minutes": 0}).json()["until"] is None
        assert c.get("/sw.js").status_code == 200
        assert c.get("/manifest.webmanifest").status_code == 200


def test_sessions_and_coach_api(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        t = c.post("/api/tasks", json={"title": "write report", "steps": ["open doc"]}).json()
        s = c.post("/api/sessions", json={"minutes": 20, "task_id": t["id"]}).json()
        assert s["state"] == "running" and s["coach"]["text"]
        b = c.get("/api/board").json()
        assert b["session"]["id"] == s["id"] and b["hyperfocus_until"]
        assert b["coach"]["persona"]["id"] == "sunny" and b["coach"]["latest"]
        r = c.post(f"/api/sessions/{s['id']}/checkin", json={"mood": "good"}).json()
        assert r["coach"]["event"] == "checkin_good"
        assert c.post(f"/api/sessions/{s['id']}/checkin", json={"mood": "meh"}).status_code == 400
        assert c.post(f"/api/sessions/{s['id']}/extend", json={"minutes": 10}).json()["minutes"] == 30
        e = c.post(f"/api/sessions/{s['id']}/end", json={"complete_task": True}).json()
        assert e["outcome"] == "done" and e["reward"]["xp"] > 0
        assert c.post(f"/api/sessions/{s['id']}/break", json={"minutes": 3}).json()["state"] == "break"
        assert c.get("/api/sessions/active").json()["session"]["state"] == "break"
        c.post(f"/api/sessions/{s['id']}/end-break")
        assert c.get("/api/sessions/active").json()["session"] is None
        info = c.get("/api/coach").json()
        assert len(info["personas"]) == 6 and "Laundry" in info["personas"][0]["sample"]
        c.patch("/api/prefs", json={"coach": {"persona": "mochi"}, "sessions": {"minutes": 45, "sound": "rain"}})
        t2 = c.post("/api/quick", json={"text": "x"}).json()
        done = c.post(f"/api/tasks/{t2['id']}/complete").json()
        assert done["coach"]["name"] == "Mochi"
        assert c.post("/api/sessions", json={}).json()["minutes"] == 45


def test_game_api(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch)
    with c:
        t = c.post("/api/quick", json={"text": "x"}).json()
        done = c.post(f"/api/tasks/{t['id']}/complete").json()
        assert done["reward"]["coins"] >= 1
        b = c.get("/api/board").json()
        assert b["game"]["coins"] == done["reward"]["coins"] and b["game"]["streak"] == 1
        assert "first_task" in {x["key"] for x in b["new_badges"]}
        c.post("/api/game/seen")
        assert c.get("/api/board").json()["new_badges"] == []
        g = c.get("/api/game").json()
        assert len(g["challenges"]) == 3 and any(i["id"] == "blush" for i in g["items"])
        assert c.post("/api/game/buy", json={"item": "crown"}).status_code == 400
        with main.db.tx() as cur:
            cur.execute("INSERT INTO coin_events(user_id, amount, reason, created_at) VALUES(1, 100, 't', '2026')")
        assert c.post("/api/game/buy", json={"item": "blush"}).json()["coins"] >= 70
        assert c.post("/api/game/equip", json={"item": "blush"}).json()["equipped"] == {"face": "blush"}
        assert c.post("/api/game/theme", json={"item": "paper"}).json()["theme"] == "paper"
        assert c.post("/api/game/claim", json={"slot": 0}).status_code == 400


def test_people_api_and_join_no_login(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch)
    sent = []

    async def fake_send(uid, note):
        sent.append((uid, note.title))
        return True
    monkeypatch.setattr(main.scheduler, "send_now", fake_send)
    with c:
        inv = c.post("/api/people/invite", json={"name": "Sam"}).json()
        assert "/join?code=" in inv["url"]
        page = c.get(f"/join?code={inv['code']}")
        assert page.status_code == 200 and "Join as Sam" in page.text
        r = c.post(f"/join?code={inv['code']}", follow_redirects=False)
        token = r.headers["location"].split("token=")[1].split("&")[0]
        sam = {"Authorization": f"Bearer {token}"}
        assert c.patch("/api/me", json={"name": "Alex"}).json()["name"] == "Alex"
        assert c.patch("/api/me", json={"name": " "}).status_code == 400
        me_people = c.get("/api/people").json()["people"]
        assert me_people[0]["name"] == "Sam"
        sam_id = me_people[0]["id"]
        # I share a chore; Sam sees it with my name on it
        c.post("/api/tasks", json={"title": "bins", "shared": True})
        board = c.get("/api/board", headers=sam).json()
        bins = next(t for t in board["anytime"] if t["title"] == "bins")
        assert bins["owner_name"] == "Alex" and bins["shared"]
        # I send Sam a task; Sam is notified
        assert c.post(f"/api/people/{sam_id}/send", json={"text": "call the plumber"}).json()["sent"] == 1
        assert sent[-1][0] == sam_id and "call the plumber" in sent[-1][1]
        assert any(t["title"] == "call the plumber" and t.get("from_name") for t in c.get("/api/board", headers=sam).json()["anytime"])
        # I finish something, Sam cheers
        t = c.post("/api/quick", json={"text": "taxes"}).json()
        c.post(f"/api/tasks/{t['id']}/complete")
        feed = c.get("/api/people/feed", headers=sam).json()["feed"]
        win = next(f for f in feed if f["title"] == "taxes")
        assert c.post("/api/people/react", json={"completion_id": win["completion_id"], "emoji": "🎉"}, headers=sam).json()["ok"]
        assert "🎉" in sent[-1][1] and "taxes" in sent[-1][1]
        assert c.post("/api/people/999/send", json={"text": "x"}).status_code == 404
        assert c.get("/join?code=nope").status_code == 404
        assert c.post("/api/telegram/link-code").json()["code"]
