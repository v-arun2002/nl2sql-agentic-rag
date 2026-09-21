
    
    

select
    history_id as unique_field,
    count(*) as n_records

from NL2SQL_ANALYTICS.STAGING.stg_query_history
where history_id is not null
group by history_id
having count(*) > 1


