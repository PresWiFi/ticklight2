from app.wishlist import split_title

from .test_api import make_client
from .test_projects import fake_ai


def test_split_title():
    assert split_title("Noise cancelling headphones £200 https://shop.example/x?id=1.") == \
        ("Noise cancelling headphones", "£200", "https://shop.example/x?id=1")
    assert split_title("  lego  set  ") == ("lego set", "", "")
    assert split_title("https://only.example/thing") == ("https://only.example/thing", "", "https://only.example/thing")
    assert split_title("bike $1,250.50 - road")[1] == "$1,250.50"


def test_wishlist_api(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        w = c.post("/api/wishes", json={"title": "Kindle £95 https://shop.example/k", "note": "paperwhite"}).json()
        assert (w["title"], w["price"], w["link"], w["note"], w["status"]) == \
            ("Kindle", "£95", "https://shop.example/k", "paperwhite", "want")
        assert c.post("/api/wishes", json={"title": "  "}).status_code == 400
        c.post("/api/wishes", json={"title": "Switch"})
        b = c.get("/api/board").json()
        assert b["wishlist"] == {"count": 2, "recent": ["Switch", "Kindle"]}
        assert all(t["title"] not in ("Kindle", "Switch") for t in b["anytime"])
        # edit
        w = c.patch(f"/api/wishes/{w['id']}", json={"title": "Kindle Paperwhite", "price": "£110"}).json()
        assert (w["title"], w["price"], w["note"]) == ("Kindle Paperwhite", "£110", "paperwhite")
        assert c.patch(f"/api/wishes/{w['id']}", json={"link": "javascript:alert(1)"}).status_code == 400
        assert c.patch(f"/api/wishes/{w['id']}", json={"status": "nope"}).status_code == 400
        # got it, and back
        g = c.patch(f"/api/wishes/{w['id']}", json={"status": "got"}).json()
        assert g["status"] == "got" and g["got_at"]
        assert [x["title"] for x in c.get("/api/wishes").json()] == ["Switch"]
        assert [x["title"] for x in c.get("/api/wishes?status=got").json()] == ["Kindle Paperwhite"]
        assert c.patch(f"/api/wishes/{w['id']}", json={"status": "want"}).json()["got_at"] is None
        c.delete(f"/api/wishes/{w['id']}")
        assert c.get("/api/board").json()["wishlist"]["count"] == 1
        assert c.delete(f"/api/wishes/{w['id']}").status_code == 404


def test_brain_dump_wishes_without_ai(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        r = c.post("/api/capture?wait=1", json={"text": "buy milk\nwish: lego castle £60\nWishlist:\nnew bike\nairpods\n\ncall mum"}).json()
        assert r["method"] == "parser"
        assert [t["title"] for t in r["tasks"]] == ["buy milk", "call mum"]
        assert [(w["title"], w["price"]) for w in r["wishes"]] == [("lego castle", "£60"), ("new bike", ""), ("airpods", "")]
        assert r["projects"] == []
        c.post(f"/api/captures/{r['capture_id']}/undo")
        assert c.get("/api/wishes").json() == []


def test_brain_dump_wishes_with_ai(tmp_path, monkeypatch):
    c, m = make_client(tmp_path, monkeypatch)
    with c:
        fake_ai(m, [{"title": "Mechanical keyboard £90", "wish": True}, {"title": "Order printer ink"}])
        r = c.post("/api/capture?wait=1", json={"text": "I really want a mech keyboard, 90 quid. also order ink"}).json()
        assert [t["title"] for t in r["tasks"]] == ["Order printer ink"]
        assert [(w["title"], w["price"]) for w in r["wishes"]] == [("Mechanical keyboard", "£90")]
