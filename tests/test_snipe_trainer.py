import sqlite3, json, os, pytest
from pathlib import Path


def _make_test_db(path: str):
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute("""CREATE TABLE trades (
        id INTEGER PRIMARY KEY, wallet TEXT, wallet_label TEXT, tx_sig TEXT,
        ts INTEGER, token_mint TEXT, token_symbol TEXT, program TEXT,
        sol_spent REAL, price_usd REAL, mcap_usd REAL, liquidity_usd REAL,
        token_age_min REAL, pair_created_at INTEGER, price_change_5m REAL,
        price_change_1h REAL, buys_5m INTEGER, sells_5m INTEGER,
        sell_ts INTEGER, hold_min REAL, pnl_pct REAL, outcome TEXT
    )""")
    rows = [
        # 3 wins en ventana 1-3 min, mcap 30-70k, buys 50-150
        (1,"w1","Theo","s1",1,"m1","A","Pump.fun",0.1,1e-6,50000,0,2.0,1,0,0,80,10,2,5.0,120.0,"WIN"),
        (2,"w1","Theo","s2",2,"m2","B","Pump.fun",0.1,1e-6,50000,0,1.5,2,0,0,60,8, 3,4.0, 80.0,"WIN"),
        (3,"w1","Theo","s3",3,"m3","C","Pump.fun",0.1,1e-6,40000,0,2.5,3,0,0,100,15,4,3.0, 50.0,"WIN"),
        # 1 loss en 30+ min, mcap bajo, pocos buys
        (4,"w2","Decu","s4",4,"m4","D","Pump.fun",0.1,1e-6,10000,0,35.0,4,0,0,5,2, 5,60.0,-30.0,"LOSS"),
        # 1 UNKNOWN — debe ser ignorado
        (5,"w2","Decu","s5",5,"m5","E","Pump.fun",0.1,1e-6,50000,0,2.0, 5,0,0,80,10,0,0.0,  0.0,"UNKNOWN"),
    ]
    c.executemany(
        "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def db_and_out(tmp_path):
    db = str(tmp_path / "test.db")
    out = str(tmp_path / "patterns.json")
    _make_test_db(db)
    os.environ["DB_PATH"] = db
    os.environ["PATTERNS_PATH"] = out
    yield db, out
    os.environ.pop("DB_PATH", None)
    os.environ.pop("PATTERNS_PATH", None)


def _get_trainer():
    import importlib
    import data_collector.snipe_trainer as m
    importlib.reload(m)
    return m


def test_only_win_loss_counted(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    assert p["total_trades"] == 4  # excluye UNKNOWN


def test_age_bucket_1_3min(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    b = next(x for x in p["age_buckets"] if x["label"] == "1-3min")
    assert b["n"] == 3
    assert b["wr"] == 100.0
    assert b["score_pts"] >= 35  # máximo para WR >= 90%


def test_age_bucket_30plus_low_pts(db_and_out):
    trainer = _get_trainer()
    p = trainer.compute_patterns()
    b = next(x for x in p["age_buckets"] if x["label"] == "30+min")
    assert b["n"] == 1
    assert b["wr"] == 0.0
    assert b["score_pts"] <= 15


def test_output_file_written(db_and_out):
    _, out = db_and_out
    trainer = _get_trainer()
    trainer.main()
    assert Path(out).exists()
    with open(out) as f:
        data = json.load(f)
    assert "age_buckets" in data
    assert "mcap_buckets" in data
    assert "buys_buckets" in data
    assert data["buy_threshold"] == 55
