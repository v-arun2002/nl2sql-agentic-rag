# Airflow image with this project's dependencies baked in.
#
# BUILD CONTEXT IS THE PROJECT ROOT, NOT airflow/.
# See docker-compose.yaml: `build: {context: .., dockerfile: airflow/Dockerfile}`.
# That is what lets this COPY reach requirements.txt, which lives one level up
# from this Dockerfile. Building with `docker build airflow/` will fail.
FROM apache/airflow:3.3.1

# Root ONLY for system packages. Airflow's image refuses `pip install` as root
# (it warns and the resulting root-owned site-packages break the airflow user
# at runtime), so the switch back below is mandatory, not stylistic.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

USER airflow

# Created here, as the airflow user, so the named volume mounted over it in
# docker-compose.yaml inherits airflow ownership. A volume mounted at a path
# that does not exist in the image is created root-owned, which UID 50000
# cannot then write to.
RUN mkdir -p /home/airflow/.cache

COPY requirements.txt /tmp/requirements.txt

# No constraints file on purpose -- and this is the riskiest line here.
#
# Airflow ships a constraints file pinning its own dependency tree, and the
# documented way to extend the image is to install against it. That cannot work
# unmodified here: requirements.txt hard-pins openai==3.0.0 and caps
# mcp<2 / snowflake-connector-python<5, and any constraint that disagrees makes
# the resolve unsatisfiable rather than merely suboptimal.
#
# So this lets pip resolve freely, which means it MAY move versions Airflow
# itself depends on (protobuf and certifi already shifted when dbt-snowflake
# was added on the host). Verify Airflow still starts before trusting the
# image; `docker compose run --rm airflow-cli airflow version` is the cheap
# check.
RUN pip install --no-cache-dir -r /tmp/requirements.txt
