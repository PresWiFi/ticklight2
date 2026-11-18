from app.ai import clean_triage

from .test_api import make_client


def fake_ai(m, tasks):
    async def triage(text, now, projects=None):
        fake_ai.seen_projects = projects
        return clean_triage({"tasks": tasks}), "fake"
    m.ai.triage = triage


def titles(r):
    return {t["title"]: (t["project"] or {}).get("name") for t in r["tasks"]}


def test_heading_files_lines_into_a_project_without_ai(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        r = c.post("/api/capture?wait=1", json={"text": "Garage:\nbuy shelves\nsell the bike\n\nbuy milk\nToday:\ncall mum"}).json()
        assert r["method"] == "parser"
        assert titles(r) == {"buy shelves": "Garage", "sell the bike": "Garage", "buy milk": None,
                             "Today:": None, "call mum": None}
        assert [(p["name"], p["new"]) for p in r["projects"]] == [("Garage", True)]
        # A second dump with the same heading reuses it
        r = c.post("/api/capture?wait=1", json={"text": "garage:\nsweep floor"}).json()
        assert titles(r) == {"sweep floor": "Garage"} and r["projects"][0]["new"] is False
        b = c.get("/api/board").json()
        assert [(p["name"], p["open"]) for p in b["projects"]] == [("Garage", 3)]
        assert b["projects"][0]["next"]["title"] == "buy shelves"


def test_ai_project_rules(tmp_path, monkeypatch):
    c, m = make_client(tmp_path, monkeypatch)
    with c:
        pid = c.post("/api/projects", json={"name": "Kitchen Reno"}).json()["id"]
        fake_ai(m, [
            {"title": "Pick tiles", "project": "kitchen reno"},          # existing, any case
            {"title": "Book flights", "project": "Japan Trip"},          # new, 2 tasks
            {"title": "Find hotel", "project": "Japan Trip"},
            {"title": "Buy milk", "project": "Groceries"},               # new, 1 task, not said
            {"title": "Fix gate", "project": "Fence"},                   # new, 1 task, but said
        ])
        r = c.post("/api/capture?wait=1", json={"text": "tiles, japan flights and hotel, milk, fix gate for the fence"}).json()
        assert fake_ai.seen_projects == ["Kitchen Reno"]
        assert titles(r) == {"Pick tiles": "Kitchen Reno", "Book flights": "Japan Trip", "Find hotel": "Japan Trip",
                             "Buy milk": None, "Fix gate": "Fence"}
        assert {p["name"]: p["new"] for p in r["projects"]} == {"Kitchen Reno": False, "Japan Trip": True, "Fence": True}
        # Undo removes the tasks and the projects that dump started, not older ones
        c.post(f"/api/captures/{r['capture_id']}/undo")
        assert [p["name"] for p in c.get("/api/projects").json()] == ["Kitchen Reno"]
        assert c.get(f"/api/projects/{pid}").json()["open"] == 0


def test_capture_into_a_project(tmp_path, monkeypatch):
    c, m = make_client(tmp_path, monkeypatch)
    with c:
        pid = c.post("/api/projects", json={"name": "Move"}).json()["id"]
        fake_ai(m, [{"title": "Order boxes", "project": "Other"}, {"title": "Call movers", "project": ""}])
        r = c.post(f"/api/capture?wait=1&project_id={pid}", json={"text": "boxes, movers"}).json()
        assert titles(r) == {"Order boxes": "Move", "Call movers": "Move"}
        r = c.post(f"/api/capture?wait=1&project_id={pid}", json={"text": "label boxes\npack books"})
        assert c.get(f"/api/projects/{pid}").json()["open"] == 4
        assert c.post("/api/capture?wait=1&project_id=999", json={"text": "x"}).status_code == 404


def test_edit_finish_reopen_delete(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch)
    with c:
        p = c.post("/api/projects", json={"name": "  Garden  "}).json()
        assert p["name"] == "Garden" and c.post("/api/projects", json={"name": "garden"}).json()["id"] == p["id"]
        a = c.post("/api/tasks", json={"title": "plant bulbs", "project_id": p["id"]}).json()
        b = c.post("/api/tasks", json={"title": "mulch"}).json()
        assert a["project"]["name"] == "Garden" and b["project"] is None
        b = c.patch(f"/api/tasks/{b['id']}", json={"project_id": p["id"], "title": "mulch beds"}).json()
        assert b["project"]["id"] == p["id"]
        c.post(f"/api/tasks/{a['id']}/complete")
        assert c.patch(f"/api/projects/{p['id']}", json={"name": "Yard"}).json()["name"] == "Yard"
        c.post("/api/projects", json={"name": "Other"})
        assert c.patch(f"/api/projects/{p['id']}", json={"name": "other"}).status_code == 400
        # Finishing parks what's left in Someday and hides the project
        d = c.patch(f"/api/projects/{p['id']}", json={"status": "done"}).json()
        assert d["status"] == "done" and d["open"] == 0 and d["someday"] == 1 and d["done"] == 1
        assert c.get(f"/api/tasks/{b['id']}").json()["status"] == "someday"
        assert [x["name"] for x in c.get("/api/board").json()["projects"]] == ["Other"]
        assert [x["name"] for x in c.get("/api/projects?status=done").json()] == ["Yard"]
        assert c.patch(f"/api/projects/{p['id']}", json={"status": "open"}).json()["status"] == "open"
        # Deleting keeps the tasks
        c.delete(f"/api/projects/{p['id']}")
        assert c.get(f"/api/tasks/{b['id']}").json()["project"] is None
        assert c.patch(f"/api/tasks/{b['id']}", json={"project_id": p["id"]}).status_code == 404
