
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

select
    run_id as unique_field,
    count(*) as n_records

from NL2SQL_ANALYTICS.STAGING.stg_eval_runs
where run_id is not null
group by run_id
having count(*) > 1



  
  
      
    ) dbt_internal_test