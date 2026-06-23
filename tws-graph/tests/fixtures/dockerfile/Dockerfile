FROM python:3.11-slim AS builder

COPY requirements.txt /app/
COPY src/ /app/src/

RUN pip install --no-cache-dir -r /app/requirements.txt

ENV APP_HOME=/app
ENV MODE=production DEBUG=false

EXPOSE 8080
EXPOSE 3000

VOLUME /data
VOLUME /var/log /var/tmp

CMD ["python", "-m", "app"]

ENTRYPOINT ["/bin/sh", "-c"]

ADD extra.tar.gz /tmp/

FROM nginx:alpine
COPY --from=builder /app/static /usr/share/nginx/html
EXPOSE 80
