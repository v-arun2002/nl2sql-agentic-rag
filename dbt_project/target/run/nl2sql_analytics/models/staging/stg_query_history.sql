
  create or replace   view NL2SQL_ANALYTICS.STAGING.stg_query_history
  
  
  
  
  as (
    -- Thin pass-through over RAW.QUERY_HISTORY. One row per live agent invocation
-- logged by the MCP server.
--
-- Unlike the eval_* tables this is append-only telemetry with no run_id, so it
-- cannot be joined to a benchmark run. It answers "what is actually being
-- asked in production", not "how did configuration X score".

with source as (

    select * from NL2SQL_ANALYTICS.RAW.query_history

),

casted as (

    select
        cast(history_id    as number)        as history_id,
        cast(invoked_at    as timestamp_ntz) as invoked_at,
        cast(db_id         as varchar(50))   as db_id,
        cast(question      as varchar)       as question,
        cast(sql_generated as varchar)       as sql_generated,
        cast(success       as boolean)       as success,
        cast(retries       as number)        as retries,
        cast(source        as varchar(20))   as source

    from source

)

select * from casted
  );

