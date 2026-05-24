PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE ws_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE positions (
    client_order_id TEXT PRIMARY KEY,
    arb_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    market_key TEXT NOT NULL,
    side TEXT NOT NULL,
    intended_size REAL NOT NULL,
    filled_size REAL NOT NULL,
    avg_price REAL NOT NULL,
    status TEXT NOT NULL,
    order_ids_json TEXT NOT NULL,
    oracle_source TEXT NOT NULL,
    resolution_time TEXT,
    bridge_in_flight INTEGER NOT NULL,
    directional_unhedged INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arb_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    venue TEXT NOT NULL,
    market_key TEXT NOT NULL,
    pnl_usd REAL NOT NULL,
    closed INTEGER NOT NULL DEFAULT 0,
    profitable INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    venue TEXT NOT NULL,
    drift_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
INSERT INTO reconciliations VALUES(1,'all',0,'{"dry_run": true}','2026-05-23T00:52:47.881334+00:00');
INSERT INTO reconciliations VALUES(2,'all',0,'{"dry_run": true}','2026-05-23T11:16:20.754119+00:00');
INSERT INTO reconciliations VALUES(3,'all',0,'{"dry_run": true}','2026-05-23T11:16:23.417387+00:00');
INSERT INTO reconciliations VALUES(4,'all',0,'{"dry_run": true}','2026-05-23T11:16:25.636875+00:00');
INSERT INTO reconciliations VALUES(5,'all',0,'{"dry_run": true}','2026-05-23T11:16:28.158439+00:00');
INSERT INTO reconciliations VALUES(6,'all',0,'{"dry_run": true}','2026-05-23T11:16:31.247405+00:00');
INSERT INTO reconciliations VALUES(7,'all',0,'{"dry_run": true}','2026-05-23T11:16:35.107183+00:00');
INSERT INTO reconciliations VALUES(8,'all',0,'{"dry_run": true}','2026-05-23T11:16:40.377932+00:00');
INSERT INTO reconciliations VALUES(9,'all',0,'{"dry_run": true}','2026-05-23T11:16:48.789355+00:00');
INSERT INTO reconciliations VALUES(10,'all',0,'{"dry_run": true}','2026-05-23T11:17:03.793227+00:00');
INSERT INTO reconciliations VALUES(11,'all',0,'{"dry_run": true}','2026-05-23T11:17:31.416418+00:00');
INSERT INTO reconciliations VALUES(12,'all',0,'{"dry_run": true}','2026-05-23T11:18:24.840197+00:00');
INSERT INTO reconciliations VALUES(13,'all',0,'{"dry_run": true}','2026-05-23T11:19:27.266303+00:00');
INSERT INTO reconciliations VALUES(14,'all',0,'{"dry_run": true}','2026-05-23T11:19:35.488199+00:00');
INSERT INTO reconciliations VALUES(15,'all',0,'{"dry_run": true}','2026-05-23T11:19:43.975975+00:00');
INSERT INTO reconciliations VALUES(16,'all',0,'{"dry_run": true}','2026-05-23T11:19:52.660420+00:00');
INSERT INTO reconciliations VALUES(17,'all',0,'{"dry_run": true}','2026-05-23T11:20:01.525930+00:00');
INSERT INTO reconciliations VALUES(18,'all',0,'{"dry_run": true}','2026-05-23T11:20:10.568622+00:00');
INSERT INTO reconciliations VALUES(19,'all',0,'{"dry_run": true}','2026-05-23T11:20:20.422500+00:00');
INSERT INTO reconciliations VALUES(20,'all',0,'{"dry_run": true}','2026-05-23T11:20:32.678455+00:00');
INSERT INTO reconciliations VALUES(21,'all',0,'{"dry_run": true}','2026-05-23T11:20:47.846333+00:00');
INSERT INTO reconciliations VALUES(22,'all',0,'{"dry_run": true}','2026-05-23T11:21:09.170332+00:00');
INSERT INTO reconciliations VALUES(23,'all',0,'{"dry_run": true}','2026-05-23T11:21:43.170058+00:00');
INSERT INTO reconciliations VALUES(24,'all',0,'{"dry_run": true}','2026-05-23T11:22:42.930347+00:00');
INSERT INTO reconciliations VALUES(25,'all',0,'{"dry_run": true}','2026-05-23T11:23:51.719986+00:00');
INSERT INTO reconciliations VALUES(26,'all',0,'{"dry_run": true}','2026-05-23T11:24:25.098986+00:00');
INSERT INTO reconciliations VALUES(27,'all',0,'{"dry_run": true}','2026-05-24T00:42:08.006245+00:00');
DELETE FROM sqlite_sequence;
INSERT INTO sqlite_sequence VALUES('reconciliations',27);
COMMIT;
