
    
    

with all_values as (

    select
        difficulty as value_field,
        count(*) as n_records

    from NL2SQL_ANALYTICS.STAGING.stg_eval_results
    group by difficulty

)

select *
from all_values
where value_field not in (
    'simple','moderate','challenging'
)


