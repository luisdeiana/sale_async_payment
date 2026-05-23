from trytond.model import fields
from trytond.pool import PoolMeta


class User(metaclass=PoolMeta):
    __name__ = 'res.user'

    async_payment_filters = fields.One2Many(
        'sale.async_payment.user_filter', 'user',
        "Async Payment Filters",
        help="Configures which async payments this user sees in the "
             "Pending Payments view.")
