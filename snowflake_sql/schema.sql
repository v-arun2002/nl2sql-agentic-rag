USE ROLE NL2SQL_APP_ROLE;
USE DATABASE NL2SQL_ANALYTICS;
USE SCHEMA RAW;

CREATE TABLE IF NOT EXISTS EVAL_RUNS (
    run_id                       VARCHAR(36) PRIMARY KEY,
    run_timestamp                TIMESTAMP_NTZ NOT NULL,
    config_label                 VARCHAR(100),
    include_evidence_in_prompts  BOOLEAN,
    planner_model                VARCHAR(100),
    generator_model              VARCHAR(100),
    classifier_model             VARCHAR(100),
    total_questions               NUMBER
);

CREATE TABLE IF NOT EXISTS EVAL_RESULTS (
    result_id              NUMBER AUTOINCREMENT PRIMARY KEY,
    run_id                  VARCHAR(36) NOT NULL REFERENCES EVAL_RUNS(run_id),
    question_id             NUMBER,
    db_id                    VARCHAR(50),
    question                 VARCHAR,
    evidence                 VARCHAR,
    gold_sql                 VARCHAR,
    predicted_sql            VARCHAR,
    correct                  BOOLEAN,
    difficulty                VARCHAR(20),
    retries                   NUMBER,
    error_classes_hit         VARCHAR,
    fatal_error                VARCHAR,
    schema_context_chars       NUMBER,
    seconds                    FLOAT
);

CREATE TABLE IF NOT EXISTS QUERY_HISTORY (
    history_id      NUMBER AUTOINCREMENT PRIMARY KEY,
    invoked_at       TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP(),
    db_id             VARCHAR(50),
    question           VARCHAR,
    sql_generated       VARCHAR,
    success              BOOLEAN,
    retries               NUMBER,
    source                 VARCHAR(20)
);
