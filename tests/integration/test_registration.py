from fastapi.testclient import TestClient
from app.main import create_app

def client(tmp_path):
    return TestClient(create_app(data_dir=tmp_path, test_mode=True),
                      client=("127.0.0.1", 12345))

def body(name="owner"):
    return dict(username=name, password="test-password", password_confirm="test-password")

def test_bootstrap_and_authenticated_additional(tmp_path):
    c=client(tmp_path)
    assert "在这里创建账号" in c.get("/login").text
    assert "显示密码" in c.get("/register").text
    r=c.post("/api/v1/auth/register",json=body(),headers={"Origin":"http://testserver"})
    assert r.status_code==200
    csrf=r.json()["data"]["csrf"]
    owner=c.get("/api/v1/auth/me").json()
    assert c.post("/api/v1/auth/register",json=body("second"),headers={"Origin":"http://testserver"}).status_code==403
    assert c.post("/api/v1/auth/register",json=body("second"),headers={"Origin":"http://testserver","X-CSRF-Token":csrf}).status_code==200
    assert c.get("/api/v1/auth/me").json()==owner
    c.cookies.clear()
    assert c.get("/register",follow_redirects=False).status_code==307
    assert c.post("/api/v1/auth/register",json=body("third"),headers={"Origin":"http://testserver"}).status_code==409

def test_registration_rejects_cross_origin_remote_and_bad_input(tmp_path):
    c=client(tmp_path)
    for origin in ["https://evil.invalid", "null", ""]:
        assert c.post("/api/v1/auth/register",json=body(),headers={"Origin":origin}).status_code==403
    b=body();b["password_confirm"]="different"
    assert c.post("/api/v1/auth/register",json=b,headers={"Origin":"http://testserver"}).status_code==400
    b=body();b["password"]=123
    r=c.post("/api/v1/auth/register",json=b,headers={"Origin":"http://testserver"})
    assert r.status_code==422 and "test-password" not in r.text
    remote=TestClient(c.app,client=("192.0.2.1",12345))
    assert remote.post("/api/v1/auth/register",json=body(),headers={"Origin":"http://testserver"}).status_code==403

def test_first_owner_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.auth import create_owner
    from app.db import connect
    c=client(tmp_path)
    def create(n):
        try:
            create_owner(tmp_path/"app.db",f"user{n}","test-password",first_only=True)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(create,range(2)))==1
    conn=connect(tmp_path/"app.db")
    assert conn.execute("select count(*) from owners").fetchone()[0]==1
    conn.close()
