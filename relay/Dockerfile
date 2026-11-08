FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin relay  && mkdir /data && chown relay:relay /data
WORKDIR /app
COPY relay/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY LICENSE /app/LICENSE
COPY edmrn/__init__.py /app/edmrn/__init__.py
COPY edmrn/ext/__init__.py /app/edmrn/ext/__init__.py
COPY edmrn/ext/crew_events.py edmrn/ext/crew_identity.py edmrn/ext/crew_roster.py edmrn/ext/crew_protocol.py /app/edmrn/ext/
COPY relay/edmrn_relay /app/edmrn_relay
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1     EDMRN_RELAY_HOST=0.0.0.0 EDMRN_RELAY_DB=/data/relay.db
VOLUME /data
USER relay
EXPOSE 8765
CMD ["python", "-m", "edmrn_relay"]
