import io
import json
import pytest
from fastapi.testclient import TestClient
from app.auth import create_owner
from app.main import create_app
from app.db import connect
from app.services import table_imports as svc

@pytest.fixture
def setup(tmp_path):
    app = create_app(data_dir=tmp_path, test_mode=True)
    owner = create_owner(tmp_path/"app.db", "user", "password")
    c = TestClient(app)
    response = c.post("/api/v1/auth/login", json={"username":"user","password":"password"})
    c.headers["X-CSRF-Token"] = response.json()["data"]["csrf"]
    return c, tmp_path/"app.db", owner

def upload(c, kind, text, name="test.csv"):
    return c.post("/api/v1/table-imports/preview", data={"kind":kind}, files={"file":(name,text.encode("utf-8-sig"))})

def apply(c, response):
    data=response.json()["data"]; table=data["tables"][0]
    return c.post("/api/v1/table-imports/"+data["id"]+"/commit",json={"sheet":table["name"],"mapping":table["mapping"]})

def test_product_atomic_repeat_and_unknown_columns(setup):
    c,db,owner=setup
    r=upload(c,"products","商品标题,品类,备注\n测试商品,文具,仅用户提供\n")
    assert r.status_code==200
    result=apply(c,r);assert result.json()["data"]["imported"]==1
    assert apply(c,r).json()==result.json()
    r2=upload(c,"products","商品标题,品类,备注\n测试商品,文具,仅用户提供\n")
    assert apply(c,r2).json()["data"]["duplicates"]==1
    conn=connect(db)
    p=conn.execute("select * from products").fetchone()
    assert p["stage"]=="DISCOVERY"
    assert json.loads(p["facts_json"])["import_source"]["raw"]["备注"]=="仅用户提供"
    conn.close()

def test_invalid_batch_writes_nothing(setup):
    c,db,_=setup
    r=upload(c,"products","商品标题,品类\n好商品,文具\n缺品类,\n")
    report=apply(c,r).json()["data"]
    assert report["imported"]==0 and report["errors"][0]["row"]==3
    conn=connect(db);assert conn.execute("select count(*) from products").fetchone()[0]==0;conn.close()

def test_all_types_and_money(setup):
    c,db,owner=setup
    apply(c,upload(c,"products","商品标题,品类\n商品,文具\n"))
    conn=connect(db);pid=conn.execute("select id from products").fetchone()[0];conn.close()
    r=upload(c,"suppliers",f"商品ID,供应商,SKU,拿货价,运费\n{pid},供应商A,S1,12.30,2\n")
    assert apply(c,r).json()["data"]["imported"]==1
    assert apply(c,upload(c,"market","关键词,品类组,评分\n笔,文具,90\n")).json()["data"]["imported"]==1
    assert apply(c,upload(c,"orders","平台,订单号,实付金额\n店铺A,000123,99.99\n")).json()["data"]["imported"]==1
    assert apply(c,upload(c,"sales","平台,日期,销售额\n店铺A,2026-09-19,199.90\n")).json()["data"]["imported"]==1
    conn=connect(db)
    offer=conn.execute("select * from offers").fetchone()
    assert offer["procurement_minor"]==1230 and offer["verification_status"]=="UNVERIFIED"
    assert conn.execute("select disposition from opportunities").fetchone()[0]=="待核验"
    assert conn.execute("select count(*) from metric_snapshots").fetchone()[0]==0
    conn.close()
    records=c.get("/api/v1/table-imports/records/orders").json()["data"]
    assert json.loads(records[0]["normalized_json"])["order_id"]=="000123"

def test_auth_csrf_owner_isolation(setup):
    c,db,_=setup
    r=upload(c,"products","商品标题,品类\n商品,文具\n")
    token=c.headers.pop("X-CSRF-Token")
    assert upload(c,"products","a,b\n1,2").status_code==403
    c.headers["X-CSRF-Token"]=token
    create_owner(db,"other","password")
    result=c.post("/api/v1/auth/login",json={"username":"other","password":"password"})
    c.headers["X-CSRF-Token"]=result.json()["data"]["csrf"]
    assert apply(c,r).status_code==400
    assert c.get("/api/v1/table-imports").json()["data"]==[]
    assert c.get("/api/v1/table-imports/records/products").json()["data"]==[]
    c.cookies.clear()
    assert c.get("/api/v1/table-imports").status_code==401

@pytest.mark.parametrize("text",["a,a\n1,2","a,\n1,2","a,b\n1,2,3","a,b\n",""])
def test_bad_csv(text):
    with pytest.raises(ValueError): svc.parse("file.csv",text.encode())

def test_tsv_and_gbk():
    assert svc.parse("a.tsv","商品标题\t品类\n笔\t文具".encode("gb18030"))[0]["rows"][0]["values"]["商品标题"]=="笔"

@pytest.mark.parametrize("money",["NaN","Infinity","-1","0.001","abc"])
def test_invalid_money(setup,money):
    c,_,_=setup
    r=upload(c,"orders",f"平台,订单号,实付金额\na,1,{money}")
    assert apply(c,r).json()["data"]["errors"]

def test_excel_multiple_sheets_and_formula():
    openpyxl=pytest.importorskip("openpyxl")
    book=openpyxl.Workbook();book.active.append(["商品标题","品类"]);book.active.append(["笔","文具"])
    second=book.create_sheet("其他");second.append(["平台","订单号"]);second.append(["店铺","0001"])
    stream=io.BytesIO();book.save(stream)
    assert len(svc.parse("a.xlsx",stream.getvalue()))==2
    book.active["A2"]="=1+1";stream=io.BytesIO();book.save(stream)
    with pytest.raises(ValueError,match="公式"):svc.parse("a.xlsx",stream.getvalue())

def test_sales_identical_rows_preserved_and_order_conflicts_rollback(setup):
    c,db,_=setup
    # EC-04: 销售汇总按 平台+日期+商品名 定业务身份——完全相同的两行=同一业务记录，
    # 第二行计入 duplicates（不丢数据、不误报冲突），不再按"文件哈希+行号"拆成两条。
    r=upload(c,"sales","平台,日期,销售额\n店铺,2026-09-19,10\n店铺,2026-09-19,10\n".replace("\\\n","\n"))
    report=apply(c,r).json()["data"]
    assert report["imported"]==1 and report["duplicates"]==1
    first=upload(c,"orders","平台,订单号,实付金额\n店铺,0001,10")
    assert apply(c,first).json()["data"]["imported"]==1
    changed=upload(c,"orders","平台,订单号,实付金额\n店铺,0002,20\n店铺,0001,11")
    assert apply(c,changed).status_code==400
    conn=connect(db)
    assert conn.execute("select count(*) from imported_rows where kind='orders'").fetchone()[0]==1
    conn.close()


def test_exports_templates_and_help(setup):
    c,db,owner=setup
    for kind in svc.KINDS:
        r=c.get("/api/v1/table-imports/template/"+kind)
        assert r.status_code==200 and r.text.startswith("\ufeff")
        assert c.get("/api/v1/table-imports/export/"+kind).status_code==200
    assert c.get("/imports").status_code==200
    assert "在线填写模板" in c.get("/imports").text
    assert "使用教程" in c.get("/help").text
    assert apply(c,upload(c,"products","商品标题,品类\n=1+1,文具")).json()["data"]["imported"]==1
    r=c.get("/api/v1/table-imports/export/products")
    assert "'=1+1" in r.text and "商品ID" in r.text
    assert "password_hash" not in r.text
    c.cookies.clear()
    assert c.get("/api/v1/table-imports/export/products").status_code==401

def test_order_export_owner_boundary(setup):
    c,db,owner=setup
    apply(c,upload(c,"orders","平台,订单号,实付金额\n店铺,001,10"))
    assert "001" in c.get("/api/v1/table-imports/export/orders").text
    create_owner(db,"second","password")
    c.post("/api/v1/auth/login",json={"username":"second","password":"password"})
    assert "001" not in c.get("/api/v1/table-imports/export/orders").text


def test_excel_dimension_limit_and_upload(setup):
    from openpyxl import Workbook
    from zipfile import ZipFile
    import re
    book=Workbook();book.active.append(["商品标题","品类"]);book.active.append(["笔","文具"])
    source=io.BytesIO();book.save(source)
    c,db,owner=setup
    r=c.post("/api/v1/table-imports/preview",data={"kind":"products"},files={"file":("test.xlsx",source.getvalue())})
    assert r.status_code==200
    assert apply(c,r).json()["data"]["imported"]==1
    modified=io.BytesIO()
    with ZipFile(io.BytesIO(source.getvalue())) as z, ZipFile(modified,"w") as out:
        for name in z.namelist():
            data=z.read(name)
            if name=="xl/worksheets/sheet1.xml":
                data=re.sub(b'<dimension ref="[^"]+"',b'<dimension ref="A1:XFD1048576"',data)
            out.writestr(name,data)
    with pytest.raises(ValueError,match="最多"):svc.parse("big.xlsx",modified.getvalue())


def test_excel_undersized_dimension_keeps_all_rows():
    from openpyxl import Workbook
    from zipfile import ZipFile
    import re
    book=Workbook();book.active.append(["商品标题","品类"])
    book.active.append(["笔","文具"]);book.active.append(["纸","文具"])
    source=io.BytesIO();book.save(source);modified=io.BytesIO()
    with ZipFile(io.BytesIO(source.getvalue())) as z, ZipFile(modified,"w") as out:
        for name in z.namelist():
            data=z.read(name)
            if name=="xl/worksheets/sheet1.xml":
                data=re.sub(b'<dimension ref="[^"]+"',b'<dimension ref="A1:A2"',data)
            out.writestr(name,data)
    table=svc.parse("short.xlsx",modified.getvalue())[0]
    assert len(table["rows"])==2 and table["headers"]==["商品标题","品类"]
