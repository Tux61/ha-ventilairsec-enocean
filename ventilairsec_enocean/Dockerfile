ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.20
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pyserial py3-paho-mqtt

COPY run.sh vmi_bridge.py /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
