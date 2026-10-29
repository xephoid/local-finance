FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 finance \
    && useradd --uid 10001 --gid 10001 --no-create-home finance \
    && install -d -m 0700 -o 10001 -g 10001 /data
WORKDIR /app
COPY finance.py assistant_features.py deployment.py notify.py supervise.py provision.py ./
COPY static/materialize.min.css static/finance.css static/LICENSE.materialize ./static/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FINANCE_DATA_DIR=/data FINANCE_BIND=0.0.0.0 FINANCE_PORT=8787
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8787
ENTRYPOINT ["python3", "finance.py"]
CMD ["serve"]
