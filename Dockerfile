FROM python:3.7-slim-buster

WORKDIR /usr/src/app

RUN pip install --no-cache-dir kubernetes \
                               proxmoxer \
                               urllib3
COPY main.py .
COPY autoscaler ./autoscaler

CMD [ "python", "./main.py" ]