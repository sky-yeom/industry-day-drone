# Production image for the Python relay. Needs the repo root (data/,
# public/monitors/) alongside relay/ since camera.py and survey.py read
# those paths relative to the repo root, not just relay/.
FROM python:3.11.9-slim
WORKDIR /app

COPY relay/requirements.lock.txt ./relay/requirements.lock.txt
RUN pip install --no-cache-dir -r relay/requirements.lock.txt

COPY relay ./relay
COPY data ./data
COPY public/monitors ./public/monitors

ENV RELAY_HOST=0.0.0.0
ENV RELAY_PORT=8080
EXPOSE 8080

CMD ["python", "-m", "relay.server"]
