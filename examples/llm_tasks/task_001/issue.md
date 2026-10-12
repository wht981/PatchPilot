# Normalize names and format greetings consistently

`normalize_name` should trim and collapse all whitespace and use Unicode
case folding. `greet` should return `Hello, <normalized name>!`.
Both modules currently return incorrect results. Preserve the public functions.
