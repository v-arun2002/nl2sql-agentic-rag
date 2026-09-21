{#
    Override dbt's default schema naming.

    dbt's built-in generate_schema_name CONCATENATES: a model with
    `+schema: staging` under a profile whose schema is RAW lands in
    RAW_STAGING. That default exists so several developers can share one
    warehouse without overwriting each other, but here it would scatter models
    across RAW_STAGING / RAW_MARTS -- names that read as if they were raw data,
    next to the actual RAW schema holding the source tables.

    This version returns the configured schema verbatim instead, so
    `+schema: staging` means exactly STAGING and `+schema: marts` means exactly
    MARTS. Models with no `+schema:` still fall back to the profile's schema.

    Upper-cased because the DDL in snowflake_sql/schema.sql created everything
    unquoted, which Snowflake folds to upper case; emitting lower case here
    would produce a quoted, case-sensitive schema that does not match.

    TRADE-OFF: giving up the default also gives up its collision safety. Two
    people running `dbt run` against this profile now write to the same STAGING
    and MARTS schemas. If this ever grows past one operator, reintroduce an
    environment prefix here (e.g. keyed off target.name) rather than reverting
    to the concatenating default.
#}

{% macro generate_schema_name(custom_schema_name, node) -%}

    {%- set default_schema = target.schema -%}

    {%- if custom_schema_name is none -%}

        {{ default_schema }}

    {%- else -%}

        {{ custom_schema_name | trim | upper }}

    {%- endif -%}

{%- endmacro %}
