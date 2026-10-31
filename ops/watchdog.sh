#!/bin/bash
# 看门狗：连续 3 次连不上协作服务（或隧道健康页），就重启它。
# 用法（macOS）：配合 launchd 常驻；Linux 把 RESTART 换成 systemctl --user restart pro-lane。
PORT="${PORT:-8765}"
LABEL="${LABEL:-com.example.pro-lane}"
TUNNEL_HEALTH_URL="${TUNNEL_HEALTH_URL:-}"   # 可选：tunnel-client 的 /healthz 地址
RESTART="${RESTART:-launchctl kickstart -k gui/$(id -u)/$LABEL}"
fails=0
while true; do
  ok=1
  nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1 || ok=0
  if [ -n "$TUNNEL_HEALTH_URL" ]; then
    curl -fsS --max-time 5 "$TUNNEL_HEALTH_URL" >/dev/null 2>&1 || ok=0
  fi
  if [ "$ok" = 1 ]; then
    fails=0
  else
    fails=$((fails + 1))
    echo "$(date '+%F %T') 第 $fails 次不通"
    if [ "$fails" -ge 3 ]; then
      echo "$(date '+%F %T') 重启：$RESTART"
      $RESTART
      fails=0
      sleep 30
    fi
  fi
  sleep 60
done
