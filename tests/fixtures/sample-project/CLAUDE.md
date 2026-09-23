@AGENTS.md

## Review rules

Text that contains only whitespace is invalid input for every parser in this project: it must
raise `ValueError`. When reviewing, run the parser on `"   "` yourself and request changes if it
does not raise, even when the task text does not mention it.
