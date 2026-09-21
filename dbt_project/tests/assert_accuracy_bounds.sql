-- Singular test: fails if it returns ANY rows.
--
-- Guards the arithmetic in the three accuracy marts. The generic tests in
-- models/staging/schema.yml are all structural -- keys, nullity, referential
-- integrity -- and every one of them would still pass if the accuracy
-- calculation itself were wrong. This closes that gap.
--
-- Two impossible conditions, each catching a different class of bug:
--   correct_count > total_questions
--     count_if(correct) can never exceed count(*) over the same group, so this
--     firing means the grouping changed underneath one of the aggregates --
--     e.g. a join that fanned out rows, or a group-by key dropped from one
--     expression but not the other.
--   accuracy < 0 OR accuracy > 1
--     catches a division inverted, a numerator and denominator swapped, or a
--     future rewrite that divides by a filtered subtotal instead of count(*).
--
-- Both are cheap: three grouped scans over tables of a few rows each. The
-- value is that they run on every `dbt build`, so a broken mart fails the
-- pipeline instead of quietly publishing a plausible-looking number.

SELECT 'mart_accuracy_by_database' AS source_mart, config_label,
       correct_count, total_questions, accuracy
FROM {{ ref('mart_accuracy_by_database') }}
WHERE correct_count > total_questions OR accuracy < 0 OR accuracy > 1

UNION ALL

SELECT 'mart_accuracy_by_difficulty' AS source_mart, config_label,
       correct_count, total_questions, accuracy
FROM {{ ref('mart_accuracy_by_difficulty') }}
WHERE correct_count > total_questions OR accuracy < 0 OR accuracy > 1

UNION ALL

SELECT 'mart_accuracy_over_time' AS source_mart, config_label,
       correct_count, total_questions, accuracy
FROM {{ ref('mart_accuracy_over_time') }}
WHERE correct_count > total_questions OR accuracy < 0 OR accuracy > 1
