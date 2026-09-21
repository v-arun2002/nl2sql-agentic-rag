
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select run_id
from NL2SQL_ANALYTICS.STAGING.stg_eval_results
where run_id is null



  
  
      
    ) dbt_internal_test