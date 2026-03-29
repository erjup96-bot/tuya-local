#!/usr/bin/with-contenv bashio

echo "Witaj w moim własnym dodatku Home Assistant!"

# Pętla utrzymująca kontener przy życiu
while true; do
    echo "Dodatek działa... $(date)"
    sleep 60
done
