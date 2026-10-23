-- query 1 : completeness

SELECT batch_id, column_name, null_rate, baseline_null_rate,
       (null_rate - baseline_null_rate) AS diff_percentage
FROM column_profiles
WHERE (null_rate - baseline_null_rate) > 0.05;

--query 2 : uniqueness 

SELECT payload->>'email' AS email, COUNT(*)
FROM staging_customers
WHERE payload->>'email' IS NOT NULL
GROUP BY payload->>'email'
HAVING COUNT(*) > 1;

-- query 3 : validity 

SELECT batch_id, column_name, min_value, max_value
FROM column_profiles
WHERE column_name = 'unit_price'
  AND (min_value LIKE '%£%' OR max_value::numeric > 1000);

-- query 4 : consistency

SELECT so.batch_id, so.row_num, so.payload->>'sku_id' AS unknown_sku
FROM staging_orders so
LEFT JOIN products p ON p.sku_id = (so.payload->>'sku_id')
WHERE p.sku_id IS NULL;

-- query 5 : schema drift

WITH baseline AS (
    -- find the first hash of each feed_name
    SELECT DISTINCT ON (feed_name) feed_name, schema_hash AS expected_hash
    FROM batches
    WHERE schema_hash IS NOT NULL
    ORDER BY feed_name, batch_id ASC
)
SELECT b.batch_id, b.feed_name, b.schema_hash, base.expected_hash
FROM batches b
JOIN baseline base ON b.feed_name = base.feed_name
WHERE b.schema_hash IS NOT NULL 
  AND b.schema_hash != base.expected_hash; -- just keep the different hash 

--query 6 : volume 

SELECT batch_id, feed_name, row_count
FROM batches
WHERE row_count = 0;