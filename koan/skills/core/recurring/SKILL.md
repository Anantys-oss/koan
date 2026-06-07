---
name: recurring
scope: core
group: missions
emoji: 🔁
description: Manage recurring missions (hourly, daily, weekly, custom interval)
version: 1.5.0
audience: bridge
chat_confirmable: true
commands:
  - name: daily
    description: Add a daily recurring mission
    usage: /daily [HH:MM] <text> [project:<name>]
  - name: hourly
    description: Add an hourly recurring mission
    usage: /hourly <text> [project:<name>]
  - name: weekly
    description: Add a weekly recurring mission
    usage: /weekly [HH:MM] <text> [project:<name>]
  - name: every
    description: Add a custom-interval recurring mission
    usage: /every <interval> <text> [project:<name>]
  - name: recurring
    description: List recurring missions, or manage with resume/run/pause/cancel/days sub-commands
    usage: /recurring, /recurring resume <n>, /recurring run [n], /recurring pause <n>, /recurring cancel <n>, /recurring days <n> <days>
handler: handler.py
---
