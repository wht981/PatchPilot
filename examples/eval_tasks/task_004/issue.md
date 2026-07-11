# Bug: clamp_percent caps percentages at 99 instead of 100

The `clamp_percent(value)` function should cap percentage values at 100,
but values like 150 currently come back as 99. Please fix the cap and
verify with tests.
