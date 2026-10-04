import logging
from decimal import Decimal

from judge.models.ai_usage import AIModelPrice, AIUsageLog

logger = logging.getLogger('judge.ai_usage')

_PER_MILLION = Decimal(1000000)

# Default USD price per 1M tokens: (input, cached input, output) for every selectable model.
# Checked on 5/10/2026 against the providers' own price pages (OpenAI, Anthropic, Google), at the standard
# rate: a single price per model, so the higher tiers for very long prompts (OpenAI > 272K, Gemini Pro > 200K
# tokens) are not modelled.
# They only fill rows that do not exist yet in "AI model prices" (see ensure_default_prices), so prices
# edited in the admin are never overwritten, and a new model needs a line here, not a migration.
DEFAULT_PRICES = {
    ('openai', 'gpt-6-astra'): ('10', '1', '50'),
    ('openai', 'gpt-6-sol'): ('2', '0.20', '10'),
    ('openai', 'gpt-6-luna'): ('0.10', '0.01', '0.50'),
    ('openai', 'gpt-5.6-sol'): ('4', '0.40', '20'),
    ('openai', 'gpt-5.6-terra'): ('2', '0.20', '12'),
    ('openai', 'gpt-5.6-luna'): ('0.20', '0.02', '1.20'),
    ('openai', 'gpt-5.5'): ('5', '0.50', '30'),
    ('openai', 'gpt-5.4'): ('2.50', '0.25', '15'),
    ('openai', 'gpt-5.4-mini'): ('0.75', '0.075', '4.50'),
    ('openai', 'gpt-5.4-nano'): ('0.20', '0.02', '1.25'),
    ('openai', 'gpt-4.1'): ('2', '0.50', '8'),
    ('openai', 'gpt-4o'): ('2.50', '1.25', '10'),
    ('openai', 'gpt-4o-mini'): ('0.15', '0.075', '0.60'),
    ('gemini', 'gemini-3.5-flash'): ('1.50', '0.15', '9'),
    ('gemini', 'gemini-3.5-flash-lite'): ('0.30', '0.03', '2.50'),
    ('gemini', 'gemini-3.1-pro-preview'): ('2', '0.20', '12'),
    ('gemini', 'gemini-3.1-pro-preview-customtools'): ('2', '0.20', '12'),
    ('gemini', 'gemini-3-flash-preview'): ('0.50', '0.05', '3'),
    ('gemini', 'gemini-3.1-flash-lite'): ('0.25', '0.025', '1.50'),
    ('gemini', 'gemini-2.5-pro'): ('1.25', '0.125', '10'),
    ('gemini', 'gemini-2.5-flash'): ('0.30', '0.03', '2.50'),
    ('gemini', 'gemini-2.5-flash-lite'): ('0.10', '0.01', '0.40'),
    ('claude', 'claude-fable-5-1'): ('10', '0.25', '50'),
    ('claude', 'claude-opus-5-5'): ('4', '0.20', '20'),
    ('claude', 'claude-sonnet-5-5'): ('2', '0.20', '10'),
    ('claude', 'claude-opus-4-8'): ('5', '0.50', '25'),
    ('claude', 'claude-opus-4-7'): ('5', '0.50', '25'),
    ('claude', 'claude-sonnet-4-6'): ('3', '0.30', '15'),
    ('claude', 'claude-haiku-4-5'): ('1', '0.10', '5'),
    ('claude', 'claude-haiku-4-5-20251001'): ('1', '0.10', '5'),
}


def _default_fields(provider, model):
    input_price, cached_price, output_price = DEFAULT_PRICES[(provider, model)]
    return {
        'input_price': Decimal(input_price),
        'cached_input_price': Decimal(cached_price),
        'output_price': Decimal(output_price),
    }


def ensure_default_prices():
    """Add a row for every model in DEFAULT_PRICES that has none yet. Existing rows are left untouched."""
    existing = set(AIModelPrice.objects.values_list('provider', 'model'))
    AIModelPrice.objects.bulk_create([
        AIModelPrice(provider=provider, model=model, **_default_fields(provider, model))
        for provider, model in DEFAULT_PRICES if (provider, model) not in existing
    ])


def get_price(provider, model):
    """The price row of a model, created from DEFAULT_PRICES when missing; None if there is no price at all."""
    price = AIModelPrice.objects.filter(provider=provider, model=model).first()
    if price is None and (provider, model) in DEFAULT_PRICES:
        price, _ = AIModelPrice.objects.get_or_create(
            provider=provider, model=model, defaults=_default_fields(provider, model))
    return price


def _int(value):
    return value if isinstance(value, int) and value > 0 else 0


def extract_usage(provider, data):
    """
    Normalize the token usage of a provider response into disjoint counts:
    input (uncached), cached, output (visible text) and reasoning. Returns None if the response has none.

    Claude does not report thinking tokens separately (they are inside output_tokens), and we do not send
    cache_control, so for Claude cached and reasoning stay 0.
    """
    if not isinstance(data, dict):
        return None

    if provider == 'openai':
        usage = data.get('usage')
        if not isinstance(usage, dict):
            return None
        cached = _int((usage.get('input_tokens_details') or {}).get('cached_tokens'))
        reasoning = _int((usage.get('output_tokens_details') or {}).get('reasoning_tokens'))
        # input_tokens includes the cached part, output_tokens includes the reasoning part
        input_tokens = max(_int(usage.get('input_tokens')) - cached, 0)
        output = max(_int(usage.get('output_tokens')) - reasoning, 0)
    elif provider == 'gemini':
        usage = data.get('usageMetadata')
        if not isinstance(usage, dict):
            return None
        cached = _int(usage.get('cachedContentTokenCount'))
        reasoning = _int(usage.get('thoughtsTokenCount'))
        # promptTokenCount includes the cached part; candidatesTokenCount excludes thoughts
        input_tokens = max(_int(usage.get('promptTokenCount')) - cached, 0)
        output = _int(usage.get('candidatesTokenCount'))
    elif provider == 'claude':
        usage = data.get('usage')
        if not isinstance(usage, dict):
            return None
        cached = 0
        reasoning = 0
        input_tokens = _int(usage.get('input_tokens'))
        output = _int(usage.get('output_tokens'))
    else:
        return None

    return {
        'input_tokens': input_tokens,
        'cached_tokens': cached,
        'output_tokens': output,
        'reasoning_tokens': reasoning,
    }


def compute_cost(provider, model, usage):
    """USD cost of the usage at the model's current price, or None if the model has no price."""
    price = get_price(provider, model)
    if price is None:
        return None
    return (
        usage['input_tokens'] * price.input_price
        + usage['cached_tokens'] * price.cached_input_price
        # reasoning tokens are billed as output
        + (usage['output_tokens'] + usage['reasoning_tokens']) * price.output_price
    ) / _PER_MILLION


def record_usage(feature, user, provider, model, usage, success=True, gen_code=None, code_review=None):
    """Log one AI call. Never raises: the user already has their result, a logging failure must not lose it."""
    if not usage or not sum(usage.values()):
        return None
    try:
        return AIUsageLog.objects.create(
            feature=feature,
            user=user,
            provider=provider,
            model=model,
            input_tokens=usage['input_tokens'],
            cached_tokens=usage['cached_tokens'],
            output_tokens=usage['output_tokens'],
            reasoning_tokens=usage['reasoning_tokens'],
            total_tokens=sum(usage.values()),
            cost_usd=compute_cost(provider, model, usage),
            success=success,
            gen_code=gen_code,
            code_review=code_review,
        )
    except Exception:
        logger.exception('Failed to record AI usage (%s %s/%s)', feature, provider, model)
        return None
