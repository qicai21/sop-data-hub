-- 工单 2026-06-29 §3a/§item2:九三集装箱池 snapshot 迁项目级 key + 6/29 途重更正
-- 可回滚:执行前已备份 data/backups/sop_agent.db.bak-*-cyclefix。幂等(可重复执行)。
BEGIN;
-- §3a: ship_name '和谐1' -> '九三大豆'(项目级 key),id 随 (project|ship|date) 重算
UPDATE container_pool_snapshot SET id='663c49516c600202630d13a9d8f4e1e5fb3b584f', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-15';
UPDATE container_pool_snapshot SET id='2404299d2573331d4c37296b0889c0bf4d99bae1', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-16';
UPDATE container_pool_snapshot SET id='3189668a3b676874d7194ce49b7ff49f16b8757e', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-17';
UPDATE container_pool_snapshot SET id='85f1026572ee249d50ac6bc3a15e3a913f1b896c', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-18';
UPDATE container_pool_snapshot SET id='04c8078e8028dfea8cbda185f6d61ed859054dc6', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-19';
UPDATE container_pool_snapshot SET id='b4c1dcebdfa4ba0d440b14b665b5fd28a16f2898', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-20';
UPDATE container_pool_snapshot SET id='25efa8e167c943163eeeee56eafe016e1410cc05', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-21';
UPDATE container_pool_snapshot SET id='03b103c65f8cee2197377b6debadb025c10835e4', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-22';
UPDATE container_pool_snapshot SET id='6ac805c77e0d88c95f2434a1ac8a07a38cf571b0', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-23';
UPDATE container_pool_snapshot SET id='de70af371543f216c0aff75929aa021e72ea24cc', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-24';
UPDATE container_pool_snapshot SET id='23aab12167b939d168fcbaf859e6dd490f969487', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-25';
UPDATE container_pool_snapshot SET id='bcaf2c6bd645ce9aa9455e34a224151d19dd0bb0', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-26';
UPDATE container_pool_snapshot SET id='ed29d51a36f8c90c94e6c522235bee995cfd5e93', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-27';
UPDATE container_pool_snapshot SET id='f3f8069cf6c4328c0c8fe1aebbe6d6d48ea49d3d', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-28';
UPDATE container_pool_snapshot SET id='ad2fc28134b53439ee925d7f4fd62b6572d9cc08', ship_name='九三大豆' WHERE project='jiusan' AND ship_name='和谐1' AND snapshot_date='2026-06-29';
-- §item2: 6/29 途重(在途去程重箱)0->80,ground330_empty 平池 259->179,池总 630 不变
UPDATE container_pool_snapshot SET transit_loaded=80, ground330_empty=179, total_loaded=334, total_empty=296, total_pool=630, updated_at='2026-06-29T13:30:00+08:00', note='auto从GROUP093晨报截止29日:返空取95306返空列、330空反推平物理池(unique箱630);昨装163发180。港空/330若按流量口径精修请人工--record覆盖。[2026-06-29人工更正 工单§item2]transit_loaded 0→80(晨报"在途40节重箱",旧正则漏配致0);ground330_empty 259→179 平池(total_pool 630 不变)。' WHERE project='jiusan' AND ship_name='九三大豆' AND snapshot_date='2026-06-29';
COMMIT;
