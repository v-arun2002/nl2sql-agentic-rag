
    
    

select
    result_id as unique_field,
    count(*) as n_records

from NL2SQL_ANALYTICS.STAGING.stg_eval_results
where result_id is not null
group by result_id
having count(*) > 1


