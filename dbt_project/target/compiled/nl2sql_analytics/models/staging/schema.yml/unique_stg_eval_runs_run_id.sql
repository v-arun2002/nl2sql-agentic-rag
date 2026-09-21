
    
    

select
    run_id as unique_field,
    count(*) as n_records

from NL2SQL_ANALYTICS.STAGING.stg_eval_runs
where run_id is not null
group by run_id
having count(*) > 1


