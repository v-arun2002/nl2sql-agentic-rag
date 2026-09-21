
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    

with all_values as (

    select
        source as value_field,
        count(*) as n_records

    from NL2SQL_ANALYTICS.STAGING.stg_query_history
    group by source

)

select *
from all_values
where value_field not in (
    'api','mcp','streamlit_ui'
)



  
  
      
    ) dbt_internal_test