from django.contrib import admin
from django.db.models import Count, Sum
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from judge.models.ai_usage import AIModelPrice, AIUsageLog
from judge.utils.ai_usage import DEFAULT_PRICES, ensure_default_prices


class AIUsageLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'feature', 'provider_model', 'action_link', 'input_tokens',
                    'cached_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens', 'cost', 'success')
    list_filter = ('feature', 'provider', 'model', 'success', 'created_at')
    search_fields = ('user__user__username', 'model', 'gen_code__problem__code')
    date_hierarchy = 'created_at'
    list_select_related = ('user__user', 'gen_code__problem', 'code_review')
    list_per_page = 50
    change_list_template = 'admin/judge/aiusagelog/change_list.html'
    readonly_fields = ('created_at', 'user', 'feature', 'provider', 'model', 'action_link', 'input_tokens',
                       'cached_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens', 'cost', 'success')
    fields = readonly_fields

    # A log is a record of what was billed: nobody adds or edits it by hand
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context)
        # Totals of the current filter (all pages), shown above the table
        cl = getattr(response, 'context_data', {}).get('cl')
        if cl is not None:
            totals = cl.queryset.aggregate(
                calls=Count('id'), input=Sum('input_tokens'), cached=Sum('cached_tokens'),
                output=Sum('output_tokens'), reasoning=Sum('reasoning_tokens'), total=Sum('total_tokens'),
                cost=Sum('cost_usd'),
            )
            # Formatted here so it matches the per-row cost (the template would localize the decimal point)
            totals['cost_text'] = f"${totals['cost'] or 0:.6f}"
            totals['unpriced'] = cl.queryset.filter(cost_usd__isnull=True).count()
            response.context_data['usage_totals'] = totals
        return response

    def provider_model(self, obj):
        return f'{obj.get_provider_display()} / {obj.model}'
    provider_model.short_description = _('Model')
    provider_model.admin_order_field = 'model'

    def action_link(self, obj):
        """Link to the record this call produced."""
        if obj.gen_code_id:
            label = _('Generator code #%(id)s — %(problem)s') % {
                'id': obj.gen_code_id, 'problem': obj.gen_code.problem.code}
            url = reverse('admin:judge_aigencode_change', args=[obj.gen_code_id])
        elif obj.code_review_id:
            label = _('Code review #%(id)s — submission #%(sub)s') % {
                'id': obj.code_review_id, 'sub': obj.code_review.submission_id}
            url = reverse('admin:judge_aicodereview_change', args=[obj.code_review_id])
        else:
            return '—'
        return format_html('<a href="{}">{}</a>', url, label)
    action_link.short_description = _('Action')

    def cost(self, obj):
        if obj.cost_usd is None:
            return '—'
        return f'${obj.cost_usd:.6f}'
    cost.short_description = _('Cost (USD)')
    cost.admin_order_field = 'cost_usd'


class AIModelPriceAdmin(admin.ModelAdmin):
    list_display = ('provider', 'model', 'input_price', 'cached_input_price', 'output_price', 'updated_at')
    list_filter = ('provider',)
    search_fields = ('model',)
    actions = ['reset_to_default']

    def changelist_view(self, request, extra_context=None):
        # Every selectable model shows up here with its default price, ready to be adjusted
        ensure_default_prices()
        return super().changelist_view(request, extra_context)

    def reset_to_default(self, request, queryset):
        count = 0
        for price in queryset:
            default = DEFAULT_PRICES.get((price.provider, price.model))
            if default:
                price.input_price, price.cached_input_price, price.output_price = default
                price.save()
                count += 1
        self.message_user(request, _('%d price(s) reset to the default.') % count)
    reset_to_default.short_description = _('Reset selected prices to the default')
