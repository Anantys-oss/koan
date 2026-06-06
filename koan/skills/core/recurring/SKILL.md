---
name: recurring
scope: core
group: missions
emoji: 🔁
description: Manage recurring missions (hourly, daily, weekly, custom interval)
version: 1.4.0
audience: bridge
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
    description: List all recurring missions, or manage with resume/run sub-commands
    usage: /recurring, /recurring resume <n>, /recurring run [n]
  - name: cancel_recurring
    description: Cancel a recurring mission
    usage: /cancel_recurring <n>, /cancel_recurring <keyword>
  - name: pause_recurring
    description: Disable a recurring mission without deleting it
    usage: /pause_recurring <n>, /pause_recurring <keyword>
  - name: days_recurring
    description: Set day-of-week filter (weekdays/weekends/specific days)
    usage: /days_recurring <n> weekdays, /days_recurring <n> mon,wed,fri, /days_recurring <n> all
handler: handler.py
---
