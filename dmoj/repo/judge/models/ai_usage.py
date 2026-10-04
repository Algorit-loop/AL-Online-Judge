from django.db import models
from django.utils.translation import gettext_lazy as _

from judge.models.ai_code_review import AICodeReview
from judge.models.ai_gen_code import AIGenCode
from judge.models.api_key import AI_PROVIDER_CHOICES
from judge.models.profile import Profile

__all__ = ['AIModelPrice', 'AIUsageLog']


class AIModelPrice(models.Model):
    """USD price per 1M tokens of a model, used to price each AI call when it is logged."""
    provider = models.CharField(max_length=20, choices=AI_PROVIDER_CHOICES, verbose_name=_('provider'))
    model = models.CharField(max_length=100, verbose_name=_('model'))
    input_price = models.DecimalField(max_digits=10, decimal_places=4, verbose_name=_('input (USD / 1M tokens)'))
    cached_input_price = models.DecimalField(max_digits=10, decimal_places=4,
                                             verbose_name=_('cached input (USD / 1M tokens)'))
    output_price = models.DecimalField(max_digits=10, decimal_places=4,
                                       verbose_name=_('output (USD / 1M tokens)'),
                                       help_text=_('Reasoning tokens are billed at this price too.'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('updated at'))

    class Meta:
        verbose_name = _('AI model price')
        verbose_name_plural = _('AI model prices')
        unique_together = ('provider', 'model')
        ordering = ['provider', 'model']

    def __str__(self):
        return f'{self.provider}/{self.model}'


class AIUsageLog(models.Model):
    """One call to an AI provider that spent tokens, with what it cost at the time of the call."""
    FEATURE_GEN_CODE = 'gen_code'
    FEATURE_CODE_REVIEW = 'code_review'
    FEATURE_CHOICES = [
        (FEATURE_GEN_CODE, _('Generator code')),
        (FEATURE_CODE_REVIEW, _('Code review')),
    ]

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_('time'))
    user = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, related_name='ai_usage_logs',
                             verbose_name=_('user'))
    feature = models.CharField(max_length=20, choices=FEATURE_CHOICES, verbose_name=_('feature'))
    provider = models.CharField(max_length=20, choices=AI_PROVIDER_CHOICES, verbose_name=_('provider'))
    model = models.CharField(max_length=100, verbose_name=_('model'))

    # Uncached input, cached input, visible output and reasoning are disjoint; total is their sum
    input_tokens = models.PositiveIntegerField(default=0, verbose_name=_('input tokens'))
    cached_tokens = models.PositiveIntegerField(default=0, verbose_name=_('cached input tokens'))
    output_tokens = models.PositiveIntegerField(default=0, verbose_name=_('output tokens'))
    reasoning_tokens = models.PositiveIntegerField(default=0, verbose_name=_('reasoning tokens'))
    total_tokens = models.PositiveIntegerField(default=0, verbose_name=_('total tokens'))
    # Fixed when the call is made, so later price changes never rewrite history. NULL = no price known.
    cost_usd = models.DecimalField(max_digits=14, decimal_places=8, null=True, blank=True,
                                   verbose_name=_('cost (USD)'))

    # False when the provider answered (and billed) but the answer was unusable, so nothing was saved
    success = models.BooleanField(default=True, verbose_name=_('success'))
    # What the call produced; SET_NULL so the cost survives when that record is deleted
    gen_code = models.ForeignKey(AIGenCode, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='usage_logs', verbose_name=_('generated code'))
    code_review = models.ForeignKey(AICodeReview, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='usage_logs', verbose_name=_('code review'))

    class Meta:
        verbose_name = _('AI usage log')
        verbose_name_plural = _('AI usage logs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['provider', 'model']),
        ]

    def __str__(self):
        return f'{self.get_feature_display()} {self.provider}/{self.model} #{self.id}'
