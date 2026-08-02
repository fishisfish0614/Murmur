#!/usr/bin/env bash
# Murmur quickstart — poke the engine by hand.
set -e
E=http://127.0.0.1:8020

echo "── current state ──"
curl -s $E/emotion/state | python3 -m json.tool

echo "── push a delta (they said something sweet) ──"
curl -s -X POST $E/emotion/update -H "Content-Type: application/json" \
  -d '{"source":"input","dimensions":{"warmth":0.1,"playfulness":0.15},"trigger":"they teased me and I liked it"}' \
  | python3 -m json.tool

echo "── a heart-skipping moment ──"
curl -s -X POST $E/emotion/update -H "Content-Type: application/json" \
  -d '{"source":"input","dimensions":{"warmth":0.2},"trigger":"they said the thing","moved":true}' >/dev/null

echo "── vitals (watch the moved spike, then run again in 2 minutes) ──"
curl -s $E/emotion/vitals | python3 -m json.tool

echo "── the record of moved moments ──"
curl -s $E/emotion/moved | python3 -m json.tool

echo "── force a tick (normally automatic every 5 min) ──"
curl -s -X POST $E/emotion/tick | python3 -m json.tool

echo "── graceful goodbye, carrying nothing ──"
curl -s -X POST $E/emotion/snapshot -H "Content-Type: application/json" \
  -d '{"end_type":"graceful","carry":[]}' >/dev/null && echo "snapshot written"
