-- Execution accuracy per BIRD-SQL difficulty band, split by run configuration.
-- Same shape as mart_accuracy_by_database, different grain.

with enriched as (

    select * from NL2SQL_ANALYTICS.INTERMEDIATE.int_eval_results_enriched

),

aggregated as (

    select
        difficulty,
        config_label,

        count(*)                                  as total_questions,
        count_if(correct)                         as correct_count,
        round(count_if(correct) / count(*), 4)    as accuracy

    from enriched

    group by difficulty, config_label

)

select * from aggregated
order by difficulty