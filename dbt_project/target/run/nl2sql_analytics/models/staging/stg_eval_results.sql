
  create or replace   view NL2SQL_ANALYTICS.STAGING.stg_eval_results
  
  
  
  
  as (
    -- Thin pass-through over RAW.EVAL_RESULTS. One row per benchmark question.
--
-- Note `correct` is a real boolean here because eval/load_to_snowflake.py
-- converts the CSV's "True"/"False" text before loading. Downstream models can
-- therefore aggregate it directly rather than comparing against strings.

with source as (

    select * from NL2SQL_ANALYTICS.RAW.eval_results

),

casted as (

    select
        cast(result_id            as number)       as result_id,
        cast(run_id               as varchar(36))  as run_id,
        cast(question_id          as number)       as question_id,
        cast(db_id                as varchar(50))  as db_id,

        cast(question             as varchar)      as question,
        cast(evidence             as varchar)      as evidence,
        cast(gold_sql             as varchar)      as gold_sql,
        cast(predicted_sql        as varchar)      as predicted_sql,

        cast(correct              as boolean)      as correct,
        cast(difficulty           as varchar(20))  as difficulty,
        cast(retries              as number)       as retries,

        cast(error_classes_hit    as varchar)      as error_classes_hit,
        cast(fatal_error          as varchar)      as fatal_error,
        cast(schema_context_chars as number)       as schema_context_chars,
        cast(seconds              as float)        as seconds

    from source

)

select * from casted
  );

