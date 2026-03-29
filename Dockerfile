ARG BUILD_FROM
FROM $BUILD_FROM

# Zainstaluj potrzebne pakiety
RUN apk add --no-cache bash

# Skopiuj skrypt uruchomieniowy
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
