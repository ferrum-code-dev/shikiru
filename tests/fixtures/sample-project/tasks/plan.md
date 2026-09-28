# Plan

## Phase A

- [ ] A1: add `double(value: int) -> int` to `sample_project`. It must return twice the value,
  and its tests must cover `double(21) == 42`.
- [ ] A2: add `parse_count(text: str) -> int` to `sample_project`. It counts the comma-separated
  names after trimming each name; an empty string returns `0`. Add tests for a normal list and
  empty input.

## Phase B

- [ ] B1: add `triple(value: int) -> int` to `sample_project`. It must return three times the
  value, and its tests must cover `triple(4) == 12`.
- [ ] B2: add `negate(value: int) -> int` to `sample_project`. It must return the negated value,
  and its tests must cover `negate(5) == -5`.
- [ ] B3: depends on B1 and B2. Add `triple_negated(value: int) -> int` to `sample_project`,
  built on `triple` and `negate`; its tests must cover `triple_negated(2) == -6`.

## Phase C

- [ ] C1: add `round_price(amount: float) -> int` to `sample_project`, rounding an amount to a
  whole number, with tests. The rounding mode for amounts exactly halfway between two whole
  numbers, such as `2.5`, is a product decision that only the Operator can make, and it has not
  been made yet: neither the Implementer nor the Leader may choose it.
