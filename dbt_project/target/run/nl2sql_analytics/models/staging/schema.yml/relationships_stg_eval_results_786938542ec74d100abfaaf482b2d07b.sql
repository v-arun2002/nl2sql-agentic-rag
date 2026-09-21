
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

with child as (
    select run_id as from_field
    from NL2SQL_ANALYTICS.STAGING.stg_eval_results
    where run_id is not null
),

parent as (
    select run_id as to_field
    from NL2SQL_ANALYTICS.STAGING.stg_eval_runs
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null



  
  
      
    ) dbt_internal_test