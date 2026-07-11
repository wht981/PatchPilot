# Bug: in_range accepts values outside the range

The `in_range(value, low, high)` function should return `True` only when
`value` lies between `low` and `high` inclusive. Currently it returns
`True` for values far above the upper bound. Please fix the logic and
verify with tests.
