# Override with a verified immutable digest for a reproducible local build.
ARG PYTHON_BASE=python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d
FROM ${PYTHON_BASE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /job
COPY ontologics/ingest.py ontologics/__init__.py /job/ontologics/
COPY sandbox_jobs/parser_job.py /job/parser_job.py
RUN mkdir /input && chmod 755 /job /job/ontologics /input
USER 65532:65532
ENTRYPOINT ["python", "-I", "/job/parser_job.py"]
