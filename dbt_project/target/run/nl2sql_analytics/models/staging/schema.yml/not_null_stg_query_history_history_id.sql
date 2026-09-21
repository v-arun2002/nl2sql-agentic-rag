
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select history_id
from NL2SQL_ANALYTICS.STAGING.stg_query_history
where history_id is null



  
  
      
    ) dbt_internal_test