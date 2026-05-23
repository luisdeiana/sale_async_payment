from decimal import Decimal

from trytond.exceptions import UserError
from trytond.model import fields
from trytond.pool import Pool, PoolMeta


class Sale(metaclass=PoolMeta):
    __name__ = 'sale.sale'

    async_payments = fields.One2Many(
        'sale.async_payment', 'sale', "Async Payments")

    async_pending_amount = fields.Function(
        fields.Numeric(
            "Pending Async", digits=(16, 2),
            help="Sum of async payments in Pending or Suggested state."),
        'get_async_pending_amount')

    effective_residual_amount = fields.Function(
        fields.Numeric(
            "Effective Residual", digits=(16, 2),
            help="Sale total minus confirmed payments minus pending "
                 "async payments."),
        'get_effective_residual_amount')

    @classmethod
    def get_async_pending_amount(cls, sales, name):
        result = {sale.id: Decimal('0.0') for sale in sales}
        for sale in sales:
            total = Decimal('0.0')
            for ap in sale.async_payments:
                if ap.state in ('pending', 'suggested') and ap.amount:
                    total += ap.amount
            result[sale.id] = total
        return result

    @classmethod
    def get_effective_residual_amount(cls, sales, name):
        result = {}
        for sale in sales:
            total = sale.total_amount or Decimal('0.0')
            paid = sale.paid_amount or Decimal('0.0')
            pending = sale.async_pending_amount or Decimal('0.0')
            result[sale.id] = total - paid - pending
        return result

    @fields.depends('async_pending_amount',
                    methods=['on_change_with_allow_to_pay'])
    def on_change_with_allow_to_pay(self, name=None):
        result = super().on_change_with_allow_to_pay(name)
        if not result:
            return False
        if self.total_amount is None or self.total_amount == 0:
            return result
        paid = self.paid_amount or Decimal('0.0')
        pending = self.async_pending_amount or Decimal('0.0')
        if abs(self.total_amount) <= abs(paid + pending):
            return False
        return True

    @classmethod
    def _async_classify_for_cancel(cls, sales):
        # Retorna (blocked, to_cascade): ventas con async confirmados quedan
        # bloqueadas; los async pendientes/sugeridos se cascadean a cancelled.
        blocked = []
        to_cascade = []
        for sale in sales:
            has_confirmed = any(
                ap.state == 'confirmed' for ap in sale.async_payments)
            if has_confirmed:
                blocked.append(sale)
                continue
            for ap in sale.async_payments:
                if ap.state in ('pending', 'suggested'):
                    to_cascade.append(ap)
        return blocked, to_cascade

    @classmethod
    def cancel(cls, sales):
        pool = Pool()
        AsyncPayment = pool.get('sale.async_payment')

        blocked, to_cascade = cls._async_classify_for_cancel(sales)

        if blocked:
            names = ', '.join(s.rec_name for s in blocked)
            raise UserError(
                "Cannot cancel sale because it has confirmed async "
                "payments: " + names)

        if to_cascade:
            AsyncPayment.write(to_cascade, {'state': 'cancelled'})

        super().cancel(sales)

    @classmethod
    def copy(cls, sales, default=None):
        # Al duplicar una venta no se arrastran los cobros asíncronos
        # (la copia arranca limpia y debe registrar sus propios cobros).
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('async_payments', None)
        return super().copy(sales, default=default)
