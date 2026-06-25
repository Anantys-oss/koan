---
name: report
scope: core
group: status
emoji: 📊
description: Weekly/monthly PR activity report (per-project + global)
version: 1.0.0
audience: bridge
commands:
  - name: report
    description: PR activity report for a time window
    usage: /report --week | /report --month
    aliases: [weekly_report, monthly_report]
handler: handler.py
worker: true
---
