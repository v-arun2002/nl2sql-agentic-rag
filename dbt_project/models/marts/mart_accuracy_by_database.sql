-- Execution accuracy per database, split by run configuration.
--
-- Answers "which schemas does the agent struggle on, and did the config
-- change help?" -- the per-database table in the project README is this query.
--
-- Note accuracy here is EXECUTION accuracy: the predicted SQL returned the
-- same rows as the gold SQL. It does not mean the SQL is well-formed or that
-- it would generalise; see the README's "What this doesn't do".

with enriched as (

    select * from {{ ref('int_eval_results_enriched') }}

),

aggregated as (

    select
        db_id,
        config_label,

        count(*)                                  as total_questions,
        count_if(correct)                         as correct_count,
        -- Snowflake division yields a decimal (not integer truncation), so
        -- this is a true ratio. 4dp keeps 0.1667-style values exact enough to
        -- compare runs without pretending to more precision than 150-500
        -- questions supports.
        round(count_if(correct) / count(*), 4)    as accuracy

    from enriched

    group by db_id, config_label

)

select * from aggregated
order by db_id
