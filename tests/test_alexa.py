import asyncio
import base64
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from app.alexa import AlexaError, CertCache, best_match, check_cert_url, check_signature, check_timestamp
from tests.test_api import make_client

SKILL = "amzn1.ask.skill.test"


@pytest.mark.parametrize("url,ok", [
    ("https://s3.amazonaws.com/echo.api/echo-api-cert.pem", True),
    ("https://s3.amazonaws.com:443/echo.api/echo-api-cert.pem", True),
    ("https://S3.AMAZONAWS.COM/echo.api/x.pem", True),
    ("http://s3.amazonaws.com/echo.api/x.pem", False),
    ("https://notamazon.com/echo.api/x.pem", False),
    ("https://s3.amazonaws.com/EcHo.aPi/x.pem", False),
    ("https://s3.amazonaws.com:563/echo.api/x.pem", False),
    ("https://s3.amazonaws.com/echo.api/../evil.pem", False),
])
def test_cert_url(url, ok):
    if ok:
        check_cert_url(url)
    else:
        with pytest.raises(AlexaError):
            check_cert_url(url)


def test_timestamp():
    now = time.time()
    fresh = datetime.fromtimestamp(now, timezone.utc).isoformat().replace("+00:00", "Z")
    check_timestamp({"request": {"timestamp": fresh}}, now)
    with pytest.raises(AlexaError):
        check_timestamp({"request": {"timestamp": fresh}}, now + 200)
    with pytest.raises(AlexaError):
        check_timestamp({"request": {"timestamp": "nope"}}, now)


def _self_signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazon.com")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("echo-api.amazon.com")]), False)
            .sign(key, hashes.SHA256()))
    return key, cert


def test_signature():
    key, cert = _self_signed()
    raw = b'{"hello": 1}'
    sig = base64.b64encode(key.sign(raw, padding.PKCS1v15(), hashes.SHA256())).decode()
    check_signature(cert, raw, sig)
    with pytest.raises(AlexaError):
        check_signature(cert, raw + b" ", sig)


def test_untrusted_chain_rejected(monkeypatch):
    _, cert = _self_signed()
    pem = cert.public_bytes(serialization.Encoding.PEM)

    async def fake_get(self, url, **kw):
        return httpx.Response(200, content=pem, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(AlexaError, match="didn't verify"):
        asyncio.run(CertCache().chain("https://s3.amazonaws.com/echo.api/x.pem"))


def test_best_match():
    ts = [{"title": "Take the bins out"}, {"title": "Call the dentist"}, {"title": "Write proposal"}]
    assert best_match("the bins", ts)["title"] == "Take the bins out"
    assert best_match("called the dentist", ts)["title"] == "Call the dentist"
    assert best_match("walk to the moon", ts) is None


def req(intent=None, slots=None, kind="IntentRequest", skill=SKILL):
    body = {"version": "1.0", "session": {"application": {"applicationId": skill},
                                          "user": {"userId": "amzn1.ask.account.X"}},
            "request": {"type": kind, "timestamp": datetime.now(timezone.utc).isoformat()}}
    if intent:
        body["request"]["intent"] = {"name": intent, "slots": {k: {"name": k, "value": v} for k, v in (slots or {}).items()}}
    return body


def test_skill_end_to_end(tmp_path, monkeypatch):
    c, main = make_client(tmp_path, monkeypatch, ALEXA_SKILL_ID=SKILL, ALEXA_VERIFY="false")
    say = lambda r: r.json()["response"]["outputSpeech"]["text"]  # noqa: E731
    with c:
        assert "add" in say(c.post("/alexa", json=req(kind="LaunchRequest")))
        assert c.post("/alexa", json=req("NextIntent", skill="amzn1.ask.skill.other")).status_code == 400
        assert say(c.post("/alexa", json=req("CaptureIntent", {"text": "take the bins out tomorrow"}))) == "Added take the bins out."
        assert say(c.post("/alexa", json=req("NextIntent"))).startswith("Next up: take the bins out.")
        r = say(c.post("/alexa", json=req("DoneIntent", {"task": "the bins"})))
        assert r.startswith("Ticked off take the bins out. Plus")
        assert "couldn't find" in say(c.post("/alexa", json=req("DoneIntent", {"task": "fly to mars"})))
        c.patch("/api/prefs", json={"nudges": [{"id": "meds", "kind": "meds", "label": "Meds", "enabled": True, "times": ["08:00"]}]})
        assert "meds taken" in say(c.post("/alexa", json=req("MedsIntent")))
        assert c.get("/api/today-checkins").json()[0]["kind"] == "meds"
        assert say(c.post("/alexa", json=req("FocusIntent", {"minutes": "15"}))).startswith("Starting 15 minutes")
        assert c.get("/api/sessions/active").json()["session"]["minutes"] == 15
        assert "Nothing needs you" in say(c.post("/alexa", json=req("NextIntent")))
        assert say(c.post("/alexa", json=req("AMAZON.StopIntent"))) == "Okay."
        assert c.post("/alexa", content=b"not json", headers={"Content-Type": "application/json"}).status_code == 400


def test_skill_disabled_and_verification_on(tmp_path, monkeypatch):
    c, _ = make_client(tmp_path, monkeypatch, ALEXA_SKILL_ID="")
    with c:
        assert c.post("/alexa", json=req("NextIntent")).status_code == 404
    c, _ = make_client(tmp_path, monkeypatch, ALEXA_SKILL_ID=SKILL, ALEXA_VERIFY="true")
    with c:  # no signature headers → rejected
        assert c.post("/alexa", json=req("NextIntent")).status_code == 400
