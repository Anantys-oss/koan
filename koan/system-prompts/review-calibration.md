You are analyzing the outcome of code review findings to calibrate the review pipeline.

Below is a JSONL dataset of review findings and whether each was "addressed" (the flagged line was modified in the merge commit) or not addressed (the author merged without changing the flagged code).

## Outcomes data

{OUTCOMES_JSONL}

## Task

Analyze the outcomes and identify finding types (by title pattern and severity) that were addressed less than 30% of the time. These are likely false positives or low-value findings that the reflect pass should score lower.

Also identify finding types addressed more than 80% of the time — these are high-value findings the reflect pass should score higher.

## Output format

Respond with ONLY a markdown section like this:

### Review calibration ({DATE})

**Low-value findings (consider scoring lower in reflect pass):**
- "{finding title pattern}" ({severity}): addressed {N}/{total} times ({pct}%)

**High-value findings (consider scoring higher in reflect pass):**
- "{finding title pattern}" ({severity}): addressed {N}/{total} times ({pct}%)

Group similar titles together. Only include patterns with at least 3 data points.
If all findings have reasonable address rates (30-80%), say "No calibration adjustments needed at this time."
