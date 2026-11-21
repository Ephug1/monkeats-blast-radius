FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS profile
COPY fetch_source.py /tmp/fetch_source.py
RUN python /tmp/fetch_source.py
WORKDIR /profile
COPY profile-requirements.txt measure.py inventory.json map.json.gz ./
ENV PYTHONDONTWRITEBYTECODE=1 SKIP_INTEGRATION=0 OPENBLAS_NUM_THREADS=1
RUN if timeout 75 python -m pip install --only-binary=:all: --no-cache-dir -r profile-requirements.txt && timeout 300 python measure.py /src inventory.json /profile/linux.json.gz; then mv linux.json.gz map.json.gz; fi

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
COPY --from=profile /src/sqlglot /src/sqlglot
COPY --from=profile /src/tests /src/tests
COPY --from=profile /profile/map.json.gz /app/map.json.gz
COPY selector.py scheduler.py structure.py fixtures.json /app/
RUN python -m compileall -q /src/sqlglot /src/tests
CMD ["python", "-B", "/app/selector.py"]
