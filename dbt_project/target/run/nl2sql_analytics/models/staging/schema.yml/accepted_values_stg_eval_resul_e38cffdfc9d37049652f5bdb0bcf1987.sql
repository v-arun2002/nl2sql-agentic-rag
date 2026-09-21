
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

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



  
  
      
    ) dbt_internal_test