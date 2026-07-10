# Bug: is_adult rejects people who are exactly 18

The `is_adult(age)` function in the validator module should return `True`
for anyone aged 18 or older, but a person who is exactly 18 is currently
rejected. Please fix the boundary check and verify with tests.
