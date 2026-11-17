from shikiru.agents.contracts import TokenCounts, Usage
from shikiru.domain.model_pricing import model_price, priced_usage, token_cost


def test_token_prices_and_long_prompt_override() -> None:
    prices = {
        "prompt": "1",
        "completion": "2",
        "input_cache_read": "3",
        "input_cache_write": "4",
        "overrides": [
            {
                "min_prompt_tokens": 10,
                "prompt": "5",
                "completion": "6",
                "input_cache_read": "7",
                "input_cache_write": "8",
            }
        ],
    }
    assert token_cost(prices, TokenCounts(4, 2, 3, 2)) == 4 + 4 + 9 + 8
    assert token_cost(prices, TokenCounts(5, 2, 3, 2)) == 25 + 12 + 21 + 16
    del prices["input_cache_write"]
    assert token_cost(prices, TokenCounts(4, 2, 3, 0)) is None


def test_model_match_uses_only_the_approved_retry() -> None:
    models = {
        "meta/muse-wonder-1.0": {"pricing": {"prompt": "1"}},
        "meta/muse-wonder": {"pricing": {"prompt": "2"}},
        "meta/muse-spark-2": {"pricing": {"prompt": "3"}},
        "openai/gpt-test": {"pricing": {"prompt": "4"}},
    }
    assert model_price(models, "meta", "muse-wonder-1.0") == {"prompt": "1"}
    del models["meta/muse-wonder-1.0"]
    assert model_price(models, "meta", "muse-wonder-1.0") == {"prompt": "2"}
    assert model_price(models, "meta", "muse-spark-2.0") == {"prompt": "3"}
    assert model_price(models, "meta", "muse-spark-2.1") is None
    assert model_price(models, "openai", "gpt-test-1.0") is None
    assert model_price(models, "openai", "gpt-test") == {"prompt": "4"}


def test_pricing_only_new_calls_preserves_prior_cost() -> None:
    prices = {
        "openai/test": {
            "pricing": {
                "prompt": "1",
                "completion": "2",
                "input_cache_read": "3",
                "input_cache_write": "4",
                "overrides": [{"min_prompt_tokens": 10, "prompt": "5"}],
            }
        }
    }
    previous = Usage(10, 1, 0, 0, 7.0, "api_equivalent", None)
    current = Usage(25, 3, 0, 0, None, "unknown", None)
    calls = [("test", TokenCounts(5, 1, 0, 0)), ("test", TokenCounts(10, 1, 0, 0))]
    assert priced_usage(previous, current, prices, "openai", calls).cost_usd == 66.0
    unknown_previous = Usage(10, 1, 0, 0, None, "unknown", None)
    assert priced_usage(unknown_previous, current, prices, "openai", calls).cost_usd is None
    assert priced_usage(previous, current, prices, "openai", calls[:1]).cost_usd is None
